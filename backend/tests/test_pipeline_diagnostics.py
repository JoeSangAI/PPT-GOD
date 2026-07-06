import json

from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import slides as slides_api
from app.models.base import Base
from app.models.models import ReferenceImage, Slide
from app.models.models import Project
from app.services import content_plan, generation_pipeline
from app.services.content_plan_diagnostics import (
    build_content_outline_quality_diagnostic,
    build_content_source_diagnostic,
)
from app.services.pipeline_diagnostics import pipeline_diagnostic_log_path
from app.services.run_state import create_project_run


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


class DummySourceContext:
    status = "ready"
    selected_scopes = [{"source": "chapter-1"}]
    source_stats = {"documents": 1, "estimated_tokens": 90_000}


def test_content_source_diagnostic_exposes_budget_and_truncation_boundaries():
    documents = "A" * 200_000
    source_draft = "P1｜content｜正文｜标题\n- bullet\n\n" * 4_000

    diagnostic = build_content_source_diagnostic(
        topic="把长文做成课件",
        documents=documents,
        source_context=DummySourceContext(),
        source_draft_markdown=source_draft,
        target_page_count=30,
        requested_page_count=None,
    )

    assert diagnostic["source_context_token_budget"] >= 500_000
    assert diagnostic["page_map_document_limit_chars"] == content_plan.PAGE_MAP_DOCUMENT_LIMIT
    assert diagnostic["source_draft_limit_chars"] == content_plan.PAGE_MAP_SOURCE_DRAFT_LIMIT
    assert diagnostic["documents_chars"] == 200_000
    assert diagnostic["page_map_document_will_truncate"] is True
    assert diagnostic["source_draft_will_truncate"] is True
    assert diagnostic["selected_scope_count"] == 1


def test_content_outline_quality_diagnostic_flags_thin_duplicate_pages():
    outline = [
        {"page_num": 1, "type": "cover", "text_content": {"headline": "封面"}},
        {"page_num": 2, "type": "content", "text_content": {"headline": "同题", "body": ""}},
        {"page_num": 3, "type": "content", "text_content": {"headline": "同题", "body": "- 太短"}},
    ]

    diagnostic = build_content_outline_quality_diagnostic(outline, target_page_count=6, min_pages=5)

    assert diagnostic["outline_page_count"] == 3
    assert diagnostic["below_min_pages"] is True
    assert diagnostic["empty_body_pages"] == [2]
    assert diagnostic["thin_body_pages"] == [2, 3]
    assert diagnostic["duplicate_headlines"][0]["headline"] == "同题"


def test_reference_input_audit_logs_loaded_and_overlay_skipped_refs(tmp_path, monkeypatch):
    monkeypatch.setattr(generation_pipeline.settings, "OUTPUT_DIR", str(tmp_path))

    normal_path = tmp_path / "normal.png"
    overlay_path = tmp_path / "overlay.png"
    Image.new("RGB", (80, 80), "blue").save(normal_path)
    Image.new("RGB", (80, 80), "red").save(overlay_path)

    normal_ref = ReferenceImage(
        id="ref-normal",
        project_id="project-1",
        slide_id="slide-1",
        file_path=str(normal_path),
        role="content_ref",
        process_mode="blend",
    )
    overlay_ref = ReferenceImage(
        id="ref-overlay",
        project_id="project-1",
        slide_id="slide-1",
        file_path=str(overlay_path),
        role="content_ref",
        process_mode="original",
    )
    slide = Slide(
        id="slide-1",
        project_id="project-1",
        page_num=1,
        visual_json={
            "overlay_layers": [
                {"asset_id": "ref-overlay", "enabled": True, "preset": "right-card", "mode": "exact_cutout"}
            ]
        },
    )
    slide.reference_images = [normal_ref, overlay_ref]

    refs = generation_pipeline._load_reference_images(
        slide,
        audit_project_id="project-1",
        audit_run_id="run-1",
    )

    assert [ref.get("id") for ref in refs if ref.get("image") is not None] == ["ref-normal"]
    log_path = pipeline_diagnostic_log_path("project-1", "run-1", kind="image-reference")
    records = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    event = records[-1]
    assert event["event"] == "reference_inputs_resolved"
    assert event["page_num"] == 1
    assert event["skipped_overlay_count"] == 1
    assert event["loaded_image_reference_count"] == 1
    assert event["overlay_asset_ids"] == ["ref-overlay"]


def test_content_plan_background_writes_source_and_outline_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setattr(slides_api.settings, "OUTPUT_DIR", str(tmp_path))
    db = make_session()
    project = Project(title="诊断测试", status="draft")
    db.add(project)
    db.flush()
    run = create_project_run(db, project.id, kind="content_plan", stage="content_plan", total_count=8)
    db.commit()

    class SourceContext:
        status = "ready"
        text = "A" * 200_000
        selected_scopes = [{"source": "chapter-1"}]
        source_stats = {"documents": 1, "estimated_tokens": 90_000}

    outline = [
        {"page_num": 1, "type": "cover", "text_content": {"headline": "封面"}},
        {"page_num": 2, "type": "content", "text_content": {"headline": "正文", "body": "- 具体内容"}},
    ]

    monkeypatch.setattr(slides_api, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    monkeypatch.setattr(slides_api, "load_project_source_packs", lambda *_args, **_kwargs: [{"stats": {"estimated_tokens": 90_000}}])
    monkeypatch.setattr(slides_api, "build_source_context", lambda *_args, **_kwargs: SourceContext())
    monkeypatch.setattr(slides_api, "generate_content_plan", lambda *_args, **_kwargs: outline)
    monkeypatch.setattr(slides_api, "_content_plan_run_is_active_for_writeback", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(slides_api, "infer_intent_contract", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(slides_api, "infer_effective_content_intent_contract", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(slides_api, "infer_page_count_from_single_ppt", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(slides_api, "resolve_content_plan_page_target", lambda _topic, page_count=None, *_args, **_kwargs: (page_count or 8, 5, page_count or 8))

    slides_api._generate_content_plan_bg(
        project.id,
        "把长材料做成 PPT",
        page_count=8,
        run_id=run.id,
    )

    log_path = pipeline_diagnostic_log_path(project.id, run.id, kind="content-plan")
    records = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert [record["event"] for record in records] == [
        "content_plan_source_context",
        "content_plan_outline_quality",
    ]
    assert records[0]["page_map_document_will_truncate"] is True
    assert records[0]["source_context_token_budget"] >= 500_000
    assert records[1]["outline_page_count"] == 2
    assert records[1]["below_min_pages"] is True


def test_generate_content_page_map_reports_internal_source_draft_diagnostics(monkeypatch):
    events = []
    source_draft = [
        {
            "page_num": index,
            "type": "content",
            "section_title": "章节",
            "headline": f"页面 {index}",
            "bullets": ["具体事实"],
            "speaker_notes": "讲稿内容：围绕具体事实展开讲述，并自然转场。",
            "visual_suggestion": "信息图",
        }
        for index in range(1, 1600)
    ]

    def fake_model_page_map(**_kwargs):
        return [
            {
                "page_num": 1,
                "type": "content",
                "section_title": "章节",
                "headline": "页面 1",
                "bullets": ["具体事实"],
                "speaker_notes": "讲稿内容：围绕具体事实展开讲述，并自然转场。",
                "visual_suggestion": "信息图",
                "generation_status": "page_map_model",
            }
        ]

    monkeypatch.setattr(content_plan, "_source_draft_page_map", lambda **_kwargs: source_draft)
    monkeypatch.setattr(content_plan, "_generate_model_page_map", fake_model_page_map)
    monkeypatch.setattr(content_plan, "resolve_content_plan_page_target", lambda *_args, **_kwargs: (1, 1, 1))
    monkeypatch.setattr(content_plan, "_page_map_is_useful", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(content_plan, "_missing_source_tail_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(content_plan, "_missing_source_structure_candidates", lambda *_args, **_kwargs: [])

    content_plan.generate_content_page_map(
        topic="把长材料做成 PPT",
        documents="材料正文",
        page_count=1,
        on_progress=events.append,
    )

    diagnostics = [
        event for event in events
        if event.get("diagnostic_event") == "content_plan_page_map_input"
    ]
    assert diagnostics
    diagnostic = diagnostics[-1]
    assert diagnostic["page_map_document_limit_chars"] == content_plan.PAGE_MAP_DOCUMENT_LIMIT
    assert diagnostic["source_draft_limit_chars"] == content_plan.PAGE_MAP_SOURCE_DRAFT_LIMIT
    assert diagnostic["source_draft_chars"] > content_plan.PAGE_MAP_SOURCE_DRAFT_LIMIT
    assert diagnostic["source_draft_will_truncate"] is True
