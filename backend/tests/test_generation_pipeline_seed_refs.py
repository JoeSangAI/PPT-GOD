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
        captured.setdefault("prompts", []).append(prompt)
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

    assert any("PAGE REFERENCE FIDELITY" in prompt for prompt in captured["prompts"])
    assert any("Do not replace these references with invented people" in prompt for prompt in captured["prompts"])


def test_blend_page_refs_add_coverage_instruction():
    instruction = generation_pipeline._page_reference_fidelity_instruction([
        {
            "process_mode": "blend",
            "role": "content_ref",
            "label": "Reference Image 1",
        },
        {
            "process_mode": "blend",
            "role": "content_ref",
            "label": "Reference Image 2",
        },
    ])

    assert "PAGE REFERENCE COVERAGE" in instruction
    assert "Use every attached page reference" in instruction
    assert "do not base the slide on only one" in instruction


def test_team_portrait_refs_get_identity_bindings_from_bullet_order_and_bbox(tmp_path):
    names = ["朱德栋", "王颖", "王思鉴", "王硕"]
    boxes = [
        [58.5, 211.5, 114.75, 282.75],
        [489.0, 216.75, 540.75, 287.25],
        [59.25, 377.25, 111.0, 445.5],
        [495.75, 379.5, 544.5, 443.25],
    ]
    refs = []
    for idx, box in enumerate(boxes, start=1):
        path = tmp_path / f"portrait-{idx}.png"
        Image.new("RGB", (80, 110), "white").save(path)
        refs.append(
            ReferenceImage(
                project_id="project",
                file_path=str(path),
                role="content_ref",
                process_mode="crop",
                asset_kind="document_image",
                asset_analysis={
                    "source_document": "team.pdf",
                    "source_page_num": 16,
                    "bbox": box,
                    "image_width": 80,
                    "image_height": 110,
                },
            )
        )
    slide = Slide(
        page_num=16,
        type="content",
        visual_json={},
        content_json={
            "text_content": {
                "headline": "Botlife.ai 新一代AI社交平台",
                "subhead": "核心团队",
                "body": "\n".join(f"- {name} 简介文字" for name in names),
            }
        },
    )
    slide.reference_images = [refs[1], refs[3], refs[2], refs[0]]

    loaded = generation_pipeline._load_reference_images(slide)

    assert [ref["reference_binding"]["name"] for ref in loaded] == names
    assert [ref["reference_binding"]["position"] for ref in loaded] == [
        "top-left",
        "top-right",
        "bottom-left",
        "bottom-right",
    ]


def test_page_reference_fidelity_instruction_includes_identity_bindings():
    instruction = generation_pipeline._page_reference_fidelity_instruction([
        {
            "process_mode": "blend",
            "role": "content_ref",
            "label": "Reference Image 1",
            "reference_binding": {"name": "朱德栋", "position": "top-left"},
        },
        {
            "process_mode": "blend",
            "role": "content_ref",
            "label": "Reference Image 2",
            "reference_binding": {"name": "王颖", "position": "top-right"},
        },
    ])

    assert "PAGE REFERENCE BINDINGS" in instruction
    assert "Reference Image 1 -> 朱德栋 (top-left)" in instruction
    assert "Reference Image 2 -> 王颖 (top-right)" in instruction
    assert "Do not swap identities" in instruction


def test_product_refinement_prompt_includes_identity_bindings_for_each_input():
    prompt = generation_pipeline._product_refinement_prompt(
        Slide(page_num=16),
        [
            {
                "image": Image.new("RGB", (80, 110), "white"),
                "reference_binding": {"name": "朱德栋", "position": "top-left"},
            },
            {
                "image": Image.new("RGB", (80, 110), "white"),
                "reference_binding": {"name": "王颖", "position": "top-right"},
            },
        ],
    )

    assert "第2张参考图 -> 朱德栋（top-left）" in prompt
    assert "第3张参考图 -> 王颖（top-right）" in prompt
    assert "不要交换人物身份" in prompt


def test_local_slot_refinement_maps_multiple_crop_refs_to_detected_slots(monkeypatch, tmp_path):
    calls = []

    def fake_generate_slide_image(*, prompt, reference_images=None, aspect_ratio="16:9", **kwargs):
        calls.append({
            "prompt": prompt,
            "reference_count": len(reference_images or []),
            "aspect_ratio": aspect_ratio,
        })
        if len(calls) == 1:
            return Image.new("RGB", (1000, 600), "white")
        return Image.new("RGB", (256, 256), ["red", "green"][len(calls) - 2])

    monkeypatch.setattr(generation_pipeline, "generate_slide_image", fake_generate_slide_image)
    monkeypatch.setattr(
        generation_pipeline,
        "_detect_local_refinement_slots",
        lambda _base_path, _product_refs: [
            {"x": 0.10, "y": 0.20, "width": 0.20, "height": 0.30},
            {"x": 0.60, "y": 0.20, "width": 0.20, "height": 0.30},
        ],
    )

    refs = [
        {
            "image": Image.new("RGB", (80, 100), "black"),
            "process_mode": "crop",
            "role": "content_ref",
            "label": "Reference Image 1",
            "reference_binding": {"name": "甲", "position": "top-left"},
        },
        {
            "image": Image.new("RGB", (80, 100), "gray"),
            "process_mode": "crop",
            "role": "content_ref",
            "label": "Reference Image 2",
            "reference_binding": {"name": "乙", "position": "top-right"},
        },
    ]
    slide = Slide(
        id="slide-16",
        project_id="project",
        page_num=16,
        type="content",
        prompt_text="team slide",
        visual_json={},
    )

    result = generation_pipeline._generate_one_slide(
        slide,
        project_id="project",
        output_dir=str(tmp_path),
        preloaded_ref_data=refs,
        run_id="run",
    )

    assert result["error"] is None
    assert [call["reference_count"] for call in calls] == [2, 2, 2]
    assert calls[1]["aspect_ratio"] == "1:1"
    assert "甲" in calls[1]["prompt"]
    assert "乙" in calls[2]["prompt"]
    output = Image.open(result["image_path"]).convert("RGB")
    assert output.getpixel((150, 250)) == (255, 0, 0)
    assert output.getpixel((650, 250)) == (0, 128, 0)


def test_api_reference_sort_key_uses_pdf_bbox_when_shape_bounds_are_missing():
    from app.api.slides import _reference_image_sort_key

    top_left = ReferenceImage(
        project_id="project",
        file_path="x164.png",
        role="content_ref",
        asset_analysis={"source_document": "team.pdf", "source_page_num": 16, "bbox": [10, 20, 30, 40]},
    )
    top_right = ReferenceImage(
        project_id="project",
        file_path="x165.png",
        role="content_ref",
        asset_analysis={"source_document": "team.pdf", "source_page_num": 16, "bbox": [300, 22, 330, 44]},
    )
    bottom_left = ReferenceImage(
        project_id="project",
        file_path="x166.png",
        role="content_ref",
        asset_analysis={"source_document": "team.pdf", "source_page_num": 16, "bbox": [12, 200, 31, 240]},
    )

    assert sorted(
        [bottom_left, top_right, top_left],
        key=_reference_image_sort_key,
    ) == [top_left, top_right, bottom_left]
