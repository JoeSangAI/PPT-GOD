import asyncio
import copy
import io
import json
import logging
import os
import shutil
import time
from typing import List, Optional
from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image as PILImage
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from app.models.base import get_db, SessionLocal
from app.models.models import Project, Slide, ReferenceImage, SlideVersion
from app.schemas.project import SlideResponse
from app.core.llm_client import get_llm_client
from app.utils.project_docs import load_project_documents
from app.utils.text_cleaning import normalize_markdown_emphasis
from app.utils.reference_image import (
    ALLOWED_VISUAL_ASSET_KINDS,
    default_visual_asset_process_mode,
    normalize_visual_asset_kind,
    reference_process_mode_instruction,
)

# 全局运行中任务跟踪（project_id -> asyncio.Task）
_running_tasks: dict = {}
_running_tasks_lock = asyncio.Lock()
from app.core.config import settings
from app.services.content_plan import generate_content_plan
from app.services.logo_policy import (
    DEFAULT_LOGO_ANCHOR,
    LOGO_ANCHORS,
    logo_anchor_from_ref,
    normalize_logo_anchor,
    should_show_logo,
    should_use_logo_as_scene_asset,
)
from app.services.logo_assets import prepare_logo_overlay_image
from app.services.visual_plan import generate_visual_plan
from app.services.prompt_engine import generate_prompt_for_page, generate_prompts_for_all_pages
from app.services.style_pack import derive_style_pack_from_content, style_pack_from_selected_style
from app.services.image_analyzer import analyze_reference_image, analyze_visual_asset
from app.tasks import generate_slides_task, redis_client
from app.services.run_state import (
    cancel_active_run,
    create_project_run,
    finish_run,
    get_active_run,
    get_latest_run,
    mark_run_running,
    normalize_confirmed_project_stage,
    reconcile_project_state,
    serialize_run,
    serialize_workflow_status,
    set_run_task,
    stale_active_run,
    target_counts,
    update_run_progress,
    generation_progress,
    cleanup_generation_progress,
)
from celery.result import AsyncResult

router = APIRouter(prefix="/projects", tags=["slides"])
logger = logging.getLogger(__name__)

# 上传限制常量
MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # 20MB
MAX_REFERENCE_IMAGES_PER_PAGE = 10
ALLOWED_UPLOAD_ROLES = {"style_ref", "logo", "template", "visual_asset", "content_ref", "chart_ref", "finetune_ref"}
MAX_VISUAL_ASSETS_PER_PROJECT = 30
ALLOWED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/svg+xml",
    "image/heic",
    "image/heif",
    "image/bmp",
    "image/tiff",
}

# 全局生成进度存储（内存级，项目重启后丢失），定义在 run_state.py，此处引用


def _style_text_from_selected_style(selected_style: dict | str | None) -> str | None:
    return style_pack_from_selected_style(selected_style)


def _style_override_from_text(style_text: str | None) -> dict | None:
    if not style_text:
        return None
    palette = []
    mood = ""
    style_name = "Content-derived style pack"
    for line in style_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Style:"):
            style_name = stripped.split(":", 1)[1].strip() or style_name
        elif stripped.startswith("Palette:"):
            palette = [p.strip() for p in stripped.split(":", 1)[1].split(",") if p.strip()][:5]
        elif stripped.startswith("Mood:"):
            mood = stripped.split(":", 1)[1].strip()
    return {
        "meta": {
            "theme": style_name,
            "style_name": style_name,
            "palette": palette,
            "mood": mood,
        },
        "body": style_text,
    }


def _derive_project_style_pack(project: Project, content_plan: list[dict]) -> str:
    selected = _style_text_from_selected_style(project.selected_style)
    if selected:
        return selected
    analyses = []
    for ref in project.reference_images or []:
        if ref.role != "style_ref" or ref.slide_id or not os.path.exists(ref.file_path):
            continue
        try:
            analyses.append(analyze_reference_image(ref.file_path))
        except Exception as exc:
            logger.warning(f"StylePack: failed to analyze style_ref {ref.file_path}: {exc}")
    return derive_style_pack_from_content(content_plan, reference_analyses=analyses)


def _project_template_refs_for_prompt(project: Project) -> list[dict]:
    """Template pages are global layout references; style_ref is handled as text style."""
    refs = []
    for img in project.reference_images or []:
        if img.role != "template":
            continue
        refs.append({
            "id": img.id,
            "role": img.role,
            "process_mode": img.process_mode,
            "description": img.file_path,
        })
    return refs


def _project_logo_refs_for_prompt(project: Project, page_intent: dict | None = None) -> list[dict]:
    refs = []
    for img in project.reference_images or []:
        if img.role != "logo" or img.slide_id:
            continue
        if not should_use_logo_as_scene_asset(page_intent or {}, img):
            break
        refs.append({
            "id": img.id,
            "role": "logo",
            "process_mode": "blend",
            "description": img.file_path,
            "logo_anchor": logo_anchor_from_ref(img),
        })
        break
    return refs


def _project_logo_ref(project: Project) -> ReferenceImage | None:
    return next(
        (
            img for img in project.reference_images or []
            if img.role == "logo" and not img.slide_id
        ),
        None,
    )


def _logo_overlay_url(ref: ReferenceImage, project_id: str) -> str | None:
    if ref.role != "logo" or not ref.file_path or not os.path.exists(ref.file_path):
        return None
    overlay_path = prepare_logo_overlay_image(ref.file_path)
    if not overlay_path or not os.path.exists(overlay_path):
        return None
    return f"/uploads/{project_id}/{os.path.basename(overlay_path)}"


def _with_project_logo_policy(page_intent: dict | None, project: Project) -> dict | None:
    if not isinstance(page_intent, dict):
        return page_intent
    logo_ref = _project_logo_ref(project)
    if not logo_ref:
        return page_intent
    intent = copy.deepcopy(page_intent)
    policy = intent.get("logo_policy") if isinstance(intent.get("logo_policy"), dict) else {}
    page_type = str(intent.get("type") or "").lower()
    if page_type not in {"cover", "ending"}:
        policy["placement"] = logo_anchor_from_ref(logo_ref)
    intent["logo_policy"] = policy
    return intent


def _visual_asset_summary(ref: ReferenceImage) -> str:
    analysis = ref.asset_analysis if isinstance(ref.asset_analysis, dict) else {}
    parts = []
    if ref.asset_name:
        parts.append(f"name={ref.asset_name}")
    if ref.asset_kind:
        parts.append(f"kind={ref.asset_kind}")
    if ref.usage_note:
        parts.append(f"user_note={ref.usage_note}")
    subject = analysis.get("subject")
    if subject and subject != ref.asset_name:
        parts.append(f"subject={subject}")
    if analysis.get("description"):
        parts.append(f"description={analysis['description']}")
    identity_elements = analysis.get("identity_elements")
    if isinstance(identity_elements, list) and identity_elements:
        parts.append("identity_elements=" + "、".join(str(x) for x in identity_elements[:6]))
    features = analysis.get("distinctive_features")
    if isinstance(features, list) and features:
        parts.append("features=" + "、".join(str(x) for x in features[:5]))
    must_not_change = analysis.get("must_not_change")
    if isinstance(must_not_change, list) and must_not_change:
        parts.append("must_not_change=" + "、".join(str(x) for x in must_not_change[:6]))
    keywords = analysis.get("suggested_keywords")
    if isinstance(keywords, list) and keywords:
        parts.append("keywords=" + "、".join(str(x) for x in keywords[:8]))
    if analysis.get("recommended_usage"):
        parts.append(f"recommended_usage={analysis['recommended_usage']}")
    if analysis.get("fidelity_note"):
        parts.append(f"fidelity={analysis['fidelity_note']}")
    return "; ".join(parts)


def _project_visual_assets_for_planning(project: Project) -> list[dict]:
    assets = []
    for ref in project.reference_images or []:
        if ref.role != "visual_asset" or ref.slide_id:
            continue
        assets.append({
            "id": ref.id,
            "name": ref.asset_name or os.path.splitext(os.path.basename(ref.file_path or ""))[0],
            "kind": ref.asset_kind or "other",
            "process_mode": ref.process_mode or default_visual_asset_process_mode(ref.asset_kind),
            "usage_note": ref.usage_note or "",
            "analysis_summary": _visual_asset_summary(ref),
        })
    return assets


def _project_refs_for_prompt(
    project: Project,
    visual_asset_ids: list[str] | None = None,
    page_intent: dict | None = None,
) -> list[dict]:
    refs = []
    wanted = set(visual_asset_ids or [])
    if wanted:
        for img in project.reference_images or []:
            if img.role != "visual_asset" or img.id not in wanted:
                continue
            refs.append({
                "id": img.id,
                "role": img.role,
                "process_mode": img.process_mode or default_visual_asset_process_mode(img.asset_kind),
                "description": _visual_asset_summary(img) or img.file_path,
                "asset_name": img.asset_name,
                "asset_kind": img.asset_kind,
                "usage_note": img.usage_note,
            })
    refs.extend(_project_logo_refs_for_prompt(project, page_intent))
    refs.extend(_project_template_refs_for_prompt(project))
    return refs


def _invalidate_visual_asset_dependent_outputs(project: Project):
    """
    Core visual assets affect matching, prompts, and generated images, but not the
    selected style. Keep the style choice intact and move downstream outputs back
    to visual planning.
    """
    if project.status in {"prompt_ready", "prototype_ready", "completed", "failed"}:
        project.status = "visual_ready"
    for slide in project.slides or []:
        if slide.prompt_text:
            slide.prompt_text = None
        if slide.status in {"prompt_ready", "completed", "failed"}:
            slide.status = "visual_ready"


def _clear_slide_generation_outputs(slide: Slide, *, clear_visual: bool):
    if clear_visual:
        slide.visual_json = {}
    slide.prompt_text = None
    slide.image_path = None
    slide.error_msg = None


def _invalidate_content_dependent_outputs(project: Project):
    """Content edits require a fresh confirmation before visual work continues."""
    project.content_plan_confirmed = False
    project.style_proposal = None
    project.selected_style = None
    project.status = "planning" if project.slides else "draft"
    for slide in project.slides or []:
        _clear_slide_generation_outputs(slide, clear_visual=True)
        slide.status = "pending"


def _invalidate_style_dependent_outputs(project: Project):
    """Style-source changes keep confirmed content, but invalidate visual outputs."""
    project.style_proposal = None
    project.selected_style = None
    if project.slides:
        project.status = "visual_ready" if project.content_plan_confirmed else "planning"
    for slide in project.slides or []:
        _clear_slide_generation_outputs(slide, clear_visual=True)
        slide.status = "pending"


def _invalidate_visual_plan_dependent_outputs(project: Project, slides: list[Slide]):
    """Visual edits/page refs invalidate prompts and images, not confirmed content."""
    if project.status in {"prompt_ready", "prototype_ready", "completed", "failed"}:
        project.status = "visual_ready"
    for slide in slides:
        slide.prompt_text = None
        slide.image_path = None
        slide.error_msg = None
        if slide.status in {"prompt_ready", "completed", "failed"}:
            slide.status = "visual_ready"


def _resolve_generation_page_nums(slides: list[Slide], requested_page_nums: list[int] | None, prototype: bool) -> list[int] | None:
    """Resolve generation targets. Prototype without explicit pages samples the first 3 slides."""
    if prototype and not requested_page_nums:
        return [s.page_num for s in slides[:3]]
    return requested_page_nums


def _normalize_content_json_markdown(content_json: dict) -> dict:
    """Normalize Markdown fields at API boundaries while preserving structure."""
    if not isinstance(content_json, dict):
        return content_json
    content = copy.deepcopy(content_json)
    text_content = content.get("text_content")
    if isinstance(text_content, dict):
        for key in ("headline", "subhead", "body"):
            value = text_content.get(key)
            if isinstance(value, str):
                text_content[key] = normalize_markdown_emphasis(value)
            elif isinstance(value, list):
                text_content[key] = [
                    normalize_markdown_emphasis(item) if isinstance(item, str) else item
                    for item in value
                ]
    if isinstance(content.get("speaker_notes"), str):
        content["speaker_notes"] = normalize_markdown_emphasis(content["speaker_notes"])
    return content


def _update_progress(project_id: str, data: dict, run_id: str | None = None):
    """更新指定项目的生成进度。"""
    generation_progress[project_id] = data
    if run_id:
        db = SessionLocal()
        try:
            update_run_progress(
                db,
                run_id,
                stage=data.get("stage"),
                message=data.get("message"),
                completed_count=data.get("current_page"),
                total_count=data.get("total_pages") or data.get("target_count"),
            )
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning(f"Failed to persist run progress for {run_id}: {exc}")
        finally:
            db.close()


def _mark_generation_idle(project: Project | None, db: Session, reason: str):
    """Return a stale generating project to a recoverable state."""
    if project:
        slides = db.query(Slide).filter(
            Slide.project_id == project.id,
            Slide.status == "generating",
        ).all()
        for slide in slides:
            slide.status = "prompt_ready"
            slide.error_msg = reason
        if project.status not in {"draft", "planning", "visual_ready", "prompt_ready", "prototype_ready", "completed", "failed"}:
            project.status = "prompt_ready"
        db.commit()


def _format_reference_analysis(analysis: dict) -> str:
    parts = []
    if analysis.get("description"):
        parts.append(str(analysis["description"]))
    if analysis.get("composition_style"):
        parts.append(f"composition={analysis['composition_style']}")
    if analysis.get("mood"):
        parts.append(f"mood={analysis['mood']}")
    colors = analysis.get("colors")
    if isinstance(colors, dict):
        color_text = ", ".join(f"{k}:{v}" for k, v in colors.items() if v)
        if color_text:
            parts.append(f"colors={color_text}")
    if analysis.get("font_suggestion"):
        parts.append(f"font={analysis['font_suggestion']}")
    return "; ".join(parts)


def _build_slide_reference_contexts(
    slides: list[Slide],
) -> tuple[dict[int, list[str]], dict[int, str]]:
    """Analyze page-level reference images for visual/prompt regeneration.

    Returns:
        contexts: verbose lines for LLM (existing behavior)
        user_hints: page_num -> short Chinese copy for humans (must appear in 画面描述)
    """
    contexts: dict[int, list[str]] = {}
    user_hints: dict[int, str] = {}
    mode_zh = {"blend": "融合", "crop": "裁剪", "original": "完整保留原图"}

    for slide in slides:
        page_contexts = []
        hint_parts: list[str] = []
        ref_count = len(slide.reference_images or [])
        logger.info(f"RefContext: slide {slide.page_num} has {ref_count} reference_images")
        for idx, ref in enumerate(slide.reference_images or [], start=1):
            file_exists = os.path.exists(ref.file_path)
            logger.info(f"RefContext: slide {slide.page_num} ref {idx} file={ref.file_path} exists={file_exists} role={ref.role}")
            if not file_exists:
                continue
            analysis = analyze_reference_image(ref.file_path)
            summary = _format_reference_analysis(analysis)
            page_contexts.append(
                f"Reference Image {idx}: role={ref.role}; process_mode={ref.process_mode or 'blend'}; "
                f"intent={reference_process_mode_instruction(ref.process_mode)};"
                f"file={os.path.basename(ref.file_path)}; "
                f"actual_input=uploaded_to_image_model_as_reference_{idx}; analysis={summary}"
            )
            mz = mode_zh.get(ref.process_mode or "blend", ref.process_mode or "融合")
            hint_parts.append(f"参考图{idx}（{mz}）：{summary}")

        if page_contexts:
            contexts[slide.page_num] = page_contexts
        if hint_parts:
            user_hints[slide.page_num] = "\n".join(hint_parts)

    logger.info(f"RefContext: built contexts for pages={list(contexts.keys())}, hints for pages={list(user_hints.keys())}")
    return contexts, user_hints


class PageNumsRequest(BaseModel):
    page_nums: Optional[List[int]] = None
    prototype: bool = False


class ContentPlanRequest(BaseModel):
    topic: Optional[str] = None
    page_count: Optional[int] = None


class CreateSlideRequest(BaseModel):
    page_num: int
    content_json: dict


class UpdateContentRequest(BaseModel):
    page_num: int
    slide_id: Optional[str] = None
    content_json: dict


class UpdateVisualRequest(BaseModel):
    page_num: int
    slide_id: Optional[str] = None
    visual_json: dict


class ReorderRequest(BaseModel):
    page_nums: List[int]


def _generate_content_plan_bg(project_id: str, topic: str, page_count: int | None = None, run_id: str | None = None):
    """后台任务：异步生成 Content Plan。"""
    import logging
    logger = logging.getLogger(__name__)
    from app.models.base import SessionLocal
    db = SessionLocal()
    try:
        logger.info(f"[ContentPlan BG] Starting for project={project_id}, topic={topic[:30]}...")
        mark_run_running(db, run_id, stage="content_plan", message="开始生成 Content Plan...")
        db.commit()
        _update_progress(project_id, {"stage": "content_plan", "message": "开始生成 Content Plan...", "current_page": 0, "total_pages": page_count or 10}, run_id)

        documents = load_project_documents(project_id)
        if documents:
            logger.info(f"[ContentPlan BG] Loaded documents, length={len(documents)}")

        def report_progress(data: dict):
            _update_progress(project_id, data, run_id)

        outline = generate_content_plan(
            topic=topic,
            page_count=page_count or 10,
            documents=documents,
            on_progress=report_progress,
        )
        logger.info(f"[ContentPlan BG] Generated {len(outline)} pages")
        update_run_progress(
            db,
            run_id,
            stage="saving",
            message="正在保存结果...",
            total_count=len(outline),
            completed_count=len(outline),
        )
        db.commit()
        _update_progress(project_id, {"stage": "saving", "message": "正在保存结果...", "current_page": len(outline), "total_pages": len(outline)}, run_id)
        # 删除旧的 slides（如果存在）
        db.query(Slide).filter(Slide.project_id == project_id).delete()
        # 保存新的 slides
        for item in outline:
            item = copy.deepcopy(item)
            item.setdefault("page_num", item.get("page_num", 0))
            slide = Slide(
                project_id=project_id,
                page_num=item["page_num"],
                type=item.get("type", "content"),
                content_json=item,
            )
            db.add(slide)
        project = db.query(Project).filter(Project.id == project_id).first()
        if project:
            project.status = "planning"
            project.content_plan_confirmed = False
            project.style_proposal = None
            project.selected_style = None
        finish_run(
            db,
            run_id,
            status="succeeded",
            message=f"内容规划已生成，共 {len(outline)} 页",
            completed_count=len(outline),
        )
        # 内容规划生成完成，设置未读通知
        if project:
            project.has_unread_notification = True
            project.unread_notification_message = "内容规划已生成"
        db.commit()
        logger.info(f"[ContentPlan BG] Completed for project={project_id}")
    except Exception as e:
        db.rollback()
        logger.error(f"[ContentPlan BG] Failed for project={project_id}: {e}")
        _update_progress(project_id, {"stage": "error", "message": f"生成失败：{str(e)[:100]}"}, run_id)
        # 标记项目状态为失败，让前端可以检测
        try:
            project = db.query(Project).filter(Project.id == project_id).first()
            if project:
                project.status = "draft"
            finish_run(db, run_id, status="failed", message="内容规划生成失败", error_msg=str(e)[:500])
            db.commit()
        except Exception:
            pass
    finally:
        # 生成结束后清理内存进度，避免过时进度残留误导前端
        generation_progress.pop(project_id, None)
        db.close()


@router.post("/{project_id}/content-plan")
def create_content_plan(
    project_id: str,
    background_tasks: BackgroundTasks,
    body: ContentPlanRequest = ContentPlanRequest(),
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if get_active_run(db, project_id):
        raise HTTPException(status_code=409, detail="当前项目已有任务正在运行，请等待完成后再开始下一步")

    # 优先使用用户传入的 topic，否则用项目标题
    topic = body.topic.strip() if body.topic else project.title
    page_count = body.page_count

    documents = load_project_documents(project_id)
    if documents:
        logger.info(f"[ContentPlan] Loaded documents for project={project_id}, length={len(documents)}")

    try:
        run = create_project_run(
            db,
            project_id,
            kind="content_plan",
            stage="content_plan",
            total_count=page_count or 10,
            message="内容规划生成已排队",
        )
        db.commit()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    background_tasks.add_task(_generate_content_plan_bg, project_id, topic, page_count, run.id)
    return {"message": "Content plan generation started", "status": project.status, "run": serialize_run(run)}


@router.get("/{project_id}/slides", response_model=List[SlideResponse])
def list_slides(project_id: str, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    slides = (
        db.query(Slide)
        .filter(Slide.project_id == project_id)
        .order_by(Slide.page_num)
        .all()
    )
    # 手动加载所有参考图，避免 joinedload 在 SQLAlchemy 2.0 下的兼容问题
    slide_ids = [s.id for s in slides]
    refs = db.query(ReferenceImage).filter(
        ReferenceImage.slide_id.in_(slide_ids),
        ReferenceImage.role != "finetune_ref",
    ).all() if slide_ids else []
    refs_by_slide = {}
    for ref in refs:
        refs_by_slide.setdefault(ref.slide_id, []).append(ref)

    return [
        {
            "id": s.id,
            "project_id": s.project_id,
            "page_num": s.page_num,
            "type": s.type,
            "status": s.status,
            "content_json": s.content_json,
            "visual_json": s.visual_json,
            "prompt_text": s.prompt_text,
            "image_path": s.image_path,
            "error_msg": s.error_msg,
            "reference_images": [
                {
                    "id": ref.id,
                    "role": ref.role,
                    "process_mode": ref.process_mode or "blend",
                    "asset_name": ref.asset_name,
                    "asset_kind": ref.asset_kind,
                    "usage_note": ref.usage_note,
                    "asset_analysis": ref.asset_analysis,
                    "logo_anchor": ref.logo_anchor or (DEFAULT_LOGO_ANCHOR if ref.role == "logo" else None),
                    "url": f"/uploads/{project_id}/{os.path.basename(ref.file_path)}",
                    "overlay_url": _logo_overlay_url(ref, project_id) if ref.role == "logo" else None,
                }
                for ref in refs_by_slide.get(s.id, [])
            ],
        }
        for s in slides
    ]


@router.post("/{project_id}/visual-plan")
def create_visual_plan(
    project_id: str,
    body: PageNumsRequest = PageNumsRequest(),
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    slides = (
        db.query(Slide)
        .filter(Slide.project_id == project_id)
        .order_by(Slide.page_num)
        .all()
    )
    if not slides:
        raise HTTPException(status_code=400, detail="No content plan found. Generate content plan first.")

    # 手动加载参考图，避免 joinedload 在 SQLAlchemy 2.0 下的兼容问题
    slide_ids = [s.id for s in slides]
    refs = db.query(ReferenceImage).filter(
        ReferenceImage.slide_id.in_(slide_ids),
        ReferenceImage.role != "finetune_ref",
    ).all() if slide_ids else []
    refs_by_slide = {}
    for ref in refs:
        refs_by_slide.setdefault(ref.slide_id, []).append(ref)
    for s in slides:
        s.reference_images = refs_by_slide.get(s.id, [])

    ref_contexts, ref_user_hints = _build_slide_reference_contexts(slides)
    content_plan = []
    for s in slides:
        item = copy.deepcopy(s.content_json) or {}
        item["page_num"] = s.page_num
        if ref_contexts.get(s.page_num):
            item["reference_context"] = "\n".join(ref_contexts[s.page_num])
        if ref_user_hints.get(s.page_num):
            item["reference_user_hint"] = ref_user_hints[s.page_num]
        content_plan.append(item)

    # 打样：只处理选中的页
    if body.page_nums:
        content_plan = [p for p in content_plan if p["page_num"] in body.page_nums]

    # 获取参考图 ID（如果有）
    ref_images = project.reference_images
    ref_ids = [img.id for img in ref_images] if ref_images else None
    global_visual_assets = _project_visual_assets_for_planning(project)
    style_text_override = _derive_project_style_pack(project, content_plan)
    style_override = _style_override_from_text(style_text_override)

    visual_plan = generate_visual_plan(
        content_plan=content_plan,
        style_id=project.style_id or "default",
        reference_image_ids=ref_ids,
        style_override=style_override,
        global_visual_assets=global_visual_assets,
    )

    # 保存 visual_plan 到每页 slide（只更新选中的页，或全部）
    visual_by_page = {v["page_num"]: v for v in visual_plan}
    target_slides = slides
    if body.page_nums:
        target_slides = [s for s in slides if s.page_num in body.page_nums]

    for slide in target_slides:
        slide.visual_json = visual_by_page.get(slide.page_num, {})
        if slide.status in ("pending", "planning"):
            slide.status = "visual_ready"

    # 如果全部都有 visual，项目状态推进
    if all(s.visual_json for s in slides):
        project.status = "visual_ready"
    db.commit()

    return {"message": "Visual plan generated", "slides_count": len(visual_plan), "prototype": bool(body.page_nums)}


@router.post("/{project_id}/prompts")
def create_prompts(
    project_id: str,
    body: PageNumsRequest = PageNumsRequest(),
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    slides = (
        db.query(Slide)
        .filter(Slide.project_id == project_id)
        .order_by(Slide.page_num)
        .all()
    )
    if not slides:
        raise HTTPException(status_code=400, detail="No slides found")

    # 手动加载参考图，避免 joinedload 在 SQLAlchemy 2.0 下的兼容问题
    slide_ids = [s.id for s in slides]
    refs = db.query(ReferenceImage).filter(
        ReferenceImage.slide_id.in_(slide_ids),
        ReferenceImage.role != "finetune_ref",
    ).all() if slide_ids else []
    refs_by_slide = {}
    for ref in refs:
        refs_by_slide.setdefault(ref.slide_id, []).append(ref)
    for s in slides:
        s.reference_images = refs_by_slide.get(s.id, [])

    # 检查是否有 visual_plan
    if not any(s.visual_json for s in slides):
        raise HTTPException(status_code=400, detail="No visual plan found. Generate visual plan first.")

    # 过滤选中的页
    target_slides = slides
    if body.page_nums:
        target_slides = [s for s in slides if s.page_num in body.page_nums]

    ref_contexts, ref_user_hints = _build_slide_reference_contexts(target_slides)
    content_plan = []
    for s in target_slides:
        item = copy.deepcopy(s.content_json) or {}
        item["page_num"] = s.page_num
        if ref_contexts.get(s.page_num):
            item["reference_context"] = "\n".join(ref_contexts[s.page_num])
        if ref_user_hints.get(s.page_num):
            item["reference_user_hint"] = ref_user_hints[s.page_num]
        content_plan.append(item)
    visual_plan = [
        _with_project_logo_policy(s.visual_json, project)
        for s in target_slides
        if s.visual_json
    ]

    # style_ref 已被转成 style_text；Logo 按页面级 policy 融合，visual_asset 按页选择。
    ref_images_by_page = {
        s.page_num: _project_refs_for_prompt(
            project,
            (s.visual_json or {}).get("visual_asset_ids") if isinstance(s.visual_json, dict) else [],
            _with_project_logo_policy(s.visual_json, project) if isinstance(s.visual_json, dict) else None,
        )
        for s in target_slides
    }
    style_text_override = _style_text_from_selected_style(project.selected_style) or _derive_project_style_pack(project, content_plan)

    prompts = generate_prompts_for_all_pages(
        visual_plan=visual_plan,
        content_plan=content_plan,
        style_id="default",
        reference_images_by_page=ref_images_by_page,
        style_text_override=style_text_override,
    )

    # 保存 prompt 到每页 slide
    prompt_by_page = {p["page_num"]: p["prompt"] for p in prompts}
    for slide in target_slides:
        slide.prompt_text = prompt_by_page.get(slide.page_num)
        if slide.prompt_text:
            slide.status = "prompt_ready"

    # 如果全部都有 prompt，项目状态推进
    if all(s.prompt_text for s in slides):
        project.status = "prompt_ready"
    db.commit()

    return {"message": "Prompts generated", "slides_count": len(prompts), "prototype": bool(body.page_nums)}


@router.post("/{project_id}/visual-prompts")
async def create_visual_and_prompts(
    project_id: str,
    body: PageNumsRequest = PageNumsRequest(),
    db: Session = Depends(get_db),
):
    """一步生成视觉方案和生图 Prompt（SSE 流式返回真实进度）。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    slides = (
        db.query(Slide)
        .filter(Slide.project_id == project_id)
        .order_by(Slide.page_num)
        .all()
    )
    if not slides:
        raise HTTPException(status_code=400, detail="No content plan found. Generate content plan first.")

    target_slides = slides
    if body.page_nums:
        target_slides = [s for s in slides if s.page_num in body.page_nums]

    content_plan = []
    for s in target_slides:
        item = copy.deepcopy(s.content_json) or {}
        item["page_num"] = s.page_num
        content_plan.append(item)

    ref_images_project = project.reference_images
    ref_ids = [img.id for img in ref_images_project] if ref_images_project else None

    style_text_override = _derive_project_style_pack(project, content_plan)
    style_override = _style_override_from_text(style_text_override)
    target_page_nums = [s.page_num for s in target_slides] if body.page_nums else None
    try:
        run = create_project_run(
            db,
            project_id,
            kind="visual_prompts",
            stage="visual_planning",
            target_page_nums=target_page_nums,
            total_count=len(target_slides),
            message="画面描述和生图 Prompt 生成已排队",
        )
        db.commit()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # 启动后台任务，独立于 HTTP 连接运行
    async with _running_tasks_lock:
        existing_task = _running_tasks.get(project_id)
        if existing_task and not existing_task.done():
            existing_task.cancel()
            try:
                await existing_task
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(
            _do_generate_visual_and_prompts(project_id, target_page_nums, run.id)
        )
        _running_tasks[project_id] = task

    return {"status": "started", "message": "视觉方案和生图 Prompt 生成已启动，请稍候。", "run": serialize_run(run)}


async def _do_generate_visual_and_prompts(project_id: str, page_nums: Optional[List[int]] = None, run_id: str | None = None):
    """后台任务：生成视觉方案和 Prompt，完成后更新数据库。"""
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            logger.warning(f"Project {project_id} not found for background generation")
            return

        mark_run_running(db, run_id, stage="visual_planning", message="正在分析内容结构，为每一页设计视觉方案...")
        db.commit()

        slides = (
            db.query(Slide)
            .filter(Slide.project_id == project_id)
            .order_by(Slide.page_num)
            .all()
        )
        if not slides:
            logger.warning(f"No slides found for project {project_id}")
            project.status = "planning"
            db.commit()
            return

        target_slides = slides
        if page_nums:
            target_slides = [s for s in slides if s.page_num in page_nums]

        # 手动加载参考图，避免 joinedload 在 SQLAlchemy 2.0 下的兼容问题
        slide_ids = [s.id for s in target_slides]
        refs = db.query(ReferenceImage).filter(
            ReferenceImage.slide_id.in_(slide_ids),
            ReferenceImage.role != "finetune_ref",
        ).all() if slide_ids else []
        refs_by_slide = {}
        for ref in refs:
            refs_by_slide.setdefault(ref.slide_id, []).append(ref)
        for s in target_slides:
            s.reference_images = refs_by_slide.get(s.id, [])

        ref_contexts, ref_user_hints = _build_slide_reference_contexts(target_slides)
        logger.info(f"VisualPrompts BG: project={project_id}, ref_contexts pages={list(ref_contexts.keys())}, ref_user_hints pages={list(ref_user_hints.keys())}")
        content_plan = []
        for s in target_slides:
            item = copy.deepcopy(s.content_json) or {}
            item["page_num"] = s.page_num
            if ref_contexts.get(s.page_num):
                item["reference_context"] = "\n".join(ref_contexts[s.page_num])
            if ref_user_hints.get(s.page_num):
                item["reference_user_hint"] = ref_user_hints[s.page_num]
            content_plan.append(item)

        ref_images_project = project.reference_images
        ref_ids = [img.id for img in ref_images_project] if ref_images_project else None
        global_visual_assets = _project_visual_assets_for_planning(project)

        style_text_override = _derive_project_style_pack(project, content_plan)
        style_override = _style_override_from_text(style_text_override)
        # 注意：页面级参考图已通过 content_plan 的 reference_context 传递；visual_asset 按页选择。

        # Step 1: 生成 Visual Plan
        _update_progress(project_id, {
            "stage": "visual_planning",
            "message": "正在分析内容结构，为每一页设计视觉方案...",
            "current_page": 0,
            "total_pages": len(target_slides),
        }, run_id)
        visual_plan = await asyncio.to_thread(
            generate_visual_plan,
            content_plan=content_plan,
            style_id=project.style_id or "default",
            reference_image_ids=ref_ids,
            style_override=style_override,
            global_visual_assets=global_visual_assets,
            progress_callback=lambda progress: _update_progress(project_id, {
                "stage": progress.get("stage", "visual_planning") if isinstance(progress, dict) else "visual_planning",
                "message": progress.get("message", "正在生成视觉方案") if isinstance(progress, dict) else str(progress),
                "current_page": progress.get("current_page", 0) if isinstance(progress, dict) else 0,
                "total_pages": progress.get("total_pages", len(target_slides)) if isinstance(progress, dict) else len(target_slides),
            }, run_id),
        )

        # 更新数据库：visual plan（先提交，避免后续 prompt 失败导致 visual plan 也被回滚）
        visual_by_page = {v["page_num"]: v for v in visual_plan}
        for slide in target_slides:
            slide.visual_json = visual_by_page.get(slide.page_num, {})
            if slide.status in ("pending", "planning"):
                slide.status = "visual_ready"
        db.commit()

        # Step 2: 并发生成 Prompts（最多 5 个并发，避免 API 限流）
        visual_plan_for_prompts = [
            _with_project_logo_policy(s.visual_json, project)
            for s in target_slides
            if s.visual_json
        ]
        content_plan_for_prompts = content_plan
        content_by_page = {item["page_num"]: item.get("text_content", {}) for item in content_plan_for_prompts}

        total_prompt_pages = len(visual_plan_for_prompts)
        completed_count = 0
        semaphore = asyncio.Semaphore(5)
        progress_lock = asyncio.Lock()

        async def _gen_one(intent: dict) -> dict:
            nonlocal completed_count
            page_num = intent["page_num"]
            try:
                async with semaphore:
                    content_text = content_by_page.get(page_num, {})
                    prompt = await asyncio.to_thread(
                        generate_prompt_for_page,
                        page_intent=intent,
                        content_text=content_text,
                        style_id=project.style_id or "default",
                        reference_images=_project_refs_for_prompt(project, intent.get("visual_asset_ids") or [], intent) or None,
                        style_text_override=style_text_override,
                    )
                async with progress_lock:
                    completed_count += 1
                    _update_progress(project_id, {
                        "stage": "prompt_writing",
                        "message": "正在撰写生图 Prompt",
                        "current_page": completed_count,
                        "total_pages": total_prompt_pages,
                    }, run_id)
                return {"page_num": page_num, "prompt": prompt}
            except Exception as e:
                logger.error(f"PromptEngine: 第 {page_num} 页 Prompt 生成失败: {e}")
                async with progress_lock:
                    completed_count += 1
                    _update_progress(project_id, {
                        "stage": "prompt_writing",
                        "message": "部分页面 Prompt 生成失败，继续处理剩余页面",
                        "current_page": completed_count,
                        "total_pages": total_prompt_pages,
                    }, run_id)
                return {"page_num": page_num, "prompt": "", "error": str(e)}

        tasks = [_gen_one(intent) for intent in visual_plan_for_prompts]
        prompts = await asyncio.gather(*tasks, return_exceptions=False)
        _update_progress(project_id, {
            "stage": "saving",
            "message": "正在保存结果...",
            "current_page": total_prompt_pages,
            "total_pages": total_prompt_pages,
        }, run_id)

        # 更新数据库：prompts
        prompt_by_page = {p["page_num"]: p["prompt"] for p in prompts}
        for slide in target_slides:
            slide.prompt_text = prompt_by_page.get(slide.page_num)
            if slide.prompt_text:
                slide.status = "prompt_ready"

        # 状态更新：优先按目标页判断（支持部分生成），再回退到全局判断
        target_all_visual = all(s.visual_json for s in target_slides)
        target_all_prompt = all(s.prompt_text for s in target_slides)
        if target_all_prompt:
            project.status = "prompt_ready"
        elif target_all_visual:
            project.status = "visual_ready"
        elif all(s.prompt_text for s in slides):
            project.status = "prompt_ready"
        elif all(s.visual_json for s in slides):
            project.status = "visual_ready"

        finish_run(
            db,
            run_id,
            status="succeeded",
            message=f"画面描述和生图 Prompt 已生成，共 {total_prompt_pages} 页",
            completed_count=total_prompt_pages,
        )
        db.commit()
        logger.info(f"Visual plan and prompts generated for project {project_id}")
    except asyncio.CancelledError:
        logger.info(f"VisualPrompts BG: project={project_id} task was cancelled")
        # 任务被取消时回滚未提交变更，并恢复状态
        try:
            db.rollback()
            project = db.query(Project).filter(Project.id == project_id).first()
            if project:
                if all(s.visual_json for s in project.slides):
                    project.status = "visual_ready"
                elif all(s.content_json for s in project.slides):
                    project.status = "planning"
                else:
                    project.status = "draft"
                finish_run(db, run_id, status="cancelled", message="任务被取消", error_msg="任务被取消")
                db.commit()
        except Exception as status_err:
            logger.warning(f"Failed to reset project status after cancellation: {status_err}")
        _update_progress(project_id, {
            "stage": "error",
            "message": "任务被取消",
            "current_page": 0,
            "total_pages": len(target_slides) if page_nums else 0,
        }, run_id)
        raise  # 重新抛出，让 asyncio 框架识别取消
    except Exception as e:
        db.rollback()
        logger.exception(f"Failed to generate visual plan for project {project_id}: {e}")
        # 重置项目状态，避免永远卡在 generating
        try:
            project = db.query(Project).filter(Project.id == project_id).first()
            if project:
                if all(s.visual_json for s in project.slides):
                    project.status = "visual_ready"
                elif all(s.content_json for s in project.slides):
                    project.status = "planning"
                else:
                    project.status = "draft"
                finish_run(db, run_id, status="failed", message="画面方案生成失败", error_msg=str(e)[:500])
                db.commit()
        except Exception as status_err:
            logger.warning(f"Failed to reset project status after error: {status_err}")
        _update_progress(project_id, {
            "stage": "error",
            "message": f"生成失败：{str(e)[:100]}",
            "current_page": 0,
            "total_pages": len(target_slides) if page_nums else 0,
        }, run_id)
    finally:
        db.close()
        _running_tasks.pop(project_id, None)
        generation_progress.pop(project_id, None)


@router.get("/{project_id}/generation-status")
async def get_generation_status(project_id: str, db: Session = Depends(get_db)):
    """查询正在运行的生成任务状态（同时检查 asyncio 后台任务和 Celery 任务）。"""
    task = _running_tasks.get(project_id)
    project = db.query(Project).filter(Project.id == project_id).first()
    active_run = get_active_run(db, project_id)
    if project and not active_run:
        slides = db.query(Slide).filter(Slide.project_id == project_id).order_by(Slide.page_num).all()
        before_status = project.status
        normalize_confirmed_project_stage(project, slides, active_run)
        if project.status != before_status:
            db.commit()

    # 检查 asyncio 后台任务
    if active_run and task and not task.done():
        return {"generation_status": "running", "project_status": project.status if project else None, "active_run": serialize_run(active_run)}

    # 检查 Celery 任务（通过 Redis 中的 task_id）
    try:
        task_id = active_run.task_id if active_run and active_run.task_id else redis_client.get(f"project:{project_id}:task_id")
        if task_id:
            from app.celery_app import celery_app
            celery_task = AsyncResult(task_id.decode() if isinstance(task_id, bytes) else task_id, app=celery_app)
            if celery_task.state in ("PENDING", "STARTED", "RETRY"):
                if celery_task.state == "PENDING":
                    started_raw = redis_client.get(f"project:{project_id}:task_started_at")
                    try:
                        started_at = float(started_raw.decode() if isinstance(started_raw, bytes) else started_raw)
                    except (TypeError, ValueError):
                        started_at = 0
                    timeout = int(settings.GENERATION_PENDING_TIMEOUT_SECONDS or 0)
                    if timeout > 0 and started_at and time.time() - started_at > timeout:
                        _mark_generation_idle(project, db, "生成任务长时间未被 worker 接收，请检查 Celery worker")
                        stale_active_run(db, project_id, "生成任务长时间未被 worker 接收，请检查 Celery worker")
                        db.commit()
                        redis_client.delete(f"project:{project_id}:task_id")
                        redis_client.delete(f"project:{project_id}:task_started_at")
                        return {"generation_status": "idle", "project_status": project.status if project else None, "active_run": None}
                return {"generation_status": "running", "project_status": project.status if project else None, "active_run": serialize_run(active_run)}
    except Exception as e:
        logger.warning(f"Failed to check Celery status for {project_id}: {e}")

    if active_run:
        return {"generation_status": "running", "project_status": project.status if project else None, "active_run": serialize_run(active_run)}

    return {"generation_status": "idle", "project_status": project.status if project else None, "active_run": None}


# ==================== P3: Generation & Download ====================

@router.post("/{project_id}/generate")
def start_generation(
    project_id: str,
    body: PageNumsRequest = PageNumsRequest(),
    db: Session = Depends(get_db),
):
    """启动生成流水线（Celery 异步执行）。支持打样：只生成选中的页。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if get_active_run(db, project_id):
        raise HTTPException(status_code=409, detail="当前项目已有任务正在运行，请等待完成后再开始下一步")

    if project.status == "completed" and not body.page_nums:
        raise HTTPException(status_code=400, detail="已全部完成，如需重新生成请指定页码")

    # 检查项目是否有 slides
    slides = (
        db.query(Slide)
        .filter(Slide.project_id == project_id)
        .order_by(Slide.page_num)
        .all()
    )
    if not slides:
        raise HTTPException(status_code=400, detail="No slides found. Generate content plan first.")

    page_nums = _resolve_generation_page_nums(slides, body.page_nums, bool(body.prototype))

    # 记录本次生成的目标页码，供前端进度显示用
    target_slides = [s for s in slides if s.page_num in page_nums] if page_nums else slides
    run_kind = "prototype_generation" if body.prototype else ("page_generation" if page_nums else "batch_generation")
    run_stage = "prototype_generation" if body.prototype else "batch_generation"
    run = create_project_run(
        db,
        project_id,
        kind=run_kind,
        stage=run_stage,
        target_page_nums=[s.page_num for s in target_slides],
        total_count=len(target_slides),
        message="图片生成任务已排队",
    )
    db.commit()
    _update_progress(project_id, {
        "target_page_nums": page_nums,
        "target_count": len(target_slides),
    }, run.id)

    # 使用 Celery 异步任务（Celery worker 内部有 Redis 锁兜底防重）
    task = generate_slides_task.delay(project_id, page_nums, prototype=body.prototype, run_id=run.id)
    set_run_task(db, run.id, task.id)
    db.commit()
    # 保存 task_id 到 Redis，供 stop-generation 撤销用
    redis_client.set(f"project:{project_id}:task_id", task.id, ex=3600)
    redis_client.set(f"project:{project_id}:task_started_at", str(time.time()), ex=3600)

    return {
        "message": "Generation started",
        "project_id": project_id,
        "prototype": body.prototype or bool(page_nums),
        "page_nums": page_nums,
        "task_id": task.id,
        "run": serialize_run(run),
    }


@router.post("/{project_id}/confirm-prototype")
def confirm_prototype(
    project_id: str,
    db: Session = Depends(get_db),
):
    """确认打样结果，启动全量生成。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if get_active_run(db, project_id):
        raise HTTPException(status_code=409, detail="当前项目已有任务正在运行，请等待完成后再开始下一步")

    if project.status != "prototype_ready":
        raise HTTPException(
            status_code=400,
            detail=f"当前状态为 {project.status}，不支持确认打样。请先完成打样生成。"
        )

    # 找出所有未完成或失败的页，打样成功的页面不重复生成。
    slides = (
        db.query(Slide)
        .filter(Slide.project_id == project_id)
        .order_by(Slide.page_num)
        .all()
    )

    pending_slides = [s for s in slides if s.status not in ("completed", "failed")]
    failed_slides = [s for s in slides if s.status == "failed"]

    # 如果有失败的，先重试；如果有未生成的，一起生成
    target_page_nums = [s.page_num for s in pending_slides + failed_slides]

    if not target_page_nums:
        # 所有页都已完成，直接标记 completed
        project.status = "completed"
        db.commit()
        return {"message": "All slides already completed", "status": "completed"}

    run = create_project_run(
        db,
        project_id,
        kind="batch_generation",
        stage="batch_generation",
        target_page_nums=target_page_nums,
        total_count=len(target_page_nums),
        message="批量生成任务已排队",
    )
    db.commit()

    task = generate_slides_task.delay(project_id, target_page_nums, run_id=run.id)
    set_run_task(db, run.id, task.id)
    db.commit()
    redis_client.set(f"project:{project_id}:task_id", task.id, ex=3600)
    redis_client.set(f"project:{project_id}:task_started_at", str(time.time()), ex=3600)

    return {
        "message": "Full generation started",
        "project_id": project_id,
        "page_nums": target_page_nums,
        "task_id": task.id,
        "run": serialize_run(run),
    }


@router.post("/{project_id}/stop-generation")
def stop_generation(
    project_id: str,
    db: Session = Depends(get_db),
):
    """停止当前生成任务，重置项目和 slide 状态。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    active_run = get_active_run(db, project_id)
    if not active_run:
        return {"message": "No generation in progress", "status": project.status}

    # 重置所有 generating 状态的 slide
    slides = (
        db.query(Slide)
        .filter(Slide.project_id == project_id)
        .all()
    )
    for slide in slides:
        if slide.status == "generating":
            slide.status = "prompt_ready"
            slide.error_msg = "用户手动停止"
    cancel_active_run(db, project_id, "用户手动停止")
    reconcile_project_state(project, slides, active_run)

    db.commit()

    # 撤销 Celery 任务，真正阻止 worker 继续生图
    task_id = redis_client.get(f"project:{project_id}:task_id")
    if task_id:
        try:
            AsyncResult(task_id.decode() if isinstance(task_id, bytes) else task_id).revoke(terminate=True)
            logger.info(f"Revoked Celery task {task_id} for project {project_id}")
        except Exception as e:
            logger.warning(f"Failed to revoke task {task_id}: {e}")
        redis_client.delete(f"project:{project_id}:task_id")
        redis_client.delete(f"project:{project_id}:task_started_at")

    # 清除进度缓存
    if project_id in generation_progress:
        del generation_progress[project_id]

    return {"message": "Generation stopped", "status": project.status}


@router.get("/{project_id}/status")
def get_project_status(project_id: str, db: Session = Depends(get_db)):
    """获取项目生成状态和每页进度。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    slides = (
        db.query(Slide)
        .filter(Slide.project_id == project_id)
        .order_by(Slide.page_num)
        .all()
    )

    slide_status = []
    completed_count = 0
    for s in slides:
        # 只有真正生成完成的页面才算 completed
        if s.status == "completed":
            completed_count += 1
        slide_status.append({
            "page_num": s.page_num,
            "status": s.status,
            "error_msg": s.error_msg,
        })

    active_run = get_active_run(db, project_id)
    before_status = project.status
    normalize_confirmed_project_stage(project, slides, active_run)
    if project.status != before_status:
        db.commit()

    pptx_filename = "prototype.pptx" if project.status == "prototype_ready" else "presentation.pptx"
    pptx_path = os.path.join(
        settings.OUTPUT_DIR or "./outputs",
        project_id,
        pptx_filename,
    )
    has_pptx = os.path.exists(pptx_path)

    target_count, target_completed, target_failed = target_counts(active_run, slides)
    progress = generation_progress.get(project_id, {})
    return {
        "project_id": project_id,
        "project_status": project.status,
        "total_slides": len(slides),
        "completed_slides": target_completed,  # backward compatible: now target scoped
        "total_completed_slides": completed_count,
        "target_completed_slides": target_completed,
        "target_failed_slides": target_failed,
        "target_count": target_count or progress.get("target_count") or len(slides),
        "target_page_nums": (active_run.target_page_nums if active_run else progress.get("target_page_nums")),
        "active_run": serialize_run(active_run, slides),
        "has_pptx": has_pptx,
        "pptx_path": pptx_path if has_pptx else None,
        "slides": slide_status,
    }


@router.get("/{project_id}/workflow-status")
def get_project_workflow_status(project_id: str, db: Session = Depends(get_db)):
    """获取统一的项目阶段、任务进度和页面状态。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    slides = (
        db.query(Slide)
        .filter(Slide.project_id == project_id)
        .order_by(Slide.page_num)
        .all()
    )

    active_run = get_active_run(db, project_id)
    latest_run = get_latest_run(db, project_id)
    before_status = project.status
    normalize_confirmed_project_stage(project, slides, active_run)
    if project.status != before_status:
        db.commit()

    pptx_filename = "prototype.pptx" if project.status == "prototype_ready" else "presentation.pptx"
    pptx_path = os.path.join(
        settings.OUTPUT_DIR or "./outputs",
        project_id,
        pptx_filename,
    )
    has_pptx = os.path.exists(pptx_path)

    return serialize_workflow_status(
        project,
        slides,
        active_run=active_run,
        latest_run=latest_run,
        has_pptx=has_pptx,
        pptx_path=pptx_path,
    )


@router.get("/{project_id}/generation-progress")
def get_generation_progress(project_id: str, db: Session = Depends(get_db)):
    """获取 Content Plan 后台生成的实时进度。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    active_run = get_active_run(db, project_id)
    if not active_run:
        generation_progress.pop(project_id, None)
        slides = db.query(Slide).filter(Slide.project_id == project_id).order_by(Slide.page_num).all()
        target_count = len(slides)
        completed_count = sum(1 for s in slides if s.status == "completed")
        return {
            "project_id": project_id,
            "project_status": project.status,
            "active_run": None,
            "stage": None,
            "message": None,
            "current_page": completed_count,
            "total_pages": target_count,
            "think": None,
        }

    progress = generation_progress.get(project_id, {})
    slides = db.query(Slide).filter(Slide.project_id == project_id).order_by(Slide.page_num).all()
    run_data = serialize_run(active_run, slides)
    return {
        "project_id": project_id,
        "project_status": project.status,
        "active_run": run_data,
        "stage": progress.get("stage") or active_run.stage,
        "message": progress.get("message") or active_run.message,
        "current_page": run_data["completed_count"],
        "total_pages": run_data["total_count"],
        "think": progress.get("think"),
    }


@router.get("/{project_id}/download")
def download_pptx(project_id: str, prototype: bool = False, db: Session = Depends(get_db)):
    """下载生成的 PPTX 文件。支持 prototype 模式下载打样文件。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    filename = "prototype.pptx" if prototype else "presentation.pptx"
    pptx_path = os.path.join(
        settings.OUTPUT_DIR or "./outputs",
        project_id,
        filename,
    )
    if not os.path.exists(pptx_path):
        raise HTTPException(status_code=404, detail="PPTX not found. Generate first.")

    display_name = f"{project.title}_prototype.pptx" if prototype else f"{project.title}.pptx"
    return FileResponse(
        path=pptx_path,
        filename=display_name,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )


@router.post("/{project_id}/upload")
def upload_file(
    project_id: str,
    file: UploadFile = File(...),
    role: str = Form("style_ref"),
    slide_id: Optional[str] = Form(None),
    process_mode: Optional[str] = Form(None),
    asset_name: Optional[str] = Form(None),
    asset_kind: Optional[str] = Form(None),
    usage_note: Optional[str] = Form(None),
    logo_anchor: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """上传参考图或 Logo 到项目目录。支持按页上传（传 slide_id）。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if role not in ALLOWED_UPLOAD_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role '{role}'. Allowed: {', '.join(sorted(ALLOWED_UPLOAD_ROLES))}",
        )

    explicit_process_mode = process_mode is not None
    if process_mode is not None and process_mode not in {"blend", "crop", "original"}:
        raise HTTPException(status_code=400, detail="Invalid process_mode. Allowed: blend, crop, original")
    normalized_logo_anchor = normalize_logo_anchor(logo_anchor)

    normalized_asset_kind = None
    if role == "visual_asset":
        if slide_id:
            raise HTTPException(status_code=400, detail="visual_asset must be uploaded as a project-level asset")
        normalized_asset_kind = normalize_visual_asset_kind(asset_kind)
        if process_mode is None:
            process_mode = default_visual_asset_process_mode(normalized_asset_kind)
    elif role == "logo" and process_mode is None:
        process_mode = "original"
    elif process_mode is None:
        process_mode = "blend"

    # 如果传了 slide_id，校验该 slide 存在
    if slide_id:
        slide = db.query(Slide).filter(Slide.id == slide_id, Slide.project_id == project_id).first()
        if not slide:
            raise HTTPException(status_code=404, detail="Slide not found")

    # 数量限制：单页长期参考图最多 10 张；全局视觉资产单独限额。
    if role == "visual_asset" and not slide_id:
        existing_count = db.query(ReferenceImage).filter(
            ReferenceImage.project_id == project_id,
            ReferenceImage.slide_id.is_(None),
            ReferenceImage.role == "visual_asset",
        ).count()
        if existing_count >= MAX_VISUAL_ASSETS_PER_PROJECT:
            raise HTTPException(
                status_code=400,
                detail=f"该项目已有 {existing_count} 张视觉资产，上限 {MAX_VISUAL_ASSETS_PER_PROJECT} 张",
            )
    elif role != "finetune_ref":
        existing_count = db.query(ReferenceImage).filter(
            ReferenceImage.project_id == project_id,
            ReferenceImage.slide_id == slide_id,
            ReferenceImage.role != "finetune_ref",
        ).count()
        if existing_count >= MAX_REFERENCE_IMAGES_PER_PAGE:
            raise HTTPException(
                status_code=400,
                detail=f"该页面已有 {existing_count} 张参考图，上限 {MAX_REFERENCE_IMAGES_PER_PAGE} 张",
            )

    # 读取并校验文件
    file_bytes = file.file.read()
    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail="上传的文件为空")
    if len(file_bytes) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"文件大小超过限制（最大 {MAX_UPLOAD_SIZE // 1024 // 1024}MB）",
        )
    if file.content_type and file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型 '{file.content_type}'。允许: JPEG, PNG, GIF, WebP, SVG",
        )

    project_upload_dir = os.path.join(settings.UPLOAD_DIR, project_id)
    if not os.path.isabs(project_upload_dir):
        project_upload_dir = os.path.abspath(project_upload_dir)
    os.makedirs(project_upload_dir, exist_ok=True)

    # 安全检查：拒绝路径遍历攻击
    safe_name = file.filename.replace("\\", "/").split("/")[-1]
    if ".." in safe_name or not safe_name or safe_name.startswith("."):
        raise HTTPException(status_code=400, detail="非法文件名")

    # 尝试用 PIL 打开并统一转 PNG，兼容 JPEG/PNG/GIF/WebP/HEIC/BMP/TIFF 等
    try:
        img = PILImage.open(io.BytesIO(file_bytes))
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGBA")
        else:
            img = img.convert("RGB")
        safe_name = os.path.splitext(safe_name)[0] + ".png"
        prefix = f"slide_{slide_id}_" if slide_id else ""
        if role == "finetune_ref":
            safe_name = f"{int(time.time() * 1000)}_{safe_name}"
        filename = f"{prefix}{role}_{safe_name}"
        file_path = os.path.join(project_upload_dir, filename)
        img.save(file_path, "PNG")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"图片格式无法处理: {e}")

    old_refs_to_remove: list[ReferenceImage] = []
    if not slide_id and role == "logo":
        old_refs_to_remove = db.query(ReferenceImage).filter(
            ReferenceImage.project_id == project_id,
            ReferenceImage.slide_id.is_(None),
            ReferenceImage.role == "logo",
        ).all()

    asset_analysis = None
    cleaned_asset_name = (asset_name or "").strip() or None
    cleaned_usage_note = (usage_note or "").strip() or None
    if role == "visual_asset":
        try:
            asset_analysis = analyze_visual_asset(
                file_path,
                asset_name=cleaned_asset_name or os.path.splitext(os.path.basename(safe_name))[0],
                asset_kind=normalized_asset_kind or "other",
                usage_note=cleaned_usage_note or "",
            )
        except Exception as e:
            logger.warning(f"Visual asset analysis failed for {file_path}: {e}")
            asset_analysis = None
        if not asset_kind and isinstance(asset_analysis, dict):
            normalized_asset_kind = normalize_visual_asset_kind(asset_analysis.get("detected_kind"))
            if not explicit_process_mode:
                process_mode = default_visual_asset_process_mode(normalized_asset_kind)
        if not cleaned_asset_name:
            analyzed_subject = asset_analysis.get("subject") if isinstance(asset_analysis, dict) else None
            cleaned_asset_name = analyzed_subject or os.path.splitext(os.path.basename(safe_name))[0]

    ref_image = ReferenceImage(
        project_id=project_id,
        slide_id=slide_id,
        file_path=file_path,
        role=role,
        process_mode=process_mode,
        asset_name=cleaned_asset_name if role == "visual_asset" else None,
        asset_kind=normalized_asset_kind if role == "visual_asset" else None,
        usage_note=cleaned_usage_note if role == "visual_asset" else None,
        asset_analysis=asset_analysis if role == "visual_asset" else None,
        logo_anchor=normalized_logo_anchor if role == "logo" else None,
    )
    db.add(ref_image)
    for old_ref in old_refs_to_remove:
        db.delete(old_ref)
    if not slide_id and role in {"style_ref", "logo", "template"}:
        _invalidate_style_dependent_outputs(project)
    if role == "visual_asset":
        _invalidate_visual_asset_dependent_outputs(project)
    if slide_id and role != "finetune_ref" and slide:
        _invalidate_visual_plan_dependent_outputs(project, [slide])
    db.commit()
    db.refresh(ref_image)
    for old_ref in old_refs_to_remove:
        try:
            if old_ref.file_path != file_path and os.path.exists(old_ref.file_path):
                os.remove(old_ref.file_path)
                logger.info(f"Deleted old logo file: {old_ref.file_path}")
        except Exception as e:
            logger.warning(f"Failed to delete old logo file {old_ref.file_path}: {e}")

    return {
        "id": ref_image.id,
        "file_path": file_path,
        "role": role,
        "slide_id": slide_id,
        "process_mode": process_mode,
        "asset_name": ref_image.asset_name,
        "asset_kind": ref_image.asset_kind,
        "usage_note": ref_image.usage_note,
        "asset_analysis": ref_image.asset_analysis,
        "logo_anchor": ref_image.logo_anchor,
        "url": f"/uploads/{project_id}/{filename}",
        "overlay_url": _logo_overlay_url(ref_image, project_id) if role == "logo" else None,
    }


@router.get("/{project_id}/reference-images")
def list_reference_images(
    project_id: str,
    slide_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """列出项目已上传的参考图。支持按 slide_id 过滤页面级参考图。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    query = db.query(ReferenceImage).filter(ReferenceImage.project_id == project_id)
    if slide_id:
        query = query.filter(ReferenceImage.slide_id == slide_id)
    else:
        # 不传 slide_id 时，只返回项目级（slide_id 为 null）的参考图
        query = query.filter(ReferenceImage.slide_id.is_(None))

    query = query.filter(ReferenceImage.role != "finetune_ref")
    images = query.all()
    return [
        {
            "id": img.id,
            "role": img.role,
            "slide_id": img.slide_id,
            "process_mode": img.process_mode or "blend",
            "asset_name": img.asset_name,
            "asset_kind": img.asset_kind,
            "usage_note": img.usage_note,
            "asset_analysis": img.asset_analysis,
            "logo_anchor": img.logo_anchor or (DEFAULT_LOGO_ANCHOR if img.role == "logo" else None),
            "file_exists": os.path.exists(img.file_path),
            "url": f"/uploads/{project_id}/{os.path.basename(img.file_path)}",
            "overlay_url": _logo_overlay_url(img, project_id) if img.role == "logo" else None,
        }
        for img in images
    ]


@router.post("/{project_id}/suggest-reference-images")
def suggest_reference_images(
    project_id: str,
    db: Session = Depends(get_db),
):
    """内容总监：根据内容大纲推荐参考图片。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    slides = (
        db.query(Slide)
        .filter(Slide.project_id == project_id)
        .order_by(Slide.page_num)
        .all()
    )
    if not slides:
        return {"suggestions": []}

    outline = []
    for s in slides:
        content = s.content_json or {}
        tc = content.get("text_content", {})
        outline.append({
            "page_num": s.page_num,
            "type": s.type or "content",
            "headline": tc.get("headline", ""),
            "subhead": tc.get("subhead", ""),
            "body": tc.get("body", "")[:300],
        })

    system_prompt = (
        "你是资深 PPT 内容总监。根据用户提供的 PPT 大纲，为每一页分析是否需要参考图片，"
        "并给出具体建议。必须且只能输出合法 JSON 数组，严禁添加任何额外说明文本。"
    )
    user_prompt = f"""请根据以下 PPT 大纲，推荐需要参考图片的页面。

要求：
1. 只建议"有明确视觉主体"的页面（产品、人物、场景、数据图表、品牌展示等）
2. 纯文字过渡页、目录页不要推荐
3. 输出 JSON 数组，每个元素包含：page_num(int), type(str), reason(str), recommended_mode(str)
4. recommended_mode 建议：
   - 人像/产品/场景融入 → "blend"（融合提取）
   - Logo/多图并排/图标 → "crop"（统一裁切）
   - 证书/严格比例/不允许改动 → "original"（原图）

大纲：
{json.dumps(outline, ensure_ascii=False, indent=2)}

只输出 JSON 数组，不要任何其他文字。"""

    try:
        client = get_llm_client()
        resp = client.chat.completions.create(
            model=settings.MINIMAX_LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.5,
        )
        content = resp.choices[0].message.content or ""
        # 清理可能的 markdown 代码块
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else ""
            if content.endswith("```"):
                content = content.rsplit("\n", 1)[0]
            content = content.strip()
        suggestions = json.loads(content)
        if not isinstance(suggestions, list):
            suggestions = []
    except Exception as e:
        logger.warning(f"LLM suggest_reference_images failed: {e}")
        suggestions = []

    return {"suggestions": suggestions}


@router.delete("/{project_id}/reference-images/{ref_id}")
def delete_reference_image(
    project_id: str,
    ref_id: str,
    db: Session = Depends(get_db),
):
    """删除指定参考图/Logo/模板，同时清理文件。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    ref = db.query(ReferenceImage).filter(
        ReferenceImage.id == ref_id,
        ReferenceImage.project_id == project_id,
    ).first()
    if not ref:
        raise HTTPException(status_code=404, detail="Reference image not found")

    # 如果删除的是模板，需要删除该项目的所有模板页记录和文件
    if ref.role == "template":
        all_template_refs = db.query(ReferenceImage).filter(
            ReferenceImage.project_id == project_id,
            ReferenceImage.role == "template",
        ).all()
        for t_ref in all_template_refs:
            db.delete(t_ref)
        if project.selected_template_recommendations:
            project.selected_template_recommendations = None
            logger.info(f"Cleared template recommendations for project {project_id}")
        _invalidate_style_dependent_outputs(project)
        db.commit()
        # 数据库提交成功后再删物理文件，文件删失败不影响事务
        for t_ref in all_template_refs:
            try:
                if os.path.exists(t_ref.file_path):
                    os.remove(t_ref.file_path)
                    logger.info(f"Deleted template file: {t_ref.file_path}")
            except Exception as e:
                logger.warning(f"Failed to delete template file {t_ref.file_path}: {e}")
        return {"message": "Deleted all template pages", "count": len(all_template_refs)}

    # 普通参考图/Logo 删除
    if ref.role in {"style_ref", "logo"} and not ref.slide_id:
        _invalidate_style_dependent_outputs(project)
    if ref.slide_id and ref.role != "finetune_ref":
        slide = db.query(Slide).filter(Slide.id == ref.slide_id, Slide.project_id == project_id).first()
        if slide:
            _invalidate_visual_plan_dependent_outputs(project, [slide])
    if ref.role == "visual_asset":
        slides = db.query(Slide).filter(Slide.project_id == project_id).all()
        for slide in slides:
            visual = copy.deepcopy(slide.visual_json) or {}
            ids = visual.get("visual_asset_ids")
            if isinstance(ids, list) and ref_id in ids:
                visual["visual_asset_ids"] = [x for x in ids if x != ref_id]
                usage = visual.get("visual_asset_usage")
                if isinstance(usage, dict):
                    usage.pop(ref_id, None)
                    visual["visual_asset_usage"] = usage
                slide.visual_json = visual
        _invalidate_visual_asset_dependent_outputs(project)
    db.delete(ref)
    db.commit()
    try:
        if os.path.exists(ref.file_path):
            os.remove(ref.file_path)
            logger.info(f"Deleted file: {ref.file_path}")
    except Exception as e:
        logger.warning(f"Failed to delete file {ref.file_path}: {e}")
    return {"message": "Deleted", "id": ref_id}


@router.patch("/{project_id}/reference-images/{ref_id}")
def update_reference_image(
    project_id: str,
    ref_id: str,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
):
    """更新参考图的处理模式（blend/crop/original）和视觉资产元信息。"""
    process_mode = payload.get("process_mode")
    if process_mode is not None and process_mode not in {"blend", "crop", "original"}:
        raise HTTPException(status_code=400, detail="Invalid process_mode. Allowed: blend, crop, original")
    logo_anchor = payload.get("logo_anchor")
    if logo_anchor is not None and str(logo_anchor).strip().lower().replace("_", "-") not in LOGO_ANCHORS:
        raise HTTPException(status_code=400, detail="Invalid logo_anchor. Allowed: top-left, top-right, bottom-left, bottom-right")

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    ref = db.query(ReferenceImage).filter(
        ReferenceImage.id == ref_id,
        ReferenceImage.project_id == project_id,
    ).first()
    if not ref:
        raise HTTPException(status_code=404, detail="Reference image not found")

    changed_visual_asset = False
    changed_logo = False
    if process_mode is not None:
        ref.process_mode = process_mode
        changed_visual_asset = ref.role == "visual_asset"
        changed_logo = ref.role == "logo"

    if ref.role == "logo" and logo_anchor is not None:
        ref.logo_anchor = normalize_logo_anchor(logo_anchor)
        changed_logo = True

    if ref.role == "visual_asset":
        if "asset_name" in payload:
            ref.asset_name = (payload.get("asset_name") or "").strip() or None
            changed_visual_asset = True
        if "asset_kind" in payload:
            ref.asset_kind = normalize_visual_asset_kind(payload.get("asset_kind"))
            changed_visual_asset = True
        if "usage_note" in payload:
            ref.usage_note = (payload.get("usage_note") or "").strip() or None
            changed_visual_asset = True
        if payload.get("reanalyze"):
            try:
                ref.asset_analysis = analyze_visual_asset(
                    ref.file_path,
                    asset_name=ref.asset_name or "",
                    asset_kind=ref.asset_kind or "other",
                    usage_note=ref.usage_note or "",
                )
            except Exception as e:
                logger.warning(f"Visual asset reanalysis failed for {ref.file_path}: {e}")
            changed_visual_asset = True

    if not process_mode and not changed_visual_asset and not changed_logo:
        raise HTTPException(status_code=400, detail="No supported fields to update")

    if changed_visual_asset and ref.role == "visual_asset":
        _invalidate_visual_asset_dependent_outputs(project)
    elif ref.slide_id and ref.role != "finetune_ref":
        slide = db.query(Slide).filter(Slide.id == ref.slide_id, Slide.project_id == project_id).first()
        if slide:
            _invalidate_visual_plan_dependent_outputs(project, [slide])
    db.commit()
    db.refresh(ref)
    return {
        "id": ref.id,
        "process_mode": ref.process_mode,
        "asset_name": ref.asset_name,
        "asset_kind": ref.asset_kind,
        "usage_note": ref.usage_note,
        "asset_analysis": ref.asset_analysis,
        "logo_anchor": ref.logo_anchor or (DEFAULT_LOGO_ANCHOR if ref.role == "logo" else None),
        "url": f"/uploads/{project_id}/{os.path.basename(ref.file_path)}",
        "overlay_url": _logo_overlay_url(ref, project_id) if ref.role == "logo" else None,
    }


@router.post("/{project_id}/retry-failed")
def retry_failed_slides(
    project_id: str,
    db: Session = Depends(get_db),
):
    """批量重试 project 下所有失败的 slides。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if get_active_run(db, project_id):
        raise HTTPException(status_code=409, detail="当前项目已有任务正在运行，请等待完成后再开始下一步")

    failed_slides = (
        db.query(Slide)
        .filter(Slide.project_id == project_id, Slide.status == "failed")
        .order_by(Slide.page_num)
        .all()
    )

    if not failed_slides:
        raise HTTPException(status_code=400, detail="没有失败的页面需要重试")

    # 过滤掉正在生成中的 slide，避免重复触发
    page_nums = []
    for slide in failed_slides:
        if slide.status == "generating":
            continue
        slide.status = "generating"
        slide.error_msg = None
        page_nums.append(slide.page_num)

    if not page_nums:
        raise HTTPException(status_code=400, detail="所有失败页面正在重试中，请稍候")

    run = create_project_run(
        db,
        project_id,
        kind="retry_failed",
        stage="batch_generation",
        target_page_nums=page_nums,
        total_count=len(page_nums),
        message="失败页面重试任务已排队",
    )
    db.commit()

    # 记录本次重试的目标页码和数量，供前端进度显示用
    _update_progress(project_id, {
        "target_page_nums": page_nums,
        "target_count": len(page_nums),
    }, run.id)

    task = generate_slides_task.delay(project_id, page_nums, run_id=run.id)
    set_run_task(db, run.id, task.id)
    db.commit()
    redis_client.set(f"project:{project_id}:task_id", task.id, ex=3600)
    redis_client.set(f"project:{project_id}:task_started_at", str(time.time()), ex=3600)

    return {
        "message": "Retry started",
        "page_nums": page_nums,
        "count": len(page_nums),
        "run": serialize_run(run),
    }


class RetryRequest(BaseModel):
    regenerate_prompt: bool = False


def _regenerate_slide_prompt(slide: Slide, project: Project, db: Session) -> str:
    """为单页重新生成 prompt_text，基于最新的 content_json 和 visual_json。"""
    content_json = slide.content_json or {}
    visual_json = slide.visual_json or {}

    page_intent = {
        "page_num": slide.page_num,
        "type": visual_json.get("type") or content_json.get("type", "content"),
        "layout": visual_json.get("layout"),
        "visual_summary": visual_json.get("visual_summary", ""),
        "visual_evidence": visual_json.get("visual_evidence", ""),
        "visual_description": visual_json.get("visual_description", ""),
        "design_notes": visual_json.get("design_notes", ""),
        "visual_asset_ids": visual_json.get("visual_asset_ids", []),
        "visual_asset_usage": visual_json.get("visual_asset_usage", {}),
        "logo_policy": visual_json.get("logo_policy"),
    }
    page_intent = _with_project_logo_policy(page_intent, project) or page_intent

    content_text = content_json.get("text_content", {})
    if isinstance(content_text, dict):
        content_text = {
            "headline": content_text.get("headline", ""),
            "subhead": content_text.get("subhead", ""),
            "body": content_text.get("body", ""),
        }
    else:
        content_text = {"headline": "", "subhead": "", "body": ""}

    # 收集参考图描述：项目级核心资产优先，页面级参考图随后补充。
    reference_images = _project_refs_for_prompt(project, visual_json.get("visual_asset_ids") or [], page_intent)
    if slide.reference_images:
        for ref in slide.reference_images:
            reference_images.append({
                "id": ref.id,
                "role": ref.role,
                "description": os.path.basename(ref.file_path or ""),
                "process_mode": ref.process_mode or "blend",
            })

    # 获取项目级风格覆盖；没有确认风格时从内容推导紧凑 style pack。
    style_text_override = _style_text_from_selected_style(project.selected_style) or _derive_project_style_pack(
        project,
        [{"type": slide.type or "content", "text_content": content_text}],
    )

    prompt = generate_prompt_for_page(
        page_intent=page_intent,
        content_text=content_text,
        style_id=project.style_id or "default",
        reference_images=reference_images or None,
        style_text_override=style_text_override,
    )

    slide.prompt_text = prompt
    db.commit()
    logger.info(f"RetrySlide: 已重新生成第 {slide.page_num} 页 prompt")
    return prompt


class FinetuneRequest(BaseModel):
    # Backward compatible: older frontend versions send a fully composed prompt.
    new_prompt: Optional[str] = None
    # Preferred path: send the user's plain edit request and let the image model
    # edit the current slide image directly.
    instruction: Optional[str] = None
    attachment_ids: Optional[List[str]] = None


def _finetune_should_attach_project_product_assets(instruction: str) -> bool:
    text = (instruction or "").lower()
    action_terms = ("换成", "替换", "换上", "改成", "改为", "replace", "use the uploaded", "上传")
    asset_terms = (
        "核心资产", "客户", "产品", "油瓶", "瓶", "包装",
        "visual asset", "product", "bottle", "packaging",
    )
    return any(term in text for term in action_terms) and any(term in text for term in asset_terms)


def _project_visual_asset_ids_for_finetune(project: Project, slide: Slide, instruction: str) -> List[str]:
    if not _finetune_should_attach_project_product_assets(instruction):
        return []

    selected_ids: list[str] = []
    if slide.visual_json and isinstance(slide.visual_json, dict):
        raw_ids = slide.visual_json.get("visual_asset_ids") or []
        if isinstance(raw_ids, list):
            selected_ids = [str(x) for x in raw_ids]

    candidate_refs = [
        ref for ref in (project.reference_images or [])
        if ref.role == "visual_asset"
        and not ref.slide_id
        and str(ref.asset_kind or "").lower() in {"product", "material"}
        and os.path.exists(ref.file_path)
    ]
    candidate_refs.sort(
        key=lambda ref: (
            selected_ids.index(ref.id) if ref.id in selected_ids else 999,
            ref.asset_name or "",
        )
    )
    return [ref.id for ref in candidate_refs[:3]]


def _build_direct_finetune_prompt(
    slide: Slide,
    instruction: str,
    attachment_count: int = 0,
    project_visual_asset_count: int = 0,
) -> str:
    """Compose a direct image-edit prompt for single-slide finetuning."""
    content = slide.content_json or {}
    text_content = content.get("text_content") if isinstance(content, dict) else {}
    headline = ""
    subhead = ""
    body = ""
    if isinstance(text_content, dict):
        headline = str(text_content.get("headline") or "")
        subhead = str(text_content.get("subhead") or "")
        raw_body = text_content.get("body") or ""
        if isinstance(raw_body, list):
            body = "\n".join(str(item.get("content") if isinstance(item, dict) else item) for item in raw_body[:5])
        else:
            body = str(raw_body)

    ref_count = attachment_count + project_visual_asset_count
    ref_note = (
        f"\nThere are {ref_count} additional reference image(s) after the current slide. "
        "These additional images are the user's current-turn references. "
        "When the user says 'this image', 'the image', 'this person', or similar wording, resolve it to the most prominent subject in these current-turn reference images, not to anything already printed on the slide, product packaging, labels, icons, or earlier page assets. "
        "Use them only when they help satisfy the user's request. Infer natural placement, scale, and cropping from the slide layout."
        if ref_count
        else ""
    )
    project_asset_note = (
        "\nProject visual asset rule: the last "
        f"{project_visual_asset_count} additional reference image(s) are protected project product/material assets. "
        "If the request asks to replace a product, bottle, packaging, or client asset, use these protected project asset images as the authoritative source. "
        "Do not keep, copy, or regenerate any conflicting brand/product already visible in the current slide image."
        if project_visual_asset_count
        else ""
    )

    return f"""Edit the FIRST supplied image, which is the current PPT slide.

User edit request:
{instruction.strip()}

Rules:
- Apply the request immediately. Do not ask clarifying questions.
- Preserve all unmentioned text, layout, colors, typography, icons, charts, image areas, and background exactly as much as possible.
- If the user supplied extra reference images, they are the current-turn references. Integrate or borrow from them only where the request implies it.
- Do not copy people or illustrations printed on product packaging unless the user explicitly asks for the packaging artwork.
- Keep the final result as a polished 16:9 presentation slide, not a mockup.
- Keep text readable and do not invent new copy unless the user explicitly asked for it.
{ref_note}
{project_asset_note}

Slide text context for preservation:
Headline: {headline}
Subhead: {subhead}
Body: {body}
"""


@router.post("/{project_id}/slides/{slide_id}/finetune")
def finetune_slide(
    project_id: str,
    slide_id: str,
    body: FinetuneRequest,
    db: Session = Depends(get_db),
):
    """单页微调：存档当前图片 → 更新 prompt → 触发重新生成。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if get_active_run(db, project_id):
        raise HTTPException(status_code=409, detail="当前项目已有任务正在运行，请等待完成后再开始下一步")

    slide = db.query(Slide).filter(Slide.id == slide_id, Slide.project_id == project_id).first()
    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found")

    if slide.status == "generating":
        raise HTTPException(status_code=400, detail="该页面正在生成中，请等待完成后再微调")

    instruction = (body.instruction or "").strip()
    new_prompt = (body.new_prompt or "").strip()
    attachment_ids = body.attachment_ids or []
    if not instruction and not new_prompt:
        raise HTTPException(status_code=400, detail="instruction 不能为空")

    if instruction and (not slide.image_path or not os.path.exists(slide.image_path)):
        raise HTTPException(status_code=400, detail="当前页面没有可用于微调的图片")

    # 1. 存档当前图片为历史版本
    archived_version = _archive_current_image(slide, db)

    # 2. 更新 prompt。instruction 模式下，把当前页图片作为第一张参考图走 image edit。
    if instruction:
        if not archived_version or not os.path.exists(archived_version.image_path):
            raise HTTPException(status_code=400, detail="当前页面图片存档失败，无法微调")
        valid_attachment_ids = []
        if attachment_ids:
            refs = db.query(ReferenceImage).filter(
                ReferenceImage.project_id == project_id,
                ReferenceImage.slide_id == slide_id,
                ReferenceImage.id.in_(attachment_ids),
                ReferenceImage.role == "finetune_ref",
            ).all()
            found_ids = {ref.id for ref in refs}
            valid_attachment_ids = [ref_id for ref_id in attachment_ids if ref_id in found_ids]

        project_visual_asset_ids = _project_visual_asset_ids_for_finetune(project, slide, instruction)
        slide.prompt_text = _build_direct_finetune_prompt(
            slide,
            instruction,
            len(valid_attachment_ids),
            len(project_visual_asset_ids),
        )
        visual_json = copy.deepcopy(slide.visual_json) or {}
        visual_json["finetune_base_image_path"] = archived_version.image_path
        visual_json["finetune_instruction"] = instruction
        visual_json["finetune_attachment_ids"] = valid_attachment_ids
        visual_json["finetune_visual_asset_ids"] = project_visual_asset_ids
        slide.visual_json = visual_json
    else:
        slide.prompt_text = new_prompt
    slide.status = "generating"
    slide.error_msg = None
    run = create_project_run(
        db,
        project_id,
        kind="finetune",
        stage="batch_generation",
        target_page_nums=[slide.page_num],
        total_count=1,
        message="单页微调任务已排队",
    )
    db.commit()

    # 3. 触发异步生图
    _update_progress(project_id, {
        "target_page_nums": [slide.page_num],
        "target_count": 1,
    }, run.id)

    task = generate_slides_task.delay(project_id, [slide.page_num], run_id=run.id)
    set_run_task(db, run.id, task.id)
    db.commit()
    redis_client.set(f"project:{project_id}:task_id", task.id, ex=3600)
    redis_client.set(f"project:{project_id}:task_started_at", str(time.time()), ex=3600)

    return {
        "message": "Finetune started",
        "slide_id": slide_id,
        "page_num": slide.page_num,
        "mode": "direct_edit" if instruction else "prompt",
        "run": serialize_run(run),
    }


@router.post("/{project_id}/slides/{slide_id}/retry")
def retry_slide(
    project_id: str,
    slide_id: str,
    body: RetryRequest = Body(default_factory=RetryRequest),
    db: Session = Depends(get_db),
):
    """重试单页生成。支持重新生成 prompt 后再生图。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if get_active_run(db, project_id):
        raise HTTPException(status_code=409, detail="当前项目已有任务正在运行，请等待完成后再开始下一步")

    slide = db.query(Slide).filter(Slide.id == slide_id, Slide.project_id == project_id).first()
    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found")

    if slide.status == "generating":
        raise HTTPException(status_code=400, detail="该页面正在生成中，请勿重复重试")

    # 如果要求重新生成 prompt，基于最新 content/visual 重新生成
    if body.regenerate_prompt:
        _regenerate_slide_prompt(slide, project, db)

    if not slide.prompt_text:
        raise HTTPException(status_code=400, detail="Slide has no prompt. Generate prompts first.")

    slide.status = "generating"
    slide.error_msg = None
    run = create_project_run(
        db,
        project_id,
        kind="page_generation",
        stage="batch_generation",
        target_page_nums=[slide.page_num],
        total_count=1,
        message="单页生成任务已排队",
    )
    db.commit()

    # 记录本次重试的目标页码和数量，供前端进度显示用
    _update_progress(project_id, {
        "target_page_nums": [slide.page_num],
        "target_count": 1,
    }, run.id)

    task = generate_slides_task.delay(project_id, [slide.page_num], run_id=run.id)
    set_run_task(db, run.id, task.id)
    db.commit()
    redis_client.set(f"project:{project_id}:task_id", task.id, ex=3600)
    redis_client.set(f"project:{project_id}:task_started_at", str(time.time()), ex=3600)

    return {"message": "Retry started", "slide_id": slide_id, "page_num": slide.page_num, "run": serialize_run(run)}


@router.patch("/{project_id}/slides/content")
def update_slide_content(
    project_id: str,
    body: UpdateContentRequest,
    db: Session = Depends(get_db),
):
    """更新指定页码的 slide content_json。安全 merge：只更新 text_content 和 speaker_notes。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if body.slide_id:
        slide = db.query(Slide).filter(Slide.project_id == project_id, Slide.id == body.slide_id).first()
    else:
        slide = (
            db.query(Slide)
            .filter(Slide.project_id == project_id, Slide.page_num == body.page_num)
            .first()
        )
    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found")

    # 必须用深拷贝，否则 SQLAlchemy 检测不到 JSON 字段的变更
    existing = copy.deepcopy(slide.content_json) or {}
    new_content = _normalize_content_json_markdown(body.content_json)

    # 安全 merge：只替换 text_content 和 speaker_notes，保留其他字段
    if "text_content" in new_content:
        existing_text = existing.get("text_content") or {}
        if not isinstance(existing_text, dict):
            existing_text = {}
        existing_text.update(new_content["text_content"])
        existing["text_content"] = existing_text
    if "speaker_notes" in new_content:
        existing["speaker_notes"] = new_content["speaker_notes"]

    slide.content_json = existing
    _invalidate_content_dependent_outputs(project)
    db.commit()

    return {
        "message": "Slide content updated",
        "page_num": slide.page_num,
        "slide_id": slide.id,
    }


@router.patch("/{project_id}/slides/visual")
def update_slide_visual(
    project_id: str,
    body: UpdateVisualRequest,
    db: Session = Depends(get_db),
):
    """更新指定页码的 slide visual_json。安全 merge：只更新传入的字段。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if body.slide_id:
        slide = db.query(Slide).filter(Slide.project_id == project_id, Slide.id == body.slide_id).first()
    else:
        slide = (
            db.query(Slide)
            .filter(Slide.project_id == project_id, Slide.page_num == body.page_num)
            .first()
        )
    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found")

    existing = copy.deepcopy(slide.visual_json) or {}
    new_visual = body.visual_json

    # 安全 merge：替换传入的字段，保留其他字段
    allowed_fields = {"visual_evidence", "visual_description", "visual_summary", "design_notes", "layout"}
    for key in allowed_fields:
        if key in new_visual:
            existing[key] = new_visual[key]

    slide.visual_json = existing
    _invalidate_visual_plan_dependent_outputs(project, [slide])
    db.commit()

    return {
        "message": "Slide visual updated",
        "page_num": slide.page_num,
        "slide_id": slide.id,
    }


@router.delete("/{project_id}/slides/{slide_id}")
def delete_slide(
    project_id: str,
    slide_id: str,
    db: Session = Depends(get_db),
):
    """删除指定 slide，并自动压缩后续页码。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    slide = db.query(Slide).filter(Slide.id == slide_id, Slide.project_id == project_id).first()
    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found")

    deleted_page_num = slide.page_num
    db.delete(slide)

    # 后续页面页码减 1
    later_slides = (
        db.query(Slide)
        .filter(Slide.project_id == project_id, Slide.page_num > deleted_page_num)
        .order_by(Slide.page_num)
        .all()
    )
    for s in later_slides:
        s.page_num -= 1
        # 同步更新 content_json 中的 page_num（必须用深拷贝，否则 SQLAlchemy 检测不到变更）
        if s.content_json and isinstance(s.content_json, dict):
            updated = copy.deepcopy(s.content_json)
            updated["page_num"] = s.page_num
            s.content_json = updated
        # 同步更新 visual_json 中的 page_num
        visual_json = getattr(s, "visual_json", None)
        if visual_json and isinstance(visual_json, dict):
            updated = copy.deepcopy(visual_json)
            updated["page_num"] = s.page_num
            s.visual_json = updated

    _invalidate_content_dependent_outputs(project)
    db.commit()
    return {"message": "Slide deleted", "slide_id": slide_id, "deleted_page_num": deleted_page_num}


@router.post("/{project_id}/slides")
def create_slide(
    project_id: str,
    body: CreateSlideRequest,
    db: Session = Depends(get_db),
):
    """在指定页码位置插入新 slide，后续页面页码自动后移。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    slides = (
        db.query(Slide)
        .filter(Slide.project_id == project_id)
        .order_by(Slide.page_num)
        .all()
    )

    insert_page_num = body.page_num

    # 如果插入位置超出末尾，直接追加到最后
    if not slides:
        insert_page_num = 1
    elif insert_page_num > len(slides) + 1:
        insert_page_num = len(slides) + 1

    # 后续页面页码加 1（从后往前遍历，避免重复更新）
    for s in reversed(slides):
        if s.page_num >= insert_page_num:
            s.page_num += 1
            if s.content_json and isinstance(s.content_json, dict):
                updated = copy.deepcopy(s.content_json)
                updated["page_num"] = s.page_num
                s.content_json = updated
            visual_json = getattr(s, "visual_json", None)
            if visual_json and isinstance(visual_json, dict):
                updated = copy.deepcopy(visual_json)
                updated["page_num"] = s.page_num
                s.visual_json = updated

    new_content = copy.deepcopy(body.content_json)
    new_content.setdefault("page_num", insert_page_num)
    new_slide = Slide(
        project_id=project_id,
        page_num=insert_page_num,
        type=new_content.get("type", "content"),
        content_json=new_content,
    )
    db.add(new_slide)
    _invalidate_content_dependent_outputs(project)
    db.commit()
    db.refresh(new_slide)

    return {
        "message": "Slide created",
        "slide_id": new_slide.id,
        "page_num": new_slide.page_num,
    }


@router.post("/{project_id}/reorder")
def reorder_slides(
    project_id: str,
    body: ReorderRequest,
    db: Session = Depends(get_db),
):
    """根据传入的 page_nums 顺序重新排序 slides。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    slides = (
        db.query(Slide)
        .filter(Slide.project_id == project_id)
        .order_by(Slide.page_num)
        .all()
    )

    # 建立 page_num -> slide 映射
    slide_by_page = {s.page_num: s for s in slides}

    if len(body.page_nums) != len(slides):
        raise HTTPException(status_code=400, detail="页码数量与项目不符")
    if len(set(body.page_nums)) != len(body.page_nums):
        raise HTTPException(status_code=400, detail="页码列表中存在重复")

    for new_idx, old_page_num in enumerate(body.page_nums, start=1):
        slide = slide_by_page.get(old_page_num)
        if not slide:
            raise HTTPException(status_code=400, detail=f"页码 {old_page_num} 不存在")
        slide.page_num = new_idx
        if slide.content_json and isinstance(slide.content_json, dict):
            updated = copy.deepcopy(slide.content_json)
            updated["page_num"] = new_idx
            slide.content_json = updated

    _invalidate_content_dependent_outputs(project)
    db.commit()
    return {"message": "Slides reordered", "new_order": body.page_nums}


@router.post("/{project_id}/extract-template")
def extract_template(
    project_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """上传 PPT/PDF 并提取每页缩略图作为模板参考。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # 安全检查：拒绝路径遍历攻击
    safe_name = file.filename.replace("\\", "/").split("/")[-1]
    if ".." in safe_name or not safe_name or safe_name.startswith("."):
        raise HTTPException(status_code=400, detail="非法文件名")

    # 保存上传文件
    upload_dir = os.path.join(settings.UPLOAD_DIR, project_id)
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, f"template_{safe_name}")
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # 提取缩略图
    from app.services.template_extractor import extract_template_images, recommend_template_pages
    try:
        pages = extract_template_images(file_path, project_id, settings.UPLOAD_DIR)
    except Exception as e:
        logger.error(f"模板提取失败: {e}")
        raise HTTPException(status_code=500, detail=f"模板提取失败: {str(e)}")

    old_template_refs = db.query(ReferenceImage).filter(
        ReferenceImage.project_id == project_id,
        ReferenceImage.role == "template",
    ).all()
    for old_ref in old_template_refs:
        db.delete(old_ref)

    # 获取 Content Plan 用于更智能的推荐
    slides = db.query(Slide).filter(Slide.project_id == project_id).order_by(Slide.page_num).all()
    content_plan = [s.content_json for s in slides]
    recommendations = recommend_template_pages(pages, content_plan)

    # 保存为 reference_images (role = template)
    for page in pages:
        ref = ReferenceImage(
            project_id=project_id,
            file_path=page["file_path"],
            role="template",
        )
        db.add(ref)

    # 保存推荐映射到 project.selected_template_recommendations
    # 格式: {cover: {page_num, file_path, category}, toc: {...}, content: {...}, ending: {...}}
    project.selected_template_recommendations = {
        k: ({"page_num": v["page_num"], "file_path": v["file_path"], "category": v.get("category", "content")} if v else None)
        for k, v in recommendations.items()
    }
    _invalidate_style_dependent_outputs(project)
    db.commit()

    return {
        "message": "Template extracted",
        "total_pages": len(pages),
        "pages": [
            {"page_num": p["page_num"], "url": p["url"], "category": p.get("category", "content")}
            for p in pages
        ],
        "recommendations": {
            k: ({"page_num": v["page_num"], "url": v["url"], "category": v.get("category", "content")} if v else None)
            for k, v in recommendations.items()
        },
    }


@router.get("/{project_id}/template-pages")
def list_template_pages(
    project_id: str,
    db: Session = Depends(get_db),
):
    """列出已提取的模板页面。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    refs = db.query(ReferenceImage).filter(
        ReferenceImage.project_id == project_id,
        ReferenceImage.role == "template",
    ).all()

    total = len(refs)
    from app.services.template_extractor import _infer_page_category

    return [
        {
            "id": ref.id,
            "url": f"/uploads/{project_id}/templates/{os.path.basename(ref.file_path)}",
            "category": _infer_page_category(idx + 1, total),
        }
        for idx, ref in enumerate(refs)
    ]


# ========== 单页微调：版本管理 ==========

MAX_VERSIONS_PER_SLIDE = 10


def _archive_current_image(slide: Slide, db: Session) -> SlideVersion | None:
    """将 slide 当前图片存档为一个历史版本。若版本数超限则删除最老的。"""
    if not slide.image_path or not os.path.exists(slide.image_path):
        return None

    # 计算下一个版本号
    max_ver = db.query(SlideVersion).filter(
        SlideVersion.slide_id == slide.id
    ).order_by(SlideVersion.version_number.desc()).first()
    next_ver = (max_ver.version_number + 1) if max_ver else 1

    version_dir = os.path.join(settings.OUTPUT_DIR or "./outputs", slide.project_id, "versions")
    os.makedirs(version_dir, exist_ok=True)
    version_path = os.path.join(version_dir, f"slide_{slide.page_num:02d}_v{next_ver}.png")
    shutil.copy2(slide.image_path, version_path)

    version = SlideVersion(
        slide_id=slide.id,
        project_id=slide.project_id,
        image_path=version_path,
        prompt_text=slide.prompt_text,
        version_number=next_ver,
    )
    db.add(version)

    # 清理超限版本
    all_versions = db.query(SlideVersion).filter(
        SlideVersion.slide_id == slide.id
    ).order_by(SlideVersion.version_number.asc()).all()
    if len(all_versions) > MAX_VERSIONS_PER_SLIDE:
        for v in all_versions[:len(all_versions) - MAX_VERSIONS_PER_SLIDE]:
            # Older versions used to point at the live slide image. Never delete
            # the file currently shown by the slide while pruning version rows.
            if v.image_path != slide.image_path and v.image_path != version_path and os.path.exists(v.image_path):
                try:
                    os.remove(v.image_path)
                except Exception:
                    pass
            db.delete(v)

    db.flush()
    return version


def _output_url_for_path(path: str) -> str:
    output_root = os.path.abspath(settings.OUTPUT_DIR or "./outputs")
    abs_path = os.path.abspath(path)
    try:
        rel_path = os.path.relpath(abs_path, output_root)
    except ValueError:
        rel_path = os.path.basename(path)
    return "/outputs/" + rel_path.replace(os.sep, "/")


@router.get("/{project_id}/slides/{slide_id}/versions")
def list_slide_versions(
    project_id: str,
    slide_id: str,
    db: Session = Depends(get_db),
):
    """返回某页的所有历史版本（按版本号倒序）。"""
    slide = db.query(Slide).filter(Slide.id == slide_id, Slide.project_id == project_id).first()
    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found")

    versions = db.query(SlideVersion).filter(
        SlideVersion.slide_id == slide_id
    ).order_by(SlideVersion.version_number.desc()).all()

    return [
        {
            "id": v.id,
            "version_number": v.version_number,
            "image_url": _output_url_for_path(v.image_path),
            "prompt_text": v.prompt_text,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }
        for v in versions
    ]


@router.delete("/{project_id}/slides/{slide_id}/versions/{version_id}")
def delete_slide_version(
    project_id: str,
    slide_id: str,
    version_id: str,
    db: Session = Depends(get_db),
):
    """删除某个历史版本（含物理文件）。"""
    version = db.query(SlideVersion).filter(
        SlideVersion.id == version_id,
        SlideVersion.slide_id == slide_id,
        SlideVersion.project_id == project_id,
    ).first()
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    # 删除物理文件
    if os.path.exists(version.image_path):
        try:
            os.remove(version.image_path)
        except Exception as e:
            logger.warning(f"Failed to delete version file {version.image_path}: {e}")

    db.delete(version)
    db.commit()
    return {"message": "Version deleted", "version_id": version_id}


class RestoreVersionBody(BaseModel):
    pass


@router.post("/{project_id}/slides/{slide_id}/versions/{version_id}/restore")
def restore_slide_version(
    project_id: str,
    slide_id: str,
    version_id: str,
    db: Session = Depends(get_db),
):
    """将某历史版本恢复为当前（新旧 image_path 互换）。"""
    slide = db.query(Slide).filter(Slide.id == slide_id, Slide.project_id == project_id).first()
    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found")

    version = db.query(SlideVersion).filter(
        SlideVersion.id == version_id,
        SlideVersion.slide_id == slide_id,
    ).first()
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    if not os.path.exists(version.image_path):
        raise HTTPException(status_code=400, detail="Version image file no longer exists")

    # 交换当前图片和版本图片的路径
    current_path = slide.image_path
    version_path = version.image_path

    slide.image_path = version_path
    slide.prompt_text = version.prompt_text
    version.image_path = current_path

    db.commit()
    return {
        "message": "Version restored",
        "slide_id": slide_id,
        "new_image_url": _output_url_for_path(version_path),
    }
