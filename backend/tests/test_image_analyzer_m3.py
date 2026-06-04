from types import SimpleNamespace

from PIL import Image

from app.services import image_analyzer


class _FakeCompletions:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="VISION_OK"))]
        )


class _FakeClient:
    def __init__(self):
        self.completions = _FakeCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


def test_vision_model_uses_minimax_m3_multimodal_chat(monkeypatch, tmp_path):
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (1, 1), (255, 255, 255)).save(image_path)
    fake_client = _FakeClient()

    monkeypatch.setattr(image_analyzer, "get_llm_client", lambda: fake_client)
    monkeypatch.setattr(image_analyzer, "get_minimax_llm_model", lambda: "MiniMax-M3")
    monkeypatch.setattr(image_analyzer, "get_minimax_chat_extra_body", lambda: {"thinking": {"type": "disabled"}})

    result = image_analyzer._call_vision_model(str(image_path), "只回复 VISION_OK")

    assert result == "VISION_OK"
    assert fake_client.completions.kwargs["model"] == "MiniMax-M3"
    assert fake_client.completions.kwargs["max_tokens"] == 8192
    assert "max_completion_tokens" not in fake_client.completions.kwargs
    assert fake_client.completions.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    message = fake_client.completions.kwargs["messages"][0]
    assert message["role"] == "user"
    assert message["content"][0] == {"type": "text", "text": "只回复 VISION_OK"}
    assert message["content"][1]["type"] == "image_url"
    assert message["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "max_long_side_pixel" not in message["content"][1]["image_url"]
