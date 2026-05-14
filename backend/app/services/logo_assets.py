import hashlib
import json
import os

from PIL import Image, ImageChops, ImageFilter, ImageStat


def _overlay_cache_path(source_path: str) -> str:
    directory = os.path.dirname(source_path)
    stem, _ = os.path.splitext(os.path.basename(source_path))
    return os.path.join(directory, f"logo_overlay_clean_{stem}.png")


def _symbol_cache_path(source_path: str) -> str:
    directory = os.path.dirname(source_path)
    stem, _ = os.path.splitext(os.path.basename(source_path))
    return os.path.join(directory, f"logo_symbol_clean_{stem}.png")


def _symbol_none_cache_path(source_path: str) -> str:
    return _symbol_cache_path(source_path) + ".none"


def _valid_logo_paths(source_paths: list[str] | tuple[str, ...] | None) -> list[str]:
    valid: list[str] = []
    for path in source_paths or []:
        if not path or not os.path.exists(path):
            continue
        normalized = os.path.abspath(path)
        if normalized not in valid:
            valid.append(normalized)
    return valid


def _lockup_cache_path(source_paths: list[str]) -> str:
    directory = os.path.dirname(source_paths[0])
    signature = []
    for path in source_paths:
        try:
            stat = os.stat(path)
            signature.append([path, stat.st_mtime_ns, stat.st_size])
        except OSError:
            signature.append([path, None, None])
    digest = hashlib.sha1(json.dumps(signature, ensure_ascii=False).encode("utf-8")).hexdigest()[:14]
    return os.path.join(directory, f"logo_lockup_{digest}.png")


def _border_background_color(img: Image.Image) -> tuple[int, int, int]:
    rgb = img.convert("RGB")
    width, height = rgb.size
    border = Image.new("RGB", (width * 2 + height * 2, 1))
    x = 0
    for crop in (
        rgb.crop((0, 0, width, 1)),
        rgb.crop((0, height - 1, width, height)),
        rgb.crop((0, 0, 1, height)).transpose(Image.Transpose.ROTATE_90),
        rgb.crop((width - 1, 0, width, height)).transpose(Image.Transpose.ROTATE_90),
    ):
        border.paste(crop, (x, 0))
        x += crop.width
    stat = ImageStat.Stat(border)
    return tuple(int(v) for v in stat.median[:3])


def _foreground_mask(img: Image.Image) -> Image.Image:
    rgba = img.convert("RGBA")
    alpha = rgba.getchannel("A")
    if alpha.getextrema()[0] < 245:
        return alpha.point(lambda a: 255 if a > 16 else 0)

    bg = _border_background_color(rgba)
    bg_img = Image.new("RGB", rgba.size, bg)
    diff = ImageChops.difference(rgba.convert("RGB"), bg_img).convert("L")
    return diff.point(lambda v: 255 if v > 24 else 0)


def _padded_bbox(mask: Image.Image, padding_ratio: float = 0.045) -> tuple[int, int, int, int]:
    bbox = mask.getbbox()
    if not bbox:
        return (0, 0, mask.width, mask.height)
    left, top, right, bottom = bbox
    pad = max(2, int(max(right - left, bottom - top) * padding_ratio))
    return (
        max(0, left - pad),
        max(0, top - pad),
        min(mask.width, right + pad),
        min(mask.height, bottom + pad),
    )


def _prepared_logo_crop(source_path: str) -> tuple[Image.Image, Image.Image] | None:
    img = Image.open(source_path).convert("RGBA")
    mask = _foreground_mask(img)
    bbox = _padded_bbox(mask)
    # Shave sparse edge columns/rows to remove attached decorative lines
    # (e.g. vertical separators next to wordmarks)
    bbox = _shave_sparse_edges(mask, bbox, max_shave=8)
    cropped = img.crop(bbox)
    cropped_mask = mask.crop(bbox)

    if cropped.getchannel("A").getextrema()[0] >= 245:
        alpha = cropped_mask.filter(ImageFilter.GaussianBlur(0.4))
        cropped.putalpha(alpha)
    return cropped, cropped_mask


def _component_bboxes(mask: Image.Image, max_side: int = 520) -> list[tuple[int, int, int, int, int]]:
    if mask.width <= 0 or mask.height <= 0:
        return []
    scale = min(1.0, max_side / max(mask.width, mask.height))
    if scale < 1:
        work = mask.resize((max(1, int(mask.width * scale)), max(1, int(mask.height * scale))), Image.Resampling.NEAREST)
    else:
        work = mask
    width, height = work.size
    pixels = list(work.getdata())
    seen = bytearray(width * height)
    components: list[tuple[int, int, int, int, int]] = []

    for idx, value in enumerate(pixels):
        if value <= 0 or seen[idx]:
            continue
        stack = [idx]
        seen[idx] = 1
        min_x = max_x = idx % width
        min_y = max_y = idx // width
        count = 0
        while stack:
            current = stack.pop()
            count += 1
            x = current % width
            y = current // width
            if x < min_x:
                min_x = x
            elif x > max_x:
                max_x = x
            if y < min_y:
                min_y = y
            elif y > max_y:
                max_y = y
            for neighbor in (current - 1, current + 1, current - width, current + width):
                if neighbor < 0 or neighbor >= len(pixels) or seen[neighbor] or pixels[neighbor] <= 0:
                    continue
                nx = neighbor % width
                ny = neighbor // width
                if abs(nx - x) + abs(ny - y) != 1:
                    continue
                seen[neighbor] = 1
                stack.append(neighbor)
        if count < 16:
            continue
        if scale < 1:
            components.append((
                int(min_x / scale),
                int(min_y / scale),
                min(mask.width, int((max_x + 1) / scale) + 1),
                min(mask.height, int((max_y + 1) / scale) + 1),
                int(count / (scale * scale)),
            ))
        else:
            components.append((min_x, min_y, max_x + 1, max_y + 1, count))
    return components


def _bbox_chroma_share(img: Image.Image, mask: Image.Image, bbox: tuple[int, int, int, int]) -> float:
    crop = img.crop(bbox).convert("RGBA")
    crop_mask = mask.crop(bbox)
    total = 0
    chroma = 0
    for (r, g, b, a), m in zip(crop.getdata(), crop_mask.getdata()):
        if a <= 96 or m <= 0:
            continue
        total += 1
        if max(r, g, b) - min(r, g, b) >= 38:
            chroma += 1
    return chroma / max(total, 1)


def _shave_sparse_edges(mask: Image.Image, bbox: tuple[int, int, int, int], max_shave: int = 8) -> tuple[int, int, int, int]:
    """
    Density-based edge trimming: shave columns/rows at the bbox edges
    whose foreground density is anomalously low compared to the median.
    Helps remove attached decorative lines (vertical or horizontal)
    that sit at the edge of the logo crop.
    """
    left, top, right, bottom = bbox
    crop_mask = mask.crop(bbox)
    width, height = crop_mask.size
    if width <= 10 or height <= 10:
        return bbox

    # Column density
    col_density = []
    for x in range(width):
        col = crop_mask.crop((x, 0, x + 1, height))
        non_zero = sum(1 for v in col.getdata() if v > 0)
        col_density.append(non_zero / height)

    sorted_density = sorted(col_density)
    median_density = sorted_density[len(sorted_density) // 2]
    effective_threshold = max(0.15, median_density * 0.25)

    max_shave_x = min(max_shave, int(width * 0.15))
    new_left = left
    for x in range(max_shave_x):
        d = col_density[x]
        if 0 < d < effective_threshold:
            new_left += 1
        else:
            break

    new_right = right
    for x in range(max_shave_x):
        idx = width - 1 - x
        d = col_density[idx]
        if 0 < d < effective_threshold:
            new_right -= 1
        else:
            break

    # Row density
    row_density = []
    for y in range(height):
        row = crop_mask.crop((0, y, width, y + 1))
        non_zero = sum(1 for v in row.getdata() if v > 0)
        row_density.append(non_zero / width)

    sorted_row_density = sorted(row_density)
    median_row_density = sorted_row_density[len(sorted_row_density) // 2]
    row_threshold = max(0.15, median_row_density * 0.25)

    max_shave_y = min(max_shave, int(height * 0.15))
    new_top = top
    for y in range(max_shave_y):
        d = row_density[y]
        if 0 < d < row_threshold:
            new_top += 1
        else:
            break

    new_bottom = bottom
    for y in range(max_shave_y):
        idx = height - 1 - y
        d = row_density[idx]
        if 0 < d < row_threshold:
            new_bottom -= 1
        else:
            break

    # Safety: don't over-trim
    if new_right - new_left < max(8, width * 0.3):
        new_left, new_right = left, right
    if new_bottom - new_top < max(8, height * 0.3):
        new_top, new_bottom = top, bottom

    return (new_left, new_top, new_right, new_bottom)


def _tighten_symbol_bbox(mask: Image.Image, bbox: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """
    Shave off sparse edge columns/rows or attached decorative lines
    (e.g. vertical separators) from a symbol bbox.

    Uses erosion + re-connected-components to break thin connections,
    then picks the largest surviving component as the true symbol.
    """
    left, top, right, bottom = bbox
    crop_mask = mask.crop(bbox)
    width, height = crop_mask.size
    if width <= 6 or height <= 6:
        return bbox

    # Erode to break thin connections (e.g. a 1-px bridge to a separator line)
    eroded = crop_mask.filter(ImageFilter.MinFilter(3))
    components = _component_bboxes(eroded)

    if len(components) >= 2:
        # Erosion split the shape; keep the largest piece (the real symbol)
        largest = max(components, key=lambda c: c[4])
        lx, ly, lrx, lby, _ = largest
        pad = max(2, int(max(lrx - lx, lby - ly) * 0.1))
        return (
            max(left, left + lx - pad),
            max(top, top + ly - pad),
            min(right, left + lrx + pad),
            min(bottom, top + lby + pad),
        )

    # Fallback: density-based edge tightening for sparse tails
    return _shave_sparse_edges(mask, bbox, max_shave=5)


def _symbol_candidate_bbox(img: Image.Image, mask: Image.Image) -> tuple[int, int, int, int] | None:
    foreground_area = sum(1 for value in mask.getdata() if value > 0)
    if foreground_area <= 0:
        return None
    full_ratio = img.width / max(img.height, 1)
    if full_ratio < 1.45:
        return None

    best: tuple[float, tuple[int, int, int, int], float, float] | None = None
    min_area = max(24, int(foreground_area * 0.018))
    for left, top, right, bottom, area in _component_bboxes(mask):
        if area < min_area:
            continue
        width = max(1, right - left)
        height = max(1, bottom - top)
        ratio = width / height
        # Tightened aspect ratio filter to reject thin vertical/horizontal lines
        if ratio < 0.35 or ratio > 2.0:
            continue
        if width >= img.width * 0.72:
            continue
        chroma_share = _bbox_chroma_share(img, mask, (left, top, right, bottom))
        area_share = area / max(foreground_area, 1)
        # Skip tiny decorative lines even if they pass aspect ratio
        if area_share < 0.05:
            continue
        compactness = min(ratio, 1 / max(ratio, 0.01))
        left_bonus = 1.0 - min(1.0, left / max(img.width, 1))
        # Reduced left_bonus weight and increased compactness weight
        # so that thin decorative lines near the left edge don't outscore
        # the actual brand mark.
        score = area_share * 1.15 + chroma_share * 0.85 + compactness * 0.45 + left_bonus * 0.06
        if best is None or score > best[0]:
            best = (score, (left, top, right, bottom), area_share, chroma_share)

    if not best:
        return None
    _score, bbox, area_share, chroma_share = best
    if chroma_share < 0.12 and area_share < 0.16:
        return None

    # Post-process: tighten bbox by dropping edge columns that are too sparse
    # (helps remove attached vertical separator lines)
    left, top, right, bottom = _tighten_symbol_bbox(mask, bbox)

    pad = max(2, int(max(right - left, bottom - top) * 0.18))
    return (
        max(0, left - pad),
        max(0, top - pad),
        min(img.width, right + pad),
        min(img.height, bottom + pad),
    )


def prepare_logo_overlay_image(source_path: str) -> str:
    """
    Build a cached transparent, tightly cropped logo for overlays and logo-as-scene refs.

    Users often upload screenshots with white or solid-color backgrounds. The raw file
    should be preserved, but the render pipeline needs a clean mark.
    """
    if not source_path or not os.path.exists(source_path):
        return source_path

    output_path = _overlay_cache_path(source_path)
    try:
        if os.path.exists(output_path) and os.path.getmtime(output_path) >= os.path.getmtime(source_path):
            return output_path
    except OSError:
        return source_path

    try:
        prepared = _prepared_logo_crop(source_path)
        if not prepared:
            return source_path
        cropped, _cropped_mask = prepared
        cropped.save(output_path, "PNG")
        return output_path
    except Exception:
        return source_path


def prepare_logo_symbol_image(source_path: str) -> str | None:
    """
    Extract a likely standalone mark from a horizontal logo lockup.

    This is deterministic and local. If the logo looks like a wordmark-only
    lockup, return None so the render policy can omit or use the full logo.
    """
    if not source_path or not os.path.exists(source_path):
        return None

    output_path = _symbol_cache_path(source_path)
    none_path = _symbol_none_cache_path(source_path)
    try:
        source_mtime = os.path.getmtime(source_path)
        if os.path.exists(output_path) and os.path.getmtime(output_path) >= source_mtime:
            return output_path
        if os.path.exists(none_path) and os.path.getmtime(none_path) >= source_mtime:
            return None
    except OSError:
        return None

    try:
        prepared = _prepared_logo_crop(source_path)
        if not prepared:
            return None
        cropped, cropped_mask = prepared
        bbox = _symbol_candidate_bbox(cropped, cropped_mask)
        if not bbox:
            with open(none_path, "w", encoding="utf-8") as f:
                f.write("no reliable symbol")
            return None
        symbol = cropped.crop(bbox)
        symbol_mask = cropped_mask.crop(bbox)
        if symbol.getchannel("A").getextrema()[0] >= 245:
            symbol.putalpha(symbol_mask.filter(ImageFilter.GaussianBlur(0.4)))
        symbol.save(output_path, "PNG")
        return output_path
    except Exception:
        return None


def prepare_logo_lockup_image(source_paths: list[str] | tuple[str, ...] | None) -> str | None:
    """
    Build a cached transparent co-brand lockup from one or more uploaded logos.

    Multiple logos need a stable visual signature instead of being placed
    independently on every page. Each logo is first trimmed/transparentized,
    then scaled to a shared optical height with separators between marks.
    """
    valid_paths = _valid_logo_paths(source_paths)
    if not valid_paths:
        return None
    if len(valid_paths) == 1:
        return prepare_logo_overlay_image(valid_paths[0])

    output_path = _lockup_cache_path(valid_paths)
    try:
        newest_source = max(os.path.getmtime(path) for path in valid_paths)
        if os.path.exists(output_path) and os.path.getmtime(output_path) >= newest_source:
            return output_path
    except OSError:
        return prepare_logo_overlay_image(valid_paths[0])

    opened: list[Image.Image] = []
    try:
        for path in valid_paths:
            logo_path = prepare_logo_overlay_image(path)
            if not logo_path or not os.path.exists(logo_path):
                continue
            with Image.open(logo_path) as source:
                img = source.convert("RGBA")
                if img.width <= 0 or img.height <= 0:
                    continue
                opened.append(img.copy())
    except Exception:
        return prepare_logo_overlay_image(valid_paths[0])

    if not opened:
        return prepare_logo_overlay_image(valid_paths[0])
    if len(opened) == 1:
        opened[0].save(output_path, "PNG")
        return output_path

    target_height = 160
    gap = int(target_height * 0.24)
    separator_width = max(2, int(target_height * 0.018))
    separator_height = int(target_height * 0.58)

    scaled: list[Image.Image] = []
    for img in opened:
        ratio = img.width / max(img.height, 1)
        width = max(1, int(target_height * ratio))
        scaled.append(img.resize((width, target_height), Image.Resampling.LANCZOS))

    total_width = sum(img.width for img in scaled) + gap * (len(scaled) - 1) + separator_width * (len(scaled) - 1)
    canvas = Image.new("RGBA", (max(1, total_width), target_height), (255, 255, 255, 0))
    x = 0
    for idx, img in enumerate(scaled):
        canvas.alpha_composite(img, (x, 0))
        x += img.width
        if idx < len(scaled) - 1:
            sep_x = x + gap // 2
            sep_y = (target_height - separator_height) // 2
            separator = Image.new("RGBA", (separator_width, separator_height), (42, 52, 65, 118))
            canvas.alpha_composite(separator, (sep_x, sep_y))
            x += gap + separator_width

    try:
        canvas.save(output_path, "PNG")
        return output_path
    except Exception:
        return prepare_logo_overlay_image(valid_paths[0])
