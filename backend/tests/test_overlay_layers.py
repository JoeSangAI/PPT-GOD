import uuid

from app.services.overlay_layers import enabled_overlay_layers, normalize_overlay_layers, overlay_reservation_instruction


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
