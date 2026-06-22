from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from app.services.document_parser import detect_ppt_sources


ALLOWED_VALUES = {
    "task_type": {"replicate", "polish", "restructure", "extract", "merge", "template_reference"},
    "rewrite_level": {"none", "light", "moderate", "free"},
    "page_order_policy": {"preserve", "mostly_preserve", "can_reorder"},
    "page_count_policy": {"same", "similar", "target_count", "free"},
    "source_fidelity": {"verbatim", "faithful", "optimized", "synthesized"},
    "visual_source_use": {"page_reference", "style_reference", "asset_library", "ignore"},
}

DEFAULT_INTENT_CONTRACT = {
    "task_type": "polish",
    "rewrite_level": "light",
    "page_order_policy": "preserve",
    "page_count_policy": "same",
    "source_fidelity": "faithful",
    "visual_source_use": "page_reference",
    "confidence": 0.55,
    "evidence": [],
}

_REPLICATE_STRONG_CUES = (
    r"1\s*[:：]\s*1",
    r"一比一",
    r"复刻",
    r"原样",
    r"照搬",
    r"逐页还原",
    r"内容(?:尽量)?不要动",
    r"内容(?:尽量)?不动",
    r"不改内容",
    r"不要改(?:原文|内容)",
    r"verbatim",
    r"exact\s+copy",
    r"copy\s+exactly",
)
_PAGE_ORDER_CUES = (
    r"页码(?:等信息)?(?:尽量)?保持不变",
    r"页数(?:尽量)?保持不变",
    r"页序(?:尽量)?不(?:要)?(?:改|乱|动)",
    r"页序别乱",
    r"顺序(?:尽量)?不(?:要)?(?:改|乱|动)",
    r"保留(?:原)?页序",
    r"保持(?:原)?顺序",
    r"按原页",
    r"same\s+order",
)
_SOURCE_PRESERVATION_CUES = (
    r"原话(?:和原文)?(?:内容)?(?:基本上?|尽量)?保持不变",
    r"原文(?:内容)?(?:基本上?|尽量)?保持不变",
    r"原话(?:和原文)?(?:内容)?(?:基本上?|尽量)?不变",
    r"原文(?:内容)?(?:基本上?|尽量)?不变",
    r"原文(?:内容)?(?:的)?字眼",
    r"原文字眼",
    r"(?:保留|保持).{0,12}(?:原文|原话|原句).{0,12}(?:结构|金句|主线|要点|内容)",
    r"(?:保留|保持).{0,12}(?:原文|原话|原句).{0,12}(?:字眼|措辞|表达|说法)",
    r"(?:尽量|不要|别).{0,12}改.{0,12}(?:原文|原话|原句|字眼|措辞|表达|内容)",
    r"完整还原(?:原文|材料|讲稿|内容)?",
    r"文字(?:信息|内容)?(?:基本上?|尽量)?保持不变",
    r"内容(?:基本上?|尽量)?保持不变",
)
_POLISH_CUES = (
    r"优化",
    r"美化",
    r"润色",
    r"升级",
    r"做得更好",
    r"做成(?:一个|一份)?更好",
    r"重新整理",
    r"整理成更好的",
    r"标题.*(?:重组|优化)",
    r"正文.*(?:重组|优化)",
    r"polish",
    r"improve",
)
_RESTRUCTURE_CUES = (
    r"重组结构",
    r"结构可以重组",
    r"重构",
    r"重新规划",
    r"提炼",
    r"压缩",
    r"精简",
    r"扩展",
    r"扩充",
    r"拓展",
    r"改写",
    r"重新写",
    r"结构.*重组",
    r"restructure",
    r"rewrite",
    r"condense",
    r"summarize",
)
_MERGE_CUES = (
    r"融合",
    r"合并",
    r"整合",
    r"多份",
    r"几份",
    r"merge",
    r"combine",
)
_EXTRACT_CUES = (
    r"提取",
    r"抽取",
    r"只要",
    r"只保留",
    r"extract",
)
_TEMPLATE_REFERENCE_CUES = (
    r"版式参考",
    r"风格参考",
    r"模板",
    r"学习(?:它|这个|这份).*(?:版式|风格)",
    r"只学习(?:版式|风格)",
    r"template",
    r"style\s+reference",
)
_TARGET_COUNT_RE = re.compile(r"(?:做成|变成|改成|压缩到|扩展到|提炼成|生成|一共|总共|目标)?\s*\d{1,3}\s*(?:页|頁|张|張|pages?|slides?)", re.IGNORECASE)


def _copy_default() -> dict[str, Any]:
    return deepcopy(DEFAULT_INTENT_CONTRACT)


def _matches(text: str, patterns: tuple[str, ...]) -> list[str]:
    evidence: list[str] = []
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = match.group(0).strip()
            if value and value not in evidence:
                evidence.append(value)
    return evidence


def _clamp_confidence(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def normalize_intent_contract(value: dict | None) -> dict[str, Any]:
    contract = _copy_default()
    if not isinstance(value, dict):
        return contract

    for key, allowed in ALLOWED_VALUES.items():
        raw = str(value.get(key) or "").strip()
        if raw in allowed:
            contract[key] = raw

    contract["confidence"] = _clamp_confidence(value.get("confidence"), contract["confidence"])
    evidence = value.get("evidence")
    if isinstance(evidence, list):
        contract["evidence"] = [
            str(item).strip()
            for item in evidence
            if str(item or "").strip()
        ][:12]
    return contract


def source_diagnostics_from_documents(documents: str) -> dict[str, Any]:
    sources = detect_ppt_sources(documents or "")
    return {
        "ppt_source_count": len(sources),
        "source_page_count": sum(int(item.get("pages") or 0) for item in sources),
        "has_ppt_source": bool(sources),
    }


def infer_intent_contract(
    brief: str = "",
    *,
    source_diagnostics: dict | None = None,
) -> dict[str, Any]:
    text = str(brief or "")
    diagnostics = source_diagnostics if isinstance(source_diagnostics, dict) else {}
    evidence: list[str] = []
    has_single_ppt = int(diagnostics.get("ppt_source_count") or 0) == 1
    has_multiple_ppts = int(diagnostics.get("ppt_source_count") or 0) > 1

    replicate_hits = _matches(text, _REPLICATE_STRONG_CUES)
    page_order_hits = _matches(text, _PAGE_ORDER_CUES)
    polish_hits = _matches(text, _POLISH_CUES)
    restructure_hits = _matches(text, _RESTRUCTURE_CUES)
    merge_hits = _matches(text, _MERGE_CUES)
    extract_hits = _matches(text, _EXTRACT_CUES)
    template_hits = _matches(text, _TEMPLATE_REFERENCE_CUES)
    source_preservation_hits = _matches(text, _SOURCE_PRESERVATION_CUES)
    has_target_count = bool(_TARGET_COUNT_RE.search(text))
    has_source_preservation = bool(source_preservation_hits or page_order_hits)

    contract = _copy_default()
    if template_hits:
        contract.update({
            "task_type": "template_reference",
            "rewrite_level": "free",
            "page_order_policy": "can_reorder",
            "page_count_policy": "free",
            "source_fidelity": "synthesized",
            "visual_source_use": "style_reference",
            "confidence": 0.86,
        })
        evidence.extend(template_hits)
    elif replicate_hits:
        contract.update({
            "task_type": "replicate",
            "rewrite_level": "none",
            "page_order_policy": "preserve",
            "page_count_policy": "same",
            "source_fidelity": "verbatim",
            "visual_source_use": "page_reference",
            "confidence": 0.9,
        })
        evidence.extend(replicate_hits + page_order_hits)
    elif merge_hits and (has_multiple_ppts or "多" in text or "几" in text):
        contract.update({
            "task_type": "merge",
            "rewrite_level": "moderate",
            "page_order_policy": "can_reorder",
            "page_count_policy": "target_count" if has_target_count else "free",
            "source_fidelity": "optimized",
            "visual_source_use": "page_reference",
            "confidence": 0.82,
        })
        evidence.extend(merge_hits + restructure_hits)
    elif extract_hits:
        contract.update({
            "task_type": "extract",
            "rewrite_level": "moderate",
            "page_order_policy": "can_reorder",
            "page_count_policy": "target_count" if has_target_count else "free",
            "source_fidelity": "optimized",
            "visual_source_use": "page_reference",
            "confidence": 0.8,
        })
        evidence.extend(extract_hits + restructure_hits)
    elif has_source_preservation and has_target_count:
        contract.update({
            "task_type": "polish",
            "rewrite_level": "light",
            "page_order_policy": "preserve",
            "page_count_policy": "target_count",
            "source_fidelity": "faithful",
            "visual_source_use": "page_reference",
            "confidence": 0.86,
        })
        evidence.extend(source_preservation_hits + page_order_hits)
    elif restructure_hits or has_target_count:
        preserve_order = bool(page_order_hits) and not re.search(r"可以\s*重组|结构可以重组|can\s+reorder", text, flags=re.IGNORECASE)
        contract.update({
            "task_type": "restructure",
            "rewrite_level": "moderate",
            "page_order_policy": "mostly_preserve" if preserve_order else "can_reorder",
            "page_count_policy": "target_count" if has_target_count else "free",
            "source_fidelity": "optimized",
            "visual_source_use": "page_reference",
            "confidence": 0.78,
        })
        evidence.extend(restructure_hits + page_order_hits)
    elif polish_hits or has_single_ppt:
        contract.update({
            "task_type": "polish",
            "rewrite_level": "light",
            "page_order_policy": "preserve" if page_order_hits or has_single_ppt else "mostly_preserve",
            "page_count_policy": "same" if has_single_ppt or page_order_hits else "similar",
            "source_fidelity": "faithful",
            "visual_source_use": "page_reference",
            "confidence": 0.72 if polish_hits else 0.55,
        })
        evidence.extend(polish_hits + page_order_hits)
    else:
        contract["confidence"] = 0.45

    if has_target_count:
        contract["page_count_policy"] = "target_count"
        evidence.append(_TARGET_COUNT_RE.search(text).group(0).strip())

    if has_single_ppt and has_source_preservation and contract["task_type"] in {"extract", "restructure", "polish"} and not has_target_count:
        contract.update({
            "task_type": "polish",
            "rewrite_level": "light",
            "page_order_policy": "preserve",
            "page_count_policy": "same",
            "source_fidelity": "faithful",
            "visual_source_use": "page_reference",
            "confidence": max(float(contract.get("confidence") or 0), 0.84),
        })
        evidence.extend(page_order_hits + source_preservation_hits)

    contract["evidence"] = [item for idx, item in enumerate(evidence) if item and item not in evidence[:idx]][:12]
    return normalize_intent_contract(contract)


def merge_intent_contract(existing: dict | None, incoming: dict | None) -> dict[str, Any]:
    current = normalize_intent_contract(existing)
    next_contract = normalize_intent_contract(incoming)
    if not existing:
        return next_contract
    if next_contract["confidence"] >= current["confidence"] or next_contract.get("evidence"):
        merged = {**current, **next_contract}
    else:
        merged = current
    evidence = []
    for item in [*current.get("evidence", []), *next_contract.get("evidence", [])]:
        if item and item not in evidence:
            evidence.append(item)
    merged["evidence"] = evidence[:12]
    return normalize_intent_contract(merged)


def contract_to_planning_policy(contract: dict | None) -> dict[str, Any]:
    normalized = normalize_intent_contract(contract)
    task_type = normalized["task_type"]
    return {
        "task_type": task_type,
        "allow_direct_ppt_replicate": task_type == "replicate" and normalized["rewrite_level"] == "none",
        "preserve_source_page_order": normalized["page_order_policy"] in {"preserve", "mostly_preserve"},
        "preserve_source_page_count": normalized["page_count_policy"] == "same",
        "requires_clarification": normalized["confidence"] < 0.5,
        "rewrite_instruction": normalized["source_fidelity"],
        "visual_source_use": normalized["visual_source_use"],
    }
