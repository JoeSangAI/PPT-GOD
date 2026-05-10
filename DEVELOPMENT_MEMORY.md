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

## Image Generation Architecture Constraint

Recorded 2026-05-10 from user direction.

- Do not propose or implement a default workflow that first generates a background image and then programmatically types the slide text on top.
- The product direction is direct full-slide image generation for slide content. Text, layout, and visual composition should remain part of the generated image unless the user explicitly asks for an overlay/paste workflow.
- Programmatic overlay remains acceptable only for explicit overlay assets such as pasted images/logos/materials, not as the general solution for slide text or section-page layout consistency.
- For consistency issues, prefer improving reference-image use, prompt contracts, seed selection, and generation strategy within the direct-generation workflow before considering any deterministic text-rendering layer.
