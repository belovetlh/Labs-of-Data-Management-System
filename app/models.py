"""接口的请求 / 响应数据格式。字段名在 JSON 中使用驼峰（如 displayName）。"""

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel
from pydantic_core import PydanticCustomError

from db.schema import USERNAME_PATTERN

# 可打印 ASCII（英文字母、数字、英文符号），不含空格；一个字符一个字节，72 正好是 bcrypt 上限
PASSWORD_PATTERN = r"^[\x21-\x7E]{8,72}$"


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# ---------- 账号 ----------

class RegisterRequest(ApiModel):
    username: str = Field(description="账号：3–32 位字母、数字或下划线，区分大小写，唯一")
    password: str = Field(description="密码：8–72 位英文字母、数字或英文符号，不能有汉字和空格")
    display_name: str = Field(description="名字：1–32 个字符，可重复")

    @field_validator("username")
    @classmethod
    def _check_username(cls, v: str) -> str:
        v = v.strip()
        if not re.fullmatch(USERNAME_PATTERN, v):
            raise PydanticCustomError("username_format", "账号须为 3–32 位字母、数字或下划线")
        return v

    @field_validator("password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        if not re.fullmatch(PASSWORD_PATTERN, v):
            raise PydanticCustomError(
                "password_format", "密码须为 8–72 位英文字母、数字或英文符号，不能包含汉字或空格"
            )
        return v

    @field_validator("display_name")
    @classmethod
    def _check_display_name(cls, v: str) -> str:
        v = v.strip()
        if not 1 <= len(v) <= 32:
            raise PydanticCustomError("display_name_length", "名字须为 1–32 个字符")
        return v


class LoginRequest(ApiModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=128)


class UserOut(ApiModel):
    id: str
    username: str
    display_name: str
    created_at: datetime


class TokenOut(ApiModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserOut
