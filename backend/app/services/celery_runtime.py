from __future__ import annotations

import logging
import os
import subprocess
import sys
import time

from app.celery_app import celery_app
from app.core.config import settings


logger = logging.getLogger(__name__)

_worker_process: subprocess.Popen | None = None


def has_celery_worker(timeout: float = 0.7) -> bool:
    try:
        return bool(celery_app.control.ping(timeout=timeout))
    except Exception as exc:
        logger.warning("Celery worker ping failed: %s", exc)
        return False


def ensure_celery_worker() -> bool:
    """Ensure Celery work will be consumed before creating a queued run.

    In local single-process development, it is easy to start FastAPI without the
    worker. When that happens, jobs sit in Redis forever. We either detect an
    existing worker, start one for the local process, or report that dispatch is
    unavailable before the run is created.
    """
    global _worker_process

    if has_celery_worker():
        return True

    if not settings.AUTO_START_CELERY_WORKER:
        return False

    if _worker_process and _worker_process.poll() is None:
        return _wait_for_worker()

    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    log_path = os.path.join("/tmp", "pptgod-celery-worker.log")
    pid_path = os.path.join("/tmp", "pptgod-celery.pid")
    cmd = [
        sys.executable,
        "-m",
        "celery",
        "-A",
        "app.celery_app",
        "worker",
        "-l",
        "info",
        "--concurrency=1",
        f"--pidfile={pid_path}",
        f"--logfile={log_path}",
    ]
    try:
        _worker_process = subprocess.Popen(
            cmd,
            cwd=backend_dir,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        logger.error("Failed to start Celery worker: %s", exc)
        return False

    return _wait_for_worker()


def _wait_for_worker() -> bool:
    timeout = max(1, int(settings.CELERY_WORKER_STARTUP_TIMEOUT_SECONDS or 8))
    deadline = time.time() + timeout
    while time.time() < deadline:
        if has_celery_worker(timeout=0.5):
            return True
        time.sleep(0.4)
    logger.error("Celery worker did not become ready within %s seconds", timeout)
    return False
