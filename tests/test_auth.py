"""账号与登录接口测试。"""

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.config import settings
from app.security import JWT_ALGORITHM

REGISTER = "/api/auth/register"
LOGIN = "/api/auth/login"
ME = "/api/auth/me"

PASSWORD = "Passw0rd!"


def register(client, username="zhangsan01", password=PASSWORD, display_name="张三"):
    return client.post(REGISTER, json={"username": username, "password": password, "displayName": display_name})


def login(client, username="zhangsan01", password=PASSWORD):
    return client.post(LOGIN, json={"username": username, "password": password})


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


# ---------- 注册 ----------

def test_register_success_stores_hash_not_plaintext(client, db):
    resp = register(client)
    assert resp.status_code == 201
    body = resp.json()
    assert body["username"] == "zhangsan01"
    assert body["displayName"] == "张三"
    assert "createdAt" in body and "id" in body
    assert "passwordHash" not in body and "password" not in body

    doc = db.users.find_one({"username": "zhangsan01"})
    assert doc["passwordHash"] != PASSWORD
    assert doc["passwordHash"].startswith("$2b$")
    assert doc["createdAt"].tzinfo is not None


def test_register_strips_spaces(client):
    body = register(client, username="  lisi_02 ", display_name="  李四 ").json()
    assert body["username"] == "lisi_02"
    assert body["displayName"] == "李四"


def test_register_duplicate_username_409(client):
    assert register(client).status_code == 201
    resp = register(client, display_name="另一个张三")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "该账号已被注册"


def test_same_display_name_different_accounts_ok(client):
    assert register(client, username="zhangsan01").status_code == 201
    assert register(client, username="zhangsan02").status_code == 201


def test_username_is_case_sensitive(client):
    assert register(client, username="Alice").status_code == 201
    assert register(client, username="alice").status_code == 201


@pytest.mark.parametrize("username", ["ab", "a" * 33, "张三三", "has space", "bad-dash", ""])
def test_register_invalid_username_422(client, username):
    resp = register(client, username=username)
    assert resp.status_code == 422
    assert "账号须为" in resp.text


@pytest.mark.parametrize(
    "password",
    ["Short1!", "a" * 73, "密码Passw0rd", "Pass w0rd!", "Passw0rd！"],  # 太短 / 太长 / 汉字 / 空格 / 全角符号
)
def test_register_invalid_password_422(client, password):
    resp = register(client, password=password)
    assert resp.status_code == 422
    assert "密码须为" in resp.text


def test_register_password_boundaries_ok(client):
    assert register(client, username="user_min", password="a" * 8).status_code == 201
    assert register(client, username="user_max", password="a" * 72).status_code == 201


@pytest.mark.parametrize("display_name", ["", "   ", "名" * 33])
def test_register_invalid_display_name_422(client, display_name):
    resp = register(client, display_name=display_name)
    assert resp.status_code == 422
    assert "名字须为" in resp.text


# ---------- 登录 ----------

def test_login_success_returns_token(client):
    register(client)
    resp = login(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["tokenType"] == "bearer"
    assert body["accessToken"]
    assert body["user"]["username"] == "zhangsan01"


def test_login_wrong_password_and_unknown_user_same_message(client):
    register(client)
    wrong_pw = login(client, password="WrongPass1")
    no_user = login(client, username="nobody_here")
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json()["detail"] == no_user.json()["detail"] == "账号或密码错误"


def test_login_is_case_sensitive(client):
    register(client, username="Alice")
    assert login(client, username="alice").status_code == 401


def test_login_overlong_password_rejected_without_error(client):
    register(client)
    assert login(client, password="x" * 100).status_code == 401


# ---------- 当前用户 / 令牌 ----------

def test_me_with_valid_token(client):
    register(client)
    token = login(client).json()["accessToken"]
    resp = client.get(ME, headers=auth_header(token))
    assert resp.status_code == 200
    assert resp.json()["displayName"] == "张三"


def test_me_without_token_401(client):
    resp = client.get(ME)
    assert resp.status_code == 401
    assert resp.json()["detail"] == "未登录"


def test_me_with_tampered_token_401(client):
    register(client)
    token = login(client).json()["accessToken"]
    header, payload, sig = token.split(".")
    # 改签名的第一个字符（末尾字符含填充位，改了可能不影响解码结果）
    tampered = ".".join([header, payload, ("B" if sig[0] == "A" else "A") + sig[1:]])
    assert client.get(ME, headers=auth_header(tampered)).status_code == 401


def test_me_with_expired_token_401(client, db):
    register(client)
    user_id = str(db.users.find_one({"username": "zhangsan01"})["_id"])
    expired = jwt.encode(
        {"sub": user_id, "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        settings.jwt_secret, algorithm=JWT_ALGORITHM,
    )
    assert client.get(ME, headers=auth_header(expired)).status_code == 401


def test_me_with_token_signed_by_other_key_401(client, db):
    register(client)
    user_id = str(db.users.find_one({"username": "zhangsan01"})["_id"])
    forged = jwt.encode(
        {"sub": user_id, "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        "someone-elses-secret-key-0123456789abcdef", algorithm=JWT_ALGORITHM,
    )
    assert client.get(ME, headers=auth_header(forged)).status_code == 401


def test_me_after_user_deleted_401(client, db):
    register(client)
    token = login(client).json()["accessToken"]
    db.users.delete_many({})
    assert client.get(ME, headers=auth_header(token)).status_code == 401
