import logging
import threading

from openai import OpenAI

from app.core.config import settings
from app.core.provider_credentials import get_provider_credentials

logger = logging.getLogger(__name__)

_client = None
_client_cache = {}
_client_lock = threading.Lock()


def _make_llm_client(api_key: str, base_url: str) -> OpenAI:
    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=120.0,
        max_retries=2,
    )


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
