from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict


class ProjectBase(BaseModel):
    title: str
    status: Optional[str] = "draft"
    style_id: Optional[str] = None
    content_plan_confirmed: bool = False


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None
    style_id: Optional[str] = None
    content_plan_confirmed: Optional[bool] = None


class ProjectResponse(ProjectBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    style_proposal: Optional[dict] = None
    selected_style: Optional[dict] = None
    selected_template_recommendations: Optional[dict] = None
    created_at: datetime
    updated_at: datetime


class ReferenceImageResponse(BaseModel):
    id: str
    role: str = "style_ref"
    process_mode: str = "blend"
    url: str


class SlideBase(BaseModel):
    page_num: int
    type: Optional[str] = "content"
    status: Optional[str] = "pending"


class SlideResponse(SlideBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    content_json: Optional[dict] = None
    visual_json: Optional[dict] = None
    prompt_text: Optional[str] = None
    image_path: Optional[str] = None
    error_msg: Optional[str] = None
    reference_images: Optional[list[ReferenceImageResponse]] = None
