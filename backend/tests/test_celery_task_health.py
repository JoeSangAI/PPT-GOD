from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import slides as slides_api
from app.celery_app import celery_app
from app.core.config import settings
from app.models.base import Base
from app.models.models import Project, Slide
from app.services.run_state import create_project_run, utc_now


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_celery_routes_text_and_image_tasks_to_separate_queues():
    routes = celery_app.conf.task_routes

    assert routes["app.tasks.generate_style_proposals_task"]["queue"] == settings.CELERY_TEXT_QUEUE
    assert routes["app.tasks.generate_style_proposals_task"]["routing_key"] == settings.CELERY_TEXT_QUEUE
    assert routes["app.tasks.generate_slides_task"]["queue"] == settings.CELERY_IMAGE_QUEUE
    assert routes["app.tasks.generate_slides_task"]["routing_key"] == settings.CELERY_IMAGE_QUEUE


def test_generation_worker_check_targets_image_queue(monkeypatch):
    captured = {}

    def fake_ensure_worker(*, queue=None):
        captured["queue"] = queue
        return True

    monkeypatch.setattr(slides_api, "ensure_celery_worker", fake_ensure_worker)

    slides_api.ensure_generation_worker_ready()

    assert captured["queue"] == settings.CELERY_IMAGE_QUEUE


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


def test_pending_celery_task_waits_when_worker_is_online(monkeypatch):
    db = make_session()
    project = Project(
        title="Queued behind busy worker",
        status="prompt_ready",
        content_plan_confirmed=True,
        selected_style={"name": "Style"},
    )
    db.add(project)
    db.flush()
    slide = Slide(
        project_id=project.id,
        page_num=1,
        status="prompt_ready",
        prompt_text="prompt",
    )
    db.add(slide)
    db.flush()
    run = create_project_run(
        db,
        project.id,
        kind="prototype_generation",
        stage="batch_generation",
        target_page_nums=[1],
        total_count=1,
    )
    run.task_id = "queued-task"
    run.started_at = utc_now() - timedelta(seconds=180)
    db.flush()

    monkeypatch.setattr(slides_api, "_celery_result_state", lambda task_id: "PENDING")
    monkeypatch.setattr(slides_api, "_celery_workers_online", lambda: True)
    monkeypatch.setattr(slides_api, "_task_age_seconds", lambda project_id, active_run: 180)

    stale = slides_api._stale_missing_celery_task_if_needed(project, db, run)

    assert stale is False
    assert run.status == "queued"
    assert slide.status == "prompt_ready"


def test_pending_celery_task_stales_quickly_when_worker_is_offline(monkeypatch):
    db = make_session()
    project = Project(
        title="Queued without worker",
        status="prompt_ready",
        content_plan_confirmed=True,
        selected_style={"name": "Style"},
    )
    db.add(project)
    db.flush()
    slide = Slide(
        project_id=project.id,
        page_num=1,
        status="generating",
        prompt_text="prompt",
    )
    db.add(slide)
    db.flush()
    run = create_project_run(
        db,
        project.id,
        kind="prototype_generation",
        stage="batch_generation",
        target_page_nums=[1],
        total_count=1,
    )
    run.task_id = "queued-task"
    run.started_at = utc_now() - timedelta(seconds=180)
    db.flush()

    monkeypatch.setattr(slides_api, "_celery_result_state", lambda task_id: "PENDING")
    monkeypatch.setattr(slides_api, "_celery_workers_online", lambda: False)
    monkeypatch.setattr(slides_api, "_task_age_seconds", lambda project_id, active_run: 180)
    monkeypatch.setattr(slides_api.redis_client, "delete", lambda *_args, **_kwargs: 1)

    stale = slides_api._stale_missing_celery_task_if_needed(project, db, run)

    assert stale is True
    assert run.status == "stale"
    assert "后台生成服务未在线" in run.error_msg
    assert slide.status == "prompt_ready"
