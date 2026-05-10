import json
from types import SimpleNamespace

from app.services import style_proposal
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
