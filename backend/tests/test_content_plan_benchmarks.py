from app.services import content_plan as content_plan_module
from app.services.content_plan import resolve_content_plan_page_target
from app.services.content_plan_quality import (
    ContentPlanQualityCase,
    evaluate_page_map_quality_case,
)

from content_plan_benchmark_fixtures import (
    LONG_SOURCE_CAPACITY_MANUSCRIPT,
    MID_SOURCE_FOR_DELIVERY_INTENT,
    SHORT_ALIGNMENT_BRIEF,
)


def test_long_source_capacity_benchmark_preserves_tail_and_gold_sentences():
    contract = {
        "task_type": "teaching_deck",
        "source_use": "faithful",
        "coverage": "near_complete",
        "compression": "low",
        "depth": "deep",
        "page_budget_policy": "source_capacity",
        "structure_policy": "source_order",
        "delivery_intent": "面向一小时课程演讲，保留原文结构、关键章节和金句。",
        "confidence": 0.93,
        "evidence": ["一小时课程", "保留原文结构和金句"],
    }

    target, min_pages, max_pages = resolve_content_plan_page_target(
        "把这份材料做成一小时课程 PPT，保留原文结构和金句",
        None,
        LONG_SOURCE_CAPACITY_MANUSCRIPT,
        intent_contract=contract,
    )
    source_draft = content_plan_module._source_draft_page_map(
        topic="把这份材料做成一小时课程 PPT，保留原文结构和金句",
        documents=LONG_SOURCE_CAPACITY_MANUSCRIPT,
        target_count=target,
        min_pages=min_pages,
        max_pages=max_pages,
        intent_contract=contract,
    )
    case = ContentPlanQualityCase(
        name="long-source-capacity",
        target_count=target,
        min_pages=min_pages,
        required_anchors=("第六章：企业 90 天行动清单", "复盘与下一步：从今天开始的三件事"),
        required_gold_sentences=(
            "未来所有平台都想做同一件事：让用户有事先问我。",
            "在人心里有位置，在平台里有流量，在 AI 里有推荐。",
            "当客户的 AI 凝视你的品牌时，它到底能看到什么？",
        ),
        forbidden_terms=("内容待细化", "这一页口头展开", "--- SOURCE"),
    )

    report = evaluate_page_map_quality_case(
        source_draft,
        case,
        source_draft=source_draft,
        intent_contract=contract,
    )

    assert target >= content_plan_module.LONG_DECK_INCREMENTAL_THRESHOLD
    assert report.passed, [issue.code for issue in report.issues]


def test_short_material_benchmark_stays_compact_but_contentful():
    contract = {
        "task_type": "source_to_ppt",
        "source_use": "optimized",
        "coverage": "balanced",
        "compression": "medium",
        "depth": "standard",
        "page_budget_policy": "auto",
        "structure_policy": "reorganize",
        "delivery_intent": "面向团队内部对齐，用短 PPT 讲清现状、原因和下周行动。",
        "confidence": 0.88,
        "evidence": ["团队内部对齐"],
    }

    target, min_pages, max_pages = resolve_content_plan_page_target(
        "做一份团队内部对齐 PPT，5-7 页就够",
        None,
        SHORT_ALIGNMENT_BRIEF,
        intent_contract=contract,
    )
    page_map = content_plan_module._source_draft_page_map(
        topic="做一份团队内部对齐 PPT，5-7 页就够",
        documents=SHORT_ALIGNMENT_BRIEF,
        target_count=target,
        min_pages=min_pages,
        max_pages=max_pages,
        intent_contract=contract,
    )
    report = content_plan_module._page_map_is_useful(
        page_map,
        target_count=target,
        min_pages=min_pages,
        strict=False,
    )

    assert target <= 7
    assert max_pages <= 7
    assert report
    assert all("内容待细化" not in str(page) for page in page_map)


def test_delivery_intent_matrix_changes_budget_without_genre_branches():
    full_contract = {
        "task_type": "teaching_deck",
        "source_use": "faithful",
        "coverage": "near_complete",
        "compression": "low",
        "depth": "deep",
        "page_budget_policy": "source_capacity",
        "structure_policy": "source_order",
        "delivery_intent": "面向 90 分钟工作坊，保留背景、问题、方法、案例和行动。",
        "confidence": 0.9,
        "evidence": ["工作坊"],
    }
    brief_contract = {
        "task_type": "summary",
        "source_use": "optimized",
        "coverage": "selective",
        "compression": "high",
        "depth": "brief",
        "page_budget_policy": "compact",
        "structure_policy": "reorganize",
        "delivery_intent": "面向管理层快速决策，只保留问题、收益和 90 天动作。",
        "confidence": 0.9,
        "evidence": ["快速决策"],
    }

    full_target, _full_min, full_max = resolve_content_plan_page_target(
        "做成工作坊课件",
        None,
        MID_SOURCE_FOR_DELIVERY_INTENT,
        intent_contract=full_contract,
    )
    brief_target, _brief_min, brief_max = resolve_content_plan_page_target(
        "做成管理层决策简报",
        None,
        MID_SOURCE_FOR_DELIVERY_INTENT,
        intent_contract=brief_contract,
    )
    full_prompt = content_plan_module._intent_contract_policy_text(full_contract)
    brief_prompt = content_plan_module._intent_contract_policy_text(brief_contract)

    assert full_target > brief_target
    assert full_max > brief_max
    assert full_contract["delivery_intent"] in full_prompt
    assert brief_contract["delivery_intent"] in brief_prompt
    assert "genre" not in full_prompt.lower()
    assert "交付类型" not in full_prompt
