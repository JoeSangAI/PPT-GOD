#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = PROJECT_ROOT / "scripts" / "pptgod_cli.py"


PLAN_MARKDOWN = """# Agent CLI Smoke Test

## P1
### 类型
cover

### 标题
Agent CLI Smoke Test

### 副标题
Contract verification

### 正文
This page verifies that the Agent CLI can hand off a strict content plan into PPT God.

### 备注
The smoke test checks contract shape and safe state transitions.

## P2
### 类型
content

### 标题
Workflow contract first

### 副标题
Adapters stay thin

### 正文
- Core capability stays in PPT God services.
- CLI calls the Agent contract.
- MCP can later reuse the same contract.

### 备注
This slide keeps the test deck small but realistic.
"""


STYLE_JSON = {
    "name": "Agent Smoke Style",
    "palette": ["#111827", "#2563EB", "#F8FAFC"],
    "mood": "clean technical presentation",
    "description": "A quiet, high-contrast technology presentation style for contract smoke testing.",
}


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _load_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"ok": False, "error": "non_json_stdout", "stdout": text[:1000]}


def classify_result(
    name: str,
    *,
    returncode: int,
    payload: dict,
    expected_statuses: set[int] | None = None,
) -> dict:
    expected_statuses = expected_statuses or set()
    http_status = payload.get("status")
    if http_status is None and isinstance(payload.get("response"), dict):
        http_status = payload["response"].get("status")

    if returncode == 0 and payload.get("ok", True) is not False:
        outcome = "passed"
        ok = True
    elif http_status in expected_statuses:
        outcome = "expected_failure"
        ok = True
    else:
        outcome = "failed"
        ok = False

    return {
        "name": name,
        "ok": ok,
        "outcome": outcome,
        "returncode": returncode,
        "http_status": http_status,
    }


class SmokeRunner:
    def __init__(self, *, backend_url: str, frontend_url: str, tester_name: str, keep_temp: bool = False):
        self.backend_url = backend_url
        self.frontend_url = frontend_url
        self.tester_name = tester_name
        self.keep_temp = keep_temp
        self.project_id: str | None = None
        self.steps: list[dict] = []
        self._tempdir_obj = tempfile.TemporaryDirectory(prefix="pptgod-agent-cli-smoke-")
        self.tempdir = Path(self._tempdir_obj.name)
        self.plan_path = self.tempdir / "content-plan.md"
        self.exported_plan_path = self.tempdir / "exported-content-plan.md"
        self.style_path = self.tempdir / "style.json"

    def close(self) -> None:
        if self.keep_temp:
            return
        self._tempdir_obj.cleanup()

    def write_inputs(self) -> None:
        self.plan_path.write_text(PLAN_MARKDOWN, encoding="utf-8")
        self.style_path.write_text(json.dumps(STYLE_JSON, ensure_ascii=False, indent=2), encoding="utf-8")

    def _base_args(self) -> list[str]:
        return [
            "--backend-url",
            self.backend_url,
            "--frontend-url",
            self.frontend_url,
            "--tester-name",
            self.tester_name,
        ]

    def run_cli(
        self,
        name: str,
        args: list[str],
        *,
        expected_statuses: set[int] | None = None,
        include_base_args: bool = True,
    ) -> dict:
        command = [sys.executable, str(CLI_PATH), *args]
        if include_base_args:
            command.extend(self._base_args())
        started = time.time()
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        payload = _load_json(completed.stdout)
        result = classify_result(
            name,
            returncode=completed.returncode,
            payload=payload,
            expected_statuses=expected_statuses,
        )
        result.update(
            {
                "duration_ms": round((time.time() - started) * 1000),
                "command": [Path(command[0]).name, *command[1:]],
                "payload": payload,
            }
        )
        if completed.stderr.strip():
            result["stderr"] = completed.stderr.strip()[:1000]
        self.steps.append(result)
        return result

    def run_help(self, command_name: str) -> None:
        completed = subprocess.run(
            [sys.executable, str(CLI_PATH), command_name, "--help"],
            cwd=str(PROJECT_ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        self.steps.append(
            {
                "name": f"{command_name} --help",
                "ok": completed.returncode == 0,
                "outcome": "passed" if completed.returncode == 0 else "failed",
                "returncode": completed.returncode,
            }
        )

    def run(self) -> dict:
        self.write_inputs()

        self.run_cli("validate-content-plan", ["validate-content-plan", str(self.plan_path)], include_base_args=False)
        imported = self.run_cli(
            "import-content-plan",
            ["import-content-plan", str(self.plan_path), "--title", "Agent CLI Smoke Test"],
        )
        self.project_id = str(imported.get("payload", {}).get("project_id") or "")
        if not self.project_id:
            return self.report()

        self.run_cli("status", ["status", self.project_id])
        self.run_cli("export-content-plan", ["export-content-plan", self.project_id, "--output", str(self.exported_plan_path)])
        self.run_cli("validate-exported-content-plan", ["validate-content-plan", str(self.exported_plan_path)], include_base_args=False)
        self.run_cli("confirm-content-plan", ["confirm-content-plan", self.project_id])
        self.run_cli("get-visual-proposals", ["get-visual-proposals", self.project_id])
        self.run_cli(
            "confirm-visual-proposal",
            ["confirm-visual-proposal", self.project_id, "--style-json", str(self.style_path)],
        )
        self.run_cli("status-after-style", ["status", self.project_id])

        self.run_help("generate-visual-prompts")
        self.steps.append(
            {
                "name": "generate-visual-prompts",
                "ok": True,
                "outcome": "skipped",
                "reason": "Would start model-backed background visual prompt generation; covered by unit contract tests.",
            }
        )

        self.run_cli("generate-slides-without-prompts", ["generate-slides", self.project_id], expected_statuses={400, 503})
        self.run_cli("get-generation-status", ["get-generation-status", self.project_id])
        self.run_cli("retry-failed-slides-without-failures", ["retry-failed-slides", self.project_id], expected_statuses={400, 503})
        self.run_cli("export-ppt", ["export-ppt", self.project_id])
        self.run_cli("download-ppt-without-generated-pages", ["download-ppt", self.project_id, "--output", str(self.tempdir / "deck.pptx")], expected_statuses={404})
        return self.report()

    def report(self) -> dict:
        failed = [step for step in self.steps if not step.get("ok")]
        return {
            "ok": not failed,
            "project_id": self.project_id,
            "tempdir": str(self.tempdir),
            "steps_count": len(self.steps),
            "failed_count": len(failed),
            "steps": self.steps,
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a safe smoke test across PPT God Agent CLI contracts")
    parser.add_argument("--backend-url", default="http://127.0.0.1:8001")
    parser.add_argument("--frontend-url", default="http://localhost:5173")
    parser.add_argument("--tester-name", default="Codex Smoke")
    parser.add_argument("--keep-temp", action="store_true", help="Keep temporary Markdown/JSON/PPTX files for inspection")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runner = SmokeRunner(
        backend_url=args.backend_url,
        frontend_url=args.frontend_url,
        tester_name=args.tester_name,
        keep_temp=args.keep_temp,
    )
    try:
        report = runner.run()
        _print_json(report)
        return 0 if report["ok"] else 1
    finally:
        runner.close()


if __name__ == "__main__":
    raise SystemExit(main())
