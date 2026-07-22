from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = PROJECT_ROOT / "打开 PPT GOD.command"


def test_local_launcher_checks_the_runtime_contract_before_reusing_port_8000():
    script = LAUNCHER.read_text(encoding="utf-8")

    subprocess.run(["bash", "-n", str(LAUNCHER)], check=True)
    assert "/agent/readiness" in script
    assert "source_fingerprint" in script
    assert "runtime_matches_source" in script
    assert 'tell application "Google Chrome"' in script
    assert 'open "$launch_url"' in script
    assert script.index("if current_service_ready; then") < script.index(
        'if curl -fsS --max-time 5 "${BASE_URL}/health"'
    )
    assert "stop_stale_local_pptgod" in script


def test_local_launcher_is_not_excluded_from_the_open_source_repository():
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "!打开 PPT GOD.command" in gitignore
