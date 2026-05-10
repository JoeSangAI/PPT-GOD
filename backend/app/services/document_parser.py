import os
from io import BytesIO
import re


def parse_document(file_bytes: bytes, filename: str) -> str:
    """根据文件扩展名解析文档，返回纯文本内容。"""
    ext = os.path.splitext(filename)[1].lower()

    if ext == ".pdf":
        return _parse_pdf(file_bytes)
    elif ext in (".docx", ".doc"):
        return _parse_docx(file_bytes)
    elif ext in (".pptx", ".ppt"):
        return _parse_pptx(file_bytes, filename)
    elif ext in (".md", ".txt", ".json", ".csv", ".html", ".htm"):
        return _parse_text(file_bytes)
    else:
        # 未知格式，尝试按文本读取
        try:
            return file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError(f"不支持的文件格式: {ext}，请上传 PDF、Word、PPT、Markdown 或文本文件。")


def _parse_pdf(file_bytes: bytes) -> str:
    import fitz  # PyMuPDF

    text_parts = []
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        for page in doc:
            text_parts.append(page.get_text())
    return "\n\n".join(text_parts)


def _parse_docx(file_bytes: bytes) -> str:
    from docx import Document

    doc = Document(BytesIO(file_bytes))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def _parse_pptx(file_bytes: bytes, filename: str = "") -> str:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    prs = Presentation(BytesIO(file_bytes))
    text_parts = []
    text_parts.append(f'--- PPT_SOURCE filename="{filename}" pages={len(prs.slides)} ---')
    for i, slide in enumerate(prs.slides, 1):
        slide_texts = _extract_shape_texts(slide.shapes, MSO_SHAPE_TYPE)
        notes = _extract_notes_text(slide)
        if notes:
            slide_texts.append("【备注】\n" + notes)
        text_parts.append(f"--- 第{i}页 ---\n" + "\n".join(slide_texts))
    return "\n\n".join(text_parts)


def _append_unique(parts: list[str], text: str) -> None:
    cleaned = re.sub(r"[ \t]+", " ", str(text or "")).strip()
    if cleaned and cleaned not in parts:
        parts.append(cleaned)


def _extract_shape_texts(shapes, shape_type_enum) -> list[str]:
    texts: list[str] = []
    for shape in shapes:
        if getattr(shape, "shape_type", None) == shape_type_enum.GROUP:
            texts.extend(text for text in _extract_shape_texts(shape.shapes, shape_type_enum) if text not in texts)
            continue

        if getattr(shape, "has_table", False):
            for row in shape.table.rows:
                cells = []
                for cell in row.cells:
                    cell_text = re.sub(r"\s+", " ", cell.text or "").strip()
                    if cell_text:
                        cells.append(cell_text)
                if cells:
                    _append_unique(texts, " | ".join(cells))
            continue

        if getattr(shape, "has_chart", False):
            chart_title = getattr(getattr(shape.chart, "chart_title", None), "text_frame", None)
            if chart_title is not None:
                _append_unique(texts, chart_title.text)

        if hasattr(shape, "text"):
            _append_unique(texts, shape.text)
    return texts


def _extract_notes_text(slide) -> str:
    try:
        if not getattr(slide, "has_notes_slide", False):
            return ""
        notes = []
        for shape in slide.notes_slide.notes_text_frame.paragraphs:
            line = "".join(run.text for run in shape.runs).strip()
            if line:
                notes.append(line)
        return "\n".join(notes).strip()
    except Exception:
        return ""


def detect_ppt_sources(documents_text: str) -> list[dict]:
    """Return PPT upload metadata embedded in extracted document text."""
    sources = []
    pattern = r'---\s*PPT_SOURCE(?:\s+filename="([^"]*)")?\s+pages=(\d+)\s*---'
    for match in re.finditer(pattern, documents_text or ""):
        sources.append({
            "filename": match.group(1) or "",
            "pages": int(match.group(2)),
        })
    return sources


def _parse_text(file_bytes: bytes) -> str:
    return file_bytes.decode("utf-8")


def extract_text_preview(text: str, max_chars: int = 300) -> str:
    """提取文本预览，截取前 max_chars 个字符。"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."
