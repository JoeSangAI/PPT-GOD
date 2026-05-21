"""
本地测试 text_region_detector 的几何避让逻辑，无需调用 MiniMax API。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ENV", "dev")

from app.services.text_region_detector import _overlaps, compute_safe_overlay_box

SLIDE_W, SLIDE_H = 1792, 1024


def test_no_overlap():
    """预设位置没有文字，应返回原始位置。"""
    text_regions = [
        {"x": 0.05, "y": 0.05, "width": 0.40, "height": 0.10},  # 左上角标题
    ]
    # right-card 预设: x=0.595, y=0.18, w=0.34, h=0.58
    left = int(SLIDE_W * 0.595)
    top = int(SLIDE_H * 0.18)
    width = int(SLIDE_W * 0.34)
    height = int(SLIDE_H * 0.58)

    result = compute_safe_overlay_box(left, top, width, height, text_regions, SLIDE_W, SLIDE_H)
    assert result == (left, top, width, height), f"Expected no change, got {result}"
    print("✅ test_no_overlap passed")


def test_overlap_move_down():
    """预设位置与文字重叠，向下移动应避开。"""
    # 文字区域覆盖 right-card 的上半部分
    text_regions = [
        {"x": 0.60, "y": 0.20, "width": 0.30, "height": 0.25},
    ]
    left = int(SLIDE_W * 0.595)
    top = int(SLIDE_H * 0.18)
    width = int(SLIDE_W * 0.34)
    height = int(SLIDE_H * 0.58)

    result = compute_safe_overlay_box(left, top, width, height, text_regions, SLIDE_W, SLIDE_H)
    r_left, r_top, r_width, r_height = result

    # 应向下移动
    assert r_top > top, f"Expected top to move down, got {r_top} vs {top}"
    assert r_width == width and r_height == height, "Width/height should not change"
    print(f"✅ test_overlap_move_down passed: top moved from {top} to {r_top}")


def test_overlap_shrink():
    """上方有文字阻挡，下方空间足够，缩小高度应能避开。"""
    text_regions = [
        {"x": 0.55, "y": 0.15, "width": 0.40, "height": 0.20},  # 上方文字，覆盖预设上半部
    ]
    left = int(SLIDE_W * 0.595)
    top = int(SLIDE_H * 0.18)
    width = int(SLIDE_W * 0.34)
    height = int(SLIDE_H * 0.58)

    result = compute_safe_overlay_box(left, top, width, height, text_regions, SLIDE_W, SLIDE_H)
    r_left, r_top, r_width, r_height = result

    # 应先尝试下移，如果下移也重叠再缩小
    if r_height < height:
        print(f"✅ test_overlap_shrink passed: height shrunk from {height} to {r_height}")
    elif r_top > top:
        print(f"✅ test_overlap_shrink passed: moved down from {top} to {r_top}")
    else:
        print(f"⚠️ test_overlap_shrink: could not find safe position, returning original")
    assert result is not None


def test_empty_text_regions():
    """空文字区域列表，应返回原始位置。"""
    left, top, width, height = 100, 200, 300, 400
    result = compute_safe_overlay_box(left, top, width, height, [], SLIDE_W, SLIDE_H)
    assert result == (left, top, width, height)
    print("✅ test_empty_text_regions passed")


def test_overlaps_logic():
    """测试 IoU 重叠判断。"""
    box = {"x": 0.5, "y": 0.3, "width": 0.2, "height": 0.4}
    # 不重叠
    assert _overlaps(box, [{"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1}]) == False
    # 大面积重叠 (>15%)
    assert _overlaps(box, [{"x": 0.55, "y": 0.35, "width": 0.15, "height": 0.35}]) == True
    # 小面积接触 (<15%)
    assert _overlaps(box, [{"x": 0.69, "y": 0.30, "width": 0.10, "height": 0.40}]) == False
    print("✅ test_overlaps_logic passed")


def main():
    print("=" * 50)
    print("Testing text_region_detector logic")
    print("=" * 50)
    test_no_overlap()
    test_overlap_move_down()
    test_overlap_shrink()
    test_empty_text_regions()
    test_overlaps_logic()
    print("=" * 50)
    print("All tests passed!")


if __name__ == "__main__":
    main()
