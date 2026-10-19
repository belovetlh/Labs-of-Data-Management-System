"""数据库初始化：建集合（带校验器）、更新校验器、建索引。

可重复执行，不删除任何数据：
- 集合不存在 → 带校验器创建；
- 集合已存在 → 用 collMod 把校验器更新为 db/schema.py 中的版本；
- 索引已存在且定义相同 → 跳过；同名但定义不同 → MongoDB 报错（提示需要写迁移）。

用法：python -m db.init_db
"""

from pymongo.database import Database
from pymongo.errors import CollectionInvalid

from db.schema import INDEXES, VALIDATION_ACTION, VALIDATION_LEVEL, VALIDATORS


def init_db(db: Database) -> list[str]:
    """初始化数据库，返回执行过的操作说明。"""
    actions: list[str] = []
    existing = set(db.list_collection_names())

    for name, validator in VALIDATORS.items():
        options = {
            "validator": validator,
            "validationLevel": VALIDATION_LEVEL,
            "validationAction": VALIDATION_ACTION,
        }
        if name not in existing:
            try:
                db.create_collection(name, **options)
                actions.append(f"创建集合 {name}（带校验器）")
                continue
            except CollectionInvalid:
                pass  # 并发启动时可能已被另一个进程建好，转为更新
        db.command("collMod", name, **options)
        actions.append(f"更新集合 {name} 的校验器")

    for coll, keys, opts in INDEXES:
        if opts["name"] in db[coll].index_information():
            # 仍然调用一次：定义没变时是空操作，定义变了时 MongoDB 会报错
            db[coll].create_index(keys, **opts)
            actions.append(f"索引 {coll}.{opts['name']} 已存在")
        else:
            db[coll].create_index(keys, **opts)
            actions.append(f"创建索引 {coll}.{opts['name']}")

    return actions


if __name__ == "__main__":
    from app.config import settings
    from app.db import get_db

    print(f"初始化数据库 {settings.mongodb_db}（{settings.mongodb_uri}）")
    for line in init_db(get_db()):
        print("  " + line)
