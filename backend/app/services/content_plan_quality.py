from __future__ import annotations

from dataclasses import dataclass
import re

from app.services import content_plan


@dataclass(frozen=True)
class ContentPlanQualityIssue:
    code: str
    message: str
    page_num: int | None = None
    severity: str = "error"


@dataclass(frozen=True)
class ContentPlanQualityReport:
    passed: bool
    issues: list[ContentPlanQualityIssue]

    @property
    def errors(self) -> list[ContentPlanQualityIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[ContentPlanQualityIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]


@dataclass(frozen=True)
class ContentPlanQualityCase:
    name: str
    target_count: int
    min_pages: int
    strict: bool = False
    required_anchors: tuple[str, ...] = ()
    required_gold_sentences: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()


_GENERIC_SPEAKER_NOTE_PATTERNS = (
    r"^讲解?第\s*\d+\s*页$",
    r"^讲前文[。.]?$",
    r"^先复述本页判断[。.]?$",
    r"^这一页口头展开[:：]?",
    r"^占位备注[。.]?$",
    r"^开场[。.]?$",
    r"^收束[。.]?$",
    r"^模型输出备注$",
)


def _issue(
    issues: list[ContentPlanQualityIssue],
    code: str,
    message: str,
    *,
    page_num: int | None = None,
    severity: str = "error",
) -> None:
    issues.append(ContentPlanQualityIssue(code=code, message=message, page_num=page_num, severity=severity))


def _page_num(page: dict) -> int | None:
    try:
        value = int(page.get("page_num") or 0)
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else None


def _is_generic_speaker_note(value: str) -> bool:
    text = re.sub(r"\s+", "", str(value or "").strip())
    if not text:
        return True
    if len(text) < 8:
        return True
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _GENERIC_SPEAKER_NOTE_PATTERNS)


def _speaker_note_repeats_bullets(note: str, bullets: list[str]) -> bool:
    if re.search(r"(讲法|转场|补证据|补充证据|讲清楚|收束|回扣|预埋)", str(note or "")):
        return False
    note_text = content_plan._compact_coverage_text(note)
    bullet_texts = [
        content_plan._compact_coverage_text(str(item or ""))
        for item in bullets
        if str(item or "").strip()
    ]
    meaningful = [item for item in bullet_texts if len(item) >= 8]
    if len(meaningful) < 2:
        return False
    repeated = sum(1 for item in meaningful if item and item in note_text)
    return repeated >= max(2, int(len(meaningful) * 0.75 + 0.999))


def _bullet_label(value: str) -> str:
    text = str(value or "").strip()
    match = re.match(r"^([^:：]{1,10})\s*[:：]", text)
    if not match:
        return ""
    label = content_plan._compact_coverage_text(match.group(1))
    if len(label) < 2:
        return ""
    return label


def _repetitive_label_pages(page_map: list[dict]) -> list[int]:
    label_sets: list[tuple[int, tuple[str, ...]]] = []
    for idx, page in enumerate(page_map or [], start=1):
        if not isinstance(page, dict) or not content_plan._page_map_requires_body_bullets(page):
            continue
        bullets = page.get("bullets") if isinstance(page.get("bullets"), list) else []
        labels = tuple(_bullet_label(str(item or "")) for item in bullets)
        labels = tuple(label for label in labels if label)
        if len(labels) >= 3:
            label_sets.append((_page_num(page) or idx, labels))
    if len(label_sets) < 3:
        return []
    counts: dict[tuple[str, ...], list[int]] = {}
    for page_num, labels in label_sets:
        counts.setdefault(labels, []).append(page_num)
    repeated_pages = max(counts.values(), key=len, default=[])
    if len(repeated_pages) >= max(3, int(len(label_sets) * 0.6 + 0.999)):
        return repeated_pages
    return []


def _duplicate_headlines_for_quality(page_map: list[dict]) -> list[str]:
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for page in page_map or []:
        if not isinstance(page, dict):
            continue
        page_type = str(page.get("type") or "content").strip().lower()
        if page_type in {"cover", "toc", "agenda", "ending"}:
            continue
        headline = content_plan._clean_visible_page_map_text(str(page.get("headline") or ""))
        compact = content_plan._compact_coverage_text(headline)
        if len(compact) < 4:
            continue
        if compact in seen and headline not in duplicates:
            duplicates.append(headline)
        else:
            seen[compact] = headline
    return duplicates


def evaluate_page_map_quality(
    page_map: list[dict],
    *,
    target_count: int,
    min_pages: int,
    strict: bool = False,
    source_draft: list[dict] | None = None,
    intent_contract: dict | None = None,
    required_anchors: tuple[str, ...] = (),
    required_gold_sentences: tuple[str, ...] = (),
    forbidden_terms: tuple[str, ...] = (),
) -> ContentPlanQualityReport:
    issues: list[ContentPlanQualityIssue] = []
    raw_pages = [page for page in page_map or [] if isinstance(page, dict)]
    normalized = content_plan._normalize_page_map(page_map)
    total = len(normalized)
    target = max(1, int(target_count or total or 1))
    minimum = max(1, int(min_pages or 1))

    if not normalized:
        _issue(issues, "empty_page_map", "内容规划为空")
        return ContentPlanQualityReport(passed=False, issues=issues)

    if total < minimum:
        _issue(issues, "page_count_below_min", f"页数低于最低要求：{total}/{minimum}")
    if strict and total < target:
        _issue(issues, "page_count_below_target", f"页数低于严格目标：{total}/{target}")
    if total < max(3, int(target * content_plan.PAGE_MAP_USEFUL_RATIO)):
        _issue(issues, "page_count_too_sparse", f"页数明显低于目标：{total}/{target}")

    for headline in _duplicate_headlines_for_quality(raw_pages):
        _issue(issues, "duplicate_headline", f"重复标题：{headline}")

    body_required = 0
    contentful = 0
    for idx, page in enumerate(normalized):
        raw_page = raw_pages[idx] if idx < len(raw_pages) else page
        page_num = _page_num(page)
        bullets = page.get("bullets") if isinstance(page.get("bullets"), list) else []
        has_headline = bool(str(page.get("headline") or "").strip())
        speaker_notes = str(page.get("speaker_notes") or "").strip()

        if content_plan._page_map_is_skeleton_placeholder(raw_page) or content_plan._page_map_is_skeleton_placeholder(page):
            _issue(issues, "skeleton_placeholder", "页面仍是低质量占位内容", page_num=page_num)
        if content_plan._page_map_has_format_placeholders(raw_page) or content_plan._page_map_has_format_placeholders(page):
            _issue(issues, "format_placeholder", "页面包含格式占位符", page_num=page_num)
        if content_plan._page_map_has_inline_page_markers(raw_page) or content_plan._page_map_has_inline_page_markers(page):
            _issue(issues, "inline_page_marker", "页面正文混入页码标记", page_num=page_num)
        if content_plan._page_map_requires_body_bullets(page):
            body_required += 1
            if not bullets:
                _issue(issues, "missing_body_bullets", "内容页缺少正文要点", page_num=page_num)
        if content_plan._page_map_requires_body_bullets(page) and _is_generic_speaker_note(speaker_notes):
            _issue(issues, "generic_speaker_notes", "演讲者备注过短或模板化", page_num=page_num)
        if content_plan._page_map_requires_body_bullets(page) and content_plan._page_map_speaker_notes_missing_talk_content(page):
            _issue(issues, "speaker_notes_missing_talk_content", "演讲者备注缺少可直接讲述的具体内容", page_num=page_num)
        if content_plan._page_map_requires_body_bullets(page) and _speaker_note_repeats_bullets(speaker_notes, bullets):
            _issue(issues, "speaker_notes_repeat_body", "演讲者备注主要在复述正文", page_num=page_num)
        if has_headline and (bullets or speaker_notes):
            contentful += 1

    if body_required <= 0:
        _issue(issues, "no_body_pages", "缺少正文页")
    if contentful < max(1, int(total * 0.8)):
        _issue(issues, "low_contentful_ratio", f"有效内容页比例过低：{contentful}/{total}")
    repetitive_label_pages = _repetitive_label_pages(normalized)
    if repetitive_label_pages:
        _issue(
            issues,
            "repetitive_bullet_labels",
            "多页正文使用重复标签式 bullet，缺少讲述节奏变化",
            page_num=repetitive_label_pages[0],
        )

    if source_draft and content_plan._is_source_preserve_contract(intent_contract):
        missing_tail = content_plan._missing_source_tail_candidates(normalized, source_draft, intent_contract)
        for item in missing_tail:
            _issue(issues, "missing_source_tail", f"缺少原文结尾覆盖：{item}")
        missing_structure = content_plan._missing_source_structure_candidates(normalized, source_draft, intent_contract)
        for item in missing_structure:
            _issue(issues, "missing_source_structure", f"缺少原文结构覆盖：{item}")

    coverage_text = content_plan._page_map_coverage_text(normalized)
    visible_text = "\n".join(
        str(part or "")
        for page in normalized
        for part in [
            page.get("section_title"),
            page.get("headline"),
            page.get("subhead"),
            "\n".join(str(item or "") for item in page.get("bullets", []) if isinstance(page.get("bullets"), list)),
            page.get("speaker_notes"),
        ]
    )
    for anchor in required_anchors:
        compact = content_plan._compact_coverage_text(anchor)
        if compact and compact not in coverage_text:
            _issue(issues, "missing_required_anchor", f"缺少必含结构锚点：{anchor}")
    for sentence in required_gold_sentences:
        compact = content_plan._compact_coverage_text(sentence)
        if compact and compact not in coverage_text:
            _issue(issues, "missing_gold_sentence", f"缺少必含原文金句：{sentence}")
    for term in forbidden_terms:
        if term and term in visible_text:
            _issue(issues, "forbidden_term", f"出现不应新增的内容：{term}")

    return ContentPlanQualityReport(passed=not any(issue.severity == "error" for issue in issues), issues=issues)


def evaluate_page_map_quality_case(
    page_map: list[dict],
    case: ContentPlanQualityCase,
    *,
    source_draft: list[dict] | None = None,
    intent_contract: dict | None = None,
) -> ContentPlanQualityReport:
    return evaluate_page_map_quality(
        page_map,
        target_count=case.target_count,
        min_pages=case.min_pages,
        strict=case.strict,
        source_draft=source_draft,
        intent_contract=intent_contract,
        required_anchors=case.required_anchors,
        required_gold_sentences=case.required_gold_sentences,
        forbidden_terms=case.forbidden_terms,
    )
