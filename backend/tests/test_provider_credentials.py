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
