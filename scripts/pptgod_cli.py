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
import urllib.request
import webbrowser


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
FRONTEND_DIR = PROJECT_ROOT / "frontend"
LOG_DIR = PROJECT_ROOT / ".pptgod-data" / "agent-cli"
DEFAULT_TESTER_NAME = "Codex Local"

sys.path.insert(0, str(BACKEND_DIR))

from app.services.content_plan_markdown import validate_content_plan_markdown  # noqa: E402


def _read_markdown(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


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
            return int(response.status), json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            payload = {"detail": body}
        return int(exc.code), payload


def _tester_login(backend_url: str, tester_name: str) -> str:
    status, response = _post_json(
        backend_url.rstrip("/") + "/auth/tester-login",
        {"display_name": tester_name, "passcode": ""},
    )
    if not (200 <= status < 300) or not response.get("tester_id"):
        raise RuntimeError(f"测试账号登录失败：{response}")
    return str(response["tester_id"])


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PPT God local Agent CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

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
    import_parser.add_argument("--frontend-url", default="http://localhost:5173")
    import_parser.set_defaults(func=import_content_plan_command)
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
