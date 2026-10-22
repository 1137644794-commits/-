"""异步数据库引擎与会话管理"""

import asyncio
import logging

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

logger = logging.getLogger("shortlink.db")

engine = create_async_engine(settings.database_url, pool_pre_ping=True, echo=False)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    """FastAPI 依赖：请求级会话"""
    async with async_session_factory() as session:
        yield session


async def create_all(retries: int = 10, delay: float = 3.0) -> None:
    """启动时自动建表，带重试以等待 MySQL 就绪"""
    for attempt in range(retries):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return
        except Exception:
            if attempt == retries - 1:
                raise
            logger.warning("等待 MySQL 就绪（%s/%s）...", attempt + 1, retries)
            await asyncio.sleep(delay)
