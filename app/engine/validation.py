"""校验引擎：检查一道题的答案是否满足题目规则（纯函数，不访问数据库）。

按题型查表执行：题目规则全部来自问卷文档里的 validation 字段，没有针对某份问卷的代码。
新增题型时，在 CHECKERS 里加一个检查函数即可。
"""

import math
from numbers import Real
from typing import Any, Callable

# "看过、不答"：答案为 None、空字符串（含只有空格）或空数组
EMPTY = object()

REQUIRED_MESSAGE = "此题为必答题"


def is_empty(value: Any) -> bool:
    return (
        value is None
        or (isinstance(value, str) and value.strip() == "")
        or (isinstance(value, list) and len(value) == 0)
    )


def _is_number(v: Any) -> bool:
    return isinstance(v, Real) and not isinstance(v, bool) and math.isfinite(v)


def _fmt(n: Real) -> str:
    """18.0 显示为 18。"""
    return str(int(n)) if float(n).is_integer() else str(n)


# ---------- 各题型的检查函数：返回 (整理后的值, 错误信息或 None) ----------

def _check_single(q: dict, v: Any) -> tuple[Any, str | None]:
    option_ids = {o["id"] for o in q["options"]}
    if not isinstance(v, str) or v not in option_ids:
        return None, "请选择本题给出的选项"
    return v, None


def _check_multiple(q: dict, v: Any) -> tuple[Any, str | None]:
    order = [o["id"] for o in q["options"]]
    if not isinstance(v, list) or not all(isinstance(x, str) and x in order for x in v):
        return None, "请选择本题给出的选项"
    if len(set(v)) != len(v):
        return None, "同一个选项不能重复选择"
    rule = q.get("validation") or {}
    lo, hi, n = rule.get("minSelect"), rule.get("maxSelect"), len(v)
    if lo is not None and lo == hi and n != lo:
        return None, f"请选择 {lo} 项"
    if lo is not None and n < lo:
        return None, f"至少选择 {lo} 项"
    if hi is not None and n > hi:
        return None, f"最多选择 {hi} 项"
    # 按选项原本的顺序保存，与提交时的点击顺序无关
    chosen = set(v)
    return [oid for oid in order if oid in chosen], None


def _check_text(q: dict, v: Any) -> tuple[Any, str | None]:
    if not isinstance(v, str):
        return None, "请输入文字"
    text = v.strip()
    rule = q.get("validation") or {}
    lo, hi = rule.get("minLength"), rule.get("maxLength")
    if lo is not None and len(text) < lo:
        return None, f"至少输入 {lo} 个字"
    if hi is not None and len(text) > hi:
        return None, f"最多输入 {hi} 个字"
    return text, None


def _check_number(q: dict, v: Any) -> tuple[Any, str | None]:
    if not _is_number(v):
        return None, "请输入数字"
    rule = q.get("validation") or {}
    integer = rule.get("integer", False)
    kind = "整数" if integer else "数字"
    if integer:
        if isinstance(v, float):
            if not v.is_integer():
                return None, "请输入整数"
            v = int(v)  # 18.0 视为 18
    lo, hi = rule.get("min"), rule.get("max")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        if lo is not None and hi is not None:
            return None, f"请输入 {_fmt(lo)}–{_fmt(hi)} 之间的{kind}"
        if lo is not None:
            return None, f"请输入不小于 {_fmt(lo)} 的{kind}"
        return None, f"请输入不大于 {_fmt(hi)} 的{kind}"
    return v, None


CHECKERS: dict[str, Callable[[dict, Any], tuple[Any, str | None]]] = {
    "single_choice": _check_single,
    "multiple_choice": _check_multiple,
    "text_input": _check_text,
    "number_input": _check_number,
}


def validate_answer(question: dict, value: Any) -> tuple[Any, str | None]:
    """检查一道题的答案。

    返回 (整理后的值, 错误信息)：
    - 没答（None / 空字符串 / 空数组）：必答题返回错误；非必答题返回 (EMPTY, None)；
    - 答了：按题型检查，非必答题答了也必须满足规则。
    """
    if is_empty(value):
        return (None, REQUIRED_MESSAGE) if question["required"] else (EMPTY, None)
    return CHECKERS[question["type"]](question, value)
