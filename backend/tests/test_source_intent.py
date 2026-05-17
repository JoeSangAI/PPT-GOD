from app.services.source_intent import (
    contract_to_planning_policy,
    infer_intent_contract,
    normalize_intent_contract,
)


SINGLE_PPT_DIAGNOSTICS = {
    "ppt_source_count": 1,
    "source_page_count": 35,
    "editable_text_density": "sparse",
    "image_only_page_count": 35,
}


def test_replicate_cues_lock_verbatim_same_order():
    contract = infer_intent_contract(
        "请 1:1 复刻这份 PPT，内容不要动，页序不要乱",
        source_diagnostics=SINGLE_PPT_DIAGNOSTICS,
    )

    assert contract["task_type"] == "replicate"
    assert contract["rewrite_level"] == "none"
    assert contract["page_order_policy"] == "preserve"
    assert contract["page_count_policy"] == "same"
    assert contract["source_fidelity"] == "verbatim"
    assert contract["visual_source_use"] == "page_reference"
    assert contract["confidence"] >= 0.8


def test_finished_ppt_default_is_light_polish_not_replicate():
    contract = infer_intent_contract(
        "帮我把这个 PPT 做得更好",
        source_diagnostics=SINGLE_PPT_DIAGNOSTICS,
    )

    assert contract["task_type"] == "polish"
    assert contract["rewrite_level"] == "light"
    assert contract["page_order_policy"] == "preserve"
    assert contract["source_fidelity"] == "faithful"


def test_preserve_page_and_original_text_overrides_extract_restructure_words():
    contract = infer_intent_contract(
        "把这份里面的文字信息提取出来，然后截图也放到对应页面。我们要重构一下，重新做一个 PPT。要求：1. 页码等信息尽量保持不变 2. 确保原话和原文内容基本上保持不变",
        source_diagnostics=SINGLE_PPT_DIAGNOSTICS,
    )

    assert contract["task_type"] == "polish"
    assert contract["rewrite_level"] == "light"
    assert contract["page_order_policy"] == "preserve"
    assert contract["page_count_policy"] == "same"
    assert contract["source_fidelity"] == "faithful"
    assert contract["confidence"] >= 0.8


def test_restructure_cues_allow_reordering_and_target_count():
    contract = infer_intent_contract(
        "把这几份材料提炼成 12 页，结构可以重组",
        source_diagnostics={"ppt_source_count": 2, "source_page_count": 60},
    )

    assert contract["task_type"] in {"restructure", "merge"}
    assert contract["rewrite_level"] in {"moderate", "free"}
    assert contract["page_order_policy"] == "can_reorder"
    assert contract["page_count_policy"] == "target_count"


def test_normalize_rejects_unknown_values_and_preserves_evidence():
    contract = normalize_intent_contract({
        "task_type": "magic",
        "rewrite_level": "light",
        "page_order_policy": "preserve",
        "page_count_policy": "same",
        "source_fidelity": "faithful",
        "visual_source_use": "page_reference",
        "confidence": 2,
        "evidence": ["页序不要乱"],
    })

    assert contract["task_type"] == "polish"
    assert contract["confidence"] == 1.0
    assert contract["evidence"] == ["页序不要乱"]


def test_planning_policy_maps_contract_to_runtime_flags():
    policy = contract_to_planning_policy({
        "task_type": "replicate",
        "rewrite_level": "none",
        "page_order_policy": "preserve",
        "page_count_policy": "same",
        "source_fidelity": "verbatim",
        "visual_source_use": "page_reference",
        "confidence": 0.9,
        "evidence": ["1:1"],
    })

    assert policy["allow_direct_ppt_replicate"] is True
    assert policy["preserve_source_page_order"] is True
    assert policy["preserve_source_page_count"] is True
    assert policy["rewrite_instruction"] == "verbatim"
