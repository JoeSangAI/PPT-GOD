import copy
import os
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
}

OVERLAY_PRESET_LABELS = {
    "top-right-small": "top-right small reserved media slot",
    "bottom-right-small": "bottom-right small reserved media slot",
    "left-card": "left-side card media slot",
    "right-card": "right-side card media slot",
    "center-card": "center card media slot",
    "bottom-band": "bottom horizontal band media slot",
}

OVERLAY_MODES = {"exact_card", "exact_cutout"}
OVERLAY_VALIGNS = {"center", "bottom"}
DEFAULT_OVERLAY_PRESET = "right-card"
DEFAULT_OVERLAY_MODE = "exact_card"
DEFAULT_OVERLAY_VALIGN = "bottom"


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
) -> list[dict[str, Any]]:
    if not isinstance(layers, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
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
        preset = str(raw.get("preset") or DEFAULT_OVERLAY_PRESET).strip()
        if preset not in OVERLAY_PRESETS:
            preset = DEFAULT_OVERLAY_PRESET
        mode = str(raw.get("mode") or DEFAULT_OVERLAY_MODE).strip()
        if mode not in OVERLAY_MODES:
            mode = DEFAULT_OVERLAY_MODE
        valign = str(raw.get("valign") or DEFAULT_OVERLAY_VALIGN).strip()
        if valign not in OVERLAY_VALIGNS:
            valign = DEFAULT_OVERLAY_VALIGN
        normalized.append({
            "id": layer_id,
            "asset_id": asset_id,
            "enabled": bool(raw.get("enabled", True)),
            "preset": preset,
            "fit": "contain",
            "mode": mode,
            "valign": valign,
            "usage_note": str(raw.get("usage_note") or "").strip(),
            "z_index": index,
        })
    return normalized


def apply_llm_overlay_layout(
    overlay_layers: list[dict[str, Any]],
    llm_overlay_layout: list[Any] | None,
) -> list[dict[str, Any]]:
    """应用 LLM 建议的 overlay 布局到 overlay_layers，自动避免 preset 冲突。"""
    if not llm_overlay_layout or not overlay_layers:
        return overlay_layers

    # 创建 asset_id -> preset 的映射
    llm_presets: dict[str, str] = {}
    for item in llm_overlay_layout:
        if isinstance(item, dict):
            asset_id = str(item.get("asset_id") or "").strip()
            preset = str(item.get("preset") or "").strip()
            if asset_id and preset and preset in OVERLAY_PRESETS:
                llm_presets[asset_id] = preset

    # 应用 LLM 建议的 preset，自动处理冲突
    updated: list[dict[str, Any]] = []
    used_presets: set[str] = set()

    for layer in overlay_layers:
        asset_id = str(layer.get("asset_id") or "")
        preset = llm_presets.get(asset_id) or layer.get("preset", DEFAULT_OVERLAY_PRESET)

        # 验证 preset 合法性
        if preset not in OVERLAY_PRESETS:
            preset = DEFAULT_OVERLAY_PRESET

        # 处理冲突：如果 preset 已被使用，找下一个可用的
        if preset in used_presets:
            for p in OVERLAY_PRESETS:
                if p not in used_presets:
                    preset = p
                    break

        used_presets.add(preset)

        updated_layer = copy.deepcopy(layer)
        updated_layer["preset"] = preset
        updated.append(updated_layer)

    return updated


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
