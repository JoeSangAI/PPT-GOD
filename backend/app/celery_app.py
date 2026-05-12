from celery import Celery
from app.core.config import settings

redis_url = settings.REDIS_URL or "redis://localhost:6379/0"

celery_app = Celery(
    "ppt_god",
    broker=redis_url,
    backend=redis_url,
    include=["app.tasks"],
)

celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    result_expires=3600,
    broker_connection_timeout=settings.CELERY_BROKER_CONNECTION_TIMEOUT_SECONDS,
    broker_transport_options={
        "socket_connect_timeout": settings.REDIS_SOCKET_TIMEOUT_SECONDS,
        "socket_timeout": settings.REDIS_SOCKET_TIMEOUT_SECONDS,
        "retry_on_timeout": False,
        "visibility_timeout": 3600,
    },
    result_backend_transport_options={
        "socket_connect_timeout": settings.REDIS_SOCKET_TIMEOUT_SECONDS,
        "socket_timeout": settings.REDIS_SOCKET_TIMEOUT_SECONDS,
        "retry_on_timeout": False,
    },
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_soft_time_limit=settings.CELERY_TASK_SOFT_TIME_LIMIT,
    task_time_limit=settings.CELERY_TASK_TIME_LIMIT,
)
