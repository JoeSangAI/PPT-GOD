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
3. If bypassing PPT God's internal content Agent, write a strict PPT God content-plan Markdown file under `/Users/Joe_1/Desktop/AI output/YYYY-MM-DD/<task-name>/`.
4. Validate the content plan before importing it:

```bash
python /Users/Joe_1/Desktop/Development/ppt-god/scripts/pptgod_cli.py validate-content-plan <plan.md>
```

5. Import the plan and open the Web UI when confirmation is useful:

```bash
python /Users/Joe_1/Desktop/Development/ppt-god/scripts/pptgod_cli.py import-content-plan <plan.md> --title "<title>" --open
```

6. Save the returned `project_id`. Use it for later operations.
7. Use the Web UI at visual or review-heavy stages instead of forcing every choice through chat.

## CLI Operations

Common commands:

- `status <project_id>`
- `open <project_id> --stage content|visual|review`
- `export-content-plan <project_id> --output <path>`
- `confirm-content-plan <project_id>`
- `start-visual-proposals <project_id> --user-description "..."`
- `get-visual-proposals <project_id>`
- `confirm-visual-proposal <project_id> --index N`
- `generate-visual-prompts <project_id> --page-nums "1,2"`
- `generate-slides <project_id> --page-nums "1,2" --prototype`
- `get-generation-status <project_id>`
- `retry-failed-slides <project_id>`
- `export-ppt <project_id>`
- `download-ppt <project_id> --output <path>`

Run `python /Users/Joe_1/Desktop/Development/ppt-god/scripts/pptgod_cli.py <command> --help` before using an unfamiliar command.

## Content Contract

When bypassing PPT God's internal content Agent, the output must still satisfy PPT God's strict content-plan Markdown format. The format is strict, but the field content can be rich. Validate before import; if validation fails, fix the Markdown and validate again.

Use PPT God's methodology, examples, and validation rules as guidance, not as a hard ceiling on user intent. If the user's high-context direction is clear and the final fields remain valid, prefer the user's intent.

## Handoff

When reporting back, include the `project_id`, current stage, UI URL if available, and final output path when a PPTX is exported. Explain only what the user needs next; do not expose internal CLI details unless debugging is needed.
