import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models.base import SessionLocal, engine
from app.models.models import Project, Slide


def import_content_plan(json_path: str, project_title: str):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    db = SessionLocal()
    try:
        # 创建项目
        project = Project(title=project_title, status="planning")
        db.add(project)
        db.commit()
        db.refresh(project)
        print(f"创建项目: {project.id} - {project.title}")

        slides_data = data.get("slides", [])
        for item in slides_data:
            text_content = item.get("text_content", {})
            # 统一字段名
            content_json = {
                "page_num": item["slide_number"],
                "type": item.get("type", "content"),
                "section_title": "",
                "text_content": {
                    "headline": text_content.get("headline", ""),
                    "subhead": text_content.get("subhead", ""),
                    "body": text_content.get("body", []),
                    "body_format": text_content.get("body_format", "bullets"),
                    "table_data": text_content.get("table_data"),
                },
                "speaker_notes": item.get("speaker_notes", ""),
                "visual_suggestion": "",
            }

            slide = Slide(
                project_id=project.id,
                page_num=item["slide_number"],
                type=item.get("type", "content"),
                content_json=content_json,
            )
            db.add(slide)

        db.commit()
        print(f"导入完成: 共 {len(slides_data)} 页")
        print(f"项目ID: {project.id}")
        return project.id
    finally:
        db.close()


if __name__ == "__main__":
    json_path = "/Users/Joe_1/Desktop/AI output/ppt/20260414_分众×粗门战略合作分享大纲/content_plan.json"
    project_title = "分众×粗门战略合作分享大纲"
    pid = import_content_plan(json_path, project_title)
    print(f"\n请在前端选择项目 ID: {pid}")
