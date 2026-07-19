from app.services.semantic_relations import (
    SEMANTIC_RELATIONS,
    is_supported_semantic_relation_label,
    normalize_semantic_relation,
    semantic_relation_prompt_rule,
)
from app.services.prompt_engine import generate_prompt_for_page
from app.services.visual_plan import _build_batch_prompt


def test_semantic_relation_contract_is_explicit_and_stable():
    assert SEMANTIC_RELATIONS == (
        "none",
        "sequence",
        "parallel",
        "comparison",
        "causality",
        "hierarchy",
        "convergence",
        "cycle",
    )


def test_semantic_relation_normalization_does_not_guess_from_copy():
    assert normalize_semantic_relation("many-to-one") == "convergence"
    assert normalize_semantic_relation("cause and effect") == "causality"
    assert normalize_semantic_relation("market growth story") == "none"
    assert normalize_semantic_relation("list") == "none"
    assert is_supported_semantic_relation_label("parallel") is True
    assert is_supported_semantic_relation_label("list") is False


def test_parallel_relation_forbids_false_process_visuals():
    rule = semantic_relation_prompt_rule("parallel")
    assert "equal-status parallel components" in rule
    assert "numbered steps" in rule
    assert "loop" in rule


def test_convergence_relation_keeps_inputs_independent():
    rule = semantic_relation_prompt_rule("convergence")
    assert "independent inputs" in rule
    assert "converge" in rule
    assert "containment" in rule


def test_visual_planner_must_choose_relation_before_composition():
    prompt = _build_batch_prompt(
        pages_summary=[
            {
                "page_num": 1,
                "type": "content",
                "headline": "影响选择的多个因素",
                "body_context": "时间、身份、人生阶段、场景、预算与情绪",
            }
        ],
        style={"meta": {"theme": "商务", "mood": "克制", "palette": ["#111111", "#D4AF37"]}, "body": ""},
    )

    assert "semantic_relation：先判断本页信息关系" in prompt
    assert "不要为了方便画图而把并列内容改成步骤" in prompt
    assert "把独立变量改成循环或包含" in prompt


def test_final_image_prompt_preserves_parallel_relationship_boundary():
    prompt = generate_prompt_for_page(
        page_intent={
            "page_num": 1,
            "type": "content",
            "layout": "content",
            "semantic_relation": "parallel",
            "visual_evidence": "六个共同影响选择的上下文因素",
            "visual_description": "用克制的排版呈现六个因素。",
        },
        content_text={
            "headline": "一人千面",
            "body": ["时间", "身份", "人生阶段", "场景", "预算", "情绪"],
        },
        reference_images=[],
        style_text_override="Style: 黑金克制\nPalette: #111111, #D4AF37",
    )

    assert "Semantic relationship (parallel)" in prompt
    assert "do not turn them into numbered steps" in prompt
    assert "a loop" in prompt
