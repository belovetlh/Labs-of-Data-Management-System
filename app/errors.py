"""请求格式错误（422）的统一处理：把 Pydantic 自带的英文提示换成中文。

- 只换提示文字，不改任何检查规则；我们自己写的中文提示（如"账号须为……"）原样保留。
- 返回时不带原始输入（FastAPI 默认会原样回显，注册失败时会把用户填的密码发回去）。
"""

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from db.schema import QUESTION_TYPES

TIME_EXAMPLE = "例如 2026-11-01T00:00:00+08:00"

# Pydantic 错误类型 → 中文提示；{field} 为字段名，其余占位符来自错误的 ctx
MESSAGES = {
    "missing": "缺少必填字段：{field}",
    "extra_forbidden": "不允许的字段：{field}",
    "timezone_aware": f"{{field}}：时间必须带时区，{TIME_EXAMPLE}",
    "datetime_parsing": f"{{field}}：时间格式不正确，{TIME_EXAMPLE}",
    "datetime_from_date_parsing": f"{{field}}：时间格式不正确，{TIME_EXAMPLE}",
    "datetime_type": f"{{field}}：时间格式不正确，{TIME_EXAMPLE}",
    "int_type": "{field}：应为整数",
    "int_parsing": "{field}：应为整数",
    "int_from_float": "{field}：应为整数",
    "float_type": "{field}：应为数字",
    "float_parsing": "{field}：应为数字",
    "finite_number": "{field}：应为有限的数字",
    "bool_type": "{field}：应为 true 或 false",
    "bool_parsing": "{field}：应为 true 或 false",
    "string_type": "{field}：应为文字",
    "list_type": "{field}：应为数组",
    "dict_type": "{field}：应为对象",
    "model_type": "{field}：应为对象",
    "model_attributes_type": "{field}：应为对象",
    "greater_than_equal": "{field}：不能小于 {ge}",
    "greater_than": "{field}：必须大于 {gt}",
    "less_than_equal": "{field}：不能大于 {le}",
    "less_than": "{field}：必须小于 {lt}",
    "too_short": "{field}：至少需要 {min_length} 项",
    "too_long": "{field}：最多 {max_length} 项",
    "string_too_short": "{field}：至少 {min_length} 个字符",
    "string_too_long": "{field}：最多 {max_length} 个字符",
    "union_tag_invalid": "不支持的题型：{tag}，可选：" + " / ".join(QUESTION_TYPES),
    "union_tag_not_found": "缺少题型字段 type，可选：" + " / ".join(QUESTION_TYPES),
    "json_invalid": "请求体不是合法的 JSON",
}

# 位置信息里不算"字段名"的部分：请求的哪一块，以及按题型区分时 Pydantic 插入的题型名
_NOT_FIELDS = {"body", "query", "path", "header", *QUESTION_TYPES}


def _field_name(loc) -> str:
    names = [str(x) for x in loc if isinstance(x, str) and x not in _NOT_FIELDS]
    return names[-1] if names else "请求"


def translate(error: dict) -> dict:
    """把一条 Pydantic 错误转成 {type, loc, msg}，msg 为中文；不认识的类型保留原提示。"""
    template = MESSAGES.get(error["type"])
    msg = error.get("msg", "")
    if template:
        try:
            msg = template.format(field=_field_name(error.get("loc", ())), **(error.get("ctx") or {}))
        except (KeyError, IndexError):
            pass  # ctx 缺少占位符时退回原提示
    return {"type": error["type"], "loc": list(error.get("loc", ())), "msg": msg}


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422,
                        content={"detail": jsonable_encoder([translate(e) for e in exc.errors()])})


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, validation_error_handler)
