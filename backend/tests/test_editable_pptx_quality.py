from app.services.editable_pptx_diagnostics import EditablePptxDiagnostics, EditablePptxPageDiagnostics
from app.services.editable_pptx_quality import evaluate_editable_pptx_quality


def test_quality_gate_fails_when_no_text_is_restored():
    diagnostics = EditablePptxDiagnostics(
        restore_mode="standard",
        pages=[EditablePptxPageDiagnostics(page_num=1, raw_region_count=0, restored_text_count=0, ocr_failed=True)],
    )

    decision = evaluate_editable_pptx_quality(diagnostics)

    assert decision.status == "fail"
    assert decision.reason == "no_editable_text"


def test_quality_gate_warns_when_most_pages_need_review():
    diagnostics = EditablePptxDiagnostics(
        restore_mode="standard",
        pages=[
            EditablePptxPageDiagnostics(page_num=1, raw_region_count=5, restored_text_count=4, quality_warning=True),
            EditablePptxPageDiagnostics(page_num=2, raw_region_count=5, restored_text_count=4, quality_warning=True),
            EditablePptxPageDiagnostics(page_num=3, raw_region_count=5, restored_text_count=4, quality_warning=False),
        ],
    )

    decision = evaluate_editable_pptx_quality(diagnostics, warning_page_ratio=0.5)

    assert decision.status == "warn"
    assert decision.reason == "many_pages_need_review"


def test_quality_gate_passes_clean_diagnostics():
    diagnostics = EditablePptxDiagnostics(
        restore_mode="standard",
        pages=[
            EditablePptxPageDiagnostics(page_num=1, raw_region_count=5, restored_text_count=4),
            EditablePptxPageDiagnostics(page_num=2, raw_region_count=5, restored_text_count=3),
        ],
    )

    decision = evaluate_editable_pptx_quality(diagnostics)

    assert decision.status == "pass"
    assert decision.reason == "ok"
