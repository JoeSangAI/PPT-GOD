import json
from types import SimpleNamespace

from app.services import style_proposal
from app.api.chat import _visual_style_requirement_text
from app.services.prompt_engine import _compact_style_pack, generate_prompt_for_page
from app.services.style_pack import derive_style_pack_from_content, style_pack_from_selected_style


def _style(style_id, alias, category="编辑、杂志与潮流型"):
    return {
        "id": style_id,
        "name": alias,
        "category": category,
        "palette": ["#111111", "#7A1F1D", "#E8DDC8", "#A8743A"],
        "fonts": ["serif", "sans-serif"],
        "best_for": [],
        "avoid": [],
        "description": f"{alias} 风格描述",
        "aliases": [alias],
    }


class _FakeCompletions:
    def create(self, **kwargs):
        generic = [
            {
                "name": "瑞士设计风",
                "palette": ["#FFFFFF", "#111111", "#E1312D", "#1D5BFF"],
                "mood": "严谨、商务",
                "font": "Helvetica",
                "description": "适合严谨企业报告的信息秩序。",
                "source": "swiss_design",
            },
            {
                "name": "luxury",
                "palette": ["#0D0D0D", "#F5F5F5", "#D4AF37", "#C5B358"],
                "mood": "高端、奢华",
                "font": "Cinzel",
                "description": "适合高端品牌和奢侈品展示。",
                "source": "dark_luxury",
            },
            {
                "name": "苹果风",
                "palette": ["#000000", "#FFFFFF", "#0066CC", "#FF2D55"],
                "mood": "现代、发布会",
                "font": "San Francisco",
                "description": "适合产品发布和高端演讲。",
                "source": "apple_keynote",
            },
        ]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(generic, ensure_ascii=False)))]
        )


class _FakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_FakeCompletions())


def test_user_style_description_overrides_reference_clone_shortcut(monkeypatch):
    captured = {}

    class _AssetCompletions:
        def create(self, **kwargs):
            captured["prompt"] = kwargs["messages"][-1]["content"]
            proposal = {
                "name": "冷白极简",
                "palette": [
                    {"name": "冷白", "hex": "#F8FAFC", "role": "正文页基底"},
                    {"name": "雾灰", "hex": "#CBD5E1", "role": "分割线"},
                    {"name": "炭黑", "hex": "#111827", "role": "标题文字"},
                    {"name": "冰蓝", "hex": "#38BDF8", "role": "少量强调"},
                ],
                "mood": "冷静、留白、克制",
                "font": "几何无衬线体",
                "description": "按最新聊天要求去掉红色暖调，改成冷白留白系统，内容页保持清晰克制。",
                "source": "asset_based",
            }
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(proposal, ensure_ascii=False)))]
            )

    class _AssetClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=_AssetCompletions())

    monkeypatch.setattr(style_proposal, "get_llm_client", lambda: _AssetClient())
    monkeypatch.setattr(style_proposal, "get_minimax_llm_model", lambda: "fake-model")
    monkeypatch.setattr(style_proposal, "_load_style_library", lambda: [])

    proposals = style_proposal.generate_style_proposals(
        [{"type": "cover", "text_content": {"headline": "品牌策略", "body": "年度沟通材料"}}],
        assets={
            "reference_analysis": {
                "style_name": "红色暖调",
                "description": "大面积红色背景，暖色装饰。",
                "dominant_palette": [{"hex": "#B91C1C", "share": 0.6}],
                "colors": {"primary": "#B91C1C"},
            },
            "user_description": "不要原来的红色暖调，改成冷白极简、更多留白。",
        },
    )

    assert proposals[0]["name"] == "冷白极简"
    assert "不要原来的红色暖调" in captured["prompt"]
    assert "聊天要求优先级" in captured["prompt"]


def test_explicit_deck_wide_dark_request_removes_light_content_policy():
    proposal = {
        "name": "墨韵金调",
        "palette": [
            {"name": "琥珀金", "hex": "#FFCD00", "role": "品牌主色"},
            {"name": "檀墨", "hex": "#2B2316", "role": "强视觉页主色"},
            {"name": "暖玉白", "hex": "#FFF9E6", "role": "正文页基底"},
            {"name": "焦墨", "hex": "#1A1A1A", "role": "正文数据文字"},
        ],
        "description": "整体以浅色信息基底为主；正文页以浅底和留白保证阅读效率。",
    }

    normalized = style_proposal.enforce_user_style_requirements(
        proposal,
        "正文也可以用黑色的，主要都是以黑色的、深色的底作为内容页，全页深色背景。",
    )

    joined = " ".join(
        str(normalized.get(key) or "")
        for key in ("name", "description", "page_type_adaptation", "content_style_hint")
    )
    assert normalized["visual_strategy"]["base_tone"] == "dark"
    assert normalized["palette"][0]["role"] == "整套页面背景/内容页深色基底"
    assert "正文页不使用浅底" in normalized["visual_strategy"]["content_treatment"]
    assert "不得自动切换成白底" in normalized["page_type_adaptation"]
    assert "浅色信息基底为主" not in joined
    assert "正文页以浅底" not in joined


def test_explicit_deck_wide_light_request_overrides_dark_reference_policy():
    proposal = {
        "name": "柔紫暖白",
        "palette": [
            {"name": "柔紫", "hex": "#C4B4E0", "role": "品牌主色/视觉锚点色"},
            {"name": "米白", "hex": "#F9F8F5", "role": "页面基底/主背景"},
            {"name": "淡紫", "hex": "#E8E0F0", "role": "内容区/卡片底色"},
            {"name": "墨灰紫", "hex": "#3A3038", "role": "正文/标题文字"},
        ],
        "description": "舍弃黑紫色调，改为以米白为基底、柔紫为主色的明亮组合。",
        "visual_strategy": {
            "base_tone": "dark",
            "summary": "整体以深色视觉基底为主；信息页保持同一深色系基底。",
            "content_treatment": "信息页保持同一深色系基底，用高对比暗色卡片保证阅读效率。",
        },
        "page_type_adaptation": "页面类型适配规则：先保持整套深色视觉基底，再按页面功能调节强弱。",
    }

    normalized = style_proposal.enforce_user_style_requirements(
        proposal,
        "客户说他不喜欢这个黑紫的整个调性，我们要换成以白色为主，也就是明亮一点的颜色。使用明亮一点的紫色，而不是那种很深邃的黑紫感觉。",
    )

    joined = " ".join(
        str(normalized.get(key) or "")
        for key in ("description", "page_type_adaptation", "content_style_hint")
    )
    assert normalized["visual_strategy"]["base_tone"] == "light"
    assert normalized["palette"][0]["role"] == "整套页面主背景/内容页浅色基底"
    assert "白色/米白/浅色明亮基底" in normalized["visual_strategy"]["summary"]
    assert "黑紫或深邃暗色整页背景" in normalized["page_type_adaptation"]
    assert "整体以深色视觉基底为主" not in joined


def test_selected_style_pack_repairs_stale_dark_strategy_for_light_contract():
    selected_style = {
        "name": "柔紫暖白",
        "palette": [
            {"name": "柔紫", "hex": "#C4B4E0", "role": "品牌主色/视觉锚点色"},
            {"name": "米白", "hex": "#F9F8F5", "role": "页面基底/主背景"},
            {"name": "淡紫", "hex": "#E8E0F0", "role": "内容区/卡片底色"},
            {"name": "墨灰紫", "hex": "#3A3038", "role": "正文/标题文字"},
        ],
        "description": "舍弃黑紫色调，改为以米白为基底、柔紫为主色、玫瑰粉与浅金作温暖点缀的明亮组合。",
        "visual_strategy": {
            "base_tone": "dark",
            "summary": "整体以深色视觉基底为主；信息页保持同一深色系基底。",
            "content_treatment": "信息页保持同一深色系基底。",
        },
        "page_type_adaptation": "页面类型适配规则：先保持整套深色视觉基底，再按页面功能调节强弱。",
    }

    style_pack = style_pack_from_selected_style(selected_style)

    assert "Visual strategy: base_tone=light" in style_pack
    assert "整体以深色视觉基底为主" not in style_pack
    assert "先保持整套深色视觉基底" not in style_pack
    assert "白色、米白或淡紫浅底" in style_pack


def test_prompt_rewrites_stale_dark_visual_intent_when_selected_style_is_light():
    selected_style = {
        "name": "柔紫暖白",
        "palette": [
            {"name": "柔紫", "hex": "#C4B4E0", "role": "品牌主色/视觉锚点色"},
            {"name": "米白", "hex": "#F9F8F5", "role": "页面基底/主背景"},
            {"name": "淡紫", "hex": "#E8E0F0", "role": "内容区/卡片底色"},
            {"name": "墨灰紫", "hex": "#3A3038", "role": "正文/标题文字"},
        ],
        "description": "舍弃黑紫色调，改为以米白为基底、柔紫为主色的明亮组合。",
        "visual_strategy": {"base_tone": "dark", "summary": "整体以深色视觉基底为主。"},
    }
    style_pack = style_pack_from_selected_style(selected_style)

    prompt = generate_prompt_for_page(
        page_intent={
            "page_num": 3,
            "type": "content",
            "layout": "content_top",
            "visual_evidence": "深色背景中的三层关系图示",
            "visual_description": "保持深色基底统一，使用高对比暗色卡片承载内容，保证阅读效率同时延续深色视觉基调。",
        },
        content_text={"headline": "定位", "body": ["灵咖啡不仅仅是一家咖啡店。"]},
        style_text_override=style_pack,
    )

    assert "Visual strategy: base_tone=light" in prompt
    assert "保持深色基底统一" not in prompt
    assert "高对比暗色卡片" not in prompt
    assert "延续深色视觉基调" not in prompt
    assert "保持浅色基底统一" in prompt
    assert "白色/米白浅色背景" in prompt


def test_visual_chat_confirmation_is_part_of_style_action_alignment():
    proposal = {
        "name": "墨韵金调",
        "palette": [
            {"name": "琥珀金", "hex": "#FFCD00", "role": "品牌主色"},
            {"name": "檀墨", "hex": "#2B2316", "role": "强视觉页主色"},
            {"name": "暖玉白", "hex": "#FFF9E6", "role": "正文页基底"},
            {"name": "焦墨", "hex": "#1A1A1A", "role": "正文数据文字"},
        ],
        "description": "整体以浅色信息基底为主；正文页以浅底和留白保证阅读效率。",
    }

    requirement = _visual_style_requirement_text(
        "重新生成视觉方案",
        {"response": "我会把正文页也改为黑色/深色底，全套页面保持全页深色背景。"},
        history=[{"role": "user", "content": "不要浅色正文页，主要以黑色深色底作为内容页。"}],
    )
    normalized = style_proposal.enforce_user_style_requirements(proposal, requirement)

    joined = " ".join(
        str(normalized.get(key) or "")
        for key in ("description", "page_type_adaptation", "content_style_hint")
    )
    assert normalized["visual_strategy"]["base_tone"] == "dark"
    assert "正文页不使用浅底" in normalized["visual_strategy"]["content_treatment"]
    assert "浅色信息基底为主" not in joined
    assert "正文页以浅底" not in joined


def test_ancient_rome_topic_rejects_generic_business_proposals(monkeypatch):
    monkeypatch.setattr(style_proposal, "get_llm_client", lambda: _FakeClient())
    monkeypatch.setattr(style_proposal, "get_minimax_llm_model", lambda: "fake-model")
    monkeypatch.setattr(
        style_proposal,
        "_load_style_library",
        lambda: [
            _style("classic_pop_sculpture_vaporwave", "古典波普风", "Artistic & Avant-garde"),
            _style("magazine_editorial", "magazine"),
            _style("sports_energy", "运动风", "流行、娱乐与高冲击型"),
            _style("swiss_design", "瑞士设计风", "结构与技术型"),
            _style("dark_luxury", "luxury", "商务与高端型"),
            _style("apple_keynote", "苹果风", "商务与高端型"),
        ],
    )
    content_plan = [
        {
            "type": "cover",
            "text_content": {
                "headline": "角斗士：古罗马的血腥舞台",
                "subhead": "探索罗马帝国最具争议的娱乐文化",
                "body": "斗兽场、gladius 短剑、竞技场规则、观众与帝国权力。",
            },
        },
        {
            "type": "content",
            "text_content": {
                "headline": "角斗士的训练生活",
                "body": "武器类型、盾牌、盔甲、竞技场中的生死对抗。",
            },
        },
    ]

    proposals = style_proposal.generate_style_proposals(content_plan)

    assert len(proposals) == 3
    joined = " ".join(f"{p['name']} {p['description']} {p['source']}" for p in proposals)
    assert "瑞士设计风" not in joined
    assert "apple_keynote" not in joined
    assert "dark_luxury" not in joined
    assert "古罗马" in joined
    assert "角斗士" in joined
    assert "竞技场" in joined or "斗兽场" in joined


def test_topic_style_proposals_are_actionable_decision_choices():
    content_plan = [
        {
            "type": "cover",
            "text_content": {
                "headline": "角斗士：古罗马的血腥舞台",
                "subhead": "探索罗马帝国最具争议的娱乐文化",
                "body": "斗兽场、gladius 短剑、竞技场规则、观众与帝国权力。",
            },
        },
        {
            "type": "content",
            "text_content": {
                "headline": "角斗士的训练生活",
                "body": "武器类型、盾牌、盔甲、竞技场中的生死对抗。",
            },
        },
    ]

    proposals = style_proposal.generate_style_proposals(content_plan)

    labels = [p.get("decision_label") for p in proposals]
    palette_signatures = [
        tuple(color["hex"] for color in p["palette"][:4])
        for p in proposals
    ]
    assert labels == ["沉浸史诗", "展陈可读", "力量冲突"]
    assert len(set(palette_signatures)) == 3
    for proposal in proposals:
        assert proposal.get("best_for")
        assert proposal.get("tradeoff")
        assert proposal.get("visual_focus")
        assert "选它如果" in proposal["description"]


def test_content_derived_style_pack_keeps_ancient_rome_subject():
    content_plan = [
        {
            "type": "cover",
            "text_content": {
                "headline": "角斗士：古罗马的血腥舞台",
                "body": "斗兽场、gladius 短剑、罗马帝国观众席和竞技场规则。",
            },
        }
    ]

    style_pack = derive_style_pack_from_content(content_plan)

    assert "古罗马竞技史诗风" in style_pack
    assert "斗兽场" in style_pack
    assert "历史史诗" in style_pack
    assert "瑞士设计" not in style_pack
    assert "苹果发布会" not in style_pack


def test_compact_style_pack_preserves_visual_rhythm_before_cosmetic_details():
    style_text = "\n".join([
        "Style: 古罗马竞技史诗风",
        "Palette: #171310, #7A1F1D, #E8DDC8, #A8743A",
        "Mood: 史诗、粗粝、古典、戏剧化",
        "Visual strategy: base_tone=mixed; 按页面功能分组控制明暗",
        "Typography: very long type guidance",
        "Texture/material: stone, bronze, parchment",
        "Page type adaptation: 封面/章节页用竞技场暗部，正文页用石材浅底。",
        "Reference usage: style text only",
        "Visual rhythm: 每页画面证据必须来自古罗马角斗士主题：斗兽场、短剑、盾牌、盔甲、雕塑、石柱、观众席。",
    ])

    compact = _compact_style_pack(style_text, max_lines=7)

    assert "Visual rhythm:" in compact
    assert "斗兽场" in compact
    assert "Reference usage:" not in compact


def test_prompt_inherits_ancient_rome_style_pack_subject():
    content_text = {
        "headline": "角斗士的训练生活",
        "body": "训练体系、武器类型、竞技场中的生死对抗。",
    }
    style_pack = derive_style_pack_from_content([
        {"type": "content", "text_content": content_text}
    ])

    prompt = generate_prompt_for_page(
        page_intent={
            "page_num": 2,
            "type": "content",
            "layout": "content_split",
            "visual_evidence": "训练场、短剑、盾牌和竞技场观众席",
            "visual_description": "右侧用训练场和武器形成画面证据，左侧承载正文。",
        },
        content_text=content_text,
        style_text_override=style_pack,
    )

    assert "Style:\nStyle: 古罗马竞技史诗风" in prompt
    assert "Visual rhythm:" in prompt
    assert "斗兽场" in prompt
    assert "短剑" in prompt


def test_selected_style_description_survives_as_visual_rhythm():
    selected_style = {
        "name": "古罗马竞技史诗风",
        "palette": [
            {"name": "火山岩黑", "hex": "#171310"},
            {"name": "血酒红", "hex": "#7A1F1D"},
        ],
        "mood": "史诗、粗粝、古典",
        "font": "标题用古典衬线，正文用高可读黑体。",
        "description": "配图优先斗兽场、盾牌、短剑、雕塑和观众席，整体保持历史史诗与古典材质方向。",
    }

    style_pack = style_pack_from_selected_style(selected_style)
    compact = _compact_style_pack(style_pack or "")

    assert "Visual rhythm:" in compact
    assert "斗兽场" in compact
    assert "短剑" in compact
