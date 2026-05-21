"""
用 P20 的真实截图素材测试 exact_cutout 叠加效果。
"""
import os
from PIL import Image, ImageDraw, ImageFont

OUTPUT_DIR = "./test_outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)

ASSETS_DIR = "/Users/Joe_1/Desktop/Development/ppt-god/backend/uploads/78a21fde-1857-48ca-9047-8494612e26f8/pptx_assets"

# P20 的 3 张真实截图
screenshots = [
    ("ref1", os.path.join(ASSETS_DIR, "2026.05_5_p020_3524386527.png")),
    ("ref2", os.path.join(ASSETS_DIR, "2026.05_5_p020_63fbd7601f.png")),
    ("ref3", os.path.join(ASSETS_DIR, "2026.05_5_p020_f7d45a97d7.png")),
]

# 预设区域（基于 1792x1024）
right_card = (int(1792 * 0.595), int(1024 * 0.18), int(1792 * 0.34), int(1024 * 0.58))
left_card = (int(1792 * 0.065), int(1024 * 0.18), int(1792 * 0.36), int(1024 * 0.58))
bottom_band = (int(1792 * 0.12), int(1024 * 0.68), int(1792 * 0.76), int(1024 * 0.22))

presets = {
    "right-card": right_card,
    "left-card": left_card,
    "bottom-band": bottom_band,
}


def load_or_create_bg(path, size=(1792, 1024)):
    if os.path.exists(path):
        img = Image.open(path).convert("RGBA")
        if img.size != size:
            img = img.resize(size, Image.LANCZOS)
        return img
    # 创建渐变背景
    bg = Image.new("RGBA", size, (30, 50, 80, 255))
    draw = ImageDraw.Draw(bg)
    for y in range(size[1]):
        r = int(30 + (60 - 30) * y / size[1])
        g = int(50 + (100 - 50) * y / size[1])
        b = int(80 + (140 - 80) * y / size[1])
        draw.line([(0, y), (size[0], y)], fill=(r, g, b, 255))
    return bg


def simulate_overlay(bg, asset_path, box, label=""):
    asset = Image.open(asset_path).convert("RGBA")
    left, top, width, height = box
    asset_w, asset_h = asset.size

    # contain 模式
    scale = min(width / asset_w, height / asset_h)
    pic_w = int(asset_w * scale)
    pic_h = int(asset_h * scale)
    pic_left = left + int((width - pic_w) / 2)
    pic_top = top + max(0, height - pic_h)

    # 绘制预留区域（红色半透明边框）
    overlay = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle([left, top, left + width, top + height], outline=(255, 0, 0, 200), width=4)
    # 填充半透明红色
    draw.rectangle([left, top, left + width, top + height], fill=(255, 0, 0, 30))
    bg = Image.alpha_composite(bg, overlay)

    # 叠加素材
    bg.paste(asset, (pic_left, pic_top), asset)

    # 标注信息
    draw = ImageDraw.Draw(bg)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 22)
        small_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
    except:
        font = ImageFont.load_default()
        small_font = font

    info = f"{label}: Box({left},{top},{width},{height}) -> Pic({pic_left},{pic_top},{pic_w},{pic_h})"
    bbox = draw.textbbox((0, 0), info, font=font)
    text_w = bbox[2] - bbox[0]
    draw.rectangle([10, 10, 20 + text_w, 45], fill=(0, 0, 0, 200))
    draw.text((15, 14), info, fill=(255, 255, 255), font=font)

    return bg


def main():
    # 使用之前生成的背景图，或创建渐变背景
    bg_path_1 = os.path.join(OUTPUT_DIR, "bg_scene_1.png")
    bg_path_2 = os.path.join(OUTPUT_DIR, "bg_scene_2.png")

    print("=" * 60)
    print("Real Screenshot Overlay Test (P20 assets)")
    print("=" * 60)

    # 场景1：1个 overlay (right-card)
    print("\n[Scene 1] 1 overlay: right-card")
    bg = load_or_create_bg(bg_path_1)
    result = simulate_overlay(bg, screenshots[0][1], right_card, "R1")
    path = os.path.join(OUTPUT_DIR, "test_real_1_overlay.png")
    result.save(path, "PNG")
    print(f"Saved: {path}")

    # 场景2：2个 overlay (left + right)
    print("\n[Scene 2] 2 overlays: left-card + right-card")
    bg = load_or_create_bg(bg_path_2)
    temp = simulate_overlay(bg, screenshots[0][1], right_card, "R1")
    result = simulate_overlay(temp, screenshots[1][1], left_card, "R2")
    path = os.path.join(OUTPUT_DIR, "test_real_2_overlays.png")
    result.save(path, "PNG")
    print(f"Saved: {path}")

    # 场景3：3个 overlay (left + right + bottom)
    print("\n[Scene 3] 3 overlays: left + right + bottom-band")
    bg = load_or_create_bg(bg_path_2)
    temp1 = simulate_overlay(bg, screenshots[0][1], right_card, "R1")
    temp2 = simulate_overlay(temp1, screenshots[1][1], left_card, "R2")
    result = simulate_overlay(temp2, screenshots[2][1], bottom_band, "R3")
    path = os.path.join(OUTPUT_DIR, "test_real_3_overlays.png")
    result.save(path, "PNG")
    print(f"Saved: {path}")

    print("\n" + "=" * 60)
    print("Done. Check test_outputs/ for results.")
    print("=" * 60)


if __name__ == "__main__":
    main()
