import functools
import glob
import json
import logging
import os
import re
from typing import List, Dict, Optional

import yaml

from app.core.config import settings
from app.core.llm_client import get_llm_client
from app.core.provider_credentials import get_minimax_llm_model

logger = logging.getLogger(__name__)


TRADITIONAL_CULTURE_TERMS = [
    "古法", "非遗", "传承", "匠心", "老字号", "古朴", "传统", "文化",
    "中式", "东方", "国潮", "节庆", "喜庆", "宴会", "礼赠",
]
FOOD_AGRI_TERMS = ["食品", "餐饮", "农业", "花生油", "粮油", "调味品", "食用油", "风味", "香"]
TECH_TERMS = ["科技", "AI", "人工智能", "数据", "算法", "数字化", "芯片", "云计算"]
TECH_NEGATION_PATTERNS = [
    "拒绝科技", "不是科技", "非科技", "去科技", "反科技", "科技与狠活", "科技狠活",
    "拒绝工业化", "反工业化",
]


def _contains_unnegated_tech(text: str) -> bool:
    """Avoid classifying anti-tech phrases such as “拒绝科技与狠活” as a tech deck."""
    if any(pattern in text for pattern in TECH_NEGATION_PATTERNS):
        text = text
        for pattern in TECH_NEGATION_PATTERNS:
            text = text.replace(pattern, "")
    return any(term in text for term in TECH_TERMS)


def _score_terms(text: str, terms: List[str]) -> int:
    return sum(text.count(term) for term in terms)


@functools.lru_cache(maxsize=1)
def _load_style_library() -> List[Dict]:
    """加载 nano-banana-ppt/styles 目录下的所有风格库文件。（结果已缓存）"""
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    styles_dir = os.path.join(base_dir, "..", "..", "nano-banana-ppt", "styles")
    styles_dir = os.path.abspath(styles_dir)

    if not os.path.isdir(styles_dir):
        logger.warning(f"Style library not found at {styles_dir}")
        return []

    styles = []
    for path in glob.glob(os.path.join(styles_dir, "*.md")):
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()

            if not content.startswith("---"):
                continue

            parts = content.split("---", 2)
            meta = yaml.safe_load(parts[1]) if len(parts) >= 2 else {}
            body = parts[2].strip() if len(parts) >= 3 else ""

            # 提取描述：取"风格描述"段落的第一句/段
            desc = ""
            for line in body.splitlines():
                line = line.strip()
                if line and not line.startswith("#") and not line.startswith("-"):
                    desc = line
                    break

            styles.append({
                "id": meta.get("id", ""),
                "name": meta.get("id", "").replace("_", " ").title(),
                "category": meta.get("category", ""),
                "palette": meta.get("palette", [])[:6],
                "fonts": meta.get("fonts", []),
                "best_for": meta.get("best_for", []),
                "avoid": meta.get("avoid", []),
                "description": desc,
                "aliases": meta.get("aliases", []),
            })
        except Exception as e:
            logger.warning(f"Failed to parse style file {path}: {e}")

    return styles


def _extract_content_summary(content_plan: List[Dict]) -> Dict:
    """从 content plan 提取用于风格匹配的摘要信息。"""
    headlines = []
    page_types = set()
    industries = []
    keywords = []
    full_text_fragments = []
    # 用于内容主旨推断（供无素材时 LLM 理解主题）
    topic_hints = []

    for page in content_plan[:15]:
        text = page.get("text_content", {}) or {}
        h = text.get("headline", "")
        sub = text.get("subhead", "")
        body = text.get("body", "")

        if h:
            headlines.append(h)
            topic_hints.append(h)
            full_text_fragments.append(h)
        if sub:
            topic_hints.append(sub)
            full_text_fragments.append(sub)
        # 从 body 中提取第一行作为 topic hint
        if body:
            if isinstance(body, str):
                full_text_fragments.append(body)
                first_line = body.strip().split("\n")[0][:100]
                if first_line:
                    topic_hints.append(first_line)
            elif isinstance(body, list) and len(body) > 0:
                full_text_fragments.append(" ".join(str(x) for x in body[:8]))
                first_item = body[0] if isinstance(body[0], str) else (body[0].get("content", "") if isinstance(body[0], dict) else "")
                if first_item:
                    topic_hints.append(str(first_item)[:100])

        ptype = page.get("type", "content")
        page_types.add(ptype)

        # 简单关键词提取（从 headline + subhead + body 中找行业/场景词）
        text_to_search = f"{h} {sub} {str(body) if body else ''}"
        keyword_pool = [
            "金融", "医疗", "教育", "消费", "品牌", "学术", "艺术", "设计", "汽车",
            "地产", "零售", "投资", "产品", "战略", *TECH_TERMS,
            *FOOD_AGRI_TERMS, *TRADITIONAL_CULTURE_TERMS,
        ]
        for kw in keyword_pool:
            if kw in TECH_TERMS and not _contains_unnegated_tech(text_to_search):
                continue
            if kw in text_to_search:
                keywords.append(kw)

    full_text = "\n".join(full_text_fragments)
    traditional_score = _score_terms(full_text, TRADITIONAL_CULTURE_TERMS)
    food_score = _score_terms(full_text, FOOD_AGRI_TERMS)
    tech_score = _score_terms(full_text, TECH_TERMS) if _contains_unnegated_tech(full_text) else 0
    brand_score = _score_terms(full_text, ["消费", "品牌", "零售", "产品", "战略"])

    # 推断行业/场景。先处理强语义，避免“拒绝科技与狠活”一类反向表达误判为科技。
    if "金融" in keywords or "投资" in keywords:
        industries.append("金融/投资")
    if tech_score >= 2 and tech_score >= traditional_score + food_score:
        industries.append("科技/数据")
    if brand_score:
        industries.append("消费/品牌")
    if food_score:
        industries.append("食品/农业")
    if traditional_score:
        industries.append("古法非遗/传统文化")
    if "学术" in keywords:
        industries.append("学术/研究")
    if "艺术" in keywords or "设计" in keywords:
        industries.append("艺术/设计")
    if not industries:
        industries.append("通用商务")

    return {
        "headlines": headlines[:8],
        "page_types": list(page_types),
        "industries": list(set(industries)),
        "keywords": list(set(keywords)),
        "total_pages": len(content_plan),
        "topic_hints": topic_hints[:6],  # 用于帮助 LLM 理解内容主旨
        "style_direction_hint": _build_content_style_direction(
            traditional_score=traditional_score,
            food_score=food_score,
            tech_score=tech_score,
            brand_score=brand_score,
        ),
    }


def _build_content_style_direction(traditional_score: int, food_score: int, tech_score: int, brand_score: int) -> str:
    if traditional_score and food_score:
        return "内容核心更接近古法非遗、传统食品/农业品牌，应优先考虑传统质感、品牌主色、暖性浅底、纹样装饰与可信赖的商业表达。"
    if traditional_score:
        return "内容核心偏传统文化/非遗/东方审美，应优先考虑中式、古朴、典雅、节庆或国潮方向。"
    if food_score:
        return "内容核心偏食品/农业/消费品牌，应优先考虑温暖、有食欲、可信赖、品牌化的视觉方向。"
    if tech_score:
        return "内容核心偏科技/数据/AI，可考虑冷色、秩序感、数据化的现代视觉方向。"
    if brand_score:
        return "内容核心偏消费品牌/商业提案，应优先考虑品牌识别、货架记忆和说服效率。"
    return "根据内容标题和正文真实判断风格，选择最贴合主题和受众的视觉方向。"


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int] | None:
    if not isinstance(hex_color, str):
        return None
    value = hex_color.strip().lstrip("#")
    if len(value) != 6:
        return None
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def _is_warm_accent(hex_color: str) -> bool:
    rgb = _hex_to_rgb(hex_color)
    return bool(rgb and rgb[0] > 130 and rgb[1] > 85 and rgb[0] >= rgb[1] and rgb[2] < 110)


def _is_dark(hex_color: str) -> bool:
    rgb = _hex_to_rgb(hex_color)
    return bool(rgb and (rgb[0] + rgb[1] + rgb[2]) / 3 < 75)


def _is_neutral_dark(hex_color: str) -> bool:
    rgb = _hex_to_rgb(hex_color)
    if not rgb:
        return False
    r, g, b = rgb
    return _is_dark(hex_color) and max(rgb) - min(rgb) < 28


def _brightness(hex_color: str) -> float:
    rgb = _hex_to_rgb(hex_color)
    return sum(rgb) / 3 if rgb else 0


def _saturation(hex_color: str) -> float:
    rgb = _hex_to_rgb(hex_color)
    if not rgb:
        return 0
    high = max(rgb)
    low = min(rgb)
    return (high - low) / high if high else 0


def _is_chromatic_brand_color(hex_color: str) -> bool:
    return _saturation(hex_color) >= 0.28 and not _is_neutral_dark(hex_color)


def _needs_page_type_modulation(hex_color: str) -> bool:
    return _is_dark(hex_color) or _saturation(hex_color) >= 0.38


def _get_color_name(hex_color: str) -> str:
    """根据色值返回直观的中文颜色名（如'酒红'、'琥珀金'），而非技术角色名。"""
    rgb = _hex_to_rgb(hex_color)
    if not rgb:
        return "参考色"
    r, g, b = rgb

    # 黑白灰判断
    diff = max(rgb) - min(rgb)
    if diff < 20:
        avg = sum(rgb) / 3
        if avg < 50:
            return "深墨"
        if avg < 100:
            return "炭灰"
        if avg < 180:
            return "银灰"
        return "纯白"

    # 计算色调
    max_val = max(rgb)
    min_val = min(rgb)
    delta = max_val - min_val

    if delta == 0:
        hue = 0
    elif max_val == r:
        hue = (60 * ((g - b) / delta) + 360) % 360
    elif max_val == g:
        hue = (60 * ((b - r) / delta) + 120) % 360
    else:
        hue = (60 * ((r - g) / delta) + 240) % 360

    brightness = sum(rgb) / 3

    # 按色调区间 + 亮度命名
    if 345 <= hue or hue < 15:
        if brightness < 80:
            return "酒红"
        if brightness > 180:
            return "粉红"
        return "朱红"
    elif 15 <= hue < 35:
        if brightness < 100:
            return "咖啡棕"
        if delta / max_val < 0.5:
            return "暖棕"
        return "琥珀金"
    elif 35 <= hue < 50:
        if brightness < 120:
            return "土黄"
        if delta / max_val < 0.5:
            return "米黄"
        return "金黄"
    elif 50 <= hue < 75:
        if brightness < 120:
            return "橄榄绿"
        return "柠檬黄"
    elif 75 <= hue < 150:
        if brightness < 100:
            return "墨绿"
        if brightness > 180:
            return "翠绿"
        return "草绿"
    elif 150 <= hue < 190:
        return "湖蓝"
    elif 190 <= hue < 260:
        if brightness < 100:
            return "藏蓝"
        if brightness > 180:
            return "天蓝"
        return "海蓝"
    elif 260 <= hue < 300:
        if brightness < 100:
            return "深紫"
        return "紫罗兰"
    elif 300 <= hue < 345:
        if brightness < 120:
            return "玫红"
        return "桃红"

    return "参考色"


def _color_label(hex_color: str) -> str:
    if _is_warm_accent(hex_color):
        return "暖性强调色"
    if _is_chromatic_brand_color(hex_color):
        return "品牌主色"
    if _is_neutral_dark(hex_color):
        return "深色层次"
    if _is_dark(hex_color):
        return "深色系"
    return "参考色"


def _extract_hex(value) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.search(r"#[0-9a-fA-F]{6}", value)
    return match.group(0).upper() if match else None


def _collect_clone_palette(ref: Dict, logo: Dict | None = None) -> List[Dict]:
    colors = ref.get("colors") or {}
    weighted: list[tuple[str, float]] = []

    for key in ("background", "primary", "accent", "text"):
        hex_color = _extract_hex(colors.get(key))
        if hex_color:
            weighted.append((hex_color, 0.05))
    for item in ref.get("dominant_palette") or []:
        if isinstance(item, dict) and item.get("hex"):
            weighted.append((_extract_hex(item["hex"]) or "", float(item.get("share") or 0.0)))
    if logo:
        logo_primary = _extract_hex(logo.get("primary_color"))
        if logo_primary:
            weighted.append((logo_primary, 0.25))
        for color in logo.get("secondary_colors") or []:
            hex_color = _extract_hex(color)
            if hex_color:
                weighted.append((hex_color, 0.15))

    score_by_color: dict[str, float] = {}
    order: list[str] = []
    for hex_color, score in weighted:
        if not hex_color:
            continue
        if hex_color not in order:
            order.append(hex_color)
        score_by_color[hex_color] = score_by_color.get(hex_color, 0.0) + max(score, 0.01)

    unique = order
    if unique:
        brand_colors = sorted(
            [c for c in unique if _is_chromatic_brand_color(c) and _brightness(c) <= 170],
            key=lambda c: score_by_color.get(c, 0),
            reverse=True,
        )
        accent_colors = sorted(
            [c for c in unique if _is_chromatic_brand_color(c)],
            key=lambda c: score_by_color.get(c, 0),
            reverse=True,
        )
        light_colors = sorted(
            [
                c for c in unique
                if (_hex_to_rgb(c) and sum(_hex_to_rgb(c) or (0, 0, 0)) / 3 > 145)
            ],
            key=lambda c: score_by_color.get(c, 0),
            reverse=True,
        )
        neutral_darks = sorted(
            [c for c in unique if _is_neutral_dark(c)],
            key=lambda c: score_by_color.get(c, 0),
            reverse=True,
        )

        ordered: list[str] = []

        # 参考图如果有明确的有彩品牌主色，优先记录为风格基因；
        # 中性深色只作为层次，不应抢走品牌主色。
        if brand_colors:
            ordered.append(brand_colors[0])
        else:
            ordered.append(unique[0])

        for group in (accent_colors[:2], light_colors[:1], neutral_darks[:1], unique):
            for color in group:
                if color not in ordered:
                    ordered.append(color)
        unique = ordered
        has_light_text = any((_hex_to_rgb(c) and sum(_hex_to_rgb(c) or (0, 0, 0)) / 3 > 150) for c in unique)
        if not has_light_text and "#F4E8D0" not in unique:
            insert_at = min(2, len(unique))
            unique.insert(insert_at, "#F4E8D0")

    fallback = ["#2F2A24", "#B8945C", "#F4E8D0", "#1F1F1F"]
    for color in fallback:
        if len(unique) >= 4:
            break
        if color not in unique:
            unique.append(color)

    if unique and _needs_page_type_modulation(unique[0]):
        roles = ["品牌主色（强视觉页可放大，信息页做强调）", "强调/装饰色", "信息页浅底/留白", "深色文字/层次"]
    else:
        roles = ["主背景色", "标题/强调色", "正文色", "辅助点缀色"]
    names = [_get_color_name(color) for color in unique[:4]]

    return [{"name": names[i], "hex": color, "role": roles[i]} for i, color in enumerate(unique[:4])]


def _has_clone_reference(ref: Dict, template: Dict) -> bool:
    if ref.get("description") or ref.get("style_name") or ref.get("dominant_palette"):
        return True
    if ref.get("colors", {}).get("primary"):
        return True
    template_ref = template.get("reference_analysis") if isinstance(template, dict) else None
    return bool(template_ref and (template_ref.get("description") or template_ref.get("dominant_palette")))


def _page_type_adaptation_rules(palette: List[Dict]) -> str:
    primary = palette[0]["hex"] if palette else "#2F2A24"
    accent = palette[1]["hex"] if len(palette) > 1 else "#B8945C"
    light = "#F7F3EA"
    for color in palette:
        rgb = _hex_to_rgb(color.get("hex", ""))
        if rgb and sum(rgb) / 3 > 145 and _saturation(color["hex"]) < 0.45:
            light = color["hex"]
            break

    if palette and _needs_page_type_modulation(primary):
        return (
            "页面类型适配规则：参考图只用于定调，不要求所有页面按同一强度复刻。"
            f"封面、章节页、转场页、金句页可放大使用品牌主色 {primary} 和强调色 {accent}，承担品牌定调和情绪冲击；"
            f"内容页、数据页、表格页、长文分析页应优先使用 {light} 或其他低饱和浅底，"
            f"用 {primary} 做标题、页眉、编号、强调块，用 {accent} 做少量装饰线和重点信息；"
            "正文使用高可读的深色文字。信息越密集，背景越要降饱和、提亮度、减少装饰。"
            "地图页、图表页、配图页和业务场景页的具体画面必须由该页文案决定，不能机械复刻参考图构图。"
        )

    return (
        "页面类型适配规则：参考图提供风格基因，不是每页画面模板。"
        "封面/章节页可以更强烈地使用主色，内容/数据/表格页必须优先保证阅读效率，"
        "根据页面信息密度选择浅底或留白，并由文案决定具体配图内容。"
    )


def _build_reference_clone_proposal(summary: Dict, assets: Dict) -> Dict:
    logo = assets.get("logo_analysis") or {}
    ref = assets.get("reference_analysis") or {}
    template = assets.get("template_analysis") or {}
    template_ref = template.get("reference_analysis") if isinstance(template, dict) else None
    if template_ref and not (ref.get("description") or ref.get("dominant_palette")):
        ref = template_ref

    palette = _collect_clone_palette(ref, logo)
    palette_hex = [c["hex"] for c in palette]
    style_name = (ref.get("style_name") or "").strip()
    if not style_name:
        desc_for_name = " ".join(str(x) for x in [ref.get("description"), ref.get("mood"), ref.get("ornaments"), ref.get("texture")] if x)
        if any(_is_chromatic_brand_color(c) for c in palette_hex) and any(_is_warm_accent(c) for c in palette_hex):
            style_name = "品牌主色典雅"
        elif any(_is_dark(c) for c in palette_hex) and any(_is_warm_accent(c) for c in palette_hex):
            style_name = "深色典雅"
        elif "国潮" in desc_for_name or "中式" in desc_for_name:
            style_name = "中式典雅"
        else:
            style_name = "参考图复刻"

    # 用户给了参考图时，风格命名必须来自图像，不混入内容里的“科技/战略”等词。
    style_name = re.sub(r"(科技|战略|未来|数据|智能|AI)", "", style_name, flags=re.IGNORECASE).strip(" -_·")
    if not style_name:
        style_name = "参考图复刻"
    if any(_is_chromatic_brand_color(c) for c in palette_hex) and any(_is_warm_accent(c) for c in palette_hex) and style_name == "参考图复刻":
        style_name = "品牌主色典雅"

    mood = ref.get("mood") or "古朴、典雅、厚重"
    font = ref.get("font_suggestion") or logo.get("font_style") or "标题使用文化感较强的宋体/书法体，正文使用清晰黑体"
    composition = ref.get("composition_style") or "沿用参考图的版式节奏"
    ornaments = ref.get("ornaments") or "沿用参考图中的装饰纹样与边框语言"
    texture = ref.get("texture") or "沿用参考图的背景肌理与光影层次"
    clone_rules = ref.get("clone_rules") or "提取参考图的主色关系、装饰气质和整体氛围，并按页面类型调节使用强度。"
    adaptation_rules = _page_type_adaptation_rules(palette)

    primary_name = _get_color_name(palette[0]['hex']) if palette else '品牌主色'
    accent_name = _get_color_name(palette[1]['hex']) if len(palette) > 1 else '强调色'
    description = (
        f"整体「{mood}」气质。{primary_name}定调品牌识别，{accent_name}做重点强调；"
        f"封面/章节页可放大装饰，内容/数据页优先留白与可读性。{clone_rules}"
    )

    return {
        "name": style_name,
        "palette": palette,
        "mood": mood,
        "font": font,
        "description": description[:420],
        "source": "asset_clone",
        "clone_mode": "style_dna",
        "reference_usage": "style_text_only",
        "page_type_adaptation": adaptation_rules,
        "content_style_hint": summary.get("style_direction_hint", ""),
    }


def generate_style_proposals(content_plan: List[Dict], assets: Optional[Dict] = None) -> List[Dict]:
    """
    根据 Content Plan 生成风格提案。
    - 如果用户提供了素材（logo、参考图、描述等），输出 1 套基于素材的完整风格阐述
    - 如果用户未提供素材，输出 3 套推荐（AI原创1套 + 风格库匹配2套）
    每套包含：name, palette(4色), mood, font, description（专业总监口吻长文本）
    """
    assets = assets or {}
    summary = _extract_content_summary(content_plan)
    style_library = _load_style_library()

    # 判断是否有有效用户素材（内容不为空才算）
    logo = assets.get("logo_analysis") or {}
    ref = assets.get("reference_analysis") or {}
    has_logo = bool(logo.get("primary_color") or logo.get("description"))
    has_ref = bool(
        ref.get("description")
        or ref.get("style_name")
        or ref.get("dominant_palette")
        or ref.get("colors", {}).get("primary")
    )
    has_user_desc = bool(assets.get("user_description", "").strip())
    has_template = bool(assets.get("template_analysis", {}).get("has_template"))
    has_assets = has_logo or has_ref or has_user_desc or has_template

    if has_assets:
        return _generate_asset_based_proposal(content_plan, summary, assets, style_library)

    client = get_llm_client()

    # 构建 style 库摘要，供 LLM 挑选
    style_catalog = []
    for s in style_library:
        style_catalog.append({
            "id": s["id"],
            "category": s["category"],
            "best_for": s["best_for"],
            "avoid": s["avoid"],
            "palette_preview": s["palette"][:4],
            "short_desc": s["description"][:80],
        })

    prompt = f"""你是一位顶级 PPT 视觉总监。你的任务是根据客户的 PPT 内容，输出 3 套风格提案。你要像真正的设计总监一样说话——**具体、有观点、有逻辑，而不是堆砌形容词和文学修辞**。

【PPT 内容概览】
- 主题关键词：{"、".join(summary["keywords"]) if summary["keywords"] else "商务演示"}
- 行业/场景：{"、".join(summary["industries"])}
- 页面类型：{"、".join(summary["page_types"])}
- 总页数：{summary["total_pages"]}

【内容标题摘录】（这些标题决定了 PPT 的核心主题和受众）
{"\n".join("- " + h for h in summary["headlines"][:8])}

【内容主题线索】（从正文提取的事实关键词，帮助判断配色方向）
{summary.get("topic_hints", "")}

【内容风格判断提示】（必须优先于通用商务/科技模板）
{summary.get("style_direction_hint", "")}

【可用风格库（第 2、3 套必须从中选择）】
{json.dumps(style_catalog, ensure_ascii=False, indent=2)}

【输出格式】
严格输出 JSON 数组，3 个对象：
{{
  "name": "风格名称（简洁直观的设计语言命名，如'流体玻璃极简'、'折叠纸艺温暖'，禁止用'原生之境'这类虚词）",
  "palette": [
    {{"name": "直观颜色名（如'酒红'、'琥珀金'、'米白'，不要用'品牌主色'这类技术词）", "hex": "#0A1628", "role": "主背景色"}},
    {{"name": "直观颜色名", "hex": "#E8D5A3", "role": "标题色"}},
    {{"name": "直观颜色名", "hex": "#F5F5F0", "role": "正文色"}},
    {{"name": "直观颜色名", "hex": "#1E3A5F", "role": "点缀色"}}
  ],
  "mood": "氛围标签（3-5个具体形容词，如'冷静、专业、克制'）",
  "font": "字体建议（如'无衬线黑体，标题加粗'）",
  "description": "风格说明（80-120字，不要出现色号，用直观颜色名，说清为什么适合这份PPT即可）",
  "source": "original（第1套）或 风格库id（第2、3套）"
}}

【3 套方案的结构要求】

第 1 套：AI 原创（source = "original"）
- 你必须基于 PPT 的主题、行业和受众，设计一套最适合的原创风格。
- 不要泛泛而谈"商务通用"，要具体到这份 PPT 的内容；必须说明为什么这套视觉语言适合当前主题、受众和使用场景。
- 不得只因为标题或正文中出现少量行业热词，就套用与真实主题气质不一致的通用风格。风格判断必须来自内容主线、品牌/产品属性、受众和演示目标。

第 2、3 套：风格库匹配（source = 对应 id）
- 你必须从上方风格库中挑选，**挑选依据必须是这份 PPT 的内容**。
- 不要随机选。要根据 PPT 的主题、行业、页面类型来判断哪个库最适合。
- 在 description 中，必须明确说明："我从风格库中选择了『XX』，因为它原本的设计定位是……，非常适合这份 PPT 的……需求。"

【description 写作要求——极其重要】

1. **第一段必须开门见山**：直接说「这份 PPT 讲的是 XXX，所以我认为最适合的风格是……」。不要绕弯子。

2. **配色只说功能，不写色号**：
   - 用直观颜色名（如"酒红"、"琥珀金"），禁止写"品牌主色 #800000"这类技术参数
   - 说清每种颜色在PPT里承担什么功能即可（如"深色背景让数据图表更突出"）

3. **禁止以下说辞**（这些都是用户讨厌的空话套话）：
   - "凝视深渊的勇气与沉静"
   - "极度克制、极度干净的空间感"
   - "没有任何多余的视觉噪音干扰观众的情绪投入"
   - "让文字和图像成为唯一的主角"
   - "为情感内容提供最大程度的纯净舞台"
   - 任何类似的文学修辞、哲学隐喻、抽象形容词堆砌

4. **要像在给客户讲方案**：客户关心的是"我的PPT用这个风格会不会更好看、更专业、更能说服听众"。所以你要解释的是：**这个风格如何解决这个PPT的具体问题**（文字多怎么办、数据多怎么办、需要品牌感怎么办）。

5. **情绪氛围关键词放在最后**，3-5 个词即可，不要展开解释。

【参考口吻示例】（精简、有功能指向、不说色号）
"这份PPT面向投资人，需要传递信任和专业。我推荐「白色为主、海军蓝为辅」的配色：白色背景最大化数据可读性，海军蓝标题传递沉稳信任，琥珀金仅用于关键数字和品牌logo，面积控制在5%以内。字体标题用黑体Heavy保证投影清晰，正文用Regular保证长段落舒适。整体情绪：干净、通透、高端。"
"""

    response = client.chat.completions.create(
        model=get_minimax_llm_model(),
        messages=[
            {
                "role": "system",
                "content": "你是世界一流的 PPT 视觉总监。必须且只能输出合法的 JSON 数组，严禁添加任何额外说明文本。description 字段必须具体、说人话、解决实际问题，严禁堆砌形容词和哲学隐喻。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.7,
    )

    raw = response.choices[0].message.content or ""
    raw = raw.strip()
    logger.info(f"StyleProposal: LLM raw response length={len(raw)}")

    import re
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    raw = re.sub(r"^```(?:json)?\s*|```$", "", raw, flags=re.MULTILINE | re.IGNORECASE).strip()

    proposals = []
    if raw:
        try:
            proposals = json.loads(raw)
            if not isinstance(proposals, list):
                logger.warning("StyleProposal: LLM 返回的不是数组，使用默认方案")
                proposals = []
        except json.JSONDecodeError as e:
            logger.warning(f"StyleProposal: JSON 解析失败: {e}，raw前200字: {raw[:200]}")
    else:
        logger.warning("StyleProposal: LLM 返回空内容，使用默认方案")

    # 如果解析失败或数量不足，用 style 库兜底
    if len(proposals) < 3:
        logger.info("StyleProposal: 使用 style 库兜底")
        fallback_ids = ["swiss_design", "dark_luxury", "apple_keynote"]
        fallback_map = {s["id"]: s for s in style_library}
        for fid in fallback_ids:
            if fid in fallback_map and len(proposals) < 3:
                s = fallback_map[fid]
                proposals.append({
                    "name": s["aliases"][0] if s["aliases"] else s["name"],
                    "palette": s["palette"][:4] if len(s["palette"]) >= 4 else s["palette"] + ["#FFFFFF"],
                    "mood": s["category"],
                    "font": ", ".join(s["fonts"][:2]) if s["fonts"] else "无衬线",
                    "description": s["description"],
                    "source": s["id"],
                })

    # 确保每个提案都有所需字段，并把 source 放入 description 的末尾提示（仅库匹配项）
    for p in proposals:
        p.setdefault("name", "未命名风格")
        if not p.get("palette"):
            p["palette"] = [
                {"name": "深墨蓝", "hex": "#333333", "role": "主色"},
                {"name": "纯白", "hex": "#FFFFFF", "role": "辅色"},
                {"name": "中灰", "hex": "#999999", "role": "点缀色"},
                {"name": "浅灰", "hex": "#CCCCCC", "role": "背景色"},
            ]
        p.setdefault("mood", "专业商务")
        p.setdefault("font", "无衬线")
        p.setdefault("description", "")
        p.setdefault("source", "original")

        # 兼容旧格式：如果 palette 是字符串数组，转换为对象数组
        if p["palette"] and isinstance(p["palette"][0], str):
            default_roles = ["主色", "辅色", "点缀色", "背景色"]
            default_names = ["主色", "辅色", "点缀色", "背景色"]
            p["palette"] = [
                {
                    "name": default_names[i] if i < len(default_names) else f"颜色{i+1}",
                    "hex": color,
                    "role": default_roles[i] if i < len(default_roles) else f"配色{i+1}",
                }
                for i, color in enumerate(p["palette"])
            ]

        # 如果 description 太短，补一段默认话术
        if len(p["description"]) < 60:
            first_name = p["palette"][0].get("name", "主色") if p["palette"] and isinstance(p["palette"][0], dict) else "主色"
            p["description"] = f"「{p['name']}」是一套{p['mood']}的视觉方案。以{first_name}定调，封面可放大使用，内容页优先保证可读性与留白。"

    return proposals[:3]


def _generate_asset_based_proposal(
    content_plan: List[Dict],
    summary: Dict,
    assets: Dict,
    style_library: List[Dict],
) -> List[Dict]:
    """基于用户素材生成 1 套完整风格阐述。"""
    client = get_llm_client()

    # 整理素材信息
    logo = assets.get("logo_analysis") or {}
    ref = assets.get("reference_analysis") or {}
    user_desc = assets.get("user_description", "").strip()
    template = assets.get("template_analysis") or {}

    if _has_clone_reference(ref, template):
        logger.info("StyleProposal(AssetBased): using deterministic strict reference clone proposal")
        return [_build_reference_clone_proposal(summary, assets)]

    asset_sections = []
    if logo.get("description") or logo.get("primary_color"):
        asset_sections.append(f"【Logo 分析】\n主色: {logo.get('primary_color', 'N/A')}\n辅助色: {', '.join(logo.get('secondary_colors', []))}\n调性: {logo.get('mood', 'N/A')}\n字体风格: {logo.get('font_style', 'N/A')}\n行业气质: {logo.get('industry_vibe', 'N/A')}\n描述: {logo.get('description', 'N/A')}")

    if ref.get("description") or ref.get("colors", {}).get("primary"):
        colors = ref.get("colors", {})
        dominant_palette = ", ".join(c.get("hex", "") for c in ref.get("dominant_palette", []) if isinstance(c, dict) and c.get("hex"))
        asset_sections.append(f"【参考图分析】\n风格名: {ref.get('style_name', 'N/A')}\n背景色: {colors.get('background', 'N/A')}\n主色: {colors.get('primary', 'N/A')}\n点缀色: {colors.get('accent', 'N/A')}\n文字色: {colors.get('text', 'N/A')}\n本地提取主色: {dominant_palette or 'N/A'}\n构图: {ref.get('composition_style', 'N/A')}\n氛围: {ref.get('mood', 'N/A')}\n字体建议: {ref.get('font_suggestion', 'N/A')}\n装饰: {ref.get('ornaments', 'N/A')}\n材质: {ref.get('texture', 'N/A')}\n复刻规则: {ref.get('clone_rules', 'N/A')}\n描述: {ref.get('description', 'N/A')}")

    if template.get("has_template"):
        asset_sections.append(f"【模板信息】\n用户提供了参考模板，包含封面、目录、内容、结尾页。模板页的配色和布局应作为核心参考。")

    if user_desc:
        asset_sections.append(f"【用户风格描述】\n{user_desc}")

    prompt = f"""你是一位顶级 PPT 视觉总监。客户提供了参考风格图，你的任务是提取这套风格的视觉基因，并把它转成可用于整套 PPT 的风格系统。

【PPT 内容概览】（用于判断页面类型、阅读密度和具体配图方向；不用于篡改参考图本身的风格判断）
- 主题关键词：{"、".join(summary["keywords"]) if summary["keywords"] else "商务演示"}
- 行业/场景：{"、".join(summary["industries"])}
- 页面类型：{"、".join(summary["page_types"])}
- 总页数：{summary["total_pages"]}
- 内容风格提示：{summary.get("style_direction_hint", "")}

【用户提供的素材】（参考图决定风格基因，具体页面画面仍由页面文案决定）
{"\n\n".join(asset_sections)}

【输出格式】
严格输出 JSON 对象（不是数组）：
{{
  "name": "风格调性词，不是布局词。示范：'暖橘衬线'、'墨白极简'、'红白都会' ✅；'三栏暖橘'、'分屏极简'、'居左商务' ❌（版式特征写进 description，不是 name）",
  "palette": [
    {{"name": "直观颜色名（如'酒红'、'琥珀金'，不要用'品牌主色'这类技术词）", "hex": "参考图中的实际色值", "role": "品牌主色/强视觉页主色"}},
    {{"name": "直观颜色名", "hex": "参考图中的实际色值", "role": "强调色/标题色/装饰色"}},
    {{"name": "直观颜色名", "hex": "适合信息页阅读的浅底色", "role": "内容页背景/留白"}},
    {{"name": "直观颜色名", "hex": "高可读文字色", "role": "正文/数据文字"}}
  ],
  "mood": "氛围标签（忠实来自参考图，不发明新风格）",
  "font": "字体建议（延续参考图字体气质，同时保证正文可读）",
  "description": "风格说明（80-120字，不要出现色号，用直观颜色名，说清风格基因和页面类型调节即可。版式特征如'参考图本身是三栏布局'可以在这里说明"在合适的页面会复用这种分栏感"）"
}}

【核心原则】
1. **忠实定调**：风格名、主色关系、材质、装饰语言必须来自参考图，不得根据文案另造风格
2. **不是逐页照搬**：参考图只提供风格基因，不是每一页的画面模板
3. **按页面类型调强度**：封面/章节/转场/金句页可以更强烈使用主色和装饰；内容/数据/表格/长文页必须优先可读，降低背景强度、减少装饰、增加留白
4. **内容决定配图**：地图、图表、业务场景、产品场景和人物/物件选择由该页文案决定，不机械复制参考图里的画面对象
5. **命名不跑偏**：风格名只取调性，不混入行业词，也不混入版式词（参考输出格式 name 字段的示范）"""

    response = client.chat.completions.create(
        model=get_minimax_llm_model(),
        messages=[
            {
                "role": "system",
                "content": "你是世界一流的 PPT 视觉总监。必须且只能输出合法的 JSON 对象，严禁添加任何额外说明文本。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.5,
    )

    raw = response.choices[0].message.content or ""
    raw = raw.strip()
    logger.info(f"StyleProposal(AssetBased): LLM raw response length={len(raw)}")

    import re
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    raw = re.sub(r"^```(?:json)?\s*|```$", "", raw, flags=re.MULTILINE | re.IGNORECASE).strip()

    proposal = {}
    if raw:
        try:
            proposal = json.loads(raw)
            if not isinstance(proposal, dict):
                proposal = {}
        except json.JSONDecodeError as e:
            logger.warning(f"StyleProposal(AssetBased): JSON 解析失败: {e}")

    if not proposal:
        # 回退：从风格库中找最接近的一套
        logger.info("StyleProposal(AssetBased): 使用风格库兜底")
        fallback_ids = ["swiss_design", "dark_luxury", "apple_keynote"]
        fallback_map = {s["id"]: s for s in style_library}
        for fid in fallback_ids:
            if fid in fallback_map:
                s = fallback_map[fid]
                proposal = {
                    "name": s["aliases"][0] if s["aliases"] else s["name"],
                    "palette": s["palette"][:4] if len(s["palette"]) >= 4 else s["palette"] + ["#FFFFFF"],
                    "mood": s["category"],
                    "font": ", ".join(s["fonts"][:2]) if s["fonts"] else "无衬线",
                    "description": s["description"],
                }
                break

    # 标准化
    proposal.setdefault("name", "基于素材的定制风格")
    if not proposal.get("palette"):
        proposal["palette"] = [
            {"name": "深墨蓝", "hex": "#333333", "role": "主色"},
            {"name": "纯白", "hex": "#FFFFFF", "role": "辅色"},
            {"name": "中灰", "hex": "#999999", "role": "点缀色"},
            {"name": "浅灰", "hex": "#CCCCCC", "role": "背景色"},
        ]
    proposal.setdefault("mood", "专业商务")
    proposal.setdefault("font", "无衬线")
    proposal.setdefault("description", "")
    proposal.setdefault("source", "asset_based")

    # 兼容旧格式
    if proposal["palette"] and isinstance(proposal["palette"][0], str):
        default_roles = ["主色", "辅色", "点缀色", "背景色"]
        default_names = ["主色", "辅色", "点缀色", "背景色"]
        proposal["palette"] = [
            {
                "name": default_names[i] if i < len(default_names) else f"颜色{i+1}",
                "hex": color,
                "role": default_roles[i] if i < len(default_roles) else f"配色{i+1}",
            }
            for i, color in enumerate(proposal["palette"])
        ]

    if len(proposal.get("description", "")) < 60:
        first_name = proposal["palette"][0].get("name", "主色") if proposal["palette"] and isinstance(proposal["palette"][0], dict) else "主色"
        proposal["description"] = f"「{proposal['name']}」是一套{proposal['mood']}的视觉方案。以{first_name}定调，封面可放大使用，内容页优先保证可读性与留白。"

    return [proposal]
