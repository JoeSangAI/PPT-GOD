"""
使用真实风火轮素材测试精确粘贴（exact_cutout）效果。
从 pptx_assets 中裁剪出适合 overlay 的局部区域作为参考图。
"""
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ENV", "dev")

from PIL import Image, ImageDraw, ImageFont
from app.services.image_generation import generate_slide_image
from app.services.overlay_layers import overlay_reservation_instruction

ASSETS_DIR = "/Users/Joe_1/Desktop/Development/ppt-god/backend/uploads/78a21fde-1857-48ca-9047-8494612e26f8/pptx_assets"
OUTPUT_DIR = "./test_outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def crop_overlay_region(source_path: str, output_path: str, box: tuple):
    """从源图中裁剪出 overlay 区域作为参考图。"""
    img = Image.open(source_path)
    left, top, width, height = box
    cropped = img.crop((left, top, left + width, top + height))
    cropped.save(output_path, "PNG")
    print(f"Cropped reference: {output_path} ({cropped.size})")
    return output_path


def simulate_exact_cutout(background_path: str, asset_path: str, output_path: str, box: tuple):
    """模拟 exact_cutout 叠加（使用真实 pptx_assembler 逻辑）。"""
    bg = Image.open(background_path).convert("RGBA")
    asset = Image.open(asset_path).convert("RGBA")

    left, top, width, height = box
    asset_w, asset_h = asset.size

    # contained_picture_box 逻辑（contain + bottom valign）
    scale = min(width / asset_w, height / asset_h)
    pic_w = int(asset_w * scale)
    pic_h = int(asset_h * scale)
    pic_left = left + int((width - pic_w) / 2)
    pic_top = top + max(0, height - pic_h)

    # 绘制预留区域边框（调试用，红色半透明）
    overlay = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle([left, top, left + width, top + height], outline=(255, 0, 0, 180), width=3)
    bg = Image.alpha_composite(bg, overlay)

    # 叠加素材
    bg.paste(asset, (pic_left, pic_top), asset)

    # 在图片上标注坐标信息
    draw = ImageDraw.Draw(bg)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 20)
    except:
        font = ImageFont.load_default()
    info = f"Box: ({left},{top},{width},{height}) | Pic: ({pic_left},{pic_top},{pic_w},{pic_h})"
    draw.rectangle([10, 10, len(info)*10+20, 40], fill=(0,0,0,180))
    draw.text((15, 15), info, fill=(255,255,255), font=font)

    bg.save(output_path, "PNG")
    print(f"Overlay saved: {output_path}")


def build_test_prompt(base_prompt: str, overlay_visual: dict | None = None) -> str:
    reservation = overlay_reservation_instruction(overlay_visual)
    parts = [
        "Create one polished widescreen landscape presentation slide. Keep visible text legible.",
        "",
        "Visible Text:",
        "- Headline: Quarterly Business Review",
        "- Subhead: Q3 2024 Performance Summary",
        "- Body: Revenue grew 23% YoY with strong momentum in APAC region",
        "",
        "Style:",
        "Modern corporate presentation, clean minimalist design, soft gradient background in muted blue tones, professional typography.",
        "",
        "Visual:",
        base_prompt,
    ]
    if reservation:
        parts.extend([
            "",
            "Exact Overlay Reservation:",
            reservation,
        ])
    return "\n".join(parts)


def main():
    # 素材是 1792x1008，但 AI 生图是 1792x1024
    # 统一按 1792x1024 计算裁剪坐标（素材垂直方向会多裁剪 8px，可接受）
    right_card = (int(1792 * 0.595), int(1024 * 0.18), int(1792 * 0.34), int(1024 * 0.58))
    left_card = (int(1792 * 0.065), int(1024 * 0.18), int(1792 * 0.36), int(1024 * 0.58))
    bottom_band = (int(1792 * 0.12), int(1024 * 0.68), int(1792 * 0.76), int(1024 * 0.22))

    print("=" * 60)
    print("Real Asset Overlay Test")
    print("=" * 60)

    # 从真实素材裁剪参考图
    ref1 = crop_overlay_region(
        os.path.join(ASSETS_DIR, "2026.05_5_p001_f3c774ff83.png"),
        os.path.join(OUTPUT_DIR, "real_ref_right_card.png"),
        right_card
    )
    ref2 = crop_overlay_region(
        os.path.join(ASSETS_DIR, "2026.05_5_p003_4a03a17ddd.png"),
        os.path.join(OUTPUT_DIR, "real_ref_left_card.png"),
        left_card
    )
    ref3 = crop_overlay_region(
        os.path.join(ASSETS_DIR, "2026.05_5_p005_d63b235805.png"),
        os.path.join(OUTPUT_DIR, "real_ref_bottom_band.png"),
        bottom_band
    )

    base_visual = "Arrange text in the upper portion with supporting visual areas on the left, right, and bottom. Use a soft atmospheric background with subtle depth."

    # 场景1：1个 overlay（right-card）
    print("\n[Scene 1] 1 overlay layer (right-card)")
    visual_1 = {
        "overlay_layers": [
            {"asset_id": "ref1", "enabled": True, "preset": "right-card", "mode": "exact_cutout"}
        ]
    }
    prompt_1 = build_test_prompt(base_visual, visual_1)
    print(f"Prompt length: {len(prompt_1)}")
    print(f"Reservation: {overlay_reservation_instruction(visual_1)}")
    img_1 = generate_slide_image(prompt_1, resolution="1K", aspect_ratio="16:9")
    bg_path_1 = os.path.join(OUTPUT_DIR, "bg_scene_1.png")
    img_1.save(bg_path_1, "PNG")
    simulate_exact_cutout(bg_path_1, ref1, os.path.join(OUTPUT_DIR, "final_real_1_overlay.png"), right_card)

    # 场景2：2个 overlay（right-card + left-card）
    print("\n[Scene 2] 2 overlay layers")
    visual_2 = {
        "overlay_layers": [
            {"asset_id": "ref1", "enabled": True, "preset": "right-card", "mode": "exact_cutout"},
            {"asset_id": "ref2", "enabled": True, "preset": "left-card", "mode": "exact_cutout"},
        ]
    }
    prompt_2 = build_test_prompt(base_visual, visual_2)
    print(f"Prompt length: {len(prompt_2)}")
    img_2 = generate_slide_image(prompt_2, resolution="1K", aspect_ratio="16:9")
    bg_path_2 = os.path.join(OUTPUT_DIR, "bg_scene_2.png")
    img_2.save(bg_path_2, "PNG")
    temp = os.path.join(OUTPUT_DIR, "temp_scene_2.png")
    simulate_exact_cutout(bg_path_2, ref1, temp, right_card)
    simulate_exact_cutout(temp, ref2, os.path.join(OUTPUT_DIR, "final_real_2_overlays.png"), left_card)

    # 场景3：3个 overlay
    print("\n[Scene 3] 3 overlay layers")
    visual_3 = {
        "overlay_layers": [
            {"asset_id": "ref1", "enabled": True, "preset": "right-card", "mode": "exact_cutout"},
            {"asset_id": "ref2", "enabled": True, "preset": "left-card", "mode": "exact_cutout"},
            {"asset_id": "ref3", "enabled": True, "preset": "bottom-band", "mode": "exact_cutout"},
        ]
    }
    prompt_3 = build_test_prompt(base_visual, visual_3)
    print(f"Prompt length: {len(prompt_3)}")
    img_3 = generate_slide_image(prompt_3, resolution="1K", aspect_ratio="16:9")
    bg_path_3 = os.path.join(OUTPUT_DIR, "bg_scene_3.png")
    img_3.save(bg_path_3, "PNG")
    temp1 = os.path.join(OUTPUT_DIR, "temp_scene_3_1.png")
    simulate_exact_cutout(bg_path_3, ref1, temp1, right_card)
    temp2 = os.path.join(OUTPUT_DIR, "temp_scene_3_2.png")
    simulate_exact_cutout(temp1, ref2, temp2, left_card)
    simulate_exact_cutout(temp2, ref3, os.path.join(OUTPUT_DIR, "final_real_3_overlays.png"), bottom_band)

    print("\n" + "=" * 60)
    print("All tests completed.")
    print("=" * 60)


if __name__ == "__main__":
    main()
