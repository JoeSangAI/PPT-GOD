import logging

from openai import OpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

_client = None


def get_llm_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = settings.MINIMAX_API_KEY
        if not api_key:
            raise RuntimeError("MINIMAX_API_KEY is not configured. Please set it in your .env file.")
        _client = OpenAI(
            api_key=api_key,
            base_url=settings.MINIMAX_API_BASE,
            timeout=120.0,
        )
    return _client
