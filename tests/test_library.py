"""题库：题目与版本（阶段二第 2 段）。"""

import threading

import pytest
from bson import ObjectId
from pymongo.collection import Collection

from app.services import library as lib

API = "/api/questions"

AGE = {"type": "number_input", "title": "你的年龄", "required": True,
       "validation": {"min": 0, "max": 120, "integer": True}}
GRADE = {"type": "single_choice", "title": "你的年级", "required": True,
         "options": [{"label": "大一"}, {"label": "大二"}, {"label": "大三"}]}


@pytest.fixture()
def owner(login_as):
    return login_as("teacher_01", "王老师")


@pytest.fixture()
def other(login_as):
    return login_as("student_01", "小明")


def create(client, headers, content=AGE, note=""):
    r = client.post(API, headers=headers, json={"content": content, "note": note})
    assert r.status_code == 201, r.text
    return r.json()


def new_version(client, headers, qid, base, content, note=""):
    return client.post(f"{API}/{qid}/versions", headers=headers,
                       json={"baseVersion": base, "content": content, "note": note})


def history(client, headers, qid):
    r = client.get(f"{API}/{qid}/versions", headers=headers)
    assert r.status_code == 200
    return r.json()


def share(client, headers, qid, username):
    r = client.post(f"{API}/{qid}/shares", headers=headers, json={"username": username})
    assert r.status_code == 200, r.text
    return r.json()


def age(max_value):
    return {**AGE, "validation": {**AGE["validation"], "max": max_value}}


# ---------- 新建、列表、详情 ----------

def test_create_question_makes_version_1(client, owner):
    q = create(client, owner, note="常用题")
    assert q["latest"]["version"] == 1 and q["latest"]["title"] == "你的年龄"
    assert q["isOwner"] is True and q["owner"] == "王老师（teacher_01）"
    assert q["derivedFrom"] is None
    assert q["content"] == {"type": "number_input", "title": "你的年龄", "required": True,
                            "validation": {"min": 0, "max": 120, "integer": True}}
    [v1] = history(client, owner, q["id"])
    assert (v1["version"], v1["parentVersion"], v1["kind"], v1["note"]) == (1, None, "create", "常用题")


def test_create_choice_question_generates_option_ids(client, owner):
    q = create(client, owner, GRADE)
    ids = [o["id"] for o in q["content"]["options"]]
    assert len(set(ids)) == 3 and all(i.startswith("o_") for i in ids)


def test_create_checks_definition(client, owner):
    r = client.post(API, headers=owner, json={"content": age(-1)})
    assert r.status_code == 422 and "最小值不能大于最大值" in r.json()["detail"]
    bad_option = {**GRADE, "options": [{"id": "o_12345678", "label": "大一"}, {"label": "大二"}]}
    r = client.post(API, headers=owner, json={"content": bad_option})
    assert r.status_code == 422 and "不属于这道题" in r.json()["detail"]


def test_create_rejects_jump_rules_in_content(client, owner):
    # 题目内容不带跳转规则：跳转属于问卷
    r = client.post(API, headers=owner, json={"content": {**AGE, "jumpRules": []}})
    assert r.status_code == 422


def test_list_mine_newest_first_and_isolated(client, owner, other):
    a = create(client, owner)
    b = create(client, owner, GRADE)
    mine = client.get(API, headers=owner).json()
    assert [q["id"] for q in mine] == [b["id"], a["id"]]
    assert client.get(API, headers=other).json() == []
    assert client.get(f"{API}?scope=shared", headers=owner).json() == []


def test_others_question_not_visible(client, owner, other):
    q = create(client, owner)
    assert client.get(f"{API}/{q['id']}", headers=other).status_code == 404
    assert client.get(f"{API}/{q['id']}/versions", headers=other).status_code == 404
    assert client.get(f"{API}/not-an-id", headers=owner).status_code == 404


def test_requires_login(client):
    assert client.get(API).status_code == 401


# ---------- 新版本 ----------

def test_new_version_keeps_old_one(client, owner):
    q = create(client, owner)
    r = new_version(client, owner, q["id"], 1, age(100), "上限改为 100")
    assert r.status_code == 201
    v2 = r.json()
    assert (v2["version"], v2["parentVersion"], v2["kind"]) == (2, 1, "edit")
    detail = client.get(f"{API}/{q['id']}", headers=owner).json()
    assert detail["latest"]["version"] == 2 and detail["content"]["validation"]["max"] == 100
    v1 = client.get(f"{API}/{q['id']}/versions/1", headers=owner).json()
    assert v1["content"]["validation"]["max"] == 120   # 旧版本原样保留


def test_versions_form_a_tree(client, owner):
    q = create(client, owner)
    new_version(client, owner, q["id"], 1, age(100))   # v2 ← v1
    new_version(client, owner, q["id"], 2, age(90))    # v3 ← v2
    new_version(client, owner, q["id"], 1, age(150))   # v4 ← v1：从旧版本分出另一支
    assert [(v["version"], v["parentVersion"]) for v in history(client, owner, q["id"])] == \
           [(1, None), (2, 1), (3, 2), (4, 1)]


def test_option_ids_kept_across_versions(client, owner):
    q = create(client, owner, GRADE)
    o1, o2, o3 = q["content"]["options"]
    content = {**GRADE, "options": [
        {"id": o1["id"], "label": "大一"},
        {"id": o2["id"], "label": "大二（含转专业）"},   # 只改文字，id 不变
        {"label": "研究生"},                             # 新选项
    ]}                                                   # 大三被删掉
    v2 = new_version(client, owner, q["id"], 1, content).json()
    ids = [o["id"] for o in v2["content"]["options"]]
    assert ids[:2] == [o1["id"], o2["id"]]
    assert ids[2] not in {o1["id"], o2["id"], o3["id"]}


def test_option_id_must_come_from_base_version(client, owner):
    q = create(client, owner, GRADE)
    other_q = create(client, owner, GRADE)
    foreign = other_q["content"]["options"][0]["id"]
    content = {**GRADE, "options": [{"id": foreign, "label": "大一"}, {"label": "大二"}]}
    r = new_version(client, owner, q["id"], 1, content)
    assert r.status_code == 422 and "不属于这道题" in r.json()["detail"]


def test_type_cannot_change_between_versions(client, owner):
    q = create(client, owner)
    r = new_version(client, owner, q["id"], 1, {"type": "text_input", "title": "你的年龄", "required": True})
    assert r.status_code == 422 and "题型必须相同" in r.json()["detail"]


def test_unchanged_content_rejected(client, owner):
    q = create(client, owner)
    r = new_version(client, owner, q["id"], 1, AGE)
    assert r.status_code == 409 and "内容与 v1 相同" in r.json()["detail"]


def test_unknown_base_version(client, owner):
    q = create(client, owner)
    assert new_version(client, owner, q["id"], 9, age(100)).status_code == 404


def test_concurrent_new_versions_get_distinct_numbers(client, db, owner):
    """同时基于 v1 建 5 个新版本：唯一索引让撞号的重新取号，版本号各不相同，一个不丢。"""
    q = create(client, owner)
    uid = db.users.find_one({"username": "teacher_01"})["_id"]
    barrier = threading.Barrier(5)
    numbers, errors = [], []

    def worker(i):
        barrier.wait()
        try:
            data = {"type": "number_input", "title": "你的年龄", "required": True,
                    "validation": {"min": 0, "max": 200 + i, "integer": True}}
            numbers.append(lib.create_version(db, uid, q["id"], 1, data, "")["version"])
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sorted(numbers) == [2, 3, 4, 5, 6]
    assert client.get(f"{API}/{q['id']}", headers=owner).json()["latest"]["version"] == 6


def test_stale_latest_summary_heals(client, db, owner):
    """latest 摘要落后（如更新摘要前程序中断）时，下一次建版本会补上，版本号不受影响。"""
    q = create(client, owner)
    new_version(client, owner, q["id"], 1, age(100))
    db.questions.update_one({"_id": ObjectId(q["id"])}, {"$set": {"latest.version": 1}})
    v3 = new_version(client, owner, q["id"], 2, age(90)).json()
    assert v3["version"] == 3
    assert client.get(f"{API}/{q['id']}", headers=owner).json()["latest"]["version"] == 3


# ---------- 恢复旧版本 ----------

def test_restore_creates_new_version_with_old_content(client, owner):
    q = create(client, owner)
    new_version(client, owner, q["id"], 1, age(100))
    r = client.post(f"{API}/{q['id']}/versions/1/restore", headers=owner)
    assert r.status_code == 201
    v3 = r.json()
    assert (v3["version"], v3["parentVersion"], v3["kind"], v3["note"]) == (3, 1, "restore", "恢复自 v1")
    assert v3["content"] == q["content"]
    assert len(history(client, owner, q["id"])) == 3   # 历史没有被改写


def test_restore_same_as_latest_rejected(client, owner):
    q = create(client, owner)
    r = client.post(f"{API}/{q['id']}/versions/1/restore", headers=owner)
    assert r.status_code == 409 and "不需要恢复" in r.json()["detail"]


# ---------- 共享后的权限 ----------

def test_shared_user_can_view_but_not_modify(client, db, owner, other):
    q = create(client, owner)
    new_version(client, owner, q["id"], 1, age(100))
    share(client, owner, q["id"], "student_01")

    shared = client.get(f"{API}?scope=shared", headers=other).json()
    assert [s["id"] for s in shared] == [q["id"]] and shared[0]["isOwner"] is False
    assert client.get(f"{API}/{q['id']}/versions/1", headers=other).status_code == 200
    assert new_version(client, other, q["id"], 2, age(90)).status_code == 403
    assert client.post(f"{API}/{q['id']}/versions/1/restore", headers=other).status_code == 403
    assert client.delete(f"{API}/{q['id']}", headers=other).status_code == 403


# ---------- 复制为我的题 ----------

def test_fork_shared_question(client, db, owner, other):
    q = create(client, owner, GRADE)
    share(client, owner, q["id"], "student_01")
    r = client.post(f"{API}/{q['id']}/fork", headers=other, json={"version": 1})
    assert r.status_code == 201
    mine = r.json()
    assert mine["id"] != q["id"] and mine["isOwner"] is True and mine["owner"] == "小明（student_01）"
    assert mine["derivedFrom"] == {"questionId": q["id"], "versionId": q["latest"]["versionId"], "version": 1}
    assert mine["content"] == q["content"]   # 选项 id 也原样保留
    [v1] = history(client, other, mine["id"])
    assert (v1["kind"], v1["note"]) == ("fork", "复制自「你的年级」v1")
    # 复制出的题归自己，可以继续建版本；原题不受影响
    content = {**GRADE, "options": [{"label": "大一"}, {"label": "大二"}]}
    assert new_version(client, other, mine["id"], 1, content).status_code == 201
    assert client.get(f"{API}/{q['id']}", headers=owner).json()["latest"]["version"] == 1


def test_fork_long_title_note_is_shortened(client, owner):
    """自动生成的说明引用标题时截短：标题最长 500 字，说明最长 200 字。"""
    q = create(client, owner, {**AGE, "title": "长" * 300})
    r = client.post(f"{API}/{q['id']}/fork", headers=owner)
    assert r.status_code == 201, r.text
    [v] = history(client, owner, r.json()["id"])
    assert v["note"] == f"复制自「{'长' * lib.NOTE_TITLE_CHARS}…」v1"


def test_fork_defaults_to_latest_and_owner_can_fork(client, owner):
    q = create(client, owner)
    new_version(client, owner, q["id"], 1, age(100))
    copy = client.post(f"{API}/{q['id']}/fork", headers=owner).json()
    assert copy["derivedFrom"]["version"] == 2 and copy["latest"]["version"] == 1


def test_cannot_fork_invisible_question(client, owner, other):
    q = create(client, owner)
    assert client.post(f"{API}/{q['id']}/fork", headers=other).status_code == 404


def test_failed_question_insert_removes_version(client, db, owner, monkeypatch):
    """先插版本、再插题目；题目插入失败时补偿删除刚插的版本，不留下孤立的版本。"""
    original = Collection.insert_one

    def fake(self, *args, **kwargs):
        if self.name == "questions":
            raise RuntimeError("模拟写入失败")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Collection, "insert_one", fake)
    uid = db.users.find_one({"username": "teacher_01"})["_id"]
    data = {"type": "text_input", "title": "建议", "required": False, "validation": {}}
    with pytest.raises(RuntimeError):
        lib.create_question(db, uid, data, "")
    assert db.question_versions.count_documents({}) == 0


# ---------- 删除 ----------

def test_delete_is_soft_and_hides_question(client, db, owner, other):
    q = create(client, owner)
    share(client, owner, q["id"], "student_01")
    assert client.delete(f"{API}/{q['id']}", headers=owner).status_code == 204
    assert client.get(f"{API}/{q['id']}", headers=owner).status_code == 404
    assert client.get(API, headers=owner).json() == []
    assert client.get(f"{API}?scope=shared", headers=other).json() == []
    assert db.question_versions.count_documents({"questionId": ObjectId(q["id"])}) == 1   # 版本保留
    assert client.delete(f"{API}/{q['id']}", headers=owner).status_code == 404


def test_list_uses_owner_index(client, db, owner):
    create(client, owner)
    uid = db.users.find_one({"username": "teacher_01"})["_id"]
    plan = str(db.questions.find({"ownerId": uid, "deletedAt": None}).sort("updatedAt", -1).explain())
    assert "owner_updatedAt" in plan and "COLLSCAN" not in plan
