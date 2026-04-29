import os
from typing import List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from app.models.base import get_db
from app.models.models import Project
from app.core.config import settings
from app.services.document_parser import parse_document, extract_text_preview

router = APIRouter(prefix="/projects", tags=["documents"])

MAX_DOC_SIZE = 50 * 1024 * 1024  # 50MB
ALLOWED_DOC_EXTENSIONS = {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".md", ".txt", ".markdown"}


def _get_docs_dir(project_id: str) -> str:
    docs_dir = os.path.join(settings.UPLOAD_DIR, project_id, "docs")
    os.makedirs(docs_dir, exist_ok=True)
    return docs_dir


@router.post("/{project_id}/upload-document")
def upload_document(
    project_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """上传文档并提取文字内容。支持 PDF、Word、PPT、Markdown、TXT 等。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # 读取文件内容
    file_bytes = file.file.read()
    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail="上传的文件为空")
    if len(file_bytes) > MAX_DOC_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"文件大小超过限制（最大 {MAX_DOC_SIZE // 1024 // 1024}MB）",
        )

    # 验证文件扩展名
    ext = os.path.splitext(file.filename.lower())[1]
    if ext not in ALLOWED_DOC_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式 '{ext}'。允许: PDF, Word, PPT, Markdown, TXT",
        )

    # 解析文档
    try:
        text = parse_document(file_bytes, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文档解析失败: {e}")

    docs_dir = _get_docs_dir(project_id)

    # 保存原始文件
    original_path = os.path.join(docs_dir, file.filename)
    with open(original_path, "wb") as f:
        f.write(file_bytes)

    # 保存提取的文本
    text_filename = f"{file.filename}.extracted.txt"
    text_path = os.path.join(docs_dir, text_filename)
    with open(text_path, "w", encoding="utf-8") as f:
        f.write(text)

    return {
        "filename": file.filename,
        "char_count": len(text),
        "text_preview": extract_text_preview(text, 300),
    }


@router.get("/{project_id}/documents")
def list_documents(project_id: str, db: Session = Depends(get_db)):
    """列出项目已上传的文档及其提取信息。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    docs_dir = _get_docs_dir(project_id)
    if not os.path.exists(docs_dir):
        return []

    documents = []
    for filename in os.listdir(docs_dir):
        if filename.endswith(".extracted.txt"):
            original_name = filename[:-14]  # 去掉 .extracted.txt
            text_path = os.path.join(docs_dir, filename)
            char_count = 0
            try:
                char_count = os.path.getsize(text_path)
            except OSError:
                pass
            documents.append({
                "filename": original_name,
                "char_count": char_count,
            })

    return documents


@router.delete("/{project_id}/documents/{filename}")
def delete_document(
    project_id: str,
    filename: str,
    db: Session = Depends(get_db),
):
    """删除已上传的文档及其提取的文本。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    docs_dir = _get_docs_dir(project_id)
    original_path = os.path.join(docs_dir, filename)
    text_path = os.path.join(docs_dir, f"{filename}.extracted.txt")

    deleted = False
    for path in [original_path, text_path]:
        if os.path.exists(path):
            os.remove(path)
            deleted = True

    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")

    return {"message": "Document deleted"}
