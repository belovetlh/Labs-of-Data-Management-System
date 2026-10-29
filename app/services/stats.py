"""统计：每次查看时用聚合管道从答卷实时计算，不在问卷里存计数（设计 2.2、2.8 节）。

分工：
- MongoDB 聚合管道负责"算"：筛选、展开、分组、计数、平均值、最新几条；
- Python 负责"拼"：和问卷定义合并，补选项文字、没人选的补 0、算占比、保留两位小数。

跨问卷统计（阶段二）也在这里：先用 question_source 索引找到自己用了这道题的问卷，再统计这些问卷的答卷。
"""

import math

from bson import ObjectId
from pymongo.database import Database

from app.services import library as lib
from app.services.content import InvalidDefinition
from app.services.surveys import SurveyNotFound, owned_filter, response_counts
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


# ---------- 跨问卷统计（阶段二，设计 4.9 节） ----------

MAX_BUCKETS = 100       # 自己指定区间宽度时最多分多少个区间
TARGET_BUCKETS = 10     # 自动选宽度时希望的区间数


def sources_pipeline(question_id: ObjectId, owner_id: ObjectId, version_id: ObjectId | None) -> list[dict]:
    """第一步：自己的哪些问卷用了这道题，以及它在每份问卷里的题目 id、用的版本。"""
    match = {"questions.source.questionId": question_id, "ownerId": owner_id, "deletedAt": None}
    if version_id is not None:
        # 版本 id 只属于这一道题，所以与上面的条件一定落在同一道题上
        match["questions.source.versionId"] = version_id
    return [
        {"$match": match},   # 走 question_source 多键索引
        {"$addFields": {"position": {"$indexOfArray": [
            {"$map": {"input": "$questions", "in": "$$this.source.questionId"}}, question_id]}}},
        {"$project": {"title": 1, "settings": 1, "createdAt": 1, "position": 1,
                      "question": {"$arrayElemAt": ["$questions", "$position"]}}},
        {"$project": {"title": 1, "createdAt": 1, "position": 1,
                      "anonymous": "$settings.allowAnonymous",
                      "localId": "$question.id", "source": "$question.source"}},
        {"$sort": {"createdAt": -1}},
    ]


def cross_pipeline(sources: list[dict], facets: dict[str, list[dict]]) -> list[dict]:
    """第二步：只读这几份问卷的答卷，展开后留下这道题的回答，再分几路统计。

    按 (问卷, 本问卷里的题目 id) 成对匹配：题目 id 只在一份问卷内唯一，同一道题库题在每份问卷里 id 不同。
    """
    pairs = [{"surveyId": s["_id"], "answers.questionId": s["localId"]} for s in sources]
    return [
        {"$match": {"$or": pairs}},   # 每个分支都走 survey_submittedAt 索引
        {"$sort": {"submittedAt": -1, "_id": -1}},
        {"$unwind": "$answers"},
        {"$match": {"$or": pairs}},
        {"$facet": facets},
    ]


def _clean(x: float):
    """去掉浮点误差（0.1 × 3 = 0.30000000000000004），整数写成 int。"""
    x = round(x, 10)
    return int(x) if x == int(x) else x


def _nice_width(low: float, high: float, integers: bool):
    """从 1、2、5 × 10^k 中选最小的、使区间数不超过 TARGET_BUCKETS 的宽度；全是整数时至少为 1。"""
    raw = (high - low) / TARGET_BUCKETS
    if raw <= 0:
        return 1
    k = math.floor(math.log10(raw))
    width = next(_clean(step * 10 ** k) for step in (1, 2, 5, 10) if step * 10 ** k >= raw)
    return max(width, 1) if integers else width


def _boundaries(low: float, high: float, width: float) -> list:
    """区间左闭右开，从 width 的整数倍开始，最大值落在最后一个区间里。"""
    start = math.floor(low / width)
    end = math.floor(high / width) + 1
    # 除法的浮点误差（0.3 / 0.1 = 2.9999…）可能让端点差一格，补齐
    if _clean(start * width) > low:
        start -= 1
    if _clean(end * width) <= high:
        end += 1
    if end - start > MAX_BUCKETS:
        raise InvalidDefinition(f"区间宽度太小：会分成 {end - start} 个区间，最多 {MAX_BUCKETS} 个")
    return [_clean(i * width) for i in range(start, end + 1)]


def _merged_options(versions: list[dict]) -> list[dict]:
    """按选项 id 合并各版本：文字取包含它的最新版本，otherLabels 列出其他版本里不同的写法；
    按最新版本的选项顺序，旧版本独有的选项排在后面。"""
    merged: dict[str, dict] = {}
    for v in sorted(versions, key=lambda v: v["version"], reverse=True):
        for o in v["content"]["options"]:
            m = merged.setdefault(o["id"], {"id": o["id"], "label": o["label"], "versions": [], "other_labels": []})
            m["versions"].insert(0, v["version"])
            if o["label"] != m["label"] and o["label"] not in m["other_labels"]:
                m["other_labels"].append(o["label"])
    return list(merged.values())


def _facets(qtype: str, sources: list[dict], page: int, page_size: int) -> dict[str, list[dict]]:
    facets: dict[str, list[dict]] = {"bySurvey": [{"$group": {"_id": "$surveyId", "n": {"$sum": 1}}}]}
    if qtype in CHOICE_TYPES:
        facets["optionCounts"] = [{"$unwind": "$answers.value"},
                                  {"$group": {"_id": "$answers.value", "n": {"$sum": 1}}}]
    elif qtype == "number_input":
        facets["numbers"] = [{"$group": {
            "_id": None, "avg": {"$avg": "$answers.value"},
            "min": {"$min": "$answers.value"}, "max": {"$max": "$answers.value"},
            "integers": {"$min": {"$eq": [{"$mod": ["$answers.value", 1]}, 0]}}}}]
    else:
        # 填空题：分页列出内容；只给现在不匿名的问卷带出填写者（沿用每份问卷自己的匿名设置）
        named = [s["_id"] for s in sources if not s["anonymous"]]
        facets["entries"] = [
            {"$skip": (page - 1) * page_size}, {"$limit": page_size},
            {"$lookup": {"from": "users", "as": "user",
                         "let": {"rid": {"$cond": [{"$in": ["$surveyId", named]}, "$respondentId", None]}},
                         "pipeline": [{"$match": {"$expr": {"$eq": ["$_id", "$$rid"]}}},
                                      {"$project": {"_id": 0, "username": 1, "displayName": 1}}]}},
            {"$project": {"_id": 0, "surveyId": 1, "value": "$answers.value", "submittedAt": 1,
                          "user": {"$first": "$user"}}},
        ]
    return facets


def cross_question_stats(db: Database, user_id: ObjectId, question_id: str, number: int | None,
                         bucket_size: float | None, page: int, page_size: int) -> dict:
    """一道题库题在自己所有问卷里的回答情况；number 为空时合并全部版本。"""
    question = lib.load_visible(db, user_id, question_id)
    picked = lib.pick_version(db, question, number) if number is not None else None
    sources = list(db.surveys.aggregate(
        sources_pipeline(question["_id"], user_id, picked["_id"] if picked else None)))

    # 参与合并的版本：问卷实际用到的版本（数据只来自它们）；一份问卷都没有时用指定版本或最新版本
    used = list({s["source"]["versionId"] for s in sources})
    versions = (list(db.question_versions.find({"_id": {"$in": used}})) if used
                else [picked or lib.pick_version(db, question, None)])
    newest = max(versions, key=lambda v: v["version"])
    qtype = newest["content"]["type"]   # 第 2 段保证各版本题型相同

    totals = response_counts(db, [s["_id"] for s in sources])
    result = (next(db.responses.aggregate(cross_pipeline(sources, _facets(qtype, sources, page, page_size))), {})
              if sources else {})
    answered_by = {r["_id"]: r["n"] for r in result.get("bySurvey", [])}
    answered = sum(answered_by.values())
    stat = {
        "question_id": str(question["_id"]), "type": qtype, "title": newest["content"]["title"],
        "version": picked["version"] if picked else None,
        "survey_count": len(sources), "total_responses": sum(totals.values()), "answered_count": answered,
        "by_survey": [{"survey_id": str(s["_id"]), "title": s["title"], "version": s["source"]["version"],
                       "question_number": s["position"] + 1, "answered_count": answered_by.get(s["_id"], 0),
                       "total_responses": totals.get(s["_id"], 0)} for s in sources],
    }

    if qtype in CHOICE_TYPES:
        counts = {r["_id"]: r["n"] for r in result.get("optionCounts", [])}
        stat["options"] = [{**o, "count": counts.get(o["id"], 0),
                            "percent": round(counts.get(o["id"], 0) * 100 / answered, 2) if answered else 0.0}
                           for o in _merged_options(versions)]
    elif qtype == "number_input":
        num = (result.get("numbers") or [{}])[0]
        stat.update(average=_round(num.get("avg")), min=num.get("min"), max=num.get("max"), distribution=[])
        if num:
            # 区间边界要先知道最小值、最大值，所以分布单独再聚合一次
            width = bucket_size or _nice_width(num["min"], num["max"], num["integers"])
            bounds = _boundaries(num["min"], num["max"], width)
            rows = next(db.responses.aggregate(cross_pipeline(sources, {"buckets": [
                {"$bucket": {"groupBy": "$answers.value", "boundaries": bounds}}]})))["buckets"]
            counts = {r["_id"]: r["count"] for r in rows}
            stat["bucket_size"] = _clean(width)
            stat["distribution"] = [{"from": lo, "to": hi, "count": counts.get(lo, 0)}
                                    for lo, hi in zip(bounds, bounds[1:])]
    else:
        titles = {s["_id"]: s["title"] for s in sources}
        stat.update(entries_total=answered, page=page, page_size=page_size, entries=[
            {"value": i["value"], "submitted_at": i["submittedAt"], "survey_id": str(i["surveyId"]),
             "survey_title": titles[i["surveyId"]], "respondent": _respondent(i.get("user"))}
            for i in result.get("entries", [])])
    return stat
