import pytest
from PIL import Image

from app.core.config import settings
from app.services import image_generation


def teardown_function():
    settings.IMAGE_GEN_MODE = "real"
    settings.IMAGE_GEN_CACHE_DIR = "./outputs/image-cache"
    settings.MAX_REAL_IMAGES_PER_RUN = 0
    image_generation.reset_image_generation_run_state()


def test_mock_image_mode_returns_placeholder_without_real_api(monkeypatch):
    settings.IMAGE_GEN_MODE = "mock"

    def fail_if_called(*args, **kwargs):
        raise AssertionError("real image API should not be called in mock mode")

    monkeypatch.setattr(image_generation, "_generate_real_slide_image", fail_if_called)

    img = image_generation.generate_slide_image("test prompt")

    assert isinstance(img, Image.Image)
    assert img.size == (1792, 1008)


def test_real_image_budget_blocks_second_real_call(monkeypatch):
    settings.IMAGE_GEN_MODE = "real"
    settings.MAX_REAL_IMAGES_PER_RUN = 1

    def fake_real(*args, **kwargs):
        return Image.new("RGB", (1792, 1008), "white")

    monkeypatch.setattr(image_generation, "_generate_real_slide_image", fake_real)

    image_generation.generate_slide_image("first prompt")

    with pytest.raises(RuntimeError, match="Real image generation limit exceeded"):
        image_generation.generate_slide_image("second prompt")


def test_cached_mode_reuses_existing_image_without_real_api(monkeypatch, tmp_path):
    settings.IMAGE_GEN_MODE = "cached"
    settings.IMAGE_GEN_CACHE_DIR = str(tmp_path)
    calls = 0

    def fake_real(*args, **kwargs):
        nonlocal calls
        calls += 1
        return Image.new("RGB", (1792, 1008), "white")

    monkeypatch.setattr(image_generation, "_generate_real_slide_image", fake_real)

    first = image_generation.generate_slide_image("same prompt")
    second = image_generation.generate_slide_image("same prompt")

    assert first.size == (1792, 1008)
    assert second.size == (1792, 1008)
    assert calls == 1

