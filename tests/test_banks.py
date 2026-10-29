"""共享与题库（阶段二第 3 段）。

人物：王老师（teacher_01）创建题目和题库；助教小李（ta_01）；学生小明（student_01）。
"""

import threading

import pytest
from bson import ObjectId

from app.services import banks as bsvc
from app.services import library as lib
from db.schema import MAX_BANK_ITEMS, MAX_SHARED_USERS

QAPI = "/api/questions"
BAPI = "/api/banks"

AGE = {"type": "number_input", "title": "你的年龄", "required": True,
       "validation": {"min": 0, "max": 120, "integer": True}}
GENDER = {"type": "single_choice", "title": "你的性别", "required": True,
          "options": [{"label": "男"}, {"label": "女"}]}


@pytest.fixture()
def teacher(login_as):
    return login_as("teacher_01", "王老师")


@pytest.fixture()
def ta(login_as):
    return login_as("ta_01", "小李")


@pytest.fixture()
def student(login_as):
    return login_as("student_01", "小明")


def uid(db, username):
    return db.users.find_one({"username": username})["_id"]


def new_question(client, headers, content=AGE):
    r = client.post(QAPI, headers=headers, json={"content": content})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def new_bank(client, headers, name="常用人口学题目"):
    r = client.post(BAPI, headers=headers, json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def add_item(client, headers, bank_id, question_id):
    return client.post(f"{BAPI}/{bank_id}/items", headers=headers, json={"questionId": question_id})


def share_question(client, headers, qid, username):
    return client.post(f"{QAPI}/{qid}/shares", headers=headers, json={"username": username})


def share_bank(client, headers, bank_id, username):
    return client.post(f"{BAPI}/{bank_id}/shares", headers=headers, json={"username": username})


def can_see(client, headers, qid):
    return client.get(f"{QAPI}/{qid}", headers=headers).status_code == 200


# ---------- 题目共享 ----------

def test_share_question(client, teacher, student):
    qid = new_question(client, teacher)
    r = share_question(client, teacher, qid, "student_01")
    assert r.status_code == 200
    assert r.json()["sharedWith"] == [{"username": "student_01", "displayName": "小明"}]
    # 被共享者看得到题，但看不到共享名单
    view = client.get(f"{QAPI}/{qid}", headers=student).json()
    assert view["isOwner"] is False and view["sharedWith"] is None
    assert [q["id"] for q in client.get(f"{QAPI}?scope=shared", headers=student).json()] == [qid]


def test_share_twice_is_idempotent(client, teacher, student):
    qid = new_question(client, teacher)
    share_question(client, teacher, qid, "student_01")
    assert len(share_question(client, teacher, qid, "student_01").json()["sharedWith"]) == 1


def test_share_errors(client, teacher, student):
    qid = new_question(client, teacher)
    r = share_question(client, teacher, qid, "nobody_99")
    assert r.status_code == 404 and r.json()["detail"] == "账号不存在"
    r = share_question(client, teacher, qid, "teacher_01")
    assert r.status_code == 422 and r.json()["detail"] == "不能共享给自己"
    share_question(client, teacher, qid, "student_01")
    assert share_question(client, student, qid, "teacher_01").status_code == 403   # 被共享者不能再共享


def test_unshare_removes_access_but_keeps_forks(client, teacher, student):
    qid = new_question(client, teacher)
    share_question(client, teacher, qid, "student_01")
    fork = client.post(f"{QAPI}/{qid}/fork", headers=student).json()
    r = client.delete(f"{QAPI}/{qid}/shares/student_01", headers=teacher)
    assert r.status_code == 200 and r.json()["sharedWith"] == []
    assert not can_see(client, student, qid)
    assert client.get(f"{QAPI}?scope=shared", headers=student).json() == []
    # 之前复制出来的题已经归小明，不受影响，来源记录仍在
    mine = client.get(f"{QAPI}/{fork['id']}", headers=student).json()
    assert mine["derivedFrom"]["questionId"] == qid


def test_share_limit_holds_under_concurrency(client, db, teacher, login_as):
    """已共享 99 人时 5 个请求同时共享给不同的人：上限写在过滤条件里，只有一个成功。"""
    qid = new_question(client, teacher)
    db.questions.update_one({"_id": ObjectId(qid)},
                            {"$set": {"sharedWith": [ObjectId() for _ in range(MAX_SHARED_USERS - 1)]}})
    names = [f"member_{i:02d}" for i in range(5)]
    for name in names:
        login_as(name)
    owner_id = uid(db, "teacher_01")
    barrier = threading.Barrier(5)
    ok, full = [], []

    def worker(name):
        barrier.wait()
        try:
            lib.share_question(db, owner_id, qid, name)
            ok.append(name)
        except lib.LibraryConflict:
            full.append(name)

    threads = [threading.Thread(target=worker, args=(n,)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ok) == 1 and len(full) == 4
    assert len(db.questions.find_one({"_id": ObjectId(qid)})["sharedWith"]) == MAX_SHARED_USERS


# ---------- 题库：增删改查 ----------

def test_create_list_update_delete_bank(client, teacher, student):
    bid = new_bank(client, teacher)
    detail = client.get(f"{BAPI}/{bid}", headers=teacher).json()
    assert (detail["name"], detail["itemCount"], detail["items"], detail["isOwner"]) == \
           ("常用人口学题目", 0, [], True)
    assert detail["owner"] == "王老师（teacher_01）" and detail["sharedWith"] == []

    r = client.patch(f"{BAPI}/{bid}", headers=teacher, json={"description": "年龄、性别等"})
    assert r.status_code == 200 and r.json()["name"] == "常用人口学题目" and r.json()["description"] == "年龄、性别等"
    assert [b["id"] for b in client.get(BAPI, headers=teacher).json()] == [bid]
    assert client.get(BAPI, headers=student).json() == []
    assert client.get(f"{BAPI}/{bid}", headers=student).status_code == 404

    assert client.delete(f"{BAPI}/{bid}", headers=teacher).status_code == 204
    assert client.get(f"{BAPI}/{bid}", headers=teacher).status_code == 404
    assert client.get(BAPI, headers=teacher).json() == []


def test_bank_name_checked(client, teacher):
    r = client.post(BAPI, headers=teacher, json={"name": "   "})
    assert r.status_code == 422 and "题库名称" in r.json()["detail"][0]["msg"]


def test_add_and_remove_items(client, teacher):
    bid = new_bank(client, teacher)
    q1, q2 = new_question(client, teacher), new_question(client, teacher, GENDER)
    add_item(client, teacher, bid, q1)
    r = add_item(client, teacher, bid, q2)
    assert r.status_code == 200
    assert [i["id"] for i in r.json()["items"]] == [q1, q2]   # 按加入时间
    assert add_item(client, teacher, bid, q1).json()["itemCount"] == 2   # 重复加入视为成功
    r = client.delete(f"{BAPI}/{bid}/items/{q1}", headers=teacher)
    assert [i["id"] for i in r.json()["items"]] == [q2]
    assert client.delete(f"{BAPI}/{bid}/items/{q1}", headers=teacher).status_code == 404


def test_bank_shows_latest_version(client, teacher):
    """N7：题库里的题可以更新版本——题库引用题目，题目出了新版本，题库里显示的就是新版本。"""
    bid = new_bank(client, teacher)
    qid = new_question(client, teacher)
    add_item(client, teacher, bid, qid)
    client.post(f"{QAPI}/{qid}/versions", headers=teacher, json={
        "baseVersion": 1, "content": {**AGE, "title": "你的周岁年龄"}})
    [item] = client.get(f"{BAPI}/{bid}", headers=teacher).json()["items"]
    assert item["latest"]["version"] == 2 and item["latest"]["title"] == "你的周岁年龄"


def test_only_owner_can_modify_bank(client, teacher, student):
    bid = new_bank(client, teacher)
    share_bank(client, teacher, bid, "student_01")
    qid = new_question(client, student)
    assert add_item(client, student, bid, qid).status_code == 403
    assert client.patch(f"{BAPI}/{bid}", headers=student, json={"name": "改名"}).status_code == 403
    assert client.delete(f"{BAPI}/{bid}", headers=student).status_code == 403
    assert share_bank(client, student, bid, "ta_01").status_code == 403


def test_cannot_add_invisible_question(client, teacher, student):
    bid = new_bank(client, teacher)
    qid = new_question(client, student)
    assert add_item(client, teacher, bid, qid).status_code == 404


# ---------- 题库共享与可见性 ----------

def test_shared_bank_grants_access_to_its_questions(client, teacher, student):
    bid = new_bank(client, teacher)
    q_in, q_out = new_question(client, teacher), new_question(client, teacher, GENDER)
    add_item(client, teacher, bid, q_in)
    assert not can_see(client, student, q_in)

    share_bank(client, teacher, bid, "student_01")
    shared = client.get(f"{BAPI}?scope=shared", headers=student).json()
    assert [b["id"] for b in shared] == [bid] and shared[0]["isOwner"] is False
    detail = client.get(f"{BAPI}/{bid}", headers=student).json()
    assert [i["id"] for i in detail["items"]] == [q_in] and detail["sharedWith"] is None

    assert can_see(client, student, q_in)
    assert client.get(f"{QAPI}/{q_in}/versions", headers=student).status_code == 200
    assert client.post(f"{QAPI}/{q_in}/fork", headers=student).status_code == 201
    assert not can_see(client, student, q_out)        # 不在题库里的题看不到
    # ③A："共享给我的题目"只列直接共享的题，题库里的题在题库里看
    assert client.get(f"{QAPI}?scope=shared", headers=student).json() == []


@pytest.mark.parametrize("revoke", ["unshare", "delete_bank", "remove_item"])
def test_access_via_bank_ends_when_revoked(client, teacher, student, revoke):
    bid = new_bank(client, teacher)
    qid = new_question(client, teacher)
    add_item(client, teacher, bid, qid)
    share_bank(client, teacher, bid, "student_01")
    assert can_see(client, student, qid)
    if revoke == "unshare":
        client.delete(f"{BAPI}/{bid}/shares/student_01", headers=teacher)
    elif revoke == "delete_bank":
        client.delete(f"{BAPI}/{bid}", headers=teacher)
    else:
        client.delete(f"{BAPI}/{bid}/items/{qid}", headers=teacher)
    assert not can_see(client, student, qid)


def test_no_resharing_through_banks(client, teacher, ta, student):
    """②A：王老师只把题共享给小李；小李不能把它放进自己的题库再转给小明，要先复制为自己的题。"""
    original = new_question(client, teacher)
    share_question(client, teacher, original, "ta_01")
    bid = new_bank(client, ta, "助教的题库")
    share_bank(client, ta, bid, "student_01")

    r = add_item(client, ta, bid, original)
    assert r.status_code == 409 and "先复制为自己的题" in r.json()["detail"]

    copy = client.post(f"{QAPI}/{original}/fork", headers=ta).json()["id"]
    assert add_item(client, ta, bid, copy).status_code == 200
    assert can_see(client, student, copy)              # 小明看到的是小李的副本
    assert not can_see(client, student, original)      # 王老师的原题依然看不到


def test_deleting_question_removes_it_from_banks(client, db, teacher):
    b1, b2 = new_bank(client, teacher, "题库一"), new_bank(client, teacher, "题库二")
    qid = new_question(client, teacher)
    add_item(client, teacher, b1, qid)
    add_item(client, teacher, b2, qid)
    client.delete(f"{QAPI}/{qid}", headers=teacher)
    for bid in (b1, b2):
        assert db.question_banks.find_one({"_id": ObjectId(bid)})["items"] == []


def test_bank_detail_skips_deleted_question(client, db, teacher):
    """兜底：即使删除题目后没来得及从题库移除，详情页也不显示已删除的题。"""
    bid = new_bank(client, teacher)
    qid = new_question(client, teacher)
    add_item(client, teacher, bid, qid)
    db.questions.update_one({"_id": ObjectId(qid)}, {"$set": {"deletedAt": db.questions.find_one()["createdAt"]}})
    detail = client.get(f"{BAPI}/{bid}", headers=teacher).json()
    assert detail["items"] == [] and detail["itemCount"] == 0


def test_bank_item_limit_holds_under_concurrency(client, db, teacher):
    bid = new_bank(client, teacher)
    filler = [{"questionId": ObjectId(), "addedAt": db.question_banks.find_one()["createdAt"]}
              for _ in range(MAX_BANK_ITEMS - 1)]
    db.question_banks.update_one({"_id": ObjectId(bid)}, {"$set": {"items": filler}})
    qids = [new_question(client, teacher) for _ in range(3)]
    owner_id = uid(db, "teacher_01")
    barrier = threading.Barrier(3)
    ok, full = [], []

    def worker(qid):
        barrier.wait()
        try:
            bsvc.add_item(db, owner_id, bid, qid)
            ok.append(qid)
        except lib.LibraryConflict:
            full.append(qid)

    threads = [threading.Thread(target=worker, args=(q,)) for q in qids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ok) == 1 and len(full) == 2
    assert len(db.question_banks.find_one({"_id": ObjectId(bid)})["items"]) == MAX_BANK_ITEMS


def test_bank_access_check_uses_index(client, db, teacher, student):
    bid = new_bank(client, teacher)
    qid = new_question(client, teacher)
    add_item(client, teacher, bid, qid)
    query = {"sharedWith": uid(db, "student_01"), "deletedAt": None, "items.questionId": ObjectId(qid)}
    plan = str(db.question_banks.find(query).explain())
    assert "shared_updatedAt" in plan and "COLLSCAN" not in plan


def test_my_banks_list_uses_owner_index(client, db, teacher):
    new_bank(client, teacher)
    plan = str(db.question_banks.find({"ownerId": uid(db, "teacher_01"), "deletedAt": None})
               .sort("updatedAt", -1).explain())
    assert "owner_updatedAt" in plan and "COLLSCAN" not in plan
