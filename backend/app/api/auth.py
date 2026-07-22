from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.core.config import settings
from app.core.tester_auth import get_or_create_tester, require_existing_tester, require_tester_id, verify_project_access
from app.models.base import get_db
from app.models.models import Project, TesterUser
from app.services.browser_handoff import BrowserHandoffError, issue_browser_handoff, redeem_browser_handoff
from app.services.content_plan_markdown import project_ui_url


router = APIRouter(prefix="/auth", tags=["auth"])


class TesterLoginRequest(BaseModel):
    display_name: str
    passcode: str = ""


class BrowserHandoffCreateRequest(BaseModel):
    project_id: str = Field(min_length=1)
    stage: str = Field(default="project", pattern="^(project|content|visual|review)$")
    frontend_base_url: str = "http://localhost:8000"
    agent_text: bool = False
    agent_image: bool = False
    agent_name: str = Field(default="外部 Agent", max_length=60)


class BrowserHandoffRedeemRequest(BaseModel):
    token: str = Field(min_length=1)
    project_id: str = Field(min_length=1)


def _handoff_url(base_url: str, project_id: str, stage: str, token: str) -> str:
    stage_name = None if stage in {"project", "review"} else stage
    parts = urlsplit(project_ui_url(project_id, base_url, stage=stage_name))
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["handoff"] = token
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


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
        raise HTTPException(status_code=401, detail="项目衔接已失效，请从原 Agent 或 CLI 重新打开项目")
    return {
        "tester_id": tester.id,
        "display_name": tester.display_name,
        "created_at": tester.created_at,
        "last_login_at": tester.last_login_at,
    }


@router.post("/browser-handoff")
def create_browser_handoff(
    payload: BrowserHandoffCreateRequest,
    tester_id: str = Depends(require_tester_id),
    db: Session = Depends(get_db),
):
    tester = require_existing_tester(db, tester_id)
    project = db.query(Project).filter(Project.id == payload.project_id).first()
    verify_project_access(project, tester.id)
    token, handoff = issue_browser_handoff(
        tester_id=tester.id,
        project_id=project.id,
        stage=payload.stage,
        ttl_seconds=settings.BROWSER_HANDOFF_TTL_SECONDS,
        agent_text=payload.agent_text,
        agent_image=payload.agent_image,
        agent_name=payload.agent_name,
    )
    return {
        "ok": True,
        "project_id": project.id,
        "stage": handoff.stage,
        "expires_in_seconds": settings.BROWSER_HANDOFF_TTL_SECONDS,
        "handoff_url": _handoff_url(payload.frontend_base_url, project.id, handoff.stage, token),
        "agent_capabilities": {
            "text_generation": handoff.agent_text,
            "image_generation": handoff.agent_image,
        },
        "agent_name": handoff.agent_name,
    }


@router.post("/browser-handoff/redeem")
def consume_browser_handoff(payload: BrowserHandoffRedeemRequest, db: Session = Depends(get_db)):
    try:
        handoff = redeem_browser_handoff(payload.token, target_project_id=payload.project_id)
    except BrowserHandoffError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    tester = require_existing_tester(db, handoff.tester_id)
    project = db.query(Project).filter(Project.id == handoff.project_id).first()
    verify_project_access(project, tester.id)
    return {
        "ok": True,
        "tester_id": tester.id,
        "display_name": tester.display_name,
        "project_id": project.id,
        "stage": handoff.stage,
        "agent_capabilities": {
            "text_generation": handoff.agent_text,
            "image_generation": handoff.agent_image,
        },
        "agent_name": handoff.agent_name,
    }
