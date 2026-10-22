"""单元测试：JWT 签发/校验 —— 测试设计文档 §2.5

覆盖：过期 Token、伪造签名、密码哈希、登出后 Refresh Token 黑名单/轮换。
"""

import jwt as pyjwt
import pytest
from fastapi import HTTPException
from freezegun import freeze_time

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.services.user_service import UserService


class TestPasswordHash:
    def test_roundtrip(self):
        hashed = hash_password("secret123")
        assert hashed != "secret123"
        assert verify_password("secret123", hashed)

    def test_wrong_password(self):
        assert not verify_password("wrong", hash_password("secret123"))


class TestAccessToken:
    def test_issue_and_decode(self):
        token = create_access_token(user_id=7, username="alice")
        payload = decode_token(token)
        assert payload["sub"] == "7"
        assert payload["type"] == "access"
        assert payload["username"] == "alice"

    def test_expired_token_raises(self):
        with freeze_time("2026-01-01 00:00:00") as freezer:
            token = create_access_token(user_id=1, username="alice")
            assert decode_token(token)["sub"] == "1"
            # 有效期 30 分钟后
            freezer.move_to("2026-01-01 00:31:00")
            with pytest.raises(pyjwt.ExpiredSignatureError):
                decode_token(token)

    def test_forged_signature_raises(self):
        # 用错误密钥签发的 Token，业务侧必须拒绝
        forged = pyjwt.encode(
            {"sub": "1", "type": "access", "exp": 10**10},
            "attacker-secret",
            algorithm="HS256",
        )
        with pytest.raises(pyjwt.InvalidSignatureError):
            decode_token(forged)

    def test_tampered_token_raises(self):
        token = create_access_token(user_id=1, username="alice")
        tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
        with pytest.raises(pyjwt.PyJWTError):
            decode_token(tampered)


class TestRefreshToken:
    def test_refresh_token_carries_jti(self):
        jti, token = create_refresh_token(user_id=3)
        payload = decode_token(token)
        assert payload["type"] == "refresh"
        assert payload["jti"] == jti
        assert payload["sub"] == "3"


class TestLogoutBlacklist:
    """登出后 Refresh Token 进入黑名单：refresh 必须拒绝（通过 Redis 完成）"""

    @pytest.mark.asyncio
    async def test_logout_invalidates_refresh_token(self, db_session, fake_redis):
        svc = UserService(db_session)
        await svc.register("alice", "secret123")
        tokens = await svc.login("alice", "secret123")
        refresh = tokens["refresh_token"]

        await svc.logout(refresh)

        # jti 已写入黑名单、refresh 键已删除
        jti = decode_token(refresh)["jti"]
        assert await fake_redis.exists(f"blacklist:{jti}") == 1

        with pytest.raises(HTTPException) as exc:
            await svc.refresh(refresh)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_refresh_rotation_invalidates_old_token(self, db_session):
        svc = UserService(db_session)
        await svc.register("bob", "secret123")
        tokens = await svc.login("bob", "secret123")

        # 轮换：旧 token 换到新 token
        new_tokens = await svc.refresh(tokens["refresh_token"])
        assert new_tokens["refresh_token"] != tokens["refresh_token"]

        # 旧 token 再次使用必须失败
        with pytest.raises(HTTPException):
            await svc.refresh(tokens["refresh_token"])
