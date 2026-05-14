import pytest

from app.core.provider_credentials import (
    ProviderCredentials,
    load_task_provider_credentials,
    provider_credentials_context,
    store_current_provider_credentials,
)


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
