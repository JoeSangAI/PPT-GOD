from app.api.chat import _source_intent_context_text


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
