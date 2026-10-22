"""短链跳转缓存封装：对应缓存三兄弟问题的完整策略。

- 穿透：不存在的短码写空值缓存（短 TTL）+ 布隆过滤器拦截
- 击穿：热点短链逻辑过期后，用 SET NX 分布式锁保证只有一个请求回源重建
- 雪崩：物理 TTL = 逻辑 TTL + 随机抖动，避免批量同时过期
"""

import json
import random

from app.cache.redis_client import get_redis
from app.core.config import settings

LINK_PREFIX = "link:"
EMPTY_VALUE = "__EMPTY__"
LOCK_PREFIX = "lock:rebuild:"


def _key(short_code: str) -> str:
    return f"{LINK_PREFIX}{short_code}"


def _lock_key(short_code: str) -> str:
    return f"{LOCK_PREFIX}{short_code}"


def jitter() -> int:
    """TTL 随机抖动，防雪崩"""
    return random.randint(1, settings.cache_jitter_seconds)


async def get_link(short_code: str) -> tuple[str, dict | None]:
    """返回 (status, data)，status: hit / empty / miss"""
    raw = await get_redis().get(_key(short_code))
    if raw is None:
        return "miss", None
    if raw == EMPTY_VALUE:
        return "empty", None
    return "hit", json.loads(raw)


async def set_link(short_code: str, payload: dict, ttl: int) -> None:
    await get_redis().set(_key(short_code), json.dumps(payload), ex=max(ttl, 1))


async def set_empty(short_code: str) -> None:
    """空值缓存：短 TTL，拦截穿透请求"""
    await get_redis().set(_key(short_code), EMPTY_VALUE, ex=settings.cache_empty_ttl)


async def delete_link(short_code: str) -> None:
    """更新/禁用/删除短链后调用：先更 DB 再删缓存"""
    await get_redis().delete(_key(short_code))


async def acquire_rebuild_lock(short_code: str) -> bool:
    """SET NX EX：分布式锁，只有抢到锁的请求才回源重建缓存"""
    return bool(await get_redis().set(_lock_key(short_code), "1", nx=True, ex=settings.cache_lock_ttl))


async def release_rebuild_lock(short_code: str) -> None:
    await get_redis().delete(_lock_key(short_code))
