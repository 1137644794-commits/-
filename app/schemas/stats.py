from pydantic import BaseModel


class StatsOut(BaseModel):
    short_code: str
    date: str
    pv: int
    uv: int


class DailyStatsOut(BaseModel):
    date: str
    pv: int
    uv: int


class TopItemOut(BaseModel):
    short_code: str
    pv: int
