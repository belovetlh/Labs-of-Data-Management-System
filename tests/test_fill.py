"""填写与提交接口测试：打开链接、navigate、提交、多次填写、匿名、失败撤回。"""

import threading
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from pymongo.collection import Collection

from app.services import submissions as sub_svc

API = "/api/surveys"
FILL = "/api/fill"


def make_survey(client, owner, *, anonymous=False, multiple=True, publish=True):
    """建一份按作业原文例子配置跳转的问卷：第 1 题选 A → 第 5 题；选 B → 第 3 题。

    返回 (survey_id, slug, 题目列表)。
    """
    s = client.post(API, json={"title": "生活调查", "description": "约 1 分钟", "settings": {
        "allowAnonymous": anonymous, "allowMultipleSubmissions": multiple}}, headers=owner).json()
    sid = s["id"]
    defs = [
        {"type": "single_choice", "title": "第一题", "required": True,
         "options": [{"label": "A"}, {"label": "B"}, {"label": "C"}]},
        {"type": "multiple_choice", "title": "第二题", "required": True,
         "options": [{"label": "苹果"}, {"label": "香蕉"}, {"label": "西瓜"}],
         "validation": {"minSelect": 2, "maxSelect": 3}},
        {"type": "number_input", "title": "第三题", "required": True,
         "validation": {"min": 0, "max": 120, "integer": True}},
        {"type": "text_input", "title": "第四题", "validation": {"maxLength": 20}},
        {"type": "text_input", "title": "第五题", "required": True, "validation": {"minLength": 2}},
    ]
    for d in defs:
        client.post(f"{API}/{sid}/questions", json=d, headers=owner)
    qs = client.get(f"{API}/{sid}", headers=owner).json()["questions"]
    a, b = qs[0]["options"][0]["id"], qs[0]["options"][1]["id"]
    client.put(f"{API}/{sid}/questions/{qs[0]['id']}/jump-rules", headers=owner, json={"rules": [
        {"when": {"op": "eq", "value": a}, "goto": qs[4]["id"]},
        {"when": {"op": "eq", "value": b}, "goto": qs[2]["id"]},
    ]})
    if publish:
        assert client.post(f"{API}/{sid}/publish", headers=owner).status_code == 200
    return sid, s["slug"], client.get(f"{API}/{sid}", headers=owner).json()["questions"]


def opt(q, i):
    return q["options"][i]["id"]


def path_a(qs):
    """选 A：只答第 1、5 题。"""
    return {qs[0]["id"]: opt(qs[0], 0), qs[4]["id"]: "很好很好"}


def path_b(qs):
    """选 B：答第 1、3、4、5 题。"""
    return {qs[0]["id"]: opt(qs[0], 1), qs[2]["id"]: 20, qs[3]["id"]: None, qs[4]["id"]: "还行"}


def submit(client, headers, slug, answers):
    return client.post(f"{FILL}/{slug}/responses", json={"answers": answers}, headers=headers)


@pytest.fixture()
def owner(login_as):
    return login_as("owner_01", "问卷作者")


@pytest.fixture()
def resp(login_as):
    return login_as("resp_01", "填写者")


# ---------- 打开链接 ----------

def test_fill_view(client, owner, resp):
    _, slug, qs = make_survey(client, owner)
    body = client.get(f"{FILL}/{slug}", headers=resp).json()
    assert body["title"] == "生活调查" and body["description"] == "约 1 分钟"
    assert body["phase"] == "open" and body["canSubmit"] is True and body["reason"] is None
    assert body["alreadySubmitted"] is False and body["allowAnonymous"] is False
    assert [q["id"] for q in body["questions"]] == [q["id"] for q in qs]
    assert all("jumpRules" not in q for q in body["questions"])  # 不暴露跳转规则


def test_fill_requires_login(client, owner):
    _, slug, _ = make_survey(client, owner)
    assert client.get(f"{FILL}/{slug}").status_code == 401
    assert client.post(f"{FILL}/{slug}/responses", json={"answers": {}}).status_code == 401


def test_unknown_slug_and_draft_404(client, owner, resp):
    _, slug, _ = make_survey(client, owner, publish=False)
    assert client.get(f"{FILL}/{slug}", headers=resp).status_code == 404
    assert client.get(f"{FILL}/NoSuch12", headers=resp).status_code == 404


def test_deleted_survey_410(client, owner, resp):
    sid, slug, qs = make_survey(client, owner)
    client.delete(f"{API}/{sid}", headers=owner)
    resp_open = client.get(f"{FILL}/{slug}", headers=resp)
    assert resp_open.status_code == 410 and resp_open.json()["detail"] == "问卷已删除"
    assert submit(client, resp, slug, path_a(qs)).status_code == 410


def test_deleted_draft_404(client, owner, resp):
    sid, slug, _ = make_survey(client, owner, publish=False)
    client.delete(f"{API}/{sid}", headers=owner)
    assert client.get(f"{FILL}/{slug}", headers=resp).status_code == 404


def test_closed_survey_viewable_but_not_submittable(client, owner, resp):
    sid, slug, qs = make_survey(client, owner)
    client.post(f"{API}/{sid}/close", headers=owner)
    body = client.get(f"{FILL}/{slug}", headers=resp).json()
    assert body["canSubmit"] is False and body["reason"] == "问卷已关闭"
    r = submit(client, resp, slug, path_a(qs))
    assert r.status_code == 409 and r.json()["detail"] == "问卷已关闭"
    nav = client.post(f"{FILL}/{slug}/navigate", json={"answers": {}}, headers=resp)
    assert nav.status_code == 409


def test_expired_survey_not_submittable(client, db, owner, resp):
    sid, slug, qs = make_survey(client, owner)
    db.surveys.update_one({"_id": ObjectId(sid)},
                          {"$set": {"settings.deadline": datetime.now(timezone.utc) - timedelta(minutes=1)}})
    assert client.get(f"{FILL}/{slug}", headers=resp).json()["reason"] == "问卷已截止"
    assert submit(client, resp, slug, path_a(qs)).json()["detail"] == "问卷已截止"


# ---------- navigate ----------

def test_navigate_step_by_step_doc_example(client, owner, resp):
    _, slug, qs = make_survey(client, owner)
    ids = [q["id"] for q in qs]

    def nav(answers):
        r = client.post(f"{FILL}/{slug}/navigate", json={"answers": answers}, headers=resp)
        assert r.status_code == 200
        return r.json()

    first = nav({})
    assert first["nextQuestion"] == ids[0] and first["complete"] is False

    after_a = nav({ids[0]: opt(qs[0], 0)})
    assert after_a["path"] == [ids[0], ids[4]] and after_a["nextQuestion"] == ids[4]

    after_b = nav({ids[0]: opt(qs[0], 1)})
    assert after_b["nextQuestion"] == ids[2]

    bad = nav({ids[0]: opt(qs[0], 1), ids[2]: 200})
    assert bad["errors"] == {ids[2]: "请输入 0–120 之间的整数"} and bad["nextQuestion"] == ids[2]

    done = nav(path_b(qs))
    assert done["complete"] is True and done["nextQuestion"] is None

    # 选 B 后又改回 A：第 3、4 题的答案已不在路径上，只提示不报错
    changed = nav({**path_b(qs), ids[0]: opt(qs[0], 0)})
    assert sorted(changed["offPath"]) == sorted([ids[2], ids[3]])
    assert changed["errors"] == {} and changed["complete"] is False


def test_navigate_saves_nothing(client, db, owner, resp):
    _, slug, qs = make_survey(client, owner)
    client.post(f"{FILL}/{slug}/navigate", json={"answers": path_a(qs)}, headers=resp)
    assert db.responses.count_documents({}) == 0
    assert db.participations.count_documents({}) == 0


# ---------- 提交 ----------

def test_submit_named_response(client, db, owner, resp):
    sid, slug, qs = make_survey(client, owner)
    r = submit(client, resp, slug, path_b(qs))
    assert r.status_code == 201 and "submittedAt" in r.json()
    doc = db.responses.find_one({"surveyId": ObjectId(sid)})
    resp_user = db.users.find_one({"username": "resp_01"})
    assert doc["respondentId"] == resp_user["_id"]
    # 只保存路径上实际作答的题（第 4 题传了 null：看过不答，不保存）
    assert doc["answers"] == [
        {"questionId": qs[0]["id"], "value": opt(qs[0], 1)},
        {"questionId": qs[2]["id"], "value": 20},
        {"questionId": qs[4]["id"], "value": "还行"},
    ]


def test_submit_anonymous_response_has_no_respondent(client, db, owner, resp):
    sid, slug, qs = make_survey(client, owner, anonymous=True)
    assert client.get(f"{FILL}/{slug}", headers=resp).json()["allowAnonymous"] is True
    assert submit(client, resp, slug, path_a(qs)).status_code == 201
    doc = db.responses.find_one({"surveyId": ObjectId(sid)})
    assert "respondentId" not in doc
    # 参与记录与答卷分开：只知道此人填过，不知道填了哪一份
    assert db.participations.count_documents({"surveyId": ObjectId(sid)}) == 1


def test_owner_can_fill_own_survey(client, owner):
    _, slug, qs = make_survey(client, owner)
    assert submit(client, owner, slug, path_a(qs)).status_code == 201


@pytest.mark.parametrize("make_answers,expect_index,expect", [
    (lambda qs: {qs[0]["id"]: opt(qs[0], 0)}, 4, "此题为必答题"),                       # 路径上的必答题没答
    (lambda qs: {**path_a(qs), qs[4]["id"]: "好"}, 4, "至少输入 2 个字"),                 # 不合规
    (lambda qs: {**path_a(qs), qs[2]["id"]: 20}, 2, "按前面的回答应被跳过"),               # 答了被跳过的题
    (lambda qs: {qs[0]["id"]: opt(qs[0], 2), qs[1]["id"]: [opt(qs[1], 0)], qs[2]["id"]: 1,
                 qs[4]["id"]: "好的"}, 1, "至少选择 2 项"),
])
def test_submit_invalid_422_saves_nothing(client, db, owner, resp, make_answers, expect_index, expect):
    _, slug, qs = make_survey(client, owner)
    r = submit(client, resp, slug, make_answers(qs))
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["message"] == "提交内容有误"
    assert expect in detail["errors"][qs[expect_index]["id"]]
    assert db.responses.count_documents({}) == 0
    assert db.participations.count_documents({}) == 0


def test_submit_unknown_question_422(client, owner, resp):
    _, slug, qs = make_survey(client, owner)
    r = submit(client, resp, slug, {**path_a(qs), "q_00000000": "x"})
    assert r.status_code == 422 and "q_00000000" in r.json()["detail"]["errors"]


# ---------- 多次填写 ----------

def test_multiple_submissions_allowed(client, db, owner, resp):
    sid, slug, qs = make_survey(client, owner, multiple=True)
    assert submit(client, resp, slug, path_a(qs)).status_code == 201
    assert submit(client, resp, slug, path_b(qs)).status_code == 201
    assert db.responses.count_documents({"surveyId": ObjectId(sid)}) == 2
    assert db.participations.find_one({"surveyId": ObjectId(sid)})["count"] == 2
    assert client.get(f"{FILL}/{slug}", headers=resp).json()["canSubmit"] is True


def test_multiple_submissions_disallowed(client, db, owner, resp, login_as):
    sid, slug, qs = make_survey(client, owner, multiple=False)
    assert submit(client, resp, slug, path_a(qs)).status_code == 201
    second = submit(client, resp, slug, path_b(qs))
    assert second.status_code == 409 and second.json()["detail"] == "你已经填写过这份问卷"
    assert db.responses.count_documents({"surveyId": ObjectId(sid)}) == 1

    view = client.get(f"{FILL}/{slug}", headers=resp).json()  # 打开链接时就提示
    assert view["alreadySubmitted"] is True and view["canSubmit"] is False
    assert view["reason"] == "你已经填写过这份问卷"

    other = login_as("resp_02")  # 别人不受影响
    assert submit(client, other, slug, path_a(qs)).status_code == 201


def _concurrent_submits(client, db, slug, qs, n):
    user = db.users.find_one({"username": "resp_01"})
    barrier = threading.Barrier(n)
    results = []

    def worker():
        barrier.wait()
        try:
            sub_svc.submit(db, slug, user, path_a(qs))
            results.append("ok")
        except sub_svc.AlreadySubmitted:
            results.append("dup")

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_concurrent_submits_disallowed_only_one(client, db, owner, resp):
    sid, slug, qs = make_survey(client, owner, multiple=False)
    results = _concurrent_submits(client, db, slug, qs, 5)
    assert results.count("ok") == 1 and results.count("dup") == 4
    assert db.responses.count_documents({"surveyId": ObjectId(sid)}) == 1


def test_concurrent_submits_allowed_all_saved(client, db, owner, resp):
    sid, slug, qs = make_survey(client, owner, multiple=True)
    results = _concurrent_submits(client, db, slug, qs, 5)
    assert results == ["ok"] * 5
    assert db.responses.count_documents({"surveyId": ObjectId(sid)}) == 5
    assert db.participations.find_one({"surveyId": ObjectId(sid)})["count"] == 5


# ---------- 写答卷失败时撤回登记 ----------

@pytest.fixture()
def failing_response_insert(monkeypatch):
    """让 responses 集合的 insert_one 抛错，模拟写答卷失败。"""
    original = Collection.insert_one

    def fake(self, *args, **kwargs):
        if self.name == "responses":
            raise RuntimeError("模拟写入失败")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Collection, "insert_one", fake)
    return monkeypatch


@pytest.mark.parametrize("multiple", [False, True])
def test_failed_response_insert_rolls_back_participation(client, db, owner, resp, failing_response_insert, multiple):
    sid, slug, qs = make_survey(client, owner, multiple=multiple)
    user = db.users.find_one({"username": "resp_01"})
    with pytest.raises(RuntimeError):
        sub_svc.submit(db, slug, user, path_a(qs))
    assert db.participations.count_documents({"surveyId": ObjectId(sid)}) == 0

    failing_response_insert.undo()  # 恢复正常后，此人仍能提交
    sub_svc.submit(db, slug, user, path_a(qs))
    assert db.responses.count_documents({"surveyId": ObjectId(sid)}) == 1


def test_failed_repeat_insert_decrements_count(client, db, owner, resp, monkeypatch):
    sid, slug, qs = make_survey(client, owner, multiple=True)
    user = db.users.find_one({"username": "resp_01"})
    sub_svc.submit(db, slug, user, path_a(qs))  # 第一次正常
    original = Collection.insert_one

    def fake(self, *args, **kwargs):
        if self.name == "responses":
            raise RuntimeError("模拟写入失败")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Collection, "insert_one", fake)
    with pytest.raises(RuntimeError):
        sub_svc.submit(db, slug, user, path_b(qs))
    assert db.participations.find_one({"surveyId": ObjectId(sid)})["count"] == 1


# ---------- 答卷不可修改 ----------

def test_no_endpoint_to_modify_or_delete_responses(client, owner, resp):
    _, slug, qs = make_survey(client, owner)
    submit(client, resp, slug, path_a(qs))
    for method in ("put", "patch", "delete"):
        r = getattr(client, method)(f"{FILL}/{slug}/responses", headers=resp)
        assert r.status_code == 405
