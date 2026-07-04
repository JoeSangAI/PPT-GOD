import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import agent as agent_api
from app.core.tester_auth import LOCAL_ADMIN_TESTER_ID, reset_current_request_is_local, set_current_request_is_local
from app.models.base import Base
from app.models.models import Project, Slide


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
    assert response["ui_url"].endswith(f"/projects/{project.id}?stage=content")
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
