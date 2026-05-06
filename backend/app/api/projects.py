import logging
from fastapi import APIRouter, Depends, HTTPException

logger = logging.getLogger(__name__)
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.base import get_db
from app.models.models import Project, Slide
from app.schemas.project import ProjectCreate, ProjectUpdate, ProjectResponse
from app.tasks import compute_style_asset_signature, generate_style_proposals_task, redis_client
from app.services.run_state import cancel_active_run, create_project_run, get_active_run, normalize_confirmed_project_stage, serialize_run
from celery.result import AsyncResult

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectResponse)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)):
    title = payload.title.strip() if payload.title else "未命名项目"
    if len(title) > 100:
        raise HTTPException(status_code=400, detail="项目标题不能超过100个字符")

    project = Project(title=title, style_id=payload.style_id)
    db.add(project)
    db.commit()
    db.refresh(project)

    return project


@router.get("", response_model=list[ProjectResponse])
def list_projects(db: Session = Depends(get_db)):
    projects = db.query(Project).order_by(Project.created_at.desc()).all()
    changed = False
    for project in projects:
        before = project.status
        normalize_confirmed_project_stage(project, list(project.slides or []), get_active_run(db, project.id))
        changed = changed or project.status != before
    if changed:
        db.commit()
    return projects


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: str, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    before = project.status
    normalize_confirmed_project_stage(project, list(project.slides or []), get_active_run(db, project_id))
    # 用户已进入项目详情，清除未读通知
    if project.has_unread_notification:
        project.has_unread_notification = False
        project.unread_notification_message = None
    if project.status != before or not project.has_unread_notification:
        db.commit()
        db.refresh(project)
    return project


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(project_id: str, payload: ProjectUpdate, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
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
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}")
def delete_project(project_id: str, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    db.delete(project)
    db.commit()
    return {"message": "Project deleted"}


class StyleUpdateRequest(BaseModel):
    selected_style: dict | None = None


@router.patch("/{project_id}/style", response_model=ProjectResponse)
def update_project_style(
    project_id: str,
    payload: StyleUpdateRequest,
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if payload.selected_style is not None:
        project.selected_style = payload.selected_style
        project.content_plan_confirmed = True
        project.status = "visual_ready"
        for slide in project.slides or []:
            slide.visual_json = {}
            slide.prompt_text = None
            slide.image_path = None
            slide.error_msg = None
            slide.status = "pending"
    db.commit()
    db.refresh(project)
    return project


@router.post("/{project_id}/style-proposals")
def create_style_proposals(
    project_id: str,
    force: bool = False,
    db: Session = Depends(get_db),
):
    """基于 Content Plan 生成 3 套风格提案并保存到 project.style_proposal。
    支持异步后台生成，前端通过轮询 project.style_proposal 获取结果。
    force=true 时强制重新生成（忽略缓存）。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if get_active_run(db, project_id):
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
    has_global_style_ref = any(
        ref.role == "style_ref" and not ref.slide_id
        for ref in (project.reference_images or [])
    )

    # 如果已有有效的风格提案且未强制重新生成，只有在素材指纹一致时才返回缓存。
    # 这样用户上传/删除参考图后，不会继续看到旧的“无素材推荐”或过期提案。
    if not force and project.style_proposal and project.style_proposal.get("proposals"):
        cached_asset_signature = project.style_proposal.get("asset_signature")
        cached_proposals = project.style_proposal.get("proposals") or []
        cached_is_style_dna = any(
            isinstance(p, dict)
            and (p.get("source") == "asset_clone" or p.get("clone_mode") in {"style_dna", "strict_reference"})
            for p in cached_proposals
        )
        cache_signature_matches = cached_asset_signature == current_asset_signature or (
            not cached_asset_signature and not current_asset_signature
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
                "Style proposal cache invalidated for project=%s: cached assets changed",
                project_id,
            )
        project.style_proposal = None
        if has_global_style_ref:
            project.selected_style = None
        db.commit()
        db.refresh(project)

    # 强制重新生成时先清空旧缓存
    if force:
        project.style_proposal = None
        db.commit()
        db.refresh(project)

    # 根据是否有素材决定 total_count：有素材时生成 1 套，无素材时生成 3 套
    has_assets = any(
        ref.role in {"logo", "style_ref", "template"}
        for ref in (project.reference_images or [])
    )
    total_count = 1 if has_assets else 3

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

    task = generate_style_proposals_task.delay(project_id, run.id)
    run.task_id = task.id
    db.commit()
    return {"status": "generating", "proposals": None, "run": serialize_run(run)}


class TemplateRecommendationsRequest(BaseModel):
    recommendations: dict | None = None


@router.patch("/{project_id}/template-recommendations", response_model=ProjectResponse)
def update_template_recommendations(
    project_id: str,
    payload: TemplateRecommendationsRequest,
    db: Session = Depends(get_db),
):
    """保存用户确认的模板页面推荐（cover/toc/content/ending）。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if payload.recommendations is not None:
        project.selected_template_recommendations = payload.recommendations
    db.commit()
    db.refresh(project)
    return project


class RollbackRequest(BaseModel):
    target_stage: str


@router.post("/{project_id}/rollback", response_model=ProjectResponse)
def rollback_project(
    project_id: str,
    payload: RollbackRequest,
    db: Session = Depends(get_db),
):
    """按目标阶段回退项目，清理下游数据。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    target = payload.target_stage
    valid_stages = {"planning", "visual_ready", "prompt_ready", "prototype_ready"}
    if target not in valid_stages:
        raise HTTPException(status_code=400, detail=f"无效的回退目标阶段，可选: {valid_stages}")

    # 根据目标阶段清理下游数据
    cancel_active_run(db, project_id, "用户回退流程")
    if target == "planning":
        project.status = "planning"
        project.content_plan_confirmed = False
        project.selected_style = None
        project.style_proposal = None
        for slide in project.slides:
            slide.visual_json = {}
            slide.prompt_text = None
            slide.image_path = None
            slide.status = "pending"
            slide.error_msg = None
    elif target == "visual_ready":
        project.status = "visual_ready"
        project.content_plan_confirmed = True
        project.selected_style = None
        for slide in project.slides:
            slide.prompt_text = None
            slide.image_path = None
            slide.status = "visual_ready" if slide.visual_json else "pending"
            slide.error_msg = None
    elif target == "prompt_ready":
        project.status = "prompt_ready"
        project.content_plan_confirmed = True
        for slide in project.slides:
            slide.image_path = None
            slide.status = "prompt_ready"
            slide.error_msg = None
    elif target == "prototype_ready":
        project.status = "prototype_ready"
        project.content_plan_confirmed = True
        for slide in project.slides:
            slide.image_path = None
            slide.status = "prompt_ready"
            slide.error_msg = None

    db.commit()

    # 撤销可能正在运行的 Celery 任务，防止回退后任务完成又改回状态
    task_id = redis_client.get(f"project:{project_id}:task_id")
    if task_id:
        try:
            AsyncResult(task_id.decode() if isinstance(task_id, bytes) else task_id).revoke(terminate=True)
            logger.info(f"Rollback: revoked Celery task {task_id} for project {project_id}")
        except Exception as e:
            logger.warning(f"Rollback: failed to revoke task {task_id}: {e}")
        redis_client.delete(f"project:{project_id}:task_id")
        redis_client.delete(f"project:{project_id}:task_started_at")

    # 清除 Redis 生成锁
    redis_client.delete(f"project:{project_id}:generating")

    db.refresh(project)
    return project
