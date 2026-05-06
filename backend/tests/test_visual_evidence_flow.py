import io

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import projects as projects_api
from app.api import slides as slides_api
from app.services import generation_pipeline
from app.schemas.project import ProjectUpdate
from app.api.slides import _resolve_generation_page_nums
from app.models.base import Base
from app.models.models import Project, ReferenceImage, Slide
from app.services.generation_pipeline import (
    _generate_one_slide,
    _load_reference_images,
)
from app.services.image_generation import _cache_key
from app.services.logo_assets import prepare_logo_overlay_image
from app.services import prompt_engine
from app.utils.text_cleaning import normalize_markdown_emphasis
from app.services.visual_plan import (
    _build_batch_prompt,
    _do_generate_visual_plan,
    _default_visual_asset_usage,
    _fallback_visual_plan,
    _recall_visual_assets_for_page,
    _safe_parse_json,
)
from app.services.style_proposal import _build_content_style_direction
from types import SimpleNamespace

from PIL import Image


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def png_upload(name="asset.png"):
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(buf, "PNG")
    buf.seek(0)
    return SimpleNamespace(filename=name, file=buf, content_type="image/png")


def test_logo_overlay_preprocess_trims_white_background(tmp_path):
    logo_path = tmp_path / "logo.png"
    img = Image.new("RGB", (120, 80), "white")
    for x in range(42, 78):
        for y in range(18, 62):
            img.putpixel((x, y), (150, 0, 0))
    img.save(logo_path)

    overlay_path = prepare_logo_overlay_image(str(logo_path))
    overlay = Image.open(overlay_path).convert("RGBA")

    assert overlay.size[0] < 50
    assert overlay.size[1] < 60
    assert overlay.getchannel("A").getextrema()[0] == 0


def test_normalize_markdown_emphasis_balances_line_start_marker():
    text = "**第四部分：媒介与资本叙事\n普通正文"

    assert normalize_markdown_emphasis(text) == "**第四部分：媒介与资本叙事**\n普通正文"


def test_prompt_text_contract_strips_unbalanced_markdown():
    prompt = prompt_engine.generate_prompt_for_page(
        page_intent={
            "page_num": 2,
            "type": "toc",
            "layout": "toc",
            "visual_evidence": "目录结构",
            "visual_description": "四个章节纵向排列。",
        },
        content_text={
            "headline": "战略全局",
            "body": "**第四部分：媒介与资本叙事",
        },
        style_text_override="Style: 简洁\nPalette: #FFFFFF, #111111",
    )

    assert 'Body: "第四部分：媒介与资本叙事"' in prompt
    assert 'Body: "**第四部分' not in prompt


def test_fallback_visual_plan_uses_concrete_visual_evidence():
    plan = _fallback_visual_plan(
        [
            {
                "page_num": 12,
                "type": "content",
                "text_content": {
                    "headline": "线上渠道：内容电商与社交语境统一",
                    "body": "直播间背景板与达人合作 Brief 统一古法香话术",
                },
            },
            {
                "page_num": 13,
                "type": "content",
                "text_content": {
                    "headline": "公关主线：夺取行业标准制定权",
                    "body": "发布《古法香标准白皮书》，联合权威机构举办发布会",
                },
            },
        ],
        [],
    )

    assert plan[0]["visual_evidence"] == "直播间背景板、达人短视频矩阵和统一话术卡"
    assert "现代商务风格画面" not in plan[0]["visual_description"]
    assert "白皮书" in plan[1]["visual_evidence"]
    assert plan[0]["seed_family"] == "content"
    assert plan[0]["is_seed_recommended"] is True


def test_visual_plan_json_repair_handles_multiline_llm_strings():
    raw = '{"6": {"visual_evidence": "终端货架", "visual_description": "第一行\n第二行", "visual_asset_ids": [], "visual_asset_usage": {}}}'

    parsed = _safe_parse_json(raw, 1)

    assert parsed["6"]["visual_description"] == "第一行\n第二行"


def test_prompt_keeps_exact_text_contract_and_visual_evidence(monkeypatch):
    monkeypatch.setattr(prompt_engine, "_call_llm_for_final_prompt", lambda _: "A concise slide image prompt.")

    prompt = prompt_engine.generate_prompt_for_page(
        page_intent={
            "page_num": 1,
            "type": "content",
            "layout": "content_split",
            "visual_evidence": "行业标准白皮书、官方印章和发布会背板",
            "visual_description": "围绕白皮书和发布会组织左右分栏。",
        },
        content_text={
            "headline": "整合营销：夺取行业标准制定权",
            "subhead": "两大事件，树立古法香正统地位",
            "body": "发布《古法香标准白皮书》\n联动权威机构举办发布会",
        },
        style_text_override="Style: 中式高端品牌\nPalette: #400000, #D4AF37\nPage type adaptation: 内容页优先可读",
    )

    assert 'Headline: "整合营销：夺取行业标准制定权"' in prompt
    assert 'Subhead: "两大事件，树立古法香正统地位"' in prompt
    assert 'Body: "发布《古法香标准白皮书》"' in prompt
    assert 'Body: "联动权威机构举办发布会"' in prompt
    assert "Visual:\n行业标准白皮书、官方印章和发布会背板" in prompt
    assert "FONT REQUIREMENT" not in prompt


def test_prompt_includes_selected_global_visual_asset(monkeypatch):
    monkeypatch.setattr(prompt_engine, "_call_llm_for_final_prompt", lambda brief: brief)

    prompt = prompt_engine.generate_prompt_for_page(
        page_intent={
            "page_num": 2,
            "type": "content",
            "layout": "content_split",
            "visual_evidence": "胡姬花花生油瓶与终端货架",
            "visual_description": "右侧展示产品瓶，左侧放正文。",
            "visual_asset_usage": {"asset-1": "放在右侧产品展示区，保持瓶型和包装文字可识别"},
        },
        content_text={"headline": "终端小油瓶体验", "body": "用产品实物建立货架记忆"},
        reference_images=[
            {
                "id": "asset-1",
                "role": "visual_asset",
                "process_mode": "crop",
                "asset_name": "胡姬花花生油瓶",
                "asset_kind": "product",
                "description": "subject=胡姬花花生油瓶; features=红色瓶盖、黄色标签",
            }
        ],
        style_text_override="Style: 品牌展示\nPalette: #FFFFFF, #B01622",
    )

    assert "Product slot: 胡姬花花生油瓶" in prompt
    assert "uploaded product image as the product source" in prompt
    assert "hidden refinement pass" in prompt
    assert "Place the uploaded product image in the right side area" in prompt
    assert "红色瓶盖" not in prompt
    assert "黄色标签" not in prompt


def test_prompt_strips_product_details_from_layout_usage_and_style_negatives(monkeypatch):
    monkeypatch.setattr(prompt_engine, "_call_llm_for_final_prompt", lambda brief: brief)

    prompt = prompt_engine.generate_prompt_for_page(
        page_intent={
            "page_num": 1,
            "type": "cover",
            "layout": "cover",
            "visual_evidence": "品牌主视觉与胡姬花花生油产品实物",
            "visual_description": (
                "全屏深红色背景；画面中央偏下位置展示胡姬花古法花生油产品实物——"
                "5升装透明金黄桶身、金色提手盖、瓶颈深红扇形非遗吊牌尤为突出；"
                "产品后方呈现古法木榨工艺场景；不要使用科技风"
            ),
            "visual_asset_usage": {
                "asset-1": "置于画面中央偏下位置，作为品牌核心实物锚点；产品瓶颈扇形吊牌结构、红底金边标签必须完整保留"
            },
        },
        content_text={"headline": "2026胡姬花花生油品牌策略建议"},
        reference_images=[
            {
                "id": "asset-1",
                "role": "visual_asset",
                "process_mode": "crop",
                "asset_name": "胡姬花古法花生油",
                "asset_kind": "product",
                "description": "5升装透明金黄色塑料桶装花生油，瓶盖为金色带提手设计",
            }
        ],
        style_text_override=(
            "Style: 新中式古典重彩风格\n"
            "Mood: 厚重、典雅、华丽、古朴\n"
            "Visual rhythm: 内容核心更接近古法非遗、传统食品/农业品牌，应优先考虑传统质感；不要因为出现“战略”而推荐科技风"
        ),
    )

    assert prompt.index("References:") < prompt.index("Style:")
    assert "Product slot: 胡姬花古法花生油" in prompt
    assert "uploaded product image as the product source" in prompt
    assert "Place the uploaded product image in the lower center area" in prompt
    assert "产品后方呈现古法木榨工艺场景" not in prompt
    for unwanted in ["5升", "金黄桶身", "金色提手", "非遗吊牌", "红底金边标签", "不要因为", "科技风"]:
        assert unwanted not in prompt


def test_style_direction_hint_is_positive_not_negative():
    hint = _build_content_style_direction(
        traditional_score=1,
        food_score=1,
        tech_score=1,
        brand_score=1,
    )

    assert "传统质感" in hint
    assert "不要" not in hint
    assert "科技风" not in hint


def test_visual_plan_source_prompt_avoids_asset_detail_production():
    prompt = _build_batch_prompt(
        pages_summary=[
            {
                "page_num": 1,
                "type": "cover",
                "text_content": {"headline": "产品封面", "body": "展示产品与工艺场景"},
            }
        ],
        style={"meta": {"theme": "新中式", "mood": "典雅", "palette": ["#6B0B0B", "#D4AF37"]}, "body": "品牌风格"},
        global_visual_assets=[
            {
                "id": "asset-1",
                "name": "胡姬花花生油瓶",
                "kind": "product",
                "usage_note": "产品展示页使用",
            }
        ],
    )

    assert "visual_asset_usage 只能写位置和画面占比" in prompt
    assert "不要在 visual_evidence、visual_description、visual_asset_usage 里复述外观" in prompt
    assert "保真要求" not in prompt


def test_default_visual_asset_usage_does_not_describe_product_appearance():
    usage = _default_visual_asset_usage(
        {"name": "胡姬花花生油瓶", "kind": "product"},
        {"page_num": 1},
    )

    assert "uploaded product image" in usage
    assert "胡姬花花生油瓶" not in usage
    for unwanted in ["主体形状", "包装结构", "颜色", "品牌识别"]:
        assert unwanted not in usage


def test_prompt_reserves_logo_overlay_corner_without_logo_ban(monkeypatch):
    monkeypatch.setattr(prompt_engine, "_call_llm_for_final_prompt", lambda brief: brief)

    prompt = prompt_engine.generate_prompt_for_page(
        page_intent={
            "page_num": 1,
            "type": "cover",
            "layout": "cover",
            "visual_evidence": "品牌主张与主视觉背景",
            "visual_description": "封面突出主标题，品牌 Logo 与标题形成稳定关系。",
        },
        content_text={"headline": "胡姬花年度整合营销提案"},
        reference_images=[],
        style_text_override="Style: 品牌提案\nPalette: #FFFFFF, #B01622",
    )

    assert "Logo Overlay Reservation" in prompt
    assert "title/text block brand lockup area" in prompt
    assert "No logo, brand mark" not in prompt


def test_generation_loads_selected_visual_assets_without_signature_logo(tmp_path):
    page_ref_path = tmp_path / "page.png"
    logo_path = tmp_path / "logo.png"
    asset_path = tmp_path / "asset.png"
    template_path = tmp_path / "template.png"
    for path in (page_ref_path, logo_path, asset_path, template_path):
        Image.new("RGB", (8, 8), "white").save(path)

    page_ref = SimpleNamespace(
        id="page-ref",
        role="content_ref",
        file_path=str(page_ref_path),
        process_mode="blend",
    )
    logo = SimpleNamespace(
        id="logo-1",
        role="logo",
        slide_id=None,
        file_path=str(logo_path),
        process_mode="blend",
    )
    visual_asset = SimpleNamespace(
        id="asset-1",
        role="visual_asset",
        slide_id=None,
        file_path=str(asset_path),
        process_mode="crop",
        asset_kind="product",
    )
    project = SimpleNamespace(
        reference_images=[logo, visual_asset],
        selected_template_recommendations={"content": {"file_path": str(template_path)}},
    )
    slide = SimpleNamespace(
        page_num=3,
        type="content",
        visual_json={"visual_asset_ids": ["asset-1"]},
        reference_images=[page_ref],
        project=project,
    )

    refs = _load_reference_images(slide)

    assert [r["role"] for r in refs] == ["visual_asset", "content_ref", "template"]
    assert refs[0]["process_mode"] == "crop"


def test_generation_can_load_logo_as_scene_asset_on_cover_when_blend(tmp_path):
    logo_path = tmp_path / "logo.png"
    Image.new("RGB", (8, 8), "white").save(logo_path)
    logo = SimpleNamespace(
        id="logo-1",
        role="logo",
        slide_id=None,
        file_path=str(logo_path),
        process_mode="blend",
    )
    slide = SimpleNamespace(
        page_num=1,
        type="cover",
        visual_json={
            "type": "cover",
            "layout": "cover",
            "logo_policy": {"use_as_scene_asset": True, "show_logo": False},
        },
        reference_images=[],
        project=SimpleNamespace(reference_images=[logo], selected_template_recommendations=None),
    )

    refs = _load_reference_images(slide)

    assert [r["role"] for r in refs] == ["logo"]


def test_logo_policy_skips_immersive_hero_pages(tmp_path):
    logo_path = tmp_path / "logo.png"
    template_path = tmp_path / "template.png"
    for path in (logo_path, template_path):
        Image.new("RGB", (8, 8), "white").save(path)

    logo = SimpleNamespace(
        id="logo-1",
        role="logo",
        slide_id=None,
        file_path=str(logo_path),
        process_mode="original",
    )
    project = SimpleNamespace(
        reference_images=[logo],
        selected_template_recommendations={"content": {"file_path": str(template_path)}},
    )
    slide = SimpleNamespace(
        page_num=5,
        type="hero",
        visual_json={"type": "hero", "layout": "hero"},
        reference_images=[],
        project=project,
    )

    refs = _load_reference_images(slide)

    assert "logo" not in [r["role"] for r in refs]


def test_project_refs_follow_page_logo_policy():
    logo = SimpleNamespace(id="logo-1", role="logo", slide_id=None, file_path="/tmp/logo.png", process_mode="original")
    project = SimpleNamespace(reference_images=[logo])

    content_refs = slides_api._project_refs_for_prompt(
        project,
        [],
        {"page_num": 2, "type": "content", "layout": "content_split"},
    )
    hero_refs = slides_api._project_refs_for_prompt(
        project,
        [],
        {"page_num": 3, "type": "hero", "layout": "hero"},
    )

    assert content_refs == []
    assert hero_refs == []


def test_finetune_loads_project_product_asset_when_requested(tmp_path):
    base_path = tmp_path / "base.png"
    asset_path = tmp_path / "product.png"
    for path in (base_path, asset_path):
        Image.new("RGB", (8, 8), "white").save(path)

    visual_asset = SimpleNamespace(
        id="asset-1",
        role="visual_asset",
        slide_id=None,
        file_path=str(asset_path),
        process_mode="crop",
        asset_kind="product",
        asset_name="胡姬花花生油瓶",
        usage_note=None,
    )
    project = SimpleNamespace(reference_images=[visual_asset], selected_template_recommendations=None)
    slide = SimpleNamespace(
        page_num=15,
        type="content",
        visual_json={
            "finetune_base_image_path": str(base_path),
            "finetune_attachment_ids": [],
            "finetune_visual_asset_ids": ["asset-1"],
        },
        reference_images=[],
        project=project,
    )

    refs = _load_reference_images(slide)

    assert [r["role"] for r in refs] == ["finetune_base", "visual_asset"]
    assert refs[1]["asset_name"] == "胡姬花花生油瓶"
    assert refs[1]["process_mode"] == "crop"


def test_direct_finetune_prompt_distinguishes_project_visual_assets():
    slide = Slide(
        page_num=15,
        content_json={
            "text_content": {
                "headline": "全年脉冲式投放",
                "subhead": "全年不断线",
                "body": "品牌宣传说明",
            }
        },
    )

    prompt = slides_api._build_direct_finetune_prompt(
        slide,
        "把油瓶换成我上传的核心资产",
        attachment_count=0,
        project_visual_asset_count=1,
    )

    assert "protected project product/material assets" in prompt
    assert "authoritative source" in prompt
    assert "conflicting brand/product already visible" in prompt


def test_generate_one_slide_uses_single_pass_by_default(tmp_path, monkeypatch):
    calls = []

    def fake_generate_slide_image(prompt, reference_images=None, resolution="4K", aspect_ratio="16:9"):
        calls.append({
            "prompt": prompt,
            "reference_count": len(reference_images or []),
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
        })
        color = "green" if len(calls) == 2 else "blue"
        return Image.new("RGB", (16, 9), color)

    monkeypatch.setattr(generation_pipeline, "generate_slide_image", fake_generate_slide_image)

    slide = Slide(
        page_num=4,
        prompt_text="draft prompt",
        content_json={"text_content": {"headline": "场景展示"}},
        visual_json={"visual_asset_usage": {"asset-1": "放在中央场景区"}},
    )
    ref_data = [
        {
            "id": "asset-1",
            "role": "visual_asset",
            "process_mode": "blend",
            "asset_kind": "scene",
            "asset_name": "终端货架",
            "image": Image.new("RGB", (8, 8), "white"),
        }
    ]

    result = _generate_one_slide(slide, "project-1", str(tmp_path), ref_data)

    assert result["error"] is None
    assert len(calls) == 1
    assert calls[0]["prompt"] == "draft prompt"
    assert calls[0]["reference_count"] == 1

    final_img = Image.open(result["image_path"])
    assert final_img.getpixel((0, 0)) == (0, 0, 255)
    assert not (tmp_path / "project-1" / "slide_04_base.png").exists()


def test_generate_one_slide_uses_hidden_product_refinement_pass(tmp_path, monkeypatch):
    calls = []

    def fake_generate_slide_image(prompt, reference_images=None, resolution="4K", aspect_ratio="16:9"):
        calls.append({
            "prompt": prompt,
            "reference_count": len(reference_images or []),
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
        })
        color = "green" if len(calls) == 2 else "blue"
        return Image.new("RGB", (16, 9), color)

    monkeypatch.setattr(generation_pipeline, "generate_slide_image", fake_generate_slide_image)

    slide = Slide(
        page_num=4,
        prompt_text="draft prompt",
        content_json={"text_content": {"headline": "产品展示"}},
        visual_json={"visual_asset_usage": {"asset-1": "Place the uploaded product image at center-right."}},
    )
    ref_data = [
        {
            "id": "asset-1",
            "role": "visual_asset",
            "process_mode": "crop",
            "asset_kind": "product",
            "asset_name": "胡姬花花生油瓶",
            "file_path": "/tmp/uploads/huji-product.png",
            "image": Image.new("RGB", (8, 8), "white"),
        }
    ]

    result = _generate_one_slide(slide, "project-1", str(tmp_path), ref_data)

    assert result["error"] is None
    assert len(calls) == 2
    assert "FIRST PASS" in calls[0]["prompt"]
    assert "second hidden refinement pass" in calls[0]["prompt"]
    assert calls[0]["reference_count"] == 1
    assert calls[1]["prompt"] == "用第2张及后续参考图替换第一张PPT图中的产品。参考素材路径：/tmp/uploads/huji-product.png"
    assert calls[1]["reference_count"] == 2

    final_img = Image.open(result["image_path"])
    assert final_img.getpixel((0, 0)) == (0, 128, 0)
    assert (tmp_path / "project-1" / "slide_04_base.png").exists()


def test_product_refinement_pass_accepts_multiple_product_refs(tmp_path, monkeypatch):
    calls = []

    def fake_generate_slide_image(prompt, reference_images=None, resolution="4K", aspect_ratio="16:9"):
        calls.append({"prompt": prompt, "reference_count": len(reference_images or [])})
        return Image.new("RGB", (16, 9), "green" if len(calls) == 2 else "blue")

    monkeypatch.setattr(generation_pipeline, "generate_slide_image", fake_generate_slide_image)

    slide = Slide(page_num=6, prompt_text="draft prompt", visual_json={})
    ref_data = [
        {
            "id": "asset-1",
            "role": "visual_asset",
            "asset_kind": "product",
            "asset_name": "产品 A",
            "file_path": "/tmp/uploads/product-a.png",
            "image": Image.new("RGB", (8, 8), "white"),
        },
        {
            "id": "asset-2",
            "role": "visual_asset",
            "asset_kind": "material",
            "asset_name": "产品 B",
            "file_path": "/tmp/uploads/product-b.png",
            "image": Image.new("RGB", (8, 8), "black"),
        },
    ]

    result = _generate_one_slide(slide, "project-2", str(tmp_path), ref_data)

    assert result["error"] is None
    assert len(calls) == 2
    assert calls[1]["prompt"] == "用第2张及后续参考图替换第一张PPT图中的产品。参考素材路径：/tmp/uploads/product-a.png; /tmp/uploads/product-b.png"
    assert calls[1]["reference_count"] == 3


def test_image_cache_key_includes_reference_image_content():
    white_ref = Image.new("RGB", (8, 8), "white")
    black_ref = Image.new("RGB", (8, 8), "black")

    assert (
        _cache_key("same prompt", [white_ref], "4K", "16:9")
        != _cache_key("same prompt", [black_ref], "4K", "16:9")
    )


def test_product_visual_asset_recall_uses_generic_product_page_when_single_asset():
    recalled = _recall_visual_assets_for_page(
        {
            "page_num": 4,
            "type": "content",
            "text_content": {
                "headline": "终端货架：用产品实物建立购买记忆",
                "body": "包装、瓶身和导购体验台是本页核心画面。",
            },
        },
        [
            {
                "id": "asset-1",
                "name": "胡姬花花生油瓶",
                "kind": "product",
                "usage_note": "用于产品、包装、货架、终端展示页",
                "analysis_summary": "keywords=胡姬花、花生油、油瓶、包装、货架",
            }
        ],
    )

    assert recalled[0]["id"] == "asset-1"


def test_visual_plan_auto_adds_recalled_product_asset_when_llm_misses(monkeypatch):
    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**_kwargs):
                    return SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                message=SimpleNamespace(
                                    content=(
                                        '{"7": {'
                                        '"visual_evidence": "终端货架、产品包装和体验台", '
                                        '"visual_summary": "产品货架体验画面", '
                                        '"visual_description": "以终端货架和体验台组织画面，突出产品实物展示。", '
                                        '"visual_asset_ids": [], '
                                        '"visual_asset_usage": {}'
                                        '}}'
                                    )
                                )
                            )
                        ]
                    )

    import app.services.visual_plan as visual_plan_module

    monkeypatch.setattr(visual_plan_module, "get_llm_client", lambda: FakeClient())
    monkeypatch.setattr(visual_plan_module, "_load_style", lambda _style_id: {"meta": {}, "body": ""})

    plan = _do_generate_visual_plan(
        content_plan=[
            {
                "page_num": 7,
                "type": "content",
                "text_content": {
                    "headline": "终端货架体验",
                    "body": "用产品实物、包装和小油瓶建立购买记忆",
                },
            }
        ],
        global_visual_assets=[
            {
                "id": "asset-1",
                "name": "胡姬花花生油瓶",
                "kind": "product",
                "process_mode": "crop",
                "usage_note": "用于产品、包装、货架、终端展示页",
                "analysis_summary": "keywords=胡姬花、花生油、油瓶、包装、货架",
            }
        ],
    )

    assert plan[0]["visual_asset_ids"] == ["asset-1"]
    assert "uploaded product image" in plan[0]["visual_asset_usage"]["asset-1"]
    assert "胡姬花花生油瓶" not in plan[0]["visual_asset_usage"]["asset-1"]


def test_prototype_without_selected_pages_samples_first_three_and_ignores_seed_flags():
    slides = [
        Slide(page_num=1, visual_json={"is_seed_recommended": False}),
        Slide(page_num=2, visual_json={"is_seed_recommended": False}),
        Slide(page_num=3, visual_json={"is_seed_recommended": False}),
        Slide(page_num=4, visual_json={"is_seed_recommended": True}),
    ]

    assert _resolve_generation_page_nums(slides, None, True) == [1, 2, 3]
    assert _resolve_generation_page_nums(slides, [2, 4], True) == [2, 4]
    assert _resolve_generation_page_nums(slides, None, False) is None


def test_confirm_content_plan_advances_backend_stage():
    db = make_session()
    project = Project(title="Confirm flow", status="planning", content_plan_confirmed=False)
    db.add(project)
    db.flush()
    db.add(Slide(project_id=project.id, page_num=1, status="pending", content_json={"page_num": 1}))
    db.commit()

    updated = projects_api.update_project(
        project.id,
        ProjectUpdate(content_plan_confirmed=True),
        db=db,
    )

    assert updated.content_plan_confirmed is True
    assert updated.status == "visual_ready"


def test_content_edit_reopens_confirmation_and_clears_downstream_outputs():
    db = make_session()
    project = Project(
        title="Content edit",
        status="prompt_ready",
        content_plan_confirmed=True,
        selected_style={"name": "Old"},
        style_proposal={"proposals": [{"name": "Old"}]},
    )
    db.add(project)
    db.flush()
    slide = Slide(
        project_id=project.id,
        page_num=1,
        status="completed",
        content_json={"page_num": 1, "text_content": {"headline": "旧标题"}},
        visual_json={"visual_description": "old visual"},
        prompt_text="old prompt",
        image_path="/tmp/old.png",
    )
    db.add(slide)
    db.commit()

    slides_api.update_slide_content(
        project.id,
        slides_api.UpdateContentRequest(
            page_num=1,
            content_json={"text_content": {"headline": "新标题"}},
        ),
        db=db,
    )
    refreshed_project = db.query(Project).filter(Project.id == project.id).first()
    refreshed_slide = db.query(Slide).filter(Slide.id == slide.id).first()

    assert refreshed_project.status == "planning"
    assert refreshed_project.content_plan_confirmed is False
    assert refreshed_project.selected_style is None
    assert refreshed_project.style_proposal is None
    assert refreshed_slide.visual_json == {}
    assert refreshed_slide.prompt_text is None
    assert refreshed_slide.image_path is None
    assert refreshed_slide.status == "pending"


def test_style_reference_upload_after_confirmation_stays_in_visual_stage(tmp_path, monkeypatch):
    db = make_session()
    project = Project(
        title="Style upload",
        status="prompt_ready",
        content_plan_confirmed=True,
        selected_style={"name": "Old"},
        style_proposal={"proposals": [{"name": "Old"}]},
    )
    db.add(project)
    db.flush()
    slide = Slide(
        project_id=project.id,
        page_num=1,
        status="completed",
        content_json={"page_num": 1},
        visual_json={"visual_description": "old visual"},
        prompt_text="old prompt",
        image_path="/tmp/old.png",
    )
    db.add(slide)
    db.commit()
    monkeypatch.setattr(slides_api.settings, "UPLOAD_DIR", str(tmp_path))

    slides_api.upload_file(
        project.id,
        png_upload("style.png"),
        role="style_ref",
        slide_id=None,
        process_mode=None,
        asset_name=None,
        asset_kind=None,
        usage_note=None,
        db=db,
    )
    refreshed_project = db.query(Project).filter(Project.id == project.id).first()
    refreshed_slide = db.query(Slide).filter(Slide.id == slide.id).first()

    assert refreshed_project.status == "visual_ready"
    assert refreshed_project.content_plan_confirmed is True
    assert refreshed_project.selected_style is None
    assert refreshed_project.style_proposal is None
    assert refreshed_slide.visual_json == {}
    assert refreshed_slide.prompt_text is None
    assert refreshed_slide.image_path is None
    assert refreshed_slide.status == "pending"


def test_visual_edit_invalidates_prompt_and_image_only():
    db = make_session()
    project = Project(title="Visual edit", status="completed", content_plan_confirmed=True, selected_style={"name": "Brand"})
    db.add(project)
    db.flush()
    slide = Slide(
        project_id=project.id,
        page_num=1,
        status="completed",
        content_json={"page_num": 1, "text_content": {"headline": "标题"}},
        visual_json={"visual_description": "old visual"},
        prompt_text="old prompt",
        image_path="/tmp/old.png",
    )
    db.add(slide)
    db.commit()

    slides_api.update_slide_visual(
        project.id,
        slides_api.UpdateVisualRequest(
            page_num=1,
            visual_json={"visual_description": "new visual"},
        ),
        db=db,
    )
    refreshed_project = db.query(Project).filter(Project.id == project.id).first()
    refreshed_slide = db.query(Slide).filter(Slide.id == slide.id).first()

    assert refreshed_project.status == "visual_ready"
    assert refreshed_project.content_plan_confirmed is True
    assert refreshed_project.selected_style == {"name": "Brand"}
    assert refreshed_slide.visual_json["visual_description"] == "new visual"
    assert refreshed_slide.prompt_text is None
    assert refreshed_slide.image_path is None
    assert refreshed_slide.status == "visual_ready"


def test_visual_asset_upload_defaults_crop_but_honors_explicit_blend(tmp_path, monkeypatch):
    db = make_session()
    project = Project(title="Asset upload", status="completed", selected_style={"name": "Brand"})
    db.add(project)
    db.flush()
    db.add(
        Slide(
            project_id=project.id,
            page_num=1,
            status="completed",
            visual_json={"visual_asset_ids": []},
            prompt_text="old prompt",
        )
    )
    db.commit()

    monkeypatch.setattr(slides_api.settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(
        slides_api,
        "analyze_visual_asset",
        lambda *_args, **_kwargs: {
            "detected_kind": "product",
            "subject": "测试产品瓶",
            "description": "测试产品",
            "distinctive_features": ["瓶身"],
            "suggested_keywords": ["产品"],
        },
    )

    default_result = slides_api.upload_file(
        project.id,
        png_upload("product.png"),
        role="visual_asset",
        slide_id=None,
        process_mode=None,
        asset_name=None,
        asset_kind="product",
        usage_note=None,
        db=db,
    )
    explicit_result = slides_api.upload_file(
        project.id,
        png_upload("product-hero.png"),
        role="visual_asset",
        slide_id=None,
        process_mode="blend",
        asset_name=None,
        asset_kind="product",
        usage_note=None,
        db=db,
    )
    refreshed_project = db.query(Project).filter(Project.id == project.id).first()
    refreshed_slide = db.query(Slide).filter(Slide.project_id == project.id).first()

    assert default_result["process_mode"] == "crop"
    assert explicit_result["process_mode"] == "blend"
    assert refreshed_project.selected_style == {"name": "Brand"}
    assert refreshed_project.status == "visual_ready"
    assert refreshed_slide.prompt_text is None


def test_visual_asset_upload_rejects_slide_level_asset(tmp_path, monkeypatch):
    db = make_session()
    project = Project(title="Asset upload", status="planning")
    db.add(project)
    db.flush()
    slide = Slide(project_id=project.id, page_num=1, status="pending")
    db.add(slide)
    db.commit()

    monkeypatch.setattr(slides_api.settings, "UPLOAD_DIR", str(tmp_path))

    with pytest.raises(HTTPException) as exc:
        slides_api.upload_file(
            project.id,
            png_upload("product.png"),
            role="visual_asset",
            slide_id=slide.id,
            process_mode=None,
            asset_name=None,
            asset_kind=None,
            usage_note=None,
            db=db,
        )

    assert exc.value.status_code == 400
    assert "project-level" in exc.value.detail


def test_delete_visual_asset_cleans_slide_selection_and_invalidates_outputs(tmp_path):
    db = make_session()
    asset_path = tmp_path / "asset.png"
    Image.new("RGB", (10, 10), "white").save(asset_path)
    project = Project(title="Asset delete", status="completed", selected_style={"name": "Brand"})
    db.add(project)
    db.flush()
    asset = ReferenceImage(
        project_id=project.id,
        role="visual_asset",
        file_path=str(asset_path),
        process_mode="crop",
        asset_name="测试产品瓶",
        asset_kind="product",
    )
    db.add(asset)
    db.flush()
    slide = Slide(
        project_id=project.id,
        page_num=1,
        status="completed",
        visual_json={"visual_asset_ids": [asset.id], "visual_asset_usage": {asset.id: "右侧展示"}},
        prompt_text="old prompt",
    )
    db.add(slide)
    db.commit()

    result = slides_api.delete_reference_image(project.id, asset.id, db=db)
    refreshed_project = db.query(Project).filter(Project.id == project.id).first()
    refreshed_slide = db.query(Slide).filter(Slide.project_id == project.id).first()

    assert result["message"] == "Deleted"
    assert refreshed_slide.visual_json["visual_asset_ids"] == []
    assert refreshed_slide.visual_json["visual_asset_usage"] == {}
    assert refreshed_slide.prompt_text is None
    assert refreshed_slide.status == "visual_ready"
    assert refreshed_project.status == "visual_ready"
