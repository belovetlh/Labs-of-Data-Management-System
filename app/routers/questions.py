from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pymongo.database import Database

from app.db import get_db
from app.deps import get_current_user
from app.models import (FromLibraryRequest, JumpRulesRequest, QuestionIn, QuestionOrderRequest, SaveToLibraryRequest,
                        SurveyDetail, SwitchVersionRequest)
from app.services import library as lib
from app.services import questions as qsvc
from app.services import survey_library as slib
from app.services import surveys as ssvc

router = APIRouter(prefix="/api/surveys/{survey_id}", tags=["题目管理（仅草稿）"])


def _call(fn, *args):
    """调用业务函数，把业务异常转成 HTTP 状态码。"""
    try:
        return fn(*args)
    except ssvc.SurveyNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "问卷不存在") from None
    except qsvc.QuestionNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "题目不存在") from None
    except ssvc.SurveyConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    except qsvc.InvalidDefinition as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    # 阶段二：涉及题库的操作
    except lib.QuestionNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "题库里没有这道题，或你看不到它") from None
    except lib.VersionNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "题库里没有这个版本") from None
    except lib.NotOwner:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "只有题库里这道题的创建者可以存为新版本；请选择只改本问卷") from None
    except lib.LibraryConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None


def _detail(db: Database, user: dict, survey_id: str) -> dict:
    detail = ssvc.to_detail(_call(ssvc.get_survey, db, user["_id"], survey_id))
    return slib.attach_library_info(db, user["_id"], detail)


@router.post("/questions", response_model=SurveyDetail, status_code=status.HTTP_201_CREATED,
             summary="添加题目")
def add(survey_id: str, body: QuestionIn,
        position: int | None = Query(default=None, ge=0, description="插入位置（从 0 开始）；不传则加在末尾"),
        user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    _call(qsvc.add_question, db, user["_id"], survey_id, body.model_dump(), position)
    return _detail(db, user, survey_id)


@router.put("/questions/{question_id}", response_model=SurveyDetail, summary="修改题目（不含跳转规则）")
def update(survey_id: str, question_id: str, body: QuestionIn,
           library_action: Literal["detach", "new_version"] | None = Query(
               default=None, alias="libraryAction",
               description="仅对来自题库的题、且改了内容时需要：detach 只改本问卷（与题库脱钩）；"
                           "new_version 在题库里存为新版本并使用（仅题目创建者）。只改是否必答时不需要"),
           user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    _call(qsvc.update_question, db, user["_id"], survey_id, question_id, body.model_dump(), library_action)
    return _detail(db, user, survey_id)


@router.delete("/questions/{question_id}", response_model=SurveyDetail, summary="删除题目")
def delete(survey_id: str, question_id: str,
           user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    _call(qsvc.delete_question, db, user["_id"], survey_id, question_id)
    return _detail(db, user, survey_id)


@router.put("/question-order", response_model=SurveyDetail, summary="调整题目顺序")
def reorder(survey_id: str, body: QuestionOrderRequest,
            user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    _call(qsvc.reorder_questions, db, user["_id"], survey_id, body.question_ids)
    return _detail(db, user, survey_id)


@router.put("/questions/{question_id}/jump-rules", response_model=SurveyDetail, summary="设置跳转规则")
def set_jump_rules(survey_id: str, question_id: str, body: JumpRulesRequest,
                   user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    rules = [r.model_dump() for r in body.rules]
    _call(qsvc.set_jump_rules, db, user["_id"], survey_id, question_id, rules)
    return _detail(db, user, survey_id)


# ---------- 阶段二：问卷 ↔ 题库 ----------

@router.post("/questions/from-library", response_model=SurveyDetail, status_code=status.HTTP_201_CREATED,
             summary="从题库选一道题加入问卷（仅草稿）")
def add_from_library(survey_id: str, body: FromLibraryRequest,
                     position: int | None = Query(default=None, ge=0, description="插入位置（从 0 开始）；不传则加在末尾"),
                     user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    _call(slib.add_from_library, db, user["_id"], survey_id, body.question_id, body.version, body.required, position)
    return _detail(db, user, survey_id)


@router.put("/questions/{question_id}/source", response_model=SurveyDetail,
            summary="把来自题库的题换成另一个版本（仅草稿）")
def switch_version(survey_id: str, question_id: str, body: SwitchVersionRequest,
                   user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    _call(slib.switch_version, db, user["_id"], survey_id, question_id, body.version)
    return _detail(db, user, survey_id)


@router.post("/questions/{question_id}/save-to-library", response_model=SurveyDetail,
             summary="把问卷里的题存为题库新题并关联（已发布的问卷也可以，内容不变）")
def save_to_library(survey_id: str, question_id: str, body: SaveToLibraryRequest | None = None,
                    user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    _call(slib.save_to_library, db, user["_id"], survey_id, question_id, body.note if body else "")
    return _detail(db, user, survey_id)
