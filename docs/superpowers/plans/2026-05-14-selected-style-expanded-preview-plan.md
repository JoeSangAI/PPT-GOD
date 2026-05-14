# Selected Style Expanded Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the selected visual style expanded text row with four compact page-type miniatures and concise rhythm/font guidance.

**Architecture:** Add a small pure frontend helper module that derives deterministic preview data from `selectedProject.selected_style`, then render that data inside the existing selected style bar in `App.tsx`. CSS owns the miniature visuals through stable classes and CSS variables so the App markup stays focused on data flow.

**Tech Stack:** React 18, TypeScript, Vite, Node source tests using TypeScript transpilation.

---

## File Structure

- Create `frontend/src/selectedStylePreview.ts`: pure helper functions for palette normalization, light/dark inference, page preview treatments, visual rhythm text, and font summary.
- Create `frontend/src/selectedStylePreview.test.mjs`: Node test that imports the TypeScript helper and verifies dark, light, and malformed palette behavior.
- Create `frontend/src/selected-style-expanded-preview.test.mjs`: source-level regression test for the App/CSS integration.
- Modify `frontend/src/App.tsx`: import the helper, compute preview data, and render page miniatures in the existing expanded selected-style bar.
- Modify `frontend/src/index.css`: style the expanded preview band, miniatures, responsive wrapping, and compact text blocks.

## Task 1: Pure Preview Helper

**Files:**
- Create: `frontend/src/selectedStylePreview.test.mjs`
- Create: `frontend/src/selectedStylePreview.ts`

- [ ] **Step 1: Write the failing helper test**

Create `frontend/src/selectedStylePreview.test.mjs`:

```js
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import vm from "node:vm";
import ts from "typescript";

function loadTsModule(filename) {
  const sourcePath = join(import.meta.dirname, filename);
  const source = readFileSync(sourcePath, "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;

  const sandbox = {
    exports: {},
    module: { exports: {} },
  };
  sandbox.module.exports = sandbox.exports;
  vm.runInNewContext(compiled, sandbox, { filename: sourcePath });
  return sandbox.module.exports;
}

const { buildSelectedStylePreview } = loadTsModule("selectedStylePreview.ts");

const darkPreview = buildSelectedStylePreview({
  name: "蓝紫流体",
  palette: [
    { name: "电光紫", hex: "#5648FF", role: "标题强调" },
    { name: "科技蓝", hex: "#3867FF", role: "图表重点" },
    { name: "深夜黑", hex: "#111827", role: "整套页面背景/内容页深色基底" },
    { name: "雾白", hex: "#F7F9FF", role: "文字" },
  ],
  font: "思源黑体（CN）/DIN Alternate（数据）/Helvetica Neue（英文）",
  visual_strategy: {
    base_tone: "dark",
    summary: "整套页面保持深色视觉基底，内容页也不切成白底。",
  },
});

assert.equal(darkPreview.pages.length, 4);
assert.deepEqual(darkPreview.pages.map((page) => page.label), ["封面", "章节", "正文", "数据"]);
assert.equal(darkPreview.pages.find((page) => page.key === "content").tone, "dark");
assert.equal(darkPreview.pages.find((page) => page.key === "data").tone, "dark");
assert.match(darkPreview.rhythmText, /封面\/章节页/);
assert.match(darkPreview.fontText, /思源黑体/);

const lightPreview = buildSelectedStylePreview({
  name: "柔紫暖白",
  palette: [
    { name: "柔紫", hex: "#C4B4E0", role: "品牌主色/视觉锚点色" },
    { name: "米白", hex: "#F9F8F5", role: "页面基底/主背景" },
    { name: "淡紫", hex: "#E8E0F0", role: "内容区/卡片底色" },
    { name: "墨灰紫", hex: "#3A3038", role: "正文/标题文字" },
  ],
  font: "标题使用现代黑体，正文使用清晰黑体",
  visual_strategy: {
    base_tone: "light",
    summary: "整套页面以白色/米白/浅色明亮基底为主。",
  },
});

assert.equal(lightPreview.pages.find((page) => page.key === "content").tone, "light");
assert.equal(lightPreview.pages.find((page) => page.key === "data").tone, "light");
assert.equal(lightPreview.baseTone, "light");

const fallbackPreview = buildSelectedStylePreview({
  name: "无配色方案",
  palette: [{ name: "坏色值", hex: "not-a-color", role: "主色" }],
  description: "用于测试坏数据时仍能渲染。",
});

assert.equal(fallbackPreview.palette[0].hex, "#CBD5E1");
assert.equal(fallbackPreview.pages.length, 4);
assert.ok(fallbackPreview.summary.length > 0);
```

- [ ] **Step 2: Run the helper test to verify RED**

Run:

```bash
cd frontend && node src/selectedStylePreview.test.mjs
```

Expected: FAIL because `selectedStylePreview.ts` does not exist or does not export `buildSelectedStylePreview`.

- [ ] **Step 3: Implement the helper**

Create `frontend/src/selectedStylePreview.ts`:

```ts
export type StylePreviewTone = "dark" | "light" | "mixed";

export interface StylePreviewColor {
  name: string;
  hex: string;
  role: string;
}

export interface StylePagePreview {
  key: "cover" | "section" | "content" | "data";
  label: string;
  tone: StylePreviewTone;
  background: string;
  accent: string;
  text: string;
  surface: string;
  intensity: "strong" | "medium" | "calm";
}

export interface SelectedStylePreview {
  name: string;
  summary: string;
  baseTone: StylePreviewTone;
  palette: StylePreviewColor[];
  pages: StylePagePreview[];
  rhythmText: string;
  fontText: string;
}

const HEX_COLOR_PATTERN = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;
const FALLBACK_PALETTE: StylePreviewColor[] = [
  { name: "主色", hex: "#4F46E5", role: "标题强调" },
  { name: "强调色", hex: "#7C3AED", role: "重点信息" },
  { name: "信息底", hex: "#F8FAFC", role: "正文页基底" },
  { name: "正文色", hex: "#111827", role: "正文文字" },
];

function stripHexCodes(value: any) {
  return String(value || "")
    .replace(/#(?:[0-9a-fA-F]{3}){1,2}\b/g, "")
    .replace(/\s+([，。；;,.])/g, "$1")
    .replace(/（\s*）|\(\s*\)/g, "")
    .replace(/\s{2,}/g, " ")
    .trim();
}

function normalizeHex(value: any) {
  const raw = String(value || "").trim();
  if (!HEX_COLOR_PATTERN.test(raw)) return "#CBD5E1";
  if (raw.length === 4) {
    return `#${raw[1]}${raw[1]}${raw[2]}${raw[2]}${raw[3]}${raw[3]}`.toUpperCase();
  }
  return raw.toUpperCase();
}

function hexBrightness(hex: string) {
  const normalized = normalizeHex(hex);
  if (normalized === "#CBD5E1" && hex !== "#CBD5E1") return 210;
  const r = Number.parseInt(normalized.slice(1, 3), 16);
  const g = Number.parseInt(normalized.slice(3, 5), 16);
  const b = Number.parseInt(normalized.slice(5, 7), 16);
  return (r * 299 + g * 587 + b * 114) / 1000;
}

function isLight(hex: string) {
  return hexBrightness(hex) >= 180;
}

function normalizePalette(palette: any[] | undefined): StylePreviewColor[] {
  const normalized = (Array.isArray(palette) ? palette : []).map((color, index) => {
    if (typeof color === "string") {
      return { name: stripHexCodes(color) || `颜色 ${index + 1}`, hex: normalizeHex(color), role: "" };
    }
    return {
      name: stripHexCodes(color?.name) || `颜色 ${index + 1}`,
      hex: normalizeHex(color?.hex),
      role: stripHexCodes(color?.role) || "",
    };
  });
  const merged = [...normalized];
  for (const fallback of FALLBACK_PALETTE) {
    if (merged.length >= 4) break;
    merged.push(fallback);
  }
  return merged.slice(0, 5);
}

function inferBaseTone(style: any, palette: StylePreviewColor[]): StylePreviewTone {
  const explicit = String(style?.visual_strategy?.base_tone || "").toLowerCase();
  if (explicit === "dark" || explicit === "light") return explicit;
  const joined = stripHexCodes([
    style?.visual_strategy?.summary,
    style?.visual_strategy?.content_treatment,
    style?.page_type_adaptation,
    style?.description,
  ].filter(Boolean).join(" "));
  if (/深色|黑色|暗色|深蓝|深紫|dark/i.test(joined) && !/浅色|白色|米白|明亮|light/i.test(joined)) return "dark";
  if (/浅色|白色|米白|明亮|light/i.test(joined) && !/全页深色|深色基底|dark/i.test(joined)) return "light";
  const lightCount = palette.filter((color) => isLight(color.hex)).length;
  const darkCount = palette.filter((color) => !isLight(color.hex)).length;
  if (lightCount >= 3) return "light";
  if (darkCount >= 3) return "dark";
  return "mixed";
}

function pickColor(palette: StylePreviewColor[], matcher: RegExp, fallbackIndex: number) {
  return palette.find((color) => matcher.test(`${color.name} ${color.role}`)) || palette[fallbackIndex] || FALLBACK_PALETTE[fallbackIndex];
}

function buildPagePreviews(baseTone: StylePreviewTone, palette: StylePreviewColor[]): StylePagePreview[] {
  const primary = palette[0] || FALLBACK_PALETTE[0];
  const accent = palette[1] || FALLBACK_PALETTE[1];
  const lightBase = pickColor(palette, /基底|背景|内容区|卡片|白|浅|米/i, 2);
  const darkBase = pickColor(palette, /深|黑|暗|背景|基底/i, 3);
  const textColor = pickColor(palette, /正文|文字|标题|text/i, 3);
  const darkBackground = isLight(darkBase.hex) ? "#111827" : darkBase.hex;
  const lightBackground = isLight(lightBase.hex) ? lightBase.hex : "#F8FAFC";
  const darkText = isLight(textColor.hex) ? textColor.hex : "#F8FAFC";
  const lightText = isLight(textColor.hex) ? "#111827" : textColor.hex;
  const informationTone = baseTone === "dark" ? "dark" : baseTone === "light" ? "light" : "light";
  const informationBackground = informationTone === "dark" ? darkBackground : lightBackground;
  const informationText = informationTone === "dark" ? darkText : lightText;
  const informationSurface = informationTone === "dark" ? "rgba(15, 23, 42, 0.72)" : "#FFFFFF";

  return [
    {
      key: "cover",
      label: "封面",
      tone: baseTone === "light" ? "light" : "dark",
      background: baseTone === "light" ? lightBackground : darkBackground,
      accent: primary.hex,
      text: baseTone === "light" ? lightText : darkText,
      surface: accent.hex,
      intensity: "strong",
    },
    {
      key: "section",
      label: "章节",
      tone: baseTone === "light" ? "light" : "dark",
      background: baseTone === "light" ? lightBackground : darkBackground,
      accent: accent.hex,
      text: baseTone === "light" ? lightText : darkText,
      surface: primary.hex,
      intensity: "medium",
    },
    {
      key: "content",
      label: "正文",
      tone: informationTone,
      background: informationBackground,
      accent: primary.hex,
      text: informationText,
      surface: informationSurface,
      intensity: "calm",
    },
    {
      key: "data",
      label: "数据",
      tone: informationTone,
      background: informationBackground,
      accent: accent.hex,
      text: informationText,
      surface: informationSurface,
      intensity: "calm",
    },
  ];
}

export function buildSelectedStylePreview(style: any): SelectedStylePreview {
  const palette = normalizePalette(style?.palette);
  const baseTone = inferBaseTone(style, palette);
  const pages = buildPagePreviews(baseTone, palette);
  const summary = stripHexCodes(
    style?.visual_strategy?.summary ||
      style?.visual_strategy?.content_treatment ||
      style?.description ||
      style?.mood ||
      "这套方案会按页面类型控制视觉强弱，先保证正文和数据页可读。"
  );
  const rhythmText =
    baseTone === "dark"
      ? "封面/章节页放大主色和装饰，正文/数据页保持同一深色基底，用卡片、留白和高对比文字保证阅读。"
      : baseTone === "light"
      ? "封面/章节页保持明亮基底并增强品牌色，正文/数据页降低装饰强度，优先保证信息清晰。"
      : "封面/章节页承担视觉记忆点，正文/数据页降低背景复杂度，保持同一套色彩和层级。";
  const fontText = stripHexCodes(style?.font) || "标题、正文和数据使用同一套清晰字体系，数字和重点信息保持更强对比。";
  return {
    name: stripHexCodes(style?.name) || "视觉方案",
    summary,
    baseTone,
    palette,
    pages,
    rhythmText,
    fontText,
  };
}
```

- [ ] **Step 4: Run the helper test to verify GREEN**

Run:

```bash
cd frontend && node src/selectedStylePreview.test.mjs
```

Expected: PASS with exit code 0.

## Task 2: App And CSS Integration

**Files:**
- Create: `frontend/src/selected-style-expanded-preview.test.mjs`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/index.css`

- [ ] **Step 1: Write the failing integration test**

Create `frontend/src/selected-style-expanded-preview.test.mjs`:

```js
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

for (const label of ["封面", "章节", "正文", "数据"]) {
  assert.match(source, new RegExp(`page\\.label\\}[\\s\\S]{0,400}${label}|${label}[\\s\\S]{0,400}page\\.label`), `${label} page preview should be represented`);
}

for (const className of [
  "pg-style-preview-band",
  "pg-style-page-previews",
  "pg-style-page-mini",
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
assert.match(css, /@media \(max-width: 760px\)[\s\S]*pg-style-page-previews/, "miniatures should wrap on narrow screens");
```

- [ ] **Step 2: Run the integration test to verify RED**

Run:

```bash
cd frontend && node src/selected-style-expanded-preview.test.mjs
```

Expected: FAIL because App does not import or render the new preview helper/classes.

- [ ] **Step 3: Import and compute preview data**

In `frontend/src/App.tsx`, add this import after the workflow imports:

```ts
import { buildSelectedStylePreview } from "./selectedStylePreview";
```

After `styleDockProposals` is defined, add:

```ts
  const selectedStylePreview = selectedProject?.selected_style
    ? buildSelectedStylePreview(selectedProject.selected_style)
    : null;
```

- [ ] **Step 4: Replace the selected style expanded row**

In `frontend/src/App.tsx`, replace the existing `styleBarExpanded` detail block:

```tsx
            {styleBarExpanded && (
              <div className="pg-style-bar-detail">
                <span>氛围：{stripHexCodes(selectedProject.selected_style.mood) || "—"}</span>
                <span>字体：{stripHexCodes(selectedProject.selected_style.font) || "—"}</span>
                {visualStrategyText(selectedProject.selected_style) && (
                  <span>整体基底：{visualStrategyText(selectedProject.selected_style)}</span>
                )}
              </div>
            )}
```

with:

```tsx
            {styleBarExpanded && selectedStylePreview && (
              <div className="pg-style-preview-band">
                <p className="pg-style-preview-summary">{selectedStylePreview.summary}</p>
                <div className="pg-style-page-previews" aria-label="视觉方案页面类型预览">
                  {selectedStylePreview.pages.map((page) => (
                    <div
                      key={page.key}
                      className={`pg-style-page-mini is-${page.tone} is-${page.intensity}`}
                      style={{
                        "--style-page-bg": page.background,
                        "--style-page-accent": page.accent,
                        "--style-page-text": page.text,
                        "--style-page-surface": page.surface,
                      } as CSSProperties}
                    >
                      <span className="pg-style-page-mini-label">{page.label}</span>
                      <i className="pg-style-page-mini-glow" />
                      <i className="pg-style-page-mini-title" />
                      <i className="pg-style-page-mini-line line-1" />
                      <i className="pg-style-page-mini-line line-2" />
                      {page.key === "data" && (
                        <span className="pg-style-page-mini-chart" aria-hidden="true">
                          <i style={{ height: "42%" }} />
                          <i style={{ height: "76%" }} />
                          <i style={{ height: "55%" }} />
                          <i style={{ height: "90%" }} />
                        </span>
                      )}
                    </div>
                  ))}
                </div>
                <div className="pg-style-preview-notes">
                  <div>
                    <b>视觉节奏</b>
                    <p>{selectedStylePreview.rhythmText}</p>
                  </div>
                  <div>
                    <b>字体体系</b>
                    <p>{selectedStylePreview.fontText}</p>
                  </div>
                </div>
              </div>
            )}
```

- [ ] **Step 5: Add CSS for the preview band**

In `frontend/src/index.css`, replace the old `.pg-style-bar-detail` rules with:

```css
.pg-style-preview-band {
  border-top: 1px solid #eef1f5;
  margin-top: 7px;
  padding-top: 10px;
}

.pg-style-preview-summary {
  margin: 0 0 9px;
  color: #475467;
  font-size: 12px;
  line-height: 1.45;
}

.pg-style-page-previews {
  display: grid;
  grid-template-columns: repeat(4, minmax(120px, 1fr));
  gap: 8px;
}

.pg-style-page-mini {
  position: relative;
  overflow: hidden;
  aspect-ratio: 16 / 9;
  min-height: 74px;
  border: 1px solid #dbe4f0;
  border-radius: 8px;
  background:
    radial-gradient(circle at 78% 20%, color-mix(in srgb, var(--style-page-accent) 72%, transparent), transparent 28%),
    linear-gradient(135deg, var(--style-page-bg), color-mix(in srgb, var(--style-page-bg) 76%, var(--style-page-accent)));
  color: var(--style-page-text);
}

.pg-style-page-mini.is-light {
  background:
    radial-gradient(circle at 80% 18%, color-mix(in srgb, var(--style-page-accent) 18%, transparent), transparent 30%),
    linear-gradient(180deg, var(--style-page-bg), color-mix(in srgb, var(--style-page-bg) 90%, #ffffff));
}

.pg-style-page-mini.is-calm {
  background: var(--style-page-bg);
}

.pg-style-page-mini-label {
  position: absolute;
  left: 8px;
  top: 7px;
  z-index: 1;
  color: var(--style-page-text);
  font-size: 10px;
  font-weight: 800;
}

.pg-style-page-mini-glow {
  position: absolute;
  right: -18px;
  top: -24px;
  width: 62px;
  height: 62px;
  border-radius: 999px;
  background: var(--style-page-accent);
  opacity: 0.24;
}

.pg-style-page-mini.is-calm .pg-style-page-mini-glow {
  width: 42px;
  height: 42px;
  opacity: 0.12;
}

.pg-style-page-mini-title,
.pg-style-page-mini-line {
  position: absolute;
  left: 9px;
  border-radius: 999px;
  background: var(--style-page-text);
}

.pg-style-page-mini-title {
  top: 27px;
  width: 50%;
  height: 7px;
}

.pg-style-page-mini-line {
  height: 4px;
  opacity: 0.34;
}

.pg-style-page-mini-line.line-1 {
  top: 43px;
  width: 72%;
}

.pg-style-page-mini-line.line-2 {
  top: 53px;
  width: 56%;
}

.pg-style-page-mini.is-calm::after {
  content: "";
  position: absolute;
  inset: 24px 8px 8px;
  border: 1px solid color-mix(in srgb, var(--style-page-text) 14%, transparent);
  border-radius: 6px;
  background: var(--style-page-surface);
  opacity: 0.72;
}

.pg-style-page-mini.is-calm .pg-style-page-mini-title,
.pg-style-page-mini.is-calm .pg-style-page-mini-line,
.pg-style-page-mini.is-calm .pg-style-page-mini-chart {
  z-index: 1;
}

.pg-style-page-mini-chart {
  position: absolute;
  left: 10px;
  right: 10px;
  bottom: 10px;
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  align-items: end;
  gap: 4px;
  height: 26px;
}

.pg-style-page-mini-chart i {
  border-radius: 3px 3px 0 0;
  background: var(--style-page-accent);
}

.pg-style-preview-notes {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin-top: 9px;
}

.pg-style-preview-notes > div {
  border: 1px solid #e4e9f1;
  border-radius: 8px;
  background: #fbfcfd;
  padding: 8px 9px;
}

.pg-style-preview-notes b {
  display: block;
  margin-bottom: 3px;
  color: #101828;
  font-size: 11px;
  font-weight: 780;
}

.pg-style-preview-notes p {
  margin: 0;
  color: #667085;
  font-size: 11.5px;
  line-height: 1.42;
}
```

In the existing `@media (max-width: 760px)` block, add:

```css
  .pg-style-page-previews,
  .pg-style-preview-notes {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
```

- [ ] **Step 6: Run the integration test to verify GREEN**

Run:

```bash
cd frontend && node src/selected-style-expanded-preview.test.mjs
```

Expected: PASS with exit code 0.

## Task 3: Full Frontend Verification

**Files:**
- Verify: `frontend/src/selectedStylePreview.test.mjs`
- Verify: `frontend/src/selected-style-expanded-preview.test.mjs`
- Verify: `frontend/src/App.tsx`
- Verify: `frontend/src/index.css`

- [ ] **Step 1: Run focused tests**

Run:

```bash
cd frontend && node src/selectedStylePreview.test.mjs && node src/selected-style-expanded-preview.test.mjs
```

Expected: PASS with exit code 0.

- [ ] **Step 2: Run build**

Run:

```bash
cd frontend && npm run build
```

Expected: PASS with exit code 0. TypeScript and Vite build should complete.

- [ ] **Step 3: Review the diff**

Run:

```bash
git diff -- frontend/src/selectedStylePreview.ts frontend/src/selectedStylePreview.test.mjs frontend/src/selected-style-expanded-preview.test.mjs frontend/src/App.tsx frontend/src/index.css
```

Expected: Diff only contains selected-style preview helper, tests, App integration, and CSS for the expanded selected style preview.
