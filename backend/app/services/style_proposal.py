import functools
import glob
import json
import json_repair
import logging
import os
import re
import copy
from typing import List, Dict, Optional

import yaml

from app.core.llm_client import get_llm_client
from app.core.provider_credentials import get_minimax_llm_model
from app.services.visual_strategy import build_visual_strategy

logger = logging.getLogger(__name__)

STYLE_PROPOSAL_POLICY_VERSION = "2026-05-15-style-source-priority-v1"
DEFAULT_CONTENT_STYLE_HINT = "每页由文案决定画面证据，风格只统一色彩、材质和装饰强度"


def _parse_llm_json(raw: str, *, expected_type: type, context: str):
    text = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL).strip()
    text = re.sub(r"^```(?:json)?\s*|```$", "", text, flags=re.MULTILINE | re.IGNORECASE).strip()
    if expected_type is list:
        start, end = text.find("["), text.rfind("]")
    else:
        start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and start < end:
        text = text[start:end + 1]

    parse_errors: list[str] = []
    try:
        parsed = json.loads(text)
    except Exception as exc:
        parse_errors.append(f"json.loads: {exc}")
    else:
        if isinstance(parsed, expected_type):
            return parsed
        parse_errors.append(f"json.loads type={type(parsed).__name__}")

    try:
        parsed = json_repair.loads(text)
    except Exception as exc:
        parse_errors.append(f"json_repair: {exc}")
    else:
        if isinstance(parsed, expected_type):
            logger.info("%s: json_repair recovered malformed LLM JSON", context)
            return parsed
        parse_errors.append(f"json_repair type={type(parsed).__name__}")

    logger.warning("%s: JSON 解析失败: %s，raw前200字: %s", context, "; ".join(parse_errors), text[:200])
    return [] if expected_type is list else {}










def _palette_color_by(predicate, palette: list[dict], fallback: str) -> str:
    for color in palette:
        if isinstance(color, dict):
            hex_color = _extract_hex(str(color.get("hex") or ""))
            if hex_color and predicate(hex_color):
                return hex_color
    return fallback












def _normalize_palette_item(item, index: int) -> dict:
    if isinstance(item, dict):
        color = dict(item)
        color["name"] = color.get("name") or f"颜色{index + 1}"
        color["hex"] = _extract_hex(str(color.get("hex") or color.get("color") or "")) or "#CCCCCC"
        color["role"] = color.get("role") or ""
        return color
    hex_color = _extract_hex(str(item or "")) or "#CCCCCC"
    return {"name": _get_color_name(hex_color), "hex": hex_color, "role": ""}


def _normalize_palette_items(palette: List | None) -> list[dict]:
    return [_normalize_palette_item(item, index) for index, item in enumerate(palette or [])]


UPLOAD_CONTEXT_TERMS = [
    "已上传", "上传了", "上传品牌logo", "上传品牌 Logo", "Brief Studio", "素材清单",
    "图片会作为", "文件名", "项目素材说明",
]
STYLE_PREFERENCE_TERMS = [
    "红", "橙", "黄", "金", "绿", "蓝", "紫", "粉", "黑", "白", "灰", "棕",
    "暖色", "冷色", "浅色", "深色", "配色", "颜色", "色调", "主色",
    "视觉", "风格", "调性", "审美", "低饱和", "饱和度", "海军蓝", "深海军蓝",
    "蓝金", "品牌色", "点缀", "背景", "基底", "内容页", "正文页", "数据页",
    "标题", "章节", "留白", "字体", "版式", "排版", "质感", "纹理", "商务",
    "科技感", "参考图", "按这个方向", "重新生成一版",
]


def _style_preference_text(user_description: str) -> str:
    """Remove upload/system/content bookkeeping so it is not treated as visual taste."""
    lines: list[str] = []
    for raw_line in str(user_description or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        compact = re.sub(r"\s+", "", line).lower()
        is_upload_record = any(re.sub(r"\s+", "", term).lower() in compact for term in UPLOAD_CONTEXT_TERMS)
        has_style_signal = (
            bool(re.search(r"#[0-9a-fA-F]{6}", line))
            or any(re.sub(r"\s+", "", term).lower() in compact for term in STYLE_PREFERENCE_TERMS)
        )
        if not has_style_signal:
            continue
        has_instruction = any(term in line for term in ("希望", "想要", "觉得", "建议", "不要", "不用", "避免", "改成", "换成", "主色", "配色", "颜色"))
        if is_upload_record and not has_instruction:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


# 用户禁用色相关：用于"重新生成"时把禁色强制传给 LLM，避免 LLM 无视软约束。
# 设计思路：源头预防 — prompt 顶端写"绝对禁色"块；输出后只做诊断日志，不静默替换。







# HSL 范围判定函数：传入 (h, s, l)，返回是否命中该 canonical 色。






def _logo_brand_colors(logo: Dict | None) -> list[str]:
    logo = logo or {}
    colors: list[str] = []
    for value in [logo.get("primary_color"), *(logo.get("secondary_colors") or [])]:
        hex_color = _extract_hex(str(value or ""))
        if hex_color and hex_color not in colors:
            colors.append(hex_color)
    for item in logo.get("dominant_palette") or []:
        hex_color = _extract_hex(str(item.get("hex") if isinstance(item, dict) else item))
        if hex_color and hex_color not in colors:
            colors.append(hex_color)
    return colors












# 根据原色明度选一个中性替换色，避免再次跑到色相，但保留接近的明度层级。




# 用于在文本里识别"任意修饰词 + 色彩锚字"的复合词（如"玫瑰粉"=修饰"玫瑰"+锚字"粉"）。
# 只覆盖 _color_term_canonical 已映射到的色相类，不展开到具体明度/饱和度。






def enforce_user_style_requirements(proposal: Dict, user_description: str, logo_analysis: Dict | None = None) -> Dict:
    """Pass-through: all style enforcement is handled by LLM via prompt constraints.

    Previously this function applied code-layer keyword matching and color overrides.
    Under the "LLM thick, pipeline thin" philosophy, user constraints are passed
    as natural language in the generation prompt; no code-layer enforcement remains.
    """
    return proposal if isinstance(proposal, dict) else proposal












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
    keywords = []
    full_text_fragments = []
    topic_hints = []
    dense_pages = 0
    table_pages = 0
    measured_pages = 0

    for page in content_plan[:15]:
        text = page.get("text_content", {}) or {}
        h = text.get("headline", "")
        sub = text.get("subhead", "")
        body = text.get("body", "")
        measured_pages += 1

        if h:
            headlines.append(h)
            topic_hints.append(h)
            full_text_fragments.append(h)
        if sub:
            topic_hints.append(sub)
            full_text_fragments.append(sub)
        if body:
            if isinstance(body, str):
                full_text_fragments.append(body)
                first_line = body.strip().split("\n")[0][:100]
                lines = [line for line in body.splitlines() if line.strip()]
                if len(lines) >= 10 or len(body) >= 520:
                    dense_pages += 1
                if "|" in body:
                    table_pages += 1
                if first_line:
                    topic_hints.append(first_line)
            elif isinstance(body, list) and len(body) > 0:
                full_text_fragments.append(" ".join(str(x) for x in body[:8]))
                joined_body = "\n".join(str(x) for x in body)
                if len(body) >= 10 or len(joined_body) >= 520:
                    dense_pages += 1
                if "|" in joined_body:
                    table_pages += 1
                first_item = body[0] if isinstance(body[0], str) else (body[0].get("content", "") if isinstance(body[0], dict) else "")
                if first_item:
                    topic_hints.append(str(first_item)[:100])

        ptype = page.get("type", "content")
        page_types.add(ptype)

    # Under "LLM thick, pipeline thin" philosophy, all industry/topic inference
    # is left to the LLM. We only collect raw text for the prompt to consume.
    industries = []
    keywords = []

    return {
        "headlines": headlines[:8],
        "page_types": list(page_types),
        "industries": industries,
        "keywords": keywords,
        "total_pages": len(content_plan),
        "topic_hints": topic_hints[:6],
        "style_direction_hint": "",
        "dense_page_ratio": round(dense_pages / max(measured_pages, 1), 3),
        "table_page_ratio": round(table_pages / max(measured_pages, 1), 3),
    }






def _proposal_text(proposal: Dict) -> str:
    parts = [
        str(proposal.get(key) or "")
        for key in (
            "name", "mood", "font", "description", "source", "texture", "ornaments",
            "clone_rules", "page_type_adaptation", "content_style_hint",
        )
    ]
    palette = proposal.get("palette")
    if isinstance(palette, list):
        for color in palette:
            if isinstance(color, dict):
                parts.extend([str(color.get("name") or ""), str(color.get("role") or "")])
            else:
                parts.append(str(color))
    return " ".join(parts)


def _summary_evidence_text(summary: Dict) -> str:
    return " ".join(
        [
            " ".join(str(item) for item in summary.get("headlines") or []),
            " ".join(str(item) for item in summary.get("topic_hints") or []),
            " ".join(str(item) for item in summary.get("keywords") or []),
            " ".join(str(item) for item in summary.get("industries") or []),
            str(summary.get("style_direction_hint") or ""),
        ]
    )


def _summary_prefers_tech(summary: Dict) -> bool:
    text = _summary_evidence_text(summary)
    return "科技/数据" in text


def _asset_evidence_text(assets: Optional[Dict]) -> str:
    if not assets:
        return ""
    fields: List[str] = []
    fields.append(str(assets.get("user_description") or ""))
    for section_key in ("logo_analysis", "reference_analysis", "template_analysis"):
        section = assets.get(section_key)
        if isinstance(section, dict):
            for value in section.values():
                if isinstance(value, (str, int, float)):
                    fields.append(str(value))
                elif isinstance(value, list):
                    fields.extend(str(item) for item in value if isinstance(item, (str, int, float)))
                elif isinstance(value, dict):
                    fields.extend(str(item) for item in value.values() if isinstance(item, (str, int, float)))
    for section in assets.get("reference_analyses") or []:
        if isinstance(section, dict):
            for value in section.values():
                if isinstance(value, (str, int, float)):
                    fields.append(str(value))
                elif isinstance(value, list):
                    fields.extend(str(item) for item in value if isinstance(item, (str, int, float)))
                elif isinstance(value, dict):
                    fields.extend(str(item) for item in value.values() if isinstance(item, (str, int, float)))
    return " ".join(fields)


def _reference_analyses_from_assets(assets: Optional[Dict]) -> List[Dict]:
    if not assets:
        return []
    analyses: List[Dict] = []
    for item in assets.get("reference_analyses") or []:
        if isinstance(item, dict) and item:
            analyses.append(item)
    single = assets.get("reference_analysis")
    if isinstance(single, dict) and single:
        single_key = json.dumps(single, ensure_ascii=False, sort_keys=True, default=str)
        existing_keys = {
            json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            for item in analyses
        }
        if single_key not in existing_keys:
            analyses.insert(0, single)
    return analyses


def _merged_reference_analysis(assets: Optional[Dict]) -> Dict:
    analyses = _reference_analyses_from_assets(assets)
    if not analyses:
        return {}
    if len(analyses) == 1:
        return copy.deepcopy(analyses[0])

    merged = copy.deepcopy(analyses[0])
    for key in ("style_name", "description", "mood", "composition_style", "font_suggestion", "ornaments", "texture", "clone_rules"):
        values: list[str] = []
        for analysis in analyses:
            value = str(analysis.get(key) or "").strip()
            if value and value not in values:
                values.append(value)
        if values:
            merged[key] = "；".join(values[:4])

    colors: Dict[str, str] = {}
    for analysis in analyses:
        color_map = analysis.get("colors") if isinstance(analysis.get("colors"), dict) else {}
        for key in ("background", "primary", "accent", "text"):
            value = _extract_hex(str(color_map.get(key) or ""))
            if value and key not in colors:
                colors[key] = value
    if colors:
        merged["colors"] = colors

    palette: list[dict] = []
    seen_hexes: set[str] = set()
    for analysis in analyses:
        for item in analysis.get("dominant_palette") or []:
            if not isinstance(item, dict):
                continue
            hex_color = _extract_hex(str(item.get("hex") or ""))
            if not hex_color or hex_color in seen_hexes:
                continue
            seen_hexes.add(hex_color)
            palette.append({**item, "hex": hex_color})
    if palette:
        merged["dominant_palette"] = palette
    return merged












def _decision_archetypes(summary: Dict) -> List[Dict]:
    # 不再根据硬编码主题匹配返回特定决策框架；统一使用通用框架，让 LLM 自行理解内容并生成取舍
    industries = summary.get("industries") or []
    topic = industries[0] if industries else "这份内容"
    dense = (summary.get("dense_page_ratio") or 0) >= 0.28 or (summary.get("table_page_ratio") or 0) >= 0.18
    information_label = "信息清晰" if dense else "稳妥专业"
    return [
        {
            "decision_label": "主题记忆",
            "best_for": f"想让观众第一眼记住{topic}的气质、场景和品牌/主题识别。",
            "tradeoff": "强视觉页更有存在感，正文页需要控制装饰密度。",
            "visual_focus": "用更明确的主色、场景化画面和统一装饰语言建立整套 PPT 的第一印象。",
            "content_style_hint": "用明确主色、场景化画面和统一装饰语言建立整套 PPT 的第一印象；正文页控制装饰密度。",
        },
        {
            "decision_label": information_label,
            "best_for": "页数、文字或数据较多，希望阅读效率、可信感和汇报稳定性优先。",
            "tradeoff": "画面冲击力更克制，但更适合长时间讲解和逐页阅读。",
            "visual_focus": "浅底、清晰层级、图表/卡片秩序和少量强调色，降低理解成本。",
            "content_style_hint": "内容页优先浅底或低干扰基底、清晰层级、图表/卡片秩序和少量强调色，降低理解成本。",
        },
        {
            "decision_label": "表达冲击",
            "best_for": "希望提案更有态度，适合路演、发布、竞标或需要快速抓住注意力的场景。",
            "tradeoff": "视觉个性更强，需要接受更高对比和更鲜明的版式节奏。",
            "visual_focus": "高对比色块、大标题、强节奏分区和更鲜明的视觉符号。",
            "content_style_hint": "使用高对比色块、大标题、强节奏分区和鲜明视觉符号；内容页仍保持阅读层级。",
        },
    ]


def _ensure_decision_metadata(proposals: List[Dict], summary: Dict) -> List[Dict]:
    archetypes = _decision_archetypes(summary)
    if not archetypes:
        return proposals
    normalized: List[Dict] = []
    for index, proposal in enumerate(proposals[:3]):
        if not isinstance(proposal, dict):
            continue
        archetype = archetypes[index % len(archetypes)]
        for key in ("decision_label", "best_for", "tradeoff", "visual_focus"):
            if not proposal.get(key):
                proposal[key] = archetype.get(key, "")
        if not proposal.get("content_style_hint"):
            proposal["content_style_hint"] = (
                proposal.get("visual_rhythm")
                or archetype.get("content_style_hint")
                or ""
            )

        description = str(proposal.get("description") or "").strip()
        best_for = str(proposal.get("best_for") or "").strip()
        tradeoff = str(proposal.get("tradeoff") or "").strip()
        visual_focus = str(proposal.get("visual_focus") or "").strip()
        if best_for and "选它如果" not in description and best_for not in description:
            decision_sentence = f"选它如果你更看重：{best_for}"
            if visual_focus:
                decision_sentence += f" 视觉重点是{visual_focus}"
            if tradeoff:
                decision_sentence += f" 需要接受的取舍是{tradeoff}"
            description = f"{decision_sentence} {description}".strip()
            proposal["description"] = description[:520]
        normalized.append(proposal)
    return normalized


def _finalize_style_proposals(proposals: List[Dict], summary: Dict) -> List[Dict]:
    proposals = _ensure_decision_metadata(proposals, summary)

    for p in proposals:
        p.setdefault("name", "未命名风格")
        p.setdefault("palette", [])
        p.setdefault("mood", "")
        p.setdefault("font", "")
        p.setdefault("description", "")
        p.setdefault("source", "original")

        p["palette"] = _normalize_palette_items(p.get("palette") if isinstance(p.get("palette"), list) else [])

        # 如果 description 太短，补一段默认话术
        if len(p["description"]) < 60:
            first_name = p["palette"][0].get("name", "主色") if p["palette"] and isinstance(p["palette"][0], dict) else "主色"
            p["description"] = f"「{p['name']}」是一套{p['mood']}的视觉方案。以{first_name}定调，封面可放大使用，内容页在同一视觉语言内保证可读性与留白。"

        p.setdefault(
            "visual_strategy",
            build_visual_strategy(
                summary=summary,
                palette=p.get("palette") if isinstance(p.get("palette"), list) else None,
            ),
        )
        p.setdefault("page_type_adaptation", _page_type_adaptation_rules(p.get("palette") or [], p.get("visual_strategy")))
        if not p.get("content_style_hint"):
            p["content_style_hint"] = (
                p.get("visual_rhythm")
                or DEFAULT_CONTENT_STYLE_HINT
            )

    return proposals[:3]


def _topic_library_description(style: Dict, summary: Dict) -> str:
    style_name = style["aliases"][0] if style.get("aliases") else style.get("name", "风格库方案")
    return style.get("description", "")


def _proposal_from_style_library(style: Dict, summary: Dict) -> Dict:
    return {
        "name": style["aliases"][0] if style.get("aliases") else style.get("name", "风格库方案"),
        "palette": style["palette"][:4] if len(style.get("palette", [])) >= 4 else style.get("palette", []) + ["#FFFFFF"],
        "mood": style.get("category", "专业商务"),
        "font": ", ".join(style.get("fonts", [])[:2]) if style.get("fonts") else "无衬线",
        "description": _topic_library_description(style, summary),
        "source": style.get("id", "style_library"),
    }


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


def _extract_hex(value) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.search(r"(?<![0-9a-fA-F])#?([0-9a-fA-F]{6})(?![0-9a-fA-F])", value)
    return f"#{match.group(1).upper()}" if match else None


def _collect_clone_palette(ref: Dict, logo: Dict | None = None) -> List[Dict]:
    colors = ref.get("colors") or {}
    weighted: list[tuple[str, float]] = []

    ref_color_weights = {
        "background": 0.55,
        "primary": 1.0,
        "accent": 0.95,
        "text": 0.45,
    }
    for key in ("background", "primary", "accent", "text"):
        hex_color = _extract_hex(colors.get(key))
        if hex_color:
            weighted.append((hex_color, ref_color_weights.get(key, 0.5)))
    for item in ref.get("dominant_palette") or []:
        if isinstance(item, dict) and item.get("hex"):
            weighted.append((_extract_hex(item["hex"]) or "", float(item.get("share") or 0.0)))
    if logo:
        logo_primary = _extract_hex(logo.get("primary_color"))
        if logo_primary:
            weighted.append((logo_primary, 0.05))
        for color in logo.get("secondary_colors") or []:
            hex_color = _extract_hex(color)
            if hex_color:
                weighted.append((hex_color, 0.03))

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




def _page_type_adaptation_rules(palette: List[Dict], visual_strategy: Dict | None = None) -> str:
    primary = palette[0]["hex"] if palette else ""
    accent = palette[1]["hex"] if len(palette) > 1 else "#B8945C"
    strategy = visual_strategy or {}
    base_tone = str(strategy.get("base_tone") or "").lower()

    if base_tone == "dark":
        return (
            "页面类型适配规则：先保持整套深色视觉基底，再按页面功能调节强弱。"
            f"封面、章节页、转场页、金句页可放大使用品牌主色 {primary} 和强调色 {accent}，承担品牌定调和情绪冲击；"
            "内容页、数据页、表格页仍使用同一深色系语言，通过高对比暗色卡片、局部浅色内容区、清晰字号层级和留白保证阅读效率。"
            "除非用户明确要求或出现极端表格/长文页，不要把正文页自动切成浅色或另一套视觉语言。"
        )
    if base_tone == "light":
        brand = _palette_color_by(lambda c: 105 <= _brightness(c) < 225 and _saturation(c) >= 0.08, palette, primary)
        light_base = _palette_color_by(lambda c: _brightness(c) >= 225, palette, "#F9F8F5")
        accent_light = _palette_color_by(
            lambda c: c.upper() != brand.upper() and 150 <= _brightness(c) < 245 and _saturation(c) >= 0.04,
            palette,
            accent,
        )
        return (
            f"页面类型适配规则：整套页面以 {light_base} 一类浅底和留白保证阅读效率，强视觉页也保持明亮基底。"
            f"用 {brand} 做标题、页眉、编号和品牌装饰，用 {accent_light} 做少量装饰线和重点信息；"
            "深色只用于文字、细线或局部强调，不能在封面、章节或正文页随机回到深色整页背景。"
        )
    if palette and _needs_page_type_modulation(primary):
        return (
            "页面类型适配规则：参考图只用于定调，不要求所有页面按同一强度复刻。"
            f"封面、章节页、转场页、金句页可放大使用品牌主色 {primary} 和强调色 {accent}，承担品牌定调和情绪冲击；"
            "内容页、数据页、表格页、长文分析页必须先沿用同一套色彩和材质语言，再用卡片、留白、局部内容区和字号层级解决阅读效率。"
            "如果需要浅底，必须按同类信息页成组出现，并保留相同品牌色、装饰语言和 Logo 对比处理。"
        )

    return (
        "页面类型适配规则：参考图提供风格基因，不是每页画面模板。"
        "封面/章节页可以更强烈地使用主色，内容/数据/表格页必须优先保证阅读效率，"
        "但同类正文页应使用同一种信息页处理，不要逐页随机切换明暗语言。"
    )


def _template_source_labels(template: Dict) -> tuple[str, str, str]:
    source_kind = str((template or {}).get("source_kind") or "").strip()
    if source_kind == "finished_ppt":
        return "沿用原稿风格", "沿用原稿", "这份成品 PPT"
    return "沿用模板风格", "沿用模板", "这份模板"


def _build_template_clone_fallback_proposal(summary: Dict, assets: Dict) -> Dict:
    logo = assets.get("logo_analysis") or {}
    template = assets.get("template_analysis") or {}
    style_name, decision_label, source_label = _template_source_labels(template)
    page_count = template.get("template_page_count")
    page_text = f"已读取 {page_count} 类模板页，" if page_count else ""
    # Use the template's own color analysis so palette is not empty
    template_ref = template.get("reference_analysis") if isinstance(template, dict) else None
    palette = _collect_clone_palette(template_ref or {}, logo)
    visual_strategy = build_visual_strategy(
        summary=summary,
        palette=palette,
        reference_analysis=template_ref or {},
        logo_analysis=logo,
    )
    return {
        "name": style_name,
        "palette": palette,
        "mood": "按模板统一、克制、可读",
        "font": "沿用模板的标题/正文字体层级；缺失字体时使用高可读黑体。",
        "description": (
            f"{page_text}{source_label}是本次视觉方向的第一来源。"
            "后续页面只学习它的版式、配色、字体节奏、Logo 位置和信息密度，不把旧正文混入新内容；"
            "内容页和数据页在同一视觉语言内优先保证阅读效率。"
        )[:520],
        "source": "template_clone",
        "clone_mode": "template_dna",
        "reference_usage": "layout_color_typography_only",
        "decision_label": decision_label,
        "best_for": "你希望新 PPT 看起来就是沿着上传模板或原稿继续做，减少风格发散。",
        "tradeoff": "探索性会变少；如果想换一个全新方向，需要在视觉对话里明确提出。",
        "visual_focus": "复用模板的页面结构、配色关系、字体层级、Logo 位置和同类页面节奏。",
        "page_type_adaptation": (
            "页面类型适配规则：模板是默认风格来源。封面、目录、章节、正文、数据页分别借用对应模板页的结构；"
            "没有对应模板页时，使用最接近的内容页节奏，不另起一套视觉语言。"
        ),
        "visual_strategy": visual_strategy,
        "content_style_hint": (
            "模板是本项目的第一视觉来源；后续画面方案和 Prompt 只学习版式、配色、字体层级和 Logo 位置。"
        ),
    }


def _build_reference_clone_proposal(summary: Dict, assets: Dict) -> Dict:
    logo = assets.get("logo_analysis") or {}
    ref = _merged_reference_analysis(assets)
    template = assets.get("template_analysis") or {}
    template_ref = template.get("reference_analysis") if isinstance(template, dict) else None
    template_driven = False
    # When a template is present, its colors should be the primary source.
    # Merge template colors into ref so they survive even if the user also
    # uploaded a style reference image.
    if template_ref and template.get("has_template"):
        # Start from template colors, then overlay any richer ref data
        merged_ref = dict(template_ref)
        for key in ("description", "mood", "texture", "composition_style", "font_suggestion", "ornaments"):
            if ref.get(key):
                merged_ref[key] = ref[key]
        # Merge dominant palettes: template first, then ref extras
        template_palette = list(template_ref.get("dominant_palette") or [])
        ref_palette = list(ref.get("dominant_palette") or [])
        merged_hexes = {p.get("hex") for p in template_palette if isinstance(p, dict)}
        for p in ref_palette:
            if isinstance(p, dict) and p.get("hex") and p.get("hex") not in merged_hexes:
                template_palette.append(p)
        if template_palette:
            merged_ref["dominant_palette"] = template_palette
        # Preserve template colors dict if ref lacks it
        if template_ref.get("colors") and not ref.get("colors"):
            merged_ref["colors"] = template_ref["colors"]
        ref = merged_ref
        template_driven = True
    elif template_ref and not (ref.get("description") or ref.get("dominant_palette")):
        ref = template_ref
        template_driven = True

    palette = _collect_clone_palette(ref, logo)
    style_name = (ref.get("style_name") or "").strip()
    template_style_name, template_decision_label, template_source_label = _template_source_labels(template)
    if template_driven:
        style_name = template_style_name
    if not style_name:
        style_name = "参考图风格基因"

    mood = ref.get("mood") or ""
    font = ref.get("font_suggestion") or logo.get("font_style") or ""
    composition = ref.get("composition_style") or ""
    ornaments = ref.get("ornaments") or ""
    texture = ref.get("texture") or ""
    clone_rules = ref.get("clone_rules") or ""
    visual_strategy = build_visual_strategy(
        summary=summary,
        palette=palette,
        reference_analysis=ref,
        logo_analysis=logo,
    )
    adaptation_rules = _page_type_adaptation_rules(palette, visual_strategy)

    primary_name = _get_color_name(palette[0]['hex']) if palette else '品牌主色'
    accent_name = _get_color_name(palette[1]['hex']) if len(palette) > 1 else '强调色'
    if template_driven:
        description = (
            f"{template_source_label}是本次视觉方向的第一来源。整体「{mood}」气质，{primary_name}定调，{accent_name}做重点强调；"
            "后续页面只学习模板的版式、配色、字体节奏、Logo 位置和信息密度，不把旧正文混入新内容。"
        )
    else:
        description = (
            f"整体「{mood}」气质。{primary_name}定调品牌识别，{accent_name}做重点强调；"
            f"封面/章节页可放大装饰，内容/数据页在同一视觉语言内提高留白与可读性。{clone_rules}"
        )

    return {
        "name": style_name,
        "palette": palette,
        "mood": mood,
        "font": font,
        "description": description[:420],
        "texture": texture,
        "ornaments": ornaments,
        "clone_rules": clone_rules,
        "source": "template_clone" if template_driven else "asset_clone",
        "clone_mode": "template_dna" if template_driven else "style_dna",
        "reference_usage": "layout_color_typography_only" if template_driven else "style_text_only",
        "decision_label": template_decision_label if template_driven else None,
        "best_for": (
            "你希望新 PPT 尽量贴近上传模板或原稿，只替换成新的内容。"
            if template_driven else None
        ),
        "tradeoff": (
            "探索性会变少；如果要跳出模板，需要明确提出新的风格要求。"
            if template_driven else None
        ),
        "visual_focus": (
            "复用模板的版式结构、配色关系、字体层级、Logo 位置和同类页面节奏。"
            if template_driven else None
        ),
        "page_type_adaptation": adaptation_rules,
        "visual_strategy": visual_strategy,
        "content_style_hint": (
            "模板是本项目的第一视觉来源；后续画面方案和 Prompt 不另起风格。"
            if template_driven else summary.get("style_direction_hint", "")
        ),
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
    ref = _merged_reference_analysis(assets)
    has_logo = bool(logo.get("primary_color") or logo.get("description") or logo.get("dominant_palette"))
    has_ref = bool(
        ref.get("description")
        or ref.get("style_name")
        or ref.get("dominant_palette")
        or ref.get("colors", {}).get("primary")
    )
    has_user_desc = bool(assets.get("user_description", "").strip())
    has_clear_style_desc = bool(_style_preference_text(assets.get("user_description", "")).strip())
    has_template = bool(assets.get("template_analysis", {}).get("has_template"))
    has_assets = has_logo or has_ref or has_template

    # 用户有素材，或写了清楚的风格描述 → 直接生成 1 套
    # 用户没什么想法 → 让 LLM 生成 3 套供选择
    if has_assets or has_clear_style_desc:
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

【用户风格偏好】
{_style_preference_text(assets.get("user_description", "")) or "用户未提供额外风格偏好"}

【强题材一致性要求】
- 如果内容主题已经指向明确时代、地域、人物、场景或文化类型，三套方案都必须围绕这个题材建立视觉语言。
- 风格库只能作为表现手法，不能替代题材本身；方案必须与内容主线一致。
- 任何方案名称和说明都必须让客户一眼看出它服务于当前 PPT 主题，而不是通用模板。
- 风格判断必须来自内容主线、品牌/产品属性、受众和演示目标，不得只因标题或正文中出现少量行业热词就套用与真实主题气质不一致的通用风格。

【三套方案必须是三种明确选择】
- 不是给 3 个相似名字，而是给 3 种不同取舍：第一眼记忆、信息可读、表达冲击。
- 三套方案的 decision_label、best_for、tradeoff、visual_focus 必须互不重复。
- palette 的主导色和页面使用方式必须有实质差异，不能只是同一套深色/浅色换顺序。
- 卡片首屏会展示 best_for 和 tradeoff，所以这两项必须像给用户做选择题一样清楚。

【本次建议的决策框架】（可改写，但不能合并成同一种方案）
{json.dumps(_decision_archetypes(summary), ensure_ascii=False, indent=2)}

【可用风格库（第 2、3 套必须从中选择）】
{json.dumps(style_catalog, ensure_ascii=False, indent=2)}

【输出格式】
严格输出 JSON 数组，3 个对象：
{{
  "name": "风格名称（简洁直观的设计语言命名，如'流体玻璃极简'、'折叠纸艺温暖'，禁止用'原生之境'这类虚词）",
  "palette": [
    {{"name": "直观颜色名（如'酒红'、'琥珀金'、'米白'，不要用'品牌主色'这类技术词）", "hex": "#0A1628", "role": "主背景色/整体基底"}},
    {{"name": "直观颜色名", "hex": "#E8D5A3", "role": "标题色"}},
    {{"name": "直观颜色名", "hex": "#F5F5F0", "role": "正文页基底/内容区"}},
    {{"name": "直观颜色名", "hex": "#1E3A5F", "role": "点缀色"}}
  ],
  "mood": "氛围标签（3-5个具体形容词，如'冷静、专业、克制'）",
  "font": "字体建议（如'无衬线黑体，标题加粗'）",
  "description": "风格说明（80-120字，不要出现色号，用直观颜色名，说清为什么适合这份PPT即可）",
  "decision_label": "用户一眼能看懂的选择标签，如'主题记忆'、'信息清晰'、'表达冲击'",
  "best_for": "选它如果用户更看重什么结果，必须具体到这份 PPT",
  "tradeoff": "选择它需要接受什么取舍，必须能帮助用户排除不适合的方案",
  "visual_focus": "这套方案最主要的画面差异和页面处理方式",
  "content_style_hint": "给生图 Prompt 使用的视觉节奏约束；只写可执行画面语言，不写选择理由、推荐原因、适合人群、取舍或给用户看的说明",
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

【content_style_hint 写作要求——给生图，不给用户看】
- 这是后续每页生图 Prompt 的全局风格约束，只写画面如何生成：色彩节奏、材质、装饰强度、页面类型适配、配图证据选择原则。
- 不要写"选它如果"、"更看重"、"推荐此方案"、"原因是"、"非常适合"、"需要接受的取舍"等决策说明。
- 不要复述客户收益、讲者专业感、方案说服力或选择理由；这些只属于 description、best_for、tradeoff。
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

    if raw:
        proposals = _parse_llm_json(raw, expected_type=list, context="StyleProposal")
    else:
        proposals = []
        logger.warning("StyleProposal: LLM 返回空内容，使用默认方案")

    # Under "LLM thick" philosophy, style drift and topic mismatch are handled
    # by the LLM prompt instructions. No code-layer post-filtering remains.

    if not proposals:
        raise RuntimeError("StyleProposal: LLM 未返回有效风格提案")

    return _finalize_style_proposals(proposals, summary)


def _generate_asset_based_proposal(
    content_plan: List[Dict],
    summary: Dict,
    assets: Dict,
    style_library: List[Dict],
) -> List[Dict]:
    """基于用户素材生成 1 套完整风格阐述。"""
    # 整理素材信息
    logo = assets.get("logo_analysis") or {}
    ref = _merged_reference_analysis(assets)
    user_desc = assets.get("user_description", "").strip()
    style_user_desc = _style_preference_text(user_desc)
    template = assets.get("template_analysis") or {}
    previous_proposal = assets.get("previous_proposal") or {}
    if not isinstance(previous_proposal, dict):
        previous_proposal = {}

    if _has_clone_reference(ref, template) and not style_user_desc:
        logger.info("StyleProposal(AssetBased): using deterministic strict reference clone proposal")
        return [_build_reference_clone_proposal(summary, assets)]
    if template.get("has_template") and not style_user_desc:
        logger.info("StyleProposal(AssetBased): using deterministic template fallback proposal")
        return [_build_template_clone_fallback_proposal(summary, assets)]

    client = get_llm_client()

    asset_sections = []
    logo_palette = ", ".join(_logo_brand_colors(logo))
    if logo.get("description") or logo.get("primary_color") or logo_palette:
        asset_sections.append(f"【Logo 分析】\n主色: {logo.get('primary_color', 'N/A')}\n辅助色: {', '.join(logo.get('secondary_colors', []))}\n本地提取色: {logo_palette or 'N/A'}\n调性: {logo.get('mood', 'N/A')}\n字体风格: {logo.get('font_style', 'N/A')}\n行业气质: {logo.get('industry_vibe', 'N/A')}\n描述: {logo.get('description', 'N/A')}")

    if ref.get("description") or ref.get("colors", {}).get("primary"):
        colors = ref.get("colors", {})
        dominant_palette = ", ".join(c.get("hex", "") for c in ref.get("dominant_palette", []) if isinstance(c, dict) and c.get("hex"))
        asset_sections.append(f"【参考图分析】\n风格名: {ref.get('style_name', 'N/A')}\n背景色: {colors.get('background', 'N/A')}\n主色: {colors.get('primary', 'N/A')}\n点缀色: {colors.get('accent', 'N/A')}\n文字色: {colors.get('text', 'N/A')}\n本地提取主色: {dominant_palette or 'N/A'}\n构图: {ref.get('composition_style', 'N/A')}\n氛围: {ref.get('mood', 'N/A')}\n字体建议: {ref.get('font_suggestion', 'N/A')}\n装饰: {ref.get('ornaments', 'N/A')}\n材质: {ref.get('texture', 'N/A')}\n复刻规则: {ref.get('clone_rules', 'N/A')}\n描述: {ref.get('description', 'N/A')}")

    if template.get("has_template"):
        asset_sections.append(f"【模板信息】\n用户提供了参考模板，包含封面、目录、内容、结尾页。模板页的配色和布局应作为核心参考。")

    if style_user_desc:
        asset_sections.append(f"【用户风格描述】\n{style_user_desc}")

    # 构造"⛔ 用户拒绝块"和"被拒方案块"，强约束注入到 prompt 顶端
    # 从用户原始描述中提取所有带否定/拒绝意图的句子，原样放入 prompt，让 LLM 自己理解具体禁了什么
    _REJECTION_TRIGGERS = ("不要", "不用", "避免", "去掉", "去除", "别用", "别要", "讨厌", "不喜欢", "不想要", "不能有", "换掉", "禁止", "禁用", "排除", "去除掉", "不能用")
    rejection_lines = [
        line.strip() for line in str(style_user_desc or "").splitlines()
        if any(t in line for t in _REJECTION_TRIGGERS)
    ]
    forbidden_block = ""
    if rejection_lines:
        forbidden_block = (
            "\n【⛔ 用户明确拒绝的要求 — 必须严格遵守】\n"
            "用户在最近对话中明确表达了以下拒绝/否定意见，这些是硬性约束：\n"
            + "\n".join(f"- {line}" for line in rejection_lines)
            + "\n\n强制约束：\n"
            "- 以上要求是硬性约束，必须在 palette、description、mood、page_type_adaptation 等所有字段中严格遵守。\n"
            "- 如果用户拒绝了某个颜色/色系，palette 中不允许出现该颜色及其近亲色（例如禁止粉色 ≠ 用玫红/桃红/樱粉代替）。\n"
            "- 如果用户拒绝了某种风格/质感/装饰，description 和 mood 中也不允许提及或暗示该方向。\n"
            "- 如果原参考图或 Logo 的主色刚好命中用户拒绝的颜色，请在用户素材的剩余次色中选择，或采用中性互补色替代。\n"
        )

    previous_block = ""
    prev_palette = previous_proposal.get("palette") if isinstance(previous_proposal.get("palette"), list) else []
    if prev_palette:
        prev_colors_desc = []
        for color in prev_palette[:5]:
            if isinstance(color, dict):
                name = color.get("name") or "?"
                hex_str = _extract_hex(str(color.get("hex") or "")) or color.get("hex", "")
                prev_colors_desc.append(f"{name}({hex_str})")
        previous_block = (
            "\n【🚫 上一版方案 — 已被用户拒绝】\n"
            f"上一版名称：{previous_proposal.get('name', '?')}\n"
            f"上一版 palette：{'、'.join(prev_colors_desc)}\n"
            "强制约束：\n"
            "- 新方案的 palette 必须和上一版有明显视觉差异，不允许只改一两个色号、整体色相和氛围保持不变。\n"
            "- 如果用户在【用户风格描述】里明确说了拒绝原因（例如不要某个色系），新方案必须正面回应这个反馈。\n"
        )

    prompt = f"""你是一位顶级 PPT 视觉总监。客户提供了参考风格资料和/或文字风格要求，你的任务是提取这套风格的视觉基因，并把它转成可用于整套 PPT 的风格系统。
{forbidden_block}{previous_block}
【PPT 内容概览】（用于判断页面类型、阅读密度和具体配图方向；不用于篡改参考图本身的风格判断）
- 主题关键词：{"、".join(summary["keywords"]) if summary["keywords"] else "商务演示"}
- 行业/场景：{"、".join(summary["industries"])}
- 页面类型：{"、".join(summary["page_types"])}
- 总页数：{summary["total_pages"]}
- 内容风格提示：{summary.get("style_direction_hint", "")}

【用户提供的素材】（参考图决定风格基因，具体页面画面仍由页面文案决定）
{"\n\n".join(asset_sections)}

【聊天要求优先级】
- 如果出现【用户风格描述】，它来自用户和视觉总监的最新对话，是本次生成必须执行的最新要求。
- 用户风格描述中明确点名的配色、字体、质感、布局节奏、不要/改掉的方向，优先级高于旧参考图或旧提案中冲突的部分。
- 用户没有提到的部分，才继续继承参考图、模板或 Logo 的风格基因。
- 如果【用户风格描述】只是上传素材清单、项目素材说明、Logo 文件名或系统代用户确认的素材状态，不代表审美偏好；不得把文件名、地点或 Logo 名称扩写成风格方向。
- 如果用户明确要求加入某个品牌色或 Logo 色，该颜色必须出现在 palette 前 4 个颜色中，并在 description/page_type_adaptation 中说明它如何用于关键数字、细线、编号或图表重点。

【题材一致性红线】
- 内容标题和内容风格提示决定题材方向；素材只决定可用色彩、Logo 对比和参考版式，不能把 PPT 主题改写成无关叙事。
- 风格判断必须来自内容主线、品牌/产品属性、受众和演示目标，不得只因标题或正文中出现少量行业热词就套用与真实主题气质不一致的通用风格。

【输出格式】
严格输出 JSON 对象（不是数组）：
{{
  "name": "风格调性词，不是布局词。示范：'暖橘衬线'、'墨白极简'、'红白都会' ✅；'三栏暖橘'、'分屏极简'、'居左商务' ❌（版式特征写进 description，不是 name）",
  "palette": [
    {{"name": "直观颜色名（如'酒红'、'琥珀金'，不要用'品牌主色'这类技术词）", "hex": "#4A3728", "role": "品牌主色/强视觉页主色"}},
    {{"name": "直观颜色名", "hex": "#C9924A", "role": "强调色/标题色/装饰色"}},
    {{"name": "直观颜色名", "hex": "#FBF8F3", "role": "正文页基底/内容区"}},
    {{"name": "直观颜色名", "hex": "#3A3A3A", "role": "正文/数据文字"}}
  ],
  "mood": "氛围标签（忠实来自参考图，不发明新风格）",
  "font": "字体建议（延续参考图字体气质，同时保证正文可读）",
  "description": "风格说明（80-120字，不要出现色号，用直观颜色名，说清风格基因和页面类型调节即可。版式特征如'参考图本身是三栏布局'可以在这里说明：'在合适的页面会复用这种分栏感'）",
  "content_style_hint": "给生图 Prompt 使用的视觉节奏约束；只写参考资料中可执行的色彩、材质、装饰、字体层级和页面类型适配，不写选择理由或给用户看的说明"
}}

【核心原则】
1. **忠实定调**：风格名、主色关系、材质、装饰语言必须来自参考资料和用户最新风格描述，不得只根据文案另造风格
2. **不是逐页照搬**：参考图只提供风格基因，不是每一页的画面模板
3. **先定整套基底，再按页面类型调强度**：封面/章节/转场/金句页可以更强烈使用主色和装饰；内容/数据/表格/长文页必须优先可读，但要在同一视觉语言内通过卡片、内容区、字号层级和留白解决，不要机械切成另一套浅底风格
4. **内容决定配图**：地图、图表、业务场景、产品场景和人物/物件选择由该页文案决定，不机械复制参考图里的画面对象
5. **命名不跑偏**：风格名只取调性，不混入行业词，也不混入版式词（参考输出格式 name 字段的示范）

【content_style_hint 写作要求——给生图，不给用户看】
- 只写可执行的画面生成约束：主色关系、背景/内容页节奏、材质纹理、装饰强度、参考图复用边界、配图对象由每页文案决定。
- 不要写"推荐此方案"、"原因是"、"非常适合"、"需要接受的取舍"、"客户会感受到"等解释性语言。
- 不要复述用户上传了什么素材或为什么选择这套素材；只把素材转成最终画面的风格约束。"""

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

    if raw:
        proposal = _parse_llm_json(raw, expected_type=dict, context="StyleProposal(AssetBased)")
    else:
        proposal = {}

    if not proposal:
        raise RuntimeError("StyleProposal(AssetBased): LLM 未返回有效风格提案")

    # 标准化
    proposal.setdefault("name", "基于素材的定制风格")
    proposal.setdefault("mood", "专业商务")
    proposal.setdefault("font", "无衬线")
    proposal.setdefault("description", "")
    proposal.setdefault("source", "asset_based")

    proposal["palette"] = _normalize_palette_items(proposal.get("palette") if isinstance(proposal.get("palette"), list) else [])

    if len(proposal.get("description", "")) < 60:
        first_name = proposal["palette"][0].get("name", "主色") if proposal["palette"] and isinstance(proposal["palette"][0], dict) else "主色"
        proposal["description"] = f"「{proposal['name']}」是一套{proposal['mood']}的视觉方案。以{first_name}定调，封面可放大使用，内容页在同一视觉语言内保证可读性与留白。"
    if not proposal.get("content_style_hint"):
        proposal["content_style_hint"] = proposal.get("visual_rhythm") or DEFAULT_CONTENT_STYLE_HINT

    proposal["visual_strategy"] = build_visual_strategy(
        summary=summary,
        palette=proposal.get("palette") if isinstance(proposal.get("palette"), list) else None,
        reference_analysis=ref,
        logo_analysis=logo,
    )
    proposal["page_type_adaptation"] = _page_type_adaptation_rules(
        proposal.get("palette") or [],
        proposal.get("visual_strategy"),
    )
    proposal = enforce_user_style_requirements(proposal, user_desc, logo_analysis=logo)

    return [proposal]
