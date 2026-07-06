import importlib.util
import json
from pathlib import Path
import subprocess
import sys


CLI_PATH = Path(__file__).resolve().parents[2] / "scripts" / "pptgod_cli.py"
CLI_SPEC = importlib.util.spec_from_file_location("pptgod_cli_for_tests", CLI_PATH)
assert CLI_SPEC and CLI_SPEC.loader
pptgod_cli = importlib.util.module_from_spec(CLI_SPEC)
CLI_SPEC.loader.exec_module(pptgod_cli)


VALID_MARKDOWN = """# CLI 内容规划

## P1
### 类型
cover

### 标题
CLI 内容规划

### 副标题
本地校验命令

### 正文
这一页用于验证 CLI 可以复用后端内容规划 Markdown 校验逻辑。

### 备注
校验通过即可，不导入项目。
"""


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "scripts/pptgod_cli.py", *args],
        cwd=".",
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_validate_content_plan_returns_json_success(tmp_path):
    plan_path = tmp_path / "plan.md"
    plan_path.write_text(VALID_MARKDOWN, encoding="utf-8")

    result = run_cli("validate-content-plan", str(plan_path))

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["title"] == "CLI 内容规划"
    assert payload["slides_count"] == 1


def test_cli_validate_content_plan_returns_json_errors(tmp_path):
    plan_path = tmp_path / "bad.md"
    plan_path.write_text("# Bad\n\n## P1\n### 类型\ncontent", encoding="utf-8")

    result = run_cli("validate-content-plan", str(plan_path))

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert any("缺少字段" in error for error in payload["errors"])


def test_cli_status_reads_agent_project_status(monkeypatch, capsys):
    def fake_prepare(_args, *, start_frontend=False):
        assert start_frontend is False
        return "http://backend", "http://frontend", "tester-1"

    def fake_agent_get(_backend_url, path, *, tester_id, params=None):
        assert path == "/agent/projects/project-1/status"
        assert tester_id == "tester-1"
        assert params == {"frontend_base_url": "http://frontend"}
        return 200, {"ok": True, "project": {"id": "project-1", "status": "planning"}}

    monkeypatch.setattr(pptgod_cli, "_prepare_agent_request", fake_prepare)
    monkeypatch.setattr(pptgod_cli, "_agent_get", fake_agent_get)

    exit_code = pptgod_cli.main(["status", "project-1"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["project"]["id"] == "project-1"


def test_cli_open_print_only_resolves_content_url_without_frontend_start(monkeypatch, capsys):
    def fake_prepare(_args, *, start_frontend=False):
        assert start_frontend is False
        return "http://backend", "http://frontend", "tester-1"

    def fake_agent_get(_backend_url, _path, *, tester_id, params=None):
        return 200, {
            "ok": True,
            "project": {"id": "project-1"},
            "ui_urls": {
                "project": "http://frontend/projects/project-1",
                "content": "http://frontend/projects/project-1?stage=content",
            },
            "ui_url": "http://frontend/projects/project-1",
        }

    monkeypatch.setattr(pptgod_cli, "_prepare_agent_request", fake_prepare)
    monkeypatch.setattr(pptgod_cli, "_agent_get", fake_agent_get)

    exit_code = pptgod_cli.main(["open", "project-1", "--stage", "content", "--print-only"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["opened"] is False
    assert payload["selected_ui_url"].endswith("?stage=content")


def test_cli_export_content_plan_writes_output_file(monkeypatch, tmp_path, capsys):
    def fake_prepare(_args, *, start_frontend=False):
        assert start_frontend is False
        return "http://backend", "http://frontend", "tester-1"

    def fake_agent_get(_backend_url, path, *, tester_id, params=None):
        assert path == "/agent/projects/project-1/content-plan/export"
        return 200, {
            "ok": True,
            "project_id": "project-1",
            "filename": "内容规划.md",
            "markdown": "# Deck\n\n## P1\n### 类型\n\ncover\n",
        }

    output_path = tmp_path / "deck.md"
    monkeypatch.setattr(pptgod_cli, "_prepare_agent_request", fake_prepare)
    monkeypatch.setattr(pptgod_cli, "_agent_get", fake_agent_get)

    exit_code = pptgod_cli.main(["export-content-plan", "project-1", "--output", str(output_path)])

    assert exit_code == 0
    assert output_path.read_text(encoding="utf-8").startswith("# Deck")
    payload = json.loads(capsys.readouterr().out)
    assert payload["output_path"] == str(output_path.resolve())
    assert "markdown" not in payload


def test_cli_confirm_content_plan_posts_contract(monkeypatch, capsys):
    def fake_prepare(_args, *, start_frontend=False):
        assert start_frontend is False
        return "http://backend", "http://frontend", "tester-1"

    def fake_agent_post(_backend_url, path, payload, *, tester_id):
        assert path == "/agent/projects/project-1/content-plan/confirm"
        assert payload == {"frontend_base_url": "http://frontend"}
        assert tester_id == "tester-1"
        return 200, {"ok": True, "project": {"id": "project-1", "content_plan_confirmed": True}}

    monkeypatch.setattr(pptgod_cli, "_prepare_agent_request", fake_prepare)
    monkeypatch.setattr(pptgod_cli, "_agent_post", fake_agent_post)

    exit_code = pptgod_cli.main(["confirm-content-plan", "project-1"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True


def test_cli_start_visual_proposals_posts_force_and_description(monkeypatch, capsys):
    def fake_prepare(_args, *, start_frontend=False):
        return "http://backend", "http://frontend", "tester-1"

    def fake_agent_post(_backend_url, path, payload, *, tester_id):
        assert path == "/agent/projects/project-1/visual-proposals/start"
        assert payload == {
            "frontend_base_url": "http://frontend",
            "force": True,
            "user_description": "科技感更强",
        }
        return 200, {"ok": True, "status": "generating"}

    monkeypatch.setattr(pptgod_cli, "_prepare_agent_request", fake_prepare)
    monkeypatch.setattr(pptgod_cli, "_agent_post", fake_agent_post)

    exit_code = pptgod_cli.main(["start-visual-proposals", "project-1", "--force", "--user-description", "科技感更强"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "generating"


def test_cli_get_visual_proposals_reads_contract(monkeypatch, capsys):
    def fake_prepare(_args, *, start_frontend=False):
        return "http://backend", "http://frontend", "tester-1"

    def fake_agent_get(_backend_url, path, *, tester_id, params=None):
        assert path == "/agent/projects/project-1/visual-proposals"
        assert params == {"frontend_base_url": "http://frontend"}
        return 200, {"ok": True, "proposals_count": 2}

    monkeypatch.setattr(pptgod_cli, "_prepare_agent_request", fake_prepare)
    monkeypatch.setattr(pptgod_cli, "_agent_get", fake_agent_get)

    exit_code = pptgod_cli.main(["get-visual-proposals", "project-1"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["proposals_count"] == 2


def test_cli_confirm_visual_proposal_posts_one_based_index(monkeypatch, capsys):
    def fake_prepare(_args, *, start_frontend=False):
        return "http://backend", "http://frontend", "tester-1"

    def fake_agent_post(_backend_url, path, payload, *, tester_id):
        assert path == "/agent/projects/project-1/visual-proposals/confirm"
        assert payload == {"frontend_base_url": "http://frontend", "proposal_index": 2}
        return 200, {"ok": True, "selected_style": {"name": "高对比商业风"}}

    monkeypatch.setattr(pptgod_cli, "_prepare_agent_request", fake_prepare)
    monkeypatch.setattr(pptgod_cli, "_agent_post", fake_agent_post)

    exit_code = pptgod_cli.main(["confirm-visual-proposal", "project-1", "--index", "2"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["selected_style"]["name"] == "高对比商业风"


def test_cli_generate_visual_prompts_posts_page_nums(monkeypatch, capsys):
    def fake_prepare(_args, *, start_frontend=False):
        return "http://backend", "http://frontend", "tester-1"

    def fake_agent_post(_backend_url, path, payload, *, tester_id):
        assert path == "/agent/projects/project-1/visual-prompts/start"
        assert payload == {
            "frontend_base_url": "http://frontend",
            "page_nums": [1, 3],
            "stage_context": "更强调数据感",
        }
        return 200, {"ok": True, "status": "started"}

    monkeypatch.setattr(pptgod_cli, "_prepare_agent_request", fake_prepare)
    monkeypatch.setattr(pptgod_cli, "_agent_post", fake_agent_post)

    exit_code = pptgod_cli.main(["generate-visual-prompts", "project-1", "--page-nums", "1,3", "--stage-context", "更强调数据感"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "started"


def test_cli_generate_slides_posts_generation_payload(monkeypatch, capsys):
    def fake_prepare(_args, *, start_frontend=False):
        return "http://backend", "http://frontend", "tester-1"

    def fake_agent_post(_backend_url, path, payload, *, tester_id):
        assert path == "/agent/projects/project-1/slides/generate"
        assert payload == {
            "frontend_base_url": "http://frontend",
            "page_nums": [1, 3],
            "prototype": True,
        }
        return 200, {"ok": True, "message": "Generation started"}

    monkeypatch.setattr(pptgod_cli, "_prepare_agent_request", fake_prepare)
    monkeypatch.setattr(pptgod_cli, "_agent_post", fake_agent_post)

    exit_code = pptgod_cli.main(["generate-slides", "project-1", "--page-nums", "1,3", "--prototype"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_cli_get_generation_status_reads_contract(monkeypatch, capsys):
    def fake_prepare(_args, *, start_frontend=False):
        return "http://backend", "http://frontend", "tester-1"

    def fake_agent_get(_backend_url, path, *, tester_id, params=None):
        assert path == "/agent/projects/project-1/generation-status"
        assert params == {"frontend_base_url": "http://frontend"}
        return 200, {"ok": True, "workflow_status": {"project_status": "prompt_ready"}}

    monkeypatch.setattr(pptgod_cli, "_prepare_agent_request", fake_prepare)
    monkeypatch.setattr(pptgod_cli, "_agent_get", fake_agent_get)

    exit_code = pptgod_cli.main(["get-generation-status", "project-1"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["workflow_status"]["project_status"] == "prompt_ready"


def test_cli_retry_failed_slides_posts_contract(monkeypatch, capsys):
    def fake_prepare(_args, *, start_frontend=False):
        return "http://backend", "http://frontend", "tester-1"

    def fake_agent_post(_backend_url, path, payload, *, tester_id):
        assert path == "/agent/projects/project-1/slides/retry-failed"
        assert payload == {"frontend_base_url": "http://frontend"}
        return 200, {"ok": True, "message": "Retry started"}

    monkeypatch.setattr(pptgod_cli, "_prepare_agent_request", fake_prepare)
    monkeypatch.setattr(pptgod_cli, "_agent_post", fake_agent_post)

    exit_code = pptgod_cli.main(["retry-failed-slides", "project-1"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["message"] == "Retry started"


def test_cli_export_ppt_reads_download_contract(monkeypatch, capsys):
    def fake_prepare(_args, *, start_frontend=False):
        return "http://backend", "http://frontend", "tester-1"

    def fake_agent_get(_backend_url, path, *, tester_id, params=None):
        assert path == "/agent/projects/project-1/pptx/export"
        assert params == {
            "frontend_base_url": "http://frontend",
            "api_base_url": "http://backend",
            "prototype": "false",
        }
        return 200, {"ok": True, "download_url": "http://backend/projects/project-1/download"}

    monkeypatch.setattr(pptgod_cli, "_prepare_agent_request", fake_prepare)
    monkeypatch.setattr(pptgod_cli, "_agent_get", fake_agent_get)

    exit_code = pptgod_cli.main(["export-ppt", "project-1"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["download_url"].endswith("/download")


def test_cli_download_ppt_writes_file_from_download_url(monkeypatch, tmp_path, capsys):
    def fake_prepare(_args, *, start_frontend=False):
        return "http://backend", "http://frontend", "tester-1"

    def fake_agent_get(_backend_url, path, *, tester_id, params=None):
        return 200, {
            "ok": True,
            "filename": "Deck.pptx",
            "download_url": "http://backend/projects/project-1/download?tester_id=tester-1",
        }

    def fake_download(url, output_path, *, tester_id):
        assert url.endswith("tester_id=tester-1")
        assert tester_id == "tester-1"
        output_path.write_bytes(b"pptx")
        return {"output_path": str(output_path.resolve()), "bytes": 4}

    output_path = tmp_path / "deck.pptx"
    monkeypatch.setattr(pptgod_cli, "_prepare_agent_request", fake_prepare)
    monkeypatch.setattr(pptgod_cli, "_agent_get", fake_agent_get)
    monkeypatch.setattr(pptgod_cli, "_download_url_to_file", fake_download)

    exit_code = pptgod_cli.main(["download-ppt", "project-1", "--output", str(output_path)])

    assert exit_code == 0
    assert output_path.read_bytes() == b"pptx"
    payload = json.loads(capsys.readouterr().out)
    assert payload["output_path"] == str(output_path.resolve())


def test_cli_download_ppt_reports_http_status_on_download_failure(monkeypatch, tmp_path, capsys):
    def fake_prepare(_args, *, start_frontend=False):
        return "http://backend", "http://frontend", "tester-1"

    def fake_agent_get(_backend_url, path, *, tester_id, params=None):
        return 200, {
            "ok": True,
            "filename": "Deck.pptx",
            "download_url": "http://backend/projects/project-1/download?tester_id=tester-1",
        }

    def fake_download(_url, _output_path, *, tester_id):
        raise pptgod_cli.DownloadHttpError(404, "{\"detail\":\"还没有生成过页面\"}")

    monkeypatch.setattr(pptgod_cli, "_prepare_agent_request", fake_prepare)
    monkeypatch.setattr(pptgod_cli, "_agent_get", fake_agent_get)
    monkeypatch.setattr(pptgod_cli, "_download_url_to_file", fake_download)

    exit_code = pptgod_cli.main(["download-ppt", "project-1", "--output", str(tmp_path / "deck.pptx")])

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["status"] == 404
