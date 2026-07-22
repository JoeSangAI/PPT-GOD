from __future__ import annotations

import copy
from collections import defaultdict
from dataclasses import dataclass, field
import os
import re
from typing import Any

from sqlalchemy.orm import Session

from app.models.models import Project, Slide
from app.services.artifact_versions import digest, with_stale_flags
from app.services.slide_types import CANONICAL_SLIDE_TYPES, CANONICAL_SLIDE_TYPE_SET, normalize_slide_type
from app.services.visual_block_renderer import blocks_to_markdown, normalize_content_blocks
from app.utils.text_cleaning import normalize_markdown_content


ALLOWED_SLIDE_TYPES = CANONICAL_SLIDE_TYPE_SET
REQUIRED_FIELDS = ("类型", "标题", "副标题", "正文", "备注")
OPTIONAL_EMPTY_FIELDS = {"副标题", "备注"}
BODY_REQUIRED_SLIDE_TYPES = {"content", "data"}


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


@dataclass
class ContentPlanSyncReceipt:
    project_id: str
    applied: bool
    preview_token: str
    summary: dict[str, int]
    changes: list[dict[str, Any]]
    warnings: list[str]
    readback: dict[str, Any] | None = None


class ContentPlanMarkdownError(ValueError):
    def __init__(self, errors: list[str], warnings: list[str] | None = None):
        self.errors = errors
        self.warnings = warnings or []
        super().__init__("; ".join(errors))


class ContentPlanSyncConflictError(ContentPlanMarkdownError):
    pass


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
            allowed = "、".join(CANONICAL_SLIDE_TYPES)
            errors.append(f"P{page_num}: 类型「{slide_type}」不合法；仅支持：{allowed}")

        for field_name, value in fields.items():
            if value.strip():
                continue
            if field_name == "正文" and slide_type not in BODY_REQUIRED_SLIDE_TYPES:
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
    # Keep browser routes outside the /projects API namespace. When the backend
    # serves the SPA on :8000, a direct /projects/{id} navigation is otherwise
    # handled as an API request before the SPA fallback can run.
    url = f"{frontend_base_url.rstrip('/')}/app/projects/{project_id}"
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


def content_body_storage_state(content_json: dict | None) -> dict[str, Any]:
    """Describe the body exactly as the Web editor resolves it.

    The editor prefers non-empty ``content_blocks`` arrays over ``text_content.body``.
    Keep this resolution in one shared backend helper so export, diff, status, and
    post-apply verification cannot silently read a stale mirror.
    """
    content = content_json if isinstance(content_json, dict) else {}
    text = content.get("text_content") if isinstance(content.get("text_content"), dict) else {}
    text_body = normalize_markdown_content(_markdown_value(text.get("body")))
    raw_blocks = content.get("content_blocks")
    normalized_blocks = normalize_content_blocks(content) if isinstance(raw_blocks, list) else []
    has_editor_blocks = bool(normalized_blocks)
    blocks_body = normalize_markdown_content(blocks_to_markdown(normalized_blocks)) if has_editor_blocks else ""
    effective_body = blocks_body if has_editor_blocks else text_body
    return {
        "effective_body": effective_body,
        "text_body": text_body,
        "blocks_body": blocks_body,
        "has_editor_blocks": has_editor_blocks,
        "content_blocks_count": len(normalized_blocks),
        "consistent": text_body == effective_body,
    }


def effective_content_body_markdown(content_json: dict | None) -> str:
    return str(content_body_storage_state(content_json).get("effective_body") or "")


def strict_markdown_content_blocks(body: Any) -> list[dict[str, Any]]:
    return [
        {
            "id": "body",
            "kind": "markdown",
            "markdown": normalize_markdown_content(_markdown_value(body)),
        }
    ]


def safe_content_plan_filename(title: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', "-", str(title or "").strip()).strip(" .")
    return (name[:80] or "内容规划") + ".md"


def build_strict_content_plan_markdown(project: Project, slides: list[Slide]) -> str:
    lines: list[str] = [f"# {_markdown_value(project.title) or '未命名项目'}", ""]
    for slide in sorted(slides, key=lambda item: int(item.page_num or 0)):
        content = slide.content_json if isinstance(slide.content_json, dict) else {}
        text_content = content.get("text_content") if isinstance(content.get("text_content"), dict) else {}
        page_num = int(slide.page_num or content.get("page_num") or 0)
        page_type = normalize_slide_type(
            content.get("type") or slide.type,
            allow_legacy_stored_aliases=True,
            default="content",
        )

        lines.extend(
            [
                f"## P{page_num}",
                _markdown_section("类型", page_type),
                _markdown_section("标题", text_content.get("headline")),
                _markdown_section("副标题", text_content.get("subhead")),
                _markdown_section("正文", effective_content_body_markdown(content)),
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
                content_json=_new_strict_slide_content(slide_payload),
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


def _normalized_match_text(value: Any) -> str:
    return re.sub(r"\s+", " ", _markdown_value(value)).strip().casefold()


def _slide_explicit_content(slide: Slide) -> dict[str, Any]:
    content = slide.content_json if isinstance(slide.content_json, dict) else {}
    text = content.get("text_content") if isinstance(content.get("text_content"), dict) else {}
    return {
        "page_num": int(slide.page_num or content.get("page_num") or 0),
        "type": normalize_slide_type(
            content.get("type") or slide.type,
            allow_legacy_stored_aliases=True,
            default="content",
        ),
        "headline": _markdown_value(text.get("headline")),
        "subhead": _markdown_value(text.get("subhead")),
        "body": effective_content_body_markdown(content),
        "speaker_notes": _markdown_value(content.get("speaker_notes")),
    }


def _payload_explicit_content(payload: dict[str, Any]) -> dict[str, Any]:
    text = payload.get("text_content") if isinstance(payload.get("text_content"), dict) else {}
    return {
        "page_num": int(payload.get("page_num") or 0),
        "type": normalize_slide_type(payload.get("type"), default="content"),
        "headline": _markdown_value(text.get("headline")),
        "subhead": _markdown_value(text.get("subhead")),
        "body": _markdown_value(text.get("body")),
        "speaker_notes": _markdown_value(payload.get("speaker_notes")),
    }


def _content_match_signature(explicit: dict[str, Any]) -> tuple[str, ...]:
    return (
        str(explicit["type"]),
        _normalized_match_text(explicit["headline"]),
        _normalized_match_text(explicit["subhead"]),
        _normalized_match_text(explicit["body"]),
        _normalized_match_text(explicit["speaker_notes"]),
    )


def _headline_match_key(explicit: dict[str, Any]) -> str:
    return _normalized_match_text(explicit["headline"])


def _changed_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    fields = ("page_num", "type", "headline", "subhead", "body", "speaker_notes")
    return [field for field in fields if before.get(field) != after.get(field)]


def _slide_changed_fields(slide: Slide, before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    fields = _changed_fields(before, after)
    body_state = content_body_storage_state(slide.content_json)
    if not body_state["consistent"] and "body_storage" not in fields:
        fields.append("body_storage")
    return fields


def _asset_summary(slide: Slide) -> dict[str, Any]:
    return {
        "reference_images": len(slide.reference_images or []),
        "versions": len(slide.versions or []),
        "has_visual": bool(slide.visual_json),
        "has_prompt": bool(slide.prompt_text),
        "has_image": bool(slide.image_path),
    }


def _match_existing_slides(
    existing_slides: list[Slide],
    target_payloads: list[dict[str, Any]],
) -> tuple[dict[int, tuple[Slide, str]], set[str]]:
    """Match target indexes to stable slides, using only high-confidence deterministic anchors."""
    existing = sorted(existing_slides, key=lambda slide: (int(slide.page_num or 0), str(slide.id)))
    old_explicit = {slide.id: _slide_explicit_content(slide) for slide in existing}
    target_explicit = [_payload_explicit_content(payload) for payload in target_payloads]
    matched: dict[int, tuple[Slide, str]] = {}
    used_ids: set[str] = set()

    def pair_by_key(old_key, target_key, strategy: str, *, skip_empty: bool = False) -> None:
        old_groups: dict[Any, list[Slide]] = defaultdict(list)
        target_groups: dict[Any, list[int]] = defaultdict(list)
        for slide in existing:
            if slide.id in used_ids:
                continue
            key = old_key(old_explicit[slide.id])
            if skip_empty and not key:
                continue
            old_groups[key].append(slide)
        for index, explicit in enumerate(target_explicit):
            if index in matched:
                continue
            key = target_key(explicit)
            if skip_empty and not key:
                continue
            target_groups[key].append(index)
        for key, target_indexes in target_groups.items():
            old_items = old_groups.get(key) or []
            for slide, target_index in zip(old_items, target_indexes):
                matched[target_index] = (slide, strategy)
                used_ids.add(slide.id)

    pair_by_key(_content_match_signature, _content_match_signature, "exact_content")
    pair_by_key(_headline_match_key, _headline_match_key, "headline", skip_empty=True)

    old_by_page = {
        int(slide.page_num or 0): slide
        for slide in existing
        if slide.id not in used_ids
    }
    for target_index, explicit in enumerate(target_explicit):
        if target_index in matched:
            continue
        slide = old_by_page.get(int(explicit["page_num"]))
        if slide is None or slide.id in used_ids:
            continue
        matched[target_index] = (slide, "same_page")
        used_ids.add(slide.id)

    return matched, used_ids


def _sync_preview_token(project: Project, existing_slides: list[Slide], target_payloads: list[dict[str, Any]]) -> str:
    current = [
        {
            "id": slide.id,
            **_slide_explicit_content(slide),
            "body_storage": content_body_storage_state(slide.content_json),
        }
        for slide in sorted(existing_slides, key=lambda item: (int(item.page_num or 0), str(item.id)))
    ]
    target = [_payload_explicit_content(payload) for payload in target_payloads]
    return digest(
        {
            "project_id": project.id,
            "project_status": project.status,
            "content_plan_confirmed": bool(project.content_plan_confirmed),
            "current": current,
            "target": target,
        }
    )


def _merge_strict_content(
    slide: Slide,
    target_payload: dict[str, Any],
    *,
    replace_editor_body: bool,
) -> dict[str, Any]:
    current = copy.deepcopy(slide.content_json) if isinstance(slide.content_json, dict) else {}
    current_text = current.get("text_content") if isinstance(current.get("text_content"), dict) else {}
    target_text = target_payload.get("text_content") if isinstance(target_payload.get("text_content"), dict) else {}
    current["page_num"] = int(target_payload["page_num"])
    current["type"] = str(target_payload.get("type") or "content")
    current["text_content"] = {
        **current_text,
        "headline": target_text.get("headline") or "",
        "subhead": target_text.get("subhead") or "",
        "body": target_text.get("body") or "",
    }
    if replace_editor_body:
        current["content_blocks"] = strict_markdown_content_blocks(target_text.get("body"))
    current["speaker_notes"] = target_payload.get("speaker_notes") or ""
    return current


def _new_strict_slide_content(target_payload: dict[str, Any]) -> dict[str, Any]:
    content = copy.deepcopy(target_payload)
    text = content.get("text_content") if isinstance(content.get("text_content"), dict) else {}
    content["content_blocks"] = strict_markdown_content_blocks(text.get("body"))
    return content


def _sync_readback(
    target_payloads: list[dict[str, Any]],
    matches: dict[int, tuple[Slide, str]],
    added_by_target_index: dict[int, Slide],
) -> dict[str, Any]:
    slides: list[dict[str, Any]] = []
    for target_index, target_payload in enumerate(target_payloads):
        slide = matches.get(target_index, (None, ""))[0] or added_by_target_index.get(target_index)
        if slide is None:
            slides.append({"target_index": target_index, "ok": False, "error": "slide_missing_after_sync"})
            continue
        expected = _payload_explicit_content(target_payload)
        actual = _slide_explicit_content(slide)
        body_state = content_body_storage_state(slide.content_json)
        mismatched_fields = _changed_fields(actual, expected)
        if not body_state["consistent"]:
            mismatched_fields.append("body_storage")
        slides.append(
            {
                "slide_id": slide.id,
                "page_num": slide.page_num,
                "ok": not mismatched_fields,
                "mismatched_fields": mismatched_fields,
                "body_storage_consistent": body_state["consistent"],
                "effective_body": body_state["effective_body"],
            }
        )
    return {
        "ok": all(item.get("ok") for item in slides),
        "slides_count": len(slides),
        "slides": slides,
    }


def sync_content_plan_markdown(
    db: Session,
    project: Project,
    markdown: str,
    *,
    apply: bool = False,
    expected_preview_token: str | None = None,
) -> ContentPlanSyncReceipt:
    """Preview or apply a strict Markdown plan to an existing project in place.

    Project workflow fields are intentionally untouched. Retained slides keep their ids,
    visual artifacts, images, references, versions, locks, and statuses; changed content
    merely marks retained visual metadata as stale.
    """
    parsed = parse_content_plan_markdown(markdown)
    existing_slides = (
        db.query(Slide)
        .filter(Slide.project_id == project.id)
        .order_by(Slide.page_num, Slide.id)
        .all()
    )
    matched, used_ids = _match_existing_slides(existing_slides, parsed.slides)
    preview_token = _sync_preview_token(project, existing_slides, parsed.slides)
    if apply and not expected_preview_token:
        raise ContentPlanMarkdownError(["应用更新前必须提供 dry-run 返回的 preview_token"])
    if apply and expected_preview_token != preview_token:
        raise ContentPlanSyncConflictError(["项目内容在预览后发生了变化，请重新 dry-run 后再应用"])

    changes: list[dict[str, Any]] = []
    warnings = list(parsed.warnings)
    internal_matches: list[tuple[int, Slide, dict[str, Any], list[str], dict[str, Any]]] = []

    for target_index, target_payload in enumerate(parsed.slides):
        after = _payload_explicit_content(target_payload)
        matched_item = matched.get(target_index)
        if matched_item is None:
            changes.append(
                {
                    "action": "added",
                    "slide_id": None,
                    "from_page": None,
                    "to_page": after["page_num"],
                    "before": None,
                    "after": after,
                    "changed_fields": list(after.keys()),
                    "match_strategy": None,
                    "assets": None,
                }
            )
            continue

        slide, strategy = matched_item
        before = _slide_explicit_content(slide)
        fields = _slide_changed_fields(slide, before, after)
        action = "changed" if fields else "unchanged"
        change = {
            "action": action,
            "slide_id": slide.id,
            "from_page": before["page_num"],
            "to_page": after["page_num"],
            "before": before,
            "after": after,
            "changed_fields": fields,
            "match_strategy": strategy,
            "assets": _asset_summary(slide),
        }
        changes.append(change)
        internal_matches.append((target_index, slide, target_payload, fields, change))
        if strategy == "same_page" and "headline" in fields:
            assets = change["assets"] or {}
            if any(
                (
                    assets.get("reference_images"),
                    assets.get("versions"),
                    assets.get("has_visual"),
                    assets.get("has_prompt"),
                    assets.get("has_image"),
                )
            ):
                warnings.append(
                    f"P{after['page_num']} 仅按相同页码复用了原页面 ID，且标题已变化；请复核该页素材绑定是否仍然适用"
                )

    deleted_slides = [slide for slide in existing_slides if slide.id not in used_ids]
    for slide in deleted_slides:
        before = _slide_explicit_content(slide)
        assets = _asset_summary(slide)
        changes.append(
            {
                "action": "deleted",
                "slide_id": slide.id,
                "from_page": before["page_num"],
                "to_page": None,
                "before": before,
                "after": None,
                "changed_fields": [],
                "match_strategy": None,
                "assets": assets,
            }
        )
        if any(
            (
                assets["reference_images"],
                assets["versions"],
                assets["has_visual"],
                assets["has_prompt"],
                assets["has_image"],
            )
        ):
            warnings.append(
                f"删除 P{before['page_num']}「{before['headline']}」会同时删除该页绑定的参考图或历史版本；请在应用前复核"
            )

    summary = {action: sum(1 for change in changes if change["action"] == action) for action in ("changed", "added", "deleted", "unchanged")}
    summary["total_before"] = len(existing_slides)
    summary["total_after"] = len(parsed.slides)

    if apply:
        for _target_index, slide, target_payload, fields, _change in internal_matches:
            if not fields:
                continue
            slide.page_num = int(target_payload["page_num"])
            slide.type = str(target_payload.get("type") or "content")
            slide.content_json = _merge_strict_content(
                slide,
                target_payload,
                replace_editor_body="body" in fields,
            )
            visual = copy.deepcopy(slide.visual_json) if isinstance(slide.visual_json, dict) else {}
            if "page_num" in visual:
                visual["page_num"] = slide.page_num
            slide.visual_json = with_stale_flags(visual, content=True)
            slide.error_msg = None

        for slide in deleted_slides:
            db.delete(slide)

        added_changes = iter(change for change in changes if change["action"] == "added")
        added_by_target_index: dict[int, Slide] = {}
        for target_index, target_payload in enumerate(parsed.slides):
            if target_index in matched:
                continue
            new_slide = Slide(
                project_id=project.id,
                page_num=int(target_payload["page_num"]),
                type=str(target_payload.get("type") or "content"),
                status="pending",
                content_json=_new_strict_slide_content(target_payload),
                visual_json={},
                prompt_text=None,
                image_path=None,
                error_msg=None,
            )
            db.add(new_slide)
            db.flush()
            added_by_target_index[target_index] = new_slide
            next(added_changes)["slide_id"] = new_slide.id

        db.flush()
        readback = _sync_readback(parsed.slides, matched, added_by_target_index)
        if not readback["ok"]:
            raise ContentPlanMarkdownError(["内容同步后的 UI 正文读回校验失败，事务已回滚"])
        db.commit()
    else:
        readback = None

    return ContentPlanSyncReceipt(
        project_id=project.id,
        applied=bool(apply),
        preview_token=preview_token,
        summary=summary,
        changes=changes,
        warnings=warnings,
        readback=readback,
    )
