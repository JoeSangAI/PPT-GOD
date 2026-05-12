import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "PPT GOD"
    VERSION: str = "0.1.0"

    DATABASE_URL: str = "sqlite:///./pptgod.db"
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_SOCKET_TIMEOUT_SECONDS: float = 3.0
    CELERY_BROKER_CONNECTION_TIMEOUT_SECONDS: float = 3.0

    MINIMAX_API_KEY: str = ""
    MINIMAX_API_BASE: str = "https://api.minimaxi.com/v1"
    MINIMAX_LLM_MODEL: str = "MiniMax-M2.7"
    DEER_API_KEY: str = ""
    DEER_API_BASE: str = "https://api.deerapi.com/v1"
    DEER_IMAGE_MODEL: str = "gpt-image-2-all"
    IMAGE_API_TIMEOUT_SECONDS: float = 125.0
    IMAGE_GEN_MODE: str = "real"  # real | mock | cached
    IMAGE_GEN_CACHE_DIR: str = "./outputs/image-cache"
    MAX_REAL_IMAGES_PER_RUN: int = 0  # 0 means unlimited
    IMAGE_API_MAX_CONCURRENCY: int = 1
    IMAGE_GPT_QUALITY: str = "high"  # low | medium | high | auto
    IMAGE_PROVIDER_GATEWAY_CUTOFF_SECONDS: int = 120
    IMAGE_GATEWAY_CUTOFF_MAX_ATTEMPTS: int = 1
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
    AUTO_START_CELERY_WORKER: bool = True
    CELERY_WORKER_STARTUP_TIMEOUT_SECONDS: int = 8
    RUN_HEARTBEAT_TIMEOUT_SECONDS: int = 300
    CELERY_TASK_SOFT_TIME_LIMIT: int = 1800
    CELERY_TASK_TIME_LIMIT: int = 2100
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:5174,http://localhost:5175,http://localhost:8000,http://127.0.0.1:8000,http://127.0.0.1:5173,http://127.0.0.1:5174,http://127.0.0.1:5175"

    OUTPUT_DIR: str = "./outputs"
    UPLOAD_DIR: str = "./uploads"

    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(__file__), "..", "..", ".env"),
    )


settings = Settings()
