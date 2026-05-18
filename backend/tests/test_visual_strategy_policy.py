from pathlib import Path

from PIL import Image, ImageDraw

from app.models.models import Slide
from app.services.generation_pipeline import _collect_existing_seeds, _slide_family
from app.services import prompt_engine
from app.services.style_pack import style_pack_from_selected_style
from app.services.style_proposal import _build_reference_clone_proposal
from app.services.visual_plan import _fallback_visual_plan, _infer_seed_family
from app.services.visual_strategy import detect_logo_tone_from_image, visual_language_group


def test_dark_tech_reference_with_light_logo_keeps_dark_content_strategy():
    summary = {
        "industries": ["科技/数据"],
        "keywords": ["AI", "数据"],
        "style_direction_hint": "内容核心偏科技/数据/AI，可考虑冷色、秩序感、数据化的现代视觉方向。",
        "dense_page_ratio": 0.2,
        "table_page_ratio": 0.1,
    }
    assets = {
        "logo_analysis": {"logo_tone": "light", "description": "白色文字标识"},
        "reference_analysis": {
            "style_name": "霓虹噪点弥散流光风格",
            "colors": {
                "background": "#050814",
                "primary": "#FF26B9",
                "accent": "#7000FF",
                "text": "#FFFFFF",
            },
            "dominant_palette": [{"hex": "#000010", "share": 0.4}],
            "mood": "梦幻、高能、流动、神秘、前卫",
            "clone_rules": "深黑底色配高亮霓虹渐变；全屏覆盖细腻噪点颗粒。",
            "description": "深暗背景与高饱和玫紫渐变的视觉系统。",
        },
    }

    proposal = _build_reference_clone_proposal(summary, assets)

    assert proposal["visual_strategy"]["base_tone"] == "dark"
    assert "#F7F3EA" not in proposal["page_type_adaptation"]
    assert "自动切成浅色" in proposal["page_type_adaptation"]
    assert "Logo 偏浅" in proposal["visual_strategy"]["logo_contrast"]


def test_ai_tech_weak_reference_uses_neutral_modern_defaults():
    summary = {
        "industries": ["科技/数据"],
        "keywords": ["AI", "数据", "云计算"],
        "style_direction_hint": "内容核心偏科技/数据/AI，可考虑冷色、秩序感、数据化的现代视觉方向。",
        "dense_page_ratio": 0.2,
        "table_page_ratio": 0.1,
    }
    assets = {
        "reference_analysis": {
            "colors": {"primary": "#6D4CFF", "background": "#F7F8FB", "text": "#111827"},
            "dominant_palette": [{"hex": "#6D4CFF", "share": 0.4}],
        },
    }

    proposal = _build_reference_clone_proposal(summary, assets)
    combined = " ".join(
        str(proposal.get(key) or "")
        for key in ("name", "mood", "font", "description", "clone_rules", "ornaments", "texture")
    )

    assert proposal["name"] == "参考图风格基因"
    # LLM thick: code layer no longer infers mood/font from palette traits.
    for unwanted in ("古朴", "宋体/书法体", "中式", "国潮", "山水"):
        assert unwanted not in combined


def test_reference_texture_and_typography_survive_to_compact_prompt():
    selected_style = {
        "name": "霓虹噪点弥散流光",
        "palette": [{"hex": "#050814"}, {"hex": "#FF26B9"}, {"hex": "#7000FF"}],
        "mood": "梦幻、高能、流动、前卫",
        "font": "Headline Inter Display Semibold; Body Inter Regular; same family across all pages.",
        "texture": "soft blur glow, fine grain, diffuse neon haze",
        "page_type_adaptation": "内容页保持深色科技基底和高对比信息卡片。",
    }
    style_text = style_pack_from_selected_style(selected_style)

    prompt = prompt_engine.generate_prompt_for_page(
        page_intent={
            "page_num": 2,
            "type": "toc",
            "layout": "toc",
            "visual_evidence": "AI 公司目录页，五个模块的结构化导航",
            "visual_description": "深色科技目录页，细腻模糊光晕背景。",
        },
        content_text={"headline": "内容导览", "body": ["使命愿景", "产品矩阵", "商业模式"]},
        style_text_override=style_text,
    )

    assert "Typography:" in prompt
    assert "clean sans-serif hierarchy" in prompt
    assert "strong headline weight" in prompt
    assert "Inter Display" not in prompt
    assert "Inter Regular" not in prompt
    assert "font family names" in prompt
    assert "Texture/material:" in prompt
    assert "soft blur glow" in prompt


def test_logo_tone_detection_uses_visible_pixels(tmp_path: Path):
    logo_path = tmp_path / "white-logo.png"
    img = Image.new("RGBA", (120, 60), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle((10, 15, 110, 45), fill=(245, 245, 245, 255))
    img.save(logo_path)

    result = detect_logo_tone_from_image(str(logo_path))

    assert result["logo_tone"] == "light"
    assert result["logo_light_pixel_share"] > 0.5


def test_seed_collection_isolated_by_visual_language_group(tmp_path: Path):
    dark_path = tmp_path / "dark.png"
    light_path = tmp_path / "light.png"
    Image.new("RGB", (16, 9), "black").save(dark_path)
    Image.new("RGB", (16, 9), "white").save(light_path)
    dark_slide = Slide(
        page_num=2,
        type="content",
        status="completed",
        image_path=str(dark_path),
        visual_json={"seed_family": "content", "visual_language_group": "dark_content", "is_seed_recommended": True},
    )
    light_slide = Slide(
        page_num=3,
        type="content",
        status="completed",
        image_path=str(light_path),
        visual_json={"seed_family": "content", "visual_language_group": "light_content", "is_seed_recommended": True},
    )

    seeds = _collect_existing_seeds([dark_slide, light_slide])

    assert seeds[("content", "dark_content")] == [str(dark_path)]
    assert seeds[("content", "light_content")] == [str(light_path)]


def test_section_toc_and_data_are_separate_seed_families():
    assert _infer_seed_family("toc") == "toc"
    assert _infer_seed_family("section") == "section"
    assert _infer_seed_family("data") == "data"

    content_plan = [
        {"page_num": 1, "type": "cover", "text_content": {"headline": "主标题", "body": ""}},
        {"page_num": 2, "type": "toc", "text_content": {"headline": "目录", "body": "- 第一章\n- 第二章"}},
        {"page_num": 3, "type": "section", "text_content": {"headline": "第一章 机会窗口", "body": ""}},
        {"page_num": 4, "type": "content", "text_content": {"headline": "用户需求变化", "body": "- A\n- B"}},
        {"page_num": 5, "type": "data", "text_content": {"headline": "增长数据", "body": "| 年份 | GMV |\n| --- | --- |\n| 2025 | 100 |"}},
        {"page_num": 6, "type": "ending", "text_content": {"headline": "谢谢", "body": ""}},
    ]

    plan = _fallback_visual_plan(content_plan, [], "base_tone=dark")
    by_page = {item["page_num"]: item for item in plan}

    assert by_page[2]["seed_family"] == "toc"
    assert by_page[3]["seed_family"] == "section"
    assert by_page[3]["layout"] == "section"
    assert by_page[3]["visual_language_group"] == "dark_section"
    assert by_page[5]["seed_family"] == "data"
    assert by_page[5]["visual_language_group"] == "dark_data"

    recommended = {
        item["seed_family"]: item["page_num"]
        for item in plan
        if item.get("is_seed_recommended")
    }
    assert recommended == {
        "cover": 1,
        "toc": 2,
        "section": 3,
        "content": 4,
        "data": 5,
        "ending": 6,
    }


def test_legacy_slide_family_fallback_keeps_structural_pages_separate():
    assert _slide_family(Slide(page_num=2, type="toc")) == "toc"
    assert _slide_family(Slide(page_num=3, type="section")) == "section"
    assert _slide_family(Slide(page_num=4, type="data")) == "data"
    assert visual_language_group("section", "section", {"base_tone": "light"}) == "light_section"
