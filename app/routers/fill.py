from fastapi import APIRouter, Depends, HTTPException, status
from pymongo.database import Database

from app.db import get_db
from app.deps import get_current_user
from app.models import AnswersRequest, FillView, NavigateResult, SubmitResult
from app.services import submissions as svc

router = APIRouter(prefix="/api/fill/{slug}", tags=["填写问卷"])


def _call(fn, *args):
    """调用业务函数，把业务异常转成 HTTP 状态码。"""
    try:
        return fn(*args)
    except svc.FillNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "问卷不存在") from None
    except svc.FillGone:
        raise HTTPException(status.HTTP_410_GONE, "问卷已删除") from None
    except svc.FillClosed as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    except svc.AlreadySubmitted:
        raise HTTPException(status.HTTP_409_CONFLICT, svc.ALREADY_SUBMITTED_MESSAGE) from None
    except svc.SubmissionInvalid as e:
        # 按题目分开返回，前端把错误显示在对应的题旁边
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, {"message": "提交内容有误", "errors": e.errors}
        ) from None


@router.get("", response_model=FillView, summary="打开问卷（填写视图）")
def open_survey(slug: str, user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    return _call(svc.fill_view, db, slug, user)


@router.post("/navigate", response_model=NavigateResult, summary="按目前的答案计算路径、下一题和错误（不保存）")
def navigate(slug: str, body: AnswersRequest,
             user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    ev = _call(svc.navigate_answers, db, slug, body.answers)
    return {"path": ev.path, "next_question": ev.next_question, "errors": ev.errors,
            "off_path": ev.off_path, "complete": ev.complete}


@router.post("/responses", response_model=SubmitResult, status_code=status.HTTP_201_CREATED,
             summary="提交答卷（提交后不能修改、不能删除）")
def submit(slug: str, body: AnswersRequest,
           user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    return {"submitted_at": _call(svc.submit, db, slug, user, body.answers)}
