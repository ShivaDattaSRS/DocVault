"""Job queue: Redis/RQ when available, in-process threads otherwise.

The API never blocks on processing — uploads return immediately with
status=processing and the worker flips the record to ready/failed.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from ..config import settings

log = logging.getLogger("docvault.jobs")

QUEUE_NAME = "docvault"
_lock = threading.Lock()
_redis = None
_queue = None
_checked = False
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="docvault-job")


def _connect():
    """Lazily connect to Redis; returns (redis, queue) or (None, None)."""
    global _redis, _queue, _checked
    with _lock:
        if _checked:
            return _redis, _queue
        _checked = True
        try:
            import redis as redis_lib
            from rq import Queue

            client = redis_lib.from_url(settings.redis_url, socket_connect_timeout=2)
            client.ping()
            _redis = client
            _queue = Queue(QUEUE_NAME, connection=client, default_timeout=900)
            log.info("Connected to Redis at %s — background jobs use RQ", settings.redis_url)
        except Exception as exc:  # noqa: BLE001
            log.warning("Redis unavailable (%s) — falling back to in-process worker threads", exc)
            _redis, _queue = None, None
        return _redis, _queue


def redis_client():
    return _connect()[0]


def backend_name() -> str:
    return "redis-rq" if _connect()[1] is not None else "thread-pool"


def enqueue_processing(file_id: str) -> str:
    """Queue a file for background processing. Returns the backend used."""
    from .processing import process_file

    _, queue = _connect()
    if queue is not None:
        try:
            queue.enqueue("app.services.processing.process_file", file_id, job_id=f"process:{file_id}")
            return "redis-rq"
        except Exception as exc:  # noqa: BLE001
            log.warning("Enqueue to Redis failed (%s) — running in-process", exc)

    _pool.submit(process_file, file_id)
    return "thread-pool"


def queue_depth() -> int | None:
    _, queue = _connect()
    if queue is None:
        return None
    try:
        return len(queue)
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------- rate limits
def rate_limit(key: str, limit: int, window_seconds: int) -> bool:
    """Return True if the action is allowed. Redis-backed, no-op without Redis."""
    client = redis_client()
    if client is None:
        return True
    try:
        full = f"ratelimit:{key}"
        count = client.incr(full)
        if count == 1:
            client.expire(full, window_seconds)
        return count <= limit
    except Exception:  # noqa: BLE001
        return True
