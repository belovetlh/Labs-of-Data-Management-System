"""问卷 ↔ 题库（设计 4.7、4.8 节）：从题库选题、把问卷里的题存入题库、切换所用的版本、被哪些问卷使用。

问卷里存的是版本内容的快照（含选项 id）加 source 引用：填写、校验、跳转、按问卷统计都只看快照，
和阶段一完全相同；source 只用于"被哪些问卷使用"和跨问卷统计。
修改来自题库的题见 questions.update_question。
"""

from bson import ObjectId
from pymongo.database import Database

from app.engine.structure import question_label
from app.services import library as lib
from app.services import questions as qsvc
from app.services.content import new_id, snapshot, version_ref
from app.services.surveys import (SurveyConflict, SurveyNotFound, now_utc, owned_filter, phase_of,
                                  response_counts)
from db.schema import MAX_QUESTIONS


def _position_of_source(questions: list[dict], library_question_id: ObjectId) -> int | None:
    for i, q in enumerate(questions):
        if (q.get("source") or {}).get("questionId") == library_question_id:
            return i
    return None


def add_from_library(db: Database, owner_id: ObjectId, survey_id: str, question_id: str,
                     number: int | None, required: bool | None, position: int | None) -> str:
    """把看得到的一道题（默认最新版本）加入草稿问卷，返回它在本问卷里的题目 id。"""
    survey = qsvc._load_draft(db, owner_id, survey_id)
    questions = list(survey["questions"])
    if len(questions) >= MAX_QUESTIONS:
        raise SurveyConflict(f"一份问卷最多 {MAX_QUESTIONS} 道题")
    library_question = lib.load_visible(db, owner_id, question_id)
    existing = _position_of_source(questions, library_question["_id"])
    if existing is not None:
        # 同一道题出现两次，统计时说不清一份答卷该算哪一个
        raise SurveyConflict(f"这道题已在问卷中（{question_label(existing, questions[existing])}）")
    version = lib.pick_version(db, library_question, number)

    taken = {q["id"] for q in questions}
    default_required = version["content"]["required"]
    question = snapshot(version, new_id("q", taken), default_required if required is None else required, [])
    if position is None or position >= len(questions):
        questions.append(question)
    else:
        questions.insert(max(position, 0), question)
    qsvc._save(db, survey, questions, SurveyConflict)
    return question["id"]


def switch_version(db: Database, owner_id: ObjectId, survey_id: str, question_id: str, number: int) -> None:
    """把草稿里来自题库的题换成另一个版本（升级、回退或换到别的分支）。

    保留本问卷的题目 id、是否必答、跳转规则；新版本删掉了跳转规则用到的选项时，整份检查会拒绝。
    """
    survey = qsvc._load_draft(db, owner_id, survey_id)
    questions = list(survey["questions"])
    i = qsvc._index_of(questions, question_id)
    old = questions[i]
    source = old.get("source")
    if source is None:
        raise SurveyConflict(f"{question_label(i, old)}不是来自题库的题")
    try:
        library_question = lib.load_visible(db, owner_id, str(source["questionId"]))
    except lib.QuestionNotFound:
        raise SurveyConflict(f"{question_label(i, old)}在题库里已被删除或不再共享给你，不能切换版本") from None
    version = lib.pick_version(db, library_question, number)
    if version["_id"] == source["versionId"]:
        return
    questions[i] = snapshot(version, question_id, old["required"], old.get("jumpRules") or [])
    qsvc._save(db, survey, questions, SurveyConflict)


def save_to_library(db: Database, owner_id: ObjectId, survey_id: str, question_id: str, note: str) -> None:
    """把自己问卷里的一道题存为题库里的新题，并给问卷里的题加上 source。

    已发布、已关闭的问卷也可以：题目内容一个字都不改，只加来源标记，于是阶段一的旧问卷也能并入跨问卷统计。
    """
    survey = db.surveys.find_one(owned_filter(owner_id, survey_id))
    if survey is None:
        raise SurveyNotFound
    i = qsvc._index_of(survey["questions"], question_id)
    q = survey["questions"][i]
    if "source" in q:
        raise SurveyConflict(f"{question_label(i, q)}已关联题库 v{q['source']['version']}")

    content = {k: q[k] for k in ("type", "title", "required", "options", "validation") if k in q}
    derived_from = q.get("derivedFrom")   # 脱钩后改过的题：新题记着改自哪道题的哪个版本
    created = lib.insert_question(db, owner_id, content, note, "fork" if derived_from else "create", derived_from)
    version = lib.pick_version(db, created, None)

    # 带 revision 条件写回：期间问卷被改过（如这道题被修改或删除）就失败，并撤回刚建的题
    result = db.surveys.update_one(
        {"_id": survey["_id"], "deletedAt": None, "revision": survey["revision"],
         f"questions.{i}.id": question_id},
        {"$set": {f"questions.{i}.source": version_ref(version), "updatedAt": now_utc()}, "$inc": {"revision": 1}},
    )
    if result.matched_count == 0:
        db.question_versions.delete_one({"_id": version["_id"]})
        db.questions.delete_one({"_id": created["_id"]})
        raise SurveyConflict("问卷刚被修改过，请刷新后重试")


def attach_library_info(db: Database, user_id: ObjectId, detail: dict) -> dict:
    """给问卷详情里来自题库的题加上 library：题库里的最新版本、现在还能不能用、能否切换版本，
    以及能否存为新版本（canEdit：只有题目创建者能建新版本，前端据此决定是否显示这个按钮）。"""
    sourced = [q for q in detail["questions"] if q.get("source")]
    if not sourced:
        return detail
    ids = [ObjectId(q["source"]["questionId"]) for q in sourced]
    found = {q["_id"]: q for q in db.questions.find({"_id": {"$in": ids}, "deletedAt": None})}
    for q in sourced:
        library_question = found.get(ObjectId(q["source"]["questionId"]))
        if library_question is None or not lib.can_view(db, user_id, library_question):
            q["library"] = {"available": False}
            continue
        latest = library_question["latest"]
        q["library"] = {"available": True, "latestVersion": latest["version"],
                        "isLatest": str(latest["versionId"]) == q["source"]["versionId"],
                        "canEdit": library_question["ownerId"] == user_id}
    return detail


# ---------- 被哪些问卷使用（设计 4.8 节） ----------

def usage_pipeline(question_id: ObjectId, user_id: ObjectId) -> list[dict]:
    """一次聚合：自己的问卷取详情，别人的问卷只按版本计数。"""
    return [
        # 走 question_source 多键索引；只看 source（脱钩的题内容已不同，改题库不影响它）
        {"$match": {"questions.source.questionId": question_id, "deletedAt": None}},
        # 这道题在问卷里的下标；第 4 段保证同一道题在一份问卷里只出现一次
        {"$addFields": {"position": {"$indexOfArray": [
            {"$map": {"input": "$questions", "in": "$$this.source.questionId"}}, question_id]}}},
        {"$project": {"ownerId": 1, "title": 1, "status": 1, "settings": 1, "createdAt": 1, "position": 1,
                      "source": {"$getField": {"field": "source",
                                               "input": {"$arrayElemAt": ["$questions", "$position"]}}}}},
        {"$facet": {
            "mine": [{"$match": {"ownerId": user_id}}, {"$sort": {"createdAt": -1}}],
            "others": [{"$match": {"ownerId": {"$ne": user_id}}},
                       {"$group": {"_id": "$source.version", "versionId": {"$first": "$source.versionId"},
                                   "n": {"$sum": 1}}}],
        }},
    ]


def question_usage(db: Database, user_id: ObjectId, question_id: str) -> dict:
    """看得到这道题的人都能查：自己的问卷给详情；别人的问卷有多少份只告诉题目创建者（只有他能改这道题）。"""
    question = lib.load_visible(db, user_id, question_id)
    is_owner = question["ownerId"] == user_id
    facets = next(db.surveys.aggregate(usage_pipeline(question["_id"], user_id)), {"mine": [], "others": []})
    mine = facets["mine"]
    others = facets["others"] if is_owner else []
    counts = response_counts(db, [s["_id"] for s in mine])
    latest_id = question["latest"]["versionId"]

    versions: dict[int, dict] = {}
    for s in mine:
        v = versions.setdefault(s["source"]["version"], {"version": s["source"]["version"],
                                                          "version_id": str(s["source"]["versionId"]),
                                                          "mine": 0, "others": 0})
        v["mine"] += 1
    for row in others:
        v = versions.setdefault(row["_id"], {"version": row["_id"], "version_id": str(row["versionId"]),
                                             "mine": 0, "others": 0})
        v["others"] = row["n"]

    return {
        "question_id": str(question["_id"]),
        "is_owner": is_owner,
        "total_surveys": len(mine) + sum(row["n"] for row in others),
        "others_total": sum(row["n"] for row in others) if is_owner else None,
        "versions": [versions[k] for k in sorted(versions)],
        "my_surveys": [{
            "survey_id": str(s["_id"]),
            "title": s["title"],
            "phase": phase_of(s),
            "version": s["source"]["version"],
            "question_number": s["position"] + 1,
            "response_count": counts.get(s["_id"], 0),
            "is_latest": s["source"]["versionId"] == latest_id,
        } for s in mine],
    }
