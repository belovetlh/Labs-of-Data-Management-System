from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import get_db
from app.routers import auth, fill, questions, stats, surveys
from db.init_db import init_db


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
