"""短链接接口：创建（限流）/ 我的列表 / 禁用 / 删除"""

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_id
from app.dao.database import get_db
from app.middleware.rate_limit import check_create_link_rate
from app.schemas.link import LinkCreateIn, LinkListOut, LinkOut, LinkPatchIn
from app.services.link_service import LinkService

router = APIRouter(prefix="/links", tags=["短链接"])


@router.post(
    "",
    status_code=201,
    response_model=LinkOut,
    dependencies=[Depends(check_create_link_rate)],  # 创建接口限流防刷
)
async def create_link(
    data: LinkCreateIn,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    return await LinkService(db).create(
        user_id,
        str(data.origin_url),
        data.custom_alias,
        data.expire_in_seconds,
    )


@router.get("", response_model=LinkListOut)
async def list_links(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    return await LinkService(db).list_my_links(user_id, page, size)


@router.patch("/{short_code}", response_model=LinkOut)
async def patch_link(
    data: LinkPatchIn,
    short_code: str = Path(pattern=r"^[A-Za-z0-9_-]{1,16}$"),
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    return await LinkService(db).patch(user_id, short_code, data.is_disabled)


@router.delete("/{short_code}", status_code=204)
async def delete_link(
    short_code: str = Path(pattern=r"^[A-Za-z0-9_-]{1,16}$"),
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    await LinkService(db).delete(user_id, short_code)
    return Response(status_code=204)
