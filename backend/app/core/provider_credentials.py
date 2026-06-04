from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
import json
import logging
import uuid
from typing import Any, Mapping

from app.core.config import settings

logger = logging.getLogger(__name__)


MINIMAX_API_KEY_HEADER = "x-pptgod-minimax-api-key"
MINIMAX_API_BASE_HEADER = "x-pptgod-minimax-api-base"
MINIMAX_LLM_MODEL_HEADER = "x-pptgod-minimax-llm-model"
COMET_API_KEY_HEADER = "x-pptgod-comet-api-key"
COMET_API_BASE_HEADER = "x-pptgod-comet-api-base"
COMET_IMAGE_MODEL_HEADER = "x-pptgod-comet-image-model"
TESTER_NAME_HEADER = "x-pptgod-tester-name"

TASK_CREDENTIAL_TTL_SECONDS = 12 * 60 * 60
MINIMAX_M3_MODEL = "MiniMax-M3"
LEGACY_BUILTIN_MINIMAX_LLM_MODELS = {"MiniMax-M2.7"}


@dataclass(frozen=True)
class ProviderCredentials:
    minimax_api_key: str = ""
    minimax_api_base: str = ""
    minimax_llm_model: str = ""
    comet_api_key: str = ""
    comet_api_base: str = ""
    comet_image_model: str = ""
    tester_name: str = ""

    def with_defaults(self) -> "ProviderCredentials":
        return ProviderCredentials(
            minimax_api_key=self.minimax_api_key or settings.MINIMAX_API_KEY,
            minimax_api_base=_clean_base_url(self.minimax_api_base or settings.MINIMAX_API_BASE),
            minimax_llm_model=_normalize_minimax_llm_model(self.minimax_llm_model or settings.MINIMAX_LLM_MODEL),
            comet_api_key=self.comet_api_key or settings.COMET_API_KEY or self.minimax_api_key or settings.MINIMAX_API_KEY,
            comet_api_base=_clean_base_url(self.comet_api_base or settings.COMET_API_BASE),
            comet_image_model=(self.comet_image_model or settings.COMET_IMAGE_MODEL).strip(),
            tester_name=self.tester_name.strip(),
        )

    def has_request_override(self) -> bool:
        return bool(self.minimax_api_key or self.comet_api_key)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "ProviderCredentials":
        data = data or {}
        return cls(
            minimax_api_key=_clean_secret(data.get("minimax_api_key")),
            minimax_api_base=_clean_base_url(data.get("minimax_api_base")),
            minimax_llm_model=str(data.get("minimax_llm_model") or "").strip(),
            comet_api_key=_clean_secret(data.get("comet_api_key")),
            comet_api_base=_clean_base_url(data.get("comet_api_base")),
            comet_image_model=str(data.get("comet_image_model") or "").strip(),
            tester_name=str(data.get("tester_name") or "").strip()[:80],
        )

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> "ProviderCredentials":
        return cls(
            minimax_api_key=_clean_secret(headers.get(MINIMAX_API_KEY_HEADER)),
            minimax_api_base=_clean_base_url(headers.get(MINIMAX_API_BASE_HEADER)),
            minimax_llm_model=str(headers.get(MINIMAX_LLM_MODEL_HEADER) or "").strip(),
            comet_api_key=_clean_secret(headers.get(COMET_API_KEY_HEADER)),
            comet_api_base=_clean_base_url(headers.get(COMET_API_BASE_HEADER)),
            comet_image_model=str(headers.get(COMET_IMAGE_MODEL_HEADER) or "").strip(),
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


def _normalize_minimax_llm_model(value: Any) -> str:
    model = str(value or "").strip()
    if not model or model in LEGACY_BUILTIN_MINIMAX_LLM_MODELS:
        return settings.MINIMAX_LLM_MODEL.strip()
    return model


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


def get_minimax_chat_extra_body(model: str | None = None) -> dict[str, Any]:
    selected_model = str(model or get_minimax_llm_model() or "").strip()
    if selected_model == MINIMAX_M3_MODEL:
        return {"thinking": {"type": "disabled"}}
    return {}


def get_comet_image_model() -> str:
    return get_provider_credentials().comet_image_model


def store_current_provider_credentials(redis_client) -> str | None:
    credentials = get_raw_provider_credentials()
    if not credentials.has_request_override():
        return None
    credential_id = str(uuid.uuid4())
    try:
        redis_client.set(
            f"provider_credentials:{credential_id}",
            credentials.to_json(),
            ex=TASK_CREDENTIAL_TTL_SECONDS,
        )
    except Exception as exc:
        logger.error("Failed to store provider credentials for task: %s", exc)
        raise RuntimeError("任务凭据保存失败，请确认 Redis 正常后重试。") from exc
    return credential_id


def load_task_provider_credentials(redis_client, credential_id: str | None) -> ProviderCredentials | None:
    if not credential_id:
        return None
    try:
        raw = redis_client.get(f"provider_credentials:{credential_id}")
    except Exception as exc:
        logger.error("Failed to load provider credentials %s: %s", credential_id, exc)
        raise RuntimeError("任务凭据读取失败，请重新发起任务。") from exc
    if not raw:
        logger.error("Provider credentials %s are missing or expired", credential_id)
        raise RuntimeError("任务凭据已过期或不存在，请重新发起任务。")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        return ProviderCredentials.from_mapping(json.loads(raw))
    except Exception as exc:
        logger.error("Failed to decode provider credentials %s: %s", credential_id, exc)
        raise RuntimeError("任务凭据格式异常，请重新发起任务。") from exc
