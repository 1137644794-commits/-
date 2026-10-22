"""用户服务：注册、登录、JWT 刷新、登出"""

import jwt
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis_client import get_redis
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    refresh_token_ttl,
    verify_password,
)
from app.dao.crud import create_user, get_user_by_username
from app.dao.models import User


def _refresh_key(jti: str) -> str:
    """jti 全局唯一（uuid4），存 Redis 便于主动失效"""
    return f"refresh:{jti}"


def _blacklist_key(jti: str) -> str:
    return f"blacklist:{jti}"


class UserService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ---------- 注册 ----------
    async def register(self, username: str, password: str) -> User:
        if await get_user_by_username(self.db, username):
            raise HTTPException(status_code=409, detail="用户名已存在")
        try:
            user = await create_user(self.db, username, hash_password(password))
            await self.db.commit()
        except IntegrityError:
            # 并发注册同名用户时唯一索引兜底
            await self.db.rollback()
            raise HTTPException(status_code=409, detail="用户名已存在")
        return user

    # ---------- 登录 ----------
    async def login(self, username: str, password: str) -> dict:
        user = await get_user_by_username(self.db, username)
        if user is None or not verify_password(password, user.password_hash):
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        return await self._issue_tokens(user)

    async def _issue_tokens(self, user: User) -> dict:
        jti, refresh_token = create_refresh_token(user.id)
        await get_redis().set(_refresh_key(jti), "1", ex=refresh_token_ttl())
        access_token = create_access_token(user.id, user.username)
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
        }

    # ---------- 刷新（Refresh Token 轮换） ----------
    async def refresh(self, refresh_token: str) -> dict:
        payload = self._parse_refresh_token(refresh_token)
        jti = payload["jti"]
        if not await get_redis().exists(_refresh_key(jti)):
            raise HTTPException(status_code=401, detail="Refresh Token 已失效")
        user = await self.db.get(User, int(payload["sub"]))
        if user is None:
            raise HTTPException(status_code=401, detail="用户不存在")
        # 轮换：旧 jti 作废，签发新双 Token，可防 Refresh Token 泄漏后被长期盗用
        await get_redis().delete(_refresh_key(jti))
        return await self._issue_tokens(user)

    # ---------- 登出（Refresh Token 黑名单） ----------
    async def logout(self, refresh_token: str) -> None:
        try:
            payload = self._parse_refresh_token(refresh_token)
        except HTTPException:
            return
        jti = payload["jti"]
        key = _refresh_key(jti)
        ttl = await get_redis().ttl(key)
        if ttl > 0:
            await get_redis().set(_blacklist_key(jti), "1", ex=ttl)
        await get_redis().delete(key)

    def _parse_refresh_token(self, token: str) -> dict:
        try:
            payload = decode_token(token)
        except jwt.PyJWTError:
            raise HTTPException(status_code=401, detail="无效的 Refresh Token")
        if payload.get("type") != "refresh" or not payload.get("jti"):
            raise HTTPException(status_code=401, detail="Token 类型错误")
        return payload
