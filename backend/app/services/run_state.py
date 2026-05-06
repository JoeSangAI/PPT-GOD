from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.models import Project, ProjectRun, Slide


ACTIVE_RUN_STATUSES = {"queued", "running"}
TERMINAL_RUN_STATUSES = {"succeeded", "failed", "cancelled", "stale"}
RUN_PROGRESS_LABELS = {
    "content_plan": "内容规划进度",
    "style_proposal": "风格提案生成进度",
    "visual_prompts": "画面描述生成进度",
    "prototype_generation": "打样生成进度",
    "batch_generation": "批量生成进度",
    "page_generation": "单页生成进度",
    "retry_failed": "失败页重试进度",
    "finetune": "单页微调进度",
}
RUN_PROGRESS_UNITS = {
    "style_proposal": "套",
}

# 全局生成进度存储（内存级，项目重启后丢失）
generation_progress: dict[str, dict] = {}


def cleanup_generation_progress(project_id: str):
    generation_progress.pop(project_id, None)



def utc_now():
    return datetime.now(timezone.utc)


def normalize_page_nums(page_nums: Iterable[int] | None) -> list[int] | None:
    if not page_nums:
        return None
    return sorted({int(p) for p in page_nums})


def get_active_run(db: Session, project_id: str) -> ProjectRun | None:
    return (
        db.query(ProjectRun)
        .filter(ProjectRun.project_id == project_id, ProjectRun.status.in_(ACTIVE_RUN_STATUSES))
        .order_by(ProjectRun.started_at.desc())
        .first()
    )


def get_latest_run(db: Session, project_id: str) -> ProjectRun | None:
    return (
        db.query(ProjectRun)
        .filter(ProjectRun.project_id == project_id)
        .order_by(ProjectRun.started_at.desc())
        .first()
    )


def create_project_run(
    db: Session,
    project_id: str,
    kind: str,
    stage: str,
    target_page_nums: Iterable[int] | None = None,
    total_count: int | None = None,
    message: str | None = None,
) -> ProjectRun:
    existing = get_active_run(db, project_id)
    if existing:
        raise RuntimeError("当前项目已有任务正在运行，请等待完成后再开始下一步")

    pages = normalize_page_nums(target_page_nums)
    run = ProjectRun(
        project_id=project_id,
        kind=kind,
        status="queued",
        stage=stage,
        message=message,
        target_page_nums=pages,
        total_count=int(total_count if total_count is not None else (len(pages) if pages else 0)),
        completed_count=0,
        failed_count=0,
        started_at=utc_now(),
    )
    db.add(run)
    db.flush()
    return run


def set_run_task(db: Session, run_id: str | None, task_id: str | None) -> ProjectRun | None:
    run = get_run(db, run_id)
    if run:
        run.task_id = task_id
        run.status = "queued"
        db.flush()
    return run


def get_run(db: Session, run_id: str | None) -> ProjectRun | None:
    if not run_id:
        return None
    return db.query(ProjectRun).filter(ProjectRun.id == run_id).first()


def mark_run_running(
    db: Session,
    run_id: str | None,
    stage: str | None = None,
    message: str | None = None,
) -> ProjectRun | None:
    run = get_run(db, run_id)
    if run and run.status in ACTIVE_RUN_STATUSES:
        run.status = "running"
        if stage:
            run.stage = stage
        if message is not None:
            run.message = message
        db.flush()
    return run


def update_run_progress(
    db: Session,
    run_id: str | None,
    *,
    stage: str | None = None,
    message: str | None = None,
    completed_count: int | None = None,
    failed_count: int | None = None,
    total_count: int | None = None,
) -> ProjectRun | None:
    run = get_run(db, run_id)
    if run and run.status in ACTIVE_RUN_STATUSES:
        run.status = "running"
        if stage:
            run.stage = stage
        if message is not None:
            run.message = message
        if total_count is not None:
            run.total_count = max(0, int(total_count))
        if completed_count is not None:
            run.completed_count = clamp_count(completed_count, run.total_count)
        if failed_count is not None:
            run.failed_count = clamp_count(failed_count, run.total_count)
        db.flush()
    return run


def finish_run(
    db: Session,
    run_id: str | None,
    *,
    status: str = "succeeded",
    message: str | None = None,
    completed_count: int | None = None,
    failed_count: int | None = None,
    error_msg: str | None = None,
) -> ProjectRun | None:
    run = get_run(db, run_id)
    if run:
        run.status = status
        if message is not None:
            run.message = message
        if completed_count is not None:
            run.completed_count = clamp_count(completed_count, run.total_count)
        if failed_count is not None:
            run.failed_count = clamp_count(failed_count, run.total_count)
        run.error_msg = error_msg
        run.finished_at = utc_now()
        db.flush()
    return run


def cancel_active_run(db: Session, project_id: str, message: str = "任务已取消") -> ProjectRun | None:
    run = get_active_run(db, project_id)
    if run:
        finish_run(db, run.id, status="cancelled", message=message, error_msg=message)
    return run


def stale_active_run(db: Session, project_id: str, message: str) -> ProjectRun | None:
    run = get_active_run(db, project_id)
    if run:
        finish_run(db, run.id, status="stale", message=message, error_msg=message)
    return run


def clamp_count(value: int | None, total: int | None) -> int:
    v = max(0, int(value or 0))
    if total is None or total <= 0:
        return v
    return min(v, int(total))


def target_pages_for_run(run: ProjectRun | None, slides: list[Slide]) -> set[int]:
    if run and run.target_page_nums:
        return {int(p) for p in run.target_page_nums}
    return {s.page_num for s in slides}


def target_counts(run: ProjectRun | None, slides: list[Slide]) -> tuple[int, int, int]:
    if run and run.kind in {"content_plan", "style_proposal", "visual_prompts"}:
        total = run.total_count or 0
        completed = run.completed_count or 0
        failed = run.failed_count or 0
        return total, clamp_count(completed, total), clamp_count(failed, total)

    target_pages = target_pages_for_run(run, slides)
    target_slides = [s for s in slides if s.page_num in target_pages]
    total = len(target_slides) if target_slides else (run.total_count if run else len(slides))
    completed = sum(1 for s in target_slides if s.status == "completed")
    failed = sum(1 for s in target_slides if s.status == "failed")
    return total, clamp_count(completed, total), clamp_count(failed, total)


def serialize_run(run: ProjectRun | None, slides: list[Slide] | None = None) -> dict | None:
    if not run:
        return None
    total = run.total_count or 0
    completed = run.completed_count or 0
    failed = run.failed_count or 0
    if slides is not None:
        total, completed, failed = target_counts(run, slides)
    return {
        "id": run.id,
        "project_id": run.project_id,
        "kind": run.kind,
        "status": run.status,
        "stage": run.stage,
        "message": run.message,
        "target_page_nums": run.target_page_nums,
        "total_count": total,
        "completed_count": clamp_count(completed, total),
        "failed_count": clamp_count(failed, total),
        "task_id": run.task_id,
        "error_msg": run.error_msg,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def serialize_run_progress(run: ProjectRun | None, slides: list[Slide] | None = None) -> dict | None:
    run_data = serialize_run(run, slides)
    if not run_data:
        return None

    total = max(0, int(run_data.get("total_count") or 0))
    completed = clamp_count(run_data.get("completed_count") or 0, total)
    failed = clamp_count(run_data.get("failed_count") or 0, total)
    percent = round((completed / total) * 100, 1) if total > 0 else 0
    kind = run_data.get("kind") or ""
    unit = RUN_PROGRESS_UNITS.get(kind, "页")
    return {
        "run_id": run_data.get("id"),
        "kind": kind,
        "status": run_data.get("status"),
        "stage": run_data.get("stage"),
        "label": RUN_PROGRESS_LABELS.get(kind, "任务进度"),
        "message": run_data.get("message") or RUN_PROGRESS_LABELS.get(kind, "任务处理中"),
        "current": completed,
        "total": total,
        "failed": failed,
        "unit": unit,
        "percent": percent,
        "target_page_nums": run_data.get("target_page_nums"),
        "can_cancel": run_data.get("status") in ACTIVE_RUN_STATUSES,
        # Backward-compatible aliases for older frontend surfaces while the UI is
        # migrated to current/total.
        "current_page": completed,
        "total_pages": total,
    }


def serialize_workflow_status(
    project: Project,
    slides: list[Slide],
    *,
    active_run: ProjectRun | None = None,
    latest_run: ProjectRun | None = None,
    has_pptx: bool = False,
    pptx_path: str | None = None,
) -> dict:
    target_count, target_completed, target_failed = target_counts(active_run, slides)
    total_completed = sum(1 for s in slides if s.status == "completed")
    target_page_nums = active_run.target_page_nums if active_run else None

    return {
        "project_id": project.id,
        "project_phase": project.status,
        # Backward-compatible name used by existing frontend code.
        "project_status": project.status,
        "total_slides": len(slides),
        "completed_slides": target_completed,
        "total_completed_slides": total_completed,
        "target_completed_slides": target_completed,
        "target_failed_slides": target_failed,
        "target_count": target_count or len(slides),
        "target_page_nums": target_page_nums,
        "active_run": serialize_run(active_run, slides),
        "last_run": serialize_run(latest_run, slides) if latest_run else None,
        "progress": serialize_run_progress(active_run, slides),
        "has_pptx": has_pptx,
        "pptx_path": pptx_path if has_pptx else None,
        "slides": [
            {
                "id": s.id,
                "page_num": s.page_num,
                "status": s.status,
                "error_msg": s.error_msg,
            }
            for s in slides
        ],
    }


def infer_project_stage_from_slides(project: Project, slides: list[Slide]) -> str:
    if not slides:
        return "draft"
    if all(s.status == "completed" for s in slides):
        return "completed"
    if any(s.prompt_text for s in slides):
        return "prompt_ready"
    if any(s.visual_json for s in slides):
        return "visual_ready"
    return "planning"


def normalize_confirmed_project_stage(project: Project, slides: list[Slide], run: ProjectRun | None = None) -> str:
    """
    Repair legacy/edge states where content was confirmed but the project still
    advertises planning. This is a no-op while a run is active.
    """
    if run and run.status in ACTIVE_RUN_STATUSES:
        return project.status
    if project.content_plan_confirmed and slides and project.status in {"draft", "planning"}:
        project.status = "visual_ready"
    return project.status


def reconcile_project_state(project: Project, slides: list[Slide], run: ProjectRun | None = None) -> str:
    if run and run.status in ACTIVE_RUN_STATUSES:
        return project.status

    normalize_confirmed_project_stage(project, slides, run)

    if run and run.status == "succeeded":
        if run.kind == "content_plan":
            project.status = "planning"
        elif run.kind == "style_proposal":
            project.status = "visual_ready"
        elif run.kind == "visual_prompts":
            project.status = "prompt_ready" if any(s.prompt_text for s in slides) else "visual_ready"
        elif run.kind == "prototype_generation":
            project.status = "prototype_ready"
        elif run.kind in {"batch_generation", "page_generation", "retry_failed", "finetune"}:
            generating = any(s.status == "generating" for s in slides)
            if generating:
                project.status = "prompt_ready"
            elif slides and all(s.status == "completed" for s in slides):
                project.status = "completed"
            else:
                project.status = "prompt_ready"
        return project.status

    if run and run.status in {"failed", "cancelled", "stale"}:
        if project.status not in {"draft", "planning", "visual_ready", "prompt_ready", "prototype_ready", "completed", "failed"}:
            project.status = infer_project_stage_from_slides(project, slides)
        return project.status

    return project.status
