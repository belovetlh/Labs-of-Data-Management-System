"""接口公用的依赖：当前登录用户。"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pymongo.database import Database

from app.db import get_db
from app.security import decode_access_token
from app.services.users import find_by_id

_bearer = HTTPBearer(auto_error=False, description="登录接口返回的 accessToken")


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Database = Depends(get_db),
) -> dict:
    """需要登录的接口声明 Depends(get_current_user) 即可拿到当前用户文档。"""
    if credentials is None:
        raise _unauthorized("未登录")
    user_id = decode_access_token(credentials.credentials)
    user = find_by_id(db, user_id) if user_id else None
    if user is None:
        raise _unauthorized("登录已失效，请重新登录")
    return user
