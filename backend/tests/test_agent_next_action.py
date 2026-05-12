from app.services.agent_next_action import CONTENT_ACTIONS, FINETUNE_ACTIONS, VISUAL_ACTIONS, with_next_action
from app.api.chat import (
    _content_result_needs_contract_review,
    _enforce_content_action_contract,
    _enforce_visual_action_contract,
    _infer_requested_page_count,
    _has_page_count_change_intent,
    _visual_result_needs_contract_review,
)
from app.api.slides import PageNumsRequest
from app.services.content_plan import (
    _enforce_requested_page_range,
    _is_strict_page_count_request,
    infer_page_count_from_topic,
    infer_page_count_range_from_topic,
)
from app.services.visual_plan import _build_batch_prompt


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


def test_brief_page_count_range_is_inferred_from_initial_topic():
    topic = "把这个 MD 文件做成 60 到 80 页的 PPT，给大连混沌学员讲 1.5 小时"

    assert infer_page_count_range_from_topic(topic) == (60, 80)
    assert infer_page_count_from_topic(topic) == 80
    assert _infer_requested_page_count(topic) == 80


def test_brief_page_count_range_handles_real_user_variants():
    variants = [
        "页数控制在 60-80，适合 90 分钟内训",
        "做成60页到80页的PPT",
        "不少于 60 页，不超过 80 页，做成课程课件",
        "最多 80 页，至少 60 页",
        "Make this into 60-80 slides for a workshop",
        "做 120-150 页，越细越好",
    ]

    assert [infer_page_count_from_topic(text) for text in variants] == [80, 80, 80, 80, 80, 150]


def test_upper_bound_page_count_is_not_strict_exact_count():
    assert infer_page_count_range_from_topic("不要超过80页") == (1, 80)
    assert infer_page_count_from_topic("不要超过80页") == 80
    assert not _is_strict_page_count_request("不要超过80页")


def test_page_count_inference_ignores_slide_references():
    assert infer_page_count_from_topic("第 3 页标题更锐利") is None
    assert _infer_requested_page_count("第 3 页标题更锐利") is None
    assert infer_page_count_from_topic("P12 页标题改小") is None
    assert infer_page_count_from_topic("12页标题改小") is None
    assert not _has_page_count_change_intent("12页标题改小", {"total_slides": 40})


def test_explicit_page_range_allows_shorter_outline_when_material_is_insufficient():
    outline = [{"page_num": i, "text_content": {"headline": f"P{i}"}} for i in range(1, 15)]

    accepted = _enforce_requested_page_range(outline, (60, 80))

    assert len(accepted) == 14


def test_explicit_page_range_trims_only_far_above_soft_upper_bound():
    outline = [{"page_num": i, "text_content": {"headline": f"P{i}"}} for i in range(1, 87)]

    trimmed = _enforce_requested_page_range(outline, (60, 80))

    assert len(trimmed) == 84


def test_small_page_range_accepts_slight_overshoot():
    outline = [{"page_num": i, "text_content": {"headline": f"P{i}"}} for i in range(1, 18)]

    accepted = _enforce_requested_page_range(outline, (10, 15))

    assert len(accepted) == 17


def test_upper_bound_page_count_still_trims_at_upper_bound():
    outline = [{"page_num": i, "text_content": {"headline": f"P{i}"}} for i in range(1, 86)]

    trimmed = _enforce_requested_page_range(outline, (1, 80))

    assert len(trimmed) == 80


def test_agent_action_contract_includes_handoffs_and_content_regeneration():
    assert "forward_to_visual" in CONTENT_ACTIONS
    assert "regenerate_plan" in CONTENT_ACTIONS
    assert "forward_to_content" in VISUAL_ACTIONS
    assert "refine_slide" in FINETUNE_ACTIONS


def test_visual_generation_request_accepts_cross_stage_context():
    request = PageNumsRequest(stage_context="内容阶段要求突出 OpenDay 39 场和累计参会约 2 万人")

    assert "OpenDay 39 场" in request.stage_context


def test_visual_plan_prompt_inherits_cross_stage_requirements():
    prompt = _build_batch_prompt(
        pages_summary=[
            {
                "page_num": 6,
                "type": "data",
                "headline": "活动增长",
                "subhead": "",
                "body_preview": "OpenDay：39 场；累计参会人数：约 2 万人",
                "existing_visual_suggestion": "",
                "global_user_requirements": "内容阶段要求突出 OpenDay 39 场和累计参会约 2 万人",
            }
        ],
        style={"meta": {"palette": ["#111111"], "theme": "商务"}, "body": ""},
    )

    assert "跨阶段用户补充要求" in prompt
    assert "OpenDay 39 场" in prompt


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


def test_visual_generation_answer_is_coerced_to_confirmation_action():
    result = {"action": "answer", "response": "好的，我开始生成图片。"}

    compiled = _enforce_visual_action_contract(
        result=result,
        user_message="可以了，出图",
        page_context={"mode": "global", "target_page_nums": [2, 3]},
    )

    assert compiled["action"] == "request_generate_image"
    assert compiled["page_nums"] == [2, 3]


def test_visual_page_edit_answer_is_coerced_to_reroll_action():
    result = {"action": "answer", "response": "好的，我会把背景换成深蓝。"}

    compiled = _enforce_visual_action_contract(
        result=result,
        user_message="这一页背景换成深蓝，标题更亮",
        page_context={"mode": "page", "current_page": {"page_num": 5}},
    )

    assert compiled["action"] == "reroll_page_visual_plan"
    assert compiled["page_nums"] == [5]


def test_visual_deck_edit_answer_is_coerced_to_adjust_style_action():
    result = {"action": "answer", "response": "好的，我会把整套统一成深色医疗科技感。"}

    compiled = _enforce_visual_action_contract(
        result=result,
        user_message="所有页面统一换成深色医疗科技感",
        page_context={"mode": "global", "scope": "deck", "target_page_nums": []},
    )

    assert compiled["action"] == "adjust_style"
    assert "全局视觉要求" in compiled["response"]


def test_visual_answer_without_mutation_intent_does_not_need_review():
    result = {"action": "answer", "response": "这个风格更适合路演场景。"}

    assert not _visual_result_needs_contract_review(result, "为什么这套风格适合路演？")


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


def test_content_answer_is_recompiled_by_action_contract():
    result = {"action": "answer", "response": "明白，我会完整使用原文重新规划。"}
    context = {"title": "品牌策略提案", "total_slides": 12}

    compiled = _enforce_content_action_contract(
        result=result,
        user_message="页数太少了，而且没有用原文。",
        project_context=context,
        compiler=lambda **_: {
            "action": "regenerate_plan",
            "topic": "品牌策略提案。基于原文重新生成 24 页内容规划。",
            "page_count": 24,
            "response": "收到，我会按原文重新生成更完整的内容规划。",
        },
    )

    assert compiled["action"] == "regenerate_plan"
    assert compiled["page_count"] == 24
    assert "原文" in compiled["topic"]


def test_story_feedback_is_handled_by_contract_not_keyword_fallback():
    result = {"action": "answer", "response": "明白，我会把故事讲得更完整。"}
    context = {"title": "果蝇之梯", "total_slides": 10}

    compiled = _enforce_content_action_contract(
        result=result,
        user_message="你这个感觉看完之后，并没有把故事完整地展现出来。我更想要你把这个故事好好地讲出来。",
        project_context=context,
        compiler=lambda **_: {
            "action": "regenerate_plan",
            "topic": "果蝇之梯。重构故事线，补足铺垫、转折和结尾。",
            "page_count": 14,
            "response": "收到，我会重构整套故事线。",
        },
    )

    assert compiled["action"] == "regenerate_plan"
    assert compiled["page_count"] == 14
    assert "果蝇之梯" in compiled["topic"]


def test_incomplete_mutation_payload_is_recompiled():
    result = {"action": "update_all_slides", "response": "我会补一个案例页并调整结构。"}
    context = {"title": "果蝇之梯", "total_slides": 10}

    compiled = _enforce_content_action_contract(
        result=result,
        user_message="结构不对，少了一个案例页。",
        project_context=context,
        compiler=lambda **_: {
            "action": "add_slide_after",
            "new_slide": {
                "page_num": 4,
                "type": "content",
                "section_title": "",
                "text_content": {"headline": "关键案例", "subhead": "", "body": "补充案例内容"},
                "speaker_notes": "",
                "visual_suggestion": "",
            },
            "response": "已将反馈编译为插入案例页。",
        },
    )

    assert compiled["action"] == "add_slide_after"
    assert compiled["new_slide"]["page_num"] == 4


def test_complete_mutation_payload_bypasses_contract_compiler():
    result = {
        "action": "update_all_slides",
        "updated_slides": [{"page_num": 1, "text_content": {"headline": "新标题", "subhead": "", "body": ""}}],
        "response": "已更新。",
    }

    assert not _content_result_needs_contract_review(result, is_draft=False)


def test_forward_to_visual_is_reviewed_before_final_handoff():
    result = {"action": "forward_to_visual", "response": "内容已确认，进入视觉阶段。"}

    compiled = _enforce_content_action_contract(
        result=result,
        user_message="先别进视觉，整体故事线还没讲清楚。",
        project_context={"title": "果蝇之梯", "total_slides": 10, "content_plan_confirmed": True},
        compiler=lambda **_: {
            "action": "regenerate_plan",
            "topic": "果蝇之梯。重构整体故事线后再进入视觉阶段。",
            "response": "收到，先重构内容规划。",
        },
    )

    assert compiled["action"] == "regenerate_plan"
    assert "故事线" in compiled["topic"]


def test_contract_compiler_can_preserve_valid_visual_handoff():
    result = {"action": "forward_to_visual", "response": "内容已确认，进入视觉阶段。"}

    compiled = _enforce_content_action_contract(
        result=result,
        user_message="可以了，进入视觉。",
        project_context={"title": "果蝇之梯", "total_slides": 10, "content_plan_confirmed": True},
        compiler=lambda **_: {
            "action": "forward_to_visual",
            "response": "内容已确认，现在进入视觉总监。",
        },
    )

    assert compiled["action"] == "forward_to_visual"


def test_pure_question_can_exit_contract_as_answer():
    result = {"action": "answer", "response": "这页是在做背景铺垫。"}

    compiled = _enforce_content_action_contract(
        result=result,
        user_message="第3页现在的作用是什么？",
        project_context={"title": "果蝇之梯", "total_slides": 10},
        compiler=lambda **_: {
            "action": "answer",
            "response": "第3页是在建立科学起点。",
            "no_change_reason": "用户是在询问页面作用，没有提出内容修改。",
        },
    )

    assert compiled["action"] == "answer"
    assert compiled["no_change_reason"]


def test_failed_contract_compiler_does_not_silently_keep_promise_answer():
    result = {"action": "answer", "response": "好的，我会调整。"}

    compiled = _enforce_content_action_contract(
        result=result,
        user_message="整体感觉不对，帮我调整一下。",
        project_context={"title": "果蝇之梯", "total_slides": 10},
        compiler=lambda **_: None,
    )

    assert compiled["action"] == "answer"
    assert compiled["no_change_reason"] == "content_instruction_compiler_failed"
    assert "没有修改 PPT" in compiled["response"]


def test_page_count_change_is_forced_to_regenerate_plan_even_if_compiler_answers():
    result = {"action": "answer", "response": "已根据你的要求重新规划为 12 页内容。"}

    compiled = _enforce_content_action_contract(
        result=result,
        user_message="把它变成 12 页 PPT。",
        project_context={"title": "非凡产研", "total_slides": 10},
        compiler=lambda **_: {
            "action": "answer",
            "response": "已根据你的要求重新规划为 12 页内容。",
            "no_change_reason": "incorrectly_treated_as_answer",
        },
    )

    assert compiled["action"] == "regenerate_plan"
    assert compiled["page_count"] == 12
    assert "必须 12 页" in compiled["topic"]


def test_confirmation_after_unapplied_plan_offer_generates_before_visual_handoff():
    result = {"action": "forward_to_visual", "response": "内容已就绪，已切换至视觉总监阶段。"}
    history = [
        {
            "role": "assistant",
            "content": "已根据你的要求重新规划内容：\n\n>12页内容规划：\n• >P1 封面\n• >P2 使命愿景\n• >P12 结尾",
        }
    ]

    compiled = _enforce_content_action_contract(
        result=result,
        user_message="ok",
        project_context={"title": "非凡产研", "total_slides": 10, "content_plan_confirmed": False},
        history=history,
        compiler=lambda **_: {
            "action": "forward_to_visual",
            "response": "内容已确认，现在进入视觉总监。",
        },
    )

    assert compiled["action"] == "regenerate_plan"
    assert compiled["page_count"] == 12
    assert "真正生成" in compiled["response"]


def test_page_reference_is_not_misread_as_deck_page_count():
    assert _infer_requested_page_count("第12页现在是什么作用？") is None


def test_page_attachment_feedback_updates_current_slide_instead_of_regenerating_deck():
    result = {"action": "answer", "response": "收到，正在把这两页信息做到当前页里。"}
    page_context = {
        "mode": "page",
        "current_page": {
            "page_num": 7,
            "type": "content",
            "content_json": {
                "page_num": 7,
                "type": "content",
                "section_title": "",
                "text_content": {"headline": "", "subhead": "", "body": ""},
                "speaker_notes": "",
                "visual_suggestion": "",
            },
        },
    }

    compiled = _enforce_content_action_contract(
        result=result,
        user_message="把这两页的信息做到这一页的 PPT 里面去",
        project_context={"title": "非凡产研", "total_slides": 12},
        page_context=page_context,
        attachment_context="### 图片 1: 第一页\n关键数据：OpenDay 39 场。\n\n### 图片 2: 第二页\n累计参会约 2 万人。",
        compiler=lambda **_: {
            "action": "regenerate_plan",
            "topic": "非凡产研。重新生成内容规划。",
            "response": "收到，正在重新生成内容规划。",
        },
    )

    assert compiled["action"] == "update_slide_content"
    assert compiled["updated_content"]["page_num"] == 7
    assert "OpenDay 39 场" in compiled["updated_content"]["text_content"]["body"]
    assert "累计参会约 2 万人" in compiled["updated_content"]["text_content"]["body"]
