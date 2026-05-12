import logging

from openai import OpenAI

from app.core.config import settings
from app.core.provider_credentials import get_provider_credentials

logger = logging.getLogger(__name__)

_client = None


def get_llm_client() -> OpenAI:
    global _client
    credentials = get_provider_credentials()
    if credentials.minimax_api_key != settings.MINIMAX_API_KEY or credentials.minimax_api_base != settings.MINIMAX_API_BASE.rstrip("/"):
        if not credentials.minimax_api_key:
            raise RuntimeError("请先在登录页填写文本/规划接口 API Key。")
        return OpenAI(
            api_key=credentials.minimax_api_key,
            base_url=credentials.minimax_api_base,
            timeout=120.0,
            max_retries=0,
        )
    if _client is None:
        api_key = settings.MINIMAX_API_KEY
        if not api_key:
            raise RuntimeError("请先在登录页填写文本/规划接口 API Key。")
        _client = OpenAI(
            api_key=api_key,
            base_url=credentials.minimax_api_base,
            timeout=120.0,
            max_retries=0,
        )
    return _client
