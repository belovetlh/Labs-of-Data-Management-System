"""统计接口测试：先通过填写接口提交答卷，再核对统计结果。"""

import json

import pytest
from bson import ObjectId

from app.services import stats as stats_svc

API = "/api/surveys"
FILL = "/api/fill"


def setup_survey(client, owner, anonymous=False):
    """第 1 题选"其他" → 跳到第 4 题（跳过第 2、3 题）。返回 (sid, slug, 题目列表)。"""
    s = client.post(API, json={"title": "统计测试", "settings": {"allowAnonymous": anonymous}}, headers=owner).json()
    sid = s["id"]
    for d in [
        {"type": "single_choice", "title": "性别", "required": True,
         "options": [{"label": "男"}, {"label": "女"}, {"label": "其他"}]},
        {"type": "multiple_choice", "title": "水果", "required": True,
         "options": [{"label": "苹果"}, {"label": "香蕉"}, {"label": "西瓜"}]},
        {"type": "number_input", "title": "年龄", "validation": {"min": 0, "max": 120, "integer": True}},
        {"type": "text_input", "title": "建议"},
    ]:
        client.post(f"{API}/{sid}/questions", json=d, headers=owner)
    qs = client.get(f"{API}/{sid}", headers=owner).json()["questions"]
    client.put(f"{API}/{sid}/questions/{qs[0]['id']}/jump-rules", headers=owner, json={"rules": [
        {"when": {"op": "eq", "value": qs[0]["options"][2]["id"]}, "goto": qs[3]["id"]}]})
    client.post(f"{API}/{sid}/publish", headers=owner)
    return sid, s["slug"], qs


def o(q, i):
    return q["options"][i]["id"]


def submit_four(client, login_as, slug, qs):
    """4 份答卷（第 4 份与第 1 份是同一人，问卷默认允许多次填写）。"""
    alice = login_as("alice_01", "爱丽丝")
    bob = login_as("bob_01", "鲍勃")
    carol = login_as("carol_01", "卡萝")
    q1, q2, q3, q4 = (q["id"] for q in qs)
    rows = [
        (alice, {q1: o(qs[0], 0), q2: [o(qs[1], 0), o(qs[1], 1)], q3: 20, q4: "很好很好"}),
        (bob, {q1: o(qs[0], 1), q2: [o(qs[1], 0)], q3: 31, q4: None}),
        (carol, {q1: o(qs[0], 2), q4: "一般般吧"}),                       # 选"其他"，跳过第 2、3 题
        (alice, {q1: o(qs[0], 0), q2: [o(qs[1], 1)], q3: None, q4: "还可以"}),  # 年龄看过不答
    ]
    for headers, answers in rows:
        r = client.post(f"{FILL}/{slug}/responses", json={"answers": answers}, headers=headers)
        assert r.status_code == 201, r.text


@pytest.fixture()
def owner(login_as):
    return login_as("owner_01", "作者")


def get_stats(client, owner, sid):
    r = client.get(f"{API}/{sid}/stats", headers=owner)
    assert r.status_code == 200, r.text
    return r.json()


# ---------- 整份统计 ----------

def test_survey_stats(client, owner, login_as):
    sid, slug, qs = setup_survey(client, owner)
    submit_four(client, login_as, slug, qs)
    body = get_stats(client, owner, sid)
    assert body["totalResponses"] == 4
    single, multi, number, text = body["questions"]

    assert single["answeredCount"] == 4
    assert [(x["label"], x["count"], x["percent"]) for x in single["options"]] == [
        ("男", 2, 50.0), ("女", 1, 25.0), ("其他", 1, 25.0)]

    # 有一份答卷按跳转跳过了第 2、3 题：回答人数少于总提交数
    assert multi["answeredCount"] == 3
    assert [(x["label"], x["count"], x["percent"]) for x in multi["options"]] == [
        ("苹果", 2, 66.67), ("香蕉", 2, 66.67), ("西瓜", 0, 0.0)]  # 没人选的也显示 0

    # 年龄：跳过 1 份、看过不答 1 份，只有 2 份计入
    assert number["answeredCount"] == 2
    assert (number["average"], number["min"], number["max"]) == (25.5, 20, 31)

    assert text["answeredCount"] == 3 and text["entriesTotal"] == 3
    assert [(e["value"], e["respondent"]) for e in text["entries"]] == [
        ("还可以", "爱丽丝（alice_01）"), ("一般般吧", "卡萝（carol_01）"), ("很好很好", "爱丽丝（alice_01）")]


def test_survey_stats_without_responses(client, owner):
    sid, _, _ = setup_survey(client, owner)
    body = get_stats(client, owner, sid)
    assert body["totalResponses"] == 0
    single, multi, number, text = body["questions"]
    assert all(x["count"] == 0 and x["percent"] == 0.0 for x in single["options"] + multi["options"])
    assert number["answeredCount"] == 0 and "average" not in number
    assert text["entries"] == [] and text["entriesTotal"] == 0


def test_draft_stats_all_zero(client, owner):
    sid = client.post(API, json={"title": "草稿"}, headers=owner).json()["id"]
    assert get_stats(client, owner, sid) == {"surveyId": sid, "title": "草稿", "totalResponses": 0, "questions": []}


def test_summary_only_latest_five_entries(client, owner, login_as):
    sid, slug, qs = setup_survey(client, owner)
    user = login_as("many_01")
    for i in range(7):
        client.post(f"{FILL}/{slug}/responses", headers=user,
                    json={"answers": {qs[0]["id"]: o(qs[0], 2), qs[3]["id"]: f"第{i}条"}})
    text = get_stats(client, owner, sid)["questions"][3]
    assert text["entriesTotal"] == 7
    assert [e["value"] for e in text["entries"]] == ["第6条", "第5条", "第4条", "第3条", "第2条"]


def test_anonymous_survey_hides_respondent(client, owner, login_as):
    sid, slug, qs = setup_survey(client, owner, anonymous=True)
    submit_four(client, login_as, slug, qs)
    text = get_stats(client, owner, sid)["questions"][3]
    assert all("respondent" not in e for e in text["entries"])
    page = client.get(f"{API}/{sid}/stats/{qs[3]['id']}", headers=owner).json()
    assert all("respondent" not in e for e in page["entries"])
    assert "爱丽丝" not in json.dumps(page, ensure_ascii=False)


# ---------- 单题统计 ----------

def test_question_stats_choice(client, owner, login_as):
    sid, slug, qs = setup_survey(client, owner)
    submit_four(client, login_as, slug, qs)
    single = client.get(f"{API}/{sid}/stats/{qs[0]['id']}", headers=owner).json()
    assert single["answeredCount"] == 4 and single["options"][0]["count"] == 2


def test_question_stats_text_pagination(client, owner, login_as):
    sid, slug, qs = setup_survey(client, owner)
    submit_four(client, login_as, slug, qs)
    url = f"{API}/{sid}/stats/{qs[3]['id']}"
    p1 = client.get(url, params={"page": 1, "pageSize": 2}, headers=owner).json()
    p2 = client.get(url, params={"page": 2, "pageSize": 2}, headers=owner).json()
    p3 = client.get(url, params={"page": 3, "pageSize": 2}, headers=owner).json()
    assert p1["entriesTotal"] == 3 and p1["page"] == 1 and p1["pageSize"] == 2
    assert [e["value"] for e in p1["entries"]] == ["还可以", "一般般吧"]
    assert [e["value"] for e in p2["entries"]] == ["很好很好"]
    assert p2["entries"][0]["respondent"] == "爱丽丝（alice_01）"
    assert p3["entries"] == []


def test_question_stats_number(client, owner, login_as):
    sid, slug, qs = setup_survey(client, owner)
    submit_four(client, login_as, slug, qs)
    num = client.get(f"{API}/{sid}/stats/{qs[2]['id']}", headers=owner).json()
    assert (num["average"], num["min"], num["max"], num["entriesTotal"]) == (25.5, 20, 31, 2)
    assert [e["value"] for e in num["entries"]] == [31, 20]


@pytest.mark.parametrize("params", [{"page": 0}, {"pageSize": 0}, {"pageSize": 101}])
def test_question_stats_bad_paging_422(client, owner, params):
    sid, _, qs = setup_survey(client, owner)
    assert client.get(f"{API}/{sid}/stats/{qs[3]['id']}", params=params, headers=owner).status_code == 422


def test_question_stats_unknown_question_404(client, owner):
    sid, _, _ = setup_survey(client, owner)
    r = client.get(f"{API}/{sid}/stats/q_00000000", headers=owner)
    assert r.status_code == 404 and r.json()["detail"] == "题目不存在"


# ---------- 权限与状态 ----------

def test_only_owner_can_view(client, owner, login_as):
    sid, _, qs = setup_survey(client, owner)
    other = login_as("other_01")
    assert client.get(f"{API}/{sid}/stats", headers=other).status_code == 404
    assert client.get(f"{API}/{sid}/stats/{qs[0]['id']}", headers=other).status_code == 404
    assert client.get(f"{API}/{sid}/stats").status_code == 401


def test_closed_survey_stats_visible(client, owner, login_as):
    sid, slug, qs = setup_survey(client, owner)
    submit_four(client, login_as, slug, qs)
    client.post(f"{API}/{sid}/close", headers=owner)
    assert get_stats(client, owner, sid)["totalResponses"] == 4


def test_deleted_survey_stats_404(client, owner, login_as):
    sid, slug, qs = setup_survey(client, owner)
    submit_four(client, login_as, slug, qs)
    client.delete(f"{API}/{sid}", headers=owner)
    assert client.get(f"{API}/{sid}/stats", headers=owner).status_code == 404


def test_stats_only_count_this_survey(client, owner, login_as):
    sid1, slug1, qs1 = setup_survey(client, owner)
    sid2, slug2, qs2 = setup_survey(client, owner)
    submit_four(client, login_as, slug1, qs1)
    assert get_stats(client, owner, sid2)["totalResponses"] == 0


# ---------- 索引 ----------

def _explain(db, pipeline):
    return json.dumps(db.command("aggregate", "responses", pipeline=pipeline, explain=True), default=str)


@pytest.mark.parametrize("which", ["summary", "entries"])
def test_stats_queries_use_index(client, db, owner, login_as, which):
    sid, slug, qs = setup_survey(client, owner)
    submit_four(client, login_as, slug, qs)
    survey = db.surveys.find_one({"_id": ObjectId(sid)})
    pipeline = (stats_svc.summary_pipeline(survey) if which == "summary"
                else stats_svc.entries_pipeline(survey, qs[3]["id"], 0, 20))
    plan = _explain(db, pipeline)
    assert "IXSCAN" in plan and "survey_submittedAt" in plan
    assert "COLLSCAN" not in plan
