import os

from bullmq import Queue
from redis.asyncio import Redis

_queue: Queue | None = None
_redis_client: Redis | None = None

JOB_OPTS = {
    "attempts": 3,
    "backoff": {"type": "exponential", "delay": 2000},
    "removeOnComplete": 1000,
    "removeOnFail": 5000,
}


def get_queue() -> Queue:
    global _queue, _redis_client

    if _queue is None:
        redis_url = os.getenv(
            "REDIS_URL",
            "redis://127.0.0.1:6379/0",
        )

        # Explicitly use RESP2.
        _redis_client = Redis.from_url(
            redis_url,
            decode_responses=True,
            protocol=2,
        )

        _queue = Queue(
            os.getenv("QUEUE_NAME", "order-queue"),
            {"connection": _redis_client},
        )

    return _queue


async def close_queue() -> None:
    global _queue, _redis_client

    if _queue is not None:
        await _queue.close()
        _queue = None

    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None