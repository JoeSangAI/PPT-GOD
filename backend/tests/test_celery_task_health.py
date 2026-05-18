from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import slides as slides_api
from app.celery_app import celery_app
from app.core.config import settings
from app.models.base import Base
from app.models.models import Project, Slide
from app import tasks as image_tasks
from app.services import generation_pipeline
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


def test_stop_generation_releases_page_locks(monkeypatch):
    db = make_session()
    project = Project(title="Stop clears locks", status="prototype", content_plan_confirmed=True)
    db.add(project)
    db.flush()
    db.add_all(
        [
            Slide(project_id=project.id, page_num=1, status="generating", prompt_text="p1"),
            Slide(project_id=project.id, page_num=2, status="generating", prompt_text="p2"),
            Slide(project_id=project.id, page_num=3, status="completed", prompt_text="p3"),
        ]
    )
    run = create_project_run(
        db,
        project.id,
        kind="prototype_generation",
        stage="batch_generation",
        target_page_nums=[1, 2, 3],
        total_count=3,
    )
    run.status = "running"
    run.task_id = "task-1"
    db.commit()

    deleted = []
    revoked = {}

    class FakeRedis:
        def get(self, key):
            return b"task-1" if key.endswith(":task_id") else None

        def delete(self, *keys):
            deleted.extend(keys)
            return len(keys)

    class FakeAsyncResult:
        def __init__(self, task_id):
            revoked["task_id"] = task_id

        def revoke(self, terminate=False):
            revoked["terminate"] = terminate

    monkeypatch.setattr(slides_api, "redis_client", FakeRedis())
    monkeypatch.setattr(slides_api, "AsyncResult", FakeAsyncResult)

    result = slides_api.stop_generation(project.id, db=db)

    assert result["message"] == "Generation stopped"
    assert revoked == {"task_id": "task-1", "terminate": True}
    for page_num in (1, 2, 3):
        assert f"project:{project.id}:slide:{page_num}:generating" in deleted


def test_generation_page_chunks_are_bounded(monkeypatch):
    monkeypatch.setattr(image_tasks.settings, "IMAGE_GENERATION_TASK_PAGE_CHUNK_SIZE", 3)

    current, remaining = image_tasks._split_generation_pages([1, "2", 3, 4, 5])

    assert current == [1, 2, 3]
    assert remaining == [4, 5]


def test_generation_task_queues_continuation_with_same_credential(monkeypatch):
    db = make_session()
    project = Project(title="Continuation run", status="prompt_ready", content_plan_confirmed=True)
    db.add(project)
    db.flush()
    db.add_all(
        [
            Slide(project_id=project.id, page_num=1, status="prompt_ready", prompt_text="p1"),
            Slide(project_id=project.id, page_num=2, status="prompt_ready", prompt_text="p2"),
            Slide(project_id=project.id, page_num=3, status="prompt_ready", prompt_text="p3"),
        ]
    )
    db.flush()
    run = create_project_run(
        db,
        project.id,
        kind="batch_generation",
        stage="batch_generation",
        target_page_nums=[1, 2, 3],
        total_count=3,
    )
    run.status = "running"
    db.commit()
    project_id = project.id
    run_id = run.id

    captured = {}

    class NextTask:
        id = "next-task"

    def fake_enqueue(_db, project_id, remaining_page_nums, *, prototype, run_id, credential_id):
        captured.update(
            project_id=project_id,
            remaining_page_nums=remaining_page_nums,
            prototype=prototype,
            run_id=run_id,
            credential_id=credential_id,
        )
        return NextTask()

    monkeypatch.setattr(image_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(image_tasks.settings, "IMAGE_GENERATION_TASK_PAGE_CHUNK_SIZE", 2)
    monkeypatch.setattr(image_tasks, "_acquire_page_locks", lambda _project_id, pages: pages)
    monkeypatch.setattr(image_tasks, "_release_page_locks", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(image_tasks, "_cleanup_stale_generating_slides", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(image_tasks, "run_generation_pipeline", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(image_tasks, "is_run_active", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(image_tasks, "_enqueue_generation_continuation", fake_enqueue)

    result = image_tasks._generate_slides_task_inner(
        project_id,
        page_nums=[1, 2, 3],
        run_id=run_id,
        credential_id="credential-1",
    )

    assert result["status"] == "continued"
    assert captured == {
        "project_id": project_id,
        "remaining_page_nums": [3],
        "prototype": False,
        "run_id": run_id,
        "credential_id": "credential-1",
    }


def test_deferred_generation_chunk_keeps_run_active(monkeypatch):
    db = make_session()
    project = Project(title="Chunked run", status="prompt_ready", content_plan_confirmed=True)
    db.add(project)
    db.flush()
    db.add_all(
        [
            Slide(project_id=project.id, page_num=1, status="prompt_ready", prompt_text="p1"),
            Slide(project_id=project.id, page_num=2, status="prompt_ready", prompt_text="p2"),
            Slide(project_id=project.id, page_num=3, status="prompt_ready", prompt_text="p3"),
        ]
    )
    db.flush()
    run = create_project_run(
        db,
        project.id,
        kind="batch_generation",
        stage="batch_generation",
        target_page_nums=[1, 2, 3],
        total_count=3,
    )
    run.status = "running"
    db.commit()

    def fake_generate_one_slide(slide, project_id, output_dir, ref_data, run_id=None):
        return {"slide": slide, "image_path": f"/tmp/slide_{slide.page_num:02d}.png"}

    monkeypatch.setattr(generation_pipeline, "_load_reference_images", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(generation_pipeline, "_generate_one_slide", fake_generate_one_slide)
    monkeypatch.setattr(generation_pipeline, "assemble_pptx", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not assemble")))

    generation_pipeline.run_generation_pipeline(
        project.id,
        db,
        page_nums=[1, 2],
        run_id=run.id,
        defer_finalization=True,
    )

    refreshed_run = db.query(run.__class__).filter(run.__class__.id == run.id).one()
    refreshed_slides = db.query(Slide).filter(Slide.project_id == project.id).order_by(Slide.page_num).all()

    assert refreshed_run.status == "running"
    assert refreshed_run.total_count == 3
    assert refreshed_run.completed_count == 2
    assert [s.status for s in refreshed_slides] == ["completed", "completed", "prompt_ready"]


def test_final_generation_chunk_finishes_shared_run(tmp_path, monkeypatch):
    db = make_session()
    project = Project(title="Final chunk", status="prompt_ready", content_plan_confirmed=True)
    db.add(project)
    db.flush()
    existing_paths = []
    for page_num in (1, 2):
        image_path = tmp_path / f"slide_{page_num:02d}.png"
        image_path.write_bytes(b"image")
        existing_paths.append(str(image_path))
    db.add_all(
        [
            Slide(project_id=project.id, page_num=1, status="completed", prompt_text="p1", image_path=existing_paths[0]),
            Slide(project_id=project.id, page_num=2, status="completed", prompt_text="p2", image_path=existing_paths[1]),
            Slide(project_id=project.id, page_num=3, status="prompt_ready", prompt_text="p3"),
        ]
    )
    db.flush()
    run = create_project_run(
        db,
        project.id,
        kind="batch_generation",
        stage="batch_generation",
        target_page_nums=[1, 2, 3],
        total_count=3,
    )
    run.status = "running"
    db.commit()

    generated_path = tmp_path / "slide_03.png"
    generated_path.write_bytes(b"image")
    assembled = {}

    def fake_generate_one_slide(slide, project_id, output_dir, ref_data, run_id=None):
        return {"slide": slide, "image_path": str(generated_path)}

    def fake_assemble_pptx(**kwargs):
        assembled["count"] = len(kwargs["slide_images"])

    monkeypatch.setattr(generation_pipeline, "_load_reference_images", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(generation_pipeline, "_generate_one_slide", fake_generate_one_slide)
    monkeypatch.setattr(generation_pipeline, "assemble_pptx", fake_assemble_pptx)
    monkeypatch.setattr(generation_pipeline.redis_client, "set", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(generation_pipeline.redis_client, "delete", lambda *_args, **_kwargs: 1)

    generation_pipeline.run_generation_pipeline(project.id, db, page_nums=[3], run_id=run.id)

    refreshed_run = db.query(run.__class__).filter(run.__class__.id == run.id).one()
    refreshed_project = db.query(Project).filter(Project.id == project.id).one()

    assert refreshed_run.status == "succeeded"
    assert refreshed_run.completed_count == 3
    assert refreshed_project.status == "completed"
    assert assembled["count"] == 3


def test_generation_retries_once_on_provider_gateway_cutoff_then_stops(monkeypatch):
    db = make_session()
    project = Project(title="Provider outage", status="prompt_ready", content_plan_confirmed=True)
    db.add(project)
    db.flush()
    db.add_all(
        [
            Slide(project_id=project.id, page_num=1, status="prompt_ready", prompt_text="p1"),
            Slide(project_id=project.id, page_num=2, status="prompt_ready", prompt_text="p2"),
        ]
    )
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
    db.commit()

    calls = []

    def fake_generate_one_slide(slide, project_id, output_dir, ref_data, run_id=None):
        calls.append(slide.page_num)
        return {
            "slide": slide,
            "error": "图片接口超过约 120 秒仍未返回，被上游连接窗口截断；已停止重复重试。",
            "image_generation_events": [{"status": "gateway_timeout"}],
        }

    monkeypatch.setattr(generation_pipeline.settings, "IMAGE_API_MAX_CONCURRENCY", 1)
    monkeypatch.setattr(generation_pipeline, "_load_reference_images", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(generation_pipeline, "_generate_one_slide", fake_generate_one_slide)
    monkeypatch.setattr(
        generation_pipeline,
        "assemble_pptx",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not assemble")),
    )

    generation_pipeline.run_generation_pipeline(project.id, db, page_nums=[1, 2], prototype=True, run_id=run.id)

    refreshed_run = db.query(run.__class__).filter(run.__class__.id == run.id).one()
    refreshed_slides = db.query(Slide).filter(Slide.project_id == project.id).order_by(Slide.page_num).all()

    # Gateway cutoff is transient -> retried once on page 1, then pipeline stops
    assert calls == [1, 1]
    assert refreshed_run.status == "failed"
    assert refreshed_run.completed_count == 0
    assert refreshed_run.failed_count == 1
    assert refreshed_slides[0].status == "failed"
    assert refreshed_slides[1].status == "prompt_ready"


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


def test_stale_detection_releases_redis_page_locks(monkeypatch):
    db = make_session()
    project = Project(
        title="Lock release",
        status="prompt_ready",
        content_plan_confirmed=True,
        selected_style={"name": "Style"},
    )
    db.add(project)
    db.flush()
    generating = Slide(
        project_id=project.id,
        page_num=2,
        status="generating",
        prompt_text="prompt",
    )
    db.add(generating)
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

    deleted_locks = []

    def capture_delete(*keys):
        deleted_locks.extend(keys)
        return len(keys)

    monkeypatch.setattr(slides_api, "_celery_result_state", lambda task_id: "STARTED")
    monkeypatch.setattr(slides_api, "_celery_task_present_in_worker", lambda task_id: False)
    monkeypatch.setattr(slides_api, "_task_age_seconds", lambda project_id, active_run: 600)
    monkeypatch.setattr(slides_api.redis_client, "delete", capture_delete)

    stale = slides_api._stale_missing_celery_task_if_needed(project, db, run)

    assert stale is True
    assert f"project:{project.id}:slide:2:generating" in deleted_locks
    assert generating.status == "prompt_ready"


def test_start_generation_pre_cleans_stale_generating_slides(monkeypatch):
    db = make_session()
    project = Project(
        title="Pre-clean",
        status="prompt_ready",
        content_plan_confirmed=True,
        selected_style={"name": "Style"},
    )
    db.add(project)
    db.flush()
    generating = Slide(
        project_id=project.id,
        page_num=1,
        status="generating",
        prompt_text="prompt",
    )
    db.add(generating)
    db.flush()

    cleaned = []

    def fake_cleanup(db, project_id):
        slide = db.query(Slide).filter(
            Slide.project_id == project_id,
            Slide.status == "generating",
        ).first()
        if slide:
            slide.status = "prompt_ready"
            slide.error_msg = None
            cleaned.append(slide.page_num)
        return len(cleaned)

    class FakeTask:
        id = "task-1"

    monkeypatch.setattr(slides_api, "_cleanup_stale_generating_slides_for_project", fake_cleanup)
    monkeypatch.setattr(slides_api, "ensure_generation_worker_ready", lambda: True)
    monkeypatch.setattr(slides_api, "_enqueue_generation_task", lambda *args, **kwargs: FakeTask())

    result = slides_api.start_generation(project.id, db=db)

    assert cleaned == [1]
    assert result["message"] == "Generation started"


def test_skipped_pages_are_appended_to_remaining_pages(monkeypatch):
    db = make_session()
    project = Project(title="Skipped retry", status="prompt_ready", content_plan_confirmed=True)
    db.add(project)
    db.flush()
    db.add_all(
        [
            Slide(project_id=project.id, page_num=1, status="prompt_ready", prompt_text="p1"),
            Slide(project_id=project.id, page_num=2, status="prompt_ready", prompt_text="p2"),
            Slide(project_id=project.id, page_num=3, status="prompt_ready", prompt_text="p3"),
        ]
    )
    db.flush()
    run = create_project_run(
        db,
        project.id,
        kind="batch_generation",
        stage="batch_generation",
        target_page_nums=[1, 2, 3],
        total_count=3,
    )
    run.status = "running"
    db.commit()
    project_id = project.id
    run_id = run.id

    captured = {}

    class NextTask:
        id = "next-task"

    def fake_enqueue(_db, project_id, remaining_page_nums, *, prototype, run_id, credential_id):
        captured.update(remaining_page_nums=remaining_page_nums)
        return NextTask()

    monkeypatch.setattr(image_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(image_tasks.settings, "IMAGE_GENERATION_TASK_PAGE_CHUNK_SIZE", 2)
    # Simulate page 1 being locked by another task
    monkeypatch.setattr(image_tasks, "_acquire_page_locks", lambda _project_id, pages: [p for p in pages if p != 1])
    monkeypatch.setattr(image_tasks, "_release_page_locks", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(image_tasks, "_cleanup_stale_generating_slides", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(image_tasks, "run_generation_pipeline", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(image_tasks, "is_run_active", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(image_tasks, "_enqueue_generation_continuation", fake_enqueue)

    result = image_tasks._generate_slides_task_inner(
        project_id,
        page_nums=[1, 2, 3],
        run_id=run_id,
        credential_id="credential-1",
    )

    assert result["status"] == "continued"
    # Page 1 was skipped (locked), page 3 was remaining from chunking
    assert captured["remaining_page_nums"] == [3, 1]


def test_transient_error_triggers_one_retry(monkeypatch):
    db = make_session()
    project = Project(title="Transient retry", status="prompt_ready", content_plan_confirmed=True)
    db.add(project)
    db.flush()
    db.add(
        Slide(project_id=project.id, page_num=1, status="prompt_ready", prompt_text="p1"),
    )
    db.flush()
    run = create_project_run(
        db,
        project.id,
        kind="batch_generation",
        stage="batch_generation",
        target_page_nums=[1],
        total_count=1,
    )
    run.status = "running"
    db.commit()

    calls = []

    def fake_generate_one_slide(slide, project_id, output_dir, ref_data, run_id=None):
        calls.append("attempt")
        if len(calls) == 1:
            return {"slide": slide, "error": "Timed out waiting for image API slot after 600s"}
        return {"slide": slide, "image_path": "/tmp/slide_01.png"}

    monkeypatch.setattr(generation_pipeline.settings, "IMAGE_API_MAX_CONCURRENCY", 1)
    monkeypatch.setattr(generation_pipeline, "_load_reference_images", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(generation_pipeline, "_generate_one_slide", fake_generate_one_slide)
    monkeypatch.setattr(generation_pipeline.redis_client, "set", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(generation_pipeline.redis_client, "delete", lambda *_args, **_kwargs: 1)

    generation_pipeline.run_generation_pipeline(project.id, db, page_nums=[1], run_id=run.id)

    refreshed_slide = db.query(Slide).filter(Slide.project_id == project.id).one()
    assert calls == ["attempt", "attempt"]
    assert refreshed_slide.status == "completed"


def test_non_transient_error_does_not_retry(monkeypatch):
    db = make_session()
    project = Project(title="Non-transient no retry", status="prompt_ready", content_plan_confirmed=True)
    db.add(project)
    db.flush()
    db.add(
        Slide(project_id=project.id, page_num=1, status="prompt_ready", prompt_text="p1"),
    )
    db.flush()
    run = create_project_run(
        db,
        project.id,
        kind="batch_generation",
        stage="batch_generation",
        target_page_nums=[1],
        total_count=1,
    )
    run.status = "running"
    db.commit()

    calls = []

    def fake_generate_one_slide(slide, project_id, output_dir, ref_data, run_id=None):
        calls.append("attempt")
        return {"slide": slide, "error": "Content policy violation: invalid prompt"}

    monkeypatch.setattr(generation_pipeline.settings, "IMAGE_API_MAX_CONCURRENCY", 1)
    monkeypatch.setattr(generation_pipeline, "_load_reference_images", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(generation_pipeline, "_generate_one_slide", fake_generate_one_slide)
    monkeypatch.setattr(generation_pipeline.redis_client, "set", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(generation_pipeline.redis_client, "delete", lambda *_args, **_kwargs: 1)

    generation_pipeline.run_generation_pipeline(project.id, db, page_nums=[1], run_id=run.id)

    refreshed_slide = db.query(Slide).filter(Slide.project_id == project.id).one()
    assert calls == ["attempt"]
    assert refreshed_slide.status == "failed"


def test_stale_detection_auto_restarts_generation(monkeypatch):
    db = make_session()
    project = Project(
        title="Auto-restart",
        status="prompt_ready",
        content_plan_confirmed=True,
        selected_style={"name": "Style"},
    )
    db.add(project)
    db.flush()
    db.add_all(
        [
            Slide(project_id=project.id, page_num=1, status="generating", prompt_text="p1"),
            Slide(project_id=project.id, page_num=2, status="prompt_ready", prompt_text="p2"),
            Slide(project_id=project.id, page_num=3, status="completed", prompt_text="p3", image_path="/tmp/03.png"),
        ]
    )
    db.flush()
    run = create_project_run(
        db,
        project.id,
        kind="batch_generation",
        stage="batch_generation",
        target_page_nums=[1, 2, 3],
        total_count=3,
    )
    run.status = "running"
    run.task_id = "dead-task"
    run.completed_count = 1  # Page 3 already completed, so auto-restart should trigger
    run.updated_at = utc_now() - timedelta(seconds=600)
    db.flush()

    enqueued = {}

    class FakeTask:
        id = "auto-restart-task"

    def fake_enqueue_task(db, project_id, page_nums, *, run, prototype=False):
        enqueued.update(
            project_id=project_id,
            page_nums=page_nums,
            run_id=run.id,
            prototype=prototype,
        )
        return FakeTask()

    monkeypatch.setattr(slides_api, "_celery_result_state", lambda task_id: "STARTED")
    monkeypatch.setattr(slides_api, "_celery_task_present_in_worker", lambda task_id: False)
    monkeypatch.setattr(slides_api, "_task_age_seconds", lambda project_id, active_run: 600)
    monkeypatch.setattr(slides_api.redis_client, "delete", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(slides_api.redis_client, "set", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(slides_api.redis_client, "get", lambda key: None)
    monkeypatch.setattr(slides_api, "_enqueue_generation_task", fake_enqueue_task)

    stale = slides_api._stale_missing_celery_task_if_needed(project, db, run)

    assert stale is True
    # Auto-restart should pick up page 1 (was generating, reset to prompt_ready) and page 2 (prompt_ready)
    # Page 3 is completed, so excluded
    assert enqueued["page_nums"] == [1, 2]
    assert enqueued["prototype"] is False


def test_stale_detection_auto_restart_only_once(monkeypatch):
    db = make_session()
    project = Project(
        title="Auto-restart once",
        status="prompt_ready",
        content_plan_confirmed=True,
        selected_style={"name": "Style"},
    )
    db.add(project)
    db.flush()
    db.add(
        Slide(project_id=project.id, page_num=1, status="generating", prompt_text="p1"),
    )
    db.flush()
    run = create_project_run(
        db,
        project.id,
        kind="batch_generation",
        stage="batch_generation",
        target_page_nums=[1],
        total_count=1,
    )
    run.status = "running"
    run.task_id = "dead-task"
    run.completed_count = 1
    run.updated_at = utc_now() - timedelta(seconds=600)
    db.flush()

    enqueue_calls = []

    class FakeTask:
        id = "auto-restart-task"

    def fake_enqueue_task(db, project_id, page_nums, *, run, prototype=False):
        enqueue_calls.append(run.id)
        return FakeTask()

    redis_store = {}

    def fake_redis_get(key):
        val = redis_store.get(key)
        return val.encode() if val else None

    def fake_redis_set(key, value, ex=None):
        redis_store[key] = value

    monkeypatch.setattr(slides_api, "_celery_result_state", lambda task_id: "STARTED")
    monkeypatch.setattr(slides_api, "_celery_task_present_in_worker", lambda task_id: False)
    monkeypatch.setattr(slides_api, "_task_age_seconds", lambda project_id, active_run: 600)
    monkeypatch.setattr(slides_api.redis_client, "delete", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(slides_api.redis_client, "set", fake_redis_set)
    monkeypatch.setattr(slides_api.redis_client, "get", fake_redis_get)
    monkeypatch.setattr(slides_api, "_enqueue_generation_task", fake_enqueue_task)

    # First stale detection should trigger auto-restart
    stale1 = slides_api._stale_missing_celery_task_if_needed(project, db, run)
    assert stale1 is True
    assert len(enqueue_calls) == 1

    # Second stale detection on the same run should NOT trigger again
    stale2 = slides_api._stale_missing_celery_task_if_needed(project, db, run)
    assert stale2 is True
    assert len(enqueue_calls) == 1


def test_stale_detection_no_auto_restart_when_zero_completed(monkeypatch):
    db = make_session()
    project = Project(
        title="No auto-restart on zero completed",
        status="prompt_ready",
        content_plan_confirmed=True,
        selected_style={"name": "Style"},
    )
    db.add(project)
    db.flush()
    db.add(
        Slide(project_id=project.id, page_num=1, status="generating", prompt_text="p1"),
    )
    db.flush()
    run = create_project_run(
        db,
        project.id,
        kind="batch_generation",
        stage="batch_generation",
        target_page_nums=[1],
        total_count=1,
    )
    run.status = "running"
    run.task_id = "dead-task"
    run.completed_count = 0  # Never completed anything
    run.updated_at = utc_now() - timedelta(seconds=600)
    db.flush()

    enqueue_calls = []

    class FakeTask:
        id = "auto-restart-task"

    def fake_enqueue_task(db, project_id, page_nums, *, run, prototype=False):
        enqueue_calls.append(run.id)
        return FakeTask()

    monkeypatch.setattr(slides_api, "_celery_result_state", lambda task_id: "STARTED")
    monkeypatch.setattr(slides_api, "_celery_task_present_in_worker", lambda task_id: False)
    monkeypatch.setattr(slides_api, "_task_age_seconds", lambda project_id, active_run: 600)
    monkeypatch.setattr(slides_api.redis_client, "delete", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(slides_api.redis_client, "set", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(slides_api.redis_client, "get", lambda key: None)
    monkeypatch.setattr(slides_api, "_enqueue_generation_task", fake_enqueue_task)

    stale = slides_api._stale_missing_celery_task_if_needed(project, db, run)

    assert stale is True
    assert len(enqueue_calls) == 0  # Should NOT auto-restart when zero pages completed
