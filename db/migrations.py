"""结构迁移：按编号依次执行，执行过的步骤记录在 migrations 集合里，不再重复执行。

001 是阶段一的初始结构，由 init_db 建集合、校验器、索引完成，没有单独的迁移步骤。
每一步本身也写成可重复执行的（只改还没改过的文档），中途失败后重跑是安全的。
"""

from datetime import datetime, timezone

from pymongo.database import Database
from pymongo.errors import DuplicateKeyError


def _002_surveys_question_source(db: Database) -> int:
    """阶段二：问卷里的题可以带 source / derivedFrom（来自题库）。

    这是只增加可选字段的变化，旧问卷的内容一个字节都不用改，只把 schemaVersion 从 1 升到 2；
    revision、updatedAt 都不动，不影响正在编辑的草稿和乐观锁。
    """
    return db.surveys.update_many({"schemaVersion": 1}, {"$set": {"schemaVersion": 2}}).modified_count


MIGRATIONS = [
    ("002_surveys_question_source", _002_surveys_question_source),
]


def migrate(db: Database) -> list[str]:
    """执行还没执行过的迁移步骤，返回操作说明。"""
    actions: list[str] = []
    done = {d["_id"] for d in db.migrations.find({}, {"_id": 1})}
    for name, step in MIGRATIONS:
        if name in done:
            actions.append(f"迁移 {name} 已执行过")
            continue
        modified = step(db)
        try:
            db.migrations.insert_one({"_id": name, "appliedAt": datetime.now(timezone.utc), "modified": modified})
        except DuplicateKeyError:
            pass  # 并发启动时另一个进程刚记录过；步骤本身可重复执行，结果相同
        actions.append(f"执行迁移 {name}：改动 {modified} 个文档")
    return actions
