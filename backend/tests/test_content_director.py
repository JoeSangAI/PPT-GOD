import json

from app.services.content_director import (
    infer_content_director_contract,
    normalize_content_director_contract,
)


def test_normalize_content_director_contract_accepts_restoration_contract():
    contract = normalize_content_director_contract({
        "task_type": "teaching_deck",
        "source_use": "faithful",
        "coverage": "near_complete",
        "compression": "low",
        "depth": "deep",
        "page_budget_policy": "source_capacity",
        "structure_policy": "source_order",
        "delivery_intent": "面向一小时课程演讲，尽可能保留原文结构和金句。",
        "confidence": 0.91,
        "rationale": "用户要求尽量完整体现讲稿原本内容。",
        "evidence": ["尽可能地还原原文意思", "尽量完整地体现"],
    })

    assert contract["task_type"] == "teaching_deck"
    assert contract["coverage"] == "near_complete"
    assert contract["compression"] == "low"
    assert contract["page_budget_policy"] == "source_capacity"
    assert contract["delivery_intent"] == "面向一小时课程演讲，尽可能保留原文结构和金句。"
    assert contract["confidence"] == 0.91
    assert contract["evidence"] == ["尽可能地还原原文意思", "尽量完整地体现"]


def test_normalize_content_director_contract_rejects_unknown_values():
    contract = normalize_content_director_contract({
        "task_type": "magic",
        "source_use": "hallucinate",
        "coverage": "everything forever",
        "confidence": 2,
        "evidence": ["x"] * 20,
    })

    assert contract["task_type"] == "source_to_ppt"
    assert contract["source_use"] == "faithful"
    assert contract["coverage"] == "balanced"
    assert contract["confidence"] == 1.0
    assert len(contract["evidence"]) == 12


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content):
        self.message = FakeMessage(content)


class FakeResponse:
    def __init__(self, content):
        self.choices = [FakeChoice(content)]


class FakeCompletions:
    def create(self, **kwargs):
        prompt = kwargs["messages"][1]["content"]
        assert "你是内容总监" in prompt
        assert "只输出 JSON" in prompt
        assert "source_diagnostics" in prompt
        assert "delivery_intent 用一句自然语言概括" in prompt
        assert "固定分类枚举" in prompt
        assert kwargs["extra_body"]["thinking"]["type"] == "adaptive"
        assert kwargs["extra_body"]["reasoning_split"] is True
        return FakeResponse(json.dumps({
            "task_type": "teaching_deck",
            "source_use": "faithful",
            "coverage": "near_complete",
            "compression": "low",
            "depth": "deep",
            "page_budget_policy": "source_capacity",
            "structure_policy": "source_order",
            "delivery_intent": "面向一小时课程演讲，尽可能保留原文结构和金句。",
            "confidence": 0.92,
            "rationale": "用户要求尽量完整还原讲稿。",
            "evidence": ["尽可能地还原原文意思"],
        }, ensure_ascii=False))


class FakeChat:
    completions = FakeCompletions()


class FakeClient:
    chat = FakeChat()


def test_infer_content_director_contract_uses_llm_contract(monkeypatch):
    monkeypatch.setattr("app.services.content_director.get_llm_client", lambda: FakeClient())

    contract = infer_content_director_contract(
        brief="把讲稿做成 PPT，要尽可能还原原文意思，尽量完整体现。",
        documents="## 第一部分\n正文" * 200,
        source_diagnostics={"char_count": 12000, "heading_count": 12},
    )

    assert contract["task_type"] == "teaching_deck"
    assert contract["coverage"] == "near_complete"
    assert contract["page_budget_policy"] == "source_capacity"
    assert contract["delivery_intent"] == "面向一小时课程演讲，尽可能保留原文结构和金句。"
    assert contract["confidence"] >= 0.9


def test_infer_content_director_contract_derives_delivery_intent_when_model_omits_it(monkeypatch):
    class MissingDeliveryCompletions:
        def create(self, **kwargs):
            return FakeResponse(json.dumps({
                "task_type": "teaching_deck",
                "source_use": "faithful",
                "coverage": "near_complete",
                "compression": "low",
                "depth": "deep",
                "page_budget_policy": "explicit",
                "structure_policy": "source_order",
                "confidence": 0.93,
                "rationale": "用户要求将长文档做成约 50 页课程 PPT。",
                "evidence": ["1 小时左右", "50 页左右", "保留结构和金句"],
            }, ensure_ascii=False))

    class MissingDeliveryChat:
        completions = MissingDeliveryCompletions()

    class MissingDeliveryClient:
        chat = MissingDeliveryChat()

    monkeypatch.setattr("app.services.content_director.get_llm_client", lambda: MissingDeliveryClient())

    contract = infer_content_director_contract(
        brief="把《AI时代消费者决策路径与品牌策略》做成 1 小时左右课程 PPT，大约 50 页，保留原来文件的结构和金句。",
        documents="## 序章\n正文" * 200,
        source_diagnostics={"char_count": 12820, "heading_count": 28},
    )

    assert contract["delivery_intent"]
    assert "1 小时左右课程 PPT" in contract["delivery_intent"]
    assert "保留原来文件的结构和金句" in contract["delivery_intent"]
    assert "genre" not in contract["delivery_intent"].lower()


def test_infer_content_director_contract_falls_back_low_confidence(monkeypatch):
    class BrokenCompletions:
        def create(self, **kwargs):
            raise RuntimeError("model unavailable")

    class BrokenChat:
        completions = BrokenCompletions()

    class BrokenClient:
        chat = BrokenChat()

    monkeypatch.setattr("app.services.content_director.get_llm_client", lambda: BrokenClient())

    contract = infer_content_director_contract(
        brief="帮我做成 PPT",
        documents="短材料",
        source_diagnostics={"char_count": 3, "heading_count": 0},
    )

    assert contract["task_type"] == "source_to_ppt"
    assert contract["confidence"] <= 0.55
