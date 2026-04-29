import base64
import hashlib
import io
import logging
import os
import threading
import time
import uuid
from typing import List, Optional

import httpx
import requests
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
)
from PIL import Image

from app.core.config import settings

logger = logging.getLogger(__name__)

_image_client = None
_image_client_lock = threading.Lock()
_run_state_lock = threading.Lock()
_real_image_calls_this_run = 0


def _get_image_client() -> OpenAI:
    global _image_client
    if _image_client is None:
        with _image_client_lock:
            if _image_client is None:
                timeout = httpx.Timeout(1800.0, connect=30.0)
                _image_client = OpenAI(
                    api_key=settings.DEER_API_KEY or settings.MINIMAX_API_KEY,
                    base_url=settings.DEER_API_BASE,
                    timeout=timeout,
                    max_retries=0,  # 禁用 SDK 自动重试，由本模块手动控制，防止重复计费
                )
    return _image_client


def _pil_to_bytes(img: Image.Image, fmt: str = "PNG") -> bytes:
    buffered = io.BytesIO()
    img.save(buffered, format=fmt)
    return buffered.getvalue()


def _crop_to_3_2(img: Image.Image) -> Image.Image:
    """裁剪为 3:2（1536x1024）比例。"""
    w, h = img.size
    target_ratio = 3 / 2
    current_ratio = w / h
    if abs(current_ratio - target_ratio) > 0.05:
        if current_ratio < target_ratio:
            new_h = int(w / target_ratio)
            top = (h - new_h) // 2
            img = img.crop((0, top, w, top + new_h))
        else:
            new_w = int(h * target_ratio)
            left = (w - new_w) // 2
            img = img.crop((left, 0, left + new_w, h))
    return img


def reset_image_generation_run_state() -> None:
    """Reset per-process real image accounting for tests and controlled smoke runs."""
    global _real_image_calls_this_run
    with _run_state_lock:
        _real_image_calls_this_run = 0


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
    h.update(settings.DEER_IMAGE_MODEL.encode("utf-8"))
    h.update(resolution.encode("utf-8"))
    h.update(aspect_ratio.encode("utf-8"))
    h.update(prompt.encode("utf-8"))
    for ref in reference_images or []:
        h.update(str(ref.size).encode("utf-8"))
        h.update(str(ref.mode).encode("utf-8"))
    return h.hexdigest()


def _cache_path(key: str) -> str:
    return os.path.join(settings.IMAGE_GEN_CACHE_DIR, f"{key}.png")


def _is_api_retryable(exc: Exception) -> bool:
    """
    只对明确不计费的状态码重试。
    429 = 服务器限流，请求未被处理，不计费。
    ConnectionError / Timeout / 5xx 一律不重试：
    请求可能已到达服务器并完成计费，重试会导致重复扣费。
    """
    if isinstance(exc, APIStatusError):
        status = exc.status_code
        if status == 429:
            return True
    return False


def _download_image_bytes(url: str, max_attempts: int = 3) -> bytes:
    for attempt in range(max_attempts):
        try:
            logger.info(f"ImageGen: downloading URL (attempt {attempt + 1}/{max_attempts})")
            resp = requests.get(url, timeout=300)
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


def _call_gpt_image_2_generate(
    prompt: str, size: str = "1536x1024", idempotency_key: Optional[str] = None
) -> Image.Image:
    client = _get_image_client()
    headers = {}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    resp = client.images.generate(
        model=settings.DEER_IMAGE_MODEL,
        prompt=prompt,
        size=size,
        quality="high",
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
    return Image.open(io.BytesIO(img_bytes))


def _call_gpt_image_2_edit(
    prompt: str, reference_images: List[Image.Image], size: str = "1536x1024",
    idempotency_key: Optional[str] = None
) -> Image.Image:
    """使用 requests 直接调用 DeerAPI images/edit，支持 additional_images[] 多图垫图。"""
    if not reference_images:
        raise ValueError("reference_images required for edit")
    files = []
    primary_ref = reference_images[0]
    buf = io.BytesIO()
    primary_ref.save(buf, format="PNG")
    buf.seek(0)
    files.append(("image", ("ref_0.png", buf, "image/png")))
    for i, ref in enumerate(reference_images[1:], 1):
        buf = io.BytesIO()
        ref.save(buf, format="PNG")
        buf.seek(0)
        files.append(("additional_images[]", (f"ref_{i}.png", buf, "image/png")))
    data = {
        "model": settings.DEER_IMAGE_MODEL,
        "prompt": prompt,
        "size": size,
        "n": "1",
    }
    headers = {"Authorization": f"Bearer {settings.DEER_API_KEY or settings.MINIMAX_API_KEY}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    logger.info(f"ImageGen: calling edit API with {len(reference_images)} reference images")
    resp = requests.post(
        f"{settings.DEER_API_BASE}/images/edits",
        headers=headers,
        data=data,
        files=files,
        timeout=(30, 600),
    )
    resp.raise_for_status()
    body = resp.json()
    image_data = body["data"][0]
    if image_data.get("b64_json"):
        img_bytes = base64.b64decode(image_data["b64_json"])
    elif image_data.get("url"):
        img_bytes = _download_image_bytes(image_data["url"])
    else:
        raise ValueError("DeerAPI returned no image content")
    return Image.open(io.BytesIO(img_bytes))


def _call_gemini_chat_generate(
    prompt: str,
    reference_images: Optional[List[Image.Image]] = None,
    aspect_ratio: str = "16:9",
    resolution: str = "4K",
) -> Image.Image:
    client = _get_image_client()
    messages = [{"role": "user", "content": [{"type": "text", "text": prompt}]}]
    for ref_img in (reference_images or [])[:14]:
        buffered = io.BytesIO()
        ref_img.save(buffered, format="PNG")
        img_b64 = base64.b64encode(buffered.getvalue()).decode()
        messages[0]["content"].append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{img_b64}"}
        })
    resp = client.chat.completions.create(
        model=settings.DEER_IMAGE_MODEL,
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
    model = settings.DEER_IMAGE_MODEL.lower()
    size = "1536x1024"
    idempotency_key = str(uuid.uuid4())
    api_backoff = [0, 5]
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
            img = _crop_to_3_2(img)
            logger.info(f"ImageGen: success, model={settings.DEER_IMAGE_MODEL}, size={img.size}")
            return img
        except Exception as e:
            req_id = getattr(e, "request_id", None)
            err_detail = str(e)
            if req_id:
                err_detail = f"{err_detail} [request_id={req_id}]"
            logger.warning(
                f"ImageGen: API call failed (attempt {attempt + 1}/{len(api_backoff)}): {err_detail}"
            )
            if not _is_api_retryable(e):
                logger.error(f"ImageGen: non-retryable error, aborting: {err_detail}")
                raise
            if attempt == len(api_backoff) - 1:
                logger.error(f"ImageGen: all retries exhausted: {err_detail}")
                raise
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
        img = _generate_real_slide_image(prompt, reference_images, resolution, aspect_ratio)
        os.makedirs(settings.IMAGE_GEN_CACHE_DIR, exist_ok=True)
        img.save(path, "PNG")
        logger.info(f"ImageGen: cached generated image {path}")
        return img

    if mode != "real":
        raise ValueError("IMAGE_GEN_MODE must be one of: real, mock, cached")

    _reserve_real_image_call()
    return _generate_real_slide_image(prompt, reference_images, resolution, aspect_ratio)


def save_slide_image(img: Image.Image, project_id: str, page_num: int, output_dir: str = "./outputs") -> str:
    project_dir = os.path.join(output_dir, project_id)
    os.makedirs(project_dir, exist_ok=True)
    path = os.path.join(project_dir, f"slide_{page_num:02d}.png")
    img.save(path, "PNG")
    return path
