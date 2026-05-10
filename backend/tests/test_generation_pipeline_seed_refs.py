from PIL import Image

from app.models.models import Slide
from app.services import generation_pipeline


def test_seed_images_default_to_prompt_hints(monkeypatch, tmp_path):
    seed_path = tmp_path / "seed.png"
    Image.new("RGB", (32, 18), "white").save(seed_path)
    slide = Slide(page_num=2, type="content", visual_json={})

    monkeypatch.setattr(generation_pipeline.settings, "IMAGE_USE_SEED_REFERENCE_IMAGES", False)

    refs = generation_pipeline._load_reference_images(slide, seed_image_paths=[str(seed_path)])

    assert refs[0]["role"] == "seed_ref_hint"
    assert "image" not in refs[0]


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
    assert "module marker 「三」" in instruction
    assert "main title 「术」" in instruction
    assert "headline 「模块三：术」" in instruction
    assert "subhead 「企业怎么布局」" in instruction
    assert "overrides any earlier layout or composition wording" in instruction
    assert len(instruction) < 1000


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
