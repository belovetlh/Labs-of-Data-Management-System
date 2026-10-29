"""题目管理：增、改、删、调序、设置跳转规则（只对草稿有效）。

阶段二：来自题库的题（带 source）修改内容时，须选择"只改本问卷"（脱钩）或"存为新版本"，见 update_question。

题目之间有引用关系（跳转规则指向别的题），很多检查要看整份问卷才能做，所以统一采用：
读出问卷 → 在代码里修改题目数组 → 对整份问卷做结构检查 → 带 revision 条件写回（乐观锁）。
写回时如果 revision 已变，说明中间有别的修改插进来，本次写入失败，以先到者为准。
"""

from bson import ObjectId
from pymongo.database import Database

from app.engine.structure import check_question, check_survey, question_label, referencing_questions
from app.services import library as lib
from app.services.content import InvalidDefinition, build_content, new_id, options_keep_ids, same_content, snapshot
from app.services.surveys import SurveyConflict, SurveyNotFound, owned_filter, now_utc
from db.schema import CHOICE_TYPES, MAX_QUESTIONS

LOCKED_MESSAGE = "问卷已发布，题目不能再修改；如有问题请删除后重新创建"


class QuestionNotFound(Exception):
    pass


# ---------- 读写骨架 ----------

def _load_draft(db: Database, owner_id: ObjectId, survey_id: str) -> dict:
    survey = db.surveys.find_one(owned_filter(owner_id, survey_id))
    if survey is None:
        raise SurveyNotFound
    if survey["status"] != "draft":
        raise SurveyConflict(LOCKED_MESSAGE)
    return survey


def _save(db: Database, survey: dict, questions: list[dict], conflict_cls=SurveyConflict) -> None:
    """整份检查通过后，带 revision 条件写回。"""
    errors = check_survey(questions)
    if errors:
        raise conflict_cls("；".join(errors))
    result = db.surveys.update_one(
        {"_id": survey["_id"], "status": "draft", "deletedAt": None, "revision": survey["revision"]},
        {"$set": {"questions": questions, "updatedAt": now_utc()}, "$inc": {"revision": 1}},
    )
    if result.matched_count == 0:
        current = db.surveys.find_one({"_id": survey["_id"], "deletedAt": None})
        if current is None:
            raise SurveyNotFound
        if current["status"] != "draft":
            raise SurveyConflict(LOCKED_MESSAGE)
        raise SurveyConflict("问卷刚被修改过，请刷新后重试")


def _index_of(questions: list[dict], question_id: str) -> int:
    for i, q in enumerate(questions):
        if q["id"] == question_id:
            return i
    raise QuestionNotFound


def _all_option_ids(questions: list[dict]) -> set[str]:
    return {o["id"] for q in questions for o in q.get("options") or []}


# ---------- 题目定义 → 文档格式 ----------

def _build_question(data: dict, question_id: str, options: list[dict] | None, jump_rules: list[dict]) -> dict:
    """data 为请求模型 model_dump() 的结果（下划线字段名）；题目内容的组装与题库共用。"""
    return {"id": question_id, **build_content(data, options), "jumpRules": jump_rules}


# ---------- 对外操作 ----------

def add_question(db: Database, owner_id: ObjectId, survey_id: str, data: dict,
                 position: int | None = None) -> str:
    survey = _load_draft(db, owner_id, survey_id)
    questions = list(survey["questions"])
    if len(questions) >= MAX_QUESTIONS:
        raise SurveyConflict(f"一份问卷最多 {MAX_QUESTIONS} 道题")
    taken = {q["id"] for q in questions} | _all_option_ids(questions)
    options = None
    if data["type"] in CHOICE_TYPES:
        options = [{"id": new_id("o", taken), "label": o["label"]} for o in data["options"]]
    question = _build_question(data, new_id("q", taken), options, [])
    if position is None or position >= len(questions):
        questions.append(question)
    else:
        questions.insert(max(position, 0), question)
    # 插入新题不会让已有规则失效（规则按 id 引用），检查失败只可能是本题定义有误
    _save(db, survey, questions, InvalidDefinition)
    return question["id"]


def update_question(db: Database, owner_id: ObjectId, survey_id: str, question_id: str, data: dict,
                    library_action: str | None = None) -> None:
    """library_action 只对来自题库的题有意义：detach 只改本问卷（脱钩）；new_version 在题库里存为新版本并使用。"""
    survey = _load_draft(db, owner_id, survey_id)
    questions = list(survey["questions"])
    i = _index_of(questions, question_id)
    old = questions[i]
    rules = old.get("jumpRules") or []
    if data["type"] != old["type"] and rules:
        raise SurveyConflict(f"{question_label(i, old)}已有跳转规则，修改题型前请先清空它的跳转规则")
    options = None
    if data["type"] in CHOICE_TYPES:
        taken = {q["id"] for q in questions} | _all_option_ids(questions)
        options = options_keep_ids(data["options"], old.get("options") or [], taken)
    new = _build_question(data, question_id, options, rules)

    # 先单独检查本题定义（422），再看是否让本题已有的跳转规则失效（409）
    definition_errors = check_question(new)
    if definition_errors:
        raise InvalidDefinition("；".join(f"{question_label(i, new)}：{m}" for m in definition_errors))

    source = old.get("source")
    if source is None:
        if "derivedFrom" in old:
            new["derivedFrom"] = old["derivedFrom"]   # 已脱钩的题再改，仍记着最初改自哪个版本
    elif same_content(new, old):
        new["source"] = source                        # 只改了是否必答：仍与来源版本一致
    elif library_action is None:
        raise SurveyConflict(
            f"{question_label(i, old)}来自题库 v{source['version']}，修改内容前请选择："
            "只改本问卷（libraryAction=detach）或存为题库新版本（libraryAction=new_version）"
        )
    elif library_action == "detach":
        new["derivedFrom"] = source
    else:
        # 先用改后的内容做整份检查（如删掉了跳转规则用到的选项）：不通过就不去题库建版本，
        # 否则问卷改不成，题库里却多了一个版本。新版本里原有选项的 id 与 new 相同，检查结果一致
        errors = check_survey(questions[:i] + [new] + questions[i + 1:])
        if errors:
            raise SurveyConflict("；".join(errors))
        # 再在题库建版本（基于本问卷所用的版本，形成分支），最后写回问卷；
        # 写回时问卷恰好被别人改过而失败的话，题库里多一个完整的版本，无害
        version = lib.create_version(db, owner_id, str(source["questionId"]), source["version"], data,
                                     f"在问卷「{lib.short_title(survey['title'])}」中修改")
        new = snapshot(version, question_id, data["required"], rules)
    questions[i] = new
    _save(db, survey, questions, SurveyConflict)


def delete_question(db: Database, owner_id: ObjectId, survey_id: str, question_id: str) -> None:
    survey = _load_draft(db, owner_id, survey_id)
    questions = list(survey["questions"])
    i = _index_of(questions, question_id)
    refs = referencing_questions(questions, question_id)
    if refs:
        raise SurveyConflict(
            f"{'、'.join(refs)}的跳转规则会跳到{question_label(i, questions[i])}，请先修改这些规则"
        )
    del questions[i]
    _save(db, survey, questions, SurveyConflict)


def reorder_questions(db: Database, owner_id: ObjectId, survey_id: str, question_ids: list[str]) -> None:
    survey = _load_draft(db, owner_id, survey_id)
    by_id = {q["id"]: q for q in survey["questions"]}
    if len(question_ids) != len(by_id) or set(question_ids) != set(by_id):
        raise InvalidDefinition("新顺序必须恰好包含问卷的全部题目，且每道题只出现一次")
    # 顺序变了可能让某条规则变成往前跳，由整份检查报出（409）
    _save(db, survey, [by_id[qid] for qid in question_ids], SurveyConflict)


def set_jump_rules(db: Database, owner_id: ObjectId, survey_id: str, question_id: str, rules: list[dict]) -> None:
    survey = _load_draft(db, owner_id, survey_id)
    questions = list(survey["questions"])
    i = _index_of(questions, question_id)
    questions[i] = {**questions[i], "jumpRules": rules}
    # 规则是本次请求提交的，检查不通过属于输入不合法（422）
    _save(db, survey, questions, InvalidDefinition)
