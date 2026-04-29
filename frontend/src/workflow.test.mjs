import assert from "node:assert/strict";
import {
  buildWorkflowState,
  getGuidanceText,
  getPrimaryActionKey,
  getSecondaryActionKeys,
} from "./workflow.js";

const base = {
  projectStatus: "prompt_ready",
  slides: [{ status: "prompt_ready", prompt_text: "p", image_path: null }],
  contentPlanConfirmed: true,
  showPrototypePreview: true,
  selectedPageCount: 0,
  staleSummary: { hasContentOrVisualStale: false, imageStaleCount: 0 },
  templatePageCount: 0,
  isBusy: false,
};

assert.equal(buildWorkflowState(base).stepIndex, 2);
assert.equal(getPrimaryActionKey(buildWorkflowState(base)), "start-prototype");
assert.deepEqual(getSecondaryActionKeys(buildWorkflowState(base)), ["generate-all"]);

const prototype = buildWorkflowState({
  ...base,
  projectStatus: "prototype_ready",
  slides: [{ status: "completed", prompt_text: "p", image_path: "./x.png" }],
  showPrototypePreview: true,
});
assert.equal(prototype.stepIndex, 3);
assert.equal(prototype.viewLabel, "打样结果");
assert.equal(getPrimaryActionKey(prototype), "confirm-prototype");
assert.deepEqual(getSecondaryActionKeys(prototype), ["toggle-prototype-view", "resample"]);
assert.match(getGuidanceText(prototype), /种子页已生成/);

const globalView = buildWorkflowState({
  ...base,
  projectStatus: "prototype_ready",
  slides: [{ status: "completed", prompt_text: "p", image_path: "./x.png" }],
  showPrototypePreview: false,
});
assert.equal(globalView.viewLabel, "全局预览");

const failed = buildWorkflowState({
  ...base,
  projectStatus: "failed",
  slides: [{ status: "failed", prompt_text: "p", image_path: null }],
});
assert.equal(failed.stepIndex, 2);
assert.equal(getPrimaryActionKey(failed), "start-prototype");
assert.deepEqual(getSecondaryActionKeys(failed), ["generate-all", "retry-failed"]);

console.log("workflow tests passed");
