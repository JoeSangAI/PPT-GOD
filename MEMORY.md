# MEMORY.md

This file stores useful PPT God lessons that are not yet mature enough for `AGENTS.md`.

Use `AGENTS.md` for current project rules. Use this file only during retrospective, rule updates, or experience compression.

## Active Notes

- 2026-07-03: Agent-driven products should not default to pure language control. Agent is strongest at high-context intent understanding and orchestration; GUI is strongest at high-frequency, broad-scope, multimodal alignment and precise confirmation. PPT God should support full Agent control, but expose contextual UI checkpoints when content, visual direction, page layout, assets, or generation quality need user alignment.

## Processed

- 2026-07-02: Compressed the previous long project memory into `AGENTS.md` and standardized the project memory file as `MEMORY.md`.
- Product copy principles were promoted to `AGENTS.md`: user-facing copy should be based on the user's job-to-be-done, not implementation details.
- Agent-driven product principles were promoted to `AGENTS.md`: user feedback should map to visible PPT artifact changes or a clear reason no workflow action ran.
- Product architecture taste was promoted to `AGENTS.md`: prefer one elegant underlying principle, simple pipelines, model judgment for intent, and deterministic code for objective boundaries.
- Source-level prevention, fallback quality, and case-driven robustness were promoted to `AGENTS.md`: fix root causes, avoid hidden fallback, and avoid one-off keyword patches or demo-specific logic.
- Project isolation was promoted to `AGENTS.md`: state that can influence prompts, briefs, content plans, visual plans, or generation inputs must be scoped to the current project.
- Image generation architecture constraints were promoted to `AGENTS.md`: the default direction is direct full-slide image generation, not background generation plus programmatic text overlay.
