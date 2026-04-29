import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from PIL import Image
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import Project, Slide
from app.services.image_generation import generate_slide_image, save_slide_image
from app.services.pptx_assembler import assemble_pptx
from app.services.visual_plan import _infer_seed_family

logger = logging.getLogger(__name__)


def _load_reference_images(slide: Slide) -> List[Dict]:
    """
    加载一页的参考图，包含三层：
    1. 项目级：project.reference_images 中 role=style_ref 的全局风格参考图
    2. 模板级：project.selected_template_recommendations 中对应类型的模板种子页
    3. 页面级：slide.reference_images 中该页单独上传的内容/图表参考图

    返回 List[Dict]，每个 Dict 包含 image (PIL.Image) 和 process_mode (str)。
    """
    refs = []

    # 1. 页面级参考图优先：Prompt 中的 Reference Image 1/2/3 必须与
    # 生图 API 的图片输入顺序一致，便于模型按用户意图使用这些图。
    if slide.reference_images:
        for idx, ref in enumerate(slide.reference_images, start=1):
            if os.path.exists(ref.file_path):
                try:
                    refs.append({
                        "image": Image.open(ref.file_path),
                        "process_mode": ref.process_mode or "blend",
                        "role": ref.role,
                        "label": f"Reference Image {idx}",
                        "file_path": ref.file_path,
                    })
                except Exception as e:
                    logger.warning(f"无法加载页面参考图 {ref.file_path}: {e}")
            else:
                logger.warning(f"页面参考图文件不存在: {ref.file_path}")

    # 2. 项目级风格参考图（默认 blend）
    if slide.project and slide.project.reference_images:
        for ref in slide.project.reference_images:
            if ref.role == "style_ref" and os.path.exists(ref.file_path):
                try:
                    refs.append({
                        "image": Image.open(ref.file_path),
                        "process_mode": "blend",
                        "role": ref.role,
                        "label": "Global Style Reference",
                        "file_path": ref.file_path,
                    })
                except Exception as e:
                    logger.warning(f"无法加载参考图 {ref.file_path}: {e}")

    # 3. 模板级参考图（默认 blend）
    if slide.project and slide.project.selected_template_recommendations:
        recommendations = slide.project.selected_template_recommendations
        slide_type = slide.type or "content"
        template_key = _map_slide_type_to_template_key(slide_type)
        tmpl = recommendations.get(template_key)
        if tmpl and isinstance(tmpl, dict) and tmpl.get("file_path"):
            tmpl_path = tmpl["file_path"]
            if os.path.exists(tmpl_path):
                try:
                    refs.append({
                        "image": Image.open(tmpl_path),
                        "process_mode": "blend",
                        "role": "template",
                        "label": "Template Reference",
                        "file_path": tmpl_path,
                    })
                    logger.info(f"Slide {slide.page_num}: 加载模板参考图 {tmpl_path} (type={slide_type} -> key={template_key})")
                except Exception as e:
                    logger.warning(f"无法加载模板参考图 {tmpl_path}: {e}")

    logger.info(f"Slide {slide.page_num}: 共加载 {len(refs)} 张参考图")
    return refs


def _map_slide_type_to_template_key(slide_type: str) -> str:
    """将 slide 类型映射到模板类别。"""
    mapping = {
        "cover": "cover",
        "toc": "toc",
        "hero": "content",
        "data": "content",
        "ending": "ending",
    }
    return mapping.get(slide_type, "content")


def _generate_one_slide(
    slide: Slide,
    project_id: str,
    output_dir: str,
    seed_image_paths: Optional[Dict[str, str]] = None,
    preloaded_ref_data: Optional[List[Dict]] = None,
) -> Dict:
    """
    在线程池中执行单页生成（纯 IO/计算，不涉及数据库操作）。
    返回 dict: {slide, image_path?, error?}
    """
    if not slide.prompt_text:
        return {"slide": slide, "error": "缺少 prompt"}

    try:
        ref_data = list(preloaded_ref_data) if preloaded_ref_data else []
        ref_images = [r["image"] for r in ref_data]

        # 非种子页加载同 family 的种子页图片作为风格参考
        if seed_image_paths and slide.visual_json and not slide.visual_json.get("is_seed_recommended"):
            family = slide.visual_json.get("seed_family") or _infer_seed_family(slide.type or "content")
            seed_path = seed_image_paths.get(family)
            if seed_path and os.path.exists(seed_path):
                try:
                    ref_images.append(Image.open(seed_path))
                    ref_data.append({
                        "image": ref_images[-1],
                        "process_mode": "blend",
                        "role": "seed",
                        "label": f"Seed Reference ({family})",
                        "file_path": seed_path,
                    })
                    logger.info(f"Slide {slide.page_num}: 加载种子页参考图 {seed_path} (family={family})")
                except Exception as e:
                    logger.warning(f"无法加载种子页参考图 {seed_path}: {e}")

        # 根据页面级参考图的 process_mode 在 prompt 中追加处理指令
        mode_instructions = []
        for r in ref_data:
            mode = r.get("process_mode", "blend")
            label = r.get("label", "Reference Image")
            basename = os.path.basename(r.get("file_path", "")) if r.get("file_path") else ""
            if mode == "blend":
                mode_instructions.append(
                    f"{label}{f' ({basename})' if basename else ''}: Extract the main subject from this reference image and seamlessly blend it into the slide composition. "
                    "You may adjust angle, lighting, and perspective to fit the layout while preserving the subject's key visual characteristics."
                )
            elif mode == "crop":
                mode_instructions.append(
                    f"{label}{f' ({basename})' if basename else ''}: Preserve this reference image content but allow cropping to fit the 3:2 (1536x1024) slide aspect ratio."
                    "Maintain the core visual information and subject integrity."
                )
            elif mode == "original":
                mode_instructions.append(
                    f"{label}{f' ({basename})' if basename else ''}: Insert this reference image exactly as-is. Do NOT crop, stretch, rotate, or alter its proportions or content in any way."
                )

        prompt = slide.prompt_text
        if mode_instructions:
            extra = (
                "CRITICAL — The following user-uploaded reference images MUST be incorporated into the final slide background as concrete visual elements, not abstract replacements:\n"
                + "\n".join(f"- {d}" for d in mode_instructions)
                + "\n\nNow generate the slide background according to the original design brief below:\n"
            )
            prompt = extra + prompt

        img = generate_slide_image(
            prompt=prompt,
            reference_images=ref_images if ref_images else None,
            resolution="4K",
            aspect_ratio="16:9",
        )
        image_path = save_slide_image(
            img=img,
            project_id=project_id,
            page_num=slide.page_num,
            output_dir=output_dir,
        )
        logger.info(f"Pipeline: 第 {slide.page_num} 页生成完成")
        return {"slide": slide, "image_path": image_path, "error": None}
    except Exception as e:
        logger.error(f"Pipeline: 第 {slide.page_num} 页生成失败: {e}")
        return {"slide": slide, "error": str(e)[:500]}


def run_generation_pipeline(
    project_id: str,
    db: Session,
    page_nums: Optional[List[int]] = None,
    prototype: bool = False,
):
    """
    执行生成流水线（支持单页并行，由 Celery 调用）。
    并行策略：IO 密集型（调用 DeerAPI），用 ThreadPoolExecutor 并发 3 页。
    """
    logger.info(f"Pipeline: 开始生成项目 {project_id}, page_nums={page_nums}")

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        logger.error(f"Pipeline: 项目 {project_id} 不存在")
        return

    slides = (
        db.query(Slide)
        .filter(Slide.project_id == project_id)
        .order_by(Slide.page_num)
        .all()
    )
    if not slides:
        logger.error(f"Pipeline: 项目 {project_id} 没有幻灯片")
        return

    # 过滤目标页
    target_slides = slides
    if page_nums:
        target_slides = [s for s in slides if s.page_num in page_nums]
        mode_desc = "打样模式" if prototype else "指定页面生成"
        logger.info(f"Pipeline: {mode_desc}，只生成 {len(target_slides)} 页")

    original_status = project.status
    project.status = "generating"
    db.commit()

    # 先把所有目标页标记为 generating
    for slide in target_slides:
        if not slide.prompt_text:
            slide.status = "failed"
            slide.error_msg = "缺少 prompt"
        else:
            slide.status = "generating"
            slide.error_msg = None
    db.commit()

    output_dir = settings.OUTPUT_DIR or "./outputs"
    slide_images = []

    # ===== 两阶段生成：先种子页，再非种子页（带种子页参考）=====

    # 1. 预先收集项目中所有种子页的已有图片（包括不在 target_slides 中的）
    seed_image_paths: Dict[str, str] = {}
    for s in slides:
        if s.visual_json and s.visual_json.get("is_seed_recommended") and s.image_path and os.path.exists(s.image_path):
            family = s.visual_json.get("seed_family") or _infer_seed_family(s.type or "content")
            if family not in seed_image_paths:
                seed_image_paths[family] = s.image_path
                logger.info(f"Pipeline: 复用已有种子页图片 family={family}, page={s.page_num}")

    # 2. 从 target_slides 中分离种子页和非种子页
    seed_slides = [s for s in target_slides if s.visual_json and s.visual_json.get("is_seed_recommended")]
    non_seed_slides = [s for s in target_slides if s not in seed_slides]

    # 3. 阶段一：生成需要重新生成的种子页
    seeds_to_generate = []
    for s in seed_slides:
        family = s.visual_json.get("seed_family") or _infer_seed_family(s.type or "content")
        if not seed_image_paths.get(family) and s.prompt_text:
            seeds_to_generate.append(s)
        elif seed_image_paths.get(family):
            # 该种子页已有图片，直接复用并标记为完成，避免状态永远卡在 generating
            s.status = "completed"
            s.error_msg = None
            logger.info(f"Slide {s.page_num}: 种子页已有图片，跳过生成 (family={family})")

    if seeds_to_generate:
        logger.info(f"Pipeline: 阶段一，生成 {len(seeds_to_generate)} 个种子页")
        # 在主线程预加载参考图，避免 worker 线程访问 SQLAlchemy 触发 SQLite 线程错误
        seed_ref_data = {s.id: _load_reference_images(s) for s in seeds_to_generate}
        with ThreadPoolExecutor(max_workers=min(len(seeds_to_generate), 2)) as executor:
            future_to_slide = {
                executor.submit(_generate_one_slide, slide, project_id, output_dir, None, seed_ref_data.get(slide.id)): slide
                for slide in seeds_to_generate
            }
            for future in as_completed(future_to_slide):
                result = future.result()
                slide = result["slide"]
                if result.get("error"):
                    slide.status = "failed"
                    slide.error_msg = result["error"]
                else:
                    slide.image_path = result["image_path"]
                    slide.status = "completed"
                    slide.error_msg = None
                    family = slide.visual_json.get("seed_family") or _infer_seed_family(slide.type or "content")
                    seed_image_paths[family] = result["image_path"]
                    speaker_notes = ""
                    if slide.content_json and isinstance(slide.content_json, dict):
                        speaker_notes = slide.content_json.get("speaker_notes", "")
                    slide_images.append({
                        "page_num": slide.page_num,
                        "image_path": result["image_path"],
                        "speaker_notes": speaker_notes,
                    })
                db.commit()

    # 4. 阶段二：生成非种子页，传入种子页图片作为参考
    non_seed_with_prompt = [s for s in non_seed_slides if s.prompt_text]
    if non_seed_with_prompt:
        logger.info(f"Pipeline: 阶段二，生成 {len(non_seed_with_prompt)} 个非种子页，种子页参考 families={list(seed_image_paths.keys())}")
        # 在主线程预加载参考图，避免 worker 线程访问 SQLAlchemy 触发 SQLite 线程错误
        non_seed_ref_data = {s.id: _load_reference_images(s) for s in non_seed_with_prompt}
        max_workers = min(len(non_seed_with_prompt), 3)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_slide = {
                executor.submit(_generate_one_slide, slide, project_id, output_dir, seed_image_paths, non_seed_ref_data.get(slide.id)): slide
                for slide in non_seed_with_prompt
            }
            for future in as_completed(future_to_slide):
                result = future.result()
                slide = result["slide"]
                if result.get("error"):
                    slide.status = "failed"
                    slide.error_msg = result["error"]
                else:
                    slide.image_path = result["image_path"]
                    slide.status = "completed"
                    slide.error_msg = None
                    speaker_notes = ""
                    if slide.content_json and isinstance(slide.content_json, dict):
                        speaker_notes = slide.content_json.get("speaker_notes", "")
                    slide_images.append({
                        "page_num": slide.page_num,
                        "image_path": result["image_path"],
                        "speaker_notes": speaker_notes,
                    })
                db.commit()

    # 组装 PPTX 时，收集所有已完成页面的图片（包括之前任务生成的），
    # 而不是仅用当前任务生成的页面，避免重试单页后 PPTX 只剩 1 页。
    all_completed_images = []
    for s in slides:
        if s.status == "completed" and s.image_path and os.path.exists(s.image_path):
            speaker_notes = ""
            if s.content_json and isinstance(s.content_json, dict):
                speaker_notes = s.content_json.get("speaker_notes", "")
            all_completed_images.append({
                "page_num": s.page_num,
                "image_path": s.image_path,
                "speaker_notes": speaker_notes,
            })
    all_completed_images.sort(key=lambda x: x["page_num"])

    # 组装 PPTX
    if all_completed_images:
        try:
            # 打样时输出到 prototype.pptx，全量生成输出 presentation.pptx
            if prototype:
                pptx_path = os.path.join(output_dir, project_id, "prototype.pptx")
            else:
                pptx_path = os.path.join(output_dir, project_id, "presentation.pptx")
            os.makedirs(os.path.dirname(pptx_path), exist_ok=True)

            assemble_pptx(
                slide_images=all_completed_images,
                output_path=pptx_path,
                logo_path=None,
            )

            # 状态流转
            if prototype:
                project.status = "prototype_ready"
            elif not page_nums:
                project.status = "completed"
            else:
                if all(s.status in ("completed", "failed") for s in slides):
                    project.status = "completed"
                else:
                    # 部分生成后若还有未完成页面，original_status 可能是
                    # 被本任务临时设成的 "generating"，不能恢复回去，否则死锁。
                    project.status = "prompt_ready" if original_status == "generating" else original_status
            db.commit()
            logger.info(f"Pipeline: PPTX 组装完成 {pptx_path}")
        except Exception as e:
            logger.error(f"Pipeline: PPTX 组装失败: {e}")
            # 图片都已成功，只是 PPTX 组装出错，用单独状态区分，避免和生成失败混淆
            project.status = "assembly_failed"
            db.commit()
    else:
        if prototype:
            project.status = "failed"
        elif not page_nums:
            project.status = "failed"
        else:
            project.status = original_status
        db.commit()
        logger.error(f"Pipeline: 没有成功生成的图片，无法组装 PPTX")
