"""被哪些问卷使用（阶段二第 5 段）。"""

import json

import pytest
from bson import ObjectId

from app.services import survey_library as slib

SAPI = "/api/surveys"
QAPI = "/api/questions"

AGE = {"type": "number_input", "title": "你的年龄", "required": True,
       "validation": {"min": 0, "max": 120, "integer": True}}
NOTE = {"type": "text_input", "title": "备注", "required": False}


@pytest.fixture()
def teacher(login_as):
    return login_as("teacher_01", "王老师")


@pytest.fixture()
def student(login_as):
    return login_as("student_01", "小明")


@pytest.fixture()
def ta(login_as):
    return login_as("ta_01", "小李")


@pytest.fixture()
def age_q(client, teacher):
    """王老师的"你的年龄"，有 v1、v2。"""
    q = client.post(QAPI, headers=teacher, json={"content": AGE}).json()
    client.post(f"{QAPI}/{q['id']}/versions", headers=teacher,
                json={"baseVersion": 1, "content": {**AGE, "title": "你的周岁年龄"}})
    return q["id"]


def survey_using(client, headers, qid, title, version, extra_first=False):
    sid = client.post(SAPI, headers=headers, json={"title": title}).json()["id"]
    if extra_first:
        client.post(f"{SAPI}/{sid}/questions", headers=headers, json=NOTE)
    r = client.post(f"{SAPI}/{sid}/questions/from-library", headers=headers,
                    json={"questionId": qid, "version": version})
    assert r.status_code == 201, r.text
    return sid, r.json()["questions"][-1]


def usage(client, headers, qid):
    r = client.get(f"{QAPI}/{qid}/usage", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def share(client, headers, qid, username):
    client.post(f"{QAPI}/{qid}/shares", headers=headers, json={"username": username})


def test_unused_question(client, teacher, age_q):
    u = usage(client, teacher, age_q)
    assert (u["totalSurveys"], u["othersTotal"], u["versions"], u["mySurveys"]) == (0, 0, [], [])


def test_owner_sees_own_details_and_others_counts(client, teacher, student, ta, age_q):
    s1, q1 = survey_using(client, teacher, age_q, "期中调查", 1, extra_first=True)
    client.post(f"{SAPI}/{s1}/publish", headers=teacher)
    slug = client.get(f"{SAPI}/{s1}", headers=teacher).json()["slug"]
    client.post(f"/api/fill/{slug}/responses", headers=student, json={"answers": {q1["id"]: 20}})
    survey_using(client, teacher, age_q, "期末调查", 2)
    for name, headers, version in [("student_01", student, 1), ("ta_01", ta, 2)]:
        share(client, teacher, age_q, name)
        survey_using(client, headers, age_q, f"{name} 的问卷", version)

    u = usage(client, teacher, age_q)
    assert u["isOwner"] is True and u["totalSurveys"] == 4 and u["othersTotal"] == 2
    assert [(v["version"], v["mine"], v["others"]) for v in u["versions"]] == [(1, 1, 1), (2, 1, 1)]
    final, midterm = u["mySurveys"]                                      # 按创建时间倒序
    assert (final["title"], final["phase"], final["version"], final["isLatest"]) == ("期末调查", "draft", 2, True)
    assert (midterm["title"], midterm["phase"], midterm["version"]) == ("期中调查", "open", 1)
    assert (midterm["questionNumber"], midterm["responseCount"], midterm["isLatest"]) == (2, 1, False)
    text = json.dumps(u, ensure_ascii=False)
    assert "student_01 的问卷" not in text and "ta_01 的问卷" not in text   # 别人的问卷不暴露标题


def test_shared_user_sees_only_own_surveys(client, teacher, student, age_q):
    survey_using(client, teacher, age_q, "王老师的问卷", 1)
    share(client, teacher, age_q, "student_01")
    survey_using(client, student, age_q, "小明的问卷", 2)

    u = usage(client, student, age_q)
    assert u["isOwner"] is False and u["othersTotal"] is None
    assert u["totalSurveys"] == 1                                        # 不告诉他别人用了多少
    assert [(v["version"], v["mine"], v["others"]) for v in u["versions"]] == [(2, 1, 0)]
    assert [s["title"] for s in u["mySurveys"]] == ["小明的问卷"]


def test_invisible_question_404(client, teacher, student, age_q):
    assert client.get(f"{QAPI}/{age_q}/usage", headers=student).status_code == 404


def test_detached_and_deleted_surveys_not_counted(client, teacher, age_q):
    s1, q1 = survey_using(client, teacher, age_q, "会脱钩的问卷", 1)
    s2, _ = survey_using(client, teacher, age_q, "会删除的问卷", 1)
    survey_using(client, teacher, age_q, "保留的问卷", 1)
    assert usage(client, teacher, age_q)["totalSurveys"] == 3

    changed = {**AGE, "validation": {"min": 0, "max": 99, "integer": True}}
    client.put(f"{SAPI}/{s1}/questions/{q1['id']}?libraryAction=detach", headers=teacher, json=changed)
    client.delete(f"{SAPI}/{s2}", headers=teacher)
    u = usage(client, teacher, age_q)
    assert [s["title"] for s in u["mySurveys"]] == ["保留的问卷"]


def test_usage_follows_version_switch(client, teacher, age_q):
    sid, q = survey_using(client, teacher, age_q, "草稿", 1)
    assert usage(client, teacher, age_q)["mySurveys"][0]["isLatest"] is False
    client.put(f"{SAPI}/{sid}/questions/{q['id']}/source", headers=teacher, json={"version": 2})
    u = usage(client, teacher, age_q)
    assert [(v["version"], v["mine"]) for v in u["versions"]] == [(2, 1)]
    assert u["mySurveys"][0]["isLatest"] is True


def test_saved_stage1_question_shows_up(client, db, teacher):
    """已发布的旧问卷把题存入题库后，就出现在这道题的使用情况里。"""
    sid = client.post(SAPI, headers=teacher, json={"title": "去年的问卷"}).json()["id"]
    client.post(f"{SAPI}/{sid}/questions", headers=teacher, json=AGE)
    client.post(f"{SAPI}/{sid}/publish", headers=teacher)
    q = client.get(f"{SAPI}/{sid}", headers=teacher).json()["questions"][0]
    after = client.post(f"{SAPI}/{sid}/questions/{q['id']}/save-to-library", headers=teacher).json()
    qid = after["questions"][0]["source"]["questionId"]
    [s] = usage(client, teacher, qid)["mySurveys"]
    assert (s["title"], s["phase"], s["version"], s["isLatest"]) == ("去年的问卷", "open", 1, True)


def test_usage_query_uses_index(client, db, teacher, age_q):
    survey_using(client, teacher, age_q, "问卷", 1)
    uid = db.users.find_one({"username": "teacher_01"})["_id"]
    plan = json.dumps(db.command("aggregate", "surveys", pipeline=slib.usage_pipeline(ObjectId(age_q), uid),
                                 explain=True), default=str)
    assert "IXSCAN" in plan and "question_source" in plan
    assert "COLLSCAN" not in plan
