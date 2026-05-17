from __future__ import annotations

import os
import re
from typing import Any, Mapping

from PIL import Image


def hex_to_rgb(hex_color: str | None) -> tuple[int, int, int] | None:
    if not isinstance(hex_color, str):
        return None
    match = re.search(r"#[0-9a-fA-F]{6}", hex_color)
    if not match:
        return None
    value = match.group(0).lstrip("#")
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def color_brightness(hex_color: str | None) -> float | None:
    rgb = hex_to_rgb(hex_color)
    if not rgb:
        return None
    return sum(rgb) / 3


def is_dark_color(hex_color: str | None, threshold: float = 86) -> bool:
    brightness = color_brightness(hex_color)
    return brightness is not None and brightness < threshold


def detect_logo_tone_from_image(image_path: str | None) -> dict[str, Any]:
    """Classify visible logo pixels so background strategy can protect contrast."""
    if not image_path or not os.path.exists(image_path):
        return {"logo_tone": "unknown"}
    try:
        with Image.open(image_path) as img:
            img = img.convert("RGBA")
            img.thumbnail((320, 320))
            samples: list[float] = []
            for r, g, b, a in img.getdata():
                if a < 32:
                    continue
                samples.append((r + g + b) / 3)
            if not samples:
                return {"logo_tone": "unknown"}
            avg = sum(samples) / len(samples)
            light_share = sum(1 for x in samples if x >= 178) / len(samples)
            dark_share = sum(1 for x in samples if x <= 82) / len(samples)
            if light_share >= 0.58 or avg >= 174:
                tone = "light"
            elif dark_share >= 0.58 or avg <= 88:
                tone = "dark"
            else:
                tone = "mixed"
            return {
                "logo_tone": tone,
                "logo_avg_brightness": round(avg, 1),
                "logo_light_pixel_share": round(light_share, 3),
                "logo_dark_pixel_share": round(dark_share, 3),
            }
    except Exception:
        return {"logo_tone": "unknown"}


def _first_hex(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str):
            match = re.search(r"#[0-9a-fA-F]{6}", value)
            if match:
                return match.group(0).upper()
    return None


def _palette_hexes(palette: list[Any] | None) -> list[str]:
    hexes: list[str] = []
    for item in palette or []:
        value = item.get("hex") if isinstance(item, Mapping) else item
        hex_color = _first_hex(str(value) if value else "")
        if hex_color and hex_color not in hexes:
            hexes.append(hex_color)
    return hexes


def _logo_tone(logo: Mapping[str, Any] | None) -> str:
    if not isinstance(logo, Mapping):
        return "unknown"
    tone = str(logo.get("logo_tone") or "").strip().lower()
    if tone in {"light", "dark", "mixed"}:
        return tone
    primary = _first_hex(logo.get("primary_color"))
    brightness = color_brightness(primary)
    if brightness is None:
        text = " ".join(str(logo.get(k) or "") for k in ("description", "font_style", "mood"))
        if any(token in text for token in ("白", "浅色", "亮色", "white", "light")):
            return "light"
        return "unknown"
    if brightness >= 170:
        return "light"
    if brightness <= 85:
        return "dark"
    return "mixed"


def build_visual_strategy(
    *,
    summary: Mapping[str, Any] | None = None,
    palette: list[Any] | None = None,
    reference_analysis: Mapping[str, Any] | None = None,
    logo_analysis: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    summary = summary or {}
    ref = reference_analysis or {}
    colors = ref.get("colors") if isinstance(ref.get("colors"), Mapping) else {}
    palette_hex = _palette_hexes(palette)
    background = _first_hex(colors.get("background"), *(palette_hex[:2] or []))
    primary = _first_hex(colors.get("primary"), *(palette_hex[:2] or []))
    ref_text = " ".join(
        str(ref.get(k) or "")
        for k in ("style_name", "mood", "description", "texture", "clone_rules", "composition_style")
    )
    dark_reference = is_dark_color(background) or is_dark_color(primary) or bool(
        re.search(r"(深色|暗调|深暗|黑底|深黑|深紫|深邃|霓虹|赛博)", ref_text)
    )
    logo_tone = _logo_tone(logo_analysis)
    dense_ratio = float(summary.get("dense_page_ratio") or 0)
    table_ratio = float(summary.get("table_page_ratio") or 0)
    high_density = dense_ratio >= 0.45 or table_ratio >= 0.35

    if dark_reference:
        base_tone = "dark"
    elif high_density and not dark_reference and logo_tone != "light":
        base_tone = "light"
    else:
        base_tone = "mixed"

    if base_tone == "dark":
        content_treatment = "信息页保持同一深色系基底，用高对比暗色卡片、局部浅色内容区、清晰字号层级和留白提高可读性。"
        exception_policy = "只有用户明确要求或出现极端表格/长文页时，才允许成组使用浅底信息页，且必须保留同一色系和品牌装饰。"
        logo_contrast = (
            "Logo 偏浅，优先放在低干扰的深色区域，避免浅底吞掉标识；不要在底图里绘制固定 Logo 框或底板。"
            if logo_tone == "light"
            else "Logo 区域必须保持足够对比，优先通过低干扰角落和局部明暗关系解决，不在底图里绘制固定 Logo 框。"
        )
        label = "整体以深色视觉基底为主"
    elif base_tone == "light":
        content_treatment = "正文页以浅底和留白保证阅读效率，强视觉页可使用更深的主色或装饰区形成节奏。"
        exception_policy = "深色页只用于封面、章节、金句或明确需要情绪冲击的页面，不能在正文页随机混用。"
        logo_contrast = (
            "Logo 偏浅时，浅底页应选择低干扰深色角落或深色页眉承载标识；不要在底图里绘制固定 Logo 框或底板。"
            if logo_tone == "light"
            else "Logo 区域必须保持足够对比，避免和背景融合。"
        )
        label = "整体以浅色信息基底为主"
    else:
        content_treatment = "先保持同一套色彩和材质语言，再按页面功能分组调整明暗；同类正文页使用同一种信息页处理。"
        exception_policy = "深浅变化必须按封面/正文/金句/结尾等功能成组出现，不能逐页随机切换。"
        logo_contrast = "Logo 区域必须根据明暗分组选择低干扰角落和足够对比，保证全 deck 可见；不要在底图里绘制固定 Logo 框或底板。"
        label = "按页面功能分组控制明暗"

    strategy = {
        "base_tone": base_tone,
        "background_policy": label,
        "content_treatment": content_treatment,
        "exception_policy": exception_policy,
        "logo_contrast": logo_contrast,
        "summary": f"{label}；{content_treatment}",
    }
    if logo_tone != "unknown":
        strategy["logo_tone"] = logo_tone
    return strategy


def visual_strategy_text(strategy: Mapping[str, Any] | None) -> str:
    if not isinstance(strategy, Mapping):
        return ""
    lines = [
        str(strategy.get("summary") or "").strip(),
        str(strategy.get("exception_policy") or "").strip(),
        str(strategy.get("logo_contrast") or "").strip(),
    ]
    return " ".join(line for line in lines if line)


def visual_language_group(page_type: str | None, layout: str | None, strategy: Mapping[str, Any] | None) -> str:
    base_tone = str((strategy or {}).get("base_tone") or "mixed").lower()
    page_type = str(page_type or "content").lower()
    layout = str(layout or "").lower()
    if page_type in {"cover", "ending"}:
        role = "bookend"
    elif page_type in {"hero", "quote"} or layout == "hero":
        role = "hero"
    elif page_type == "toc" or layout == "toc":
        role = "toc"
    elif page_type == "section" or layout == "section":
        role = "section"
    elif page_type == "data" or layout == "data":
        role = "data"
    else:
        role = "content"
    if base_tone in {"dark", "light"}:
        return f"{base_tone}_{role}"
    return f"mixed_{role}"
