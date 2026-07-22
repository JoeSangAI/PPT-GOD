from __future__ import annotations

from collections import Counter
import hashlib
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm import Session

from app.api import projects as project_api
from app.api import slides as slides_api
from app.core.tester_auth import is_local_admin_request, require_existing_tester, require_tester_id, verify_project_access
from app.core.config import settings
from app.models.base import get_db
from app.models.models import Project, Slide
from app.services.artifact_versions import dependency_signature, strip_artifact_meta, with_artifact_meta, with_stale_flags
from app.services.content_plan_markdown import (
    ContentPlanMarkdownError,
    ContentPlanSyncConflictError,
    content_body_storage_state,
    export_content_plan_markdown,
    import_content_plan_markdown,
    project_ui_url,
    sync_content_plan_markdown,
)
from app.services.run_state import get_active_run, reconcile_project_state, serialize_run
from app.services.runtime_readiness import build_runtime_readiness
from app.services.slide_artifacts import SlideArtifactError, import_slide_image_artifact
from app.services.slide_types import CANONICAL_SLIDE_TYPES


router = APIRouter(prefix="/agent", tags=["agent"])
DEFAULT_AGENT_FRONTEND_BASE_URL = "http://localhost:8000"
AGENT_CONTRACT_VERSION = "1"

AGENT_OPERATIONS = {
    "project": ["status", "open"],
    "content_plan": ["import", "export", "update_preview", "update_apply", "confirm"],
    "visual_direction": ["start_proposals", "get_proposals", "confirm_proposal"],
    "visual_plan": ["import", "generate_prompts"],
    "generation": [
        "generate_prototype",
        "generate_pages",
        "import_page_image",
        "status",
        "retry_failed",
        "confirm_prototype",
        "stop",
    ],
    "export": ["export_pptx_contract", "download_pptx"],
}


class ImportContentPlanRequest(BaseModel):
    markdown: str = Field(min_length=1)
    title: str | None = None
    source_filename: str | None = None
    frontend_base_url: str = DEFAULT_AGENT_FRONTEND_BASE_URL


class AgentActionRequest(BaseModel):
    frontend_base_url: str = DEFAULT_AGENT_FRONTEND_BASE_URL


class UpdateContentPlanRequest(AgentActionRequest):
    markdown: str = Field(min_length=1)
    apply: bool = False
    expected_preview_token: str | None = None


class StartVisualProposalsRequest(AgentActionRequest):
    force: bool = False
    user_description: str | None = None


class ConfirmVisualProposalRequest(AgentActionRequest):
    proposal_index: int | None = Field(default=None, ge=1)
    selected_style: Any = None


class StartVisualPromptsRequest(AgentActionRequest):
    page_nums: list[int] | None = None
    stage_context: str | None = None


class ImportVisualPlanPage(BaseModel):
    page_num: int = Field(ge=1)
    visual_description: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    visual_json: dict[str, Any] | None = None


class ImportVisualPlanRequest(AgentActionRequest):
    pages: list[ImportVisualPlanPage] = Field(min_length=1)
    selected_style: dict[str, Any] | None = None


class StartSlideGenerationRequest(AgentActionRequest):
    page_nums: list[int] | None = None
    prototype: bool = False


def _frontend_base_url(payload: AgentActionRequest | None, fallback: str) -> str:
    return (payload.frontend_base_url if payload else None) or fallback or DEFAULT_AGENT_FRONTEND_BASE_URL


@router.get("/capabilities")
def get_agent_capabilities():
    runtime_identity = hashlib.sha256(settings.DATABASE_URL.encode("utf-8")).hexdigest()[:12]
    return {
        "ok": True,
        "contract_version": AGENT_CONTRACT_VERSION,
        "service": {
            "name": settings.PROJECT_NAME,
            "version": settings.VERSION,
            "runtime_instance_id": runtime_identity,
            "database_backend": settings.DATABASE_URL.split(":", 1)[0],
        },
        "slide_types": list(CANONICAL_SLIDE_TYPES),
        "operations": AGENT_OPERATIONS,
        "async_contract": {
            "start_returns_run": True,
            "terminal_statuses": ["succeeded", "failed", "cancelled", "stale"],
            "status_endpoint": "/agent/projects/{project_id}/generation-status",
        },
    }


@router.get("/readiness")
def get_runtime_readiness(
    agent_text: bool = Query(False),
    agent_image: bool = Query(False),
):
    return build_runtime_readiness(agent_text=agent_text, agent_image=agent_image)


def _load_accessible_project(db: Session, project_id: str, tester_id: str | None) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    return verify_project_access(project, tester_id)


def _project_slides(db: Session, project_id: str) -> list[Slide]:
    return db.query(Slide).filter(Slide.project_id == project_id).order_by(Slide.page_num).all()


def _slide_status_summary(slides: list[Slide]) -> dict:
    status_counts = Counter(str(slide.status or "unknown") for slide in slides)
    return {
        "total": len(slides),
        "by_status": dict(sorted(status_counts.items())),
        "with_content": sum(1 for slide in slides if bool(slide.content_json)),
        "with_visual": sum(1 for slide in slides if bool(slide.visual_json)),
        "with_prompt": sum(1 for slide in slides if bool(slide.prompt_text)),
        "with_image": sum(1 for slide in slides if bool(slide.image_path)),
        "with_error": sum(1 for slide in slides if bool(slide.error_msg)),
    }


def _compact_slides(slides: list[Slide]) -> list[dict]:
    compact: list[dict] = []
    for slide in slides:
        content = slide.content_json if isinstance(slide.content_json, dict) else {}
        text_content = content.get("text_content") if isinstance(content.get("text_content"), dict) else {}
        body_state = content_body_storage_state(content)
        compact.append(
            {
                "page_num": slide.page_num,
                "type": slide.type or content.get("type") or "content",
                "status": slide.status,
                "headline": text_content.get("headline") or "",
                "body": body_state["effective_body"],
                "body_storage_consistent": body_state["consistent"],
                "content_blocks_count": body_state["content_blocks_count"],
                "has_content": bool(slide.content_json),
                "has_visual": bool(slide.visual_json),
                "has_prompt": bool(slide.prompt_text),
                "has_image": bool(slide.image_path),
                "error_msg": slide.error_msg,
            }
        )
    return compact


def _next_action(project: Project, slides: list[Slide], frontend_base_url: str) -> dict:
    if not slides:
        return {
            "type": "import_or_generate_content_plan",
            "label": "导入或生成内容规划",
            "url": project_ui_url(project.id, frontend_base_url),
        }
    if not project.content_plan_confirmed:
        return {
            "type": "open_ui",
            "stage": "content",
            "label": "打开内容确认页",
            "url": project_ui_url(project.id, frontend_base_url, stage="content"),
        }
    if project.selected_style and not all(slide.prompt_text for slide in slides):
        return {
            "type": "generate_visual_prompts",
            "label": "生成画面方案和生图 Prompt",
            "url": project_ui_url(project.id, frontend_base_url, stage="visual"),
        }
    if any(slide.status == "failed" for slide in slides):
        return {
            "type": "retry_failed_slides",
            "label": "重试失败页面",
            "url": project_ui_url(project.id, frontend_base_url),
        }
    if slides and all(slide.prompt_text for slide in slides) and not all(slide.image_path for slide in slides):
        return {
            "type": "generate_slides",
            "label": "生成 PPT 页面",
            "url": project_ui_url(project.id, frontend_base_url),
        }
    if slides and all(slide.image_path for slide in slides):
        return {
            "type": "export_ppt",
            "label": "导出 PPT",
            "url": project_ui_url(project.id, frontend_base_url),
        }
    if slides and any(slide.image_path for slide in slides):
        missing_pages = [slide.page_num for slide in slides if not slide.image_path]
        return {
            "type": "import_or_generate_remaining_slides",
            "label": f"继续完成剩余 {len(missing_pages)} 页",
            "missing_page_nums": missing_pages,
            "url": project_ui_url(project.id, frontend_base_url),
        }
    if project.status in {"planning", "visual_ready"}:
        return {
            "type": "open_ui",
            "stage": "visual",
            "label": "打开视觉提案页",
            "url": project_ui_url(project.id, frontend_base_url, stage="visual"),
        }
    if project.status in {"prompt_ready", "prototype_ready", "completed"}:
        return {
            "type": "open_ui",
            "stage": "review",
            "label": "打开项目检查页",
            "url": project_ui_url(project.id, frontend_base_url),
        }
    return {
        "type": "open_ui",
        "label": "打开项目",
        "url": project_ui_url(project.id, frontend_base_url),
    }


def _generation_next_action(workflow_status: dict, project_id: str, frontend_base_url: str) -> dict:
    active_run = workflow_status.get("active_run")
    if active_run:
        return {
            "type": "wait",
            "label": "等待当前任务完成",
            "url": project_ui_url(project_id, frontend_base_url),
        }
    if workflow_status.get("target_failed_slides") or workflow_status.get("failed_slides"):
        return {
            "type": "retry_failed_slides",
            "label": "重试失败页面",
            "url": project_ui_url(project_id, frontend_base_url),
        }
    if workflow_status.get("has_pptx"):
        return {
            "type": "export_ppt",
            "label": "导出 PPT",
            "url": project_ui_url(project_id, frontend_base_url),
        }
    project_status = str(workflow_status.get("project_status") or workflow_status.get("status") or "")
    if project_status in {"prompt_ready", "prototype_ready"}:
        return {
            "type": "generate_slides",
            "label": "生成 PPT 页面",
            "url": project_ui_url(project_id, frontend_base_url),
        }
    if project_status == "visual_ready":
        return {
            "type": "generate_visual_prompts",
            "label": "生成画面方案和生图 Prompt",
            "url": project_ui_url(project_id, frontend_base_url, stage="visual"),
        }
    return {
        "type": "open_ui",
        "label": "打开项目",
        "url": project_ui_url(project_id, frontend_base_url),
    }


def _download_url(project_id: str, api_base_url: str, *, tester_id: str, prototype: bool = False) -> str:
    query: dict[str, str] = {"tester_id": tester_id}
    if prototype:
        query["prototype"] = "1"
    return f"{api_base_url.rstrip('/')}/projects/{quote(project_id)}/download?{urlencode(query)}"


def _agent_project_status_response(project: Project, slides: list[Slide], active_run, frontend_base_url: str) -> dict:
    ui_urls = {
        "project": project_ui_url(project.id, frontend_base_url),
        "content": project_ui_url(project.id, frontend_base_url, stage="content"),
        "visual": project_ui_url(project.id, frontend_base_url, stage="visual"),
    }
    return {
        "ok": True,
        "project": {
            "id": project.id,
            "title": project.title,
            "status": project.status,
            "content_plan_confirmed": bool(project.content_plan_confirmed),
            "has_selected_style": bool(project.selected_style),
            "created_at": project.created_at.isoformat() if project.created_at else None,
            "updated_at": project.updated_at.isoformat() if project.updated_at else None,
        },
        "slides_summary": _slide_status_summary(slides),
        "slides": _compact_slides(slides),
        "active_run": serialize_run(active_run, slides),
        "ui_url": ui_urls["project"],
        "ui_urls": ui_urls,
        "next_action": _next_action(project, slides, frontend_base_url),
    }


@router.post("/content-plans/import")
def import_content_plan(
    payload: ImportContentPlanRequest,
    tester_id: str = Depends(require_tester_id),
    db: Session = Depends(get_db),
):
    owner_tester_id = None if is_local_admin_request(tester_id) else require_existing_tester(db, tester_id).id
    try:
        receipt = import_content_plan_markdown(
            db,
            payload.markdown,
            title=payload.title,
            tester_id=owner_tester_id,
            source_filename=payload.source_filename,
            frontend_base_url=payload.frontend_base_url,
        )
    except ContentPlanMarkdownError as exc:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail={
                "message": "内容规划 Markdown 格式不合格",
                "errors": exc.errors,
                "warnings": exc.warnings,
            },
        ) from exc

    return {
        "ok": True,
        "project_id": receipt.project_id,
        "title": receipt.title,
        "slides_count": receipt.slides_count,
        "warnings": receipt.warnings,
        "ui_url": receipt.ui_url,
        "next_action": {
            "type": "open_ui",
            "label": "打开内容确认页",
            "url": receipt.ui_url,
        },
    }


@router.get("/projects/{project_id}/status")
def get_agent_project_status(
    project_id: str,
    frontend_base_url: str = Query(DEFAULT_AGENT_FRONTEND_BASE_URL),
    tester_id: str = Depends(require_tester_id),
    db: Session = Depends(get_db),
):
    project = _load_accessible_project(db, project_id, tester_id)
    slides = _project_slides(db, project_id)
    active_run = get_active_run(db, project_id)
    before = project.status
    reconcile_project_state(project, slides, active_run)
    if project.status != before or db.dirty:
        db.commit()
        db.refresh(project)

    return _agent_project_status_response(project, slides, active_run, frontend_base_url)


@router.post("/projects/{project_id}/slides/{page_num}/image")
def import_agent_slide_image(
    project_id: str,
    page_num: int,
    file: UploadFile = File(...),
    source: str = Form("external_agent"),
    frontend_base_url: str = Form(DEFAULT_AGENT_FRONTEND_BASE_URL),
    tester_id: str = Depends(require_tester_id),
    db: Session = Depends(get_db),
):
    project = _load_accessible_project(db, project_id, tester_id)
    if not project.content_plan_confirmed:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "content_confirmation_required",
                "message": "请先确认内容规划，再导入最终页面图；否则后续内容调整会使页面图失效。",
                "next_action": {
                    "type": "confirm_content_plan",
                    "label": "先确认内容规划",
                },
            },
        )
    slide = (
        db.query(Slide)
        .filter(Slide.project_id == project_id, Slide.page_num == page_num)
        .first()
    )
    if not slide:
        raise HTTPException(status_code=404, detail=f"第 {page_num} 页不存在。")
    try:
        receipt = import_slide_image_artifact(
            db,
            project,
            slide,
            file.file.read(),
            source=source,
        )
    except SlideArtifactError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    receipt["ui_url"] = project_ui_url(project_id, frontend_base_url, stage="review")
    return receipt


@router.get("/projects/{project_id}/content-plan/export")
def export_agent_content_plan(
    project_id: str,
    frontend_base_url: str = Query(DEFAULT_AGENT_FRONTEND_BASE_URL),
    tester_id: str = Depends(require_tester_id),
    db: Session = Depends(get_db),
):
    project = _load_accessible_project(db, project_id, tester_id)
    slides = _project_slides(db, project_id)
    if not slides:
        raise HTTPException(status_code=400, detail="当前项目还没有可导出的内容页")

    receipt = export_content_plan_markdown(project, slides)
    return {
        "ok": True,
        "project_id": receipt.project_id,
        "title": receipt.title,
        "slides_count": receipt.slides_count,
        "filename": receipt.filename,
        "markdown": receipt.markdown,
        "ui_url": project_ui_url(project.id, frontend_base_url),
        "next_action": _next_action(project, slides, frontend_base_url),
    }


@router.post("/projects/{project_id}/content-plan/update")
def update_agent_content_plan(
    project_id: str,
    payload: UpdateContentPlanRequest,
    tester_id: str = Depends(require_tester_id),
    db: Session = Depends(get_db),
):
    project = _load_accessible_project(db, project_id, tester_id)
    active_run = get_active_run(db, project_id)
    if payload.apply and active_run:
        raise HTTPException(status_code=409, detail="项目当前有生成任务在运行，请等待任务结束后重新预览并应用内容更新")

    state_before = {
        "status": project.status,
        "content_plan_confirmed": bool(project.content_plan_confirmed),
    }
    try:
        receipt = sync_content_plan_markdown(
            db,
            project,
            payload.markdown,
            apply=payload.apply,
            expected_preview_token=payload.expected_preview_token,
        )
    except ContentPlanSyncConflictError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={"message": "内容同步预览已失效", "errors": exc.errors, "warnings": exc.warnings},
        ) from exc
    except ContentPlanMarkdownError as exc:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail={"message": "内容规划 Markdown 格式不合格", "errors": exc.errors, "warnings": exc.warnings},
        ) from exc

    if payload.apply:
        db.refresh(project)
    state_after = {
        "status": project.status,
        "content_plan_confirmed": bool(project.content_plan_confirmed),
    }
    return {
        "ok": True,
        "project_id": receipt.project_id,
        "applied": receipt.applied,
        "preview_token": receipt.preview_token,
        "summary": receipt.summary,
        "changes": receipt.changes,
        "warnings": receipt.warnings,
        "readback": receipt.readback,
        "project_state_before": state_before,
        "project_state_after": state_after,
        "project_state_unchanged": state_before == state_after,
        "ui_url": project_ui_url(project.id, payload.frontend_base_url),
        "content_review_url": project_ui_url(project.id, payload.frontend_base_url, stage="content"),
        "next_action": {
            "type": "apply_update" if not payload.apply else "review_content",
            "label": "确认差异后使用 --apply" if not payload.apply else "打开内容页复核更新",
        },
    }


@router.post("/projects/{project_id}/content-plan/confirm")
def confirm_agent_content_plan(
    project_id: str,
    payload: AgentActionRequest | None = None,
    frontend_base_url: str = Query(DEFAULT_AGENT_FRONTEND_BASE_URL),
    tester_id: str = Depends(require_tester_id),
    db: Session = Depends(get_db),
):
    resolved_frontend = _frontend_base_url(payload, frontend_base_url)
    project = _load_accessible_project(db, project_id, tester_id)
    slides = _project_slides(db, project_id)
    if not slides:
        raise HTTPException(status_code=400, detail="当前项目还没有可确认的内容规划")

    project.content_plan_confirmed = True
    if project.status in {"draft", "planning", "content_plan_ready"}:
        project.status = "visual_ready"
    db.commit()
    db.refresh(project)
    return _agent_project_status_response(project, slides, get_active_run(db, project_id), resolved_frontend)


@router.post("/projects/{project_id}/visual-proposals/start")
def start_agent_visual_proposals(
    project_id: str,
    payload: StartVisualProposalsRequest | None = None,
    frontend_base_url: str = Query(DEFAULT_AGENT_FRONTEND_BASE_URL),
    tester_id: str = Depends(require_tester_id),
    db: Session = Depends(get_db),
):
    resolved_frontend = _frontend_base_url(payload, frontend_base_url)
    _load_accessible_project(db, project_id, tester_id)
    user_description = (payload.user_description if payload else None) or ""
    response = project_api.create_style_proposals(
        project_id,
        project_api.StyleProposalRequest(user_description=user_description),
        force=bool(payload.force) if payload else False,
        tester_id=tester_id,
        db=db,
    )
    return {
        "ok": True,
        **response,
        "ui_url": project_ui_url(project_id, resolved_frontend, stage="visual"),
    }


@router.get("/projects/{project_id}/visual-proposals")
def get_agent_visual_proposals(
    project_id: str,
    frontend_base_url: str = Query(DEFAULT_AGENT_FRONTEND_BASE_URL),
    tester_id: str = Depends(require_tester_id),
    db: Session = Depends(get_db),
):
    project = _load_accessible_project(db, project_id, tester_id)
    proposal = project.style_proposal if isinstance(project.style_proposal, dict) else {}
    proposals = proposal.get("proposals") if isinstance(proposal.get("proposals"), list) else []
    active_run = get_active_run(db, project_id)
    status = "completed" if proposals else ("generating" if active_run and active_run.kind == "style_proposal" else "not_started")
    return {
        "ok": True,
        "project_id": project.id,
        "status": status,
        "proposals_count": len(proposals),
        "proposals": proposals,
        "active_run": serialize_run(active_run),
        "ui_url": project_ui_url(project.id, frontend_base_url, stage="visual"),
        "next_action": {
            "type": "confirm_visual_proposal" if proposals else "start_visual_proposals",
            "label": "确认视觉方向" if proposals else "生成视觉提案",
            "url": project_ui_url(project.id, frontend_base_url, stage="visual"),
        },
    }


@router.post("/projects/{project_id}/visual-proposals/confirm")
def confirm_agent_visual_proposal(
    project_id: str,
    payload: ConfirmVisualProposalRequest,
    frontend_base_url: str = Query(DEFAULT_AGENT_FRONTEND_BASE_URL),
    tester_id: str = Depends(require_tester_id),
    db: Session = Depends(get_db),
):
    resolved_frontend = _frontend_base_url(payload, frontend_base_url)
    project = _load_accessible_project(db, project_id, tester_id)
    proposal = project.style_proposal if isinstance(project.style_proposal, dict) else {}
    proposals = proposal.get("proposals") if isinstance(proposal.get("proposals"), list) else []

    selected_style = payload.selected_style
    if payload.proposal_index is not None:
        index = payload.proposal_index - 1
        if index < 0 or index >= len(proposals):
            raise HTTPException(status_code=400, detail=f"视觉提案序号超出范围：{payload.proposal_index}")
        selected_style = proposals[index]
    elif selected_style is None and proposals:
        selected_style = proposals[0]

    if selected_style is None:
        raise HTTPException(status_code=400, detail="请先生成视觉提案，或直接提供 selected_style")

    updated_project = project_api.update_project_style(
        project_id,
        project_api.StyleUpdateRequest(selected_style=selected_style),
        tester_id=tester_id,
        db=db,
    )
    slides = _project_slides(db, project_id)
    response = _agent_project_status_response(updated_project, slides, get_active_run(db, project_id), resolved_frontend)
    response["selected_style"] = strip_artifact_meta(updated_project.selected_style) if updated_project.selected_style else None
    return response


@router.post("/projects/{project_id}/visual-prompts/start")
async def start_agent_visual_prompts(
    project_id: str,
    payload: StartVisualPromptsRequest | None = None,
    frontend_base_url: str = Query(DEFAULT_AGENT_FRONTEND_BASE_URL),
    tester_id: str = Depends(require_tester_id),
    db: Session = Depends(get_db),
):
    resolved_frontend = _frontend_base_url(payload, frontend_base_url)
    _load_accessible_project(db, project_id, tester_id)
    body = slides_api.PageNumsRequest(
        page_nums=payload.page_nums if payload else None,
        stage_context=(payload.stage_context if payload else None),
    )
    response = await slides_api.create_visual_and_prompts(project_id, body, db)
    return {
        "ok": True,
        **response,
        "ui_url": project_ui_url(project_id, resolved_frontend, stage="visual"),
    }


@router.post("/projects/{project_id}/visual-plan/import")
def import_agent_visual_plan(
    project_id: str,
    payload: ImportVisualPlanRequest,
    tester_id: str = Depends(require_tester_id),
    db: Session = Depends(get_db),
):
    """Import Agent-authored visual descriptions and ready-to-run image prompts."""
    project = _load_accessible_project(db, project_id, tester_id)
    if not project.content_plan_confirmed:
        raise HTTPException(status_code=409, detail="请先确认内容规划，再导入画面方案。")

    requested_pages = [item.page_num for item in payload.pages]
    if len(requested_pages) != len(set(requested_pages)):
        raise HTTPException(status_code=400, detail="画面方案中存在重复页码。")

    slides = _project_slides(db, project_id)
    slide_by_page = {slide.page_num: slide for slide in slides}
    missing_pages = [page_num for page_num in requested_pages if page_num not in slide_by_page]
    if missing_pages:
        raise HTTPException(status_code=404, detail=f"项目中不存在这些页码：{missing_pages}")

    if payload.selected_style is not None or not project.selected_style:
        selected_style = payload.selected_style or {
            "name": "Agent 提供的视觉方向",
            "description": "视觉方向、页面画面描述和生图 Prompt 由外部 Agent 提供。",
            "source": "external_agent",
        }
        project = project_api.update_project_style(
            project_id,
            project_api.StyleUpdateRequest(selected_style=selected_style),
            tester_id=tester_id,
            db=db,
        )
        slides = _project_slides(db, project_id)
        slide_by_page = {slide.page_num: slide for slide in slides}

    artifact_deps = dependency_signature(project, slides)
    for item in payload.pages:
        slide = slide_by_page[item.page_num]
        visual = dict(item.visual_json or {})
        visual["visual_description"] = item.visual_description.strip()
        visual["artifact_source"] = "external_agent"
        slide.visual_json = with_stale_flags(
            with_artifact_meta(
                visual,
                kind="visual_plan",
                dependencies=artifact_deps,
                prompt_dependencies=artifact_deps,
            ),
            content=False,
            visual=False,
            image=False,
        )
        flag_modified(slide, "visual_json")
        slide.prompt_text = item.prompt.strip()
        slide.image_path = None
        slide.error_msg = None
        slide.status = "prompt_ready"

    project.status = "prompt_ready" if all(slide.prompt_text for slide in slides) else "visual_ready"
    db.commit()
    db.refresh(project)
    return {
        "ok": True,
        "project_id": project.id,
        "imported_page_nums": requested_pages,
        "project_status": project.status,
        "ui_url": project_ui_url(project.id, payload.frontend_base_url, stage="visual"),
        "next_action": _next_action(project, slides, payload.frontend_base_url),
    }


@router.post("/projects/{project_id}/slides/generate")
def start_agent_slide_generation(
    project_id: str,
    payload: StartSlideGenerationRequest | None = None,
    frontend_base_url: str = Query(DEFAULT_AGENT_FRONTEND_BASE_URL),
    tester_id: str = Depends(require_tester_id),
    db: Session = Depends(get_db),
):
    resolved_frontend = _frontend_base_url(payload, frontend_base_url)
    _load_accessible_project(db, project_id, tester_id)
    body = slides_api.PageNumsRequest(
        page_nums=payload.page_nums if payload else None,
        prototype=bool(payload.prototype) if payload else False,
    )
    response = slides_api.start_generation(project_id, body, db)
    return {
        "ok": True,
        **response,
        "ui_url": project_ui_url(project_id, resolved_frontend),
    }


@router.get("/projects/{project_id}/generation-status")
def get_agent_generation_status(
    project_id: str,
    frontend_base_url: str = Query(DEFAULT_AGENT_FRONTEND_BASE_URL),
    tester_id: str = Depends(require_tester_id),
    db: Session = Depends(get_db),
):
    _load_accessible_project(db, project_id, tester_id)
    workflow_status = slides_api.get_project_workflow_status(project_id, db)
    return {
        "ok": True,
        "project_id": project_id,
        "workflow_status": workflow_status,
        "ui_url": project_ui_url(project_id, frontend_base_url),
        "next_action": _generation_next_action(workflow_status, project_id, frontend_base_url),
    }


@router.post("/projects/{project_id}/slides/retry-failed")
def retry_agent_failed_slides(
    project_id: str,
    payload: AgentActionRequest | None = None,
    frontend_base_url: str = Query(DEFAULT_AGENT_FRONTEND_BASE_URL),
    tester_id: str = Depends(require_tester_id),
    db: Session = Depends(get_db),
):
    resolved_frontend = _frontend_base_url(payload, frontend_base_url)
    _load_accessible_project(db, project_id, tester_id)
    response = slides_api.retry_failed_slides(project_id, db)
    return {
        "ok": True,
        **response,
        "ui_url": project_ui_url(project_id, resolved_frontend),
    }


@router.post("/projects/{project_id}/prototype/confirm")
def confirm_agent_prototype(
    project_id: str,
    payload: AgentActionRequest | None = None,
    frontend_base_url: str = Query(DEFAULT_AGENT_FRONTEND_BASE_URL),
    tester_id: str = Depends(require_tester_id),
    db: Session = Depends(get_db),
):
    resolved_frontend = _frontend_base_url(payload, frontend_base_url)
    _load_accessible_project(db, project_id, tester_id)
    response = slides_api.confirm_prototype(project_id, db)
    return {
        "ok": True,
        **response,
        "ui_url": project_ui_url(project_id, resolved_frontend),
    }


@router.post("/projects/{project_id}/runs/stop")
def stop_agent_generation(
    project_id: str,
    payload: AgentActionRequest | None = None,
    frontend_base_url: str = Query(DEFAULT_AGENT_FRONTEND_BASE_URL),
    tester_id: str = Depends(require_tester_id),
    db: Session = Depends(get_db),
):
    resolved_frontend = _frontend_base_url(payload, frontend_base_url)
    _load_accessible_project(db, project_id, tester_id)
    response = slides_api.stop_generation(project_id, db)
    return {
        "ok": True,
        **response,
        "ui_url": project_ui_url(project_id, resolved_frontend),
    }


@router.get("/projects/{project_id}/pptx/export")
def export_agent_ppt(
    project_id: str,
    prototype: bool = Query(False),
    api_base_url: str = Query("http://127.0.0.1:8000"),
    frontend_base_url: str = Query(DEFAULT_AGENT_FRONTEND_BASE_URL),
    tester_id: str = Depends(require_tester_id),
    db: Session = Depends(get_db),
):
    project = _load_accessible_project(db, project_id, tester_id)
    workflow_status = slides_api.get_project_workflow_status(project_id, db)
    filename = f"{project.title}{'_prototype' if prototype else ''}.pptx"
    return {
        "ok": True,
        "project_id": project_id,
        "title": project.title,
        "filename": filename,
        "prototype": bool(prototype),
        "has_pptx": bool(workflow_status.get("has_pptx")),
        "pptx_path": workflow_status.get("pptx_path"),
        "download_url": _download_url(project_id, api_base_url, tester_id=tester_id, prototype=bool(prototype)),
        "workflow_status": workflow_status,
        "ui_url": project_ui_url(project_id, frontend_base_url),
        "next_action": _generation_next_action(workflow_status, project_id, frontend_base_url),
    }
