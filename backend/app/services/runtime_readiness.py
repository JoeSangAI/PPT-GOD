from __future__ import annotations

from typing import Any

from app.core.provider_credentials import ProviderCredentials, get_raw_provider_credentials


CAPABILITY_LABELS = {
    "text_generation": "文本生成",
    "image_generation": "图片生成",
}

CAPABILITY_USES = {
    "text_generation": "内容规划、视觉方向和每页画面描述",
    "image_generation": "整页画面生成、单页修改和参考图编辑",
}


def _capability(
    *,
    capability_id: str,
    label: str,
    api_key: str,
    api_base: str,
    model: str,
    agent_supplied: bool,
    used_for: list[str],
) -> dict[str, Any]:
    missing_fields: list[str] = []
    if not api_key:
        missing_fields.append("api_key")
    if not api_base:
        missing_fields.append("api_base")
    if not model:
        missing_fields.append("model")
    provider_configured = not missing_fields
    available = provider_configured or agent_supplied
    source = "provider" if provider_configured else ("agent" if agent_supplied else "missing")
    return {
        "id": capability_id,
        "label": label,
        "available": available,
        "provider_configured": provider_configured,
        "agent_supplied": bool(agent_supplied),
        "source": source,
        "api_base": api_base if provider_configured else "",
        "model": model if provider_configured else "",
        "missing_fields": missing_fields,
        "used_for": used_for,
    }


def build_runtime_readiness(
    credentials: ProviderCredentials | None = None,
    *,
    agent_text: bool = False,
    agent_image: bool = False,
) -> dict[str, Any]:
    raw = credentials or get_raw_provider_credentials()
    resolved = raw.with_defaults()
    text = _capability(
        capability_id="text_generation",
        label="文本生成",
        api_key=resolved.minimax_api_key,
        api_base=resolved.minimax_api_base,
        model=resolved.minimax_llm_model,
        agent_supplied=agent_text,
        used_for=["内容规划", "视觉方向", "页面画面描述"],
    )
    image = _capability(
        capability_id="image_generation",
        label="图片生成",
        api_key=resolved.comet_api_key,
        api_base=resolved.comet_api_base,
        model=resolved.comet_image_model,
        agent_supplied=agent_image,
        used_for=["整页画面生成", "单页修改", "参考图编辑"],
    )
    capabilities = {
        "text_generation": text,
        "image_generation": image,
    }
    missing = [item["id"] for item in capabilities.values() if not item["available"]]
    missing_provider = [item["id"] for item in capabilities.values() if not item["provider_configured"]]
    labels = [capabilities[item]["label"] for item in missing]
    if not missing:
        summary = "运行所需能力已经就绪。"
    else:
        summary = f"还缺 {len(missing)} 项能力：{'、'.join(labels)}。"
    return {
        "ok": True,
        "ready": not missing,
        "standalone_ready": not missing_provider,
        "summary": summary,
        "capabilities": capabilities,
        "missing": missing,
        "missing_provider_configuration": missing_provider,
        "principle": "PPT God 负责工作流；模型能力可以来自 BYOK 接口，也可以由外部 Agent 产出并导入对应成果。",
        "next_steps": [
            item
            for item in [
                {
                    "capability": "text_generation",
                    "action": "configure_or_supply",
                    "message": "配置兼容 OpenAI Chat Completions 的文本模型；若 Agent 已负责内容与视觉规划，可由 Agent 提供结构化成果。",
                },
                {
                    "capability": "image_generation",
                    "action": "configure_or_supply",
                    "message": "配置兼容 OpenAI Images 的生图模型；若 Agent 能生成并导入最终页面图，可由 Agent 提供页面成果。",
                },
            ]
            if item["capability"] in missing
        ],
    }


def build_action_preflight(
    capability_id: str,
    *,
    action: str,
    credentials: ProviderCredentials | None = None,
    agent_supplied: bool = False,
) -> dict[str, Any]:
    """Describe whether one concrete workflow action can start now.

    Backend-owned generation can only start from provider credentials. An
    external Agent is still reported as a valid delegation source, but it does
    not make a GUI click magically callable by the backend.
    """
    if capability_id not in CAPABILITY_LABELS:
        raise ValueError(f"Unknown runtime capability: {capability_id}")

    readiness = build_runtime_readiness(
        credentials,
        agent_text=agent_supplied if capability_id == "text_generation" else False,
        agent_image=agent_supplied if capability_id == "image_generation" else False,
    )
    capability = readiness["capabilities"][capability_id]
    provider_ready = bool(capability["provider_configured"])
    if provider_ready:
        return {
            "ok": True,
            "status": "ready",
            "action": action,
            "capability": capability_id,
            "source": "provider",
            "message": f"{CAPABILITY_LABELS[capability_id]}能力已就绪。",
        }

    label = CAPABILITY_LABELS[capability_id]
    used_for = CAPABILITY_USES[capability_id]
    if agent_supplied:
        return {
            "ok": False,
            "code": "agent_action_required",
            "status": "delegated",
            "action": action,
            "capability": capability_id,
            "source": "agent",
            "message": f"这一步需要{label}。当前 Agent 可以代劳，请回到 Agent 对话继续。",
            "used_for": used_for,
            "next_action": {
                "type": "return_to_agent",
                "label": "回到 Agent 继续",
            },
        }

    return {
        "ok": False,
        "code": "missing_model_capability",
        "status": "action_required",
        "action": action,
        "capability": capability_id,
        "source": "missing",
        "message": f"这一步还缺少{label}能力。它用于{used_for}。请先配置对应 API Key。",
        "used_for": used_for,
        "next_action": {
            "type": "configure_model",
            "capability": capability_id,
            "label": f"配置{label}模型",
        },
    }


def missing_provider_capability(
    capability_id: str,
    *,
    action: str,
    credentials: ProviderCredentials | None = None,
) -> dict[str, Any] | None:
    result = build_action_preflight(
        capability_id,
        action=action,
        credentials=credentials,
    )
    return None if result["ok"] else result
