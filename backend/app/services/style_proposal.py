import functools
import glob
import json
import logging
import os
import re
from typing import List, Dict, Optional

import yaml

from app.core.llm_client import get_llm_client
from app.core.provider_credentials import get_minimax_llm_model
from app.services.visual_strategy import build_visual_strategy

logger = logging.getLogger(__name__)


TRADITIONAL_CULTURE_TERMS = [
    "古法", "非遗", "传承", "匠心", "老字号", "古朴", "传统", "文化",
    "中式", "东方", "国潮", "节庆", "喜庆", "宴会", "礼赠",
]
FOOD_AGRI_TERMS = ["食品", "餐饮", "农业", "花生油", "粮油", "调味品", "食用油", "风味", "香"]
TECH_TERMS = ["科技", "AI", "人工智能", "数据", "算法", "数字化", "芯片", "云计算"]
ANCIENT_ROME_TERMS = [
    "古罗马", "罗马", "角斗士", "角斗", "斗兽场", "竞技场", "Colosseum", "gladiator",
    "gladius", "凯撒", "帝国", "元老院", "军团", "罗马帝国", "血腥舞台",
]
HISTORICAL_EPIC_TERMS = [
    "古代", "史诗", "战争", "文明", "帝国", "遗迹", "神话", "历史", "博物馆",
    "文物", "考古", "雕塑", "石柱", "石刻", "青铜", "羊皮纸",
]
TOPIC_STYLE_RULES = [
    {
        "id": "ancient_rome_gladiator",
        "label": "古罗马角斗士/竞技场文化",
        "terms": ANCIENT_ROME_TERMS,
        "match_terms": [
            "古罗马", "罗马", "角斗士", "角斗", "竞技场", "斗兽场", "帝国", "军团",
            "短剑", "盾牌", "盔甲", "雕塑", "石柱", "观众席", "青铜", "石材",
        ],
        "style_name": "古罗马竞技史诗风",
        "palette": [
            {"name": "火山岩黑", "hex": "#171310", "role": "强视觉页背景/竞技场暗部"},
            {"name": "血酒红", "hex": "#7A1F1D", "role": "标题强调/冲突线索"},
            {"name": "石灰白", "hex": "#E8DDC8", "role": "正文页基底/石材留白"},
            {"name": "旧青铜", "hex": "#A8743A", "role": "徽章、编号和重点信息"},
        ],
        "mood": "史诗、粗粝、古典、戏剧化",
        "font": "标题用古典衬线或罗马碑刻感字体，正文用高可读黑体/无衬线，字形保持锐利和碑刻感。",
        "visual_language": "历史史诗、古典雕塑、石材建筑、青铜器、羊皮纸和暗红冲突感",
        "page_type_adaptation": (
            "封面/章节/金句页可强化题材场景和材质情绪；目录/正文/表格页使用低干扰信息基底，"
            "保留同一套题材色、编号和装饰语言来保证阅读效率。"
        ),
        "recommended_library_ids": [
            "classic_pop_sculpture_vaporwave",
            "magazine_editorial",
            "sports_energy",
        ],
        "score_threshold": 2,
    },
    {
        "id": "historical_culture",
        "label": "历史文化/文明叙事",
        "terms": HISTORICAL_EPIC_TERMS,
        "match_terms": ["历史", "文明", "文物", "雕塑", "博物馆", "史诗", "地图", "年表", "档案"],
        "style_name": "博物馆档案叙事风",
        "palette": [
            {"name": "档案黑", "hex": "#1D1A16", "role": "标题和章节基底"},
            {"name": "羊皮纸", "hex": "#E9DDC3", "role": "正文页基底"},
            {"name": "文物金", "hex": "#A37A3B", "role": "编号和重点证据"},
            {"name": "石灰灰", "hex": "#B8B0A2", "role": "辅助层次"},
        ],
        "mood": "历史、克制、展陈、可信",
        "font": "标题用稳重衬线，正文用清晰黑体，数据页保持高可读。",
        "visual_language": "博物馆展陈、历史杂志、古典材质、文物证据和克制档案感",
        "page_type_adaptation": "强视觉页建立时代氛围，正文页用档案、年表、地图、图注和留白组织知识点。",
        "recommended_library_ids": [
            "magazine_editorial",
            "classic_pop_sculpture_vaporwave",
            "modern_newspaper",
        ],
        "score_threshold": 3,
    },
]
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


def _infer_topic_style_profile(text: str) -> Optional[Dict]:
    for rule in TOPIC_STYLE_RULES:
        score = _score_terms(text, rule["terms"])
        if rule["id"] == "ancient_rome_gladiator" and ("罗马" in text and "角斗" in text):
            score = max(score, rule["score_threshold"])
        if score >= rule["score_threshold"]:
            match_terms = [term for term in rule["match_terms"] if term.lower() in text.lower()]
            if not match_terms:
                match_terms = list(rule["match_terms"][:5])
            return {
                **rule,
                "keywords": match_terms[:8],
                "direction": (
                    f"内容核心是{rule['label']}，风格必须先服务题材锚点："
                    f"{'、'.join(match_terms[:8])}。表现手法可多样，但视觉语言应围绕{rule['visual_language']}展开。"
                ),
            }
    return None


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
        # 从 body 中提取第一行作为 topic hint
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

        # 简单关键词提取（从 headline + subhead + body 中找行业/场景词）
        text_to_search = f"{h} {sub} {str(body) if body else ''}"
        keyword_pool = [
            "金融", "医疗", "教育", "消费", "品牌", "学术", "艺术", "设计", "汽车",
            "地产", "零售", "投资", "产品", "战略", *TECH_TERMS,
            *FOOD_AGRI_TERMS, *TRADITIONAL_CULTURE_TERMS, *ANCIENT_ROME_TERMS, *HISTORICAL_EPIC_TERMS,
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
    topic_style_profile = _infer_topic_style_profile(full_text)

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
    if topic_style_profile:
        if topic_style_profile.get("id") in {"ancient_rome_gladiator", "historical_culture"}:
            industries = [industry for industry in industries if industry != "古法非遗/传统文化"]
        industries.append(topic_style_profile["label"])
    if not industries:
        industries.append("通用商务")

    style_direction_hint = _build_content_style_direction(
        traditional_score=traditional_score,
        food_score=food_score,
        tech_score=tech_score,
        brand_score=brand_score,
    )
    if topic_style_profile:
        style_direction_hint = topic_style_profile["direction"]

    return {
        "headlines": headlines[:8],
        "page_types": list(page_types),
        "industries": list(set(industries)),
        "keywords": list(set(keywords)),
        "total_pages": len(content_plan),
        "topic_hints": topic_hints[:6],  # 用于帮助 LLM 理解内容主旨
        "style_direction_hint": style_direction_hint,
        "topic_style_profile": topic_style_profile,
        "dense_page_ratio": round(dense_pages / max(measured_pages, 1), 3),
        "table_page_ratio": round(table_pages / max(measured_pages, 1), 3),
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


def _topic_profile(summary: Dict) -> Dict:
    profile = summary.get("topic_style_profile")
    return profile if isinstance(profile, dict) else {}


def _proposal_text(proposal: Dict) -> str:
    return " ".join(
        str(proposal.get(key) or "")
        for key in ("name", "mood", "font", "description", "source", "texture", "ornaments", "clone_rules")
    )


def _proposal_matches_topic(proposal: Dict, summary: Dict) -> bool:
    profile = _topic_profile(summary)
    if not profile:
        return True

    text = _proposal_text(proposal)
    topic_terms = list(dict.fromkeys([
        *(profile.get("keywords") or []),
        *(profile.get("match_terms") or []),
        profile.get("label", ""),
    ]))
    return any(term and term in text for term in topic_terms)


def _filter_topic_mismatched_proposals(proposals: List[Dict], summary: Dict) -> List[Dict]:
    if not _topic_profile(summary):
        return proposals
    return [p for p in proposals if isinstance(p, dict) and _proposal_matches_topic(p, summary)]


def _topic_original_proposal(summary: Dict) -> Optional[Dict]:
    profile = _topic_profile(summary)
    if not profile:
        return None
    anchors = "、".join((profile.get("keywords") or profile.get("match_terms") or [])[:8])
    return {
        "name": profile["style_name"],
        "palette": profile["palette"],
        "mood": profile["mood"],
        "font": profile["font"],
        "description": (
            f"这份 PPT 讲的是{profile['label']}，视觉应先抓住{anchors}这些题材锚点，"
            f"再用{profile['visual_language']}形成统一风格。强视觉页负责定调，信息页降低装饰强度，"
            "用同一套颜色、材质和编号系统保证阅读效率。"
        ),
        "page_type_adaptation": profile["page_type_adaptation"],
        "content_style_hint": (
            f"每页画面证据必须来自{profile['label']}的题材锚点："
            f"{anchors or '核心人物、场景、物件、结构或证据'}；"
            f"整体保持{profile['visual_language']}，风格库只作为表现手法。"
        ),
        "source": "original",
    }


def _topic_decision_variants(summary: Dict) -> List[Dict]:
    """Build strong-topic proposals as distinct choices, not cosmetic variants."""
    profile = _topic_profile(summary)
    if not profile:
        return []

    anchors = "、".join((profile.get("keywords") or profile.get("match_terms") or [])[:8])
    label = profile.get("label", "当前主题")
    visual_language = profile.get("visual_language", "主题化视觉语言")
    font = profile.get("font", "标题强化主题气质，正文保持高可读。")

    if profile.get("id") == "ancient_rome_gladiator":
        return [
            {
                "name": "斗兽场暗幕",
                "decision_label": "沉浸史诗",
                "best_for": "想让观众第一眼记住斗兽场、盔甲和短剑带来的历史戏剧感。",
                "tradeoff": "正文页需要控制暗色面积，用浅色内容区保证长段落可读。",
                "visual_focus": "强视觉页使用斗兽场暗部、火山岩黑、血酒红和旧青铜材质；信息页保留同一套编号和纹理。",
                "palette": [
                    {"name": "火山岩黑", "hex": "#171310", "role": "封面/章节/金句页背景"},
                    {"name": "血酒红", "hex": "#7A1F1D", "role": "冲突线索和标题强调"},
                    {"name": "石灰白", "hex": "#E8DDC8", "role": "正文内容区和留白"},
                    {"name": "旧青铜", "hex": "#A8743A", "role": "编号、徽章和重点信息"},
                ],
                "mood": "史诗、沉浸、古典、戏剧化",
                "font": font,
                "description": (
                    "选它如果你更看重开场冲击和历史氛围。画面会把斗兽场暗部、盔甲、短剑和青铜装饰作为主记忆点，"
                    "封面和转场更像一场古罗马竞技入场；正文页则用石材浅色内容区承载信息。"
                ),
                "page_type_adaptation": (
                    "封面、章节页、金句页可深色沉浸；目录、规则解释、训练体系和表格页必须用浅色石材内容区提高阅读效率，"
                    "只保留暗红强调、青铜编号和石纹边界。"
                ),
                "content_style_hint": (
                    f"每页画面证据必须来自{label}：{anchors or '斗兽场、短剑、盾牌、盔甲、雕塑、石柱、观众席'}；"
                    "优先塑造沉浸式历史场景。"
                ),
                "source": "original_immersive_epic",
            },
            {
                "name": "石刻档案",
                "decision_label": "展陈可读",
                "best_for": "内容解释、规则、年表和知识点较多，希望观众读得清楚、觉得可信。",
                "tradeoff": "视觉冲击弱于深色沉浸方案，但更稳，更像博物馆展陈或历史读物。",
                "visual_focus": "浅石材底、文物图注、地图/制度图解、旧青铜编号和克制暗红强调。",
                "palette": [
                    {"name": "羊皮纸", "hex": "#EFE3CA", "role": "正文页和目录页基底"},
                    {"name": "碑刻黑", "hex": "#26211A", "role": "标题和正文文字"},
                    {"name": "文物金", "hex": "#A67C3D", "role": "编号、图注和重点证据"},
                    {"name": "暗酒红", "hex": "#7A1F1D", "role": "少量冲突强调"},
                ],
                "mood": "克制、可信、展陈、历史感",
                "font": font,
                "description": (
                    "选它如果你更看重清晰讲解和可信感。它把古罗马题材做成展览导览式系统：浅石材底负责阅读，"
                    "文物图注、地图、编号和暗红重点帮助观众理解角斗士制度、训练和竞技规则。"
                ),
                "page_type_adaptation": (
                    "正文、目录、时间线和制度解释页统一浅底；封面和章节页可以短暂加深背景，但仍保持展陈秩序、图注和编号系统。"
                ),
                "content_style_hint": (
                    f"每页画面证据必须来自{label}：{anchors or '斗兽场、短剑、盾牌、盔甲、雕塑、石柱、观众席'}；"
                    "优先服务知识解释、图注和结构化阅读。"
                ),
                "source": "original_exhibition_readable",
            },
            {
                "name": "竞技场硝烟",
                "decision_label": "力量冲突",
                "best_for": "想突出训练、对抗、生死规则和竞技场里的原始张力。",
                "tradeoff": "情绪更锋利，历史展陈感会少一些，需要避免每页都变成过度戏剧化海报。",
                "visual_focus": "盾牌、短剑、砂土、红色斜切动线和高对比标题块，强化动作感与生死规则。",
                "palette": [
                    {"name": "炭铁黑", "hex": "#202126", "role": "高对比背景和标题块"},
                    {"name": "战痕红", "hex": "#9A2B22", "role": "冲突线、章节标记和重点词"},
                    {"name": "沙尘土黄", "hex": "#C6A36A", "role": "竞技场地面和材质过渡"},
                    {"name": "骨白", "hex": "#EADDC4", "role": "正文区和图注文字"},
                ],
                "mood": "强烈、粗粝、紧张、运动感",
                "font": font,
                "description": (
                    "选它如果你希望这份 PPT 更有力量和现场感。它会把盾牌、短剑、砂土和红色动线作为视觉重点，"
                    "让训练、对抗和生死规则更有冲击；信息页用高对比标题块和骨白内容区控制阅读。"
                ),
                "page_type_adaptation": (
                    "封面、训练、对抗和规则转折页可使用斜切动线和强对比；知识解释页减少动效感，用稳定内容区承载文字。"
                ),
                "content_style_hint": (
                    f"每页画面证据必须来自{label}：{anchors or '斗兽场、短剑、盾牌、盔甲、雕塑、石柱、观众席'}；"
                    "优先强化训练、对抗和竞技场规则的动作张力。"
                ),
                "source": "original_arena_conflict",
            },
        ]

    if profile.get("id") == "historical_culture":
        return [
            {
                "name": "文明暗厅",
                "decision_label": "沉浸展厅",
                "best_for": "希望先建立历史厚重感，让封面和章节页像走进一间暗色展厅。",
                "tradeoff": "需要在正文页主动增加浅色内容区，否则长文阅读压力会变大。",
                "visual_focus": f"{visual_language}，用深色展厅、局部打光、文物金和题材场景定调。",
                "palette": profile["palette"],
                "mood": profile["mood"],
                "font": font,
                "description": "选它如果你更看重历史氛围和第一眼记忆。强视觉页像暗色展厅，正文页用浅色内容区承接信息，避免气氛压过知识点。",
                "page_type_adaptation": profile["page_type_adaptation"],
                "content_style_hint": profile.get("direction", ""),
                "source": "original_history_immersive",
            },
            {
                "name": "档案图注",
                "decision_label": "资料清晰",
                "best_for": "页面里有较多解释、年表、引用或事实证据，需要像历史读物一样好读。",
                "tradeoff": "气氛更克制，封面冲击力弱于沉浸展厅方案。",
                "visual_focus": "羊皮纸浅底、图注、地图、年表、编号和少量文物金重点。",
                "palette": [
                    {"name": "羊皮纸", "hex": "#E9DDC3", "role": "正文页基底"},
                    {"name": "档案黑", "hex": "#1D1A16", "role": "标题和正文文字"},
                    {"name": "文物金", "hex": "#A37A3B", "role": "编号和证据重点"},
                    {"name": "石灰灰", "hex": "#B8B0A2", "role": "辅助层次和边界"},
                ],
                "mood": "可信、克制、展陈、清晰",
                "font": font,
                "description": "选它如果你更看重读得清楚。它把历史内容转成档案、图注、地图和年表系统，适合解释复杂知识点和证据链。",
                "page_type_adaptation": "正文和数据页统一浅底；封面/章节页只做适度加深，保持图注和编号系统不断裂。",
                "content_style_hint": profile.get("direction", ""),
                "source": "original_history_archive",
            },
            {
                "name": "史诗剧照",
                "decision_label": "叙事冲击",
                "best_for": "希望每个章节像一段历史故事，有更强的戏剧性和传播感。",
                "tradeoff": "需要控制素材密度，避免叙事画面抢走事实和结构。",
                "visual_focus": "大幅场景、雕塑/遗迹剪影、强标题和少量高对比色块。",
                "palette": [
                    {"name": "夜幕黑", "hex": "#181614", "role": "强视觉页背景"},
                    {"name": "赤陶红", "hex": "#8E3B2E", "role": "章节冲突和标题强调"},
                    {"name": "砂岩米", "hex": "#D9C5A3", "role": "内容区和材质过渡"},
                    {"name": "古铜金", "hex": "#B18445", "role": "重点信息和装饰"},
                ],
                "mood": "史诗、叙事、戏剧、厚重",
                "font": font,
                "description": "选它如果你希望 PPT 更像历史纪录片分镜。章节和金句页更有剧照感，内容页保留清晰网格和图注来承载事实。",
                "page_type_adaptation": "章节/金句页强化大幅场景；正文页退回清晰网格、浅内容区和图注结构。",
                "content_style_hint": profile.get("direction", ""),
                "source": "original_history_cinematic",
            },
        ]

    return []


def _decision_archetypes(summary: Dict) -> List[Dict]:
    topic_variants = _topic_decision_variants(summary)
    if topic_variants:
        return [
            {
                key: variant[key]
                for key in ("decision_label", "best_for", "tradeoff", "visual_focus")
                if key in variant
            }
            for variant in topic_variants
        ]

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
        },
        {
            "decision_label": information_label,
            "best_for": "页数、文字或数据较多，希望阅读效率、可信感和汇报稳定性优先。",
            "tradeoff": "画面冲击力更克制，但更适合长时间讲解和逐页阅读。",
            "visual_focus": "浅底、清晰层级、图表/卡片秩序和少量强调色，降低理解成本。",
        },
        {
            "decision_label": "表达冲击",
            "best_for": "希望提案更有态度，适合路演、发布、竞标或需要快速抓住注意力的场景。",
            "tradeoff": "视觉个性更强，需要接受更高对比和更鲜明的版式节奏。",
            "visual_focus": "高对比色块、大标题、强节奏分区和更鲜明的视觉符号。",
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
            p["description"] = f"「{p['name']}」是一套{p['mood']}的视觉方案。以{first_name}定调，封面可放大使用，内容页在同一视觉语言内保证可读性与留白。"

        p.setdefault(
            "visual_strategy",
            build_visual_strategy(
                summary=summary,
                palette=p.get("palette") if isinstance(p.get("palette"), list) else None,
            ),
        )
        p.setdefault("page_type_adaptation", _page_type_adaptation_rules(p.get("palette") or [], p.get("visual_strategy")))

    return proposals[:3]


def _topic_library_description(style: Dict, summary: Dict) -> str:
    profile = _topic_profile(summary)
    style_name = style["aliases"][0] if style.get("aliases") else style.get("name", "风格库方案")
    if profile:
        anchors = "、".join((profile.get("keywords") or profile.get("match_terms") or [])[:6])
        return (
            f"我从风格库中选择了『{style_name}』作为表现手法，但题材仍然必须围绕{profile['label']}。"
            f"它应服务{anchors}等画面锚点，用{profile['visual_language']}统一封面、目录和正文页；"
            "风格库提供版式节奏和情绪强度，不能替代内容主题。"
        )
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


def _fallback_style_ids_for_summary(summary: Dict) -> List[str]:
    profile = _topic_profile(summary)
    if profile.get("recommended_library_ids"):
        return list(profile["recommended_library_ids"])
    industries = " ".join(summary.get("industries") or [])
    keywords = " ".join(summary.get("keywords") or [])
    text = f"{industries} {keywords} {summary.get('style_direction_hint', '')}"
    if any(term in text for term in TRADITIONAL_CULTURE_TERMS):
        return ["traditional_chinese", "magazine_editorial", "modern_newspaper"]
    if any(term in text for term in FOOD_AGRI_TERMS):
        return ["magazine_editorial", "paper_craft", "sharp_minimalism"]
    if _contains_unnegated_tech(text):
        return ["minimal_data", "blueprint", "executive_dashboard"]
    if "品牌" in text or "消费" in text:
        return ["magazine_editorial", "strategic_infographic", "sharp_minimalism"]
    return ["magazine_editorial", "strategic_infographic", "sharp_minimalism"]


def _append_content_aware_fallbacks(proposals: List[Dict], summary: Dict, style_library: List[Dict]) -> List[Dict]:
    topic_original = _topic_original_proposal(summary)
    if topic_original and not any(_proposal_matches_topic(p, summary) for p in proposals):
        proposals.append(topic_original)

    fallback_map = {s["id"]: s for s in style_library}
    used_sources = {str(p.get("source") or "") for p in proposals if isinstance(p, dict)}
    for fid in _fallback_style_ids_for_summary(summary):
        if len(proposals) >= 3:
            break
        if fid in fallback_map and fid not in used_sources:
            proposals.append(_proposal_from_style_library(fallback_map[fid], summary))
            used_sources.add(fid)

    if topic_original and len(proposals) < 3:
        variants = [
            {
                **topic_original,
                "name": f"{_topic_profile(summary).get('label', '主题')}展陈信息风",
                "description": (
                    f"这份 PPT 讲的是{_topic_profile(summary).get('label', '当前主题')}，可以用展陈信息逻辑处理大量知识点。"
                    "正文页用清晰图注、编号、地图或结构图承载信息，强视觉页保留题材场景与材质气质，"
                    "保证既有主题记忆，也能读清目录、规则和解释性内容。"
                ),
                "source": "original_museum_variant",
            },
            {
                **topic_original,
                "name": f"{_topic_profile(summary).get('label', '主题')}沉浸叙事风",
                "description": (
                    f"这份 PPT 的主角是{_topic_profile(summary).get('label', '当前主题')}，适合把封面和转场做成沉浸式叙事场景。"
                    "内容页降低情绪强度，用同一套题材色、材质和编号系统承载长文，"
                    "让观众持续感到这是同一个主题世界，而不是通用商务模板。"
                ),
                "source": "original_arena_variant",
            },
        ]
        for variant in variants:
            if len(proposals) < 3:
                proposals.append(variant)
    return proposals


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


def _reference_explicitly_traditional(ref: Dict) -> bool:
    text = " ".join(
        str(ref.get(key) or "")
        for key in ("style_name", "description", "mood", "ornaments", "texture", "clone_rules", "composition_style")
    )
    return bool(text and any(term in text for term in TRADITIONAL_CULTURE_TERMS))


def _page_type_adaptation_rules(palette: List[Dict], visual_strategy: Dict | None = None) -> str:
    primary = palette[0]["hex"] if palette else "#2F2A24"
    accent = palette[1]["hex"] if len(palette) > 1 else "#B8945C"
    strategy = visual_strategy or {}
    base_tone = str(strategy.get("base_tone") or "").lower()

    if base_tone == "dark":
        return (
            "页面类型适配规则：先保持整套深色视觉基底，再按页面功能调节强弱。"
            f"封面、章节页、转场页、金句页可放大使用品牌主色 {primary} 和强调色 {accent}，承担品牌定调和情绪冲击；"
            "内容页、数据页、表格页仍使用同一深色系语言，通过高对比暗色卡片、局部浅色内容区、清晰字号层级和留白保证阅读效率。"
            "除非用户明确要求或出现极端表格/长文页，不要把正文页自动切成米白、浅灰等另一套视觉语言。"
        )
    if base_tone == "light":
        return (
            "页面类型适配规则：正文页以浅底和留白保证阅读效率，强视觉页可使用更深的主色或装饰区形成节奏。"
            f"用 {primary} 做标题、页眉、编号、强调块，用 {accent} 做少量装饰线和重点信息；"
            "深色页只用于封面、章节、金句或明确需要情绪冲击的页面，不能在正文页随机混用。"
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


def _build_reference_clone_proposal(summary: Dict, assets: Dict) -> Dict:
    logo = assets.get("logo_analysis") or {}
    ref = assets.get("reference_analysis") or {}
    template = assets.get("template_analysis") or {}
    template_ref = template.get("reference_analysis") if isinstance(template, dict) else None
    if template_ref and not (ref.get("description") or ref.get("dominant_palette")):
        ref = template_ref

    palette = _collect_clone_palette(ref, logo)
    palette_hex = [c["hex"] for c in palette]
    explicit_traditional_ref = _reference_explicitly_traditional(ref)
    style_name = (ref.get("style_name") or "").strip()
    if not style_name:
        desc_for_name = " ".join(str(x) for x in [ref.get("description"), ref.get("mood"), ref.get("ornaments"), ref.get("texture")] if x)
        if explicit_traditional_ref and any(_is_chromatic_brand_color(c) for c in palette_hex) and any(_is_warm_accent(c) for c in palette_hex):
            style_name = "品牌主色典雅"
        elif explicit_traditional_ref and any(_is_dark(c) for c in palette_hex) and any(_is_warm_accent(c) for c in palette_hex):
            style_name = "深色典雅"
        elif "国潮" in desc_for_name or "中式" in desc_for_name:
            style_name = "中式典雅"
        elif any(_is_chromatic_brand_color(c) for c in palette_hex):
            style_name = "品牌主色复刻"
        elif any(_is_dark(c) for c in palette_hex):
            style_name = "深色参考质感"
        else:
            style_name = "参考图复刻"

    # 用户给了参考图时，风格命名必须来自图像，不混入内容里的“科技/战略”等词。
    style_name = re.sub(r"(科技|战略|未来|数据|智能|AI)", "", style_name, flags=re.IGNORECASE).strip(" -_·")
    if not style_name:
        style_name = "参考图复刻"
    if explicit_traditional_ref and any(_is_chromatic_brand_color(c) for c in palette_hex) and any(_is_warm_accent(c) for c in palette_hex) and style_name == "参考图复刻":
        style_name = "品牌主色典雅"

    mood = ref.get("mood") or ("古朴、典雅、厚重" if explicit_traditional_ref else "现代、清晰、克制")
    font = ref.get("font_suggestion") or logo.get("font_style") or (
        "标题使用文化感较强的宋体/书法体，正文使用清晰黑体"
        if explicit_traditional_ref
        else "标题使用现代黑体/几何无衬线，正文使用清晰黑体，整套保持同一字体系"
    )
    composition = ref.get("composition_style") or "沿用参考图的版式节奏"
    ornaments = ref.get("ornaments") or (
        "沿用参考图中的装饰纹样与边框语言"
        if explicit_traditional_ref
        else "只沿用参考图中明确出现的装饰语言；没有明确装饰时使用简洁几何线条"
    )
    texture = ref.get("texture") or (
        "沿用参考图的背景肌理与光影层次"
        if explicit_traditional_ref
        else "沿用参考图的背景质感；没有明确肌理时保持干净现代"
    )
    clone_rules = ref.get("clone_rules") or (
        "提取参考图的主色关系、装饰气质和整体氛围，并按页面类型调节使用强度。"
        if explicit_traditional_ref
        else "提取参考图的主色关系、版式节奏和整体氛围，按页面类型调节强度；不要引入参考图中没有的文化符号。"
    )
    visual_strategy = build_visual_strategy(
        summary=summary,
        palette=palette,
        reference_analysis=ref,
        logo_analysis=logo,
    )
    adaptation_rules = _page_type_adaptation_rules(palette, visual_strategy)

    primary_name = _get_color_name(palette[0]['hex']) if palette else '品牌主色'
    accent_name = _get_color_name(palette[1]['hex']) if len(palette) > 1 else '强调色'
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
        "source": "asset_clone",
        "clone_mode": "style_dna",
        "reference_usage": "style_text_only",
        "page_type_adaptation": adaptation_rules,
        "visual_strategy": visual_strategy,
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

    topic_variants = _topic_decision_variants(summary)
    if topic_variants:
        logger.info("StyleProposal: using deterministic topic decision variants for profile=%s", _topic_profile(summary).get("id"))
        return _finalize_style_proposals(topic_variants, summary)

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

【强题材一致性要求】
- 如果内容主题已经指向明确时代、地域、人物、场景或文化类型，三套方案都必须围绕这个题材建立视觉语言。
- 风格库只能作为表现手法，不能替代题材本身；例如古罗马角斗士不能被改写成瑞士设计、苹果发布会或泛奢侈品风。
- 任何方案名称和说明都必须让客户一眼看出它服务于当前 PPT 主题，而不是通用模板。

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

    filtered_proposals = _filter_topic_mismatched_proposals(proposals, summary)
    if len(filtered_proposals) != len(proposals):
        logger.warning(
            "StyleProposal: filtered %s topic-mismatched proposals for profile=%s",
            len(proposals) - len(filtered_proposals),
            _topic_profile(summary).get("id"),
        )
    proposals = filtered_proposals

    # 如果解析失败或数量不足，用 style 库兜底
    if len(proposals) < 3:
        logger.info("StyleProposal: 使用内容感知兜底")
        proposals = _append_content_aware_fallbacks(proposals, summary, style_library)

    return _finalize_style_proposals(proposals, summary)


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
    {{"name": "直观颜色名", "hex": "参考图中可承载正文的底色或内容区颜色", "role": "正文页基底/内容区"}},
    {{"name": "直观颜色名", "hex": "高可读文字色", "role": "正文/数据文字"}}
  ],
  "mood": "氛围标签（忠实来自参考图，不发明新风格）",
  "font": "字体建议（延续参考图字体气质，同时保证正文可读）",
  "description": "风格说明（80-120字，不要出现色号，用直观颜色名，说清风格基因和页面类型调节即可。版式特征如'参考图本身是三栏布局'可以在这里说明"在合适的页面会复用这种分栏感"）"
}}

【核心原则】
1. **忠实定调**：风格名、主色关系、材质、装饰语言必须来自参考图，不得根据文案另造风格
2. **不是逐页照搬**：参考图只提供风格基因，不是每一页的画面模板
3. **先定整套基底，再按页面类型调强度**：封面/章节/转场/金句页可以更强烈使用主色和装饰；内容/数据/表格/长文页必须优先可读，但要在同一视觉语言内通过卡片、内容区、字号层级和留白解决，不要机械切成另一套浅底风格
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
        logger.info("StyleProposal(AssetBased): 使用内容感知兜底")
        fallbacks = _append_content_aware_fallbacks([], summary, style_library)
        proposal = fallbacks[0] if fallbacks else {}

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
        proposal["description"] = f"「{proposal['name']}」是一套{proposal['mood']}的视觉方案。以{first_name}定调，封面可放大使用，内容页在同一视觉语言内保证可读性与留白。"

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

    return [proposal]
