import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const source = readFileSync(join(import.meta.dirname, "components/VisualAssetsPanel.tsx"), "utf8");

assert.match(
  source,
  /function getImageUrl\(apiBase: string, url\?: string \| null\)[\s\S]*if \(!url\) return "";/,
  "VisualAssetsPanel image URL helper must tolerate missing or migrated empty URLs"
);
assert.match(
  source,
  /const displayUrl = item\.overlay_url \|\| item\.url;[\s\S]*const canPreview = Boolean\(displayUrl\) &&/,
  "VisualAssetsPanel thumbnails must render a missing-file state when no preview URL exists"
);
assert.match(
  source,
  /onClick=\{\(\) => canPreview && onImageClick\(getImageUrl\(apiBase, displayUrl\)\)\}/,
  "VisualAssetsPanel must not invoke preview callbacks without a safe display URL"
);
