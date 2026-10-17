import os

from bullmq import Queue

_queue: Queue | None = None

JOB_OPTS = {
    "attempts": 3,                                   # retry on technical errors
    "backoff": {"type": "exponential", "delay": 2000},
    "removeOnComplete": 1000,
    "removeOnFail": 5000,
}


def get_queue() -> Queue:
    global _queue
    if _queue is None:
        _queue = Queue(
            os.getenv("QUEUE_NAME", "order-queue"),
            {"connection": os.getenv("REDIS_URL", "redis://localhost:6379")},
        )
    return _queue


async def close_queue() -> None:
    if _queue is not None:
        await _queue.close()
