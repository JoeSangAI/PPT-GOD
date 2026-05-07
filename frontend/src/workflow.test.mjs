import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import vm from "node:vm";
import ts from "typescript";

const sourcePath = join(import.meta.dirname, "workflow.ts");
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

const { buildGateContext, buildWorkflowState } = sandbox.module.exports;

function context(input) {
  return buildGateContext(buildWorkflowState(input), input.revision ?? 0);
}

assert.equal(context({ projectStatus: "draft" }).mainStageMode, "brief_studio");

const content = context({
  projectStatus: "planning",
  slides: [{ status: "pending" }],
  contentPlanConfirmed: false,
});
assert.equal(content.gate, "content");
assert.equal(content.mainStageMode, "deck_content");
assert.ok(content.allowedActions.includes("confirm_content"));
assert.ok(!content.allowedActions.includes("start_generation"));

const style = context({
  projectStatus: "visual_ready",
  slides: [{ status: "pending" }],
  contentPlanConfirmed: true,
  hasSelectedStyle: false,
});
assert.equal(style.gate, "visual");
assert.equal(style.mainStageMode, "deck_style");
assert.ok(style.allowedActions.includes("generate_style_proposals"));
assert.ok(!style.allowedActions.includes("start_prototype"));

const selectedStyleWithoutPrompt = context({
  projectStatus: "visual_ready",
  slides: [{ status: "pending" }],
  contentPlanConfirmed: true,
  hasSelectedStyle: true,
});
assert.equal(selectedStyleWithoutPrompt.gate, "visual_design");
assert.equal(selectedStyleWithoutPrompt.mainStageMode, "deck_visual");
assert.ok(selectedStyleWithoutPrompt.allowedActions.includes("generate_visual_prompts"));
assert.ok(!selectedStyleWithoutPrompt.allowedActions.includes("start_prototype"));
assert.ok(!selectedStyleWithoutPrompt.allowedActions.includes("start_generation"));

const promptReady = context({
  projectStatus: "prompt_ready",
  slides: [{ status: "prompt_ready", prompt_text: "prompt" }],
  contentPlanConfirmed: true,
  hasSelectedStyle: true,
});
assert.equal(promptReady.gate, "visual_design");
assert.ok(promptReady.allowedActions.includes("start_prototype"));

const prototype = context({
  projectStatus: "prototype_ready",
  slides: [{ status: "completed", image_path: "./outputs/demo.png", prompt_text: "prompt" }],
});
assert.equal(prototype.mainStageMode, "deck_prototype");
assert.equal(
  JSON.stringify(prototype.allowedActions.filter((action) => action === "resample_prototype" || action === "confirm_prototype").sort()),
  JSON.stringify(["confirm_prototype", "resample_prototype"])
);

console.log("workflow gate tests passed");
