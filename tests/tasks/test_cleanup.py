"""过期短链定时清理任务测试 —— 惰性删除之外的主动治理

验证：过期短链被物理删除 + 缓存键被清除；未过期/永久短链不受影响。
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.cache import link_cache
from app.dao import crud
from app.dao.models import ShortLink
from app.tasks.cleanup import cleanup_expired_links


@pytest.mark.asyncio
async def test_cleanup_deletes_expired_and_keeps_alive(db_session, fake_redis):
    await crud.create_user(db_session, "cleaner", "x")
    await db_session.commit()
    user = await crud.get_user_by_username(db_session, "cleaner")

    # 一条已过期、一条未过期、一条永久
    await crud.create_link(
        db_session,
        short_code="old001",
        origin_url="https://example.com/old",
        user_id=user.id,
        expire_at=datetime.now() - timedelta(seconds=10),
    )
    await crud.create_link(
        db_session,
        short_code="new001",
        origin_url="https://example.com/new",
        user_id=user.id,
        expire_at=datetime.now() + timedelta(hours=1),
    )
    await crud.create_link(
        db_session,
        short_code="forever1",
        origin_url="https://example.com/forever",
        user_id=user.id,
    )
    await db_session.commit()
    # 给过期链留一个脏缓存键，验证清理会一并删掉
    await link_cache.set_link("old001", {"origin_url": "https://example.com/old"}, 100)

    deleted = cleanup_expired_links()

    assert deleted == 1
    remaining = (await db_session.execute(select(ShortLink.short_code))).scalars().all()
    assert sorted(remaining) == ["forever1", "new001"]
    # 缓存键被清除
    assert await fake_redis.exists("link:old001") == 0


@pytest.mark.asyncio
async def test_cleanup_noop_when_nothing_expired(db_session):
    assert cleanup_expired_links() == 0
    count = await db_session.scalar(select(func.count()).select_from(ShortLink))
    assert count == 0
