"""问卷的数据库读写与状态切换。

状态切换和草稿修改一律用"条件更新"：把"当前状态允许"写进 update_one 的过滤条件，
判断与修改由 MongoDB 在单个文档上一次完成（原子操作），并发时以先到者为准。
条件不满足（改了 0 条）时，再读一次问卷，给出具体原因。
"""

import secrets
import string
from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.engine.structure import check_survey
from db.schema import SCHEMA_VERSION

SLUG_ALPHABET = string.ascii_letters + string.digits
SLUG_LENGTH = 8


class SurveyNotFound(Exception):
    """问卷不存在、已删除，或不属于当前用户。"""


class SurveyConflict(Exception):
    """当前状态不允许该操作；异常信息为给用户看的原因。"""


def now_utc() -> datetime:
    # MongoDB 日期精度为毫秒，提前截断，保证返回值与库里一致
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


def generate_slug() -> str:
    return "".join(secrets.choice(SLUG_ALPHABET) for _ in range(SLUG_LENGTH))


def phase_of(survey: dict, now: datetime | None = None) -> str:
    """计算阶段（不存库）：draft 草稿 / open 收集中 / expired 已截止 / closed 已关闭。"""
    if survey["status"] == "draft":
        return "draft"
    if survey["status"] == "closed":
        return "closed"
    deadline = survey["settings"]["deadline"]
    if deadline is not None and deadline <= (now or datetime.now(timezone.utc)):
        return "expired"
    return "open"


def owned_filter(owner_id: ObjectId, survey_id: str) -> dict:
    try:
        oid = ObjectId(survey_id)
    except (InvalidId, TypeError):
        raise SurveyNotFound from None
    # deletedAt 为 None 同时匹配"没有这个字段"，即未删除
    return {"_id": oid, "ownerId": owner_id, "deletedAt": None}


def _not_deadline_passed(now: datetime) -> dict:
    return {"$or": [{"settings.deadline": None}, {"settings.deadline": {"$gt": now}}]}


# ---------- 创建与查询 ----------

def create_survey(db: Database, owner_id: ObjectId, title: str, description: str, settings: dict) -> dict:
    now = now_utc()
    doc = {
        "schemaVersion": SCHEMA_VERSION,
        "revision": 0,
        "ownerId": owner_id,
        "title": title,
        "description": description,
        "status": "draft",
        "settings": {
            "allowAnonymous": settings["allow_anonymous"],
            "allowMultipleSubmissions": settings["allow_multiple_submissions"],
            "deadline": settings["deadline"],
        },
        "questions": [],
        "createdAt": now,
        "updatedAt": now,
        "publishedAt": None,
        "closedAt": None,
    }
    # slug 由唯一索引 uniq_slug 保证不重复；极小概率撞上时换一个重试
    for _ in range(5):
        doc["slug"] = generate_slug()
        doc.pop("_id", None)
        try:
            db.surveys.insert_one(doc)
            return doc
        except DuplicateKeyError as e:
            if "uniq_slug" not in str(e):
                raise
    raise RuntimeError("生成问卷链接失败，请重试")


def response_counts(db: Database, survey_ids: list[ObjectId]) -> dict[ObjectId, int]:
    """一次聚合算出多份问卷各自的答卷数（不在问卷里存计数字段）。"""
    if not survey_ids:
        return {}
    pipeline = [
        {"$match": {"surveyId": {"$in": survey_ids}}},
        {"$group": {"_id": "$surveyId", "n": {"$sum": 1}}},
    ]
    return {row["_id"]: row["n"] for row in db.responses.aggregate(pipeline)}


def list_surveys(db: Database, owner_id: ObjectId) -> list[dict]:
    # 列表不需要题目内容，只用 $size 算出题目数（find 投影支持聚合表达式；
    # 带表达式时只能用"列出要哪些字段"的写法，不能和"排除 questions"混用）
    projection = {
        "title": 1, "status": 1, "slug": 1, "settings": 1, "createdAt": 1, "publishedAt": 1,
        "questionCount": {"$size": "$questions"},
    }
    surveys = list(
        db.surveys.find({"ownerId": owner_id, "deletedAt": None}, projection).sort("createdAt", -1)
    )
    counts = response_counts(db, [s["_id"] for s in surveys])
    for s in surveys:
        s["responseCount"] = counts.get(s["_id"], 0)
    return surveys


def get_survey(db: Database, owner_id: ObjectId, survey_id: str) -> dict:
    survey = db.surveys.find_one(owned_filter(owner_id, survey_id))
    if survey is None:
        raise SurveyNotFound
    survey["questionCount"] = len(survey["questions"])
    survey["responseCount"] = db.responses.count_documents({"surveyId": survey["_id"]})
    return survey


# ---------- 条件更新 ----------

def _conditional_update(db, owner_id, survey_id, condition: dict, update: dict, explain) -> dict:
    """condition 满足才执行 update（并把 revision 加 1）；否则由 explain(当前文档) 给出原因。"""
    flt = owned_filter(owner_id, survey_id)
    updated = db.surveys.find_one_and_update(
        {**flt, **condition}, {**update, "$inc": {"revision": 1}}, return_document=ReturnDocument.AFTER
    )
    if updated is not None:
        return updated
    current = db.surveys.find_one(flt)
    if current is None:
        raise SurveyNotFound
    raise SurveyConflict(explain(current))


def update_draft(db: Database, owner_id: ObjectId, survey_id: str, changes: dict) -> dict:
    """只有草稿能改；changes 中只包含请求里传了的字段。"""
    fields = {}
    if "title" in changes:
        fields["title"] = changes["title"]
    if "description" in changes:
        fields["description"] = changes["description"]
    names = {"allow_anonymous": "allowAnonymous",
             "allow_multiple_submissions": "allowMultipleSubmissions",
             "deadline": "deadline"}
    for key, value in (changes.get("settings") or {}).items():
        if key in ("allow_anonymous", "allow_multiple_submissions") and value is None:
            continue  # 这两个开关不接受 null，视为未传
        fields[f"settings.{names[key]}"] = value
    fields["updatedAt"] = now_utc()
    return _conditional_update(
        db, owner_id, survey_id,
        {"status": "draft"},
        {"$set": fields},
        lambda s: "问卷已发布，内容不能再修改；如有问题请删除后重新创建",
    )


def publish(db: Database, owner_id: ObjectId, survey_id: str) -> dict:
    now = now_utc()
    current = db.surveys.find_one(owned_filter(owner_id, survey_id))
    if current is None:
        raise SurveyNotFound
    # 发布前对整份问卷做一次结构检查（题目定义、跳转规则）
    if current["status"] == "draft":
        errors = check_survey(current["questions"])
        if errors:
            raise SurveyConflict("问卷存在问题，不能发布：" + "；".join(errors))

    def explain(s):
        if s["status"] != "draft":
            return "只有草稿可以发布"
        if not s["questions"]:
            return "问卷至少要有 1 道题才能发布"
        if s["revision"] != current["revision"]:
            return "问卷刚被修改过，请刷新后重试"
        return "截止时间已过，请先修改截止时间再发布"

    # revision 条件保证：发布的正是刚才检查过的那一版
    return _conditional_update(
        db, owner_id, survey_id,
        {"status": "draft", "revision": current["revision"], "questions.0": {"$exists": True},
         **_not_deadline_passed(now)},
        {"$set": {"status": "published", "publishedAt": now, "updatedAt": now}},
        explain,
    )


def close(db: Database, owner_id: ObjectId, survey_id: str) -> dict:
    now = now_utc()
    return _conditional_update(
        db, owner_id, survey_id,
        {"status": "published"},
        {"$set": {"status": "closed", "closedAt": now, "updatedAt": now}},
        lambda s: "草稿还没有发布，无需关闭" if s["status"] == "draft" else "问卷已经关闭",
    )


def reopen(db: Database, owner_id: ObjectId, survey_id: str, deadline_given: bool,
           deadline: datetime | None) -> dict:
    """已关闭，或已发布但已过截止时间的问卷可以重新开放。

    deadline_given 为 False 时沿用原截止时间，此时原截止时间必须为空或在未来。
    """
    now = now_utc()
    reopenable = {"$or": [
        {"status": "closed"},
        {"status": "published", "settings.deadline": {"$ne": None, "$lte": now}},
    ]}
    update = {"status": "published", "closedAt": None, "updatedAt": now}
    if deadline_given:
        update["settings.deadline"] = deadline
        condition = reopenable
    else:
        condition = {"$and": [reopenable, _not_deadline_passed(now)]}

    def explain(s):
        if s["status"] == "draft":
            return "草稿还没有发布，请直接发布"
        if phase_of(s, now) == "open":
            return "问卷正在收集中，无需重新开放"
        return "截止时间已过，重新开放时请设置新的截止时间或取消截止"

    return _conditional_update(db, owner_id, survey_id, condition, {"$set": update}, explain)


def delete(db: Database, owner_id: ObjectId, survey_id: str) -> None:
    """标记删除：任何状态都可以；之后列表、链接、统计、提交都看不到这份问卷。"""
    now = now_utc()
    result = db.surveys.update_one(
        owned_filter(owner_id, survey_id), {"$set": {"deletedAt": now, "updatedAt": now}}
    )
    if result.matched_count == 0:
        raise SurveyNotFound


# ---------- 输出格式 ----------

def to_summary(survey: dict, now: datetime | None = None) -> dict:
    st = survey["settings"]
    return {
        "id": str(survey["_id"]),
        "title": survey["title"],
        "status": survey["status"],
        "phase": phase_of(survey, now),
        "slug": survey["slug"],
        "share_url": f"/survey/{survey['slug']}",
        "settings": {
            "allow_anonymous": st["allowAnonymous"],
            "allow_multiple_submissions": st["allowMultipleSubmissions"],
            "deadline": st["deadline"],
        },
        "question_count": survey.get("questionCount", len(survey.get("questions", []))),
        "response_count": survey.get("responseCount", 0),
        "created_at": survey["createdAt"],
        "published_at": survey.get("publishedAt"),
    }


def to_detail(survey: dict, now: datetime | None = None) -> dict:
    return {
        **to_summary(survey, now),
        "description": survey["description"],
        "questions": survey["questions"],
        "updated_at": survey["updatedAt"],
        "closed_at": survey.get("closedAt"),
    }
