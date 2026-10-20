"""接口的请求 / 响应数据格式。字段名在 JSON 中使用驼峰（如 displayName）。"""

import re
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel
from pydantic_core import PydanticCustomError

from db.schema import MAX_JUMP_RULES, MAX_OPTIONS, USERNAME_PATTERN

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


# ---------- 问卷 ----------

def _check_future(v: datetime) -> datetime:
    if v <= datetime.now(timezone.utc):
        raise PydanticCustomError("deadline_past", "截止时间必须晚于当前时间")
    return v


def _check_title(v: str) -> str:
    v = v.strip()
    if not 1 <= len(v) <= 200:
        raise PydanticCustomError("title_length", "标题须为 1–200 个字符")
    return v


def _check_description(v: str) -> str:
    if len(v) > 5000:
        raise PydanticCustomError("description_length", "说明不能超过 5000 个字符")
    return v


Title = Annotated[str, AfterValidator(_check_title)]
Description = Annotated[str, AfterValidator(_check_description)]
# 截止时间须带时区（不带时区返回 422），且必须在未来
Deadline = Annotated[AwareDatetime, AfterValidator(_check_future)]


class SurveySettingsIn(ApiModel):
    allow_anonymous: bool = Field(default=False, description="是否允许匿名填写")
    allow_multiple_submissions: bool = Field(default=True, description="同一人是否可以填写多次")
    deadline: Deadline | None = Field(default=None, description="截止时间，须带时区；null 表示不截止")


class SurveySettingsPatch(ApiModel):
    allow_anonymous: bool | None = None
    allow_multiple_submissions: bool | None = None
    deadline: Deadline | None = None


class SurveyCreate(ApiModel):
    title: Title
    description: Description = ""
    settings: SurveySettingsIn = Field(default_factory=SurveySettingsIn)


class SurveyUpdate(ApiModel):
    """只修改传了的字段；deadline 显式传 null 表示取消截止。仅草稿可用。"""

    title: Title | None = None
    description: Description | None = None
    settings: SurveySettingsPatch | None = None


class ReopenRequest(ApiModel):
    """不传 deadline：沿用原截止时间（须为空或在未来）；传 null：取消截止；传时间：设为新的截止时间。"""

    deadline: Deadline | None = None


class SurveySettingsOut(ApiModel):
    allow_anonymous: bool
    allow_multiple_submissions: bool
    deadline: datetime | None


class SurveySummary(ApiModel):
    id: str
    title: str
    status: str = Field(description="存储的状态：draft / published / closed")
    phase: str = Field(description="计算出的阶段：draft 草稿 / open 收集中 / expired 已截止 / closed 已关闭")
    slug: str
    share_url: str
    settings: SurveySettingsOut
    question_count: int
    response_count: int
    created_at: datetime
    published_at: datetime | None = None


class SurveyDetail(SurveySummary):
    description: str
    questions: list[dict]
    updated_at: datetime
    closed_at: datetime | None = None


# ---------- 题目 ----------

class StrictModel(ApiModel):
    """带了未定义的字段直接报错（比如文本题带了 options），不会悄悄忽略。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


def _check_question_title(v: str) -> str:
    v = v.strip()
    if not 1 <= len(v) <= 500:
        raise PydanticCustomError("question_title_length", "题目须为 1–500 个字符")
    return v


def _check_option_label(v: str) -> str:
    v = v.strip()
    if not 1 <= len(v) <= 200:
        raise PydanticCustomError("option_label_length", "选项文字须为 1–200 个字符")
    return v


QuestionTitle = Annotated[str, AfterValidator(_check_question_title)]
OptionLabel = Annotated[str, AfterValidator(_check_option_label)]
Number = Annotated[int | float, Field(allow_inf_nan=False)]


class OptionIn(StrictModel):
    id: str | None = Field(default=None, description="修改题目时，原有选项带上 id 以保持不变；新选项不带")
    label: OptionLabel


Options = Annotated[list[OptionIn], Field(min_length=2, max_length=MAX_OPTIONS)]


class MultipleChoiceValidation(StrictModel):
    min_select: int | None = Field(default=None, ge=0, description="至少选几个")
    max_select: int | None = Field(default=None, ge=1, description="最多选几个；与 minSelect 相等即「必须选 N 个」")


class TextValidation(StrictModel):
    min_length: int | None = Field(default=None, ge=0, description="最少字数")
    max_length: int | None = Field(default=None, ge=1, description="最多字数")


class NumberValidation(StrictModel):
    min: Number | None = Field(default=None, description="最小值")
    max: Number | None = Field(default=None, description="最大值")
    integer: bool = Field(default=False, description="是否必须为整数")


class SingleChoiceIn(StrictModel):
    type: Literal["single_choice"]
    title: QuestionTitle
    required: bool = False
    options: Options


class MultipleChoiceIn(StrictModel):
    type: Literal["multiple_choice"]
    title: QuestionTitle
    required: bool = False
    options: Options
    validation: MultipleChoiceValidation = Field(default_factory=MultipleChoiceValidation)


class TextInputIn(StrictModel):
    type: Literal["text_input"]
    title: QuestionTitle
    required: bool = False
    validation: TextValidation = Field(default_factory=TextValidation)


class NumberInputIn(StrictModel):
    type: Literal["number_input"]
    title: QuestionTitle
    required: bool = False
    validation: NumberValidation = Field(default_factory=NumberValidation)


# 按 type 字段区分题型
QuestionIn = Annotated[
    SingleChoiceIn | MultipleChoiceIn | TextInputIn | NumberInputIn,
    Field(discriminator="type"),
]


class JumpCondition(StrictModel):
    op: str = Field(description="条件，见设计 2.5 节")
    value: Any = Field(description="选项 id、选项 id 数组、数字或 [下限, 上限]")


class JumpRuleIn(StrictModel):
    when: JumpCondition
    goto: str = Field(description='目标题 id，或 "END" 表示结束问卷')


class JumpRulesRequest(StrictModel):
    rules: Annotated[list[JumpRuleIn], Field(max_length=MAX_JUMP_RULES)] = Field(
        description="按顺序判断，第一条命中的生效；空数组表示清空")


class QuestionOrderRequest(StrictModel):
    question_ids: list[str] = Field(description="全部题目 id 的新顺序")
