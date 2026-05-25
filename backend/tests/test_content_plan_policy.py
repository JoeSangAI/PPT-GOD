import json
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
    should_generate_incremental_long_deck,
    _soft_page_bounds,
)


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
                "speaker_notes": f"围绕第 {page_num} 页展开讲解。",
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
    assert "failed before producing usable pages" in str(exc_info.value) or "质量不足" in str(exc_info.value)


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


def test_director_contract_policy_text_describes_restoration_requirements():
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

    assert "尽量完整覆盖上传材料" in text
    assert "不要压缩成摘要" in text
    assert "保留原文结构和讲述顺序" in text


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


def test_page_map_placeholder_model_output_uses_source_draft(monkeypatch):
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

    page_map = generate_content_page_map(
        topic="把脚本整理成 3 页提案 PPT",
        documents="真实脚本材料",
        page_count=3,
    )

    assert [page["headline"] for page in page_map] == ["真实项目标题", "真实执行节奏", "真实下一步"]
    assert "bullet" not in "\n".join("\n".join(page.get("bullets") or []) for page in page_map)


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
备注：解释经营问题。

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
                f"P{idx}｜content｜内容｜第 {idx} 页主题\n- 具体内容 {idx}\n备注：讲解第 {idx} 页。\n视觉：内容画面 {idx}"
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


def test_page_map_falls_back_to_source_draft_when_model_fails(monkeypatch):
    documents = """# 面向AI时代，企业营与销该如何布局

## 模块一：道

- ChatGPT 达到 1 亿规模只用了 2 个月
- AI 正在成为消费决策的新中介
"""

    class FakeCompletions:
        def create(self, **_kwargs):
            raise TimeoutError("timeout")

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(content_plan_module, "get_llm_client", lambda: FakeClient())

    page_map = generate_content_page_map(
        topic="做成 10 页课程",
        documents=documents,
        page_count=10,
    )

    rendered = "\n".join(
        "\n".join(str(item) for item in (page.get("bullets") or []))
        for page in page_map
    )
    assert len(page_map) == 10
    assert "ChatGPT 达到 1 亿规模只用了 2 个月" in rendered
    assert all(page["generation_status"] == "page_map_source" for page in page_map)


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

P3｜ending｜总结｜下一步
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

    assert "低于本轮最低要求" in str(exc_info.value)


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
                f"P{idx}｜content｜达尔文｜第 {idx} 页内容\n- 真实要点 {idx}\n备注：讲解第 {idx} 页。"
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
            assert "text_content.body 是页面卡片/PPT 上可见的正文区域" in prompt
            assert "content/data 页的 text_content.body 必须写 2-4 行具体正文或 bullet" in prompt
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
            assert "text_content.body 是页面卡片/PPT 上可见的正文区域" in prompt
            assert "content/data 页的 text_content.body 必须写 2-4 行具体正文或 bullet" in prompt
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
