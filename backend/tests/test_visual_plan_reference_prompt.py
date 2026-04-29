"""visual_plan LLM prompt 在含参考图时必须强制「参考图应用」结构化说明。"""

from app.services.visual_plan import _build_batch_prompt


def _minimal_page(page_num: int, reference_context: str = "") -> dict:
    return {
        "page_num": page_num,
        "type": "content",
        "headline": "标题",
        "subhead": "",
        "body_preview": "正文预览",
        "existing_visual_suggestion": "",
        "reference_context": reference_context,
    }


def test_batch_prompt_requires_user_facing_reference_copy_when_refs_present():
    style = {"meta": {"palette": ["#1E3A5F", "#F5F5F0"]}, "body": ""}
    pages_summary = [
        _minimal_page(1, ""),
        _minimal_page(
            2,
            "Reference Image 1: role=content_ref; process_mode=blend; analysis=红绿双色动线与方框标签。",
        ),
    ]
    prompt = _build_batch_prompt(pages_summary, style)
    assert "含参考图页面" in prompt
    assert "给用户" in prompt
    assert "大致" in prompt


def test_batch_prompt_skips_reference_block_when_no_refs():
    style = {"meta": {"palette": ["#111"]}, "body": ""}
    pages_summary = [_minimal_page(1, "")]
    prompt = _build_batch_prompt(pages_summary, style)
    assert "含参考图页面 — 画面描述是给用户读的（页号" not in prompt
