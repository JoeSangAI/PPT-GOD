import logging
from pathlib import Path

from openai import OpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

_client = None


def _read_key_from_dotenv() -> str | None:
    """从 .env 文件读取 MINIMAX_API_KEY，解决环境变量覆盖问题。"""
    env_path = Path(__file__).parent.parent.parent / ".env"
    if not env_path.exists():
        return None
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("MINIMAX_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return None


def get_llm_client() -> OpenAI:
    global _client
    if _client is None:
        # 优先使用 .env 文件中的 key，避免系统环境变量中的旧 key 覆盖
        api_key = _read_key_from_dotenv() or settings.MINIMAX_API_KEY
        _client = OpenAI(
            api_key=api_key,
            base_url=settings.MINIMAX_API_BASE,
            timeout=120.0,
        )
    return _client
