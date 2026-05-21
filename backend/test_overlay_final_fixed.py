"""
修复版：正确缩放截图后再叠加。
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

screenshots = {
    "ref1": os.path.join(ASSETS_DIR, "2026.05_5_p020_3524386527.png"),
    "ref2": os.path.join(ASSETS_DIR, "2026.05_5_p020_63fbd7601f.png"),
}

right_card = (int(1792 * 0.595), int(1024 * 0.18), int(1792 * 0.34), int(1024 * 0.58))
left_card = (int(1792 * 0.065), int(1024 * 0.18), int(1792 * 0.36), int(1024 * 0.58))


def build_prompt(visual: dict | None) -> str:
    reservation = overlay_reservation_instruction(visual)
    parts = [
        "Create one polished widescreen landscape presentation slide.",
        "",
        "Visible Text (place strictly in safe zones, avoid reserved areas):",
        "- Headline: 消费趋势与时间窗口",
        "- Subhead: 市场洞察与策略分析", 
        "- Body: 82%消费者将成分安全性作为首要考量",
        "",
        "Style: Modern corporate, soft blue gradient background, professional typography.",
        "Visual: Text in upper portion, clean atmospheric background with subtle depth.",
    ]
    if reservation:
        parts.extend(["", "Exact Overlay Reservation:", reservation])
    return "\n".join(parts)


def simulate_overlay(bg_input, asset_path, box, label):
    if isinstance(bg_input, str):
        bg = Image.open(bg_input).convert("RGBA")
    else:
        bg = bg_input.copy()
    asset = Image.open(asset_path).convert("RGBA")
    left, top, width, height = box
    asset_w, asset_h = asset.size
    scale = min(width / asset_w, height / asset_h)
    pic_w, pic_h = int(asset_w * scale), int(asset_h * scale)
    pic_left = left + int((width - pic_w) / 2)
    pic_top = top + max(0, height - pic_h)
    
    # 正确缩放图片
    asset_resized = asset.resize((pic_w, pic_h), Image.Resampling.LANCZOS)
    
    # 画调试红框（preset 区域）
    overlay = Image.new("RGBA", bg.size, (0,0,0,0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle([left, top, left+width, top+height], outline=(255,0,0,180), width=3)
    bg = Image.alpha_composite(bg, overlay)
    
    # 叠加缩放后的截图
    bg.paste(asset_resized, (pic_left, pic_top), asset_resized)
    
    # 标注
    draw = ImageDraw.Draw(bg)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 20)
    except:
        font = ImageFont.load_default()
    info = f"{label}: ({pic_left},{pic_top},{pic_w},{pic_h})"
    bbox = draw.textbbox((0,0), info, font=font)
    tw = bbox[2]-bbox[0]
    draw.rectangle([10, 10, 20+tw, 42], fill=(0,0,0,180))
    draw.text((14, 14), info, fill=(255,255,255), font=font)
    return bg


def main():
    print("="*60)
    print("Fixed Test: Properly resize before paste")
    print("="*60)
    
    # 场景1: 1 overlay
    print("\n[Scene 1] 1 right-card")
    prompt = build_prompt({"overlay_layers": [{"asset_id": "ref1", "enabled": True, "preset": "right-card", "mode": "exact_cutout"}]})
    img = generate_slide_image(prompt, resolution="1K", aspect_ratio="16:9")
    bg_path = os.path.join(OUTPUT_DIR, "fixed_bg_1.png")
    img.save(bg_path, "PNG")
    result = simulate_overlay(bg_path, screenshots["ref1"], right_card, "R1")
    result.save(os.path.join(OUTPUT_DIR, "fixed_1_right.png"), "PNG")
    print("Saved: fixed_1_right.png")
    
    # 场景2: 2 overlays
    print("\n[Scene 2] left + right")
    prompt = build_prompt({"overlay_layers": [
        {"asset_id": "ref1", "enabled": True, "preset": "right-card", "mode": "exact_cutout"},
        {"asset_id": "ref2", "enabled": True, "preset": "left-card", "mode": "exact_cutout"},
    ]})
    img = generate_slide_image(prompt, resolution="1K", aspect_ratio="16:9")
    bg_path = os.path.join(OUTPUT_DIR, "fixed_bg_2.png")
    img.save(bg_path, "PNG")
    temp = simulate_overlay(bg_path, screenshots["ref1"], right_card, "R1")
    result = simulate_overlay(temp, screenshots["ref2"], left_card, "R2")
    result.save(os.path.join(OUTPUT_DIR, "fixed_2_lr.png"), "PNG")
    print("Saved: fixed_2_lr.png")
    
    print("\n" + "="*60)
    print("Done.")
    print("="*60)

if __name__ == "__main__":
    main()
