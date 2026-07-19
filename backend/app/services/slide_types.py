from __future__ import annotations


CANONICAL_SLIDE_TYPES = (
    "cover",
    "toc",
    "section",
    "content",
    "data",
    "hero",
    "quote",
    "ending",
)
CANONICAL_SLIDE_TYPE_SET = frozenset(CANONICAL_SLIDE_TYPES)

# These values may still exist in older stored projects. They are explicit
# migration aliases only; new user/Agent input must use a canonical value.
LEGACY_STORED_SLIDE_TYPE_ALIASES = {
    "agenda": "toc",
    "chart": "data",
    "table": "data",
    "content_dense": "content",
    "content_hero": "content",
    "content_split": "content",
    "content_top": "content",
}


class UnsupportedSlideTypeError(ValueError):
    pass


def normalize_slide_type(
    value: object,
    *,
    allow_legacy_stored_aliases: bool = False,
    default: str | None = None,
) -> str:
    raw = str(value or "").strip().lower()
    key = raw.replace("-", "_").replace(" ", "_")
    if not key:
        if default in CANONICAL_SLIDE_TYPE_SET:
            return str(default)
        raise UnsupportedSlideTypeError("页面类型不能为空")
    if key in CANONICAL_SLIDE_TYPE_SET:
        return key
    if allow_legacy_stored_aliases and key in LEGACY_STORED_SLIDE_TYPE_ALIASES:
        return LEGACY_STORED_SLIDE_TYPE_ALIASES[key]
    allowed = "、".join(CANONICAL_SLIDE_TYPES)
    raise UnsupportedSlideTypeError(f"不支持的页面类型「{value}」；仅支持：{allowed}")
