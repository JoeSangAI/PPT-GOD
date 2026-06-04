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

## Content Director Principle

Recorded 2026-05-23 from user direction after the long-manuscript content-planning incident.

- Treat a user request as a task contract, not as a bag of trigger words. For serious content work, the system must infer the user's desired fidelity, coverage, compression, depth, page-budget logic, and source-use policy before generating artifacts.
- Put user-intent understanding in an Agent-like "content director" layer that produces a structured contract. Engineering code should execute that contract and enforce stable quality boundaries; it should not grow by accumulating scenario-specific keyword branches.
- Long prompts, short prompts, long documents, books, manuscripts, PPT sources, and mixed materials should enter the same content pipeline. Differences belong in the content director contract and source profile, not in separate ad hoc pipelines.
- Use deterministic code for objective facts and safety rails: explicit page counts, source page counts, document structure, invalid output types, empty visible content, markdown leakage, project isolation, and pre-persistence contract violations.
- If a heuristic is added to stop an urgent failure, treat it as a temporary guardrail. Follow up by migrating the behavior into the content director contract and covering the generalized behavior with tests.
- Prefer model understanding plus compact structured schemas over larger prompts and more rules. The goal is for the product to behave like a capable content lead who understands the user's job, not like a page-count calculator.

## Product Architecture Taste

Recorded 2026-05-24 from user direction.

- Prefer one elegant underlying principle over separate rules for every input scale. Short prompts, long manuscripts, PDFs, books, PPT sources, and follow-up edits should feel like different expressions of the same product contract, not unrelated pipelines.
- If a capability is meant to generalize, implement it through non-hardcoded intent understanding, source structure, contracts, and reusable policies. Do not solve broad user-intent problems with project-specific examples, fixed page numbers, demo filenames, or one-off keyword patches.
- Keep pipelines simple and legible. Add a new entity only when it removes real complexity, clarifies ownership, or gives the Agent better context; do not add layers just to patch a symptom.
- Be skeptical of fallback. Hidden fallback that silently lowers quality is usually worse than a clear preflight decision, scope request, or visible failure. Fallback is acceptable only when it remains source-grounded, quality-preserving, and explicit in status.
- Put quality responsibility before generation when possible. The system should understand scope, source coverage, and feasibility before it spends model calls, not save weak artifacts and hope later validators repair them.
- Use model intelligence for intent and judgment, and deterministic code for objective boundaries. The product should behave like a serious content director who understands the user's job, while code enforces page refs, source metadata, empty-body rejection, and persistence rules.
- The highest bar is not "the system returns something"; it is "the artifact respects the user's serious intent and is worth using."

## Case-Driven Robustness Principle

Recorded 2026-06-04 from user direction during editable PPTX pipeline testing.

- Treat real-world test cases as compounding product memory. Each serious case should either validate the current pipeline or produce a generalized improvement that makes the system stronger for the next similar case.
- Do not turn case learning into case-specific patches. A fix learned from one deck, client, industry, language, or visual style must be expressed as a reusable diagnostic, contract, policy, metric, or source-level correction.
- Preserve the existing architecture taste while learning from more examples: keep pipelines simple, use Occam's razor, and avoid adding orchestration, layers, or state paths unless they remove more complexity than they introduce.
- Be especially cautious about prompt growth. Do not respond to every failed case by appending more instructions. Prefer better input structure, smaller model contracts, deterministic measurement, source repair, and narrow prompt changes with regression evidence.
- Every practical test should leave behind one of three durable assets: a reproducible benchmark, a regression test, or a documented design rule. If it leaves only an anecdote or a one-off workaround, the learning was not captured.
- Case-driven robustness is a loop: reproduce with a real artifact, diagnose the failure class, fix the smallest general source, add a guard or benchmark, and only then broaden to more cases.

## Simplicity Principle

Recorded 2026-05-12 from user direction.

- Prefer the smallest root-cause fix that removes a whole class of failures.
- Do not add extra orchestration, queues, or recovery layers when a single source of truth or a synchronous state write solves the problem.
- Use Occam's razor in workflow/state bugs: first remove duplicated state paths and hidden async handoffs before adding new fallback behavior.

## Source-Level Prevention Principle

Recorded 2026-05-14 from user direction.

- For every serious defect, trace the root cause and fix it at the source. Do not stop at symptom cleanup, downstream normalization, UI wording, or case-specific guards when the upstream intent, source parsing, state transition, or prompt contract is wrong.
- Always eliminate failures at the source; do not treat post-hoc checking, filtering, or validation as the primary solution when the source can be made clean.
- Design workflows so bad intermediate artifacts cannot enter the next generation step in the first place. For generation bugs, fix input eligibility, prompt construction, state transitions, and source-of-truth boundaries before adding result cleanup.
- General generation paths must not contain project-specific, demo-specific, or stale business-context defaults. Prompt inputs and deterministic drafts should come only from the current user intent, current project state, uploaded materials, and intentionally global templates.
- Use downstream checks only as defense-in-depth or diagnostics. If a check catches an issue, follow it back to the contaminated source and remove that source instead of normalizing the symptom.
- For cross-project contamination, stale defaults, or unintended domain bias, the fix is to remove the upstream contamination path, not to add a final-stage blocker.

## Fallback Quality Gate Principle

Recorded 2026-05-22 from user direction after the Darwin content-plan incident.

- Fallback is a last-resort protection mechanism, not a normal generation strategy. Prefer source-level fixes, clearer contracts, and continuation of real partial model output before introducing or expanding fallback paths.
- Decide whether fallback is eligible before it is used as model input, saved state, or user-visible output. Low-quality fallback must not enter prompts and then rely on later validators to clean it up.
- A fallback that lowers artifact quality must not be silently marked as a successful generation result. It must either fail visibly, continue from the usable partial output, or mark affected pages/items as `needs_review`.
- Empty skeletons, placeholder copy, mock images, and generic editable drafts are not acceptable final PPT artifacts. They may exist only as transient scaffolding or explicit development/test output.
- Source-driven deterministic paths are acceptable only when they are grounded in current user materials, uploaded documents, selected templates, reference images, or confirmed project state. Treat these as explicit source paths, not as generic fallback.
- Any fallback used in prompts or generation inputs must be labeled by confidence/status so downstream models do not mistake incomplete analysis, queued analysis, or placeholder summaries for high-quality evidence.

## Project Isolation Principle

Recorded 2026-05-14 from user direction.

- Every project must start as a fresh, isolated workspace. New projects must not inherit chat history, uploaded materials, slides, selected styles, generated assets, pending requests, composer drafts, or transient UI state from any previous project.
- Any state that can influence prompts, Brief summaries, content plans, visual plans, or generation inputs must be keyed by `project_id` and cleared before the next project is hydrated.
- On project switch or project creation, clear transient frontend state first, then load only that project's server state. Do not allow stale local state to appear as current project context during the loading window.
- Backend reads and writes must stay project-scoped by storage path and database relation. Shared caches or background tasks must carry explicit project ownership and must drop stale writes if ownership changes.

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
