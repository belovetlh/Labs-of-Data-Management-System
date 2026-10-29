"""题库：题目、版本与共享（设计第 4 节）。

- 题目（questions）存会变化的部分：创建者、共享对象、最新版本摘要；
- 版本（question_versions）每个一个文档，只插入不修改。"修改"= 基于某个版本插入新版本，
  "恢复"= 用旧版本的内容插入新版本，历史从不改写，因此不存在"后保存的覆盖先保存的"。
- 只有创建者能建新版本、恢复、删除、设置共享；被共享者可以查看、复制为自己的题。
- 看得到一道题 = 自己创建的，或直接共享给自己，或在共享给自己的题库里（题库只能放创建者自己的题，
  所以通过题库看到的题也都是创建者本人同意共享的，不会被转手共享）。
- 同一道题的各版本题型相同，原有选项的 id 跨版本不变，跨问卷统计才能按选项 id 合并。
"""

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import DESCENDING
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.engine.structure import check_question
from app.services.content import InvalidDefinition, build_content, options_keep_ids
from app.services.surveys import now_utc
from db.schema import CHOICE_TYPES, MAX_SHARED_USERS, SCHEMA_VERSIONS

VERSION_RETRIES = 5   # 并发撞上同一个版本号时，重新取号的次数
NOTE_TITLE_CHARS = 40  # 自动生成的版本说明里引用标题时最多取几个字（说明最长 200 字，标题最长 500 字）


class QuestionNotFound(Exception):
    """题目不存在、已删除，或当前用户看不到。"""


class VersionNotFound(Exception):
    pass


class NotOwner(Exception):
    """看得到但不是创建者（403）。"""


class LibraryConflict(Exception):
    """当前状态不允许该操作（409）；异常信息为给用户看的原因。"""


class UserNotFound(Exception):
    """共享对象的账号不存在。"""


# ---------- 权限 ----------

def parse_id(value: str, not_found=QuestionNotFound) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise not_found from None


def can_view(db: Database, user_id: ObjectId, question: dict) -> bool:
    """自己创建的、直接共享给自己的，或在某个共享给自己的（未删除的）题库里。"""
    if question["ownerId"] == user_id or user_id in question["sharedWith"]:
        return True
    # 走 shared_updatedAt 索引找到共享给自己的题库，再看里面有没有这道题
    return db.question_banks.find_one(
        {"sharedWith": user_id, "deletedAt": None, "items.questionId": question["_id"]}, {"_id": 1}
    ) is not None


def load_visible(db: Database, user_id: ObjectId, question_id: str) -> dict:
    question = db.questions.find_one({"_id": parse_id(question_id), "deletedAt": None})
    if question is None or not can_view(db, user_id, question):
        raise QuestionNotFound
    return question


def _load_owned(db: Database, user_id: ObjectId, question_id: str) -> dict:
    question = load_visible(db, user_id, question_id)
    if question["ownerId"] != user_id:
        raise NotOwner
    return question


# ---------- 版本 ----------

def short_title(title: str) -> str:
    """自动生成的版本说明里引用题目或问卷标题时截短，保证说明不超过 200 字。"""
    return title if len(title) <= NOTE_TITLE_CHARS else title[:NOTE_TITLE_CHARS] + "…"


def _version(db: Database, question_id: ObjectId, number: int) -> dict:
    version = db.question_versions.find_one({"questionId": question_id, "version": number})
    if version is None:
        raise VersionNotFound
    return version


def _newest_version(db: Database, question_id: ObjectId) -> dict:
    # 以版本集合为准（走 uniq_question_version 索引倒序取第一条），不依赖可能落后的 latest 摘要
    return db.question_versions.find_one({"questionId": question_id}, sort=[("version", DESCENDING)])


def pick_version(db: Database, question: dict, number: int | None) -> dict:
    """指定版本号就取那个版本，不指定取最新版本。"""
    return _newest_version(db, question["_id"]) if number is None else _version(db, question["_id"], number)


def _make_content(data: dict, base_options: list[dict], taken: set[str]) -> dict:
    """请求里的题目定义 → 版本内容；原有选项保留 id，新选项生成不与 taken 重复的 id。"""
    options = None
    if data["type"] in CHOICE_TYPES:
        options = options_keep_ids(data["options"], base_options, taken)
    content = build_content(data, options)
    errors = check_question(content)
    if errors:
        raise InvalidDefinition("；".join(errors))
    return content


def _insert_version(db: Database, question_id: ObjectId, parent_id: ObjectId | None, kind: str,
                    content: dict, note: str, user_id: ObjectId) -> dict:
    """取当前最大版本号加 1 插入；唯一索引 uniq_question_version 保证同一个号只有一个成功，撞号就重新取号。"""
    for _ in range(VERSION_RETRIES):
        newest = _newest_version(db, question_id)
        doc = {
            "schemaVersion": SCHEMA_VERSIONS["question_versions"],
            "questionId": question_id,
            "version": newest["version"] + 1 if newest else 1,
            "parentVersionId": parent_id,
            "kind": kind,
            "content": content,
            "note": note,
            "createdBy": user_id,
            "createdAt": now_utc(),
        }
        try:
            db.question_versions.insert_one(doc)
            return doc
        except DuplicateKeyError:
            continue
    raise LibraryConflict("这道题正在被同时修改，请稍后重试")


def _latest_summary(version: dict) -> dict:
    return {"versionId": version["_id"], "version": version["version"],
            "type": version["content"]["type"], "title": version["content"]["title"]}


def _bump_latest(db: Database, version: dict) -> None:
    """只在新版本号更大时更新摘要：并发时先后到达的顺序不影响结果；中途失败也会在下一次建版本时补上。"""
    db.questions.update_one(
        {"_id": version["questionId"], "latest.version": {"$lt": version["version"]}},
        {"$set": {"latest": _latest_summary(version), "updatedAt": version["createdAt"]}},
    )


def insert_question(db: Database, user_id: ObjectId, content: dict, note: str, kind: str,
                  derived_from: dict | None) -> dict:
    """先插第一个版本，再插题目；题目插入失败就删掉刚插的版本（补偿），不会出现没有版本的题目。"""
    question_id = ObjectId()
    version = _insert_version(db, question_id, None, kind, content, note, user_id)
    doc = {
        "_id": question_id,
        "schemaVersion": SCHEMA_VERSIONS["questions"],
        "ownerId": user_id,
        "latest": _latest_summary(version),
        "derivedFrom": derived_from,
        "sharedWith": [],
        "createdAt": version["createdAt"],
        "updatedAt": version["createdAt"],
    }
    try:
        db.questions.insert_one(doc)
    except Exception:
        db.question_versions.delete_one({"_id": version["_id"]})
        raise
    return doc


# ---------- 对外操作 ----------

def create_question(db: Database, user_id: ObjectId, data: dict, note: str) -> dict:
    return insert_question(db, user_id, _make_content(data, [], set()), note, "create", None)


def list_questions(db: Database, user_id: ObjectId, scope: str) -> list[dict]:
    # 分别走 owner_updatedAt / shared_updatedAt 索引，过滤和排序一次完成
    field = "ownerId" if scope == "mine" else "sharedWith"
    return list(db.questions.find({field: user_id, "deletedAt": None}).sort("updatedAt", DESCENDING))


def get_question(db: Database, user_id: ObjectId, question_id: str) -> tuple[dict, dict]:
    """返回 (题目, 最新版本)。"""
    question = load_visible(db, user_id, question_id)
    return question, _newest_version(db, question["_id"])


def list_versions(db: Database, user_id: ObjectId, question_id: str) -> list[dict]:
    question = load_visible(db, user_id, question_id)
    return list(db.question_versions.find({"questionId": question["_id"]}).sort("version", 1))


def get_version(db: Database, user_id: ObjectId, question_id: str, number: int) -> dict:
    question = load_visible(db, user_id, question_id)
    return _version(db, question["_id"], number)


def create_version(db: Database, user_id: ObjectId, question_id: str, base_number: int,
                   data: dict, note: str) -> dict:
    question = _load_owned(db, user_id, question_id)
    base = _version(db, question["_id"], base_number)
    if data["type"] != base["content"]["type"]:
        raise InvalidDefinition("同一道题的各版本题型必须相同；要换题型，请把这道题复制为新题")
    # 新选项的 id 也避开这道题所有版本里用过的 id：已删掉的旧选项不会被误认成新选项而合并统计
    taken = set(db.question_versions.distinct("content.options.id", {"questionId": question["_id"]}))
    content = _make_content(data, base["content"].get("options") or [], taken)
    if content == base["content"]:
        raise LibraryConflict(f"内容与 v{base_number} 相同，没有生成新版本")
    version = _insert_version(db, question["_id"], base["_id"], "edit", content, note, user_id)
    _bump_latest(db, version)
    return version


def restore_version(db: Database, user_id: ObjectId, question_id: str, number: int) -> dict:
    question = _load_owned(db, user_id, question_id)
    target = _version(db, question["_id"], number)
    newest = _newest_version(db, question["_id"])
    if target["content"] == newest["content"]:
        raise LibraryConflict(f"v{number} 与最新版本 v{newest['version']} 内容相同，不需要恢复")
    version = _insert_version(db, question["_id"], target["_id"], "restore", target["content"],
                              f"恢复自 v{number}", user_id)
    _bump_latest(db, version)
    return version


def fork_question(db: Database, user_id: ObjectId, question_id: str, number: int | None) -> dict:
    """把看得到的某个版本复制为自己的新题；选项 id 原样保留。"""
    question = load_visible(db, user_id, question_id)
    source = pick_version(db, question, number)
    derived_from = {"questionId": question["_id"], "versionId": source["_id"], "version": source["version"]}
    note = f"复制自「{short_title(source['content']['title'])}」v{source['version']}"
    return insert_question(db, user_id, source["content"], note, "fork", derived_from)


def delete_question(db: Database, user_id: ObjectId, question_id: str) -> None:
    """软删除：版本保留；问卷里存的是快照，不受影响。

    同时从创建者自己的题库里移除（题库只放创建者自己的题，所以只需查他自己的题库）。
    两步之间没有事务；万一第二步没完成，题库详情也会跳过已删除的题。
    """
    question = _load_owned(db, user_id, question_id)
    now = now_utc()
    db.questions.update_one({"_id": question["_id"], "deletedAt": None}, {"$set": {"deletedAt": now}})
    db.question_banks.update_many(
        {"ownerId": user_id, "items.questionId": question["_id"]},
        {"$pull": {"items": {"questionId": question["_id"]}}, "$set": {"updatedAt": now}},
    )


# ---------- 共享（题目与题库共用） ----------

def share_target(db: Database, owner_id: ObjectId, username: str) -> dict:
    user = db.users.find_one({"username": username}, {"username": 1, "displayName": 1})
    if user is None:
        raise UserNotFound
    if user["_id"] == owner_id:
        raise InvalidDefinition("不能共享给自己")
    return user


def add_share(collection, doc_id: ObjectId, user_id: ObjectId, not_found) -> None:
    """$addToSet 加入共享对象；人数上限写进过滤条件，判断与修改一次完成，并发也不会超出上限。
    已经共享过的再加一次视为成功。"""
    result = collection.update_one(
        {"_id": doc_id, "deletedAt": None,
         "$or": [{"sharedWith": user_id}, {f"sharedWith.{MAX_SHARED_USERS - 1}": {"$exists": False}}]},
        {"$addToSet": {"sharedWith": user_id}},
    )
    if result.matched_count == 0:
        if collection.find_one({"_id": doc_id, "deletedAt": None}, {"_id": 1}) is None:
            raise not_found
        raise LibraryConflict(f"最多共享给 {MAX_SHARED_USERS} 人")


def remove_share(collection, doc_id: ObjectId, user_id: ObjectId, not_found) -> None:
    """取消共享；本来就没共享给这个人也视为成功。已用到这道题的问卷存的是快照，不受影响。"""
    if collection.update_one({"_id": doc_id, "deletedAt": None},
                             {"$pull": {"sharedWith": user_id}}).matched_count == 0:
        raise not_found


def share_question(db: Database, user_id: ObjectId, question_id: str, username: str) -> None:
    question = _load_owned(db, user_id, question_id)
    target = share_target(db, user_id, username)
    add_share(db.questions, question["_id"], target["_id"], QuestionNotFound)


def unshare_question(db: Database, user_id: ObjectId, question_id: str, username: str) -> None:
    question = _load_owned(db, user_id, question_id)
    target = share_target(db, user_id, username)
    remove_share(db.questions, question["_id"], target["_id"], QuestionNotFound)


# ---------- 输出格式 ----------

def owner_names(db: Database, docs: list[dict]) -> dict[ObjectId, str]:
    """题目或题库的创建者：一次查询带出「名字（账号）」。"""
    ids = list({d["ownerId"] for d in docs})
    return {u["_id"]: f"{u['displayName']}（{u['username']}）"
            for u in db.users.find({"_id": {"$in": ids}}, {"username": 1, "displayName": 1})}


def shared_users(db: Database, doc: dict, user_id: ObjectId) -> list[dict] | None:
    """共享给了谁：只有创建者能看到；被共享者看不到其他被共享者（返回 None）。"""
    if doc["ownerId"] != user_id:
        return None
    users = {u["_id"]: u for u in db.users.find({"_id": {"$in": doc["sharedWith"]}},
                                                {"username": 1, "displayName": 1})}
    return [{"username": users[uid]["username"], "display_name": users[uid]["displayName"]}
            for uid in doc["sharedWith"] if uid in users]


def _ref(ref: dict | None) -> dict | None:
    if ref is None:
        return None
    return {"question_id": str(ref["questionId"]), "version_id": str(ref["versionId"]), "version": ref["version"]}


def to_summary(question: dict, owners: dict[ObjectId, str], user_id: ObjectId) -> dict:
    latest = question["latest"]
    return {
        "id": str(question["_id"]),
        "latest": {"version_id": str(latest["versionId"]), "version": latest["version"],
                   "type": latest["type"], "title": latest["title"]},
        "owner": owners.get(question["ownerId"], "（账号已不存在）"),
        "is_owner": question["ownerId"] == user_id,
        "derived_from": _ref(question["derivedFrom"]),
        "created_at": question["createdAt"],
        "updated_at": question["updatedAt"],
    }


def to_version_summaries(versions: list[dict]) -> list[dict]:
    numbers = {v["_id"]: v["version"] for v in versions}
    return [{
        "id": str(v["_id"]),
        "version": v["version"],
        "parent_version": numbers.get(v["parentVersionId"]),
        "kind": v["kind"],
        "note": v["note"],
        "title": v["content"]["title"],
        "created_at": v["createdAt"],
    } for v in versions]


def to_version(version: dict, parent_number: int | None) -> dict:
    return {
        "id": str(version["_id"]), "version": version["version"], "parent_version": parent_number,
        "kind": version["kind"], "note": version["note"], "title": version["content"]["title"],
        "created_at": version["createdAt"], "content": version["content"],
    }


def parent_number(db: Database, version: dict) -> int | None:
    if version["parentVersionId"] is None:
        return None
    parent = db.question_versions.find_one({"_id": version["parentVersionId"]}, {"version": 1})
    return parent["version"] if parent else None
