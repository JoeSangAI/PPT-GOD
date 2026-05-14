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

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

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
assert.deepEqual(plain(darkPreview.pages.map((page) => page.label)), ["封面", "章节", "正文", "数据"]);
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
