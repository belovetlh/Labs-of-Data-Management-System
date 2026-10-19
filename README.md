# 基于 MongoDB 的问卷系统

技术栈：Python 3.13 + FastAPI + PyMongo（MongoDB 8.x）+ pytest；前端为后端托管的静态 HTML + 原生 JS。

需求与决策基准见 [需求基准与任务拆解.md](需求基准与任务拆解.md)，AI 使用日志见 [docs/AI使用日志.md](docs/AI使用日志.md)。

## 配置与运行

### 1. 启动 MongoDB

已安装 MongoDB 服务的直接启动服务即可；或在项目目录用本地数据目录启动：

```bash
mkdir -p .mongo-data
mongod --dbpath .mongo-data --bind_ip 127.0.0.1 --port 27017 --logpath .mongo-data/mongod.log
```

### 2. 安装依赖

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
```

（macOS / Linux 下把 `.venv/Scripts/python` 换成 `.venv/bin/python`。）

### 3. 配置

复制 `.env.example` 为 `.env`，按需修改。不建 `.env` 时使用默认值（连接 `mongodb://localhost:27017`，数据库 `survey_system`）。

| 变量 | 说明 | 默认值 |
|---|---|---|
| `MONGODB_URI` | MongoDB 连接串 | `mongodb://localhost:27017` |
| `MONGODB_DB` | 数据库名 | `survey_system` |
| `JWT_SECRET` | 登录令牌签名密钥 | `dev-secret-change-me` |
| `JWT_EXPIRE_MINUTES` | 令牌有效期（分钟） | `1440` |

### 4. 初始化数据库

```bash
.venv/Scripts/python -m db.init_db
```

建好 4 个集合（users / surveys / responses / participations，各带 `$jsonSchema` 校验器）和 5 个索引。可以重复执行，不会删除已有数据；集合已存在时会把校验器更新为 [db/schema.py](db/schema.py) 中的版本。

后端启动时也会自动执行一次，忘了这一步也能正常运行。

### 5. 启动后端

```bash
.venv/Scripts/python -m uvicorn app.main:app --reload
```

- 健康检查：<http://127.0.0.1:8000/api/health>
- 自动生成的 API 文档：<http://127.0.0.1:8000/docs>

### 6. 运行测试

测试连接真实 MongoDB，使用独立的 `survey_system_test` 数据库：开始前清空并初始化，每个测试前清空数据（保留校验器和索引），结束后删除。

```bash
.venv/Scripts/python -m pytest
```
