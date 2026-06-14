from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image, ImageFilter


def _corner_background(rgb: np.ndarray) -> tuple[int, int, int]:
    h, w, _ = rgb.shape
    patch = max(8, min(h, w) // 30)
    samples = np.concatenate(
        [
            rgb[:patch, :patch].reshape(-1, 3),
            rgb[:patch, w - patch :].reshape(-1, 3),
            rgb[h - patch :, :patch].reshape(-1, 3),
            rgb[h - patch :, w - patch :].reshape(-1, 3),
        ],
        axis=0,
    )
    return tuple(int(x) for x in np.median(samples, axis=0))


def _luminance(color: tuple[int, int, int] | np.ndarray) -> float:
    r, g, b = [float(x) for x in color[:3]]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _rgb_distance(rgb: np.ndarray, color: tuple[int, int, int]) -> np.ndarray:
    delta = rgb.astype(np.int32) - np.array(color, dtype=np.int32)
    return np.sqrt(np.sum(delta * delta, axis=2))


def _connected_components(mask: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    h, w = mask.shape
    seen = np.zeros((h, w), dtype=bool)
    components: list[tuple[int, int, int, int, int]] = []
    for y0, x0 in zip(*np.where(mask & ~seen)):
        if seen[y0, x0]:
            continue
        stack = [(int(x0), int(y0))]
        seen[y0, x0] = True
        min_x = max_x = int(x0)
        min_y = max_y = int(y0)
        area = 0
        while stack:
            x, y = stack.pop()
            area += 1
            min_x = min(min_x, x)
            max_x = max(max_x, x)
            min_y = min(min_y, y)
            max_y = max(max_y, y)
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if nx < 0 or ny < 0 or nx >= w or ny >= h or seen[ny, nx] or not mask[ny, nx]:
                    continue
                seen[ny, nx] = True
                stack.append((nx, ny))
        components.append((min_x, min_y, max_x + 1, max_y + 1, area))
    return components


def _intersection_area(a: dict[str, Any], b: dict[str, Any]) -> float:
    ax1, ay1 = float(a["x"]), float(a["y"])
    ax2, ay2 = ax1 + float(a["width"]), ay1 + float(a["height"])
    bx1, by1 = float(b["x"]), float(b["y"])
    bx2, by2 = bx1 + float(b["width"]), by1 + float(b["height"])
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    return (ix2 - ix1) * (iy2 - iy1)


def detect_image_blocks(image_path: str, text_boxes: list[dict[str, Any]], max_assets: int = 6) -> list[dict[str, float]]:
    img = Image.open(image_path).convert("RGB")
    rgb = np.asarray(img)
    h, w, _ = rgb.shape
    bg = _corner_background(rgb)
    diff = _rgb_distance(rgb, bg)
    threshold = 44 if _luminance(bg) > 160 else 52
    mask = (diff > threshold).astype(np.uint8) * 255
    mask_img = Image.fromarray(mask, mode="L").filter(ImageFilter.MaxFilter(15)).filter(ImageFilter.MinFilter(7))
    closed = np.asarray(mask_img) > 0
    candidates: list[dict[str, float]] = []
    slide_area = w * h
    for x1, y1, x2, y2, _area in _connected_components(closed):
        bw = x2 - x1
        bh = y2 - y1
        box_area = bw * bh
        if box_area < slide_area * 0.018 or box_area > slide_area * 0.68:
            continue
        if bw < 100 or bh < 70:
            continue
        original_density = float(np.mean(diff[y1:y2, x1:x2] > threshold))
        if original_density < 0.22:
            continue
        crop_std = float(rgb[y1:y2, x1:x2].reshape(-1, 3).std(axis=0).mean())
        if box_area < slide_area * 0.035 and crop_std < 34:
            continue
        if original_density < 0.36 and crop_std < 45:
            continue
        aspect = bw / max(1, bh)
        if original_density < 0.38 and (aspect > 5.0 or aspect < 0.22):
            continue
        block = {
            "x": max(0, x1 - 2) / w,
            "y": max(0, y1 - 2) / h,
            "width": (min(w, x2 + 2) - max(0, x1 - 2)) / w,
            "height": (min(h, y2 + 2) - max(0, y1 - 2)) / h,
        }
        block_area_norm = max(0.0001, block["width"] * block["height"])
        text_overlap = sum(_intersection_area(block, text_box) for text_box in text_boxes)
        has_large_text = any(
            (
                _intersection_area(block, text_box)
                / max(0.0001, float(text_box["width"]) * float(text_box["height"]))
                > 0.65
                or _intersection_area(block, text_box) / block_area_norm > 0.55
            )
            and (float(text_box["height"]) > 0.045 or float(text_box["width"]) > 0.24)
            for text_box in text_boxes
        )
        if text_overlap / block_area_norm > 0.30 and has_large_text:
            continue
        candidates.append(block)
    candidates.sort(key=lambda c: c["width"] * c["height"], reverse=True)
    return candidates[: max(1, int(max_assets or 6))]
