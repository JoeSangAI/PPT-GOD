import copy

import pytest

from app.services.content_plan_markdown import (
    ContentPlanMarkdownError,
    ContentPlanSyncConflictError,
    content_body_storage_state,
    effective_content_body_markdown,
    export_content_plan_markdown,
    import_content_plan_markdown,
    parse_content_plan_markdown,
    sync_content_plan_markdown,
    validate_content_plan_markdown,
)
from app.services.artifact_versions import artifact_stale
from app.services.slide_types import CANONICAL_SLIDE_TYPES
from app.models.base import Base
from app.models.models import Project, ReferenceImage, Slide, SlideVersion
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


VALID_MARKDOWN = """# AI 时代消费者决策路径

## P1
### 类型
cover

### 标题
AI 时代消费者决策路径

### 副标题
从搜索到推荐，再到智能体代买

### 正文
这是一套面向品牌团队的趋势判断。

### 备注
开场说明：这不是渠道变化，而是决策权迁移。

## P2
### 类型
content

### 标题
消费者不再只是在搜索框里决策

### 副标题
算法、内容和智能体正在重排入口

### 正文
- 传统搜索仍然重要，但不再是唯一入口。
- 内容平台承担了种草和比较。
- 智能体开始替用户做筛选和执行。

### 备注
这一页解释为什么品牌需要重新理解决策路径。
"""


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_parse_strict_content_plan_markdown_to_slide_payloads():
    parsed = parse_content_plan_markdown(VALID_MARKDOWN)

    assert parsed.title == "AI 时代消费者决策路径"
    assert parsed.warnings == []
    assert len(parsed.slides) == 2
    assert parsed.slides[0]["page_num"] == 1
    assert parsed.slides[0]["type"] == "cover"
    assert parsed.slides[0]["text_content"]["headline"] == "AI 时代消费者决策路径"
    assert parsed.slides[0]["text_content"]["subhead"] == "从搜索到推荐，再到智能体代买"
    assert "趋势判断" in parsed.slides[0]["text_content"]["body"]
    assert parsed.slides[0]["speaker_notes"].startswith("开场说明")


def test_validate_rejects_missing_required_field():
    markdown = VALID_MARKDOWN.replace("### 正文\n这是一套面向品牌团队的趋势判断。\n", "")

    result = validate_content_plan_markdown(markdown)

    assert not result.ok
    assert any("P1" in error and "正文" in error for error in result.errors)


def test_validate_allows_headline_only_ending_body():
    markdown = """# 收束页

## P1
### 类型
ending
### 标题
当技术让我们跑得更快，是否也让人生活得更好？
### 副标题

### 正文

### 备注
停顿后结束。
"""

    result = validate_content_plan_markdown(markdown)

    assert result.ok is True
    assert result.slides[0]["text_content"]["body"] == ""


def test_validate_still_rejects_empty_content_body():
    markdown = """# 论证页

## P1
### 类型
content
### 标题
核心判断
### 副标题

### 正文

### 备注
展开说明。
"""

    result = validate_content_plan_markdown(markdown)

    assert result.ok is False
    assert any("正文不能为空" in error for error in result.errors)


def test_validate_warns_on_empty_optional_fields_and_non_contiguous_pages():
    markdown = VALID_MARKDOWN.replace("## P2", "## P4").replace(
        "### 副标题\n算法、内容和智能体正在重排入口\n",
        "### 副标题\n\n",
    )

    result = validate_content_plan_markdown(markdown)

    assert result.ok
    assert any("页码不连续" in warning for warning in result.warnings)
    assert any("P4" in warning and "副标题为空" in warning for warning in result.warnings)


def test_validate_rejects_duplicate_pages_and_unknown_type():
    markdown = VALID_MARKDOWN.replace("## P2", "## P1").replace("content", "unknown_type", 1)

    result = validate_content_plan_markdown(markdown)

    assert not result.ok
    assert any("页码重复" in error for error in result.errors)
    assert any("unknown_type" in error for error in result.errors)


@pytest.mark.parametrize("slide_type", CANONICAL_SLIDE_TYPES)
def test_validate_accepts_every_canonical_slide_type(slide_type):
    markdown = VALID_MARKDOWN.replace("### 类型\ncover", f"### 类型\n{slide_type}", 1)

    result = validate_content_plan_markdown(markdown)

    assert result.ok, result.errors
    assert result.slides[0]["type"] == slide_type


@pytest.mark.parametrize(
    "slide_type",
    ["content_dense", "content_hero", "content_split", "content_top", "agenda", "chart", "table"],
)
def test_validate_rejects_legacy_and_half_supported_slide_types(slide_type):
    markdown = VALID_MARKDOWN.replace("### 类型\ncover", f"### 类型\n{slide_type}", 1)

    result = validate_content_plan_markdown(markdown)

    assert not result.ok
    assert any(f"类型「{slide_type}」不合法" in error for error in result.errors)


def test_import_content_plan_markdown_creates_project_and_pending_slides():
    db = make_session()

    receipt = import_content_plan_markdown(db, VALID_MARKDOWN, title=None, tester_id=None)

    project = db.query(Project).filter(Project.id == receipt.project_id).first()
    slides = db.query(Slide).filter(Slide.project_id == receipt.project_id).order_by(Slide.page_num).all()
    assert project.title == "AI 时代消费者决策路径"
    assert project.status == "planning"
    assert project.content_plan_confirmed is False
    assert receipt.ui_url.endswith(f"/projects/{project.id}?stage=content")
    assert [slide.page_num for slide in slides] == [1, 2]
    assert slides[0].status == "pending"
    assert slides[0].content_json["text_content"]["headline"] == "AI 时代消费者决策路径"
    assert slides[0].content_json["content_blocks"] == [
        {"id": "body", "kind": "markdown", "markdown": "这是一套面向品牌团队的趋势判断。"}
    ]
    assert slides[1].content_json["speaker_notes"].startswith("这一页解释")


def test_export_content_plan_markdown_round_trips_to_strict_agent_format():
    db = make_session()
    receipt = import_content_plan_markdown(db, VALID_MARKDOWN, title=None, tester_id=None)
    project = db.query(Project).filter(Project.id == receipt.project_id).first()
    slides = db.query(Slide).filter(Slide.project_id == receipt.project_id).order_by(Slide.page_num).all()

    export = export_content_plan_markdown(project, slides)
    validation = validate_content_plan_markdown(export.markdown)

    assert export.slides_count == 2
    assert export.filename.endswith(".md")
    assert "## P1 ·" not in export.markdown
    assert "### 类型\n\ncover" in export.markdown
    assert validation.ok is True
    assert validation.slides[1]["text_content"]["headline"] == "消费者不再只是在搜索框里决策"


def test_export_normalizes_legacy_stored_type_to_canonical_contract():
    db = make_session()
    receipt = import_content_plan_markdown(db, VALID_MARKDOWN, title=None, tester_id=None)
    project = db.query(Project).filter(Project.id == receipt.project_id).first()
    slides = db.query(Slide).filter(Slide.project_id == receipt.project_id).order_by(Slide.page_num).all()
    slides[1].type = "content_split"
    slides[1].content_json = {**slides[1].content_json, "type": "content_split"}
    db.commit()

    export = export_content_plan_markdown(project, slides)
    validation = validate_content_plan_markdown(export.markdown)

    assert "content_split" not in export.markdown
    assert validation.ok is True
    assert validation.slides[1]["type"] == "content"


def test_export_and_effective_body_follow_editor_content_blocks_instead_of_stale_text_mirror():
    db = make_session()
    receipt = import_content_plan_markdown(db, VALID_MARKDOWN, title=None, tester_id=None)
    project = db.query(Project).filter(Project.id == receipt.project_id).first()
    slide = db.query(Slide).filter(Slide.project_id == project.id, Slide.page_num == 1).first()
    slide.content_json = {
        **slide.content_json,
        "text_content": {**slide.content_json["text_content"], "body": "CLI 曾经错误读取的新版正文。"},
        "content_blocks": [{"id": "body", "kind": "markdown", "markdown": "用户在结构化编辑器里仍然看到的旧正文。"}],
    }
    db.commit()

    state = content_body_storage_state(slide.content_json)
    exported = export_content_plan_markdown(project, [slide])

    assert state["effective_body"] == "用户在结构化编辑器里仍然看到的旧正文。"
    assert state["text_body"] == "CLI 曾经错误读取的新版正文。"
    assert state["consistent"] is False
    assert effective_content_body_markdown(slide.content_json) == state["effective_body"]
    assert "用户在结构化编辑器里仍然看到的旧正文。" in exported.markdown
    assert "CLI 曾经错误读取的新版正文。" not in exported.markdown


def test_import_rejects_invalid_markdown_without_partial_project():
    db = make_session()
    bad_markdown = "# Bad\n\n## P1\n### 类型\ncontent\n### 标题\n只有标题"

    with pytest.raises(ContentPlanMarkdownError):
        import_content_plan_markdown(db, bad_markdown, title="Bad", tester_id=None)

    assert db.query(Project).count() == 0
    assert db.query(Slide).count() == 0


UPDATED_MARKDOWN = """# 标题不应覆盖原项目

## P1
### 类型
cover

### 标题
AI 时代消费者决策路径

### 副标题
从搜索到推荐，再到智能体代买

### 正文
这是一套经过更新、面向品牌团队的趋势判断。

### 备注
开场说明：先制造冲突，再讨论决策权迁移。

## P2
### 类型
section

### 标题
新的转折页

### 副标题
企业正在夹缝中生存

### 正文
一边是更精明的消费者，另一边是更强势的平台。

### 备注
这一页是新增的叙事转折。

## P3
### 类型
content

### 标题
消费者不再只是在搜索框里决策

### 副标题
算法、内容和智能体正在重排入口

### 正文
- 传统搜索仍然重要，但不再是唯一入口。
- 内容平台承担了种草和比较。
- 智能体开始替用户做筛选和执行。

### 备注
这一页解释为什么品牌需要重新理解决策路径。
"""


def _prepare_sync_project(db):
    receipt = import_content_plan_markdown(db, VALID_MARKDOWN, title="保留的项目名", tester_id=None)
    project = db.query(Project).filter(Project.id == receipt.project_id).first()
    slides = db.query(Slide).filter(Slide.project_id == project.id).order_by(Slide.page_num).all()
    project.status = "completed"
    project.content_plan_confirmed = True
    project.selected_style = {"name": "保留风格"}
    slides[1].status = "completed"
    slides[1].visual_json = {"page_num": 2, "layout": "keep"}
    slides[1].prompt_text = "keep prompt"
    slides[1].image_path = "/tmp/keep.png"
    retained_ref = ReferenceImage(
        project_id=project.id,
        slide_id=slides[1].id,
        file_path="/tmp/keep-ref.png",
        role="visual_asset",
    )
    retained_version = SlideVersion(
        project_id=project.id,
        slide_id=slides[1].id,
        image_path="/tmp/keep-v1.png",
        version_number=1,
    )
    obsolete = Slide(
        project_id=project.id,
        page_num=3,
        type="quote",
        status="completed",
        content_json={
            "page_num": 3,
            "type": "quote",
            "text_content": {"headline": "待删除页", "subhead": "", "body": "这页已经不再需要，应当从项目中删除。"},
            "speaker_notes": "删除。",
        },
        visual_json={"layout": "obsolete"},
        prompt_text="obsolete prompt",
        image_path="/tmp/obsolete.png",
    )
    db.add_all([retained_ref, retained_version, obsolete])
    db.flush()
    obsolete_ref = ReferenceImage(
        project_id=project.id,
        slide_id=obsolete.id,
        file_path="/tmp/obsolete-ref.png",
        role="visual_asset",
    )
    db.add(obsolete_ref)
    db.commit()
    return project, slides[0], slides[1], obsolete


def test_sync_content_plan_dry_run_reports_diff_without_mutation():
    db = make_session()
    project, first, retained, obsolete = _prepare_sync_project(db)

    receipt = sync_content_plan_markdown(db, project, UPDATED_MARKDOWN)

    assert receipt.applied is False
    assert receipt.summary == {
        "changed": 2,
        "added": 1,
        "deleted": 1,
        "unchanged": 0,
        "total_before": 3,
        "total_after": 3,
    }
    assert len(receipt.preview_token) == 40
    assert any(change["slide_id"] == retained.id and change["to_page"] == 3 for change in receipt.changes)
    assert any(change["slide_id"] == obsolete.id and change["action"] == "deleted" for change in receipt.changes)
    assert any("会同时删除" in warning for warning in receipt.warnings)
    db.refresh(project)
    db.refresh(first)
    db.refresh(retained)
    assert project.title == "保留的项目名"
    assert project.status == "completed"
    assert project.content_plan_confirmed is True
    assert first.content_json["text_content"]["body"] == "这是一套面向品牌团队的趋势判断。"
    assert retained.page_num == 2
    assert db.query(Slide).filter(Slide.project_id == project.id).count() == 3


def test_sync_content_plan_apply_preserves_retained_assets_and_project_state():
    db = make_session()
    project, first, retained, obsolete = _prepare_sync_project(db)
    first_id = first.id
    retained_id = retained.id
    obsolete_id = obsolete.id
    preview = sync_content_plan_markdown(db, project, UPDATED_MARKDOWN)

    receipt = sync_content_plan_markdown(
        db,
        project,
        UPDATED_MARKDOWN,
        apply=True,
        expected_preview_token=preview.preview_token,
    )

    assert receipt.applied is True
    current = db.query(Slide).filter(Slide.project_id == project.id).order_by(Slide.page_num).all()
    assert [slide.page_num for slide in current] == [1, 2, 3]
    assert current[0].id == first_id
    assert current[2].id == retained_id
    assert current[1].id not in {first_id, retained_id, obsolete_id}
    assert current[0].content_json["text_content"]["body"].startswith("这是一套经过更新")
    assert current[2].prompt_text == "keep prompt"
    assert current[2].image_path == "/tmp/keep.png"
    assert current[2].visual_json["layout"] == "keep"
    assert current[2].visual_json["page_num"] == 3
    assert artifact_stale(current[2].visual_json) == {"content": True}
    assert len(current[2].reference_images) == 1
    assert len(current[2].versions) == 1
    assert db.query(Slide).filter(Slide.id == obsolete_id).first() is None
    db.refresh(project)
    assert project.title == "保留的项目名"
    assert project.status == "completed"
    assert project.content_plan_confirmed is True
    assert project.selected_style == {"name": "保留风格"}


def test_sync_content_plan_rejects_stale_preview_token():
    db = make_session()
    project, first, _retained, _obsolete = _prepare_sync_project(db)
    preview = sync_content_plan_markdown(db, project, UPDATED_MARKDOWN)
    first.content_json = {
        **first.content_json,
        "text_content": {**first.content_json["text_content"], "body": "预览之后被其他人改过。"},
    }
    db.commit()

    with pytest.raises(ContentPlanSyncConflictError):
        sync_content_plan_markdown(
            db,
            project,
            UPDATED_MARKDOWN,
            apply=True,
            expected_preview_token=preview.preview_token,
        )


def test_sync_repairs_manual_editor_body_and_text_mirror_then_readback_is_clean():
    db = make_session()
    receipt = import_content_plan_markdown(db, VALID_MARKDOWN, title="正文一致性", tester_id=None)
    project = db.query(Project).filter(Project.id == receipt.project_id).first()
    slide = db.query(Slide).filter(Slide.project_id == project.id, Slide.page_num == 1).first()
    target_body = "这是一套面向品牌团队的趋势判断。"
    slide.content_json = {
        **slide.content_json,
        "text_content": {**slide.content_json["text_content"], "body": target_body},
        "content_blocks": [
            {
                "id": "body",
                "kind": "markdown",
                "markdown": "释放 = 衬衫。\n\n发现 = 下午 3 点咖啡。\n\n创造 = 私人学习教练。",
            }
        ],
    }
    db.commit()

    preview = sync_content_plan_markdown(db, project, VALID_MARKDOWN)

    first_change = next(change for change in preview.changes if change["slide_id"] == slide.id)
    assert preview.summary["changed"] == 1
    assert first_change["before"]["body"].startswith("释放 = 衬衫")
    assert "body" in first_change["changed_fields"]
    assert "body_storage" in first_change["changed_fields"]

    applied = sync_content_plan_markdown(
        db,
        project,
        VALID_MARKDOWN,
        apply=True,
        expected_preview_token=preview.preview_token,
    )
    db.refresh(slide)

    assert applied.readback is not None and applied.readback["ok"] is True
    assert slide.content_json["text_content"]["body"] == target_body
    assert slide.content_json["content_blocks"] == [
        {"id": "body", "kind": "markdown", "markdown": target_body}
    ]
    assert content_body_storage_state(slide.content_json)["consistent"] is True
    second_preview = sync_content_plan_markdown(db, project, VALID_MARKDOWN)
    assert second_preview.summary == {
        "changed": 0,
        "added": 0,
        "deleted": 0,
        "unchanged": 2,
        "total_before": 2,
        "total_after": 2,
    }


def test_sync_headline_only_preserves_existing_structured_body_blocks():
    db = make_session()
    receipt = import_content_plan_markdown(db, VALID_MARKDOWN, title="结构化正文保留", tester_id=None)
    project = db.query(Project).filter(Project.id == receipt.project_id).first()
    slide = db.query(Slide).filter(Slide.project_id == project.id, Slide.page_num == 1).first()
    slide.content_json = {
        **slide.content_json,
        "content_blocks": [
            {"id": "body", "kind": "markdown", "markdown": "这是一套面向品牌团队的趋势判断。"},
            {
                "id": "flow_1",
                "kind": "flow",
                "title": "保留的结构图",
                "source_spec": {"steps": ["输入", "判断", "行动"]},
                "route_mode": "crop",
            },
        ],
    }
    effective_body = effective_content_body_markdown(slide.content_json)
    slide.content_json = {
        **slide.content_json,
        "text_content": {**slide.content_json["text_content"], "body": effective_body},
    }
    original_blocks = copy.deepcopy(slide.content_json["content_blocks"])
    db.commit()
    markdown = export_content_plan_markdown(project, [slide]).markdown.replace(
        "### 标题\n\nAI 时代消费者决策路径",
        "### 标题\n\n只更新标题，不动结构化正文",
        1,
    )
    preview = sync_content_plan_markdown(db, project, markdown)

    assert preview.summary["changed"] == 1
    assert preview.changes[0]["changed_fields"] == ["headline"]
    sync_content_plan_markdown(
        db,
        project,
        markdown,
        apply=True,
        expected_preview_token=preview.preview_token,
    )
    db.refresh(slide)

    assert slide.content_json["text_content"]["headline"] == "只更新标题，不动结构化正文"
    assert slide.content_json["content_blocks"] == original_blocks
