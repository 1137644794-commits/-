"""数据访问层：只做 SQL，不含业务逻辑，便于单测 mock"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dao.models import ShortLink, User


async def get_user_by_username(db: AsyncSession, username: str) -> User | None:
    return await db.scalar(select(User).where(User.username == username))


async def create_user(db: AsyncSession, username: str, password_hash: str) -> User:
    """add + flush：唯一约束冲突（重复用户名）在此抛出 IntegrityError"""
    user = User(username=username, password_hash=password_hash)
    db.add(user)
    await db.flush()
    return user


async def get_link_by_code(db: AsyncSession, short_code: str) -> ShortLink | None:
    return await db.scalar(select(ShortLink).where(ShortLink.short_code == short_code))


async def create_link(db: AsyncSession, **kwargs) -> ShortLink:
    """add + flush：短码唯一约束冲突在此抛出 IntegrityError"""
    link = ShortLink(**kwargs)
    db.add(link)
    await db.flush()
    return link


async def list_links_with_total(db: AsyncSession, user_id: int, offset: int, limit: int) -> tuple[list[ShortLink], int]:
    total = await db.scalar(select(func.count()).select_from(ShortLink).where(ShortLink.user_id == user_id))
    result = await db.execute(
        select(ShortLink).where(ShortLink.user_id == user_id).order_by(ShortLink.id.desc()).offset(offset).limit(limit)
    )
    return list(result.scalars().all()), total or 0


async def update_link(db: AsyncSession, link: ShortLink, **kwargs) -> ShortLink:
    for field, value in kwargs.items():
        setattr(link, field, value)
    await db.flush()
    return link


async def delete_link(db: AsyncSession, link: ShortLink) -> None:
    await db.delete(link)
    await db.flush()


async def get_all_codes(db: AsyncSession) -> list[str]:
    """启动时预热布隆过滤器用"""
    result = await db.execute(select(ShortLink.short_code))
    return list(result.scalars().all())
