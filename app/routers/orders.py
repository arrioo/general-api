from fastapi import APIRouter, Depends, HTTPException, Request

from ..db import get_conn
from ..deps import get_current_user
from ..limiter import limiter
from ..queue import JOB_OPTS, get_queue
from ..schemas import OrderCreate
from ..services import make_no, set_order_status

router = APIRouter(prefix="/orders", tags=["orders"])


async def _get_order_or_404(conn, order_id: int, user: dict):
    o = await conn.fetchrow(
        """SELECT o.*, u.name AS user_name, w.code AS warehouse_code
             FROM orders o
             JOIN users u ON u.id = o.user_id
             JOIN warehouses w ON w.id = o.warehouse_id
            WHERE o.id = $1""",
        order_id,
    )
    # customers can only see their own orders
    if not o or (user["role"] == "customer" and o["user_id"] != user["id"]):
        raise HTTPException(404, "Order not found")
    return o


@router.post("", status_code=202)
@limiter.limit("10/minute")  # per IP: first line of defence against bots / spam clicks
async def create_order(
    request: Request,
    body: OrderCreate,
    user: dict = Depends(get_current_user),
    conn=Depends(get_conn),
):
    """Validate, save the order as `queued`, push to Redis. Worker deducts stock one by one."""
    wanted: dict[int, int] = {}
    for it in body.items:                       # merge duplicate products
        wanted[it.product_id] = wanted.get(it.product_id, 0) + it.qty

    if not await conn.fetchval("SELECT 1 FROM warehouses WHERE id=$1 AND is_active", body.warehouse_id):
        raise HTTPException(404, "Warehouse not found")

    rows = await conn.fetch(
        "SELECT id, price FROM products WHERE id = ANY($1::bigint[]) AND is_active", list(wanted)
    )
    prices = {r["id"]: r["price"] for r in rows}
    missing = sorted(set(wanted) - set(prices))
    if missing:
        raise HTTPException(404, f"Products not found: {missing}")

    async with conn.transaction():
        order_id = await conn.fetchval(
            """INSERT INTO orders (order_no, user_id, warehouse_id, status)
               VALUES ($1, $2, $3, 'queued') RETURNING id""",
            make_no("ORD"), user["id"], body.warehouse_id,
        )
        await conn.executemany(
            "INSERT INTO order_items (order_id, product_id, qty, unit_price) VALUES ($1,$2,$3,$4)",
            [(order_id, pid, qty, prices[pid]) for pid, qty in sorted(wanted.items())],
        )
        job_id = f"order-{order_id}"  # BullMQ custom ids must not be plain integers
        await conn.execute("UPDATE orders SET queue_job_id = $2 WHERE id = $1", order_id, job_id)
        await conn.execute(
            """INSERT INTO order_status_history (order_id, from_status, to_status, changed_by, note)
               VALUES ($1, NULL, 'queued', $2, 'Order accepted')""",
            order_id, user["id"],
        )

    # push AFTER commit so the worker can always find the order
    try:
        await get_queue().add("process-order", {"orderId": order_id}, {**JOB_OPTS, "jobId": job_id})
    except Exception:
        async with conn.transaction():
            await set_order_status(conn, order_id, "queued", "failed", None, "Queue unavailable")
            await conn.execute("UPDATE orders SET fail_reason='queue_unavailable' WHERE id=$1", order_id)
        raise HTTPException(503, "Queue unavailable, please try again")

    return {"order_id": order_id, "job_id": job_id, "status": "queued"}


@router.get("/{order_id}")
async def get_order(order_id: int, user: dict = Depends(get_current_user), conn=Depends(get_conn)):
    o = await _get_order_or_404(conn, order_id, user)
    items = await conn.fetch(
        """SELECT oi.product_id, p.sku, p.name, oi.qty, oi.unit_price
             FROM order_items oi JOIN products p ON p.id = oi.product_id
            WHERE oi.order_id = $1 ORDER BY oi.id""",
        order_id,
    )
    return {**dict(o), "items": [dict(i) for i in items]}


@router.get("/{order_id}/history")
async def order_history(order_id: int, user: dict = Depends(get_current_user), conn=Depends(get_conn)):
    await _get_order_or_404(conn, order_id, user)
    rows = await conn.fetch(
        """SELECT h.id, h.from_status, h.to_status, h.note, h.created_at,
                  u.name AS changed_by
             FROM order_status_history h
             LEFT JOIN users u ON u.id = h.changed_by
            WHERE h.order_id = $1 ORDER BY h.id""",
        order_id,
    )
    return [dict(r) for r in rows]


@router.post("/{order_id}/cancel")
async def cancel_order(order_id: int, user: dict = Depends(get_current_user), conn=Depends(get_conn)):
    async with conn.transaction():
        # row lock: serialises with the worker, which locks the same row before deducting
        o = await conn.fetchrow(
            "SELECT id, user_id, status, queue_job_id FROM orders WHERE id = $1 FOR UPDATE", order_id
        )
        if not o or (user["role"] == "customer" and o["user_id"] != user["id"]):
            raise HTTPException(404, "Order not found")
        if o["status"] not in ("pending", "queued"):
            raise HTTPException(
                409, f"Cannot cancel an order in status '{o['status']}'. Use /returns for confirmed orders."
            )
        await set_order_status(conn, order_id, o["status"], "cancelled", user["id"], "Cancelled by user")

    # best effort: drop the waiting job (the worker also skips non-queued orders)
    try:
        job = await get_queue().getJob(o["queue_job_id"])
        if job:
            await job.remove()
    except Exception:
        pass
    return {"order_id": order_id, "status": "cancelled"}
