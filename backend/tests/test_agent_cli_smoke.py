import importlib.util
from pathlib import Path


SMOKE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "agent_cli_smoke.py"
SMOKE_SPEC = importlib.util.spec_from_file_location("agent_cli_smoke_for_tests", SMOKE_PATH)
assert SMOKE_SPEC and SMOKE_SPEC.loader
agent_cli_smoke = importlib.util.module_from_spec(SMOKE_SPEC)
SMOKE_SPEC.loader.exec_module(agent_cli_smoke)


def test_expected_http_error_is_recorded_as_expected_failure():
    result = agent_cli_smoke.classify_result(
        "generate-slides",
        returncode=1,
        payload={"ok": False, "status": 400, "response": {"detail": "缺少生图 Prompt"}},
        expected_statuses={400},
    )

    assert result["ok"] is True
    assert result["outcome"] == "expected_failure"
    assert result["http_status"] == 400


def test_unexpected_nonzero_exit_is_recorded_as_failure():
    result = agent_cli_smoke.classify_result(
        "status",
        returncode=1,
        payload={"ok": False, "error": "接口返回非 JSON 响应"},
    )

    assert result["ok"] is False
    assert result["outcome"] == "failed"
