import json
import json_repair
import logging
import re
import time
from dataclasses import dataclass
from typing import Callable, Dict, List

from app.core.config import settings
from app.core.llm_client import get_llm_client
from app.core.provider_credentials import get_minimax_llm_model
from app.services.content_director import (
    content_director_contract_to_legacy_intent,
    infer_content_director_contract,
    is_content_director_contract,
    normalize_content_director_contract,
)
from app.services.document_parser import detect_ppt_sources
from app.services.pptx_page_recovery import extract_ocr_text_from_unstructured_description
from app.services.search_service import get_knowledge_augmenter
from app.services.source_intent import (
    contract_to_planning_policy,
    infer_intent_contract as infer_legacy_intent_contract,
    normalize_intent_contract as normalize_legacy_intent_contract,
    source_diagnostics_from_documents,
)
from app.services.visual_directives import separate_visual_directives_from_page
from app.utils.text_cleaning import (
    is_markdown_thematic_break_line,
    normalize_markdown_content,
    normalize_markdown_emphasis,
    remove_markdown_structural_noise,
)

logger = logging.getLogger(__name__)

LONG_DECK_INCREMENTAL_THRESHOLD = 40
LONG_DECK_CHUNK_SIZE = 2
LONG_DECK_CHUNK_TIMEOUT_SECONDS = 60.0
LONG_DECK_SYNC_ENRICHMENT_PAGE_LIMIT = 0

LONG_DECK_SECTION_TITLES = [
    "开场定调：明确目标、受众语境和核心问题",
    "背景与痛点：解释为什么现在必须讨论这个主题",
    "总框架：建立整份内容的主线模型和判断标准",
    "模块一：拆解第一组关键概念、案例和常见误区",
    "模块二：展开方法步骤、工具和可迁移经验",
    "模块三：连接真实业务场景、决策问题和行动方案",
    "关键收束：回到主线，整合判断与行动方向",
    "总结收束：回到主线，给出行动清单和结束页",
]

LOW_CONTENT_DRAFT_STATUSES = {"skeleton", "needs_review"}
SKELETON_PLACEHOLDER_MARKERS = (
    "本页已先放入长篇 PPT 结构中",
    "内容待细化",
    "占位备注",
    "待内容细化后",
    "后续分段生成会",
)
PAGE_MAP_FORMAT_PLACEHOLDERS = {
    "标题",
    "副标题",
    "bullet",
    "bullets",
    "...",
    "……",
    "内容",
    "正文",
    "要点",
    "演讲者备注",
    "画面建议",
    "材料线索",
    "来源",
    "章节",
    "真实标题",
    "具体要点",
    "用用户材料写出的封面标题",
    "用用户材料写出的具体要点",
}
PAGE_MAP_MODEL_TIMEOUT_SECONDS = 150.0
PAGE_MAP_BASE_MAX_TOKENS = 16_000
PAGE_MAP_MAX_TOKENS_CAP = 64_000
PAGE_MAP_TOKENS_PER_PAGE = 650
PAGE_MAP_DOCUMENT_LIMIT = max(30_000, int(settings.CONTENT_PLAN_PAGE_MAP_DOCUMENT_CHAR_LIMIT or 180_000))
PAGE_MAP_SOURCE_DRAFT_LIMIT = max(18_000, int(settings.CONTENT_PLAN_PAGE_MAP_SOURCE_DRAFT_CHAR_LIMIT or 90_000))
PAGE_MAP_USEFUL_RATIO = 0.75
AUTO_DOCUMENT_PAGE_MIN_CHARS = 5000
AUTO_DOCUMENT_PAGE_MIN = 12
AUTO_DOCUMENT_PAGE_MAX = 60
AUTO_DOCUMENT_CHARS_PER_SLIDE = 700
AUTO_RESTORATION_PAGE_MAX = 100
MIN_UNPROMPTED_CONTENT_PLAN_PAGE_COUNT = 3
EXPANDED_OUTLINE_OVERRIDE_MIN_PAGES = 12

CONTENT_PLAN_STRATEGY_REUSE_EXPORTED = "reuse_exported_plan"
CONTENT_PLAN_STRATEGY_REUSE_PAGINATED = "reuse_paginated_markdown"
CONTENT_PLAN_STRATEGY_LONG_DECK = "long_structured_deck"
CONTENT_PLAN_STRATEGY_PAGE_MAP = "page_map"
CANONICAL_CONTENT_PLAN_TYPES = {"cover", "toc", "section", "content", "data", "hero", "quote", "ending"}
CONTENT_BODY_REQUIRED_TYPES = {"content", "data"}
CONTENT_PLAN_TYPE_ALIASES = {
    "agenda": "toc",
    "outline": "toc",
    "目录": "toc",
    "目录页": "toc",
    "课程目录": "toc",
    "section_cover": "section",
    "sectioncover": "section",
    "chapter_cover": "section",
    "chaptercover": "section",
    "chapter": "section",
    "divider": "section",
    "章节": "section",
    "章节页": "section",
    "章节封面": "section",
    "章封面": "section",
    "core_judgment": "content",
    "corejudgment": "content",
    "framework": "content",
    "case": "content",
    "quiz": "content",
    "checklist": "content",
    "transition": "content",
    "summary": "content",
    "method": "content",
    "action": "content",
    "insight": "content",
    "story": "content",
    "正文": "content",
    "内容": "content",
    "金句": "hero",
    "quote": "quote",
    "quotation": "quote",
    "quote_slide": "quote",
    "quoteslide": "quote",
    "引用": "quote",
    "引用页": "quote",
    "名人名言": "quote",
    "名人名言页": "quote",
    "punchline": "hero",
    "golden_sentence": "hero",
    "goldensentence": "hero",
    "数据": "data",
    "chart": "data",
    "封面": "cover",
    "封底": "ending",
    "结尾": "ending",
    "closing": "ending",
    "conclusion": "ending",
}


@dataclass
class ContentPlanJob:
    topic: str
    audience: str
    page_count: int
    min_pages: int
    max_pages: int
    documents: str
    has_docs: bool
    requested_page_range: tuple[int, int] | None
    strict_page_count: bool
    allow_expanded_outline_override: bool
    intent_contract: dict
    ppt_sources: list[dict]
    planning_policy: dict
    mode: str = "default"
    search_context: str = ""
    exported_outline: list[dict] | None = None
    paginated_markdown_outline: list[dict] | None = None


def _clean_json_response(content: str) -> str:
    """从 LLM 响应中提取 JSON 数组。"""
    content = re.sub(r"^```(?:json)?\s*|```$", "", content, flags=re.MULTILINE | re.IGNORECASE).strip()
    start_idx = content.find("[")
    end_idx = content.rfind("]")
    if start_idx != -1 and end_idx != -1:
        content = content[start_idx : end_idx + 1]
    return content


def _parse_outline_json_response(content: str) -> List[Dict]:
    clean = re.sub(r"<think>.*?</think>", "", content or "", flags=re.DOTALL).strip()
    clean = _clean_json_response(clean)
    outline = None
    parse_errors: list[str] = []

    try:
        outline = json_repair.loads(clean)
    except Exception as e:
        parse_errors.append(f"json_repair: {e}")

    if outline is None:
        try:
            outline = json.loads(clean)
        except Exception as e:
            parse_errors.append(f"json.loads: {e}")

    if outline is None:
        first_obj = clean.find("{")
        last_arr = clean.rfind("]")
        if first_obj != -1 and last_arr != -1 and first_obj < last_arr:
            snippet = "[" + clean[first_obj:last_arr + 1].replace("}\n{", "},\n{") + "]"
            try:
                outline = json_repair.loads(snippet)
            except Exception as e:
                parse_errors.append(f"snippet repair: {e}")

    if outline is None:
        preview = clean[:500].replace("\n", " ")
        logger.error(f"[ContentPlan] JSON parse failed after all fixes. Preview: {preview!r}")
        logger.error(f"[ContentPlan] Errors: {'; '.join(parse_errors)}")
        raise ValueError(f"Content plan generation failed: invalid JSON from LLM. Preview: {preview[:200]}")
    if not isinstance(outline, list):
        raise ValueError("Content plan generation failed: LLM output is not a JSON array")
    return [page for page in outline if isinstance(page, dict)]


def _parse_export_attr(attrs: str, key: str) -> str:
    match = re.search(rf'{re.escape(key)}=("(?:\\.|[^"])*"|[^\s>]+)', attrs or "")
    if not match:
        return ""
    raw = match.group(1).strip()
    if raw.startswith('"') and raw.endswith('"'):
        try:
            return str(json.loads(raw))
        except Exception:
            return raw[1:-1]
    return raw


def _clean_export_section_value(value: str) -> str:
    text = (value or "").strip()
    if text in {"<!-- 留空 -->", "<!--留空-->"}:
        return ""
    return text


def _parse_export_sections(page_markdown: str) -> dict[str, str]:
    section_alias = {
        "标题": "headline",
        "副标题": "subhead",
        "正文": "body",
        "备注": "speaker_notes",
        "演讲备注": "speaker_notes",
        "备注（演讲备注）": "speaker_notes",
    }
    labels = "|".join(re.escape(label) for label in section_alias)
    pattern = re.compile(
        rf"^###\s*({labels})\s*\n+(.*?)(?=^###\s*(?:{labels})\s*\n+|\Z)",
        flags=re.MULTILINE | re.DOTALL,
    )
    values: dict[str, str] = {}
    for match in pattern.finditer(page_markdown or ""):
        key = section_alias.get(match.group(1).strip())
        if key:
            values[key] = _clean_export_section_value(match.group(2))
    return values


def parse_exported_content_plan_markdown(documents: str) -> list[dict]:
    """Parse PPT God's lightweight Markdown export back into content plan pages."""
    if "PPTGOD_EXPORT_KIND: content_plan_markdown" not in (documents or ""):
        return []
    page_pattern = re.compile(
        r"<!--\s*PPTGOD_PAGE_START\s+([^>]*)-->(.*?)<!--\s*PPTGOD_PAGE_END\s+page_num=\d+\s*-->",
        flags=re.DOTALL,
    )
    pages: list[dict] = []
    for idx, match in enumerate(page_pattern.finditer(documents), start=1):
        attrs = match.group(1)
        page_markdown = match.group(2)
        try:
            page_num = int(_parse_export_attr(attrs, "page_num") or idx)
        except ValueError:
            page_num = idx
        page_type = _parse_export_attr(attrs, "type") or "content"
        section_title = _parse_export_attr(attrs, "section_title")
        sections = _parse_export_sections(page_markdown)
        pages.append({
            "page_num": page_num,
            "type": page_type,
            "section_title": section_title,
            "text_content": {
                "headline": sections.get("headline", ""),
                "subhead": sections.get("subhead", ""),
                "body": sections.get("body", ""),
            },
            "speaker_notes": sections.get("speaker_notes", ""),
            "visual_suggestion": "",
            "source_refs": [],
        })

    pages.sort(key=lambda page: int(page.get("page_num") or 0))
    for idx, page in enumerate(pages, start=1):
        page["page_num"] = idx
    return _normalize_content_markdown(pages) if pages else []


def content_plan_topic_with_chat_context(topic: str, chat_context: str | None = None) -> str:
    chat_context_text = (chat_context or "").strip()
    if not chat_context_text:
        return topic
    return (
        "【⚠ 用户对内容规划的最新反馈 — 必须采纳】\n"
        f"{chat_context_text}\n"
        "—— 以上是用户最新指令，优先于下方的原始主题。\n\n"
        "【原始主题】\n"
        f"{topic}"
    )


def _is_strict_page_count_request(topic: str) -> bool:
    text = topic or ""
    strict_patterns = (
        r"一定要\s*\d+\s*页",
        r"必须\s*\d+\s*页",
        r"严格\s*\d+\s*页",
        r"固定\s*\d+\s*页",
        r"只能\s*\d+\s*页",
        r"只要\s*\d+\s*(?:页|頁|张|張|pages?|slides?)?",
        r"一页(?:都)?不能(?:多|少)",
        r"不能(?:多|少)一页",
        r"exactly\s+\d+\s+pages?",
        r"must\s+be\s+\d+\s+pages?",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in strict_patterns)


def _is_hard_upper_page_count_request(topic: str) -> bool:
    text = _normalize_page_count_digits(topic or "")
    patterns = (
        r"不要超过\s*\d+\s*(?:页|頁|张|張|pages?|slides?)?",
        r"不超过\s*\d+\s*(?:页|頁|张|張|pages?|slides?)?",
        r"不多于\s*\d+\s*(?:页|頁|张|張|pages?|slides?)?",
        r"最多\s*\d+\s*(?:页|頁|张|張|pages?|slides?)?",
        r"至多\s*\d+\s*(?:页|頁|张|張|pages?|slides?)?",
        r"(?:max(?:imum)?|at\s*most)\s*\d+\s*(?:pages?|slides?)?",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _normalize_page_count_digits(text: str) -> str:
    normalized = (text or "").translate(str.maketrans("０１２３４５６７８９", "0123456789"))

    chinese_count = r"[一二两三四五六七八九十]{1,4}"
    chinese_digit = r"[一二两三四五六七八九]"
    page_unit = r"(?:页|頁|张|張|pages?|slides?)"
    count_label = r"(?:页数|頁数|页面数|頁面数|张数|張数|slide\s*count|page\s*count|slides?|pages?)"

    def replace_chinese_count(match: re.Match) -> str:
        value = _parse_chinese_page_count(match.group("count"))
        return str(value) if value else match.group("count")

    def replace_range(match: re.Match) -> str:
        start = _parse_chinese_page_count(match.group("start"))
        end = _parse_chinese_page_count(match.group("end"))
        if not start or not end:
            return match.group(0)
        return f"{start}{match.group('sep')}{end}{match.group('unit')}"

    def replace_colloquial_tens_range(match: re.Match) -> str:
        start = _parse_chinese_page_count(match.group("start"))
        end = _parse_chinese_page_count(match.group("end"))
        if not start or not end:
            return match.group(0)
        return f"{start * 10}-{end * 10}{match.group('unit')}"

    normalized = re.sub(
        rf"(?P<start>{chinese_digit})(?P<end>{chinese_digit})十\s*(?P<unit>{page_unit})",
        replace_colloquial_tens_range,
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        rf"(?P<start>{chinese_count})\s*(?P<sep>-|~|～|—|–|－|到|至|to)\s*(?P<end>{chinese_count})\s*(?P<unit>{page_unit})",
        replace_range,
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        rf"(?P<count>{chinese_count})\s*(?={page_unit})",
        replace_chinese_count,
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        rf"(?P<label>{count_label}\D{{0,12}})(?P<count>{chinese_count})",
        lambda match: match.group("label") + replace_chinese_count(match),
        normalized,
        flags=re.IGNORECASE,
    )
    return normalized


def _parse_chinese_page_count(value: str | None) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    digits = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if text == "十":
        return 10
    if "十" in text:
        left, _, right = text.partition("十")
        tens = digits.get(left, 1 if left == "" else 0)
        ones = digits.get(right, 0 if right == "" else -1)
        value = tens * 10 + ones
        return value if 1 <= value <= 200 else None
    if len(text) == 1:
        return digits.get(text)
    return None


def _coerce_requested_page_count(value: str | int | None) -> int | None:
    try:
        page_count = int(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    return page_count if 1 <= page_count <= 200 else None


def resolve_requested_content_plan_page_count(topic: str, page_count: int | None) -> int | None:
    """Resolve a page-count request without letting an LLM estimate become a tiny deck.

    User-visible topic text is authoritative. A separate page_count can come from
    Agent estimates or UI payloads, so implausibly small values are ignored unless
    the user explicitly asked for that size in the topic.
    """
    topic_page_count = infer_page_count_from_topic(topic)
    if topic_page_count:
        return topic_page_count
    supplied_page_count = _coerce_requested_page_count(page_count)
    if not supplied_page_count:
        return None
    if supplied_page_count < MIN_UNPROMPTED_CONTENT_PLAN_PAGE_COUNT:
        logger.info(
            "ContentPlan: ignored unprompted low page_count=%s for topic='%s'",
            supplied_page_count,
            (topic or "")[:80],
        )
        return None
    return supplied_page_count


def _looks_like_slide_reference(text: str, index: int) -> bool:
    return bool(re.search(r"(?:第|P)\s*$", text[max(0, index - 4):index], flags=re.IGNORECASE))


PAGE_COUNT_CONTEXT_RE = re.compile(
    r"(页数|頁数|页面数|頁面数|张数|張数|PPT|幻灯片|簡報|课件|课程|培训|内训|演讲|讲课|"
    r"大纲|规划|重新|重做|做|做成|做一份|扩成|扩展|拓展|压缩|缩减|控制|目标|最终|生成|约|左右|"
    r"以内|以上|不少于|至少|不低于|不超过|不多于|最多|至多|deck|slides?|pages?|presentation|workshop|training)",
    flags=re.IGNORECASE,
)


def _has_page_count_context(text: str, start: int, end: int) -> bool:
    snippet = text[max(0, start - 24):min(len(text), end + 24)]
    return bool(PAGE_COUNT_CONTEXT_RE.search(snippet))


def _number_is_percent(text: str, end: int) -> bool:
    return bool(re.match(r"\s*[％%]", text[end:end + 3]))


def _looks_like_per_page_expression(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 8):start]
    after = text[end:min(len(text), end + 12)]
    page_unit = r"(?:页|頁|张|張|pages?|slides?)"
    return bool(
        re.search(r"每\s*$", before, flags=re.IGNORECASE)
        and re.match(rf"\s*{page_unit}", after, flags=re.IGNORECASE)
    ) or bool(
        re.search(rf"每\s*{page_unit}\s*$", before, flags=re.IGNORECASE)
    )


def _looks_like_numbered_list_marker(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 8):start]
    after = text[end:min(len(text), end + 3)]
    return bool(re.search(r"(?:^|\n)\s*$", before) and re.match(r"\s*[.．、)）:：]", after))


def infer_page_count_range_from_topic(topic: str) -> tuple[int, int] | None:
    """Infer an explicit page-count range from user-facing brief text."""
    text = _normalize_page_count_digits(topic or "")
    page_unit = r"(?:页|頁|张|張|pages?|slides?)"
    count_label = r"(?:页数|頁数|页面数|頁面数|张数|張数|slide\s*count|page\s*count|slides?|pages?)"
    range_patterns = (
        rf"(?:不少于|至少|不低于|min(?:imum)?|at\s*least)\s*(\d{{1,3}})\s*{page_unit}?.{{0,24}}(?:不超过|不多于|最多|至多|max(?:imum)?|at\s*most)\s*(\d{{1,3}})\s*{page_unit}?",
        rf"(?:不超过|不多于|最多|至多|max(?:imum)?|at\s*most)\s*(\d{{1,3}})\s*{page_unit}?.{{0,24}}(?:不少于|至少|不低于|min(?:imum)?|at\s*least)\s*(\d{{1,3}})\s*{page_unit}?",
        rf"{count_label}\D{{0,12}}(\d{{1,3}})\s*(?:-|~|～|—|–|－|到|至|to)\s*(\d{{1,3}})",
        rf"(\d{{1,3}})\s*{page_unit}?\s*(?:-|~|～|—|–|－|到|至|to)\s*(\d{{1,3}})\s*{page_unit}",
        rf"(?:between|from)\s+(\d{{1,3}})\s+(?:and|to)\s+(\d{{1,3}})\s*{page_unit}",
    )
    for pattern in range_patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            if _looks_like_slide_reference(text, match.start(1)):
                continue
            if _number_is_percent(text, match.end(1)) or _number_is_percent(text, match.end(2)):
                continue
            start = _coerce_requested_page_count(match.group(1))
            end = _coerce_requested_page_count(match.group(2))
            if start and end and (min(start, end) >= 20 or _has_page_count_context(text, match.start(1), match.end(2))):
                return (min(start, end), max(start, end))
    upper_bound_patterns = (
        rf"(?:不要超过|不超过|不多于|最多|至多|max(?:imum)?|at\s*most)\s*(\d{{1,3}})\s*{page_unit}?",
    )
    for pattern in upper_bound_patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            if _number_is_percent(text, match.end(1)):
                continue
            page_count = _coerce_requested_page_count(match.group(1))
            if page_count:
                return (1, page_count)
    return None


def infer_page_count_from_topic(topic: str) -> int | None:
    """Infer the user's requested deck size from topic/brief text."""
    text = _normalize_page_count_digits(topic or "")
    requested_range = infer_page_count_range_from_topic(text)
    if requested_range:
        return requested_range[1]

    page_unit = r"(?:页|頁|张|張|pages?|slides?)"
    count_label = r"(?:页数|頁数|页面数|頁面数|张数|張数|slide\s*count|page\s*count|slides?|pages?)"
    exact_patterns = (
        rf"(\d{{1,3}})\s*{page_unit}",
        rf"{page_unit}\s*(\d{{1,3}})",
        rf"{count_label}\D{{0,12}}(\d{{1,3}})",
    )
    for pattern in exact_patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            if _looks_like_slide_reference(text, match.start(1)):
                continue
            if _looks_like_numbered_list_marker(text, match.start(1), match.end(1)):
                continue
            if _looks_like_per_page_expression(text, match.start(1), match.end(1)):
                continue
            if _number_is_percent(text, match.end(1)):
                continue
            page_count = _coerce_requested_page_count(match.group(1))
            if page_count and _has_page_count_context(text, match.start(1), match.end(1)):
                return page_count
    return None


def _has_explicit_short_page_count_request(topic: str, page_count: int) -> bool:
    if page_count >= MIN_UNPROMPTED_CONTENT_PLAN_PAGE_COUNT:
        return False
    text = _normalize_page_count_digits(topic or "")
    page_unit = r"(?:页|頁|张|張|pages?|slides?)"
    count_label = r"(?:页数|頁数|页面数|頁面数|张数|張数|slide\s*count|page\s*count|slides?|pages?)"
    count = re.escape(str(page_count))
    patterns = (
        rf"{count}\s*{page_unit}",
        rf"{page_unit}\s*{count}",
        rf"{count_label}\D{{0,12}}{count}",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _soft_page_bounds(page_count: int) -> tuple[int, int]:
    target = max(1, int(page_count or 1))
    if target <= 12:
        lower = max(1, target - 1)
        upper = target + 1
    elif target <= 30:
        lower = max(1, target - 2)
        upper = target + 2
    else:
        lower = max(1, int(target * 0.85))
        upper = max(target, int(target * 1.15 + 0.999))
    return lower, upper


def _is_ppt_transform_request(topic: str) -> bool:
    text = (topic or "").lower()
    patterns = (
        r"扩展到\s*\d+\s*页",
        r"扩展成\s*\d+\s*(?:-|~|～|到|至)?\s*\d*\s*页",
        r"拓展到\s*\d+\s*页",
        r"拓展成\s*\d+\s*(?:-|~|～|到|至)?\s*\d*\s*页",
        r"扩成\s*\d+\s*(?:-|~|～|到|至)?\s*\d*\s*页",
        r"做成\s*\d+\s*(?:-|~|～|到|至)?\s*\d*\s*页",
        r"缩减到\s*\d+\s*页",
        r"压缩到\s*\d+\s*页",
        r"减少到\s*\d+\s*页",
        r"提取",
        r"融合",
        r"合并",
        r"重组",
        r"改写",
        r"重新规划",
        r"只要",
        r"某个主题",
        r"特定主题",
        r"expand to\s+\d+\s+pages?",
        r"reduce to\s+\d+\s+pages?",
        r"extract",
        r"merge",
        r"combine",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _is_general_transform_request(topic: str) -> bool:
    text = (topic or "").lower()
    patterns = (
        r"摘要",
        r"总结",
        r"提炼",
        r"压缩",
        r"精简",
        r"缩减",
        r"扩展到\s*\d+\s*页",
        r"扩展成\s*\d+\s*(?:-|~|～|到|至)?\s*\d*\s*页",
        r"拓展到\s*\d+\s*页",
        r"拓展成\s*\d+\s*(?:-|~|～|到|至)?\s*\d*\s*页",
        r"扩成\s*\d+\s*(?:-|~|～|到|至)?\s*\d*\s*页",
        r"做成\s*\d+\s*(?:-|~|～|到|至)?\s*\d*\s*页",
        r"重组",
        r"融合",
        r"合并",
        r"改写",
        r"重新规划",
        r"提取",
        r"只要",
        r"特定主题",
        r"summarize",
        r"summary",
        r"extract",
        r"condense",
        r"rewrite",
        r"restructure",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


_SOURCE_PRESERVATION_REQUEST_PATTERNS = (
    r"原文(?:内容)?(?:的)?字眼",
    r"原文字眼",
    r"(?:保留|保持).{0,12}(?:原文|原话|原句).{0,12}(?:结构|金句|主线|要点|内容)",
    r"(?:保留|保持).{0,12}(?:原文|原话|原句).{0,12}(?:字眼|措辞|表达|说法)",
    r"(?:尽量|不要|别).{0,12}改.{0,12}(?:原文|原话|原句|字眼|措辞|表达|内容)",
    r"完整还原(?:原文|材料|讲稿|内容)?",
    r"尽量保留原文",
    r"尽量保持原文",
    r"(?:不要|别|不能).{0,8}(?:遗漏|漏掉|跳过).{0,12}(?:全部|所有|每个|任何)?(?:章节|结构|内容|原文|材料|模块|小节)",
    r"(?:逐章|逐节|逐段|按章节|按原文顺序).{0,12}(?:展开|讲清楚|保留|覆盖|还原)",
    r"(?:覆盖|保留|讲清楚).{0,12}(?:全部|所有|每个|完整).{0,12}(?:章节|结构|内容|模块|小节)",
    r"(?:cover|preserve|retain).{0,20}(?:all|every|full).{0,20}(?:source|section|chapter|module)",
    r"(?:do not|don't|dont).{0,12}(?:omit|skip).{0,20}(?:source|section|chapter|module|content)",
)


def _topic_requests_source_preservation(topic: str) -> bool:
    text = str(topic or "")
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _SOURCE_PRESERVATION_REQUEST_PATTERNS)


def _strengthen_source_preserve_director_contract(contract: dict | None, topic: str) -> dict:
    normalized = normalize_content_director_contract(contract)
    evidence = list(normalized.get("evidence") or [])
    evidence.append("用户明确要求保持原文内容/字眼")
    normalized.update({
        "task_type": "teaching_deck" if normalized["task_type"] in {"summary", "source_to_ppt"} else normalized["task_type"],
        "source_use": "verbatim" if normalized["source_use"] == "verbatim" else "faithful",
        "coverage": "complete" if normalized["coverage"] == "complete" else "near_complete",
        "compression": "low",
        "depth": "deep",
        "page_budget_policy": "explicit" if re.search(r"\d{1,3}\s*(?:页|頁|张|張|pages?|slides?)", topic or "", flags=re.IGNORECASE) else "source_capacity",
        "structure_policy": "preserve_order" if normalized["structure_policy"] == "preserve_order" else "source_order",
        "delivery_intent": normalized.get("delivery_intent") or "保留源材料结构、关键表达和讲述顺序，并转成适合演讲使用的 PPT 内容。",
        "requires_clarification": False,
        "confidence": max(float(normalized.get("confidence") or 0), 0.86),
        "rationale": "用户明确要求保留原文表达，页数变化只通过拆分或合并源材料实现。",
        "evidence": [item for idx, item in enumerate(evidence) if item and item not in evidence[:idx]][:12],
    })
    return normalize_content_director_contract(normalized)


def _document_preservation_mode(documents: str, topic: str = "") -> str:
    """Choose how aggressively content planning may transform uploaded text."""
    if not documents or not documents.strip():
        return "none"
    if _is_brief_only_source_context(documents):
        return "none"
    if detect_ppt_sources(documents):
        return "ppt_source"
    if _topic_requests_source_preservation(topic):
        return "faithful" if len(documents) <= 14_000 else "structured_extract"
    if _is_general_transform_request(topic):
        return "transform"
    if len(documents) <= 14_000:
        return "faithful"
    if len(documents) <= 40_000:
        return "structured_extract"
    return "synthesis"


def _is_brief_only_source_context(documents: str) -> bool:
    source_kinds = [match.group(1) for match in re.finditer(r'kind="([^"]+)"', documents or "")]
    return bool(source_kinds and set(source_kinds) <= {"brief"})


def _director_contract_from_legacy_intent(legacy_contract: dict) -> dict:
    legacy = normalize_legacy_intent_contract(legacy_contract)
    task_type = legacy["task_type"]
    contract = {
        "task_type": "source_to_ppt",
        "source_use": legacy["source_fidelity"],
        "coverage": "balanced",
        "compression": "medium",
        "depth": "standard",
        "page_budget_policy": "auto",
        "structure_policy": "source_order",
        "requires_clarification": legacy["confidence"] < 0.5,
        "confidence": legacy["confidence"],
        "rationale": "由旧版 PPT 来源意图识别兼容转换。",
        "evidence": legacy.get("evidence", []),
    }
    if task_type == "replicate":
        contract.update({
            "task_type": "direct_replicate",
            "source_use": "verbatim",
            "coverage": "complete",
            "compression": "low",
            "page_budget_policy": "same_as_source",
            "structure_policy": "preserve_order",
        })
    elif task_type == "polish":
        contract.update({
            "task_type": "polish_existing",
            "source_use": "faithful",
            "coverage": "near_complete",
            "compression": "low",
            "page_budget_policy": "same_as_source" if legacy["page_count_policy"] == "same" else "auto",
            "structure_policy": "preserve_order" if legacy["page_order_policy"] == "preserve" else "source_order",
        })
    elif task_type == "merge":
        contract["task_type"] = "merge_sources"
    elif task_type == "extract":
        contract["task_type"] = "extract_only"
    return normalize_content_director_contract(contract)


def _legacy_intent_for_policy(intent_contract: dict | None) -> dict:
    if is_content_director_contract(intent_contract):
        return content_director_contract_to_legacy_intent(intent_contract)
    return normalize_legacy_intent_contract(intent_contract)


def _content_director_source_diagnostics(documents: str) -> dict:
    text = sanitize_ppt_recovery_text_for_content(documents)
    units = extract_document_outline_units(text)
    return {
        "char_count": len(text),
        "line_count": len([line for line in text.splitlines() if line.strip()]),
        "heading_count": len([unit for unit in units if int(unit.get("level") or 9) <= 4]),
        "estimated_page_capacity": _estimate_document_page_capacity(text),
        **source_diagnostics_from_documents(text),
    }


def _has_single_page_source_shape(documents: str) -> bool:
    return bool(detect_ppt_sources(documents) or _single_source_context_page_source(documents))


def _legacy_contract_has_evidence(legacy_contract: dict | None) -> bool:
    if not isinstance(legacy_contract, dict):
        return False
    evidence = legacy_contract.get("evidence")
    return bool(
        isinstance(evidence, list)
        and any(str(item or "").strip() for item in evidence)
    )


def _legacy_contract_is_high_confidence_source_guard(
    legacy_contract: dict | None,
    documents: str,
) -> bool:
    legacy = normalize_legacy_intent_contract(legacy_contract)
    if float(legacy.get("confidence") or 0) < 0.84 or not _legacy_contract_has_evidence(legacy_contract):
        return False
    if legacy.get("task_type") in {"replicate", "template_reference"}:
        return True
    if not _has_single_page_source_shape(documents):
        return False
    return bool(
        legacy.get("page_count_policy") == "same"
        and legacy.get("page_order_policy") == "preserve"
        and legacy.get("source_fidelity") in {"verbatim", "faithful"}
    )


def _effective_intent_contract(topic: str, documents: str, intent_contract: dict | None = None) -> dict:
    legacy_director_contract = None
    has_page_source = _has_single_page_source_shape(documents)
    if intent_contract is not None:
        if is_content_director_contract(intent_contract):
            contract = normalize_content_director_contract(intent_contract)
            if documents.strip() and _topic_requests_source_preservation(topic):
                return _strengthen_source_preserve_director_contract(contract, topic)
            return contract
        else:
            legacy_contract = normalize_legacy_intent_contract(intent_contract)
            legacy_director_contract = _director_contract_from_legacy_intent(legacy_contract)
            if _legacy_contract_is_high_confidence_source_guard(legacy_contract, documents):
                contract = legacy_director_contract
                if documents.strip() and _topic_requests_source_preservation(topic):
                    return _strengthen_source_preserve_director_contract(contract, topic)
                return contract

    legacy_contract = None
    if intent_contract is None and has_page_source:
        legacy_contract = infer_legacy_intent_contract(
            topic,
            source_diagnostics=source_diagnostics_from_documents(documents),
        )
        if _legacy_contract_is_high_confidence_source_guard(legacy_contract, documents):
            return _director_contract_from_legacy_intent(legacy_contract)
        legacy_director_contract = _director_contract_from_legacy_intent(legacy_contract)

    director_contract = infer_content_director_contract(
        brief=topic,
        documents=documents,
        source_diagnostics=_content_director_source_diagnostics(documents),
    )
    if documents.strip() and _topic_requests_source_preservation(topic):
        return _strengthen_source_preserve_director_contract(director_contract, topic)
    if legacy_director_contract and float(director_contract.get("confidence") or 0) < 0.65:
        if has_page_source and float(legacy_director_contract.get("confidence") or 0) >= float(director_contract.get("confidence") or 0):
            return legacy_director_contract
    return director_contract


def infer_effective_content_intent_contract(
    topic: str,
    documents: str,
    intent_contract: dict | None = None,
) -> dict:
    return _effective_intent_contract(
        topic,
        sanitize_ppt_recovery_text_for_content(documents),
        intent_contract,
    )


def _planning_policy(topic: str, documents: str, intent_contract: dict | None = None) -> dict:
    return contract_to_planning_policy(_legacy_intent_for_policy(_effective_intent_contract(topic, documents, intent_contract)))


def _planning_policy_for_explicit_contract(
    topic: str,
    documents: str,
    intent_contract: dict | None = None,
) -> dict:
    if intent_contract is not None and not is_content_director_contract(intent_contract):
        return contract_to_planning_policy(normalize_legacy_intent_contract(intent_contract))
    return _planning_policy(topic, documents, intent_contract)


def _is_source_preserve_contract(intent_contract: dict | None) -> bool:
    if not is_content_director_contract(intent_contract):
        return False
    contract = normalize_content_director_contract(intent_contract)
    has_explicit_budget = contract["page_budget_policy"] in {"explicit", "same_as_source", "source_capacity"}
    has_confidence = float(contract.get("confidence") or 0) >= 0.8
    has_evidence = bool(contract.get("evidence"))
    return bool(
        contract["source_use"] in {"verbatim", "faithful"}
        and contract["coverage"] in {"near_complete", "complete"}
        and contract["compression"] == "low"
        and contract["structure_policy"] in {"preserve_order", "source_order"}
        and has_explicit_budget
        and (has_confidence or has_evidence)
    )


def _is_source_preserve_job(job: ContentPlanJob) -> bool:
    return bool(job.has_docs and _is_source_preserve_contract(job.intent_contract))


def _intent_contract_policy_text(intent_contract: dict | None) -> str:
    """
    Prompt 注入的 PPT 创作指导（纯输出视角，无过程语言）。

    只描述产出应有的形态，不教 LLM "怎么想"。
    """
    contract = normalize_content_director_contract(intent_contract)
    delivery_intent = str(contract.get("delivery_intent") or "").strip()
    text = (
        "【产出目标】\n"
        "PPT 应当准确体现用户的真实意图和诉求，让用户的演讲更有力。\n"
        "如果用户意图不清晰，应当主动澄清而不是猜测输出。\n"
        "\n"
        "【好 PPT 的特征】\n"
        "- 言之有物（具体案例/数据/反例，不是抽象概括）\n"
        "- 节奏感（多形式交替，不要每页同一种结构）\n"
        "- 适配演讲场景（不是阅读型材料）\n"
        "\n"
        "【避免的输出形态】\n"
        "- 连续多页都是 “label: content” 的小标题加冒号格式（如“现状：xxx / 原因：xxx / 工具：xxx”）。\n"
        "- 把源材料的“按主题分类”结构原样搬过来当 PPT 结构。\n"
        "- 每页形式一样（全 bullet / 全分类标签 / 全段落），没有节奏变化。\n"
        "\n"
        "【硬要求】\n"
        "- 标题必须承载具体判断、问题或原文概念；不要“续2”“续3”或“背景介绍”这类机械标题。\n"
        "- 演讲者备注必须先写出这一页需要讲什么：关键事实、原文细节、案例、数据、解释和结论；讲法、停顿和转场只能作为补充。\n"
    )
    if delivery_intent:
        text += "\n【交付理解】\n" + f"- {delivery_intent}\n"
    return text


def _document_preservation_policy(documents: str, topic: str = "", intent_contract: dict | None = None) -> str:
    mode = _document_preservation_mode(documents, topic)
    director_policy = _intent_contract_policy_text(_effective_intent_contract(topic, documents, intent_contract))
    if mode == "ppt_source":
        return director_policy
    if mode == "faithful":
        return director_policy + (
            "【原文保真模式】\n"
            "- 用户材料篇幅可控，默认目标是整理成 PPT，而不是重写。\n"
            "- 尽量保留原文的关键句、术语、数据、案例和表达顺序；标题可优化，但正文不要大幅改写。\n"
            "- 只有为分段、去重、纠正明显病句或适配版面时才做轻微编辑。\n"
            "- 不得新增用户材料里没有的互动、问答、练习、复盘、金句合集、带走表或课程包装页；页数增加只能通过拆分原文材料实现。\n"
            "- 如果材料来自 PPT 解析，自动识别并过滤版式模板占位文字和布局结构标注，只保留真实内容。\n"
        )
    if mode == "structured_extract":
        return director_policy + (
            "【结构化摘取模式】\n"
            "- 材料较长，但仍应优先摘取原文中的关键段落和数据，不要重新发明叙事。\n"
            "- 每页正文使用原文要点的压缩版；删减只针对重复、旁枝和低信息密度内容。\n"
            "- 不得新增用户材料里没有的互动、问答、练习、复盘、金句合集、带走表或课程包装页。\n"
            "- 如果材料来自 PPT 解析，自动识别并过滤版式模板占位文字和布局结构标注，只保留真实内容。\n"
        )
    if mode == "synthesis":
        return director_policy + (
            "【综合提炼模式】\n"
            "- 材料过长，允许按 PPT 方法论提炼主线。\n"
            "- 必须保留核心论点、专有名词、关键数字、引用和结论，避免改写用户立场。\n"
            "- 如果材料来自 PPT 解析，自动识别并过滤版式模板占位文字和布局结构标注，只保留真实内容。\n"
        )
    if mode == "transform":
        return director_policy + (
            "【用户要求改写/提炼】\n"
            "- 可按用户目标重组材料，但不得改变事实、立场和关键术语。\n"
            "- 如果材料来自 PPT 解析，自动识别并过滤版式模板占位文字和布局结构标注，只保留真实内容。\n"
        )
    return ""


_PPT_RECOVERED_OCR_BLOCK_RE = re.compile(
    r"(?m)(?P<header>^【截图识别文字】\s*\n?)(?P<body>.*?)(?=^\s*---\s*第\s*\d+\s*页\s*---\s*$|^\s*【备注】\s*$|\Z)",
    flags=re.DOTALL | re.MULTILINE,
)
_PPT_RECOVERY_METADATA_LINE_RE = re.compile(r"(?m)^\s*【(?:页面意图|识别置信度)】.*$")
_PPT_RECOVERY_KEY_FACTS_BLOCK_RE = re.compile(
    r"(?m)^\s*【关键事实】\s*$\n(?:^\s*[-*+]\s+.*$\n?)*"
)
_PPT_SOURCE_TEXT_MARKERS = {
    "【截图识别文字】",
    "【备注】",
}
_SOURCE_CONTEXT_MARKER_LINE_RE = re.compile(
    r'^\s*(?:---\s*(?:SOURCE|PAGE|CHAPTER|AVAILABLE_FIGURES)\b.*?---|FIGURE\s+figure_id\b.*)\s*$',
    flags=re.IGNORECASE,
)
_SOURCE_CONTEXT_INLINE_MARKER_RE = re.compile(
    r'---\s*(?:SOURCE|PAGE|CHAPTER|AVAILABLE_FIGURES)\b.*?---',
    flags=re.IGNORECASE,
)
_SOURCE_CONTEXT_FIGURE_INLINE_RE = re.compile(r'\bFIGURE\s+figure_id\b.*$', flags=re.IGNORECASE)
_SOURCE_CONTEXT_SOURCE_FILENAME_RE = re.compile(r'^\s*---\s*SOURCE\s+filename="([^"]+)"[^-]*---\s*$', flags=re.IGNORECASE)


def sanitize_ppt_recovery_text_for_content(documents: str) -> str:
    """Remove vision-analysis scaffolding from PPT OCR recovery text.

    Old extracted PPT files may contain the full image-analysis response under
    "截图识别文字". Content planning must consume only recovered page text.
    """
    text = str(documents or "")
    if not text:
        return ""

    def replace_ocr_block(match: re.Match) -> str:
        body = str(match.group("body") or "")
        ocr_text = extract_ocr_text_from_unstructured_description(body).strip()
        if not ocr_text:
            return "【截图识别文字】\n"
        return "【截图识别文字】\n" + ocr_text + "\n"

    if "【截图识别文字】" in text:
        text = _PPT_RECOVERED_OCR_BLOCK_RE.sub(replace_ocr_block, text)
    text = _PPT_RECOVERY_KEY_FACTS_BLOCK_RE.sub("", text)
    text = _PPT_RECOVERY_METADATA_LINE_RE.sub("", text)
    return re.sub(r"\n{4,}", "\n\n\n", text).strip()


_SOURCE_LINE_SENTENCE_END_RE = re.compile(r"[。！？!?；;：:]$")
_SOURCE_LINE_LIST_START_RE = re.compile(
    r"^(?:[•·*▪▫◦]\s*|[-–—]\s+|\d+[.)、．]\s*|[一二三四五六七八九十]+[、.)．]\s*)"
)
_SOURCE_LINE_CONTINUATION_PUNCT_RE = re.compile(r"^[，,、；;：:）)\]}】]")
_SOURCE_LINE_SPLIT_TOKEN_RE = re.compile(r"^[A-Za-z\u4e00-\u9fff][、，,；;：:]")


def _source_line_is_wrapped_continuation(previous: str, current: str) -> bool:
    previous = str(previous or "").rstrip()
    current = str(current or "").lstrip()
    if not previous or not current:
        return False
    if _SOURCE_LINE_SENTENCE_END_RE.search(previous):
        return False
    if _SOURCE_LINE_LIST_START_RE.match(current):
        return False
    if previous.endswith("-"):
        return True
    return bool(
        _SOURCE_LINE_CONTINUATION_PUNCT_RE.match(current)
        or _SOURCE_LINE_SPLIT_TOKEN_RE.match(current)
    )


def _join_wrapped_source_line(previous: str, current: str) -> str:
    previous = str(previous or "").rstrip()
    current = str(current or "").lstrip()
    if previous.endswith("-"):
        return previous[:-1] + current
    if re.search(r"[\u4e00-\u9fff]$", previous) or re.match(r"^[\u4e00-\u9fff，,、；;：:]", current):
        return previous + current
    return previous + " " + current


_LEADING_NUMERIC_LIST_MARKER_RE = re.compile(r"^\s*(\d+)([.)、．])(\s*)(.*)$")


def _strip_leading_list_marker(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"^\s*[-*+]\s*", "", text).strip()
    match = _LEADING_NUMERIC_LIST_MARKER_RE.match(text)
    if not match:
        return text
    _number, delimiter, spacing, rest = match.groups()
    # Preserve decimals and timeline dates such as 2023.8 or 3.14.
    if delimiter in {".", "．"} and not spacing and rest[:1].isdigit():
        return text
    return rest.strip() if rest else text


def _clean_ppt_source_page_lines(lines: list[str]) -> list[str]:
    cleaned: list[str] = []
    for line in lines:
        value = str(line or "").strip()
        if not value or value in _PPT_SOURCE_TEXT_MARKERS:
            continue
        if value.startswith("【页面意图】") or value.startswith("【识别置信度】"):
            continue
        if cleaned and _source_line_is_wrapped_continuation(cleaned[-1], value):
            cleaned[-1] = _join_wrapped_source_line(cleaned[-1], value)
            continue
        cleaned.append(value)
    return cleaned


def _parse_ppt_source_pages(documents: str) -> list[dict]:
    """Parse the lightweight PPT text markers emitted by document_parser."""
    text = sanitize_ppt_recovery_text_for_content(documents)
    source_match = re.search(
        r'---\s*PPT_SOURCE(?:\s+filename="([^"]*)")?\s+pages=(\d+)\s*---',
        text,
    )
    if not source_match:
        return []

    filename = source_match.group(1) or ""
    try:
        expected_pages = int(source_match.group(2) or 0)
    except ValueError:
        expected_pages = 0

    source_body = text[source_match.end():]
    next_source = re.search(r'---\s*PPT_SOURCE(?:\s+filename="[^"]*")?\s+pages=\d+\s*---', source_body)
    if next_source:
        source_body = source_body[: next_source.start()]

    page_headers = list(re.finditer(r"(?m)^---\s*第\s*(\d+)\s*页\s*---\s*$", source_body))
    pages: list[dict] = []
    for idx, match in enumerate(page_headers):
        try:
            page_num = int(match.group(1))
        except ValueError:
            continue
        start = match.end()
        end = page_headers[idx + 1].start() if idx + 1 < len(page_headers) else len(source_body)
        raw = source_body[start:end].strip()
        notes = ""
        slide_text = raw
        if "【备注】" in raw:
            slide_text, notes = raw.split("【备注】", 1)
        lines = _clean_ppt_source_page_lines([
            line.strip() for line in slide_text.splitlines() if line.strip()
        ])
        notes = notes.strip()
        pages.append({
            "page_num": page_num,
            "source_document": filename,
            "source_pages": expected_pages,
            "lines": lines,
            "notes": notes,
            "raw_text": slide_text.strip(),
        })

    pages.sort(key=lambda page: int(page.get("page_num") or 0))
    return pages


PAGE_SOURCE_KINDS = {"pdf", "pptx", "ppt"}


def _canonical_page_source_kind(kind: str) -> str:
    value = str(kind or "").strip().lower()
    if value in {"application/pdf"}:
        return "pdf"
    if value in {"application/vnd.openxmlformats-officedocument.presentationml.presentation", "powerpoint"}:
        return "pptx"
    return value


def _outline_source_type(kind: str) -> str:
    canonical = _canonical_page_source_kind(kind)
    if canonical in {"pptx", "ppt"}:
        return "pptx_slide"
    return canonical or "pdf"


def _source_context_page_sources(documents: str) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for page in _extract_source_context_pages(documents):
        source_document = str(page.get("source_document") or "").strip()
        source_type = _canonical_page_source_kind(str(page.get("source_type") or ""))
        if not source_document or source_type not in PAGE_SOURCE_KINDS:
            continue
        try:
            page_num = int(page.get("page_num") or 0)
        except (TypeError, ValueError):
            page_num = 0
        if page_num <= 0:
            continue
        grouped.setdefault((source_document, source_type), []).append(page)

    sources: list[dict] = []
    for (source_document, source_type), pages in grouped.items():
        page_by_num: dict[int, dict] = {}
        for page in pages:
            page_num = int(page.get("page_num") or 0)
            page_by_num.setdefault(page_num, page)
        ordered_pages = [page_by_num[page_num] for page_num in sorted(page_by_num)]
        sources.append({
            "filename": source_document,
            "kind": source_type,
            "pages": ordered_pages,
            "page_count": len(ordered_pages),
        })
    return sorted(sources, key=lambda item: str(item.get("filename") or ""))


def _single_source_context_page_source(documents: str) -> dict | None:
    sources = _source_context_page_sources(documents)
    return sources[0] if len(sources) == 1 else None


def _figure_refs_for_source_page(
    documents: str,
    source_document: str,
    source_type: str,
    page_num: int,
    reason: str,
    page_text: str = "",
) -> list[dict]:
    by_page = _source_context_figures_by_page(documents)
    figures = by_page.get((source_document, page_num), [])
    if not figures:
        return []
    page_for_scoring = {
        "section_title": "",
        "headline": page_text,
        "subhead": "",
        "bullets": [],
        "speaker_notes": "",
        "visual_suggestion": "",
    }
    candidates: list[tuple[float, dict]] = []
    for figure in figures:
        figure_id = str(figure.get("figure_id") or "").strip()
        if not figure_id or _figure_is_source_page_reference(figure):
            continue
        if not _figure_is_content_bearing(figure):
            continue
        candidates.append((_figure_selection_score(page_for_scoring, figure), {
            "source_document": str(figure.get("source_document") or source_document).strip(),
            "source_page_num": page_num,
            "source_type": _outline_source_type(str(figure.get("source_type") or source_type)),
            "figure_id": figure_id,
            "reason": reason,
        }))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [ref for _score, ref in candidates[:8]]


def _source_context_page_source_count(documents: str) -> int | None:
    source = _single_source_context_page_source(documents)
    if not source:
        return None
    page_count = int(source.get("page_count") or 0)
    return page_count if page_count > 0 else None


def _ppt_page_is_cover_like(page_num: int, lines: list[str]) -> bool:
    if page_num != 1:
        return False
    clean_lines = [str(line).strip() for line in lines if str(line).strip()]
    if not clean_lines:
        return True
    total_chars = sum(len(line) for line in clean_lines)
    if len(clean_lines) >= 4:
        return False
    if total_chars > 160:
        return False
    if len(clean_lines) == 1:
        return True
    if len(clean_lines) == 2:
        return total_chars <= 120
    return total_chars <= 120


def _ppt_page_is_ending_like(page_num: int, total_pages: int, lines: list[str]) -> bool:
    if total_pages <= 1 or page_num != total_pages:
        return False
    clean_lines = [str(line).strip() for line in lines if str(line).strip()]
    if not clean_lines:
        return True
    total_chars = sum(len(line) for line in clean_lines)
    compact = re.sub(r"\s+", "", "\n".join(clean_lines)).lower()
    ending_terms = (
        "谢谢",
        "感谢",
        "thankyou",
        "thanks",
        "q&a",
        "qa",
        "theend",
        "联系我们",
        "联系方式",
    )
    if not any(term in compact for term in ending_terms):
        return False
    signal_re = re.compile(
        r"(谢谢|感谢|thank\s*you|thanks|q\s*&\s*a|the\s*end|联系我们|联系方式|扫码|二维码|"
        r"电话|邮箱|邮件|email|wechat|微信|联系人|https?://|www\.|@)",
        flags=re.IGNORECASE,
    )
    content_lines = [line for line in clean_lines if not signal_re.search(line)]
    content_chars = sum(len(line) for line in content_lines)
    if not content_lines and len(clean_lines) <= 4 and total_chars <= 160:
        return True
    if len(clean_lines) <= 2 and total_chars <= 100 and content_chars <= 32:
        return True
    if len(clean_lines) <= 3 and total_chars <= 120 and content_chars <= 24:
        return True
    return False


def _ppt_page_type(page_num: int, total_pages: int, lines: list[str]) -> str:
    if _ppt_page_is_cover_like(page_num, lines):
        return "cover"
    if _ppt_page_is_ending_like(page_num, total_pages, lines):
        return "ending"
    if len(lines) <= 2 and sum(len(line) for line in lines) <= 80:
        return "section"
    return "content"


_SOURCE_TIMELINE_DATE_RE = re.compile(r"\b(20\d{2})[./．](\d{1,2})\b")
_SOURCE_TIMELINE_CONTEXT_RE = re.compile(
    r"(发展规划|路线图|时间轴|里程碑|阶段规划|roadmap|timeline|milestone)",
    flags=re.IGNORECASE,
)
_SOURCE_TIMELINE_METRIC_RE = re.compile(
    r"(?:MAU|MRR|ARR|GMV|DAU|营收|收入|用户|付费|金额|美金|美元|万元|万|亿|\$)",
    flags=re.IGNORECASE,
)
_SOURCE_TIMELINE_STAGE_RE = re.compile(
    r"(?:版本|开发|测试|公测|上线|发布|商业化|启动|完成|阶段|alpha|beta|release)",
    flags=re.IGNORECASE,
)


def _timeline_date_key(value: str) -> tuple[int, int] | None:
    match = _SOURCE_TIMELINE_DATE_RE.search(str(value or ""))
    if not match:
        return None
    try:
        return int(match.group(1)), int(match.group(2))
    except ValueError:
        return None


def _format_timeline_date(key: tuple[int, int]) -> str:
    return f"{key[0]}.{key[1]}"


def _strip_timeline_dates(value: str) -> str:
    return re.sub(_SOURCE_TIMELINE_DATE_RE, "", str(value or "")).strip(" \t-–—:：,，、")


def _timeline_metric_sort_value(value: str) -> float | None:
    text = str(value or "")
    matches = re.findall(r"(\d+(?:\.\d+)?)\s*(亿|万)?", text)
    if not matches:
        return None
    best = 0.0
    for raw_number, unit in matches:
        try:
            number = float(raw_number)
        except ValueError:
            continue
        if unit == "亿":
            number *= 10000
        best = max(best, number)
    return best or None


def _normalize_timeline_source_lines(lines: list[str]) -> list[str]:
    clean_lines = [str(line or "").strip() for line in lines if str(line or "").strip()]
    if len(clean_lines) < 5:
        return lines

    context_text = "\n".join(clean_lines[:4])
    if not _SOURCE_TIMELINE_CONTEXT_RE.search(context_text):
        return lines

    date_keys: list[tuple[int, int]] = []
    body_start = 2 if len(clean_lines) >= 3 else 1
    if not any(_SOURCE_TIMELINE_CONTEXT_RE.search(line) for line in clean_lines[:body_start]):
        body_start = 1

    stage_lines: list[str] = []
    metric_lines: list[str] = []
    leftover_lines: list[str] = []
    for line in clean_lines[body_start:]:
        line_has_date = bool(_SOURCE_TIMELINE_DATE_RE.search(line))
        for match in _SOURCE_TIMELINE_DATE_RE.finditer(line):
            try:
                date_keys.append((int(match.group(1)), int(match.group(2))))
            except ValueError:
                continue
        text_without_dates = _strip_timeline_dates(line) if line_has_date else line
        if not text_without_dates:
            continue
        if _SOURCE_TIMELINE_METRIC_RE.search(text_without_dates):
            metric_lines.append(text_without_dates)
        elif _SOURCE_TIMELINE_STAGE_RE.search(text_without_dates):
            stage_lines.append(text_without_dates)
        else:
            leftover_lines.append(text_without_dates)

    unique_dates = sorted(set(date_keys))
    if len(unique_dates) < 3 or len(stage_lines) + len(metric_lines) < 2:
        return lines

    ordered_metrics = list(metric_lines)
    metric_values = [_timeline_metric_sort_value(line) for line in ordered_metrics]
    if len(ordered_metrics) >= 2 and all(value is not None for value in metric_values):
        ordered_metrics = [
            line
            for _value, line in sorted(
                zip(metric_values, ordered_metrics),
                key=lambda item: float(item[0] or 0),
            )
        ]

    normalized_body: list[str] = []
    cursor = 0
    for stage in stage_lines:
        if cursor >= len(unique_dates):
            break
        normalized_body.append(f"{_format_timeline_date(unique_dates[cursor])} {stage}")
        cursor += 1
    for metric in ordered_metrics:
        if cursor >= len(unique_dates):
            break
        normalized_body.append(f"{_format_timeline_date(unique_dates[cursor])} {metric}")
        cursor += 1

    used_values = set(stage_lines) | set(metric_lines)
    normalized_body.extend(line for line in leftover_lines if line not in used_values)
    if cursor < len(unique_dates):
        normalized_body.extend(_format_timeline_date(key) for key in unique_dates[cursor:])

    if len(normalized_body) < 3:
        return lines
    return clean_lines[:body_start] + normalized_body


def _ppt_page_text_content(page_num: int, lines: list[str]) -> dict[str, str]:
    lines = _normalize_timeline_source_lines(lines)

    if not lines:
        return {
            "headline": f"原 PPT 第{page_num}页",
            "subhead": "",
            "body": "",
        }

    if len(lines) == 1:
        return {
            "headline": lines[0],
            "subhead": "",
            "body": "",
        }

    if len(lines) == 2:
        subtitle_like = (
            page_num == 1
            or len(lines[1]) > 18
            or bool(re.search(r"(负责人|主讲|讲师|教授|博士|创始人|CEO|COO|CTO|/)", lines[1], flags=re.IGNORECASE))
        )
        return {
            "headline": lines[0] if subtitle_like else "\n".join(lines),
            "subhead": lines[1] if subtitle_like else "",
            "body": "",
        }

    return {
        "headline": lines[0],
        "subhead": lines[1] if len(lines[1]) <= 48 else "",
        "body": "\n".join(lines[2:] if len(lines[1]) <= 48 else lines[1:]),
    }


DIRECT_PPT_REPLICATE_FORBIDDEN_MARKERS = (
    "PPT_SOURCE",
    "PPTSOURCE",
    "用户上传材料",
)


def validate_direct_ppt_replicate_outline(
    outline: list[dict],
    *,
    expected_pages: int | None = None,
    source_document: str = "",
) -> dict:
    """Quality gate for the deterministic PPT replicate path."""
    expected = int(expected_pages or 0)
    rendered_pages = []
    missing_refs: list[int] = []
    marker_pages: list[int] = []
    for idx, page in enumerate(outline, start=1):
        page_num = int(page.get("page_num") or idx)
        text_content = page.get("text_content") if isinstance(page.get("text_content"), dict) else {}
        rendered = "\n".join(
            str(text_content.get(key) or "")
            for key in ("headline", "subhead", "body")
        )
        if any(marker in rendered for marker in DIRECT_PPT_REPLICATE_FORBIDDEN_MARKERS):
            marker_pages.append(page_num)
        source_refs = page.get("source_refs") if isinstance(page.get("source_refs"), list) else []
        has_source_ref = any(
            isinstance(ref, dict)
            and int(ref.get("source_page_num") or 0) == page_num
            and (not source_document or str(ref.get("source_document") or "") == source_document)
            for ref in source_refs
        )
        if not has_source_ref:
            missing_refs.append(page_num)
        rendered_pages.append(page_num)

    duplicate_pages = sorted({page for page in rendered_pages if rendered_pages.count(page) > 1})
    missing_pages = [
        page_num
        for page_num in range(1, expected + 1)
        if expected and page_num not in rendered_pages
    ]
    checks = {
        "page_count_match": expected <= 0 or len(outline) == expected,
        "page_numbers_contiguous": rendered_pages == list(range(1, len(outline) + 1)),
        "source_refs_complete": not missing_refs,
        "marker_free": not marker_pages,
    }
    passed = all(checks.values()) and not duplicate_pages and not missing_pages
    return {
        "mode": "direct_ppt_replicate",
        "status": "passed" if passed else "needs_review",
        "checks": checks,
        "expected_pages": expected or None,
        "actual_pages": len(outline),
        "missing_source_ref_pages": missing_refs,
        "duplicate_pages": duplicate_pages,
        "missing_pages": missing_pages,
        "marker_pages": marker_pages,
    }


def _is_direct_replicate_request(
    topic: str,
    documents: str,
    intent_contract: dict | None = None,
) -> bool:
    """Detect if the user is requesting a 1:1 PPT replication."""
    sources = detect_ppt_sources(documents)
    page_source = _single_source_context_page_source(documents)
    if len(sources) != 1 and not page_source:
        return False
    policy = _planning_policy_for_explicit_contract(topic, documents, intent_contract)
    return bool(policy.get("allow_direct_ppt_replicate"))


def build_direct_ppt_replicate_outline(
    documents: str,
    topic: str = "",
    intent_contract: dict | None = None,
) -> list[dict]:
    """Build a deterministic 1:1 content plan from a single uploaded PPT."""
    sources = detect_ppt_sources(documents)
    if len(sources) != 1:
        return []
    if not _planning_policy_for_explicit_contract(topic, documents, intent_contract)["allow_direct_ppt_replicate"]:
        return []

    parsed_pages = _parse_ppt_source_pages(documents)
    if not parsed_pages:
        return []

    source = sources[0]
    filename = str(source.get("filename") or parsed_pages[0].get("source_document") or "").strip()
    total_pages = int(source.get("pages") or len(parsed_pages) or 0)
    outline: list[dict] = []
    for idx, page in enumerate(parsed_pages, start=1):
        page_num = int(page.get("page_num") or idx)
        lines = [str(line).strip() for line in (page.get("lines") or []) if str(line).strip()]
        text_content = _ppt_page_text_content(page_num, lines)
        outline.append({
            "page_num": page_num,
            "type": _ppt_page_type(page_num, total_pages or len(parsed_pages), lines),
            "section_title": "原 PPT 逐页复刻",
            "text_content": text_content,
            "speaker_notes": str(page.get("notes") or "").strip(),
            "source_facts": {
                "mode": "direct_ppt_replicate",
                "source_document": filename,
                "source_page_num": page_num,
                "source_total_pages": total_pages or len(parsed_pages),
                "source_line_count": len(lines),
                "has_speaker_notes": bool(str(page.get("notes") or "").strip()),
            },
            "visual_suggestion": (
                f"按原 PPT 第{page_num}页的版式、图片位置、背景和信息层级复刻；"
                "内容规划阶段只做逐页承接，不改写成新叙事。"
            ),
            "source_refs": [{
                "source_document": filename,
                "source_page_num": page_num,
                "source_type": "pptx_slide",
                "reason": "direct_replicate",
            }],
            "generation_status": "pptx_direct",
        })

    outline.sort(key=lambda page: int(page.get("page_num") or 0))
    for idx, page in enumerate(outline, start=1):
        page["page_num"] = idx
        if isinstance(page.get("source_facts"), dict):
            page["source_facts"]["source_page_num"] = idx
    outline = _normalize_content_markdown(outline)
    quality = validate_direct_ppt_replicate_outline(
        outline,
        expected_pages=total_pages or len(outline),
        source_document=filename,
    )
    for page in outline:
        page["replicate_quality"] = {
            "mode": quality["mode"],
            "status": quality["status"],
        }
    if outline:
        outline[0]["replicate_quality"] = quality
    return outline


def build_source_context_page_preserve_outline(
    documents: str,
    topic: str = "",
    intent_contract: dict | None = None,
) -> list[dict]:
    """Build a deterministic page-by-page draft from a single PDF/PPT-like source context."""
    documents = sanitize_ppt_recovery_text_for_content(documents)
    source = _single_source_context_page_source(documents)
    if not source:
        return []

    policy = _planning_policy_for_explicit_contract(topic, documents, intent_contract)
    if not (policy["preserve_source_page_order"] and policy["preserve_source_page_count"]):
        return []
    if policy["task_type"] == "template_reference":
        return []

    filename = str(source.get("filename") or "").strip()
    source_kind = _canonical_page_source_kind(str(source.get("kind") or ""))
    source_type = _outline_source_type(source_kind)
    pages = source.get("pages") if isinstance(source.get("pages"), list) else []
    total_pages = int(source.get("page_count") or len(pages) or 0)
    if not filename or not pages:
        return []

    is_direct = bool(policy.get("allow_direct_ppt_replicate"))
    reason = "direct_replicate" if is_direct else "single_ppt_page_polish"
    source_label = "PDF" if source_kind == "pdf" else "PPT"
    section_title = f"原 {source_label} 逐页复刻" if is_direct else f"原 {source_label} 逐页整理"
    mode = f"direct_{source_kind}_replicate" if is_direct else f"single_{source_kind}_page_polish"
    outline: list[dict] = []

    for idx, page in enumerate(pages, start=1):
        try:
            page_num = int(page.get("page_num") or idx)
        except (TypeError, ValueError):
            page_num = idx
        lines = _clean_ppt_source_page_lines([
            str(line).strip()
            for line in str(page.get("text") or "").splitlines()
            if str(line).strip()
        ])
        text_content = _ppt_page_text_content(page_num, lines)
        outline.append({
            "page_num": page_num,
            "type": _ppt_page_type(page_num, total_pages or len(pages), lines),
            "section_title": section_title,
            "text_content": text_content,
            "speaker_notes": "",
            "source_facts": {
                "mode": mode,
                "source_document": filename,
                "source_page_num": page_num,
                "source_total_pages": total_pages or len(pages),
                "source_type": source_type,
                "source_line_count": len(lines),
            },
            "visual_suggestion": (
                f"按原 {source_label} 第{page_num}页的整页原图、图片位置、背景和信息层级复刻；"
                "内容规划阶段只做逐页承接，不改写成新叙事。"
            ),
            "source_refs": [{
                "source_document": filename,
                "source_page_num": page_num,
                "source_type": source_type,
                "reason": reason,
            }],
            "figure_refs": _figure_refs_for_source_page(
                documents,
                filename,
                source_type,
                page_num,
                reason,
                page_text="\n".join(lines),
            ),
            "generation_status": f"{source_kind}_direct" if is_direct else f"{source_kind}_preserve_source",
        })

    outline.sort(key=lambda page: int(page.get("page_num") or 0))
    for idx, page in enumerate(outline, start=1):
        page["page_num"] = idx
        if isinstance(page.get("source_facts"), dict):
            page["source_facts"]["source_page_num"] = idx
    return _normalize_content_markdown(outline)


def build_ppt_page_preserve_source_draft(
    documents: str,
    topic: str = "",
    intent_contract: dict | None = None,
) -> list[dict]:
    """Build a page-by-page source draft for single-PPT polish tasks."""
    documents = sanitize_ppt_recovery_text_for_content(documents)
    sources = detect_ppt_sources(documents)
    if len(sources) != 1:
        return build_source_context_page_preserve_outline(
            documents,
            topic,
            intent_contract=intent_contract,
        )

    policy = _planning_policy_for_explicit_contract(topic, documents, intent_contract)
    if not (policy["preserve_source_page_order"] and policy["preserve_source_page_count"]):
        return []
    if policy["task_type"] == "template_reference":
        return []

    parsed_pages = _parse_ppt_source_pages(documents)
    if not parsed_pages:
        return []

    source = sources[0]
    filename = str(source.get("filename") or parsed_pages[0].get("source_document") or "").strip()
    total_pages = int(source.get("pages") or len(parsed_pages) or 0)
    outline: list[dict] = []
    for idx, page in enumerate(parsed_pages, start=1):
        page_num = int(page.get("page_num") or idx)
        lines = _clean_ppt_source_page_lines([
            str(line).strip() for line in (page.get("lines") or []) if str(line).strip()
        ])
        text_content = _ppt_page_text_content(page_num, lines)
        outline.append({
            "page_num": page_num,
            "type": _ppt_page_type(page_num, total_pages or len(parsed_pages), lines),
            "section_title": "原 PPT 逐页整理",
            "text_content": text_content,
            "speaker_notes": str(page.get("notes") or "").strip(),
            "source_facts": {
                "mode": "single_ppt_page_polish",
                "source_document": filename,
                "source_page_num": page_num,
                "source_total_pages": total_pages or len(parsed_pages),
                "source_line_count": len(lines),
                "has_speaker_notes": bool(str(page.get("notes") or "").strip()),
            },
            "visual_suggestion": (
                f"参考原 PPT 第{page_num}页的截图、背景、图片位置和信息层级；"
                "内容可轻微整理，但页序和原页意图保持一致。"
            ),
            "source_refs": [{
                "source_document": filename,
                "source_page_num": page_num,
                "source_type": "pptx_slide",
                "reason": "single_ppt_page_polish",
            }],
            "generation_status": "pptx_preserve_source",
        })

    outline.sort(key=lambda page: int(page.get("page_num") or 0))
    for idx, page in enumerate(outline, start=1):
        page["page_num"] = idx
        if isinstance(page.get("source_facts"), dict):
            page["source_facts"]["source_page_num"] = idx
    return _normalize_content_markdown(outline)


def infer_page_count_from_single_ppt(
    documents: str,
    topic: str = "",
    intent_contract: dict | None = None,
) -> int | None:
    if not _planning_policy_for_explicit_contract(topic, documents, intent_contract)["preserve_source_page_count"]:
        return None
    sources = detect_ppt_sources(documents)
    if len(sources) != 1:
        return _source_context_page_source_count(documents)
    pages = int(sources[0].get("pages") or 0)
    return pages if pages > 0 else None


def _source_context_documents(documents: str) -> dict[str, str]:
    sources: dict[str, str] = {}
    for match in re.finditer(r'---\s*SOURCE\s+filename="([^"]+)"\s+kind="([^"]+)".*?---', documents or ""):
        sources[match.group(1)] = match.group(2)
    for source in detect_ppt_sources(documents):
        filename = str(source.get("filename") or "").strip()
        if filename:
            sources.setdefault(filename, "pptx")
    return sources


def _source_context_primary_title(documents: str, topic: str = "") -> str:
    source_documents = _source_context_documents(documents)
    for filename in source_documents:
        match = re.search(r"《([^》]{2,60})》", filename)
        if match:
            return _clean_markdown_inline(match.group(1))
        stem = re.sub(r"\.(?:pdf|docx?|pptx?|md|markdown|txt)$", "", filename, flags=re.IGNORECASE)
        stem = re.sub(r"\d{6,}.*$", "", stem).strip(" _-—")
        if 2 <= len(stem) <= 36:
            return _clean_markdown_inline(stem)
    return _brief_title(topic)


def _source_document_from_text(value: str, source_documents: dict[str, str]) -> str:
    text = str(value or "").strip()
    for filename in source_documents:
        if filename and filename in text:
            return filename
    if len(source_documents) == 1:
        return next(iter(source_documents))
    match = re.search(r"([^\s；;，,]+?\.(?:pdf|pptx?|docx?|md|txt))", text, flags=re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _source_page_num_from_text(value: str) -> int:
    page_match = re.search(r"(?:第|P|page\s*)\s*(\d{1,4})\s*(?:页|頁)?", str(value or ""), flags=re.IGNORECASE)
    if not page_match:
        return 0
    try:
        page_num = int(page_match.group(1))
    except (TypeError, ValueError):
        return 0
    return page_num if page_num > 0 else 0


def _source_kind_from_document(
    source_document: str,
    source_documents: dict[str, str] | None = None,
    *,
    default: str = "document",
) -> str:
    known_sources = source_documents or {}
    if source_document in known_sources:
        return known_sources[source_document]
    lower = str(source_document or "").lower()
    if lower.endswith((".pptx", ".ppt")):
        return "pptx"
    if lower.endswith(".pdf"):
        return "pdf"
    if lower.endswith(".docx"):
        return "docx"
    if lower.endswith(".md"):
        return "md"
    if lower.endswith(".txt"):
        return "txt"
    return default


def _parse_source_ref_string(value: str, source_documents: dict[str, str]) -> dict | None:
    text = str(value or "").strip()
    if not text:
        return None
    source_document = _source_document_from_text(text, source_documents)
    if not source_document:
        return None
    page_num = _source_page_num_from_text(text)
    if not page_num:
        return None
    kind = _source_kind_from_document(source_document, source_documents)
    return {
        "source_document": source_document,
        "source_page_num": page_num,
        "source_type": "pptx_slide" if kind == "pptx" else kind,
        "reason": text[:120],
    }


_PLACEHOLDER_FIGURE_IDS = {
    "figureid",
    "figure_id",
    "figure-id",
    "figid",
    "fig_id",
    "fig-id",
    "imageid",
    "image_id",
    "image-id",
    "id",
    "图片id",
    "素材id",
}


def _is_valid_figure_id(value: str) -> bool:
    figure_id = str(value or "").strip().strip('"\'')
    if not figure_id:
        return False
    normalized = re.sub(r"[\s_：:：-]+", "", figure_id.lower())
    if normalized in {re.sub(r"[\s_：:：-]+", "", item.lower()) for item in _PLACEHOLDER_FIGURE_IDS}:
        return False
    if re.fullmatch(r"(figure|fig|image|图片|素材)(id)?", normalized):
        return False
    return True


def _parse_figure_ref_string(value: str, source_documents: dict[str, str] | None = None) -> dict | None:
    text = str(value or "").strip()
    if not text:
        return None
    known_sources = source_documents or {}
    source_document = _source_document_from_text(text, known_sources)
    source_page_num = _source_page_num_from_text(text)
    if not source_document or source_page_num <= 0:
        return None
    figure_match = re.search(r'(?:figure_id|figureid)\s*=\s*["\']([^"\']+)["\']', text, flags=re.IGNORECASE)
    if not figure_match:
        figure_match = re.search(r'(?:figure_id|figureid)\s*=\s*([^"\'\s；;,，]+)', text, flags=re.IGNORECASE)
    if not figure_match:
        figure_match = re.search(r"\b(fig[-_:][A-Za-z0-9.:-]+)\b", text, flags=re.IGNORECASE)
    if not figure_match:
        figure_match = re.search(r"([^\s；;,，]+:p\d{1,4}:x[^\s；;,，]+)", text, flags=re.IGNORECASE)
    figure_id = figure_match.group(1).strip() if figure_match else ""
    if not _is_valid_figure_id(figure_id):
        return None
    source_kind = _source_kind_from_document(source_document, known_sources, default="pdf")
    return {
        "source_document": source_document,
        "source_page_num": source_page_num,
        "source_type": "pptx_slide" if source_kind == "pptx" else source_kind,
        "figure_id": figure_id,
        "reason": text[:120],
    }


def _normalize_figure_refs(value, source_documents: dict[str, str] | None = None) -> list[dict]:
    if not isinstance(value, list):
        return []
    refs: list[dict] = []
    known_sources = source_documents or {}
    for item in value:
        if isinstance(item, str):
            parsed = _parse_figure_ref_string(item, known_sources)
            if parsed:
                refs.append(parsed)
            continue
        if not isinstance(item, dict):
            continue
        source_document = str(item.get("source_document") or item.get("filename") or "").strip()
        try:
            source_page_num = int(item.get("source_page_num") or item.get("page_num") or 0)
        except (TypeError, ValueError):
            source_page_num = 0
        if not source_document or source_page_num <= 0:
            continue
        figure_id = str(item.get("figure_id") or item.get("id") or "").strip()
        if not _is_valid_figure_id(figure_id):
            continue
        source_kind = str(item.get("source_type") or _source_kind_from_document(source_document, known_sources, default="pdf"))
        refs.append({
            "source_document": source_document,
            "source_page_num": source_page_num,
            "source_type": "pptx_slide" if source_kind == "pptx" else source_kind,
            "figure_id": figure_id,
            "reason": str(item.get("reason") or item.get("usage") or item.get("nearby_text") or "").strip(),
        })
    return refs[:8]


_FIGURE_RELEVANCE_STOP_TERMS = {
    "一个",
    "一些",
    "这个",
    "这些",
    "我们",
    "他们",
    "通过",
    "进行",
    "可以",
    "需要",
    "应该",
    "以及",
    "因为",
    "所以",
    "企业",
    "创新",
    "愿景",
    "使命",
    "目标",
    "内容",
    "材料",
    "页面",
    "世界",
}


def _parse_source_context_attrs(line: str) -> dict[str, str]:
    return {
        match.group(1): match.group(2).strip()
        for match in re.finditer(r'([A-Za-z_]+)="([^"]*)"', line or "")
    }


def _source_context_figure_index(source_context: str | None) -> dict[str, dict]:
    figures: dict[str, dict] = {}
    if not source_context:
        return figures
    for line in str(source_context).splitlines():
        text = line.strip()
        if not text.startswith("FIGURE "):
            continue
        attrs = _parse_source_context_attrs(text)
        figure_id = attrs.get("figure_id", "").strip()
        if not _is_valid_figure_id(figure_id):
            continue
        try:
            source_page_num = int(attrs.get("source_page_num") or attrs.get("pdf_source_page_num") or 0)
        except (TypeError, ValueError):
            source_page_num = 0
        if source_page_num <= 0:
            continue
        figures[figure_id] = {
            "figure_id": figure_id,
            "source_document": attrs.get("source_document", "").strip(),
            "source_type": attrs.get("source_type", "").strip(),
            "source_page_num": source_page_num,
            "chapter_id": attrs.get("chapter_id", "").strip(),
            "bbox": attrs.get("bbox", "").strip(),
            "bbox_area": attrs.get("bbox_area", "").strip(),
            "image_width": attrs.get("image_width", "").strip(),
            "image_height": attrs.get("image_height", "").strip(),
            "figure_role": attrs.get("figure_role", "").strip(),
            "content_significance": attrs.get("content_significance", "").strip(),
            "nearby_text": attrs.get("nearby_text", "").strip(),
            "asset_kind": attrs.get("asset_kind", "").strip(),
            "is_full_page_reference": attrs.get("is_full_page_reference", "").strip(),
        }
    return figures


def _extract_source_context_pages(source_context: str | None) -> list[dict]:
    pages: list[dict] = []
    current_source_document = ""
    current_source_type = ""
    current: dict | None = None
    body_lines: list[str] = []

    def flush() -> None:
        nonlocal current, body_lines
        if not current:
            body_lines = []
            return
        text = "\n".join(body_lines).strip()
        if text:
            current["text"] = text
            pages.append(current)
        current = None
        body_lines = []

    for raw_line in str(source_context or "").splitlines():
        line = raw_line.strip()
        source_match = re.match(r'---\s*SOURCE\s+.*?---\s*$', line, flags=re.IGNORECASE)
        if source_match:
            flush()
            attrs = _parse_source_context_attrs(line)
            current_source_document = attrs.get("filename", "").strip()
            current_source_type = attrs.get("kind", "").strip()
            continue
        if _is_source_context_marker_line(line) and "PAGE" not in line.upper():
            flush()
            continue
        page_match = re.match(r'---\s*PAGE\s+(\d{1,4})(.*?)---\s*$', line, flags=re.IGNORECASE)
        if page_match:
            flush()
            attrs = _parse_source_context_attrs(line)
            current = {
                "source_document": current_source_document,
                "source_type": current_source_type,
                "page_num": int(page_match.group(1)),
                "chapter": attrs.get("chapter", "").strip(),
                "text": "",
            }
            continue
        if line.startswith("FIGURE "):
            flush()
            continue
        if current is not None:
            body_lines.append(raw_line)
    flush()
    return pages


def _source_context_figures_by_page(source_context: str | None) -> dict[tuple[str, int], list[dict]]:
    by_page: dict[tuple[str, int], list[dict]] = {}
    for figure in _source_context_figure_index(source_context).values():
        source_document = str(figure.get("source_document") or "").strip()
        try:
            source_page_num = int(figure.get("source_page_num") or 0)
        except (TypeError, ValueError):
            source_page_num = 0
        if not source_document or source_page_num <= 0:
            continue
        by_page.setdefault((source_document, source_page_num), []).append(figure)
    return by_page


def _source_doc_basename(value: str) -> str:
    return str(value or "").replace("\\", "/").rsplit("/", 1)[-1].strip()


def _source_doc_matches(left: str, right: str) -> bool:
    if str(left or "").strip() == str(right or "").strip():
        return True
    return bool(left and right and _source_doc_basename(left) == _source_doc_basename(right))


def _relevance_terms(text: str) -> set[str]:
    raw = str(text or "")
    terms = {
        term
        for term in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", raw.lower())
        if term not in _FIGURE_RELEVANCE_STOP_TERMS
    }
    for seq in re.findall(r"[\u4e00-\u9fff]{2,}", raw):
        for size in (2, 3, 4):
            if len(seq) < size:
                continue
            for idx in range(0, len(seq) - size + 1):
                term = seq[idx:idx + size]
                if term not in _FIGURE_RELEVANCE_STOP_TERMS:
                    terms.add(term)
    return terms


def _page_map_text_for_relevance(page: dict) -> str:
    bullets = page.get("bullets") if isinstance(page.get("bullets"), list) else []
    parts = [
        page.get("section_title"),
        page.get("headline"),
        page.get("subhead"),
        *bullets,
        page.get("speaker_notes"),
        page.get("visual_suggestion"),
    ]
    return "\n".join(str(part or "") for part in parts if str(part or "").strip())


def _source_ref_pages_for_doc(source_refs: list, source_document: str) -> set[int]:
    pages: set[int] = set()
    for ref in source_refs:
        if not isinstance(ref, dict):
            continue
        if not _source_doc_matches(str(ref.get("source_document") or ""), source_document):
            continue
        try:
            page_num = int(ref.get("source_page_num") or 0)
        except (TypeError, ValueError):
            page_num = 0
        if page_num > 0:
            pages.add(page_num)
    return pages


def _figure_relevance_allows(page: dict, source_refs: list, figure: dict) -> bool:
    source_type = str(figure.get("source_type") or "").strip().lower()
    if source_type not in {"pdf", "application/pdf"}:
        return True
    figure_doc = str(figure.get("source_document") or "").strip()
    figure_page = int(figure.get("source_page_num") or 0)
    source_pages = _source_ref_pages_for_doc(source_refs, figure_doc)
    if source_pages and any(abs(figure_page - page_num) <= 1 for page_num in source_pages):
        return True
    page_terms = _relevance_terms(_page_map_text_for_relevance(page))
    figure_terms = _relevance_terms(str(figure.get("nearby_text") or ""))
    if not page_terms or not figure_terms:
        return False
    shared = page_terms & figure_terms
    overlap_ratio = len(shared) / max(1, min(len(page_terms), len(figure_terms)))
    return len(shared) >= 4 and overlap_ratio >= 0.06


def _filter_figure_refs_for_page(
    figure_refs: list[dict],
    *,
    page: dict,
    source_refs: list,
    source_figures: dict[str, dict],
) -> list[dict]:
    if not source_figures:
        return figure_refs
    filtered: list[dict] = []
    for ref in figure_refs:
        figure_id = str(ref.get("figure_id") or "").strip()
        figure = source_figures.get(figure_id)
        if not figure:
            continue
        if not _figure_is_content_bearing(figure):
            continue
        ref_doc = str(ref.get("source_document") or "").strip()
        figure_doc = str(figure.get("source_document") or "").strip()
        if ref_doc and figure_doc and not _source_doc_matches(ref_doc, figure_doc):
            continue
        try:
            ref_page = int(ref.get("source_page_num") or 0)
        except (TypeError, ValueError):
            ref_page = 0
        figure_page = int(figure.get("source_page_num") or 0)
        if ref_page > 0 and figure_page > 0 and ref_page != figure_page:
            continue
        if not _figure_relevance_allows(page, source_refs, figure):
            continue
        filtered.append({
            "source_document": figure_doc or ref_doc,
            "source_page_num": figure_page or ref_page,
            "source_type": str(figure.get("source_type") or ref.get("source_type") or "pdf"),
            "figure_id": figure_id,
            "reason": str(ref.get("reason") or "").strip(),
        })
    return filtered[:8]


def _figure_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _figure_bbox_metrics(value) -> tuple[float, float, float]:
    numbers = [
        _figure_float(item)
        for item in re.findall(r"-?\d+(?:\.\d+)?", str(value or ""))
    ]
    if len(numbers) < 4:
        return 0.0, 0.0, 0.0
    width = max(0.0, numbers[2] - numbers[0])
    height = max(0.0, numbers[3] - numbers[1])
    return width, height, width * height


def _figure_is_content_bearing(figure: dict) -> bool:
    role = str(figure.get("figure_role") or "").strip().lower()
    if role in {"auxiliary", "decorative", "ornament", "icon", "logo"}:
        return False
    if role in {"content", "content_figure", "evidence", "chart", "table", "diagram"}:
        return True
    significance = str(figure.get("content_significance") or "").strip().lower()
    if significance in {"low", "auxiliary", "decorative"}:
        return False
    if significance in {"high", "medium"}:
        return True

    width = _figure_float(figure.get("image_width"))
    height = _figure_float(figure.get("image_height"))
    bbox_area = _figure_float(figure.get("bbox_area"))
    bbox_width, bbox_height, inferred_bbox_area = _figure_bbox_metrics(figure.get("bbox"))
    if not bbox_area:
        bbox_area = inferred_bbox_area
    has_metrics = any(value > 0 for value in (width, height, bbox_area, bbox_width, bbox_height))
    if not has_metrics:
        return True
    pixel_area = width * height
    if pixel_area and (pixel_area < 12_000 or min(width, height) < 45):
        return False
    if bbox_area and (bbox_area < 3_000 or min(bbox_width or 10_000, bbox_height or 10_000) < 32):
        return False
    if pixel_area >= 40_000 and min(width or 0, height or 0) >= 90:
        return True
    if bbox_area >= 20_000 and min(bbox_width or 0, bbox_height or 0) >= 80:
        return True
    return True


def _figure_is_source_page_reference(figure: dict) -> bool:
    figure_id = str(figure.get("figure_id") or "").strip()
    role = str(figure.get("figure_role") or "").strip().lower()
    asset_kind = str(figure.get("asset_kind") or "").strip().lower()
    full_page = str(figure.get("is_full_page_reference") or "").strip().lower()
    return (
        role == "source_page"
        or asset_kind == "source_page_image"
        or full_page in {"1", "true", "yes"}
        or figure_id.endswith(":page")
    )


def _figure_role_priority(figure: dict) -> float:
    role = str(figure.get("figure_role") or "").strip().lower()
    asset_kind = str(figure.get("asset_kind") or "").strip().lower()
    if _figure_is_source_page_reference(figure):
        return -50.0
    if role in {"chart", "table", "diagram"}:
        return 36.0
    if role in {"content", "content_figure", "evidence"}:
        return 30.0
    if asset_kind in {"product", "screenshot", "material"}:
        return 28.0
    if role in {"auxiliary", "decorative", "ornament", "icon", "logo"}:
        return -100.0
    return 12.0


def _figure_size_score(figure: dict) -> float:
    width = _figure_float(figure.get("image_width"))
    height = _figure_float(figure.get("image_height"))
    pixel_area = max(0.0, width) * max(0.0, height)
    bbox_area = _figure_float(figure.get("bbox_area"))
    _bbox_width, _bbox_height, inferred_bbox_area = _figure_bbox_metrics(figure.get("bbox"))
    if not bbox_area:
        bbox_area = inferred_bbox_area
    area = max(pixel_area, bbox_area)
    if area >= 150_000:
        return 10.0
    if area >= 40_000:
        return 6.0
    if area >= 12_000:
        return 2.0
    return -12.0


def _figure_text_relevance_score(page: dict | None, figure: dict) -> float:
    if not page:
        return 0.0
    page_terms = _relevance_terms(_page_map_text_for_relevance(page))
    figure_terms = _relevance_terms(str(figure.get("nearby_text") or ""))
    if not page_terms or not figure_terms:
        return 0.0
    shared = page_terms & figure_terms
    overlap_ratio = len(shared) / max(1, min(len(page_terms), len(figure_terms)))
    return min(30.0, len(shared) * 4.0 + overlap_ratio * 12.0)


def _allows_source_page_auto_selection(page: dict | None, source_refs: list) -> bool:
    text = _page_map_text_for_relevance(page or {})
    ref_reasons = " ".join(str(ref.get("reason") or "") for ref in source_refs if isinstance(ref, dict))
    combined = f"{text}\n{ref_reasons}"
    return bool(re.search(r"(整页(?:截图|原图)?(?:作为|当作)?(?:本页)?素材|whole[- ]page image asset)", combined, flags=re.IGNORECASE))


def _figure_selection_score(page: dict | None, figure: dict) -> float:
    significance = str(figure.get("content_significance") or "").strip().lower()
    significance_score = {"high": 8.0, "medium": 4.0, "low": -8.0}.get(significance, 0.0)
    return (
        _figure_role_priority(figure)
        + significance_score
        + _figure_size_score(figure)
        + _figure_text_relevance_score(page, figure)
    )


def _source_context_figures_for_source_refs(source_refs: list, source_figures: dict[str, dict], page: dict | None = None) -> list[dict]:
    if not source_refs or not source_figures:
        return []
    candidates: list[tuple[float, dict, dict]] = []
    seen: set[str] = set()
    allow_source_page = _allows_source_page_auto_selection(page, source_refs)
    for figure in source_figures.values():
        figure_id = str(figure.get("figure_id") or "").strip()
        if not _is_valid_figure_id(figure_id) or figure_id in seen:
            continue
        is_source_page = _figure_is_source_page_reference(figure)
        if is_source_page and not allow_source_page:
            continue
        if not _figure_is_content_bearing(figure):
            continue
        figure_doc = str(figure.get("source_document") or "").strip()
        try:
            figure_page = int(figure.get("source_page_num") or 0)
        except (TypeError, ValueError):
            figure_page = 0
        if not figure_doc or figure_page <= 0:
            continue
        for source_ref in source_refs:
            if not isinstance(source_ref, dict):
                continue
            ref_doc = str(source_ref.get("source_document") or "").strip()
            try:
                ref_page = int(source_ref.get("source_page_num") or 0)
            except (TypeError, ValueError):
                ref_page = 0
            if ref_page != figure_page or not _source_doc_matches(ref_doc, figure_doc):
                continue
            seen.add(figure_id)
            ref = {
                "source_document": figure_doc,
                "source_page_num": figure_page,
                "source_type": str(figure.get("source_type") or source_ref.get("source_type") or "pdf"),
                "figure_id": figure_id,
                "reason": str(figure.get("nearby_text") or source_ref.get("reason") or "source_figure").strip(),
            }
            candidates.append((_figure_selection_score(page, figure), ref, figure))
            break
    candidates.sort(key=lambda item: (
        item[0],
        _figure_float(item[2].get("bbox_area")),
        _figure_float(item[2].get("image_width")) * _figure_float(item[2].get("image_height")),
    ), reverse=True)
    return [ref for _score, ref, _figure in candidates[:8]]


def _source_refs_from_figure_refs(figure_refs: list[dict]) -> list[dict]:
    refs: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for item in figure_refs:
        source_document = str(item.get("source_document") or "").strip()
        try:
            source_page_num = int(item.get("source_page_num") or 0)
        except (TypeError, ValueError):
            source_page_num = 0
        if not source_document or source_page_num <= 0:
            continue
        key = (source_document, source_page_num)
        if key in seen:
            continue
        seen.add(key)
        refs.append({
            "source_document": source_document,
            "source_page_num": source_page_num,
            "source_type": str(item.get("source_type") or "pdf"),
            "reason": str(item.get("reason") or "figure_ref").strip(),
        })
    return refs


def _clean_page_map_source_ref_values(value) -> list:
    if not isinstance(value, list):
        return []
    refs: list = []
    for item in value:
        if isinstance(item, str):
            cleaned = _strip_source_context_markers(item).strip()
            if cleaned:
                refs.append(cleaned)
            continue
        if isinstance(item, dict):
            source_document = _strip_source_context_markers(str(item.get("source_document") or "")).strip()
            if _is_source_context_marker_line(source_document):
                continue
            cleaned_item = {**item}
            if source_document:
                cleaned_item["source_document"] = source_document
            reason = str(cleaned_item.get("reason") or "")
            if reason:
                cleaned_item["reason"] = _strip_source_context_markers(reason).strip()
            refs.append(cleaned_item)
    return refs[:8]


def _normalize_source_refs_for_page_map(value, source_documents: dict[str, str] | None = None) -> list:
    if not isinstance(value, list):
        return []
    refs: list = []
    known_sources = source_documents or {}
    for item in value:
        if isinstance(item, str):
            if _is_source_context_marker_line(item) or not _strip_source_context_markers(item).strip():
                continue
            parsed = _parse_source_ref_string(item, known_sources)
            refs.append(parsed if parsed else item)
            continue
        if isinstance(item, dict):
            if _is_source_context_marker_line(str(item.get("source_document") or "")):
                continue
            normalized = _normalize_source_refs([item], known_sources)
            refs.append(normalized[0] if normalized else item)
    return refs[:8]


def _normalize_source_refs(value, source_documents: dict[str, str] | None = None) -> list[dict]:
    if not isinstance(value, list):
        return []
    refs: list[dict] = []
    known_sources = source_documents or {}
    for item in value:
        if isinstance(item, str):
            parsed = _parse_source_ref_string(item, known_sources)
            if parsed:
                refs.append(parsed)
            continue
        if not isinstance(item, dict):
            continue
        source_document = str(item.get("source_document") or item.get("filename") or "").strip()
        try:
            source_page_num = int(item.get("source_page_num") or item.get("page_num") or 0)
        except (TypeError, ValueError):
            source_page_num = 0
        if not source_document or source_page_num <= 0:
            continue
        source_kind = str(item.get("source_type") or _source_kind_from_document(source_document, known_sources))
        refs.append({
            "source_document": source_document,
            "source_page_num": source_page_num,
            "source_type": "pptx_slide" if source_kind == "pptx" else source_kind,
            "reason": str(item.get("reason") or item.get("usage") or "").strip(),
        })
    return refs[:8]


def _annotate_ppt_source_refs(
    outline: List[Dict],
    documents: str,
    topic: str = "",
    intent_contract: dict | None = None,
) -> List[Dict]:
    """Keep a stable link from generated pages back to source PPT pages."""
    sources = detect_ppt_sources(documents)
    source_documents = _source_context_documents(documents)
    single_source = sources[0] if len(sources) == 1 else None
    policy = _planning_policy(topic, documents, intent_contract)
    direct_single_ppt_polish = bool(single_source and policy["preserve_source_page_order"])
    single_filename = str((single_source or {}).get("filename") or "").strip()
    single_page_count = int((single_source or {}).get("pages") or 0) if single_source else 0

    for page in outline:
        if not isinstance(page, dict):
            continue
        refs = _normalize_source_refs(page.get("source_refs"), source_documents)
        page_num = int(page.get("page_num") or 0)
        if not refs and direct_single_ppt_polish and single_filename and 1 <= page_num <= single_page_count:
            refs = [{
                "source_document": single_filename,
                "source_page_num": page_num,
                "source_type": "pptx_slide",
                "reason": "single_ppt_page_polish",
            }]
        if refs:
            page["source_refs"] = refs
    return outline


def _should_preserve_expanded_model_outline(
    *,
    outline_count: int,
    target_count: int,
    max_count: int,
    strict_page_count: bool,
    allow_expanded_outline_override: bool,
) -> bool:
    if strict_page_count or not allow_expanded_outline_override:
        return False
    if target_count >= MIN_UNPROMPTED_CONTENT_PLAN_PAGE_COUNT:
        return False
    return outline_count >= max(EXPANDED_OUTLINE_OVERRIDE_MIN_PAGES, max_count * 4)


def _normalize_outline_page_count(
    outline: List[Dict],
    page_count: int,
    strict_page_count: bool = False,
    allow_expanded_outline_override: bool = False,
) -> List[Dict]:
    """Keep LLM output reasonable while treating page_count as a soft target by default."""
    if not isinstance(outline, list):
        raise ValueError("Content plan generation failed: LLM output is not a JSON array")
    target_count = max(1, int(page_count or len(outline) or 1))
    max_count = target_count if strict_page_count else _soft_page_bounds(target_count)[1]
    preserve_expanded_outline = _should_preserve_expanded_model_outline(
        outline_count=len(outline),
        target_count=target_count,
        max_count=max_count,
        strict_page_count=strict_page_count,
        allow_expanded_outline_override=allow_expanded_outline_override,
    )
    if len(outline) > max_count and not preserve_expanded_outline:
        logger.warning(
            f"ContentPlan: LLM returned {len(outline)} pages, trimming to max {max_count} "
            f"(target={target_count}, strict={strict_page_count})"
        )
        closing_page = outline[-1] if isinstance(outline[-1], dict) else None
        if max_count >= 2 and closing_page and str(closing_page.get("type") or "").lower() == "ending":
            outline = outline[: max_count - 1] + [{**closing_page}]
        else:
            outline = outline[:max_count]
    elif preserve_expanded_outline:
        logger.warning(
            f"ContentPlan: preserving expanded {len(outline)} page outline despite low-confidence "
            f"target={target_count} (soft max={max_count})"
        )
    for idx, page in enumerate(outline, start=1):
        if isinstance(page, dict):
            page["page_num"] = idx
            page_type = str(page.get("type") or "content").strip().lower() or "content"
            if idx == 1:
                if page_type != "cover" and _page_map_preserves_source_page_type(page):
                    page["type"] = page_type if page_type != "ending" else "content"
                else:
                    page["type"] = "cover"
            elif idx == len(outline) and len(outline) > 1:
                if page_type != "ending" and _page_map_preserves_source_page_type(page):
                    page["type"] = page_type if page_type != "cover" else "section"
                else:
                    page["type"] = "ending"
            elif page_type == "cover":
                page["type"] = "section"
            elif page_type == "ending":
                page["type"] = "content"
    return outline


def _outline_page_headline(page: Dict) -> str:
    text_content = page.get("text_content") if isinstance(page, dict) else {}
    if isinstance(text_content, dict):
        return str(text_content.get("headline") or "").strip()
    return str(page.get("headline") or "").strip() if isinstance(page, dict) else ""


def _outline_extension_summary(outline: List[Dict], limit: int = 80) -> str:
    lines: list[str] = []
    for page in outline[-limit:]:
        if not isinstance(page, dict):
            continue
        text_content = page.get("text_content") if isinstance(page.get("text_content"), dict) else {}
        body = str(text_content.get("body") or "").replace("\n", " ").strip()
        body_preview = body[:90] + ("..." if len(body) > 90 else "")
        headline = _outline_page_headline(page) or "未命名页面"
        section = str(page.get("section_title") or "").strip()
        lines.append(
            f"P{page.get('page_num') or len(lines) + 1} [{page.get('type') or 'content'}]"
            f"{' / ' + section if section else ''}: {headline}"
            f"{' - ' + body_preview if body_preview else ''}"
        )
    return "\n".join(lines)


def _document_excerpt_for_extension(documents: str, limit: int = 12000) -> str:
    text = (documents or "").strip()
    if len(text) <= limit:
        return text
    head = text[: int(limit * 0.7)].rstrip()
    tail = text[-int(limit * 0.3):].lstrip()
    return f"{head}\n\n...[中间材料已省略，用于控制续写上下文长度]...\n\n{tail}"


def _should_generate_deck_blueprint(
    requested_page_range: tuple[int, int] | None,
    page_count: int,
    documents: str,
) -> bool:
    return (page_count or 0) >= LONG_DECK_INCREMENTAL_THRESHOLD


def _requested_range_soft_upper(min_pages: int, max_pages: int) -> int:
    return max_pages + max(2, int((max_pages - min_pages) * 0.2 + 0.999))


def _estimate_document_page_capacity(documents: str) -> int | None:
    text = sanitize_ppt_recovery_text_for_content(documents).strip()
    if not text:
        return None
    if _is_brief_only_source_context(text):
        return None
    units = extract_document_outline_units(text)
    if not units:
        line_count = len([line for line in text.splitlines() if line.strip()])
        char_count = len(text)
        return max(1, int(char_count / 140) + int(line_count / 4) + 2)

    units_for_draft = _with_child_context([
        unit for unit in units if str(unit.get("title") or "").strip() != "用户上传材料"
    ] or units)
    root_title = ""
    for unit in units_for_draft:
        if int(unit.get("level") or 9) <= 1 and str(unit.get("title") or "").strip() != "用户上传材料":
            root_title = str(unit.get("title") or "").strip()
            break
    source_units = [unit for unit in units_for_draft if str(unit.get("title") or "").strip() != root_title]
    specs = _unit_page_specs(source_units or units_for_draft)
    line_count = sum(len(unit.get("plain_lines") or []) for unit in units_for_draft)
    char_count = sum(len("\n".join(str(line) for line in (unit.get("plain_lines") or []))) for unit in units_for_draft)
    return max(
        1,
        len(specs) + 2,
        int(char_count / 140) + 2,
        int(line_count / 3) + 2,
    )


def _infer_document_driven_page_count(documents: str) -> int | None:
    """Pick a practical default page count when the user uploads a dense brief."""
    text = sanitize_ppt_recovery_text_for_content(documents).strip()
    if len(text) < AUTO_DOCUMENT_PAGE_MIN_CHARS:
        return None

    units = extract_document_outline_units(text)
    content_units = [
        unit for unit in units
        if str(unit.get("title") or "").strip() != "用户上传材料"
    ]
    root_title = ""
    for unit in content_units:
        if int(unit.get("level") or 9) <= 1:
            root_title = str(unit.get("title") or "").strip()
            break
    source_units = [
        unit for unit in content_units
        if str(unit.get("title") or "").strip() != root_title
    ] or content_units

    char_count = len(text)
    heading_count = sum(1 for unit in source_units if int(unit.get("level") or 9) <= 4)
    char_target = int(char_count / AUTO_DOCUMENT_CHARS_PER_SLIDE) + 4
    structure_target = int(heading_count * 0.45) + 4 if heading_count else 0
    target = max(char_target, structure_target, AUTO_DOCUMENT_PAGE_MIN)

    if char_count >= 20_000:
        target = max(target, 32)
    elif char_count >= 12_000:
        target = max(target, 24)
    elif char_count >= 7_000:
        target = max(target, 16)

    capacity = _estimate_document_page_capacity(text)
    if capacity is not None:
        target = min(target, capacity)
    return max(AUTO_DOCUMENT_PAGE_MIN, min(AUTO_DOCUMENT_PAGE_MAX, target))


def _infer_source_capacity_page_count(documents: str) -> int | None:
    """Use source capacity, not summary density, when the contract asks for source restoration."""
    text = sanitize_ppt_recovery_text_for_content(documents).strip()
    capacity = _estimate_document_page_capacity(text)
    if capacity is None:
        return None

    source_sections = _source_deck_sections(text, limit=80)
    if len(text) < AUTO_DOCUMENT_PAGE_MIN_CHARS and not source_sections and capacity < LONG_DECK_INCREMENTAL_THRESHOLD:
        return None

    compressed_default = _infer_document_driven_page_count(text) or min(
        AUTO_DOCUMENT_PAGE_MAX,
        max(AUTO_DOCUMENT_PAGE_MIN, capacity),
    )
    section_floor = len(source_sections) * 4 + 3 if source_sections else 0
    if len(text) >= 7_000:
        section_floor = max(section_floor, 60 if len(source_sections) >= 8 else 40)
    else:
        section_floor = max(section_floor, 40 if len(source_sections) >= 6 else 24)
    section_floor = min(section_floor, max(1, int(capacity * 1.25 + 0.999)))

    capacity_target = int(capacity * 0.9 + 0.999)
    target = max(compressed_default, section_floor, capacity_target)
    return max(AUTO_DOCUMENT_PAGE_MIN, min(AUTO_RESTORATION_PAGE_MAX, target))


def _resolve_soft_range_target(
    *,
    topic: str,
    documents: str,
    resolved_page_count: int,
    min_pages: int,
    max_pages: int,
) -> int:
    natural_count = _estimate_document_page_capacity(documents)
    hard_upper = _is_hard_upper_page_count_request(topic)
    if natural_count is None:
        if hard_upper and min_pages <= 1:
            natural_count = min(max_pages, 10)
        else:
            natural_count = resolved_page_count or max_pages

    upper_bound = max_pages if hard_upper else _requested_range_soft_upper(min_pages, max_pages)
    if min_pages > 1 and natural_count >= int(min_pages * 0.65):
        natural_count = max(min_pages, natural_count)
    return max(1, min(upper_bound, natural_count))


def _single_page_source_count(documents: str) -> int | None:
    sources = detect_ppt_sources(documents)
    if len(sources) == 1:
        try:
            pages = int(sources[0].get("pages") or 0)
        except (TypeError, ValueError):
            pages = 0
        if pages > 0:
            return pages
    return _source_context_page_source_count(documents)


def _same_source_page_count_for_contract(
    topic: str,
    documents: str,
    intent_contract: dict | None = None,
) -> int | None:
    if not documents.strip() or intent_contract is None:
        return None
    contract = (
        normalize_content_director_contract(intent_contract)
        if is_content_director_contract(intent_contract)
        else _director_contract_from_legacy_intent(intent_contract)
    )
    if (
        contract.get("page_budget_policy") == "same_as_source"
        and contract.get("structure_policy") == "preserve_order"
    ):
        return _single_page_source_count(documents)
    return None


def resolve_content_plan_page_target(
    topic: str,
    page_count: int | None,
    documents: str = "",
    intent_contract: dict | None = None,
) -> tuple[int, int, int]:
    """Return target, min, max pages using the same policy as content-plan generation."""
    documents = sanitize_ppt_recovery_text_for_content(documents)
    requested_page_range = infer_page_count_range_from_topic(topic)
    explicit_page_count = resolve_requested_content_plan_page_count(topic, page_count)
    same_source_page_count = _same_source_page_count_for_contract(topic, documents, intent_contract)
    if same_source_page_count and not requested_page_range and not explicit_page_count:
        return same_source_page_count, same_source_page_count, same_source_page_count
    contract = normalize_content_director_contract(intent_contract) if is_content_director_contract(intent_contract) else None
    uses_source_capacity = bool(
        contract
        and contract["page_budget_policy"] == "source_capacity"
        and contract["coverage"] in {"near_complete", "complete"}
        and contract["compression"] == "low"
    )
    source_capacity_page_count = (
        None
        if explicit_page_count or requested_page_range or not uses_source_capacity
        else _infer_source_capacity_page_count(documents)
    )
    auto_document_page_count = (
        None
        if explicit_page_count or requested_page_range or source_capacity_page_count
        else _infer_document_driven_page_count(documents)
    )
    resolved_page_count = max(1, int(explicit_page_count or source_capacity_page_count or auto_document_page_count or 10))
    if requested_page_range and resolved_page_count == 10:
        resolved_page_count = requested_page_range[1]
    strict_page_count = _is_strict_page_count_request(topic) and not requested_page_range
    if requested_page_range:
        min_pages, max_pages = requested_page_range
    elif strict_page_count:
        min_pages, max_pages = resolved_page_count, resolved_page_count
    elif source_capacity_page_count:
        lower_floor = LONG_DECK_INCREMENTAL_THRESHOLD if resolved_page_count >= LONG_DECK_INCREMENTAL_THRESHOLD else 1
        min_pages, max_pages = min(
            resolved_page_count,
            max(lower_floor, int(resolved_page_count * 0.8)),
        ), resolved_page_count
    elif auto_document_page_count:
        min_pages, max_pages = max(1, int(resolved_page_count * 0.8)), resolved_page_count
    else:
        min_pages, max_pages = _soft_page_bounds(resolved_page_count)
    target_count = max(min_pages, min(max_pages, resolved_page_count if resolved_page_count else max_pages))
    if requested_page_range:
        target_count = _resolve_soft_range_target(
            topic=topic,
            documents=documents,
            resolved_page_count=resolved_page_count,
            min_pages=min_pages,
            max_pages=max_pages,
        )
    return target_count, min_pages, max_pages


def should_generate_incremental_long_deck(
    topic: str,
    page_count: int | None,
    documents: str = "",
    intent_contract: dict | None = None,
) -> bool:
    target_count, min_pages, max_pages = resolve_content_plan_page_target(topic, page_count, documents, intent_contract)
    return _should_generate_deck_blueprint((min_pages, max_pages), target_count, documents)


def _build_content_plan_job(
    *,
    topic: str,
    audience: str = "通用受众",
    page_count: int | None = None,
    documents: str = "",
    intent_contract: dict | None = None,
    chat_context: str | None = None,
) -> ContentPlanJob:
    documents = sanitize_ppt_recovery_text_for_content(documents)
    chat_context_text = (chat_context or "").strip()
    if chat_context_text:
        topic = content_plan_topic_with_chat_context(topic, chat_context_text)
        logger.info(
            "ContentPlan: 注入 chat_context 反馈 (%s chars) 到 topic 前端，用于驱动重新生成",
            len(chat_context_text),
        )

    effective_intent_contract = _effective_intent_contract(topic, documents, intent_contract)
    if page_count is None:
        inferred_ppt_pages = infer_page_count_from_single_ppt(
            documents,
            topic,
            intent_contract=effective_intent_contract,
        )
        if inferred_ppt_pages:
            page_count = inferred_ppt_pages

    requested_page_range = infer_page_count_range_from_topic(topic)
    resolved_page_count, min_pages, max_pages = resolve_content_plan_page_target(
        topic,
        page_count,
        documents,
        intent_contract=effective_intent_contract,
    )
    strict_page_count = _is_strict_page_count_request(topic) and not requested_page_range
    allow_expanded_outline_override = (
        not requested_page_range
        and not strict_page_count
        and resolved_page_count < MIN_UNPROMPTED_CONTENT_PLAN_PAGE_COUNT
        and not _has_explicit_short_page_count_request(topic, resolved_page_count)
    )
    ppt_sources = detect_ppt_sources(documents)
    mode = "direct_replicate" if _is_direct_replicate_request(topic, documents, effective_intent_contract) else "default"
    planning_policy = _planning_policy(topic, documents, effective_intent_contract)

    return ContentPlanJob(
        topic=topic,
        audience=audience,
        page_count=resolved_page_count,
        min_pages=min_pages,
        max_pages=max_pages,
        documents=documents,
        has_docs=bool(documents and documents.strip()),
        requested_page_range=requested_page_range,
        strict_page_count=strict_page_count,
        allow_expanded_outline_override=allow_expanded_outline_override,
        intent_contract=effective_intent_contract,
        ppt_sources=ppt_sources,
        planning_policy=planning_policy,
        mode=mode,
        exported_outline=parse_exported_content_plan_markdown(documents),
        paginated_markdown_outline=parse_paginated_markdown_content_plan(documents),
    )


def _can_reuse_uploaded_content_plan(job: ContentPlanJob) -> bool:
    return (
        not job.requested_page_range
        and not job.strict_page_count
        and not _is_general_transform_request(job.topic)
    )


def _select_content_plan_strategy(job: ContentPlanJob) -> str:
    if job.exported_outline and _can_reuse_uploaded_content_plan(job):
        return CONTENT_PLAN_STRATEGY_REUSE_EXPORTED
    if job.paginated_markdown_outline and _can_reuse_uploaded_content_plan(job):
        return CONTENT_PLAN_STRATEGY_REUSE_PAGINATED

    return CONTENT_PLAN_STRATEGY_PAGE_MAP


def _ensure_search_context(job: ContentPlanJob) -> ContentPlanJob:
    if job.search_context:
        return job
    job.search_context = get_knowledge_augmenter().augment(job.topic, has_documents=job.has_docs)
    if job.search_context:
        logger.info(f"ContentPlan: 已注入搜索上下文，topic={job.topic[:30]}")
    return job


def _long_deck_section_ranges(target_count: int) -> list[tuple[int, int, str]]:
    target_count = max(1, int(target_count or 1))
    if target_count <= 2:
        return []
    body_start = 2
    body_end = target_count - 1
    body_pages = max(0, body_end - body_start + 1)
    section_count = 8 if target_count >= 60 else 6
    section_titles = LONG_DECK_SECTION_TITLES[:section_count]
    base = body_pages // section_count if section_count else body_pages
    remainder = body_pages % section_count if section_count else 0
    ranges: list[tuple[int, int, str]] = []
    cursor = body_start
    for idx, title in enumerate(section_titles):
        length = base + (1 if idx < remainder else 0)
        if length <= 0:
            continue
        start = cursor
        end = min(body_end, cursor + length - 1)
        cursor = end + 1
        if start > body_end:
            break
        ranges.append((start, end, title))
    return ranges


def _source_deck_sections(documents: str, *, limit: int = 18) -> list[str]:
    text = sanitize_ppt_recovery_text_for_content(documents or "")
    if not text.strip():
        return []
    sections: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        match = re.match(r"^\s{0,3}#{2,4}\s+(.+?)\s*$", str(raw_line or ""))
        if not match:
            continue
        title = _clean_markdown_inline(match.group(1))
        title = re.sub(r"^[一二三四五六七八九十百]+[、.．]\s*", "", title).strip()
        if not title or len(title) > 80 or title in seen:
            continue
        seen.add(title)
        sections.append(title)
        if len(sections) >= limit:
            break
    return sections if len(sections) >= 3 else []


def _distribute_source_sections(*, target_count: int, sections: list[str]) -> list[tuple[int, int, str]]:
    target_count = max(1, int(target_count or 1))
    if target_count <= 3 or not sections:
        return []
    body_start = 3
    body_end = target_count - 1
    body_pages = max(0, body_end - body_start + 1)
    usable_sections = sections[:body_pages] if body_pages else []
    if not usable_sections:
        return []
    base = body_pages // len(usable_sections)
    remainder = body_pages % len(usable_sections)
    ranges: list[tuple[int, int, str]] = []
    cursor = body_start
    for idx, title in enumerate(usable_sections):
        length = base + (1 if idx < remainder else 0)
        start = cursor
        end = min(body_end, cursor + length - 1)
        cursor = end + 1
        ranges.append((start, end, title))
    return ranges


def _source_page_section_plan(documents: str, target_count: int) -> dict[int, str]:
    sections = _source_deck_sections(documents)
    ranges = _distribute_source_sections(target_count=target_count, sections=sections)
    plan: dict[int, str] = {}
    for start, end, title in ranges:
        for page_num in range(start, end + 1):
            plan[page_num] = title
    return plan


def _source_section_first_pages(documents: str, target_count: int) -> set[int]:
    sections = _source_deck_sections(documents)
    return {start for start, _end, _title in _distribute_source_sections(target_count=target_count, sections=sections)}


def _format_source_page_plan(plan: dict[int, str], start_page: int, end_page: int) -> str:
    lines = [f"P{page_num}：{plan[page_num]}" for page_num in range(start_page, end_page + 1) if plan.get(page_num)]
    return "\n".join(lines) or "无；按全局蓝图和已有页面摘要接续。"


def _enforce_source_page_section(
    page: dict,
    *,
    page_num: int,
    target_count: int,
    source_page_plan: dict[int, str],
    source_section_first_pages: set[int],
) -> None:
    expected_section = source_page_plan.get(page_num)
    if not expected_section:
        return

    page["section_title"] = expected_section
    page_type = _canonical_content_plan_type(page.get("type") or "content")
    is_section_start = page_num in source_section_first_pages and page_num not in {1, target_count}
    if is_section_start:
        page["type"] = "section"
        text_content = page.get("text_content") if isinstance(page.get("text_content"), dict) else {}
        headline = _clean_headline_text(str(text_content.get("headline") or ""))
        if not headline or headline.lower() in GENERIC_CONTENT_HEADLINES or "模型漂移" in headline:
            text_content["headline"] = expected_section
        page["text_content"] = text_content
    elif page_type == "section":
        page["type"] = "content"


def _source_module_key(title: str) -> str:
    text = _clean_markdown_inline(title)
    match = re.search(r"第[一二三四五六七八九十百\d]+部", text)
    return match.group(0) if match else ""


def _missing_required_source_modules(outline: list[dict], documents: str) -> list[str]:
    required = [_source_module_key(title) for title in _source_deck_sections(documents)]
    required = [key for key in dict.fromkeys(required) if key]
    if not required:
        return []
    planned_text = "\n".join(
        "\n".join(
            str(value or "")
            for value in (
                page.get("section_title"),
                (page.get("text_content") or {}).get("headline") if isinstance(page.get("text_content"), dict) else "",
            )
        )
        for page in outline
        if isinstance(page, dict)
    )
    return [key for key in required if key not in planned_text]


def _brief_title(topic: str) -> str:
    text = re.sub(r"【文件：.*?】", "", topic or "", flags=re.DOTALL)
    text = re.sub(r"已上传材料：.*", "", text, flags=re.DOTALL)
    text = re.sub(r"识别到页数目标：.*", "", text, flags=re.DOTALL)
    lines = [line.strip(" ：:，,。") for line in text.splitlines() if line.strip()]
    for line in lines:
        cleaned = re.sub(r"我[要想希望]?制作一份|请?帮我|做成|PPT|ppt", "", line).strip(" ：:，,。")
        if 4 <= len(cleaned) <= 36:
            return cleaned
    return "PPT 内容规划"


def _clean_markdown_inline(text: str) -> str:
    value = normalize_markdown_emphasis(str(text or ""))
    if is_markdown_thematic_break_line(value):
        return ""
    value = re.sub(r"^\s{0,3}#{1,6}\s+", "", value)
    value = re.sub(r"`([^`]*)`", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"[*_~]+", "", value)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _is_source_context_marker_line(line: str) -> bool:
    return bool(_SOURCE_CONTEXT_MARKER_LINE_RE.match(str(line or "").strip()))


def _source_context_document_title(line: str) -> str:
    match = _SOURCE_CONTEXT_SOURCE_FILENAME_RE.match(str(line or "").strip())
    if not match:
        return ""
    filename = match.group(1).strip()
    title = re.sub(r"\.(?:pdf|docx?|pptx?|md|markdown|txt)$", "", filename, flags=re.IGNORECASE)
    return _clean_markdown_inline(title)


def _strip_source_context_markers(text: str) -> str:
    cleaned_lines: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if _is_source_context_marker_line(line):
            continue
        line = _SOURCE_CONTEXT_INLINE_MARKER_RE.sub("", line)
        line = _SOURCE_CONTEXT_FIGURE_INLINE_RE.sub("", line)
        line = re.sub(r"^[（(]?\s*续\s*\d+\s*[）)]?$", "", line).strip()
        if line:
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def _clean_visible_page_map_text(value: str) -> str:
    cleaned = _strip_source_context_markers(str(value or ""))
    lines = [_clean_markdown_inline(line) for line in cleaned.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines).strip()


def _is_table_separator(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and set(stripped) <= {"|", "-", ":", " "}


def _markdown_line_to_plain(line: str) -> str:
    text = line.strip()
    if not text:
        return ""
    if re.match(r"^(?:`{3,}|~{3,})\s*[\w.+-]*\s*$", text):
        return ""
    if is_markdown_thematic_break_line(text):
        return ""
    text = re.sub(r"^>\s*", "", text)
    text = re.sub(r"^[-*+]\s+", "", text)
    text = re.sub(r"^\d+[.)、]\s+", "", text)
    text = text.strip()
    if _is_table_separator(text):
        return ""
    if "|" in text:
        cells = [_clean_markdown_inline(cell) for cell in text.strip("|").split("|")]
        cells = [cell for cell in cells if cell and cell != "------"]
        if cells:
            return " / ".join(cells)
    return _clean_markdown_inline(text)


DOCUMENT_BOUNDARY_RE = re.compile(r"^---\s*文档:\s*(.+?)\s*---\s*$")
PLAIN_CHINESE_HEADING_RE = re.compile(r"^([一二三四五六七八九十百]+)[、．.]\s*(.+?)\s*$")
PLAIN_DECIMAL_HEADING_RE = re.compile(r"^(\d+(?:\.\d+){1,3})\s+(.+?)\s*$")
MERMAID_START_RE = re.compile(
    r"^\s*(?:"
    r"(?:graph|flowchart)\s+(?:TB|TD|BT|RL|LR)\b|"
    r"sequenceDiagram\b|classDiagram\b|stateDiagram(?:-v2)?\b|erDiagram\b|"
    r"journey\b|gantt\b|pie\b|mindmap\b|timeline\b|quadrantChart\b|gitGraph\b"
    r")",
    flags=re.IGNORECASE,
)
MERMAID_NODE_LABEL_RE = re.compile(
    r"\b([A-Za-z][\w-]*)\s*(?:\[\s*\"([^\"]+)\"\s*\]|\(\s*\"([^\"]+)\"\s*\)|\{\s*\"([^\"]+)\"\s*\})"
)
MERMAID_EDGE_RE = re.compile(
    r"\b([A-Za-z][\w-]*)\b\s*(?:-->|---|==>|-.->|--[^-]+-->|-\.[^.]+\.->)\s*\b([A-Za-z][\w-]*)\b"
)
MERMAID_RELATION_RE = re.compile(
    r"^\s*(?P<left>.+?)\s*(?:-->>|->>|-->|---|==>|-.->|<\|--|--\|>|o--|--o|\*--|--\*)\s*"
    r"(?P<right>.+?)(?:\s*:\s*(?P<label>.+))?\s*$"
)
MERMAID_DIRECTIVE_RE = re.compile(
    r"^\s*(?:"
    r"%%|accTitle|accDescr|title|subgraph|end\b|style\b|classDef\b|class\b|click\b|linkStyle\b|"
    r"direction\b|participant\b|actor\b|autonumber\b|activate\b|deactivate\b|note\b"
    r")",
    flags=re.IGNORECASE,
)
MERMAID_PARTICIPANT_RE = re.compile(r"^\s*(?:participant|actor)\s+(\S+)(?:\s+as\s+(.+))?\s*$", flags=re.IGNORECASE)


def _document_boundary_title(line: str) -> str:
    source_title = _source_context_document_title(line)
    if source_title:
        return source_title
    match = DOCUMENT_BOUNDARY_RE.match(str(line or "").strip())
    if not match:
        return ""
    filename = match.group(1).strip()
    title = re.sub(r"\.(?:pdf|docx?|pptx?|md|markdown|txt)$", "", filename, flags=re.IGNORECASE)
    return _clean_markdown_inline(title)


def _is_source_decoration_line(line: str) -> bool:
    text = str(line or "").strip()
    if _is_source_context_marker_line(text):
        return True
    return bool(text) and not re.search(r"[A-Za-z0-9\u4e00-\u9fff]", text)


def _is_source_production_note_line(line: str) -> bool:
    text = str(line or "").strip()
    return bool(re.match(r"^【(?:想法|待办|TODO|ToDo|todo|修复)】", text))


def _plain_source_heading(line: str) -> tuple[int, str] | None:
    text = _clean_markdown_inline(str(line or "").strip())
    if not text or len(text) > 90:
        return None
    match = PLAIN_CHINESE_HEADING_RE.match(text)
    if match:
        return 2, match.group(2).strip()
    match = PLAIN_DECIMAL_HEADING_RE.match(text)
    if match:
        depth = match.group(1).count(".")
        return min(6, 2 + depth), match.group(2).strip()
    return None


def _plain_unit_title_from_line(line: str, fallback: str = "") -> str:
    if _is_source_context_marker_line(line):
        return fallback
    text = _clean_markdown_inline(str(line or "").strip())
    if not text or _is_source_decoration_line(text):
        return fallback
    if len(text) <= 60:
        return text
    return fallback or text[:36].rstrip("，。；：:、") or "材料正文"


def _looks_like_wrapped_heading_continuation(line: str) -> bool:
    text = _clean_markdown_inline(str(line or "").strip())
    if not text or len(text) > 12:
        return False
    if text.endswith(("：", ":")):
        return False
    if DOCUMENT_BOUNDARY_RE.match(text) or _plain_source_heading(text) or _is_mermaid_start(text):
        return False
    return bool(re.search(r"[\u4e00-\u9fffA-Za-z0-9]", text))


def _join_wrapped_plain_source_lines(lines: list[str]) -> list[str]:
    joined: list[str] = []
    idx = 0
    while idx < len(lines):
        line = str(lines[idx] or "").rstrip()
        next_line = str(lines[idx + 1] or "").strip() if idx + 1 < len(lines) else ""
        next_clean = _clean_markdown_inline(next_line)
        should_join_heading = (
            bool(_plain_source_heading(line))
            and not re.match(r"^\s*#{1,6}\s+", line)
            and _looks_like_wrapped_heading_continuation(next_line)
            and len(next_clean) <= 3
        )
        should_join_title = (
            not should_join_heading
            and not _plain_source_heading(line)
            and any(marker in line for marker in ("｜", "|", "——", "·"))
            and _looks_like_wrapped_heading_continuation(next_line)
        )
        if should_join_heading or should_join_title:
            joined.append(line + next_line)
            idx += 2
            continue
        joined.append(line)
        idx += 1
    return joined


def _is_mermaid_start(line: str) -> bool:
    return bool(MERMAID_START_RE.match(str(line or "")))


def _mermaid_continue_line(line: str) -> bool:
    raw = str(line or "")
    stripped = raw.strip()
    if not stripped:
        return False
    if DOCUMENT_BOUNDARY_RE.match(stripped) or _plain_source_heading(stripped):
        return False
    if raw.startswith((" ", "\t")):
        return True
    if MERMAID_EDGE_RE.search(stripped):
        return True
    if MERMAID_NODE_LABEL_RE.search(stripped):
        return True
    return _is_mermaid_start(stripped)


def _mermaid_has_unclosed_label(lines: list[str]) -> bool:
    text = "\n".join(str(line or "") for line in lines)
    opened = len(re.findall(r"[\[\(\{]\s*\"", text))
    closed = len(re.findall(r"\"\s*[\]\)\}]", text))
    return opened > closed


def _compact_source_phrase(text: str, limit: int = 36) -> str:
    value = re.sub(r"\s+", " ", _clean_markdown_inline(text)).strip()
    value = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", value)
    if len(value) <= limit:
        return value
    return value[:limit].rstrip("，。；：:、") + "…"


def _mermaid_summary_label(first_line: str) -> str:
    text = str(first_line or "").strip().lower()
    if text.startswith(("graph", "flowchart")):
        return "流程图"
    if text.startswith("sequence"):
        return "时序图"
    if text.startswith("class"):
        return "类图"
    if text.startswith("state"):
        return "状态图"
    if text.startswith("er"):
        return "关系图"
    return "图示关系"


def _clean_mermaid_endpoint(value: str, labels: dict[str, str]) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s*:::.*$", "", text)
    text = re.sub(r"\s*\|.*?\|\s*$", "", text).strip()
    match = MERMAID_NODE_LABEL_RE.search(text)
    if match:
        label = next((group for group in match.groups()[1:] if group), "")
        return _compact_source_phrase(label or match.group(1))
    bare = re.sub(r"[\[\]\(\)\{\}\"]", "", text).strip()
    return labels.get(bare, _compact_source_phrase(bare))


def _fallback_mermaid_summaries(lines: list[str], labels: dict[str, str]) -> list[str]:
    summaries: list[str] = []
    seen: set[str] = set()
    for raw in lines[1:]:
        line = str(raw or "").strip()
        if not line or MERMAID_DIRECTIVE_RE.match(line) or _is_mermaid_start(line):
            continue
        relation = MERMAID_RELATION_RE.match(line)
        if relation:
            left = _clean_mermaid_endpoint(relation.group("left"), labels)
            right = _clean_mermaid_endpoint(relation.group("right"), labels)
            label = _compact_source_phrase(relation.group("label") or "")
            if not left or not right:
                continue
            item = f"{left} → {right}" + (f"：{label}" if label else "")
        else:
            item = _compact_source_phrase(re.sub(r"^[A-Za-z][\w-]*\s*", "", line).strip())
        if item and item not in seen:
            seen.add(item)
            summaries.append(item)
        if len(summaries) >= 8:
            break
    return summaries


def _summarize_mermaid_block(lines: list[str]) -> str:
    labels: dict[str, str] = {}
    block_text = "\n".join(str(line or "") for line in lines)
    for match in MERMAID_NODE_LABEL_RE.finditer(block_text):
        label = next((group for group in match.groups()[1:] if group), "")
        if label:
            labels[match.group(1)] = _compact_source_phrase(label)
    for raw in lines:
        participant = MERMAID_PARTICIPANT_RE.match(str(raw or "").strip())
        if participant:
            alias = participant.group(1)
            label = participant.group(2) or alias
            labels[alias] = _compact_source_phrase(label)
    normalized = MERMAID_NODE_LABEL_RE.sub(lambda match: match.group(1), block_text)
    edges = MERMAID_EDGE_RE.findall(normalized)

    summaries: list[str] = []
    seen: set[str] = set()
    for left, right in edges:
        left_label = labels.get(left, left)
        right_label = labels.get(right, right)
        if not left_label or not right_label:
            continue
        item = f"{left_label} → {right_label}"
        if item not in seen:
            seen.add(item)
            summaries.append(item)
        if len(summaries) >= 8:
            break
    for item in _fallback_mermaid_summaries(lines, labels):
        if item and item not in seen:
            seen.add(item)
            summaries.append(item)
        if len(summaries) >= 8:
            break
    if summaries:
        return f"{_mermaid_summary_label(lines[0] if lines else '')}：" + "；".join(summaries)

    node_labels = list(dict.fromkeys(label for label in labels.values() if label))[:8]
    if node_labels:
        return f"{_mermaid_summary_label(lines[0] if lines else '')}节点：" + "、".join(node_labels)
    return ""


def _source_lines_to_plain(lines: list[str]) -> list[str]:
    plain_lines: list[str] = []
    idx = 0
    while idx < len(lines):
        raw = str(lines[idx] or "").rstrip()
        if (
            not raw.strip()
            or DOCUMENT_BOUNDARY_RE.match(raw.strip())
            or _is_source_context_marker_line(raw)
            or _is_source_decoration_line(raw)
            or _is_source_production_note_line(raw)
        ):
            idx += 1
            continue
        if _is_mermaid_start(raw):
            block = [raw]
            idx += 1
            while idx < len(lines) and (
                _mermaid_continue_line(str(lines[idx] or ""))
                or _mermaid_has_unclosed_label(block)
            ):
                block.append(str(lines[idx] or "").rstrip())
                idx += 1
            summary = _summarize_mermaid_block(block)
            if summary:
                plain_lines.append(summary)
            continue
        plain = _markdown_line_to_plain(raw)
        if plain:
            plain_lines.append(plain)
        idx += 1
    return plain_lines


PAGINATED_MARKDOWN_FIELD_RE = re.compile(
    r"^-\s*(标题|内容|正文|表达意图|演讲备注|备注|视觉建议|画面建议)\s*[:：]\s*(.*)$",
    flags=re.MULTILINE,
)


_ARABIC_TO_CHINESE = {
    "1": "一", "2": "二", "3": "三", "4": "四", "5": "五",
    "6": "六", "7": "七", "8": "八", "9": "九", "10": "十",
}


def _normalize_section_title(title: str) -> str:
    """统一章节标题的序号格式为「模块X：」。支持 Part/Module/中文数字/阿拉伯数字。"""
    if not title:
        return title
    # Part/Module/Section + 数字
    m = re.search(r"^(?:Part|Module|Section)\s*(\d+)\s*[:：]?\s*(.*)$", title, re.IGNORECASE)
    if m:
        chinese = _ARABIC_TO_CHINESE.get(m.group(1), m.group(1))
        suffix = m.group(2).strip()
        return f"模块{chinese}：{suffix}" if suffix else f"模块{chinese}"
    # 中文数字前缀（一、二、三…）
    m = re.search(r"^([一二三四五六七八九十]+)(?:[、．.\s]+(.+))?$", title)
    if m:
        suffix = (m.group(2) or "").strip()
        return f"模块{m.group(1)}：{suffix}" if suffix else f"模块{m.group(1)}"
    # 阿拉伯数字前缀（1、2、3… 或 1. 2. 3.）
    m = re.search(r"^(\d+)(?:[、．.\s]+(.+))?$", title)
    if m:
        chinese = _ARABIC_TO_CHINESE.get(m.group(1), m.group(1))
        suffix = (m.group(2) or "").strip()
        return f"模块{chinese}：{suffix}" if suffix else f"模块{chinese}"
    return title


def _strip_module_prefix(title: str) -> str:
    return re.sub(r"^\s*模块\s*[:：]\s*", "", title or "").strip()


def _clean_paginated_markdown_field(lines: list[str]) -> str:
    kept: list[str] = []
    for raw in lines:
        line = str(raw or "").rstrip()
        if is_markdown_thematic_break_line(line):
            continue
        kept.append(line)
    while kept and not kept[0].strip():
        kept.pop(0)
    while kept and not kept[-1].strip():
        kept.pop()
    text = "\n".join(kept).strip()
    if not text:
        return ""
    compact = re.sub(r"\s+", "", text)
    if "无额外正文" in compact or compact in {"无正文", "标题即核心信息", "（无正文）"}:
        return ""
    return normalize_markdown_content(text)


def _paginated_markdown_page_type(label: str, page_num: int) -> str:
    text = re.sub(r"\s+", "", label or "")
    if page_num == 1 or "封面" in text:
        return "cover"
    if any(marker in text for marker in ("行动引导", "收尾", "结束", "感谢", "封底")):
        return "ending"
    if any(marker in text for marker in ("过渡", "章节")):
        return "section"
    if "数据" in text:
        return "data"
    if any(marker in text for marker in ("引用", "名人名言")):
        return "quote"
    if any(marker in text for marker in ("金句", "使命")):
        return "hero"
    return "content"


_QUOTE_PAGE_TYPE_MARKERS = {"quote", "quotation", "quote_slide", "quoteslide"}
_QUOTE_TEXT_MARKERS = ("名人名言", "引用页", "quote page", "quotation page", "肖像")


def _is_attributed_quote_page(content_json: dict, *, original_type: str | None = None) -> bool:
    raw_type = re.sub(r"\s+", "", str(original_type or "")).strip().lower()
    if raw_type in _QUOTE_PAGE_TYPE_MARKERS or "引用" in raw_type or "名人名言" in raw_type:
        return True

    text_content = content_json.get("text_content") if isinstance(content_json.get("text_content"), dict) else {}
    headline = str(text_content.get("headline") or "")
    subhead = str(text_content.get("subhead") or "")
    body = str(text_content.get("body") or "")
    supporting_text = "\n".join(
        str(content_json.get(key) or "")
        for key in ("visual_suggestion", "speaker_notes", "section_title")
    )
    if any(marker.lower() in supporting_text.lower() for marker in _QUOTE_TEXT_MARKERS):
        return True
    return False


def _auto_reclassify_page_type(content_json: dict, current_type: str, original_type: str | None = None) -> str | None:
    """根据内容特征判断是否应切换类型。只处理 content/data/hero 之间，其他类型不变。

    返回新类型（如需切换），或 None（保持当前）。
    """
    text_content = content_json.get("text_content") or {}
    body = str(text_content.get("body", "")).strip()
    if not body:
        return None

    # 结构性类型不自动重分类
    if current_type in ("cover", "ending", "section", "agenda", "toc"):
        return None

    lines = [l.strip() for l in body.split("\n") if l.strip()]
    has_table = bool(re.search(r"(?m)^\|.*\|.*\|$", body))
    bullet_lines = sum(1 for l in lines if l.startswith(("- ", "* ", "1. ", "2. ", "3. ", "4. ", "5. ")))
    total_chars = len(body)
    headline_compact = re.sub(r"\s+", "", str(text_content.get("headline") or ""))
    body_compact = ""
    if len(lines) == 1:
        body_compact = re.sub(r"\s+", "", _strip_leading_list_marker(lines[0]))

    # 数字/百分比/金额密度
    digit_chars = len(re.findall(r"[0-9]", body))
    percent_money = len(re.findall(r"%|％|元|万|亿|千|百|个|次|人", body))
    data_density = (digit_chars + percent_money) / max(total_chars, 1)

    # 独立数据指标（如 47%、1.2亿、增长率32%）
    data_indicators = len(
        re.findall(
            r"\d+[,.]?\d*\s*[％%]|\d+[,.]?\d*\s*[万亿千百十个次人]|(?:增长|下降|提升|降低|占比|份额|率)\s*\d+",
            body,
        )
    )

    should_be_data = has_table or data_density > 0.40 or data_indicators >= 3
    should_be_hero = (
        not has_table and bullet_lines == 0 and total_chars < 80 and len(lines) <= 2
    ) or (
        current_type == "hero"
        and not has_table
        and bullet_lines == 1
        and len(lines) == 1
        and total_chars < 80
        and bool(headline_compact)
        and body_compact == headline_compact
    )
    should_be_quote_hero = current_type == "hero" and _is_attributed_quote_page(content_json, original_type=original_type)

    if current_type == "content":
        if should_be_data:
            return "data"
        if should_be_hero:
            return "hero"
    elif current_type == "data":
        if not should_be_data:
            return "content"
    elif current_type == "hero":
        if should_be_data:
            return "data"
        if not should_be_hero and not should_be_quote_hero:
            return "content"

    return None


def parse_paginated_markdown_content_plan(documents: str) -> list[dict]:
    """Parse a client-provided page-by-page Markdown draft into content plan pages."""
    text = documents or ""
    if not re.search(r"(?m)^###\s+", text) or not re.search(PAGINATED_MARKDOWN_FIELD_RE, text):
        return []

    pages: list[dict] = []
    section_title = ""
    current: dict | None = None
    current_field = ""
    in_code_fence = False
    fence_marker = ""

    def flush() -> None:
        nonlocal current, current_field
        if not current:
            current_field = ""
            return
        headline = _clean_paginated_markdown_field(current.get("headline_lines") or [])
        body = _clean_paginated_markdown_field(current.get("body_lines") or [])
        notes = _clean_paginated_markdown_field(current.get("notes_lines") or [])
        visual = _clean_paginated_markdown_field(current.get("visual_lines") or [])
        label = str(current.get("label") or "").strip()
        if headline or body:
            page_num = len(pages) + 1
            pages.append({
                "page_num": page_num,
                "type": _paginated_markdown_page_type(label, page_num),
                "section_title": str(current.get("section_title") or "").strip(),
                "text_content": {
                    "headline": headline or label or f"第 {page_num} 页",
                    "subhead": "",
                    "body": body,
                },
                "speaker_notes": notes,
                "visual_suggestion": visual or notes,
                "source_refs": [ref for ref in [str(current.get("section_title") or "").strip(), label] if ref],
                "generation_status": "source_paginated_markdown",
                "source_facts": {
                    "mode": "paginated_markdown",
                    "source_section": str(current.get("section_title") or "").strip(),
                    "source_page_label": label,
                },
            })
        current = None
        current_field = ""

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        fence_match = re.match(r"^\s*(```+|~~~+)", line)
        if fence_match:
            marker = fence_match.group(1)[:3]
            if not in_code_fence:
                in_code_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_code_fence = False
                fence_marker = ""
            continue
        if in_code_fence:
            continue
        if is_markdown_thematic_break_line(line):
            continue

        page_heading = re.match(r"^###\s+(.+?)\s*$", line)
        if page_heading:
            flush()
            label = _clean_markdown_inline(page_heading.group(1))
            current = {
                "label": label,
                "section_title": section_title,
                "headline_lines": [],
                "body_lines": [],
                "notes_lines": [],
                "visual_lines": [],
            }
            current_field = ""
            continue

        section_heading = re.match(r"^##\s+(.+?)\s*$", line)
        if section_heading:
            flush()
            title = _clean_markdown_inline(section_heading.group(1))
            section_title = _strip_module_prefix(title) if title.startswith("模块") else ""
            continue

        if current is None:
            continue

        field_match = PAGINATED_MARKDOWN_FIELD_RE.match(line.strip())
        if field_match:
            label = field_match.group(1)
            value = field_match.group(2)
            if label == "标题":
                current_field = "headline_lines"
            elif label in {"内容", "正文"}:
                current_field = "body_lines"
            elif label in {"表达意图", "演讲备注", "备注"}:
                current_field = "notes_lines"
            else:
                current_field = "visual_lines"
            if value:
                current.setdefault(current_field, []).append(value)
            continue

        if current_field:
            current.setdefault(current_field, []).append(line)

    flush()
    if len(pages) < 2:
        return []
    return _normalize_content_markdown(pages)


def extract_document_outline_units(documents: str) -> list[dict]:
    """Extract source-driven units from Markdown-ish or PDF-extracted text."""
    text = sanitize_ppt_recovery_text_for_content(documents).strip()
    if not text:
        return []
    units: list[dict] = []
    stack: list[tuple[int, str]] = []
    current: dict | None = None
    body_lines: list[str] = []
    current_document_title = ""

    def flush() -> None:
        nonlocal current, body_lines
        if not current:
            body_lines = []
            return
        body = "\n".join(line for line in body_lines if line.strip()).strip()
        current["text"] = body
        current["plain_lines"] = _source_lines_to_plain(body.splitlines())
        units.append(current)
        current = None
        body_lines = []

    for raw_line in _join_wrapped_plain_source_lines(text.splitlines()):
        line = raw_line.rstrip()
        document_title = _document_boundary_title(line)
        if document_title:
            flush()
            stack = []
            current_document_title = document_title
            continue
        if _is_source_context_marker_line(line):
            continue
        heading_match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if heading_match:
            flush()
            level = len(heading_match.group(1))
            title = _clean_markdown_inline(heading_match.group(2).strip())
            stack = [(lvl, value) for lvl, value in stack if lvl < level]
            stack.append((level, title))
            current = {
                "level": level,
                "title": title,
                "path": " > ".join(value for _, value in stack),
                "text": "",
                "plain_lines": [],
            }
            continue
        plain_heading = _plain_source_heading(line)
        if plain_heading:
            flush()
            level, title = plain_heading
            stack = [(lvl, value) for lvl, value in stack if lvl < level]
            stack.append((level, title))
            current = {
                "level": level,
                "title": title,
                "path": " > ".join(value for _, value in stack),
                "text": "",
                "plain_lines": [],
            }
            continue
        if current is None and _is_source_decoration_line(line):
            continue
        if current is None and line.strip():
            title = _plain_unit_title_from_line(line, current_document_title) or current_document_title or "材料正文"
            stack = [(1, title)]
            current = {
                "level": 1,
                "title": title,
                "path": title,
                "text": "",
                "plain_lines": [],
            }
            if _clean_markdown_inline(line) == title:
                continue
        body_lines.append(line)
    flush()

    filtered: list[dict] = []
    for unit in units:
        title = str(unit.get("title") or "").strip()
        path = str(unit.get("path") or "").strip()
        lines = unit.get("plain_lines") if isinstance(unit.get("plain_lines"), list) else []
        if not title:
            continue
        if any(skip in path for skip in ("素材文件索引", "下次继续的工作")):
            continue
        if not lines and unit.get("level", 9) >= 4:
            continue
        filtered.append(unit)
    return filtered


def _chunk_plain_lines(lines: list[str], *, max_chars: int = 260, max_lines: int = 4) -> list[list[str]]:
    chunks: list[list[str]] = []
    current: list[str] = []
    current_chars = 0
    for line in lines:
        cleaned = _clean_markdown_inline(line)
        if not cleaned:
            continue
        would_exceed = current and (current_chars + len(cleaned) > max_chars or len(current) >= max_lines)
        if would_exceed:
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(cleaned)
        current_chars += len(cleaned)
    if current:
        chunks.append(current)
    return chunks


def _source_headline_candidate_is_fragment(value: str) -> bool:
    text = re.sub(r"\s+", " ", _clean_markdown_inline(_strip_leading_list_marker(value))).strip()
    if not text:
        return True
    compact = re.sub(r"\s+", "", text)
    if len(compact) <= 5:
        return True
    if re.match(r"^(?:帮我|我要|请你|请帮|去|买|订|安排)[\u4e00-\u9fffA-Za-z0-9]{0,8}$", compact):
        return True
    slash_parts = [part.strip() for part in re.split(r"\s*[/／]\s*", text) if part.strip()]
    if len(slash_parts) >= 3 and len(slash_parts[0]) <= 6:
        return True
    return False


def _source_headline_candidate_is_useful(value: str) -> bool:
    text = re.sub(r"\s+", " ", _clean_markdown_inline(_strip_leading_list_marker(value))).strip()
    if not text or len(text) > 72:
        return False
    if _source_headline_candidate_is_fragment(text):
        return False
    if re.search(r"[。！？!?：:]$", text):
        return True
    if re.search(r"(为什么|如何|怎么|什么|核心|关键|路径|机制|时代|品牌|平台|消费者|系统|行动|证据|定位)", text):
        return True
    return 8 <= len(re.sub(r"\s+", "", text)) <= 48


def _source_chunk_headline(title: str, chunk: list[str], idx: int) -> str:
    if idx <= 0:
        return title
    for line in chunk:
        candidate = _clean_markdown_inline(str(line or ""))
        if not candidate or candidate == title:
            continue
        if _source_headline_candidate_is_useful(candidate):
            return candidate
    return title


def _unit_page_specs(units: list[dict]) -> list[dict]:
    specs: list[dict] = []
    for unit in units:
        title = str(unit.get("title") or "未命名主题").strip()
        path = str(unit.get("path") or title).strip()
        lines = [str(line) for line in (unit.get("plain_lines") or []) if str(line).strip()]
        chunks = _chunk_plain_lines(lines) if lines else [[]]
        if not chunks:
            chunks = [[]]
        for idx, chunk in enumerate(chunks):
            headline = _source_chunk_headline(title, chunk, idx)
            specs.append({
                "headline": headline,
                "section_title": path,
                "lines": chunk,
                "source_path": path,
                "source_heading_level": int(unit.get("level") or 9),
                "source_chunk_index": idx,
            })
    return specs


def _with_child_context(units: list[dict]) -> list[dict]:
    enriched: list[dict] = []
    for idx, unit in enumerate(units):
        item = {**unit}
        lines = [str(line) for line in (item.get("plain_lines") or []) if str(line).strip()]
        if not lines:
            level = int(item.get("level") or 9)
            child_titles: list[str] = []
            child_lines: list[str] = []
            for child in units[idx + 1:]:
                child_level = int(child.get("level") or 9)
                if child_level <= level:
                    break
                child_title = str(child.get("title") or "").strip()
                if child_title and child_title not in child_titles:
                    child_titles.append(child_title)
                for line in child.get("plain_lines") or []:
                    if len(child_lines) >= 4:
                        break
                    if str(line).strip():
                        child_lines.append(str(line).strip())
                if len(child_titles) >= 5 and len(child_lines) >= 4:
                    break
            if child_titles:
                lines.append("本部分包括：" + "、".join(child_titles[:5]))
            lines.extend(child_lines[:4])
            item["plain_lines"] = lines
        enriched.append(item)
    return enriched


def _expand_page_specs(specs: list[dict], required_count: int) -> list[dict]:
    if required_count <= 0:
        return []
    if not specs:
        return []
    expanded = [dict(spec) for spec in specs]
    idx = 0
    while len(expanded) < required_count:
        base = specs[idx % len(specs)]
        clone = {
            **base,
            "headline": _source_chunk_headline(
                str(base.get("headline") or "主题"),
                [str(line) for line in (base.get("lines") or [])],
                len(expanded),
            ),
            "lines": list(base.get("lines") or []),
        }
        expanded.append(clone)
        idx += 1
    return expanded[:required_count]


def _split_source_specs_by_visible_points(specs: list[dict]) -> list[dict]:
    split_specs: list[dict] = []
    for spec in specs:
        points: list[str] = []
        seen: set[str] = set()
        for raw in spec.get("lines") or []:
            for point in _source_visible_bullet_candidates(str(raw or ""), max_chars=96):
                key = re.sub(r"\s+", "", point)
                if not key or key in seen or len(key) < 6:
                    continue
                seen.add(key)
                points.append(point)
        if len(points) <= 1:
            split_specs.append(dict(spec))
            continue
        for idx, point in enumerate(points):
            support_points = [point]
            if idx + 1 < len(points):
                support_points.append(points[idx + 1])
            elif idx > 0:
                support_points.append(points[idx - 1])
            split_specs.append({
                **spec,
                "headline": _source_visible_headline(point) or point,
                "lines": support_points,
                "source_chunk_index": idx,
            })
    return split_specs


def _is_compact_single_source_spec(specs: list[dict]) -> bool:
    if len(specs) != 1:
        return False
    lines = [str(line or "").strip() for line in (specs[0].get("lines") or []) if str(line or "").strip()]
    if not lines or len(lines) > 3:
        return False
    total_chars = sum(len(re.sub(r"\s+", "", line)) for line in lines)
    return total_chars <= 360


def _dedupe_ordered_text(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = re.sub(r"\s+", "", text)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(text)
    return deduped


def _merge_source_spec_group(group: list[dict]) -> dict:
    if not group:
        return {}
    if len(group) == 1:
        return dict(group[0])

    first = group[0]
    last = group[-1]
    lines: list[str] = []
    source_refs: list = []
    figure_refs: list = []
    for spec in group:
        spec_lines = [str(line) for line in (spec.get("lines") or []) if str(line).strip()]
        if not spec_lines:
            headline = str(spec.get("headline") or "").strip()
            if headline:
                spec_lines = [headline]
        lines.extend(spec_lines)
        if isinstance(spec.get("source_refs"), list):
            source_refs.extend(spec.get("source_refs") or [])
        if isinstance(spec.get("figure_refs"), list):
            figure_refs.extend(spec.get("figure_refs") or [])

    first_path = str(first.get("source_path") or first.get("section_title") or "").strip()
    last_path = str(last.get("source_path") or last.get("section_title") or "").strip()
    source_path = first_path if not last_path or last_path == first_path else f"{first_path} -> {last_path}"
    section_title = str(first.get("section_title") or "").strip()

    return {
        **first,
        "headline": str(first.get("headline") or last.get("headline") or "材料正文").strip(),
        "section_title": section_title,
        "lines": _dedupe_ordered_text(lines),
        "source_path": source_path,
        "source_refs": source_refs or first.get("source_refs") or [],
        "figure_refs": figure_refs or first.get("figure_refs") or [],
    }


def _fit_source_specs_to_count(specs: list[dict], required_count: int) -> list[dict]:
    required_count = max(0, int(required_count or 0))
    if required_count <= 0 or not specs:
        return []
    if len(specs) == required_count:
        return [dict(spec) for spec in specs]
    if len(specs) < required_count:
        if _is_compact_single_source_spec(specs):
            split_specs = _split_source_specs_by_visible_points(specs)
            if len(split_specs) > len(specs):
                if len(split_specs) >= required_count:
                    return _fit_source_specs_to_count(split_specs, required_count)
                return _expand_page_specs(split_specs, required_count)
        return _expand_page_specs(specs, required_count)

    fitted: list[dict] = []
    total = len(specs)
    for idx in range(required_count):
        start = (idx * total) // required_count
        end = ((idx + 1) * total) // required_count
        if end <= start:
            end = start + 1
        fitted.append(_merge_source_spec_group(specs[start:end]))
    return fitted


def _source_line_sentences(text: str) -> list[str]:
    value = re.sub(r"\s+", " ", _clean_markdown_inline(_strip_leading_list_marker(text))).strip()
    if not value:
        return []
    parts = re.split(r"(?<=[。！？!?])\s*", value)
    return [part.strip() for part in parts if part.strip()]


def _split_repeated_label_segments(text: str, *, max_segments: int = 4) -> list[str]:
    value = re.sub(r"\s+", " ", _clean_markdown_inline(_strip_leading_list_marker(text))).strip()
    label_match = re.match(r"^([^：:]{1,12}[：:])\s*(.+)$", value)
    if not label_match:
        return []
    label = label_match.group(1)
    if value.count(label) < 2:
        return []
    parts = [
        part.strip(" ，,；;")
        for part in re.split(rf"\s*{re.escape(label)}\s*", value)
        if part.strip(" ，,；;")
    ]
    segments = [f"{label}{part}" for part in parts if part]
    return segments[:max_segments]


def _source_visible_headline(text: str, *, max_chars: int = 42) -> str:
    value = re.sub(r"\s+", " ", _clean_markdown_inline(_strip_leading_list_marker(text))).strip()
    if not value:
        return ""
    repeated_label_segments = _split_repeated_label_segments(value, max_segments=1)
    if repeated_label_segments and len(repeated_label_segments[0]) <= max_chars:
        return repeated_label_segments[0]
    if len(value) <= max_chars:
        return value
    slash_parts = [part.strip() for part in re.split(r"\s*[/／]\s*", value) if part.strip()]
    if len(slash_parts) >= 2:
        candidate = f"{slash_parts[0]}：{slash_parts[1]}"
        if len(candidate) <= max_chars:
            return candidate
    sentences = _source_line_sentences(value)
    if sentences and len(sentences[0]) <= max_chars:
        return sentences[0]
    return _compact_source_phrase(value, limit=max_chars)


def _source_visible_bullet_candidates(text: str, *, max_chars: int = 76) -> list[str]:
    value = re.sub(r"\s+", " ", _clean_markdown_inline(_strip_leading_list_marker(text))).strip()
    if not value:
        return []

    repeated_label_segments = _split_repeated_label_segments(value)
    if repeated_label_segments:
        return [_compact_source_phrase(item, limit=max_chars) for item in repeated_label_segments]

    label_match = re.match(r"^([^：:]{1,18}[：:])\s*(.+)$", value)
    if label_match:
        label, rest = label_match.groups()
        rest_candidates = _source_visible_bullet_candidates(rest, max_chars=max(28, max_chars - len(label)))
        if len(rest_candidates) >= 2:
            return [f"{label}{rest_candidates[0]}", *rest_candidates[1:3]]
        if len(rest) <= max_chars - len(label):
            return [f"{label}{rest}"]
        return [f"{label}{_compact_source_phrase(rest, limit=max_chars - len(label))}"]

    slash_match = re.match(r"^([^/／]{1,18}\s*[/／]\s*[^/／]{1,28})", value)
    if slash_match:
        return [_compact_source_phrase(slash_match.group(1), limit=max_chars)]

    sentences = _source_line_sentences(value)
    if len(sentences) >= 2:
        return [_compact_source_phrase(sentence, limit=max_chars) for sentence in sentences if sentence][:3]
    if sentences and len(sentences[0]) <= max_chars:
        value = sentences[0]

    clauses = [
        clause.strip(" ，,；;")
        for clause in re.split(r"[，,；;]", value)
        if clause.strip(" ，,；;")
    ]
    clause_candidates: list[str] = []
    current = ""
    for clause in clauses:
        if not current:
            current = clause
            continue
        merged = f"{current}，{clause}"
        if len(merged) <= max_chars:
            current = merged
        else:
            clause_candidates.append(current)
            current = clause
    if current:
        clause_candidates.append(current)
    useful_clauses = [item for item in clause_candidates if len(re.sub(r"\s+", "", item)) >= 8]
    if len(useful_clauses) >= 2:
        return [_compact_source_phrase(item, limit=max_chars) for item in useful_clauses[:3]]
    if useful_clauses:
        return [_compact_source_phrase(useful_clauses[0], limit=max_chars)]
    if len(value) <= max_chars:
        return [value]
    return [_compact_source_phrase(value, limit=max_chars)]


def _source_visible_bullet(text: str, *, max_chars: int = 76) -> str:
    candidates = _source_visible_bullet_candidates(text, max_chars=max_chars)
    return candidates[0] if candidates else ""


def _source_visible_body_lines(lines: list[str], *, headline: str, max_lines: int = 4) -> list[str]:
    visible: list[str] = []
    seen: set[str] = set()
    headline_key = re.sub(r"\s+", "", _clean_markdown_inline(headline))
    for raw in lines:
        cleaned = re.sub(r"\s+", " ", _clean_markdown_inline(_strip_leading_list_marker(str(raw or "")))).strip()
        if not cleaned:
            continue
        key = re.sub(r"\s+", "", cleaned)
        if key == headline_key:
            continue
        for bullet in _source_visible_bullet_candidates(cleaned):
            bullet_key = re.sub(r"\s+", "", bullet)
            if not bullet or bullet_key in seen:
                continue
            seen.add(bullet_key)
            visible.append(bullet)
            if len(visible) >= max_lines:
                break
        if len(visible) >= max_lines:
            break
    if visible:
        return visible
    fallback = _source_visible_bullet(headline, max_chars=56)
    return [fallback] if fallback else []


def _source_speaker_notes_from_lines(*, headline: str, lines: list[str], is_ending: bool = False) -> str:
    detail_lines = _dedupe_ordered_text([
        re.sub(r"\s+", " ", _clean_markdown_inline(_strip_leading_list_marker(str(line or "")))).strip()
        for line in lines
    ])[:8]
    if not detail_lines:
        return (
            "讲稿内容：回到本页结论，给听众一个明确的行动或判断。"
            if is_ending
            else "讲稿内容：围绕本页标题展开真实论点、例子或证据，再用一句话连接到下一页。"
        )

    main_point = detail_lines[0]
    support_lines = [line for line in detail_lines[1:4] if line]
    if is_ending:
        notes = [
            "讲稿内容：",
            f"- {main_point}",
        ]
        for line in support_lines[:2]:
            notes.append(f"- {line}")
        for line in detail_lines[3:8]:
            notes.append(f"- {line}")
        notes.append(f"收束提示：把「{headline}」落到最后一个可执行判断，不再引入新论点。")
        return "\n".join(notes)

    notes = [
        "讲稿内容：",
        f"- {main_point}",
    ]
    if support_lines:
        notes.append(f"- {support_lines[0]}")
    if len(support_lines) >= 2:
        notes.append(f"- {support_lines[1]}")
    if len(support_lines) >= 3:
        notes.append(f"- {support_lines[2]}")
    else:
        notes.append(f"转场提示：把「{headline}」带到下一页的证据、机制或行动。")
    for line in detail_lines[4:8]:
        notes.append(f"- {line}")
    return "\n".join(notes)


def _join_short_wrapped_title(first: str, second: str) -> str:
    first_clean = _clean_markdown_inline(first)
    second_clean = _clean_markdown_inline(second)
    if not first_clean:
        return second_clean
    if not second_clean:
        return first_clean
    if len(first_clean) <= 38 and len(second_clean) <= 24:
        return (first_clean + second_clean).strip()
    return first_clean


def _looks_like_source_heading_line(text: str) -> bool:
    value = _clean_markdown_inline(text)
    if not value or len(value) > 52:
        return False
    compact = re.sub(r"\s+", "", value)
    if compact in {"绪论", "序论", "导论", "引言", "前言", "结语", "阅读指南"}:
        return True
    if value.startswith("——"):
        return False
    if re.search(r"[。；;]$", value):
        return False
    if re.match(r"^第\s*[0-9一二三四五六七八九十百]+\s*[章节部篇讲课]", value):
        return True
    if re.match(r"^[一二三四五六七八九十百]+[、.．]\s*.+", value):
        return True
    return bool(re.search(r"(使命|愿景|真谛|关键因素|指南|结语|如何|为什么|北极星|付诸实践|心智模式)", value))


def _source_path_leaf(value: str) -> str:
    parts = [part.strip() for part in str(value or "").split(">") if part.strip()]
    return parts[-1] if parts else str(value or "").strip()


def _looks_like_numbered_section_entry_title(text: str) -> bool:
    value = re.sub(r"\s+", " ", _clean_markdown_inline(str(text or ""))).strip()
    if not value or len(value) > 72:
        return False
    return bool(
        re.match(r"^第\s*[0-9一二三四五六七八九十百]+\s*(?:章|章节|部|部分|篇|讲|课)\s*[:：].+", value)
        or re.match(r"^模块\s*[0-9一二三四五六七八九十百]+\s*[:：].+", value)
        or re.match(r"^(?:part|chapter)\s*0?\d{1,3}\s*[:：-]\s*.+", value, flags=re.IGNORECASE)
    )


def _source_spec_should_be_section(spec: dict, headline: str) -> bool:
    candidates = [
        headline,
        str(spec.get("headline") or ""),
        _source_path_leaf(str(spec.get("section_title") or spec.get("source_path") or "")),
    ]
    return any(_looks_like_numbered_section_entry_title(candidate) for candidate in candidates)


def _source_page_title_and_body(lines: list[str], chapter_title: str = "") -> tuple[str, list[str]]:
    clean_lines = [_clean_markdown_inline(line) for line in lines if _clean_markdown_inline(line)]
    if not clean_lines:
        return _clean_markdown_inline(chapter_title), []

    first = clean_lines[0]
    second = clean_lines[1] if len(clean_lines) > 1 else ""
    compact_first = re.sub(r"\s+", "", first)
    if compact_first in {"绪论", "序论", "导论", "引言", "前言"} and second:
        return f"{compact_first}：{second}", clean_lines[2:] or clean_lines
    if re.match(r"^第\s*[0-9一二三四五六七八九十百]+\s*[章节部篇讲课]", first) and second:
        return _join_short_wrapped_title(first, second), clean_lines[2:] or clean_lines
    for idx, line in enumerate(clean_lines[:20]):
        if line in {"结语", "阅读指南"}:
            return line, clean_lines[idx + 1:] or clean_lines
    for idx, line in enumerate(clean_lines[:12]):
        if _looks_like_source_heading_line(line):
            if line in {"绪论", "序论", "导论", "引言", "前言"} and idx + 1 < len(clean_lines):
                return f"{line}：{clean_lines[idx + 1]}", clean_lines[idx + 2:] or clean_lines
            return line, clean_lines[idx + 1:] or clean_lines
    if len(first) <= 42 and not first.endswith(("。", "，", "；", "、", ",")):
        return first, clean_lines[1:] or clean_lines
    if chapter_title:
        return _clean_markdown_inline(chapter_title), clean_lines
    return _compact_source_phrase(first, limit=42), clean_lines


def _source_context_figure_refs_for_page(page: dict, figures_by_page: dict[tuple[str, int], list[dict]]) -> list[dict]:
    source_document = str(page.get("source_document") or "").strip()
    try:
        source_page_num = int(page.get("page_num") or 0)
    except (TypeError, ValueError):
        source_page_num = 0
    refs: list[dict] = []
    for figure in figures_by_page.get((source_document, source_page_num), []):
        figure_id = str(figure.get("figure_id") or "").strip()
        if not _is_valid_figure_id(figure_id):
            continue
        if not _figure_is_content_bearing(figure):
            continue
        refs.append({
            "source_document": source_document,
            "source_page_num": source_page_num,
            "source_type": str(figure.get("source_type") or page.get("source_type") or "pdf"),
            "figure_id": figure_id,
            "reason": str(figure.get("nearby_text") or "source_figure").strip(),
        })
    return refs[:3]


def _source_context_page_specs(documents: str) -> list[dict]:
    pages = [
        page
        for page in _extract_source_context_pages(documents)
        if _canonical_page_source_kind(str(page.get("source_type") or "")) in PAGE_SOURCE_KINDS
    ]
    if not pages:
        return []
    figures_by_page = _source_context_figures_by_page(documents)
    specs: list[dict] = []
    for page in pages:
        plain_lines = _source_lines_to_plain(str(page.get("text") or "").splitlines())
        if not plain_lines:
            continue
        chapter_title = str(page.get("chapter") or "").strip()
        headline, body_lines = _source_page_title_and_body(plain_lines, chapter_title)
        section_title = _normalize_section_title(chapter_title or headline)
        chunks = _chunk_plain_lines(body_lines or plain_lines, max_chars=360, max_lines=5)
        lines = chunks[0] if chunks else (body_lines or plain_lines)[:5]
        source_document = str(page.get("source_document") or "").strip()
        try:
            source_page_num = int(page.get("page_num") or 0)
        except (TypeError, ValueError):
            source_page_num = 0
        source_type = str(page.get("source_type") or _source_kind_from_document(source_document, default="pdf"))
        source_ref = {
            "source_document": source_document,
            "source_page_num": source_page_num,
            "source_type": "pptx_slide" if source_type == "pptx" else source_type,
            "reason": headline,
        }
        page_figure_refs = _source_context_figure_refs_for_page(page, figures_by_page)
        specs.append({
            "headline": headline,
            "section_title": section_title,
            "lines": lines,
            "source_path": f"{section_title} / P{source_page_num}" if section_title else f"P{source_page_num}",
            "source_refs": [source_ref],
            "figure_refs": page_figure_refs,
        })
    return specs


def _agenda_lines_from_specs(specs: list[dict], *, limit: int = 8) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for spec in specs:
        for value in (spec.get("section_title"), spec.get("headline")):
            text = _clean_markdown_inline(str(value or ""))
            key = re.sub(r"\s+", "", text)
            is_structural_label = bool(
                re.search(r"^(?:绪论|序论|导论|引言|前言|结语|阅读指南)$", text)
                or re.search(r"^第\s*[0-9一二三四五六七八九十百]+\s*(?:[章节篇]|部(?!分))", text)
            )
            is_named_section = is_structural_label or bool(
                len(text) <= 36
                and not re.search(r"[，。；;]", text)
                and re.search(r"绪论|序论|导论|引言|前言|使命|愿景|真谛|关键因素|指南|北极星|付诸实践|心智模式", text)
            )
            if not is_named_section:
                continue
            if not text or key in seen:
                continue
            seen.add(key)
            lines.append(text)
            break
        if len(lines) >= limit:
            break
    return lines


def _should_include_toc_page(*, target_count: int, agenda_lines: list[str]) -> bool:
    if int(target_count or 0) <= 6:
        return False
    distinct_lines = {
        re.sub(r"\s+", "", str(line or ""))
        for line in agenda_lines
        if str(line or "").strip()
    }
    return len(distinct_lines) >= 2


def _source_body_spec_count(*, target_count: int, include_toc: bool) -> int:
    target_count = max(1, int(target_count or 1))
    if target_count <= 1:
        return 0
    non_body_pages = 3 if include_toc else 2
    return max(1, target_count - non_body_pages)


def build_document_driven_long_deck_draft(
    *,
    topic: str,
    documents: str,
    target_count: int,
    min_pages: int,
    max_pages: int,
    deck_blueprint: str = "",
) -> list[dict]:
    documents = sanitize_ppt_recovery_text_for_content(documents)
    units = extract_document_outline_units(documents)
    context_specs = _source_context_page_specs(documents)
    content_units = [unit for unit in units if str(unit.get("title") or "").strip() != "用户上传材料"]
    units_for_draft = _with_child_context(content_units or units)
    if not units and not context_specs:
        return build_long_deck_skeleton(
            topic=topic,
            target_count=target_count,
            min_pages=min_pages,
            max_pages=max_pages,
            deck_blueprint=deck_blueprint,
        )

    target_count = max(1, int(target_count or max_pages or min_pages or 1))
    if context_specs:
        title = _source_context_primary_title(documents, topic)
        top_sections = _agenda_lines_from_specs(context_specs)
        include_toc = _should_include_toc_page(target_count=target_count, agenda_lines=top_sections)
        specs = _fit_source_specs_to_count(
            context_specs,
            _source_body_spec_count(target_count=target_count, include_toc=include_toc),
        )
        ending_spec = context_specs[-1] if context_specs else None
    else:
        root_title = ""
        for unit in units_for_draft:
            if int(unit.get("level") or 9) <= 1 and str(unit.get("title") or "").strip() != "用户上传材料":
                root_title = str(unit.get("title") or "").strip()
                break
        title = root_title or _brief_title(topic)
        top_sections = [
            str(unit.get("title") or "").strip()
            for unit in units_for_draft
            if int(unit.get("level") or 9) <= 2 and str(unit.get("title") or "").strip() != title
        ][:8]
        source_units = [unit for unit in units_for_draft if str(unit.get("title") or "").strip() != title]
        include_toc = _should_include_toc_page(target_count=target_count, agenda_lines=top_sections)
        specs = _fit_source_specs_to_count(
            _unit_page_specs(source_units or units_for_draft),
            _source_body_spec_count(target_count=target_count, include_toc=include_toc),
        )
        ending_spec = None

    pages: list[dict] = [
        {
            "page_num": 1,
            "type": "cover",
            "section_title": "封面",
            "text_content": {
                "headline": title,
                "subhead": "",
                "body": "",
            },
            "speaker_notes": "封面页。主题来自源文档，后续页面按原文结构展开。",
            "visual_suggestion": "封面应突出源文档中的主题、品牌或核心对象，视觉方向以源文档为准。",
            "source_refs": [title],
            "generation_status": "source_draft",
        }
    ]

    agenda_lines = top_sections or [str(spec.get("headline") or "") for spec in specs[:6]]
    content_start_page = 2
    if include_toc:
        pages.append({
            "page_num": 2,
            "type": "toc",
            "section_title": "目录",
            "text_content": {
                "headline": "目录",
                "subhead": "",
                "body": "\n".join(f"- {line}" for line in agenda_lines if line),
            },
            "speaker_notes": "先把整份内容的主线和展开顺序讲清楚，帮助听众理解后续结构。",
            "visual_suggestion": "目录页，用清晰短标题呈现章节顺序；可采用列表、分栏、矩阵或简洁路径。",
            "source_refs": agenda_lines,
            "generation_status": "source_draft",
        })
        content_start_page = 3

    for page_num in range(content_start_page, target_count + 1):
        spec_index = page_num - content_start_page
        spec = (
            ending_spec
            if page_num == target_count and ending_spec
            else specs[spec_index] if spec_index < len(specs) else specs[-1]
        )
        lines = [line for line in (spec.get("lines") or []) if line]
        if not lines:
            lines = [f"围绕「{spec.get('headline') or '本页主题'}」展开内容。"]
        body_lines = lines[:8]
        if page_num == target_count:
            page_type = "ending"
            raw_headline = str(spec.get("headline") or "最后一页").strip()
            headline = _source_visible_headline(raw_headline) or raw_headline
            section_title = _normalize_section_title(str(spec.get("section_title") or "结尾").strip())
            screen_lines = _source_visible_body_lines(body_lines, headline=headline, max_lines=4)
        else:
            raw_headline = str(spec.get("headline") or f"第 {page_num} 页").strip()
            headline = _source_visible_headline(raw_headline) or raw_headline
            page_type = (
                "section"
                if _source_spec_should_be_section(spec, headline) or page_num in {content_start_page, 12, 23, 34, 45, 56, 67}
                else "content"
            )
            section_title = _normalize_section_title(str(spec.get("section_title") or "内容规划").strip())
            screen_lines = _source_visible_body_lines(body_lines, headline=headline, max_lines=3 if page_type == "section" else 4)
            if len(screen_lines) < 2:
                headline_key = re.sub(r"\s+", "", headline)
                body_keys = {re.sub(r"\s+", "", line) for line in screen_lines}
                if headline_key and headline_key not in body_keys:
                    screen_lines = [headline, *screen_lines]
            if page_type == "content" and len(screen_lines) < 2:
                page_type = "hero"
        body = "\n".join(f"- {line}" for line in screen_lines)
        speaker_notes = _source_speaker_notes_from_lines(
            headline=headline,
            lines=body_lines,
            is_ending=page_num == target_count,
        )
        pages.append({
            "page_num": page_num,
            "type": page_type,
            "section_title": section_title,
            "text_content": {
                "headline": headline,
                "subhead": "",
                "body": body,
            },
            "speaker_notes": speaker_notes,
            "visual_suggestion": "根据本页是数据、框架、案例还是行动清单，选择图表、对比表、流程图或案例卡片。",
            "source_refs": spec.get("source_refs") if isinstance(spec.get("source_refs"), list) else [str(spec.get("source_path") or "")],
            "figure_refs": spec.get("figure_refs") if isinstance(spec.get("figure_refs"), list) else [],
            "generation_status": "source_draft",
        })
    if deck_blueprint:
        for page in pages:
            page["deck_blueprint_ref"] = deck_blueprint[:1000]
    return pages[:target_count]


def _outline_to_page_map(outline: list[dict], *, status: str = "page_map_source") -> list[dict]:
    page_map: list[dict] = []
    for idx, page in enumerate(outline, start=1):
        if not isinstance(page, dict):
            continue
        source_status = str(page.get("generation_status") or "").strip()
        page_status = source_status if source_status in LOW_CONTENT_DRAFT_STATUSES else status
        bullet_limit = _page_map_bullet_limit(page_status)
        text_content = page.get("text_content") if isinstance(page.get("text_content"), dict) else {}
        body = str(text_content.get("body") or "").strip()
        bullets = [
            _strip_leading_list_marker(line)
            for line in body.splitlines()
            if str(line).strip()
        ]
        bullets = [line for line in bullets if line]
        page_map.append({
            "page_num": int(page.get("page_num") or idx),
            "type": str(page.get("type") or "content").strip() or "content",
            "section_title": _normalize_section_title(str(page.get("section_title") or "").strip()),
            "headline": str(text_content.get("headline") or "").strip() or f"第 {idx} 页",
            "subhead": str(text_content.get("subhead") or "").strip(),
            "bullets": bullets[:bullet_limit],
            "speaker_notes": str(page.get("speaker_notes") or "").strip(),
            "visual_suggestion": str(page.get("visual_suggestion") or "").strip(),
            "source_refs": page.get("source_refs") if isinstance(page.get("source_refs"), list) else [],
            "figure_refs": page.get("figure_refs") if isinstance(page.get("figure_refs"), list) else [],
            "generation_status": page_status,
        })
    return _normalize_page_map(page_map)


def _source_draft_page_map(
    *,
    topic: str,
    documents: str,
    target_count: int,
    min_pages: int,
    max_pages: int,
    intent_contract: dict | None = None,
) -> list[dict]:
    documents = sanitize_ppt_recovery_text_for_content(documents)
    preserve_outline = build_ppt_page_preserve_source_draft(
        documents,
        topic,
        intent_contract=intent_contract,
    )
    if preserve_outline:
        return _outline_to_page_map(preserve_outline, status="page_map_source")

    source_outline = build_document_driven_long_deck_draft(
        topic=topic,
        documents=documents,
        target_count=target_count,
        min_pages=min_pages,
        max_pages=max_pages,
    )
    source_page_map = _outline_to_page_map(source_outline, status="page_map_source")
    if _page_map_has_skeleton_placeholders(source_page_map):
        return []
    return source_page_map


def render_page_map_markdown(page_map: list[dict]) -> str:
    lines: list[str] = []
    for page in page_map:
        if not isinstance(page, dict):
            continue
        page_num = int(page.get("page_num") or len(lines) + 1)
        page_type = str(page.get("type") or "content").strip() or "content"
        section = str(page.get("section_title") or "").strip() or "内容"
        headline = str(page.get("headline") or "").strip() or f"第 {page_num} 页"
        lines.append(f"P{page_num}｜{page_type}｜{section}｜{headline}")
        subhead = str(page.get("subhead") or "").strip()
        if subhead:
            lines.append(f"副标题：{subhead}")
        bullets = page.get("bullets") if isinstance(page.get("bullets"), list) else []
        for bullet in bullets[:5]:
            text = str(bullet or "").strip()
            if text:
                lines.append(f"- {text}")
        notes = str(page.get("speaker_notes") or "").strip()
        if notes:
            lines.append(f"备注：{notes}")
        visual = str(page.get("visual_suggestion") or "").strip()
        if visual:
            lines.append(f"视觉：{visual}")
        refs = page.get("source_refs") if isinstance(page.get("source_refs"), list) else []
        if refs:
            lines.append("来源：" + "；".join(str(ref) for ref in refs[:4] if str(ref).strip()))
        figure_refs = page.get("figure_refs") if isinstance(page.get("figure_refs"), list) else []
        if figure_refs:
            lines.append("配图：" + "；".join(str(ref) for ref in figure_refs[:4] if str(ref).strip()))
        lines.append("")
    return "\n".join(lines).strip()


def parse_page_map_markdown(markdown: str) -> list[dict]:
    pages: list[dict] = []
    current: dict | None = None

    def flush() -> None:
        nonlocal current
        if not current:
            return
        current.setdefault("bullets", [])
        pages.append(current)
        current = None

    header_re = re.compile(
        r"^\s*P\s*(\d{1,3})\s*[|｜]\s*([^|｜\n]+)\s*[|｜]\s*([^|｜\n]*)\s*[|｜]\s*(.+?)\s*$",
        flags=re.IGNORECASE,
    )
    for raw_line in (markdown or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = header_re.match(line)
        if match:
            flush()
            current = {
                "page_num": int(match.group(1)),
                "type": _clean_markdown_inline(match.group(2)) or "content",
                "section_title": _clean_markdown_inline(match.group(3)),
                "headline": _clean_markdown_inline(match.group(4)),
                "subhead": "",
                "bullets": [],
                "speaker_notes": "",
                "visual_suggestion": "",
                "source_refs": [],
                "figure_refs": [],
                "generation_status": "page_map_model",
            }
            continue
        if current is None:
            continue
        label_line = re.sub(r"^[-*+]\s+", "", line)
        label_match = re.match(
            r"^(副标题|subhead|subtitle|章节标题|section_title|备注|演讲备注|speaker_notes?|notes?|视觉|visual|visual_suggestion|来源|source_refs?|sources?|配图|图片|figure_refs?|figures?|image_refs?)\s*[:：]\s*(.*)$",
            label_line,
            flags=re.IGNORECASE,
        )
        if label_match:
            label = label_match.group(1).lower()
            value = _clean_markdown_inline(label_match.group(2))
            if label in {"副标题", "subhead", "subtitle"}:
                current["subhead"] = value
            elif label in {"章节标题", "section_title"}:
                if value and not str(current.get("section_title") or "").strip():
                    current["section_title"] = value
            elif label in {"备注", "演讲备注", "speaker_notes", "speaker_note", "note", "notes"}:
                existing = str(current.get("speaker_notes") or "").strip()
                current["speaker_notes"] = f"{existing}\n{value}".strip() if existing else value
            elif label in {"视觉", "visual", "visual_suggestion"}:
                current["visual_suggestion"] = value
            elif label in {"配图", "图片", "figure_ref", "figure_refs", "figure", "figures", "image_ref", "image_refs"}:
                current["figure_refs"] = [part.strip() for part in re.split(r"[；;]", value) if part.strip()]
            else:
                current["source_refs"] = [part.strip() for part in re.split(r"[；;]", value) if part.strip()]
            continue
        if re.match(r"^[-*+]\s+", line):
            bullet = _clean_markdown_inline(re.sub(r"^[-*+]\s+", "", line))
            if bullet:
                current.setdefault("bullets", []).append(bullet)
            continue
        cleaned = _clean_markdown_inline(line)
        if cleaned:
            current.setdefault("bullets", []).append(cleaned)
    flush()
    return _normalize_page_map(pages)


def _page_map_bullet_limit(status: str) -> int:
    status_value = str(status or "").strip()
    if (
        status_value == "page_map_source"
        or status_value.endswith("_direct")
        or status_value.endswith("_preserve_source")
        or "source_body" in status_value
    ):
        return 32
    return 5


_TOC_PAGE_TITLE_RE = re.compile(
    r"(目录|议程|大纲|内容地图|课程地图|课程总览|内容总览|课程全景|整体结构|全局结构|本次.*展开|怎么展开|agenda|contents|outline)",
    flags=re.IGNORECASE,
)
_TOC_PAGE_ITEM_RE = re.compile(
    r"(^|\s)(模块|章节|章|节|部分|part|chapter|module)\s*[一二三四五六七八九十\d]*|"
    r"(^|\s)P\s*\d+|"
    r"^[一二三四五六七八九十\d]{1,3}[、.)．]",
    flags=re.IGNORECASE,
)


def _body_lines_for_type_inference(value) -> list[str]:
    if isinstance(value, list):
        candidates = [str(item or "") for item in value]
    else:
        candidates = str(value or "").splitlines()
    lines: list[str] = []
    for item in candidates:
        line = _strip_leading_list_marker(str(item or "")).strip()
        if line:
            lines.append(line)
    return lines


def _should_reclassify_content_page_as_toc(
    *,
    page_type: str,
    section_title: str,
    headline: str,
    body_lines: list[str],
) -> bool:
    if str(page_type or "").strip().lower() != "content":
        return False
    title_text = f"{section_title} {headline}".strip()
    if not _TOC_PAGE_TITLE_RE.search(title_text):
        return False
    if len(body_lines) < 3:
        return False
    structured_items = sum(1 for line in body_lines if _TOC_PAGE_ITEM_RE.search(line))
    return structured_items >= 2 or len(body_lines) >= 4


def _should_reclassify_source_page_map_as_hero(
    *,
    page_type: str,
    generation_status: str,
    headline: str,
    subhead: str,
    bullets: list[str],
) -> bool:
    if page_type != "content" or generation_status != "page_map_source":
        return False
    if subhead.strip() or len(bullets) != 1:
        return False
    bullet = _clean_visible_page_map_text(_strip_leading_list_marker(str(bullets[0] or "")))
    headline_text = _clean_visible_page_map_text(str(headline or ""))
    compact_bullet = re.sub(r"\s+", "", bullet)
    compact_headline = re.sub(r"\s+", "", headline_text)
    if not compact_bullet or len(compact_bullet) > 40:
        return False
    return bool(compact_headline and compact_headline == compact_bullet)


def _normalize_page_map(page_map: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for page in sorted([p for p in page_map if isinstance(p, dict)], key=lambda item: int(item.get("page_num") or 10**6)):
        page_num = len(normalized) + 1
        page_type = _canonical_content_plan_type(str(page.get("type") or "content").strip().lower() or "content")
        generation_status = str(page.get("generation_status") or "page_map_model")
        bullet_limit = _page_map_bullet_limit(generation_status)
        raw_bullets = page.get("bullets") if isinstance(page.get("bullets"), list) else []
        bullets = [
            _clean_visible_page_map_text(str(item))
            for item in raw_bullets
            if _clean_visible_page_map_text(str(item)) and not _is_page_map_format_placeholder(str(item))
        ]
        section_title = _normalize_section_title(_clean_visible_page_map_text(str(page.get("section_title") or "")))
        headline = _clean_visible_page_map_text(str(page.get("headline") or ""))
        subhead = _clean_visible_page_map_text(str(page.get("subhead") or ""))
        speaker_notes = _clean_visible_page_map_text(str(page.get("speaker_notes") or ""))
        visual_suggestion = _clean_visible_page_map_text(str(page.get("visual_suggestion") or ""))
        if _should_reclassify_content_page_as_toc(
            page_type=page_type,
            section_title=section_title,
            headline=headline,
            body_lines=bullets,
        ):
            page_type = "toc"
        elif _should_reclassify_source_page_map_as_hero(
            page_type=page_type,
            generation_status=generation_status,
            headline=headline,
            subhead=subhead,
            bullets=bullets,
        ):
            page_type = "hero"
        normalized.append({
            "page_num": page_num,
            "type": page_type,
            "section_title": section_title,
            "headline": headline or f"第 {page_num} 页",
            "subhead": subhead,
            "bullets": bullets[:bullet_limit],
            "speaker_notes": speaker_notes,
            "visual_suggestion": visual_suggestion,
            "source_refs": _clean_page_map_source_ref_values(page.get("source_refs")),
            "figure_refs": page.get("figure_refs") if isinstance(page.get("figure_refs"), list) else [],
            "generation_status": generation_status,
        })
    return normalized


def _page_map_requires_body_bullets(page: dict) -> bool:
    page_type = str(page.get("type") or "content").strip().lower() or "content"
    return page_type in {"agenda", "toc", "content", "data"}


def _page_map_is_skeleton_placeholder(page: dict) -> bool:
    if not isinstance(page, dict):
        return False
    status = str(page.get("generation_status") or "")
    if status in LOW_CONTENT_DRAFT_STATUSES:
        return True
    if status != "page_map_source":
        return False
    bullets = page.get("bullets") if isinstance(page.get("bullets"), list) else []
    text = "\n".join(
        str(part or "")
        for part in [
            page.get("headline"),
            page.get("subhead"),
            "\n".join(str(item or "") for item in bullets),
            page.get("speaker_notes"),
            page.get("visual_suggestion"),
        ]
    )
    return any(marker in text for marker in SKELETON_PLACEHOLDER_MARKERS)


def _page_map_has_skeleton_placeholders(page_map: list[dict]) -> bool:
    return any(_page_map_is_skeleton_placeholder(page) for page in page_map or [])


def _is_page_map_format_placeholder(value: str) -> bool:
    raw = str(value or "")
    stripped = _strip_source_context_markers(raw)
    if _is_source_context_marker_line(raw) or (raw.strip() and not stripped.strip()):
        return True
    text = _clean_markdown_inline(value).strip()
    if not text:
        return False
    compact = re.sub(r"\s+", "", text).lower()
    if compact in {item.lower() for item in PAGE_MAP_FORMAT_PLACEHOLDERS}:
        return True
    if re.fullmatch(r"(?:bullet|要点|内容|正文|标题|title|point|item)[\d一二三四五六七八九十]*", compact):
        return True
    if compact and all(ch in ".。…·•-_*#—–" for ch in compact):
        return True
    return False


def _page_map_has_format_placeholders(page: dict) -> bool:
    if not isinstance(page, dict):
        return False
    bullets = page.get("bullets") if isinstance(page.get("bullets"), list) else []
    values = [
        str(page.get("headline") or ""),
        str(page.get("subhead") or ""),
        str(page.get("speaker_notes") or ""),
        str(page.get("visual_suggestion") or ""),
        *[str(item or "") for item in bullets],
    ]
    return any(_is_page_map_format_placeholder(value) for value in values)


_INLINE_PAGE_MARKER_RE = re.compile(r"(?:^|\s)P\s*\d{1,3}\s*[|｜]")


def _page_map_has_inline_page_markers(page: dict) -> bool:
    if not isinstance(page, dict):
        return False
    bullets = page.get("bullets") if isinstance(page.get("bullets"), list) else []
    values = [
        str(page.get("headline") or ""),
        str(page.get("subhead") or ""),
        str(page.get("speaker_notes") or ""),
        str(page.get("visual_suggestion") or ""),
        *[str(item or "") for item in bullets],
    ]
    return any(_INLINE_PAGE_MARKER_RE.search(value) for value in values)


_GENERIC_REPEATABLE_HEADLINES = {
    "目录",
    "内容地图",
    "结语",
    "谢谢",
    "qanda",
    "qa",
}


def _duplicate_page_map_headlines(page_map: list[dict]) -> list[str]:
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for page in page_map or []:
        if not isinstance(page, dict):
            continue
        page_type = str(page.get("type") or "content").strip().lower()
        if page_type in {"cover", "toc", "agenda", "ending"}:
            continue
        headline = _clean_visible_page_map_text(str(page.get("headline") or ""))
        compact = _compact_coverage_text(headline)
        if len(compact) < 8 or compact in _GENERIC_REPEATABLE_HEADLINES:
            continue
        if compact in seen and headline not in duplicates:
            duplicates.append(headline)
        else:
            seen[compact] = headline
    return duplicates


_PAGE_MAP_GENERIC_SPEAKER_NOTE_PATTERNS = (
    r"^这一页口头展开[:：]?",
    r"^占位备注[。.]?$",
    r"^模型输出备注$",
)

_SPEAKER_NOTE_PROCEDURAL_PHRASE_RE = re.compile(
    r"(讲稿内容|讲述提示|讲法|表达节奏|停顿|转场提示|转场|收束提示|收束|"
    r"先|再|然后|最后|自然|引出|带到|转到|转向|回到|回扣|"
    r"抛出|提出|补充|强调|压实|讲清楚|讲师|听众|"
    r"这一页|本页|上页|上一页|下页|下一页|判断|反问|节奏|开场|结尾|展开|"
    r"证据、机制或行动|证据机制或行动|明确的下一步|不再引入新论点)"
)


def _page_map_has_generic_speaker_notes(page: dict) -> bool:
    if not isinstance(page, dict) or not _page_map_requires_body_bullets(page):
        return False
    text = re.sub(r"\s+", "", str(page.get("speaker_notes") or "").strip())
    if not text:
        return False
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _PAGE_MAP_GENERIC_SPEAKER_NOTE_PATTERNS)


def _page_map_speaker_notes_repeat_body(page: dict) -> bool:
    if not isinstance(page, dict) or not _page_map_requires_body_bullets(page):
        return False
    speaker_notes = str(page.get("speaker_notes") or "")
    if re.search(r"(讲法|转场|补证据|补充证据|讲清楚|收束|回扣|预埋)", speaker_notes):
        return False
    bullets = page.get("bullets") if isinstance(page.get("bullets"), list) else []
    note_text = _compact_coverage_text(speaker_notes)
    meaningful_bullets = [
        _compact_coverage_text(str(item or ""))
        for item in bullets
        if len(_compact_coverage_text(str(item or ""))) >= 8
    ]
    if len(meaningful_bullets) < 2:
        return False
    repeated = sum(1 for item in meaningful_bullets if item and item in note_text)
    return repeated >= max(2, int(len(meaningful_bullets) * 0.75 + 0.999))


def _speaker_note_talk_content_signal(value: str) -> str:
    text = _clean_markdown_inline(str(value or ""))
    text = re.sub(r"(?m)^[-*+]\s*", "", text)
    text = _SPEAKER_NOTE_PROCEDURAL_PHRASE_RE.sub("", text)
    return _compact_coverage_text(text)


def _page_map_speaker_notes_missing_talk_content(page: dict) -> bool:
    if not isinstance(page, dict) or not _page_map_requires_body_bullets(page):
        return False
    speaker_notes = str(page.get("speaker_notes") or "").strip()
    if not speaker_notes:
        return False
    return len(_speaker_note_talk_content_signal(speaker_notes)) < 8


def _page_map_is_useful(page_map: list[dict], *, target_count: int, min_pages: int, strict: bool) -> bool:
    if not page_map:
        return False
    if strict and len(page_map) < target_count:
        return False
    if min_pages > 1 and len(page_map) < min_pages:
        return False
    if len(page_map) < max(3, int(target_count * PAGE_MAP_USEFUL_RATIO)):
        return False
    if _duplicate_page_map_headlines(page_map):
        return False
    contentful = 0
    body_required = 0
    for page in page_map:
        if _page_map_is_skeleton_placeholder(page):
            return False
        if _page_map_has_format_placeholders(page):
            return False
        if _page_map_has_inline_page_markers(page):
            return False
        bullets = page.get("bullets") if isinstance(page.get("bullets"), list) else []
        has_headline = bool(str(page.get("headline") or "").strip())
        if _page_map_requires_body_bullets(page):
            body_required += 1
            if not bullets:
                return False
            if _page_map_has_generic_speaker_notes(page):
                return False
            if _page_map_speaker_notes_missing_talk_content(page):
                return False
            if _page_map_speaker_notes_repeat_body(page):
                return False
        if has_headline and (bullets or str(page.get("speaker_notes") or "").strip()):
            contentful += 1
    if body_required <= 0:
        return False
    return contentful >= max(1, int(len(page_map) * 0.8))


def _compact_coverage_text(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(value or "").lower())


def _page_map_coverage_text(page_map: list[dict]) -> str:
    parts: list[str] = []
    for page in page_map or []:
        if not isinstance(page, dict):
            continue
        bullets = page.get("bullets") if isinstance(page.get("bullets"), list) else []
        parts.extend([
            str(page.get("section_title") or ""),
            str(page.get("headline") or ""),
            str(page.get("subhead") or ""),
            str(page.get("speaker_notes") or ""),
            *[str(item or "") for item in bullets],
        ])
    return _compact_coverage_text("\n".join(parts))


def _is_source_structure_anchor(text: str) -> bool:
    raw = str(text or "").strip()
    compact = _compact_coverage_text(raw)
    if not compact:
        return False
    if compact in {"序章", "结语"}:
        return True
    if len(compact) < 3:
        return False
    if re.search(r"第[一二三四五六七八九十0-9]+章", raw):
        return True
    if re.search(r"\b\d{1,2}\.\d{1,2}\b", raw):
        return True
    if any(term in compact for term in ("序章", "结语", "两张图", "90天", "行动清单")):
        return True
    return False


def _source_structure_anchor_text(value: str) -> str:
    text = _clean_visible_page_map_text(value)
    if " > " in text:
        text = text.rsplit(" > ", 1)[-1].strip()
    return text


def _is_source_tail_anchor(text: str) -> bool:
    raw = str(text or "").strip()
    compact = _compact_coverage_text(raw)
    if not compact:
        return False
    if re.search(r"第[一二三四五六七八九十0-9]+章", raw):
        return True
    tail_terms = (
        "结语",
        "结尾",
        "最后一页",
        "行动清单",
        "90天",
        "查定建放",
        "在人心里",
        "在平台里",
        "在ai里",
        "ai里有推荐",
    )
    return any(term in compact for term in tail_terms)


def _source_structure_anchor_candidates(source_draft: list[dict]) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for page in source_draft or []:
        if not isinstance(page, dict):
            continue
        page_type = str(page.get("type") or "content").strip().lower()
        if page_type == "cover":
            continue
        values = [
            str(page.get("headline") or ""),
        ]
        for value in values:
            text = _source_structure_anchor_text(value)
            compact = _compact_coverage_text(text)
            is_anchor = _is_source_structure_anchor(text)
            if (len(compact) < 3 and not is_anchor) or len(compact) > 90:
                continue
            if compact in seen or _is_page_map_format_placeholder(text) or _INLINE_PAGE_MARKER_RE.search(text):
                continue
            if not is_anchor:
                continue
            seen.add(compact)
            candidates.append(text)
    return candidates


def _document_structure_anchor_candidates(documents: str) -> list[str]:
    anchors: list[str] = []
    seen: set[str] = set()
    for unit in extract_document_outline_units(sanitize_ppt_recovery_text_for_content(documents or "")):
        if not isinstance(unit, dict):
            continue
        try:
            level = int(unit.get("level") or 9)
        except (TypeError, ValueError):
            level = 9
        if level <= 1 or level > 3:
            continue
        title = _source_structure_anchor_text(str(unit.get("title") or ""))
        compact = _compact_coverage_text(title)
        is_anchor = _is_source_structure_anchor(title)
        if (len(compact) < 3 and not is_anchor) or len(compact) > 90 or compact in seen:
            continue
        if _is_page_map_format_placeholder(title) or _INLINE_PAGE_MARKER_RE.search(title):
            continue
        seen.add(compact)
        anchors.append(title)
    return anchors


def _source_structure_checklist_text(source_draft: list[dict], documents: str = "") -> str:
    anchors: list[str] = []
    seen: set[str] = set()
    for item in [*_document_structure_anchor_candidates(documents), *_source_structure_anchor_candidates(source_draft)]:
        compact = _compact_coverage_text(item)
        if not compact or compact in seen:
            continue
        seen.add(compact)
        anchors.append(item)
    if not anchors:
        return ""
    lines = [f"- {item}" for item in anchors[:80]]
    return "\n".join(lines)


def _missing_source_structure_candidates(
    page_map: list[dict],
    source_draft: list[dict],
    intent_contract: dict | None,
) -> list[str]:
    if not source_draft or not _is_source_preserve_contract(intent_contract):
        return []
    generated_text = _page_map_coverage_text(page_map)
    missing: list[str] = []
    for candidate in _source_structure_anchor_candidates(source_draft):
        compact = _compact_coverage_text(candidate)
        if compact and compact not in generated_text:
            missing.append(candidate)
    return missing


def _tail_source_candidate_lines(page: dict) -> list[str]:
    if not isinstance(page, dict):
        return []
    page_type = str(page.get("type") or "content").strip().lower()
    section_title = str(page.get("section_title") or "")
    section_compact = _compact_coverage_text(section_title)
    generic_tail_page = bool(
        page_type == "ending"
        or any(term in section_compact for term in ("复盘", "下一步", "收束", "最后", "结束"))
    )
    bullets = page.get("bullets") if isinstance(page.get("bullets"), list) else []
    values = [
        str(page.get("headline") or ""),
        *[str(item or "") for item in bullets],
    ]
    candidates: list[str] = []
    for value in values:
        text = _clean_visible_page_map_text(str(value or ""))
        compact = _compact_coverage_text(text)
        if len(compact) < 8 or len(compact) > 90:
            continue
        if _is_page_map_format_placeholder(text) or _INLINE_PAGE_MARKER_RE.search(text):
            continue
        if not _is_source_tail_anchor(text) and not generic_tail_page:
            continue
        candidates.append(text)
    return candidates[:4]


def _missing_source_tail_candidates(
    page_map: list[dict],
    source_draft: list[dict],
    intent_contract: dict | None,
) -> list[str]:
    if not source_draft or not _is_source_preserve_contract(intent_contract):
        return []
    tail_pages = [page for page in source_draft[-min(3, len(source_draft)):] if isinstance(page, dict)]
    if not tail_pages:
        return []
    generated_text = _page_map_coverage_text(page_map)
    missing: list[str] = []
    for page in tail_pages:
        candidates = _tail_source_candidate_lines(page)
        if not candidates:
            continue
        if not any(_compact_coverage_text(candidate) in generated_text for candidate in candidates):
            missing.append(candidates[0])
    return missing


def _repair_page_map_with_source_coverage(
    page_map: list[dict],
    source_draft: list[dict],
    missing_candidates: list[str],
    *,
    target_count: int,
    min_pages: int,
    strict_page_count: bool,
) -> list[dict]:
    if not page_map or not source_draft or not missing_candidates:
        return []
    candidate_compacts = [
        compact
        for compact in (_compact_coverage_text(candidate) for candidate in missing_candidates)
        if compact
    ]
    if not candidate_compacts:
        return []

    repaired_by_num = {
        int(page.get("page_num") or 0): {**page}
        for page in page_map
        if isinstance(page, dict)
    }
    source_anchor_compacts = {
        compact
        for compact in (
            _compact_coverage_text(candidate)
            for candidate in [
                *_source_structure_anchor_candidates(source_draft),
                *[
                    line
                    for page in source_draft[-min(3, len(source_draft)):]
                    for line in _tail_source_candidate_lines(page)
                ],
            ]
        )
        if compact
    }

    def replaceable_page_nums() -> list[int]:
        preferred: list[int] = []
        fallback: list[int] = []
        for page_num in range(target_count, 0, -1):
            page = repaired_by_num.get(page_num)
            if not isinstance(page, dict):
                fallback.append(page_num)
                continue
            page_type = str(page.get("type") or "content").strip().lower()
            if page_type in {"cover", "ending"}:
                continue
            page_text = _page_map_coverage_text([page])
            if any(anchor in page_text for anchor in source_anchor_compacts):
                fallback.append(page_num)
                continue
            preferred.append(page_num)
        return preferred + fallback

    fallback_page_nums = replaceable_page_nums()
    used_fallback_page_nums: set[int] = set()

    def fallback_page_num() -> int:
        for page_num in fallback_page_nums:
            if page_num not in used_fallback_page_nums:
                used_fallback_page_nums.add(page_num)
                return page_num
        return 0

    replacements = 0
    for source_page in source_draft:
        if not isinstance(source_page, dict):
            continue
        source_text = _page_map_coverage_text([source_page])
        if not any(candidate in source_text for candidate in candidate_compacts):
            continue
        try:
            page_num = int(source_page.get("page_num") or 0)
        except (TypeError, ValueError):
            page_num = 0
        if page_num <= 0 or page_num > target_count:
            page_num = fallback_page_num()
        if page_num <= 0:
            continue
        repaired_page = {**source_page}
        repaired_page["page_num"] = page_num
        repaired_page["generation_status"] = "page_map_source"
        repaired_by_num[page_num] = repaired_page
        replacements += 1

    if replacements <= 0:
        return []

    repaired: list[dict] = []
    for idx in range(1, target_count + 1):
        page = repaired_by_num.get(idx)
        if page:
            repaired.append(page)
        elif idx - 1 < len(source_draft):
            repaired.append({**source_draft[idx - 1]})

    repaired = _normalize_page_map(repaired)
    if _page_map_is_useful(
        repaired,
        target_count=target_count,
        min_pages=min_pages,
        strict=strict_page_count,
    ):
        return repaired

    source_only = _normalize_page_map([{**page} for page in source_draft[:target_count] if isinstance(page, dict)])
    if _page_map_is_useful(
        source_only,
        target_count=target_count,
        min_pages=min_pages,
        strict=strict_page_count,
    ):
        return source_only
    return []


def _source_page_key_from_ref(ref: dict) -> tuple[str, int] | None:
    if not isinstance(ref, dict):
        return None
    source_document = _source_doc_basename(str(ref.get("source_document") or ""))
    try:
        source_page_num = int(ref.get("source_page_num") or 0)
    except (TypeError, ValueError):
        source_page_num = 0
    if not source_document or source_page_num <= 0:
        return None
    return source_document, source_page_num


def _page_source_page_keys(page: dict) -> set[tuple[str, int]]:
    keys: set[tuple[str, int]] = set()
    for ref in _normalize_source_refs_for_page_map(page.get("source_refs")):
        key = _source_page_key_from_ref(ref)
        if key:
            keys.add(key)
    return keys


def _dedupe_figure_refs(refs: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[str] = set()
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        figure_id = str(ref.get("figure_id") or "").strip()
        if not _is_valid_figure_id(figure_id) or figure_id in seen:
            continue
        seen.add(figure_id)
        deduped.append(ref)
    return deduped


def _mark_used_figure_refs(refs: list[dict], used_figure_ids: set[str]) -> None:
    for ref in refs or []:
        if not isinstance(ref, dict):
            continue
        figure_id = str(ref.get("figure_id") or "").strip()
        if _is_valid_figure_id(figure_id):
            used_figure_ids.add(figure_id)


def _unused_auto_figure_refs(refs: list[dict], used_figure_ids: set[str]) -> list[dict]:
    filtered: list[dict] = []
    for ref in _dedupe_figure_refs(refs):
        figure_id = str(ref.get("figure_id") or "").strip()
        if not _is_valid_figure_id(figure_id) or figure_id in used_figure_ids:
            continue
        used_figure_ids.add(figure_id)
        filtered.append(ref)
    return filtered


def _source_draft_figures_by_source_page(source_draft: list[dict]) -> dict[tuple[str, int], list[dict]]:
    by_source_page: dict[tuple[str, int], list[dict]] = {}
    for page in source_draft or []:
        if not isinstance(page, dict):
            continue
        for ref in _normalize_figure_refs(page.get("figure_refs")):
            key = _source_page_key_from_ref(ref)
            if key:
                by_source_page.setdefault(key, []).append(ref)
    return {key: _dedupe_figure_refs(refs) for key, refs in by_source_page.items()}


_SOURCE_FACT_TOKEN_RE = re.compile(
    r"\d{4}\.\d{1,2}|"
    r"(?:MAU|MRR)[:：]?\s*[^\s\n]+|"
    r"\d+(?:\.\d+)?\s*[％%]|"
    r"[A-Za-z][A-Za-z0-9./_-]{2,}",
    flags=re.IGNORECASE,
)
_SOURCE_CJK_FACT_SIGNAL_RE = re.compile(
    r"(?:真正|不是|而是|必须|核心|关键|底牌|闭环|行动|选择|相信|证据|品牌|增长|战略|用户|客户|组织|资产)"
)


def _source_fact_tokens(value: str) -> set[str]:
    text = str(value or "")
    tokens: set[str] = set()
    for match in _SOURCE_FACT_TOKEN_RE.finditer(text):
        token = re.sub(r"\s+", "", match.group(0))
        if token:
            tokens.add(token.lower())
    for raw_line in re.split(r"[\n\r]+", text):
        line = _clean_visible_page_map_text(raw_line)
        compact = _compact_coverage_text(line)
        if not compact or not re.search(r"[\u4e00-\u9fff]", compact):
            continue
        if 8 <= len(compact) <= 70 and _SOURCE_CJK_FACT_SIGNAL_RE.search(line):
            tokens.add(compact.lower())
    return tokens


def _page_map_loses_source_facts(page_bullets: list, source_bullets: list) -> bool:
    source_text = "\n".join(str(item or "") for item in source_bullets)
    page_text = "\n".join(str(item or "") for item in page_bullets)
    source_tokens = _source_fact_tokens(source_text)
    if not source_tokens:
        return False
    page_tokens = _source_fact_tokens(page_text)
    return bool(source_tokens - page_tokens)


def _same_source_page(page: dict, source_draft_page: dict | None) -> bool:
    if not isinstance(source_draft_page, dict):
        return False
    page_keys = _page_source_page_keys(page)
    source_keys = _page_source_page_keys(source_draft_page)
    return bool(page_keys and source_keys and page_keys.intersection(source_keys))


def _matching_source_draft_figure_refs(
    page: dict,
    source_draft_page: dict | None,
    figures_by_source_page: dict[tuple[str, int], list[dict]],
) -> list[dict]:
    keys = _page_source_page_keys(page)
    if not keys and isinstance(source_draft_page, dict):
        keys = _page_source_page_keys(source_draft_page)
    refs: list[dict] = []
    for key in sorted(keys):
        refs.extend(figures_by_source_page.get(key) or [])
    if not refs and isinstance(source_draft_page, dict):
        refs = _normalize_figure_refs(source_draft_page.get("figure_refs"))
    return _dedupe_figure_refs(refs)[:8]




def _merge_page_map_with_source_draft(
    page_map: list[dict],
    source_draft: list[dict],
    *,
    target_count: int,
    fill_missing_from_source: bool = True,
) -> list[dict]:
    by_num = {int(page.get("page_num") or 0): page for page in page_map if isinstance(page, dict)}
    source_draft_by_num = {int(page.get("page_num") or 0): page for page in source_draft if isinstance(page, dict)}
    figures_by_source_page = _source_draft_figures_by_source_page(source_draft)
    used_figure_ids: set[str] = set()
    merged: list[dict] = []
    for idx in range(1, target_count + 1):
        if idx in by_num:
            page = {**by_num[idx]}
            _mark_used_figure_refs(_normalize_figure_refs(page.get("figure_refs")), used_figure_ids)
            source_draft_page = source_draft_by_num.get(idx)
            source_draft_is_placeholder = _page_map_is_skeleton_placeholder(source_draft_page) if source_draft_page else False
            bullets = page.get("bullets") if isinstance(page.get("bullets"), list) else []
            source_draft_bullets = (
                source_draft_page.get("bullets")
                if isinstance(source_draft_page, dict)
                and isinstance(source_draft_page.get("bullets"), list)
                and not source_draft_is_placeholder
                else []
            )
            should_restore_source_bullets = (
                bool(bullets)
                and bool(source_draft_bullets)
                and _same_source_page(page, source_draft_page)
                and _page_map_loses_source_facts(bullets, source_draft_bullets)
            )
            if _page_map_requires_body_bullets(page) and source_draft_bullets and (not bullets or should_restore_source_bullets):
                page["bullets"] = list(source_draft_bullets)
                if not str(page.get("subhead") or "").strip():
                    page["subhead"] = str(source_draft_page.get("subhead") or "").strip()
                if not page.get("source_refs"):
                    page["source_refs"] = (
                        source_draft_page.get("source_refs")
                        if isinstance(source_draft_page.get("source_refs"), list)
                        else []
                    )
                if not page.get("figure_refs"):
                    source_figure_refs = (
                        source_draft_page.get("figure_refs")
                        if isinstance(source_draft_page.get("figure_refs"), list)
                        else []
                    )
                    unused_figure_refs = _unused_auto_figure_refs(source_figure_refs, used_figure_ids)
                    if unused_figure_refs:
                        page["figure_refs"] = unused_figure_refs
                page["generation_status"] = "page_map_model_with_source_body"
            if isinstance(source_draft_page, dict) and not page.get("source_refs") and source_draft_page.get("source_refs"):
                page["source_refs"] = (
                    source_draft_page.get("source_refs")
                    if isinstance(source_draft_page.get("source_refs"), list)
                    else []
                )
            if not page.get("figure_refs"):
                matched_figure_refs = _matching_source_draft_figure_refs(
                    page,
                    source_draft_page,
                    figures_by_source_page,
                )
                matched_figure_refs = _unused_auto_figure_refs(matched_figure_refs, used_figure_ids)
                if matched_figure_refs:
                    page["figure_refs"] = matched_figure_refs
                    if str(page.get("generation_status") or "page_map_model") == "page_map_model":
                        page["generation_status"] = "page_map_model_with_source_refs"
            merged.append(page)
        elif fill_missing_from_source and idx - 1 < len(source_draft):
            source_draft_page = source_draft[idx - 1]
            if not _page_map_is_skeleton_placeholder(source_draft_page):
                page = {**source_draft_page}
                if page.get("figure_refs"):
                    page["figure_refs"] = _unused_auto_figure_refs(
                        _normalize_figure_refs(page.get("figure_refs")),
                        used_figure_ids,
                    )
                merged.append(page)
    return _normalize_page_map(merged[:target_count])


def _generate_model_page_map(
    *,
    topic: str,
    audience: str,
    documents: str,
    page_goal_text: str,
    target_count: int,
    min_pages: int,
    max_pages: int,
    search_context: str = "",
    source_page_map_markdown: str = "",
    source_structure_checklist: str = "",
    intent_contract: dict | None = None,
    on_progress: Callable[[dict], None] | None = None,
    mode: str = "default",
) -> list[dict]:
    documents = sanitize_ppt_recovery_text_for_content(documents)
    if on_progress:
        on_progress({
            "stage": "analyzing",
            "message": "正在整理每一页要讲什么...",
            "current_page": 0,
            "total_pages": target_count,
        })
    doc_text = remove_markdown_structural_noise(documents or "")
    if len(doc_text) > PAGE_MAP_DOCUMENT_LIMIT:
        doc_text = _document_excerpt_for_extension(doc_text, limit=PAGE_MAP_DOCUMENT_LIMIT)
    preservation_policy = _document_preservation_policy(documents, topic, intent_contract)
    mode_instruction = ""
    if mode == "direct_replicate":
        mode_instruction = """
【直接复刻约束】
- 严格按原PPT页数和顺序输出，不要合并、拆分或重新排序页面。
- 尽量保留原PPT的标题和正文，不要改写内容。
- 自动识别并过滤版式模板占位符，只保留真实信息。
- 保持原PPT的信息密度，不要因为"每页只说一件事"而删减原文内容。"""

    prompt = f"""你是一位顶尖的 PPT 内容架构师。请先生成整份 PPT 的"逐页内容规划"，不要输出 JSON。

【用户主题和约束】
{topic}

【目标受众】
{audience}

【页数目标】
{page_goal_text}
本轮建议按约 {target_count} 页规划；如果材料明显不足，可以减少页数，但不能用空话或重复页凑页数。

【用户上传材料】
{doc_text or "无"}

【系统预生成的正文底稿】
{source_page_map_markdown or "无"}

【必须覆盖的原文结构清单】
{source_structure_checklist or "无"}

【实时搜索上下文】
{search_context or "无"}

【材料使用规则】
{preservation_policy or "没有上传文档时，根据用户主题和受众目标生成内容结构。"}
{mode_instruction}

【输出要求】
1. 一次性给出全局逐页内容规划，必须覆盖整份 PPT 的开场、主线、转场、案例和结尾。
2. 标题和 bullet 必须尽量来自用户材料或 Brief，不能为了凑页数发明不相干内容。
3. 如果提供了"必须覆盖的原文结构清单"，必须逐项覆盖；可以优化标题表达，但不能漏掉任何章节、编号小节、结语或行动清单。
4. 如果提供了"系统预生成的正文底稿"，你必须以它为基础优化标题、顺序和取舍；content/toc/data 页不能删除底稿 bullet，合并页面时也要把被合并页面的关键事实写进新 bullet。
5. 不要把同一个来源主题拆成"简短版"和"展开版"两页；如果两页标题、bullet 或来源线索接近，必须合并为一页，只保留更完整的一版。
6. 相邻页面必须有明确的新信息、新问题或新叙事功能；不能出现连续两页同标题、同 bullet、同来源框架。
7. 页间要有连续叙事：上一页为什么引出下一页要想清楚。
8. 封面页可以没有 bullet；封底页只收束，不引入新论点。
9. 用户材料中的 Markdown 分隔线（如 ---、***、___）只代表结构，不能作为标题、bullet 或正文输出。
10. 如果用户上传材料来自 PPT 解析，其中可能残留版式模板占位文字（如"单击此处添加标题""标题/主文案""第一行："等）或布局结构标注。你必须自动识别并过滤这些非内容元素，只保留真实信息。
11. 如果用户材料里出现 AVAILABLE_FIGURES / FIGURE 行，你要自主判断哪些原图适合哪一页；适合时在该页写"配图：source_document 第source_page_num页 figure_id=\"完整FIGURE_ID\" 使用理由"。figure_id 必须原样复制 FIGURE 行里的值；不要写 figure_id、figureid、图片ID 等占位符；不适合就不要硬配图。
12. 目录页是可选页；篇幅紧凑或叙事更连贯时可以不放。使用目录页时 type 用 toc，标题可以直接写“目录”，不要套固定命名或固定路径构图。
13. 输出格式必须固定为；下面是格式说明，不要复制"标题"、"bullet"、"演讲者备注"等占位词，必须替换成用户材料中的真实内容。备注要先写出这一页需要讲什么，再补一句必要的转场：
P1｜cover｜封面｜用用户材料写出的封面标题
- 用用户材料写出的具体要点
- 用用户材料写出的具体要点
备注：讲稿内容：这一页要讲出的关键事实、原文细节、案例、数据、解释和结论；必要时补一句转场。
视觉：画面建议
来源：材料线索
配图：材料文件名 第页码页 figure_id="完整FIGURE_ID" 使用理由

P2｜content｜章节｜用用户材料写出的页面标题
- 用用户材料写出的具体要点
- 用用户材料写出的具体要点
备注：讲稿内容：这一页要讲出的关键事实、原文细节、案例、数据、解释和结论；必要时补一句转场。
视觉：画面建议
来源：材料线索
配图：材料文件名 第页码页 figure_id="完整FIGURE_ID" 使用理由

不要输出 JSON，不要输出 Markdown 表格，不要加额外解释。"""

    started_at = time.monotonic()
    client = get_llm_client()
    raw = ""
    try:
        stream = client.chat.completions.create(
            model=get_minimax_llm_model(),
            messages=[
                {"role": "system", "content": "你是世界一流的 PPT 总架构师。先做完整逐页内容规划，不输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.45,
            max_tokens=_page_map_output_token_budget(target_count),
            timeout=PAGE_MAP_MODEL_TIMEOUT_SECONDS,
            stream=True,
            extra_body={
                "thinking": {"type": "adaptive"},
                "reasoning_split": True,
            },
        )
        if hasattr(stream, "choices"):
            raw = stream.choices[0].message.content or ""
        else:
            seen_pages = 0
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content or ""
                if not delta:
                    continue
                raw += delta
                current_pages = len(re.findall(r"(?m)^\s*P\s*\d{1,3}\s*[|｜]", raw))
                if current_pages > seen_pages:
                    seen_pages = current_pages
                    if on_progress:
                        on_progress({
                            "stage": "generating",
                            "message": f"正在整理第 {min(seen_pages, target_count)}/{target_count} 页内容...",
                            "current_page": min(seen_pages, target_count),
                            "total_pages": target_count,
                        })
    except Exception:
        if not raw.strip():
            raise
        logger.warning(
            "[ContentPlan] Page map stream stopped after partial output, chars=%s pages=%s",
            len(raw),
            len(re.findall(r"(?m)^\s*P\s*\d{1,3}\s*[|｜]", raw)),
        )
    logger.info(
        "[ContentPlan] Page map generated target=%s range=%s-%s elapsed=%.2fs output_chars=%s",
        target_count,
        min_pages,
        max_pages,
        time.monotonic() - started_at,
        len(raw),
    )
    return parse_page_map_markdown(raw)


def _page_map_output_token_budget(target_count: int) -> int:
    try:
        page_count = max(1, int(target_count))
    except (TypeError, ValueError):
        page_count = 1
    return min(
        PAGE_MAP_MAX_TOKENS_CAP,
        max(PAGE_MAP_BASE_MAX_TOKENS, page_count * PAGE_MAP_TOKENS_PER_PAGE),
    )


def generate_content_page_map(
    *,
    topic: str,
    audience: str = "通用受众",
    page_count: int | None = None,
    documents: str = "",
    search_context: str = "",
    intent_contract: dict | None = None,
    on_progress: Callable[[dict], None] | None = None,
    mode: str = "default",
) -> list[dict]:
    documents = sanitize_ppt_recovery_text_for_content(documents)
    if intent_contract is not None:
        intent_contract = _effective_intent_contract(topic, documents, intent_contract)
    requested_page_range = infer_page_count_range_from_topic(topic)
    target_count, min_pages, max_pages = resolve_content_plan_page_target(
        topic,
        page_count,
        documents,
        intent_contract=intent_contract,
    )
    strict_page_count = _is_strict_page_count_request(topic) and not requested_page_range
    if requested_page_range:
        page_goal_text = (
            f"优先参考 {min_pages}-{max_pages} 页范围；材料不足时可以更短，"
            f"当前建议约 {target_count} 页，不要为了凑页数灌水。"
        )
    elif strict_page_count:
        page_goal_text = f"用户明确要求必须 {target_count} 页。"
    else:
        page_goal_text = (
            f"优先生成约 {target_count} 页；只有内容结构确实需要时才在 {min_pages}-{max_pages} 页内小幅浮动，"
            "不要为了靠近上限而拆出重复页。"
        )

    source_draft = _source_draft_page_map(
        topic=topic,
        documents=documents,
        target_count=target_count,
        min_pages=min_pages,
        max_pages=max_pages,
        intent_contract=intent_contract,
    )
    if mode == "direct_replicate" and source_draft and not detect_ppt_sources(documents):
        logger.info("ContentPlan: using deterministic source page map for direct replicate, pages=%s", len(source_draft))
        return _normalize_page_map(source_draft)

    raw_source_page_map_markdown = render_page_map_markdown(source_draft)
    if on_progress:
        on_progress({
            "stage": "diagnostic",
            "diagnostic_event": "content_plan_page_map_input",
            "documents_chars": len(documents or ""),
            "page_map_document_limit_chars": PAGE_MAP_DOCUMENT_LIMIT,
            "page_map_document_will_truncate": len(documents or "") > PAGE_MAP_DOCUMENT_LIMIT,
            "source_draft_page_count": len(source_draft or []),
            "source_draft_chars": len(raw_source_page_map_markdown),
            "source_draft_limit_chars": PAGE_MAP_SOURCE_DRAFT_LIMIT,
            "source_draft_will_truncate": len(raw_source_page_map_markdown) > PAGE_MAP_SOURCE_DRAFT_LIMIT,
            "target_page_count": target_count,
            "min_pages": min_pages,
            "max_pages": max_pages,
        })
    source_page_map_markdown = raw_source_page_map_markdown
    if len(source_page_map_markdown) > PAGE_MAP_SOURCE_DRAFT_LIMIT:
        source_page_map_markdown = _document_excerpt_for_extension(
            source_page_map_markdown,
            limit=PAGE_MAP_SOURCE_DRAFT_LIMIT,
        )
    source_structure_checklist = _source_structure_checklist_text(source_draft, documents=documents)
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            model_map = _generate_model_page_map(
                topic=topic,
                audience=audience,
                documents=documents,
                page_goal_text=page_goal_text,
                target_count=target_count,
                min_pages=min_pages,
                max_pages=max_pages,
                search_context=search_context,
                source_page_map_markdown=source_page_map_markdown,
                source_structure_checklist=source_structure_checklist,
                intent_contract=intent_contract,
                on_progress=on_progress,
                mode=mode,
            )
            model_map = _merge_page_map_with_source_draft(
                model_map,
                source_draft,
                target_count=max(target_count, len(model_map)),
                fill_missing_from_source=False,
            )
            if _page_map_is_useful(model_map, target_count=target_count, min_pages=min_pages, strict=strict_page_count):
                missing_tail = _missing_source_tail_candidates(model_map, source_draft, intent_contract)
                missing_structure = _missing_source_structure_candidates(model_map, source_draft, intent_contract)
                if missing_tail or missing_structure:
                    missing_candidates = [*missing_tail, *missing_structure]
                    repair_target_count = max(target_count, len(model_map))
                    repaired_map = _repair_page_map_with_source_coverage(
                        model_map,
                        source_draft,
                        missing_candidates,
                        target_count=repair_target_count,
                        min_pages=min_pages,
                        strict_page_count=strict_page_count,
                    )
                    if repaired_map:
                        remaining_tail = _missing_source_tail_candidates(repaired_map, source_draft, intent_contract)
                        remaining_structure = _missing_source_structure_candidates(repaired_map, source_draft, intent_contract)
                        if not remaining_tail and not remaining_structure:
                            logger.warning(
                                "ContentPlan: repaired model page map with source coverage, replaced_missing=%s",
                                "；".join(missing_candidates[:8]),
                            )
                            if on_progress:
                                on_progress({
                                    "stage": "quality_review",
                                    "message": "正在补齐原文结构，确保章节和结尾完整...",
                                    "current_page": 0,
                                    "total_pages": len(repaired_map) or repair_target_count,
                                })
                            return _normalize_page_map(repaired_map)
                    missing_parts: list[str] = []
                    if missing_tail:
                        missing_parts.append("source tail coverage: " + "；".join(missing_tail[:3]))
                    if missing_structure:
                        missing_parts.append("source structure coverage: " + "；".join(missing_structure[:5]))
                    last_error = ValueError("Content plan generation failed: model output missed " + " | ".join(missing_parts))
                    logger.warning(
                        "ContentPlan: model page map missed source coverage, no source repair available attempt=%s error=%s",
                        attempt + 1,
                        last_error,
                    )
                    break
                return _normalize_page_map(model_map)
            logger.warning(
                "ContentPlan: model page map not useful, pages=%s target=%s min=%s attempt=%s",
                len(model_map),
                target_count,
                min_pages,
                attempt + 1,
            )
            if any(_page_map_has_inline_page_markers(page) for page in model_map):
                raise ValueError("Content plan generation failed: model output contained inline page markers.")
            if any(_page_map_has_format_placeholders(page) for page in model_map):
                raise ValueError("Content plan generation failed: model output contained format placeholders.")
            duplicate_headlines = _duplicate_page_map_headlines(model_map)
            if duplicate_headlines:
                raise ValueError(
                    "Content plan generation failed: model output contained duplicate headlines: "
                    + "；".join(duplicate_headlines[:5])
                )
            if model_map:
                last_error = ValueError(f"model output was incomplete: {len(model_map)}/{target_count} pages")
            else:
                last_error = ValueError("model output was empty")
        except Exception as exc:
            last_error = exc
            logger.warning(
                "ContentPlan: failed to generate model page map attempt=%s error=%s",
                attempt + 1,
                exc,
            )
        if attempt == 0:
            continue
    raise ValueError(f"Content plan generation failed before producing usable model pages: {last_error}")


def _page_map_preserves_source_page_type(page: dict) -> bool:
    status = str(page.get("generation_status") or "").strip()
    if status == "page_map_source" or status.endswith("_direct") or status.endswith("_preserve_source"):
        return True
    refs = page.get("source_refs") if isinstance(page.get("source_refs"), list) else []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        reason = str(ref.get("reason") or "").strip().lower()
        if reason in {"direct_replicate", "single_pdf_page_polish", "single_ppt_page_polish"}:
            return True
    return False


def _page_map_preserves_source_first_page_type(page: dict) -> bool:
    return _page_map_preserves_source_page_type(page)


def content_plan_from_page_map(
    page_map: list[dict],
    *,
    expected_total: int | None = None,
    source_context: str | None = None,
) -> list[dict]:
    outline: list[dict] = []
    normalized_map = _normalize_page_map(page_map)
    source_figures = _source_context_figure_index(source_context)
    total = max(len(normalized_map), int(expected_total or 0)) if expected_total else len(normalized_map)
    for idx, page in enumerate(normalized_map, start=1):
        page_type = str(page.get("type") or "content").strip().lower() or "content"
        if idx == 1:
            if page_type != "cover" and _page_map_preserves_source_page_type(page):
                page_type = page_type if page_type != "ending" else "content"
            else:
                page_type = "cover"
        elif page_type == "cover":
            page_type = "section"
        elif idx == total and total > 1:
            if page_type != "ending" and _page_map_preserves_source_page_type(page):
                page_type = page_type if page_type != "cover" else "section"
            else:
                page_type = "ending"
        elif page_type == "ending":
            page_type = "section"
        bullets = page.get("bullets") if isinstance(page.get("bullets"), list) else []
        body = "" if page_type == "cover" else "\n".join(f"- {str(item).strip()}" for item in bullets if str(item).strip())
        source_refs = _normalize_source_refs_for_page_map(page.get("source_refs"))
        requested_figure_refs = _normalize_figure_refs(page.get("figure_refs"))
        figure_refs = _filter_figure_refs_for_page(
            requested_figure_refs,
            page=page,
            source_refs=source_refs,
            source_figures=source_figures,
        )
        if not figure_refs and not requested_figure_refs:
            figure_refs = _source_context_figures_for_source_refs(source_refs, source_figures, page=page)
            figure_refs = _filter_figure_refs_for_page(
                figure_refs,
                page=page,
                source_refs=source_refs,
                source_figures=source_figures,
            )
        source_keys: set[tuple[str, int]] = set()
        for ref in source_refs:
            if not isinstance(ref, dict):
                continue
            try:
                source_page_num = int(ref.get("source_page_num") or 0)
            except (TypeError, ValueError):
                source_page_num = 0
            source_keys.add((str(ref.get("source_document") or ""), source_page_num))
        for ref in _source_refs_from_figure_refs(figure_refs):
            key = (str(ref.get("source_document") or ""), int(ref.get("source_page_num") or 0))
            if key not in source_keys:
                source_refs.append(ref)
                source_keys.add(key)
        outline.append({
            "page_num": idx,
            "type": page_type,
            "section_title": _normalize_section_title(str(page.get("section_title") or "").strip()),
            "text_content": {
                "headline": str(page.get("headline") or "").strip() or f"第 {idx} 页",
                "subhead": str(page.get("subhead") or "").strip(),
                "body": body,
            },
            "speaker_notes": str(page.get("speaker_notes") or "").strip(),
            "visual_suggestion": str(page.get("visual_suggestion") or "").strip() or "根据本页内容选择清晰的版式，优先保证信息层级和演示节奏。",
            "source_refs": source_refs,
            "figure_refs": figure_refs,
            "generation_status": str(page.get("generation_status") or "page_map"),
            "page_map_markdown": render_page_map_markdown([page])[:2000],
        })
    return _normalize_content_markdown(outline)


def build_long_deck_skeleton(
    *,
    topic: str,
    target_count: int,
    min_pages: int,
    max_pages: int,
    deck_blueprint: str = "",
) -> list[dict]:
    """Create editable placeholder pages before any long-running LLM call."""
    target_count = max(1, int(target_count or max_pages or min_pages or 1))
    title = _brief_title(topic)
    section_ranges = _long_deck_section_ranges(target_count)
    skeleton: list[dict] = []

    def section_for_page(page_num: int) -> str:
        for start, end, section_title in section_ranges:
            if start <= page_num <= end:
                return section_title
        return "内容结构"

    for page_num in range(1, target_count + 1):
        if page_num == 1:
            page = {
                "page_num": page_num,
                "type": "cover",
                "section_title": "封面",
                "text_content": {
                    "headline": title,
                    "subhead": f"{min_pages}-{max_pages} 页 PPT 内容规划",
                    "body": "",
                },
                "speaker_notes": "封面页。后续分段生成会补齐内容定位、开场表达和演示备注。",
                "visual_suggestion": "封面视觉待内容细化后统一生成。",
                "source_refs": [],
                "generation_status": "skeleton",
            }
        elif page_num == target_count:
            page = {
                "page_num": page_num,
                "type": "ending",
                "section_title": "总结收束",
                "text_content": {
                    "headline": "总结与下一步",
                    "subhead": "",
                    "body": "回到整份内容主线，提炼关键结论、行动建议和后续讨论方向。",
                },
                "speaker_notes": "封底页。后续分段生成会根据完整内容补齐收束表达。",
                "visual_suggestion": "封底视觉待内容细化后统一生成。",
                "source_refs": [],
                "generation_status": "skeleton",
            }
        else:
            section_title = section_for_page(page_num)
            short_section = section_title.split("：", 1)[-1]
            is_section_start = any(start == page_num for start, _, _ in section_ranges)
            page = {
                "page_num": page_num,
                "type": "section" if is_section_start else "content",
                "section_title": section_title,
                "text_content": {
                    "headline": short_section if is_section_start else f"{short_section} · 第 {page_num} 页",
                    "subhead": "内容待细化",
                    "body": "本页已先放入长篇 PPT 结构中，系统会继续根据 Brief 和上传材料补齐正文、案例、备注和视觉建议。",
                },
                "speaker_notes": "占位备注。系统会继续分段补齐本页的表达逻辑、演示节奏和转场。",
                "visual_suggestion": "待内容细化后生成本页视觉建议。",
                "source_refs": [],
                "generation_status": "skeleton",
            }
        if deck_blueprint:
            page["deck_blueprint_ref"] = deck_blueprint[:1000]
        skeleton.append(page)
    return skeleton


def _fallback_deck_blueprint(target_count: int, min_pages: int, max_pages: int, documents: str = "") -> str:
    target_count = max(1, int(target_count or max_pages or min_pages or 1))
    lines = [
        "## 全局蓝图",
        f"- P1：封面。定主题、受众和表达语境，正文留空。",
    ]
    source_sections = _source_deck_sections(documents)
    if source_sections and _should_include_toc_page(target_count=target_count, agenda_lines=source_sections):
        lines.append("- P2：目录。按上传材料的核心章节显示全局结构，标题可以简洁写成“目录”。")
        for start, end, title in _distribute_source_sections(target_count=target_count, sections=source_sections):
            lines.append(f"- P{start}-P{end}：{title}。严格围绕上传材料这一章节展开，拆成原文判断、方法框架、案例和关键段落。")
        lines.append("\n必须覆盖上传材料中的每个章节，不能只讲前半部分；后续分段生成时不得跳过后面的模块。")
    else:
        for start, end, title in _long_deck_section_ranges(target_count):
            lines.append(f"- P{start}-P{end}：{title}。围绕用户材料展开，按内容节奏拆成论点、案例、方法和转场页。")
    if target_count >= 2:
        lines.append(f"- P{target_count}：封底。只做感谢或下一步，不引入新论点。")
    lines.append(f"\n页码必须完整覆盖 P1-P{target_count}；用户要求页数范围是 {min_pages}-{max_pages} 页。")
    return "\n".join(lines)


def _generate_deck_blueprint(
    *,
    topic: str,
    audience: str,
    documents: str,
    min_pages: int,
    max_pages: int,
    target_count: int,
    search_context: str = "",
    on_progress: Callable[[dict], None] | None = None,
) -> str:
    if on_progress:
        on_progress({
            "stage": "analyzing",
            "message": "正在快速设计全局内容结构...",
            "current_page": 0,
            "total_pages": max_pages,
        })

    fallback = _fallback_deck_blueprint(target_count, min_pages, max_pages, documents)
    source_sections = _source_deck_sections(documents)
    if source_sections:
        logger.info("ContentPlan: using source-aware blueprint with %s source sections", len(source_sections))
        return fallback
    doc_excerpt = _document_excerpt_for_extension(documents, limit=7000)
    prompt = f"""你是一位顶尖的 PPT 内容架构师。先为一份长篇课程型 PPT 设计全局蓝图，不要生成逐页 JSON。

【用户主题和约束】
{topic}

【目标受众】
{audience}

【页数要求】
- 用户明确要求落在 {min_pages}-{max_pages} 页。
- 本次建议按 {target_count} 页规划。

【用户上传材料摘录】
{doc_excerpt or "无"}

【实时搜索上下文】
{search_context or "无"}

【蓝图要求】
1. 输出 6-10 个一级章节，每章必须给出连续页码区间，例如 P1-P4。
2. 页码区间必须完整覆盖 P1-P{target_count}，不能断档、重叠或提前结束。
3. 标明每章的表达目标、核心论点、关键材料来源、案例安排、转场逻辑。
4. 必须把封面、必要的过渡、总结和封底纳入页码规划。
5. 这是一份长篇 PPT，要服务用户的受众、场景和时长，不要做成薄摘要。
6. 不要把原文机械切成"续2/续3"页面；必须按内容功能拆成开场设问、核心判断、公式/框架、案例、动作清单和转场。

只输出可读的中文 Markdown 蓝图，不要输出 JSON。"""

    try:
        client = get_llm_client()
        response = client.chat.completions.create(
            model=get_minimax_llm_model(),
            messages=[
                {"role": "system", "content": "你是世界一流的长篇 PPT 内容架构师。只输出全局蓝图，不生成逐页 JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.35,
            max_tokens=1800,
            timeout=35.0,
        )
        blueprint = (response.choices[0].message.content or "").strip()
        return blueprint[:12000] or fallback
    except Exception as e:
        logger.warning("ContentPlan: failed to generate long deck blueprint: %s", e)
        if on_progress:
            on_progress({
                "stage": "analyzing",
                "message": "全局结构设计耗时较长，正在使用快速结构继续生成...",
                "current_page": 0,
                "total_pages": max_pages,
            })
        return fallback


def _extend_outline_to_target_count(
    outline: List[Dict],
    *,
    topic: str,
    documents: str,
    deck_blueprint: str = "",
    target_count: int,
    min_pages: int,
    max_pages: int,
    on_progress: Callable[[dict], None] | None = None,
) -> List[Dict]:
    if len(outline) >= min_pages:
        return outline

    target_count = max(min_pages, min(max_pages, int(target_count or max_pages)))
    if len(outline) >= target_count:
        return outline

    extended = [page for page in outline if isinstance(page, dict)]
    if extended and str(extended[-1].get("type") or "").lower() == "ending" and len(extended) < target_count:
        extended = extended[:-1]

    client = get_llm_client()
    chunk_size = 16
    doc_excerpt = _document_excerpt_for_extension(documents)
    logger.info(
        "ContentPlan: initial outline has %s pages, extending to %s pages for requested range %s-%s",
        len(extended),
        target_count,
        min_pages,
        max_pages,
    )

    while len(extended) < target_count:
        start_page = len(extended) + 1
        end_page = min(target_count, start_page + chunk_size - 1)
        is_final_chunk = end_page >= target_count
        if on_progress:
            on_progress({
                "stage": "generating",
                "message": f"正在补齐第 {start_page}-{end_page}/{target_count} 页...",
                "current_page": len(extended),
                "total_pages": target_count,
            })

        prompt = f"""你是一位顶尖的商业演示架构师。上一轮内容规划只生成到第 {len(extended)} 页，但用户要求 {min_pages}-{max_pages} 页。

【用户主题和约束】
{topic}

【全局蓝图（必须遵守）】
{deck_blueprint or "无。请严格接续已有页面摘要。"}

【已生成页面摘要】
{_outline_extension_summary(extended)}

【用户上传材料摘录】
{doc_excerpt}

【本轮任务】
只续写第 {start_page} 页到第 {end_page} 页，共 {end_page - start_page + 1} 页。
- page_num 必须从 {start_page} 连续编号到 {end_page}。
- 如果提供了全局蓝图，本轮页面必须落在蓝图对应的章节和页码区间内。
- 续写必须接上已有叙事，不要重复已生成页面。
- 每页仍必须包含 type、section_title、text_content、speaker_notes、visual_suggestion、source_refs。
- {"第 " + str(end_page) + " 页必须是 ending 封底页，用于收束全场。" if is_final_chunk else "本轮不是最后一组页面，不要生成 ending 封底页。"}
- 如果材料不足，优先扩展为论点拆解页、案例页、方法页、过渡页和总结页，而不是灌水。

严格输出 JSON 数组，不要包含 Markdown 代码块标记。"""

        response = client.chat.completions.create(
            model=get_minimax_llm_model(),
            messages=[
                {"role": "system", "content": "你是世界一流的 PPT 架构师。必须且只能输出合法的 JSON 数组，严禁添加额外说明文本。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.45,
        )
        raw = response.choices[0].message.content or ""
        new_pages = _parse_outline_json_response(raw)
        if not new_pages:
            raise ValueError(f"内容规划续写失败：第 {start_page}-{end_page} 页没有返回可用页面。")

        before_count = len(extended)
        for page in new_pages:
            if len(extended) >= target_count:
                break
            next_page_num = len(extended) + 1
            page["page_num"] = next_page_num
            if not is_final_chunk and str(page.get("type") or "").lower() == "ending":
                page["type"] = "content"
            extended.append(page)
        if len(extended) <= before_count:
            raise ValueError(f"内容规划续写失败：第 {start_page}-{end_page} 页没有推进。")

    for idx, page in enumerate(extended, start=1):
        page["page_num"] = idx
    return extended[:target_count]


def _generate_outline_from_blueprint_in_chunks(
    *,
    topic: str,
    documents: str,
    deck_blueprint: str,
    target_count: int,
    min_pages: int,
    max_pages: int,
    search_context: str = "",
    on_progress: Callable[[dict], None] | None = None,
) -> List[Dict]:
    target_count = max(min_pages, min(max_pages, int(target_count or max_pages)))
    client = get_llm_client()
    chunk_size = LONG_DECK_CHUNK_SIZE
    outline: list[dict] = []
    doc_excerpt = _document_excerpt_for_extension(documents, limit=12000)
    preservation_policy = _document_preservation_policy(documents, topic)
    source_page_plan = _source_page_section_plan(documents, target_count)
    source_section_first_pages = _source_section_first_pages(documents, target_count)

    logger.info(
        "ContentPlan: generating long deck in chunks, target=%s, range=%s-%s",
        target_count,
        min_pages,
        max_pages,
    )

    while len(outline) < target_count:
        start_page = len(outline) + 1
        end_page = min(target_count, start_page + chunk_size - 1)
        is_first_chunk = start_page == 1
        is_final_chunk = end_page >= target_count
        if on_progress:
            on_progress({
                "stage": "generating",
                "message": f"正在按全局结构生成第 {start_page}-{end_page}/{target_count} 页...",
                "current_page": len(outline),
                "total_pages": target_count,
            })

        prompt = f"""你是一位顶尖的商业演示架构师。请根据同一份全局蓝图，分段生成一份长 PPT 的详细内容规划。

【用户主题和约束】
{topic}

【全局蓝图（必须遵守）】
{deck_blueprint}

【已生成页面摘要】
{_outline_extension_summary(outline) or "无，这是第一组页面。"}

【用户上传材料摘录】
{doc_excerpt or "无"}

【实时搜索上下文】
{search_context or "无"}

【文档使用规则】
{preservation_policy or "没有上传文档时，根据用户主题和受众目标生成内容结构。"}

【本轮页码对应的原文章节（必须逐页遵守）】
{_format_source_page_plan(source_page_plan, start_page, end_page)}

【本轮任务】
只生成第 {start_page} 页到第 {end_page} 页，共 {end_page - start_page + 1} 页。
- page_num 必须从 {start_page} 连续编号到 {end_page}。
- 每页必须落在全局蓝图对应的章节和页码区间内。
- 如果上方给出了逐页原文章节，section_title 必须与对应页码一致；不能提前跳到后续章节，也不能重复上一章节。
- 不能重复已生成页面；要接上已有页面摘要的叙事。
- 每页必须包含 type、section_title、text_content、speaker_notes、visual_suggestion、source_refs。
- type 只能使用 cover、toc、section、content、data、hero、quote、ending；目录页用 toc，章节页用 section，普通论述/案例/自检/框架页用 content，含真实数字/表格/对比指标的页面用 data，hero 只用于无正文或极短正文的一句话金句/关键判断页，quote 用于名人名言/引用页；其他多段/长段正文必须用 content；禁止使用 outline、section_cover、framework、case、quiz、transition 等新类型。
- text_content.headline、subhead、body 中禁止出现 Markdown 标题符号（#、##、###）；正文列表必须逐条换行，不要把多个目录项、项目符号或编号压成同一行。
- text_content.body 是页面卡片/PPT 上可见的正文区域；speaker_notes 只是讲师备注，不会显示在页面正文里。
- content/data 页的 text_content.body 必须写得言之有物 —— 给听众具体的案例/数据/反例/类比，不是抽象概括；不要把丰富段落压成一行标题，也不要把多句压缩成一句。
- 【产出目标】PPT 应当准确体现当前用户的真实意图和诉求。源材料不等于产出，PPT 应当让用户的演讲更有力。如果用户意图不清晰，应当主动澄清而不是猜测。
- 避免的输出形态：连续多页都是 "label: content" 小标题加冒号格式（如“现状：xxx / 原因：xxx / 工具：xxx”）；把源材料的“按主题分类”结构原样搬过来当 PPT 结构；每页形式一样没有节奏感。
- 本轮 content/data 页的 headline 不得与【已生成页面摘要】中的任何 headline 重复；同一主题拆成多页时，要用不同标题表达不同角度、动作或问题。
- {"第 1 页必须是 cover 封面页，body 保持为空。" if is_first_chunk else "本轮不是封面段，不要再生成 cover。"}
- {"第 " + str(end_page) + " 页必须是 ending 封底页，用于收束全场。" if is_final_chunk else "本轮不是最后一组页面，不要生成 ending 封底页。"}
- 长篇页数要靠论点、案例、方法和过渡自然展开，不能灌水。
- 不要使用“续2”“续3”这类机械标题；每页标题必须承载具体判断、问题或原文概念。
- 封面标题必须使用真实课程主题，不要写成“封面”“Cover”或页面类型标签。
- 演讲备注必须具体。演讲备注必须先写出这一页需要讲什么：关键事实、原文细节、案例、数据、解释和结论；再补充必要的讲述节奏或转场，禁止只写“先复述本页判断”这类通用模板。

严格输出 JSON 数组，不要包含 Markdown 代码块标记。"""

        response = client.chat.completions.create(
            model=get_minimax_llm_model(),
            messages=[
                {"role": "system", "content": "你是世界一流的 PPT 架构师。必须且只能输出合法的 JSON 数组，严禁添加额外说明文本。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.45,
            timeout=90.0,
        )
        raw = response.choices[0].message.content or ""
        new_pages = _parse_outline_json_response(raw)
        if not new_pages:
            raise ValueError(f"内容规划分段生成失败：第 {start_page}-{end_page} 页没有返回可用页面。")

        before_count = len(outline)
        chunk_pages = _prepare_long_deck_chunk_pages(
            new_pages,
            existing_count=len(outline),
            target_count=target_count,
            source_page_plan=source_page_plan,
            source_section_first_pages=source_section_first_pages,
            topic=topic,
        )
        if not chunk_pages:
            raise ValueError(f"内容规划分段生成失败：第 {start_page}-{end_page} 页没有推进。")
        try:
            _assert_long_deck_chunk_contract(chunk_pages, start_page=start_page, end_page=end_page, existing_pages=outline)
        except ValueError as exc:
            logger.warning(
                "ContentPlan: retrying long deck chunk %s-%s after contract failure: %s",
                start_page,
                end_page,
                exc,
            )
            correction_prompt = f"""上一轮第 {start_page}-{end_page} 页内容规划不合格，错误如下：
{exc}

请重新生成第 {start_page} 页到第 {end_page} 页。必须修正上述问题，尤其：
- text_content.body 是页面卡片/PPT 上可见的正文区域；content/data 页必须把页面可见正文写在 text_content.body，不得留空。
- text_content.body 要写得言之有物（具体案例/数据/反例，不是抽象概括）。PPT 应当准确体现用户的真实意图；不要套用 "label: content" 模板（"现状：xxx / 原因：xxx / 工具：xxx"）。
- headline 不能与已生成页面重复；同主题多页拆解时，用不同角度、动作或问题命名。
- section_title 必须遵守下面的逐页原文章节。

【用户主题和约束】
{topic}

【全局蓝图】
{deck_blueprint}

【已生成页面摘要】
{_outline_extension_summary(outline) or "无，这是第一组页面。"}

【本轮页码对应的原文章节】
{_format_source_page_plan(source_page_plan, start_page, end_page)}

【用户上传材料摘录】
{doc_excerpt or "无"}

严格输出 JSON 数组，不要包含 Markdown 代码块标记。"""
            response = client.chat.completions.create(
                model=get_minimax_llm_model(),
                messages=[
                    {"role": "system", "content": "你是世界一流的 PPT 架构师。必须且只能输出合法的 JSON 数组，严禁添加额外说明文本。"},
                    {"role": "user", "content": correction_prompt},
                ],
                temperature=0.35,
                timeout=90.0,
            )
            retry_raw = response.choices[0].message.content or ""
            retry_pages = _parse_outline_json_response(retry_raw)
            if not retry_pages:
                raise ValueError(f"内容规划分段生成失败：第 {start_page}-{end_page} 页重试后没有返回可用页面。") from exc
            chunk_pages = _prepare_long_deck_chunk_pages(
                retry_pages,
                existing_count=len(outline),
                target_count=target_count,
                source_page_plan=source_page_plan,
                source_section_first_pages=source_section_first_pages,
                topic=topic,
            )
            if not chunk_pages:
                raise ValueError(f"内容规划分段生成失败：第 {start_page}-{end_page} 页重试后没有推进。") from exc
            _assert_long_deck_chunk_contract(chunk_pages, start_page=start_page, end_page=end_page, existing_pages=outline)
        outline.extend(chunk_pages)
        if len(outline) <= before_count:
            raise ValueError(f"内容规划分段生成失败：第 {start_page}-{end_page} 页没有推进。")

    for idx, page in enumerate(outline, start=1):
        page["page_num"] = idx
    return outline[:target_count]


def generate_long_deck_outline_chunk(
    *,
    topic: str,
    documents: str,
    deck_blueprint: str,
    existing_outline: list[dict],
    skeleton_chunk: list[dict],
    target_count: int,
    start_page: int,
    end_page: int,
    search_context: str = "",
    timeout_seconds: float = LONG_DECK_CHUNK_TIMEOUT_SECONDS,
) -> list[dict]:
    """Generate one long-deck chunk. The caller owns persistence and fallback."""
    target_count = max(1, int(target_count or end_page or 1))
    start_page = max(1, int(start_page))
    end_page = min(target_count, int(end_page))
    is_first_chunk = start_page == 1
    is_final_chunk = end_page >= target_count
    client = get_llm_client()
    doc_excerpt = _document_excerpt_for_extension(documents, limit=10000)
    preservation_policy = _document_preservation_policy(documents, topic)
    skeleton_summary = _outline_extension_summary(skeleton_chunk, limit=max(20, len(skeleton_chunk)))
    source_page_plan = _source_page_section_plan(documents, target_count)
    source_section_first_pages = _source_section_first_pages(documents, target_count)

    prompt = f"""你是一位顶尖的商业演示架构师。请根据同一份全局蓝图，分段补齐一份长 PPT 的详细内容规划。

【用户主题和约束】
{topic}

【全局蓝图（必须遵守）】
{deck_blueprint}

【已生成页面摘要】
{_outline_extension_summary(existing_outline) or "无，这是第一组页面。"}

【本组已有骨架】
{skeleton_summary}

【用户上传材料摘录】
{doc_excerpt or "无"}

【实时搜索上下文】
{search_context or "无"}

【文档使用规则】
{preservation_policy or "没有上传文档时，根据用户主题和受众目标生成内容结构。"}

【本轮页码对应的原文章节（必须逐页遵守）】
{_format_source_page_plan(source_page_plan, start_page, end_page)}

【本轮任务】
只生成第 {start_page} 页到第 {end_page} 页，共 {end_page - start_page + 1} 页。
- page_num 必须从 {start_page} 连续编号到 {end_page}。
- 每页必须落在全局蓝图对应的章节和页码区间内。
- 如果上方给出了逐页原文章节，section_title 必须与对应页码一致；不能提前跳到后续章节，也不能重复上一章节。
- 不能重复已生成页面；要接上已有页面摘要的叙事。
- 每页必须包含 type、section_title、text_content、speaker_notes、visual_suggestion、source_refs。
- type 只能使用 cover、toc、section、content、data、hero、quote、ending；目录页用 toc，章节页用 section，普通论述/案例/自检/框架页用 content，含真实数字/表格/对比指标的页面用 data，hero 只用于无正文或极短正文的一句话金句/关键判断页，quote 用于名人名言/引用页；其他多段/长段正文必须用 content；禁止使用 outline、section_cover、framework、case、quiz、transition 等新类型。
- text_content.headline、subhead、body 中禁止出现 Markdown 标题符号（#、##、###）；正文列表必须逐条换行，不要把多个目录项、项目符号或编号压成同一行。
- text_content.body 是页面卡片/PPT 上可见的正文区域；speaker_notes 只是讲师备注，不会显示在页面正文里。
- content/data 页的 text_content.body 必须写得言之有物 —— 给听众具体的案例/数据/反例/类比，不是抽象概括；不要把丰富段落压成一行标题，也不要把多句压缩成一句。
- 【产出目标】PPT 应当准确体现当前用户的真实意图和诉求。源材料不等于产出，PPT 应当让用户的演讲更有力。如果用户意图不清晰，应当主动澄清而不是猜测。
- 避免的输出形态：连续多页都是 "label: content" 小标题加冒号格式（如“现状：xxx / 原因：xxx / 工具：xxx”）；把源材料的“按主题分类”结构原样搬过来当 PPT 结构；每页形式一样没有节奏感。
- 本轮 content/data 页的 headline 不得与【已生成页面摘要】中的任何 headline 重复；同一主题拆成多页时，要用不同标题表达不同角度、动作或问题。
- {"第 1 页必须是 cover 封面页，body 保持为空。" if is_first_chunk else "本轮不是封面段，不要再生成 cover。"}
- {"第 " + str(end_page) + " 页必须是 ending 封底页，用于收束全场。" if is_final_chunk else "本轮不是最后一组页面，不要生成 ending 封底页。"}
- 长篇页数要靠论点、案例、方法和过渡自然展开，不能灌水。
- 不要使用“续2”“续3”这类机械标题；每页标题必须承载具体判断、问题或原文概念。
- 封面标题必须使用真实课程主题，不要写成“封面”“Cover”或页面类型标签。
- 演讲备注必须具体。演讲备注必须先写出这一页需要讲什么：关键事实、原文细节、案例、数据、解释和结论；再补充必要的讲述节奏或转场，禁止只写“先复述本页判断”这类通用模板。

严格输出 JSON 数组，不要包含 Markdown 代码块标记。"""

    started_at = time.monotonic()
    logger.info(
        "[ContentPlan] Long deck chunk request start range=%s-%s/%s model=%s input_chars=%s timeout=%ss",
        start_page,
        end_page,
        target_count,
        get_minimax_llm_model(),
        len(prompt),
        timeout_seconds,
    )
    response = client.chat.completions.create(
        model=get_minimax_llm_model(),
        messages=[
            {"role": "system", "content": "你是世界一流的 PPT 架构师。必须且只能输出合法的 JSON 数组，严禁添加额外说明文本。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.45,
        timeout=timeout_seconds,
    )
    raw = response.choices[0].message.content or ""
    logger.info(
        "[ContentPlan] Long deck chunk request done range=%s-%s/%s elapsed=%.2fs output_chars=%s",
        start_page,
        end_page,
        target_count,
        time.monotonic() - started_at,
        len(raw),
    )
    new_pages = _parse_outline_json_response(raw)
    if not new_pages:
        raise ValueError(f"内容规划分段生成失败：第 {start_page}-{end_page} 页没有返回可用页面。")

    normalized: list[dict] = []
    expected_page = start_page
    for page in new_pages:
        if expected_page > end_page:
            break
        page["page_num"] = expected_page
        _enforce_source_page_section(
            page,
            page_num=expected_page,
            target_count=target_count,
            source_page_plan=source_page_plan,
            source_section_first_pages=source_section_first_pages,
        )
        if expected_page == 1:
            page["type"] = "cover"
            text_content = page.get("text_content") if isinstance(page.get("text_content"), dict) else {}
            text_content["body"] = ""
            page["text_content"] = text_content
        elif expected_page < target_count and str(page.get("type") or "").lower() == "ending":
            page["type"] = "content"
        elif expected_page == target_count:
            page["type"] = "ending"
        page["generation_status"] = "drafted"
        normalized.append(page)
        expected_page += 1

    normalized = _normalize_content_markdown(normalized, topic=topic)
    _assert_long_deck_chunk_contract(normalized, start_page=start_page, end_page=end_page, existing_pages=existing_outline)
    if len(normalized) != end_page - start_page + 1:
        raise ValueError(
            f"内容规划分段生成失败：第 {start_page}-{end_page} 页只返回 {len(normalized)} 页。"
        )
    return normalized


def _enforce_requested_page_range(outline: List[Dict], requested_page_range: tuple[int, int] | None) -> List[Dict]:
    if not requested_page_range:
        return outline
    min_pages, max_pages = requested_page_range
    upper_bound = max_pages if min_pages <= 1 else _requested_range_soft_upper(min_pages, max_pages)
    if len(outline) > upper_bound:
        logger.warning(
            "ContentPlan: LLM returned %s pages, trimming to requested soft range max %s",
            len(outline),
            upper_bound,
        )
        closing_page = outline[-1] if isinstance(outline[-1], dict) else None
        if upper_bound >= 2 and closing_page and str(closing_page.get("type") or "").lower() == "ending":
            outline = outline[: upper_bound - 1] + [{**closing_page}]
        else:
            outline = outline[:upper_bound]
    for idx, page in enumerate(outline, start=1):
        if isinstance(page, dict):
            page["page_num"] = idx
            page_type = str(page.get("type") or "content").strip().lower() or "content"
            if idx == 1:
                if page_type != "cover" and _page_map_preserves_source_page_type(page):
                    page["type"] = page_type if page_type != "ending" else "content"
                else:
                    page["type"] = "cover"
            elif idx == len(outline) and len(outline) > 1:
                if page_type != "ending" and _page_map_preserves_source_page_type(page):
                    page["type"] = page_type if page_type != "cover" else "section"
                else:
                    page["type"] = "ending"
            elif page_type == "cover":
                page["type"] = "section"
            elif page_type == "ending":
                page["type"] = "content"
    return outline


GENERIC_CONTENT_HEADLINES = {
    "封面",
    "cover",
    "目录",
    "课程目录",
    "outline",
    "内容",
    "content",
    "无标题",
}


def _title_from_source_refs(value) -> str:
    candidates: list[str] = []
    if isinstance(value, str):
        candidates.append(value)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                candidates.append(item)
            elif isinstance(item, dict):
                candidates.extend(str(item.get(key) or "") for key in ("source_document", "document", "filename", "reason"))
    elif isinstance(value, dict):
        candidates.extend(str(value.get(key) or "") for key in ("source_document", "document", "filename", "reason"))

    for candidate in candidates:
        text = _clean_markdown_inline(candidate)
        text = re.sub(r"^材料文件[:：]\s*", "", text)
        text = re.split(r"\s+-\s+|\s+>\s+|[；;]", text, maxsplit=1)[0].strip()
        text = re.sub(r"\.(?:pdf|docx?|pptx?|md|markdown|txt)$", "", text, flags=re.IGNORECASE)
        text = re.sub(
            r"(?:完整)?(?:演讲内容稿|演讲稿|内容稿|课程稿|讲稿|文案|逐字稿|完整稿)$",
            "",
            text,
        ).strip(" _-—：:，,。；;")
        if 2 <= len(text) <= 36 and text.lower() not in GENERIC_CONTENT_HEADLINES:
            return text
    return ""


def _title_from_speaker_notes(value: str) -> str:
    text = _clean_markdown_inline(value)
    for pattern in (
        r"主题(?:叫|是|为)\s*[\"'“‘《]?([^\"'”’》。，；;\n]{2,36})",
        r"分享(?:的)?主题(?:叫|是|为)\s*[\"'“‘《]?([^\"'”’》。，；;\n]{2,36})",
    ):
        match = re.search(pattern, text)
        if match:
            title = match.group(1).strip(" \"'“”‘’《》：:，,。；;")
            if title.lower() not in GENERIC_CONTENT_HEADLINES:
                return title
    return ""


def _extract_headline_from_text_content(value: str) -> tuple[str, str]:
    text = normalize_markdown_content(str(value or "")).strip()
    if not text:
        return "", ""
    lines = [line.strip() for line in text.splitlines()]
    first_idx = next((idx for idx, line in enumerate(lines) if line), -1)
    if first_idx < 0:
        return "", text
    first_line = _strip_leading_list_marker(lines[first_idx])
    bracket_match = re.match(r"^【(.{2,60})】$", first_line)
    if bracket_match:
        headline = _clean_markdown_inline(bracket_match.group(1)).strip()
        body = "\n".join(lines[first_idx + 1:]).strip()
        return headline, body
    if 2 <= len(first_line) <= 60 and first_line.lower() not in GENERIC_CONTENT_HEADLINES:
        first_line = _clean_markdown_inline(first_line)
        body = "\n".join(lines[first_idx + 1:]).strip()
        return first_line, body or text
    return "", text


def _canonical_content_plan_type(value: str) -> str:
    raw = str(value or "").strip()
    key = raw.lower().replace("-", "_").replace(" ", "_")
    compact_key = re.sub(r"[\s_-]+", "", raw.lower())
    if key in CANONICAL_CONTENT_PLAN_TYPES:
        return key
    return CONTENT_PLAN_TYPE_ALIASES.get(key) or CONTENT_PLAN_TYPE_ALIASES.get(compact_key) or "content"


def _strip_markdown_heading_markers(text: str) -> str:
    cleaned_lines: list[str] = []
    for line in str(text or "").splitlines():
        cleaned_lines.append(re.sub(r"^\s{0,3}#{1,6}\s+", "", line).strip())
    return "\n".join(cleaned_lines).strip()


_DIGIT_COMPACT_LIST_MARKER_RE = re.compile(r"(?<![\d.])(\d{1,2})([.)、])\s+")
_CHINESE_COMPACT_LIST_MARKER_RE = re.compile(r"([一二三四五六七八九十]{1,3})、")
_UNORDERED_COMPACT_LIST_MARKER_RE = re.compile(r"(?:(?<=^)|(?<=[\s:：；;。.!！?？]))([-*•■□])\s+")
_BRACKET_SECTION_MARKER_RE = re.compile(r"【[^】]{1,30}】")
_COMPACT_LIST_PREFIX_ENDINGS = ("：", ":", "；", ";", "。", ".", "！", "!", "？", "?", "】")


def _split_compact_marker_line(line: str, matches: list[re.Match]) -> str:
    if not matches:
        return line
    parts: list[str] = []
    prefix = line[:matches[0].start()].rstrip()
    if prefix:
        parts.append(prefix)
    for idx, match in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(line)
        part = line[match.start():end].strip()
        if part:
            parts.append(part)
    return "\n".join(parts) if parts else line


def _sequence_is_incrementing(values: list[int]) -> bool:
    return len(values) >= 2 and all(values[idx + 1] == values[idx] + 1 for idx in range(len(values) - 1))


def _chinese_ordinal_value(value: str) -> int:
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    text = str(value or "")
    if text in digits:
        return digits[text]
    if text == "十":
        return 10
    if text.startswith("十") and len(text) == 2:
        return 10 + digits.get(text[1], 0)
    if text.endswith("十") and len(text) == 2:
        return digits.get(text[0], 0) * 10
    if "十" in text and len(text) == 3:
        left, right = text.split("十", 1)
        return digits.get(left, 0) * 10 + digits.get(right, 0)
    return 0


def _restore_compact_ordered_list_line(line: str) -> str:
    matches = list(_DIGIT_COMPACT_LIST_MARKER_RE.finditer(line))
    values = [int(match.group(1)) for match in matches]
    if not _sequence_is_incrementing(values):
        return line
    return _split_compact_marker_line(line, matches)


def _restore_compact_chinese_ordered_list_line(line: str) -> str:
    matches = list(_CHINESE_COMPACT_LIST_MARKER_RE.finditer(line))
    values = [_chinese_ordinal_value(match.group(1)) for match in matches]
    if not values or any(value <= 0 for value in values) or not _sequence_is_incrementing(values):
        return line
    return _split_compact_marker_line(line, matches)


def _restore_compact_unordered_list_line(line: str) -> str:
    matches = list(_UNORDERED_COMPACT_LIST_MARKER_RE.finditer(line))
    if len(matches) < 2:
        return line
    prefix = line[:matches[0].start()].rstrip()
    if prefix and not prefix.endswith(_COMPACT_LIST_PREFIX_ENDINGS):
        return line
    return _split_compact_marker_line(line, matches)


def _restore_compact_bracket_section_line(line: str) -> str:
    matches = list(_BRACKET_SECTION_MARKER_RE.finditer(line))
    if len(matches) < 2:
        return line
    prefix = line[:matches[0].start()].rstrip()
    if prefix and not prefix.endswith(_COMPACT_LIST_PREFIX_ENDINGS):
        return line
    return _split_compact_marker_line(line, matches)


def _restore_compact_list_line_breaks(text: str) -> str:
    repaired_lines: list[str] = []
    for line in str(text or "").splitlines():
        if "|" in line and re.search(r"\|.*\|", line):
            repaired_lines.append(line.rstrip())
            continue
        if re.match(r"^\s*(?:[-*+]|\d+[.)、])\s+", line):
            repaired_lines.append(line.rstrip())
            continue
        repaired = line.rstrip()
        for repair in (
            _restore_compact_ordered_list_line,
            _restore_compact_chinese_ordered_list_line,
            _restore_compact_unordered_list_line,
            _restore_compact_bracket_section_line,
        ):
            next_repaired = repair(repaired)
            if next_repaired != repaired:
                repaired = next_repaired
                break
        repaired_lines.extend(repaired.splitlines())
    return "\n".join(repaired_lines).strip()


def _canonicalize_body_list_markers(text: str) -> str:
    lines: list[str] = []
    for line in str(text or "").splitlines():
        lines.append(re.sub(r"^(\s*)\+\s+", r"\1- ", line.rstrip()))
    return "\n".join(lines).strip()


def _normalize_body_markdown(value: str) -> str:
    text = normalize_markdown_content(str(value or ""))
    text = _strip_markdown_heading_markers(text)
    text = _restore_compact_list_line_breaks(text)
    text = _canonicalize_body_list_markers(text)
    return text.strip()


def _body_text(value) -> str:
    if isinstance(value, list):
        return "\n".join(str(item or "") for item in value)
    return str(value or "")


def _empty_required_content_body_pages(outline: list[dict]) -> list[int]:
    missing: list[int] = []
    for idx, page in enumerate(outline, start=1):
        if not isinstance(page, dict):
            continue
        page_type = _canonical_content_plan_type(page.get("type") or "content")
        if page_type not in CONTENT_BODY_REQUIRED_TYPES:
            continue
        text_content = page.get("text_content") if isinstance(page.get("text_content"), dict) else {}
        if len(re.sub(r"\s", "", _body_text(text_content.get("body")))) == 0:
            missing.append(int(page.get("page_num") or idx))
    return missing


def _content_body_bullet_count(value) -> int:
    lines = [
        _strip_leading_list_marker(line).strip()
        for line in _body_text(value).splitlines()
        if _strip_leading_list_marker(line).strip()
    ]
    return len(lines)


def _thin_required_content_body_pages(outline: list[dict]) -> list[int]:
    thin: list[int] = []
    source_statuses = {
        "page_map_model",
        "page_map_source",
        "source_draft",
        "source_paginated_markdown",
        "source_exported_plan",
        "page_map_model_with_source_body",
    }
    for idx, page in enumerate(outline, start=1):
        if not isinstance(page, dict):
            continue
        status = str(page.get("generation_status") or "").strip()
        if status not in source_statuses:
            continue
        page_type = _canonical_content_plan_type(page.get("type") or "content")
        if page_type not in CONTENT_BODY_REQUIRED_TYPES:
            continue
        text_content = page.get("text_content") if isinstance(page.get("text_content"), dict) else {}
        if _content_body_bullet_count(text_content.get("body")) < 2:
            thin.append(int(page.get("page_num") or idx))
    return thin


def _headline_contract_key(value: str) -> str:
    text = _clean_headline_text(str(value or ""))
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[，。；：:、,.!?！？“”\"'《》（）()【】\[\]\-—_]+", "", text)
    return text


def _duplicate_content_headline_pages(outline: list[dict]) -> list[dict]:
    seen: dict[str, int] = {}
    duplicates: list[dict] = []
    for idx, page in enumerate(outline, start=1):
        if not isinstance(page, dict):
            continue
        page_type = _canonical_content_plan_type(page.get("type") or "content")
        if page_type not in CONTENT_BODY_REQUIRED_TYPES:
            continue
        text_content = page.get("text_content") if isinstance(page.get("text_content"), dict) else {}
        key = _headline_contract_key(str(text_content.get("headline") or ""))
        if len(key) < 6:
            continue
        page_num = int(page.get("page_num") or idx)
        if key in seen:
            duplicates.append({"headline": str(text_content.get("headline") or ""), "pages": [seen[key], page_num]})
        else:
            seen[key] = page_num
    return duplicates


def _body_contract_key(value) -> str:
    text = _body_text(value)
    text = _clean_headline_text(text)
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[，。；：:、,.!?！？“”\"'《》（）()【】\[\]\-—_]+", "", text)
    return text[:140]


def _headline_candidate_from_body_line(value: str, *, max_chars: int = 34) -> str:
    text = _strip_leading_list_marker(str(value or ""))
    text = normalize_markdown_emphasis(text).strip()
    text = re.sub(r"^(?:关键判断|核心观点|本页要点|要点|结论|主张)\s*[:：]\s*", "", text)
    text = _clean_headline_text(text)
    if not text:
        return ""
    segments = [
        segment.strip()
        for segment in re.split(r"[。；;！？?\n]", text)
        if segment.strip()
    ]
    if segments:
        text = segments[0]
    comma_parts = [part.strip() for part in re.split(r"[，,、]", text) if part.strip()]
    if comma_parts and len(text) > max_chars:
        text = "，".join(comma_parts[:2]).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip("，,、：:；;。.")
    return text.strip()


def _duplicate_headline_candidate(page: dict, used_keys: set[str]) -> str:
    text_content = page.get("text_content") if isinstance(page.get("text_content"), dict) else {}
    body = text_content.get("body")
    lines = body if isinstance(body, list) else str(body or "").splitlines()
    candidates: list[str] = []
    for line in lines:
        candidate = _headline_candidate_from_body_line(str(line))
        if candidate:
            candidates.append(candidate)
    subhead = _headline_candidate_from_body_line(str(text_content.get("subhead") or ""))
    if subhead:
        candidates.append(subhead)
    section_title = _headline_candidate_from_body_line(str(page.get("section_title") or ""))
    if section_title:
        candidates.append(section_title)

    for candidate in candidates:
        key = _headline_contract_key(candidate)
        if len(key) >= 6 and key not in used_keys:
            return candidate
    return ""


def _dedupe_content_headlines(outline: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    used_keys: set[str] = set()
    for idx, page in enumerate(outline, start=1):
        if not isinstance(page, dict):
            continue
        page_type = _canonical_content_plan_type(page.get("type") or "content")
        if page_type not in CONTENT_BODY_REQUIRED_TYPES:
            continue
        text_content = page.get("text_content") if isinstance(page.get("text_content"), dict) else {}
        headline = str(text_content.get("headline") or "")
        key = _headline_contract_key(headline)
        if len(key) < 6:
            continue
        body_key = _body_contract_key(text_content.get("body"))
        if key not in seen:
            seen[key] = {"page": page, "body_key": body_key, "page_num": int(page.get("page_num") or idx)}
            used_keys.add(key)
            continue

        first = seen[key]
        first_body_key = str(first.get("body_key") or "")
        if body_key and first_body_key and body_key == first_body_key:
            continue
        candidate = _duplicate_headline_candidate(page, used_keys)
        candidate_key = _headline_contract_key(candidate)
        if candidate and len(candidate_key) >= 6 and candidate_key not in used_keys:
            text_content["headline"] = candidate
            page["text_content"] = text_content
            seen[candidate_key] = {"page": page, "body_key": body_key, "page_num": int(page.get("page_num") or idx)}
            used_keys.add(candidate_key)
    return outline


def _assert_long_deck_chunk_contract(
    pages: list[dict],
    *,
    start_page: int,
    end_page: int,
    existing_pages: list[dict] | None = None,
) -> None:
    empty_body_pages = _empty_required_content_body_pages(pages)
    if empty_body_pages:
        raise ValueError(
            f"内容规划分段生成失败：第 {start_page}-{end_page} 页中 "
            + "、".join(f"P{page}" for page in empty_body_pages)
            + " 的正文为空。content/data 页必须把页面可见正文写入 text_content.body，不能只写 speaker_notes。"
        )
    duplicates = _duplicate_content_headline_pages([*(existing_pages or []), *pages])
    if duplicates:
        duplicate = duplicates[0]
        pages_label = "、".join(f"P{page}" for page in duplicate["pages"])
        raise ValueError(
            f"内容规划分段生成失败：{pages_label} 的内容页标题重复「{duplicate['headline']}」。"
            "同一主题拆成多页时，标题必须体现不同角度、动作或问题。"
        )


def _prepare_long_deck_chunk_pages(
    new_pages: list[dict],
    *,
    existing_count: int,
    target_count: int,
    source_page_plan: dict[int, str],
    source_section_first_pages: set[int],
    topic: str,
) -> list[dict]:
    chunk_pages: list[dict] = []
    for page in new_pages:
        if existing_count + len(chunk_pages) >= target_count:
            break
        next_page_num = existing_count + len(chunk_pages) + 1
        page["page_num"] = next_page_num
        _enforce_source_page_section(
            page,
            page_num=next_page_num,
            target_count=target_count,
            source_page_plan=source_page_plan,
            source_section_first_pages=source_section_first_pages,
        )
        if next_page_num == 1:
            page["type"] = "cover"
            text_content = page.get("text_content") if isinstance(page.get("text_content"), dict) else {}
            text_content["body"] = ""
            page["text_content"] = text_content
        elif next_page_num < target_count and str(page.get("type") or "").lower() == "ending":
            page["type"] = "content"
        elif next_page_num == target_count:
            page["type"] = "ending"
        chunk_pages.append(page)
    return _normalize_content_markdown(chunk_pages, topic=topic) if chunk_pages else []


def _clean_headline_text(value: str) -> str:
    text = normalize_markdown_content(str(value or "")).strip()
    if not text:
        return ""
    cleaned_lines: list[str] = []
    for line in text.splitlines():
        cleaned = re.sub(r"^\s{0,3}#{1,6}\s+", "", line).strip()
        for _ in range(2):
            cleaned = re.sub(r"^\*{1,3}(.+?)\*{1,3}$", r"\1", cleaned).strip()
            cleaned = re.sub(r"^_{1,3}(.+?)_{1,3}$", r"\1", cleaned).strip()
        cleaned_lines.append(cleaned)
    return "\n".join(cleaned_lines).strip()


def _page_fallback_headline(page: dict, *, topic: str = "", body: str = "") -> str:
    for key in ("headline", "title"):
        value = _clean_markdown_inline(str(page.get(key) or ""))
        if value and value.lower() not in GENERIC_CONTENT_HEADLINES:
            return value

    page_type = str(page.get("type") or "").strip().lower()
    if page_type == "cover":
        for value in (
            _title_from_speaker_notes(str(page.get("speaker_notes") or "")),
            _title_from_source_refs(page.get("source_refs")),
            _brief_title(topic),
        ):
            if value and value.lower() not in GENERIC_CONTENT_HEADLINES:
                return value

    body_headline, _body = _extract_headline_from_text_content(body)
    if body_headline and body_headline.lower() not in GENERIC_CONTENT_HEADLINES:
        return body_headline

    section_title = _clean_markdown_inline(str(page.get("section_title") or ""))
    if section_title and section_title.lower() not in GENERIC_CONTENT_HEADLINES:
        return section_title

    source_title = _title_from_source_refs(page.get("source_refs"))
    if source_title:
        return source_title

    brief_title = _brief_title(topic)
    if brief_title and brief_title.lower() not in GENERIC_CONTENT_HEADLINES:
        return brief_title

    page_num = page.get("page_num") or ""
    return f"第 {page_num} 页".strip()


def _normalize_content_markdown(outline: List[Dict], *, topic: str = "") -> List[Dict]:
    """Normalize Markdown generated by the LLM before it becomes project state."""
    for page in outline:
        if not isinstance(page, dict):
            continue
        original_type = str(page.get("type") or "content")
        page["type"] = _canonical_content_plan_type(original_type)
        text_content = page.get("text_content")
        if isinstance(text_content, dict):
            for key in ("headline", "subhead", "body"):
                value = text_content.get(key)
                if isinstance(value, str):
                    normalized_value = _normalize_body_markdown(value) if key == "body" else normalize_markdown_content(value)
                    normalized_value = _strip_source_context_markers(normalized_value)
                    text_content[key] = _clean_headline_text(normalized_value) if key in {"headline", "subhead"} else normalized_value
                elif isinstance(value, list):
                    text_content[key] = [
                        _strip_source_context_markers(_normalize_body_markdown(item)) if key == "body" and isinstance(item, str)
                        else _strip_source_context_markers(normalize_markdown_content(item)) if isinstance(item, str)
                        else item
                        for item in value
                    ]
            body_value = text_content.get("body")
            body_text = "\n".join(str(item) for item in body_value) if isinstance(body_value, list) else str(body_value or "")
            if not str(text_content.get("headline") or "").strip():
                text_content["headline"] = _page_fallback_headline(page, topic=topic, body=body_text)
            if "subhead" not in text_content:
                text_content["subhead"] = ""
            if "body" not in text_content:
                text_content["body"] = ""
        else:
            headline, body = _extract_headline_from_text_content(str(text_content or ""))
            page["text_content"] = {
                "headline": headline or _page_fallback_headline(page, topic=topic, body=body),
                "subhead": "",
                "body": _normalize_body_markdown(body),
            }
        text_content = page.get("text_content") if isinstance(page.get("text_content"), dict) else {}
        if _should_reclassify_content_page_as_toc(
            page_type=str(page.get("type") or ""),
            section_title=str(page.get("section_title") or ""),
            headline=str(text_content.get("headline") or ""),
            body_lines=_body_lines_for_type_inference(text_content.get("body")),
        ):
            page["type"] = "toc"
        elif str(page.get("type") or "").strip().lower() == "hero":
            reclassified_type = _auto_reclassify_page_type(page, "hero", original_type=original_type)
            if reclassified_type:
                page["type"] = reclassified_type
        notes = page.get("speaker_notes")
        if isinstance(notes, str):
            page["speaker_notes"] = _strip_source_context_markers(normalize_markdown_content(notes))
        page["source_refs"] = _clean_page_map_source_ref_values(page.get("source_refs"))
        _normalize_punchline_page_content(page, original_type=original_type)
        separated = separate_visual_directives_from_page(page)
        if separated is not page:
            page.clear()
            page.update(separated)
    return outline


def _first_text_line(value) -> str:
    if isinstance(value, list):
        candidates = [str(item) for item in value if str(item).strip()]
    else:
        candidates = str(value or "").splitlines()
    for line in candidates:
        cleaned = _strip_leading_list_marker(str(line))
        cleaned = normalize_markdown_emphasis(cleaned).strip()
        if cleaned:
            return cleaned
    return ""


def _normalize_punchline_page_content(page: Dict, *, original_type: str | None = None) -> None:
    """Keep hero/quote slides as punchline slides, not lightweight content slides."""
    page_type = str(page.get("type") or "").strip().lower()
    if page_type not in {"hero", "quote"}:
        return

    text_content = page.get("text_content")
    if not isinstance(text_content, dict):
        return

    headline = _first_text_line(text_content.get("headline"))
    subhead = _first_text_line(text_content.get("subhead"))
    body = text_content.get("body")
    body_line = _first_text_line(body)

    if not headline and body_line:
        headline = body_line

    body_text = "\n".join(str(x) for x in body) if isinstance(body, list) else str(body or "")
    if _is_attributed_quote_page(page, original_type=original_type):
        text_content["headline"] = headline
        text_content["subhead"] = subhead
        if isinstance(body, list):
            text_content["body"] = "\n".join(str(x).strip() for x in body if str(x).strip())
        else:
            text_content["body"] = normalize_markdown_emphasis(body_text.strip()) if body_text.strip() else ""
        suggestion = str(page.get("visual_suggestion") or "").strip()
        if not suggestion or any(term in suggestion for term in ("内容页", "信息图", "列表", "要点")):
            page["visual_suggestion"] = (
                "名人名言/引用金句页：保留引文正文与作者署名；"
                "如有明确人物，可将人物肖像作为背景的一部分，低对比度融入版面，"
                "同时沿用整套 PPT 的配色、字体气质和材质语言。"
            )
        return

    if body_text.strip():
        notes = str(page.get("speaker_notes") or "").strip()
        preserved = f"原金句页正文素材：{normalize_markdown_emphasis(body_text.strip())}"
        page["speaker_notes"] = f"{notes}\n\n{preserved}".strip() if notes else preserved

    text_content["headline"] = headline
    text_content["subhead"] = subhead
    text_content["body"] = ""

    suggestion = str(page.get("visual_suggestion") or "").strip()
    if not suggestion or any(term in suggestion for term in ("内容页", "信息图", "列表", "要点")):
        page["visual_suggestion"] = (
            "真正的金句页：只突出一句话、一个短语或一个词；"
            "可走引用、口号、结论、转场判断、数据洞察等方向，"
            "视觉用克制背景、象征物、人物/场景或纯色纹理承托，"
            "必须沿用整套 PPT 的配色、字体气质和材质语言。"
        )


def _emit_content_plan_saved(on_progress: Callable[[dict], None] | None, outline: list[dict]) -> None:
    if on_progress:
        on_progress({
            "stage": "saving",
            "message": "正在保存结果...",
            "current_page": len(outline),
            "total_pages": len(outline),
        })


def _execute_reuse_content_plan_strategy(
    job: ContentPlanJob,
    strategy: str,
    on_progress: Callable[[dict], None] | None = None,
) -> list[dict]:
    if strategy == CONTENT_PLAN_STRATEGY_REUSE_EXPORTED:
        outline = job.exported_outline or []
        logger.info("ContentPlan: detected PPT God Markdown export, reusing %s pages directly", len(outline))
        if on_progress:
            on_progress({
                "stage": "saving",
                "message": "已识别导出的内容规划，正在保存结果...",
                "current_page": len(outline),
                "total_pages": len(outline),
            })
        return outline

    if strategy == CONTENT_PLAN_STRATEGY_REUSE_PAGINATED:
        outline = job.paginated_markdown_outline or []
        logger.info("ContentPlan: detected paginated Markdown draft, reusing %s pages directly", len(outline))
        if on_progress:
            on_progress({
                "stage": "saving",
                "message": "已按上传分页稿整理，正在保存结果...",
                "current_page": len(outline),
                "total_pages": len(outline),
            })
        return outline

    raise ValueError(f"Unsupported reuse content plan strategy: {strategy}")


def _execute_long_structured_deck_strategy(
    job: ContentPlanJob,
    on_progress: Callable[[dict], None] | None = None,
) -> list[dict]:
    deck_blueprint = _generate_deck_blueprint(
        topic=job.topic,
        audience=job.audience,
        documents=job.documents,
        min_pages=job.min_pages,
        max_pages=job.max_pages,
        target_count=job.page_count,
        search_context=job.search_context,
        on_progress=on_progress,
    )
    if deck_blueprint:
        logger.info("ContentPlan: generated long deck blueprint, chars=%s", len(deck_blueprint))

    outline = _generate_outline_from_blueprint_in_chunks(
        topic=job.topic,
        documents=job.documents,
        deck_blueprint=deck_blueprint,
        target_count=job.page_count,
        min_pages=job.min_pages,
        max_pages=job.max_pages,
        search_context=job.search_context,
        on_progress=on_progress,
    )
    missing_modules = _missing_required_source_modules(outline, job.documents)
    if missing_modules:
        raise ValueError("内容规划未完整覆盖上传材料章节：" + "、".join(missing_modules))
    return outline


def _execute_page_map_strategy(
    job: ContentPlanJob,
    on_progress: Callable[[dict], None] | None = None,
) -> list[dict]:
    page_map = generate_content_page_map(
        topic=job.topic,
        audience=job.audience,
        page_count=job.page_count,
        documents=job.documents,
        search_context=job.search_context,
        intent_contract=job.intent_contract,
        on_progress=on_progress,
        mode=job.mode,
    )
    expected_total = job.page_count if len(page_map) < job.min_pages else None
    outline = content_plan_from_page_map(page_map, expected_total=expected_total, source_context=job.documents)
    if len(outline) < job.min_pages:
        raise ValueError(
            f"内容规划质量不足：模型只生成 {len(outline)} 页，低于本轮最低要求 {job.min_pages} 页。"
            "请缩小范围、明确章节，或重新生成；系统不会用空壳页面补足页数。"
        )
    return outline


def _source_outline_for_duplicate_repair(job: ContentPlanJob) -> list[dict]:
    if not job.has_docs:
        return []
    source_draft = _source_draft_page_map(
        topic=job.topic,
        documents=job.documents,
        target_count=job.page_count,
        min_pages=job.min_pages,
        max_pages=job.max_pages,
        intent_contract=job.intent_contract,
    )
    if not source_draft:
        return []
    outline = content_plan_from_page_map(
        source_draft,
        expected_total=job.page_count if len(source_draft) < job.min_pages else None,
        source_context=job.documents,
    )
    outline = _normalize_outline_page_count(
        outline,
        job.page_count,
        strict_page_count=job.strict_page_count,
        allow_expanded_outline_override=job.allow_expanded_outline_override,
    )
    outline = _enforce_requested_page_range(outline, job.requested_page_range)
    outline = _normalize_content_markdown(outline, topic=job.topic)
    if _empty_required_content_body_pages(outline):
        return []
    if _thin_required_content_body_pages(outline):
        return []
    outline = _dedupe_content_headlines(outline)
    if _duplicate_content_headline_pages(outline):
        return []
    return outline


def _execute_content_plan_strategy(
    job: ContentPlanJob,
    strategy: str,
    on_progress: Callable[[dict], None] | None = None,
) -> list[dict]:
    if strategy in {CONTENT_PLAN_STRATEGY_REUSE_EXPORTED, CONTENT_PLAN_STRATEGY_REUSE_PAGINATED}:
        return _execute_reuse_content_plan_strategy(job, strategy, on_progress)

    if not _is_source_preserve_job(job):
        _ensure_search_context(job)
    if strategy == CONTENT_PLAN_STRATEGY_LONG_DECK:
        raise ValueError(
            "long_structured_deck is deprecated diagnostics-only and is not available "
            "for user-visible content planning."
        )
    if strategy == CONTENT_PLAN_STRATEGY_PAGE_MAP:
        return _execute_page_map_strategy(job, on_progress)
    raise ValueError(f"Unsupported content plan strategy: {strategy}")


def _finalize_generated_content_plan(
    outline: list[dict],
    job: ContentPlanJob,
    strategy: str = "",
    on_progress: Callable[[dict], None] | None = None,
) -> list[dict]:
    outline = _normalize_outline_page_count(
        outline,
        job.page_count,
        strict_page_count=job.strict_page_count,
        allow_expanded_outline_override=job.allow_expanded_outline_override,
    )
    outline = _enforce_requested_page_range(outline, job.requested_page_range)
    outline = _normalize_content_markdown(outline, topic=job.topic)
    if job.mode != "direct_replicate":
        empty_body_pages = _empty_required_content_body_pages(outline)
        if empty_body_pages:
            raise ValueError("内容规划质量不足，以下正文页缺少具体正文：" + "、".join(f"P{page}" for page in empty_body_pages))
        thin_body_pages = _thin_required_content_body_pages(outline)
        if thin_body_pages:
            raise ValueError("内容规划质量不足，以下正文页信息量过薄：" + "、".join(f"P{page}" for page in thin_body_pages))
        outline = _dedupe_content_headlines(outline)
        duplicate_headlines = _duplicate_content_headline_pages(outline)
        if duplicate_headlines and strategy == CONTENT_PLAN_STRATEGY_PAGE_MAP:
            repaired_outline = _source_outline_for_duplicate_repair(job)
            if repaired_outline:
                logger.warning(
                    "ContentPlan: repaired duplicate model headlines with source-derived outline, duplicate=%s",
                    duplicate_headlines[0],
                )
                outline = repaired_outline
                duplicate_headlines = _duplicate_content_headline_pages(outline)
        if duplicate_headlines:
            duplicate = duplicate_headlines[0]
            raise ValueError(
                "内容规划质量不足，内容页标题重复："
                + "、".join(f"P{page}" for page in duplicate["pages"])
                + f"「{duplicate['headline']}」"
            )
    outline = _annotate_ppt_source_refs(outline, job.documents, job.topic, job.intent_contract)
    _emit_content_plan_saved(on_progress, outline)
    return outline


def generate_content_plan(
    topic: str,
    audience: str = "通用受众",
    page_count: int | None = None,
    documents: str = "",
    on_progress: Callable[[dict], None] | None = None,
    intent_contract: dict | None = None,
    chat_context: str | None = None,
) -> List[Dict]:
    """
    根据主题和文档生成 Content Plan。
    支持流式读取和进度回调，让前端能看到生成过程。
    """
    job = _build_content_plan_job(
        topic=topic,
        audience=audience,
        page_count=page_count,
        documents=documents,
        intent_contract=intent_contract,
        chat_context=chat_context,
    )
    logger.info(
        "ContentPlan: 为主题 '%s...' 生成大纲, page_count=%s, has_documents=%s",
        job.topic[:30],
        job.page_count,
        job.has_docs,
    )

    if on_progress:
        on_progress({"stage": "analyzing", "message": "正在分析主题和文档素材..."})

    strategy = _select_content_plan_strategy(job)
    logger.info("ContentPlan: selected strategy=%s", strategy)
    outline = _execute_content_plan_strategy(job, strategy, on_progress)

    if strategy in {CONTENT_PLAN_STRATEGY_REUSE_EXPORTED, CONTENT_PLAN_STRATEGY_REUSE_PAGINATED}:
        return outline

    outline = _finalize_generated_content_plan(outline, job, strategy, on_progress)
    logger.info("ContentPlan: %s 生成完成，共 %s 页", strategy, len(outline))
    return outline
