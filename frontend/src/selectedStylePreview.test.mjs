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

const darkInformationPagesPreview = buildSelectedStylePreview({
  name: "禅灰极简（金色点缀）",
  palette: [
    { name: "曜石黑", hex: "#090B10", role: "整套页面背景/内容页深色基底" },
    { name: "分众金", hex: "#C7A348", role: "Logo 呼应色/关键数字和装饰线点缀" },
    { name: "雾白", hex: "#F4F4F0", role: "正文和图表文字" },
    { name: "冷灰", hex: "#D7DBE1", role: "辅助线" },
  ],
  description: "按用户最新要求，分众金必须进入配色系统，但只做少量点缀。",
  page_type_adaptation: "正文/内容/数据/表格页也必须保持黑色或深色底，不得自动切换成白底、米白底或浅色信息基底。",
  content_style_hint: "用户明确要求内容页也以黑色/深色底为主。",
});

assert.equal(darkInformationPagesPreview.baseTone, "dark");
assert.equal(darkInformationPagesPreview.pages.find((page) => page.key === "content").tone, "dark");
assert.equal(darkInformationPagesPreview.pages.find((page) => page.key === "data").tone, "dark");
assert.equal(darkInformationPagesPreview.pages.find((page) => page.key === "content").accent, "#C7A348");
assert.equal(darkInformationPagesPreview.pages.find((page) => page.key === "data").accent, "#C7A348");

const mixedLightInformationPagesPreview = buildSelectedStylePreview({
  name: "蓝金沉稳",
  palette: [
    { name: "深海军蓝", hex: "#1B3A5C", role: "主色/背景色/标题色" },
    { name: "品牌金", hex: "#D3BC8E", role: "Logo 呼应色/关键数字和装饰线点缀" },
    { name: "雾灰蓝", hex: "#E8EDF2", role: "内容页基底/卡片底" },
    { name: "炭灰", hex: "#3D3D3D", role: "正文/数据文字" },
  ],
  description: "封面/章节页大胆使用深蓝底，内容/数据页以雾灰蓝为基底，降低背景强度，保证信息可读性。",
  visual_strategy: {
    summary: "品牌金仅作低占比品牌点缀。",
  },
});

assert.equal(mixedLightInformationPagesPreview.baseTone, "mixed");
assert.equal(mixedLightInformationPagesPreview.pages.find((page) => page.key === "cover").tone, "dark");
assert.equal(mixedLightInformationPagesPreview.pages.find((page) => page.key === "content").tone, "light");
assert.equal(mixedLightInformationPagesPreview.pages.find((page) => page.key === "data").tone, "light");

const brightLogoGoldPreview = buildSelectedStylePreview({
  name: "明亮精密工业蓝金",
  palette: [
    { name: "米白", hex: "#F8F6F0", role: "白色/米白/浅色明亮基底" },
    { name: "品牌金", hex: "#C4A25A", role: "Logo 色/品牌识别色（低占比）" },
    { name: "明亮柔紫", hex: "#D9CBE8", role: "辅助强调色/标题强调/视觉锚点" },
    { name: "浅粉", hex: "#E6C7D7", role: "辅助装饰色" },
    { name: "工业蓝黑", hex: "#203A5F", role: "正文/标题文字" },
  ],
  visual_strategy: {
    base_tone: "light",
    summary: "整体以白色/米白/浅色明亮基底为主；使用明亮柔紫作为品牌主色，品牌金仅作低占比品牌点缀。",
  },
});

assert.equal(brightLogoGoldPreview.baseTone, "light");
assert.equal(brightLogoGoldPreview.pages.find((page) => page.key === "cover").background, "#F8F6F0");
assert.equal(brightLogoGoldPreview.pages.find((page) => page.key === "cover").brand, "#C4A25A");
assert.equal(brightLogoGoldPreview.pages.find((page) => page.key === "cover").accent, "#D9CBE8");
assert.equal(brightLogoGoldPreview.pages.find((page) => page.key === "section").accent, "#D9CBE8");
assert.equal(brightLogoGoldPreview.pages.find((page) => page.key === "content").accent, "#D9CBE8");
assert.equal(brightLogoGoldPreview.pages.find((page) => page.key === "data").accent, "#D9CBE8");
assert.equal(brightLogoGoldPreview.pages.find((page) => page.key === "content").text, "#203A5F");
assert.match(brightLogoGoldPreview.summary, /品牌金作为品牌识别色/);
assert.doesNotMatch(brightLogoGoldPreview.summary, /柔紫作为品牌主色/);

const redLogoBlueAccentPreview = buildSelectedStylePreview({
  name: "明亮品牌红蓝",
  palette: [
    { name: "瓷白", hex: "#FBFAF7", role: "整套页面浅色基底" },
    { name: "品牌红", hex: "#E60012", role: "Logo 色/品牌识别色（低占比）" },
    { name: "清透蓝", hex: "#2F80ED", role: "辅助强调色/图表主强调/视觉锚点" },
    { name: "墨灰", hex: "#263238", role: "正文/图表文字" },
  ],
  visual_strategy: {
    base_tone: "light",
    summary: "清透蓝定调品牌识别和主装饰，品牌红仅作低占比品牌点缀。",
  },
});

assert.equal(redLogoBlueAccentPreview.pages.find((page) => page.key === "cover").brand, "#E60012");
assert.equal(redLogoBlueAccentPreview.pages.find((page) => page.key === "cover").accent, "#2F80ED");
assert.equal(redLogoBlueAccentPreview.pages.find((page) => page.key === "data").accent, "#2F80ED");
assert.match(redLogoBlueAccentPreview.summary, /品牌红作为品牌识别色/);
assert.doesNotMatch(redLogoBlueAccentPreview.summary, /清透蓝定调品牌识别/);

const trueBrandPrimaryPreview = buildSelectedStylePreview({
  name: "蓝色品牌",
  palette: [
    { name: "科技蓝", hex: "#0066CC", role: "品牌主色/标题强调" },
    { name: "白色", hex: "#FFFFFF", role: "页面基底" },
    { name: "灰蓝", hex: "#E8EEF6", role: "内容区底色" },
    { name: "深灰", hex: "#1F2937", role: "正文文字" },
  ],
  visual_strategy: {
    base_tone: "light",
    summary: "科技蓝作为品牌主色，整套页面保持白底高可读。",
  },
});

assert.equal(trueBrandPrimaryPreview.pages.find((page) => page.key === "cover").brand, "#0066CC");
assert.equal(trueBrandPrimaryPreview.pages.find((page) => page.key === "cover").accent, "#0066CC");
assert.match(trueBrandPrimaryPreview.summary, /科技蓝作为品牌主色/);

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
