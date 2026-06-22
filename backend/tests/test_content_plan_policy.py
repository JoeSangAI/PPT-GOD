import json
import inspect
import re

import pytest

from app.services import content_plan as content_plan_module
from app.services.content_plan import (
    build_long_deck_skeleton,
    build_document_driven_long_deck_draft,
    content_plan_from_page_map,
    generate_content_plan,
    generate_content_page_map,
    build_direct_ppt_replicate_outline,
    validate_direct_ppt_replicate_outline,
    _document_preservation_mode,
    _document_preservation_policy,
    _enforce_requested_page_range,
    _extend_outline_to_target_count,
    _generate_deck_blueprint,
    _generate_outline_from_blueprint_in_chunks,
    _is_general_transform_request,
    _should_generate_deck_blueprint,
    generate_long_deck_outline_chunk,
    _infer_document_driven_page_count,
    parse_page_map_markdown,
    parse_paginated_markdown_content_plan,
    parse_exported_content_plan_markdown,
    resolve_content_plan_page_target,
    resolve_requested_content_plan_page_count,
    should_generate_incremental_long_deck,
    _soft_page_bounds,
)
from app.services.source_intent import infer_intent_contract


@pytest.fixture(autouse=True)
def stub_content_director_contract(monkeypatch):
    def fake_director(**_kwargs):
        return {
            "task_type": "source_to_ppt",
            "source_use": "faithful",
            "coverage": "balanced",
            "compression": "medium",
            "depth": "standard",
            "page_budget_policy": "auto",
            "structure_policy": "source_order",
            "confidence": 0.55,
            "evidence": [],
        }

    monkeypatch.setattr(content_plan_module, "infer_content_director_contract", fake_director)


INTERNAL_SOURCE_MARKER_RE = re.compile(
    r"---\s*(?:SOURCE|PAGE|CHAPTER|AVAILABLE_FIGURES)\b|SOURCE filename|AVAILABLE_FIGURES|FIGURE\s+figure_id",
    flags=re.IGNORECASE,
)


def _iter_nested_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_nested_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_nested_strings(item)


def assert_no_internal_source_markers(value):
    leaked = [
        text
        for text in _iter_nested_strings(value)
        if INTERNAL_SOURCE_MARKER_RE.search(text)
    ]
    assert leaked == []


def _test_talk_notes(*parts: str) -> str:
    content = "；".join(part for part in parts if part)
    return f"讲稿内容：{content or '围绕本页材料讲出具体事实、判断和结论。'}"


def test_short_uploaded_text_defaults_to_faithful_mode():
    documents = "这是用户写好的完整段落。\n\n关键判断：保持原文表达，不要重写。"

    assert _document_preservation_mode(documents, "帮我做成 PPT") == "faithful"
    policy = _document_preservation_policy(documents, "帮我做成 PPT")
    assert "整理成 PPT，而不是重写" in policy
    assert "尽量保留原文" in policy


def test_brief_source_is_not_treated_as_uploaded_material():
    documents = '--- SOURCE filename="brief" kind="brief" ---\n--- PAGE 1 ---\n帮我做一份品牌策略 PPT'

    assert _document_preservation_mode(documents, "帮我做一份品牌策略 PPT") == "none"


def test_transform_request_allows_restructure_but_preserves_facts():
    documents = "第一章 很长的材料\n" * 200

    assert _document_preservation_mode(documents, "请总结提炼成 10 页") == "transform"
    assert "不得改变事实" in _document_preservation_policy(documents, "请总结提炼成 10 页")


def test_page_count_expansion_is_general_transform_request():
    assert _is_general_transform_request("以这个为基础扩成 60-80 页培训课")
    assert _is_general_transform_request("做成60页到80页的PPT")


def test_unprompted_one_page_payload_is_not_treated_as_deck_size():
    assert resolve_requested_content_plan_page_count("恒河猴实验", 1) is None
    assert resolve_content_plan_page_target("恒河猴实验", 1) == (10, 9, 11)


def test_explicit_chinese_one_page_request_is_preserved():
    assert resolve_requested_content_plan_page_count("做成一页 PPT：恒河猴实验", 1) == 1
    assert resolve_content_plan_page_target("做成一页 PPT：恒河猴实验", 1) == (1, 1, 2)


def test_per_page_feedback_is_not_treated_as_one_page_request():
    topic = "原来演讲的体量每一页的 ppt 内容要更有深度一些。每一页的内容可以再增加一点。"

    assert content_plan_module.infer_page_count_from_topic(topic) is None
    assert resolve_requested_content_plan_page_count(topic, 60) == 60


def test_content_plan_page_types_keep_quote_as_legacy_hero_alias():
    assert "quote" not in content_plan_module.CANONICAL_CONTENT_PLAN_TYPES
    assert content_plan_module._canonical_content_plan_type("quote") == "hero"
    assert content_plan_module._canonical_content_plan_type("金句") == "hero"
    assert "cover、toc、section、content、data、hero、quote、ending" not in inspect.getsource(content_plan_module)


def test_colloquial_chinese_page_range_survives_numbered_brief_items():
    topic = """我要做一个完整的《增长黑客》这本书的 PPT。

你先去搜一下这本书的全文内容，然后再把它做成一个 PPT，要求如下：
1. 篇幅大概三四十页
2. 内容要详实，要让人家看完这个 PPT 就能大概知道《增长黑客》这本书讲了什么"""

    assert content_plan_module.infer_page_count_range_from_topic(topic) == (30, 40)
    assert content_plan_module.infer_page_count_from_topic(topic) == 40
    assert resolve_content_plan_page_target(topic, None) == (40, 30, 40)

    job = content_plan_module._build_content_plan_job(topic=topic, documents="")
    assert job.page_count == 40
    assert job.min_pages == 30
    assert job.max_pages == 40

    brief_documents = f'--- SOURCE filename="brief" kind="brief" pages="1" ---\n--- PAGE 1 ---\n{topic}'
    assert resolve_content_plan_page_target(topic, 40, brief_documents) == (40, 30, 40)


def test_page_unit_before_numbered_list_item_is_not_page_count():
    topic = """做成一个 PPT，要求如下：
1. 篇幅尽量多页
2. 内容要详实"""

    assert content_plan_module.infer_page_count_from_topic(topic) is None
    assert resolve_content_plan_page_target(topic, None) == (10, 9, 11)


def test_low_confidence_tiny_target_does_not_trim_model_expanded_outline():
    def outline(page_total: int) -> list[dict]:
        return [
            {
                "page_num": idx,
                "type": "cover" if idx == 1 else "ending" if idx == page_total else "content",
                "section_title": "增长黑客",
                "text_content": {
                    "headline": f"第 {idx} 页",
                    "body": "" if idx == 1 else f"- 具体内容 {idx}",
                },
                "speaker_notes": f"讲解第 {idx} 页",
            }
            for idx in range(1, page_total + 1)
        ]

    preserved = content_plan_module._normalize_outline_page_count(
        outline(32),
        2,
        strict_page_count=False,
        allow_expanded_outline_override=True,
    )
    trimmed = content_plan_module._normalize_outline_page_count(
        outline(32),
        2,
        strict_page_count=False,
        allow_expanded_outline_override=False,
    )
    strict_trimmed = content_plan_module._normalize_outline_page_count(
        outline(32),
        2,
        strict_page_count=True,
        allow_expanded_outline_override=True,
    )

    assert len(preserved) == 32
    assert preserved[0]["type"] == "cover"
    assert preserved[-1]["type"] == "ending"
    assert [page["page_num"] for page in preserved] == list(range(1, 33))
    assert len(trimmed) == 3
    assert len(strict_trimmed) == 2


def test_explicit_chinese_one_page_request_disables_expanded_outline_override():
    job = content_plan_module._build_content_plan_job(
        topic="做成一页 PPT：恒河猴实验",
        page_count=1,
        documents="",
    )

    assert job.page_count == 1
    assert job.allow_expanded_outline_override is False


def test_long_page_target_uses_deck_blueprint():
    assert _should_generate_deck_blueprint((60, 80), 80, "课程材料")
    assert _should_generate_deck_blueprint(None, 60, "课程材料")
    assert _should_generate_deck_blueprint((60, 80), 80, "")
    assert not _should_generate_deck_blueprint((20, 30), 30, "课程材料")
    assert not _should_generate_deck_blueprint(None, 12, "课程材料")


def test_long_deck_target_uses_incremental_generation():
    assert resolve_content_plan_page_target("做成 60 到 80 页课程", 80) == (80, 60, 80)
    sparse_material = "# 主题\n\n- 只有一个观点"
    assert resolve_content_plan_page_target("做成 60 到 80 页课程", 80, sparse_material)[0] < 60
    assert should_generate_incremental_long_deck("做成 60 到 80 页课程", 80, "")
    assert not should_generate_incremental_long_deck("做成 60 到 80 页课程", 80, sparse_material)
    assert not should_generate_incremental_long_deck("做成 12 页课程", 12, "课程材料")


def test_strategy_selector_routes_brief_long_prompt_to_long_deck():
    assert hasattr(content_plan_module, "_build_content_plan_job")
    assert hasattr(content_plan_module, "_select_content_plan_strategy")

    job = content_plan_module._build_content_plan_job(
        topic="给企业家做一场 2 小时增长课程，生成 60 页左右的 PPT，内容要深入。",
        page_count=60,
        documents="",
    )

    assert content_plan_module._select_content_plan_strategy(job) == "page_map"


def test_strategy_selector_routes_dense_uploaded_material_to_long_deck():
    documents = "# 长篇经营课原文\n\n" + "\n\n".join(
        f"## 模块 {idx}\n"
        f"核心判断 {idx}：企业经营要把战略、产品、组织、财务和客户价值放在同一个闭环里。\n"
        f"课堂案例 {idx}：" + "真实业务材料。" * 130
        for idx in range(1, 51)
    )

    job = content_plan_module._build_content_plan_job(
        topic="请尽量贴合原文，整理成一份高质量 PPT。",
        documents=documents,
    )

    assert job.page_count >= 40
    assert content_plan_module._select_content_plan_strategy(job) == "page_map"


def test_strategy_selector_preserves_uploaded_ppt_from_long_deck_route():
    documents = '--- PPT_SOURCE filename="source.pptx" pages=60 ---\n\n--- 第1页 ---\n原稿标题'

    job = content_plan_module._build_content_plan_job(
        topic="请 1:1 复刻这份 PPT，尽量贴合原稿。",
        documents=documents,
    )

    assert job.page_count == 60
    assert job.mode == "direct_replicate"
    assert content_plan_module._select_content_plan_strategy(job) == "page_map"


def test_percent_fidelity_is_not_inferred_as_page_count():
    assert content_plan_module.infer_page_count_from_topic("保持页数和里面的信息 100%一致性") is None


def test_direct_replicate_pdf_source_uses_source_pages_without_model(monkeypatch):
    documents = '''--- SOURCE filename="deck.pdf" kind="pdf" ---
--- PAGE 1 ---
封面标题
封面副标题
--- AVAILABLE_FIGURES ---
FIGURE figure_id="deck.pdf:p1:page" source_document="deck.pdf" source_type="pdf" source_page_num="1" figure_role="source_page" content_significance="high"
--- PAGE 2 ---
核心结论
证据 A
证据 B
--- AVAILABLE_FIGURES ---
FIGURE figure_id="deck.pdf:p2:page" source_document="deck.pdf" source_type="pdf" source_page_num="2" figure_role="source_page" content_significance="high"
'''

    def fail_if_model_called(**_kwargs):
        raise AssertionError("direct PDF replicate should use deterministic source pages")

    monkeypatch.setattr(content_plan_module, "_generate_model_page_map", fail_if_model_called)
    outline = generate_content_plan(
        topic="请 1:1 复刻这个 pdf ppt，保持页数和里面的信息 100%一致性。",
        documents=documents,
        intent_contract={
            "task_type": "replicate",
            "rewrite_level": "none",
            "page_order_policy": "preserve",
            "page_count_policy": "same",
            "source_fidelity": "verbatim",
            "visual_source_use": "page_reference",
            "confidence": 0.9,
            "evidence": ["1:1", "复刻"],
        },
    )

    assert len(outline) == 2
    assert [page["text_content"]["headline"] for page in outline] == ["封面标题", "核心结论"]
    assert outline[0]["source_refs"] == [{
        "source_document": "deck.pdf",
        "source_page_num": 1,
        "source_type": "pdf",
        "reason": "direct_replicate",
    }]
    assert outline[0]["figure_refs"] == []


def test_direct_replicate_pdf_keeps_dense_first_page_as_content(monkeypatch):
    documents = '''--- SOURCE filename="deck.pdf" kind="pdf" ---
--- PAGE 1 ---
本融资概要说明
本融资概要仅为非凡资本为该项目准备的初步评估材料，不能作为出价基础、投资决策基础或达成任何交易的基础
潜在购买方应完全依靠自身判断能力对公司进行考察和商业分析，并作出评估
本概要包含基于公司管理层及行业信息渠道的声明、估计和预测，这些假设有可能被证明正确或错误
本概要信息高度保密，未经书面同意不得复印、复制或散发
请各潜在投资方在独立判断基础上进一步沟通
所有资料应以尽职调查和正式交易文件为准
--- AVAILABLE_FIGURES ---
FIGURE figure_id="deck.pdf:p1:page" source_document="deck.pdf" source_type="pdf" source_page_num="1" figure_role="source_page" content_significance="high"
--- PAGE 2 ---
项目优势
市场巨大
技术优势
专业团队
运营优势
--- AVAILABLE_FIGURES ---
FIGURE figure_id="deck.pdf:p2:page" source_document="deck.pdf" source_type="pdf" source_page_num="2" figure_role="source_page" content_significance="high"
'''

    def fail_if_model_called(**_kwargs):
        raise AssertionError("direct PDF replicate should use deterministic source pages")

    monkeypatch.setattr(content_plan_module, "_generate_model_page_map", fail_if_model_called)
    outline = generate_content_plan(
        topic="请 1:1 复刻这个 pdf ppt，保持页数和里面的信息 100%一致性。",
        documents=documents,
        intent_contract={
            "task_type": "replicate",
            "rewrite_level": "none",
            "page_order_policy": "preserve",
            "page_count_policy": "same",
            "source_fidelity": "verbatim",
            "visual_source_use": "page_reference",
            "confidence": 0.9,
            "evidence": ["1:1", "复刻"],
        },
    )

    assert outline[0]["type"] == "content"
    assert outline[0]["text_content"]["headline"] == "本融资概要说明"
    assert "潜在购买方应完全依靠自身判断能力" in outline[0]["text_content"]["body"]
    assert "本概要信息高度保密" in outline[0]["text_content"]["body"]
    assert "所有资料应以尽职调查和正式交易文件为准" in outline[0]["text_content"]["body"]


def test_page_map_explicit_first_content_page_keeps_body():
    page_map = [{
        "page_num": 1,
        "type": "content",
        "section_title": "融资概要",
        "headline": "本融资概要说明",
        "subhead": "",
        "bullets": [
            "本融资概要仅为非凡资本为该项目准备的初步评估材料",
            "潜在购买方应完全依靠自身判断能力对公司进行考察和商业分析",
        ],
        "generation_status": "page_map_source",
    }]

    outline = content_plan_from_page_map(page_map)

    assert outline[0]["type"] == "content"
    assert "潜在购买方应完全依靠自身判断能力" in outline[0]["text_content"]["body"]


def test_direct_replicate_pdf_keeps_dense_last_page_as_content(monkeypatch):
    documents = '''--- SOURCE filename="deck.pdf" kind="pdf" ---
--- PAGE 1 ---
封面标题
封面副标题
--- PAGE 2 ---
核心优势
市场巨大
技术领先
--- PAGE 3 ---
本轮融资由非凡资本担任财务顾问
非凡资本是产业互联网的创新服务平台，包括投资和投后两大服务版块
旗下有母基金、跟投基金、融资顾问、研究咨询等产品及服务
累计已投资和服务创业公司达数百家
如需约见本项目或了解具体情况请扫码
--- AVAILABLE_FIGURES ---
FIGURE figure_id="deck.pdf:p3:page" source_document="deck.pdf" source_type="pdf" source_page_num="3" figure_role="source_page" content_significance="high"
'''

    def fail_if_model_called(**_kwargs):
        raise AssertionError("direct PDF replicate should use deterministic source pages")

    monkeypatch.setattr(content_plan_module, "_generate_model_page_map", fail_if_model_called)
    outline = generate_content_plan(
        topic="请 1:1 复刻这个 pdf ppt，保持页数和里面的信息 100%一致性。",
        documents=documents,
        intent_contract={
            "task_type": "replicate",
            "rewrite_level": "none",
            "page_order_policy": "preserve",
            "page_count_policy": "same",
            "source_fidelity": "verbatim",
            "visual_source_use": "page_reference",
            "confidence": 0.9,
            "evidence": ["1:1", "复刻"],
        },
    )

    assert outline[-1]["type"] == "content"
    assert outline[-1]["text_content"]["headline"] == "本轮融资由非凡资本担任财务顾问"
    assert "累计已投资和服务创业公司达数百家" in outline[-1]["text_content"]["body"]


def test_direct_replicate_pdf_keeps_dense_contact_last_page_as_content(monkeypatch):
    documents = '''--- SOURCE filename="deck.pdf" kind="pdf" ---
--- PAGE 1 ---
封面标题
封面副标题
--- PAGE 2 ---
联系方式与后续安排
本页同时说明客户案例复盘、实施范围、交付节奏、风险清单、验收标准与下一步会议安排。
请扫码预约后续沟通并确认材料清单。
--- AVAILABLE_FIGURES ---
FIGURE figure_id="deck.pdf:p2:page" source_document="deck.pdf" source_type="pdf" source_page_num="2" figure_role="source_page" content_significance="high"
'''

    def fail_if_model_called(**_kwargs):
        raise AssertionError("direct PDF replicate should use deterministic source pages")

    monkeypatch.setattr(content_plan_module, "_generate_model_page_map", fail_if_model_called)
    outline = generate_content_plan(
        topic="请 1:1 复刻这个 pdf ppt，保持页数和里面的信息 100%一致性。",
        documents=documents,
        intent_contract={
            "task_type": "replicate",
            "rewrite_level": "none",
            "page_order_policy": "preserve",
            "page_count_policy": "same",
            "source_fidelity": "verbatim",
            "visual_source_use": "page_reference",
            "confidence": 0.9,
            "evidence": ["1:1", "复刻"],
        },
    )

    assert outline[-1]["type"] == "content"
    rendered = "\n".join(outline[-1]["text_content"].values())
    assert "实施范围" in rendered


def test_direct_replicate_pdf_joins_wrapped_last_page_lines(monkeypatch):
    documents = '''--- SOURCE filename="deck.pdf" kind="pdf" ---
--- PAGE 1 ---
封面标题
封面副标题
--- PAGE 2 ---
核心优势
市场巨大
技术领先
--- PAGE 3 ---
本轮融资由非凡资本担任财务顾问
非凡资本是产业互联网的创新服务平台，包括投资和投后两大服务版块，旗下有母基金、跟投基金、融资顾
问、研究咨询等产品及服务，累计已投资和服务创业公司达数百家。
如需约见本项目或了解具体情况请扫码
--- AVAILABLE_FIGURES ---
FIGURE figure_id="deck.pdf:p3:page" source_document="deck.pdf" source_type="pdf" source_page_num="3" figure_role="source_page" content_significance="high"
'''

    def fail_if_model_called(**_kwargs):
        raise AssertionError("direct PDF replicate should use deterministic source pages")

    monkeypatch.setattr(content_plan_module, "_generate_model_page_map", fail_if_model_called)
    outline = generate_content_plan(
        topic="请 1:1 复刻这个 pdf ppt，保持页数和里面的信息 100%一致性。",
        documents=documents,
        intent_contract={
            "task_type": "replicate",
            "rewrite_level": "none",
            "page_order_policy": "preserve",
            "page_count_policy": "same",
            "source_fidelity": "verbatim",
            "visual_source_use": "page_reference",
            "confidence": 0.9,
            "evidence": ["1:1", "复刻"],
        },
    )

    rendered = "\n".join(outline[-1]["text_content"].values())

    assert "融资顾问、研究咨询" in rendered
    assert "\n问、研究咨询" not in rendered


def test_page_map_explicit_last_content_page_keeps_type():
    page_map = [
        {
            "page_num": 1,
            "type": "cover",
            "section_title": "封面",
            "headline": "项目标题",
            "bullets": [],
            "generation_status": "page_map_source",
        },
        {
            "page_num": 2,
            "type": "content",
            "section_title": "非凡资本",
            "headline": "本轮融资由非凡资本担任财务顾问",
            "subhead": "",
            "bullets": [
                "非凡资本是产业互联网的创新服务平台",
                "累计已投资和服务创业公司达数百家",
            ],
            "generation_status": "page_map_source",
        },
    ]

    outline = content_plan_from_page_map(page_map)

    assert outline[-1]["type"] == "content"
    assert "累计已投资和服务创业公司达数百家" in outline[-1]["text_content"]["body"]


def test_direct_replicate_pdf_uses_content_figures_not_full_page_as_slide_material(monkeypatch):
    documents = '''--- SOURCE filename="deck.pdf" kind="pdf" ---
--- PAGE 1 ---
产品界面
多模态交互展示
--- AVAILABLE_FIGURES ---
FIGURE figure_id="deck.pdf:p1:page" source_document="deck.pdf" source_type="pdf" source_page_num="1" figure_role="source_page" content_significance="high" image_width="1920" image_height="1080" bbox_area="518400" nearby_text="整页原图"
FIGURE figure_id="deck.pdf:p1:x5:1" source_document="deck.pdf" source_type="pdf" source_page_num="1" figure_role="content" content_significance="high" image_width="900" image_height="520" bbox_area="200000" nearby_text="产品界面 多模态交互 展示"
'''

    def fail_if_model_called(**_kwargs):
        raise AssertionError("direct PDF replicate should use deterministic source pages")

    monkeypatch.setattr(content_plan_module, "_generate_model_page_map", fail_if_model_called)
    outline = generate_content_plan(
        topic="请 1:1 智能复刻这个 pdf ppt，保持原文信息和主要视觉元素。",
        documents=documents,
        intent_contract={
            "task_type": "replicate",
            "rewrite_level": "none",
            "page_order_policy": "preserve",
            "page_count_policy": "same",
            "source_fidelity": "verbatim",
            "visual_source_use": "page_reference",
            "confidence": 0.9,
            "evidence": ["1:1", "复刻"],
        },
    )

    assert outline[0]["figure_refs"] == [{
        "source_document": "deck.pdf",
        "source_page_num": 1,
        "source_type": "pdf",
        "figure_id": "deck.pdf:p1:x5:1",
        "reason": "direct_replicate",
    }]


def test_direct_replicate_pdf_allows_repeated_source_headlines(monkeypatch):
    page_blocks = []
    for page_num, body in [
        (1, "封面\n副标题"),
        (2, "Botlife.AI\n这是一段足够长的原文说明，用于保留原页信息 A"),
        (3, "Botlife.AI\n这是一段足够长的原文说明，用于保留原页信息 B"),
        (4, "结束页\n感谢关注"),
    ]:
        page_blocks.append(f'''--- PAGE {page_num} ---
{body}
--- AVAILABLE_FIGURES ---
FIGURE figure_id="deck.pdf:p{page_num}:page" source_document="deck.pdf" source_type="pdf" source_page_num="{page_num}" figure_role="source_page" content_significance="high"''')
    documents = '--- SOURCE filename="deck.pdf" kind="pdf" ---\n' + "\n".join(page_blocks)

    def fail_if_model_called(**_kwargs):
        raise AssertionError("direct PDF replicate should use deterministic source pages")

    monkeypatch.setattr(content_plan_module, "_generate_model_page_map", fail_if_model_called)
    outline = generate_content_plan(
        topic="请 1:1 复刻这个 pdf ppt，保持页数和里面的信息 100%一致性。",
        documents=documents,
        intent_contract={
            "task_type": "replicate",
            "rewrite_level": "none",
            "page_order_policy": "preserve",
            "page_count_policy": "same",
            "source_fidelity": "verbatim",
            "visual_source_use": "page_reference",
            "confidence": 0.9,
            "evidence": ["1:1", "复刻"],
        },
    )

    assert len(outline) == 4
    assert outline[1]["text_content"]["headline"] == "Botlife.AI"
    assert outline[2]["text_content"]["headline"] == "Botlife.AI"


def test_direct_replicate_pdf_preserves_timeline_dates_and_terminal_milestone(monkeypatch):
    documents = '''--- SOURCE filename="deck.pdf" kind="pdf" ---
--- PAGE 1 ---
封面标题
封面副标题
--- AVAILABLE_FIGURES ---
FIGURE figure_id="deck.pdf:p1:page" source_document="deck.pdf" source_type="pdf" source_page_num="1" figure_role="source_page" content_significance="high"
--- PAGE 2 ---
Botlife.ai 新一代AI社交平台
发展规划
系统开发
2023.8
2023.11
测试版本（完成）
2024.3
α版本（完成）
2024.8
商业化
2024.12
MAU：200万MRR：20万美金
2025.12
MAU：2500万MRR：180万美金
2026.12
MAU：3亿MRR：1500万美金
--- AVAILABLE_FIGURES ---
FIGURE figure_id="deck.pdf:p2:page" source_document="deck.pdf" source_type="pdf" source_page_num="2" figure_role="source_page" content_significance="high"
'''

    def fail_if_model_called(**_kwargs):
        raise AssertionError("direct PDF replicate should use deterministic source pages")

    monkeypatch.setattr(content_plan_module, "_generate_model_page_map", fail_if_model_called)
    outline = generate_content_plan(
        topic="请 1:1 复刻这个 pdf ppt，保持页数和里面的信息 100%一致性。",
        documents=documents,
        intent_contract={
            "task_type": "replicate",
            "rewrite_level": "none",
            "page_order_policy": "preserve",
            "page_count_policy": "same",
            "source_fidelity": "verbatim",
            "visual_source_use": "page_reference",
            "confidence": 0.9,
            "evidence": ["1:1", "复刻"],
        },
    )

    body = outline[1]["text_content"]["body"]
    assert "2023.8 系统开发" in body
    assert "2023.11 测试版本（完成）" in body
    assert "2024.3 α版本（完成）" in body
    assert "2024.8 商业化" in body
    assert "2024.12 MAU：200万MRR：20万美金" in body
    assert "2025.12 MAU：2500万MRR：180万美金" in body
    assert "2026.12 MAU：3亿MRR：1500万美金" in body
    assert "\n- 8\n" not in body
    assert "\n- 12\n" not in body


def test_page_map_merge_restores_source_bullets_when_model_loses_date_facts():
    source_ref = {
        "source_document": "deck.pdf",
        "source_page_num": 13,
        "source_type": "pdf",
        "reason": "direct_replicate",
    }
    source_draft = [{
        "page_num": 1,
        "type": "content",
        "section_title": "原 PDF 逐页复刻",
        "headline": "Botlife.ai 新一代AI社交平台",
        "subhead": "发展规划",
        "bullets": [
            "系统开发",
            "2023.8",
            "2023.11",
            "测试版本（完成）",
            "2024.3",
            "α版本（完成）",
            "2024.8",
            "商业化",
            "2024.12 MAU：200万MRR：20万美金",
            "2025.12 MAU：2500万MRR：180万美金",
            "2026.12 MAU：3亿MRR：1500万美金",
        ],
        "source_refs": [source_ref],
        "generation_status": "page_map_source",
    }]
    lossy_model_map = [{
        "page_num": 1,
        "type": "content",
        "section_title": "原 PDF 逐页复刻",
        "headline": "Botlife.ai 新一代AI社交平台",
        "subhead": "发展规划",
        "bullets": [
            "系统开发",
            "8",
            "11",
            "测试版本（完成）",
            "3",
            "α版本（完成）",
            "8",
            "12 MAU：200万MRR：20万美金",
            "12 MAU：2500万MRR：180万美金",
            "12 MAU：3亿MRR：1500万美金",
        ],
        "source_refs": [source_ref],
        "generation_status": "page_map_model",
    }]

    merged = content_plan_module._merge_page_map_with_source_draft(
        lossy_model_map,
        source_draft,
        target_count=1,
    )

    assert "2023.8" in merged[0]["bullets"]
    assert "2024.12 MAU：200万MRR：20万美金" in merged[0]["bullets"]
    assert "商业化" in merged[0]["bullets"]
    assert "8" not in merged[0]["bullets"]


def test_auto_figure_selection_prefers_content_image_over_source_page_reference():
    source_context = '''--- SOURCE filename="deck.pdf" kind="pdf" pages="1" ---
--- PAGE 8 ---
核心功能界面
--- AVAILABLE_FIGURES ---
FIGURE figure_id="deck.pdf:p8:page" source_document="deck.pdf" source_type="pdf" source_page_num="8" figure_role="source_page" content_significance="high" image_width="1920" image_height="1080" bbox_area="518400" nearby_text="整页画面"
FIGURE figure_id="deck.pdf:p8:x57:1" source_document="deck.pdf" source_type="pdf" source_page_num="8" figure_role="content" content_significance="high" image_width="820" image_height="460" bbox_area="180000" nearby_text="Botlife 产品核心功能界面 多模态交互 用户体验"
'''
    page_map = [{
        "page_num": 1,
        "type": "content",
        "section_title": "产品展示",
        "headline": "核心功能界面",
        "subhead": "",
        "bullets": ["展示 Botlife 产品的多模态交互界面和用户体验"],
        "source_refs": [{
            "source_document": "deck.pdf",
            "source_page_num": 8,
            "source_type": "pdf",
            "reason": "source_page",
        }],
    }]

    outline = content_plan_module.content_plan_from_page_map(page_map, source_context=source_context)

    assert outline[0]["figure_refs"][0]["figure_id"] == "deck.pdf:p8:x57:1"


def test_auto_figure_selection_ranks_by_page_text_relevance():
    source_context = '''--- SOURCE filename="deck.pdf" kind="pdf" pages="1" ---
--- PAGE 15 ---
用户增长策略
--- AVAILABLE_FIGURES ---
FIGURE figure_id="deck.pdf:p15:x1:1" source_document="deck.pdf" source_type="pdf" source_page_num="15" figure_role="content" content_significance="high" image_width="700" image_height="420" bbox_area="160000" nearby_text="三阶段收入结构 订阅 增值收费 API 广告"
FIGURE figure_id="deck.pdf:p15:x2:1" source_document="deck.pdf" source_type="pdf" source_page_num="15" figure_role="content" content_significance="high" image_width="650" image_height="390" bbox_area="140000" nearby_text="用户增长 SEO SEM 社媒运营 KOL KOC 口碑传播 社区运营"
'''
    page_map = [{
        "page_num": 1,
        "type": "content",
        "section_title": "增长策略",
        "headline": "用户增长策略",
        "subhead": "",
        "bullets": ["SEO/SEM", "社媒运营", "KOL/KOC", "社区运营"],
        "source_refs": [{
            "source_document": "deck.pdf",
            "source_page_num": 15,
            "source_type": "pdf",
            "reason": "source_page",
        }],
    }]

    outline = content_plan_module.content_plan_from_page_map(page_map, source_context=source_context)

    assert outline[0]["figure_refs"][0]["figure_id"] == "deck.pdf:p15:x2:1"


def test_long_structured_deck_strategy_is_diagnostics_only(monkeypatch):
    job = content_plan_module._build_content_plan_job(
        topic="给企业家做一场 2 小时增长课程，生成 60 页左右的 PPT，内容要深入。",
        page_count=60,
        documents="# 长篇材料\n\n## 第一章\n\n真实内容",
    )

    monkeypatch.setattr(content_plan_module.get_knowledge_augmenter(), "augment", lambda *_args, **_kwargs: "")

    def fail_if_executed(*_args, **_kwargs):
        raise AssertionError("old long JSON route should not execute from the user-visible strategy dispatcher")

    monkeypatch.setattr(content_plan_module, "_execute_long_structured_deck_strategy", fail_if_executed)

    with pytest.raises(ValueError, match="diagnostics-only"):
        content_plan_module._execute_content_plan_strategy(
            job,
            content_plan_module.CONTENT_PLAN_STRATEGY_LONG_DECK,
        )


def test_generate_content_plan_routes_long_course_to_page_map(monkeypatch):
    documents = "# 赢利与天龙十部：企业高质量增长的经营闭环\n\n" + "\n\n".join(
        f"## 第 {idx} 部：经营模块 {idx}\n\n"
        f"核心判断 {idx}：企业必须把战略、价值、产品、组织和财务放进经营闭环。\n\n"
        f"- 原文金句 {idx}\n"
        f"- 企业家自检问题 {idx}\n"
        f"- 课堂案例 {idx}"
        for idx in range(1, 12)
    )
    calls: dict[str, dict] = {}

    def fake_page_map(**kwargs):
        calls["page_map"] = kwargs
        return [
            {
                "page_num": page_num,
                "type": "cover" if page_num == 1 else "ending" if page_num == 60 else "content",
                "section_title": "课程主体",
                "headline": "赢利与天龙十部" if page_num == 1 else f"第 {page_num} 页课程判断",
                "subhead": "",
                "bullets": [] if page_num in {1, 60} else [f"原文依据 {page_num}", f"课堂讲解 {page_num}"],
                "speaker_notes": _test_talk_notes(f"第 {page_num} 页围绕原文依据和课堂讲解展开。"),
                "visual_suggestion": f"第 {page_num} 页课程视觉建议。",
                "source_refs": [],
                "generation_status": "page_map_model",
            }
            for page_num in range(1, 61)
        ]

    def fake_blueprint(**kwargs):
        raise AssertionError("long course plans should stay on the unified Page Map path")

    def fake_chunked_outline(**kwargs):
        raise AssertionError("long course plans should not enter the old chunked JSON route")

    monkeypatch.setattr(content_plan_module, "_source_draft_page_map", lambda **_kwargs: [])
    monkeypatch.setattr(content_plan_module, "_generate_model_page_map", fake_page_map)
    monkeypatch.setattr(content_plan_module, "_generate_deck_blueprint", fake_blueprint)
    monkeypatch.setattr(content_plan_module, "_generate_outline_from_blueprint_in_chunks", fake_chunked_outline)
    monkeypatch.setattr(content_plan_module.get_knowledge_augmenter(), "augment", lambda *_args, **_kwargs: "")

    outline = generate_content_plan(
        topic="这是一场 2 个小时的企业家分享课程，尽可能用原文整理成 PPT，预计 60 页左右。",
        documents=documents,
        page_count=60,
    )

    assert calls["page_map"]["target_count"] == 60
    assert len(outline) == 60
    assert outline[0]["text_content"]["headline"] == "赢利与天龙十部"
    assert {page["generation_status"] for page in outline} == {"page_map_model"}


def test_source_aware_blueprint_allocates_all_numbered_modules():
    documents = "# 赢利与天龙十部\n\n## 一、总论\n\n" + "\n\n".join(
        f"## {idx}、第{idx}部：模块{idx}\n\n模块正文"
        for idx in "二三四五六七八九十"
    )

    blueprint = content_plan_module._fallback_deck_blueprint(60, 60, 60, documents)

    assert "第八部" in blueprint
    assert "第九部" in blueprint
    assert "第十部" in blueprint
    assert "必须覆盖上传材料中的每个章节" in blueprint


def test_missing_required_source_modules_detects_incomplete_long_outline():
    documents = "# 赢利与天龙十部\n\n## 一、总论\n\n## 二、第一部：战略设计\n\n## 三、第二部：价值创造\n\n## 四、第三部：产品战略"
    outline = [
        {"page_num": 1, "type": "cover", "section_title": "封面", "text_content": {"headline": "赢利与天龙十部"}},
        {"page_num": 2, "type": "section", "section_title": "第一部：战略设计", "text_content": {"headline": "战略设计"}},
    ]

    assert content_plan_module._missing_required_source_modules(outline, documents) == ["第二部", "第三部"]


def test_source_page_section_plan_keeps_late_modules_in_range():
    documents = "# 赢利与天龙十部\n\n## 一、总论\n\n" + "\n\n".join(
        f"## {idx}、第{idx}部：模块{idx}\n\n模块正文"
        for idx in "二三四五六七八九十"
    ) + "\n\n## 十一、结语\n\n收束"

    plan = content_plan_module._source_page_section_plan(documents, 60)

    assert plan[3].startswith("总论")
    assert plan[43].startswith("第八部")
    assert plan[48].startswith("第九部")
    assert plan[52].startswith("第十部")
    assert plan[59] == "结语"


def test_long_deck_chunks_enforce_source_page_section_plan(monkeypatch):
    documents = (
        "# 赢利与天龙十部\n\n"
        "## 一、总论\n\n总论正文\n\n"
        "## 二、第一部：战略设计\n\n战略正文\n\n"
        "## 三、第二部：价值创造\n\n价值正文\n\n"
        "## 四、第三部：产品战略\n\n产品正文"
    )

    class FakeMessage:
        def __init__(self, content):
            self.content = content

    class FakeChoice:
        def __init__(self, content):
            self.message = FakeMessage(content)

    class FakeResponse:
        def __init__(self, content):
            self.choices = [FakeChoice(content)]

    def make_pages(start: int, end: int):
        return [
            {
                "page_num": page_num,
                "type": "section" if page_num not in {1, 8} else "content",
                "section_title": "模型漂移章节",
                "text_content": {
                    "headline": f"### 模型漂移标题 {page_num}",
                    "subhead": "",
                    "body": "模型输出正文",
                },
                "speaker_notes": "模型输出备注",
                "visual_suggestion": "模型输出视觉",
                "source_refs": [],
            }
            for page_num in range(start, end + 1)
        ]

    class FakeCompletions:
        def create(self, **kwargs):
            prompt = kwargs["messages"][1]["content"]
            assert "【本轮页码对应的原文章节（必须逐页遵守）】" in prompt
            match = re.search(r"只生成第 (\d+) 页到第 (\d+) 页", prompt)
            assert match
            start = int(match.group(1))
            end = int(match.group(2))
            return FakeResponse(json.dumps(make_pages(start, end), ensure_ascii=False))

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(content_plan_module, "get_llm_client", lambda: FakeClient())

    outline = content_plan_module._generate_outline_from_blueprint_in_chunks(
        topic="2 小时企业家分享课，尽可能贴合原文，生成 8 页 PPT。",
        documents=documents,
        deck_blueprint="## 全局蓝图\n- P1-P8：按原文章节展开",
        target_count=8,
        min_pages=8,
        max_pages=8,
    )

    assert outline[2]["section_title"] == "总论"
    assert outline[3]["section_title"] == "总论"
    assert outline[4]["section_title"] == "第一部：战略设计"
    assert outline[5]["section_title"] == "第二部：价值创造"
    assert outline[6]["section_title"] == "第三部：产品战略"
    assert outline[2]["type"] == "section"
    assert outline[3]["type"] == "content"
    assert outline[4]["type"] == "section"


def test_long_course_generation_failure_does_not_save_source_draft_as_success(monkeypatch):
    documents = "# 赢利与天龙十部：企业高质量增长的经营闭环\n\n" + "\n\n".join(
        f"## 经营模块 {idx}\n\n"
        f"- 原文要点 {idx}\n"
        f"- 企业家课堂案例 {idx}"
        for idx in range(1, 12)
    )

    monkeypatch.setattr(content_plan_module, "_source_draft_page_map", lambda **_kwargs: [])
    monkeypatch.setattr(content_plan_module, "_generate_model_page_map", lambda **_kwargs: [])
    monkeypatch.setattr(content_plan_module.get_knowledge_augmenter(), "augment", lambda *_args, **_kwargs: "")

    with pytest.raises(ValueError) as exc_info:
        generate_content_plan(
            topic="2 小时企业家课程，尽量保留原文，预计 60 页左右。",
            documents=documents,
            page_count=60,
        )
    assert "failed before producing usable model pages" in str(exc_info.value) or "质量不足" in str(exc_info.value)


def test_short_soft_page_target_stays_close_to_requested_count():
    assert _soft_page_bounds(8) == (7, 9)
    assert resolve_content_plan_page_target("做一份约 8 页儿童课", 8) == (8, 7, 9)


def test_long_uploaded_document_infers_larger_default_page_count():
    documents = "# AI 时代企业营与销课程\n\n" + "\n\n".join(
        f"## 模块 {idx}\n\n"
        f"### 关键问题 {idx}\n\n"
        f"- 第 {idx} 组事实、数据和案例\n"
        f"- 第 {idx} 组企业主启示\n"
        f"- 第 {idx} 组落地动作"
        for idx in range(1, 100)
    )

    inferred = _infer_document_driven_page_count(documents)
    target, min_pages, max_pages = resolve_content_plan_page_target("帮我做成 PPT", None, documents)

    assert inferred is not None
    assert target >= 32
    assert min_pages < target == max_pages
    assert target <= 60
    assert resolve_content_plan_page_target("帮我做成 10 页 PPT", 10, documents) == (10, 9, 11)


def _medium_length_course_manuscript() -> str:
    section_titles = [
        "总论：企业为什么需要一套经营闭环",
        "第一部：战略设计 - 世界级标准",
        "第二部：价值创造 - 独一无二的价值锚点",
        "第三部：产品战略 - 聚焦品牌第一",
        "第四部：组织发展 - 强研发、大营销",
        "第五部：预算管理 - 全员增长",
        "第六部：营销管理 - 价量双增",
        "第七部：用户经营 - 成就大客户",
        "第八部：绩效管理 - 机制设计",
        "第九部：财务管理 - 经营检测系统",
        "第十部：资本杠杆 - 股权价值最大化",
        "结语：经营的金刚圈",
    ]
    body = (
        "企业经营不是单点解决问题，而是建立一套彼此咬合的经营闭环。"
        "战略决定标准，价值支撑价格，产品承载价值，组织提供能力，预算让战略变成施工图。"
        "课堂上要保留原文的判断、追问、案例和行动要求，让企业家能够顺着讲稿一步一步复盘。"
        "这不是摘要页，而是演讲还原页，需要把原文里的逻辑顺序、关键句子、连续追问和经营动作展开。"
        "每一页都应该服务现场讲述，让听众看到问题从哪里来、判断如何成立、下一步应该怎么做。"
    )
    return "# 赢利与天龙十部：企业高质量增长的经营闭环\n\n" + "\n\n".join(
        f"## {title}\n\n" + "\n".join(
            [
                f"核心判断：{body}",
                f"关键问题：{body}",
                f"行动要求：{body}",
            ]
        )
        for title in section_titles
    )


def test_restoration_priority_prompt_expands_medium_document_into_long_deck(monkeypatch):
    documents = _medium_length_course_manuscript()
    topic = (
        "【文件：赢利与天龙十部完整演讲内容稿.md】 "
        "把这一份内容讲稿制作成一个 PPT，要尽可能地还原原文意思。"
        "不要受时长和页数的影响，尽量完整地体现讲稿原本的内容。"
    )
    contract = {
        "task_type": "teaching_deck",
        "source_use": "faithful",
        "coverage": "near_complete",
        "compression": "low",
        "depth": "deep",
        "page_budget_policy": "source_capacity",
        "structure_policy": "source_order",
        "confidence": 0.92,
        "evidence": ["不要受时长和页数的影响", "尽量完整地体现"],
    }

    monkeypatch.setattr(content_plan_module, "infer_content_director_contract", lambda **_kwargs: contract)
    target, min_pages, max_pages = resolve_content_plan_page_target(topic, None, documents, intent_contract=contract)
    job = content_plan_module._build_content_plan_job(topic=topic, documents=documents)

    assert 7_000 <= len(documents) < 12_000
    assert target >= 60
    assert min_pages >= 40
    assert max_pages >= target
    assert should_generate_incremental_long_deck(topic, None, documents, intent_contract=contract)
    assert job.page_count == target
    assert content_plan_module._select_content_plan_strategy(job) == "page_map"


def test_build_content_plan_job_uses_content_director_contract(monkeypatch):
    documents = _medium_length_course_manuscript()

    def fake_director(**kwargs):
        assert "尽量完整" in kwargs["brief"]
        return {
            "task_type": "teaching_deck",
            "source_use": "faithful",
            "coverage": "near_complete",
            "compression": "low",
            "depth": "deep",
            "page_budget_policy": "source_capacity",
            "structure_policy": "source_order",
            "confidence": 0.91,
            "evidence": ["尽量完整"],
        }

    monkeypatch.setattr(content_plan_module, "infer_content_director_contract", fake_director)

    job = content_plan_module._build_content_plan_job(
        topic="请把这份讲稿做成 PPT，尽量完整体现内容。",
        documents=documents,
    )

    assert job.intent_contract["page_budget_policy"] == "source_capacity"
    assert job.page_count >= 60
    assert content_plan_module._select_content_plan_strategy(job) == "page_map"


def test_source_preserve_synonyms_strengthen_director_contract(monkeypatch):
    documents = """# 企业增长课

## 第一章：增长不是拉新
正文

## 第二章：留存才是复利
正文
"""

    monkeypatch.setattr(content_plan_module, "infer_content_director_contract", lambda **_kwargs: {
        "task_type": "summary",
        "source_use": "synthesized",
        "coverage": "selective",
        "compression": "high",
        "depth": "brief",
        "page_budget_policy": "compact",
        "structure_policy": "reorganize",
        "confidence": 0.8,
        "evidence": ["模型误判成摘要"],
    })

    contract = content_plan_module.infer_effective_content_intent_contract(
        "请逐章讲清楚，不要遗漏全部章节，做成 20 页课程",
        documents,
    )

    assert contract["task_type"] == "teaching_deck"
    assert contract["source_use"] == "faithful"
    assert contract["coverage"] == "near_complete"
    assert contract["compression"] == "low"
    assert contract["depth"] == "deep"
    assert contract["page_budget_policy"] == "explicit"
    assert contract["structure_policy"] == "source_order"
    assert "保留源材料结构" in contract["delivery_intent"]
    assert _document_preservation_mode(documents, "请逐章讲清楚，不要遗漏全部章节") == "faithful"


def test_weak_legacy_contract_does_not_bypass_content_director(monkeypatch):
    documents = _medium_length_course_manuscript()
    calls = {"director": 0}

    def fake_director(**_kwargs):
        calls["director"] += 1
        return {
            "task_type": "summary",
            "source_use": "synthesized",
            "coverage": "selective",
            "compression": "high",
            "depth": "brief",
            "page_budget_policy": "compact",
            "structure_policy": "reorganize",
            "confidence": 0.88,
            "evidence": ["用户要求总结提炼"],
        }

    weak_legacy_contract = {
        "task_type": "polish",
        "rewrite_level": "light",
        "page_order_policy": "preserve",
        "page_count_policy": "same",
        "source_fidelity": "faithful",
        "visual_source_use": "page_reference",
        "confidence": 0.55,
        "evidence": [],
    }
    monkeypatch.setattr(content_plan_module, "infer_content_director_contract", fake_director)

    job = content_plan_module._build_content_plan_job(
        topic="请把这份材料总结成一份高管汇报 PPT",
        documents=documents,
        intent_contract=weak_legacy_contract,
    )

    assert calls["director"] == 1
    assert job.intent_contract["task_type"] == "summary"
    assert job.intent_contract["page_budget_policy"] == "compact"


def test_real_manuscript_prompt_routes_to_long_deck_through_content_director(monkeypatch):
    documents = _medium_length_course_manuscript()

    monkeypatch.setattr(content_plan_module, "infer_content_director_contract", lambda **_kwargs: {
        "task_type": "teaching_deck",
        "source_use": "faithful",
        "coverage": "near_complete",
        "compression": "low",
        "depth": "deep",
        "page_budget_policy": "source_capacity",
        "structure_policy": "source_order",
        "confidence": 0.92,
        "evidence": ["不要受时长和页数的影响", "尽量完整地体现"],
    })

    job = content_plan_module._build_content_plan_job(
        topic=(
            "【文件：赢利与天龙十部完整演讲内容稿.md】 "
            "把这一份内容讲稿制作成一个 PPT，要尽可能地还原原文意思。"
            "不要受时长和页数的影响，尽量完整地体现讲稿原本的内容。"
        ),
        documents=documents,
    )

    assert job.page_count >= 60
    assert job.min_pages >= 40
    assert job.intent_contract["page_budget_policy"] == "source_capacity"
    assert content_plan_module._select_content_plan_strategy(job) == "page_map"


def test_source_capacity_contract_expands_without_keyword_matching():
    documents = _medium_length_course_manuscript()
    contract = {
        "task_type": "teaching_deck",
        "source_use": "faithful",
        "coverage": "near_complete",
        "compression": "low",
        "depth": "deep",
        "page_budget_policy": "source_capacity",
        "structure_policy": "source_order",
        "confidence": 0.9,
        "evidence": ["contract supplied by content director"],
    }

    target, min_pages, max_pages = resolve_content_plan_page_target(
        "请做成一份 PPT",
        None,
        documents,
        intent_contract=contract,
    )

    assert target >= 60
    assert min_pages >= 40
    assert max_pages == target
    assert should_generate_incremental_long_deck("请做成一份 PPT", None, documents, intent_contract=contract)


def test_source_capacity_uses_structural_capacity_below_char_threshold():
    documents = "# 结构清楚的短讲稿\n\n" + "\n\n".join(
        f"## 第 {idx} 章：关键问题 {idx}\n\n"
        f"核心判断：AI 时代的业务变化不是工具替换，而是决策链条被重新分配。\n"
        f"关键证据：第 {idx} 组案例说明，用户会先比较、再验证、最后才购买。\n"
        f"讲述要求：保留这一章的判断、证据和行动提醒，形成单独页面展开。"
        for idx in range(1, 13)
    )
    contract = {
        "task_type": "teaching_deck",
        "source_use": "faithful",
        "coverage": "near_complete",
        "compression": "low",
        "depth": "deep",
        "page_budget_policy": "source_capacity",
        "structure_policy": "source_order",
        "confidence": 0.9,
        "evidence": ["按源材料容量展开"],
    }

    assert len(documents) < content_plan_module.AUTO_DOCUMENT_PAGE_MIN_CHARS

    target, min_pages, max_pages = resolve_content_plan_page_target(
        "请按材料结构做成一份 PPT",
        None,
        documents,
        intent_contract=contract,
    )

    assert target >= 18
    assert target < content_plan_module.LONG_DECK_INCREMENTAL_THRESHOLD
    assert min_pages >= 1
    assert max_pages == target


def test_upper_bound_only_page_request_is_cap_not_target():
    target, min_pages, max_pages = resolve_content_plan_page_target(
        "请做一份不超过 20 页的高管摘要 PPT",
        None,
        "",
    )

    assert min_pages == 1
    assert max_pages == 20
    assert target < 20


def test_latest_per_page_depth_feedback_no_longer_translates_contract():
    """用户说'加深'不应再被翻译成 contract 字段；原话应进 topic（标记"必须采纳"）。"""
    documents = _medium_length_course_manuscript()
    legacy_contract = {
        "task_type": "polish",
        "rewrite_level": "light",
        "page_order_policy": "preserve",
        "page_count_policy": "same",
        "source_fidelity": "faithful",
        "visual_source_use": "page_reference",
        "confidence": 0.82,
        "evidence": ["已上传讲稿"],
    }
    chat_context_text = (
        "原来演讲的体量每一页的 ppt 内容要更有深度一些。"
        "每一页的内容可以再增加一点。"
    )

    job = content_plan_module._build_content_plan_job(
        topic="张帆 AI 与企业战略融合",
        documents=documents,
        intent_contract=legacy_contract,
        chat_context=chat_context_text,
    )

    # contract.depth 不再被翻译成 "deep"（保持原 contract 状态）
    assert job.intent_contract.get("depth") != "deep"
    # 用户原话进 topic，且被显式标记为"必须采纳"
    assert "深度" in job.topic
    assert "必须采纳" in job.topic
    assert chat_context_text in job.topic


def test_agent_chat_context_restores_page_count_when_topic_is_summarized():
    topic = "《增长黑客》读书分享 PPT"
    chat_context = """用户：我要做一个完整的《增长黑客》这本书的 PPT。

你先去搜一下这本书的全文内容，然后再把它做成一个 PPT，要求如下：
1. 篇幅大概三四十页
2. 内容要详实，要让人家看完这个 PPT 就能大概知道《增长黑客》这本书讲了什么"""

    combined_topic = content_plan_module.content_plan_topic_with_chat_context(topic, chat_context)

    assert resolve_content_plan_page_target(combined_topic, 3) == (40, 30, 40)


def test_explicit_page_count_survives_latest_depth_feedback_same_source_contract():
    documents = _medium_length_course_manuscript()
    legacy_contract = {
        "task_type": "polish",
        "rewrite_level": "light",
        "page_order_policy": "preserve",
        "page_count_policy": "same",
        "source_fidelity": "faithful",
        "visual_source_use": "page_reference",
        "confidence": 0.82,
        "evidence": ["已上传讲稿"],
    }

    job = content_plan_module._build_content_plan_job(
        topic="张帆 AI 与企业战略融合",
        documents=documents,
        page_count=60,
        intent_contract=legacy_contract,
        chat_context=(
            "原来演讲的体量每一页的 ppt 内容要更有深度一些。"
            "每一页的内容可以再增加一点。"
        ),
    )

    assert job.page_count == 60
    assert job.min_pages > 1
    assert content_plan_module._select_content_plan_strategy(job) == "page_map"


def test_abstract_quality_constraints_injected_into_page_map_prompt(monkeypatch):
    """abstract constraints 应注入到 page_map prompt；旧硬编码 bullet 模板不再出现。"""
    captured = {}
    contract = {
        "task_type": "teaching_deck",
        "source_use": "faithful",
        "coverage": "near_complete",
        "compression": "low",
        "depth": "deep",
        "page_budget_policy": "source_capacity",
        "structure_policy": "source_order",
        "confidence": 0.9,
        "evidence": ["每页内容再增加一点"],
    }

    class FakeMessage:
        def __init__(self, content):
            self.content = content

    class FakeChoice:
        def __init__(self, content):
            self.message = FakeMessage(content)

    class FakeResponse:
        def __init__(self, content):
            self.choices = [FakeChoice(content)]

    class FakeCompletions:
        def create(self, **kwargs):
            prompt = kwargs["messages"][1]["content"]
            captured["prompt"] = prompt
            captured["extra_body"] = kwargs.get("extra_body")
            return FakeResponse(
                "P1｜cover｜封面｜张帆 AI 与企业战略融合\n"
                "备注：开场。\n"
                "视觉：封面\n\n"
                "P2｜content｜内容｜为什么这场课不一样\n"
                "- 企业家正在既兴奋又焦虑的状态中学习 AI\n"
                "- 工具层出不穷但少有沉淀，需要回到第一性\n"
                "- 本页要把从工具焦虑到战略重构的转场讲清楚\n"
                "- 听众需要意识到 AI 不是工具清单，而是业务系统变量\n"
                "备注：先承接现场焦虑，再把讨论拉回不变的底层逻辑。\n"
                "视觉：内容页"
            )

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(content_plan_module, "get_llm_client", lambda: FakeClient())

    content_plan_module._generate_model_page_map(
        topic="张帆 AI 与企业战略融合",
        audience="企业家",
        documents="## 为什么这场课不一样\n企业家既兴奋又焦虑。",
        page_goal_text="生成 2 页",
        target_count=2,
        min_pages=2,
        max_pages=2,
        intent_contract=contract,
    )

    # 新输出视角信号
    assert "产出目标" in captured["prompt"]
    assert "PPT 应当准确体现用户的真实意图" in captured["prompt"]
    assert "好 PPT 的特征" in captured["prompt"]
    assert "避免的输出形态" in captured["prompt"]
    assert "label: content" in captured["prompt"]
    assert captured["extra_body"]["thinking"]["type"] == "adaptive"
    assert captured["extra_body"]["reasoning_split"] is True
    # 旧硬编码 bullet 模板不再出现
    assert "每页 4-6 个具体 bullet" not in captured["prompt"]
    assert "判断、依据、案例" not in captured["prompt"]
    assert "2-3 个具体 bullet" not in captured["prompt"]


def test_compact_summary_contract_stays_compact_for_same_document():
    documents = _medium_length_course_manuscript()
    contract = {
        "task_type": "summary",
        "source_use": "optimized",
        "coverage": "selective",
        "compression": "high",
        "depth": "brief",
        "page_budget_policy": "compact",
        "structure_policy": "reorganize",
        "confidence": 0.9,
        "evidence": ["用户要求总结提炼"],
    }

    target, _min_pages, max_pages = resolve_content_plan_page_target(
        "请做成一份 PPT",
        None,
        documents,
        intent_contract=contract,
    )

    assert target < 40
    assert max_pages < 40
    assert not should_generate_incremental_long_deck("请做成一份 PPT", None, documents, intent_contract=contract)


def test_intent_contract_policy_text_returns_abstract_quality_constraints():
    """director_policy 应返回 abstract constraints，不再读 contract 字段做翻译。"""
    text = content_plan_module._intent_contract_policy_text({
        "task_type": "teaching_deck",
        "source_use": "faithful",
        "coverage": "near_complete",
        "compression": "low",
        "depth": "deep",
        "page_budget_policy": "source_capacity",
        "structure_policy": "source_order",
        "confidence": 0.9,
        "evidence": ["尽量完整"],
    })

    # 新输出视角信号
    assert "产出目标" in text
    assert "PPT 应当准确体现用户的真实意图" in text
    assert "好 PPT 的特征" in text
    assert "避免的输出形态" in text
    assert "label: content" in text
    assert "标题必须承载具体判断、问题或原文概念" in text
    assert "金句 / 课程动作" not in text
    assert "转场、互动" not in text
    # 旧的 contract 字段翻译不再出现
    assert "尽量完整覆盖上传材料" not in text
    assert "不要压缩成摘要" not in text
    assert "保留原文结构和讲述顺序" not in text


def test_intent_contract_policy_text_uses_open_delivery_intent_not_type_branches():
    text = content_plan_module._intent_contract_policy_text({
        "task_type": "teaching_deck",
        "source_use": "faithful",
        "coverage": "near_complete",
        "compression": "low",
        "depth": "deep",
        "page_budget_policy": "source_capacity",
        "structure_policy": "source_order",
        "delivery_intent": "面向一小时课程演讲，保留原文结构和关键表达。",
        "confidence": 0.9,
        "evidence": ["课程"],
    })
    ignored_legacy_type_text = content_plan_module._intent_contract_policy_text({
        "task_type": "summary",
        "source_use": "optimized",
        "coverage": "selective",
        "compression": "high",
        "depth": "brief",
        "page_budget_policy": "compact",
        "structure_policy": "reorganize",
        "legacy_output_type": "fixed_report_type",
        "delivery_intent": "面向管理层快速判断的决策简报。",
        "confidence": 0.9,
        "evidence": ["高管汇报"],
    })

    assert "交付理解" in text
    assert "面向一小时课程演讲，保留原文结构和关键表达。" in text
    assert "面向管理层快速判断的决策简报。" in ignored_legacy_type_text
    assert "交付类型" not in text
    assert "legacy_output_type" not in ignored_legacy_type_text


def test_restoration_priority_does_not_override_explicit_short_page_count():
    documents = _medium_length_course_manuscript()

    assert resolve_content_plan_page_target(
        "做成 10 页 PPT，尽量还原原文意思。",
        10,
        documents,
    ) == (10, 9, 11)
    assert not should_generate_incremental_long_deck("做成 10 页 PPT，尽量还原原文意思。", 10, documents)


def test_regular_summary_prompt_for_medium_document_stays_compact():
    documents = _medium_length_course_manuscript()
    topic = "帮我把这份讲稿总结提炼成一份 PPT。"

    target, _min_pages, max_pages = resolve_content_plan_page_target(topic, None, documents)

    assert target < 40
    assert max_pages < 40
    assert not should_generate_incremental_long_deck(topic, None, documents)


def test_requested_range_trim_preserves_closing_page():
    outline = [
        {
            "page_num": idx,
            "type": "cover" if idx == 1 else "ending" if idx == 8 else "content",
            "text_content": {"headline": f"第 {idx} 页", "body": "- 内容"},
        }
        for idx in range(1, 9)
    ]
    outline[-1]["text_content"]["headline"] = "结束与行动"

    trimmed = _enforce_requested_page_range(outline, (4, 5))

    assert len(trimmed) == 7
    assert trimmed[0]["type"] == "cover"
    assert trimmed[-1]["type"] == "ending"
    assert trimmed[-1]["page_num"] == 7
    assert trimmed[-1]["text_content"]["headline"] == "结束与行动"


def test_long_deck_skeleton_creates_all_pages_before_llm():
    skeleton = build_long_deck_skeleton(
        topic="我要制作一份在大连演讲的 PPT，听众是中小企业老板",
        target_count=80,
        min_pages=60,
        max_pages=80,
    )

    assert len(skeleton) == 80
    assert [page["page_num"] for page in skeleton] == list(range(1, 81))
    assert skeleton[0]["type"] == "cover"
    assert skeleton[-1]["type"] == "ending"
    assert all(page["generation_status"] == "skeleton" for page in skeleton)


def test_document_driven_long_deck_draft_uses_uploaded_material():
    documents = """# 面向AI时代，企业营与销该如何布局

## 模块一：道（为什么必须变）

### 第一部分：时代已经变了

- ChatGPT 达到 1 亿规模只用了 2 个月
- 中国 AI 原生 App 月活已达 4.4 亿

### 第二部分：变的是什么

- 消费者问 AI，AI 替他做选择
- 不被 AI 推荐约等于不存在

## 模块三：术（企业怎么布局）

### 零售

- 工厂实拍、产地溯源、试吃视频
- 不要用 AI 生成用户评价
"""

    draft = build_document_driven_long_deck_draft(
        topic="做成 60 到 80 页课程",
        documents=documents,
        target_count=10,
        min_pages=10,
        max_pages=10,
    )

    bodies = "\n".join(str(page.get("text_content", {}).get("body") or "") for page in draft)
    headlines = "\n".join(str(page.get("text_content", {}).get("headline") or "") for page in draft)
    assert len(draft) == 10
    assert all(page["generation_status"] == "source_draft" for page in draft)
    assert "ChatGPT 达到 1 亿规模只用了 2 个月" in bodies
    assert "不被 AI 推荐约等于不存在" in bodies
    assert "零售" in headlines or "零售" in bodies
    assert "系统会继续根据 Brief" not in bodies

    notes = "\n".join(str(page.get("speaker_notes") or "") for page in draft)
    assert "这一页口头展开" not in notes
    assert "讲稿内容：" in notes
    assert "转场提示" in notes


def test_plain_pdf_text_infers_source_headings_without_upload_marker():
    documents = """--- 文档: 课程脉络解析.pdf ---
🧠
课程脉络解析｜一体性宇宙观
本文是对课程录音的完整脉络梳理。
一、总纲：这堂课在干什么？
核心命题：认识论时代已走到边界，必须回到本体论。
讲者的论证路径：
graph TD
    A["两个终极问题"] --> B["必须下移基点：认
识论 → 本体论"]
    B --> C["三阶文明模型：能感→能思→能觉"]
二、第一层：哲学地基——为什么要回到本体
论？
认识论的边界需要被重新打开。
"""

    draft = build_document_driven_long_deck_draft(
        topic="把 PDF 提炼成 8 页 PPT",
        documents=documents,
        target_count=8,
        min_pages=8,
        max_pages=8,
    )

    assert "用户上传材料" not in str(draft)

    rendered = "\n".join(
        "\n".join(str(value or "") for value in page.get("text_content", {}).values())
        for page in draft
    )

    assert "总纲：这堂课在干什么" in rendered
    assert "第一层：哲学地基" in rendered
    assert "流程图" in rendered
    assert "两个终极问题" in rendered
    assert "必须下移基点：认识论" in rendered
    assert "第一层：哲学地基——为什么要回到本体论？" in rendered
    assert "认 识论" not in rendered
    assert "graph TD" not in rendered
    assert "-->" not in rendered


def test_mermaid_blocks_are_summarized_across_common_diagram_types():
    documents = """# 技术方案

## 时序

sequenceDiagram
    participant U as 用户
    U->>AI: 上传 PDF
    AI-->>U: 返回内容规划

## 类图

classDiagram
    Strategy <|-- MermaidAwareParser
    MermaidAwareParser --> SourceDraft : produces clean bullets
"""

    units = content_plan_module.extract_document_outline_units(documents)
    rendered = "\n".join(
        "\n".join(str(line) for line in unit.get("plain_lines") or [])
        for unit in units
    )

    assert "时序图" in rendered
    assert "用户 → AI：上传 PDF" in rendered
    assert "AI → 用户：返回内容规划" in rendered
    assert "类图" in rendered
    assert "Strategy → MermaidAwareParser" in rendered
    assert "MermaidAwareParser → SourceDraft：produces clean bullets" in rendered
    assert "sequenceDiagram" not in rendered
    assert "classDiagram" not in rendered
    assert "->>" not in rendered
    assert "<|--" not in rendered


def test_page_map_markdown_parses_and_structures_to_content_plan():
    markdown = """P1｜cover｜封面｜面向AI时代，企业营与销该如何布局
备注：开场定调，说明这不是工具课。
视觉：课程主视觉

P2｜content｜道｜为什么必须变
- ChatGPT 达到 1 亿规模只用了 2 个月
- 消费者问 AI，AI 替他做选择
备注：把速度和行为迁移连起来。
视觉：速度对比图
来源：模块一"""

    page_map = parse_page_map_markdown(markdown)
    outline = content_plan_from_page_map(page_map)

    assert len(outline) == 2
    assert outline[0]["type"] == "cover"
    assert outline[0]["text_content"]["body"] == ""
    assert outline[1]["type"] == "ending"
    assert "ChatGPT 达到 1 亿规模只用了 2 个月" in outline[1]["text_content"]["body"]
    assert "把速度和行为迁移连起来" in outline[1]["speaker_notes"]


def test_page_map_parser_routes_bulleted_metadata_out_of_body():
    markdown = """P1｜cover｜封面｜AI时代的文明跃迁
- 副标题：一体性宇宙观·意识能量·理念组织
- 备注：开场不需要自我介绍，直接切入问题。
- 视觉：深色背景，中央大字标题。
- 来源：课程脉络解析 > 总纲

P2｜agenda｜目录｜课程全景图：四个维度一次穿透
- 四个模块：哲学地基 → 物理地基 → 意识起源 → 文明实践
- 备注：先用这张图让听众看到全貌。
- 视觉：四象限或垂直金字塔结构。
- 来源：课程脉络解析 > 总纲；两日课程整体脉络

P3｜section｜哲学地基｜第一层：为什么必须回到本体论？
- 章节标题：哲学地基——本体是什么？
- 备注：接下来进入第一层哲学论证。
- 视觉：章节分隔页，深色背景大字标题。
- 来源：课程脉络解析 > 第一层"""

    outline = content_plan_from_page_map(parse_page_map_markdown(markdown))

    cover = outline[0]
    agenda = outline[1]
    section = outline[2]
    assert cover["text_content"]["subhead"] == "一体性宇宙观·意识能量·理念组织"
    assert "开场不需要自我介绍" in cover["speaker_notes"]
    assert "深色背景" in cover["visual_suggestion"]
    assert cover["source_refs"] == ["课程脉络解析 > 总纲"]

    agenda_body = agenda["text_content"]["body"]
    assert "四个模块：哲学地基" in agenda_body
    assert "备注：" not in agenda_body
    assert "视觉：" not in agenda_body
    assert "来源：" not in agenda_body
    assert "先用这张图" in agenda["speaker_notes"]
    assert "四象限" in agenda["visual_suggestion"]
    assert agenda["source_refs"] == ["课程脉络解析 > 总纲", "两日课程整体脉络"]

    section_body = section["text_content"]["body"]
    assert "章节标题：" not in section_body
    assert "哲学地基——本体是什么" not in section_body
    assert section["text_content"]["headline"] == "第一层：为什么必须回到本体论？"
    assert section["section_title"] == "哲学地基"


def test_page_map_parser_carries_figure_refs_into_content_plan():
    markdown = """P1｜cover｜封面｜向硅谷学创新第一章
备注：开场。

P2｜content｜使命重构｜医院愿景如何落地
- 建立世界级患者服务流程
- 让患者满意、员工自豪
备注：讲医院案例。
视觉：使用原书图辅助说明
配图：创新.pdf 第47页 fig-p47 医院愿景图
来源：创新.pdf 第47页"""

    page_map = parse_page_map_markdown(markdown)
    outline = content_plan_from_page_map(page_map)

    assert page_map[1]["figure_refs"] == ["创新.pdf 第47页 fig-p47 医院愿景图"]
    assert outline[1]["figure_refs"][0]["source_document"] == "创新.pdf"
    assert outline[1]["figure_refs"][0]["source_page_num"] == 47
    assert outline[1]["figure_refs"][0]["figure_id"] == "fig-p47"
    assert outline[1]["source_refs"][0]["source_page_num"] == 47


def test_page_map_parser_preserves_source_pack_figure_id_with_filename_prefix():
    markdown = """P1｜cover｜封面｜向硅谷学创新第一章

P2｜content｜使命重构｜医院愿景如何落地
- 建立世界级患者服务流程
- 让患者满意、员工自豪
配图：《向硅谷学创新》刘立20250429.pdf 第47页 《向硅谷学创新》刘立20250429.pdf:p47:x421:1 医院愿景图"""

    outline = content_plan_from_page_map(parse_page_map_markdown(markdown))

    assert outline[1]["figure_refs"][0]["figure_id"] == "《向硅谷学创新》刘立20250429.pdf:p47:x421:1"


def test_page_map_parser_rejects_placeholder_figure_ids():
    markdown = """P1｜cover｜封面｜向硅谷学创新第一章

P2｜content｜使命重构｜医院愿景如何落地
- 建立世界级患者服务流程
- 用 SEE 框架对齐个人使命
配图：创新.pdf 第47页 figureid 使用理由：原图展示 SEE 框架
来源：创新.pdf 第47页"""

    outline = content_plan_from_page_map(parse_page_map_markdown(markdown))

    assert outline[1]["figure_refs"] == []
    assert outline[1]["source_refs"][0]["source_page_num"] == 47


def test_page_map_figure_gate_rejects_unrelated_real_pdf_figure_id():
    source_context = """--- SOURCE filename="book.pdf" kind="pdf" ---
--- AVAILABLE_FIGURES ---
FIGURE figure_id="amazon-fig" source_document="book.pdf" source_type="pdf" source_page_num="31" nearby_text="贝索斯面对外界批评保持开放，亚马逊通过永续创新革新零售和云计算行业"
FIGURE figure_id="see-fig" source_document="book.pdf" source_type="pdf" source_page_num="47" nearby_text="医院愿景与员工个人抱负保持一致，使用 SEE 框架：优势、唤醒、振奋，开发个人愿景宣言"
"""
    markdown = """P1｜cover｜封面｜向硅谷学创新第一章

P2｜content｜使命重构｜圣克拉拉谷医疗中心案例
- 医院建立世界级患者服务流程
- 80 到 100 名医生、护士和理疗师参与个人愿景练习
- 参与者运用 SEE 框架对齐优势、唤醒与振奋
配图：book.pdf 第31页 figure_id="amazon-fig" 使用理由：展示亚马逊被批评的报刊图
来源：book.pdf 第47页"""

    outline = content_plan_from_page_map(
        parse_page_map_markdown(markdown),
        source_context=source_context,
    )

    assert outline[1]["figure_refs"] == []
    assert outline[1]["source_refs"][0]["source_page_num"] == 47


def test_page_map_figure_gate_keeps_relevant_cross_page_pdf_figure_id():
    source_context = """--- SOURCE filename="book.pdf" kind="pdf" ---
--- AVAILABLE_FIGURES ---
FIGURE figure_id="amazon-fig" source_document="book.pdf" source_type="pdf" source_page_num="31" nearby_text="贝索斯面对外界批评保持开放，亚马逊通过永续创新革新零售和云计算行业"
"""
    markdown = """P1｜cover｜封面｜向硅谷学创新第一章

P2｜content｜案例｜打破零售局限
- 亚马逊以客户为中心，围绕便利、选择和价格持续创新
- 贝索斯长期坚持愿景，在批评中保持开放和长期主义
- 亚马逊革新零售，也拓展到云计算等行业
配图：book.pdf 第31页 figure_id="amazon-fig" 使用理由：展示外界对亚马逊和贝索斯的质疑
来源：book.pdf 第45页"""

    outline = content_plan_from_page_map(
        parse_page_map_markdown(markdown),
        source_context=source_context,
    )

    assert outline[1]["figure_refs"][0]["figure_id"] == "amazon-fig"
    assert outline[1]["figure_refs"][0]["source_page_num"] == 31


def test_page_map_figure_gate_keeps_same_page_pdf_figure_id():
    source_context = """--- SOURCE filename="book.pdf" kind="pdf" ---
--- AVAILABLE_FIGURES ---
FIGURE figure_id="see-fig" source_document="book.pdf" source_type="pdf" source_page_num="47" nearby_text="医院愿景与员工个人抱负保持一致，使用 SEE 框架：优势、唤醒、振奋，开发个人愿景宣言"
"""
    markdown = """P1｜cover｜封面｜向硅谷学创新第一章

P2｜content｜使命重构｜圣克拉拉谷医疗中心案例
- 医院建立世界级患者服务流程
- 参与者运用 SEE 框架对齐优势、唤醒与振奋
配图：book.pdf 第47页 figure_id="see-fig" 使用理由：解释 SEE 框架
来源：book.pdf 第47页"""

    outline = content_plan_from_page_map(
        parse_page_map_markdown(markdown),
        source_context=source_context,
    )

    assert outline[1]["figure_refs"][0]["figure_id"] == "see-fig"
    assert outline[1]["figure_refs"][0]["source_page_num"] == 47


def test_page_map_compiler_removes_source_context_markers_from_visible_fields():
    markdown = """P1｜cover｜封面｜向硅谷学创新第一章

P2｜content｜--- SOURCE filename="book.pdf" kind="pdf" ---｜--- SOURCE filename="book.pdf" kind="pdf" ---（续 37）
副标题：--- SOURCE filename="book.pdf" kind="pdf" ---
- --- PAGE 39 chapter="第1章" ---
- 真正内容：纳德拉通过互利合作重建微软文化
来源：--- SOURCE filename="book.pdf" kind="pdf" ---"""

    outline = content_plan_from_page_map(parse_page_map_markdown(markdown))
    page = outline[1]

    assert "SOURCE filename" not in page["section_title"]
    assert "SOURCE filename" not in page["text_content"]["headline"]
    assert page["text_content"]["headline"] == "第 2 页"
    assert page["text_content"]["subhead"] == ""
    assert "PAGE 39" not in page["text_content"]["body"]
    assert "真正内容" in page["text_content"]["body"]
    assert page["source_refs"] == []
    assert_no_internal_source_markers(outline)


@pytest.mark.parametrize(
    "marker",
    [
        '--- SOURCE filename="book.pdf" kind="pdf" ---',
        '--- PAGE 39 chapter="第一章" ---',
        '--- CHAPTER id="chapter_1" title="第一章" pages="33-51" ---',
        "--- AVAILABLE_FIGURES ---",
        'FIGURE figure_id="fig-1" source_document="book.pdf" source_type="pdf" source_page_num="39" nearby_text="图示说明"',
    ],
)
def test_page_map_compiler_removes_all_source_context_marker_variants(marker):
    markdown = f"""P1｜cover｜封面｜向硅谷学创新第一章

P2｜content｜{marker}｜{marker}
副标题：{marker}
- {marker}
- 真正内容：用企业愿景约束组织行动
备注：{marker}
视觉：{marker}
来源：{marker}"""

    outline = content_plan_from_page_map(parse_page_map_markdown(markdown))

    assert "真正内容" in outline[1]["text_content"]["body"]
    assert_no_internal_source_markers(outline)


def test_generate_content_page_map_cleans_model_copied_source_context_markers(monkeypatch):
    class FakeMessage:
        content = """P1｜cover｜封面｜向硅谷学创新第一章
备注：封面。

P2｜content｜--- SOURCE filename="book.pdf" kind="pdf" ---｜圣克拉拉谷医疗中心案例
副标题：--- PAGE 47 chapter="第一章" ---
- 医院建立世界级患者服务流程
- --- AVAILABLE_FIGURES ---
- 参与者使用 SEE 框架对齐个人愿景
备注：FIGURE figure_id="fig-1" source_document="book.pdf" source_type="pdf" source_page_num="47"
视觉：--- CHAPTER id="chapter_1" title="第一章" pages="33-51" ---
来源：--- SOURCE filename="book.pdf" kind="pdf" ---

P3｜ending｜总结｜下一步
- 回到存在主义使命
备注：收束。"""

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeCompletions:
        def create(self, **kwargs):
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(content_plan_module, "get_llm_client", lambda: FakeClient())

    page_map = generate_content_page_map(
        topic="做一份 3 页向硅谷学创新课程",
        documents='--- SOURCE filename="book.pdf" kind="pdf" ---\n--- PAGE 47 ---\n医院愿景和 SEE 框架。',
        page_count=3,
    )
    outline = content_plan_from_page_map(page_map)

    assert outline[1]["text_content"]["headline"] == "圣克拉拉谷医疗中心案例"
    assert "SEE 框架" in outline[1]["text_content"]["body"]
    assert_no_internal_source_markers(page_map)
    assert_no_internal_source_markers(outline)


def test_source_outline_units_ignore_source_context_control_lines():
    documents = """--- SOURCE filename="book.pdf" kind="pdf" ---
--- PAGE 38 chapter="第一章" ---
纳德拉通过互利合作重建微软文化。
--- AVAILABLE_FIGURES ---
FIGURE figure_id="fig-1" source_document="book.pdf" source_type="pdf" source_page_num="38" nearby_text="配图说明"
--- PAGE 39 chapter="第一章" ---
认同企业愿景的管理者加入领导团队。"""

    units = content_plan_module.extract_document_outline_units(documents)
    joined = "\n".join(
        [str(unit.get("title") or "") for unit in units]
        + [line for unit in units for line in (unit.get("plain_lines") or [])]
    )

    assert "SOURCE filename" not in joined
    assert "PAGE 38" not in joined
    assert "AVAILABLE_FIGURES" not in joined
    assert "figure_id" not in joined
    assert "纳德拉通过互利合作重建微软文化" in joined
    assert_no_internal_source_markers(units)


def test_source_draft_from_source_context_does_not_expose_internal_markers():
    documents = """--- SOURCE filename="book.pdf" kind="pdf" ---
--- PAGE 38 chapter="第一章" ---
纳德拉通过互利合作重建微软文化。
--- AVAILABLE_FIGURES ---
FIGURE figure_id="fig-1" source_document="book.pdf" source_type="pdf" source_page_num="38" nearby_text="配图说明"
--- PAGE 39 chapter="第一章" ---
认同企业愿景的管理者加入领导团队。"""

    draft = build_document_driven_long_deck_draft(
        topic="把第一章做成 6 页讲课 PPT",
        documents=documents,
        target_count=6,
        min_pages=5,
        max_pages=7,
    )

    assert_no_internal_source_markers(draft)


def test_source_context_draft_uses_real_agenda_ending_and_pdf_figures():
    documents = """--- SOURCE filename="book.pdf" kind="pdf" ---
--- PAGE 17 ---
绪论
看清挑战
大企业为什么会失去创新能力
--- PAGE 20 ---
永续创新的真谛
情感在变革过程中替代理性口号，使命、心智模式和文化共同支撑永续创新。
--- PAGE 31 ---
阅读指南
保持开放和乐观的心态，将从后续章节中获益良多。
--- PAGE 33 chapter="第1章 确立一个存在主义使命：你的公司为什么重要？" ---
第1章 确立一个存在主义使命：你的公司为什么重要？
微软在 2014 年被卡住，需要重新发现企业存在的原因。
--- PAGE 47 chapter="第1章 确立一个存在主义使命：你的公司为什么重要？" ---
让组织愿景与个人北极星相一致
圣克拉拉谷医疗中心让员工参与愿景制定，并用 SEE 框架对齐个人使命。
--- PAGE 51 chapter="第1章 确立一个存在主义使命：你的公司为什么重要？" ---
结语
存在主义是真正实现永续创新企业的核心动力，既是变革的源泉，也是变革的方向。
--- AVAILABLE_FIGURES ---
FIGURE figure_id="book.pdf:p20:x1:1" source_document="book.pdf" source_type="pdf" source_page_num="20" nearby_text="绪论里的永续创新示意图"
FIGURE figure_id="book.pdf:p47:x1:1" source_document="book.pdf" source_type="pdf" source_page_num="47" nearby_text="医院愿景与员工个人抱负保持一致，使用 SEE 框架"
"""

    draft = build_document_driven_long_deck_draft(
        topic="把绪论和第一章做成 8 页 PPT，参考图片尽可能放进去",
        documents=documents,
        target_count=8,
        min_pages=8,
        max_pages=8,
    )

    toc_body = draft[1]["text_content"]["body"]
    assert draft[1]["text_content"]["headline"] == "内容地图"
    assert "绪论" in toc_body
    assert "第1章" in toc_body
    assert "续 2" not in toc_body

    ending_body = draft[-1]["text_content"]["body"]
    assert "回到整份内容主线" not in ending_body
    assert "存在主义" in ending_body

    figure_ids = {
        ref["figure_id"]
        for page in draft
        for ref in (page.get("figure_refs") or [])
    }
    assert {"book.pdf:p20:x1:1", "book.pdf:p47:x1:1"} <= figure_ids

    page_map = content_plan_module._outline_to_page_map(draft)
    outline = content_plan_from_page_map(page_map, source_context=documents)
    outline_figure_ids = {
        ref["figure_id"]
        for page in outline
        for ref in (page.get("figure_refs") or [])
    }
    assert {"book.pdf:p20:x1:1", "book.pdf:p47:x1:1"} <= outline_figure_ids


def test_source_draft_condenses_full_source_instead_of_truncating_tail():
    documents = """# AI 时代品牌课

## 第一章：问题从哪里来
- 第一章原文句子 1
- 第一章原文句子 2
- 第一章原文句子 3
- 第一章原文句子 4
- 第一章原文句子 5
- 第一章原文句子 6

## 第二章：消费者如何变化
- 第二章原文句子 1
- 第二章原文句子 2
- 第二章原文句子 3
- 第二章原文句子 4
- 第二章原文句子 5
- 第二章原文句子 6

## 第三章：平台如何变化
- 第三章原文句子 1
- 第三章原文句子 2
- 第三章原文句子 3
- 第三章原文句子 4
- 第三章原文句子 5
- 第三章原文句子 6

## 第四章：品牌如何分化
- 第四章原文句子 1
- 第四章原文句子 2
- 第四章原文句子 3
- 第四章原文句子 4
- 第四章原文句子 5
- 第四章原文句子 6

## 第五章：品牌怎么做
- 第五章原文句子 1
- 第五章原文句子 2
- 第五章原文句子 3
- 第五章原文句子 4
- 第五章原文句子 5
- 第五章原文句子 6

## 第六章：九十天行动
- 第六章原文句子 1
- 第六章原文句子 2
- 第六章原文句子 3
- 第六章原文句子 4
- 第六章原文句子 5
- 第六章原文句子 6

【想法】这里是制作备注，不应该进入内容规划。
"""

    draft = build_document_driven_long_deck_draft(
        topic="严格 8 页，保持原文顺序",
        documents=documents,
        target_count=8,
        min_pages=8,
        max_pages=8,
    )
    text = json.dumps(draft, ensure_ascii=False)

    assert len(draft) == 8
    assert "第一章原文句子 1" in text
    assert "第六章原文句子 6" in text
    assert "制作备注" not in text
    assert "互动与复盘" not in text
    assert "围绕本页材料设计一个提问" not in text


def test_source_draft_marks_numbered_chapter_entries_as_section_pages():
    documents = """# AI 时代品牌课

## 第一章：问题从哪里来
- 第一章原文句子 1
- 第一章原文句子 2

## 第二章：消费者如何变化
- 第二章原文句子 1
- 第二章原文句子 2

## 第三章：平台如何变化
- 第三章原文句子 1
- 第三章原文句子 2

## 第四章：品牌如何分化
- 第四章原文句子 1
- 第四章原文句子 2

## 第五章：品牌怎么做
- 第五章原文句子 1
- 第五章原文句子 2

## 第六章：企业 90 天行动清单
- 查：检查 AI、平台、达人和消费者如何描述你的品牌。
- 定：确定最希望被记住、被调用、被推荐的核心价值。

## 复盘与下一步
- 今天开始检查证据货架。
- 下周完成第一轮修订。
"""

    draft = build_document_driven_long_deck_draft(
        topic="做成 9 页 PPT，保持原文顺序",
        documents=documents,
        target_count=9,
        min_pages=9,
        max_pages=9,
    )

    chapter_page = next(
        page for page in draft if page["text_content"]["headline"] == "第六章：企业 90 天行动清单"
    )
    assert chapter_page["type"] == "section"


def test_markdown_source_context_uses_outline_units_instead_of_repeating_page_one():
    documents = """--- SOURCE filename="AI时代品牌营销课题研究-讨论记录.md" kind="markdown" pages="1" ---
--- PAGE 1 ---
# 《AI时代消费者决策路径与品牌策略》

## 混沌 AI 院，为什么要重新讲品牌营销？
- 混沌 AI 院，为什么要讲一门品牌营销课？
- 不是因为我们还缺一门教大家用 AI 写文案、做海报、剪视频、提效率的课。
- AI 时代真正值得追问的是品牌和商家真正的底牌是什么。

## 两张图看清这个时代
- 经典营销时代争夺注意力。
- 移动互联网时代争夺匹配效率。
- AI 时代争夺推荐资格。

## 企业 90 天行动
- 查：AI 和平台现在如何描述你。
- 定：找到品牌那个 1。
- 建：搭建结构化证据资产。
"""

    draft = build_document_driven_long_deck_draft(
        topic="做成 8 页 PPT",
        documents=documents,
        target_count=8,
        min_pages=7,
        max_pages=9,
    )
    text = json.dumps(draft, ensure_ascii=False)
    headlines = [
        str((page.get("text_content") or {}).get("headline") or "")
        for page in draft
        if page.get("type") in {"content", "data"}
    ]

    assert len(draft) == 8
    assert "两张图看清这个时代" in text
    assert "企业 90 天行动" in text
    assert headlines.count("混沌 AI 院，为什么要讲一门品牌营销课？") <= 1


def test_source_draft_separates_screen_copy_from_speaker_notes():
    long_sentence = (
        "AI 介入后最关键的变化不是信息更多速度更快，而是消费者开始把一部分信息处理权让渡出去，"
        "过去消费者自己看自己比自己判断，现在 AI 先读比筛总结再把少数候选交给消费者。"
    )
    documents = f"""# AI 时代消费者决策路径与品牌策略

## 第一章：什么变了
- 核心能力：重复 核心能力：精准 核心能力：对齐
- {long_sentence}
- 信息到达消费者之前，已经被压缩过一遍。
- 品牌不再只争曝光，还要争取被 AI 理解、纳入候选、形成推荐。
"""

    draft = build_document_driven_long_deck_draft(
        topic="把讲稿做成 5 页 PPT，尽量保持原文内容和原文字眼",
        documents=documents,
        target_count=5,
        min_pages=5,
        max_pages=5,
    )
    content_pages = draft[2:]
    text = json.dumps(content_pages, ensure_ascii=False)

    assert all(page["text_content"]["subhead"] == "" for page in content_pages)
    assert "AI 时代消费者决策路径与品牌策略" not in "\n".join(
        page["text_content"]["subhead"] for page in content_pages
    )
    assert long_sentence in text
    assert long_sentence in "\n".join(page["speaker_notes"] for page in content_pages)
    assert long_sentence not in "\n".join(page["text_content"]["body"] for page in content_pages)
    headlines = "\n".join(page["text_content"]["headline"] for page in content_pages)
    body_and_notes = "\n".join(
        f"{page['text_content']['body']}\n{page['speaker_notes']}"
        for page in content_pages
    )
    assert "重复 核心能力" not in headlines
    assert "核心能力：重复" in body_and_notes
    assert "核心能力：精准" in body_and_notes
    assert "核心能力：对齐" in body_and_notes
    assert "重复核心能力" not in text
    assert "讲述重点：先复述本页判断" not in text


def test_weak_legacy_contract_does_not_force_source_preserve_page_map(monkeypatch):
    documents = """# AI 时代品牌课

## 开场
- 原文开场第一句
- 原文开场第二句

## 结尾
- 原文结尾第一句
- 原文结尾第二句
"""
    weak_legacy_contract = {
        "task_type": "polish",
        "rewrite_level": "light",
        "page_order_policy": "preserve",
        "page_count_policy": "same",
        "source_fidelity": "faithful",
        "visual_source_use": "page_reference",
        "confidence": 0.55,
        "evidence": [],
    }
    calls = {"page_map": 0}

    def fake_model_page_map(**_kwargs):
        calls["page_map"] += 1
        return [
            {
                "page_num": 1,
                "type": "cover",
                "section_title": "封面",
                "headline": "AI 时代品牌课",
                "bullets": [],
                "speaker_notes": "开场。",
                "visual_suggestion": "封面。",
                "source_refs": [],
                "figure_refs": [],
                "generation_status": "page_map_model",
            },
            {
                "page_num": 2,
                "type": "content",
                "section_title": "内容",
                "headline": "从材料到观点",
                "bullets": ["保留开场的核心判断", "把结尾观点提前形成主线"],
                    "speaker_notes": _test_talk_notes("开场材料说明问题起点，结尾材料给出面向证据的行动提醒。"),
                "visual_suggestion": "结构图。",
                "source_refs": [],
                "figure_refs": [],
                "generation_status": "page_map_model",
            },
            {
                "page_num": 3,
                "type": "ending",
                "section_title": "收束",
                "headline": "真正的差异来自证据",
                "bullets": ["用源材料的结尾形成行动提醒"],
                "speaker_notes": "收束。",
                "visual_suggestion": "结束页。",
                "source_refs": [],
                "figure_refs": [],
                "generation_status": "page_map_model",
            },
        ]

    monkeypatch.setattr(content_plan_module, "_generate_model_page_map", fake_model_page_map)

    page_map = generate_content_page_map(
        topic="把这份材料做成 3 页高管汇报 PPT",
        documents=documents,
        page_count=3,
        intent_contract=weak_legacy_contract,
    )

    assert calls["page_map"] == 1
    assert len(page_map) == 3
    assert any(page["generation_status"] != "page_map_source" for page in page_map)


def test_source_preserve_page_map_uses_model_with_source_draft(monkeypatch):
    documents = """# AI 时代品牌课

## 开场
- 原文开场第一句
- 原文开场第二句

## 结尾
- 原文结尾第一句
- 原文结尾第二句
"""
    contract = {
        "task_type": "teaching_deck",
        "source_use": "faithful",
        "coverage": "near_complete",
        "compression": "low",
        "depth": "deep",
        "page_budget_policy": "explicit",
        "structure_policy": "source_order",
        "confidence": 0.9,
        "evidence": [],
    }

    calls = {"page_map": 0, "source_page_map_markdown": ""}

    def fake_model_page_map(**kwargs):
        calls["page_map"] += 1
        calls["source_page_map_markdown"] = kwargs.get("source_page_map_markdown") or ""
        return [
            {
                "page_num": 1,
                "type": "cover",
                "section_title": "封面",
                "headline": "AI 时代品牌课",
                "bullets": [],
                "speaker_notes": "开场。",
                "visual_suggestion": "封面。",
                "source_refs": [],
                "figure_refs": [],
                "generation_status": "page_map_model",
            },
            {
                "page_num": 2,
                "type": "content",
                "section_title": "开场",
                "headline": "开场先保留原文问题",
                "bullets": ["原文开场第一句", "原文开场第二句"],
                "speaker_notes": _test_talk_notes("原文开场第一句和第二句共同建立问题。"),
                "visual_suggestion": "问题页。",
                "source_refs": [],
                "figure_refs": [],
                "generation_status": "page_map_model",
            },
            {
                "page_num": 3,
                "type": "content",
                "section_title": "开场",
                "headline": "把开场判断拆成听众能进入的问题",
                "bullets": ["原文开场第一句", "原文开场第二句"],
                "speaker_notes": _test_talk_notes("继续解释原文开场，让听众进入核心问题。"),
                "visual_suggestion": "对比页。",
                "source_refs": [],
                "figure_refs": [],
                "generation_status": "page_map_model",
            },
            {
                "page_num": 4,
                "type": "content",
                "section_title": "结尾",
                "headline": "结尾第一句成为行动提醒",
                "bullets": ["原文结尾第一句", "原文结尾第二句"],
                "speaker_notes": _test_talk_notes("原文结尾第一句成为行动提醒。"),
                "visual_suggestion": "行动页。",
                "source_refs": [],
                "figure_refs": [],
                "generation_status": "page_map_model",
            },
            {
                "page_num": 5,
                "type": "content",
                "section_title": "结尾",
                "headline": "结尾第二句收住主线",
                "bullets": ["原文结尾第一句", "原文结尾第二句"],
                "speaker_notes": _test_talk_notes("原文结尾第二句收住整份内容主线。"),
                "visual_suggestion": "总结页。",
                "source_refs": [],
                "figure_refs": [],
                "generation_status": "page_map_model",
            },
            {
                "page_num": 6,
                "type": "ending",
                "section_title": "结束",
                "headline": "回到 AI 时代品牌课",
                "bullets": ["原文结尾第一句", "原文结尾第二句"],
                "speaker_notes": "结束。",
                "visual_suggestion": "封底。",
                "source_refs": [],
                "figure_refs": [],
                "generation_status": "page_map_model",
            },
        ]

    monkeypatch.setattr(content_plan_module, "_generate_model_page_map", fake_model_page_map)

    page_map = generate_content_page_map(
        topic="严格 6 页，尽量保持原文内容和原文字眼",
        documents=documents,
        page_count=6,
        intent_contract=contract,
    )
    text = json.dumps(page_map, ensure_ascii=False)

    assert calls["page_map"] == 1
    assert "原文开场第一句" in calls["source_page_map_markdown"]
    assert "原文结尾第二句" in calls["source_page_map_markdown"]
    assert len(page_map) == 6
    assert all(page["generation_status"] != "page_map_source" for page in page_map)
    assert "原文开场第一句" in text
    assert "原文结尾第二句" in text
    assert "互动" not in text
    assert "金句合集" not in text


def test_source_preserve_target_count_overrides_restructure_intent(monkeypatch):
    documents = """# AI 时代品牌课

## 开场
- 原文开场第一句
- 原文开场第二句

## 5.4 把证据做成资产：让人和 AI 都能相信你
- 适合谁：哪类人、哪类场景、哪类预算最适合你？
- 不适合谁：哪些人不该买你？
- 凭什么相信：真实评价、长测、认证、案例、售后是什么？

## 第六章：企业 90 天行动清单
- 查：AI 和平台现在如何描述你
- 定：找到品牌那个 1
- 建：搭建结构化证据资产
- 放：规模化转化成内容、销售话术、客服问答

## 最后一页
- AI 时代会加速淘汰伪品牌，也会让真正有差异、有证据的品牌更值钱。
"""
    topic = "把讲稿做成 8 页 PPT。尽量保持原文内容和原文字眼，完整还原原文结构与顺序。严格 8 页。"
    legacy_contract = infer_intent_contract(topic)

    assert legacy_contract["source_fidelity"] == "faithful"
    assert legacy_contract["rewrite_level"] == "light"
    assert _document_preservation_mode(documents, topic) == "faithful"

    calls = {"page_map": 0, "source_page_map_markdown": ""}

    def fake_model_page_map(**kwargs):
        calls["page_map"] += 1
        calls["source_page_map_markdown"] = kwargs.get("source_page_map_markdown") or ""
        return [
            {
                "page_num": idx,
                "type": "ending" if idx == 8 else "cover" if idx == 1 else "content",
                "section_title": "AI 时代品牌课",
                "headline": f"源文档主线第 {idx} 页",
                "bullets": [] if idx == 1 else [
                    "5.4 把证据做成资产：让人和 AI 都能相信你",
                    "第六章：企业 90 天行动清单",
                    "最后一页",
                ],
                "speaker_notes": _test_talk_notes("保留 5.4 证据资产、第六章行动清单和最后一页主线。"),
                "visual_suggestion": "课程页。",
                "source_refs": [],
                "figure_refs": [],
                "generation_status": "page_map_model",
            }
            for idx in range(1, 9)
        ]

    monkeypatch.setattr(content_plan_module, "_generate_model_page_map", fake_model_page_map)

    page_map = generate_content_page_map(
        topic=topic,
        documents=documents,
        page_count=8,
        intent_contract=legacy_contract,
    )
    text = json.dumps(page_map, ensure_ascii=False)

    assert calls["page_map"] == 1
    assert "5.4 把证据做成资产" in calls["source_page_map_markdown"]
    assert "第六章：企业 90 天行动清单" in calls["source_page_map_markdown"]
    assert len(page_map) == 8
    assert any(page["generation_status"] == "page_map_source" for page in page_map)
    assert "5.4 把证据做成资产" in text
    assert "第六章：企业 90 天行动清单" in text
    assert "最后一页" in text
    assert "AI 时代会加速淘汰伪品牌，也会让真正有差异、有证据的品牌更值钱。" in text
    assert "互动" not in text
    assert "金句合集" not in text


def test_page_map_merge_preserves_source_draft_figures_when_model_keeps_body():
    model_page_map = [
        {
            "page_num": 1,
            "type": "cover",
            "section_title": "封面",
            "headline": "向硅谷学创新",
            "bullets": [],
            "source_refs": [],
            "figure_refs": [],
        },
        {
            "page_num": 2,
            "type": "agenda",
            "section_title": "内容总览",
            "headline": "内容地图",
            "bullets": ["绪论", "第一章"],
            "source_refs": [],
            "figure_refs": [],
        },
        {
            "page_num": 3,
            "type": "content",
            "section_title": "绪论",
            "headline": "永续创新的真谛",
            "bullets": ["情感在变革过程中替代理性口号"],
            "source_refs": [{
                "source_document": "book.pdf",
                "source_page_num": 20,
                "source_type": "pdf",
                "reason": "永续创新的真谛",
            }],
            "figure_refs": [],
        },
    ]
    source_draft = [
        {**model_page_map[0], "generation_status": "page_map_source"},
        {**model_page_map[1], "generation_status": "page_map_source"},
        {
            **model_page_map[2],
            "generation_status": "page_map_source",
            "figure_refs": [{
                "source_document": "book.pdf",
                "source_page_num": 20,
                "source_type": "pdf",
                "figure_id": "book.pdf:p20:x1:1",
                "reason": "绪论里的永续创新示意图",
            }],
        },
    ]

    merged = content_plan_module._merge_page_map_with_source_draft(
        model_page_map,
        source_draft,
        target_count=3,
    )

    assert merged[2]["figure_refs"][0]["figure_id"] == "book.pdf:p20:x1:1"
    assert merged[2]["generation_status"] == "page_map_model_with_source_refs"


def test_page_map_merge_does_not_auto_repeat_the_same_source_draft_figure():
    source_ref = {
        "source_document": "book.pdf",
        "source_page_num": 20,
        "source_type": "pdf",
        "reason": "Nike 原则备忘录",
    }
    figure_ref = {
        "source_document": "book.pdf",
        "source_page_num": 20,
        "source_type": "pdf",
        "figure_id": "book.pdf:p20:x1:1",
        "reason": "Nike 原则备忘录",
    }
    model_page_map = [
        {"page_num": 1, "type": "cover", "headline": "向硅谷学创新", "bullets": []},
        {
            "page_num": 2,
            "type": "content",
            "section_title": "绪论",
            "headline": "永续创新的真谛",
            "bullets": ["耐克原则备忘录激发情感共鸣"],
            "source_refs": [source_ref],
            "figure_refs": [],
        },
        {
            "page_num": 3,
            "type": "content",
            "section_title": "绪论",
            "headline": "乔布斯与苹果的转型",
            "bullets": ["乔布斯重返苹果"],
            "source_refs": [source_ref],
            "figure_refs": [],
        },
    ]
    source_draft = [
        {**model_page_map[0], "generation_status": "page_map_source"},
        {**model_page_map[1], "generation_status": "page_map_source", "figure_refs": [figure_ref]},
        {**model_page_map[2], "generation_status": "page_map_source", "figure_refs": []},
    ]

    merged = content_plan_module._merge_page_map_with_source_draft(
        model_page_map,
        source_draft,
        target_count=3,
    )

    assert [page.get("figure_refs") or [] for page in merged] == [[], [figure_ref], []]


def test_content_plan_preserves_plus_signs_inside_framework_bullets():
    page_map = [
        {"page_num": 1, "type": "cover", "headline": "向硅谷学创新", "bullets": []},
        {
            "page_num": 2,
            "type": "content",
            "section_title": "全章节",
            "headline": "永续创新的完整框架",
            "bullets": [
                "利他（存在主义 + 客户痴迷 + 皮格马利翁效应）→ 激情（创业心智模式 + 节奏管理 + 双重模式）→ 勇气（大胆进攻 + 跨界协作）",
                "六大支撑特征：元敏捷、第一性原理、忘却重构、减法、可控混沌、敢于谏言",
            ],
        },
        {"page_num": 3, "type": "ending", "headline": "谢谢", "bullets": []},
    ]

    outline = content_plan_from_page_map(page_map, expected_total=3)

    body = outline[1]["text_content"]["body"]
    assert "存在主义 + 客户痴迷 + 皮格马利翁效应" in body
    assert "创业心智模式 + 节奏管理 + 双重模式" in body
    assert "\n+ 客户痴迷" not in body
    assert "\n+ 节奏管理" not in body


def test_body_normalization_preserves_inline_symbol_separators():
    body = "\n".join([
        "- 战略主线：端到端 - 高质量 - 可规模化",
        "- 增长公式：投入 * 效率 * 复利",
        "- 品牌资产：品牌符号 • 信任资产 • 情感记忆",
        "- 版本演进：1.0 → 2.0 → 3.0",
    ])

    normalized = content_plan_module._normalize_body_markdown(body)

    assert "端到端 - 高质量 - 可规模化" in normalized
    assert "投入 * 效率 * 复利" in normalized
    assert "品牌符号 • 信任资产 • 情感记忆" in normalized
    assert "1.0 → 2.0 → 3.0" in normalized
    assert "\n- 高质量" not in normalized
    assert "\n* 效率" not in normalized
    assert "\n• 信任资产" not in normalized


def test_body_normalization_keeps_single_inline_numbered_reference():
    body = "- 评分维度 1. 战略清晰度不是唯一标准，还需要组织执行和客户反馈。"

    normalized = content_plan_module._normalize_body_markdown(body)

    assert normalized == body


def test_body_normalization_preserves_single_inline_bracket_phrase():
    body = "判断：这里不是 【流程图】 指令，而是对材料中括号标签的引用。"

    normalized = content_plan_module._normalize_body_markdown(body)

    assert normalized == body


def test_body_normalization_restores_clear_compact_numbered_lists():
    body = "核心步骤：1. 确认使命 2. 对齐团队 3. 快速试验"

    normalized = content_plan_module._normalize_body_markdown(body)

    assert normalized == "核心步骤：\n1. 确认使命\n2. 对齐团队\n3. 快速试验"


def test_body_normalization_canonicalizes_leading_plus_list_markers():
    body = "+ 同理心式想象力预见未来需求\n+ 多案例理论构建"

    normalized = content_plan_module._normalize_body_markdown(body)

    assert normalized == "- 同理心式想象力预见未来需求\n- 多案例理论构建"


def test_content_plan_backfills_same_source_page_figures_from_source_context():
    source_context = """--- SOURCE filename="book.pdf" kind="pdf" ---
--- PAGE 20 ---
这些原则激发了人们内心深处的共鸣，强调了情感在变革过程中的作用。
--- AVAILABLE_FIGURES ---
FIGURE figure_id="book.pdf:p20:x1:1" source_document="book.pdf" source_type="pdf" source_page_num="20" nearby_text="Nike 原则备忘录激发情感共鸣"
"""
    page_map = [
        {
            "page_num": 1,
            "type": "cover",
            "section_title": "封面",
            "headline": "向硅谷学创新",
            "bullets": [],
        },
        {
            "page_num": 2,
            "type": "content",
            "section_title": "绪论",
            "headline": "永续创新的真谛：情感驱动变革",
            "bullets": [
                "情感是变革成功的关键",
                "耐克第一任营销负责人发布的原则备忘录激发了情感共鸣",
            ],
            "source_refs": [{
                "source_document": "book.pdf",
                "source_page_num": 20,
                "source_type": "pdf",
                "reason": "Nike 原则备忘录",
            }],
            "figure_refs": [],
        },
    ]

    outline = content_plan_from_page_map(page_map, source_context=source_context)

    assert outline[1]["figure_refs"] == [{
        "source_document": "book.pdf",
        "source_page_num": 20,
        "source_type": "pdf",
        "figure_id": "book.pdf:p20:x1:1",
        "reason": "Nike 原则备忘录激发情感共鸣",
    }]


def test_content_plan_does_not_backfill_auxiliary_same_source_page_figures():
    source_context = """--- SOURCE filename="book.pdf" kind="pdf" ---
--- PAGE 67 ---
ZARA 每天根据门店销售数据调整设计。
--- AVAILABLE_FIGURES ---
FIGURE figure_id="book.pdf:p67:x1:1" source_document="book.pdf" source_type="pdf" source_page_num="67" figure_role="auxiliary" content_significance="low" image_width="92" image_height="45" nearby_text="正文旁的小装饰符号"
"""
    page_map = [
        {
            "page_num": 1,
            "type": "cover",
            "section_title": "封面",
            "headline": "向硅谷学创新",
            "bullets": [],
        },
        {
            "page_num": 2,
            "type": "content",
            "section_title": "客户痴迷",
            "headline": "ZARA 的敏捷设计",
            "bullets": ["根据门店销售数据调整设计"],
            "source_refs": [{
                "source_document": "book.pdf",
                "source_page_num": 67,
                "source_type": "pdf",
                "reason": "ZARA 案例",
            }],
            "figure_refs": [],
        },
    ]

    outline = content_plan_from_page_map(page_map, source_context=source_context)

    assert outline[1]["figure_refs"] == []


def test_content_markdown_normalization_repairs_model_text_content_schema():
    outline = [
        {
            "page_num": 1,
            "type": "cover",
            "section_title": "封面",
            "text_content": {"body": ""},
            "speaker_notes": "开场白：今天的主题叫'嬴利与天龙十部'。",
            "visual_suggestion": "深色封面。",
            "source_refs": "材料文件：嬴利与天龙十部完整演讲内容稿.md",
        },
        {
            "page_num": 2,
            "type": "outline",
            "section_title": "课程目录",
            "text_content": "【天龙十部·课程地图】\n一、战略设计：世界级标准\n二、价值创造：独一无二的价值锚点",
            "speaker_notes": "翻页过渡：先看课程地图。",
            "visual_suggestion": "时间轴。",
            "source_refs": "材料文件：嬴利与天龙十部完整演讲内容稿.md - 总论",
        },
        {
            "page_num": 3,
            "type": "content",
            "section_title": "营销管理",
            "text_content": {
                "headline": "***营销不是卖货，而是实现价量双增。**",
                "body": "价决定生死，量决定大小。",
            },
            "speaker_notes": "强调价量双增。",
            "visual_suggestion": "公式页。",
            "source_refs": "原文第六部",
        },
    ]

    normalized = content_plan_module._normalize_content_markdown(
        outline,
        topic="这是一场 2 小时的企业家分享课程，尽可能用原文整理成 PPT。",
    )

    assert normalized[0]["text_content"]["headline"] == "嬴利与天龙十部"
    assert normalized[0]["text_content"]["body"] == ""
    assert normalized[1]["text_content"]["headline"] == "天龙十部·课程地图"
    assert "战略设计：世界级标准" in normalized[1]["text_content"]["body"]
    assert "【天龙十部·课程地图】" not in normalized[1]["text_content"]["body"]
    assert normalized[2]["text_content"]["headline"] == "营销不是卖货，而是实现价量双增。"
    assert normalized[2]["text_content"]["subhead"] == ""


def test_content_normalization_strips_heading_markers_and_canonicalizes_types():
    outline = [
        {
            "page_num": 2,
            "type": "outline",
            "section_title": "课程目录",
            "text_content": {
                "headline": "### 天龙十部·课程地图",
                "body": "一、战略设计：世界级标准 二、价值创造：独一无二的价值锚点 三、产品战略：聚焦品牌第一",
            },
            "speaker_notes": "目录页。",
            "visual_suggestion": "路线图。",
        },
        {
            "page_num": 28,
            "type": "section_cover",
            "section_title": "第三部：产品战略",
            "text_content": {
                "headline": "### 第三部：产品战略",
                "body": "## 聚焦品牌第一\n\n### 核心公式\n\n1米宽 × 1000米深 = 品牌第一",
            },
            "speaker_notes": "章节页。",
            "visual_suggestion": "章节封面。",
        },
        {
            "page_num": 29,
            "type": "core_judgment",
            "section_title": "产品战略",
            "text_content": {
                "headline": "### 产和品必须分开看",
                "body": "【旧逻辑】 ■ 先生产，再找市场 ■ 先扩产能，再想销售\n\n### 高质量增长的产品逻辑",
            },
            "speaker_notes": "判断页。",
            "visual_suggestion": "对比页。",
        },
    ]

    normalized = content_plan_module._normalize_content_markdown(outline)

    assert normalized[0]["type"] == "toc"
    assert normalized[0]["text_content"]["headline"] == "天龙十部·课程地图"
    assert " 二、" not in normalized[0]["text_content"]["body"]
    assert any(line.startswith("二、价值创造") for line in normalized[0]["text_content"]["body"].splitlines())
    assert normalized[1]["type"] == "section"
    assert normalized[1]["text_content"]["headline"] == "第三部：产品战略"
    assert "##" not in normalized[1]["text_content"]["body"]
    assert normalized[2]["type"] == "content"
    assert normalized[2]["text_content"]["headline"] == "产和品必须分开看"
    assert "###" not in normalized[2]["text_content"]["body"]
    assert "\n■ 先生产" in normalized[2]["text_content"]["body"]
    assert "\n■ 先扩产能" in normalized[2]["text_content"]["body"]


def test_page_map_usefulness_rejects_format_placeholder_words():
    page_map = [
        {
            "page_num": 1,
            "type": "cover",
            "section_title": "封面",
            "headline": "标题",
            "bullets": [],
            "speaker_notes": "",
            "visual_suggestion": "",
        },
        {
            "page_num": 2,
            "type": "content",
            "section_title": "主体",
            "headline": "标题",
            "bullets": ["bullet", "bullet", "..."],
            "speaker_notes": "演讲者备注",
            "visual_suggestion": "画面建议",
        },
        {
            "page_num": 3,
            "type": "content",
            "section_title": "主体",
            "headline": "标题",
            "bullets": ["bullet", "bullet"],
            "speaker_notes": "演讲者备注",
            "visual_suggestion": "画面建议",
        },
    ]

    assert not content_plan_module._page_map_is_useful(page_map, target_count=3, min_pages=3, strict=False)


def test_page_map_normalization_drops_format_placeholder_bullets():
    page_map = content_plan_module._normalize_page_map([
        {
            "page_num": 1,
            "type": "content",
            "section_title": "目录",
            "headline": "本次提案怎么展开",
            "bullets": ["-", "一、核心创意", "bullet", "...", "二、执行节奏"],
        }
    ])

    assert page_map[0]["bullets"] == ["一、核心创意", "二、执行节奏"]


def test_page_map_usefulness_rejects_body_replay_speaker_notes():
    page_map = [
        {
            "page_num": 1,
            "type": "cover",
            "section_title": "封面",
            "headline": "AI 时代品牌课",
            "bullets": [],
            "speaker_notes": "开场说明主题。",
        },
        {
            "page_num": 2,
            "type": "content",
            "section_title": "第一章",
            "headline": "消费者开始把信息处理交给 AI",
            "bullets": [
                "消费者把测评、参数、优惠和差评交给 AI 先读一遍",
                "品牌不只争曝光，还要争取被 AI 理解成合适答案",
            ],
            "speaker_notes": (
                "这一页口头展开：\n"
                "- 消费者把测评、参数、优惠和差评交给 AI 先读一遍\n"
                "- 品牌不只争曝光，还要争取被 AI 理解成合适答案"
            ),
        },
        {
            "page_num": 3,
            "type": "content",
            "section_title": "第二章",
            "headline": "品牌要建设可被读取的证据系统",
            "bullets": [
                "详情页、评价、问答和内容资产都要回答同一个核心承诺",
                "证据越结构化，越容易进入平台和 AI 的推荐理由",
            ],
            "speaker_notes": (
                "这一页口头展开：\n"
                "- 详情页、评价、问答和内容资产都要回答同一个核心承诺\n"
                "- 证据越结构化，越容易进入平台和 AI 的推荐理由"
            ),
        },
    ]

    assert not content_plan_module._page_map_is_useful(page_map, target_count=3, min_pages=3, strict=False)


def test_page_map_placeholder_model_output_is_rejected_instead_of_using_source_draft(monkeypatch):
    placeholder_map = [
        {"page_num": 1, "type": "cover", "section_title": "封面", "headline": "标题", "bullets": []},
        {"page_num": 2, "type": "content", "section_title": "主体", "headline": "标题", "bullets": ["bullet", "bullet"]},
        {"page_num": 3, "type": "ending", "section_title": "结尾", "headline": "下一步", "bullets": []},
    ]
    source_draft = [
        {"page_num": 1, "type": "cover", "section_title": "封面", "headline": "真实项目标题", "bullets": []},
        {"page_num": 2, "type": "content", "section_title": "主体", "headline": "真实执行节奏", "bullets": ["第一阶段：冷静开场", "第二阶段：省电收束"]},
        {"page_num": 3, "type": "ending", "section_title": "结尾", "headline": "真实下一步", "bullets": []},
    ]

    monkeypatch.setattr(content_plan_module, "_generate_model_page_map", lambda **_kwargs: placeholder_map)
    monkeypatch.setattr(content_plan_module, "_source_draft_page_map", lambda **_kwargs: source_draft)

    with pytest.raises(ValueError) as exc_info:
        generate_content_page_map(
            topic="把脚本整理成 3 页提案 PPT",
            documents="真实脚本材料",
            page_count=3,
        )

    assert "model output contained format placeholders" in str(exc_info.value)


def test_long_deck_contract_rejects_empty_content_body_instead_of_patching_from_notes():
    outline = [
        {
            "page_num": 31,
            "type": "content",
            "section_title": "第五部：预算管理",
            "text_content": {
                "headline": "预算要处理三条增长曲线：当下、明年与未来",
                "body": "",
            },
            "speaker_notes": (
                "讲师在这里要强调三条曲线的递进关系。"
                "先让主营业务有造血能力，再用主营业务赚的钱布局新业务。"
                "不要三条曲线一起烧钱，那是自杀式经营。"
            ),
            "visual_suggestion": "三横条递进图示。",
        }
    ]

    normalized = content_plan_module._normalize_content_markdown(outline)

    assert normalized[0]["text_content"]["body"] == ""
    assert content_plan_module._empty_required_content_body_pages(normalized) == [31]
    try:
        content_plan_module._assert_long_deck_chunk_contract(normalized, start_page=31, end_page=32)
    except ValueError as exc:
        assert "text_content.body" in str(exc)
        assert "speaker_notes" in str(exc)
    else:
        raise AssertionError("long-deck chunks with empty visible body must fail before persistence")


def test_page_map_parser_drops_markdown_separator_bullets():
    markdown = """P1｜cover｜封面｜品牌增长课
备注：封面。

P2｜content｜正文｜核心判断
- ---
- 真正要保留的内容
备注：解释判断。"""

    outline = content_plan_from_page_map(parse_page_map_markdown(markdown))
    body = outline[1]["text_content"]["body"]

    assert "---" not in body
    assert "真正要保留的内容" in body


def test_paginated_markdown_draft_reuses_pages_without_separator_copy(monkeypatch):
    documents = """--- 文档: 客户分页稿.md ---
# 客户分页稿

## 注意事项
```
### 页面类型
- 标题：示例
- 内容：示例
- 表达意图：示例
```

---

## 模块：封面与开场

---

### 封面
- 标题：疯火轮 AI — 营销人的 AI 工作台
- 内容：
越用越懂你 · 边说边交付 · 团队一起用
madfireai.com
- 表达意图：品牌认知锚点

---

### 使命页
- 标题：为营销人构建一个高效的AI工作环境
- 内容：（无额外正文，标题即核心信息）
- 表达意图：用一句有力的话传递产品分量

---
"""

    monkeypatch.setattr(content_plan_module, "get_llm_client", lambda: (_ for _ in ()).throw(AssertionError("LLM should not be called")))
    monkeypatch.setattr(content_plan_module.get_knowledge_augmenter(), "augment", lambda *_args, **_kwargs: "")

    assert len(parse_paginated_markdown_content_plan(documents)) == 2

    outline = generate_content_plan(topic="帮我做成 PPT", documents=documents)
    rendered = "\n".join(
        "\n".join(str(v) for v in page.get("text_content", {}).values())
        for page in outline
    )

    assert len(outline) == 2
    assert outline[0]["generation_status"] == "source_paginated_markdown"
    assert outline[0]["type"] == "cover"
    assert outline[0]["text_content"]["headline"] == "疯火轮 AI — 营销人的 AI 工作台"
    assert "越用越懂你" in outline[0]["text_content"]["body"]
    assert outline[1]["text_content"]["headline"] == "为营销人构建一个高效的AI工作环境"
    assert outline[1]["text_content"]["body"] == ""
    assert "---" not in rendered


def test_single_uploaded_ppt_replicate_uses_model_path_with_direct_mode(monkeypatch):
    documents = """--- 文档: 混沌-分众传媒AI落地实践-20250527.pptx ---
--- PPT_SOURCE filename="混沌-分众传媒AI落地实践-20250527.pptx" pages=3 ---

--- 第1页 ---
分众传媒 AI 实践分享

分众 KA 负责人/AI 创新业务负责人 桑卓豪
【备注】
开场备注

--- 第2页 ---
为什么今天
分众要做AI？

--- 第3页 ---
目前已开发落地的
AI 业务场景
客户管理
创意流程
"""

    captured = {}

    def fake_generate_model_page_map(**kwargs):
        captured["mode"] = kwargs.get("mode")
        return [
            {
                "page_num": 1,
                "type": "cover",
                "headline": "分众传媒 AI 实践分享",
                "subhead": "分众 KA 负责人/AI 创新业务负责人 桑卓豪",
                "bullets": [],
                "speaker_notes": "开场备注",
                "source_refs": [{"source_document": "混沌-分众传媒AI落地实践-20250527.pptx", "source_page_num": 1, "reason": "direct_replicate"}],
                "generation_status": "page_map_model",
            },
            {
                "page_num": 2,
                "type": "section",
                "headline": "为什么今天\n分众要做AI？",
                "subhead": "",
                "bullets": [],
                "speaker_notes": "",
                "source_refs": [{"source_document": "混沌-分众传媒AI落地实践-20250527.pptx", "source_page_num": 2, "reason": "direct_replicate"}],
                "generation_status": "page_map_model",
            },
            {
                "page_num": 3,
                "type": "content",
                "headline": "目前已开发落地的\nAI 业务场景",
                "subhead": "",
                "bullets": ["客户管理", "创意流程"],
                "speaker_notes": "",
                "source_refs": [{"source_document": "混沌-分众传媒AI落地实践-20250527.pptx", "source_page_num": 3, "reason": "direct_replicate"}],
                "generation_status": "page_map_model",
            },
        ]

    monkeypatch.setattr(content_plan_module, "_generate_model_page_map", fake_generate_model_page_map)

    outline = generate_content_plan(
        topic="【文件：混沌-分众传媒AI落地实践-20250527.pptx】 1:1 复刻这个 ppt",
        documents=documents,
    )

    rendered = "\n".join(
        "\n".join(str(v) for v in page.get("text_content", {}).values())
        for page in outline
    )
    assert captured.get("mode") == "direct_replicate"
    assert len(outline) == 3
    assert [page["generation_status"] for page in outline] == ["page_map_model"] * 3
    assert outline[0]["text_content"]["headline"] == "分众传媒 AI 实践分享"
    assert "分众 KA 负责人" in outline[0]["text_content"]["subhead"]
    assert outline[0]["speaker_notes"] == "开场备注"
    assert outline[1]["text_content"]["headline"] == "为什么今天\n分众要做AI？"
    assert "客户管理" in outline[2]["text_content"]["body"]
    assert "用户上传材料" not in rendered
    assert outline[1]["source_refs"] == [{
        "source_document": "混沌-分众传媒AI落地实践-20250527.pptx",
        "source_page_num": 2,
        "source_type": "pptx_slide",
        "reason": "direct_replicate",
    }]
    assert "source_facts" not in outline[1]
    assert "replicate_quality" not in outline[0]


def test_direct_ppt_outline_is_disabled_for_transform_requests():
    documents = """--- PPT_SOURCE filename="source.pptx" pages=1 ---

--- 第1页 ---
原始第一页
"""

    assert build_direct_ppt_replicate_outline(documents, "请提取其中的客户管理部分做成 5 页") == []


def test_direct_ppt_cover_two_lines_uses_second_line_as_subhead():
    documents = """--- PPT_SOURCE filename="source.pptx" pages=2 ---

--- 第1页 ---
极简复刻封面
这是一份没有图片素材的 PPT

--- 第2页 ---
为什么今天
分众要做AI？
"""

    outline = build_direct_ppt_replicate_outline(documents, "1:1 复刻这个 ppt")

    assert outline[0]["text_content"]["headline"] == "极简复刻封面"
    assert outline[0]["text_content"]["subhead"] == "这是一份没有图片素材的 PPT"
    assert outline[1]["text_content"]["headline"] == "为什么今天\n分众要做AI？"


def test_direct_ppt_quality_gate_flags_marker_and_missing_source_ref():
    outline = [
        {
            "page_num": 1,
            "text_content": {"headline": "用户上传材料", "subhead": "", "body": ""},
            "source_refs": [{"source_document": "source.pptx", "source_page_num": 1}],
        },
        {
            "page_num": 2,
            "text_content": {"headline": "真实第二页", "subhead": "", "body": ""},
            "source_refs": [],
        },
    ]

    quality = validate_direct_ppt_replicate_outline(
        outline,
        expected_pages=3,
        source_document="source.pptx",
    )

    assert quality["status"] == "needs_review"
    assert quality["checks"]["marker_free"] is False
    assert quality["checks"]["source_refs_complete"] is False
    assert quality["marker_pages"] == [1]
    assert quality["missing_source_ref_pages"] == [2]
    assert quality["missing_pages"] == [3]


def test_generate_content_plan_uses_model_page_map_before_json(monkeypatch):
    class FakeMessage:
        content = """P1｜cover｜封面｜品牌增长课
备注：封面开场。

P2｜content｜背景｜增长为什么变难
- 流量红利变薄
- 用户决策链路变长
备注：讲稿内容：增长变难来自获客效率下降和决策周期拉长，需要重新组织经营动作。

P3｜ending｜总结｜下一步
- 回到增长动作
备注：收束。"""

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeCompletions:
        def create(self, **kwargs):
            prompt = kwargs["messages"][1]["content"]
            assert "逐页内容地图" in prompt
            assert "不要输出 JSON" in prompt
            assert "不要把同一个来源主题拆成" in prompt
            assert "不能出现连续两页同标题" in prompt
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(content_plan_module, "get_llm_client", lambda: FakeClient())
    monkeypatch.setattr(content_plan_module.get_knowledge_augmenter(), "augment", lambda *_args, **_kwargs: "")

    outline = generate_content_plan(
        topic="做一份 3 页品牌增长课",
        documents="增长材料",
        page_count=3,
    )

    assert [page["page_num"] for page in outline] == [1, 2, 3]
    assert outline[1]["text_content"]["headline"] == "增长为什么变难"
    assert "流量红利变薄" in outline[1]["text_content"]["body"]
    assert outline[1]["generation_status"] == "page_map_model"


def test_generate_content_plan_trims_page_map_to_strict_requested_count(monkeypatch):
    page_map = "\n\n".join(
        [
                "P1｜cover｜封面｜夏日水果和甜品大探险\n备注：开场。\n视觉：封面主视觉",
                *[
                    f"P{idx}｜content｜内容｜第 {idx} 页主题\n"
                    f"- 具体内容 {idx} 的观察\n"
                    f"- 具体内容 {idx} 的行动\n"
                    f"备注：讲稿内容：第 {idx} 页用一个具体观察说明甜品冰箱贴的制作动作。\n视觉：内容画面 {idx}"
                    for idx in range(2, 16)
                ],
            "P16｜ending｜结束｜甜品冰箱贴完成啦\n- 展示大家做好的甜品冰箱贴\n备注：收束并表扬孩子。\n视觉：甜品冰箱贴作品墙",
        ]
    )

    class FakeMessage:
        content = page_map

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeCompletions:
        def create(self, **kwargs):
            prompt = kwargs["messages"][1]["content"]
            assert "逐页内容地图" in prompt
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(content_plan_module, "get_llm_client", lambda: FakeClient())
    monkeypatch.setattr(content_plan_module.get_knowledge_augmenter(), "augment", lambda *_args, **_kwargs: "")

    outline = generate_content_plan(
        topic="我只要 8 页，最后的手工是甜品冰箱贴",
        page_count=8,
    )

    assert len(outline) == 8
    assert [page["page_num"] for page in outline] == list(range(1, 9))
    assert outline[0]["type"] == "cover"
    assert outline[-1]["type"] == "ending"
    assert "甜品冰箱贴" in outline[-1]["text_content"]["headline"]


def test_page_map_retries_model_generation_when_first_call_fails(monkeypatch):
    documents = """# 面向AI时代，企业营与销该如何布局

## 模块一：道

- ChatGPT 达到 1 亿规模只用了 2 个月
- AI 正在成为消费决策的新中介
"""
    calls = {"count": 0}

    def fake_model_page_map(**_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise TimeoutError("timeout")
        return [
            {
                "page_num": 1,
                "type": "cover",
                "section_title": "封面",
                "headline": "面向AI时代，企业营与销该如何布局",
                "bullets": [],
                "speaker_notes": "开场。",
                "visual_suggestion": "封面。",
                "generation_status": "page_map_model",
            },
            *[
                {
                    "page_num": idx,
                    "type": "content",
                    "section_title": "模块一：道",
                    "headline": f"AI 时代课程第 {idx} 页",
                    "bullets": [
                        "ChatGPT 达到 1 亿规模只用了 2 个月",
                        "AI 正在成为消费决策的新中介",
                    ],
                    "speaker_notes": _test_talk_notes("ChatGPT 两个月达到 1 亿规模，说明 AI 正在进入消费决策入口。"),
                    "visual_suggestion": "结构页。",
                    "generation_status": "page_map_model",
                }
                for idx in range(2, 10)
            ],
            {
                "page_num": 10,
                "type": "ending",
                "section_title": "结束",
                "headline": "回到企业营与销的行动",
                "bullets": ["把材料转成下一步行动"],
                "speaker_notes": "收束。",
                "visual_suggestion": "封底。",
                "generation_status": "page_map_model",
            },
        ]

    monkeypatch.setattr(content_plan_module, "_generate_model_page_map", fake_model_page_map)

    page_map = generate_content_page_map(
        topic="做成 10 页课程",
        documents=documents,
        page_count=10,
    )

    rendered = "\n".join("\n".join(str(item) for item in (page.get("bullets") or [])) for page in page_map)
    assert calls["count"] == 2
    assert len(page_map) == 10
    assert "ChatGPT 达到 1 亿规模只用了 2 个月" in rendered
    assert all(page["generation_status"] != "page_map_source" for page in page_map)


def test_page_map_does_not_fallback_to_source_draft_when_model_keeps_failing(monkeypatch):
    documents = """# 面向AI时代，企业营与销该如何布局

## 模块一：道

- ChatGPT 达到 1 亿规模只用了 2 个月
- AI 正在成为消费决策的新中介
"""
    calls = {"count": 0}

    def fake_model_page_map(**_kwargs):
        calls["count"] += 1
        raise TimeoutError("timeout")

    monkeypatch.setattr(content_plan_module, "_generate_model_page_map", fake_model_page_map)

    with pytest.raises(ValueError) as exc_info:
        generate_content_page_map(
            topic="做成 10 页课程",
            documents=documents,
            page_count=10,
        )

    assert calls["count"] == 2
    assert "failed before producing usable model pages" in str(exc_info.value)


def test_partial_model_page_map_does_not_succeed_by_filling_missing_pages_from_source_draft(monkeypatch):
    documents = """# 面向AI时代，企业营与销该如何布局

## 模块一：道

- ChatGPT 达到 1 亿规模只用了 2 个月
- AI 正在成为消费决策的新中介
"""
    source_draft = [
        {
            "page_num": idx,
            "type": "content",
            "section_title": "源稿补齐",
            "headline": f"源稿第 {idx} 页",
            "bullets": ["源稿要点"],
            "speaker_notes": "源稿备注。",
            "visual_suggestion": "源稿画面。",
            "generation_status": "page_map_source",
        }
        for idx in range(1, 11)
    ]
    partial_model_map = [
        {
            "page_num": idx,
            "type": "content",
            "section_title": "模型规划",
            "headline": f"模型第 {idx} 页",
            "bullets": ["ChatGPT 达到 1 亿规模只用了 2 个月"],
            "speaker_notes": "模型备注。",
            "visual_suggestion": "模型画面。",
            "generation_status": "page_map_model",
        }
        for idx in range(1, 7)
    ]

    monkeypatch.setattr(content_plan_module, "_source_draft_page_map", lambda **_kwargs: source_draft)
    monkeypatch.setattr(content_plan_module, "_generate_model_page_map", lambda **_kwargs: partial_model_map)

    with pytest.raises(ValueError) as exc_info:
        generate_content_page_map(
            topic="做成 10 页课程",
            documents=documents,
            page_count=10,
        )

    assert "failed before producing usable model pages" in str(exc_info.value)


def test_page_map_rejects_inline_page_markers_inside_body(monkeypatch):
    model_map = [
        {
            "page_num": 1,
            "type": "cover",
            "section_title": "封面",
            "headline": "AI 时代消费者决策路径与品牌策略",
            "bullets": [],
            "speaker_notes": "开场。",
            "visual_suggestion": "封面。",
            "generation_status": "page_map_model",
        },
        {
            "page_num": 2,
            "type": "content",
            "section_title": "证据货架",
            "headline": "未来的详情页，是给 AI 读的",
            "bullets": [
                "AI 时代品牌需要一张证据货架",
                'P52｜content｜证据货架 + "不适合谁"的逆向价值｜',
            ],
            "speaker_notes": "讲证据货架。",
            "visual_suggestion": "表格页。",
            "generation_status": "page_map_model",
        },
        {
            "page_num": 3,
            "type": "ending",
            "section_title": "结尾",
            "headline": "结语",
            "bullets": ["在人心里有位置，在平台里有流量，在 AI 里有推荐"],
            "speaker_notes": "收束。",
            "visual_suggestion": "结束页。",
            "generation_status": "page_map_model",
        },
    ]

    monkeypatch.setattr(content_plan_module, "_generate_model_page_map", lambda **_kwargs: model_map)
    monkeypatch.setattr(content_plan_module, "_source_draft_page_map", lambda **_kwargs: [])

    with pytest.raises(ValueError) as exc_info:
        generate_content_page_map(
            topic="做成 3 页课程",
            documents="AI 时代消费者决策路径与品牌策略",
            page_count=3,
        )

    assert "page markers" in str(exc_info.value)


def test_source_preserve_page_map_repairs_missing_source_tail(monkeypatch):
    source_draft = [
        {
            "page_num": idx,
            "type": "content",
            "section_title": "前文",
            "headline": f"前文第 {idx} 页",
            "bullets": ["消费者正在让渡信息处理权"],
            "speaker_notes": _test_talk_notes("用户把信息筛选交给 AI，平台因此获得新的决策影响力。"),
            "visual_suggestion": "内容页。",
            "generation_status": "page_map_source",
        }
        for idx in range(1, 9)
    ] + [
        {
            "page_num": 9,
            "type": "content",
            "section_title": "第六章",
            "headline": "第六章：企业 90 天行动清单",
            "bullets": ["查 -> 定 -> 建 -> 放", "没有建，AI 抓不到可引用的结构化证据"],
            "speaker_notes": _test_talk_notes("90 天行动清单包括查、定、建、放和结构化证据。"),
            "visual_suggestion": "行动清单表。",
            "generation_status": "page_map_source",
        },
        {
            "page_num": 10,
            "type": "ending",
            "section_title": "结语",
            "headline": "在人心里有位置，在平台里有流量，在 AI 里有推荐",
            "bullets": ["当客户的 AI 凝视你的品牌时，它到底能看到什么？"],
            "speaker_notes": "收束。",
            "visual_suggestion": "结束页。",
            "generation_status": "page_map_source",
        },
    ]
    model_map = [
        {
            "page_num": idx,
            "type": "content" if idx not in {1, 10} else ("cover" if idx == 1 else "ending"),
            "section_title": "前文",
            "headline": f"模型前文第 {idx} 页",
            "bullets": ["消费者正在让渡信息处理权", "平台权力正在重构"],
            "speaker_notes": _test_talk_notes("用户把信息筛选交给 AI，平台因此获得新的决策影响力。"),
            "visual_suggestion": "内容页。",
            "generation_status": "page_map_model",
        }
        for idx in range(1, 11)
    ]
    contract = {
        "task_type": "source_to_ppt",
        "source_use": "faithful",
        "coverage": "near_complete",
        "compression": "low",
        "depth": "deep",
        "page_budget_policy": "explicit",
        "structure_policy": "source_order",
        "confidence": 0.95,
        "evidence": ["保留原文结构和金句"],
    }

    calls = {"model": 0}

    def fake_model_page_map(**_kwargs):
        calls["model"] += 1
        return model_map

    monkeypatch.setattr(content_plan_module, "_source_draft_page_map", lambda **_kwargs: source_draft)
    monkeypatch.setattr(content_plan_module, "_generate_model_page_map", fake_model_page_map)

    result = generate_content_page_map(
        topic="保留原文结构和金句，做成 10 页课程",
        documents="第六章：企业 90 天行动清单\n在人心里有位置，在平台里有流量，在 AI 里有推荐",
        page_count=10,
        intent_contract=contract,
    )

    assert calls["model"] == 1
    assert result[8]["headline"] == "第六章：企业 90 天行动清单"
    assert result[9]["headline"] == "在人心里有位置，在平台里有流量，在 AI 里有推荐"


def test_source_preserve_page_map_repairs_missing_source_structure_anchor(monkeypatch):
    source_draft = [
        {
            "page_num": 1,
            "type": "cover",
            "section_title": "封面",
            "headline": "AI 时代品牌课",
            "bullets": [],
            "speaker_notes": "开场。",
            "visual_suggestion": "封面。",
            "generation_status": "page_map_source",
        },
        {
            "page_num": 2,
            "type": "section",
            "section_title": "第一章",
            "headline": "第一章：什么变了？决策不再只发生在人脑里",
            "bullets": ["消费者正在让渡信息处理权"],
            "speaker_notes": _test_talk_notes("第一章说明消费者正在让渡信息处理权。"),
            "visual_suggestion": "章节页。",
            "generation_status": "page_map_source",
        },
        {
            "page_num": 3,
            "type": "section",
            "section_title": "第二章",
            "headline": "第二章：什么没变？人心仍然是终点",
            "bullets": ["人还是为自己的待办任务而买"],
            "speaker_notes": "讲第二章。",
            "visual_suggestion": "章节页。",
            "generation_status": "page_map_source",
        },
        {
            "page_num": 4,
            "type": "section",
            "section_title": "第三章",
            "headline": "第三章：平台权力正在重构",
            "bullets": ["平台争夺的是 AI 超级入口"],
            "speaker_notes": "讲第三章。",
            "visual_suggestion": "章节页。",
            "generation_status": "page_map_source",
        },
        {
            "page_num": 5,
            "type": "section",
            "section_title": "第四章",
            "headline": "第四章：品牌会如何分化？从被比价到被指名",
            "bullets": ["从被比价到被指名"],
            "speaker_notes": "讲第四章。",
            "visual_suggestion": "章节页。",
            "generation_status": "page_map_source",
        },
        {
            "page_num": 6,
            "type": "section",
            "section_title": "第五章",
            "headline": "第五章：品牌怎么做？先判断战场，再确定打法",
            "bullets": ["先判断战场，再确定打法"],
            "speaker_notes": "讲第五章。",
            "visual_suggestion": "章节页。",
            "generation_status": "page_map_source",
        },
        {
            "page_num": 7,
            "type": "content",
            "section_title": "第六章",
            "headline": "第六章：企业 90 天行动清单",
            "bullets": ["查 -> 定 -> 建 -> 放"],
            "speaker_notes": "讲第六章。",
            "visual_suggestion": "行动清单。",
            "generation_status": "page_map_source",
        },
        {
            "page_num": 8,
            "type": "ending",
            "section_title": "结语",
            "headline": "结语",
            "bullets": ["在人心里有位置，在平台里有流量，在 AI 里有推荐"],
            "speaker_notes": "收束。",
            "visual_suggestion": "结束页。",
            "generation_status": "page_map_source",
        },
    ]
    model_map = [
        {
            "page_num": idx,
            "type": "content" if idx not in {1, 8} else ("cover" if idx == 1 else "ending"),
            "section_title": "模型规划",
            "headline": f"模型第 {idx} 页",
            "bullets": [
                "第一章：什么变了？决策不再只发生在人脑里",
                "第二章：什么没变？人心仍然是终点",
                "第四章：品牌会如何分化？从被比价到被指名",
                "第五章：品牌怎么做？先判断战场，再确定打法",
                "第六章：企业 90 天行动清单",
            ],
            "speaker_notes": "按原文结构讲。结尾收束到：在人心里有位置，在平台里有流量，在 AI 里有推荐。",
            "visual_suggestion": "内容页。",
            "generation_status": "page_map_model",
        }
        for idx in range(1, 9)
    ]
    contract = {
        "task_type": "source_to_ppt",
        "source_use": "faithful",
        "coverage": "near_complete",
        "compression": "low",
        "depth": "deep",
        "page_budget_policy": "explicit",
        "structure_policy": "source_order",
        "confidence": 0.95,
        "evidence": ["完整还原原文结构"],
    }

    calls = {"model": 0}

    def fake_model_page_map(**_kwargs):
        calls["model"] += 1
        return model_map

    monkeypatch.setattr(content_plan_module, "_source_draft_page_map", lambda **_kwargs: source_draft)
    monkeypatch.setattr(content_plan_module, "_generate_model_page_map", fake_model_page_map)

    result = generate_content_page_map(
        topic="完整保留原文结构，做成 5 页课程",
        documents="第一章\n第二章\n第三章：平台权力正在重构\n第四章\n第五章\n第六章\n结语",
        page_count=8,
        intent_contract=contract,
    )

    assert calls["model"] == 1
    assert result[3]["headline"] == "第三章：平台权力正在重构"


def test_source_preserve_page_map_repairs_misaligned_source_structure_anchor(monkeypatch):
    source_draft = [
        {
            "page_num": idx,
            "type": "content",
            "section_title": "原文章节",
            "headline": f"原文铺垫第 {idx} 页",
            "bullets": ["消费者正在让渡信息处理权", "AI 正在改变信息处理链路"],
            "speaker_notes": _test_talk_notes("消费者正在让渡信息处理权，AI 正在改变信息处理链路。"),
            "visual_suggestion": "内容页。",
            "generation_status": "page_map_source",
        }
        for idx in range(1, 10)
    ] + [
        {
            "page_num": 24,
            "type": "section",
            "section_title": "第三章",
            "headline": "3.6 品牌会被上下夹击",
            "bullets": [
                "上游是掌握超级入口的平台",
                "下游是越来越会比较、会提问的消费者",
            ],
            "speaker_notes": "这一节必须保留，因为它承接第四章。",
            "visual_suggestion": "上下夹击结构图。",
            "generation_status": "page_map_source",
        },
    ]
    model_map = [
        {
            "page_num": idx,
            "type": "content" if idx not in {1, 10} else ("cover" if idx == 1 else "ending"),
            "section_title": "模型规划",
            "headline": f"模型第 {idx} 页",
            "bullets": [
                "消费者正在让渡信息处理权",
                "AI 正在改变信息处理链路",
            ],
            "speaker_notes": _test_talk_notes("消费者让渡信息处理权，AI 改变信息处理链路。"),
            "visual_suggestion": "内容页。",
            "generation_status": "page_map_model",
        }
        for idx in range(1, 11)
    ]
    contract = {
        "task_type": "source_to_ppt",
        "source_use": "faithful",
        "coverage": "near_complete",
        "compression": "low",
        "depth": "deep",
        "page_budget_policy": "explicit",
        "structure_policy": "source_order",
        "confidence": 0.95,
        "evidence": ["完整还原原文结构和金句"],
    }

    monkeypatch.setattr(content_plan_module, "_source_draft_page_map", lambda **_kwargs: source_draft)
    monkeypatch.setattr(content_plan_module, "_generate_model_page_map", lambda **_kwargs: model_map)

    result = generate_content_page_map(
        topic="完整保留原文结构，做成 10 页课程",
        documents="3.6 品牌会被上下夹击",
        page_count=10,
        intent_contract=contract,
    )

    assert content_plan_module._compact_coverage_text("3.6 品牌会被上下夹击") in content_plan_module._page_map_coverage_text(result)
    assert len(result) == 10


def test_source_preserve_page_map_repairs_non_keyword_final_section(monkeypatch):
    source_draft = [
        {
            "page_num": 1,
            "type": "cover",
            "section_title": "封面",
            "headline": "增长训练营",
            "bullets": [],
            "speaker_notes": "开场。",
            "visual_suggestion": "封面。",
            "generation_status": "page_map_source",
        },
        {
            "page_num": 2,
            "type": "content",
            "section_title": "开场",
            "headline": "增长不是拉新，而是连续改善决策",
            "bullets": ["先找到阻碍用户行动的关键摩擦"],
            "speaker_notes": _test_talk_notes("增长开场要说明关键摩擦如何阻碍用户行动。"),
            "visual_suggestion": "内容页。",
            "generation_status": "page_map_source",
        },
        {
            "page_num": 3,
            "type": "content",
            "section_title": "方法",
            "headline": "把实验做成组织动作",
            "bullets": ["每个实验都要对应一个业务假设"],
            "speaker_notes": _test_talk_notes("方法部分要说明实验必须对应业务假设。"),
            "visual_suggestion": "内容页。",
            "generation_status": "page_map_source",
        },
        {
            "page_num": 4,
            "type": "content",
            "section_title": "案例",
            "headline": "从单点优化到系统增长",
            "bullets": ["增长团队要同时看转化、留存和复购"],
            "speaker_notes": _test_talk_notes("案例部分要说明增长团队同时看转化、留存和复购。"),
            "visual_suggestion": "内容页。",
            "generation_status": "page_map_source",
        },
        {
            "page_num": 5,
            "type": "ending",
            "section_title": "复盘与下一步",
            "headline": "复盘与下一步：从今天开始的三件事",
            "bullets": ["把今天的三个动作写进下周例会，而不是停留在启发里"],
            "speaker_notes": _test_talk_notes("下一步要把三个动作写进下周例会。"),
            "visual_suggestion": "结束页。",
            "generation_status": "page_map_source",
        },
    ]
    model_map = [
        {
            "page_num": idx,
            "type": "content" if idx not in {1, 5} else ("cover" if idx == 1 else "ending"),
            "section_title": "模型章节",
            "headline": f"模型只讲前半段第 {idx} 页",
            "bullets": ["先找到阻碍用户行动的关键摩擦", "每个实验都要对应一个业务假设"],
            "speaker_notes": _test_talk_notes("前半段说明关键摩擦和业务假设。"),
            "visual_suggestion": "内容页。",
            "generation_status": "page_map_model",
        }
        for idx in range(1, 6)
    ]
    contract = {
        "task_type": "teaching_deck",
        "source_use": "faithful",
        "coverage": "near_complete",
        "compression": "low",
        "depth": "deep",
        "page_budget_policy": "explicit",
        "structure_policy": "source_order",
        "confidence": 0.95,
        "evidence": ["逐章讲清楚，不要遗漏"],
    }

    monkeypatch.setattr(content_plan_module, "_source_draft_page_map", lambda **_kwargs: source_draft)
    monkeypatch.setattr(content_plan_module, "_generate_model_page_map", lambda **_kwargs: model_map)

    result = generate_content_page_map(
        topic="逐章讲清楚，不要遗漏，做成 5 页课程",
        documents="复盘与下一步：从今天开始的三件事",
        page_count=5,
        intent_contract=contract,
    )

    assert result[4]["headline"] == "复盘与下一步：从今天开始的三件事"


def test_page_map_merge_restores_chinese_gold_sentence_when_model_paraphrases():
    source_ref = {
        "source_document": "brand.md",
        "source_page_num": 2,
        "source_type": "markdown",
        "reason": "source_draft",
    }
    source_draft = [
        {
            "page_num": 1,
            "type": "cover",
            "section_title": "封面",
            "headline": "品牌增长课",
            "bullets": [],
            "speaker_notes": "开场。",
            "visual_suggestion": "封面。",
            "source_refs": [],
            "generation_status": "page_map_source",
        },
        {
            "page_num": 2,
            "type": "content",
            "section_title": "核心判断",
            "headline": "真正的品牌，是在人心里被点名",
            "bullets": ["真正的品牌，是在人心里被点名，而不是在货架上被看见。", "品牌资产必须能同时被人和 AI 识别。"],
            "speaker_notes": "讲核心判断。",
            "visual_suggestion": "内容页。",
            "source_refs": [source_ref],
            "generation_status": "page_map_source",
        },
    ]
    model_map = [
        source_draft[0],
        {
            "page_num": 2,
            "type": "content",
            "section_title": "核心判断",
            "headline": "品牌要被消费者主动选择",
            "bullets": ["品牌需要在消费者心智中形成明确位置。", "也要让 AI 理解自己的差异化证据。"],
            "speaker_notes": "讲核心判断。",
            "visual_suggestion": "内容页。",
            "source_refs": [source_ref],
            "generation_status": "page_map_model",
        },
    ]

    merged = content_plan_module._merge_page_map_with_source_draft(
        model_map,
        source_draft,
        target_count=2,
    )

    assert "真正的品牌，是在人心里被点名，而不是在货架上被看见。" in merged[1]["bullets"]
    assert merged[1]["generation_status"] == "page_map_model_with_source_body"


def test_source_structure_checklist_uses_uploaded_document_headings(monkeypatch):
    documents = """# AI 时代品牌课

## 第一章：什么变了？决策不再只发生在人脑里
### 1.1 消费者正在让渡信息处理权
### 1.2 消费者决策旅程正在被重写

## 结语
- 在人心里有位置，在平台里有流量，在 AI 里有推荐
"""
    source_draft = [
        {
            "page_num": 1,
            "type": "cover",
            "section_title": "封面",
            "headline": "AI 时代品牌课",
            "bullets": [],
            "speaker_notes": "开场。",
            "visual_suggestion": "封面。",
            "generation_status": "page_map_source",
        },
        {
            "page_num": 2,
            "type": "section",
            "section_title": "第一章",
            "headline": "第一章：什么变了？决策不再只发生在人脑里",
            "bullets": ["消费者正在让渡信息处理权"],
            "speaker_notes": _test_talk_notes("第一章包括 1.1 信息处理权和 1.2 决策旅程重写。"),
            "visual_suggestion": "章节页。",
            "generation_status": "page_map_source",
        },
        {
            "page_num": 3,
            "type": "ending",
            "section_title": "结语",
            "headline": "结语",
            "bullets": ["在人心里有位置，在平台里有流量，在 AI 里有推荐"],
            "speaker_notes": "收束。",
            "visual_suggestion": "结束页。",
            "generation_status": "page_map_source",
        },
    ]
    captured = {"checklist": ""}

    def fake_model_page_map(**kwargs):
        captured["checklist"] = kwargs.get("source_structure_checklist") or ""
        return [
            {
                "page_num": 1,
                "type": "cover",
                "section_title": "封面",
                "headline": "AI 时代品牌课",
                "bullets": [],
                "speaker_notes": "开场。",
                "visual_suggestion": "封面。",
                "generation_status": "page_map_model",
            },
            {
                "page_num": 2,
                "type": "content",
                "section_title": "第一章",
                "headline": "第一章：什么变了？决策不再只发生在人脑里",
                "bullets": ["1.1 消费者正在让渡信息处理权", "1.2 消费者决策旅程正在被重写"],
                "speaker_notes": _test_talk_notes("第一章先讲 1.1 信息处理权，再讲 1.2 决策旅程重写。"),
                "visual_suggestion": "内容页。",
                "generation_status": "page_map_model",
            },
            {
                "page_num": 3,
                "type": "ending",
                "section_title": "结语",
                "headline": "结语",
                "bullets": ["在人心里有位置，在平台里有流量，在 AI 里有推荐"],
                "speaker_notes": "收束。",
                "visual_suggestion": "结束页。",
                "generation_status": "page_map_model",
            },
        ]

    monkeypatch.setattr(content_plan_module, "_source_draft_page_map", lambda **_kwargs: source_draft)
    monkeypatch.setattr(content_plan_module, "_generate_model_page_map", fake_model_page_map)

    generate_content_page_map(
        topic="保留原文结构，做成 3 页课程",
        documents=documents,
        page_count=3,
        intent_contract={
            "task_type": "source_to_ppt",
            "source_use": "faithful",
            "coverage": "near_complete",
            "compression": "low",
            "depth": "deep",
            "page_budget_policy": "explicit",
            "structure_policy": "source_order",
            "confidence": 0.95,
            "evidence": ["保留原文结构"],
        },
    )

    assert "1.2 消费者决策旅程正在被重写" in captured["checklist"]
    assert "结语" in captured["checklist"]


def test_page_map_rejects_duplicate_substantive_headlines(monkeypatch):
    model_map = [
        {
            "page_num": 1,
            "type": "cover",
            "section_title": "封面",
            "headline": "AI 时代品牌课",
            "bullets": [],
            "speaker_notes": "开场。",
            "visual_suggestion": "封面。",
            "generation_status": "page_map_model",
        },
        {
            "page_num": 2,
            "type": "content",
            "section_title": "任务语言",
            "headline": "品牌是在给大模型写说明书",
            "bullets": ["把商品语言翻译成任务语言"],
            "speaker_notes": "讲任务语言。",
            "visual_suggestion": "内容页。",
            "generation_status": "page_map_model",
        },
        {
            "page_num": 3,
            "type": "content",
            "section_title": "任务语言",
            "headline": "品牌是在给大模型写说明书",
            "bullets": ["从大需求到细分赛道，再到长尾任务"],
            "speaker_notes": "讲长尾任务。",
            "visual_suggestion": "内容页。",
            "generation_status": "page_map_model",
        },
        {
            "page_num": 4,
            "type": "ending",
            "section_title": "结尾",
            "headline": "结语",
            "bullets": ["在人心里有位置，在平台里有流量，在 AI 里有推荐"],
            "speaker_notes": "收束。",
            "visual_suggestion": "结束页。",
            "generation_status": "page_map_model",
        },
    ]

    monkeypatch.setattr(content_plan_module, "_generate_model_page_map", lambda **_kwargs: model_map)
    monkeypatch.setattr(content_plan_module, "_source_draft_page_map", lambda **_kwargs: [])

    with pytest.raises(ValueError) as exc_info:
        generate_content_page_map(
            topic="做成 4 页课程",
            documents="品牌是在给大模型写说明书",
            page_count=4,
        )

    assert "duplicate headlines" in str(exc_info.value)


def test_page_map_without_bullets_keeps_source_draft_body(monkeypatch):
    documents = """# 面向AI时代，企业营与销该如何布局

## 模块一：道

- ChatGPT 达到 1 亿规模只用了 2 个月
- AI 正在成为消费决策的新中介

## 模块二：术

- 先打动 AI，再让客户拍板
- 内容要同时给人看，也给 AI 读
"""

    class FakeMessage:
        content = """P1｜cover｜封面｜面向AI时代，企业营与销该如何布局
备注：开场定调。
视觉：课程主视觉

P2｜content｜道｜AI时代，客户已经变了
备注：直接用数据和事实砸。
视觉：全屏数据可视化
来源：模块一

P3｜content｜道｜AI 正在成为消费决策的新中介
备注：展开消费者决策工具变化。
视觉：决策链路变化图
来源：模块一

P4｜content｜术｜内容要同时给人看，也给 AI 读
备注：展开内容生产的新要求。
视觉：人机双通道信息结构
来源：模块二

P5｜ending｜总结｜下一步
备注：收束。
视觉：总结页"""

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    seen_prompts: list[str] = []

    class FakeCompletions:
        def create(self, **_kwargs):
            seen_prompts.append(_kwargs["messages"][1]["content"])
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(content_plan_module, "get_llm_client", lambda: FakeClient())
    monkeypatch.setattr(content_plan_module.get_knowledge_augmenter(), "augment", lambda *_args, **_kwargs: "")

    outline = generate_content_plan(
        topic="做一份 5 页课程",
        documents=documents,
        page_count=5,
    )

    bodies = "\n".join(str(page["text_content"].get("body") or "") for page in outline)
    assert "系统预生成的正文底稿" in seen_prompts[0]
    assert "AI 正在成为消费决策的新中介" in bodies or "先打动 AI，再让客户拍板" in bodies
    assert any(str(page["text_content"].get("body") or "").strip() for page in outline[1:])


def test_partial_page_map_does_not_save_skeleton_placeholders(monkeypatch):
    partial_map = "\n\n".join(
        [
            "P1｜cover｜封面｜达尔文进化论\n备注：开场。",
            *[
                f"P{idx}｜content｜达尔文｜第 {idx} 页真实内容\n- 真实要点 {idx}\n备注：讲解第 {idx} 页。"
                for idx in range(2, 7)
            ],
        ]
    )

    extension_pages = [
        {
            "page_num": idx,
            "type": "content",
            "section_title": "达尔文",
            "text_content": {
                "headline": f"第 {idx} 页补齐内容",
                "subhead": "",
                "body": f"补齐正文 {idx}",
            },
            "speaker_notes": f"补齐讲解 {idx}",
            "visual_suggestion": f"补齐视觉 {idx}",
            "source_refs": [],
        }
        for idx in range(7, 17)
    ]

    class FakeMessage:
        def __init__(self, content):
            self.content = content

    class FakeChoice:
        def __init__(self, content):
            self.message = FakeMessage(content)

    class FakeResponse:
        def __init__(self, content):
            self.choices = [FakeChoice(content)]

    class FakeCompletions:
        def create(self, **kwargs):
            prompt = kwargs["messages"][1]["content"]
            if "逐页内容地图" in prompt:
                return FakeResponse(partial_map)
            assert "只续写第 7 页到第 16 页" in prompt
            return FakeResponse(json.dumps(extension_pages, ensure_ascii=False))

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(content_plan_module, "get_llm_client", lambda: FakeClient())
    monkeypatch.setattr(content_plan_module.get_knowledge_augmenter(), "augment", lambda *_args, **_kwargs: "")

    with pytest.raises(ValueError) as exc_info:
        generate_content_plan(
            topic="做一份 16 页达尔文进化论课程",
            page_count=16,
        )

    assert "failed before producing usable model pages" in str(exc_info.value)


def test_page_map_generation_scales_output_budget_for_long_decks(monkeypatch):
    captured_kwargs: dict = {}

    class FakeMessage:
        content = "P1｜cover｜封面｜AI 时代消费者决策路径与品牌策略\n备注：开场。\n视觉：封面"

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeCompletions:
        def create(self, **kwargs):
            captured_kwargs.update(kwargs)
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(content_plan_module, "get_llm_client", lambda: FakeClient())

    content_plan_module._generate_model_page_map(
        topic="做成 50 页课程",
        audience="通用受众",
        documents="AI 时代消费者决策路径与品牌策略",
        page_goal_text="优先生成约 50 页。",
        target_count=50,
        min_pages=40,
        max_pages=60,
    )

    assert captured_kwargs["max_tokens"] >= 30000


def test_source_draft_page_map_does_not_create_skeleton_without_documents():
    source_draft = content_plan_module._source_draft_page_map(
        topic="做一份 16 页达尔文进化论课程",
        documents="",
        target_count=16,
        min_pages=14,
        max_pages=18,
    )

    assert source_draft == []


def test_skeleton_source_draft_is_not_sent_to_model_prompt(monkeypatch):
    page_map = "\n\n".join(
        [
            "P1｜cover｜封面｜达尔文进化论\n备注：开场。",
            *[
                f"P{idx}｜content｜达尔文｜第 {idx} 页内容\n- 真实要点 {idx}\n备注：讲稿内容：真实要点 {idx} 是本页需要讲出的核心内容。"
                for idx in range(2, 17)
            ],
        ]
    )

    class FakeMessage:
        def __init__(self, content):
            self.content = content

    class FakeChoice:
        def __init__(self, content):
            self.message = FakeMessage(content)

    class FakeResponse:
        def __init__(self, content):
            self.choices = [FakeChoice(content)]

    class FakeCompletions:
        def create(self, **kwargs):
            prompt = kwargs["messages"][1]["content"]
            assert "【系统预生成的正文底稿】\n无" in prompt
            assert "本页已先放入长篇 PPT 结构中" not in prompt
            assert "占位备注" not in prompt
            return FakeResponse(page_map)

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(content_plan_module, "get_llm_client", lambda: FakeClient())

    result = generate_content_page_map(
        topic="做一份 16 页达尔文进化论课程",
        page_count=16,
    )

    assert len(result) == 16


def test_generate_deck_blueprint_uses_global_page_ranges(monkeypatch):
    class FakeMessage:
        content = "## 全局蓝图\n- P1-P4：开场\n- P5-P80：主体与收束"

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeCompletions:
        def create(self, **kwargs):
            prompt = kwargs["messages"][1]["content"]
            assert "课程型 PPT" in prompt
            assert "60-80 页" in prompt
            assert "P1-P80" in prompt
            assert "不要把原文机械切成" in prompt
            assert "只输出可读的中文 Markdown 蓝图" in prompt
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(content_plan_module, "get_llm_client", lambda: FakeClient())

    blueprint = _generate_deck_blueprint(
        topic="做成 60-80 页课程",
        audience="企业老板",
        documents="课程材料",
        min_pages=60,
        max_pages=80,
        target_count=80,
    )

    assert "P1-P4" in blueprint


def test_short_explicit_range_outline_is_extended(monkeypatch):
    class FakeMessage:
        content = json.dumps([
            {
                "page_num": 2,
                "type": "content",
                "section_title": "课程主体",
                "text_content": {"headline": "补充一", "subhead": "", "body": "扩展讲解"},
                "speaker_notes": "",
                "visual_suggestion": "",
                "source_refs": [],
            },
            {
                "page_num": 3,
                "type": "content",
                "section_title": "课程主体",
                "text_content": {"headline": "补充二", "subhead": "", "body": "扩展案例"},
                "speaker_notes": "",
                "visual_suggestion": "",
                "source_refs": [],
            },
            {
                "page_num": 4,
                "type": "ending",
                "section_title": "",
                "text_content": {"headline": "收束", "subhead": "", "body": ""},
                "speaker_notes": "",
                "visual_suggestion": "",
                "source_refs": [],
            },
        ])

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeCompletions:
        def create(self, **kwargs):
            prompt = kwargs["messages"][1]["content"]
            assert "只续写第 2 页到第 4 页" in prompt
            assert "【全局蓝图（必须遵守）】" in prompt
            assert "P1-P4：整体课程结构" in prompt
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(content_plan_module, "get_llm_client", lambda: FakeClient())

    outline = [
        {
            "page_num": 1,
            "type": "cover",
            "section_title": "",
            "text_content": {"headline": "课程封面", "subhead": "", "body": ""},
            "speaker_notes": "",
            "visual_suggestion": "",
            "source_refs": [],
        },
        {
            "page_num": 2,
            "type": "ending",
            "section_title": "",
            "text_content": {"headline": "旧结尾", "subhead": "", "body": ""},
            "speaker_notes": "",
            "visual_suggestion": "",
            "source_refs": [],
        },
    ]

    extended = _extend_outline_to_target_count(
        outline,
        topic="做成 4 页课程",
        documents="课程材料",
        deck_blueprint="P1-P4：整体课程结构",
        target_count=4,
        min_pages=4,
        max_pages=4,
    )

    assert [page["page_num"] for page in extended] == [1, 2, 3, 4]
    assert extended[-1]["type"] == "ending"
    assert extended[1]["text_content"]["headline"] == "补充一"


def test_long_deck_outline_generates_in_blueprint_chunks(monkeypatch):
    calls: list[str] = []

    def make_pages(start: int, end: int):
        pages = []
        for page_num in range(start, end + 1):
            pages.append({
                "page_num": page_num,
                "type": "content",
                "section_title": "课程主体",
                "text_content": {"headline": f"第 {page_num} 页", "subhead": "", "body": "讲解内容"},
                "speaker_notes": "",
                "visual_suggestion": "",
                "source_refs": [],
            })
        return pages

    class FakeMessage:
        def __init__(self, content):
            self.content = content

    class FakeChoice:
        def __init__(self, content):
            self.message = FakeMessage(content)

    class FakeResponse:
        def __init__(self, content):
            self.choices = [FakeChoice(content)]

    class FakeCompletions:
        def create(self, **kwargs):
            prompt = kwargs["messages"][1]["content"]
            calls.append(prompt)
            assert kwargs["timeout"] == 90.0
            assert "不要使用“续2”“续3”" in prompt
            assert "封面标题必须使用真实课程主题" in prompt
            assert "演讲备注必须具体" in prompt
            assert "演讲备注必须先写出这一页需要讲什么" in prompt
            assert "text_content.body 是页面卡片/PPT 上可见的正文区域" in prompt
            assert "text_content.body 必须写得言之有物" in prompt
            assert "PPT 应当准确体现当前用户的真实意图" in prompt
            assert "label: content" in prompt
            assert "headline 不得与【已生成页面摘要】中的任何 headline 重复" in prompt
            match = re.search(r"只生成第 (\d+) 页到第 (\d+) 页", prompt)
            assert match
            start = int(match.group(1))
            end = int(match.group(2))
            assert end - start + 1 <= content_plan_module.LONG_DECK_CHUNK_SIZE
            assert "【已生成页面摘要】" in prompt
            return FakeResponse(json.dumps(make_pages(start, end)))

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(content_plan_module, "get_llm_client", lambda: FakeClient())

    outline = _generate_outline_from_blueprint_in_chunks(
        topic="做成 14 页课程",
        documents="课程材料",
        deck_blueprint="P1-P14：整体课程结构",
        target_count=14,
        min_pages=14,
        max_pages=14,
    )

    assert len(outline) == 14
    assert [page["page_num"] for page in outline] == list(range(1, 15))
    assert outline[0]["type"] == "cover"
    assert outline[-1]["type"] == "ending"
    assert len(calls) == 7


def test_long_deck_chunk_rejects_model_output_that_puts_content_only_in_notes(monkeypatch):
    class FakeMessage:
        content = json.dumps([
            {
                "page_num": 1,
                "type": "cover",
                "section_title": "封面",
                "text_content": {"headline": "课程封面", "subhead": "", "body": ""},
                "speaker_notes": "开场。",
                "visual_suggestion": "封面。",
                "source_refs": [],
            },
            {
                "page_num": 2,
                "type": "content",
                "section_title": "课程主体",
                "text_content": {"headline": "预算要处理三条增长曲线", "subhead": "", "body": ""},
                "speaker_notes": "先让主营业务有造血能力，再用主营业务赚的钱布局新业务，不要三条曲线一起烧钱。",
                "visual_suggestion": "三横条递进图。",
                "source_refs": [],
            },
        ])

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeCompletions:
        def create(self, **kwargs):
            prompt = kwargs["messages"][1]["content"]
            assert "text_content.body 是页面卡片/PPT 上可见的正文区域" in prompt
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(content_plan_module, "get_llm_client", lambda: FakeClient())

    try:
        _generate_outline_from_blueprint_in_chunks(
            topic="做成 3 页课程",
            documents="课程材料",
            deck_blueprint="P1-P3：整体课程结构",
            target_count=3,
            min_pages=3,
            max_pages=3,
        )
    except ValueError as exc:
        assert "正文为空" in str(exc)
        assert "speaker_notes" in str(exc)
    else:
        raise AssertionError("long deck generation must reject chunks whose visible body is empty")


def test_long_deck_chunk_rejects_duplicate_content_headline_against_existing_pages(monkeypatch):
    existing = [
        {
            "page_num": 34,
            "type": "content",
            "section_title": "营销管理",
            "text_content": {"headline": "定价必须组织化", "subhead": "", "body": "已有正文"},
            "speaker_notes": "",
            "visual_suggestion": "",
            "source_refs": [],
        }
    ]
    chunk = [
        {
            "page_num": 35,
            "type": "content",
            "section_title": "营销管理",
            "text_content": {"headline": "定价必须组织化", "subhead": "", "body": "新的正文"},
            "speaker_notes": "",
            "visual_suggestion": "",
            "source_refs": [],
        }
    ]

    try:
        content_plan_module._assert_long_deck_chunk_contract(chunk, start_page=35, end_page=36, existing_pages=existing)
    except ValueError as exc:
        assert "标题重复" in str(exc)
        assert "不同角度" in str(exc)
    else:
        raise AssertionError("long deck generation must reject duplicate content headlines")


def test_final_content_plan_dedupes_duplicate_headlines_when_body_differs():
    outline = [
        {
            "page_num": 1,
            "type": "content",
            "section_title": "品牌营销课",
            "text_content": {
                "headline": "混沌 AI 院，为什么要讲一门品牌营销课？",
                "subhead": "",
                "body": "- AI 时代的品牌课不是工具清单，而是重新定义品牌资产的入口。",
            },
            "speaker_notes": "",
            "visual_suggestion": "",
            "source_refs": [],
        },
        {
            "page_num": 2,
            "type": "content",
            "section_title": "品牌营销课",
            "text_content": {
                "headline": "混沌 AI 院，为什么要讲一门品牌营销课？",
                "subhead": "",
                "body": "- 课程要回答企业如何从流量运营转向 AI 可理解的证据系统。",
            },
            "speaker_notes": "",
            "visual_suggestion": "",
            "source_refs": [],
        },
    ]

    fixed = content_plan_module._dedupe_content_headlines(outline)

    assert content_plan_module._duplicate_content_headline_pages(fixed) == []
    assert fixed[1]["text_content"]["headline"] == "课程要回答企业如何从流量运营转向 AI 可理解的证据系统"


def test_final_content_plan_keeps_duplicate_failure_when_body_is_same():
    outline = [
        {
            "page_num": 1,
            "type": "content",
            "section_title": "品牌营销课",
            "text_content": {
                "headline": "定价必须组织化",
                "subhead": "",
                "body": "同一段正文",
            },
            "speaker_notes": "",
            "visual_suggestion": "",
            "source_refs": [],
        },
        {
            "page_num": 2,
            "type": "content",
            "section_title": "品牌营销课",
            "text_content": {
                "headline": "定价必须组织化",
                "subhead": "",
                "body": "同一段正文",
            },
            "speaker_notes": "",
            "visual_suggestion": "",
            "source_refs": [],
        },
    ]

    fixed = content_plan_module._dedupe_content_headlines(outline)

    assert content_plan_module._duplicate_content_headline_pages(fixed)


def test_page_map_finalization_repairs_unrecoverable_duplicate_headlines_from_source():
    documents = """# AI 时代品牌课

## 开场
- AI 时代的品牌课不是工具清单，而是重新定义品牌资产的入口。
- 品牌要同时成为 AI 的首选和人的首选。

## 两张图看清这个时代
- 经典营销时代争夺注意力。
- 移动互联网时代争夺匹配效率。
- AI 时代争夺推荐资格。

## 企业 90 天行动
- 查：AI 和平台现在如何描述你。
- 定：找到品牌那个 1。
- 建：搭建结构化证据资产。
"""
    duplicate_outline = [
        {
            "page_num": 1,
            "type": "cover",
            "section_title": "封面",
            "text_content": {"headline": "AI 时代品牌课", "subhead": "", "body": ""},
            "speaker_notes": "",
            "visual_suggestion": "",
            "source_refs": [],
        },
        {
            "page_num": 2,
            "type": "content",
            "section_title": "开场",
            "text_content": {"headline": "混沌 AI 院，为什么要讲一门品牌营销课？", "subhead": "", "body": "重复正文"},
            "speaker_notes": "",
            "visual_suggestion": "",
            "source_refs": [],
        },
        {
            "page_num": 3,
            "type": "content",
            "section_title": "开场",
            "text_content": {"headline": "混沌 AI 院，为什么要讲一门品牌营销课？", "subhead": "", "body": "重复正文"},
            "speaker_notes": "",
            "visual_suggestion": "",
            "source_refs": [],
        },
        {
            "page_num": 4,
            "type": "content",
            "section_title": "时代线",
            "text_content": {"headline": "两张图看清这个时代", "subhead": "", "body": "时代线正文"},
            "speaker_notes": "",
            "visual_suggestion": "",
            "source_refs": [],
        },
        {
            "page_num": 5,
            "type": "ending",
            "section_title": "结尾",
            "text_content": {"headline": "行动收束", "subhead": "", "body": ""},
            "speaker_notes": "",
            "visual_suggestion": "",
            "source_refs": [],
        },
    ]
    contract = {
        "task_type": "source_to_ppt",
        "source_use": "faithful",
        "coverage": "balanced",
        "compression": "medium",
        "depth": "standard",
        "page_budget_policy": "explicit",
        "structure_policy": "source_order",
        "confidence": 0.8,
        "evidence": ["5 页"],
    }
    job = content_plan_module.ContentPlanJob(
        topic="做成 5 页 PPT",
        audience="通用受众",
        page_count=5,
        min_pages=5,
        max_pages=5,
        documents=documents,
        has_docs=True,
        requested_page_range=None,
        strict_page_count=True,
        allow_expanded_outline_override=False,
        intent_contract=contract,
        ppt_sources=[],
        planning_policy={},
        mode="default",
    )

    finalized = content_plan_module._finalize_generated_content_plan(
        duplicate_outline,
        job,
        strategy=content_plan_module.CONTENT_PLAN_STRATEGY_PAGE_MAP,
    )

    assert len(finalized) == 5
    assert content_plan_module._duplicate_content_headline_pages(finalized) == []
    assert "AI 时代的品牌课不是工具清单" in json.dumps(finalized, ensure_ascii=False)


def test_source_chunk_headline_skips_short_command_fragments():
    headline = content_plan_module._source_chunk_headline(
        "3.1 平台争夺的是 AI 超级入口",
        [
            "帮我订",
            "帮我买",
            "帮我安排",
            "未来所有平台都想做同一件事：",
            "让用户有事先问我。",
        ],
        1,
    )

    assert headline == "未来所有平台都想做同一件事："


def test_source_draft_splits_dense_single_lines_before_quality_gate():
    documents = """# AI时代消费者决策路径与品牌策略

## 两张图看清这个时代
平台与消费者：消费者想要更省心的选择，平台想要把选择留在自己这里完成。平台越能替消费者省心，越有能力影响消费者看见什么、相信什么、买什么。

## 第二章：什么没变？人心仍然是终点
AI 改变了消费者处理信息的方式，但没有改写两件事：人为什么买，最后怎么判断。

## 3.1 平台争夺的是 AI 超级入口
帮我订
帮我买
帮我安排
未来所有平台都想做同一件事：
让用户有事先问我。

## 3.2 字节：从信息找人，到需求找货
品牌在字节生态里，不能只做“被刷到”，还要做“被问到”。短视频、直播、达人、测评、商品卡、体验分、价格、库存和评价，都会成为豆包理解品牌的材料。

## 3.4 腾讯：从关系连接，到服务调用
这件事的重点是：小程序不再只是一个页面，而是会变成可被 AI 调用的服务单元。品牌不能只想“我有没有小程序”，而要想商品、门店、库存、会员权益、客服和支付，能不能被微信 AI 准确调用。

## 第六章：企业 90 天行动清单
建 / 搭建结构化证据资产 / 建好体系：产品信息、适用/不适用人群、真实评价、第三方资质、客户案例、FAQ、竞品对比和风险说明
放 / 用 AI 放大核心价值 / 用 AI 和 AIGC 把品牌的核心价值、典型场景、用户证据和选择理由，规模化转化成内容、销售话术、客服问答
"""
    contract = {
        "task_type": "source_to_ppt",
        "source_use": "faithful",
        "coverage": "near_complete",
        "compression": "medium",
        "depth": "deep",
        "page_budget_policy": "explicit",
        "structure_policy": "source_order",
        "confidence": 0.9,
        "evidence": ["10 页左右"],
    }

    page_map = content_plan_module._source_draft_page_map(
        topic="做成 10 页左右的 ppt",
        documents=documents,
        target_count=10,
        min_pages=8,
        max_pages=12,
        intent_contract=contract,
    )
    outline = content_plan_from_page_map(page_map, source_context=documents)
    outline = content_plan_module._dedupe_content_headlines(outline)

    headlines = [page["text_content"]["headline"] for page in outline]
    assert "帮我安排" not in headlines
    assert any("未来所有平台都想做同一件事" in json.dumps(page, ensure_ascii=False) for page in outline)
    assert content_plan_module._thin_required_content_body_pages(outline) == []
    assert content_plan_module._duplicate_content_headline_pages(outline) == []


def test_source_speaker_notes_prioritize_talk_content_over_delivery_cues():
    notes = content_plan_module._source_speaker_notes_from_lines(
        headline="品牌要进入 AI 的答案链路",
        lines=[
            "用户不再只在搜索框里找信息，而是把比较、判断和推荐交给 AI。",
            "品牌需要准备可被引用的证据：产品差异、真实案例、第三方评价和可核验数据。",
            "如果这些证据缺席，AI 很难把品牌推荐成合适答案。",
        ],
    )

    assert notes.startswith("讲稿内容：")
    assert "用户不再只在搜索框里找信息" in notes
    assert "产品差异、真实案例、第三方评价" in notes
    assert "讲法：先用" not in notes


def test_finalize_rejects_thin_model_page_map_body():
    outline = [
        {
            "page_num": 1,
            "type": "cover",
            "text_content": {"headline": "AI 品牌课", "body": ""},
            "generation_status": "page_map_model",
        },
        {
            "page_num": 2,
            "type": "content",
            "text_content": {"headline": "消费者决策正在变化", "body": "AI 正在改变消费者决策"},
            "speaker_notes": "从购买前研究、比较和判断三个动作展开讲清楚。",
            "generation_status": "page_map_model",
        },
        {
            "page_num": 3,
            "type": "content",
            "text_content": {"headline": "品牌需要新的证据资产", "body": "品牌要建立可被 AI 调用的证据"},
            "speaker_notes": "先讲证据资产，再转向企业应该如何补齐。",
            "generation_status": "page_map_model",
        },
        {
            "page_num": 4,
            "type": "ending",
            "text_content": {"headline": "行动收束", "body": ""},
            "generation_status": "page_map_model",
        },
    ]
    job = content_plan_module.ContentPlanJob(
        topic="做成 4 页 PPT",
        audience="通用受众",
        page_count=4,
        min_pages=4,
        max_pages=4,
        documents="",
        has_docs=False,
        requested_page_range=None,
        strict_page_count=True,
        allow_expanded_outline_override=False,
        intent_contract={
            "task_type": "source_to_ppt",
            "source_use": "optimized",
            "coverage": "balanced",
            "compression": "medium",
            "depth": "standard",
            "page_budget_policy": "explicit",
            "structure_policy": "reorganize",
            "confidence": 0.8,
            "evidence": ["4 页"],
        },
        ppt_sources=[],
        planning_policy={},
    )

    with pytest.raises(ValueError, match="信息量过薄"):
        content_plan_module._finalize_generated_content_plan(
            outline,
            job,
            strategy=content_plan_module.CONTENT_PLAN_STRATEGY_PAGE_MAP,
        )


def test_long_deck_single_chunk_normalizes_page_numbers(monkeypatch):
    class FakeMessage:
        content = json.dumps([
            {
                "page_num": 99,
                "type": "ending",
                "section_title": "课程主体",
                "text_content": {"headline": "第一组", "subhead": "", "body": "讲解内容"},
                "speaker_notes": "",
                "visual_suggestion": "",
                "source_refs": [],
            },
            {
                "page_num": 100,
                "type": "ending",
                "section_title": "课程主体",
                "text_content": {"headline": "第二组", "subhead": "", "body": "讲解内容"},
                "speaker_notes": "",
                "visual_suggestion": "",
                "source_refs": [],
            },
        ])

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeCompletions:
        def create(self, **kwargs):
            assert kwargs["timeout"] == 60.0
            prompt = kwargs["messages"][1]["content"]
            assert "只生成第 13 页到第 14 页" in prompt
            assert "【本组已有骨架】" in prompt
            assert "不要使用“续2”“续3”" in prompt
            assert "演讲备注必须具体" in prompt
            assert "演讲备注必须先写出这一页需要讲什么" in prompt
            assert "text_content.body 是页面卡片/PPT 上可见的正文区域" in prompt
            assert "text_content.body 必须写得言之有物" in prompt
            assert "PPT 应当准确体现当前用户的真实意图" in prompt
            assert "label: content" in prompt
            assert "headline 不得与【已生成页面摘要】中的任何 headline 重复" in prompt
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(content_plan_module, "get_llm_client", lambda: FakeClient())

    chunk = generate_long_deck_outline_chunk(
        topic="做成 14 页课程",
        documents="课程材料",
        deck_blueprint="P1-P14：整体课程结构",
        existing_outline=[],
        skeleton_chunk=build_long_deck_skeleton(topic="课程", target_count=14, min_pages=14, max_pages=14)[12:14],
        target_count=14,
        start_page=13,
        end_page=14,
    )

    assert [page["page_num"] for page in chunk] == [13, 14]
    assert chunk[0]["type"] == "content"
    assert chunk[1]["type"] == "ending"
    assert all(page["generation_status"] == "drafted" for page in chunk)


def test_very_long_material_uses_synthesis_mode():
    documents = "完整书稿内容\n" * 8000

    assert _document_preservation_mode(documents, "帮我做成 PPT") == "synthesis"
    assert "材料过长" in _document_preservation_policy(documents, "帮我做成 PPT")


def test_pptgod_markdown_export_parses_back_to_pages():
    documents = """
--- 文档: 非凡产研战略框架-内容规划.md ---
# 非凡产研战略框架 - 内容规划导出

<!--
PPTGOD_EXPORT_KIND: content_plan_markdown
-->

---
<!-- PPTGOD_PAGE_START page_num=1 type=cover section_title="" -->
## P1 · cover

### 标题

非凡产研战略框架

### 副标题

成就你的非凡

### 正文

<!-- 留空 -->

### 备注

封面备注

<!-- PPTGOD_PAGE_END page_num=1 -->

---
<!-- PPTGOD_PAGE_START page_num=2 type=content section_title="使命愿景" -->
## P2 · content · 使命愿景

### 标题

服务 AI 创业者

### 副标题

使命与愿景

### 正文

- 数据
- 资源

### 备注

正文备注

<!-- PPTGOD_PAGE_END page_num=2 -->
"""

    pages = parse_exported_content_plan_markdown(documents)

    assert len(pages) == 2
    assert pages[0]["type"] == "cover"
    assert pages[0]["text_content"]["headline"] == "非凡产研战略框架"
    assert pages[0]["text_content"]["body"] == ""
    assert pages[0]["speaker_notes"] == "封面备注"
    assert pages[1]["section_title"] == "使命愿景"
    assert pages[1]["text_content"]["body"] == "- 数据\n- 资源"
