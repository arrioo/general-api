from fastapi import APIRouter, Depends, HTTPException

from ..db import get_conn
from ..deps import get_current_user, staff_only
from ..schemas import ReturnCreate, ReturnReceiveIn
from ..services import make_no, move_stock, set_order_status

router = APIRouter(prefix="/returns", tags=["returns"])


@router.post("", status_code=201)
async def create_return(body: ReturnCreate, user: dict = Depends(get_current_user), conn=Depends(get_conn)):
    async with conn.transaction():
        order = await conn.fetchrow(
            "SELECT id, user_id, warehouse_id, status FROM orders WHERE id=$1 FOR UPDATE", body.order_id
        )
        if not order or (user["role"] == "customer" and order["user_id"] != user["id"]):
            raise HTTPException(404, "Order not found")
        if order["status"] not in ("confirmed", "completed"):
            raise HTTPException(409, f"Order in status '{order['status']}' cannot be returned")

        ordered = {
            r["product_id"]: r["qty"]
            for r in await conn.fetch("SELECT product_id, qty FROM order_items WHERE order_id=$1", body.order_id)
        }
        already = {
            r["product_id"]: r["qty"]
            for r in await conn.fetch(
                """SELECT ri.product_id, SUM(ri.qty)::int AS qty
                     FROM return_items ri JOIN returns r ON r.id = ri.return_id
                    WHERE r.order_id = $1 AND r.status <> 'rejected'
                    GROUP BY ri.product_id""",
                body.order_id,
            )
        }

        wanted: dict[int, int] = {}
        for it in body.items:
            wanted[it.product_id] = wanted.get(it.product_id, 0) + it.qty
        for pid, qty in wanted.items():
            left = ordered.get(pid, 0) - already.get(pid, 0)
            if qty > left:
                raise HTTPException(422, f"Product {pid}: can return at most {max(left, 0)}")

        rid = await conn.fetchval(
            """INSERT INTO returns (return_no, order_id, warehouse_id, reason)
               VALUES ($1,$2,$3,$4) RETURNING id""",
            make_no("RET"), body.order_id, order["warehouse_id"], body.reason,
        )
        await conn.executemany(
            "INSERT INTO return_items (return_id, product_id, qty) VALUES ($1,$2,$3)",
            [(rid, pid, q) for pid, q in sorted(wanted.items())],
        )
    return {"return_id": rid, "status": "requested"}


@router.post("/{return_id}/receive")
async def receive_return(
    return_id: int,
    body: ReturnReceiveIn = ReturnReceiveIn(),
    user: dict = Depends(staff_only),
    conn=Depends(get_conn),
):
    """Goods physically arrived. Restock good items (return_in); damaged ones are not added back."""
    async with conn.transaction():
        r = await conn.fetchrow("SELECT * FROM returns WHERE id=$1 FOR UPDATE", return_id)
        if not r:
            raise HTTPException(404, "Return not found")
        if r["status"] not in ("requested", "approved"):
            raise HTTPException(409, f"Return is already '{r['status']}'")

        for o in body.items:  # staff marks damaged items: restock = false
            await conn.execute(
                "UPDATE return_items SET restock=$3 WHERE return_id=$1 AND product_id=$2",
                return_id, o.product_id, o.restock,
            )

        items = await conn.fetch(
            "SELECT id, product_id, qty, restock FROM return_items WHERE return_id=$1 ORDER BY product_id",
            return_id,
        )
        restocked = 0
        for it in items:
            if it["restock"]:
                await move_stock(
                    conn, product_id=it["product_id"], warehouse_id=r["warehouse_id"],
                    mtype="return_in", qty_change=it["qty"], ref_type="return", ref_id=return_id,
                    user_id=user["id"], idem_key=f"return-{return_id}-item-{it['id']}",
                )
                restocked += it["qty"]

        await conn.execute(
            "UPDATE returns SET status='received', received_at=now() WHERE id=$1", return_id
        )

        # whole order returned? -> mark order as 'returned'
        totals = await conn.fetchrow(
            """SELECT (SELECT SUM(qty) FROM order_items WHERE order_id=$1) AS ordered,
                      (SELECT COALESCE(SUM(ri.qty),0) FROM return_items ri
                         JOIN returns rr ON rr.id = ri.return_id
                        WHERE rr.order_id=$1 AND rr.status='received') AS returned""",
            r["order_id"],
        )
        if totals["returned"] >= totals["ordered"]:
            cur = await conn.fetchval("SELECT status FROM orders WHERE id=$1 FOR UPDATE", r["order_id"])
            await set_order_status(conn, r["order_id"], cur, "returned", user["id"], f"Return {r['return_no']} received")

    return {"return_id": return_id, "status": "received", "restocked_qty": restocked}
