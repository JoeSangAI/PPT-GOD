export interface WorkflowSlide {
  status?: string;
  prompt_text?: string | null;
  image_path?: string | null;
}

export interface WorkflowInput {
  projectStatus?: string;
  slides?: WorkflowSlide[];
  activeRun?: WorkflowRun | null;
  contentPlanConfirmed?: boolean;
  showPrototypePreview?: boolean;
  hasSelectedStyle?: boolean;
  selectedPageCount?: number;
  staleSummary?: {
    hasContentOrVisualStale: boolean;
    imageStaleCount: number;
  };
  templatePageCount?: number;
  isBusy?: boolean;
}

export interface WorkflowRun {
  id?: string;
  kind?: string;
  status?: string;
  stage?: string;
  message?: string | null;
  target_page_nums?: number[] | null;
  total_count?: number;
  completed_count?: number;
  failed_count?: number;
}

export interface WorkflowState {
  projectStatus: string;
  statusLabel: string;
  steps: typeof WORKFLOW_STEPS;
  stepIndex: number;
  stepStatuses: string[];
  isLoading: boolean;
  hasFailedSlide: boolean;
  hasGeneratedImage: boolean;
  hasPrompt: boolean;
  viewLabel: string | null;
  selectedPageCount: number;
  staleSummary: {
    hasContentOrVisualStale: boolean;
    imageStaleCount: number;
  };
  templatePageCount: number;
  isBusy: boolean;
  contentPlanConfirmed: boolean;
  hasSelectedStyle: boolean;
  activeRun: WorkflowRun | null;
}

export type WorkflowGate =
  | "draft"
  | "content"
  | "visual"
  | "visual_design"
  | "prototype"
  | "batch";

export type MainStageMode =
  | "brief_studio"
  | "deck_content"
  | "deck_style"
  | "deck_visual"
  | "deck_prototype"
  | "deck_final";

export type GateActionKey =
  | "send_brief"
  | "generate_content_plan"
  | "confirm_content"
  | "switch_to_content"
  | "switch_to_visual"
  | "generate_style_proposals"
  | "confirm_style"
  | "generate_visual_prompts"
  | "start_prototype"
  | "resample_prototype"
  | "confirm_prototype"
  | "start_generation"
  | "retry_failed"
  | "download";

export interface GateContext {
  gate: WorkflowGate;
  gateRevision: number;
  mainStageMode: MainStageMode;
  activeAgentRole: "content" | "visual" | "finetune";
  allowedActions: GateActionKey[];
}

export const WORKFLOW_STEPS = [
  { key: "planning", label: "内容规划" },
  { key: "visual_ready", label: "视觉方案" },
  { key: "prompt_ready", label: "画面设计" },
  { key: "prototype_ready", label: "效果预览" },
  { key: "completed", label: "批量生成" },
];

export const STATUS_LABEL: Record<string, string> = {
  draft: "草稿",
  planning: "内容规划",
  content_plan_ready: "内容待确认",
  visual_ready: "视觉方案",
  prompt_ready: "画面设计",
  prototype: "效果预览中",
  prototype_ready: "效果预览",
  generating: "批量生成中",
  completed: "已完成",
  failed: "失败",
};

export function buildWorkflowState(input: WorkflowInput): WorkflowState {
  const slides = input.slides || [];
  const projectStatus = input.projectStatus || "draft";
  const activeRun = isActiveRun(input.activeRun) ? input.activeRun! : null;
  const hasGeneratedImage = slides.some((s) => Boolean(s.image_path));
  const hasPrompt = slides.some((s) => Boolean(s.prompt_text));
  const hasFailedSlide = slides.some((s) => s.status === "failed");
  const stepIndex = getStepIndex(projectStatus, {
    contentPlanConfirmed: input.contentPlanConfirmed,
    hasSelectedStyle: input.hasSelectedStyle,
    hasGeneratedImage,
    hasPrompt,
  }, activeRun);

  return {
    projectStatus,
    statusLabel: STATUS_LABEL[projectStatus] || projectStatus,
    steps: WORKFLOW_STEPS,
    stepIndex,
    stepStatuses: WORKFLOW_STEPS.map((_, idx) => getStepStatus(projectStatus, stepIndex, idx)),
    isLoading: Boolean(activeRun),
    hasFailedSlide,
    hasGeneratedImage,
    hasPrompt,
    viewLabel: projectStatus === "prototype_ready"
      ? (input.showPrototypePreview ? "打样结果" : "全局预览")
      : null,
    selectedPageCount: input.selectedPageCount || 0,
    staleSummary: input.staleSummary || { hasContentOrVisualStale: false, imageStaleCount: 0 },
    templatePageCount: input.templatePageCount || 0,
    isBusy: Boolean(input.isBusy),
    contentPlanConfirmed: Boolean(input.contentPlanConfirmed),
    hasSelectedStyle: Boolean(input.hasSelectedStyle),
    activeRun,
  };
}

export function buildGateContext(state: WorkflowState, revision = 0): GateContext {
  const gate = getGate(state);
  const mainStageMode = getMainStageMode(state, gate);
  const activeAgentRole = getActiveAgentRole(state, gate);
  const allowedActions = getAllowedGateActions(state, gate);

  return {
    gate,
    gateRevision: revision,
    mainStageMode,
    activeAgentRole,
    allowedActions,
  };
}

export function isActiveRun(run?: WorkflowRun | null) {
  return Boolean(run && (run.status === "queued" || run.status === "running"));
}

function getStepIndex(
  projectStatus: string,
  facts: { contentPlanConfirmed?: boolean; hasSelectedStyle?: boolean; hasGeneratedImage: boolean; hasPrompt: boolean },
  activeRun?: WorkflowRun | null
) {
  const activeStep = getStepIndexForRun(activeRun);
  if (activeStep != null) return activeStep;

  switch (projectStatus) {
    case "draft":
      return 0;
    case "planning":
    case "content_plan_ready":
      return facts.contentPlanConfirmed ? 1 : 0;
    case "visual_ready":
      if (facts.hasSelectedStyle) return 2;
      return 1;
    case "prompt_ready":
      return 2;
    case "prototype":
    case "prototype_ready":
      return 3;
    case "generating":
    case "completed":
      return 4;
    case "failed":
      if (facts.hasGeneratedImage) return 4;
      if (facts.hasPrompt) return 2;
      return 0;
    default:
      return 0;
  }
}

function getStepIndexForRun(run?: WorkflowRun | null) {
  if (!isActiveRun(run)) return null;
  switch (run?.kind) {
    case "content_plan":
      return 0;
    case "style_proposal":
      return 1;
    case "visual_prompts":
      return 2;
    case "prototype_generation":
      return 3;
    case "batch_generation":
    case "page_generation":
    case "retry_failed":
    case "finetune":
      return 4;
    default:
      return null;
  }
}

function getStepStatus(projectStatus: string, stepIndex: number, idx: number) {
  if (projectStatus === "failed") {
    if (idx === stepIndex) return "error";
    if (idx < stepIndex) return "done";
    return "pending";
  }
  if (idx < stepIndex) return "done";
  if (idx === stepIndex) return "current";
  return "pending";
}

export function getGuidanceText(state: WorkflowState) {
  if (state.activeRun) {
    return state.activeRun.message || "任务正在处理中，请稍候";
  }
  switch (state.projectStatus) {
    case "draft":
      return "新建项目，请输入 PPT 主题或上传文档开始";
    case "planning":
      if (state.contentPlanConfirmed) return "内容已确认，请进入视觉总监生成视觉方向";
      return "内容规划已完成，请检查并确认";
    case "visual_ready":
      if (state.hasSelectedStyle && !state.hasPrompt) return "视觉方向已确认，请先生成每页画面方案";
      if (state.hasSelectedStyle) return "请检查每页画面描述，可上传参考图，然后点击「生成样张」";
      return "生成视觉方向前，可先上传 Logo、风格参考、可复用素材或模板";
    case "prompt_ready":
      return "请检查每页画面描述，可上传参考图，然后点击「生成样张」";
    case "prototype":
      return "打样页正在生成中，请稍候";
    case "prototype_ready":
      return "打样页已生成，请检查效果，满意后点击确认开始批量生成";
    case "generating":
      return "正在批量生成所有页面";
    case "completed":
      return "已生成，可从右上角导出";
    case "failed":
      return "部分页面生成失败，可点击「一键重试失败页」或单页重试";
    default:
      return "";
  }
}

export function getPrimaryActionKey(state: WorkflowState) {
  if (state.activeRun) {
    return null;
  }
  if (state.projectStatus === "visual_ready" && !state.hasSelectedStyle) {
    return "generate-style-proposals";
  }
  if (
    (state.projectStatus === "visual_ready" || state.projectStatus === "failed") &&
    state.hasSelectedStyle &&
    !state.hasPrompt
  ) {
    return "generate-visual-prompts";
  }
  if (state.projectStatus === "prompt_ready" || state.projectStatus === "failed") {
    return "start-prototype";
  }
  if (state.projectStatus === "prototype_ready") {
    return "confirm-prototype";
  }
  if (state.projectStatus === "completed") {
    return "download";
  }
  return null;
}

export function getSecondaryActionKeys(state: WorkflowState) {
  const actions: string[] = [];
  if (state.projectStatus === "prototype_ready") {
    actions.push("resample");
  }
  if (state.hasFailedSlide) {
    actions.push("retry-failed");
  }
  // Page regeneration actions are intentionally not global header actions.
  // They belong on the affected page/card so the loading state stays local.
  if (state.projectStatus === "completed") {
    actions.push("regenerate");
  }
  return actions;
}

function getGate(state: WorkflowState): WorkflowGate {
  if (state.activeRun?.kind === "content_plan") return "content";
  if (state.activeRun?.kind === "style_proposal") return "visual";
  if (state.activeRun?.kind === "visual_prompts") return "visual_design";
  if (state.activeRun?.kind === "prototype_generation") return "prototype";
  if (state.activeRun) return "batch";

  switch (state.projectStatus) {
    case "draft":
      return "draft";
    case "planning":
    case "content_plan_ready":
      return state.contentPlanConfirmed ? "visual" : "content";
    case "visual_ready":
      return state.hasSelectedStyle ? "visual_design" : "visual";
    case "prompt_ready":
    case "failed":
      return "visual_design";
    case "prototype":
    case "prototype_ready":
      return "prototype";
    case "generating":
    case "completed":
      return "batch";
    default:
      return "content";
  }
}

function getMainStageMode(state: WorkflowState, gate: WorkflowGate): MainStageMode {
  if (gate === "draft") return "brief_studio";
  if (gate === "content") return "deck_content";
  if (gate === "visual") return "deck_style";
  if (gate === "visual_design") return "deck_visual";
  if (gate === "prototype") return "deck_prototype";
  if (gate === "batch") return "deck_final";
  return state.hasGeneratedImage ? "deck_final" : "deck_content";
}

function getActiveAgentRole(_state: WorkflowState, gate: WorkflowGate): "content" | "visual" | "finetune" {
  if (gate === "draft" || gate === "content") return "content";
  return "visual";
}

function getAllowedGateActions(state: WorkflowState, gate: WorkflowGate): GateActionKey[] {
  const actions: GateActionKey[] = [];
  if (gate === "draft") {
    actions.push("send_brief", "generate_content_plan");
  }
  if (gate === "content") {
    actions.push("switch_to_content");
    if (!state.contentPlanConfirmed) actions.push("generate_content_plan");
    if (state.projectStatus !== "draft" && !state.contentPlanConfirmed) actions.push("confirm_content", "switch_to_visual");
  }
  if (gate === "visual") {
    actions.push("switch_to_visual", "generate_style_proposals");
    if (!state.isBusy) actions.push("confirm_style");
  }
  if (gate === "visual_design") {
    if (!state.isBusy) actions.push("confirm_style");
    actions.push("generate_visual_prompts");
    if (state.hasPrompt) actions.push("start_prototype", "start_generation");
  }
  if (gate === "prototype") {
    if (!state.isBusy) actions.push("confirm_style");
    actions.push("resample_prototype", "confirm_prototype");
  }
  if (gate === "batch") {
    if (!state.isBusy && state.projectStatus !== "generating") actions.push("confirm_style");
    actions.push("download");
    if (state.projectStatus !== "generating") actions.push("start_generation");
  }
  if (state.hasFailedSlide) actions.push("retry_failed");
  return actions;
}
