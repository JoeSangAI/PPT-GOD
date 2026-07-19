import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const css = readFileSync(join(import.meta.dirname, "index.css"), "utf8");

assert.match(
  css,
  /\.pg-slide-grid > \.pg-slide-card\s*\{[^}]*min-width:\s*min\(100%,\s*200px\)[^}]*flex-basis:\s*clamp\(200px,\s*calc\(\(100% - 48px\) \/ 3\),\s*448px\)/s,
  "desktop slide cards must preserve a three-column layout when the AI panel is open",
);
assert.match(
  css,
  /@media \(max-width:\s*768px\)[\s\S]*?\.pg-slide-grid > \.pg-slide-card\s*\{[^}]*min-width:\s*100%[^}]*flex-basis:\s*100%/,
  "mobile slide cards must still collapse to one column",
);
