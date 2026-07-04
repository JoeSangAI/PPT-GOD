# PPT God Content Planning Playbook

This playbook is for external Agents that directly submit a PPT God content plan.

The hard format is mandatory. The planning method is guidance, not a higher-priority rule than the user's intent.

## Purpose

Use this when an external Agent, such as Codex or Claude Code, has enough context to produce the final content-planning artifact directly.

In this mode, do not ask PPT God to regenerate the content plan. Submit the content plan in the strict Markdown format below, then let PPT God parse it, save it as slides, and show the content review UI.

## Priority

1. User intent and confirmed context are highest priority.
2. The strict Markdown format is mandatory because PPT God parses it deterministically.
3. The content planning method below is a default quality bar, not an absolute template.
4. If user intent conflicts with the method, follow the user intent while preserving the strict format.

## Strict Markdown Format

The document may start with one H1 title:

```markdown
# Project title
```

Each slide must use exactly this page boundary:

```markdown
## P1
```

Each slide must contain exactly these field headings:

```markdown
### 类型
### 标题
### 副标题
### 正文
### 备注
```

Rules:

- Page headings must be `## P{number}`.
- Field headings must use the exact Chinese labels above.
- `类型`, `标题`, and `正文` must not be empty.
- `副标题` and `备注` may be empty, but should be filled when useful.
- Field content may contain rich Markdown, including paragraphs, bullets, numbered lists, tables, and emphasis.
- Do not add extra `###` fields.
- Do not repeat page numbers.

## Allowed Slide Types

Use one of these values in `### 类型`:

- `cover`
- `toc`
- `section`
- `content`
- `content_dense`
- `content_hero`
- `content_split`
- `content_top`
- `data`
- `hero`
- `quote`
- `ending`

Default to `content` when there is no strong reason to choose another type.

## Field Meaning

### 类型

The slide role/layout family. It helps PPT God choose visual planning behavior later.

### 标题

The main visible headline. It should be clear enough to identify the slide's job.

### 副标题

The secondary visible explanation. Use it for framing, tension, conclusion, or scope.

### 正文

The main visible content. It can be concise or rich depending on the deck's use case. Preserve key facts, arguments, examples, numbers, and source logic when they matter.

### 备注

Speaker notes, source notes, page intent, or delivery guidance. Use this to preserve context that helps a human presenter but does not need to appear as visible slide text.

## Content Planning Method

A good PPT God content plan should make each page's job clear before visual generation begins.

Prefer:

- One clear role per slide.
- Titles that express the page's point, not only its topic.
- Body content that contains enough substance for visual planning.
- Notes that preserve presenter intent, important source context, or caveats.
- Page sequencing that supports how the deck will be delivered.

Avoid:

- Pages that are only slogans with no supporting content.
- Mechanical document splitting without slide-level judgment.
- Over-compressing source material when the user asked for fidelity.
- Adding visual implementation instructions into body text.
- Inventing facts, numbers, customer claims, or source evidence.

## Validation

The validator blocks:

- Missing page boundaries.
- Duplicate page numbers.
- Missing required fields.
- Unknown slide types.
- Empty `类型`, `标题`, or `正文`.
- Zero parsed slides.

The validator warns:

- Empty `副标题`.
- Empty `备注`.
- Very short body content.
- Non-contiguous page numbers.

Warnings do not block import. Errors must be fixed before import.

## Agent Workflow

1. Read this playbook.
2. Produce strict Markdown.
3. Run:

```bash
python scripts/pptgod_cli.py validate-content-plan path/to/plan.md
```

4. Fix all errors.
5. Import:

```bash
python scripts/pptgod_cli.py import-content-plan path/to/plan.md --open
```

6. Let the user review content in PPT God's Web UI before moving to visual generation.
