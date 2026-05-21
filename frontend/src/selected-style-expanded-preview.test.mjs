import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const source = readFileSync(join(import.meta.dirname, "App.tsx"), "utf8");
const css = readFileSync(join(import.meta.dirname, "index.css"), "utf8");

function cssBlock(selector) {
  const selectorIndex = css.indexOf(selector);
  assert.notEqual(selectorIndex, -1, `${selector} should exist`);
  const openIndex = css.indexOf("{", selectorIndex);
  let depth = 0;
  for (let index = openIndex; index < css.length; index += 1) {
    if (css[index] === "{") depth += 1;
    if (css[index] === "}") depth -= 1;
    if (depth === 0) return css.slice(selectorIndex, index + 1);
  }
  throw new Error(`Could not parse CSS block for ${selector}`);
}

assert.match(
  source,
  /import \{ buildSelectedStylePreview \} from "\.\/selectedStylePreview";/,
  "App should use the selected style preview helper"
);

assert.match(
  source,
  /const selectedStylePreview = selectedProject\?\.selected_style[\s\S]*buildSelectedStylePreview\(selectedProject\.selected_style\)/,
  "App should derive preview data from the selected style once"
);

assert.match(
  source,
  /selectedStylePreview\.pages\.map\(\(page\)[\s\S]*page\.label/,
  "expanded selected style bar should render the derived page labels"
);

assert.doesNotMatch(
  source,
  /selectedStyleInspectorProposal|Style Inspector|pg-style-inspector/,
  "style proposal details should stay in the horizontal proposal drawer, not open a right-side inspector"
);

assert.match(
  source,
  /const proposalPreview = buildSelectedStylePreview\(proposal\);[\s\S]*proposalPreview\.pages\.map\(\(page\)[\s\S]*page\.label/,
  "expanded style proposal details should render the same page-type preview legend before confirmation"
);

assert.match(
  source,
  /pg-style-dock-compact-meta[\s\S]*视觉节奏[\s\S]*字体[\s\S]*整体基底/,
  "expanded proposal details should collapse repeated text into a compact meta row"
);

assert.doesNotMatch(
  source,
  /pg-style-dock-preview-block[\s\S]*<p className="pg-style-preview-summary">/,
  "expanded proposal preview should not repeat the same style summary above the miniatures"
);

assert.doesNotMatch(
  source,
  /pg-style-dock-detail-block/,
  "expanded proposal details should not render repeated detail cards below the preview"
);

for (const className of [
  "pg-style-preview-band",
  "pg-style-page-previews",
  "pg-style-page-mini",
  "pg-style-page-mini-brand",
  "pg-style-page-mini-chart",
  "pg-style-preview-notes",
]) {
  assert.match(source, new RegExp(className), `${className} should be rendered by App`);
  assert.match(css, new RegExp(`\\.${className}`), `${className} should be styled`);
}

assert.doesNotMatch(
  source,
  /<span>氛围：\{stripHexCodes\(selectedProject\.selected_style\.mood\)/,
  "expanded selected style bar should no longer start with mood metadata"
);

assert.match(css, /grid-template-columns: repeat\(4, minmax\(120px, 1fr\)\)/, "desktop preview band should show four stable miniatures");
assert.match(css, /\.pg-style-page-mini\.is-calm \.pg-style-page-mini-title[\s\S]*var\(--style-page-accent\)/, "calm content/data miniatures should show palette accent on key marks");
assert.match(source, /"--style-page-secondary": page\.secondary/, "page miniatures should receive the secondary palette color");
assert.match(source, /"--style-page-highlight": page\.highlight/, "page miniatures should receive the highlight palette color");
assert.match(source, /"--style-page-chart-2": page\.chartColors\[1\]/, "data miniatures should receive chart color roles");
assert.match(css, /var\(--style-page-secondary\)/, "miniatures should visibly use the secondary palette color");
assert.match(css, /var\(--style-page-highlight\)/, "miniatures should visibly use the highlight palette color");
assert.match(css, /var\(--style-page-chart-2\)/, "chart bars should use derived chart palette roles");
assert.doesNotMatch(
  cssBlock(".pg-style-page-mini"),
  /var\(--style-page-secondary\)/,
  "secondary or contrast colors should not be washed into the main slide background"
);
assert.doesNotMatch(
  cssBlock(".pg-style-page-mini"),
  /var\(--style-page-highlight\)/,
  "low-ratio highlight colors should not become large background fields"
);
assert.doesNotMatch(
  css,
  /conic-gradient\([^;]*var\(--style-page-secondary\)[^;]*var\(--style-page-highlight\)/,
  "preview glow should not mix all accent colors into one decorative blob"
);
assert.match(css, /@media \(max-width: 760px\)[\s\S]*pg-style-page-previews/, "miniatures should wrap on narrow screens");
assert.doesNotMatch(css, /\.pg-style-dock-card\.is-inspected \.pg-style-dock-detail\s*\{[\s\S]*display:\s*none/, "expanded proposal detail should remain visible inside the horizontal drawer");
assert.match(css, /\.pg-style-dock-card\.is-expanded\s*\{[\s\S]*grid-column:\s*1 \/ -1/, "expanded proposal detail should become a full-width horizontal drawer");
