import json
import logging
import os
import time
from typing import Callable

from app.core.config import settings
from app.services.document_parser import extract_text_preview, parse_document
from app.services.source_pack import (
    build_source_pack,
    read_source_pack as read_source_pack_file,
    write_source_pack as write_source_pack_file,
)

logger = logging.getLogger(__name__)

EXTRACTED_TEXT_SUFFIX = ".extracted.txt"
PARSE_STATUS_SUFFIX = ".parse.json"
ASSET_STATUS_SUFFIX = ".assets.json"
SOURCE_PACK_SUFFIX = ".source.json"
DOCUMENT_METADATA_SUFFIXES = (EXTRACTED_TEXT_SUFFIX, PARSE_STATUS_SUFFIX, ASSET_STATUS_SUFFIX, SOURCE_PACK_SUFFIX)
DOCUMENT_PARSE_STALE_SECONDS = 90
DocumentProgressCallback = Callable[[dict], None]


def get_project_docs_dir(project_id: str, *, create: bool = False) -> str:
    docs_dir = os.path.join(settings.UPLOAD_DIR, project_id, "docs")
    if create:
        os.makedirs(docs_dir, exist_ok=True)
    return docs_dir


def is_document_metadata_filename(filename: str) -> bool:
    return filename.endswith(DOCUMENT_METADATA_SUFFIXES)


def document_text_path(project_id: str, filename: str) -> str:
    return os.path.join(get_project_docs_dir(project_id, create=True), f"{filename}{EXTRACTED_TEXT_SUFFIX}")


def document_parse_status_path(project_id: str, filename: str) -> str:
    return os.path.join(get_project_docs_dir(project_id, create=True), f"{filename}{PARSE_STATUS_SUFFIX}")


def document_source_pack_path(project_id: str, filename: str) -> str:
    return os.path.join(get_project_docs_dir(project_id, create=True), f"{filename}{SOURCE_PACK_SUFFIX}")


def get_project_source_assets_dir(project_id: str, *, create: bool = False) -> str:
    upload_dir = settings.UPLOAD_DIR
    if not os.path.isabs(upload_dir):
        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        upload_dir = os.path.join(backend_dir, upload_dir)
    assets_dir = os.path.join(upload_dir, project_id, "source_assets")
    if create:
        os.makedirs(assets_dir, exist_ok=True)
    return assets_dir


def write_document_parse_status(
    project_id: str,
    filename: str,
    status: str,
    *,
    char_count: int = 0,
    text_preview: str = "",
    error: str | None = None,
    current_page: int | None = None,
    total_pages: int | None = None,
    message: str | None = None,
) -> None:
    payload = {
        "status": status,
        "updated_at": time.time(),
        "char_count": char_count,
        "text_preview": text_preview,
    }
    if current_page is not None:
        payload["current_page"] = max(0, int(current_page))
    if total_pages is not None:
        payload["total_pages"] = max(0, int(total_pages))
    if message:
        payload["message"] = str(message)[:200]
    if error:
        payload["error"] = error[:500]
    try:
        with open(document_parse_status_path(project_id, filename), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except OSError as exc:
        logger.warning("Failed to write document parse status: project=%s file=%s error=%s", project_id, filename, exc)


def read_document_parse_status(project_id: str, filename: str) -> dict:
    status_path = document_parse_status_path(project_id, filename)
    if os.path.exists(status_path):
        try:
            with open(status_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("status", "unknown")
                data.setdefault("char_count", 0)
                data.setdefault("text_preview", "")
                if data.get("status") == "running" and _parse_status_is_stale(data):
                    data["status"] = "stale"
                    data.setdefault("message", "文档解析长时间没有进度，稍后会重新读取。")
                return data
        except (OSError, json.JSONDecodeError):
            pass

    text_path = document_text_path(project_id, filename)
    if os.path.exists(text_path):
        try:
            with open(text_path, "r", encoding="utf-8") as f:
                text = f.read()
            return {
                "status": "completed",
                "char_count": len(text),
                "text_preview": extract_text_preview(text, 300),
            }
        except OSError:
            return {"status": "unknown", "char_count": 0, "text_preview": ""}

    docs_dir = get_project_docs_dir(project_id)
    if os.path.exists(os.path.join(docs_dir, filename)):
        return {"status": "queued", "char_count": 0, "text_preview": ""}
    return {"status": "not_found", "char_count": 0, "text_preview": ""}


def read_document_source_pack(project_id: str, filename: str) -> dict | None:
    return read_source_pack_file(document_source_pack_path(project_id, filename))


def read_document_source_status(project_id: str, filename: str) -> dict:
    pack = read_document_source_pack(project_id, filename)
    if pack:
        stats = pack.get("stats") if isinstance(pack.get("stats"), dict) else {}
        return {
            "status": "completed",
            "stats": {
                "pages": int(stats.get("pages") or 0),
                "chapters": int(stats.get("chapters") or 0),
                "images": int(stats.get("images") or 0),
                "text_chars": int(stats.get("text_chars") or 0),
                "estimated_tokens": int(stats.get("estimated_tokens") or 0),
            },
        }
    parse_status = read_document_parse_status(project_id, filename)
    status = parse_status.get("status") or "unknown"
    if status in {"queued", "running", "stale", "failed", "not_found"}:
        return {
            "status": status,
            "stats": {"pages": 0, "chapters": 0, "images": 0, "text_chars": 0, "estimated_tokens": 0},
        }
    return {
        "status": "queued",
        "stats": {"pages": 0, "chapters": 0, "images": 0, "text_chars": 0, "estimated_tokens": 0},
    }


def _parse_status_is_stale(status: dict, *, stale_seconds: float = DOCUMENT_PARSE_STALE_SECONDS) -> bool:
    try:
        updated_at = float(status.get("updated_at") or 0)
    except (TypeError, ValueError):
        return False
    return bool(updated_at and time.time() - updated_at > stale_seconds)


def iter_project_document_filenames(project_id: str, *, include_legacy_extracted: bool = True) -> list[str]:
    docs_dir = get_project_docs_dir(project_id)
    if not os.path.exists(docs_dir):
        return []

    filenames: set[str] = set()
    for filename in os.listdir(docs_dir):
        path = os.path.join(docs_dir, filename)
        if os.path.isdir(path):
            continue
        if filename.endswith(EXTRACTED_TEXT_SUFFIX):
            if include_legacy_extracted:
                filenames.add(filename[: -len(EXTRACTED_TEXT_SUFFIX)])
            continue
        if is_document_metadata_filename(filename):
            continue
        filenames.add(filename)
    return sorted(filenames)


def extract_document_text(
    project_id: str,
    source_path: str,
    filename: str,
    progress_callback: DocumentProgressCallback | None = None,
) -> dict:
    """Parse and persist document text without blocking the upload request."""
    write_document_parse_status(project_id, filename, "running")
    last_page_state: dict = {}

    def report_progress(payload: dict) -> None:
        current_page = payload.get("current_page")
        total_pages = payload.get("total_pages")
        message = str(payload.get("message") or "正在读取上传材料...")
        if current_page is not None:
            last_page_state["current_page"] = current_page
        if total_pages is not None:
            last_page_state["total_pages"] = total_pages
        write_document_parse_status(
            project_id,
            filename,
            "running",
            current_page=current_page,
            total_pages=total_pages,
            message=message,
        )
        if progress_callback:
            try:
                progress_callback({
                    **payload,
                    "filename": filename,
                    "status": "running",
                })
            except Exception:
                pass

    try:
        with open(source_path, "rb") as f:
            file_bytes = f.read()
        try:
            source_pack = build_source_pack(
                file_bytes,
                filename,
                asset_output_dir=get_project_source_assets_dir(project_id, create=True),
            )
            write_source_pack_file(document_source_pack_path(project_id, filename), source_pack)
        except Exception as exc:
            logger.warning("SourcePack build failed: project=%s file=%s error=%s", project_id, filename, exc)
        text = parse_document(file_bytes, filename, progress_callback=report_progress)
        text_path = document_text_path(project_id, filename)
        with open(text_path, "w", encoding="utf-8") as f:
            f.write(text)
        result = {
            "status": "completed",
            "char_count": len(text),
            "text_preview": extract_text_preview(text, 300),
            "text": text,
        }
        write_document_parse_status(
            project_id,
            filename,
            "completed",
            char_count=result["char_count"],
            text_preview=result["text_preview"],
            current_page=last_page_state.get("total_pages"),
            total_pages=last_page_state.get("total_pages"),
            message="上传材料文字读取完成",
        )
        return result
    except Exception as exc:
        logger.warning("Document text parse failed: project=%s file=%s error=%s", project_id, filename, exc)
        write_document_parse_status(project_id, filename, "failed", error=str(exc))
        return {
            "status": "failed",
            "char_count": 0,
            "text_preview": "",
            "text": "",
            "error": str(exc),
        }


def _wait_for_source_pack(
    project_id: str,
    filename: str,
    *,
    timeout_seconds: float = 4.0,
    progress_callback: DocumentProgressCallback | None = None,
) -> dict | None:
    deadline = time.time() + timeout_seconds
    last_progress_emit = 0.0
    while time.time() < deadline:
        pack = read_document_source_pack(project_id, filename)
        if pack:
            return pack
        status = read_document_parse_status(project_id, filename)
        now = time.time()
        if progress_callback and status.get("status") in {"running", "completed", "failed", "stale"} and now - last_progress_emit >= 1.0:
            last_progress_emit = now
            try:
                progress_callback({
                    "filename": filename,
                    "status": status.get("status"),
                    "current_page": status.get("current_page"),
                    "total_pages": status.get("total_pages"),
                    "message": status.get("message") or "正在读取上传材料...",
                    "char_count": status.get("char_count", 0),
                })
            except Exception:
                pass
        if status.get("status") in {"failed", "stale"}:
            return None
        time.sleep(1.0)
    return None


def load_project_source_packs(
    project_id: str,
    *,
    parse_missing: bool = False,
    running_wait_seconds: float = 4.0,
    progress_callback: DocumentProgressCallback | None = None,
) -> list[dict]:
    """Load persistent SourcePacks for all uploaded documents."""
    docs_dir = get_project_docs_dir(project_id)
    if not os.path.exists(docs_dir):
        return []

    packs: list[dict] = []
    for filename in iter_project_document_filenames(project_id):
        try:
            pack = read_document_source_pack(project_id, filename)
            if not pack and parse_missing:
                source_path = os.path.join(docs_dir, filename)
                if not os.path.exists(source_path):
                    continue
                parse_status = read_document_parse_status(project_id, filename)
                if parse_status.get("status") == "running":
                    pack = _wait_for_source_pack(
                        project_id,
                        filename,
                        timeout_seconds=running_wait_seconds,
                        progress_callback=progress_callback,
                    )
                if not pack:
                    if parse_status.get("status") == "running":
                        continue
                    extract_document_text(
                        project_id,
                        source_path,
                        filename,
                        progress_callback=progress_callback,
                    )
                    pack = read_document_source_pack(project_id, filename)
            if pack:
                packs.append(pack)
        except Exception:
            continue
    return packs


def _read_extracted_text(project_id: str, filename: str) -> str:
    with open(document_text_path(project_id, filename), "r", encoding="utf-8") as f:
        return f.read()


def _wait_for_extracted_text(
    project_id: str,
    filename: str,
    *,
    timeout_seconds: float = 4.0,
    progress_callback: DocumentProgressCallback | None = None,
) -> str:
    deadline = time.time() + timeout_seconds
    last_progress_emit = 0.0
    while time.time() < deadline:
        if os.path.exists(document_text_path(project_id, filename)):
            return _read_extracted_text(project_id, filename)
        status = read_document_parse_status(project_id, filename)
        now = time.time()
        if progress_callback and status.get("status") in {"running", "completed", "failed", "stale"} and now - last_progress_emit >= 1.0:
            last_progress_emit = now
            try:
                progress_callback({
                    "filename": filename,
                    "status": status.get("status"),
                    "current_page": status.get("current_page"),
                    "total_pages": status.get("total_pages"),
                    "message": status.get("message") or "正在读取上传材料...",
                    "char_count": status.get("char_count", 0),
                })
            except Exception:
                pass
        if status.get("status") in {"failed", "stale"}:
            return ""
        time.sleep(1.0)
    return ""


def load_project_documents(
    project_id: str,
    *,
    parse_missing: bool = False,
    text_limit: int | None = None,
    preserve_ppt_sources: bool = False,
    running_wait_seconds: float = 4.0,
    progress_callback: DocumentProgressCallback | None = None,
) -> str:
    """读取项目已上传文档的提取文本，必要时在后台生成任务内补解析。"""
    docs_dir = get_project_docs_dir(project_id)
    if not os.path.exists(docs_dir):
        return ""

    parts = []
    for filename in iter_project_document_filenames(project_id):
        text = ""
        try:
            if os.path.exists(document_text_path(project_id, filename)):
                text = _read_extracted_text(project_id, filename)
            elif parse_missing:
                source_path = os.path.join(docs_dir, filename)
                if os.path.exists(source_path):
                    parse_status = read_document_parse_status(project_id, filename)
                    if parse_status.get("status") == "running":
                        text = _wait_for_extracted_text(
                            project_id,
                            filename,
                            timeout_seconds=running_wait_seconds,
                            progress_callback=progress_callback,
                        )
                    if not text:
                        if parse_status.get("status") == "running":
                            continue
                        result = extract_document_text(
                            project_id,
                            source_path,
                            filename,
                            progress_callback=progress_callback,
                        )
                        text = result.get("text") or ""
            if not text:
                continue
            is_ppt_source = "--- PPT_SOURCE" in text[:500]
            if preserve_ppt_sources and is_ppt_source:
                max_chars = None
            elif text_limit is not None:
                max_chars = text_limit
            else:
                max_chars = 40_000 if is_ppt_source else 12_000
            if max_chars is not None and len(text) > max_chars:
                text = text[:max_chars] + "\n\n[文档内容过长，已截断]"
            parts.append(f"--- 文档: {filename} ---\n{text}")
        except Exception:
            continue

    return "\n\n".join(parts)
