import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import vm from "node:vm";
import ts from "typescript";

const sourcePath = join(import.meta.dirname, "overlayPlacement.ts");
const compiled = ts.transpileModule(readFileSync(sourcePath, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const sandbox = { exports: {}, module: { exports: {} } };
sandbox.module.exports = sandbox.exports;
vm.runInNewContext(compiled, sandbox, { filename: sourcePath });

const { OVERLAY_PRESET_BOXES, overlayPlacementStyle } = sandbox.module.exports;

assert.equal(Object.keys(OVERLAY_PRESET_BOXES).length, 19);
assert.deepEqual(
  JSON.parse(JSON.stringify(overlayPlacementStyle({ preset: "primary-left", mode: "exact_card" }))),
  { left: "9%", top: "24%", width: "46%", height: "58%" },
);
assert.deepEqual(
  JSON.parse(JSON.stringify(overlayPlacementStyle({
    preset: "primary-left",
    mode: "exact_card",
    resolved_overlay_box: {
      left: 0.03,
      top: 0.19,
      width: 0.38,
      height: 0.52,
      source_preset: "primary-left",
      source_mode: "exact_card",
    },
  }))),
  { left: "3%", top: "19%", width: "38%", height: "52%" },
);
assert.equal(
  overlayPlacementStyle({
    preset: "right-card",
    mode: "exact_card",
    resolved_overlay_box: {
      left: 0.03,
      top: 0.19,
      width: 0.38,
      height: 0.52,
      source_preset: "primary-left",
      source_mode: "exact_card",
    },
  }).left,
  "59.5%",
);
