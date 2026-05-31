from datetime import datetime, timezone
import logging
import os

import redis

from app.celery_app import celery_app
from app.core.config import settings
from app.core.provider_credentials import load_task_provider_credentials, provider_credentials_context
from app.models.base import SessionLocal
from app.models.models import Project, Slide
from app.services.artifact_versions import content_signature, style_asset_signature
from app.services.editable_pptx_export import build_editable_pptx, build_project_slide_images, normalize_editable_pptx_restore_mode
from app.services.generation_pipeline import run_generation_pipeline
from app.services.image_generation import clear_reference_upload_cache
from app.services.image_task_audit import append_image_generation_log
from app.services.image_analyzer import analyze_logo, analyze_reference_image
from app.services.logo_assets import prepare_logo_lockup_image
from app.services.logo_policy import is_logo_confirmed
from app.services.run_state import (
    finish_run,
    get_run,
    image_generation_run_stage,
    image_generation_running_message,
    is_run_active,
    mark_run_running,
    set_run_task,
    update_run_progress,
)
from app.services.style_proposal import STYLE_PROPOSAL_POLICY_VERSION, generate_style_proposals

logger = logging.getLogger(__name__)

redis_client = redis.from_url(
    settings.REDIS_URL or "redis://localhost:6379/0",
    socket_connect_timeout=settings.REDIS_SOCKET_TIMEOUT_SECONDS,
    socket_timeout=settings.REDIS_SOCKET_TIMEOUT_SECONDS,
    retry_on_timeout=True,
    health_check_interval=30,
)


def compute_style_asset_signature(project: Project | None) -> str:
    """Fingerprint global style assets so cached proposals are invalidated after uploads/deletes."""
    return style_asset_signature(project)


def _cached_reference_analysis(ref) -> dict | None:
    analysis = ref.asset_analysis if isinstance(getattr(ref, "asset_analysis", None), dict) else {}
    if not analysis:
        return None
    meaningful_keys = (
        "primary_color",
        "secondary_colors",
        "description",
        "style_name",
        "dominant_palette",
        "colors",
        "composition_style",
        "mood",
    )
    if any(analysis.get(key) for key in meaningful_keys):
        return analysis
    return None


def _file_exists(path: str | None) -> bool:
    return bool(path and os.path.exists(path))


def _first_cached_reference_analysis(refs) -> dict | None:
    for ref in refs or []:
        cached = _cached_reference_analysis(ref)
        if cached:
            return cached
    return None


def _cached_reference_analyses(refs) -> list[dict]:
    analyses = []
    for ref in refs or []:
        cached = _cached_reference_analysis(ref)
        if cached:
            analyses.append(cached)
    return analyses


def _first_existing_ref(refs):
    return next((ref for ref in refs or [] if _file_exists(getattr(ref, "file_path", None))), None)


def _resolve_target_pages(project_id: str, page_nums: list = None) -> list:
    """Resolve which pages to generate. Returns list of page_num."""
    if page_nums:
        return list(page_nums)
    db = SessionLocal()
    try:
        slides = db.query(Slide).filter(Slide.project_id == project_id).order_by(Slide.page_num).all()
        return [s.page_num for s in slides if s.prompt_text]
    finally:
        db.close()


def _resolve_task_file_path(path: str | None) -> str | None:
    if not path:
        return None
    if os.path.exists(path):
        return path
    output_dir = settings.OUTPUT_DIR or "./outputs"
    if str(path).startswith("./outputs"):
        candidate = os.path.join(os.path.dirname(os.path.abspath(output_dir)), str(path)[2:])
        if os.path.exists(candidate):
            return candidate
    abs_path = os.path.abspath(str(path))
    return abs_path if os.path.exists(abs_path) else path


def _image_task_page_chunk_size() -> int:
    try:
        return max(1, int(settings.IMAGE_GENERATION_TASK_PAGE_CHUNK_SIZE or 8))
    except (TypeError, ValueError):
        return 8


def _split_generation_pages(page_nums: list[int]) -> tuple[list[int], list[int]]:
    chunk_size = _image_task_page_chunk_size()
    normalized = [int(p) for p in page_nums]
    return normalized[:chunk_size], normalized[chunk_size:]


def _page_lock_ttl_seconds() -> int:
    try:
        return max(600, int(settings.CELERY_TASK_TIME_LIMIT or 2100) + 300)
    except (TypeError, ValueError):
        return 2400


def _acquire_page_locks(project_id: str, page_nums: list, ttl: int | None = None) -> list:
    """Try to acquire per-page locks. Returns list of page_nums that were successfully locked."""
    ttl = ttl or _page_lock_ttl_seconds()
    acquired = []
    for pn in page_nums:
        lock_key = f"project:{project_id}:slide:{pn}:generating"
        if redis_client.set(lock_key, "1", nx=True, ex=ttl):
            acquired.append(pn)
        else:
            logger.info(f"Page {pn} is already being generated by another task, skipping")
    return acquired


def _release_page_locks(project_id: str, page_nums: list):
    """Release per-page locks."""
    for pn in page_nums:
        try:
            redis_client.delete(f"project:{project_id}:slide:{pn}:generating")
        except Exception as exc:
            logger.warning("Failed to release page lock project=%s page=%s: %s", project_id, pn, exc)


def _finish_run_for_credential_error(run_id: str | None, message: str, exc: Exception):
    db = SessionLocal()
    try:
        finish_run(db, run_id, status="failed", message=message, error_msg=str(exc)[:500])
        db.commit()
    except Exception as cleanup_exc:
        db.rollback()
        logger.warning("Failed to mark run failed after credential error: %s", cleanup_exc)
    finally:
        db.close()


def _cleanup_stale_generating_slides(project_id: str, page_nums: list):
    """Reset slide status for pages we couldn't lock (locked by another task)."""
    db = SessionLocal()
    try:
        slides = db.query(Slide).filter(
            Slide.project_id == project_id,
            Slide.page_num.in_(page_nums),
            Slide.status == "generating",
        ).all()
        for slide in slides:
            lock_key = f"project:{project_id}:slide:{slide.page_num}:generating"
            if not redis_client.exists(lock_key):
                slide.status = "prompt_ready"
                slide.error_msg = "生成任务已跳过：锁已过期"
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning(f"Failed to cleanup stale generating slides: {exc}")
    finally:
        db.close()


def _enqueue_generation_continuation(
    db,
    project_id: str,
    remaining_page_nums: list[int],
    *,
    prototype: bool,
    run_id: str | None,
    credential_id: str | None,
):
    if not remaining_page_nums or not is_run_active(db, run_id):
        return None
    task = generate_slides_task.apply_async(
        args=[project_id, remaining_page_nums],
        kwargs={
            "prototype": prototype,
            "run_id": run_id,
            "credential_id": credential_id,
        },
    )
    set_run_task(db, run_id, task.id)
    db.commit()
    try:
        redis_client.set(f"project:{project_id}:task_id", task.id, ex=3600)
        redis_client.set(f"project:{project_id}:task_started_at", str(datetime.now(timezone.utc).timestamp()), ex=3600)
    except Exception as exc:
        logger.warning(
            "Failed to update continuation task tracking keys for project %s task %s: %s",
            project_id,
            task.id,
            exc,
        )
    append_image_generation_log(
        project_id,
        run_id,
        "task_continuation_queued",
        task_id=task.id,
        remaining_page_nums=remaining_page_nums,
    )
    return task


@celery_app.task
def generate_slides_task(
    project_id: str,
    page_nums: list = None,
    prototype: bool = False,
    run_id: str = None,
    credential_id: str = None,
    **legacy_kwargs,
):
    """Celery task: 执行幻灯片生成流水线（页级别锁，允许不同页并发生成）。"""
    if legacy_kwargs:
        logger.info("Ignoring legacy generation task kwargs: %s", sorted(legacy_kwargs.keys()))
    try:
        credentials = load_task_provider_credentials(redis_client, credential_id)
    except RuntimeError as exc:
        message = "任务凭据读取失败，请重新发起生成。"
        logger.error("Generation task credential failure: project=%s run=%s error=%s", project_id, run_id, exc)
        append_image_generation_log(project_id, run_id, "task_failed_before_start", reason="credential_error", error=str(exc))
        _finish_run_for_credential_error(run_id, message, exc)
        return {"project_id": project_id, "status": "failed", "error": message}
    with provider_credentials_context(credentials):
        return _generate_slides_task_inner(project_id, page_nums, prototype, run_id, credential_id=credential_id)


def _generate_slides_task_inner(
    project_id: str,
    page_nums: list = None,
    prototype: bool = False,
    run_id: str = None,
    credential_id: str = None,
):
    """Celery task body with provider credentials installed in context."""
    append_image_generation_log(
        project_id,
        run_id,
        "task_started",
        requested_page_nums=page_nums,
        prototype=prototype,
    )
    if run_id:
        db = SessionLocal()
        try:
            if not is_run_active(db, run_id):
                append_image_generation_log(
                    project_id,
                    run_id,
                    "task_skipped",
                    reason="run_not_active",
                    requested_page_nums=page_nums,
                )
                return {"project_id": project_id, "status": "stale", "reason": "run_not_active"}
        finally:
            db.close()

    target_pages = _resolve_target_pages(project_id, page_nums)
    if not target_pages:
        logger.warning(f"No pages to generate for project {project_id}")
        append_image_generation_log(project_id, run_id, "task_skipped", reason="no_pages")
        db = SessionLocal()
        try:
            finish_run(db, run_id, status="stale", message="没有可生成的页面", error_msg="no_pages")
            db.commit()
        finally:
            db.close()
        return {"project_id": project_id, "status": "skipped", "reason": "no_pages"}

    current_pages, remaining_pages = _split_generation_pages(target_pages)
    if remaining_pages:
        append_image_generation_log(
            project_id,
            run_id,
            "task_chunk_selected",
            current_page_nums=current_pages,
            remaining_page_nums=remaining_pages,
            chunk_size=_image_task_page_chunk_size(),
        )

    # 尝试获取每页的锁，只锁住没被其他任务占用的页
    acquired_pages = _acquire_page_locks(project_id, current_pages)
    if not acquired_pages:
        logger.info(f"All target pages for project {project_id} are already being generated")
        append_image_generation_log(
            project_id,
            run_id,
            "task_skipped",
            reason="all_pages_locked",
            target_page_nums=current_pages,
        )
        _cleanup_stale_generating_slides(project_id, current_pages)
        db = SessionLocal()
        try:
            finish_run(db, run_id, status="stale", message="所有目标页面都已被其他任务锁定", error_msg="all_pages_locked")
            db.commit()
        finally:
            db.close()
        return {"project_id": project_id, "status": "skipped", "reason": "all_pages_locked"}

    skipped_pages = [p for p in current_pages if p not in acquired_pages]
    if skipped_pages:
        logger.info(f"Skipping locked pages: {skipped_pages}")
        append_image_generation_log(
            project_id,
            run_id,
            "task_partially_locked",
            acquired_page_nums=acquired_pages,
            skipped_page_nums=skipped_pages,
        )
        _cleanup_stale_generating_slides(project_id, skipped_pages)

    db = SessionLocal()
    continuation_enqueued = False
    try:
        logger.info(f"Celery task started: project={project_id}, pages={acquired_pages}, prototype={prototype}")
        run = get_run(db, run_id)
        run_stage = image_generation_run_stage(
            kind=run.kind if run else None,
            prototype=prototype,
            page_nums=acquired_pages,
        )
        mark_run_running(db, run_id, stage=run_stage, message=image_generation_running_message(run_stage))
        db.commit()
        run_generation_pipeline(
            project_id,
            db,
            page_nums=acquired_pages,
            prototype=prototype,
            run_id=run_id,
            defer_finalization=bool(remaining_pages),
        )
        # 被跳过的页（因锁占用）不能永久丢失，加回剩余队列
        if skipped_pages:
            remaining_pages = list(dict.fromkeys(remaining_pages + skipped_pages))

        if remaining_pages and is_run_active(db, run_id):
            next_task = _enqueue_generation_continuation(
                db,
                project_id,
                remaining_pages,
                prototype=prototype,
                run_id=run_id,
                credential_id=credential_id,
            )
            continuation_enqueued = bool(next_task)
        # 生成完成，设置未读通知提醒用户查看
        project = db.query(Project).filter(Project.id == project_id).first()
        if project and not continuation_enqueued:
            project.has_unread_notification = True
            project.unread_notification_message = "打样完成" if prototype else "图片生成完成"
        db.commit()
        logger.info(f"Celery task completed: project={project_id}")
        append_image_generation_log(
            project_id,
            run_id,
            "task_finished",
            status="completed",
            acquired_page_nums=acquired_pages,
            skipped_page_nums=skipped_pages,
            remaining_page_nums=remaining_pages,
            prototype=prototype,
        )
        return {
            "project_id": project_id,
            "status": "continued" if continuation_enqueued else "completed",
            "page_nums": acquired_pages,
            "remaining_page_nums": remaining_pages,
            "prototype": prototype,
            "skipped_pages": skipped_pages,
        }
    except Exception as exc:
        logger.error(f"Celery task failed: {exc}")
        append_image_generation_log(
            project_id,
            run_id,
            "task_finished",
            status="failed",
            error=str(exc)[:1000],
            acquired_page_nums=acquired_pages,
            prototype=prototype,
        )
        try:
            project = db.query(Project).filter(Project.id == project_id).first()
            if project:
                has_any_completed = db.query(Slide).filter(
                    Slide.project_id == project_id, Slide.status == "completed"
                ).count() > 0
                if not has_any_completed:
                    project.status = "failed"
                else:
                    # 部分完成 → 回到 prompt_ready，用户可重试失败页
                    project.status = "prompt_ready"
                finish_run(db, run_id, status="failed", message="图片生成任务失败", error_msg=str(exc)[:500])
                db.commit()
        except Exception as cleanup_err:
            logger.warning(f"Failed to update project status after task error: {cleanup_err}")
        return {"project_id": project_id, "status": "failed", "error": str(exc), "page_nums": acquired_pages, "prototype": prototype}
    finally:
        _release_page_locks(project_id, acquired_pages)
        # 清理任何可能残留的孤儿锁（包括被跳过或尚未处理的页）
        all_related_pages = list(dict.fromkeys(acquired_pages + skipped_pages + remaining_pages))
        if all_related_pages:
            try:
                _cleanup_stale_generating_slides(project_id, all_related_pages)
            except Exception as exc:
                logger.warning("Failed to cleanup stale generating slides in finally for project %s: %s", project_id, exc)
        try:
            if not continuation_enqueued:
                redis_client.delete(f"project:{project_id}:task_id")
                redis_client.delete(f"project:{project_id}:task_started_at")
        except Exception as exc:
            logger.warning("Failed to delete generation task tracking keys for project %s: %s", project_id, exc)
        clear_reference_upload_cache()
        db.close()


@celery_app.task
def generate_editable_pptx_task(
    project_id: str,
    run_id: str = None,
    credential_id: str = None,
    restore_mode: str = "standard",
    **legacy_kwargs,
):
    """Celery task: build an optional editable PPTX from the completed image deck."""
    if legacy_kwargs:
        logger.info("Ignoring legacy editable PPTX task kwargs: %s", sorted(legacy_kwargs.keys()))
    try:
        credentials = load_task_provider_credentials(redis_client, credential_id)
    except RuntimeError as exc:
        message = "任务凭据读取失败，请重新准备可编辑版。"
        logger.error("Editable PPTX task credential failure: project=%s run=%s error=%s", project_id, run_id, exc)
        _finish_run_for_credential_error(run_id, message, exc)
        return {"project_id": project_id, "status": "failed", "error": message}
    with provider_credentials_context(credentials):
        return _generate_editable_pptx_task_inner(project_id, run_id=run_id, restore_mode=restore_mode)


def _generate_editable_pptx_task_inner(project_id: str, run_id: str = None, restore_mode: str = "standard"):
    mode = normalize_editable_pptx_restore_mode(restore_mode)
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            finish_run(db, run_id, status="failed", message="项目不存在", error_msg="project_not_found")
            db.commit()
            return {"project_id": project_id, "status": "not_found"}

        slides = db.query(Slide).filter(Slide.project_id == project_id).order_by(Slide.page_num).all()
        if not slides:
            finish_run(db, run_id, status="failed", message="没有可处理的页面", error_msg="no_slides")
            db.commit()
            return {"project_id": project_id, "status": "no_slides"}

        slide_images = build_project_slide_images(slides)
        for slide_data in slide_images:
            resolved = _resolve_task_file_path(slide_data.get("image_path"))
            slide_data["image_path"] = resolved or ""
        missing_pages = [
            int(slide.page_num)
            for slide in slides
            if slide.status != "completed" or not _resolve_task_file_path(slide.image_path) or not os.path.exists(str(_resolve_task_file_path(slide.image_path)))
        ]
        if missing_pages:
            message = f"第 {', '.join(map(str, missing_pages))} 页还没有完整生成，暂时不能准备可编辑版。"
            finish_run(db, run_id, status="failed", message=message, error_msg="missing_slide_images")
            db.commit()
            return {"project_id": project_id, "status": "failed", "error": message}

        output_dir = os.path.join(settings.OUTPUT_DIR or "./outputs", project_id)
        output_filename = "editable_presentation.pptx" if mode == "standard" else f"editable_presentation_{mode}.pptx"
        output_path = os.path.join(output_dir, output_filename)
        work_dir = os.path.join(output_dir, ".editable_pptx_assets", mode)

        mark_run_running(db, run_id, stage="editable_pptx", message="正在准备可编辑版...")
        db.commit()

        def report_progress(completed: int, total: int, message: str):
            update_run_progress(
                db,
                run_id,
                stage="editable_pptx",
                completed_count=completed,
                total_count=total,
                message=message,
            )
            db.commit()

        result = build_editable_pptx(
            slide_images=slide_images,
            output_path=output_path,
            progress_callback=report_progress,
            work_dir=work_dir,
            restore_mode=mode,
            reuse_ocr_cache=False,
        )
        failed_count = len(result.ocr_failed_pages)
        if failed_count:
            message = f"可编辑版已生成，{failed_count} 页保留为图片"
        else:
            message = "可编辑版已生成"
        finish_run(
            db,
            run_id,
            status="succeeded",
            message=message,
            completed_count=result.slide_count,
            failed_count=failed_count,
        )
        project.has_unread_notification = True
        project.unread_notification_message = "可编辑版已准备好"
        db.commit()
        logger.info(
            "Editable PPTX task completed: project=%s slides=%s text_boxes=%s visual_assets=%s failed_pages=%s",
            project_id,
            result.slide_count,
            result.text_box_count,
            result.visual_asset_count,
            result.ocr_failed_pages,
        )
        return {
            "project_id": project_id,
            "status": "completed",
            "restore_mode": mode,
            "output_path": result.output_path,
            "slide_count": result.slide_count,
            "text_box_count": result.text_box_count,
            "visual_asset_count": result.visual_asset_count,
            "ocr_failed_pages": result.ocr_failed_pages,
        }
    except Exception as exc:
        db.rollback()
        logger.exception("Editable PPTX task failed: project=%s", project_id)
        try:
            finish_run(db, run_id, status="failed", message="可编辑版生成失败", error_msg=str(exc)[:500])
            db.commit()
        except Exception as cleanup_exc:
            db.rollback()
            logger.warning("Failed to mark editable PPTX run failed: %s", cleanup_exc)
        return {"project_id": project_id, "status": "failed", "error": str(exc)}
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=2)
def generate_style_proposals_task(
    self,
    project_id: str,
    run_id: str = None,
    credential_id: str = None,
    user_description: str | None = None,
    previous_proposal: dict | None = None,
    **legacy_kwargs,
):
    """Celery task: 生成风格提案。"""
    if legacy_kwargs:
        logger.info("Ignoring legacy style proposal task kwargs: %s", sorted(legacy_kwargs.keys()))
    try:
        credentials = load_task_provider_credentials(redis_client, credential_id)
    except RuntimeError as exc:
        message = "任务凭据读取失败，请重新生成风格提案。"
        logger.error("Style proposal task credential failure: project=%s run=%s error=%s", project_id, run_id, exc)
        _finish_run_for_credential_error(run_id, message, exc)
        return {"project_id": project_id, "status": "failed", "error": message}
    with provider_credentials_context(credentials):
        return _generate_style_proposals_task_inner(
            self,
            project_id,
            run_id,
            user_description=user_description,
            previous_proposal=previous_proposal,
        )


def _generate_style_proposals_task_inner(
    self,
    project_id: str,
    run_id: str = None,
    user_description: str | None = None,
    previous_proposal: dict | None = None,
):
    """Style proposal task body with provider credentials installed in context."""
    db = SessionLocal()
    try:
        logger.info(f"[StyleProposals Celery] Starting for project={project_id}")
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            logger.warning(f"[StyleProposals Celery] Project not found: {project_id}")
            finish_run(db, run_id, status="failed", message="项目不存在", error_msg="project_not_found")
            db.commit()
            return {"project_id": project_id, "status": "not_found"}

        slides = db.query(Slide).filter(Slide.project_id == project_id).order_by(Slide.page_num).all()
        if not slides:
            logger.warning(f"[StyleProposals Celery] No slides found for project: {project_id}")
            finish_run(db, run_id, status="failed", message="没有内容规划页面", error_msg="no_slides")
            db.commit()
            return {"project_id": project_id, "status": "no_slides"}

        mark_run_running(db, run_id, stage="style_proposal", message="正在准备风格提案上下文...")
        db.commit()

        content_plan = [s.content_json for s in slides]

        # 自动分析项目素材（失败不阻断主流程）
        assets = {}
        user_description = (user_description or "").strip()
        if user_description:
            assets["user_description"] = user_description[:4000]
        if previous_proposal and isinstance(previous_proposal, dict):
            assets["previous_proposal"] = previous_proposal
            logger.info(
                "[StyleProposals Celery] Carrying previous_proposal for regeneration: project=%s name=%s",
                project_id,
                previous_proposal.get("name"),
            )
        if project.reference_images:
            update_run_progress(db, run_id, stage="asset_analysis", message="正在读取项目素材...")
            db.commit()
            logo_refs = [r for r in project.reference_images if r.role == "logo" and is_logo_confirmed(r)]
            style_refs = [r for r in project.reference_images if r.role == "style_ref"]
            template_refs = [r for r in project.reference_images if r.role == "template"]

            if logo_refs:
                cached = _first_cached_reference_analysis(logo_refs)
                if cached:
                    logger.info(f"[StyleProposals Celery] Reusing cached logo analysis for project={project_id}")
                    assets["logo_analysis"] = cached
                else:
                    try:
                        logger.info(f"[StyleProposals Celery] Analyzing logo lockup for project={project_id}")
                        existing_logo_paths = [r.file_path for r in logo_refs if os.path.exists(r.file_path)]
                        logo_path = prepare_logo_lockup_image(existing_logo_paths) if existing_logo_paths else None
                        if logo_path:
                            logo_analysis = analyze_logo(logo_path)
                            assets["logo_analysis"] = logo_analysis
                            if logo_analysis and logo_refs:
                                logo_refs[0].asset_analysis = {"analysis_status": "completed", **logo_analysis}
                    except Exception as e:
                        logger.warning(f"[StyleProposals Celery] Logo analysis failed: {e}")

            if style_refs:
                cached_analyses = _cached_reference_analyses(style_refs)
                if cached_analyses:
                    logger.info(f"[StyleProposals Celery] Reusing cached reference analysis for project={project_id}")
                    assets["reference_analysis"] = cached_analyses[0]
                    assets["reference_analyses"] = cached_analyses
                else:
                    existing_style_refs = [
                        ref for ref in style_refs
                        if _file_exists(getattr(ref, "file_path", None))
                    ]
                    if not existing_style_refs:
                        logger.warning(
                            "[StyleProposals Celery] Skipping %s missing style reference file(s) for project=%s; generating from remaining context",
                            len(style_refs),
                            project_id,
                        )
                    else:
                        reference_analyses = []
                        for style_ref in existing_style_refs:
                            try:
                                logger.info(f"[StyleProposals Celery] Analyzing reference image for project={project_id}")
                                reference_analysis = analyze_reference_image(style_ref.file_path)
                                if reference_analysis:
                                    reference_analyses.append(reference_analysis)
                                    style_ref.asset_analysis = {"analysis_status": "completed", **reference_analysis}
                            except Exception as e:
                                logger.warning(f"[StyleProposals Celery] Reference analysis failed: {e}")
                        if reference_analyses:
                            assets["reference_analysis"] = reference_analyses[0]
                            assets["reference_analyses"] = reference_analyses

            if template_refs or project.selected_template_recommendations:
                assets["template_analysis"] = {"has_template": True}
                template_sample = None
                template_ref = None
                recommendations = project.selected_template_recommendations or {}
                if isinstance(recommendations, dict):
                    assets["template_analysis"]["template_page_count"] = sum(
                        1 for value in recommendations.values() if isinstance(value, dict)
                    )
                    for key in ("cover", "content", "data", "section", "quote", "toc", "ending"):
                        rec = recommendations.get(key)
                        if isinstance(rec, dict) and rec.get("file_path"):
                            template_sample = rec["file_path"]
                            assets["template_analysis"]["sample_category"] = key
                            assets["template_analysis"]["source_kind"] = rec.get("source_kind") or "template"
                            assets["template_analysis"]["application_strength"] = rec.get("application_strength") or "standard"
                            break
                if template_refs:
                    template_ref = next((r for r in template_refs if r.file_path == template_sample), None) if template_sample else None
                    template_ref = template_ref or _first_existing_ref(template_refs) or template_refs[0]
                    analysis = template_ref.asset_analysis if isinstance(template_ref.asset_analysis, dict) else {}
                    assets["template_analysis"].setdefault("source_kind", analysis.get("source_kind") or "template")
                if not template_sample and template_ref and _file_exists(template_ref.file_path):
                    template_sample = template_ref.file_path
                cached = _cached_reference_analysis(template_ref) if template_ref else None
                if cached:
                    logger.info(f"[StyleProposals Celery] Reusing cached template analysis for project={project_id}")
                    assets["template_analysis"]["reference_analysis"] = cached
                elif template_sample and _file_exists(template_sample):
                    try:
                        logger.info(f"[StyleProposals Celery] Analyzing template reference for project={project_id}")
                        template_analysis = analyze_reference_image(template_sample)
                        assets["template_analysis"]["reference_analysis"] = template_analysis
                        if template_analysis and template_ref:
                            template_ref.asset_analysis = {"analysis_status": "completed", **template_analysis}
                    except Exception as e:
                        logger.warning(f"[StyleProposals Celery] Template analysis failed: {e}")
                elif template_refs:
                    logger.warning(
                        "[StyleProposals Celery] Skipping missing template reference file(s) for project=%s; generating from remaining context",
                        project_id,
                    )

        update_run_progress(db, run_id, stage="style_proposal", message="正在生成风格提案...")
        db.commit()

        proposals = generate_style_proposals(content_plan, assets=assets if assets else None)
        db.expire_all()
        if not is_run_active(db, run_id):
            logger.info(f"[StyleProposals Celery] Run {run_id} is no longer active; skipping stale writeback")
            return {"project_id": project_id, "status": "stale"}
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            finish_run(db, run_id, status="failed", message="项目不存在", error_msg="project_not_found")
            db.commit()
            return {"project_id": project_id, "status": "not_found"}
        asset_based = any(
            p.get("source") in {"asset_clone", "asset_based"} or p.get("clone_mode") == "strict_reference"
            for p in proposals
            if isinstance(p, dict)
        )
        update_run_progress(db, run_id, completed_count=len(proposals), total_count=len(proposals), message="正在保存风格提案...")
        project.style_proposal = {
            "proposals": proposals,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "policy_version": STYLE_PROPOSAL_POLICY_VERSION,
            "asset_based": asset_based,
            "asset_signature": compute_style_asset_signature(project),
            "content_signature": content_signature(project.slides or []),
            "user_description": user_description[:4000],
        }
        # 风格提案生成后，项目进入视觉方案待确认阶段
        if project.status in ("planning", "draft"):
            project.status = "visual_ready"
        project.content_plan_confirmed = True
        finish_run(
            db,
            run_id,
            status="succeeded",
            message=f"风格提案已生成，共 {len(proposals)} 套",
            completed_count=len(proposals),
        )
        # 风格提案生成完成，设置未读通知
        project.has_unread_notification = True
        project.unread_notification_message = "风格提案已生成"
        db.commit()
        logger.info(f"[StyleProposals Celery] Completed for project={project_id}, count={len(proposals)}, asset_based={asset_based}")
        return {"project_id": project_id, "status": "completed", "proposals_count": len(proposals), "asset_based": asset_based}
    except Exception as exc:
        db.rollback()
        logger.error(f"[StyleProposals Celery] Failed for project={project_id}: {exc}")
        if self.request.retries >= self.max_retries:
            # 重试已用完，标记项目失败，避免前端永远轮询
            try:
                project = db.query(Project).filter(Project.id == project_id).first()
                if project:
                    project.status = "failed"
                    finish_run(db, run_id, status="failed", message="风格提案生成失败", error_msg=str(exc)[:500])
                    db.commit()
                    logger.info(f"[StyleProposals Celery] Marked project={project_id} as failed after max retries")
            except Exception as mark_err:
                logger.warning(f"[StyleProposals Celery] Failed to mark project failed: {mark_err}")
            raise
        raise self.retry(exc=exc, countdown=15)
    finally:
        db.close()
