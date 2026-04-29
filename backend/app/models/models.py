import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Integer, DateTime, Text, ForeignKey, JSON, Boolean
from sqlalchemy.orm import relationship

from app.models.base import Base


def gen_uuid():
    return str(uuid.uuid4())


def utc_now():
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    id = Column(String, primary_key=True, default=gen_uuid)
    title = Column(String, nullable=False)
    status = Column(String, default="draft")
    content_plan_confirmed = Column(Boolean, default=False, nullable=False)
    style_id = Column(String, nullable=True)
    style_proposal = Column(JSON, nullable=True)
    selected_style = Column(JSON, nullable=True)
    selected_template_recommendations = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    slides = relationship("Slide", back_populates="project", cascade="all, delete-orphan")
    reference_images = relationship("ReferenceImage", back_populates="project", cascade="all, delete-orphan")


class Slide(Base):
    __tablename__ = "slides"

    id = Column(String, primary_key=True, default=gen_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    page_num = Column(Integer, nullable=False)
    type = Column(String, default="content")
    status = Column(String, default="pending")
    error_msg = Column(Text, nullable=True)

    content_json = Column(JSON, default=dict)
    visual_json = Column(JSON, default=dict)
    prompt_text = Column(Text, nullable=True)
    image_path = Column(String, nullable=True)

    project = relationship("Project", back_populates="slides")
    reference_images = relationship("ReferenceImage", back_populates="slide")


class ReferenceImage(Base):
    __tablename__ = "reference_images"

    id = Column(String, primary_key=True, default=gen_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    slide_id = Column(String, ForeignKey("slides.id"), nullable=True)
    file_path = Column(String, nullable=False)
    role = Column(String, default="style_ref")
    process_mode = Column(String, default="blend")

    project = relationship("Project", back_populates="reference_images")
    slide = relationship("Slide", back_populates="reference_images")
