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


def test_prompt_engine_assigns_multi_subject_body_to_linked_captions_without_extra_blocking_rules():
    prompt = generate_prompt_for_page(
        page_intent={
            "page_num": 5,
            "type": "content",
            "layout": "postcard",
            "visual_evidence": "Eiffel Tower viewpoint photos and restaurant photo collage",
            "visual_description": "主图与辅图采用不同形状，浅黄色信息块位于底部承载机位与餐厅正文要点，整体留白充足",
        },
        content_text={
            "headline": "埃菲尔铁塔打卡与餐厅 Tour Eiffel",
            "subhead": "经典机位与塔内法餐体验",
            "body": [
                "特罗卡德罗 Trocadéro：最经典的正面视角。",
                "Le Jules Verne：塔内法餐体验。",
            ],
        },
        reference_images=[],
        style_text_override="Style: 留白明信片\nTypography: 正文只放必要要点，优先放入浅黄色信息块。",
    )

    assert 'Linked caption body: "特罗卡德罗 Trocadéro：最经典的正面视角。"' in prompt
    assert 'Info block body: "特罗卡德罗 Trocadéro：最经典的正面视角。"' not in prompt
    assert 'Body: "特罗卡德罗 Trocadéro：最经典的正面视角。"' not in prompt
    assert "浅黄色信息块位于底部承载机位与餐厅正文要点" not in prompt
    assert "浅黄色信息块位于底部" in prompt
    assert "Image-text binding:" in prompt
    assert "Single-copy body rule" not in prompt


def test_prompt_engine_keeps_default_body_slot_when_info_block_is_not_copy_container():
    prompt = generate_prompt_for_page(
        page_intent={
            "page_num": 6,
            "type": "content",
            "layout": "postcard",
            "visual_evidence": "Seine River postcard layout",
            "visual_description": "右下角有浅黄色信息块作为页码与短标签装饰，正文放在左侧说明区",
        },
        content_text={
            "headline": "塞纳河 La Seine",
            "subhead": "巴黎城市景观的主轴",
            "body": ["塞纳河把巴黎的重要建筑、桥梁和博物馆串联起来。"],
        },
        reference_images=[],
        style_text_override="Style: light postcard with a small yellow note block decoration.",
    )

    assert 'Body: "塞纳河把巴黎的重要建筑、桥梁和博物馆串联起来。"' in prompt
    assert "Info block body:" not in prompt


def test_prompt_engine_assigns_body_to_info_block_when_copy_container_clause_is_adjacent():
    prompt = generate_prompt_for_page(
        page_intent={
            "page_num": 8,
            "type": "content",
            "layout": "postcard",
            "visual_evidence": "Tuileries garden and Orangerie museum photos",
            "visual_description": "浅黄色信息块位于右下或底部，收拢正文要点；整体构图保持留白40%左右",
        },
        content_text={
            "headline": "杜乐丽花园与橘园美术馆 Jardin des Tuileries / Musée de l’Orangerie",
            "subhead": "皇家花园散步，顺路看莫奈《睡莲》",
            "body": ["橘园美术馆就在花园一侧，最重要的看点是莫奈《睡莲》。"],
        },
        reference_images=[],
        style_text_override="Style: 留白明信片",
    )

    assert 'Info block body: "橘园美术馆就在花园一侧，最重要的看点是莫奈《睡莲》。"' in prompt
    assert "浅黄色信息块位于右下或底部，收拢正文要点" not in prompt
    assert "浅黄色信息块位于右下或底部" in prompt


def test_prompt_engine_assigns_body_to_info_block_for_contains_points_clause():
    prompt = generate_prompt_for_page(
        page_intent={
            "page_num": 2,
            "type": "content",
            "layout": "postcard",
            "visual_evidence": "Paris overview with Seine River photos",
            "visual_description": "浅黄色信息块位于右下，内含城市特色要点；整体保持留白",
        },
        content_text={
            "headline": "巴黎 Paris",
            "subhead": "塞纳河、古典建筑、艺术与时尚之都",
            "body": ["巴黎以塞纳河为城市主轴，许多重要地标都分布在两岸。"],
        },
        reference_images=[],
        style_text_override="Style: 留白明信片",
    )

    assert 'Info block body: "巴黎以塞纳河为城市主轴，许多重要地标都分布在两岸。"' in prompt
    assert 'Body: "巴黎以塞纳河为城市主轴，许多重要地标都分布在两岸。"' not in prompt
    assert "浅黄色信息块位于右下，内含城市特色要点" not in prompt
    assert "浅黄色信息块位于右下" in prompt


def test_prompt_engine_drops_empty_info_block_when_body_is_absent():
    prompt = generate_prompt_for_page(
        page_intent={
            "page_num": 4,
            "type": "hero",
            "layout": "postcard",
            "visual_evidence": "Eiffel Tower dusk panorama",
            "visual_description": "主视觉使用埃菲尔铁塔全景写实照片；右下浅黄色信息块承载地标介绍要点；整体如明信片般大图配留白",
        },
        content_text={
            "headline": "埃菲尔铁塔 Tour Eiffel",
            "subhead": "巴黎最经典的城市符号",
        },
        reference_images=[],
        style_text_override="Style: 留白明信片",
    )

    assert "Info block body:" not in prompt
    assert "Body:" not in prompt
    assert "右下浅黄色信息块" not in prompt
    assert "主视觉使用埃菲尔铁塔全景写实照片" in prompt


def test_prompt_engine_renders_hero_body_when_info_block_is_copy_container():
    prompt = generate_prompt_for_page(
        page_intent={
            "page_num": 4,
            "type": "hero",
            "layout": "postcard",
            "visual_evidence": "Eiffel Tower dusk panorama",
            "visual_description": "主视觉使用埃菲尔铁塔全景写实照片；右下浅黄色信息块承载地标介绍要点；整体如明信片般大图配留白",
        },
        content_text={
            "headline": "埃菲尔铁塔 Tour Eiffel",
            "subhead": "巴黎最经典的城市符号",
            "body": ["从远处看铁塔，往往比站在塔下更能感受到巴黎感。"],
        },
        reference_images=[],
        style_text_override="Style: 留白明信片",
    )

    assert 'Info block body: "从远处看铁塔，往往比站在塔下更能感受到巴黎感。"' in prompt
    assert 'Body: "从远处看铁塔，往往比站在塔下更能感受到巴黎感。"' not in prompt
    assert "右下浅黄色信息块承载地标介绍要点" not in prompt
    assert "右下浅黄色信息块" in prompt


def test_prompt_engine_handles_looser_info_area_language():
    prompt = generate_prompt_for_page(
        page_intent={
            "page_num": 5,
            "type": "content",
            "layout": "postcard",
            "visual_evidence": "Eiffel Tower viewpoints and restaurant photos",
            "visual_description": "浅黄色信息区贴合正文要点，放在照片旁留白区域；照片错位叠放保持呼吸感",
        },
        content_text={
            "headline": "埃菲尔铁塔打卡与餐厅 Tour Eiffel",
            "subhead": "经典机位与塔内法餐体验",
            "body": ["Le Jules Verne：位于铁塔二层，是塔内最有代表性的高规格法餐体验。"],
        },
        reference_images=[],
        style_text_override="Style: 留白明信片",
    )

    assert 'Info block body: "Le Jules Verne：位于铁塔二层，是塔内最有代表性的高规格法餐体验。"' in prompt
    assert "浅黄色信息区贴合正文要点" not in prompt
    assert "浅黄色信息区" in prompt


def test_prompt_engine_binds_multi_item_body_to_matching_visual_subjects():
    prompt = generate_prompt_for_page(
        page_intent={
            "page_num": 5,
            "type": "content",
            "layout": "postcard",
            "visual_evidence": "特罗卡德罗远景、战神广场草坪、比尔阿克姆桥与Le Jules Verne餐厅场景拼贴",
            "visual_description": "采用精致拼贴展示多个打卡机位与餐厅：特罗卡德罗远景、战神广场草坪视角、比尔阿克姆桥，以及Le Jules Verne餐厅内景；主图与辅图采用不同形状，轻微错位叠放保持呼吸感；浅黄色信息区贴合正文要点",
        },
        content_text={
            "headline": "埃菲尔铁塔打卡与餐厅 Tour Eiffel",
            "subhead": "经典机位与塔内法餐体验",
            "body": [
                "特罗卡德罗 Trocadéro：最经典的正面视角，适合拍完整铁塔。",
                "战神广场 Champ de Mars：草坪与铁塔同框，画面更轻松开阔。",
                "比尔阿克姆桥 Pont de Bir-Hakeim：桥梁结构与铁塔同框，更有电影感。",
                "Le Jules Verne：位于铁塔二层，是塔内最有代表性的高规格法餐体验。",
            ],
        },
        reference_images=[],
        style_text_override="Style: 留白明信片",
    )

    assert 'Linked caption body: "特罗卡德罗 Trocadéro：最经典的正面视角，适合拍完整铁塔。"' in prompt
    assert 'Info block body: "特罗卡德罗 Trocadéro：最经典的正面视角，适合拍完整铁塔。"' not in prompt
    assert "Image-text binding:" in prompt
    assert "Match these caption anchors to corresponding photos/subjects: 特罗卡德罗 Trocadéro、战神广场 Champ de Mars、比尔阿克姆桥 Pont de Bir-Hakeim、Le Jules Verne" in prompt


def test_prompt_engine_uses_explicit_image_slots_as_compact_source_of_truth():
    prompt = generate_prompt_for_page(
        page_intent={
            "page_num": 5,
            "type": "content",
            "layout": "postcard",
            "visual_evidence": "Eiffel Tower viewpoint photos and restaurant photo collage",
            "visual_description": "采用精致拼贴展示多个打卡机位与餐厅；主图与辅图采用不同形状，轻微错位叠放保持呼吸感",
            "image_slots": [
                {
                    "id": "A",
                    "subject": "特罗卡德罗 Trocadéro 正面铁塔远景",
                    "role": "primary",
                    "position": "upper-center large landscape",
                    "shape": "landscape postcard",
                    "linked_text": ["body_1", "特罗卡德罗 Trocadéro"],
                },
                {
                    "id": "B",
                    "subject": "Le Jules Verne 塔内餐厅窗边餐桌",
                    "role": "support",
                    "position": "right-side tall card",
                    "shape": "vertical rounded photo",
                    "linked_text": ["body_4", "Le Jules Verne"],
                },
            ],
        },
        content_text={
            "headline": "埃菲尔铁塔打卡与餐厅 Tour Eiffel",
            "subhead": "经典机位与塔内法餐体验",
            "body": [
                "特罗卡德罗 Trocadéro：最经典的正面视角，适合拍完整铁塔。",
                "战神广场 Champ de Mars：草坪与铁塔同框，画面更轻松开阔。",
                "比尔阿克姆桥 Pont de Bir-Hakeim：桥梁结构与铁塔同框，更有电影感。",
                "Le Jules Verne：位于铁塔二层，是塔内最有代表性的高规格法餐体验。",
            ],
        },
        reference_images=[],
        style_text_override="Style: 留白明信片",
    )

    assert 'Linked caption body: "特罗卡德罗 Trocadéro：最经典的正面视角，适合拍完整铁塔。"' in prompt
    assert "Slot map:" in prompt
    assert "A: 特罗卡德罗 Trocadéro 正面铁塔远景; role=primary; position=upper-center large landscape; shape=landscape postcard; linked text=body_1, 特罗卡德罗 Trocadéro" in prompt
    assert "B: Le Jules Verne 塔内餐厅窗边餐桌; role=support; position=right-side tall card; shape=vertical rounded photo; linked text=body_4, Le Jules Verne" in prompt
    assert "Use Slot map as the source of truth for image placement and caption association." in prompt


def test_prompt_engine_keeps_info_block_for_single_visual_subject():
    prompt = generate_prompt_for_page(
        page_intent={
            "page_num": 4,
            "type": "content",
            "layout": "postcard",
            "visual_evidence": "黄昏或蓝调时刻的埃菲尔铁塔全景",
            "visual_description": "主视觉使用埃菲尔铁塔全景写实照片；浅黄色信息区贴合正文要点，放在照片旁留白区域",
        },
        content_text={
            "headline": "埃菲尔铁塔 Tour Eiffel",
            "subhead": "巴黎最经典的城市符号",
            "body": [
                "埃菲尔铁塔建于1889年巴黎世界博览会。",
                "黄昏适合看天际线，夜晚适合看灯光。",
            ],
        },
        reference_images=[],
        style_text_override="Style: 留白明信片",
    )

    assert 'Info block body: "埃菲尔铁塔建于1889年巴黎世界博览会。"' in prompt
    assert "Linked caption body:" not in prompt
    assert "Image-text binding:" not in prompt


def test_prompt_engine_does_not_bind_unlabeled_overview_points():
    prompt = generate_prompt_for_page(
        page_intent={
            "page_num": 2,
            "type": "content",
            "layout": "postcard",
            "visual_evidence": "塞纳河两岸航拍俯瞰，桥梁、河道、古典建筑屋顶与城市轴线清晰可见",
            "visual_description": "主图采用塞纳河两岸航拍或高处俯瞰视角；主图左右可叠放1-2张小辅图，采用圆角卡片轻微错位；浅黄色信息区贴合正文要点",
        },
        content_text={
            "headline": "巴黎 Paris",
            "subhead": "塞纳河、古典建筑、艺术与时尚之都",
            "body": [
                "巴黎以塞纳河为城市主轴，许多重要地标都分布在两岸。",
                "右岸更集中体现历史广场、商业街区与奢侈品氛围。",
                "左岸更有艺术、学院、书店、咖啡馆和博物馆气质。",
            ],
        },
        reference_images=[],
        style_text_override="Style: 留白明信片",
    )

    assert 'Info block body: "巴黎以塞纳河为城市主轴，许多重要地标都分布在两岸。"' in prompt
    assert "Linked caption body:" not in prompt
    assert "Image-text binding:" not in prompt


def test_prompt_engine_assigns_body_to_info_block_for_text_content_clause():
    prompt = generate_prompt_for_page(
        page_intent={
            "page_num": 14,
            "type": "content",
            "layout": "postcard",
            "visual_evidence": "Paris fashion district photos",
            "visual_description": "右下浅黄色信息块承载文字内容，主图以街区照片为核心，四周保持留白",
        },
        content_text={
            "headline": "玛黑区 Le Marais",
            "subhead": "巴黎时尚与买手店街区",
            "body": ["玛黑区聚集独立设计师、古着店、香氛、美妆与生活方式小店。"],
        },
        reference_images=[],
        style_text_override="Style: 留白明信片",
    )

    assert 'Info block body: "玛黑区聚集独立设计师、古着店、香氛、美妆与生活方式小店。"' in prompt
    assert 'Body: "玛黑区聚集独立设计师、古着店、香氛、美妆与生活方式小店。"' not in prompt
    assert "右下浅黄色信息块承载文字内容" not in prompt
    assert "右下浅黄色信息块" in prompt


def test_prompt_engine_assigns_body_to_info_block_for_intro_text_clause():
    prompt = generate_prompt_for_page(
        page_intent={
            "page_num": 1,
            "type": "cover",
            "layout": "postcard",
            "visual_evidence": "Paris city photo collage",
            "visual_description": "右下浅黄色信息块承载副标题与简介文字，标题区留白克制",
        },
        content_text={
            "headline": "PARIS TRAVEL GUIDE",
            "subhead": "Business Reception Edition",
            "body": ["一份用于商务接待的巴黎及法国重点目的地导览。"],
        },
        reference_images=[],
        style_text_override="Style: 留白明信片",
    )

    assert 'Info block body: "一份用于商务接待的巴黎及法国重点目的地导览。"' in prompt
    assert 'Body: "一份用于商务接待的巴黎及法国重点目的地导览。"' not in prompt
    assert "右下浅黄色信息块承载副标题与简介文字" not in prompt
    assert "右下浅黄色信息块" in prompt
