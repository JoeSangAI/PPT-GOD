import json
import subprocess
import sys


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
