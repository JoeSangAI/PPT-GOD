import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "PPT GOD"
    VERSION: str = "0.1.0"

    DATABASE_URL: str = "sqlite:///./pptgod.db"
    REDIS_URL: str = "redis://localhost:6379/0"

    MINIMAX_API_KEY: str = ""
    MINIMAX_API_BASE: str = "https://api.minimaxi.com/v1"
    MINIMAX_LLM_MODEL: str = "MiniMax-M2.7"
    MINIMAX_VISION_MODEL: str = "abab6.5s-chat"
    DEER_API_KEY: str = ""
    DEER_API_BASE: str = "https://api.deerapi.com/v1"
    DEER_IMAGE_MODEL: str = "gpt-image-2-all"
    IMAGE_GEN_MODE: str = "real"  # real | mock | cached
    IMAGE_GEN_CACHE_DIR: str = "./outputs/image-cache"
    MAX_REAL_IMAGES_PER_RUN: int = 0  # 0 means unlimited
    GENERATION_PENDING_TIMEOUT_SECONDS: int = 120
    CELERY_TASK_SOFT_TIME_LIMIT: int = 1800
    CELERY_TASK_TIME_LIMIT: int = 2100

    OUTPUT_DIR: str = "./outputs"
    UPLOAD_DIR: str = "./uploads"

    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(__file__), "..", "..", ".env"),
    )


settings = Settings()