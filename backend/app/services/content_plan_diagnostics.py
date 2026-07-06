from __future__ import annotations

import re
from typing import Any

from app.services.content_plan import PAGE_MAP_DOCUMENT_LIMIT, PAGE_MAP_SOURCE_DRAFT_LIMIT
from app.services.source_context import DEFAULT_SOURCE_CONTEXT_TOKEN_BUDGET
from app.services.source_pack import estimate_tokens


def _attr_or_key(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def build_content_source_diagnostic(
    *,
    topic: str,
    documents: str,
    source_context: Any = None,
    source_draft_markdown: str = "",
    target_page_count: int | None = None,
    requested_page_count: int | None = None,
) -> dict[str, Any]:
    documents_text = documents or ""
    source_draft_text = source_draft_markdown or ""
    selected_scopes = _attr_or_key(source_context, "selected_scopes", []) or []
    source_stats = _attr_or_key(source_context, "source_stats", {}) or {}
    return {
        "topic_chars": len(topic or ""),
        "documents_chars": len(documents_text),
        "documents_estimated_tokens": estimate_tokens(documents_text),
        "source_context_status": _attr_or_key(source_context, "status", None),
        "source_context_token_budget": DEFAULT_SOURCE_CONTEXT_TOKEN_BUDGET,
        "source_context_stats": source_stats if isinstance(source_stats, dict) else {},
        "selected_scope_count": len(selected_scopes) if isinstance(selected_scopes, list) else 0,
        "requested_page_count": requested_page_count,
        "target_page_count": target_page_count,
        "page_map_document_limit_chars": PAGE_MAP_DOCUMENT_LIMIT,
        "page_map_document_will_truncate": len(documents_text) > PAGE_MAP_DOCUMENT_LIMIT,
        "source_draft_chars": len(source_draft_text),
        "source_draft_limit_chars": PAGE_MAP_SOURCE_DRAFT_LIMIT,
        "source_draft_will_truncate": len(source_draft_text) > PAGE_MAP_SOURCE_DRAFT_LIMIT,
    }


def _page_body_text(page: dict) -> str:
    content = page.get("text_content") if isinstance(page.get("text_content"), dict) else {}
    body = content.get("body")
    if isinstance(body, list):
        return "\n".join(str(item or "").strip() for item in body if str(item or "").strip())
    return str(body or "").strip()


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).lower()


def build_content_outline_quality_diagnostic(
    outline: list[dict],
    *,
    target_page_count: int | None = None,
    min_pages: int | None = None,
) -> dict[str, Any]:
    pages = [page for page in (outline or []) if isinstance(page, dict)]
    empty_body_pages: list[int] = []
    thin_body_pages: list[int] = []
    headline_pages: dict[str, dict[str, Any]] = {}

    for index, page in enumerate(pages, start=1):
        page_num = int(page.get("page_num") or index)
        page_type = str(page.get("type") or "content").strip().lower()
        content = page.get("text_content") if isinstance(page.get("text_content"), dict) else {}
        headline = str(content.get("headline") or "").strip()
        if page_type not in {"cover", "toc", "agenda", "ending"}:
            body = _page_body_text(page)
            if not body:
                empty_body_pages.append(page_num)
            if len(_compact(body)) < 12:
                thin_body_pages.append(page_num)
            headline_key = _compact(headline)
            if headline_key:
                entry = headline_pages.setdefault(headline_key, {"headline": headline, "pages": []})
                entry["pages"].append(page_num)

    duplicates = [
        {"headline": item["headline"], "pages": item["pages"]}
        for item in headline_pages.values()
        if len(item["pages"]) > 1
    ]
    minimum = int(min_pages or 0)
    target = int(target_page_count or 0)
    return {
        "outline_page_count": len(pages),
        "target_page_count": target_page_count,
        "min_pages": min_pages,
        "below_min_pages": bool(minimum and len(pages) < minimum),
        "below_target_pages": bool(target and len(pages) < target),
        "empty_body_pages": empty_body_pages,
        "thin_body_pages": thin_body_pages,
        "duplicate_headlines": duplicates,
    }
