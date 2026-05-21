"""
P20 真实案例 - Kimi K2.6 视觉检测 + 智能排版
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

KIMI_API_KEY = "sk-kimi-5doecx7Y7h4klZME1MnOEDTRJUmzu8x58cdVeEu9RxCtSgxo06rANKSU4UIkqmUb"
KIMI_API_URL = "https://api.kimi.com/coding/v1/chat/completions"

SCREENSHOTS = {
    "s1": os.path.join(ASSETS_DIR, "2026.05_5_p020_3524386527.png"),
    "s2": os.path.join(ASSETS_DIR, "2026.05_5_p020_63fbd7601f.png"),
    "s3": os.path.join(ASSETS_DIR, "2026.05_5_p020_f7d45a97d7.png"),
}

SLIDE_W, SLIDE_H = 1792, 1024


def detect_text_with_kimi(image_path: str) -> list:
    """用 Kimi K2.6 检测文字区域。"""
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    prompt = """分析这张PPT背景图，找出所有文字区域的精确位置。

要求：
1. 只检测实际文字（标题、正文），不检测图片、装饰
2. 返回每个文字区域的归一化坐标（0-1），格式为JSON
3. 坐标系：左上角为(0,0)

输出格式：
{"text_regions": [{"x":0.1, "y":0.1, "width":0.3, "height":0.08, "text":"标题"}]}"""

    resp = requests.post(
        KIMI_API_URL,
        headers={
            "Authorization": f"Bearer {KIMI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "kimi-k2.6",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
                    ]
                }
            ],
            "temperature": 0.2,
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"]["content"]

    # 解析 JSON
    import json, re
    cleaned = re.sub(r"^```(?:json)?\s*|```$", "", content.strip(), flags=re.MULTILINE | re.IGNORECASE).strip()
    try:
        result = json.loads(cleaned)
        regions = result.get("text_regions", [])
        valid = []
        for r in regions:
            if all(k in r for k in ("x", "y", "width", "height")):
                valid.append({
                    "x": max(0.0, float(r["x"])),
                    "y": max(0.0, float(r["y"])),
                    "width": float(r["width"]),
                    "height": float(r["height"]),
                })
        print(f"Kimi detected {len(valid)} text regions")
        return valid
    except Exception as e:
        print(f"Parse error: {e}, raw: {content[:200]}")
        return []


def smart_layout_3_screenshots(text_regions: list, screenshots: dict) -> dict:
    """
    智能排版：3张截图在安全区域内分布。
    布局：上排2张（左右） + 下排1张（居中）
    """
    # 确定标题底部位置
    title_bottom = 0.15  # 默认标题占顶部15%
    if text_regions:
        # 取所有文字区域的底部最大值
        title_bottom = max(r["y"] + r["height"] for r in text_regions)
        title_bottom = min(title_bottom + 0.05, 0.35)  # 加边距，但不超过35%

    safe_top = int(SLIDE_H * title_bottom)
    safe_bottom = int(SLIDE_H * 0.95)  # 底部留5%边距
    safe_height = safe_bottom - safe_top

    # 水平边距和间距
    margin_x = int(SLIDE_W * 0.05)
    gap_x = int(SLIDE_W * 0.04)  # 图之间水平间距
    gap_y = int(SLIDE_H * 0.05)  # 上下排之间间距

    # 上排：2张图
    top_row_height = int(safe_height * 0.58)  # 上排占58%
    top_row_y = safe_top
    top_img_width = (SLIDE_W - margin_x * 2 - gap_x) // 2

    # 获取截图比例
    def get_aspect(path):
        with Image.open(path) as img:
            return img.width / img.height

    aspect_s1 = get_aspect(screenshots["s1"])
    aspect_s2 = get_aspect(screenshots["s2"])
    aspect_s3 = get_aspect(screenshots["s3"])

    # s1: 左侧
    s1_height = min(top_row_height, int(top_img_width / aspect_s1))
    s1_width = int(s1_height * aspect_s1)
    s1_x = margin_x
    s1_y = top_row_y + (top_row_height - s1_height) // 2  # 垂直居中在上排

    # s2: 右侧
    s2_height = min(top_row_height, int(top_img_width / aspect_s2))
    s2_width = int(s2_height * aspect_s2)
    s2_x = margin_x + top_img_width + gap_x
    s2_y = top_row_y + (top_row_height - s2_height) // 2

    # 下排：1张图居中
    bottom_row_y = safe_top + top_row_height + gap_y
    bottom_row_height = safe_bottom - bottom_row_y
    s3_max_width = int(SLIDE_W * 0.7)  # 最大占70%宽度
    s3_height = min(bottom_row_height, int(s3_max_width / aspect_s3))
    s3_width = int(s3_height * aspect_s3)
    s3_x = (SLIDE_W - s3_width) // 2  # 水平居中
    s3_y = bottom_row_y + (bottom_row_height - s3_height) // 2

    return {
        "s1": (s1_x, s1_y, s1_width, s1_height),
        "s2": (s2_x, s2_y, s2_width, s2_height),
        "s3": (s3_x, s3_y, s3_width, s3_height),
        "title_bottom": title_bottom,
    }


def place_screenshot(bg_path, asset_path, box, label, output_path):
    """放置截图到指定位置。"""
    bg = Image.open(bg_path).convert("RGBA")
    asset = Image.open(asset_path).convert("RGBA")

    left, top, width, height = box
    asset_w, asset_h = asset.size

    # contain 缩放
    scale = min(width / asset_w, height / asset_h)
    pic_w = int(asset_w * scale)
    pic_h = int(asset_h * scale)
    pic_left = left + (width - pic_w) // 2
    pic_top = top + (height - pic_h) // 2  # 居中放置

    # 绘制放置区域（蓝色框）
    overlay = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle([left, top, left + width, top + height], outline=(0, 100, 255, 200), width=2)
    bg = Image.alpha_composite(bg, overlay)

    # 贴图
    asset_resized = asset.resize((pic_w, pic_h), Image.Resampling.LANCZOS)
    bg.paste(asset_resized, (pic_left, pic_top), asset_resized)

    # 标注
    draw = ImageDraw.Draw(bg)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
    except:
        font = ImageFont.load_default()
    info = f"{label}: ({left},{top},{width},{height})"
    bbox = draw.textbbox((0, 0), info, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.rectangle([left, top - th - 4, left + tw + 8, top], fill=(0, 0, 0, 180))
    draw.text((left + 4, top - th - 2), info, fill=(255, 255, 255), font=font)

    bg.save(output_path, "PNG")
    print(f"Saved: {output_path}")
    return output_path


def main():
    print("=" * 60)
    print("P20 Case Demo - Kimi K2.6 + Smart Layout")
    print("=" * 60)

    # Step 1: 生图
    visual = {
        "overlay_layers": [
            {"asset_id": "s1", "enabled": True, "preset": "left-card", "mode": "exact_cutout"},
            {"asset_id": "s2", "enabled": True, "preset": "right-card", "mode": "exact_cutout"},
            {"asset_id": "s3", "enabled": True, "preset": "top-right-small", "mode": "exact_cutout"},
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
    bg_path = os.path.join(OUTPUT_DIR, "bg_p20_kimi.png")
    img.save(bg_path, "PNG")
    print(f"Background: {bg_path}")

    # Step 2: Kimi 检测文字
    print("\n[Step 2] Detecting text with Kimi K2.6...")
    text_regions = detect_text_with_kimi(bg_path)
    print(f"Text regions: {text_regions}")

    # Step 3: 智能排版
    print("\n[Step 3] Computing smart layout...")
    layout = smart_layout_3_screenshots(text_regions, SCREENSHOTS)
    print(f"Layout computed:")
    for key, box in layout.items():
        if key != "title_bottom":
            print(f"  {key}: {box}")

    # Step 4: 放置截图
    print("\n[Step 4] Placing screenshots...")
    temp1 = os.path.join(OUTPUT_DIR, "temp_kimi_1.png")
    temp2 = os.path.join(OUTPUT_DIR, "temp_kimi_2.png")

    place_screenshot(bg_path, SCREENSHOTS["s1"], layout["s1"], "S1", temp1)
    place_screenshot(temp1, SCREENSHOTS["s2"], layout["s2"], "S2", temp2)
    place_screenshot(temp2, SCREENSHOTS["s3"], layout["s3"], "S3",
                     os.path.join(OUTPUT_DIR, "final_p20_kimi.png"))

    print("\n" + "=" * 60)
    print("Done! Check final_p20_kimi.png")
    print("=" * 60)


if __name__ == "__main__":
    main()
