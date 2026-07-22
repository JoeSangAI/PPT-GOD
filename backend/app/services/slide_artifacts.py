from __future__ import annotations

from io import BytesIO
import os
import shutil
import uuid

from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import Project, Slide, SlideVersion


class SlideArtifactError(ValueError):
    pass


def _archive_current_image(slide: Slide, db: Session) -> None:
    if not slide.image_path or not os.path.exists(slide.image_path):
        return
    latest = (
        db.query(SlideVersion)
        .filter(SlideVersion.slide_id == slide.id)
        .order_by(SlideVersion.version_number.desc())
        .first()
    )
    version_number = (latest.version_number + 1) if latest else 1
    version_dir = os.path.join(settings.OUTPUT_DIR, slide.project_id, "versions")
    os.makedirs(version_dir, exist_ok=True)
    version_path = os.path.join(version_dir, f"slide_{slide.page_num:02d}_v{version_number}.png")
    shutil.copy2(slide.image_path, version_path)
    db.add(SlideVersion(
        slide_id=slide.id,
        project_id=slide.project_id,
        image_path=version_path,
        prompt_text=slide.prompt_text,
        version_number=version_number,
    ))


def import_slide_image_artifact(
    db: Session,
    project: Project,
    slide: Slide,
    image_bytes: bytes,
    *,
    source: str = "external_agent",
) -> dict:
    if not image_bytes:
        raise SlideArtifactError("页面图片为空。")
    if len(image_bytes) > 40 * 1024 * 1024:
        raise SlideArtifactError("页面图片超过 40MB，请压缩后重试。")
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image.load()
            width, height = image.size
            normalized = image.convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise SlideArtifactError("无法识别页面图片，请上传 PNG、JPG 或 WebP。") from exc
    if width < 800 or height < 450:
        raise SlideArtifactError("页面图片至少需要 800×450 像素。")
    ratio = width / height
    if abs(ratio - (16 / 9)) > 0.05:
        raise SlideArtifactError("页面图片需要是 16:9 比例。")

    _archive_current_image(slide, db)
    output_dir = os.path.join(settings.OUTPUT_DIR, project.id, "agent-artifacts")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(
        output_dir,
        f"slide_{slide.page_num:02d}_{uuid.uuid4().hex[:10]}.png",
    )
    normalized.save(output_path, format="PNG", optimize=True)

    visual = dict(slide.visual_json or {})
    visual["artifact_source"] = str(source or "external_agent")[:80]
    visual["artifact_dimensions"] = {"width": width, "height": height}
    slide.visual_json = visual
    slide.image_path = output_path
    slide.status = "completed"
    slide.error_msg = None

    project_slides = db.query(Slide).filter(Slide.project_id == project.id).all()
    project.status = "completed" if project_slides and all(item.image_path for item in project_slides) else "prototype_ready"
    db.commit()
    db.refresh(slide)
    db.refresh(project)
    return {
        "ok": True,
        "project_id": project.id,
        "slide_id": slide.id,
        "page_num": slide.page_num,
        "image_path": slide.image_path,
        "width": width,
        "height": height,
        "source": visual["artifact_source"],
        "project_status": project.status,
    }
