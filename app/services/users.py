"""用户数据访问：只有这里直接读写 users 集合。"""

from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.security import hash_password, verify_dummy, verify_password
from db.schema import SCHEMA_VERSION


class UsernameTaken(Exception):
    pass


def _now() -> datetime:
    # MongoDB 日期精度为毫秒，提前截断，保证返回值与库里一致
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


def create_user(db: Database, username: str, password: str, display_name: str) -> dict:
    doc = {
        "schemaVersion": SCHEMA_VERSION,
        "username": username,
        "displayName": display_name,
        "passwordHash": hash_password(password),
        "createdAt": _now(),
    }
    try:
        db.users.insert_one(doc)  # 写入后 doc 中带上 _id
    except DuplicateKeyError:
        # 由唯一索引 uniq_username 保证，并发注册同一账号也只有一个成功
        raise UsernameTaken(username) from None
    return doc


def find_by_username(db: Database, username: str) -> dict | None:
    return db.users.find_one({"username": username})


def find_by_id(db: Database, user_id: str) -> dict | None:
    try:
        oid = ObjectId(user_id)
    except (InvalidId, TypeError):
        return None
    return db.users.find_one({"_id": oid})


def authenticate(db: Database, username: str, password: str) -> dict | None:
    """账号密码正确返回用户，否则返回 None（不区分账号不存在和密码错误）。"""
    user = find_by_username(db, username)
    if user is None:
        verify_dummy(password)
        return None
    if not verify_password(password, user["passwordHash"]):
        return None
    return user


def to_user_out(user: dict) -> dict:
    """转为接口返回格式，永远不包含 passwordHash。"""
    return {
        "id": str(user["_id"]),
        "username": user["username"],
        "display_name": user["displayName"],
        "created_at": user["createdAt"],
    }
