import logging
import re
from typing import Dict, List, Optional

from app.services.overlay_layers import (
    enabled_overlay_layers,
    overlay_reservation_instruction,
)
from app.services.section_text import sanitize_section_visual_numbering, should_render_section_title
from app.services.visual_directives import (
    extract_visual_directives,
    normalize_visual_requirements,
)
from app.utils.text_cleaning import is_markdown_thematic_break_line, normalize_markdown_emphasis

logger = logging.getLogger(__name__)

BRAND_MARK_DRAWING_TERMS = (
    "logo", "wordmark", "lockup", "标识", "徽标", "角标", "小logo", "小 Logo",
    "品牌标识", "品牌角标", "品牌抽象", "展翅", "翅膀", "翼形", "飞翼",
)
WATERMARK_TERMS = ("虎课", "虎课网", "watermark", "水印", "stock", "template watermark")


def _strip_markdown(text: str) -> str:
    """去除常见 Markdown 标记，保留表格结构供生图模型识别。"""
    if not text:
        return text
    text = normalize_markdown_emphasis(text)

    lines = text.splitlines()
    cleaned_lines = []
    in_table = False
    for line in lines:
        stripped = line.strip()
        if is_markdown_thematic_break_line(stripped):
            continue
        # 跳过 Markdown 表格分隔线（如 | --- | :---: |）
        if re.match(r'^\|?[\s:\-]+(?:\|[\s:\-]+)+\|?$', stripped):
            continue
        # 表格行：保留管道符结构，生图模型需要识别为表格
        if stripped.count('|') >= 2:
            cells = [c.strip() for c in stripped.split('|')]
            # 去掉首尾空单元格（由行首行尾的 | 产生）
            cells = [c for c in cells if c]
            if cells:
                if not in_table:
                    cleaned_lines.append("[表格]")
                    in_table = True
                cleaned_lines.append(' | '.join(cells))
            else:
                cleaned_lines.append(stripped)
        else:
            in_table = False
            cleaned_lines.append(stripped)

    text = '\n'.join(cleaned_lines)

    # 去除加粗/斜体
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    # 去除残余的未配对强调符，避免进入图片模型的文字合同。
    text = text.replace("**", "").replace("__", "")
    # 去除行首列表符号和引用符号
    text = re.sub(r'^\s*[-*]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*>\s+', '', text, flags=re.MULTILINE)
    # 去除行首标题符号
    text = re.sub(r'^\s*#{1,6}\s+', '', text, flags=re.MULTILINE)
    cleaned_lines = [line for line in text.splitlines() if not is_markdown_thematic_break_line(line)]
    return "\n".join(cleaned_lines).strip()


def _image_typography_line(line: str) -> str:
    """Reduce typography metadata to visual intent so font names are not drawn."""
    raw = str(line or "")
    lowered = raw.lower()
    cues: list[str] = []
    if any(token in raw for token in ("衬线", "宋体", "明朝")) or "serif" in lowered:
        cues.append("serif-flavored headline option")
    if (
        any(token in raw for token in ("无衬线", "黑体", "思源黑体", "苹方"))
        or any(token in lowered for token in ("sans", "inter", "source han", "helvetica", "arial", "roboto", "san francisco"))
    ):
        cues.append("clean sans-serif hierarchy")
    if any(token in raw for token in ("粗", "大字", "标题")) or any(token in lowered for token in ("bold", "heavy", "semibold", "headline")):
        cues.append("strong headline weight")
    if any(token in raw for token in ("正文", "高可读")) or any(token in lowered for token in ("body", "regular", "readable")):
        cues.append("readable body copy")
    if not cues:
        cues.append("clear presentation hierarchy")
    unique_cues = list(dict.fromkeys(cues))[:3]
    return "Typography: " + ", ".join(unique_cues) + "; do not render or spell out font family names."


def _compact_style_pack(style_text: str, max_lines: int = 6, max_chars: int = 760) -> str:
    """Keep the global style useful but short so page evidence stays dominant."""
    if not style_text:
        return (
            "Style: 由页面内容自然决定\n"
            "Palette: 由主题和场景自然选择\n"
            "Mood: 贴合当前页面内容气质\n"
            "Typography: 由风格气质决定字体搭配\n"
            "Visual rhythm: 每页由文案决定画面证据"
        )
    lines = [line.strip() for line in style_text.splitlines() if line.strip()]
    priority_by_key: dict[str, str] = {}
    # Keep the semantic contract before cosmetic details. Visual rhythm is where
    # topic-specific subject anchors usually live, so it must survive compaction.
    keywords = (
        "Style:", "Palette:", "Mood:", "Visual rhythm:",
        "Texture/material:", "Typography:",
    )
    for line in lines:
        for keyword in keywords:
            if line.startswith(keyword):
                if keyword == "Typography:":
                    cleaned = _image_typography_line(line)
                else:
                    cleaned = _remove_brand_mark_drawing_language(
                        _remove_microcopy_clauses(_remove_negative_clauses(line))
                    )
                if cleaned and keyword not in priority_by_key:
                    priority_by_key[keyword] = cleaned
                break
    priority = [priority_by_key[keyword] for keyword in keywords if keyword in priority_by_key]
    compact_lines = (priority or lines)[:max_lines]
    compact = "\n".join(compact_lines)
    if len(compact) > max_chars:
        compact = compact[:max_chars].rstrip() + "..."
    return compact


def _remove_brand_mark_drawing_language(text: str) -> str:
    """Strip positive instructions that would make the image model redraw logos or watermarks."""
    cleaned: list[str] = []
    for clause in re.split(r"[。；;\n]+", str(text or "")):
        value = clause.strip()
        if not value:
            continue
        compact = re.sub(r"\s+", "", value).lower()
        if any(re.sub(r"\s+", "", term).lower() in compact for term in WATERMARK_TERMS):
            continue
        if any(re.sub(r"\s+", "", term).lower() in compact for term in BRAND_MARK_DRAWING_TERMS):
            continue
        cleaned.append(value)
    return "；".join(cleaned).strip()

_PRODUCT_DETAIL_MARKERS = (
    "5升", "5L", "桶身", "瓶身", "瓶盖", "瓶颈", "提手", "吊牌",
    "标签", "标贴", "红底", "金边", "书法字体", "非遗", "透明",
    "金黄", "金色", "包装文字", "完整保留", "完整展示",
    "产品", "产品实物", "产品图", "产品照片", "包装", "瓶型", "具体产品", "品牌产品",
    "品牌名称", "产品名称", "品牌标识", "品牌资产", "品牌识别",
    "文化符号", "品质背书", "视觉锚点",
)


def _split_clauses(text: str) -> list[str]:
    """Split loosely on punctuation while keeping useful short clauses."""
    return [part.strip(" ，,。；;") for part in re.split(r"[。；;]\s*", str(text or "")) if part.strip()]


def _has_visible_content_value(value) -> bool:
    if isinstance(value, str):
        return bool(_strip_markdown(value).strip())
    if isinstance(value, list):
        return any(_has_visible_content_value(item.get("content") if isinstance(item, dict) else item) for item in value)
    return bool(value)


def _strip_absent_text_slot_clauses(text: str, content_text: Optional[Dict] = None) -> str:
    """Remove layout prose that asks for text slots the slide content does not have."""
    if not text:
        return str(text or "")
    content_text = content_text or {}
    has_subhead = _has_visible_content_value(content_text.get("subhead"))
    has_body = _has_visible_content_value(content_text.get("body"))
    if has_subhead and has_body:
        return str(text)

    subhead_markers = ("副标题", "小标题", "subtitle", "subhead")
    kept: list[str] = []
    for clause in _split_clauses(text):
        lowered = clause.lower()
        mentions_subhead = any(marker in lowered for marker in subhead_markers)
        mentions_body = any(marker.lower() in lowered for marker in (*_BODY_SLOT_MARKERS, *_MICROCOPY_MARKERS))
        mentions_info_block = any(marker.lower() in lowered for marker in _INFO_BLOCK_MARKERS)
        if mentions_subhead and not has_subhead:
            continue
        if mentions_body and not has_body:
            continue
        if mentions_info_block and not has_body:
            continue
        kept.append(clause)
    return "；".join(kept).strip()


_INFO_BLOCK_MARKERS = (
    "信息块", "信息区", "提示块", "提示框", "说明框", "浅黄色", "黄色信息",
    "info block", "note block", "callout", "yellow note", "footer strip",
)

_BODY_SLOT_MARKERS = (
    "正文", "正文要点", "正文文字", "正文文案", "文字内容",
    "简介文字", "介绍文字", "说明内容", "要点", "文案",
    "body", "body copy", "body text", "copy", "points",
)

_BODY_CONTAINER_ACTION_MARKERS = (
    "承载", "容纳", "收拢", "放入", "放置", "展示", "呈现", "内含", "包含", "贴合",
    "carry", "carries", "contain", "contains", "hold", "holds", "show", "shows",
)

_INFO_BLOCK_DECORATIVE_MARKERS = (
    "装饰", "页码", "短标签", "label-only", "decorative", "page number",
)

_MULTI_VISUAL_SUBJECT_MARKERS = (
    "多张", "多个", "多图", "三图", "拼贴", "辅图", "小图", "小尺寸",
    "照片组合", "照片卡片", "环绕", "叠放", "错位", "分栏", "对比",
    "集合", "三宝", "作品", "馆藏", "机位", "餐厅", "橱窗", "门面",
    "主图与辅图", "1-2张", "0-2张", "2张", "3张",
    "collage", "multiple", "comparison", "grid", "cards",
)


def _body_items(content_text: Optional[Dict]) -> list[str]:
    body = (content_text or {}).get("body")
    raw_items: list[str]
    if isinstance(body, str):
        raw_items = [line.strip() for line in body.splitlines() if line.strip()]
    elif isinstance(body, list):
        raw_items = [
            str(item.get("content") if isinstance(item, dict) else item).strip()
            for item in body
            if str(item.get("content") if isinstance(item, dict) else item).strip()
        ]
    else:
        raw_items = []
    return [_strip_markdown(item) for item in raw_items if _strip_markdown(item)]


def _body_item_anchor(item: str) -> str:
    value = _strip_markdown(item).strip()
    if not value:
        return ""
    artwork_match = re.match(r"^(《[^》]{1,36}》)", value)
    if artwork_match:
        return artwork_match.group(1).strip()
    if "：" in value or ":" in value:
        label = re.split(r"[：:]", value, maxsplit=1)[0].strip()
        if 2 <= len(label) <= 48:
            return label
    return ""


def _body_item_anchors(content_text: Optional[Dict]) -> list[str]:
    anchors: list[str] = []
    for item in _body_items(content_text):
        anchor = _body_item_anchor(item)
        if anchor and anchor not in anchors:
            anchors.append(anchor)
    return anchors


def _slot_text(value, max_chars: int = 80) -> str:
    cleaned = _strip_markdown(str(value or "")).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip() + "..."
    return cleaned


def _slot_links(value) -> list[str]:
    if isinstance(value, str):
        raw_links = [part.strip() for part in re.split(r"[,，;；\n]+", value) if part.strip()]
    elif isinstance(value, list):
        raw_links = [str(item or "").strip() for item in value if str(item or "").strip()]
    else:
        raw_links = []
    links: list[str] = []
    for link in raw_links:
        cleaned = _slot_text(link, 48)
        if cleaned and cleaned not in links:
            links.append(cleaned)
    return links[:4]


def _normalize_image_slots(page_intent: Dict) -> list[Dict]:
    raw_slots = (page_intent or {}).get("image_slots") or []
    if not isinstance(raw_slots, list):
        return []

    slots: list[Dict] = []
    for index, raw_slot in enumerate(raw_slots[:8]):
        if not isinstance(raw_slot, dict):
            continue
        subject = _slot_text(
            raw_slot.get("subject")
            or raw_slot.get("visual")
            or raw_slot.get("label")
            or raw_slot.get("description"),
            86,
        )
        if not subject:
            continue
        slot_id = _slot_text(raw_slot.get("id") or chr(ord("A") + index), 16)
        linked_text = _slot_links(
            raw_slot.get("linked_text")
            or raw_slot.get("linked_body")
            or raw_slot.get("links")
            or raw_slot.get("caption_anchor")
        )
        slots.append({
            "id": slot_id,
            "subject": subject,
            "role": _slot_text(raw_slot.get("role"), 28),
            "position": _slot_text(raw_slot.get("position") or raw_slot.get("placement"), 56),
            "shape": _slot_text(raw_slot.get("shape") or raw_slot.get("frame"), 48),
            "linked_text": linked_text,
            "strictness": _slot_text(raw_slot.get("strictness"), 16),
        })
    return slots


def _has_explicit_image_slot_links(page_intent: Dict) -> bool:
    return any(slot.get("linked_text") for slot in _normalize_image_slots(page_intent))


def _image_slot_map_instruction(page_intent: Dict) -> str:
    slots = _normalize_image_slots(page_intent)
    if not slots:
        return ""

    lines: list[str] = []
    for slot in slots:
        attrs: list[str] = []
        if slot.get("role"):
            attrs.append(f"role={slot['role']}")
        if slot.get("position"):
            attrs.append(f"position={slot['position']}")
        if slot.get("shape"):
            attrs.append(f"shape={slot['shape']}")
        if slot.get("linked_text"):
            attrs.append("linked text=" + ", ".join(slot["linked_text"]))
        if slot.get("strictness"):
            attrs.append(f"strictness={slot['strictness']}")
        suffix = "; " + "; ".join(attrs) if attrs else ""
        lines.append(f"{slot['id']}: {slot['subject']}{suffix}")

    lines.append("Use Slot map as the source of truth for image placement and caption association.")
    return "\n".join(lines)


def _should_bind_body_to_visual_subjects(page_intent: Dict, content_text: Optional[Dict]) -> bool:
    """Use caption-like body when multiple body items map to multiple visual subjects."""
    items = _body_items(content_text)
    if len(items) < 2:
        return False
    if len(_body_item_anchors(content_text)) < 2:
        return False
    source = " ".join(
        str((page_intent or {}).get(key) or "")
        for key in ("visual_evidence", "visual_summary", "visual_description", "design_notes")
    ).lower()
    if not any(marker.lower() in source for marker in _MULTI_VISUAL_SUBJECT_MARKERS):
        return False
    return True


def _composition_binding_instruction(page_intent: Dict, content_text: Optional[Dict], body_label: str) -> str:
    if body_label != "Linked caption body":
        return ""
    if _normalize_image_slots(page_intent):
        return (
            "Place each Linked caption body close to the Slot map photo it belongs to, using the slot IDs or short "
            "caption chips consistently. Let photo cards and captions interleave across the center instead of forming "
            "a detached bottom tag bar or a rigid left-text/right-image split."
        )
    item_labels = _body_item_anchors(content_text)[:8]
    label_text = "、".join(item_labels[:6])
    label_clause = f" Match these caption anchors to corresponding photos/subjects: {label_text}." if label_text else ""
    return (
        "Image-text binding: use an interleaved composition. Place each Linked caption body near its matching photo "
        "or visual subject, using small numbered markers, caption chips, or short connector lines consistently across "
        "text and images. Let photo cards and captions cross the center line so the middle of the slide feels active "
        "and balanced, not split into a detached text side and detached image side."
        + label_clause
    )


def _body_directive_label(page_intent: Dict, style_text: str | None, content_text: Optional[Dict] = None) -> str:
    """Name the body text slot once, based on the visual system's intended container."""
    if _has_explicit_image_slot_links(page_intent) and _body_items(content_text):
        return "Linked caption body"
    if _should_bind_body_to_visual_subjects(page_intent, content_text):
        return "Linked caption body"
    for source in (
        (page_intent or {}).get("visual_description"),
        (page_intent or {}).get("design_notes"),
        style_text,
    ):
        phrases = [
            part.strip()
            for part in re.split(r"[。；;，,\n]+", str(source or ""))
            if part.strip()
        ]
        for idx, phrase in enumerate(phrases):
            lowered = phrase.lower()
            mentions_info_block = any(marker.lower() in lowered for marker in _INFO_BLOCK_MARKERS)
            mentions_body_slot = any(marker.lower() in lowered for marker in _BODY_SLOT_MARKERS)
            if mentions_info_block and mentions_body_slot:
                return "Info block body"
            if not mentions_info_block:
                continue
            if any(marker.lower() in lowered for marker in _INFO_BLOCK_DECORATIVE_MARKERS):
                continue
            next_phrase = phrases[idx + 1].lower() if idx + 1 < len(phrases) else ""
            if (
                any(marker.lower() in next_phrase for marker in _BODY_SLOT_MARKERS)
                and any(marker.lower() in next_phrase for marker in _BODY_CONTAINER_ACTION_MARKERS)
            ):
                return "Info block body"
    return "Body"


def _normalize_body_slot_clauses(text: str, body_label: str = "Body") -> str:
    """
    Keep layout prose about where the text container sits, but remove duplicate
    instructions that describe the same body copy as another text slot.
    """
    if not text or body_label == "Body":
        return str(text or "")

    kept: list[str] = []
    for clause in _split_clauses(text):
        lowered = clause.lower()
        mentions_info_block = any(marker.lower() in lowered for marker in _INFO_BLOCK_MARKERS)
        mentions_body = any(marker.lower() in lowered for marker in _BODY_SLOT_MARKERS)
        if mentions_info_block and mentions_body:
            clause = re.split(
                r"(承载|容纳|收拢|放入|放置|展示|呈现|内含|包含|贴合|用来|作为|carr(?:y|ies)|contain(?:s)?|hold(?:s)?|place(?:s)?|show(?:s)?)",
                clause,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0].strip(" ，,。；;")
        if clause:
            kept.append(clause)
    return "；".join(kept).strip()


def _split_product_clauses(text: str) -> list[str]:
    """Product-related cleanup needs finer cuts than normal copy."""
    return [part.strip(" ：:，,。；;") for part in re.split(r"[。；;，,]\s*", str(text or "")) if part.strip()]


_MICROCOPY_MARKERS = (
    "小字号", "小字", "微文案", "细小文字", "装饰文字", "占位文字",
    "microcopy", "decorative text", "placeholder text", "lorem ipsum",
)

_REFERENCE_METADATA_MARKERS = (
    "asset=", "source=", "classification=", "area_ratio=", "source_slide_text=",
    "tags=", "usage=", "group=", "ppt_page_", ".pptx", "AI参考", "AI 参考",
)


def _looks_like_internal_reference_metadata(clause: str) -> bool:
    compact = re.sub(r"\s+", "", str(clause or "")).lower()
    if re.search(r"参考图\d+", compact):
        return True
    return any(marker.lower().replace(" ", "") in compact for marker in _REFERENCE_METADATA_MARKERS)


def _strip_internal_reference_metadata(text: str) -> str:
    """Remove pipeline/PPT reference bookkeeping before sending text to image models."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    clauses = _split_clauses(raw)
    if not clauses:
        return raw
    kept = [clause for clause in clauses if not _looks_like_internal_reference_metadata(clause)]
    return "；".join(kept).strip()


def _remove_microcopy_clauses(text: str) -> str:
    clauses = _split_clauses(text)
    if not clauses:
        return str(text or "").strip()
    kept = [
        clause for clause in clauses
        if not any(marker in clause.lower() for marker in _MICROCOPY_MARKERS)
    ]
    return "；".join(kept).strip()


def _remove_negative_clauses(text: str) -> str:
    """保留原文，不再过滤含否定词的分句。LLM 应自行理解并遵守用户的否定/约束指令。"""
    return str(text or "").strip()


def _is_product_ref(ref: Dict) -> bool:
    if (ref or {}).get("role") != "visual_asset":
        return False
    return str((ref or {}).get("asset_kind") or "").lower() in {"product", "material"}


def _is_punchline_page(page_intent: Dict) -> bool:
    page_type = str((page_intent or {}).get("type") or "").strip().lower()
    layout = str((page_intent or {}).get("layout") or "").strip().lower()
    return page_type in {"hero", "quote"} or layout == "hero"


def _has_product_ref(reference_images: Optional[List[Dict]]) -> bool:
    return any(_is_product_ref(ref) for ref in reference_images or [])


def _product_placement_instruction(text: str) -> str:
    """Convert noisy product placement prose into one compact model-facing line."""
    raw = str(text or "")
    position = ""
    if any(marker in raw for marker in ("中央偏左", "视觉中心偏左", "中心偏左")):
        position = "center-left"
    elif any(marker in raw for marker in ("中央偏右", "视觉中心偏右", "中心偏右")):
        position = "center-right"
    elif any(marker in raw for marker in ("中央偏下", "中下", "画面下方中央")):
        position = "lower center"
    elif "右下角" in raw:
        position = "bottom-right"
    elif "左下角" in raw:
        position = "bottom-left"
    elif "左上角" in raw:
        position = "top-left"
    elif "右上角" in raw:
        position = "top-right"
    elif any(marker in raw for marker in ("居中", "中央", "视觉中心", "居中展示")):
        position = "center"
    elif "时间轴下方" in raw:
        position = "below the timeline"
    elif "右侧" in raw and any(marker in raw for marker in ("放置", "放在", "置于", "展示")):
        position = "right side"
    elif "左侧" in raw and any(marker in raw for marker in ("放置", "放在", "置于", "展示")):
        position = "left side"
    elif any(marker in raw for marker in ("侧边", "页面边缘", "信息区边缘")):
        position = "a side area"
    if not position:
        return ""

    scale = ""
    if any(marker in raw for marker in ("次要", "小区域", "补充露出", "无需占据过大")):
        scale = "small secondary"
    elif any(marker in raw for marker in ("核心", "主视觉", "视觉锚点", "视觉重心")):
        scale = "large unobstructed"

    scale_prefix = f"{scale} " if scale else ""
    return f"Place the uploaded product image in the {scale_prefix}{position} area."


def _sanitize_product_reference_text(text: str) -> str:
    """
    Keep only scene/layout language and generic placement. The uploaded image
    carries product identity; text must not reconstruct or embellish it.
    """
    cleaned: list[str] = []
    for clause in _split_product_clauses(text):
        clause = _remove_negative_clauses(clause)
        if not clause:
            continue
        if any(marker in clause for marker in _PRODUCT_DETAIL_MARKERS):
            placement = _product_placement_instruction(clause)
            if placement and placement not in cleaned:
                cleaned.append(placement)
            continue
        cleaned.append(clause)
    return "；".join(cleaned).strip()


def _compact_visual_evidence(page_intent: Dict, reference_images: Optional[List[Dict]] = None) -> str:
    visual_evidence = str(page_intent.get("visual_evidence", "") or "").strip()
    visual_evidence = _strip_internal_reference_metadata(visual_evidence)
    if _has_product_ref(reference_images):
        visual_evidence = _sanitize_product_reference_text(visual_evidence)
    visual_evidence = _remove_brand_mark_drawing_language(visual_evidence)
    return visual_evidence or "Use the uploaded product image as the product source, with supporting visuals derived from this slide's content."


def _compact_visual_evidence_with_style(
    page_intent: Dict,
    reference_images: Optional[List[Dict]],
    style_text: str | None,
    content_text: Optional[Dict] = None,
    body_label: str = "Body",
) -> str:
    visual_evidence = str(page_intent.get("visual_evidence", "") or "").strip()
    visual_evidence = _strip_internal_reference_metadata(visual_evidence)
    if _has_product_ref(reference_images):
        visual_evidence = _sanitize_product_reference_text(visual_evidence)
    visual_evidence = _remove_brand_mark_drawing_language(visual_evidence)
    visual_evidence = _strip_absent_text_slot_clauses(visual_evidence, content_text)
    visual_evidence = _normalize_body_slot_clauses(visual_evidence, body_label)
    if str((page_intent or {}).get("type") or "").strip().lower() == "section":
        visual_evidence = sanitize_section_visual_numbering(visual_evidence)
    return visual_evidence or "Use the uploaded product image as the product source, with supporting visuals derived from this slide's content."


def _compact_layout_intent(
    page_intent: Dict,
    reference_images: Optional[List[Dict]] = None,
    style_text: str | None = None,
    content_text: Optional[Dict] = None,
    body_label: str = "Body",
) -> str:
    layout = page_intent.get("layout") or page_intent.get("type", "content")
    visual_desc = " ".join(str(page_intent.get("visual_description", "")).split())
    visual_desc = _strip_internal_reference_metadata(visual_desc)
    visual_desc = _remove_negative_clauses(visual_desc)
    if _has_product_ref(reference_images):
        visual_desc = _sanitize_product_reference_text(visual_desc)
    visual_desc = _remove_brand_mark_drawing_language(visual_desc)
    visual_desc = _strip_absent_text_slot_clauses(visual_desc, content_text)
    visual_desc = _normalize_body_slot_clauses(visual_desc, body_label)
    if str((page_intent or {}).get("type") or "").strip().lower() == "section":
        visual_desc = sanitize_section_visual_numbering(visual_desc)
    if len(visual_desc) > 260:
        visual_desc = visual_desc[:260].rstrip() + "..."

    if visual_desc:
        return f"Layout: {layout}. {visual_desc}"
    return f"Layout: {layout}. Arrange text and visual evidence with clear hierarchy and strong readability."


def _compact_reference_text(text: str, max_chars: int = 260) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def _reference_context_text(text: str, max_chars: int = 180) -> str:
    cleaned = _strip_internal_reference_metadata(text)
    return _compact_reference_text(cleaned, max_chars) if cleaned else ""


def _content_visual_contract(content_text: Dict) -> tuple[Dict, list[str], list[str]]:
    """Split body copy into visible text, visual intent, and diagram labels."""
    next_content = dict(content_text or {})
    body = next_content.get("body")
    visual_intents: list[str] = []
    diagram_labels: list[str] = []

    def add_requirement(requirement: Dict) -> None:
        directive = str(requirement.get("directive") or "").strip()
        if directive and directive not in visual_intents:
            visual_intents.append(directive)
        for label in requirement.get("diagram_labels") or []:
            value = str(label or "").strip()
            if value and value not in diagram_labels:
                diagram_labels.append(value)

    for requirement in normalize_visual_requirements(next_content.get("visual_requirements")):
        add_requirement(requirement)

    if isinstance(body, str):
        extraction = extract_visual_directives(body)
        next_content["body"] = extraction["cleaned_markdown"]
        for suggestion in extraction["suggestions"]:
            add_requirement(suggestion)
    elif isinstance(body, list):
        cleaned_items = []
        for item in body:
            extraction = extract_visual_directives(str(item or ""))
            if extraction["cleaned_markdown"]:
                cleaned_items.append(extraction["cleaned_markdown"])
            for suggestion in extraction["suggestions"]:
                add_requirement(suggestion)
        next_content["body"] = cleaned_items

    return next_content, visual_intents, diagram_labels


def _is_protected_asset(ref: Dict) -> bool:
    """Identity-locked assets that must NOT be redrawn or reinterpreted."""
    role = (ref or {}).get("role", "")
    if role == "logo":
        return True
    if _is_product_ref(ref):
        return True
    return False


def _protected_asset_priority(ref: Dict) -> int:
    if _is_product_ref(ref):
        return 0
    if (ref or {}).get("role") == "logo":
        return 1
    return 9


def _reference_priority(ref: Dict) -> int:
    role = (ref or {}).get("role", "")
    if _is_product_ref(ref):
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


def _protected_assets_block(reference_images: Optional[List[Dict]]) -> str:
    if not reference_images:
        return ""
    protected = sorted(
        [ref for ref in reference_images if _is_protected_asset(ref)],
        key=_protected_asset_priority,
    )
    if not protected:
        return ""

    lines = []
    for idx, ref in enumerate(protected, start=1):
        role = ref.get("role", "")
        if role == "logo":
            label = ref.get("asset_name") or "Logo / lockup"
            rule = (
                "overlay-only identity asset; do not draw or reinterpret it in the base image."
            )
        elif _is_product_ref(ref):
            label = ref.get("asset_name") or "Product"
            if str(ref.get("asset_route_mode") or "").lower() == "blend":
                rule = "use the uploaded product image as a natural scene reference."
            else:
                rule = "use the uploaded product image as the product source; a hidden refinement pass strengthens fidelity."
        lines.append(f"{idx}. {label} — {rule}")

    return "Assets:\n" + "\n".join(lines)


def _valid_overlay_asset_ids(page_intent: Dict) -> set[str] | None:
    if not isinstance(page_intent, dict):
        return None
    raw = page_intent.get("available_overlay_asset_ids")
    if raw is None:
        return None
    if isinstance(raw, (list, tuple, set)):
        return {str(item) for item in raw if item}
    return set()


def _brand_mark_safety_instruction(page_intent: Dict) -> str:
    return ""


def _reference_descriptions_for_prompt(
    page_intent: Dict,
    content_text: Dict,
    reference_images: Optional[List[Dict]],
) -> list[str]:
    reference_descriptions: list[str] = []
    ref_context = (content_text or {}).get("reference_context") or (page_intent or {}).get("reference_context")
    if ref_context:
        detail = _reference_context_text(ref_context, 220)
        reference_descriptions.append(
            "Page reference: follow this uploaded visual."
            + (f" Context: {detail}" if detail else "")
        )

    if reference_images:
        for img in sorted(reference_images, key=_reference_priority):
            role = img.get("role", "style_ref")
            desc = img.get("description", "")
            process_mode = img.get("process_mode", "")
            if role == "style_ref":
                reference_descriptions.append(
                    "Style reference: borrow only mood, palette, and composition rhythm."
                )
            elif role == "logo":
                reference_descriptions.append(
                    "Uploaded identity asset: use only when explicitly requested as a scene object."
                )
            elif role == "content_ref":
                detail = _reference_context_text(desc, 180) if desc else ""
                reference_descriptions.append(
                    "Page reference: use this uploaded image as the page visual source."
                    + (f" Context: {detail}" if detail else "")
                )
            elif role == "chart_ref":
                detail = _reference_context_text(desc, 180) if desc else ""
                reference_descriptions.append(
                    "Chart/data reference: follow this uploaded chart for the chart area. "
                    "Preserve its core structure, node labels, arrows, and table relationships."
                    + (f" Context: {detail}" if detail else "")
                )
            elif role == "visual_asset":
                asset_name = img.get("asset_name") or "visual asset"
                asset_kind = img.get("asset_kind") or "other"
                route_mode = str(img.get("asset_route_mode") or "").lower()
                if route_mode == "overlay":
                    continue
                usage_map = page_intent.get("visual_asset_usage") if isinstance(page_intent, dict) else {}
                page_usage = ""
                if isinstance(usage_map, dict) and img.get("id") in usage_map:
                    page_usage = str(usage_map.get(img.get("id")) or "")
                if route_mode == "double_blend":
                    rule = (
                        f"Product slot: {asset_name}. Use the uploaded product image as the product source; product fidelity is reinforced in a hidden refinement pass."
                    )
                elif str(asset_kind).lower() in {"product", "material"}:
                    rule = (
                        f"Product slot: {asset_name}. Blend the uploaded product image naturally into the scene while preserving its core identity."
                    )
                else:
                    rule = (
                        f"Visual asset: {asset_name}. Use the uploaded image as the visual source."
                    )
                if page_usage:
                    placement = _sanitize_product_reference_text(page_usage) if str(asset_kind).lower() in {"product", "material"} else _remove_negative_clauses(page_usage)
                    if placement:
                        placement_text = _compact_reference_text(placement, 100).rstrip(".")
                        rule += f" Placement/use: {placement_text}."
                reference_descriptions.append(rule)
            elif role == "seed_ref":
                reference_descriptions.append(
                    "Seed page: copy layout DNA only (grid, hierarchy, palette rhythm). "
                    "Do not copy seed text, body imagery, product shots, or logo unless this page has its own uploaded logo."
                )
            elif role == "template":
                strength = str(img.get("application_strength") or "standard").lower()
                if strength == "strong":
                    reference_descriptions.append(
                        "Template page: stay very close to the template's layout, color palette, typography rhythm, and visual mood. "
                        "Replace old content with this slide's own text; do not copy old images or logos."
                    )
                elif strength == "standard":
                    reference_descriptions.append(
                        "Template page: borrow page layout plus color palette and typography feel. "
                        "Use this slide's own subject and evidence; do not copy old text, old images, or old logos."
                    )
                else:  # light
                    reference_descriptions.append(
                        "Template page: borrow layout only: text zones, image zones, card/grid placement, and hierarchy. "
                        "Do not borrow template colors, old text, old images, or old logos."
                    )
    return [line.strip() for line in reference_descriptions if line and line.strip()]


def generate_prompt_for_page(
    page_intent: Dict,
    content_text: Dict,
    style_id: str = "default",
    reference_images: Optional[List[Dict]] = None,
    style_text_override: Optional[str] = None,
    user_feedback: Optional[str] = None,
) -> str:
    """
    为一页生成 Final Image Prompt。
    输入：Visual Plan Intent + Content + Style + References
    输出：自然流畅的 Final Prompt 字符串
    """
    logger.info(f"PromptEngine: 为第 {page_intent.get('page_num')} 页生成 Final Prompt")
    content_text, visual_intents, diagram_labels = _content_visual_contract(content_text or {})

    if style_text_override is not None:
        style_text = style_text_override
    elif isinstance(page_intent, dict) and page_intent.get("style_pack_snapshot"):
        style_text = str(page_intent.get("style_pack_snapshot") or "")
    else:
        style_text = (
            "Style: 由页面内容自然决定\n"
            "Palette: 由主题和场景自然选择\n"
            "Mood: 贴合当前页面内容气质\n"
            "Typography: 由风格气质决定字体搭配\n"
            "Visual rhythm: 每页由文案决定画面证据"
        )

    reference_descriptions = _reference_descriptions_for_prompt(page_intent, content_text or {}, reference_images)
    body_label = _body_directive_label(page_intent, style_text, content_text)

    # 强制追加文字渲染指令（确保文字一定出现在图片上）
    # 外层用单引号包裹用户文本，避免与用户文本中的双引号冲突
    # 同时去除 Markdown 标记（**、- 等），避免模型把格式符号也渲染到图上
    def _escape(text: str) -> str:
        # 只处理会破坏 prompt 结构的反斜杠，保留用户原始引号
        return text.replace("\\", "")

    text_directives = []
    is_punchline_page = _is_punchline_page(page_intent)
    page_type = str((page_intent or {}).get("type") or "").strip().lower()
    section_title = str((content_text or {}).get("section_title") or "").strip()
    if page_type == "section" and should_render_section_title(section_title, content_text):
        label = _escape(_strip_markdown(section_title))
        if label:
            text_directives.append(f'Chapter label: "{label}"')
    if content_text.get("headline"):
        h = _escape(_strip_markdown(content_text["headline"]))
        text_directives.append(f'Headline: "{h}"')
    if content_text.get("subhead"):
        s = _escape(_strip_markdown(content_text["subhead"]))
        text_directives.append(f'Subhead: "{s}"')
    body = content_text.get("body")
    if body and (not is_punchline_page or body_label != "Body"):
        if isinstance(body, str):
            lines = [line.strip() for line in body.splitlines() if line.strip()]
            for item in lines:
                cleaned = _escape(_strip_markdown(item))
                if cleaned:
                    text_directives.append(f'{body_label}: "{cleaned}"')
        else:
            for item in body:
                cleaned = _escape(_strip_markdown(item))
                if cleaned:
                    text_directives.append(f'{body_label}: "{cleaned}"')
    for label in diagram_labels[:16]:
        cleaned = _escape(_strip_markdown(label))
        if cleaned:
            text_directives.append(f'Diagram label: "{cleaned}"')

    if text_directives:
        text_directives.append(
            "Visible text rule: render the quoted strings in this section as required slide copy; "
            "do not render prompt labels, section headers, color codes, invented copy, lorem ipsum, or decorative microtext."
        )
    if visual_intents or diagram_labels:
        text_directives.append("Do not render visual intent phrases as text.")
        text_directives.append("Render diagram labels as visible labels inside the diagram.")
        text_directives.append("Render visible body text only as readable slide copy.")
    visual_intent_section = ""
    if visual_intents:
        visual_intent_section = "\n\nVisual Intent:\n" + "\n".join(
            f"- {_compact_reference_text(intent, 140)}" for intent in visual_intents[:6]
        )

    punchline_treatment = ""
    if is_punchline_page:
        punchline_treatment = (
            "Punchline slide treatment: render only one dominant short line/phrase/word plus minimal context if useful; "
            "do not add bullets, explanatory body copy, charts, dense panels, or unrelated typography. "
            "Use the same project typeface feel, palette, material texture, and decoration language, only with stronger scale and negative space."
        )

    protected_block = _protected_assets_block(reference_images)
    protected_section = f"{protected_block}\n\n" if protected_block else ""
    brand_mark_safety = _brand_mark_safety_instruction(page_intent)
    artifact_safety = (
        "Watermarks and stray marks: no third-party watermarks, stock/template labels, "
        "tutorial-site stamps, 虎课网, or unauthorized extra text."
    )

    # Keep the first-pass prompt compact: visible copy, style, page intent, and
    # short reference roles. Uploaded images carry asset identity; long product
    # descriptions are intentionally omitted.
    text_block = "\n".join(text_directives)
    text_section = f"Visible Text:\n{text_block}\n\n" if text_block else ""
    # 用户在 chat 中对单页的最新反馈（重试时携带）— 必须放在 prompt 最前面，
    # 让模型在生图时优先采纳，不被后续 Style/Visual 规则覆盖。
    user_feedback_text = (user_feedback or "").strip()
    if len(user_feedback_text) > 1500:
        user_feedback_text = user_feedback_text[-1500:]
    user_feedback_section = (
        f"User Feedback (must honor, overrides style/layout defaults):\n{user_feedback_text}\n\n"
        if user_feedback_text
        else ""
    )
    style_block = _compact_style_pack(style_text)
    overlay_layers = enabled_overlay_layers(page_intent)
    visual_evidence = _compact_visual_evidence_with_style(
        page_intent,
        reference_images,
        style_text,
        content_text,
        body_label=body_label,
    )
    layout_intent = _compact_layout_intent(
        page_intent,
        reference_images,
        style_text,
        content_text,
        body_label=body_label,
    )
    slot_map = _image_slot_map_instruction(page_intent)
    composition_binding = _composition_binding_instruction(page_intent, content_text, body_label)
    composition_parts = []
    if slot_map:
        composition_parts.append("Slot map:\n" + slot_map)
    if composition_binding:
        composition_parts.append(composition_binding)
    composition_section = f"\n\nComposition:\n" + "\n".join(composition_parts) if composition_parts else ""
    refs_block = "\n".join(f"- {desc}" for desc in reference_descriptions[:6])
    refs_section = f"\n\nReferences:\n{refs_block}" if refs_block else ""
    overlay_reservation = overlay_reservation_instruction(
        page_intent,
        valid_asset_ids=_valid_overlay_asset_ids(page_intent),
    )
    overlay_section = f"\n\nExact Overlay Reservation:\n{overlay_reservation}" if overlay_reservation else ""
    final_prompt = (
        user_feedback_section
        + text_section
        + protected_section
        + (refs_section.strip() + "\n\n" if refs_section else "")
        + (visual_intent_section.strip() + "\n\n" if visual_intent_section else "")
        + "Rules:\n"
        + ((brand_mark_safety + "\n") if brand_mark_safety else "")
        + artifact_safety
        + "\n\n"
        + "Style:\n"
        + style_block
        + "\n\nVisual:\n"
        + str(visual_evidence)
        + "\n\nLayout:\n"
        + str(layout_intent)
        + composition_section
        + (f"\n{punchline_treatment}" if punchline_treatment else "")
        + overlay_section
        + "\n\nCreate one polished widescreen landscape presentation slide. Keep visible text legible."
    )

    prompt_len = len(final_prompt)
    if prompt_len > 3000:
        logger.warning(f"PromptEngine: 第 {page_intent.get('page_num')} 页 Prompt 过长 ({prompt_len} chars)，可能超出模型有效上下文窗口")
    logger.info(f"PromptEngine: 第 {page_intent.get('page_num')} 页 Prompt 生成完成，长度 {prompt_len}")
    return final_prompt


def generate_prompts_for_all_pages(
    visual_plan: List[Dict],
    content_plan: List[Dict],
    style_id: str = "default",
    reference_images: Optional[List[Dict]] = None,
    reference_images_by_page: Optional[Dict[int, List[Dict]]] = None,
    style_text_override: Optional[str] = None,
    progress_callback: Optional[callable] = None,
) -> List[Dict]:
    """
    为所有页面批量生成 Final Prompt。
    返回每页的 {page_num, prompt} 列表。
    """
    results = []
    total = len(visual_plan)
    # 建立 content_plan 索引（保留完整 item，不只是 text_content）
    content_item_by_page = {item.get("page_num", 0): item for item in content_plan}

    for idx, intent in enumerate(visual_plan):
        page_num = intent.get("page_num", 0)
        if progress_callback:
            progress_callback(f"📝 第 {idx + 1} / {total} 页 Prompt 生成中...")
        content_item = content_item_by_page.get(page_num, {})
        content_text = content_item.get("text_content", {}) or {}
        page_type = str(intent.get("type") or content_item.get("type") or "").strip().lower()
        section_title = str(content_item.get("section_title") or "").strip()
        if page_type == "section" and section_title:
            content_text = {**content_text, "section_title": section_title}
        # 注入页面级参考图上下文（修复参考图丢失）
        if content_item.get("reference_context"):
            content_text = {**content_text, "reference_context": content_item["reference_context"]}
        if content_item.get("reference_user_hint"):
            content_text = {**content_text, "reference_user_hint": content_item["reference_user_hint"]}
        if content_item.get("global_user_requirements"):
            content_text = {**content_text, "global_user_requirements": content_item["global_user_requirements"]}
        if content_item.get("visual_requirements"):
            content_text = {**content_text, "visual_requirements": content_item["visual_requirements"]}
        prompt = generate_prompt_for_page(
            page_intent=intent,
            content_text=content_text,
            style_id=style_id,
            reference_images=(reference_images_by_page or {}).get(page_num, reference_images),
            style_text_override=style_text_override,
        )
        results.append({"page_num": page_num, "prompt": prompt})

    logger.info(f"PromptEngine: 全部 {len(results)} 页 Prompt 生成完成")
    return results
