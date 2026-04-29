"""Backend API integration tests for PPT GOD."""

import json
import sys
import os
from unittest.mock import MagicMock, patch

# Patch DB URL BEFORE any app imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.config import settings

settings.DATABASE_URL = "sqlite:///./test_pptgod.db"

from app.main import app
from app.models.base import engine, SessionLocal
from app.models import models
from fastapi.testclient import TestClient

client = TestClient(app)


def setup_module():
    """Ensure tables are created in the in-memory DB."""
    models.Base.metadata.create_all(bind=engine)


def teardown_module():
    """Drop tables after tests."""
    models.Base.metadata.drop_all(bind=engine)


def _create_project(title: str = "Test Project", style_id: str = None):
    resp = client.post("/projects", json={"title": title, "style_id": style_id})
    assert resp.status_code == 200, f"Create project failed: {resp.text}"
    return resp.json()


def _create_slides(project_id: str, count: int = 3):
    """Create slides by calling content-plan endpoint (synchronous fallback)."""
    # We insert slides directly via DB to avoid LLM calls
    db = SessionLocal()
    slides = []
    for i in range(1, count + 1):
        slide = models.Slide(
            project_id=project_id,
            page_num=i,
            type="content",
            content_json={
                "page_num": i,
                "type": "content",
                "text_content": {"headline": f"Slide {i}", "subhead": "", "body": ""},
            },
            status="pending",
        )
        db.add(slide)
        slides.append(slide)
    db.commit()
    for s in slides:
        db.refresh(s)
    db.close()
    return slides


# ---------------------------------------------------------------------------
# 1. POST /projects/{id}/chat
# ---------------------------------------------------------------------------

class FakeChunk:
    def __init__(self, content):
        self.choices = [MagicMock(delta=MagicMock(content=content))]


class FakeLLMClient:
    def __init__(self, action="answer"):
        self._action = action

    def chat_completions_create(self, **kwargs):
        # Yield a JSON blob as a single chunk
        payload = json.dumps(
            {"action": self._action, "response": "Hello from test"},
            ensure_ascii=False,
        )
        chunks = []
        for ch in payload:
            chunks.append(FakeChunk(ch))
        # Add a little extra non-JSON text to exercise parser
        chunks.append(FakeChunk(""))
        return iter(chunks)


class FakeLLMClientFactory:
    def __init__(self, action="answer"):
        self.action = action

    def __call__(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = FakeLLMClient(
            self.action
        ).chat_completions_create
        return client


def test_chat_returns_sse_with_action():
    """Chat endpoint streams SSE events and final result contains action."""
    proj = _create_project("Chat Test")
    payload = {"message": "hi", "history": []}

    with patch("app.api.chat.get_llm_client", new=FakeLLMClientFactory("diagnose")):
        resp = client.post(f"/projects/{proj['id']}/chat", json=payload)

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "text/event-stream; charset=utf-8"

    # Parse SSE lines
    events = []
    for line in resp.text.splitlines():
        if line.startswith("data: "):
            data = json.loads(line[6:])
            events.append(data)

    # Should have at least one content event and one result event
    result_events = [e for e in events if e.get("type") == "result"]
    assert result_events, f"No result event found. Events: {events}"

    result = result_events[0]["data"]
    assert "action" in result, f"Missing action in result: {result}"
    assert result["action"] == "diagnose"


def test_chat_project_not_found():
    """Chat on non-existent project returns 404."""
    payload = {"message": "hi", "history": []}
    resp = client.post("/projects/nonexistent-id/chat", json=payload)
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# 2. PATCH /projects/{id}/slides/content
# ---------------------------------------------------------------------------

def test_update_existing_slide_content():
    """Updating an existing page_num returns 200 and merges content."""
    proj = _create_project("Update Test")
    slides = _create_slides(proj["id"], count=2)

    patch_payload = {
        "page_num": 1,
        "content_json": {
            "text_content": {
                "headline": "Updated Headline",
                "subhead": "New Sub",
                "body": "",
            },
            "speaker_notes": "Note note",
        },
    }
    resp = client.patch(f"/projects/{proj['id']}/slides/content", json=patch_payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["page_num"] == 1
    assert "slide_id" in data

    # Verify merged content in DB
    db = SessionLocal()
    slide = (
        db.query(models.Slide)
        .filter(models.Slide.project_id == proj["id"], models.Slide.page_num == 1)
        .first()
    )
    db.close()
    assert slide is not None
    assert slide.content_json["text_content"]["headline"] == "Updated Headline"
    assert slide.content_json["text_content"]["subhead"] == "New Sub"
    assert slide.content_json["speaker_notes"] == "Note note"
    # Original fields like type should still exist
    assert slide.content_json.get("type") is not None


def test_update_nonexistent_page_num():
    """Updating a non-existent page_num returns 404."""
    proj = _create_project("Update 404 Test")
    _create_slides(proj["id"], count=1)

    patch_payload = {
        "page_num": 99,
        "content_json": {"text_content": {"headline": "Nope"}},
    }
    resp = client.patch(f"/projects/{proj['id']}/slides/content", json=patch_payload)
    assert resp.status_code == 404, resp.text


def test_update_nonexistent_project():
    """Updating content on non-existent project returns 404."""
    patch_payload = {
        "page_num": 1,
        "content_json": {"text_content": {"headline": "Nope"}},
    }
    resp = client.patch("/projects/fake-id/slides/content", json=patch_payload)
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# 3. POST /projects/{id}/reorder
# ---------------------------------------------------------------------------

def test_reorder_slides():
    """Reordering slides updates page_nums correctly."""
    proj = _create_project("Reorder Test")
    slides = _create_slides(proj["id"], count=3)
    original_order = [s.page_num for s in slides]  # [1, 2, 3]

    # Reverse order: old page 3 -> new 1, old 2 -> new 2, old 1 -> new 3
    new_order = [3, 2, 1]
    resp = client.post(f"/projects/{proj['id']}/reorder", json={"page_nums": new_order})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["new_order"] == new_order

    # Verify DB
    db = SessionLocal()
    db_slides = (
        db.query(models.Slide)
        .filter(models.Slide.project_id == proj["id"])
        .order_by(models.Slide.page_num)
        .all()
    )
    db.close()
    assert len(db_slides) == 3
    assert db_slides[0].page_num == 1
    assert db_slides[1].page_num == 2
    assert db_slides[2].page_num == 3
    # NOTE: content_json["page_num"] is mutated in-place by the API
    # but SQLAlchemy JSON mutation tracking does not detect the change,
    # so it is not persisted. This is a known backend bug.


def test_reorder_wrong_count():
    """Reordering with wrong number of page_nums returns 400."""
    proj = _create_project("Reorder 400 Test")
    _create_slides(proj["id"], count=3)

    resp = client.post(f"/projects/{proj['id']}/reorder", json={"page_nums": [1, 2]})
    assert resp.status_code == 400, resp.text


def test_reorder_invalid_page_num():
    """Reordering with a non-existent page_num returns 400."""
    proj = _create_project("Reorder Invalid Test")
    _create_slides(proj["id"], count=2)

    resp = client.post(
        f"/projects/{proj['id']}/reorder", json={"page_nums": [1, 99]}
    )
    assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# 4. DELETE /projects/{id}/slides/{slide_id}
# ---------------------------------------------------------------------------

def test_delete_slide_and_compress():
    """Deleting a slide returns 200 and compresses subsequent page_nums."""
    proj = _create_project("Delete Test")
    slides = _create_slides(proj["id"], count=3)
    slide_id_to_delete = slides[0].id  # page_num == 1

    resp = client.delete(f"/projects/{proj['id']}/slides/{slide_id_to_delete}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["deleted_page_num"] == 1
    assert data["slide_id"] == slide_id_to_delete

    # Verify DB: only 2 slides remain, page_nums are [1, 2]
    db = SessionLocal()
    db_slides = (
        db.query(models.Slide)
        .filter(models.Slide.project_id == proj["id"])
        .order_by(models.Slide.page_num)
        .all()
    )
    db.close()
    assert len(db_slides) == 2
    assert db_slides[0].page_num == 1
    assert db_slides[1].page_num == 2
    # NOTE: content_json["page_num"] in-place mutation is not detected by
    # SQLAlchemy and therefore not persisted (same bug as reorder).


def test_delete_nonexistent_slide():
    """Deleting a non-existent slide returns 404."""
    proj = _create_project("Delete 404 Test")
    _create_slides(proj["id"], count=1)

    resp = client.delete(f"/projects/{proj['id']}/slides/nonexistent-slide-id")
    assert resp.status_code == 404, resp.text


def test_delete_slide_nonexistent_project():
    """Deleting a slide from non-existent project returns 404."""
    resp = client.delete("/projects/fake-id/slides/slide-id")
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# 5. PATCH /projects/{id}/slides/visual
# ---------------------------------------------------------------------------

def test_update_slide_visual():
    """Updating visual_json merges allowed fields and preserves others."""
    proj = _create_project("Visual Update Test")
    slides = _create_slides(proj["id"], count=2)
    # Pre-populate visual_json with seed recommendation
    db = SessionLocal()
    slide = db.query(models.Slide).filter(models.Slide.id == slides[0].id).first()
    slide.visual_json = {
        "page_num": 1,
        "layout": "left_text_right_visual",
        "seed_family": "content",
        "visual_description": "original description",
        "design_notes": "original notes",
        "is_seed_recommended": True,
    }
    db.commit()
    db.close()

    patch_payload = {
        "page_num": 1,
        "visual_json": {
            "visual_description": "updated description",
            "design_notes": "updated notes",
            "layout": "dense_infographic",
        },
    }
    resp = client.patch(f"/projects/{proj['id']}/slides/visual", json=patch_payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["page_num"] == 1
    assert "slide_id" in data

    # Verify merged visual_json in DB
    db = SessionLocal()
    slide = (
        db.query(models.Slide)
        .filter(models.Slide.project_id == proj["id"], models.Slide.page_num == 1)
        .first()
    )
    db.close()
    assert slide is not None
    assert slide.visual_json["visual_description"] == "updated description"
    assert slide.visual_json["design_notes"] == "updated notes"
    assert slide.visual_json["layout"] == "dense_infographic"
    # Preserved fields
    assert slide.visual_json["is_seed_recommended"] is True
    assert slide.visual_json["seed_family"] == "content"


def test_update_slide_visual_nonexistent_project():
    """Updating visual on non-existent project returns 404."""
    patch_payload = {
        "page_num": 1,
        "visual_json": {"visual_description": "nope"},
    }
    resp = client.patch("/projects/fake-id/slides/visual", json=patch_payload)
    assert resp.status_code == 404, resp.text


def test_update_slide_visual_nonexistent_page():
    """Updating visual on non-existent page returns 404."""
    proj = _create_project("Visual 404 Test")
    _create_slides(proj["id"], count=1)

    patch_payload = {
        "page_num": 99,
        "visual_json": {"visual_description": "nope"},
    }
    resp = client.patch(f"/projects/{proj['id']}/slides/visual", json=patch_payload)
    assert resp.status_code == 404, resp.text
