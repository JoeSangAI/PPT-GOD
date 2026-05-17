from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import slides as slides_api
from app.models.base import Base
from app.models.models import Project, Slide
from app.services import prompt_engine
from app.services.content_plan import _normalize_content_markdown


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_prompt_moves_visual_directive_out_of_body_and_keeps_diagram_labels():
    prompt = prompt_engine.generate_prompt_for_page(
        page_intent={
            "page_num": 3,
            "type": "content",
            "layout": "content_split",
            "visual_evidence": "增长动作闭环",
            "visual_description": "围绕增长动作组织画面。",
        },
        content_text={
            "headline": "增长闭环",
            "body": "增长的关键在于四个动作形成闭环。\n用增长飞轮表示：获客、激活、留存、推荐",
        },
        style_text_override="Style: 清晰商务\nPalette: #111111, #FFFFFF",
    )

    assert 'Body: "用增长飞轮表示' not in prompt
    assert "Visual Intent:" in prompt
    assert "用增长飞轮表示" in prompt
    assert 'Diagram label: "获客"' in prompt
    assert 'Diagram label: "推荐"' in prompt
    assert "Do not render visual intent phrases as text." in prompt
    assert "Render diagram labels as visible labels inside the diagram." in prompt


def test_prompt_keeps_non_directive_growth_flywheel_copy_visible():
    prompt = prompt_engine.generate_prompt_for_page(
        page_intent={
            "page_num": 4,
            "type": "content",
            "layout": "content_split",
            "visual_evidence": "模型解释",
            "visual_description": "文字区解释模型含义。",
        },
        content_text={
            "headline": "模型解释",
            "body": "增长飞轮是帮助团队理解复利增长的模型。",
        },
        style_text_override="Style: 清晰商务\nPalette: #111111, #FFFFFF",
    )

    assert 'Body: "增长飞轮是帮助团队理解复利增长的模型。"' in prompt
    assert "Visual Intent:" not in prompt


def test_prompt_does_not_invite_rendering_prompt_metadata_as_slide_text():
    prompt = prompt_engine.generate_prompt_for_page(
        page_intent={
            "page_num": 1,
            "type": "cover",
            "layout": "cover",
            "visual_evidence": "品牌名称大字+运动速度线背景",
            "visual_description": "深蓝背景，荧光绿速度线，主标题纯白高对比。",
        },
        content_text={"headline": "疯火轮 AI"},
        style_text_override=(
            "Style: 运动机能风\n"
            "Palette: #002B9A, #CCFF00, #0A0A0A, #FFFFFF\n"
            "Typography: 中文：Source Han Sans Bold / 英文：Inter Bold\n"
            "Visual rhythm: 速度线、箭头、网格。"
        ),
    )

    assert 'Headline: "疯火轮 AI"' in prompt
    assert "All listed text must appear" not in prompt
    assert "16:9" not in prompt
    assert "Source Han Sans" not in prompt
    assert "Inter Bold" not in prompt
    assert "font family names" in prompt


def test_prompt_drops_absent_text_slots_from_visual_description():
    prompt = prompt_engine.generate_prompt_for_page(
        page_intent={
            "page_num": 1,
            "type": "cover",
            "layout": "cover",
            "visual_evidence": "品牌名称大字+速度线背景",
            "visual_description": (
                "中央位置放置品牌名「疯火轮 AI」采用粗壮字体，颜色为纯白。"
                "页面副标题/说明文字采用高亮荧光绿，与深蓝背景保持高对比。"
                "背景叠加网格纹理。"
            ),
        },
        content_text={"headline": "疯火轮 AI"},
        style_text_override="Style: 运动机能风\nPalette: #002B9A, #CCFF00",
    )

    assert 'Headline: "疯火轮 AI"' in prompt
    assert "页面副标题" not in prompt
    assert "说明文字" not in prompt
    assert "invented copy" in prompt


def test_prompt_drops_style_microcopy_cues():
    prompt = prompt_engine.generate_prompt_for_page(
        page_intent={
            "page_num": 1,
            "type": "cover",
            "layout": "cover",
            "visual_evidence": "品牌名称大字+速度线背景",
            "visual_description": "中央品牌名，深蓝背景，荧光绿速度线。",
        },
        content_text={"headline": "疯火轮 AI"},
        style_text_override=(
            "Style: 运动机能风\n"
            "Palette: #002B9A, #CCFF00\n"
            "Visual rhythm: 锯齿边缘、箭头、赛道线条贯穿全篇；大面积分隔、小字号留白，让信息清晰可读。"
        ),
    )

    assert "小字号" not in prompt
    assert "decorative microtext" in prompt
    assert "lorem ipsum" in prompt


def test_prompt_does_not_expose_logo_placeholder_words_without_logo_reference():
    prompt = prompt_engine.generate_prompt_for_page(
        page_intent={
            "page_num": 1,
            "type": "cover",
            "layout": "cover",
            "visual_evidence": "品牌名称大字+速度线背景",
            "visual_description": "中央品牌名，深蓝背景，荧光绿速度线。",
            "logo_policy": {"show_logo": True, "placement": "top-right", "scale": "small"},
        },
        content_text={"headline": "疯火轮 AI"},
        style_text_override="Style: 运动机能风\nPalette: #002B9A, #CCFF00",
    )

    assert "Logo Placement Note" not in prompt
    assert "Brand marks:" not in prompt
    assert "brand signature" not in prompt.lower()
    assert "safe corner" not in prompt.lower()
    assert "logo" not in prompt.lower()
    assert "LOGO" not in prompt


def test_prompt_preserves_visible_copy_when_directive_is_inline():
    prompt = prompt_engine.generate_prompt_for_page(
        page_intent={
            "page_num": 6,
            "type": "content",
            "layout": "content_split",
            "visual_evidence": "增长动作闭环",
            "visual_description": "围绕增长动作组织画面。",
        },
        content_text={
            "headline": "增长闭环",
            "body": "增长的关键在于四个动作形成闭环。 用增长飞轮表示：获客、激活、留存、推荐",
        },
        style_text_override="Style: 清晰商务\nPalette: #111111, #FFFFFF",
    )

    assert 'Body: "增长的关键在于四个动作形成闭环。"' in prompt
    assert 'Body: "增长的关键在于四个动作形成闭环。 用增长飞轮表示' not in prompt
    assert "用增长飞轮表示" in prompt
    assert 'Diagram label: "获客"' in prompt
    assert 'Diagram label: "推荐"' in prompt


def test_prompt_keeps_visual_contract_when_only_visual_intent_exists():
    prompt = prompt_engine.generate_prompt_for_page(
        page_intent={
            "page_num": 5,
            "type": "content",
            "layout": "content_full",
            "visual_evidence": "增长飞轮",
            "visual_description": "围绕增长动作组织画面。",
        },
        content_text={
            "visual_requirements": [{"directive": "用增长飞轮表示"}],
        },
        style_text_override="Style: 清晰商务\nPalette: #111111, #FFFFFF",
    )

    assert "Visual Intent:" in prompt
    assert "用增长飞轮表示" in prompt
    assert "Do not render visual intent phrases as text." in prompt
    assert 'Body: "用增长飞轮表示"' not in prompt


def test_prompt_supports_other_explicit_diagram_forms():
    prompt = prompt_engine.generate_prompt_for_page(
        page_intent={
            "page_num": 7,
            "type": "content",
            "layout": "content_split",
            "visual_evidence": "产品推进路径",
            "visual_description": "围绕阶段推进组织画面。",
        },
        content_text={
            "headline": "产品路线",
            "body": "用路线图表示：验证、上线、扩张",
        },
        style_text_override="Style: 清晰商务\nPalette: #111111, #FFFFFF",
    )

    assert "Visual Intent:" in prompt
    assert "用路线图表示" in prompt
    assert 'Diagram label: "验证"' in prompt
    assert 'Diagram label: "扩张"' in prompt


def test_content_plan_normalization_moves_obvious_visual_directive_to_visual_suggestion():
    outline = [
        {
            "page_num": 1,
            "type": "content",
            "text_content": {
                "headline": "增长闭环",
                "subhead": "",
                "body": "增长的关键在于四个动作形成闭环。\n用增长飞轮表示：获客、激活、留存、推荐",
            },
            "visual_suggestion": "",
        }
    ]

    normalized = _normalize_content_markdown(outline)
    page = normalized[0]

    assert "增长的关键在于四个动作形成闭环" in page["text_content"]["body"]
    assert "用增长飞轮表示" not in page["text_content"]["body"]
    assert "用增长飞轮表示" in page["visual_suggestion"]
    assert page["visual_requirements"][0]["directive"] == "用增长飞轮表示"
    assert page["visual_requirements"][0]["diagram_labels"] == ["获客", "激活", "留存", "推荐"]


def test_update_slide_content_returns_visual_directive_suggestion_without_mutating_body():
    db = make_session()
    project = Project(title="Directive suggestions", status="planning")
    db.add(project)
    db.flush()
    slide = Slide(
        project_id=project.id,
        page_num=1,
        type="content",
        content_json={"page_num": 1, "text_content": {"headline": "旧标题", "body": ""}},
        visual_json={},
    )
    db.add(slide)
    db.commit()

    body = "增长的关键在于四个动作形成闭环。\n用增长飞轮表示：获客、激活、留存、推荐"
    result = slides_api.update_slide_content(
        project.id,
        slides_api.UpdateContentRequest(
            page_num=1,
            slide_id=slide.id,
            content_json={"text_content": {"headline": "增长闭环", "body": body}},
        ),
        db,
    )

    db.refresh(slide)
    suggestions = result["visual_directive_suggestions"]
    assert len(suggestions) == 1
    assert suggestions[0]["directive"] == "用增长飞轮表示"
    assert suggestions[0]["diagram_labels"] == ["获客", "激活", "留存", "推荐"]
    assert slide.content_json["text_content"]["body"] == body
