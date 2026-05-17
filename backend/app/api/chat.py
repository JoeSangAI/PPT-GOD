import json
import json_repair
import copy
import os
import re
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models.base import get_db
from app.models.models import Project, ReferenceImage, Slide
from app.core.llm_client import get_llm_client
from app.core.provider_credentials import get_minimax_llm_model
from app.utils.project_docs import load_project_documents
from app.services.search_service import get_knowledge_augmenter
from app.services.agent_next_action import CONTENT_ACTIONS, FINETUNE_ACTIONS, VISUAL_ACTIONS, with_next_action
from app.services.artifact_versions import content_signature, style_asset_signature
from app.services.image_analyzer import describe_context_image
from app.services.style_proposal import enforce_user_style_requirements
from app.services.content_plan import infer_page_count_from_topic
from app.services.source_intent import (
    infer_intent_contract,
    merge_intent_contract,
    normalize_intent_contract,
    source_diagnostics_from_documents,
)

router = APIRouter(prefix="/projects", tags=["chat"])


CONTENT_MUTATION_PAYLOAD_KEYS = {
    "regenerate_plan": "topic",
    "update_slide_content": "updated_content",
    "update_all_slides": "updated_slides",
    "add_slide_before": "new_slide",
    "add_slide_after": "new_slide",
}
CONTENT_MUTATION_ACTIONS = frozenset(CONTENT_MUTATION_PAYLOAD_KEYS)
CONTENT_CONTRACT_REVIEW_ACTIONS = frozenset({
    "answer",
    "collect_content",
    "forward_to_visual",
    *CONTENT_MUTATION_ACTIONS,
})
VISUAL_MUTATION_PAYLOAD_KEYS = {
    "update_slide_visual": "updated_visual",
    "update_all_slides_visual": "updated_slides_visual",
    "adjust_style": "style_proposal",
    "confirm_style": "style",
}
VISUAL_MUTATION_ACTIONS = frozenset(VISUAL_MUTATION_PAYLOAD_KEYS)


class ChatMessage(BaseModel):
    message: str
    history: list[dict] = []
    page_context: dict | None = None
    agent_role: str = "content"  # "content" | "visual" | "finetune"
    attachment_ids: list[str] = []

    @field_validator("agent_role")
    @classmethod
    def _validate_agent_role(cls, v: str) -> str:
        allowed = {"content", "visual", "finetune"}
        if v not in allowed:
            raise ValueError(f"agent_role must be one of {allowed}, got '{v}'")
        return v


def _load_chat_attachments(db: Session, project_id: str, attachment_ids: list[str]) -> list[ReferenceImage]:
    ids = [str(item) for item in (attachment_ids or []) if str(item).strip()]
    if not ids:
        return []
    refs = db.query(ReferenceImage).filter(
        ReferenceImage.project_id == project_id,
        ReferenceImage.id.in_(ids),
        ReferenceImage.role.in_(["chat_ref", "content_ref", "chart_ref", "visual_asset", "finetune_ref"]),
    ).all()
    by_id = {ref.id: ref for ref in refs if ref.file_path and os.path.exists(ref.file_path)}
    return [by_id[ref_id] for ref_id in ids if ref_id in by_id]


def _message_mentions_images(message: str) -> bool:
    return bool(re.search(r"(图|图片|截图|照片|素材|参考图|读图|识图|OCR|ocr|解读|这张|这两张|上传)", message or ""))


def _load_ambient_chat_attachments(db: Session, project_id: str, agent_role: str, message: str) -> list[ReferenceImage]:
    if not _message_mentions_images(message):
        return []
    allowed_roles = ["content_ref"]
    if agent_role == "visual":
        allowed_roles = ["content_ref", "visual_asset", "style_ref", "logo"]
    refs = db.query(ReferenceImage).filter(
        ReferenceImage.project_id == project_id,
        ReferenceImage.role.in_(allowed_roles),
    ).all()
    usable: list[ReferenceImage] = []
    for ref in refs:
        if not ref.file_path or not os.path.exists(ref.file_path):
            continue
        analysis = ref.asset_analysis if isinstance(ref.asset_analysis, dict) else {}
        if ref.role == "content_ref" and analysis.get("pptx_source_page_num"):
            continue
        usable.append(ref)
    return usable[-8:]


def _format_page_target_context(page_context: dict | None) -> str:
    if not isinstance(page_context, dict):
        return ""

    scope_map = {
        "deck": "整套 PPT",
        "selected_slides": "选中页",
        "current_slide": "当前页",
    }
    area_map = {
        "whole": "全页内容",
        "title": "标题",
        "body": "正文",
        "visual": "画面",
        "materials": "素材",
        "notes": "备注",
    }

    raw_scope = str(page_context.get("scope") or "").strip()
    scope_label = scope_map.get(raw_scope)
    if not scope_label:
        scope_label = "当前页" if page_context.get("mode") == "page" else "整套 PPT"

    raw_area = str(page_context.get("target_area") or "").strip()
    if raw_area == "whole":
        area_label = "全页内容"
    else:
        area_label = str(page_context.get("area_label") or "").strip() or area_map.get(raw_area, "全页内容")

    page_nums: list[int] = []
    target_nums = page_context.get("target_page_nums")
    if isinstance(target_nums, list):
        for item in target_nums:
            try:
                page_num = int(item)
            except (TypeError, ValueError):
                continue
            if page_num > 0 and page_num not in page_nums:
                page_nums.append(page_num)
    if not page_nums and page_context.get("mode") == "page":
        current_page = page_context.get("current_page")
        if isinstance(current_page, dict):
            try:
                page_num = int(current_page.get("page_num"))
            except (TypeError, ValueError):
                page_num = 0
            if page_num > 0:
                page_nums.append(page_num)

    lines = [
        f"范围：{scope_label}",
        f"区域：{area_label}",
    ]
    if page_nums:
        lines.insert(1, f"目标页：第 {'、'.join(str(num) for num in page_nums)} 页")
    lines.append("必须优先遵守这个修改目标；用户没有另行说明时，不得扩大或缩小范围，也不得修改目标区域之外的内容。")
    return "\n".join(lines)


def _build_page_context_prompt_section(page_context: dict | None) -> str:
    if not isinstance(page_context, dict):
        return ""

    parts: list[str] = []
    target_context = _format_page_target_context(page_context)
    if target_context:
        parts.append("【本轮修改目标（来自用户输入框上方的选择）】\n" + target_context)

    cross_stage_context = str(page_context.get("cross_stage_context") or "").strip()
    if cross_stage_context:
        parts.append(
            "【跨阶段用户补充要求】\n"
            "这些内容来自用户在其他阶段提出的要求。即使当前阶段不能直接完成，也必须回应并在相关后续动作中考虑；"
            "如果要求影响当前阶段产物，请落实到当前 action/payload 中。\n"
            f"{cross_stage_context}"
        )

    if page_context.get("mode") == "global":
        slides_summary = page_context.get("slides", [])
        parts.append(
            "【当前处于全局调整模式 —— 用户指令可能影响多个页面】\n"
            f"所有页面摘要：\n{json.dumps(slides_summary, ensure_ascii=False, indent=2)}"
        )
    elif page_context.get("mode") == "page":
        current_page = page_context.get("current_page", {})
        other_pages = page_context.get("other_pages", [])
        page_num = current_page.get("page_num") if isinstance(current_page, dict) else "?"
        section = (
            f"【当前处于单页编辑模式 —— 你只能修改第 {page_num} 页，不得影响其他页面】\n"
            f"\n=== 当前正在编辑的页面（修改目标） ===\n{json.dumps(current_page, ensure_ascii=False, indent=2)}"
        )
        if other_pages:
            section += f"\n\n=== 其他页面摘要（仅作风格/格式参考，禁止修改） ===\n{json.dumps(other_pages, ensure_ascii=False, indent=2)}"
        parts.append(section)
    else:
        page_json = json.dumps(page_context, ensure_ascii=False, indent=2)
        parts.append(f"=== 当前正在编辑的单页上下文 ===\n{page_json}\n=== 单页上下文结束 ===")

    return "\n\n".join(parts).strip()


def _build_attachment_context(attachments: list[ReferenceImage] | None, agent_role: str) -> str:
    lines: list[str] = []
    role_label_map = {
        "chat_ref": "本轮对话图片",
        "content_ref": "内容素材图",
        "chart_ref": "图表参考图",
        "visual_asset": "项目素材图",
        "style_ref": "风格参考图",
        "logo": "品牌 Logo",
        "finetune_ref": "微调参考图",
    }
    purpose = "内容提取和PPT修改" if agent_role == "content" else "视觉参考、素材理解和PPT修改"
    for idx, ref in enumerate(attachments or [], start=1):
        label = ref.asset_name or os.path.basename(ref.file_path)
        role_label = role_label_map.get(ref.role, "参考图")
        description = describe_context_image(ref.file_path, label, role_label, purpose)
        lines.append(f"### 图片 {idx}: {label}（{role_label}）")
        if description:
            lines.append(description)
        else:
            analysis = ref.asset_analysis if isinstance(ref.asset_analysis, dict) else {}
            fallback = analysis.get("description") or analysis.get("subject") or "图片已上传，但当前读图接口没有返回可用解读。"
            lines.append(str(fallback))
    return "\n".join(lines).strip()


def _infer_requested_page_count(message: str) -> int | None:
    return infer_page_count_from_topic(message)


def _is_simple_confirmation(message: str) -> bool:
    text = re.sub(r"[\s。.!！?？~～]+", "", message or "").lower()
    return text in {
        "ok",
        "okay",
        "好的",
        "好",
        "可以",
        "可以了",
        "确认",
        "没问题",
        "就这样",
        "行",
        "嗯",
        "嗯嗯",
    }


def _source_intent_context_text(project_context: dict) -> str:
    raw = project_context.get("intent_contract") if isinstance(project_context, dict) else None
    if not isinstance(raw, dict):
        return ""
    contract = normalize_intent_contract(raw)
    task_type = contract.get("task_type")
    if task_type == "replicate":
        guidance = "用户希望尽量按原文和原页顺序整理；不要主动重组结构，标题正文只做必要清理。"
    elif task_type == "polish":
        guidance = "用户希望保留原页顺序和主要事实，优化标题和正文表达，不重组叙事。"
    elif task_type in {"restructure", "merge", "extract"}:
        guidance = "用户允许按目标重组材料；必须保留关键事实，并说明内容来自哪些原页。"
    elif task_type == "template_reference":
        guidance = "用户主要把上传 PPT 当作版式或视觉参考；正文应根据 Brief 重新组织。"
    else:
        return ""
    if float(contract.get("confidence") or 0) < 0.5:
        guidance += " 如果处理方式会明显改变产物，只问一个窄问题：更接近按原页顺序轻优化，还是可以重组结构？"
    return "【上传 PPT 的处理方式】\n" + guidance


def _update_project_intent_from_message_if_needed(
    project: Project,
    user_message: str,
    documents: str,
    db: Session,
) -> dict:
    current = project.intent_contract if isinstance(project.intent_contract, dict) else None
    diagnostics = source_diagnostics_from_documents(documents)
    if not diagnostics.get("has_ppt_source") and not current:
        return {}

    incoming = infer_intent_contract(user_message, source_diagnostics=diagnostics)
    if current and not incoming.get("evidence") and float(incoming.get("confidence") or 0) < 0.7:
        return current

    merged = merge_intent_contract(current, incoming)
    if merged != current:
        project.intent_contract = merged
        flag_modified(project, "intent_contract")
        db.commit()
        db.refresh(project)
    return project.intent_contract if isinstance(project.intent_contract, dict) else {}


def _has_page_count_change_intent(message: str, project_context: dict) -> bool:
    page_count = _infer_requested_page_count(message)
    if not page_count:
        return False
    text = message or ""
    # “第 12 页怎么样” is a page reference, not a deck-size request.
    if re.search(r"第\s*\d{1,3}\s*页", text):
        return False
    current_count = int(project_context.get("total_slides") or 0)
    page_count_verbs = (
        r"变成|改成|改为|调整到|扩展到|拓展到|扩充到|增加到|加到|缩减到|减少到|"
        r"做成|生成|重新|规划|页数|一共|总共|整套|PPT"
    )
    return bool(re.search(page_count_verbs, text, flags=re.IGNORECASE)) or (current_count > 0 and page_count != current_count)


def _has_content_mutation_intent(message: str) -> bool:
    text = message or ""
    if not text.strip():
        return False
    mutation_then_target = (
        r"(重新|重做|重构|重写|改写|修改|调整|扩充|扩展|拓展|补充|增加|新增|删除|删掉|"
        r"替换|改成|变成|改为|对齐|按照|基于|落实|突出|强化|整合|合并|融入|放到|放进|做到|做进)"
        r".{0,30}(内容|规划|页面|页|标题|正文|故事|结构|逻辑|信息|素材|图片|截图|图表|表格|数据|原文|文档|MD|Markdown|大纲|PPT)"
    )
    target_then_mutation = (
        r"(内容|规划|页面|页|标题|正文|故事|结构|逻辑|信息|素材|图片|截图|图表|表格|数据|原文|文档|MD|Markdown|大纲|PPT)"
        r".{0,30}(重新|重做|重构|重写|改写|修改|调整|扩充|扩展|拓展|补充|增加|新增|删除|删掉|"
        r"替换|改成|变成|改为|对齐|落实|突出|强化|整合|合并|融入|放到|放进|做到|做进)"
    )
    return bool(re.search(mutation_then_target, text, flags=re.IGNORECASE) or re.search(target_then_mutation, text, flags=re.IGNORECASE))


def _response_promises_content_mutation(response: str | None) -> bool:
    text = response or ""
    if not text:
        return False
    promise = r"(已根据|已经|我会|会把|正在|开始|将|准备).{0,40}(调整|修改|更新|重构|重新|规划|生成|扩展|扩充|增加|新增|变成|改成|落实)"
    target = r"(内容|规划|页面|页|标题|正文|故事|结构|逻辑|PPT)"
    return bool(re.search(promise, text, flags=re.IGNORECASE) and re.search(target, text, flags=re.IGNORECASE))


def _requires_content_mutation_action(user_message: str, result: dict, project_context: dict) -> bool:
    if _has_page_count_change_intent(user_message, project_context):
        return True
    if _has_content_mutation_intent(user_message):
        return True
    return _response_promises_content_mutation(result.get("response"))


def _infer_pending_content_plan_offer(history: list[dict], project_context: dict) -> dict | None:
    current_count = int(project_context.get("total_slides") or 0)
    for item in reversed(history or []):
        if item.get("role") not in {"assistant", "agent"}:
            continue
        content = str(item.get("content") or "")
        if not content:
            continue
        if "内容规划已生成完毕" in content or "正在启动内容规划生成" in content:
            return None
        looks_like_plan = "内容规划" in content and (re.search(r"\bP\s*\d+", content, flags=re.IGNORECASE) or "P1" in content)
        if not looks_like_plan:
            continue
        page_count = _infer_requested_page_count(content)
        if not page_count:
            p_markers = re.findall(r"\bP\s*(\d{1,3})", content, flags=re.IGNORECASE)
            if p_markers:
                page_count = max(int(n) for n in p_markers)
        if page_count and (not current_count or page_count != current_count):
            return {
                "page_count": page_count,
                "excerpt": content[:1200],
            }
    return None


def _content_action_payload_complete(result: dict) -> bool:
    action = result.get("action")
    payload_key = CONTENT_MUTATION_PAYLOAD_KEYS.get(action)
    if not payload_key:
        return True
    payload = result.get(payload_key)
    if isinstance(payload, list):
        return len(payload) > 0
    if isinstance(payload, dict):
        return bool(payload)
    return bool(payload)


def _content_result_needs_contract_review(result: dict, is_draft: bool) -> bool:
    if is_draft or not isinstance(result, dict):
        return False
    action = result.get("action")
    if action not in CONTENT_CONTRACT_REVIEW_ACTIONS:
        return False
    return action in {"answer", "collect_content", "forward_to_visual"} or not _content_action_payload_complete(result)


def _fallback_regenerate_plan(
    user_message: str,
    project_context: dict,
    response: str | None = None,
    page_count: int | None = None,
) -> dict:
    title = project_context.get("title") or "当前项目"
    feedback = user_message.strip().rstrip("。.!！？? ")
    page_count = page_count or _infer_requested_page_count(user_message)
    page_count_instruction = f"目标页数：必须 {page_count} 页。" if page_count else ""
    coerced = {
        "action": "regenerate_plan",
        "topic": (
            f"{title}。用户反馈：{feedback}。{page_count_instruction}"
            "请重新生成内容规划，把用户的自然语言指令落实为整套 PPT 的内容结构、页面节奏和文字表达改动。"
        ),
        "response": response or "明白，我会把这条反馈落实到内容规划里重新生成。",
    }
    if page_count:
        coerced["page_count"] = page_count
    return coerced


def _targets_current_page(message: str) -> bool:
    return bool(re.search(r"(当前页|当前页面|这一页|这页|本页|这个页面|这一个页面|这张PPT|这一张PPT)", message or ""))


def _mentions_attachment_material(message: str) -> bool:
    return bool(re.search(r"(附件|上传|图片|截图|照片|这张|这两张|这几张|两页|几页|素材|信息|图表|表格|数据|OCR|ocr)", message or ""))


def _fallback_update_current_slide_from_attachments(
    *,
    user_message: str,
    page_context: dict | None,
    attachment_context: str,
    response: str | None = None,
) -> dict | None:
    if not attachment_context or not isinstance(page_context, dict):
        return None
    if page_context.get("mode") != "page":
        return None
    current_page = page_context.get("current_page")
    if not isinstance(current_page, dict):
        return None
    if not (_targets_current_page(user_message) or _has_content_mutation_intent(user_message)):
        return None
    if not _mentions_attachment_material(user_message):
        return None

    content = copy.deepcopy(current_page.get("content_json") or {})
    page_num = current_page.get("page_num") or content.get("page_num")
    if not page_num:
        return None
    text_content = content.get("text_content")
    if not isinstance(text_content, dict):
        text_content = {}

    summary = re.sub(r"\n{3,}", "\n\n", attachment_context).strip()
    if len(summary) > 2200:
        summary = summary[:2200].rstrip() + "..."
    existing_body = str(text_content.get("body") or "").strip()
    material_block = f"【本轮素材信息】\n{summary}"
    text_content["body"] = f"{existing_body}\n\n{material_block}".strip() if existing_body else material_block
    if not str(text_content.get("headline") or "").strip():
        text_content["headline"] = "整合素材信息"
    text_content.setdefault("subhead", "")

    content["page_num"] = page_num
    content["type"] = content.get("type") or current_page.get("type") or "content"
    content.setdefault("section_title", "")
    content["text_content"] = text_content
    content.setdefault("speaker_notes", "")
    content.setdefault("visual_suggestion", "")

    return {
        "action": "update_slide_content",
        "updated_content": content,
        "response": response or "收到，我已把本轮附件信息整理进当前页内容，请检查文字取舍。",
    }


def _has_visual_generation_intent(message: str) -> bool:
    return bool(re.search(r"(出图|生图|生成图片|生成成图|生成全部|批量生成|打样|确认生成|开始生成图片|就按这个生成|可以了.*生成)", message or "", flags=re.IGNORECASE))


def _has_visual_mutation_intent(message: str) -> bool:
    text = message or ""
    if not text.strip():
        return False
    mutation_then_target = (
        r"(重新|重做|修改|调整|换成|改成|改为|加入|添加|放到|放在|移到|去掉|删除|删掉|统一|放大|缩小|突出|弱化|看不清|不清楚|看不见|读不清|不明显|对比度|再来一版|换个方向|不满意)"
        r".{0,35}(视觉|画面|图片|图|背景|配色|颜色|字体|排版|版式|风格|素材|Logo|logo|参考图|主色|标题|页面|整套|PPT|科技感|商务感)"
    )
    target_then_mutation = (
        r"(视觉|画面|图片|图|背景|配色|颜色|字体|排版|版式|风格|素材|Logo|logo|参考图|主色|标题|页面|整套|PPT|科技感|商务感)"
        r".{0,35}(重新|重做|修改|调整|换成|改成|改为|加入|添加|放到|放在|移到|去掉|删除|删掉|统一|放大|缩小|突出|弱化|看不清|不清楚|看不见|读不清|不明显|对比度|再来一版|换个方向|不满意)"
    )
    return bool(re.search(mutation_then_target, text, flags=re.IGNORECASE) or re.search(target_then_mutation, text, flags=re.IGNORECASE))


def _response_promises_visual_mutation(response: str | None) -> bool:
    text = response or ""
    if not text:
        return False
    promise = r"(已根据|已经|我会|会把|正在|开始|将|准备).{0,40}(调整|修改|更新|重新|生成|换成|改成|加入|添加|统一)"
    target = r"(视觉|画面|图片|背景|配色|颜色|字体|排版|版式|风格|生图|页面|整套|PPT|科技感|商务感)"
    return bool(re.search(promise, text, flags=re.IGNORECASE) and re.search(target, text, flags=re.IGNORECASE))


def _visual_action_payload_complete(result: dict) -> bool:
    action = result.get("action")
    payload_key = VISUAL_MUTATION_PAYLOAD_KEYS.get(action)
    if not payload_key:
        return True
    payload = result.get(payload_key)
    if action == "adjust_style" and not payload:
        # Adjustments can still be handled by backend regeneration when the UI is
        # in a state that can produce fresh proposal cards.
        return bool(result.get("response"))
    if action == "update_slide_visual":
        if not isinstance(payload, dict):
            return False
        visual_json = payload.get("visual_json")
        return isinstance(visual_json, dict) and bool(
            str(visual_json.get("visual_description") or "").strip()
            or str(visual_json.get("design_notes") or "").strip()
        )
    if action == "update_all_slides_visual":
        if not isinstance(payload, list) or not payload:
            return False
        return any(
            isinstance(item, dict)
            and item.get("page_num")
            and isinstance(item.get("visual_json"), dict)
            and (
                str(item["visual_json"].get("visual_description") or "").strip()
                or str(item["visual_json"].get("design_notes") or "").strip()
            )
            for item in payload
        )
    if isinstance(payload, list):
        return len(payload) > 0
    if isinstance(payload, dict):
        return bool(payload)
    return bool(payload)


def _infer_visual_page_nums(user_message: str, page_context: dict | None) -> list[int]:
    nums = []
    for match in re.finditer(r"第\s*(\d{1,3})\s*(?:页|頁|张|張)", user_message or ""):
        try:
            nums.append(int(match.group(1)))
        except ValueError:
            pass
    if isinstance(page_context, dict):
        target_nums = page_context.get("target_page_nums")
        if isinstance(target_nums, list):
            nums.extend(int(n) for n in target_nums if isinstance(n, int) or str(n).isdigit())
        if page_context.get("mode") == "page":
            current_page = page_context.get("current_page")
            if isinstance(current_page, dict):
                page_num = current_page.get("page_num")
                if isinstance(page_num, int):
                    nums.append(page_num)
    seen = set()
    ordered = []
    for num in nums:
        if num <= 0 or num in seen:
            continue
        seen.add(num)
        ordered.append(num)
    return ordered


def _has_visual_reroll_intent(message: str) -> bool:
    return bool(
        re.search(
            r"(再来一版|换个方向|不满意|重新生成(?:一版)?画面方案|重写画面方案|重新出(?:一版)?方案|重做这一页|重画这一页)",
            message or "",
            flags=re.IGNORECASE,
        )
    )


def _visual_instruction_change_note(user_message: str) -> str:
    text = re.sub(r"\s+", " ", user_message or "").strip()
    if re.search(r"(看不清|不清楚|看不见|读不清|辨识|对比度|黑色.{0,8}(字体|文字|字)|(?:字体|文字|字).{0,8}黑色)", text):
        return "提升文字对比度和可读性，避免黑色或低对比文字压在深色背景上，标题和关键信息必须清晰可读"
    if re.search(r"(二维码|QR|qr)", text) and re.search(r"(靠下|太低|底部|下面|下沿)", text):
        return "上移二维码并留出安全边距，确保二维码完整清晰、不会贴近画面底部"
    if re.search(r"(太暗|过暗|暗了)", text):
        return "提高画面亮度和主体层级，避免关键内容被深色背景压暗"
    if re.search(r"(太浅|太淡|不明显)", text):
        return "增强关键元素的对比度和视觉层级，让主体更明显"
    return f"按用户反馈调整：{_short_text(text, 90)}"


def _current_visual_json_for_page(page_context: dict | None, page_num: int) -> dict:
    if not isinstance(page_context, dict):
        return {}
    current_page = page_context.get("current_page")
    if isinstance(current_page, dict) and int(current_page.get("page_num") or 0) == page_num:
        visual_json = current_page.get("visual_json")
        return copy.deepcopy(visual_json) if isinstance(visual_json, dict) else {}
    for item in page_context.get("slides") or []:
        if isinstance(item, dict) and int(item.get("page_num") or 0) == page_num:
            visual_json = item.get("visual_json")
            return copy.deepcopy(visual_json) if isinstance(visual_json, dict) else {}
    return {}


def _build_visual_update_contract_payload(user_message: str, page_context: dict | None, page_nums: list[int]) -> dict | None:
    if not page_nums or _has_visual_reroll_intent(user_message):
        return None

    note = _visual_instruction_change_note(user_message)
    updates = []
    for page_num in page_nums:
        current_visual = _current_visual_json_for_page(page_context, page_num)
        existing_desc = str(current_visual.get("visual_description") or "").strip()
        existing_notes = str(current_visual.get("design_notes") or "").strip()
        if existing_desc:
            visual_description = f"{existing_desc}\n\n本轮修改：{note}。保持未提及的构图、素材关系和版式不变。"
        else:
            visual_description = f"本轮修改：{note}。保持页面原有内容目标，优先保证关键文字和主体元素清晰。"
        design_notes = f"{existing_notes}\n本轮修改：{note}。".strip() if existing_notes else f"本轮修改：{note}。"
        updates.append({
            "page_num": page_num,
            "visual_json": {
                "visual_description": visual_description,
                "design_notes": design_notes,
            },
        })

    if len(updates) == 1:
        page_num = updates[0]["page_num"]
        return {
            "action": "update_slide_visual",
            "page_nums": [page_num],
            "updated_visual": updates[0],
            "response": f"已写入第 {page_num} 页：{note}",
        }

    return {
        "action": "update_all_slides_visual",
        "page_nums": [item["page_num"] for item in updates],
        "updated_slides_visual": updates,
        "response": f"已写入第 {', '.join(str(item['page_num']) for item in updates)} 页：{note}",
    }


def _is_deck_visual_scope(page_context: dict | None) -> bool:
    if not isinstance(page_context, dict):
        return False
    if page_context.get("scope") == "deck":
        return True
    target_nums = page_context.get("target_page_nums")
    return page_context.get("mode") == "global" and not target_nums


def _visual_result_needs_contract_review(result: dict, user_message: str) -> bool:
    if not isinstance(result, dict):
        return False
    action = result.get("action")
    if action == "answer":
        return (
            _has_visual_generation_intent(user_message)
            or _has_visual_mutation_intent(user_message)
            or _response_promises_visual_mutation(result.get("response"))
        )
    if action in VISUAL_MUTATION_ACTIONS:
        return not _visual_action_payload_complete(result)
    return False


def _enforce_visual_action_contract(
    *,
    result: dict,
    user_message: str,
    page_context: dict | None = None,
    compiler=None,
    logger=None,
) -> dict:
    if not _visual_result_needs_contract_review(result, user_message):
        return result

    page_nums = _infer_visual_page_nums(user_message, page_context)

    try:
        compiled = compiler(
            user_message=user_message,
            page_context=page_context,
            initial_result=result,
        ) if compiler else None
    except Exception as exc:
        if logger:
            logger.warning("[Chat] Visual action contract compiler failed: %s", exc)
        compiled = None

    if isinstance(compiled, dict) and compiled.get("action") in VISUAL_ACTIONS:
        action = compiled.get("action")
        if action == "answer" and not compiled.get("no_change_reason"):
            return {
                "action": "answer",
                "response": "我理解这是视觉修改请求，但没有拿到可安全应用到 PPT 的结构化结果，所以这次没有修改画面。请指定页码或说明要改的画面元素。",
                "no_change_reason": "visual_instruction_compiler_returned_answer",
            }
        if action not in VISUAL_MUTATION_ACTIONS or _visual_action_payload_complete(compiled):
            return compiled

    if _has_visual_generation_intent(user_message):
        response = "可以生成图片，但这会产生生图成本。请确认后我再继续。"
        payload = {"action": "request_generate_image", "response": response}
        if page_nums:
            payload["page_nums"] = page_nums
        return payload

    if (
        _has_visual_mutation_intent(user_message)
        or _response_promises_visual_mutation(result.get("response"))
        or result.get("action") in VISUAL_MUTATION_ACTIONS
    ):
        if page_nums:
            if _has_visual_mutation_intent(user_message) or result.get("action") in {"update_slide_visual", "update_all_slides_visual"}:
                update_payload = _build_visual_update_contract_payload(user_message, page_context, page_nums)
                if update_payload:
                    return update_payload
            return {
                "action": "reroll_page_visual_plan",
                "page_nums": page_nums,
                "response": f"收到，我会先为第 {', '.join(str(n) for n in page_nums)} 页重写画面方案；这一步不会直接生图。",
            }
        if _is_deck_visual_scope(page_context):
            return {
                "action": "adjust_style",
                "response": "收到，我会把这条全局视觉要求转成新的整套风格方案或画面描述调整；请查看后续可确认的方案卡片。",
            }
        return {
            "action": "answer",
            "response": "我理解这是视觉修改请求，但还缺少明确页码或当前页上下文，所以没有修改 PPT。请说明要改哪一页，或进入单页后再发送。",
            "no_change_reason": "missing_visual_target",
        }

    return {
        "action": "answer",
        "response": "我没有修改 PPT；这条视觉指令没有被解析成可安全执行的动作。请换成更具体的修改要求。",
        "no_change_reason": "visual_instruction_compiler_failed",
    }


def _parse_json_object(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        parsed = json_repair.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json_repair.loads(text[start:end + 1])
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return None


def _compact_for_contract(value, text_limit: int = 1800):
    if isinstance(value, str):
        return value if len(value) <= text_limit else value[:text_limit] + "..."
    if isinstance(value, list):
        return [_compact_for_contract(item, text_limit) for item in value]
    if isinstance(value, dict):
        return {k: _compact_for_contract(v, text_limit) for k, v in value.items()}
    return value


def _short_text(value, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else f"{text[:limit]}..."


def _build_content_contract_prompt() -> str:
    return """你是 PPT GOD 的「内容指令编译器」。你的职责不是聊天，而是把用户的自然语言指令编译成对当前 PPT 内容的结构化操作。

硬性工作流合同：
1. 只要用户的话可以被理解为要求、建议、反馈、抱怨或暗示要改变 PPT 的内容、页面、标题、正文、故事线、逻辑、结构、页数、顺序、表达方式，就必须返回一个可执行 mutation action。
2. 严禁用 action="answer" 口头承诺“我会修改/我建议调整/可以重构”。凡是会改变 PPT 内容的回复，都必须带上具体 action 和 payload。
3. 只有纯咨询、纯解释、寒暄、明显视觉问题、或确实无法判断要改哪里时，才允许 action="answer"，并必须提供 no_change_reason。
4. 如果用户只是确认内容已经可以进入视觉阶段，返回 action="forward_to_visual"。
5. 如果用户给的是整体质量反馈，或者局部补丁风险较高，优先返回 action="regenerate_plan"，把用户反馈写进 topic，让后台重新生成内容规划。
6. 如果用户指定单页且当前页内容足够明确，返回 action="update_slide_content"，updated_content 必须是该页完整 content_json。
7. 如果用户在单页上下文里说把附件、截图、图片、这两页或素材信息放到当前页，优先返回 action="update_slide_content"，不要重做整套内容规划。
8. 如果用户要求多页/全局局部文字修改，返回 action="update_all_slides"，updated_slides 只包含需要改的页，每项包含 page_num 和 text_content。
9. 如果用户要求插入新页，返回 add_slide_before 或 add_slide_after，并给出完整 new_slide。

只输出合法 JSON 对象。允许的 action：
- regenerate_plan
- update_slide_content
- update_all_slides
- add_slide_before
- add_slide_after
- forward_to_visual
- answer

输出字段：
- response: 给用户看的简短中文反馈
- topic: regenerate_plan 必填
- page_count: regenerate_plan 可选
- updated_content / updated_slides / new_slide: 对应 action 必填
- no_change_reason: 仅 answer 必填"""


def _compile_content_instruction_with_llm(
    *,
    client,
    user_message: str,
    project_context: dict,
    documents: str,
    page_context: dict | None,
    slides_context: list[dict] | None,
    initial_result: dict,
    attachment_context: str = "",
) -> dict | None:
    payload = {
        "project_context": project_context,
        "user_message": user_message,
        "initial_agent_result": initial_result,
        "page_context_from_frontend": page_context,
        "current_deck_content": slides_context or [],
        "uploaded_documents_excerpt": (documents or "")[:12000],
        "current_message_attachment_context": (attachment_context or "")[:12000],
    }
    response = client.chat.completions.create(
        model=get_minimax_llm_model(),
        messages=[
            {"role": "system", "content": _build_content_contract_prompt()},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        temperature=0.1,
        max_tokens=3500,
    )
    return _parse_json_object(response.choices[0].message.content or "")


def _content_contract_result_usable(result: dict | None) -> bool:
    if not isinstance(result, dict):
        return False
    action = result.get("action")
    if action not in CONTENT_ACTIONS:
        return False
    if action == "answer":
        return bool(result.get("response")) and bool(result.get("no_change_reason"))
    if action == "forward_to_visual":
        return bool(result.get("response"))
    if action in CONTENT_MUTATION_ACTIONS:
        return _content_action_payload_complete(result)
    return False


def _enforce_content_action_contract(
    *,
    result: dict,
    user_message: str,
    project_context: dict,
    client=None,
    documents: str = "",
    page_context: dict | None = None,
    slides_context: list[dict] | None = None,
    attachment_context: str = "",
    history: list[dict] | None = None,
    compiler=None,
    logger=None,
) -> dict:
    if not _content_result_needs_contract_review(result, is_draft=False):
        return result
    pending_offer = _infer_pending_content_plan_offer(history or [], project_context)
    if (
        pending_offer
        and result.get("action") == "forward_to_visual"
        and _is_simple_confirmation(user_message)
    ):
        return _fallback_regenerate_plan(
            pending_offer["excerpt"],
            project_context,
            "收到，我会先把这版内容规划真正生成出来，再进入视觉阶段。",
            page_count=pending_offer.get("page_count"),
        )
    strong_mutation_intent = _requires_content_mutation_action(user_message, result, project_context)

    try:
        if compiler:
            compiled = compiler(
                user_message=user_message,
                project_context=project_context,
                documents=documents,
                page_context=page_context,
                slides_context=slides_context,
                attachment_context=attachment_context,
                initial_result=result,
            )
        else:
            compiled = _compile_content_instruction_with_llm(
                client=client,
                user_message=user_message,
                project_context=project_context,
                documents=documents,
                page_context=page_context,
                slides_context=slides_context,
                attachment_context=attachment_context,
                initial_result=result,
            )
    except Exception as exc:
        if logger:
            logger.warning("[Chat] Content action contract compiler failed: %s", exc)
        compiled = None

    if _content_contract_result_usable(compiled):
        if compiled.get("action") == "regenerate_plan":
            page_update = _fallback_update_current_slide_from_attachments(
                user_message=user_message,
                page_context=page_context,
                attachment_context=attachment_context,
                response=result.get("response"),
            )
            if page_update:
                return page_update
        if compiled.get("action") in {"answer", "forward_to_visual"} and strong_mutation_intent:
            page_update = _fallback_update_current_slide_from_attachments(
                user_message=user_message,
                page_context=page_context,
                attachment_context=attachment_context,
                response=result.get("response"),
            )
            if page_update:
                return page_update
            return _fallback_regenerate_plan(user_message, project_context, result.get("response"))
        if logger:
            logger.info(
                "[Chat] Content action contract compiled %s -> %s",
                result.get("action"),
                compiled.get("action"),
            )
        return compiled

    if result.get("action") in CONTENT_MUTATION_ACTIONS and not _content_action_payload_complete(result):
        page_update = _fallback_update_current_slide_from_attachments(
            user_message=user_message,
            page_context=page_context,
            attachment_context=attachment_context,
            response=result.get("response"),
        )
        if page_update:
            return page_update
        return _fallback_regenerate_plan(user_message, project_context, result.get("response"))
    if strong_mutation_intent:
        page_update = _fallback_update_current_slide_from_attachments(
            user_message=user_message,
            page_context=page_context,
            attachment_context=attachment_context,
            response=result.get("response"),
        )
        if page_update:
            return page_update
        return _fallback_regenerate_plan(user_message, project_context, result.get("response"))
    if result.get("action") in {"answer", "collect_content"}:
        return {
            "action": "answer",
            "response": "我没有把这条指令落成可执行的内容改动，因此没有修改 PPT。请明确要改哪一页，或告诉我是否要重做整套内容规划。",
            "no_change_reason": "content_instruction_compiler_failed",
        }
    return result


def _get_current_stage_description(project_context: dict) -> str:
    """根据项目状态返回当前阶段描述，让 LLM 明确知道用户在哪个 Gate 阶段以及该确认什么。"""
    status = project_context.get("status", "")
    content_confirmed = project_context.get("content_plan_confirmed", False)
    has_selected_style = project_context.get("has_selected_style", False)
    has_prompts = project_context.get("has_prompts", False)
    has_images = project_context.get("has_images", False)

    if status == "draft":
        return "需求收集阶段 —— 用户正在确认需求和素材，待确认物：定调摘要"
    if status in ("planning", "content_plan_ready"):
        if not content_confirmed:
            return "内容规划待确认阶段 —— 待确认物：整套内容规划（页数、标题、顺序、文案）"
        return "内容规划已确认，等待进入视觉阶段"
    if status == "visual_ready":
        if not has_selected_style:
            return "视觉方向待确认阶段 —— 待确认物：风格提案（配色、气质、字体、整体调性）"
        if not has_prompts:
            return "画面方案待生成阶段 —— 待确认物：每页的视觉描述和生图方案（尚未生成）"
        return "画面方案已生成，等待样张确认"
    if status in ("prompt_ready", "failed"):
        if not has_prompts:
            return "画面方案待生成阶段 —— 待确认物：每页的视觉描述和生图方案"
        if not has_images:
            return "画面方案已生成，等待样张确认 —— 待确认物：样张效果"
        return "部分页面已生成样张，等待全部确认"
    if status in ("prototype", "prototype_ready"):
        if has_images:
            return "样张已生成，等待确认批量生成 —— 待确认物：样张效果"
        return "样张生成中"
    if status == "generating":
        return "批量生成中"
    if status == "completed":
        return "已完成"
    return f"未知阶段（状态：{status}）"


def _build_draft_prompt(has_documents: bool) -> str:
    """draft 阶段（无 slides）的对话收集 prompt。"""
    doc_hint = """
【重要：用户已上传文档素材】
用户已经上传了文档（PDF / Word / PPT / Markdown 等），文档内容已包含在系统上下文中。你必须：
1. 基于这些文档内容来回答，绝对不要要求用户重新发送文档内容。
2. 仔细阅读文档，提取核心主题、关键论点、数据。
3. 在追问时引用文档中的具体内容来确认理解。
4. 最终生成时把文档内容作为核心素材融入主题描述。""" if has_documents else ""

    return f"""你是 PPT GOD 的内容总监。你有三重背景：TED演讲教练、麦肯锡咨询顾问、顶尖商业文案。你不是问答机器人，你是在帮用户导演一场演示。

你的任务是通过多轮对话帮用户把 PPT 需求理清楚，然后输出定调摘要，等用户确认后再生成。

【绝对长度上限】
- "response" 字段是给用户的"聊天回复"，必须 ≤200 字。
- 详细页面规划（每页内容、子标题、列表）**绝对禁止写进 response**。详细规划由后续 generate_plan 触发的 Celery 任务输出，不在这次回复的职责范围。
- 即使用户要求"再丰富一点"、"按原话还原"等让你想多写的指令，response 仍必须 ≤200 字，把丰富放到 positioning.key_highlights 数组里、把每页要点放到 positioning.strategy 里，绝不在 response 直接铺陈一大堆 markdown。
- 违反此长度规则会导致 JSON 被截断、用户卡住，是严重故障。

{doc_hint}

【场景推断规则】
根据用户输入自动判断场景类型，不要问用户"你要什么类型"：
- 年终总结/述职/业绩报告/公司介绍/讲义/培训 → reading（阅读/汇报型，侧重逻辑清晰、数据突出）
- 产品发布/品牌路演/keynote/演讲 → presentation（演讲驱动型，侧重情绪节奏、钩子、高潮）
- 客户提案/方案/商业计划书 → mixed（混合型）

【工作流 action 说明】
- "diagnose"：首轮对话且信息极少时（如用户只说"帮我做个PPT"），先给出场景诊断和策略建议
- "collect_content"：信息不够，追问1-2个关键问题。但**每次追问时必须附带下一步预告**，比如"回答这两个问题后，我会立即为你生成内容规划定调"。**禁止只问问题不给方向**。
- "propose_plan"：信息已足够，**立即**输出定调摘要，**绝对禁止再追问**。用户看到摘要后，会自己决定是否点击"开始生成"。
- "generate_plan"：当用户明确表达"立即开始生成"的意图时触发。包括但不限于这些表达："直接生成"、"开始生成"、"就这样"、"开始吧"、"生成吧"、"确认生成"、"生成"、"走起"、"开搞"、"开始制作"。**只要用户表达了明确的立即开始意图，即使措辞不在列表中，也应触发 generate_plan。不要在用户只回复"ok"、"好的"、"明白了"时触发 generate_plan。**
- "answer"：用户问无关问题，正常回答

【信息足够判定标准 —— 满足以下任意两项就必须输出 propose_plan，绝对禁止返回 collect_content】
1. 有明确的主题/标题（如"销售训练营"、"年终汇报"）
2. 有明确的场景类型或目标受众（如"内部培训"、"客户提案"、"给老板看"）
3. 有核心内容方向或关键信息点（如"基于刚上传的文档"、"关于AI应用"）
4. 用户已上传文档并明确表达"做成PPT"等制作意图
**当满足两项及以上时，action 必须是 "propose_plan" 或 "generate_plan"，绝对禁止返回 "collect_content"或反问用户。**

【推动原则 —— 绝对禁止停在反问】
1. 当用户已上传文档并明确表达"做成PPT""做一个精美的PPT"等制作意图时，**信息已足够，直接输出 propose_plan**，不要反问"你想做什么"。
2. 当用户给出主题 + 明确场景（如"销售训练营""年终汇报"）时，**信息已足够，直接输出 propose_plan**，不要再问"这是什么场景"。
3. Agent 的每次回复都必须给出**明确的下一步**：要么直接输出定调摘要，要么告诉用户"再确认X和Y两点，我就立即开始生成"。**禁止把决策成本抛给用户**。
4. 如果用户回复了"内部青年销售训练营。我要做成一个精美的 ppt"这类明确指令，你的 action 必须是 "propose_plan"，response 里直接给出定调摘要和结构建议。
5. **【绝对规则·调整提案】当对话历史中已经存在一次 propose_plan（即用户已经看过定调摘要），且用户现在提出任何修改、补充、调整意见（如"再加一页"、"主题改成XX"、"结构不对"、"太长了"等），你必须：**
   - **返回 action="propose_plan"**（绝对禁止返回 collect_content 或 answer 然后文字描述）
   - **在 positioning 字段中输出更新后的完整定调摘要**，基于之前的 positioning 只修改用户要求改的部分
   - **response 里用一两句话点出"我调整了 X"**，让用户一眼看出差异
   - 示例：用户说"再加一个案例页"，你必须返回 `{{"action":"propose_plan","response":"已在前面的结构中加入案例页，亮点也同步更新。","positioning":{{...完整对象...}}}}`

【输出 JSON 格式】
{{
  "action": "diagnose" | "collect_content" | "propose_plan" | "generate_plan" | "answer",
  "response": "给用户的友好中文回复，用内容总监的口吻，专业但有温度。不要出现'diagnose'、'propose_plan'等技术词汇。每次回复都必须包含下一步行动指引，不能停在反问。",
  "scene_type": "reading" | "presentation" | "mixed" | null,
  "diagnosis": {{  // 仅在 action="diagnose" 时输出
    "input_type": "raw_document" | "vague_request" | "mature_outline" | "data_report",
    "suggested_strategy": "人话描述策略，如'建议先抛核心数据做钩子，再展开过程'",
    "confidence": 0.8,
    "missing_focus": ["还缺的关键信息"]
  }},
  "positioning": {{  // 仅在 action="propose_plan" 时输出
    "core_thesis": "一句话核心洞察",
    "strategy": "整体结构策略，人话描述",
    "tone": "文案调性，如'克制专业，数据驱动'",
    "estimated_pages": 12,
    "key_highlights": ["亮点1", "亮点2", "亮点3"]
  }},
  "title": "为项目起一个简洁的中文标题，8-15字，能概括主题。不要照搬用户原文，要提炼。如用户说'做一份关于AI在医疗领域应用的PPT'，标题应为'AI医疗应用洞察'",
  "topic": "整理后的完整主题描述。propose_plan 和 generate_plan 时都必须输出，供生成接口使用"
}}

【规则】
- 用户明确说"直接生成"、"不用问了"、"就这样"、"开始吧" → action="generate_plan"
- 用户只给模糊需求（如"帮我做个PPT"）→ 先 action="diagnose" 给出判断和建议，同时指出还缺什么
- 不要问用户"你要什么框架"，直接推断并给出建议
- 每次只追问1-2个问题
- 必须只返回合法JSON，不要markdown代码块，不要任何解释性文字"""


def _build_visual_prompt(content_plan_summary: str, assets_summary: str = "", num_proposals: int = 3) -> str:
    """视觉总监的 system prompt。num_proposals: 本次将生成几套提案（1 或 3），需注入到 prompt 让 LLM 不要幻觉数字。"""
    asset_section = f"\n\n【用户已上传的设计素材】\n{assets_summary}\n" if assets_summary else ""

    count_constraint = (
        f"\n【方案数量硬约束】本次将生成 {num_proposals} 套风格提案。"
        f"凡涉及方案数量的口径必须使用「{num_proposals} 套」，禁止换成其他数字（如：N=1 时禁说「3 套/三套/多套」；N=3 时禁说「1 套/一套」）。"
        f"用户对方案数量很敏感，幻觉数字会让用户困惑。"
    )

    # 根据是否有素材，调整首次介入的策略
    if assets_summary:
        first_interaction_rule = """- **首次介入时，用户已经上传了设计素材**。你的任务是：
  1. 简要确认收到的素材（如"已收到你的品牌 Logo、2个可复用素材和3张风格参考"）
  2. 询问用户是否还有其他素材需要补充
  3. 如果素材已经足够，返回 action="propose_styles" 推进到风格提案生成
  4. 如果系统上下文只告诉你"已上传风格参考/版式模板"但没有给出图片的颜色、构图、字体等分析细节，你只能在 response 中说明将由后端读取素材并提取真实视觉特征，**不要自己编造 style_proposal 对象**
  5. 如果用户想补充素材，等待补充后再提案"""
    else:
        first_interaction_rule = """- **首次介入时，用户还没有上传任何设计素材**。你的首要任务是**引导用户上传设计素材**。回复结构：自我介绍（1句）+ 按参考强度从高到低询问用户是否有以下素材可以上传：品牌 Logo、可复用素材（产品/主 KV/人物/物料图）、风格参考、版式模板、文字风格描述（清晰列出5项）+ 说明上传这些素材如何帮助提案和后续画面更精准。
- **绝对不能**在首次回复中直接给出配色方案、字体建议、风格判断或完整的视觉分析。你必须先确认用户的素材情况。"""

    return f"""你是 PPT GOD 的视觉总监。你有三重背景：顶尖平面设计师、品牌视觉顾问、演示设计专家。你不是模板推荐机器人，你是在帮客户制定视觉策略。

【绝对规则】你必须且只能输出合法的 JSON 对象。不要输出任何解释性文字、markdown 代码块、HTML 标签或多余的自然语言。无论用户说什么，你的每一次回复都必须是且只能是一个可被直接解析的 JSON 对象。违反此规则会导致系统错误。

【绝对长度上限】
- "response" 字段是给用户的"聊天回复"，必须 ≤200 字。
- 详细风格说明、配色逻辑、设计推理**不要写进 response**，写进 style_proposal.description（150-250 字）。
- response 只用一两句话点明：风格名 + 一句调性 + 下一步指引。
- 即使用户要求"详细一点"、"再具体说说"，response 仍 ≤200 字，把详细放进 style_proposal.description 或 style_proposal.mood 字段，绝不在 response 铺陈大段文字。
- 违反此长度规则会导致 JSON 被截断、用户卡住，是严重故障。

你的任务是根据客户的内容规划，为他们制定视觉策略、提案风格方案，并解答视觉相关咨询。**你不是问答机器人，你是流程推进者。每次回复都必须给用户明确的下一步指引，不能停在反问或让用户体验到"我不知道该做什么"。**

【当前项目内容规划】
{content_plan_summary}{asset_section}{count_constraint}

【素材优先级规则】
- 如果用户上传了品牌 Logo，Logo 默认作为预览/PPTX 阶段的统一角标叠加，不作为每页生图垫图；只有用户明确把它作为画面主体，且页面适合品牌招牌/主视觉标识时，才作为画面资产使用。
- 如果用户上传了可复用素材（产品图、主 KV、模特图、物料图等），它不是风格来源，也不是每页全局必用素材；它是后续画面生成的可复用内容资产，只在相关页面智能调用，用于提高产品/人物/物料准确度。
- 如果用户上传了风格参考，风格参考是最高优先级的风格来源：提取其色彩关系、字体气质、材质、装饰密度和构图节奏，转成文字风格系统；不要把风格参考当作每页生图垫图。
- 如果用户上传了版式模板，模板用于拆分和匹配封面、目录、内容、结尾等页面类型；模板适度影响版式和视觉秩序，具体配图仍由每页文案决定。
- 上传风格参考/版式模板后，内容规划用于判断页面类型、信息密度和具体配图；不得根据内容里的行业热词推翻素材本身的视觉气质。
- 强视觉单页参考只用于定调。封面/章节/转场/金句页可以强化主色和装饰；内容/数据/表格/长文页必须优先可读，降低背景强度、减少装饰、增加留白。
- 如果你无法看到图片细节，只能触发 action="propose_styles" 让后端图像分析生成，不要输出臆测的 style_proposal。

【工作流 action 说明】
- "collect_assets"：用户还没有素材，或素材不够，你需要引导用户上传/描述更多设计素材。**不要直接输出风格提案**。
- "propose_styles"：用户已上传素材，或在聊天中明确表示"没有素材""直接提案吧""你推荐吧""生成风格提案""确认素材"之后，基于已有信息输出风格提案。如果有素材，必须基于素材来阐述风格；如果没有素材，基于内容自行推荐。
- "adjust_style"：用户对已有提案提出调整意见（如"太冷了"、"太花哨"、"更商务一点"），你在 response 中说明调整思路。
- "confirm_style"：用户明确确认选择某个风格（如说"ok"、"就用这个"、"确认"、"选这个"等），返回此 action 并带上完整的风格对象。系统会自动保存该风格并进入下一步。
- "reroll_page_visual_plan"：用户在单页/页面上下文里表达"再来一版"、"这个不满意"、"换个方向"等，希望**重新生成**这一页画面方案（由 LLM 自动重新写）。这个 action 只更新画面描述和生图提示词，不生图。
- "update_slide_visual"：用户给出**明确的、具体的画面修改指令**（如"这一页加入里尔克的头像"、"背景换成深海蓝"、"把参考图放在右侧"），你直接修改该页的 `visual_description` 或 `design_notes`，而不是让 LLM 重新生成一整版。修改后前端会自动更新生图提示词。**单页模式下优先使用此 action，它比 reroll_page_visual_plan 更精准。**
- "update_all_slides_visual"：用户在全局模式下给出影响多页的视觉调整指令（如"所有页面背景都换成深色"、"统一把点缀色改成金色"），你返回多个页面的 visual_json 修改。前端会批量应用并自动更新生图提示词。
- "request_generate_image"：用户表达"可以了"、"生成图片"、"就按这个出图"等希望生图的意图。注意：这会产生成本，你只能返回该 action 让前端提示用户确认，不能直接生图。
- "answer"：用户咨询视觉相关问题，或描述风格偏好（如"要小红书那种温暖生活感的风格"），你正常回答并给出专业建议。
- "forward_to_content"：用户要求修改页面文字内容、重生成内容规划、调整结构等**不属于视觉策略**的需求时，返回此 action，并在 response 中礼貌告知用户"这是内容总监的工作范围，我帮你转接"。

【输出 JSON 格式】
{{
  "action": "collect_assets" | "propose_styles" | "adjust_style" | "confirm_style" | "reroll_page_visual_plan" | "update_slide_visual" | "update_all_slides_visual" | "request_generate_image" | "answer" | "forward_to_content",
  "response": "给用户的友好中文回复，用视觉总监的口吻，专业但有审美品味。",
  "style": {{  // 仅在 action="confirm_style" 时输出，必须包含完整的风格定义
    "name": "风格名称",
    "palette": [
      {{"name": "颜色名称", "hex": "#FF2442", "role": "主色/背景色/标题色/点缀色"}}
    ],
    "mood": "3-5个氛围词",
    "font": "字体建议",
    "description": "风格说明（150-250字）",
    "source": "original"
  }},
  "page_nums": [5],  // 仅在 reroll_page_visual_plan、update_slide_visual、request_generate_image 且能判断页码时输出
  "updated_visual": {{  // 仅在 action="update_slide_visual" 时输出
    "page_num": 2,
    "visual_json": {{
      "visual_description": "修改后的画面描述（只改需要变的部分，保留其他原有内容）",
      "design_notes": "可选的设计备注"
    }}
  }},
  "updated_slides_visual": [  // 仅在 action="update_all_slides_visual" 时输出
    {{ "page_num": 1, "visual_json": {{ "visual_description": "..." }} }},
    {{ "page_num": 3, "visual_json": {{ "visual_description": "..." }} }}
  ],
  "style_proposal": {{  // 仅在 action="propose_styles" 或 action="adjust_style" 时输出
    "name": "风格名称（简洁直观）",
    "palette": [
      {{"name": "颜色名称", "hex": "#FF2442", "role": "主色/背景色/标题色/点缀色"}},
      ...
    ],
    "mood": "3-5个氛围词",
    "font": "字体建议",
    "description": "风格说明（150-250字，具体说人话，不要堆砌形容词）",
    "source": "original"
  }}
}}
- style_proposal 的 palette 必须包含 4 个颜色，每个颜色必须有 name、hex（6位大写）、role
- style_proposal 的 description 要和 response 里的风格描述保持一致

【规则】
{first_interaction_rule}
- **【绝对规则】当用户已提供足够素材、或明确表达了风格偏好（如描述了喜欢的配色、风格、场景）、或明确表示"直接提案吧/你推荐吧/生成风格提案/确认素材"时，必须返回 action="propose_styles"。如果素材是用户上传的品牌 Logo、可复用素材、风格参考或版式模板且你看不到图片细节，不要输出 `style_proposal`，让后端图像分析生成；如果是用户用文字明确描述了风格，则在 `style_proposal` 字段中输出完整的结构化风格提案。禁止只返回 action="answer" 和文字描述。**
- **【绝对规则·调整提案】当系统上下文里已存在「当前已存在的风格提案」，且用户提出任何调整意见（如"换个色"、"太花哨了"、"不要三分式"、"更暖一点"、"加点深色"等），你必须：**
  1. **返回 action="adjust_style"**（绝对禁止只返回 action="answer" 然后文字描述新方案）
  2. **在 `style_proposal` 字段中输出一个完整的新提案对象**，name/palette（4色全部带 hex）/mood/font/description/source 一个不能少
  3. **以"当前已存在的风格提案"为基础，只修改用户要求改的部分**（如用户说"换个色"，可以替换 palette；用户说"字体硬一点"，只改 font；其余字段保持不变或微调）
  4. **response 里用一两句话点出"我把 X 改成了 Y"**，让用户一眼看出差异
  5. 示例：用户说"主色太红了，换个温暖一点的"，你必须返回 `{{"action":"adjust_style","response":"我把主色从冷红 #E60012 换成了暖橘 #FF6B35，其余配色保持不变。","style_proposal":{{...完整对象...}}}}`
- **当用户提出调整意见但当前还没有任何提案时**（即上下文里没有"当前已存在的风格提案"），你应当返回 action="propose_styles" 并按用户意见生成首次提案，而不是 adjust_style。
- **【阶段优先规则·风格提案阶段】如果上下文里存在「当前风格提案」且尚未被用户确认，用户给出的任何与配色、风格气质、页面类型处理（如"内容页用浅色"、"封面要大气"、"数据页不要太花哨"）相关的反馈，必须优先视为对风格提案的调整，返回 action="adjust_style"，在 style_proposal 中输出调整后的新提案。禁止把这些反馈理解为对具体页面的修改（update_slide_visual / update_all_slides_visual）。**
- **【阶段优先规则·画面方案阶段】如果风格已经确认（用户已选风格），但画面方案尚未生成或刚生成还未确认，用户给出的任何与配色、风格气质、整体画面方向、页面类型处理（如"内容页用浅色"、"封面要大气"、"数据页不要太花哨"）相关的反馈，必须优先视为对已确认风格的进一步调整，返回 action="adjust_style"，在 style_proposal 中输出调整后的新提案（这会触发重新生成画面方案）。只有当用户明确指定单页并给出具体画面修改指令时，才返回 update_slide_visual / reroll_page_visual_plan。**
- **每次回复都必须包含下一步行动指引**。比如：提案后告诉用户"满意请确认，不满意告诉我调整方向"；确认风格后告诉用户"正在进入画面设计阶段"；调整画面后告诉用户"调整已应用，可以确认生成图片"。禁止只回答用户当前问题而不给下一步方向。
- **当用户明确确认选择某个风格时（如"ok"、"就用这个"、"确认"、"选这个"），必须返回 action="confirm_style"，并在 `style` 字段中输出完整的风格对象。不要只返回 "answer"。**
- 说话要有设计师的品味，但不要说空话套话。具体、有观点。
- 如果用户提到颜色、字体、排版、风格，给出专业建议。
- 如果用户在单页里给出**具体的画面修改指令**（如"加入里尔克的头像"、"背景换成深蓝"、"人物放左边"、"参考图更突出"），返回 action="update_slide_visual"，并在 `updated_visual` 中给出修改后的 `visual_description`。这是**首选 action**，比 reroll_page_visual_plan 更精准。
- 如果用户在单页里说"再来一版""不满意""换个方向"等模糊的重做需求，返回 action="reroll_page_visual_plan"。
- 如果用户在全局模式下说"所有页面都..."、"统一改成..."等影响多页的指令，返回 action="update_all_slides_visual"，在 `updated_slides_visual` 数组中给出每页的修改。
- 如果用户说"可以了，生成图片""确认生图""就按这个出图"，返回 action="request_generate_image"，并提醒用户需要在页面中确认，避免误产生生图成本。
- 你绝不能直接触发真实生图。任何会产生成本的动作，都必须让用户在 UI 中确认。
- **当用户描述某个品牌/平台的风格偏好时（如"要小红书那种风格"、"想要温暖生活感的调性"），使用 action="answer"，在 response 中确认理解该风格特征，并给出基于此风格的专业建议。不要返回空内容。**
- **【关键】每次回复的末尾，必须根据当前素材状态，明确告诉用户下一步可以点击什么按钮。格式：另起一行写 "👉 下一步：..."**
  - 如果用户已上传素材或描述了风格，但还没生成提案：👉 下一步：点击「确认素材已齐，生成风格提案」按钮，我立即开始
  - 如果用户素材明显不够（只有文字描述，没有图）：👉 下一步：你可以继续上传品牌 Logo、可复用素材、风格参考或版式模板，补完后点击「确认素材已齐，生成风格提案」
  - 如果风格提案已生成，等待用户选择：👉 下一步：请查看页面或本条消息里的风格卡片，点击「选择此方案」确认，或告诉我你的调整意见（如「更暖一些」「更现代一点」）
  - 如果你刚刚返回了 action="adjust_style"（即调整后的新提案）：👉 下一步：请查看本条消息下方的调整后方案卡片，满意请点「选择此方案」，不满意继续告诉我哪里需要再改
  - 如果用户在聊天中直接确认风格（如说"ok"、"选这个"）：返回 action="confirm_style"，👉 下一步：已确认，正在进入画面设计阶段
  - 如果用户已选风格，还没生成生图方案：👉 下一步：正在生成画面描述，请稍候
  - 如果是纯咨询问题：👉 下一步：如果还有其他视觉问题随时问我，或者点击按钮继续推进
- 必须只返回合法JSON，不要markdown代码块，不要任何解释性文字"""


def _build_normal_prompt() -> str:
    """有 slides 后的内容执行阶段 prompt（内容总监）。"""
    return """你是 PPT GOD 的内容总监。你有三重背景：TED演讲教练、麦肯锡咨询顾问、顶尖商业文案。用户已经进入了内容执行阶段，你的任务是根据用户指令执行内容操作或给出专业建议。**你不是问答机器人，你是流程推进者。每次回复都必须给用户明确的下一步指引，不能停在反问或让用户体验到"我不知道该做什么"。**

解析用户意图并返回 JSON：
- "action": "regenerate_pages" | "retry_failed" | "update_style" | "update_slide_content" | "update_all_slides" | "regenerate_plan" | "add_slide_before" | "add_slide_after" | "forward_to_visual" | "answer"
- "page_nums": int[]（regenerate_pages 时提取页码）
- "style_id": string（update_style 时）
- "updated_content": object（update_slide_content 时，返回该页完整的 content_json，必须包含 page_num、type、section_title、text_content、speaker_notes、visual_suggestion）
- "updated_slides": object[]（update_all_slides 时，数组中每个元素只需包含 page_num 和 text_content）
- "new_slide": object（add_slide_before / add_slide_after 时，返回新页的完整 content_json，必须包含 page_num、type、section_title、text_content、speaker_notes、visual_suggestion）
- "topic": string（regenerate_plan 时必须输出，完整的主题描述用于重新生成内容规划）
- "page_count": number（regenerate_plan 时可选，用户明确要求多少页就输出多少页，未提及则不输出）
- "response": string（给用户的中文回复）

规则：
- "重新生成第X页" / "重做第X页" → action="regenerate_pages"
- "重试失败" / "重新生成失败的页" → action="retry_failed"
- 用户明确要求修改某一页 → action="update_slide_content"
- 用户要求修改全部页面、全局调整、整体改写文字 → action="update_all_slides"
- **用户反馈整体内容质量问题时（叙事/主线/脉络/结构/逻辑/节奏/论证不完整、不清楚、不连贯、像罗列素材、没有递进或缺少转折）→ action="regenerate_plan"，不要只口头答应；topic 中必须写明需要重构整体内容规划，并补足缺失的铺垫、冲突、转折、证据或结尾。**
- **用户提到"按照 content plan"、"按照原文/文档"、"完全按照...来"、"按原来的大纲"等，意图是让现有页面内容对齐文档/大纲时 → action="update_all_slides"，不要只口头答应**
- **用户要求"重新生成内容规划"、"重新规划页面"、"按大纲重新来"、页数需要增减变化时 → action="regenerate_plan"，并在 topic 字段中输出完整的主题描述（用于重新生成内容规划）**
- **【阶段优先规则·内容规划阶段】如果内容规划尚未被用户确认（上下文显示"内容规划已确认：否"），用户给出的任何与叙事结构、页面顺序、逻辑脉络、整体节奏、内容完整性、页数增减相关的反馈，必须优先视为对整套内容规划的调整，返回 action="regenerate_plan"（重构整套规划）或 "update_all_slides"（全局文字调整）。禁止把这些反馈理解为对单页内容的局部修改（update_slide_content），除非用户明确指定了具体页码和具体修改内容。**
- 用户说"在第X页前面加一页"、"在前面插入一页"、"加一页" → action="add_slide_before"
- 用户说"在第X页后面加一页"、"在后面插入一页"、"追加一页" → action="add_slide_after"
- 如果用户提出的是偏视觉呈现的要求（如"后面视觉上突出这些数字/时间轴/对比关系"），但当前仍处于内容总监阶段：不能无响应。若这条要求会影响内容规划的页面类型、重点数据、标题或叙事顺序，返回对应内容 mutation；若只影响后续视觉表达，返回 action="answer"，明确说明内容层面已记录/会带入后续视觉阶段，并建议进入视觉总监后继续强调该视觉要求。
- **【规则】当上下文显示"内容规划已确认：是"，且用户只回复了简单的确认性词语（如"ok"、"好的"、"明白了"、"可以"）或闲聊时，返回 action="forward_to_visual" 引导用户切换到视觉总监，不要反问用户"是否需要生成PPT"。**
- **但如果用户有明确的内容操作意图（如"修改"、"添加"、"删除"、"调整"、"重写"、"加一页"、"改标题"、"按文档更新"等），正常处理用户请求，执行相应操作，并在 response 末尾提示用户"内容调整完成后，可点击上方切换到视觉总监继续"。**
- 其他 → action="answer"
- 如果用户提到"文档""原文""MD""文件"里的内容，请基于已上传的文档内容回答，不要反问用户。
- update_slide_content 时：
  1. 必须在 updated_content 中返回该页完整的 content_json（包含所有字段）
  2. 只修改用户明确要求改的部分，其他字段保持原样
  3. 同步在 response 中简要说明改了什么
  4. **response 中必须告诉用户下一步该做什么**（如"如需继续调整其他页面请告诉我，或点击上方切换到视觉总监进入设计阶段"）
- update_all_slides 时：
  1. 在 updated_slides 数组中返回需要修改的页面，每个元素格式：{"page_num": N, "text_content": {"headline":"...","subhead":"...","body":"markdown正文..."}}
  2. 只返回确实需要改的页面，无需修改的页面不要出现在数组中
  3. body 是 markdown 格式的字符串，不是数组
  4. **response 中必须告诉用户下一步该做什么**
- add_slide_before / add_slide_after 时：
  1. 必须在 new_slide 中返回新页完整的 content_json（包含所有字段）
  2. page_num 填用户指定的目标位置页码；如果用户没有明确指定，填当前上下文中的 page_num
  3. type 根据内容推断（cover/toc/section/content/data/hero/ending），默认 content；重大章节转换或叙事转折用 section
  4. 如果用户没有提供具体内容，生成与上下文风格一致、自然过渡的页面内容
  5. **response 中必须告诉用户下一步该做什么**
- 只返回 JSON，不要 markdown。
- 【重要】JSON 字符串值中如果包含双引号 "，必须转义为 \"。建议避免在字符串中使用双引号，可用中文引号「」或单引号代替。

示例输出（必须严格遵循此格式）：
{"action": "regenerate_pages", "page_nums": [3, 4], "response": "好的，正在重新生成第3页和第4页。"}
{"action": "update_slide_content", "updated_content": {"page_num":1,"type":"cover","section_title":"","text_content":{"headline":"新标题","subhead":"新副标题","body":""},"speaker_notes":"","visual_suggestion":""}, "response": "已更新封面标题和副标题。"}
{"action": "update_all_slides", "updated_slides": [{"page_num":1,"text_content":{"headline":"...","subhead":"...","body":"markdown正文..."}},{"page_num":2,"text_content":{"headline":"...","subhead":"...","body":"markdown正文..."}}], "response": "已根据原文调整所有页面。"}
{"action": "add_slide_after", "new_slide": {"page_num":3,"type":"content","section_title":"","text_content":{"headline":"新标题","subhead":"新副标题","body":""},"speaker_notes":"","visual_suggestion":""}, "response": "已在第3页后插入新页。"}

- 必须只返回合法 JSON，不要 markdown 代码块，不要任何解释性文字。确保 JSON 可以被直接解析。"""


def _build_finetune_prompt(current_slide_info: str = "") -> str:
    """单页微调 Agent 的 system prompt。"""
    slide_context = f"\n\n【当前正在微调的幻灯片】\n{current_slide_info}" if current_slide_info else ""

    return f"""你是「单页微调总监」—— 一个专注幻灯片单页精准修改的 AI Agent。

你的核心职责：根据用户的修改指令，对当前这一页幻灯片进行**外科手术式的精准调整**。

## 铁律（最高优先级）

1. **用户没提到的，一个字、一个像素都不能改**
   - 用户只说"把标题加粗" → 只改标题字重，不改颜色、不改位置、不改其他文字
   - 用户只说"换张图" → 只替换指定图片，不改文字、不改布局、不改配色
   - 不存在"顺便优化一下"—— 任何用户未明确授权的内容都是禁区

2. **精准理解修改范围**
   - 改文字 → 只替换文字内容，保持字号/颜色/位置/字体不变
   - 改配色 → 只调整指定元素的颜色，不牵连同页其他元素
   - 改图片 → 只修改指定区域/图片，其他区域原封不动
   - 改布局 → 只移动指定元素，不影响其他内容

3. **你的输出是生图指令**
   - 你需要输出一个 `refine_slide` action，其中 `new_prompt` 是对当前页的完整生图 prompt
   - 这个 prompt 必须包含：原始图片的所有内容描述 + 用户要求的精准修改
   - 在 prompt 中明确标注："保持所有未提及的元素与原始图片完全一致"

4. **上下文隔离**
   - 你只操作当前这一页，不涉及其他页面
   - 你不修改内容规划（content_json），只修改视觉呈现（通过生图 prompt）

## 输出格式

严格返回以下 JSON（只返回 JSON，不要任何其他文字）：

{{"action": "refine_slide", "new_prompt": "完整的生图 prompt...", "response": "对用户说的话（简短说明修改了什么）"}}

如果用户的话不涉及具体修改（如闲聊、问问题），返回：
{{"action": "answer", "response": "你的回复"}}{slide_context}"""


def _history_has_prior_proposal(history: list[dict], agent_role: str) -> bool:
    """检查对话历史中是否已经存在过内容/视觉提案。用于兜底时判断用户是否在'反馈调整'阶段。"""
    target_actions = {"propose_plan", "propose_styles", "adjust_style"}
    for h in history:
        if h.get("role") != "assistant":
            continue
        content = h.get("content", "")
        if not isinstance(content, str):
            continue
        # 简单字符串匹配，不用 JSON 解析（历史消息可能不是 JSON）
        if any(f'"action": "{a}"' in content for a in target_actions):
            return True
        # 兼容无引号或单引号的情况
        if any(f"'action': '{a}'" in content for a in target_actions):
            return True
    return False


def _visual_style_requirement_text(user_message: str, result: dict, history: list[dict] | None = None) -> str:
    parts: list[str] = []
    for item in (history or [])[-10:]:
        if item.get("role") in {"user", "system"}:
            content = str(item.get("content") or "").strip()
            if content:
                parts.append(content)
    if user_message:
        parts.append(user_message)
    if isinstance(result, dict) and result.get("response"):
        parts.append(str(result.get("response") or ""))
    return "\n".join(parts)


def _stream_intent(
    user_message: str,
    project_context: dict,
    history: list[dict],
    documents: str = "",
    page_context: dict | None = None,
    agent_role: str = "content",
    content_plan_summary: str = "",
    assets_summary: str = "",
    slides_context: list[dict] | None = None,
    attachments: list[ReferenceImage] | None = None,
):
    """流式解析用户意图，yield SSE 事件。"""
    import logging
    logger = logging.getLogger(__name__)

    client = get_llm_client()

    # draft 阶段：没有 slides 时（无论 status 是 draft 还是 planning），都视为内容收集阶段
    is_draft = project_context["total_slides"] == 0
    has_documents = bool(documents and documents.strip())
    logger.info(f"Chat stream: project={project_context['title']}, role={agent_role}, is_draft={is_draft}, has_documents={has_documents}, doc_len={len(documents) if documents else 0}, attachments={len(attachments or [])}")

    # 根据 agent_role 选择 system prompt
    if agent_role == "visual":
        system_prompt = _build_visual_prompt(content_plan_summary, assets_summary, num_proposals=project_context.get("num_proposals", 3))
    elif agent_role == "finetune":
        # 单页微调：构建当前页上下文
        current_slide_info = ""
        if page_context:
            cp = page_context.get("current_page", {}) if isinstance(page_context, dict) else {}
            if cp:
                current_slide_info = json.dumps(cp, ensure_ascii=False, indent=2)
        system_prompt = _build_finetune_prompt(current_slide_info)
    elif agent_role == "content":
        if is_draft:
            system_prompt = _build_draft_prompt(has_documents)
        else:
            system_prompt = _build_normal_prompt()
    else:
        # 兜底：未指定角色时按有无 slides 判断
        if is_draft:
            system_prompt = _build_draft_prompt(has_documents)
        else:
            system_prompt = _build_normal_prompt()

    source_intent_context = _source_intent_context_text(project_context)
    if source_intent_context and agent_role == "content":
        system_prompt += "\n\n" + source_intent_context

    # 把用户从 Brief / Agent 窗口上传的文件内容放到上下文中。
    if has_documents:
        if agent_role == "visual":
            system_prompt += (
                "\n\n=== 用户已上传的文件内容（作为视觉理解、页面修改和素材使用依据） ===\n"
                f"{documents}\n"
                "=== 文件内容结束 ==="
            )
        else:
            system_prompt += f"\n\n=== 用户已上传的文件内容（你必须基于这些文件回答） ===\n{documents}\n=== 文件内容结束 ==="

    # 把页面上下文放到 system prompt 中
    if page_context:
        try:
            page_context_prompt = _build_page_context_prompt_section(page_context)
            if page_context_prompt:
                system_prompt += f"\n\n{page_context_prompt}"
        except Exception as e:
            logger.warning(f"Failed to serialize page_context: {e}")

    attachment_context = ""
    if attachments:
        attachment_lines = []
        for idx, ref in enumerate(attachments, start=1):
            label = ref.asset_name or os.path.basename(ref.file_path)
            role_label = {
                "chat_ref": "用户本轮拖入的图片",
                "content_ref": "内容参考图",
                "chart_ref": "图表参考图",
                "visual_asset": "项目素材",
                "finetune_ref": "微调参考图",
            }.get(ref.role, "参考图")
            attachment_lines.append(f"{idx}. {role_label}：{label}")
        system_prompt += (
            "\n\n【用户本轮随消息上传的图片】\n"
            + "\n".join(attachment_lines)
            + "\n你可以直接识别这些图片中的文字、图表、版式和视觉问题。"
            "如果用户要求修改 PPT，必须把图片信息转化为可执行的内容、视觉或单页微调建议/动作。"
        )
        attachment_context = _build_attachment_context(attachments, agent_role)
        if attachment_context:
            system_prompt += (
                "\n\n【图片 OCR 与读图结果】\n"
                f"{attachment_context}\n"
                "必须优先基于这些读图结果回答，不要说自己无法读取图片。"
            )

    stage_desc = _get_current_stage_description(project_context)
    context = f"项目：{project_context['title']}，状态：{project_context['status']}，当前阶段：{stage_desc}，共 {project_context['total_slides']} 页，已完成 {project_context['completed_slides']} 页，内容规划已确认：{'是' if project_context.get('content_plan_confirmed') else '否'}"

    # 把 history 中的 system 操作日志合并到 system prompt 中
    # MiniMax API 不支持 messages 中出现多条 system 角色，所以必须合并
    system_log_parts = []
    for h in history:
        if h.get("role") == "system":
            system_log_parts.append(h.get("content", ""))
    if system_log_parts:
        system_prompt += "\n\n【用户在主舞台的操作日志】\n" + "\n".join(f"- {p}" for p in system_log_parts)
        system_prompt += "\n\n你必须基于上述日志理解用户当前的项目进展和状态，给出精准的建议。不要反问用户\"你做了什么\"，因为日志里已经有了。"

    # 【内容总监】按需搜索实时信息
    if agent_role == "content":
        search_context = get_knowledge_augmenter().augment(user_message)
        if search_context:
            system_prompt += f"\n\n{search_context}"

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    for h in history:
        role = h.get("role")
        if role == "system":
            continue  # 已合并到 system_prompt，不再重复传入
        if role not in ("user", "assistant"):
            role = "assistant"
        messages.append({"role": role, "content": h.get("content", "")})

    user_content = f"上下文：{context}\n用户：{user_message}"
    messages.append({"role": "user", "content": user_content})

    stream = client.chat.completions.create(
        model=get_minimax_llm_model(),
        messages=messages,
        temperature=0.5 if is_draft and agent_role != "visual" else 0.4 if agent_role == "visual" else 0.1,
        # 长度护栏：think 段 + JSON content 总上限。给 think 留 ~3000，给 JSON 留 ~1500。
        # 防止 LLM 把整本规划塞进 response 字段导致流截断、JSON 残废、用户看到"响应未返回完整结果"。
        max_tokens=4096,
        stream=True,
    )

    buffer = ""
    in_think = False
    content_buffer = ""
    full_buffer = ""
    chunk_count = 0

    logger.info(f"Chat stream: starting LLM stream, messages_count={len(messages)}")
    try:
        for chunk in stream:
            if not chunk.choices:
                continue
            delta_obj = chunk.choices[0].delta
            if not delta_obj:
                continue
            delta = delta_obj.content or ""
            buffer += delta
            full_buffer += delta
            chunk_count += 1

            while buffer:
                if not in_think:
                    idx = buffer.find("<think>")
                    if idx == -1:
                        if buffer:
                            yield {"type": "content", "delta": buffer}
                            content_buffer += buffer
                        buffer = ""
                        break
                    else:
                        if idx > 0:
                            yield {"type": "content", "delta": buffer[:idx]}
                            content_buffer += buffer[:idx]
                        buffer = buffer[idx + 7:]
                        in_think = True
                else:
                    idx = buffer.find("</think>")
                    if idx == -1:
                        if buffer:
                            yield {"type": "thinking", "delta": buffer}
                        buffer = ""
                        break
                    else:
                        if idx > 0:
                            yield {"type": "thinking", "delta": buffer[:idx]}
                        buffer = buffer[idx + 8:]
                        in_think = False
    except Exception as stream_exc:
        logger.error(f"Chat stream: LLM stream exception: {stream_exc}", exc_info=True)
        raise

    logger.info(f"Chat stream: LLM stream finished, chunks={chunk_count}, content_len={len(content_buffer)}, full_len={len(full_buffer)}")

    def _try_parse(text: str):
        text = text.strip()
        # 去掉 think 标签（可能被截断或不完整）
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.startswith("json"):
                text = text[4:].strip()

        # 1. 优先用 json_repair 自动修复 LLM 常见的 JSON 错误
        try:
            parsed = json_repair.loads(text)
            # 纯自然语言无 JSON 时 json_repair 常返回 ''，不能当作合法结果（否则会绕过上层 None 兜底）
            if parsed == "":
                pass
            elif isinstance(parsed, list):
                # LLM 偶尔输出 JSON 数组而非对象，拒绝并继续尝试提取对象
                pass
            else:
                return parsed
        except Exception:
            pass

        # 2. 提取第一个 JSON 对象/数组后再次尝试 json_repair
        start_obj = text.find("{")
        start_arr = text.find("[")
        start = start_obj if start_obj != -1 and (start_arr == -1 or start_obj < start_arr) else start_arr
        if start != -1:
            end = text.rfind("}") if text[start] == "{" else text.rfind("]")
            if end != -1 and end > start:
                snippet = text[start:end + 1]
                try:
                    snip_parsed = json_repair.loads(snippet)
                    if snip_parsed == "":
                        pass
                    else:
                        return snip_parsed
                except Exception:
                    pass

        # 兜底：记录解析失败信息以便排查
        preview = text[:200].replace("\n", " ")
        logger.warning(f"[Chat] JSON parse failed after all fixes. Preview: {preview!r}")
        return None

    # 依次尝试解析 content_buffer、full_buffer
    result = _try_parse(content_buffer) or _try_parse(full_buffer)

    if result is None:
        # 尝试从 full_buffer 中提取自然语言回复作为兜底
        clean = full_buffer.strip()
        # 去掉 think 标签内容
        clean = re.sub(r"<think>.*?</think>", "", clean, flags=re.DOTALL).strip()
        # 去掉 markdown 代码块
        clean = re.sub(r"```[\s\S]*?```", "", clean).strip()
        # 去掉首尾引号
        clean = clean.strip('"').strip()

        # 如果清理后仍然像 JSON，强制尝试解析（有时 LLM 把 JSON 包在引号里）
        if clean and (clean.startswith("{") or clean.startswith("[")):
            forced = _try_parse(clean)
            if forced and isinstance(forced, dict) and "action" in forced:
                result = forced

        if result is None:
            # JSON 解析失败时，根据用户消息意图兜底，不要默认反问
            user_msg_lower = user_message.lower()
            force_generate = any(k in user_msg_lower for k in ["直接生成", "开始生成", "就这样", "开始吧", "生成吧", "确认生成", "生成", "走起", "开搞", "开始制作"])
            force_propose = any(k in user_msg_lower for k in ["做个ppt", "做一个", "帮我做", "生成ppt", "做成ppt"])

            # 关键上下文：用户是否已经在"反馈调整"阶段（历史中有过提案）
            has_prior_proposal = _history_has_prior_proposal(history, agent_role) or (
                agent_role == "visual" and bool(project_context.get("has_selected_style"))
            )

            if clean and len(clean) > 5:
                if agent_role == "visual":
                    # 视觉：如果已经有提案，用户反馈应视为调整，而不是普通 answer
                    result = {"action": "adjust_style" if has_prior_proposal else "answer", "response": clean}
                else:
                    if is_draft and force_generate:
                        result = {"action": "generate_plan", "response": clean}
                    elif is_draft and force_propose:
                        result = {"action": "propose_plan", "response": clean}
                    elif is_draft and has_prior_proposal:
                        # 内容：已经有提案，用户现在在提修改意见 → 必须重新 propose_plan，不能 collect_content
                        result = {"action": "propose_plan", "response": clean}
                    else:
                        result = {"action": "answer" if not is_draft else "collect_content", "response": clean}
            else:
                if agent_role == "visual":
                    result = {"action": "answer", "response": "抱歉，我没太理解你的视觉需求。你可以直接描述喜欢的风格（如「想要小红书那种温暖、生活感的调性」），或者上传参考图让我更精准地把握方向。"}
                elif is_draft and force_generate:
                    result = {"action": "generate_plan", "response": "好的，我立即为你开始生成内容规划。"}
                elif is_draft and force_propose:
                    result = {"action": "propose_plan", "response": "好的，我基于你的需求输出内容定调摘要。"}
                elif is_draft and has_prior_proposal:
                    result = {"action": "propose_plan", "response": "已收到你的调整意见，正在重新输出定调摘要..."}
                elif is_draft:
                    result = {"action": "collect_content", "response": "抱歉，我没太听懂，能再详细说说你的需求吗？比如主题是什么、给谁看、核心想传达什么？"}
                else:
                    result = {"action": "answer", "response": "抱歉，我不太理解您的指令，请尝试说\"重新生成第3页\"或\"重试失败的页面\"。"}

    # 角色权限过滤：视觉总监不能返回内容规划相关 action
    if result and isinstance(result, dict):
        if agent_role == "visual":
            allowed_actions = VISUAL_ACTIONS
            if result.get("action") not in allowed_actions:
                result["action"] = "answer"
        elif agent_role == "content":
            allowed_actions = CONTENT_ACTIONS
            if result.get("action") not in allowed_actions:
                result["action"] = "answer"
        elif agent_role == "finetune":
            allowed_actions = FINETUNE_ACTIONS
            if result.get("action") not in allowed_actions:
                result["action"] = "answer"
    elif result and not isinstance(result, dict):
        # 解析结果不是合法对象，强制兜底
        if agent_role == "visual":
            result = {"action": "answer", "response": "抱歉，我没太理解你的视觉需求。你可以直接描述喜欢的风格（如「想要小红书那种温暖、生活感的调性」），或者上传参考图让我更精准地把握方向。"}
        else:
            result = {"action": "answer", "response": "抱歉，我不太理解您的指令，请尝试说\"重新生成第3页\"或\"重试失败的页面\"。"}

    if result and isinstance(result, dict) and agent_role == "content" and not is_draft:
        result = _enforce_content_action_contract(
            result=result,
            user_message=user_message,
            project_context=project_context,
            client=client,
            documents=documents,
            page_context=page_context,
            slides_context=slides_context or [],
            attachment_context=attachment_context,
            history=history,
            logger=logger,
        )

    # 【Fix-3】修改意见强制重生成：JSON 解析成功但 action 不对时，根据上下文强制纠正
    # 场景：用户已经看过提案，现在给出修改意见，但 LLM 返回了 answer/collect_content（常见于流截断后的解析歧义）
    if result and isinstance(result, dict):
        current_action = result.get("action", "")
        if current_action in ("answer", "collect_content"):
            has_prior = _history_has_prior_proposal(history, agent_role) or (
                agent_role == "visual" and bool(project_context.get("has_selected_style"))
            )
            if has_prior:
                # 内容总监：强制 propose_plan，让用户能看到新的提案卡片
                if agent_role == "content" and is_draft:
                    logger.info(f"[Chat] Fix-3 triggered: forced propose_plan from {current_action} (user feedback after prior proposal)")
                    result["action"] = "propose_plan"
                    if not result.get("response"):
                        result["response"] = "已收到你的调整意见，正在重新输出定调摘要..."
                # 视觉总监：强制 adjust_style（有现成提案）或 propose_styles（无现成提案但用户要生成）
                elif agent_role == "visual":
                    logger.info(f"[Chat] Fix-3 triggered: forced adjust_style from {current_action} (user feedback after prior proposal)")
                    result["action"] = "adjust_style"
                    if not result.get("response"):
                        result["response"] = "已根据你的反馈调整风格方案，请查看下方新卡片。"

    # 安全网：确保 result 始终有非空 response 字段，防止前端显示 "..."
    if result and isinstance(result, dict) and result.get("action") not in ("forward_to_visual", "forward_to_content"):
        if not result.get("response"):
            action = result.get("action", "")
            fallback_map = {
                "update_slide_content": "内容已更新，请查看左侧页面预览。",
                "update_all_slides": "所有页面内容已更新，请查看左侧预览。",
                "regenerate_pages": "页面正在重新生成。",
                "regenerate_plan": "内容规划正在重新生成。",
                "retry_failed": "正在重试失败的页面。",
                "update_style": "风格已更新。",
                "add_slide_before": "已在目标页前插入新页面。",
                "add_slide_after": "已在目标页后插入新页面。",
                "collect_content": "请继续告诉我更多细节。",
                "propose_plan": "内容定调摘要已生成，请查看。",
                "generate_plan": "内容规划生成中，请稍候。",
                "diagnose": "分析完成，请查看我的建议。",
                "answer": "",
            }
            result["response"] = fallback_map.get(action) or "操作已完成。"

    # 视觉总监 fallback：如果 JSON 解析失败导致返回了 answer，但用户明显表达了具体修改意图，
    # 再发一次低 temperature 请求强制输出 JSON，避免用户指令被忽略。
    if result and result.get("action") == "answer" and agent_role == "visual":
        modification_keywords = ["加入", "添加", "换成", "改成", "修改", "调整", "放", "移", "删", "加", "换", "改", "去掉", "增加", "放大", "缩小"]
        user_msg_lower = user_message.lower()
        has_modification_intent = any(k in user_msg_lower for k in modification_keywords)
        response_text = (result.get("response", "") or "").lower()
        has_confirmation = any(k in response_text for k in ["好的", "已为你", "已经", "加入", "修改", "调整", "换成", "添加"])
        if has_modification_intent and has_confirmation:
            logger.info("[Chat] Visual director fallback triggered for modification intent")
            try:
                fallback_messages = list(messages)
                fallback_messages.append({"role": "assistant", "content": result.get("response", "")})
                fallback_messages.append({
                    "role": "user",
                    "content": (
                        "请把你刚才的回复转换成合法的 JSON 格式。根据用户指令，action 必须是 update_slide_visual（单页修改）或 update_all_slides_visual（全局修改）。"
                        "updated_visual 或 updated_slides_visual 中必须包含修改后的 visual_description。"
                        "只输出 JSON，不要任何解释文字。"
                    ),
                })
                fallback_response = client.chat.completions.create(
                    model=get_minimax_llm_model(),
                    messages=fallback_messages,
                    temperature=0.1,
                )
                fallback_text = clean_llm_output(fallback_response.choices[0].message.content or "")
                if fallback_text:
                    fallback_parsed = json_repair.loads(fallback_text)
                    if isinstance(fallback_parsed, dict) and fallback_parsed.get("action") in allowed_actions:
                        result = fallback_parsed
                        logger.info(f"[Chat] Visual director fallback success, action={result.get('action')}")
            except Exception as e:
                logger.warning(f"[Chat] Visual director fallback failed: {e}")

    # 【兜底】视觉总监关键词兜底：用户消息明确表达"生成提案/做风格"等意图，
    # 但 LLM 返回了 answer/聊天文本，强制纠正为 propose_styles，确保推进到下一步。
    if result and isinstance(result, dict) and agent_role == "visual" and result.get("action") == "answer":
        propose_keywords = [
            "生成提案", "开始生成", "重新生成", "重新提案",
            "生成风格", "出方案", "出提案", "做风格", "出风格",
            "素材已齐", "可以生成", "开始做"
        ]
        if any(k in user_message for k in propose_keywords) and not _has_visual_generation_intent(user_message):
            logger.info(f"[Chat] Visual director keyword fallback: forced propose_styles from answer (msg={user_message[:50]!r})")
            result = {
                "action": "propose_styles",
                "response": "好的，正在基于你的素材生成风格提案，请稍候..."
            }

    if result and isinstance(result, dict) and agent_role == "visual":
        result = _enforce_visual_action_contract(
            result=result,
            user_message=user_message,
            page_context=page_context,
            logger=logger,
        )

    if (
        result
        and isinstance(result, dict)
        and agent_role == "visual"
        and result.get("action") in {"propose_styles", "adjust_style"}
        and isinstance(result.get("style_proposal"), dict)
    ):
        result["style_proposal"] = enforce_user_style_requirements(
            result["style_proposal"],
            _visual_style_requirement_text(user_message, result, history),
        )

    logger.info(f"Chat stream: yielding result, action={result.get('action') if isinstance(result, dict) else 'n/a'}, content_len={len(result.get('response', '')) if isinstance(result, dict) else 0}")
    yield {"type": "result", "data": result}


@router.post("/{project_id}/chat")
def chat_with_agent(project_id: str, body: ChatMessage, db: Session = Depends(get_db)):
    """Agent 聊天接口：流式返回思考过程和最终结果。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    slides = db.query(Slide).filter(Slide.project_id == project_id).order_by(Slide.page_num).all()
    completed = sum(1 for s in slides if s.status == "completed")
    failed = sum(1 for s in slides if s.status == "failed")

    # 风格提案数量：与 projects.py:188 的判定一致（有 logo/style_ref/template 任一即视为"有风格素材"，生成 1 套；否则 3 套）
    has_style_assets = any(
        ref.role in {"logo", "style_ref", "template"}
        for ref in (project.reference_images or [])
    )
    num_proposals = 1 if has_style_assets else 3

    context = {
        "title": project.title,
        "status": project.status,
        "total_slides": len(slides),
        "completed_slides": completed,
        "failed_slides": failed,
        "has_failed_slides": failed > 0,
        "has_selected_style": bool(project.selected_style),
        "has_prompts": any(bool(s.prompt_text) for s in slides),
        "has_images": any(bool(s.image_path) for s in slides),
        "content_plan_confirmed": project.content_plan_confirmed or False,
        "num_proposals": num_proposals,
    }

    documents = load_project_documents(project_id, parse_missing=True)
    if body.agent_role == "content":
        context["intent_contract"] = _update_project_intent_from_message_if_needed(
            project,
            body.message,
            documents,
            db,
        )
    else:
        context["intent_contract"] = project.intent_contract if isinstance(project.intent_contract, dict) else None
    chat_attachments = _load_chat_attachments(db, project_id, body.attachment_ids)
    if not chat_attachments:
        chat_attachments = _load_ambient_chat_attachments(db, project_id, body.agent_role, body.message)

    # 为视觉总监构建内容规划摘要
    content_plan_summary = ""
    if body.agent_role == "visual" and slides:
        summary_parts = []
        summary_parts.append(f"项目主题：{project.title}")
        summary_parts.append(f"共 {len(slides)} 页，场景类型：{project.status}")
        summary_parts.append("页面结构：")
        for s in slides[:20]:
            tc = s.content_json.get("text_content", {}) if s.content_json else {}
            headline = tc.get("headline", "") if isinstance(tc, dict) else ""
            ptype = s.type or "content"
            summary_parts.append(f"  第{s.page_num}页（{ptype}）：{headline}")
        content_plan_summary = "\n".join(summary_parts)

    slides_context = []
    if body.agent_role == "content" and slides:
        for s in slides:
            slides_context.append({
                "page_num": s.page_num,
                "slide_id": s.id,
                "type": s.type or "content",
                "content_json": _compact_for_contract(s.content_json or {}),
            })

    # 构建素材摘要（视觉总监使用）
    assets_summary = ""
    if body.agent_role == "visual":
        parts: list[str] = []

        # 1. 当前已有的风格提案/已选风格（锚点）—— 用户调整时必须基于此修改
        selected_style_anchor = project.selected_style if isinstance(project.selected_style, dict) else None
        if selected_style_anchor:
            current = selected_style_anchor
            name = current.get("name", "")
            mood = current.get("mood", "")
            font = current.get("font", "")
            palette = current.get("palette", [])
            palette_str = ""
            if isinstance(palette, list):
                palette_items = []
                for c in palette[:4]:
                    if isinstance(c, dict):
                        label = " / ".join(
                            part for part in [
                                _short_text(c.get("name"), 12),
                                _short_text(c.get("role"), 24),
                            ] if part
                        )
                        if label:
                            palette_items.append(label)
                palette_str = "、".join(palette_items)
            parts.append("【当前已选择的风格】")
            parts.append(f"  - 风格名：{name}")
            if mood:
                parts.append(f"  - 调性：{_short_text(mood, 80)}")
            if font:
                parts.append(f"  - 字体：{_short_text(font, 60)}")
            if palette_str:
                parts.append(f"  - 配色：{palette_str}")
            desc = current.get("description", "")
            if desc:
                parts.append(f"  - 说明：{_short_text(desc, 120)}")
            parts.append("")

        if project.style_proposal and isinstance(project.style_proposal, dict):
            proposals = project.style_proposal.get("proposals", [])
            if proposals and isinstance(proposals, list):
                current = proposals[0]
                if isinstance(current, dict):
                    name = current.get("name", "")
                    mood = current.get("mood", "")
                    font = current.get("font", "")
                    palette = current.get("palette", [])
                    palette_str = ""
                    if isinstance(palette, list):
                        palette_items = []
                        for c in palette[:4]:
                            if isinstance(c, dict):
                                label = " / ".join(
                                    part for part in [
                                        _short_text(c.get("name"), 12),
                                        _short_text(c.get("role"), 24),
                                    ] if part
                                )
                                if label:
                                    palette_items.append(label)
                        palette_str = "、".join(palette_items)
                    parts.append("【当前风格提案】")
                    parts.append(f"  - 风格名：{name}")
                    if mood:
                        parts.append(f"  - 调性：{_short_text(mood, 80)}")
                    if font:
                        parts.append(f"  - 字体：{_short_text(font, 60)}")
                    if palette_str:
                        parts.append(f"  - 配色：{palette_str}")
                    desc = current.get("description", "")
                    if desc:
                        parts.append(f"  - 说明：{_short_text(desc, 120)}")
                    parts.append("")

        # 2. 已上传素材
        if project.reference_images:
            asset_counts: dict[str, int] = {}
            has_template = False
            visual_asset_details: list[str] = []
            max_visual_asset_details = 4
            for ref in project.reference_images:
                if ref.role == "template":
                    has_template = True
                else:
                    asset_counts[ref.role] = asset_counts.get(ref.role, 0) + 1
                # 收集 visual_asset 的分析结果（已持久化的部分）
                if (
                    ref.role == "visual_asset"
                    and len(visual_asset_details) < max_visual_asset_details
                    and ref.asset_analysis
                    and isinstance(ref.asset_analysis, dict)
                ):
                    a = ref.asset_analysis
                    name = ref.asset_name or a.get("subject", "可复用素材")
                    features = a.get("distinctive_features", "") or a.get("description", "")
                    source_page = a.get("pptx_source_page_num")
                    detail = f"  • {name}"
                    if source_page:
                        detail += f"（原PPT第{source_page}页）"
                    if features:
                        detail += f"：{_short_text(features, 50)}"
                    visual_asset_details.append(detail)

            asset_lines: list[str] = []
            if asset_counts.get("logo", 0):
                asset_lines.append(f"- 品牌 Logo：{asset_counts['logo']} 张（颜色/调性由后端提取，禁止臆测）")
            if asset_counts.get("visual_asset", 0):
                asset_lines.append(f"- 可复用素材：{asset_counts['visual_asset']} 个（后端按页面内容召回）")
                asset_lines.extend(visual_asset_details)
                hidden_count = asset_counts["visual_asset"] - len(visual_asset_details)
                if hidden_count > 0:
                    asset_lines.append(f"  • 另有 {hidden_count} 个未展开")
            if asset_counts.get("style_ref", 0):
                asset_lines.append(f"- 风格参考：{asset_counts['style_ref']} 张（色彩/构图/字体由后端提取，禁止臆测）")
            if has_template:
                asset_lines.append("- 版式模板：已上传（含封面/目录/内容/封底页）")
            if asset_counts.get("content_ref", 0):
                asset_lines.append(f"- 内容配图：{asset_counts['content_ref']} 张（页面级）")
            if asset_lines:
                if parts:
                    parts.append("【已上传素材】")
                parts.extend(asset_lines)

        if parts:
            assets_summary = "\n".join(parts)

    import logging as _logging
    _logger = _logging.getLogger(__name__)

    def event_stream():
        _logger.info(f"Chat API: starting stream for project={project_id}, role={body.agent_role}")
        result_data = None
        try:
            if chat_attachments:
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "type": "thinking",
                            "delta": f"已收到 {len(chat_attachments)} 张图片，正在读取图片内容并结合当前页面判断修改方式。\n",
                        },
                        ensure_ascii=False,
                    )
                    + "\n\n"
                )
            elif documents and documents.strip():
                yield (
                    "data: "
                    + json.dumps(
                        {"type": "thinking", "delta": "正在读取已上传文件内容并结合当前页面判断下一步。\n"},
                        ensure_ascii=False,
                    )
                    + "\n\n"
                )
            for event in _stream_intent(
                body.message, context, body.history, documents,
                body.page_context, body.agent_role, content_plan_summary, assets_summary, slides_context, chat_attachments
            ):
                if event.get("type") == "result":
                    event = {**event, "data": with_next_action(event.get("data"), context, body.agent_role)}
                    result_data = event.get("data")
                    _logger.info(f"Chat API: yielding result action={result_data.get('action') if isinstance(result_data, dict) else 'n/a'}")
                line = f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                yield line
        except Exception as e:
            _logger.error(f"Chat API: stream exception: {e}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

        # 流结束后：如果 Agent 返回了风格提案，保存到数据库确保前后端一致
        if result_data and isinstance(result_data, dict) and result_data.get("action") in ("propose_styles", "adjust_style"):
            style_proposal = result_data.get("style_proposal")
            if style_proposal and isinstance(style_proposal, dict):
                try:
                    from datetime import datetime, timezone
                    # 标准化 palette 格式
                    palette = style_proposal.get("palette", [])
                    if palette and isinstance(palette, list):
                        normalized_palette = []
                        for c in palette:
                            if isinstance(c, dict) and "hex" in c:
                                normalized_palette.append(c)
                            elif isinstance(c, str):
                                normalized_palette.append({"name": c, "hex": c, "role": ""})
                        style_proposal["palette"] = normalized_palette

                    project.style_proposal = {
                        "proposals": [style_proposal],
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "agent_based": True,
                        "asset_signature": style_asset_signature(project),
                        "content_signature": content_signature(slides),
                    }
                    db.commit()
                    _logger.info(f"[Chat] Saved agent style_proposal to project={project_id}, action={result_data.get('action')}")
                except Exception as e:
                    _logger.warning(f"[Chat] Failed to save agent style_proposal: {e}")

        _logger.info(f"Chat API: stream ended for project={project_id}")

    return StreamingResponse(event_stream(), media_type="text/event-stream")
