import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const source = readFileSync(join(import.meta.dirname, "App.tsx"), "utf8");

assert.match(
  source,
  /const \[materialsOpen, setMaterialsOpen\] = useState\(true\);/,
  "本页画面素材 should be expanded by default"
);

assert.doesNotMatch(
  source,
  /grid grid-cols-3 gap-2 text-\[11px\] mb-3/,
  "mode explanations should no longer render as the top three-card legend"
);

assert.match(
  source,
  /const ASSET_ROUTE_HELP: Record<AssetRoute, \{ label: string; description: string; costNote\?: string \}> = \{/,
  "route labels and descriptions should be centralized"
);

for (const [label, description] of [
  ["智能融合", "把素材作为画面参考，融入整体风格、光影和构图，适合照片、场景和氛围图。"],
  ["精修融合", "先融合进画面，再校准主体边缘、比例和关键细节，适合产品、人像或必须更准确的素材。"],
  ["精确粘贴", "保留原图细节和比例，并放在可控位置，适合 Logo、截图、图表和必须原样呈现的素材。"],
]) {
  assert.match(
    source,
    new RegExp(`${label}[\\s\\S]{0,240}${description}`),
    `${label} should keep its user-facing explanation`
  );
}

assert.match(source, /const renderAssetRouteButton = \(/, "asset route buttons should share one tooltip renderer");
assert.match(source, /role="tooltip"/, "route help should render as hover tooltip content");
assert.match(source, /group-hover\/route:block/, "route tooltip should be shown on button hover");
assert.match(source, /group-focus-within\/route:block/, "route tooltip should also be shown on keyboard focus");
assert.match(source, /w-64/, "longer route explanations should have enough tooltip width");
assert.match(
  source,
  /aria-label=\{`\$\{help\.label\}：\$\{help\.description\}`\}/,
  "route buttons should expose their explanation to assistive technology"
);
assert.match(
  source,
  /costNote: "每个组件会单独精修，生成更慢，也会消耗更多 credits。"/,
  "refined fusion should show a concise user-visible cost note"
);
assert.match(source, /help\.costNote/, "refined fusion cost note should render from route help");
assert.equal(
  (source.match(/ASSET_ROUTE_OPTIONS\.map/g) || []).length,
  3,
  "all three route button groups should use the shared route options"
);
