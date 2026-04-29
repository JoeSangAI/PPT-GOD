import json
import os
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.models.base import SessionLocal, engine
from app.models import models
from app.services.content_plan import generate_content_plan

client = TestClient(app)


class FakeChunk:
    def __init__(self, content: str):
        self.choices = [MagicMock(delta=MagicMock(content=content))]


class FakeLLMClient:
    def __init__(self, payload):
        self.payload = payload
        self.chat = MagicMock()
        self.chat.completions.create.return_value = iter(
            FakeChunk(ch) for ch in json.dumps(payload, ensure_ascii=False)
        )


def setup_module():
    models.Base.metadata.create_all(bind=engine)


def teardown_function():
    settings.OUTPUT_DIR = "./outputs"
    db = SessionLocal()
    db.query(models.Project).filter(models.Project.title.like("Smoke Regression%")).delete()
    db.commit()
    db.close()


def test_content_plan_caps_llm_extra_pages(monkeypatch):
    payload = [
        {"page_num": 1, "type": "cover", "text_content": {"headline": "A"}},
        {"page_num": 2, "type": "content", "text_content": {"headline": "B"}},
        {"page_num": 3, "type": "ending", "text_content": {"headline": "C"}},
    ]
    monkeypatch.setattr("app.services.content_plan.get_llm_client", lambda: FakeLLMClient(payload))

    progress = []
    outline = generate_content_plan(
        "one page smoke",
        page_count=1,
        on_progress=lambda data: progress.append(data),
    )

    assert len(outline) == 1
    assert outline[0]["page_num"] == 1
    assert all(
        item.get("current_page", 0) <= item.get("total_pages", 1)
        for item in progress
        if item.get("stage") == "generating"
    )


def test_status_reports_prototype_pptx_when_project_is_prototype_ready(tmp_path):
    settings.OUTPUT_DIR = str(tmp_path)
    db = SessionLocal()
    project = models.Project(title="Smoke Regression Prototype", status="prototype_ready")
    db.add(project)
    db.commit()
    db.refresh(project)
    slide = models.Slide(
        project_id=project.id,
        page_num=1,
        type="cover",
        status="completed",
        image_path="./outputs/test/slide_01.png",
        content_json={"page_num": 1, "text_content": {"headline": "A"}},
    )
    db.add(slide)
    project_dir = tmp_path / project.id
    project_dir.mkdir()
    (project_dir / "prototype.pptx").write_bytes(b"fake pptx")
    project_id = project.id
    db.commit()
    db.close()

    resp = client.get(f"/projects/{project_id}/status")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["has_pptx"] is True
    assert data["pptx_path"].endswith("prototype.pptx")

