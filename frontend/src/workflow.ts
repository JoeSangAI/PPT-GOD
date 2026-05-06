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
  activeRun: WorkflowRun | null;
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
    activeRun,
  };
}

export function isActiveRun(run?: WorkflowRun | null) {
  return Boolean(run && (run.status === "queued" || run.status === "running"));
}

function getStepIndex(
  projectStatus: string,
  facts: { contentPlanConfirmed?: boolean; hasGeneratedImage: boolean; hasPrompt: boolean },
  activeRun?: WorkflowRun | null
) {
  const activeStep = getStepIndexForRun(activeRun);
  if (activeStep != null) return activeStep;

  switch (projectStatus) {
    case "draft":
      return 0;
    case "planning":
      return facts.contentPlanConfirmed ? 1 : 0;
    case "visual_ready":
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
      if (state.contentPlanConfirmed) return "内容已确认，请与视觉总监沟通风格偏好";
      return "内容规划已完成，请检查并确认";
    case "visual_ready":
      return "请选择视觉风格方案，或告诉视觉总监你的偏好";
    case "prompt_ready":
      return "请检查每页画面描述，可上传参考图，然后点击「打样确认」";
    case "prototype":
      return "打样页正在生成中，请稍候";
    case "prototype_ready":
      return "打样页已生成，请检查效果，满意后点击确认开始批量生成";
    case "generating":
      return "正在批量生成所有页面";
    case "completed":
      return "PPT 已生成完成，可点击右上角下载";
    case "failed":
      return "部分页面生成失败，可点击「一键重试失败页」或单页重试";
    default:
      return "";
  }
}

export function getPrimaryActionKey(state: WorkflowState) {
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
  if (state.projectStatus === "planning" && state.templatePageCount > 0) {
    actions.push("templates");
  }
  if (state.projectStatus === "prompt_ready" || state.projectStatus === "failed") {
    actions.push("generate-all");
  }
  if (state.projectStatus === "prototype_ready") {
    actions.push("toggle-prototype-view", "resample");
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
