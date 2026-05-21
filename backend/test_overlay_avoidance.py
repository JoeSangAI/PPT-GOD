"""
新版精确粘贴测试：先生图 → VLM 检测文字 → 计算安全位置 → 贴图。
"""
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ENV", "dev")

from PIL import Image, ImageDraw, ImageFont
from app.services.image_generation import generate_slide_image
from app.services.overlay_layers import overlay_reservation_instruction
from app.services.text_region_detector import detect_text_regions, compute_safe_overlay_box

OUTPUT_DIR = "./test_outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)

ASSETS_DIR = "/Users/Joe_1/Desktop/Development/ppt-god/backend/uploads/78a21fde-1857-48ca-9047-8494612e26f8/pptx_assets"


def simulate_overlay_with_avoidance(
    bg_path: str,
    asset_path: str,
    output_path: str,
    preset_name: str,
    text_regions: list,
    label: str,
):
    """模拟 exact_cutout，但使用安全位置（文字避让）。"""
    bg = Image.open(bg_path).convert("RGBA")
    asset = Image.open(asset_path).convert("RGBA")

    # 原始预设位置（像素）
    presets = {
        "right-card": (int(1792 * 0.595), int(1024 * 0.18), int(1792 * 0.34), int(1024 * 0.58)),
        "left-card": (int(1792 * 0.065), int(1024 * 0.18), int(1792 * 0.36), int(1024 * 0.58)),
        "bottom-band": (int(1792 * 0.12), int(1024 * 0.68), int(1792 * 0.76), int(1024 * 0.22)),
    }
    left, top, width, height = presets[preset_name]

    # 文字避让：计算安全位置
    safe_left, safe_top, safe_width, safe_height = compute_safe_overlay_box(
        left, top, width, height,
        text_regions,
        1792, 1024,
    )

    # 安全区域内 contain 缩放
    asset_w, asset_h = asset.size
    scale = min(safe_width / asset_w, safe_height / asset_h)
    pic_w = int(asset_w * scale)
    pic_h = int(asset_h * scale)
    pic_left = safe_left + int((safe_width - pic_w) / 2)
    pic_top = safe_top + max(0, safe_height - pic_h)

    # 绘制安全区域（蓝色半透明）和原始预设（红色虚线）
    overlay = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    # 原始预设（红色虚线）
    for i in range(0, max(width, height), 10):
        if i + 5 <= width:
            draw.line([(left + i, top), (left + i + 5, top)], fill=(255, 0, 0, 180), width=2)
            draw.line([(left + i, top + height), (left + i + 5, top + height)], fill=(255, 0, 0, 180), width=2)
        if i + 5 <= height:
            draw.line([(left, top + i), (left, top + i + 5)], fill=(255, 0, 0, 180), width=2)
            draw.line([(left + width, top + i), (left + width, top + i + 5)], fill=(255, 0, 0, 180), width=2)
    # 安全区域（蓝色实线）
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
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 18)
    except:
        font = ImageFont.load_default()
    info = f"{label}: Safe=({safe_left},{safe_top},{safe_width},{safe_height})"
    bbox = draw.textbbox((0, 0), info, font=font)
    tw = bbox[2] - bbox[0]
    draw.rectangle([10, 10, 20 + tw, 38], fill=(0, 0, 0, 180))
    draw.text((14, 14), info, fill=(255, 255, 255), font=font)

    bg.save(output_path, "PNG")
    print(f"Saved: {output_path}")
    return output_path


def build_prompt(visual: dict | None) -> str:
    reservation = overlay_reservation_instruction(visual)
    parts = [
        "Create one polished widescreen landscape presentation slide.",
        "Visible Text:",
        "- Headline: 消费趋势与时间窗口",
        "- Subhead: 市场洞察与策略分析",
        "- Body: 82%消费者将成分安全性作为首要考量",
        "Style: Modern corporate, soft blue gradient, professional typography.",
        "Visual: Clean atmospheric background with subtle depth.",
    ]
    if reservation:
        parts.extend(["", "Exact Overlay Reservation:", reservation])
    return "\n".join(parts)


def main():
    print("=" * 60)
    print("New Test: Text-Aware Overlay Placement")
    print("=" * 60)

    # 场景：左右两个 overlay
    visual = {
        "overlay_layers": [
            {"asset_id": "ref1", "enabled": True, "preset": "right-card", "mode": "exact_cutout"},
            {"asset_id": "ref2", "enabled": True, "preset": "left-card", "mode": "exact_cutout"},
        ]
    }
    prompt = build_prompt(visual)
    print("\n[Step 1] Generating background image...")
    img = generate_slide_image(prompt, resolution="1K", aspect_ratio="16:9")
    bg_path = os.path.join(OUTPUT_DIR, "bg_avoidance_test.png")
    img.save(bg_path, "PNG")
    print(f"Background saved: {bg_path}")

    print("\n[Step 2] Detecting text regions with MiniMax VLM...")
    text_regions = detect_text_regions(bg_path)
    print(f"Detected {len(text_regions)} text regions:")
    for i, r in enumerate(text_regions, 1):
        px = int(r['x'] * 1792)
        py = int(r['y'] * 1024)
        pw = int(r['width'] * 1792)
        ph = int(r['height'] * 1024)
        print(f"  Region {i}: ({px},{py},{pw},{ph})")

    # 素材路径
    ref1 = os.path.join(ASSETS_DIR, "2026.05_5_p001_f3c774ff83.png")
    ref2 = os.path.join(ASSETS_DIR, "2026.05_5_p003_4a03a17ddd.png")

    print("\n[Step 3] Placing overlays with text avoidance...")
    temp = os.path.join(OUTPUT_DIR, "temp_avoid_1.png")
    simulate_overlay_with_avoidance(bg_path, ref1, temp, "right-card", text_regions, "R")
    simulate_overlay_with_avoidance(temp, ref2, os.path.join(OUTPUT_DIR, "final_avoidance.png"), "left-card", text_regions, "L")

    print("\n" + "=" * 60)
    print("Done. Check final_avoidance.png")
    print("Red dashed = original preset, Blue solid = safe position")
    print("=" * 60)


if __name__ == "__main__":
    main()
