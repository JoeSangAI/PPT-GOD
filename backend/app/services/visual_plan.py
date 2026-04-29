import json
import logging
import os
from typing import Dict, List, Optional

from app.core.config import settings
from app.core.llm_client import get_llm_client

logger = logging.getLogger(__name__)


def _load_style(style_id: str) -> Dict:
    """加载风格模板，解析 frontmatter 和正文。"""
    path = os.path.join("templates", "styles", f"{style_id}.md")
    if not os.path.exists(path):
        logger.warning(f"风格模板 {style_id} 不存在，使用 default")
        path = os.path.join("templates", "styles", "default.md")

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # 简单解析 frontmatter
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            meta_text = parts[1].strip()
            body = parts[2].strip()
            import yaml

            meta = yaml.safe_load(meta_text) or {}
            return {"meta": meta, "body": body}

    return {"meta": {}, "body": content}


def _assign_layout(page_type: str, body_count: int = 0, headline: str = "", subhead: str = "") -> str:
    """根据页面类型和内容特征分配合适的 layout。"""
    mapping = {
        "cover": "cover",
        "toc": "toc",
        "hero": "hero",
        "data": "data",
        "ending": "ending",
    }
    if page_type in mapping:
        return mapping[page_type]

    # content 类型根据内容特征多样化分配，避免所有页面都是同一版式
    if body_count > 6:
        # 文字极多：以文字为主，背景氛围为辅
        return "content_dense"
    if body_count <= 2 and len(headline) < 25 and not subhead:
        # 短标题、极少正文、无副标题：视觉可以占主导，全幅沉浸
        return "content_hero"
    if body_count <= 3 and len(headline) > 15:
        # 中等标题、少量正文：上下结构，顶部大视觉
        return "content_top"
    # 默认：分栏布局，但强调视觉与文字平等，视觉是内容翻译而非配图
    return "content_split"


def _infer_seed_family(page_type: str) -> str:
    """推断种子家族，用于视觉一致性。"""
    if page_type in ("cover", "ending"):
        return "bookend"
    if page_type in ("hero",):
        return "hero"
    if page_type in ("toc",):
        return "section"
    return "content"


def _build_batch_prompt(pages_summary: List[Dict], style: Dict, batch_num: int = 1, total_batches: int = 1) -> str:
    """构建批量生成 visual_description 的 LLM prompt。"""
    palette = style["meta"].get("palette", ["#1E3A5F", "#F5F5F0"])
    theme = "Modern business presentation"
    mood = style["meta"].get("mood", "Professional, clean, confident")
    description = style.get("body", "")

    batch_hint = f"这是第 {batch_num}/{total_batches} 批。" if total_batches > 1 else ""

    # 隔离参考图：每页只保留自己的 reference_context，防止 LLM 跨页引用
    sanitized_pages = []
    has_any_ref = False
    for p in pages_summary:
        page_copy = dict(p)
        if (page_copy.get("reference_context") or "").strip():
            has_any_ref = True
        else:
            page_copy.pop("reference_context", None)
            page_copy.pop("reference_user_hint", None)
        sanitized_pages.append(page_copy)

    ref_instruction = ""
    if has_any_ref:
        ref_instruction = """
【含参考图页面的处理规则】
部分页面的数据中包含 reference_context 字段，表示该页有用户上传的参考图。
⚠️ 严禁跨页引用：每页的 reference_context 只属于该页，绝对不能出现在其他页的 visual_description 中。
没有 reference_context 的页面，visual_description 中不得提及任何参考图、用户上传图片、或其他页面的视觉素材。

对于含 reference_context 的页面，请在 visual_description 中用自然中文写清：
1. 参考图里大概是什么（一句话概括）
2. 大致放在版面哪个区域、和文字怎么分工
3. blend/crop/original 处理意图（一句人话）
字数约 90–150 字。

"""

    prompt = f"""你是一位顶级 PPT 视觉总监。为以下每一页 PPT 生成视觉意图（visual intent）。

{batch_hint}
【视觉风格】
主题：{theme}
氛围：{mood}
配色：{', '.join(str(c) for c in palette[:5])}
{f"描述：{description}" if description else ""}

【大纲（{len(sanitized_pages)} 页）】
{json.dumps(sanitized_pages, ensure_ascii=False, indent=2)}
{ref_instruction}
【任务】
为每一页生成两个字段：
1. visual_summary：一句话画面意向（20-30字；有参考图时 20-35 字），用于全局预览页快速理解。如"全屏深色背景，中央 DNA 双螺旋光纹"
2. visual_description：画面方案文案——**给用户阅读**，也会进入下游 pipeline。**不含**任何必须在页面上逐字渲染的正文（正文由单独约束）。
   - 无参考图：约 80–120 字，背景基调、意境、主视觉意象、配色语气（可参考风格系统）。
   - 含参考图：见上文规则。

【质量标准】
1. visual_summary 必须极简，只说「画面是什么」（有参考图时可点出「基于用户参考路线图」等）。
2. visual_description 与页面主题强相关；禁止空洞套话；有参考图时**必须**让读者看出参考素材是什么、大致摆在哪，而不是只写氛围。
3. 必须体现风格系统的配色和调性（无参考图页尤其重要）
4. 如果 existing_visual_suggestion 有内容，以它为基础扩展；如果为空，根据内容自行设计
5. 不要规定具体字体名与字号；不要写出必须在页面上出现的正文原句。
6. 颜色出现时，尽量带 HEX，如"深墨蓝(#0A1628)"
7. ⚠️ 每页的 visual_description 只能描述该页自己的画面。严禁引用其他页面的参考图或视觉元素。

【输出格式】
严格输出 JSON 对象，key 为 page_num（字符串），value 为对象：
{{"1": {{"visual_summary": "一句话意向...", "visual_description": "详细描述..."}}, "2": {{...}}, ...}}

为当前批次的每一页都生成，不要遗漏。"""
    return prompt


def _fallback_visual_plan(content_plan: List[Dict], reference_image_ids: List[str]) -> List[Dict]:
    """返回安全的 fallback visual plan，保证 pipeline 不中断。"""
    visual_plan = []
    for page in content_plan:
        page_type = page.get("type", "content")
        body = page.get("text_content", {}).get("body", "")
        body_count = 0
        if isinstance(body, str):
            body_count = len([l for l in body.splitlines() if l.strip()])
        elif isinstance(body, list):
            body_count = len(body)
        text_content = page.get("text_content", {})
        headline = text_content.get("headline", "")
        visual_plan.append({
            "page_num": page.get("page_num", 0),
            "type": page_type,
            "layout": _assign_layout(page_type, body_count, headline, text_content.get("subhead", "")),
            "seed_family": _infer_seed_family(page_type),
            "visual_summary": f"现代商务风格画面，与「{headline}」主题相关",
            "visual_description": f"现代商务风格画面，背景使用主色调，与「{headline}」主题相关的视觉元素",
            "design_notes": f"种子家族: {_infer_seed_family(page_type)}，布局: {_assign_layout(page_type, body_count)}",
            "reference_image_ids": reference_image_ids or [],
            "is_seed_recommended": False,
        })
    # 推荐种子页
    family_first_pages = {}
    for intent in visual_plan:
        family = intent["seed_family"]
        page_num = intent["page_num"]
        if family not in family_first_pages or page_num < family_first_pages[family]["page_num"]:
            family_first_pages[family] = intent
    for intent in visual_plan:
        intent["is_seed_recommended"] = intent in family_first_pages.values()
    return visual_plan


def _do_generate_visual_plan(
    content_plan: List[Dict],
    style_id: str = "default",
    reference_image_ids: Optional[List[str]] = None,
    style_override: Optional[Dict] = None,
    progress_callback: Optional[callable] = None,
) -> List[Dict]:
    """generate_visual_plan 的实际实现（不含异常捕获）。"""
    style = _load_style(style_id)
    if style_override:
        style["meta"].update(style_override.get("meta", {}))
        if style_override.get("body"):
            style["body"] = style_override["body"]

    # 1. 准备 batch prompt 的页面摘要
    pages_summary = []
    for page in content_plan:
        text = page.get("text_content", {})
        summary = {
            "page_num": page.get("page_num", 0),
            "type": page.get("type", "content"),
            "headline": text.get("headline", ""),
            "subhead": text.get("subhead", ""),
            "body_preview": text.get("body", [])[:3] if isinstance(text.get("body"), list) else (text.get("body", "")[:120] if isinstance(text.get("body"), str) else ""),
            "existing_visual_suggestion": page.get("visual_suggestion", ""),
            "reference_context": page.get("reference_context", ""),
            "reference_user_hint": page.get("reference_user_hint", ""),
        }
        pages_summary.append(summary)

    # 2. 分批调用 LLM 生成 visual_description，避免单 prompt 过长导致超时
    BATCH_SIZE = 5
    total_pages = len(pages_summary)
    descriptions: Dict[str, Dict] = {}
    client = get_llm_client()
    import re

    for batch_idx in range(0, total_pages, BATCH_SIZE):
        batch = pages_summary[batch_idx : batch_idx + BATCH_SIZE]
        batch_num = batch_idx // BATCH_SIZE + 1
        total_batches = (total_pages + BATCH_SIZE - 1) // BATCH_SIZE
        ref_pages_in_batch = [p["page_num"] for p in batch if (p.get("reference_context") or "").strip()]
        logger.info(f"VisualPlan: batch {batch_num}/{total_batches}, pages={[p['page_num'] for p in batch]}, ref_pages={ref_pages_in_batch}")
        if progress_callback:
            progress_callback(f"🧠 正在生成视觉方案（批次 {batch_num}/{total_batches}）...")

        prompt = _build_batch_prompt(batch, style, batch_num=batch_num, total_batches=total_batches)

        try:
            response = client.chat.completions.create(
                model=settings.MINIMAX_LLM_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": "你是世界一流的 PPT 视觉总监。必须且只能输出合法的 JSON 对象，严禁添加任何额外说明文本。",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.5,
            )
            raw = response.choices[0].message.content or ""
            raw = raw.strip()
            logger.info(f"VisualPlan: batch {batch_num}/{total_batches} raw length={len(raw)}")

            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            raw = re.sub(r"^```(?:json)?\s*|```$", "", raw, flags=re.MULTILINE | re.IGNORECASE).strip()

            if raw:
                try:
                    batch_descriptions = json.loads(raw)
                    if isinstance(batch_descriptions, dict):
                        descriptions.update(batch_descriptions)
                    else:
                        logger.warning(f"VisualPlan: batch {batch_num} 返回非 dict: {type(batch_descriptions)}")
                except json.JSONDecodeError as e:
                    logger.warning(f"VisualPlan: batch {batch_num} JSON 解析失败: {e}，raw前100字: {raw[:100]}")
            else:
                logger.warning(f"VisualPlan: batch {batch_num} 返回空内容")
        except Exception as e:
            logger.error(f"VisualPlan: batch {batch_num}/{total_batches} 调用失败: {e}")
            # 单批次失败不影响其他批次，继续执行

    # 3. 组装 Visual Plan Intent
    visual_plan = []
    for page in content_plan:
        page_num = str(page.get("page_num", 0))
        page_type = page.get("type", "content")
        body = page.get("text_content", {}).get("body", "")
        # 兼容旧数据：body 可能是 list，也可能是 string
        body_count = 0
        if isinstance(body, str):
            body_count = len([l for l in body.splitlines() if l.strip()])
        elif isinstance(body, list):
            body_count = len(body)

        desc_data = descriptions.get(page_num, {})
        if isinstance(desc_data, dict):
            visual_summary = desc_data.get("visual_summary", "")
            visual_desc = desc_data.get("visual_description", "")
        else:
            # 兼容旧格式：直接是字符串
            visual_summary = ""
            visual_desc = str(desc_data) if desc_data else ""

        if not visual_desc:
            logger.warning(f"VisualPlan: 第 {page_num} 页缺失 visual_description，使用默认")
            visual_desc = f"现代商务风格画面，背景使用主色调，与「{page.get('text_content', {}).get('headline', '')}」主题相关的视觉元素"

        hint = (page.get("reference_user_hint") or "").strip()
        if hint:
            # 用户可见区必须出现参考图识别摘要；LLM 常与页面主题「跑题」而忽略参考图
            visual_desc = f"{hint}\n\n{visual_desc.strip()}".strip()
            logger.info(f"VisualPlan: 第 {page_num} 页已注入 reference_user_hint，visual_desc 长度={len(visual_desc)}")
        else:
            logger.info(f"VisualPlan: 第 {page_num} 页无 reference_user_hint")

        if not visual_summary:
            visual_summary = visual_desc[:40] + "..." if len(visual_desc) > 40 else visual_desc

        text_content = page.get("text_content", {})
        intent = {
            "page_num": page.get("page_num", 0),
            "type": page_type,
            "layout": _assign_layout(
                page_type,
                body_count,
                text_content.get("headline", ""),
                text_content.get("subhead", ""),
            ),
            "seed_family": _infer_seed_family(page_type),
            "visual_summary": visual_summary,
            "visual_description": visual_desc,
            "design_notes": f"种子家族: {_infer_seed_family(page_type)}，布局: {_assign_layout(page_type, body_count)}",
            "reference_image_ids": reference_image_ids or [],
        }
        visual_plan.append(intent)

    # 4. 推荐种子页：每类 Seed Family 选 page_num 最小的一页
    family_first_pages = {}
    for intent in visual_plan:
        family = intent["seed_family"]
        page_num = intent["page_num"]
        if family not in family_first_pages or page_num < family_first_pages[family]["page_num"]:
            family_first_pages[family] = intent

    for intent in visual_plan:
        intent["is_seed_recommended"] = intent in family_first_pages.values()

    logger.info(f"VisualPlan: 生成完成，共 {len(visual_plan)} 页，推荐种子页: {list(family_first_pages.keys())}")
    if progress_callback:
        progress_callback(f"✅ 视觉方案已生成（{len(visual_plan)} 页），开始逐页撰写生图 Prompt...")
    return visual_plan


def generate_visual_plan(
    content_plan: List[Dict],
    style_id: str = "default",
    reference_image_ids: Optional[List[str]] = None,
    style_override: Optional[Dict] = None,
    progress_callback: Optional[callable] = None,
) -> List[Dict]:
    """
    根据 Content Plan 生成 Visual Plan Intent。
    只输出 intent（layout, visual_description, design_notes 等），不写 final prompt。
    """
    logger.info(f"VisualPlan: 为 {len(content_plan)} 页生成视觉意图，风格={style_id}")

    try:
        return _do_generate_visual_plan(
            content_plan, style_id, reference_image_ids, style_override, progress_callback
        )
    except Exception as e:
        logger.exception(f"VisualPlan: 生成视觉方案时发生未预期错误: {e}，返回默认 fallback")
        # 返回安全 fallback，保证 pipeline 不中断
        return _fallback_visual_plan(content_plan, reference_image_ids or [])
