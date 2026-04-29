from unittest.mock import patch

from app.api.slides import _build_slide_reference_contexts
from app.services.prompt_engine import generate_prompt_for_page


class FakeRef:
    def __init__(self, file_path, role="content_ref", process_mode="blend"):
        self.file_path = file_path
        self.role = role
        self.process_mode = process_mode


class FakeSlide:
    def __init__(self):
        self.page_num = 5
        self.reference_images = [
            FakeRef("/tmp/person.png", role="content_ref", process_mode="blend")
        ]


def test_slide_reference_context_analyzes_page_level_image(monkeypatch):
    monkeypatch.setattr("os.path.exists", lambda path: True)

    with patch("app.api.slides.analyze_reference_image") as analyze:
        analyze.return_value = {
            "composition_style": "人物居中，红色背景",
            "mood": "温暖、纪实",
            "description": "人物与小老虎合影，适合做视觉主体",
        }

        contexts, user_hints = _build_slide_reference_contexts([FakeSlide()])

    assert 5 in contexts
    assert 5 in user_hints
    assert "Reference Image 1" in contexts[5][0]
    assert "参考图1" in user_hints[5]
    assert "role=content_ref" in contexts[5][0]
    assert "process_mode=blend" in contexts[5][0]
    assert "Blend mode" in contexts[5][0]
    assert "人物与小老虎合影" in contexts[5][0]
    analyze.assert_called_once_with("/tmp/person.png")


def test_reference_context_includes_crop_and_original_intent(monkeypatch):
    monkeypatch.setattr("os.path.exists", lambda path: True)
    slide = FakeSlide()
    slide.reference_images = [
        FakeRef("/tmp/chart.png", role="chart_ref", process_mode="crop"),
        FakeRef("/tmp/cert.png", role="content_ref", process_mode="original"),
    ]

    with patch("app.api.slides.analyze_reference_image") as analyze:
        analyze.return_value = {
            "composition_style": "证书居中",
            "description": "证书或图表主体清晰",
        }

        contexts, user_hints = _build_slide_reference_contexts([slide])

    joined = "\n".join(contexts[5])
    assert "Reference Image 1" in joined
    assert "Reference Image 2" in joined
    assert "Crop mode" in joined
    assert "Original mode" in joined


@patch("app.services.prompt_engine._call_llm_for_final_prompt")
@patch("app.services.prompt_engine._load_template")
@patch("app.services.prompt_engine._extract_model_facing_text")
def test_prompt_keeps_reference_context_visible(mock_extract, mock_load, mock_llm):
    mock_load.return_value = "template"
    mock_extract.return_value = "template"
    mock_llm.return_value = "base prompt"

    prompt = generate_prompt_for_page(
        page_intent={"page_num": 6, "type": "content", "layout": "content_dense"},
        content_text={"headline": "转山路线图", "body": ""},
        reference_images=[
            {
                "role": "content_ref",
                "process_mode": "blend",
                "description": "Reference Image 1: analysis=白底路线图，绿色路径，红色节点，中心环绕式构图",
            }
        ],
    )

    rich_brief = mock_llm.call_args[0][0]
    assert "【References" in rich_brief
    assert "白底路线图" in rich_brief
    assert "绿色路径" in rich_brief
    assert "红色节点" in rich_brief
    assert "Rough placement relative to text" in rich_brief
    assert "转山路线图" in prompt

