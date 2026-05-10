import io

from pptx import Presentation
from pptx.util import Inches

from app.services.document_parser import parse_document


def test_pptx_parser_extracts_grouped_text_and_table_cells():
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_textbox(Inches(0.5), Inches(0.4), Inches(3), Inches(0.5)).text = "顶层标题"

    group = slide.shapes.add_group_shape()
    group.shapes.add_textbox(Inches(0.8), Inches(1.0), Inches(3), Inches(0.5)).text = "组合内关键数字 3000+"

    table_shape = slide.shapes.add_table(2, 2, Inches(0.5), Inches(2.0), Inches(4), Inches(1.0))
    table_shape.table.cell(0, 0).text = "指标"
    table_shape.table.cell(0, 1).text = "数据"
    table_shape.table.cell(1, 0).text = "覆盖城市"
    table_shape.table.cell(1, 1).text = "300城"

    out = io.BytesIO()
    prs.save(out)

    text = parse_document(out.getvalue(), "source.pptx")

    assert "顶层标题" in text
    assert "组合内关键数字 3000+" in text
    assert "指标 | 数据" in text
    assert "覆盖城市 | 300城" in text
