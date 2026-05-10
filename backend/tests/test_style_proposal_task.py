from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import tasks
from app.models.base import Base
from app.models.models import Project, ReferenceImage, Slide
from app.services.run_state import create_project_run


def test_style_proposal_uses_cached_style_ref_when_file_is_missing(monkeypatch, tmp_path):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    project = Project(title="Cached style ref", status="visual_ready", content_plan_confirmed=True)
    db.add(project)
    db.flush()
    db.add(Slide(project_id=project.id, page_num=1, content_json={"title": "测试页"}))
    db.add(
        ReferenceImage(
            project_id=project.id,
            role="style_ref",
            file_path=str(tmp_path / "missing-style-ref.png"),
            asset_analysis={
                "analysis_status": "completed",
                "style_name": "霓虹科技风",
                "description": "深色背景、紫粉高光、科技感",
            },
        )
    )
    db.flush()
    run = create_project_run(db, project.id, kind="style_proposal", stage="style_proposal", total_count=1)
    project_id = project.id
    run_id = run.id
    db.commit()
    db.close()

    captured = {}

    def fake_generate_style_proposals(content_plan, assets=None):
        captured["assets"] = assets
        return [{"name": "复用缓存风格", "source": "asset_clone"}]

    monkeypatch.setattr(tasks, "SessionLocal", Session)
    monkeypatch.setattr(tasks, "generate_style_proposals", fake_generate_style_proposals)
    monkeypatch.setattr(tasks, "analyze_reference_image", lambda path: (_ for _ in ()).throw(AssertionError("should not read missing file")))

    result = tasks._generate_style_proposals_task_inner(SimpleNamespace(), project_id, run_id)

    verify = Session()
    refreshed_project = verify.query(Project).filter(Project.id == project_id).first()
    assert result["status"] == "completed"
    assert captured["assets"]["reference_analysis"]["style_name"] == "霓虹科技风"
    assert refreshed_project.style_proposal["proposals"][0]["name"] == "复用缓存风格"
    verify.close()
