# PPT God Agent Compatibility Layer

> Version: 2026-07-03 draft
> Direction: workflow-first Agent control with selective fine-grained editing

## 1. Why This Exists

PPT God needs to work well with external Agent tools such as Codex, Claude Code, Cursor, and future MCP-compatible clients.

The goal is not to replace the Web UI with pure chat. The goal is to make PPT God callable, inspectable, and controllable by Agents while preserving the GUI where it is the better alignment surface.

External Agents should be able to run the full PPT God workflow when appropriate, but PPT God should surface contextual UI checkpoints when the user needs to review or precisely adjust content, visual direction, page layout, source assets, or generation quality.

## 2. Product Principle

Agent and GUI have different strengths.

- Agent is best at high-context intent understanding, source synthesis, planning, orchestration, and cross-tool work.
- GUI is best at high-frequency, broad-scope, multimodal alignment, precise selection, visual review, and page-level confirmation.
- PPT God should expose deterministic product actions to Agents, not raw implementation internals.
- Every Agent-visible action must map to a real workflow action, persisted project state, or an explicit reason that no change happened.

## 3. Design Decision

The first version should be workflow-first.

PPT God should expose a small set of high-level actions that match real user workflow stages:

1. Create or select a project.
2. Add sources and assets.
3. Generate or update the content plan.
4. Confirm content.
5. Generate or update visual direction.
6. Confirm visual direction.
7. Generate page visual plans and prompts.
8. Generate prototype pages.
9. Generate final deck.
10. Export PPTX.

Fine-grained editing should exist in the first version only for high-value cases:

- Update selected slide content.
- Update selected slide visual requirements.
- Attach, pin, or exclude page assets.
- Retry failed pages.
- Open the relevant UI checkpoint.

Do not expose every internal HTTP endpoint as an Agent tool. That would make Agents reason about implementation details instead of product intent.

## 4. Contract Shape

The Agent layer should be built around three object types.

### 4.1 Project Context

External Agents need a compact, reliable project snapshot:

```json
{
  "project_id": "uuid",
  "title": "Deck title",
  "stage": "draft | content | visual | visual_design | prototype | batch | completed",
  "status": "draft | planning | visual_ready | prompt_ready | prototype_ready | completed | failed",
  "page_count": 12,
  "content_plan_confirmed": true,
  "has_selected_style": true,
  "has_visual_prompts": true,
  "has_generated_images": false,
  "active_run": {
    "id": "run-id",
    "kind": "visual_prompts",
    "status": "queued | running",
    "progress": {"completed": 3, "total": 12, "failed": 0}
  },
  "sources": {
    "documents": 2,
    "logos": 1,
    "style_refs": 3,
    "visual_assets": 8
  },
  "risks": [
    {
      "code": "content_not_confirmed",
      "message": "Content plan must be confirmed before visual generation."
    }
  ],
  "next_actions": [
    {
      "action": "confirm_content",
      "label": "Confirm content plan",
      "requires_ui_review": true
    }
  ]
}
```

This context is the first thing an external Agent should call before acting.

### 4.2 Agent Action

Every action should have:

- A stable action name.
- A narrow input schema.
- A deterministic state transition.
- A structured receipt.
- A next-action recommendation.
- A UI checkpoint when visual or multimodal alignment is needed.

### 4.3 Action Receipt

Every write action should return:

```json
{
  "ok": true,
  "action": "generate_content_plan",
  "project_id": "uuid",
  "changed": true,
  "affected_pages": [1, 2, 3],
  "run": {
    "id": "run-id",
    "kind": "content_plan",
    "status": "queued"
  },
  "message": "Content plan generation started.",
  "ui_checkpoint": {
    "kind": "content_plan_review",
    "url": "http://localhost:5173/projects/uuid?stage=content",
    "reason": "Review page titles, body, and structure before visual generation."
  },
  "next_actions": ["get_run_status", "open_project_ui"]
}
```

For no-op or rejected actions:

```json
{
  "ok": false,
  "action": "start_generation",
  "changed": false,
  "error": {
    "code": "missing_visual_prompts",
    "message": "Some slides do not have current visual prompts."
  },
  "recoverable": true,
  "next_actions": ["generate_visual_prompts"]
}
```

## 5. Target Tool Surface

The target surface has two layers:

- Core workflow tools that should be implemented first.
- Selective editing tools that can follow after the workflow contract is stable.

The first implementation milestone should start with the smallest useful subset and avoid exposing internal endpoints directly.

### Read Tools

#### `get_project_context`

Returns the compact project context, current stage, risks, and next actions.

#### `list_projects`

Returns projects visible to the current tester/user, with title, status, updated time, and active run summary.

#### `get_artifacts`

Returns selected artifacts for inspection:

- `content_plan`
- `style_proposals`
- `selected_style`
- `visual_plans`
- `prompts`
- `slide_previews`
- `export`

This should be bounded and summarized by default. Full artifact retrieval can be opt-in.

#### `get_run_status`

Returns active or latest run status, progress, failure reason, and next safe action.

### Workflow Tools

#### `create_project`

Creates a project and returns `project_id`, UI URL, and next actions.

#### `add_sources`

Adds source documents and reference assets. It should support local file paths in local mode and uploaded file IDs or URLs in hosted mode.

#### `generate_content_plan`

Starts or updates content planning from topic, sources, page count, and intent. Returns a run when async.

#### `confirm_content`

Confirms the content plan. Should be allowed only when slides exist and no blocking content risk remains.

#### `generate_style_proposals`

Generates visual direction options from confirmed content and assets.

#### `select_style`

Selects a style proposal by ID, index, or structured style payload.

#### `generate_visual_prompts`

Generates page visual plans and image prompts. Supports selected pages when safe.

#### `start_generation`

Starts prototype, selected-page, retry-failed, or final batch generation.

#### `export_pptx`

Returns the downloadable PPTX path or URL. If export is stale, returns why and which action must run first.

### Optional Selective Editing Tools

#### `update_slide_content`

Updates a single slide or selected pages with explicit page bounds.

#### `update_slide_visual`

Updates visual requirements for one slide or selected pages, without directly generating images unless explicitly requested.

#### `manage_assets`

Pins, unpins, excludes, or changes processing mode for existing assets.

## 6. UI Checkpoints

The Agent layer should return UI checkpoints instead of forcing text-only confirmation.

### Required UI Checkpoints

#### Content Plan Review

Trigger after content planning or content mutation.

Reason: page sequence, titles, body content, notes, and source fidelity are easier to review as cards/pages than in chat.

#### Visual Direction Review

Trigger after style proposals.

Reason: palette, typography, mood, and reference-image interpretation require visual comparison.

#### Page Visual Plan Review

Trigger after visual prompt generation or page-level visual edits.

Reason: individual page layout and asset usage need multimodal inspection.

#### Prototype Review

Trigger after prototype generation.

Reason: actual generated images must be visually inspected before batch generation.

#### Export Review

Trigger when final PPTX is ready or partially exportable.

Reason: user needs the artifact path, download status, and any known stale pages.

### Optional UI Checkpoints

- Source document review when parsing confidence is low.
- Logo review when extracted logo is uncertain.
- Asset pinning when there are multiple plausible visual assets for a page.
- Failure review when multiple pages fail with different causes.

## 7. Local vs Hosted Mode

PPT God should support both local and hosted Agent control, but both modes must share the same action contract.

### Local Mode

Best for Joe and open-source users who want to modify code while using PPT God.

Local mode can use:

- Local FastAPI server.
- Local CLI.
- Local MCP server over STDIO.
- Local files and project data.
- Direct browser opening for Web UI checkpoints.

Advantages:

- Easy to debug and patch.
- Works with local files.
- Fits Codex/Claude Code development workflows.
- No need to solve commercial auth on day one.

Risks:

- Environment setup can break.
- Users may run inconsistent versions.
- Harder to support non-technical users.

### Hosted Mode

Best for commercial use.

Hosted mode should use:

- Remote API/MCP endpoint.
- OAuth or account token.
- Server-side storage and generation workers.
- Stable versioned deployments.
- Audit logs and billing.

Advantages:

- Stable for ordinary users.
- Easier to enforce permissions, quotas, billing, and support.
- One product version can serve many Agent clients.

Risks:

- Requires auth, security, privacy, quota, and cost controls.
- Less flexible for users who want to patch code immediately.

### Principle

Do not build separate product logic for local and hosted mode. Build one Agent Action Contract and multiple adapters:

- Web UI adapter.
- CLI adapter.
- Local MCP adapter.
- Hosted MCP adapter.

## 8. Conflict Handling

External Agent control creates predictable conflicts. The contract should handle them explicitly.

### Agent Wants to Skip Confirmation

If the action affects visual direction, page content, generation quality, or final export, PPT God may return a UI checkpoint even when the Agent asks to continue.

The Agent can still continue only if the action allows `approval_mode: "agent"` and the project risk level is low.

### Agent Uses Stale Context

Every write action should validate current project revision or artifact signatures when possible.

If stale:

- Reject with `stale_context`.
- Return the current context.
- Recommend `get_project_context`.

### Agent Requests a Downstream Action Too Early

Reject with a clear precondition error:

- `content_not_ready`
- `content_not_confirmed`
- `style_not_selected`
- `visual_prompts_missing`
- `generation_inputs_stale`
- `active_run_exists`

### Agent and GUI Edit at the Same Time

The backend remains source of truth. GUI and Agent actions both go through the same action layer.

Actions should return affected pages and current stage after mutation.

### Local Code Version Differs From Hosted Product

Expose product version and contract version in every context response:

```json
{
  "product_version": "0.0.0-local",
  "contract_version": "agent-contract-2026-07-03"
}
```

## 9. Current PPT God Fit

PPT God already has many required foundations:

- Project and slide state stored in database.
- Workflow states such as draft, planning, visual_ready, prompt_ready, prototype_ready, completed, failed.
- Run tracking through `ProjectRun`.
- Existing endpoints for project creation, upload, content plan, style proposals, visual prompts, generation, retry, and download.
- Content and visual Agent action catalogs.
- Stale artifact detection and state reconciliation.
- Rollback and cancellation mechanisms.
- Web UI stages and gate actions in the frontend workflow model.

## 10. Main Gaps

The current system lacks a dedicated external Agent-facing layer.

Important gaps:

1. No single compact project context endpoint designed for external Agents.
2. No stable list of current allowed actions with machine-readable preconditions.
3. Existing APIs are implementation-facing, not product-action-facing.
4. Chat action results and workflow API actions are not yet unified into one external contract.
5. UI checkpoints are implicit in the Web UI, not returned as structured action results.
6. CLI does not exist yet.
7. MCP server does not exist yet.
8. Local and future hosted modes do not yet share a versioned contract.

## 11. First Implementation Milestone

The first implementation milestone should not be MCP.

It should be an internal Agent Action service and one HTTP surface:

```text
GET  /agent/projects
GET  /agent/projects/{project_id}/context
GET  /agent/projects/{project_id}/artifacts
GET  /agent/projects/{project_id}/runs/{run_id}
POST /agent/projects/{project_id}/actions
```

`POST /actions` receives:

```json
{
  "action": "generate_content_plan",
  "payload": {
    "topic": "...",
    "page_count": 12
  },
  "approval_mode": "ui | agent",
  "context_revision": "optional"
}
```

This gives Web UI, CLI, and MCP a shared execution layer.

## 12. Done Means

The first milestone is done when:

- Codex can inspect a PPT God project without reading internal database files.
- Codex can know the safe next action from structured context.
- Codex can start content planning and observe the run status.
- Codex can receive a UI checkpoint URL after content planning.
- Codex can continue to style proposal, visual prompt generation, prototype generation, and export through the same action contract.
- Invalid actions fail with clear precondition errors and recovery suggestions.
- Existing Web UI behavior still works.
