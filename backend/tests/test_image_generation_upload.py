import io

from PIL import Image

from app.services import image_generation


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
