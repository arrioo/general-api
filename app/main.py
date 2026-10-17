from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from . import db, queue
from .limiter import limiter
from .routers import orders, returns, stock, transfers


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    yield
    await queue.close_queue()
    await db.close_pool()


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
@app.get("/test-redis", tags=["system"])
async def test_redis():
    q = queue.get_queue()
    return await q.connection.ping()

# test Worker
@app.get("/test-worker", tags=["system"])
async def test_worker():
    q = queue.get_queue()
    await q.enqueue("order_created", {"order_id": 1})
    return "Worker test"

# test all
@app.get("/test-all", tags=["system"])
async def test_all():
    return await db.get_pool(), await queue.get_queue().connection.ping(), "Worker test"