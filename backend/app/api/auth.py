from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.tester_auth import get_or_create_tester, require_tester_id
from app.models.base import get_db
from app.models.models import TesterUser


router = APIRouter(prefix="/auth", tags=["auth"])


class TesterLoginRequest(BaseModel):
    display_name: str
    passcode: str = ""


@router.post("/tester-login")
def tester_login(payload: TesterLoginRequest, db: Session = Depends(get_db)):
    tester = get_or_create_tester(db, payload.display_name, payload.passcode)
    return {
        "tester_id": tester.id,
        "display_name": tester.display_name,
        "created_at": tester.created_at,
        "last_login_at": tester.last_login_at,
    }


@router.get("/me")
def auth_me(tester_id: str = Depends(require_tester_id), db: Session = Depends(get_db)):
    tester = db.query(TesterUser).filter(TesterUser.id == tester_id).first()
    if not tester:
        raise HTTPException(status_code=401, detail="登录状态已失效，请重新输入固定用户名")
    return {
        "tester_id": tester.id,
        "display_name": tester.display_name,
        "created_at": tester.created_at,
        "last_login_at": tester.last_login_at,
    }
