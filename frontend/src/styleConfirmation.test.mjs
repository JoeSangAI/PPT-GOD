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

const { resolveStyleForConfirmation } = loadTsModule("styleConfirmation.ts");

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

const recommended = {
  name: "推荐视觉方向",
  decision_label: "推荐",
  palette: [{ name: "黑", hex: "#111111", role: "背景" }],
  mood: "克制、专业",
};
const alternate = {
  name: "备选视觉方向",
  decision_label: "方案 2",
  palette: [{ name: "白", hex: "#FFFFFF", role: "背景" }],
  mood: "清爽、明亮",
};

const project = {
  selected_style: null,
  style_proposal: { proposals: [recommended, alternate] },
};

assert.deepEqual(
  plain(resolveStyleForConfirmation(undefined, project, []).style),
  recommended,
  "missing Agent style payload should select the recommended proposal"
);

assert.deepEqual(
  plain(resolveStyleForConfirmation("直接继续吧", project, []).style),
  recommended,
  "direct continue instruction should select the recommended proposal"
);

assert.deepEqual(
  plain(resolveStyleForConfirmation("备选视觉方向", project, []).style),
  alternate,
  "style-name payload should match the corresponding proposal"
);

assert.deepEqual(
  plain(resolveStyleForConfirmation("方案2", project, []).style),
  alternate,
  "numbered payload should match the corresponding proposal"
);

assert.equal(
  resolveStyleForConfirmation({ name: "自定义方案", palette: [] }, project, []).style.name,
  "自定义方案",
  "complete object payload should be used directly"
);

const unresolved = resolveStyleForConfirmation("不存在的方案", { selected_style: null, style_proposal: null }, []);
assert.equal(unresolved.style, null);
assert.match(unresolved.message, /先生成视觉方向/);

console.log("Style confirmation tests passed.");
