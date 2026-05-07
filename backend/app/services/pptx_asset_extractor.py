import hashlib
import io
import os
import re
import zipfile
from dataclasses import dataclass, field
from typing import Iterable
from xml.etree import ElementTree as ET

from PIL import Image, ImageStat
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE


@dataclass
class PptxImageAsset:
    file_path: str
    source_page_num: int
    repeated_page_nums: list[int]
    classification: str
    role: str
    process_mode: str
    asset_kind: str | None
    asset_name: str
    usage_note: str
    metadata: dict = field(default_factory=dict)


@dataclass
class _ImageOccurrence:
    page_num: int
    blob: bytes
    ext: str
    left: int
    top: int
    width: int
    height: int
    slide_width: int
    slide_height: int
    slide_text: str

    @property
    def area_ratio(self) -> float:
        slide_area = max(1, self.slide_width * self.slide_height)
        return max(0, self.width) * max(0, self.height) / slide_area


def _safe_stem(filename: str) -> str:
    stem = os.path.splitext(os.path.basename(filename or "pptx"))[0]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return stem or "pptx"


def _iter_picture_shapes(shapes) -> Iterable:
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _iter_picture_shapes(shape.shapes)
            continue
        if shape.shape_type == MSO_SHAPE_TYPE.PICTURE and hasattr(shape, "image"):
            yield shape


def _shape_image_ext(shape) -> str:
    try:
        return (shape.image.ext or "png").lower()
    except Exception:
        return "png"


def _slide_text(slide) -> str:
    parts: list[str] = []
    for shape in slide.shapes:
        if hasattr(shape, "text") and shape.text and shape.text.strip():
            parts.append(shape.text.strip())
    return "\n".join(parts)


def _image_stats(blob: bytes) -> tuple[int, int, float, float, bool]:
    with Image.open(io.BytesIO(blob)) as img:
        rgba = img.convert("RGBA")
        width, height = rgba.size
        alpha = rgba.getchannel("A")
        alpha_min, alpha_max = alpha.getextrema()
        has_transparency = alpha_min < 250

        small = rgba.copy()
        small.thumbnail((160, 160))
        rgb = small.convert("RGB")
        colors = rgb.getcolors(maxcolors=4096) or []
        total = max(1, rgb.size[0] * rgb.size[1])
        dominant_share = max((count for count, _color in colors), default=0) / total

        stat = ImageStat.Stat(rgb)
        channel_std = sum(stat.stddev) / max(1, len(stat.stddev))
        return width, height, dominant_share, channel_std, has_transparency or alpha_max < 255


def _is_decorative(occ: _ImageOccurrence, dominant_share: float, channel_std: float) -> bool:
    if occ.width <= 0 or occ.height <= 0:
        return True
    if occ.area_ratio < 0.0015:
        return True
    if min(occ.width, occ.height) < 24:
        return True
    aspect = occ.width / max(1, occ.height)
    if (aspect > 12 or aspect < 1 / 12) and occ.area_ratio < 0.02:
        return True
    if dominant_share > 0.985 and channel_std < 8:
        return True
    return False


def _asset_kind_for_occ(occ: _ImageOccurrence, has_transparency: bool) -> str:
    if has_transparency and occ.area_ratio <= 0.12:
        return "material"
    if occ.area_ratio >= 0.22:
        return "scene"
    return "other"


def _classify_asset(occurrences: list[_ImageOccurrence], dominant_share: float, channel_std: float, has_transparency: bool) -> tuple[str, str, str | None]:
    first = occurrences[0]
    repeated_pages = {occ.page_num for occ in occurrences}
    if _is_decorative(first, dominant_share, channel_std):
        return "decorative", "ignore", None
    # Be conservative with auto-logo detection. Real PPTs often reuse phone
    # frames, QR-code screenshots, icons, and UI chrome across pages; promoting
    # those to a global logo is worse than leaving brand marks as recallable
    # library assets. Only very small repeated transparent marks become logo.
    if len(repeated_pages) >= 3 and first.area_ratio <= 0.006 and has_transparency:
        return "logo", "logo", None
    if len(repeated_pages) >= 4 and first.area_ratio <= 0.004:
        return "logo", "logo", None
    kind = _asset_kind_for_occ(first, has_transparency)
    return "useful", "content_ref", kind


def _slide_xml_blip_targets(file_bytes: bytes) -> dict[int, list[tuple[str, bytes]]]:
    """Find images referenced by slide XML, including backgrounds/fill blips.

    python-pptx exposes ordinary picture shapes well, but background images and
    shape fills may only appear as a:blip relationships. This fallback keeps
    those visual sources in the asset library without adding a new dependency.
    """
    ns = {
        "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    }
    result: dict[int, list[tuple[str, bytes]]] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            slide_names = sorted(
                (name for name in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", name)),
                key=lambda name: int(re.search(r"slide(\d+)\.xml$", name).group(1)),
            )
            for slide_name in slide_names:
                page_num = int(re.search(r"slide(\d+)\.xml$", slide_name).group(1))
                rels_name = slide_name.replace("ppt/slides/", "ppt/slides/_rels/") + ".rels"
                if rels_name not in zf.namelist():
                    continue
                rels_root = ET.fromstring(zf.read(rels_name))
                rel_targets = {
                    rel.attrib.get("Id"): rel.attrib.get("Target", "")
                    for rel in rels_root.findall("rel:Relationship", ns)
                }
                slide_root = ET.fromstring(zf.read(slide_name))
                for blip in slide_root.findall(".//a:blip", ns):
                    rel_id = blip.attrib.get(f"{{{ns['r']}}}embed") or blip.attrib.get(f"{{{ns['r']}}}link")
                    target = rel_targets.get(rel_id)
                    if not target:
                        continue
                    media_path = os.path.normpath(os.path.join("ppt/slides", target)).replace("\\", "/")
                    if media_path.startswith("ppt/slides/../"):
                        media_path = "ppt/" + media_path[len("ppt/slides/../"):]
                    if media_path not in zf.namelist():
                        continue
                    result.setdefault(page_num, []).append((media_path, zf.read(media_path)))
    except Exception:
        return result
    return result


def _keyword_tags(source_filename: str, occ: _ImageOccurrence, repeated_pages: list[int]) -> list[str]:
    tags = [
        "原PPT素材",
        f"第{occ.page_num}页",
        f"ppt_page_{occ.page_num}",
    ]
    if len(repeated_pages) > 1:
        tags.append("跨页重复素材")
        tags.extend(f"第{page_num}页" for page_num in repeated_pages[:8])
    for token in re.split(r"[\s,，。；;:：、/|()（）\[\]{}\"'“”‘’_-]+", occ.slide_text):
        token = token.strip()
        if 2 <= len(token) <= 24 and token not in tags:
            tags.append(token)
        if len(tags) >= 24:
            break
    source_stem = _safe_stem(source_filename)
    if source_stem not in tags:
        tags.append(source_stem)
    return tags


def _base_metadata(
    source_filename: str,
    digest: str,
    classification: str,
    occ: _ImageOccurrence,
    repeated_pages: list[int],
    pixel_w: int,
    pixel_h: int,
    dominant_share: float,
) -> dict:
    source_text = " ".join(occ.slide_text.split())
    return {
        "source_document": source_filename,
        "pptx_source_page_num": occ.page_num,
        "pptx_repeated_page_nums": repeated_pages,
        "pptx_image_sha1": digest,
        "classification": classification,
        "area_ratio": round(occ.area_ratio, 5),
        "pixel_size": [pixel_w, pixel_h],
        "dominant_color_share": round(dominant_share, 4),
        "source_slide_text": source_text[:800],
        "asset_tags": _keyword_tags(source_filename, occ, repeated_pages),
        "asset_lock": {
            "scope": "pptx_source_page",
            "source_document": source_filename,
            "page_num": occ.page_num,
            "repeated_page_nums": repeated_pages,
        },
    }


def extract_pptx_image_assets(
    file_bytes: bytes,
    source_filename: str,
    output_dir: str,
    *,
    max_assets_per_slide: int = 3,
    max_total_assets: int | None = None,
) -> list[PptxImageAsset]:
    """Extract useful images from a PPTX and classify them for the deck pipeline.

    This is intentionally conservative: it skips solid decorations and tiny
    icons, promotes repeated small transparent marks to logo, and keeps useful
    slide images as page references with source_page_num metadata.
    """
    prs = Presentation(io.BytesIO(file_bytes))
    os.makedirs(output_dir, exist_ok=True)

    occurrences_by_hash: dict[str, list[_ImageOccurrence]] = {}
    shape_digests_by_page: dict[int, set[str]] = {}
    for page_num, slide in enumerate(prs.slides, start=1):
        slide_text = _slide_text(slide)
        for shape in _iter_picture_shapes(slide.shapes):
            blob = shape.image.blob
            digest = hashlib.sha1(blob).hexdigest()
            shape_digests_by_page.setdefault(page_num, set()).add(digest)
            occurrences_by_hash.setdefault(digest, []).append(
                _ImageOccurrence(
                    page_num=page_num,
                    blob=blob,
                    ext=_shape_image_ext(shape),
                    left=int(shape.left or 0),
                    top=int(shape.top or 0),
                    width=int(shape.width or 0),
                    height=int(shape.height or 0),
                    slide_width=int(prs.slide_width),
                    slide_height=int(prs.slide_height),
                    slide_text=slide_text,
                )
            )

    xml_blips = _slide_xml_blip_targets(file_bytes)
    slide_text_by_page = {
        page_num: _slide_text(slide)
        for page_num, slide in enumerate(prs.slides, start=1)
    }
    for page_num, blips in xml_blips.items():
        for _media_path, blob in blips:
            digest = hashlib.sha1(blob).hexdigest()
            if digest in shape_digests_by_page.get(page_num, set()):
                continue
            occurrences_by_hash.setdefault(digest, []).append(
                _ImageOccurrence(
                    page_num=page_num,
                    blob=blob,
                    ext="png",
                    left=0,
                    top=0,
                    width=int(prs.slide_width),
                    height=int(prs.slide_height),
                    slide_width=int(prs.slide_width),
                    slide_height=int(prs.slide_height),
                    slide_text=slide_text_by_page.get(page_num, ""),
                )
            )

    assets: list[PptxImageAsset] = []
    per_slide_count: dict[int, int] = {}
    source_stem = _safe_stem(source_filename)

    for digest, occurrences in occurrences_by_hash.items():
        occurrences.sort(key=lambda occ: (occ.page_num, -occ.area_ratio))
        first = occurrences[0]
        try:
            pixel_w, pixel_h, dominant_share, channel_std, has_transparency = _image_stats(first.blob)
        except Exception:
            continue

        classification, role, kind = _classify_asset(occurrences, dominant_share, channel_std, has_transparency)
        if role == "ignore":
            continue

        page_nums = sorted({occ.page_num for occ in occurrences})
        safe_ext = "png"
        out_name = f"{source_stem}_p{first.page_num:03d}_{digest[:10]}.{safe_ext}"
        file_path = os.path.join(output_dir, out_name)
        if not os.path.exists(file_path):
            with Image.open(io.BytesIO(first.blob)) as img:
                mode = "RGBA" if img.mode in {"RGBA", "LA", "P"} else "RGB"
                img.convert(mode).save(file_path, "PNG")

        base_metadata = _base_metadata(
            source_filename,
            digest,
            classification,
            first,
            page_nums,
            pixel_w,
            pixel_h,
            dominant_share,
        )

        if role == "logo":
            assets.append(
                PptxImageAsset(
                    file_path=file_path,
                    source_page_num=first.page_num,
                    repeated_page_nums=page_nums,
                    classification=classification,
                    role="logo",
                    process_mode="original",
                    asset_kind=None,
                    asset_name=f"{source_stem} extracted logo",
                    usage_note="从上传 PPT 中识别出的重复 Logo，作为全局品牌标识使用。",
                    metadata=base_metadata,
                )
            )
            continue

        assets.append(
            PptxImageAsset(
                file_path=file_path,
                source_page_num=first.page_num,
                repeated_page_nums=page_nums,
                classification="library_asset",
                role="visual_asset",
                process_mode="crop" if kind in {"material", "other"} else "blend",
                asset_kind=kind,
                asset_name=f"{source_stem} p{first.page_num} image",
                usage_note="从上传 PPT 中提取的后台资源库素材；页面内容或用户后续调整相关时自动召回。",
                metadata={**base_metadata, "library_role": "global_recall_asset"},
            )
        )

        for occ in occurrences:
            if max_total_assets is not None and len(assets) >= max_total_assets:
                break
            if per_slide_count.get(occ.page_num, 0) >= max_assets_per_slide:
                continue
            per_slide_count[occ.page_num] = per_slide_count.get(occ.page_num, 0) + 1
            metadata = _base_metadata(
                source_filename,
                digest,
                classification,
                occ,
                page_nums,
                pixel_w,
                pixel_h,
                dominant_share,
            )
            assets.append(
                PptxImageAsset(
                    file_path=file_path,
                    source_page_num=occ.page_num,
                    repeated_page_nums=page_nums,
                    classification=classification,
                    role="content_ref",
                    process_mode="crop" if kind in {"material", "other"} else "blend",
                    asset_kind=kind,
                    asset_name=f"{source_stem} p{occ.page_num} image",
                    usage_note="从上传 PPT 对应页提取的有用图片，优先作为本页参考图使用。",
                    metadata=metadata,
                )
            )

    return assets[:max_total_assets] if max_total_assets is not None else assets
