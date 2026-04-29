import functools
import glob
import json
import logging
import os
from typing import List, Dict, Optional

import yaml

from app.core.config import settings
from app.core.llm_client import get_llm_client

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def _load_style_library() -> List[Dict]:
    """加载 nano-banana-ppt/styles 目录下的所有风格库文件。（结果已缓存）"""
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    styles_dir = os.path.join(base_dir, "..", "..", "nano-banana-ppt", "styles")
    styles_dir = os.path.abspath(styles_dir)

    if not os.path.isdir(styles_dir):
        logger.warning(f"Style library not found at {styles_dir}")
        return []

    styles = []
    for path in glob.glob(os.path.join(styles_dir, "*.md")):
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()

            if not content.startswith("---"):
                continue

            parts = content.split("---", 2)
            meta = yaml.safe_load(parts[1]) if len(parts) >= 2 else {}
            body = parts[2].strip() if len(parts) >= 3 else ""

            # 提取描述：取"风格描述"段落的第一句/段
            desc = ""
            for line in body.splitlines():
                line = line.strip()
                if line and not line.startswith("#") and not line.startswith("-"):
                    desc = line
                    break

            styles.append({
                "id": meta.get("id", ""),
                "name": meta.get("id", "").replace("_", " ").title(),
                "category": meta.get("category", ""),
                "palette": meta.get("palette", [])[:6],
                "fonts": meta.get("fonts", []),
                "best_for": meta.get("best_for", []),
                "avoid": meta.get("avoid", []),
                "description": desc,
                "aliases": meta.get("aliases", []),
            })
        except Exception as e:
            logger.warning(f"Failed to parse style file {path}: {e}")

    return styles


def _extract_content_summary(content_plan: List[Dict]) -> Dict:
    """从 content plan 提取用于风格匹配的摘要信息。"""
    headlines = []
    page_types = set()
    industries = []
    keywords = []

    for page in content_plan[:15]:
        text = page.get("text_content", {})
        h = text.get("headline", "")
        if h:
            headlines.append(h)

        ptype = page.get("type", "content")
        page_types.add(ptype)

        # 简单关键词提取（从 headline 中找行业/场景词）
        for kw in ["金融", "医疗", "教育", "科技", "消费", "品牌", "数据", "学术", "艺术", "设计", "汽车", "地产", "零售", "投资", "AI", "产品", "战略"]:
            if kw in h:
                keywords.append(kw)

    # 推断行业/场景
    if "金融" in keywords or "投资" in keywords:
        industries.append("金融/投资")
    if "科技" in keywords or "AI" in keywords or "数据" in keywords:
        industries.append("科技/数据")
    if "消费" in keywords or "品牌" in keywords or "零售" in keywords:
        industries.append("消费/品牌")
    if "学术" in keywords:
        industries.append("学术/研究")
    if "艺术" in keywords or "设计" in keywords:
        industries.append("艺术/设计")
    if not industries:
        industries.append("通用商务")

    return {
        "headlines": headlines[:8],
        "page_types": list(page_types),
        "industries": list(set(industries)),
        "keywords": list(set(keywords)),
        "total_pages": len(content_plan),
    }


def generate_style_proposals(content_plan: List[Dict], assets: Optional[Dict] = None) -> List[Dict]:
    """
    根据 Content Plan 生成风格提案。
    - 如果用户提供了素材（logo、参考图、描述等），输出 1 套基于素材的完整风格阐述
    - 如果用户未提供素材，输出 3 套推荐（AI原创1套 + 风格库匹配2套）
    每套包含：name, palette(4色), mood, font, description（专业总监口吻长文本）
    """
    summary = _extract_content_summary(content_plan)
    style_library = _load_style_library()

    # 判断是否有用户素材
    has_assets = assets and any([
        assets.get("logo_analysis"),
        assets.get("reference_analysis"),
        assets.get("user_description"),
        assets.get("template_analysis"),
    ])

    if has_assets:
        return _generate_asset_based_proposal(content_plan, summary, assets, style_library)

    client = get_llm_client()

    # 构建 style 库摘要，供 LLM 挑选
    style_catalog = []
    for s in style_library:
        style_catalog.append({
            "id": s["id"],
            "category": s["category"],
            "best_for": s["best_for"],
            "avoid": s["avoid"],
            "palette_preview": s["palette"][:4],
            "short_desc": s["description"][:80],
        })

    prompt = f"""你是一位顶级 PPT 视觉总监。你的任务是根据客户的 PPT 内容，输出 3 套风格提案。你要像真正的设计总监一样说话——**具体、有观点、有逻辑，而不是堆砌形容词和文学修辞**。

【PPT 内容概览】
- 主题关键词：{"、".join(summary["keywords"]) if summary["keywords"] else "商务演示"}
- 行业/场景：{"、".join(summary["industries"])}
- 页面类型：{"、".join(summary["page_types"])}
- 总页数：{summary["total_pages"]}

【内容标题摘录】（这些标题决定了 PPT 的核心主题和受众）
{"\n".join("- " + h for h in summary["headlines"][:8])}

【可用风格库（第 2、3 套必须从中选择）】
{json.dumps(style_catalog, ensure_ascii=False, indent=2)}

【输出格式】
严格输出 JSON 数组，3 个对象：
{{
  "name": "风格名称（简洁直观的设计语言命名，如'流体玻璃极简'、'折叠纸艺温暖'，禁止用'原生之境'这类虚词）",
  "palette": [
    {{"name": "深墨蓝", "hex": "#0A1628", "role": "主背景色"}},
    {{"name": "象牙白", "hex": "#E8D5A3", "role": "标题色"}},
    {{"name": "米白", "hex": "#F5F5F0", "role": "正文色"}},
    {{"name": "深蓝", "hex": "#1E3A5F", "role": "点缀色"}}
  ],
  "mood": "氛围标签（3-5个具体形容词，如'冷静、专业、克制'）",
  "font": "字体建议（如'无衬线黑体，标题加粗'）",
  "description": "风格说明（250-400字）",
  "source": "original（第1套）或 风格库id（第2、3套）"
}}

【3 套方案的结构要求】

第 1 套：AI 原创（source = "original"）
- 你必须基于 PPT 的主题、行业和受众，设计一套最适合的原创风格。
- 不要泛泛而谈"商务通用"，要具体到这份 PPT 的内容。比如如果是科技投资主题，就要说明为什么冷色调适合传达数据可信度；如果是消费品品牌，就要说明为什么暖色调能唤起购买欲。

第 2、3 套：风格库匹配（source = 对应 id）
- 你必须从上方风格库中挑选，**挑选依据必须是这份 PPT 的内容**。
- 不要随机选。要根据 PPT 的主题、行业、页面类型来判断哪个库最适合。
- 在 description 中，必须明确说明："我从风格库中选择了『XX』，因为它原本的设计定位是……，非常适合这份 PPT 的……需求。"

【description 写作要求——极其重要】

1. **第一段必须开门见山**：直接说「这份 PPT 讲的是 XXX，所以我认为最适合的风格是……」。不要绕弯子。

2. **配色必须具体到功能**：
   - 背景色用什么，为什么（如"深色背景能让数据图表更突出"）
   - 标题色用什么，为什么（如"高对比的白色标题在深色背景上能第一时间抓住注意力"）
   - 正文色用什么，为什么（如"浅灰色正文避免与标题抢戏，同时保证长文可读性"）
   - 点缀色用什么，为什么（如"橙色仅用于关键数据和 CTA，控制使用面积在 5% 以内"）

3. **禁止以下说辞**（这些都是用户讨厌的空话套话）：
   - "凝视深渊的勇气与沉静"
   - "极度克制、极度干净的空间感"
   - "没有任何多余的视觉噪音干扰观众的情绪投入"
   - "让文字和图像成为唯一的主角"
   - "为情感内容提供最大程度的纯净舞台"
   - 任何类似的文学修辞、哲学隐喻、抽象形容词堆砌

4. **要像在给客户讲方案**：客户关心的是"我的PPT用这个风格会不会更好看、更专业、更能说服听众"。所以你要解释的是：**这个风格如何解决这个PPT的具体问题**（文字多怎么办、数据多怎么办、需要品牌感怎么办）。

5. **情绪氛围关键词放在最后**，3-5 个词即可，不要展开解释。

【参考口吻示例】（这种具体的、有功能指向的说明才是对的）
"这份 PPT 是关于老庙黄金的品牌升级方案，面向的是经销商和投资人，需要在专业感和品牌溢价之间找到平衡。我推荐「白色为主、海军蓝为辅」的配色逻辑：白色（#FFFFFF / #F4F7FA）作为背景，因为这份 PPT 有大量产品参数和财务数据，白色背景能最大化文字可读性，避免深色背景导致的阅读疲劳。海军蓝（#1A365D）用于所有标题和重点数据，它传递的是沉稳和信任——对投资人来说，这是最重要的情绪信号。琥珀金（#D69E2E）作为点缀色，仅用于品牌 logo 复刻和关键价格数字，面积控制在 5% 以内，制造「克制中的奢华感」。字体上，标题用思源黑体 Heavy 保证远距离投影的清晰度，正文用 Regular 保证长段落的阅读舒适。整体情绪：干净、通透、高端。"
"""

    response = client.chat.completions.create(
        model=settings.MINIMAX_LLM_MODEL,
        messages=[
            {
                "role": "system",
                "content": "你是世界一流的 PPT 视觉总监。必须且只能输出合法的 JSON 数组，严禁添加任何额外说明文本。description 字段必须具体、说人话、解决实际问题，严禁堆砌形容词和哲学隐喻。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.7,
    )

    raw = response.choices[0].message.content or ""
    raw = raw.strip()
    logger.info(f"StyleProposal: LLM raw response length={len(raw)}")

    import re
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    raw = re.sub(r"^```(?:json)?\s*|```$", "", raw, flags=re.MULTILINE | re.IGNORECASE).strip()

    proposals = []
    if raw:
        try:
            proposals = json.loads(raw)
            if not isinstance(proposals, list):
                logger.warning("StyleProposal: LLM 返回的不是数组，使用默认方案")
                proposals = []
        except json.JSONDecodeError as e:
            logger.warning(f"StyleProposal: JSON 解析失败: {e}，raw前200字: {raw[:200]}")
    else:
        logger.warning("StyleProposal: LLM 返回空内容，使用默认方案")

    # 如果解析失败或数量不足，用 style 库兜底
    if len(proposals) < 3:
        logger.info("StyleProposal: 使用 style 库兜底")
        fallback_ids = ["swiss_design", "dark_luxury", "apple_keynote"]
        fallback_map = {s["id"]: s for s in style_library}
        for fid in fallback_ids:
            if fid in fallback_map and len(proposals) < 3:
                s = fallback_map[fid]
                proposals.append({
                    "name": s["aliases"][0] if s["aliases"] else s["name"],
                    "palette": s["palette"][:4] if len(s["palette"]) >= 4 else s["palette"] + ["#FFFFFF"],
                    "mood": s["category"],
                    "font": ", ".join(s["fonts"][:2]) if s["fonts"] else "无衬线",
                    "description": s["description"],
                    "source": s["id"],
                })

    # 确保每个提案都有所需字段，并把 source 放入 description 的末尾提示（仅库匹配项）
    for p in proposals:
        p.setdefault("name", "未命名风格")
        if not p.get("palette"):
            p["palette"] = [
                {"name": "深墨蓝", "hex": "#333333", "role": "主色"},
                {"name": "纯白", "hex": "#FFFFFF", "role": "辅色"},
                {"name": "中灰", "hex": "#999999", "role": "点缀色"},
                {"name": "浅灰", "hex": "#CCCCCC", "role": "背景色"},
            ]
        p.setdefault("mood", "专业商务")
        p.setdefault("font", "无衬线")
        p.setdefault("description", "")
        p.setdefault("source", "original")

        # 兼容旧格式：如果 palette 是字符串数组，转换为对象数组
        if p["palette"] and isinstance(p["palette"][0], str):
            default_roles = ["主色", "辅色", "点缀色", "背景色"]
            default_names = ["主色", "辅色", "点缀色", "背景色"]
            p["palette"] = [
                {
                    "name": default_names[i] if i < len(default_names) else f"颜色{i+1}",
                    "hex": color,
                    "role": default_roles[i] if i < len(default_roles) else f"配色{i+1}",
                }
                for i, color in enumerate(p["palette"])
            ]

        # 如果 description 太短，补一段默认话术
        if len(p["description"]) < 60:
            first_color = p["palette"][0]["hex"] if p["palette"] and isinstance(p["palette"][0], dict) else str(p["palette"][0])
            second_color = p["palette"][1]["hex"] if len(p["palette"]) > 1 and isinstance(p["palette"][1], dict) else str(p["palette"][1]) if len(p["palette"]) > 1 else ""
            p["description"] = f"「{p['name']}」是一套{p['mood']}的视觉方案。以 {first_color} 为主色调，搭配 {second_color} 营造整体氛围，适合本演示文稿的内容调性。"

    return proposals[:3]


def _generate_asset_based_proposal(
    content_plan: List[Dict],
    summary: Dict,
    assets: Dict,
    style_library: List[Dict],
) -> List[Dict]:
    """基于用户素材生成 1 套完整风格阐述。"""
    client = get_llm_client()

    # 整理素材信息
    logo = assets.get("logo_analysis") or {}
    ref = assets.get("reference_analysis") or {}
    user_desc = assets.get("user_description", "").strip()
    template = assets.get("template_analysis") or {}

    asset_sections = []
    if logo.get("description") or logo.get("primary_color"):
        asset_sections.append(f"【Logo 分析】\n主色: {logo.get('primary_color', 'N/A')}\n辅助色: {', '.join(logo.get('secondary_colors', []))}\n调性: {logo.get('mood', 'N/A')}\n字体风格: {logo.get('font_style', 'N/A')}\n行业气质: {logo.get('industry_vibe', 'N/A')}\n描述: {logo.get('description', 'N/A')}")

    if ref.get("description") or ref.get("colors", {}).get("primary"):
        colors = ref.get("colors", {})
        asset_sections.append(f"【参考图分析】\n背景色: {colors.get('background', 'N/A')}\n主色: {colors.get('primary', 'N/A')}\n点缀色: {colors.get('accent', 'N/A')}\n文字色: {colors.get('text', 'N/A')}\n构图: {ref.get('composition_style', 'N/A')}\n氛围: {ref.get('mood', 'N/A')}\n字体建议: {ref.get('font_suggestion', 'N/A')}\n描述: {ref.get('description', 'N/A')}")

    if template.get("has_template"):
        asset_sections.append(f"【模板信息】\n用户提供了参考模板，包含封面、目录、内容、结尾页。模板页的配色和布局应作为核心参考。")

    if user_desc:
        asset_sections.append(f"【用户风格描述】\n{user_desc}")

    prompt = f"""你是一位顶级 PPT 视觉总监。客户已经提供了明确的设计素材，你的任务是基于这些素材，输出 1 套完整的视觉风格阐述。

【PPT 内容概览】
- 主题关键词：{"、".join(summary["keywords"]) if summary["keywords"] else "商务演示"}
- 行业/场景：{"、".join(summary["industries"])}
- 页面类型：{"、".join(summary["page_types"])}
- 总页数：{summary["total_pages"]}

【用户提供的素材】
{"\n\n".join(asset_sections)}

【输出格式】
严格输出 JSON 对象（不是数组）：
{{
  "name": "风格名称（简洁直观的设计语言命名）",
  "palette": [
    {{"name": "颜色名称", "hex": "#0A1628", "role": "主背景色"}},
    {{"name": "颜色名称", "hex": "#E8D5A3", "role": "标题色"}},
    {{"name": "颜色名称", "hex": "#F5F5F0", "role": "正文色"}},
    {{"name": "颜色名称", "hex": "#1E3A5F", "role": "点缀色"}}
  ],
  "mood": "氛围标签（3-5个具体形容词）",
  "font": "字体建议",
  "description": "风格说明（250-400字，具体、说人话、解决实际问题）"
}}

【写作要求】
1. 第一段必须开门见山：直接说基于客户提供的素材，最适合的风格是什么
2. 配色必须具体到功能（背景色为什么、标题色为什么、点缀色为什么）
3. 必须明确回应用户素材中的信息（如 Logo 主色怎么用、参考图的构图怎么借鉴）
4. 禁止堆砌形容词和哲学隐喻
5. 要像在给客户讲方案：解释这个风格如何解决这份 PPT 的具体问题"""

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
    logger.info(f"StyleProposal(AssetBased): LLM raw response length={len(raw)}")

    import re
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    raw = re.sub(r"^```(?:json)?\s*|```$", "", raw, flags=re.MULTILINE | re.IGNORECASE).strip()

    proposal = {}
    if raw:
        try:
            proposal = json.loads(raw)
            if not isinstance(proposal, dict):
                proposal = {}
        except json.JSONDecodeError as e:
            logger.warning(f"StyleProposal(AssetBased): JSON 解析失败: {e}")

    if not proposal:
        # 回退：从风格库中找最接近的一套
        logger.info("StyleProposal(AssetBased): 使用风格库兜底")
        fallback_ids = ["swiss_design", "dark_luxury", "apple_keynote"]
        fallback_map = {s["id"]: s for s in style_library}
        for fid in fallback_ids:
            if fid in fallback_map:
                s = fallback_map[fid]
                proposal = {
                    "name": s["aliases"][0] if s["aliases"] else s["name"],
                    "palette": s["palette"][:4] if len(s["palette"]) >= 4 else s["palette"] + ["#FFFFFF"],
                    "mood": s["category"],
                    "font": ", ".join(s["fonts"][:2]) if s["fonts"] else "无衬线",
                    "description": s["description"],
                }
                break

    # 标准化
    proposal.setdefault("name", "基于素材的定制风格")
    if not proposal.get("palette"):
        proposal["palette"] = [
            {"name": "深墨蓝", "hex": "#333333", "role": "主色"},
            {"name": "纯白", "hex": "#FFFFFF", "role": "辅色"},
            {"name": "中灰", "hex": "#999999", "role": "点缀色"},
            {"name": "浅灰", "hex": "#CCCCCC", "role": "背景色"},
        ]
    proposal.setdefault("mood", "专业商务")
    proposal.setdefault("font", "无衬线")
    proposal.setdefault("description", "")
    proposal.setdefault("source", "asset_based")

    # 兼容旧格式
    if proposal["palette"] and isinstance(proposal["palette"][0], str):
        default_roles = ["主色", "辅色", "点缀色", "背景色"]
        default_names = ["主色", "辅色", "点缀色", "背景色"]
        proposal["palette"] = [
            {
                "name": default_names[i] if i < len(default_names) else f"颜色{i+1}",
                "hex": color,
                "role": default_roles[i] if i < len(default_roles) else f"配色{i+1}",
            }
            for i, color in enumerate(proposal["palette"])
        ]

    if len(proposal.get("description", "")) < 60:
        first_color = proposal["palette"][0]["hex"] if proposal["palette"] else ""
        proposal["description"] = f"「{proposal['name']}」是一套{proposal['mood']}的视觉方案。以 {first_color} 为主色调，适合本演示文稿的内容调性。"

    return [proposal]
