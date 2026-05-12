import base64
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import logging
import os
import threading
import time
import uuid
from typing import List, Optional

import httpx
import redis
import requests
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
)
from PIL import Image, ImageOps

from app.core.config import settings
from app.core.provider_credentials import get_deer_image_model, get_provider_credentials

logger = logging.getLogger(__name__)

_image_client = None
_image_client_lock = threading.Lock()
_image_redis_client = None
_image_redis_lock = threading.Lock()
_run_state_lock = threading.Lock()
_image_call_events = threading.local()
_image_queue_wait = threading.local()
_image_api_semaphore = threading.BoundedSemaphore(
    max(1, int(settings.IMAGE_API_MAX_CONCURRENCY or 1))
)
_real_image_calls_this_run = 0
_REFERENCE_UPLOAD_CACHE_MAX_ITEMS = 128
_reference_upload_cache_lock = threading.Lock()
_reference_upload_cache: OrderedDict[str, tuple[str, bytes, str, int]] = OrderedDict()
_reference_upload_inflight: dict[str, threading.Event] = {}


class ReferenceUploadTimeoutError(RuntimeError):
    """Raised when an image-edit request is interrupted and should not be retried."""


class ProviderGatewayCutoffError(RuntimeError):
    """Raised when the upstream/proxy cuts off a long-running image request."""


class ImageAspectRatioMismatchError(RuntimeError):
    """Raised when a generated slide image is clearly outside the requested shape."""


@dataclass(frozen=True)
class _ReferenceUploadProfile:
    max_side: int | None
    jpeg_quality: int
    png_threshold_bytes: int
    label: str


def _get_image_client() -> OpenAI:
    global _image_client
    credentials = get_provider_credentials()
    if credentials.deer_api_key != (settings.DEER_API_KEY or settings.MINIMAX_API_KEY) or credentials.deer_api_base != settings.DEER_API_BASE.rstrip("/"):
        request_timeout = max(30.0, float(settings.IMAGE_API_TIMEOUT_SECONDS or 125.0))
        timeout = httpx.Timeout(request_timeout, connect=min(30.0, request_timeout))
        return OpenAI(
            api_key=credentials.deer_api_key,
            base_url=credentials.deer_api_base,
            timeout=timeout,
            max_retries=0,
        )
    if _image_client is None:
        with _image_client_lock:
            if _image_client is None:
                request_timeout = max(30.0, float(settings.IMAGE_API_TIMEOUT_SECONDS or 125.0))
                timeout = httpx.Timeout(request_timeout, connect=min(30.0, request_timeout))
                _image_client = OpenAI(
                    api_key=settings.DEER_API_KEY or settings.MINIMAX_API_KEY,
                    base_url=settings.DEER_API_BASE,
                    timeout=timeout,
                    max_retries=0,  # 禁用 SDK 自动重试，由本模块手动控制，防止重复计费
                )
    return _image_client


def reset_image_call_events() -> None:
    _image_call_events.events = []


def get_image_call_events() -> list[dict]:
    return list(getattr(_image_call_events, "events", []) or [])


def _clear_reference_upload_cache() -> None:
    with _reference_upload_cache_lock:
        _reference_upload_cache.clear()
        _reference_upload_inflight.clear()


def _utc_iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or time.time(), tz=timezone.utc).isoformat()


def _response_request_id(resp) -> str | None:
    request_id = getattr(resp, "_request_id", None)
    if request_id:
        return str(request_id)
    headers = getattr(resp, "headers", None)
    if headers:
        for name in ("x-request-id", "request-id", "x-deer-request-id", "x-deerapi-request-id"):
            value = headers.get(name) or headers.get(name.title())
            if value:
                return str(value)
    return None


def _exception_debug_summary(exc: Exception) -> str:
    parts = [exc.__class__.__name__]
    message = str(exc)
    if message:
        parts.append(message)
    cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen and len(parts) < 8:
        seen.add(id(cause))
        cause_message = str(cause)
        parts.append(
            f"caused_by={cause.__class__.__name__}: {cause_message}"
            if cause_message
            else f"caused_by={cause.__class__.__name__}"
        )
        cause = getattr(cause, "__cause__", None) or getattr(cause, "__context__", None)
    return " | ".join(parts)[:1000]


def _configured_image_api_limit() -> int:
    try:
        return max(1, min(4, int(settings.IMAGE_API_MAX_CONCURRENCY or 1)))
    except (TypeError, ValueError):
        return 1


def _gateway_cutoff_seconds() -> int:
    try:
        return max(30, int(settings.IMAGE_PROVIDER_GATEWAY_CUTOFF_SECONDS or 120))
    except (TypeError, ValueError):
        return 120


def _gateway_cutoff_max_attempts() -> int:
    try:
        return max(1, min(3, int(settings.IMAGE_GATEWAY_CUTOFF_MAX_ATTEMPTS or 1)))
    except (TypeError, ValueError):
        return 1


def _image_quality() -> str:
    quality = str(settings.IMAGE_GPT_QUALITY or "high").strip().lower()
    return quality if quality in {"low", "medium", "high", "auto"} else "high"


def _get_image_redis_client():
    global _image_redis_client
    if _image_redis_client is not None:
        return _image_redis_client
    with _image_redis_lock:
        if _image_redis_client is None:
            try:
                _image_redis_client = redis.from_url(
                    settings.REDIS_URL or "redis://localhost:6379/0",
                    socket_connect_timeout=settings.REDIS_SOCKET_TIMEOUT_SECONDS,
                    socket_timeout=settings.REDIS_SOCKET_TIMEOUT_SECONDS,
                    retry_on_timeout=False,
                    health_check_interval=30,
                )
                _image_redis_client.ping()
            except Exception as exc:
                logger.warning("ImageGen: Redis image queue unavailable, using local limiter only: %s", exc)
                _image_redis_client = False
    return _image_redis_client or None


def _image_lock_ttl_seconds() -> int:
    try:
        task_limit = int(settings.CELERY_TASK_TIME_LIMIT or 2100)
    except (TypeError, ValueError):
        task_limit = 2100
    return max(300, task_limit + 300)


def _release_redis_slot(client, key: str, token: str) -> None:
    try:
        client.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end",
            1,
            key,
            token,
        )
    except Exception as exc:
        logger.warning("ImageGen: failed to release Redis image slot %s: %s", key, exc)


def _start_redis_slot_renewer(client, key: str, token: str, ttl_seconds: int):
    stop = threading.Event()
    interval = max(10, min(60, ttl_seconds // 3))

    def renew() -> None:
        while not stop.wait(interval):
            try:
                client.eval(
                    "if redis.call('get', KEYS[1]) == ARGV[1] then "
                    "return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end",
                    1,
                    key,
                    token,
                    ttl_seconds,
                )
            except Exception as exc:
                logger.warning("ImageGen: failed to renew Redis image slot %s: %s", key, exc)

    thread = threading.Thread(target=renew, name="pptgod-image-slot-renewer", daemon=True)
    thread.start()
    return stop


def _acquire_redis_image_slot():
    client = _get_image_redis_client()
    if client is None:
        return None

    limit = _configured_image_api_limit()
    ttl = _image_lock_ttl_seconds()
    token = str(uuid.uuid4())
    logged_wait = False
    started = time.time()

    while True:
        for index in range(limit):
            key = f"pptgod:image_api:slot:{index}"
            try:
                if client.set(key, token, nx=True, ex=ttl):
                    if logged_wait:
                        logger.info("ImageGen: acquired image API slot after %.1fs", time.time() - started)
                    return client, key, token, _start_redis_slot_renewer(client, key, token, ttl)
            except Exception as exc:
                logger.warning("ImageGen: Redis image queue failed, using local limiter only: %s", exc)
                return None
        waited = time.time() - started
        if waited >= 30 and not logged_wait:
            logger.info("ImageGen: waiting for image API slot (%.1fs)", waited)
            logged_wait = True
        time.sleep(1.0)


class _ImageApiSlot:
    def __enter__(self):
        self.started = time.time()
        self.redis_slot = None
        self.local_slot = _image_api_semaphore
        self.local_slot.acquire()
        try:
            self.redis_slot = _acquire_redis_image_slot()
            _image_queue_wait.seconds = time.time() - self.started
            return self
        except Exception:
            self.local_slot.release()
            raise

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.redis_slot:
                client, key, token, stop = self.redis_slot
                stop.set()
                _release_redis_slot(client, key, token)
        finally:
            _image_queue_wait.seconds = None
            self.local_slot.release()


def _image_api_slot() -> _ImageApiSlot:
    return _ImageApiSlot()


def _current_image_queue_wait_seconds() -> float | None:
    value = getattr(_image_queue_wait, "seconds", None)
    if value is None:
        return None
    return round(float(value), 3)


def _is_connection_like_error(exc: Exception) -> bool:
    if isinstance(exc, (APIConnectionError, APITimeoutError, requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return True
    text = _exception_debug_summary(exc).lower()
    return any(
        marker in text
        for marker in (
            "connection error",
            "connection aborted",
            "connection reset",
            "timed out",
            "timeout",
            "read operation timed out",
            "remote end closed",
        )
    )


def _is_gateway_idle_cutoff(exc: Exception, duration_seconds: float) -> bool:
    timeout = _gateway_cutoff_seconds()
    return (
        _is_connection_like_error(exc)
        and duration_seconds >= max(30, timeout - 10)
        and duration_seconds <= timeout + 45
    )


def _record_image_call_event(**event) -> None:
    events = getattr(_image_call_events, "events", None)
    if events is None:
        events = []
        _image_call_events.events = events
    clean = {k: v for k, v in event.items() if v is not None}
    events.append(clean)


def _reserve_real_image_call() -> None:
    global _real_image_calls_this_run
    limit = int(settings.MAX_REAL_IMAGES_PER_RUN or 0)
    if limit <= 0:
        return
    with _run_state_lock:
        if _real_image_calls_this_run >= limit:
            raise RuntimeError(
                f"Real image generation limit exceeded: "
                f"MAX_REAL_IMAGES_PER_RUN={limit}"
            )
        _real_image_calls_this_run += 1


def _make_mock_slide_image(prompt: str) -> Image.Image:
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    bg = tuple(int(digest[i:i + 2], 16) for i in (0, 2, 4))
    accent = tuple(255 - c for c in bg)
    img = Image.new("RGB", (1792, 1008), bg)
    # Simple deterministic bands make mock images visually distinct in UI tests.
    for i in range(0, 1792, 128):
        color = accent if (i // 128) % 2 == 0 else bg
        for x in range(i, min(i + 48, 1792)):
            for y in range(0, 1008, 6):
                img.putpixel((x, y), color)
    return img


def _cache_key(
    prompt: str,
    reference_images: Optional[List[Image.Image]],
    resolution: str,
    aspect_ratio: str,
) -> str:
    h = hashlib.sha256()
    h.update(get_deer_image_model().encode("utf-8"))
    h.update(_image_quality().encode("utf-8"))
    h.update(resolution.encode("utf-8"))
    h.update(aspect_ratio.encode("utf-8"))
    h.update(prompt.encode("utf-8"))
    for ref in reference_images or []:
        h.update(str(ref.size).encode("utf-8"))
        h.update(str(ref.mode).encode("utf-8"))
        h.update(ref.tobytes())
    return h.hexdigest()


def _cache_path(key: str) -> str:
    return os.path.join(settings.IMAGE_GEN_CACHE_DIR, f"{key}.png")


def _is_api_retryable(exc: Exception) -> bool:
    """
    只在同一接口、同一 Idempotency-Key 下重试瞬时失败。
    不切换模型，不改成无参考图生成，也不返回占位图。
    """
    if isinstance(exc, (ReferenceUploadTimeoutError, ProviderGatewayCutoffError)):
        return False
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return True
    if isinstance(exc, APIStatusError):
        status = exc.status_code
        if status in {408, 409, 425, 429} or 500 <= status < 600:
            return True
    if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return True
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        if status in {408, 409, 425, 429} or 500 <= status < 600:
            return True
    text = str(exc).lower()
    retryable_markers = (
        "rate limit",
        "too many requests",
        "connection error",
        "connection aborted",
        "connection reset",
        "timed out",
        "timeout",
        "read operation timed out",
        "remote end closed",
        "bad gateway",
        "service unavailable",
        "gateway timeout",
        "temporarily unavailable",
    )
    if any(marker in text for marker in retryable_markers):
        return True
    return False


def _target_aspect_ratio(aspect_ratio: str) -> float | None:
    try:
        width_text, height_text = str(aspect_ratio or "").split(":", 1)
        width = float(width_text)
        height = float(height_text)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return width / height


def _aspect_ratio_tolerance() -> float:
    return max(0.0, float(settings.IMAGE_ASPECT_RATIO_TOLERANCE or 0.0))


def _validate_generated_image_aspect_ratio(img: Image.Image, aspect_ratio: str) -> None:
    target = _target_aspect_ratio(aspect_ratio)
    if target is None:
        return
    width, height = img.size
    if width <= 0 or height <= 0:
        raise ImageAspectRatioMismatchError("Image API returned an empty image")
    actual = width / height
    tolerance = _aspect_ratio_tolerance()
    relative_delta = abs(actual - target) / target
    if relative_delta <= tolerance:
        return
    raise ImageAspectRatioMismatchError(
        f"Image API returned {width}x{height} (aspect {actual:.3f}); "
        f"expected {aspect_ratio} (aspect {target:.3f}) within {tolerance:.0%}"
    )


def _retry_after_seconds(exc: Exception, fallback: int) -> int:
    headers = None
    response = getattr(exc, "response", None)
    if response is not None:
        headers = getattr(response, "headers", None)
    retry_after = None
    if headers:
        retry_after = headers.get("retry-after") or headers.get("Retry-After")
    if retry_after:
        try:
            return max(0, min(90, int(float(retry_after))))
        except (TypeError, ValueError):
            pass
    return fallback


def _download_image_bytes(url: str, max_attempts: int = 3) -> bytes:
    for attempt in range(max_attempts):
        try:
            logger.info(f"ImageGen: downloading URL (attempt {attempt + 1}/{max_attempts})")
            resp = requests.get(url, timeout=min(300, max(30.0, float(settings.IMAGE_API_TIMEOUT_SECONDS or 125.0))))
            resp.raise_for_status()
            return resp.content
        except requests.exceptions.RequestException as e:
            logger.warning(f"ImageGen: download failed (attempt {attempt + 1}/{max_attempts}): {e}")
            if attempt == max_attempts - 1:
                raise Exception(f"Image download failed, URL may have expired: {e}")
            sleep_time = 5 * (2 ** attempt)
            logger.info(f"ImageGen: retrying download in {sleep_time}s...")
            time.sleep(sleep_time)
    raise Exception("Image download failed after all retries")


def _base_reference_upload_profile() -> _ReferenceUploadProfile:
    configured_max_side = int(settings.IMAGE_REFERENCE_MAX_SIDE or 0)
    jpeg_quality = max(60, min(95, int(settings.IMAGE_REFERENCE_JPEG_QUALITY or 85)))
    return _ReferenceUploadProfile(
        max_side=configured_max_side if configured_max_side > 0 else None,
        jpeg_quality=jpeg_quality,
        png_threshold_bytes=512 * 1024,
        label="source" if configured_max_side <= 0 else f"max{configured_max_side}",
    )


def _reference_upload_profiles() -> List[_ReferenceUploadProfile]:
    base = _base_reference_upload_profile()
    profiles = [base]
    seen = {base.max_side}
    fallback_specs = [
        (2200, max(82, min(base.jpeg_quality, 88)), 512 * 1024, "fallback2200"),
        (1800, max(80, min(base.jpeg_quality, 86)), 384 * 1024, "fallback1800"),
        (1440, max(78, min(base.jpeg_quality, 84)), 256 * 1024, "fallback1440"),
        (1280, max(76, min(base.jpeg_quality, 82)), 192 * 1024, "fallback1280"),
    ]
    for max_side, quality, png_threshold, label in fallback_specs:
        if base.max_side is not None and max_side >= base.max_side:
            continue
        if max_side in seen:
            continue
        profiles.append(_ReferenceUploadProfile(max_side, quality, png_threshold, label))
        seen.add(max_side)
    return profiles


def _reference_upload_budget_bytes() -> tuple[int, int]:
    target_mb = max(1.0, float(settings.IMAGE_REFERENCE_UPLOAD_TARGET_MB or 20.0))
    max_file_mb = max(1.0, float(settings.IMAGE_REFERENCE_MAX_FILE_MB or 8.0))
    return int(target_mb * 1024 * 1024), int(max_file_mb * 1024 * 1024)


def _prepare_reference_image_for_upload(
    ref: Image.Image,
    max_side: int | None = None,
) -> Image.Image:
    """Normalize user/template references before sending them to the image API."""
    img = ImageOps.exif_transpose(ref)
    img = img.copy()
    if max_side and max_side > 0 and max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    if img.mode in ("RGBA", "LA"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        alpha = img.getchannel("A")
        background.paste(img.convert("RGBA"), mask=alpha)
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")
    return img


def _effective_reference_upload_settings(
    ref: Image.Image,
    upload_profile: _ReferenceUploadProfile,
) -> tuple[int | None, int, int, str]:
    role = str((getattr(ref, "info", {}) or {}).get("pptgod_reference_role") or "")
    role_max_side = None
    jpeg_quality = upload_profile.jpeg_quality
    png_threshold_bytes = upload_profile.png_threshold_bytes
    if role == "seed_ref":
        configured_seed_max = int(settings.IMAGE_SEED_REFERENCE_MAX_SIDE or 0)
        if configured_seed_max > 0:
            role_max_side = configured_seed_max
        jpeg_quality = max(60, min(92, int(settings.IMAGE_SEED_REFERENCE_JPEG_QUALITY or jpeg_quality)))
        seed_png_threshold_kb = max(16, int(settings.IMAGE_SEED_REFERENCE_PNG_THRESHOLD_KB or 128))
        png_threshold_bytes = seed_png_threshold_kb * 1024

    max_side = upload_profile.max_side
    if role_max_side:
        max_side = min(max_side, role_max_side) if max_side else role_max_side
    return max_side, jpeg_quality, png_threshold_bytes, role


def _reference_source_fingerprint(ref: Image.Image) -> str:
    info = getattr(ref, "info", {}) or {}
    source_path = str(info.get("pptgod_reference_source_path") or "")
    source_mtime = info.get("pptgod_reference_source_mtime_ns")
    source_size = info.get("pptgod_reference_source_size")
    if source_path and source_mtime is not None and source_size is not None:
        return f"path:{source_path}:{source_mtime}:{source_size}"

    h = hashlib.sha256()
    h.update(str(ref.size).encode("utf-8"))
    h.update(str(ref.mode).encode("utf-8"))
    h.update(ref.tobytes())
    return f"bytes:{h.hexdigest()}"


def _reference_upload_cache_key(
    ref: Image.Image,
    upload_profile: _ReferenceUploadProfile,
) -> str:
    max_side, jpeg_quality, png_threshold_bytes, role = _effective_reference_upload_settings(ref, upload_profile)
    h = hashlib.sha256()
    h.update(_reference_source_fingerprint(ref).encode("utf-8"))
    h.update(str(role).encode("utf-8"))
    h.update(str(max_side).encode("utf-8"))
    h.update(str(jpeg_quality).encode("utf-8"))
    h.update(str(png_threshold_bytes).encode("utf-8"))
    return h.hexdigest()


def _store_cached_reference_upload(
    key: str,
    extension: str,
    payload: bytes,
    mime_type: str,
    size_bytes: int,
) -> None:
    with _reference_upload_cache_lock:
        _reference_upload_cache[key] = (extension, payload, mime_type, size_bytes)
        _reference_upload_cache.move_to_end(key)
        while len(_reference_upload_cache) > _REFERENCE_UPLOAD_CACHE_MAX_ITEMS:
            _reference_upload_cache.popitem(last=False)


def _claim_reference_upload_encoding(key: str, index: int) -> tuple[tuple[str, io.BytesIO, str, int] | None, threading.Event | None]:
    while True:
        with _reference_upload_cache_lock:
            cached = _reference_upload_cache.get(key)
            if cached:
                _reference_upload_cache.move_to_end(key)
                extension, payload, mime_type, size_bytes = cached
                return (f"ref_{index}.{extension}", io.BytesIO(payload), mime_type, size_bytes), None
            in_flight = _reference_upload_inflight.get(key)
            if in_flight is None:
                in_flight = threading.Event()
                _reference_upload_inflight[key] = in_flight
                return None, in_flight

        in_flight.wait()


def _release_reference_upload_encoding(key: str, event: threading.Event | None) -> None:
    if event is None:
        return
    with _reference_upload_cache_lock:
        current = _reference_upload_inflight.get(key)
        if current is event:
            _reference_upload_inflight.pop(key, None)
        event.set()


def _reference_upload_file(
    ref: Image.Image,
    index: int,
    profile: _ReferenceUploadProfile | None = None,
) -> tuple[str, io.BytesIO, str, int]:
    upload_profile = profile or _base_reference_upload_profile()
    cache_key = _reference_upload_cache_key(ref, upload_profile)
    cached, in_flight = _claim_reference_upload_encoding(cache_key, index)
    if cached:
        return cached

    try:
        max_side, jpeg_quality, png_threshold_bytes, _role = _effective_reference_upload_settings(ref, upload_profile)
        img = _prepare_reference_image_for_upload(ref, max_side=max_side)
        buf = io.BytesIO()
        png_buf = io.BytesIO()
        img.save(png_buf, format="PNG", optimize=True)
        png_size = png_buf.tell()

        # PPT-extracted references are often full-slide photos or screenshots. Sending
        # several lossless PNGs can dominate request time and make the image API upload
        # fail before generation starts. Prefer a high-quality JPEG for normal RGB
        # references, while keeping tiny PNGs lossless for marks/screenshots where PNG is
        # already compact.
        if png_size <= png_threshold_bytes:
            payload = png_buf.getvalue()
            filename = f"ref_{index}.png"
            mime_type = "image/png"
            size_bytes = len(payload)
            _store_cached_reference_upload(cache_key, "png", payload, mime_type, size_bytes)
            return filename, io.BytesIO(payload), mime_type, size_bytes

        img.save(
            buf,
            format="JPEG",
            quality=jpeg_quality,
            optimize=True,
            progressive=True,
        )
        payload = buf.getvalue()
        filename = f"ref_{index}.jpg"
        mime_type = "image/jpeg"
        size_bytes = len(payload)
        _store_cached_reference_upload(cache_key, "jpg", payload, mime_type, size_bytes)
        return filename, io.BytesIO(payload), mime_type, size_bytes
    finally:
        _release_reference_upload_encoding(cache_key, in_flight)


def _build_reference_upload_files(
    reference_images: List[Image.Image],
) -> tuple[list[tuple[str, tuple[str, io.BytesIO, str]]], int, _ReferenceUploadProfile]:
    target_bytes, max_file_bytes = _reference_upload_budget_bytes()
    last_files: list[tuple[str, tuple[str, io.BytesIO, str]]] = []
    last_upload_bytes = 0
    last_profile = _base_reference_upload_profile()

    for profile in _reference_upload_profiles():
        files: list[tuple[str, tuple[str, io.BytesIO, str]]] = []
        upload_bytes = 0
        largest_file = 0
        for i, ref in enumerate(reference_images):
            filename, buf, mime_type, size_bytes = _reference_upload_file(ref, i, profile)
            upload_bytes += size_bytes
            largest_file = max(largest_file, size_bytes)
            field_name = "image" if i == 0 else "additional_images[]"
            files.append((field_name, (filename, buf, mime_type)))

        last_files = files
        last_upload_bytes = upload_bytes
        last_profile = profile
        if upload_bytes <= target_bytes and largest_file <= max_file_bytes:
            return files, upload_bytes, profile

    logger.warning(
        "ImageGen: reference upload still exceeds budget after fallback: upload=%.2fMB profile=%s",
        last_upload_bytes / (1024 * 1024),
        last_profile.label,
    )
    return last_files, last_upload_bytes, last_profile


def _is_connection_timeout(exc: Exception) -> bool:
    if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return True
    text = str(exc).lower()
    return "timed out" in text or "timeout" in text or "connection aborted" in text


def _call_gpt_image_2_generate(
    prompt: str, size: str = "1792x1024", idempotency_key: Optional[str] = None
) -> Image.Image:
    client = _get_image_client()
    headers = {}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    started_at = time.time()
    resp = None
    try:
        quality = _image_quality()
        resp = client.images.generate(
            model=get_deer_image_model(),
            prompt=prompt,
            size=size,
            quality=quality,
            n=1,
            extra_headers=headers or None,
        )
        image_data = resp.data[0]
        if image_data.b64_json:
            img_bytes = base64.b64decode(image_data.b64_json)
        elif image_data.url:
            img_bytes = _download_image_bytes(image_data.url)
        else:
            raise ValueError("DeerAPI returned no image content")
        duration_seconds = time.time() - started_at
        _record_image_call_event(
            endpoint="/v1/images/generations",
            model=get_deer_image_model(),
            status="succeeded",
            size=size,
            quality=quality,
            reference_count=0,
            queue_wait_seconds=_current_image_queue_wait_seconds(),
            idempotency_key=idempotency_key,
            request_id=_response_request_id(resp),
            started_at=_utc_iso(started_at),
            duration_seconds=round(duration_seconds, 3),
        )
        return Image.open(io.BytesIO(img_bytes))
    except Exception as exc:
        duration_seconds = time.time() - started_at
        gateway_cutoff = _is_gateway_idle_cutoff(exc, duration_seconds)
        setattr(exc, "pptgod_duration_seconds", duration_seconds)
        setattr(exc, "pptgod_gateway_idle_cutoff", gateway_cutoff)
        _record_image_call_event(
            endpoint="/v1/images/generations",
            model=get_deer_image_model(),
            status="gateway_timeout" if gateway_cutoff else "failed",
            size=size,
            quality=_image_quality(),
            reference_count=0,
            queue_wait_seconds=_current_image_queue_wait_seconds(),
            idempotency_key=idempotency_key,
            request_id=getattr(exc, "request_id", None) or _response_request_id(resp),
            started_at=_utc_iso(started_at),
            duration_seconds=round(duration_seconds, 3),
            error_type=exc.__class__.__name__,
            error=str(exc)[:500],
            error_debug=_exception_debug_summary(exc),
        )
        raise


def _call_gpt_image_2_edit(
    prompt: str, reference_images: List[Image.Image], size: str = "1792x1024",
    idempotency_key: Optional[str] = None
) -> Image.Image:
    """使用 requests 直接调用 DeerAPI images/edit，支持 additional_images[] 多图垫图。"""
    if not reference_images:
        raise ValueError("reference_images required for edit")
    prepare_started_at = time.time()
    files, upload_bytes, upload_profile = _build_reference_upload_files(reference_images)
    upload_prepare_seconds = round(time.time() - prepare_started_at, 3)
    data = {
        "model": get_deer_image_model(),
        "prompt": prompt,
        "size": size,
        "n": "1",
    }
    credentials = get_provider_credentials()
    headers = {"Authorization": f"Bearer {credentials.deer_api_key}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    logger.info(
        "ImageGen: calling edit API with %s reference images, upload=%.2fMB, profile=%s, prepare=%.3fs",
        len(reference_images),
        upload_bytes / (1024 * 1024),
        upload_profile.label,
        upload_prepare_seconds,
    )
    started_at = time.time()
    resp = None
    try:
        resp = requests.post(
            f"{credentials.deer_api_base}/images/edits",
            headers=headers,
            data=data,
            files=files,
            timeout=(
                int(settings.IMAGE_EDIT_CONNECT_TIMEOUT_SECONDS or 120),
                int(settings.IMAGE_EDIT_READ_TIMEOUT_SECONDS or 900),
            ),
        )
    except requests.exceptions.ConnectTimeout as e:
        duration_seconds = time.time() - started_at
        gateway_cutoff = _is_gateway_idle_cutoff(e, duration_seconds)
        setattr(e, "pptgod_duration_seconds", duration_seconds)
        setattr(e, "pptgod_gateway_idle_cutoff", gateway_cutoff)
        _record_image_call_event(
            endpoint="/v1/images/edits",
            model=get_deer_image_model(),
            status="gateway_timeout" if gateway_cutoff else "failed",
            size=size,
            reference_count=len(reference_images),
            queue_wait_seconds=_current_image_queue_wait_seconds(),
            upload_bytes=upload_bytes,
            upload_profile=upload_profile.label,
            upload_prepare_seconds=upload_prepare_seconds,
            idempotency_key=idempotency_key,
            started_at=_utc_iso(started_at),
            duration_seconds=round(duration_seconds, 3),
            error_type=e.__class__.__name__,
            error=str(e)[:500],
            error_debug=_exception_debug_summary(e),
        )
        raise ReferenceUploadTimeoutError(
            "图片接口连接超时：请求未稳定送达，已停止自动重试，避免重复消耗生图额度"
        ) from e
    except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
        duration_seconds = time.time() - started_at
        gateway_cutoff = _is_gateway_idle_cutoff(e, duration_seconds)
        setattr(e, "pptgod_duration_seconds", duration_seconds)
        setattr(e, "pptgod_gateway_idle_cutoff", gateway_cutoff)
        _record_image_call_event(
            endpoint="/v1/images/edits",
            model=get_deer_image_model(),
            status="gateway_timeout" if gateway_cutoff else "interrupted",
            size=size,
            reference_count=len(reference_images),
            queue_wait_seconds=_current_image_queue_wait_seconds(),
            upload_bytes=upload_bytes,
            upload_profile=upload_profile.label,
            upload_prepare_seconds=upload_prepare_seconds,
            idempotency_key=idempotency_key,
            started_at=_utc_iso(started_at),
            duration_seconds=round(duration_seconds, 3),
            error_type=e.__class__.__name__,
            error=str(e)[:500],
            error_debug=_exception_debug_summary(e),
        )
        raise ReferenceUploadTimeoutError(
            "图片接口响应中断：本次请求可能已进入生成并产生费用，已停止自动重试。请稍后重试失败页，避免重复扣费。"
        ) from e
    except requests.exceptions.RequestException as e:
        duration_seconds = time.time() - started_at
        gateway_cutoff = _is_gateway_idle_cutoff(e, duration_seconds)
        setattr(e, "pptgod_duration_seconds", duration_seconds)
        setattr(e, "pptgod_gateway_idle_cutoff", gateway_cutoff)
        _record_image_call_event(
            endpoint="/v1/images/edits",
            model=get_deer_image_model(),
            status="gateway_timeout" if gateway_cutoff else "failed",
            size=size,
            reference_count=len(reference_images),
            queue_wait_seconds=_current_image_queue_wait_seconds(),
            upload_bytes=upload_bytes,
            upload_profile=upload_profile.label,
            upload_prepare_seconds=upload_prepare_seconds,
            idempotency_key=idempotency_key,
            request_id=_response_request_id(getattr(e, "response", None)),
            started_at=_utc_iso(started_at),
            duration_seconds=round(duration_seconds, 3),
            error_type=e.__class__.__name__,
            error=str(e)[:500],
            error_debug=_exception_debug_summary(e),
        )
        raise
    try:
        resp.raise_for_status()
        body = resp.json()
        image_data = body["data"][0]
        if image_data.get("b64_json"):
            img_bytes = base64.b64decode(image_data["b64_json"])
        elif image_data.get("url"):
            img_bytes = _download_image_bytes(image_data["url"])
        else:
            raise ValueError("DeerAPI returned no image content")
        duration_seconds = time.time() - started_at
        _record_image_call_event(
            endpoint="/v1/images/edits",
            model=get_deer_image_model(),
            status="succeeded",
            size=size,
            reference_count=len(reference_images),
            queue_wait_seconds=_current_image_queue_wait_seconds(),
            upload_bytes=upload_bytes,
            upload_profile=upload_profile.label,
            upload_prepare_seconds=upload_prepare_seconds,
            idempotency_key=idempotency_key,
            request_id=_response_request_id(resp),
            started_at=_utc_iso(started_at),
            duration_seconds=round(duration_seconds, 3),
        )
        return Image.open(io.BytesIO(img_bytes))
    except Exception as exc:
        duration_seconds = time.time() - started_at
        gateway_cutoff = _is_gateway_idle_cutoff(exc, duration_seconds)
        setattr(exc, "pptgod_duration_seconds", duration_seconds)
        setattr(exc, "pptgod_gateway_idle_cutoff", gateway_cutoff)
        _record_image_call_event(
            endpoint="/v1/images/edits",
            model=get_deer_image_model(),
            status="gateway_timeout" if gateway_cutoff else "failed",
            size=size,
            reference_count=len(reference_images),
            queue_wait_seconds=_current_image_queue_wait_seconds(),
            upload_bytes=upload_bytes,
            upload_profile=upload_profile.label,
            upload_prepare_seconds=upload_prepare_seconds,
            idempotency_key=idempotency_key,
            request_id=getattr(exc, "request_id", None) or _response_request_id(resp),
            started_at=_utc_iso(started_at),
            duration_seconds=round(duration_seconds, 3),
            error_type=exc.__class__.__name__,
            error=str(exc)[:500],
            error_debug=_exception_debug_summary(exc),
        )
        raise


def _call_gemini_chat_generate(
    prompt: str,
    reference_images: Optional[List[Image.Image]] = None,
    aspect_ratio: str = "16:9",
    resolution: str = "4K",
) -> Image.Image:
    client = _get_image_client()
    messages = [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
    upload_profile = _base_reference_upload_profile()
    if len(reference_images or []) >= 8 and upload_profile.max_side is None:
        upload_profile = _ReferenceUploadProfile(
            max_side=2200,
            jpeg_quality=upload_profile.jpeg_quality,
            png_threshold_bytes=upload_profile.png_threshold_bytes,
            label="gemini-bulk2200",
        )
    for ref_img in (reference_images or [])[:14]:
        buffered = io.BytesIO()
        _prepare_reference_image_for_upload(ref_img, max_side=upload_profile.max_side).save(
            buffered,
            format="PNG",
            optimize=True,
        )
        img_b64 = base64.b64encode(buffered.getvalue()).decode()
        messages[0]["content"].append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{img_b64}"}
        })
    resp = client.chat.completions.create(
        model=get_deer_image_model(),
        messages=messages,
        extra_body={
            "image_config": {
                "aspect_ratio": aspect_ratio,
                "imageSize": resolution,
            }
        },
    )
    content = resp.choices[0].message.content or ""
    import re
    match = re.search(r'data:image/[a-zA-Z]+;base64,([a-zA-Z0-9+/=]+)', content)
    if not match:
        raise ValueError("DeerAPI returned unexpected image format")
    image_data = base64.b64decode(match.group(1))
    return Image.open(io.BytesIO(image_data))


def _generate_real_slide_image(
    prompt: str,
    reference_images: Optional[List[Image.Image]] = None,
    resolution: str = "4K",
    aspect_ratio: str = "16:9",
) -> Image.Image:
    model = get_deer_image_model().lower()
    # GPT-Image / DALL-E 支持的标准尺寸
    if aspect_ratio == "16:9":
        size = "1792x1024"
    elif aspect_ratio == "9:16":
        size = "1024x1792"
    else:
        size = "1024x1024"
    idempotency_key = str(uuid.uuid4())
    api_backoff = [0, 5, 15]
    aspect_ratio_retries = 0
    max_aspect_ratio_retries = max(0, int(settings.IMAGE_ASPECT_RATIO_MAX_RETRIES or 0))
    for attempt, delay in enumerate(api_backoff):
        if delay > 0:
            logger.info(f"ImageGen: waiting {delay}s before API call...")
            time.sleep(delay)
        try:
            if "gpt-image" in model or "dall-e" in model:
                if reference_images:
                    img = _call_gpt_image_2_edit(
                        prompt, reference_images, size=size,
                        idempotency_key=idempotency_key
                    )
                else:
                    img = _call_gpt_image_2_generate(
                        prompt, size=size,
                        idempotency_key=idempotency_key
                    )
            else:
                img = _call_gemini_chat_generate(
                    prompt, reference_images, aspect_ratio, resolution
                )
            _validate_generated_image_aspect_ratio(img, aspect_ratio)
            logger.info(f"ImageGen: success, model={get_deer_image_model()}, size={img.size}")
            return img
        except Exception as e:
            req_id = getattr(e, "request_id", None)
            err_detail = _exception_debug_summary(e)
            if req_id:
                err_detail = f"{err_detail} [request_id={req_id}]"
            logger.warning(
                f"ImageGen: API call failed (attempt {attempt + 1}/{len(api_backoff)}): {err_detail}"
            )
            if isinstance(e, ImageAspectRatioMismatchError):
                if aspect_ratio_retries >= max_aspect_ratio_retries or attempt == len(api_backoff) - 1:
                    logger.error(
                        "ImageGen: aspect ratio gate failed after %s retry attempt(s): %s",
                        aspect_ratio_retries,
                        err_detail,
                    )
                    raise
                aspect_ratio_retries += 1
                idempotency_key = str(uuid.uuid4())
                api_backoff[attempt + 1] = 0
                logger.warning(
                    "ImageGen: aspect ratio gate rejected result; retrying current slide once "
                    "with a fresh idempotency key"
                )
                continue
            if attempt == len(api_backoff) - 1:
                logger.error(f"ImageGen: all retries exhausted: {err_detail}")
                raise
            if getattr(e, "pptgod_gateway_idle_cutoff", False) and attempt >= _gateway_cutoff_max_attempts() - 1:
                timeout = _gateway_cutoff_seconds()
                logger.error(
                    "ImageGen: upstream gateway cut off image request around %ss; stopping retries to avoid repeated long failures",
                    timeout,
                )
                raise ProviderGatewayCutoffError(
                    f"图片接口超过约 {timeout} 秒仍未返回，被上游连接窗口截断；已停止重复重试。请稍后重试失败页。"
                ) from e
            if not _is_api_retryable(e):
                logger.error(f"ImageGen: non-retryable error, aborting: {err_detail}")
                raise
            api_backoff[attempt + 1] = _retry_after_seconds(e, api_backoff[attempt + 1])
    raise Exception("Image generation failed after all retries")


def generate_slide_image(
    prompt: str,
    reference_images: Optional[List[Image.Image]] = None,
    resolution: str = "4K",
    aspect_ratio: str = "16:9",
) -> Image.Image:
    mode = (settings.IMAGE_GEN_MODE or "real").lower()
    if mode == "mock":
        logger.info("ImageGen: mock mode enabled, returning placeholder image")
        return _make_mock_slide_image(prompt)

    if mode == "cached":
        key = _cache_key(prompt, reference_images, resolution, aspect_ratio)
        path = _cache_path(key)
        if os.path.exists(path):
            logger.info(f"ImageGen: cache hit {path}")
            return Image.open(path).copy()

        _reserve_real_image_call()
        with _image_api_slot():
            img = _generate_real_slide_image(prompt, reference_images, resolution, aspect_ratio)
        os.makedirs(settings.IMAGE_GEN_CACHE_DIR, exist_ok=True)
        img.save(path, "PNG")
        logger.info(f"ImageGen: cached generated image {path}")
        return img

    if mode != "real":
        raise ValueError("IMAGE_GEN_MODE must be one of: real, mock, cached")

    _reserve_real_image_call()
    with _image_api_slot():
        return _generate_real_slide_image(prompt, reference_images, resolution, aspect_ratio)


def save_slide_image(
    img: Image.Image,
    project_id: str,
    page_num: int,
    output_dir: str = "./outputs",
    suffix: str = "",
) -> str:
    project_dir = os.path.join(output_dir, project_id)
    os.makedirs(project_dir, exist_ok=True)
    safe_suffix = "".join(ch for ch in suffix if ch.isalnum() or ch in {"_", "-"})
    path = os.path.join(project_dir, f"slide_{page_num:02d}{safe_suffix}.png")
    img.save(path, "PNG")
    return path
