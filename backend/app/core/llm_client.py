import logging
import threading

from openai import OpenAI

from app.core.config import settings
from app.core.provider_credentials import get_minimax_chat_extra_body, get_provider_credentials

logger = logging.getLogger(__name__)

_client = None
_client_cache = {}
_client_lock = threading.Lock()


class _ChatCompletionsProxy:
    def __init__(self, completions):
        self._completions = completions

    def create(self, *args, **kwargs):
        model = kwargs.get("model")
        extra_body = get_minimax_chat_extra_body(model)
        if extra_body:
            merged = dict(extra_body)
            if isinstance(kwargs.get("extra_body"), dict):
                merged.update(kwargs["extra_body"])
            kwargs["extra_body"] = merged
        return self._completions.create(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._completions, name)


class _ChatProxy:
    def __init__(self, chat):
        self._chat = chat
        self.completions = _ChatCompletionsProxy(chat.completions)

    def __getattr__(self, name):
        return getattr(self._chat, name)


class _LlmClientProxy:
    def __init__(self, client: OpenAI):
        self._client = client
        self.chat = _ChatProxy(client.chat)

    def __getattr__(self, name):
        return getattr(self._client, name)


def _make_llm_client(api_key: str, base_url: str) -> OpenAI:
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=120.0,
        max_retries=2,
    )
    return _LlmClientProxy(client)


def get_llm_client() -> OpenAI:
    global _client
    credentials = get_provider_credentials()
    if credentials.minimax_api_key != settings.MINIMAX_API_KEY or credentials.minimax_api_base != settings.MINIMAX_API_BASE.rstrip("/"):
        if not credentials.minimax_api_key:
            raise RuntimeError("请先在登录页填写文本/规划接口 API Key。")
        cache_key = (credentials.minimax_api_key, credentials.minimax_api_base)
        with _client_lock:
            client = _client_cache.get(cache_key)
            if client is None:
                client = _make_llm_client(credentials.minimax_api_key, credentials.minimax_api_base)
                _client_cache[cache_key] = client
            return client
    if _client is None:
        with _client_lock:
            if _client is None:
                api_key = settings.MINIMAX_API_KEY
                if not api_key:
                    raise RuntimeError("请先在登录页填写文本/规划接口 API Key。")
                _client = _make_llm_client(api_key, credentials.minimax_api_base)
    return _client
