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
    assert message.startswith("⚠️ **还不能交付最终稿**")
    assert "目前只完成了 **1 / 2 页**，还有 **1 页**没有生成完成。" in message
    assert "请先补齐剩余页面，完成后再导出最终 PPTX。" in message
    assert "**下一步**" in message
    assert "1. 点击状态卡里的「继续生成剩余页」或重试未完成页" in message
    assert "2. 等页面全部完成后，点击「导出 PPTX」" in message
    assert "**需要处理**" in message
    assert "🔴 **未完成页面**" in message
    assert "第 2 页还未完成。请先补齐这些页面，再导出最终稿。" in message
    assert "🔴 **PPTX 暂未确认可导出**" in message
    assert "刷新状态后再试；如果仍不可导出，请重新生成或重试失败页。" in message
    assert "**建议复核**" in message
    assert "🟡 **Logo 对比度偏弱**" in message
    assert "第 1 页的 Logo 可能不够清晰。导出后建议顺手检查，必要时手动调整位置或替换 Logo。" in message
    assert "**说明**" in message
    assert "ℹ️ 章节页和金句页可以不放 Logo；内容页会保留品牌 Logo。" in message
    assert "\n>" not in message
    assert "- >" not in message


def test_project_quality_report_names_prototype_full_generation_cta():
    project = Project(id="p1", title="Deck", status="prototype_ready")
    slides = [
        Slide(
            id="s1",
            project_id="p1",
            page_num=1,
            type="cover",
            status="completed",
            image_path="/tmp/slide-1.png",
            content_json={"title": "样张"},
        ),
        Slide(
            id="s2",
            project_id="p1",
            page_num=2,
            type="content",
            status="prompt_ready",
            content_json={"title": "剩余页"},
        ),
    ]

    report = build_project_quality_report(project, slides, has_pptx=False)

    assert report is not None
    message = report["message"]
    assert "1. 点击状态卡里的「样张满意，生成全部」补齐剩余页" in message
    assert "生成全部页面" not in message
