"""访问日志异步落库（削峰）：
跳转接口只把日志消息推进 Redis 缓冲队列立即返回；
Worker 凑满 100 条或每 5 秒（Beat）批量插入 MySQL。
"""

import asyncio
import json
import threading
from datetime import datetime

import redis as redis_sync
from sqlalchemy import insert

from app.core.config import settings
from app.dao.database import async_session_factory
from app.dao.models import AccessLog
from app.tasks.celery_app import celery_app

BUFFER_KEY = "buffer:access_log"
FLUSH_SIZE = 500  # 单次落库最多取出的条数

_sync_redis: redis_sync.Redis | None = None


def _get_redis() -> redis_sync.Redis:
    """Worker 是同步环境，用同步 Redis 客户端操作缓冲区（主 Redis db0）"""
    global _sync_redis
    if _sync_redis is None:
        _sync_redis = redis_sync.Redis.from_url(settings.redis_url, decode_responses=True)
    return _sync_redis


@celery_app.task(ignore_result=True)
def push_access_log(short_code: str, ip: str, user_agent: str, referer: str, accessed_at: str):
    """跳转接口调用：日志只进缓冲队列，立即返回，不阻塞跳转"""
    record = json.dumps(
        {
            "short_code": short_code,
            "ip": ip,
            "user_agent": user_agent,
            "referer": referer,
            "accessed_at": accessed_at,
        },
        ensure_ascii=False,
    )
    r = _get_redis()
    r.rpush(BUFFER_KEY, record)
    # 凑够批量阈值立即触发落库；否则等 Beat 定时任务
    if r.llen(BUFFER_KEY) >= settings.log_flush_batch:
        flush_access_logs.delay()


@celery_app.task(ignore_result=True)
def flush_access_logs():
    """批量落库：LPOP 一批日志，一次 insert 写入 access_log"""
    r = _get_redis()
    pipe = r.pipeline()
    pipe.lrange(BUFFER_KEY, 0, FLUSH_SIZE - 1)
    pipe.ltrim(BUFFER_KEY, FLUSH_SIZE, -1)
    raw_items, _ = pipe.execute()
    if not raw_items:
        return 0

    rows = []
    for raw in raw_items:
        try:
            row = json.loads(raw)
            row["accessed_at"] = datetime.fromisoformat(row["accessed_at"])
            rows.append(row)
        except (json.JSONDecodeError, ValueError):
            continue
    if not rows:
        return 0

    _run_async(_bulk_insert(rows))
    return len(rows)


def _run_async(coro):
    """兼容两种执行环境：
    - 独立 Worker 进程（无事件循环）：直接 asyncio.run
    - eager 模式（跑在 FastAPI 事件循环内）：切到子线程跑新循环
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result = {}

    def _target():
        result["value"] = asyncio.run(coro)

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join()
    return result.get("value")


async def _bulk_insert(rows: list[dict]) -> None:
    async with async_session_factory() as session:
        await session.execute(insert(AccessLog), rows)
        await session.commit()
