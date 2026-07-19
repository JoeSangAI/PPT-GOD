from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import slides as slides_api
from app.models.base import Base
from app.models.models import Project, ReferenceImage, Slide
from app.services.content_plan_markdown import content_body_storage_state


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_update_content_renders_visual_block_as_page_material(tmp_path, monkeypatch):
    monkeypatch.setattr(slides_api.settings, "UPLOAD_DIR", str(tmp_path))
    db = make_session()
    project = Project(title="content blocks", status="planning")
    db.add(project)
    db.flush()
    slide = Slide(
        project_id=project.id,
        page_num=1,
        type="content",
        content_json={"page_num": 1, "type": "content", "text_content": {"headline": "增长飞轮", "body": ""}},
        visual_json={},
    )
    db.add(slide)
    db.commit()

    payload = slides_api.UpdateContentRequest(
        page_num=1,
        slide_id=slide.id,
        content_json={
            "text_content": {"headline": "增长飞轮", "subhead": "", "body": "普通正文"},
            "content_blocks": [
                {"id": "body", "kind": "markdown", "markdown": "普通正文"},
                {
                    "id": "wheel",
                    "kind": "flywheel",
                    "title": "增长飞轮",
                    "route_mode": "crop",
                    "source_spec": {"center": "增长", "nodes": ["获客", "激活", "留存", "推荐"]},
                },
            ],
        },
    )

    result = slides_api.update_slide_content(project.id, payload, db)

    db.refresh(slide)
    ref = db.query(ReferenceImage).filter(ReferenceImage.slide_id == slide.id, ReferenceImage.role == "chart_ref").one()
    blocks = slide.content_json["content_blocks"]
    wheel = next(block for block in blocks if block["id"] == "wheel")
    assert result["slide_id"] == slide.id
    assert wheel["rendered_asset_id"] == ref.id
    assert ref.process_mode == "crop"
    assert ref.asset_analysis["source"] == "content_block"
    assert ref.asset_analysis["content_block_kind"] == "flywheel"
    assert ref.file_path.endswith(".png")
    assert "画面素材：增长飞轮" in slide.content_json["text_content"]["body"]


def test_exact_visual_block_creates_overlay_layer(tmp_path, monkeypatch):
    monkeypatch.setattr(slides_api.settings, "UPLOAD_DIR", str(tmp_path))
    db = make_session()
    project = Project(title="overlay block", status="planning")
    db.add(project)
    db.flush()
    slide = Slide(project_id=project.id, page_num=1, type="content", content_json={"text_content": {"body": ""}}, visual_json={})
    db.add(slide)
    db.commit()

    payload = slides_api.UpdateContentRequest(
        page_num=1,
        slide_id=slide.id,
        content_json={
            "text_content": {"headline": "流程", "body": ""},
            "content_blocks": [
                {
                    "id": "flow",
                    "kind": "flow",
                    "title": "交付流程",
                    "route_mode": "original",
                    "source_spec": {"steps": ["输入", "处理", "输出"]},
                }
            ],
        },
    )

    slides_api.update_slide_content(project.id, payload, db)

    db.refresh(slide)
    ref = db.query(ReferenceImage).filter(ReferenceImage.slide_id == slide.id, ReferenceImage.role == "chart_ref").one()
    layers = slide.visual_json.get("overlay_layers") or []
    assert ref.process_mode == "original"
    assert len(layers) == 1
    assert layers[0]["asset_id"] == ref.id
    assert layers[0]["preset"] == "center-card"


def test_content_update_replaces_stale_editor_blocks_when_markdown_body_is_updated():
    db = make_session()
    project = Project(title="editor body sync", status="planning")
    db.add(project)
    db.flush()
    slide = Slide(
        project_id=project.id,
        page_num=1,
        type="content",
        content_json={
            "text_content": {"headline": "正文同步", "subhead": "", "body": "旧正文"},
            "content_blocks": [{"id": "body", "kind": "markdown", "markdown": "旧正文"}],
        },
        visual_json={},
    )
    db.add(slide)
    db.commit()

    slides_api.update_slide_content(
        project.id,
        slides_api.UpdateContentRequest(
            page_num=1,
            slide_id=slide.id,
            content_json={
                "text_content": {"headline": "正文同步", "subhead": "", "body": "用户手动编辑后的新版正文"},
                "speaker_notes": "同步备注",
            },
        ),
        db,
    )

    db.refresh(slide)
    state = content_body_storage_state(slide.content_json)
    assert state["effective_body"] == "用户手动编辑后的新版正文"
    assert state["text_body"] == "用户手动编辑后的新版正文"
    assert state["blocks_body"] == "用户手动编辑后的新版正文"
    assert state["consistent"] is True
