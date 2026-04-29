import logging
import os
from typing import Dict, List, Optional

from pptx import Presentation
from pptx.util import Inches, Pt

logger = logging.getLogger(__name__)


def assemble_pptx(
    slide_images: List[Dict],
    output_path: str,
    logo_path: Optional[str] = None,
) -> str:
    """
    将生成的图片组装为 PPTX。

    slide_images: [{"page_num": int, "image_path": str, "speaker_notes": str}]
    output_path: 输出文件完整路径
    logo_path: 可选的 Logo 图片路径（叠加到每页）
    """
    prs = Presentation()
    # 3:2 (1536x1024)
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(8.889)

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

        # 叠加 Logo（如果提供）
        if logo_path and os.path.exists(logo_path):
            # Logo 放在右下角，占屏幕宽度 8%
            logo_width = prs.slide_width * 0.08
            logo_height = logo_width  # 假设正方形
            slide.shapes.add_picture(
                logo_path,
                left=prs.slide_width - logo_width - Inches(0.3),
                top=prs.slide_height - logo_height - Inches(0.3),
                width=logo_width,
                height=logo_height,
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
