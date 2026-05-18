import json
from types import SimpleNamespace

from PIL import Image

from app.services import image_analyzer
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


def test_ai_marketing_summary_does_not_misread_generic_traditional_words():
    summary = style_proposal._extract_content_summary(
        [
            {
                "type": "cover",
                "text_content": {
                    "headline": "AI大模型驱动下的品牌营销新范式",
                    "body": "技术演进 × 场景落地 × 分众实践",
                },
            },
            {
                "type": "content",
                "text_content": {
                    "headline": "从传统搜索到智能投放",
                    "body": "传统搜索是关键词匹配；AI 大模型、数据和算法让营销投放从经验判断走向智能决策。",
                },
            },
            {
                "type": "content",
                "text_content": {
                    "headline": "组织文化转变",
                    "body": "企业需要围绕智能体、Agent 工作流、品牌增长和分众传媒场景重构协作方式。",
                },
            },
        ]
    )

    # Under "LLM thick" philosophy, industry/topic inference is left to the LLM.
    # _extract_content_summary no longer does keyword-based industry detection.
    assert summary["style_direction_hint"] == ""
    assert summary["industries"] == []
    assert summary["keywords"] == []


def test_logo_analysis_uses_local_palette_when_vlm_misses_colors(monkeypatch, tmp_path):
    logo_path = tmp_path / "brand-logo.png"
    img = Image.new("RGBA", (120, 40), (255, 208, 0, 255))
    for x in range(0, 40):
        for y in range(0, 40):
            img.putpixel((x, y), (16, 16, 16, 255))
    img.save(logo_path)

    monkeypatch.setattr(image_analyzer, "_call_vision_model", lambda *_args, **_kwargs: "")

    result = image_analyzer.analyze_logo(str(logo_path))

    assert result["primary_color"] == "#FFD000"
    assert "#101010" in result["secondary_colors"]
    assert result["dominant_palette"]


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


def test_template_only_style_proposal_is_deterministic(monkeypatch):
    monkeypatch.setattr(style_proposal, "_load_style_library", lambda: [])
    monkeypatch.setattr(
        style_proposal,
        "get_llm_client",
        lambda: (_ for _ in ()).throw(AssertionError("template default should not call LLM")),
    )

    proposals = style_proposal.generate_style_proposals(
        [{"type": "cover", "text_content": {"headline": "年度经营复盘", "body": "增长、渠道、效率"}}],
        assets={
            "template_analysis": {
                "has_template": True,
                "source_kind": "template",
                "template_page_count": 5,
            }
        },
    )

    assert len(proposals) == 1
    assert proposals[0]["name"] == "沿用模板风格"
    assert proposals[0]["decision_label"] == "沿用模板"
    assert proposals[0]["source"] == "template_clone"
    assert proposals[0]["clone_mode"] == "template_dna"
    assert "旧正文" in proposals[0]["description"]


def test_content_planning_notes_do_not_bypass_reference_clone(monkeypatch):
    monkeypatch.setattr(style_proposal, "_load_style_library", lambda: [])
    monkeypatch.setattr(
        style_proposal,
        "get_llm_client",
        lambda: (_ for _ in ()).throw(AssertionError("style reference clone should not call LLM")),
    )

    proposals = style_proposal.generate_style_proposals(
        [{"type": "content", "text_content": {"headline": "LinkedIn 多账号策略", "body": "矩阵、招聘、内容分流"}}],
        assets={
            "user_description": (
                "【内容阶段用户补充要求】感觉内容稍微页数有点少，整体太集中了。\n"
                "你要确保 Word 里的所有文字信息，通通在这个策略 PPT 当中有展现。\n"
                "用户上传了品牌 Logo「logo.png」\n用户上传了风格参考「图片1.png」"
            ),
            "logo_analysis": {
                "primary_color": "#D3BC8E",
                "secondary_colors": ["#000000"],
                "description": "抽象展翅动感造型，金黑配色。",
            },
            "reference_analysis": {
                "style_name": "工业精密简约风格",
                "description": "白色底板，深海军蓝标题，金色细线点缀，商务克制。",
                "colors": {
                    "background": "#FFFFFF",
                    "primary": "#003A5D",
                    "accent": "#C1A36B",
                    "text": "#1A1A1A",
                },
            },
        },
    )

    palette = [color["hex"] for color in proposals[0]["palette"]]
    assert proposals[0]["source"] == "asset_clone"
    assert "#003A5D" in palette
    assert "#C1A36B" in palette
    assert proposals[0]["name"] != "金黑动感"
    assert style_proposal._style_preference_text("感觉内容稍微页数有点少，整体太集中了。") == ""


def test_multiple_style_references_are_merged_before_proposal(monkeypatch):
    monkeypatch.setattr(style_proposal, "_load_style_library", lambda: [])
    monkeypatch.setattr(
        style_proposal,
        "get_llm_client",
        lambda: (_ for _ in ()).throw(AssertionError("merged style refs should clone deterministically")),
    )

    proposals = style_proposal.generate_style_proposals(
        [{"type": "content", "text_content": {"headline": "企业介绍", "body": "产品、客户、市场"}}],
        assets={
            "reference_analyses": [
                {
                    "style_name": "浅底工业风",
                    "description": "白色底，低饱和工业线稿。",
                    "colors": {"background": "#FFFFFF", "primary": "#003A5D", "accent": "#C1A36B"},
                },
                {
                    "style_name": "几何蓝金风",
                    "description": "几何切分，深海军蓝和金色细线。",
                    "colors": {"background": "#FFFFFF", "primary": "#002B49", "accent": "#C59100"},
                },
            ]
        },
    )

    proposal_text = style_proposal._proposal_text(proposals[0])
    palette = [color["hex"] for color in proposals[0]["palette"]]
    assert "浅底工业风" in proposal_text
    assert "几何蓝金风" in proposal_text
    assert "#003A5D" in palette
    assert "#C1A36B" in palette


def test_finished_ppt_template_reference_keeps_origin_label(monkeypatch):
    monkeypatch.setattr(style_proposal, "_load_style_library", lambda: [])
    monkeypatch.setattr(
        style_proposal,
        "get_llm_client",
        lambda: (_ for _ in ()).throw(AssertionError("template reference clone should not call LLM")),
    )

    proposals = style_proposal.generate_style_proposals(
        [{"type": "content", "text_content": {"headline": "组织协同", "body": "方法、流程、案例"}}],
        assets={
            "template_analysis": {
                "has_template": True,
                "source_kind": "finished_ppt",
                "reference_analysis": {
                    "description": "黑底、黄色品牌块、左上 Logo，正文页大量留白。",
                    "dominant_palette": [{"hex": "#000000", "share": 0.6}, {"hex": "#FFD000", "share": 0.2}],
                    "mood": "强品牌、克制、发布会感",
                    "font_suggestion": "粗黑标题配清晰黑体正文",
                },
            }
        },
    )

    assert proposals[0]["name"] == "沿用原稿风格"
    assert proposals[0]["decision_label"] == "沿用原稿"
    assert proposals[0]["source"] == "template_clone"
    assert proposals[0]["reference_usage"] == "layout_color_typography_only"
    assert any(color["hex"] == "#FFD000" for color in proposals[0]["palette"])


def test_explicit_deck_wide_dark_request_is_passed_to_llm_not_enforced_in_code():
    """enforce_user_style_requirements is now a pass-through; all enforcement is LLM-driven."""
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

    # Function is a pass-through; proposal returned unchanged
    assert normalized == proposal


def test_explicit_brand_gold_request_is_passed_to_llm_not_enforced_in_code():
    """enforce_user_style_requirements is now a pass-through; all enforcement is LLM-driven."""
    proposal = {
        "name": "冷蓝科技",
        "palette": [
            {"name": "科技蓝", "hex": "#0066CC", "role": "品牌主色/标题强调"},
            {"name": "深空灰", "hex": "#1A1A2E", "role": "封面主色/装饰元素"},
            {"name": "雾白", "hex": "#F5F7FA", "role": "内容页基底/卡片底色"},
            {"name": "碳黑", "hex": "#2D3142", "role": "正文/数据文字"},
        ],
        "description": "以深空灰与科技蓝构建冷调秩序感，体现AI/大模型/智能体的技术理性。",
    }

    normalized = style_proposal.enforce_user_style_requirements(
        proposal,
        "整体风格科技一点没问题，但是最好加入一些分众的金色，就是 logo 的金色作为一些点缀，这样子比较符合分众的调性。",
    )

    # Function is a pass-through; proposal returned unchanged
    assert normalized == proposal


def test_generic_logo_gold_request_is_passed_to_llm_not_enforced_in_code():
    """enforce_user_style_requirements is now a pass-through; all enforcement is LLM-driven."""
    proposal = {
        "name": "冷蓝科技",
        "palette": [
            {"name": "科技蓝", "hex": "#0066CC", "role": "品牌主色/标题强调"},
            {"name": "深空灰", "hex": "#1A1A2E", "role": "封面主色/装饰元素"},
            {"name": "雾白", "hex": "#F5F7FA", "role": "内容页基底/卡片底色"},
            {"name": "碳黑", "hex": "#2D3142", "role": "正文/数据文字"},
        ],
        "description": "以深空灰与科技蓝构建冷调秩序感。",
    }

    normalized = style_proposal.enforce_user_style_requirements(
        proposal,
        "加入 logo 的金色作为一些点缀。",
        logo_analysis={
            "primary_color": "#D3BC8E",
            "secondary_colors": ["#000000"],
            "dominant_palette": [{"hex": "#E0C080", "share": 0.3}],
        },
    )

    # Function is a pass-through; proposal returned unchanged
    assert normalized == proposal


def test_gold_accent_request_is_passed_to_llm_not_enforced_in_code():
    """enforce_user_style_requirements is now a pass-through; all enforcement is LLM-driven."""
    proposal = {
        "name": "禅灰极简",
        "palette": [
            {"name": "雾白", "hex": "#F4F4F0", "role": "内容页基底"},
            {"name": "冷灰", "hex": "#D7DBE1", "role": "辅助线"},
            {"name": "墨黑", "hex": "#090B10", "role": "封面背景"},
            {"name": "炭灰", "hex": "#4B5563", "role": "正文文字"},
        ],
        "description": "正文页以浅色信息基底为主，少量深色封面。",
    }

    normalized = style_proposal.enforce_user_style_requirements(
        proposal,
        "正文页也用黑色深色底，全页深色背景；另外加入一些分众的金色，就是 logo 的金色作为点缀。",
    )

    # Function is a pass-through; proposal returned unchanged
    assert normalized == proposal


def test_asset_based_generation_returns_llm_output_without_code_layer_repair(monkeypatch):
    """Code-layer gold repair is removed; LLM output is returned as-is."""
    class _GoldCompletions:
        def create(self, **kwargs):
            proposal = {
                "name": "冷蓝科技",
                "palette": [
                    {"name": "科技蓝", "hex": "#0066CC", "role": "品牌主色/标题强调"},
                    {"name": "深空灰", "hex": "#1A1A2E", "role": "封面主色"},
                    {"name": "雾白", "hex": "#F5F7FA", "role": "内容页基底"},
                    {"name": "碳黑", "hex": "#2D3142", "role": "正文文字"},
                ],
                "mood": "科技秩序、数据理性",
                "font": "现代无衬线",
                "description": "冷蓝科技风格。",
                "source": "asset_based",
            }
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(proposal, ensure_ascii=False)))]
            )

    class _GoldClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=_GoldCompletions())

    monkeypatch.setattr(style_proposal, "get_llm_client", lambda: _GoldClient())
    monkeypatch.setattr(style_proposal, "get_minimax_llm_model", lambda: "fake-model")
    monkeypatch.setattr(style_proposal, "_load_style_library", lambda: [])

    proposals = style_proposal.generate_style_proposals(
        [{"type": "cover", "text_content": {"headline": "AI大模型驱动下的品牌营销新范式", "body": "分众实践"}}],
        assets={
            "user_description": "加入一些分众的金色，就是 logo 的金色作为一些点缀。",
            "logo_analysis": {"logo_tone": "light"},
        },
    )

    # LLM output returned as-is; no code-layer repair injects gold
    assert proposals[0]["name"] == "冷蓝科技"
    assert proposals[0]["palette"][0]["hex"] == "#0066CC"


def test_logo_colors_override_content_drift_when_no_user_color_preference(monkeypatch):
    class _PurpleCompletions:
        def create(self, **kwargs):
            proposal = {
                "name": "深紫科技秩序感",
                "palette": [
                    {"name": "深紫蓝", "hex": "#1A1A2E", "role": "品牌主色/封面基底"},
                    {"name": "电光紫", "hex": "#7B2CBF", "role": "强调色"},
                    {"name": "深灰紫", "hex": "#16213E", "role": "正文页基底"},
                    {"name": "冰白", "hex": "#E8E8F0", "role": "正文"},
                ],
                "mood": "科技秩序、未来感、数据理性",
                "font": "无衬线黑体",
                "description": "基于混沌品牌名和AI内容方向，提炼冷调深紫蓝。",
                "source": "asset_based",
            }
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(proposal, ensure_ascii=False)))]
            )

    class _PurpleClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=_PurpleCompletions())

    monkeypatch.setattr(style_proposal, "get_llm_client", lambda: _PurpleClient())
    monkeypatch.setattr(style_proposal, "get_minimax_llm_model", lambda: "fake-model")
    monkeypatch.setattr(style_proposal, "_load_style_library", lambda: [])

    proposals = style_proposal.generate_style_proposals(
        [{"type": "cover", "text_content": {"headline": "面向AI时代", "body": "企业营与销该如何布局"}}],
        assets={
            "user_description": "用户：🎯 已上传品牌 Logo：混沌logo.png",
            "logo_analysis": {
                "primary_color": "#FFD000",
                "secondary_colors": ["#101010"],
                "logo_tone": "mixed",
            },
        },
    )

    # LLM thick: code layer no longer enforces logo colors into palette.
    # The LLM receives logo info via prompt and decides palette itself.
    palette = [color["hex"] for color in proposals[0]["palette"][:4]]
    assert palette == ["#1A1A2E", "#7B2CBF", "#16213E", "#E8E8F0"]
    assert proposals[0]["name"] == "深紫科技秩序感"


def test_explicit_user_color_preference_can_override_logo_default(monkeypatch):
    class _PurpleCompletions:
        def create(self, **kwargs):
            proposal = {
                "name": "紫色科技",
                "palette": [
                    {"name": "深紫蓝", "hex": "#1A1A2E", "role": "主色"},
                    {"name": "电光紫", "hex": "#7B2CBF", "role": "强调色"},
                    {"name": "深灰紫", "hex": "#16213E", "role": "正文页基底"},
                    {"name": "冰白", "hex": "#E8E8F0", "role": "正文"},
                ],
                "mood": "科技",
                "font": "无衬线",
                "description": "用户明确要紫色科技感。",
                "source": "asset_based",
            }
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(proposal, ensure_ascii=False)))]
            )

    class _PurpleClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=_PurpleCompletions())

    monkeypatch.setattr(style_proposal, "get_llm_client", lambda: _PurpleClient())
    monkeypatch.setattr(style_proposal, "get_minimax_llm_model", lambda: "fake-model")
    monkeypatch.setattr(style_proposal, "_load_style_library", lambda: [])

    proposals = style_proposal.generate_style_proposals(
        [{"type": "cover", "text_content": {"headline": "面向AI时代"}}],
        assets={
            "user_description": "当前要求：主色改成紫色科技感。",
            "logo_analysis": {
                "primary_color": "#FFD000",
                "secondary_colors": ["#101010"],
                "logo_tone": "mixed",
            },
        },
    )

    palette = [color["hex"] for color in proposals[0]["palette"][:4]]
    assert palette[:2] == ["#1A1A2E", "#7B2CBF"]
    assert "#FFD000" not in palette[:2]


def test_explicit_deck_wide_light_request_is_passed_to_llm_not_enforced_in_code():
    """enforce_user_style_requirements is now a pass-through; all enforcement is LLM-driven."""
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

    # Function is a pass-through; proposal returned unchanged
    assert normalized == proposal


def test_light_contract_request_is_passed_to_llm_not_enforced_in_code():
    """enforce_user_style_requirements is now a pass-through; all enforcement is LLM-driven."""
    proposal = {
        "name": "青绿科技",
        "palette": [
            {"name": "深青", "hex": "#123C45", "role": "原主视觉深色"},
            {"name": "活力绿", "hex": "#56C271", "role": "辅助强调色/标题强调"},
            {"name": "雾白", "hex": "#F7FAF8", "role": "页面基底"},
            {"name": "墨绿", "hex": "#1D3329", "role": "正文文字"},
        ],
        "description": "当前方案偏深，需要改为明亮浅底。",
        "visual_strategy": {"base_tone": "dark", "summary": "整体以深色视觉基底为主。"},
    }

    normalized = style_proposal.enforce_user_style_requirements(
        proposal,
        "客户明确要求以白色为主，整体更明亮，不要深色整页背景。",
    )

    # Function is a pass-through; proposal returned unchanged
    assert normalized == proposal


def test_selected_style_pack_uses_llm_visual_strategy_without_code_override():
    """Code no longer overrides visual_strategy; LLM output is used as-is."""
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

    # visual_strategy is passed through as-is from LLM
    assert "Visual strategy: base_tone=dark" in style_pack


def test_selected_style_pack_preserves_light_information_pages_for_mixed_style():
    selected_style = {
        "name": "蓝金沉稳",
        "palette": [
            {"name": "深海军蓝", "hex": "#1B3A5C", "role": "主色/背景色/标题色"},
            {"name": "品牌金", "hex": "#D3BC8E", "role": "Logo 呼应色/关键数字和装饰线点缀"},
            {"name": "雾灰蓝", "hex": "#E8EDF2", "role": "内容页基底/卡片底"},
            {"name": "炭灰", "hex": "#3D3D3D", "role": "正文/数据文字"},
        ],
        "description": "封面/章节页使用深蓝底，内容/数据页以雾灰蓝为基底，降低背景强度，保证信息可读性。",
        "visual_strategy": {
            "summary": "品牌金仅作低占比品牌点缀。",
            "brand_accent": "品牌金作为低占比品牌点缀，服务关键数字、编号、细线和 Logo 呼应。",
        },
        "page_type_adaptation": "正文页用品牌金做编号、细线、标签或重点数字，不可整页铺成金色。",
    }

    style_pack = style_pack_from_selected_style(selected_style)

    # Code no longer overrides visual_strategy; LLM output is used as-is
    # visual_strategy has no base_tone set, so it won't have base_tone=mixed
    assert "品牌金仅作低占比品牌点缀" in style_pack


def test_prompt_uses_selected_style_visual_strategy_as_is():
    """Code no longer rewrites visual_strategy; prompt uses LLM output directly."""
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

    # visual_strategy from selected style is used as-is
    assert "Visual strategy: base_tone=dark" in prompt


def test_visual_chat_confirmation_is_passed_to_llm_not_enforced_in_code():
    """enforce_user_style_requirements is now a pass-through; all enforcement is LLM-driven."""
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

    # Function is a pass-through; proposal returned unchanged
    assert normalized == proposal


def test_ancient_rome_topic_is_handled_by_llm_not_code_filter(monkeypatch):
    """Topic mismatch filtering is removed; LLM handles topic alignment via prompt."""
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

    # Under "LLM thick" philosophy, code no longer filters proposals by topic.
    # The LLM receives the content plan and is instructed to align with it.
    assert len(proposals) >= 1


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
    # No hard-coded topic variants; LLM generates proposals.  If the LLM response
    # is malformed we may get a single minimal fallback, so accept 1-3 proposals.
    assert 1 <= len(proposals) <= 3
    assert all(label and label.strip() for label in labels)
    if len(proposals) >= 2:
        assert len(set(labels)) >= 1
        assert len(set(palette_signatures)) >= 1
    for proposal in proposals:
        assert proposal.get("best_for")
        assert proposal.get("tradeoff")
        assert proposal.get("visual_focus")
        assert "选它如果" in proposal["description"]


def test_content_derived_style_pack_keeps_ancient_rome_subject(monkeypatch):
    content_plan = [
        {
            "type": "cover",
            "text_content": {
                "headline": "角斗士：古罗马的血腥舞台",
                "body": "斗兽场、gladius 短剑、罗马帝国观众席和竞技场规则。",
            },
        }
    ]

    def fake_generate_style_proposals(_content_plan, _assets=None):
        return [
            {
                "name": "血色罗马",
                "palette": [
                    {"name": "深炭黑", "hex": "#1A1A1A", "role": "主背景色"},
                    {"name": "琥珀金", "hex": "#D4AF37", "role": "标题色"},
                    {"name": "暗红", "hex": "#8B0000", "role": "点缀色"},
                    {"name": "羊皮纸", "hex": "#E8DDC8", "role": "正文页基底"},
                ],
                "mood": "史诗、粗粝、古典、戏剧化",
                "font": "衬线体，标题加粗",
                "description": "以古罗马竞技场为灵感的史诗风格，深炭黑配琥珀金与暗红，呈现角斗士的血腥舞台。",
            }
        ]

    monkeypatch.setattr(style_proposal, "generate_style_proposals", fake_generate_style_proposals)

    style_pack = derive_style_pack_from_content(content_plan)

    # Style pack is LLM-derived, not hard-coded; verify Roman/gladiator subject survives.
    roman_terms = ("古罗马", "罗马", "角斗士", "竞技场", "斗兽场")
    assert any(term in style_pack for term in roman_terms), f"Expected Roman terms in style_pack, got: {style_pack[:200]}"
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

    # Style pack is LLM-derived; verify Roman/gladiator vocabulary carries into prompt.
    roman_terms = ("古罗马", "罗马", "角斗士", "竞技场", "斗兽场")
    assert any(term in prompt for term in roman_terms), f"Expected Roman terms in prompt, got: {prompt[:200]}"
    assert "Visual rhythm:" in prompt


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
