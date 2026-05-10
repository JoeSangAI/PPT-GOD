from __future__ import annotations

import copy
import hashlib
import json
import os
from typing import Any


ARTIFACT_META_KEY = "_artifact"


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def digest(value: Any) -> str:
    return hashlib.sha1(stable_json(value).encode("utf-8")).hexdigest()


def strip_artifact_meta(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: strip_artifact_meta(v) for k, v in value.items() if k != ARTIFACT_META_KEY}
    if isinstance(value, list):
        return [strip_artifact_meta(v) for v in value]
    return value


def content_signature(slides: list[Any]) -> str:
    items = []
    for slide in sorted(slides or [], key=lambda s: int(getattr(s, "page_num", 0) or 0)):
        items.append({
            "page_num": getattr(slide, "page_num", None),
            "type": getattr(slide, "type", None),
            "content_json": strip_artifact_meta(getattr(slide, "content_json", None) or {}),
        })
    return digest(items)


def style_asset_signature(project: Any | None) -> str:
    if not project:
        return ""
    parts = []
    for ref in sorted(getattr(project, "reference_images", None) or [], key=lambda r: (getattr(r, "role", "") or "", getattr(r, "id", "") or "")):
        if getattr(ref, "slide_id", None) or getattr(ref, "role", None) not in {"logo", "style_ref", "template"}:
            continue
        file_path = getattr(ref, "file_path", "") or ""
        try:
            mtime = os.path.getmtime(file_path) if os.path.exists(file_path) else 0
        except OSError:
            mtime = 0
        analysis = getattr(ref, "asset_analysis", None) if isinstance(getattr(ref, "asset_analysis", None), dict) else {}
        parts.append({
            "id": getattr(ref, "id", None),
            "role": getattr(ref, "role", None),
            "path": os.path.basename(file_path),
            "exists": os.path.exists(file_path),
            "mtime": round(mtime, 3),
            "analysis": digest(strip_artifact_meta(analysis)) if analysis else "",
        })
    if getattr(project, "selected_template_recommendations", None):
        parts.append({"template_recommendations": strip_artifact_meta(project.selected_template_recommendations)})
    return digest(parts) if parts else ""


def visual_asset_signature(project: Any | None) -> str:
    if not project:
        return ""
    parts = []
    for ref in sorted(getattr(project, "reference_images", None) or [], key=lambda r: getattr(r, "id", "") or ""):
        if getattr(ref, "slide_id", None) or getattr(ref, "role", None) != "visual_asset":
            continue
        analysis = getattr(ref, "asset_analysis", None) if isinstance(getattr(ref, "asset_analysis", None), dict) else {}
        parts.append({
            "id": getattr(ref, "id", None),
            "process_mode": getattr(ref, "process_mode", None),
            "asset_name": getattr(ref, "asset_name", None),
            "asset_kind": getattr(ref, "asset_kind", None),
            "usage_note": getattr(ref, "usage_note", None),
            "analysis": digest(strip_artifact_meta(analysis)) if analysis else "",
        })
    return digest(parts) if parts else ""


def selected_style_signature(selected_style: Any) -> str:
    if not selected_style:
        return ""
    return digest(strip_artifact_meta(selected_style))


def dependency_signature(project: Any, slides: list[Any]) -> dict[str, str]:
    return {
        "content": content_signature(slides),
        "style_assets": style_asset_signature(project),
        "visual_assets": visual_asset_signature(project),
        "selected_style": selected_style_signature(getattr(project, "selected_style", None)),
    }


def with_artifact_meta(value: dict | None, **meta: Any) -> dict:
    result = copy.deepcopy(value) if isinstance(value, dict) else {}
    existing = result.get(ARTIFACT_META_KEY) if isinstance(result.get(ARTIFACT_META_KEY), dict) else {}
    result[ARTIFACT_META_KEY] = {**existing, **meta}
    return result


def artifact_meta(value: Any) -> dict:
    if isinstance(value, dict) and isinstance(value.get(ARTIFACT_META_KEY), dict):
        return value[ARTIFACT_META_KEY]
    return {}
