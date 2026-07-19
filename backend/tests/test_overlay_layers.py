import uuid

from app.services.overlay_layers import (
    apply_llm_overlay_layout,
    build_overlay_asset_context_map,
    enabled_overlay_layers,
    normalize_overlay_layers,
    overlay_box,
    overlay_reservation_instruction,
)


def test_normalize_overlay_layers_accepts_string_asset_ids():
    ref_id = uuid.uuid4()

    result = normalize_overlay_layers(
        [{"asset_id": str(ref_id), "mode": "exact_cutout", "preset": "left-card"}],
        valid_asset_ids={str(ref_id)},
        strict_assets=True,
    )

    assert len(result) == 1
    assert result[0]["asset_id"] == str(ref_id)
    assert result[0]["mode"] == "exact_cutout"
    assert result[0]["preset"] == "left-card"
    assert result[0]["fit"] == "contain"


def test_normalize_overlay_layers_preserves_matching_resolved_placement():
    result = normalize_overlay_layers(
        [{
            "asset_id": "asset-1",
            "mode": "exact_card",
            "preset": "primary-left",
            "resolved_overlay_box": {
                "left": 0.03,
                "top": 0.19,
                "width": 0.38,
                "height": 0.52,
                "source_preset": "primary-left",
                "source_mode": "exact_card",
            },
        }],
        valid_asset_ids=None,
        strict_assets=False,
    )

    assert result[0]["resolved_overlay_box"]["left"] == 0.03
    assert result[0]["resolved_overlay_box"]["source_preset"] == "primary-left"


def test_normalize_overlay_layers_drops_stale_resolved_placement():
    result = normalize_overlay_layers(
        [{
            "asset_id": "asset-1",
            "mode": "exact_card",
            "preset": "right-card",
            "resolved_overlay_box": {
                "left": 0.03,
                "top": 0.19,
                "width": 0.38,
                "height": 0.52,
                "source_preset": "primary-left",
                "source_mode": "exact_card",
            },
        }],
        valid_asset_ids=None,
        strict_assets=False,
    )

    assert "resolved_overlay_box" not in result[0]


def test_normalize_overlay_layers_keeps_single_default_slot():
    result = normalize_overlay_layers(
        [{"asset_id": "asset-1", "mode": "exact_cutout"}],
        valid_asset_ids=None,
        strict_assets=False,
    )

    assert len(result) == 1
    assert result[0]["preset"] == "right-card"


def test_normalize_overlay_layers_assigns_distinct_slots_for_multiple_defaults():
    result = normalize_overlay_layers(
        [
            {"asset_id": "asset-1", "mode": "exact_cutout"},
            {"asset_id": "asset-2", "mode": "exact_cutout"},
            {"asset_id": "asset-3", "mode": "exact_cutout"},
        ],
        valid_asset_ids=None,
        strict_assets=False,
    )

    assert [layer["preset"] for layer in result] == ["gallery-3-left", "gallery-3-center", "gallery-3-right"]


def test_default_three_overlay_slots_have_equal_geometry():
    result = normalize_overlay_layers(
        [
            {"asset_id": "asset-1", "mode": "exact_cutout"},
            {"asset_id": "asset-2", "mode": "exact_cutout"},
            {"asset_id": "asset-3", "mode": "exact_cutout"},
        ],
        valid_asset_ids=None,
        strict_assets=False,
    )

    class FakePresentation:
        slide_width = 12_000
        slide_height = 6_750

    boxes = [overlay_box(FakePresentation, layer["preset"]) for layer in result]

    assert len({box[1] for box in boxes}) == 1
    assert len({box[2] for box in boxes}) == 1
    assert len({box[3] for box in boxes}) == 1


def test_overlay_layers_infer_primary_secondary_from_usage_notes():
    result = normalize_overlay_layers(
        [
            {"asset_id": "asset-main", "mode": "exact_cutout", "usage_note": "这张作为主图放大展示"},
            {"asset_id": "asset-a", "mode": "exact_cutout", "usage_note": "辅图：细节补充"},
            {"asset_id": "asset-b", "mode": "exact_cutout", "usage_note": "补充截图"},
        ],
        valid_asset_ids=None,
        strict_assets=False,
    )

    assert [layer["layout_role"] for layer in result] == ["primary", "secondary", "secondary"]
    assert [layer["preset"] for layer in result] == [
        "primary-left",
        "secondary-right-top",
        "secondary-right-bottom",
    ]


def test_overlay_layers_use_explicit_layout_role_for_primary_secondary():
    result = normalize_overlay_layers(
        [
            {"asset_id": "asset-a", "mode": "exact_cutout", "layout_role": "secondary"},
            {"asset_id": "asset-main", "mode": "exact_cutout", "layout_role": "primary"},
        ],
        valid_asset_ids=None,
        strict_assets=False,
    )

    by_asset = {layer["asset_id"]: layer for layer in result}
    assert by_asset["asset-main"]["preset"] == "primary-left"
    assert by_asset["asset-a"]["preset"] == "secondary-right"


def test_llm_overlay_layout_can_promote_asset_to_primary_without_preset():
    layers = normalize_overlay_layers(
        [
            {"asset_id": "asset-a", "mode": "exact_cutout"},
            {"asset_id": "asset-main", "mode": "exact_cutout"},
        ],
        valid_asset_ids=None,
        strict_assets=False,
    )

    result = apply_llm_overlay_layout(
        layers,
        [{"asset_id": "asset-main", "layout_role": "primary", "reason": "主画面"}],
    )

    by_asset = {layer["asset_id"]: layer for layer in result}
    assert by_asset["asset-main"]["layout_role"] == "primary"
    assert by_asset["asset-main"]["preset"] == "primary-left"
    assert by_asset["asset-a"]["preset"] == "secondary-right"


def test_overlay_layers_demote_extra_primary_roles_to_secondary():
    result = normalize_overlay_layers(
        [
            {"asset_id": "asset-main-a", "mode": "exact_cutout", "layout_role": "primary"},
            {"asset_id": "asset-main-b", "mode": "exact_cutout", "layout_role": "primary"},
            {"asset_id": "asset-c", "mode": "exact_cutout"},
        ],
        valid_asset_ids=None,
        strict_assets=False,
    )

    assert [layer["layout_role"] for layer in result] == ["primary", "secondary", "secondary"]
    assert [layer["preset"] for layer in result] == [
        "primary-left",
        "secondary-right-top",
        "secondary-right-bottom",
    ]


def test_overlay_layers_detect_comparison_group_from_asset_context():
    result = normalize_overlay_layers(
        [
            {"asset_id": "after", "mode": "exact_cutout"},
            {"asset_id": "before", "mode": "exact_cutout"},
        ],
        valid_asset_ids=None,
        strict_assets=False,
        asset_context_by_id={
            "before": {"asset_name": "优化前首页截图", "usage_note": "before"},
            "after": {"asset_name": "优化后首页截图", "usage_note": "after"},
        },
    )

    by_asset = {layer["asset_id"]: layer for layer in result}
    assert by_asset["before"]["layout_group"] == "comparison"
    assert by_asset["after"]["layout_group"] == "comparison"
    assert by_asset["before"]["preset"] == "gallery-2-left"
    assert by_asset["after"]["preset"] == "gallery-2-right"


def test_overlay_layers_detect_sequence_group_and_order_from_asset_context():
    result = normalize_overlay_layers(
        [
            {"asset_id": "step-3", "mode": "exact_cutout"},
            {"asset_id": "step-1", "mode": "exact_cutout"},
            {"asset_id": "step-2", "mode": "exact_cutout"},
        ],
        valid_asset_ids=None,
        strict_assets=False,
        asset_context_by_id={
            "step-1": {"asset_name": "步骤1：扫码"},
            "step-2": {"asset_name": "步骤2：确认"},
            "step-3": {"asset_name": "步骤3：完成"},
        },
    )

    by_asset = {layer["asset_id"]: layer for layer in result}
    assert {layer["layout_group"] for layer in result} == {"sequence"}
    assert by_asset["step-1"]["preset"] == "gallery-3-left"
    assert by_asset["step-2"]["preset"] == "gallery-3-center"
    assert by_asset["step-3"]["preset"] == "gallery-3-right"


def test_build_overlay_asset_context_map_preserves_reference_metadata():
    class Asset:
        id = "asset-1"
        asset_name = "优化前截图"
        asset_kind = "material"
        usage_note = "before"
        asset_analysis = {"subject": "旧版首页", "image_width": 1080, "image_height": 1440}

    result = build_overlay_asset_context_map([Asset()])

    assert result["asset-1"]["asset_name"] == "优化前截图"
    assert result["asset-1"]["asset_analysis"]["subject"] == "旧版首页"


def test_normalize_overlay_layers_resolves_duplicate_enabled_slots():
    result = normalize_overlay_layers(
        [
            {"asset_id": "asset-1", "enabled": True, "preset": "right-card"},
            {"asset_id": "asset-2", "enabled": True, "preset": "right-card"},
        ],
        valid_asset_ids=None,
        strict_assets=False,
    )

    assert len({layer["preset"] for layer in result}) == 2
    assert result[0]["preset"] == "right-card"


def test_enabled_overlay_layers_filters_invalid_or_disabled_assets():
    visual = {
        "overlay_layers": [
            {"asset_id": "asset-ok", "enabled": True, "preset": "right-card"},
            {"asset_id": "asset-disabled", "enabled": False, "preset": "left-card"},
            {"asset_id": "asset-missing", "enabled": True, "preset": "center-card"},
        ]
    }

    layers = enabled_overlay_layers(visual, valid_asset_ids={"asset-ok", "asset-disabled"})

    assert [layer["asset_id"] for layer in layers] == ["asset-ok"]


def test_overlay_reservation_instruction_describes_enabled_slots():
    instruction = overlay_reservation_instruction({
        "overlay_layers": [
            {"asset_id": "asset-a", "enabled": True, "preset": "right-card", "mode": "exact_cutout"},
            {"asset_id": "asset-b", "enabled": False, "preset": "left-card", "mode": "exact_cutout"},
        ]
    })

    assert "CRITICAL LAYOUT INSTRUCTION" in instruction
    assert "1 clean empty background zone" in instruction
    assert "right side" in instruction
    assert "left side" not in instruction
