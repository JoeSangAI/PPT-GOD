export interface WorkflowSlide {
  page_num?: number;
  status?: string;
  prompt_text?: string | null;
  image_path?: string | null;
  error_msg?: string | null;
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
  project_id?: string;
  kind?: string;
  status?: string;
  stage?: string;
  message?: string | null;
  error_msg?: string | null;
  target_page_nums?: number[] | null;
  total_count?: number;
  completed_count?: number;
  failed_count?: number;
  started_at?: string | null;
  updated_at?: string | null;
}

export interface WorkflowStatusLike {
  project_id?: string;
  project_phase?: string;
  project_status?: string;
  total_slides?: number;
  completed_slides?: number;
  total_completed_slides?: number;
  target_completed_slides?: number;
  target_failed_slides?: number;
  target_count?: number;
  target_page_nums?: number[] | null;
  active_run?: WorkflowRun | null;
  last_run?: WorkflowRun | null;
  progress?: {
    run_id?: string;
    kind?: string;
    status?: string;
    stage?: string;
    label?: string;
    message?: string | null;
    current?: number;
    total?: number;
    failed?: number;
    unit?: string;
    percent?: number;
    target_page_nums?: number[] | null;
    can_cancel?: boolean;
    current_page?: number;
    total_pages?: number;
    active_page_nums?: number[];
    running_count?: number;
    updated_at?: string | null;
  } | null;
  quality_report?: unknown;
}

export interface ImageGenerationOutcomeInput {
  prototype?: boolean;
  projectStatus?: string | null;
  run?: WorkflowRun | null;
}

export interface ImageGenerationOutcome {
  kind: "success" | "partial" | "cancelled" | "failed" | "unknown";
  isSuccess: boolean;
  canConfirmPrototype: boolean;
  completed: number;
  total: number;
  failed: number;
  targetText: string;
  message: string;
}

export interface WorkflowProgressViewInput {
  active_run?: WorkflowRun | null;
  progress?: {
    run_id?: string;
    kind?: string;
    status?: string;
    stage?: string;
    label?: string;
    message?: string | null;
    current?: number;
    total?: number;
    failed?: number;
    unit?: string;
    percent?: number;
    target_page_nums?: number[] | null;
    active_page_nums?: number[];
    running_count?: number;
    current_page?: number;
    total_pages?: number;
    updated_at?: string | null;
  } | null;
  target_count?: number;
  target_page_nums?: number[] | null;
  total_slides?: number;
  completed_slides?: number;
  target_completed_slides?: number;
  target_failed_slides?: number;
}

export interface WorkflowProgressMetric {
  label: string;
  value: string;
}

export interface WorkflowProgressStep {
  label: string;
  status: "done" | "current" | "pending";
}

export interface WorkflowProgressDisclosure {
  headline: string;
  summary: string;
  detail: string;
  metrics: WorkflowProgressMetric[];
  steps: WorkflowProgressStep[];
  percent: number;
  current: number;
  total: number;
  failed: number;
  unit: string;
  activePageNums: number[];
  targetPageNums: number[];
  status?: string | null;
}

export type WorkflowProgressOverviewVariant = "drawer" | "empty" | "agent";

export interface WorkflowProgressOverviewDisplay {
  showHeaderCopy: boolean;
  metrics: WorkflowProgressMetric[];
  showSteps: boolean;
  showStandaloneTitle: boolean;
  showFooterSummary: boolean;
  showAgentCopy: boolean;
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
  incompletePageNums: number[];
}

export interface SlideStaleFlags {
  content?: boolean;
  visual?: boolean;
  image?: boolean;
  localImage?: boolean;
}

export interface StaleSlideActionInput {
  slide: {
    id?: string;
    page_num?: number;
  };
  stale?: SlideStaleFlags | null;
}

export interface StaleSlideActionPlan {
  primaryActionKey: "update_visual_plan" | "regenerate_images" | null;
  primaryLabel: string | null;
  progressTitle: string | null;
  progressMeta: string | null;
  contentOrVisualCount: number;
  imageOnlyCount: number;
  pageNumsForVisualPlan: number[];
  pageNumsForPrompt: number[];
  imageOnlyPageNums: number[];
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

const WORKFLOW_RUN_LABELS: Record<string, string> = {
  content_plan: "内容规划进度",
  style_proposal: "视觉方向进度",
  visual_prompts: "画面方案进度",
  prototype_generation: "打样生成进度",
  batch_generation: "批量生成进度",
  page_generation: "单页生成进度",
  retry_failed: "失败页重试进度",
  finetune: "单页微调进度",
};

const WORKFLOW_RUN_UNITS: Record<string, string> = {
  style_proposal: "套",
};

export function adoptWorkflowRun<T extends WorkflowStatusLike | null | undefined>(
  workflow: T,
  run?: WorkflowRun | null
): T {
  if (!run?.id || !isActiveRun(run)) return workflow;

  const workflowProjectId = workflow?.project_id ? String(workflow.project_id) : "";
  const runProjectId = run.project_id ? String(run.project_id) : "";
  if (workflowProjectId && runProjectId && workflowProjectId !== runProjectId) {
    return workflow;
  }
  const projectId = workflowProjectId || runProjectId;
  if (!projectId) return workflow;

  const kind = String(run.kind || "");
  const total = Math.max(0, Number(run.total_count ?? run.target_page_nums?.length ?? workflow?.target_count ?? 0) || 0);
  const completed = Math.min(total || Number.MAX_SAFE_INTEGER, Math.max(0, Number(run.completed_count || 0)));
  const failed = Math.min(total || Number.MAX_SAFE_INTEGER, Math.max(0, Number(run.failed_count || 0)));
  const percent = total > 0 ? Math.round((completed / total) * 1000) / 10 : 0;
  const next = {
    ...(workflow || {}),
    project_id: projectId,
    project_phase: workflow?.project_phase || workflow?.project_status || "draft",
    project_status: workflow?.project_status || workflow?.project_phase || "draft",
    total_slides: workflow?.total_slides ?? total,
    completed_slides: completed,
    target_completed_slides: completed,
    target_failed_slides: failed,
    target_count: total || workflow?.target_count || 0,
    target_page_nums: run.target_page_nums ?? workflow?.target_page_nums ?? null,
    active_run: run,
    progress: {
      run_id: run.id,
      kind,
      status: run.status,
      stage: run.stage,
      label: WORKFLOW_RUN_LABELS[kind] || "任务进度",
      message: run.message || WORKFLOW_RUN_LABELS[kind] || "任务处理中",
      current: completed,
      total,
      failed,
      unit: WORKFLOW_RUN_UNITS[kind] || "页",
      percent,
      target_page_nums: run.target_page_nums ?? workflow?.target_page_nums ?? null,
      can_cancel: isActiveRun(run),
      current_page: completed,
      total_pages: total,
      active_page_nums: workflow?.progress?.active_page_nums || [],
      running_count: workflow?.progress?.running_count || 0,
      updated_at: run.updated_at || null,
    },
    quality_report: null,
  };
  return next as T;
}

function uniqueSortedPageNums(items: StaleSlideActionInput[]) {
  return Array.from(new Set(
    items
      .map((item) => Number(item.slide.page_num))
      .filter(Number.isFinite)
  )).sort((a, b) => a - b);
}

export function planStaleSlideAction(items: StaleSlideActionInput[]): StaleSlideActionPlan {
  const staleItems = (items || []).filter((item) => {
    const stale = item.stale || {};
    return Boolean(stale.content || stale.visual || stale.image);
  });
  const needsVisualPlan = staleItems.filter((item) => Boolean(item.stale?.content));
  const needsPrompt = staleItems.filter((item) => Boolean(item.stale?.content || item.stale?.visual));
  const imageOnly = staleItems.filter((item) => Boolean(item.stale?.image && !item.stale?.content && !item.stale?.visual));

  if (needsPrompt.length > 0) {
    return {
      primaryActionKey: "update_visual_plan",
      primaryLabel: "更新画面方案",
      progressTitle: "待更新画面方案",
      progressMeta: `${needsPrompt.length} 页内容或画面描述变更，需要先更新画面方案`,
      contentOrVisualCount: needsPrompt.length,
      imageOnlyCount: imageOnly.length,
      pageNumsForVisualPlan: uniqueSortedPageNums(needsVisualPlan),
      pageNumsForPrompt: uniqueSortedPageNums(needsPrompt),
      imageOnlyPageNums: uniqueSortedPageNums(imageOnly),
    };
  }

  if (imageOnly.length > 0) {
    return {
      primaryActionKey: "regenerate_images",
      primaryLabel: "重新生成图片",
      progressTitle: "待重新生成图片",
      progressMeta: `${imageOnly.length} 页图片已过期，确认后重新生成这些页面`,
      contentOrVisualCount: 0,
      imageOnlyCount: imageOnly.length,
      pageNumsForVisualPlan: [],
      pageNumsForPrompt: [],
      imageOnlyPageNums: uniqueSortedPageNums(imageOnly),
    };
  }

  return {
    primaryActionKey: null,
    primaryLabel: null,
    progressTitle: null,
    progressMeta: null,
    contentOrVisualCount: 0,
    imageOnlyCount: 0,
    pageNumsForVisualPlan: [],
    pageNumsForPrompt: [],
    imageOnlyPageNums: [],
  };
}

export function buildWorkflowState(input: WorkflowInput): WorkflowState {
  const slides = input.slides || [];
  const projectStatus = input.projectStatus || "draft";
  const activeRun = isActiveRun(input.activeRun) ? input.activeRun! : null;
  const hasGeneratedImage = slides.some((s) => Boolean(s.image_path));
  const hasPrompt = slides.some((s) => Boolean(s.prompt_text));
  const hasFailedSlide = slides.some((s) => s.status === "failed");
  const incompletePageNums = slides
    .filter((s) => s.status !== "completed" && s.status !== "failed")
    .map((s) => s.page_num)
    .filter((n): n is number => typeof n === "number" && Number.isFinite(n))
    .sort((a, b) => a - b);
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
      ? (input.showPrototypePreview ? "样张结果" : "全局预览")
      : null,
    selectedPageCount: input.selectedPageCount || 0,
    staleSummary: input.staleSummary || { hasContentOrVisualStale: false, imageStaleCount: 0 },
    templatePageCount: input.templatePageCount || 0,
    isBusy: Boolean(input.isBusy),
    contentPlanConfirmed: Boolean(input.contentPlanConfirmed),
    hasSelectedStyle: Boolean(input.hasSelectedStyle),
    activeRun,
    incompletePageNums,
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

const RUN_COPY: Record<string, {
  headline: string;
  running: string;
  doneNoun: string;
  detail: string;
  steps: string[];
}> = {
  content_plan: {
    headline: "内容规划进度",
    running: "正在整理每页要讲什么",
    doneNoun: "内容规划",
    detail: "正在读取材料并整理每页要讲什么；完成后会进入内容规划页。",
    steps: ["读取材料", "整理页面结构", "保存到画布"],
  },
  style_proposal: {
    headline: "视觉方向进度",
    running: "正在整理可选视觉方向",
    doneNoun: "视觉方向",
    detail: "正在根据内容和素材整理可选视觉方向；完成后会显示方案卡片。",
    steps: ["读取内容与素材", "生成视觉方向", "保存方案"],
  },
  visual_prompts: {
    headline: "画面方案进度",
    running: "正在生成每页画面方案",
    doneNoun: "画面方案",
    detail: "正在把每页内容转换成可生成样张的画面方案；完成后每页会出现检查项。",
    steps: ["读取内容与素材", "生成画面方案", "保存到页面"],
  },
  prototype_generation: {
    headline: "样张进度",
    running: "正在生成样张",
    doneNoun: "样张页",
    detail: "正在生成样张；完成后会直接出现在画布中。",
    steps: ["准备页面", "生成图片", "写入画布"],
  },
  batch_generation: {
    headline: "批量生成进度",
    running: "正在生成图片",
    doneNoun: "页面图片",
    detail: "正在生成页面图片；完成后会直接出现在画布中。",
    steps: ["准备页面", "生成图片", "写入画布"],
  },
  page_generation: {
    headline: "单页生成进度",
    running: "正在生成图片",
    doneNoun: "页面图片",
    detail: "正在生成选中页面；完成后会直接出现在画布中。",
    steps: ["准备页面", "生成图片", "写入画布"],
  },
  retry_failed: {
    headline: "失败页重试进度",
    running: "正在重试失败页",
    doneNoun: "页面图片",
    detail: "正在重试失败页面；完成后会直接出现在画布中。",
    steps: ["准备失败页", "重新生成图片", "写入画布"],
  },
  finetune: {
    headline: "单页微调进度",
    running: "正在微调当前页",
    doneNoun: "页面图片",
    detail: "正在根据你的修改生成当前页；完成后会替换到画布中。",
    steps: ["读取修改要求", "生成新画面", "保存版本"],
  },
};

function progressCopy(kind?: string | null) {
  return RUN_COPY[String(kind || "")] || {
    headline: "任务进度",
    running: "任务正在处理",
    doneNoun: "任务",
    detail: "任务正在处理；页面会自动刷新进度。",
    steps: ["等待开始", "处理中", "更新结果"],
  };
}

function isImageProgressKind(kind?: string | null) {
  return ["prototype_generation", "batch_generation", "page_generation", "retry_failed", "finetune"].includes(String(kind || ""));
}

function cleanWorkflowMessage(message?: string | null) {
  return String(message || "")
    .replace(/[🧠🚀⏳✅📝🎨]/gu, "")
    .replace(/（?批次\s*\d+\s*\/\s*\d+）?/g, "")
    .replace(/\d+\s*\/\s*\d+\s*页完成/g, "")
    .replace(/\.\.\./g, "")
    .replace(/……/g, "")
    .replace(/\bprompt\b/gi, "画面方案")
    .replace(/\s+/g, " ")
    .trim();
}

function displayProgressMessage(kind: string, message: string, fallback: string) {
  if (kind === "visual_prompts") {
    if (!message || /视觉方案|画面方案|prompt|生图|撰写/i.test(message)) {
      return fallback;
    }
  }
  return message || fallback;
}

function numberFrom(value: unknown, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function secondsSinceIsoAt(value: string | null | undefined, nowMs: number) {
  if (!value) return 0;
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return 0;
  return Math.max(0, Math.floor((nowMs - timestamp) / 1000));
}

export function formatWorkflowDuration(seconds: number) {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 10) return "刚刚";
  if (s < 60) return `${s} 秒`;
  const minutes = Math.floor(s / 60);
  const rest = s % 60;
  if (minutes < 60) return rest > 0 ? `${minutes} 分 ${rest} 秒` : `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes > 0 ? `${hours} 小时 ${remainingMinutes} 分钟` : `${hours} 小时`;
}

export function formatWorkflowPageNumsForUser(pageNums?: number[] | null, limit = 6) {
  const unique = Array.from(new Set((pageNums || []).map(Number).filter(Number.isFinite))).sort((a, b) => a - b);
  if (unique.length === 0) return "";
  const shown = unique.slice(0, limit).join("、");
  return unique.length > limit ? `第 ${shown} 等 ${unique.length} 页` : `第 ${shown} 页`;
}

export function evaluateImageGenerationOutcome(input: ImageGenerationOutcomeInput): ImageGenerationOutcome {
  const run = input.run || null;
  const prototype = Boolean(input.prototype);
  const projectStatus = String(input.projectStatus || "");
  const status = String(run?.status || "");
  const total = Math.max(0, Number(run?.total_count || 0));
  const completed = total > 0
    ? Math.min(total, Math.max(0, Number(run?.completed_count || 0)))
    : Math.max(0, Number(run?.completed_count || 0));
  const failed = total > 0
    ? Math.min(total, Math.max(0, Number(run?.failed_count || 0)))
    : Math.max(0, Number(run?.failed_count || 0));
  const targetText = formatWorkflowPageNumsForUser(run?.target_page_nums || null);
  const scopeText = targetText ? `（${targetText}）` : "";
  const noun = prototype ? "样张生成" : "图片生成";
  const base = { completed, total, failed, targetText };

  if (status === "cancelled") {
    const message = completed > 0
      ? `已停止${noun}${scopeText}，本次只生成了 ${completed} / ${total || completed} 页，不算完整样张。请检查已生成页面，或重打样张。`
      : `已停止${noun}${scopeText}，本次没有生成新样张。旧样张仍会保留在画布中，不能代表刚才的新视觉方案。`;
    return {
      ...base,
      kind: "cancelled",
      isSuccess: false,
      canConfirmPrototype: false,
      message,
    };
  }

  if (["failed", "stale"].includes(status)) {
    const reason = run?.message || "后台任务没有完成";
    const hasPartialResult = completed > 0;
    return {
      ...base,
      kind: hasPartialResult ? "partial" : "failed",
      isSuccess: false,
      canConfirmPrototype: false,
      message: hasPartialResult
        ? `${noun}${scopeText}只完成了 ${completed} / ${total || completed} 页，不能直接确认生成全部。原因：${reason}`
        : `${noun}${scopeText}没有生成成功，画布里仍是旧结果或待生成页面。原因：${reason}`,
    };
  }

  const expectedStatus = prototype ? "prototype_ready" : "completed";
  const successByRun = status === "succeeded" && completed > 0 && (!total || completed >= total) && failed === 0;
  const successByLegacyStatus = !run && projectStatus === expectedStatus;
  if (successByRun || successByLegacyStatus) {
    return {
      ...base,
      kind: "success",
      isSuccess: true,
      canConfirmPrototype: prototype,
      message: prototype
        ? `样张已生成${scopeText}，页面已刷新。请检查风格、构图和文字可读性；满意后再生成全部。`
        : "全部页面生成完成，页面已刷新。可以导出 PPTX，需要调整时再选中页面微调。",
    };
  }

  return {
    ...base,
    kind: "unknown",
    isSuccess: false,
    canConfirmPrototype: false,
    message: `生成任务已结束，但当前状态是「${projectStatus || "未知"}」。请检查页面是否有失败或待生成项后再继续。`,
  };
}

function buildStepStatuses(labels: string[], currentIndex: number): WorkflowProgressStep[] {
  const safeCurrent = Math.max(0, Math.min(labels.length - 1, currentIndex));
  return labels.map((label, index) => ({
    label,
    status: index < safeCurrent ? "done" : index === safeCurrent ? "current" : "pending",
  }));
}

function progressStepIndex(kind: string, status: string | null, stage: string | null, current: number, total: number) {
  if (status === "queued") return 0;
  const normalizedStage = String(stage || "").toLowerCase();
  if (/(saving|save|complete|final|assembling)/.test(normalizedStage)) return 2;
  if (/(document|parse|analyz|asset|read)/.test(normalizedStage)) return 0;
  if (total > 0 && current >= total) return 2;
  if (kind === "content_plan" && current <= 0 && /(brief|ingest|document)/.test(normalizedStage)) return 0;
  return 1;
}

export function buildWorkflowProgressDisclosure(
  input?: WorkflowProgressViewInput | null,
  nowMs = Date.now()
): WorkflowProgressDisclosure | null {
  const activeRun = input?.active_run || null;
  const progress = input?.progress || null;
  if (!activeRun && !progress) return null;

  const kind = String(progress?.kind || activeRun?.kind || "");
  const copy = progressCopy(kind);
  const status = String(progress?.status || activeRun?.status || "");
  const total = Math.max(0, numberFrom(progress?.total ?? progress?.total_pages ?? activeRun?.total_count ?? input?.target_count ?? input?.total_slides));
  const rawCurrent = numberFrom(progress?.current ?? progress?.current_page ?? activeRun?.completed_count ?? input?.target_completed_slides ?? input?.completed_slides);
  const current = Math.min(total || rawCurrent, Math.max(0, rawCurrent));
  const failed = Math.max(0, numberFrom(progress?.failed ?? activeRun?.failed_count ?? input?.target_failed_slides));
  const unit = progress?.unit || (kind === "style_proposal" ? "套" : "页");
  const percent = total > 0 ? Math.min(100, (current / total) * 100) : 0;
  const targetPageNums = Array.isArray(progress?.target_page_nums)
    ? progress.target_page_nums.map(Number).filter(Number.isFinite)
    : Array.isArray(activeRun?.target_page_nums)
    ? activeRun.target_page_nums.map(Number).filter(Number.isFinite)
    : Array.isArray(input?.target_page_nums)
    ? input.target_page_nums.map(Number).filter(Number.isFinite)
    : [];
  const activePageNums = Array.isArray(progress?.active_page_nums)
    ? progress.active_page_nums.map(Number).filter(Number.isFinite).sort((a, b) => a - b)
    : [];
  const message = displayProgressMessage(kind, cleanWorkflowMessage(progress?.message || activeRun?.message), copy.running);
  const startedAt = activeRun?.started_at || null;
  const updatedAt = progress?.updated_at || activeRun?.updated_at || null;
  const elapsed = startedAt ? formatWorkflowDuration(secondsSinceIsoAt(startedAt, nowMs)) : "";
  const recentSeconds = updatedAt ? secondsSinceIsoAt(updatedAt, nowMs) : 0;
  const recentDuration = updatedAt ? formatWorkflowDuration(recentSeconds) : "";
  const recent = recentDuration ? (recentDuration === "刚刚" ? "刚刚" : `${recentDuration}前`) : "";
  const hasTotal = total > 0;
  const progressValue = hasTotal ? `${current} / ${total} ${unit}` : current > 0 ? `${current} ${unit}` : "";
  const targetText = formatWorkflowPageNumsForUser(targetPageNums);

  const summary = status === "queued"
    ? `${copy.headline.replace(/进度$/, "")}已排队，等待开始${elapsed ? `，已等待 ${elapsed}` : ""}`
    : activePageNums.length > 0 && isImageProgressKind(kind)
    ? `${activePageNums.length === 1 ? `正在生成第 ${activePageNums[0]} 页` : `正在处理${formatWorkflowPageNumsForUser(activePageNums)}`}：${progressValue || message}完成`
    : hasTotal
    ? `${message}：${progressValue}完成`
    : message;

  const detail = status === "queued"
    ? "任务已提交，正在等待后台开始；页面会自动刷新进度。"
    : activePageNums.length > 0 && isImageProgressKind(kind)
    ? `${formatWorkflowPageNumsForUser(activePageNums)}正在生成；完成后会直接出现在画布中。`
    : copy.detail;

  const metrics: WorkflowProgressMetric[] = [];
  if (progressValue) metrics.push({ label: "进度", value: progressValue });
  if (failed > 0) metrics.push({ label: "失败", value: `${failed} ${unit}` });
  if (targetText) metrics.push({ label: "处理范围", value: targetText });
  if (elapsed) metrics.push({ label: status === "queued" ? "已等待" : "已运行", value: elapsed });
  if (recent && recentSeconds >= 30) metrics.push({ label: "最近更新", value: recent });

  return {
    headline: copy.headline,
    summary,
    detail,
    metrics,
    steps: buildStepStatuses(copy.steps, progressStepIndex(kind, status || null, progress?.stage || activeRun?.stage || null, current, total)),
    percent,
    current,
    total,
    failed,
    unit,
    activePageNums,
    targetPageNums,
    status,
  };
}

export function getWorkflowProgressOverviewDisplay(
  disclosure: WorkflowProgressDisclosure,
  variant: WorkflowProgressOverviewVariant = "drawer"
): WorkflowProgressOverviewDisplay {
  if (variant === "agent") {
    return {
      showHeaderCopy: false,
      metrics: [],
      showSteps: false,
      showStandaloneTitle: false,
      showFooterSummary: false,
      showAgentCopy: false,
    };
  }
  if (variant === "empty") {
    return {
      showHeaderCopy: true,
      metrics: disclosure.metrics.slice(0, 5),
      showSteps: true,
      showStandaloneTitle: false,
      showFooterSummary: false,
      showAgentCopy: false,
    };
  }
  return {
    showHeaderCopy: true,
    metrics: disclosure.metrics.slice(0, 5),
    showSteps: true,
    showStandaloneTitle: false,
    showFooterSummary: false,
    showAgentCopy: true,
  };
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
      return "样张正在生成中，请稍候";
    case "prototype_ready":
      return "样张已生成，请检查效果；满意后点击「样张满意，生成全部」";
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
  // retry-failed is handled exclusively by StatusCard (getStatusCard).
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

// ============================================================
// StatusCard: 状态栏的唯一数据源
// 原则:任何时刻只显示一张卡 + 一个主 CTA(异常态最多一个副 CTA)
// 优先级:任务运行中 > 失败 > 未完成 > 过期 > 样张就绪 > 待操作
// ============================================================

export type StatusCardTone = "running" | "danger" | "warning" | "success" | "info";

export type StatusActionKey =
  | "stop"
  | "retry-failed"
  | "continue-generation"
  | "update-stale-visual"
  | "regenerate-stale-images"
  | "confirm-prototype"
  | "resample-prototype"
  | "start-prototype"
  | "generate-style"
  | "generate-visual-prompts"
  | "confirm-content"
  | "switch-to-visual"
  | "start-generation"
  | "download";

export interface StatusCardAction {
  key: StatusActionKey;
  label: string;
  variant: "primary" | "secondary" | "danger";
  disabled?: boolean;
  title?: string;
}

export interface StatusCardData {
  tone: StatusCardTone;
  title: string;
  description?: string;
  detail?: string;
  progress?: { current: number; total: number; percent: number; unit: string };
  primary?: StatusCardAction;
  secondary?: StatusCardAction;
}

export interface StatusCardInput {
  workflowState: WorkflowState;
  staleActionPlan: StaleSlideActionPlan;
  failedPageNums: number[];
  incompletePageNums: number[];
  visiblePrototypePageNums: number[];
  resamplePageNums: number[];
  prototypePromptTargetCount: number;
  completedSlideCount: number;
  totalSlideCount: number;
  progressDisclosure?: WorkflowProgressDisclosure | null;
  canStartPrototypeGeneration?: boolean;
  canStartFullGeneration?: boolean;
  latestProblemRun?: WorkflowRun | null;
}

function compactActiveRunTitle(kind?: string | null, status?: string | null) {
  const noun: Record<string, string> = {
    content_plan: "内容规划",
    style_proposal: "视觉方向",
    visual_prompts: "画面方案",
    prototype_generation: "样张生成",
    batch_generation: "批量生成",
    page_generation: "单页生成",
    retry_failed: "失败页重试",
    finetune: "单页微调",
  };
  const label = noun[String(kind || "")] || "任务";
  return status === "queued" ? `${label}排队中` : `${label}中`;
}

function isTerminalProblemRun(run?: WorkflowRun | null) {
  return Boolean(run && ["failed", "stale", "cancelled"].includes(String(run.status || "")));
}

function visualPromptProblemMessage(run?: WorkflowRun | null) {
  const raw = String(run?.error_msg || run?.message || "").trim();
  if (/logo placeholder/i.test(raw)) {
    const page = raw.match(/page\s+(\d+)/i)?.[1];
    return `${page ? `第 ${page} 页` : "有页面"}只生成了 Logo 占位信息，未产出可用画面方案。请重试；如果仍失败，可补充 Logo 或说明不要使用 Logo。`;
  }
  return raw || "后台没有产出完整画面方案，请重试。";
}

export function getStatusCard(input: StatusCardInput): StatusCardData | null {
  const {
    workflowState: w,
    staleActionPlan,
    failedPageNums,
    incompletePageNums,
    visiblePrototypePageNums,
    resamplePageNums,
    prototypePromptTargetCount,
    completedSlideCount,
    totalSlideCount,
    progressDisclosure,
    canStartPrototypeGeneration = true,
    latestProblemRun = null,
  } = input;

  // 优先级 1:任务正在运行
  if (w.activeRun) {
    const disc = progressDisclosure;
    const total = disc?.total || 0;
    const current = disc?.current || 0;
    const unit = disc?.unit || "页";
    return {
      tone: "running",
      title: compactActiveRunTitle(w.activeRun.kind, disc?.status || w.activeRun.status),
      progress: total > 0 ? { current, total, percent: disc?.percent || 0, unit } : undefined,
      primary: {
        key: "stop",
        label: "停止",
        variant: "secondary",
      },
    };
  }

  if (
    isTerminalProblemRun(latestProblemRun) &&
    latestProblemRun?.kind === "visual_prompts" &&
    (
      !w.hasPrompt ||
      incompletePageNums.length > 0 ||
      staleActionPlan.contentOrVisualCount > 0 ||
      Number(latestProblemRun.failed_count || 0) > 0 ||
      Number(latestProblemRun.completed_count || 0) < Number(latestProblemRun.total_count || 0)
    )
  ) {
    return {
      tone: "danger",
      title: "画面方案生成失败",
      description: visualPromptProblemMessage(latestProblemRun),
      primary: {
        key: "generate-visual-prompts",
        label: "重新生成画面方案",
        variant: "danger",
      },
    };
  }

  // 优先级 2:有失败页(异常态独占)
  if (w.hasFailedSlide && failedPageNums.length > 0) {
    const pageText = formatWorkflowPageNumsForUser(failedPageNums);
    return {
      tone: "danger",
      title: failedPageNums.length === 1
        ? `第 ${failedPageNums[0]} 页生成失败`
        : `${failedPageNums.length} 页生成失败`,
      description: failedPageNums.length === 1
        ? "点击重试可继续;反复失败请进入该页修改画面描述"
        : `失败页:${pageText}。点击重试可继续;反复失败请进入对应页面修改`,
      primary: {
        key: "retry-failed",
        label: "重试失败页",
        variant: "danger",
      },
    };
  }

  // 优先级 3:有未完成的页(可以继续生成)——仅在中断续跑场景显示
  // 条件:已可生图 + 至少已有部分页完成过(避免全新状态抢 start-prototype)
  const canContinueGeneration = ["prompt_ready", "completed"].includes(w.projectStatus);
  const hasPartiallyCompleted = completedSlideCount > 0;
  if (!w.activeRun && canContinueGeneration && hasPartiallyCompleted && incompletePageNums.length > 0) {
    const pageText = formatWorkflowPageNumsForUser(incompletePageNums);
    return {
      tone: "warning",
      title: incompletePageNums.length === 1
        ? `第 ${incompletePageNums[0]} 页尚未生成`
        : `${incompletePageNums.length} 页尚未生成`,
      description: incompletePageNums.length === 1
        ? "该页面等待生成，点击继续"
        : `第 ${pageText} 页等待生成，点击继续`,
      primary: {
        key: "continue-generation",
        label: "继续生成剩余页",
        variant: "primary",
      },
    };
  }

  const canSurfaceVisualStaleAction = w.hasSelectedStyle && [
    "visual_ready",
    "prompt_ready",
    "prototype_ready",
    "completed",
    "failed",
  ].includes(w.projectStatus);
  const canSurfaceImageStaleAction = w.hasPrompt && [
    "prompt_ready",
    "prototype_ready",
    "completed",
    "failed",
  ].includes(w.projectStatus);

  // 优先级 4:有过期画面(异常态独占)
  if (canSurfaceVisualStaleAction && staleActionPlan.primaryActionKey === "update_visual_plan") {
    return {
      tone: "warning",
      title: `${staleActionPlan.contentOrVisualCount} 页需要更新画面方案`,
      description: "内容或画面描述变更,需要先更新画面方案再重新生成图片",
      primary: {
        key: "update-stale-visual",
        label: "更新画面方案",
        variant: "primary",
      },
    };
  }
  if (canSurfaceImageStaleAction && staleActionPlan.primaryActionKey === "regenerate_images") {
    return {
      tone: "warning",
      title: `${staleActionPlan.imageOnlyCount} 页图片需要重新生成`,
      description: "确认画面方案后,重新生成这些页面即可",
      primary: {
        key: "regenerate-stale-images",
        label: "重新生成图片",
        variant: "primary",
      },
    };
  }

  // 优先级 5:样张已生成,等待用户判断
  if (w.projectStatus === "prototype_ready") {
    const scopeText = formatWorkflowPageNumsForUser(visiblePrototypePageNums);
    const totalSuffix = totalSlideCount > 0 && visiblePrototypePageNums.length < totalSlideCount
      ? `(共 ${totalSlideCount} 页)`
      : "";
    return {
      tone: "success",
      title: scopeText
        ? `样张已生成 · ${scopeText}${totalSuffix}`
        : "样张已生成",
      description: resamplePageNums.length > 0
        ? `当前样张范围为 ${resamplePageNums.length} 页;满意可生成全部,不满意可重打这些样张页`
        : "满意 → 点击「样张满意，生成全部」;不满意 → 在画布勾选页面后重打",
      primary: {
        key: "confirm-prototype",
        label: "样张满意，生成全部",
        variant: "primary",
      },
      secondary: resamplePageNums.length > 0
        ? {
            key: "resample-prototype",
            label: `重打样张(${resamplePageNums.length} 页)`,
            variant: "secondary",
          }
        : undefined,
    };
  }

  // 优先级 6:批量完成
  if (w.projectStatus === "completed") {
    return {
      tone: "success",
      title: `已生成 ${completedSlideCount} / ${totalSlideCount} 页`,
      description: "右上角可导出 PPTX;需要修改时选中页面进入微调",
      primary: {
        key: "download",
        label: "导出 PPTX",
        variant: "primary",
      },
    };
  }

  // 优先级 7:按 stage 引导(画面方案就绪 → 生成样张)
  if ((w.projectStatus === "prompt_ready" || w.projectStatus === "failed") && w.hasPrompt) {
    const scopeText = formatWorkflowPageNumsForUser(visiblePrototypePageNums);
    return {
      tone: "info",
      title: scopeText
        ? `样张范围:${scopeText}${totalSlideCount > 0 && visiblePrototypePageNums.length < totalSlideCount ? `(共 ${totalSlideCount} 页)` : ""}`
        : "下一步:生成样张",
      description: "勾选画布页面可改变范围,准备好后点击生成样张",
      primary: {
        key: "start-prototype",
        label: prototypePromptTargetCount > 0
          ? `生成样张(${prototypePromptTargetCount} 页)`
          : "选择样张页",
        variant: "primary",
        disabled: !canStartPrototypeGeneration || prototypePromptTargetCount === 0,
      },
    };
  }

  // 视觉方向待生成
  if (w.projectStatus === "visual_ready" && !w.hasSelectedStyle) {
    return {
      tone: "info",
      title: "下一步:生成视觉方向",
      description: "可先在「素材库」上传 Logo、风格参考或模板;没有素材也可直接生成",
      primary: {
        key: "generate-style",
        label: w.isBusy ? "生成中..." : "生成视觉方向",
        variant: "primary",
        disabled: w.isBusy,
      },
    };
  }

  // 画面方案待生成
  if (w.projectStatus === "visual_ready" && w.hasSelectedStyle && !w.hasPrompt) {
    return {
      tone: "info",
      title: "下一步:生成画面方案",
      description: "为每页生成画面方案和生图 Prompt,然后再生成样张",
      primary: {
        key: "generate-visual-prompts",
        label: "生成画面方案",
        variant: "primary",
      },
    };
  }

  // 内容规划阶段:待确认
  if ((w.projectStatus === "planning" || w.projectStatus === "content_plan_ready") && !w.contentPlanConfirmed) {
    return {
      tone: "info",
      title: "下一步:确认内容",
      description: "检查页数、标题和顺序,确认进入视觉阶段",
      primary: {
        key: "confirm-content",
        label: "确认内容,请视觉总监",
        variant: "primary",
      },
    };
  }

  // 内容已确认:请视觉总监
  if ((w.projectStatus === "planning" || w.projectStatus === "content_plan_ready") && w.contentPlanConfirmed) {
    return {
      tone: "info",
      title: "下一步:进入视觉阶段",
      description: "内容已确认,接下来由视觉总监生成整体方向",
      primary: {
        key: "switch-to-visual",
        label: "请视觉总监介入",
        variant: "primary",
      },
    };
  }

  return null;
}
