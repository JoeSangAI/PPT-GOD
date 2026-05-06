from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.models import Project, Slide
from app.services.run_state import (
    create_project_run,
    normalize_confirmed_project_stage,
    serialize_run,
    serialize_workflow_status,
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
    assert payload["progress"]["can_cancel"] is True
