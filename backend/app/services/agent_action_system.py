from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Literal


ActionScope = Literal["none", "deck", "slide", "slides", "handoff"]


@dataclass(frozen=True)
class ContentActionDefinition:
    name: str
    scope: ActionScope
    description: str
    required_fields: tuple[str, ...] = ()
    payload_fields: tuple[str, ...] = ()
    target_bound: bool = False


@dataclass(frozen=True)
class ActionValidation:
    valid: bool
    reason: str = ""
    message: str = ""


CONTENT_ACTION_DEFINITIONS: tuple[ContentActionDefinition, ...] = (
    ContentActionDefinition("diagnose", "none", "分析需求是否足够清楚。"),
    ContentActionDefinition("collect_content", "none", "继续收集制作 PPT 所需信息。"),
    ContentActionDefinition("propose_plan", "none", "输出生成内容规划前的定调摘要。"),
    ContentActionDefinition("generate_plan", "deck", "开始生成整套内容规划。", ("topic",), ("topic", "page_count")),
    ContentActionDefinition("regenerate_pages", "slides", "重新生成指定页图片。", ("page_nums",), ("page_nums",), True),
    ContentActionDefinition("retry_failed", "none", "重试失败页面。"),
    ContentActionDefinition("update_style", "deck", "更新整套内容规划风格标记。", ("style_id",), ("style_id",)),
    ContentActionDefinition(
        "update_slide_content",
        "slide",
        "更新单页内容，必须只影响目标页。",
        ("updated_content",),
        ("updated_content",),
        True,
    ),
    ContentActionDefinition(
        "update_all_slides",
        "slides",
        "更新多页内容，必须只允许影响目标页。",
        ("updated_slides",),
        ("updated_slides",),
        True,
    ),
    ContentActionDefinition(
        "merge_slides",
        "slides",
        "把目标页合并为更少页，必须只允许影响目标页；updated_slides 保留页，delete_page_nums 删除多余页。",
        ("updated_slides", "delete_page_nums"),
        ("updated_slides", "delete_page_nums"),
        True,
    ),
    ContentActionDefinition("regenerate_plan", "deck", "重做整套内容规划。", ("topic",), ("topic", "page_count")),
    ContentActionDefinition(
        "add_slide_before",
        "slide",
        "在目标页前插入一页。",
        ("new_slide",),
        ("new_slide",),
        True,
    ),
    ContentActionDefinition(
        "add_slide_after",
        "slide",
        "在目标页后插入一页。",
        ("new_slide",),
        ("new_slide",),
        True,
    ),
    ContentActionDefinition("forward_to_visual", "handoff", "内容已确认，进入视觉总监。"),
    ContentActionDefinition("answer", "none", "纯回答或解释，不修改 PPT。"),
)

CONTENT_ACTION_REGISTRY = {definition.name: definition for definition in CONTENT_ACTION_DEFINITIONS}
CONTENT_ACTION_NAMES = frozenset(CONTENT_ACTION_REGISTRY)
CONTENT_MUTATION_PAYLOAD_KEYS = {
    definition.name: definition.required_fields[0]
    for definition in CONTENT_ACTION_DEFINITIONS
    if definition.required_fields and definition.name not in {"generate_plan", "regenerate_pages", "retry_failed"}
}


def content_action_catalog_prompt() -> str:
    lines = ["【内容动作目录】"]
    for definition in CONTENT_ACTION_DEFINITIONS:
        fields = "、".join(definition.payload_fields) if definition.payload_fields else "无"
        target_rule = "；只允许影响目标页" if definition.target_bound else ""
        lines.append(
            f"- {definition.name}: {definition.description} scope={definition.scope}; payload={fields}{target_rule}"
        )
    return "\n".join(lines)


def _target_page_nums_from_context(page_context: dict | None) -> list[int]:
    if not isinstance(page_context, dict):
        return []
    nums: list[int] = []
    raw_nums = page_context.get("target_page_nums")
    if isinstance(raw_nums, list):
        for item in raw_nums:
            try:
                page_num = int(item)
            except (TypeError, ValueError):
                continue
            if page_num > 0 and page_num not in nums:
                nums.append(page_num)
    if not nums and page_context.get("mode") == "page":
        current_page = page_context.get("current_page")
        if isinstance(current_page, dict):
            try:
                page_num = int(current_page.get("page_num"))
            except (TypeError, ValueError):
                page_num = 0
            if page_num > 0:
                nums.append(page_num)
    return nums


def _selected_or_page_scope(page_context: dict | None) -> bool:
    if not isinstance(page_context, dict):
        return False
    return bool(
        page_context.get("scope") == "selected_slides"
        or page_context.get("mode") == "page"
        or _target_page_nums_from_context(page_context)
    )


def _explicit_deck_intent(user_message: str) -> bool:
    return bool(
        re.search(
            r"(整套|全部|全局|所有页面|所有页|重新规划|重做整套|重构整套|从头来|重新生成内容规划)",
            user_message or "",
            flags=re.IGNORECASE,
        )
    )


def _payload_missing(result: dict[str, Any], definition: ContentActionDefinition) -> str | None:
    for field in definition.required_fields:
        value = result.get(field)
        if value is None:
            return field
        if isinstance(value, (str, list, dict)) and not value:
            return field
    return None


def _page_nums_from_items(items: Any) -> list[int]:
    nums: list[int] = []
    if not isinstance(items, list):
        return nums
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            page_num = int(item.get("page_num"))
        except (TypeError, ValueError):
            continue
        if page_num > 0:
            nums.append(page_num)
    return nums


def _page_nums_from_result(result: dict[str, Any]) -> list[int]:
    action = result.get("action")
    if action == "update_slide_content":
        updated = result.get("updated_content")
        if isinstance(updated, dict):
            try:
                page_num = int(updated.get("page_num"))
            except (TypeError, ValueError):
                return []
            return [page_num] if page_num > 0 else []
    if action in {"update_all_slides", "merge_slides"}:
        nums = _page_nums_from_items(result.get("updated_slides"))
        if action == "merge_slides":
            for item in result.get("delete_page_nums") or []:
                try:
                    page_num = int(item)
                except (TypeError, ValueError):
                    continue
                if page_num > 0:
                    nums.append(page_num)
        return nums
    if action == "regenerate_pages":
        nums = []
        for item in result.get("page_nums") or []:
            try:
                page_num = int(item)
            except (TypeError, ValueError):
                continue
            if page_num > 0:
                nums.append(page_num)
        return nums
    if action in {"add_slide_before", "add_slide_after"}:
        new_slide = result.get("new_slide")
        if isinstance(new_slide, dict):
            try:
                page_num = int(new_slide.get("page_num"))
            except (TypeError, ValueError):
                return []
            return [page_num] if page_num > 0 else []
    return []


def _validate_merge_payload(result: dict[str, Any]) -> ActionValidation | None:
    if result.get("action") != "merge_slides":
        return None
    updated_nums = _page_nums_from_items(result.get("updated_slides"))
    deleted_nums: list[int] = []
    for item in result.get("delete_page_nums") or []:
        try:
            page_num = int(item)
        except (TypeError, ValueError):
            continue
        if page_num > 0:
            deleted_nums.append(page_num)
    if not updated_nums or not deleted_nums:
        return ActionValidation(False, "missing_payload", "merge_slides requires updated_slides and delete_page_nums")
    if set(updated_nums).intersection(deleted_nums):
        return ActionValidation(False, "payload_conflict", "merge_slides cannot update and delete the same page")
    return None


def validate_content_action_result(
    result: dict[str, Any] | None,
    *,
    user_message: str = "",
    page_context: dict | None = None,
    project_context: dict | None = None,
) -> ActionValidation:
    if not isinstance(result, dict):
        return ActionValidation(False, "invalid_result", "result must be an object")
    action = str(result.get("action") or "")
    definition = CONTENT_ACTION_REGISTRY.get(action)
    if not definition:
        return ActionValidation(False, "unknown_action", f"unknown content action: {action or '<empty>'}")

    missing = _payload_missing(result, definition)
    if missing:
        return ActionValidation(False, "missing_payload", f"{action} missing required field: {missing}")

    if definition.scope == "deck" and _selected_or_page_scope(page_context) and not _explicit_deck_intent(user_message):
        return ActionValidation(False, "scope_conflict", f"{action} cannot replace selected_slides scope")

    merge_validation = _validate_merge_payload(result)
    if merge_validation:
        return merge_validation

    target_nums = _target_page_nums_from_context(page_context)
    if definition.target_bound and target_nums:
        target_set = set(target_nums)
        affected_nums = _page_nums_from_result(result)
        if not affected_nums:
            return ActionValidation(False, "missing_payload", f"{action} did not identify target pages")
        if any(page_num not in target_set for page_num in affected_nums):
            return ActionValidation(False, "scope_conflict", f"{action} affects pages outside selected_slides scope")

    return ActionValidation(True)


def content_action_payload_complete(result: dict[str, Any] | None) -> bool:
    validation = validate_content_action_result(result, user_message="", page_context=None, project_context=None)
    return validation.valid


def no_change_for_action_validation(validation: ActionValidation) -> dict[str, str]:
    reason = validation.reason or "content_action_invalid"
    return {
        "action": "answer",
        "response": "我没有修改 PPT；这条指令没有被转换成可安全执行的内容动作。请明确要改哪几页，或说明是否允许重做整套内容规划。",
        "no_change_reason": f"content_action_{reason}",
    }
