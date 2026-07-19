from app.services.visual_plan import (
    VISUAL_PLAN_SPEAKER_NOTES_MAX_CHARS,
    VISUAL_PLAN_VISIBLE_BODY_MAX_CHARS,
    _build_batch_prompt,
    _sanitize_section_notes_context,
    _visual_plan_context_text,
)


def test_visual_plan_context_keeps_later_list_items():
    context, truncated = _visual_plan_context_text(
        ["数字产品", "柔性制造", "复杂服务", "标准化采购"],
        max_chars=VISUAL_PLAN_VISIBLE_BODY_MAX_CHARS,
    )

    assert truncated is False
    assert "数字产品" in context
    assert "标准化采购" in context


def test_visual_plan_context_keeps_late_paragraph_content():
    late_evidence = "最后必须保留的商业证据"
    context, truncated = _visual_plan_context_text(
        "第一段解释。\n" + ("中间论证。" * 80) + f"\n{late_evidence}",
        max_chars=VISUAL_PLAN_VISIBLE_BODY_MAX_CHARS,
    )

    assert truncated is False
    assert late_evidence in context


def test_visual_plan_context_reports_safety_truncation():
    context, truncated = _visual_plan_context_text(
        "讲稿" * (VISUAL_PLAN_SPEAKER_NOTES_MAX_CHARS + 100),
        max_chars=VISUAL_PLAN_SPEAKER_NOTES_MAX_CHARS,
    )

    assert truncated is True
    assert context.endswith("[context truncated]")


def test_visual_plan_prompt_treats_speaker_notes_as_non_visible_context():
    prompt = _build_batch_prompt(
        pages_summary=[
            {
                "page_num": 1,
                "type": "content",
                "headline": "创造需求",
                "body_context": "AI 可以提出新的生活可能",
                "speaker_notes_context": "爱人的 Agent 曾经聊到想去海边，另一个 Agent 查到特价机票。",
                "context_truncated": {"body": False, "speaker_notes": False},
            }
        ],
        style={"meta": {"theme": "克制商务", "mood": "清晰", "palette": ["#111111", "#D4AF37"]}, "body": ""},
    )

    assert "视觉方案必须覆盖其中所有关键对象和要点" in prompt
    assert "speaker_notes_context 是不可见的演讲者讲稿" in prompt
    assert "绝对不能把讲稿原句" in prompt


def test_visual_plan_prompt_does_not_default_dense_pages_to_cards():
    prompt = _build_batch_prompt(
        pages_summary=[
            {
                "page_num": 2,
                "type": "content",
                "headline": "复杂判断",
                "body_context": "三个并列判断",
            }
        ],
        style={"meta": {"theme": "克制商务", "mood": "清晰", "palette": ["#111111", "#D4AF37"]}, "body": ""},
    )

    assert "卡片只在语义需要分组时使用" in prompt
    assert "不要把卡片和图标当成默认装饰" in prompt


def test_section_notes_do_not_reintroduce_hidden_numbering():
    notes = _sanitize_section_notes_context(
        "转场到第六章，并提示接下来进入 1.1。",
        visible_text="什么变了？决策不再只发生在人脑里",
    )

    assert "第六章" not in notes
    assert "1.1" not in notes


def test_section_notes_keep_numbering_when_it_is_visible_copy():
    notes = _sanitize_section_notes_context(
        "接下来进入 1.1。",
        visible_text="1.1 决策不再只发生在人脑里",
    )

    assert "1.1" in notes
