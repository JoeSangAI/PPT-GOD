#!/usr/bin/env python3
"""Audit and safely prune local PPT God runtime storage."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_ROOT = PROJECT_ROOT / ".pptgod-data"


@dataclass(frozen=True)
class PruneAction:
    path: Path
    bytes: int
    reason: str


@dataclass(frozen=True)
class OrphanAction:
    project_id: str
    source: Path
    destination: Path | None
    bytes: int
    action: str
    reason: str


def bytes_to_human(size: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)}B"
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{size}B"


def path_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total


def read_projects(db_path: Path) -> dict[str, dict[str, object]]:
    if not db_path.exists() or db_path.stat().st_size == 0:
        return {}

    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT id, title, status, created_at, updated_at
                FROM projects
                """
            ).fetchall()
    except sqlite3.DatabaseError:
        return {}
    except sqlite3.OperationalError:
        return {}

    return {
        str(row["id"]): {
            "title": row["title"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    }


def child_dir_names(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {item.name for item in path.iterdir() if item.is_dir()}


def build_audit(data_root: Path = DEFAULT_DATA_ROOT) -> dict[str, object]:
    data_root = data_root.resolve()
    uploads_root = data_root / "uploads"
    outputs_root = data_root / "outputs"
    db_path = data_root / "db" / "pptgod.db"
    db_projects = read_projects(db_path)

    project_ids = set(db_projects)
    project_ids.update(child_dir_names(uploads_root))
    project_ids.update(child_dir_names(outputs_root))

    projects: dict[str, dict[str, object]] = {}
    total_upload_bytes = 0
    total_output_bytes = 0

    for project_id in sorted(project_ids):
        upload_dir = uploads_root / project_id
        output_dir = outputs_root / project_id
        upload_bytes = path_size(upload_dir)
        output_bytes = path_size(output_dir)
        total_upload_bytes += upload_bytes
        total_output_bytes += output_bytes
        metadata = db_projects.get(project_id, {})

        projects[project_id] = {
            "project_id": project_id,
            "title": metadata.get("title"),
            "status": metadata.get("status"),
            "created_at": metadata.get("created_at"),
            "updated_at": metadata.get("updated_at"),
            "in_database": project_id in db_projects,
            "has_uploads": upload_dir.exists(),
            "has_outputs": output_dir.exists(),
            "has_final_presentation": (output_dir / "presentation.pptx").exists(),
            "upload_bytes": upload_bytes,
            "output_bytes": output_bytes,
            "total_bytes": upload_bytes + output_bytes,
        }

    return {
        "data_root": str(data_root),
        "totals": {
            "project_count": len(projects),
            "upload_bytes": total_upload_bytes,
            "output_bytes": total_output_bytes,
            "total_bytes": total_upload_bytes + total_output_bytes,
        },
        "projects": projects,
    }


def latest_mtime(path: Path) -> float:
    if not path.exists():
        return 0.0
    latest = path.stat().st_mtime
    if path.is_dir():
        for item in path.rglob("*"):
            if item.exists():
                latest = max(latest, item.stat().st_mtime)
    return latest


def collect_orphan_projects(
    audit: dict[str, object],
    project_ids: set[str] | None = None,
    require_final: bool | None = None,
) -> list[dict[str, object]]:
    projects = []
    data_root = Path(str(audit["data_root"]))
    for project in audit["projects"].values():
        project_id = str(project["project_id"])
        if project["in_database"]:
            continue
        if project_ids is not None and project_id not in project_ids:
            continue
        if require_final is not None and bool(project["has_final_presentation"]) != require_final:
            continue

        upload_dir = data_root / "uploads" / project_id
        output_dir = data_root / "outputs" / project_id
        enriched = dict(project)
        enriched["latest_mtime"] = max(latest_mtime(upload_dir), latest_mtime(output_dir))
        projects.append(enriched)

    projects.sort(key=lambda item: (int(item["total_bytes"]), str(item["project_id"])), reverse=True)
    return projects


def default_archive_root(data_root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return data_root / "archive" / stamp


def collect_orphan_actions(
    data_root: Path,
    orphan_projects: Iterable[dict[str, object]],
    action: str,
    archive_root: Path | None = None,
) -> list[OrphanAction]:
    if action not in {"archive", "delete"}:
        raise ValueError(f"unsupported orphan action: {action}")

    data_root = data_root.resolve()
    archive_root = (archive_root or default_archive_root(data_root)).resolve()
    actions: list[OrphanAction] = []

    for project in orphan_projects:
        project_id = str(project["project_id"])
        for bucket in ("uploads", "outputs"):
            source = data_root / bucket / project_id
            if not source.exists():
                continue
            size = path_size(source)
            destination = archive_root / bucket / project_id if action == "archive" else None
            actions.append(
                OrphanAction(
                    project_id=project_id,
                    source=source.resolve(),
                    destination=destination,
                    bytes=size,
                    action=action,
                    reason="orphan project not present in active database",
                )
            )
    return actions


def apply_orphan_actions(actions: Iterable[OrphanAction], apply: bool = False) -> dict[str, int]:
    affected_projects: set[str] = set()
    affected_bytes = 0
    for action in actions:
        if not action.source.exists():
            continue
        affected_projects.add(action.project_id)
        affected_bytes += action.bytes
        if not apply:
            continue
        if action.action == "delete":
            if action.source.is_dir():
                shutil.rmtree(action.source)
            else:
                action.source.unlink()
        elif action.action == "archive":
            if action.destination is None:
                raise ValueError("archive action requires a destination")
            action.destination.parent.mkdir(parents=True, exist_ok=True)
            if action.destination.exists():
                raise FileExistsError(f"archive destination already exists: {action.destination}")
            shutil.move(str(action.source), str(action.destination))
        else:
            raise ValueError(f"unsupported orphan action: {action.action}")

    return {
        "affected_projects": len(affected_projects) if apply else 0,
        "affected_bytes": affected_bytes if apply else 0,
    }


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def add_existing_action(actions: list[PruneAction], path: Path, reason: str):
    if path.exists():
        actions.append(PruneAction(path=path, bytes=path_size(path), reason=reason))


def iter_pycache_dirs(project_root: Path) -> Iterable[Path]:
    excluded = {".git", "node_modules", "venv", ".env.venv"}
    for path in project_root.rglob("__pycache__"):
        if any(part in excluded for part in path.parts):
            continue
        if path.is_dir():
            yield path


def collect_prune_actions(
    project_root: Path = PROJECT_ROOT,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> list[PruneAction]:
    project_root = project_root.resolve()
    data_root = data_root.resolve()
    outputs_root = data_root / "outputs"
    actions: list[PruneAction] = []

    safe_project_dirs = [
        project_root / "output",
        project_root / "outputs",
        project_root / ".playwright-cli",
        project_root / ".pytest_cache",
        project_root / "backend" / ".pytest_cache",
        project_root / "frontend" / ".pytest_cache",
        project_root / "frontend" / ".playwright-cli",
        project_root / "frontend" / "dist",
        project_root / "frontend" / "node_modules" / ".vite",
    ]
    for path in safe_project_dirs:
        add_existing_action(actions, path, "local generated cache/output")

    for path in iter_pycache_dirs(project_root):
        add_existing_action(actions, path, "python bytecode cache")

    for path in [
        outputs_root / "prompt-log-replay",
        outputs_root / "experiments",
        outputs_root / "image-generation-logs",
        outputs_root / "_legacy-root-outputs",
    ]:
        add_existing_action(actions, path, "runtime experiment/log output")

    if outputs_root.exists():
        for path in outputs_root.glob("live-finetune-region-test-*"):
            add_existing_action(actions, path, "runtime experiment output")

        for path in outputs_root.rglob("partial_presentation.pptx"):
            final_path = path.parent / "presentation.pptx"
            if final_path.exists():
                add_existing_action(actions, path, "partial output superseded by presentation.pptx")

    safe_actions = []
    seen: set[Path] = set()
    for action in actions:
        resolved = action.path.resolve()
        if resolved in seen:
            continue
        if is_relative_to(resolved, project_root) or is_relative_to(resolved, data_root):
            safe_actions.append(PruneAction(path=resolved, bytes=action.bytes, reason=action.reason))
            seen.add(resolved)
    return sorted(safe_actions, key=lambda item: str(item.path))


def apply_prune_actions(actions: Iterable[PruneAction], apply: bool = False) -> dict[str, int]:
    deleted_count = 0
    deleted_bytes = 0
    for action in actions:
        if not action.path.exists():
            continue
        if apply:
            if action.path.is_dir():
                shutil.rmtree(action.path)
            else:
                action.path.unlink()
            deleted_count += 1
            deleted_bytes += action.bytes
    return {
        "deleted_count": deleted_count,
        "deleted_bytes": deleted_bytes,
    }


def format_audit_table(audit: dict[str, object], limit: int = 30) -> str:
    projects = list(audit["projects"].values())
    projects.sort(key=lambda item: int(item["total_bytes"]), reverse=True)
    rows = projects[:limit]

    lines = [
        f"Data root: {audit['data_root']}",
        (
            "Projects: {project_count} | Uploads: {uploads} | Outputs: {outputs} | Total: {total}"
        ).format(
            project_count=audit["totals"]["project_count"],
            uploads=bytes_to_human(int(audit["totals"]["upload_bytes"])),
            outputs=bytes_to_human(int(audit["totals"]["output_bytes"])),
            total=bytes_to_human(int(audit["totals"]["total_bytes"])),
        ),
        "",
        "total     uploads   outputs   final  db  project_id                            title",
        "--------  --------  --------  -----  --  ------------------------------------  ----------------",
    ]

    for row in rows:
        title = str(row["title"] or "")
        if len(title) > 48:
            title = title[:45] + "..."
        lines.append(
            "{total:<8}  {uploads:<8}  {outputs:<8}  {final:<5}  {db:<2}  {project_id:<36}  {title}".format(
                total=bytes_to_human(int(row["total_bytes"])),
                uploads=bytes_to_human(int(row["upload_bytes"])),
                outputs=bytes_to_human(int(row["output_bytes"])),
                final="yes" if row["has_final_presentation"] else "no",
                db="yes" if row["in_database"] else "no",
                project_id=row["project_id"],
                title=title,
            )
        )

    if len(projects) > limit:
        lines.append(f"... {len(projects) - limit} more projects not shown")
    return "\n".join(lines)


def format_prune_actions(project_root: Path, actions: list[PruneAction], apply: bool) -> str:
    mode = "APPLY" if apply else "DRY RUN"
    total_bytes = sum(action.bytes for action in actions)
    lines = [
        f"Mode: {mode}",
        f"Actions: {len(actions)} | Reclaimable: {bytes_to_human(total_bytes)}",
        "",
    ]
    if not actions:
        lines.append("No safe prune actions found.")
        return "\n".join(lines)

    for action in actions:
        try:
            shown_path = action.path.relative_to(project_root.resolve())
        except ValueError:
            shown_path = action.path
        lines.append(f"{bytes_to_human(action.bytes):>8}  {shown_path}  ({action.reason})")
    if not apply:
        lines.append("")
        lines.append("Run with --apply to delete these paths.")
    return "\n".join(lines)


def format_orphan_projects(orphan_projects: list[dict[str, object]], limit: int) -> str:
    shown = orphan_projects[:limit]
    total_bytes = sum(int(project["total_bytes"]) for project in orphan_projects)
    lines = [
        f"Orphan projects: {len(orphan_projects)} | Total: {bytes_to_human(total_bytes)}",
        "",
        "total     uploads   outputs   final  project_id",
        "--------  --------  --------  -----  ------------------------------------",
    ]
    for project in shown:
        lines.append(
            "{total:<8}  {uploads:<8}  {outputs:<8}  {final:<5}  {project_id}".format(
                total=bytes_to_human(int(project["total_bytes"])),
                uploads=bytes_to_human(int(project["upload_bytes"])),
                outputs=bytes_to_human(int(project["output_bytes"])),
                final="yes" if project["has_final_presentation"] else "no",
                project_id=project["project_id"],
            )
        )
    if len(orphan_projects) > limit:
        lines.append(f"... {len(orphan_projects) - limit} more orphan projects not shown")
    return "\n".join(lines)


def format_orphan_actions(data_root: Path, actions: list[OrphanAction], apply: bool, limit: int = 30) -> str:
    mode = "APPLY" if apply else "DRY RUN"
    total_bytes = sum(action.bytes for action in actions)
    project_count = len({action.project_id for action in actions})
    shown_actions = actions[:limit]
    lines = [
        f"Mode: {mode}",
        f"Projects: {project_count} | Actions: {len(actions)} | Affected: {bytes_to_human(total_bytes)}",
        "",
    ]
    if not actions:
        lines.append("No orphan actions selected.")
        return "\n".join(lines)

    for action in shown_actions:
        try:
            shown_source = action.source.relative_to(data_root.resolve())
        except ValueError:
            shown_source = action.source
        if action.destination is not None:
            try:
                shown_dest = action.destination.relative_to(data_root.resolve())
            except ValueError:
                shown_dest = action.destination
            target = f" -> {shown_dest}"
        else:
            target = ""
        lines.append(
            f"{bytes_to_human(action.bytes):>8}  {action.action:<7} {shown_source}{target}  ({action.reason})"
        )
    if len(actions) > len(shown_actions):
        lines.append(f"... {len(actions) - len(shown_actions)} more actions not shown")
    if not apply:
        lines.append("")
        lines.append("Run with --apply to perform these orphan actions.")
    return "\n".join(lines)


def validate_orphan_apply_scope(
    apply: bool,
    action: str,
    project_ids: set[str] | None,
    all_projects: bool,
):
    if not apply or action == "report":
        return
    if project_ids or all_projects:
        return
    raise SystemExit("Refusing orphan --apply without --project-id or --all.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)

    subparsers = parser.add_subparsers(dest="command", required=True)

    audit_parser = subparsers.add_parser("audit", help="Show project storage usage")
    audit_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    audit_parser.add_argument("--limit", type=int, default=30, help="Number of largest projects to show")

    prune_parser = subparsers.add_parser("prune", help="List or delete safe temporary artifacts")
    prune_parser.add_argument("--apply", action="store_true", help="Delete listed artifacts")

    orphan_parser = subparsers.add_parser("orphans", help="List, archive, or delete projects missing from the active database")
    orphan_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON for report mode")
    orphan_parser.add_argument("--limit", type=int, default=30, help="Number of largest orphan projects to show")
    orphan_parser.add_argument(
        "--action",
        choices=("report", "archive", "delete"),
        default="report",
        help="Action to prepare for orphan projects",
    )
    orphan_parser.add_argument("--apply", action="store_true", help="Apply archive/delete action")
    orphan_parser.add_argument(
        "--all",
        action="store_true",
        dest="all_projects",
        help="Allow --apply over every selected orphan project",
    )
    orphan_parser.add_argument(
        "--project-id",
        action="append",
        dest="project_ids",
        help="Restrict to a specific orphan project id; may be passed multiple times",
    )
    orphan_parser.add_argument(
        "--require-final",
        action="store_true",
        help="Only include orphan projects that have presentation.pptx",
    )
    orphan_parser.add_argument(
        "--no-final",
        action="store_true",
        help="Only include orphan projects without presentation.pptx",
    )
    orphan_parser.add_argument(
        "--archive-root",
        type=Path,
        default=None,
        help="Archive destination root; defaults to .pptgod-data/archive/<timestamp>",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "audit":
        audit = build_audit(args.data_root)
        if args.json:
            print(json.dumps(audit, ensure_ascii=False, indent=2))
        else:
            print(format_audit_table(audit, limit=args.limit))
        return 0

    if args.command == "prune":
        actions = collect_prune_actions(project_root=args.project_root, data_root=args.data_root)
        print(format_prune_actions(args.project_root, actions, apply=args.apply))
        result = apply_prune_actions(actions, apply=args.apply)
        if args.apply:
            print(
                "Deleted: {count} | Reclaimed: {size}".format(
                    count=result["deleted_count"],
                    size=bytes_to_human(result["deleted_bytes"]),
                )
            )
        return 0

    if args.command == "orphans":
        if args.require_final and args.no_final:
            raise SystemExit("--require-final and --no-final cannot be used together")
        require_final = True if args.require_final else False if args.no_final else None
        project_ids = set(args.project_ids) if args.project_ids else None
        validate_orphan_apply_scope(
            apply=args.apply,
            action=args.action,
            project_ids=project_ids,
            all_projects=args.all_projects,
        )
        audit = build_audit(args.data_root)
        orphan_projects = collect_orphan_projects(
            audit,
            project_ids=project_ids,
            require_final=require_final,
        )
        if args.action == "report":
            if args.json:
                print(json.dumps({"projects": orphan_projects}, ensure_ascii=False, indent=2))
            else:
                print(format_orphan_projects(orphan_projects, limit=args.limit))
            return 0

        actions = collect_orphan_actions(
            data_root=args.data_root,
            orphan_projects=orphan_projects,
            action=args.action,
            archive_root=args.archive_root,
        )
        print(format_orphan_actions(args.data_root, actions, apply=args.apply, limit=args.limit))
        result = apply_orphan_actions(actions, apply=args.apply)
        if args.apply:
            print(
                "Affected projects: {count} | Affected bytes: {size}".format(
                    count=result["affected_projects"],
                    size=bytes_to_human(result["affected_bytes"]),
                )
            )
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
