from __future__ import annotations

import json
from pathlib import Path

from scripts.run_editable_pptx_pipeline import _cache_missing_pages


def _write_cache(path: Path) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "text": "标题",
                    "bbox": {"x": 0.1, "y": 0.1, "width": 0.4, "height": 0.1},
                    "role": "title",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_cache_missing_pages_uses_standard_cache_names(tmp_path: Path):
    _write_cache(tmp_path / "slide_01_ocr_regions.json")
    _write_cache(tmp_path / "slide_03_ocr_regions.json")

    assert _cache_missing_pages(tmp_path, [1, 2, 3], "standard") == [2]


def test_cache_missing_pages_uses_mode_specific_cache_names(tmp_path: Path):
    _write_cache(tmp_path / "slide_01_enhanced_ocr_regions.json")

    assert _cache_missing_pages(tmp_path, [1, 2], "enhanced") == [2]
