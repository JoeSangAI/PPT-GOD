"""
验证无框叠加的实际效果：把参考图叠加到生成的背景上。
"""
import os
from PIL import Image, ImageDraw, ImageFont

# 创建模拟参考图（截图风格）
def create_mock_screenshot(path: str, text: str):
    """创建一张模拟截图（白色背景 + 文字 + 灰色边框）。"""
    img = Image.new("RGBA", (600, 400), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)
    # 灰色边框模拟截图
    draw.rectangle([2, 2, 597, 397], outline=(200, 200, 200), width=2)
    # 文字
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 32)
    except:
        font = ImageFont.load_default()
    draw.text((40, 160), text, fill=(50, 50, 50), font=font)
    # 底部模拟工具栏
    draw.rectangle([0, 360, 600, 400], fill=(240, 240, 240))
    draw.text((20, 370), "Screenshot.png", fill=(100, 100, 100), font=font)
    img.save(path, "PNG")
    return path


def overlay_exact_cutout(background_path: str, asset_path: str, output_path: str, box: tuple):
    """
    模拟 exact_cutout 叠加：把参考图按 contain 模式放入指定区域。
    box = (left, top, width, height) in pixels (based on 1792x1024)
    """
    bg = Image.open(background_path).convert("RGBA")
    asset = Image.open(asset_path).convert("RGBA")

    left, top, width, height = box
    asset_w, asset_h = asset.size

    # contain 模式：保持比例，完整显示
    scale = min(width / asset_w, height / asset_h)
    pic_w = int(asset_w * scale)
    pic_h = int(asset_h * scale)
    pic_left = left + int((width - pic_w) / 2)
    pic_top = top + max(0, height - pic_h)  # bottom valign

    # 直接叠加（无框）
    bg.paste(asset, (pic_left, pic_top), asset)
    bg.save(output_path, "PNG")
    print(f"Overlay saved: {output_path}")


def main():
    test_dir = "./test_outputs"
    os.makedirs(test_dir, exist_ok=True)

    # 创建模拟截图
    screenshot_path = os.path.join(test_dir, "mock_screenshot.png")
    create_mock_screenshot(screenshot_path, "Q3 Revenue:\n$12.4M (+23%)")

    # 背景图路径
    bg_1 = os.path.join(test_dir, "scene_1_right_card.png")
    bg_2 = os.path.join(test_dir, "scene_2_two_cards.png")
    bg_3 = os.path.join(test_dir, "scene_3_three_overlays.png")

    # right-card preset: x=0.595, y=0.18, w=0.34, h=0.58
    # on 1792x1024 background
    right_card = (int(1792 * 0.595), int(1024 * 0.18), int(1792 * 0.34), int(1024 * 0.58))
    left_card = (int(1792 * 0.065), int(1024 * 0.18), int(1792 * 0.36), int(1024 * 0.58))
    bottom_band = (int(1792 * 0.12), int(1024 * 0.68), int(1792 * 0.76), int(1024 * 0.22))

    # 场景1：1个 overlay
    if os.path.exists(bg_1):
        overlay_exact_cutout(bg_1, screenshot_path, os.path.join(test_dir, "final_1_overlay.png"), right_card)

    # 场景2：2个 overlay
    if os.path.exists(bg_2):
        # 第一张截图放右侧
        temp = os.path.join(test_dir, "temp_bg.png")
        overlay_exact_cutout(bg_2, screenshot_path, temp, right_card)
        # 第二张截图（不同内容）放左侧
        screenshot2 = os.path.join(test_dir, "mock_screenshot_2.png")
        create_mock_screenshot(screenshot2, "User Growth:\n+45K MAU")
        overlay_exact_cutout(temp, screenshot2, os.path.join(test_dir, "final_2_overlays.png"), left_card)

    # 场景3：3个 overlay
    if os.path.exists(bg_3):
        temp1 = os.path.join(test_dir, "temp_bg3_1.png")
        overlay_exact_cutout(bg_3, screenshot_path, temp1, right_card)
        temp2 = os.path.join(test_dir, "temp_bg3_2.png")
        screenshot3 = os.path.join(test_dir, "mock_screenshot_3.png")
        create_mock_screenshot(screenshot3, "Market Share:\n18% → 24%")
        overlay_exact_cutout(temp1, screenshot3, temp2, left_card)
        overlay_exact_cutout(temp2, screenshot_path, os.path.join(test_dir, "final_3_overlays.png"), bottom_band)

    print("\nAll overlay assembly tests completed.")


if __name__ == "__main__":
    main()
