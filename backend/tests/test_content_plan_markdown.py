import pytest

from app.services.content_plan_markdown import (
    ContentPlanMarkdownError,
    import_content_plan_markdown,
    parse_content_plan_markdown,
    validate_content_plan_markdown,
)
from app.models.base import Base
from app.models.models import Project, Slide
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
    assert slides[1].content_json["speaker_notes"].startswith("这一页解释")


def test_import_rejects_invalid_markdown_without_partial_project():
    db = make_session()
    bad_markdown = "# Bad\n\n## P1\n### 类型\ncontent\n### 标题\n只有标题"

    with pytest.raises(ContentPlanMarkdownError):
        import_content_plan_markdown(db, bad_markdown, title="Bad", tester_id=None)

    assert db.query(Project).count() == 0
    assert db.query(Slide).count() == 0
