import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from . import db, queue
from .limiter import limiter
from .routers import orders, returns, stock, transfers
from redis.asyncio import Redis

@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    yield
    await queue.close_queue()
    await db.close_pool()

logger = logging.getLogger(__name__)

app = FastAPI(title="general-api", version="1.0.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.include_router(orders.router)
app.include_router(stock.router)
app.include_router(transfers.router)
app.include_router(returns.router)


@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok"}

# test DB
@app.get("/test-db", tags=["system"])
async def test_db(conn=Depends(db.get_conn)):
    try:
        result = await conn.fetchval("SELECT 1")
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# test REDIS
# Test Redis connection
@app.get("/test-redis", tags=["system"])
async def test_redis():
    redis_url = os.getenv("REDIS_URL")

    if not redis_url:
        raise HTTPException(
            status_code=500,
            detail="REDIS_URL environment variable is not configured",
        )

    redis_client = None

    try:
        redis_client = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            protocol=2
        )

        result = await redis_client.ping()

        return {
            "redis_connected": result,
            "status": "ok",
        }

    except Exception as exc:
        logger.exception("Redis connection test failed")
        raise HTTPException(
            status_code=503,
            detail=f"Redis connection failed: {type(exc).__name__}: {str(exc)}",
        ) from exc

    finally:
        if redis_client is not None:
            await redis_client.aclose()


# Test BullMQ job enqueueing
@app.get("/test-worker", tags=["system"])
async def test_worker():
    try:
        q = queue.get_queue()

        job = await q.add(
            "order_created",
            {"order_id": 1},
        )

        return {
            "message": "Job added successfully",
            "job_id": str(job.id),
            "status": "waiting",
        }

    except Exception as exc:
        logger.exception("Failed to enqueue test job")

        raise HTTPException(
            status_code=503,
            detail=f"Failed to enqueue job: {type(exc).__name__}: {str(exc)}",
        ) from exc

# test all
@app.get("/test-all", tags=["system"])
async def test_all():
    return await db.get_pool(), await queue.get_queue().connection.ping(), "Worker test"