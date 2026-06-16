import io
import os
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import documents as documents_api
from app.models.base import Base
from app.models.models import Project


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def txt_upload(name: str, text: str = "brief"):
    return SimpleNamespace(filename=name, file=io.BytesIO(text.encode("utf-8")), content_type="text/plain")


def test_upload_document_normalizes_browser_path_filename(tmp_path, monkeypatch):
    db = make_session()
    project = Project(title="Safe document names", status="draft")
    db.add(project)
    db.commit()

    monkeypatch.setattr(documents_api.settings, "UPLOAD_DIR", str(tmp_path))

    result = documents_api.upload_document(
        project.id,
        txt_upload("C:\\Users\\Joe\\Desktop\\brief.txt", "预算有限"),
        db=db,
    )

    docs_dir = documents_api._get_docs_dir(project.id)
    assert result["filename"] == "brief.txt"
    assert os.path.exists(os.path.join(docs_dir, "brief.txt"))
    assert not os.path.exists(os.path.join(docs_dir, "C:\\Users\\Joe\\Desktop\\brief.txt"))
    assert documents_api.list_documents(project.id, db=db)[0]["filename"] == "brief.txt"


def test_upload_document_keeps_path_traversal_name_inside_docs_dir(tmp_path, monkeypatch):
    db = make_session()
    project = Project(title="Safe document names", status="draft")
    db.add(project)
    db.commit()

    monkeypatch.setattr(documents_api.settings, "UPLOAD_DIR", str(tmp_path))

    result = documents_api.upload_document(project.id, txt_upload("../brief.txt", "预算有限"), db=db)

    docs_dir = documents_api._get_docs_dir(project.id)
    assert result["filename"] == "brief.txt"
    assert os.path.exists(os.path.join(docs_dir, "brief.txt"))
    assert not os.path.exists(os.path.join(os.path.dirname(docs_dir), "brief.txt"))


def test_upload_document_rejects_metadata_suffix_that_would_hide_source(tmp_path, monkeypatch):
    db = make_session()
    project = Project(title="Safe document names", status="draft")
    db.add(project)
    db.commit()

    monkeypatch.setattr(documents_api.settings, "UPLOAD_DIR", str(tmp_path))

    try:
        documents_api.upload_document(project.id, txt_upload("brief.extracted.txt"), db=db)
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("expected metadata-like document filename to be rejected")
