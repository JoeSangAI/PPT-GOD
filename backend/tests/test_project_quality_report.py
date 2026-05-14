from PIL import Image

from app.models.models import Project, ReferenceImage, Slide
from app.services.project_quality_report import build_project_quality_report


def test_project_quality_report_message_is_grouped_readable_markdown(tmp_path):
    logo_path = tmp_path / "logo.png"
    slide_path = tmp_path / "slide.png"
    Image.new("RGBA", (120, 60), (20, 20, 20, 255)).save(logo_path)
    Image.new("RGB", (1792, 1024), (15, 15, 25)).save(slide_path)

    project = Project(id="p1", title="Deck", status="completed")
    project.reference_images = [
        ReferenceImage(
            id="logo-1",
            project_id="p1",
            role="logo",
            file_path=str(logo_path),
            asset_analysis={"review_status": "user_confirmed"},
        )
    ]
    slides = [
        Slide(
            id="s1",
            project_id="p1",
            page_num=1,
            type="content",
            status="completed",
            image_path=str(slide_path),
            content_json={"title": "增长策略", "bullets": ["统一品牌露出"]},
            visual_json={
                "type": "content",
                "logo_policy": {
                    "show_logo": True,
                    "logo_contrast": "low_contrast_manual_review",
                },
            },
        ),
        Slide(
            id="s2",
            project_id="p1",
            page_num=2,
            type="content",
            status="pending",
            content_json={"title": "未完成页"},
        ),
    ]

    report = build_project_quality_report(project, slides, has_pptx=False)

    assert report is not None
    message = report["message"]
    assert message.startswith("**交付前检查**")
    assert "- 页面生成：1 / 2 页" in message
    assert "- PPTX：暂未确认可导出" in message
    assert "**需要处理**" in message
    assert "1. **存在未完成页面**：第 2 页。请先补齐这些页面，再导出最终稿。" in message
    assert "2. **未确认最终 PPTX 文件**：先刷新状态；如果仍不可导出，请重新生成或重试失败页。" in message
    assert "**建议复核**" in message
    assert "1. **Logo 对比度可能偏弱**：第 1 页。建议在导出的 PPT 里手动调整位置或替换为更适合当前底色的版本。" in message
    assert "\n>" not in message
    assert "- >" not in message
