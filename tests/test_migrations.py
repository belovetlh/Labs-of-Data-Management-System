"""结构迁移测试：阶段一的旧数据迁移后内容不变、旧功能照常可用，迁移可以重复执行。"""

from datetime import datetime, timezone

from bson import ObjectId

from db.init_db import init_db
from db.migrations import MIGRATIONS, migrate

EARLIER = datetime(2026, 10, 18, 8, 0, tzinfo=timezone.utc)
STEP = "002_surveys_question_source"


def stage1_survey(owner_id, **overrides):
    """阶段一写入的问卷：schemaVersion 1，题目没有 source 字段。"""
    doc = {
        "schemaVersion": 1,
        "revision": 4,
        "ownerId": owner_id,
        "title": "阶段一的问卷",
        "description": "",
        "slug": "Old1Srv9",
        "status": "published",
        "settings": {"allowAnonymous": False, "allowMultipleSubmissions": True, "deadline": None},
        "questions": [{
            "id": "q_a0000001", "type": "single_choice", "title": "你的性别", "required": True,
            "options": [{"id": "o_b0000001", "label": "男"}, {"id": "o_b0000002", "label": "女"}],
            "jumpRules": [],
        }],
        "createdAt": EARLIER,
        "updatedAt": EARLIER,
        "publishedAt": EARLIER,
        "closedAt": None,
    }
    doc.update(overrides)
    return doc


def test_step_names_unique_and_ordered():
    names = [name for name, _ in MIGRATIONS]
    assert names == sorted(set(names))


def test_stage1_survey_migrated_without_changing_content(db):
    sid = db.surveys.insert_one(stage1_survey(ObjectId())).inserted_id
    before = db.surveys.find_one({"_id": sid})
    db.migrations.delete_many({})  # 模拟阶段一的数据库：还没执行过任何迁移

    actions = init_db(db)

    after = db.surveys.find_one({"_id": sid})
    assert after["schemaVersion"] == 2
    # 除结构版本号外一个字段都不变：题目、revision（乐观锁）、updatedAt
    assert {k: v for k, v in after.items() if k != "schemaVersion"} == \
           {k: v for k, v in before.items() if k != "schemaVersion"}
    assert f"执行迁移 {STEP}：改动 1 个文档" in actions
    record = db.migrations.find_one({"_id": STEP})
    assert record["modified"] == 1


def test_migration_runs_only_once(db):
    db.migrations.delete_many({})
    migrate(db)
    # 已记录的步骤直接跳过：之后再出现的旧版本文档不会被它处理
    sid = db.surveys.insert_one(stage1_survey(ObjectId())).inserted_id
    assert migrate(db) == [f"迁移 {STEP} 已执行过"]
    assert db.surveys.find_one({"_id": sid})["schemaVersion"] == 1


def test_migration_step_is_rerunnable(db):
    # 中途失败（改了文档但没来得及记录）后重跑，结果相同
    db.surveys.insert_one(stage1_survey(ObjectId()))
    db.migrations.delete_many({})
    migrate(db)
    db.migrations.delete_many({})
    assert migrate(db) == [f"执行迁移 {STEP}：改动 0 个文档"]
    assert db.surveys.count_documents({"schemaVersion": 1}) == 0


def test_migrated_stage1_survey_still_fillable_and_counted(client, db, login_as):
    owner = login_as("owner_01")
    owner_id = db.users.find_one({"username": "owner_01"})["_id"]
    sid = db.surveys.insert_one(stage1_survey(owner_id)).inserted_id
    db.migrations.delete_many({})
    init_db(db)

    r = client.post("/api/fill/Old1Srv9/responses", headers=login_as("filler_01"),
                    json={"answers": {"q_a0000001": "o_b0000002"}})
    assert r.status_code == 201

    stats = client.get(f"/api/surveys/{sid}/stats", headers=owner).json()
    assert stats["totalResponses"] == 1
    counts = {o["label"]: o["count"] for o in stats["questions"][0]["options"]}
    assert counts == {"男": 0, "女": 1}
