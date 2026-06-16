from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import projects as projects_api
from app.core.tester_auth import get_or_create_tester, require_existing_tester, verify_project_access
from app.models.base import Base
from app.models.models import Project
from app.schemas.project import ProjectCreate


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_same_name_login_reuses_same_tester_space():
    db = make_session()

    first = get_or_create_tester(db, "阿桑")
    second = get_or_create_tester(db, "  阿桑  ")

    assert second.id == first.id
    assert second.login_key == "阿桑"
    assert second.display_name == "阿桑"


def test_same_name_login_requires_matching_passcode():
    db = make_session()

    first = get_or_create_tester(db, "阿桑", "123456")
    second = get_or_create_tester(db, "  阿桑  ", "123456")

    assert second.id == first.id

    try:
        get_or_create_tester(db, "阿桑", "wrong-passcode")
    except HTTPException as exc:
        assert exc.status_code == 401
        assert "密码不正确" in str(exc.detail)
    else:
        raise AssertionError("expected wrong passcode to be rejected")


def test_project_access_is_scoped_by_tester_id():
    db = make_session()
    asang = get_or_create_tester(db, "阿桑")
    friend = get_or_create_tester(db, "朋友A")

    project = Project(title="阿桑的项目", tester_id=asang.id)
    db.add(project)
    db.commit()
    db.refresh(project)

    assert verify_project_access(project, asang.id).id == project.id

    try:
        verify_project_access(project, friend.id)
    except HTTPException as exc:
        assert exc.status_code == 403
        assert "其他测试账号" in str(exc.detail)
    else:
        raise AssertionError("expected another tester to be rejected")


def test_require_existing_tester_rejects_stale_id():
    db = make_session()

    try:
        require_existing_tester(db, "stale-browser-id")
    except HTTPException as exc:
        assert exc.status_code == 401
        assert "登录状态已失效" in str(exc.detail)
    else:
        raise AssertionError("expected stale tester id to be rejected")


def test_create_project_rejects_stale_tester_before_insert():
    db = make_session()

    try:
        projects_api.create_project(ProjectCreate(title="旧缓存账号"), tester_id="stale-browser-id", db=db)
    except HTTPException as exc:
        assert exc.status_code == 401
        assert "登录状态已失效" in str(exc.detail)
    else:
        raise AssertionError("expected stale tester id to be rejected")

    assert db.query(Project).count() == 0
