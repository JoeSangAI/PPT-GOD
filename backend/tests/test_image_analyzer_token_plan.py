import base64
from unittest.mock import MagicMock

from app.services import image_analyzer


def test_call_vision_model_uses_token_plan_vlm_endpoint(monkeypatch, tmp_path):
    image_path = tmp_path / "ref.png"
    image_path.write_bytes(b"png-bytes")
    monkeypatch.setattr(image_analyzer.settings, "MINIMAX_API_KEY", "test-key")
    monkeypatch.setattr(image_analyzer.settings, "MINIMAX_API_BASE", "https://api.minimaxi.com/v1")

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {
            "content": '{"description":"路线图","composition_style":"地图居中"}',
            "base_resp": {"status_code": 0, "status_msg": "success"},
        }
        return resp

    monkeypatch.setattr(image_analyzer.requests, "post", fake_post)

    result = image_analyzer._call_vision_model(str(image_path), "analyze")

    assert result == '{"description":"路线图","composition_style":"地图居中"}'
    assert captured["url"] == "https://api.minimaxi.com/v1/coding_plan/vlm"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["headers"]["MM-API-Source"] == "Minimax-MCP"
    assert captured["json"]["prompt"] == "analyze"
    assert captured["json"]["image_url"] == (
        "data:image/png;base64," + base64.b64encode(b"png-bytes").decode("utf-8")
    )

