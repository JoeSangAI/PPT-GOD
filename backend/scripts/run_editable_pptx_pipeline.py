from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.services.editable_pptx_diagnostics import inspect_pptx_editability
from app.services.editable_pptx_export import build_editable_pptx
from app.services.template_extractor import convert_ppt_to_pdf, extract_pdf_thumbnails


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run editable PPTX pipeline against a source deck.")
    parser.add_argument("source_pptx")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--mode", action="append", choices=["standard", "enhanced", "aggressive"], default=None)
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--skip-preview", action="store_true")
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

    summary: dict[str, object] = {"source": str(source), "source_pages": len(source_pngs), "modes": {}}
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
        if not args.skip_preview:
            rendered_dir = mode_dir / "rendered"
            rendered_pdf = Path(convert_ppt_to_pdf(str(output_path), str(pdf_dir)))
            rendered_pngs = [Path(p) for p in extract_pdf_thumbnails(str(rendered_pdf), str(rendered_dir), dpi=110)]
            contact_sheet = mode_dir / f"contact_sheet_{mode}.png"
            _make_contact_sheet(source_pngs, rendered_pngs, contact_sheet)
            mode_summary["contact_sheet"] = str(contact_sheet)
        summary["modes"][mode] = mode_summary

    summary_path = root / "editable_pipeline_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(summary_path)


if __name__ == "__main__":
    main()
