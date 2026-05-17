import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Integer, DateTime, Text, ForeignKey, JSON, Boolean, Index
from sqlalchemy.orm import relationship

from app.models.base import Base


def gen_uuid():
    return str(uuid.uuid4())


def utc_now():
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    __table_args__ = (
        Index('ix_projects_status', 'status'),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    tester_id = Column(String, ForeignKey("tester_users.id"), nullable=True, index=True)
    title = Column(String, nullable=False)
    status = Column(String, default="draft")
    content_plan_confirmed = Column(Boolean, default=False, nullable=False)
    style_id = Column(String, nullable=True)
    style_proposal = Column(JSON, nullable=True)
    selected_style = Column(JSON, nullable=True)
    selected_template_recommendations = Column(JSON, nullable=True)
    intent_contract = Column(JSON, nullable=True)
    has_unread_notification = Column(Boolean, default=False, nullable=False)
    unread_notification_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    tester = relationship("TesterUser", back_populates="projects")
    slides = relationship("Slide", back_populates="project", cascade="all, delete-orphan")
    reference_images = relationship("ReferenceImage", back_populates="project", cascade="all, delete-orphan")
    runs = relationship("ProjectRun", back_populates="project", cascade="all, delete-orphan")


class TesterUser(Base):
    __tablename__ = "tester_users"

    id = Column(String, primary_key=True, default=gen_uuid)
    display_name = Column(String, nullable=False)
    login_key = Column(String, nullable=False, unique=True, index=True)
    passcode_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=utc_now)
    last_login_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    projects = relationship("Project", back_populates="tester")


class ProjectRun(Base):
    __tablename__ = "project_runs"

    __table_args__ = (
        Index('ix_project_runs_project_status', 'project_id', 'status'),
        Index('ix_project_runs_project_started', 'project_id', 'started_at'),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    kind = Column(String, nullable=False)
    status = Column(String, nullable=False, default="queued")
    stage = Column(String, nullable=True)
    message = Column(Text, nullable=True)
    target_page_nums = Column(JSON, nullable=True)
    total_count = Column(Integer, nullable=False, default=0)
    completed_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    task_id = Column(String, nullable=True)
    error_msg = Column(Text, nullable=True)
    started_at = Column(DateTime, default=utc_now)
    finished_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    project = relationship("Project", back_populates="runs")


class Slide(Base):
    __tablename__ = "slides"

    __table_args__ = (
        Index('ix_slides_project_id_status', 'project_id', 'status'),
        Index('ix_slides_project_id_page_num', 'project_id', 'page_num'),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    page_num = Column(Integer, nullable=False)
    type = Column(String, default="content")
    type_locked = Column(Boolean, default=False, nullable=False)
    status = Column(String, default="pending")
    error_msg = Column(Text, nullable=True)

    content_json = Column(JSON, default=dict)
    visual_json = Column(JSON, default=dict)
    prompt_text = Column(Text, nullable=True)
    image_path = Column(String, nullable=True)

    project = relationship("Project", back_populates="slides")
    reference_images = relationship("ReferenceImage", back_populates="slide", cascade="all, delete-orphan")
    versions = relationship("SlideVersion", back_populates="slide", cascade="all, delete-orphan", order_by="SlideVersion.version_number")


class ReferenceImage(Base):
    __tablename__ = "reference_images"

    __table_args__ = (
        Index('ix_reference_images_project_slide_role', 'project_id', 'slide_id', 'role'),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    slide_id = Column(String, ForeignKey("slides.id"), nullable=True)
    file_path = Column(String, nullable=False)
    role = Column(String, default="style_ref")
    process_mode = Column(String, default="blend")
    asset_name = Column(String, nullable=True)
    asset_kind = Column(String, nullable=True)
    usage_note = Column(Text, nullable=True)
    asset_analysis = Column(JSON, nullable=True)
    logo_anchor = Column(String, nullable=True)

    project = relationship("Project", back_populates="reference_images")
    slide = relationship("Slide", back_populates="reference_images")


class SlideVersion(Base):
    __tablename__ = "slide_versions"

    __table_args__ = (
        Index('ix_slide_versions_slide_id', 'slide_id'),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    slide_id = Column(String, ForeignKey("slides.id"), nullable=False)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    image_path = Column(String, nullable=False)
    prompt_text = Column(Text, nullable=True)
    version_number = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=utc_now)

    slide = relationship("Slide", back_populates="versions")
