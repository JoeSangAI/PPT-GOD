import os
import re

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text

from app.core.config import settings
from app.core.provider_credentials import ProviderCredentials, reset_provider_credentials, set_provider_credentials
from app.core.tester_auth import (
    LOCAL_ADMIN_TESTER_ID,
    TESTER_ID_HEADER,
    reset_current_request_is_local,
    reset_current_tester_id,
    set_current_request_is_local,
    set_current_tester_id,
)
from app.api import auth, projects, slides, chat, documents
from app.models.base import SessionLocal, engine
from app.models import models

models.Base.metadata.create_all(bind=engine)


def _ensure_runtime_mvp_schema() -> None:
    """Keep local SQLite/Postgres dev DBs usable when create_all cannot add columns."""
    inspector = inspect(engine)
    if "projects" not in inspector.get_table_names():
        return
    project_columns = {col["name"] for col in inspector.get_columns("projects")}
    if "tester_id" in project_columns:
        return
    with engine.begin() as conn:
        dialect = engine.dialect.name
        if dialect == "postgresql":
            conn.execute(text("ALTER TABLE projects ADD COLUMN IF NOT EXISTS tester_id VARCHAR"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_projects_tester_id ON projects (tester_id)"))
        else:
            conn.execute(text("ALTER TABLE projects ADD COLUMN tester_id VARCHAR"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_projects_tester_id ON projects (tester_id)"))


_ensure_runtime_mvp_schema()

# Startup validation: warn about missing API keys in real mode
if settings.IMAGE_GEN_MODE == "real":
    missing = []
    if not settings.MINIMAX_API_KEY:
        missing.append("MINIMAX_API_KEY")
    if not settings.DEER_API_KEY:
        missing.append("DEER_API_KEY")
    if missing:
        import warnings
        warnings.warn(
            f"IMAGE_GEN_MODE=real but missing API keys: {', '.join(missing)}. "
            "Set them in .env or switch to IMAGE_GEN_MODE=mock/cached.",
            stacklevel=2,
        )

app = FastAPI(title=settings.PROJECT_NAME, version=settings.VERSION)


def _cors_origins() -> list[str]:
    return [origin.strip() for origin in (settings.CORS_ORIGINS or "").split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_project_path_re = re.compile(r"^/projects/([^/]+)")


@app.middleware("http")
async def mvp_context_and_project_guard(request: Request, call_next):
    provider_token = set_provider_credentials(ProviderCredentials.from_headers(request.headers))
    tester_id = (request.headers.get(TESTER_ID_HEADER) or request.query_params.get("tester_id") or "").strip() or None
    tester_token = set_current_tester_id(tester_id)
    client_host = (request.client.host if request.client else "") or ""
    host_header = (request.headers.get("host") or "").split(":", 1)[0]
    local_hosts = {"127.0.0.1", "::1", "localhost"}
    # Docker Desktop forwards browser requests through a bridge IP, so localhost
    # debug traffic is best identified by the Host header.
    is_local_request = client_host in local_hosts or host_header in local_hosts
    local_token = set_current_request_is_local(is_local_request)
    try:
        match = None if request.method == "OPTIONS" else _project_path_re.match(request.url.path)
        if match:
            project_id = match.group(1)
            db = SessionLocal()
            try:
                project = db.query(models.Project).filter(models.Project.id == project_id).first()
                local_admin_allowed = tester_id == LOCAL_ADMIN_TESTER_ID and is_local_request
                if project and project.tester_id and project.tester_id != tester_id and not local_admin_allowed:
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "这个项目属于其他测试账号，请切换账号后再试"},
                    )
            finally:
                db.close()
        return await call_next(request)
    finally:
        reset_current_tester_id(tester_token)
        reset_current_request_is_local(local_token)
        reset_provider_credentials(provider_token)

app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(slides.router)
app.include_router(chat.router)
app.include_router(documents.router)

# Static files for uploads and outputs
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
os.makedirs(settings.OUTPUT_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")
app.mount("/outputs", StaticFiles(directory=settings.OUTPUT_DIR), name="outputs")


@app.get("/health")
def health_check():
    return {"status": "ok"}


# Production: serve frontend build (SPA fallback) — must be registered LAST
_frontend_dist = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
if os.path.exists(_frontend_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(_frontend_dist, "assets")), name="assets")

    @app.get("/{full_path:path}")
    def serve_frontend(full_path: str):
        index_path = os.path.join(_frontend_dist, "index.html")
        if os.path.exists(index_path):
            from fastapi.responses import FileResponse
            return FileResponse(index_path, headers={"Cache-Control": "no-store"})
        return {"detail": "Not Found"}
