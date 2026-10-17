from fastapi import APIRouter, Depends, Query

from ..db import get_conn
from ..deps import get_current_user, staff_only
from ..schemas import AdjustIn, ReceiveIn
from ..services import ensure_exists, move_stock

router = APIRouter(prefix="/stock", tags=["stock"])


@router.get("")
async def get_stock(
    product_id: int | None = None,
    warehouse_id: int | None = None,
    _: dict = Depends(get_current_user),
    conn=Depends(get_conn),
):
    conds, args = [], []
    if product_id is not None:
        args.append(product_id)
        conds.append(f"s.product_id = ${len(args)}")
    if warehouse_id is not None:
        args.append(warehouse_id)
        conds.append(f"s.warehouse_id = ${len(args)}")
    where = ("WHERE " + " AND ".join(conds)) if conds else ""

    rows = await conn.fetch(
        f"""SELECT s.product_id, p.sku, p.name AS product_name,
                   s.warehouse_id, w.code AS warehouse_code,
                   s.on_hand, s.reserved, (s.on_hand - s.reserved) AS available, s.updated_at
              FROM stock_levels s
              JOIN products p   ON p.id = s.product_id
              JOIN warehouses w ON w.id = s.warehouse_id
              {where}
             ORDER BY p.sku, w.code""",
        *args,
    )
    return [dict(r) for r in rows]


@router.post("/receive", status_code=201)
async def receive_stock(body: ReceiveIn, user: dict = Depends(staff_only), conn=Depends(get_conn)):
    await ensure_exists(conn, body.product_id, body.warehouse_id)
    async with conn.transaction():
        on_hand = await move_stock(
            conn, product_id=body.product_id, warehouse_id=body.warehouse_id,
            mtype="receive", qty_change=body.qty, ref_type="manual", ref_id=None,
            user_id=user["id"], idem_key=body.idempotency_key, note=body.note,
        )
    return {"product_id": body.product_id, "warehouse_id": body.warehouse_id, "on_hand": on_hand}


@router.post("/adjust", status_code=201)
async def adjust_stock(body: AdjustIn, user: dict = Depends(staff_only), conn=Depends(get_conn)):
    await ensure_exists(conn, body.product_id, body.warehouse_id)
    async with conn.transaction():
        on_hand = await move_stock(
            conn, product_id=body.product_id, warehouse_id=body.warehouse_id,
            mtype="adjustment", qty_change=body.qty_change, ref_type="manual", ref_id=None,
            user_id=user["id"], idem_key=body.idempotency_key, note=body.note,
        )
    return {"product_id": body.product_id, "warehouse_id": body.warehouse_id, "on_hand": on_hand}


@router.get("/movements")
async def stock_movements(
    product_id: int | None = None,
    warehouse_id: int | None = None,
    movement_type: str | None = None,
    reference_type: str | None = None,
    reference_id: int | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: dict = Depends(staff_only),
    conn=Depends(get_conn),
):
    conds, args = [], []

    def add(col: str, val, cast: str = ""):
        if val is not None:
            args.append(val)
            conds.append(f"m.{col} = ${len(args)}{cast}")

    add("product_id", product_id)
    add("warehouse_id", warehouse_id)
    add("movement_type", movement_type, "::movement_type")
    add("reference_type", reference_type)
    add("reference_id", reference_id)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""

    args += [limit, offset]
    rows = await conn.fetch(
        f"""SELECT m.id, m.created_at, m.movement_type, m.qty_change, m.on_hand_after,
                   p.sku, w.code AS warehouse_code,
                   m.reference_type, m.reference_id, u.name AS by_user, m.note
              FROM stock_movements m
              JOIN products p   ON p.id = m.product_id
              JOIN warehouses w ON w.id = m.warehouse_id
              LEFT JOIN users u ON u.id = m.user_id
              {where}
             ORDER BY m.id DESC
             LIMIT ${len(args) - 1} OFFSET ${len(args)}""",
        *args,
    )
    return [dict(r) for r in rows]
