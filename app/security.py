"""密码哈希与登录令牌（JWT）。"""

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import settings

JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=settings.bcrypt_rounds)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    data = password.encode("utf-8")
    if len(data) > 72:  # 超过 bcrypt 上限的密码不可能是注册时设置的
        return False
    return bcrypt.checkpw(data, password_hash.encode("ascii"))


# 账号不存在时也做一次同样耗时的核对，避免通过响应快慢判断账号是否存在
_DUMMY_HASH = hash_password("dummy-password-for-timing")


def verify_dummy(password: str) -> None:
    verify_password(password, _DUMMY_HASH)


def create_access_token(user_id: str) -> tuple[str, datetime]:
    """签发令牌，返回（令牌, 过期时间）。令牌里只放用户 id 和过期时间。"""
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    token = jwt.encode({"sub": user_id, "exp": expires_at}, settings.jwt_secret, algorithm=JWT_ALGORITHM)
    return token, expires_at


def decode_access_token(token: str) -> str | None:
    """校验签名和有效期，返回用户 id；无效或过期返回 None。"""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALGORITHM])
    except jwt.InvalidTokenError:
        return None
    sub = payload.get("sub")
    return sub if isinstance(sub, str) else None
