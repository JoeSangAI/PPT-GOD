# Fallback Quality Gates And Audit

Recorded: 2026-05-22

## Triggering Incident

The Darwin project exposed a failure mode in content planning:

- The model streamed a usable partial page map for pages 1-6.
- The page-map stream stopped early.
- The missing pages were filled with long-deck skeleton fallback pages.
- Those fallback pages contained generic copy such as "本页已先放入长篇 PPT 结构中..." but were accepted as a completed content plan.

This made the first pages look real while the later pages became editable placeholders. The root issue was not long text itself; it was that the page-map fallback path created skeleton pages to satisfy page count, then downstream treated those pages as eligible content.

The root fix is simple: page-map fallback is allowed only for real source-derived drafts. It must not create skeleton pages. When partial model output exists, the system keeps the real partial pages and lets the normal continuation path complete the remaining pages. If there is no source-derived fallback and no usable model output, content planning fails instead of saving placeholders as success.

Final placeholder rejection remains as defense-in-depth, not as the primary mechanism.

## Design Position

Fallback should be rare and explicit. It is acceptable only when it protects the user workflow without pretending that lower-quality output is finished work.

The preferred order is:

1. Fix the source of failure.
2. Generate real content structure.
3. Convert that structure into per-page JSON.
4. Continue from usable partial model output if the first pass is incomplete.
5. Use source-derived fallback only when it contains real material.
6. Fail visibly instead of filling missing pages with skeletons.

The disallowed pattern is:

1. Model path fails or truncates.
2. Generic fallback produces skeleton or placeholder content.
3. The system saves it as a successful PPT artifact.

## Quality Gates

Every fallback path must satisfy these gates before it can become user-visible generated work:

- `Grounding`: The output must be grounded in current project state, current user instructions, uploaded materials, selected templates, reference images, or confirmed generated pages.
- `Input Eligibility`: Low-quality fallback must be rejected before it is used as prompt input, saved project state, or user-visible output.
- `Specificity`: The output must contain concrete slide content, visual direction, or executable state changes. Generic placeholders do not qualify.
- `Status`: If output quality is below the normal generation contract, it must carry a visible status such as `needs_review`, `failed`, or an explicit draft warning.
- `No Silent Success`: Degraded fallback must not update project status, run status, or chat copy in a way that implies the requested generation succeeded.
- `Downstream Honesty`: Fallback summaries or placeholder analysis must be labeled so later prompts do not treat them as full visual/content evidence.
- `Bounded Degradation`: Transport fallbacks such as image compression or retry profiles must have hard limits and logs; they should not keep degrading until quality collapses.

## Audit Summary

| Area | Current Fallback | Protects Flow? | Quality Impact | Decision |
| --- | --- | --- | --- | --- |
| `content_plan.generate_content_page_map` | Source page map fills missing model pages | Yes, only if source-derived | Can be high quality when based on real documents; skeleton fallback caused major degradation | Fixed: skeleton placeholders are rejected and partial model output continues |
| `content_plan.build_long_deck_skeleton` | Editable long-deck skeleton | Only as transient scaffolding | Low quality if saved as final content | Must never be accepted as completed content |
| `content_plan._fallback_deck_blueprint` | Deterministic section blueprint | Yes, as planning scaffold | Low risk if used only to structure prompts | Keep, but do not treat as content quality |
| `api.slides` obsolete incremental skeleton path | Removed from the API module | No | Avoids reconnecting a skeleton-as-complete path | Use the current `generate_content_plan` background entrypoint |
| `api.slides` reference-analysis placeholders | Queued/failed analysis placeholder and fallback reference summary | Yes, keeps uploads non-blocking | Medium: prompt quality drops if placeholder is treated as real analysis | Keep only with explicit queued/failed status and concise labels |
| `visual_plan._fallback_visual_plan` | Deterministic visual draft for tests/tools | Yes for tests and explicit tools | Lower than LLM visual planning | Production LLM failures should surface, as current docstring states |
| `visual_plan` logo-placeholder cleanup | Fills visual evidence only when a logo exists | Yes | Low if logo state is correct | Keep; fail when no-logo pages contain only logo placeholder language |
| `style_proposal` template/reference clone builders | Deterministic proposal from uploaded template/reference | Yes | Usually quality-preserving because user provided the source | Treat as explicit source-driven path; consider renaming away from "fallback" |
| `image_generation` reference upload profiles | Two smaller upload profiles | Yes, handles API upload limits | Some fidelity loss, bounded | Keep with current two-level cap |
| `image_generation` mock mode | Placeholder image | Yes for local tests | Total quality loss if used in production | Must remain dev/test only |
| `image_generation` cached mode | Reuse cached generated image | Yes for development/cost control | No quality loss if cache key is correct | Keep; never substitute unrelated cached images |
| `generation_pipeline` template reference fallback | Uses template page when no family seed exists | Yes, preserves style consistency | Can reduce originality, but source is selected template | Keep, lower priority than page/family seeds |
| `generation_pipeline` legacy family inference | Infers family from slide type for old data | Yes, supports old projects | Low/medium if type is broad | Keep as compatibility path |
| `generation_pipeline` seed promotion | Promotes first available family page as seed | Yes, prevents seedless generation batches | Can make an arbitrary page the style seed | Keep, but recommended seeds should remain preferred |
| `chat` JSON/natural-language repair | Converts incomplete streams into structured actions | Yes, avoids dead chat responses | Medium: intent can be coerced incorrectly | Keep narrow; executable action must match user intent and state mutation |
| `chat` response fallback copy | Fills empty response text by action | Partially | Risky if copy says done before state changed | Completion copy must follow actual mutation/result state |
| `frontend` streamed-content parser fallback | Salvages structured result from streamed text after retry | Yes | Low if validation stays strict | Keep after retry, not before |
| `frontend` local style adjustment fallback | Builds a style card from current style if backend cannot provide one | Yes | Medium: may look like a new proposal while mostly reusing old style | Prefer backend proposal; label/limit local draft behavior |
| `frontend` selected-style preview fallback palette | UI preview defaults | Yes | UI-only | Keep |
| `main.py` SPA fallback | Routes frontend paths to index | Yes | No generation quality impact | Not part of generation fallback policy |

## Current Code Change

The content-plan fix implements the following policy:

- `_fallback_page_map` returns only usable source-derived page maps; when the deterministic draft is just skeleton content, it returns no fallback.
- Preserve low-content draft status when converting deterministic outlines into page maps, so `skeleton` and `needs_review` remain distinguishable from usable source drafts.
- Do not send skeleton fallback pages to the model as a pre-generated source draft.
- Detect known long-deck skeleton placeholder markers in page maps with `generation_status=page_map_source`.
- Reject page maps containing skeleton placeholders as useful output.
- Do not merge placeholder fallback pages into missing model pages.
- If partial real model pages exist and the only source fallback is skeleton content, return the partial model map so normal continuation fills missing pages.
- If model generation fails before usable pages and the only fallback is skeleton content, raise an error instead of saving placeholders.
- Preserve expected total page count while converting partial page maps, so page 6 of a partial 16-page plan is not mislabeled as the ending page.

Regression coverage:

- `test_page_map_fallback_does_not_create_skeleton_without_documents` locks the root rule: no documents means no skeleton page-map fallback.
- `test_partial_page_map_does_not_save_skeleton_placeholders` reproduces the Darwin failure: pages 1-6 arrive from the model, pages 7-16 would previously become skeleton placeholders, and now pages 7-16 are produced by the continuation path.

## Policy By Category

### Acceptable Fallback

Acceptable fallback must be source-grounded and quality-equivalent enough for the task:

- Document-derived content draft that preserves real uploaded text.
- Template/reference clone proposal when the user provided a template or visual reference as the intended style source.
- Template image reference when no stronger seed exists and the user selected the template.
- Bounded image upload resizing when the original reference exceeds API limits.
- UI-only preview defaults that do not enter prompts or generated artifacts.

### Conditional Fallback

Conditional fallback may exist, but only with visible status and narrow gates:

- Queued/failed reference image analysis summaries.
- Chat intent coercion after JSON parse failure.
- Local frontend style adjustment card when backend style generation is not available.
- Legacy slide-family inference for old projects.
- Seed promotion when a batch has no recommended seed.

These paths should not claim full quality. They should preserve enough workflow continuity for the user to continue, while keeping the lower-confidence state visible.

### Disallowed As Final Output

These paths must not become final generated PPT artifacts:

- Empty long-deck skeleton pages.
- Placeholder copy.
- Mock images.
- Generic "operation completed" text without a corresponding state change.
- Any fallback analysis that downstream prompts treat as completed expert analysis.

## Open Follow-Ups

- Rename deterministic source-driven builders that currently include `fallback` in the function name when they are not quality-degrading fallback paths.
- Add narrow tests for chat fallback actions that currently coerce `answer` into executable actions after parse failures.
- Review frontend local style adjustment UX so it cannot be mistaken for a fully regenerated backend proposal.
