from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import projects as projects_api
from app import tasks
from app.models.base import Base
from app.models.models import Project, ReferenceImage, Slide
from app.services.artifact_versions import content_signature, style_asset_signature
from app.services.run_state import create_project_run


def test_stale_style_proposal_is_hidden_until_regenerated():
    project = Project(title="Old visual proposal", status="visual_ready", content_plan_confirmed=True)
    project.style_proposal = {"proposals": [{"name": "金墨典藏"}]}

    changed = projects_api._clear_stale_style_proposal(project)

    assert changed is True
    assert project.style_proposal is None


def test_stale_style_proposal_is_kept_after_user_selects_style():
    project = Project(title="Selected visual proposal", status="visual_ready", content_plan_confirmed=True)
    project.style_proposal = {"proposals": [{"name": "旧方案"}]}
    project.selected_style = {"name": "用户已选方案"}

    changed = projects_api._clear_stale_style_proposal(project)

    assert changed is False
    assert project.style_proposal["proposals"][0]["name"] == "旧方案"


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
    assert refreshed_project.style_proposal["policy_version"] == tasks.STYLE_PROPOSAL_POLICY_VERSION
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
    assert refreshed_project.style_proposal["policy_version"] == tasks.STYLE_PROPOSAL_POLICY_VERSION
    verify.close()


def test_style_proposal_reuses_cached_logo_analysis_when_file_is_missing(monkeypatch, tmp_path):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    project = Project(title="Cached logo", status="visual_ready", content_plan_confirmed=True)
    db.add(project)
    db.flush()
    db.add(Slide(project_id=project.id, page_num=1, content_json={"title": "测试页"}))
    db.add(
        ReferenceImage(
            project_id=project.id,
            role="logo",
            file_path=str(tmp_path / "missing-logo.png"),
            asset_analysis={
                "analysis_status": "completed",
                "primary_color": "#0057FF",
                "description": "蓝色科技感字标",
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
        return [{"name": "复用 Logo 风格", "source": "asset_based"}]

    monkeypatch.setattr(tasks, "SessionLocal", Session)
    monkeypatch.setattr(tasks, "generate_style_proposals", fake_generate_style_proposals)
    monkeypatch.setattr(tasks, "analyze_logo", lambda path: (_ for _ in ()).throw(AssertionError("should reuse cached logo")))

    result = tasks._generate_style_proposals_task_inner(SimpleNamespace(), project_id, run_id)

    assert result["status"] == "completed"
    assert captured["assets"]["logo_analysis"]["primary_color"] == "#0057FF"


def test_style_proposal_reanalyzes_tone_only_logo_cache(monkeypatch, tmp_path):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    logo_path = tmp_path / "logo.png"
    logo_path.write_bytes(b"fake image bytes")

    project = Project(title="Tone-only logo", status="visual_ready", content_plan_confirmed=True)
    db.add(project)
    db.flush()
    db.add(Slide(project_id=project.id, page_num=1, content_json={"title": "测试页"}))
    db.add(
        ReferenceImage(
            project_id=project.id,
            role="logo",
            file_path=str(logo_path),
            asset_analysis={
                "analysis_status": "completed",
                "logo_tone": "mixed",
                "logo_dark_pixel_share": 0.3,
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
        return [{"name": "Logo 品牌色", "source": "asset_based"}]

    monkeypatch.setattr(tasks, "SessionLocal", Session)
    monkeypatch.setattr(tasks, "generate_style_proposals", fake_generate_style_proposals)
    monkeypatch.setattr(tasks, "prepare_logo_lockup_image", lambda paths: str(logo_path))
    monkeypatch.setattr(
        tasks,
        "analyze_logo",
        lambda path: {"primary_color": "#FFD000", "secondary_colors": ["#101010"], "description": "黄黑字标"},
    )

    result = tasks._generate_style_proposals_task_inner(SimpleNamespace(), project_id, run_id)

    assert result["status"] == "completed"
    assert captured["assets"]["logo_analysis"]["primary_color"] == "#FFD000"


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

    captured_worker = {}

    def fake_ensure_worker(*, queue=None):
        captured_worker["queue"] = queue
        return True

    monkeypatch.setattr(projects_api, "ensure_celery_worker", fake_ensure_worker)
    monkeypatch.setattr(projects_api, "store_current_provider_credentials", lambda redis_client: "credential-id")
    monkeypatch.setattr(projects_api, "generate_style_proposals_task", _FakeTask)

    result = projects_api.create_style_proposals(
        project_id,
        payload=projects_api.StyleProposalRequest(user_description="用户：不要红色，改成冷白极简、更多留白。"),
        db=db,
    )

    assert result["status"] == "generating"
    assert captured_worker["queue"] == projects_api.settings.CELERY_TEXT_QUEUE
    assert captured["project_id"] == project_id
    assert captured["kwargs"]["user_description"] == "用户：不要红色，改成冷白极简、更多留白。"
    refreshed_project = db.query(Project).filter(Project.id == project_id).first()
    assert refreshed_project.style_proposal is None
    db.close()


def test_style_proposal_api_invalidates_cache_when_policy_version_changes(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    project = Project(title="Cached old policy", status="visual_ready", content_plan_confirmed=True)
    db.add(project)
    db.flush()
    slide = Slide(project_id=project.id, page_num=1, content_json={"title": "AI营销"})
    db.add(slide)
    db.flush()
    project.style_proposal = {
        "proposals": [{"name": "旧策略生成的方案"}],
        "asset_signature": style_asset_signature(project),
        "content_signature": content_signature([slide]),
        "user_description": "",
    }
    project_id = project.id
    db.commit()

    class _FakeTask:
        @staticmethod
        def delay(project_id_arg, run_id_arg, **kwargs):
            return SimpleNamespace(id="fake-task-id")

    monkeypatch.setattr(projects_api, "ensure_celery_worker", lambda *, queue=None: True)
    monkeypatch.setattr(projects_api, "store_current_provider_credentials", lambda redis_client: "credential-id")
    monkeypatch.setattr(projects_api, "generate_style_proposals_task", _FakeTask)

    result = projects_api.create_style_proposals(project_id, db=db)

    assert result["status"] == "generating"
    refreshed_project = db.query(Project).filter(Project.id == project_id).first()
    assert refreshed_project.style_proposal is None
    db.close()
