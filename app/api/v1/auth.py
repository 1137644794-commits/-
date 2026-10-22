"""用户认证接口：注册 / 登录 / 刷新 / 登出"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_id
from app.dao.database import get_db
from app.schemas.common import MessageOut
from app.schemas.user import LoginIn, RefreshIn, RegisterIn, TokenPair
from app.services.user_service import UserService

router = APIRouter(prefix="/auth", tags=["用户认证"])


@router.post("/register", status_code=201, response_model=MessageOut)
async def register(data: RegisterIn, db: AsyncSession = Depends(get_db)):
    await UserService(db).register(data.username, data.password)
    return {"message": "注册成功"}


@router.post("/login", response_model=TokenPair)
async def login(data: LoginIn, db: AsyncSession = Depends(get_db)):
    return await UserService(db).login(data.username, data.password)


@router.post("/refresh", response_model=TokenPair)
async def refresh(data: RefreshIn, db: AsyncSession = Depends(get_db)):
    return await UserService(db).refresh(data.refresh_token)


@router.post("/logout", response_model=MessageOut)
async def logout(
    data: RefreshIn,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    await UserService(db).logout(data.refresh_token)
    return {"message": "登出成功"}
