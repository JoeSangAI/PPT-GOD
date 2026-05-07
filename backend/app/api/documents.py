import os
import logging

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from app.models.base import get_db
from app.models.models import Project, ReferenceImage, Slide
from app.core.config import settings
from app.services.document_parser import parse_document, extract_text_preview
from app.services.pptx_asset_extractor import PptxImageAsset, extract_pptx_image_assets
from app.utils.reference_image import default_visual_asset_process_mode

router = APIRouter(prefix="/projects", tags=["documents"])
logger = logging.getLogger(__name__)

ALLOWED_DOC_EXTENSIONS = {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".md", ".txt", ".markdown"}


def _get_docs_dir(project_id: str) -> str:
    docs_dir = os.path.join(settings.UPLOAD_DIR, project_id, "docs")
    os.makedirs(docs_dir, exist_ok=True)
    return docs_dir


def _get_pptx_assets_dir(project_id: str) -> str:
    assets_dir = os.path.join(settings.UPLOAD_DIR, project_id, "pptx_assets")
    os.makedirs(assets_dir, exist_ok=True)
    return assets_dir


def _slide_by_page(project_id: str, db: Session) -> dict[int, Slide]:
    slides = db.query(Slide).filter(Slide.project_id == project_id).all()
    return {slide.page_num: slide for slide in slides}


def _asset_analysis(asset: PptxImageAsset) -> dict:
    tags = asset.metadata.get("asset_tags") if isinstance(asset.metadata, dict) else []
    tags = [str(tag) for tag in tags] if isinstance(tags, list) else []
    return {
        **asset.metadata,
        "detected_kind": asset.asset_kind or "other",
        "subject": asset.asset_name,
        "description": asset.usage_note,
        "recommended_usage": asset.usage_note,
        "suggested_keywords": list(dict.fromkeys([
            f"第{asset.source_page_num}页",
            "原PPT素材",
            "参考图",
            *tags,
        ]))[:32],
    }


def _attach_extracted_pptx_assets(
    project: Project,
    assets: list[PptxImageAsset],
    db: Session,
) -> dict:
    if not assets:
        return {"logos": 0, "page_refs": 0, "visual_assets": 0}

    slides_by_page = _slide_by_page(project.id, db)
    existing_global_refs = db.query(ReferenceImage).filter(
        ReferenceImage.project_id == project.id,
        ReferenceImage.slide_id.is_(None),
        ReferenceImage.role == "visual_asset",
    ).all()
    existing_global_hashes = {
        str(ref.asset_analysis.get("pptx_image_sha1"))
        for ref in existing_global_refs
        if isinstance(ref.asset_analysis, dict) and ref.asset_analysis.get("pptx_image_sha1")
    }
    has_logo = db.query(ReferenceImage).filter(
        ReferenceImage.project_id == project.id,
        ReferenceImage.slide_id.is_(None),
        ReferenceImage.role == "logo",
    ).first() is not None

    stats = {"logos": 0, "page_refs": 0, "visual_assets": 0}
    affected_slides: set[str] = set()

    for asset in assets:
        analysis = _asset_analysis(asset)
        if asset.role == "logo":
            if has_logo:
                continue
            db.add(ReferenceImage(
                project_id=project.id,
                slide_id=None,
                file_path=asset.file_path,
                role="logo",
                process_mode="original",
                asset_analysis=analysis,
            ))
            has_logo = True
            stats["logos"] += 1
            continue

        if asset.role == "visual_asset":
            digest = str(analysis.get("pptx_image_sha1") or "")
            if digest and digest in existing_global_hashes:
                continue
            kind = asset.asset_kind or "other"
            db.add(ReferenceImage(
                project_id=project.id,
                slide_id=None,
                file_path=asset.file_path,
                role="visual_asset",
                process_mode=asset.process_mode or default_visual_asset_process_mode(kind),
                asset_name=asset.asset_name,
                asset_kind=kind,
                usage_note=asset.usage_note,
                asset_analysis=analysis,
            ))
            if digest:
                existing_global_hashes.add(digest)
            stats["visual_assets"] += 1
            continue

        slide = slides_by_page.get(asset.source_page_num)
        if slide:
            affected_slides.add(slide.id)
        db.add(ReferenceImage(
            project_id=project.id,
            slide_id=slide.id if slide else None,
            file_path=asset.file_path,
            role="content_ref",
            process_mode=asset.process_mode,
            asset_name=asset.asset_name,
            asset_kind=asset.asset_kind,
            usage_note=asset.usage_note,
            asset_analysis=analysis,
        ))
        stats["page_refs"] += 1

    if affected_slides or stats["visual_assets"]:
        if project.slides:
            project.status = "visual_ready" if project.content_plan_confirmed else "planning"
        for slide in project.slides or []:
            if affected_slides and slide.id not in affected_slides:
                continue
            slide.visual_json = {}
            slide.prompt_text = None
            slide.image_path = None
            slide.error_msg = None
            if slide.status in {"visual_ready", "prompt_ready", "prototype_ready", "completed", "failed"}:
                slide.status = "pending"

    return stats


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

    extracted_assets = []
    extracted_stats = {"logos": 0, "page_refs": 0, "visual_assets": 0}
    if ext == ".pptx":
        try:
            extracted_assets = extract_pptx_image_assets(
                file_bytes=file_bytes,
                source_filename=file.filename,
                output_dir=_get_pptx_assets_dir(project_id),
            )
            extracted_stats = _attach_extracted_pptx_assets(project, extracted_assets, db)
            db.commit()
        except Exception as e:
            db.rollback()
            # 文档文本仍然可用；图片拆解失败不应让用户整个上传失败。
            logger.warning(f"PPTX 图片素材拆解失败: {e}")

    unique_asset_count = len({
        str(asset.metadata.get("pptx_image_sha1") or asset.file_path)
        for asset in extracted_assets
    })
    return {
        "filename": file.filename,
        "char_count": len(text),
        "text_preview": extract_text_preview(text, 300),
        "extracted_assets": {
            "total": unique_asset_count,
            **extracted_stats,
        },
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

    # 防止路径遍历攻击
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    docs_dir = _get_docs_dir(project_id)
    original_path = os.path.join(docs_dir, filename)
    text_path = os.path.join(docs_dir, f"{filename}.extracted.txt")

    deleted = False
    for path in [original_path, text_path]:
        if os.path.exists(path):
            os.remove(path)
            deleted = True

    refs = db.query(ReferenceImage).filter(ReferenceImage.project_id == project_id).all()
    for ref in refs:
        analysis = ref.asset_analysis if isinstance(ref.asset_analysis, dict) else {}
        if analysis.get("source_document") != filename:
            continue
        if ref.file_path and os.path.exists(ref.file_path):
            try:
                os.remove(ref.file_path)
            except OSError:
                pass
        db.delete(ref)
        deleted = True

    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")

    db.commit()
    return {"message": "Document deleted"}
