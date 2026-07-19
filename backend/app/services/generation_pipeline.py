import logging
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

import redis
from PIL import Image, ImageDraw, ImageFilter, ImageOps
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import Project, Slide, SlideVersion
from app.services.artifact_versions import has_stale_flags, with_stale_flags
from app.services.image_generation import (
    clear_reference_upload_cache,
    generate_slide_image,
    get_image_call_events,
    reset_image_call_events,
    reset_run_image_counter,
    save_slide_image,
)
from app.services.image_task_audit import append_image_generation_log, image_generation_log_path
from app.services.logo_assets import prepare_logo_lockup_image, prepare_logo_overlay_image, prepare_logo_symbol_image
from app.services.logo_overlay_layout import resolve_logo_render_policy
from app.services.logo_policy import is_logo_confirmed, logo_policy_for_page, should_show_logo, should_use_logo_as_scene_asset
from app.services.overlay_layers import enabled_overlay_layers, exact_overlay_asset_ids
from app.services.pipeline_diagnostics import append_pipeline_diagnostic_log
from app.services.pptx_assembler import assemble_pptx
from app.services.run_state import (
    cleanup_generation_progress,
    finish_run,
    get_run,
    image_generation_progress_message,
    image_generation_run_stage,
    image_generation_running_message,
    is_run_active,
    mark_run_running,
    update_run_progress,
)
from app.services.slide_types import normalize_slide_type
from app.utils.reference_image import default_visual_asset_process_mode

logger = logging.getLogger(__name__)


def _resolve_file_path(file_path: str) -> str:
    """安全解析文件路径，兼容从不同工作目录启动的情况。
    数据库中的路径可能是相对路径（如 ./uploads/...），而服务可能在
    任意工作目录下运行，因此需要基于代码位置来解析。"""
    if not file_path:
        return file_path
    if os.path.exists(file_path):
        return file_path
    # 基于当前文件位置推断项目根目录（app/services/ -> backend/）
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    candidate = os.path.join(backend_dir, file_path)
    if os.path.exists(candidate):
        return candidate
    # 最后尝试标准 abspath（依赖当前工作目录）
    abs_path = os.path.abspath(file_path)
    if os.path.exists(abs_path):
        return abs_path
    return file_path


redis_client = redis.from_url(
    settings.REDIS_URL or "redis://localhost:6379/0",
    socket_connect_timeout=settings.REDIS_SOCKET_TIMEOUT_SECONDS,
    socket_timeout=settings.REDIS_SOCKET_TIMEOUT_SECONDS,
    retry_on_timeout=True,
    health_check_interval=30,
)
MAX_REFERENCE_INPUTS = max(1, min(14, int(settings.IMAGE_MAX_REFERENCE_INPUTS or 14)))
MAX_VERSIONS_PER_SLIDE = 10


def _pipeline_image_worker_count() -> int:
    try:
        return max(1, min(3, int(settings.IMAGE_API_MAX_CONCURRENCY or 1)))
    except (TypeError, ValueError):
        return 1


def _slide_needs_placement_safety(slide_data: Dict, *, logo_available: bool) -> bool:
    """精准粘贴或 Logo 会在生成图之后落版的页面，都必须检测文字区域。"""
    return bool(
        enabled_overlay_layers(slide_data.get("visual_json"))
        or (logo_available and should_show_logo(slide_data))
    )


def _result_has_gateway_timeout(result: Dict) -> bool:
    events = result.get("image_generation_events") or []
    if any((event or {}).get("status") == "gateway_timeout" for event in events):
        return True
    error = str(result.get("error") or "")
    return "上游连接窗口截断" in error or "gateway" in error.lower()


def _prompt_audit(prompt: str | None) -> Dict:
    text = prompt or ""
    import hashlib

    return {
        "prompt_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None,
        "prompt_length": len(text),
        "prompt": text,
    }


def _reference_audit(refs: Optional[List[Dict]]) -> List[Dict]:
    summary = []
    for index, ref in enumerate(refs or [], start=1):
        img = ref.get("image")
        summary.append({
            "index": index,
            "role": ref.get("role"),
            "label": ref.get("label"),
            "process_mode": ref.get("process_mode"),
            "asset_route_mode": ref.get("asset_route_mode"),
            "asset_name": ref.get("asset_name"),
            "asset_kind": ref.get("asset_kind"),
            "file_path": ref.get("file_path") or getattr(img, "info", {}).get("pptgod_reference_source_path"),
            "image_size": list(getattr(img, "size", []) or []),
            "image_mode": getattr(img, "mode", None),
            "template_reference_mode": ref.get("template_reference_mode") or getattr(img, "info", {}).get("pptgod_template_reference_mode"),
            "template_application_strength": ref.get("application_strength") or getattr(img, "info", {}).get("pptgod_template_application_strength"),
            "source_mtime_ns": getattr(img, "info", {}).get("pptgod_reference_source_mtime_ns"),
            "source_size_bytes": getattr(img, "info", {}).get("pptgod_reference_source_size"),
            "reference_binding": ref.get("reference_binding"),
        })
    return summary


def _archive_current_image(slide: Slide, db: Session) -> None:
    if not slide.image_path or not os.path.exists(slide.image_path):
        return
    visual = slide.visual_json if isinstance(slide.visual_json, dict) else {}
    if visual.get("finetune_base_image_path"):
        return

    max_ver = (
        db.query(SlideVersion)
        .filter(SlideVersion.slide_id == slide.id)
        .order_by(SlideVersion.version_number.desc())
        .first()
    )
    next_ver = (max_ver.version_number + 1) if max_ver else 1
    version_dir = os.path.join(settings.OUTPUT_DIR or "./outputs", slide.project_id, "versions")
    os.makedirs(version_dir, exist_ok=True)
    version_path = os.path.join(version_dir, f"slide_{slide.page_num:02d}_v{next_ver}.png")
    shutil.copy2(slide.image_path, version_path)
    db.add(
        SlideVersion(
            slide_id=slide.id,
            project_id=slide.project_id,
            image_path=version_path,
            prompt_text=slide.prompt_text,
            version_number=next_ver,
        )
    )

    all_versions = (
        db.query(SlideVersion)
        .filter(SlideVersion.slide_id == slide.id)
        .order_by(SlideVersion.version_number.asc())
        .all()
    )
    if len(all_versions) > MAX_VERSIONS_PER_SLIDE:
        for version in all_versions[:len(all_versions) - MAX_VERSIONS_PER_SLIDE]:
            if version.image_path != slide.image_path and version.image_path != version_path and os.path.exists(version.image_path):
                try:
                    os.remove(version.image_path)
                except OSError:
                    pass
            db.delete(version)


def _existing_path(path: str | None, output_dir: str | None = None) -> str | None:
    if not path:
        return None
    if os.path.exists(path):
        return path
    if path.startswith("./outputs") and output_dir:
        candidate = os.path.join(os.path.dirname(os.path.abspath(output_dir)), path[2:])
        if os.path.exists(candidate):
            return candidate
    return path


def _tag_reference_image(img: Image.Image, role: str, source_path: str | None = None) -> Image.Image:
    tagged = img.copy()
    tagged.info["pptgod_reference_role"] = role
    if source_path:
        tagged.info["pptgod_reference_source_path"] = source_path
        try:
            stat = os.stat(source_path)
            tagged.info["pptgod_reference_source_mtime_ns"] = stat.st_mtime_ns
            tagged.info["pptgod_reference_source_size"] = stat.st_size
        except OSError:
            pass
    return tagged


def _open_reference_image(path: str, role: str) -> Image.Image:
    with Image.open(path) as img:
        return _tag_reference_image(img, role or "reference", path)


def _normalize_template_application_strength(strength: str | None) -> str:
    value = str(strength or "standard").strip().lower()
    return value if value in {"light", "standard", "strong"} else "standard"


def _template_reference_mode(strength: str | None) -> str:
    normalized = _normalize_template_application_strength(strength)
    if normalized == "strong":
        return "direct_page"
    if normalized == "light":
        return "layout_map"
    return "layout_color_map"


def _blur_radius_for_template_map(img: Image.Image) -> float:
    return max(1.0, min(img.size) / 45)


def _open_template_reference_image(path: str, strength: str | None) -> Image.Image:
    normalized = _normalize_template_application_strength(strength)
    mode = _template_reference_mode(normalized)
    if normalized == "strong":
        tagged = _open_reference_image(path, "template")
    else:
        with Image.open(path) as source:
            img = source.convert("RGB")
            img = img.filter(ImageFilter.GaussianBlur(radius=_blur_radius_for_template_map(img)))
            if normalized == "light":
                img = ImageOps.grayscale(img).convert("RGB")
            else:
                img = img.quantize(colors=16).convert("RGB")
            tagged = _tag_reference_image(img, "template", path)
    tagged.info["pptgod_template_application_strength"] = normalized
    tagged.info["pptgod_template_reference_mode"] = mode
    return tagged


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
    if role in {"seed_ref", "seed_ref_hint"}:
        return 4
    if role in {"template", "template_hint"}:
        return 5
    return 9


def _is_parallel_page_reference_analysis(analysis) -> bool:
    return isinstance(analysis, dict) and analysis.get("asset_group_role") == "parallel_page_reference_set"


def _effective_slide_reference_process_mode(ref) -> str:
    mode = str(getattr(ref, "process_mode", None) or "blend").lower()
    if (
        getattr(ref, "role", None) in {"content_ref", "chart_ref"}
        and mode == "blend"
        and _is_parallel_page_reference_analysis(getattr(ref, "asset_analysis", None))
    ):
        return "crop"
    return mode


def _analysis_bbox(analysis: dict | None) -> tuple[float, float, float, float] | None:
    if not isinstance(analysis, dict):
        return None
    bbox = analysis.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        try:
            left, top, right, bottom = [float(value) for value in bbox]
            return left, top, right, bottom
        except (TypeError, ValueError):
            return None
    bounds = analysis.get("shape_bounds") if isinstance(analysis.get("shape_bounds"), dict) else {}
    try:
        left = float(bounds.get("left"))
        top = float(bounds.get("top"))
        width = float(bounds.get("width"))
        height = float(bounds.get("height"))
    except (TypeError, ValueError):
        return None
    return left, top, left + width, top + height


def _slide_reference_input_sort_key(ref, original_index: int) -> tuple:
    analysis = getattr(ref, "asset_analysis", None) if hasattr(ref, "asset_analysis") else None
    analysis = analysis if isinstance(analysis, dict) else {}
    bbox = _analysis_bbox(analysis)
    if bbox:
        left, top, _right, _bottom = bbox
    else:
        left, top = 0.0, 0.0
    try:
        group_index = int(analysis.get("asset_group_index") or 9999)
    except (TypeError, ValueError):
        group_index = 9999
    role_order = {
        "content_ref": 0,
        "chart_ref": 1,
        "visual_asset": 2,
        "style_ref": 3,
        "template": 4,
        "logo": 5,
        "finetune_ref": 6,
    }.get(getattr(ref, "role", None) or "", 9)
    try:
        source_page = int(analysis.get("source_page_num") or analysis.get("pdf_source_page_num") or 10_000)
    except (TypeError, ValueError):
        source_page = 10_000
    return (
        role_order,
        str(analysis.get("source_document") or ""),
        source_page,
        str(analysis.get("asset_group_key") or ""),
        group_index,
        top,
        left,
        original_index,
    )


def _slide_team_member_names(slide: Slide) -> list[str]:
    content = slide.content_json if isinstance(slide.content_json, dict) else {}
    text = content.get("text_content") if isinstance(content.get("text_content"), dict) else {}
    joined = " ".join(
        str(value or "")
        for value in (
            text.get("headline"),
            text.get("subhead"),
            text.get("body"),
            content.get("page_map_markdown"),
        )
    )
    if not any(term in joined for term in ("团队", "成员", "创始人", "负责人", "核心团队", "team", "Team")):
        return []

    body = text.get("body")
    if isinstance(body, list):
        lines = [str(item or "") for item in body]
    else:
        lines = str(body or "").splitlines()

    names: list[str] = []
    for line in lines:
        cleaned = re.sub(r"^\s*[-*•·\d.、）)]+\s*", "", str(line or "").strip())
        if not cleaned:
            continue
        match = re.match(r"^([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z·.\-]{1,18})(?:\s|：|:|，|,|。)", cleaned)
        if not match:
            continue
        name = match.group(1).strip()
        if name and name not in names:
            names.append(name)
    return names


def _reference_bbox(ref: Dict) -> tuple[float, float, float, float] | None:
    analysis = ref.get("asset_analysis") if isinstance(ref.get("asset_analysis"), dict) else {}
    return _analysis_bbox(analysis)


def _binding_position_label(
    center_x: float,
    center_y: float,
    *,
    min_x: float,
    max_x: float,
    min_y: float,
    max_y: float,
) -> str:
    mid_x = (min_x + max_x) / 2.0
    mid_y = (min_y + max_y) / 2.0
    horizontal = "left" if center_x <= mid_x else "right"
    vertical = "top" if center_y <= mid_y else "bottom"
    return f"{vertical}-{horizontal}"


def _attach_page_reference_identity_bindings(slide: Slide, refs: list[Dict]) -> None:
    """Bind multi-portrait page refs to the matching text item and source position.

    Image models see a flat list of reference images. For team pages, that is not enough:
    four similar portrait crops can be blended, omitted, or swapped. The source PDF already
    gives us the order through each crop bbox, and the slide text gives us the names.
    """
    page_refs = [
        ref for ref in refs
        if ref.get("role") in {"content_ref", "chart_ref"} and ref.get("image") is not None
    ]
    if len(page_refs) < 2:
        return
    names = _slide_team_member_names(slide)
    if len(names) != len(page_refs):
        return

    positioned: list[tuple[Dict, tuple[float, float, float, float], float, float]] = []
    for ref in page_refs:
        bbox = _reference_bbox(ref)
        if not bbox:
            return
        left, top, right, bottom = bbox
        positioned.append((ref, bbox, (left + right) / 2.0, (top + bottom) / 2.0))
    if len(positioned) != len(names):
        return

    centers_x = [item[2] for item in positioned]
    centers_y = [item[3] for item in positioned]
    min_x, max_x = min(centers_x), max(centers_x)
    min_y, max_y = min(centers_y), max(centers_y)
    # Source decks are normally read row-major for cards: top-left, top-right,
    # then lower rows. Use source bbox order rather than upload/file order.
    for name, (ref, bbox, center_x, center_y) in zip(names, sorted(positioned, key=lambda item: (item[3], item[2]))):
        ref["reference_binding"] = {
            "name": name,
            "position": _binding_position_label(
                center_x,
                center_y,
                min_x=min_x,
                max_x=max_x,
                min_y=min_y,
                max_y=max_y,
            ),
            "source_bbox": list(bbox),
        }


def _reference_binding_lines(refs: list[Dict], *, label_start_index: int = 1) -> list[str]:
    lines: list[str] = []
    for offset, ref in enumerate(refs):
        binding = ref.get("reference_binding") if isinstance(ref.get("reference_binding"), dict) else None
        if not binding:
            continue
        name = str(binding.get("name") or "").strip()
        position = str(binding.get("position") or "").strip()
        if not name:
            continue
        label = str(ref.get("label") or f"Reference Image {label_start_index + offset}").strip()
        suffix = f" ({position})" if position else ""
        lines.append(f"{label} -> {name}{suffix}")
    return lines


def _page_reference_fidelity_instruction(ref_data: List[Dict]) -> str:
    blend_page_refs = [
        ref for ref in ref_data
        if ref.get("role") in {"content_ref", "chart_ref"}
        and str(ref.get("process_mode") or "").lower() == "blend"
    ]
    page_refs = [
        ref for ref in ref_data
        if ref.get("role") in {"content_ref", "chart_ref"}
        and str(ref.get("process_mode") or "").lower() in {"crop", "original"}
    ]
    parts: list[str] = []
    if blend_page_refs:
        labels = [
            str(ref.get("label") or f"Reference Image {index}")
            for index, ref in enumerate(blend_page_refs, start=1)
        ]
        label_text = ", ".join(labels[:6])
        parts.append(
            f"\n\nPAGE REFERENCE COVERAGE: {label_text} are page-level visual evidence, not generic mood boards. "
            "Keep a recognizable visual trace from the attached reference material in the final slide. "
            "Use every attached page reference; when multiple references are provided, do not base the slide on only one."
        )
        binding_lines = _reference_binding_lines(blend_page_refs)
        if binding_lines:
            parts.append(
                "\nPAGE REFERENCE BINDINGS: "
                + "; ".join(binding_lines)
                + ". Do not swap identities, positions, or name-to-image relationships."
            )
    if page_refs:
        labels = [
            str(ref.get("label") or f"Reference Image {index}")
            for index, ref in enumerate(page_refs, start=1)
        ]
        label_text = ", ".join(labels[:6])
        parts.append(
            f"\n\nPAGE REFERENCE FIDELITY: {label_text} are source visuals, not style mood boards. "
            "Preserve their depicted people, objects, layout relationships, and recognizable scene evidence. "
            "Do not replace these references with invented people, new scenes, or unrelated stock imagery. "
            "When multiple source visuals are attached, account for every one; do not omit all but a single reference."
        )
        binding_lines = _reference_binding_lines(page_refs)
        if binding_lines:
            parts.append(
                "\nPAGE REFERENCE BINDINGS: "
                + "; ".join(binding_lines)
                + ". Do not swap identities, positions, or name-to-image relationships."
            )
    return "".join(parts)


def _template_reference_instruction(ref_data: List[Dict]) -> str:
    template_refs = [ref for ref in ref_data if ref.get("role") == "template"]
    if not template_refs:
        return ""
    modes = {
        str(ref.get("template_reference_mode") or _template_reference_mode(ref.get("application_strength"))).lower()
        for ref in template_refs
    }
    if "direct_page" in modes:
        return (
            "\n\nTEMPLATE DIRECT PAGE REFERENCE: Attached template page(s) are strong visual anchors. "
            "Stay close to the attached template page in layout, spacing, hierarchy, palette, typography rhythm, "
            "material texture, and ornament language. Replace the old template content with this slide's own text. "
            "Do not copy old template text, images, products, logos, portraits, people, or scene subjects."
        )
    if "layout_color_map" in modes:
        return (
            "\n\nTEMPLATE LAYOUT AND COLOR MAP: Attached template image(s) are simplified maps, not source content. "
            "Reuse grid, spacing, hierarchy, and palette relationship from the map. "
            "Keep this slide's own subject, evidence, and visible text. "
            "Do not copy old template text, images, products, logos, portraits, people, or scene subjects."
        )
    return (
        "\n\nTEMPLATE LAYOUT MAP: Attached template image(s) are desaturated layout maps, not color references. "
        "Reuse grid, spacing, hierarchy, text/image zones, card positions, and alignment only. "
        "Do not reuse the template colors, old text, images, products, logos, portraits, people, or scene subjects; "
        "choose colors from the current selected style and this slide's content."
    )


def _slide_text_content(slide: Slide) -> Dict:
    content = slide.content_json if isinstance(slide.content_json, dict) else {}
    text = content.get("text_content")
    return text if isinstance(text, dict) else {}


def _uses_seed_base_edit_contract(slide: Slide) -> bool:
    return (slide.type or "").lower() == "section"


def _seed_reference_limit(slide: Slide) -> int:
    return 1 if _uses_seed_base_edit_contract(slide) else 2


def _split_compact_section_headline(title: str) -> tuple[str, str] | None:
    value = str(title or "").strip()
    if not value:
        return None
    for marker in ("正在", "行动清单", "如何", "怎么", "怎样"):
        idx = value.find(marker)
        if 1 < idx < len(value) - 1:
            return value[:idx].strip(), value[idx:].strip()
    compact = re.sub(r"\s+", "", value)
    if (
        len(compact) >= 8
        and len(compact) % 2 == 0
        and re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9]+", compact)
    ):
        midpoint = len(compact) // 2
        return compact[:midpoint], compact[midpoint:]
    return None


def _section_seed_title_structure_instruction(headline: str, subhead: str) -> str:
    title = str(headline or "").strip()
    secondary = str(subhead or "").strip()
    if not title:
        return ""

    question_break = None
    for marker in ("？", "?"):
        idx = title.find(marker)
        if idx >= 0:
            question_break = idx + len(marker)
            break
    if question_break:
        kicker = title[:question_break].strip()
        main = title[question_break:].strip()
        if main:
            return (
                " Title hierarchy: preserve the seed slide's two-tier title block exactly: "
                f"render 「{kicker}」 as the smaller white top line, then render 「{main}」 as "
                "the larger champagne-gold main statement below it, wrapping only within the "
                "same left title block used by the seed."
            )

    if secondary:
        return (
            " Title hierarchy: preserve the seed slide's two-tier title block exactly: "
            f"render headline 「{title}」 as the smaller white top line and subhead 「{secondary}」 "
            "as the larger champagne-gold main statement below it, wrapping only within the "
            "same left title block used by the seed."
        )

    compact_split = _split_compact_section_headline(title)
    if compact_split:
        kicker, main = compact_split
        return (
            " Title hierarchy: preserve the seed slide's two-tier title block exactly: "
            f"render 「{kicker}」 as the smaller white top line, then render 「{main}」 as "
            "the larger champagne-gold main statement below it, wrapping only within the "
            "same left title block used by the seed."
        )

    return (
        " Title hierarchy: preserve the seed slide's left title block position, baseline, "
        "divider line, font scale, and color hierarchy. Use the seed's white/gold text treatment; "
        "when the headline needs two lines, keep the first line smaller white and the second line "
        "larger champagne-gold inside the same title area."
    )


def _seed_base_edit_instruction(slide: Slide, seed_image_count: int) -> str:
    if seed_image_count <= 0 or not _uses_seed_base_edit_contract(slide):
        return ""

    text = _slide_text_content(slide)
    headline = str(text.get("headline") or "").strip()
    subhead = str(text.get("subhead") or "").strip()
    title_structure_instruction = _section_seed_title_structure_instruction(headline, subhead)

    targets = []
    if headline:
        targets.append(f"headline 「{headline}」")
    if subhead:
        targets.append(f"subhead 「{subhead}」")
    target_text = "; ".join(targets) or "this slide's own section text"

    return (
        "\n\nDIRECT SEED IMAGE EDIT CONTRACT: Use Reference Image 1 as the base slide image. "
        "Keep its background, texture, ornament placement, left-right composition, typography scale, "
        "spacing, alignment, title-block geometry, line breaks, and color hierarchy as unchanged as possible. "
        "Only update the visible section headline/subhead text to: "
        f"{target_text}. Remove old seed section text, standalone chapter-number badges, and old module "
        "markers/titles. Do not render structural section metadata such as 第X章, Chapter X, module markers, "
        "standalone chapter-number badges, module titles, or Arabic numerals unless they appear inside the "
        "quoted target text. Ignore any earlier layout or composition wording that asks for numeric chapter "
        f"transitions or standalone chapter markers.{title_structure_instruction} This contract overrides any earlier layout or composition "
        "wording that conflicts with the base image."
    )


def _load_reference_images(
    slide: Slide,
    seed_image_paths: Optional[List[str]] = None,
    *,
    audit_project_id: str | None = None,
    audit_run_id: str | None = None,
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
    visual = slide.visual_json if isinstance(slide.visual_json, dict) else {}
    overlay_asset_ids = exact_overlay_asset_ids(visual)
    if overlay_asset_ids:
        logger.info(
            "Slide %s: overlay_asset_ids=%s (来自 visual_json.overlay_layers)",
            slide.page_num,
            overlay_asset_ids,
        )
    asset_route_modes = {}
    raw_route_modes = visual.get("asset_route_modes") or {}
    if isinstance(raw_route_modes, dict):
        asset_route_modes = {str(k): str(v).lower() for k, v in raw_route_modes.items() if k and v}

    # 0. 单页微调时，当前页历史图片必须排第一，图像编辑模型以它为底图。
    finetune_base_path = None
    if visual:
        finetune_base_path = visual.get("finetune_base_image_path")
    if finetune_base_path:
        if os.path.exists(finetune_base_path):
            try:
                refs.append({
                    "image": _open_reference_image(finetune_base_path, "finetune_base"),
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

    finetune_regions = _finetune_regions_for_slide(slide)
    if finetune_base_path and refs and finetune_regions:
        guide = _finetune_region_guide_image(refs[0]["image"], finetune_regions)
        if guide:
            guide.info["pptgod_reference_role"] = "finetune_region_guide"
            refs.append({
                "image": guide,
                "process_mode": "original",
                "role": "finetune_region_guide",
                "label": "Selected Region Visual Guide",
            })
            logger.info(f"Slide {slide.page_num}: 已添加微调框选区域视觉标注图，共 {len(finetune_regions)} 个区域")

    finetune_visual_asset_ids = []
    if finetune_base_path and slide.visual_json and isinstance(slide.visual_json, dict):
        raw_asset_ids = slide.visual_json.get("finetune_visual_asset_ids") or []
        if isinstance(raw_asset_ids, list):
            finetune_visual_asset_ids = [str(x) for x in raw_asset_ids][:3]

    # 1. 页面级参考图优先：Prompt 中的 Reference Image 1/2/3 必须与
    # 生图 API 的图片输入顺序一致，便于模型按用户意图使用这些图。
    already_loaded_paths = set()
    skipped_overlay = 0
    skipped_missing = 0
    skipped_duplicate = 0
    skipped_finetune = 0
    loaded_count = 0
    if slide.reference_images:
        finetune_attachment_ids = set()
        if visual:
            finetune_attachment_ids = set(visual.get("finetune_attachment_ids") or [])
        logger.info(
            "Slide %s: 开始加载页面参考图，共 %s 张，overlay_asset_ids=%s",
            slide.page_num,
            len(slide.reference_images),
            overlay_asset_ids,
        )
        ordered_slide_refs = [
            ref for _original_index, ref in sorted(
                enumerate(slide.reference_images or [], start=1),
                key=lambda item: _slide_reference_input_sort_key(item[1], item[0]),
            )
        ]
        for idx, ref in enumerate(ordered_slide_refs, start=1):
            if str(getattr(ref, "id", "") or "") in overlay_asset_ids:
                skipped_overlay += 1
                logger.info(f"Slide {slide.page_num}: 页面参考图 {ref.id} 走精确粘贴，跳过生图参考输入")
                continue
            if finetune_base_path:
                # In direct edit mode, keep the image context tight: current slide
                # plus only the images uploaded for this chat turn. Long-lived page
                # refs can contain people or products that make "this person/image"
                # ambiguous for the image model.
                if ref.role != "finetune_ref" or str(ref.id) not in finetune_attachment_ids:
                    skipped_finetune += 1
                    continue
            elif ref.role == "finetune_ref":
                skipped_finetune += 1
                continue
            resolved_path = _resolve_file_path(ref.file_path)
            if resolved_path in already_loaded_paths:
                skipped_duplicate += 1
                logger.info(f"Slide {slide.page_num}: 跳过重复页面参考图 {resolved_path}")
                continue
            if os.path.exists(resolved_path):
                try:
                    process_mode = _effective_slide_reference_process_mode(ref)
                    refs.append({
                        "image": _open_reference_image(resolved_path, ref.role),
                        "process_mode": process_mode,
                        "role": ref.role,
                        "label": f"Reference Image {idx}",
                        "file_path": resolved_path,
                        "id": getattr(ref, "id", None),
                        "asset_name": getattr(ref, "asset_name", None),
                        "asset_kind": getattr(ref, "asset_kind", None),
                        "usage_note": getattr(ref, "usage_note", None),
                        "asset_analysis": getattr(ref, "asset_analysis", None),
                        "asset_route_mode": "double_blend" if process_mode == "crop" else "blend",
                    })
                    already_loaded_paths.add(resolved_path)
                    loaded_count += 1
                except Exception as e:
                    logger.warning(f"无法加载页面参考图 {resolved_path}: {e}")
            else:
                skipped_missing += 1
                logger.warning(f"页面参考图文件不存在: {ref.file_path} (解析后: {resolved_path})")
        logger.info(
            "Slide %s: 页面参考图加载完成: 加载=%s, 跳过(精确粘贴)=%s, 跳过(微调过滤)=%s, 跳过(重复)=%s, 不存在=%s",
            slide.page_num,
            loaded_count,
            skipped_overlay,
            skipped_finetune,
            skipped_duplicate,
            skipped_missing,
        )

    if finetune_base_path and finetune_visual_asset_ids and slide.project and slide.project.reference_images:
        selected_set = set(finetune_visual_asset_ids)
        project_assets = [
            ref for ref in slide.project.reference_images
            if ref.role == "visual_asset" and str(ref.id) in selected_set and not ref.slide_id
        ]
        project_assets.sort(
            key=lambda ref: finetune_visual_asset_ids.index(str(ref.id))
            if str(ref.id) in finetune_visual_asset_ids else 999
        )
        for idx, ref in enumerate(project_assets, start=1):
            if len(refs) >= MAX_REFERENCE_INPUTS:
                break
            resolved = _resolve_file_path(ref.file_path)
            if os.path.exists(resolved):
                try:
                    refs.append({
                        "image": _open_reference_image(resolved, ref.role),
                        "process_mode": ref.process_mode or default_visual_asset_process_mode(getattr(ref, "asset_kind", None)),
                        "role": ref.role,
                        "label": f"Protected Project Visual Asset {idx}",
                        "file_path": resolved,
                        "id": getattr(ref, "id", None),
                        "asset_name": getattr(ref, "asset_name", None),
                        "asset_kind": getattr(ref, "asset_kind", None),
                        "usage_note": getattr(ref, "usage_note", None),
                        "asset_analysis": getattr(ref, "asset_analysis", None),
                    })
                    logger.info(f"Slide {slide.page_num}: 微调模式加载项目视觉资产 {resolved}")
                except Exception as e:
                    logger.warning(f"无法加载微调项目视觉资产 {resolved}: {e}")
            else:
                logger.warning(f"微调项目视觉资产文件不存在: {ref.file_path} (解析后: {resolved})")

    if finetune_base_path:
        logger.info(f"Slide {slide.page_num}: 微调模式加载底图、本轮附件和项目资产，共 {len(refs)} 张参考图")
        return refs[:MAX_REFERENCE_INPUTS]

    # 2. 项目级 Logo：默认不进生图，后续用程序 overlay。
    # 只有用户把 Logo 设为 blend 且页面适合做场景标识时，才作为画面元素参考。
    if slide.project and slide.project.reference_images and len(refs) < MAX_REFERENCE_INPUTS:
        logo_refs = [
            ref for ref in slide.project.reference_images
            if (
                ref.role == "logo"
                and is_logo_confirmed(ref)
                and not ref.slide_id
                and os.path.exists(_resolve_file_path(ref.file_path))
            )
        ]
        if logo_refs and any(should_use_logo_as_scene_asset(slide, ref) for ref in logo_refs):
            try:
                resolved_logo_paths = [_resolve_file_path(ref.file_path) for ref in logo_refs]
                logo_path = prepare_logo_lockup_image(resolved_logo_paths) or prepare_logo_overlay_image(resolved_logo_paths[0])
                refs.append({
                    "image": _open_reference_image(logo_path, "logo"),
                    "process_mode": "blend",
                    "role": "logo",
                    "label": "Protected Brand Logo Lockup" if len(logo_refs) > 1 else "Protected Brand Logo",
                    "file_path": logo_path,
                    "id": ",".join(str(getattr(ref, "id", "")) for ref in logo_refs if getattr(ref, "id", None)),
                    "asset_name": "Co-brand lockup" if len(logo_refs) > 1 else getattr(logo_refs[0], "asset_name", None),
                    "asset_kind": None,
                    "usage_note": "Use the uploaded co-brand lockup as one protected mark." if len(logo_refs) > 1 else getattr(logo_refs[0], "usage_note", None),
                })
                logger.info(f"Slide {slide.page_num}: 加载受保护 Logo {logo_path}")
            except Exception as e:
                logger.warning(f"无法加载 Logo lockup: {e}")

    # 3. 全局视觉资产：只加载 visual plan 为当前页选中的资产。
    selected_asset_ids = []
    manual_asset_ids = set()
    if visual:
        raw_ids = visual.get("visual_asset_ids") or []
        if isinstance(raw_ids, list):
            selected_asset_ids = []
            for x in raw_ids:
                value = str(x)
                route_mode = asset_route_modes.get(value)
                if (
                    value
                    and value not in overlay_asset_ids
                    and route_mode != "overlay"
                    and value not in selected_asset_ids
                ):
                    selected_asset_ids.append(value)
        manual_raw = visual.get("manual_visual_asset_ids") or []
        if isinstance(manual_raw, list):
            manual_asset_ids = {str(x) for x in manual_raw if x}

    if selected_asset_ids and slide.project and slide.project.reference_images:
        selected_set = set(selected_asset_ids)
        project_assets = [
            ref for ref in slide.project.reference_images
            if ref.role == "visual_asset" and str(ref.id) in selected_set and not ref.slide_id
        ]
        project_assets.sort(key=lambda ref: selected_asset_ids.index(str(ref.id)) if str(ref.id) in selected_asset_ids else 999)
        for idx, ref in enumerate(project_assets, start=1):
            if len(refs) >= MAX_REFERENCE_INPUTS:
                break
            resolved = _resolve_file_path(ref.file_path)
            if resolved in already_loaded_paths:
                logger.info(f"Slide {slide.page_num}: 跳过重复视觉资产 {resolved}")
                continue
            if os.path.exists(resolved):
                try:
                    route_mode = asset_route_modes.get(str(ref.id)) or (
                        "double_blend"
                        if str(getattr(ref, "asset_kind", "") or "").lower() in {"product", "material"}
                        else "blend"
                    )
                    effective_process_mode = "crop" if route_mode == "double_blend" else (
                        "original" if route_mode == "overlay" else "blend"
                    )
                    refs.append({
                        "image": _open_reference_image(resolved, ref.role),
                        "process_mode": effective_process_mode,
                        "asset_route_mode": route_mode,
                        "role": ref.role,
                        "label": f"Global Visual Asset {idx}",
                        "file_path": resolved,
                        "id": getattr(ref, "id", None),
                        "asset_name": getattr(ref, "asset_name", None),
                        "asset_kind": getattr(ref, "asset_kind", None),
                        "usage_note": getattr(ref, "usage_note", None),
                        "asset_analysis": getattr(ref, "asset_analysis", None),
                        "manual_pin": str(ref.id) in manual_asset_ids,
                    })
                    already_loaded_paths.add(resolved)
                    logger.info(f"Slide {slide.page_num}: 加载视觉资产 {resolved}")
                except Exception as e:
                    logger.warning(f"无法加载视觉资产 {resolved}: {e}")
            else:
                logger.warning(f"视觉资产文件不存在: {ref.file_path} (解析后: {resolved})")

    # 4. 同家族种子页：版式锚点。仅取最多 2 张，避免抢主体权重。
    seed_loaded = False
    use_seed_reference_images = bool(settings.IMAGE_USE_SEED_REFERENCE_IMAGES)
    seed_reference_limit = _seed_reference_limit(slide)
    if seed_image_paths and not use_seed_reference_images:
        valid_seed_paths = [seed_path for seed_path in seed_image_paths[:seed_reference_limit] if seed_path and os.path.exists(_resolve_file_path(seed_path))]
        for seed_idx, seed_path in enumerate(valid_seed_paths, start=1):
            refs.append({
                "role": "seed_ref_hint",
                "label": f"Family Seed Layout {seed_idx}",
                "file_path": _resolve_file_path(seed_path),
            })
        seed_loaded = bool(valid_seed_paths)
        if seed_loaded:
            logger.info(
                f"Slide {slide.page_num}: 检测到同家族种子页 {len(valid_seed_paths)} 张，"
                "默认只作为文字一致性提示，不上传到图片编辑接口"
            )
    elif seed_image_paths:
        for seed_idx, seed_path in enumerate(seed_image_paths[:seed_reference_limit], start=1):
            if len(refs) >= MAX_REFERENCE_INPUTS:
                break
            resolved_seed = _resolve_file_path(seed_path)
            if not resolved_seed or not os.path.exists(resolved_seed):
                logger.warning(f"种子页文件不存在: {seed_path} (解析后: {resolved_seed})")
                continue
            try:
                refs.append({
                    "image": _open_reference_image(resolved_seed, "seed_ref"),
                    "process_mode": "blend",
                    "role": "seed_ref",
                    "label": f"Family Seed Layout {seed_idx}",
                    "file_path": resolved_seed,
                })
                seed_loaded = True
                logger.info(f"Slide {slide.page_num}: 加载同家族种子页 {resolved_seed}")
            except Exception as e:
                logger.warning(f"无法加载种子页 {seed_path}: {e}")

    # 5. 模板级参考：没有同家族种子页时，使用去 Logo 后的模板页作为版式锚点。
    if not seed_loaded and slide.project and slide.project.selected_template_recommendations:
        recommendations = slide.project.selected_template_recommendations
        slide_type = slide.type or "content"
        template_key = _map_slide_type_to_template_key(slide_type)
        tmpl = recommendations.get(template_key)
        if tmpl and isinstance(tmpl, dict) and tmpl.get("file_path"):
            tmpl_path = tmpl.get("layout_file_path") or tmpl.get("file_path")
            resolved_tmpl_path = _resolve_file_path(tmpl_path)
            if resolved_tmpl_path and os.path.exists(resolved_tmpl_path):
                try:
                    application_strength = _normalize_template_application_strength(tmpl.get("application_strength"))
                    template_reference_mode = _template_reference_mode(application_strength)
                    refs.append({
                        "image": _open_template_reference_image(resolved_tmpl_path, application_strength),
                        "process_mode": "blend",
                        "role": "template",
                        "label": "Template Layout Reference",
                        "file_path": resolved_tmpl_path,
                        "template_key": template_key,
                        "application_strength": application_strength,
                        "template_reference_mode": template_reference_mode,
                        "source_kind": tmpl.get("source_kind"),
                        "category": tmpl.get("category"),
                    })
                    logger.info(
                        "Slide %s: 加载模板版式参考图 %s (type=%s -> key=%s)",
                        slide.page_num,
                        resolved_tmpl_path,
                        slide_type,
                        template_key,
                    )
                except Exception as e:
                    logger.warning("无法加载模板版式参考图 %s: %s", resolved_tmpl_path, e)

    _attach_page_reference_identity_bindings(slide, refs)
    refs = sorted(refs, key=_reference_input_priority)
    image_ref_count = sum(1 for ref in refs if ref.get("image") is not None)
    if image_ref_count > MAX_REFERENCE_INPUTS:
        logger.warning(
            "Slide %s: 参考图 %s 张超过 API 上限 %s，仅前 %s 张进入本次生图",
            slide.page_num,
            image_ref_count,
            MAX_REFERENCE_INPUTS,
            MAX_REFERENCE_INPUTS,
        )
    limited_refs = []
    kept_image_refs = 0
    for ref in refs:
        if ref.get("image") is not None:
            if kept_image_refs >= MAX_REFERENCE_INPUTS:
                continue
            kept_image_refs += 1
        limited_refs.append(ref)
    logger.info(
        f"Slide {slide.page_num}: 共加载 {kept_image_refs} 张图片参考"
        f"（种子: {'有' if seed_loaded else '无'}）"
    )
    if audit_project_id or audit_run_id:
        append_pipeline_diagnostic_log(
            audit_project_id,
            audit_run_id,
            "reference_inputs_resolved",
            kind="image-reference",
            slide_id=getattr(slide, "id", None),
            page_num=getattr(slide, "page_num", None),
            overlay_asset_ids=sorted(overlay_asset_ids),
            loaded_page_reference_count=loaded_count,
            loaded_image_reference_count=kept_image_refs,
            skipped_overlay_count=skipped_overlay,
            skipped_finetune_count=skipped_finetune,
            skipped_duplicate_count=skipped_duplicate,
            skipped_missing_count=skipped_missing,
            max_reference_inputs=MAX_REFERENCE_INPUTS,
            references=_reference_audit(limited_refs),
        )
    return limited_refs


def _map_slide_type_to_template_key(slide_type: str) -> str:
    """将 slide 类型映射到模板类别。"""
    slide_type = normalize_slide_type(
        slide_type,
        allow_legacy_stored_aliases=True,
        default="content",
    )
    mapping = {
        "cover": "cover",
        "toc": "toc",
        "section": "section",
        "content": "content",
        "hero": "quote",
        "quote": "quote",
        "data": "data",
        "ending": "ending",
    }
    return mapping[slide_type]


def _slide_family(slide: Slide) -> Optional[str]:
    """从 slide.visual_json 读取 seed_family；为兼容老数据，缺失时按 type 推断。"""
    if slide.visual_json and isinstance(slide.visual_json, dict):
        family = slide.visual_json.get("seed_family")
        if family:
            return str(family)
    # 老数据 fallback：按 slide.type 推断（MECE：cover/ending 已拆分）
    slide_type = normalize_slide_type(
        slide.type,
        allow_legacy_stored_aliases=True,
        default="content",
    )
    if slide_type == "cover":
        return "cover"
    if slide_type == "ending":
        return "ending"
    if slide_type in {"hero", "quote"}:
        return "hero"
    if slide_type == "toc":
        return "toc"
    if slide_type == "section":
        return "section"
    if slide_type == "data":
        return "data"
    return "content"


def _slide_language_group(slide: Slide) -> str:
    if slide.visual_json and isinstance(slide.visual_json, dict):
        group = slide.visual_json.get("visual_language_group")
        if group:
            return str(group)
    family = _slide_family(slide) or "content"
    return f"legacy_{family}"


def _slide_seed_key(slide: Slide) -> tuple[str, str] | None:
    family = _slide_family(slide)
    if not family:
        return None
    return (family, _slide_language_group(slide))


def _is_finetune_slide(slide: Slide) -> bool:
    """该页是否处于 finetune（单页微调）模式：底图优先，跳过种子参考。"""
    if not slide.visual_json or not isinstance(slide.visual_json, dict):
        return False
    return bool(slide.visual_json.get("finetune_base_image_path"))


def _collect_existing_seeds(slides: List[Slide]) -> Dict[tuple[str, str], List[str]]:
    """
    扫描所有 slide，把已经成功生成的页按 seed_family 聚合，
    返回 {(family, visual_language_group): [image_path, ...]}，按推荐种子优先 + 页码升序排序。
    每家族最多保留 2 张，避免 Stage 2 注入过多版式样本干扰主体。
    """
    pool: Dict[tuple[str, str], List[Dict]] = {}
    for s in slides:
        if s.status != "completed":
            continue
        if not s.image_path or not os.path.exists(s.image_path):
            continue
        if _is_finetune_slide(s):
            # finetune 模式产生的图片继承自旧底图，不作为家族种子
            continue
        seed_key = _slide_seed_key(s)
        if not seed_key:
            continue
        is_recommended = False
        if isinstance(s.visual_json, dict):
            is_recommended = bool(s.visual_json.get("is_seed_recommended"))
        pool.setdefault(seed_key, []).append({
            "image_path": s.image_path,
            "page_num": s.page_num,
            "is_recommended": is_recommended,
        })

    seeds_by_family: Dict[tuple[str, str], List[str]] = {}
    for seed_key, items in pool.items():
        items.sort(key=lambda x: (not x["is_recommended"], x["page_num"]))
        seeds_by_family[seed_key] = [item["image_path"] for item in items[:2]]
    return seeds_by_family


def _is_product_or_material_ref(ref: Dict) -> bool:
    return (
        ref.get("role") == "visual_asset"
        and str(ref.get("asset_kind") or "").lower() in {"product", "material"}
    )


def _uses_direct_finetune_ref(ref_data: List[Dict]) -> bool:
    return any(ref.get("role") == "finetune_base" for ref in ref_data)


def _finetune_regions_for_slide(slide: Slide) -> List[Dict]:
    visual = slide.visual_json if isinstance(slide.visual_json, dict) else {}
    regions = visual.get("finetune_regions") or []
    normalized: List[Dict] = []
    if not isinstance(regions, list):
        return normalized
    for index, region in enumerate(regions):
        if not isinstance(region, dict):
            continue
        bbox = region.get("bbox")
        if not isinstance(bbox, dict):
            continue
        try:
            x = max(0.0, min(1.0, float(bbox.get("x", 0))))
            y = max(0.0, min(1.0, float(bbox.get("y", 0))))
            width = max(0.0, min(1.0 - x, float(bbox.get("width", 0))))
            height = max(0.0, min(1.0 - y, float(bbox.get("height", 0))))
        except (TypeError, ValueError):
            continue
        if width <= 0 or height <= 0:
            continue
        normalized.append({
            "id": str(region.get("id") or f"region-{index + 1}"),
            "label": str(region.get("label") or f"Region {index + 1}"),
            "bbox": {"x": x, "y": y, "width": width, "height": height},
        })
    return normalized


def _region_pixel_bounds(bbox: Dict, width: int, height: int) -> tuple[int, int, int, int]:
    x1 = max(0, min(width, int(round(float(bbox["x"]) * width))))
    y1 = max(0, min(height, int(round(float(bbox["y"]) * height))))
    x2 = max(0, min(width, int(round((float(bbox["x"]) + float(bbox["width"])) * width))))
    y2 = max(0, min(height, int(round((float(bbox["y"]) + float(bbox["height"])) * height))))
    return x1, y1, x2, y2


def _finetune_region_edit_mask(base_image: Image.Image, regions: List[Dict]) -> Image.Image | None:
    if not regions:
        return None
    width, height = base_image.size
    mask = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    draw = ImageDraw.Draw(mask, "RGBA")
    for region in regions:
        x1, y1, x2, y2 = _region_pixel_bounds(region["bbox"], width, height)
        if x2 > x1 and y2 > y1:
            draw.rectangle((x1, y1, x2, y2), fill=(0, 0, 0, 0))
    return mask


def _finetune_region_composite_mask(size: tuple[int, int], regions: List[Dict]) -> Image.Image | None:
    if not regions:
        return None
    width, height = size
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    for region in regions:
        x1, y1, x2, y2 = _region_pixel_bounds(region["bbox"], width, height)
        if x2 > x1 and y2 > y1:
            draw.rectangle((x1, y1, x2, y2), fill=255)
    return mask.filter(ImageFilter.GaussianBlur(1.2))


def _restore_unselected_finetune_pixels(base_image: Image.Image, edited_image: Image.Image, regions: List[Dict]) -> Image.Image:
    composite_mask = _finetune_region_composite_mask(edited_image.size, regions)
    if composite_mask is None:
        return edited_image
    base = ImageOps.exif_transpose(base_image).convert("RGB")
    edited = ImageOps.exif_transpose(edited_image).convert("RGB")
    if base.size != edited.size:
        base = base.resize(edited.size, Image.Resampling.LANCZOS)
    return Image.composite(edited, base, composite_mask)


def _finetune_region_guide_image(base_image: Image.Image, regions: List[Dict]) -> Image.Image | None:
    if not regions:
        return None
    guide = ImageOps.exif_transpose(base_image).convert("RGBA")
    overlay = Image.new("RGBA", guide.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    width, height = guide.size
    stroke = max(4, round(min(width, height) * 0.006))
    for region in regions:
        x1, y1, x2, y2 = _region_pixel_bounds(region["bbox"], width, height)
        if x2 <= x1 or y2 <= y1:
            continue
        draw.rectangle((x1, y1, x2, y2), fill=(255, 0, 0, 28), outline=(255, 0, 0, 255), width=stroke)
    return Image.alpha_composite(guide, overlay).convert("RGB")


def _product_refinement_refs(ref_data: List[Dict]) -> List[Dict]:
    refs = []
    for ref in ref_data:
        if not ref.get("image"):
            continue
        route_mode = str(ref.get("asset_route_mode") or "").lower()
        process_mode = str(ref.get("process_mode") or "").lower()
        is_page_precision_ref = ref.get("role") in {"content_ref", "chart_ref"} and process_mode == "crop"
        if not (_is_product_or_material_ref(ref) or is_page_precision_ref):
            continue
        if route_mode in {"blend", "overlay", "original", "exact", "exact_overlay"}:
            continue
        if route_mode and route_mode not in {"double_blend", "crop"}:
            continue
        if not route_mode and process_mode in {"blend", "original", "text_only"}:
            continue
        refs.append(ref)
    return refs


def _background_pass_prompt(prompt: str, product_refs: List[Dict]) -> str:
    ref_names = [
        str(ref.get("asset_name") or "").strip()
        for ref in product_refs
        if str(ref.get("asset_name") or "").strip()
    ]
    ref_label = " / ".join(ref_names[:3]) if ref_names else "the uploaded reference material"
    return (
        prompt
        + "\n\nReference fidelity: generate the complete slide using the supplied reference material, including "
        f"{ref_label}. Use the uploaded product/material image as the source for the corresponding object. "
        "Preserve the referenced people, objects, screenshots, charts, shape, color blocks, visible text, "
        "labels, logos, and small markings as much as possible. Do not invent a different subject or reduce it "
        "to generic shapes."
    )


def _product_refinement_prompt(slide: Slide, product_refs: List[Dict]) -> str:
    ref_count = len([ref for ref in product_refs if ref.get("image") is not None])
    binding_lines = []
    for idx, ref in enumerate([ref for ref in product_refs if ref.get("image") is not None], start=2):
        binding = ref.get("reference_binding") if isinstance(ref.get("reference_binding"), dict) else None
        if not binding:
            continue
        name = str(binding.get("name") or "").strip()
        position = str(binding.get("position") or "").strip()
        if name:
            binding_lines.append(f"第{idx}张参考图 -> {name}" + (f"（{position}）" if position else ""))
    binding_text = ""
    if binding_lines:
        binding_text = (
            "参考图身份绑定："
            + "；".join(binding_lines)
            + "。不要交换人物身份、姓名对应关系或页面位置。"
        )
    if ref_count > 1:
        last_index = ref_count + 1
        ref_range = f"第2-{last_index}张"
        return (
            f"第1张图是当前PPT页面，{ref_range}图是要精修融合进去的参考素材，可能是人物、产品、截图或图表。"
            f"{binding_text}"
            f"请分别把{ref_range}参考图放回第1张图中对应的位置，"
            f"要求尽量1:1保留{ref_range}图的所有可见细节、比例、颜色、文字、标识、人物身份和小标记。"
            "不要把多张参考图合并成一个物体，不要遗漏任意一张参考图。"
            "不要改变第1张图中的其它任何画面元素、文字、版式、背景、人物、图表和装饰。"
            "只输出修改后的完整PPT页面图片。"
        )
    return (
        "第1张图是当前PPT页面，第2张图是要精修融合进去的参考素材，可能是人物、产品、截图或图表。"
        f"{binding_text}"
        "请把第1张图中对应的位置校准为第2张图，要求尽量1:1保留第2张图的所有可见细节、人物身份、文字和标识。"
        "不要改变第1张图中的其它任何画面元素、文字、版式、背景、人物、图表和装饰。"
        "只输出修改后的完整PPT页面图片。"
    )


def _ref_image_aspect(ref: Dict) -> float | None:
    img = ref.get("image")
    width, height = getattr(img, "size", (0, 0))
    if width <= 0 or height <= 0:
        return None
    return width / height


def _slot_center(block: Dict) -> tuple[float, float]:
    return (
        float(block.get("x") or 0) + float(block.get("width") or 0) / 2.0,
        float(block.get("y") or 0) + float(block.get("height") or 0) / 2.0,
    )


def _slot_position_label(block: Dict, blocks: list[Dict]) -> str:
    centers = [_slot_center(item) for item in blocks]
    center_x, center_y = _slot_center(block)
    return _binding_position_label(
        center_x,
        center_y,
        min_x=min(x for x, _y in centers),
        max_x=max(x for x, _y in centers),
        min_y=min(y for _x, y in centers),
        max_y=max(y for _x, y in centers),
    )


def _assign_slots_to_refs(candidates: list[Dict], product_refs: list[Dict]) -> list[Dict]:
    row_major = sorted(candidates, key=lambda block: (_slot_center(block)[1], _slot_center(block)[0]))
    if len(row_major) == len(product_refs):
        labels = [_slot_position_label(block, row_major) for block in row_major]
        bindings = [
            ref.get("reference_binding") if isinstance(ref.get("reference_binding"), dict) else {}
            for ref in product_refs
        ]
        desired = [str(binding.get("position") or "") for binding in bindings]
        if all(desired) and len(set(desired)) == len(desired) and len(set(labels)) == len(labels):
            by_label = dict(zip(labels, row_major))
            if all(label in by_label for label in desired):
                return [by_label[label] for label in desired]
    return row_major[:len(product_refs)]


def _detect_local_refinement_slots(base_path: str, product_refs: list[Dict]) -> list[Dict]:
    """Find target image slots on the generated page for per-component refinement."""
    if len(product_refs) <= 1:
        return []
    try:
        from app.services.visual_slot_detection import detect_image_blocks

        blocks = detect_image_blocks(base_path, [], max_assets=6)
    except Exception as exc:
        logger.warning("Pipeline: local slot detection failed for %s: %s", base_path, exc)
        return []

    source_aspects = [aspect for aspect in (_ref_image_aspect(ref) for ref in product_refs) if aspect]
    median_aspect = sorted(source_aspects)[len(source_aspects) // 2] if source_aspects else 1.0
    max_candidate_aspect = max(1.0, min(4.0, median_aspect * 1.4))
    candidates: list[Dict] = []
    for block in blocks:
        width = float(block.get("width") or 0)
        height = float(block.get("height") or 0)
        x = float(block.get("x") or 0)
        y = float(block.get("y") or 0)
        if width <= 0 or height <= 0:
            continue
        area = width * height
        aspect = width / height
        if area < 0.012 or area > 0.18:
            continue
        if width < 0.04 or height < 0.10:
            continue
        if y < 0.10 or x + width > 0.985:
            continue
        if aspect > max_candidate_aspect:
            continue
        candidates.append(block)
    if len(candidates) < len(product_refs):
        return []
    return _assign_slots_to_refs(candidates, product_refs)


def _normalized_box_to_pixels(block: Dict, size: tuple[int, int]) -> tuple[int, int, int, int]:
    width, height = size
    left = max(0, min(width - 1, int(round(float(block.get("x") or 0) * width))))
    top = max(0, min(height - 1, int(round(float(block.get("y") or 0) * height))))
    right = max(left + 1, min(width, int(round((float(block.get("x") or 0) + float(block.get("width") or 0)) * width))))
    bottom = max(top + 1, min(height, int(round((float(block.get("y") or 0) + float(block.get("height") or 0)) * height))))
    return left, top, right, bottom


def _local_slot_refinement_prompt(ref: Dict, index: int, total: int) -> str:
    binding = ref.get("reference_binding") if isinstance(ref.get("reference_binding"), dict) else {}
    name = str(binding.get("name") or ref.get("asset_name") or f"素材 {index}").strip()
    position = str(binding.get("position") or "").strip()
    target = f"「{name}」" if name else f"第 {index}/{total} 个素材"
    position_text = f"（目标位置：{position}）" if position else ""
    return (
        f"第1张图是必须保留身份和细节的源素材：{target}{position_text}。"
        "第2张图是当前PPT页面里对应位置的样式裁剪。"
        "请只输出一个可粘回该位置的局部图片：保持第1张图的人物身份、五官结构、发型、眼镜、服装、产品细节或截图文字；"
        "同时匹配第2张图的构图、裁切比例、背景质感、光影和色调。"
        "不要生成整页PPT，不要生成姓名、正文、卡片边框或额外装饰。"
    )


def _try_local_slot_refinement(
    *,
    slide: Slide,
    project_id: str,
    base_img: Image.Image,
    base_path: str,
    product_refs: list[Dict],
    run_id: str | None = None,
) -> Image.Image | None:
    slots = _detect_local_refinement_slots(base_path, product_refs)
    if len(slots) < len(product_refs):
        append_image_generation_log(
            project_id,
            run_id,
            "local_slot_refinement_unavailable",
            page_num=slide.page_num,
            slide_id=slide.id,
            product_ref_count=len(product_refs),
            detected_slot_count=len(slots),
        )
        return None

    composite = base_img.convert("RGB").copy()
    append_image_generation_log(
        project_id,
        run_id,
        "local_slot_refinement_started",
        page_num=slide.page_num,
        slide_id=slide.id,
        product_ref_count=len(product_refs),
        detected_slot_count=len(slots),
        references=_reference_audit(product_refs),
    )
    for index, (ref, slot) in enumerate(zip(product_refs, slots), start=1):
        left, top, right, bottom = _normalized_box_to_pixels(slot, composite.size)
        slot_crop = composite.crop((left, top, right, bottom))
        prompt = _local_slot_refinement_prompt(ref, index, len(product_refs))
        append_image_generation_log(
            project_id,
            run_id,
            "local_slot_refinement_slot_started",
            page_num=slide.page_num,
            slide_id=slide.id,
            slot_index=index,
            slot_box=slot,
            reference=(_reference_audit([ref]) or [{}])[0],
            **_prompt_audit(prompt),
        )
        refined = generate_slide_image(
            prompt=prompt,
            reference_images=[ref["image"], slot_crop],
            resolution="4K",
            aspect_ratio="1:1",
            project_id=project_id,
        )
        patch = ImageOps.fit(refined.convert("RGB"), (right - left, bottom - top), method=Image.Resampling.LANCZOS)
        composite.paste(patch, (left, top))
        append_image_generation_log(
            project_id,
            run_id,
            "local_slot_refinement_slot_finished",
            page_num=slide.page_num,
            slide_id=slide.id,
            slot_index=index,
            slot_box=slot,
        )
    append_image_generation_log(
        project_id,
        run_id,
        "local_slot_refinement_finished",
        page_num=slide.page_num,
        slide_id=slide.id,
        product_ref_count=len(product_refs),
    )
    return composite


def _generate_one_slide(
    slide: Slide,
    project_id: str,
    output_dir: str,
    preloaded_ref_data: Optional[List[Dict]] = None,
    run_id: str | None = None,
) -> Dict:
    """
    在线程池中执行单页生成（纯 IO/计算，不涉及数据库操作）。
    返回 dict: {slide, image_path?, error?}
    """
    if not slide.prompt_text:
        append_image_generation_log(
            project_id,
            run_id,
            "slide_skipped",
            page_num=slide.page_num,
            slide_id=slide.id,
            reason="missing_prompt",
        )
        return {"slide": slide, "error": "缺少 prompt"}

    slide_started_at = time.time()
    reset_image_call_events()
    try:
        ref_data = list(preloaded_ref_data) if preloaded_ref_data else []
        product_refs = _product_refinement_refs(ref_data)
        use_product_refinement = bool(product_refs) and not _uses_direct_finetune_ref(ref_data)
        first_pass_ref_data = list(ref_data)
        ref_images = [r["image"] for r in first_pass_ref_data if r.get("image") is not None]
        prompt = slide.prompt_text
        finetune_regions = _finetune_regions_for_slide(slide)
        finetune_base_ref = next(
            (
                ref for ref in first_pass_ref_data
                if ref.get("role") == "finetune_base" and ref.get("image") is not None
            ),
            None,
        )
        edit_mask = (
            _finetune_region_edit_mask(finetune_base_ref["image"], finetune_regions)
            if finetune_base_ref and finetune_regions
            else None
        )

        # 当本页带有种子参考图时，在 prompt 末尾追加一行明确告知模型，
        # 让它把同家族种子页当作版式锚点 — 不复制内容、只复用视觉系统。
        seed_image_count = sum(1 for r in first_pass_ref_data if r.get("role") == "seed_ref")
        seed_hint_count = sum(1 for r in first_pass_ref_data if r.get("role") == "seed_ref_hint")
        seed_count = seed_image_count + seed_hint_count
        template_ref_count = sum(1 for r in first_pass_ref_data if r.get("role") == "template")
        template_hint_count = sum(1 for r in first_pass_ref_data if r.get("role") == "template_hint")
        page_reference_fidelity = _page_reference_fidelity_instruction(first_pass_ref_data)
        if seed_image_count > 0:
            seed_instruction = _seed_base_edit_instruction(slide, seed_image_count) or (
                "\n\nSAME-FAMILY LAYOUT REFERENCE: Attached seed slide(s) are layout anchors only. "
                "Reuse grid, hierarchy, palette rhythm, typography scale, and ornament language. "
                "Do not copy seed text, photos, scene subjects, product shots, or logos. "
                "Render this slide's own visible text and visual evidence in the same deck DNA."
            )
            prompt = prompt + seed_instruction
        elif seed_hint_count > 0:
            prompt = prompt + (
                "\n\nSAME-FAMILY VISUAL CONTINUITY: Follow the deck's established grid, hierarchy, palette, "
                "typography scale, and ornament language. "
                "Keep this slide's own text and visual evidence."
            )
        elif template_ref_count > 0:
            prompt = prompt + _template_reference_instruction(first_pass_ref_data)
        elif template_hint_count > 0:
            prompt = prompt + (
                "\n\nTEMPLATE STYLE MEMORY: Use only the template-derived layout, spacing, hierarchy, "
                "palette relationship, typography rhythm, and ornament language. "
                "Do not copy old template text, images, products, logos, portraits, people, or scene subjects."
            )
        if page_reference_fidelity:
            prompt = prompt + page_reference_fidelity

        if use_product_refinement:
            prompt = _background_pass_prompt(prompt, product_refs)

        append_image_generation_log(
            project_id,
            run_id,
            "slide_started",
            page_num=slide.page_num,
            slide_id=slide.id,
            slide_type=slide.type,
            reference_count=len(ref_images),
            references=_reference_audit(first_pass_ref_data),
            seed_reference_count=seed_count,
            template_reference_count=template_ref_count + template_hint_count,
            template_uploaded_reference_count=template_ref_count,
            template_hint_count=template_hint_count,
            product_refinement=use_product_refinement,
            finetune_region_count=len(finetune_regions),
            edit_mask=bool(edit_mask),
            **_prompt_audit(prompt),
        )

        image_generation_kwargs = {
            "prompt": prompt,
            "reference_images": ref_images if ref_images else None,
            "resolution": "4K",
            "aspect_ratio": "16:9",
            "project_id": project_id,
        }
        if edit_mask:
            image_generation_kwargs["edit_mask"] = edit_mask
        img = generate_slide_image(**image_generation_kwargs)
        if edit_mask and finetune_base_ref:
            img = _restore_unselected_finetune_pixels(
                finetune_base_ref["image"],
                img,
                finetune_regions,
            )

        if use_product_refinement:
            base_path = save_slide_image(
                img=img,
                project_id=project_id,
                page_num=slide.page_num,
                output_dir=output_dir,
                suffix="_base",
            )
            local_refined = None
            if len(product_refs) > 1:
                try:
                    local_refined = _try_local_slot_refinement(
                        slide=slide,
                        project_id=project_id,
                        base_img=img,
                        base_path=base_path,
                        product_refs=product_refs,
                        run_id=run_id,
                    )
                except Exception as local_refine_error:
                    logger.warning(
                        "Pipeline: 第 %s 页局部精修失败，继续尝试整页精修: %s",
                        slide.page_num,
                        local_refine_error,
                    )
                    append_image_generation_log(
                        project_id,
                        run_id,
                        "local_slot_refinement_fallback",
                        page_num=slide.page_num,
                        slide_id=slide.id,
                        base_path=base_path,
                        product_ref_count=len(product_refs),
                        error=str(local_refine_error)[:1000],
                    )

            if local_refined is not None:
                img = local_refined
                logger.info(
                    f"Pipeline: 第 {slide.page_num} 页完成逐组件精修 "
                    f"(base={base_path}, product_refs={len(product_refs)})"
                )
            else:
                refinement_images = [img] + [ref["image"] for ref in product_refs]
                refinement_prompt = _product_refinement_prompt(slide, product_refs)
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
                append_image_generation_log(
                    project_id,
                    run_id,
                    "product_refinement_started",
                    page_num=slide.page_num,
                    slide_id=slide.id,
                    base_path=base_path,
                    product_ref_count=len(product_refs),
                    reference_count=len(refinement_images[:MAX_REFERENCE_INPUTS]),
                    references=_reference_audit(product_refs),
                    **_prompt_audit(refinement_prompt),
                )
                try:
                    img = generate_slide_image(
                        prompt=refinement_prompt,
                        reference_images=refinement_images[:MAX_REFERENCE_INPUTS],
                        resolution="4K",
                        aspect_ratio="16:9",
                        project_id=project_id,
                    )
                    logger.info(
                        f"Pipeline: 第 {slide.page_num} 页完成产品二次生成 "
                        f"(base={base_path}, product_refs={len(product_refs)})"
                    )
                    append_image_generation_log(
                        project_id,
                        run_id,
                        "product_refinement_finished",
                        page_num=slide.page_num,
                        slide_id=slide.id,
                        base_path=base_path,
                        product_ref_count=len(product_refs),
                    )
                except Exception as refine_error:
                    logger.warning(
                        "Pipeline: 第 %s 页产品二次生成失败，回退使用 base image: %s",
                        slide.page_num,
                        refine_error,
                    )
                    append_image_generation_log(
                        project_id,
                        run_id,
                        "product_refinement_fallback",
                        page_num=slide.page_num,
                        slide_id=slide.id,
                        base_path=base_path,
                        product_ref_count=len(product_refs),
                        error=str(refine_error)[:1000],
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
        events = get_image_call_events()
        for event in events:
            append_image_generation_log(
                project_id,
                run_id,
                "image_api_event",
                page_num=slide.page_num,
                slide_id=slide.id,
                **event,
            )
        append_image_generation_log(
            project_id,
            run_id,
            "slide_finished",
            page_num=slide.page_num,
            slide_id=slide.id,
            status="completed",
            image_path=image_path,
            elapsed_seconds=round(time.time() - slide_started_at, 3),
            api_event_count=len(events),
        )
        return {
            "slide": slide,
            "image_path": image_path,
            "error": None,
            "image_generation_events": events,
        }
    except Exception as e:
        logger.error(f"Pipeline: 第 {slide.page_num} 页生成失败: {e}")
        events = get_image_call_events()
        for event in events:
            append_image_generation_log(
                project_id,
                run_id,
                "image_api_event",
                page_num=slide.page_num,
                slide_id=slide.id,
                **event,
            )
        append_image_generation_log(
            project_id,
            run_id,
            "slide_finished",
            page_num=slide.page_num,
            slide_id=slide.id,
            status="failed",
            error=str(e)[:1000],
            elapsed_seconds=round(time.time() - slide_started_at, 3),
            api_event_count=len(events),
        )
        return {
            "slide": slide,
            "error": str(e)[:500],
            "image_generation_events": events,
        }


def run_generation_pipeline(
    project_id: str,
    db: Session,
    page_nums: Optional[List[int]] = None,
    prototype: bool = False,
    run_id: str | None = None,
    defer_finalization: bool = False,
):
    """
    执行生成流水线（支持单页并行，由 Celery 调用）。
    并行策略：主流程仍用线程池分派页面，真实图片 API 调用在 image_generation
    模块内限流，避免多页同时上传参考图导致写入超时。
    """
    logger.info(f"Pipeline: 开始生成项目 {project_id}, page_nums={page_nums}")
    reset_run_image_counter()

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

    run = get_run(db, run_id)
    run_stage = image_generation_run_stage(
        kind=run.kind if run else None,
        prototype=prototype,
        page_nums=page_nums,
    )
    run_target_pages = set()
    if run and run.target_page_nums:
        run_target_pages = {int(p) for p in run.target_page_nums}
    progress_slides = [s for s in slides if s.page_num in run_target_pages] if run_target_pages else target_slides
    if not progress_slides:
        progress_slides = target_slides

    def progress_counts() -> tuple[int, int, int]:
        total = len(progress_slides)
        completed = sum(1 for s in progress_slides if s.status == "completed")
        failed = sum(1 for s in progress_slides if s.status == "failed")
        return total, completed, failed

    log_path = image_generation_log_path(project_id, run_id)
    append_image_generation_log(
        project_id,
        run_id,
        "pipeline_started",
        project_title=project.title,
        prototype=prototype,
        requested_page_nums=page_nums,
        target_page_nums=[s.page_num for s in target_slides],
        total_target=len(target_slides),
        log_path=log_path,
    )
    logger.info("Pipeline: image generation audit log: %s", log_path)

    mark_run_running(db, run_id, stage=run_stage, message=image_generation_running_message(run_stage))
    db.commit()

    # 先把目标页标记为 generating（只涉及本任务负责的页面）
    for slide in target_slides:
        if has_stale_flags(slide.visual_json, "content", "visual"):
            slide.status = "failed"
            slide.error_msg = "页面内容或画面方案已更新，请先重新生成画面方案。"
        elif not slide.prompt_text:
            slide.status = "failed"
            slide.error_msg = "缺少 prompt"
        else:
            _archive_current_image(slide, db)
            slide.status = "generating"
            slide.error_msg = None
    db.commit()
    total_target, completed_now, failed_now = progress_counts()
    update_run_progress(db, run_id, total_count=total_target, completed_count=completed_now, failed_count=failed_now)
    db.commit()

    output_dir = settings.OUTPUT_DIR or "./outputs"
    slide_images = []

    def attach_generation_audit(slide: Slide, result: Dict) -> None:
        events = result.get("image_generation_events") or []
        if not events and not result.get("error"):
            return
        visual = dict(slide.visual_json) if isinstance(slide.visual_json, dict) else {}
        visual["last_image_generation"] = {
            "status": "failed" if result.get("error") else "completed",
            "events": events[-8:],
            "error": result.get("error"),
        }
        slide.visual_json = visual
        flag_modified(slide, "visual_json")

    def record_generation_result(result: Dict) -> None:
        if not current_run_active():
            logger.info(f"Pipeline: run {run_id} is no longer active; skipping stale slide writeback")
            return
        slide = result["slide"]
        if result.get("error"):
            slide.status = "failed"
            slide.error_msg = result["error"]
            attach_generation_audit(slide, result)
        else:
            slide.image_path = result["image_path"]
            slide.status = "completed"
            slide.error_msg = None
            if isinstance(slide.visual_json, dict):
                slide.visual_json = with_stale_flags(slide.visual_json, image=False)
                flag_modified(slide, "visual_json")
            if slide.visual_json and isinstance(slide.visual_json, dict) and slide.visual_json.get("finetune_base_image_path"):
                slide.visual_json = {
                    k: v for k, v in slide.visual_json.items()
                    if k not in {
                        "finetune_base_image_path",
                        "finetune_instruction",
                        "finetune_attachment_ids",
                        "finetune_visual_asset_ids",
                        "finetune_regions",
                    }
                }
                flag_modified(slide, "visual_json")
            attach_generation_audit(slide, result)
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
        total_target, completed_now, failed_now = progress_counts()
        update_run_progress(
            db,
            run_id,
            completed_count=completed_now,
            failed_count=failed_now,
            total_count=total_target,
            message=image_generation_progress_message(run_stage, completed_now, total_target, failed_now),
        )
        db.commit()

    abort_generation_error: str | None = None

    def should_abort_after_provider_cutoff(result: Dict) -> bool:
        if not result.get("error") or not _result_has_gateway_timeout(result):
            return False
        if _pipeline_image_worker_count() > 1:
            return False
        _, completed, _ = progress_counts()
        return completed == 0

    def mark_unattempted_after_provider_cutoff(error: str) -> None:
        message = (
            "图片服务长时间没有返回，本次生成已停止，未继续生成此页。"
            "请稍后重新打样，或检查图片服务配置。"
        )
        for slide in target_slides:
            if slide.status == "generating":
                slide.status = "prompt_ready"
                slide.error_msg = message
        db.commit()
        append_image_generation_log(
            project_id,
            run_id,
            "pipeline_aborted",
            reason="provider_gateway_timeout",
            error=error[:1000],
            log_path=log_path,
        )

    def _is_transient_error(error: str | None) -> bool:
        if not error:
            return False
        lower = error.lower()
        transient_markers = (
            "timeout", "timed out", "connection error", "connection reset",
            "connection aborted", "remote end closed", "read operation timed out",
            "gateway", "上游连接窗口截断", "rate limit", "too many requests",
            "429", " temporarily unavailable", "service unavailable", "503",
        )
        return any(marker in lower for marker in transient_markers)

    def _result_has_image_api_attempts(result: Dict) -> bool:
        events = result.get("image_generation_events")
        return isinstance(events, list) and bool(events)

    def _generate_one_slide_with_retry(
        slide: Slide, project_id: str, output_dir: str,
        preloaded_ref_data: Optional[List[Dict]] = None, run_id: str | None = None,
    ) -> Dict:
        result = _generate_one_slide(slide, project_id, output_dir, preloaded_ref_data, run_id)
        if (
            result.get("error")
            and _is_transient_error(result["error"])
            and not _result_has_image_api_attempts(result)
        ):
            logger.info("Retrying slide %s after transient error: %s", slide.page_num, result["error"][:120])
            time.sleep(1.0)
            result = _generate_one_slide(slide, project_id, output_dir, preloaded_ref_data, run_id)
            if result.get("error"):
                logger.warning("Slide %s retry also failed: %s", slide.page_num, result["error"][:120])
        elif result.get("error") and _is_transient_error(result["error"]):
            logger.info(
                "Skipping slide-level retry for page %s because image API retries already ran",
                slide.page_num,
            )
        return result

    def run_slide_group(group_slides: List[Slide], ref_data_by_slide: Dict, on_success=None) -> None:
        nonlocal abort_generation_error
        if not group_slides or abort_generation_error:
            return

        max_workers = min(len(group_slides), _pipeline_image_worker_count())
        if max_workers <= 1:
            for slide in group_slides:
                if abort_generation_error:
                    break
                result = _generate_one_slide_with_retry(slide, project_id, output_dir, ref_data_by_slide.get(slide.id), run_id)
                record_generation_result(result)
                if not result.get("error") and on_success:
                    on_success(result)
                if should_abort_after_provider_cutoff(result):
                    abort_generation_error = str(result.get("error") or "图片服务长时间没有返回")
                    mark_unattempted_after_provider_cutoff(abort_generation_error)
                    break
            return

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_slide = {
                executor.submit(_generate_one_slide_with_retry, slide, project_id, output_dir, ref_data_by_slide.get(slide.id), run_id): slide
                for slide in group_slides
            }
            for future in as_completed(future_to_slide):
                result = future.result()
                record_generation_result(result)
                if not result.get("error") and on_success:
                    on_success(result)
                if should_abort_after_provider_cutoff(result):
                    abort_generation_error = str(result.get("error") or "图片服务长时间没有返回")
                    for pending in future_to_slide:
                        pending.cancel()
                    mark_unattempted_after_provider_cutoff(abort_generation_error)
                    break

    slides_with_prompt = [s for s in target_slides if s.prompt_text and s.status != "failed"]
    if slides_with_prompt:
        # 两阶段生成：先生成同家族的种子页，再以种子图为版式锚点生成其它页。
        # 已经完成的页（finetune/重生成场景）直接进入 existing seeds 池，
        # 不重新生成；finetune 模式的页面跳过种子参考。
        seeds_by_family = _collect_existing_seeds(slides)

        # Stage 1：识别需要先行生成的「家族种子页」。
        # 条件：1) 是 is_seed_recommended 推荐种子页 OR 同家族目前还没有任何已完成页；
        #      2) 该页是 target_slides 的一部分；
        #      3) 不是 finetune 模式（finetune 优先底图，不参与种子家族）。
        target_seed_keys = {
            _slide_seed_key(s) for s in slides_with_prompt if _slide_seed_key(s)
        }

        seed_pages: List[Slide] = []
        non_seed_pages: List[Slide] = []
        for slide in slides_with_prompt:
            if _is_finetune_slide(slide):
                non_seed_pages.append(slide)
                continue
            family = _slide_family(slide)
            seed_key = _slide_seed_key(slide)
            if not family or not seed_key:
                non_seed_pages.append(slide)
                continue
            if seed_key in seeds_by_family:
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
        seed_target_keys = {_slide_seed_key(s) for s in seed_pages if _slide_seed_key(s)}
        for seed_key in target_seed_keys:
            if not seed_key or seed_key in seeds_by_family or seed_key in seed_target_keys:
                continue
            family_pages = [s for s in non_seed_pages if _slide_seed_key(s) == seed_key]
            if not family_pages:
                continue
            family_pages.sort(key=lambda s: s.page_num)
            promoted = family_pages[0]
            non_seed_pages = [s for s in non_seed_pages if s.id != promoted.id]
            seed_pages.append(promoted)
            seed_target_keys.add(seed_key)
            logger.info(
                f"Pipeline: 种子组 {seed_key} 没有推荐种子或现有种子，临时提升 page {promoted.page_num} 作种子"
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
            ref_data_by_slide = {
                s.id: _load_reference_images(
                    s,
                    seed_image_paths=None,
                    audit_project_id=project_id,
                    audit_run_id=run_id,
                )
                for s in seed_pages
            }
            def add_seed_result(result: Dict) -> None:
                slide = result["slide"]
                seed_key = _slide_seed_key(slide)
                if seed_key and result.get("image_path"):
                    seeds_by_family.setdefault(seed_key, []).append(result["image_path"])

            run_slide_group(seed_pages, ref_data_by_slide, on_success=add_seed_result)

        # Stage 2：生成非种子页，按家族注入种子参考图
        if non_seed_pages and not abort_generation_error:
            ref_data_by_slide = {}
            for s in non_seed_pages:
                seed_key = _slide_seed_key(s)
                seed_paths = seeds_by_family.get(seed_key, []) if seed_key else []
                # finetune 页面不使用种子参考（底图优先）
                if _is_finetune_slide(s):
                    seed_paths = []
                ref_data_by_slide[s.id] = _load_reference_images(
                    s,
                    seed_image_paths=seed_paths,
                    audit_project_id=project_id,
                    audit_run_id=run_id,
                )
            run_slide_group(non_seed_pages, ref_data_by_slide)

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

    total_target, target_completed, target_failed = progress_counts()
    unfinished_run_targets = [
        s for s in progress_slides if s.status not in {"completed", "failed"}
    ] if not abort_generation_error else []
    if defer_finalization or unfinished_run_targets:
        update_run_progress(
            db,
            run_id,
            completed_count=target_completed,
            failed_count=target_failed,
            total_count=total_target,
            message=image_generation_progress_message(run_stage, target_completed, total_target, target_failed),
        )
        db.commit()
        append_image_generation_log(
            project_id,
            run_id,
            "pipeline_chunk_finished",
            current_page_nums=[s.page_num for s in target_slides],
            unfinished_page_nums=[s.page_num for s in unfinished_run_targets],
            target_completed=target_completed,
            target_failed=target_failed,
            total_target=total_target,
            log_path=log_path,
        )
        logger.info(
            "Pipeline: chunk completed for run %s (%s/%s complete); deferring final writeback",
            run_id,
            target_completed,
            total_target,
        )
        clear_reference_upload_cache()
        return

    # 组装 PPTX（用 Redis 锁防止并发生成任务同时写文件）
    pptx_lock_key = f"project:{project_id}:pptx_assembly"
    assembly_error = None
    if all_completed_images:
        pptx_lock_ttl = max(120, int(settings.PPTX_ASSEMBLY_LOCK_TTL_SECONDS or 300))
        pptx_acquired = redis_client.set(pptx_lock_key, "1", nx=True, ex=pptx_lock_ttl)
        if pptx_acquired:
            try:
                if prototype:
                    pptx_path = os.path.join(output_dir, project_id, "prototype.pptx")
                else:
                    pptx_path = os.path.join(output_dir, project_id, "presentation.pptx")
                os.makedirs(os.path.dirname(pptx_path), exist_ok=True)

                logo_refs = [
                    ref for ref in project.reference_images or []
                    if ref.role == "logo" and is_logo_confirmed(ref) and not ref.slide_id and os.path.exists(_resolve_file_path(ref.file_path))
                ]
                logo_config = (
                    {
                        "file_paths": [_resolve_file_path(ref.file_path) for ref in logo_refs],
                        "anchor": getattr(logo_refs[0], "logo_anchor", None) or "top-right",
                    }
                    if logo_refs else None
                )
                logo_path_for_overlay = prepare_logo_lockup_image([_resolve_file_path(ref.file_path) for ref in logo_refs]) if logo_refs else None
                logo_symbol_path_for_overlay = prepare_logo_symbol_image(_resolve_file_path(logo_refs[0].file_path)) if len(logo_refs) == 1 else None
                slides_by_page = {s.page_num: s for s in slides}
                if logo_path_for_overlay and logo_config:
                    for slide_data in all_completed_images:
                        if not should_show_logo(slide_data):
                            continue
                        visual = dict(slide_data.get("visual_json") or {})
                        raw_policy = dict(visual.get("logo_policy") or {})
                        policy = logo_policy_for_page(slide_data)
                        render_policy_input = {**raw_policy, **policy}
                        if "render_variant" not in policy:
                            render_policy_input.pop("render_variant", None)
                        render_policy = resolve_logo_render_policy(
                            _existing_path(slide_data.get("image_path"), output_dir),
                            logo_path_for_overlay,
                            logo_symbol_path_for_overlay,
                            str(slide_data.get("type") or "content").lower(),
                            policy.get("placement") or logo_config.get("anchor") or "top-right",
                            policy.get("scale") or "small",
                            render_policy_input,
                        )
                        policy.update({k: v for k, v in render_policy.items() if v is not None})
                        visual["logo_policy"] = policy
                        slide_data["visual_json"] = visual
                        slide_model = slides_by_page.get(slide_data.get("page_num"))
                        if slide_model:
                            slide_model.visual_json = visual
                            flag_modified(slide_model, "visual_json")
                    db.commit()
                overlay_assets = {}
                for ref in project.reference_images or []:
                    resolved = _resolve_file_path(ref.file_path)
                    if os.path.exists(resolved):
                        overlay_assets[str(ref.id)] = {
                            "file_path": resolved,
                            "asset_name": ref.asset_name,
                            "asset_kind": ref.asset_kind,
                        }
                    else:
                        logger.warning(
                            "Pipeline: overlay asset %s file not found: %s (resolved: %s)",
                            ref.id, ref.file_path, resolved,
                        )
                if overlay_assets:
                    logger.info(
                        "Pipeline: overlay_assets 构建完成，共 %s 个素材: %s",
                        len(overlay_assets),
                        list(overlay_assets.keys()),
                    )

                # 所有后贴元素共享同一套文字避让：精准粘贴和 Logo 页面都要检测。
                slides_needing_placement_safety = [
                    sd for sd in all_completed_images
                    if _slide_needs_placement_safety(
                        sd,
                        logo_available=bool(logo_path_for_overlay),
                    )
                ]
                if slides_needing_placement_safety:
                    from app.services.text_region_detector import detect_text_regions
                    logger.info(
                        "Pipeline: 开始并发检测 %s 页的文字区域",
                        len(slides_needing_placement_safety),
                    )

                    def _detect_for_slide(sd):
                        img_path = sd.get("image_path")
                        if not img_path:
                            return sd["page_num"], None
                        try:
                            regions = detect_text_regions(img_path)
                            return sd["page_num"], regions
                        except Exception as e:
                            logger.warning(
                                "Pipeline: 文字检测失败 page=%s: %s",
                                sd.get("page_num"), e,
                            )
                            return sd["page_num"], None

                    slide_data_map = {sd["page_num"]: sd for sd in all_completed_images}
                    with ThreadPoolExecutor(max_workers=3) as executor:
                        futures = [
                            executor.submit(_detect_for_slide, sd)
                            for sd in slides_needing_placement_safety
                        ]
                        for future in as_completed(futures):
                            page_num, regions = future.result()
                            if regions is not None:
                                slide_data = slide_data_map[page_num]
                                slide_data["text_regions"] = regions
                                visual = dict(slide_data.get("visual_json") or {})
                                visual["detected_text_regions"] = regions
                                slide_data["visual_json"] = visual
                                slide_model = slides_by_page.get(page_num)
                                if slide_model:
                                    slide_model.visual_json = visual
                                    flag_modified(slide_model, "visual_json")
                                logger.info(
                                    "Pipeline: page %s 检测到 %s 个文字区域",
                                    page_num, len(regions),
                                )
                    db.commit()

                assemble_pptx(
                    slide_images=all_completed_images,
                    output_path=pptx_path,
                    logo_config=logo_config,
                    overlay_assets=overlay_assets,
                )
                # 组装器已经算出碰撞安全的最终坐标。把它写回页面数据，确保网页预览
                # 与导出的 PPT 使用同一结果，而不是继续显示旧的预设位置。
                for slide_data in all_completed_images:
                    slide_model = slides_by_page.get(slide_data.get("page_num"))
                    if slide_model:
                        slide_model.visual_json = slide_data.get("visual_json") or {}
                        flag_modified(slide_model, "visual_json")
                db.commit()
                logger.info(f"Pipeline: PPTX 组装完成 {pptx_path}")
            except Exception as e:
                logger.error(f"Pipeline: PPTX 组装失败: {e}")
                # Assembly failures should not put the workflow in a synthetic
                # phase. Keep the slide generation facts and surface the error
                # on the run instead.
                assembly_error = str(e)[:500]
                db.commit()
            finally:
                try:
                    redis_client.delete(pptx_lock_key)
                except Exception as exc:
                    logger.warning("Pipeline: failed to release PPTX assembly lock: %s", exc)
        else:
            logger.info(f"Pipeline: 跳过 PPTX 组装，另一任务正在组装")

    # 状态流转：基于所有 slide 的实际状态判定，不依赖 original_status（并发安全）
    all_slide_statuses = [s.status for s in slides]
    generating_count = sum(1 for st in all_slide_statuses if st == "generating")
    completed_count = sum(1 for st in all_slide_statuses if st == "completed")
    failed_count = sum(1 for st in all_slide_statuses if st == "failed")
    total_target, target_completed, target_failed = progress_counts()
    target_errors = [
        str(s.error_msg).strip()
        for s in target_slides
        if s.status == "failed" and s.error_msg and str(s.error_msg).strip()
    ]
    failure_summary = abort_generation_error or (target_errors[0] if target_errors else "部分页面生成失败")

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
            else image_generation_progress_message(run_stage, target_completed, total_target, target_failed).replace("正在", "", 1)
        ),
        completed_count=target_completed,
        failed_count=target_failed,
        error_msg=assembly_error or (failure_summary if target_failed > 0 else None),
    )
    db.commit()
    append_image_generation_log(
        project_id,
        run_id,
        "pipeline_finished",
        run_status=run_status,
        project_status=project.status,
        target_completed=target_completed,
        target_failed=target_failed,
        total_target=total_target,
        completed_count=completed_count,
        failed_count=failed_count,
        generating_count=generating_count,
        assembly_error=assembly_error,
        failure_summary=failure_summary if target_failed > 0 else None,
        log_path=log_path,
    )
    logger.info(f"Pipeline: 项目状态流转 -> {project.status} (generating={generating_count}, completed={completed_count}, failed={failed_count})")

    if not all_completed_images:
        logger.warning(f"Pipeline: 没有成功生成的图片，无法组装 PPTX")

    # 清理内存中的 generation_progress，避免任务结束后端点仍返回旧数据
    cleanup_generation_progress(project_id)
    clear_reference_upload_cache()
