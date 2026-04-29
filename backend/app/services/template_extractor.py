import logging
import os
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


def convert_ppt_to_pdf(ppt_path: str, output_dir: str) -> str:
    """使用 LibreOffice 将 PPT/PPTX 转换为 PDF。"""
    pdf_name = os.path.splitext(os.path.basename(ppt_path))[0] + ".pdf"
    pdf_path = os.path.join(output_dir, pdf_name)

    cmd = [
        "soffice",
        "--headless",
        "--convert-to", "pdf",
        "--outdir", output_dir,
        ppt_path,
    ]
    logger.info(f"TemplateExtractor: 转换 PPT -> PDF: {ppt_path}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        logger.error(f"LibreOffice 转换失败: {result.stderr}")
        raise RuntimeError(f"PPT 转换失败: {result.stderr}")

    # LibreOffice 输出的文件名可能和原文件名相同但后缀改为 .pdf
    generated_pdf = os.path.join(output_dir, os.path.splitext(os.path.basename(ppt_path))[0] + ".pdf")
    if not os.path.exists(generated_pdf):
        raise RuntimeError("PDF 文件未生成")

    return generated_pdf


def extract_pdf_thumbnails(pdf_path: str, output_dir: str, dpi: int = 150) -> List[str]:
    """使用 PyMuPDF 提取 PDF 每页为 PNG 缩略图。"""
    doc = fitz.open(pdf_path)
    paths = []

    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        # 计算缩放比例以达到目标 DPI
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        img_path = os.path.join(output_dir, f"page_{page_num + 1:03d}.png")
        pix.save(img_path)
        paths.append(img_path)
        logger.info(f"TemplateExtractor: 提取第 {page_num + 1} 页缩略图 -> {img_path}")

    doc.close()
    return paths


def extract_template_images(file_path: str, project_id: str, upload_dir: str) -> List[Dict]:
    """
    从上传的 PPT/PDF 中提取每页缩略图。
    返回 [{page_num, file_path, url}, ...]
    """
    ext = os.path.splitext(file_path)[1].lower()
    output_dir = os.path.join(upload_dir, project_id, "templates")
    os.makedirs(output_dir, exist_ok=True)

    # 清理旧的模板缩略图
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
        os.makedirs(output_dir, exist_ok=True)

    pdf_path = file_path
    if ext in (".ppt", ".pptx"):
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = convert_ppt_to_pdf(file_path, tmpdir)
            # 复制到 output_dir 以便后续使用
            dest_pdf = os.path.join(output_dir, "converted.pdf")
            shutil.copy2(pdf_path, dest_pdf)
            pdf_path = dest_pdf

    if not os.path.exists(pdf_path):
        raise RuntimeError("PDF 文件不存在")

    img_paths = extract_pdf_thumbnails(pdf_path, output_dir)

    results = []
    for idx, img_path in enumerate(img_paths, start=1):
        filename = os.path.basename(img_path)
        results.append({
            "page_num": idx,
            "file_path": img_path,
            "url": f"/uploads/{project_id}/templates/{filename}",
        })

    logger.info(f"TemplateExtractor: 共提取 {len(results)} 页模板缩略图")
    return results


def _infer_page_category(page_num: int, total: int) -> str:
    """基于页码位置推断页面类别。"""
    if page_num == 1:
        return "cover"
    if page_num == total and total > 1:
        return "ending"
    if page_num == 2 and total >= 3:
        return "toc"
    return "content"


def recommend_template_pages(pages: List[Dict], content_plan: Optional[List[Dict]] = None) -> Dict[str, Optional[Dict]]:
    """
    根据页数和 Content Plan 推荐代表性页面。
    返回 {cover, toc, content, ending} 各推荐哪一页，并为每页标注 category。
    简单启发式规则：
    - cover: 第 1 页
    - toc: 第 2 页（或前 20%）
    - content: 中间页
    - ending: 最后一页
    """
    total = len(pages)
    if total == 0:
        return {"cover": None, "toc": None, "content": None, "ending": None}

    # 为每页标注 category
    for p in pages:
        p["category"] = _infer_page_category(p["page_num"], total)

    cover = pages[0]
    ending = pages[-1]

    # toc: 如果 content_plan 中有 toc 类型，找对应的页码
    toc = None
    if content_plan:
        for item in content_plan:
            if item.get("type") == "toc":
                page_num = item.get("page_num", 2)
                if 1 <= page_num <= total:
                    toc = pages[page_num - 1]
                break
    if not toc and total >= 2:
        toc = pages[1]

    # content: 中间页（优先选标注为 content 的）
    content_candidates = [p for p in pages if p.get("category") == "content"]
    content_page = content_candidates[len(content_candidates) // 2] if content_candidates else pages[total // 2]

    return {
        "cover": cover,
        "toc": toc,
        "content": content_page,
        "ending": ending,
    }
