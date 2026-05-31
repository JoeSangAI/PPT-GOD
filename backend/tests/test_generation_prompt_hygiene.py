from PIL import Image

from app.models.models import Slide
from app.services import generation_pipeline
from app.services.prompt_engine import generate_prompt_for_page


def test_seed_ref_layout_instruction_stays_compact(monkeypatch, tmp_path):
    captured = {}

    def fake_generate_slide_image(*, prompt, **kwargs):
        captured["prompt"] = prompt
        return Image.new("RGB", (1792, 1024), "white")

    monkeypatch.setattr(generation_pipeline, "generate_slide_image", fake_generate_slide_image)

    slide = Slide(
        id="slide-2",
        project_id="project",
        page_num=2,
        type="content",
        prompt_text="Base prompt.",
        visual_json={},
    )

    generation_pipeline._generate_one_slide(
        slide,
        project_id="project",
        output_dir=str(tmp_path),
        preloaded_ref_data=[
            {
                "image": Image.new("RGB", (120, 80), "white"),
                "process_mode": "layout",
                "role": "seed_ref",
                "label": "Reference Image 1",
                "file_path": str(tmp_path / "seed.png"),
            }
        ],
        run_id="run",
    )

    appended = captured["prompt"].replace("Base prompt.", "")
    assert "SAME-FAMILY LAYOUT REFERENCE" in appended
    assert "layout anchors only" in appended
    assert "Do not copy seed text" in appended
    assert "previously generated slides from the same page family" not in appended
    assert len(appended) < 380


def test_crop_page_references_and_product_assets_trigger_product_refinement():
    refs = generation_pipeline._product_refinement_refs([
        {
            "image": Image.new("RGB", (120, 80), "white"),
            "role": "content_ref",
            "process_mode": "crop",
            "asset_route_mode": "double_blend",
            "asset_kind": "other",
        },
        {
            "image": Image.new("RGB", (120, 80), "white"),
            "role": "visual_asset",
            "process_mode": "crop",
            "asset_route_mode": "double_blend",
            "asset_kind": "product",
        },
    ])

    assert [ref["role"] for ref in refs] == ["content_ref", "visual_asset"]


def test_prompt_engine_injects_exact_overlay_reservation_once():
    prompt = generate_prompt_for_page(
        page_intent={
            "page_num": 1,
            "type": "content",
            "layout": "single-focus",
            "visual_evidence": "A modern dashboard with charts and data visualizations",
            "visual_description": "Clean corporate layout with grid system",
            "overlay_layers": [
                {"asset_id": "asset-1", "enabled": True, "preset": "right-card", "mode": "exact_cutout"}
            ],
        },
        content_text={
            "headline": "Q3 Performance",
            "subhead": "Quarterly Review",
            "body": ["Revenue up 23%"],
        },
        reference_images=[],
        style_text_override="Modern corporate, blue accent",
    )

    assert prompt.count("Exact Overlay Reservation:") == 1
    reservation_text = prompt[prompt.find("Exact Overlay Reservation:"):]
    assert "right side" in reservation_text
    assert "CRITICAL LAYOUT INSTRUCTION" in reservation_text
    assert "Background treatment: keep the following zones completely free" not in prompt


def test_prompt_engine_omits_overlay_reservation_without_layers():
    prompt = generate_prompt_for_page(
        page_intent={
            "page_num": 1,
            "type": "content",
            "layout": "single-focus",
            "visual_evidence": "A simple background",
            "visual_description": "Minimal layout",
        },
        content_text={"headline": "Hello"},
        reference_images=[],
        style_text_override="",
    )

    assert "Exact Overlay Reservation" not in prompt
