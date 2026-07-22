import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import vm from "node:vm";
import ts from "typescript";

const sourcePath = join(import.meta.dirname, "api/client.ts");
const source = readFileSync(sourcePath, "utf8").replaceAll("import.meta.env", "__env");
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const storage = { getItem: () => null, setItem() {}, removeItem() {} };
const sandbox = {
  __env: { DEV: false, PROD: true },
  exports: {},
  module: { exports: {} },
  localStorage: storage,
  sessionStorage: storage,
  window: { location: { origin: "http://localhost:8000" }, fetch() {} },
};
sandbox.module.exports = sandbox.exports;
vm.runInNewContext(compiled, sandbox, { filename: sourcePath });

const htmlFallback = {
  ok: true,
  status: 200,
  headers: { get: () => "text/html; charset=utf-8" },
};

await assert.rejects(
  sandbox.module.exports.checkRes(htmlFallback),
  /本地服务正在更新.*重新双击/,
  "a successful HTML fallback must become a beginner-friendly restart instruction instead of a JSON parse error",
);

console.log("API response regression checks passed");
