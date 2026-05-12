import re
from typing import Dict, List, Optional

from app.services.visual_strategy import build_visual_strategy, visual_strategy_text


TRADITIONAL_TERMS = ("古法", "非遗", "传承", "匠心", "老字号", "中式", "东方", "国潮", "传统", "文化")
FOOD_TERMS = ("食品", "花生油", "食用油", "粮油", "农业", "风味", "香", "餐饮", "调味")
BRAND_TERMS = ("品牌", "消费", "零售", "产品", "渠道", "营销", "心智", "提案", "战略")
TECH_TERMS = ("科技", "AI", "人工智能", "数据", "算法", "数字化", "芯片", "云计算")
TECH_NEGATIONS = ("拒绝科技", "科技与狠活", "科技狠活", "反科技", "非科技", "去科技", "拒绝工业化")
LIGHT_STYLE_TERMS = (
    "白色为主", "以白色为主", "米白为基底", "白底", "浅底", "浅色基底", "浅色信息基底",
    "明亮", "亮一点", "明亮一点", "舍弃黑紫", "不要黑紫", "不喜欢黑紫", "不是黑紫",
)
LIGHT_STYLE_NEGATIONS = ("不要浅色", "不用浅色", "不要白底", "不用白底", "不要明亮")
ANCIENT_ROME_TERMS = (
    "古罗马", "罗马", "角斗士", "角斗", "斗兽场", "竞技场", "Colosseum", "gladiator",
    "gladius", "凯撒", "帝国", "元老院", "军团", "罗马帝国", "血腥舞台",
)
TOPIC_STYLE_PRESETS = [
    {
        "id": "ancient_rome_gladiator",
        "terms": ANCIENT_ROME_TERMS,
        "matches": lambda text: _score(text, ANCIENT_ROME_TERMS) >= 2 or ("罗马" in text and "角斗" in text),
        "style": "古罗马竞技史诗风",
        "palette": ["#171310", "#7A1F1D", "#E8DDC8", "#A8743A"],
        "mood": "史诗、粗粝、古典、戏剧化",
        "typography": (
            "Headline uses classical serif or Roman inscription-inspired display type; "
            "Body uses Source Han Sans Regular / Latin: Inter Regular for readability; "
            "keep typography sharp, carved, and historically grounded rather than rounded or product-launch oriented."
        ),
        "page_rule": (
            "封面/章节/金句页可使用火山岩黑、血酒红、斗兽场暗部和青铜纹理制造史诗感；"
            "目录/正文/表格页使用石灰白或浅石材基底，保留旧青铜编号和暗红强调，保证阅读效率。"
        ),
        "rhythm": (
            "每页画面证据必须来自古罗马角斗士主题：斗兽场、短剑、盾牌、盔甲、雕塑、石柱、观众席、地图或制度图解；"
            "整体保持历史史诗和古典材质方向，以题材物件和场景作为主要视觉来源。"
        ),
    }
]


def _selected_style_requests_light(style_obj: dict) -> bool:
    palette = style_obj.get("palette") if isinstance(style_obj.get("palette"), list) else []
    palette_text = " ".join(
        " ".join(str(item.get(key) or "") for key in ("name", "role", "hex"))
        if isinstance(item, dict)
        else str(item)
        for item in palette
    )
    text = " ".join(
        str(style_obj.get(key) or "")
        for key in ("name", "description", "page_type_adaptation", "content_style_hint", "visual_rhythm")
    )
    normalized = re.sub(r"\s+", "", f"{text} {palette_text}").lower()
    return bool(
        normalized
        and not any(term.lower() in normalized for term in LIGHT_STYLE_NEGATIONS)
        and any(term.lower() in normalized for term in LIGHT_STYLE_TERMS)
    )


def _light_strategy_from_selected_style() -> dict:
    return {
        "base_tone": "light",
        "summary": "整体以白色/米白/浅色明亮基底为主，明亮柔紫做品牌识别和装饰。",
        "background_policy": "整套页面以浅色视觉基底为主",
        "content_treatment": "正文页、内容页、数据页和表格页使用白色/米白/淡紫浅底，通过柔紫标题、浅色卡片、留白和墨灰紫文字保证阅读效率。",
        "exception_policy": "深色只用于文字、细线或局部强调，不使用黑紫或深色整页基底。",
    }


def _light_page_type_adaptation_from_selected_style() -> str:
    return (
        "页面类型适配规则：整套页面以白色、米白或淡紫浅底为主。"
        "内容页、数据页、表格页必须保持明亮基底和高可读正文；封面、章节页可以放大柔紫、玫瑰粉、浅金和保留纹理，"
        "但不能回到黑紫或深邃暗色整页背景。"
    )


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


def _topic_style_preset(text: str) -> Optional[Dict]:
    for preset in TOPIC_STYLE_PRESETS:
        if preset["matches"](text):
            return preset
    return None


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
    if (
        _selected_style_requests_light(style_obj)
        and isinstance(visual_strategy, dict)
        and str(visual_strategy.get("base_tone") or "").lower() == "dark"
    ):
        visual_strategy = _light_strategy_from_selected_style()
        page_type_adaptation = _light_page_type_adaptation_from_selected_style()
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
            f"Typography: {style_obj.get('font') or 'Headline 思源黑体 Bold (Source Han Sans Bold) / Latin: Inter SemiBold; Body 思源黑体 Regular / Latin: Inter Regular; consistent hierarchy across all pages.'}",
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

    topic_preset = _topic_style_preset(text)

    if palette:
        style = ref_name or "参考图风格基因"
        mood = ref_mood or "贴合用户参考图，克制统一"
        typography = ref_font or (
            "Headline 思源黑体 Bold (Source Han Sans Bold) / Latin: Inter Semibold; "
            "Body 思源黑体 Regular / Latin: Inter Regular; consistent hierarchy across all pages."
        )
        page_rule = (
            "封面/章节/金句页可强化参考图主色和装饰；内容/数据/表格页必须在同一视觉语言内保证高可读，不自动切换成另一套浅底风格。"
        )
        rhythm = ref_clone or ref_ornaments or "每页由文案决定画面证据，参考图只统一色彩、材质和装饰强度"
    elif topic_preset:
        style = topic_preset["style"]
        palette = list(topic_preset["palette"])
        mood = topic_preset["mood"]
        typography = topic_preset["typography"]
        page_rule = topic_preset["page_rule"]
        rhythm = topic_preset["rhythm"]
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
