"""
用 Gemini 3.1 Flash 做多图理解 + 智能排版验证
"""
import os
import sys
import base64
import requests

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ENV", "dev")

from PIL import Image, ImageDraw, ImageFont
from app.services.image_generation import generate_slide_image
from app.services.overlay_layers import overlay_reservation_instruction

OUTPUT_DIR = "./test_outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)

ASSETS_DIR = "/Users/Joe_1/Desktop/Development/ppt-god/backend/uploads/78a21fde-1857-48ca-9047-8494612e26f8/pptx_assets"

COMET_KEY = "sk-wX2r2x7Df0i0NIvf1nC3UD38xQctsnbNHo0owagvH4QaBj3g"
COMET_URL = "https://api.cometapi.com/v1/chat/completions"

SCREENSHOTS = {
    "s1": os.path.join(ASSETS_DIR, "2026.05_5_p020_3524386527.png"),
    "s2": os.path.join(ASSETS_DIR, "2026.05_5_p020_63fbd7601f.png"),
    "s3": os.path.join(ASSETS_DIR, "2026.05_5_p020_f7d45a97d7.png"),
}


def encode_image(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def get_layout_from_gemini(bg_path, assets):
    """用 Gemini 3.1 Flash 分析背景+素材，输出布局 JSON。"""

    # 构建多图消息
    content = [
        {
            "type": "text",
            "text": """你是 PPT 设计专家。请分析这张 PPT 背景图和3张素材截图，给出最优的布局方案。

要求：
1. 标题"案例演示"必须清晰可见，不能被素材遮挡
2. 3张素材要美观分布，间距均匀
3. 考虑每张素材的宽高比，不要让任何素材变形或缩得太小
4. 输出格式必须是合法 JSON

输出格式：
{
  "layout_description": "布局说明",
  "placements": [
    {"asset_id": "s1", "x": 0.05, "y": 0.25, "width": 0.4, "height": 0.5, "reason": "左侧主图"},
    {"asset_id": "s2", "x": 0.55, "y": 0.25, "width": 0.4, "height": 0.5, "reason": "右侧主图"},
    {"asset_id": "s3", "x": 0.3, "y": 0.78, "width": 0.4, "height": 0.15, "reason": "底部辅助图"}
  ]
}

坐标说明：x/y/width/height 都是 0-1 的归一化值，基于 1792x1024 像素的幻灯片。"""
        },
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{encode_image(bg_path)}"}
        },
    ]

    # 添加素材图
    for aid, apath in assets.items():
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{encode_image(apath)}"}
        })

    resp = requests.post(
        COMET_URL,
        headers={
            "Authorization": f"Bearer {COMET_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "gemini-3.1-flash-image",
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.3,
            "max_tokens": 1000,
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    raw = data["choices"][0]["message"]["content"]

    # 解析 JSON
    import json, re
    cleaned = re.sub(r"^```(?:json)?\s*|```$", "", raw.strip(), flags=re.MULTILINE | re.IGNORECASE).strip()
    result = json.loads(cleaned)

    placements = {}
    for p in result.get("placements", []):
        aid = p["asset_id"]
        placements[aid] = (
            int(float(p["x"]) * 1792),
            int(float(p["y"]) * 1024),
            int(float(p["width"]) * 1792),
            int(float(p["height"]) * 1024),
        )

    print(f"Gemini layout: {result.get('layout_description', '')}")
    for aid, box in placements.items():
        print(f"  {aid}: {box}")

    return placements


def place_screenshot(bg_path, asset_path, box, label, output_path):
    """放置截图。"""
    bg = Image.open(bg_path).convert("RGBA")
    asset = Image.open(asset_path).convert("RGBA")

    left, top, width, height = box
    asset_w, asset_h = asset.size

    scale = min(width / asset_w, height / asset_h)
    pic_w = int(asset_w * scale)
    pic_h = int(asset_h * scale)
    pic_left = left + (width - pic_w) // 2
    pic_top = top + (height - pic_h) // 2

    # 蓝色框
    overlay = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle([left, top, left + width, top + height], outline=(0, 100, 255, 200), width=2)
    bg = Image.alpha_composite(bg, overlay)

    asset_resized = asset.resize((pic_w, pic_h), Image.Resampling.LANCZOS)
    bg.paste(asset_resized, (pic_left, pic_top), asset_resized)

    # 标注
    draw = ImageDraw.Draw(bg)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
    except:
        font = ImageFont.load_default()
    info = f"{label}"
    bbox = draw.textbbox((0, 0), info, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.rectangle([left, top - th - 4, left + tw + 8, top], fill=(0, 0, 0, 180))
    draw.text((left + 4, top - th - 2), info, fill=(255, 255, 255), font=font)

    bg.save(output_path, "PNG")
    return output_path


def main():
    print("=" * 60)
    print("Gemini 3.1 Flash - Multi-Image Layout Test")
    print("=" * 60)

    # Step 1: 生图
    visual = {
        "overlay_layers": [
            {"asset_id": "s1", "enabled": True, "preset": "left-card", "mode": "exact_cutout"},
            {"asset_id": "s2", "enabled": True, "preset": "right-card", "mode": "exact_cutout"},
            {"asset_id": "s3", "enabled": True, "preset": "bottom-band", "mode": "exact_cutout"},
        ]
    }
    prompt = (
        "Create one polished widescreen landscape presentation slide.\n\n"
        "Visible Text:\n"
        '- Headline: "案例演示"\n'
        "Style: Modern corporate, soft blue gradient, professional typography.\n\n"
        "Visual: Clean atmospheric background with subtle depth.\n\n"
        f"Exact Overlay Reservation:\n{overlay_reservation_instruction(visual)}"
    )

    print("\n[Step 1] Generating background...")
    img = generate_slide_image(prompt, resolution="1K", aspect_ratio="16:9")
    bg_path = os.path.join(OUTPUT_DIR, "bg_gemini_layout.png")
    img.save(bg_path, "PNG")
    print(f"Background: {bg_path}")

    # Step 2: Gemini 多图理解
    print("\n[Step 2] Gemini analyzing background + assets...")
    try:
        layout = get_layout_from_gemini(bg_path, SCREENSHOTS)
    except Exception as e:
        print(f"Gemini failed: {e}")
        print("Falling back to smart layout...")
        # 兜底：用代码算法
        layout = {
            "s1": (int(1792*0.05), int(1024*0.22), int(1792*0.4), int(1024*0.55)),
            "s2": (int(1792*0.55), int(1024*0.22), int(1792*0.4), int(1024*0.55)),
            "s3": (int(1792*0.25), int(1024*0.78), int(1792*0.5), int(1024*0.15)),
        }

    # Step 3: 放置截图
    print("\n[Step 3] Placing screenshots...")
    temp1 = os.path.join(OUTPUT_DIR, "temp_gemini_1.png")
    temp2 = os.path.join(OUTPUT_DIR, "temp_gemini_2.png")

    place_screenshot(bg_path, SCREENSHOTS["s1"], layout["s1"], "S1", temp1)
    place_screenshot(temp1, SCREENSHOTS["s2"], layout["s2"], "S2", temp2)
    place_screenshot(temp2, SCREENSHOTS["s3"], layout["s3"], "S3",
                     os.path.join(OUTPUT_DIR, "final_gemini_layout.png"))

    print("\n" + "=" * 60)
    print("Done! Check final_gemini_layout.png")
    print("=" * 60)


if __name__ == "__main__":
    main()
