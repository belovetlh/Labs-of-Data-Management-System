"""数据库初始化测试：集合、校验器、索引是否按设计生效。"""

import json
from datetime import datetime, timezone

import pytest
from bson import ObjectId
from pymongo.errors import DuplicateKeyError, WriteError

from db.init_db import init_db
from db.schema import INDEXES, SCHEMA_VERSIONS, VALIDATORS

NOW = datetime.now(timezone.utc)


def make_user(**overrides):
    doc = {
        "schemaVersion": SCHEMA_VERSIONS["users"],
        "username": "alice",
        "displayName": "爱丽丝",
        "passwordHash": "$2b$12$hash",
        "createdAt": NOW,
    }
    doc.update(overrides)
    return doc


def make_survey(**overrides):
    doc = {
        "schemaVersion": SCHEMA_VERSIONS["surveys"],
        "revision": 0,
        "ownerId": ObjectId(),
        "title": "大学生生活调查",
        "description": "",
        "slug": "k3X9pQ2a",
        "status": "draft",
        "settings": {"allowAnonymous": False, "allowMultipleSubmissions": True, "deadline": None},
        "questions": [
            {
                "id": "q_a0000001", "type": "single_choice", "title": "你的性别", "required": True,
                "options": [
                    {"id": "o_b0000001", "label": "男"},
                    {"id": "o_b0000002", "label": "女"},
                    {"id": "o_b0000003", "label": "其他"},
                ],
                "jumpRules": [{"when": {"op": "eq", "value": "o_b0000003"}, "goto": "q_a0000004"}],
            },
            {
                "id": "q_a0000002", "type": "multiple_choice", "title": "你喜欢的水果", "required": True,
                "options": [
                    {"id": "o_c0000001", "label": "苹果"},
                    {"id": "o_c0000002", "label": "香蕉"},
                ],
                "validation": {"minSelect": 1, "maxSelect": 2},
                "jumpRules": [{"when": {"op": "containsAll", "value": ["o_c0000001"]}, "goto": "END"}],
            },
            {
                "id": "q_a0000003", "type": "number_input", "title": "年龄", "required": True,
                "validation": {"min": 0, "max": 120, "integer": True},
                "jumpRules": [{"when": {"op": "lt", "value": 18}, "goto": "END"}],
            },
            {
                "id": "q_a0000004", "type": "text_input", "title": "你的建议", "required": False,
                "validation": {"minLength": 5, "maxLength": 200},
            },
        ],
        "createdAt": NOW,
        "updatedAt": NOW,
    }
    doc.update(overrides)
    return doc


def assert_rejected(collection, doc):
    with pytest.raises(WriteError) as exc:
        collection.insert_one(doc)
    assert exc.value.code == 121  # Document failed validation


# ---------- 初始化本身 ----------

def test_init_is_idempotent(db):
    # 会话开始时已执行过一次，再执行两次也不应报错
    init_db(db)
    actions = init_db(db)
    assert all("创建集合" not in a for a in actions)
    assert set(VALIDATORS) <= set(db.list_collection_names())


def test_collections_have_strict_validators(db):
    infos = {c["name"]: c for c in db.list_collections()}
    for name in VALIDATORS:
        opts = infos[name]["options"]
        assert "$jsonSchema" in opts["validator"]
        assert opts["validationLevel"] == "strict"
        assert opts["validationAction"] == "error"


def test_indexes_exist(db):
    for coll, keys, opts in INDEXES:
        info = db[coll].index_information()
        assert opts["name"] in info
        assert info[opts["name"]]["key"] == keys
        assert info[opts["name"]].get("unique", False) == opts.get("unique", False)


def test_init_keeps_existing_data(db):
    db.users.insert_one(make_user())
    init_db(db)
    assert db.users.count_documents({}) == 1


# ---------- users ----------

def test_valid_user_accepted(db):
    db.users.insert_one(make_user())


def test_user_missing_created_at_rejected(db):
    doc = make_user()
    del doc["createdAt"]
    assert_rejected(db.users, doc)


def test_user_short_username_rejected(db):
    assert_rejected(db.users, make_user(username="ab"))


def test_user_chinese_username_rejected(db):
    assert_rejected(db.users, make_user(username="张三三"))


def test_user_missing_display_name_rejected(db):
    doc = make_user()
    del doc["displayName"]
    assert_rejected(db.users, doc)


def test_same_display_name_allowed(db):
    db.users.insert_one(make_user(username="zhangsan01", displayName="张三"))
    db.users.insert_one(make_user(username="zhangsan02", displayName="张三"))


def test_duplicate_username_rejected(db):
    db.users.insert_one(make_user())
    with pytest.raises(DuplicateKeyError):
        db.users.insert_one(make_user())


# ---------- surveys ----------

def test_valid_survey_with_all_question_types_accepted(db):
    db.surveys.insert_one(make_survey())


def test_survey_missing_revision_rejected(db):
    doc = make_survey()
    del doc["revision"]
    assert_rejected(db.surveys, doc)


def test_survey_bad_status_rejected(db):
    assert_rejected(db.surveys, make_survey(status="open"))


def test_duplicate_slug_rejected(db):
    db.surveys.insert_one(make_survey())
    with pytest.raises(DuplicateKeyError):
        db.surveys.insert_one(make_survey())


def _with_question(index, **changes):
    survey = make_survey()
    survey["questions"][index] = {**survey["questions"][index], **changes}
    return survey


def test_single_choice_needs_two_options(db):
    assert_rejected(db.surveys, _with_question(0, options=[{"id": "o_b0000001", "label": "男"}]))


def test_jump_op_must_match_question_type(db):
    rule = [{"when": {"op": "containsAny", "value": ["o_b0000001"]}, "goto": "END"}]
    assert_rejected(db.surveys, _with_question(0, jumpRules=rule))


def test_text_question_cannot_have_jump_rules(db):
    rule = [{"when": {"op": "eq", "value": "x"}, "goto": "END"}]
    assert_rejected(db.surveys, _with_question(3, jumpRules=rule))


def test_jump_target_format_checked(db):
    rule = [{"when": {"op": "gt", "value": 5}, "goto": "question5"}]
    assert_rejected(db.surveys, _with_question(2, jumpRules=rule))


def test_unknown_question_type_rejected(db):
    assert_rejected(db.surveys, _with_question(3, type="rating"))


def test_number_rule_types_checked(db):
    assert_rejected(db.surveys, _with_question(2, validation={"min": "0"}))


# ---------- responses ----------

def test_named_and_anonymous_responses_accepted(db):
    survey_id = ObjectId()
    db.responses.insert_one({
        "schemaVersion": SCHEMA_VERSIONS["responses"], "surveyId": survey_id, "respondentId": ObjectId(),
        "submittedAt": NOW,
        "answers": [
            {"questionId": "q_a0000001", "value": "o_b0000001"},
            {"questionId": "q_a0000002", "value": ["o_c0000001", "o_c0000002"]},
            {"questionId": "q_a0000003", "value": 20},
        ],
    })
    db.responses.insert_one({  # 匿名：没有 respondentId
        "schemaVersion": SCHEMA_VERSIONS["responses"], "surveyId": survey_id, "submittedAt": NOW,
        "answers": [{"questionId": "q_a0000004", "value": "没有建议，谢谢"}],
    })


def test_phone_number_stored_as_int64(db):
    db.responses.insert_one({
        "schemaVersion": SCHEMA_VERSIONS["responses"], "surveyId": ObjectId(), "submittedAt": NOW,
        "answers": [{"questionId": "q_a0000003", "value": 13800138000}],
    })


def test_answer_value_object_rejected(db):
    assert_rejected(db.responses, {
        "schemaVersion": SCHEMA_VERSIONS["responses"], "surveyId": ObjectId(), "submittedAt": NOW,
        "answers": [{"questionId": "q_a0000001", "value": {"a": 1}}],
    })


# ---------- participations ----------

def test_duplicate_participation_rejected(db):
    doc = {"schemaVersion": SCHEMA_VERSIONS["participations"], "surveyId": ObjectId(), "userId": ObjectId(), "count": 1}
    db.participations.insert_one(dict(doc))
    with pytest.raises(DuplicateKeyError):
        db.participations.insert_one(dict(doc))


# ---------- 阶段二：questions / question_versions / question_banks ----------

def make_question(**overrides):
    doc = {
        "schemaVersion": SCHEMA_VERSIONS["questions"],
        "ownerId": ObjectId(),
        "latest": {"versionId": ObjectId(), "version": 1, "type": "number_input", "title": "你的年龄"},
        "derivedFrom": None,
        "sharedWith": [],
        "createdAt": NOW,
        "updatedAt": NOW,
    }
    doc.update(overrides)
    return doc


def make_version(**overrides):
    doc = {
        "schemaVersion": SCHEMA_VERSIONS["question_versions"],
        "questionId": ObjectId(),
        "version": 1,
        "parentVersionId": None,
        "kind": "create",
        "content": {
            "type": "single_choice", "title": "你的年级", "required": True,
            "options": [{"id": "o_d0000001", "label": "大一"}, {"id": "o_d0000002", "label": "大二"}],
        },
        "note": "",
        "createdBy": ObjectId(),
        "createdAt": NOW,
    }
    doc.update(overrides)
    return doc


def make_bank(**overrides):
    doc = {
        "schemaVersion": SCHEMA_VERSIONS["question_banks"],
        "ownerId": ObjectId(),
        "name": "常用人口学题目",
        "description": "",
        "items": [{"questionId": ObjectId(), "addedAt": NOW}],
        "sharedWith": [ObjectId()],
        "createdAt": NOW,
        "updatedAt": NOW,
    }
    doc.update(overrides)
    return doc


def test_valid_question_version_and_bank_accepted(db):
    db.questions.insert_one(make_question())
    db.questions.insert_one(make_question(
        derivedFrom={"questionId": ObjectId(), "versionId": ObjectId(), "version": 2}))
    db.question_versions.insert_one(make_version())
    db.question_versions.insert_one(make_version(content={
        "type": "number_input", "title": "你的年龄", "required": True,
        "validation": {"min": 0, "max": 120, "integer": True}}))
    db.question_banks.insert_one(make_bank())


def test_question_missing_latest_rejected(db):
    doc = make_question()
    del doc["latest"]
    assert_rejected(db.questions, doc)


def test_shared_with_duplicates_rejected(db):
    uid = ObjectId()
    assert_rejected(db.questions, make_question(sharedWith=[uid, uid]))


def _with_content(**changes):
    version = make_version()
    version["content"] = {**version["content"], **changes}
    return version


def test_version_content_cannot_have_jump_rules(db):
    # 跳转规则引用同一问卷里的其他题，属于问卷，不属于题目本身
    assert_rejected(db.question_versions, _with_content(jumpRules=[]))


def test_version_content_cannot_have_question_id(db):
    # 题目 id 属于具体问卷，加入问卷时才生成
    assert_rejected(db.question_versions, _with_content(id="q_a0000001"))


def test_version_content_type_rules_checked(db):
    assert_rejected(db.question_versions, _with_content(options=[{"id": "o_d0000001", "label": "大一"}]))
    assert_rejected(db.question_versions, _with_content(type="rating"))


def test_version_number_must_be_positive(db):
    assert_rejected(db.question_versions, make_version(version=0))


def test_bad_version_kind_rejected(db):
    assert_rejected(db.question_versions, make_version(kind="overwrite"))


def test_duplicate_version_number_rejected(db):
    qid = ObjectId()
    db.question_versions.insert_one(make_version(questionId=qid, version=1))
    db.question_versions.insert_one(make_version(questionId=qid, version=2))
    with pytest.raises(DuplicateKeyError):
        db.question_versions.insert_one(make_version(questionId=qid, version=2))


def test_bank_item_needs_question_id(db):
    assert_rejected(db.question_banks, make_bank(items=[{"addedAt": NOW}]))


def test_bank_missing_name_rejected(db):
    doc = make_bank()
    del doc["name"]
    assert_rejected(db.question_banks, doc)


# ---------- surveys：题目来源（阶段二） ----------

SOURCE = {"questionId": ObjectId(), "versionId": ObjectId(), "version": 3}


def test_survey_question_with_source_or_derived_from_accepted(db):
    survey = _with_question(0, source=SOURCE)
    survey["questions"][3] = {**survey["questions"][3], "derivedFrom": SOURCE}
    db.surveys.insert_one(survey)


def test_survey_source_missing_version_id_rejected(db):
    source = {k: v for k, v in SOURCE.items() if k != "versionId"}
    assert_rejected(db.surveys, _with_question(0, source=source))


def test_stage1_survey_version_still_accepted(db):
    # 迁移前的旧文档（schemaVersion 1）也必须能通过新校验器
    db.surveys.insert_one(make_survey(schemaVersion=1))


def test_future_schema_version_rejected(db):
    assert_rejected(db.surveys, make_survey(schemaVersion=SCHEMA_VERSIONS["surveys"] + 1))


def test_where_used_query_uses_index(db):
    db.surveys.insert_one(_with_question(0, source=SOURCE))
    plan = json.dumps(db.surveys.find({"questions.source.questionId": SOURCE["questionId"]}).explain(), default=str)
    assert "IXSCAN" in plan and "question_source" in plan
    assert "COLLSCAN" not in plan
