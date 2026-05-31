import logging
import os
import re
import time
from fastapi import APIRouter, Depends, HTTPException

logger = logging.getLogger(__name__)
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.base import get_db
from app.models.models import Project, Slide, ReferenceImage
from app.schemas.project import ProjectCreate, ProjectUpdate, ProjectResponse
from app.core.tester_auth import is_local_admin_request, require_existing_tester, require_tester_id, tester_id_from_header, verify_project_access
from app.core.provider_credentials import store_current_provider_credentials
from app.core.config import settings
from app.services.artifact_versions import content_signature, dependency_signature, selected_style_signature, with_artifact_meta, with_stale_flags
from app.tasks import compute_style_asset_signature, generate_style_proposals_task, redis_client
from app.services.celery_runtime import ensure_celery_worker
from app.services.run_state import apply_project_rollback, cancel_active_run, create_project_run, finish_run, get_active_run, reconcile_project_state, serialize_run, set_run_task
from app.services.source_intent import normalize_intent_contract
from app.services.style_proposal import STYLE_PROPOSAL_POLICY_VERSION
from app.celery_app import celery_app
from celery.result import AsyncResult

router = APIRouter(prefix="/projects", tags=["projects"])


def _clear_stale_style_proposal(project: Project) -> bool:
    proposal = project.style_proposal if isinstance(project.style_proposal, dict) else None
    if not proposal or project.selected_style:
        return False
    if proposal.get("policy_version") == STYLE_PROPOSAL_POLICY_VERSION:
        return False
    if not proposal.get("proposals"):
        return False
    project.style_proposal = None
    return True


def _active_run_for_project_action(project: Project | None, db: Session):
    from app.api.slides import _active_run_for_project_action as refresh_active_run

    return refresh_active_run(project, db)


@router.post("", response_model=ProjectResponse)
def create_project(
    payload: ProjectCreate,
    tester_id: str = Depends(require_tester_id),
    db: Session = Depends(get_db),
):
    title = payload.title.strip() if payload.title else "未命名项目"
    if len(title) > 100:
        raise HTTPException(status_code=400, detail="项目标题不能超过100个字符")

    owner_tester_id = None if is_local_admin_request(tester_id) else require_existing_tester(db, tester_id).id
    project = Project(title=title, style_id=payload.style_id, tester_id=owner_tester_id)
    db.add(project)
    db.commit()
    db.refresh(project)

    return project


@router.get("", response_model=list[ProjectResponse])
def list_projects(tester_id: str = Depends(require_tester_id), db: Session = Depends(get_db)):
    query = db.query(Project)
    if not is_local_admin_request(tester_id):
        query = query.filter(Project.tester_id == tester_id)
    projects = query.order_by(Project.created_at.desc()).all()
    changed = False
    for project in projects:
        before = project.status
        reconcile_project_state(project, list(project.slides or []), get_active_run(db, project.id))
        changed = _clear_stale_style_proposal(project) or changed or project.status != before or bool(db.dirty)
    if changed:
        db.commit()
    return projects


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: str, tester_id: str = Depends(tester_id_from_header), db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    verify_project_access(project, tester_id)
    before = project.status
    reconcile_project_state(project, list(project.slides or []), get_active_run(db, project_id))
    style_proposal_changed = _clear_stale_style_proposal(project)
    # 用户已进入项目详情，清除未读通知
    if project.has_unread_notification:
        project.has_unread_notification = False
        project.unread_notification_message = None
    if project.status != before or style_proposal_changed or not project.has_unread_notification or db.dirty:
        db.commit()
        db.refresh(project)
    return project


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    tester_id: str = Depends(tester_id_from_header),
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    verify_project_access(project, tester_id)
    if payload.title is not None:
        new_title = payload.title.strip()
        if len(new_title) > 100:
            raise HTTPException(status_code=400, detail="项目标题不能超过100个字符")
        project.title = new_title
    if payload.style_id is not None:
        project.style_id = payload.style_id
    if payload.content_plan_confirmed is not None:
        project.content_plan_confirmed = payload.content_plan_confirmed
        if payload.content_plan_confirmed and project.slides and project.status in {"draft", "planning"}:
            project.status = "visual_ready"
    if payload.intent_contract is not None:
        project.intent_contract = normalize_intent_contract(payload.intent_contract)
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}")
def delete_project(project_id: str, tester_id: str = Depends(tester_id_from_header), db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    verify_project_access(project, tester_id)
    db.delete(project)
    db.commit()
    return {"message": "Project deleted"}


class StyleUpdateRequest(BaseModel):
    selected_style: dict | None = None


class StyleProposalRequest(BaseModel):
    user_description: str | None = None


@router.patch("/{project_id}/style", response_model=ProjectResponse)
def update_project_style(
    project_id: str,
    payload: StyleUpdateRequest,
    tester_id: str = Depends(tester_id_from_header),
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    verify_project_access(project, tester_id)
    if payload.selected_style is not None:
        slides = list(project.slides or [])
        style_dependencies = dependency_signature(project, slides)
        style_dependencies["selected_style"] = selected_style_signature(payload.selected_style)
        project.selected_style = with_artifact_meta(
            payload.selected_style,
            kind="selected_style",
            dependencies=style_dependencies,
        )
        project.content_plan_confirmed = True
        project.status = "visual_ready"
        for slide in slides:
            slide.visual_json = with_stale_flags(slide.visual_json if isinstance(slide.visual_json, dict) else {}, content=True)
            slide.error_msg = None
            # 视觉方案改变后，所有已生成图片和 prompt 都作废，防止旧图冒充新风格
            slide.image_path = None
            slide.prompt_text = None
            slide.status = "pending"
    db.commit()
    db.refresh(project)
    return project


@router.post("/{project_id}/style-proposals")
def create_style_proposals(
    project_id: str,
    payload: StyleProposalRequest | None = None,
    force: bool = False,
    tester_id: str = Depends(tester_id_from_header),
    db: Session = Depends(get_db),
):
    """基于 Content Plan 生成 3 套风格提案并保存到 project.style_proposal。
    支持异步后台生成，前端通过轮询 project.style_proposal 获取结果。
    force=true 时强制重新生成（忽略缓存）。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    verify_project_access(project, tester_id)
    if not project.content_plan_confirmed or project.selected_style:
        raise HTTPException(status_code=409, detail="当前阶段不能生成风格提案，请先确认内容或回退到视觉方案。")
    if _active_run_for_project_action(project, db):
        raise HTTPException(status_code=409, detail="当前项目已有任务正在运行，请等待完成后再开始下一步")

    slides = (
        db.query(Slide)
        .filter(Slide.project_id == project_id)
        .order_by(Slide.page_num)
        .all()
    )
    if not slides:
        raise HTTPException(status_code=400, detail="No content plan found. Generate content plan first.")

    current_asset_signature = compute_style_asset_signature(project)
    current_content_signature = content_signature(slides)
    user_description = (payload.user_description if payload else None) or ""
    user_description = user_description.strip()[:4000]
    has_global_style_ref = any(
        ref.role == "style_ref" and not ref.slide_id
        for ref in (project.reference_images or [])
    )

    # 如果已有有效的风格提案且未强制重新生成，只有在素材指纹一致时才返回缓存。
    # 这样用户上传/删除参考图后，不会继续看到旧的“无素材推荐”或过期提案。
    if not force and project.style_proposal and project.style_proposal.get("proposals"):
        cached_asset_signature = project.style_proposal.get("asset_signature")
        cached_content_signature = project.style_proposal.get("content_signature")
        cached_user_description = (project.style_proposal.get("user_description") or "").strip()
        cached_policy_version = project.style_proposal.get("policy_version")
        cached_proposals = project.style_proposal.get("proposals") or []
        cached_is_style_dna = any(
            isinstance(p, dict)
            and (p.get("source") == "asset_clone" or p.get("clone_mode") in {"style_dna", "strict_reference"})
            for p in cached_proposals
        )
        cache_signature_matches = (
            (cached_asset_signature == current_asset_signature or (not cached_asset_signature and not current_asset_signature))
            and (cached_content_signature == current_content_signature or not cached_content_signature)
            and cached_user_description == user_description
            and cached_policy_version == STYLE_PROPOSAL_POLICY_VERSION
        )
        if cache_signature_matches and (not has_global_style_ref or cached_is_style_dna):
            return {
                "status": "completed",
                "proposals": cached_proposals,
            }

        if cache_signature_matches and has_global_style_ref and not cached_is_style_dna:
            logger.info(
                "Style proposal cache invalidated for project=%s: style reference requires style DNA proposal",
                project_id,
            )
        else:
            logger.info(
                "Style proposal cache invalidated for project=%s: cached assets, content, requirements, or policy changed",
                project_id,
            )
        project.style_proposal = None
        if has_global_style_ref:
            project.selected_style = None
        db.commit()
        db.refresh(project)

    # 强制重新生成时先清空旧缓存（同时保留上一版 proposal 用于反对参考，避免 LLM 换汤不换药）
    previous_proposal_snapshot: dict | None = None
    if force:
        existing_proposals = (project.style_proposal or {}).get("proposals") if project.style_proposal else None
        if existing_proposals:
            for item in existing_proposals:
                if isinstance(item, dict):
                    previous_proposal_snapshot = {
                        "name": item.get("name"),
                        "palette": item.get("palette"),
                        "mood": item.get("mood"),
                        "description": item.get("description"),
                    }
                    break
        project.style_proposal = None
        db.commit()
        db.refresh(project)

    # 根据是否有素材决定 total_count：有素材时生成 1 套，无素材时生成 3 套
    has_assets = any(
        ref.role in {"logo", "style_ref", "template"}
        for ref in (project.reference_images or [])
    ) or bool(user_description)
    total_count = 1 if has_assets else 3

    if not ensure_celery_worker(queue=settings.CELERY_TEXT_QUEUE):
        raise HTTPException(status_code=503, detail="视觉方向生成服务未启动，任务没有开始。请启动 worker 后重试。")

    # 改用 Celery 队列执行，比 FastAPI BackgroundTasks 更可靠
    try:
        run = create_project_run(
            db,
            project_id,
            kind="style_proposal",
            stage="style_proposal",
            total_count=total_count,
            message="风格提案生成已排队",
        )
        db.commit()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    try:
        credential_id = store_current_provider_credentials(redis_client)
        task = generate_style_proposals_task.delay(
            project_id,
            run.id,
            credential_id=credential_id,
            user_description=user_description,
            previous_proposal=previous_proposal_snapshot,
        )
        set_run_task(db, run.id, task.id)
        db.commit()
    except Exception as exc:
        logger.exception("Failed to enqueue style proposals task for project %s", project_id)
        message = "后台队列暂时不可用。请确认 Docker/Redis 正常运行且磁盘空间充足，然后重试。"
        finish_run(db, run.id, status="stale", message=message, error_msg=str(exc)[:500])
        db.commit()
        raise HTTPException(status_code=503, detail=message) from exc
    try:
        redis_client.set(f"project:{project_id}:task_id", task.id, ex=3600)
        redis_client.set(f"project:{project_id}:task_started_at", str(time.time()), ex=3600)
    except Exception as exc:
        logger.warning("Redis 写入风格任务跟踪信息失败，不影响已分发任务: project=%s task=%s error=%s", project_id, task.id, exc)
    return {"status": "generating", "proposals": None, "run": serialize_run(run)}


class TemplateRecommendationsRequest(BaseModel):
    recommendations: dict | None = None


TEMPLATE_RECOMMENDATION_KEYS = {"cover", "toc", "section", "content", "data", "quote", "ending"}


def _template_page_num(ref: ReferenceImage) -> int:
    analysis = ref.asset_analysis if isinstance(ref.asset_analysis, dict) else {}
    try:
        page_num = int(analysis.get("template_page_num") or 0)
    except (TypeError, ValueError):
        page_num = 0
    if page_num > 0:
        return page_num
    match = re.search(r"page_(\d+)", os.path.basename(ref.file_path or ""))
    return int(match.group(1)) if match else 0


def _template_recommendation_from_ref(ref: ReferenceImage) -> dict:
    analysis = ref.asset_analysis if isinstance(ref.asset_analysis, dict) else {}
    layout_path = analysis.get("layout_file_path") or ref.file_path
    return {
        "page_num": _template_page_num(ref),
        "file_path": layout_path,
        "preview_file_path": analysis.get("preview_file_path") or ref.file_path,
        "layout_file_path": layout_path,
        "category": analysis.get("template_category") or "content",
        "category_confidence": analysis.get("category_confidence") or 0.6,
        "source_kind": analysis.get("source_kind") or "template",
        "application_strength": analysis.get("template_application_strength") or "standard",
        "logo_removed": bool(analysis.get("logo_removed")),
    }


def _hydrate_template_recommendations(project_id: str, recommendations: dict | None, db: Session) -> dict | None:
    if recommendations is None:
        return None
    refs = db.query(ReferenceImage).filter(
        ReferenceImage.project_id == project_id,
        ReferenceImage.role == "template",
    ).all()
    by_page = {
        _template_page_num(ref): ref
        for ref in refs
        if _template_page_num(ref) > 0
    }
    hydrated = {}
    for key in TEMPLATE_RECOMMENDATION_KEYS:
        value = recommendations.get(key) if isinstance(recommendations, dict) else None
        if not value:
            hydrated[key] = None
            continue
        try:
            page_num = int(value.get("page_num") if isinstance(value, dict) else value)
        except (TypeError, ValueError):
            hydrated[key] = None
            continue
        ref = by_page.get(page_num)
        hydrated_value = _template_recommendation_from_ref(ref) if ref else None
        if hydrated_value and isinstance(value, dict):
            strength = str(value.get("application_strength") or "").strip()
            if strength in {"light", "standard", "strong"}:
                hydrated_value["application_strength"] = strength
        hydrated[key] = hydrated_value
    return hydrated


@router.patch("/{project_id}/template-recommendations", response_model=ProjectResponse)
def update_template_recommendations(
    project_id: str,
    payload: TemplateRecommendationsRequest,
    tester_id: str = Depends(tester_id_from_header),
    db: Session = Depends(get_db),
):
    """保存用户确认的模板页面推荐（cover/toc/content/ending）。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    verify_project_access(project, tester_id)
    if payload.recommendations is not None:
        project.selected_template_recommendations = _hydrate_template_recommendations(
            project_id,
            payload.recommendations,
            db,
        )
    db.commit()
    db.refresh(project)
    return project


class RollbackRequest(BaseModel):
    target_stage: str


@router.post("/{project_id}/rollback", response_model=ProjectResponse)
def rollback_project(
    project_id: str,
    payload: RollbackRequest,
    tester_id: str = Depends(tester_id_from_header),
    db: Session = Depends(get_db),
):
    """按目标阶段回退项目，清理下游数据。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    verify_project_access(project, tester_id)

    target = payload.target_stage
    valid_stages = {"planning", "visual_ready", "prompt_ready", "prototype_ready"}
    if target not in valid_stages:
        raise HTTPException(status_code=400, detail=f"无效的回退目标阶段，可选: {valid_stages}")

    # 根据目标阶段清理下游数据，并尽量取消所有已知后台执行体。
    active_run = cancel_active_run(db, project_id, "用户回退流程")
    if active_run and active_run.task_id:
        try:
            AsyncResult(active_run.task_id, app=celery_app).revoke(terminate=True)
            logger.info(f"Rollback: revoked run task {active_run.task_id} for project {project_id}")
        except Exception as e:
            logger.warning(f"Rollback: failed to revoke run task {active_run.task_id}: {e}")
    try:
        from app.api.slides import _running_tasks

        running_task = _running_tasks.pop(project_id, None)
        if running_task and not running_task.done():
            running_task.cancel()
            logger.info(f"Rollback: cancelled asyncio visual task for project {project_id}")
    except Exception as e:
        logger.warning(f"Rollback: failed to cancel asyncio visual task for project {project_id}: {e}")

    apply_project_rollback(project, list(project.slides or []), target)

    db.commit()

    # 撤销可能正在运行的 Celery 任务，防止回退后任务完成又改回状态
    task_id = redis_client.get(f"project:{project_id}:task_id")
    if task_id:
        try:
            AsyncResult(task_id.decode() if isinstance(task_id, bytes) else task_id, app=celery_app).revoke(terminate=True)
            logger.info(f"Rollback: revoked Celery task {task_id} for project {project_id}")
        except Exception as e:
            logger.warning(f"Rollback: failed to revoke task {task_id}: {e}")
        redis_client.delete(f"project:{project_id}:task_id")
        redis_client.delete(f"project:{project_id}:task_started_at")

    # 清除 Redis 生成锁
    redis_client.delete(f"project:{project_id}:generating")

    db.refresh(project)
    return project
