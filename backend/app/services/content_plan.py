import json
import json_repair
import logging
import re
from typing import Callable, Dict, List

from app.core.config import settings
from app.core.llm_client import get_llm_client
from app.services.search_service import get_knowledge_augmenter

logger = logging.getLogger(__name__)


def _clean_json_response(content: str) -> str:
    """从 LLM 响应中提取 JSON 数组。"""
    content = re.sub(r"^```(?:json)?\s*|```$", "", content, flags=re.MULTILINE | re.IGNORECASE).strip()
    start_idx = content.find("[")
    end_idx = content.rfind("]")
    if start_idx != -1 and end_idx != -1:
        content = content[start_idx : end_idx + 1]
    return content


def _normalize_outline_page_count(outline: List[Dict], page_count: int) -> List[Dict]:
    """Keep LLM output within the requested page budget and renumber pages."""
    if not isinstance(outline, list):
        raise ValueError("Content plan generation failed: LLM output is not a JSON array")
    target_count = max(1, int(page_count or len(outline) or 1))
    if len(outline) > target_count:
        logger.warning(
            f"ContentPlan: LLM returned {len(outline)} pages, trimming to requested {target_count}"
        )
        outline = outline[:target_count]
    for idx, page in enumerate(outline, start=1):
        if isinstance(page, dict):
            page["page_num"] = idx
    return outline


def generate_content_plan(
    topic: str,
    audience: str = "通用受众",
    page_count: int = 10,
    documents: str = "",
    on_progress: Callable[[dict], None] | None = None,
) -> List[Dict]:
    """
    根据主题和文档生成 Content Plan。
    支持流式读取和进度回调，让前端能看到生成过程。
    """
    has_docs = bool(documents and documents.strip())
    logger.info(
        f"ContentPlan: 为主题 '{topic[:30]}...' 生成大纲, "
        f"page_count={page_count}, has_documents={has_docs}"
    )

    if on_progress:
        on_progress({"stage": "analyzing", "message": "正在分析主题和文档素材..."})

    doc_section = ""
    if has_docs:
        doc_section = f"""
【用户上传的文档素材】
{documents}

【文档使用规则】
1. 以上文档是用户提供的核心素材，你必须基于文档内容设计 PPT 大纲。
2. 文档中的关键论点、数据、结构必须体现在大纲中。
3. 页数可以从文档内容的丰富程度推断，不一定严格限制在 {page_count} 页，但应尽量接近。
"""

    # 【新增】内容规划阶段也触发实时搜索，避免模型对前沿话题产生幻觉
    search_section = ""
    search_context = get_knowledge_augmenter().augment(topic, has_documents=has_docs)
    if search_context:
        search_section = f"""
{search_context}

【搜索结果使用规则】
1. 上述网络搜索结果是实时获取的，你必须基于这些事实信息设计 PPT 大纲。
2. 人名、角色名、剧情、数据等关键信息必须与搜索结果一致，严禁编造。
3. 如果搜索结果不足以支撑完整大纲，可以合理推断，但必须标注为"推测"。
"""
        logger.info(f"ContentPlan: 已注入搜索上下文，topic={topic[:30]}")

    prompt = f"""你是一位顶尖的商业演示架构师。请为以下主题设计一份 PPT 大纲。

【主题】
{topic}

【背景】
- 目标受众: {audience}
- 期望页数: {page_count} 页左右
{doc_section}
{search_section}

【任务要求】
1. 设计清晰的叙事结构（起承转合）。
2. 每页必须包含：
   - page_num: 页码
   - type: 页面类型（cover/目录 toc/content/hero/data/ending）
   - section_title: 所属章节
   - text_content.headline: 大标题（有力、简洁的断言句）
   - text_content.subhead: 副标题（可选）
   - text_content.body: 正文（markdown 格式字符串，支持加粗、列表、表格等）
   - speaker_notes: 演讲者备注（详细论述，供演讲者参考）
   - visual_suggestion: 画面/配图建议
3. 封面和封底各占一页。
4. 内容页不要堆砌，每页只说一件事。
5. 标题多用设问或断言，少平铺直叙。

【JSON 格式】
严格输出 JSON 数组，不要包含 Markdown 代码块标记：
[
  {{
    "page_num": 1,
    "type": "cover",
    "section_title": "",
    "text_content": {{
      "headline": "主标题",
      "subhead": "副标题",
      "body": ""
    }},
    "speaker_notes": "",
    "visual_suggestion": ""
  }}
]
"""

    client = get_llm_client()
    stream = client.chat.completions.create(
        model=settings.MINIMAX_LLM_MODEL,
        messages=[
            {"role": "system", "content": "你是世界一流的 PPT 架构师。必须且只能输出合法的 JSON 数组，严禁添加任何额外说明文本。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.5,
        stream=True,
    )

    full_content = ""
    page_count_found = 0
    in_think = False
    think_buffer = ""

    if on_progress:
        on_progress({"stage": "generating", "message": "正在构建叙事结构...", "current_page": 0, "total_pages": page_count})

    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta.content or ""
        full_content += delta

        # 提取 think 内容（MiniMax 的推理过程）
        buf = delta
        while buf:
            if not in_think:
                idx = buf.find("<think>")
                if idx == -1:
                    buf = ""
                    break
                buf = buf[idx + 7:]
                in_think = True
            else:
                idx = buf.find("</think>")
                if idx == -1:
                    think_buffer += buf
                    buf = ""
                    break
                else:
                    think_buffer += buf[:idx]
                    buf = buf[idx + 8:]
                    in_think = False

        # 检测新生成的页面
        new_page_count = full_content.count('"page_num"')
        if new_page_count > page_count_found:
            page_count_found = new_page_count
            if on_progress:
                current_page = min(page_count_found, page_count)
                on_progress({
                    "stage": "generating",
                    "message": f"正在生成第 {current_page}/{page_count} 页...",
                    "current_page": current_page,
                    "total_pages": page_count,
                    "think": think_buffer[-200:] if think_buffer else None,
                })

    # 去掉 think 标签后解析 JSON
    clean = re.sub(r"<think>.*?</think>", "", full_content, flags=re.DOTALL).strip()
    clean = _clean_json_response(clean)

    # 多层降级解析：先尝试 json_repair，再尝试标准 json
    outline = None
    parse_errors = []

    try:
        outline = json_repair.loads(clean)
    except Exception as e:
        parse_errors.append(f"json_repair: {e}")

    if outline is None:
        try:
            outline = json.loads(clean)
        except Exception as e:
            parse_errors.append(f"json.loads: {e}")

    if outline is None:
        # 最后的尝试：只提取第一个完整对象到最后一个完整对象之间的内容
        first_obj = clean.find("{")
        last_arr = clean.rfind("]")
        if first_obj != -1 and last_arr != -1 and first_obj < last_arr:
            snippet = "[" + clean[first_obj:last_arr + 1].replace("}\n{", "},\n{") + "]"
            try:
                outline = json_repair.loads(snippet)
            except Exception as e:
                parse_errors.append(f"snippet repair: {e}")

    if outline is None:
        preview = clean[:500].replace("\n", " ")
        logger.error(f"[ContentPlan] JSON parse failed after all fixes. Preview: {preview!r}")
        logger.error(f"[ContentPlan] Errors: {'; '.join(parse_errors)}")
        raise ValueError(f"Content plan generation failed: invalid JSON from LLM. Preview: {preview[:200]}")

    outline = _normalize_outline_page_count(outline, page_count)

    logger.info(f"ContentPlan: 生成完成，共 {len(outline)} 页")

    if on_progress:
        on_progress({"stage": "saving", "message": "正在保存结果...", "current_page": len(outline), "total_pages": len(outline)})

    return outline
