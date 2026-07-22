---
name: ppt-god
description: Use only when the user explicitly asks to use PPT God, says "用 PPT God", "交给 PPT God", "让 PPT God 生成/导出/打开 PPT", or asks to continue an existing PPT God project. Do not use for ordinary PPT or PowerPoint tasks unless PPT God is explicitly named.
---

# PPT God

## Overview

PPT God is the workflow engine for generating, confirming, revising, and exporting editable PPT projects. Use this skill only when the user explicitly wants PPT God; do not route normal PPT creation, editing, polishing, or analysis tasks to PPT God unless the user names PPT God.

## Core Rule

PPT God owns deterministic workflow, GUI confirmation points, visual planning, generation, and export. Codex should use the user's current context to understand intent and draft structured content, then hand that content to PPT God through the local CLI.

Use the CLI as the action adapter:

```bash
python /Users/Joe_1/Desktop/Development/ppt-god/scripts/pptgod_cli.py
```

Do not ask the user to type CLI commands. Run the commands directly when the user asks to use PPT God.

## Workflow

1. Decide whether the user has explicitly asked to use PPT God.
2. Use the current conversation, files, and project context to prepare content. Ask only when a missing choice would block a useful PPT God handoff.
3. Before the first workflow action, run `doctor --json`. Read both capability states. A capability is satisfied only when a BYOK provider is configured or the current Agent will actually produce and hand off the corresponding artifact. Merely running inside Codex, WorkBuddy, or Claude Code does not satisfy it. Use plain `doctor` only when showing a beginner the human-readable explanation.
4. If bypassing PPT God's internal content Agent, write a strict PPT God content-plan Markdown file under `/Users/Joe_1/Desktop/AI output/YYYY-MM-DD/<task-name>/`.
5. Validate the content plan before importing it:

```bash
python /Users/Joe_1/Desktop/Development/ppt-god/scripts/pptgod_cli.py validate-content-plan <plan.md>
```

6. Import the plan and open the Web UI when confirmation is useful:

```bash
python /Users/Joe_1/Desktop/Development/ppt-god/scripts/pptgod_cli.py import-content-plan <plan.md> --title "<title>" --open
```

7. For an existing project, do not import a duplicate. Preview an in-place update first:

```bash
python /Users/Joe_1/Desktop/Development/ppt-god/scripts/pptgod_cli.py update-content-plan <project_id> <plan.md>
```

Review `changed`, `added`, `deleted`, `unchanged`, and deletion warnings. Only after
the diff is correct, apply it with `--apply`; add `--open` when Web review is useful.
This command must not confirm content, change the workflow stage, or start generation.
After apply, require `readback.ok: true`; this verifies the rich-text editor's
`content_blocks` body and the Markdown body mirror agree. The integrated local UI
defaults to `http://localhost:8000`; port 5173 is only for Vite development.

8. Save the returned `project_id`. Use it for later operations.
9. Use the Web UI at visual or review-heavy stages instead of forcing every choice through chat.

Do not edit internal fields such as `seed_family` or `visual_language_group` to make
a slide look different. They are consistency metadata, not public creative controls.
Use the visual-planning, page revision, local selection, version, and rollback
workflows so the requested change remains scoped and recoverable.

## CLI Operations

Common commands:

- `doctor`
- `capabilities`
- `whoami`
- `list-projects`
- `status <project_id>`
- `open <project_id> --stage content|visual|review`
- `export-content-plan <project_id> --output <path>`
- `update-content-plan <project_id> <plan.md>` (dry-run by default)
- `update-content-plan <project_id> <plan.md> --apply --open`
- `confirm-content-plan <project_id>`
- `start-visual-proposals <project_id> --user-description "..."`
- `get-visual-proposals <project_id>`
- `confirm-visual-proposal <project_id> --index N`
- `generate-visual-prompts <project_id> --page-nums "1,2"`
- `import-visual-plan <project_id> <visual-plan.json>` (use when the current Agent provides every page's visual description and ready-to-run image prompt)
- `generate-slides <project_id> --page-nums "1,2" --prototype`
- `import-slide-image <project_id> <page_num> <path>` (use when the current Agent generated the final 16:9 page image)
- `get-generation-status <project_id>`
- `wait <project_id> --run-id <run_id>`
- `retry-failed-slides <project_id>`
- `confirm-prototype <project_id>`
- `stop-generation <project_id>`
- `export-ppt <project_id>`
- `download-ppt <project_id> --output <path>`

Run `python /Users/Joe_1/Desktop/Development/ppt-god/scripts/pptgod_cli.py <command> --help` before using an unfamiliar command.

## Content Contract

When bypassing PPT God's internal content Agent, the output must still satisfy PPT God's strict content-plan Markdown format. The format is strict, but the field content can be rich. Validate before import; if validation fails, fix the Markdown and validate again.

The public `### 类型` contract contains exactly eight semantic roles:
`cover`, `toc`, `section`, `content`, `data`, `hero`, `quote`, and `ending`.
Do not encode visual composition in this field. Values such as `content_split`,
`content_top`, `content_hero`, or `content_dense` are invalid; describe the desired
composition in the content or visual-planning stage instead. The canonical format
and role definitions live in `docs/agent/content-planning-playbook.md`.

Use PPT God's methodology, examples, and validation rules as guidance, not as a hard ceiling on user intent. If the user's high-context direction is clear and the final fields remain valid, prefer the user's intent.

## Handoff

When reporting back, include the `project_id`, current stage, UI URL if available, and final output path when a PPTX is exported. Explain only what the user needs next; do not expose internal CLI details unless debugging is needed.
