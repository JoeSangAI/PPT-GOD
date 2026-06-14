from celery import Celery
from kombu import Exchange, Queue

from app.core.config import settings

redis_url = settings.REDIS_URL or "redis://localhost:6379/0"
text_queue = settings.CELERY_TEXT_QUEUE or "text"
image_queue = settings.CELERY_IMAGE_QUEUE or "image"
text_exchange = Exchange(text_queue, type="direct")
image_exchange = Exchange(image_queue, type="direct")

celery_app = Celery(
    "ppt_god",
    broker=redis_url,
    backend=redis_url,
    include=["app.tasks"],
)

celery_app.conf.update(
    task_default_queue=text_queue,
    task_default_exchange=text_queue,
    task_default_routing_key=text_queue,
    task_queues=(
        Queue(text_queue, text_exchange, routing_key=text_queue),
        Queue(image_queue, image_exchange, routing_key=image_queue),
    ),
    task_routes={
        "app.tasks.generate_style_proposals_task": {
            "queue": text_queue,
            "exchange": text_queue,
            "routing_key": text_queue,
        },
        "app.tasks.generate_slides_task": {
            "queue": image_queue,
            "exchange": image_queue,
            "routing_key": image_queue,
        },
    },
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
