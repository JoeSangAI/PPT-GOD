import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest


def load_storage_maintenance():
    project_root = Path(__file__).resolve().parents[2]
    module_path = project_root / "scripts" / "storage_maintenance.py"
    spec = importlib.util.spec_from_file_location("storage_maintenance", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_file(path: Path, size: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def create_project_db(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                title TEXT,
                status TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO projects (id, title, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("project-1", "Client deck", "completed", "2026-06-01T09:00:00", "2026-06-02T10:00:00"),
        )


def test_audit_combines_database_metadata_and_project_storage_sizes(tmp_path):
    storage = load_storage_maintenance()
    data_root = tmp_path / ".pptgod-data"
    create_project_db(data_root / "db" / "pptgod.db")
    write_file(data_root / "uploads" / "project-1" / "docs" / "source.pptx", 11)
    write_file(data_root / "outputs" / "project-1" / "presentation.pptx", 13)
    write_file(data_root / "outputs" / "orphan-output" / "prototype.pptx", 17)

    audit = storage.build_audit(data_root)

    assert audit["totals"]["upload_bytes"] == 11
    assert audit["totals"]["output_bytes"] == 30
    assert audit["totals"]["project_count"] == 2

    project = audit["projects"]["project-1"]
    assert project["title"] == "Client deck"
    assert project["status"] == "completed"
    assert project["in_database"] is True
    assert project["upload_bytes"] == 11
    assert project["output_bytes"] == 13
    assert project["has_final_presentation"] is True
    assert project["updated_at"] == "2026-06-02T10:00:00"

    orphan = audit["projects"]["orphan-output"]
    assert orphan["title"] is None
    assert orphan["in_database"] is False
    assert orphan["upload_bytes"] == 0
    assert orphan["output_bytes"] == 17


def test_prune_actions_are_safe_and_apply_only_when_requested(tmp_path):
    storage = load_storage_maintenance()
    project_root = tmp_path / "repo"
    data_root = project_root / ".pptgod-data"
    write_file(data_root / "outputs" / "project-1" / "presentation.pptx", 5)
    write_file(data_root / "outputs" / "project-1" / "partial_presentation.pptx", 7)
    write_file(data_root / "outputs" / "project-2" / "partial_presentation.pptx", 9)
    write_file(data_root / "outputs" / "experiments" / "run" / "summary.json", 3)
    write_file(data_root / "outputs" / "image-generation-logs" / "project-1" / "run.jsonl", 4)
    write_file(project_root / "output" / "playwright" / "home.png", 6)
    write_file(project_root / ".pytest_cache" / "v" / "cache" / "nodeids", 2)

    actions = storage.collect_prune_actions(project_root=project_root, data_root=data_root)
    action_paths = {action.path.relative_to(project_root).as_posix() for action in actions}

    assert ".pptgod-data/outputs/project-1/partial_presentation.pptx" in action_paths
    assert ".pptgod-data/outputs/experiments" in action_paths
    assert ".pptgod-data/outputs/image-generation-logs" in action_paths
    assert "output" in action_paths
    assert ".pytest_cache" in action_paths
    assert ".pptgod-data/outputs/project-2/partial_presentation.pptx" not in action_paths

    dry_run = storage.apply_prune_actions(actions, apply=False)
    assert dry_run["deleted_count"] == 0
    assert (data_root / "outputs" / "project-1" / "partial_presentation.pptx").exists()
    assert (data_root / "outputs" / "project-2" / "partial_presentation.pptx").exists()

    applied = storage.apply_prune_actions(actions, apply=True)
    assert applied["deleted_count"] == len(actions)
    assert not (data_root / "outputs" / "project-1" / "partial_presentation.pptx").exists()
    assert not (data_root / "outputs" / "experiments").exists()
    assert not (project_root / "output").exists()
    assert not (project_root / ".pytest_cache").exists()
    assert (data_root / "outputs" / "project-2" / "partial_presentation.pptx").exists()


def test_orphan_projects_exclude_database_projects_and_support_dry_run(tmp_path):
    storage = load_storage_maintenance()
    data_root = tmp_path / ".pptgod-data"
    create_project_db(data_root / "db" / "pptgod.db")
    write_file(data_root / "uploads" / "project-1" / "docs" / "source.pptx", 11)
    write_file(data_root / "outputs" / "project-1" / "presentation.pptx", 13)
    write_file(data_root / "uploads" / "orphan-a" / "docs" / "source.pptx", 17)
    write_file(data_root / "outputs" / "orphan-a" / "presentation.pptx", 19)
    write_file(data_root / "outputs" / "orphan-b" / "slide_01.png", 23)

    audit = storage.build_audit(data_root)
    orphans = storage.collect_orphan_projects(audit)

    assert [project["project_id"] for project in orphans] == ["orphan-a", "orphan-b"]
    assert orphans[0]["total_bytes"] == 36
    assert orphans[0]["has_final_presentation"] is True
    assert orphans[1]["total_bytes"] == 23
    assert orphans[1]["has_final_presentation"] is False

    actions = storage.collect_orphan_actions(data_root=data_root, orphan_projects=orphans, action="archive")
    result = storage.apply_orphan_actions(actions, apply=False)

    assert result["affected_projects"] == 0
    assert (data_root / "uploads" / "orphan-a").exists()
    assert (data_root / "outputs" / "orphan-a").exists()
    assert (data_root / "uploads" / "project-1").exists()
    assert (data_root / "outputs" / "project-1").exists()


def test_orphan_archive_moves_uploads_and_outputs_into_archive(tmp_path):
    storage = load_storage_maintenance()
    data_root = tmp_path / ".pptgod-data"
    write_file(data_root / "uploads" / "orphan-a" / "docs" / "source.pptx", 17)
    write_file(data_root / "outputs" / "orphan-a" / "presentation.pptx", 19)
    archive_root = data_root / "archive" / "20260623-120000"

    audit = storage.build_audit(data_root)
    orphans = storage.collect_orphan_projects(audit)
    actions = storage.collect_orphan_actions(
        data_root=data_root,
        orphan_projects=orphans,
        action="archive",
        archive_root=archive_root,
    )

    result = storage.apply_orphan_actions(actions, apply=True)

    assert result["affected_projects"] == 1
    assert result["affected_bytes"] == 36
    assert not (data_root / "uploads" / "orphan-a").exists()
    assert not (data_root / "outputs" / "orphan-a").exists()
    assert (archive_root / "uploads" / "orphan-a" / "docs" / "source.pptx").exists()
    assert (archive_root / "outputs" / "orphan-a" / "presentation.pptx").exists()


def test_orphan_delete_removes_only_selected_orphan_project(tmp_path):
    storage = load_storage_maintenance()
    data_root = tmp_path / ".pptgod-data"
    create_project_db(data_root / "db" / "pptgod.db")
    write_file(data_root / "uploads" / "project-1" / "docs" / "source.pptx", 11)
    write_file(data_root / "outputs" / "project-1" / "presentation.pptx", 13)
    write_file(data_root / "uploads" / "orphan-a" / "docs" / "source.pptx", 17)
    write_file(data_root / "outputs" / "orphan-a" / "presentation.pptx", 19)
    write_file(data_root / "uploads" / "orphan-b" / "docs" / "source.pptx", 23)

    audit = storage.build_audit(data_root)
    orphans = storage.collect_orphan_projects(audit, project_ids={"orphan-a"})
    actions = storage.collect_orphan_actions(data_root=data_root, orphan_projects=orphans, action="delete")

    result = storage.apply_orphan_actions(actions, apply=True)

    assert result["affected_projects"] == 1
    assert result["affected_bytes"] == 36
    assert not (data_root / "uploads" / "orphan-a").exists()
    assert not (data_root / "outputs" / "orphan-a").exists()
    assert (data_root / "uploads" / "orphan-b").exists()
    assert (data_root / "uploads" / "project-1").exists()
    assert (data_root / "outputs" / "project-1").exists()


def test_orphan_action_output_can_be_limited_without_changing_action_count(tmp_path):
    storage = load_storage_maintenance()
    data_root = tmp_path / ".pptgod-data"
    write_file(data_root / "uploads" / "orphan-a" / "docs" / "source.pptx", 17)
    write_file(data_root / "outputs" / "orphan-a" / "presentation.pptx", 19)
    write_file(data_root / "uploads" / "orphan-b" / "docs" / "source.pptx", 23)
    audit = storage.build_audit(data_root)
    actions = storage.collect_orphan_actions(
        data_root=data_root,
        orphan_projects=storage.collect_orphan_projects(audit),
        action="delete",
    )

    output = storage.format_orphan_actions(data_root, actions, apply=False, limit=1)

    assert "Actions: 3" in output
    assert "uploads/orphan-a" in output
    assert "uploads/orphan-b" not in output
    assert "outputs/orphan-a" not in output
    assert "... 2 more actions not shown" in output


def test_orphan_apply_requires_project_id_or_all_scope():
    storage = load_storage_maintenance()

    with pytest.raises(SystemExit):
        storage.validate_orphan_apply_scope(
            apply=True,
            action="delete",
            project_ids=None,
            all_projects=False,
        )

    storage.validate_orphan_apply_scope(
        apply=True,
        action="delete",
        project_ids={"orphan-a"},
        all_projects=False,
    )
    storage.validate_orphan_apply_scope(
        apply=True,
        action="archive",
        project_ids=None,
        all_projects=True,
    )
