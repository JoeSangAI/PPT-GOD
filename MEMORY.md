# MEMORY.md

This file stores useful PPT God lessons that are not yet mature enough for `AGENTS.md`.

Use `AGENTS.md` for current project rules. Use this file only during retrospective, rule updates, or experience compression.

## Active Notes

- 2026-07-03: Agent-driven products should not default to pure language control. Agent is strongest at high-context intent understanding and orchestration; GUI is strongest at high-frequency, broad-scope, multimodal alignment and precise confirmation. PPT God should support full Agent control, but expose contextual UI checkpoints when content, visual direction, page layout, assets, or generation quality need user alignment.
- 2026-07-19: Visual planning must preserve rich page context. Truncating a list to its first few items can silently remove the evidence the visual planner is expected to represent. Speaker notes are useful non-visible context for scenes and cases, but must stay separate from exact on-slide copy.
- 2026-07-19: Visual form should follow semantic relationship. A process, cycle, hierarchy, or containment diagram is not a harmless layout choice; it asserts a relationship. The planner should make the primary relationship explicit and carry anti-misreading constraints into generation.
- 2026-07-19: Revision quality depends on an explicit change budget: preserve approved elements when the user asks to fix one problem, and widen the redesign only when requested. Existing local edit, history, and rollback cover much of the protection; do not create another versioning system before validating the interaction need.
- 2026-07-19: Distinguish product failures from Agent orchestration failures. Manually overriding visual-language or seed-family metadata can create style drift that should not be “fixed” by adding a global visual prohibition.

## Processed

- 2026-07-02: Compressed the previous long project memory into `AGENTS.md` and standardized the project memory file as `MEMORY.md`.
- Product copy principles were promoted to `AGENTS.md`: user-facing copy should be based on the user's job-to-be-done, not implementation details.
- Agent-driven product principles were promoted to `AGENTS.md`: user feedback should map to visible PPT artifact changes or a clear reason no workflow action ran.
- Product architecture taste was promoted to `AGENTS.md`: prefer one elegant underlying principle, simple pipelines, model judgment for intent, and deterministic code for objective boundaries.
- Source-level prevention, fallback quality, and case-driven robustness were promoted to `AGENTS.md`: fix root causes, avoid hidden fallback, and avoid one-off keyword patches or demo-specific logic.
- Project isolation was promoted to `AGENTS.md`: state that can influence prompts, briefs, content plans, visual plans, or generation inputs must be scoped to the current project.
- Image generation architecture constraints were promoted to `AGENTS.md`: the default direction is direct full-slide image generation, not background generation plus programmatic text overlay.
