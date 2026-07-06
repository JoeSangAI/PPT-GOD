# AGENTS.md

## What This Is

PPT God is an Agent-driven PPT product.

It turns topics, documents, transcripts, logos, product screenshots, reference images, and revision feedback into a coherent, visually consistent, brand-accurate, editable PPT artifact.

The product standard is not "generate some slides quickly"; it is "produce a deck that is worth reviewing, editing, and delivering."

## How To Work Here

Global rules from `/Users/Joe_1/.codex/AGENTS.md` apply.

Keep user-facing copy based on the user's job-to-be-done. Do not expose internal implementation terms unless they help the user make a concrete decision.

Treat user feedback as likely feedback about the PPT artifact, not just chat. If the Agent says it will change, generate, confirm, switch, retry, or apply something, there must be a corresponding workflow action or a visible reason why no action ran.

Prefer source-level fixes over downstream cleanup. Do not solve broad product failures with one-off keyword patches, demo-specific logic, stale defaults, or hidden fallback.

Preserve project isolation. Any state that can influence prompts, briefs, content plans, visual plans, or generation inputs must be scoped to the current project.

Keep the product architecture simple. Use model intelligence for intent and judgment, and deterministic code for objective boundaries such as source metadata, page counts, empty content rejection, project scoping, and persistence.

Treat Agent integration as a workflow-contract problem, not a CLI-vs-MCP problem. PPT God core capabilities should be implemented once in backend/core services, then exposed through multiple entry points: Web GUI for visual confirmation and precise human control, CLI for local Agent/dev workflows, and MCP for standardized cross-Agent integration. CLI and MCP must stay thin adapters over the same workflow contracts; do not duplicate business logic inside either layer.

For image generation, the default direction is direct full-slide image generation. Do not introduce a default workflow that generates a background image and then programmatically types slide text on top, unless Joe explicitly asks for an overlay/paste workflow.

For retrospective or rule updates, review `MEMORY.md` before changing project rules.

## Done Means

A change is done only when the relevant user path has been verified.

For frontend or workflow changes, run the relevant frontend checks from `frontend/package.json`, such as `npm run build`, `npm run lint`, or targeted workflow/isolation/e2e scripts when the changed area requires them.

For product behavior changes, verify the actual path in the app when possible: project creation or switching, upload and source interpretation, content planning, visual direction, page generation or revision, and PPT export/download.

For bug fixes, verify the original failure path no longer fails. Do not rely only on code inspection.

For user-facing copy or PPT output changes, inspect the visible result and confirm it reads like product language, not implementation language.
