from app.services.agent_action_system import (
    CONTENT_ACTION_NAMES,
    CONTENT_MUTATION_PAYLOAD_KEYS,
    content_action_catalog_prompt,
    no_change_for_action_validation,
    validate_content_action_result,
)


def selected_pages_context():
    return {
        "mode": "global",
        "scope": "selected_slides",
        "target_page_nums": [3, 4, 5, 6],
        "target_area": "whole",
    }


def test_content_action_catalog_is_the_source_for_merge_slides_contract():
    assert "merge_slides" in CONTENT_ACTION_NAMES
    assert CONTENT_MUTATION_PAYLOAD_KEYS["merge_slides"] == "updated_slides"

    catalog = content_action_catalog_prompt()

    assert "merge_slides" in catalog
    assert "updated_slides" in catalog
    assert "delete_page_nums" in catalog
    assert "只允许影响目标页" in catalog


def test_selected_scope_rejects_deck_replan_without_explicit_deck_intent():
    validation = validate_content_action_result(
        {"action": "regenerate_plan", "topic": "AI 时代品牌课。重新生成内容规划。"},
        user_message="这里几页的内容有点重复合并成3页去说即可",
        page_context=selected_pages_context(),
        project_context={"total_slides": 46},
    )

    assert not validation.valid
    assert validation.reason == "scope_conflict"
    assert "selected_slides" in validation.message


def test_selected_scope_allows_explicit_deck_replan():
    validation = validate_content_action_result(
        {"action": "regenerate_plan", "topic": "AI 时代品牌课。重新生成内容规划。"},
        user_message="虽然现在选了几页，但我要整套重新规划成 30 页",
        page_context=selected_pages_context(),
        project_context={"total_slides": 46},
    )

    assert validation.valid


def test_merge_slides_accepts_only_selected_pages():
    validation = validate_content_action_result(
        {
            "action": "merge_slides",
            "updated_slides": [
                {"page_num": 3, "text_content": {"headline": "合并后一", "body": "正文"}},
                {"page_num": 4, "text_content": {"headline": "合并后二", "body": "正文"}},
                {"page_num": 5, "text_content": {"headline": "合并后三", "body": "正文"}},
            ],
            "delete_page_nums": [6],
            "response": "已把选中页合并为 3 页。",
        },
        user_message="这里几页的内容有点重复合并成3页去说即可",
        page_context=selected_pages_context(),
        project_context={"total_slides": 46},
    )

    assert validation.valid


def test_merge_slides_rejects_out_of_scope_and_overlapping_pages():
    out_of_scope = validate_content_action_result(
        {
            "action": "merge_slides",
            "updated_slides": [{"page_num": 3, "text_content": {"headline": "合并后一"}}],
            "delete_page_nums": [7],
        },
        user_message="这里几页合并成一页",
        page_context=selected_pages_context(),
        project_context={"total_slides": 46},
    )
    overlap = validate_content_action_result(
        {
            "action": "merge_slides",
            "updated_slides": [{"page_num": 3, "text_content": {"headline": "合并后一"}}],
            "delete_page_nums": [3],
        },
        user_message="这里几页合并成一页",
        page_context=selected_pages_context(),
        project_context={"total_slides": 46},
    )

    assert not out_of_scope.valid
    assert out_of_scope.reason == "scope_conflict"
    assert not overlap.valid
    assert overlap.reason == "payload_conflict"


def test_no_change_response_for_invalid_local_action_does_not_promise_edits():
    validation = validate_content_action_result(
        {"action": "regenerate_plan", "topic": "AI 时代品牌课。重新生成内容规划。"},
        user_message="这里几页合并成三页",
        page_context=selected_pages_context(),
        project_context={"total_slides": 46},
    )

    response = no_change_for_action_validation(validation)

    assert response["action"] == "answer"
    assert response["no_change_reason"] == "content_action_scope_conflict"
    assert "没有修改 PPT" in response["response"]
