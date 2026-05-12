import json
import logging
import os
import time

from app.core.config import settings
from app.services.document_parser import extract_text_preview, parse_document

logger = logging.getLogger(__name__)

EXTRACTED_TEXT_SUFFIX = ".extracted.txt"
PARSE_STATUS_SUFFIX = ".parse.json"
ASSET_STATUS_SUFFIX = ".assets.json"
DOCUMENT_METADATA_SUFFIXES = (EXTRACTED_TEXT_SUFFIX, PARSE_STATUS_SUFFIX, ASSET_STATUS_SUFFIX)


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


def write_document_parse_status(
    project_id: str,
    filename: str,
    status: str,
    *,
    char_count: int = 0,
    text_preview: str = "",
    error: str | None = None,
) -> None:
    payload = {
        "status": status,
        "updated_at": time.time(),
        "char_count": char_count,
        "text_preview": text_preview,
    }
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


def extract_document_text(project_id: str, source_path: str, filename: str) -> dict:
    """Parse and persist document text without blocking the upload request."""
    write_document_parse_status(project_id, filename, "running")
    try:
        with open(source_path, "rb") as f:
            file_bytes = f.read()
        text = parse_document(file_bytes, filename)
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


def _read_extracted_text(project_id: str, filename: str) -> str:
    with open(document_text_path(project_id, filename), "r", encoding="utf-8") as f:
        return f.read()


def _wait_for_extracted_text(project_id: str, filename: str, *, timeout_seconds: float = 4.0) -> str:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if os.path.exists(document_text_path(project_id, filename)):
            return _read_extracted_text(project_id, filename)
        time.sleep(0.2)
    return ""


def load_project_documents(project_id: str, *, parse_missing: bool = False, text_limit: int | None = None) -> str:
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
                    if read_document_parse_status(project_id, filename).get("status") == "running":
                        text = _wait_for_extracted_text(project_id, filename)
                    if not text:
                        result = extract_document_text(project_id, source_path, filename)
                        text = result.get("text") or ""
            if not text:
                continue
            if text_limit is not None:
                max_chars = text_limit
            else:
                max_chars = 40_000 if "--- PPT_SOURCE" in text[:500] else 12_000
            if len(text) > max_chars:
                text = text[:max_chars] + "\n\n[文档内容过长，已截断]"
            parts.append(f"--- 文档: {filename} ---\n{text}")
        except Exception:
            continue

    return "\n\n".join(parts)
