from app.services.text_region_detector import _overlaps, compute_safe_overlay_box


SLIDE_W = 1792
SLIDE_H = 1024


def _right_card_box():
    return (
        int(SLIDE_W * 0.595),
        int(SLIDE_H * 0.18),
        int(SLIDE_W * 0.34),
        int(SLIDE_H * 0.58),
    )


def test_safe_overlay_box_keeps_original_position_without_overlap():
    text_regions = [{"x": 0.05, "y": 0.05, "width": 0.40, "height": 0.10}]
    left, top, width, height = _right_card_box()

    result = compute_safe_overlay_box(left, top, width, height, text_regions, SLIDE_W, SLIDE_H)

    assert result == (left, top, width, height)


def test_safe_overlay_box_moves_down_when_text_overlaps_top():
    text_regions = [{"x": 0.60, "y": 0.20, "width": 0.30, "height": 0.25}]
    left, top, width, height = _right_card_box()

    result = compute_safe_overlay_box(left, top, width, height, text_regions, SLIDE_W, SLIDE_H)
    _, new_top, new_width, new_height = result

    assert new_top > top
    assert new_width == width
    assert new_height == height


def test_safe_overlay_box_returns_original_for_empty_text_regions():
    result = compute_safe_overlay_box(100, 200, 300, 400, [], SLIDE_W, SLIDE_H)

    assert result == (100, 200, 300, 400)


def test_overlap_uses_meaningful_iou_threshold():
    box = {"x": 0.5, "y": 0.3, "width": 0.2, "height": 0.4}

    assert _overlaps(box, [{"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1}]) is False
    assert _overlaps(box, [{"x": 0.55, "y": 0.35, "width": 0.15, "height": 0.35}]) is True
    assert _overlaps(box, [{"x": 0.69, "y": 0.30, "width": 0.10, "height": 0.40}]) is False
