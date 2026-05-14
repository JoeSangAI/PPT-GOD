import hashlib
import json
import math
import os
import textwrap
from typing import Any, Mapping

from PIL import Image, ImageDraw, ImageFont

from app.utils.text_cleaning import normalize_markdown_content

VISUAL_BLOCK_KINDS = {"table", "flywheel", "flow", "matrix"}


def stable_block_hash(block: Mapping[str, Any]) -> str:
    payload = {
        "kind": block.get("kind"),
        "visual_type": block.get("visual_type"),
        "title": block.get("title"),
        "source_spec": block.get("source_spec"),
        "route_mode": block.get("route_mode"),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def is_visual_block(block: Mapping[str, Any] | None) -> bool:
    if not isinstance(block, Mapping):
        return False
    return str(block.get("kind") or "").strip().lower() in VISUAL_BLOCK_KINDS


def normalize_route_mode(value: Any, kind: str | None = None) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"blend", "crop", "original"}:
        return raw
    if raw in {"double_blend", "精修融合"}:
        return "crop"
    if raw in {"overlay", "exact", "exact_overlay", "精确粘贴"}:
        return "original"
    # Relationship-heavy blocks default to a structure-preserving route.
    if kind in {"flywheel", "flow", "matrix"}:
        return "crop"
    return "blend"


def route_to_asset_route_mode(route_mode: str) -> str:
    if route_mode == "original":
        return "overlay"
    if route_mode == "crop":
        return "double_blend"
    return "blend"


def normalize_content_blocks(content_json: dict) -> list[dict]:
    content = content_json if isinstance(content_json, dict) else {}
    raw_blocks = content.get("content_blocks")
    if isinstance(raw_blocks, list):
        blocks = [block for block in raw_blocks if isinstance(block, dict)]
    else:
        text = content.get("text_content") if isinstance(content.get("text_content"), dict) else {}
        body = text.get("body")
        body_text = "\n\n".join(str(x) for x in body) if isinstance(body, list) else str(body or "")
        blocks = [{"id": "body", "kind": "markdown", "markdown": body_text}]

    normalized: list[dict] = []
    seen_ids: set[str] = set()
    for index, block in enumerate(blocks, start=1):
        kind = str(block.get("kind") or "markdown").strip().lower()
        block_id = str(block.get("id") or f"block_{index}").strip() or f"block_{index}"
        if block_id in seen_ids:
            block_id = f"{block_id}_{index}"
        seen_ids.add(block_id)
        next_block = dict(block)
        next_block["id"] = block_id
        next_block["kind"] = kind
        if is_visual_block(next_block):
            route = normalize_route_mode(next_block.get("route_mode"), kind)
            next_block["route_mode"] = route
            next_block["visual_type"] = str(next_block.get("visual_type") or kind).strip().lower()
            next_block["title"] = str(next_block.get("title") or _default_block_title(kind)).strip()
            spec = next_block.get("source_spec")
            next_block["source_spec"] = spec if isinstance(spec, dict) else _default_source_spec(kind, next_block["title"])
            next_block["source_hash"] = stable_block_hash(next_block)
        elif kind == "markdown":
            next_block["markdown"] = normalize_markdown_content(str(next_block.get("markdown") or ""))
        normalized.append(next_block)
    return normalized


def blocks_to_markdown(blocks: list[dict]) -> str:
    parts: list[str] = []
    for block in blocks:
        kind = str(block.get("kind") or "").lower()
        if kind == "markdown":
            markdown = normalize_markdown_content(str(block.get("markdown") or ""))
            if markdown:
                parts.append(markdown)
        elif is_visual_block(block):
            parts.append(_visual_block_markdown_summary(block))
    return "\n\n".join(parts).strip()


def render_visual_block(block: Mapping[str, Any], output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    block_id = str(block.get("id") or "block")
    digest = stable_block_hash(block)[:12]
    output_path = os.path.join(output_dir, f"content_block_{_safe_token(block_id)}_{digest}.png")
    if os.path.exists(output_path):
        return output_path

    width, height = 1400, 840
    img = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    palette = _palette()
    title_font = _font(52, bold=True)
    body_font = _font(30)
    small_font = _font(24)

    kind = str(block.get("kind") or "").lower()
    title = str(block.get("title") or _default_block_title(kind))
    _draw_title(draw, title, title_font, palette)

    spec = block.get("source_spec") if isinstance(block.get("source_spec"), dict) else {}
    if kind == "flywheel":
        _draw_flywheel(draw, spec, width, height, body_font, small_font, palette)
    elif kind == "flow":
        _draw_flow(draw, spec, width, height, body_font, small_font, palette)
    elif kind == "matrix":
        _draw_matrix(draw, spec, width, height, body_font, small_font, palette)
    else:
        _draw_table(draw, spec, width, height, body_font, small_font, palette)
    img.save(output_path, "PNG")
    return output_path


def _default_block_title(kind: str) -> str:
    return {
        "flywheel": "增长飞轮",
        "flow": "流程图",
        "matrix": "对比矩阵",
        "table": "表格",
    }.get(kind, "画面素材")


def _default_source_spec(kind: str, title: str) -> dict:
    if kind == "flywheel":
        return {"center": title, "nodes": ["获客", "激活", "留存", "推荐"]}
    if kind == "flow":
        return {"steps": ["开始", "处理", "完成"]}
    if kind == "matrix":
        return {"columns": ["维度", "方案 A", "方案 B"], "rows": [["价值", "", ""], ["成本", "", ""]]}
    return {"columns": ["项目", "说明"], "rows": [["要点", title]]}


def _visual_block_markdown_summary(block: Mapping[str, Any]) -> str:
    kind = str(block.get("kind") or "").lower()
    title = str(block.get("title") or _default_block_title(kind)).strip()
    spec = block.get("source_spec") if isinstance(block.get("source_spec"), dict) else {}
    if kind == "flywheel":
        nodes = _node_labels(spec)
        return f"[画面素材：{title}]\n类型：飞轮图\n节点：{' → '.join(nodes)}"
    if kind == "flow":
        nodes = _node_labels(spec, key="steps")
        return f"[画面素材：{title}]\n类型：流程图\n步骤：{' → '.join(nodes)}"
    if kind == "matrix":
        return f"[画面素材：{title}]\n类型：对比矩阵"
    return f"[画面素材：{title}]\n类型：表格"


def _safe_token(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)
    return safe[:48] or "block"


def _font(size: int, bold: bool = False):
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if path and os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
    return ImageFont.load_default()


def _palette() -> dict[str, tuple[int, int, int, int]]:
    return {
        "ink": (30, 41, 59, 255),
        "muted": (100, 116, 139, 255),
        "line": (100, 116, 139, 190),
        "panel": (255, 255, 255, 232),
        "panel_soft": (248, 250, 252, 228),
        "accent": (124, 58, 237, 255),
        "accent_2": (14, 165, 233, 255),
        "accent_3": (16, 185, 129, 255),
    }


def _draw_title(draw: ImageDraw.ImageDraw, title: str, font: ImageFont.ImageFont, palette: dict):
    draw.rounded_rectangle((40, 34, 1360, 126), radius=28, fill=palette["panel"])
    draw.text((76, 56), title[:38], fill=palette["ink"], font=font)


def _node_labels(spec: Mapping[str, Any], key: str = "nodes") -> list[str]:
    raw = spec.get(key)
    if not isinstance(raw, list):
        raw = spec.get("items") if isinstance(spec.get("items"), list) else []
    labels = []
    for item in raw:
        if isinstance(item, Mapping):
            label = str(item.get("label") or item.get("title") or item.get("name") or "").strip()
        else:
            label = str(item or "").strip()
        if label:
            labels.append(label)
    return labels[:8] or ["节点一", "节点二", "节点三"]


def _wrapped(text: str, width: int = 9) -> str:
    return "\n".join(textwrap.wrap(str(text), width=width, replace_whitespace=False)) or str(text)


def _text_center(draw, box, text, font, fill):
    x1, y1, x2, y2 = box
    text = _wrapped(text)
    bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=6, align="center")
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.multiline_text((x1 + (x2 - x1 - tw) / 2, y1 + (y2 - y1 - th) / 2), text, fill=fill, font=font, spacing=6, align="center")


def _draw_arrow(draw, start, end, fill, width=6):
    draw.line((start[0], start[1], end[0], end[1]), fill=fill, width=width)
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    size = 22
    p1 = (end[0] - size * math.cos(angle - math.pi / 6), end[1] - size * math.sin(angle - math.pi / 6))
    p2 = (end[0] - size * math.cos(angle + math.pi / 6), end[1] - size * math.sin(angle + math.pi / 6))
    draw.polygon([end, p1, p2], fill=fill)


def _draw_flywheel(draw, spec, width, height, body_font, small_font, palette):
    labels = _node_labels(spec)
    center_label = str(spec.get("center") or "增长飞轮")
    cx, cy = width / 2, height / 2 + 52
    radius = 270
    points = []
    for i, label in enumerate(labels):
        angle = -math.pi / 2 + (2 * math.pi * i / len(labels))
        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)
        points.append((x, y, label))
    for i, (x, y, _label) in enumerate(points):
        nx, ny, _ = points[(i + 1) % len(points)]
        _draw_arrow(draw, (x + (nx - x) * 0.22, y + (ny - y) * 0.22), (x + (nx - x) * 0.78, y + (ny - y) * 0.78), palette["line"], width=5)
    draw.ellipse((cx - 132, cy - 132, cx + 132, cy + 132), fill=palette["panel"], outline=palette["accent"], width=6)
    _text_center(draw, (cx - 110, cy - 80, cx + 110, cy + 80), center_label, body_font, palette["ink"])
    colors = [palette["accent"], palette["accent_2"], palette["accent_3"]]
    for i, (x, y, label) in enumerate(points):
        draw.rounded_rectangle((x - 128, y - 58, x + 128, y + 58), radius=26, fill=palette["panel"], outline=colors[i % len(colors)], width=5)
        _text_center(draw, (x - 104, y - 42, x + 104, y + 42), label, body_font, palette["ink"])


def _draw_flow(draw, spec, width, height, body_font, small_font, palette):
    labels = _node_labels(spec, key="steps")
    count = min(len(labels), 6)
    top = 270
    gap = 34
    card_w = min(260, (width - 120 - gap * (count - 1)) / max(count, 1))
    start_x = (width - (card_w * count + gap * (count - 1))) / 2
    y1, y2 = top, top + 150
    for i, label in enumerate(labels[:count]):
        x1 = start_x + i * (card_w + gap)
        x2 = x1 + card_w
        if i:
            _draw_arrow(draw, (x1 - gap + 8, (y1 + y2) / 2), (x1 - 12, (y1 + y2) / 2), palette["line"], width=5)
        draw.rounded_rectangle((x1, y1, x2, y2), radius=22, fill=palette["panel"], outline=palette["accent_2"], width=4)
        draw.text((x1 + 20, y1 + 18), f"{i + 1:02d}", font=small_font, fill=palette["accent_2"])
        _text_center(draw, (x1 + 18, y1 + 44, x2 - 18, y2 - 18), label, body_font, palette["ink"])


def _draw_table(draw, spec, width, height, body_font, small_font, palette):
    columns = spec.get("columns") if isinstance(spec.get("columns"), list) else ["项目", "说明"]
    rows = spec.get("rows") if isinstance(spec.get("rows"), list) else []
    rows = rows[:6] or [["要点", "说明"]]
    left, top, right = 100, 190, width - 100
    row_h = 82
    col_w = (right - left) / max(len(columns), 1)
    draw.rounded_rectangle((left, top, right, top + row_h * (len(rows) + 1)), radius=24, fill=palette["panel"], outline=palette["line"], width=3)
    for c, col in enumerate(columns):
        x1 = left + c * col_w
        draw.rectangle((x1, top, x1 + col_w, top + row_h), fill=palette["panel_soft"])
        _text_center(draw, (x1 + 8, top + 8, x1 + col_w - 8, top + row_h - 8), str(col), small_font, palette["ink"])
    for r, row in enumerate(rows):
        cells = row if isinstance(row, list) else [row]
        y = top + row_h * (r + 1)
        for c in range(len(columns)):
            x = left + c * col_w
            draw.rectangle((x, y, x + col_w, y + row_h), outline=palette["line"], width=2)
            _text_center(draw, (x + 10, y + 8, x + col_w - 10, y + row_h - 8), str(cells[c] if c < len(cells) else ""), small_font, palette["ink"])


def _draw_matrix(draw, spec, width, height, body_font, small_font, palette):
    if not isinstance(spec.get("columns"), list):
        spec = {"columns": ["维度", "方案 A", "方案 B"], "rows": spec.get("rows") if isinstance(spec.get("rows"), list) else [["价值", "", ""], ["成本", "", ""]]}
    _draw_table(draw, spec, width, height, body_font, small_font, palette)
