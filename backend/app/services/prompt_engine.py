import json
import logging
import os
import re
from typing import Dict, List, Optional

from app.core.config import settings
from app.core.llm_client import get_llm_client
from app.utils.text_cleaning import clean_llm_output
from app.utils.reference_image import reference_process_mode_instruction

logger = logging.getLogger(__name__)




def _load_template(path: str) -> str:
    """加载模板文件，如果不存在返回空字符串。"""
    if not os.path.exists(path):
        logger.warning(f"模板不存在: {path}")
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_model_facing_text(markdown: str) -> str:
    """
    从双语 Markdown 中提取发给模型的部分。
    规则：只保留英文行，过滤掉 <!-- 中文注释 --> 行。
    """
    lines = markdown.splitlines()
    filtered = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue
        filtered.append(line)
    return "\n".join(filtered)


def _strip_markdown(text: str) -> str:
    """去除常见 Markdown 标记，包括表格格式。"""
    if not text:
        return text

    lines = text.splitlines()
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        # 跳过 Markdown 表格分隔线（如 | --- | :---: |）
        if re.match(r'^\|?[\s:\-]+(?:\|[\s:\-]+)+\|?$', stripped):
            continue
        # 处理 Markdown 表格行：去掉 | 并用逗号连接单元格
        if stripped.count('|') >= 2:
            cells = [c.strip() for c in stripped.split('|')]
            # 去掉首尾空单元格（由行首行尾的 | 产生）
            cells = [c for c in cells if c]
            if cells:
                cleaned_lines.append(', '.join(cells))
            else:
                cleaned_lines.append(stripped)
        else:
            cleaned_lines.append(stripped)

    text = '\n'.join(cleaned_lines)

    # 去除加粗/斜体
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    # 去除行首列表符号和引用符号
    text = re.sub(r'^\s*[-*]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*>\s+', '', text, flags=re.MULTILINE)
    # 去除行首标题符号
    text = re.sub(r'^\s*#{1,6}\s+', '', text, flags=re.MULTILINE)
    return text.strip()


def _build_rich_brief(
    page_intent: Dict,
    style_text: str,
    layout_text: str,
    content_text: Dict,
    reference_descriptions: List[str],
) -> str:
    """
    组装 Rich Brief（给 LLM 的详细指令）。
    不是最终 Prompt，而是让 LLM 翻译的源材料。
    """
    # References 区块：有参考图时列出并说明，无参考图时只写 None，不附加任何鼓励性文字
    if reference_descriptions:
        refs_block = "\n".join([f"- {desc}" for desc in reference_descriptions])
        refs_section = f"""【References — user-uploaded images for this page】
{refs_block}
- Honor user intent and approximate placement; let the renderer handle fine details."""
    else:
        refs_section = """【References】
None"""

    def _format_body(val):
        if isinstance(val, str):
            return _strip_markdown(val)
        elif isinstance(val, list):
            return json.dumps(val, ensure_ascii=False)
        return ""

    brief = f"""【Content — 本页核心主题，决定视觉主体画什么】
Headline: {_strip_markdown(content_text.get("headline", ""))}
Subhead: {_strip_markdown(content_text.get("subhead", ""))}
Body: {_format_body(content_text.get("body"))}

【Design System】
Style:
{style_text}

Layout:
{layout_text}

【Visual Description from Director — 氛围与构图参考】
{page_intent.get("visual_description", "")}

【Design Notes】
{page_intent.get("design_notes", "")}

{refs_section}

【Requirements】
- 16:9 aspect ratio (1792x1024), landscape orientation
- Single presentation slide background image
- No logo, brand mark, watermark, UI elements, frames, or page numbers
- The visual must be a direct conceptual translation of the content topic — not generic decoration
- Magazine-quality, award-winning design
"""
    return brief


def _call_llm_for_final_prompt(rich_brief: str) -> str:
    """调用 LLM 将 Rich Brief 翻译为自然流畅的 Final Prompt。"""
    client = get_llm_client()
    response = client.chat.completions.create(
        model=settings.MINIMAX_LLM_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an elite visual designer and prompt engineer for AI image generation. "
                    "Your job is to DESIGN a single presentation slide background image — then describe it in vivid, precise, cinematic prose. "
                    "The Content section (headline, subhead, body) is your PRIMARY source for deciding the SUBJECT MATTER of the image. "
                    "The Visual Description provides the intended mood, atmosphere, and compositional direction. Translate its ESSENCE into the Final Prompt, not every decorative detail. Let the image model handle aesthetic execution — lines, shapes, lighting placement, decorative accents. Your job is to align with user intent, not to micromanage the model's artistic choices. The Design System provides color palette and typography requirements — these MUST be preserved for consistency across all slides. "
                    "REFERENCE IMAGE RULES — STRICTLY FOLLOW: "
                    "(a) If the References section lists 'None', you MUST NOT mention, describe, or incorporate ANY reference images, uploaded photos, or user-provided visual assets. Do NOT hallucinate or invent references that do not exist. "
                    "(b) ONLY when the References section explicitly describes specific images, incorporate those subjects into your design as concrete elements, not abstract replacements. "
                    "(c) When reference images specify a process mode (blend / crop / original), you MUST preserve these EXACT handling requirements in your Final Prompt. Do NOT soften, generalize, or omit the specific mode instructions. "
                    "Output ONLY the image generation prompt, no extra commentary."
                ),
            },
            {"role": "user", "content": rich_brief},
        ],
        temperature=0.3,
    )
    final = response.choices[0].message.content or ""
    final = final.strip()
    # 去掉可能的代码块包裹和 thinking 标签
    final = clean_llm_output(final)
    return final


def generate_prompt_for_page(
    page_intent: Dict,
    content_text: Dict,
    style_id: str = "default",
    reference_images: Optional[List[Dict]] = None,
    style_text_override: Optional[str] = None,
) -> str:
    """
    为一页生成 Final Image Prompt。
    输入：Visual Plan Intent + Content + Style + References
    输出：自然流畅的 Final Prompt 字符串
    """
    logger.info(f"PromptEngine: 为第 {page_intent.get('page_num')} 页生成 Final Prompt")

    # 1. 加载模板
    style_path = os.path.join("templates", "styles", f"{style_id}.md")
    # 优先使用 visual_plan 分配的具体 layout，兼容旧数据则 fallback 到 type
    layout_id = page_intent.get("layout") or page_intent.get("type", "content")
    layout_path = os.path.join("templates", "layouts", f"{layout_id}.md")
    if not os.path.exists(layout_path):
        fallback_type = page_intent.get("type", "content")
        layout_path = os.path.join("templates", "layouts", f"{fallback_type}.md")

    style_md = _load_template(style_path)
    layout_md = _load_template(layout_path)

    if style_text_override is not None:
        style_text = style_text_override
    else:
        style_text = _extract_model_facing_text(style_md)
    layout_text = _extract_model_facing_text(layout_md)

    # 2. 准备参考图描述
    reference_descriptions = []

    # 2a. 页面级参考图（从 content_text 或 page_intent 提取当前页专属 reference_context）
    # 这是最关键的：确保 Prompt Engineer 知道当前页有哪些用户上传的参考图
    ref_context = (content_text or {}).get("reference_context") or (page_intent or {}).get("reference_context")
    if ref_context:
        reference_descriptions.append(
            f"【当前页面用户上传的参考图 — CRITICAL: 这些图片会作为 actual image inputs 直接传给生图模型，"
            f"你必须在 Final Prompt 中明确描述如何保留/融合这些图片的具体视觉内容，绝不能忽略或抽象替代】\n"
            f"{ref_context}"
        )

    # 2b. 全局参考图（项目级 style_ref / template 等）
    if reference_images:
        for img in reference_images:
            role = img.get("role", "style_ref")
            desc = img.get("description", "")
            process_mode = img.get("process_mode", "")
            if role == "style_ref":
                reference_descriptions.append(
                    f"Style template image ({process_mode}): {reference_process_mode_instruction(process_mode)} "
                    f"Extract color mood and composition style. {desc}"
                )
            elif role == "content_ref":
                reference_descriptions.append(
                    f"Content reference image ({process_mode or 'blend'}): "
                    f"{reference_process_mode_instruction(process_mode)} "
                    f"{desc or 'User-provided visual reference.'} "
                    "Rough placement relative to text: follow the Visual Description from Director when it hints at layout; "
                    "otherwise balance the reference with readable text. Let the image model handle aesthetics and fine composition."
                )
            elif role == "chart_ref":
                reference_descriptions.append(
                    f"Chart reference image ({process_mode}): {reference_process_mode_instruction(process_mode)} "
                    f"Use data visualization structure and color coding. {desc}"
                )

    # 3. 组装 Rich Brief
    rich_brief = _build_rich_brief(
        page_intent=page_intent,
        style_text=style_text,
        layout_text=layout_text,
        content_text=content_text,
        reference_descriptions=reference_descriptions,
    )

    # 4. 调用 LLM 翻译
    final_prompt = _call_llm_for_final_prompt(rich_brief)

    # 5. 强制追加文字渲染指令（确保文字一定出现在图片上）
    # 外层用单引号包裹用户文本，避免与用户文本中的双引号冲突
    # 同时去除 Markdown 标记（**、- 等），避免模型把格式符号也渲染到图上
    def _escape(text: str) -> str:
        # 只处理会破坏 prompt 结构的反斜杠，保留用户原始引号
        return text.replace("\\", "")

    # 从 style_text 中提取字体要求，强制统一字体
    # 优先识别 "Font:" 行，其次从 "Typography Feel" 中提取字体关键词
    font_spec = ""
    for line in (style_text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("Font:"):
            font_spec = stripped.replace("Font:", "").strip()
            break
    if not font_spec:
        # 从 Typography Feel 中提取 serif / sans-serif 等字体关键词
        tf_text = ""
        in_tf = False
        for line in (style_text or "").splitlines():
            if "Typography Feel" in line or "排版感" in line:
                in_tf = True
            if in_tf:
                tf_text += line + " "
                if line.strip().endswith("-->"):
                    in_tf = False
        import re as _re
        font_match = _re.search(r'(serif|sans-serif|monospace|Display|Script|Slab)', tf_text, _re.IGNORECASE)
        if font_match:
            font_spec = font_match.group(1)

    text_directives = []
    if content_text.get("headline"):
        h = _escape(_strip_markdown(content_text["headline"]))
        text_directives.append(f'Headline: "{h}" must appear on the slide')
    if content_text.get("subhead"):
        s = _escape(_strip_markdown(content_text["subhead"]))
        text_directives.append(f'Subhead: "{s}" must appear on the slide')
    body = content_text.get("body")
    if body:
        if isinstance(body, str):
            lines = [line.strip() for line in body.splitlines() if line.strip()]
            for item in lines:
                cleaned = _escape(_strip_markdown(item))
                if cleaned:
                    text_directives.append(f'Body text: "{cleaned}" must appear on the slide')
        else:
            for item in body:
                cleaned = _escape(_strip_markdown(item))
                if cleaned:
                    text_directives.append(f'Body text: "{cleaned}" must appear on the slide')

    if text_directives:
        text_directives.append("ALL listed text must appear on the slide, clearly rendered, highly legible.")

    # 强制统一字体（如果 style 中指定了）
    if font_spec:
        text_directives.append(
            f'FONT REQUIREMENT — ALL text MUST use "{font_spec}" font family ONLY. '
            'Consistent typography across the entire slide. Do NOT mix fonts.'
        )

    if text_directives:
        # 把文字指令放到 Prompt 最前面，强化排他性：只允许渲染列表里的文字，
        # 视觉描述里的英文标签不许被画到图上。
        text_block = "\n".join(text_directives)
        final_prompt = (
            "CRITICAL — ONLY the exact text lines listed below may appear on the slide. "
            "DO NOT render any other words, labels, or text from the visual description. "
            "You decide the best placement, size, and color for maximum design impact and readability:\n\n"
            + text_block
            + "\n\n"
            + final_prompt
        )

    prompt_len = len(final_prompt)
    if prompt_len > 3000:
        logger.warning(f"PromptEngine: 第 {page_intent.get('page_num')} 页 Prompt 过长 ({prompt_len} chars)，可能超出模型有效上下文窗口")
    logger.info(f"PromptEngine: 第 {page_intent.get('page_num')} 页 Prompt 生成完成，长度 {prompt_len}")
    return final_prompt


def generate_prompts_for_all_pages(
    visual_plan: List[Dict],
    content_plan: List[Dict],
    style_id: str = "default",
    reference_images: Optional[List[Dict]] = None,
    style_text_override: Optional[str] = None,
    progress_callback: Optional[callable] = None,
) -> List[Dict]:
    """
    为所有页面批量生成 Final Prompt。
    返回每页的 {page_num, prompt} 列表。
    """
    results = []
    total = len(visual_plan)
    # 建立 content_plan 索引（保留完整 item，不只是 text_content）
    content_item_by_page = {item.get("page_num", 0): item for item in content_plan}

    for idx, intent in enumerate(visual_plan):
        page_num = intent.get("page_num", 0)
        if progress_callback:
            progress_callback(f"📝 第 {idx + 1} / {total} 页 Prompt 生成中...")
        content_item = content_item_by_page.get(page_num, {})
        content_text = content_item.get("text_content", {}) or {}
        # 注入页面级参考图上下文（修复参考图丢失）
        if content_item.get("reference_context"):
            content_text = {**content_text, "reference_context": content_item["reference_context"]}
        if content_item.get("reference_user_hint"):
            content_text = {**content_text, "reference_user_hint": content_item["reference_user_hint"]}
        prompt = generate_prompt_for_page(
            page_intent=intent,
            content_text=content_text,
            style_id=style_id,
            reference_images=reference_images,
            style_text_override=style_text_override,
        )
        results.append({"page_num": page_num, "prompt": prompt})

    logger.info(f"PromptEngine: 全部 {len(results)} 页 Prompt 生成完成")
    return results
