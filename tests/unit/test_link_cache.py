"""单元测试：缓存策略 —— 测试设计文档 §2.3/§2.4

fakeredis 替换真实 Redis，验证：普通缓存读写、空值缓存写入与短 TTL、
TTL 随机抖动范围、删除缓存、重建分布式锁互斥。
"""

import pytest

from app.cache import link_cache
from app.core.config import settings


@pytest.mark.asyncio
async def test_set_and_get_link(fake_redis):
    await link_cache.set_link("abc", {"origin_url": "https://example.com", "is_disabled": False}, 100)
    status, data = await link_cache.get_link("abc")

    assert status == "hit"
    assert data["origin_url"] == "https://example.com"
    ttl = await fake_redis.ttl("link:abc")
    assert 0 < ttl <= 100


@pytest.mark.asyncio
async def test_miss_returns_miss():
    status, data = await link_cache.get_link("ghost")
    assert status == "miss"
    assert data is None


class TestEmptyCache:
    """空值缓存：防穿透的关键策略"""

    @pytest.mark.asyncio
    async def test_set_empty_writes_sentinel_with_short_ttl(self, fake_redis):
        await link_cache.set_empty("nope")

        status, data = await link_cache.get_link("nope")
        assert status == "empty"
        assert data is None

        raw = await fake_redis.get("link:nope")
        assert raw == link_cache.EMPTY_VALUE
        ttl = await fake_redis.ttl("link:nope")
        # 短 TTL：60 秒内自动过期，避免误伤后面真实创建的短码
        assert 0 < ttl <= settings.cache_empty_ttl

    @pytest.mark.asyncio
    async def test_empty_cache_expires(self, fake_redis):
        await link_cache.set_empty("temp")
        await fake_redis.delete("link:temp")
        status, _ = await link_cache.get_link("temp")
        assert status == "miss"


@pytest.mark.asyncio
async def test_delete_link_removes_cache(fake_redis):
    await link_cache.set_link("abc", {"origin_url": "https://example.com"}, 100)
    await link_cache.delete_link("abc")
    assert await fake_redis.exists("link:abc") == 0


def test_ttl_jitter_within_configured_range():
    """TTL 随机抖动：几百次采样都必须落在 [1, cache_jitter_seconds] 内"""
    for _ in range(300):
        value = link_cache.jitter()
        assert 1 <= value <= settings.cache_jitter_seconds


class TestRebuildLock:
    """缓存重建分布式锁：SET NX，互斥 + 可释放"""

    @pytest.mark.asyncio
    async def test_lock_mutual_exclusion(self, fake_redis):
        assert await link_cache.acquire_rebuild_lock("hot") is True
        # 锁未释放前，其他请求抢不到
        assert await link_cache.acquire_rebuild_lock("hot") is False

        ttl = await fake_redis.ttl("lock:rebuild:hot")
        assert 0 < ttl <= settings.cache_lock_ttl

        await link_cache.release_rebuild_lock("hot")
        assert await link_cache.acquire_rebuild_lock("hot") is True
