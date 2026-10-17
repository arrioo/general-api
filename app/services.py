import uuid
from datetime import datetime, timezone

import asyncpg
from fastapi import HTTPException


def make_no(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"


async def move_stock(
    conn: asyncpg.Connection,
    *,
    product_id: int,
    warehouse_id: int,
    mtype: str,
    qty_change: int,
    ref_type: str | None,
    ref_id: int | None,
    user_id: int | None,
    idem_key: str | None = None,
    note: str | None = None,
) -> int:
    """The only way the API changes stock. Calls the SQL function (locks row + writes ledger)."""
    try:
        return await conn.fetchval(
            "SELECT apply_stock_movement($1,$2,$3::movement_type,$4,$5,$6,$7,$8,$9)",
            product_id, warehouse_id, mtype, qty_change, ref_type, ref_id, user_id, idem_key, note,
        )
    except asyncpg.exceptions.RaiseError as e:
        if "INSUFFICIENT_STOCK" in str(e):
            raise HTTPException(
                409, f"Insufficient stock for product {product_id} in warehouse {warehouse_id}"
            )
        raise


async def set_order_status(
    conn: asyncpg.Connection,
    order_id: int,
    from_status: str | None,
    to_status: str,
    user_id: int | None = None,
    note: str | None = None,
) -> None:
    await conn.execute("UPDATE orders SET status = $2 WHERE id = $1", order_id, to_status)
    await conn.execute(
        """INSERT INTO order_status_history (order_id, from_status, to_status, changed_by, note)
           VALUES ($1, $2, $3, $4, $5)""",
        order_id, from_status, to_status, user_id, note,
    )


async def ensure_exists(conn: asyncpg.Connection, product_id: int, warehouse_id: int) -> None:
    if not await conn.fetchval("SELECT 1 FROM products WHERE id = $1", product_id):
        raise HTTPException(404, f"Product {product_id} not found")
    if not await conn.fetchval("SELECT 1 FROM warehouses WHERE id = $1 AND is_active", warehouse_id):
        raise HTTPException(404, f"Warehouse {warehouse_id} not found")
