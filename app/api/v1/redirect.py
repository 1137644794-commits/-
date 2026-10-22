"""短链跳转入口：GET /s/{short_code} -> 302"""

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.dao.database import get_db
from app.services.link_service import LinkService

router = APIRouter(prefix="/s", tags=["短链跳转"])


@router.get("/{short_code}", response_class=RedirectResponse)
async def redirect(
    short_code: str = Path(pattern=r"^[A-Za-z0-9_-]{1,16}$"),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    return await LinkService(db).redirect(short_code, request)
