"""访问统计接口：单日 PV/UV、按天聚合、Top N 热门短链

注意路由注册顺序：/top 必须在 /{short_code} 之前，否则会被动态路由吞掉。
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_id
from app.dao.database import get_db
from app.schemas.stats import DailyStatsOut, StatsOut, TopItemOut
from app.services.stats_service import StatsService

router = APIRouter(prefix="/stats", tags=["访问统计"])


@router.get("/top", response_model=list[TopItemOut])
async def get_top(
    limit: int = Query(default=10, ge=1, le=100),
    user_id: int = Depends(get_current_user_id),
):
    """当日 Top N 热门短链"""
    return await StatsService.get_top(limit)


@router.get("/{short_code}/daily", response_model=list[DailyStatsOut])
async def get_series(
    short_code: str = Path(pattern=r"^[A-Za-z0-9_-]{1,16}$"),
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """某短链按天聚合的 PV/UV 序列"""
    return await StatsService(db).get_series(user_id, short_code)


@router.get("/{short_code}", response_model=StatsOut)
async def get_daily(
    short_code: str = Path(pattern=r"^[A-Za-z0-9_-]{1,16}$"),
    date: str = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """某短链指定日期（默认今天）的 PV/UV"""
    date = date or datetime.now().strftime("%Y-%m-%d")
    return await StatsService(db).get_daily(user_id, short_code, date)
