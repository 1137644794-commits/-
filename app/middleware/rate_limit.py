"""创建短链接口的滑动窗口限流依赖（按 IP 维度）"""

from fastapi import HTTPException, Request

from app.cache.rate_limiter import RateLimiter
from app.cache.redis_client import get_redis
from app.core.config import settings

_limiter: RateLimiter | None = None


def _get_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(get_redis())
    return _limiter


async def check_create_link_rate(request: Request) -> None:
    """超过阈值返回 429，窗口滑动后自动恢复"""
    ip = request.client.host if request.client else "unknown"
    allowed, _ = await _get_limiter().check(f"rl:create_link:{ip}", settings.rate_limit_max, settings.rate_limit_window)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="请求过于频繁，请稍后再试",
            headers={"Retry-After": str(settings.rate_limit_window)},
        )
