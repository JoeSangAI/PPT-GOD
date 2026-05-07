import json
import logging
import os
import re
from typing import Dict, List, Optional

from app.core.config import settings
from app.core.llm_client import get_llm_client
from app.core.provider_credentials import get_minimax_llm_model
from app.services.logo_policy import logo_prompt_instruction, logo_reservation_instruction
from app.services.style_pack import derive_style_pack_from_content
from app.utils.text_cleaning import clean_llm_output, normalize_markdown_emphasis

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
    """去除常见 Markdown 标记，保留表格结构供生图模型识别。"""
    if not text:
        return text
    text = normalize_markdown_emphasis(text)

    lines = text.splitlines()
    cleaned_lines = []
    in_table = False
    for line in lines:
        stripped = line.strip()
        # 跳过 Markdown 表格分隔线（如 | --- | :---: |）
        if re.match(r'^\|?[\s:\-]+(?:\|[\s:\-]+)+\|?$', stripped):
            continue
        # 表格行：保留管道符结构，生图模型需要识别为表格
        if stripped.count('|') >= 2:
            cells = [c.strip() for c in stripped.split('|')]
            # 去掉首尾空单元格（由行首行尾的 | 产生）
            cells = [c for c in cells if c]
            if cells:
                if not in_table:
                    cleaned_lines.append("[表格]")
                    in_table = True
                cleaned_lines.append(' | '.join(cells))
            else:
                cleaned_lines.append(stripped)
        else:
            in_table = False
            cleaned_lines.append(stripped)

    text = '\n'.join(cleaned_lines)

    # 去除加粗/斜体
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    # 去除残余的未配对强调符，避免进入图片模型的文字合同。
    text = text.replace("**", "").replace("__", "")
    # 去除行首列表符号和引用符号
    text = re.sub(r'^\s*[-*]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*>\s+', '', text, flags=re.MULTILINE)
    # 去除行首标题符号
    text = re.sub(r'^\s*#{1,6}\s+', '', text, flags=re.MULTILINE)
    return text.strip()


def _compact_style_pack(style_text: str, max_lines: int = 4, max_chars: int = 600) -> str:
    """Keep the global style useful but short so page evidence stays dominant."""
    if not style_text:
        return derive_style_pack_from_content([])
    lines = [line.strip() for line in style_text.splitlines() if line.strip()]
    priority: list[str] = []
    # Keep only high-level style language. Palette, typography, and reference
    # metadata are intentionally not expanded into hard locks here; the first
    # image pass should stay short and let uploaded references carry identity.
    keywords = (
        "Style:", "Mood:", "Visual rhythm:",
    )
    for line in lines:
        if line.startswith(keywords):
            cleaned = _remove_negative_clauses(line)
            if cleaned:
                priority.append(cleaned)
    compact_lines = (priority or lines)[:max_lines]
    compact = "\n".join(compact_lines)
    if len(compact) > max_chars:
        compact = compact[:max_chars].rstrip() + "..."
    return compact


_NEGATIVE_PROMPT_MARKERS = (
    "不要", "不得", "禁止", "严禁", "避免", "不允许", "不能",
)

_PRODUCT_DETAIL_MARKERS = (
    "5升", "5L", "桶身", "瓶身", "瓶盖", "瓶颈", "提手", "吊牌",
    "标签", "标贴", "红底", "金边", "书法字体", "非遗", "透明",
    "金黄", "金色", "包装文字", "完整保留", "完整展示",
    "产品", "产品实物", "产品图", "产品照片", "包装", "瓶型", "花生油产品", "胡姬花花生油",
    "胡姬花古法花生油", "古法香", "品牌标识", "品牌资产", "品牌识别",
    "文化符号", "品质背书", "视觉锚点",
)


def _split_clauses(text: str) -> list[str]:
    """Split loosely on punctuation while keeping useful short clauses."""
    return [part.strip(" ，,。；;") for part in re.split(r"[。；;]\s*", str(text or "")) if part.strip()]


def _split_product_clauses(text: str) -> list[str]:
    """Product-related cleanup needs finer cuts than normal copy."""
    return [part.strip(" ：:，,。；;") for part in re.split(r"[。；;，,]\s*", str(text or "")) if part.strip()]


def _remove_negative_clauses(text: str) -> str:
    clauses = _split_clauses(text)
    if not clauses:
        return str(text or "").strip()
    kept = [
        clause for clause in clauses
        if not any(marker in clause for marker in _NEGATIVE_PROMPT_MARKERS)
    ]
    return "；".join(kept).strip()


def _is_product_ref(ref: Dict) -> bool:
    if (ref or {}).get("role") != "visual_asset":
        return False
    return str((ref or {}).get("asset_kind") or "").lower() in {"product", "material"}


def _is_punchline_page(page_intent: Dict) -> bool:
    page_type = str((page_intent or {}).get("type") or "").strip().lower()
    layout = str((page_intent or {}).get("layout") or "").strip().lower()
    return page_type in {"hero", "quote"} or layout == "hero"


def _has_product_ref(reference_images: Optional[List[Dict]]) -> bool:
    return any(_is_product_ref(ref) for ref in reference_images or [])


def _product_placement_instruction(text: str) -> str:
    """Convert noisy product placement prose into one compact model-facing line."""
    raw = str(text or "")
    position = ""
    if any(marker in raw for marker in ("中央偏左", "视觉中心偏左", "中心偏左")):
        position = "center-left"
    elif any(marker in raw for marker in ("中央偏右", "视觉中心偏右", "中心偏右")):
        position = "center-right"
    elif any(marker in raw for marker in ("中央偏下", "中下", "画面下方中央")):
        position = "lower center"
    elif "右下角" in raw:
        position = "bottom-right"
    elif "左下角" in raw:
        position = "bottom-left"
    elif "左上角" in raw:
        position = "top-left"
    elif "右上角" in raw:
        position = "top-right"
    elif any(marker in raw for marker in ("居中", "中央", "视觉中心", "居中展示")):
        position = "center"
    elif "时间轴下方" in raw:
        position = "below the timeline"
    elif "右侧" in raw and any(marker in raw for marker in ("放置", "放在", "置于", "展示")):
        position = "right side"
    elif "左侧" in raw and any(marker in raw for marker in ("放置", "放在", "置于", "展示")):
        position = "left side"
    elif any(marker in raw for marker in ("侧边", "页面边缘", "信息区边缘")):
        position = "a side area"
    if not position:
        return ""

    scale = ""
    if any(marker in raw for marker in ("次要", "小区域", "补充露出", "无需占据过大")):
        scale = "small secondary"
    elif any(marker in raw for marker in ("核心", "主视觉", "视觉锚点", "视觉重心")):
        scale = "large unobstructed"

    scale_prefix = f"{scale} " if scale else ""
    return f"Place the uploaded product image in the {scale_prefix}{position} area."


def _sanitize_product_reference_text(text: str) -> str:
    """
    Keep only scene/layout language and generic placement. The uploaded image
    carries product identity; text must not reconstruct or embellish it.
    """
    cleaned: list[str] = []
    for clause in _split_product_clauses(text):
        clause = _remove_negative_clauses(clause)
        if not clause:
            continue
        if any(marker in clause for marker in _PRODUCT_DETAIL_MARKERS):
            placement = _product_placement_instruction(clause)
            if placement and placement not in cleaned:
                cleaned.append(placement)
            continue
        cleaned.append(clause)
    return "；".join(cleaned).strip()


def _compact_visual_evidence(page_intent: Dict, reference_images: Optional[List[Dict]] = None) -> str:
    visual_evidence = str(page_intent.get("visual_evidence", "") or "").strip()
    if _has_product_ref(reference_images):
        visual_evidence = _sanitize_product_reference_text(visual_evidence)
    return visual_evidence or "Use the uploaded product image as the product source, with supporting visuals derived from this slide's content."


def _compact_layout_intent(page_intent: Dict, reference_images: Optional[List[Dict]] = None) -> str:
    layout = page_intent.get("layout") or page_intent.get("type", "content")
    visual_desc = " ".join(str(page_intent.get("visual_description", "")).split())
    visual_desc = _remove_negative_clauses(visual_desc)
    if _has_product_ref(reference_images):
        visual_desc = _sanitize_product_reference_text(visual_desc)
    if len(visual_desc) > 260:
        visual_desc = visual_desc[:260].rstrip() + "..."
    if visual_desc:
        return f"Layout: {layout}. {visual_desc}"
    return f"Layout: {layout}. Arrange text and visual evidence with clear hierarchy and strong readability."


def _compact_reference_text(text: str, max_chars: int = 260) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def _is_protected_asset(ref: Dict) -> bool:
    """Identity-locked assets that must NOT be redrawn or reinterpreted."""
    role = (ref or {}).get("role", "")
    if role == "logo":
        return True
    if _is_product_ref(ref):
        return True
    return False


def _protected_asset_priority(ref: Dict) -> int:
    if _is_product_ref(ref):
        return 0
    if (ref or {}).get("role") == "logo":
        return 1
    return 9


def _reference_priority(ref: Dict) -> int:
    role = (ref or {}).get("role", "")
    if _is_product_ref(ref):
        return 0
    if role == "logo":
        return 1
    if role in {"content_ref", "chart_ref"}:
        return 2
    if role == "visual_asset":
        return 3
    if role == "seed_ref":
        return 4
    if role == "template":
        return 5
    return 9


def _protected_assets_block(reference_images: Optional[List[Dict]]) -> str:
    if not reference_images:
        return ""
    protected = sorted(
        [ref for ref in reference_images if _is_protected_asset(ref)],
        key=_protected_asset_priority,
    )
    if not protected:
        return ""

    lines = []
    for idx, ref in enumerate(protected, start=1):
        role = ref.get("role", "")
        if role == "logo":
            label = "Logo"
            rule = (
                "use the exact uploaded mark as a small, quiet brand signature."
            )
        elif _is_product_ref(ref):
            label = ref.get("asset_name") or "Product"
            rule = (
                "use the uploaded product image as the product source; a hidden refinement pass strengthens fidelity."
            )
        lines.append(f"{idx}. {label} — {rule}")

    return "Assets:\n" + "\n".join(lines)


def _reference_descriptions_for_prompt(
    page_intent: Dict,
    content_text: Dict,
    reference_images: Optional[List[Dict]],
) -> list[str]:
    reference_descriptions: list[str] = []
    ref_context = (content_text or {}).get("reference_context") or (page_intent or {}).get("reference_context")
    if ref_context:
        reference_descriptions.append(
            "Page reference: follow this uploaded visual. "
            + str(ref_context)
        )

    if reference_images:
        for img in sorted(reference_images, key=_reference_priority):
            role = img.get("role", "style_ref")
            desc = img.get("description", "")
            process_mode = img.get("process_mode", "")
            if role == "style_ref":
                reference_descriptions.append(
                    "Style reference: borrow only mood, palette, and composition rhythm."
                )
            elif role == "logo":
                logo_instruction = logo_prompt_instruction(page_intent)
                reference_descriptions.append(
                    f"Logo: exact uploaded mark. {logo_instruction}"
                )
            elif role == "content_ref":
                reference_descriptions.append(
                    "Page reference: use this uploaded image as the page visual source."
                )
            elif role == "chart_ref":
                reference_descriptions.append(
                    "Chart/data reference: follow this uploaded chart for the chart area."
                )
            elif role == "visual_asset":
                asset_name = img.get("asset_name") or "visual asset"
                asset_kind = img.get("asset_kind") or "other"
                usage_map = page_intent.get("visual_asset_usage") if isinstance(page_intent, dict) else {}
                page_usage = ""
                if isinstance(usage_map, dict) and img.get("id") in usage_map:
                    page_usage = str(usage_map.get(img.get("id")) or "")
                if str(asset_kind).lower() in {"product", "material"}:
                    rule = (
                        f"Product slot: {asset_name}. Use the uploaded product image as the product source; product fidelity is reinforced in a hidden refinement pass."
                    )
                else:
                    rule = (
                        f"Visual asset: {asset_name}. Use the uploaded image as the visual source."
                    )
                if page_usage:
                    placement = _sanitize_product_reference_text(page_usage) if str(asset_kind).lower() in {"product", "material"} else _remove_negative_clauses(page_usage)
                    if placement:
                        placement_text = _compact_reference_text(placement, 100).rstrip(".")
                        rule += f" Placement/use: {placement_text}."
                reference_descriptions.append(rule)
            elif role == "seed_ref":
                reference_descriptions.append(
                    "Seed page: copy layout DNA only (grid, hierarchy, palette rhythm). "
                    "Do not copy seed text, body imagery, product shots, or logo unless this page has its own uploaded logo."
                )
    return [line.strip() for line in reference_descriptions if line and line.strip()]


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

    visual_evidence = page_intent.get("visual_evidence", "") or "Use the strongest concrete visual evidence implied by this slide's content."
    style_pack = _compact_style_pack(style_text)
    logo_reservation = logo_reservation_instruction(page_intent)
    logo_section = f"\n【Logo Overlay Reservation】\n{logo_reservation}\n" if logo_reservation else ""

    brief = f"""【Content — 本页核心主题，决定视觉主体画什么】
Headline: {_strip_markdown(content_text.get("headline", ""))}
Subhead: {_strip_markdown(content_text.get("subhead", ""))}
Body: {_format_body(content_text.get("body"))}

【Project Style Pack — 全局一致性约束，保持简短执行】
{style_pack}

【Visual Evidence — 本页必须画出的配图对象/商业证据】
{visual_evidence}

【Layout Intent】
{layout_text}

【Visual Description from Director — 围绕画面证据组织画面】
{page_intent.get("visual_description", "")}

【Design Notes】
{page_intent.get("design_notes", "")}

{refs_section}
{logo_section}

【Requirements】
- 16:9 aspect ratio (1792x1024), landscape orientation
- Single presentation slide background image
- No watermark, UI elements, frames, or page numbers
- Do not invent or redraw brand marks. If a protected user-uploaded Logo is listed in References, integrate that exact logo with high fidelity.
- The visual must make the Visual Evidence visible. Do not replace it with generic decoration.
- Use style as a wrapper, not the subject. Page content decides the scene/object/chart.
- Magazine-quality, award-winning design
"""
    return brief


def _call_llm_for_final_prompt(rich_brief: str) -> str:
    """调用 LLM 将 Rich Brief 翻译为自然流畅的 Final Prompt。"""
    client = get_llm_client()
    response = client.chat.completions.create(
        model=get_minimax_llm_model(),
        messages=[
            {
                "role": "system",
                "content": (
                    "Write one concise image-generation prompt for a 16:9 presentation slide. "
                    "Use the slide text and Visual Evidence as the subject; use style only as visual direction. "
                    "Mention only necessary layout, references, and constraints. Output only the prompt."
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

    if style_text_override is not None:
        style_text = style_text_override
    elif isinstance(page_intent, dict) and page_intent.get("style_pack_snapshot"):
        style_text = str(page_intent.get("style_pack_snapshot") or "")
    else:
        style_text = derive_style_pack_from_content([
            {"type": page_intent.get("type", "content"), "text_content": content_text or {}}
        ])

    reference_descriptions = _reference_descriptions_for_prompt(page_intent, content_text or {}, reference_images)

    # 强制追加文字渲染指令（确保文字一定出现在图片上）
    # 外层用单引号包裹用户文本，避免与用户文本中的双引号冲突
    # 同时去除 Markdown 标记（**、- 等），避免模型把格式符号也渲染到图上
    def _escape(text: str) -> str:
        # 只处理会破坏 prompt 结构的反斜杠，保留用户原始引号
        return text.replace("\\", "")

    text_directives = []
    is_punchline_page = _is_punchline_page(page_intent)
    if content_text.get("headline"):
        h = _escape(_strip_markdown(content_text["headline"]))
        text_directives.append(f'Headline: "{h}"')
    if content_text.get("subhead"):
        s = _escape(_strip_markdown(content_text["subhead"]))
        text_directives.append(f'Subhead: "{s}"')
    body = content_text.get("body")
    if body and not is_punchline_page:
        if isinstance(body, str):
            lines = [line.strip() for line in body.splitlines() if line.strip()]
            for item in lines:
                cleaned = _escape(_strip_markdown(item))
                if cleaned:
                    text_directives.append(f'Body: "{cleaned}"')
        else:
            for item in body:
                cleaned = _escape(_strip_markdown(item))
                if cleaned:
                    text_directives.append(f'Body: "{cleaned}"')

    if text_directives:
        text_directives.append("All listed text must appear, clearly rendered and highly legible.")

    punchline_treatment = ""
    if is_punchline_page:
        punchline_treatment = (
            "Punchline slide treatment: render only one dominant short line/phrase/word plus minimal context if useful; "
            "do not add bullets, explanatory body copy, charts, dense panels, or unrelated typography. "
            "Use the same project typeface feel, palette, material texture, and decoration language, only with stronger scale and negative space."
        )

    protected_block = _protected_assets_block(reference_images)
    protected_section = f"{protected_block}\n\n" if protected_block else ""

    if text_directives:
        # Keep the first-pass prompt compact: text contract, optional logo rule,
        # style, page intent, and short reference roles. Uploaded images carry
        # asset identity; long product descriptions are intentionally omitted.
        text_block = "\n".join(text_directives)
        style_block = _compact_style_pack(style_text)
        visual_evidence = _compact_visual_evidence(page_intent, reference_images)
        layout_intent = _compact_layout_intent(page_intent, reference_images)
        refs_block = "\n".join(f"- {desc}" for desc in reference_descriptions[:6])
        refs_section = f"\n\nReferences:\n{refs_block}" if refs_block else ""
        logo_reservation = logo_reservation_instruction(page_intent)
        logo_section = f"\n\nLogo Overlay Reservation:\n{logo_reservation}" if logo_reservation else ""
        final_prompt = (
            "Text:\n"
            + text_block
            + "\n\n"
            + protected_section
            + (refs_section.strip() + "\n\n" if refs_section else "")
            + "Style:\n"
            + style_block
            + "\n\n"
            + "Visual:\n"
            + str(visual_evidence)
            + "\n\n"
            + "Layout:\n"
            + str(layout_intent)
            + (f"\n{punchline_treatment}" if punchline_treatment else "")
            + logo_section
            + "\n\n"
            + "Render one 16:9 presentation slide. Keep text legible."
        )
    else:
        style_block = _compact_style_pack(style_text)
        visual_evidence = _compact_visual_evidence(page_intent, reference_images)
        layout_intent = _compact_layout_intent(page_intent, reference_images)
        refs_block = "\n".join(f"- {desc}" for desc in reference_descriptions[:6])
        refs_section = f"\n\nReferences:\n{refs_block}" if refs_block else ""
        logo_reservation = logo_reservation_instruction(page_intent)
        logo_section = f"\n\nLogo Overlay Reservation:\n{logo_reservation}" if logo_reservation else ""
        final_prompt = (
            protected_section
            + (refs_section.strip() + "\n\n" if refs_section else "")
            + "Style:\n"
            + style_block
            + "\n\nVisual:\n"
            + str(visual_evidence)
            + "\n\nLayout:\n"
            + str(layout_intent)
            + (f"\n{punchline_treatment}" if punchline_treatment else "")
            + logo_section
            + "\n\nRender one 16:9 presentation slide."
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
    reference_images_by_page: Optional[Dict[int, List[Dict]]] = None,
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
            reference_images=(reference_images_by_page or {}).get(page_num, reference_images),
            style_text_override=style_text_override,
        )
        results.append({"page_num": page_num, "prompt": prompt})

    logger.info(f"PromptEngine: 全部 {len(results)} 页 Prompt 生成完成")
    return results
