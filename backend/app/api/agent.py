from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.tester_auth import is_local_admin_request, require_existing_tester, require_tester_id
from app.models.base import get_db
from app.services.content_plan_markdown import ContentPlanMarkdownError, import_content_plan_markdown


router = APIRouter(prefix="/agent", tags=["agent"])


class ImportContentPlanRequest(BaseModel):
    markdown: str = Field(min_length=1)
    title: str | None = None
    source_filename: str | None = None
    frontend_base_url: str = "http://localhost:5173"


@router.post("/content-plans/import")
def import_content_plan(
    payload: ImportContentPlanRequest,
    tester_id: str = Depends(require_tester_id),
    db: Session = Depends(get_db),
):
    owner_tester_id = None if is_local_admin_request(tester_id) else require_existing_tester(db, tester_id).id
    try:
        receipt = import_content_plan_markdown(
            db,
            payload.markdown,
            title=payload.title,
            tester_id=owner_tester_id,
            source_filename=payload.source_filename,
            frontend_base_url=payload.frontend_base_url,
        )
    except ContentPlanMarkdownError as exc:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail={
                "message": "内容规划 Markdown 格式不合格",
                "errors": exc.errors,
                "warnings": exc.warnings,
            },
        ) from exc

    return {
        "ok": True,
        "project_id": receipt.project_id,
        "title": receipt.title,
        "slides_count": receipt.slides_count,
        "warnings": receipt.warnings,
        "ui_url": receipt.ui_url,
        "next_action": {
            "type": "open_ui",
            "label": "打开内容确认页",
            "url": receipt.ui_url,
        },
    }
