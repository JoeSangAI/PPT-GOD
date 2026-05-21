"""
端到端测试：用 MiniMax VLM 检测真实背景图的文字区域。
"""
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ENV", "dev")

from app.services.text_region_detector import detect_text_regions

BG_PATH = "./test_outputs/bg_scene_2.png"


def main():
    print("=" * 60)
    print("End-to-End Test: MiniMax VLM Text Detection")
    print("=" * 60)
    print(f"Image: {BG_PATH}")
    print("Calling MiniMax VLM... (this may take 10-30 seconds)")

    regions = detect_text_regions(BG_PATH)

    print(f"\nDetected {len(regions)} text regions:")
    for i, r in enumerate(regions, 1):
        print(f"  Region {i}: x={r['x']:.3f}, y={r['y']:.3f}, w={r['width']:.3f}, h={r['height']:.3f}")
        # 换算成像素
        px = int(r['x'] * 1792)
        py = int(r['y'] * 1024)
        pw = int(r['width'] * 1792)
        ph = int(r['height'] * 1024)
        print(f"           → pixels: ({px}, {py}, {pw}, {ph})")

    # 对比 right-card 预设位置
    right_card = {"x": 0.595, "y": 0.18, "width": 0.34, "height": 0.58}
    print(f"\nRight-card preset: x={right_card['x']:.3f}, y={right_card['y']:.3f}, w={right_card['width']:.3f}, h={right_card['height']:.3f}")

    from app.services.text_region_detector import _overlaps
    if _overlaps(right_card, regions):
        print("⚠️  WARNING: right-card preset OVERLAPS with detected text regions!")
        print("   The assembler should move the overlay to avoid text.")
    else:
        print("✅ right-card preset is SAFE (no text overlap).")

    print("=" * 60)


if __name__ == "__main__":
    main()
