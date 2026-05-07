from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
import json
import uuid
from typing import Any, Mapping

from app.core.config import settings


MINIMAX_API_KEY_HEADER = "x-pptgod-minimax-api-key"
MINIMAX_API_BASE_HEADER = "x-pptgod-minimax-api-base"
MINIMAX_LLM_MODEL_HEADER = "x-pptgod-minimax-llm-model"
DEER_API_KEY_HEADER = "x-pptgod-deer-api-key"
DEER_API_BASE_HEADER = "x-pptgod-deer-api-base"
DEER_IMAGE_MODEL_HEADER = "x-pptgod-deer-image-model"
TESTER_NAME_HEADER = "x-pptgod-tester-name"

TASK_CREDENTIAL_TTL_SECONDS = 12 * 60 * 60


@dataclass(frozen=True)
class ProviderCredentials:
    minimax_api_key: str = ""
    minimax_api_base: str = ""
    minimax_llm_model: str = ""
    deer_api_key: str = ""
    deer_api_base: str = ""
    deer_image_model: str = ""
    tester_name: str = ""

    def with_defaults(self) -> "ProviderCredentials":
        return ProviderCredentials(
            minimax_api_key=self.minimax_api_key or settings.MINIMAX_API_KEY,
            minimax_api_base=_clean_base_url(self.minimax_api_base or settings.MINIMAX_API_BASE),
            minimax_llm_model=(self.minimax_llm_model or settings.MINIMAX_LLM_MODEL).strip(),
            deer_api_key=self.deer_api_key or settings.DEER_API_KEY or self.minimax_api_key or settings.MINIMAX_API_KEY,
            deer_api_base=_clean_base_url(self.deer_api_base or settings.DEER_API_BASE),
            deer_image_model=(self.deer_image_model or settings.DEER_IMAGE_MODEL).strip(),
            tester_name=self.tester_name.strip(),
        )

    def has_request_override(self) -> bool:
        return bool(self.minimax_api_key or self.deer_api_key)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "ProviderCredentials":
        data = data or {}
        return cls(
            minimax_api_key=_clean_secret(data.get("minimax_api_key")),
            minimax_api_base=_clean_base_url(data.get("minimax_api_base")),
            minimax_llm_model=str(data.get("minimax_llm_model") or "").strip(),
            deer_api_key=_clean_secret(data.get("deer_api_key")),
            deer_api_base=_clean_base_url(data.get("deer_api_base")),
            deer_image_model=str(data.get("deer_image_model") or "").strip(),
            tester_name=str(data.get("tester_name") or "").strip()[:80],
        )

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> "ProviderCredentials":
        return cls(
            minimax_api_key=_clean_secret(headers.get(MINIMAX_API_KEY_HEADER)),
            minimax_api_base=_clean_base_url(headers.get(MINIMAX_API_BASE_HEADER)),
            minimax_llm_model=str(headers.get(MINIMAX_LLM_MODEL_HEADER) or "").strip(),
            deer_api_key=_clean_secret(headers.get(DEER_API_KEY_HEADER)),
            deer_api_base=_clean_base_url(headers.get(DEER_API_BASE_HEADER)),
            deer_image_model=str(headers.get(DEER_IMAGE_MODEL_HEADER) or "").strip(),
            tester_name=str(headers.get(TESTER_NAME_HEADER) or "").strip()[:80],
        )


_current_provider_credentials: ContextVar[ProviderCredentials | None] = ContextVar(
    "pptgod_provider_credentials",
    default=None,
)


def _clean_secret(value: Any) -> str:
    return str(value or "").strip()


def _clean_base_url(value: Any) -> str:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return ""
    return raw


def set_provider_credentials(credentials: ProviderCredentials):
    return _current_provider_credentials.set(credentials)


def reset_provider_credentials(token) -> None:
    _current_provider_credentials.reset(token)


@contextmanager
def provider_credentials_context(credentials: ProviderCredentials | None):
    token = set_provider_credentials(credentials or ProviderCredentials())
    try:
        yield
    finally:
        reset_provider_credentials(token)


def get_provider_credentials() -> ProviderCredentials:
    return (_current_provider_credentials.get() or ProviderCredentials()).with_defaults()


def get_raw_provider_credentials() -> ProviderCredentials:
    return _current_provider_credentials.get() or ProviderCredentials()


def get_minimax_llm_model() -> str:
    return get_provider_credentials().minimax_llm_model


def get_deer_image_model() -> str:
    return get_provider_credentials().deer_image_model


def store_current_provider_credentials(redis_client) -> str | None:
    credentials = get_raw_provider_credentials()
    if not credentials.has_request_override():
        return None
    credential_id = str(uuid.uuid4())
    redis_client.set(
        f"provider_credentials:{credential_id}",
        credentials.to_json(),
        ex=TASK_CREDENTIAL_TTL_SECONDS,
    )
    return credential_id


def load_task_provider_credentials(redis_client, credential_id: str | None) -> ProviderCredentials | None:
    if not credential_id:
        return None
    raw = redis_client.get(f"provider_credentials:{credential_id}")
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        return ProviderCredentials.from_mapping(json.loads(raw))
    except Exception:
        return None
