from types import SimpleNamespace

from PIL import Image

from app.models.models import ReferenceImage, Slide
from app.services import generation_pipeline


def test_seed_images_default_to_prompt_hints(monkeypatch, tmp_path):
    seed_path = tmp_path / "seed.png"
    Image.new("RGB", (32, 18), "white").save(seed_path)
    slide = Slide(page_num=2, type="content", visual_json={})

    monkeypatch.setattr(generation_pipeline.settings, "IMAGE_USE_SEED_REFERENCE_IMAGES", False)

    refs = generation_pipeline._load_reference_images(slide, seed_image_paths=[str(seed_path)])

    assert refs[0]["role"] == "seed_ref_hint"
    assert "image" not in refs[0]


def test_template_references_default_to_text_hints_not_uploaded_images(tmp_path):
    template_path = tmp_path / "template.png"
    Image.new("RGB", (32, 18), "white").save(template_path)
    slide = SimpleNamespace(
        page_num=4,
        type="content",
        visual_json={},
        reference_images=[],
        project=SimpleNamespace(
            reference_images=[],
            selected_template_recommendations={
                "content": {
                    "file_path": str(template_path),
                    "layout_file_path": str(template_path),
                    "application_strength": "strong",
                }
            },
        ),
    )

    refs = generation_pipeline._load_reference_images(slide)

    assert refs[0]["role"] == "template_hint"
    assert refs[0]["file_path"] == str(template_path)
    assert "image" not in refs[0]
    assert [ref for ref in refs if ref.get("image") is not None] == []


def test_seed_images_can_be_uploaded_when_enabled(monkeypatch, tmp_path):
    seed_path = tmp_path / "seed.png"
    Image.new("RGB", (32, 18), "white").save(seed_path)
    slide = Slide(page_num=2, type="content", visual_json={})

    monkeypatch.setattr(generation_pipeline.settings, "IMAGE_USE_SEED_REFERENCE_IMAGES", True)

    refs = generation_pipeline._load_reference_images(slide, seed_image_paths=[str(seed_path)])

    assert refs[0]["role"] == "seed_ref"
    assert refs[0]["image"].size == (32, 18)
    assert refs[0]["image"].info["pptgod_reference_role"] == "seed_ref"
    assert refs[0]["image"].info["pptgod_reference_source_path"] == str(seed_path)
    assert refs[0]["image"].info["pptgod_reference_source_size"] > 0


def test_section_seed_base_edit_contract_is_compact():
    slide = Slide(
        page_num=8,
        type="section",
        content_json={
            "section_title": "术",
            "text_content": {
                "headline": "模块三：术",
                "subhead": "企业怎么布局",
            },
        },
    )

    instruction = generation_pipeline._seed_base_edit_instruction(slide, 1)

    assert "DIRECT SEED IMAGE EDIT CONTRACT" in instruction
    assert "Use Reference Image 1 as the base slide image" in instruction
    assert "headline 「模块三：术」" in instruction
    assert "subhead 「企业怎么布局」" in instruction
    assert "module marker 「三」" not in instruction
    assert "main title 「术」" not in instruction
    assert "standalone chapter-number badge" in instruction
    assert "overrides any earlier layout or composition wording" in instruction
    assert len(instruction) < 1000


def test_section_seed_base_edit_contract_uses_single_visible_number_source():
    slide = Slide(
        page_num=18,
        type="section",
        content_json={
            "section_title": "模块六",
            "text_content": {
                "headline": "创意概念",
                "subhead": "Part 6 — 《拿不准时刻》系列",
            },
        },
    )

    instruction = generation_pipeline._seed_base_edit_instruction(slide, 1)

    assert "DIRECT SEED IMAGE EDIT CONTRACT" in instruction
    assert "headline 「创意概念」" in instruction
    assert "subhead 「Part 6 — 《拿不准时刻》系列」" in instruction
    assert "module marker 「六」" not in instruction
    assert "main title 「模块六」" not in instruction
    assert "standalone chapter-number badge" in instruction


def test_seed_base_edit_contract_does_not_affect_content_pages():
    slide = Slide(page_num=9, type="content", content_json={"text_content": {"headline": "正文"}})

    assert generation_pipeline._seed_base_edit_instruction(slide, 1) == ""


def test_section_pages_use_only_one_seed_base(monkeypatch, tmp_path):
    seed_a = tmp_path / "seed-a.png"
    seed_b = tmp_path / "seed-b.png"
    Image.new("RGB", (32, 18), "white").save(seed_a)
    Image.new("RGB", (32, 18), "black").save(seed_b)
    slide = Slide(page_num=8, type="section", visual_json={})

    monkeypatch.setattr(generation_pipeline.settings, "IMAGE_USE_SEED_REFERENCE_IMAGES", True)

    refs = generation_pipeline._load_reference_images(slide, seed_image_paths=[str(seed_a), str(seed_b)])

    assert [ref["file_path"] for ref in refs if ref.get("role") == "seed_ref"] == [str(seed_a)]


def test_non_section_pages_can_still_use_two_seed_images(monkeypatch, tmp_path):
    seed_a = tmp_path / "seed-a.png"
    seed_b = tmp_path / "seed-b.png"
    Image.new("RGB", (32, 18), "white").save(seed_a)
    Image.new("RGB", (32, 18), "black").save(seed_b)
    slide = Slide(page_num=9, type="content", visual_json={})

    monkeypatch.setattr(generation_pipeline.settings, "IMAGE_USE_SEED_REFERENCE_IMAGES", True)

    refs = generation_pipeline._load_reference_images(slide, seed_image_paths=[str(seed_a), str(seed_b)])

    assert [ref["file_path"] for ref in refs if ref.get("role") == "seed_ref"] == [str(seed_a), str(seed_b)]


def test_pptx_parallel_page_refs_upgrade_stale_blend_to_crop(tmp_path):
    ref_path = tmp_path / "p14-ref.png"
    Image.new("RGB", (120, 80), "white").save(ref_path)
    slide = Slide(page_num=14, type="content", visual_json={})
    slide.reference_images = [
        ReferenceImage(
            project_id="project",
            file_path=str(ref_path),
            role="content_ref",
            process_mode="blend",
            asset_kind="other",
            asset_analysis={
                "source_document": "F1.pptx",
                "asset_group_role": "parallel_page_reference_set",
                "asset_group_index": 1,
                "asset_group_size": 6,
                "detected_kind": "other",
            },
        )
    ]

    refs = generation_pipeline._load_reference_images(slide)

    assert refs[0]["process_mode"] == "crop"


def test_crop_page_refs_add_fidelity_override_to_generation_prompt(monkeypatch, tmp_path):
    captured = {}

    def fake_generate_slide_image(*, prompt, **kwargs):
        captured["prompt"] = prompt
        return Image.new("RGB", (1792, 1024), "white")

    monkeypatch.setattr(generation_pipeline, "generate_slide_image", fake_generate_slide_image)

    slide = Slide(
        id="slide-14",
        project_id="project",
        page_num=14,
        type="content",
        prompt_text="Layout says blend mode and extract the main visual style.",
        visual_json={},
    )
    ref_image = Image.new("RGB", (120, 80), "white")
    generation_pipeline._generate_one_slide(
        slide,
        project_id="project",
        output_dir=str(tmp_path),
        preloaded_ref_data=[
            {
                "image": ref_image,
                "process_mode": "crop",
                "role": "content_ref",
                "label": "Reference Image 1",
                "file_path": str(tmp_path / "p14-ref.png"),
                "asset_analysis": {
                    "asset_group_role": "parallel_page_reference_set",
                    "asset_group_index": 1,
                    "asset_group_size": 6,
                },
            }
        ],
        run_id="run",
    )

    assert "PAGE REFERENCE FIDELITY" in captured["prompt"]
    assert "Do not replace these references with invented people" in captured["prompt"]
