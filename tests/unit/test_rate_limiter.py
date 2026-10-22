"""单元测试：滑动窗口限流器 —— 测试设计文档 §3.4 + §2 方法 3

fakeredis 解释执行 Lua 脚本，验证：窗口内计数、超阈值拒绝、窗口滑动后恢复。
"""

import pytest
from freezegun import freeze_time

from app.cache.rate_limiter import RateLimiter


@pytest.mark.asyncio
async def test_within_limit_counts_down(fake_redis):
    rl = RateLimiter(fake_redis)
    assert await rl.check("ip:1", limit=3, window=60) == (True, 2)
    assert await rl.check("ip:1", limit=3, window=60) == (True, 1)


@pytest.mark.asyncio
async def test_over_limit_rejected(fake_redis):
    rl = RateLimiter(fake_redis)
    for _ in range(3):
        allowed, _ = await rl.check("ip:2", limit=3, window=60)
        assert allowed is True

    allowed, remaining = await rl.check("ip:2", limit=3, window=60)
    assert allowed is False
    assert remaining == 0


@pytest.mark.asyncio
async def test_window_slide_recovers(fake_redis):
    """窗口滑动后自动恢复 —— 文档 §3.4 第二个用例"""
    rl = RateLimiter(fake_redis)
    with freeze_time("2026-01-01 00:00:00") as freezer:
        for _ in range(3):
            assert (await rl.check("ip:3", limit=3, window=60))[0] is True

        # 窗口内第 4 次：拒绝
        allowed, _ = await rl.check("ip:3", limit=3, window=60)
        assert allowed is False

        # 61 秒后旧请求滑出窗口：恢复
        freezer.move_to("2026-01-01 00:01:01")
        allowed, remaining = await rl.check("ip:3", limit=3, window=60)
        assert allowed is True


@pytest.mark.asyncio
async def test_keys_are_isolated(fake_redis):
    """不同 key（不同 IP）互不影响"""
    rl = RateLimiter(fake_redis)
    for _ in range(2):
        await rl.check("ip:a", limit=2, window=60)
    allowed, _ = await rl.check("ip:a", limit=2, window=60)
    assert allowed is False
    # 另一个 key 不受前者影响
    allowed, remaining = await rl.check("ip:b", limit=2, window=60)
    assert allowed is True
    assert remaining == 1
