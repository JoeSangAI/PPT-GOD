import re
from dataclasses import dataclass
from typing import Any

from app.services.source_pack import FRONT_MATTER_ROLE_TERMS, estimate_tokens, front_matter_role_for_text


DEFAULT_SOURCE_CONTEXT_TOKEN_BUDGET = 120_000


@dataclass
class SourceContext:
    status: str
    text: str
    selected_scopes: list[dict]
    source_stats: dict


class SourceScopeRequired(Exception):
    def __init__(self, payload: dict):
        super().__init__(payload.get("reason") or "source scope required")
        self.payload = payload


CN_NUMBERS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

INTRO_SCOPE_TERMS = tuple(term for terms in FRONT_MATTER_ROLE_TERMS.values() for term in terms)


def _cn_to_int(text: str) -> int | None:
    text = (text or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if text.startswith("十") and len(text) == 2:
        return 10 + CN_NUMBERS.get(text[1], 0)
    if text.endswith("十") and len(text) == 2:
        return CN_NUMBERS.get(text[0], 0) * 10
    if "十" in text and len(text) == 3:
        return CN_NUMBERS.get(text[0], 0) * 10 + CN_NUMBERS.get(text[2], 0)
    return CN_NUMBERS.get(text)


def _chapter_ref_from_text(text: str) -> tuple[int, str] | None:
    match = re.match(r"^\s*第\s*([0-9一二三四五六七八九十百]+)\s*(章|节|部|篇|讲|课)(?:\s+|[:：、.\-]|$)", text or "")
    if not match:
        match = re.match(r"^\s*([0-9一二三四五六七八九十百]+)\s*(部分)(?:\s+|[:：、.\-]|$)", text or "")
    if not match:
        return None
    number = _cn_to_int(match.group(1))
    if not number:
        return None
    unit = "部" if match.group(2) == "部分" else match.group(2)
    return number, unit


def build_brief_source_pack(brief: str) -> dict:
    text = brief or ""
    return {
        "schema_version": 1,
        "document": {
            "filename": "brief",
            "kind": "brief",
        },
        "parse_status": {
            "status": "completed",
        },
        "stats": {
            "pages": 1,
            "chapters": 0,
            "images": 0,
            "text_chars": len(text),
            "estimated_tokens": estimate_tokens(text),
        },
        "pages": [{
            "page_num": 1,
            "text": text,
            "text_chars": len(text),
            "estimated_tokens": estimate_tokens(text),
            "ocr_status": "not_applicable",
        }],
        "chapters": [],
        "images": [],
    }


def _pack_stats(source_packs: list[dict]) -> dict:
    return {
        "documents": len(source_packs),
        "pages": sum(int((pack.get("stats") or {}).get("pages") or len(pack.get("pages") or [])) for pack in source_packs),
        "chapters": sum(int((pack.get("stats") or {}).get("chapters") or len(pack.get("chapters") or [])) for pack in source_packs),
        "images": sum(int((pack.get("stats") or {}).get("images") or len(pack.get("images") or [])) for pack in source_packs),
        "text_chars": sum(int((pack.get("stats") or {}).get("text_chars") or 0) for pack in source_packs),
        "estimated_tokens": sum(int((pack.get("stats") or {}).get("estimated_tokens") or 0) for pack in source_packs),
    }


def _requested_chapter_refs(brief: str) -> list[tuple[int, str]]:
    refs: list[tuple[int, str]] = []
    for match in re.finditer(r"第\s*([0-9一二三四五六七八九十百]+)\s*(章|节|部|篇|讲|课)", brief or ""):
        value = _cn_to_int(match.group(1))
        if value:
            ref = (value, match.group(2))
            if ref not in refs:
                refs.append(ref)
    return refs


def _requested_front_matter_roles(brief: str) -> set[str]:
    compact = re.sub(r"\s+", "", brief or "").lower()
    roles: set[str] = set()
    for role, terms in FRONT_MATTER_ROLE_TERMS.items():
        if any(term.lower().replace(" ", "") in compact for term in terms):
            roles.add(role)
    return roles


def _requested_intro_terms(brief: str) -> list[str]:
    roles = _requested_front_matter_roles(brief)
    return [term for role in roles for term in FRONT_MATTER_ROLE_TERMS.get(role, ())]


def _requests_intro_scope(brief: str) -> bool:
    return bool(_requested_intro_terms(brief))


def _page_num(page: dict) -> int:
    try:
        return int(page.get("page_num") or 0)
    except (TypeError, ValueError):
        return 0


def _line_starts_front_matter_term(raw: str, term: str) -> bool:
    text = re.sub(r"\s+", " ", str(raw or "").strip()).lower()
    clean_term = re.sub(r"\s+", " ", term or "").strip().lower()
    if not text or not clean_term:
        return False
    if text == clean_term:
        return True
    return bool(re.match(rf"^{re.escape(clean_term)}(?:\s+|[:：、.\-]).+", text))


def _front_matter_role_for_page(page: dict, roles: set[str] | None = None) -> tuple[str, str] | None:
    allowed_roles = roles or set(FRONT_MATTER_ROLE_TERMS)
    return front_matter_role_for_text(str(page.get("text") or ""), allowed_roles)


def _page_starts_intro(page: dict, terms: list[str] | None = None) -> bool:
    if terms:
        roles = {role for role, role_terms in FRONT_MATTER_ROLE_TERMS.items() if any(term in role_terms for term in terms)}
    else:
        roles = None
    return _front_matter_role_for_page(page, roles) is not None


def _intro_scope_title(pages: list[dict], terms: list[str] | None = None) -> str:
    if terms:
        roles = {role for role, role_terms in FRONT_MATTER_ROLE_TERMS.items() if any(term in role_terms for term in terms)}
    else:
        roles = None
    for page in pages:
        match = _front_matter_role_for_page(page, roles)
        if match:
            return match[1]
    return "绪论"


def _chapter_pages(pack: dict, chapter: dict) -> list[dict]:
    try:
        start = int(chapter.get("start_page") or 1)
        end = int(chapter.get("end_page") or start)
    except (TypeError, ValueError):
        return []
    pages = []
    for page in pack.get("pages") or []:
        if not isinstance(page, dict):
            continue
        try:
            page_num = int(page.get("page_num") or 0)
        except (TypeError, ValueError):
            continue
        if start <= page_num <= end:
            pages.append(page)
    return pages


def _source_structure_sections(pack: dict) -> list[dict]:
    sections = pack.get("source_structure")
    if not isinstance(sections, list):
        return []
    return [section for section in sections if isinstance(section, dict)]


def _section_int(section: dict, key: str, default: int = 0) -> int:
    try:
        return int(section.get(key) or default)
    except (TypeError, ValueError):
        return default


def _pages_for_section_range(pack: dict, start_page: int, end_page: int) -> list[dict]:
    pages = []
    for page in _all_pages(pack):
        page_num = _page_num(page)
        if start_page <= page_num <= end_page:
            pages.append(page)
    return sorted(pages, key=_page_num)


def _profiled_front_matter_scopes_before_scope(
    pack: dict,
    before_page: int | None = None,
    roles: set[str] | None = None,
) -> list[tuple[dict, list[dict]]]:
    sections = _source_structure_sections(pack)
    if not sections:
        return []
    upper = before_page if before_page and before_page > 1 else None
    requested_roles = roles or set(FRONT_MATTER_ROLE_TERMS)
    front_sections = [
        section for section in sections
        if str(section.get("section_role") or "") in FRONT_MATTER_ROLE_TERMS
        and _section_int(section, "start_page") > 0
        and (upper is None or _section_int(section, "start_page") < upper)
    ]
    if not front_sections:
        return []
    front_sections.sort(key=lambda section: _section_int(section, "start_page"))

    scopes: list[tuple[dict, list[dict]]] = []
    covered_ranges: list[tuple[int, int]] = []
    for section in front_sections:
        role = str(section.get("section_role") or "")
        if role not in requested_roles:
            continue
        start = _section_int(section, "start_page")
        end = _section_int(section, "end_page", start)
        if role == "intro":
            for next_section in front_sections:
                next_start = _section_int(next_section, "start_page")
                if next_start <= start:
                    continue
                next_role = str(next_section.get("section_role") or "")
                if next_role == "guide":
                    end = max(end, _section_int(next_section, "end_page", next_start))
                    continue
                break
        if upper is not None:
            end = min(end, upper - 1)
        if any(start >= covered_start and end <= covered_end for covered_start, covered_end in covered_ranges):
            continue
        pages = _pages_for_section_range(pack, start, end)
        if not pages:
            continue
        covered_ranges.append((start, end))
        title = str(section.get("title") or "").strip() or next(iter(FRONT_MATTER_ROLE_TERMS.get(role, ())), role)
        synthetic_chapter = {
            "chapter_id": section.get("section_id") or role,
            "title": title,
            "start_page": start,
            "end_page": end,
        }
        scopes.append((synthetic_chapter, pages))
    return scopes


def _front_matter_scopes_before_scope(
    pack: dict,
    before_page: int | None = None,
    roles: set[str] | None = None,
) -> list[tuple[dict, list[dict]]]:
    profiled_scopes = _profiled_front_matter_scopes_before_scope(pack, before_page, roles)
    if profiled_scopes:
        return profiled_scopes

    pages = sorted(_all_pages(pack), key=_page_num)
    if not pages:
        return []
    upper = before_page if before_page and before_page > 1 else max(_page_num(page) for page in pages) + 1
    requested_roles = roles or set(FRONT_MATTER_ROLE_TERMS)
    role_starts: list[tuple[int, str, str]] = []
    for page in pages:
        page_num = _page_num(page)
        if page_num <= 0 or page_num >= upper:
            continue
        match = _front_matter_role_for_page(page)
        if not match:
            continue
        role, title = match
        role_starts.append((page_num, role, title))
    selected_starts = [
        item for item in role_starts
        if item[1] in requested_roles
    ]
    if not selected_starts:
        return []
    chapters = pack.get("chapters") if isinstance(pack.get("chapters"), list) else []
    chapter_starts = sorted(
        int(chapter.get("start_page") or 0)
        for chapter in chapters
        if 0 < int(chapter.get("start_page") or 0) < upper
    )
    scopes: list[tuple[dict, list[dict]]] = []
    for start, role, title in selected_starts:
        next_role_starts = [
            page_num
            for page_num, next_role, _title in role_starts
            if page_num > start and not (role == "intro" and next_role == "guide")
        ]
        next_boundaries = [page_num for page_num in [*next_role_starts, *chapter_starts, upper] if page_num > start]
        end = min(next_boundaries) - 1 if next_boundaries else upper - 1
        scoped_pages = [page for page in pages if start <= _page_num(page) <= end]
        if not scoped_pages:
            continue
        synthetic_chapter = {
            "chapter_id": role,
            "title": title,
            "start_page": scoped_pages[0].get("page_num"),
            "end_page": scoped_pages[-1].get("page_num"),
        }
        scopes.append((synthetic_chapter, scoped_pages))
    return scopes


def _intro_pages_before_scope(pack: dict, before_page: int | None = None, terms: list[str] | None = None) -> list[dict]:
    if terms:
        roles = {role for role, role_terms in FRONT_MATTER_ROLE_TERMS.items() if any(term in role_terms for term in terms)}
    else:
        roles = None
    scopes = _front_matter_scopes_before_scope(pack, before_page, roles)
    return scopes[0][1] if scopes else []


def _scope_images(pack: dict, pages: list[dict], chapter: dict | None = None) -> list[dict]:
    page_nums = {
        int(page.get("page_num") or 0)
        for page in pages
        if isinstance(page, dict)
    }
    chapter_id = str((chapter or {}).get("chapter_id") or "")
    images: list[dict] = []
    for image in pack.get("images") or []:
        if not isinstance(image, dict):
            continue
        try:
            page_num = int(image.get("source_page_num") or image.get("pdf_source_page_num") or 0)
        except (TypeError, ValueError):
            page_num = 0
        if page_num not in page_nums:
            continue
        if chapter_id and image.get("chapter_id") and str(image.get("chapter_id")) != chapter_id:
            continue
        images.append(image)
    return images


def _escape_attr(value) -> str:
    return str(value or "").replace('"', "'").replace("\n", " ").strip()


def _scope_text(pack: dict, pages: list[dict], chapter: dict | None = None) -> str:
    doc = pack.get("document") if isinstance(pack.get("document"), dict) else {}
    filename = str(doc.get("filename") or "")
    kind = str(doc.get("kind") or "")
    lines = [f'--- SOURCE filename="{filename}" kind="{kind}" ---']
    if chapter:
        lines.append(
            f'--- CHAPTER id="{chapter.get("chapter_id") or ""}" title="{chapter.get("title") or ""}" '
            f'pages="{chapter.get("start_page")}-{chapter.get("end_page")}" ---'
        )
    for page in pages:
        page_num = int(page.get("page_num") or 1)
        chapter_label = f' chapter="{chapter.get("title")}"' if chapter and chapter.get("title") else ""
        lines.append(f'--- PAGE {page_num}{chapter_label} ---')
        lines.append(str(page.get("text") or "").strip())
    images = _scope_images(pack, pages, chapter)
    if images:
        lines.append("--- AVAILABLE_FIGURES ---")
        for idx, image in enumerate(images[:24], start=1):
            page_num = int(image.get("source_page_num") or image.get("pdf_source_page_num") or 0)
            bbox = image.get("bbox") if isinstance(image.get("bbox"), list) else []
            nearby_text = _escape_attr(image.get("nearby_text"))[:220]
            figure_id = _escape_attr(image.get("id") or f"{filename}:p{page_num}:fig{idx}")
            lines.append(
                f'FIGURE figure_id="{figure_id}" source_document="{_escape_attr(image.get("source_document") or filename)}" '
                f'source_type="{_escape_attr(image.get("source_type") or kind)}" source_page_num="{page_num}" '
                f'chapter_id="{_escape_attr(image.get("chapter_id"))}" bbox="{_escape_attr(bbox)}" '
                f'figure_role="{_escape_attr(image.get("figure_role"))}" '
                f'content_significance="{_escape_attr(image.get("content_significance"))}" '
                f'image_width="{_escape_attr(image.get("image_width"))}" image_height="{_escape_attr(image.get("image_height"))}" '
                f'bbox_area="{_escape_attr(image.get("bbox_area"))}" '
                f'nearby_text="{nearby_text}"'
            )
    return "\n".join(lines).strip()


def _page_token_total(pages: list[dict]) -> int:
    total = 0
    for page in pages:
        try:
            total += int(page.get("estimated_tokens") or 0)
        except (TypeError, ValueError):
            total += estimate_tokens(str(page.get("text") or ""))
    return total


def _suggested_scopes(source_packs: list[dict]) -> list[dict]:
    suggestions: list[dict] = []
    for pack in source_packs:
        doc = pack.get("document") if isinstance(pack.get("document"), dict) else {}
        filename = str(doc.get("filename") or "")
        chapters = pack.get("chapters") if isinstance(pack.get("chapters"), list) else []
        for chapter in chapters[:8]:
            suggestions.append({
                "source_document": filename,
                "chapter_id": chapter.get("chapter_id"),
                "title": chapter.get("title"),
                "start_page": chapter.get("start_page"),
                "end_page": chapter.get("end_page"),
                "estimated_tokens": _page_token_total(_chapter_pages(pack, chapter)),
            })
        if not chapters:
            suggestions.append({
                "source_document": filename,
                "title": filename,
                "start_page": 1,
                "end_page": (pack.get("stats") or {}).get("pages") or len(pack.get("pages") or []),
                "estimated_tokens": int((pack.get("stats") or {}).get("estimated_tokens") or 0),
            })
    return suggestions[:12]


def _raise_scope_required(source_packs: list[dict], *, reason: str) -> None:
    raise SourceScopeRequired({
        "status": "needs_scope",
        "reason": reason,
        "suggested_scopes": _suggested_scopes(source_packs),
        "source_stats": _pack_stats(source_packs),
    })


def _all_pages(pack: dict) -> list[dict]:
    return [page for page in (pack.get("pages") or []) if isinstance(page, dict)]


def _selected_scope_payload(pack: dict, chapter: dict | None, pages: list[dict]) -> dict:
    doc = pack.get("document") if isinstance(pack.get("document"), dict) else {}
    payload: dict[str, Any] = {
        "source_document": doc.get("filename"),
        "source_type": doc.get("kind"),
        "start_page": pages[0].get("page_num") if pages else None,
        "end_page": pages[-1].get("page_num") if pages else None,
        "estimated_tokens": _page_token_total(pages),
    }
    if chapter:
        payload.update({
            "chapter_id": chapter.get("chapter_id"),
            "title": chapter.get("title"),
            "start_page": chapter.get("start_page"),
            "end_page": chapter.get("end_page"),
        })
    return payload


def build_source_context(
    brief: str,
    source_packs: list[dict] | None = None,
    *,
    token_budget: int = DEFAULT_SOURCE_CONTEXT_TOKEN_BUDGET,
) -> SourceContext:
    packs = [pack for pack in (source_packs or []) if isinstance(pack, dict)]
    if not packs:
        packs = [build_brief_source_pack(brief)]

    requested_chapters = _requested_chapter_refs(brief)
    requested_front_matter_roles = _requested_front_matter_roles(brief)
    requested_intro = bool(requested_front_matter_roles)
    selected_parts: list[str] = []
    selected_scopes: list[dict] = []
    selected_tokens = 0

    if requested_chapters or requested_intro:
        entries: list[tuple[int, dict, dict | None, list[dict]]] = []
        for pack in packs:
            chapters = pack.get("chapters") if isinstance(pack.get("chapters"), list) else []
            matched_chapters: list[dict] = []
            for chapter in chapters:
                chapter_ref = _chapter_ref_from_text(str(chapter.get("title") or ""))
                if chapter_ref not in requested_chapters:
                    continue
                pages = _chapter_pages(pack, chapter)
                if not pages:
                    continue
                matched_chapters.append(chapter)
                entries.append((int(chapter.get("start_page") or pages[0].get("page_num") or 0), pack, chapter, pages))
            if requested_intro:
                before_page = min(
                    [int(chapter.get("start_page") or 0) for chapter in matched_chapters if int(chapter.get("start_page") or 0) > 0],
                    default=None,
                )
                for synthetic_chapter, front_pages in _front_matter_scopes_before_scope(
                    pack,
                    before_page,
                    requested_front_matter_roles,
                ):
                    entries.append((_page_num(front_pages[0]), pack, synthetic_chapter, front_pages))

        seen_ranges: set[tuple[str, int, int, str]] = set()
        for _start, pack, chapter, pages in sorted(entries, key=lambda item: item[0]):
            doc = pack.get("document") if isinstance(pack.get("document"), dict) else {}
            start_page = _page_num(pages[0]) if pages else 0
            end_page = _page_num(pages[-1]) if pages else start_page
            key = (str(doc.get("filename") or ""), start_page, end_page, str((chapter or {}).get("title") or ""))
            if key in seen_ranges:
                continue
            seen_ranges.add(key)
            selected_tokens += _page_token_total(pages)
            selected_parts.append(_scope_text(pack, pages, chapter))
            selected_scopes.append(_selected_scope_payload(pack, chapter, pages))

        if selected_parts:
            if selected_tokens > token_budget:
                _raise_scope_required(packs, reason="所选章节内容超过本轮高质量规划预算，请继续缩小章节或页码范围。")
            return SourceContext(
                status="ready",
                text="\n\n".join(selected_parts),
                selected_scopes=selected_scopes,
                source_stats=_pack_stats(packs),
            )

    total_tokens = _pack_stats(packs)["estimated_tokens"]
    if total_tokens > token_budget:
        _raise_scope_required(
            packs,
            reason="上传材料超过本轮高质量规划预算，需要先选择章节、页码范围或优先文档后再生成。",
        )

    for pack in packs:
        pages = _all_pages(pack)
        if not pages:
            continue
        selected_parts.append(_scope_text(pack, pages))
        selected_scopes.append(_selected_scope_payload(pack, None, pages))

    return SourceContext(
        status="ready",
        text="\n\n".join(selected_parts).strip(),
        selected_scopes=selected_scopes,
        source_stats=_pack_stats(packs),
    )
