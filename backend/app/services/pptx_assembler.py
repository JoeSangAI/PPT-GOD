import logging
import os
from typing import Dict, List

from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE
from pptx.dml.color import RGBColor
from pptx.util import Inches

from app.services.logo_assets import prepare_logo_lockup_image
from app.services.logo_policy import LOGO_HEIGHT_RATIOS, LOGO_WIDTH_RATIOS, normalize_logo_placement, should_show_logo
from app.services.overlay_layers import contained_picture_box, enabled_overlay_layers, overlay_box

logger = logging.getLogger(__name__)


def _logo_geometry(prs: Presentation, logo_path: str, slide_type: str, placement: str, scale: str = "small"):
    placement = normalize_logo_placement(placement)
    margin = Inches(0.28)
    is_large = scale == "large" or slide_type in {"cover", "ending"}
    size_key = "large" if is_large else "small"
    max_width = int(prs.slide_width * LOGO_WIDTH_RATIOS[size_key])
    max_height = int(prs.slide_height * LOGO_HEIGHT_RATIOS[size_key])

    with Image.open(logo_path) as img:
        w_px, h_px = img.size
    ratio = h_px / max(w_px, 1)
    width = max_width
    height = int(width * ratio)
    if height > max_height:
        height = max_height
        width = int(height / max(ratio, 0.01))

    if placement == "center":
        left = int((prs.slide_width - width) / 2)
        top = int((prs.slide_height - height) / 2)
    elif placement == "lower-center":
        left = int((prs.slide_width - width) / 2)
        top = int(prs.slide_height * 0.68)
    elif placement == "title-block-center":
        left = int(prs.slide_width * 0.68 - width / 2)
        top = int(prs.slide_height * 0.70)
    else:
        left = margin if placement.endswith("left") else prs.slide_width - margin - width
        top = margin if placement.startswith("top") else prs.slide_height - margin - height
    return left, top, width, height


def assemble_pptx(
    slide_images: List[Dict],
    output_path: str,
    logo_config: Dict | None = None,
    overlay_assets: Dict[str, Dict] | None = None,
) -> str:
    """
    将生成的图片组装为 PPTX。

    slide_images: [{"page_num": int, "image_path": str, "speaker_notes": str}]
    output_path: 输出文件完整路径
    """
    prs = Presentation()
    # 16:9 (1792x1024)
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    # 按 page_num 排序
    sorted_slides = sorted(slide_images, key=lambda x: x["page_num"])

    # 安全获取空白布局：优先索引 6，不足时回退到最后一个
    blank_layout = prs.slide_layouts[6] if len(prs.slide_layouts) > 6 else prs.slide_layouts[-1]
    logo_paths = []
    if logo_config:
        raw_paths = logo_config.get("file_paths")
        if isinstance(raw_paths, list):
            logo_paths.extend(str(path) for path in raw_paths if path)
        elif logo_config.get("file_path"):
            logo_paths.append(str(logo_config["file_path"]))
    logo_path_for_overlay = prepare_logo_lockup_image(logo_paths) if logo_paths else None

    for slide_data in sorted_slides:
        slide = prs.slides.add_slide(blank_layout)

        # 插入背景图（铺满整张幻灯片）
        img_path = slide_data.get("image_path")
        if img_path and os.path.exists(img_path):
            slide.shapes.add_picture(
                img_path,
                left=Inches(0),
                top=Inches(0),
                width=prs.slide_width,
                height=prs.slide_height,
            )

        overlay_layers = enabled_overlay_layers(slide_data.get("visual_json") or {})
        for layer in overlay_layers:
            asset = (overlay_assets or {}).get(str(layer.get("asset_id")))
            asset_path = asset.get("file_path") if isinstance(asset, dict) else None
            if not asset_path or not os.path.exists(asset_path):
                logger.warning(
                    "Assembler: overlay asset missing for page %s asset=%s",
                    slide_data.get("page_num"),
                    layer.get("asset_id"),
                )
                continue
            left, top, width, height = overlay_box(prs, str(layer.get("preset") or "right-card"))
            if layer.get("mode") == "exact_card":
                card = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
                card.fill.solid()
                card.fill.fore_color.rgb = RGBColor(255, 255, 255)
                card.line.color.rgb = RGBColor(218, 226, 235)
                card.line.width = Inches(0.008)
                inset = int(min(width, height) * 0.055)
                pic_left, pic_top, pic_width, pic_height = contained_picture_box(
                    asset_path,
                    left + inset,
                    top + inset,
                    max(1, width - inset * 2),
                    max(1, height - inset * 2),
                )
            else:
                pic_left, pic_top, pic_width, pic_height = contained_picture_box(asset_path, left, top, width, height)
            slide.shapes.add_picture(
                asset_path,
                left=pic_left,
                top=pic_top,
                width=pic_width,
                height=pic_height,
            )

        if (
            logo_config
            and logo_path_for_overlay
            and os.path.exists(logo_path_for_overlay)
            and should_show_logo(slide_data)
        ):
            policy = (slide_data.get("visual_json") or {}).get("logo_policy") or {}
            left, top, width, height = _logo_geometry(
                prs,
                logo_path_for_overlay,
                str(slide_data.get("type") or "content").lower(),
                policy.get("placement") or logo_config.get("anchor") or "top-right",
                policy.get("scale") or "small",
            )
            slide.shapes.add_picture(
                logo_path_for_overlay,
                left=left,
                top=top,
                width=width,
                height=height,
            )

        # 写入 Speaker Notes
        notes = slide_data.get("speaker_notes", "")
        if notes:
            notes_slide = slide.notes_slide
            notes_text_frame = notes_slide.notes_text_frame
            notes_text_frame.text = notes

    prs.save(output_path)
    logger.info(f"Assembler: PPTX 已保存至 {output_path}，共 {len(sorted_slides)} 页")
    return output_path
