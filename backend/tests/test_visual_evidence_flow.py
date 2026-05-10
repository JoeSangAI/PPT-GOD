import io

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import projects as projects_api
from app.api import documents as documents_api
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
from app.services.logo_assets import prepare_logo_lockup_image, prepare_logo_overlay_image
from app.services.logo_overlay_layout import resolve_logo_overlay_box
from app.services.logo_policy import logo_policy_for_page
from app.services.pptx_assembler import assemble_pptx
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
from app.services.content_plan import _annotate_ppt_source_refs, _normalize_content_markdown
from app.services.style_proposal import _build_content_style_direction
from app.utils.project_docs import load_project_documents
from types import SimpleNamespace

from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches


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


def txt_upload(name="brief.txt", text="开学季绿色营销推广方案"):
    return SimpleNamespace(filename=name, file=io.BytesIO(text.encode("utf-8")), content_type="text/plain")


def pptx_upload_with_picture(name="source.pptx"):
    image_buf = io.BytesIO()
    img = Image.new("RGB", (240, 140), "white")
    for x in range(20, 220):
        for y in range(25, 115):
            img.putpixel((x, y), (20, 120, 210))
    img.save(image_buf, "PNG")
    image_buf.seek(0)

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_textbox(Inches(0.6), Inches(0.5), Inches(5.0), Inches(0.6)).text = "原 PPT 第 1 页标题"
    slide.shapes.add_picture(image_buf, Inches(1.0), Inches(1.4), width=Inches(3.0))

    out = io.BytesIO()
    prs.save(out)
    out.seek(0)
    return SimpleNamespace(filename=name, file=out, content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")


def pptx_upload_with_primary_product_image(name="product-source.pptx"):
    image_buf = io.BytesIO()
    img = Image.new("RGB", (260, 160), "white")
    for x in range(20, 240):
        for y in range(22, 138):
            img.putpixel((x, y), (30 + (x % 60), 105 + (y % 80), 180))
    img.save(image_buf, "PNG")
    image_buf.seek(0)

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_textbox(Inches(0.6), Inches(0.5), Inches(6.0), Inches(0.6)).text = "产品包装主视觉与终端陈列"
    slide.shapes.add_picture(image_buf, Inches(1.0), Inches(1.4), width=Inches(3.2))

    out = io.BytesIO()
    prs.save(out)
    out.seek(0)
    return SimpleNamespace(filename=name, file=out, content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")


def pptx_upload_with_content_graphic(name="graphic-source.pptx"):
    image_buf = io.BytesIO()
    img = Image.new("RGB", (300, 180), "white")
    for x in range(24, 276):
        for y in range(30, 150):
            if x % 70 < 36 or y % 54 < 24:
                img.putpixel((x, y), (45, 120, 210))
            else:
                img.putpixel((x, y), (230, 235, 245))
    img.save(image_buf, "PNG")
    image_buf.seek(0)

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_textbox(Inches(0.6), Inches(0.5), Inches(7.0), Inches(0.6)).text = "系统截图、数据图表与业务流程图"
    slide.shapes.add_picture(image_buf, Inches(1.0), Inches(1.4), width=Inches(3.4))

    out = io.BytesIO()
    prs.save(out)
    out.seek(0)
    return SimpleNamespace(filename=name, file=out, content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")


def pptx_upload_with_identity_code(name="identity-code-source.pptx"):
    image_buf = io.BytesIO()
    img = Image.new("RGB", (180, 180), "white")
    for x in range(24, 156):
        for y in range(24, 156):
            if (x // 12 + y // 12) % 2 == 0 or (x % 29 < 8 and y % 31 < 8):
                img.putpixel((x, y), (20, 20, 20))
    img.save(image_buf, "PNG")
    image_buf.seek(0)

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_textbox(Inches(0.6), Inches(0.5), Inches(7.0), Inches(0.6)).text = "菜鸟驿站身份码与扫码出库流程"
    slide.shapes.add_picture(image_buf, Inches(1.0), Inches(2.5), width=Inches(1.6))

    out = io.BytesIO()
    prs.save(out)
    out.seek(0)
    return SimpleNamespace(filename=name, file=out, content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")


def pptx_upload_with_phone_frame_chrome(name="phone-frame-source.pptx"):
    image_buf = io.BytesIO()
    img = Image.new("RGBA", (220, 460), (255, 255, 255, 0))
    for x in range(16, 204):
        for y in range(12, 448):
            on_outer = x < 26 or x > 194 or y < 24 or y > 438
            on_inner = 40 < x < 180 and 80 < y < 390 and (x + y) % 17 < 2
            if on_outer:
                img.putpixel((x, y), (30, 30, 30, 255))
            elif on_inner:
                img.putpixel((x, y), (210, 215, 225, 255))
    img.save(image_buf, "PNG")
    image_buf.seek(0)

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_textbox(Inches(0.6), Inches(0.5), Inches(7.0), Inches(0.6)).text = "手机边框承载小程序界面与身份码展示"
    slide.shapes.add_picture(image_buf, Inches(1.0), Inches(1.0), height=Inches(4.6))

    out = io.BytesIO()
    prs.save(out)
    out.seek(0)
    return SimpleNamespace(filename=name, file=out, content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")


def _patterned_png(color):
    buf = io.BytesIO()
    img = Image.new("RGB", (180, 120), "white")
    for x in range(180):
        for y in range(120):
            if (x // 12 + y // 12) % 2 == 0:
                img.putpixel((x, y), color)
            elif 30 < x < 150 and 25 < y < 95:
                img.putpixel((x, y), (30, 30, 30))
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


def pptx_upload_with_many_pictures(name="many.pptx", count=5):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_textbox(Inches(0.4), Inches(0.3), Inches(6.0), Inches(0.6)).text = "渠道策略 产品矩阵 终端陈列"
    colors = [(210, 40, 40), (40, 140, 210), (40, 180, 90), (210, 150, 40), (150, 80, 210)]
    for idx in range(count):
        x = Inches(0.5 + (idx % 3) * 2.0)
        y = Inches(1.1 + (idx // 3) * 1.6)
        slide.shapes.add_picture(_patterned_png(colors[idx % len(colors)]), x, y, width=Inches(1.5))
    out = io.BytesIO()
    prs.save(out)
    out.seek(0)
    return SimpleNamespace(filename=name, file=out, content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")


def pptx_upload_with_two_cover_logos(name="two-logos.pptx"):
    def logo_blob(color, accent):
        buf = io.BytesIO()
        img = Image.new("RGB", (220, 72), "white")
        for x in range(12, 208):
            for y in range(18, 54):
                if x < 62 or (x // 14 + y // 12) % 2 == 0:
                    img.putpixel((x, y), color)
                else:
                    img.putpixel((x, y), accent)
        img.save(buf, "PNG")
        buf.seek(0)
        return buf

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_textbox(Inches(0.7), Inches(1.4), Inches(9.0), Inches(0.8)).text = "开学季绿色营销推广方案"
    slide.shapes.add_picture(logo_blob((35, 80, 170), (20, 20, 20)), Inches(0.65), Inches(0.35), width=Inches(1.45))
    slide.shapes.add_picture(logo_blob((30, 160, 90), (245, 165, 35)), Inches(2.35), Inches(0.35), width=Inches(1.45))

    out = io.BytesIO()
    prs.save(out)
    out.seek(0)
    return SimpleNamespace(filename=name, file=out, content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")


def pptx_upload_with_cropped_cover_logo(name="cropped-logo.pptx"):
    logo_buf = io.BytesIO()
    img = Image.new("RGB", (440, 72), "white")
    for x in range(16, 204):
        for y in range(18, 54):
            img.putpixel((x, y), (180, 122, 0))
    for x in range(252, 408):
        for y in range(16, 56):
            img.putpixel((x, y), (210, 20, 28))
    img.save(logo_buf, "PNG")
    logo_buf.seek(0)

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_textbox(Inches(0.7), Inches(1.4), Inches(9.0), Inches(0.8)).text = "校园联播网媒体介绍"
    logo = slide.shapes.add_picture(logo_buf, Inches(0.65), Inches(6.8), width=Inches(2.8))
    logo.crop_right = 0.5

    out = io.BytesIO()
    prs.save(out)
    out.seek(0)
    return SimpleNamespace(filename=name, file=out, content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")


def pptx_upload_with_cropped_multicolor_cover_logo(name="cropped-multicolor-logo.pptx"):
    logo_buf = io.BytesIO()
    img = Image.new("RGB", (860, 260), "white")
    for x in range(35, 455):
        for y in range(52, 212):
            if x < 90 or y < 74 or y > 190 or (x // 11 + y // 9) % 3 == 0:
                img.putpixel((x, y), (184, 126, 8))
            elif (x + y) % 17 < 4:
                img.putpixel((x, y), (80, 40, 12))
    for x in range(500, 810):
        for y in range(50, 210):
            if (x - 500) ** 2 + (y - 130) ** 2 < 75 ** 2:
                img.putpixel((x, y), (30, 95, 210))
            elif x % 19 < 7:
                img.putpixel((x, y), (220, 30, 45))
    logo_buf.seek(0)
    img.save(logo_buf, "PNG")
    logo_buf.seek(0)

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_textbox(Inches(0.7), Inches(1.4), Inches(9.0), Inches(0.8)).text = "Focus Media & Cainiao Station 2026 SPRING"
    logo = slide.shapes.add_picture(logo_buf, Inches(0.65), Inches(6.75), width=Inches(1.28))
    logo.crop_left = 0.08
    logo.crop_top = 0.24
    logo.crop_right = 0.45
    logo.crop_bottom = 0.22

    out = io.BytesIO()
    prs.save(out)
    out.seek(0)
    return SimpleNamespace(filename=name, file=out, content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")


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


def test_logo_lockup_combines_multiple_marks(tmp_path):
    paths = []
    for idx, color in enumerate([(150, 0, 0), (0, 120, 80)]):
        path = tmp_path / f"logo_{idx}.png"
        img = Image.new("RGB", (120, 72), "white")
        for x in range(18, 102):
            for y in range(20, 52):
                img.putpixel((x, y), color)
        img.save(path)
        paths.append(str(path))

    lockup_path = prepare_logo_lockup_image(paths)
    lockup = Image.open(lockup_path).convert("RGBA")

    assert lockup.width > lockup.height
    assert lockup.getchannel("A").getextrema()[1] == 255


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


def test_punchline_page_content_is_normalized_to_one_line():
    outline = _normalize_content_markdown([
        {
            "page_num": 3,
            "type": "hero",
            "text_content": {
                "headline": "管理就是把复杂留给自己",
                "subhead": "",
                "body": "- 第一条解释\n- 第二条解释",
            },
            "speaker_notes": "用于章节转折。",
            "visual_suggestion": "内容页信息图，展示三点列表",
        }
    ])

    page = outline[0]
    assert page["text_content"]["headline"] == "管理就是把复杂留给自己"
    assert page["text_content"]["subhead"] == ""
    assert page["text_content"]["body"] == ""
    assert "原金句页正文素材" in page["speaker_notes"]
    assert "金句页" in page["visual_suggestion"]
    assert "整套 PPT" in page["visual_suggestion"]


def test_single_ppt_outline_gets_source_refs_when_polishing():
    outline = [
        {"page_num": 1, "type": "cover", "text_content": {"headline": "封面", "body": ""}},
        {"page_num": 2, "type": "content", "text_content": {"headline": "策略", "body": "原内容"}},
    ]
    documents = '--- PPT_SOURCE filename="source.pptx" pages=2 ---\n\n--- 第1页 ---\n封面\n\n--- 第2页 ---\n策略'

    annotated = _annotate_ppt_source_refs(outline, documents, "请美化这个 PPT")

    assert annotated[0]["source_refs"] == [{
        "source_document": "source.pptx",
        "source_page_num": 1,
        "source_type": "pptx_slide",
        "reason": "single_ppt_page_polish",
    }]
    assert annotated[1]["source_refs"][0]["source_page_num"] == 2


def test_fallback_visual_plan_treats_hero_as_punchline_slide():
    plan = _fallback_visual_plan(
        [
            {
                "page_num": 5,
                "type": "hero",
                "text_content": {
                    "headline": "现金流就是公司的氧气",
                    "subhead": "来自 Q3 经营复盘",
                    "body": "",
                },
            }
        ],
        [],
    )

    assert plan[0]["layout"] == "hero"
    assert plan[0]["seed_family"] == "hero"
    assert "金句排版" in plan[0]["visual_evidence"]
    assert "可选署名/上下文" in plan[0]["visual_evidence"]
    assert "只保留核心短句" in plan[0]["visual_description"]
    assert plan[0]["logo_policy"]["show_logo"] is False


def test_visual_plan_json_repair_handles_multiline_llm_strings():
    raw = '{"6": {"visual_evidence": "终端货架", "visual_description": "第一行\n第二行", "visual_asset_ids": [], "visual_asset_usage": {}}}'

    parsed = _safe_parse_json(raw, 1)

    assert parsed["6"]["visual_description"] == "第一行\n第二行"


def test_prompt_keeps_exact_text_contract_and_visual_evidence():
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


def test_prompt_includes_exact_overlay_reservation():
    prompt = prompt_engine.generate_prompt_for_page(
        page_intent={
            "page_num": 1,
            "type": "content",
            "layout": "content_split",
            "visual_evidence": "产品截图旁边解释三点价值",
            "visual_description": "右侧保留截图，左侧放文字。",
            "overlay_layers": [{
                "id": "ov",
                "asset_id": "asset-1",
                "enabled": True,
                "preset": "right-card",
            }],
        },
        content_text={"headline": "Exact Overlay", "body": "截图必须原样保留"},
        style_text_override="Style: clean\nPalette: #FFFFFF, #111111",
    )

    assert "Exact Overlay Reservation" in prompt
    assert "right-side card media slot" in prompt
    assert "Do not place required text" in prompt


def test_prompt_skips_exact_overlay_reservation_when_asset_unavailable():
    prompt = prompt_engine.generate_prompt_for_page(
        page_intent={
            "page_num": 1,
            "type": "content",
            "layout": "content_split",
            "visual_evidence": "产品截图旁边解释三点价值",
            "visual_description": "右侧放文字。",
            "available_overlay_asset_ids": [],
            "overlay_layers": [{
                "id": "ov",
                "asset_id": "missing-asset",
                "enabled": True,
                "preset": "right-card",
            }],
        },
        content_text={"headline": "Exact Overlay", "body": "截图不存在时不能预留空槽"},
        style_text_override="Style: clean\nPalette: #FFFFFF, #111111",
    )

    assert "Exact Overlay Reservation" not in prompt
    assert "right-side card media slot" not in prompt


def test_pptx_assembler_adds_exact_overlay_layer(tmp_path):
    bg_path = tmp_path / "slide.png"
    asset_path = tmp_path / "asset.png"
    output_path = tmp_path / "out.pptx"
    Image.new("RGB", (1792, 1024), "white").save(bg_path)
    Image.new("RGB", (320, 180), "red").save(asset_path)

    assemble_pptx(
        slide_images=[{
            "page_num": 1,
            "image_path": str(bg_path),
            "visual_json": {
                "overlay_layers": [{
                    "id": "ov",
                    "asset_id": "asset-1",
                    "enabled": True,
                    "preset": "right-card",
                    "mode": "exact_card",
                }]
            },
        }],
        output_path=str(output_path),
        overlay_assets={"asset-1": {"file_path": str(asset_path)}},
    )

    prs = Presentation(str(output_path))
    # Background image + card rectangle + exact overlay image.
    assert len(prs.slides[0].shapes) >= 3


def test_logo_overlay_layout_avoids_dense_ending_content(tmp_path):
    bg_path = tmp_path / "ending.png"
    logo_path = tmp_path / "logo.png"

    bg = Image.new("RGB", (1792, 1024), (4, 6, 18))
    draw = ImageDraw.Draw(bg)
    # Simulate a centered ending page: title, contact line, and two CTA bands.
    draw.rectangle((470, 250, 1320, 360), fill=(235, 70, 160))
    draw.rectangle((650, 450, 1140, 500), fill=(230, 230, 235))
    draw.rectangle((610, 640, 1180, 710), outline=(205, 80, 255), width=8)
    draw.rectangle((610, 750, 1180, 820), outline=(255, 80, 170), width=8)
    bg.save(bg_path)

    Image.new("RGBA", (520, 330), (255, 255, 255, 255)).save(logo_path)

    resolved = resolve_logo_overlay_box(str(bg_path), str(logo_path), "ending", "lower-center", "large")

    assert resolved is not None
    assert resolved["strategy"].startswith("smart:")
    # The old lower-center rule put the logo into the centered CTA stack.
    assert not (
        0.32 < resolved["left"] < 0.68
        and 0.55 < resolved["top"] < 0.82
    )


def test_cover_center_logo_placement_is_physical_center(tmp_path):
    bg_path = tmp_path / "cover.png"
    logo_path = tmp_path / "logo.png"
    bg = Image.new("RGB", (1792, 1024), (10, 20, 45))
    draw = ImageDraw.Draw(bg)
    draw.rectangle((80, 260, 780, 430), fill=(245, 245, 255))
    bg.save(bg_path)
    Image.new("RGBA", (300, 120), (255, 255, 255, 255)).save(logo_path)

    resolved = resolve_logo_overlay_box(str(bg_path), str(logo_path), "cover", "center", "large")

    assert resolved is not None
    assert resolved["strategy"] == "static:center"
    assert abs((resolved["left"] + resolved["width"] / 2) - 0.5) < 0.01
    assert abs((resolved["top"] + resolved["height"] / 2) - 0.5) < 0.01


def test_pptx_assembler_uses_resolved_logo_overlay_box(tmp_path):
    bg_path = tmp_path / "slide.png"
    logo_path = tmp_path / "logo.png"
    output_path = tmp_path / "out.pptx"
    Image.new("RGB", (1792, 1024), "black").save(bg_path)
    Image.new("RGBA", (300, 120), (255, 255, 255, 255)).save(logo_path)

    assemble_pptx(
        slide_images=[{
            "page_num": 1,
            "type": "cover",
            "image_path": str(bg_path),
            "visual_json": {
                "logo_policy": {
                    "show_logo": True,
                    "placement": "lower-center",
                    "scale": "large",
                    "resolved_overlay_box": {
                        "left": 0.12,
                        "top": 0.08,
                        "width": 0.18,
                        "height": 0.06,
                        "strategy": "smart:test",
                    },
                }
            },
        }],
        output_path=str(output_path),
        logo_config={"file_path": str(logo_path), "anchor": "top-right"},
    )

    prs = Presentation(str(output_path))
    logo_shape = prs.slides[0].shapes[1]
    assert logo_shape.left == int(prs.slide_width * 0.12)
    assert logo_shape.top == int(prs.slide_height * 0.08)
    assert logo_shape.width == int(prs.slide_width * 0.18)


def test_pptx_assembler_adds_dark_backplate_for_light_logo_on_light_slide(tmp_path):
    bg_path = tmp_path / "slide.png"
    logo_path = tmp_path / "logo.png"
    output_path = tmp_path / "out.pptx"
    Image.new("RGB", (1792, 1024), "white").save(bg_path)
    logo = Image.new("RGBA", (300, 120), (255, 255, 255, 0))
    draw = ImageDraw.Draw(logo)
    draw.rectangle((20, 35, 280, 85), fill=(248, 248, 248, 255))
    logo.save(logo_path)

    assemble_pptx(
        slide_images=[{
            "page_num": 1,
            "type": "cover",
            "image_path": str(bg_path),
            "visual_json": {},
        }],
        output_path=str(output_path),
        logo_config={"file_path": str(logo_path), "anchor": "top-right"},
    )

    prs = Presentation(str(output_path))
    shapes = prs.slides[0].shapes
    assert len(shapes) == 3
    assert shapes[1].fill.fore_color.rgb == RGBColor(17, 24, 39)
    logo_shape = shapes[2]
    assert abs((logo_shape.left + logo_shape.width / 2) - prs.slide_width / 2) < Inches(0.05)


def test_ending_logo_policy_defaults_to_small_corner_signature():
    policy = logo_policy_for_page({"type": "ending"})

    assert policy["show_logo"] is True
    assert policy["placement"] == "top-right"
    assert policy["scale"] == "small"


def test_prompt_for_punchline_page_uses_punchline_treatment():
    prompt = prompt_engine.generate_prompt_for_page(
        page_intent={
            "page_num": 4,
            "type": "hero",
            "layout": "hero",
            "visual_evidence": "金句排版、作者署名与纯色纹理背景",
            "visual_description": "中央大字金句，右下角小号署名，背景只有细微纸纹。",
        },
        content_text={
            "headline": "少即是多",
            "subhead": "—— 路德维希·密斯·凡德罗",
            "body": "这句名言的解释不应该出现在画面正文里",
        },
        style_text_override="Style: 冷静现代\nVisual rhythm: 内容页浅底高可读，金句页更沉浸",
    )

    assert 'Headline: "少即是多"' in prompt
    assert 'Subhead: "—— 路德维希·密斯·凡德罗"' in prompt
    assert "Body:" not in prompt
    assert "Punchline slide treatment" in prompt
    assert "same project typeface feel" in prompt
    assert "dense panels" in prompt


def test_prompt_includes_selected_global_visual_asset():
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


def test_prompt_strips_product_details_from_layout_usage_and_style_negatives():
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


def test_visual_asset_recall_uses_structured_source_refs():
    recalled = _recall_visual_assets_for_page(
        {
            "page_num": 3,
            "text_content": {"headline": "全新叙事页", "body": "这里没有直接提产品名"},
            "source_refs": [{"source_document": "source.pptx", "source_page_num": 2}],
        },
        [
            {
                "id": "asset-1",
                "name": "不在文案里的产品图",
                "kind": "product",
                "selection_tier": "core_global",
                "importance_score": 60,
                "source_document": "source.pptx",
                "source_page_num": 2,
                "analysis_summary": "name=不在文案里的产品图",
            }
        ],
    )

    assert recalled[0]["id"] == "asset-1"
    assert "源 PPT 页" in recalled[0]["reason"]


def test_visual_plan_prompt_treats_toc_as_simple_navigation():
    prompt = _build_batch_prompt(
        pages_summary=[
            {
                "page_num": 2,
                "type": "toc",
                "headline": "目录",
                "body_preview": "01 市场\n02 产品\n03 路径",
            }
        ],
        style={"meta": {"theme": "商务", "mood": "清晰", "palette": ["#111111", "#FFFFFF"]}, "body": ""},
    )

    assert "toc / 目录页：只有导航功能" in prompt
    assert "3-6 个短章节名" in prompt
    assert "不要做成花哨菜单" in prompt


def test_visual_plan_prompt_guards_cover_data_and_ending_roles():
    prompt = _build_batch_prompt(
        pages_summary=[
            {"page_num": 1, "type": "cover", "headline": "年度策略"},
            {"page_num": 5, "type": "data", "headline": "关键数字", "body_preview": "转化率 18%\n客单价 320 元"},
            {"page_num": 9, "type": "ending", "headline": "谢谢"},
        ],
        style={"meta": {"theme": "商务", "mood": "清晰", "palette": ["#111111", "#FFFFFF"]}, "body": ""},
    )

    assert "cover / 封面：只负责定调和命名" in prompt
    assert "data / 数据页：只有当页面正文给出真实数字" in prompt
    assert "不要编造数值、趋势或坐标轴" in prompt
    assert "ending / 封底：只做收束、感谢、CTA 或联系方式" in prompt
    assert "不要引入新的证明素材" in prompt


def test_visual_plan_prompt_disables_logo_policy_without_project_logo():
    prompt = _build_batch_prompt(
        pages_summary=[{"page_num": 1, "type": "cover", "headline": "年度策略"}],
        style={"meta": {"theme": "商务", "mood": "清晰", "palette": ["#111111", "#FFFFFF"]}, "body": ""},
        has_project_logo=False,
    )

    assert "当前项目没有已确认的用户 Logo" in prompt
    assert '"show_logo": false' in prompt
    assert "不要为 Logo、品牌角标、标识、徽标或占位框预留空间" in prompt
    assert "title-block-center" not in prompt


def test_visual_plan_prompt_allows_logo_policy_with_project_logo():
    prompt = _build_batch_prompt(
        pages_summary=[{"page_num": 1, "type": "cover", "headline": "年度策略"}],
        style={"meta": {"theme": "商务", "mood": "清晰", "palette": ["#111111", "#FFFFFF"]}, "body": ""},
        has_project_logo=True,
    )

    assert "title-block-center" in prompt
    assert '"show_logo": true' in prompt


def test_default_visual_asset_usage_does_not_describe_product_appearance():
    usage = _default_visual_asset_usage(
        {"name": "胡姬花花生油瓶", "kind": "product"},
        {"page_num": 1},
    )

    assert "uploaded product image" in usage
    assert "胡姬花花生油瓶" not in usage
    for unwanted in ["主体形状", "包装结构", "颜色", "品牌识别"]:
        assert unwanted not in usage


def test_prompt_does_not_reserve_logo_area_without_uploaded_logo_policy():
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

    assert "Logo Overlay Reservation" not in prompt
    assert "exact overlay" not in prompt
    assert "do not draw or invent logos" in prompt


def test_prompt_reserves_logo_overlay_area_when_policy_is_enabled():
    prompt = prompt_engine.generate_prompt_for_page(
        page_intent={
            "page_num": 1,
            "type": "cover",
            "layout": "cover",
            "visual_evidence": "品牌主张与主视觉背景",
            "visual_description": "封面突出主标题，品牌 Logo 与标题形成稳定关系。",
            "logo_policy": {"show_logo": True, "placement": "center", "scale": "large"},
        },
        content_text={"headline": "胡姬花年度整合营销提案"},
        reference_images=[],
        style_text_override="Style: 品牌提案\nPalette: #FFFFFF, #B01622",
    )

    assert "Logo Overlay Reservation" in prompt
    assert "center brand lockup area" in prompt
    assert "do not draw, invent, or stylize any logo" in prompt
    assert "exact overlay" in prompt


def test_project_logo_policy_is_disabled_when_no_confirmed_logo():
    project = SimpleNamespace(reference_images=[])

    intent = slides_api._with_project_logo_policy(
        {
            "type": "cover",
            "logo_policy": {
                "show_logo": True,
                "placement": "title-block-center",
                "scale": "large",
                "resolved_overlay_box": {"left": 0.1},
            },
        },
        project,
    )

    assert intent["logo_policy"]["show_logo"] is False
    assert intent["logo_policy"]["use_as_scene_asset"] is False
    assert "resolved_overlay_box" not in intent["logo_policy"]


def test_prompt_asset_policy_filters_stale_overlay_layers(tmp_path):
    asset_path = tmp_path / "asset.png"
    Image.new("RGB", (8, 8), "white").save(asset_path)
    project_asset = SimpleNamespace(
        id="asset-ok",
        role="visual_asset",
        slide_id=None,
        file_path=str(asset_path),
    )
    project = SimpleNamespace(reference_images=[project_asset])
    slide = SimpleNamespace(id="slide-1", reference_images=[])

    intent = slides_api._with_prompt_asset_policies(
        {
            "type": "content",
            "overlay_layers": [
                {"id": "ok", "asset_id": "asset-ok", "preset": "right-card"},
                {"id": "missing", "asset_id": "asset-missing", "preset": "left-card"},
            ],
        },
        project,
        slide,
    )

    assert [layer["asset_id"] for layer in intent["overlay_layers"]] == ["asset-ok"]
    assert intent["available_overlay_asset_ids"] == ["asset-ok"]


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
    assert calls[1]["prompt"] == (
        "用第2张及后续参考图校准第一张PPT图中的对应素材。保留第一张图的整体版式、背景和文字结构，"
        "只增强这些参考素材的外观、图案、文字和关键细节。参考素材路径：/tmp/uploads/huji-product.png"
    )
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
    assert calls[1]["prompt"] == (
        "用第2张及后续参考图校准第一张PPT图中的对应素材。保留第一张图的整体版式、背景和文字结构，"
        "只增强这些参考素材的外观、图案、文字和关键细节。参考素材路径：/tmp/uploads/product-a.png; /tmp/uploads/product-b.png"
    )
    assert calls[1]["reference_count"] == 3


def test_double_blend_pass_accepts_page_reference_refs(tmp_path, monkeypatch):
    calls = []

    def fake_generate_slide_image(prompt, reference_images=None, resolution="4K", aspect_ratio="16:9"):
        calls.append({"prompt": prompt, "reference_count": len(reference_images or [])})
        return Image.new("RGB", (16, 9), "green" if len(calls) == 2 else "blue")

    monkeypatch.setattr(generation_pipeline, "generate_slide_image", fake_generate_slide_image)

    slide = Slide(page_num=8, prompt_text="draft prompt", visual_json={})
    ref_data = [
        {
            "id": "page-ref-1",
            "role": "content_ref",
            "asset_name": "原 PPT 活动海报",
            "file_path": "/tmp/uploads/page-poster.png",
            "asset_route_mode": "double_blend",
            "image": Image.new("RGB", (8, 8), "white"),
        },
    ]

    result = _generate_one_slide(slide, "project-3", str(tmp_path), ref_data)

    assert result["error"] is None
    assert len(calls) == 2
    assert "原 PPT 活动海报" in calls[0]["prompt"]
    assert calls[1]["prompt"].startswith("用第2张及后续参考图校准第一张PPT图中的对应素材。")
    assert calls[1]["reference_count"] == 2


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


def test_project_visual_assets_for_planning_filters_legacy_ppt_scenes(tmp_path):
    scene_path = tmp_path / "scene.png"
    product_path = tmp_path / "product.png"
    map_path = tmp_path / "map.png"
    for path in (scene_path, product_path, map_path):
        Image.new("RGB", (8, 8), "white").save(path)
    project = SimpleNamespace(reference_images=[
        SimpleNamespace(
            id="legacy-scene",
            role="visual_asset",
            slide_id=None,
            file_path=str(scene_path),
            asset_name="legacy scene",
            asset_kind="scene",
            process_mode="blend",
            usage_note="",
            asset_analysis={
                "source_document": "source.pptx",
                "area_ratio": 0.42,
                "source_slide_text": "校园风景与氛围背景",
            },
        ),
        SimpleNamespace(
            id="core-product",
            role="visual_asset",
            slide_id=None,
            file_path=str(product_path),
            asset_name="core product",
            asset_kind="product",
            process_mode="crop",
            usage_note="",
            asset_analysis={
                "source_document": "source.pptx",
                "selection_tier": "core_global",
                "importance_score": 35,
            },
        ),
        SimpleNamespace(
            id="legacy-map",
            role="visual_asset",
            slide_id=None,
            file_path=str(map_path),
            asset_name="legacy map",
            asset_kind="other",
            process_mode="blend",
            usage_note="",
            asset_analysis={
                "source_document": "source.pptx",
                "area_ratio": 0.12,
                "source_slide_text": "渠道地图、数据图表与业务流程",
            },
        ),
    ])

    assets = slides_api._project_visual_assets_for_planning(project)

    assert [asset["id"] for asset in assets] == ["core-product"]


def test_missing_project_assets_do_not_create_prompt_references(tmp_path):
    existing_path = tmp_path / "asset.png"
    Image.new("RGB", (8, 8), "white").save(existing_path)
    missing_path = tmp_path / "missing.png"
    existing = SimpleNamespace(
        id="asset-ok",
        role="visual_asset",
        slide_id=None,
        file_path=str(existing_path),
        asset_name="existing product",
        asset_kind="product",
        process_mode="crop",
        usage_note="",
        asset_analysis={"selection_tier": "manual"},
    )
    missing = SimpleNamespace(
        id="asset-missing",
        role="visual_asset",
        slide_id=None,
        file_path=str(missing_path),
        asset_name="missing product",
        asset_kind="product",
        process_mode="crop",
        usage_note="",
        asset_analysis={"selection_tier": "manual"},
    )
    project = SimpleNamespace(reference_images=[existing, missing])

    planning_assets = slides_api._project_visual_assets_for_planning(project)
    prompt_refs = slides_api._project_refs_for_prompt(
        project,
        ["asset-ok", "asset-missing"],
        {"visual_asset_ids": ["asset-ok", "asset-missing"]},
    )

    assert [asset["id"] for asset in planning_assets] == ["asset-ok"]
    assert [ref["id"] for ref in prompt_refs] == ["asset-ok"]


def test_missing_template_does_not_create_prompt_reference(tmp_path):
    existing_path = tmp_path / "template.png"
    Image.new("RGB", (8, 8), "white").save(existing_path)
    project = SimpleNamespace(reference_images=[
        SimpleNamespace(id="template-ok", role="template", file_path=str(existing_path), process_mode="blend"),
        SimpleNamespace(id="template-missing", role="template", file_path=str(tmp_path / "missing-template.png"), process_mode="blend"),
    ])

    refs = slides_api._project_template_refs_for_prompt(project)

    assert [ref["id"] for ref in refs] == ["template-ok"]


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


def test_visual_plan_forces_logo_policy_off_when_project_has_no_logo(monkeypatch):
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
                                        '{"1": {'
                                        '"visual_evidence": "竞技场封面主视觉", '
                                        '"visual_summary": "暗红竞技场封面", '
                                        '"visual_description": "主标题左侧，右侧预留品牌标识区域。", '
                                        '"visual_asset_ids": [], '
                                        '"visual_asset_usage": {}, '
                                        '"logo_policy": {"show_logo": true, "placement": "title-block-center", "scale": "large", "use_as_scene_asset": false}'
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
                "page_num": 1,
                "type": "cover",
                "text_content": {"headline": "古罗马角斗士", "body": ""},
            }
        ],
        has_project_logo=False,
    )

    assert plan[0]["logo_policy"]["show_logo"] is False
    assert plan[0]["logo_policy"]["placement"] == "top-right"
    assert plan[0]["logo_policy"]["use_as_scene_asset"] is False
    assert "品牌标识" not in plan[0]["visual_description"]


def test_visual_plan_preserves_manual_pins_when_llm_selects_other_asset(monkeypatch):
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
                                        '{"1": {'
                                        '"visual_evidence": "校园驿站场景", '
                                        '"visual_summary": "驿站场景", '
                                        '"visual_description": "用校园驿站场景组织画面。", '
                                        '"visual_asset_ids": ["auto-1"], '
                                        '"visual_asset_usage": {"auto-1": "右侧补充"}'
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
                "page_num": 1,
                "type": "content",
                "text_content": {"headline": "校园驿站", "body": "取件机与回收场景"},
                "manual_visual_asset_ids": ["manual-1"],
                "manual_visual_asset_usage": {"manual-1": "用户指定放在左侧"},
            }
        ],
        global_visual_assets=[
            {"id": "manual-1", "name": "指定驿站图", "kind": "scene", "analysis_summary": "校园驿站"},
            {"id": "auto-1", "name": "自动候选图", "kind": "scene", "analysis_summary": "取件机"},
        ],
    )

    assert plan[0]["manual_visual_asset_ids"] == ["manual-1"]
    assert plan[0]["visual_asset_ids"][:2] == ["manual-1", "auto-1"]
    assert plan[0]["visual_asset_usage"]["manual-1"] == "用户指定放在左侧"


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


def test_confirm_prototype_autofills_missing_prompts_before_full_generation(monkeypatch):
    db = make_session()
    project = Project(
        title="Prototype confirm",
        status="prototype_ready",
        content_plan_confirmed=True,
        selected_style={"name": "Brand"},
    )
    db.add(project)
    db.flush()
    db.add_all(
        [
            Slide(
                project_id=project.id,
                page_num=1,
                status="completed",
                content_json={"page_num": 1, "text_content": {"headline": "已打样"}},
                visual_json={"visual_description": "sample visual"},
                prompt_text="sample prompt",
                image_path="/outputs/p1.png",
            ),
            Slide(
                project_id=project.id,
                page_num=2,
                status="pending",
                content_json={"page_num": 2, "text_content": {"headline": "待生成"}},
                visual_json={"visual_description": "pending visual"},
                prompt_text=None,
            ),
            Slide(
                project_id=project.id,
                page_num=3,
                status="pending",
                content_json={"page_num": 3, "text_content": {"headline": "已有 prompt"}},
                visual_json={"visual_description": "ready visual"},
                prompt_text="ready prompt",
            ),
        ]
    )
    db.commit()

    monkeypatch.setattr(slides_api, "generate_prompt_for_page", lambda **_kwargs: "auto prompt")
    monkeypatch.setattr(slides_api, "store_current_provider_credentials", lambda _redis: "cred")
    monkeypatch.setattr(slides_api.redis_client, "set", lambda *_args, **_kwargs: True)
    captured = {}

    def fake_delay(project_id, page_nums, **kwargs):
        captured["project_id"] = project_id
        captured["page_nums"] = page_nums
        captured["kwargs"] = kwargs
        return SimpleNamespace(id="task-1")

    monkeypatch.setattr(slides_api.generate_slides_task, "delay", fake_delay)

    result = slides_api.confirm_prototype(project.id, db=db)
    refreshed = db.query(Slide).filter(Slide.project_id == project.id).order_by(Slide.page_num).all()

    assert result["page_nums"] == [2, 3]
    assert captured["page_nums"] == [2, 3]
    assert refreshed[1].prompt_text == "auto prompt"
    assert refreshed[1].status == "prompt_ready"


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


def test_visual_asset_upload_returns_before_vlm_analysis_when_background_available(tmp_path, monkeypatch):
    db = make_session()
    project = Project(title="Asset upload", status="planning")
    db.add(project)
    db.commit()

    monkeypatch.setattr(slides_api.settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(
        slides_api,
        "analyze_visual_asset",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not analyze synchronously")),
    )
    scheduled = []
    monkeypatch.setattr(slides_api, "_submit_asset_analysis_task", lambda *args: scheduled.append(args))

    result = slides_api.upload_file(
        project.id,
        png_upload("product.png"),
        role="visual_asset",
        slide_id=None,
        process_mode=None,
        asset_name="产品图",
        asset_kind="product",
        usage_note="用于产品展示页",
        background_tasks=BackgroundTasks(),
        db=db,
    )

    assert result["asset_analysis"]["analysis_status"] == "queued"
    assert result["asset_analysis"]["subject"] == "产品图"
    assert result["process_mode"] == "crop"
    assert scheduled and scheduled[0][0] == "visual_asset"


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


def test_logo_upload_keeps_existing_global_logos(tmp_path, monkeypatch):
    db = make_session()
    project = Project(title="Logo upload", status="planning")
    db.add(project)
    db.commit()

    monkeypatch.setattr(slides_api.settings, "UPLOAD_DIR", str(tmp_path))

    first = slides_api.upload_file(
        project.id,
        png_upload("focus.png"),
        role="logo",
        slide_id=None,
        process_mode=None,
        asset_name=None,
        asset_kind=None,
        usage_note=None,
        logo_anchor="top-left",
        db=db,
    )
    second = slides_api.upload_file(
        project.id,
        png_upload("partner.png"),
        role="logo",
        slide_id=None,
        process_mode=None,
        asset_name=None,
        asset_kind=None,
        usage_note=None,
        logo_anchor="top-left",
        db=db,
    )

    logos = db.query(ReferenceImage).filter(
        ReferenceImage.project_id == project.id,
        ReferenceImage.role == "logo",
        ReferenceImage.slide_id.is_(None),
    ).all()
    assert len(logos) == 2
    assert {first["id"], second["id"]} == {logo.id for logo in logos}
    assert all(logo.logo_anchor == "top-left" for logo in logos)


def test_pptx_document_upload_extracts_generic_picture_as_page_ref_only(tmp_path, monkeypatch):
    db = make_session()
    project = Project(title="PPT upload", status="planning")
    db.add(project)
    db.flush()
    slide = Slide(project_id=project.id, page_num=1, status="pending")
    db.add(slide)
    db.commit()

    monkeypatch.setattr(documents_api.settings, "UPLOAD_DIR", str(tmp_path))

    result = documents_api.upload_document(
        project.id,
        pptx_upload_with_picture("source.pptx"),
        db=db,
    )
    assert result["text_parse_status"] == "queued"
    assert result["asset_extraction_status"] == "queued"
    assert db.query(ReferenceImage).filter(ReferenceImage.project_id == project.id).count() == 0

    stats = documents_api._extract_pptx_assets_for_document(
        project.id,
        str(tmp_path / project.id / "docs" / "source.pptx"),
        "source.pptx",
        db=db,
    )

    assert stats["page_refs"] == 1
    assert stats["visual_assets"] == 0
    page_ref = db.query(ReferenceImage).filter(
        ReferenceImage.project_id == project.id,
        ReferenceImage.role == "content_ref",
    ).one()
    visual_assets = db.query(ReferenceImage).filter(
        ReferenceImage.project_id == project.id,
        ReferenceImage.role == "visual_asset",
    ).all()
    assert page_ref.slide_id == slide.id
    assert page_ref.asset_analysis["source_document"] == "source.pptx"
    assert page_ref.asset_analysis["pptx_source_page_num"] == 1
    assert page_ref.asset_analysis["selection_tier"] == "page_ref"
    assert visual_assets == []
    docs = documents_api.list_documents(project.id, db=db)
    assert docs[0]["asset_extraction_status"] == "completed"


def test_pptx_asset_extraction_is_idempotent_for_page_refs(tmp_path, monkeypatch):
    db = make_session()
    project = Project(title="PPT upload", status="planning")
    db.add(project)
    db.flush()
    slide = Slide(project_id=project.id, page_num=1, status="pending")
    db.add(slide)
    db.commit()

    monkeypatch.setattr(documents_api.settings, "UPLOAD_DIR", str(tmp_path))

    documents_api.upload_document(
        project.id,
        pptx_upload_with_picture("source.pptx"),
        db=db,
    )
    source_path = str(tmp_path / project.id / "docs" / "source.pptx")
    first = documents_api._extract_pptx_assets_for_document(project.id, source_path, "source.pptx", db=db)
    second = documents_api._extract_pptx_assets_for_document(project.id, source_path, "source.pptx", db=db)

    page_refs = db.query(ReferenceImage).filter(
        ReferenceImage.project_id == project.id,
        ReferenceImage.role == "content_ref",
    ).all()

    assert first["page_refs"] == 1
    assert second["page_refs"] == 0
    assert len(page_refs) == 1
    assert page_refs[0].slide_id == slide.id


def test_pptx_document_upload_queues_asset_extraction_with_background_tasks(tmp_path, monkeypatch):
    db = make_session()
    project = Project(title="PPT upload", status="planning")
    db.add(project)
    db.flush()
    slide = Slide(project_id=project.id, page_num=1, status="pending")
    db.add(slide)
    db.commit()

    monkeypatch.setattr(documents_api.settings, "UPLOAD_DIR", str(tmp_path))
    background_tasks = BackgroundTasks()
    scheduled = []
    monkeypatch.setattr(
        documents_api,
        "_dispatch_document_processing",
        lambda *args: scheduled.append(args),
    )

    result = documents_api.upload_document(
        project.id,
        pptx_upload_with_primary_product_image("product-source.pptx"),
        background_tasks=background_tasks,
        db=db,
    )

    assert result["asset_extraction_status"] == "queued"
    assert result["text_parse_status"] == "queued"
    assert result["extracted_assets"]["total"] == 0
    assert len(background_tasks.tasks) == 0
    assert scheduled == [(
        project.id,
        str(tmp_path / project.id / "docs" / "product-source.pptx"),
        "product-source.pptx",
        ".pptx",
    )]
    assert db.query(ReferenceImage).filter(ReferenceImage.project_id == project.id).count() == 0
    docs = documents_api.list_documents(project.id, db=db)
    assert docs[0]["asset_extraction_status"] == "queued"
    assert docs[0]["text_parse_status"] == "queued"


def test_uploaded_document_text_is_parsed_on_generate_when_background_not_done(tmp_path, monkeypatch):
    db = make_session()
    project = Project(title="Text upload", status="draft")
    db.add(project)
    db.commit()

    monkeypatch.setattr(documents_api.settings, "UPLOAD_DIR", str(tmp_path))

    result = documents_api.upload_document(
        project.id,
        txt_upload("brief.txt", "预算有限\n目标是绿色开学季营销"),
        db=db,
    )

    assert result["text_parse_status"] == "queued"
    docs_before = documents_api.list_documents(project.id, db=db)
    assert docs_before[0]["filename"] == "brief.txt"
    assert docs_before[0]["text_parse_status"] == "queued"

    documents_text = load_project_documents(project.id, parse_missing=True)
    assert "预算有限" in documents_text
    docs_after = documents_api.list_documents(project.id, db=db)
    assert docs_after[0]["text_parse_status"] == "completed"
    assert docs_after[0]["char_count"] > 0


def test_pptx_upload_keeps_parallel_picture_group_complete_and_out_of_global_library(tmp_path, monkeypatch):
    db = make_session()
    project = Project(title="PPT upload", status="planning")
    db.add(project)
    db.flush()
    slide = Slide(project_id=project.id, page_num=1, status="pending")
    db.add(slide)
    db.commit()

    monkeypatch.setattr(documents_api.settings, "UPLOAD_DIR", str(tmp_path))

    result = documents_api.upload_document(
        project.id,
        pptx_upload_with_many_pictures("many.pptx", count=5),
        db=db,
    )
    stats = documents_api._extract_pptx_assets_for_document(
        project.id,
        str(tmp_path / project.id / "docs" / "many.pptx"),
        "many.pptx",
        db=db,
    )

    page_refs = db.query(ReferenceImage).filter(
        ReferenceImage.project_id == project.id,
        ReferenceImage.role == "content_ref",
    ).all()
    library_assets = db.query(ReferenceImage).filter(
        ReferenceImage.project_id == project.id,
        ReferenceImage.role == "visual_asset",
        ReferenceImage.slide_id.is_(None),
    ).all()

    assert result["extracted_assets"]["total"] == 0
    assert stats["total"] == 5
    assert len(page_refs) == 5
    assert len(library_assets) == 0
    assert {ref.asset_analysis["asset_group_size"] for ref in page_refs} == {5}
    assert sorted(ref.asset_analysis["asset_group_index"] for ref in page_refs) == [1, 2, 3, 4, 5]


def test_pptx_document_upload_promotes_primary_core_asset(tmp_path, monkeypatch):
    db = make_session()
    project = Project(title="PPT upload", status="planning")
    db.add(project)
    db.flush()
    slide = Slide(project_id=project.id, page_num=1, status="pending")
    db.add(slide)
    db.commit()

    monkeypatch.setattr(documents_api.settings, "UPLOAD_DIR", str(tmp_path))

    result = documents_api.upload_document(
        project.id,
        pptx_upload_with_primary_product_image("product-source.pptx"),
        db=db,
    )
    stats = documents_api._extract_pptx_assets_for_document(
        project.id,
        str(tmp_path / project.id / "docs" / "product-source.pptx"),
        "product-source.pptx",
        db=db,
    )

    assert result["asset_extraction_status"] == "queued"
    assert stats["page_refs"] == 1
    assert stats["visual_assets"] == 1
    visual_asset = db.query(ReferenceImage).filter(
        ReferenceImage.project_id == project.id,
        ReferenceImage.role == "visual_asset",
    ).one()
    assert visual_asset.slide_id is None
    assert visual_asset.asset_kind == "product"
    assert visual_asset.asset_analysis["selection_tier"] == "core_global"
    assert visual_asset.asset_analysis["library_role"] == "core_global_asset"


def test_pptx_document_upload_keeps_content_graphic_page_level(tmp_path, monkeypatch):
    db = make_session()
    project = Project(title="PPT upload", status="planning")
    db.add(project)
    db.flush()
    slide = Slide(project_id=project.id, page_num=1, status="pending")
    db.add(slide)
    db.commit()

    monkeypatch.setattr(documents_api.settings, "UPLOAD_DIR", str(tmp_path))

    result = documents_api.upload_document(
        project.id,
        pptx_upload_with_content_graphic("graphic-source.pptx"),
        db=db,
    )
    stats = documents_api._extract_pptx_assets_for_document(
        project.id,
        str(tmp_path / project.id / "docs" / "graphic-source.pptx"),
        "graphic-source.pptx",
        db=db,
    )

    assert result["asset_extraction_status"] == "queued"
    assert stats["page_refs"] == 1
    assert stats["visual_assets"] == 0
    page_ref = db.query(ReferenceImage).filter(
        ReferenceImage.project_id == project.id,
        ReferenceImage.role == "content_ref",
    ).one()
    assert page_ref.asset_analysis["selection_tier"] == "page_ref"
    assert page_ref.asset_analysis["selection_reason"] == "content graphic kept as page-level evidence"


def test_pptx_document_upload_keeps_identity_code_out_of_global_library(tmp_path, monkeypatch):
    db = make_session()
    project = Project(title="PPT upload", status="planning")
    db.add(project)
    db.flush()
    slide = Slide(project_id=project.id, page_num=1, status="pending")
    db.add(slide)
    db.commit()

    monkeypatch.setattr(documents_api.settings, "UPLOAD_DIR", str(tmp_path))

    result = documents_api.upload_document(
        project.id,
        pptx_upload_with_identity_code("identity-code-source.pptx"),
        db=db,
    )
    stats = documents_api._extract_pptx_assets_for_document(
        project.id,
        str(tmp_path / project.id / "docs" / "identity-code-source.pptx"),
        "identity-code-source.pptx",
        db=db,
    )

    assert result["asset_extraction_status"] == "queued"
    assert stats["page_refs"] == 1
    assert stats["visual_assets"] == 0
    page_ref = db.query(ReferenceImage).filter(
        ReferenceImage.project_id == project.id,
        ReferenceImage.role == "content_ref",
    ).one()
    assert page_ref.asset_analysis["selection_tier"] == "page_ref"
    assert page_ref.asset_analysis["selection_reason"] == "QR/identity code kept as page-specific evidence"


def test_pptx_document_upload_ignores_phone_frame_layout_chrome(tmp_path, monkeypatch):
    db = make_session()
    project = Project(title="PPT upload", status="planning")
    db.add(project)
    db.flush()
    slide = Slide(project_id=project.id, page_num=1, status="pending")
    db.add(slide)
    db.commit()

    monkeypatch.setattr(documents_api.settings, "UPLOAD_DIR", str(tmp_path))

    result = documents_api.upload_document(
        project.id,
        pptx_upload_with_phone_frame_chrome("phone-frame-source.pptx"),
        db=db,
    )
    stats = documents_api._extract_pptx_assets_for_document(
        project.id,
        str(tmp_path / project.id / "docs" / "phone-frame-source.pptx"),
        "phone-frame-source.pptx",
        db=db,
    )

    assert result["asset_extraction_status"] == "queued"
    assert stats["page_refs"] == 0
    assert stats["visual_assets"] == 0
    assert db.query(ReferenceImage).filter(ReferenceImage.project_id == project.id).count() == 0


def test_pptx_document_upload_promotes_multiple_cover_logos(tmp_path, monkeypatch):
    db = make_session()
    project = Project(title="PPT upload", status="draft")
    db.add(project)
    db.commit()

    monkeypatch.setattr(documents_api.settings, "UPLOAD_DIR", str(tmp_path))

    result = documents_api.upload_document(
        project.id,
        pptx_upload_with_two_cover_logos("two-logos.pptx"),
        db=db,
    )
    stats = documents_api._extract_pptx_assets_for_document(
        project.id,
        str(tmp_path / project.id / "docs" / "two-logos.pptx"),
        "two-logos.pptx",
        db=db,
    )

    logos = db.query(ReferenceImage).filter(
        ReferenceImage.project_id == project.id,
        ReferenceImage.role == "logo",
        ReferenceImage.slide_id.is_(None),
    ).all()

    assert result["asset_extraction_status"] == "queued"
    assert stats["logos"] == 2
    assert len(logos) == 2
    assert all(logo.process_mode == "original" for logo in logos)
    assert all(logo.asset_analysis["classification"] == "logo_candidate" for logo in logos)


def test_pptx_document_upload_respects_picture_crop_for_logo(tmp_path, monkeypatch):
    db = make_session()
    project = Project(title="PPT upload", status="draft")
    db.add(project)
    db.commit()

    monkeypatch.setattr(documents_api.settings, "UPLOAD_DIR", str(tmp_path))

    documents_api.upload_document(
        project.id,
        pptx_upload_with_cropped_cover_logo("cropped-logo.pptx"),
        db=db,
    )
    stats = documents_api._extract_pptx_assets_for_document(
        project.id,
        str(tmp_path / project.id / "docs" / "cropped-logo.pptx"),
        "cropped-logo.pptx",
        db=db,
    )

    logo = db.query(ReferenceImage).filter(
        ReferenceImage.project_id == project.id,
        ReferenceImage.role == "logo",
        ReferenceImage.slide_id.is_(None),
    ).one()
    extracted = Image.open(logo.file_path).convert("RGB")
    right_half_pixels = [
        extracted.getpixel((x, y))
        for x in range(extracted.width // 2, extracted.width)
        for y in range(extracted.height)
    ]

    assert stats["logos"] == 1
    assert extracted.width <= 225
    assert logo.asset_analysis["pptx_crop"] == [0.0, 0.0, 0.5, 0.0]
    assert not any(r > 180 and g < 60 and b < 70 for r, g, b in right_half_pixels)


def test_pptx_document_upload_promotes_cropped_multicolor_cover_logo(tmp_path, monkeypatch):
    db = make_session()
    project = Project(title="PPT upload", status="draft")
    db.add(project)
    db.commit()

    monkeypatch.setattr(documents_api.settings, "UPLOAD_DIR", str(tmp_path))

    documents_api.upload_document(
        project.id,
        pptx_upload_with_cropped_multicolor_cover_logo("cropped-multicolor-logo.pptx"),
        db=db,
    )
    stats = documents_api._extract_pptx_assets_for_document(
        project.id,
        str(tmp_path / project.id / "docs" / "cropped-multicolor-logo.pptx"),
        "cropped-multicolor-logo.pptx",
        db=db,
    )

    logos = db.query(ReferenceImage).filter(
        ReferenceImage.project_id == project.id,
        ReferenceImage.role == "logo",
        ReferenceImage.slide_id.is_(None),
    ).all()
    page_refs = db.query(ReferenceImage).filter(
        ReferenceImage.project_id == project.id,
        ReferenceImage.role == "content_ref",
    ).all()

    assert stats["logos"] == 1
    assert stats["page_refs"] == 0
    assert len(logos) == 1
    assert page_refs == []
    assert logos[0].asset_analysis["classification"] == "logo_candidate"
    assert logos[0].asset_analysis["review_status"] == "needs_review"
    assert max(logos[0].asset_analysis["pptx_crop"]) > 0


def test_unconfirmed_logos_do_not_enter_project_logo_refs(tmp_path):
    db = make_session()
    project = Project(title="Logo review", status="planning")
    db.add(project)
    db.flush()
    needs_review_path = tmp_path / "maybe_logo.png"
    confirmed_path = tmp_path / "confirmed_logo.png"
    Image.new("RGB", (80, 40), "white").save(needs_review_path)
    Image.new("RGB", (80, 40), "white").save(confirmed_path)
    needs_review = ReferenceImage(
        project_id=project.id,
        file_path=str(needs_review_path),
        role="logo",
        process_mode="original",
        asset_analysis={"classification": "logo_candidate", "review_status": "needs_review"},
    )
    confirmed = ReferenceImage(
        project_id=project.id,
        file_path=str(confirmed_path),
        role="logo",
        process_mode="original",
        asset_analysis={"review_status": "user_confirmed"},
    )
    db.add_all([needs_review, confirmed])
    db.commit()
    db.refresh(project)

    refs = slides_api._project_logo_refs(project)

    assert [ref.id for ref in refs] == [confirmed.id]


def test_missing_confirmed_logo_does_not_enable_logo_policy(tmp_path):
    db = make_session()
    project = Project(title="Missing logo", status="planning")
    db.add(project)
    db.flush()
    ref = ReferenceImage(
        project_id=project.id,
        file_path=str(tmp_path / "missing-logo.png"),
        role="logo",
        process_mode="original",
        asset_analysis={"review_status": "user_confirmed"},
    )
    db.add(ref)
    db.commit()
    db.refresh(project)

    intent = slides_api._with_project_logo_policy(
        {"type": "cover", "logo_policy": {"show_logo": True, "placement": "center", "scale": "large"}},
        project,
    )

    assert slides_api._project_logo_refs(project) == []
    assert intent["logo_policy"]["show_logo"] is False


def test_logo_review_status_can_promote_candidate_back_to_logo(tmp_path, monkeypatch):
    db = make_session()
    project = Project(title="Logo review", status="planning")
    db.add(project)
    db.flush()
    logo_path = tmp_path / "maybe_logo.png"
    Image.new("RGB", (80, 40), "white").save(logo_path)
    ref = ReferenceImage(
        project_id=project.id,
        file_path=str(logo_path),
        role="logo",
        process_mode="original",
        asset_analysis={"classification": "logo_candidate", "review_status": "needs_review"},
    )
    db.add(ref)
    db.commit()

    result = slides_api.update_reference_image(
        project.id,
        ref.id,
        {"review_status": "user_confirmed"},
        db=db,
    )
    db.refresh(project)

    assert result["review_status"] == "user_confirmed"
    assert [logo.id for logo in slides_api._project_logo_refs(project)] == [ref.id]


def test_pending_pptx_page_refs_link_after_content_plan_exists(tmp_path, monkeypatch):
    db = make_session()
    project = Project(title="PPT upload", status="draft")
    db.add(project)
    db.commit()

    monkeypatch.setattr(documents_api.settings, "UPLOAD_DIR", str(tmp_path))

    documents_api.upload_document(
        project.id,
        pptx_upload_with_picture("source.pptx"),
        db=db,
    )
    documents_api._extract_pptx_assets_for_document(
        project.id,
        str(tmp_path / project.id / "docs" / "source.pptx"),
        "source.pptx",
        db=db,
    )
    pending = db.query(ReferenceImage).filter(
        ReferenceImage.project_id == project.id,
        ReferenceImage.role == "content_ref",
    ).one()
    assert pending.slide_id is None

    slide = Slide(project_id=project.id, page_num=1, status="pending")
    db.add(slide)
    db.commit()

    linked = slides_api._link_pending_pptx_page_refs(project.id, db)
    db.commit()

    assert linked == 1
    db.refresh(pending)
    assert pending.slide_id == slide.id


def test_pending_pptx_page_refs_link_by_source_refs_when_pages_are_reordered(tmp_path, monkeypatch):
    db = make_session()
    project = Project(title="PPT upload", status="draft")
    db.add(project)
    db.commit()

    monkeypatch.setattr(documents_api.settings, "UPLOAD_DIR", str(tmp_path))

    documents_api.upload_document(
        project.id,
        pptx_upload_with_picture("source.pptx"),
        db=db,
    )
    documents_api._extract_pptx_assets_for_document(
        project.id,
        str(tmp_path / project.id / "docs" / "source.pptx"),
        "source.pptx",
        db=db,
    )
    pending = db.query(ReferenceImage).filter(
        ReferenceImage.project_id == project.id,
        ReferenceImage.role == "content_ref",
    ).one()
    assert pending.slide_id is None

    slide = Slide(
        project_id=project.id,
        page_num=3,
        status="pending",
        content_json={
            "page_num": 3,
            "source_refs": [{"source_document": "source.pptx", "source_page_num": 1}],
        },
    )
    db.add(slide)
    db.commit()

    linked = slides_api._link_pending_pptx_page_refs(project.id, db)
    db.commit()

    assert linked == 1
    db.refresh(pending)
    assert pending.slide_id == slide.id


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
        visual_json={
            "manual_visual_asset_ids": [asset.id],
            "manual_visual_asset_usage": {asset.id: "锁定到本页"},
            "visual_asset_ids": [asset.id],
            "visual_asset_usage": {asset.id: "右侧展示"},
            "overlay_layers": [{
                "id": "ov_asset",
                "asset_id": asset.id,
                "enabled": True,
                "preset": "right-card",
                "fit": "contain",
                "mode": "exact_card",
                "usage_note": "原样展示",
            }],
        },
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
    assert refreshed_slide.visual_json["manual_visual_asset_ids"] == []
    assert refreshed_slide.visual_json["manual_visual_asset_usage"] == {}
    assert refreshed_slide.visual_json["overlay_layers"] == []
    assert refreshed_slide.prompt_text is None
    assert refreshed_slide.status == "visual_ready"
    assert refreshed_project.status == "visual_ready"


def test_asset_pins_replace_reorder_and_survive_visual_merge(tmp_path):
    db = make_session()
    project = Project(title="Pins", status="completed")
    db.add(project)
    db.flush()
    slide = Slide(
        project_id=project.id,
        page_num=1,
        status="completed",
        visual_json={"visual_asset_ids": ["old-auto"], "visual_asset_usage": {"old-auto": "旧自动"}},
        prompt_text="old",
    )
    db.add(slide)
    assets = []
    for idx in range(2):
        path = tmp_path / f"asset_{idx}.png"
        Image.new("RGB", (10, 10), "white").save(path)
        asset = ReferenceImage(
            project_id=project.id,
            role="visual_asset",
            file_path=str(path),
            process_mode="crop",
            asset_name=f"素材{idx}",
            asset_kind="scene",
        )
        db.add(asset)
        assets.append(asset)
    db.commit()

    result = slides_api.update_slide_asset_pins(
        project.id,
        slide.id,
        slides_api.AssetPinsRequest(
            asset_ids=[assets[1].id, assets[0].id],
            usage={assets[1].id: "放在左侧"},
        ),
        db=db,
    )
    db.refresh(slide)

    assert result["manual_visual_asset_ids"] == [assets[1].id, assets[0].id]
    assert slide.visual_json["visual_asset_ids"][:2] == [assets[1].id, assets[0].id]
    assert slide.visual_json["manual_visual_asset_usage"][assets[1].id] == "放在左侧"
    assert slide.prompt_text is None
    assert slide.status == "visual_ready"

    merged = slides_api._merge_manual_pins_into_visual_json(
        {"visual_asset_ids": ["auto-1"], "visual_asset_usage": {"auto-1": "自动"}},
        slide.visual_json,
    )
    assert merged["visual_asset_ids"][:3] == [assets[1].id, assets[0].id, "auto-1"]


def test_overlay_layers_endpoint_and_visual_merge(tmp_path):
    db = make_session()
    project = Project(title="Overlay", status="completed")
    db.add(project)
    db.flush()
    slide = Slide(project_id=project.id, page_num=1, status="completed", prompt_text="old", visual_json={})
    db.add(slide)
    path = tmp_path / "screenshot.png"
    Image.new("RGB", (120, 70), "white").save(path)
    asset = ReferenceImage(
        project_id=project.id,
        role="visual_asset",
        file_path=str(path),
        process_mode="blend",
        asset_name="后台截图",
        asset_kind="material",
        asset_analysis={"subject": "后台截图"},
    )
    db.add(asset)
    db.commit()

    result = slides_api.update_slide_overlay_layers(
        project.id,
        slide.id,
        slides_api.OverlayLayersRequest(layers=[
            slides_api.OverlayLayerRequest(
                asset_id=asset.id,
                preset="right-card",
                mode="exact_card",
                usage_note="原样叠加",
            )
        ]),
        db=db,
    )
    db.refresh(slide)

    assert result["overlay_layers"][0]["asset_id"] == asset.id
    assert result["overlay_layers"][0]["fit"] == "contain"
    assert slide.visual_json["overlay_layers"][0]["preset"] == "right-card"
    assert slide.prompt_text is None
    assert slide.status == "visual_ready"
    db.refresh(asset)
    assert asset.process_mode == "original"
    assert asset.asset_analysis["exact_overlay"] is True

    merged = slides_api._merge_manual_pins_into_visual_json({"visual_asset_ids": []}, slide.visual_json)
    assert merged["overlay_layers"][0]["asset_id"] == asset.id


def test_overlay_layers_accept_page_reference_assets(tmp_path):
    db = make_session()
    project = Project(title="Page reference overlay", status="completed")
    db.add(project)
    db.flush()
    slide = Slide(project_id=project.id, page_num=2, status="completed", prompt_text="old", visual_json={})
    db.add(slide)
    db.flush()
    path = tmp_path / "page-ref.png"
    Image.new("RGB", (120, 70), "white").save(path)
    page_ref = ReferenceImage(
        project_id=project.id,
        slide_id=slide.id,
        role="content_ref",
        file_path=str(path),
        process_mode="blend",
        asset_name="原 PPT 第 2 页参考图",
        asset_analysis={"subject": "页面图片"},
    )
    db.add(page_ref)
    db.commit()

    result = slides_api.update_slide_overlay_layers(
        project.id,
        slide.id,
        slides_api.OverlayLayersRequest(layers=[
            slides_api.OverlayLayerRequest(
                asset_id=page_ref.id,
                preset="center-card",
                mode="exact_card",
                usage_note="原样保留",
            )
        ]),
        db=db,
    )
    db.refresh(slide)
    db.refresh(page_ref)

    assert result["overlay_layers"][0]["asset_id"] == page_ref.id
    assert slide.visual_json["overlay_layers"][0]["preset"] == "center-card"
    assert page_ref.process_mode == "original"
    assert page_ref.asset_analysis["exact_overlay"] is True


def test_asset_pins_unpin_removes_overlay_layer(tmp_path):
    db = make_session()
    project = Project(title="Unpin overlay", status="completed")
    db.add(project)
    db.flush()
    path = tmp_path / "asset.png"
    Image.new("RGB", (10, 10), "white").save(path)
    asset = ReferenceImage(project_id=project.id, role="visual_asset", file_path=str(path), asset_kind="other")
    db.add(asset)
    db.flush()
    slide = Slide(
        project_id=project.id,
        page_num=1,
        status="completed",
        visual_json={
            "manual_visual_asset_ids": [asset.id],
            "visual_asset_ids": [asset.id],
            "overlay_layers": [{"id": "ov", "asset_id": asset.id, "enabled": True, "preset": "right-card"}],
        },
    )
    db.add(slide)
    db.commit()

    slides_api.update_slide_asset_pins(
        project.id,
        slide.id,
        slides_api.AssetPinsRequest(asset_ids=[]),
        db=db,
    )
    db.refresh(slide)

    assert slide.visual_json["manual_visual_asset_ids"] == []
    assert slide.visual_json["overlay_layers"] == []


def test_pipeline_loads_manual_pins_before_auto_assets(tmp_path):
    db = make_session()
    project = Project(title="Pipeline pins", status="prompt_ready")
    db.add(project)
    db.flush()
    paths = []
    for idx, color in enumerate(["red", "blue"]):
        path = tmp_path / f"asset_{idx}.png"
        Image.new("RGB", (20, 20), color).save(path)
        paths.append(path)
    manual = ReferenceImage(
        project_id=project.id,
        role="visual_asset",
        file_path=str(paths[0]),
        process_mode="blend",
        asset_name="手动锁定",
        asset_kind="other",
    )
    auto = ReferenceImage(
        project_id=project.id,
        role="visual_asset",
        file_path=str(paths[1]),
        process_mode="blend",
        asset_name="自动候选",
        asset_kind="product",
    )
    db.add_all([manual, auto])
    db.flush()
    slide = Slide(
        project_id=project.id,
        page_num=1,
        status="prompt_ready",
        prompt_text="prompt",
        visual_json={
            "manual_visual_asset_ids": [manual.id],
            "visual_asset_ids": [auto.id, manual.id],
        },
    )
    db.add(slide)
    db.commit()

    loaded_slide = db.query(Slide).filter(Slide.id == slide.id).one()
    refs = _load_reference_images(loaded_slide)

    assert refs[0]["id"] == manual.id
    assert refs[0]["manual_pin"] is True
    assert [ref["id"] for ref in refs[:2]] == [manual.id, auto.id]


def test_pipeline_skips_exact_overlay_assets_as_image_inputs(tmp_path):
    db = make_session()
    project = Project(title="Pipeline overlay", status="prompt_ready")
    db.add(project)
    db.flush()
    overlay_path = tmp_path / "overlay.png"
    auto_path = tmp_path / "auto.png"
    Image.new("RGB", (20, 20), "red").save(overlay_path)
    Image.new("RGB", (20, 20), "blue").save(auto_path)
    overlay_asset = ReferenceImage(
        project_id=project.id,
        role="visual_asset",
        file_path=str(overlay_path),
        process_mode="original",
        asset_name="Exact 截图",
        asset_kind="material",
    )
    auto_asset = ReferenceImage(
        project_id=project.id,
        role="visual_asset",
        file_path=str(auto_path),
        process_mode="blend",
        asset_name="场景参考",
        asset_kind="scene",
    )
    db.add_all([overlay_asset, auto_asset])
    db.flush()
    slide = Slide(
        project_id=project.id,
        page_num=1,
        status="prompt_ready",
        prompt_text="prompt",
        visual_json={
            "visual_asset_ids": [overlay_asset.id, auto_asset.id],
            "overlay_layers": [{
                "id": "ov_exact",
                "asset_id": overlay_asset.id,
                "enabled": True,
                "preset": "right-card",
            }],
        },
    )
    db.add(slide)
    db.commit()

    refs = _load_reference_images(db.query(Slide).filter(Slide.id == slide.id).one())

    assert overlay_asset.id not in [ref.get("id") for ref in refs]
    assert auto_asset.id in [ref.get("id") for ref in refs]


def test_project_asset_route_overrides_stale_process_mode(tmp_path):
    db = make_session()
    project = Project(title="Route override", status="prompt_ready")
    db.add(project)
    db.flush()
    path = tmp_path / "asset.png"
    Image.new("RGB", (20, 20), "red").save(path)
    asset = ReferenceImage(
        project_id=project.id,
        role="visual_asset",
        file_path=str(path),
        process_mode="original",
        asset_name="已从精确粘贴切回的素材",
        asset_kind="material",
    )
    db.add(asset)
    db.flush()
    slide = Slide(
        project_id=project.id,
        page_num=1,
        status="prompt_ready",
        prompt_text="prompt",
        visual_json={
            "visual_asset_ids": [asset.id],
            "asset_route_modes": {asset.id: "blend"},
        },
    )
    db.add(slide)
    db.commit()

    refs = _load_reference_images(db.query(Slide).filter(Slide.id == slide.id).one())
    prompt_refs = slides_api._project_refs_for_prompt(
        db.query(Project).filter(Project.id == project.id).one(),
        [asset.id],
        slide.visual_json,
    )

    assert refs[0]["id"] == asset.id
    assert refs[0]["asset_route_mode"] == "blend"
    assert refs[0]["process_mode"] == "blend"
    assert prompt_refs[0]["asset_route_mode"] == "blend"
    assert prompt_refs[0]["process_mode"] == "blend"


def test_pipeline_skips_page_reference_overlay_as_image_input(tmp_path):
    db = make_session()
    project = Project(title="Pipeline page reference overlay", status="prompt_ready")
    db.add(project)
    db.flush()
    overlay_path = tmp_path / "page-overlay.png"
    blend_path = tmp_path / "page-blend.png"
    Image.new("RGB", (20, 20), "red").save(overlay_path)
    Image.new("RGB", (20, 20), "blue").save(blend_path)
    slide = Slide(
        project_id=project.id,
        page_num=3,
        status="prompt_ready",
        prompt_text="prompt",
        visual_json={
            "overlay_layers": [{
                "id": "ov_page_ref",
                "asset_id": "pending",
                "enabled": True,
                "preset": "right-card",
            }],
        },
    )
    db.add(slide)
    db.flush()
    overlay_ref = ReferenceImage(
        project_id=project.id,
        slide_id=slide.id,
        role="content_ref",
        file_path=str(overlay_path),
        process_mode="original",
        asset_name="原样保留素材",
    )
    blend_ref = ReferenceImage(
        project_id=project.id,
        slide_id=slide.id,
        role="content_ref",
        file_path=str(blend_path),
        process_mode="blend",
        asset_name="融合素材",
    )
    db.add_all([overlay_ref, blend_ref])
    db.flush()
    slide.visual_json = {
        "overlay_layers": [{
            "id": "ov_page_ref",
            "asset_id": overlay_ref.id,
            "enabled": True,
            "preset": "right-card",
        }],
    }
    db.commit()

    refs = _load_reference_images(db.query(Slide).filter(Slide.id == slide.id).one())

    assert overlay_ref.id not in [ref.get("id") for ref in refs]
    assert blend_ref.id in [ref.get("id") for ref in refs]


def test_pipeline_skips_chart_reference_overlay_as_image_input(tmp_path):
    db = make_session()
    project = Project(title="Pipeline chart reference overlay", status="prompt_ready")
    db.add(project)
    db.flush()
    chart_path = tmp_path / "chart-overlay.png"
    Image.new("RGB", (20, 20), "green").save(chart_path)
    slide = Slide(project_id=project.id, page_num=4, status="prompt_ready", prompt_text="prompt", visual_json={})
    db.add(slide)
    db.flush()
    chart_ref = ReferenceImage(
        project_id=project.id,
        slide_id=slide.id,
        role="chart_ref",
        file_path=str(chart_path),
        process_mode="original",
        asset_name="原样保留图表",
    )
    db.add(chart_ref)
    db.flush()
    slide.visual_json = {
        "overlay_layers": [{
            "id": "ov_chart_ref",
            "asset_id": chart_ref.id,
            "enabled": True,
            "preset": "center-card",
        }],
    }
    db.commit()

    refs = _load_reference_images(db.query(Slide).filter(Slide.id == slide.id).one())

    assert chart_ref.id not in [ref.get("id") for ref in refs]


def test_reference_image_library_filters_and_facets(tmp_path):
    db = make_session()
    project = Project(title="Library", status="visual_ready")
    db.add(project)
    db.flush()
    slide = Slide(project_id=project.id, page_num=3, status="visual_ready")
    db.add(slide)
    asset_path = tmp_path / "station.png"
    Image.new("RGB", (10, 10), "white").save(asset_path)
    asset = ReferenceImage(
        project_id=project.id,
        role="visual_asset",
        file_path=str(asset_path),
        process_mode="blend",
        asset_name="菜鸟驿站取件机",
        asset_kind="scene",
        asset_analysis={
            "source_document": "媒体介绍.pptx",
            "pptx_source_page_num": 6,
            "asset_tags": ["驿站", "取件机", "低碳"],
            "source_slide_text": "校园驿站低碳行为",
        },
    )
    db.add(asset)
    db.commit()

    library = slides_api.list_reference_images(
        project.id,
        q="取件机",
        source_document="媒体介绍.pptx",
        source_page_num=6,
        recommend_slide_id=slide.id,
        db=db,
    )

    assert library["total"] == 1
    assert library["items"][0]["id"] == asset.id
    assert library["items"][0]["source_document"] == "媒体介绍.pptx"
    assert "媒体介绍.pptx" in library["facets"]["source_documents"]


def test_default_reference_image_list_hides_legacy_low_value_visual_assets(tmp_path):
    db = make_session()
    project = Project(title="Library", status="visual_ready")
    db.add(project)
    db.flush()
    scene_path = tmp_path / "scene.png"
    core_path = tmp_path / "core.png"
    phone_path = tmp_path / "phone.png"
    Image.new("RGB", (10, 10), "white").save(scene_path)
    Image.new("RGB", (10, 10), "white").save(core_path)
    Image.new("RGB", (10, 10), "white").save(phone_path)
    db.add_all([
        ReferenceImage(
            project_id=project.id,
            role="visual_asset",
            file_path=str(scene_path),
            process_mode="blend",
            asset_name="校园风景背景",
            asset_kind="scene",
            asset_analysis={
                "source_document": "source.pptx",
                "area_ratio": 0.5,
                "source_slide_text": "校园风景与氛围背景",
            },
        ),
        ReferenceImage(
            project_id=project.id,
            role="visual_asset",
            file_path=str(core_path),
            process_mode="crop",
            asset_name="产品主视觉",
            asset_kind="product",
            asset_analysis={
                "source_document": "source.pptx",
                "selection_tier": "core_global",
                "importance_score": 35,
            },
        ),
        ReferenceImage(
            project_id=project.id,
            role="visual_asset",
            file_path=str(phone_path),
            process_mode="crop",
            asset_name="手机边框",
            asset_kind="product",
            asset_analysis={
                "source_document": "source.pptx",
                "selection_tier": "core_global",
                "importance_score": 35,
                "source_slide_text": "手机边框承载小程序界面与身份码展示",
            },
        ),
    ])
    db.commit()

    items = slides_api.list_reference_images(project.id, db=db)

    assert [item["asset_name"] for item in items] == ["产品主视觉"]
