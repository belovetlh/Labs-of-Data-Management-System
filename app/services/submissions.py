"""填写与提交：按链接取问卷、判断能否填写、调用引擎、登记参与记录、保存答卷。

提交流程（设计 1.5、2.9 节）：
1. 问卷已发布、未截止、未删除；
2. 引擎从头重算路径并逐题校验，任何问题都拒绝整份提交；
3. 在 participations 登记：允许多次填写时次数加 1；不允许时由唯一索引拦截第二次；
4. 写入 responses（匿名问卷不写 respondentId）；
5. 第 4 步失败则撤回第 3 步的登记（本机 MongoDB 不支持跨集合事务）。
答卷写入后不提供修改、删除接口。
"""

from datetime import datetime
from typing import Any, Callable

from bson import ObjectId
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.engine.jump import Evaluation, evaluate_submission, navigate
from app.services.surveys import now_utc, phase_of
from db.schema import SCHEMA_VERSION


class FillNotFound(Exception):
    """链接不存在，或问卷还是草稿（对外当作不存在）。"""


class FillGone(Exception):
    """问卷已删除。"""


class FillClosed(Exception):
    """问卷当前不接收填写；异常信息为原因。"""


class AlreadySubmitted(Exception):
    pass


class SubmissionInvalid(Exception):
    def __init__(self, errors: dict[str, str]):
        super().__init__("提交内容有误")
        self.errors = errors


ALREADY_SUBMITTED_MESSAGE = "你已经填写过这份问卷"


def get_by_slug(db: Database, slug: str) -> dict:
    survey = db.surveys.find_one({"slug": slug})
    if survey is None:
        raise FillNotFound
    if survey.get("deletedAt") is not None:
        # 发布过再删除的：明确告诉填写者"已删除"；从未发布的草稿：当作不存在
        if survey.get("publishedAt") is None:
            raise FillNotFound
        raise FillGone
    if survey["status"] == "draft":
        raise FillNotFound
    return survey


def closed_reason(survey: dict) -> str | None:
    phase = phase_of(survey)
    if phase == "closed":
        return "问卷已关闭"
    if phase == "expired":
        return "问卷已截止"
    return None


def has_submitted(db: Database, survey_id: ObjectId, user_id: ObjectId) -> bool:
    return db.participations.count_documents({"surveyId": survey_id, "userId": user_id}, limit=1) > 0


def fill_view(db: Database, slug: str, user: dict) -> dict:
    survey = get_by_slug(db, slug)
    reason = closed_reason(survey)
    already = (not survey["settings"]["allowMultipleSubmissions"]
               and has_submitted(db, survey["_id"], user["_id"]))
    if reason is None and already:
        reason = ALREADY_SUBMITTED_MESSAGE
    return {
        "title": survey["title"],
        "description": survey["description"],
        "slug": survey["slug"],
        "allow_anonymous": survey["settings"]["allowAnonymous"],
        "phase": phase_of(survey),
        "can_submit": reason is None,
        "reason": reason,
        "already_submitted": already,
        # 不返回跳转规则：下一题由服务端 navigate 计算
        "questions": [{k: v for k, v in q.items() if k != "jumpRules"} for q in survey["questions"]],
    }


def _open_survey(db: Database, slug: str) -> dict:
    survey = get_by_slug(db, slug)
    reason = closed_reason(survey)
    if reason:
        raise FillClosed(reason)
    return survey


def navigate_answers(db: Database, slug: str, answers: dict[str, Any]) -> Evaluation:
    """只计算、不保存：提交前随时退出，答了一半的内容不会留下。"""
    return navigate(_open_survey(db, slug)["questions"], answers)


def _register_participation(db: Database, survey: dict, user_id: ObjectId) -> Callable[[], None]:
    """登记参与记录，返回撤回函数。"""
    key = {"surveyId": survey["_id"], "userId": user_id}
    if not survey["settings"]["allowMultipleSubmissions"]:
        try:
            result = db.participations.insert_one({"schemaVersion": SCHEMA_VERSION, **key, "count": 1})
        except DuplicateKeyError:
            # 唯一索引 uniq_survey_user：并发提交时也只有一个能登记成功
            raise AlreadySubmitted from None
        return lambda: db.participations.delete_one({"_id": result.inserted_id})

    result = db.participations.update_one(
        key, {"$inc": {"count": 1}, "$setOnInsert": {"schemaVersion": SCHEMA_VERSION}}, upsert=True
    )
    if result.upserted_id is not None:
        return lambda: db.participations.delete_one({"_id": result.upserted_id})
    # 原来至少是 1，加 1 后至少是 2，减回去仍满足校验器 count ≥ 1
    return lambda: db.participations.update_one(key, {"$inc": {"count": -1}})


def submit(db: Database, slug: str, user: dict, answers: dict[str, Any]) -> datetime:
    survey = _open_survey(db, slug)
    evaluation = evaluate_submission(survey["questions"], answers)
    if not evaluation.complete:
        raise SubmissionInvalid(evaluation.errors)

    undo = _register_participation(db, survey, user["_id"])
    now = now_utc()
    response = {
        "schemaVersion": SCHEMA_VERSION,
        "surveyId": survey["_id"],
        "submittedAt": now,
        "answers": evaluation.answers,  # 只含路径上实际作答的题，值已整理
    }
    if not survey["settings"]["allowAnonymous"]:
        response["respondentId"] = user["_id"]
    try:
        db.responses.insert_one(response)
    except Exception:
        undo()
        raise
    return now
