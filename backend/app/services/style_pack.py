import re
from typing import Dict, List, Optional

from app.services.visual_strategy import build_visual_strategy, visual_strategy_text


def _extract_hex(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    match = re.search(r"#[0-9a-fA-F]{6}", value)
    return match.group(0).upper() if match else None


def _palette_from_reference(reference_analyses: Optional[List[Dict]]) -> list[str]:
    colors: list[str] = []
    for analysis in reference_analyses or []:
        color_map = analysis.get("colors") if isinstance(analysis, dict) else None
        if isinstance(color_map, dict):
            for key in ("background", "primary", "accent", "text"):
                hex_color = _extract_hex(color_map.get(key))
                if hex_color and hex_color not in colors:
                    colors.append(hex_color)
        for item in analysis.get("dominant_palette") or []:
            if isinstance(item, dict):
                hex_color = _extract_hex(item.get("hex"))
                if hex_color and hex_color not in colors:
                    colors.append(hex_color)
    return colors[:5]


def _reference_style_name(reference_analyses: Optional[List[Dict]]) -> str:
    for analysis in reference_analyses or []:
        name = (analysis.get("style_name") or "").strip() if isinstance(analysis, dict) else ""
        if name:
            return name
    return ""


def _reference_value(reference_analyses: Optional[List[Dict]], key: str) -> str:
    values = []
    for analysis in reference_analyses or []:
        if isinstance(analysis, dict) and analysis.get(key):
            values.append(str(analysis[key]).strip())
    return "；".join(v for v in values if v)


def style_pack_from_selected_style(selected_style: dict | str | None) -> str | None:
    if not selected_style:
        return None
    import json

    try:
        style_obj = json.loads(selected_style) if isinstance(selected_style, str) else selected_style
    except Exception:
        return None
    if not isinstance(style_obj, dict):
        return None
    palette = style_obj.get("palette", [])
    if isinstance(palette, list):
        palette_text = ", ".join(
            str(item.get("hex") if isinstance(item, dict) else item)
            for item in palette[:5]
            if item
        )
    else:
        palette_text = str(palette)
    visual_strategy = style_obj.get("visual_strategy") if isinstance(style_obj.get("visual_strategy"), dict) else None
    page_type_adaptation = style_obj.get("page_type_adaptation", "封面/章节页可强化情绪，内容/数据页优先可读")
    visual_strategy_line = visual_strategy_text(visual_strategy)
    if visual_strategy_line and visual_strategy.get("base_tone"):
        visual_strategy_line = f"base_tone={visual_strategy.get('base_tone')}; {visual_strategy_line}"
    texture_line = style_obj.get("texture") or style_obj.get("clone_rules") or ""
    style_rationale = str(style_obj.get("description") or "").strip()
    visual_rhythm = (
        style_obj.get("content_style_hint")
        or style_obj.get("visual_rhythm")
        or style_rationale
        or "每页由文案决定画面证据，风格只统一色彩、材质和装饰强度"
    )
    return "\n".join(
        line for line in [
            f"Style: {style_obj.get('name', '用户确认风格')}",
            f"Palette: {palette_text}",
            f"Mood: {style_obj.get('mood', '保持用户确认的整体气质')}",
            f"Visual strategy: {visual_strategy_line}" if visual_strategy_line else "",
            f"Typography: {style_obj.get('font') or '由风格气质决定字体搭配'}",
            f"Texture/material: {texture_line}" if texture_line else "",
            f"Page type adaptation: {page_type_adaptation}",
            f"Reference usage: {style_obj.get('reference_usage', 'style text only unless template/page references are present')}",
            f"Visual rhythm: {visual_rhythm}",
        ] if line and not line.endswith(": ")
    )


def derive_style_pack_from_content(
    content_plan: List[Dict],
    reference_analyses: Optional[List[Dict]] = None,
) -> str:
    palette = _palette_from_reference(reference_analyses)
    ref_name = _reference_style_name(reference_analyses)
    ref_mood = _reference_value(reference_analyses, "mood")
    ref_font = _reference_value(reference_analyses, "font_suggestion")
    ref_ornaments = _reference_value(reference_analyses, "ornaments")
    ref_clone = _reference_value(reference_analyses, "clone_rules")

    if palette:
        style = ref_name or "参考图风格基因"
        mood = ref_mood or "贴合用户参考图，克制统一"
        typography = ref_font or "由风格气质决定字体搭配"
        page_rule = (
            "封面/章节页可强化情绪，内容/数据页优先可读。"
            "如果参考图是深色风格，信息页在同一深色语言内通过高对比暗色卡片、局部浅色内容区和留白保证阅读效率。"
        )
        return (
            f"Style: {style}\n"
            f"Palette: {', '.join(palette)}\n"
            f"Mood: {mood}\n"
            f"Typography: {typography}\n"
            f"Texture/material: {ref_ornaments}\n"
            f"Page type adaptation: {page_rule}\n"
            f"Reference usage: layout_color_typography_only\n"
            f"Clone rules: {ref_clone}\n"
            f"Visual rhythm: 每页由文案决定画面证据，风格只统一色彩、材质和装饰强度"
        )

    # 无参考图时：极简开放，让 LLM 自行从 content_plan 推断
    return (
        "Style: 内容自适应\n"
        "Palette: 由页面内容主题和受众自然推导\n"
        "Mood: 贴合内容气质\n"
        "Typography: 由风格气质决定字体搭配\n"
        "Page type adaptation: 封面/章节页可强化情绪，内容/数据页优先可读\n"
        "Visual rhythm: 每页由文案决定画面证据，风格只统一色彩、材质和装饰强度"
    )


def _extract_hex(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    match = re.search(r"#[0-9a-fA-F]{6}", value)
    return match.group(0).upper() if match else None


def _palette_from_reference(reference_analyses: Optional[List[Dict]]) -> list[str]:
    colors: list[str] = []
    for analysis in reference_analyses or []:
        color_map = analysis.get("colors") if isinstance(analysis, dict) else None
        if isinstance(color_map, dict):
            for key in ("background", "primary", "accent", "text"):
                hex_color = _extract_hex(color_map.get(key))
                if hex_color and hex_color not in colors:
                    colors.append(hex_color)
        for item in analysis.get("dominant_palette") or []:
            if isinstance(item, dict):
                hex_color = _extract_hex(item.get("hex"))
                if hex_color and hex_color not in colors:
                    colors.append(hex_color)
    return colors[:5]


def _reference_style_name(reference_analyses: Optional[List[Dict]]) -> str:
    for analysis in reference_analyses or []:
        name = (analysis.get("style_name") or "").strip() if isinstance(analysis, dict) else ""
        if name:
            return name
    return ""


def _reference_value(reference_analyses: Optional[List[Dict]], key: str) -> str:
    values = []
    for analysis in reference_analyses or []:
        if isinstance(analysis, dict) and analysis.get(key):
            values.append(str(analysis[key]).strip())
    return "；".join(v for v in values if v)


def style_pack_from_selected_style(selected_style: dict | str | None) -> str | None:
    if not selected_style:
        return None
    import json

    try:
        style_obj = json.loads(selected_style) if isinstance(selected_style, str) else selected_style
    except Exception:
        return None
    if not isinstance(style_obj, dict):
        return None
    palette = style_obj.get("palette", [])
    if isinstance(palette, list):
        palette_text = ", ".join(
            str(item.get("hex") if isinstance(item, dict) else item)
            for item in palette[:5]
            if item
        )
    else:
        palette_text = str(palette)
    visual_strategy = style_obj.get("visual_strategy") if isinstance(style_obj.get("visual_strategy"), dict) else None
    page_type_adaptation = style_obj.get("page_type_adaptation", "封面/章节页可强化情绪，内容/数据页优先可读")
    visual_strategy_line = visual_strategy_text(visual_strategy)
    if visual_strategy_line and visual_strategy.get("base_tone"):
        visual_strategy_line = f"base_tone={visual_strategy.get('base_tone')}; {visual_strategy_line}"
    texture_line = style_obj.get("texture") or style_obj.get("clone_rules") or ""
    style_rationale = str(style_obj.get("description") or "").strip()
    visual_rhythm = (
        style_obj.get("content_style_hint")
        or style_obj.get("visual_rhythm")
        or style_rationale
        or "每页由文案决定画面证据，风格只统一色彩、材质和装饰强度"
    )
    return "\n".join(
        line for line in [
            f"Style: {style_obj.get('name', '用户确认风格')}",
            f"Palette: {palette_text}",
            f"Mood: {style_obj.get('mood', '保持用户确认的整体气质')}",
            f"Visual strategy: {visual_strategy_line}" if visual_strategy_line else "",
            f"Typography: {style_obj.get('font') or '由风格气质决定字体搭配'}",
            f"Texture/material: {texture_line}" if texture_line else "",
            f"Page type adaptation: {page_type_adaptation}",
            f"Reference usage: {style_obj.get('reference_usage', 'style text only unless template/page references are present')}",
            f"Visual rhythm: {visual_rhythm}",
        ] if line and not line.endswith(": ")
    )


def derive_style_pack_from_content(
    content_plan: List[Dict],
    reference_analyses: Optional[List[Dict]] = None,
) -> str:
    palette = _palette_from_reference(reference_analyses)
    ref_name = _reference_style_name(reference_analyses)
    ref_mood = _reference_value(reference_analyses, "mood")
    ref_font = _reference_value(reference_analyses, "font_suggestion")
    ref_ornaments = _reference_value(reference_analyses, "ornaments")
    ref_clone = _reference_value(reference_analyses, "clone_rules")

    if palette:
        style = ref_name or "参考图风格基因"
        mood = ref_mood or "贴合用户参考图，克制统一"
        typography = ref_font or "由风格气质决定字体搭配"
        page_rule = (
            "封面/章节/金句页可强化参考图主色和装饰；内容/数据/表格页必须在同一视觉语言内保证高可读，不自动切换成另一套浅底风格。"
        )
        rhythm = ref_clone or ref_ornaments or "每页由文案决定画面证据，参考图只统一色彩、材质和装饰强度"
    else:
        # 没有参考图时，让 LLM 根据内容计划生成风格推荐
        try:
            from app.services.style_proposal import generate_style_proposals
            proposals = generate_style_proposals(content_plan or [])
            if proposals and isinstance(proposals[0], dict):
                style_pack = style_pack_from_selected_style(proposals[0])
                if style_pack:
                    return style_pack
        except Exception:
            pass
        # 极简兜底：不硬编码具体风格，让生图模型根据内容自行推导
        style = "由内容主题自然适配"
        palette = []
        mood = "贴合内容气质，不预设具体风格"
        typography = "由风格气质决定字体搭配"
        page_rule = "封面/章节页可强化情绪；内容/数据页优先可读。"
        rhythm = "每页由文案决定画面证据，风格由内容主题自然推导"

    strategy = build_visual_strategy(
        summary={
            "industries": [],
            "keywords": [],
            "style_direction_hint": "",
            "dense_page_ratio": 0,
            "table_page_ratio": 0,
        },
        palette=palette,
        reference_analysis=(reference_analyses or [None])[0] if reference_analyses else None,
    )
    strategy_line = visual_strategy_text(strategy)
    if strategy_line and strategy.get("base_tone"):
        strategy_line = f"base_tone={strategy.get('base_tone')}; {strategy_line}"

    return "\n".join([
        f"Style: {style}",
        f"Palette: {', '.join(palette[:5])}",
        f"Mood: {mood}",
        f"Visual strategy: {strategy_line}" if strategy_line else "",
        f"Typography: {typography}",
        f"Page type adaptation: {page_rule}",
        "Reference usage: style text only unless template/page references are present",
        f"Visual rhythm: {rhythm}",
    ])
