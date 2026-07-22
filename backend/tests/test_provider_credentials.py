import pytest

from app.core.provider_credentials import (
    MINIMAX_M3_MODEL,
    ProviderCredentials,
    get_minimax_chat_extra_body,
    load_task_provider_credentials,
    provider_credentials_context,
    store_current_provider_credentials,
)
from app.core.config import settings
from app.services.runtime_readiness import build_action_preflight, build_runtime_readiness


def test_store_current_provider_credentials_raises_when_redis_write_fails():
    class FailingRedis:
        def set(self, *_args, **_kwargs):
            raise RuntimeError("redis down")

    with provider_credentials_context(ProviderCredentials(minimax_api_key="minimax-test")):
        with pytest.raises(RuntimeError, match="任务凭据保存失败"):
            store_current_provider_credentials(FailingRedis())


def test_load_task_provider_credentials_raises_when_override_is_missing():
    class EmptyRedis:
        def get(self, *_args, **_kwargs):
            return None

    with pytest.raises(RuntimeError, match="任务凭据已过期或不存在"):
        load_task_provider_credentials(EmptyRedis(), "missing-credential-id")


def test_load_task_provider_credentials_raises_on_invalid_payload():
    class InvalidRedis:
        def get(self, *_args, **_kwargs):
            return b"not json"

    with pytest.raises(RuntimeError, match="任务凭据格式异常"):
        load_task_provider_credentials(InvalidRedis(), "bad-credential-id")


def test_default_minimax_llm_model_is_m3():
    assert settings.MINIMAX_LLM_MODEL == MINIMAX_M3_MODEL


def test_m3_chat_calls_disable_thinking_for_structured_outputs():
    with provider_credentials_context(ProviderCredentials(minimax_llm_model=MINIMAX_M3_MODEL)):
        assert get_minimax_chat_extra_body() == {"thinking": {"type": "disabled"}}


def test_legacy_builtin_m27_override_normalizes_to_m3():
    with provider_credentials_context(ProviderCredentials(minimax_llm_model="MiniMax-M2.7")):
        assert get_minimax_chat_extra_body() == {"thinking": {"type": "disabled"}}


def test_provider_credentials_accept_provider_neutral_headers():
    credentials = ProviderCredentials.from_headers({
        "x-pptgod-text-api-key": "text-key",
        "x-pptgod-text-api-base": "https://text.example/v1/",
        "x-pptgod-text-model": "text-model",
        "x-pptgod-image-api-key": "image-key",
        "x-pptgod-image-api-base": "https://image.example/v1/",
        "x-pptgod-image-model": "image-model",
    })

    assert credentials.minimax_api_key == "text-key"
    assert credentials.minimax_api_base == "https://text.example/v1"
    assert credentials.minimax_llm_model == "text-model"
    assert credentials.comet_api_key == "image-key"
    assert credentials.comet_api_base == "https://image.example/v1"
    assert credentials.comet_image_model == "image-model"


def test_readiness_reports_each_missing_capability_without_blocking_service_health(monkeypatch):
    monkeypatch.setattr(settings, "MINIMAX_API_KEY", "")
    monkeypatch.setattr(settings, "COMET_API_KEY", "")

    readiness = build_runtime_readiness(ProviderCredentials())

    assert readiness["ok"] is True
    assert readiness["ready"] is False
    assert readiness["missing"] == ["text_generation", "image_generation"]
    assert "文本生成" in readiness["summary"]
    assert "图片生成" in readiness["summary"]


def test_readiness_can_distinguish_agent_supplied_capabilities_from_byok(monkeypatch):
    monkeypatch.setattr(settings, "MINIMAX_API_KEY", "")
    monkeypatch.setattr(settings, "COMET_API_KEY", "")

    readiness = build_runtime_readiness(
        ProviderCredentials(),
        agent_text=True,
        agent_image=True,
    )

    assert readiness["ready"] is True
    assert readiness["standalone_ready"] is False
    assert readiness["capabilities"]["text_generation"]["source"] == "agent"
    assert readiness["capabilities"]["image_generation"]["source"] == "agent"
    assert readiness["next_steps"] == []


def test_action_preflight_returns_one_clear_configuration_action_when_provider_is_missing(monkeypatch):
    monkeypatch.setattr(settings, "MINIMAX_API_KEY", "")

    result = build_action_preflight(
        "text_generation",
        action="generate_content_plan",
        credentials=ProviderCredentials(),
    )

    assert result["ok"] is False
    assert result["code"] == "missing_model_capability"
    assert result["next_action"] == {
        "type": "configure_model",
        "capability": "text_generation",
        "label": "配置文本生成模型",
    }


def test_readiness_only_returns_next_steps_for_capabilities_still_missing(monkeypatch):
    monkeypatch.setattr(settings, "MINIMAX_API_KEY", "")
    monkeypatch.setattr(settings, "COMET_API_KEY", "")

    readiness = build_runtime_readiness(ProviderCredentials(), agent_text=True)

    assert readiness["missing"] == ["image_generation"]
    assert [item["capability"] for item in readiness["next_steps"]] == ["image_generation"]


def test_action_preflight_tells_gui_to_return_to_capable_agent(monkeypatch):
    monkeypatch.setattr(settings, "COMET_API_KEY", "")
    monkeypatch.setattr(settings, "MINIMAX_API_KEY", "")

    result = build_action_preflight(
        "image_generation",
        action="generate_slides",
        credentials=ProviderCredentials(),
        agent_supplied=True,
    )

    assert result["ok"] is False
    assert result["code"] == "agent_action_required"
    assert result["next_action"]["type"] == "return_to_agent"
