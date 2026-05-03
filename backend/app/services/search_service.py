import logging
from typing import Dict, List, Optional

import requests

from app.core.config import settings
from app.core.llm_client import get_llm_client

logger = logging.getLogger(__name__)

# 明显不需要搜索的轻量操作指令关键词
_SKIP_KEYWORDS = {"改", "换", "调整", "修改", "变大", "变小", "颜色", "字体", "风格", "排版", "对齐", "间距"}


def _should_skip_search(topic: str) -> bool:
    """快速规则过滤：纯排版/风格调整类消息不需要搜索。"""
    msg = topic.strip()
    if len(msg) < 6:
        return True
    if len(msg) < 20 and any(kw in msg for kw in _SKIP_KEYWORDS):
        return True
    return False


def _detect_knowledge_gap(topic: str) -> str:
    """
    判断 topic 是否需要搜索。
    不依赖 LLM 自我评估（LLM 会高估自己的知识），直接基于规则：
    - 纯排版/风格指令 → 不搜
    - 其他所有情况 → 直接搜索 topic
    返回搜索词（非空表示需要搜索），返回空字符串表示不需要。
    """
    if _should_skip_search(topic):
        return ""
    # 必查：直接搜索，不问 LLM
    return topic.strip()


def _format_as_knowledge_context(results: List[Dict]) -> str:
    """将搜索结果泛化为 LLM 可用的知识上下文。"""
    if not results:
        return ""

    lines = ["【补充知识上下文 — 以下内容来自实时网络搜索，供你参考】"]
    for idx, r in enumerate(results, 1):
        title = r.get("title", "")
        snippet = r.get("snippet", "")
        date = r.get("date", "")
        date_str = f"（{date}）" if date else ""
        lines.append(f"{idx}. {title}{date_str}")
        if snippet:
            lines.append(f"   摘要：{snippet}")
    lines.append(
        "【使用规则】"
        "\n1. 当搜索结果与你的训练知识冲突时，以搜索结果为准。"
        "\n2. 关键事实（名称、数据、时间、事件）必须基于上述信息，严禁编造。"
        "\n3. 如果搜索结果不相关或为空，忽略它，使用你自己的知识。"
        "\n4. 搜索结果未覆盖的部分，可以基于常识合理推断。"
    )
    return "\n".join(lines)


class KnowledgeAugmenter:
    """
    统一的知识增强层。

    职责：检测知识缺口 → 执行搜索 → 格式化注入。
    所有内容生成服务（chat、content_plan 等）共享，避免散弹式修复。
    """

    def __init__(self):
        # 简单内存缓存：同一话题避免重复搜索
        self._cache: Dict[str, str] = {}

    def augment(
        self,
        topic: str,
        has_documents: bool = False,
        force_search: bool = False,
    ) -> str:
        """
        主入口：判断是否需要搜索，如需则执行并返回格式化上下文。

        Args:
            topic: 用户输入的主题/消息
            has_documents: 是否有用户上传的文档。有文档时通常优先文档，不额外搜索。
            force_search: 强制搜索（跳过知识缺口判断）

        Returns:
            格式化后的知识上下文字符串。不需要时返回空字符串，调用方直接忽略即可。
        """
        if not topic or not topic.strip():
            return ""

        cache_key = topic.strip()

        # 有文档素材时，优先基于文档，不额外搜索（除非强制）
        if has_documents and not force_search:
            return ""

        # 检查缓存
        if cache_key in self._cache:
            logger.debug(f"KnowledgeAugmenter: cache hit for '{cache_key[:30]}'")
            return self._cache[cache_key]

        # 判断是否需要搜索
        if not force_search:
            search_query = _detect_knowledge_gap(topic)
            if not search_query:
                return ""
        else:
            search_query = topic

        # 执行搜索
        results = search_via_minimax(search_query, top_n=3)
        if not results:
            return ""

        context = _format_as_knowledge_context(results)
        self._cache[cache_key] = context
        logger.info(f"KnowledgeAugmenter: augmented topic='{cache_key[:30]}', query='{search_query}'")
        return context

    def clear_cache(self) -> None:
        """清空缓存。可用于测试或长会话后释放内存。"""
        self._cache.clear()


# 全局单例，所有模块共享
_global_augmenter: Optional[KnowledgeAugmenter] = None


def get_knowledge_augmenter() -> KnowledgeAugmenter:
    """获取全局 KnowledgeAugmenter 实例。"""
    global _global_augmenter
    if _global_augmenter is None:
        _global_augmenter = KnowledgeAugmenter()
    return _global_augmenter


def search_via_minimax(query: str, top_n: int = 3) -> List[Dict]:
    """
    调用 MiniMax Coding Plan 搜索接口。
    返回结构化搜索结果列表，每个元素包含 title/link/snippet/date。
    """
    if not query or not query.strip():
        return []

    api_key = settings.MINIMAX_API_KEY
    api_host = settings.MINIMAX_API_BASE.rstrip("/")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(
            f"{api_host}/coding_plan/search",
            headers=headers,
            json={"q": query.strip()},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.warning(f"MiniMax search request failed: {e}")
        return []
    except Exception as e:
        logger.warning(f"MiniMax search unexpected error: {e}")
        return []

    organic = data.get("organic", []) if isinstance(data, dict) else []
    results = []
    for item in organic[:top_n]:
        if not isinstance(item, dict):
            continue
        results.append(
            {
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "snippet": item.get("snippet", ""),
                "date": item.get("date", ""),
            }
        )

    logger.info(f"MiniMax search: query={query.strip()!r}, results={len(results)}")
    return results


