from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.db import get_db
from app.routers import auth, fill, questions, stats, surveys
from db.init_db import init_db

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时确保集合、校验器、索引就绪（可重复执行，不动已有数据）
    init_db(get_db())
    yield


app = FastAPI(title="问卷系统", version="0.1.0", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(surveys.router)
app.include_router(questions.router)
app.include_router(fill.router)
app.include_router(stats.router)


@app.get("/api/health")
def health():
    get_db().command("ping")
    return {"status": "ok"}


# ---------- 页面 ----------

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse("/static/surveys.html")


@app.get("/survey/{slug}", include_in_schema=False)
def fill_page(slug: str):
    # 分享链接：返回填写页，slug 由页面里的 JS 从地址栏读取
    return FileResponse(STATIC_DIR / "fill.html")
