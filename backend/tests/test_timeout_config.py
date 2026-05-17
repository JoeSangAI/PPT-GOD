"""
Timeout configuration guardrails.

These values were tuned through multiple debugging rounds for CometAPI's
gpt-image-2-all. If you need to change them, run a full image generation
end-to-end test first and update this file.
"""

import pytest
from app.core.config import settings


class TestTimeoutConfig:
    def test_image_api_timeout_not_too_short(self):
        """IMAGE_API_TIMEOUT_SECONDS must be >= 300s.

        CometAPI gpt-image-2-all can take 30-120s normally and 2-3x
        longer during peak hours. Values below 300s caused paid but
        failed generations (2026-05-16 incident).
        """
        assert float(settings.IMAGE_API_TIMEOUT_SECONDS or 0) >= 300, (
            f"IMAGE_API_TIMEOUT_SECONDS={settings.IMAGE_API_TIMEOUT_SECONDS} is too short. "
            "Must be >= 300s for CometAPI stability."
        )

    def test_gateway_cutoff_not_too_short(self):
        """IMAGE_PROVIDER_GATEWAY_CUTOFF_SECONDS must be >= 240s."""
        assert int(settings.IMAGE_PROVIDER_GATEWAY_CUTOFF_SECONDS or 0) >= 240, (
            f"IMAGE_PROVIDER_GATEWAY_CUTOFF_SECONDS={settings.IMAGE_PROVIDER_GATEWAY_CUTOFF_SECONDS} is too short. "
            "Must be >= 240s to match the API timeout."
        )

    def test_comet_api_base_uses_https(self):
        """COMET_API_BASE must use HTTPS."""
        base = settings.COMET_API_BASE or ""
        assert base.startswith("https://"), (
            f"COMET_API_BASE={base} must use HTTPS."
        )

    def test_comet_image_model_is_gpt_image_2(self):
        """COMET_IMAGE_MODEL must be gpt-image-2-all."""
        model = (settings.COMET_IMAGE_MODEL or "").strip()
        assert model == "gpt-image-2-all", (
            f"COMET_IMAGE_MODEL={model} must be 'gpt-image-2-all'. "
            "Other models were removed from the codebase."
        )
