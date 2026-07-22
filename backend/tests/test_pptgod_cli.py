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


def test_cli_validate_content_plan_rejects_half_supported_slide_type(tmp_path):
    plan_path = tmp_path / "half-supported.md"
    plan_path.write_text(VALID_MARKDOWN.replace("### 类型\ncover", "### 类型\ncontent_split"), encoding="utf-8")

    result = run_cli("validate-content-plan", str(plan_path))

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert any("content_split" in error for error in payload["errors"])


def test_cli_capabilities_reads_contract_without_login(monkeypatch, capsys):
    monkeypatch.setattr(pptgod_cli, "_start_backend", lambda _url: None)
    monkeypatch.setattr(
        pptgod_cli,
        "_get_json",
        lambda url, tester_id=None: (200, {"ok": True, "contract_version": "1", "service": {"name": "PPT GOD"}}),
    )

    exit_code = pptgod_cli.main(["capabilities"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["contract_version"] == "1"


def test_cli_whoami_uses_default_asang_account(monkeypatch, capsys):
    def fake_prepare(args, *, start_frontend=False):
        assert args.tester_name == "阿桑"
        return "http://backend", "http://frontend", "tester-1"

    monkeypatch.setattr(pptgod_cli, "_prepare_agent_request", fake_prepare)
    monkeypatch.setattr(
        pptgod_cli,
        "_get_json",
        lambda url, tester_id=None: (200, {"tester_id": tester_id, "display_name": "阿桑"}),
    )

    exit_code = pptgod_cli.main(["whoami"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["account"]["display_name"] == "阿桑"


def test_cli_list_projects_returns_machine_readable_count(monkeypatch, capsys):
    monkeypatch.setattr(
        pptgod_cli,
        "_prepare_agent_request",
        lambda _args, start_frontend=False: ("http://backend", "http://frontend", "tester-1"),
    )
    monkeypatch.setattr(
        pptgod_cli,
        "_get_json",
        lambda url, tester_id=None: (200, [{"id": "p1", "title": "第一份"}, {"id": "p2", "title": "第二份"}]),
    )

    exit_code = pptgod_cli.main(["list-projects"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["count"] == 2
    assert payload["projects"][1]["id"] == "p2"


def test_cli_doctor_checks_contract_models_account_and_projects(monkeypatch, capsys):
    monkeypatch.setattr(
        pptgod_cli,
        "_prepare_agent_request",
        lambda _args, start_frontend=False: ("http://backend", "http://frontend", "tester-1"),
    )

    def fake_get(url, tester_id=None):
        if url.endswith("/agent/capabilities"):
            return 200, {"contract_version": "1", "service": {"runtime_instance_id": "abc123"}}
        if "/agent/readiness?" in url:
            return 200, {
                "ready": True,
                "capabilities": {
                    "text_generation": {
                        "label": "文本生成",
                        "provider_configured": True,
                        "agent_supplied": False,
                        "model": "text-model",
                    },
                    "image_generation": {
                        "label": "图片生成",
                        "provider_configured": True,
                        "agent_supplied": False,
                        "model": "image-model",
                    },
                },
            }
        if url.endswith("/auth/me"):
            return 200, {"display_name": "阿桑"}
        return 200, [{"id": "p1"}]

    monkeypatch.setattr(pptgod_cli, "_get_json", fake_get)

    exit_code = pptgod_cli.main(["doctor"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == ""
    assert "文本生成已配置" in captured.err
    assert "图片生成已配置" in captured.err

    exit_code = pptgod_cli.main(["doctor", "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["complete"] is True
    assert payload["readiness"]["ready"] is True
    assert payload["project_count"] == 1
    assert payload["capabilities"]["service"]["runtime_instance_id"] == "abc123"
    assert captured.err == ""


def test_cli_doctor_explains_missing_models_and_returns_machine_readable_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        pptgod_cli,
        "_prepare_agent_request",
        lambda _args, start_frontend=False: ("http://backend", "http://frontend", "tester-1"),
    )

    def fake_get(url, tester_id=None):
        if url.endswith("/agent/capabilities"):
            return 200, {"contract_version": "1"}
        if "/agent/readiness?" in url:
            assert "agent_text=false" in url
            assert "agent_image=false" in url
            return 200, {
                "ready": False,
                "capabilities": {
                    "text_generation": {"label": "文本生成", "provider_configured": False, "agent_supplied": False},
                    "image_generation": {"label": "图片生成", "provider_configured": False, "agent_supplied": False},
                },
            }
        if url.endswith("/auth/me"):
            return 200, {"display_name": "阿桑"}
        return 200, []

    monkeypatch.setattr(pptgod_cli, "_get_json", fake_get)

    exit_code = pptgod_cli.main(["doctor"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == ""
    assert "文本生成未配置" in captured.err
    assert "图片生成未配置" in captured.err
    assert "PPT God 负责工作流，不自带模型额度" in captured.err

    exit_code = pptgod_cli.main(["doctor", "--json", "--strict"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 1
    assert payload["service_ok"] is True
    assert payload["ok"] is True
    assert payload["complete"] is False
    assert captured.err == ""


def test_cli_update_content_plan_defaults_to_dry_run(monkeypatch, tmp_path, capsys):
    plan_path = tmp_path / "plan.md"
    plan_path.write_text(VALID_MARKDOWN, encoding="utf-8")
    calls = []

    def fake_prepare(args, *, start_frontend=False):
        assert args.tester_name == "阿桑"
        assert start_frontend is False
        return "http://backend", "http://frontend", "tester-1"

    def fake_agent_post(_backend_url, path, payload, *, tester_id):
        calls.append((path, payload, tester_id))
        return 200, {
            "ok": True,
            "applied": False,
            "preview_token": "preview-1",
            "summary": {"changed": 1, "added": 0, "deleted": 0, "unchanged": 0},
        }

    monkeypatch.setattr(pptgod_cli, "_prepare_agent_request", fake_prepare)
    monkeypatch.setattr(pptgod_cli, "_agent_post", fake_agent_post)
    monkeypatch.setattr(pptgod_cli, "_provider_capability_error", lambda *_args: None)

    exit_code = pptgod_cli.main(["update-content-plan", "project-1", str(plan_path)])

    assert exit_code == 0
    assert len(calls) == 1
    assert calls[0][0] == "/agent/projects/project-1/content-plan/update"
    assert calls[0][1] == {
        "markdown": VALID_MARKDOWN,
        "apply": False,
        "frontend_base_url": "http://frontend",
    }
    assert calls[0][2] == "tester-1"
    assert json.loads(capsys.readouterr().out)["applied"] is False


def test_cli_update_content_plan_apply_repreviews_and_uses_token(monkeypatch, tmp_path, capsys):
    plan_path = tmp_path / "plan.md"
    plan_path.write_text(VALID_MARKDOWN, encoding="utf-8")
    payloads = []

    def fake_prepare(_args, *, start_frontend=False):
        assert start_frontend is False
        return "http://backend", "http://frontend", "tester-1"

    def fake_agent_post(_backend_url, path, payload, *, tester_id):
        assert path == "/agent/projects/project-1/content-plan/update"
        assert tester_id == "tester-1"
        payloads.append(payload)
        if not payload["apply"]:
            return 200, {"ok": True, "applied": False, "preview_token": "preview-1"}
        return 200, {"ok": True, "applied": True, "preview_token": "preview-1", "summary": {"changed": 1}}

    monkeypatch.setattr(pptgod_cli, "_prepare_agent_request", fake_prepare)
    monkeypatch.setattr(pptgod_cli, "_agent_post", fake_agent_post)

    exit_code = pptgod_cli.main(["update-content-plan", "project-1", str(plan_path), "--apply"])

    assert exit_code == 0
    assert len(payloads) == 2
    assert payloads[0]["apply"] is False
    assert payloads[1]["apply"] is True
    assert payloads[1]["expected_preview_token"] == "preview-1"
    assert json.loads(capsys.readouterr().out)["applied"] is True


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


def test_cli_open_print_only_resolves_authenticated_content_url_without_frontend_start(monkeypatch, capsys):
    def fake_prepare(_args, *, start_frontend=False):
        assert start_frontend is False
        return "http://backend", "http://frontend", "tester-1"

    def fake_agent_get(_backend_url, _path, *, tester_id, params=None):
        return 200, {
            "ok": True,
            "project": {"id": "project-1"},
        }

    def fake_handoff(
        backend_url,
        frontend_url,
        *,
        tester_id,
        project_id,
        stage,
        agent_text=False,
        agent_image=False,
        agent_name="外部 Agent",
    ):
        assert (backend_url, frontend_url) == ("http://backend", "http://frontend")
        assert (tester_id, project_id, stage) == ("tester-1", "project-1", "content")
        assert (agent_text, agent_image, agent_name) == (False, False, "外部 Agent")
        return (
            "http://frontend/app/projects/project-1?stage=content&handoff=short-lived",
            {"expires_in_seconds": 90},
        )

    monkeypatch.setattr(pptgod_cli, "_prepare_agent_request", fake_prepare)
    monkeypatch.setattr(pptgod_cli, "_agent_get", fake_agent_get)
    monkeypatch.setattr(pptgod_cli, "_resolve_authenticated_ui_url", fake_handoff)

    exit_code = pptgod_cli.main(["open", "project-1", "--stage", "content", "--print-only"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["opened"] is False
    assert payload["selected_ui_url"].endswith("?stage=content&handoff=short-lived")
    assert payload["handoff_expires_in_seconds"] == 90


def test_cli_import_slide_image_uses_agent_artifact_contract(monkeypatch, tmp_path, capsys):
    image_path = tmp_path / "slide.png"
    image_path.write_bytes(b"fake-image-for-transport-test")
    monkeypatch.setattr(
        pptgod_cli,
        "_prepare_agent_request",
        lambda _args, start_frontend=False: ("http://backend", "http://frontend", "tester-1"),
    )

    def fake_post(url, path, *, fields, tester_id):
        assert url.endswith("/agent/projects/project-1/slides/2/image")
        assert path == image_path
        assert fields == {"source": "codex_imagegen", "frontend_base_url": "http://frontend"}
        assert tester_id == "tester-1"
        return 200, {"ok": True, "project_id": "project-1", "page_num": 2}

    monkeypatch.setattr(pptgod_cli, "_post_multipart_file", fake_post)

    exit_code = pptgod_cli.main([
        "import-slide-image",
        "project-1",
        "2",
        str(image_path),
        "--source",
        "codex_imagegen",
    ])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["page_num"] == 2


def test_cli_import_visual_plan_accepts_a_plain_page_array(monkeypatch, tmp_path, capsys):
    plan_path = tmp_path / "visual-plan.json"
    plan_path.write_text(json.dumps([
        {
            "page_num": 1,
            "visual_description": "深蓝背景上的标题页",
            "prompt": "Create a 16:9 title slide",
        }
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        pptgod_cli,
        "_prepare_agent_request",
        lambda _args, start_frontend=False: ("http://backend", "http://frontend", "tester-1"),
    )

    def fake_agent_post(_backend_url, path, payload, *, tester_id):
        assert path == "/agent/projects/project-1/visual-plan/import"
        assert payload["frontend_base_url"] == "http://frontend"
        assert payload["pages"][0]["page_num"] == 1
        assert tester_id == "tester-1"
        return 200, {"ok": True, "project_status": "prompt_ready"}

    monkeypatch.setattr(pptgod_cli, "_agent_post", fake_agent_post)

    exit_code = pptgod_cli.main(["import-visual-plan", "project-1", str(plan_path)])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["project_status"] == "prompt_ready"


def test_cli_update_apply_open_uses_browser_handoff_instead_of_plain_project_url(monkeypatch, tmp_path, capsys):
    plan_path = tmp_path / "plan.md"
    plan_path.write_text(VALID_MARKDOWN, encoding="utf-8")
    opened = []

    def fake_prepare(_args, *, start_frontend=False):
        assert start_frontend is True
        return "http://backend", "http://frontend", "tester-1"

    def fake_agent_post(_backend_url, _path, payload, *, tester_id):
        if not payload["apply"]:
            return 200, {"ok": True, "preview_token": "preview-1"}
        return 200, {
            "ok": True,
            "applied": True,
            "content_review_url": "http://frontend/app/projects/project-1?stage=content",
        }

    monkeypatch.setattr(pptgod_cli, "_prepare_agent_request", fake_prepare)
    monkeypatch.setattr(pptgod_cli, "_agent_post", fake_agent_post)
    monkeypatch.setattr(
        pptgod_cli,
        "_resolve_authenticated_ui_url",
        lambda *_args, **_kwargs: (
            "http://frontend/app/projects/project-1?stage=content&handoff=one-time",
            {"expires_in_seconds": 90},
        ),
    )
    monkeypatch.setattr(pptgod_cli.webbrowser, "open", lambda url: opened.append(url) or True)

    exit_code = pptgod_cli.main([
        "update-content-plan",
        "project-1",
        str(plan_path),
        "--apply",
        "--open",
    ])

    assert exit_code == 0
    assert opened == ["http://frontend/app/projects/project-1?stage=content&handoff=one-time"]
    payload = json.loads(capsys.readouterr().out)
    assert payload["opened"] is True
    assert payload["authenticated_ui_url"] == opened[0]


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
    monkeypatch.setattr(pptgod_cli, "_provider_capability_error", lambda *_args: None)

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
    monkeypatch.setattr(pptgod_cli, "_provider_capability_error", lambda *_args: None)

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
    monkeypatch.setattr(pptgod_cli, "_provider_capability_error", lambda *_args: None)

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


def test_cli_wait_returns_when_matching_run_succeeds(monkeypatch, capsys):
    monkeypatch.setattr(
        pptgod_cli,
        "_prepare_agent_request",
        lambda _args, start_frontend=False: ("http://backend", "http://frontend", "tester-1"),
    )
    monkeypatch.setattr(
        pptgod_cli,
        "_agent_get",
        lambda *_args, **_kwargs: (
            200,
            {
                "ok": True,
                "workflow_status": {
                    "active_run": None,
                    "last_run": {"id": "run-1", "status": "succeeded"},
                },
            },
        ),
    )

    exit_code = pptgod_cli.main(["wait", "project-1", "--run-id", "run-1", "--timeout", "0"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["wait_result"] == {"run_id": "run-1", "status": "succeeded", "terminal": True}


def test_cli_wait_returns_failure_for_failed_run(monkeypatch, capsys):
    monkeypatch.setattr(
        pptgod_cli,
        "_prepare_agent_request",
        lambda _args, start_frontend=False: ("http://backend", "http://frontend", "tester-1"),
    )
    monkeypatch.setattr(
        pptgod_cli,
        "_agent_get",
        lambda *_args, **_kwargs: (
            200,
            {
                "ok": True,
                "workflow_status": {
                    "active_run": None,
                    "last_run": {"id": "run-2", "status": "failed"},
                },
            },
        ),
    )

    exit_code = pptgod_cli.main(["wait", "project-1", "--run-id", "run-2", "--timeout", "0"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["wait_result"]["status"] == "failed"


def test_cli_retry_failed_slides_posts_contract(monkeypatch, capsys):
    def fake_prepare(_args, *, start_frontend=False):
        return "http://backend", "http://frontend", "tester-1"

    def fake_agent_post(_backend_url, path, payload, *, tester_id):
        assert path == "/agent/projects/project-1/slides/retry-failed"
        assert payload == {"frontend_base_url": "http://frontend"}
        return 200, {"ok": True, "message": "Retry started"}

    monkeypatch.setattr(pptgod_cli, "_prepare_agent_request", fake_prepare)
    monkeypatch.setattr(pptgod_cli, "_agent_post", fake_agent_post)
    monkeypatch.setattr(pptgod_cli, "_provider_capability_error", lambda *_args: None)

    exit_code = pptgod_cli.main(["retry-failed-slides", "project-1"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["message"] == "Retry started"


def test_cli_confirm_prototype_posts_contract(monkeypatch, capsys):
    monkeypatch.setattr(
        pptgod_cli,
        "_prepare_agent_request",
        lambda _args, start_frontend=False: ("http://backend", "http://frontend", "tester-1"),
    )

    def fake_post(_backend_url, path, payload, *, tester_id):
        assert path == "/agent/projects/project-1/prototype/confirm"
        assert payload == {"frontend_base_url": "http://frontend"}
        return 200, {"ok": True, "run": {"id": "run-full"}}

    monkeypatch.setattr(pptgod_cli, "_agent_post", fake_post)
    monkeypatch.setattr(pptgod_cli, "_provider_capability_error", lambda *_args: None)

    exit_code = pptgod_cli.main(["confirm-prototype", "project-1"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["run"]["id"] == "run-full"


def test_cli_generation_command_stops_before_request_when_image_model_is_missing(monkeypatch, capsys):
    monkeypatch.setattr(
        pptgod_cli,
        "_prepare_agent_request",
        lambda _args, start_frontend=False: ("http://backend", "http://frontend", "tester-1"),
    )
    monkeypatch.setattr(
        pptgod_cli,
        "_provider_capability_error",
        lambda _url, capability: {
            "ok": False,
            "error": "missing_model_capability",
            "capability": capability,
        },
    )
    monkeypatch.setattr(
        pptgod_cli,
        "_agent_post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("generation request must not run")),
    )

    exit_code = pptgod_cli.main(["generate-slides", "project-1"])

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "missing_model_capability"
    assert payload["capability"] == "image_generation"


def test_cli_stop_generation_posts_contract(monkeypatch, capsys):
    monkeypatch.setattr(
        pptgod_cli,
        "_prepare_agent_request",
        lambda _args, start_frontend=False: ("http://backend", "http://frontend", "tester-1"),
    )

    def fake_post(_backend_url, path, payload, *, tester_id):
        assert path == "/agent/projects/project-1/runs/stop"
        assert payload == {"frontend_base_url": "http://frontend"}
        return 200, {"ok": True, "message": "Generation stopped"}

    monkeypatch.setattr(pptgod_cli, "_agent_post", fake_post)

    exit_code = pptgod_cli.main(["stop-generation", "project-1"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["message"] == "Generation stopped"


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
