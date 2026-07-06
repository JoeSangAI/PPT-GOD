from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import slides as slides_api
from app.models.base import Base
from app.models.models import Project, Slide
from app.services.visual_directives import extract_visual_directives
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


def test_visual_directive_extraction_keeps_negated_chart_instruction_visible():
    result = extract_visual_directives("不要画成流程图；这里要保留原文判断。")

    assert result["suggestions"] == []
    assert result["cleaned_markdown"] == "不要画成流程图；这里要保留原文判断。"


def test_visual_directive_extraction_keeps_negated_matrix_reference_visible():
    result = extract_visual_directives("这不是要做成矩阵，而是在解释矩阵组织的弊端。")

    assert result["suggestions"] == []
    assert result["cleaned_markdown"] == "这不是要做成矩阵，而是在解释矩阵组织的弊端。"


def test_visual_directive_extraction_keeps_tool_use_workflow_copy_visible():
    body = "Agentic AI 则把 Agent 放进真实工作环境，让它开始读写文件、调用工具、串联更长的办公流程。"

    result = extract_visual_directives(body)

    assert result["suggestions"] == []
    assert result["cleaned_markdown"] == body


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
    visible_text_section = prompt.split("Rules:", 1)[0]
    assert "Source Han Sans" not in visible_text_section
    assert "Inter Bold" not in visible_text_section
    assert "Typography contract:" in prompt
    assert "Source Han Sans" in prompt
    assert "Inter Bold" in prompt
    assert "font family names" in prompt


def test_prompt_strips_internal_ppt_reference_metadata_from_visual_description():
    prompt = prompt_engine.generate_prompt_for_page(
        page_intent={
            "page_num": 1,
            "type": "cover",
            "layout": "cover",
            "visual_evidence": (
                "F1 赛车与父亲节礼盒；参考图1（AI 参考）：asset=F1 p1 image；"
                "source=来自上传PPT「F1.pptx」第1页；classification=useful；"
                "area_ratio=0.10819；tags=原PPT素材；source_slide_text=父亲节；"
                "usage=参考原页面视觉关系"
            ),
            "visual_description": (
                "深色赛道背景，父亲节礼盒与赛车速度感同框。"
                "参考图1（AI 参考）：asset=F1 p1 image；"
                "source=来自上传PPT「F1.pptx」第1页；classification=useful；"
                "area_ratio=0.10819；tags=原PPT素材；source_slide_text=父亲节；"
                "usage=参考原页面视觉关系"
            ),
        },
        content_text={"headline": "父亲节竞速礼遇"},
        style_text_override="Style: 高端赛车商务风\nPalette: #050B14, #C8FF2E, #FFFFFF",
    )

    assert "F1 赛车与父亲节礼盒" in prompt
    assert "深色赛道背景" in prompt
    for leaked in [
        "参考图1",
        "AI 参考",
        "asset=",
        "source=来自上传PPT",
        "classification=",
        "area_ratio=",
        "tags=",
        "source_slide_text=",
        "F1.pptx",
    ]:
        assert leaked not in prompt


def test_prompt_sanitizes_content_reference_descriptions():
    prompt = prompt_engine.generate_prompt_for_page(
        page_intent={
            "page_num": 14,
            "type": "content",
            "layout": "image_grid",
            "visual_evidence": "六张活动现场图组成整齐图墙",
            "visual_description": "用多图网格呈现活动现场氛围，文字区保持清晰。",
        },
        content_text={"headline": "线下触点"},
        reference_images=[
            {
                "role": "content_ref",
                "description": (
                    "asset=F1 p14 image; source=来自上传PPT「F1.pptx」第14页; "
                    "classification=useful; group=同页并列图片组 1/6; "
                    "area_ratio=0.08296; tags=原PPT素材; source_slide_text=活动现场; "
                    "usage=作为图墙素材参考"
                ),
            }
        ],
        style_text_override="Style: 高端赛车商务风\nPalette: #050B14, #C8FF2E, #FFFFFF",
    )

    assert "Page reference: use this uploaded image as the page visual source." in prompt
    for leaked in [
        "asset=",
        "source=来自上传PPT",
        "classification=",
        "group=同页并列图片组",
        "area_ratio=",
        "tags=",
        "source_slide_text=",
        "F1.pptx",
    ]:
        assert leaked not in prompt


def test_visible_text_rule_does_not_forbid_layout_diagram_labels():
    prompt = prompt_engine.generate_prompt_for_page(
        page_intent={
            "page_num": 10,
            "type": "content",
            "layout": "process_diagram",
            "visual_evidence": "三段流程图：对话、沉淀、复用",
            "visual_description": "流程节点可标注「对话→产出」「沉淀→知识库」「调用→复用」。",
        },
        content_text={"headline": "把 AI 变成团队资产"},
        style_text_override="Style: 清晰商务\nPalette: #111111, #FFFFFF",
    )

    assert 'Headline: "把 AI 变成团队资产"' in prompt
    assert "render only quoted strings from this section" not in prompt
    assert "invented copy" in prompt
    assert "decorative microtext" in prompt


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
