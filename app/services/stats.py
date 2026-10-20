"""统计：每次查看时用聚合管道从答卷实时计算，不在问卷里存计数（设计 2.2、2.8 节）。

分工：
- MongoDB 聚合管道负责"算"：筛选、展开、分组、计数、平均值、最新几条；
- Python 负责"拼"：和问卷定义合并，补选项文字、没人选的补 0、算占比、保留两位小数。
"""

from bson import ObjectId
from pymongo.database import Database

from app.services.surveys import SurveyNotFound, owned_filter
from db.schema import CHOICE_TYPES

PREVIEW_ENTRIES = 5     # 整份统计里每道填空题带的最新内容条数
FILL_TYPES = ["text_input", "number_input"]


class QuestionNotFound(Exception):
    pass


def _load(db: Database, owner_id: ObjectId, survey_id: str) -> dict:
    """只有创建者能看；已删除的问卷看不到统计。"""
    survey = db.surveys.find_one(owned_filter(owner_id, survey_id))
    if survey is None:
        raise SurveyNotFound
    return survey


def _ids(survey: dict, types: list[str]) -> list[str]:
    return [q["id"] for q in survey["questions"] if q["type"] in types]


def summary_pipeline(survey: dict) -> list[dict]:
    """整份统计的聚合管道：一次 $facet 分几路同时算出所有题的结果。"""
    return [
        # 走索引 survey_submittedAt；按提交时间倒序，供"最新几条"使用（同一毫秒内按 _id 区分先后）
        {"$match": {"surveyId": survey["_id"]}},
        {"$sort": {"submittedAt": -1, "_id": -1}},
        {"$unwind": "$answers"},
        {"$facet": {
            # 每道题有多少份答卷作答了
            "answered": [
                {"$group": {"_id": "$answers.questionId", "n": {"$sum": 1}}},
            ],
            # 选择题：单选的值是字符串，$unwind 当作单元素处理；多选的数组被展开成每个选项一行
            "optionCounts": [
                {"$match": {"answers.questionId": {"$in": _ids(survey, CHOICE_TYPES)}}},
                {"$unwind": "$answers.value"},
                {"$group": {"_id": {"q": "$answers.questionId", "o": "$answers.value"}, "n": {"$sum": 1}}},
            ],
            # 数字题：平均值、最小值、最大值
            "numbers": [
                {"$match": {"answers.questionId": {"$in": _ids(survey, ["number_input"])}}},
                {"$group": {"_id": "$answers.questionId",
                            "avg": {"$avg": "$answers.value"},
                            "min": {"$min": "$answers.value"},
                            "max": {"$max": "$answers.value"}}},
            ],
            # 填空题：每题最新几条（前面已按提交时间倒序）
            "latest": [
                {"$match": {"answers.questionId": {"$in": _ids(survey, FILL_TYPES)}}},
                {"$group": {"_id": "$answers.questionId",
                            "items": {"$firstN": {"n": PREVIEW_ENTRIES, "input": {
                                "value": "$answers.value",
                                "submittedAt": "$submittedAt",
                                "respondentId": "$respondentId"}}}}},
            ],
        }},
    ]


def entries_pipeline(survey: dict, question_id: str, skip: int, limit: int) -> list[dict]:
    """单道填空题的全部内容，分页；非匿名问卷用 $lookup 带出填写者。"""
    items: list[dict] = [{"$skip": skip}, {"$limit": limit}]
    if not survey["settings"]["allowAnonymous"]:
        items.append({"$lookup": {
            "from": "users", "localField": "respondentId", "foreignField": "_id", "as": "user",
            "pipeline": [{"$project": {"_id": 0, "username": 1, "displayName": 1}}],
        }})
    items.append({"$project": {"_id": 0, "value": "$answers.value", "submittedAt": 1,
                               "user": {"$first": "$user"}}})
    return [
        {"$match": {"surveyId": survey["_id"], "answers.questionId": question_id}},
        {"$sort": {"submittedAt": -1, "_id": -1}},
        {"$unwind": "$answers"},
        {"$match": {"answers.questionId": question_id}},
        {"$facet": {"total": [{"$count": "n"}], "items": items}},
    ]


def _respondent(user: dict | None) -> str | None:
    return f"{user['displayName']}（{user['username']}）" if user else None


def _round(x):
    return None if x is None else round(x, 2)


def _base(q: dict, answered: int) -> dict:
    return {"id": q["id"], "type": q["type"], "title": q["title"],
            "required": q["required"], "answered_count": answered}


def _choice_stat(q: dict, answered: int, counts: dict[tuple[str, str], int]) -> dict:
    options = []
    for o in q["options"]:  # 按题目里原本的选项顺序；没人选的补 0
        n = counts.get((q["id"], o["id"]), 0)
        options.append({"id": o["id"], "label": o["label"], "count": n,
                        "percent": round(n * 100 / answered, 2) if answered else 0.0})
    return {**_base(q, answered), "options": options}


def survey_stats(db: Database, owner_id: ObjectId, survey_id: str) -> dict:
    survey = _load(db, owner_id, survey_id)
    total = db.responses.count_documents({"surveyId": survey["_id"]})
    facets = next(db.responses.aggregate(summary_pipeline(survey)), None) or {}

    answered = {r["_id"]: r["n"] for r in facets.get("answered", [])}
    counts = {(r["_id"]["q"], r["_id"]["o"]): r["n"] for r in facets.get("optionCounts", [])}
    numbers = {r["_id"]: r for r in facets.get("numbers", [])}
    latest = {r["_id"]: r["items"] for r in facets.get("latest", [])}

    # 最新几条的填写者：一次查询带出名字和账号（匿名问卷的答卷没有 respondentId）
    user_ids = {i["respondentId"] for items in latest.values() for i in items if i.get("respondentId")}
    users = {u["_id"]: u for u in db.users.find({"_id": {"$in": list(user_ids)}},
                                                 {"username": 1, "displayName": 1})}

    questions = []
    for q in survey["questions"]:
        n = answered.get(q["id"], 0)
        if q["type"] in CHOICE_TYPES:
            questions.append(_choice_stat(q, n, counts))
            continue
        stat = {**_base(q, n), "entries_total": n, "entries": [
            {"value": i["value"], "submitted_at": i["submittedAt"],
             "respondent": _respondent(users.get(i.get("respondentId")))}
            for i in latest.get(q["id"], [])
        ]}
        if q["type"] == "number_input":
            num = numbers.get(q["id"], {})
            stat.update(average=_round(num.get("avg")), min=num.get("min"), max=num.get("max"))
        questions.append(stat)

    return {"survey_id": str(survey["_id"]), "title": survey["title"],
            "total_responses": total, "questions": questions}


def question_stats(db: Database, owner_id: ObjectId, survey_id: str, question_id: str,
                   page: int, page_size: int) -> dict:
    survey = _load(db, owner_id, survey_id)
    q = next((x for x in survey["questions"] if x["id"] == question_id), None)
    if q is None:
        raise QuestionNotFound

    if q["type"] in CHOICE_TYPES:
        # 选择题复用整份统计的结果
        return next(s for s in survey_stats(db, owner_id, survey_id)["questions"] if s["id"] == question_id)

    result = next(db.responses.aggregate(
        entries_pipeline(survey, question_id, (page - 1) * page_size, page_size)), None) or {}
    answered = result["total"][0]["n"] if result.get("total") else 0
    stat = {**_base(q, answered), "entries_total": answered, "page": page, "page_size": page_size,
            "entries": [{"value": i["value"], "submitted_at": i["submittedAt"],
                         "respondent": _respondent(i.get("user"))} for i in result.get("items", [])]}
    if q["type"] == "number_input":
        num = next(db.responses.aggregate([
            {"$match": {"surveyId": survey["_id"], "answers.questionId": question_id}},
            {"$unwind": "$answers"},
            {"$match": {"answers.questionId": question_id}},
            {"$group": {"_id": None, "avg": {"$avg": "$answers.value"},
                        "min": {"$min": "$answers.value"}, "max": {"$max": "$answers.value"}}},
        ]), {})
        stat.update(average=_round(num.get("avg")), min=num.get("min"), max=num.get("max"))
    return stat
