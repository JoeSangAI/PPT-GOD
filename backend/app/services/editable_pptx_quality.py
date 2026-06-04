from __future__ import annotations

from dataclasses import dataclass

from app.services.editable_pptx_diagnostics import EditablePptxDiagnostics


@dataclass(frozen=True)
class EditablePptxQualityDecision:
    status: str
    reason: str
    message: str


def evaluate_editable_pptx_quality(
    diagnostics: EditablePptxDiagnostics | None,
    *,
    min_text_boxes: int = 1,
    warning_page_ratio: float = 0.65,
) -> EditablePptxQualityDecision:
    if diagnostics is None or diagnostics.page_count <= 0:
        return EditablePptxQualityDecision("fail", "missing_diagnostics", "没有生成可编辑版诊断信息")
    if diagnostics.restored_text_count < min_text_boxes:
        return EditablePptxQualityDecision("fail", "no_editable_text", "没有解析出可编辑文字")
    warning_ratio = len(diagnostics.quality_warning_pages) / max(1, diagnostics.page_count)
    if warning_ratio >= warning_page_ratio:
        return EditablePptxQualityDecision("warn", "many_pages_need_review", "可编辑版已生成，但多数页面建议复核")
    if diagnostics.ocr_failed_pages:
        return EditablePptxQualityDecision("warn", "some_pages_ocr_failed", "可编辑版已生成，部分页面保留为图片")
    return EditablePptxQualityDecision("pass", "ok", "可编辑版已生成")
