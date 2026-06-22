from app.models.models import Project
from app.api.projects import _normalize_project_intent_contract_for_update
from app.schemas.project import ProjectResponse, ProjectUpdate


def test_project_update_accepts_intent_contract():
    payload = ProjectUpdate(intent_contract={"task_type": "replicate", "confidence": 0.9})

    assert payload.intent_contract == {"task_type": "replicate", "confidence": 0.9}


def test_project_response_exposes_intent_contract():
    fields = ProjectResponse.model_fields

    assert "intent_contract" in fields


def test_project_model_has_intent_contract_column():
    assert "intent_contract" in Project.__table__.columns


def test_project_update_preserves_content_director_contract_fields():
    contract = _normalize_project_intent_contract_for_update({
        "task_type": "teaching_deck",
        "source_use": "faithful",
        "coverage": "near_complete",
        "compression": "low",
        "depth": "deep",
        "page_budget_policy": "source_capacity",
        "structure_policy": "source_order",
        "delivery_intent": "面向一小时课程演讲，保留原文结构和关键表达。",
        "confidence": 0.91,
        "evidence": ["保留原文结构和金句"],
    })

    assert contract["delivery_intent"] == "面向一小时课程演讲，保留原文结构和关键表达。"
    assert contract["coverage"] == "near_complete"
    assert contract["compression"] == "low"
    assert contract["page_budget_policy"] == "source_capacity"
    assert contract["structure_policy"] == "source_order"
