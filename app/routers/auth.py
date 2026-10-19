from fastapi import APIRouter, Depends, HTTPException, status
from pymongo.database import Database

from app.db import get_db
from app.deps import get_current_user
from app.models import LoginRequest, RegisterRequest, TokenOut, UserOut
from app.security import create_access_token
from app.services.users import UsernameTaken, authenticate, create_user, to_user_out

router = APIRouter(prefix="/api/auth", tags=["账号"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED, summary="注册")
def register(body: RegisterRequest, db: Database = Depends(get_db)):
    try:
        user = create_user(db, body.username, body.password, body.display_name)
    except UsernameTaken:
        raise HTTPException(status.HTTP_409_CONFLICT, "该账号已被注册") from None
    return to_user_out(user)


@router.post("/login", response_model=TokenOut, summary="登录")
def login(body: LoginRequest, db: Database = Depends(get_db)):
    user = authenticate(db, body.username.strip(), body.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "账号或密码错误")
    token, expires_at = create_access_token(str(user["_id"]))
    return {"access_token": token, "expires_at": expires_at, "user": to_user_out(user)}


@router.get("/me", response_model=UserOut, summary="当前登录用户")
def me(user: dict = Depends(get_current_user)):
    return to_user_out(user)
