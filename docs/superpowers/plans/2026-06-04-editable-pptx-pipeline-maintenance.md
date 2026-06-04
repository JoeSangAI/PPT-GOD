# Editable PPTX Pipeline Maintenance Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade editable PPTX export from a best-effort OCR overlay into a diagnosable, regression-tested, quality-gated pipeline that produces visibly stable editable decks.

**Architecture:** Keep the existing image-first export contract: finished slide renders remain the visual source of truth, and editable PPTX is a separate derived artifact. Add a benchmark harness, per-page diagnostics, explicit restore-mode policy, improved text fitting/cleanup, and quality gates before a run can be marked successful. The production path should fail loudly for unusable outputs and return reviewable diagnostics for partial-quality outputs.

**Tech Stack:** FastAPI, Celery, python-pptx, Pillow, NumPy, LibreOffice/PyMuPDF rendering helpers, existing MiniMax/OpenAI-compatible VLM client, pytest.

---

## Current Failure Pattern

The June 4 vivo standard-mode reproduction produced a real editable PPTX, not a pure-image file:

- Source file: `/Users/Joe_1/Desktop/vivo 校园学生市场 6-12 月整合策略案 20260603.pptx`
- Pipeline output: `.pptgod-data/outputs/manual-vivo-standard-pipeline/vivo_standard_editable_pipeline.pptx`
- Slides: 18
- Restored text boxes: 304
- Visual assets: 19
- OCR failed pages: none
- PPTX XML: 330 `<p:txBody>` text shapes, 333 text runs

The remaining quality problem is pipeline-level:

- Text is often restored but visually too large.
- Dense pages reflow and collide with existing layout.
- Cleanup patches leave residuals or visually heavy blocks.
- Almost every page hit QA retry/warning, which means the pipeline knows it is struggling but does not yet make that signal actionable enough.
- Standard/enhanced/aggressive are policy variants of one exporter, but we do not yet have a mode-difference regression gate.

## Design Philosophy Audit

This plan is only acceptable if executed as a case-driven robustness loop, not as a broad rewrite.

Audit result: **partially compliant, requires scope discipline.**

Compliant parts:

- It starts from a real artifact and a reproduced failure pattern, not an abstract refactor.
- It avoids making prompt expansion the first move.
- It keeps the existing image-first editable export contract instead of replacing the whole generation architecture.
- It adds deterministic observability so future cases can strengthen the system instead of becoming anecdotes.

Risks:

- The first draft is large enough to drift into over-engineering if implemented all at once.
- Adding new diagnostic and quality modules is justified only if they stay thin and remove complexity from `editable_pptx_export.py`.
- Mode-difference comparison is useful, but it should not become a product-facing taxonomy expansion.
- Quality gates must not become downstream cleanup that hides source failures; every warning should point back to OCR, text fitting, cleanup, or mode policy.

Execution guardrails:

- Phase 1 must be the smallest useful loop: benchmark harness, PPTX editability inspection, per-page diagnostics, and the text-fitting fix for the vivo failure class.
- Phase 2 may add cleanup patch consolidation and quality gate persistence only after Phase 1 produces a before/after benchmark.
- Phase 3 may compare standard/enhanced/aggressive only after the exporter has reliable diagnostics.
- Do not expand OCR prompts unless diagnostics show that the model failed to identify text that the policy should restore.
- Every change must produce at least one durable asset: a benchmark output, a regression test, or a reusable design rule.

## File Structure

- Create: `backend/app/services/editable_pptx_diagnostics.py`
  - Owns structured per-page diagnostics, PPTX XML inspection, summary metrics, and JSON serialization.
- Create: `backend/app/services/editable_pptx_quality.py`
  - Owns quality gates that decide `pass`, `warn`, or `fail` from diagnostics.
- Create: `backend/scripts/run_editable_pptx_pipeline.py`
  - Runs local/source PPTX benchmark: render source, run selected modes, render output, write contact sheet and diagnostics JSON.
- Modify: `backend/app/services/editable_pptx_export.py`
  - Emits diagnostics while preserving current output contract.
  - Adds explicit rejection reasons and improved text fitting/cleanup behavior.
- Modify: `backend/app/tasks.py`
  - Applies production quality gates and persists meaningful run messages.
- Modify: `backend/app/api/slides.py`
  - Uses diagnostics-aware readiness checks for downloadable editable outputs.
- Modify: `backend/app/core/config.py`
  - Adds gate thresholds and benchmark toggles.
- Test: `backend/tests/test_editable_pptx_diagnostics.py`
- Test: `backend/tests/test_editable_pptx_quality.py`
- Test: `backend/tests/test_editable_pptx_export.py`
- Test: `backend/tests/test_celery_task_health.py`

---

### Task 1: Add A Reproducible Benchmark Harness

**Files:**
- Create: `backend/scripts/run_editable_pptx_pipeline.py`
- Test: `backend/tests/test_editable_pptx_diagnostics.py`

- [ ] **Step 1: Write a failing test for PPTX XML inspection**

Add this test to `backend/tests/test_editable_pptx_diagnostics.py`:

```python
from pathlib import Path

from pptx import Presentation

from app.services.editable_pptx_diagnostics import inspect_pptx_editability


def test_inspect_pptx_editability_counts_text_shapes(tmp_path: Path):
    path = tmp_path / "editable.pptx"
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(0, 0, 1000000, 500000)
    box.text_frame.text = "可编辑标题"
    prs.save(path)

    result = inspect_pptx_editability(path)

    assert result.slide_count == 1
    assert result.text_shape_count == 1
    assert result.text_run_count == 1
    assert result.picture_shape_count == 0
    assert result.has_editable_text is True
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
PYTHONPATH=. venv/bin/pytest -q tests/test_editable_pptx_diagnostics.py::test_inspect_pptx_editability_counts_text_shapes
```

Expected: fail with `ModuleNotFoundError: No module named 'app.services.editable_pptx_diagnostics'`.

- [ ] **Step 3: Implement the XML inspector**

Create `backend/app/services/editable_pptx_diagnostics.py`:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
import zipfile


@dataclass(frozen=True)
class PptxEditabilityInspection:
    slide_count: int
    text_shape_count: int
    text_run_count: int
    picture_shape_count: int

    @property
    def has_editable_text(self) -> bool:
        return self.text_shape_count > 0 and self.text_run_count > 0

    def to_dict(self) -> dict:
        return {**asdict(self), "has_editable_text": self.has_editable_text}


def inspect_pptx_editability(path: str | Path) -> PptxEditabilityInspection:
    slide_count = 0
    text_shape_count = 0
    text_run_count = 0
    picture_shape_count = 0
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not re.fullmatch(r"ppt/slides/slide\d+\.xml", name):
                continue
            slide_count += 1
            xml = archive.read(name).decode("utf-8", errors="ignore")
            text_shape_count += xml.count("<p:txBody>")
            text_run_count += xml.count("<a:t>")
            picture_shape_count += xml.count("<p:pic>")
    return PptxEditabilityInspection(
        slide_count=slide_count,
        text_shape_count=text_shape_count,
        text_run_count=text_run_count,
        picture_shape_count=picture_shape_count,
    )
```

- [ ] **Step 4: Run the test and verify it passes**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
PYTHONPATH=. venv/bin/pytest -q tests/test_editable_pptx_diagnostics.py::test_inspect_pptx_editability_counts_text_shapes
```

Expected: `1 passed`.

- [ ] **Step 5: Add the benchmark script skeleton**

Create `backend/scripts/run_editable_pptx_pipeline.py`:

```python
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.editable_pptx_diagnostics import inspect_pptx_editability
from app.services.editable_pptx_export import build_editable_pptx
from app.services.template_extractor import convert_ppt_to_pdf, extract_pdf_thumbnails


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run editable PPTX pipeline against a source deck.")
    parser.add_argument("source_pptx")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--mode", action="append", choices=["standard", "enhanced", "aggressive"], default=None)
    parser.add_argument("--dpi", type=int, default=150)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    modes = args.mode or ["standard"]
    source = Path(args.source_pptx)
    root = Path(args.output_dir)
    source_render_dir = root / "source_render"
    pdf_dir = root / "pdf"
    source_render_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir.mkdir(parents=True, exist_ok=True)

    source_pdf = Path(convert_ppt_to_pdf(str(source), str(pdf_dir)))
    source_pngs = [Path(p) for p in extract_pdf_thumbnails(str(source_pdf), str(source_render_dir), dpi=args.dpi)]
    slide_images = [
        {"page_num": index, "image_path": str(path), "speaker_notes": ""}
        for index, path in enumerate(source_pngs, start=1)
    ]

    summary = {"source": str(source), "source_pages": len(source_pngs), "modes": {}}
    for mode in modes:
        mode_dir = root / mode
        work_dir = mode_dir / "assets"
        output_path = mode_dir / f"editable_{mode}.pptx"
        mode_dir.mkdir(parents=True, exist_ok=True)
        result = build_editable_pptx(
            slide_images=slide_images,
            output_path=str(output_path),
            work_dir=str(work_dir),
            restore_mode=mode,
            reuse_ocr_cache=False,
        )
        inspection = inspect_pptx_editability(output_path)
        summary["modes"][mode] = {
            "output_path": str(output_path),
            "slide_count": result.slide_count,
            "text_box_count": result.text_box_count,
            "visual_asset_count": result.visual_asset_count,
            "ocr_failed_pages": result.ocr_failed_pages,
            "qa_retry_pages": result.qa_retry_pages,
            "quality_warning_pages": result.quality_warning_pages,
            "pptx": inspection.to_dict(),
        }

    summary_path = root / "editable_pipeline_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(summary_path)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run the vivo standard benchmark**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
PYTHONPATH=. venv/bin/python scripts/run_editable_pptx_pipeline.py \
  "/Users/Joe_1/Desktop/vivo 校园学生市场 6-12 月整合策略案 20260603.pptx" \
  --output-dir "/Users/Joe_1/Desktop/Development/ppt-god/outputs/editable-benchmarks/vivo-20260603" \
  --mode standard
```

Expected: `editable_pipeline_summary.json` exists and reports `text_box_count > 0`.

---

### Task 2: Emit Per-Page Diagnostics From The Exporter

**Files:**
- Modify: `backend/app/services/editable_pptx_diagnostics.py`
- Modify: `backend/app/services/editable_pptx_export.py`
- Test: `backend/tests/test_editable_pptx_export.py`

- [ ] **Step 1: Add diagnostics dataclasses**

Append to `backend/app/services/editable_pptx_diagnostics.py`:

```python
from dataclasses import field


@dataclass
class EditablePptxPageDiagnostics:
    page_num: int
    raw_region_count: int = 0
    normalized_region_count: int = 0
    restored_text_count: int = 0
    visual_asset_count: int = 0
    cleanup_patch_count: int = 0
    qa_retry_count: int = 0
    quality_warning: bool = False
    ocr_failed: bool = False
    rejection_reasons: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EditablePptxDiagnostics:
    restore_mode: str
    pages: list[EditablePptxPageDiagnostics] = field(default_factory=list)

    @property
    def restored_text_count(self) -> int:
        return sum(page.restored_text_count for page in self.pages)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def ocr_failed_pages(self) -> list[int]:
        return [page.page_num for page in self.pages if page.ocr_failed]

    @property
    def quality_warning_pages(self) -> list[int]:
        return [page.page_num for page in self.pages if page.quality_warning]

    def to_dict(self) -> dict:
        return {
            "restore_mode": self.restore_mode,
            "page_count": self.page_count,
            "restored_text_count": self.restored_text_count,
            "ocr_failed_pages": self.ocr_failed_pages,
            "quality_warning_pages": self.quality_warning_pages,
            "pages": [page.to_dict() for page in self.pages],
        }
```

- [ ] **Step 2: Write a failing diagnostics test**

Add to `backend/tests/test_editable_pptx_export.py`:

```python
def test_build_editable_pptx_returns_page_diagnostics(tmp_path):
    slide_path = tmp_path / "slide.png"
    make_test_slide(slide_path, "主标题", "正文内容")
    output_path = tmp_path / "editable.pptx"

    def ocr_provider(_image_path, _page_num):
        return [
            {
                "text": "主标题",
                "x": 0.1,
                "y": 0.1,
                "width": 0.3,
                "height": 0.08,
                "role": "title",
                "editable": True,
                "confidence": 0.95,
            }
        ]

    result = build_editable_pptx(
        slide_images=[{"page_num": 1, "image_path": str(slide_path)}],
        output_path=str(output_path),
        ocr_provider=ocr_provider,
        restore_mode="standard",
        reuse_ocr_cache=False,
    )

    assert result.diagnostics is not None
    assert result.diagnostics.page_count == 1
    assert result.diagnostics.pages[0].raw_region_count == 1
    assert result.diagnostics.pages[0].restored_text_count == 1
```

- [ ] **Step 3: Run the test and verify it fails**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
PYTHONPATH=. venv/bin/pytest -q tests/test_editable_pptx_export.py::test_build_editable_pptx_returns_page_diagnostics
```

Expected: fail because `EditablePptxResult` has no `diagnostics`.

- [ ] **Step 4: Add diagnostics to the result object**

Modify `EditablePptxResult` in `backend/app/services/editable_pptx_export.py`:

```python
from app.services.editable_pptx_diagnostics import EditablePptxDiagnostics, EditablePptxPageDiagnostics


@dataclass(frozen=True)
class EditablePptxResult:
    output_path: str
    slide_count: int
    text_box_count: int
    visual_asset_count: int
    ocr_failed_pages: list[int]
    qa_retry_pages: list[int] = field(default_factory=list)
    quality_fallback_pages: list[int] = field(default_factory=list)
    quality_warning_pages: list[int] = field(default_factory=list)
    diagnostics: EditablePptxDiagnostics | None = None
```

In `build_editable_pptx`, initialize:

```python
diagnostics = EditablePptxDiagnostics(restore_mode=mode)
```

Inside the page loop, create and update:

```python
page_diag = EditablePptxPageDiagnostics(page_num=page_num)
page_diag.raw_region_count = len(raw_regions)
page_diag.normalized_region_count = len(normalized_regions)
page_diag.restored_text_count = len(groups)
page_diag.visual_asset_count = len(image_blocks)
page_diag.cleanup_patch_count = len(cleanup_boxes) if groups or image_blocks else 0
page_diag.ocr_failed = not bool(raw_regions)
page_diag.qa_retry_count = len(residuals)
page_diag.quality_warning = page_num in quality_warning_pages
diagnostics.pages.append(page_diag)
```

Return:

```python
return EditablePptxResult(
    output_path=output_path,
    slide_count=len(sorted_slides),
    text_box_count=text_box_count,
    visual_asset_count=visual_asset_count,
    ocr_failed_pages=sorted({int(page) for page in ocr_failed_pages}),
    qa_retry_pages=sorted({int(page) for page in qa_retry_pages}),
    quality_fallback_pages=sorted({int(page) for page in quality_fallback_pages}),
    quality_warning_pages=sorted({int(page) for page in quality_warning_pages}),
    diagnostics=diagnostics,
)
```

- [ ] **Step 5: Run the diagnostics test**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
PYTHONPATH=. venv/bin/pytest -q tests/test_editable_pptx_export.py::test_build_editable_pptx_returns_page_diagnostics
```

Expected: `1 passed`.

---

### Task 3: Make Text Rejection Reasons Explicit

**Files:**
- Modify: `backend/app/services/editable_pptx_export.py`
- Test: `backend/tests/test_editable_pptx_export.py`

- [ ] **Step 1: Write a failing test for standard-mode small label rejection**

Add to `backend/tests/test_editable_pptx_export.py`:

```python
def test_should_restore_text_reason_rejects_small_standard_label(tmp_path):
    from app.services.editable_pptx_export import should_restore_text_with_reason

    image = np.zeros((720, 1280, 3), dtype=np.uint8) + 255
    region = {
        "text": "小标签",
        "x": 0.2,
        "y": 0.2,
        "width": 0.04,
        "height": 0.018,
        "role": "label",
        "editable": True,
        "confidence": 0.9,
    }

    keep, reason = should_restore_text_with_reason(region, [], image, "standard", visual_complexity=None)

    assert keep is False
    assert reason == "standard_small_auxiliary_text"
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
PYTHONPATH=. venv/bin/pytest -q tests/test_editable_pptx_export.py::test_should_restore_text_reason_rejects_small_standard_label
```

Expected: fail because `should_restore_text_with_reason` is missing.

- [ ] **Step 3: Implement reasoned filtering**

In `backend/app/services/editable_pptx_export.py`, add:

```python
def should_restore_text_with_reason(
    region: dict[str, Any],
    image_blocks: list[dict[str, float]],
    rgb: np.ndarray,
    restore_mode: str | None = None,
    visual_complexity: dict[str, float] | None = None,
) -> tuple[bool, str]:
    mode = normalize_editable_pptx_restore_mode(restore_mode)
    text = str(region.get("text") or "").strip()
    box = clamp_box(region)
    role = str(region.get("role") or "").lower()
    if not text:
        return False, "empty_text"
    if region.get("editable") is False:
        return False, "ocr_marked_non_editable"
    if not role_allowed_for_restore_mode(role, text, box, mode):
        return False, "role_not_allowed_for_mode"
    if float(region.get("confidence", 1.0) or 1.0) < 0.22:
        return False, "low_confidence"
    if box["width"] < 0.012 or box["height"] < 0.012:
        return False, "box_too_small"
    if mode == "standard" and is_auxiliary_text_on_complex_slide(text, box, role, visual_complexity):
        return False, "standard_complex_auxiliary_text"
    if mode == "standard" and role in {"body", "caption", "label"} and float(box["height"]) < 0.022:
        return False, "standard_small_auxiliary_text"
    if is_timeline_marker_text(text, box):
        return False, "timeline_marker"
    if is_low_contrast_decorative_text(box, rgb):
        return False, "low_contrast_decorative"
    inside_visual_asset = any(center_inside(box_center(box), block) for block in image_blocks)
    if inside_visual_asset and not is_primary_editable_text(text, box, role) and mode == "standard":
        return False, "standard_inside_visual_asset"
    if len(text) <= 1 and box["height"] < 0.06:
        return False, "single_character_noise"
    bg = sample_background(rgb, box)
    color = sample_text_color(rgb, box, bg)
    contrast = math.sqrt(sum((float(color[i]) - float(bg[i])) ** 2 for i in range(3)))
    if contrast < 30 and (len(text) <= 3 or box["height"] > 0.08):
        return False, "low_contrast"
    return True, "restored"
```

Change existing `should_restore_text` to:

```python
def should_restore_text(...same signature...) -> bool:
    keep, _reason = should_restore_text_with_reason(
        region,
        image_blocks,
        rgb,
        restore_mode,
        visual_complexity=visual_complexity,
    )
    return keep
```

- [ ] **Step 4: Use reasons in diagnostics**

In the page loop, replace the list comprehension for `text_regions` with:

```python
text_regions = []
for region in normalized_regions:
    keep, reason = should_restore_text_with_reason(
        region,
        image_blocks,
        rendered_rgb,
        mode,
        visual_complexity=visual_complexity,
    )
    if keep:
        text_regions.append(region)
    else:
        page_diag.rejection_reasons[reason] = page_diag.rejection_reasons.get(reason, 0) + 1
```

- [ ] **Step 5: Run targeted tests**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
PYTHONPATH=. venv/bin/pytest -q \
  tests/test_editable_pptx_export.py::test_should_restore_text_reason_rejects_small_standard_label \
  tests/test_editable_pptx_export.py::test_build_editable_pptx_returns_page_diagnostics
```

Expected: `2 passed`.

---

### Task 4: Fix Text Fitting Before More OCR Tuning

**Files:**
- Modify: `backend/app/services/editable_pptx_export.py`
- Test: `backend/tests/test_editable_pptx_export.py`

- [ ] **Step 1: Write a failing test for dense CJK title fitting**

Add to `backend/tests/test_editable_pptx_export.py`:

```python
def test_fitted_size_for_group_reduces_long_cjk_text_to_box():
    from app.services.editable_pptx_export import _fitted_size_for_group

    group = {
        "text": "一条主线：围绕大学生手机消费场景，建立 6-12 月学生市场长周期品牌心智",
        "role": "title",
        "bbox": {"x": 0.08, "y": 0.08, "width": 0.58, "height": 0.10},
        "weight_hint": "bold",
    }

    size = _fitted_size_for_group(group, group["bbox"])

    assert 13 <= size <= 22
```

- [ ] **Step 2: Run the test and record the current value**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
PYTHONPATH=. venv/bin/pytest -q tests/test_editable_pptx_export.py::test_fitted_size_for_group_reduces_long_cjk_text_to_box -vv
```

Expected: fail if the current fitted size is too large for dense vivo-style headings.

- [ ] **Step 3: Add a box-cap helper**

In `backend/app/services/editable_pptx_export.py`, add:

```python
def _max_font_size_by_box_height(box: dict[str, Any], role: str) -> float:
    height = float(box.get("height") or 0.0)
    if role == "title":
        return min(34.0, max(13.0, height * 190.0))
    if role in {"subtitle", "label"}:
        return min(22.0, max(8.0, height * 150.0))
    return min(18.0, max(7.0, height * 135.0))
```

Update `_fitted_size_for_group` to cap the computed size:

```python
role = str(group.get("role") or "body")
computed = ...  # existing computed value
return min(computed, _max_font_size_by_box_height(box, role))
```

- [ ] **Step 4: Add conservative wrap for long headings**

Update `wrap_text_for_box` so title text only wraps when the estimated line count fits the box:

```python
if role == "title" and estimate_text_units(text) > max(8.0, float(box["width"]) * 42.0):
    return "\n".join(balance_text_lines(text, max_lines=2))
```

If `balance_text_lines` does not exist, add:

```python
def balance_text_lines(text: str, max_lines: int = 2) -> list[str]:
    clean = str(text or "").strip()
    if max_lines <= 1 or len(clean) <= 1:
        return [clean]
    split_at = len(clean) // max_lines
    candidates = ["，", "；", "：", "、", " "]
    best = None
    for mark in candidates:
        left = clean.rfind(mark, 0, split_at + 1)
        right = clean.find(mark, split_at)
        for pos in [left, right]:
            if pos > 0:
                best = pos + (0 if mark == " " else 1)
                break
        if best:
            break
    if not best:
        best = split_at
    return [clean[:best].strip(), clean[best:].strip()]
```

- [ ] **Step 5: Run text-fitting tests**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
PYTHONPATH=. venv/bin/pytest -q tests/test_editable_pptx_export.py -k "fitted_size or wrap_text or dense_cjk"
```

Expected: all selected tests pass.

---

### Task 5: Consolidate Cleanup Patches And Reduce Visual Damage

**Files:**
- Modify: `backend/app/services/editable_pptx_export.py`
- Test: `backend/tests/test_editable_pptx_export.py`

- [ ] **Step 1: Write a failing cleanup merge test**

Add:

```python
def test_merge_cleanup_boxes_combines_nearby_text_boxes():
    from app.services.editable_pptx_export import merge_cleanup_boxes

    boxes = [
        {"x": 0.10, "y": 0.10, "width": 0.20, "height": 0.04},
        {"x": 0.10, "y": 0.145, "width": 0.21, "height": 0.04},
        {"x": 0.70, "y": 0.70, "width": 0.10, "height": 0.03},
    ]

    merged = merge_cleanup_boxes(boxes, gap=0.015)

    assert len(merged) == 2
    assert merged[0]["height"] > 0.08
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
PYTHONPATH=. venv/bin/pytest -q tests/test_editable_pptx_export.py::test_merge_cleanup_boxes_combines_nearby_text_boxes
```

Expected: fail because `merge_cleanup_boxes` is missing.

- [ ] **Step 3: Implement cleanup box merging**

Add to `backend/app/services/editable_pptx_export.py`:

```python
def _boxes_near(a: dict[str, float], b: dict[str, float], gap: float) -> bool:
    ax2 = float(a["x"]) + float(a["width"])
    ay2 = float(a["y"]) + float(a["height"])
    bx2 = float(b["x"]) + float(b["width"])
    by2 = float(b["y"]) + float(b["height"])
    separated = ax2 + gap < float(b["x"]) or bx2 + gap < float(a["x"]) or ay2 + gap < float(b["y"]) or by2 + gap < float(a["y"])
    return not separated


def _union_box(a: dict[str, float], b: dict[str, float]) -> dict[str, float]:
    x1 = min(float(a["x"]), float(b["x"]))
    y1 = min(float(a["y"]), float(b["y"]))
    x2 = max(float(a["x"]) + float(a["width"]), float(b["x"]) + float(b["width"]))
    y2 = max(float(a["y"]) + float(a["height"]), float(b["y"]) + float(b["height"]))
    return clamp_box({"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1})


def merge_cleanup_boxes(boxes: list[dict[str, float]], gap: float = 0.01) -> list[dict[str, float]]:
    merged: list[dict[str, float]] = []
    for box in sorted((clamp_box(b) for b in boxes), key=lambda b: (b["y"], b["x"])):
        for index, existing in enumerate(merged):
            if _boxes_near(existing, box, gap):
                merged[index] = _union_box(existing, box)
                break
        else:
            merged.append(box)
    return merged
```

- [ ] **Step 4: Apply merge only to compatible text cleanup boxes**

In `build_editable_pptx`, split cleanup boxes:

```python
visual_cleanup_boxes = []
for block in image_blocks:
    cleanup_box = clamp_box(block)
    cleanup_box["full_fill"] = True
    visual_cleanup_boxes.append(cleanup_box)

text_cleanup_boxes = [cleanup_box_for_group(group, rendered_rgb, visual_complexity) for group in groups]
simple_text_cleanup_boxes = [box for box in text_cleanup_boxes if not box.get("full_fill") and not box.get("solid_fill")]
special_text_cleanup_boxes = [box for box in text_cleanup_boxes if box.get("full_fill") or box.get("solid_fill")]
cleanup_boxes = visual_cleanup_boxes + special_text_cleanup_boxes + merge_cleanup_boxes(simple_text_cleanup_boxes)
```

- [ ] **Step 5: Run cleanup tests**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
PYTHONPATH=. venv/bin/pytest -q tests/test_editable_pptx_export.py -k "cleanup"
```

Expected: selected tests pass.

---

### Task 6: Add Production Quality Gates

**Files:**
- Create: `backend/app/services/editable_pptx_quality.py`
- Modify: `backend/app/tasks.py`
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/test_editable_pptx_quality.py`
- Test: `backend/tests/test_celery_task_health.py`

- [ ] **Step 1: Write quality gate tests**

Create `backend/tests/test_editable_pptx_quality.py`:

```python
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
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
PYTHONPATH=. venv/bin/pytest -q tests/test_editable_pptx_quality.py
```

Expected: fail because `editable_pptx_quality` is missing.

- [ ] **Step 3: Implement the quality gate**

Create `backend/app/services/editable_pptx_quality.py`:

```python
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
```

- [ ] **Step 4: Add config thresholds**

In `backend/app/core/config.py`, add settings:

```python
EDITABLE_PPTX_MIN_TEXT_BOXES: int = 1
EDITABLE_PPTX_WARNING_PAGE_RATIO: float = 0.65
EDITABLE_PPTX_FAIL_ON_WARNING: bool = False
```

- [ ] **Step 5: Use the gate in Celery task**

In `backend/app/tasks.py`, after `build_editable_pptx`, evaluate:

```python
from app.services.editable_pptx_quality import evaluate_editable_pptx_quality


decision = evaluate_editable_pptx_quality(
    result.diagnostics,
    min_text_boxes=int(settings.EDITABLE_PPTX_MIN_TEXT_BOXES or 1),
    warning_page_ratio=float(settings.EDITABLE_PPTX_WARNING_PAGE_RATIO or 0.65),
)
if decision.status == "fail" or (decision.status == "warn" and bool(settings.EDITABLE_PPTX_FAIL_ON_WARNING)):
    ...
```

Keep the existing zero-text deletion behavior, but route it through the decision object.

- [ ] **Step 6: Run quality and task tests**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
PYTHONPATH=. venv/bin/pytest -q tests/test_editable_pptx_quality.py tests/test_celery_task_health.py::test_editable_pptx_task_fails_when_no_text_boxes_are_restored
```

Expected: all selected tests pass.

---

### Task 7: Add Mode-Difference Regression Checks

**Files:**
- Modify: `backend/scripts/run_editable_pptx_pipeline.py`
- Test: `backend/tests/test_editable_pptx_quality.py`

- [ ] **Step 1: Add mode comparison test**

Add:

```python
from app.services.editable_pptx_quality import compare_restore_modes


def test_compare_restore_modes_flags_identical_counts():
    summary = {
        "standard": {"text_box_count": 10, "pptx": {"text_shape_count": 10}},
        "enhanced": {"text_box_count": 10, "pptx": {"text_shape_count": 10}},
        "aggressive": {"text_box_count": 10, "pptx": {"text_shape_count": 10}},
    }

    result = compare_restore_modes(summary)

    assert result["mode_difference"] == "none"
```

- [ ] **Step 2: Implement comparator**

Append to `backend/app/services/editable_pptx_quality.py`:

```python
def compare_restore_modes(mode_summary: dict[str, dict]) -> dict[str, str | int]:
    counts = {
        mode: int(data.get("text_box_count") or data.get("pptx", {}).get("text_shape_count") or 0)
        for mode, data in mode_summary.items()
    }
    unique_counts = set(counts.values())
    if len(unique_counts) <= 1 and len(counts) > 1:
        return {"mode_difference": "none", "distinct_count": len(unique_counts)}
    if counts.get("aggressive", 0) < counts.get("standard", 0):
        return {"mode_difference": "regressed", "distinct_count": len(unique_counts)}
    return {"mode_difference": "present", "distinct_count": len(unique_counts)}
```

- [ ] **Step 3: Add comparator output to benchmark script**

In `backend/scripts/run_editable_pptx_pipeline.py`, import and call:

```python
from app.services.editable_pptx_quality import compare_restore_modes


summary["mode_comparison"] = compare_restore_modes(summary["modes"])
```

- [ ] **Step 4: Run all three modes on a small synthetic fixture first**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
PYTHONPATH=. venv/bin/pytest -q tests/test_editable_pptx_quality.py::test_compare_restore_modes_flags_identical_counts
```

Expected: pass.

- [ ] **Step 5: Run all three modes on vivo as a manual benchmark**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
PYTHONPATH=. venv/bin/python scripts/run_editable_pptx_pipeline.py \
  "/Users/Joe_1/Desktop/vivo 校园学生市场 6-12 月整合策略案 20260603.pptx" \
  --output-dir "/Users/Joe_1/Desktop/Development/ppt-god/outputs/editable-benchmarks/vivo-20260603-all-modes" \
  --mode standard --mode enhanced --mode aggressive
```

Expected: summary reports mode comparison. If `mode_difference` is `none`, inspect OCR prompt output and policy thresholds before changing UI copy.

---

### Task 8: Persist Reviewable Diagnostics Beside The Output

**Files:**
- Modify: `backend/app/tasks.py`
- Modify: `backend/app/api/slides.py`
- Test: `backend/tests/test_celery_task_health.py`

- [ ] **Step 1: Write diagnostics JSON beside PPTX**

In `backend/app/tasks.py`, after a result is produced:

```python
diagnostics_path = os.path.splitext(output_path)[0] + "_diagnostics.json"
if result.diagnostics:
    with open(diagnostics_path, "w", encoding="utf-8") as f:
        json.dump(result.diagnostics.to_dict(), f, ensure_ascii=False, indent=2)
```

- [ ] **Step 2: Add a test that diagnostics file exists**

Extend the editable task test in `backend/tests/test_celery_task_health.py`:

```python
diagnostics_path = output_path.with_name(output_path.stem + "_diagnostics.json")
assert diagnostics_path.exists()
```

- [ ] **Step 3: Run the task test**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
PYTHONPATH=. venv/bin/pytest -q tests/test_celery_task_health.py -k editable_pptx
```

Expected: all selected tests pass.

---

### Task 9: Final Verification Gate

**Files:**
- Existing files only.

- [ ] **Step 1: Run focused backend test suite**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
PYTHONPATH=. venv/bin/pytest -q \
  tests/test_editable_pptx_export.py \
  tests/test_editable_pptx_diagnostics.py \
  tests/test_editable_pptx_quality.py \
  tests/test_celery_task_health.py::test_has_current_editable_pptx_rejects_image_only_file \
  tests/test_celery_task_health.py::test_has_current_editable_pptx_accepts_text_shapes \
  tests/test_celery_task_health.py::test_editable_pptx_task_fails_when_no_text_boxes_are_restored
```

Expected: all selected tests pass.

- [ ] **Step 2: Run vivo standard benchmark**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god/backend
PYTHONPATH=. venv/bin/python scripts/run_editable_pptx_pipeline.py \
  "/Users/Joe_1/Desktop/vivo 校园学生市场 6-12 月整合策略案 20260603.pptx" \
  --output-dir "/Users/Joe_1/Desktop/Development/ppt-god/outputs/editable-benchmarks/vivo-20260603-final" \
  --mode standard
```

Expected:

- `text_box_count > 0`
- `ocr_failed_pages` is empty or explicitly listed
- `editable_pipeline_summary.json` exists
- Manual contact sheet review shows no obvious systemic text oversizing compared with the June 4 baseline

- [ ] **Step 3: Run whitespace check**

Run:

```bash
cd /Users/Joe_1/Desktop/Development/ppt-god
git diff --check -- \
  backend/app/services/editable_pptx_export.py \
  backend/app/services/editable_pptx_diagnostics.py \
  backend/app/services/editable_pptx_quality.py \
  backend/app/tasks.py \
  backend/app/api/slides.py \
  backend/app/core/config.py \
  backend/scripts/run_editable_pptx_pipeline.py \
  backend/tests/test_editable_pptx_export.py \
  backend/tests/test_editable_pptx_diagnostics.py \
  backend/tests/test_editable_pptx_quality.py \
  backend/tests/test_celery_task_health.py
```

Expected: no output.

---

## Execution Order

Phase 1: smallest useful loop.

1. Task 1: benchmark harness.
2. Task 2: diagnostics.
3. Task 3: rejection reasons.
4. Task 4: text fitting.
5. Task 9: focused verification for the vivo standard benchmark.

Phase 2: quality stabilization.

6. Task 5: cleanup patch consolidation.
7. Task 6: production quality gates.
8. Task 8: persisted diagnostics.

Phase 3: mode robustness.

9. Task 7: mode-difference checks.
10. Task 9: full verification across selected cases.

This order is intentional. Do not tune OCR prompts or thresholds before Task 1 and Task 2 are complete; otherwise we will keep making changes without knowing whether quality improved or merely changed shape.

## Phase 3 Execution Notes

June 4 vivo mode audit used the standard OCR cache from the real source deck to isolate restore-policy differences without spending another full VLM run.

Artifacts:

- Before aggressive-policy fix: `.pptgod-data/outputs/mode-audit-vivo-20260604/editable_pipeline_summary.json`
- After aggressive-policy fix: `.pptgod-data/outputs/mode-audit-vivo-20260604-after-aggressive-fix/editable_pipeline_summary.json`
- After-fix aggressive visual contact sheet: `.pptgod-data/outputs/mode-audit-vivo-20260604-after-aggressive-fix/aggressive/contact_sheet_aggressive.png`

Policy-layer findings:

- Before fix: `enhanced` and `aggressive` were identical on the shared OCR input, both restoring 339 text boxes.
- Root cause: `editable=false` from OCR was applied before mode policy, so aggressive could not recover image/chart text marked non-editable.
- After fix: `standard` restores 329 text boxes, `enhanced` restores 339, and `aggressive` restores 368.
- Strict non-editable roles remain rejected in aggressive mode: Logo, watermark, decorative, page marker, and visual-only regions are still not restored as editable text.
- Visual tradeoff is explicit: aggressive now restores more image/chart annotations and can look busier, while standard/enhanced remain more conservative.

## Acceptance Criteria

- A bad all-image editable export cannot be marked successful.
- Each editable export has a diagnostics JSON artifact.
- Benchmark script can run standard/enhanced/aggressive on a local PPTX and summarize mode differences.
- Standard-mode vivo benchmark restores editable text without systemic oversizing.
- Dense pages may still require manual review, but the system must report which pages and why.
- Existing image PPTX export remains unchanged.
