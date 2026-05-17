import copy
import re
from typing import Any

from app.utils.text_cleaning import normalize_markdown_content


VISUAL_DIRECTIVE_TERMS: tuple[tuple[str, str], ...] = (
    ("flywheel", "增长飞轮|飞轮|闭环"),
    ("flow", "流程图|流程|路径图"),
    ("matrix", "矩阵|对比矩阵|象限"),
    ("pyramid", "金字塔"),
    ("hierarchy", "层级图|层级|组织结构|树状图"),
    ("funnel", "漏斗"),
    ("timeline", "时间轴"),
    ("roadmap", "路线图|路线规划图|roadmap"),
    ("venn", "韦恩图|Venn图|交集图"),
    ("swimlane", "泳道图"),
    ("gantt", "甘特图"),
    ("mindmap", "思维导图|脑图"),
    ("architecture", "架构图|系统架构|模块图"),
    ("network", "网络图|关系网络"),
    ("map", "地图|地域分布图"),
    ("stack", "分层图|堆叠图|层次图"),
    ("cycle", "循环图|闭环图|循环"),
    ("comparison", "对照图|对比图"),
    ("bubble", "气泡图"),
    ("sankey", "桑基图|桑基"),
    ("heatmap", "热力图|热图"),
    ("radar", "雷达图|雷达"),
    ("scatter", "散点图|散点"),
    ("line_chart", "折线图|折线"),
    ("bar_chart", "柱状图|柱形图|条形图|条形"),
    ("pie_chart", "饼图|饼状图"),
    ("area_chart", "面积图"),
    ("wordcloud", "词云|文字云"),
    ("treemap", "矩形树图|树图"),
    ("dashboard", "仪表盘|驾驶舱"),
    ("kpi", "KPI看板|指标卡|指标看板"),
    ("table", "表格|一览表|对照表"),
    ("diagram", "结构图|关系图|图示|图表"),
)

_TERM_PATTERN = "|".join(f"(?:{pattern})" for _kind, pattern in VISUAL_DIRECTIVE_TERMS)
_DIRECTIVE_PATTERNS = (
    re.compile(rf"(?:用|以|采用|通过).{{0,18}}(?:{_TERM_PATTERN}).{{0,12}}(?:表示|呈现|表达|展示)?"),
    re.compile(rf"(?:画成|做成|整理成|转成|改成|呈现为|表达为|表示为).{{0,18}}(?:{_TERM_PATTERN})"),
    re.compile(rf"(?:这里|此处|本段|这段|此段).{{0,8}}(?:做|画|转).{{0,8}}(?:{_TERM_PATTERN})"),
)
_LEADING_MARKDOWN_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)、]\s+|>\s+)?")
_LABEL_SPLIT_RE = re.compile(r"\s*(?:、|，|,|；|;|/|\||→|->|=>|＋|\+)\s*")


def extract_visual_directives(markdown: str) -> dict[str, Any]:
    """Find visual-expression instructions embedded in user-visible body copy."""
    lines = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cleaned_lines: list[str] = []
    suggestions: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        suggestion = _extract_line_directive(line, index)
        if suggestion:
            suggestions.append(suggestion)
            if suggestion.get("cleaned_body"):
                cleaned_lines.append(str(suggestion["cleaned_body"]))
            continue
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return {
        "cleaned_markdown": normalize_markdown_content(cleaned) if cleaned else "",
        "suggestions": suggestions,
        "diagram_labels": _unique_label_list(
            label
            for suggestion in suggestions
            for label in suggestion.get("diagram_labels", [])
        ),
    }


def normalize_visual_requirements(raw: Any) -> list[dict[str, Any]]:
    """Normalize stored visual requirements into a compact list."""
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else [raw]
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for item in items:
        if isinstance(item, str):
            item = {"directive": item}
        if not isinstance(item, dict):
            continue
        directive = _clean_directive_text(str(item.get("directive") or item.get("visual_intent") or ""))
        labels = _unique_label_list(item.get("diagram_labels") or item.get("labels") or [])
        if not directive and not labels:
            continue
        kind = str(item.get("kind") or _infer_kind(directive) or "diagram").strip()
        requirement = {
            "kind": kind,
            "directive": directive,
            "diagram_labels": labels,
        }
        source_text = str(item.get("source_text") or item.get("original_text") or "").strip()
        if source_text:
            requirement["source_text"] = source_text
        key = (directive, tuple(labels))
        if key not in seen:
            seen.add(key)
            normalized.append(requirement)
    return normalized


def visual_requirements_from_suggestions(suggestions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    requirements = []
    for suggestion in suggestions:
        requirements.append({
            "kind": suggestion.get("kind") or "diagram",
            "directive": suggestion.get("directive") or "",
            "diagram_labels": suggestion.get("diagram_labels") or [],
            "source_text": suggestion.get("original_text") or "",
        })
    return normalize_visual_requirements(requirements)


def merge_visual_requirements(existing: Any, additions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return normalize_visual_requirements([*normalize_visual_requirements(existing), *additions])


def separate_visual_directives_from_page(page: dict[str, Any]) -> dict[str, Any]:
    """Move obvious generated visual directives from body into visual metadata."""
    if not isinstance(page, dict):
        return page
    next_page = copy.deepcopy(page)
    text_content = next_page.get("text_content")
    if not isinstance(text_content, dict):
        return next_page
    body = text_content.get("body")
    if isinstance(body, list):
        body_text = "\n".join(str(item) for item in body)
    else:
        body_text = str(body or "")
    extraction = extract_visual_directives(body_text)
    suggestions = extraction["suggestions"]
    if not suggestions:
        return next_page

    text_content["body"] = extraction["cleaned_markdown"]
    next_page["text_content"] = text_content
    requirements = visual_requirements_from_suggestions(suggestions)
    next_page["visual_requirements"] = merge_visual_requirements(next_page.get("visual_requirements"), requirements)
    suggestion_text = _visual_suggestion_text(requirements)
    current_visual = str(next_page.get("visual_suggestion") or "").strip()
    if suggestion_text and suggestion_text not in current_visual:
        next_page["visual_suggestion"] = f"{current_visual}\n{suggestion_text}".strip() if current_visual else suggestion_text
    return next_page


def _extract_line_directive(line: str, line_index: int) -> dict[str, Any] | None:
    original = str(line or "").strip()
    if not original:
        return None
    leading_match = _LEADING_MARKDOWN_RE.match(original)
    leading = leading_match.group(0) if leading_match else ""
    core = original[len(leading):].strip()
    if not core:
        return None
    match = _directive_match(core)
    if not match:
        return None
    matched_text = match.group(0)
    embedded_directive, embedded_labels = _split_embedded_labels(matched_text)
    if embedded_labels:
        labels_text = f"{embedded_labels}{core[match.end():]}".strip()
        labels_end = len(core)
        directive = _clean_directive_text(embedded_directive)
    else:
        labels_text, labels_end = _labels_after_match(core, match.end())
        directive = _clean_directive_text(matched_text)
    if not directive:
        return None
    cleaned_core = _clean_body_after_directive_removal(core[:match.start()], core[labels_end:])
    cleaned_body = f"{leading}{cleaned_core}".strip() if cleaned_core else ""
    directive_source = core[match.start():labels_end].strip(" ，,；;")
    labels = _parse_labels(labels_text)
    return {
        "id": f"vd_{line_index + 1}",
        "line_index": line_index,
        "original_text": directive_source,
        "directive": directive,
        "kind": _infer_kind(core) or "diagram",
        "diagram_labels": labels,
        "cleaned_body": cleaned_body,
    }


def _directive_match(text: str) -> re.Match[str] | None:
    if not re.search(_TERM_PATTERN, text):
        return None
    for pattern in _DIRECTIVE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match
    return None


def _labels_after_match(text: str, match_end: int) -> tuple[str, int]:
    tail = text[match_end:]
    leading_space = len(tail) - len(tail.lstrip())
    offset = match_end + leading_space
    if offset < len(text) and text[offset] in {"：", ":"}:
        return text[offset + 1:].strip(), len(text)
    return "", match_end


def _split_embedded_labels(text: str) -> tuple[str, str]:
    for separator in ("：", ":"):
        if separator in text:
            left, right = text.split(separator, 1)
            return left.strip(), right.strip()
    return text.strip(), ""


def _clean_body_after_directive_removal(prefix: str, suffix: str) -> str:
    value = " ".join(part.strip() for part in (prefix, suffix) if part.strip())
    value = re.sub(r"\s+([，。；：、,.!?])", r"\1", value)
    value = re.sub(r"([，,；;])\s*([。；;，,])", r"\2", value)
    return value.strip(" ，,；;")


def _clean_directive_text(text: str) -> str:
    value = str(text or "").strip()
    value = re.sub(r"^(?:请|建议|最好|可以|这里|此处|本段|这段|此段)\s*", "", value)
    value = value.strip("。；;，, ")
    return value


def _parse_labels(text: str) -> list[str]:
    if not text:
        return []
    value = re.sub(r"^(?:包括|包含|节点|标签|步骤|分别为)\s*[:：]?\s*", "", str(text).strip())
    labels = []
    for part in _LABEL_SPLIT_RE.split(value):
        cleaned = part.strip().strip("。；;，,、")
        cleaned = re.sub(r"^\s*(?:[-*+]|\d+[.)、])\s*", "", cleaned).strip()
        if cleaned:
            labels.append(cleaned)
    return _unique_label_list(labels)[:12]


def _unique_label_list(values: Any) -> list[str]:
    result: list[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _infer_kind(text: str) -> str:
    value = str(text or "")
    for kind, pattern in VISUAL_DIRECTIVE_TERMS:
        if re.search(pattern, value):
            return kind
    return "diagram"


def _visual_suggestion_text(requirements: list[dict[str, Any]]) -> str:
    lines = []
    for requirement in requirements:
        directive = str(requirement.get("directive") or "").strip()
        labels = requirement.get("diagram_labels") or []
        if not directive:
            continue
        suffix = f"（图示标签：{'、'.join(str(label) for label in labels)}）" if labels else ""
        lines.append(f"{directive}{suffix}")
    return "\n".join(lines)
