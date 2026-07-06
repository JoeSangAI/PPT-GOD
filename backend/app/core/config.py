import os
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_BACKEND_DIR = Path(__file__).resolve().parents[2]
_PROJECT_ROOT = _BACKEND_DIR.parent


def _default_runtime_data_dir() -> str:
    return str(_PROJECT_ROOT / ".pptgod-data")


def _resolve_project_path(path: str) -> str:
    resolved = Path(path).expanduser()
    if not resolved.is_absolute():
        resolved = _PROJECT_ROOT / resolved
    return str(resolved)


class Settings(BaseSettings):
    PROJECT_NAME: str = "PPT GOD"
    VERSION: str = "0.1.0"

    RUNTIME_DATA_DIR: str = _default_runtime_data_dir()
    DATABASE_URL: str = ""
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_SOCKET_TIMEOUT_SECONDS: float = 3.0
    CELERY_BROKER_CONNECTION_TIMEOUT_SECONDS: float = 3.0
    CELERY_TEXT_QUEUE: str = "text"
    CELERY_IMAGE_QUEUE: str = "image"
    CELERY_LOCAL_WORKER_CONCURRENCY: int = 2

    MINIMAX_API_KEY: str = ""
    MINIMAX_API_BASE: str = "https://api.minimaxi.com/v1"
    MINIMAX_LLM_MODEL: str = "MiniMax-M3"
    COMET_API_KEY: str = ""
    COMET_API_BASE: str = "https://api.cometapi.com/v1"
    COMET_IMAGE_MODEL: str = "gpt-image-2"
    IMAGE_API_TIMEOUT_SECONDS: float = 125.0
    IMAGE_GEN_MODE: str = "real"  # real | mock | cached
    IMAGE_GEN_CACHE_DIR: str = ""
    MAX_REAL_IMAGES_PER_RUN: int = 0  # 0 means unlimited
    IMAGE_API_MAX_CONCURRENCY: int = 1
    IMAGE_GPT_QUALITY: str = "high"  # low | medium | high | auto
    IMAGE_PROVIDER_GATEWAY_CUTOFF_SECONDS: int = 120
    IMAGE_GATEWAY_CUTOFF_MAX_ATTEMPTS: int = 1
    IMAGE_API_SLOT_WAIT_TIMEOUT_SECONDS: int = 600
    IMAGE_GENERATION_TASK_PAGE_CHUNK_SIZE: int = 8
    IMAGE_ASPECT_RATIO_TOLERANCE: float = 0.04
    IMAGE_ASPECT_RATIO_MAX_RETRIES: int = 1
    # 0 means keep source dimensions unless upload-size fallback is required.
    IMAGE_REFERENCE_MAX_SIDE: int = 0
    IMAGE_REFERENCE_JPEG_QUALITY: int = 85
    IMAGE_REFERENCE_UPLOAD_TARGET_MB: float = 20.0
    IMAGE_REFERENCE_MAX_FILE_MB: float = 8.0
    IMAGE_MAX_REFERENCE_INPUTS: int = 14
    IMAGE_USE_SEED_REFERENCE_IMAGES: bool = True
    IMAGE_SEED_REFERENCE_MAX_SIDE: int = 1280
    IMAGE_SEED_REFERENCE_JPEG_QUALITY: int = 78
    IMAGE_SEED_REFERENCE_PNG_THRESHOLD_KB: int = 128
    IMAGE_EDIT_CONNECT_TIMEOUT_SECONDS: int = 120
    IMAGE_EDIT_READ_TIMEOUT_SECONDS: int = 1800
    GENERATION_PENDING_TIMEOUT_SECONDS: int = 120
    CELERY_QUEUE_WAIT_TIMEOUT_SECONDS: int = 3600
    AUTO_START_CELERY_WORKER: bool = True
    CELERY_WORKER_STARTUP_TIMEOUT_SECONDS: int = 8
    RUN_HEARTBEAT_TIMEOUT_SECONDS: int = 300
    CONTENT_PLAN_HEARTBEAT_TIMEOUT_SECONDS: int = 1800
    CONTENT_PLAN_SOURCE_CONTEXT_TOKEN_BUDGET: int = 500_000
    CONTENT_PLAN_PAGE_MAP_DOCUMENT_CHAR_LIMIT: int = 180_000
    CONTENT_PLAN_PAGE_MAP_SOURCE_DRAFT_CHAR_LIMIT: int = 90_000
    CELERY_TASK_SOFT_TIME_LIMIT: int = 1800
    CELERY_TASK_TIME_LIMIT: int = 2100
    PPTX_ASSEMBLY_LOCK_TTL_SECONDS: int = 300
    EDITABLE_PPTX_OCR_TIMEOUT_SECONDS: float = 90.0
    EDITABLE_PPTX_OCR_RETRY_COUNT: int = 2
    EDITABLE_PPTX_OCR_RETRY_BACKOFF_SECONDS: float = 1.5
    EDITABLE_PPTX_MAX_VISUAL_ASSETS_PER_SLIDE: int = 6
    EDITABLE_PPTX_QA_MAX_WORKERS: int = 4
    EDITABLE_PPTX_MIN_TEXT_BOXES: int = 1
    EDITABLE_PPTX_WARNING_PAGE_RATIO: float = 0.65
    EDITABLE_PPTX_FAIL_ON_WARNING: bool = False
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:5174,http://localhost:5175,http://localhost:8000,http://127.0.0.1:8000,http://127.0.0.1:5173,http://127.0.0.1:5174,http://127.0.0.1:5175"

    OUTPUT_DIR: str = ""
    UPLOAD_DIR: str = ""

    @model_validator(mode="after")
    def apply_runtime_data_defaults(self):
        data_root = _resolve_project_path(self.RUNTIME_DATA_DIR or _default_runtime_data_dir())
        self.RUNTIME_DATA_DIR = data_root
        if not self.UPLOAD_DIR:
            self.UPLOAD_DIR = str(Path(data_root) / "uploads")
        else:
            self.UPLOAD_DIR = _resolve_project_path(self.UPLOAD_DIR)
        if not self.OUTPUT_DIR:
            self.OUTPUT_DIR = str(Path(data_root) / "outputs")
        else:
            self.OUTPUT_DIR = _resolve_project_path(self.OUTPUT_DIR)
        if not self.IMAGE_GEN_CACHE_DIR:
            self.IMAGE_GEN_CACHE_DIR = str(Path(self.OUTPUT_DIR) / "image-cache")
        else:
            self.IMAGE_GEN_CACHE_DIR = _resolve_project_path(self.IMAGE_GEN_CACHE_DIR)
        if not self.DATABASE_URL:
            self.DATABASE_URL = f"sqlite:///{Path(data_root) / 'db' / 'pptgod.db'}"
        return self

    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(__file__), "..", "..", ".env"),
    )


settings = Settings()
