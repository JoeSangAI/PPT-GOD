"""
用 P20 真实案例测试：标题"案例演示" + 3 张截图。
"""
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ENV", "dev")

from PIL import Image, ImageDraw, ImageFont
from app.services.image_generation import generate_slide_image
from app.services.overlay_layers import overlay_reservation_instruction, contained_picture_box
from app.services.text_region_detector import detect_text_regions, compute_safe_overlay_box

OUTPUT_DIR = "./test_outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)

ASSETS_DIR = "/Users/Joe_1/Desktop/Development/ppt-god/backend/uploads/78a21fde-1857-48ca-9047-8494612e26f8/pptx_assets"

# P20 的 3 张截图
SCREENSHOTS = {
    "s1": os.path.join(ASSETS_DIR, "2026.05_5_p020_3524386527.png"),
    "s2": os.path.join(ASSETS_DIR, "2026.05_5_p020_63fbd7601f.png"),
    "s3": os.path.join(ASSETS_DIR, "2026.05_5_p020_f7d45a97d7.png"),
}

# 预设位置
PRESETS = {
    "left-card": (int(1792 * 0.065), int(1024 * 0.18), int(1792 * 0.36), int(1024 * 0.58)),
    "right-card": (int(1792 * 0.595), int(1024 * 0.18), int(1792 * 0.34), int(1024 * 0.58)),
    "top-right-small": (int(1792 * 0.72), int(1024 * 0.08), int(1792 * 0.20), int(1024 * 0.18)),
}


def simulate_overlay_safe(bg_path, asset_path, preset_name, text_regions, label, output_path):
    """使用安全位置放置截图。"""
    bg = Image.open(bg_path).convert("RGBA")
    asset = Image.open(asset_path).convert("RGBA")

    left, top, width, height = PRESETS[preset_name]

    # 文字避让
    safe_left, safe_top, safe_width, safe_height = compute_safe_overlay_box(
        left, top, width, height, text_regions, 1792, 1024
    )

    # contain 缩放
    asset_w, asset_h = asset.size
    scale = min(safe_width / asset_w, safe_height / asset_h)
    pic_w = int(asset_w * scale)
    pic_h = int(asset_h * scale)
    pic_left = safe_left + int((safe_width - pic_w) / 2)
    pic_top = safe_top + max(0, safe_height - pic_h)

    # 绘制原始预设（红色虚线）和安全区域（蓝色实线）
    overlay = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # 红色虚线 - 原始预设
    for i in range(0, max(width, height), 10):
        if i + 5 <= width:
            draw.line([(left + i, top), (left + i + 5, top)], fill=(255, 0, 0, 180), width=2)
            draw.line([(left + i, top + height), (left + i + 5, top + height)], fill=(255, 0, 0, 180), width=2)
        if i + 5 <= height:
            draw.line([(left, top + i), (left, top + i + 5)], fill=(255, 0, 0, 180), width=2)
            draw.line([(left + width, top + i), (left + width, top + i + 5)], fill=(255, 0, 0, 180), width=2)

    # 蓝色实线 - 安全区域
    draw.rectangle(
        [safe_left, safe_top, safe_left + safe_width, safe_top + safe_height],
        outline=(0, 100, 255, 200), width=3,
    )

    bg = Image.alpha_composite(bg, overlay)

    # 贴图
    asset_resized = asset.resize((pic_w, pic_h), Image.Resampling.LANCZOS)
    bg.paste(asset_resized, (pic_left, pic_top), asset_resized)

    # 标注
    draw = ImageDraw.Draw(bg)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
    except:
        font = ImageFont.load_default()
    info = f"{label}: safe=({safe_left},{safe_top},{safe_width},{safe_height})"
    bbox = draw.textbbox((0, 0), info, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.rectangle([10, 10, 20 + tw, 20 + th], fill=(0, 0, 0, 180))
    draw.text((14, 14), info, fill=(255, 255, 255), font=font)

    bg.save(output_path, "PNG")
    print(f"Saved: {output_path}")
    return output_path


def main():
    print("=" * 60)
    print("P20 Real Case Test: 案例演示 + 3 screenshots")
    print("=" * 60)

    # 构建 prompt
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
        "- Subhead: (empty)\n"
        "- Body: (empty)\n\n"
        "Style: Modern corporate, clean minimalist design, soft gradient background in muted blue tones, professional typography.\n\n"
        "Visual: Clean atmospheric background with subtle depth.\n\n"
        f"Exact Overlay Reservation:\n{overlay_reservation_instruction(visual)}"
    )

    # Step 1: 生图
    print("\n[Step 1] Generating background...")
    img = generate_slide_image(prompt, resolution="1K", aspect_ratio="16:9")
    bg_path = os.path.join(OUTPUT_DIR, "bg_p20_case_demo.png")
    img.save(bg_path, "PNG")
    print(f"Background: {bg_path}")

    # Step 2: 检测文字
    print("\n[Step 2] Detecting text regions...")
    text_regions = detect_text_regions(bg_path)
    print(f"Detected {len(text_regions)} regions:")
    for i, r in enumerate(text_regions, 1):
        px = int(r['x'] * 1792)
        py = int(r['y'] * 1024)
        pw = int(r['width'] * 1792)
        ph = int(r['height'] * 1024)
        print(f"  Region {i}: ({px},{py},{pw},{ph})")

    # Step 3: 放置 3 张截图
    print("\n[Step 3] Placing 3 screenshots with avoidance...")
    temp1 = os.path.join(OUTPUT_DIR, "temp_p20_1.png")
    temp2 = os.path.join(OUTPUT_DIR, "temp_p20_2.png")

    simulate_overlay_safe(bg_path, SCREENSHOTS["s1"], "left-card", text_regions, "S1-left", temp1)
    simulate_overlay_safe(temp1, SCREENSHOTS["s2"], "right-card", text_regions, "S2-right", temp2)
    simulate_overlay_safe(temp2, SCREENSHOTS["s3"], "top-right-small", text_regions, "S3-top-right",
                          os.path.join(OUTPUT_DIR, "final_p20_case_demo.png"))

    print("\n" + "=" * 60)
    print("Done! Check final_p20_case_demo.png")
    print("=" * 60)


if __name__ == "__main__":
    main()
