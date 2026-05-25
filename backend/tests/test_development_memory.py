from pathlib import Path


def test_development_memory_records_product_architecture_taste():
    memory = Path(__file__).resolve().parents[2] / "DEVELOPMENT_MEMORY.md"
    text = memory.read_text(encoding="utf-8")

    assert "Product Architecture Taste" in text
    assert "Prefer one elegant underlying principle" in text
    assert "Be skeptical of fallback" in text
    assert "The highest bar is not" in text
