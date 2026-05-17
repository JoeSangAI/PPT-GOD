import json
import os
import time

from app.utils import project_docs


def _write_source(tmp_path, monkeypatch, project_id: str, filename: str, content: str = "source") -> str:
    monkeypatch.setattr(project_docs.settings, "UPLOAD_DIR", str(tmp_path))
    docs_dir = project_docs.get_project_docs_dir(project_id, create=True)
    source_path = os.path.join(docs_dir, filename)
    with open(source_path, "w", encoding="utf-8") as f:
        f.write(content)
    return source_path


def test_load_project_documents_waits_for_running_parse_without_starting_duplicate(tmp_path, monkeypatch):
    project_id = "project-running-parse"
    filename = "demo.pptx"
    _write_source(tmp_path, monkeypatch, project_id, filename)
    project_docs.write_document_parse_status(
        project_id,
        filename,
        "running",
        current_page=2,
        total_pages=10,
        message="正在识别 PPT 第 2/10 页文字和截图...",
    )

    called = False

    def fake_extract(*args, **kwargs):
        nonlocal called
        called = True
        return {"text": "should not duplicate"}

    monkeypatch.setattr(project_docs, "extract_document_text", fake_extract)
    progress_events = []

    documents = project_docs.load_project_documents(
        project_id,
        parse_missing=True,
        running_wait_seconds=0.01,
        progress_callback=progress_events.append,
    )

    assert documents == ""
    assert called is False
    assert progress_events
    assert progress_events[0]["current_page"] == 2


def test_load_project_documents_restarts_stale_parse_status(tmp_path, monkeypatch):
    project_id = "project-stale-parse"
    filename = "demo.pptx"
    source_path = _write_source(tmp_path, monkeypatch, project_id, filename)
    status_path = project_docs.document_parse_status_path(project_id, filename)
    with open(status_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "status": "running",
                "updated_at": time.time() - project_docs.DOCUMENT_PARSE_STALE_SECONDS - 5,
                "char_count": 0,
                "text_preview": "",
            },
            f,
        )

    def fake_extract(project_id_arg, source_path_arg, filename_arg, progress_callback=None):
        assert project_id_arg == project_id
        assert source_path_arg == source_path
        assert filename_arg == filename
        return {"text": "recovered text"}

    monkeypatch.setattr(project_docs, "extract_document_text", fake_extract)

    documents = project_docs.load_project_documents(project_id, parse_missing=True)

    assert "recovered text" in documents


def test_load_project_documents_can_preserve_full_ppt_source_text(tmp_path, monkeypatch):
    project_id = "project-full-ppt-source"
    filename = "deck.pptx"
    _write_source(tmp_path, monkeypatch, project_id, filename)
    extracted = "\n".join([
        '--- PPT_SOURCE filename="deck.pptx" pages=3 ---',
        "--- 第1页 ---",
        "第一页",
        "--- 第2页 ---",
        "第二页",
        "--- 第3页 ---",
        "第三页",
    ])
    with open(project_docs.document_text_path(project_id, filename), "w", encoding="utf-8") as f:
        f.write(extracted)

    documents = project_docs.load_project_documents(
        project_id,
        text_limit=60,
        preserve_ppt_sources=True,
    )

    assert "--- 第3页 ---" in documents
    assert "[文档内容过长，已截断]" not in documents
