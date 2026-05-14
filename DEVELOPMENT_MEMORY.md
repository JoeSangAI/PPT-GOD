# Development Memory

## Product Copy Principle

All user-facing copy must be written from the user's job-to-be-done, not from implementation details.

- Do not expose internal terms such as `Exact`, `Overlay`, `pipeline`, `drawer`, `process_mode`, or model-routing language unless the user needs that distinction to make a concrete choice.
- When a technical distinction is necessary, translate it into a user outcome first. Example: use `智能融合`, `精修融合`, and `精确粘贴`, with short explanations of what the user will see.
- Before adding UI text, ask: "What decision can the user make with this information?" If the answer is unclear, remove or simplify the text.
- Prefer location/action clarity: tell the user where to review or change something, not how the system internally stores it.
- Avoid large generic loading/empty screens when real user content already exists. On project/page switches, prefer cached real content or a small truthful loading state over fake canvas placeholders such as "preparing page".

## Agent-Driven Product Principle

PPT God is an Agent-driven PPT tool. The highest product contract is: understand the user's intent and turn it into the PPT artifact, not just a chat answer.

- Treat user feedback in project stages as likely feedback about the PPT unless it is clearly pure chat, education, or unrelated consultation.
- When the Agent says it will change, generate, confirm, switch, retry, or apply something, there must be a corresponding executable workflow action or a visible reason why no action ran.
- Do not let chat copy get ahead of system state. User-facing wording should say "正在..." before the action succeeds and only claim completion after the state actually changes.
- Stage boundaries are implementation details. If a request belongs to a later stage, acknowledge it now, carry it forward as context, and surface the next action that will apply it.

## Simplicity Principle

Recorded 2026-05-12 from user direction.

- Prefer the smallest root-cause fix that removes a whole class of failures.
- Do not add extra orchestration, queues, or recovery layers when a single source of truth or a synchronous state write solves the problem.
- Use Occam's razor in workflow/state bugs: first remove duplicated state paths and hidden async handoffs before adding new fallback behavior.

## Non-Blocking Workflow Principle

Recorded 2026-05-12 from user direction.

- Complex or uncertain processing should run in parallel or be cached ahead of the user-facing step whenever possible.
- Do not block the normal frontend experience on optional analysis, visual polish, or best-effort asset inference.
- If a refinement is not ready, use a stable local fallback and let the user continue instead of holding the workflow.

## Agent Product Execution Priorities

Recorded 2026-05-13 from user direction.

- Prioritize quality over stability over speed. Speed optimizations are welcome only when they do not weaken output quality or system reliability.
- Run independent work concurrently when possible, especially slow analysis, asset parsing, proposal generation, and background preparation, so the user waits less or does not notice the work.
- Keep the pipeline simple. Use Occam's razor before adding orchestration, queues, fallback layers, or new state paths; prefer one clear source of truth and the smallest executable workflow that solves the user problem.
- Be cautious about prompt growth. Do not use larger prompts as the default fix; prefer compact context, structured action schemas, deterministic executors, retrieval of only relevant facts, and prompt changes with clear risk control.
- UI interactions must match user intuition. Surface actions in the user's mental model, make scope/cost/result visible, and avoid exposing implementation terms unless they help the user make a concrete decision.

## Image Generation Architecture Constraint

Recorded 2026-05-10 from user direction.

- Do not propose or implement a default workflow that first generates a background image and then programmatically types the slide text on top.
- The product direction is direct full-slide image generation for slide content. Text, layout, and visual composition should remain part of the generated image unless the user explicitly asks for an overlay/paste workflow.
- Programmatic overlay remains acceptable only for explicit overlay assets such as pasted images/logos/materials, not as the general solution for slide text or section-page layout consistency.
- For consistency issues, prefer improving reference-image use, prompt contracts, seed selection, and generation strategy within the direct-generation workflow before considering any deterministic text-rendering layer.
