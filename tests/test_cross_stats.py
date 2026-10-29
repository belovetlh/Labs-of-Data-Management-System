"""跨问卷统计（阶段二第 6 段）：一道题库题在自己所有问卷里的回答情况。"""

import json

import pytest
from bson import ObjectId

from app.services import stats as svc

SAPI = "/api/surveys"
QAPI = "/api/questions"

AGE = {"type": "number_input", "title": "你的年龄", "required": True,
       "validation": {"min": 0, "max": 120, "integer": True}}
GRADE = {"type": "single_choice", "title": "你的年级", "required": True,
         "options": [{"label": "大一"}, {"label": "大二"}, {"label": "大三"}]}
FRUIT = {"type": "multiple_choice", "title": "喜欢的水果", "required": True,
         "options": [{"label": "苹果"}, {"label": "香蕉"}, {"label": "西瓜"}]}
ADVICE = {"type": "text_input", "title": "课程建议", "required": True}


@pytest.fixture()
def teacher(login_as):
    return login_as("teacher_01", "王老师")


@pytest.fixture()
def student(login_as):
    return login_as("student_01", "小明")


def new_lib_question(client, headers, content):
    r = client.post(QAPI, headers=headers, json={"content": content})
    assert r.status_code == 201, r.text
    return r.json()


def new_version(client, headers, qid, content, base=1):
    r = client.post(f"{QAPI}/{qid}/versions", headers=headers, json={"baseVersion": base, "content": content})
    assert r.status_code == 201, r.text
    return r.json()


def survey_with(client, headers, qid, title, version=None, anonymous=False, gate=False):
    """建一份用到题库题的问卷并发布；gate=True 时前面加一道"选「结束」直接结束"的题。

    返回 (slug, 这道题在本问卷里的 id, 第一题)。
    """
    sid = client.post(SAPI, headers=headers, json={"title": title, "settings": {
        "allowAnonymous": anonymous, "allowMultipleSubmissions": True}}).json()["id"]
    if gate:
        client.post(f"{SAPI}/{sid}/questions", headers=headers, json={
            "type": "single_choice", "title": "继续吗", "required": True,
            "options": [{"label": "继续"}, {"label": "结束"}]})
    body = {"questionId": qid} | ({"version": version} if version else {})
    r = client.post(f"{SAPI}/{sid}/questions/from-library", headers=headers, json=body)
    assert r.status_code == 201, r.text
    questions = r.json()["questions"]
    if gate:
        stop = questions[0]["options"][1]["id"]
        client.put(f"{SAPI}/{sid}/questions/{questions[0]['id']}/jump-rules", headers=headers,
                   json={"rules": [{"when": {"op": "eq", "value": stop}, "goto": "END"}]})
    slug = client.post(f"{SAPI}/{sid}/publish", headers=headers).json()["slug"]
    return slug, questions[-1]["id"], questions[0]


def fill(client, headers, slug, answers):
    r = client.post(f"/api/fill/{slug}/responses", headers=headers, json={"answers": answers})
    assert r.status_code == 201, r.text


def cross(client, headers, qid, **params):
    r = client.get(f"{QAPI}/{qid}/stats", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def option_ids(lq):
    return [o["id"] for o in lq["content"]["options"]]


# ---------- 数字题 ----------

def test_number_question_across_surveys(client, teacher, student):
    lq = new_lib_question(client, teacher, AGE)
    new_version(client, teacher, lq["id"], {**AGE, "title": "你的周岁年龄"})
    for title, version, ages in [("期中调查", 1, [17, 18, 20]), ("期末调查", 2, [22, 25]), ("补充调查", 1, [35])]:
        slug, q, _ = survey_with(client, teacher, lq["id"], title, version)
        for age in ages:
            fill(client, student, slug, {q: age})

    s = cross(client, teacher, lq["id"])
    assert (s["type"], s["title"], s["surveyCount"], s["totalResponses"], s["answeredCount"]) == \
        ("number_input", "你的周岁年龄", 3, 6, 6)
    assert (s["average"], s["min"], s["max"]) == (22.83, 17, 35)
    # 自动宽度：跨度 18，取 1、2、5 × 10^k 中使区间不超过 10 个的最小值 → 2
    assert s["bucketSize"] == 2
    assert [(b["from"], b["to"]) for b in s["distribution"]][:2] == [(16, 18), (18, 20)]
    assert s["distribution"][-1] == {"from": 34, "to": 36, "count": 1}
    assert sum(b["count"] for b in s["distribution"]) == 6
    assert [(b["title"], b["version"], b["answeredCount"], b["totalResponses"]) for b in s["bySurvey"]] == \
        [("补充调查", 1, 1, 1), ("期末调查", 2, 2, 2), ("期中调查", 1, 3, 3)]   # 按创建时间倒序

    s = cross(client, teacher, lq["id"], bucketSize=5)
    assert s["bucketSize"] == 5
    assert [(b["from"], b["to"], b["count"]) for b in s["distribution"]] == \
        [(15, 20, 2), (20, 25, 2), (25, 30, 1), (30, 35, 0), (35, 40, 1)]


def test_decimal_values_get_decimal_buckets(client, teacher, student):
    gpa = {"type": "number_input", "title": "绩点", "required": True, "validation": {"min": 0, "max": 5}}
    lq = new_lib_question(client, teacher, gpa)
    slug, q, _ = survey_with(client, teacher, lq["id"], "绩点调查")
    for v in [2.1, 3.0, 3.3, 4.0]:
        fill(client, student, slug, {q: v})
    s = cross(client, teacher, lq["id"])
    assert s["bucketSize"] == 0.2 and s["distribution"][0]["from"] == 2
    assert s["distribution"][-1] == {"from": 4, "to": 4.2, "count": 1}   # 最大值落在最后一个区间
    assert sum(b["count"] for b in s["distribution"]) == 4


def test_too_small_bucket_size_rejected(client, teacher, student):
    lq = new_lib_question(client, teacher, AGE)
    slug, q, _ = survey_with(client, teacher, lq["id"], "问卷")
    for age in [17, 35]:
        fill(client, student, slug, {q: age})
    r = client.get(f"{QAPI}/{lq['id']}/stats", headers=teacher, params={"bucketSize": 0.01})
    assert r.status_code == 422 and "区间" in r.json()["detail"]
    assert client.get(f"{QAPI}/{lq['id']}/stats", headers=teacher,
                      params={"bucketSize": 0}).status_code == 422


def test_bucket_size_too_small_to_split_rejected(client, teacher, student):
    """宽度小到去掉浮点误差后相邻边界相同：返回 422，而不是让 $bucket 报错（500）。"""
    lq = new_lib_question(client, teacher, AGE)
    slug, q, _ = survey_with(client, teacher, lq["id"], "问卷")
    fill(client, student, slug, {q: 17})
    r = client.get(f"{QAPI}/{lq['id']}/stats", headers=teacher, params={"bucketSize": 1e-11})
    assert r.status_code == 422 and "区间宽度太小" in r.json()["detail"]


# ---------- 选择题 ----------

def test_choice_merged_by_option_id_across_versions(client, teacher, student):
    lq = new_lib_question(client, teacher, GRADE)
    first, second, third = option_ids(lq)
    # v2：改"大二"的文字（id 不变），删掉"大三"，新增"研究生"
    v2 = new_version(client, teacher, lq["id"], {**GRADE, "options": [
        {"id": first, "label": "大一"}, {"id": second, "label": "大二（含转专业）"}, {"label": "研究生"}]})
    graduate = v2["content"]["options"][2]["id"]

    slug1, q1, _ = survey_with(client, teacher, lq["id"], "去年", 1)
    for o in [third, third, second]:
        fill(client, student, slug1, {q1: o})
    slug2, q2, _ = survey_with(client, teacher, lq["id"], "今年", 2)
    for o in [graduate, second]:
        fill(client, student, slug2, {q2: o})

    s = cross(client, teacher, lq["id"])
    assert s["answeredCount"] == 5 and "version" not in s
    assert [(o["label"], o["count"], o["percent"], o["versions"], o["otherLabels"]) for o in s["options"]] == [
        ("大一", 0, 0.0, [1, 2], []),
        ("大二（含转专业）", 2, 40.0, [1, 2], ["大二"]),   # 两个版本的"大二"合在一起，并标出改过文字
        ("研究生", 1, 20.0, [2], []),
        ("大三", 2, 40.0, [1], []),                       # 只在旧版本里的选项排在后面
    ]

    s = cross(client, teacher, lq["id"], version=1)
    assert (s["version"], s["surveyCount"], s["answeredCount"]) == (1, 1, 3)
    assert [(o["label"], o["count"], o["otherLabels"]) for o in s["options"]] == \
        [("大一", 0, []), ("大二", 1, []), ("大三", 2, [])]


def test_label_comes_from_versions_actually_used(client, teacher, student):
    """题库里有 v2 改了文字但没有问卷用它：显示的仍是数据实际对应的 v1 文字。"""
    lq = new_lib_question(client, teacher, GRADE)
    ids = option_ids(lq)
    new_version(client, teacher, lq["id"], {**GRADE, "options": [
        {"id": ids[0], "label": "一年级"}, {"id": ids[1], "label": "二年级"}, {"id": ids[2], "label": "三年级"}]})
    slug, q, _ = survey_with(client, teacher, lq["id"], "问卷", 1)
    fill(client, student, slug, {q: ids[0]})
    assert [o["label"] for o in cross(client, teacher, lq["id"])["options"]] == ["大一", "大二", "大三"]


def test_multiple_choice_counts_selections(client, teacher, student):
    lq = new_lib_question(client, teacher, FRUIT)
    apple, banana, melon = option_ids(lq)
    slug1, q1, _ = survey_with(client, teacher, lq["id"], "一班")
    fill(client, student, slug1, {q1: [apple, banana]})
    fill(client, student, slug1, {q1: [apple]})
    slug2, q2, _ = survey_with(client, teacher, lq["id"], "二班")
    fill(client, student, slug2, {q2: [apple, banana, melon]})

    s = cross(client, teacher, lq["id"])
    assert s["answeredCount"] == 3
    assert [(o["label"], o["count"], o["percent"]) for o in s["options"]] == \
        [("苹果", 3, 100.0), ("香蕉", 2, 66.67), ("西瓜", 1, 33.33)]


def test_denominator_is_answered_count_with_jumps(client, teacher, student):
    """有跳转时部分答卷没答到这道题：占比的分母是回答了这道题的人数。"""
    lq = new_lib_question(client, teacher, GRADE)
    ids = option_ids(lq)
    slug, q, gate = survey_with(client, teacher, lq["id"], "带跳转", gate=True)
    go, stop = (o["id"] for o in gate["options"])
    fill(client, student, slug, {gate["id"]: go, q: ids[0]})
    fill(client, student, slug, {gate["id"]: go, q: ids[1]})
    fill(client, student, slug, {gate["id"]: stop})

    s = cross(client, teacher, lq["id"])
    assert (s["totalResponses"], s["answeredCount"]) == (3, 2)
    assert [o["percent"] for o in s["options"]] == [50.0, 50.0, 0.0]
    assert s["bySurvey"][0]["questionNumber"] == 2


# ---------- 填空题 ----------

def test_text_entries_paged_with_each_survey_anonymity(client, teacher, student, login_as):
    ta = login_as("ta_01", "小李")
    lq = new_lib_question(client, teacher, ADVICE)
    named_slug, named_q, _ = survey_with(client, teacher, lq["id"], "实名调查")
    anon_slug, anon_q, _ = survey_with(client, teacher, lq["id"], "匿名调查", anonymous=True)
    fill(client, student, named_slug, {named_q: "多讲例题"})
    fill(client, ta, anon_slug, {anon_q: "作业少一点"})
    fill(client, ta, named_slug, {named_q: "希望有回放"})

    s = cross(client, teacher, lq["id"], pageSize=2)
    assert (s["entriesTotal"], s["page"], s["pageSize"]) == (3, 1, 2)
    newest, second = s["entries"]                                  # 按提交时间倒序
    assert (newest["value"], newest["surveyTitle"], newest["respondent"]) == ("希望有回放", "实名调查", "小李（ta_01）")
    assert (second["value"], second["surveyTitle"]) == ("作业少一点", "匿名调查")
    assert "respondent" not in second                              # 匿名问卷的答卷没有记录填写者
    [last] = cross(client, teacher, lq["id"], pageSize=2, page=2)["entries"]
    assert (last["value"], last["respondent"]) == ("多讲例题", "小明（student_01）")


# ---------- 范围：只统计自己的、仍关联题库的问卷 ----------

def test_only_own_surveys_counted(client, teacher, student):
    lq = new_lib_question(client, teacher, AGE)
    slug, q, _ = survey_with(client, teacher, lq["id"], "王老师的问卷")
    fill(client, student, slug, {q: 20})
    assert client.get(f"{QAPI}/{lq['id']}/stats", headers=student).status_code == 404   # 看不到这道题

    client.post(f"{QAPI}/{lq['id']}/shares", headers=teacher, json={"username": "student_01"})
    slug2, q2, _ = survey_with(client, student, lq["id"], "小明的问卷")
    fill(client, teacher, slug2, {q2: 40})

    mine = cross(client, student, lq["id"])
    assert (mine["surveyCount"], mine["average"], [b["title"] for b in mine["bySurvey"]]) == (1, 40, ["小明的问卷"])
    theirs = cross(client, teacher, lq["id"])
    assert (theirs["surveyCount"], theirs["average"]) == (1, 20)
    assert "小明的问卷" not in json.dumps(theirs, ensure_ascii=False)   # 题目创建者也看不到别人收集的数据


def test_detached_and_deleted_surveys_not_counted(client, teacher, student):
    lq = new_lib_question(client, teacher, AGE)
    slug, q, _ = survey_with(client, teacher, lq["id"], "保留的问卷")
    fill(client, student, slug, {q: 20})

    sid = client.post(SAPI, headers=teacher, json={"title": "脱钩的问卷"}).json()["id"]
    q2 = client.post(f"{SAPI}/{sid}/questions/from-library", headers=teacher,
                     json={"questionId": lq["id"]}).json()["questions"][0]["id"]
    client.put(f"{SAPI}/{sid}/questions/{q2}?libraryAction=detach", headers=teacher,
               json={**AGE, "validation": {"min": 0, "max": 99, "integer": True}})
    slug2 = client.post(f"{SAPI}/{sid}/publish", headers=teacher).json()["slug"]
    fill(client, student, slug2, {q2: 99})

    slug3, q3, _ = survey_with(client, teacher, lq["id"], "删除的问卷")
    fill(client, student, slug3, {q3: 60})
    deleted = client.get(f"{QAPI}/{lq['id']}/usage", headers=teacher).json()["mySurveys"][0]["surveyId"]
    client.delete(f"{SAPI}/{deleted}", headers=teacher)

    s = cross(client, teacher, lq["id"])
    assert (s["surveyCount"], s["answeredCount"], s["max"]) == (1, 1, 20)


def test_stage1_survey_counted_after_save_to_library(client, teacher, student):
    """阶段一的旧问卷把题存入题库后，它以前收到的答卷也计入，旧数据不用迁移。"""
    sid = client.post(SAPI, headers=teacher, json={"title": "去年的问卷"}).json()["id"]
    q = client.post(f"{SAPI}/{sid}/questions", headers=teacher, json=AGE).json()["questions"][0]["id"]
    slug = client.post(f"{SAPI}/{sid}/publish", headers=teacher).json()["slug"]
    for age in [19, 21]:
        fill(client, student, slug, {q: age})
    saved = client.post(f"{SAPI}/{sid}/questions/{q}/save-to-library", headers=teacher).json()
    lqid = saved["questions"][0]["source"]["questionId"]

    s = cross(client, teacher, lqid)
    assert (s["surveyCount"], s["answeredCount"], s["average"]) == (1, 2, 20)


def test_unused_question_returns_empty_stats(client, teacher):
    grade = new_lib_question(client, teacher, GRADE)
    s = cross(client, teacher, grade["id"])
    assert (s["surveyCount"], s["totalResponses"], s["answeredCount"], s["bySurvey"]) == (0, 0, 0, [])
    assert [(o["label"], o["count"]) for o in s["options"]] == [("大一", 0), ("大二", 0), ("大三", 0)]
    age = new_lib_question(client, teacher, AGE)
    s = cross(client, teacher, age["id"])
    assert s["distribution"] == [] and "average" not in s
    assert client.get(f"{QAPI}/{age['id']}/stats", headers=teacher, params={"version": 9}).status_code == 404


# ---------- 索引 ----------

def test_both_steps_use_indexes(client, db, teacher, student):
    lq = new_lib_question(client, teacher, AGE)
    for title in ["一班", "二班"]:
        slug, q, _ = survey_with(client, teacher, lq["id"], title)
        fill(client, student, slug, {q: 20})
    uid = db.users.find_one({"username": "teacher_01"})["_id"]

    pipeline = svc.sources_pipeline(ObjectId(lq["id"]), uid, None)
    plan = json.dumps(db.command("aggregate", "surveys", pipeline=pipeline, explain=True), default=str)
    assert "IXSCAN" in plan and "question_source" in plan and "COLLSCAN" not in plan

    sources = list(db.surveys.aggregate(pipeline))
    plan = json.dumps(db.command("aggregate", "responses", explain=True,
                                 pipeline=svc.cross_pipeline(sources, {"n": [{"$count": "n"}]})), default=str)
    assert "IXSCAN" in plan and "survey_submittedAt" in plan and "COLLSCAN" not in plan
