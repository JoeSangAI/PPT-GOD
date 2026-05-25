# Content Director Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace keyword-led content-planning decisions with a Content Director Agent that understands the user's task contract before page planning.

**Architecture:** Add a small content-director layer that produces a structured intent contract from the user brief and source diagnostics. Keep one unified content-planning pipeline; long/short behavior is selected by contract fields and objective source profile, not by ad hoc prompt branches. Deterministic code remains responsible for explicit constraints and quality gates.

**Tech Stack:** Python 3.12, pytest, existing LLM client in `backend/app/core/llm_client.py`, existing content planning service in `backend/app/services/content_plan.py`.

---

## File Structure

- Create: `backend/app/services/content_director.py`
  - Owns the Content Director schema, LLM prompt, JSON parsing, normalization, and low-confidence fallback.
- Modify: `backend/app/services/source_intent.py`
  - Keep compatibility for existing PPT-source intent behavior, but stop treating it as the only content-planning intent model.
- Modify: `backend/app/services/content_plan.py`
  - Consume the director contract in `_build_content_plan_job`, page target resolution, strategy selection, prompt policy text, and long-deck routing.
  - Remove the temporary restoration keyword helpers once contract-driven page targeting is in place.
- Test: `backend/tests/test_content_director.py`
  - New unit tests for contract normalization, LLM parsing, fallback, and intent understanding.
- Modify: `backend/tests/test_content_plan_policy.py`
  - Add contract-driven page targeting tests and remove reliance on restoration keyword matching.
- Modify: `backend/tests/test_source_intent.py`
  - Preserve legacy PPT-specific behavior while proving the new director contract can coexist.
- Optional docs update: `docs/requirements/README.md`
  - Add one short pointer to this plan only if implementation materially changes workflow behavior.

## Contract Shape

The Content Director contract should be compact and stable:

```python
CONTENT_DIRECTOR_DEFAULT_CONTRACT = {
    "task_type": "source_to_ppt",
    "source_use": "faithful",
    "coverage": "balanced",
    "compression": "medium",
    "depth": "standard",
    "page_budget_policy": "auto",
    "structure_policy": "source_order",
    "requires_clarification": False,
    "confidence": 0.55,
    "rationale": "",
    "evidence": [],
}
```

Allowed values:

```python
ALLOWED_CONTENT_DIRECTOR_VALUES = {
    "task_type": {
        "source_to_ppt",
        "summary",
        "teaching_deck",
        "direct_replicate",
        "polish_existing",
        "merge_sources",
        "extract_only",
    },
    "source_use": {"verbatim", "faithful", "optimized", "synthesized"},
    "coverage": {"selective", "balanced", "near_complete", "complete"},
    "compression": {"low", "medium", "high"},
    "depth": {"brief", "standard", "deep"},
    "page_budget_policy": {"explicit", "same_as_source", "compact", "source_capacity", "auto"},
    "structure_policy": {"preserve_order", "source_order", "reorganize"},
}
```

## Task 1: Add Content Director Schema and Normalization

**Files:**
- Create: `backend/app/services/content_director.py`
- Test: `backend/tests/test_content_director.py`

- [ ] **Step 1: Write normalization tests**

```python
from app.services.content_director import normalize_content_director_contract


def test_normalize_content_director_contract_accepts_restoration_contract():
    contract = normalize_content_director_contract({
        "task_type": "teaching_deck",
        "source_use": "faithful",
        "coverage": "near_complete",
        "compression": "low",
        "depth": "deep",
        "page_budget_policy": "source_capacity",
        "structure_policy": "source_order",
        "confidence": 0.91,
        "rationale": "用户要求尽量完整体现讲稿原本内容。",
        "evidence": ["尽可能地还原原文意思", "尽量完整地体现"],
    })

    assert contract["task_type"] == "teaching_deck"
    assert contract["coverage"] == "near_complete"
    assert contract["compression"] == "low"
    assert contract["page_budget_policy"] == "source_capacity"
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
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
python3 -m pytest tests/test_content_director.py -q
```

Expected: FAIL because `app.services.content_director` does not exist.

- [ ] **Step 3: Implement schema and normalization**

Create `backend/app/services/content_director.py` with:

```python
from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

import json_repair


ALLOWED_CONTENT_DIRECTOR_VALUES = {
    "task_type": {
        "source_to_ppt",
        "summary",
        "teaching_deck",
        "direct_replicate",
        "polish_existing",
        "merge_sources",
        "extract_only",
    },
    "source_use": {"verbatim", "faithful", "optimized", "synthesized"},
    "coverage": {"selective", "balanced", "near_complete", "complete"},
    "compression": {"low", "medium", "high"},
    "depth": {"brief", "standard", "deep"},
    "page_budget_policy": {"explicit", "same_as_source", "compact", "source_capacity", "auto"},
    "structure_policy": {"preserve_order", "source_order", "reorganize"},
}

CONTENT_DIRECTOR_DEFAULT_CONTRACT: dict[str, Any] = {
    "task_type": "source_to_ppt",
    "source_use": "faithful",
    "coverage": "balanced",
    "compression": "medium",
    "depth": "standard",
    "page_budget_policy": "auto",
    "structure_policy": "source_order",
    "requires_clarification": False,
    "confidence": 0.55,
    "rationale": "",
    "evidence": [],
}


def _default_contract() -> dict[str, Any]:
    return deepcopy(CONTENT_DIRECTOR_DEFAULT_CONTRACT)


def _clamp_confidence(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def normalize_content_director_contract(value: dict | None) -> dict[str, Any]:
    contract = _default_contract()
    if not isinstance(value, dict):
        return contract

    for key, allowed in ALLOWED_CONTENT_DIRECTOR_VALUES.items():
        raw = str(value.get(key) or "").strip()
        if raw in allowed:
            contract[key] = raw

    contract["requires_clarification"] = bool(value.get("requires_clarification"))
    contract["confidence"] = _clamp_confidence(value.get("confidence"), contract["confidence"])
    contract["rationale"] = str(value.get("rationale") or "").strip()[:600]

    evidence = value.get("evidence")
    if isinstance(evidence, list):
        contract["evidence"] = [
            str(item).strip()
            for item in evidence
            if str(item or "").strip()
        ][:12]
    return contract
```

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
python3 -m pytest tests/test_content_director.py -q
```

Expected: PASS.

## Task 2: Add LLM-Backed Content Director Inference

**Files:**
- Modify: `backend/app/services/content_director.py`
- Test: `backend/tests/test_content_director.py`

- [ ] **Step 1: Write LLM parsing tests**

```python
import json

from app.services.content_director import infer_content_director_contract


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
        return FakeResponse(json.dumps({
            "task_type": "teaching_deck",
            "source_use": "faithful",
            "coverage": "near_complete",
            "compression": "low",
            "depth": "deep",
            "page_budget_policy": "source_capacity",
            "structure_policy": "source_order",
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
    assert contract["confidence"] >= 0.9
```

- [ ] **Step 2: Write fallback test**

```python
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
```

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
python3 -m pytest tests/test_content_director.py -q
```

Expected: FAIL because `infer_content_director_contract` is missing.

- [ ] **Step 4: Implement LLM inference**

Add to `backend/app/services/content_director.py`:

```python
from app.core.llm_client import get_llm_client
from app.core.provider_credentials import get_minimax_llm_model


def _source_excerpt(documents: str, limit: int = 6000) -> str:
    text = str(documents or "").strip()
    if len(text) <= limit:
        return text
    return text[: int(limit * 0.65)].rstrip() + "\n\n...[中间材料省略]...\n\n" + text[-int(limit * 0.35):].lstrip()


def _parse_contract_json(raw: str) -> dict:
    text = re.sub(r"^```(?:json)?\\s*|```$", "", str(raw or ""), flags=re.MULTILINE).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]
    parsed = json_repair.loads(text)
    return parsed if isinstance(parsed, dict) else {}


def _fallback_contract() -> dict[str, Any]:
    contract = _default_contract()
    contract["confidence"] = 0.45
    contract["requires_clarification"] = False
    contract["rationale"] = "模型意图识别不可用，使用保守默认契约。"
    return contract


def infer_content_director_contract(
    *,
    brief: str,
    documents: str,
    source_diagnostics: dict | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    diagnostics = source_diagnostics if isinstance(source_diagnostics, dict) else {}
    prompt = (
        "你是内容总监。你的任务不是生成 PPT 页面，而是理解用户真正想要的内容任务契约。\\n"
        "只输出 JSON，不要输出解释。\\n\\n"
        "判断维度：task_type, source_use, coverage, compression, depth, page_budget_policy, structure_policy, "
        "requires_clarification, confidence, rationale, evidence。\\n"
        "如果用户要求尽量完整还原严肃材料，应倾向 coverage=near_complete 或 complete, "
        "compression=low, page_budget_policy=source_capacity。\\n"
        "如果用户要求总结、提炼、汇报，应倾向 compression=medium/high, page_budget_policy=compact 或 explicit。\\n"
        "明确页数永远是 explicit。不要仅靠关键词，结合材料长度、结构和用户目标判断。\\n\\n"
        f"source_diagnostics: {json.dumps(diagnostics, ensure_ascii=False)}\\n\\n"
        f"用户需求:\\n{brief or ''}\\n\\n"
        f"材料摘录:\\n{_source_excerpt(documents)}"
    )
    try:
        client = get_llm_client()
        response = client.chat.completions.create(
            model=get_minimax_llm_model(),
            messages=[
                {"role": "system", "content": "你是 PPT 内容总监，只输出严格 JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            timeout=timeout,
        )
        raw = response.choices[0].message.content
        return normalize_content_director_contract(_parse_contract_json(raw))
    except Exception:
        return _fallback_contract()
```

- [ ] **Step 5: Run tests and verify GREEN**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
python3 -m pytest tests/test_content_director.py -q
```

Expected: PASS.

## Task 3: Wire the Contract Into Page Targeting

**Files:**
- Modify: `backend/app/services/content_plan.py`
- Test: `backend/tests/test_content_plan_policy.py`

- [ ] **Step 1: Write contract-driven page target tests**

```python
from app.services import content_plan as content_plan_module
from app.services.content_plan import resolve_content_plan_page_target, should_generate_incremental_long_deck


def test_source_capacity_contract_expands_without_keyword_matching():
    documents = "# 课程讲稿\n\n" + "\n\n".join(
        f"## 模块 {idx}\n\n" + "严肃课程正文，需要保留判断、案例、追问和行动要求。" * 40
        for idx in range(1, 13)
    )
    contract = {
        "task_type": "teaching_deck",
        "source_use": "faithful",
        "coverage": "near_complete",
        "compression": "low",
        "depth": "deep",
        "page_budget_policy": "source_capacity",
        "structure_policy": "source_order",
        "confidence": 0.9,
        "evidence": ["contract supplied by content director"],
    }

    target, min_pages, max_pages = resolve_content_plan_page_target(
        "请做成一份 PPT",
        None,
        documents,
        intent_contract=contract,
    )

    assert target >= 60
    assert min_pages >= 40
    assert max_pages == target
    assert should_generate_incremental_long_deck("请做成一份 PPT", None, documents, intent_contract=contract)


def test_compact_summary_contract_stays_compact_for_same_document():
    documents = "# 课程讲稿\n\n" + "\n\n".join(
        f"## 模块 {idx}\n\n" + "严肃课程正文，需要保留判断、案例、追问和行动要求。" * 40
        for idx in range(1, 13)
    )
    contract = {
        "task_type": "summary",
        "source_use": "optimized",
        "coverage": "selective",
        "compression": "high",
        "depth": "brief",
        "page_budget_policy": "compact",
        "structure_policy": "reorganize",
        "confidence": 0.9,
        "evidence": ["用户要求总结提炼"],
    }

    target, min_pages, max_pages = resolve_content_plan_page_target(
        "请做成一份 PPT",
        None,
        documents,
        intent_contract=contract,
    )

    assert target < 40
    assert max_pages < 40
    assert not should_generate_incremental_long_deck("请做成一份 PPT", None, documents, intent_contract=contract)
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
python3 -m pytest tests/test_content_plan_policy.py -q -k "source_capacity_contract or compact_summary_contract"
```

Expected: FAIL because `resolve_content_plan_page_target` and `should_generate_incremental_long_deck` do not accept `intent_contract`.

- [ ] **Step 3: Update function signatures and logic**

Change signatures in `backend/app/services/content_plan.py`:

```python
def resolve_content_plan_page_target(
    topic: str,
    page_count: int | None,
    documents: str = "",
    intent_contract: dict | None = None,
) -> tuple[int, int, int]:
    ...


def should_generate_incremental_long_deck(
    topic: str,
    page_count: int | None,
    documents: str = "",
    intent_contract: dict | None = None,
) -> bool:
    target_count, min_pages, max_pages = resolve_content_plan_page_target(topic, page_count, documents, intent_contract)
    return _should_generate_deck_blueprint((min_pages, max_pages), target_count, documents)
```

Inside `resolve_content_plan_page_target`, compute:

```python
contract = normalize_content_director_contract(intent_contract) if intent_contract else None
uses_source_capacity = bool(
    contract
    and contract["page_budget_policy"] == "source_capacity"
    and contract["coverage"] in {"near_complete", "complete"}
    and contract["compression"] == "low"
)
```

Then replace the temporary restoration keyword path with:

```python
restoration_page_count = (
    None
    if explicit_page_count or requested_page_range or not uses_source_capacity
    else _infer_source_capacity_page_count(documents)
)
```

Rename `_infer_restoration_priority_page_count` to `_infer_source_capacity_page_count(documents: str)` and remove `_is_source_restoration_priority_request`.

- [ ] **Step 4: Update job builder to pass the contract**

In `_build_content_plan_job`, after `effective_intent_contract` is computed:

```python
resolved_page_count, min_pages, max_pages = resolve_content_plan_page_target(
    topic,
    page_count,
    documents,
    intent_contract=effective_intent_contract,
)
```

- [ ] **Step 5: Run tests and verify GREEN**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
python3 -m pytest tests/test_content_plan_policy.py -q -k "source_capacity_contract or compact_summary_contract"
```

Expected: PASS.

## Task 4: Make `_effective_intent_contract` Use the Content Director

**Files:**
- Modify: `backend/app/services/content_plan.py`
- Test: `backend/tests/test_content_plan_policy.py`

- [ ] **Step 1: Write integration test**

```python
def test_build_content_plan_job_uses_content_director_contract(monkeypatch):
    documents = "# 课程讲稿\n\n" + "\n\n".join(
        f"## 模块 {idx}\n\n" + "严肃课程正文，需要完整展开。" * 60
        for idx in range(1, 13)
    )

    def fake_director(**kwargs):
        assert "尽量完整" in kwargs["brief"]
        return {
            "task_type": "teaching_deck",
            "source_use": "faithful",
            "coverage": "near_complete",
            "compression": "low",
            "depth": "deep",
            "page_budget_policy": "source_capacity",
            "structure_policy": "source_order",
            "confidence": 0.91,
            "evidence": ["尽量完整"],
        }

    monkeypatch.setattr(content_plan_module, "infer_content_director_contract", fake_director)

    job = content_plan_module._build_content_plan_job(
        topic="请把这份讲稿做成 PPT，尽量完整体现内容。",
        documents=documents,
    )

    assert job.intent_contract["page_budget_policy"] == "source_capacity"
    assert job.page_count >= 60
    assert content_plan_module._select_content_plan_strategy(job) == "long_structured_deck"
```

- [ ] **Step 2: Run test and verify RED**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
python3 -m pytest tests/test_content_plan_policy.py -q -k "build_content_plan_job_uses_content_director_contract"
```

Expected: FAIL because `content_plan.py` still imports the old source-intent inference directly.

- [ ] **Step 3: Wire the director**

In `backend/app/services/content_plan.py`, import:

```python
from app.services.content_director import (
    infer_content_director_contract,
    normalize_content_director_contract,
)
```

Update `_effective_intent_contract`:

```python
def _effective_intent_contract(topic: str, documents: str, intent_contract: dict | None = None) -> dict:
    if intent_contract is not None:
        return normalize_content_director_contract(intent_contract)
    return infer_content_director_contract(
        brief=topic,
        documents=documents,
        source_diagnostics=_content_director_source_diagnostics(documents),
    )
```

Add `_content_director_source_diagnostics` near existing page-count helpers:

```python
def _content_director_source_diagnostics(documents: str) -> dict:
    text = sanitize_ppt_recovery_text_for_content(documents)
    units = extract_document_outline_units(text)
    return {
        "char_count": len(text),
        "line_count": len([line for line in text.splitlines() if line.strip()]),
        "heading_count": len([unit for unit in units if int(unit.get("level") or 9) <= 4]),
        "estimated_page_capacity": _estimate_document_page_capacity(text),
        **source_diagnostics_from_documents(text),
    }
```

- [ ] **Step 4: Preserve legacy PPT source behavior**

If `detect_ppt_sources(documents)` returns PPT sources and the director confidence is below `0.65`, merge in the old `infer_intent_contract` result for `direct_replicate`, `polish_existing`, and `same_as_source` decisions. Keep this in `_effective_intent_contract`, not scattered through page planning.

- [ ] **Step 5: Run tests and verify GREEN**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
python3 -m pytest tests/test_content_plan_policy.py tests/test_source_intent.py tests/test_content_plan_intent_contract.py -q
```

Expected: PASS.

## Task 5: Update Prompt Policy Text to Follow the Contract

**Files:**
- Modify: `backend/app/services/content_plan.py`
- Test: `backend/tests/test_content_plan_policy.py`

- [ ] **Step 1: Write policy-text test**

```python
def test_director_contract_policy_text_describes_restoration_requirements():
    text = content_plan_module._intent_contract_policy_text({
        "task_type": "teaching_deck",
        "source_use": "faithful",
        "coverage": "near_complete",
        "compression": "low",
        "depth": "deep",
        "page_budget_policy": "source_capacity",
        "structure_policy": "source_order",
        "confidence": 0.9,
        "evidence": ["尽量完整"],
    })

    assert "尽量完整覆盖上传材料" in text
    assert "不要压缩成摘要" in text
    assert "保留原文结构和讲述顺序" in text
```

- [ ] **Step 2: Run test and verify RED**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
python3 -m pytest tests/test_content_plan_policy.py -q -k "director_contract_policy_text"
```

Expected: FAIL because policy text still maps only old source-intent values.

- [ ] **Step 3: Implement policy text**

Update `_intent_contract_policy_text` to inspect Content Director fields:

```python
if contract["page_budget_policy"] == "source_capacity" and contract["coverage"] in {"near_complete", "complete"}:
    lines.append("- 尽量完整覆盖上传材料，不要压缩成摘要。")
if contract["compression"] == "low":
    lines.append("- 每页保留具体判断、案例、追问或行动要求，避免只写抽象概括。")
if contract["structure_policy"] in {"source_order", "preserve_order"}:
    lines.append("- 保留原文结构和讲述顺序，除非用户明确要求重组。")
if contract["depth"] == "deep":
    lines.append("- 内容需要有课程深度，正文应可支持现场讲解。")
```

Return an empty string only when no policy line applies.

- [ ] **Step 4: Run test and verify GREEN**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
python3 -m pytest tests/test_content_plan_policy.py -q -k "director_contract_policy_text"
```

Expected: PASS.

## Task 6: Remove Temporary Keyword Restoration Logic

**Files:**
- Modify: `backend/app/services/content_plan.py`
- Modify: `backend/tests/test_content_plan_policy.py`

- [ ] **Step 1: Delete keyword-only helpers**

Remove from `backend/app/services/content_plan.py`:

```python
def _is_source_restoration_priority_request(...):
    ...

def _infer_restoration_priority_page_count(...):
    ...
```

Keep or rename the capacity calculation as:

```python
def _infer_source_capacity_page_count(documents: str) -> int | None:
    ...
```

This function must not inspect the user prompt.

- [ ] **Step 2: Replace tests that assert keyword behavior**

Keep the user-facing regression test, but make it assert the director output path by monkeypatching `infer_content_director_contract`, not by relying on keyword matching inside page-count code.

- [ ] **Step 3: Verify no helper references remain**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god
rg "_is_source_restoration_priority_request|_infer_restoration_priority_page_count" backend
```

Expected: no output.

- [ ] **Step 4: Run policy tests**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
python3 -m pytest tests/test_content_director.py tests/test_content_plan_policy.py tests/test_source_intent.py tests/test_content_plan_intent_contract.py -q
```

Expected: PASS.

## Task 7: Add End-to-End Regression for the Real Manuscript Prompt

**Files:**
- Modify: `backend/tests/test_content_plan_policy.py`

- [ ] **Step 1: Add regression test with real-like manuscript**

```python
def test_real_manuscript_prompt_routes_to_long_deck_through_content_director(monkeypatch):
    documents = _medium_length_course_manuscript()

    monkeypatch.setattr(content_plan_module, "infer_content_director_contract", lambda **kwargs: {
        "task_type": "teaching_deck",
        "source_use": "faithful",
        "coverage": "near_complete",
        "compression": "low",
        "depth": "deep",
        "page_budget_policy": "source_capacity",
        "structure_policy": "source_order",
        "confidence": 0.92,
        "evidence": ["不要受时长和页数的影响", "尽量完整地体现"],
    })

    job = content_plan_module._build_content_plan_job(
        topic="【文件：赢利与天龙十部完整演讲内容稿.md】 把这一份内容讲稿制作成一个 PPT，要尽可能地还原原文意思。不要受时长和页数的影响，尽量完整地体现讲稿原本的内容。",
        documents=documents,
    )

    assert job.page_count >= 60
    assert job.min_pages >= 40
    assert job.intent_contract["page_budget_policy"] == "source_capacity"
    assert content_plan_module._select_content_plan_strategy(job) == "long_structured_deck"
```

- [ ] **Step 2: Run regression test**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
python3 -m pytest tests/test_content_plan_policy.py -q -k "real_manuscript_prompt_routes"
```

Expected: PASS.

## Task 8: Full Verification

**Files:**
- No edits.

- [ ] **Step 1: Run backend policy and intent tests**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
python3 -m pytest tests/test_content_director.py tests/test_source_intent.py tests/test_content_plan_intent_contract.py tests/test_content_plan_policy.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run syntax check**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
python3 -m py_compile app/services/content_director.py app/services/content_plan.py app/services/source_intent.py
```

Expected: exit code 0.

- [ ] **Step 3: Run frontend smoke tests because pipeline state is user-visible**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/frontend
node src/project-isolation.test.mjs
npm run build
```

Expected: tests pass and build succeeds. Existing Vite chunk-size warning is acceptable.

- [ ] **Step 4: Rebuild local backend**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god
PPTGOD_HOST_PORT=8000 docker compose up -d --build backend
curl -sS http://localhost:8000/health
```

Expected: Docker rebuild succeeds and health check returns `{"status":"ok"}`.

## Self-Review

- Spec coverage: The plan covers intent understanding, schema, LLM inference, fallback, page targeting, prompt policy, temporary heuristic removal, and regression tests.
- Placeholder scan: No placeholder markers remain.
- Type consistency: Contract fields are consistent across schema, tests, and content-plan consumption.
- Scope check: This plan intentionally avoids visual design and PPT rendering changes. It is focused on content planning intent and page-budget strategy only.
