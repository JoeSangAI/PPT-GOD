import base64
from collections import Counter
import json
import logging
import os
from typing import Dict

from PIL import Image as PILImage
import requests

from app.core.config import settings

logger = logging.getLogger(__name__)


def _encode_image_to_base64(image_path: str) -> str:
    """将图片文件转为 base64 字符串。"""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _guess_mime_type(path: str) -> str:
    """根据后缀猜测图片 MIME 类型。"""
    ext = os.path.splitext(path)[1].lower()
    mapping = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    return mapping.get(ext, "image/jpeg")


def extract_image_palette(image_path: str, max_colors: int = 6) -> list[Dict]:
    """Extract an approximate dominant palette locally so style cloning has concrete colors."""
    if not os.path.exists(image_path):
        return []

    try:
        with PILImage.open(image_path) as img:
            img = img.convert("RGBA")
            img.thumbnail((240, 240))

            pixels = []
            for r, g, b, a in img.getdata():
                if a < 24:
                    continue
                # Quantize manually to reduce noise while keeping the actual hue family.
                rq, gq, bq = (round(r / 16) * 16, round(g / 16) * 16, round(b / 16) * 16)
                pixels.append((min(rq, 255), min(gq, 255), min(bq, 255)))

            if not pixels:
                return []

            total = len(pixels)
            dominant = Counter(pixels).most_common(max_colors * 3)
            palette = []
            for (r, g, b), count in dominant:
                # Skip nearly identical colors already selected.
                if any(abs(r - c["rgb"][0]) + abs(g - c["rgb"][1]) + abs(b - c["rgb"][2]) < 48 for c in palette):
                    continue
                palette.append({
                    "hex": f"#{r:02X}{g:02X}{b:02X}",
                    "share": round(count / total, 4),
                    "rgb": [r, g, b],
                })
                if len(palette) >= max_colors:
                    break
            return palette
    except Exception as e:
        logger.warning(f"ImageAnalyzer: local palette extraction failed for {image_path}: {e}")
        return []


def _minimax_coding_plan_url() -> str:
    base = settings.MINIMAX_API_BASE.rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/coding_plan/vlm"
    return f"{base}/v1/coding_plan/vlm"


def _call_vision_model(image_path: str, prompt: str) -> str:
    """调用 MiniMax Token Plan VLM 分析图片，返回文本结果。"""
    try:
        b64 = _encode_image_to_base64(image_path)
        mime = _guess_mime_type(image_path)
        image_url = f"data:{mime};base64,{b64}"
        resp = requests.post(
            _minimax_coding_plan_url(),
            headers={
                "Authorization": f"Bearer {settings.MINIMAX_API_KEY}",
                "Content-Type": "application/json",
                "MM-API-Source": "Minimax-MCP",
            },
            json={"prompt": prompt, "image_url": image_url},
            timeout=120,
        )
        resp.raise_for_status()
        body = resp.json()
        base_resp = body.get("base_resp") or {}
        if base_resp and base_resp.get("status_code", 0) != 0:
            raise RuntimeError(f"{base_resp.get('status_code')}: {base_resp.get('status_msg')}")
        return body.get("content", "") or ""
    except Exception as e:
        logger.error(f"Token Plan VLM call failed for {image_path}: {e}")
        return ""


def analyze_logo(image_path: str) -> Dict:
    """
    分析 Logo 图片，提取品牌设计信息。
    返回: {primary_color, secondary_colors, mood, font_style, industry_vibe, description}
    """
    if not os.path.exists(image_path):
        logger.warning(f"Logo file not found: {image_path}")
        return _default_logo_analysis()

    prompt = """你是一位品牌设计分析师。请分析这张 Logo 图片，提取以下信息并严格输出 JSON 格式：

{
  "primary_color": "主品牌色（带 HEX 编码，如 #1A365D）",
  "secondary_colors": ["辅助色1（带 HEX）", "辅助色2（带 HEX）"],
  "mood": "品牌整体调性（3-5个形容词，如'专业、稳重、科技'）",
  "font_style": "Logo 体现的字体风格（如'无衬线黑体、现代简洁'）",
  "industry_vibe": "行业气质推断（如'金融科技、消费品、医疗健康'）",
  "description": "50字以内的设计风格描述"
}

注意：
1. 必须输出合法的 JSON，不要加任何额外说明
2. 颜色必须给出 HEX 编码
3. 如果无法判断某项，留空字符串或空数组"""

    raw = _call_vision_model(image_path, prompt)
    return _parse_analysis_result(raw, "logo")


def analyze_reference_image(image_path: str) -> Dict:
    """
    分析参考图（PPT 设计参考 / 视觉参考），提取设计风格信息。
    返回: {colors, composition_style, mood, font_suggestion, description}
    """
    if not os.path.exists(image_path):
        logger.warning(f"Reference image not found: {image_path}")
        return _default_reference_analysis()

    local_palette = extract_image_palette(image_path)

    prompt = """你是一位 PPT 视觉设计分析师。请只分析这张参考图片本身，提取可迁移到 PPT 风格系统中的视觉基因，并严格输出 JSON 格式：

{
  "style_name": "基于图片实际观感的风格名，不要加入图片外的行业或内容推断",
  "colors": {
    "background": "背景色（带 HEX）",
    "primary": "主色调（带 HEX）",
    "accent": "点缀色（带 HEX）",
    "text": "文字色（带 HEX）"
  },
  "composition_style": "构图风格（如'全屏沉浸、左右分栏、卡片网格'）",
  "mood": "整体氛围（3-5个形容词）",
  "font_suggestion": "字体建议（如'无衬线黑体、标题粗体'）",
  "ornaments": "装饰元素/纹样/材质，描述图片中真实存在的视觉语言",
  "texture": "材质与光影，描述图片中真实存在的背景、质感和明暗层次",
  "clone_rules": "风格迁移规则：配色关系、装饰密度、留白与字体处理，80字以内",
  "description": "80字以内的风格描述，说明这张图的实际设计特点"
}

注意：
1. 必须输出合法的 JSON，不要加任何额外说明
2. 颜色必须给出 HEX 编码
3. 必须忠实描述图片自身的气质；不要因为 PPT 文案中出现战略、科技、数据、增长等词而改变参考图风格判断
4. 如果无法判断某项，留空字符串"""

    raw = _call_vision_model(image_path, prompt)
    result = _parse_analysis_result(raw, "reference")
    result["dominant_palette"] = local_palette
    colors = result.setdefault("colors", {})
    if local_palette:
        if not colors.get("background"):
            colors["background"] = local_palette[0]["hex"]
        if not colors.get("primary"):
            colors["primary"] = local_palette[1]["hex"] if len(local_palette) > 1 else local_palette[0]["hex"]
        if not colors.get("accent"):
            colors["accent"] = local_palette[2]["hex"] if len(local_palette) > 2 else local_palette[0]["hex"]
        if not colors.get("text"):
            colors["text"] = local_palette[3]["hex"] if len(local_palette) > 3 else "#FFFFFF"
    return result


def _parse_analysis_result(raw: str, analysis_type: str) -> Dict:
    """解析视觉模型返回的 JSON，失败时返回默认值。"""
    raw = raw.strip()
    # 清理 markdown 代码块
    import re
    raw = re.sub(r"^```(?:json)?\s*|```$", "", raw, flags=re.MULTILINE | re.IGNORECASE).strip()

    if raw:
        try:
            result = json.loads(raw)
            if isinstance(result, dict):
                logger.info(f"ImageAnalyzer: {analysis_type} analysis succeeded")
                return result
        except json.JSONDecodeError as e:
            logger.warning(f"ImageAnalyzer: JSON parse failed for {analysis_type}: {e}")

    # 回退默认值
    if analysis_type == "logo":
        return _default_logo_analysis()
    return _default_reference_analysis()


def _default_logo_analysis() -> Dict:
    return {
        "primary_color": "",
        "secondary_colors": [],
        "mood": "",
        "font_style": "",
        "industry_vibe": "",
        "description": "",
    }


def _default_reference_analysis() -> Dict:
    return {
        "style_name": "",
        "colors": {"background": "", "primary": "", "accent": "", "text": ""},
        "composition_style": "",
        "mood": "",
        "font_suggestion": "",
        "ornaments": "",
        "texture": "",
        "clone_rules": "",
        "description": "",
        "dominant_palette": [],
    }
