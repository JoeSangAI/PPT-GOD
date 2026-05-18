import json
import logging
import re
from typing import Dict, List, Tuple

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
        data = json.loads(cleaned)
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


def _overlaps(box: Dict, text_regions: List[Dict], threshold: float = 0.05) -> bool:
    """检查 box 是否与任何文字区域的重叠超过 threshold。"""
    return any(_iou(box, tr) > threshold for tr in text_regions)


def compute_safe_overlay_box(
    preset_left: float,
    preset_top: float,
    preset_width: float,
    preset_height: float,
    text_regions: List[Dict],
    slide_width: float,
    slide_height: float,
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

    # 如果预设位置安全，直接返回
    if not _overlaps(preset_norm, text_regions):
        return preset_left, preset_top, preset_width, preset_height

    logger.info(
        f"Preset overlaps text, searching safe position. "
        f"Preset: ({preset_norm['x']:.2f}, {preset_norm['y']:.2f}, "
        f"{preset_norm['width']:.2f}, {preset_norm['height']:.2f})"
    )

    # 尝试垂直移动（保持水平位置和宽度不变）
    for offset in [-0.08, +0.08, -0.15, +0.15, -0.25, +0.25]:
        candidate = {
            "x": preset_norm["x"],
            "y": max(0.0, min(1.0 - preset_norm["height"], preset_norm["y"] + offset)),
            "width": preset_norm["width"],
            "height": preset_norm["height"],
        }
        if not _overlaps(candidate, text_regions):
            return (
                candidate["x"] * slide_width,
                candidate["y"] * slide_height,
                candidate["width"] * slide_width,
                candidate["height"] * slide_height,
            )

    # 尝试水平移动
    for offset in [-0.05, +0.05, -0.10, +0.10]:
        candidate = {
            "x": max(0.0, min(1.0 - preset_norm["width"], preset_norm["x"] + offset)),
            "y": preset_norm["y"],
            "width": preset_norm["width"],
            "height": preset_norm["height"],
        }
        if not _overlaps(candidate, text_regions):
            return (
                candidate["x"] * slide_width,
                candidate["y"] * slide_height,
                candidate["width"] * slide_width,
                candidate["height"] * slide_height,
            )

    # 尝试缩小高度（保持底部对齐）
    for scale in [0.8, 0.6, 0.5]:
        candidate = {
            "x": preset_norm["x"],
            "y": preset_norm["y"] + preset_norm["height"] * (1 - scale),
            "width": preset_norm["width"],
            "height": preset_norm["height"] * scale,
        }
        if not _overlaps(candidate, text_regions):
            return (
                candidate["x"] * slide_width,
                candidate["y"] * slide_height,
                candidate["width"] * slide_width,
                candidate["height"] * slide_height,
            )

    # 所有尝试都失败，返回原始预设
    logger.warning(
        f"Could not find safe position for overlay. Using original preset. "
        f"text_regions_count={len(text_regions)}"
    )
    return preset_left, preset_top, preset_width, preset_height
