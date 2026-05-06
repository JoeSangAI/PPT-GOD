import logging
import os
from typing import Dict, List

from PIL import Image
from pptx import Presentation
from pptx.util import Inches

from app.services.logo_assets import prepare_logo_overlay_image
from app.services.logo_policy import LOGO_HEIGHT_RATIOS, LOGO_WIDTH_RATIOS, normalize_logo_placement, should_show_logo

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

        if (
            logo_config
            and logo_config.get("file_path")
            and os.path.exists(logo_config["file_path"])
            and should_show_logo(slide_data)
        ):
            logo_path = prepare_logo_overlay_image(logo_config["file_path"])
            policy = (slide_data.get("visual_json") or {}).get("logo_policy") or {}
            left, top, width, height = _logo_geometry(
                prs,
                logo_path,
                str(slide_data.get("type") or "content").lower(),
                policy.get("placement") or logo_config.get("anchor") or "top-right",
                policy.get("scale") or "small",
            )
            slide.shapes.add_picture(
                logo_path,
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
