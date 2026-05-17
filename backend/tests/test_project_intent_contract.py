from app.models.models import Project
from app.schemas.project import ProjectResponse, ProjectUpdate


def test_project_update_accepts_intent_contract():
    payload = ProjectUpdate(intent_contract={"task_type": "replicate", "confidence": 0.9})

    assert payload.intent_contract == {"task_type": "replicate", "confidence": 0.9}


def test_project_response_exposes_intent_contract():
    fields = ProjectResponse.model_fields

    assert "intent_contract" in fields


def test_project_model_has_intent_contract_column():
    assert "intent_contract" in Project.__table__.columns
