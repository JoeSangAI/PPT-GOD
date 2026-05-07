from app.api.slides import _derive_project_title, _should_autoname_project
from app.models.models import Project


def test_auto_title_prefers_generated_cover_headline():
    outline = [
        {
            "page_num": 1,
            "type": "cover",
            "section_title": "",
            "text_content": {
                "headline": "果蝇之梯",
                "subhead": "从连接组到意识上传的故事",
                "body": "",
            },
        },
        {
            "page_num": 2,
            "section_title": "故事脉络",
            "text_content": {"headline": "一张脑图改变问题"},
        },
    ]

    title = _derive_project_title("帮我做一份关于果蝇脑图的 PPT", outline)

    assert title == "果蝇之梯"


def test_auto_title_falls_back_to_brief_and_ignores_attachment_markers():
    topic = """
【用户上传材料】
已上传文档：品牌策略提案.pptx
[[PPTGOD_ATTACHMENT:abc123]]
帮我做一份 AI Agent 工作流升级汇报，需要给管理层看。
"""

    title = _derive_project_title(topic, [])

    assert title == "AI Agent 工作流升级"


def test_auto_title_only_overwrites_default_project_names():
    assert _should_autoname_project(Project(title="未命名项目"))
    assert _should_autoname_project(Project(title="未命名项目 2"))
    assert not _should_autoname_project(Project(title="品牌策略提案"))
