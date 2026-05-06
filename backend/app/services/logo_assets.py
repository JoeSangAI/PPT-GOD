import os
from functools import lru_cache

from PIL import Image, ImageChops, ImageFilter, ImageStat


def _overlay_cache_path(source_path: str) -> str:
    directory = os.path.dirname(source_path)
    stem, _ = os.path.splitext(os.path.basename(source_path))
    return os.path.join(directory, f"logo_overlay_{stem}.png")


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


@lru_cache(maxsize=256)
def prepared_logo_overlay_image_cached(source_path: str, mtime: float | None = None) -> str:
    return prepare_logo_overlay_image(source_path)
