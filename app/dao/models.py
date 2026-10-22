"""ORM 模型：核心三张表 user / short_link / access_log"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.dao.database import Base


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ShortLink(Base):
    __tablename__ = "short_link"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 跳转查询走的字段，必须唯一索引；origin_url 是长文本无查询场景，不加索引
    short_code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    origin_url: Mapped[str] = mapped_column(String(2048))
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), index=True)
    expire_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AccessLog(Base):
    """访问日志：量最大的一张表，只做单列索引。

    扩展点：数据量大时按天分表或按 short_code 哈希分表。
    """

    __tablename__ = "access_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    short_code: Mapped[str] = mapped_column(String(16), index=True)
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(512), default="")
    referer: Mapped[str] = mapped_column(String(512), default="")
    accessed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
