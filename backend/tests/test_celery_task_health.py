from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import slides as slides_api
from app.models.base import Base
from app.models.models import Project, Slide
from app.services.run_state import create_project_run, utc_now


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_missing_started_celery_task_marks_generation_recoverable(monkeypatch):
    db = make_session()
    project = Project(
        title="Lost task",
        status="prompt_ready",
        content_plan_confirmed=True,
        selected_style={"name": "Style"},
    )
    db.add(project)
    db.flush()
    completed = Slide(
        project_id=project.id,
        page_num=1,
        status="completed",
        prompt_text="prompt",
        image_path="/tmp/slide_01.png",
    )
    generating = Slide(
        project_id=project.id,
        page_num=2,
        status="generating",
        prompt_text="prompt",
    )
    db.add_all([completed, generating])
    db.flush()
    run = create_project_run(
        db,
        project.id,
        kind="prototype_generation",
        stage="batch_generation",
        target_page_nums=[1, 2],
        total_count=2,
    )
    run.status = "running"
    run.task_id = "dead-task"
    run.updated_at = utc_now() - timedelta(seconds=600)
    db.flush()

    monkeypatch.setattr(slides_api, "_celery_result_state", lambda task_id: "STARTED")
    monkeypatch.setattr(slides_api, "_celery_task_present_in_worker", lambda task_id: False)
    monkeypatch.setattr(slides_api, "_task_age_seconds", lambda project_id, active_run: 600)
    monkeypatch.setattr(slides_api.redis_client, "delete", lambda *_args, **_kwargs: 1)

    stale = slides_api._stale_missing_celery_task_if_needed(project, db, run)

    assert stale is True
    assert run.status == "stale"
    assert "后台生成服务中断" in run.error_msg
    assert generating.status == "prompt_ready"
    assert "请重试未完成页面" in generating.error_msg
