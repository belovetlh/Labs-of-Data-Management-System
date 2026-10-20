"""整份问卷的结构检查（纯函数，不访问数据库）。

数据库校验器只管结构和类型；这里检查需要看整份问卷才能判断的语义：
选择数量范围、选项重复、跳转规则的条件与目标等（见设计 2.4、2.5、2.7）。
每次修改题目写回前、以及发布前都会对整份问卷调用 check_survey。
"""

from numbers import Real

from db.schema import CHOICE_TYPES, JUMP_OPS, MAX_OPTIONS

END = "END"

OP_LABELS = {
    "eq": "等于", "in": "属于其中之一",
    "containsAny": "包含任一", "containsAll": "包含全部", "equals": "恰好是",
    "ne": "不等于", "gt": "大于", "gte": "大于等于", "lt": "小于", "lte": "小于等于", "between": "在区间内",
}


def question_label(index: int, question: dict) -> str:
    return f"第 {index + 1} 题「{question['title']}」"


def _is_number(v) -> bool:
    return isinstance(v, Real) and not isinstance(v, bool)


def check_question(q: dict) -> list[str]:
    """单道题的定义是否自洽。"""
    errors = []
    v = q.get("validation") or {}
    if q["type"] in CHOICE_TYPES:
        options = q.get("options") or []
        if not 2 <= len(options) <= MAX_OPTIONS:
            errors.append(f"选项须为 2–{MAX_OPTIONS} 个")
        labels = [o["label"] for o in options]
        if len(set(labels)) != len(labels):
            errors.append("选项文字不能重复")
        ids = [o["id"] for o in options]
        if len(set(ids)) != len(ids):
            errors.append("选项 id 重复")
        if q["type"] == "multiple_choice":
            lo, hi = v.get("minSelect"), v.get("maxSelect")
            if lo is not None and hi is not None and lo > hi:
                errors.append("最少选择数不能大于最多选择数")
            if lo is not None and lo > len(options):
                errors.append("最少选择数不能超过选项数")
            if hi is not None and hi > len(options):
                errors.append("最多选择数不能超过选项数")
    elif q["type"] == "text_input":
        lo, hi = v.get("minLength"), v.get("maxLength")
        if lo is not None and hi is not None and lo > hi:
            errors.append("最少字数不能大于最多字数")
    elif q["type"] == "number_input":
        lo, hi = v.get("min"), v.get("max")
        if lo is not None and hi is not None and lo > hi:
            errors.append("最小值不能大于最大值")
    return errors


def _check_rule_value(q: dict, op: str, value) -> str | None:
    if q["type"] in CHOICE_TYPES:
        option_ids = {o["id"] for o in q.get("options") or []}
        if q["type"] == "single_choice" and op == "eq":
            if not isinstance(value, str):
                return "条件的值应为一个选项"
            return None if value in option_ids else "使用了本题不存在的选项"
        if not isinstance(value, list) or not value or not all(isinstance(x, str) for x in value):
            return "条件的值应为至少一个选项"
        if len(set(value)) != len(value):
            return "条件里的选项重复"
        return None if set(value) <= option_ids else "使用了本题不存在的选项"
    # number_input
    if op == "between":
        if not (isinstance(value, list) and len(value) == 2 and all(_is_number(x) for x in value)):
            return "区间条件的值应为 [下限, 上限] 两个数字"
        return None if value[0] <= value[1] else "区间下限不能大于上限"
    return None if _is_number(value) else "条件的值应为数字"


def check_rule(q: dict, index: int, rule: dict, positions: dict[str, int]) -> list[str]:
    """一条跳转规则：条件与题型匹配、值合法、目标存在且在后面。"""
    errors = []
    allowed = JUMP_OPS.get(q["type"])
    if allowed is None:
        return ["文本填空题不支持跳转"]
    op = rule["when"].get("op")
    if op not in allowed:
        names = " / ".join(f"{OP_LABELS[o]}（{o}）" for o in allowed)
        errors.append(f"该题型的跳转条件只能是 {names}")
    else:
        msg = _check_rule_value(q, op, rule["when"].get("value"))
        if msg:
            errors.append(msg)
    target = rule["goto"]
    if target != END:
        if target not in positions:
            errors.append("跳转目标题不存在")
        elif positions[target] <= index:
            errors.append("只能跳到后面的题")
    return errors


def check_survey(questions: list[dict]) -> list[str]:
    """检查整份问卷，返回所有问题的中文说明；空列表表示没有问题。"""
    errors = []
    ids = [q["id"] for q in questions]
    if len(set(ids)) != len(ids):
        errors.append("题目 id 重复")
    positions = {qid: i for i, qid in enumerate(ids)}
    for i, q in enumerate(questions):
        label = question_label(i, q)
        errors += [f"{label}：{m}" for m in check_question(q)]
        for j, rule in enumerate(q.get("jumpRules") or []):
            errors += [f"{label}的第 {j + 1} 条跳转规则：{m}" for m in check_rule(q, i, rule, positions)]
    return errors


def referencing_questions(questions: list[dict], target_id: str) -> list[str]:
    """哪些题的跳转规则跳到了 target_id，返回这些题的说明。"""
    return [
        question_label(i, q)
        for i, q in enumerate(questions)
        if any(r["goto"] == target_id for r in q.get("jumpRules") or [])
    ]
