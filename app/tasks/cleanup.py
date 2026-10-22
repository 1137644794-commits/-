"""过期短链定时清理（Celery Beat 主动治理）：

读路径对过期短链是惰性判断（返回 410 但数据仍在）；
本任务周期性物理删除过期数据并清缓存，避免冷数据无限膨胀。
"""

from datetime import datetime

from celery.utils.log import get_task_logger
from sqlalchemy import delete, select

from app.cache.redis_client import get_redis
from app.dao.database import async_session_factory
from app.dao.models import ShortLink
from app.tasks.access_log import _run_async
from app.tasks.celery_app import celery_app

logger = get_task_logger(__name__)


async def _cleanup() -> int:
    async with async_session_factory() as session:
        expired_filter = (
            ShortLink.expire_at.is_not(None),
            ShortLink.expire_at < datetime.now(),
        )
        codes = (await session.execute(select(ShortLink.short_code).where(*expired_filter))).scalars().all()
        if not codes:
            return 0
        await session.execute(delete(ShortLink).where(*expired_filter))
        await session.commit()

    # 删除对应缓存键，避免脏缓存
    redis = get_redis()
    for code in codes:
        await redis.delete(f"link:{code}")
    return len(codes)


@celery_app.task(ignore_result=True)
def cleanup_expired_links() -> int:
    """Beat 定时调用：物理删除过期短链，返回删除条数"""
    deleted = _run_async(_cleanup())
    if deleted:
        logger.info("过期短链定时清理完成，删除 %d 条", deleted)
    return deleted
