"""问卷 ↔ 题库（阶段二第 4 段）：从题库选题、修改时脱钩或存为新版本、切换版本、存入题库。"""

import pytest
from bson import ObjectId

from app.services import library as lib

SAPI = "/api/surveys"
QAPI = "/api/questions"

GRADE = {"type": "single_choice", "title": "你的年级", "required": True,
         "options": [{"label": "大一"}, {"label": "大二"}, {"label": "大三"}]}
AGE = {"type": "number_input", "title": "你的年龄", "required": False,
       "validation": {"min": 0, "max": 120, "integer": True}}


@pytest.fixture()
def teacher(login_as):
    return login_as("teacher_01", "王老师")


@pytest.fixture()
def student(login_as):
    return login_as("student_01", "小明")


def new_lib_question(client, headers, content=GRADE):
    r = client.post(QAPI, headers=headers, json={"content": content})
    assert r.status_code == 201, r.text
    return r.json()


def new_survey(client, headers, title="课程调查"):
    return client.post(SAPI, headers=headers, json={"title": title}).json()["id"]


def add_from_library(client, headers, sid, qid, **body):
    return client.post(f"{SAPI}/{sid}/questions/from-library", headers=headers, json={"questionId": qid, **body})


def survey_questions(client, headers, sid):
    return client.get(f"{SAPI}/{sid}", headers=headers).json()["questions"]


def edit(client, headers, sid, qid, content, action=None):
    url = f"{SAPI}/{sid}/questions/{qid}" + (f"?libraryAction={action}" if action else "")
    return client.put(url, headers=headers, json=content)


def with_ids(content, survey_question, labels):
    """按顺序沿用问卷里这道题的选项 id，多出来的标签作为新选项。"""
    old = survey_question["options"]
    options = [{"id": old[i]["id"], "label": label} if i < len(old) else {"label": label}
               for i, label in enumerate(labels)]
    return {**content, "options": options}


def publish(client, headers, sid):
    r = client.post(f"{SAPI}/{sid}/publish", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["slug"]


# ---------- 从题库加入 ----------

def test_add_from_library_makes_snapshot_with_source(client, teacher):
    lq = new_lib_question(client, teacher)
    sid = new_survey(client, teacher)
    r = add_from_library(client, teacher, sid, lq["id"])
    assert r.status_code == 201
    [q] = r.json()["questions"]
    assert q["id"].startswith("q_")                                     # 本问卷自己的题目 id
    assert q["options"] == lq["content"]["options"]                     # 内容与选项 id 原样复制
    assert q["title"] == "你的年级" and q["required"] is True and q["jumpRules"] == []
    assert q["source"] == {"questionId": lq["id"], "versionId": lq["latest"]["versionId"], "version": 1}
    assert q["library"] == {"available": True, "latestVersion": 1, "isLatest": True, "canEdit": True}


def test_add_specific_version_and_override_required(client, teacher):
    lq = new_lib_question(client, teacher, AGE)
    client.post(f"{QAPI}/{lq['id']}/versions", headers=teacher,
                json={"baseVersion": 1, "content": {**AGE, "title": "你的周岁年龄"}})
    sid = new_survey(client, teacher)
    [q] = add_from_library(client, teacher, sid, lq["id"], version=1, required=True).json()["questions"]
    assert q["source"]["version"] == 1 and q["title"] == "你的年龄"
    assert q["required"] is True                                         # 版本里是选填，本问卷设为必答
    assert q["library"] == {"available": True, "latestVersion": 2, "isLatest": False, "canEdit": True}


def test_add_invisible_or_duplicate_rejected(client, teacher, student):
    lq = new_lib_question(client, teacher)
    sid = new_survey(client, student)
    assert add_from_library(client, student, sid, lq["id"]).status_code == 404

    mine = new_survey(client, teacher)
    add_from_library(client, teacher, mine, lq["id"])
    r = add_from_library(client, teacher, mine, lq["id"])
    assert r.status_code == 409 and "已在问卷中" in r.json()["detail"]


def test_shared_question_can_be_used(client, teacher, student):
    lq = new_lib_question(client, teacher)
    client.post(f"{QAPI}/{lq['id']}/shares", headers=teacher, json={"username": "student_01"})
    sid = new_survey(client, student)
    r = add_from_library(client, student, sid, lq["id"])
    assert r.status_code == 201
    assert r.json()["questions"][0]["library"]["canEdit"] is False   # 不是创建者：不能存为新版本


def test_published_survey_cannot_add(client, teacher):
    lq = new_lib_question(client, teacher)
    sid = new_survey(client, teacher)
    add_from_library(client, teacher, sid, lq["id"])
    publish(client, teacher, sid)
    other = new_lib_question(client, teacher, AGE)
    assert add_from_library(client, teacher, sid, other["id"]).status_code == 409


def test_library_question_filled_and_counted(client, teacher, student):
    """填写、提交、统计只看快照，和阶段一的题完全一样。"""
    lq = new_lib_question(client, teacher)
    sid = new_survey(client, teacher)
    [q] = add_from_library(client, teacher, sid, lq["id"]).json()["questions"]
    slug = publish(client, teacher, sid)

    view = client.get(f"/api/fill/{slug}", headers=student).json()
    assert "source" not in view["questions"][0]                         # 填写者看不到题库来源
    second = q["options"][1]["id"]
    r = client.post(f"/api/fill/{slug}/responses", headers=student, json={"answers": {q["id"]: second}})
    assert r.status_code == 201
    stats = client.get(f"{SAPI}/{sid}/stats", headers=teacher).json()
    assert {o["label"]: o["count"] for o in stats["questions"][0]["options"]} == {"大一": 0, "大二": 1, "大三": 0}


# ---------- 修改来自题库的题 ----------

def test_changing_required_only_keeps_source(client, teacher):
    lq = new_lib_question(client, teacher)
    sid = new_survey(client, teacher)
    [q] = add_from_library(client, teacher, sid, lq["id"]).json()["questions"]
    r = edit(client, teacher, sid, q["id"], with_ids({**GRADE, "required": False}, q, ["大一", "大二", "大三"]))
    assert r.status_code == 200
    [after] = r.json()["questions"]
    assert after["required"] is False and after["source"] == q["source"]


def test_content_change_requires_choice(client, teacher):
    lq = new_lib_question(client, teacher)
    sid = new_survey(client, teacher)
    [q] = add_from_library(client, teacher, sid, lq["id"]).json()["questions"]
    r = edit(client, teacher, sid, q["id"], with_ids(GRADE, q, ["大一", "大二", "大三", "大四"]))
    assert r.status_code == 409 and "libraryAction=detach" in r.json()["detail"]
    assert survey_questions(client, teacher, sid)[0]["source"] == q["source"]   # 没有被改


def test_detach_changes_only_this_survey(client, teacher):
    lq = new_lib_question(client, teacher)
    sid = new_survey(client, teacher)
    [q] = add_from_library(client, teacher, sid, lq["id"]).json()["questions"]
    r = edit(client, teacher, sid, q["id"], with_ids(GRADE, q, ["大一", "大二", "大三", "大四"]), "detach")
    assert r.status_code == 200
    [after] = r.json()["questions"]
    assert "source" not in after and after["derivedFrom"] == q["source"]
    assert len(after["options"]) == 4
    assert client.get(f"{QAPI}/{lq['id']}", headers=teacher).json()["latest"]["version"] == 1   # 题库不变
    # 脱钩后再改，仍记着最初改自哪个版本
    [again] = edit(client, teacher, sid, q["id"], {**GRADE, "title": "年级", "options": after["options"]}).json()[
        "questions"]
    assert again["derivedFrom"] == q["source"]


def test_new_version_from_survey(client, teacher):
    lq = new_lib_question(client, teacher)
    sid = new_survey(client, teacher, "期中调查")
    [q] = add_from_library(client, teacher, sid, lq["id"]).json()["questions"]
    rules = [{"when": {"op": "eq", "value": q["options"][0]["id"]}, "goto": "END"}]
    client.put(f"{SAPI}/{sid}/questions/{q['id']}/jump-rules", headers=teacher, json={"rules": rules})

    r = edit(client, teacher, sid, q["id"], with_ids(GRADE, q, ["大一", "大二", "大三", "研究生"]), "new_version")
    assert r.status_code == 200
    [after] = r.json()["questions"]
    assert after["id"] == q["id"] and after["source"]["version"] == 2
    assert after["jumpRules"] == rules                                  # 跳转规则保留
    assert after["options"][:3] == q["options"]                         # 原有选项 id 不变
    versions = client.get(f"{QAPI}/{lq['id']}/versions", headers=teacher).json()
    assert (versions[1]["parentVersion"], versions[1]["note"]) == (1, "在问卷「期中调查」中修改")


def test_new_version_rejected_by_jump_rules_leaves_library_unchanged(client, teacher):
    """改后会让本题跳转规则失效：问卷不改，题库里也不留下新版本。"""
    lq = new_lib_question(client, teacher)
    sid = new_survey(client, teacher)
    [q] = add_from_library(client, teacher, sid, lq["id"]).json()["questions"]
    third = q["options"][2]["id"]
    client.put(f"{SAPI}/{sid}/questions/{q['id']}/jump-rules", headers=teacher,
               json={"rules": [{"when": {"op": "eq", "value": third}, "goto": "END"}]})
    r = edit(client, teacher, sid, q["id"], with_ids(GRADE, q, ["大一", "大二"]), "new_version")   # 删掉"大三"
    assert r.status_code == 409 and "不存在的选项" in r.json()["detail"]
    versions = client.get(f"{QAPI}/{lq['id']}/versions", headers=teacher).json()
    assert [v["version"] for v in versions] == [1]
    assert survey_questions(client, teacher, sid)[0]["source"]["version"] == 1


def test_new_version_from_long_titled_survey(client, teacher):
    lq = new_lib_question(client, teacher)
    sid = new_survey(client, teacher, title="问" * 200)
    [q] = add_from_library(client, teacher, sid, lq["id"]).json()["questions"]
    r = edit(client, teacher, sid, q["id"], {**with_ids(GRADE, q, ["大一", "大二", "大三"]), "title": "你现在的年级"},
             "new_version")
    assert r.status_code == 200, r.text
    v2 = client.get(f"{QAPI}/{lq['id']}/versions", headers=teacher).json()[-1]
    assert v2["note"] == f"在问卷「{'问' * lib.NOTE_TITLE_CHARS}…」中修改" and len(v2["note"]) <= 200


def test_new_version_needs_library_ownership(client, teacher, student):
    lq = new_lib_question(client, teacher)
    client.post(f"{QAPI}/{lq['id']}/shares", headers=teacher, json={"username": "student_01"})
    sid = new_survey(client, student)
    [q] = add_from_library(client, student, sid, lq["id"]).json()["questions"]
    r = edit(client, student, sid, q["id"], with_ids(GRADE, q, ["大一", "大二"]), "new_version")
    assert r.status_code == 403 and "只改本问卷" in r.json()["detail"]
    assert survey_questions(client, student, sid)[0]["source"] == q["source"]
    # 被共享者可以选择脱钩
    assert edit(client, student, sid, q["id"], with_ids(GRADE, q, ["大一", "大二"]), "detach").status_code == 200


def test_new_version_cannot_change_type(client, teacher):
    lq = new_lib_question(client, teacher, AGE)
    sid = new_survey(client, teacher)
    [q] = add_from_library(client, teacher, sid, lq["id"]).json()["questions"]
    text = {"type": "text_input", "title": "你的年龄", "required": False}
    assert edit(client, teacher, sid, q["id"], text, "new_version").status_code == 422
    assert edit(client, teacher, sid, q["id"], text, "detach").status_code == 200   # 只改本问卷可以换题型


# ---------- 切换版本 ----------

def test_switch_version_keeps_survey_settings(client, teacher):
    lq = new_lib_question(client, teacher, AGE)
    sid = new_survey(client, teacher)
    [q] = add_from_library(client, teacher, sid, lq["id"], required=True).json()["questions"]
    rules = [{"when": {"op": "lt", "value": 18}, "goto": "END"}]
    client.put(f"{SAPI}/{sid}/questions/{q['id']}/jump-rules", headers=teacher, json={"rules": rules})
    client.post(f"{QAPI}/{lq['id']}/versions", headers=teacher,
                json={"baseVersion": 1, "content": {**AGE, "validation": {"min": 0, "max": 150, "integer": True}}})

    r = client.put(f"{SAPI}/{sid}/questions/{q['id']}/source", headers=teacher, json={"version": 2})
    assert r.status_code == 200
    [after] = r.json()["questions"]
    assert after["source"]["version"] == 2 and after["validation"]["max"] == 150
    assert (after["id"], after["required"], after["jumpRules"]) == (q["id"], True, rules)
    assert after["library"]["isLatest"] is True
    # 也可以换回旧版本
    r = client.put(f"{SAPI}/{sid}/questions/{q['id']}/source", headers=teacher, json={"version": 1})
    assert r.json()["questions"][0]["validation"]["max"] == 120


def test_switch_blocked_when_jump_rule_option_removed(client, teacher):
    lq = new_lib_question(client, teacher)
    sid = new_survey(client, teacher)
    [q] = add_from_library(client, teacher, sid, lq["id"]).json()["questions"]
    third = q["options"][2]["id"]
    client.put(f"{SAPI}/{sid}/questions/{q['id']}/jump-rules", headers=teacher,
               json={"rules": [{"when": {"op": "eq", "value": third}, "goto": "END"}]})
    client.post(f"{QAPI}/{lq['id']}/versions", headers=teacher, json={   # v2 删掉了"大三"
        "baseVersion": 1, "content": {**GRADE, "options": lq["content"]["options"][:2]}})
    r = client.put(f"{SAPI}/{sid}/questions/{q['id']}/source", headers=teacher, json={"version": 2})
    assert r.status_code == 409 and "不存在的选项" in r.json()["detail"]
    assert survey_questions(client, teacher, sid)[0]["source"]["version"] == 1


def test_unshared_question_keeps_working_in_survey(client, teacher, student):
    lq = new_lib_question(client, teacher)
    client.post(f"{QAPI}/{lq['id']}/shares", headers=teacher, json={"username": "student_01"})
    sid = new_survey(client, student)
    [q] = add_from_library(client, student, sid, lq["id"]).json()["questions"]
    client.delete(f"{QAPI}/{lq['id']}/shares/student_01", headers=teacher)

    assert survey_questions(client, student, sid)[0]["library"] == {"available": False}
    r = client.put(f"{SAPI}/{sid}/questions/{q['id']}/source", headers=student, json={"version": 1})
    assert r.status_code == 409 and "不再共享" in r.json()["detail"]
    slug = publish(client, student, sid)                                 # 快照不受影响，照常发布、填写
    r = client.post(f"/api/fill/{slug}/responses", headers=student,
                    json={"answers": {q["id"]: q["options"][0]["id"]}})
    assert r.status_code == 201


# ---------- 存入题库 ----------

def test_save_draft_question_to_library(client, teacher):
    sid = new_survey(client, teacher)
    client.post(f"{SAPI}/{sid}/questions", headers=teacher, json=GRADE)
    [q] = survey_questions(client, teacher, sid)
    r = client.post(f"{SAPI}/{sid}/questions/{q['id']}/save-to-library", headers=teacher, json={"note": "常用"})
    assert r.status_code == 200
    [after] = r.json()["questions"]
    lq = client.get(f"{QAPI}/{after['source']['questionId']}", headers=teacher).json()
    assert lq["content"]["options"] == q["options"] and lq["isOwner"] is True
    assert after["library"] == {"available": True, "latestVersion": 1, "isLatest": True, "canEdit": True}
    r = client.post(f"{SAPI}/{sid}/questions/{q['id']}/save-to-library", headers=teacher)
    assert r.status_code == 409 and "已关联题库" in r.json()["detail"]


def test_save_published_survey_question_without_changing_content(client, db, teacher, student):
    """①A：已发布问卷（包括阶段一的旧问卷）也能把题存入题库，内容一个字不改，填写照常。"""
    sid = new_survey(client, teacher)
    client.post(f"{SAPI}/{sid}/questions", headers=teacher, json=GRADE)
    slug = publish(client, teacher, sid)
    before = db.surveys.find_one({"_id": ObjectId(sid)})
    q = before["questions"][0]

    r = client.post(f"{SAPI}/{sid}/questions/{q['id']}/save-to-library", headers=teacher)
    assert r.status_code == 200
    after = db.surveys.find_one({"_id": ObjectId(sid)})
    assert {k: v for k, v in after["questions"][0].items() if k != "source"} == q
    assert after["status"] == "published" and after["revision"] == before["revision"] + 1
    assert client.post(f"/api/fill/{slug}/responses", headers=student,
                       json={"answers": {q["id"]: q["options"][0]["id"]}}).status_code == 201
    # 发布后的锁定不变：仍然不能改题
    assert edit(client, teacher, sid, q["id"], GRADE, "detach").status_code == 409


def test_save_detached_question_records_origin(client, teacher):
    lq = new_lib_question(client, teacher)
    sid = new_survey(client, teacher)
    [q] = add_from_library(client, teacher, sid, lq["id"]).json()["questions"]
    edit(client, teacher, sid, q["id"], with_ids(GRADE, q, ["大一", "大二"]), "detach")
    [after] = client.post(f"{SAPI}/{sid}/questions/{q['id']}/save-to-library", headers=teacher).json()["questions"]
    new_id = after["source"]["questionId"]
    assert new_id != lq["id"] and "derivedFrom" in after                 # 问卷里仍记着改自哪个版本
    saved = client.get(f"{QAPI}/{new_id}", headers=teacher).json()
    assert saved["derivedFrom"] == q["source"]
    assert client.get(f"{QAPI}/{new_id}/versions", headers=teacher).json()[0]["kind"] == "fork"


def test_save_rolls_back_when_survey_changed_meanwhile(client, db, teacher, monkeypatch):
    """存入题库要写两个集合：建好题库题后发现问卷被改过（revision 变了），撤回刚建的题。"""
    sid = new_survey(client, teacher)
    client.post(f"{SAPI}/{sid}/questions", headers=teacher, json=GRADE)
    [q] = survey_questions(client, teacher, sid)
    original = lib.insert_question

    def insert_then_survey_changes(*args, **kwargs):
        created = original(*args, **kwargs)
        db.surveys.update_one({"_id": ObjectId(sid)}, {"$inc": {"revision": 1}})   # 模拟中途有别的修改
        return created

    monkeypatch.setattr(lib, "insert_question", insert_then_survey_changes)
    r = client.post(f"{SAPI}/{sid}/questions/{q['id']}/save-to-library", headers=teacher)
    assert r.status_code == 409
    assert db.questions.count_documents({}) == 0 and db.question_versions.count_documents({}) == 0
    assert "source" not in survey_questions(client, teacher, sid)[0]


def test_deleted_library_question_does_not_affect_survey(client, teacher, student):
    lq = new_lib_question(client, teacher)
    sid = new_survey(client, teacher)
    [q] = add_from_library(client, teacher, sid, lq["id"]).json()["questions"]
    client.delete(f"{QAPI}/{lq['id']}", headers=teacher)
    assert survey_questions(client, teacher, sid)[0]["library"] == {"available": False}
    slug = publish(client, teacher, sid)
    assert client.post(f"/api/fill/{slug}/responses", headers=student,
                       json={"answers": {q["id"]: q["options"][0]["id"]}}).status_code == 201
