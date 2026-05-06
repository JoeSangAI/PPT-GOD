import re
from typing import Dict, List, Optional


TRADITIONAL_TERMS = ("古法", "非遗", "传承", "匠心", "老字号", "中式", "东方", "国潮", "传统", "文化")
FOOD_TERMS = ("食品", "花生油", "食用油", "粮油", "农业", "风味", "香", "餐饮", "调味")
BRAND_TERMS = ("品牌", "消费", "零售", "产品", "渠道", "营销", "心智", "提案", "战略")
TECH_TERMS = ("科技", "AI", "人工智能", "数据", "算法", "数字化", "芯片", "云计算")
TECH_NEGATIONS = ("拒绝科技", "科技与狠活", "科技狠活", "反科技", "非科技", "去科技", "拒绝工业化")


def _extract_text(content_plan: List[Dict]) -> str:
    parts: list[str] = []
    for page in content_plan or []:
        text = page.get("text_content", {}) or {}
        for key in ("headline", "subhead"):
            if text.get(key):
                parts.append(str(text[key]))
        body = text.get("body")
        if isinstance(body, list):
            parts.extend(str(x) for x in body[:8])
        elif body:
            parts.append(str(body)[:1000])
        if page.get("section_title"):
            parts.append(str(page["section_title"]))
        if page.get("visual_suggestion"):
            parts.append(str(page["visual_suggestion"]))
    return "\n".join(parts)


def _score(text: str, terms: tuple[str, ...]) -> int:
    return sum(text.count(term) for term in terms)


def _has_unnegated_tech(text: str) -> bool:
    cleaned = text
    for negation in TECH_NEGATIONS:
        cleaned = cleaned.replace(negation, "")
    return any(term in cleaned for term in TECH_TERMS)


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
    return "\n".join(
        line for line in [
            f"Style: {style_obj.get('name', '用户确认风格')}",
            f"Palette: {palette_text}",
            f"Mood: {style_obj.get('mood', '保持用户确认的整体气质')}",
            f"Typography: {style_obj.get('font') or 'Headline 思源黑体 Bold (Source Han Sans Bold) / Latin: Inter SemiBold; Body 思源黑体 Regular / Latin: Inter Regular; consistent hierarchy across all pages.'}",
            f"Page type adaptation: {style_obj.get('page_type_adaptation', '封面/章节页可强化情绪，内容/数据页优先可读')}",
            f"Reference usage: {style_obj.get('reference_usage', 'style text only unless template/page references are present')}",
            f"Visual rhythm: {style_obj.get('content_style_hint', '') or '每页由文案决定画面证据，风格只统一色彩、材质和装饰强度'}",
        ] if line and not line.endswith(": ")
    )


def derive_style_pack_from_content(
    content_plan: List[Dict],
    reference_analyses: Optional[List[Dict]] = None,
) -> str:
    text = _extract_text(content_plan)
    palette = _palette_from_reference(reference_analyses)
    ref_name = _reference_style_name(reference_analyses)
    ref_mood = _reference_value(reference_analyses, "mood")
    ref_font = _reference_value(reference_analyses, "font_suggestion")
    ref_ornaments = _reference_value(reference_analyses, "ornaments")
    ref_clone = _reference_value(reference_analyses, "clone_rules")

    traditional = _score(text, TRADITIONAL_TERMS)
    food = _score(text, FOOD_TERMS)
    brand = _score(text, BRAND_TERMS)
    tech = _score(text, TECH_TERMS) if _has_unnegated_tech(text) else 0

    if palette:
        style = ref_name or "参考图风格基因"
        mood = ref_mood or "贴合用户参考图，克制统一"
        typography = ref_font or (
            "Headline 思源黑体 Bold (Source Han Sans Bold) / Latin: Inter Semibold; "
            "Body 思源黑体 Regular / Latin: Inter Regular; consistent hierarchy across all pages."
        )
        page_rule = (
            "封面/章节/金句页可强化参考图主色和装饰；内容/数据/表格页使用浅底、高可读、少装饰。"
        )
        rhythm = ref_clone or ref_ornaments or "每页由文案决定画面证据，参考图只统一色彩、材质和装饰强度"
    elif traditional and food:
        style = "中式红金非遗食品品牌"
        palette = ["#7A1511", "#D4AF37", "#F7F1E6", "#2A1A16"]
        mood = "古法、可信、高端、温暖、商业提案感"
        typography = (
            "Headline 思源宋体 Bold (Source Han Serif Bold) for cultural depth / Latin: Garamond Bold; "
            "Body 思源黑体 Regular (Source Han Sans Regular) for readability / Latin: Inter Regular; "
            "consistent weight and hierarchy across all pages."
        )
        page_rule = "封面/章节/金句页可深红金强仪式感；内容/数据/表格页浅底、红黑文字、少量金色点缀。"
        rhythm = "以非遗工艺、产品、渠道场景和商业证据形成节奏，避免科技商务模板"
    elif traditional:
        style = "东方文化典雅"
        palette = ["#6E1B16", "#C9A45C", "#F6F0E6", "#1F1A17"]
        mood = "典雅、传统、沉稳、有文化厚度"
        typography = (
            "Headline 思源宋体 SemiBold (Source Han Serif SemiBold) for oriental elegance / Latin: Cormorant Garamond; "
            "Body 思源黑体 Regular / Latin: Inter Regular; clean and consistent hierarchy."
        )
        page_rule = "强视觉页可深色和金色装饰；信息页优先浅底和高可读。"
        rhythm = "东方纹样和材质只做风格包装，每页画面由内容证据决定"
    elif food:
        style = "温暖食品品牌提案"
        palette = ["#8A3A16", "#D8A84E", "#FBF3E4", "#243126"]
        mood = "温暖、可信、有食欲、品牌化"
        typography = (
            "Headline 思源黑体 Bold (Source Han Sans Bold) for friendly authority / Latin: Inter Bold; "
            "Body 思源黑体 Regular / Latin: Inter Regular; consistent across all pages."
        )
        page_rule = "封面和产品页可增强品牌色；内容和数据页优先浅底与阅读效率。"
        rhythm = "用产品、消费场景、渠道和证据对象组织画面"
    elif tech:
        style = "内容驱动科技商务"
        palette = ["#102A43", "#2F80ED", "#F5F7FA", "#111827"]
        mood = "清晰、理性、可信、未来感"
        typography = (
            "Headline 思源黑体 Bold (Source Han Sans Bold) / Latin: Inter SemiBold; "
            "Body 思源黑体 Regular / Latin: Inter Regular; data labels in same family for unified look."
        )
        page_rule = "结构/数据页保持浅底和秩序感，章节页可使用深色科技氛围。"
        rhythm = "用系统图、数据结构和业务场景表达观点，不堆叠装饰"
    elif brand:
        style = "消费品牌策略提案"
        palette = ["#2F2A24", "#B8945C", "#F6F0E6", "#1F1F1F"]
        mood = "专业、品牌化、克制、有说服力"
        typography = (
            "Headline 思源黑体 Bold (Source Han Sans Bold) for proposal-grade authority / Latin: Inter SemiBold; "
            "Body 思源黑体 Regular / Latin: Inter Regular; strict hierarchy across all pages."
        )
        page_rule = "封面/章节页可强化品牌气质；内容/数据页用浅底和少量强调色。"
        rhythm = "用产品、渠道、消费者和商业证据组织画面"
    else:
        style = "内容驱动编辑商务"
        palette = ["#2B2B2B", "#8A6F4D", "#F4F1EA", "#FFFFFF"]
        mood = "清晰、克制、可信、编辑感"
        typography = (
            "Headline 思源黑体 Bold (Source Han Sans Bold) / Latin: Inter SemiBold; "
            "Body 思源黑体 Regular / Latin: Inter Regular; consistent hierarchy across all pages."
        )
        page_rule = "封面/章节页可增强情绪；内容/数据/表格页优先浅底、高可读、少装饰。"
        rhythm = "每页由文案决定画面证据，风格只统一色彩、材质和装饰强度"

    return "\n".join([
        f"Style: {style}",
        f"Palette: {', '.join(palette[:5])}",
        f"Mood: {mood}",
        f"Typography: {typography}",
        f"Page type adaptation: {page_rule}",
        "Reference usage: style text only unless template/page references are present",
        f"Visual rhythm: {rhythm}",
    ])
