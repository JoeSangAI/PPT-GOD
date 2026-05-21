"""
Overlay prompt 效果测试脚本。
生成3张测试图：0/1/2个 overlay layers 的场景。
每张图验证 overlay_reservation_instruction 的效果。
"""
import os
import sys

# 确保在项目根目录运行
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("ENV", "dev")

from PIL import Image
from app.services.image_generation import generate_slide_image
from app.services.overlay_layers import overlay_reservation_instruction


def build_test_prompt(base_prompt: str, overlay_visual: dict | None = None) -> str:
    """构建带 overlay reservation 的测试 prompt。"""
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


def save_test_image(img: Image.Image, name: str):
    output_dir = "./test_outputs"
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"{name}.png")
    img.save(path, "PNG")
    print(f"Saved: {path}")
    return path


def main():
    print("=" * 60)
    print("Overlay Prompt Test")
    print("=" * 60)

    base_visual = "Arrange text on the left side with a supporting visual area on the right. Use a soft atmospheric background with subtle depth."

    # 场景0：无 overlay（对照组）
    print("\n[Scene 0] No overlay (control)")
    prompt_0 = build_test_prompt(base_visual, None)
    print(f"Prompt length: {len(prompt_0)}")
    img_0 = generate_slide_image(prompt_0, resolution="1K", aspect_ratio="16:9")
    save_test_image(img_0, "scene_0_no_overlay")

    # 场景1：1个 overlay（right-card）
    print("\n[Scene 1] 1 overlay layer (right-card)")
    visual_1 = {
        "overlay_layers": [
            {"asset_id": "test1", "enabled": True, "preset": "right-card", "mode": "exact_cutout"}
        ]
    }
    prompt_1 = build_test_prompt(base_visual, visual_1)
    print(f"Prompt length: {len(prompt_1)}")
    print(f"Reservation text: {overlay_reservation_instruction(visual_1)}")
    img_1 = generate_slide_image(prompt_1, resolution="1K", aspect_ratio="16:9")
    save_test_image(img_1, "scene_1_right_card")

    # 场景2：2个 overlay（right-card + left-card）
    print("\n[Scene 2] 2 overlay layers (right-card + left-card)")
    visual_2 = {
        "overlay_layers": [
            {"asset_id": "test1", "enabled": True, "preset": "right-card", "mode": "exact_cutout"},
            {"asset_id": "test2", "enabled": True, "preset": "left-card", "mode": "exact_cutout"},
        ]
    }
    prompt_2 = build_test_prompt(base_visual, visual_2)
    print(f"Prompt length: {len(prompt_2)}")
    print(f"Reservation text: {overlay_reservation_instruction(visual_2)}")
    img_2 = generate_slide_image(prompt_2, resolution="1K", aspect_ratio="16:9")
    save_test_image(img_2, "scene_2_two_cards")

    print("\n" + "=" * 60)
    print("All tests completed. Check ./test_outputs/ for results.")
    print("=" * 60)


if __name__ == "__main__":
    main()
