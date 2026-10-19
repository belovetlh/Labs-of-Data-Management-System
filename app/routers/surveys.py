from fastapi import APIRouter, Body, Depends, HTTPException, Response, status
from pymongo.database import Database

from app.db import get_db
from app.deps import get_current_user
from app.models import ReopenRequest, SurveyCreate, SurveyDetail, SurveySummary, SurveyUpdate
from app.services import surveys as svc

router = APIRouter(prefix="/api/surveys", tags=["问卷管理"])


def _call(fn, *args):
    """调用业务函数，把业务异常转成 HTTP 状态码。"""
    try:
        return fn(*args)
    except svc.SurveyNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "问卷不存在") from None
    except svc.SurveyConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None


def _detail(db: Database, user: dict, survey_id: str) -> dict:
    return svc.to_detail(_call(svc.get_survey, db, user["_id"], survey_id))


@router.post("", response_model=SurveyDetail, status_code=status.HTTP_201_CREATED, summary="创建问卷（草稿）")
def create(body: SurveyCreate, user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    survey = svc.create_survey(db, user["_id"], body.title, body.description, body.settings.model_dump())
    return svc.to_detail(survey)


@router.get("", response_model=list[SurveySummary], summary="我的问卷列表（最新在前）")
def list_mine(user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    return [svc.to_summary(s) for s in svc.list_surveys(db, user["_id"])]


@router.get("/{survey_id}", response_model=SurveyDetail, summary="问卷详情")
def detail(survey_id: str, user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    return _detail(db, user, survey_id)


@router.patch("/{survey_id}", response_model=SurveyDetail, summary="修改问卷（仅草稿）")
def update(survey_id: str, body: SurveyUpdate,
           user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    _call(svc.update_draft, db, user["_id"], survey_id, body.model_dump(exclude_unset=True))
    return _detail(db, user, survey_id)


@router.post("/{survey_id}/publish", response_model=SurveyDetail, summary="发布")
def publish(survey_id: str, user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    _call(svc.publish, db, user["_id"], survey_id)
    return _detail(db, user, survey_id)


@router.post("/{survey_id}/close", response_model=SurveyDetail, summary="关闭")
def close(survey_id: str, user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    _call(svc.close, db, user["_id"], survey_id)
    return _detail(db, user, survey_id)


@router.post("/{survey_id}/reopen", response_model=SurveyDetail, summary="重新开放（可同时设新的截止时间）")
def reopen(survey_id: str, body: ReopenRequest | None = Body(default=None),
           user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    deadline_given = body is not None and "deadline" in body.model_fields_set
    deadline = body.deadline if deadline_given else None
    _call(svc.reopen, db, user["_id"], survey_id, deadline_given, deadline)
    return _detail(db, user, survey_id)


@router.delete("/{survey_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除问卷（标记删除）")
def delete(survey_id: str, user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    _call(svc.delete, db, user["_id"], survey_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
