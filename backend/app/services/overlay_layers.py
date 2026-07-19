import copy
import os
import re
import uuid
from typing import Any, Mapping

from PIL import Image


OVERLAY_PRESETS: dict[str, dict[str, float]] = {
    "top-right-small": {"x": 0.72, "y": 0.08, "w": 0.20, "h": 0.18},
    "bottom-right-small": {"x": 0.72, "y": 0.72, "w": 0.20, "h": 0.18},
    "left-card": {"x": 0.065, "y": 0.18, "w": 0.36, "h": 0.58},
    "right-card": {"x": 0.595, "y": 0.18, "w": 0.34, "h": 0.58},
    "center-card": {"x": 0.28, "y": 0.20, "w": 0.44, "h": 0.56},
    "bottom-band": {"x": 0.12, "y": 0.68, "w": 0.76, "h": 0.22},
    "gallery-2-left": {"x": 0.22, "y": 0.24, "w": 0.25, "h": 0.58},
    "gallery-2-right": {"x": 0.53, "y": 0.24, "w": 0.25, "h": 0.58},
    "gallery-3-left": {"x": 0.11, "y": 0.24, "w": 0.25, "h": 0.58},
    "gallery-3-center": {"x": 0.375, "y": 0.24, "w": 0.25, "h": 0.58},
    "gallery-3-right": {"x": 0.64, "y": 0.24, "w": 0.25, "h": 0.58},
    "gallery-4-left": {"x": 0.075, "y": 0.26, "w": 0.20, "h": 0.52},
    "gallery-4-mid-left": {"x": 0.285, "y": 0.26, "w": 0.20, "h": 0.52},
    "gallery-4-mid-right": {"x": 0.495, "y": 0.26, "w": 0.20, "h": 0.52},
    "gallery-4-right": {"x": 0.705, "y": 0.26, "w": 0.20, "h": 0.52},
    "primary-left": {"x": 0.09, "y": 0.24, "w": 0.46, "h": 0.58},
    "secondary-right": {"x": 0.63, "y": 0.24, "w": 0.25, "h": 0.58},
    "secondary-right-top": {"x": 0.63, "y": 0.24, "w": 0.25, "h": 0.27},
    "secondary-right-bottom": {"x": 0.63, "y": 0.55, "w": 0.25, "h": 0.27},
}

OVERLAY_PRESET_LABELS = {
    "top-right-small": "top-right small reserved media slot",
    "bottom-right-small": "bottom-right small reserved media slot",
    "left-card": "left-side card media slot",
    "right-card": "right-side card media slot",
    "center-card": "center card media slot",
    "bottom-band": "bottom horizontal band media slot",
    "gallery-2-left": "left equal gallery media slot",
    "gallery-2-right": "right equal gallery media slot",
    "gallery-3-left": "left equal gallery media slot",
    "gallery-3-center": "center equal gallery media slot",
    "gallery-3-right": "right equal gallery media slot",
    "gallery-4-left": "first equal gallery media slot",
    "gallery-4-mid-left": "second equal gallery media slot",
    "gallery-4-mid-right": "third equal gallery media slot",
    "gallery-4-right": "fourth equal gallery media slot",
    "primary-left": "large primary media slot on the left",
    "secondary-right": "secondary media slot on the right",
    "secondary-right-top": "upper secondary media slot on the right",
    "secondary-right-bottom": "lower secondary media slot on the right",
}

OVERLAY_MODES = {"exact_card", "exact_cutout"}
OVERLAY_VALIGNS = {"center", "bottom"}
OVERLAY_LAYOUT_ROLES = {"peer", "primary", "secondary"}
OVERLAY_LAYOUT_GROUPS = {"auto", "gallery", "comparison", "sequence", "primary_secondary"}
DEFAULT_OVERLAY_PRESET = "right-card"
DEFAULT_OVERLAY_MODE = "exact_card"
DEFAULT_OVERLAY_VALIGN = "bottom"
DEFAULT_OVERLAY_LAYOUT_ROLE = "peer"
DEFAULT_OVERLAY_LAYOUT_GROUP = "auto"
MULTI_OVERLAY_PRESET_SEQUENCE = (
    "left-card",
    "center-card",
    "right-card",
    "bottom-band",
    "top-right-small",
    "bottom-right-small",
)
AUTO_GALLERY_PRESETS_BY_COUNT = {
    2: ("gallery-2-left", "gallery-2-right"),
    3: ("gallery-3-left", "gallery-3-center", "gallery-3-right"),
    4: ("gallery-4-left", "gallery-4-mid-left", "gallery-4-mid-right", "gallery-4-right"),
}
PRIMARY_SECONDARY_PRESETS_BY_COUNT = {
    2: ("primary-left", "secondary-right"),
    3: ("primary-left", "secondary-right-top", "secondary-right-bottom"),
}
HEADER_SAFE_OVERLAY_PRESETS = {
    "left-card",
    "right-card",
    "center-card",
    *AUTO_GALLERY_PRESETS_BY_COUNT[2],
    *AUTO_GALLERY_PRESETS_BY_COUNT[3],
    *AUTO_GALLERY_PRESETS_BY_COUNT[4],
    *PRIMARY_SECONDARY_PRESETS_BY_COUNT[2],
    *PRIMARY_SECONDARY_PRESETS_BY_COUNT[3],
}
HEADER_SAFE_OVERLAY_TOP_RATIO = 0.24
HEADER_SAFE_OVERLAY_BOTTOM_RATIO = 0.90
PRIMARY_ROLE_MARKERS = (
    "主图",
    "主视觉",
    "主画面",
    "主素材",
    "核心",
    "重点",
    "放大",
    "大图",
    "主要",
    "primary",
    "main",
    "hero",
)
SECONDARY_ROLE_MARKERS = (
    "辅图",
    "辅助",
    "补充",
    "次要",
    "小图",
    "secondary",
    "supporting",
    "support",
    "detail",
)
COMPARISON_BEFORE_MARKERS = (
    "before",
    "优化前",
    "改前",
    "调整前",
    "修改前",
    "升级前",
    "前版",
    "旧版",
    "原版",
    "原始",
    "之前",
)
COMPARISON_AFTER_MARKERS = (
    "after",
    "优化后",
    "改后",
    "调整后",
    "修改后",
    "升级后",
    "后版",
    "新版",
    "现在",
    "之后",
)
SEQUENCE_MARKERS = ("步骤", "step", "流程", "阶段", "第")
CHINESE_NUMERAL_MAP = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
}


def _next_unused_overlay_preset(used_presets: set[str]) -> str:
    for preset in MULTI_OVERLAY_PRESET_SEQUENCE:
        if preset not in used_presets:
            return preset
    return DEFAULT_OVERLAY_PRESET


def _auto_gallery_preset(index: int, count: int) -> str | None:
    presets = AUTO_GALLERY_PRESETS_BY_COUNT.get(count)
    if not presets or index >= len(presets):
        return None
    return presets[index]


def _normalize_layout_role(raw_role: Any, usage_note: Any = "") -> str:
    value = str(raw_role or "").strip().lower()
    if value in {"primary", "main", "hero"}:
        return "primary"
    if value in {"secondary", "support", "supporting", "detail"}:
        return "secondary"
    if value == "peer":
        return "peer"
    note = str(usage_note or "").strip().lower()
    if any(marker.lower() in note for marker in PRIMARY_ROLE_MARKERS):
        return "primary"
    if any(marker.lower() in note for marker in SECONDARY_ROLE_MARKERS):
        return "secondary"
    return DEFAULT_OVERLAY_LAYOUT_ROLE


def _normalize_layout_group(raw_group: Any) -> str:
    value = str(raw_group or "").strip().lower()
    if value in {"peer", "peers", "equal", "same", "gallery"}:
        return "gallery"
    if value in {"compare", "comparison", "before_after", "before-after", "vs"}:
        return "comparison"
    if value in {"sequence", "flow", "steps", "step"}:
        return "sequence"
    if value in {"primary_secondary", "primary-secondary", "main_secondary", "main-secondary"}:
        return "primary_secondary"
    return DEFAULT_OVERLAY_LAYOUT_GROUP


def _asset_value(asset: Any, key: str) -> Any:
    if isinstance(asset, Mapping):
        return asset.get(key)
    return getattr(asset, key, None)


def build_overlay_asset_context_map(assets: list[Any] | tuple[Any, ...] | None) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for asset in assets or []:
        asset_id = str(_asset_value(asset, "id") or "").strip()
        if not asset_id:
            continue
        analysis = _asset_value(asset, "asset_analysis")
        result[asset_id] = {
            "asset_name": _asset_value(asset, "asset_name"),
            "asset_kind": _asset_value(asset, "asset_kind"),
            "usage_note": _asset_value(asset, "usage_note"),
            "asset_analysis": analysis if isinstance(analysis, Mapping) else {},
            "file_path": _asset_value(asset, "file_path"),
            "role": _asset_value(asset, "role"),
            "process_mode": _asset_value(asset, "process_mode"),
        }
    return result


def _context_for_asset(asset_context_by_id: Mapping[str, Mapping[str, Any]] | None, asset_id: str) -> Mapping[str, Any]:
    if not isinstance(asset_context_by_id, Mapping):
        return {}
    context = asset_context_by_id.get(str(asset_id))
    return context if isinstance(context, Mapping) else {}


def _overlay_context_text(raw: Mapping[str, Any], context: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in ("asset_name", "asset_kind", "usage_note", "layout_role", "layout_group"):
        value = raw.get(key)
        if value:
            parts.append(str(value))
    for key in ("asset_name", "asset_kind", "usage_note", "role", "process_mode"):
        value = context.get(key)
        if value:
            parts.append(str(value))
    analysis = context.get("asset_analysis")
    if isinstance(analysis, Mapping):
        for key in (
            "subject",
            "description",
            "recommended_usage",
            "detected_kind",
            "selection_reason",
            "nearby_text",
            "source_document",
        ):
            value = analysis.get(key)
            if value:
                parts.append(str(value))
        for key in ("suggested_keywords", "identity_elements", "distinctive_features"):
            value = analysis.get(key)
            if isinstance(value, list):
                parts.extend(str(item) for item in value if item)
    return " ".join(parts).strip()


def _comparison_side(text: str) -> str | None:
    lowered = str(text or "").lower()
    has_before = any(marker.lower() in lowered for marker in COMPARISON_BEFORE_MARKERS)
    has_after = any(marker.lower() in lowered for marker in COMPARISON_AFTER_MARKERS)
    if has_before and not has_after:
        return "before"
    if has_after and not has_before:
        return "after"
    return None


def _sequence_index(text: str) -> int | None:
    raw = str(text or "")
    lowered = raw.lower()
    match = re.search(r"(?:step|步骤|阶段|第)\s*([1-4])", lowered)
    if match:
        return int(match.group(1))
    match = re.search(r"(^|[^\d])([1-4])\s*(?:[./_-]|步|阶段)", lowered)
    if match:
        return int(match.group(2))
    for marker, value in CHINESE_NUMERAL_MAP.items():
        if f"步骤{marker}" in raw or f"第{marker}" in raw or f"{marker}步" in raw:
            return value
    return None


def _infer_layout_group(infos: list[dict[str, Any]], enabled_count: int, has_primary_layout: bool) -> str:
    explicit_groups = [info["layout_group"] for info in infos if info.get("enabled") and info.get("layout_group") != DEFAULT_OVERLAY_LAYOUT_GROUP]
    if explicit_groups:
        return explicit_groups[0]
    if has_primary_layout:
        return "primary_secondary"
    if enabled_count == 2:
        sides = {info.get("comparison_side") for info in infos if info.get("enabled")}
        if {"before", "after"}.issubset(sides):
            return "comparison"
    if 2 <= enabled_count <= 4:
        sequence_indices = [info.get("sequence_index") for info in infos if info.get("enabled") and info.get("sequence_index")]
        if len(set(sequence_indices)) >= 2:
            return "sequence"
    if enabled_count > 1:
        return "gallery"
    return DEFAULT_OVERLAY_LAYOUT_GROUP


def _group_order_by_asset(infos: list[dict[str, Any]], group: str) -> dict[str, int]:
    enabled_infos = [info for info in infos if info.get("enabled")]
    if group == "comparison":
        order = {"before": 0, "after": 1}
        return {
            str(info["asset_id"]): order.get(str(info.get("comparison_side")), info["enabled_index"])
            for info in enabled_infos
        }
    if group == "sequence":
        sorted_infos = sorted(
            enabled_infos,
            key=lambda info: (
                info.get("sequence_index") if info.get("sequence_index") is not None else 999 + info["enabled_index"],
                info["enabled_index"],
            ),
        )
        return {str(info["asset_id"]): index for index, info in enumerate(sorted_infos)}
    return {str(info["asset_id"]): info["enabled_index"] for info in enabled_infos}


def _auto_primary_secondary_preset(
    role: str,
    *,
    count: int,
    primary_used: bool,
    secondary_index: int,
) -> str | None:
    presets = PRIMARY_SECONDARY_PRESETS_BY_COUNT.get(count)
    if not presets:
        return None
    if role == "primary" and not primary_used:
        return presets[0]
    if count == 2:
        return presets[1]
    index = min(secondary_index + 1, len(presets) - 1)
    return presets[index]


def enabled_overlay_layers(
    visual_json: Mapping[str, Any] | None,
    *,
    valid_asset_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    visual = visual_json if isinstance(visual_json, Mapping) else {}
    layers = visual.get("overlay_layers") or []
    if not isinstance(layers, list):
        return []
    normalized = normalize_overlay_layers(
        layers,
        valid_asset_ids=valid_asset_ids,
        strict_assets=valid_asset_ids is not None,
    )
    return [layer for layer in normalized if layer.get("enabled", True)]


def exact_overlay_asset_ids(visual_json: Mapping[str, Any] | None) -> set[str]:
    return {str(layer.get("asset_id")) for layer in enabled_overlay_layers(visual_json) if layer.get("asset_id")}


def normalize_overlay_layers(
    layers: list[Any] | None,
    *,
    valid_asset_ids: set[str] | None,
    strict_assets: bool = True,
    asset_context_by_id: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(layers, list):
        return []
    enabled_candidate_count = 0
    inferred_enabled_roles: list[str] = []
    candidate_infos: list[dict[str, Any]] = []
    for index, raw in enumerate(layers):
        if not isinstance(raw, Mapping):
            continue
        asset_id = str(raw.get("asset_id") or "").strip()
        if not asset_id:
            continue
        if strict_assets and valid_asset_ids is not None and asset_id not in valid_asset_ids:
            continue
        context = _context_for_asset(asset_context_by_id, asset_id)
        context_text = _overlay_context_text(raw, context)
        layout_role = _normalize_layout_role(raw.get("layout_role") or context.get("layout_role"), context_text)
        layout_group = _normalize_layout_group(raw.get("layout_group") or context.get("layout_group"))
        enabled = bool(raw.get("enabled", True))
        if bool(raw.get("enabled", True)):
            enabled_candidate_count += 1
            inferred_enabled_roles.append(layout_role)
        candidate_infos.append({
            "index": index,
            "asset_id": asset_id,
            "enabled": enabled,
            "layout_role": layout_role,
            "layout_group": layout_group,
            "comparison_side": _comparison_side(context_text),
            "sequence_index": _sequence_index(context_text),
            "enabled_index": enabled_candidate_count - 1 if enabled else -1,
        })
    has_multiple_enabled_layers = enabled_candidate_count > 1
    has_primary_layout = has_multiple_enabled_layers and "primary" in inferred_enabled_roles
    inferred_group = _infer_layout_group(candidate_infos, enabled_candidate_count, has_primary_layout)
    group_order = _group_order_by_asset(candidate_infos, inferred_group)
    info_by_index = {info["index"]: info for info in candidate_infos}
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    used_enabled_presets: set[str] = set()
    enabled_index = 0
    primary_preset_used = False
    secondary_preset_index = 0
    for index, raw in enumerate(layers):
        if not isinstance(raw, Mapping):
            continue
        asset_id = str(raw.get("asset_id") or "").strip()
        if not asset_id:
            continue
        if strict_assets and valid_asset_ids is not None and asset_id not in valid_asset_ids:
            continue
        layer_id = str(raw.get("id") or "").strip() or f"ov_{uuid.uuid4().hex[:12]}"
        if layer_id in seen_ids:
            layer_id = f"ov_{uuid.uuid4().hex[:12]}"
        seen_ids.add(layer_id)
        enabled = bool(raw.get("enabled", True))
        info = info_by_index.get(index) or {}
        layout_role = str(info.get("layout_role") or DEFAULT_OVERLAY_LAYOUT_ROLE)
        if has_primary_layout and enabled and layout_role == "peer":
            layout_role = "secondary"
        if has_primary_layout and enabled and layout_role == "primary" and primary_preset_used:
            layout_role = "secondary"
        layout_group = str(info.get("layout_group") or DEFAULT_OVERLAY_LAYOUT_GROUP)
        if enabled and inferred_group != DEFAULT_OVERLAY_LAYOUT_GROUP and layout_group == DEFAULT_OVERLAY_LAYOUT_GROUP:
            layout_group = inferred_group
        raw_preset = str(raw.get("preset") or "").strip()
        preset = raw_preset if raw_preset in OVERLAY_PRESETS else ""
        if enabled:
            if has_multiple_enabled_layers and not preset:
                if inferred_group in {"gallery", "comparison", "sequence"}:
                    preset = _auto_gallery_preset(
                        group_order.get(asset_id, enabled_index),
                        enabled_candidate_count,
                    )
                else:
                    preset = (
                        _auto_primary_secondary_preset(
                            layout_role,
                            count=enabled_candidate_count,
                            primary_used=primary_preset_used,
                            secondary_index=secondary_preset_index,
                        )
                        if has_primary_layout
                        else None
                    )
                preset = preset or _auto_gallery_preset(enabled_index, enabled_candidate_count) or _next_unused_overlay_preset(used_enabled_presets)
            elif not preset:
                preset = DEFAULT_OVERLAY_PRESET
            elif preset in used_enabled_presets:
                preset = _next_unused_overlay_preset(used_enabled_presets)
            used_enabled_presets.add(preset)
            if has_primary_layout:
                if layout_role == "primary" and not primary_preset_used:
                    primary_preset_used = True
                else:
                    secondary_preset_index += 1
            enabled_index += 1
        elif not preset:
            preset = DEFAULT_OVERLAY_PRESET
        mode = str(raw.get("mode") or DEFAULT_OVERLAY_MODE).strip()
        if mode not in OVERLAY_MODES:
            mode = DEFAULT_OVERLAY_MODE
        valign = str(raw.get("valign") or DEFAULT_OVERLAY_VALIGN).strip()
        if valign not in OVERLAY_VALIGNS:
            valign = DEFAULT_OVERLAY_VALIGN
        normalized_layer = {
            "id": layer_id,
            "asset_id": asset_id,
            "enabled": enabled,
            "preset": preset,
            "fit": "contain",
            "mode": mode,
            "valign": valign,
            "layout_role": layout_role,
            "layout_group": layout_group,
            "usage_note": str(raw.get("usage_note") or "").strip(),
            "z_index": index,
        }
        resolved_box = raw.get("resolved_overlay_box")
        if isinstance(resolved_box, Mapping):
            try:
                left = float(resolved_box.get("left"))
                top = float(resolved_box.get("top"))
                box_width = float(resolved_box.get("width"))
                box_height = float(resolved_box.get("height"))
            except (TypeError, ValueError):
                resolved_box = None
            if (
                resolved_box is not None
                and 0 <= left < 1
                and 0 <= top < 1
                and box_width > 0
                and box_height > 0
                and left + box_width <= 1.001
                and top + box_height <= 1.001
                and str(resolved_box.get("source_preset") or preset) == preset
                and str(resolved_box.get("source_mode") or mode) == mode
            ):
                normalized_layer["resolved_overlay_box"] = {
                    "left": left,
                    "top": top,
                    "width": box_width,
                    "height": box_height,
                    "source_preset": preset,
                    "source_mode": mode,
                    "strategy": str(resolved_box.get("strategy") or "collision-safe"),
                }
        normalized.append(normalized_layer)
    return normalized


def apply_llm_overlay_layout(
    overlay_layers: list[dict[str, Any]],
    llm_overlay_layout: list[Any] | None,
) -> list[dict[str, Any]]:
    """应用 LLM 建议的 overlay 布局到 overlay_layers，自动避免 preset 冲突。"""
    if not llm_overlay_layout or not overlay_layers:
        return overlay_layers

    llm_updates: dict[str, dict[str, str]] = {}
    for item in llm_overlay_layout:
        if isinstance(item, dict):
            asset_id = str(item.get("asset_id") or "").strip()
            preset = str(item.get("preset") or "").strip()
            if not asset_id:
                continue
            update: dict[str, str] = {}
            if preset and preset in OVERLAY_PRESETS:
                update["preset"] = preset
            layout_role = _normalize_layout_role(item.get("layout_role") or item.get("role"), item.get("reason"))
            if layout_role != DEFAULT_OVERLAY_LAYOUT_ROLE:
                update["layout_role"] = layout_role
            layout_group = str(item.get("layout_group") or "").strip()
            if layout_group:
                update["layout_group"] = layout_group
            if update:
                llm_updates[asset_id] = update

    if not llm_updates:
        return overlay_layers

    updated: list[dict[str, Any]] = []
    any_role_without_preset = any("layout_role" in item and "preset" not in item for item in llm_updates.values())
    for layer in overlay_layers:
        asset_id = str(layer.get("asset_id") or "")
        updated_layer = copy.deepcopy(layer)
        update = llm_updates.get(asset_id) or {}
        if any_role_without_preset:
            updated_layer.pop("preset", None)
            updated_layer.pop("layout_group", None)
        for key, value in update.items():
            updated_layer[key] = value
        updated.append(updated_layer)

    return normalize_overlay_layers(updated, valid_asset_ids=None, strict_assets=False)


def merge_overlay_layers_into_visual_json(visual_json: dict | None, existing_visual_json: dict | None) -> dict:
    visual = copy.deepcopy(visual_json) if isinstance(visual_json, dict) else {}
    existing = existing_visual_json if isinstance(existing_visual_json, dict) else {}
    layers = existing.get("overlay_layers")
    if isinstance(layers, list):
        visual["overlay_layers"] = normalize_overlay_layers(layers, valid_asset_ids=None, strict_assets=False)
    return visual


def remove_asset_from_overlay_layers(visual_json: dict | None, asset_id: str) -> dict:
    visual = copy.deepcopy(visual_json) if isinstance(visual_json, dict) else {}
    layers = visual.get("overlay_layers")
    if isinstance(layers, list):
        visual["overlay_layers"] = [
            layer for layer in normalize_overlay_layers(layers, valid_asset_ids=None, strict_assets=False)
            if str(layer.get("asset_id")) != str(asset_id)
        ]
    return visual


def overlay_reservation_instruction(
    visual_json: Mapping[str, Any] | None,
    *,
    valid_asset_ids: set[str] | None = None,
) -> str:
    layers = enabled_overlay_layers(visual_json, valid_asset_ids=valid_asset_ids)
    if not layers:
        return ""
    area_descriptions = []
    for i, layer in enumerate(layers[:4], 1):
        preset = str(layer.get("preset") or DEFAULT_OVERLAY_PRESET)
        label = OVERLAY_PRESET_LABELS.get(preset, "reserved media slot")
        # 基于 preset 给出更具体的区域描述
        position_hints = {
            "top-right-small": "upper-right corner",
            "bottom-right-small": "lower-right corner",
            "left-card": "left side (approximately 36% width, center vertically)",
            "right-card": "right side (approximately 34% width, center vertically)",
            "center-card": "center (approximately 44% width, center vertically)",
            "bottom-band": "bottom edge (approximately 76% width, lower portion)",
            "gallery-2-left": "left equal-size gallery slot in the lower-middle area",
            "gallery-2-right": "right equal-size gallery slot in the lower-middle area",
            "gallery-3-left": "left equal-size gallery slot in the lower-middle area",
            "gallery-3-center": "center equal-size gallery slot in the lower-middle area",
            "gallery-3-right": "right equal-size gallery slot in the lower-middle area",
            "gallery-4-left": "first equal-size gallery slot in the lower-middle area",
            "gallery-4-mid-left": "second equal-size gallery slot in the lower-middle area",
            "gallery-4-mid-right": "third equal-size gallery slot in the lower-middle area",
            "gallery-4-right": "fourth equal-size gallery slot in the lower-middle area",
            "primary-left": "large primary slot on the left side",
            "secondary-right": "secondary slot on the right side",
            "secondary-right-top": "upper secondary slot on the right side",
            "secondary-right-bottom": "lower secondary slot on the right side",
        }
        position = position_hints.get(preset, label)
        area_descriptions.append(f"{i}. {position}")
    areas_text = "\n".join(area_descriptions)
    return (
        "CRITICAL LAYOUT INSTRUCTION: This page requires "
        f"{len(layers[:4])} clean empty background zone(s) for post-generation overlay assets. "
        "Reserve the following areas completely free of any content:\n"
        f"{areas_text}\n\n"
        "TEXT PLACEMENT RULE: Place ALL visible text, headlines, subheads, and body copy strictly in safe zones that do NOT overlap with reserved areas. "
        "When left and right areas are both reserved, place all text in the upper-center region only, keeping it well above and clear of any reserved zones. "
        "Never place text between two reserved side areas or inside reserved zones. "
        "RESERVED AREA RULES: These reserved areas MUST remain completely free of ALL text, faces, key subjects, "
        "charts, icons, logos, dense texture, decorative marks, placeholder rectangles, tinted panels, card backgrounds, "
        "or ANY visual elements. The reserved space should be visually indistinguishable from the background — "
        "no visual indication that space is held. Generate only soft muted background tones with subtle edge gradients for natural blending. "
        "Do NOT add borders, frames, decorative edges, shadows, boxes, cards, placeholders, or ANY content inside or around these reserved areas. "
        "Treat these as invisible strict no-content zones where absolutely nothing should be rendered."
    )


def overlay_box(prs: Any, preset: str) -> tuple[int, int, int, int]:
    box = OVERLAY_PRESETS.get(preset) or OVERLAY_PRESETS[DEFAULT_OVERLAY_PRESET]
    left = int(prs.slide_width * box["x"])
    top = int(prs.slide_height * box["y"])
    width = int(prs.slide_width * box["w"])
    height = int(prs.slide_height * box["h"])
    return left, top, width, height


def header_safe_overlay_box(
    left: int,
    top: int,
    width: int,
    height: int,
    slide_width: float,
    slide_height: float,
    preset: str,
) -> tuple[int, int, int, int]:
    if preset not in HEADER_SAFE_OVERLAY_PRESETS:
        return left, top, width, height
    min_top = int(slide_height * HEADER_SAFE_OVERLAY_TOP_RATIO)
    if top >= min_top:
        return left, top, width, height
    max_bottom = int(slide_height * HEADER_SAFE_OVERLAY_BOTTOM_RATIO)
    next_top = min_top
    next_height = max(1, min(height, max_bottom - next_top))
    return left, next_top, width, next_height


def contained_picture_box(
    image_path: str,
    left: int,
    top: int,
    width: int,
    height: int,
    *,
    valign: str = DEFAULT_OVERLAY_VALIGN,
) -> tuple[int, int, int, int]:
    if not image_path or not os.path.exists(image_path):
        return left, top, width, height
    try:
        with Image.open(image_path) as img:
            img_w, img_h = img.size
    except Exception:
        return left, top, width, height
    if img_w <= 0 or img_h <= 0 or width <= 0 or height <= 0:
        return left, top, width, height
    scale = min(width / img_w, height / img_h)
    pic_w = int(img_w * scale)
    pic_h = int(img_h * scale)
    pic_left = left + int((width - pic_w) / 2)
    if valign == "bottom":
        pic_top = top + max(0, height - pic_h)
    else:
        pic_top = top + int((height - pic_h) / 2)
    return pic_left, pic_top, pic_w, pic_h
