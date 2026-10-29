"""MongoDB schema：各集合的 $jsonSchema 校验器和索引定义。

内容与 docs/设计文档.md 附录 A、2.6 节一致。这里只做结构与类型校验，
跨字段、依赖具体问卷的语义检查在应用层完成（见设计 2.7 节）。
阶段二新增题目、题目版本、题库三个集合，结构迁移见 db/migrations.py。
"""

from pymongo import ASCENDING, DESCENDING

# 各集合的文档结构版本号，写入每个文档的 schemaVersion 字段；某个集合结构变化时只递增它自己的
# surveys 2：题目可带 source / derivedFrom（来自题库），迁移见 db/migrations.py
SCHEMA_VERSIONS = {
    "users": 1,
    "surveys": 2,
    "responses": 1,
    "participations": 1,
    "questions": 1,
    "question_versions": 1,
    "question_banks": 1,
}

VALIDATION_LEVEL = "strict"
VALIDATION_ACTION = "error"

USERNAME_PATTERN = "^[A-Za-z0-9_]{3,32}$"
QUESTION_ID_PATTERN = "^q_[0-9a-f]{8}$"
OPTION_ID_PATTERN = "^o_[0-9a-f]{8}$"

QUESTION_TYPES = ["single_choice", "multiple_choice", "text_input", "number_input"]

CHOICE_TYPES = ["single_choice", "multiple_choice"]

# 数量上限（防止单个问卷文档过大）
MAX_QUESTIONS = 200
MAX_OPTIONS = 50
MAX_JUMP_RULES = 50
MAX_SHARED_USERS = 100    # 一道题 / 一个题库最多共享给多少人
MAX_BANK_ITEMS = 500      # 一个题库最多多少道题

# 版本是怎么来的：新建 / 修改 / 恢复旧版本 / 复制别人的题
VERSION_KINDS = ["create", "edit", "restore", "fork"]

# 各题型允许的跳转条件 op（文本题不支持跳转）
JUMP_OPS = {
    "single_choice": ["eq", "in"],
    "multiple_choice": ["containsAny", "containsAll", "equals"],
    "number_input": ["eq", "ne", "gt", "gte", "lt", "lte", "between"],
}

_options = {
    "bsonType": "array",
    "minItems": 2,
    "items": {
        "bsonType": "object",
        "required": ["id", "label"],
        "properties": {
            "id": {"bsonType": "string", "pattern": OPTION_ID_PATTERN},
            "label": {"bsonType": "string", "minLength": 1, "maxLength": 200},
        },
    },
}


def _rules_with_ops(ops: list[str]) -> dict:
    """限制跳转规则里 when.op 只能取给定的值。"""
    return {"items": {"properties": {"when": {"properties": {"op": {"enum": ops}}}}}}


def _type_branches(with_jump_rules: bool) -> list[dict]:
    """按 type 分支：每种题型各自的字段约束。

    问卷里的题还要约束跳转规则；题库里的题目内容没有跳转规则（跳转引用的是同一问卷里的其他题）。
    """
    branches = [
        {
            "required": ["options"],
            "properties": {
                "type": {"enum": ["single_choice"]},
                "options": _options,
            },
        },
        {
            "required": ["options"],
            "properties": {
                "type": {"enum": ["multiple_choice"]},
                "options": _options,
                "validation": {
                    "bsonType": "object",
                    "properties": {
                        "minSelect": {"bsonType": "int", "minimum": 0},
                        "maxSelect": {"bsonType": "int", "minimum": 1},
                    },
                },
            },
        },
        {
            "properties": {
                "type": {"enum": ["text_input"]},
                "validation": {
                    "bsonType": "object",
                    "properties": {
                        "minLength": {"bsonType": "int", "minimum": 0},
                        "maxLength": {"bsonType": "int", "minimum": 1},
                    },
                },
            },
        },
        {
            "properties": {
                "type": {"enum": ["number_input"]},
                "validation": {
                    "bsonType": "object",
                    "properties": {
                        "min": {"bsonType": "number"},
                        "max": {"bsonType": "number"},
                        "integer": {"bsonType": "bool"},
                    },
                },
            },
        },
    ]
    if with_jump_rules:
        for branch in branches:
            qtype = branch["properties"]["type"]["enum"][0]
            # 文本题不支持跳转
            branch["properties"]["jumpRules"] = (
                _rules_with_ops(JUMP_OPS[qtype]) if qtype in JUMP_OPS else {"maxItems": 0}
            )
    return branches


# 题目内容的公共字段：问卷里的题和题库里的题目版本共用
_content_properties = {
    "type": {"enum": QUESTION_TYPES},
    "title": {"bsonType": "string", "minLength": 1, "maxLength": 500},
    "required": {"bsonType": "bool"},
    "options": {"bsonType": "array"},  # 具体约束见各题型分支
    "validation": {"bsonType": "object"},
}

# 指向题库中某道题的某个版本
_version_ref = {
    "bsonType": "object",
    "required": ["questionId", "versionId", "version"],
    "properties": {
        "questionId": {"bsonType": "objectId"},
        "versionId": {"bsonType": "objectId"},
        "version": {"bsonType": "int", "minimum": 1},
    },
}

# 题库里一个版本的题目内容：没有题目 id（题目 id 属于具体问卷），也没有跳转规则
_question_content = {
    "bsonType": "object",
    "required": ["type", "title", "required"],
    "additionalProperties": False,
    "properties": _content_properties,
    "oneOf": _type_branches(with_jump_rules=False),
}

# 问卷里的一道题
_question = {
    "bsonType": "object",
    "required": ["id", "type", "title", "required"],
    "properties": {
        "id": {"bsonType": "string", "pattern": QUESTION_ID_PATTERN},
        **_content_properties,
        "jumpRules": {
            "bsonType": "array",
            "items": {
                "bsonType": "object",
                "required": ["when", "goto"],
                "properties": {
                    "when": {
                        "bsonType": "object",
                        "required": ["op", "value"],
                        "properties": {"op": {"bsonType": "string"}},
                    },
                    "goto": {"bsonType": "string", "pattern": "^(q_[0-9a-f]{8}|END)$"},
                },
            },
        },
        # 来自题库，且内容与该版本完全相同（阶段一的题、本问卷自建的题没有这个字段）
        "source": _version_ref,
        # 来自题库，但在本问卷里改过（已脱钩）：记录改自哪个版本
        "derivedFrom": _version_ref,
    },
    "oneOf": _type_branches(with_jump_rules=True),
}

_shared_with = {
    "bsonType": "array",
    "maxItems": MAX_SHARED_USERS,
    "uniqueItems": True,
    "items": {"bsonType": "objectId"},
}


def _schema_version(collection: str) -> dict:
    # 不接受比当前代码更新的结构版本
    return {"bsonType": "int", "minimum": 1, "maximum": SCHEMA_VERSIONS[collection]}


VALIDATORS: dict[str, dict] = {
    "users": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["_id", "schemaVersion", "username", "displayName", "passwordHash", "createdAt"],
            "properties": {
                "_id": {"bsonType": "objectId"},
                "schemaVersion": _schema_version("users"),
                # 账号：登录用，唯一；名字：展示用，可重复
                "username": {"bsonType": "string", "pattern": USERNAME_PATTERN},
                "displayName": {"bsonType": "string", "minLength": 1, "maxLength": 32},
                "passwordHash": {"bsonType": "string", "minLength": 1},
                "createdAt": {"bsonType": "date"},
            },
        }
    },
    "surveys": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": [
                "_id", "schemaVersion", "revision", "ownerId", "title", "description", "slug",
                "status", "settings", "questions", "createdAt", "updatedAt",
            ],
            "properties": {
                "_id": {"bsonType": "objectId"},
                "schemaVersion": _schema_version("surveys"),
                # 修改版本号：每次修改加 1，写回时用作乐观锁条件
                "revision": {"bsonType": ["int", "long"], "minimum": 0},
                "ownerId": {"bsonType": "objectId"},
                "title": {"bsonType": "string", "minLength": 1, "maxLength": 200},
                "description": {"bsonType": "string", "maxLength": 5000},
                "slug": {"bsonType": "string", "pattern": "^[A-Za-z0-9]{8}$"},
                "status": {"enum": ["draft", "published", "closed"]},
                "settings": {
                    "bsonType": "object",
                    "required": ["allowAnonymous", "allowMultipleSubmissions", "deadline"],
                    "properties": {
                        "allowAnonymous": {"bsonType": "bool"},
                        "allowMultipleSubmissions": {"bsonType": "bool"},
                        "deadline": {"bsonType": ["date", "null"]},
                    },
                },
                "questions": {"bsonType": "array", "items": _question},
                "createdAt": {"bsonType": "date"},
                "updatedAt": {"bsonType": "date"},
                "publishedAt": {"bsonType": ["date", "null"]},
                "closedAt": {"bsonType": ["date", "null"]},
                # 标记删除：未删除的问卷没有这个字段
                "deletedAt": {"bsonType": "date"},
            },
        }
    },
    "responses": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["_id", "schemaVersion", "surveyId", "submittedAt", "answers"],
            "properties": {
                "_id": {"bsonType": "objectId"},
                "schemaVersion": _schema_version("responses"),
                "surveyId": {"bsonType": "objectId"},
                # 匿名问卷的答卷没有这个字段
                "respondentId": {"bsonType": "objectId"},
                "submittedAt": {"bsonType": "date"},
                "answers": {
                    "bsonType": "array",
                    "items": {
                        "bsonType": "object",
                        "required": ["questionId", "value"],
                        "properties": {
                            "questionId": {"bsonType": "string", "pattern": QUESTION_ID_PATTERN},
                            "value": {"bsonType": ["string", "number", "array"]},
                        },
                    },
                },
            },
        }
    },
    "participations": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["_id", "schemaVersion", "surveyId", "userId", "count"],
            "properties": {
                "_id": {"bsonType": "objectId"},
                "schemaVersion": _schema_version("participations"),
                "surveyId": {"bsonType": "objectId"},
                "userId": {"bsonType": "objectId"},
                "count": {"bsonType": "int", "minimum": 1},
            },
        }
    },
    # ---------- 阶段二：题目、题目版本、题库 ----------
    "questions": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": [
                "_id", "schemaVersion", "ownerId", "latest", "derivedFrom", "sharedWith", "createdAt", "updatedAt",
            ],
            "properties": {
                "_id": {"bsonType": "objectId"},
                "schemaVersion": _schema_version("questions"),
                "ownerId": {"bsonType": "objectId"},
                # 最新版本的摘要（冗余，列表页直接显示；可由 question_versions 重算）
                "latest": {
                    "bsonType": "object",
                    "required": ["versionId", "version", "type", "title"],
                    "properties": {
                        "versionId": {"bsonType": "objectId"},
                        "version": {"bsonType": "int", "minimum": 1},
                        "type": {"enum": QUESTION_TYPES},
                        "title": {"bsonType": "string", "minLength": 1, "maxLength": 500},
                    },
                },
                # 复制自别人的哪道题、哪个版本；自己新建的为 null
                "derivedFrom": {**_version_ref, "bsonType": ["object", "null"]},
                "sharedWith": _shared_with,
                "createdAt": {"bsonType": "date"},
                "updatedAt": {"bsonType": "date"},
                # 标记删除：未删除的题目没有这个字段
                "deletedAt": {"bsonType": "date"},
            },
        }
    },
    # 只插入、不修改：问卷里的快照因此永远和它的来源版本一致
    "question_versions": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": [
                "_id", "schemaVersion", "questionId", "version", "parentVersionId", "kind",
                "content", "note", "createdBy", "createdAt",
            ],
            "properties": {
                "_id": {"bsonType": "objectId"},
                "schemaVersion": _schema_version("question_versions"),
                "questionId": {"bsonType": "objectId"},
                "version": {"bsonType": "int", "minimum": 1},
                # 基于哪个版本创建（同一道题内），组成版本树；每道题的第一个版本为 null
                "parentVersionId": {"bsonType": ["objectId", "null"]},
                "kind": {"enum": VERSION_KINDS},
                "content": _question_content,
                "note": {"bsonType": "string", "maxLength": 200},
                "createdBy": {"bsonType": "objectId"},
                "createdAt": {"bsonType": "date"},
            },
        }
    },
    "question_banks": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": [
                "_id", "schemaVersion", "ownerId", "name", "description", "items", "sharedWith",
                "createdAt", "updatedAt",
            ],
            "properties": {
                "_id": {"bsonType": "objectId"},
                "schemaVersion": _schema_version("question_banks"),
                "ownerId": {"bsonType": "objectId"},
                "name": {"bsonType": "string", "minLength": 1, "maxLength": 100},
                "description": {"bsonType": "string", "maxLength": 1000},
                # 引用题目而不固定版本：题库里显示的总是最新版本
                "items": {
                    "bsonType": "array",
                    "maxItems": MAX_BANK_ITEMS,
                    "items": {
                        "bsonType": "object",
                        "required": ["questionId", "addedAt"],
                        "properties": {
                            "questionId": {"bsonType": "objectId"},
                            "addedAt": {"bsonType": "date"},
                        },
                    },
                },
                "sharedWith": _shared_with,
                "createdAt": {"bsonType": "date"},
                "updatedAt": {"bsonType": "date"},
                "deletedAt": {"bsonType": "date"},
            },
        }
    },
    # 已执行的结构迁移，一步一条（见 db/migrations.py）
    "migrations": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["_id", "appliedAt", "modified"],
            "properties": {
                "_id": {"bsonType": "string", "minLength": 1},
                "appliedAt": {"bsonType": "date"},
                "modified": {"bsonType": ["int", "long"], "minimum": 0},
            },
        }
    },
}

# (集合, 索引键, 选项)；索引显式命名，便于文档引用和阶段二按名字修改
INDEXES: list[tuple[str, list[tuple[str, int]], dict]] = [
    ("users", [("username", ASCENDING)], {"name": "uniq_username", "unique": True}),
    ("surveys", [("slug", ASCENDING)], {"name": "uniq_slug", "unique": True}),
    ("surveys", [("ownerId", ASCENDING), ("createdAt", DESCENDING)], {"name": "owner_createdAt"}),
    ("responses", [("surveyId", ASCENDING), ("submittedAt", DESCENDING)], {"name": "survey_submittedAt"}),
    ("participations", [("surveyId", ASCENDING), ("userId", ASCENDING)], {"name": "uniq_survey_user", "unique": True}),
    # ---------- 阶段二 ----------
    ("questions", [("ownerId", ASCENDING), ("updatedAt", DESCENDING)], {"name": "owner_updatedAt"}),
    ("questions", [("sharedWith", ASCENDING), ("updatedAt", DESCENDING)], {"name": "shared_updatedAt"}),
    # 同一道题的版本号唯一：按顺序列出历史；两人同时创建同一个版本号时后到者失败
    ("question_versions", [("questionId", ASCENDING), ("version", ASCENDING)],
     {"name": "uniq_question_version", "unique": True}),
    ("question_banks", [("ownerId", ASCENDING), ("updatedAt", DESCENDING)], {"name": "owner_updatedAt"}),
    ("question_banks", [("sharedWith", ASCENDING), ("updatedAt", DESCENDING)], {"name": "shared_updatedAt"}),
    # 多键索引：某道题（某个版本）被哪些问卷使用；跨问卷统计先用它找问卷
    ("surveys", [("questions.source.questionId", ASCENDING), ("questions.source.versionId", ASCENDING)],
     {"name": "question_source"}),
]
