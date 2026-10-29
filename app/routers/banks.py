from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pymongo.database import Database

from app.db import get_db
from app.deps import get_current_user
from app.models import BankCreate, BankDetail, BankItemAdd, BankSummary, BankUpdate, ShareRequest
from app.services import banks as bsvc
from app.services import library as lib
from app.services.content import InvalidDefinition

router = APIRouter(prefix="/api/banks", tags=["题库：题库与共享"])


def _call(fn, *args):
    """调用业务函数，把业务异常转成 HTTP 状态码。"""
    try:
        return fn(*args)
    except bsvc.BankNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "题库不存在") from None
    except bsvc.NotBankOwner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "只有题库的创建者可以修改题库") from None
    except bsvc.ItemNotInBank:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "这道题不在题库里") from None
    except lib.QuestionNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "题目不存在") from None
    except lib.UserNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "账号不存在") from None
    except lib.LibraryConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    except InvalidDefinition as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None


def _detail(db: Database, user: dict, bank_id: str) -> dict:
    bank, entries = _call(bsvc.get_bank, db, user["_id"], bank_id)
    return bsvc.to_detail(db, bank, entries, user["_id"])


@router.post("", response_model=BankDetail, status_code=status.HTTP_201_CREATED, summary="新建题库")
def create(body: BankCreate, user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    bank = bsvc.create_bank(db, user["_id"], body.name, body.description)
    return _detail(db, user, str(bank["_id"]))


@router.get("", response_model=list[BankSummary], summary="我的题库 / 共享给我的题库")
def list_(scope: Literal["mine", "shared"] = Query(default="mine", description="mine 我创建的；shared 共享给我的"),
          user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    banks = bsvc.list_banks(db, user["_id"], scope)
    owners = lib.owner_names(db, banks)
    return [bsvc.to_summary(b, owners, user["_id"]) for b in banks]


@router.get("/{bank_id}", response_model=BankDetail, summary="题库详情（每道题显示最新版本）")
def detail(bank_id: str, user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    return _detail(db, user, bank_id)


@router.patch("/{bank_id}", response_model=BankDetail, summary="修改题库名称、说明（仅创建者）")
def update(bank_id: str, body: BankUpdate, user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    _call(bsvc.update_bank, db, user["_id"], bank_id, body.model_dump())
    return _detail(db, user, bank_id)


@router.delete("/{bank_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="删除题库（软删除；里面的题不受影响）")
def delete(bank_id: str, user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    _call(bsvc.delete_bank, db, user["_id"], bank_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{bank_id}/items", response_model=BankDetail, summary="把自己创建的题加入题库（仅创建者）")
def add_item(bank_id: str, body: BankItemAdd, user: dict = Depends(get_current_user),
             db: Database = Depends(get_db)):
    _call(bsvc.add_item, db, user["_id"], bank_id, body.question_id)
    return _detail(db, user, bank_id)


@router.delete("/{bank_id}/items/{question_id}", response_model=BankDetail, summary="把题移出题库（仅创建者）")
def remove_item(bank_id: str, question_id: str, user: dict = Depends(get_current_user),
                db: Database = Depends(get_db)):
    _call(bsvc.remove_item, db, user["_id"], bank_id, question_id)
    return _detail(db, user, bank_id)


@router.post("/{bank_id}/shares", response_model=BankDetail, summary="把题库共享给某个账号（仅创建者）")
def share(bank_id: str, body: ShareRequest, user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    _call(bsvc.share_bank, db, user["_id"], bank_id, body.username)
    return _detail(db, user, bank_id)


@router.delete("/{bank_id}/shares/{username}", response_model=BankDetail, summary="取消共享题库（仅创建者）")
def unshare(bank_id: str, username: str, user: dict = Depends(get_current_user), db: Database = Depends(get_db)):
    _call(bsvc.unshare_bank, db, user["_id"], bank_id, username)
    return _detail(db, user, bank_id)
