"""统计服务：PV/UV 实时读 Redis，按天聚合、Top N 热门短链"""

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis_client import get_redis
from app.dao import crud


def _pv_key(short_code: str, date: str) -> str:
    return f"pv:{short_code}:{date}"


def _uv_key(short_code: str, date: str) -> str:
    return f"uv:{short_code}:{date}"


class StatsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _check_owner(self, user_id: int, short_code: str) -> None:
        link = await crud.get_link_by_code(self.db, short_code)
        if link is None:
            raise HTTPException(status_code=404, detail="短链接不存在")
        if link.user_id != user_id:
            raise HTTPException(status_code=403, detail="无权查看该短链接的统计")

    @staticmethod
    async def _read_day(short_code: str, date: str) -> dict:
        redis = get_redis()
        pv = int(await redis.get(_pv_key(short_code, date)) or 0)
        uv = await redis.pfcount(_uv_key(short_code, date))
        return {"date": date, "pv": pv, "uv": uv}

    async def get_daily(self, user_id: int, short_code: str, date: str) -> dict:
        """单日 PV/UV"""
        await self._check_owner(user_id, short_code)
        data = await self._read_day(short_code, date)
        return {"short_code": short_code, **data}

    async def get_series(self, user_id: int, short_code: str) -> list[dict]:
        """按天聚合：扫描 pv:{code}:* 得到全部有访问的日期"""
        await self._check_owner(user_id, short_code)
        redis = get_redis()
        result = []
        async for key in redis.scan_iter(match=f"pv:{short_code}:*"):
            date = key.split(":")[-1]
            result.append(await self._read_day(short_code, date))
        result.sort(key=lambda item: item["date"])
        return result

    @staticmethod
    async def get_top(limit: int) -> list[dict]:
        """当日 Top N 热门短链：扫描 pv:*:{today} 后排序"""
        date = datetime.now().strftime("%Y-%m-%d")
        redis = get_redis()
        counts: list[tuple[str, int]] = []
        async for key in redis.scan_iter(match=f"pv:*:{date}"):
            short_code = key.split(":")[1]
            counts.append((short_code, int(await redis.get(key) or 0)))
        counts.sort(key=lambda item: -item[1])
        return [{"short_code": code, "pv": pv} for code, pv in counts[:limit]]
