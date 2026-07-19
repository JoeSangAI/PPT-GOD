#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
FRONTEND_DIR = PROJECT_ROOT / "frontend"
LOG_DIR = PROJECT_ROOT / ".pptgod-data" / "agent-cli"
DEFAULT_TESTER_NAME = "阿桑"
DEFAULT_FRONTEND_URL = os.getenv("PPTGOD_FRONTEND_URL", "http://localhost:8000")

sys.path.insert(0, str(BACKEND_DIR))

from app.services.content_plan_markdown import validate_content_plan_markdown  # noqa: E402


class DownloadHttpError(RuntimeError):
    def __init__(self, status: int, body: str):
        self.status = int(status)
        self.body = body
        super().__init__(f"HTTP {self.status}: {body[:240]}")


def _read_markdown(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _decode_json_body(body: str, *, url: str) -> dict:
    if not body.strip():
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        preview = body.strip().replace("\n", " ")[:240]
        raise RuntimeError(f"接口返回非 JSON 响应，请确认本地后端已重启并加载最新代码：{url}；响应片段：{preview}") from exc


def _validation_payload(markdown: str) -> dict:
    result = validate_content_plan_markdown(markdown)
    return {
        "ok": result.ok,
        "title": result.title,
        "slides_count": len(result.slides),
        "errors": result.errors,
        "warnings": result.warnings,
    }


def validate_content_plan_command(args) -> int:
    markdown = _read_markdown(Path(args.path))
    payload = _validation_payload(markdown)
    _print_json(payload)
    return 0 if payload["ok"] else 1


def _url_ok(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= int(response.status) < 500
    except Exception:
        return False


def _wait_for_url(url: str, timeout_seconds: float = 20.0) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if _url_ok(url):
            return True
        time.sleep(0.4)
    return False


def _open_log(name: str):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return open(LOG_DIR / name, "a", encoding="utf-8")


def _venv_python() -> str:
    candidate = BACKEND_DIR / "venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def _start_backend(backend_url: str) -> None:
    health_url = backend_url.rstrip("/") + "/health"
    if _url_ok(health_url):
        return
    stdout = _open_log("backend.log")
    subprocess.Popen(
        [
            _venv_python(),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            backend_url.rsplit(":", 1)[-1].strip("/"),
        ],
        cwd=str(BACKEND_DIR),
        stdout=stdout,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    if not _wait_for_url(health_url):
        raise RuntimeError(f"后端服务启动失败：{health_url} 不可用。日志：{LOG_DIR / 'backend.log'}")


def _start_frontend(frontend_url: str) -> None:
    if _url_ok(frontend_url):
        return
    stdout = _open_log("frontend.log")
    subprocess.Popen(
        [
            "npm",
            "run",
            "dev",
            "--",
            "--host",
            "127.0.0.1",
            "--port",
            frontend_url.rsplit(":", 1)[-1].strip("/"),
        ],
        cwd=str(FRONTEND_DIR),
        stdout=stdout,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    if not _wait_for_url(frontend_url, timeout_seconds=30):
        raise RuntimeError(f"前端服务启动失败：{frontend_url} 不可用。日志：{LOG_DIR / 'frontend.log'}")


def _post_json(url: str, payload: dict, *, tester_id: str | None = None) -> tuple[int, dict]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if tester_id:
        headers["x-pptgod-tester-id"] = tester_id
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            return int(response.status), _decode_json_body(body, url=url)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            payload = {"detail": body}
        return int(exc.code), payload


def _get_json(url: str, *, tester_id: str | None = None) -> tuple[int, dict]:
    headers = {}
    if tester_id:
        headers["x-pptgod-tester-id"] = tester_id
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            return int(response.status), _decode_json_body(body, url=url)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            payload = {"detail": body}
        return int(exc.code), payload


def _download_url_to_file(url: str, output_path: Path, *, tester_id: str | None = None) -> dict:
    headers = {}
    if tester_id:
        headers["x-pptgod-tester-id"] = tester_id
    request = urllib.request.Request(url, headers=headers, method="GET")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise DownloadHttpError(exc.code, body) from exc
    output_path.write_bytes(data)
    return {"output_path": str(output_path.resolve()), "bytes": len(data)}


def _tester_login(backend_url: str, tester_name: str) -> str:
    status, response = _post_json(
        backend_url.rstrip("/") + "/auth/tester-login",
        {"display_name": tester_name, "passcode": ""},
    )
    if not (200 <= status < 300) or not response.get("tester_id"):
        raise RuntimeError(f"测试账号登录失败：{response}")
    return str(response["tester_id"])


def _agent_get(
    backend_url: str,
    path: str,
    *,
    tester_id: str,
    params: dict[str, str] | None = None,
) -> tuple[int, dict]:
    query = f"?{urllib.parse.urlencode(params or {})}" if params else ""
    return _get_json(backend_url.rstrip("/") + path + query, tester_id=tester_id)


def _agent_post(
    backend_url: str,
    path: str,
    payload: dict,
    *,
    tester_id: str,
) -> tuple[int, dict]:
    return _post_json(backend_url.rstrip("/") + path, payload, tester_id=tester_id)


def _prepare_agent_request(args, *, start_frontend: bool = False) -> tuple[str, str, str]:
    backend_url = args.backend_url.rstrip("/")
    frontend_url = args.frontend_url.rstrip("/")
    if not args.no_start:
        _start_backend(backend_url)
        if start_frontend:
            _start_frontend(frontend_url)
    tester_id = _tester_login(backend_url, args.tester_name)
    return backend_url, frontend_url, tester_id


def capabilities_command(args) -> int:
    backend_url = args.backend_url.rstrip("/")
    if not args.no_start:
        _start_backend(backend_url)
    status, response = _get_json(backend_url + "/agent/capabilities")
    if not (200 <= status < 300):
        _print_json({"ok": False, "status": status, "response": response})
        return 1
    _print_json(response)
    return 0


def whoami_command(args) -> int:
    backend_url, _frontend_url, tester_id = _prepare_agent_request(args)
    status, response = _get_json(backend_url + "/auth/me", tester_id=tester_id)
    if not (200 <= status < 300):
        _print_json({"ok": False, "status": status, "response": response})
        return 1
    _print_json({"ok": True, "account": response})
    return 0


def list_projects_command(args) -> int:
    backend_url, _frontend_url, tester_id = _prepare_agent_request(args)
    status, response = _get_json(backend_url + "/projects", tester_id=tester_id)
    if not (200 <= status < 300):
        _print_json({"ok": False, "status": status, "response": response})
        return 1
    projects = response if isinstance(response, list) else []
    _print_json({"ok": True, "count": len(projects), "projects": projects})
    return 0


def doctor_command(args) -> int:
    backend_url, frontend_url, tester_id = _prepare_agent_request(args)
    cap_status, capabilities = _get_json(backend_url + "/agent/capabilities")
    me_status, account = _get_json(backend_url + "/auth/me", tester_id=tester_id)
    project_status, projects = _get_json(backend_url + "/projects", tester_id=tester_id)
    ok = all(200 <= status < 300 for status in (cap_status, me_status, project_status))
    _print_json({
        "ok": ok,
        "backend_url": backend_url,
        "frontend_url": frontend_url,
        "capabilities": capabilities if 200 <= cap_status < 300 else None,
        "account": account if 200 <= me_status < 300 else None,
        "project_count": len(projects) if isinstance(projects, list) else None,
        "checks": {
            "capabilities": cap_status,
            "account": me_status,
            "projects": project_status,
        },
    })
    return 0 if ok else 1


def import_content_plan_command(args) -> int:
    plan_path = Path(args.path)
    markdown = _read_markdown(plan_path)
    validation = _validation_payload(markdown)
    if not validation["ok"]:
        _print_json(validation)
        return 1

    backend_url = args.backend_url.rstrip("/")
    frontend_url = args.frontend_url.rstrip("/")
    if not args.no_start:
        _start_backend(backend_url)
        _start_frontend(frontend_url)

    tester_id = _tester_login(backend_url, args.tester_name)
    status, response = _post_json(
        backend_url + "/agent/content-plans/import",
        {
            "markdown": markdown,
            "title": args.title,
            "source_filename": os.path.basename(plan_path),
            "frontend_base_url": frontend_url,
        },
        tester_id=tester_id,
    )
    if not (200 <= status < 300):
        _print_json({"ok": False, "status": status, "response": response})
        return 1

    if args.open and response.get("ui_url"):
        webbrowser.open(response["ui_url"])
    _print_json(response)
    return 0


def update_content_plan_command(args) -> int:
    plan_path = Path(args.path)
    markdown = _read_markdown(plan_path)
    validation = _validation_payload(markdown)
    if not validation["ok"]:
        _print_json(validation)
        return 1

    backend_url, frontend_url, tester_id = _prepare_agent_request(
        args,
        start_frontend=bool(args.open and args.apply),
    )
    project_path = urllib.parse.quote(str(args.project_id), safe="")
    endpoint = f"/agent/projects/{project_path}/content-plan/update"
    preview_request = {
        "markdown": markdown,
        "apply": False,
        "frontend_base_url": frontend_url,
    }
    status, preview = _agent_post(backend_url, endpoint, preview_request, tester_id=tester_id)
    if not (200 <= status < 300):
        _print_json({"ok": False, "status": status, "response": preview})
        return 1

    if not args.apply:
        _print_json(preview)
        return 0

    preview_token = str(preview.get("preview_token") or "")
    if not preview_token:
        _print_json({"ok": False, "error": "dry-run 响应缺少 preview_token", "response": preview})
        return 1
    apply_request = {
        "markdown": markdown,
        "apply": True,
        "expected_preview_token": preview_token,
        "frontend_base_url": frontend_url,
    }
    status, response = _agent_post(backend_url, endpoint, apply_request, tester_id=tester_id)
    if not (200 <= status < 300):
        _print_json({"ok": False, "status": status, "response": response, "preview": preview})
        return 1

    if args.open and response.get("content_review_url"):
        webbrowser.open(str(response["content_review_url"]))
    _print_json(response)
    return 0


def status_command(args) -> int:
    backend_url, frontend_url, tester_id = _prepare_agent_request(args)
    project_path = urllib.parse.quote(str(args.project_id), safe="")
    status, response = _agent_get(
        backend_url,
        f"/agent/projects/{project_path}/status",
        tester_id=tester_id,
        params={"frontend_base_url": frontend_url},
    )
    if not (200 <= status < 300):
        _print_json({"ok": False, "status": status, "response": response})
        return 1
    _print_json(response)
    return 0


def open_project_command(args) -> int:
    backend_url, frontend_url, tester_id = _prepare_agent_request(args, start_frontend=not args.print_only)
    project_path = urllib.parse.quote(str(args.project_id), safe="")
    status, response = _agent_get(
        backend_url,
        f"/agent/projects/{project_path}/status",
        tester_id=tester_id,
        params={"frontend_base_url": frontend_url},
    )
    if not (200 <= status < 300):
        _print_json({"ok": False, "status": status, "response": response})
        return 1

    ui_urls = response.get("ui_urls") if isinstance(response.get("ui_urls"), dict) else {}
    ui_url = ui_urls.get(args.stage) or response.get("ui_url")
    if args.stage == "review":
        ui_url = ui_urls.get("project") or ui_url
    if not ui_url:
        _print_json({"ok": False, "error": "项目状态响应缺少 ui_url", "response": response})
        return 1

    if not args.print_only:
        webbrowser.open(str(ui_url))
    response["opened"] = not args.print_only
    response["selected_stage"] = args.stage
    response["selected_ui_url"] = ui_url
    _print_json(response)
    return 0


def export_content_plan_command(args) -> int:
    backend_url, frontend_url, tester_id = _prepare_agent_request(args)
    project_path = urllib.parse.quote(str(args.project_id), safe="")
    status, response = _agent_get(
        backend_url,
        f"/agent/projects/{project_path}/content-plan/export",
        tester_id=tester_id,
        params={"frontend_base_url": frontend_url},
    )
    if not (200 <= status < 300):
        _print_json({"ok": False, "status": status, "response": response})
        return 1

    markdown = str(response.get("markdown") or "")
    output_path = Path(args.output).expanduser() if args.output else None
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")
        response["output_path"] = str(output_path.resolve())
        if not args.include_markdown:
            response.pop("markdown", None)

    _print_json(response)
    return 0


def _post_agent_action(args, path: str, payload: dict) -> int:
    backend_url, frontend_url, tester_id = _prepare_agent_request(args)
    request_payload = {"frontend_base_url": frontend_url, **payload}
    status, response = _agent_post(backend_url, path, request_payload, tester_id=tester_id)
    if not (200 <= status < 300):
        _print_json({"ok": False, "status": status, "response": response})
        return 1
    _print_json(response)
    return 0


def confirm_content_plan_command(args) -> int:
    project_path = urllib.parse.quote(str(args.project_id), safe="")
    return _post_agent_action(args, f"/agent/projects/{project_path}/content-plan/confirm", {})


def start_visual_proposals_command(args) -> int:
    project_path = urllib.parse.quote(str(args.project_id), safe="")
    payload = {"force": bool(args.force)}
    if args.user_description:
        payload["user_description"] = args.user_description
    return _post_agent_action(args, f"/agent/projects/{project_path}/visual-proposals/start", payload)


def get_visual_proposals_command(args) -> int:
    backend_url, frontend_url, tester_id = _prepare_agent_request(args)
    project_path = urllib.parse.quote(str(args.project_id), safe="")
    status, response = _agent_get(
        backend_url,
        f"/agent/projects/{project_path}/visual-proposals",
        tester_id=tester_id,
        params={"frontend_base_url": frontend_url},
    )
    if not (200 <= status < 300):
        _print_json({"ok": False, "status": status, "response": response})
        return 1
    _print_json(response)
    return 0


def _read_json_file(path: str) -> dict:
    return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))


def confirm_visual_proposal_command(args) -> int:
    project_path = urllib.parse.quote(str(args.project_id), safe="")
    payload: dict = {}
    if args.index is not None:
        payload["proposal_index"] = int(args.index)
    if args.style_json:
        payload["selected_style"] = _read_json_file(args.style_json)
    return _post_agent_action(args, f"/agent/projects/{project_path}/visual-proposals/confirm", payload)


def _parse_page_nums(value: str | None) -> list[int] | None:
    if not value:
        return None
    nums: list[int] = []
    for part in value.split(","):
        item = part.strip()
        if not item:
            continue
        nums.append(int(item))
    return nums or None


def generate_visual_prompts_command(args) -> int:
    project_path = urllib.parse.quote(str(args.project_id), safe="")
    payload: dict = {}
    page_nums = _parse_page_nums(args.page_nums)
    if page_nums:
        payload["page_nums"] = page_nums
    if args.stage_context:
        payload["stage_context"] = args.stage_context
    return _post_agent_action(args, f"/agent/projects/{project_path}/visual-prompts/start", payload)


def generate_slides_command(args) -> int:
    project_path = urllib.parse.quote(str(args.project_id), safe="")
    payload: dict = {"prototype": bool(args.prototype)}
    page_nums = _parse_page_nums(args.page_nums)
    if page_nums:
        payload["page_nums"] = page_nums
    return _post_agent_action(args, f"/agent/projects/{project_path}/slides/generate", payload)


def get_generation_status_command(args) -> int:
    backend_url, frontend_url, tester_id = _prepare_agent_request(args)
    project_path = urllib.parse.quote(str(args.project_id), safe="")
    status, response = _agent_get(
        backend_url,
        f"/agent/projects/{project_path}/generation-status",
        tester_id=tester_id,
        params={"frontend_base_url": frontend_url},
    )
    if not (200 <= status < 300):
        _print_json({"ok": False, "status": status, "response": response})
        return 1
    _print_json(response)
    return 0


def wait_command(args) -> int:
    backend_url, frontend_url, tester_id = _prepare_agent_request(args)
    project_path = urllib.parse.quote(str(args.project_id), safe="")
    endpoint = f"/agent/projects/{project_path}/generation-status"
    deadline = time.monotonic() + max(0.0, float(args.timeout))
    requested_run_id = str(args.run_id or "").strip() or None

    while True:
        status, response = _agent_get(
            backend_url,
            endpoint,
            tester_id=tester_id,
            params={"frontend_base_url": frontend_url},
        )
        if not (200 <= status < 300):
            _print_json({"ok": False, "status": status, "response": response})
            return 1

        workflow = response.get("workflow_status") if isinstance(response.get("workflow_status"), dict) else {}
        active_run = workflow.get("active_run") if isinstance(workflow.get("active_run"), dict) else None
        last_run = workflow.get("last_run") if isinstance(workflow.get("last_run"), dict) else None
        matching_run = None
        if requested_run_id:
            for candidate in (active_run, last_run):
                if candidate and str(candidate.get("id") or "") == requested_run_id:
                    matching_run = candidate
                    break
            if matching_run is None and active_run is None:
                _print_json({
                    "ok": False,
                    "error": f"未找到 run_id={requested_run_id}",
                    "project_id": args.project_id,
                    "workflow_status": workflow,
                })
                return 1
        else:
            matching_run = active_run or last_run

        run_status = str((matching_run or {}).get("status") or "")
        if active_run is None or run_status in {"succeeded", "failed", "cancelled", "stale"}:
            response["wait_result"] = {
                "run_id": (matching_run or {}).get("id"),
                "status": run_status or "idle",
                "terminal": True,
            }
            _print_json(response)
            return 0 if run_status not in {"failed", "cancelled", "stale"} else 1

        if time.monotonic() >= deadline:
            _print_json({
                "ok": False,
                "error": "等待任务超时",
                "project_id": args.project_id,
                "run_id": (matching_run or {}).get("id"),
                "workflow_status": workflow,
            })
            return 1
        time.sleep(max(0.2, float(args.interval)))


def retry_failed_slides_command(args) -> int:
    project_path = urllib.parse.quote(str(args.project_id), safe="")
    return _post_agent_action(args, f"/agent/projects/{project_path}/slides/retry-failed", {})


def confirm_prototype_command(args) -> int:
    project_path = urllib.parse.quote(str(args.project_id), safe="")
    return _post_agent_action(args, f"/agent/projects/{project_path}/prototype/confirm", {})


def stop_generation_command(args) -> int:
    project_path = urllib.parse.quote(str(args.project_id), safe="")
    return _post_agent_action(args, f"/agent/projects/{project_path}/runs/stop", {})


def _export_ppt_contract(args) -> tuple[int, dict, str]:
    backend_url, frontend_url, tester_id = _prepare_agent_request(args)
    project_path = urllib.parse.quote(str(args.project_id), safe="")
    status, response = _agent_get(
        backend_url,
        f"/agent/projects/{project_path}/pptx/export",
        tester_id=tester_id,
        params={
            "frontend_base_url": frontend_url,
            "api_base_url": backend_url,
            "prototype": "true" if args.prototype else "false",
        },
    )
    return status, response, tester_id


def export_ppt_command(args) -> int:
    status, response, _tester_id = _export_ppt_contract(args)
    if not (200 <= status < 300):
        _print_json({"ok": False, "status": status, "response": response})
        return 1
    _print_json(response)
    return 0


def download_ppt_command(args) -> int:
    status, response, tester_id = _export_ppt_contract(args)
    if not (200 <= status < 300):
        _print_json({"ok": False, "status": status, "response": response})
        return 1
    download_url = str(response.get("download_url") or "")
    if not download_url:
        _print_json({"ok": False, "error": "导出响应缺少 download_url", "response": response})
        return 1
    filename = str(response.get("filename") or ("prototype.pptx" if args.prototype else "presentation.pptx"))
    output_path = Path(args.output or filename).expanduser()
    try:
        download_info = _download_url_to_file(download_url, output_path, tester_id=tester_id)
    except DownloadHttpError as exc:
        _print_json({"ok": False, "status": exc.status, "response": {"detail": exc.body}, "download_url": download_url})
        return 1
    response.update(download_info)
    _print_json(response)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PPT God local Agent CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    capabilities_parser = subparsers.add_parser("capabilities", help="Read PPT God Agent contract and service capabilities")
    capabilities_parser.add_argument("--no-start", action="store_true", help="Do not auto-start local backend service")
    capabilities_parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    capabilities_parser.set_defaults(func=capabilities_command)

    whoami_parser = subparsers.add_parser("whoami", help="Show the current PPT God CLI account")
    whoami_parser.add_argument("--tester-name", default=os.getenv("PPTGOD_TESTER_NAME", DEFAULT_TESTER_NAME))
    whoami_parser.add_argument("--no-start", action="store_true", help="Do not auto-start local backend service")
    whoami_parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    whoami_parser.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    whoami_parser.set_defaults(func=whoami_command)

    list_projects_parser = subparsers.add_parser("list-projects", help="List projects owned by the current PPT God account")
    list_projects_parser.add_argument("--tester-name", default=os.getenv("PPTGOD_TESTER_NAME", DEFAULT_TESTER_NAME))
    list_projects_parser.add_argument("--no-start", action="store_true", help="Do not auto-start local backend service")
    list_projects_parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    list_projects_parser.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    list_projects_parser.set_defaults(func=list_projects_command)

    doctor_parser = subparsers.add_parser("doctor", help="Verify service contract, account, and project connection")
    doctor_parser.add_argument("--tester-name", default=os.getenv("PPTGOD_TESTER_NAME", DEFAULT_TESTER_NAME))
    doctor_parser.add_argument("--no-start", action="store_true", help="Do not auto-start local backend service")
    doctor_parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    doctor_parser.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    doctor_parser.set_defaults(func=doctor_command)

    validate_parser = subparsers.add_parser("validate-content-plan", help="Validate strict content-plan Markdown")
    validate_parser.add_argument("path", help="Path to content-plan Markdown")
    validate_parser.set_defaults(func=validate_content_plan_command)

    import_parser = subparsers.add_parser("import-content-plan", help="Import strict content-plan Markdown into a new PPT God project")
    import_parser.add_argument("path", help="Path to content-plan Markdown")
    import_parser.add_argument("--title", default=None, help="Project title. Defaults to Markdown H1, then filename.")
    import_parser.add_argument("--tester-name", default=os.getenv("PPTGOD_TESTER_NAME", DEFAULT_TESTER_NAME), help="PPT God tester name that will own the imported project")
    import_parser.add_argument("--open", action="store_true", help="Open the content review UI after import")
    import_parser.add_argument("--no-start", action="store_true", help="Do not auto-start local backend/frontend services")
    import_parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    import_parser.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    import_parser.set_defaults(func=import_content_plan_command)

    update_parser = subparsers.add_parser(
        "update-content-plan",
        help="Preview or apply strict Markdown changes to an existing PPT God project",
    )
    update_parser.add_argument("project_id", help="Existing PPT God project id")
    update_parser.add_argument("path", help="Path to strict content-plan Markdown")
    update_parser.add_argument("--apply", action="store_true", help="Apply the previewed diff in place; default is dry-run only")
    update_parser.add_argument("--open", action="store_true", help="Open the content review UI after a successful apply")
    update_parser.add_argument("--tester-name", default=os.getenv("PPTGOD_TESTER_NAME", DEFAULT_TESTER_NAME), help="PPT God tester name that owns the project")
    update_parser.add_argument("--no-start", action="store_true", help="Do not auto-start local backend/frontend services")
    update_parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    update_parser.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    update_parser.set_defaults(func=update_content_plan_command)

    status_parser = subparsers.add_parser("status", help="Read a PPT God project status for Agent handoff")
    status_parser.add_argument("project_id", help="PPT God project id")
    status_parser.add_argument("--tester-name", default=os.getenv("PPTGOD_TESTER_NAME", DEFAULT_TESTER_NAME), help="PPT God tester name that owns the project")
    status_parser.add_argument("--no-start", action="store_true", help="Do not auto-start local backend service")
    status_parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    status_parser.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    status_parser.set_defaults(func=status_command)

    open_parser = subparsers.add_parser("open", help="Open a PPT God project UI after verifying project access")
    open_parser.add_argument("project_id", help="PPT God project id")
    open_parser.add_argument("--stage", choices=["project", "content", "visual", "review"], default="project")
    open_parser.add_argument("--print-only", action="store_true", help="Print the resolved URL without opening a browser")
    open_parser.add_argument("--tester-name", default=os.getenv("PPTGOD_TESTER_NAME", DEFAULT_TESTER_NAME), help="PPT God tester name that owns the project")
    open_parser.add_argument("--no-start", action="store_true", help="Do not auto-start local backend/frontend services")
    open_parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    open_parser.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    open_parser.set_defaults(func=open_project_command)

    export_parser = subparsers.add_parser("export-content-plan", help="Export a project content plan as strict Agent Markdown")
    export_parser.add_argument("project_id", help="PPT God project id")
    export_parser.add_argument("--output", default=None, help="Optional path to write the exported Markdown")
    export_parser.add_argument("--include-markdown", action="store_true", help="Keep markdown in JSON output even when --output is set")
    export_parser.add_argument("--tester-name", default=os.getenv("PPTGOD_TESTER_NAME", DEFAULT_TESTER_NAME), help="PPT God tester name that owns the project")
    export_parser.add_argument("--no-start", action="store_true", help="Do not auto-start local backend service")
    export_parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    export_parser.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    export_parser.set_defaults(func=export_content_plan_command)

    confirm_content_parser = subparsers.add_parser("confirm-content-plan", help="Confirm imported/generated content plan and move to visual stage")
    confirm_content_parser.add_argument("project_id", help="PPT God project id")
    confirm_content_parser.add_argument("--tester-name", default=os.getenv("PPTGOD_TESTER_NAME", DEFAULT_TESTER_NAME), help="PPT God tester name that owns the project")
    confirm_content_parser.add_argument("--no-start", action="store_true", help="Do not auto-start local backend service")
    confirm_content_parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    confirm_content_parser.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    confirm_content_parser.set_defaults(func=confirm_content_plan_command)

    start_visual_parser = subparsers.add_parser("start-visual-proposals", help="Start or reuse PPT God visual proposal generation")
    start_visual_parser.add_argument("project_id", help="PPT God project id")
    start_visual_parser.add_argument("--force", action="store_true", help="Regenerate proposals even when cached proposals exist")
    start_visual_parser.add_argument("--user-description", default="", help="Optional visual direction requirements")
    start_visual_parser.add_argument("--tester-name", default=os.getenv("PPTGOD_TESTER_NAME", DEFAULT_TESTER_NAME), help="PPT God tester name that owns the project")
    start_visual_parser.add_argument("--no-start", action="store_true", help="Do not auto-start local backend service")
    start_visual_parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    start_visual_parser.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    start_visual_parser.set_defaults(func=start_visual_proposals_command)

    get_visual_parser = subparsers.add_parser("get-visual-proposals", help="Read cached or running visual proposals")
    get_visual_parser.add_argument("project_id", help="PPT God project id")
    get_visual_parser.add_argument("--tester-name", default=os.getenv("PPTGOD_TESTER_NAME", DEFAULT_TESTER_NAME), help="PPT God tester name that owns the project")
    get_visual_parser.add_argument("--no-start", action="store_true", help="Do not auto-start local backend service")
    get_visual_parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    get_visual_parser.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    get_visual_parser.set_defaults(func=get_visual_proposals_command)

    confirm_visual_parser = subparsers.add_parser("confirm-visual-proposal", help="Confirm one visual proposal by index or a supplied style JSON")
    confirm_visual_parser.add_argument("project_id", help="PPT God project id")
    confirm_visual_parser.add_argument("--index", type=int, default=None, help="One-based proposal index to confirm")
    confirm_visual_parser.add_argument("--style-json", default=None, help="Path to a selected_style JSON object")
    confirm_visual_parser.add_argument("--tester-name", default=os.getenv("PPTGOD_TESTER_NAME", DEFAULT_TESTER_NAME), help="PPT God tester name that owns the project")
    confirm_visual_parser.add_argument("--no-start", action="store_true", help="Do not auto-start local backend service")
    confirm_visual_parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    confirm_visual_parser.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    confirm_visual_parser.set_defaults(func=confirm_visual_proposal_command)

    visual_prompts_parser = subparsers.add_parser("generate-visual-prompts", help="Start visual-plan and image-prompt generation")
    visual_prompts_parser.add_argument("project_id", help="PPT God project id")
    visual_prompts_parser.add_argument("--page-nums", default=None, help="Optional comma-separated page numbers, such as 1,3,5")
    visual_prompts_parser.add_argument("--stage-context", default="", help="Optional generation requirements for this run")
    visual_prompts_parser.add_argument("--tester-name", default=os.getenv("PPTGOD_TESTER_NAME", DEFAULT_TESTER_NAME), help="PPT God tester name that owns the project")
    visual_prompts_parser.add_argument("--no-start", action="store_true", help="Do not auto-start local backend service")
    visual_prompts_parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    visual_prompts_parser.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    visual_prompts_parser.set_defaults(func=generate_visual_prompts_command)

    generate_slides_parser = subparsers.add_parser("generate-slides", help="Start PPT slide image generation")
    generate_slides_parser.add_argument("project_id", help="PPT God project id")
    generate_slides_parser.add_argument("--page-nums", default=None, help="Optional comma-separated page numbers, such as 1,3,5")
    generate_slides_parser.add_argument("--prototype", action="store_true", help="Generate a prototype subset instead of a full batch")
    generate_slides_parser.add_argument("--tester-name", default=os.getenv("PPTGOD_TESTER_NAME", DEFAULT_TESTER_NAME), help="PPT God tester name that owns the project")
    generate_slides_parser.add_argument("--no-start", action="store_true", help="Do not auto-start local backend service")
    generate_slides_parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    generate_slides_parser.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    generate_slides_parser.set_defaults(func=generate_slides_command)

    generation_status_parser = subparsers.add_parser("get-generation-status", help="Read generation workflow status")
    generation_status_parser.add_argument("project_id", help="PPT God project id")
    generation_status_parser.add_argument("--tester-name", default=os.getenv("PPTGOD_TESTER_NAME", DEFAULT_TESTER_NAME), help="PPT God tester name that owns the project")
    generation_status_parser.add_argument("--no-start", action="store_true", help="Do not auto-start local backend service")
    generation_status_parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    generation_status_parser.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    generation_status_parser.set_defaults(func=get_generation_status_command)

    wait_parser = subparsers.add_parser("wait", help="Wait for the current or specified PPT God run to reach a terminal state")
    wait_parser.add_argument("project_id", help="PPT God project id")
    wait_parser.add_argument("--run-id", default=None, help="Optional run id returned by a start command")
    wait_parser.add_argument("--timeout", type=float, default=1800.0, help="Maximum wait time in seconds")
    wait_parser.add_argument("--interval", type=float, default=2.0, help="Polling interval in seconds")
    wait_parser.add_argument("--tester-name", default=os.getenv("PPTGOD_TESTER_NAME", DEFAULT_TESTER_NAME))
    wait_parser.add_argument("--no-start", action="store_true", help="Do not auto-start local backend service")
    wait_parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    wait_parser.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    wait_parser.set_defaults(func=wait_command)

    retry_failed_parser = subparsers.add_parser("retry-failed-slides", help="Retry failed slide image generations")
    retry_failed_parser.add_argument("project_id", help="PPT God project id")
    retry_failed_parser.add_argument("--tester-name", default=os.getenv("PPTGOD_TESTER_NAME", DEFAULT_TESTER_NAME), help="PPT God tester name that owns the project")
    retry_failed_parser.add_argument("--no-start", action="store_true", help="Do not auto-start local backend service")
    retry_failed_parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    retry_failed_parser.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    retry_failed_parser.set_defaults(func=retry_failed_slides_command)

    confirm_prototype_parser = subparsers.add_parser("confirm-prototype", help="Confirm prototype pages and start remaining full generation")
    confirm_prototype_parser.add_argument("project_id", help="PPT God project id")
    confirm_prototype_parser.add_argument("--tester-name", default=os.getenv("PPTGOD_TESTER_NAME", DEFAULT_TESTER_NAME))
    confirm_prototype_parser.add_argument("--no-start", action="store_true", help="Do not auto-start local backend service")
    confirm_prototype_parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    confirm_prototype_parser.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    confirm_prototype_parser.set_defaults(func=confirm_prototype_command)

    stop_generation_parser = subparsers.add_parser("stop-generation", help="Stop the active generation run for a project")
    stop_generation_parser.add_argument("project_id", help="PPT God project id")
    stop_generation_parser.add_argument("--tester-name", default=os.getenv("PPTGOD_TESTER_NAME", DEFAULT_TESTER_NAME))
    stop_generation_parser.add_argument("--no-start", action="store_true", help="Do not auto-start local backend service")
    stop_generation_parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    stop_generation_parser.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    stop_generation_parser.set_defaults(func=stop_generation_command)

    export_ppt_parser = subparsers.add_parser("export-ppt", help="Return the PPTX download contract for a project")
    export_ppt_parser.add_argument("project_id", help="PPT God project id")
    export_ppt_parser.add_argument("--prototype", action="store_true", help="Export prototype PPTX")
    export_ppt_parser.add_argument("--tester-name", default=os.getenv("PPTGOD_TESTER_NAME", DEFAULT_TESTER_NAME), help="PPT God tester name that owns the project")
    export_ppt_parser.add_argument("--no-start", action="store_true", help="Do not auto-start local backend service")
    export_ppt_parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    export_ppt_parser.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    export_ppt_parser.set_defaults(func=export_ppt_command)

    download_ppt_parser = subparsers.add_parser("download-ppt", help="Download a generated PPTX file")
    download_ppt_parser.add_argument("project_id", help="PPT God project id")
    download_ppt_parser.add_argument("--output", default=None, help="Output .pptx path. Defaults to the project filename.")
    download_ppt_parser.add_argument("--prototype", action="store_true", help="Download prototype PPTX")
    download_ppt_parser.add_argument("--tester-name", default=os.getenv("PPTGOD_TESTER_NAME", DEFAULT_TESTER_NAME), help="PPT God tester name that owns the project")
    download_ppt_parser.add_argument("--no-start", action="store_true", help="Do not auto-start local backend service")
    download_ppt_parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    download_ppt_parser.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    download_ppt_parser.set_defaults(func=download_ppt_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
