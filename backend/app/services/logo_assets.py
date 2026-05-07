import hashlib
import json
import os
from functools import lru_cache

from PIL import Image, ImageChops, ImageFilter, ImageStat


def _overlay_cache_path(source_path: str) -> str:
    directory = os.path.dirname(source_path)
    stem, _ = os.path.splitext(os.path.basename(source_path))
    return os.path.join(directory, f"logo_overlay_{stem}.png")


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
        img = Image.open(source_path).convert("RGBA")
        mask = _foreground_mask(img)
        bbox = _padded_bbox(mask)
        cropped = img.crop(bbox)
        cropped_mask = mask.crop(bbox)

        if cropped.getchannel("A").getextrema()[0] >= 245:
            alpha = cropped_mask.filter(ImageFilter.GaussianBlur(0.4))
            cropped.putalpha(alpha)

        cropped.save(output_path, "PNG")
        return output_path
    except Exception:
        return source_path


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


@lru_cache(maxsize=256)
def prepared_logo_overlay_image_cached(source_path: str, mtime: float | None = None) -> str:
    return prepare_logo_overlay_image(source_path)
