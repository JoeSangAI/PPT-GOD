import json
import logging
import math
import re
from typing import Dict, List, Tuple

import json_repair

from app.services.image_analyzer import _call_vision_model

logger = logging.getLogger(__name__)


def detect_text_regions(image_path: str) -> List[Dict]:
    """
    用 MiniMax VLM 检测图片中的文字区域。
    返回: [{"x": 0.05, "y": 0.10, "width": 0.40, "height": 0.08}, ...]
    坐标为归一化值 (0-1)，左上角为 (0,0)，右下角为 (1,1)。
    失败时返回空列表。
    """
    prompt = """请分析这张 PPT 页面截图，精确识别所有文字内容所在的区域。

要求：
1. 只检测实际文字（标题、正文、数字、标签），不检测图片、图标、装饰元素
2. 为每个文字区域提供归一化坐标，范围 0-1
3. 坐标系：左上角为 (0,0)，右下角为 (1,1)
4. 尽量合并同一行/同一段的文字为一个区域，不要每个字单独一个框
5. 只输出合法 JSON，不要任何额外说明

输出格式：
{
  "text_regions": [
    {"x": 0.05, "y": 0.10, "width": 0.40, "height": 0.08, "text": "标题示例"},
    {"x": 0.05, "y": 0.22, "width": 0.50, "height": 0.15, "text": "正文区域"}
  ]
}"""

    raw = _call_vision_model(image_path, prompt, timeout_seconds=60)
    if not raw:
        logger.warning(f"Text region detection returned empty for {image_path}")
        return []

    try:
        cleaned = re.sub(
            r"^```(?:json)?\s*|```$",
            "",
            raw.strip(),
            flags=re.MULTILINE | re.IGNORECASE,
        ).strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            # 视觉模型偶尔会漏逗号或多一个引号；可修复的 JSON 不应让安全检测整页失效。
            data = json_repair.loads(cleaned)
        regions = data.get("text_regions", [])
        valid = []
        for r in regions:
            if all(k in r for k in ("x", "y", "width", "height")):
                x = float(r["x"])
                y = float(r["y"])
                w = float(r["width"])
                h = float(r["height"])
                # 基本范围校验
                if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1.01 or y + h > 1.01:
                    logger.warning(f"Text region out of bounds, skipping: {r}")
                    continue
                valid.append({"x": max(0.0, x), "y": max(0.0, y), "width": w, "height": h})
        logger.info(f"Detected {len(valid)} text regions in {image_path}")
        return valid
    except Exception as e:
        logger.error(f"Text region detection parse failed for {image_path}: {e}")
        return []


def _iou(box1: Dict, box2: Dict) -> float:
    """计算两个归一化框的 IoU。"""
    x1 = max(box1["x"], box2["x"])
    y1 = max(box1["y"], box2["y"])
    x2 = min(box1["x"] + box1["width"], box2["x"] + box2["width"])
    y2 = min(box1["y"] + box1["height"], box2["y"] + box2["height"])

    if x1 >= x2 or y1 >= y2:
        return 0.0

    inter = (x2 - x1) * (y2 - y1)
    area1 = box1["width"] * box1["height"]
    area2 = box2["width"] * box2["height"]
    union = area1 + area2 - inter
    return inter / union if union > 0 else 0.0


def _intersection_area(box1: Dict, box2: Dict) -> float:
    x1 = max(box1["x"], box2["x"])
    y1 = max(box1["y"], box2["y"])
    x2 = min(box1["x"] + box1["width"], box2["x"] + box2["width"])
    y2 = min(box1["y"] + box1["height"], box2["y"] + box2["height"])
    if x1 >= x2 or y1 >= y2:
        return 0.0
    return (x2 - x1) * (y2 - y1)


def _expanded_box(box: Dict, clearance: float) -> Dict:
    """给文字/已放置元素留出视觉呼吸区，避免只是几何上擦边。"""
    x = max(0.0, float(box["x"]) - clearance)
    y = max(0.0, float(box["y"]) - clearance)
    right = min(1.0, float(box["x"]) + float(box["width"]) + clearance)
    bottom = min(1.0, float(box["y"]) + float(box["height"]) + clearance)
    return {"x": x, "y": y, "width": right - x, "height": bottom - y}


def _overlap_score(box: Dict, avoid_regions: List[Dict], clearance: float = 0.012) -> float:
    """
    返回最严重的覆盖比例。

    不能只看 IoU：Logo 很小，即使把一行文字盖住，IoU 也可能很低。这里同时看
    交集占后贴元素和被保护区域的比例，任一侧被明显覆盖都算冲突。
    """
    box_area = max(float(box["width"]) * float(box["height"]), 1e-9)
    worst = 0.0
    for region in avoid_regions:
        protected = _expanded_box(region, clearance)
        inter = _intersection_area(box, protected)
        if inter <= 0:
            continue
        protected_area = max(protected["width"] * protected["height"], 1e-9)
        worst = max(worst, inter / box_area, inter / protected_area)
    return worst


def _overlaps(
    box: Dict,
    text_regions: List[Dict],
    threshold: float = 0.002,
    *,
    clearance: float = 0.012,
) -> bool:
    """检查后贴元素是否覆盖文字/其他受保护区域。"""
    return _overlap_score(box, text_regions, clearance=clearance) > threshold


def _clamp_position(value: float, size: float, margin: float) -> float:
    return max(margin, min(1.0 - margin - size, value))


def _candidate_positions(
    box: Dict,
    scale: float,
    margin: float,
    candidate_layout: str,
) -> List[Tuple[float, float]]:
    width = box["width"] * scale
    height = box["height"] * scale
    original_x = _clamp_position(box["x"], width, margin)
    original_y = _clamp_position(box["y"], height, margin)

    # 先尝试靠近原始设计意图的位置，再尝试四角。
    positions = [
        (original_x, original_y),
        (original_x, _clamp_position(box["y"] - 0.08, height, margin)),
        (original_x, _clamp_position(box["y"] + 0.08, height, margin)),
        (_clamp_position(box["x"] - 0.06, width, margin), original_y),
        (_clamp_position(box["x"] + 0.06, width, margin), original_y),
        (margin, margin),
        (1.0 - margin - width, margin),
        (margin, 1.0 - margin - height),
        (1.0 - margin - width, 1.0 - margin - height),
    ]
    if candidate_layout == "corners":
        return positions

    positions.extend([
        ((1.0 - width) / 2, margin),
        ((1.0 - width) / 2, 1.0 - margin - height),
        (margin, (1.0 - height) / 2),
        (1.0 - margin - width, (1.0 - height) / 2),
        ((1.0 - width) / 2, (1.0 - height) / 2),
    ])
    # 在连续布局里，稀疏网格可以找到不属于固定四角的真实空白区。
    for x_ratio in (0.04, 0.16, 0.28, 0.40, 0.52, 0.64, 0.76, 0.88):
        for y_ratio in (0.04, 0.16, 0.28, 0.40, 0.52, 0.64, 0.76, 0.88):
            positions.append((
                _clamp_position(x_ratio, width, margin),
                _clamp_position(y_ratio, height, margin),
            ))

    unique = []
    seen = set()
    for x, y in positions:
        key = (round(x, 5), round(y, 5))
        if key not in seen:
            seen.add(key)
            unique.append((x, y))
    return unique


def compute_safe_overlay_box(
    preset_left: float,
    preset_top: float,
    preset_width: float,
    preset_height: float,
    text_regions: List[Dict],
    slide_width: float,
    slide_height: float,
    *,
    min_scale: float = 0.45,
    clearance: float = 0.012,
    candidate_layout: str = "free",
) -> Tuple[float, float, float, float]:
    """
    根据文字区域，计算安全的 overlay 放置位置。
    返回: (left, top, width, height) 像素坐标
    """
    if not text_regions:
        return preset_left, preset_top, preset_width, preset_height

    preset_norm = {
        "x": preset_left / slide_width,
        "y": preset_top / slide_height,
        "width": preset_width / slide_width,
        "height": preset_height / slide_height,
    }

    # 如果预设位置安全，直接返回，最大限度保持用户选择的版式。
    if not _overlaps(preset_norm, text_regions, clearance=clearance):
        return preset_left, preset_top, preset_width, preset_height

    logger.info(
        f"Preset overlaps text, searching safe position. "
        f"Preset: ({preset_norm['x']:.2f}, {preset_norm['y']:.2f}, "
        f"{preset_norm['width']:.2f}, {preset_norm['height']:.2f})"
    )

    min_scale = max(0.20, min(1.0, float(min_scale)))
    scale_steps = [1.0, 0.92, 0.84, 0.76, 0.68, 0.60, 0.52, 0.45, 0.38, 0.30, 0.24, 0.20]
    scales = [scale for scale in scale_steps if scale + 1e-9 >= min_scale]
    if not scales or scales[-1] > min_scale + 1e-9:
        scales.append(min_scale)

    candidates = []
    margin = 0.018
    for scale in scales:
        width = preset_norm["width"] * scale
        height = preset_norm["height"] * scale
        for x, y in _candidate_positions(preset_norm, scale, margin, candidate_layout):
            candidate = {"x": x, "y": y, "width": width, "height": height}
            movement = math.hypot(x - preset_norm["x"], y - preset_norm["y"])
            # 保持原尺寸优先；同尺寸下选择离预设最近的位置。
            design_cost = (1.0 - scale) * 0.70 + movement
            candidates.append((design_cost, -scale, candidate))

    for _, __, candidate in sorted(candidates, key=lambda item: (item[0], item[1])):
        if not _overlaps(candidate, text_regions, clearance=clearance):
            logger.info(
                "Resolved safe overlay box: (%.2f, %.2f, %.2f, %.2f)",
                candidate["x"], candidate["y"], candidate["width"], candidate["height"],
            )
            return (
                candidate["x"] * slide_width,
                candidate["y"] * slide_height,
                candidate["width"] * slide_width,
                candidate["height"] * slide_height,
            )

    # 失败时绝不能退回已知会覆盖文字的原坐标。让组装明确失败，促使上游重新排版。
    raise ValueError(
        "No collision-free placement found for overlay; refusing to cover slide text. "
        f"text_regions_count={len(text_regions)}, min_scale={min_scale:.2f}"
    )
