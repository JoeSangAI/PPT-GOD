from pathlib import Path


def test_memory_records_compression_into_project_agents():
    memory = Path(__file__).resolve().parents[2] / "MEMORY.md"
    text = memory.read_text(encoding="utf-8")

    assert "Compressed the previous long project memory into `AGENTS.md`" in text
    assert "Product architecture taste was promoted to `AGENTS.md`" in text
    assert "Source-level prevention, fallback quality, and case-driven robustness" in text
    assert "Image generation architecture constraints were promoted to `AGENTS.md`" in text
