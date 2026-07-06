# Selected Style Expanded Preview Design

## Context

The selected visual style bar currently shows the chosen style name, palette chips, a truncated description, and a small expanded row with mood, font, and overall base text.

This does not solve the user's real question after a style is selected: "What will this visual direction actually do to my deck?" The expanded state still reads like a metadata dump. It does not make the difference between cover, section, content, and data pages visible.

The product copy principle from `AGENTS.md` applies here: every visible detail should help the user make a concrete decision. If the information does not help the user decide whether to trust, adjust, or regenerate the direction, it should be removed or pushed into a deeper detail view.

## Problem

The current expanded style bar has three weaknesses:

- It repeats abstract text instead of showing how the style will be applied.
- It exposes adjacent implementation language such as "overall base" and strategy summaries without turning them into a user outcome.
- It gives no visual evidence that the style will stay coherent across different page types.

The user already chose a visual scheme. At this stage, the UI should not keep selling the style. It should help the user inspect whether the selected scheme will behave correctly before they generate or regenerate page visuals.

## Goals

- Make the expanded selected-style area visually useful within one glance.
- Show how the selected style changes across common page types.
- Keep the workbench compact enough that it does not steal space from slide editing.
- Preserve the existing selected style data contract where possible.
- Use user-facing language: visual rhythm, page usage, readability, and adjustment targets.

## Non-Goals

- Do not build real slide thumbnails in this component.
- Do not generate images or render actual slide previews for the style bar.
- Do not expose prompt, pipeline, source, or internal strategy fields.
- Do not redesign the earlier style proposal selection cards.
- Do not move the full style inspector into the bar.

## Useful Information

The expanded area should keep information that helps the user decide whether to continue with this style:

- Style name and palette chips in the collapsed row.
- A concise one-sentence style behavior summary.
- Four page-type treatments: cover, section, content, and data.
- Palette roles when they explain visible usage, such as title emphasis, background, data highlight, or text color.
- Font system only as a compact readable sentence.
- Direct adjustment affordances, such as making the style brighter, darker, more restrained, or less decorative, if the surrounding workflow supports it.

## Information To Visualize

The following details should be visual rather than text-heavy:

- Relative decorative intensity across page types.
- Light or dark base usage by page type.
- Whether content and data pages remain readable.
- Where accent colors appear: title, numbering, chart highlight, or decorative edge.
- The difference between strong visual pages and information pages.

The component should use lightweight schematic page miniatures, not real generated slide images. These miniatures are visual explanations of style behavior, not output previews.

## Information To De-Emphasize

The expanded first layer should not show:

- Full long-form `description`.
- Raw hex codes except in hover titles on color chips.
- `source`, `base_tone`, `content_treatment`, or other internal field names.
- Repeated mood adjectives when the miniature already communicates tone.
- Full page-type adaptation paragraphs.

These details can remain available in the existing inspector or future detail entry, but they should not be the primary expanded state.

## Selected Design

Use sample B: four page-type miniatures.

Collapsed row:

- `视觉方案: {name}`
- Up to five color chips.
- A compact behavior summary derived from the selected style.
- Expand/collapse control.

Expanded row:

- A short summary sentence.
- Four miniature cards: `封面`, `章节`, `正文`, `数据`.
- Each miniature shows the likely background treatment, accent color strength, and text or data structure for that page class.
- Below the miniatures, show two compact text blocks:
  - `视觉节奏`: how strong visual pages differ from content-heavy pages.
  - `字体体系`: the chosen title/body/data font relationship.

This design puts the important visual judgment first and keeps textual explanation secondary.

## Component Behavior

The selected style bar remains a workbench module, not a modal.

When collapsed, it should stay close to its current height. It gives quick orientation without interrupting workflow.

When expanded, it should use a fixed, compact preview band. The band can wrap on small screens but should not create a long text panel. The miniatures should have stable dimensions and not resize based on text length.

The miniatures should be deterministic from selected style data:

- Cover: strongest treatment. Uses the strongest background or primary color relationship.
- Section: strong but simpler than cover. Uses the accent or gradient cue and sparse title structure.
- Content: prioritizes readability. Uses the selected content base or a light/dark information background inferred from palette roles and strategy.
- Data: prioritizes contrast and chart clarity. Uses accent colors for key values, not full decoration.

If the selected style implies a single deck-wide dark or light base, the content and data miniatures should respect that. They should not invent a different visual language.

## Data Flow

Input remains `selectedProject.selected_style`.

Use existing fields:

- `name`
- `palette`
- `description`
- `mood`
- `font`
- `visual_strategy.summary`
- `visual_strategy.base_tone`
- `visual_strategy.content_treatment`
- `page_type_adaptation`
- `content_style_hint`

Add frontend-only helpers to derive:

- normalized palette roles
- primary/accent/background/text colors
- page preview treatment for cover, section, content, and data
- compact visual rhythm text
- compact font system text

No backend schema change is required for the first implementation.

## Error Handling

If palette data is missing, show neutral skeleton miniatures and keep the style name plus text summary.

If only string colors are available, use the color values and omit role labels.

If `visual_strategy` is missing, infer light or dark treatment from palette brightness. If inference is uncertain, use a mixed treatment but keep the summary cautious.

If a color value is invalid, replace it with a neutral fallback in the miniature while preserving the rest of the style.

The UI should never show raw JSON, empty strategy labels, or broken color chips.

## Copy Rules

Use outcome-oriented labels:

- `视觉节奏`
- `字体体系`
- `封面`
- `章节`
- `正文`
- `数据`

Avoid implementation labels:

- `base_tone`
- `visual_strategy`
- `content_treatment`
- `source`
- `pipeline`

Avoid over-explaining the component. The miniatures should carry the explanation.

## Testing

Frontend tests should cover:

- Collapsed bar still renders name, color chips, summary, and expand control.
- Expanded bar renders four miniatures with stable labels.
- Dark style keeps content and data previews dark when the selected style requires a deck-wide dark base.
- Light style keeps content and data previews light when the selected style requires a deck-wide light base.
- Missing or malformed palette does not crash rendering.
- Long descriptions do not overflow or replace the miniature preview.

Manual visual verification should include:

- Desktop width: expanded band remains compact and readable.
- Narrow width: miniatures wrap without text overlap.
- Existing workbench controls remain reachable below the style bar.

## Implementation Boundary

The first implementation should not add a new side drawer. If users still need deeper inspection after the miniature band lands, the existing style inspector can be reused or refined later.
