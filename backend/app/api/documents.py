import os
import logging
import time
import json
from concurrent.futures import Future, ThreadPoolExecutor

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from app.models.base import SessionLocal, get_db
from app.models.models import Project, ReferenceImage, Slide
from app.core.config import settings
from app.services.pptx_asset_extractor import PptxImageAsset, extract_pptx_image_assets
from app.utils.project_docs import (
    document_parse_status_path,
    document_text_path,
    extract_document_text,
    get_project_docs_dir,
    iter_project_document_filenames,
    read_document_parse_status,
    write_document_parse_status,
)
from app.utils.reference_image import default_visual_asset_process_mode

router = APIRouter(prefix="/projects", tags=["documents"])
logger = logging.getLogger(__name__)

ALLOWED_DOC_EXTENSIONS = {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".md", ".txt", ".markdown"}


def _document_worker_count() -> int:
    try:
        return int(os.getenv("PPTGOD_DOCUMENT_WORKERS", "4"))
    except ValueError:
        return 4


DOCUMENT_WORKERS = max(2, min(8, _document_worker_count()))
DOCUMENT_PROCESSING_POOL = ThreadPoolExecutor(max_workers=DOCUMENT_WORKERS, thread_name_prefix="pptgod-doc")


def _get_docs_dir(project_id: str) -> str:
    return get_project_docs_dir(project_id, create=True)


def _get_pptx_assets_dir(project_id: str) -> str:
    upload_dir = settings.UPLOAD_DIR
    if not os.path.isabs(upload_dir):
        # 基于当前文件位置解析（app/api/ -> backend/）
        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        upload_dir = os.path.join(backend_dir, upload_dir)
    assets_dir = os.path.join(upload_dir, project_id, "pptx_assets")
    os.makedirs(assets_dir, exist_ok=True)
    return assets_dir


def _asset_status_path(project_id: str, filename: str) -> str:
    return os.path.join(_get_docs_dir(project_id), f"{filename}.assets.json")


def _write_asset_status(
    project_id: str,
    filename: str,
    status: str,
    *,
    stats: dict | None = None,
    error: str | None = None,
) -> None:
    payload = {
        "status": status,
        "updated_at": time.time(),
        "stats": stats or {"logos": 0, "page_refs": 0, "visual_assets": 0, "total": 0},
    }
    if error:
        payload["error"] = error[:500]
    try:
        with open(_asset_status_path(project_id, filename), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except OSError as exc:
        logger.warning("Failed to write PPTX asset status: project=%s file=%s error=%s", project_id, filename, exc)


def _read_asset_status(project_id: str, filename: str) -> dict:
    path = _asset_status_path(project_id, filename)
    if not os.path.exists(path):
        return {"status": "not_applicable", "stats": {"logos": 0, "page_refs": 0, "visual_assets": 0, "total": 0}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("status", "not_applicable")
            data.setdefault("stats", {"logos": 0, "page_refs": 0, "visual_assets": 0, "total": 0})
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"status": "unknown", "stats": {"logos": 0, "page_refs": 0, "visual_assets": 0, "total": 0}}


def _slide_by_page(project_id: str, db: Session) -> dict[int, Slide]:
    slides = db.query(Slide).filter(Slide.project_id == project_id).all()
    return {slide.page_num: slide for slide in slides}


def _asset_analysis(asset: PptxImageAsset) -> dict:
    tags = asset.metadata.get("asset_tags") if isinstance(asset.metadata, dict) else []
    tags = [str(tag) for tag in tags] if isinstance(tags, list) else []
    logo_review = {}
    if asset.role == "logo":
        is_candidate = asset.classification == "logo_candidate"
        logo_review = {
            "review_status": "needs_review" if is_candidate else "auto_confirmed",
            "needs_user_review": is_candidate,
            "confidence_score": 0.58 if is_candidate else 0.92,
            "review_reason": "从 PPT 封面/封底位置识别出的疑似 Logo，请确认是否为品牌标识。" if is_candidate else "跨页重复的小型标识，已自动确认为 Logo。",
            "detected_names": [],
        }
    return {
        **asset.metadata,
        **logo_review,
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


def _pptx_page_ref_key_from_analysis(analysis: dict, file_path: str | None = None) -> tuple[str, int | None, str]:
    source_document = str(analysis.get("source_document") or "")
    source_page_num = analysis.get("pptx_source_page_num")
    try:
        source_page_num = int(source_page_num) if source_page_num is not None else None
    except (TypeError, ValueError):
        source_page_num = None
    digest = str(analysis.get("pptx_image_sha1") or file_path or "")
    return source_document, source_page_num, digest


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
    existing_logo_refs = db.query(ReferenceImage).filter(
        ReferenceImage.project_id == project.id,
        ReferenceImage.slide_id.is_(None),
        ReferenceImage.role == "logo",
    ).all()
    existing_logo_hashes = {
        str(ref.asset_analysis.get("pptx_image_sha1"))
        for ref in existing_logo_refs
        if isinstance(ref.asset_analysis, dict) and ref.asset_analysis.get("pptx_image_sha1")
    }
    existing_page_refs = db.query(ReferenceImage).filter(
        ReferenceImage.project_id == project.id,
        ReferenceImage.role == "content_ref",
    ).all()
    existing_page_ref_keys = {
        _pptx_page_ref_key_from_analysis(ref.asset_analysis, ref.file_path)
        for ref in existing_page_refs
        if isinstance(ref.asset_analysis, dict)
    }

    stats = {"logos": 0, "page_refs": 0, "visual_assets": 0}
    affected_slides: set[str] = set()

    for asset in assets:
        analysis = _asset_analysis(asset)
        if asset.role == "logo":
            digest = str(analysis.get("pptx_image_sha1") or "")
            if digest and digest in existing_logo_hashes:
                continue
            db.add(ReferenceImage(
                project_id=project.id,
                slide_id=None,
                file_path=asset.file_path,
                role="logo",
                process_mode="original",
                asset_analysis=analysis,
            ))
            if digest:
                existing_logo_hashes.add(digest)
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
        page_ref_key = _pptx_page_ref_key_from_analysis(analysis, asset.file_path)
        if page_ref_key in existing_page_ref_keys:
            continue
        existing_page_ref_keys.add(page_ref_key)
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


def _extract_pptx_assets_for_document(
    project_id: str,
    source_path: str,
    source_filename: str,
    db: Session | None = None,
) -> dict:
    """Extract PPTX images after text upload is already available.

    When called by FastAPI BackgroundTasks this opens its own DB session. Tests
    can pass an existing session to keep synchronous assertions simple.
    """
    owns_session = db is None
    db = db or SessionLocal()
    started_at = time.perf_counter()
    try:
        _write_asset_status(project_id, source_filename, "running")
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            logger.warning("PPTX asset extraction skipped: project not found project=%s", project_id)
            return {"logos": 0, "page_refs": 0, "visual_assets": 0, "total": 0, "status": "not_found"}
        with open(source_path, "rb") as f:
            file_bytes = f.read()
        assets = extract_pptx_image_assets(
            file_bytes=file_bytes,
            source_filename=source_filename,
            output_dir=_get_pptx_assets_dir(project_id),
        )
        stats = _attach_extracted_pptx_assets(project, assets, db)
        db.commit()
        unique_asset_count = len({
            str(asset.metadata.get("pptx_image_sha1") or asset.file_path)
            for asset in assets
        })
        elapsed = time.perf_counter() - started_at
        logger.info(
            "PPTX asset extraction completed: project=%s file=%s total=%s stats=%s elapsed=%.2fs",
            project_id,
            source_filename,
            unique_asset_count,
            stats,
            elapsed,
        )
        completed = {"total": unique_asset_count, **stats, "status": "completed"}
        _write_asset_status(project_id, source_filename, "completed", stats=completed)
        return completed
    except Exception as e:
        db.rollback()
        # 文档文本仍然可用；图片拆解失败不应让用户整个上传失败。
        logger.warning("PPTX 图片素材拆解失败: project=%s file=%s error=%s", project_id, source_filename, e)
        failed = {"logos": 0, "page_refs": 0, "visual_assets": 0, "total": 0, "status": "failed", "error": str(e)}
        _write_asset_status(project_id, source_filename, "failed", stats=failed, error=str(e))
        return failed
    finally:
        if owns_session:
            db.close()


def _log_document_future_result(label: str, future: Future) -> None:
    try:
        future.result()
    except Exception as exc:
        logger.exception("Document background task crashed: task=%s error=%s", label, exc)


def _submit_document_task(label: str, fn, *args) -> None:
    future = DOCUMENT_PROCESSING_POOL.submit(fn, *args)
    future.add_done_callback(lambda done: _log_document_future_result(label, done))


def _dispatch_document_processing(project_id: str, source_path: str, source_filename: str, ext: str) -> None:
    """Kick off independent document processing tasks without gating upload."""
    _submit_document_task("text_parse", extract_document_text, project_id, source_path, source_filename)
    if ext == ".pptx":
        status = _read_asset_status(project_id, source_filename).get("status")
        if status in {"running", "completed"}:
            return
        _write_asset_status(project_id, source_filename, "queued")
        _submit_document_task("pptx_asset_extract", _extract_pptx_assets_for_document, project_id, source_path, source_filename)


@router.post("/{project_id}/upload-document")
def upload_document(
    project_id: str,
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
):
    """上传文档并立即入库；文字和 PPT 图片素材在后台分步解析。"""
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

    docs_dir = _get_docs_dir(project_id)

    # 保存原始文件
    original_path = os.path.join(docs_dir, file.filename)
    with open(original_path, "wb") as f:
        f.write(file_bytes)

    for stale_path in [document_text_path(project_id, file.filename), _asset_status_path(project_id, file.filename)]:
        if os.path.exists(stale_path):
            try:
                os.remove(stale_path)
            except OSError:
                logger.warning("Failed to remove stale document derivative: %s", stale_path)

    extracted_stats = {"logos": 0, "page_refs": 0, "visual_assets": 0, "total": 0}
    asset_extraction_status = "not_applicable"
    write_document_parse_status(project_id, file.filename, "queued")
    if ext == ".pptx":
        _write_asset_status(project_id, file.filename, "queued")
        asset_extraction_status = "queued"

    if background_tasks is not None:
        _dispatch_document_processing(project_id, original_path, file.filename, ext)

    return {
        "filename": file.filename,
        "char_count": 0,
        "text_parse_status": "queued",
        "text_preview": "",
        "asset_extraction_status": asset_extraction_status,
        "extracted_assets": {
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
    for filename in iter_project_document_filenames(project_id):
        parse_status = read_document_parse_status(project_id, filename)
        asset_status = _read_asset_status(project_id, filename)
        documents.append({
            "filename": filename,
            "char_count": parse_status.get("char_count", 0),
            "text_parse_status": parse_status.get("status"),
            "text_preview": parse_status.get("text_preview", ""),
            "asset_extraction_status": asset_status.get("status"),
            "extracted_assets": asset_status.get("stats") or {},
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
    parse_status_path = document_parse_status_path(project_id, filename)
    asset_status_path = _asset_status_path(project_id, filename)

    deleted = False
    for path in [original_path, text_path, parse_status_path, asset_status_path]:
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
