"""请求格式错误（422）的提示统一为中文，且不回显原始输入。"""

import pytest

from app.errors import MESSAGES, translate


@pytest.fixture()
def headers(login_as):
    return login_as()


@pytest.fixture()
def sid(client, headers):
    return client.post("/api/surveys", json={"title": "t"}, headers=headers).json()["id"]


def msgs(resp):
    assert resp.status_code == 422
    return [e["msg"] for e in resp.json()["detail"]]


def test_deadline_without_timezone(client, headers):
    r = client.post("/api/surveys", json={"title": "x", "settings": {"deadline": "2030-01-01T00:00:00"}},
                    headers=headers)
    assert msgs(r) == ["deadline：时间必须带时区，例如 2026-11-01T00:00:00+08:00"]


def test_deadline_bad_format(client, headers):
    r = client.post("/api/surveys", json={"title": "x", "settings": {"deadline": "明天"}}, headers=headers)
    assert msgs(r) == ["deadline：时间格式不正确，例如 2026-11-01T00:00:00+08:00"]


def test_extra_field(client, headers, sid):
    r = client.post(f"/api/surveys/{sid}/questions", headers=headers,
                    json={"type": "text_input", "title": "建议", "options": [{"label": "a"}, {"label": "b"}]})
    assert msgs(r) == ["不允许的字段：options"]


def test_missing_field(client):
    r = client.post("/api/auth/register", json={"username": "abc_1", "displayName": "x"})
    assert msgs(r) == ["缺少必填字段：password"]


@pytest.mark.parametrize("question,expect", [
    ({"type": "rating", "title": "a"},
     "不支持的题型：rating，可选：single_choice / multiple_choice / text_input / number_input"),
    ({"title": "a"}, "缺少题型字段 type，可选：single_choice / multiple_choice / text_input / number_input"),
    ({"type": "single_choice", "title": "a", "options": [{"label": "a"}]}, "options：至少需要 2 项"),
    ({"type": "multiple_choice", "title": "a", "options": [{"label": "a"}, {"label": "b"}],
      "validation": {"minSelect": "abc"}}, "minSelect：应为整数"),
    ({"type": "multiple_choice", "title": "a", "options": [{"label": "a"}, {"label": "b"}],
      "validation": {"maxSelect": 0}}, "maxSelect：不能小于 1"),
    ({"type": "number_input", "title": "a", "validation": {"min": "inf"}}, "min：应为有限的数字"),
])
def test_question_format_errors(client, headers, sid, question, expect):
    assert msgs(client.post(f"/api/surveys/{sid}/questions", json=question, headers=headers)) == [expect]


@pytest.mark.parametrize("body,expect", [
    ({"title": 123}, "title：应为文字"),
    ({"title": "x", "settings": {"allowAnonymous": "maybe"}}, "allowAnonymous：应为 true 或 false"),
])
def test_survey_format_errors(client, headers, body, expect):
    assert msgs(client.post("/api/surveys", json=body, headers=headers)) == [expect]


def test_list_and_length_errors(client, headers, sid):
    r = client.put(f"/api/surveys/{sid}/question-order", json={"questionIds": "q_1"}, headers=headers)
    assert msgs(r) == ["questionIds：应为数组"]
    r = client.post("/api/auth/login", json={"username": "a" * 65, "password": "x"})
    assert msgs(r) == ["username：最多 64 个字符"]


def test_query_param_error(client, headers, sid):
    r = client.get(f"/api/surveys/{sid}/stats/q_00000000", params={"pageSize": "abc"}, headers=headers)
    assert msgs(r) == ["pageSize：应为整数"]


def test_invalid_json_body(client):
    r = client.post("/api/auth/register", content=b"{bad json", headers={"Content-Type": "application/json"})
    assert msgs(r) == ["请求体不是合法的 JSON"]


def test_our_own_chinese_messages_unchanged(client):
    r = client.post("/api/auth/register", json={"username": "王老师", "password": "123", "displayName": "王"})
    assert msgs(r) == ["账号须为 3–32 位字母、数字或下划线",
                       "密码须为 8–72 位英文字母、数字或英文符号，不能包含汉字或空格"]


def test_input_is_not_echoed(client):
    """FastAPI 默认会回显原始输入（包括密码），统一处理后不再回显。"""
    r = client.post("/api/auth/register", json={"username": "abc_1", "password": "mysecret", "displayName": ""})
    assert r.status_code == 422
    assert "mysecret" not in r.text
    assert all(set(e) == {"type", "loc", "msg"} for e in r.json()["detail"])


def test_unknown_type_keeps_original_message():
    assert translate({"type": "some_new_type", "loc": ("body", "x"), "msg": "original"})["msg"] == "original"


def test_all_templates_are_chinese():
    assert all(any("一" <= ch <= "鿿" for ch in t) for t in MESSAGES.values())
