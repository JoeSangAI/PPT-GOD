import os

from app.core.config import settings


def load_project_documents(project_id: str) -> str:
    """读取项目已上传文档的提取文本。"""
    docs_dir = os.path.join(settings.UPLOAD_DIR, project_id, "docs")
    if not os.path.exists(docs_dir):
        return ""

    parts = []
    for filename in sorted(os.listdir(docs_dir)):
        if filename.endswith(".extracted.txt"):
            original_name = filename[:-14]
            path = os.path.join(docs_dir, filename)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
                if len(text) > 8000:
                    text = text[:8000] + "\n\n[文档内容过长，已截断]"
                parts.append(f"--- 文档: {original_name} ---\n{text}")
            except Exception:
                continue

    return "\n\n".join(parts)
