import os

import asyncpg

pool: asyncpg.Pool | None = None

async def init_pool() -> None:
    global pool
    DATABASE_URL = os.environ["DATABASE_URL"]
    DATABASE_SCHEMA = os.environ["DATABASE_SCHEMA"]
    pool = await asyncpg.create_pool(
        DATABASE_URL, 
        min_size=2, 
        max_size=10,
        server_settings={
            "search_path": DATABASE_SCHEMA
        }
    )


async def close_pool() -> None:
    if pool:
        await pool.close()


async def get_conn():
    """FastAPI dependency: one pooled connection per request."""
    async with pool.acquire() as conn:
        yield conn

async def get_pool():
    return pool

    