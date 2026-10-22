"""滑动窗口限流依赖（按 IP 维度）

创建 + 跳转双向限流（同类短链服务标配：防滥用 + 防短码枚举扫描）。
Redis 故障时 fail-open 放行：限流是防护手段，不能反过来成为单点故障。
"""

import logging

from fastapi import HTTPException, Request

from app.cache.rate_limiter import RateLimiter
from app.cache.redis_client import get_redis
from app.core.config import settings

logger = logging.getLogger("shortlink.rate_limit")

_limiter: RateLimiter | None = None


def _get_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(get_redis())
    return _limiter


async def _check(scope: str, ip: str, limit: int, window: int) -> None:
    try:
        allowed, _ = await _get_limiter().check(f"rl:{scope}:{ip}", limit, window)
    except Exception:
        # fail-open：Redis 故障时限流降级放行，由服务自身降级逻辑兜底
        logger.warning("限流器不可用，降级放行: scope=%s ip=%s", scope, ip, exc_info=True)
        return
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="请求过于频繁，请稍后再试",
            headers={"Retry-After": str(window)},
        )


async def check_create_link_rate(request: Request) -> None:
    """创建短链限流：超过阈值返回 429，窗口滑动后自动恢复"""
    ip = request.client.host if request.client else "unknown"
    await _check("create_link", ip, settings.rate_limit_max, settings.rate_limit_window)


async def check_redirect_rate(request: Request) -> None:
    """跳转限流：防爬虫刷跳转、防遍历短码枚举（阈值比创建宽松）"""
    ip = request.client.host if request.client else "unknown"
    await _check(
        "redirect",
        ip,
        settings.redirect_rate_limit_max,
        settings.redirect_rate_limit_window,
    )
