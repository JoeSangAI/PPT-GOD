import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const source = readFileSync(join(import.meta.dirname, "App.tsx"), "utf8");
const css = readFileSync(join(import.meta.dirname, "index.css"), "utf8");

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
assert.match(css, /@media \(max-width: 760px\)[\s\S]*pg-style-page-previews/, "miniatures should wrap on narrow screens");
assert.doesNotMatch(css, /\.pg-style-dock-card\.is-inspected \.pg-style-dock-detail\s*\{[\s\S]*display:\s*none/, "expanded proposal detail should remain visible inside the horizontal drawer");
assert.match(css, /\.pg-style-dock-card\.is-expanded\s*\{[\s\S]*grid-column:\s*1 \/ -1/, "expanded proposal detail should become a full-width horizontal drawer");
