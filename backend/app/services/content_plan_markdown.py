from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
from typing import Any

from sqlalchemy.orm import Session

from app.models.models import Project, Slide
from app.utils.text_cleaning import normalize_markdown_content


ALLOWED_SLIDE_TYPES = {
    "cover",
    "toc",
    "section",
    "content",
    "content_dense",
    "content_hero",
    "content_split",
    "content_top",
    "data",
    "hero",
    "quote",
    "ending",
}
REQUIRED_FIELDS = ("类型", "标题", "副标题", "正文", "备注")
OPTIONAL_EMPTY_FIELDS = {"副标题", "备注"}


@dataclass
class ContentPlanParseResult:
    title: str | None
    slides: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)


@dataclass
class ContentPlanValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    title: str | None = None
    slides: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ContentPlanImportReceipt:
    project_id: str
    title: str
    slides_count: int
    warnings: list[str]
    ui_url: str


@dataclass
class ContentPlanExportReceipt:
    project_id: str
    title: str
    slides_count: int
    filename: str
    markdown: str


class ContentPlanMarkdownError(ValueError):
    def __init__(self, errors: list[str], warnings: list[str] | None = None):
        self.errors = errors
        self.warnings = warnings or []
        super().__init__("; ".join(errors))


def _extract_title(markdown: str) -> str | None:
    for line in markdown.splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match and not line.startswith("##"):
            return match.group(1).strip() or None
    return None


def _page_blocks(markdown: str) -> list[tuple[int, str]]:
    matches = list(re.finditer(r"(?m)^##\s*P\s*(\d{1,3})\s*$", markdown))
    blocks: list[tuple[int, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        blocks.append((int(match.group(1)), markdown[start:end].strip()))
    return blocks


def _parse_fields(page_num: int, block: str) -> tuple[dict[str, str], list[str]]:
    matches = list(re.finditer(r"(?m)^###\s*(.+?)\s*$", block))
    fields: dict[str, str] = {}
    errors: list[str] = []
    for index, match in enumerate(matches):
        name = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(block)
        value = block[start:end].strip()
        if name not in REQUIRED_FIELDS:
            errors.append(f"P{page_num}: 无法识别字段「{name}」")
            continue
        if name in fields:
            errors.append(f"P{page_num}: 字段「{name}」重复")
            continue
        fields[name] = value

    for name in REQUIRED_FIELDS:
        if name not in fields:
            errors.append(f"P{page_num}: 缺少字段「{name}」")
    return fields, errors


def _slide_from_fields(page_num: int, fields: dict[str, str]) -> dict[str, Any]:
    slide_type = fields.get("类型", "").strip()
    headline = normalize_markdown_content(fields.get("标题", ""))
    subhead = normalize_markdown_content(fields.get("副标题", ""))
    body = normalize_markdown_content(fields.get("正文", ""))
    speaker_notes = normalize_markdown_content(fields.get("备注", ""))
    return {
        "page_num": page_num,
        "type": slide_type,
        "section_title": "",
        "text_content": {
            "headline": headline,
            "subhead": subhead,
            "body": body,
        },
        "speaker_notes": speaker_notes,
        "visual_suggestion": "",
        "visual_requirements": [],
    }


def validate_content_plan_markdown(markdown: str) -> ContentPlanValidationResult:
    text = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    errors: list[str] = []
    warnings: list[str] = []
    slides: list[dict[str, Any]] = []

    blocks = _page_blocks(text)
    if not blocks:
        return ContentPlanValidationResult(
            ok=False,
            errors=["没有找到任何页面边界，请使用固定格式：## P1、## P2 ..."],
            warnings=[],
            title=_extract_title(text),
            slides=[],
        )

    seen_pages: set[int] = set()
    for page_num, block in blocks:
        if page_num in seen_pages:
            errors.append(f"页码重复：P{page_num}")
        seen_pages.add(page_num)

        fields, field_errors = _parse_fields(page_num, block)
        errors.extend(field_errors)
        if field_errors:
            continue

        slide_type = fields.get("类型", "").strip()
        if slide_type not in ALLOWED_SLIDE_TYPES:
            errors.append(f"P{page_num}: 类型「{slide_type}」不合法")

        for field_name, value in fields.items():
            if value.strip():
                continue
            if field_name in OPTIONAL_EMPTY_FIELDS:
                warnings.append(f"P{page_num}: {field_name}为空")
            else:
                errors.append(f"P{page_num}: {field_name}不能为空")

        body = fields.get("正文", "").strip()
        if body and len(re.sub(r"\s+", "", body)) < 12:
            warnings.append(f"P{page_num}: 正文较短，请确认是否足够支撑页面")

        if not any(error.startswith(f"P{page_num}:") or error == f"页码重复：P{page_num}" for error in errors):
            slides.append(_slide_from_fields(page_num, fields))

    page_nums = [page_num for page_num, _ in blocks]
    if len(set(page_nums)) == len(page_nums):
        expected = list(range(min(page_nums), max(page_nums) + 1))
        if sorted(page_nums) != expected:
            warnings.append("页码不连续，将按实际页码顺序导入")

    slides.sort(key=lambda slide: int(slide.get("page_num") or 0))
    return ContentPlanValidationResult(
        ok=not errors and bool(slides),
        errors=errors or ([] if slides else ["解析后页数为 0"]),
        warnings=warnings,
        title=_extract_title(text),
        slides=slides,
    )


def parse_content_plan_markdown(markdown: str) -> ContentPlanParseResult:
    result = validate_content_plan_markdown(markdown)
    if not result.ok:
        raise ContentPlanMarkdownError(result.errors, result.warnings)
    return ContentPlanParseResult(title=result.title, slides=result.slides, warnings=result.warnings)


def _clean_project_title(value: str | None, fallback: str = "未命名项目") -> str:
    title = re.sub(r"\s+", " ", str(value or "").strip())
    title = title.strip(" \t\r\n，。；：、,.!?！？—-")
    return title[:100] or fallback


def project_ui_url(project_id: str, frontend_base_url: str = "http://localhost:5173", *, stage: str | None = None) -> str:
    url = f"{frontend_base_url.rstrip('/')}/projects/{project_id}"
    return f"{url}?stage={stage}" if stage else url


def _content_review_ui_url(project_id: str, frontend_base_url: str = "http://localhost:5173") -> str:
    return project_ui_url(project_id, frontend_base_url, stage="content")


def _markdown_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("content") or "").strip())
            else:
                parts.append(str(item).strip())
        return "\n\n".join(part for part in parts if part)
    return str(value).strip()


def _markdown_section(label: str, value: Any) -> str:
    return f"### {label}\n\n{_markdown_value(value)}\n"


def safe_content_plan_filename(title: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', "-", str(title or "").strip()).strip(" .")
    return (name[:80] or "内容规划") + ".md"


def build_strict_content_plan_markdown(project: Project, slides: list[Slide]) -> str:
    lines: list[str] = [f"# {_markdown_value(project.title) or '未命名项目'}", ""]
    for slide in sorted(slides, key=lambda item: int(item.page_num or 0)):
        content = slide.content_json if isinstance(slide.content_json, dict) else {}
        text_content = content.get("text_content") if isinstance(content.get("text_content"), dict) else {}
        page_num = int(slide.page_num or content.get("page_num") or 0)
        page_type = str(content.get("type") or slide.type or "content").strip() or "content"

        lines.extend(
            [
                f"## P{page_num}",
                _markdown_section("类型", page_type),
                _markdown_section("标题", text_content.get("headline")),
                _markdown_section("副标题", text_content.get("subhead")),
                _markdown_section("正文", text_content.get("body")),
                _markdown_section("备注", content.get("speaker_notes")),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def export_content_plan_markdown(project: Project, slides: list[Slide]) -> ContentPlanExportReceipt:
    ordered_slides = sorted(slides, key=lambda item: int(item.page_num or 0))
    markdown = build_strict_content_plan_markdown(project, ordered_slides)
    return ContentPlanExportReceipt(
        project_id=project.id,
        title=project.title,
        slides_count=len(ordered_slides),
        filename=safe_content_plan_filename(f"{project.title}-内容规划"),
        markdown=markdown,
    )


def import_content_plan_markdown(
    db: Session,
    markdown: str,
    *,
    title: str | None = None,
    tester_id: str | None = None,
    source_filename: str | None = None,
    frontend_base_url: str = "http://localhost:5173",
) -> ContentPlanImportReceipt:
    parsed = parse_content_plan_markdown(markdown)
    fallback_title = os.path.splitext(os.path.basename(source_filename or ""))[0] or "未命名项目"
    project_title = _clean_project_title(title or parsed.title, fallback=fallback_title)

    project = Project(
        title=project_title,
        status="planning",
        content_plan_confirmed=False,
        tester_id=tester_id or None,
    )
    db.add(project)
    db.flush()

    for slide_payload in parsed.slides:
        db.add(
            Slide(
                project_id=project.id,
                page_num=int(slide_payload["page_num"]),
                type=str(slide_payload.get("type") or "content"),
                status="pending",
                content_json=slide_payload,
                visual_json={},
                prompt_text=None,
                image_path=None,
                error_msg=None,
            )
        )

    db.commit()
    db.refresh(project)
    return ContentPlanImportReceipt(
        project_id=project.id,
        title=project.title,
        slides_count=len(parsed.slides),
        warnings=parsed.warnings,
        ui_url=_content_review_ui_url(project.id, frontend_base_url),
    )
