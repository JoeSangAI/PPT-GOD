import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

import json_repair

from app.core.llm_client import get_llm_client
from app.core.provider_credentials import get_minimax_llm_model, get_raw_provider_credentials, provider_credentials_context
from app.services.logo_policy import logo_policy_for_page
from app.services.overlay_layers import normalize_overlay_layers
from app.services.prompt_engine import _sanitize_product_reference_text
from app.services.style_pack import derive_style_pack_from_content
from app.services.visual_strategy import visual_language_group
from app.utils.text_cleaning import clean_llm_output

logger = logging.getLogger(__name__)

PRODUCT_ASSET_TRIGGER_TERMS = (
    "产品", "主产品", "包装", "瓶", "瓶身", "油瓶", "礼盒", "sku", "SKU",
    "货架", "终端", "陈列", "卖点", "实物", "样品", "体验台", "主视觉", "KV", "品牌物件",
)
PERSON_ASSET_TRIGGER_TERMS = ("人物", "模特", "代言人", "创始人", "讲师", "专家", "团队", "肖像")
SCENE_ASSET_TRIGGER_TERMS = ("场景", "门店", "工厂", "展台", "发布会", "直播间", "办公室", "货架", "终端")
ASSET_RECALL_SUMMARY_KEYS = {
    "name",
    "subject",
    "description",
    "identity_elements",
    "features",
    "must_not_change",
    "keywords",
    "source",
    "source_slide_text",
    "tags",
}
LOW_CONFIDENCE_ASSET_TERMS = {
    "品牌",
    "品牌宣传",
    "宣传",
    "展示",
    "页面",
    "ppt页面",
    "核心",
    "作为核心",
    "适合",
    "用于",
    "进行展示",
}
LOGO_PLACEHOLDER_TERMS = ("logo", "标识", "徽标", "角标", "wordmark", "lockup", "占位框", "空框")
LOGO_RESERVATION_TERMS = (
    "预留", "留出", "保留", "放置", "位置", "区域", "右上角",
    "左上角", "右下角", "左下角", "叠加", "overlay", "title-block",
)


def _is_punchline_page_type(page_type: str) -> bool:
    return str(page_type or "").strip().lower() in {"hero", "quote"}


def _infer_seed_family(page_type: str) -> str:
    """推断页面所属"家族"，用于版式一致性锚定。

    家族规则：同家族的页面共享版式、字体、配色和装饰语言。
    每个家族里第一张已生成的页会自动成为该家族的"种子页"，
    后续兄弟页生成时拿种子图作为视觉参考，保证商业提案级一致性。
    """
    page_type = str(page_type or "content").strip().lower()
    if page_type in ("cover", "ending"):
        return "bookend"
    if _is_punchline_page_type(page_type):
        return "hero"
    if page_type == "toc":
        return "toc"
    if page_type == "section":
        return "section"
    if page_type == "data":
        return "data"
    return "content"


def _annotate_seed_family(visual_plan: List[Dict]) -> None:
    """给 visual_plan 每页打上 seed_family，并把每个家族最早的页标为推荐种子。

    is_seed_recommended 仅作为 hint：实际种子由 generation_pipeline 在全量生成时
    根据"已完成的页"动态推断（已打样的页自动升为该家族种子）。
    """
    family_first_page_num: Dict[str, int] = {}
    for intent in visual_plan:
        family = _infer_seed_family(intent.get("type", "content"))
        intent["seed_family"] = family
        page_num = int(intent.get("page_num") or 0)
        if family not in family_first_page_num or page_num < family_first_page_num[family]:
            family_first_page_num[family] = page_num
    for intent in visual_plan:
        intent["is_seed_recommended"] = (
            int(intent.get("page_num") or 0) == family_first_page_num.get(intent.get("seed_family"), -1)
        )


def _load_style(style_id: str) -> Dict:
    """加载风格模板，解析 frontmatter 和正文。"""
    templates_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "templates", "styles"))
    path = os.path.join(templates_dir, f"{style_id}.md")
    if not os.path.exists(path):
        logger.warning(f"风格模板 {style_id} 不存在，使用 default")
        path = os.path.join(templates_dir, "default.md")

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


def _visual_strategy_from_style_text(style_text: str | None) -> dict:
    text = str(style_text or "")
    match = re.search(r"base_tone\s*=\s*(dark|light|mixed)", text, flags=re.IGNORECASE)
    base_tone = match.group(1).lower() if match else "mixed"
    summary = ""
    for line in text.splitlines():
        if line.strip().lower().startswith("visual strategy:"):
            summary = line.split(":", 1)[1].strip()
            break
    return {
        "base_tone": base_tone,
        "summary": summary or "按页面功能成组控制明暗，同类正文页使用同一种信息页处理。",
    }


def _remove_logo_placeholder_language(text: str) -> str:
    cleaned: list[str] = []
    for clause in re.split(r"[。；;\n]+", str(text or "")):
        value = clause.strip()
        if not value:
            continue
        lower = value.lower()
        has_logo_term = any(term.lower() in lower for term in LOGO_PLACEHOLDER_TERMS)
        has_reservation_term = any(term.lower() in lower for term in LOGO_RESERVATION_TERMS)
        if has_logo_term and has_reservation_term:
            continue
        cleaned.append(value)
    return "；".join(cleaned).strip()


def _assign_layout(page_type: str, body_count: int = 0, headline: str = "", subhead: str = "") -> str:
    """根据页面类型和内容特征分配合适的 layout。"""
    page_type = str(page_type or "content").strip().lower()
    mapping = {
        "cover": "cover",
        "toc": "toc",
        "section": "section",
        "hero": "hero",
        "quote": "hero",
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


def _fallback_visual_evidence(page: Dict) -> str:
    """Build a concrete, content-led visual object when the LLM misses a page."""
    page_type = str(page.get("type") or "").strip().lower()
    text = page.get("text_content", {}) or {}
    headline = text.get("headline", "") or page.get("section_title", "")
    body = text.get("body", "")
    body_text = "\n".join(str(x) for x in body) if isinstance(body, list) else str(body or "")
    source = f"{headline}\n{body_text}"

    if any(k in source for k in ("直播", "达人", "KOL", "KOC", "内容电商", "线上")):
        return "直播间背景板、达人短视频矩阵和统一话术卡"
    if any(k in source for k in ("白皮书", "标准", "发布会", "文旅部", "粮油学会", "认证")):
        return "行业标准白皮书、官方印章和发布会背板"
    if any(k in source for k in ("华为", "腾讯", "阿里", "名企", "企业", "团购", "B端")):
        return "企业楼宇、团购礼盒和非遗体验官活动现场"
    if any(k in source for k in ("终端", "货架", "小油", "闻香", "导购", "C端")):
        return "超市货架、试闻小油瓶和终端导购体验台"
    if any(k in source for k in ("VS", "对立", "竞品", "工业", "古法", "5S")):
        return "工业流水线与古法小榨工坊的左右对比"
    if any(k in source for k in ("地图", "区域", "市场", "份额", "窗口期")):
        return "区域市场地图、机会箭头和竞争态势标记"
    if any(k in source for k in ("资本", "利润", "定价权", "增长", "估值", "8个亿")):
        return "增长曲线、利润阶梯和品牌资产护城河示意"
    if _is_punchline_page_type(page_type):
        return f"围绕「{headline}」的金句排版、可选署名/上下文与象征性背景"
    if page_type == "section":
        return f"围绕「{headline}」的章节标题、序号和转场氛围"
    if page_type in ("cover", "ending"):
        return f"围绕「{headline}」的品牌主视觉和核心记忆符号"
    return f"支撑「{headline}」这一页观点的核心场景、物件或结构图"


def _fallback_visual_description(page: Dict, visual_evidence: str) -> str:
    page_type = str(page.get("type") or "").strip().lower()
    if _is_punchline_page_type(page_type):
        return (
            f"以「{visual_evidence}」作为金句页主视觉，只保留核心短句和必要的轻量辅助信息；"
            "版面使用大量留白和低干扰背景，可用纹理、光效、象征物、人物或场景承托，"
            "并严格沿用全局风格的配色、字体气质和材质语言。"
        )
    if page_type == "section":
        return (
            f"以「{visual_evidence}」作为章节分隔页主视觉，突出章节名、编号或一句转场判断；"
            "正文信息极少，版面可以更强烈使用主色、留白、材质或象征物来建立段落节奏，"
            "但必须沿用整套 deck 的字体气质和视觉语言。"
        )
    return (
        f"以「{visual_evidence}」作为本页画面证据，"
        "根据页面信息密度安排文字区和配图区，使用全局风格色彩与材质做克制统一的包装。"
    )


def _page_search_text(page: Dict) -> str:
    text = page.get("text_content", {}) or {}
    chunks = [
        str(page.get("type", "")),
        str(page.get("visual_suggestion", "")),
        str(text.get("headline", "")),
        str(text.get("subhead", "")),
    ]
    body = text.get("body", "")
    if isinstance(body, list):
        chunks.extend(str(item) for item in body)
    else:
        chunks.append(str(body or ""))
    return "\n".join(chunks).lower()


def _page_source_ref_keys(page: Dict) -> set[tuple[str, int]]:
    refs = page.get("source_refs") if isinstance(page.get("source_refs"), list) else []
    keys: set[tuple[str, int]] = set()
    for item in refs:
        if not isinstance(item, dict):
            continue
        source_doc = str(item.get("source_document") or "").strip()
        try:
            source_page = int(item.get("source_page_num") or 0)
        except (TypeError, ValueError):
            source_page = 0
        if source_doc and source_page > 0:
            keys.add((source_doc, source_page))
    return keys


def _asset_search_terms(asset: Dict) -> set[str]:
    terms: set[str] = set()
    tier = str(asset.get("selection_tier") or "").lower()
    try:
        importance_score = float(asset.get("importance_score") or 0)
    except (TypeError, ValueError):
        importance_score = 0
    allow_source_terms = not tier or tier in {"manual", "core_global"} or importance_score >= 45
    for key in ("name",):
        value = asset.get(key)
        if value:
            terms.add(str(value).lower())
    summary = str(asset.get("analysis_summary") or "")
    summary_chunks = []
    for part in summary.split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            summary_key = key.strip()
            if summary_key not in ASSET_RECALL_SUMMARY_KEYS:
                continue
            if summary_key in {"source_slide_text", "tags"} and not allow_source_terms:
                continue
            summary_chunks.append(value)
        elif not summary_chunks:
            summary_chunks.append(part)
    summary_text = ";".join(summary_chunks)
    for token in re.split(r"[\s,，;；:：、/|()（）\[\]{}\"'“”‘’]+", summary_text):
        token = token.strip().lower()
        if len(token) >= 2 and token not in LOW_CONFIDENCE_ASSET_TERMS:
            terms.add(token)
    name = str(asset.get("name") or "").strip().lower()
    if name:
        terms.add(name)
        for token in re.split(r"[\s_\-—,，、/]+", name):
            token = token.strip().lower()
            if len(token) >= 2:
                terms.add(token)
    return terms


def _asset_is_recallable(asset: Dict) -> bool:
    tier = str(asset.get("selection_tier") or "").lower()
    if tier in {"page_ref_only", "low_value", "decorative"}:
        return False
    try:
        score = float(asset.get("importance_score") or 0)
    except (TypeError, ValueError):
        score = 0
    if tier and tier not in {"manual", "core_global", "legacy_candidate"} and score < 45:
        return False
    return True


def _asset_kind_triggers(kind: str) -> tuple[str, ...]:
    if kind in {"product", "material"}:
        return PRODUCT_ASSET_TRIGGER_TERMS
    if kind == "person":
        return PERSON_ASSET_TRIGGER_TERMS
    if kind == "scene":
        return SCENE_ASSET_TRIGGER_TERMS
    return ()


def _recall_visual_assets_for_page(page: Dict, global_visual_assets: Optional[List[Dict]]) -> list[Dict]:
    """
    Lightweight deterministic recall for must-consider assets.
    It only scans already available page text and asset metadata, so it avoids
    extra model calls and does not constrain composition.
    """
    if not global_visual_assets:
        return []

    page_text = _page_search_text(page)
    page_source_refs = _page_source_ref_keys(page)
    recalled = []
    for asset in global_visual_assets:
        asset_id = asset.get("id")
        if not asset_id:
            continue
        if not _asset_is_recallable(asset):
            continue
        kind = str(asset.get("kind") or "other").lower()
        terms = _asset_search_terms(asset)
        direct_hits = [term for term in terms if len(term) >= 2 and term in page_text]
        kind_hits = [term for term in _asset_kind_triggers(kind) if term.lower() in page_text]
        try:
            asset_source_page = int(asset.get("source_page_num") or 0)
        except (TypeError, ValueError):
            asset_source_page = 0
        asset_source_key = (str(asset.get("source_document") or "").strip(), asset_source_page)
        source_hit = bool(asset_source_key[0] and asset_source_key[1] and asset_source_key in page_source_refs)
        should_recall = source_hit or bool(direct_hits)
        # Product/material assets are the critical case: if the page asks for a product
        # or packaging scene and there is only one such global asset, make it a candidate.
        if not should_recall and kind in {"product", "material"} and kind_hits:
            product_assets = [
                a for a in global_visual_assets
                if str(a.get("kind") or "").lower() in {"product", "material"}
            ]
            should_recall = len(product_assets) == 1
        if should_recall:
            reason = "页面继承了该素材的源 PPT 页" if source_hit else (
                "页面内容命中资产名称/关键词" if direct_hits else "页面内容需要产品/包装类画面且项目只有一个核心产品资产"
            )
            recalled.append({
                "id": str(asset_id),
                "name": asset.get("name") or "",
                "kind": kind,
                "matched_terms": (direct_hits or kind_hits)[:8],
                "reason": reason,
            })
    return recalled[:3]


def _default_visual_asset_usage(asset: Dict, page: Dict) -> str:
    name = asset.get("name") or "该视觉资产"
    kind = str(asset.get("kind") or "other").lower()
    if kind in {"product", "material"}:
        return "Place the uploaded product image in the relevant visual area; keep usage to placement and narrative role only."
    if kind == "person":
        return f"在本页相关视觉区域使用「{name}」作为人物画面证据；只说明位置与叙事作用，人物外观以上传图为准。"
    return f"在本页相关视觉区域使用「{name}」作为画面证据；只说明位置与叙事作用，外观以上传图为准。"


def _manual_visual_asset_ids(page: Dict, valid_asset_ids: set[str]) -> list[str]:
    raw = page.get("manual_visual_asset_ids") or []
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for asset_id in raw:
        value = str(asset_id)
        if value in valid_asset_ids and value not in result:
            result.append(value)
    return result


def _manual_visual_asset_usage(page: Dict, manual_ids: list[str]) -> dict[str, str]:
    raw = page.get("manual_visual_asset_usage") or {}
    if not isinstance(raw, dict):
        return {}
    allowed = set(manual_ids)
    return {str(k): str(v) for k, v in raw.items() if str(k) in allowed and v}


def _manual_overlay_layers(page: Dict, valid_asset_ids: set[str]) -> list[dict]:
    raw = page.get("overlay_layers") or []
    return normalize_overlay_layers(raw, valid_asset_ids=valid_asset_ids, strict_assets=True)


def _safe_parse_json(raw: str, batch_num: int) -> Optional[Dict]:
    """安全解析 LLM 返回的 JSON，尝试多种修复策略。"""
    text = clean_llm_output(raw or "").strip()
    # 策略 1: 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 策略 2: 使用 json_repair 修复 LLM 常见问题：
    # 未转义换行、尾逗号、局部缺引号等。项目其它 JSON 入口也使用它。
    try:
        repaired = json_repair.loads(text)
        if isinstance(repaired, dict):
            return repaired
    except Exception:
        pass

    # 策略 3: 查找最外层 JSON 对象（有时 LLM 会包裹在 markdown 或其他文本中）
    try:
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            snippet = text[start:end + 1]
            try:
                return json.loads(snippet)
            except json.JSONDecodeError:
                try:
                    repaired = json_repair.loads(snippet)
                    if isinstance(repaired, dict):
                        return repaired
                except Exception:
                    pass
    except Exception:
        pass

    # 策略 4: 修复常见 JSON 语法错误
    fixed = text
    # 3a. 修复 trailing commas（如 {"a": 1,}）
    fixed = re.sub(r',(\s*[}\]])', r'\1', fixed)
    # 3b. 修复单引号为双引号
    fixed = fixed.replace("'", '"')
    # 3c. 修复未转义的换行符在字符串值中（简单修复：将字符串内的换行替换为 \n）
    # 这个比较复杂，先尝试简单修复
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # 策略 5: 尝试逐行提取有效 JSON（如果 LLM 返回了多个对象或混合文本）
    # 先尝试找到所有完整的 key-value 对
    try:
        # 使用更宽松的解析：提取所有 "数字": { ... } 模式
        pattern = r'"(\d+)"\s*:\s*\{[^{}]*\}'
        matches = re.findall(pattern, text)
        if matches:
            # 尝试构建一个完整 JSON
            entries = []
            for m in re.finditer(r'"(\d+)"\s*:\s*(\{[^{}]*\})', text):
                entries.append(f'"{m.group(1)}": {m.group(2)}')
            if entries:
                combined = "{" + ", ".join(entries) + "}"
                return json.loads(combined)
    except (json.JSONDecodeError, re.error):
        pass

    logger.warning(f"VisualPlan: batch {batch_num} JSON 解析失败，raw 前200字: {text[:200]}")
    return None


def _build_batch_prompt(
    pages_summary: List[Dict],
    style: Dict,
    batch_num: int = 1,
    total_batches: int = 1,
    global_visual_assets: Optional[List[Dict]] = None,
    has_project_logo: bool = False,
) -> str:
    """构建批量生成 visual_description 的 LLM prompt。"""
    palette = style["meta"].get("palette", ["#1E3A5F", "#F5F5F0"])
    theme = style["meta"].get("theme") or style["meta"].get("style_name") or "Content-derived presentation style"
    mood = style["meta"].get("mood", "Professional, clean, confident")
    description = style.get("body", "")
    visual_strategy = _visual_strategy_from_style_text(description)
    visual_strategy_text = visual_strategy.get("summary") or "按页面功能成组控制明暗，同类正文页使用同一种信息页处理。"

    batch_hint = f"这是第 {batch_num}/{total_batches} 批。" if total_batches > 1 else ""
    requirement_lines = []
    for page in pages_summary:
        value = str(page.get("global_user_requirements") or "").strip()
        if value and value not in requirement_lines:
            requirement_lines.append(value)
    requirements_instruction = ""
    if requirement_lines:
        requirements_instruction = f"""
【跨阶段用户补充要求 — 必须继承】
以下要求来自用户在前序阶段提出的补充说明。它们不一定只属于视觉阶段；如果与本批页面相关，必须影响 visual_evidence、数据强调、时间轴/对比/结构图选择和 visual_description。
{chr(10).join(requirement_lines)}
"""
    all_visual_assets = list(global_visual_assets or [])
    recalled_ids = {
        str(item.get("id"))
        for page in pages_summary
        for item in (page.get("must_consider_visual_assets") or [])
        if isinstance(item, dict) and item.get("id")
    }
    recalled_assets = [asset for asset in all_visual_assets if str(asset.get("id")) in recalled_ids]
    fallback_assets = [
        asset for asset in all_visual_assets
        if str(asset.get("id")) not in recalled_ids
    ]
    fallback_assets.sort(key=lambda asset: -float(asset.get("importance_score") or 0))
    # Keep the prompt lean: always include deterministic recalls for this batch,
    # plus a small high-value fallback pool for manual product/person/material assets.
    visual_assets = [*recalled_assets, *fallback_assets[: max(0, 8 - len(recalled_assets))]][:12]
    visual_asset_instruction = ""
    if visual_assets:
        visual_asset_instruction = f"""
【全局视觉资产 — 智能选择，不是风格参考】
以下图片是用户上传的产品图、人物图、场景图或物料图。它们全局可用，但不需要出现在每一页。
只有当某页内容明确需要该产品、人物、物料或场景作为画面证据时，才选择对应资产；无关页面必须返回空数组。
每页最多选择 3 个资产。页面级 reference_context 的优先级高于这些全局视觉资产。
如果页面数据里有 must_consider_visual_assets，说明系统用关键词轻量召回了高度相关资产：当页面画面证据涉及这些资产对应的产品/人物/物料/场景时，必须选择；只有页面明确无关时才不选。
资产清单：
{json.dumps(visual_assets, ensure_ascii=False, indent=2)}

选择资产时：
1. 严格基于页面标题、正文、visual_evidence 是否提到资产名称、关键词、产品类别或用户说明中的使用场景
2. 仅在页面内容明确相关时选择资产
3. 产品/物料/人物资产的外观和身份由上传图片本身承担，不要在 visual_evidence、visual_description、visual_asset_usage 里复述外观、颜色、包装、标签、名称、服装或姿态细节
4. 如果选择产品/物料资产，visual_asset_usage 只能写位置和画面占比，例如 "Place the uploaded product image at center-left, large and unobstructed."
5. 如果选择资产，在 visual_description 里只描述其他背景/场景/图表/文字区如何配合，不要再描述该资产本身
"""

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

    page_type_style_rules = """
【页面类型适配规则 — 必须遵守】
参考图/风格方案只提供“风格基因”，不是每页画面模板。具体每页画什么，必须由该页文案内容决定。
- cover / section / ending：可以更强烈使用品牌主色、深色、高饱和色或装饰元素，承担品牌定调和仪式感。
- cover / 封面：只负责定调和命名，不承载正文论证；标题、副标题和主视觉必须形成单一焦点，避免信息堆叠。
- toc / 目录页：只有导航功能，不承担内容论证。画面必须像清爽的路线图：3-6 个短章节名、明确编号、足够留白、少装饰；不要做成花哨菜单、图标墙、复杂信息图或封面式海报。
- section / 章节页：只负责章节切换、段落节奏和仪式感。画面应突出章节名、序号或一句转场判断；不要塞正文论证、三点列表、数据图或复杂商业证据。
- data / 数据页：只有当页面正文给出真实数字、标签或表格时才画图；必须围绕这些数据做大数字、简单图表或高可读表格，不要编造数值、趋势或坐标轴。
- ending / 封底：只做收束、感谢、CTA 或联系方式；呼应封面但更安静，不引入新商业证据或复杂画面。
- hero / quote / 金句页：这是独立的 punchline treatment，不是内容页。只围绕一句短句、一个短语或一个词做强记忆点；它可以是引用，也可以是口号、结论、转场判断、数据洞察或用户原话。视觉形式不固定，但必须沿用全局配色、字体气质、材质和装饰语言，不要突然改成另一套字体或海报风格。
- content / data / table / 对比分析页：优先保证阅读效率，但必须在整套明暗/基底策略内处理。信息越密集，越应通过卡片、内容区、网格、字号层级和留白降噪；不要机械切换到另一套浅底风格。
- 地图 / 图表 / 结构页：由文案决定地图、图表、流程或业务场景，不要机械复刻参考图里的封面构图。
- 如果参考图本身是强视觉封面、海报、广告KV或单页主视觉，必须先抽象为色彩/材质/装饰/构图原则，再按页面类型调节强度，不能把单页主视觉机械扩散到全 deck。
"""

    logo_pipeline_rule = (
        "Logo 由后端按页面类型统一处理：内容页默认右上角小尺寸叠加；封面是品牌主标识，可以按封面构图选择 "
        "title-block-center / center / lower-center / top-right 等页面级位置，其中 title-block-center 表示相对标题/副标题/年份这一组内容居中，而不是页面物理居中；"
        "封底/结束页如果已经有 CTA、联系方式或多行收束信息，默认用 top-right+small 角标，只有页面非常空旷、以品牌收束为主体时才用 center/lower-center+large；"
        "沉浸式 hero / 金句页默认不出现，除非本页明确需要角标或品牌招牌场景。你不要把 Logo 写进 visual_asset_ids。"
        if has_project_logo
        else "当前项目没有已确认的用户 Logo。所有页面的 logo_policy 必须返回 show_logo=false；不要为 Logo、品牌角标、标识、徽标或占位框预留空间，也不要把空框画进封面。"
    )
    logo_policy_rule = (
        '5. logo_policy：对象，格式 {"show_logo": true/false, "placement": "top-right|top-left|bottom-right|bottom-left|center|lower-center|title-block-center", "scale": "small|large", "use_as_scene_asset": false}。'
        "内容页通常 top-right+small；封面优先考虑 title-block-center+large，只有主视觉需要才用 center/lower-center；封底/结束页有 CTA 或联系方式时通常 top-right+small，只有空旷品牌收束页才考虑 center/lower-center+large；金句页默认 show_logo=false。"
        if has_project_logo
        else '5. logo_policy：对象，格式 {"show_logo": false, "placement": "top-right", "scale": "small", "use_as_scene_asset": false}。因为当前项目没有已确认 Logo，所有页面都必须 show_logo=false。'
    )
    logo_policy_example = (
        '{"show_logo": true, "placement": "title-block-center", "scale": "large", "use_as_scene_asset": false}'
        if has_project_logo
        else '{"show_logo": false, "placement": "top-right", "scale": "small", "use_as_scene_asset": false}'
    )

    prompt = f"""你是一位顶级 PPT 视觉总监。为以下每一页 PPT 生成视觉意图（visual intent）。

{batch_hint}
【视觉风格】
主题：{theme}
氛围：{mood}
配色：{', '.join(str(c) for c in palette[:5])}
{f"描述：{description}" if description else ""}

【整套明暗/基底策略 — 必须先遵守，再设计单页】
{visual_strategy_text}
除非这套策略明确允许浅底例外，否则内容页不得因为信息量较大就自动切换成米白、浅灰等另一套视觉语言；应优先在当前基底内用卡片、局部内容区、字号层级和留白解决可读性。

【大纲（{len(sanitized_pages)} 页）】
{json.dumps(sanitized_pages, ensure_ascii=False, indent=2)}
{requirements_instruction}
{ref_instruction}
{visual_asset_instruction}
{page_type_style_rules}
【Logo 管道规则】
{logo_pipeline_rule}

【任务】
为每一页生成五个字段：
0. visual_evidence：这一页最应该出现的“画面证据/配图对象”（20-45字）。它必须是可被看见的场景、物件、图表、对比结构或商业证据，不能只写风格。
1. visual_summary：一句话画面意向（20-30字；有参考图时 20-35 字），用于全局预览页快速理解。如"全屏深色背景，中央 DNA 双螺旋光纹"
2. visual_description：围绕 visual_evidence 写画面方案——**给用户阅读**，也会进入下游 pipeline。**不含**任何必须在页面上逐字渲染的正文（正文由单独约束）。
3. visual_asset_ids：本页需要使用的全局视觉资产 id 数组；无关页面输出 []，最多 3 个
4. visual_asset_usage：对象，key 为 asset_id，value 为一句中文说明，只说明该资产在本页的用途、位置和叙事作用；无资产输出 {{}}
{logo_policy_rule}
   - 无参考图：约 80–120 字，说明画面证据、布局主次、文字区与配图区如何分工、整体色调强度。
   - 含参考图：见上文规则。

【质量标准】
1. visual_evidence 必须具体。优先选择白皮书、直播间、达人矩阵、终端货架、企业楼宇、地图、增长曲线、VS 对比、产品/工艺场景等能证明观点的对象。
   - toc / 目录页例外：visual_evidence 应写成「章节路线图 / 导航结构」，不要编造商业证据或场景图。
   - section / 章节页例外：visual_evidence 应写成「章节标题 / 编号 / 转场氛围」，不要写正文证据或信息图。
   - data / 数据页例外：visual_evidence 应写成「正文给出的具体数字/图表对象」，不要写没有数据支撑的抽象增长曲线。
   - ending / 封底例外：visual_evidence 应写成「收束画面 / CTA / 联系方式区域」，不要引入新的证明素材。
   - 但 hero / quote / 金句页例外：visual_evidence 应写成「金句排版 + 轻量上下文/背景/象征物」这类 punchline treatment，不能写成普通信息图、三点列表或商业证据堆叠。
2. 禁止输出“现代商务风格画面”“与主题相关的视觉元素”“品牌调性背景”等空泛句。
3. visual_summary 必须极简，只说「画面是什么」（有参考图时可点出「基于用户参考路线图」等）。
4. visual_description 必须先服务 visual_evidence，再体现风格系统。不要堆叠每页重复的纹样、光效、材质细节。
5. 必须体现风格系统的配色和调性（无参考图页尤其重要），但只描述整体色调强弱，不写过多具体装饰元素。
6. 如果 existing_visual_suggestion 有内容，以它为基础扩展；如果为空，根据内容自行设计。
7. 不要规定具体字体名与字号；不要写出必须在页面上出现的正文原句。
   - hero / quote / 金句页不要引入与全局风格冲突的字体方向；只描述“同一套字体气质下更大、更克制/更有张力的排版层级”。
8. ⚠️ 每页的 visual_description 只能描述该页自己的画面。严禁引用其他页面的参考图或视觉元素。
9. 如果本页选择了产品/物料/人物视觉资产，visual_evidence、visual_description 和 visual_asset_usage 不要描述该资产的名称、外观或身份细节；只写 uploaded product/person/material image 以及位置、大小、与背景关系。资产身份由上传图决定。

【输出格式】
严格输出 JSON 对象，key 为 page_num（字符串），value 为对象：
{{"1": {{"visual_evidence": "画面证据...", "visual_summary": "一句话意向...", "visual_description": "详细描述...", "visual_asset_ids": [], "visual_asset_usage": {{}}, "logo_policy": {logo_policy_example}}}, "2": {{...}}, ...}}

为当前批次的每一页都生成，不要遗漏。"""
    return prompt


def _visual_plan_batch_worker_count(total_batches: int) -> int:
    try:
        configured = int(os.getenv("PPTGOD_VISUAL_PLAN_BATCH_WORKERS", "3"))
    except ValueError:
        configured = 3
    return max(1, min(total_batches, configured, 3))


def _generate_visual_plan_batch(
    batch: List[Dict],
    style: Dict,
    batch_num: int,
    total_batches: int,
    global_visual_assets: Optional[List[Dict]],
    provider_credentials,
    has_project_logo: bool,
) -> Dict:
    with provider_credentials_context(provider_credentials):
        prompt = _build_batch_prompt(
            batch,
            style,
            batch_num=batch_num,
            total_batches=total_batches,
            global_visual_assets=global_visual_assets,
            has_project_logo=has_project_logo,
        )
        client = get_llm_client()
        response = client.chat.completions.create(
            model=get_minimax_llm_model(),
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
        raw = clean_llm_output(raw.strip())
        logger.info(f"VisualPlan: batch {batch_num}/{total_batches} raw length={len(raw)}")
        if not raw:
            logger.warning(f"VisualPlan: batch {batch_num} 返回空内容")
            return {}
        parsed = _safe_parse_json(raw, batch_num)
        return parsed if isinstance(parsed, dict) else {}


def _fallback_visual_plan(
    content_plan: List[Dict],
    reference_image_ids: List[str],
    style_pack_snapshot: str | None = None,
    has_project_logo: bool = False,
) -> List[Dict]:
    """返回安全的 fallback visual plan，保证 pipeline 不中断。"""
    visual_plan = []
    style_pack_snapshot = style_pack_snapshot or derive_style_pack_from_content(content_plan)
    visual_strategy = _visual_strategy_from_style_text(style_pack_snapshot)
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
        visual_evidence = _fallback_visual_evidence(page)
        visual_plan.append({
            "page_num": page.get("page_num", 0),
            "type": page_type,
            "layout": _assign_layout(page_type, body_count, headline, text_content.get("subhead", "")),
            "visual_evidence": visual_evidence,
            "visual_summary": visual_evidence,
            "visual_description": _fallback_visual_description(page, visual_evidence),
            "design_notes": f"布局: {_assign_layout(page_type, body_count, headline, text_content.get('subhead', ''))}",
            "reference_image_ids": reference_image_ids or [],
            "visual_asset_ids": [],
            "visual_asset_usage": {},
            "style_pack_snapshot": style_pack_snapshot,
            "visual_language_group": visual_language_group(
                page_type,
                _assign_layout(page_type, body_count, headline, text_content.get("subhead", "")),
                visual_strategy,
            ),
        })
        visual_plan[-1]["logo_policy"] = logo_policy_for_page(visual_plan[-1])
        if not has_project_logo:
            visual_plan[-1]["logo_policy"] = {
                "show_logo": False,
                "placement": "top-right",
                "scale": "small",
                "visibility": "omit",
                "use_as_scene_asset": False,
            }
    _annotate_seed_family(visual_plan)
    return visual_plan


def _do_generate_visual_plan(
    content_plan: List[Dict],
    style_id: str = "default",
    reference_image_ids: Optional[List[str]] = None,
    style_override: Optional[Dict] = None,
    global_visual_assets: Optional[List[Dict]] = None,
    progress_callback: Optional[callable] = None,
    has_project_logo: bool | None = None,
) -> List[Dict]:
    """generate_visual_plan 的实际实现（不含异常捕获）。"""
    style_pack_snapshot = ""
    style = _load_style(style_id)
    if style_override:
        style["meta"].update(style_override.get("meta", {}))
        if style_override.get("body"):
            style["body"] = style_override["body"]
            style_pack_snapshot = style_override["body"]
    else:
        style_pack_snapshot = derive_style_pack_from_content(content_plan)
        style = {
            "meta": {
                "theme": "Content-derived style pack",
                "mood": "",
                "palette": [],
            },
            "body": style_pack_snapshot,
        }
    visual_strategy = _visual_strategy_from_style_text(style_pack_snapshot or style.get("body", ""))
    effective_has_project_logo = bool(reference_image_ids) if has_project_logo is None else bool(has_project_logo)

    # 1. 准备 batch prompt 的页面摘要
    pages_summary = []
    recalled_assets_by_page: dict[int, list[Dict]] = {}
    for page in content_plan:
        text = page.get("text_content", {})
        page_num = page.get("page_num", 0)
        recalled_assets = _recall_visual_assets_for_page(page, global_visual_assets)
        recalled_assets_by_page[int(page_num or 0)] = recalled_assets
        summary = {
            "page_num": page_num,
            "type": page.get("type", "content"),
            "headline": text.get("headline", ""),
            "subhead": text.get("subhead", ""),
            "body_preview": text.get("body", [])[:3] if isinstance(text.get("body"), list) else (text.get("body", "")[:120] if isinstance(text.get("body"), str) else ""),
            "existing_visual_suggestion": page.get("visual_suggestion", ""),
            "reference_context": page.get("reference_context", ""),
            "reference_user_hint": page.get("reference_user_hint", ""),
            "global_user_requirements": page.get("global_user_requirements", ""),
        }
        if recalled_assets:
            summary["must_consider_visual_assets"] = recalled_assets
        pages_summary.append(summary)

    # 2. 分批调用 LLM 生成 visual_description，避免单 prompt 过长导致超时
    BATCH_SIZE = 5
    total_pages = len(pages_summary)
    descriptions: Dict[str, Dict] = {}
    total_batches = (total_pages + BATCH_SIZE - 1) // BATCH_SIZE
    batches: list[tuple[int, int, list[Dict]]] = []
    for batch_idx in range(0, total_pages, BATCH_SIZE):
        batch = pages_summary[batch_idx : batch_idx + BATCH_SIZE]
        batch_num = batch_idx // BATCH_SIZE + 1
        batches.append((batch_idx, batch_num, batch))
        ref_pages_in_batch = [p["page_num"] for p in batch if (p.get("reference_context") or "").strip()]
        logger.info(f"VisualPlan: batch {batch_num}/{total_batches}, pages={[p['page_num'] for p in batch]}, ref_pages={ref_pages_in_batch}")

    if progress_callback:
        progress_callback({
            "stage": "visual_planning",
            "message": "正在生成视觉方案",
            "current_page": 0,
            "total_pages": total_pages,
        })

    completed_pages = 0
    provider_credentials = get_raw_provider_credentials()
    worker_count = _visual_plan_batch_worker_count(total_batches)
    if worker_count <= 1:
        batch_results = []
        for _batch_idx, batch_num, batch in batches:
            try:
                batch_results.append((batch_num, _generate_visual_plan_batch(
                    batch,
                    style,
                    batch_num,
                    total_batches,
                    global_visual_assets,
                    provider_credentials,
                    effective_has_project_logo,
                )))
            except Exception as e:
                logger.error(f"VisualPlan: batch {batch_num}/{total_batches} 调用失败: {e}")
            finally:
                completed_pages += len(batch)
                if progress_callback:
                    progress_callback({
                        "stage": "visual_planning",
                        "message": "正在生成视觉方案",
                        "current_page": min(completed_pages, total_pages),
                        "total_pages": total_pages,
                    })
    else:
        batch_results = []
        try:
            with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="visual-plan") as executor:
                future_map = {
                    executor.submit(
                        _generate_visual_plan_batch,
                        batch,
                        style,
                        batch_num,
                        total_batches,
                        global_visual_assets,
                        provider_credentials,
                        effective_has_project_logo,
                    ): (batch_num, len(batch))
                    for _batch_idx, batch_num, batch in batches
                }
                for future in as_completed(future_map):
                    batch_num, batch_len = future_map[future]
                    try:
                        batch_results.append((batch_num, future.result()))
                    except Exception as e:
                        logger.error(f"VisualPlan: batch {batch_num}/{total_batches} 调用失败: {e}")
                    finally:
                        completed_pages += batch_len
                        if progress_callback:
                            progress_callback({
                                "stage": "visual_planning",
                                "message": "正在生成视觉方案",
                                "current_page": min(completed_pages, total_pages),
                                "total_pages": total_pages,
                            })
        except Exception as e:
            logger.error(f"VisualPlan: 并发批处理失败，已保留可用批次结果: {e}")

    for _batch_num, batch_descriptions in sorted(batch_results, key=lambda item: item[0]):
        if isinstance(batch_descriptions, dict):
            descriptions.update(batch_descriptions)

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
            visual_evidence = desc_data.get("visual_evidence", "")
            visual_summary = desc_data.get("visual_summary", "")
            visual_desc = desc_data.get("visual_description", "")
            visual_asset_ids = desc_data.get("visual_asset_ids", [])
            visual_asset_usage = desc_data.get("visual_asset_usage", {})
            llm_logo_policy = desc_data.get("logo_policy") if isinstance(desc_data.get("logo_policy"), dict) else None
        else:
            # 兼容旧格式：直接是字符串
            visual_evidence = ""
            visual_summary = ""
            visual_desc = str(desc_data) if desc_data else ""
            visual_asset_ids = []
            visual_asset_usage = {}
            llm_logo_policy = None

        valid_asset_ids = {
            str(asset.get("id"))
            for asset in (global_visual_assets or [])
            if asset.get("id")
        }
        manual_asset_ids = _manual_visual_asset_ids(page, valid_asset_ids)
        manual_asset_usage = _manual_visual_asset_usage(page, manual_asset_ids)
        overlay_layers = _manual_overlay_layers(page, valid_asset_ids)
        if not isinstance(visual_asset_ids, list):
            visual_asset_ids = []
        visual_asset_ids = [
            str(asset_id)
            for asset_id in visual_asset_ids
            if str(asset_id) in valid_asset_ids and str(asset_id) not in set(manual_asset_ids)
        ][:3]
        if not isinstance(visual_asset_usage, dict):
            visual_asset_usage = {}
        visual_asset_usage = {
            str(k): str(v)
            for k, v in visual_asset_usage.items()
            if str(k) in set(visual_asset_ids) and v
        }
        visual_asset_ids = [*manual_asset_ids, *visual_asset_ids]
        visual_asset_usage = {**manual_asset_usage, **visual_asset_usage}

        recalled_assets = recalled_assets_by_page.get(int(page.get("page_num", 0) or 0), [])
        if recalled_assets:
            # Safety net: if the LLM missed a recalled core product/person/material asset,
            # include it without constraining composition. This keeps recall high while
            # leaving visual execution to the model.
            auto_added_count = len([asset_id for asset_id in visual_asset_ids if asset_id not in set(manual_asset_ids)])
            for recalled in recalled_assets:
                asset_id = str(recalled.get("id") or "")
                if not asset_id or asset_id in visual_asset_ids or auto_added_count >= 3:
                    continue
                asset_obj = next(
                    (asset for asset in (global_visual_assets or []) if str(asset.get("id")) == asset_id),
                    recalled,
                )
                visual_asset_ids.append(asset_id)
                auto_added_count += 1
                visual_asset_usage[asset_id] = _default_visual_asset_usage(asset_obj, page)
                logger.info(
                    "VisualPlan: page %s auto-added recalled visual asset %s (%s)",
                    page_num,
                    asset_id,
                    recalled.get("reason", "matched"),
                )

        selected_asset_lookup = {
            str(asset.get("id")): asset
            for asset in (global_visual_assets or [])
            if asset.get("id")
        }
        selected_product_asset_ids = {
            asset_id for asset_id in visual_asset_ids
            if str((selected_asset_lookup.get(asset_id) or {}).get("kind") or "").lower() in {"product", "material"}
        }
        if selected_product_asset_ids:
            visual_evidence = _sanitize_product_reference_text(visual_evidence) or "Supporting scene or diagram derived from this slide's content"
            visual_desc = _sanitize_product_reference_text(visual_desc) or (
                "Arrange text and supporting visual evidence around the uploaded product image with clear hierarchy."
            )
            visual_asset_usage = {
                asset_id: (
                    _sanitize_product_reference_text(usage) or
                    "Place the uploaded product image in the relevant visual area."
                )
                if asset_id in selected_product_asset_ids else usage
                for asset_id, usage in visual_asset_usage.items()
            }

        if not visual_evidence:
            logger.warning(f"VisualPlan: 第 {page_num} 页缺失 visual_evidence，使用默认")
            visual_evidence = _fallback_visual_evidence(page)

        if not visual_desc:
            logger.warning(f"VisualPlan: 第 {page_num} 页缺失 visual_description，使用默认")
            visual_desc = _fallback_visual_description(page, visual_evidence)

        hint = (page.get("reference_user_hint") or "").strip()
        if hint:
            # 用户可见区必须出现参考图识别摘要；LLM 常与页面主题「跑题」而忽略参考图
            visual_desc = f"{hint}\n\n{visual_desc.strip()}".strip()
            logger.info(f"VisualPlan: 第 {page_num} 页已注入 reference_user_hint，visual_desc 长度={len(visual_desc)}")
        else:
            logger.info(f"VisualPlan: 第 {page_num} 页无 reference_user_hint")

        if not visual_summary:
            visual_summary = visual_desc[:40] + "..." if len(visual_desc) > 40 else visual_desc

        if not effective_has_project_logo:
            visual_evidence = _remove_logo_placeholder_language(visual_evidence) or _fallback_visual_evidence(page)
            visual_summary = _remove_logo_placeholder_language(visual_summary) or visual_evidence
            visual_desc = _remove_logo_placeholder_language(visual_desc) or _fallback_visual_description(page, visual_evidence)

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
            "visual_evidence": visual_evidence,
            "visual_summary": visual_summary,
            "visual_description": visual_desc,
            "design_notes": f"布局: {_assign_layout(page_type, body_count, text_content.get('headline', ''), text_content.get('subhead', ''))}",
            "reference_image_ids": reference_image_ids or [],
            "manual_visual_asset_ids": manual_asset_ids,
            "manual_visual_asset_usage": manual_asset_usage,
            "overlay_layers": overlay_layers,
            "visual_asset_ids": visual_asset_ids,
            "visual_asset_usage": visual_asset_usage,
            "style_pack_snapshot": style_pack_snapshot,
            "visual_language_group": visual_language_group(
                page_type,
                _assign_layout(
                    page_type,
                    body_count,
                    text_content.get("headline", ""),
                    text_content.get("subhead", ""),
                ),
                visual_strategy,
            ),
        }
        if llm_logo_policy:
            intent["logo_policy"] = logo_policy_for_page({**intent, "logo_policy": llm_logo_policy})
            if "use_as_scene_asset" in llm_logo_policy:
                intent["logo_policy"]["use_as_scene_asset"] = bool(llm_logo_policy.get("use_as_scene_asset"))
        else:
            intent["logo_policy"] = logo_policy_for_page(intent)
        if not effective_has_project_logo:
            intent["logo_policy"] = {
                "show_logo": False,
                "placement": "top-right",
                "scale": "small",
                "visibility": "omit",
                "use_as_scene_asset": False,
            }
        visual_plan.append(intent)

    _annotate_seed_family(visual_plan)
    logger.info(f"VisualPlan: 生成完成，共 {len(visual_plan)} 页")
    if progress_callback:
        progress_callback({
            "stage": "prompt_writing",
            "message": "视觉方案已生成，正在撰写生图 Prompt",
            "current_page": len(visual_plan),
            "total_pages": len(visual_plan),
        })
    return visual_plan


def generate_visual_plan(
    content_plan: List[Dict],
    style_id: str = "default",
    reference_image_ids: Optional[List[str]] = None,
    style_override: Optional[Dict] = None,
    global_visual_assets: Optional[List[Dict]] = None,
    progress_callback: Optional[callable] = None,
    has_project_logo: bool | None = None,
) -> List[Dict]:
    """
    根据 Content Plan 生成 Visual Plan Intent。
    只输出 intent（layout, visual_description, design_notes 等），不写 final prompt。
    """
    logger.info(f"VisualPlan: 为 {len(content_plan)} 页生成视觉意图，风格={style_id}")

    try:
        return _do_generate_visual_plan(
            content_plan,
            style_id,
            reference_image_ids,
            style_override,
            global_visual_assets,
            progress_callback,
            has_project_logo,
        )
    except Exception as e:
        logger.exception(f"VisualPlan: 生成视觉方案时发生未预期错误: {e}，返回默认 fallback")
        # 返回安全 fallback，保证 pipeline 不中断
        effective_has_project_logo = bool(reference_image_ids) if has_project_logo is None else bool(has_project_logo)
        return _fallback_visual_plan(content_plan, reference_image_ids or [], has_project_logo=effective_has_project_logo)
