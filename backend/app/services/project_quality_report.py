from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Iterable, Mapping

from PIL import Image

from app.models.models import Project, Slide
from app.services.logo_policy import is_logo_confirmed, logo_policy_for_page


FINAL_PROJECT_STATUSES = {"completed", "prototype_ready"}
LOW_CONTRAST_LOGO_STATES = {"low_contrast_manual_review", "full_forced"}


def _page_nums(slides: Iterable[Slide]) -> list[int]:
    return sorted({int(s.page_num) for s in slides if getattr(s, "page_num", None) is not None})


def _format_pages(page_nums: Iterable[int], limit: int = 8) -> str:
    nums = sorted({int(p) for p in page_nums})
    if not nums:
        return ""
    if len(nums) <= limit:
        return "第 " + "、".join(str(p) for p in nums) + " 页"
    return "第 " + "、".join(str(p) for p in nums[:limit]) + f" 页等 {len(nums)} 页"


def _slide_visual(slide: Slide) -> Mapping[str, Any]:
    value = getattr(slide, "visual_json", None)
    return value if isinstance(value, Mapping) else {}


def _slide_content(slide: Slide) -> Mapping[str, Any]:
    value = getattr(slide, "content_json", None)
    return value if isinstance(value, Mapping) else {}


def _text_stats(value: Any) -> tuple[int, int]:
    chars = 0
    bullets = 0
    if isinstance(value, str):
        text = value.strip()
        return len(text), 1 if text else 0
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in {"speaker_notes", "notes", "source_refs", "references", "replicate_quality"}:
                continue
            child_chars, child_bullets = _text_stats(child)
            chars += child_chars
            if str(key).lower() in {"bullets", "points", "items", "list"}:
                bullets += child_bullets
            else:
                bullets += child_bullets
    elif isinstance(value, list):
        for child in value:
            child_chars, child_bullets = _text_stats(child)
            chars += child_chars
            bullets += max(1, child_bullets) if isinstance(child, str) and child.strip() else child_bullets
    return chars, bullets


def _inspect_image(path: str | None) -> dict[str, Any]:
    if not path:
        return {"ok": False, "reason": "missing_path"}
    if not os.path.exists(path):
        return {"ok": False, "reason": "missing_file"}
    try:
        with Image.open(path) as img:
            width, height = img.size
            ratio = width / max(height, 1)
            return {
                "ok": True,
                "width": width,
                "height": height,
                "ratio": ratio,
                "ratio_ok": 1.70 <= ratio <= 1.86,
            }
    except Exception:
        return {"ok": False, "reason": "unreadable_file"}


def _confirmed_logo_count(project: Project) -> int:
    refs = getattr(project, "reference_images", None) or []
    count = 0
    for ref in refs:
        if getattr(ref, "role", None) != "logo":
            continue
        if not is_logo_confirmed(ref):
            continue
        path = getattr(ref, "file_path", None)
        if path and os.path.exists(path):
            count += 1
    return count


def _issue(kind: str, severity: str, title: str, pages: Iterable[int] | None = None, recommendation: str | None = None) -> dict:
    page_list = sorted({int(p) for p in pages or []})
    payload = {
        "kind": kind,
        "severity": severity,
        "title": title,
        "pages": page_list,
    }
    if recommendation:
        payload["recommendation"] = recommendation
    return payload


def _signature_payload(project: Project, slides: list[Slide], has_pptx: bool, issues: list[dict]) -> dict:
    return {
        "project_id": project.id,
        "project_status": project.status,
        "has_pptx": has_pptx,
        "slides": [
            {
                "page_num": slide.page_num,
                "status": slide.status,
                "image_path": slide.image_path,
                "logo_policy": _slide_visual(slide).get("logo_policy"),
            }
            for slide in slides
        ],
        "issues": issues,
    }


def _build_signature(project: Project, slides: list[Slide], has_pptx: bool, issues: list[dict]) -> str:
    raw = json.dumps(_signature_payload(project, slides, has_pptx, issues), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _build_message(
    *,
    project: Project,
    total_slides: int,
    completed_count: int,
    has_pptx: bool,
    issues: list[dict],
    logo_count: int,
) -> str:
    lines = [
        "**交付前检查**",
        "",
        f"- 页面生成：{completed_count} / {total_slides} 页",
        f"- PPTX：{'可导出' if has_pptx else '暂未确认可导出'}",
    ]

    blocking = [i for i in issues if i.get("severity") == "error"]
    warnings = [i for i in issues if i.get("severity") == "warning"]
    infos = [i for i in issues if i.get("severity") == "info"]

    if not blocking and not warnings:
        lines.extend(["", "**结果**", "", "未发现缺页、文件损坏或必选 Logo 缺失。"])
    else:
        if blocking:
            lines.extend(["", "**需要处理**", ""])
            lines.extend(f"{idx}. {_format_issue_message(item)}" for idx, item in enumerate(blocking, start=1))
        if warnings:
            lines.extend(["", "**建议复核**", ""])
            lines.extend(f"{idx}. {_format_issue_message(item)}" for idx, item in enumerate(warnings, start=1))

    if infos:
        lines.extend(["", "**已按规则处理**", ""])
        for item in infos[:2]:
            lines.append(f"- {_format_issue_message(item)}")

    lines.extend(["", "**Logo 说明**", ""])
    if logo_count > 0:
        lines.append("- 章节页和金句页允许不放；内容页会保留品牌 Logo。")
        lines.append("- 若个别页面对比度仍不理想，建议在导出的 PPT 里手动微调。")
    else:
        lines.append("- 本项目没有可用品牌 Logo。")
        lines.append("- 需要品牌露出时，可在导出的 PPT 里手动添加，或下次生成前上传 Logo。")

    if project.status == "prototype_ready":
        lines.extend(["", "**打样说明**", "", "- 当前是打样文件，确认样张后再生成全部页面。"])
    return "\n".join(lines)


def _format_issue_message(item: Mapping[str, Any]) -> str:
    page_text = _format_pages(item.get("pages") or [])
    recommendation = str(item.get("recommendation") or "").strip()
    text = f"**{item['title']}**"
    if page_text:
        text += f"：{page_text}"
    if recommendation:
        text += f"。{recommendation}" if page_text else f"：{recommendation}"
    elif page_text:
        text += "。"
    return text


def build_project_quality_report(
    project: Project,
    slides: list[Slide],
    *,
    has_pptx: bool = False,
    pptx_path: str | None = None,
) -> dict | None:
    if project.status not in FINAL_PROJECT_STATUSES:
        return None

    ordered_slides = sorted(slides, key=lambda s: int(s.page_num or 0))
    total_slides = len(ordered_slides)
    completed_slides = [s for s in ordered_slides if s.status == "completed"]
    logo_count = _confirmed_logo_count(project)
    issues: list[dict] = []

    if not has_pptx:
        issues.append(_issue(
            "pptx_missing",
            "error",
            "未确认最终 PPTX 文件",
            recommendation="先刷新状态；如果仍不可导出，请重新生成或重试失败页。",
        ))
    elif pptx_path and not os.path.exists(pptx_path):
        issues.append(_issue(
            "pptx_file_missing",
            "error",
            "PPTX 文件路径不可用",
            recommendation="先刷新状态；如果仍不可导出，请重新生成或重试失败页。",
        ))

    incomplete_pages = [s.page_num for s in ordered_slides if s.status != "completed"]
    if incomplete_pages:
        issues.append(_issue(
            "incomplete_pages",
            "error",
            "存在未完成页面",
            incomplete_pages,
            "请先补齐这些页面，再导出最终稿。",
        ))

    missing_image_pages: list[int] = []
    unreadable_pages: list[int] = []
    ratio_pages: list[int] = []
    for slide in completed_slides:
        image_status = _inspect_image(slide.image_path)
        if image_status.get("ok") is not True:
            reason = image_status.get("reason")
            if reason in {"missing_path", "missing_file"}:
                missing_image_pages.append(slide.page_num)
            else:
                unreadable_pages.append(slide.page_num)
            continue
        if image_status.get("ratio_ok") is False:
            ratio_pages.append(slide.page_num)

    if missing_image_pages:
        issues.append(_issue(
            "missing_images",
            "error",
            "页面图片文件缺失",
            missing_image_pages,
            "请重新生成这些页面。",
        ))
    if unreadable_pages:
        issues.append(_issue(
            "unreadable_images",
            "error",
            "页面图片文件无法读取",
            unreadable_pages,
            "请重新生成这些页面。",
        ))
    if ratio_pages:
        issues.append(_issue(
            "image_ratio",
            "warning",
            "页面比例可能异常",
            ratio_pages,
            "建议导出后快速检查是否有拉伸或裁切。",
        ))

    if logo_count > 0:
        required_logo_pages: list[int] = []
        low_contrast_logo_pages: list[int] = []
        stale_omit_pages: list[int] = []
        for slide in completed_slides:
            policy = logo_policy_for_page(slide)
            if not policy.get("show_logo"):
                continue
            required_logo_pages.append(slide.page_num)
            visual_policy = _slide_visual(slide).get("logo_policy")
            raw_policy = visual_policy if isinstance(visual_policy, Mapping) else {}
            raw_variant = str(raw_policy.get("render_variant") or "").strip().lower()
            raw_show = raw_policy.get("show_logo")
            if raw_variant == "omit" or raw_show is False:
                stale_omit_pages.append(slide.page_num)
            if str(raw_policy.get("logo_contrast") or "").strip().lower() in LOW_CONTRAST_LOGO_STATES:
                low_contrast_logo_pages.append(slide.page_num)

        if required_logo_pages and len(set(required_logo_pages) - set(stale_omit_pages)) == 0:
            issues.append(_issue(
                "required_logo_policy_missing",
                "error",
                "内容页 Logo 规则没有落到页面上",
                required_logo_pages,
                "请导出后手动添加 Logo，或重新生成这些页面。",
            ))
        elif stale_omit_pages:
            issues.append(_issue(
                "required_logo_policy_corrected",
                "info",
                "检测到部分内容页原本请求省略 Logo，已按当前规则保留",
                stale_omit_pages,
            ))

        if low_contrast_logo_pages:
            issues.append(_issue(
                "logo_low_contrast",
                "warning",
                "Logo 对比度可能偏弱",
                low_contrast_logo_pages,
                "建议在导出的 PPT 里手动调整位置或替换为更适合当前底色的版本。",
            ))

    dense_text_pages: list[int] = []
    for slide in completed_slides:
        chars, bullets = _text_stats(_slide_content(slide))
        if chars >= 520 or bullets >= 12:
            dense_text_pages.append(slide.page_num)
    if dense_text_pages:
        issues.append(_issue(
            "dense_text",
            "warning",
            "文字密度偏高",
            dense_text_pages,
            "建议导出后检查可读性，必要时拆页或精简。",
        ))

    severity_rank = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda item: (severity_rank.get(item.get("severity"), 9), item.get("kind", "")))
    signature = _build_signature(project, ordered_slides, has_pptx, issues)
    message = _build_message(
        project=project,
        total_slides=total_slides,
        completed_count=len(completed_slides),
        has_pptx=has_pptx,
        issues=issues,
        logo_count=logo_count,
    )
    return {
        "status": "completed",
        "signature": signature,
        "summary": message.split("\n", 1)[0],
        "issues": issues,
        "message": message,
        "agent_role": "visual",
    }
