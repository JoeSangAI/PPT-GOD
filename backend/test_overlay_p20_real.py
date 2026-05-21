"""
用 P20 的真实截图素材做 end-to-end 测试：
1. 生成带预留区域的背景图（改进 prompt）
2. 用 exact_cutout 模式叠加真实截图
3. 验证配合效果
"""
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ENV", "dev")

from PIL import Image, ImageDraw, ImageFont
from app.services.image_generation import generate_slide_image
from app.services.overlay_layers import overlay_reservation_instruction

OUTPUT_DIR = "./test_outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)

ASSETS_DIR = "/Users/Joe_1/Desktop/Development/ppt-god/backend/uploads/78a21fde-1857-48ca-9047-8494612e26f8/pptx_assets"

# P20 的 3 张真实截图
screenshots = {
    "ref1": os.path.join(ASSETS_DIR, "2026.05_5_p020_3524386527.png"),  # 932x1134
    "ref2": os.path.join(ASSETS_DIR, "2026.05_5_p020_63fbd7601f.png"),   # 876x1136
    "ref3": os.path.join(ASSETS_DIR, "2026.05_5_p020_f7d45a97d7.png"),   # 1428x1170
}

# 预设区域（1792x1024）
right_card = (int(1792 * 0.595), int(1024 * 0.18), int(1792 * 0.34), int(1024 * 0.58))
left_card = (int(1792 * 0.065), int(1024 * 0.18), int(1792 * 0.36), int(1024 * 0.58))
bottom_band = (int(1792 * 0.12), int(1024 * 0.68), int(1792 * 0.76), int(1024 * 0.22))


def build_prompt(overlay_visual: dict | None) -> str:
    reservation = overlay_reservation_instruction(overlay_visual)
    parts = [
        "Create one polished widescreen landscape presentation slide. Keep visible text legible.",
        "",
        "Visible Text:",
        "- Headline: 消费趋势与时间窗口",
        "- Subhead: 市场洞察与策略分析",
        "- Body: 82%消费者将成分安全性作为首要考量，78%关注抑菌效果",
        "",
        "Style:",
        "Modern corporate presentation, clean minimalist design, soft gradient background in muted blue tones, professional typography.",
        "",
        "Visual:",
        "Arrange text in the upper portion with supporting visual areas on the left, right, and bottom. Use a soft atmospheric background with subtle depth.",
    ]
    if reservation:
        parts.extend(["", "Exact Overlay Reservation:", reservation])
    return "\n".join(parts)


def simulate_exact_cutout(bg_path: str, asset_path: str, output_path: str, box: tuple, label: str):
    bg = Image.open(bg_path).convert("RGBA")
    asset = Image.open(asset_path).convert("RGBA")

    left, top, width, height = box
    asset_w, asset_h = asset.size

    # contain + bottom valign（和 pptx_assembler 一致）
    scale = min(width / asset_w, height / asset_h)
    pic_w = int(asset_w * scale)
    pic_h = int(asset_h * scale)
    pic_left = left + int((width - pic_w) / 2)
    pic_top = top + max(0, height - pic_h)

    # 画预留区域红框（调试）
    overlay = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle([left, top, left + width, top + height], outline=(255, 0, 0, 200), width=3)
    draw.rectangle([left, top, left + width, top + height], fill=(255, 0, 0, 25))
    bg = Image.alpha_composite(bg, overlay)

    # 叠加素材
    bg.paste(asset, (pic_left, pic_top), asset)

    # 标注
    draw = ImageDraw.Draw(bg)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 20)
    except:
        font = ImageFont.load_default()
    info = f"{label}: Box({left},{top},{width},{height}) -> Pic({pic_left},{pic_top},{pic_w},{pic_h})"
    bbox = draw.textbbox((0, 0), info, font=font)
    tw = bbox[2] - bbox[0]
    draw.rectangle([10, 10, 20 + tw, 42], fill=(0, 0, 0, 200))
    draw.text((14, 14), info, fill=(255, 255, 255), font=font)

    bg.save(output_path, "PNG")
    print(f"Saved: {output_path}")


def run_scene(name: str, visual: dict, bg_name: str, overlays: list):
    print(f"\n[{name}]")
    prompt = build_prompt(visual)
    print(f"Prompt length: {len(prompt)}")
    print(f"Reservation: {overlay_reservation_instruction(visual)[:120]}...")

    img = generate_slide_image(prompt, resolution="1K", aspect_ratio="16:9")
    bg_path = os.path.join(OUTPUT_DIR, bg_name)
    img.save(bg_path, "PNG")
    print(f"BG saved: {bg_path}")

    result_path = os.path.join(OUTPUT_DIR, f"p20_{name.lower().replace(' ', '_')}.png")
    temp = bg_path
    for i, (asset_id, box, label) in enumerate(overlays):
        path = result_path if i == len(overlays) - 1 else os.path.join(OUTPUT_DIR, f"temp_{name}_{i}.png")
        simulate_exact_cutout(temp, screenshots[asset_id], path, box, label)
        temp = path


def main():
    print("=" * 60)
    print("P20 Real Screenshot End-to-End Test")
    print("=" * 60)

    # 场景1：1个 overlay (right-card)
    run_scene(
        "Scene 1 Right Card",
        {"overlay_layers": [{"asset_id": "ref1", "enabled": True, "preset": "right-card", "mode": "exact_cutout"}]},
        "p20_bg_1.png",
        [("ref1", right_card, "R1")],
    )

    # 场景2：2个 overlay (left + right)
    run_scene(
        "Scene 2 Left Right",
        {"overlay_layers": [
            {"asset_id": "ref1", "enabled": True, "preset": "right-card", "mode": "exact_cutout"},
            {"asset_id": "ref2", "enabled": True, "preset": "left-card", "mode": "exact_cutout"},
        ]},
        "p20_bg_2.png",
        [("ref1", right_card, "R1"), ("ref2", left_card, "R2")],
    )

    # 场景3：3个 overlay (left + right + bottom)
    run_scene(
        "Scene 3 All",
        {"overlay_layers": [
            {"asset_id": "ref1", "enabled": True, "preset": "right-card", "mode": "exact_cutout"},
            {"asset_id": "ref2", "enabled": True, "preset": "left-card", "mode": "exact_cutout"},
            {"asset_id": "ref3", "enabled": True, "preset": "bottom-band", "mode": "exact_cutout"},
        ]},
        "p20_bg_3.png",
        [("ref1", right_card, "R1"), ("ref2", left_card, "R2"), ("ref3", bottom_band, "R3")],
    )

    print("\n" + "=" * 60)
    print("All done.")
    print("=" * 60)


if __name__ == "__main__":
    main()
