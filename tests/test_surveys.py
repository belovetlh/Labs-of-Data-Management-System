"""问卷管理接口测试：创建、列表、详情、修改草稿、发布 / 关闭 / 重开、删除。"""

import re
import threading
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.services import surveys as svc
from db.schema import SCHEMA_VERSION

API = "/api/surveys"

QUESTION = {
    "id": "q_a0000001", "type": "single_choice", "title": "你的性别", "required": True,
    "options": [{"id": "o_b0000001", "label": "男"}, {"id": "o_b0000002", "label": "女"}],
}


def iso(dt: datetime) -> str:
    return dt.isoformat()


def future(days=7) -> str:
    return iso(datetime.now(timezone.utc) + timedelta(days=days))


def create(client, headers, **body):
    body.setdefault("title", "大学生生活调查")
    return client.post(API, json=body, headers=headers)


def add_question(db, survey_id):
    # 题目管理在第 4 段实现，这里直接写库
    db.surveys.update_one({"_id": ObjectId(survey_id)}, {"$push": {"questions": QUESTION}})


def published_survey(client, db, headers, **body):
    sid = create(client, headers, **body).json()["id"]
    add_question(db, sid)
    assert client.post(f"{API}/{sid}/publish", headers=headers).status_code == 200
    return sid


def set_deadline_in_db(db, survey_id, dt):
    # 绕过接口的"必须在未来"检查，模拟时间已经过去
    db.surveys.update_one({"_id": ObjectId(survey_id)}, {"$set": {"settings.deadline": dt}})


def add_response(db, survey_id):
    db.responses.insert_one({
        "schemaVersion": SCHEMA_VERSION, "surveyId": ObjectId(survey_id),
        "submittedAt": datetime.now(timezone.utc),
        "answers": [{"questionId": "q_a0000001", "value": "o_b0000001"}],
    })


# ---------- 创建 ----------

def test_create_defaults(client, login_as):
    resp = create(client, login_as())
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "draft" and body["phase"] == "draft"
    assert body["description"] == ""
    assert body["settings"] == {"allowAnonymous": False, "allowMultipleSubmissions": True, "deadline": None}
    assert body["questions"] == [] and body["questionCount"] == 0 and body["responseCount"] == 0
    assert re.fullmatch(r"[A-Za-z0-9]{8}", body["slug"])
    assert body["shareUrl"] == f"/survey/{body['slug']}"


def test_create_with_settings(client, login_as):
    deadline = future()
    body = create(client, login_as(), description="约 2 分钟",
                  settings={"allowAnonymous": True, "allowMultipleSubmissions": False, "deadline": deadline}).json()
    assert body["settings"]["allowAnonymous"] is True
    assert body["settings"]["allowMultipleSubmissions"] is False
    assert datetime.fromisoformat(body["settings"]["deadline"]) == pytest.approx(
        datetime.fromisoformat(deadline), abs=timedelta(milliseconds=1))


def test_slugs_are_unique(client, login_as):
    headers = login_as()
    slugs = {create(client, headers).json()["slug"] for _ in range(20)}
    assert len(slugs) == 20


@pytest.mark.parametrize("body", [
    {"title": ""},
    {"title": "   "},
    {"title": "a" * 201},
    {"title": "ok", "description": "a" * 5001},
    {"title": "ok", "settings": {"deadline": "2020-01-01T00:00:00+08:00"}},  # 已过
    {"title": "ok", "settings": {"deadline": "2030-01-01T00:00:00"}},        # 不带时区
])
def test_create_invalid_422(client, login_as, body):
    assert client.post(API, json=body, headers=login_as()).status_code == 422


def test_create_requires_login(client):
    assert client.post(API, json={"title": "x"}).status_code == 401


# ---------- 列表与详情 ----------

def test_list_only_mine_newest_first(client, login_as):
    alice, bob = login_as("alice_01"), login_as("bob_01")
    create(client, alice, title="A1")
    create(client, bob, title="B1")
    create(client, alice, title="A2")
    titles = [s["title"] for s in client.get(API, headers=alice).json()]
    assert titles == ["A2", "A1"]
    assert [s["title"] for s in client.get(API, headers=bob).json()] == ["B1"]


def test_list_counts_questions_and_responses(client, db, login_as):
    headers = login_as()
    sid = published_survey(client, db, headers)
    for _ in range(3):
        add_response(db, sid)
    item = client.get(API, headers=headers).json()[0]
    assert item["questionCount"] == 1
    assert item["responseCount"] == 3
    assert "questions" not in item  # 列表不带题目内容


def test_other_users_survey_is_404(client, login_as):
    alice, bob = login_as("alice_01"), login_as("bob_01")
    sid = create(client, alice).json()["id"]
    assert client.get(f"{API}/{sid}", headers=alice).status_code == 200
    for method, path in [("get", ""), ("patch", ""), ("post", "/publish"), ("post", "/close"),
                         ("post", "/reopen"), ("delete", "")]:
        kwargs = {"json": {}} if method in ("patch", "post") else {}
        resp = getattr(client, method)(f"{API}/{sid}{path}", headers=bob, **kwargs)
        assert resp.status_code == 404, (method, path)


@pytest.mark.parametrize("sid", [str(ObjectId()), "not-an-id"])
def test_unknown_or_malformed_id_404(client, login_as, sid):
    assert client.get(f"{API}/{sid}", headers=login_as()).status_code == 404


# ---------- 修改草稿 ----------

def test_update_draft_partial(client, login_as):
    headers = login_as()
    sid = create(client, headers, description="原说明", settings={"deadline": future()}).json()["id"]
    body = client.patch(f"{API}/{sid}", json={"title": "新标题", "settings": {"allowAnonymous": True}},
                        headers=headers).json()
    assert body["title"] == "新标题"
    assert body["description"] == "原说明"           # 没传的不变
    assert body["settings"]["allowAnonymous"] is True
    assert body["settings"]["deadline"] is not None  # 没传的不变


def test_update_draft_clear_deadline(client, login_as):
    headers = login_as()
    sid = create(client, headers, settings={"deadline": future()}).json()["id"]
    body = client.patch(f"{API}/{sid}", json={"settings": {"deadline": None}}, headers=headers).json()
    assert body["settings"]["deadline"] is None


@pytest.mark.parametrize("change", [
    {"title": "改标题"},
    {"description": "改说明"},
    {"settings": {"allowAnonymous": True}},
    {"settings": {"allowMultipleSubmissions": False}},
    {"settings": {"deadline": None}},
])
def test_published_survey_content_locked(client, db, login_as, change):
    headers = login_as()
    sid = published_survey(client, db, headers)
    resp = client.patch(f"{API}/{sid}", json=change, headers=headers)
    assert resp.status_code == 409
    assert "不能再修改" in resp.json()["detail"]


# ---------- 发布 / 关闭 / 重开 ----------

def test_publish_requires_question(client, db, login_as):
    headers = login_as()
    sid = create(client, headers).json()["id"]
    resp = client.post(f"{API}/{sid}/publish", headers=headers)
    assert resp.status_code == 409
    assert "至少要有 1 道题" in resp.json()["detail"]

    add_question(db, sid)
    body = client.post(f"{API}/{sid}/publish", headers=headers).json()
    assert body["status"] == "published" and body["phase"] == "open"
    assert body["publishedAt"] is not None


def test_publish_with_past_deadline_409(client, db, login_as):
    headers = login_as()
    sid = create(client, headers).json()["id"]
    add_question(db, sid)
    set_deadline_in_db(db, sid, datetime.now(timezone.utc) - timedelta(hours=1))
    resp = client.post(f"{API}/{sid}/publish", headers=headers)
    assert resp.status_code == 409 and "截止时间已过" in resp.json()["detail"]


def test_publish_twice_409(client, db, login_as):
    headers = login_as()
    sid = published_survey(client, db, headers)
    resp = client.post(f"{API}/{sid}/publish", headers=headers)
    assert resp.status_code == 409 and resp.json()["detail"] == "只有草稿可以发布"


def test_close_and_reopen(client, db, login_as):
    headers = login_as()
    sid = published_survey(client, db, headers)
    closed = client.post(f"{API}/{sid}/close", headers=headers).json()
    assert closed["status"] == "closed" and closed["phase"] == "closed" and closed["closedAt"]
    assert client.post(f"{API}/{sid}/close", headers=headers).status_code == 409

    reopened = client.post(f"{API}/{sid}/reopen", headers=headers).json()
    assert reopened["status"] == "published" and reopened["phase"] == "open"
    assert reopened["closedAt"] is None


def test_close_draft_409(client, login_as):
    headers = login_as()
    sid = create(client, headers).json()["id"]
    assert client.post(f"{API}/{sid}/close", headers=headers).status_code == 409


def test_reopen_open_survey_409(client, db, login_as):
    headers = login_as()
    sid = published_survey(client, db, headers)
    resp = client.post(f"{API}/{sid}/reopen", headers=headers)
    assert resp.status_code == 409 and "正在收集中" in resp.json()["detail"]


def test_expired_survey_reopen_needs_new_deadline(client, db, login_as):
    headers = login_as()
    sid = published_survey(client, db, headers)
    set_deadline_in_db(db, sid, datetime.now(timezone.utc) - timedelta(hours=1))
    assert client.get(f"{API}/{sid}", headers=headers).json()["phase"] == "expired"

    resp = client.post(f"{API}/{sid}/reopen", headers=headers)  # 不给新截止时间
    assert resp.status_code == 409 and "设置新的截止时间" in resp.json()["detail"]

    body = client.post(f"{API}/{sid}/reopen", json={"deadline": future()}, headers=headers).json()
    assert body["phase"] == "open"


def test_expired_survey_reopen_clear_deadline(client, db, login_as):
    headers = login_as()
    sid = published_survey(client, db, headers)
    set_deadline_in_db(db, sid, datetime.now(timezone.utc) - timedelta(hours=1))
    body = client.post(f"{API}/{sid}/reopen", json={"deadline": None}, headers=headers).json()
    assert body["phase"] == "open" and body["settings"]["deadline"] is None


def test_closed_and_expired_reopen_needs_new_deadline(client, db, login_as):
    headers = login_as()
    sid = published_survey(client, db, headers)
    client.post(f"{API}/{sid}/close", headers=headers)
    set_deadline_in_db(db, sid, datetime.now(timezone.utc) - timedelta(hours=1))
    assert client.post(f"{API}/{sid}/reopen", headers=headers).status_code == 409
    assert client.post(f"{API}/{sid}/reopen", json={"deadline": future()}, headers=headers).status_code == 200


def test_reopen_with_past_deadline_422(client, db, login_as):
    headers = login_as()
    sid = published_survey(client, db, headers)
    client.post(f"{API}/{sid}/close", headers=headers)
    resp = client.post(f"{API}/{sid}/reopen", json={"deadline": "2020-01-01T00:00:00+08:00"}, headers=headers)
    assert resp.status_code == 422


def test_concurrent_publish_only_one_succeeds(client, db, login_as):
    headers = login_as()
    sid = create(client, headers).json()["id"]
    add_question(db, sid)
    owner_id = db.users.find_one({"username": "owner_01"})["_id"]

    barrier = threading.Barrier(8)
    results = []

    def worker():
        barrier.wait()
        try:
            svc.publish(db, owner_id, sid)
            results.append("ok")
        except svc.SurveyConflict:
            results.append("conflict")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 1
    assert results.count("conflict") == 7


def test_concurrent_edit_and_publish_never_edits_published(client, db, login_as):
    """修改草稿和发布同时进行：要么改成功后再发布，要么发布后修改被拒，不会改到已发布的问卷。"""
    headers = login_as()
    owner_id = None
    for _ in range(10):
        sid = create(client, headers, title="原标题").json()["id"]
        add_question(db, sid)
        owner_id = owner_id or db.users.find_one({"username": "owner_01"})["_id"]
        barrier = threading.Barrier(2)
        edit_result = {}

        def edit():
            barrier.wait()
            try:
                svc.update_draft(db, owner_id, sid, {"title": "改后标题"})
                edit_result["ok"] = True
            except svc.SurveyConflict:
                edit_result["ok"] = False

        def pub():
            barrier.wait()
            svc.publish(db, owner_id, sid)

        threads = [threading.Thread(target=edit), threading.Thread(target=pub)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        doc = db.surveys.find_one({"_id": ObjectId(sid)})
        assert doc["status"] == "published"
        # 修改成功 ⇔ 标题被改；修改失败 ⇔ 标题保持原样
        assert (doc["title"] == "改后标题") == edit_result["ok"]


# ---------- 删除 ----------

@pytest.mark.parametrize("state", ["draft", "published", "closed"])
def test_delete_any_state(client, db, login_as, state):
    headers = login_as()
    if state == "draft":
        sid = create(client, headers).json()["id"]
    else:
        sid = published_survey(client, db, headers)
        add_response(db, sid)
        if state == "closed":
            client.post(f"{API}/{sid}/close", headers=headers)

    resp = client.delete(f"{API}/{sid}", headers=headers)
    assert resp.status_code == 204
    assert client.get(f"{API}/{sid}", headers=headers).status_code == 404
    assert client.get(API, headers=headers).json() == []
    assert client.delete(f"{API}/{sid}", headers=headers).status_code == 404  # 不能删两次

    # 标记删除：数据仍在库里
    doc = db.surveys.find_one({"_id": ObjectId(sid)})
    assert doc["deletedAt"] is not None
    if state != "draft":
        assert db.responses.count_documents({"surveyId": ObjectId(sid)}) == 1


def test_deleted_survey_cannot_be_operated(client, db, login_as):
    headers = login_as()
    sid = create(client, headers).json()["id"]
    add_question(db, sid)
    client.delete(f"{API}/{sid}", headers=headers)
    assert client.patch(f"{API}/{sid}", json={"title": "x"}, headers=headers).status_code == 404
    assert client.post(f"{API}/{sid}/publish", headers=headers).status_code == 404
