from app.services.agent_next_action import CONTENT_ACTIONS, FINETUNE_ACTIONS, VISUAL_ACTIONS, with_next_action
from app.api.chat import _coerce_content_regenerate_action


def test_content_proposal_gets_generate_plan_next_action():
    result = {
        "action": "propose_plan",
        "topic": "品牌年轻化策略提案",
        "positioning": {"estimated_pages": 12},
        "response": "已整理好方向。",
    }

    decorated = with_next_action(result, {"total_slides": 0}, "content")

    assert decorated["next_action"] == {
        "type": "generate_content_plan",
        "label": "开始生成内容规划",
        "payload": {"topic": "品牌年轻化策略提案", "page_count": 12},
    }
    assert "next_action" not in result


def test_agent_action_contract_includes_handoffs_and_content_regeneration():
    assert "forward_to_visual" in CONTENT_ACTIONS
    assert "regenerate_plan" in CONTENT_ACTIONS
    assert "forward_to_content" in VISUAL_ACTIONS
    assert "refine_slide" in FINETUNE_ACTIONS


def test_visual_answer_before_style_can_offer_style_proposal():
    result = {"action": "answer", "response": "这个方向适合温暖生活感。"}
    context = {
        "content_plan_confirmed": True,
        "has_selected_style": False,
        "has_prompts": False,
        "has_images": False,
    }

    decorated = with_next_action(result, context, "visual")

    assert decorated["next_action"]["type"] == "generate_style_proposals"


def test_visual_answer_after_style_can_offer_prompt_generation():
    result = {"action": "answer", "response": "风格已明确。"}
    context = {
        "content_plan_confirmed": True,
        "has_selected_style": True,
        "has_prompts": False,
        "has_images": False,
    }

    decorated = with_next_action(result, context, "visual")

    assert decorated["next_action"]["type"] == "generate_visual_prompts"


def test_request_generate_image_is_explicit_confirmation_action():
    result = {"action": "request_generate_image", "page_nums": [2, 3]}

    decorated = with_next_action(result, {}, "visual")

    assert decorated["next_action"] == {
        "type": "generate_images",
        "label": "确认生成图片",
        "payload": {"page_nums": [2, 3]},
        "confirm": True,
    }


def test_content_forward_to_visual_gets_switch_action():
    result = {"action": "forward_to_visual", "response": "内容已确认，进入视觉阶段。"}

    decorated = with_next_action(result, {"total_slides": 12}, "content")

    assert decorated["next_action"] == {"type": "switch_to_visual", "label": "进入视觉总监"}


def test_content_regenerate_plan_gets_generate_next_action():
    result = {
        "action": "regenerate_plan",
        "topic": "基于原文重新生成更完整内容规划",
        "page_count": 24,
        "response": "开始重做。",
    }

    decorated = with_next_action(result, {"total_slides": 12}, "content")

    assert decorated["next_action"] == {
        "type": "generate_content_plan",
        "label": "重新生成内容规划",
        "payload": {"topic": "基于原文重新生成更完整内容规划", "page_count": 24},
    }


def test_content_feedback_about_original_and_page_count_becomes_regenerate_plan():
    result = {"action": "answer", "response": "明白，我会完整使用原文重新规划。"}
    context = {"title": "品牌策略提案", "total_slides": 12}

    coerced = _coerce_content_regenerate_action(
        result,
        "页数太少了，而且没有用原文。",
        context,
        is_draft=False,
    )

    assert coerced["action"] == "regenerate_plan"
    assert coerced["page_count"] == 24
    assert "原文" in coerced["topic"]


def test_incomplete_content_action_payload_becomes_regenerate_plan():
    result = {"action": "update_all_slides", "response": "我会补一个案例页并调整结构。"}
    context = {"title": "品牌策略提案", "total_slides": 12}

    coerced = _coerce_content_regenerate_action(
        result,
        "结构不对，少了一个案例页。",
        context,
        is_draft=False,
    )

    assert coerced["action"] == "regenerate_plan"
    assert coerced["topic"].startswith("品牌策略提案")
