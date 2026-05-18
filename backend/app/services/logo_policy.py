from typing import Any, Mapping


DEFAULT_LOGO_ANCHOR = "top-right"
LOGO_WIDTH_RATIOS = {"small": 0.085, "large": 0.18}
LOGO_HEIGHT_RATIOS = {"small": 0.085, "large": 0.18}
LOGO_ANCHORS = {"top-left", "top-right", "bottom-left", "bottom-right"}
LOGO_PLACEMENTS = LOGO_ANCHORS | {"center", "lower-center", "title-block-center"}
LOGO_RENDER_VARIANTS = {"full", "symbol", "omit"}
LOGO_REVIEW_CONFIRMED_STATUSES = {"auto_confirmed", "user_confirmed"}
LOGO_REVIEW_NON_CONFIRMED_STATUSES = {"needs_review", "dismissed", "not_logo"}
LOGO_OPTIONAL_PAGE_TYPES = {"section", "hero", "quote"}
LOGO_OPTIONAL_LAYOUTS = {"hero"}
ANCHOR_LABELS = {
    "top-left": "top-left safe corner",
    "top-right": "top-right safe corner",
    "bottom-left": "bottom-left safe corner",
    "bottom-right": "bottom-right safe corner",
    "center": "center brand lockup area",
    "lower-center": "lower-center brand lockup area",
    "title-block-center": "title/text block brand lockup area",
}


def _as_dict(source: Any) -> dict:
    if isinstance(source, Mapping):
        data = dict(source.get("visual_json") or {}) if isinstance(source.get("visual_json"), Mapping) else {}
        for key, value in source.items():
            if key != "visual_json" and key not in data:
                data[key] = value
        return data
    visual_json = getattr(source, "visual_json", None)
    if isinstance(visual_json, Mapping):
        data = dict(visual_json)
    else:
        data = {}
    if "type" not in data and getattr(source, "type", None):
        data["type"] = getattr(source, "type")
    if "page_num" not in data and getattr(source, "page_num", None):
        data["page_num"] = getattr(source, "page_num")
    return data


def should_show_logo(page: Any) -> bool:
    """Decide whether the uploaded logo should be attached/rendered on a page."""
    data = _as_dict(page)
    page_type = str(data.get("type") or "").lower()
    layout = str(data.get("layout") or "").lower()
    optional_page = page_type in LOGO_OPTIONAL_PAGE_TYPES or layout in LOGO_OPTIONAL_LAYOUTS

    explicit = data.get("logo_policy")
    if isinstance(explicit, Mapping) and str(explicit.get("render_variant") or "").strip().lower() == "omit":
        return False if optional_page else True
    if isinstance(explicit, Mapping) and "show_logo" in explicit:
        return bool(explicit.get("show_logo")) if optional_page else True

    if page_type in {"cover", "ending"}:
        return True
    if page_type in {"hero", "quote"}:
        return False
    if layout in {"hero"}:
        return False
    return True


def normalize_logo_anchor(anchor: str | None) -> str:
    anchor = str(anchor or "").strip().lower().replace("_", "-")
    return anchor if anchor in LOGO_ANCHORS else DEFAULT_LOGO_ANCHOR


def normalize_logo_placement(placement: str | None, fallback: str = DEFAULT_LOGO_ANCHOR) -> str:
    placement = str(placement or "").strip().lower().replace("_", "-")
    return placement if placement in LOGO_PLACEMENTS else fallback


def logo_anchor_from_ref(ref: Any | None) -> str:
    if ref is None:
        return DEFAULT_LOGO_ANCHOR
    if isinstance(ref, Mapping):
        return normalize_logo_anchor(ref.get("logo_anchor"))
    return normalize_logo_anchor(getattr(ref, "logo_anchor", None))


def _asset_analysis_for_ref(ref: Any | None) -> Mapping:
    if ref is None:
        return {}
    if isinstance(ref, Mapping):
        analysis = ref.get("asset_analysis")
    else:
        analysis = getattr(ref, "asset_analysis", None)
    return analysis if isinstance(analysis, Mapping) else {}


def logo_review_status(ref: Any | None) -> str:
    """Return the review state for a logo, with safe legacy defaults."""
    analysis = _asset_analysis_for_ref(ref)
    status = str(analysis.get("review_status") or "").strip().lower()
    if status in LOGO_REVIEW_CONFIRMED_STATUSES or status in LOGO_REVIEW_NON_CONFIRMED_STATUSES:
        return status

    # Existing manual uploads often have no analysis at all; keep them usable.
    if not analysis:
        return "auto_confirmed"

    # Cover-only PPT detections are guesses. Keep them visible for user review,
    # but do not let them into the protected brand lockup until confirmed.
    if str(analysis.get("classification") or "").strip().lower() == "logo_candidate":
        return "needs_review"
    return "auto_confirmed"


def is_logo_confirmed(ref: Any | None) -> bool:
    return logo_review_status(ref) in LOGO_REVIEW_CONFIRMED_STATUSES


def logo_policy_for_page(page: Any) -> dict:
    data = _as_dict(page)
    show_logo = should_show_logo(data)
    explicit = data.get("logo_policy")
    page_type = str(data.get("type") or "").lower()
    default_placement = "center" if page_type == "cover" else DEFAULT_LOGO_ANCHOR
    anchor = (
        normalize_logo_placement(explicit.get("placement"), default_placement)
        if isinstance(explicit, Mapping) and explicit.get("placement")
        else default_placement
    )
    scale = str(explicit.get("scale")) if isinstance(explicit, Mapping) and explicit.get("scale") else (
        "large" if page_type == "cover" else "small"
    )
    render_variant = str(explicit.get("render_variant") or "").strip().lower() if isinstance(explicit, Mapping) else ""
    if render_variant == "symbol":
        render_variant = ""
    if show_logo and render_variant == "omit":
        render_variant = ""
    return {
        "show_logo": show_logo,
        "placement": anchor,
        "scale": scale,
        "visibility": "unobtrusive" if show_logo else "omit",
        **({"render_variant": render_variant} if render_variant in LOGO_RENDER_VARIANTS else {}),
    }


def logo_prompt_instruction(page: Any) -> str:
    data = _as_dict(page)
    explicit = data.get("logo_policy")
    if not (isinstance(explicit, Mapping) and explicit.get("show_logo") is True):
        return ""
    policy = logo_policy_for_page(page)
    if not policy["show_logo"]:
        return ""
    scale = "about 8-9% of slide width" if policy["scale"] == "small" else "modest brand signature size"
    placement = ANCHOR_LABELS[normalize_logo_placement(policy.get("placement"))]
    return (
        "Use the exact uploaded logo or co-brand lockup as a quiet brand signature in the "
        f"{placement}; keep this same position across slides, keep it {scale}, "
        "and never let it compete with the slide text or main visual evidence."
    )


def logo_reservation_instruction(page: Any, anchor: str | None = None) -> str:
    data = _as_dict(page)
    explicit = data.get("logo_policy")
    if not (isinstance(explicit, Mapping) and explicit.get("show_logo") is True):
        return ""
    policy = logo_policy_for_page(page)
    if not policy["show_logo"]:
        return ""
    placement_key = normalize_logo_placement(anchor or policy.get("placement"))
    placement = ANCHOR_LABELS[placement_key]
    size = "readable small" if policy["scale"] == "small" else "modest"
    return (
        f"Keep the {placement} visually quiet for a {size} brand signature overlay: no text, "
        "no key subject, and no dense detail in that area. Do not draw any mark placeholder, "
        "box, border, rounded rectangle, dashed frame, label, badge, or decorative container."
    )


def should_use_logo_as_scene_asset(page: Any, logo_ref: Any | None = None) -> bool:
    if logo_ref is None:
        return False
    data = _as_dict(page)
    explicit = data.get("logo_policy")
    if isinstance(explicit, Mapping) and "use_as_scene_asset" in explicit:
        return bool(explicit.get("use_as_scene_asset"))
    return False
