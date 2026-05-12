import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping

from PIL import Image, ImageFilter, ImageStat

from app.services.logo_policy import LOGO_HEIGHT_RATIOS, LOGO_WIDTH_RATIOS, normalize_logo_placement

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LogoBox:
    left: int
    top: int
    width: int
    height: int
    strategy: str

    def as_ratios(self, canvas_width: int, canvas_height: int) -> dict:
        return {
            "left": self.left / max(canvas_width, 1),
            "top": self.top / max(canvas_height, 1),
            "width": self.width / max(canvas_width, 1),
            "height": self.height / max(canvas_height, 1),
            "strategy": self.strategy,
        }


def _clamp(value: float, low: float, high: float) -> int:
    if high < low:
        return int(low)
    return int(max(low, min(high, value)))


def _logo_size(
    canvas_width: int,
    canvas_height: int,
    logo_width: int,
    logo_height: int,
    slide_type: str,
    scale: str,
) -> tuple[int, int]:
    is_large = scale == "large" or slide_type == "cover"
    size_key = "large" if is_large else "small"
    max_width = int(canvas_width * LOGO_WIDTH_RATIOS[size_key])
    max_height = int(canvas_height * LOGO_HEIGHT_RATIOS[size_key])
    ratio = logo_height / max(logo_width, 1)
    width = max(1, max_width)
    height = max(1, int(width * ratio))
    if height > max_height:
        height = max(1, max_height)
        width = max(1, int(height / max(ratio, 0.01)))
    return width, height


def _static_logo_box(
    canvas_width: int,
    canvas_height: int,
    logo_width: int,
    logo_height: int,
    placement: str,
) -> LogoBox:
    margin_x = int(canvas_width * 0.028)
    margin_y = int(canvas_height * 0.028)
    placement = normalize_logo_placement(placement)
    if placement == "center":
        left = int((canvas_width - logo_width) / 2)
        top = int((canvas_height - logo_height) / 2)
    elif placement == "lower-center":
        left = int((canvas_width - logo_width) / 2)
        top = int(canvas_height * 0.68)
    elif placement == "title-block-center":
        left = int((canvas_width - logo_width) / 2)
        top = int(canvas_height * 0.70)
    else:
        left = margin_x if placement.endswith("left") else canvas_width - margin_x - logo_width
        top = margin_y if placement.startswith("top") else canvas_height - margin_y - logo_height
    return LogoBox(left, top, logo_width, logo_height, f"static:{placement}")


def _region_activity(img: Image.Image, box: tuple[int, int, int, int]) -> float:
    left, top, right, bottom = box
    left = _clamp(left, 0, img.width)
    top = _clamp(top, 0, img.height)
    right = _clamp(right, left + 1, img.width)
    bottom = _clamp(bottom, top + 1, img.height)
    crop = img.crop((left, top, right, bottom)).convert("L")
    if crop.width <= 0 or crop.height <= 0:
        return 1.0

    edges = crop.filter(ImageFilter.FIND_EDGES)
    edge_mean = ImageStat.Stat(edges).mean[0] / 255
    contrast_weights = _contrast_weights(crop)
    total = max(1, crop.width * crop.height)
    contrast_mean = sum(contrast_weights) / total if contrast_weights else 0
    strong_ratio = sum(1 for weight in contrast_weights if weight > 0.16) / total if contrast_weights else 0
    rows = []
    cols = [0] * crop.width
    pixels = [1 if weight > 0.16 else 0 for weight in contrast_weights]
    for y in range(crop.height):
        row = pixels[y * crop.width: (y + 1) * crop.width]
        rows.append(sum(row) / max(crop.width, 1))
        for x, value in enumerate(row):
            cols[x] += value
    max_row_ink = max(rows or [0])
    max_col_ink = max((value / max(crop.height, 1) for value in cols), default=0)
    return (
        edge_mean * 0.28
        + contrast_mean * 0.34
        + strong_ratio * 0.18
        + max_row_ink * 0.14
        + max_col_ink * 0.06
    )


def _contrast_weights(gray: Image.Image) -> list[float]:
    pixels = list(gray.getdata())
    if not pixels:
        return []
    ordered = sorted(pixels)
    p25 = ordered[int(len(ordered) * 0.25)]
    p75 = ordered[int(len(ordered) * 0.75)]
    median = ordered[int(len(ordered) * 0.50)]
    light_canvas = median >= 150 or p75 >= 185
    threshold = max(12, int((p75 - p25) * 0.08))
    if light_canvas:
        return [min(1.0, max(0.0, p75 - v - threshold) / 120) for v in pixels]
    return [min(1.0, max(0.0, v - p25 - threshold) / 120) for v in pixels]


def _dominant_content_box(img: Image.Image) -> tuple[float, float, float, float, float] | None:
    """
    Estimate the densest bright/content block, usually the title block on covers.

    This is intentionally heuristic: we do not need OCR, only a stable anchor
    that keeps the logo aligned with the page's dominant text cluster.
    """
    small_w = 160
    small_h = max(1, int(small_w * img.height / max(img.width, 1)))
    gray = img.resize((small_w, small_h), Image.Resampling.BILINEAR).convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    contrast_weights = _contrast_weights(gray)
    edge_weights = [min(1.0, edge / 80) * 0.28 for edge in edges.getdata()]
    weights = [min(1.0, contrast + edge) for contrast, edge in zip(contrast_weights, edge_weights)]

    x_min = int(small_w * 0.08)
    x_max = int(small_w * 0.94)
    y_min = int(small_h * 0.10)
    y_max = int(small_h * 0.84)
    win_w = max(18, int(small_w * 0.38))
    win_h = max(12, int(small_h * 0.24))
    step_x = max(2, int(small_w * 0.025))
    step_y = max(2, int(small_h * 0.025))

    best: tuple[float, int, int] | None = None
    for y in range(y_min, max(y_min + 1, y_max - win_h + 1), step_y):
        for x in range(x_min, max(x_min + 1, x_max - win_w + 1), step_x):
            mass = 0.0
            for yy in range(y, y + win_h):
                row = yy * small_w
                mass += sum(weights[row + x: row + x + win_w])
            # Prefer slide body over decorative borders.
            cx = (x + win_w / 2) / small_w
            cy = (y + win_h / 2) / small_h
            centrality = 1 - min(0.55, abs(cx - 0.52) * 0.28 + abs(cy - 0.48) * 0.18)
            score = mass * centrality
            if best is None or score > best[0]:
                best = (score, x, y)

    if not best:
        return None
    score, x, y = best
    confidence = score / max(1, win_w * win_h)
    if confidence < 0.035:
        return None
    scale_x = img.width / small_w
    scale_y = img.height / small_h
    return (
        x * scale_x,
        y * scale_y,
        (x + win_w) * scale_x,
        (y + win_h) * scale_y,
        min(1.0, confidence),
    )


def _salient_content_bbox(img: Image.Image) -> tuple[float, float, float, float] | None:
    small_w = 160
    small_h = max(1, int(small_w * img.height / max(img.width, 1)))
    gray = img.resize((small_w, small_h), Image.Resampling.BILINEAR).convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    contrast_weights = _contrast_weights(gray)
    edge_pixels = list(edges.getdata())
    points: list[tuple[int, int]] = []
    for y in range(int(small_h * 0.08), int(small_h * 0.90)):
        for x in range(int(small_w * 0.10), int(small_w * 0.92)):
            idx = y * small_w + x
            if contrast_weights[idx] > 0.16 or edge_pixels[idx] > 28:
                points.append((x, y))
    if len(points) < max(24, int(small_w * small_h * 0.0025)):
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    scale_x = img.width / small_w
    scale_y = img.height / small_h
    pad_x = img.width * 0.025
    pad_y = img.height * 0.025
    return (
        max(0, min(xs) * scale_x - pad_x),
        max(0, min(ys) * scale_y - pad_y),
        min(img.width, (max(xs) + 1) * scale_x + pad_x),
        min(img.height, (max(ys) + 1) * scale_y + pad_y),
    )


def _overlap_ratio(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float] | None,
) -> float:
    if not b:
        return 0.0
    left = max(a[0], b[0])
    top = max(a[1], b[1])
    right = min(a[2], b[2])
    bottom = min(a[3], b[3])
    if right <= left or bottom <= top:
        return 0.0
    overlap_area = (right - left) * (bottom - top)
    a_area = max(1.0, (a[2] - a[0]) * (a[3] - a[1]))
    return overlap_area / a_area


def _clip_candidate(
    canvas_width: int,
    canvas_height: int,
    logo_width: int,
    logo_height: int,
    left: float,
    top: float,
) -> tuple[int, int]:
    margin_x = int(canvas_width * 0.035)
    margin_y = int(canvas_height * 0.04)
    return (
        _clamp(left, margin_x, canvas_width - margin_x - logo_width),
        _clamp(top, margin_y, canvas_height - margin_y - logo_height),
    )


def _smart_title_block_box(
    img: Image.Image,
    logo_width: int,
    logo_height: int,
    slide_type: str = "",
) -> LogoBox:
    content_box = _dominant_content_box(img)
    canvas_width, canvas_height = img.size
    gap = max(18, int(canvas_height * 0.04))

    if content_box:
        left, top, right, bottom, confidence = content_box
        content_cx = (left + right) / 2
        content_width = right - left
    else:
        content_cx = canvas_width / 2
        top = canvas_height * 0.28
        bottom = canvas_height * 0.58
        content_width = canvas_width * 0.4
        confidence = 0.0

    follow_penalty = 0.0 if confidence >= 0.08 else 0.22
    balanced = abs(content_cx - canvas_width / 2) < canvas_width * 0.10 or content_width > canvas_width * 0.52
    center_x = canvas_width / 2 if balanced else content_cx
    protected_content = _salient_content_bbox(img)

    raw_candidates: list[tuple[str, float, float, float]] = [
        ("below-title", center_x - logo_width / 2, bottom + gap * 0.65, 0.00 + follow_penalty),
        ("above-title", center_x - logo_width / 2, top - logo_height - gap * 0.65, 0.08 + follow_penalty),
        ("lower-center", canvas_width / 2 - logo_width / 2, canvas_height * 0.70, 0.20),
        ("top-center", canvas_width / 2 - logo_width / 2, canvas_height * 0.08, 0.34),
        ("bottom-center", canvas_width / 2 - logo_width / 2, canvas_height * 0.86 - logo_height, 0.38),
        ("center", canvas_width / 2 - logo_width / 2, canvas_height / 2 - logo_height / 2, 0.58),
        ("top-right", canvas_width * 0.93 - logo_width, canvas_height * 0.07, 0.76),
        ("top-left", canvas_width * 0.07, canvas_height * 0.07, 0.78),
        ("bottom-right", canvas_width * 0.93 - logo_width, canvas_height * 0.91 - logo_height, 0.88),
        ("bottom-left", canvas_width * 0.07, canvas_height * 0.91 - logo_height, 0.90),
    ]

    best: tuple[float, LogoBox] | None = None
    pad_x = int(logo_width * 0.45)
    pad_y = int(logo_height * 0.55)
    for name, raw_left, raw_top, preference in raw_candidates:
        cand_left, cand_top = _clip_candidate(canvas_width, canvas_height, logo_width, logo_height, raw_left, raw_top)
        box = (cand_left - pad_x, cand_top - pad_y, cand_left + logo_width + pad_x, cand_top + logo_height + pad_y)
        activity = _region_activity(img, box)
        overlap = _overlap_ratio(
            (cand_left, cand_top, cand_left + logo_width, cand_top + logo_height),
            protected_content,
        )
        title_distance = abs((cand_left + logo_width / 2) - center_x) / canvas_width
        vertical_distance = 0 if name in {"below-title", "above-title"} else 0.08
        middle_band_penalty = (
            0.45
            if str(slide_type or "").lower() == "ending" and 0.25 <= (cand_top / max(canvas_height, 1)) <= 0.84
            else 0.0
        )
        overlap_weight = 8.0 if str(slide_type or "").lower() == "ending" else 3.4
        score = activity * 4.6 + overlap * overlap_weight + preference + title_distance * 0.55 + vertical_distance + middle_band_penalty
        logo_box = LogoBox(cand_left, cand_top, logo_width, logo_height, f"smart:{name}")
        if best is None or score < best[0]:
            best = (score, logo_box)

    return best[1] if best else _static_logo_box(canvas_width, canvas_height, logo_width, logo_height, "lower-center")


def resolve_logo_overlay_box(
    slide_image_path: str | None,
    logo_path: str,
    slide_type: str,
    placement: str | None,
    scale: str = "small",
) -> dict | None:
    if not slide_image_path or not logo_path:
        return None
    if not os.path.exists(slide_image_path) or not os.path.exists(logo_path):
        return None

    placement_key = normalize_logo_placement(placement)
    try:
        with Image.open(slide_image_path) as bg_source, Image.open(logo_path) as logo_source:
            bg = bg_source.convert("RGB")
            logo_width, logo_height = _logo_size(
                bg.width,
                bg.height,
                logo_source.width,
                logo_source.height,
                str(slide_type or "content").lower(),
                str(scale or "small").lower(),
            )
            slide_type_key = str(slide_type or "").lower()
            smart_brand_page = (
                placement_key == "title-block-center"
                or (
                    slide_type_key == "ending"
                    and str(scale or "").lower() == "large"
                    and placement_key in {"center", "lower-center"}
                )
            )
            if smart_brand_page:
                box = _smart_title_block_box(bg, logo_width, logo_height, str(slide_type or "content").lower())
            else:
                box = _static_logo_box(bg.width, bg.height, logo_width, logo_height, placement_key)
            return box.as_ratios(bg.width, bg.height)
    except Exception as exc:
        logger.warning("Logo overlay layout failed for %s: %s", slide_image_path, exc)
        return None


def logo_geometry_from_resolved_box(
    resolved_box: Mapping[str, Any] | None,
    slide_width: int,
    slide_height: int,
) -> tuple[int, int, int, int] | None:
    if not isinstance(resolved_box, Mapping):
        return None
    try:
        left = int(float(resolved_box.get("left")) * slide_width)
        top = int(float(resolved_box.get("top")) * slide_height)
        width = int(float(resolved_box.get("width")) * slide_width)
        height = int(float(resolved_box.get("height")) * slide_height)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return left, top, width, height
