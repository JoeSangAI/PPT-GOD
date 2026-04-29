import os
from io import BytesIO
from typing import Tuple


def parse_document(file_bytes: bytes, filename: str) -> str:
    """根据文件扩展名解析文档，返回纯文本内容。"""
    ext = os.path.splitext(filename)[1].lower()

    if ext == ".pdf":
        return _parse_pdf(file_bytes)
    elif ext in (".docx", ".doc"):
        return _parse_docx(file_bytes)
    elif ext in (".pptx", ".ppt"):
        return _parse_pptx(file_bytes)
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


def _parse_pptx(file_bytes: bytes) -> str:
    from pptx import Presentation

    prs = Presentation(BytesIO(file_bytes))
    text_parts = []
    for i, slide in enumerate(prs.slides, 1):
        slide_texts = []
        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text.strip():
                slide_texts.append(shape.text.strip())
        if slide_texts:
            text_parts.append(f"--- 第{i}页 ---\n" + "\n".join(slide_texts))
    return "\n\n".join(text_parts)


def _parse_text(file_bytes: bytes) -> str:
    return file_bytes.decode("utf-8")


def extract_text_preview(text: str, max_chars: int = 300) -> str:
    """提取文本预览，截取前 max_chars 个字符。"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."
