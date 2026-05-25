from PIL import Image

from app.models.models import Slide
from app.services import generation_pipeline


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


def test_crop_page_references_do_not_trigger_product_refinement():
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

    assert len(refs) == 1
    assert refs[0]["role"] == "visual_asset"
