from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import projects as projects_api
from app import tasks
from app.models.base import Base
from app.models.models import Project, ReferenceImage, Slide
from app.services.artifact_versions import content_signature, style_asset_signature
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


def test_style_proposal_task_passes_visual_chat_requirements(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    project = Project(title="Chat guided style", status="visual_ready", content_plan_confirmed=True)
    db.add(project)
    db.flush()
    db.add(Slide(project_id=project.id, page_num=1, content_json={"title": "测试页"}))
    db.flush()
    run = create_project_run(db, project.id, kind="style_proposal", stage="style_proposal", total_count=1)
    project_id = project.id
    run_id = run.id
    db.commit()
    db.close()

    captured = {}

    def fake_generate_style_proposals(content_plan, assets=None):
        captured["assets"] = assets
        return [{"name": "冷白极简", "source": "asset_based"}]

    monkeypatch.setattr(tasks, "SessionLocal", Session)
    monkeypatch.setattr(tasks, "generate_style_proposals", fake_generate_style_proposals)

    result = tasks._generate_style_proposals_task_inner(
        SimpleNamespace(),
        project_id,
        run_id,
        user_description="用户要求：不要原来的红色方案，改成冷白极简、更多留白。",
    )

    verify = Session()
    refreshed_project = verify.query(Project).filter(Project.id == project_id).first()
    assert result["status"] == "completed"
    assert "冷白极简" in captured["assets"]["user_description"]
    assert "红色方案" in refreshed_project.style_proposal["user_description"]
    verify.close()


def test_style_proposal_api_invalidates_cache_when_visual_chat_requirements_change(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    project = Project(title="Cached visual direction", status="visual_ready", content_plan_confirmed=True)
    db.add(project)
    db.flush()
    slide = Slide(project_id=project.id, page_num=1, content_json={"title": "测试页"})
    db.add(slide)
    db.flush()
    project.style_proposal = {
        "proposals": [{"name": "旧红色方案"}],
        "asset_signature": style_asset_signature(project),
        "content_signature": content_signature([slide]),
        "user_description": "用户：保持红色热烈风格",
    }
    project_id = project.id
    db.commit()

    captured = {}

    class _FakeTask:
        @staticmethod
        def delay(project_id_arg, run_id_arg, **kwargs):
            captured["project_id"] = project_id_arg
            captured["run_id"] = run_id_arg
            captured["kwargs"] = kwargs
            return SimpleNamespace(id="fake-task-id")

    monkeypatch.setattr(projects_api, "ensure_celery_worker", lambda: True)
    monkeypatch.setattr(projects_api, "store_current_provider_credentials", lambda redis_client: "credential-id")
    monkeypatch.setattr(projects_api, "generate_style_proposals_task", _FakeTask)

    result = projects_api.create_style_proposals(
        project_id,
        payload=projects_api.StyleProposalRequest(user_description="用户：不要红色，改成冷白极简、更多留白。"),
        db=db,
    )

    assert result["status"] == "generating"
    assert captured["project_id"] == project_id
    assert captured["kwargs"]["user_description"] == "用户：不要红色，改成冷白极简、更多留白。"
    refreshed_project = db.query(Project).filter(Project.id == project_id).first()
    assert refreshed_project.style_proposal is None
    db.close()
