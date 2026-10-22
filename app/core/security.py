"""安全模块：密码哈希 + JWT（Access/Refresh 双 Token）"""

import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import settings


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def _create_token(token_type: str, subject: str, expires_delta: timedelta, **extra) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
        **extra,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: int, username: str) -> str:
    return _create_token(
        "access",
        str(user_id),
        timedelta(minutes=settings.access_token_expire_minutes),
        username=username,
    )


def create_refresh_token(user_id: int) -> tuple[str, str]:
    """返回 (jti, token)。jti 存 Redis，可主动失效（登出/轮换）"""
    jti = uuid.uuid4().hex
    token = _create_token(
        "refresh",
        str(user_id),
        timedelta(days=settings.refresh_token_expire_days),
        jti=jti,
    )
    return jti, token


def decode_token(token: str) -> dict:
    """校验签名与过期时间，失败抛 jwt.PyJWTError"""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


def refresh_token_ttl() -> int:
    return settings.refresh_token_expire_days * 86400
