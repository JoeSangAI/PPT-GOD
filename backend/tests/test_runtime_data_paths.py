from pathlib import Path

from app.core.config import Settings


def test_default_runtime_data_paths_are_outside_code_tree(monkeypatch):
    for key in ("RUNTIME_DATA_DIR", "UPLOAD_DIR", "OUTPUT_DIR", "DATABASE_URL", "IMAGE_GEN_CACHE_DIR"):
        monkeypatch.delenv(key, raising=False)

    settings = Settings(_env_file=None)
    project_root = Path(__file__).resolve().parents[2]
    data_root = project_root / ".pptgod-data"

    assert Path(settings.RUNTIME_DATA_DIR) == data_root
    assert Path(settings.UPLOAD_DIR) == data_root / "uploads"
    assert Path(settings.OUTPUT_DIR) == data_root / "outputs"
    assert Path(settings.IMAGE_GEN_CACHE_DIR) == data_root / "outputs" / "image-cache"
    assert settings.DATABASE_URL == f"sqlite:///{data_root / 'db' / 'pptgod.db'}"
