import base64
from collections import Counter
import json
import logging
import os
from typing import Dict

from PIL import Image as PILImage
import requests

from app.core.provider_credentials import get_provider_credentials
from app.services.visual_strategy import detect_logo_tone_from_image

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
    base = get_provider_credentials().minimax_api_base.rstrip("/")
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
                "Authorization": f"Bearer {get_provider_credentials().minimax_api_key}",
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
    result = _parse_analysis_result(raw, "logo")
    if isinstance(result, dict):
        result.update(detect_logo_tone_from_image(image_path))
    return result


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


def analyze_visual_asset(image_path: str, asset_name: str = "", asset_kind: str = "", usage_note: str = "") -> Dict:
    """
    Analyze a global visual asset such as a product, person, scene, or material.
    The result is content-facing, not style-facing: it helps later pages decide
    whether and how to use the image as a concrete reference.
    """
    if not os.path.exists(image_path):
        logger.warning(f"Visual asset file not found: {image_path}")
        return _default_visual_asset_analysis(asset_name, asset_kind, usage_note)

    local_palette = extract_image_palette(image_path)
    filename = os.path.basename(image_path)
    prompt = f"""你是一位 PPT 视觉资产编目助手。请只分析这张图片中真实存在的主体，帮助后续模型判断哪些页面需要引用它，并在生成时锁定它的身份。严格输出 JSON：

{{
  "detected_kind": "product/person/scene/material/other 中最合适的一类",
  "subject": "图片主体的简短名称，如某个具体产品、人物、主KV、物料或场景",
  "description": "80字以内，客观描述图片中主体、包装/外观/服装/姿态/场景",
  "identity_elements": ["身份承载元素1，如外轮廓/结构/材质/标识/文字层级/表面图形/姿态", "元素2", "元素3"],
  "distinctive_features": ["必须保真的可识别特征1", "特征2", "特征3"],
  "must_not_change": ["生成时绝对不能改变的身份项1", "身份项2", "身份项3"],
  "suggested_keywords": ["后续页面文案中可能触发使用它的关键词"],
  "recommended_usage": "这张资产适合出现在哪类页面，80字以内",
  "fidelity_note": "通用保真要求，说明哪些身份元素必须保持，80字以内"
}}

用户给出的名称：{asset_name or "未提供"}
用户给出的类别：{asset_kind or "未提供"}
用户给出的使用说明：{usage_note or "未提供"}
文件名：{filename}

注意：
1. 必须输出合法 JSON，不要加额外说明
2. 不要把它当作风格参考图，不要分析 PPT 风格系统，重点是主体识别和身份保真
3. 输出必须可泛化到任意产品/人物/物料/主KV，不要只套用某一种商品品类的细节
4. 如果无法判断，仍给出基于文件名和可见主体的保守描述"""

    raw = _call_vision_model(image_path, prompt)
    result = _parse_analysis_result(raw, "visual_asset")
    result.setdefault("detected_kind", asset_kind or "other")
    result.setdefault("subject", asset_name or os.path.splitext(filename)[0])
    result.setdefault("description", "")
    result.setdefault("identity_elements", [])
    result.setdefault("distinctive_features", [])
    result.setdefault("must_not_change", [])
    result.setdefault("suggested_keywords", [])
    result.setdefault("recommended_usage", usage_note or "")
    result.setdefault("fidelity_note", "")
    result["dominant_palette"] = local_palette
    return result


def describe_context_image(image_path: str, image_name: str = "", role: str = "", purpose: str = "") -> str:
    """
    Read a user-supplied context image for Agent chat and content planning.
    This is content-facing: OCR first, then summarize the visual material so
    text-only downstream prompts still receive the image's substance.
    """
    if not os.path.exists(image_path):
        logger.warning(f"Context image file not found: {image_path}")
        return ""

    filename = os.path.basename(image_path)
    prompt = f"""你是 PPT Agent 的读图助手。请忠实解读这张用户上传的图片，重点服务于 PPT 内容提取、页面修改和视觉参考。

请按以下结构输出，尽量具体，不要编造图片外信息：

1. OCR文字：逐条列出图片中可读的标题、正文、数字、标签、品牌名、流程节点、图表文字；如果没有文字，写“无明显文字”。
2. 图像内容：客观描述图片中的主体、版式、图表/流程/产品/场景，以及层级关系。
3. 可用于PPT的信息：提炼可以写进 PPT 的要点、数据、结构或修改建议。
4. 视觉参考：如果图片适合作为视觉参考，描述配色、排版、构图、素材主体和必须保留的识别特征。

图片名称：{image_name or filename}
图片角色：{role or "用户上传图片"}
使用场景：{purpose or "Agent 对话上下文"}"""

    raw = _call_vision_model(image_path, prompt)
    return (raw or "").strip()[:4000]


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
    if analysis_type == "visual_asset":
        return _default_visual_asset_analysis()
    return _default_reference_analysis()


def _default_logo_analysis() -> Dict:
    return {
        "primary_color": "",
        "secondary_colors": [],
        "mood": "",
        "font_style": "",
        "industry_vibe": "",
        "description": "",
        "logo_tone": "unknown",
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


def _default_visual_asset_analysis(asset_name: str = "", asset_kind: str = "", usage_note: str = "") -> Dict:
    return {
        "detected_kind": asset_kind or "other",
        "subject": asset_name or "",
        "description": "",
        "identity_elements": [],
        "distinctive_features": [],
        "must_not_change": [],
        "suggested_keywords": [],
        "recommended_usage": usage_note or "",
        "fidelity_note": "",
        "dominant_palette": [],
    }
