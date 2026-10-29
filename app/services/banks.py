"""题库（question_banks）：把常用题整理在一起，可以整体共享（设计 4.6 节）。

- 题库里存题目引用（不固定版本），显示的总是题目的最新版本；
- 只有题库创建者能改名、加题、移题、删除、设置共享；被共享者只能浏览、使用里面的题；
- 只能加入自己创建的题：共享题库时，里面每道题都是创建者本人同意共享的，不会把别人的题转手共享出去。
"""

from bson import ObjectId
from pymongo import DESCENDING
from pymongo.database import Database

from app.services import library as lib
from app.services.surveys import now_utc
from db.schema import MAX_BANK_ITEMS, SCHEMA_VERSIONS


class BankNotFound(Exception):
    """题库不存在、已删除，或当前用户看不到。"""


class NotBankOwner(Exception):
    """看得到但不是创建者（403）。"""


class ItemNotInBank(Exception):
    pass


def _load_visible(db: Database, user_id: ObjectId, bank_id: str) -> dict:
    bank = db.question_banks.find_one({"_id": lib.parse_id(bank_id, BankNotFound), "deletedAt": None,
                                       "$or": [{"ownerId": user_id}, {"sharedWith": user_id}]})
    if bank is None:
        raise BankNotFound
    return bank


def _load_owned(db: Database, user_id: ObjectId, bank_id: str) -> dict:
    bank = _load_visible(db, user_id, bank_id)
    if bank["ownerId"] != user_id:
        raise NotBankOwner
    return bank


# ---------- 对外操作 ----------

def create_bank(db: Database, user_id: ObjectId, name: str, description: str) -> dict:
    now = now_utc()
    doc = {
        "schemaVersion": SCHEMA_VERSIONS["question_banks"],
        "ownerId": user_id,
        "name": name,
        "description": description,
        "items": [],
        "sharedWith": [],
        "createdAt": now,
        "updatedAt": now,
    }
    doc["_id"] = db.question_banks.insert_one(doc).inserted_id
    return doc


def list_banks(db: Database, user_id: ObjectId, scope: str) -> list[dict]:
    # 分别走 owner_updatedAt / shared_updatedAt 索引
    field = "ownerId" if scope == "mine" else "sharedWith"
    return list(db.question_banks.find({field: user_id, "deletedAt": None}).sort("updatedAt", DESCENDING))


def get_bank(db: Database, user_id: ObjectId, bank_id: str) -> tuple[dict, list[tuple[dict, dict]]]:
    """返回 (题库, [(题目, 条目)])；按加入顺序，跳过已删除的题。"""
    bank = _load_visible(db, user_id, bank_id)
    ids = [item["questionId"] for item in bank["items"]]
    questions = {q["_id"]: q for q in db.questions.find({"_id": {"$in": ids}, "deletedAt": None})}
    return bank, [(questions[i["questionId"]], i) for i in bank["items"] if i["questionId"] in questions]


def update_bank(db: Database, user_id: ObjectId, bank_id: str, changes: dict) -> None:
    bank = _load_owned(db, user_id, bank_id)
    fields = {k: v for k, v in changes.items() if v is not None}
    if fields:
        db.question_banks.update_one({"_id": bank["_id"], "deletedAt": None},
                                     {"$set": {**fields, "updatedAt": now_utc()}})


def delete_bank(db: Database, user_id: ObjectId, bank_id: str) -> None:
    """软删除；里面的题不受影响，只是不再通过这个题库共享出去。"""
    bank = _load_owned(db, user_id, bank_id)
    db.question_banks.update_one({"_id": bank["_id"], "deletedAt": None}, {"$set": {"deletedAt": now_utc()}})


def add_item(db: Database, user_id: ObjectId, bank_id: str, question_id: str) -> None:
    bank = _load_owned(db, user_id, bank_id)
    question = lib.load_visible(db, user_id, question_id)
    if question["ownerId"] != user_id:
        raise lib.LibraryConflict("只能把自己创建的题加入题库；别人共享的题请先复制为自己的题")
    # 条目带加入时间，$addToSet 比较整个对象认不出重复，所以把"还没加入""没满"写进过滤条件，用 $push 加入
    result = db.question_banks.update_one(
        {"_id": bank["_id"], "deletedAt": None, "items.questionId": {"$ne": question["_id"]},
         f"items.{MAX_BANK_ITEMS - 1}": {"$exists": False}},
        {"$push": {"items": {"questionId": question["_id"], "addedAt": now_utc()}},
         "$set": {"updatedAt": now_utc()}},
    )
    if result.matched_count == 0:
        # 条件不满足时再读一次，给出具体原因
        current = db.question_banks.find_one({"_id": bank["_id"], "deletedAt": None})
        if current is None:
            raise BankNotFound
        if any(i["questionId"] == question["_id"] for i in current["items"]):
            return  # 已经在题库里，视为成功
        raise lib.LibraryConflict(f"一个题库最多 {MAX_BANK_ITEMS} 道题")


def remove_item(db: Database, user_id: ObjectId, bank_id: str, question_id: str) -> None:
    bank = _load_owned(db, user_id, bank_id)
    qid = lib.parse_id(question_id, ItemNotInBank)
    result = db.question_banks.update_one(
        {"_id": bank["_id"], "deletedAt": None, "items.questionId": qid},
        {"$pull": {"items": {"questionId": qid}}, "$set": {"updatedAt": now_utc()}},
    )
    if result.matched_count == 0:
        raise ItemNotInBank


def share_bank(db: Database, user_id: ObjectId, bank_id: str, username: str) -> None:
    bank = _load_owned(db, user_id, bank_id)
    target = lib.share_target(db, user_id, username)
    lib.add_share(db.question_banks, bank["_id"], target["_id"], BankNotFound)


def unshare_bank(db: Database, user_id: ObjectId, bank_id: str, username: str) -> None:
    bank = _load_owned(db, user_id, bank_id)
    target = lib.share_target(db, user_id, username)
    lib.remove_share(db.question_banks, bank["_id"], target["_id"], BankNotFound)


# ---------- 输出格式 ----------

def to_summary(bank: dict, owners: dict[ObjectId, str], user_id: ObjectId) -> dict:
    return {
        "id": str(bank["_id"]),
        "name": bank["name"],
        "description": bank["description"],
        "owner": owners.get(bank["ownerId"], "（账号已不存在）"),
        "is_owner": bank["ownerId"] == user_id,
        "item_count": len(bank["items"]),
        "created_at": bank["createdAt"],
        "updated_at": bank["updatedAt"],
    }


def to_detail(db: Database, bank: dict, entries: list[tuple[dict, dict]], user_id: ObjectId) -> dict:
    # 题库里的题和题库同属一个创建者，共用一次创建者查询
    owners = lib.owner_names(db, [bank])
    return {
        **to_summary(bank, owners, user_id),
        "item_count": len(entries),
        "items": [{**lib.to_summary(q, owners, user_id), "added_at": item["addedAt"]} for q, item in entries],
        "shared_with": lib.shared_users(db, bank, user_id),
    }
