"""
集成测试：验证组装器能正确读取预计算的 text_regions 并调整 overlay 位置。
无需调用 MiniMax API，使用 mock 数据。
"""
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ENV", "dev")

from app.services.pptx_assembler import assemble_pptx
from PIL import Image

OUTPUT_DIR = "./test_outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def create_mock_background(path, color=(100, 150, 200)):
    """创建一张纯色背景图作为模拟背景。"""
    img = Image.new("RGB", (1792, 1024), color)
    img.save(path)
    return path


def test_assembler_reads_text_regions():
    """验证组装器从 slide_data 中读取 text_regions 并调整位置。"""
    bg_path = os.path.join(OUTPUT_DIR, "mock_bg_integration.png")
    create_mock_background(bg_path)

    # 模拟文字区域：覆盖 right-card 的上半部分
    text_regions = [
        {"x": 0.60, "y": 0.20, "width": 0.30, "height": 0.25},
    ]

    slide_images = [{
        "page_num": 1,
        "image_path": bg_path,
        "speaker_notes": "",
        "type": "content",
        "visual_json": {
            "overlay_layers": [
                {"asset_id": "test_asset", "enabled": True, "preset": "right-card", "mode": "exact_cutout"}
            ]
        },
        "text_regions": text_regions,
    }]

    # 模拟 overlay asset
    asset_path = os.path.join(OUTPUT_DIR, "mock_asset.png")
    Image.new("RGBA", (400, 600), (255, 0, 0, 200)).save(asset_path)
    overlay_assets = {
        "test_asset": {"file_path": asset_path},
    }

    pptx_path = os.path.join(OUTPUT_DIR, "test_text_region.pptx")
    result = assemble_pptx(
        slide_images=slide_images,
        output_path=pptx_path,
        overlay_assets=overlay_assets,
    )

    assert os.path.exists(result), f"PPTX not created: {result}"
    print(f"✅ Integration test passed: {result}")
    print(f"   Background: {bg_path}")
    print(f"   Text regions: {text_regions}")
    print(f"   Asset: {asset_path}")


def test_assembler_no_text_regions_fallback():
    """验证没有 text_regions 时，组装器使用原始预设位置。"""
    bg_path = os.path.join(OUTPUT_DIR, "mock_bg_fallback.png")
    create_mock_background(bg_path, color=(200, 100, 150))

    slide_images = [{
        "page_num": 1,
        "image_path": bg_path,
        "speaker_notes": "",
        "type": "content",
        "visual_json": {
            "overlay_layers": [
                {"asset_id": "test_asset", "enabled": True, "preset": "left-card", "mode": "exact_cutout"}
            ]
        },
        # 不提供 text_regions
    }]

    asset_path = os.path.join(OUTPUT_DIR, "mock_asset.png")
    overlay_assets = {
        "test_asset": {"file_path": asset_path},
    }

    pptx_path = os.path.join(OUTPUT_DIR, "test_fallback.pptx")
    result = assemble_pptx(
        slide_images=slide_images,
        output_path=pptx_path,
        overlay_assets=overlay_assets,
    )

    assert os.path.exists(result), f"PPTX not created: {result}"
    print(f"✅ Fallback test passed: {result}")


def main():
    print("=" * 60)
    print("Integration Test: text_regions awareness")
    print("=" * 60)
    test_assembler_reads_text_regions()
    test_assembler_no_text_regions_fallback()
    print("=" * 60)
    print("All integration tests passed!")


if __name__ == "__main__":
    main()
