from typing import Any, Mapping


DEFAULT_LOGO_ANCHOR = "top-right"
LOGO_WIDTH_RATIOS = {"small": 0.052, "large": 0.18}
LOGO_HEIGHT_RATIOS = {"small": 0.068, "large": 0.18}
LOGO_ANCHORS = {"top-left", "top-right", "bottom-left", "bottom-right"}
LOGO_PLACEMENTS = LOGO_ANCHORS | {"center", "lower-center", "title-block-center"}
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
    explicit = data.get("logo_policy")
    if isinstance(explicit, Mapping) and "show_logo" in explicit:
        return bool(explicit.get("show_logo"))

    page_type = str(data.get("type") or "").lower()
    layout = str(data.get("layout") or "").lower()

    if page_type in {"cover", "ending"}:
        return True
    if page_type in {"hero", "quote"}:
        return False
    if layout in {"hero", "content_hero"}:
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


def logo_policy_for_page(page: Any) -> dict:
    data = _as_dict(page)
    show_logo = should_show_logo(data)
    explicit = data.get("logo_policy")
    page_type = str(data.get("type") or "").lower()
    default_placement = "title-block-center" if page_type == "cover" else "lower-center" if page_type == "ending" else DEFAULT_LOGO_ANCHOR
    anchor = (
        normalize_logo_placement(explicit.get("placement"), default_placement)
        if isinstance(explicit, Mapping) and explicit.get("placement")
        else default_placement
    )
    scale = str(explicit.get("scale")) if isinstance(explicit, Mapping) and explicit.get("scale") else (
        "large" if page_type in {"cover", "ending"} else "small"
    )
    return {
        "show_logo": show_logo,
        "placement": anchor,
        "scale": scale,
        "visibility": "unobtrusive" if show_logo else "omit",
    }


def logo_prompt_instruction(page: Any) -> str:
    policy = logo_policy_for_page(page)
    if not policy["show_logo"]:
        return ""
    scale = "about 5% of slide width" if policy["scale"] == "small" else "modest brand signature size"
    placement = ANCHOR_LABELS[normalize_logo_placement(policy.get("placement"))]
    return (
        "Use the exact uploaded logo or co-brand lockup as a quiet brand signature in the "
        f"{placement}; keep this same position across slides, keep it {scale}, "
        "and never let it compete with the slide text or main visual evidence."
    )


def logo_reservation_instruction(page: Any, anchor: str | None = None) -> str:
    policy = logo_policy_for_page(page)
    if not policy["show_logo"]:
        return ""
    placement_key = normalize_logo_placement(anchor or policy.get("placement"))
    placement = ANCHOR_LABELS[placement_key]
    size = "small" if policy["scale"] == "small" else "modest"
    return (
        f"Keep the {placement} clean for a {size} logo/lockup overlay: no required text, "
        "no key subject, and no dense detail in that area."
    )


def should_use_logo_as_scene_asset(page: Any, logo_ref: Any | None = None) -> bool:
    if logo_ref is None:
        return False
    data = _as_dict(page)
    explicit = data.get("logo_policy")
    if isinstance(explicit, Mapping) and "use_as_scene_asset" in explicit:
        return bool(explicit.get("use_as_scene_asset"))
    return False
