"""题目内容的组装：问卷里的题（阶段一）和题库里的题目版本（阶段二）共用。

题目内容 = 题型、标题、是否必答、选项、校验规则；不含题目 id 和跳转规则（它们属于具体问卷）。
"""

import secrets

from db.schema import CHOICE_TYPES


class InvalidDefinition(Exception):
    """请求本身不合法（422）；异常信息为给用户看的原因。"""


VALIDATION_KEYS = {
    "min_select": "minSelect", "max_select": "maxSelect",
    "min_length": "minLength", "max_length": "maxLength",
    "min": "min", "max": "max", "integer": "integer",
}


def new_id(prefix: str, taken: set[str]) -> str:
    while True:
        candidate = f"{prefix}_{secrets.token_hex(4)}"
        if candidate not in taken:
            taken.add(candidate)
            return candidate


def build_content(data: dict, options: list[dict] | None) -> dict:
    """data 为请求模型 model_dump() 的结果（下划线字段名）。"""
    doc = {"type": data["type"], "title": data["title"], "required": data["required"]}
    if data["type"] in CHOICE_TYPES:
        doc["options"] = options
    if "validation" in data:
        doc["validation"] = {
            VALIDATION_KEYS[k]: v for k, v in data["validation"].items() if v is not None
        }
    return doc


def options_keep_ids(new_options: list[dict], old_options: list[dict], taken: set[str]) -> list[dict]:
    """原有选项保留 id，新选项生成 id；传了不属于本题的 id 视为错误。"""
    old_ids = {o["id"] for o in old_options}
    result = []
    for o in new_options:
        if o["id"] is None:
            result.append({"id": new_id("o", taken), "label": o["label"]})
        elif o["id"] in old_ids:
            result.append({"id": o["id"], "label": o["label"]})
        else:
            raise InvalidDefinition(f"选项 id {o['id']} 不属于这道题")
    if len({o["id"] for o in result}) != len(result):
        raise InvalidDefinition("同一个选项 id 出现了多次")
    return result


# ---------- 问卷里来自题库的题（快照 + 引用，设计 4.7 节） ----------

# 判断问卷里的题与来源版本是否一致时只比较这些字段：是否必答是问卷自己的设置，同一道题可以在
# A 问卷必答、B 问卷选填，改必答不算改题（不需要建新版本或脱钩），也不影响跨问卷合并统计
COMPARED_FIELDS = ("type", "title", "options", "validation")


def same_content(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in COMPARED_FIELDS)


def version_ref(version: dict) -> dict:
    return {"questionId": version["questionId"], "versionId": version["_id"], "version": version["version"]}


def snapshot(version: dict, question_id: str, required: bool, jump_rules: list[dict]) -> dict:
    """把题库里的一个版本复制成问卷里的一道题：内容（含选项 id）原样复制，题目 id 属于本问卷，
    跳转规则在问卷里另外设置，source 记下来源版本。"""
    return {"id": question_id, **version["content"], "required": required,
            "jumpRules": jump_rules, "source": version_ref(version)}
