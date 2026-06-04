from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from PIL import Image, ImageDraw, ImageFont

from app.services.editable_pptx_diagnostics import inspect_pptx_editability
from app.services.editable_pptx_export import (
    build_editable_pptx,
    normalize_editable_pptx_restore_mode,
    read_cached_ocr_regions,
)
from app.services.template_extractor import convert_ppt_to_pdf, extract_pdf_thumbnails


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run editable PPTX pipeline against a source deck.")
    parser.add_argument("source_pptx")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--mode", action="append", choices=["standard", "enhanced", "aggressive"], default=None)
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--skip-preview", action="store_true")
    parser.add_argument("--no-ocr-cache", action="store_true", help="Ignore existing OCR cache and call the VLM again.")
    parser.add_argument(
        "--policy-cache-mode",
        choices=["standard", "enhanced", "aggressive"],
        default=None,
        help="Use cached OCR from this mode for all output modes, to audit restore-policy differences without VLM calls.",
    )
    return parser.parse_args()


def _make_contact_sheet(source_pngs: list[Path], rendered_pngs: list[Path], output_path: Path) -> None:
    if not source_pngs or not rendered_pngs:
        return
    thumb_w, thumb_h = 300, 169
    pair_w = thumb_w * 2 + 26
    row_h = thumb_h + 34
    pairs_per_row = 3
    count = min(len(source_pngs), len(rendered_pngs))
    rows = (count + pairs_per_row - 1) // pairs_per_row
    sheet = Image.new("RGB", (pairs_per_row * pair_w + 20, rows * row_h + 36), "white")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
    except Exception:
        font = ImageFont.load_default()
    draw.text((10, 8), "Left: source render    Right: editable PPTX render", fill=(20, 28, 40), font=font)
    for index, (src, rendered) in enumerate(zip(source_pngs, rendered_pngs), start=1):
        grid = index - 1
        col = grid % pairs_per_row
        row = grid // pairs_per_row
        x0 = 10 + col * pair_w
        y0 = 32 + row * row_h
        draw.text((x0, y0), f"P{index}", fill=(20, 28, 40), font=font)
        for offset, path in [(0, src), (thumb_w + 12, rendered)]:
            img = Image.open(path).convert("RGB")
            img.thumbnail((thumb_w, thumb_h), Image.Resampling.LANCZOS)
            frame = Image.new("RGB", (thumb_w, thumb_h), (248, 250, 252))
            frame.paste(img, ((thumb_w - img.width) // 2, (thumb_h - img.height) // 2))
            sheet.paste(frame, (x0 + offset, y0 + 18))
            draw.rectangle(
                (x0 + offset, y0 + 18, x0 + offset + thumb_w - 1, y0 + 18 + thumb_h - 1),
                outline=(203, 213, 225),
            )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path)


def _cache_missing_pages(work_dir: Path, pages: list[int], restore_mode: str) -> list[int]:
    return [page for page in pages if read_cached_ocr_regions(work_dir, page, restore_mode) is None]


def _write_summary(summary_path: Path, summary: dict[str, object]) -> None:
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    args = parse_args()
    modes = args.mode or ["standard"]
    cache_mode = normalize_editable_pptx_restore_mode(args.policy_cache_mode) if args.policy_cache_mode else None
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
    page_nums = [int(item["page_num"]) for item in slide_images]

    shared_cache_dir: Path | None = None
    if cache_mode:
        shared_cache_dir = root / cache_mode / "assets"
        missing = _cache_missing_pages(shared_cache_dir, page_nums, cache_mode)
        if missing:
            missing_text = ", ".join(str(page) for page in missing)
            raise SystemExit(f"Missing {cache_mode} OCR cache pages in {shared_cache_dir}: {missing_text}")

    summary: dict[str, object] = {"source": str(source), "source_pages": len(source_pngs), "modes": {}}
    summary_path = root / "editable_pipeline_summary.json"
    for mode in modes:
        mode_dir = root / mode
        work_dir = mode_dir / "assets"
        output_path = mode_dir / f"editable_{mode}.pptx"
        mode_dir.mkdir(parents=True, exist_ok=True)
        print(f"[{mode}] build started", flush=True)

        def progress(done: int, total: int, message: str) -> None:
            print(f"[{mode}] {done}/{total} {message}", flush=True)

        ocr_provider = None
        if shared_cache_dir is not None and cache_mode is not None:
            ocr_provider = lambda _image_path, page_num: read_cached_ocr_regions(shared_cache_dir, page_num, cache_mode) or []

        result = build_editable_pptx(
            slide_images=slide_images,
            output_path=str(output_path),
            ocr_provider=ocr_provider,
            progress_callback=progress,
            work_dir=str(work_dir),
            restore_mode=mode,
            reuse_ocr_cache=not args.no_ocr_cache,
        )
        inspection = inspect_pptx_editability(output_path)
        mode_summary = {
            "output_path": str(output_path),
            "slide_count": result.slide_count,
            "text_box_count": result.text_box_count,
            "visual_asset_count": result.visual_asset_count,
            "ocr_failed_pages": result.ocr_failed_pages,
            "qa_retry_pages": result.qa_retry_pages,
            "quality_warning_pages": result.quality_warning_pages,
            "pptx": inspection.to_dict(),
            "diagnostics": result.diagnostics.to_dict() if result.diagnostics else None,
        }
        mode_summary_path = mode_dir / "diagnostics_summary.json"
        mode_summary_path.write_text(json.dumps(mode_summary, ensure_ascii=False, indent=2), encoding="utf-8")
        mode_summary["summary_path"] = str(mode_summary_path)
        if not args.skip_preview:
            rendered_dir = mode_dir / "rendered"
            rendered_dir.mkdir(parents=True, exist_ok=True)
            rendered_pdf = Path(convert_ppt_to_pdf(str(output_path), str(pdf_dir)))
            rendered_pngs = [Path(p) for p in extract_pdf_thumbnails(str(rendered_pdf), str(rendered_dir), dpi=110)]
            contact_sheet = mode_dir / f"contact_sheet_{mode}.png"
            _make_contact_sheet(source_pngs, rendered_pngs, contact_sheet)
            mode_summary["contact_sheet"] = str(contact_sheet)
        summary["modes"][mode] = mode_summary
        _write_summary(summary_path, summary)
        print(f"[{mode}] build finished: {output_path}", flush=True)

    print(summary_path)


if __name__ == "__main__":
    main()
