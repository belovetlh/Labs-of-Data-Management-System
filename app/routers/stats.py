from fastapi import APIRouter, Depends, HTTPException, Query, status
from pymongo.database import Database

from app.db import get_db
from app.deps import get_current_user
from app.models import QuestionStat, SurveyStats
from app.services import stats as svc
from app.services.surveys import SurveyNotFound

router = APIRouter(prefix="/api/surveys/{survey_id}/stats", tags=["统计"])


def _call(fn, *args):
    try:
        return fn(*args)
    except SurveyNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "问卷不存在") from None
    except svc.QuestionNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "题目不存在") from None


@router.get("", response_model=SurveyStats, response_model_exclude_none=True, summary="整份问卷的统计")
def survey_stats(survey_id: str, user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    return _call(svc.survey_stats, db, user["_id"], survey_id)


@router.get("/{question_id}", response_model=QuestionStat, response_model_exclude_none=True,
            summary="单题统计（填空题分页查看全部内容）")
def question_stats(survey_id: str, question_id: str,
                   page: int = Query(default=1, ge=1, description="页码，从 1 开始"),
                   page_size: int = Query(default=20, ge=1, le=100, alias="pageSize", description="每页条数"),
                   user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    return _call(svc.question_stats, db, user["_id"], survey_id, question_id, page, page_size)
