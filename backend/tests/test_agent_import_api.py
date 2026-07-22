import asyncio
from io import BytesIO

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import agent as agent_api
from app.services import artifact_versions
from app.core.tester_auth import LOCAL_ADMIN_TESTER_ID, reset_current_request_is_local, set_current_request_is_local
from app.models.base import Base
from app.models.models import Project, Slide
from app.services.content_plan_markdown import validate_content_plan_markdown
from app.services.style_proposal import STYLE_PROPOSAL_POLICY_VERSION
from app.services.slide_artifacts import SlideArtifactError, import_slide_image_artifact
from app.core.config import settings


VALID_MARKDOWN = """# 外部 Agent 导入测试

## P1
### 类型
cover

### 标题
外部 Agent 导入测试

### 副标题
用严格 Markdown 交付内容规划

### 正文
这一页用于验证 Codex 直接提交内容规划后，PPT God 能进入内容确认阶段。

### 备注
导入后应打开 Web UI 让用户确认内容。
"""


def test_agent_capabilities_exposes_stable_contract_and_semantic_types():
    response = agent_api.get_agent_capabilities()

    assert response["ok"] is True
    assert response["contract_version"] == "1"
    assert response["slide_types"] == [
        "cover", "toc", "section", "content", "data", "hero", "quote", "ending"
    ]
    assert response["async_contract"]["start_returns_run"] is True
    assert "update_preview" in response["operations"]["content_plan"]


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_agent_import_content_plan_creates_project_for_local_admin():
    db = make_session()
    token = set_current_request_is_local(True)
    try:
        response = agent_api.import_content_plan(
            agent_api.ImportContentPlanRequest(markdown=VALID_MARKDOWN),
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )
    finally:
        reset_current_request_is_local(token)

    project = db.query(Project).filter(Project.id == response["project_id"]).first()
    slides = db.query(Slide).filter(Slide.project_id == response["project_id"]).all()
    assert response["ok"] is True
    assert response["slides_count"] == 1
    assert response["ui_url"].endswith(f"/app/projects/{project.id}?stage=content")
    assert project.title == "外部 Agent 导入测试"
    assert project.status == "planning"
    assert project.tester_id is None
    assert slides[0].content_json["text_content"]["headline"] == "外部 Agent 导入测试"


def test_agent_import_content_plan_rejects_invalid_markdown():
    db = make_session()
    token = set_current_request_is_local(True)
    try:
        with pytest.raises(HTTPException) as exc:
            agent_api.import_content_plan(
                agent_api.ImportContentPlanRequest(markdown="# Bad\n\n## P1\n### 类型\ncontent"),
                tester_id=LOCAL_ADMIN_TESTER_ID,
                db=db,
            )
    finally:
        reset_current_request_is_local(token)

    assert exc.value.status_code == 400
    assert "缺少字段" in str(exc.value.detail)
    assert db.query(Project).count() == 0


def _image_bytes(size=(1600, 900)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, "#2b2f36").save(output, format="PNG")
    return output.getvalue()


def test_agent_can_import_a_final_slide_image_without_image_provider(tmp_path, monkeypatch):
    db = make_session()
    project = Project(title="Agent image import", status="planning")
    db.add(project)
    db.flush()
    slide = Slide(project_id=project.id, page_num=1, status="pending", content_json={})
    db.add(slide)
    db.commit()
    monkeypatch.setattr(settings, "OUTPUT_DIR", str(tmp_path))

    receipt = import_slide_image_artifact(
        db,
        project,
        slide,
        _image_bytes(),
        source="codex_imagegen",
    )

    assert receipt["ok"] is True
    assert receipt["project_status"] == "completed"
    assert slide.status == "completed"
    assert slide.visual_json["artifact_source"] == "codex_imagegen"
    assert slide.image_path.endswith(".png")
    assert (tmp_path / project.id / "agent-artifacts").is_dir()


def test_agent_slide_image_import_rejects_non_widescreen_artifact(tmp_path, monkeypatch):
    db = make_session()
    project = Project(title="Bad ratio", status="planning")
    db.add(project)
    db.flush()
    slide = Slide(project_id=project.id, page_num=1, status="pending", content_json={})
    db.add(slide)
    db.commit()
    monkeypatch.setattr(settings, "OUTPUT_DIR", str(tmp_path))

    with pytest.raises(SlideArtifactError, match="16:9"):
        import_slide_image_artifact(db, project, slide, _image_bytes((1200, 900)))


def test_agent_page_image_requires_content_confirmation_and_survives_status_reconciliation(tmp_path, monkeypatch):
    db = make_session()
    project = Project(title="Agent page handoff", status="planning", content_plan_confirmed=False)
    db.add(project)
    db.flush()
    db.add_all([
        Slide(project_id=project.id, page_num=1, status="pending", content_json={"page_num": 1}),
        Slide(project_id=project.id, page_num=2, status="pending", content_json={"page_num": 2}),
    ])
    db.commit()
    monkeypatch.setattr(settings, "OUTPUT_DIR", str(tmp_path))
    token = set_current_request_is_local(True)
    try:
        with pytest.raises(HTTPException) as blocked:
            agent_api.import_agent_slide_image(
                project.id,
                1,
                file=type("Upload", (), {"file": BytesIO(_image_bytes())})(),
                tester_id=LOCAL_ADMIN_TESTER_ID,
                db=db,
            )
        assert blocked.value.status_code == 409
        assert blocked.value.detail["code"] == "content_confirmation_required"

        project.content_plan_confirmed = True
        project.status = "visual_ready"
        db.commit()
        receipt = agent_api.import_agent_slide_image(
            project.id,
            1,
            file=type("Upload", (), {"file": BytesIO(_image_bytes())})(),
            source="codex_imagegen",
            frontend_base_url="http://localhost:5173",
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )
        status = agent_api.get_agent_project_status(
            project.id,
            frontend_base_url="http://localhost:5173",
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )
    finally:
        reset_current_request_is_local(token)

    imported_slide = db.query(Slide).filter(Slide.project_id == project.id, Slide.page_num == 1).first()
    assert receipt["project_status"] == "prototype_ready"
    assert receipt["ui_url"].endswith(f"/app/projects/{project.id}?stage=review")
    assert imported_slide.image_path
    assert imported_slide.status == "completed"
    assert status["project"]["status"] == "prototype_ready"
    assert status["next_action"]["type"] == "import_or_generate_remaining_slides"
    assert status["next_action"]["missing_page_nums"] == [2]


def test_agent_can_import_visual_plan_and_prompts_without_text_provider():
    db = make_session()
    project = Project(
        title="Agent visual plan",
        status="visual_ready",
        content_plan_confirmed=True,
    )
    db.add(project)
    db.flush()
    db.add_all([
        Slide(project_id=project.id, page_num=1, status="pending", content_json={"page_num": 1}),
        Slide(project_id=project.id, page_num=2, status="pending", content_json={"page_num": 2}),
    ])
    db.commit()
    token = set_current_request_is_local(True)
    try:
        response = agent_api.import_agent_visual_plan(
            project.id,
            agent_api.ImportVisualPlanRequest(
                frontend_base_url="http://localhost:5173",
                pages=[
                    agent_api.ImportVisualPlanPage(
                        page_num=1,
                        visual_description="深蓝背景上的单一金色标题。",
                        prompt="Create a 16:9 title slide with a deep blue background.",
                    ),
                    agent_api.ImportVisualPlanPage(
                        page_num=2,
                        visual_description="左右对照的两栏信息图。",
                        prompt="Create a 16:9 two-column comparison slide.",
                    ),
                ],
            ),
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )
    finally:
        reset_current_request_is_local(token)

    refreshed = db.query(Project).filter(Project.id == project.id).first()
    slides = db.query(Slide).filter(Slide.project_id == project.id).order_by(Slide.page_num).all()
    assert response["ok"] is True
    assert response["project_status"] == "prompt_ready"
    assert response["next_action"]["type"] == "generate_slides"
    assert refreshed.selected_style["name"] == "Agent 提供的视觉方向"
    assert slides[0].status == "prompt_ready"
    assert slides[0].visual_json["visual_description"].startswith("深蓝背景")
    assert slides[0].visual_json["artifact_source"] == "external_agent"
    assert slides[1].prompt_text.startswith("Create a 16:9")


def test_agent_project_status_returns_handoff_state():
    db = make_session()
    token = set_current_request_is_local(True)
    try:
        imported = agent_api.import_content_plan(
            agent_api.ImportContentPlanRequest(markdown=VALID_MARKDOWN),
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )
        response = agent_api.get_agent_project_status(
            imported["project_id"],
            frontend_base_url="http://localhost:5173",
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )
    finally:
        reset_current_request_is_local(token)

    assert response["ok"] is True
    assert response["project"]["status"] == "planning"
    assert response["project"]["content_plan_confirmed"] is False
    assert response["slides_summary"]["total"] == 1
    assert response["slides_summary"]["by_status"] == {"pending": 1}
    assert response["slides"][0]["headline"] == "外部 Agent 导入测试"
    assert response["slides"][0]["body"].startswith("这一页用于验证")
    assert response["slides"][0]["body_storage_consistent"] is True
    assert response["slides"][0]["content_blocks_count"] == 1
    assert response["next_action"]["stage"] == "content"


def test_agent_export_content_plan_returns_strict_markdown():
    db = make_session()
    token = set_current_request_is_local(True)
    try:
        imported = agent_api.import_content_plan(
            agent_api.ImportContentPlanRequest(markdown=VALID_MARKDOWN),
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )
        response = agent_api.export_agent_content_plan(
            imported["project_id"],
            frontend_base_url="http://localhost:5173",
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )
    finally:
        reset_current_request_is_local(token)

    validation = validate_content_plan_markdown(response["markdown"])
    assert response["ok"] is True
    assert response["slides_count"] == 1
    assert response["filename"].endswith(".md")
    assert "### 类型\n\ncover" in response["markdown"]
    assert validation.ok is True


def test_agent_update_content_plan_previews_then_applies_without_advancing_stage():
    db = make_session()
    updated_markdown = VALID_MARKDOWN.replace(
        "这一页用于验证 Codex 直接提交内容规划后，PPT God 能进入内容确认阶段。",
        "这一页已经由外部 Agent 更新，但项目阶段与确认状态必须保持不变。",
    )
    token = set_current_request_is_local(True)
    try:
        imported = agent_api.import_content_plan(
            agent_api.ImportContentPlanRequest(markdown=VALID_MARKDOWN),
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )
        project = db.query(Project).filter(Project.id == imported["project_id"]).first()
        project.status = "completed"
        project.content_plan_confirmed = True
        db.commit()

        preview = agent_api.update_agent_content_plan(
            imported["project_id"],
            agent_api.UpdateContentPlanRequest(markdown=updated_markdown),
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )
        applied = agent_api.update_agent_content_plan(
            imported["project_id"],
            agent_api.UpdateContentPlanRequest(
                markdown=updated_markdown,
                apply=True,
                expected_preview_token=preview["preview_token"],
            ),
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )
    finally:
        reset_current_request_is_local(token)

    slide = db.query(Slide).filter(Slide.project_id == imported["project_id"]).first()
    db.refresh(project)
    assert preview["applied"] is False
    assert preview["summary"]["changed"] == 1
    assert applied["applied"] is True
    assert applied["readback"]["ok"] is True
    assert applied["readback"]["slides"][0]["body_storage_consistent"] is True
    assert applied["project_state_unchanged"] is True
    assert project.status == "completed"
    assert project.content_plan_confirmed is True
    assert "项目阶段与确认状态必须保持不变" in slide.content_json["text_content"]["body"]


def test_agent_confirm_content_plan_advances_project_to_visual_ready():
    db = make_session()
    token = set_current_request_is_local(True)
    try:
        imported = agent_api.import_content_plan(
            agent_api.ImportContentPlanRequest(markdown=VALID_MARKDOWN),
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )
        response = agent_api.confirm_agent_content_plan(
            imported["project_id"],
            frontend_base_url="http://localhost:5173",
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )
    finally:
        reset_current_request_is_local(token)

    project = db.query(Project).filter(Project.id == imported["project_id"]).first()
    assert response["ok"] is True
    assert project.content_plan_confirmed is True
    assert project.status == "visual_ready"
    assert response["next_action"]["stage"] == "visual"


def test_agent_start_visual_proposals_reuses_existing_project_contract(monkeypatch):
    db = make_session()
    token = set_current_request_is_local(True)
    try:
        imported = agent_api.import_content_plan(
            agent_api.ImportContentPlanRequest(markdown=VALID_MARKDOWN),
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )
        agent_api.confirm_agent_content_plan(
            imported["project_id"],
            frontend_base_url="http://localhost:5173",
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )

        captured = {}

        def fake_create_style_proposals(project_id, payload=None, force=False, tester_id=None, db=None):
            captured.update(
                {
                    "project_id": project_id,
                    "payload": payload,
                    "force": force,
                    "tester_id": tester_id,
                    "db": db,
                }
            )
            return {"status": "generating", "proposals": None, "run": {"id": "run-1"}}

        monkeypatch.setattr(agent_api.project_api, "create_style_proposals", fake_create_style_proposals)
        response = agent_api.start_agent_visual_proposals(
            imported["project_id"],
            agent_api.StartVisualProposalsRequest(force=True, user_description="更像科技品牌"),
            frontend_base_url="http://localhost:5173",
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )
    finally:
        reset_current_request_is_local(token)

    assert response["ok"] is True
    assert response["status"] == "generating"
    assert captured["project_id"] == imported["project_id"]
    assert captured["force"] is True
    assert captured["payload"].user_description == "更像科技品牌"


def test_agent_get_visual_proposals_returns_cached_options():
    db = make_session()
    token = set_current_request_is_local(True)
    try:
        imported = agent_api.import_content_plan(
            agent_api.ImportContentPlanRequest(markdown=VALID_MARKDOWN),
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )
        project = db.query(Project).filter(Project.id == imported["project_id"]).first()
        project.content_plan_confirmed = True
        project.status = "visual_ready"
        project.style_proposal = {
            "policy_version": STYLE_PROPOSAL_POLICY_VERSION,
            "proposals": [{"name": "冷静科技蓝"}, {"name": "高对比商业风"}],
        }
        db.commit()

        response = agent_api.get_agent_visual_proposals(
            imported["project_id"],
            frontend_base_url="http://localhost:5173",
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )
    finally:
        reset_current_request_is_local(token)

    assert response["ok"] is True
    assert response["status"] == "completed"
    assert response["proposals_count"] == 2
    assert response["proposals"][0]["name"] == "冷静科技蓝"


def test_agent_confirm_visual_proposal_selects_by_one_based_index():
    db = make_session()
    token = set_current_request_is_local(True)
    try:
        imported = agent_api.import_content_plan(
            agent_api.ImportContentPlanRequest(markdown=VALID_MARKDOWN),
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )
        project = db.query(Project).filter(Project.id == imported["project_id"]).first()
        slide = db.query(Slide).filter(Slide.project_id == project.id).first()
        project.content_plan_confirmed = True
        project.status = "visual_ready"
        project.style_proposal = {
            "policy_version": STYLE_PROPOSAL_POLICY_VERSION,
            "proposals": [{"name": "冷静科技蓝"}, {"name": "高对比商业风", "palette": ["#111111"]}],
        }
        slide.status = "completed"
        slide.prompt_text = "old prompt"
        slide.image_path = "/tmp/old.png"
        db.commit()

        response = agent_api.confirm_agent_visual_proposal(
            imported["project_id"],
            agent_api.ConfirmVisualProposalRequest(proposal_index=2),
            frontend_base_url="http://localhost:5173",
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )
    finally:
        reset_current_request_is_local(token)

    db.refresh(project)
    db.refresh(slide)
    assert response["ok"] is True
    assert artifact_versions.strip_artifact_meta(project.selected_style)["name"] == "高对比商业风"
    assert project.status == "visual_ready"
    assert slide.status == "pending"
    assert slide.prompt_text is None
    assert slide.image_path is None
    assert response["next_action"]["type"] == "generate_visual_prompts"


def test_agent_start_visual_prompts_reuses_existing_slide_contract(monkeypatch):
    db = make_session()
    token = set_current_request_is_local(True)
    try:
        imported = agent_api.import_content_plan(
            agent_api.ImportContentPlanRequest(markdown=VALID_MARKDOWN),
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )

        captured = {}

        async def fake_create_visual_and_prompts(project_id, body, db):
            captured["project_id"] = project_id
            captured["body"] = body
            captured["db"] = db
            return {"status": "started", "run": {"id": "run-visual"}}

        monkeypatch.setattr(agent_api.slides_api, "create_visual_and_prompts", fake_create_visual_and_prompts)
        response = asyncio.run(
            agent_api.start_agent_visual_prompts(
                imported["project_id"],
                agent_api.StartVisualPromptsRequest(page_nums=[1], stage_context="更强调数据感"),
                frontend_base_url="http://localhost:5173",
                tester_id=LOCAL_ADMIN_TESTER_ID,
                db=db,
            )
        )
    finally:
        reset_current_request_is_local(token)

    assert response["ok"] is True
    assert response["status"] == "started"
    assert captured["project_id"] == imported["project_id"]
    assert captured["body"].page_nums == [1]
    assert captured["body"].stage_context == "更强调数据感"


def test_agent_start_slide_generation_reuses_existing_generation_contract(monkeypatch):
    db = make_session()
    token = set_current_request_is_local(True)
    try:
        imported = agent_api.import_content_plan(
            agent_api.ImportContentPlanRequest(markdown=VALID_MARKDOWN),
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )

        captured = {}

        def fake_start_generation(project_id, body, db):
            captured["project_id"] = project_id
            captured["body"] = body
            captured["db"] = db
            return {"message": "Generation started", "run": {"id": "run-generate"}, "page_nums": [1], "prototype": True}

        monkeypatch.setattr(agent_api.slides_api, "start_generation", fake_start_generation)
        response = agent_api.start_agent_slide_generation(
            imported["project_id"],
            agent_api.StartSlideGenerationRequest(page_nums=[1], prototype=True),
            frontend_base_url="http://localhost:5173",
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )
    finally:
        reset_current_request_is_local(token)

    assert response["ok"] is True
    assert response["message"] == "Generation started"
    assert captured["project_id"] == imported["project_id"]
    assert captured["body"].page_nums == [1]
    assert captured["body"].prototype is True


def test_agent_generation_status_wraps_workflow_status(monkeypatch):
    db = make_session()
    token = set_current_request_is_local(True)
    try:
        imported = agent_api.import_content_plan(
            agent_api.ImportContentPlanRequest(markdown=VALID_MARKDOWN),
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )

        def fake_workflow_status(project_id, db):
            return {
                "project_id": project_id,
                "stage": "prompt_ready",
                "project_status": "prompt_ready",
                "has_pptx": False,
                "active_run": None,
            }

        monkeypatch.setattr(agent_api.slides_api, "get_project_workflow_status", fake_workflow_status)
        response = agent_api.get_agent_generation_status(
            imported["project_id"],
            frontend_base_url="http://localhost:5173",
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )
    finally:
        reset_current_request_is_local(token)

    assert response["ok"] is True
    assert response["workflow_status"]["project_status"] == "prompt_ready"
    assert response["next_action"]["type"] == "generate_slides"


def test_agent_retry_failed_slides_reuses_existing_retry_contract(monkeypatch):
    db = make_session()
    token = set_current_request_is_local(True)
    try:
        imported = agent_api.import_content_plan(
            agent_api.ImportContentPlanRequest(markdown=VALID_MARKDOWN),
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )

        captured = {}

        def fake_retry_failed(project_id, db):
            captured["project_id"] = project_id
            captured["db"] = db
            return {"message": "Retry started", "page_nums": [1], "run": {"id": "run-retry"}}

        monkeypatch.setattr(agent_api.slides_api, "retry_failed_slides", fake_retry_failed)
        response = agent_api.retry_agent_failed_slides(
            imported["project_id"],
            agent_api.AgentActionRequest(frontend_base_url="http://localhost:5173"),
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )
    finally:
        reset_current_request_is_local(token)

    assert response["ok"] is True
    assert response["message"] == "Retry started"
    assert captured["project_id"] == imported["project_id"]


def test_agent_confirm_prototype_reuses_existing_contract(monkeypatch):
    db = make_session()
    token = set_current_request_is_local(True)
    try:
        imported = agent_api.import_content_plan(
            agent_api.ImportContentPlanRequest(markdown=VALID_MARKDOWN),
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )
        monkeypatch.setattr(
            agent_api.slides_api,
            "confirm_prototype",
            lambda project_id, db: {"message": "Full generation started", "run": {"id": "run-full"}},
        )

        response = agent_api.confirm_agent_prototype(
            imported["project_id"],
            agent_api.AgentActionRequest(frontend_base_url="http://localhost:5173"),
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )
    finally:
        reset_current_request_is_local(token)

    assert response["ok"] is True
    assert response["run"]["id"] == "run-full"


def test_agent_stop_generation_reuses_existing_contract(monkeypatch):
    db = make_session()
    token = set_current_request_is_local(True)
    try:
        imported = agent_api.import_content_plan(
            agent_api.ImportContentPlanRequest(markdown=VALID_MARKDOWN),
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )
        monkeypatch.setattr(
            agent_api.slides_api,
            "stop_generation",
            lambda project_id, db: {"message": "Generation stopped", "status": "prompt_ready"},
        )

        response = agent_api.stop_agent_generation(
            imported["project_id"],
            agent_api.AgentActionRequest(frontend_base_url="http://localhost:5173"),
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )
    finally:
        reset_current_request_is_local(token)

    assert response["ok"] is True
    assert response["message"] == "Generation stopped"


def test_agent_export_ppt_returns_download_contract(monkeypatch):
    db = make_session()
    token = set_current_request_is_local(True)
    try:
        imported = agent_api.import_content_plan(
            agent_api.ImportContentPlanRequest(markdown=VALID_MARKDOWN),
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )

        def fake_workflow_status(project_id, db):
            return {
                "project_id": project_id,
                "project_status": "completed",
                "has_pptx": True,
                "pptx_path": "/tmp/presentation.pptx",
            }

        monkeypatch.setattr(agent_api.slides_api, "get_project_workflow_status", fake_workflow_status)
        response = agent_api.export_agent_ppt(
            imported["project_id"],
            prototype=False,
            api_base_url="http://127.0.0.1:8000",
            frontend_base_url="http://localhost:5173",
            tester_id=LOCAL_ADMIN_TESTER_ID,
            db=db,
        )
    finally:
        reset_current_request_is_local(token)

    assert response["ok"] is True
    assert response["has_pptx"] is True
    assert response["download_url"].startswith("http://127.0.0.1:8000/projects/")
    assert "tester_id=local-admin" in response["download_url"]
