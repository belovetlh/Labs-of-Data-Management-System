"""题目管理接口测试：添加、修改、删除、调序、跳转规则，以及发布前的结构检查。"""

import re
import threading

import pytest
from bson import ObjectId

from app.services import questions as qsvc
from app.services import surveys as ssvc

API = "/api/surveys"

SINGLE = {"type": "single_choice", "title": "你的性别", "required": True,
          "options": [{"label": "男"}, {"label": "女"}, {"label": "其他"}]}
MULTI = {"type": "multiple_choice", "title": "你喜欢的水果", "required": True,
         "options": [{"label": "苹果"}, {"label": "香蕉"}, {"label": "西瓜"}, {"label": "葡萄"}],
         "validation": {"minSelect": 2, "maxSelect": 3}}
TEXT = {"type": "text_input", "title": "你的建议", "validation": {"minLength": 5, "maxLength": 200}}
NUMBER = {"type": "number_input", "title": "年龄", "required": True,
          "validation": {"min": 0, "max": 120, "integer": True}}


@pytest.fixture()
def headers(login_as):
    return login_as()


@pytest.fixture()
def sid(client, headers):
    return client.post(API, json={"title": "测试问卷"}, headers=headers).json()["id"]


def add(client, headers, sid, question, position=None):
    params = {} if position is None else {"position": position}
    return client.post(f"{API}/{sid}/questions", json=question, params=params, headers=headers)


def questions_of(client, headers, sid):
    return client.get(f"{API}/{sid}", headers=headers).json()["questions"]


def build(client, headers, sid, *qs):
    """依次添加题目，返回题目列表。"""
    for q in qs:
        assert add(client, headers, sid, q).status_code == 201
    return questions_of(client, headers, sid)


def set_rules(client, headers, sid, qid, rules):
    return client.put(f"{API}/{sid}/questions/{qid}/jump-rules", json={"rules": rules}, headers=headers)


# ---------- 添加 ----------

def test_add_all_four_types(client, headers, sid):
    qs = build(client, headers, sid, SINGLE, MULTI, TEXT, NUMBER)
    assert [q["type"] for q in qs] == ["single_choice", "multiple_choice", "text_input", "number_input"]
    for q in qs:
        assert re.fullmatch(r"q_[0-9a-f]{8}", q["id"])
        assert q["jumpRules"] == []
    assert all(re.fullmatch(r"o_[0-9a-f]{8}", o["id"]) for o in qs[0]["options"])
    assert [o["label"] for o in qs[0]["options"]] == ["男", "女", "其他"]
    assert qs[1]["validation"] == {"minSelect": 2, "maxSelect": 3}
    assert qs[2]["required"] is False  # 默认非必答
    assert qs[3]["validation"] == {"min": 0, "max": 120, "integer": True}
    assert "options" not in qs[2] and "options" not in qs[3]


def test_add_at_position(client, headers, sid):
    build(client, headers, sid, SINGLE, TEXT)
    add(client, headers, sid, NUMBER, position=1)
    add(client, headers, sid, MULTI, position=0)
    assert [q["title"] for q in questions_of(client, headers, sid)] == ["你喜欢的水果", "你的性别", "年龄", "你的建议"]


def test_exact_select_count(client, headers, sid):
    q = {**MULTI, "validation": {"minSelect": 2, "maxSelect": 2}}  # 必须选 2 个
    assert add(client, headers, sid, q).status_code == 201


@pytest.mark.parametrize("question,expect", [
    ({**SINGLE, "options": [{"label": "只有一个"}]}, None),                                   # 选项太少
    ({**SINGLE, "options": [{"label": "男"}, {"label": "男"}]}, "选项文字不能重复"),
    ({**SINGLE, "options": [{"label": "男"}, {"label": "   "}]}, "选项文字须为"),
    ({**MULTI, "validation": {"minSelect": 3, "maxSelect": 2}}, "最少选择数不能大于最多选择数"),
    ({**MULTI, "validation": {"maxSelect": 5}}, "最多选择数不能超过选项数"),
    ({**MULTI, "validation": {"minSelect": 5}}, "最少选择数不能超过选项数"),
    ({**TEXT, "validation": {"minLength": 10, "maxLength": 5}}, "最少字数不能大于最多字数"),
    ({**NUMBER, "validation": {"min": 10, "max": 1}}, "最小值不能大于最大值"),
    ({**TEXT, "options": [{"label": "a"}, {"label": "b"}]}, None),                           # 文本题带选项
    ({**SINGLE, "validation": {"minSelect": 1}}, None),                                      # 单选带校验规则
    ({**SINGLE, "title": ""}, "题目须为"),
    ({"type": "rating", "title": "打分"}, None),                                              # 未知题型
])
def test_add_invalid_definition_422(client, headers, sid, question, expect):
    resp = add(client, headers, sid, question)
    assert resp.status_code == 422
    if expect:
        assert expect in resp.text
    assert questions_of(client, headers, sid) == []


def test_add_to_others_survey_404(client, login_as, sid):
    other = login_as("other_01")
    assert add(client, other, sid, SINGLE).status_code == 404


# ---------- 修改 ----------

def test_update_keeps_option_ids(client, headers, sid):
    q = build(client, headers, sid, SINGLE)[0]
    male, female, _ = q["options"]
    body = {**SINGLE, "title": "性别", "options": [
        {"id": male["id"], "label": "男性"},
        {"id": female["id"], "label": "女性"},
        {"label": "不愿透露"},
    ]}
    resp = client.put(f"{API}/{sid}/questions/{q['id']}", json=body, headers=headers)
    assert resp.status_code == 200
    new = resp.json()["questions"][0]
    assert new["id"] == q["id"] and new["title"] == "性别"
    assert new["options"][0] == {"id": male["id"], "label": "男性"}
    assert new["options"][1] == {"id": female["id"], "label": "女性"}
    assert new["options"][2]["id"] not in {o["id"] for o in q["options"]}


def test_update_with_foreign_option_id_422(client, headers, sid):
    q1, q2 = build(client, headers, sid, SINGLE, MULTI)
    body = {**SINGLE, "options": [{"id": q2["options"][0]["id"], "label": "偷来的"}, {"label": "b"}]}
    resp = client.put(f"{API}/{sid}/questions/{q1['id']}", json=body, headers=headers)
    assert resp.status_code == 422 and "不属于这道题" in resp.text


def test_update_change_type(client, headers, sid):
    q = build(client, headers, sid, SINGLE)[0]
    resp = client.put(f"{API}/{sid}/questions/{q['id']}", json=TEXT, headers=headers)
    assert resp.status_code == 200
    new = resp.json()["questions"][0]
    assert new["type"] == "text_input" and "options" not in new


def test_update_change_type_with_rules_409(client, headers, sid):
    q1, q2 = build(client, headers, sid, SINGLE, TEXT)
    set_rules(client, headers, sid, q1["id"],
              [{"when": {"op": "eq", "value": q1["options"][0]["id"]}, "goto": "END"}])
    resp = client.put(f"{API}/{sid}/questions/{q1['id']}", json=TEXT, headers=headers)
    assert resp.status_code == 409 and "请先清空它的跳转规则" in resp.text


def test_update_removing_option_used_by_rule_409(client, headers, sid):
    q1, _ = build(client, headers, sid, SINGLE, TEXT)
    other = q1["options"][2]
    set_rules(client, headers, sid, q1["id"], [{"when": {"op": "eq", "value": other["id"]}, "goto": "END"}])
    body = {**SINGLE, "options": [{"id": o["id"], "label": o["label"]} for o in q1["options"][:2]]}
    resp = client.put(f"{API}/{sid}/questions/{q1['id']}", json=body, headers=headers)
    assert resp.status_code == 409 and "使用了本题不存在的选项" in resp.text


def test_update_keeps_jump_rules(client, headers, sid):
    q1, q2 = build(client, headers, sid, SINGLE, TEXT)
    rules = [{"when": {"op": "eq", "value": q1["options"][0]["id"]}, "goto": q2["id"]}]
    set_rules(client, headers, sid, q1["id"], rules)
    body = {**SINGLE, "title": "改个标题", "options": [{"id": o["id"], "label": o["label"]} for o in q1["options"]]}
    new = client.put(f"{API}/{sid}/questions/{q1['id']}", json=body, headers=headers).json()["questions"][0]
    assert new["jumpRules"] == rules


def test_update_unknown_question_404(client, headers, sid):
    resp = client.put(f"{API}/{sid}/questions/q_00000000", json=SINGLE, headers=headers)
    assert resp.status_code == 404 and resp.json()["detail"] == "题目不存在"


# ---------- 删除与调序 ----------

def test_delete_question(client, headers, sid):
    q1, q2 = build(client, headers, sid, SINGLE, TEXT)
    resp = client.delete(f"{API}/{sid}/questions/{q1['id']}", headers=headers)
    assert resp.status_code == 200
    assert [q["id"] for q in resp.json()["questions"]] == [q2["id"]]


def test_delete_referenced_question_409(client, headers, sid):
    q1, q2, q3 = build(client, headers, sid, SINGLE, NUMBER, TEXT)
    set_rules(client, headers, sid, q1["id"], [{"when": {"op": "eq", "value": q1["options"][0]["id"]}, "goto": q3["id"]}])
    resp = client.delete(f"{API}/{sid}/questions/{q3['id']}", headers=headers)
    assert resp.status_code == 409
    assert "第 1 题「你的性别」的跳转规则会跳到第 3 题「你的建议」" in resp.json()["detail"]
    assert len(questions_of(client, headers, sid)) == 3


def test_delete_question_with_own_rules_ok(client, headers, sid):
    q1, q2 = build(client, headers, sid, SINGLE, TEXT)
    set_rules(client, headers, sid, q1["id"], [{"when": {"op": "eq", "value": q1["options"][0]["id"]}, "goto": q2["id"]}])
    assert client.delete(f"{API}/{sid}/questions/{q1['id']}", headers=headers).status_code == 200


def test_reorder(client, headers, sid):
    q1, q2, q3 = build(client, headers, sid, SINGLE, MULTI, TEXT)
    order = [q3["id"], q1["id"], q2["id"]]
    resp = client.put(f"{API}/{sid}/question-order", json={"questionIds": order}, headers=headers)
    assert resp.status_code == 200
    assert [q["id"] for q in resp.json()["questions"]] == order


@pytest.mark.parametrize("make_order", [
    lambda a, b, c: [a, b],          # 少了一道
    lambda a, b, c: [a, b, c, c],    # 重复
    lambda a, b, c: [a, b, "q_00000000"],
])
def test_reorder_invalid_list_422(client, headers, sid, make_order):
    q1, q2, q3 = build(client, headers, sid, SINGLE, MULTI, TEXT)
    order = make_order(q1["id"], q2["id"], q3["id"])
    resp = client.put(f"{API}/{sid}/question-order", json={"questionIds": order}, headers=headers)
    assert resp.status_code == 422


def test_reorder_making_backward_jump_409(client, headers, sid):
    q1, q2, q3 = build(client, headers, sid, SINGLE, MULTI, TEXT)
    set_rules(client, headers, sid, q1["id"], [{"when": {"op": "eq", "value": q1["options"][0]["id"]}, "goto": q3["id"]}])
    # 规则按 id 引用：只要目标仍在后面，调序不影响规则
    ok = client.put(f"{API}/{sid}/question-order", json={"questionIds": [q2["id"], q1["id"], q3["id"]]}, headers=headers)
    assert ok.status_code == 200
    bad = client.put(f"{API}/{sid}/question-order", json={"questionIds": [q3["id"], q2["id"], q1["id"]]}, headers=headers)
    assert bad.status_code == 409 and "只能跳到后面的题" in bad.text


# ---------- 跳转规则 ----------

def test_set_valid_rules_all_types(client, headers, sid):
    q1, q2, q3, q4 = build(client, headers, sid, SINGLE, MULTI, NUMBER, TEXT)
    o1, o2 = q1["options"], q2["options"]
    cases = [
        (q1, [{"when": {"op": "eq", "value": o1[0]["id"]}, "goto": q3["id"]},
              {"when": {"op": "in", "value": [o1[1]["id"], o1[2]["id"]]}, "goto": q2["id"]}]),
        (q2, [{"when": {"op": "containsAny", "value": [o2[0]["id"]]}, "goto": q4["id"]},
              {"when": {"op": "containsAll", "value": [o2[0]["id"], o2[1]["id"]]}, "goto": "END"},
              {"when": {"op": "equals", "value": [o2[2]["id"], o2[3]["id"]]}, "goto": q3["id"]}]),
        (q3, [{"when": {"op": op, "value": 18}, "goto": "END"} for op in ["eq", "ne", "gt", "gte", "lt", "lte"]]
             + [{"when": {"op": "between", "value": [0, 17.5]}, "goto": q4["id"]}]),
    ]
    for q, rules in cases:
        resp = set_rules(client, headers, sid, q["id"], rules)
        assert resp.status_code == 200, resp.text
    saved = {q["id"]: q["jumpRules"] for q in questions_of(client, headers, sid)}
    for q, rules in cases:
        assert saved[q["id"]] == rules


def test_clear_rules(client, headers, sid):
    q1, q2 = build(client, headers, sid, SINGLE, TEXT)
    set_rules(client, headers, sid, q1["id"], [{"when": {"op": "eq", "value": q1["options"][0]["id"]}, "goto": "END"}])
    assert set_rules(client, headers, sid, q1["id"], []).json()["questions"][0]["jumpRules"] == []


def _invalid_rule_cases():
    # (题目序号, 规则生成函数, 期望的错误片段)；qs 为 [单选, 多选, 数字, 文本]
    return [
        (0, lambda qs: {"when": {"op": "eq", "value": qs[0]["options"][0]["id"]}, "goto": qs[0]["id"]}, "只能跳到后面的题"),
        (2, lambda qs: {"when": {"op": "gt", "value": 1}, "goto": qs[0]["id"]}, "只能跳到后面的题"),
        (0, lambda qs: {"when": {"op": "eq", "value": qs[0]["options"][0]["id"]}, "goto": "q_00000000"}, "跳转目标题不存在"),
        (0, lambda qs: {"when": {"op": "eq", "value": qs[0]["options"][0]["id"]}, "goto": "第5题"}, None),
        (0, lambda qs: {"when": {"op": "containsAny", "value": [qs[0]["options"][0]["id"]]}, "goto": "END"}, "该题型的跳转条件只能是"),
        (1, lambda qs: {"when": {"op": "eq", "value": qs[1]["options"][0]["id"]}, "goto": "END"}, "该题型的跳转条件只能是"),
        (2, lambda qs: {"when": {"op": "containsAll", "value": [1]}, "goto": "END"}, "该题型的跳转条件只能是"),
        (3, lambda qs: {"when": {"op": "eq", "value": "好"}, "goto": "END"}, "文本填空题不支持跳转"),
        (0, lambda qs: {"when": {"op": "eq", "value": qs[1]["options"][0]["id"]}, "goto": "END"}, "使用了本题不存在的选项"),
        (1, lambda qs: {"when": {"op": "containsAny", "value": []}, "goto": "END"}, "条件的值应为至少一个选项"),
        (1, lambda qs: {"when": {"op": "containsAny", "value": [qs[1]["options"][0]["id"]] * 2}, "goto": "END"}, "条件里的选项重复"),
        (2, lambda qs: {"when": {"op": "gt", "value": "18"}, "goto": "END"}, "条件的值应为数字"),
        (2, lambda qs: {"when": {"op": "gt", "value": True}, "goto": "END"}, "条件的值应为数字"),
        (2, lambda qs: {"when": {"op": "between", "value": [30, 18]}, "goto": "END"}, "区间下限不能大于上限"),
        (2, lambda qs: {"when": {"op": "between", "value": [18]}, "goto": "END"}, "区间条件的值应为"),
    ]


@pytest.mark.parametrize("index,make_rule,expect", _invalid_rule_cases())
def test_invalid_rules_422(client, headers, sid, index, make_rule, expect):
    qs = build(client, headers, sid, SINGLE, MULTI, NUMBER, TEXT)
    resp = set_rules(client, headers, sid, qs[index]["id"], [make_rule(qs)])
    assert resp.status_code == 422
    if expect:
        assert expect in resp.text
    assert all(q["jumpRules"] == [] for q in questions_of(client, headers, sid))


def test_rule_error_message_names_question(client, headers, sid):
    qs = build(client, headers, sid, SINGLE, MULTI, NUMBER)
    resp = set_rules(client, headers, sid, qs[2]["id"], [
        {"when": {"op": "gt", "value": 1}, "goto": "END"},
        {"when": {"op": "between", "value": [9, 1]}, "goto": "END"},
    ])
    assert resp.json()["detail"] == "第 3 题「年龄」的第 2 条跳转规则：区间下限不能大于上限"


# ---------- 草稿限定、发布检查、并发 ----------

def test_all_question_operations_locked_after_publish(client, headers, sid):
    q1, q2 = build(client, headers, sid, SINGLE, TEXT)
    assert client.post(f"{API}/{sid}/publish", headers=headers).status_code == 200
    calls = [
        add(client, headers, sid, NUMBER),
        client.put(f"{API}/{sid}/questions/{q1['id']}", json=SINGLE, headers=headers),
        client.delete(f"{API}/{sid}/questions/{q2['id']}", headers=headers),
        client.put(f"{API}/{sid}/question-order", json={"questionIds": [q2["id"], q1["id"]]}, headers=headers),
        set_rules(client, headers, sid, q1["id"], []),
    ]
    for resp in calls:
        assert resp.status_code == 409
        assert "问卷已发布，题目不能再修改" in resp.json()["detail"]


def test_publish_rejects_invalid_structure(client, db, headers, sid):
    q1, q2 = build(client, headers, sid, SINGLE, TEXT)
    # 绕过接口直接写入一条往前跳的规则，模拟数据被意外改坏
    bad_rule = {"when": {"op": "eq", "value": q1["options"][0]["id"]}, "goto": q1["id"]}
    db.surveys.update_one({"_id": ObjectId(sid)}, {"$set": {"questions.0.jumpRules": [bad_rule]}})
    resp = client.post(f"{API}/{sid}/publish", headers=headers)
    assert resp.status_code == 409
    assert resp.json()["detail"].startswith("问卷存在问题，不能发布：")


def test_each_change_increments_revision(client, db, headers, sid):
    def revision():
        return db.surveys.find_one({"_id": ObjectId(sid)})["revision"]

    assert revision() == 0
    q1 = build(client, headers, sid, SINGLE)[0]
    assert revision() == 1
    set_rules(client, headers, sid, q1["id"], [])
    assert revision() == 2
    client.patch(f"{API}/{sid}", json={"title": "新标题"}, headers=headers)
    assert revision() == 3


def test_stale_revision_write_rejected(client, db, headers, sid):
    """两个修改都基于同一版问卷：先写回的成功，后写回的因 revision 已变而失败。"""
    build(client, headers, sid, SINGLE)
    stale = db.surveys.find_one({"_id": ObjectId(sid)})          # 两个请求都读到这一版
    qsvc._save(db, stale, stale["questions"])                    # 第一个写回：成功
    with pytest.raises(ssvc.SurveyConflict, match="刚被修改过"):
        qsvc._save(db, stale, stale["questions"])                # 第二个写回：revision 已变


def test_concurrent_adds_never_lose_a_question(client, db, headers, sid):
    """多个线程同时加题：每次成功的添加都保留下来，失败的明确收到"刚被修改过"。"""
    owner_id = db.users.find_one({"username": "owner_01"})["_id"]
    data = {"type": "text_input", "title": "并发题", "required": False, "validation": {}}
    barrier = threading.Barrier(6)
    ok, conflicts = [], []

    def worker():
        barrier.wait()
        try:
            ok.append(qsvc.add_question(db, owner_id, sid, data))
        except ssvc.SurveyConflict:
            conflicts.append(1)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    saved = [q["id"] for q in questions_of(client, headers, sid)]
    assert len(ok) >= 1
    assert len(ok) + len(conflicts) == 6
    assert sorted(saved) == sorted(ok)  # 成功的一个不少，失败的一个不多
