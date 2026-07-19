import pytest

from app.services import text_region_detector
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


def test_safe_overlay_box_moves_or_scales_until_text_is_clear():
    text_regions = [{"x": 0.60, "y": 0.20, "width": 0.30, "height": 0.25}]
    left, top, width, height = _right_card_box()

    result = compute_safe_overlay_box(left, top, width, height, text_regions, SLIDE_W, SLIDE_H)
    new_left, new_top, new_width, new_height = result
    resolved = {
        "x": new_left / SLIDE_W,
        "y": new_top / SLIDE_H,
        "width": new_width / SLIDE_W,
        "height": new_height / SLIDE_H,
    }

    assert result != (left, top, width, height)
    assert _overlaps(resolved, text_regions) is False
    assert new_width / new_height == pytest.approx(width / height)


def test_safe_overlay_box_returns_original_for_empty_text_regions():
    result = compute_safe_overlay_box(100, 200, 300, 400, [], SLIDE_W, SLIDE_H)

    assert result == (100, 200, 300, 400)


def test_overlap_detects_small_element_covering_text_even_when_iou_is_low():
    box = {"x": 0.5, "y": 0.3, "width": 0.2, "height": 0.4}

    assert _overlaps(box, [{"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1}]) is False
    assert _overlaps(box, [{"x": 0.55, "y": 0.35, "width": 0.15, "height": 0.35}]) is True
    assert _overlaps(box, [{"x": 0.69, "y": 0.30, "width": 0.10, "height": 0.40}]) is True


def test_small_logo_overlap_is_not_hidden_by_low_iou():
    logo = {"x": 0.91, "y": 0.02, "width": 0.05, "height": 0.08}
    title = {"x": 0.08, "y": 0.05, "width": 0.86, "height": 0.08}

    assert _overlaps(logo, [title]) is True


def test_safe_overlay_box_fails_closed_when_page_has_no_safe_space():
    full_slide_text = [{"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}]

    with pytest.raises(ValueError, match="refusing to cover slide text"):
        compute_safe_overlay_box(
            100,
            100,
            500,
            300,
            full_slide_text,
            SLIDE_W,
            SLIDE_H,
            min_scale=0.80,
        )


def test_text_region_detection_repairs_minor_model_json_errors(monkeypatch):
    monkeypatch.setattr(
        text_region_detector,
        "_call_vision_model",
        lambda *_args, **_kwargs: (
            '{"text_regions": ['
            '{"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.1}'
            ' {"x": 0.5, "y": 0.6, "width": 0.2, "height": 0.1}'
            ']}'
        ),
    )

    regions = text_region_detector.detect_text_regions("ignored.png")

    assert len(regions) == 2
    assert regions[1]["x"] == pytest.approx(0.5)
