import os

# 测试使用独立的数据库，必须在导入 app 之前设置
os.environ["MONGODB_DB"] = "survey_system_test"
os.environ["BCRYPT_ROUNDS"] = "4"  # 测试里调低哈希强度，加快速度

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import get_client, get_db
from app.main import app
from db.init_db import init_db
from db.schema import VALIDATORS


@pytest.fixture(scope="session", autouse=True)
def _test_db():
    """整个测试会话开始前清空测试库并初始化，结束后删除。"""
    get_client().drop_database(settings.mongodb_db)
    init_db(get_db())
    yield
    get_client().drop_database(settings.mongodb_db)


@pytest.fixture(autouse=True)
def _clean_collections():
    """每个测试前清空数据，但保留集合上的校验器和索引。"""
    db = get_db()
    for name in VALIDATORS:
        db[name].delete_many({})


@pytest.fixture()
def db():
    return get_db()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c
