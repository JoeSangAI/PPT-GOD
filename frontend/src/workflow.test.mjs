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

const {
  buildGateContext,
  buildWorkflowState,
  buildWorkflowProgressDisclosure,
  evaluateImageGenerationOutcome,
  getWorkflowProgressOverviewDisplay,
  getPrimaryActionKey,
  getStatusCard,
  planStaleSlideAction,
} = loadTsModule("workflow.ts");
const { inferAgentRequestContext, inferRequestedPageCount } = loadTsModule("agentRequestContext.ts");
const {
  buildChangeReceipt,
  formatPageNumsForReceipt,
  summarizeContentChange,
  summarizeVisualChange,
  summarizeInsertedSlide,
} = loadTsModule("changeReceipt.ts");

function context(input) {
  return buildGateContext(buildWorkflowState(input), input.revision ?? 0);
}

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

assert.equal(context({ projectStatus: "draft" }).mainStageMode, "brief_studio");

assert.equal(formatPageNumsForReceipt([1, 3, 2]), "第 1, 2, 3 页");
assert.equal(
  buildChangeReceipt({
    status: "applied",
    subject: "已更新第 1 页画面描述，并同步了生图提示词",
    change: "提升文字对比度和可读性",
    next: "图片未自动重生成，请检查后再确认生图。",
  }),
  "✅ 已更新第 1 页画面描述，并同步了生图提示词。\n\n变更：提升文字对比度和可读性\n\n下一步：图片未自动重生成，请检查后再确认生图。"
);
assert.equal(
  summarizeContentChange({
    text_content: {
      headline: "新标题",
      subhead: "新副标题",
      body: "第一行\n第二行",
    },
  }),
  "标题：新标题；副标题：新副标题；正文：第一行"
);
assert.equal(
  summarizeVisualChange(
    "封面黑色的字体看不清楚",
    { visual_json: { design_notes: "本轮修改：提升文字对比度。" } },
    "已写入第 1 页"
  ),
  "本轮修改：提升文字对比度。"
);
assert.equal(
  summarizeInsertedSlide({ text_content: { headline: "新增案例" } }),
  "新增页面：新增案例"
);

const runningImageDisclosure = buildWorkflowProgressDisclosure({
  active_run: {
    kind: "batch_generation",
    status: "running",
    started_at: "2026-05-14T05:00:00.000Z",
    updated_at: "2026-05-14T05:01:20.000Z",
    target_page_nums: [2, 3, 4],
    total_count: 3,
    completed_count: 1,
  },
  progress: {
    kind: "batch_generation",
    status: "running",
    label: "批量生成进度",
    message: "正在生成图片",
    current: 1,
    total: 3,
    unit: "页",
    active_page_nums: [3],
  },
}, Date.parse("2026-05-14T05:01:30.000Z"));

assert.equal(runningImageDisclosure.headline, "批量生成进度");
assert.equal(runningImageDisclosure.summary, "正在生成第 3 页：1 / 3 页完成");
assert.equal(runningImageDisclosure.detail, "第 3 页正在生成；完成后会直接出现在画布中。");
assert.deepEqual(
  plain(runningImageDisclosure.metrics.map((item) => [item.label, item.value])),
  [
    ["进度", "1 / 3 页"],
    ["处理范围", "第 2、3、4 页"],
    ["已运行", "1 分 30 秒"],
  ]
);
assert.deepEqual(
  plain(runningImageDisclosure.steps.map((step) => [step.label, step.status])),
  [
    ["准备页面", "done"],
    ["生成图片", "current"],
    ["写入画布", "pending"],
  ]
);
assert.deepEqual(
  plain(getWorkflowProgressOverviewDisplay(runningImageDisclosure, "agent")),
  {
    showHeaderCopy: false,
    metrics: [],
    showSteps: false,
    showStandaloneTitle: false,
    showFooterSummary: false,
    showAgentCopy: false,
  },
  "agent progress overview should stay compact and keep only the bar"
);
assert.deepEqual(
  plain(getWorkflowProgressOverviewDisplay(runningImageDisclosure, "empty")),
  {
    showHeaderCopy: true,
    metrics: plain(runningImageDisclosure.metrics),
    showSteps: true,
    showStandaloneTitle: false,
    showFooterSummary: false,
    showAgentCopy: false,
  },
  "main empty progress state should rely on the progress card instead of repeating the title and summary outside it"
);

const queuedDisclosure = buildWorkflowProgressDisclosure({
  active_run: {
    kind: "content_plan",
    status: "queued",
    started_at: "2026-05-14T05:00:00.000Z",
    total_count: 50,
    completed_count: 0,
  },
}, Date.parse("2026-05-14T05:00:42.000Z"));

assert.equal(queuedDisclosure.summary, "内容规划已排队，等待开始，已等待 42 秒");
assert.equal(queuedDisclosure.detail, "任务已提交，正在等待后台开始；页面会自动刷新进度。");
assert.equal(queuedDisclosure.steps[0].status, "current");

const visualPromptDisclosure = buildWorkflowProgressDisclosure({
  active_run: {
    kind: "visual_prompts",
    status: "running",
    started_at: "2026-05-14T14:01:58.000Z",
    updated_at: "2026-05-14T14:03:56.000Z",
    total_count: 26,
    completed_count: 21,
  },
  progress: {
    kind: "visual_prompts",
    status: "running",
    stage: "visual_planning",
    message: "正在生成视觉方案",
    current: 21,
    total: 26,
    unit: "页",
    updated_at: "2026-05-14T14:03:56.000Z",
  },
}, Date.parse("2026-05-14T14:04:05.000Z"));

assert.equal(visualPromptDisclosure.headline, "画面方案进度");
assert.equal(visualPromptDisclosure.summary, "正在生成每页画面方案：21 / 26 页完成");
assert.equal(visualPromptDisclosure.detail, "正在把每页内容转换成可生成样张的画面方案；完成后每页会出现检查项。");
assert.deepEqual(
  plain(visualPromptDisclosure.metrics.map((item) => [item.label, item.value])),
  [
    ["进度", "21 / 26 页"],
    ["已运行", "2 分 7 秒"],
  ]
);

const editableState = buildWorkflowState({
  projectStatus: "completed",
  slides: [{ status: "completed", image_path: "/slide-1.png", prompt_text: "page" }],
  activeRun: {
    kind: "editable_pptx",
    status: "running",
    total_count: 3,
    completed_count: 1,
  },
});
assert.equal(editableState.steps[editableState.steps.length - 1].label, "可编辑版");
assert.equal(editableState.stepIndex, 5);
assert.equal(editableState.stepStatuses[4], "done");
assert.equal(editableState.stepStatuses[5], "current");

const editableDisclosure = buildWorkflowProgressDisclosure({
  active_run: {
    kind: "editable_pptx",
    status: "running",
    started_at: "2026-05-14T05:00:00.000Z",
    updated_at: "2026-05-14T05:01:05.000Z",
    total_count: 58,
    completed_count: 12,
  },
}, Date.parse("2026-05-14T05:01:20.000Z"));

assert.equal(editableDisclosure.headline, "可编辑版生成进度");
assert.equal(editableDisclosure.summary, "正在生成可编辑版：12 / 58 页完成");
assert.equal(editableDisclosure.detail, "正在重新解析页面文字与图层；完成后会自动下载可编辑版 PPTX。");
assert.deepEqual(
  plain(editableDisclosure.steps.map((step) => [step.label, step.status])),
  [
    ["解析页面", "done"],
    ["还原图层", "current"],
    ["生成 PPTX", "pending"],
  ]
);

const staleUpdateDisclosure = buildWorkflowProgressDisclosure({
  active_run: {
    kind: "content_plan",
    status: "running",
    started_at: "2026-05-14T05:00:00.000Z",
    updated_at: "2026-05-14T05:01:00.000Z",
    total_count: 30,
    completed_count: 15,
  },
}, Date.parse("2026-05-14T05:01:42.000Z"));
assert.ok(
  staleUpdateDisclosure.metrics.some((item) => item.label === "最近更新" && item.value === "42 秒前"),
  "recent update should only appear once the backend has been quiet long enough to matter"
);

const content = context({
  projectStatus: "planning",
  slides: [{ status: "pending" }],
  contentPlanConfirmed: false,
});
assert.equal(content.gate, "content");
assert.equal(content.mainStageMode, "deck_content");
assert.ok(content.allowedActions.includes("confirm_content"));
assert.ok(!content.allowedActions.includes("start_generation"));

const contentWithTemplate = context({
  projectStatus: "planning",
  slides: [{ status: "pending" }],
  contentPlanConfirmed: false,
  templatePageCount: 6,
});
assert.ok(!contentWithTemplate.allowedActions.includes("templates"));

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

const gateDispatchedStatusActions = {
  "retry-failed": "retry_failed",
  "continue-generation": "start_generation",
  "confirm-prototype": "confirm_prototype",
  "resample-prototype": "resample_prototype",
  "start-prototype": "start_prototype",
  "generate-style": "generate_style_proposals",
  "generate-visual-prompts": "generate_visual_prompts",
  "switch-to-visual": "switch_to_visual",
  "start-generation": "start_generation",
  download: "download",
};

for (const { name, input, cardInput } of [
  {
    name: "prompt-ready partial deck can continue remaining pages",
    input: {
      projectStatus: "prompt_ready",
      slides: [
        { page_num: 1, status: "completed", image_path: "./outputs/1.png", prompt_text: "prompt" },
        { page_num: 2, status: "prompt_ready", prompt_text: "prompt" },
      ],
      contentPlanConfirmed: true,
      hasSelectedStyle: true,
    },
    cardInput: { incompletePageNums: [2], completedSlideCount: 1, totalSlideCount: 2 },
  },
  {
    name: "prototype-ready deck can confirm sample",
    input: {
      projectStatus: "prototype_ready",
      slides: [{ page_num: 1, status: "completed", image_path: "./outputs/1.png", prompt_text: "prompt" }],
      contentPlanConfirmed: true,
      hasSelectedStyle: true,
    },
    cardInput: { visiblePrototypePageNums: [1], completedSlideCount: 1, totalSlideCount: 1 },
  },
  {
    name: "visual stage can generate style",
    input: {
      projectStatus: "visual_ready",
      slides: [{ page_num: 1, status: "pending" }],
      contentPlanConfirmed: true,
      hasSelectedStyle: false,
    },
    cardInput: { totalSlideCount: 1 },
  },
  {
    name: "selected style stage can generate visual prompts",
    input: {
      projectStatus: "visual_ready",
      slides: [{ page_num: 1, status: "pending" }],
      contentPlanConfirmed: true,
      hasSelectedStyle: true,
    },
    cardInput: { totalSlideCount: 1 },
  },
  {
    name: "completed deck can download",
    input: {
      projectStatus: "completed",
      slides: [{ page_num: 1, status: "completed", image_path: "./outputs/1.png", prompt_text: "prompt" }],
      contentPlanConfirmed: true,
      hasSelectedStyle: true,
    },
    cardInput: { completedSlideCount: 1, totalSlideCount: 1 },
  },
]) {
  const state = buildWorkflowState(input);
  const card = getStatusCard({
    workflowState: state,
    staleActionPlan: planStaleSlideAction([]),
    failedPageNums: [],
    incompletePageNums: [],
    visiblePrototypePageNums: [],
    resamplePageNums: [],
    prototypePromptTargetCount: 1,
    completedSlideCount: 0,
    totalSlideCount: 0,
    ...cardInput,
  });
  const action = gateDispatchedStatusActions[card?.primary?.key];
  if (action) {
    assert.ok(
      buildGateContext(state).allowedActions.includes(action),
      `${name}: visible status-card action ${card.primary.key} must be allowed by the current gate`
    );
  }
}

const imageOnlyStaleAction = planStaleSlideAction([
  { slide: { id: "s1", page_num: 1 }, stale: { image: true } },
  { slide: { id: "s2", page_num: 2 }, stale: { image: true } },
]);
assert.equal(imageOnlyStaleAction.primaryActionKey, "regenerate_images");
assert.equal(imageOnlyStaleAction.primaryLabel, "重新生成图片");
assert.equal(imageOnlyStaleAction.contentOrVisualCount, 0);
assert.equal(imageOnlyStaleAction.imageOnlyCount, 2);

const mixedStaleAction = planStaleSlideAction([
  { slide: { id: "s1", page_num: 1 }, stale: { content: true, image: true } },
  { slide: { id: "s2", page_num: 2 }, stale: { visual: true, image: true } },
  { slide: { id: "s3", page_num: 3 }, stale: { image: true } },
]);
assert.equal(mixedStaleAction.primaryActionKey, "update_visual_plan");
assert.equal(mixedStaleAction.primaryLabel, "更新画面方案");
assert.deepEqual(plain(mixedStaleAction.pageNumsForVisualPlan), [1]);
assert.deepEqual(plain(mixedStaleAction.pageNumsForPrompt), [1, 2]);
assert.deepEqual(plain(mixedStaleAction.imageOnlyPageNums), [3]);

const prototype = context({
  projectStatus: "prototype_ready",
  slides: [{ status: "completed", image_path: "./outputs/demo.png", prompt_text: "prompt" }],
});
assert.equal(prototype.mainStageMode, "deck_prototype");
assert.equal(
  JSON.stringify(prototype.allowedActions.filter((action) => action === "resample_prototype" || action === "confirm_prototype").sort()),
  JSON.stringify(["confirm_prototype", "resample_prototype"])
);

const prototypeWithRemainingPagesState = buildWorkflowState({
  projectStatus: "prototype_ready",
  slides: [
    { status: "completed", image_path: "./outputs/1.png", prompt_text: "prompt" },
    { status: "completed", image_path: "./outputs/2.png", prompt_text: "prompt" },
    { status: "completed", image_path: "./outputs/3.png", prompt_text: "prompt" },
    { page_num: 4, status: "prompt_ready", prompt_text: "prompt" },
    { page_num: 5, status: "prompt_ready", prompt_text: "prompt" },
  ],
  contentPlanConfirmed: true,
  hasSelectedStyle: true,
});
const prototypeWithRemainingPagesCard = getStatusCard({
  workflowState: prototypeWithRemainingPagesState,
  staleActionPlan: planStaleSlideAction([]),
  failedPageNums: [],
  incompletePageNums: [4, 5],
  visiblePrototypePageNums: [1, 2, 3],
  resamplePageNums: [],
  prototypePromptTargetCount: 3,
  completedSlideCount: 3,
  totalSlideCount: 5,
});
assert.equal(
  prototypeWithRemainingPagesCard.primary.key,
  "confirm-prototype",
  "prototype-ready decks with remaining pages must confirm the sample before full generation instead of using interruption resume"
);
assert.doesNotMatch(
  getStatusCard({
    workflowState: prototypeWithRemainingPagesState,
    staleActionPlan: planStaleSlideAction([]),
    failedPageNums: [],
    incompletePageNums: [4, 5],
    visiblePrototypePageNums: [1, 2, 3],
    resamplePageNums: [1, 2, 3],
    prototypePromptTargetCount: 3,
    completedSlideCount: 3,
    totalSlideCount: 5,
  }).description,
  /已勾选.*重打|不勾选/,
  "prototype-ready copy must not imply the user already selected pages to reroll just because sample pages are visible"
);
assert.match(
  prototypeWithRemainingPagesCard.description,
  /样张满意/,
  "prototype-ready guidance should name the visible full-generation CTA"
);
assert.doesNotMatch(
  prototypeWithRemainingPagesCard.description,
  /生成全部页面/,
  "prototype-ready guidance should not point to a generic button that is not visible"
);

const cancelledPrototypeOutcome = evaluateImageGenerationOutcome({
  prototype: true,
  projectStatus: "prototype_ready",
  run: {
    id: "run-cancelled",
    kind: "prototype_generation",
    status: "cancelled",
    message: "用户手动停止",
    total_count: 3,
    completed_count: 0,
    failed_count: 0,
    target_page_nums: [1, 2, 5],
  },
});
assert.equal(cancelledPrototypeOutcome.kind, "cancelled");
assert.equal(cancelledPrototypeOutcome.isSuccess, false);
assert.equal(cancelledPrototypeOutcome.canConfirmPrototype, false);
assert.match(cancelledPrototypeOutcome.message, /没有生成新样张/);

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
    targetArea: "body",
    areaLabel: "正文",
    confidence: "explicit",
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
    targetArea: "visual",
    areaLabel: "画面",
    confidence: "explicit",
    pageNums: [3],
    explicitScope: true,
    scopeLabel: "第 3 页",
    routeReason: "visual_intent",
  }
);

assert.deepEqual(
  plain(inferAgentRequestContext({
    message: "封面黑色的字体看不清楚",
    activeAgentRole: "visual",
    activeScope: "deck",
    projectStatus: "prototype_ready",
    slideCount: 35,
    contentPlanConfirmed: true,
    hasSelectedStyle: true,
    hasPrompt: true,
    hasGeneratedImage: true,
    slides: [
      { page_num: 1, type: "cover", headline: "疯火轮 AI" },
      { page_num: 35, type: "ending", headline: "下一步，用起来" },
    ],
  })),
  {
    targetRole: "visual",
    scope: "current_slide",
    risk: "safe",
    targetArea: "visual",
    areaLabel: "画面",
    confidence: "explicit",
    pageNums: [1],
    explicitScope: true,
    scopeLabel: "第 1 页",
    routeReason: "visual_intent",
  }
);

assert.deepEqual(
  plain(inferAgentRequestContext({
    message: "结尾页二维码太靠下了",
    activeAgentRole: "visual",
    activeScope: "deck",
    projectStatus: "prototype_ready",
    slideCount: 35,
    contentPlanConfirmed: true,
    hasSelectedStyle: true,
    hasPrompt: true,
    hasGeneratedImage: true,
    slides: [
      { page_num: 1, type: "cover", headline: "疯火轮 AI" },
      { page_num: 35, type: "ending", headline: "下一步，用起来" },
    ],
  })).pageNums,
  [35]
);

assert.deepEqual(
  plain(inferAgentRequestContext({
    message: "封面黑色的字体看不清楚",
    activeAgentRole: "visual",
    activeScope: "current_slide",
    editingPageNum: 4,
    projectStatus: "prototype_ready",
    slideCount: 35,
    contentPlanConfirmed: true,
    slides: [
      { page_num: 1, type: "cover", headline: "封面" },
      { page_num: 4, type: "content", headline: "当前正在看的页" },
    ],
  })).pageNums,
  [4]
);

assert.deepEqual(
  plain(inferAgentRequestContext({
    message: "封面黑色的字体看不清楚",
    activeAgentRole: "visual",
    activeScope: "selected_slides",
    selectedPageNums: [2, 4],
    projectStatus: "prototype_ready",
    slideCount: 35,
    contentPlanConfirmed: true,
    slides: [
      { page_num: 1, type: "cover", headline: "封面" },
      { page_num: 2, type: "content", headline: "选中页 A" },
      { page_num: 4, type: "content", headline: "选中页 B" },
    ],
  })).pageNums,
  [2, 4]
);

assert.deepEqual(
  plain(inferAgentRequestContext({
    message: "第 3 页字体看不清楚",
    activeAgentRole: "visual",
    activeScope: "current_slide",
    editingPageNum: 4,
    projectStatus: "prototype_ready",
    contentPlanConfirmed: true,
  })).pageNums,
  [3]
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
    targetArea: "visual",
    areaLabel: "画面",
    confidence: "explicit",
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

assert.deepEqual(
  plain(inferAgentRequestContext({
    message: "把标题改短",
    activeAgentRole: "content",
    activeScope: "deck",
    selectedPageNums: [2, 4, 5],
    projectStatus: "planning",
    contentPlanConfirmed: false,
  })),
  {
    targetRole: "content",
    scope: "selected_slides",
    risk: "safe",
    targetArea: "title",
    areaLabel: "标题",
    confidence: "explicit",
    pageNums: [2, 4, 5],
    explicitScope: false,
    scopeLabel: "第 2, 4, 5 页",
    routeReason: "content_intent",
  }
);

assert.deepEqual(
  plain(inferAgentRequestContext({
    message: "这些页都换成更商务的版式",
    activeAgentRole: "visual",
    activeScope: "deck",
    selectedPageNums: [],
    projectStatus: "visual_ready",
    contentPlanConfirmed: true,
  })),
  {
    targetRole: "visual",
    scope: "selected_slides",
    risk: "safe",
    targetArea: "visual",
    areaLabel: "画面",
    confidence: "needs_input",
    pageNums: [],
    explicitScope: true,
    scopeLabel: "选中页",
    routeReason: "visual_intent",
  }
);

assert.equal(
  inferAgentRequestContext({
    message: "备注改得像演讲稿一点",
    activeAgentRole: "content",
    activeScope: "deck",
    projectStatus: "planning",
    contentPlanConfirmed: false,
  }).targetArea,
  "notes"
);

assert.equal(
  inferAgentRequestContext({
    message: "把产品图作为核心素材，参考模板的版式",
    activeAgentRole: "visual",
    activeScope: "deck",
    projectStatus: "visual_ready",
    contentPlanConfirmed: true,
  }).targetArea,
  "materials"
);

assert.equal(
  inferAgentRequestContext({
    message: "语气更克制",
    activeAgentRole: "content",
    activeScope: "deck",
    projectStatus: "planning",
    contentPlanConfirmed: false,
  }).targetArea,
  "whole"
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

// --- stuck-slide / continue-generation tests ---

const incompleteState = buildWorkflowState({
  projectStatus: "prompt_ready",
  slides: [
    { page_num: 1, status: "completed" },
    { page_num: 2, status: "generating" },
    { page_num: 3, status: "prompt_ready" },
    { page_num: 4, status: "failed" },
    { page_num: 5, status: "pending" },
  ],
});
assert.deepEqual(incompleteState.incompletePageNums, [2, 3, 5]);

const allDoneState = buildWorkflowState({
  projectStatus: "prompt_ready",
  slides: [
    { page_num: 1, status: "completed" },
    { page_num: 2, status: "completed" },
    { page_num: 3, status: "failed" },
  ],
});
assert.deepEqual(allDoneState.incompletePageNums, []);

const incompleteNoFailedState = buildWorkflowState({
  projectStatus: "prompt_ready",
  slides: [
    { page_num: 1, status: "completed" },
    { page_num: 2, status: "generating" },
    { page_num: 3, status: "prompt_ready" },
    { page_num: 4, status: "completed" },
    { page_num: 5, status: "pending" },
  ],
});
assert.deepEqual(incompleteNoFailedState.incompletePageNums, [2, 3, 5]);

const continueCard = getStatusCard({
  workflowState: incompleteNoFailedState,
  staleActionPlan: planStaleSlideAction([]),
  failedPageNums: [],
  incompletePageNums: [2, 3, 5],
  visiblePrototypePageNums: [],
  resamplePageNums: [],
  prototypePromptTargetCount: 0,
  completedSlideCount: 2,
  totalSlideCount: 5,
});
assert.equal(continueCard.tone, "warning");
assert.equal(continueCard.primary.key, "continue-generation");
assert.ok(continueCard.description.includes("2、3、5"));

const runningState = buildWorkflowState({
  projectStatus: "prompt_ready",
  slides: [
    { page_num: 1, status: "completed" },
    { page_num: 2, status: "generating" },
  ],
  activeRun: { kind: "batch_generation", status: "running" },
});
const runningCard = getStatusCard({
  workflowState: runningState,
  staleActionPlan: planStaleSlideAction([]),
  failedPageNums: [],
  incompletePageNums: [2],
  visiblePrototypePageNums: [],
  resamplePageNums: [],
  prototypePromptTargetCount: 0,
  completedSlideCount: 1,
  totalSlideCount: 2,
  progressDisclosure: buildWorkflowProgressDisclosure({
    active_run: {
      kind: "batch_generation",
      status: "running",
      total_count: 2,
      completed_count: 1,
    },
  }),
});
assert.equal(runningCard.primary.key, "stop");
assert.equal(runningCard.title, "批量生成中");
assert.equal(runningCard.description, undefined);
assert.deepEqual(plain(runningCard.progress), { current: 1, total: 2, percent: 50, unit: "页" });

const noIncompleteCard = getStatusCard({
  workflowState: allDoneState,
  staleActionPlan: planStaleSlideAction([]),
  failedPageNums: [3],
  incompletePageNums: [],
  visiblePrototypePageNums: [],
  resamplePageNums: [],
  prototypePromptTargetCount: 0,
  completedSlideCount: 2,
  totalSlideCount: 3,
});
assert.equal(noIncompleteCard.tone, "danger");
assert.equal(noIncompleteCard.primary.key, "retry-failed");

// visual_ready 阶段不应显示 continue-generation，应走正常的生成风格提案流程
const visualReadyState = buildWorkflowState({
  projectStatus: "visual_ready",
  slides: [
    { page_num: 1, status: "pending" },
    { page_num: 2, status: "pending" },
  ],
  contentPlanConfirmed: true,
  hasSelectedStyle: false,
});
const visualReadyCard = getStatusCard({
  workflowState: visualReadyState,
  staleActionPlan: planStaleSlideAction([]),
  failedPageNums: [],
  incompletePageNums: [1, 2],
  visiblePrototypePageNums: [],
  resamplePageNums: [],
  prototypePromptTargetCount: 0,
  completedSlideCount: 0,
  totalSlideCount: 2,
});
assert.notEqual(visualReadyCard.primary.key, "continue-generation");
assert.equal(visualReadyCard.primary.key, "generate-style");

const visualReadyWithTemplateLocalStaleCard = getStatusCard({
  workflowState: visualReadyState,
  staleActionPlan: planStaleSlideAction([
    { slide: { id: "s1", page_num: 1 }, stale: { content: true } },
    { slide: { id: "s2", page_num: 2 }, stale: { content: true } },
  ]),
  failedPageNums: [],
  incompletePageNums: [1, 2],
  visiblePrototypePageNums: [],
  resamplePageNums: [],
  prototypePromptTargetCount: 0,
  completedSlideCount: 0,
  totalSlideCount: 2,
});
assert.equal(
  visualReadyWithTemplateLocalStaleCard.primary.key,
  "generate-style",
  "template uploads before style selection must not let local stale flags replace the visual-style CTA"
);

const failedVisualPromptRunCard = getStatusCard({
  workflowState: buildWorkflowState({
    projectStatus: "visual_ready",
    slides: [
      { page_num: 1, status: "pending" },
      { page_num: 2, status: "pending" },
    ],
    contentPlanConfirmed: true,
    hasSelectedStyle: true,
  }),
  staleActionPlan: planStaleSlideAction([
    { slide: { id: "s1", page_num: 1 }, stale: { content: true } },
    { slide: { id: "s2", page_num: 2 }, stale: { content: true } },
  ]),
  failedPageNums: [],
  incompletePageNums: [1, 2],
  visiblePrototypePageNums: [],
  resamplePageNums: [],
  prototypePromptTargetCount: 0,
  completedSlideCount: 0,
  totalSlideCount: 2,
  latestProblemRun: {
    kind: "visual_prompts",
    status: "failed",
    message: "画面方案生成失败",
    error_msg: "第 2 页缺少画面证据",
  },
});
assert.equal(
  failedVisualPromptRunCard.primary.key,
  "generate-visual-prompts",
  "failed visual prompt runs must stay visible instead of being hidden behind stale-update CTA"
);
assert.equal(failedVisualPromptRunCard.tone, "danger");
assert.match(failedVisualPromptRunCard.title, /画面方案生成失败/);

const partialFailedVisualPromptRunCard = getStatusCard({
  workflowState: buildWorkflowState({
    projectStatus: "visual_ready",
    slides: [
      { page_num: 1, status: "prompt_ready", prompt_text: "p1" },
      { page_num: 2, status: "pending" },
    ],
    contentPlanConfirmed: true,
    hasSelectedStyle: true,
  }),
  staleActionPlan: planStaleSlideAction([
    { slide: { id: "s2", page_num: 2 }, stale: { content: true } },
  ]),
  failedPageNums: [],
  incompletePageNums: [2],
  visiblePrototypePageNums: [],
  resamplePageNums: [],
  prototypePromptTargetCount: 0,
  completedSlideCount: 0,
  totalSlideCount: 2,
  latestProblemRun: {
    kind: "visual_prompts",
    status: "failed",
    message: "画面方案生成失败",
    error_msg: "第 2 页缺少画面证据",
    total_count: 2,
    completed_count: 1,
  },
});
assert.equal(
  partialFailedVisualPromptRunCard.primary.key,
  "generate-visual-prompts",
  "partial prompt output must not hide the failed visual-prompts run behind stale-update CTA"
);
assert.equal(partialFailedVisualPromptRunCard.tone, "danger");

const confirmedPlanningWithLocalStaleState = buildWorkflowState({
  projectStatus: "planning",
  slides: [
    { page_num: 1, status: "pending" },
    { page_num: 2, status: "pending" },
  ],
  contentPlanConfirmed: true,
  hasSelectedStyle: false,
});
const confirmedPlanningWithLocalStaleCard = getStatusCard({
  workflowState: confirmedPlanningWithLocalStaleState,
  staleActionPlan: planStaleSlideAction([
    { slide: { id: "s1", page_num: 1 }, stale: { content: true } },
  ]),
  failedPageNums: [],
  incompletePageNums: [1, 2],
  visiblePrototypePageNums: [],
  resamplePageNums: [],
  prototypePromptTargetCount: 0,
  completedSlideCount: 0,
  totalSlideCount: 2,
});
assert.equal(
  confirmedPlanningWithLocalStaleCard.primary.key,
  "switch-to-visual",
  "local stale flags before the visual stage must not hide the handoff to visual"
);

const selectedStyleWithStaleCard = getStatusCard({
  workflowState: buildWorkflowState({
    projectStatus: "visual_ready",
    slides: [
      { page_num: 1, status: "pending" },
      { page_num: 2, status: "pending" },
    ],
    contentPlanConfirmed: true,
    hasSelectedStyle: true,
  }),
  staleActionPlan: planStaleSlideAction([
    { slide: { id: "s1", page_num: 1 }, stale: { content: true } },
  ]),
  failedPageNums: [],
  incompletePageNums: [1, 2],
  visiblePrototypePageNums: [],
  resamplePageNums: [],
  prototypePromptTargetCount: 0,
  completedSlideCount: 0,
  totalSlideCount: 2,
});
assert.equal(
  selectedStyleWithStaleCard.primary.key,
  "update-stale-visual",
  "after a style is selected, content/visual stale flags should still ask for updated page visual plans"
);

const visualReadyWithoutPromptImageStaleCard = getStatusCard({
  workflowState: visualReadyState,
  staleActionPlan: planStaleSlideAction([
    { slide: { id: "s1", page_num: 1 }, stale: { image: true } },
  ]),
  failedPageNums: [],
  incompletePageNums: [1, 2],
  visiblePrototypePageNums: [],
  resamplePageNums: [],
  prototypePromptTargetCount: 0,
  completedSlideCount: 0,
  totalSlideCount: 2,
});
assert.equal(
  visualReadyWithoutPromptImageStaleCard.primary.key,
  "generate-style",
  "image stale flags without prompts must not replace the required visual-style CTA"
);

// 全新 prompt_ready(0 completed)不应显示 continue-generation,应走 start-prototype
const freshPromptReadyState = buildWorkflowState({
  projectStatus: "prompt_ready",
  slides: [
    { page_num: 1, status: "prompt_ready", prompt_text: "p1" },
    { page_num: 2, status: "prompt_ready", prompt_text: "p2" },
  ],
  contentPlanConfirmed: true,
  hasSelectedStyle: true,
});
const freshPromptReadyCard = getStatusCard({
  workflowState: freshPromptReadyState,
  staleActionPlan: planStaleSlideAction([]),
  failedPageNums: [],
  incompletePageNums: [1, 2],
  visiblePrototypePageNums: [],
  resamplePageNums: [],
  prototypePromptTargetCount: 0,
  completedSlideCount: 0,
  totalSlideCount: 2,
});
assert.notEqual(freshPromptReadyCard.primary.key, "continue-generation");
assert.equal(freshPromptReadyCard.primary.key, "start-prototype");

console.log("workflow gate tests passed");
