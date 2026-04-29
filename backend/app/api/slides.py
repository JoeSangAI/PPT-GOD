import asyncio
import copy
import io
import json
import logging
import os
import shutil
import time
from typing import List, Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image as PILImage
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from app.models.base import get_db, SessionLocal
from app.models.models import Project, Slide, ReferenceImage
from app.core.llm_client import get_llm_client

# 全局运行中任务跟踪（project_id -> asyncio.Task）
_running_tasks: dict = {}
from app.core.config import settings
from app.services.content_plan import generate_content_plan
from app.services.visual_plan import generate_visual_plan
from app.services.prompt_engine import generate_prompt_for_page, generate_prompts_for_all_pages
from app.services.image_analyzer import analyze_reference_image
from app.tasks import generate_slides_task, redis_client
from celery.result import AsyncResult

router = APIRouter(prefix="/projects", tags=["slides"])
logger = logging.getLogger(__name__)

# 上传限制常量
MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # 20MB
MAX_REFERENCE_IMAGES_PER_PAGE = 10
ALLOWED_UPLOAD_ROLES = {"style_ref", "logo", "template", "content_ref", "chart_ref"}
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

# 全局生成进度存储（内存级，项目重启后丢失）
generation_progress: dict[str, dict] = {}


def _update_progress(project_id: str, data: dict):
    """更新指定项目的生成进度。"""
    generation_progress[project_id] = data


def _mark_generation_idle(project: Project | None, db: Session, reason: str):
    """Return a stale generating project to a recoverable state."""
    if project and project.status == "generating":
        project.status = "prompt_ready"
        slides = db.query(Slide).filter(
            Slide.project_id == project.id,
            Slide.status == "generating",
        ).all()
        for slide in slides:
            slide.status = "prompt_ready"
            slide.error_msg = reason
        db.commit()


def _load_project_documents(project_id: str) -> str:
    """读取项目已上传文档的提取文本。"""
    docs_dir = os.path.join(settings.UPLOAD_DIR, project_id, "docs")
    if not os.path.exists(docs_dir):
        return ""

    parts = []
    for filename in sorted(os.listdir(docs_dir)):
        if filename.endswith(".extracted.txt"):
            original_name = filename[:-14]
            path = os.path.join(docs_dir, filename)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
                if len(text) > 8000:
                    text = text[:8000] + "\n\n[文档内容过长，已截断]"
                parts.append(f"--- 文档: {original_name} ---\n{text}")
            except Exception:
                continue

    return "\n\n".join(parts)


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


def _reference_process_mode_instruction(mode: str | None) -> str:
    mode = mode or "blend"
    if mode == "blend":
        return (
            "Blend mode: extract the main subject/style from the reference image and integrate it naturally into the slide. "
            "You may adjust scale, lighting, angle, and background so it fits the layout."
        )
    if mode == "crop":
        return (
            "Crop mode: preserve the reference image content as a recognizable visual block, but crop it to fit the slide composition. "
            "Do not reinterpret the subject; design around its visible core."
        )
    if mode == "original":
        return (
            "Original mode: preserve the reference image exactly as-is. Do not crop, stretch, rotate, or alter it. "
            "Reserve a layout area where the original image can be placed intact."
        )
    return f"Custom mode {mode}: respect the user's selected reference image handling mode."


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
                f"intent={_reference_process_mode_instruction(ref.process_mode)}; "
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


def _generate_content_plan_bg(project_id: str, topic: str, page_count: int | None = None):
    """后台任务：异步生成 Content Plan。"""
    import logging
    logger = logging.getLogger(__name__)
    from app.models.base import SessionLocal
    db = SessionLocal()
    try:
        logger.info(f"[ContentPlan BG] Starting for project={project_id}, topic={topic[:30]}...")
        _update_progress(project_id, {"stage": "starting", "message": "开始生成 Content Plan...", "current_page": 0, "total_pages": page_count or 10})

        documents = _load_project_documents(project_id)
        if documents:
            logger.info(f"[ContentPlan BG] Loaded documents, length={len(documents)}")

        def report_progress(data: dict):
            _update_progress(project_id, data)

        outline = generate_content_plan(
            topic=topic,
            page_count=page_count or 10,
            documents=documents,
            on_progress=report_progress,
        )
        logger.info(f"[ContentPlan BG] Generated {len(outline)} pages")
        _update_progress(project_id, {"stage": "saving", "message": "正在保存结果...", "current_page": len(outline), "total_pages": len(outline)})
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
        db.commit()
        logger.info(f"[ContentPlan BG] Completed for project={project_id}")
    except Exception as e:
        db.rollback()
        logger.error(f"[ContentPlan BG] Failed for project={project_id}: {e}")
        _update_progress(project_id, {"stage": "error", "message": f"生成失败：{str(e)[:100]}"})
        # 标记项目状态为失败，让前端可以检测
        try:
            project = db.query(Project).filter(Project.id == project_id).first()
            if project:
                project.status = "draft"
                db.commit()
        except Exception:
            pass
    finally:
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

    # 优先使用用户传入的 topic，否则用项目标题
    topic = body.topic.strip() if body.topic else project.title
    page_count = body.page_count

    documents = _load_project_documents(project_id)
    if documents:
        logger.info(f"[ContentPlan] Loaded documents for project={project_id}, length={len(documents)}")

    if background_tasks:
        background_tasks.add_task(_generate_content_plan_bg, project_id, topic, page_count)
        return {"message": "Content plan generation started", "status": "planning"}

    # fallback：同步执行（开发/测试环境）
    outline = generate_content_plan(
        topic=topic,
        page_count=page_count or 10,
        documents=documents,
    )
    db.query(Slide).filter(Slide.project_id == project_id).delete()
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
    project.status = "planning"
    db.commit()
    return {"message": "Content plan generated", "slides_count": len(outline)}


@router.get("/{project_id}/slides")
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
    return [
        {
            "id": s.id,
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
                    "url": f"/uploads/{project_id}/{os.path.basename(ref.file_path)}",
                }
                for ref in (s.reference_images or [])
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
        .options(joinedload(Slide.reference_images))
        .filter(Slide.project_id == project_id)
        .order_by(Slide.page_num)
        .all()
    )
    if not slides:
        raise HTTPException(status_code=400, detail="No content plan found. Generate content plan first.")

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

    visual_plan = generate_visual_plan(
        content_plan=content_plan,
        style_id=project.style_id or "default",
        reference_image_ids=ref_ids,
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
        .options(joinedload(Slide.reference_images))
        .filter(Slide.project_id == project_id)
        .order_by(Slide.page_num)
        .all()
    )
    if not slides:
        raise HTTPException(status_code=400, detail="No slides found")

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
    visual_plan = [s.visual_json for s in target_slides if s.visual_json]

    # 准备参考图描述（仅项目级参考图；页面级参考图已通过 content_plan 的 reference_context 传递）
    ref_images = []
    for img in project.reference_images:
        ref_images.append({
            "id": img.id,
            "role": img.role,
            "process_mode": img.process_mode,
            "description": img.file_path,
        })

    # 如果 selected_style 包含描述，直接作为 style_text_override 传入，
    # 避免依赖可能不存在的模板文件
    style_text_override = None
    if project.selected_style:
        try:
            style_obj = json.loads(project.selected_style) if isinstance(project.selected_style, str) else project.selected_style
            if style_obj and style_obj.get("description"):
                style_text_override = style_obj["description"]
        except Exception:
            pass

    prompts = generate_prompts_for_all_pages(
        visual_plan=visual_plan,
        content_plan=content_plan,
        style_id="default",
        reference_images=ref_images or None,
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

    style_override = None
    style_text_override = None
    if project.selected_style:
        palette = project.selected_style.get("palette", [])
        mood = project.selected_style.get("mood", "")
        font = project.selected_style.get("font", "")
        description = project.selected_style.get("description", "")
        style_override = {
            "meta": {
                "palette": palette[:5] if isinstance(palette, list) else [],
                "mood": mood,
                "font": font,
            },
            "body": description,
        }
        style_text_override = f"""Style: {project.selected_style.get('name', 'Custom')}
Palette: {', '.join(str(c) for c in palette[:5])}
Mood: {mood}
Font: {font}
Description: {description}"""

    ref_images = []
    for img in project.reference_images:
        ref_images.append({
            "id": img.id,
            "role": img.role,
            "description": img.file_path,
        })

    # 标记生成中状态，避免前端基于旧状态提前结束轮询
    project.status = "generating"
    db.commit()

    # 启动后台任务，独立于 HTTP 连接运行
    existing_task = _running_tasks.get(project_id)
    if existing_task and not existing_task.done():
        existing_task.cancel()
        try:
            await existing_task
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(
        _do_generate_visual_and_prompts(project_id, [s.page_num for s in target_slides] if body.page_nums else None)
    )
    _running_tasks[project_id] = task

    return {"status": "started", "message": "视觉方案和生图 Prompt 生成已启动，请稍候。"}


async def _do_generate_visual_and_prompts(project_id: str, page_nums: Optional[List[int]] = None):
    """后台任务：生成视觉方案和 Prompt，完成后更新数据库。"""
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            logger.warning(f"Project {project_id} not found for background generation")
            return

        project.status = "generating"
        db.commit()

        slides = (
            db.query(Slide)
            .options(joinedload(Slide.reference_images))
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

        style_override = None
        style_text_override = None
        if project.selected_style:
            palette = project.selected_style.get("palette", [])
            mood = project.selected_style.get("mood", "")
            font = project.selected_style.get("font", "")
            description = project.selected_style.get("description", "")
            style_override = {
                "meta": {
                    "palette": palette[:5] if isinstance(palette, list) else [],
                    "mood": mood,
                    "font": font,
                },
                "body": description,
            }
            style_text_override = f"""Style: {project.selected_style.get('name', 'Custom')}
Palette: {', '.join(str(c) for c in palette[:5])}
Mood: {mood}
Font: {font}
Description: {description}"""

        ref_images = []
        for img in project.reference_images:
            ref_images.append({
                "id": img.id,
                "role": img.role,
                "process_mode": img.process_mode,
                "description": img.file_path,
            })
        # 注意：页面级参考图已通过 content_plan 的 reference_context 传递，不再塞进全局 reference_images

        # Step 1: 生成 Visual Plan
        _update_progress(project_id, {
            "stage": "visual_planning",
            "message": "正在分析内容结构，为每一页设计视觉方案...",
            "current_page": 0,
            "total_pages": len(target_slides),
        })
        visual_plan = await asyncio.to_thread(
            generate_visual_plan,
            content_plan=content_plan,
            style_id=project.style_id or "default",
            reference_image_ids=ref_ids,
            style_override=style_override,
            progress_callback=lambda msg: _update_progress(project_id, {
                "stage": "visual_planning",
                "message": msg,
                "current_page": 0,
                "total_pages": len(target_slides),
            }),
        )

        # 更新数据库：visual plan（先提交，避免后续 prompt 失败导致 visual plan 也被回滚）
        visual_by_page = {v["page_num"]: v for v in visual_plan}
        for slide in target_slides:
            slide.visual_json = visual_by_page.get(slide.page_num, {})
            if slide.status in ("pending", "planning"):
                slide.status = "visual_ready"
        db.commit()

        # Step 2: 并发生成 Prompts（最多 5 个并发，避免 API 限流）
        visual_plan_for_prompts = [s.visual_json for s in target_slides if s.visual_json]
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
                        reference_images=ref_images or None,
                        style_text_override=style_text_override,
                    )
                async with progress_lock:
                    completed_count += 1
                    _update_progress(project_id, {
                        "stage": "prompt_writing",
                        "message": f"正在为第 {page_num} 页撰写生图 Prompt...",
                        "current_page": completed_count,
                        "total_pages": total_prompt_pages,
                    })
                return {"page_num": page_num, "prompt": prompt}
            except Exception as e:
                logger.error(f"PromptEngine: 第 {page_num} 页 Prompt 生成失败: {e}")
                async with progress_lock:
                    completed_count += 1
                    _update_progress(project_id, {
                        "stage": "prompt_writing",
                        "message": f"第 {page_num} 页 Prompt 生成失败，跳过...",
                        "current_page": completed_count,
                        "total_pages": total_prompt_pages,
                    })
                return {"page_num": page_num, "prompt": "", "error": str(e)}

        tasks = [_gen_one(intent) for intent in visual_plan_for_prompts]
        prompts = await asyncio.gather(*tasks, return_exceptions=False)
        _update_progress(project_id, {
            "stage": "saving",
            "message": "正在保存结果...",
            "current_page": total_prompt_pages,
            "total_pages": total_prompt_pages,
        })

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
                    project.status = "pending"
                db.commit()
        except Exception as status_err:
            logger.warning(f"Failed to reset project status after cancellation: {status_err}")
        _update_progress(project_id, {
            "stage": "error",
            "message": "任务被取消",
            "current_page": 0,
            "total_pages": len(target_slides) if page_nums else 0,
        })
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
                    project.status = "pending"
                db.commit()
        except Exception as status_err:
            logger.warning(f"Failed to reset project status after error: {status_err}")
        _update_progress(project_id, {
            "stage": "error",
            "message": f"生成失败：{str(e)[:100]}",
            "current_page": 0,
            "total_pages": len(target_slides) if page_nums else 0,
        })
    finally:
        db.close()
        _running_tasks.pop(project_id, None)
        generation_progress.pop(project_id, None)


@router.get("/{project_id}/generation-status")
async def get_generation_status(project_id: str, db: Session = Depends(get_db)):
    """查询正在运行的生成任务状态（同时检查 asyncio 后台任务和 Celery 任务）。"""
    task = _running_tasks.get(project_id)
    project = db.query(Project).filter(Project.id == project_id).first()

    # 检查 asyncio 后台任务
    if task and not task.done():
        return {"status": "running", "project_status": project.status if project else None}

    # 检查 Celery 任务（通过 Redis 中的 task_id）
    try:
        task_id = redis_client.get(f"project:{project_id}:task_id")
        if task_id:
            from celery.result import AsyncResult
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
                        redis_client.delete(f"project:{project_id}:task_id")
                        redis_client.delete(f"project:{project_id}:task_started_at")
                        return {"status": "idle", "project_status": project.status if project else None}
                return {"status": "running", "project_status": project.status if project else None}
    except Exception as e:
        logger.warning(f"Failed to check Celery status for {project_id}: {e}")

    return {"status": "idle", "project_status": project.status if project else None}


@router.get("/{project_id}/prompts/{slide_id}")
def get_slide_prompt(project_id: str, slide_id: str, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    slide = db.query(Slide).filter(Slide.id == slide_id, Slide.project_id == project_id).first()
    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found")

    return {
        "slide_id": slide.id,
        "page_num": slide.page_num,
        "type": slide.type,
        "prompt": slide.prompt_text,
        "visual": slide.visual_json,
        "content": slide.content_json,
    }


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

    if project.status == "generating":
        raise HTTPException(status_code=400, detail="当前已有生成任务在执行中")

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

    # 标记生成中状态，减少并发重复触发的窗口
    project.status = "generating"
    db.commit()

    page_nums = body.page_nums

    # 种子页打样：自动找出所有种子页
    if body.prototype and not page_nums:
        seed_pages = [
            s.page_num for s in slides
            if s.visual_json and s.visual_json.get("is_seed_recommended")
        ]
        if seed_pages:
            page_nums = seed_pages
        else:
            # 没有种子页时，默认生成前 4 页作为打样
            page_nums = [s.page_num for s in slides[:4]]

    # 记录本次生成的目标页码，供前端进度显示用
    target_slides = [s for s in slides if s.page_num in page_nums] if page_nums else slides
    _update_progress(project_id, {
        "target_page_nums": page_nums,
        "target_count": len(target_slides),
    })

    # 使用 Celery 异步任务（Celery worker 内部有 Redis 锁兜底防重）
    task = generate_slides_task.delay(project_id, page_nums, prototype=body.prototype)
    # 保存 task_id 到 Redis，供 stop-generation 撤销用
    redis_client.set(f"project:{project_id}:task_id", task.id, ex=3600)
    redis_client.set(f"project:{project_id}:task_started_at", str(time.time()), ex=3600)

    return {
        "message": "Generation started",
        "project_id": project_id,
        "prototype": body.prototype or bool(page_nums),
        "page_nums": page_nums,
        "task_id": task.id,
    }


@router.post("/{project_id}/confirm-prototype")
def confirm_prototype(
    project_id: str,
    db: Session = Depends(get_db),
):
    """确认种子页打样结果，启动全量生成。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.status != "prototype_ready":
        raise HTTPException(
            status_code=400,
            detail=f"当前状态为 {project.status}，不支持确认打样。请先完成打样生成。"
        )

    # 找出所有未完成的页（排除已成功的种子页）
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

    # 立即标记生成中状态，让前端在 Celery 任务真正启动前就能看到进度
    project.status = "generating"
    db.commit()

    task = generate_slides_task.delay(project_id, target_page_nums)
    redis_client.set(f"project:{project_id}:task_id", task.id, ex=3600)
    redis_client.set(f"project:{project_id}:task_started_at", str(time.time()), ex=3600)

    return {
        "message": "Full generation started",
        "project_id": project_id,
        "page_nums": target_page_nums,
        "task_id": task.id,
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

    if project.status != "generating":
        return {"message": "No generation in progress", "status": project.status}

    # 重置项目状态
    project.status = "prompt_ready"

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

    return {"message": "Generation stopped", "status": "prompt_ready"}


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
        if s.status == "completed":
            completed_count += 1
        slide_status.append({
            "page_num": s.page_num,
            "status": s.status,
            "error_msg": s.error_msg,
        })

    pptx_filename = "prototype.pptx" if project.status == "prototype_ready" else "presentation.pptx"
    pptx_path = os.path.join(
        settings.OUTPUT_DIR or "./outputs",
        project_id,
        pptx_filename,
    )
    has_pptx = os.path.exists(pptx_path)

    progress = generation_progress.get(project_id, {})
    return {
        "project_id": project_id,
        "project_status": project.status,
        "total_slides": len(slides),
        "completed_slides": completed_count,
        "target_count": progress.get("target_count") or len(slides),
        "target_page_nums": progress.get("target_page_nums"),
        "has_pptx": has_pptx,
        "pptx_path": pptx_path if has_pptx else None,
        "slides": slide_status,
    }


@router.get("/{project_id}/generation-progress")
def get_generation_progress(project_id: str, db: Session = Depends(get_db)):
    """获取 Content Plan 后台生成的实时进度。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    progress = generation_progress.get(project_id, {})
    return {
        "project_id": project_id,
        "project_status": project.status,
        "stage": progress.get("stage"),
        "message": progress.get("message"),
        "current_page": progress.get("current_page"),
        "total_pages": progress.get("total_pages"),
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
    process_mode: str = Form("blend"),
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

    if process_mode not in {"blend", "crop", "original"}:
        raise HTTPException(status_code=400, detail="Invalid process_mode. Allowed: blend, crop, original")

    # 如果传了 slide_id，校验该 slide 存在
    if slide_id:
        slide = db.query(Slide).filter(Slide.id == slide_id, Slide.project_id == project_id).first()
        if not slide:
            raise HTTPException(status_code=404, detail="Slide not found")

    # 数量限制：单页最多 10 张参考图
    existing_count = db.query(ReferenceImage).filter(
        ReferenceImage.project_id == project_id,
        ReferenceImage.slide_id == slide_id,
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
        filename = f"{prefix}{role}_{safe_name}"
        file_path = os.path.join(project_upload_dir, filename)
        img.save(file_path, "PNG")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"图片格式无法处理: {e}")

    ref_image = ReferenceImage(
        project_id=project_id,
        slide_id=slide_id,
        file_path=file_path,
        role=role,
        process_mode=process_mode,
    )
    db.add(ref_image)
    db.commit()
    db.refresh(ref_image)

    return {
        "id": ref_image.id,
        "file_path": file_path,
        "role": role,
        "slide_id": slide_id,
        "process_mode": process_mode,
        "url": f"/uploads/{project_id}/{filename}",
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

    images = query.all()
    return [
        {
            "id": img.id,
            "role": img.role,
            "slide_id": img.slide_id,
            "process_mode": img.process_mode or "blend",
            "url": f"/uploads/{project_id}/{os.path.basename(img.file_path)}",
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
    process_mode: str = Form(...),
    db: Session = Depends(get_db),
):
    """更新参考图的处理模式（blend/crop/original）。"""
    if process_mode not in {"blend", "crop", "original"}:
        raise HTTPException(status_code=400, detail="Invalid process_mode. Allowed: blend, crop, original")

    ref = db.query(ReferenceImage).filter(
        ReferenceImage.id == ref_id,
        ReferenceImage.project_id == project_id,
    ).first()
    if not ref:
        raise HTTPException(status_code=404, detail="Reference image not found")

    ref.process_mode = process_mode
    db.commit()
    db.refresh(ref)
    return {
        "id": ref.id,
        "process_mode": ref.process_mode,
        "url": f"/uploads/{project_id}/{os.path.basename(ref.file_path)}",
    }

    db.delete(ref)
    db.commit()

    # 数据库提交成功后再删物理文件，文件删失败不影响事务
    try:
        if os.path.exists(ref.file_path):
            os.remove(ref.file_path)
            logger.info(f"Deleted file: {ref.file_path}")
    except Exception as e:
        logger.warning(f"Failed to delete file {ref.file_path}: {e}")
    return {"message": "Deleted", "id": ref_id}


@router.post("/{project_id}/retry-failed")
def retry_failed_slides(
    project_id: str,
    db: Session = Depends(get_db),
):
    """批量重试 project 下所有失败的 slides。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

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

    db.commit()

    task = generate_slides_task.delay(project_id, page_nums)
    redis_client.set(f"project:{project_id}:task_id", task.id, ex=3600)
    redis_client.set(f"project:{project_id}:task_started_at", str(time.time()), ex=3600)

    return {
        "message": "Retry started",
        "page_nums": page_nums,
        "count": len(page_nums),
    }


@router.post("/{project_id}/slides/{slide_id}/retry")
def retry_slide(
    project_id: str,
    slide_id: str,
    db: Session = Depends(get_db),
):
    """重试失败的单页生成。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    slide = db.query(Slide).filter(Slide.id == slide_id, Slide.project_id == project_id).first()
    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found")

    if not slide.prompt_text:
        raise HTTPException(status_code=400, detail="Slide has no prompt. Generate prompts first.")

    if slide.status == "generating":
        raise HTTPException(status_code=400, detail="该页面正在生成中，请勿重复重试")

    slide.status = "generating"
    slide.error_msg = None
    db.commit()

    task = generate_slides_task.delay(project_id, [slide.page_num])
    redis_client.set(f"project:{project_id}:task_id", task.id, ex=3600)
    redis_client.set(f"project:{project_id}:task_started_at", str(time.time()), ex=3600)

    return {"message": "Retry started", "slide_id": slide_id, "page_num": slide.page_num}


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
    new_content = body.content_json

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
    allowed_fields = {"visual_description", "design_notes", "layout", "seed_family"}
    for key in allowed_fields:
        if key in new_visual:
            existing[key] = new_visual[key]

    slide.visual_json = existing
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

    db.commit()
    return {"message": "Slides reordered", "new_order": body.page_nums}


@router.post("/{project_id}/slides/{slide_id}/set-seed")
def set_seed_page(
    project_id: str,
    slide_id: str,
    db: Session = Depends(get_db),
):
    """将指定 slide 设为其 Seed Family 的种子页，同 Family 其他页自动取消。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    slide = db.query(Slide).filter(Slide.id == slide_id, Slide.project_id == project_id).first()
    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found")

    visual = slide.visual_json or {}
    if not visual:
        raise HTTPException(status_code=400, detail="Slide has no visual plan")

    family = visual.get("seed_family")
    if not family:
        from app.services.visual_plan import _infer_seed_family
        family = _infer_seed_family(slide.type or "content")

    # 取消同 Family 其他页的种子推荐
    all_slides = db.query(Slide).filter(Slide.project_id == project_id).all()
    for s in all_slides:
        if s.id == slide_id:
            continue
        s_visual = s.visual_json or {}
        s_family = s_visual.get("seed_family")
        if not s_family:
            from app.services.visual_plan import _infer_seed_family
            s_family = _infer_seed_family(s.type or "content")
        if s_family == family and s_visual.get("is_seed_recommended"):
            updated = copy.deepcopy(s_visual)
            updated["is_seed_recommended"] = False
            s.visual_json = updated

    # 设置当前页为种子推荐
    updated = copy.deepcopy(visual)
    updated["is_seed_recommended"] = True
    slide.visual_json = updated

    db.commit()
    return {"message": f"Slide {slide.page_num} set as seed for {family}", "family": family, "page_num": slide.page_num}


@router.post("/{project_id}/slides/{slide_id}/unset-seed")
def unset_seed_page(
    project_id: str,
    slide_id: str,
    db: Session = Depends(get_db),
):
    """取消指定 slide 的种子页推荐状态。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    slide = db.query(Slide).filter(Slide.id == slide_id, Slide.project_id == project_id).first()
    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found")

    visual = slide.visual_json or {}
    if visual.get("is_seed_recommended"):
        updated = copy.deepcopy(visual)
        updated["is_seed_recommended"] = False
        slide.visual_json = updated
        db.commit()

    return {"message": f"Slide {slide.page_num} unset as seed", "page_num": slide.page_num}


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
