"""异步任务测试（Celery eager）—— 测试设计文档 §4

eager 模式下任务进程内同步执行，验证：
1. 消息发送后字段完整地进 Redis 缓冲队列；
2. 凑够 100 条自动触发批量插入；
3. 不足 100 条时由 flush 任务（Beat 每 5 秒调用）触发插入。

注意：这里必须通过模块属性访问 _get_redis / async_session_factory，
按值导入（from ... import ...）会拿到 conftest 替换前的真实 Redis/MySQL 引用。
"""

import pytest
from sqlalchemy import func, select

import app.dao.database as database_mod
import app.tasks.access_log as task_mod
from app.dao.models import AccessLog

BUFFER_KEY = task_mod.BUFFER_KEY
push_access_log = task_mod.push_access_log
flush_access_logs = task_mod.flush_access_logs


def _push(count: int, code_prefix: str = "code") -> None:
    for i in range(count):
        push_access_log.delay(
            short_code=f"{code_prefix}{i}",
            ip=f"10.0.0.{i % 255}",
            user_agent="pytest-agent",
            referer="https://ref.example.com",
            accessed_at="2026-01-01T12:00:00",
        )


async def _count_logs() -> int:
    async with database_mod.async_session_factory() as session:
        return await session.scalar(select(func.count()).select_from(AccessLog))


class TestEagerBuffer:
    def test_delay_pushes_message_to_buffer(self):
        push_access_log.delay(
            short_code="abc",
            ip="9.9.9.9",
            user_agent="ua",
            referer="ref",
            accessed_at="2026-01-01T12:00:00",
        )
        assert task_mod._get_redis().llen(BUFFER_KEY) == 1


@pytest.mark.asyncio
async def test_batch_100_triggers_bulk_insert():
    """凑够 100 条：第 100 条触发批量落库"""
    _push(100)
    assert task_mod._get_redis().llen(BUFFER_KEY) == 0  # 已全部消费
    assert await _count_logs() == 100


@pytest.mark.asyncio
async def test_flush_task_inserts_below_batch_threshold():
    """不足 100 条：缓冲不落库，直到 flush 任务（5 秒定时）触发"""
    _push(99)
    assert task_mod._get_redis().llen(BUFFER_KEY) == 99
    assert await _count_logs() == 0

    flushed = flush_access_logs()  # Beat 每 log_flush_interval 秒调用
    assert flushed == 99
    assert task_mod._get_redis().llen(BUFFER_KEY) == 0
    assert await _count_logs() == 99


@pytest.mark.asyncio
async def test_flushed_row_keeps_all_fields():
    push_access_log.delay(
        short_code="xyz",
        ip="9.9.9.9",
        user_agent="ua-x",
        referer="https://ref.example.com",
        accessed_at="2026-01-01T12:00:00",
    )
    flush_access_logs()

    async with database_mod.async_session_factory() as session:
        row = await session.scalar(select(AccessLog).where(AccessLog.short_code == "xyz"))
    assert row.ip == "9.9.9.9"
    assert row.user_agent == "ua-x"
    assert row.referer == "https://ref.example.com"
    assert row.accessed_at.isoformat() == "2026-01-01T12:00:00"


def test_flush_with_empty_buffer_is_noop():
    assert task_mod._get_redis().llen(BUFFER_KEY) == 0
    assert flush_access_logs() == 0
