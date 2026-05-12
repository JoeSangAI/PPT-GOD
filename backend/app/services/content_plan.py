import json
import json_repair
import logging
import re
import time
from typing import Callable, Dict, List

from app.core.llm_client import get_llm_client
from app.core.provider_credentials import get_minimax_llm_model
from app.services.document_parser import detect_ppt_sources
from app.services.search_service import get_knowledge_augmenter
from app.utils.text_cleaning import normalize_markdown_emphasis

logger = logging.getLogger(__name__)

LONG_DECK_INCREMENTAL_THRESHOLD = 40
LONG_DECK_CHUNK_SIZE = 2
LONG_DECK_CHUNK_TIMEOUT_SECONDS = 60.0
LONG_DECK_SYNC_ENRICHMENT_PAGE_LIMIT = 0

LONG_DECK_SECTION_TITLES = [
    "开场定调：明确课程目标、听众语境和核心问题",
    "背景与痛点：解释为什么现在必须讨论这个主题",
    "总框架：建立整场课程的主线模型和判断标准",
    "模块一：拆解第一组关键概念、案例和常见误区",
    "模块二：展开方法步骤、工具和可迁移经验",
    "模块三：连接真实业务场景、决策问题和行动方案",
    "互动与练习：安排讨论、复盘、提问或课堂练习",
    "总结收束：回到主线，给出行动清单和结束页",
]

LOW_CONTENT_DRAFT_STATUSES = {"skeleton", "needs_review"}
PAGE_MAP_MODEL_TIMEOUT_SECONDS = 150.0
PAGE_MAP_DOCUMENT_LIMIT = 30000
PAGE_MAP_USEFUL_RATIO = 0.75


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
    return (text or "").translate(str.maketrans("０１２３４５６７８９", "0123456789"))


def _coerce_requested_page_count(value: str | int | None) -> int | None:
    try:
        page_count = int(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    return page_count if 1 <= page_count <= 200 else None


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
            start = _coerce_requested_page_count(match.group(1))
            end = _coerce_requested_page_count(match.group(2))
            if start and end and (min(start, end) >= 20 or _has_page_count_context(text, match.start(1), match.end(2))):
                return (min(start, end), max(start, end))
    upper_bound_patterns = (
        rf"(?:不要超过|不超过|不多于|最多|至多|max(?:imum)?|at\s*most)\s*(\d{{1,3}})\s*{page_unit}?",
    )
    for pattern in upper_bound_patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
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
            page_count = _coerce_requested_page_count(match.group(1))
            if page_count and _has_page_count_context(text, match.start(1), match.end(1)):
                return page_count
    return None


def _soft_page_bounds(page_count: int) -> tuple[int, int]:
    target = max(1, int(page_count or 1))
    lower = max(1, int(target * 0.7))
    upper = max(target, int(target * 1.3 + 0.999)) + 1
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


def _document_preservation_mode(documents: str, topic: str = "") -> str:
    """Choose how aggressively content planning may transform uploaded text."""
    if not documents or not documents.strip():
        return "none"
    if detect_ppt_sources(documents):
        return "ppt_source"
    if _is_general_transform_request(topic):
        return "transform"
    if len(documents) <= 14_000:
        return "faithful"
    if len(documents) <= 40_000:
        return "structured_extract"
    return "synthesis"


def _document_preservation_policy(documents: str, topic: str = "") -> str:
    mode = _document_preservation_mode(documents, topic)
    if mode == "faithful":
        return (
            "【原文保真模式】\n"
            "- 用户材料篇幅可控，默认目标是整理成 PPT，而不是重写。\n"
            "- 尽量保留原文的关键句、术语、数据、案例和表达顺序；标题可优化，但正文不要大幅改写。\n"
            "- 只有为分段、去重、纠正明显病句或适配版面时才做轻微编辑。\n"
        )
    if mode == "structured_extract":
        return (
            "【结构化摘取模式】\n"
            "- 材料较长，但仍应优先摘取原文中的关键段落和数据，不要重新发明叙事。\n"
            "- 每页正文使用原文要点的压缩版；删减只针对重复、旁枝和低信息密度内容。\n"
        )
    if mode == "synthesis":
        return (
            "【综合提炼模式】\n"
            "- 材料过长，允许按 PPT 方法论提炼主线。\n"
            "- 必须保留核心论点、专有名词、关键数字、引用和结论，避免改写用户立场。\n"
        )
    if mode == "transform":
        return (
            "【用户要求改写/提炼】\n"
            "- 可按用户目标重组材料，但不得改变事实、立场和关键术语。\n"
        )
    return ""


def infer_page_count_from_single_ppt(documents: str, topic: str = "") -> int | None:
    sources = detect_ppt_sources(documents)
    if len(sources) != 1 or _is_ppt_transform_request(topic):
        return None
    pages = int(sources[0].get("pages") or 0)
    return pages if pages > 0 else None


def _normalize_source_refs(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    refs: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        source_document = str(item.get("source_document") or item.get("filename") or "").strip()
        try:
            source_page_num = int(item.get("source_page_num") or item.get("page_num") or 0)
        except (TypeError, ValueError):
            source_page_num = 0
        if not source_document or source_page_num <= 0:
            continue
        refs.append({
            "source_document": source_document,
            "source_page_num": source_page_num,
            "source_type": str(item.get("source_type") or "pptx_slide"),
            "reason": str(item.get("reason") or item.get("usage") or "").strip(),
        })
    return refs[:8]


def _annotate_ppt_source_refs(outline: List[Dict], documents: str, topic: str = "") -> List[Dict]:
    """Keep a stable link from generated pages back to source PPT pages."""
    sources = detect_ppt_sources(documents)
    if not sources:
        return outline
    single_source = sources[0] if len(sources) == 1 else None
    direct_single_ppt_polish = bool(single_source and not _is_ppt_transform_request(topic))
    single_filename = str((single_source or {}).get("filename") or "").strip()
    single_page_count = int((single_source or {}).get("pages") or 0) if single_source else 0

    for page in outline:
        if not isinstance(page, dict):
            continue
        refs = _normalize_source_refs(page.get("source_refs"))
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


def _normalize_outline_page_count(outline: List[Dict], page_count: int, strict_page_count: bool = False) -> List[Dict]:
    """Keep LLM output reasonable while treating page_count as a soft target by default."""
    if not isinstance(outline, list):
        raise ValueError("Content plan generation failed: LLM output is not a JSON array")
    target_count = max(1, int(page_count or len(outline) or 1))
    max_count = target_count if strict_page_count else _soft_page_bounds(target_count)[1]
    if len(outline) > max_count:
        logger.warning(
            f"ContentPlan: LLM returned {len(outline)} pages, trimming to max {max_count} "
            f"(target={target_count}, strict={strict_page_count})"
        )
        closing_page = outline[-1] if isinstance(outline[-1], dict) else None
        if max_count >= 2 and closing_page and str(closing_page.get("type") or "").lower() == "ending":
            outline = outline[: max_count - 1] + [{**closing_page}]
        else:
            outline = outline[:max_count]
    for idx, page in enumerate(outline, start=1):
        if isinstance(page, dict):
            page["page_num"] = idx
            page_type = str(page.get("type") or "content").strip().lower() or "content"
            if idx == 1:
                page["type"] = "cover"
            elif idx == len(outline) and len(outline) > 1:
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
    text = (documents or "").strip()
    if not text:
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


def _resolve_soft_range_target(
    *,
    topic: str,
    documents: str,
    resolved_page_count: int,
    min_pages: int,
    max_pages: int,
) -> int:
    natural_count = _estimate_document_page_capacity(documents)
    if natural_count is None:
        natural_count = resolved_page_count or max_pages

    hard_upper = _is_hard_upper_page_count_request(topic)
    upper_bound = max_pages if hard_upper else _requested_range_soft_upper(min_pages, max_pages)
    if min_pages > 1 and natural_count >= int(min_pages * 0.65):
        natural_count = max(min_pages, natural_count)
    return max(1, min(upper_bound, natural_count))


def resolve_content_plan_page_target(topic: str, page_count: int | None, documents: str = "") -> tuple[int, int, int]:
    """Return target, min, max pages using the same policy as content-plan generation."""
    requested_page_range = infer_page_count_range_from_topic(topic)
    resolved_page_count = max(1, int(page_count or 10))
    if requested_page_range and resolved_page_count == 10:
        resolved_page_count = requested_page_range[1]
    strict_page_count = _is_strict_page_count_request(topic) and not requested_page_range
    min_pages, max_pages = (
        requested_page_range
        if requested_page_range
        else (resolved_page_count, resolved_page_count) if strict_page_count else _soft_page_bounds(resolved_page_count)
    )
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


def should_generate_incremental_long_deck(topic: str, page_count: int | None, documents: str = "") -> bool:
    target_count, min_pages, max_pages = resolve_content_plan_page_target(topic, page_count, documents)
    return _should_generate_deck_blueprint((min_pages, max_pages), target_count, documents)


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


def _brief_title(topic: str) -> str:
    text = re.sub(r"【文件：.*?】", "", topic or "", flags=re.DOTALL)
    text = re.sub(r"已上传材料：.*", "", text, flags=re.DOTALL)
    text = re.sub(r"识别到页数目标：.*", "", text, flags=re.DOTALL)
    lines = [line.strip(" ：:，,。") for line in text.splitlines() if line.strip()]
    for line in lines:
        cleaned = re.sub(r"我[要想希望]?制作一份|请?帮我|做成|PPT|ppt", "", line).strip(" ：:，,。")
        if 4 <= len(cleaned) <= 36:
            return cleaned
    return "课程内容规划"


def _clean_markdown_inline(text: str) -> str:
    value = normalize_markdown_emphasis(str(text or ""))
    value = re.sub(r"`([^`]*)`", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"[*_~]+", "", value)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _is_table_separator(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and set(stripped) <= {"|", "-", ":", " "}


def _markdown_line_to_plain(line: str) -> str:
    text = line.strip()
    if not text:
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


def extract_document_outline_units(documents: str) -> list[dict]:
    """Extract source-driven units from Markdown-ish uploaded material."""
    text = (documents or "").strip()
    if not text:
        return []
    units: list[dict] = []
    stack: list[tuple[int, str]] = []
    current: dict | None = None
    body_lines: list[str] = []

    def flush() -> None:
        nonlocal current, body_lines
        if not current:
            body_lines = []
            return
        body = "\n".join(line for line in body_lines if line.strip()).strip()
        current["text"] = body
        current["plain_lines"] = [
            plain
            for plain in (_markdown_line_to_plain(line) for line in body.splitlines())
            if plain
        ]
        units.append(current)
        current = None
        body_lines = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
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
        if current is None and line.strip():
            current = {
                "level": 1,
                "title": "用户上传材料",
                "path": "用户上传材料",
                "text": "",
                "plain_lines": [],
            }
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
            headline = title if idx == 0 else f"{title}（续 {idx + 1}）"
            specs.append({
                "headline": headline,
                "section_title": path,
                "lines": chunk,
                "source_path": path,
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
    expansion_angles = [
        ("核心判断", "把本页材料转成课堂上需要先讲清楚的判断。"),
        ("证据与案例", "提取本页材料中的数据、例子或可验证依据。"),
        ("企业主启示", "把本页材料翻译成听众可以判断自己业务的启示。"),
        ("课堂互动", "围绕本页材料设计一个提问、讨论或复盘点。"),
    ]
    idx = 0
    while len(expanded) < required_count:
        base = specs[idx % len(specs)]
        angle, hint = expansion_angles[(len(expanded) + idx) % len(expansion_angles)]
        clone = {
            **base,
            "headline": f"{base.get('headline') or '主题'}：{angle}",
            "lines": list(base.get("lines") or []) + [hint],
        }
        expanded.append(clone)
        idx += 1
    return expanded[:required_count]


def build_document_driven_long_deck_draft(
    *,
    topic: str,
    documents: str,
    target_count: int,
    min_pages: int,
    max_pages: int,
    deck_blueprint: str = "",
) -> list[dict]:
    units = extract_document_outline_units(documents)
    content_units = [unit for unit in units if str(unit.get("title") or "").strip() != "用户上传材料"]
    units_for_draft = _with_child_context(content_units or units)
    if not units:
        return build_long_deck_skeleton(
            topic=topic,
            target_count=target_count,
            min_pages=min_pages,
            max_pages=max_pages,
            deck_blueprint=deck_blueprint,
        )

    target_count = max(1, int(target_count or max_pages or min_pages or 1))
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
    specs = _expand_page_specs(_unit_page_specs(source_units or units_for_draft), max(0, target_count - 2))

    pages: list[dict] = [
        {
            "page_num": 1,
            "type": "cover",
            "section_title": "封面",
            "text_content": {
                "headline": title,
                "subhead": f"{min_pages}-{max_pages} 页课程型 PPT 内容规划",
                "body": "",
            },
            "speaker_notes": "封面页。主题来自用户上传材料，后续页面按原文结构展开。",
            "visual_suggestion": "封面应突出 AI 时代、企业营与销、面向大连中小企业主的课程语境。",
            "source_refs": [title],
            "generation_status": "source_draft",
        }
    ]

    agenda_lines = top_sections or [str(spec.get("headline") or "") for spec in specs[:6]]
    pages.append({
        "page_num": 2,
        "type": "agenda",
        "section_title": "课程总览",
        "text_content": {
            "headline": "今天这 90 分钟要解决什么",
            "subhead": "从道、法、术、器建立 AI 时代企业营与销布局",
            "body": "\n".join(f"- {line}" for line in agenda_lines if line),
        },
        "speaker_notes": "先把整场课的路径讲清楚：为什么必须变、平台怎么动、企业怎么布局、最后用什么工具落地。",
        "visual_suggestion": "课程地图式页面，用四段路径或阶梯结构呈现。",
        "source_refs": agenda_lines,
        "generation_status": "source_draft",
    })

    for page_num in range(3, target_count + 1):
        spec = specs[page_num - 3] if page_num - 3 < len(specs) else specs[-1]
        lines = [line for line in (spec.get("lines") or []) if line]
        if not lines:
            lines = [f"围绕「{spec.get('headline') or '本页主题'}」展开课堂讲解。"]
        body_lines = lines[:5]
        if page_num == target_count:
            page_type = "ending"
            headline = "总结与下一步"
            section_title = "总结收束"
            body = "回到整场课程主线：AI 会改变消费者决策中介，但企业仍要围绕信任、证据、审美和真实履约建立长期优势。"
        else:
            page_type = "section" if page_num in {3, 12, 23, 34, 45, 56, 67} else "content"
            headline = str(spec.get("headline") or f"第 {page_num} 页").strip()
            section_title = str(spec.get("section_title") or "课程内容").strip()
            body = "\n".join(f"- {line}" for line in body_lines)
        pages.append({
            "page_num": page_num,
            "type": page_type,
            "section_title": section_title,
            "text_content": {
                "headline": headline,
                "subhead": str(spec.get("source_path") or "").split(" > ")[0],
                "body": body,
            },
            "speaker_notes": (
                "讲解重点：先复述本页判断，再用材料中的数据、例子或行业场景解释，"
                "最后落到大连中小企业主可以执行或避免的动作。"
            ),
            "visual_suggestion": "根据本页是数据、框架、案例还是行动清单，选择图表、对比表、流程图或案例卡片。",
            "source_refs": [str(spec.get("source_path") or "")],
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
        text_content = page.get("text_content") if isinstance(page.get("text_content"), dict) else {}
        body = str(text_content.get("body") or "").strip()
        bullets = [
            re.sub(r"^\s*(?:[-*]|\d+[.)、])\s*", "", line).strip()
            for line in body.splitlines()
            if str(line).strip()
        ]
        bullets = [line for line in bullets if line]
        page_map.append({
            "page_num": int(page.get("page_num") or idx),
            "type": str(page.get("type") or "content").strip() or "content",
            "section_title": str(page.get("section_title") or "").strip(),
            "headline": str(text_content.get("headline") or "").strip() or f"第 {idx} 页",
            "subhead": str(text_content.get("subhead") or "").strip(),
            "bullets": bullets[:5],
            "speaker_notes": str(page.get("speaker_notes") or "").strip(),
            "visual_suggestion": str(page.get("visual_suggestion") or "").strip(),
            "source_refs": page.get("source_refs") if isinstance(page.get("source_refs"), list) else [],
            "generation_status": status,
        })
    return _normalize_page_map(page_map)


def _fallback_page_map(
    *,
    topic: str,
    documents: str,
    target_count: int,
    min_pages: int,
    max_pages: int,
) -> list[dict]:
    fallback_outline = build_document_driven_long_deck_draft(
        topic=topic,
        documents=documents,
        target_count=target_count,
        min_pages=min_pages,
        max_pages=max_pages,
    )
    return _outline_to_page_map(fallback_outline, status="page_map_source")


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
                "generation_status": "page_map_model",
            }
            continue
        if current is None:
            continue
        if re.match(r"^[-*+]\s+", line):
            bullet = _clean_markdown_inline(re.sub(r"^[-*+]\s+", "", line))
            if bullet:
                current.setdefault("bullets", []).append(bullet)
            continue
        label_match = re.match(r"^(副标题|subhead|备注|演讲备注|speaker_notes?|视觉|visual|来源|source_refs?)\s*[:：]\s*(.*)$", line, flags=re.IGNORECASE)
        if label_match:
            label = label_match.group(1).lower()
            value = _clean_markdown_inline(label_match.group(2))
            if label in {"副标题", "subhead"}:
                current["subhead"] = value
            elif label in {"备注", "演讲备注", "speaker_notes", "speaker_note"}:
                existing = str(current.get("speaker_notes") or "").strip()
                current["speaker_notes"] = f"{existing}\n{value}".strip() if existing else value
            elif label in {"视觉", "visual"}:
                current["visual_suggestion"] = value
            else:
                current["source_refs"] = [part.strip() for part in re.split(r"[；;]", value) if part.strip()]
            continue
        cleaned = _clean_markdown_inline(line)
        if cleaned:
            current.setdefault("bullets", []).append(cleaned)
    flush()
    return _normalize_page_map(pages)


def _normalize_page_map(page_map: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for page in sorted([p for p in page_map if isinstance(p, dict)], key=lambda item: int(item.get("page_num") or 10**6)):
        page_num = len(normalized) + 1
        page_type = str(page.get("type") or "content").strip().lower() or "content"
        if page_type in {"目录", "agenda"}:
            page_type = "agenda"
        elif page_type in {"章节", "section"}:
            page_type = "section"
        elif page_type in {"封面", "cover"}:
            page_type = "cover"
        elif page_type in {"封底", "ending", "end"}:
            page_type = "ending"
        elif page_type not in {"cover", "agenda", "toc", "section", "content", "hero", "quote", "data", "ending"}:
            page_type = "content"
        bullets = page.get("bullets") if isinstance(page.get("bullets"), list) else []
        normalized.append({
            "page_num": page_num,
            "type": page_type,
            "section_title": str(page.get("section_title") or "").strip(),
            "headline": str(page.get("headline") or "").strip() or f"第 {page_num} 页",
            "subhead": str(page.get("subhead") or "").strip(),
            "bullets": [str(item).strip() for item in bullets if str(item).strip()][:5],
            "speaker_notes": str(page.get("speaker_notes") or "").strip(),
            "visual_suggestion": str(page.get("visual_suggestion") or "").strip(),
            "source_refs": page.get("source_refs") if isinstance(page.get("source_refs"), list) else [],
            "generation_status": str(page.get("generation_status") or "page_map_model"),
        })
    return normalized


def _page_map_is_useful(page_map: list[dict], *, target_count: int, min_pages: int, strict: bool) -> bool:
    if not page_map:
        return False
    if strict and len(page_map) < target_count:
        return False
    if min_pages > 1 and len(page_map) < min_pages:
        return False
    if len(page_map) < max(3, int(target_count * PAGE_MAP_USEFUL_RATIO)):
        return False
    contentful = 0
    for page in page_map:
        bullets = page.get("bullets") if isinstance(page.get("bullets"), list) else []
        if str(page.get("headline") or "").strip() and (bullets or str(page.get("speaker_notes") or "").strip()):
            contentful += 1
    return contentful >= max(1, int(len(page_map) * 0.8))


def _merge_page_map_with_fallback(page_map: list[dict], fallback: list[dict], *, target_count: int) -> list[dict]:
    by_num = {int(page.get("page_num") or 0): page for page in page_map if isinstance(page, dict)}
    merged: list[dict] = []
    for idx in range(1, target_count + 1):
        if idx in by_num:
            merged.append(by_num[idx])
        elif idx - 1 < len(fallback):
            merged.append(fallback[idx - 1])
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
    on_progress: Callable[[dict], None] | None = None,
) -> list[dict]:
    if on_progress:
        on_progress({
            "stage": "analyzing",
            "message": "正在整理每一页要讲什么...",
            "current_page": 0,
            "total_pages": target_count,
        })
    doc_text = (documents or "").strip()
    if len(doc_text) > PAGE_MAP_DOCUMENT_LIMIT:
        doc_text = _document_excerpt_for_extension(doc_text, limit=PAGE_MAP_DOCUMENT_LIMIT)
    preservation_policy = _document_preservation_policy(documents, topic)
    prompt = f"""你是一位顶尖的课程型 PPT 总架构师。请先生成整份 PPT 的“逐页内容地图”，不要输出 JSON。

【用户主题和约束】
{topic}

【目标受众】
{audience}

【页数目标】
{page_goal_text}
本轮建议按约 {target_count} 页规划；如果材料明显不足，可以减少页数，但不能用空话凑页数。

【用户上传材料】
{doc_text or "无"}

【实时搜索上下文】
{search_context or "无"}

【材料使用规则】
{preservation_policy or "没有上传文档时，根据用户主题和受众目标生成课程结构。"}

【输出要求】
1. 一次性给出全局逐页内容地图，必须覆盖整场演讲/课程的开场、主线、转场、案例、复盘和结尾。
2. 每页都要有标题、2-3 个具体 bullet、演讲者备注；不要只写章节名或空泛框架。
3. 标题和 bullet 必须尽量来自用户材料或 Brief，不能为了凑页数发明不相干内容。
4. 页间要有连续叙事：上一页为什么引出下一页要想清楚。
5. 封面页可以没有 bullet；封底页只收束，不引入新论点。
6. 输出格式必须固定为：
P1｜cover｜封面｜标题
- bullet
- bullet
备注：演讲者备注
视觉：画面建议
来源：材料线索

P2｜content｜章节｜标题
- bullet
- bullet
备注：演讲者备注
视觉：画面建议
来源：材料线索

不要输出 JSON，不要输出 Markdown 表格，不要加额外解释。"""

    started_at = time.monotonic()
    client = get_llm_client()
    raw = ""
    try:
        stream = client.chat.completions.create(
            model=get_minimax_llm_model(),
            messages=[
                {"role": "system", "content": "你是世界一流的 PPT 总架构师。先做完整逐页内容地图，不输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.45,
            max_tokens=16000,
            timeout=PAGE_MAP_MODEL_TIMEOUT_SECONDS,
            stream=True,
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


def generate_content_page_map(
    *,
    topic: str,
    audience: str = "通用受众",
    page_count: int = 10,
    documents: str = "",
    search_context: str = "",
    on_progress: Callable[[dict], None] | None = None,
) -> list[dict]:
    requested_page_range = infer_page_count_range_from_topic(topic)
    target_count, min_pages, max_pages = resolve_content_plan_page_target(topic, page_count, documents)
    strict_page_count = _is_strict_page_count_request(topic) and not requested_page_range
    if requested_page_range:
        page_goal_text = (
            f"优先参考 {min_pages}-{max_pages} 页范围；材料不足时可以更短，"
            f"当前建议约 {target_count} 页，不要为了凑页数灌水。"
        )
    elif strict_page_count:
        page_goal_text = f"用户明确要求必须 {target_count} 页。"
    else:
        page_goal_text = f"{target_count} 页左右，可在 {min_pages}-{max_pages} 页范围内浮动。"

    fallback = _fallback_page_map(
        topic=topic,
        documents=documents,
        target_count=target_count,
        min_pages=min_pages,
        max_pages=max_pages,
    )
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
            on_progress=on_progress,
        )
        if _page_map_is_useful(model_map, target_count=target_count, min_pages=min_pages, strict=strict_page_count):
            if strict_page_count and len(model_map) < target_count:
                return _merge_page_map_with_fallback(model_map, fallback, target_count=target_count)
            return _normalize_page_map(model_map)
        logger.warning(
            "ContentPlan: model page map not useful, pages=%s target=%s min=%s; using source fallback",
            len(model_map),
            target_count,
            min_pages,
        )
    except Exception as exc:
        logger.warning("ContentPlan: failed to generate model page map, using source fallback: %s", exc)
    return fallback


def content_plan_from_page_map(page_map: list[dict]) -> list[dict]:
    outline: list[dict] = []
    normalized_map = _normalize_page_map(page_map)
    total = len(normalized_map)
    for idx, page in enumerate(normalized_map, start=1):
        page_type = str(page.get("type") or "content").strip().lower() or "content"
        if idx == 1:
            page_type = "cover"
        elif page_type == "cover":
            page_type = "section"
        elif idx == total and total > 1:
            page_type = "ending"
        elif page_type == "ending":
            page_type = "content"
        bullets = page.get("bullets") if isinstance(page.get("bullets"), list) else []
        body = "" if page_type == "cover" else "\n".join(f"- {str(item).strip()}" for item in bullets if str(item).strip())
        outline.append({
            "page_num": idx,
            "type": page_type,
            "section_title": str(page.get("section_title") or "").strip(),
            "text_content": {
                "headline": str(page.get("headline") or "").strip() or f"第 {idx} 页",
                "subhead": str(page.get("subhead") or "").strip(),
                "body": body,
            },
            "speaker_notes": str(page.get("speaker_notes") or "").strip(),
            "visual_suggestion": str(page.get("visual_suggestion") or "").strip() or "根据本页内容选择清晰的课程型版式，优先保证信息层级和演讲节奏。",
            "source_refs": page.get("source_refs") if isinstance(page.get("source_refs"), list) else [],
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
        return "课程结构"

    for page_num in range(1, target_count + 1):
        if page_num == 1:
            page = {
                "page_num": page_num,
                "type": "cover",
                "section_title": "封面",
                "text_content": {
                    "headline": title,
                    "subhead": f"{min_pages}-{max_pages} 页课程型 PPT 内容规划",
                    "body": "",
                },
                "speaker_notes": "封面页。后续分段生成会补齐课程定位、开场话术和演讲备注。",
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
                    "body": "回到整场课程主线，提炼关键结论、行动建议和后续讨论方向。",
                },
                "speaker_notes": "封底页。后续分段生成会根据完整课程内容补齐收束话术。",
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
                    "body": "本页已先放入 80 页课程结构中，系统会继续根据 Brief 和上传材料补齐正文、案例、讲稿备注和视觉建议。",
                },
                "speaker_notes": "占位讲稿。系统会继续分段补齐本页的讲解逻辑、课堂节奏和转场。",
                "visual_suggestion": "待内容细化后生成本页视觉建议。",
                "source_refs": [],
                "generation_status": "skeleton",
            }
        if deck_blueprint:
            page["deck_blueprint_ref"] = deck_blueprint[:1000]
        skeleton.append(page)
    return skeleton


def _fallback_deck_blueprint(target_count: int, min_pages: int, max_pages: int) -> str:
    target_count = max(1, int(target_count or max_pages or min_pages or 1))
    lines = [
        "## 全局蓝图",
        f"- P1：封面。定主题、听众和演讲语境，正文留空。",
    ]
    for start, end, title in _long_deck_section_ranges(target_count):
        lines.append(f"- P{start}-P{end}：{title}。围绕用户材料展开，按课程节奏拆成讲解、案例、方法和复盘页。")
    if target_count >= 2:
        lines.append(f"- P{target_count}：封底。只做感谢、复盘或下一步，不引入新论点。")
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
            "message": "正在快速设计全局课程结构...",
            "current_page": 0,
            "total_pages": max_pages,
        })

    fallback = _fallback_deck_blueprint(target_count, min_pages, max_pages)
    doc_excerpt = _document_excerpt_for_extension(documents, limit=7000)
    prompt = f"""你是一位顶尖的课程型 PPT 总架构师。先为一份长 PPT 设计全局蓝图，不要生成逐页 JSON。

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
3. 标明每章的讲述目标、核心论点、关键材料来源、案例/练习/讨论安排、转场逻辑。
4. 必须把封面、必要的过渡、复盘总结和封底纳入页码规划。
5. 这是一场课程/演讲型 PPT，要服务用户的受众和时长，不要做成薄摘要。

只输出可读的中文 Markdown 蓝图，不要输出 JSON。"""

    try:
        client = get_llm_client()
        response = client.chat.completions.create(
            model=get_minimax_llm_model(),
            messages=[
                {"role": "system", "content": "你是世界一流的长篇课程 PPT 总架构师。只输出全局蓝图，不生成逐页 JSON。"},
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
- 如果材料不足，优先扩展为课程讲解页、案例页、讨论页、方法拆解页、过渡页和总结页，而不是灌水。

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
    chunk_size = 12
    outline: list[dict] = []
    doc_excerpt = _document_excerpt_for_extension(documents, limit=12000)
    preservation_policy = _document_preservation_policy(documents, topic)

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
{preservation_policy or "没有上传文档时，根据用户主题和受众目标生成课程结构。"}

【本轮任务】
只生成第 {start_page} 页到第 {end_page} 页，共 {end_page - start_page + 1} 页。
- page_num 必须从 {start_page} 连续编号到 {end_page}。
- 每页必须落在全局蓝图对应的章节和页码区间内。
- 不能重复已生成页面；要接上已有页面摘要的叙事。
- 每页必须包含 type、section_title、text_content、speaker_notes、visual_suggestion、source_refs。
- {"第 1 页必须是 cover 封面页，body 保持为空。" if is_first_chunk else "本轮不是封面段，不要再生成 cover。"}
- {"第 " + str(end_page) + " 页必须是 ending 封底页，用于收束全场。" if is_final_chunk else "本轮不是最后一组页面，不要生成 ending 封底页。"}
- 长课程页数要靠讲解、案例、方法、讨论、复盘自然展开，不能灌水。

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
        for page in new_pages:
            if len(outline) >= target_count:
                break
            next_page_num = len(outline) + 1
            page["page_num"] = next_page_num
            if next_page_num == 1:
                page["type"] = "cover"
                text_content = page.get("text_content") if isinstance(page.get("text_content"), dict) else {}
                text_content["body"] = ""
                page["text_content"] = text_content
            elif next_page_num < target_count and str(page.get("type") or "").lower() == "ending":
                page["type"] = "content"
            elif next_page_num == target_count:
                page["type"] = "ending"
            outline.append(page)
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
{preservation_policy or "没有上传文档时，根据用户主题和受众目标生成课程结构。"}

【本轮任务】
只生成第 {start_page} 页到第 {end_page} 页，共 {end_page - start_page + 1} 页。
- page_num 必须从 {start_page} 连续编号到 {end_page}。
- 每页必须落在全局蓝图对应的章节和页码区间内。
- 不能重复已生成页面；要接上已有页面摘要的叙事。
- 每页必须包含 type、section_title、text_content、speaker_notes、visual_suggestion、source_refs。
- {"第 1 页必须是 cover 封面页，body 保持为空。" if is_first_chunk else "本轮不是封面段，不要再生成 cover。"}
- {"第 " + str(end_page) + " 页必须是 ending 封底页，用于收束全场。" if is_final_chunk else "本轮不是最后一组页面，不要生成 ending 封底页。"}
- 长课程页数要靠讲解、案例、方法、讨论、复盘自然展开，不能灌水。

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

    if len(normalized) != end_page - start_page + 1:
        raise ValueError(
            f"内容规划分段生成失败：第 {start_page}-{end_page} 页只返回 {len(normalized)} 页。"
        )
    return _normalize_content_markdown(normalized)


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
                page["type"] = "cover"
            elif idx == len(outline) and len(outline) > 1:
                page["type"] = "ending"
            elif page_type == "cover":
                page["type"] = "section"
            elif page_type == "ending":
                page["type"] = "content"
    return outline


def _normalize_content_markdown(outline: List[Dict]) -> List[Dict]:
    """Normalize Markdown generated by the LLM before it becomes project state."""
    for page in outline:
        if not isinstance(page, dict):
            continue
        text_content = page.get("text_content")
        if isinstance(text_content, dict):
            for key in ("headline", "subhead", "body"):
                value = text_content.get(key)
                if isinstance(value, str):
                    text_content[key] = normalize_markdown_emphasis(value)
                elif isinstance(value, list):
                    text_content[key] = [
                        normalize_markdown_emphasis(item) if isinstance(item, str) else item
                        for item in value
                    ]
        notes = page.get("speaker_notes")
        if isinstance(notes, str):
            page["speaker_notes"] = normalize_markdown_emphasis(notes)
        _normalize_punchline_page_content(page)
    return outline


def _first_text_line(value) -> str:
    if isinstance(value, list):
        candidates = [str(item) for item in value if str(item).strip()]
    else:
        candidates = str(value or "").splitlines()
    for line in candidates:
        cleaned = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", str(line)).strip()
        cleaned = normalize_markdown_emphasis(cleaned).strip()
        if cleaned:
            return cleaned
    return ""


def _normalize_punchline_page_content(page: Dict) -> None:
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


def generate_content_plan(
    topic: str,
    audience: str = "通用受众",
    page_count: int = 10,
    documents: str = "",
    on_progress: Callable[[dict], None] | None = None,
) -> List[Dict]:
    """
    根据主题和文档生成 Content Plan。
    支持流式读取和进度回调，让前端能看到生成过程。
    """
    has_docs = bool(documents and documents.strip())
    requested_page_range = infer_page_count_range_from_topic(topic)
    page_count, min_pages, max_pages = resolve_content_plan_page_target(topic, page_count, documents)
    strict_page_count = _is_strict_page_count_request(topic) and not requested_page_range
    if requested_page_range:
        page_goal_text = (
            f"优先参考 {min_pages}-{max_pages} 页范围；如果材料不足或结构更适合，"
            f"可生成约 {page_count} 页，不要为了凑页数灌水"
        )
    elif strict_page_count:
        page_goal_text = f"必须 {page_count} 页"
    else:
        page_goal_text = f"{page_count} 页左右，可在 {min_pages}-{max_pages} 页范围内浮动"
    logger.info(
        f"ContentPlan: 为主题 '{topic[:30]}...' 生成大纲, "
        f"page_count={page_count}, has_documents={has_docs}"
    )

    if on_progress:
        on_progress({"stage": "analyzing", "message": "正在分析主题和文档素材..."})

    exported_outline = parse_exported_content_plan_markdown(documents)
    if exported_outline and not requested_page_range and not strict_page_count and not _is_general_transform_request(topic):
        logger.info("ContentPlan: detected PPT God Markdown export, reusing %s pages directly", len(exported_outline))
        if on_progress:
            on_progress({
                "stage": "saving",
                "message": "已识别导出的内容规划，正在保存结果...",
                "current_page": len(exported_outline),
                "total_pages": len(exported_outline),
            })
        return exported_outline

    doc_section = ""
    if has_docs:
        ppt_sources = detect_ppt_sources(documents)
        preservation_policy = _document_preservation_policy(documents, topic)
        ppt_policy = ""
        if len(ppt_sources) == 1:
            ppt = ppt_sources[0]
            ppt_policy = f"""
【上传 PPT 的特殊处理】
系统识别到用户上传了 1 个 PPT：{ppt.get("filename") or "未命名 PPT"}，原始页数 {ppt.get("pages")} 页。
- 如果用户没有明确要求扩展、缩减、提取特定主题、融合或重组，你必须按原 PPT 逐页复刻内容结构：输出页数等于 {ppt.get("pages")} 页，第 i 个 JSON 页面对应原 PPT 第 i 页。
- 逐页复刻时，文字内容应尽量保留原页标题、要点、数据和备注，不要压缩成摘要，也不要重新发明叙事。
- 原页中出现的关键数字、标签、对比项、流程节点、学校/城市名单、产品规格必须进入 text_content.body；不能只放在 speaker_notes，也不能因为“每页只说一件事”而删除原页事实。
- 如果原页是信息密集页，允许 body 使用表格或分组 bullet 承载原文信息；视觉阶段会再做版式取舍，内容规划阶段不得提前丢信息。
- 每一页必须输出 source_refs: [{{"source_document": "{ppt.get("filename") or "未命名 PPT"}", "source_page_num": 原PPT页码, "reason": "polish"}}]，确保新页能追溯到原 PPT 页。
- 如果用户明确要求扩展/缩减/提取/融合/加入新想法，则按用户意图动态调整页数和结构。
"""
        elif len(ppt_sources) > 1:
            ppt_policy = """
【上传多个 PPT 的特殊处理】
系统识别到用户上传了多个 PPT。不要机械相加页数；先理解用户意图：融合、提取、重组、对比或加入新想法。除非用户指定页数，否则按内容逻辑生成合适页数。
每一页都要输出 source_refs，列出该页主要吸收了哪个源 PPT 的哪几页，格式为 [{"source_document": "文件名", "source_page_num": 页码, "reason": "为什么使用这一页"}]。如果某页是新增转场或总结，可以 source_refs 为空数组。
"""
        doc_section = f"""
【用户上传的文档素材】
{documents}

【文档使用规则】
1. 用户文档是最高优先级素材；不要用通用 PPT 方法论覆盖原文意图。
2. 文档中的关键论点、数据、结构必须体现在大纲中。
3. 页数是软目标；除非用户指定严格页数，应根据材料密度生成合适长度，不要为了凑页数灌水。
{preservation_policy}
{ppt_policy}
"""

    # 【新增】内容规划阶段也触发实时搜索，避免模型对前沿话题产生幻觉
    search_section = ""
    search_context = get_knowledge_augmenter().augment(topic, has_documents=has_docs)
    if search_context:
        search_section = f"""
{search_context}

【搜索结果使用规则】
1. 上述网络搜索结果是实时获取的，你必须基于这些事实信息设计 PPT 大纲。
2. 人名、角色名、剧情、数据等关键信息必须与搜索结果一致，严禁编造。
3. 如果搜索结果不足以支撑完整大纲，可以合理推断，但必须标注为"推测"。
"""
        logger.info(f"ContentPlan: 已注入搜索上下文，topic={topic[:30]}")

    page_map = generate_content_page_map(
        topic=topic,
        audience=audience,
        page_count=page_count,
        documents=documents,
        search_context=search_context,
        on_progress=on_progress,
    )
    outline = content_plan_from_page_map(page_map)
    outline = _normalize_outline_page_count(outline, page_count, strict_page_count=strict_page_count)
    outline = _enforce_requested_page_range(outline, requested_page_range)
    outline = _annotate_ppt_source_refs(outline, documents, topic)
    logger.info("ContentPlan: Page Map 生成完成，共 %s 页", len(outline))
    if on_progress:
        on_progress({"stage": "saving", "message": "正在保存结果...", "current_page": len(outline), "total_pages": len(outline)})
    return outline

    deck_blueprint = ""
    if _should_generate_deck_blueprint(requested_page_range, page_count, documents):
        deck_blueprint = _generate_deck_blueprint(
            topic=topic,
            audience=audience,
            documents=documents,
            min_pages=min_pages,
            max_pages=max_pages,
            target_count=page_count,
            search_context=search_context,
            on_progress=on_progress,
        )
        if deck_blueprint:
            logger.info("ContentPlan: generated long deck blueprint, chars=%s", len(deck_blueprint))

    blueprint_section = ""
    if deck_blueprint:
        blueprint_section = f"""
【全局蓝图（必须遵守）】
{deck_blueprint}

【蓝图使用规则】
1. 逐页大纲必须服从上述章节页码区间、讲述目标和转场逻辑。
2. 不要因为单轮输出太长而提前收束；封底只能放在蓝图指定的最后页附近。
3. 如果某一章材料较多，应在该章页码范围内拆成案例、方法、讨论、复盘页，而不是跳到后续章节。
"""

    if deck_blueprint and _should_generate_deck_blueprint(requested_page_range, page_count, documents):
        outline = _generate_outline_from_blueprint_in_chunks(
            topic=topic,
            documents=documents,
            deck_blueprint=deck_blueprint,
            target_count=page_count,
            min_pages=min_pages,
            max_pages=max_pages,
            search_context=search_context,
            on_progress=on_progress,
        )
        outline = _enforce_requested_page_range(outline, requested_page_range)
        outline = _normalize_content_markdown(outline)
        outline = _annotate_ppt_source_refs(outline, documents, topic)
        logger.info(f"ContentPlan: 长页数分段生成完成，共 {len(outline)} 页")
        if on_progress:
            on_progress({"stage": "saving", "message": "正在保存结果...", "current_page": len(outline), "total_pages": len(outline)})
        return outline

    prompt = f"""你是一位顶尖的商业演示架构师。请为以下主题设计一份 PPT 大纲。

【主题】
{topic}

【背景】
- 目标受众: {audience}
- 期望页数: {page_goal_text}
{doc_section}
{search_section}
{blueprint_section}

【任务要求】
1. 设计清晰结构；有充分原文时以“组织原文”为主，不主动换叙事。
2. 每页必须包含：
   - page_num: 页码
   - type: 页面类型（cover/目录 toc/章节 section/content/hero/data/ending）。其中 hero 是“金句页/punchline slide”，不是普通内容页。
   - section_title: 所属章节
   - text_content.headline: 大标题（有力、简洁的断言句）
   - text_content.subhead: 副标题（可选）
   - text_content.body: 正文（markdown 格式字符串，支持加粗、列表、表格等）
     格式服从认知：对比用表格，顺序要点用列表，叙事推导用段落；不要硬套表格。
   - speaker_notes: 演讲者备注（详细论述，供演讲者参考）
   - visual_suggestion: 画面/配图建议
   - source_refs: 来源页引用数组。使用上传 PPT 内容时必须填写，元素格式为 {{"source_document": "源文件名", "source_page_num": 原页码, "reason": "使用原因"}}；纯新增页可为空数组。
3. 封面和封底各占一页。封面只定主题和语境，body 保持为空；封底只做收束、感谢、下一步或联系方式，不引入新论点。
4. 内容页不要堆砌，每页只说一件事。
   - 但当任务是“改写/美化一个已上传 PPT”时，这条不能被理解为删掉原页信息；应保留原页事实，只把同一页信息组织得更清楚。
5. 标题可优化表达，但不能改变原文判断；原文标题已经清楚时直接沿用。
6. 如果一个页面同时包含多个强画面证据/大事件，可主动拆页，而不是硬塞进同一页。
7. 普通“期望页数/约 X 页/从 X 到 Y 页”是软目标；优先满足用户范围，但材料不足时生成更合适的长度，不要用空泛页面凑数。只有用户明确说“必须/严格/固定/只能 X 页/一页不能多也不能少”时才严格等于 X 页。
8. 目录页只在原文结构复杂且目录能降低理解成本时使用；不要默认插入。
9. 章节页（type="section"）只用于重大章节转换或叙事转折；它是短标题的分隔页，不承载正文论证。headline 放章节名或转场判断，body 保持为空或只放极短一句。
10. 数据页（type="data"）只在确有数字、比例、排名、时间序列、规模量级或可视化表格时使用；不要为了显得专业而编造图表。
   - data 页的 body 必须包含可被画出的真实数据点、标签或来源；如果只是定性判断，用 content 页或表格化正文。
11. 金句页只承载一句短结论、引用或转场判断；不要把普通内容页伪装成 hero。
12. 如果提供了全局蓝图，必须按照蓝图覆盖到最后一页，不允许在中途把课程当作已经结束。

【JSON 格式】
严格输出 JSON 数组，不要包含 Markdown 代码块标记：
[
  {{
    "page_num": 1,
    "type": "cover",
    "section_title": "",
    "text_content": {{
      "headline": "主标题",
      "subhead": "副标题",
      "body": ""
    }},
    "speaker_notes": "",
    "visual_suggestion": "",
    "source_refs": []
  }}
]
"""

    client = get_llm_client()
    stream = client.chat.completions.create(
        model=get_minimax_llm_model(),
        messages=[
            {"role": "system", "content": "你是世界一流的 PPT 架构师。必须且只能输出合法的 JSON 数组，严禁添加任何额外说明文本。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.5,
        stream=True,
    )

    full_content = ""
    page_count_found = 0
    in_think = False
    think_buffer = ""

    if on_progress:
        on_progress({"stage": "generating", "message": "正在构建叙事结构...", "current_page": 0, "total_pages": max_pages})

    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta.content or ""
        full_content += delta

        # 提取 think 内容（MiniMax 的推理过程）
        buf = delta
        while buf:
            if not in_think:
                idx = buf.find("<think>")
                if idx == -1:
                    buf = ""
                    break
                buf = buf[idx + 7:]
                in_think = True
            else:
                idx = buf.find("</think>")
                if idx == -1:
                    think_buffer += buf
                    buf = ""
                    break
                else:
                    think_buffer += buf[:idx]
                    buf = buf[idx + 8:]
                    in_think = False

        # 检测新生成的页面
        new_page_count = full_content.count('"page_num"')
        if new_page_count > page_count_found:
            page_count_found = new_page_count
            if on_progress:
                current_page = min(page_count_found, max_pages)
                on_progress({
                    "stage": "generating",
                    "message": f"正在生成第 {current_page}/{max_pages} 页...",
                    "current_page": current_page,
                    "total_pages": max_pages,
                    "think": think_buffer[-200:] if think_buffer else None,
                })

    outline = _parse_outline_json_response(full_content)

    outline = _normalize_outline_page_count(outline, page_count, strict_page_count=strict_page_count)
    if strict_page_count and len(outline) < min_pages:
        outline = _extend_outline_to_target_count(
            outline,
            topic=topic,
            documents=documents,
            deck_blueprint=deck_blueprint,
            target_count=page_count,
            min_pages=min_pages,
            max_pages=max_pages,
            on_progress=on_progress,
        )
    outline = _enforce_requested_page_range(outline, requested_page_range)
    outline = _normalize_content_markdown(outline)
    outline = _annotate_ppt_source_refs(outline, documents, topic)

    logger.info(f"ContentPlan: 生成完成，共 {len(outline)} 页")

    if on_progress:
        on_progress({"stage": "saving", "message": "正在保存结果...", "current_page": len(outline), "total_pages": len(outline)})

    return outline
