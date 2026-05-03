import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

import redis
from PIL import Image
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import Project, Slide
from app.services.image_generation import generate_slide_image, save_slide_image
from app.services.pptx_assembler import assemble_pptx
from app.services.run_state import finish_run, mark_run_running, update_run_progress, cleanup_generation_progress
from app.services.visual_plan import _infer_seed_family

logger = logging.getLogger(__name__)

redis_client = redis.from_url(settings.REDIS_URL or "redis://localhost:6379/0")


def _load_reference_images(slide: Slide) -> List[Dict]:
    """
    加载一页真正需要作为 image input 上传给生图模型的参考图：
    1. 单页微调：当前页历史图片 + 本轮附件
    2. 页面级内容/图表参考图
    3. 模板级：project.selected_template_recommendations 中对应类型的模板页

    注意：全局 style_ref 只用于前置风格分析和 prompt 约束，不作为垫图上传；
    logo 也暂不作为垫图上传。

    返回 List[Dict]，每个 Dict 包含 image (PIL.Image) 和 process_mode (str)。
    """
    refs = []

    # 0. 单页微调时，当前页历史图片必须排第一，图像编辑模型以它为底图。
    finetune_base_path = None
    if slide.visual_json and isinstance(slide.visual_json, dict):
        finetune_base_path = slide.visual_json.get("finetune_base_image_path")
    if finetune_base_path:
        if os.path.exists(finetune_base_path):
            try:
                refs.append({
                    "image": Image.open(finetune_base_path),
                    "process_mode": "original",
                    "role": "finetune_base",
                    "label": "Current Slide Image",
                    "file_path": finetune_base_path,
                })
                logger.info(f"Slide {slide.page_num}: 加载微调底图 {finetune_base_path}")
            except Exception as e:
                logger.warning(f"无法加载微调底图 {finetune_base_path}: {e}")
        else:
            logger.warning(f"微调底图文件不存在: {finetune_base_path}")

    # 1. 页面级参考图优先：Prompt 中的 Reference Image 1/2/3 必须与
    # 生图 API 的图片输入顺序一致，便于模型按用户意图使用这些图。
    if slide.reference_images:
        finetune_attachment_ids = set()
        if slide.visual_json and isinstance(slide.visual_json, dict):
            finetune_attachment_ids = set(slide.visual_json.get("finetune_attachment_ids") or [])
        for idx, ref in enumerate(slide.reference_images, start=1):
            if finetune_base_path:
                # In direct edit mode, keep the image context tight: current slide
                # plus only the images uploaded for this chat turn. Long-lived page
                # refs can contain people or products that make "this person/image"
                # ambiguous for the image model.
                if ref.role != "finetune_ref" or ref.id not in finetune_attachment_ids:
                    continue
            elif ref.role == "finetune_ref":
                continue
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

    if finetune_base_path:
        logger.info(f"Slide {slide.page_num}: 微调模式仅加载底图和本轮附件，共 {len(refs)} 张参考图")
        return refs

    # 2. 模板级参考图（默认 blend）
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
        is_finetune_edit = bool(
            slide.visual_json
            and isinstance(slide.visual_json, dict)
            and slide.visual_json.get("finetune_base_image_path")
        )

        prompt = slide.prompt_text

        # 非种子页只通过文字锚定种子 family 的视觉一致性，不再上传种子图垫图。
        if seed_image_paths and slide.visual_json and not slide.visual_json.get("is_seed_recommended") and not is_finetune_edit:
            family = slide.visual_json.get("seed_family") or _infer_seed_family(slide.type or "content")
            if seed_image_paths.get(family):
                prompt = (
                    "CRITICAL — Maintain deck-level visual consistency through the written style system. "
                    "Do not copy a generated seed slide as an image reference. "
                    "Use the selected style's palette, typography mood, ornament language, and page-type adaptation rules. "
                    f"This slide belongs to the {family} family.\n\n"
                    + prompt
                )

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
    run_id: str | None = None,
):
    """
    执行生成流水线（支持单页并行，由 Celery 调用）。
    并行策略：主流程仍用线程池分派页面，真实图片 API 调用在 image_generation
    模块内限流，避免多页同时上传参考图导致写入超时。
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

    mark_run_running(db, run_id, stage="batch_generation", message="正在生成图片...")
    db.commit()

    # 先把目标页标记为 generating（只涉及本任务负责的页面）
    for slide in target_slides:
        if not slide.prompt_text:
            slide.status = "failed"
            slide.error_msg = "缺少 prompt"
        else:
            slide.status = "generating"
            slide.error_msg = None
    db.commit()
    total_target = len(target_slides)
    update_run_progress(db, run_id, total_count=total_target, completed_count=0, failed_count=0)
    db.commit()

    output_dir = settings.OUTPUT_DIR or "./outputs"
    slide_images = []

    # ===== 两阶段生成：先种子页，再非种子页（仅用文字锚定一致性）=====

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
        # 如果用户明确指定了这页（通过 page_nums），强制重新生成，不走缓存
        force_regenerate = page_nums is not None and s.page_num in page_nums
        if force_regenerate:
            seeds_to_generate.append(s)
            logger.info(f"Slide {s.page_num}: 用户明确指定重新生成，强制生成种子页 (family={family})")
        elif not seed_image_paths.get(family) and s.prompt_text:
            seeds_to_generate.append(s)
        elif seed_image_paths.get(family):
            # 该种子页已有图片，直接复用并标记为完成，避免状态永远卡在 generating
            s.status = "completed"
            s.error_msg = None
            logger.info(f"Slide {s.page_num}: 种子页已有图片，跳过生成 (family={family})")

    def record_generation_result(result: Dict, *, update_seed_family: bool = False) -> None:
        slide = result["slide"]
        if result.get("error"):
            slide.status = "failed"
            slide.error_msg = result["error"]
        else:
            slide.image_path = result["image_path"]
            slide.status = "completed"
            slide.error_msg = None
            if slide.visual_json and isinstance(slide.visual_json, dict) and slide.visual_json.get("finetune_base_image_path"):
                slide.visual_json = {
                    k: v for k, v in slide.visual_json.items()
                    if k not in {"finetune_base_image_path", "finetune_instruction", "finetune_attachment_ids"}
                }
                flag_modified(slide, "visual_json")
            if update_seed_family:
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
        completed_now = sum(1 for s in target_slides if s.status == "completed")
        failed_now = sum(1 for s in target_slides if s.status == "failed")
        update_run_progress(
            db,
            run_id,
            completed_count=completed_now,
            failed_count=failed_now,
            total_count=total_target,
            message=f"正在生成图片... {completed_now} / {total_target} 页完成"
            + (f"，{failed_now} 页失败" if failed_now else ""),
        )
        db.commit()

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
                record_generation_result(result, update_seed_family=True)

    # 4. 阶段二：生成非种子页，仅传入已有种子 family 信息用于文字提示锚定
    non_seed_with_prompt = [s for s in non_seed_slides if s.prompt_text]
    if non_seed_with_prompt:
        logger.info(f"Pipeline: 阶段二，生成 {len(non_seed_with_prompt)} 个非种子页，文字锚定 families={list(seed_image_paths.keys())}")
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
                record_generation_result(result)

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

    # 组装 PPTX（用 Redis 锁防止并发生成任务同时写文件）
    pptx_lock_key = f"project:{project_id}:pptx_assembly"
    assembly_error = None
    if all_completed_images:
        pptx_acquired = redis_client.set(pptx_lock_key, "1", nx=True, ex=30)
        if pptx_acquired:
            try:
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
                logger.info(f"Pipeline: PPTX 组装完成 {pptx_path}")
            except Exception as e:
                logger.error(f"Pipeline: PPTX 组装失败: {e}")
                # Assembly failures should not put the workflow in a synthetic
                # phase. Keep the slide generation facts and surface the error
                # on the run instead.
                assembly_error = str(e)[:500]
                db.commit()
            finally:
                redis_client.delete(pptx_lock_key)
        else:
            logger.info(f"Pipeline: 跳过 PPTX 组装，另一任务正在组装")

    # 状态流转：基于所有 slide 的实际状态判定，不依赖 original_status（并发安全）
    all_slide_statuses = [s.status for s in slides]
    generating_count = sum(1 for st in all_slide_statuses if st == "generating")
    completed_count = sum(1 for st in all_slide_statuses if st == "completed")
    failed_count = sum(1 for st in all_slide_statuses if st == "failed")
    target_completed = sum(1 for s in target_slides if s.status == "completed")
    target_failed = sum(1 for s in target_slides if s.status == "failed")
    target_errors = [
        str(s.error_msg).strip()
        for s in target_slides
        if s.status == "failed" and s.error_msg and str(s.error_msg).strip()
    ]
    failure_summary = target_errors[0] if target_errors else "部分页面生成失败"

    if prototype:
        project.status = "prototype_ready" if target_completed > 0 else "prompt_ready"
    elif not page_nums:
        # 全量生成：所有页都已处理完才算完成
        project.status = "completed" if generating_count == 0 and failed_count == 0 else "prompt_ready"
    else:
        # 部分页面生成：全部页面最终完成则 completed，否则回到画面设计待处理。
        if generating_count == 0 and completed_count == len(slides):
            project.status = "completed"
        else:
            project.status = "prompt_ready"
    run_status = "failed" if assembly_error or target_failed > 0 or target_completed == 0 else "succeeded"
    finish_run(
        db,
        run_id,
        status=run_status,
        message=(
            "PPTX 组装失败"
            if assembly_error
            else f"图片生成结束：{target_completed} / {total_target} 页完成"
            + (f"，{target_failed} 页失败" if target_failed else "")
        ),
        completed_count=target_completed,
        failed_count=target_failed,
        error_msg=assembly_error or (failure_summary if target_failed > 0 else None),
    )
    db.commit()
    logger.info(f"Pipeline: 项目状态流转 -> {project.status} (generating={generating_count}, completed={completed_count}, failed={failed_count})")

    if not all_completed_images:
        logger.warning(f"Pipeline: 没有成功生成的图片，无法组装 PPTX")

    # 清理内存中的 generation_progress，避免任务结束后端点仍返回旧数据
    cleanup_generation_progress(project_id)
