from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import auth as auth_api
from app.core.tester_auth import get_or_create_tester
from app.models.base import Base
from app.models.models import Project
from app.services.browser_handoff import (
    BrowserHandoffError,
    clear_browser_handoffs_for_tests,
    issue_browser_handoff,
    redeem_browser_handoff,
)


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


@pytest.fixture(autouse=True)
def clear_handoffs():
    clear_browser_handoffs_for_tests()
    yield
    clear_browser_handoffs_for_tests()


def test_handoff_is_short_lived_project_bound_and_one_time():
    token, issued = issue_browser_handoff(
        tester_id="tester-1",
        project_id="project-1",
        stage="content",
        ttl_seconds=90,
        now=100,
    )

    assert issued.expires_at == 190
    assert redeem_browser_handoff(token, target_project_id="project-1", now=120) == issued
    with pytest.raises(BrowserHandoffError, match="已失效或已使用"):
        redeem_browser_handoff(token, target_project_id="project-1", now=121)


def test_handoff_rejects_a_changed_target_project_and_consumes_token():
    token, _issued = issue_browser_handoff(
        tester_id="tester-1",
        project_id="project-1",
        stage="visual",
        ttl_seconds=90,
        now=100,
    )

    with pytest.raises(BrowserHandoffError, match="目标项目不匹配"):
        redeem_browser_handoff(token, target_project_id="project-2", now=110)
    with pytest.raises(BrowserHandoffError, match="已失效或已使用"):
        redeem_browser_handoff(token, target_project_id="project-1", now=111)


def test_handoff_expires_without_leaving_a_reusable_credential():
    token, _issued = issue_browser_handoff(
        tester_id="tester-1",
        project_id="project-1",
        stage="project",
        ttl_seconds=5,
        now=100,
    )

    with pytest.raises(BrowserHandoffError, match="已失效或已使用"):
        redeem_browser_handoff(token, target_project_id="project-1", now=106)


def test_auth_handoff_issues_only_for_the_project_owner_and_redeems_into_that_account():
    db = make_session()
    owner = get_or_create_tester(db, "阿桑")
    other = get_or_create_tester(db, "其他账号")
    project = Project(title="阿桑的项目", tester_id=owner.id)
    db.add(project)
    db.commit()
    db.refresh(project)

    with pytest.raises(HTTPException) as forbidden:
        auth_api.create_browser_handoff(
            auth_api.BrowserHandoffCreateRequest(project_id=project.id, stage="content"),
            tester_id=other.id,
            db=db,
        )
    assert forbidden.value.status_code == 403

    issued = auth_api.create_browser_handoff(
        auth_api.BrowserHandoffCreateRequest(
            project_id=project.id,
            stage="content",
            frontend_base_url="http://localhost:8000",
            agent_text=True,
            agent_image=False,
            agent_name="Codex",
        ),
        tester_id=owner.id,
        db=db,
    )
    url = urlsplit(issued["handoff_url"])
    token = parse_qs(url.query)["handoff"][0]

    assert url.path == f"/app/projects/{project.id}"
    assert parse_qs(url.query)["stage"] == ["content"]
    redeemed = auth_api.consume_browser_handoff(
        auth_api.BrowserHandoffRedeemRequest(token=token, project_id=project.id),
        db=db,
    )
    assert redeemed == {
        "ok": True,
        "tester_id": owner.id,
        "display_name": "阿桑",
        "project_id": project.id,
        "stage": "content",
        "agent_capabilities": {
            "text_generation": True,
            "image_generation": False,
        },
        "agent_name": "Codex",
    }


def test_auth_handoff_does_not_redeem_for_a_different_project_url():
    db = make_session()
    owner = get_or_create_tester(db, "阿桑")
    project = Project(title="阿桑的项目", tester_id=owner.id)
    other_project = Project(title="另一个项目", tester_id=owner.id)
    db.add_all([project, other_project])
    db.commit()

    issued = auth_api.create_browser_handoff(
        auth_api.BrowserHandoffCreateRequest(project_id=project.id, stage="content"),
        tester_id=owner.id,
        db=db,
    )
    token = parse_qs(urlsplit(issued["handoff_url"]).query)["handoff"][0]

    with pytest.raises(HTTPException) as mismatch:
        auth_api.consume_browser_handoff(
            auth_api.BrowserHandoffRedeemRequest(token=token, project_id=other_project.id),
            db=db,
        )
    assert mismatch.value.status_code == 401
    assert "目标项目不匹配" in str(mismatch.value.detail)
