import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from PIL import Image
import pytest

from app.services import image_generation, image_task_audit


def test_prepare_reference_image_keeps_source_size_without_max_side():
    image = Image.new("RGB", (1800, 1000), "white")

    prepared = image_generation._prepare_reference_image_for_upload(image, max_side=None)

    assert prepared.size == (1800, 1000)


def test_build_reference_upload_files_falls_back_only_after_budget_check(monkeypatch):
    calls = []

    def fake_reference_upload_file(ref, index, profile=None):
        calls.append(profile.max_side)
        size_bytes = 10 * 1024 * 1024 if profile.max_side is None else 10 * 1024
        return f"ref_{index}.jpg", io.BytesIO(b"x"), "image/jpeg", size_bytes

    monkeypatch.setattr(image_generation.settings, "IMAGE_REFERENCE_MAX_SIDE", 0)
    monkeypatch.setattr(image_generation.settings, "IMAGE_REFERENCE_UPLOAD_TARGET_MB", 1.0)
    monkeypatch.setattr(image_generation.settings, "IMAGE_REFERENCE_MAX_FILE_MB", 1.0)
    monkeypatch.setattr(image_generation, "_reference_upload_file", fake_reference_upload_file)

    files, upload_bytes, profile = image_generation._build_reference_upload_files([
        Image.new("RGB", (1, 1), "white")
    ])

    assert calls[0] is None
    assert profile.max_side is not None
    assert files[0][0] == "image"
    assert upload_bytes == 10 * 1024


def test_reference_upload_timeout_is_not_retryable():
    error = image_generation.ReferenceUploadTimeoutError("图片接口上传超时")

    assert image_generation._is_api_retryable(error) is False


def test_provider_gateway_cutoff_is_not_retryable():
    error = image_generation.ProviderGatewayCutoffError("图片接口被上游连接窗口截断")

    assert image_generation._is_api_retryable(error) is False


def test_default_image_generation_is_single_flight_high_quality():
    assert image_generation.settings.IMAGE_API_MAX_CONCURRENCY == 1
    assert image_generation.settings.IMAGE_GATEWAY_CUTOFF_MAX_ATTEMPTS == 1
    assert image_generation._image_quality() == "high"


def test_image_api_slot_uses_redis_global_queue(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.set_calls = []
            self.eval_calls = []

        def set(self, key, token, nx=False, ex=None):
            self.set_calls.append((key, token, nx, ex))
            return True

        def eval(self, *args):
            self.eval_calls.append(args)
            return 1

    fake = FakeRedis()
    monkeypatch.setattr(image_generation, "_get_image_redis_client", lambda: fake)

    with image_generation._image_api_slot():
        assert image_generation._current_image_queue_wait_seconds() is not None

    assert fake.set_calls
    assert fake.eval_calls


def test_image_generation_audit_log_writes_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr(image_task_audit.settings, "OUTPUT_DIR", str(tmp_path))

    path = image_task_audit.append_image_generation_log(
        "project id",
        "run id",
        "slide_started",
        page_num=1,
        prompt_length=123,
    )

    rows = [json.loads(line) for line in open(path, encoding="utf-8")]
    assert rows[0]["event"] == "slide_started"
    assert rows[0]["project_id"] == "project id"
    assert rows[0]["run_id"] == "run id"
    assert rows[0]["page_num"] == 1
    assert path.endswith("project_id/run_id.jsonl")


def test_invalid_image_quality_falls_back_to_high(monkeypatch):
    monkeypatch.setattr(image_generation.settings, "IMAGE_GPT_QUALITY", "typo")

    assert image_generation._image_quality() == "high"


def test_gateway_cutoff_detection_matches_120_second_connection_drop(monkeypatch):
    monkeypatch.setattr(image_generation.settings, "IMAGE_PROVIDER_GATEWAY_CUTOFF_SECONDS", 120)
    error = image_generation.requests.exceptions.ConnectionError("Connection error.")

    assert image_generation._is_gateway_idle_cutoff(error, 121.2) is True
    assert image_generation._is_gateway_idle_cutoff(error, 12.0) is False


def test_gateway_cutoff_stops_after_configured_attempts(monkeypatch):
    attempts = 0

    monkeypatch.setattr(image_generation.settings, "IMAGE_GATEWAY_CUTOFF_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(image_generation, "get_deer_image_model", lambda: "gpt-image-2-all")
    monkeypatch.setattr(image_generation.time, "sleep", lambda _seconds: None)

    def fake_generate(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        error = image_generation.requests.exceptions.ConnectionError("Connection error.")
        setattr(error, "pptgod_gateway_idle_cutoff", True)
        raise error

    monkeypatch.setattr(image_generation, "_call_gpt_image_2_generate", fake_generate)

    with pytest.raises(image_generation.ProviderGatewayCutoffError):
        image_generation._generate_real_slide_image("prompt", reference_images=None)

    assert attempts == 2


def test_image_call_events_are_thread_local():
    image_generation.reset_image_call_events()
    image_generation._record_image_call_event(endpoint="/v1/images/generations", status="succeeded")

    assert image_generation.get_image_call_events() == [
        {"endpoint": "/v1/images/generations", "status": "succeeded"}
    ]


def test_seed_reference_upload_uses_lightweight_profile(monkeypatch):
    image_generation._clear_reference_upload_cache()
    image = Image.effect_noise((2400, 1200), 80).convert("RGB")
    image.info["pptgod_reference_role"] = "seed_ref"

    monkeypatch.setattr(image_generation.settings, "IMAGE_SEED_REFERENCE_MAX_SIDE", 1280)
    monkeypatch.setattr(image_generation.settings, "IMAGE_SEED_REFERENCE_JPEG_QUALITY", 78)
    monkeypatch.setattr(image_generation.settings, "IMAGE_SEED_REFERENCE_PNG_THRESHOLD_KB", 16)
    profile = image_generation._ReferenceUploadProfile(
        max_side=None,
        jpeg_quality=95,
        png_threshold_bytes=10 * 1024 * 1024,
        label="source",
    )

    filename, buf, mime_type, _size_bytes = image_generation._reference_upload_file(image, 0, profile)

    assert filename == "ref_0.jpg"
    assert mime_type == "image/jpeg"
    buf.seek(0)
    uploaded = Image.open(buf)
    assert max(uploaded.size) == 1280


def test_reference_upload_file_reuses_cached_encoded_payload(monkeypatch):
    image_generation._clear_reference_upload_cache()
    image = Image.effect_noise((1600, 900), 80).convert("RGB")
    image.info["pptgod_reference_role"] = "seed_ref"
    image.info["pptgod_reference_source_path"] = "/tmp/pptgod-seed.png"
    image.info["pptgod_reference_source_mtime_ns"] = 123
    image.info["pptgod_reference_source_size"] = 456

    calls = 0
    original_prepare = image_generation._prepare_reference_image_for_upload

    def wrapped_prepare(ref, max_side=None):
        nonlocal calls
        calls += 1
        return original_prepare(ref, max_side=max_side)

    monkeypatch.setattr(image_generation, "_prepare_reference_image_for_upload", wrapped_prepare)
    monkeypatch.setattr(image_generation.settings, "IMAGE_SEED_REFERENCE_PNG_THRESHOLD_KB", 16)
    profile = image_generation._ReferenceUploadProfile(
        max_side=None,
        jpeg_quality=88,
        png_threshold_bytes=10 * 1024 * 1024,
        label="source",
    )

    first = image_generation._reference_upload_file(image, 0, profile)
    second = image_generation._reference_upload_file(image, 7, profile)

    assert calls == 1
    assert first[0] == "ref_0.jpg"
    assert second[0] == "ref_7.jpg"
    assert first[1].getvalue() == second[1].getvalue()


def test_reference_upload_encoding_is_single_flight_under_concurrency(monkeypatch):
    image_generation._clear_reference_upload_cache()
    image = Image.effect_noise((1200, 675), 80).convert("RGB")
    image.info["pptgod_reference_role"] = "seed_ref"
    image.info["pptgod_reference_source_path"] = "/tmp/pptgod-shared-seed.png"
    image.info["pptgod_reference_source_mtime_ns"] = 456
    image.info["pptgod_reference_source_size"] = 789

    calls = 0
    original_prepare = image_generation._prepare_reference_image_for_upload

    def wrapped_prepare(ref, max_side=None):
        nonlocal calls
        calls += 1
        time.sleep(0.05)
        return original_prepare(ref, max_side=max_side)

    monkeypatch.setattr(image_generation, "_prepare_reference_image_for_upload", wrapped_prepare)
    monkeypatch.setattr(image_generation.settings, "IMAGE_SEED_REFERENCE_PNG_THRESHOLD_KB", 16)
    profile = image_generation._ReferenceUploadProfile(
        max_side=None,
        jpeg_quality=88,
        png_threshold_bytes=10 * 1024 * 1024,
        label="source",
    )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda idx: image_generation._reference_upload_file(image, idx, profile), range(8)))

    assert calls == 1
    assert [result[0] for result in results] == [f"ref_{i}.jpg" for i in range(8)]
    assert len({result[1].getvalue() for result in results}) == 1


def test_edit_read_timeout_records_interrupted_without_retry(monkeypatch):
    image_generation.reset_image_call_events()
    profile = image_generation._ReferenceUploadProfile(
        max_side=None,
        jpeg_quality=85,
        png_threshold_bytes=512 * 1024,
        label="source",
    )

    monkeypatch.setattr(
        image_generation,
        "_build_reference_upload_files",
        lambda _refs: ([("image", ("ref_0.jpg", io.BytesIO(b"x"), "image/jpeg"))], 1, profile),
    )
    monkeypatch.setattr(
        image_generation,
        "get_provider_credentials",
        lambda: SimpleNamespace(deer_api_key="test-key", deer_api_base="https://example.test/v1"),
    )

    def fake_post(*_args, **_kwargs):
        raise image_generation.requests.exceptions.ReadTimeout("read timed out")

    monkeypatch.setattr(image_generation.requests, "post", fake_post)

    with pytest.raises(image_generation.ReferenceUploadTimeoutError):
        image_generation._call_gpt_image_2_edit(
            "prompt",
            [Image.new("RGB", (32, 18), "white")],
            idempotency_key="idem-test",
        )

    event = image_generation.get_image_call_events()[-1]
    assert event["endpoint"] == "/v1/images/edits"
    assert event["status"] == "interrupted"
    assert event["upload_bytes"] == 1
    assert event["upload_prepare_seconds"] >= 0
    assert event["idempotency_key"] == "idem-test"
