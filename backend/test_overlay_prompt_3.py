"""
Overlay prompt 效果追加测试：3个 overlay layers 的场景。
"""
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ENV", "dev")

from PIL import Image
from app.services.image_generation import generate_slide_image
from app.services.overlay_layers import overlay_reservation_instruction


def main():
    base_visual = "Arrange text in the upper portion with supporting visual areas on the left, right, and bottom. Use a soft atmospheric background with subtle depth."

    print("[Scene 3] 3 overlay layers (right-card + left-card + bottom-band)")
    visual_3 = {
        "overlay_layers": [
            {"asset_id": "test1", "enabled": True, "preset": "right-card", "mode": "exact_cutout"},
            {"asset_id": "test2", "enabled": True, "preset": "left-card", "mode": "exact_cutout"},
            {"asset_id": "test3", "enabled": True, "preset": "bottom-band", "mode": "exact_cutout"},
        ]
    }

    prompt_parts = [
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
        base_visual,
        "",
        "Exact Overlay Reservation:",
        overlay_reservation_instruction(visual_3),
    ]
    prompt_3 = "\n".join(prompt_parts)
    print(f"Prompt length: {len(prompt_3)}")
    print(f"Reservation: {overlay_reservation_instruction(visual_3)}")

    img_3 = generate_slide_image(prompt_3, resolution="1K", aspect_ratio="16:9")

    output_dir = "./test_outputs"
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "scene_3_three_overlays.png")
    img_3.save(path, "PNG")
    print(f"Saved: {path}")


if __name__ == "__main__":
    main()
