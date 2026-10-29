# 基于 MongoDB 的问卷系统

当代数据管理系统课程大作业（阶段一、阶段二均已完成；阶段一提交点为标签 `v1-stage1`）：一个简化版在线问卷系统。注册用户创建问卷并通过链接分发，其他登录用户填写，创建者查看统计结果。

- 四种题型：单选、多选（至少 / 最多 / 恰好 N 项）、文本填空（字数）、数字填空（范围、整数）
- 跳转：按单选、多选、数字结果跳转，规则作为数据存在问卷里，由服务端按规则计算下一题
- 问卷管理：草稿 → 发布 → 关闭 / 截止 → 重新开放；匿名填写；是否允许同一人多次填写
- 统计：MongoDB 聚合管道实时计算，支持整份问卷与单题统计
- 题库（阶段二）：保存常用题目，修改即生成新版本，可查看版本历史、恢复旧版本；题目和题库可共享给其他账号，别人共享的题可复制为自己的题；问卷可从题库选题、切换所用版本，问卷里的题也可存入题库；可查看一道题被哪些问卷使用；可跨问卷统计一道题的回答情况（合并各版本，数字题给出分布）
- 408 个自动测试（含并发、数据迁移与浏览器自动测试）

技术栈：Python 3.13 + FastAPI + PyMongo（MongoDB 8.x）+ pytest + Playwright；前端为后端托管的静态 HTML + 原生 JS。

## 目录结构

```
app/            后端
  main.py         创建应用、注册路由、托管页面、启动时初始化数据库
  routers/        路由层：46 个接口（auth / surveys / questions / fill / stats，阶段二的 library 题目与版本、banks 题库与共享）
  services/       业务层：所有数据库读写
  engine/         规则引擎：问卷结构检查、答案校验、跳转计算（纯函数）
  models.py       请求 / 响应格式；errors.py 中文错误提示；security.py 密码与令牌
db/             MongoDB schema：schema.py（$jsonSchema 校验器与索引）、init_db.py（初始化）、migrations.py（结构迁移）
static/         前端页面（登录注册、我的问卷、编辑问卷、填写、统计；阶段二的题库首页、题目详情、题库详情、跨问卷统计）
tests/          自动测试
docs/           设计文档.md（系统与数据库设计、全部设计取舍）、需求基准与任务拆解.md、screenshots/
```

设计说明见 [docs/设计文档.md](docs/设计文档.md)，需求基准见 [docs/需求基准与任务拆解.md](docs/需求基准与任务拆解.md)。AI 使用说明与 AI 使用日志（节选）见实验报告。

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
| `JWT_SECRET` | 登录令牌签名密钥（正式使用务必修改，建议 32 位以上） | 仅供开发的默认值 |
| `JWT_EXPIRE_MINUTES` | 令牌有效期（分钟） | `1440` |
| `BCRYPT_ROUNDS` | 密码哈希强度 | `12` |

### 4. 初始化数据库

```bash
.venv/Scripts/python -m db.init_db
```

建好 8 个集合（阶段一的 users / surveys / responses / participations，阶段二新增的 questions / question_versions / question_banks，以及记录迁移的 migrations，各带 `$jsonSchema` 校验器）和 11 个索引，再执行还没执行过的结构迁移（[db/migrations.py](db/migrations.py)，例如把阶段一的问卷升级到结构版本 2）。可以重复执行，不会删除已有数据；集合已存在时会把校验器更新为 [db/schema.py](db/schema.py) 中的版本。

后端启动时也会自动执行一次，忘了这一步也能正常运行。

### 5. 启动后端

```bash
.venv/Scripts/python -m uvicorn app.main:app --reload
```

- 网页入口：<http://127.0.0.1:8000/>（未登录会先进入登录 / 注册页）。问卷发布后，分享链接形如 `http://127.0.0.1:8000/survey/xxxxxxxx`
- 健康检查：<http://127.0.0.1:8000/api/health>
- 自动生成的 API 文档：<http://127.0.0.1:8000/docs>。需要登录的接口：先调用 `/api/auth/login` 拿到 `accessToken`，点右上角 Authorize 填入，即可直接试用。

### 6. 运行测试

测试连接真实 MongoDB，使用独立的 `survey_system_test` 数据库：开始前清空并初始化，每个测试前清空数据（保留校验器和索引），结束后删除。

```bash
.venv/Scripts/python -m pytest
```

共 408 个测试，一条命令全部运行：

| 文件 | 类型 | 数量 | 内容 |
|---|---|---|---|
| `test_db_schema.py` | 数据库 | 41 | 集合、校验器、索引是否按设计生效（含阶段二新增集合） |
| `test_migrations.py` | 数据迁移 | 5 | 阶段一格式的旧数据迁移后内容不变、仍可填写和统计；迁移可重复执行 |
| `test_engine.py` | 单元测试 | 75 | 校验引擎、跳转引擎（纯函数） |
| `test_auth.py` `test_surveys.py` `test_questions.py` `test_fill.py` `test_stats.py` `test_errors.py` `test_pages.py` `test_health.py` | API 测试 | 189 | 各接口的正常与异常情况、并发、故障注入、explain 索引验证、中文错误提示 |
| `test_library.py` | API 测试（阶段二） | 26 | 题目与版本：新建、版本树、选项 id 跨版本保留、恢复、复制、权限、软删除、并发建版本、补偿 |
| `test_banks.py` | API 测试（阶段二） | 20 | 共享与题库：共享 / 取消共享、通过题库获得的权限及其收回、防止转手共享、上限在并发下不被突破 |
| `test_survey_library.py` | API 测试（阶段二） | 22 | 问卷接入题库：快照与来源、去重、修改时脱钩 / 存为新版本、改必答不脱钩、切换版本与跳转冲突、存入题库（含已发布问卷）与撤回、取消共享 / 删除后问卷照常填写 |
| `test_usage.py` | API 测试（阶段二） | 8 | 被哪些问卷使用：按版本计数、自己的问卷详情、别人的只告诉创建者数量、脱钩 / 删除不计入、explain 索引验证 |
| `test_cross_stats.py` | API 测试（阶段二） | 14 | 跨问卷统计：数字题平均值与分布（自动 / 指定区间宽度）、选择题按选项 id 跨版本合并、多选、跳转下的分母、填空题分页与匿名、只统计自己的问卷、脱钩 / 删除不计入、旧问卷存入题库后计入、explain 索引验证 |
| `test_browser.py` | 浏览器自动测试 | 8 | 用 Playwright 控制无界面 Chromium，走完"注册 → 建问卷 → 设跳转 → 发布 → 换账号填写 → 看统计"等流程；阶段二加上"题目版本、题库与共享 → 问卷从题库选题、切换版本、修改时选择保存方式、存入题库 → 跨问卷统计"三个流程；截图保存到 `docs/screenshots/`（01–08 阶段一，09–16 阶段二） |

浏览器测试需要先下载一次 Chromium（约 120 MB）：

```bash
.venv/Scripts/python -m playwright install chromium
```

没有安装 Playwright 或 Chromium 时，浏览器测试会自动跳过并给出提示，其余测试照常运行。只跑浏览器测试：`.venv/Scripts/python -m pytest tests/test_browser.py`。
