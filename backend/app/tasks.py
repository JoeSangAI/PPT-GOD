import logging
import os

import redis

from app.celery_app import celery_app
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.base import SessionLocal
from app.models.models import Project, Slide
from app.services.generation_pipeline import run_generation_pipeline
from app.services.style_proposal import generate_style_proposals
from app.services.image_analyzer import analyze_logo, analyze_reference_image
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

redis_client = redis.from_url(settings.REDIS_URL or "redis://localhost:6379/0")


def _restore_project_after_skipped_generation(project_id: str):
    """Avoid leaving the UI in generating when a duplicate Celery task is skipped."""
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if project and project.status == "generating":
            project.status = "prompt_ready"
        slides = db.query(Slide).filter(
            Slide.project_id == project_id,
            Slide.status == "generating",
        ).all()
        for slide in slides:
            slide.status = "prompt_ready"
            slide.error_msg = "生成任务已跳过：已有任务正在执行或锁未释放"
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning(f"Failed to restore skipped generation state: {exc}")
    finally:
        db.close()


@celery_app.task
def generate_slides_task(project_id: str, page_nums: list = None, prototype: bool = False):
    """Celery task: 执行幻灯片生成流水线。"""
    lock_key = f"project:{project_id}:generating"
    acquired = redis_client.set(lock_key, "1", nx=True, ex=600)
    if not acquired:
        logger.warning(f"Project {project_id} already has a generation task running")
        _restore_project_after_skipped_generation(project_id)
        redis_client.delete(f"project:{project_id}:task_id")
        redis_client.delete(f"project:{project_id}:task_started_at")
        return {"project_id": project_id, "status": "skipped", "reason": "already_running"}

    db = SessionLocal()
    try:
        logger.info(f"Celery task started: project={project_id}, pages={page_nums}, prototype={prototype}")
        run_generation_pipeline(project_id, db, page_nums=page_nums, prototype=prototype)
        logger.info(f"Celery task completed: project={project_id}")
        return {"project_id": project_id, "status": "completed", "page_nums": page_nums, "prototype": prototype}
    except Exception as exc:
        logger.error(f"Celery task failed: {exc}")
        # 图像生成是非幂等计费操作，整任务重试会导致已成功的页面被重复计费。
        # 因此不再无条件 retry，而是标记项目失败，由用户通过前端"重试失败页"逐页处理。
        try:
            project = db.query(Project).filter(Project.id == project_id).first()
            if project and project.status == "generating":
                project.status = "failed"
                db.commit()
        except Exception as cleanup_err:
            logger.warning(f"Failed to mark project as failed after task error: {cleanup_err}")
        return {"project_id": project_id, "status": "failed", "error": str(exc), "page_nums": page_nums, "prototype": prototype}
    finally:
        redis_client.delete(lock_key)
        redis_client.delete(f"project:{project_id}:task_id")
        redis_client.delete(f"project:{project_id}:task_started_at")
        db.close()


@celery_app.task(bind=True, max_retries=2)
def generate_style_proposals_task(self, project_id: str):
    """Celery task: 生成风格提案。"""
    db = SessionLocal()
    try:
        logger.info(f"[StyleProposals Celery] Starting for project={project_id}")
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            logger.warning(f"[StyleProposals Celery] Project not found: {project_id}")
            return {"project_id": project_id, "status": "not_found"}

        slides = db.query(Slide).filter(Slide.project_id == project_id).order_by(Slide.page_num).all()
        if not slides:
            logger.warning(f"[StyleProposals Celery] No slides found for project: {project_id}")
            return {"project_id": project_id, "status": "no_slides"}

        content_plan = [s.content_json for s in slides]

        # 自动分析项目素材（失败不阻断主流程）
        assets = {}
        if project.reference_images:
            logo_refs = [r for r in project.reference_images if r.role == "logo"]
            style_refs = [r for r in project.reference_images if r.role == "style_ref"]
            template_refs = [r for r in project.reference_images if r.role == "template"]

            if logo_refs:
                try:
                    logger.info(f"[StyleProposals Celery] Analyzing logo for project={project_id}")
                    assets["logo_analysis"] = analyze_logo(logo_refs[0].file_path)
                except Exception as e:
                    logger.warning(f"[StyleProposals Celery] Logo analysis failed: {e}")

            if style_refs:
                try:
                    logger.info(f"[StyleProposals Celery] Analyzing reference image for project={project_id}")
                    assets["reference_analysis"] = analyze_reference_image(style_refs[0].file_path)
                except Exception as e:
                    logger.warning(f"[StyleProposals Celery] Reference analysis failed: {e}")

            if template_refs or project.selected_template_recommendations:
                assets["template_analysis"] = {"has_template": True}

        proposals = generate_style_proposals(content_plan, assets=assets if assets else None)
        project.style_proposal = {
            "proposals": proposals,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "asset_based": bool(assets),
        }
        # 风格提案生成后，项目进入视觉方案待确认阶段
        if project.status in ("planning", "draft"):
            project.status = "visual_ready"
        db.commit()
        logger.info(f"[StyleProposals Celery] Completed for project={project_id}, count={len(proposals)}, asset_based={bool(assets)}")
        return {"project_id": project_id, "status": "completed", "proposals_count": len(proposals), "asset_based": bool(assets)}
    except Exception as exc:
        db.rollback()
        logger.error(f"[StyleProposals Celery] Failed for project={project_id}: {exc}")
        if self.request.retries >= self.max_retries:
            # 重试已用完，标记项目失败，避免前端永远轮询
            try:
                project = db.query(Project).filter(Project.id == project_id).first()
                if project:
                    project.status = "failed"
                    db.commit()
                    logger.info(f"[StyleProposals Celery] Marked project={project_id} as failed after max retries")
            except Exception as mark_err:
                logger.warning(f"[StyleProposals Celery] Failed to mark project failed: {mark_err}")
            raise
        raise self.retry(exc=exc, countdown=15)
    finally:
        db.close()
