import json
import json_repair
import logging
import re
from typing import Callable, Dict, List

from app.core.llm_client import get_llm_client
from app.core.provider_credentials import get_minimax_llm_model
from app.services.document_parser import detect_ppt_sources
from app.services.search_service import get_knowledge_augmenter
from app.utils.text_cleaning import normalize_markdown_emphasis

logger = logging.getLogger(__name__)


def _clean_json_response(content: str) -> str:
    """从 LLM 响应中提取 JSON 数组。"""
    content = re.sub(r"^```(?:json)?\s*|```$", "", content, flags=re.MULTILINE | re.IGNORECASE).strip()
    start_idx = content.find("[")
    end_idx = content.rfind("]")
    if start_idx != -1 and end_idx != -1:
        content = content[start_idx : end_idx + 1]
    return content


def _parse_export_attr(attrs: str, key: str) -> str:
    match = re.search(rf'{re.escape(key)}=("(?:\\.|[^"])*"|[^\s>]+)', attrs or "")
    if not match:
        return ""
    raw = match.group(1).strip()
    if raw.startswith('"') and raw.endswith('"'):
        try:
            return str(json.loads(raw))
        except Exception:
            return raw[1:-1]
    return raw


def _clean_export_section_value(value: str) -> str:
    text = (value or "").strip()
    if text in {"<!-- 留空 -->", "<!--留空-->"}:
        return ""
    return text


def _parse_export_sections(page_markdown: str) -> dict[str, str]:
    section_alias = {
        "标题": "headline",
        "副标题": "subhead",
        "正文": "body",
        "备注": "speaker_notes",
        "演讲备注": "speaker_notes",
        "备注（演讲备注）": "speaker_notes",
    }
    labels = "|".join(re.escape(label) for label in section_alias)
    pattern = re.compile(
        rf"^###\s*({labels})\s*\n+(.*?)(?=^###\s*(?:{labels})\s*\n+|\Z)",
        flags=re.MULTILINE | re.DOTALL,
    )
    values: dict[str, str] = {}
    for match in pattern.finditer(page_markdown or ""):
        key = section_alias.get(match.group(1).strip())
        if key:
            values[key] = _clean_export_section_value(match.group(2))
    return values


def parse_exported_content_plan_markdown(documents: str) -> list[dict]:
    """Parse PPT God's lightweight Markdown export back into content plan pages."""
    if "PPTGOD_EXPORT_KIND: content_plan_markdown" not in (documents or ""):
        return []
    page_pattern = re.compile(
        r"<!--\s*PPTGOD_PAGE_START\s+([^>]*)-->(.*?)<!--\s*PPTGOD_PAGE_END\s+page_num=\d+\s*-->",
        flags=re.DOTALL,
    )
    pages: list[dict] = []
    for idx, match in enumerate(page_pattern.finditer(documents), start=1):
        attrs = match.group(1)
        page_markdown = match.group(2)
        try:
            page_num = int(_parse_export_attr(attrs, "page_num") or idx)
        except ValueError:
            page_num = idx
        page_type = _parse_export_attr(attrs, "type") or "content"
        section_title = _parse_export_attr(attrs, "section_title")
        sections = _parse_export_sections(page_markdown)
        pages.append({
            "page_num": page_num,
            "type": page_type,
            "section_title": section_title,
            "text_content": {
                "headline": sections.get("headline", ""),
                "subhead": sections.get("subhead", ""),
                "body": sections.get("body", ""),
            },
            "speaker_notes": sections.get("speaker_notes", ""),
            "visual_suggestion": "",
            "source_refs": [],
        })

    pages.sort(key=lambda page: int(page.get("page_num") or 0))
    for idx, page in enumerate(pages, start=1):
        page["page_num"] = idx
    return _normalize_content_markdown(pages) if pages else []


def _is_strict_page_count_request(topic: str) -> bool:
    text = topic or ""
    strict_patterns = (
        r"必须\s*\d+\s*页",
        r"严格\s*\d+\s*页",
        r"固定\s*\d+\s*页",
        r"只能\s*\d+\s*页",
        r"不要超过\s*\d+\s*页",
        r"不超过\s*\d+\s*页",
        r"exactly\s+\d+\s+pages?",
        r"must\s+be\s+\d+\s+pages?",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in strict_patterns)


def _soft_page_bounds(page_count: int) -> tuple[int, int]:
    target = max(1, int(page_count or 1))
    lower = max(1, int(target * 0.7))
    upper = max(target, int(target * 1.3 + 0.999)) + 1
    return lower, upper


def _is_ppt_transform_request(topic: str) -> bool:
    text = (topic or "").lower()
    patterns = (
        r"扩展到\s*\d+\s*页",
        r"拓展到\s*\d+\s*页",
        r"缩减到\s*\d+\s*页",
        r"压缩到\s*\d+\s*页",
        r"减少到\s*\d+\s*页",
        r"提取",
        r"融合",
        r"合并",
        r"重组",
        r"改写",
        r"重新规划",
        r"只要",
        r"某个主题",
        r"特定主题",
        r"expand to\s+\d+\s+pages?",
        r"reduce to\s+\d+\s+pages?",
        r"extract",
        r"merge",
        r"combine",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _is_general_transform_request(topic: str) -> bool:
    text = (topic or "").lower()
    patterns = (
        r"摘要",
        r"总结",
        r"提炼",
        r"压缩",
        r"精简",
        r"缩减",
        r"重组",
        r"融合",
        r"合并",
        r"改写",
        r"重新规划",
        r"提取",
        r"只要",
        r"特定主题",
        r"summarize",
        r"summary",
        r"extract",
        r"condense",
        r"rewrite",
        r"restructure",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _document_preservation_mode(documents: str, topic: str = "") -> str:
    """Choose how aggressively content planning may transform uploaded text."""
    if not documents or not documents.strip():
        return "none"
    if detect_ppt_sources(documents):
        return "ppt_source"
    if _is_general_transform_request(topic):
        return "transform"
    if len(documents) <= 14_000:
        return "faithful"
    if len(documents) <= 40_000:
        return "structured_extract"
    return "synthesis"


def _document_preservation_policy(documents: str, topic: str = "") -> str:
    mode = _document_preservation_mode(documents, topic)
    if mode == "faithful":
        return (
            "【原文保真模式】\n"
            "- 用户材料篇幅可控，默认目标是整理成 PPT，而不是重写。\n"
            "- 尽量保留原文的关键句、术语、数据、案例和表达顺序；标题可优化，但正文不要大幅改写。\n"
            "- 只有为分段、去重、纠正明显病句或适配版面时才做轻微编辑。\n"
        )
    if mode == "structured_extract":
        return (
            "【结构化摘取模式】\n"
            "- 材料较长，但仍应优先摘取原文中的关键段落和数据，不要重新发明叙事。\n"
            "- 每页正文使用原文要点的压缩版；删减只针对重复、旁枝和低信息密度内容。\n"
        )
    if mode == "synthesis":
        return (
            "【综合提炼模式】\n"
            "- 材料过长，允许按 PPT 方法论提炼主线。\n"
            "- 必须保留核心论点、专有名词、关键数字、引用和结论，避免改写用户立场。\n"
        )
    if mode == "transform":
        return (
            "【用户要求改写/提炼】\n"
            "- 可按用户目标重组材料，但不得改变事实、立场和关键术语。\n"
        )
    return ""


def infer_page_count_from_single_ppt(documents: str, topic: str = "") -> int | None:
    sources = detect_ppt_sources(documents)
    if len(sources) != 1 or _is_ppt_transform_request(topic):
        return None
    pages = int(sources[0].get("pages") or 0)
    return pages if pages > 0 else None


def _normalize_source_refs(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    refs: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        source_document = str(item.get("source_document") or item.get("filename") or "").strip()
        try:
            source_page_num = int(item.get("source_page_num") or item.get("page_num") or 0)
        except (TypeError, ValueError):
            source_page_num = 0
        if not source_document or source_page_num <= 0:
            continue
        refs.append({
            "source_document": source_document,
            "source_page_num": source_page_num,
            "source_type": str(item.get("source_type") or "pptx_slide"),
            "reason": str(item.get("reason") or item.get("usage") or "").strip(),
        })
    return refs[:8]


def _annotate_ppt_source_refs(outline: List[Dict], documents: str, topic: str = "") -> List[Dict]:
    """Keep a stable link from generated pages back to source PPT pages."""
    sources = detect_ppt_sources(documents)
    if not sources:
        return outline
    single_source = sources[0] if len(sources) == 1 else None
    direct_single_ppt_polish = bool(single_source and not _is_ppt_transform_request(topic))
    single_filename = str((single_source or {}).get("filename") or "").strip()
    single_page_count = int((single_source or {}).get("pages") or 0) if single_source else 0

    for page in outline:
        if not isinstance(page, dict):
            continue
        refs = _normalize_source_refs(page.get("source_refs"))
        page_num = int(page.get("page_num") or 0)
        if not refs and direct_single_ppt_polish and single_filename and 1 <= page_num <= single_page_count:
            refs = [{
                "source_document": single_filename,
                "source_page_num": page_num,
                "source_type": "pptx_slide",
                "reason": "single_ppt_page_polish",
            }]
        if refs:
            page["source_refs"] = refs
    return outline


def _normalize_outline_page_count(outline: List[Dict], page_count: int, strict_page_count: bool = False) -> List[Dict]:
    """Keep LLM output reasonable while treating page_count as a soft target by default."""
    if not isinstance(outline, list):
        raise ValueError("Content plan generation failed: LLM output is not a JSON array")
    target_count = max(1, int(page_count or len(outline) or 1))
    max_count = target_count if strict_page_count else _soft_page_bounds(target_count)[1]
    if len(outline) > max_count:
        logger.warning(
            f"ContentPlan: LLM returned {len(outline)} pages, trimming to max {max_count} "
            f"(target={target_count}, strict={strict_page_count})"
        )
        outline = outline[:max_count]
    for idx, page in enumerate(outline, start=1):
        if isinstance(page, dict):
            page["page_num"] = idx
    return outline


def _normalize_content_markdown(outline: List[Dict]) -> List[Dict]:
    """Normalize Markdown generated by the LLM before it becomes project state."""
    for page in outline:
        if not isinstance(page, dict):
            continue
        text_content = page.get("text_content")
        if isinstance(text_content, dict):
            for key in ("headline", "subhead", "body"):
                value = text_content.get(key)
                if isinstance(value, str):
                    text_content[key] = normalize_markdown_emphasis(value)
                elif isinstance(value, list):
                    text_content[key] = [
                        normalize_markdown_emphasis(item) if isinstance(item, str) else item
                        for item in value
                    ]
        notes = page.get("speaker_notes")
        if isinstance(notes, str):
            page["speaker_notes"] = normalize_markdown_emphasis(notes)
        _normalize_punchline_page_content(page)
    return outline


def _first_text_line(value) -> str:
    if isinstance(value, list):
        candidates = [str(item) for item in value if str(item).strip()]
    else:
        candidates = str(value or "").splitlines()
    for line in candidates:
        cleaned = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", str(line)).strip()
        cleaned = normalize_markdown_emphasis(cleaned).strip()
        if cleaned:
            return cleaned
    return ""


def _normalize_punchline_page_content(page: Dict) -> None:
    """Keep hero/quote slides as punchline slides, not lightweight content slides."""
    page_type = str(page.get("type") or "").strip().lower()
    if page_type not in {"hero", "quote"}:
        return

    text_content = page.get("text_content")
    if not isinstance(text_content, dict):
        return

    headline = _first_text_line(text_content.get("headline"))
    subhead = _first_text_line(text_content.get("subhead"))
    body = text_content.get("body")
    body_line = _first_text_line(body)

    if not headline and body_line:
        headline = body_line

    body_text = "\n".join(str(x) for x in body) if isinstance(body, list) else str(body or "")
    if body_text.strip():
        notes = str(page.get("speaker_notes") or "").strip()
        preserved = f"原金句页正文素材：{normalize_markdown_emphasis(body_text.strip())}"
        page["speaker_notes"] = f"{notes}\n\n{preserved}".strip() if notes else preserved

    text_content["headline"] = headline
    text_content["subhead"] = subhead
    text_content["body"] = ""

    suggestion = str(page.get("visual_suggestion") or "").strip()
    if not suggestion or any(term in suggestion for term in ("内容页", "信息图", "列表", "要点")):
        page["visual_suggestion"] = (
            "真正的金句页：只突出一句话、一个短语或一个词；"
            "可走引用、口号、结论、转场判断、数据洞察等方向，"
            "视觉用克制背景、象征物、人物/场景或纯色纹理承托，"
            "必须沿用整套 PPT 的配色、字体气质和材质语言。"
        )


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
    strict_page_count = _is_strict_page_count_request(topic)
    min_pages, max_pages = (page_count, page_count) if strict_page_count else _soft_page_bounds(page_count)
    logger.info(
        f"ContentPlan: 为主题 '{topic[:30]}...' 生成大纲, "
        f"page_count={page_count}, has_documents={has_docs}"
    )

    if on_progress:
        on_progress({"stage": "analyzing", "message": "正在分析主题和文档素材..."})

    exported_outline = parse_exported_content_plan_markdown(documents)
    if exported_outline and not strict_page_count and not _is_general_transform_request(topic):
        logger.info("ContentPlan: detected PPT God Markdown export, reusing %s pages directly", len(exported_outline))
        if on_progress:
            on_progress({
                "stage": "saving",
                "message": "已识别导出的内容规划，正在保存结果...",
                "current_page": len(exported_outline),
                "total_pages": len(exported_outline),
            })
        return exported_outline

    doc_section = ""
    if has_docs:
        ppt_sources = detect_ppt_sources(documents)
        preservation_policy = _document_preservation_policy(documents, topic)
        ppt_policy = ""
        if len(ppt_sources) == 1:
            ppt = ppt_sources[0]
            ppt_policy = f"""
【上传 PPT 的特殊处理】
系统识别到用户上传了 1 个 PPT：{ppt.get("filename") or "未命名 PPT"}，原始页数 {ppt.get("pages")} 页。
- 如果用户没有明确要求扩展、缩减、提取特定主题、融合或重组，你必须按原 PPT 逐页复刻内容结构：输出页数等于 {ppt.get("pages")} 页，第 i 个 JSON 页面对应原 PPT 第 i 页。
- 逐页复刻时，文字内容应尽量保留原页标题、要点、数据和备注，不要压缩成摘要，也不要重新发明叙事。
- 原页中出现的关键数字、标签、对比项、流程节点、学校/城市名单、产品规格必须进入 text_content.body；不能只放在 speaker_notes，也不能因为“每页只说一件事”而删除原页事实。
- 如果原页是信息密集页，允许 body 使用表格或分组 bullet 承载原文信息；视觉阶段会再做版式取舍，内容规划阶段不得提前丢信息。
- 每一页必须输出 source_refs: [{{"source_document": "{ppt.get("filename") or "未命名 PPT"}", "source_page_num": 原PPT页码, "reason": "polish"}}]，确保新页能追溯到原 PPT 页。
- 如果用户明确要求扩展/缩减/提取/融合/加入新想法，则按用户意图动态调整页数和结构。
"""
        elif len(ppt_sources) > 1:
            ppt_policy = """
【上传多个 PPT 的特殊处理】
系统识别到用户上传了多个 PPT。不要机械相加页数；先理解用户意图：融合、提取、重组、对比或加入新想法。除非用户指定页数，否则按内容逻辑生成合适页数。
每一页都要输出 source_refs，列出该页主要吸收了哪个源 PPT 的哪几页，格式为 [{"source_document": "文件名", "source_page_num": 页码, "reason": "为什么使用这一页"}]。如果某页是新增转场或总结，可以 source_refs 为空数组。
"""
        doc_section = f"""
【用户上传的文档素材】
{documents}

【文档使用规则】
1. 用户文档是最高优先级素材；不要用通用 PPT 方法论覆盖原文意图。
2. 文档中的关键论点、数据、结构必须体现在大纲中。
3. 页数是软目标；除非用户指定严格页数，可在 {min_pages}-{max_pages} 页范围内调整。
{preservation_policy}
{ppt_policy}
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
- 期望页数: {"必须 " + str(page_count) + " 页" if strict_page_count else f"{page_count} 页左右，可在 {min_pages}-{max_pages} 页范围内浮动"}
{doc_section}
{search_section}

【任务要求】
1. 设计清晰结构；有充分原文时以“组织原文”为主，不主动换叙事。
2. 每页必须包含：
   - page_num: 页码
   - type: 页面类型（cover/目录 toc/章节 section/content/hero/data/ending）。其中 hero 是“金句页/punchline slide”，不是普通内容页。
   - section_title: 所属章节
   - text_content.headline: 大标题（有力、简洁的断言句）
   - text_content.subhead: 副标题（可选）
   - text_content.body: 正文（markdown 格式字符串，支持加粗、列表、表格等）
     格式服从认知：对比用表格，顺序要点用列表，叙事推导用段落；不要硬套表格。
   - speaker_notes: 演讲者备注（详细论述，供演讲者参考）
   - visual_suggestion: 画面/配图建议
   - source_refs: 来源页引用数组。使用上传 PPT 内容时必须填写，元素格式为 {{"source_document": "源文件名", "source_page_num": 原页码, "reason": "使用原因"}}；纯新增页可为空数组。
3. 封面和封底各占一页。封面只定主题和语境，body 保持为空；封底只做收束、感谢、下一步或联系方式，不引入新论点。
4. 内容页不要堆砌，每页只说一件事。
   - 但当任务是“改写/美化一个已上传 PPT”时，这条不能被理解为删掉原页信息；应保留原页事实，只把同一页信息组织得更清楚。
5. 标题可优化表达，但不能改变原文判断；原文标题已经清楚时直接沿用。
6. 如果一个页面同时包含多个强画面证据/大事件，可主动拆页，而不是硬塞进同一页。
7. 普通“期望页数/约 X 页”是软目标；只有用户明确说“必须/严格/固定/只能 X 页”时才严格等于 X 页。
8. 目录页只在原文结构复杂且目录能降低理解成本时使用；不要默认插入。
9. 章节页（type="section"）只用于重大章节转换或叙事转折；它是短标题的分隔页，不承载正文论证。headline 放章节名或转场判断，body 保持为空或只放极短一句。
10. 数据页（type="data"）只在确有数字、比例、排名、时间序列、规模量级或可视化表格时使用；不要为了显得专业而编造图表。
   - data 页的 body 必须包含可被画出的真实数据点、标签或来源；如果只是定性判断，用 content 页或表格化正文。
11. 金句页只承载一句短结论、引用或转场判断；不要把普通内容页伪装成 hero。

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
    "visual_suggestion": "",
    "source_refs": []
  }}
]
"""

    client = get_llm_client()
    stream = client.chat.completions.create(
        model=get_minimax_llm_model(),
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
        on_progress({"stage": "generating", "message": "正在构建叙事结构...", "current_page": 0, "total_pages": max_pages})

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
                current_page = min(page_count_found, max_pages)
                on_progress({
                    "stage": "generating",
                    "message": f"正在生成第 {current_page}/{max_pages} 页...",
                    "current_page": current_page,
                    "total_pages": max_pages,
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

    outline = _normalize_outline_page_count(outline, page_count, strict_page_count=strict_page_count)
    outline = _normalize_content_markdown(outline)
    outline = _annotate_ppt_source_refs(outline, documents, topic)

    logger.info(f"ContentPlan: 生成完成，共 {len(outline)} 页")

    if on_progress:
        on_progress({"stage": "saving", "message": "正在保存结果...", "current_page": len(outline), "total_pages": len(outline)})

    return outline
