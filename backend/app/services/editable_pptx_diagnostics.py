from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def restored_text_count(self) -> int:
        return sum(page.restored_text_count for page in self.pages)

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
