"""跳转引擎：按问卷里配置的跳转规则算出答题路径（纯函数，不访问数据库）。

求值方法（设计 2.5 节）：
1. 从第一题开始；
2. 当前题作答了，就按 jumpRules 的顺序逐条判断，第一条命中的生效；
3. 没有命中或没作答 → 顺序上的下一题；
4. goto 为 END 或已过最后一题 → 结束。

answers 是 {题目 id: 值} 的字典，约定（设计第 3 节第 17 条）：
- 字典里**没有**某道题 → 还没答到；
- 有，但值为 None / 空字符串 / 空数组 → 看过、不答。
"""

import operator
from dataclasses import dataclass, field
from typing import Any, Callable

from app.engine.structure import END, question_label
from app.engine.validation import EMPTY, validate_answer

# 条件 → 判断函数。v 为已通过校验的答案，x 为规则里配置的值
OPS: dict[str, Callable[[Any, Any], bool]] = {
    # 单选（eq 也用于数字）
    "eq": operator.eq,
    "in": lambda v, x: v in x,
    # 多选
    "containsAny": lambda v, x: bool(set(v) & set(x)),
    "containsAll": lambda v, x: set(x) <= set(v),
    "equals": lambda v, x: set(v) == set(x),
    # 数字
    "ne": operator.ne,
    "gt": operator.gt,
    "gte": operator.ge,
    "lt": operator.lt,
    "lte": operator.le,
    "between": lambda v, x: x[0] <= v <= x[1],
}


def jump_target(question: dict, value: Any) -> str | None:
    """按顺序找第一条命中的规则，返回其 goto；没作答或都不命中返回 None（进入下一题）。"""
    if value is EMPTY:
        return None
    for rule in question.get("jumpRules") or []:
        if OPS[rule["when"]["op"]](value, rule["when"]["value"]):
            return rule["goto"]
    return None


@dataclass
class Evaluation:
    path: list[str] = field(default_factory=list)         # 走过的题，按顺序
    next_question: str | None = None                       # 下一道要答的题；None 表示已走完
    errors: dict[str, str] = field(default_factory=dict)  # 题目 id → 错误信息
    off_path: list[str] = field(default_factory=list)     # 答了但不在答题路径上的题
    answers: list[dict] = field(default_factory=list)     # 路径上实际作答的题（整理后的值），按路径顺序

    @property
    def complete(self) -> bool:
        """已走到结束、没有错误、没有路径外的答案 → 可以提交。"""
        return self.next_question is None and not self.errors and not self.off_path


def _walk(questions: list[dict], answers: dict[str, Any], stop_at_unreached: bool) -> Evaluation:
    index = {q["id"]: i for i, q in enumerate(questions)}
    result = Evaluation()
    i: int | None = 0
    while i is not None and i < len(questions):
        q = questions[i]
        result.path.append(q["id"])
        if q["id"] not in answers and stop_at_unreached:
            result.next_question = q["id"]  # 还没答到：这就是下一题
            break
        value, error = validate_answer(q, answers.get(q["id"]))
        if error:
            # 答案不合规就无法决定往哪跳，路径停在这道题
            result.errors[q["id"]] = error
            result.next_question = q["id"]
            break
        if value is not EMPTY:
            result.answers.append({"questionId": q["id"], "value": value})
        target = jump_target(q, value)
        if target is None:
            i += 1
        elif target == END:
            i = None
        else:
            i = index[target]

    on_path = set(result.path)
    # 只能往后跳：位置在路径最后一题之前、却不在路径上的题，一定是被跳过的；
    # 路径还没走完时，位置更靠后的题以后可能还会走到，暂不判断
    last = index[result.path[-1]] if result.path else -1
    finished = result.next_question is None
    for qid in answers:
        if qid in index and qid not in on_path and (finished or index[qid] < last):
            result.off_path.append(qid)
    return result


def navigate(questions: list[dict], answers: dict[str, Any]) -> Evaluation:
    """填写过程中：根据目前的答案算出路径、下一题和已答题的错误。

    路径外的答案只列在 off_path 里提示前端清掉，不算错误；不存在的题目 id 直接忽略。
    """
    return _walk(questions, answers, stop_at_unreached=True)


def evaluate_submission(questions: list[dict], answers: dict[str, Any]) -> Evaluation:
    """提交时：从头重算路径并逐题校验（不信任前端）。

    路径上没出现在 answers 里的题按"不答"处理（必答题因此报错）；
    答了路径外的题、答了不存在的题，都记为错误。complete 为 True 才能保存。
    """
    result = _walk(questions, answers, stop_at_unreached=False)
    labels = {q["id"]: question_label(i, q) for i, q in enumerate(questions)}
    for qid in result.off_path:
        result.errors[qid] = f"{labels[qid]}按前面的回答应被跳过，不能作答"
    for qid in answers:
        if qid not in labels:
            result.errors[qid] = f"题目 {qid} 不存在"
    return result
