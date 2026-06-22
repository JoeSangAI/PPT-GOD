from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

import json_repair

from app.core.llm_client import get_llm_client
from app.core.provider_credentials import get_minimax_llm_model


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
    "delivery_intent": "",
    "requires_clarification": False,
    "confidence": 0.55,
    "rationale": "",
    "evidence": [],
}

LEGACY_TASK_TYPE_BY_DIRECTOR = {
    "direct_replicate": "replicate",
    "polish_existing": "polish",
    "merge_sources": "merge",
    "extract_only": "extract",
    "summary": "restructure",
    "teaching_deck": "restructure",
    "source_to_ppt": "restructure",
}


def _default_contract() -> dict[str, Any]:
    return deepcopy(CONTENT_DIRECTOR_DEFAULT_CONTRACT)


def _clamp_confidence(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def _has_director_shape(value: dict | None) -> bool:
    if not isinstance(value, dict):
        return False
    return (
        any(key in value for key in ALLOWED_CONTENT_DIRECTOR_VALUES)
        or "delivery_intent" in value
        or "requires_clarification" in value
    )


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
    contract["delivery_intent"] = str(value.get("delivery_intent") or "").strip()[:320]

    evidence = value.get("evidence")
    if isinstance(evidence, list):
        contract["evidence"] = [
            str(item).strip()
            for item in evidence
            if str(item or "").strip()
        ][:12]
    return contract


def is_content_director_contract(value: dict | None) -> bool:
    if not isinstance(value, dict):
        return False
    task_type = str(value.get("task_type") or "").strip()
    return _has_director_shape(value) and task_type in ALLOWED_CONTENT_DIRECTOR_VALUES["task_type"]


def _source_excerpt(documents: str, limit: int = 6000) -> str:
    text = str(documents or "").strip()
    if len(text) <= limit:
        return text
    head = text[: int(limit * 0.65)].rstrip()
    tail = text[-int(limit * 0.35):].lstrip()
    return f"{head}\n\n...[中间材料省略]...\n\n{tail}"


def _parse_contract_json(raw: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|```$", "", str(raw or ""), flags=re.MULTILINE).strip()
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


def _derive_delivery_intent_from_brief(brief: str, *, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(brief or "")).strip()
    if not text:
        return ""
    if len(text) > limit:
        text = text[:limit].rstrip(" ，,。；;") + "..."
    return f"根据用户原始需求生成 PPT：{text}"


def _ensure_delivery_intent(contract: dict[str, Any], brief: str) -> dict[str, Any]:
    if str(contract.get("delivery_intent") or "").strip():
        return contract
    delivery_intent = _derive_delivery_intent_from_brief(brief)
    if delivery_intent:
        contract["delivery_intent"] = delivery_intent
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
        "你是内容总监。你的任务不是生成 PPT 页面，而是理解用户真正想要的内容任务契约。\n"
        "只输出 JSON，不要输出解释。\n\n"
        "判断维度：task_type, source_use, coverage, compression, depth, page_budget_policy, structure_policy, "
        "delivery_intent, requires_clarification, confidence, rationale, evidence。\n"
        "delivery_intent 用一句自然语言概括最终 PPT 应服务的场景、读者/听众和表达目标；不要使用固定分类枚举。\n"
        "如果用户要求尽量完整还原严肃材料，应倾向 coverage=near_complete 或 complete, "
        "compression=low, page_budget_policy=source_capacity。\n"
        "如果用户要求总结、提炼、汇报，应倾向 compression=medium/high, page_budget_policy=compact 或 explicit。\n"
        "明确页数永远是 explicit。不要仅靠关键词，结合材料长度、结构和用户目标判断。\n\n"
        f"source_diagnostics: {json.dumps(diagnostics, ensure_ascii=False)}\n\n"
        f"用户需求:\n{brief or ''}\n\n"
        f"材料摘录:\n{_source_excerpt(documents)}"
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
            extra_body={
                "thinking": {"type": "adaptive"},
                "reasoning_split": True,
            },
        )
        raw = response.choices[0].message.content
        return _ensure_delivery_intent(normalize_content_director_contract(_parse_contract_json(raw)), brief)
    except Exception:
        return _fallback_contract()


def content_director_contract_to_legacy_intent(contract: dict | None) -> dict[str, Any]:
    normalized = normalize_content_director_contract(contract)
    task_type = normalized["task_type"]
    source_use = normalized["source_use"]
    page_budget = normalized["page_budget_policy"]
    structure = normalized["structure_policy"]

    rewrite_level = "light"
    if source_use == "verbatim":
        rewrite_level = "none"
    elif source_use == "optimized":
        rewrite_level = "moderate"
    elif source_use == "synthesized":
        rewrite_level = "free"

    page_order_policy = "mostly_preserve"
    if structure == "preserve_order":
        page_order_policy = "preserve"
    elif structure == "reorganize":
        page_order_policy = "can_reorder"

    page_count_policy = "free"
    if page_budget == "same_as_source":
        page_count_policy = "same"
    elif page_budget == "explicit":
        page_count_policy = "target_count"

    return {
        "task_type": LEGACY_TASK_TYPE_BY_DIRECTOR.get(task_type, "restructure"),
        "rewrite_level": rewrite_level,
        "page_order_policy": page_order_policy,
        "page_count_policy": page_count_policy,
        "source_fidelity": source_use if source_use in {"verbatim", "faithful", "optimized", "synthesized"} else "faithful",
        "visual_source_use": "page_reference",
        "confidence": normalized["confidence"],
        "evidence": normalized["evidence"],
    }
