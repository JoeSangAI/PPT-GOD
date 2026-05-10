from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.models import Project, Slide
from app.services.artifact_versions import dependency_signature, with_artifact_meta
from app.services.run_state import (
    apply_project_rollback,
    create_project_run,
    normalize_confirmed_project_stage,
    reconcile_project_state,
    serialize_run,
    serialize_workflow_status,
    stale_inactive_run_if_needed,
    stale_queued_run_if_needed,
    utc_now,
    target_counts,
    update_run_progress,
)


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_target_progress_is_scoped_to_run_pages():
    db = make_session()
    project = Project(title="Scoped progress", status="prompt_ready")
    db.add(project)
    db.flush()
    for page_num in range(1, 5):
        db.add(
            Slide(
                project_id=project.id,
                page_num=page_num,
                status="completed" if page_num in {1, 2, 3} else "prompt_ready",
                prompt_text="prompt",
            )
        )
    db.flush()

    run = create_project_run(
        db,
        project.id,
        kind="page_generation",
        stage="batch_generation",
        target_page_nums=[2, 3],
        total_count=2,
    )
    slides = db.query(Slide).filter(Slide.project_id == project.id).order_by(Slide.page_num).all()

    target_total, target_completed, target_failed = target_counts(run, slides)
    payload = serialize_run(run, slides)

    assert target_total == 2
    assert target_completed == 2
    assert target_failed == 0
    assert payload["completed_count"] == 2
    assert payload["completed_count"] <= payload["total_count"]


def test_active_run_guard_rejects_second_run():
    db = make_session()
    project = Project(title="One active run", status="planning")
    db.add(project)
    db.flush()

    create_project_run(db, project.id, kind="content_plan", stage="content_plan", total_count=10)

    try:
        create_project_run(db, project.id, kind="style_proposal", stage="style_proposal", total_count=3)
    except RuntimeError as exc:
        assert "已有任务正在运行" in str(exc)
    else:
        raise AssertionError("expected active run guard to reject a second run")


def test_stale_queued_run_unblocks_next_run():
    db = make_session()
    project = Project(title="Stale queued run", status="visual_ready")
    db.add(project)
    db.flush()

    stale_run = create_project_run(db, project.id, kind="style_proposal", stage="style_proposal", total_count=1)
    stale_run.started_at = utc_now() - timedelta(seconds=300)
    db.flush()

    stale_queued_run_if_needed(db, project.id, timeout_seconds=120)
    next_run = create_project_run(db, project.id, kind="style_proposal", stage="style_proposal", total_count=1)

    assert stale_run.status == "stale"
    assert "后台生成服务未接收" in stale_run.error_msg
    assert next_run.id != stale_run.id
    assert next_run.status == "queued"


def test_stale_running_run_without_heartbeat():
    db = make_session()
    project = Project(title="Dead background task", status="visual_ready")
    db.add(project)
    db.flush()

    run = create_project_run(db, project.id, kind="visual_prompts", stage="visual_planning", total_count=10)
    update_run_progress(db, run.id, completed_count=5, message="正在生成视觉方案")
    run.updated_at = utc_now() - timedelta(seconds=600)
    db.flush()

    stale_inactive_run_if_needed(db, project.id, heartbeat_timeout_seconds=300)

    assert run.status == "stale"
    assert "没有进度更新" in run.error_msg


def test_confirmed_planning_project_normalizes_to_visual_ready():
    db = make_session()
    project = Project(title="Legacy confirmed", status="planning", content_plan_confirmed=True)
    db.add(project)
    db.flush()
    slide = Slide(project_id=project.id, page_num=1, status="pending", content_json={"page_num": 1})
    db.add(slide)
    db.flush()

    normalize_confirmed_project_stage(project, [slide])

    assert project.status == "visual_ready"


def test_reconcile_clears_downstream_outputs_without_selected_style():
    db = make_session()
    project = Project(title="Broken nonlinear state", status="prompt_ready", content_plan_confirmed=True)
    db.add(project)
    db.flush()
    slide = Slide(
        project_id=project.id,
        page_num=1,
        status="completed",
        content_json={"page_num": 1},
        visual_json={"layout": "old"},
        prompt_text="old prompt",
        image_path="/tmp/old.png",
    )
    db.add(slide)
    db.flush()

    reconcile_project_state(project, [slide])

    assert project.status == "visual_ready"
    assert slide.visual_json == {}
    assert slide.prompt_text is None
    assert slide.image_path is None
    assert slide.status == "pending"


def test_reconcile_invalidates_signed_visual_outputs_when_content_changes():
    db = make_session()
    project = Project(
        title="Signed outputs",
        status="prompt_ready",
        content_plan_confirmed=True,
        selected_style={"name": "Brand"},
    )
    db.add(project)
    db.flush()
    slide = Slide(
        project_id=project.id,
        page_num=1,
        status="prompt_ready",
        content_json={"page_num": 1, "title": "旧标题"},
        visual_json={"layout": "old"},
        prompt_text="old prompt",
    )
    db.add(slide)
    db.flush()
    slide.visual_json = with_artifact_meta(
        slide.visual_json,
        kind="visual_plan",
        dependencies=dependency_signature(project, [slide]),
    )
    slide.content_json = {"page_num": 1, "title": "新标题"}
    db.flush()

    reconcile_project_state(project, [slide])

    assert project.status == "visual_ready"
    assert slide.prompt_text is None
    assert slide.image_path is None
    assert slide.status == "pending"
    assert slide.visual_json == {}


def test_rollback_to_prompt_requires_selected_style():
    db = make_session()
    project = Project(title="Prompt rollback without style", status="completed", content_plan_confirmed=True)
    db.add(project)
    db.flush()
    slide = Slide(
        project_id=project.id,
        page_num=1,
        status="completed",
        content_json={"page_num": 1},
        visual_json={"layout": "old"},
        prompt_text="old prompt",
        image_path="/tmp/old.png",
    )
    db.add(slide)
    db.flush()

    apply_project_rollback(project, [slide], "prompt_ready")

    assert project.status == "visual_ready"
    assert project.selected_style is None
    assert slide.visual_json == {}
    assert slide.prompt_text is None
    assert slide.image_path is None


def test_workflow_status_exposes_unified_progress_view_model():
    db = make_session()
    project = Project(title="Unified progress", status="prompt_ready")
    db.add(project)
    db.flush()
    for page_num in range(1, 4):
        db.add(
            Slide(
                project_id=project.id,
                page_num=page_num,
                status="completed" if page_num == 1 else "generating",
                prompt_text="prompt",
            )
        )
    db.flush()
    run = create_project_run(
        db,
        project.id,
        kind="batch_generation",
        stage="batch_generation",
        target_page_nums=[1, 2, 3],
        total_count=3,
        message="正在生成图片",
    )
    update_run_progress(db, run.id, completed_count=1, failed_count=0)
    slides = db.query(Slide).filter(Slide.project_id == project.id).order_by(Slide.page_num).all()

    payload = serialize_workflow_status(project, slides, active_run=run, latest_run=run)

    assert payload["project_phase"] == "prompt_ready"
    assert payload["project_status"] == "prompt_ready"
    assert payload["active_run"]["id"] == run.id
    assert payload["progress"]["label"] == "批量生成进度"
    assert payload["progress"]["current"] == 1
    assert payload["progress"]["total"] == 3
    assert payload["progress"]["unit"] == "页"
    assert payload["progress"]["active_page_nums"] == [2, 3]
    assert payload["progress"]["running_count"] == 2
    assert payload["progress"]["can_cancel"] is True
