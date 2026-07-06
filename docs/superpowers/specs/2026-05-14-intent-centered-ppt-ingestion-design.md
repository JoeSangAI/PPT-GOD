# Intent-Centered PPT Ingestion Design

## Context

Users often upload PPT files that are not editable decks. A common case is a deck made from full-slide background images plus pasted screenshots. The current system can extract many images from these PPTX files, but content planning may see little or no text because the original words are baked into images.

This creates the wrong product behavior: the system understands that screenshots can become reference images, but it misses the deeper job of recovering the original written intent before deciding how to make a new PPT.

The product principle from `AGENTS.md` applies here: PPT God should understand the user's intent and turn it into the PPT artifact, not only process files.

## Problem

PPT ingestion currently conflates two separate questions:

1. What facts and assets can be recovered from the uploaded file?
2. How should those facts and assets be used for this user's goal?

The first question is document understanding. It should recover editable text, OCR text, rendered pages, screenshots, logos, and useful visual assets.

The second question is intent understanding. The same uploaded PPT may be:

- A source deck to reproduce almost exactly.
- A rough deck to polish while preserving its page order.
- Raw material to restructure, condense, expand, or merge.
- A visual style or layout reference only.

File shape alone cannot answer that second question. A screenshot-heavy PPT still needs OCR, but OCR is only material recovery; it does not imply automatic rewriting.

## Goals

- Recover text from image-based PPT pages before content planning.
- Preserve page-level source traceability from original PPT pages to generated pages.
- Make the user's intended treatment of the uploaded PPT explicit and durable.
- Let Agent ask a narrow clarification only when the next step would materially change the result.
- Keep user-facing copy focused on outcomes, not implementation details such as OCR, pipeline, or classifiers.
- Keep normal workflow non-blocking where possible, but avoid generating empty content plans from unread source pages.

## Non-Goals

- Do not implement a deterministic text-overlay rendering system for generated slides.
- Do not force every uploaded PPT into 1:1 replication.
- Do not force every uploaded PPT into narrative restructuring.
- Do not expose internal model-routing or processing mode names to users.

## Intent Contract

Introduce a structured `Intent Contract` that travels with the project and can be updated by Agent interactions.

```json
{
  "task_type": "replicate | polish | restructure | extract | merge | template_reference",
  "rewrite_level": "none | light | moderate | free",
  "page_order_policy": "preserve | mostly_preserve | can_reorder",
  "page_count_policy": "same | similar | target_count | free",
  "source_fidelity": "verbatim | faithful | optimized | synthesized",
  "visual_source_use": "page_reference | style_reference | asset_library | ignore",
  "confidence": 0.0,
  "evidence": []
}
```

Recommended defaults for a single uploaded finished PPT:

- If the user says "1:1", "原样", "复刻", or "内容不要动": `replicate`, `none`, `preserve`, `same`, `verbatim`.
- If the user says "优化", "美化", "做得更好", or does not clarify: `polish`, `light`, `preserve`, `same` or `similar`, `faithful`.
- If the user says "提炼", "压缩", "扩展", "重组", "融合", or gives a target page count: `restructure` or `merge`, `moderate/free`, `can_reorder` when needed, `target_count/free`.
- If the upload is explicitly described as a template or visual reference: `template_reference`, with text recovery optional for style/page categorization.

The contract should be inferred from the user brief, uploaded file diagnostics, current project stage, and later chat feedback. It should also include evidence so the Agent can explain decisions in user language.

## Material Recovery

The ingestion layer should recover source facts independent of the final treatment:

- Editable PPT text through the current `python-pptx` path.
- Speaker notes and tables where available.
- Rendered page images for every source page.
- OCR text for pages whose editable text is missing or suspiciously sparse.
- Page-level screenshots as references tied to `source_document` and `source_page_num`.
- Useful local images such as product screenshots, interface screenshots, logos, and reusable assets.

For image-based PPTs, OCR text should be written into the same document text stream that content planning reads, using per-page markers. A page with OCR should not remain an empty `--- 第N页 ---` block.

Each recovered page should have a compact page understanding object:

```json
{
  "source_document": "source.pptx",
  "source_page_num": 8,
  "editable_text": "",
  "ocr_text": "...",
  "page_intent": "本页想表达什么",
  "key_facts": ["数字、产品名、流程节点、界面标签"],
  "confidence": 0.76,
  "needs_review": false
}
```

The first version can use the existing vision-reading function as the VLM OCR fallback, with local OCR as a best-effort fast path if it proves reliable enough.

## Planning Behavior

Content planning should read the recovered material and the `Intent Contract` together.

### Replicate

Use when the user wants 1:1 or content preservation.

- Keep original page count.
- Keep original page order.
- Keep source page to generated page mapping.
- Clean OCR noise lightly.
- Do not invent a new narrative.
- Put every page's `source_refs` back to the original PPT page.

### Polish

Use when the user wants a better PPT but still values the original material.

- Keep original page order by default.
- Keep page count unless the user says otherwise.
- Rewrite headings and body lightly for clarity.
- Preserve key facts, numbers, interface labels, product names, and page intent.
- Use `source_refs` on every page.
- Let visual planning use source page screenshots as page evidence.

### Restructure, Extract, Merge

Use when the user asks for deeper transformation.

- Allow page count and page order changes according to the request.
- Preserve source traceability on every page that uses uploaded material.
- Keep source facts distinct from model-supplied additions.
- If multiple source PPTs exist, do not mechanically concatenate them.

## Agent Interaction

Agent should clarify only when ambiguity affects the artifact.

Good clarification:

> 我会先读取原 PPT 的文字和页面截图。接下来更接近哪种处理：按原页顺序轻优化，还是可以重组结构？

Avoid broad questions like:

> 你想做什么类型的 PPT？

Agent should not ask if the user's intent is already clear. It should state the chosen treatment in outcome language:

- "我会按原页顺序优化表达，不重排结构。"
- "你要求 1:1 复刻，所以我会尽量保留原文和页序。"
- "你要求提炼成 10 页，所以我会重组结构，但保留来源页追溯。"

When later feedback conflicts with the current contract, update the contract rather than burying the instruction in chat history. Examples:

- "第 8 页内容不要动" creates a page-level override.
- "整体可以更大胆地重组" raises rewrite level for the deck.
- "页序别乱" locks `page_order_policy` back to `preserve`.

## UI Copy

User-facing language should describe progress and decisions, not internals.

Use:

- "已读取 35 页原 PPT，正在整理文字和图片素材。"
- "这次我会保留原页顺序，优化标题和正文表达。"
- "部分页面文字来自截图识别，请在内容规划里快速检查。"

Avoid:

- "OCR pipeline running."
- "process_mode=blend."
- "classification=finished_ppt."

If OCR or page understanding is still running, the UI may allow brief writing to continue, but content plan generation should either wait briefly for recovered text or clearly say that source text is still being prepared.

## Data Flow

1. User uploads PPT or adds it to Brief Studio.
2. Document processor saves raw file and starts background recovery tasks.
3. PPT diagnostics identify editable text density, image-only pages, rendered pages, and asset counts.
4. OCR/page understanding fills per-page text and source facts.
5. Intent inference creates or updates the `Intent Contract`.
6. If confidence is low, Agent asks one targeted clarification.
7. Content planning consumes recovered document text plus the contract.
8. Page references and extracted assets are linked by `source_document` and `source_page_num`.
9. Visual planning uses page references according to the contract.
10. Later Agent feedback updates the contract and triggers targeted regeneration when needed.

## Error Handling

- If OCR fails for a page, keep its rendered page reference and mark `needs_review`.
- If OCR confidence is low, include the text but flag the page in content planning for user review.
- If asset extraction fails, recovered text should still be available.
- If text recovery is still running, content plan generation should soft-wait, then either proceed with available pages and mark gaps or ask the user to wait when too many pages are unread.

## Testing

Add focused tests for:

- Image-only PPTs do not produce empty "原 PPT 第N页" plans when OCR text is available.
- `replicate` keeps page count, page order, and source refs.
- `polish` keeps page order but allows heading/body cleanup.
- `restructure` can change page order/count only when the user intent allows it.
- Ambiguous intent produces a narrow clarification rather than a generic question.
- Page-level overrides are respected after the initial contract is created.

## Open Questions

- Where should the persisted `Intent Contract` live: `Project.selected_style`-adjacent JSON, a new model column, or a lightweight artifact file?
- Should page understanding be stored as document metadata, reference image metadata, or a dedicated source-page artifact?
- Should the first implementation use the existing VLM reader only, or combine local OCR plus VLM cleanup?

The recommended first implementation is the smallest useful version: persist a project-level contract, add OCR/page text recovery for image-only PPT pages, and make content planning consume both before generating.
