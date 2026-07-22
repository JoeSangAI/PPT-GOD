import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import vm from "node:vm";
import ts from "typescript";
import { marked } from "marked";

function loadMarkdownEmphasis() {
  const sourcePath = join(import.meta.dirname, "markdownEmphasis.ts");
  const compiled = ts.transpileModule(readFileSync(sourcePath, "utf8"), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  const sandbox = { exports: {}, module: { exports: {} } };
  sandbox.module.exports = sandbox.exports;
  vm.runInNewContext(compiled, sandbox, { filename: sourcePath });
  return sandbox.module.exports;
}

const { fixMarkedBoldHtml } = loadMarkdownEmphasis();
const parseAndFix = (markdown) => fixMarkedBoldHtml(marked.parse(markdown));

const appSource = readFileSync(join(import.meta.dirname, "App.tsx"), "utf8");
assert.match(
  appSource,
  /const markdownToEditorHtml[\s\S]*?const parsedHtml = [\s\S]*?const html = fixMarkedBoldHtml\(parsedHtml\);[\s\S]*?DOMPurify\.sanitize\(html/,
  "The rich-text editor conversion path must apply the bold compatibility repair before sanitizing HTML",
);

assert.equal(
  parseAndFix("**样本：**19 篇笔记"),
  "<p><strong>样本：</strong>19 篇笔记</p>\n",
  "Chinese labels must render bold when text follows the closing delimiter immediately",
);
assert.equal(
  parseAndFix("**Label:**text"),
  "<p><strong>Label:</strong>text</p>\n",
  "English text immediately following a closing delimiter must not leave literal asterisks",
);
assert.equal(
  parseAndFix("**标签：**正文"),
  "<p><strong>标签：</strong>正文</p>\n",
  "Chinese text immediately following a closing delimiter must not leave literal asterisks",
);
assert.equal(
  parseAndFix("**普通粗体** 后文"),
  "<p><strong>普通粗体</strong> 后文</p>\n",
  "Markdown that marked already parses correctly must remain unchanged",
);
assert.equal(
  parseAndFix("**未配对正文"),
  "<p>**未配对正文</p>\n",
  "An unmatched opening delimiter must remain literal instead of being misidentified as bold",
);
assert.equal(
  parseAndFix("前文**未配对"),
  "<p>前文**未配对</p>\n",
  "An unmatched delimiter in the middle of text must remain literal",
);

console.log("markdown emphasis regression tests passed");
