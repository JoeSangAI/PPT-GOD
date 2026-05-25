import io

import fitz
from PIL import Image
from pptx import Presentation

from app.services.source_pack import build_source_pack


def _sample_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "目录\n第1章 创新使命 ........ 2\n第2章 客户痴迷 ........ 4", fontsize=12, fontname="china-s")

    page = doc.new_page()
    page.insert_text((72, 72), "第1章 创新使命\n微软被卡住了，需要重新找到存在主义使命。", fontsize=12, fontname="china-s")

    page = doc.new_page()
    page.insert_text((72, 72), "让组织愿景与个人北极星相一致。", fontsize=12, fontname="china-s")
    image = Image.new("RGB", (120, 60), "white")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    page.insert_image(fitz.Rect(72, 140, 220, 220), stream=buf.getvalue())

    page = doc.new_page()
    page.insert_text((72, 72), "第2章 客户痴迷\n客户永远不会满足。", fontsize=12, fontname="china-s")
    return doc.tobytes()


def test_pdf_source_pack_preserves_pages_chapters_and_images(tmp_path):
    pack = build_source_pack(
        _sample_pdf(),
        "创新.pdf",
        asset_output_dir=str(tmp_path),
    )

    assert pack["document"]["filename"] == "创新.pdf"
    assert pack["document"]["kind"] == "pdf"
    assert pack["stats"]["pages"] == 4
    assert pack["pages"][1]["page_num"] == 2
    assert "微软被卡住了" in pack["pages"][1]["text"]

    chapter_titles = [chapter["title"] for chapter in pack["chapters"]]
    assert "第1章 创新使命" in chapter_titles
    assert "第2章 客户痴迷" in chapter_titles
    chapter1 = next(chapter for chapter in pack["chapters"] if chapter["title"] == "第1章 创新使命")
    assert chapter1["start_page"] == 2
    assert chapter1["end_page"] == 3

    assert len(pack["images"]) == 1
    image = pack["images"][0]
    assert image["source_page_num"] == 3
    assert image["source_document"] == "创新.pdf"
    assert image["bbox"]
    assert image["nearby_text"]


def test_pdf_source_pack_profiles_front_matter_sections_before_body_chapters():
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "前言\n作者为什么写这本书", fontsize=12, fontname="china-s")
    page = doc.new_page()
    page.insert_text((72, 72), "绪论\n大企业为什么会失去创新能力", fontsize=12, fontname="china-s")
    page = doc.new_page()
    page.insert_text((72, 72), "阅读指南\n保持开放和乐观的心态", fontsize=12, fontname="china-s")
    page = doc.new_page()
    page.insert_text((72, 72), "第1章 创新使命\n微软需要重新发现灵魂。", fontsize=12, fontname="china-s")

    pack = build_source_pack(doc.tobytes(), "创新.pdf")

    sections = pack["source_structure"]
    assert [
        (section["section_role"], section["title"], section["start_page"], section["end_page"])
        for section in sections
    ] == [
        ("preface", "前言", 1, 1),
        ("intro", "绪论", 2, 2),
        ("guide", "阅读指南", 3, 3),
        ("chapter", "第1章 创新使命", 4, 4),
    ]
    assert pack["pages"][1]["source_section_role"] == "intro"


def test_pdf_source_pack_distinguishes_content_figures_from_auxiliary_images():
    doc = fitz.open()
    large = Image.new("RGB", (640, 480), "white")
    tiny = Image.new("RGB", (48, 24), "black")
    large_buf = io.BytesIO()
    tiny_buf = io.BytesIO()
    large.save(large_buf, format="PNG")
    tiny.save(tiny_buf, format="PNG")

    page = doc.new_page()
    page.insert_text((72, 72), "框架图展示企业使命与个人北极星如何对齐", fontsize=12, fontname="china-s")
    page.insert_image(fitz.Rect(72, 120, 500, 520), stream=large_buf.getvalue())

    page = doc.new_page()
    page.insert_text((72, 72), "正文旁边有一个小装饰符号", fontsize=12, fontname="china-s")
    page.insert_image(fitz.Rect(72, 120, 110, 138), stream=tiny_buf.getvalue())

    pack = build_source_pack(doc.tobytes(), "创新.pdf")

    figures_by_page = {image["source_page_num"]: image for image in pack["images"]}
    assert figures_by_page[1]["figure_role"] == "content"
    assert figures_by_page[1]["content_significance"] == "high"
    assert figures_by_page[1]["image_width"] == 640
    assert figures_by_page[1]["image_height"] == 480
    assert figures_by_page[2]["figure_role"] == "auxiliary"
    assert figures_by_page[2]["content_significance"] == "low"


def test_markdown_source_pack_uses_headings_as_chapters():
    pack = build_source_pack(
        "# 总论\n\n开场。\n\n## 第一章 增长逻辑\n\n正文一。\n\n## 第二章 组织逻辑\n\n正文二。".encode("utf-8"),
        "讲稿.md",
    )

    assert pack["document"]["kind"] == "markdown"
    assert pack["stats"]["pages"] == 1
    assert [chapter["title"] for chapter in pack["chapters"]] == [
        "总论",
        "第一章 增长逻辑",
        "第二章 组织逻辑",
    ]
    assert "正文一" in pack["pages"][0]["text"]


def test_pptx_source_pack_uses_lightweight_text_extraction_without_sparse_recovery(monkeypatch):
    from app.services import pptx_page_recovery

    def fail_recovery(*_args, **_kwargs):
        raise AssertionError("SourcePack PPTX parsing must not call VLM sparse page recovery")

    monkeypatch.setattr(pptx_page_recovery, "recover_sparse_slide_text", fail_recovery)

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "流量红利 VS 人心红利"
    slide.placeholders[1].text = "品牌增长需要从流量套利转向心智占领"
    buf = io.BytesIO()
    prs.save(buf)

    pack = build_source_pack(buf.getvalue(), "source.pptx")

    assert pack["document"]["kind"] == "pptx"
    assert pack["stats"]["pages"] == 1
    assert "流量红利 VS 人心红利" in pack["pages"][0]["text"]
    assert "心智占领" in pack["pages"][0]["text"]
