from app.api.chat import _build_draft_prompt, _source_intent_context_text


def test_source_intent_context_uses_outcome_language():
    text = _source_intent_context_text({
        "intent_contract": {
            "task_type": "polish",
            "rewrite_level": "light",
            "page_order_policy": "preserve",
            "page_count_policy": "same",
            "source_fidelity": "faithful",
            "visual_source_use": "page_reference",
            "confidence": 0.7,
            "evidence": ["做得更好"],
        }
    })

    assert "保留原页顺序" in text
    assert "优化标题和正文" in text
    assert "task_type" not in text
    assert "polish" not in text


def test_source_intent_context_is_empty_without_contract():
    assert _source_intent_context_text({}) == ""


def test_source_intent_context_supports_content_director_contract():
    text = _source_intent_context_text({
        "intent_contract": {
            "task_type": "teaching_deck",
            "source_use": "faithful",
            "coverage": "near_complete",
            "compression": "low",
            "depth": "deep",
            "page_budget_policy": "source_capacity",
            "structure_policy": "source_order",
            "confidence": 0.9,
            "evidence": ["尽量完整体现讲稿"],
        }
    })

    assert "充分使用上传材料" in text
    assert "不要机械照搬原格式" in text
    assert "teaching_deck" not in text
    assert "source_capacity" not in text


def test_source_intent_context_prefers_open_delivery_intent():
    text = _source_intent_context_text({
        "intent_contract": {
            "task_type": "teaching_deck",
            "source_use": "faithful",
            "coverage": "near_complete",
            "compression": "low",
            "depth": "deep",
            "page_budget_policy": "source_capacity",
            "structure_policy": "source_order",
            "delivery_intent": "面向一小时课程演讲，保留原文结构和关键表达。",
            "confidence": 0.9,
            "evidence": ["尽量完整体现讲稿"],
        }
    })

    assert "最终 PPT 应服务于：面向一小时课程演讲，保留原文结构和关键表达。" in text
    assert "保留关键事实、结构和用户明确要求保留的表达" in text
    assert "teaching_deck" not in text
    assert "source_capacity" not in text
    assert "delivery_intent" not in text


def test_draft_prompt_uses_open_delivery_intent_instead_of_scene_enum():
    prompt = _build_draft_prompt(has_documents=True)

    assert "delivery_intent" in prompt
    assert "场景推断规则" not in prompt
    assert "scene_type" not in prompt
    assert "reading" not in prompt
    assert "presentation" not in prompt
    assert "mixed" not in prompt
    assert "genre" not in prompt.lower()
