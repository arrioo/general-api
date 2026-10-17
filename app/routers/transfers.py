from fastapi import APIRouter, Depends, HTTPException

from ..db import get_conn
from ..deps import staff_only
from ..schemas import TransferCreate
from ..services import make_no, move_stock

router = APIRouter(prefix="/transfers", tags=["transfers"])


@router.post("", status_code=201)
async def create_transfer(body: TransferCreate, user: dict = Depends(staff_only), conn=Depends(get_conn)):
    for wh in (body.from_warehouse, body.to_warehouse):
        if not await conn.fetchval("SELECT 1 FROM warehouses WHERE id=$1 AND is_active", wh):
            raise HTTPException(404, f"Warehouse {wh} not found")

    wanted: dict[int, int] = {}
    for it in body.items:
        wanted[it.product_id] = wanted.get(it.product_id, 0) + it.qty
    found = await conn.fetch("SELECT id FROM products WHERE id = ANY($1::bigint[])", list(wanted))
    missing = sorted(set(wanted) - {r["id"] for r in found})
    if missing:
        raise HTTPException(404, f"Products not found: {missing}")

    async with conn.transaction():
        tid = await conn.fetchval(
            """INSERT INTO transfers (transfer_no, from_warehouse, to_warehouse, requested_by)
               VALUES ($1,$2,$3,$4) RETURNING id""",
            make_no("TRF"), body.from_warehouse, body.to_warehouse, user["id"],
        )
        await conn.executemany(
            "INSERT INTO transfer_items (transfer_id, product_id, qty) VALUES ($1,$2,$3)",
            [(tid, pid, q) for pid, q in sorted(wanted.items())],
        )
    return {"transfer_id": tid, "status": "requested"}


async def _lock_transfer(conn, transfer_id: int, expected: str):
    t = await conn.fetchrow("SELECT * FROM transfers WHERE id = $1 FOR UPDATE", transfer_id)
    if not t:
        raise HTTPException(404, "Transfer not found")
    if t["status"] != expected:
        raise HTTPException(409, f"Transfer is '{t['status']}', expected '{expected}'")
    return t


@router.post("/{transfer_id}/ship")
async def ship_transfer(transfer_id: int, user: dict = Depends(staff_only), conn=Depends(get_conn)):
    """Stock leaves the source warehouse (transfer_out). Goods are now 'in transit'."""
    async with conn.transaction():
        t = await _lock_transfer(conn, transfer_id, "requested")
        items = await conn.fetch(
            "SELECT id, product_id, qty FROM transfer_items WHERE transfer_id=$1 ORDER BY product_id",
            transfer_id,
        )
        for it in items:
            await move_stock(
                conn, product_id=it["product_id"], warehouse_id=t["from_warehouse"],
                mtype="transfer_out", qty_change=-it["qty"], ref_type="transfer", ref_id=transfer_id,
                user_id=user["id"], idem_key=f"transfer-{transfer_id}-out-{it['id']}",
            )
        await conn.execute(
            "UPDATE transfers SET status='in_transit', shipped_at=now() WHERE id=$1", transfer_id
        )
    return {"transfer_id": transfer_id, "status": "in_transit"}


@router.post("/{transfer_id}/receive")
async def receive_transfer(transfer_id: int, user: dict = Depends(staff_only), conn=Depends(get_conn)):
    """Stock arrives at the destination warehouse (transfer_in)."""
    async with conn.transaction():
        t = await _lock_transfer(conn, transfer_id, "in_transit")
        items = await conn.fetch(
            "SELECT id, product_id, qty FROM transfer_items WHERE transfer_id=$1 ORDER BY product_id",
            transfer_id,
        )
        for it in items:
            await move_stock(
                conn, product_id=it["product_id"], warehouse_id=t["to_warehouse"],
                mtype="transfer_in", qty_change=it["qty"], ref_type="transfer", ref_id=transfer_id,
                user_id=user["id"], idem_key=f"transfer-{transfer_id}-in-{it['id']}",
            )
        await conn.execute(
            "UPDATE transfers SET status='received', received_at=now() WHERE id=$1", transfer_id
        )
    return {"transfer_id": transfer_id, "status": "received"}
