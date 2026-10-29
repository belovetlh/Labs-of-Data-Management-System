from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pymongo.database import Database

from app.db import get_db
from app.deps import get_current_user
from app.models import (CrossQuestionStats, ForkRequest, QuestionCreate, QuestionDetail, QuestionSummary, QuestionUsage, ShareRequest,
                        VersionCreate, VersionOut, VersionSummary)
from app.services import library as lib
from app.services import stats as stats_svc
from app.services import survey_library as slib
from app.services.content import InvalidDefinition

router = APIRouter(prefix="/api/questions", tags=["题库：题目与版本"])


def _call(fn, *args):
    """调用业务函数，把业务异常转成 HTTP 状态码。"""
    try:
        return fn(*args)
    except lib.QuestionNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "题目不存在") from None
    except lib.VersionNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "版本不存在") from None
    except lib.NotOwner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "只有题目的创建者可以修改、删除或共享这道题") from None
    except lib.UserNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "账号不存在") from None
    except lib.LibraryConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    except InvalidDefinition as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None


def _detail(db: Database, user: dict, question_id: str) -> dict:
    question, newest = _call(lib.get_question, db, user["_id"], question_id)
    return {**lib.to_summary(question, lib.owner_names(db, [question]), user["_id"]),
            "content": newest["content"], "shared_with": lib.shared_users(db, question, user["_id"])}


def _version(db: Database, version: dict) -> dict:
    return lib.to_version(version, lib.parent_number(db, version))


@router.post("", response_model=QuestionDetail, status_code=status.HTTP_201_CREATED,
             summary="新建题目（同时生成 v1）")
def create(body: QuestionCreate, user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    question = _call(lib.create_question, db, user["_id"], body.content.model_dump(), body.note)
    return _detail(db, user, str(question["_id"]))


@router.get("", response_model=list[QuestionSummary], summary="我的题目 / 共享给我的题目")
def list_(scope: Literal["mine", "shared"] = Query(default="mine", description="mine 我创建的；shared 共享给我的"),
          user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    questions = lib.list_questions(db, user["_id"], scope)
    owners = lib.owner_names(db, questions)
    return [lib.to_summary(q, owners, user["_id"]) for q in questions]


@router.get("/{question_id}", response_model=QuestionDetail, summary="题目详情（含最新版本内容）")
def detail(question_id: str, user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    return _detail(db, user, question_id)


@router.delete("/{question_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="删除题目（软删除；已使用它的问卷不受影响）")
def delete(question_id: str, user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    _call(lib.delete_question, db, user["_id"], question_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{question_id}/versions", response_model=list[VersionSummary],
            summary="版本历史（parentVersion 表示基于哪个版本创建）")
def versions(question_id: str, user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    return lib.to_version_summaries(_call(lib.list_versions, db, user["_id"], question_id))


@router.post("/{question_id}/versions", response_model=VersionOut, status_code=status.HTTP_201_CREATED,
             summary="基于某个版本创建新版本（仅创建者）")
def create_version(question_id: str, body: VersionCreate,
                   user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    version = _call(lib.create_version, db, user["_id"], question_id, body.base_version,
                    body.content.model_dump(), body.note)
    return _version(db, version)


@router.get("/{question_id}/versions/{version}", response_model=VersionOut, summary="查看某个版本")
def get_version(question_id: str, version: int, user: dict = Depends(get_current_user),
                db: Database = Depends(get_db)):
    return _version(db, _call(lib.get_version, db, user["_id"], question_id, version))


@router.post("/{question_id}/versions/{version}/restore", response_model=VersionOut,
             status_code=status.HTTP_201_CREATED, summary="恢复旧版本：用它的内容生成一个新版本（仅创建者）")
def restore(question_id: str, version: int, user: dict = Depends(get_current_user),
            db: Database = Depends(get_db)):
    return _version(db, _call(lib.restore_version, db, user["_id"], question_id, version))


@router.post("/{question_id}/fork", response_model=QuestionDetail, status_code=status.HTTP_201_CREATED,
             summary="把某个版本复制为我的新题目")
def fork(question_id: str, body: ForkRequest | None = None,
         user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    question = _call(lib.fork_question, db, user["_id"], question_id, body.version if body else None)
    return _detail(db, user, str(question["_id"]))


@router.post("/{question_id}/shares", response_model=QuestionDetail, summary="把题目共享给某个账号（仅创建者）")
def share(question_id: str, body: ShareRequest, user: dict = Depends(get_current_user),
          db: Database = Depends(get_db)):
    _call(lib.share_question, db, user["_id"], question_id, body.username)
    return _detail(db, user, question_id)


@router.delete("/{question_id}/shares/{username}", response_model=QuestionDetail,
               summary="取消共享（仅创建者；已用到这道题的问卷不受影响）")
def unshare(question_id: str, username: str, user: dict = Depends(get_current_user),
            db: Database = Depends(get_db)):
    _call(lib.unshare_question, db, user["_id"], question_id, username)
    return _detail(db, user, question_id)


@router.get("/{question_id}/usage", response_model=QuestionUsage,
            summary="被哪些问卷使用（自己的问卷给详情，别人的只告诉创建者数量）")
def usage(question_id: str, user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    return _call(slib.question_usage, db, user["_id"], question_id)


@router.get("/{question_id}/stats", response_model=CrossQuestionStats, response_model_exclude_none=True,
            summary="跨问卷统计：这道题在自己所有问卷里的回答情况（默认合并全部版本）")
def cross_stats(question_id: str,
                version: int | None = Query(default=None, ge=1, description="只统计某个版本；不传则合并全部版本"),
                bucket_size: float | None = Query(default=None, gt=0, alias="bucketSize",
                                                  description="数字题分布的区间宽度；不传则自动选择"),
                page: int = Query(default=1, ge=1, description="填空题内容的页码，从 1 开始"),
                page_size: int = Query(default=20, ge=1, le=100, alias="pageSize", description="每页条数"),
                user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    return _call(stats_svc.cross_question_stats, db, user["_id"], question_id, version, bucket_size, page, page_size)
