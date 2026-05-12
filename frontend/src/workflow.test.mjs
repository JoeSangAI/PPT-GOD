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

const { buildGateContext, buildWorkflowState, getPrimaryActionKey } = loadTsModule("workflow.ts");
const { inferAgentRequestContext, inferRequestedPageCount } = loadTsModule("agentRequestContext.ts");

function context(input) {
  return buildGateContext(buildWorkflowState(input), input.revision ?? 0);
}

function plain(value) {
  return JSON.parse(JSON.stringify(value));
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
assert.equal(getPrimaryActionKey(buildWorkflowState({
  projectStatus: "visual_ready",
  slides: [{ status: "pending" }],
  contentPlanConfirmed: true,
  hasSelectedStyle: true,
})), "generate-visual-prompts");

const promptReady = context({
  projectStatus: "prompt_ready",
  slides: [{ status: "prompt_ready", prompt_text: "prompt" }],
  contentPlanConfirmed: true,
  hasSelectedStyle: true,
});
assert.equal(promptReady.gate, "visual_design");
assert.ok(promptReady.allowedActions.includes("start_prototype"));
assert.equal(getPrimaryActionKey(buildWorkflowState({
  projectStatus: "prompt_ready",
  slides: [{ status: "prompt_ready", prompt_text: "prompt" }],
  contentPlanConfirmed: true,
  hasSelectedStyle: true,
})), "start-prototype");

const prototype = context({
  projectStatus: "prototype_ready",
  slides: [{ status: "completed", image_path: "./outputs/demo.png", prompt_text: "prompt" }],
});
assert.equal(prototype.mainStageMode, "deck_prototype");
assert.equal(
  JSON.stringify(prototype.allowedActions.filter((action) => action === "resample_prototype" || action === "confirm_prototype").sort()),
  JSON.stringify(["confirm_prototype", "resample_prototype"])
);

assert.deepEqual(
  plain(inferAgentRequestContext({
    message: "这一页太空了，补一点市场数据",
    activeAgentRole: "visual",
    activeScope: "deck",
    editingPageNum: 3,
    projectStatus: "visual_ready",
    contentPlanConfirmed: true,
  })),
  {
    targetRole: "content",
    scope: "current_slide",
    risk: "safe",
    pageNums: [3],
    explicitScope: true,
    scopeLabel: "第 3 页",
    routeReason: "content_intent",
  }
);

assert.deepEqual(
  plain(inferAgentRequestContext({
    message: "第 3 页背景换成深蓝，标题更亮",
    activeAgentRole: "content",
    activeScope: "deck",
    projectStatus: "planning",
    contentPlanConfirmed: true,
  })),
  {
    targetRole: "visual",
    scope: "current_slide",
    risk: "safe",
    pageNums: [3],
    explicitScope: true,
    scopeLabel: "第 3 页",
    routeReason: "visual_intent",
  }
);

assert.deepEqual(
  plain(inferAgentRequestContext({
    message: "后面所有页面统一暗色，但标题要更亮",
    activeAgentRole: "visual",
    activeScope: "current_slide",
    editingPageNum: 5,
    projectStatus: "prompt_ready",
    contentPlanConfirmed: true,
  })).scope,
  "deck"
);

assert.deepEqual(
  plain(inferAgentRequestContext({
    message: "可以了，出图",
    activeAgentRole: "content",
    activeScope: "deck",
    projectStatus: "prompt_ready",
    contentPlanConfirmed: true,
    hasPrompt: true,
  })),
  {
    targetRole: "visual",
    scope: "deck",
    risk: "cost",
    pageNums: [],
    explicitScope: false,
    scopeLabel: "整套 PPT",
    routeReason: "cost_visual_action",
  }
);

assert.deepEqual(
  plain(inferAgentRequestContext({
    message: "按原文重新做 25 页",
    activeAgentRole: "visual",
    activeScope: "deck",
    projectStatus: "visual_ready",
    contentPlanConfirmed: true,
  })).risk,
  "destructive"
);

assert.deepEqual(
  plain(inferAgentRequestContext({
    message: "以这 14 页 PPT 为基础扩成 60-80 页培训课",
    activeAgentRole: "content",
    activeScope: "deck",
    projectStatus: "planning",
    contentPlanConfirmed: true,
  })).risk,
  "destructive"
);

assert.deepEqual(
  plain(inferAgentRequestContext({
    message: "把这份材料做成60页并出图",
    activeAgentRole: "visual",
    activeScope: "deck",
    projectStatus: "draft",
    contentPlanConfirmed: false,
  })).targetRole,
  "content"
);

assert.deepEqual(
  plain(inferAgentRequestContext({
    message: "P1-P10 都改得更商务",
    activeAgentRole: "visual",
    activeScope: "deck",
    projectStatus: "visual_ready",
    contentPlanConfirmed: true,
  })).scopeLabel,
  "第 1, 2, 3, 4, 5, 6, 7, 8, 9, 10 页"
);

assert.deepEqual(
  plain(inferAgentRequestContext({
    message: "3-5页标题都改短",
    activeAgentRole: "content",
    activeScope: "deck",
    projectStatus: "planning",
    contentPlanConfirmed: false,
  })).scopeLabel,
  "第 3, 4, 5 页"
);

assert.deepEqual(
  plain(inferAgentRequestContext({
    message: "这些页都换成更商务的版式",
    activeAgentRole: "visual",
    activeScope: "deck",
    selectedPageNums: [2, 4, 5],
    projectStatus: "visual_ready",
    contentPlanConfirmed: true,
  })).scopeLabel,
  "第 2, 4, 5 页"
);

assert.equal(
  inferRequestedPageCount("把这个 MD 文件做成 60 到 80 页的 PPT，给大连混沌学员讲 1.5 小时"),
  80
);
assert.equal(inferRequestedPageCount("重新做 25 页，按原文展开"), 25);
assert.equal(inferRequestedPageCount("页数控制在 60-80，适合 90 分钟内训"), 80);
assert.equal(inferRequestedPageCount("做成60页到80页的PPT"), 80);
assert.equal(inferRequestedPageCount("不少于 60 页，不超过 80 页，做成课程课件"), 80);
assert.equal(inferRequestedPageCount("不要超过80页"), 80);
assert.equal(inferRequestedPageCount("最多 80 页，至少 60 页"), 80);
assert.equal(inferRequestedPageCount("做 120-150 页，越细越好"), 150);
assert.equal(inferRequestedPageCount("第 3 页标题更锐利"), undefined);
assert.equal(inferRequestedPageCount("P12 页标题改小"), undefined);
assert.equal(inferRequestedPageCount("12页标题改小"), undefined);
assert.equal(inferRequestedPageCount("Make this into 60-80 slides for a workshop"), 80);

console.log("workflow gate tests passed");
