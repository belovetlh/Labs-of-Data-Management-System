"""MongoDB schema：四个集合的 $jsonSchema 校验器和索引定义。

内容与 docs/设计草案.md 附录 A、2.6 节一致。这里只做结构与类型校验，
跨字段、依赖具体问卷的语义检查在应用层完成（见设计 2.7 节）。
"""

from pymongo import ASCENDING, DESCENDING

# 文档结构版本号，写入每个文档的 schemaVersion 字段；阶段二结构变化时递增
SCHEMA_VERSION = 1

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


_question = {
    "bsonType": "object",
    "required": ["id", "type", "title", "required"],
    "properties": {
        "id": {"bsonType": "string", "pattern": QUESTION_ID_PATTERN},
        "type": {"enum": QUESTION_TYPES},
        "title": {"bsonType": "string", "minLength": 1, "maxLength": 500},
        "required": {"bsonType": "bool"},
        "validation": {"bsonType": "object"},
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
    },
    # 按 type 分支：每种题型各自的字段约束
    "oneOf": [
        {
            "required": ["options"],
            "properties": {
                "type": {"enum": ["single_choice"]},
                "options": _options,
                "jumpRules": _rules_with_ops(JUMP_OPS["single_choice"]),
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
                "jumpRules": _rules_with_ops(JUMP_OPS["multiple_choice"]),
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
                "jumpRules": {"maxItems": 0},
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
                "jumpRules": _rules_with_ops(JUMP_OPS["number_input"]),
            },
        },
    ],
}

_schema_version = {"bsonType": "int", "minimum": 1}

VALIDATORS: dict[str, dict] = {
    "users": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["_id", "schemaVersion", "username", "displayName", "passwordHash", "createdAt"],
            "properties": {
                "_id": {"bsonType": "objectId"},
                "schemaVersion": _schema_version,
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
                "schemaVersion": _schema_version,
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
                "schemaVersion": _schema_version,
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
                "schemaVersion": _schema_version,
                "surveyId": {"bsonType": "objectId"},
                "userId": {"bsonType": "objectId"},
                "count": {"bsonType": "int", "minimum": 1},
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
]
