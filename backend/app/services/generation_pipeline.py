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
from app.services.logo_assets import prepare_logo_overlay_image
from app.services.logo_policy import should_use_logo_as_scene_asset
from app.services.pptx_assembler import assemble_pptx
from app.services.run_state import cleanup_generation_progress, finish_run, is_run_active, mark_run_running, update_run_progress
from app.utils.reference_image import default_visual_asset_process_mode

logger = logging.getLogger(__name__)

redis_client = redis.from_url(settings.REDIS_URL or "redis://localhost:6379/0")
MAX_REFERENCE_INPUTS = 14


def _reference_input_priority(ref: Dict) -> int:
    role = ref.get("role")
    kind = str(ref.get("asset_kind") or "").lower()
    if ref.get("manual_pin"):
        return -1
    if role == "visual_asset" and kind in {"product", "material"}:
        return 0
    if role == "logo":
        return 1
    if role in {"content_ref", "chart_ref"}:
        return 2
    if role == "visual_asset":
        return 3
    if role == "seed_ref":
        return 4
    if role == "template":
        return 5
    return 9


def _load_reference_images(
    slide: Slide,
    seed_image_paths: Optional[List[str]] = None,
) -> List[Dict]:
    """
    加载一页真正需要作为 image input 上传给生图模型的参考图：
    1. 单页微调：当前页历史图片 + 本轮附件（finetune 模式提前返回）
    2. 页面级内容/图表参考图（用户为本页明确上传的素材）
    3. 项目级 Logo：默认不进生图，PPT/预览阶段叠加；blend 模式下少数页面可作场景资产
    4. 项目级视觉资产：visual plan 为当前页选中的产品/人物/物料图
    5. 同家族种子页：作为版式锚点（仅复用版式语言，不复制内容）
    6. 模板级：仅当没有种子时使用 selected_template_recommendations

    注意：全局 style_ref 只用于前置风格分析和 prompt 约束，不作为垫图上传；

    参数：
    - seed_image_paths: 同家族已生成的种子页图片路径，用于版式一致性。
      若为 None 或空，按旧逻辑使用 template 兜底；若有则跳过 template。

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

    finetune_visual_asset_ids = []
    if finetune_base_path and slide.visual_json and isinstance(slide.visual_json, dict):
        raw_asset_ids = slide.visual_json.get("finetune_visual_asset_ids") or []
        if isinstance(raw_asset_ids, list):
            finetune_visual_asset_ids = [str(x) for x in raw_asset_ids][:3]

    # 1. 页面级参考图优先：Prompt 中的 Reference Image 1/2/3 必须与
    # 生图 API 的图片输入顺序一致，便于模型按用户意图使用这些图。
    already_loaded_paths = set()
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
            if ref.file_path in already_loaded_paths:
                logger.info(f"Slide {slide.page_num}: 跳过重复页面参考图 {ref.file_path}")
                continue
            if os.path.exists(ref.file_path):
                try:
                    refs.append({
                        "image": Image.open(ref.file_path),
                        "process_mode": ref.process_mode or "blend",
                        "role": ref.role,
                        "label": f"Reference Image {idx}",
                        "file_path": ref.file_path,
                        "id": getattr(ref, "id", None),
                        "asset_name": getattr(ref, "asset_name", None),
                        "asset_kind": getattr(ref, "asset_kind", None),
                        "usage_note": getattr(ref, "usage_note", None),
                    })
                    already_loaded_paths.add(ref.file_path)
                except Exception as e:
                    logger.warning(f"无法加载页面参考图 {ref.file_path}: {e}")
            else:
                logger.warning(f"页面参考图文件不存在: {ref.file_path}")

    if finetune_base_path and finetune_visual_asset_ids and slide.project and slide.project.reference_images:
        selected_set = set(finetune_visual_asset_ids)
        project_assets = [
            ref for ref in slide.project.reference_images
            if ref.role == "visual_asset" and ref.id in selected_set and not ref.slide_id
        ]
        project_assets.sort(
            key=lambda ref: finetune_visual_asset_ids.index(ref.id)
            if ref.id in finetune_visual_asset_ids else 999
        )
        for idx, ref in enumerate(project_assets, start=1):
            if len(refs) >= MAX_REFERENCE_INPUTS:
                break
            if os.path.exists(ref.file_path):
                try:
                    refs.append({
                        "image": Image.open(ref.file_path),
                        "process_mode": ref.process_mode or default_visual_asset_process_mode(getattr(ref, "asset_kind", None)),
                        "role": ref.role,
                        "label": f"Protected Project Visual Asset {idx}",
                        "file_path": ref.file_path,
                        "id": getattr(ref, "id", None),
                        "asset_name": getattr(ref, "asset_name", None),
                        "asset_kind": getattr(ref, "asset_kind", None),
                        "usage_note": getattr(ref, "usage_note", None),
                    })
                    logger.info(f"Slide {slide.page_num}: 微调模式加载项目视觉资产 {ref.file_path}")
                except Exception as e:
                    logger.warning(f"无法加载微调项目视觉资产 {ref.file_path}: {e}")
            else:
                logger.warning(f"微调项目视觉资产文件不存在: {ref.file_path}")

    if finetune_base_path:
        logger.info(f"Slide {slide.page_num}: 微调模式加载底图、本轮附件和项目资产，共 {len(refs)} 张参考图")
        return refs[:MAX_REFERENCE_INPUTS]

    # 2. 项目级 Logo：默认不进生图，后续用程序 overlay。
    # 只有用户把 Logo 设为 blend 且页面适合做场景标识时，才作为画面元素参考。
    if slide.project and slide.project.reference_images and len(refs) < MAX_REFERENCE_INPUTS:
        logo_ref = next(
            (
                ref for ref in slide.project.reference_images
                if (
                    ref.role == "logo"
                    and not ref.slide_id
                    and os.path.exists(ref.file_path)
                    and should_use_logo_as_scene_asset(slide, ref)
                )
            ),
            None,
        )
        if logo_ref:
            try:
                logo_path = prepare_logo_overlay_image(logo_ref.file_path)
                refs.append({
                    "image": Image.open(logo_path),
                    "process_mode": "blend",
                    "role": "logo",
                    "label": "Protected Brand Logo",
                    "file_path": logo_path,
                    "id": getattr(logo_ref, "id", None),
                    "asset_name": getattr(logo_ref, "asset_name", None),
                    "asset_kind": getattr(logo_ref, "asset_kind", None),
                    "usage_note": getattr(logo_ref, "usage_note", None),
                })
                logger.info(f"Slide {slide.page_num}: 加载受保护 Logo {logo_path}")
            except Exception as e:
                logger.warning(f"无法加载 Logo {logo_ref.file_path}: {e}")

    # 3. 全局视觉资产：只加载 visual plan 为当前页选中的资产。
    selected_asset_ids = []
    manual_asset_ids = set()
    if slide.visual_json and isinstance(slide.visual_json, dict):
        raw_ids = slide.visual_json.get("visual_asset_ids") or []
        if isinstance(raw_ids, list):
            selected_asset_ids = []
            for x in raw_ids:
                value = str(x)
                if value and value not in selected_asset_ids:
                    selected_asset_ids.append(value)
        manual_raw = slide.visual_json.get("manual_visual_asset_ids") or []
        if isinstance(manual_raw, list):
            manual_asset_ids = {str(x) for x in manual_raw if x}

    if selected_asset_ids and slide.project and slide.project.reference_images:
        selected_set = set(selected_asset_ids)
        project_assets = [
            ref for ref in slide.project.reference_images
            if ref.role == "visual_asset" and ref.id in selected_set and not ref.slide_id
        ]
        project_assets.sort(key=lambda ref: selected_asset_ids.index(ref.id) if ref.id in selected_asset_ids else 999)
        for idx, ref in enumerate(project_assets, start=1):
            if len(refs) >= MAX_REFERENCE_INPUTS:
                break
            if ref.file_path in already_loaded_paths:
                logger.info(f"Slide {slide.page_num}: 跳过重复视觉资产 {ref.file_path}")
                continue
            if os.path.exists(ref.file_path):
                try:
                    refs.append({
                        "image": Image.open(ref.file_path),
                        "process_mode": ref.process_mode or default_visual_asset_process_mode(getattr(ref, "asset_kind", None)),
                        "role": ref.role,
                        "label": f"Global Visual Asset {idx}",
                        "file_path": ref.file_path,
                        "id": getattr(ref, "id", None),
                        "asset_name": getattr(ref, "asset_name", None),
                        "asset_kind": getattr(ref, "asset_kind", None),
                        "usage_note": getattr(ref, "usage_note", None),
                        "manual_pin": ref.id in manual_asset_ids,
                    })
                    already_loaded_paths.add(ref.file_path)
                    logger.info(f"Slide {slide.page_num}: 加载视觉资产 {ref.file_path}")
                except Exception as e:
                    logger.warning(f"无法加载视觉资产 {ref.file_path}: {e}")
            else:
                logger.warning(f"视觉资产文件不存在: {ref.file_path}")

    # 4. 同家族种子页：版式锚点。仅取最多 2 张，避免抢主体权重。
    seed_loaded = False
    if seed_image_paths:
        for seed_idx, seed_path in enumerate(seed_image_paths[:2], start=1):
            if len(refs) >= MAX_REFERENCE_INPUTS:
                break
            if not seed_path or not os.path.exists(seed_path):
                logger.warning(f"种子页文件不存在: {seed_path}")
                continue
            try:
                refs.append({
                    "image": Image.open(seed_path),
                    "process_mode": "blend",
                    "role": "seed_ref",
                    "label": f"Family Seed Layout {seed_idx}",
                    "file_path": seed_path,
                })
                seed_loaded = True
                logger.info(f"Slide {slide.page_num}: 加载同家族种子页 {seed_path}")
            except Exception as e:
                logger.warning(f"无法加载种子页 {seed_path}: {e}")

    # 5. 模板级参考图（仅在没有种子页时使用，作为兜底）
    if not seed_loaded and slide.project and slide.project.selected_template_recommendations:
        recommendations = slide.project.selected_template_recommendations
        slide_type = slide.type or "content"
        template_key = _map_slide_type_to_template_key(slide_type)
        tmpl = recommendations.get(template_key)
        if tmpl and isinstance(tmpl, dict) and tmpl.get("file_path"):
            tmpl_path = tmpl["file_path"]
            if os.path.exists(tmpl_path) and len(refs) < MAX_REFERENCE_INPUTS:
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

    refs = sorted(refs, key=_reference_input_priority)
    if len(refs) > MAX_REFERENCE_INPUTS:
        logger.warning(
            "Slide %s: 参考图 %s 张超过 API 上限 %s，仅前 %s 张进入本次生图",
            slide.page_num,
            len(refs),
            MAX_REFERENCE_INPUTS,
            MAX_REFERENCE_INPUTS,
        )
    logger.info(f"Slide {slide.page_num}: 共加载 {len(refs)} 张参考图（种子: {'有' if seed_loaded else '无'}）")
    return refs[:MAX_REFERENCE_INPUTS]


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


def _slide_family(slide: Slide) -> Optional[str]:
    """从 slide.visual_json 读取 seed_family；为兼容老数据，缺失时按 type 推断。"""
    if slide.visual_json and isinstance(slide.visual_json, dict):
        family = slide.visual_json.get("seed_family")
        if family:
            return str(family)
    # 老数据 fallback：按 slide.type 推断
    slide_type = (slide.type or "content").lower()
    if slide_type in ("cover", "ending"):
        return "bookend"
    if slide_type in {"hero", "quote"}:
        return "hero"
    if slide_type == "toc":
        return "section"
    return "content"


def _is_finetune_slide(slide: Slide) -> bool:
    """该页是否处于 finetune（单页微调）模式：底图优先，跳过种子参考。"""
    if not slide.visual_json or not isinstance(slide.visual_json, dict):
        return False
    return bool(slide.visual_json.get("finetune_base_image_path"))


def _collect_existing_seeds(slides: List[Slide]) -> Dict[str, List[str]]:
    """
    扫描所有 slide，把已经成功生成的页按 seed_family 聚合，
    返回 {family: [image_path, ...]}，按推荐种子优先 + 页码升序排序。
    每家族最多保留 2 张，避免 Stage 2 注入过多版式样本干扰主体。
    """
    pool: Dict[str, List[Dict]] = {}
    for s in slides:
        if s.status != "completed":
            continue
        if not s.image_path or not os.path.exists(s.image_path):
            continue
        if _is_finetune_slide(s):
            # finetune 模式产生的图片继承自旧底图，不作为家族种子
            continue
        family = _slide_family(s)
        if not family:
            continue
        is_recommended = False
        if isinstance(s.visual_json, dict):
            is_recommended = bool(s.visual_json.get("is_seed_recommended"))
        pool.setdefault(family, []).append({
            "image_path": s.image_path,
            "page_num": s.page_num,
            "is_recommended": is_recommended,
        })

    seeds_by_family: Dict[str, List[str]] = {}
    for family, items in pool.items():
        items.sort(key=lambda x: (not x["is_recommended"], x["page_num"]))
        seeds_by_family[family] = [item["image_path"] for item in items[:2]]
    return seeds_by_family


def _is_product_or_material_ref(ref: Dict) -> bool:
    return (
        ref.get("role") == "visual_asset"
        and str(ref.get("asset_kind") or "").lower() in {"product", "material"}
    )


def _uses_direct_finetune_ref(ref_data: List[Dict]) -> bool:
    return any(ref.get("role") == "finetune_base" for ref in ref_data)


def _product_refinement_refs(ref_data: List[Dict]) -> List[Dict]:
    return [ref for ref in ref_data if _is_product_or_material_ref(ref)]


def _background_pass_prompt(prompt: str, product_refs: List[Dict]) -> str:
    product_names = [
        str(ref.get("asset_name") or "").strip()
        for ref in product_refs
        if str(ref.get("asset_name") or "").strip()
    ]
    product_label = " / ".join(product_names[:3]) if product_names else "the uploaded product"
    return (
        prompt
        + "\n\nFIRST PASS: generate the complete slide using the supplied reference material, including "
        f"{product_label}. Product fidelity can be approximate in this first pass; a second hidden refinement "
        "pass will strengthen the product details using the same uploaded reference image."
    )


def _product_refinement_prompt(slide: Slide, product_refs: List[Dict]) -> str:
    paths = [
        str(ref.get("file_path") or "").strip()
        for ref in product_refs
        if str(ref.get("file_path") or "").strip()
    ]
    if paths:
        return f"用第2张及后续参考图替换第一张PPT图中的产品。参考素材路径：{'; '.join(paths)}"
    return "用第2张及后续参考图替换第一张PPT图中的产品。"


def _generate_one_slide(
    slide: Slide,
    project_id: str,
    output_dir: str,
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
        product_refs = _product_refinement_refs(ref_data)
        use_product_refinement = bool(product_refs) and not _uses_direct_finetune_ref(ref_data)
        first_pass_ref_data = list(ref_data)
        ref_images = [r["image"] for r in first_pass_ref_data]
        prompt = slide.prompt_text

        # 当本页带有种子参考图时，在 prompt 末尾追加一行明确告知模型，
        # 让它把同家族种子页当作版式锚点 — 不复制内容、只复用视觉系统。
        seed_count = sum(1 for r in first_pass_ref_data if r.get("role") == "seed_ref")
        if seed_count > 0:
            seed_instruction = (
                f"\n\nIMPORTANT — SAME-FAMILY LAYOUT REFERENCE: "
                f"{seed_count} of the attached reference images are previously generated slides from the same page family in this deck. "
                f"They are LAYOUT ANCHORS only: reuse their typography choices, color palette, ornament language, grid system, and hierarchy. "
                f"DO NOT copy any of their text content, headlines, body, photographs, illustrations, or scene subjects. "
                f"DO NOT copy a logo from the seed unless this slide also has the uploaded logo attached as its own reference. "
                f"Render this slide's own text and visual evidence in the SAME design DNA so the deck feels visually consistent."
            )
            prompt = prompt + seed_instruction

        if use_product_refinement:
            prompt = _background_pass_prompt(prompt, product_refs)

        img = generate_slide_image(
            prompt=prompt,
            reference_images=ref_images if ref_images else None,
            resolution="4K",
            aspect_ratio="16:9",
        )

        if use_product_refinement:
            base_path = save_slide_image(
                img=img,
                project_id=project_id,
                page_num=slide.page_num,
                output_dir=output_dir,
                suffix="_base",
            )
            refinement_images = [img] + [ref["image"] for ref in product_refs]
            refinement_paths = [
                str(ref.get("file_path") or "").strip()
                for ref in product_refs
                if str(ref.get("file_path") or "").strip()
            ]
            if refinement_paths:
                logger.info(
                    f"Pipeline: 第 {slide.page_num} 页产品二次生成参考素材路径: "
                    + " | ".join(refinement_paths)
                )
            img = generate_slide_image(
                prompt=_product_refinement_prompt(slide, product_refs),
                reference_images=refinement_images[:MAX_REFERENCE_INPUTS],
                resolution="4K",
                aspect_ratio="16:9",
            )
            logger.info(
                f"Pipeline: 第 {slide.page_num} 页完成产品二次生成 "
                f"(base={base_path}, product_refs={len(product_refs)})"
            )

        image_path = save_slide_image(
            img=img,
            project_id=project_id,
            page_num=slide.page_num,
            output_dir=output_dir,
        )
        logger.info(f"Pipeline: 第 {slide.page_num} 页生成完成"
                    + (f" (使用 {seed_count} 张同家族种子参考)" if seed_count else "")
                    + (f" (产品二次生成 {len(product_refs)} 张参考)" if use_product_refinement else ""))
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

    def current_run_active() -> bool:
        db.expire_all()
        return is_run_active(db, run_id)

    if not current_run_active():
        logger.info(f"Pipeline: run {run_id} is no longer active before generation; skipping")
        cleanup_generation_progress(project_id)
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

    def record_generation_result(result: Dict) -> None:
        if not current_run_active():
            logger.info(f"Pipeline: run {run_id} is no longer active; skipping stale slide writeback")
            return
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
                    if k not in {
                        "finetune_base_image_path",
                        "finetune_instruction",
                        "finetune_attachment_ids",
                        "finetune_visual_asset_ids",
                    }
                }
                flag_modified(slide, "visual_json")
            speaker_notes = ""
            if slide.content_json and isinstance(slide.content_json, dict):
                speaker_notes = slide.content_json.get("speaker_notes", "")
            slide_images.append({
                "page_num": slide.page_num,
                "image_path": result["image_path"],
                "speaker_notes": speaker_notes,
                "type": slide.type or "content",
                "visual_json": slide.visual_json or {},
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

    slides_with_prompt = [s for s in target_slides if s.prompt_text]
    if slides_with_prompt:
        # 两阶段生成：先生成同家族的种子页，再以种子图为版式锚点生成其它页。
        # 已经完成的页（finetune/重生成场景）直接进入 existing seeds 池，
        # 不重新生成；finetune 模式的页面跳过种子参考。
        seeds_by_family = _collect_existing_seeds(slides)

        # Stage 1：识别需要先行生成的「家族种子页」。
        # 条件：1) 是 is_seed_recommended 推荐种子页 OR 同家族目前还没有任何已完成页；
        #      2) 该页是 target_slides 的一部分；
        #      3) 不是 finetune 模式（finetune 优先底图，不参与种子家族）。
        target_families = {
            _slide_family(s) for s in slides_with_prompt if _slide_family(s)
        }

        seed_pages: List[Slide] = []
        non_seed_pages: List[Slide] = []
        for slide in slides_with_prompt:
            if _is_finetune_slide(slide):
                non_seed_pages.append(slide)
                continue
            family = _slide_family(slide)
            if not family:
                non_seed_pages.append(slide)
                continue
            if family in seeds_by_family:
                non_seed_pages.append(slide)
                continue
            # 此家族还没有任何已完成图片：让推荐种子优先生成；
            # 若推荐种子不在本批次，就把当前批次中该家族最早的页当种子。
            is_recommended = bool((slide.visual_json or {}).get("is_seed_recommended")) if isinstance(slide.visual_json, dict) else False
            if is_recommended:
                seed_pages.append(slide)
            else:
                non_seed_pages.append(slide)

        # 兜底：家族在 target 中但还没种子也没有推荐种子 (例如批次只生成 page 5、不含 page 3)，
        # 取该家族在 slides_with_prompt 中最早的页作种子，提升 Stage 1。
        seed_target_families = {_slide_family(s) for s in seed_pages if _slide_family(s)}
        for family in target_families:
            if family in seeds_by_family or family in seed_target_families:
                continue
            family_pages = [s for s in non_seed_pages if _slide_family(s) == family]
            if not family_pages:
                continue
            family_pages.sort(key=lambda s: s.page_num)
            promoted = family_pages[0]
            non_seed_pages = [s for s in non_seed_pages if s.id != promoted.id]
            seed_pages.append(promoted)
            seed_target_families.add(family)
            logger.info(
                f"Pipeline: 家族 {family} 没有推荐种子或现有种子，临时提升 page {promoted.page_num} 作种子"
            )

        if seed_pages:
            logger.info(
                f"Pipeline: 两阶段生成 — Stage 1 种子页 {len(seed_pages)} 张，"
                f"Stage 2 非种子页 {len(non_seed_pages)} 张；已有家族种子 {list(seeds_by_family.keys())}"
            )
        else:
            logger.info(
                f"Pipeline: 单阶段生成 — 所有 {len(non_seed_pages)} 页均使用现有种子或无种子家族；"
                f"已有家族种子 {list(seeds_by_family.keys())}"
            )

        # Stage 1：生成种子页（不带种子参考图，因为它们本身就是种子）
        if seed_pages:
            ref_data_by_slide = {s.id: _load_reference_images(s, seed_image_paths=None) for s in seed_pages}
            max_workers = min(len(seed_pages), 3)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_slide = {
                    executor.submit(_generate_one_slide, slide, project_id, output_dir, ref_data_by_slide.get(slide.id)): slide
                    for slide in seed_pages
                }
                for future in as_completed(future_to_slide):
                    result = future.result()
                    record_generation_result(result)
                    # 把成功生成的种子页加入 seeds_by_family，供 Stage 2 使用
                    if not result.get("error"):
                        slide = result["slide"]
                        family = _slide_family(slide)
                        if family and result.get("image_path"):
                            seeds_by_family.setdefault(family, []).append(result["image_path"])

        # Stage 2：生成非种子页，按家族注入种子参考图
        if non_seed_pages:
            ref_data_by_slide = {}
            for s in non_seed_pages:
                family = _slide_family(s)
                seed_paths = seeds_by_family.get(family, []) if family else []
                # finetune 页面不使用种子参考（底图优先）
                if _is_finetune_slide(s):
                    seed_paths = []
                ref_data_by_slide[s.id] = _load_reference_images(s, seed_image_paths=seed_paths)
            max_workers = min(len(non_seed_pages), 3)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_slide = {
                    executor.submit(_generate_one_slide, slide, project_id, output_dir, ref_data_by_slide.get(slide.id)): slide
                    for slide in non_seed_pages
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
                "type": s.type or "content",
                "visual_json": s.visual_json or {},
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

                logo_ref = next(
                    (
                        ref for ref in project.reference_images or []
                        if ref.role == "logo" and not ref.slide_id and os.path.exists(ref.file_path)
                    ),
                    None,
                )
                logo_config = (
                    {
                        "file_path": logo_ref.file_path,
                        "anchor": getattr(logo_ref, "logo_anchor", None) or "top-right",
                    }
                    if logo_ref else None
                )
                assemble_pptx(
                    slide_images=all_completed_images,
                    output_path=pptx_path,
                    logo_config=logo_config,
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

    if not current_run_active():
        logger.info(f"Pipeline: run {run_id} is no longer active before final writeback; skipping")
        cleanup_generation_progress(project_id)
        return

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
