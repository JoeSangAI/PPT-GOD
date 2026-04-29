import time

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.models.base import SessionLocal, engine
from app.models import models
from app.tasks import generate_slides_task

client = TestClient(app)


def setup_module():
    models.Base.metadata.create_all(bind=engine)


def _create_generating_project():
    db = SessionLocal()
    project = models.Project(title="stuck generation test", status="generating")
    db.add(project)
    db.commit()
    db.refresh(project)
    project_id = project.id
    db.close()
    return project_id


def teardown_function():
    settings.GENERATION_PENDING_TIMEOUT_SECONDS = 0
    db = SessionLocal()
    db.query(models.Project).filter(models.Project.title == "stuck generation test").delete()
    db.commit()
    db.close()


def test_generation_lock_conflict_restores_project_status(monkeypatch):
    project_id = _create_generating_project()

    monkeypatch.setattr("app.tasks.redis_client.set", lambda *args, **kwargs: False)

    result = generate_slides_task.run(project_id)

    db = SessionLocal()
    project = db.query(models.Project).filter(models.Project.id == project_id).first()
    db.close()

    assert result["status"] == "skipped"
    assert project.status == "prompt_ready"


def test_stale_pending_generation_status_returns_idle(monkeypatch):
    project_id = _create_generating_project()
    settings.GENERATION_PENDING_TIMEOUT_SECONDS = 1

    def fake_get(key):
        if key.endswith(":task_id"):
            return b"task-1"
        if key.endswith(":task_started_at"):
            return str(time.time() - 30).encode()
        return None

    class PendingTask:
        state = "PENDING"

    monkeypatch.setattr("app.api.slides.redis_client.get", fake_get)
    monkeypatch.setattr("app.api.slides.redis_client.delete", lambda *args, **kwargs: None)
    monkeypatch.setattr("celery.result.AsyncResult", lambda *args, **kwargs: PendingTask())

    resp = client.get(f"/projects/{project_id}/generation-status")

    assert resp.status_code == 200
    assert resp.json()["status"] == "idle"
    assert resp.json()["project_status"] == "prompt_ready"

