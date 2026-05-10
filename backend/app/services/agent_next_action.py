from __future__ import annotations

from copy import deepcopy
from typing import Any


CONTENT_ACTIONS = frozenset({
    "diagnose",
    "collect_content",
    "propose_plan",
    "generate_plan",
    "regenerate_pages",
    "retry_failed",
    "update_style",
    "update_slide_content",
    "update_all_slides",
    "regenerate_plan",
    "add_slide_before",
    "add_slide_after",
    "forward_to_visual",
    "answer",
})

VISUAL_ACTIONS = frozenset({
    "collect_assets",
    "propose_styles",
    "adjust_style",
    "confirm_style",
    "reroll_page_visual_plan",
    "update_slide_visual",
    "update_all_slides_visual",
    "request_generate_image",
    "forward_to_content",
    "answer",
})

FINETUNE_ACTIONS = frozenset({
    "refine_slide",
    "answer",
})


def with_next_action(result: Any, project_context: dict[str, Any], agent_role: str) -> Any:
    """Attach a small deterministic next-step hint to an Agent result.

    The LLM still owns natural-language understanding. This layer only maps an
    already chosen action plus project state to one explicit UI affordance.
    """
    if not isinstance(result, dict):
        return result

    normalized = deepcopy(result)
    if isinstance(normalized.get("next_action"), dict):
        return normalized

    next_action = infer_next_action(normalized, project_context, agent_role)
    if next_action:
        normalized["next_action"] = next_action
    return normalized


def infer_next_action(result: dict[str, Any], project_context: dict[str, Any], agent_role: str) -> dict[str, Any] | None:
    action = result.get("action")
    if agent_role == "content":
        return _content_next_action(action, result, project_context)
    if agent_role == "visual":
        return _visual_next_action(action, result, project_context)
    return None


def _content_next_action(action: str | None, result: dict[str, Any], project_context: dict[str, Any]) -> dict[str, Any] | None:
    if action in {"propose_plan", "generate_plan"} and result.get("topic"):
        page_count = result.get("page_count") or (result.get("positioning") or {}).get("estimated_pages")
        return {
            "type": "generate_content_plan",
            "label": "开始生成内容规划",
            "payload": {
                "topic": result.get("topic"),
                "page_count": page_count,
            },
        }

    if action == "regenerate_plan" and result.get("topic"):
        return {
            "type": "generate_content_plan",
            "label": "重新生成内容规划",
            "payload": {
                "topic": result.get("topic"),
                "page_count": result.get("page_count"),
            },
        }

    if action == "forward_to_visual":
        return {"type": "switch_to_visual", "label": "进入视觉总监"}

    if action == "retry_failed" and project_context.get("has_failed_slides"):
        return {"type": "retry_failed", "label": "一键重试失败页", "confirm": True}

    if action == "regenerate_pages" and result.get("page_nums"):
        return {
            "type": "generate_images",
            "label": "确认重新生成图片",
            "payload": {"page_nums": result.get("page_nums")},
            "confirm": True,
        }

    if action in {"update_slide_content", "update_all_slides", "add_slide_before", "add_slide_after"}:
        if project_context.get("content_plan_confirmed") or project_context.get("total_slides", 0) > 0:
            return {"type": "switch_to_visual", "label": "切换到视觉总监"}

    return None


def _visual_next_action(action: str | None, result: dict[str, Any], project_context: dict[str, Any]) -> dict[str, Any] | None:
    has_selected_style = bool(project_context.get("has_selected_style"))
    has_prompts = bool(project_context.get("has_prompts"))
    has_images = bool(project_context.get("has_images"))
    content_confirmed = bool(project_context.get("content_plan_confirmed"))

    if action == "forward_to_content":
        return {"type": "switch_to_content", "label": "切换到内容总监"}

    if action == "request_generate_image":
        return {
            "type": "generate_images",
            "label": "确认生成图片",
            "payload": {"page_nums": result.get("page_nums") or []},
            "confirm": True,
        }

    if action == "reroll_page_visual_plan" and result.get("page_nums"):
        return {
            "type": "generate_images",
            "label": "确认生成图片",
            "payload": {"page_nums": result.get("page_nums")},
            "confirm": True,
        }

    if action in {"collect_assets", "answer"} and content_confirmed and not has_selected_style:
        return {"type": "generate_style_proposals", "label": "生成视觉方向"}

    if action == "answer" and has_selected_style and not has_prompts:
        return {"type": "generate_visual_prompts", "label": "生成画面方案"}

    if action == "answer" and has_prompts and not has_images:
        return {"type": "start_prototype", "label": "打样确认", "confirm": True}

    return None
