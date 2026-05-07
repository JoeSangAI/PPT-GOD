from __future__ import annotations

from contextvars import ContextVar
import hashlib
import re
import secrets
from typing import Optional

from fastapi import Header, HTTPException
from sqlalchemy.orm import Session

from app.models.models import Project, TesterUser, utc_now


TESTER_ID_HEADER = "x-pptgod-tester-id"

_current_tester_id: ContextVar[str | None] = ContextVar("pptgod_tester_id", default=None)


def set_current_tester_id(tester_id: str | None):
    return _current_tester_id.set(tester_id)


def reset_current_tester_id(token) -> None:
    _current_tester_id.reset(token)


def get_current_tester_id() -> str | None:
    return _current_tester_id.get()


def normalize_login_key(display_name: str) -> str:
    key = re.sub(r"\s+", " ", (display_name or "").strip().lower())
    if not key:
        raise HTTPException(status_code=400, detail="请输入姓名或昵称")
    if len(key) > 80:
        raise HTTPException(status_code=400, detail="姓名或昵称不能超过 80 个字符")
    return key


def hash_passcode(passcode: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{passcode}".encode("utf-8")).hexdigest()


def split_passcode_hash(value: str) -> tuple[str, str]:
    if ":" not in value:
        return "", value
    salt, digest = value.split(":", 1)
    return salt, digest


def get_or_create_tester(db: Session, display_name: str, passcode: str = "") -> TesterUser:
    cleaned_name = (display_name or "").strip()
    login_key = normalize_login_key(cleaned_name)
    tester = db.query(TesterUser).filter(TesterUser.login_key == login_key).first()
    if tester:
        tester.display_name = cleaned_name
        tester.last_login_at = utc_now()
        db.commit()
        db.refresh(tester)
        return tester

    salt = secrets.token_hex(12)
    tester = TesterUser(
        display_name=cleaned_name,
        login_key=login_key,
        passcode_hash=f"{salt}:{hash_passcode((passcode or '').strip(), salt)}",
    )
    db.add(tester)
    db.commit()
    db.refresh(tester)
    return tester


def tester_id_from_header(x_pptgod_tester_id: Optional[str] = Header(default=None)) -> str | None:
    return (x_pptgod_tester_id or "").strip() or None


def require_tester_id(x_pptgod_tester_id: Optional[str] = Header(default=None)) -> str:
    tester_id = tester_id_from_header(x_pptgod_tester_id)
    if not tester_id:
        raise HTTPException(status_code=401, detail="请先登录测试账号")
    return tester_id


def verify_project_access(project: Project | None, tester_id: str | None) -> Project:
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.tester_id and project.tester_id != tester_id:
        raise HTTPException(status_code=403, detail="这个项目属于其他测试账号，请切换账号后再试")
    return project
