"""校验引擎与跳转引擎的单元测试（纯函数，不经过接口）。"""

import pytest

from app.engine.jump import OPS, evaluate_submission, jump_target, navigate
from app.engine.validation import EMPTY, REQUIRED_MESSAGE, validate_answer


def single(qid="q1", required=True, rules=None):
    return {"id": qid, "type": "single_choice", "title": "你的性别", "required": required,
            "options": [{"id": "oA", "label": "男"}, {"id": "oB", "label": "女"}, {"id": "oC", "label": "其他"}],
            "jumpRules": rules or []}


def multi(qid="q2", required=True, lo=None, hi=None, rules=None):
    validation = {k: v for k, v in {"minSelect": lo, "maxSelect": hi}.items() if v is not None}
    return {"id": qid, "type": "multiple_choice", "title": "你喜欢的水果", "required": required,
            "options": [{"id": "f1", "label": "苹果"}, {"id": "f2", "label": "香蕉"},
                        {"id": "f3", "label": "西瓜"}, {"id": "f4", "label": "葡萄"}],
            "validation": validation, "jumpRules": rules or []}


def text(qid="q4", required=False, lo=None, hi=None):
    validation = {k: v for k, v in {"minLength": lo, "maxLength": hi}.items() if v is not None}
    return {"id": qid, "type": "text_input", "title": "你的建议", "required": required,
            "validation": validation, "jumpRules": []}


def number(qid="q3", required=True, lo=None, hi=None, integer=False, rules=None):
    validation = {k: v for k, v in {"min": lo, "max": hi}.items() if v is not None}
    validation["integer"] = integer
    return {"id": qid, "type": "number_input", "title": "年龄", "required": required,
            "validation": validation, "jumpRules": rules or []}


def rule(op, value, goto):
    return {"when": {"op": op, "value": value}, "goto": goto}


# ====================== 校验引擎 ======================

@pytest.mark.parametrize("value", [None, "", "   ", []])
def test_empty_answer_required_vs_optional(value):
    assert validate_answer(single(required=True), value) == (None, REQUIRED_MESSAGE)
    assert validate_answer(single(required=False), value) == (EMPTY, None)


def test_single_choice():
    q = single()
    assert validate_answer(q, "oB") == ("oB", None)
    for bad in ["oX", ["oA"], 1]:
        assert validate_answer(q, bad)[1] == "请选择本题给出的选项"


@pytest.mark.parametrize("lo,hi,value,expect", [
    (2, 3, ["f1", "f2"], None),                 # 恰好最少
    (2, 3, ["f1", "f2", "f3"], None),           # 恰好最多
    (2, 3, ["f1"], "至少选择 2 项"),
    (2, 3, ["f1", "f2", "f3", "f4"], "最多选择 3 项"),
    (2, 2, ["f1", "f2"], None),                 # 必须选 2 个
    (2, 2, ["f1"], "请选择 2 项"),
    (2, 2, ["f1", "f2", "f3"], "请选择 2 项"),
    (None, None, ["f4"], None),                 # 不限数量
    (None, None, ["f1", "f1"], "同一个选项不能重复选择"),
    (None, None, ["f1", "oA"], "请选择本题给出的选项"),
    (None, None, "f1", "请选择本题给出的选项"),
])
def test_multiple_choice(lo, hi, value, expect):
    assert validate_answer(multi(lo=lo, hi=hi), value)[1] == expect


def test_multiple_choice_saved_in_option_order():
    assert validate_answer(multi(), ["f3", "f1"]) == (["f1", "f3"], None)


@pytest.mark.parametrize("lo,hi,value,expect,saved", [
    (5, 10, "一二三四五", None, "一二三四五"),        # 恰好最少，一个汉字算 1
    (5, 10, "  一二三四五  ", None, "一二三四五"),    # 去掉首尾空格
    (5, 10, "一二三四", "至少输入 5 个字", None),
    (5, 10, "一二三四五六七八九十", None, "一二三四五六七八九十"),
    (5, 10, "一二三四五六七八九十十", "最多输入 10 个字", None),
    (None, None, 123, "请输入文字", None),
])
def test_text_input(lo, hi, value, expect, saved):
    result, error = validate_answer(text(lo=lo, hi=hi), value)
    assert error == expect
    if expect is None:
        assert result == saved


@pytest.mark.parametrize("q,value,expect,saved", [
    (number(lo=0, hi=120, integer=True), 0, None, 0),
    (number(lo=0, hi=120, integer=True), 120, None, 120),
    (number(lo=0, hi=120, integer=True), 18.0, None, 18),             # 18.0 视为整数 18
    (number(lo=0, hi=120, integer=True), 18.5, "请输入整数", None),
    (number(lo=0, hi=120, integer=True), 121, "请输入 0–120 之间的整数", None),
    (number(lo=0, hi=120, integer=True), -1, "请输入 0–120 之间的整数", None),
    (number(integer=True), 13800138000, None, 13800138000),           # 手机号
    (number(lo=1.5), 1, "请输入不小于 1.5 的数字", None),
    (number(hi=10), 10.5, "请输入不大于 10 的数字", None),
    (number(), 3.14, None, 3.14),
    (number(), "18", "请输入数字", None),                               # 字符串不接受
    (number(), True, "请输入数字", None),
    (number(), float("nan"), "请输入数字", None),
])
def test_number_input(q, value, expect, saved):
    result, error = validate_answer(q, value)
    assert error == expect
    if expect is None:
        assert result == saved and type(result) is type(saved)


def test_optional_but_answered_must_be_valid():
    assert validate_answer(text(required=False, lo=5), "短")[1] == "至少输入 5 个字"


# ====================== 跳转条件 ======================

@pytest.mark.parametrize("op,value,x,hit", [
    ("eq", "oA", "oA", True), ("eq", "oB", "oA", False),
    ("in", "oB", ["oA", "oB"], True), ("in", "oC", ["oA", "oB"], False),
    ("containsAny", ["f1", "f3"], ["f3", "f4"], True), ("containsAny", ["f1"], ["f3", "f4"], False),
    ("containsAll", ["f1", "f2", "f3"], ["f1", "f2"], True), ("containsAll", ["f1"], ["f1", "f2"], False),
    ("equals", ["f2", "f1"], ["f1", "f2"], True), ("equals", ["f1", "f2", "f3"], ["f1", "f2"], False),
    ("eq", 18, 18, True), ("eq", 18, 19, False),
    ("ne", 18, 19, True), ("ne", 18, 18, False),
    ("gt", 19, 18, True), ("gt", 18, 18, False),
    ("gte", 18, 18, True), ("gte", 17, 18, False),
    ("lt", 17, 18, True), ("lt", 18, 18, False),
    ("lte", 18, 18, True), ("lte", 19, 18, False),
    ("between", 18, [18, 30], True), ("between", 30, [18, 30], True), ("between", 31, [18, 30], False),
])
def test_each_op(op, value, x, hit):
    assert OPS[op](value, x) is hit


def test_first_matching_rule_wins():
    q = number(rules=[rule("lt", 18, "q5"), rule("lt", 60, "q4"), rule("gte", 0, "END")])
    assert jump_target(q, 10) == "q5"   # 前两条都命中，取第一条
    assert jump_target(q, 30) == "q4"
    assert jump_target(q, 70) == "END"


def test_no_rule_matched_or_unanswered_goes_next():
    q = single(rules=[rule("eq", "oA", "q5")])
    assert jump_target(q, "oB") is None
    assert jump_target(q, EMPTY) is None


# ====================== 路径计算 ======================

def doc_example():
    """作业原文：第 1 题选 A → 跳到第 5 题；选 B → 跳到第 3 题。"""
    return [
        single("q1", rules=[rule("eq", "oA", "q5"), rule("eq", "oB", "q3")]),
        text("q2"), text("q3"), text("q4"), text("q5"),
    ]


def test_doc_example_paths():
    qs = doc_example()
    a = evaluate_submission(qs, {"q1": "oA", "q5": "好的好的"})
    assert a.path == ["q1", "q5"] and a.complete
    b = evaluate_submission(qs, {"q1": "oB", "q3": "第三题", "q4": "第四题", "q5": "第五题"})
    assert b.path == ["q1", "q3", "q4", "q5"] and b.complete
    c = evaluate_submission(qs, {"q1": "oC"})  # 都不命中 → 顺序往下
    assert c.path == ["q1", "q2", "q3", "q4", "q5"] and c.complete


def test_navigate_step_by_step():
    qs = doc_example()
    assert navigate(qs, {}).next_question == "q1"
    step = navigate(qs, {"q1": "oA"})
    assert step.path == ["q1", "q5"] and step.next_question == "q5"
    assert navigate(qs, {"q1": "oA", "q5": None}).complete       # 非必答题传 null：看过、不答
    assert not navigate(qs, {"q1": "oA"}).complete


def test_jump_to_end():
    qs = [number("q1", rules=[rule("lt", 18, "END")]), text("q2", required=True)]
    ev = evaluate_submission(qs, {"q1": 16})
    assert ev.path == ["q1"] and ev.complete   # 第 2 题虽然必答，但被跳过，不报错


def test_skipped_required_question_not_required():
    qs = [single("q1", rules=[rule("eq", "oA", "q3")]), number("q2", required=True), text("q3")]
    assert evaluate_submission(qs, {"q1": "oA"}).complete


def test_required_on_path_missing_is_error():
    qs = [single("q1"), number("q2", required=True)]
    ev = evaluate_submission(qs, {"q1": "oA"})
    assert ev.errors == {"q2": REQUIRED_MESSAGE} and not ev.complete


def test_navigate_null_on_required_is_error():
    ev = navigate([single("q1")], {"q1": None})
    assert ev.errors == {"q1": REQUIRED_MESSAGE} and ev.next_question == "q1"


def test_invalid_answer_stops_path():
    qs = [number("q1", lo=0, hi=120, rules=[rule("gt", 60, "q3")]), text("q2"), text("q3")]
    ev = navigate(qs, {"q1": 200, "q2": "x"})
    assert ev.path == ["q1"] and ev.next_question == "q1"
    assert ev.errors == {"q1": "请输入 0–120 之间的数字"}


def test_changed_earlier_answer_makes_later_answer_off_path():
    qs = doc_example()
    # 先选 A 答了第 5 题，又改成 B（应跳到第 3 题），并答完 3、4 题，第 2 题被跳过却也有答案
    answers = {"q1": "oB", "q2": "被跳过的题", "q3": "三", "q4": "四", "q5": "五"}
    nav = navigate(qs, answers)
    assert nav.off_path == ["q2"] and not nav.errors  # navigate 只提示，不算错误
    assert not nav.complete
    sub = evaluate_submission(qs, answers)
    assert set(sub.errors) == {"q2"} and "应被跳过" in sub.errors["q2"]
    assert not sub.complete


def test_navigate_does_not_judge_questions_not_yet_reached():
    qs = doc_example()
    # 改成 B 后路径停在第 3 题，第 5 题以后仍可能走到，暂不判为路径外
    nav = navigate(qs, {"q1": "oB", "q5": "五"})
    assert nav.next_question == "q3" and nav.off_path == []


def test_unknown_question_rejected_on_submit():
    ev = evaluate_submission([text("q1")], {"q1": "好", "q_nope": "x"})
    assert ev.errors == {"q_nope": "题目 q_nope 不存在"} and not ev.complete
    assert navigate([text("q1")], {"q_nope": "x"}).errors == {}  # navigate 忽略


def test_saved_answers_only_on_path_and_normalized():
    qs = [multi("q1", rules=[rule("containsAll", ["f1"], "q3")]), text("q2"), number("q3", integer=True), text("q4")]
    ev = evaluate_submission(qs, {"q1": ["f2", "f1"], "q3": 20.0, "q4": "  "})
    assert ev.complete
    assert ev.answers == [
        {"questionId": "q1", "value": ["f1", "f2"]},   # 按选项顺序
        {"questionId": "q3", "value": 20},             # 20.0 → 20
    ]                                                   # q4 只有空格：视为不答，不保存
