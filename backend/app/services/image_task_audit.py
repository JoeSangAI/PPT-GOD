from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
import threading
from typing import Any

from app.core.config import settings


_write_lock = threading.Lock()
_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_part(value: str | None, fallback: str) -> str:
    text = str(value or fallback).strip() or fallback
    return _SAFE_NAME_RE.sub("_", text)[:120]


def image_generation_log_dir() -> str:
    return os.path.join(settings.OUTPUT_DIR or "./outputs", "image-generation-logs")


def image_generation_log_path(project_id: str | None, run_id: str | None) -> str:
    project = _safe_part(project_id, "unknown-project")
    run = _safe_part(run_id, "no-run")
    return os.path.join(image_generation_log_dir(), project, f"{run}.jsonl")


def _json_default(value: Any):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def append_image_generation_log(
    project_id: str | None,
    run_id: str | None,
    event: str,
    **payload: Any,
) -> str:
    """Append one durable JSONL audit event for image generation troubleshooting."""
    path = image_generation_log_path(project_id, run_id)
    record = {
        "ts": _utc_iso(),
        "event": event,
        "project_id": project_id,
        "run_id": run_id,
        **payload,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True, default=_json_default)
    with _write_lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    return path

