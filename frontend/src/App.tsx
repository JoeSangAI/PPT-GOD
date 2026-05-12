import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, DragEvent as ReactDragEvent, MouseEvent as ReactMouseEvent, SyntheticEvent } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";
import TurndownService from "turndown";
import { tables as gfmTables, strikethrough as gfmStrikethrough } from "turndown-plugin-gfm";
import PptGodLogo from "./components/PptGodLogo";

// 修复 marked 无法解析 **text标点**后接字符 的粗体（CommonMark 规范限制）
const fixMarkedBoldHtml = (html: string): string => {
  return html.replace(/\*\*([^*]+?)\*\*([^<\s])/g, "<strong>$1</strong>$2");
};

const normalizeMarkdownEmphasis = (md: string): string => {
  const cleanLine = (line: string, delimiter: string) => {
    const positions: number[] = [];
    let idx = line.indexOf(delimiter);
    while (idx !== -1) {
      positions.push(idx);
      idx = line.indexOf(delimiter, idx + delimiter.length);
    }
    if (positions.length % 2 === 0) return line;
    const leadingWhitespace = line.match(/^\s*/)?.[0] || "";
    const stripped = line.slice(leadingWhitespace.length);
    if (stripped.startsWith(delimiter)) return `${line}${delimiter}`;
    if (line.trimEnd().endsWith(delimiter)) {
      return `${leadingWhitespace}${delimiter}${line.slice(leadingWhitespace.length)}`;
    }
    const removeAt = positions[positions.length - 1];
    return line.slice(0, removeAt) + line.slice(removeAt + delimiter.length);
  };
  return (md || "")
    .split("\n")
    .map((line) => cleanLine(cleanLine(line, "**"), "__"))
    .join("\n");
};

const renderMarkdown = (md: string, chatStyle = false): string => {
  const normalized = normalizeMarkdownEmphasis(md || "");
  let html = (marked.parse(normalized, { async: false }) as string) || "";
  html = fixMarkedBoldHtml(html);
  if (chatStyle) {
    html = html.replace(/<p\b/g, '<p class="mb-2 last:mb-0" style="white-space:pre-wrap"');
    html = html.replace(/<ul\b/g, '<ul class="list-disc pl-4 mb-2">');
    html = html.replace(/<ol\b/g, '<ol class="list-decimal pl-4 mb-2">');
    html = html.replace(/<li\b/g, '<li class="mb-1">');
    html = html.replace(/<strong\b/g, '<strong class="font-semibold text-gray-900">');
    html = html.replace(/<h1\b/g, '<h1 class="text-base font-bold mb-2 mt-1">');
    html = html.replace(/<h2\b/g, '<h2 class="text-sm font-bold mb-2 mt-1">');
    html = html.replace(/<h3\b/g, '<h3 class="text-sm font-semibold mb-1 mt-1">');
    html = html.replace(/<code\b/g, '<code class="bg-gray-200 px-1 py-0.5 rounded text-xs font-mono">');
    html = html.replace(/<pre\b/g, '<pre class="bg-gray-200 p-2 rounded text-xs overflow-auto mb-2">');
  } else {
    // 非聊天模式：给表格加 Tailwind 基础样式（display / border-collapse / 字体大小）
    html = html.replace(/<table\b/g, '<table class="table-auto w-full text-xs border border-slate-300"');
    html = html.replace(/<thead\b/g, '<thead class="bg-slate-100"');
    html = html.replace(/<th\b/g, '<th class="border border-slate-300 px-2 py-1 text-left font-medium"');
    html = html.replace(/<td\b/g, '<td class="border border-slate-300 px-2 py-1"');
  }
  // 消毒 HTML，防止 XSS（保留允许的样式类和标签）
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS: [
      "p", "br", "strong", "em", "b", "i", "u", "ul", "ol", "li",
      "h1", "h2", "h3", "h4", "h5", "h6", "code", "pre", "span", "div",
      "table", "thead", "tbody", "tr", "th", "td", "style"
    ],
    ALLOWED_ATTR: ["class", "style"],
  });
};

import { type StyleProposal } from "./components/StyleProposalSelector";
import ChatStyleProposal from "./components/ChatStyleProposal";
import TemplateRecommender from "./components/TemplateRecommender";
import VisualAssetsPanel from "./components/VisualAssetsPanel";
import ToastContainer, { type ToastItem } from "./components/Toast";
import { useProjectWorkflow } from "./hooks/useProjectWorkflow";
import {
  STATUS_LABEL,
  WORKFLOW_STEPS,
  buildGateContext,
  buildWorkflowState,
  getGuidanceText as getWorkflowGuidanceText,
  getPrimaryActionKey,
  getSecondaryActionKeys,
  type GateContext,
  type GateActionKey,
  type WorkflowGate,
} from "./workflow";
import {
  inferAgentRequestContext,
  inferRequestedPageCount,
  type AgentRequestContext,
  type AgentRequestScope,
  type AgentRole,
} from "./agentRequestContext";

import {
  API_BASE,
  fetchProjects,
  fetchProject,
  createProject,
  generateContentPlan,
  generateVisualPrompts,
  generatePrompts,
  generateVisualPlan,
  fetchSlides,
  startGeneration,
  stopGeneration,
  confirmPrototype,
  fetchWorkflowStatus,
  getDownloadUrl,
  getContentPlanMarkdownUrl,
  uploadFile,
  fetchReferenceImages,
  deleteReferenceImage,
  updateReferenceImage,
  updateSlideAssetPins,
  updateSlideOverlayLayers,
  suggestReferenceImages,
  retrySlide,
  retryFailed,
  chatWithAgentStream,
  updateProject,
  updateProjectStyle,
  generateStyleProposals,
  pollForStyleProposals,
  deleteProject,
  uploadDocument,
  fetchDocuments,
  deleteDocument,
  updateSlideContent,
  updateVisualPlan,
  deleteSlide,
  createSlide,
  reorderSlides,
  extractTemplate,
  fetchTemplatePages,
  updateTemplateRecommendations,
  rollbackProject,
  finetuneSlide,
  getSlideVersions,
  deleteSlideVersion,
  restoreSlideVersion,
} from "./api/client";

interface Project {
  id: string;
  title: string;
  status: string;
  style_id: string | null;
  content_plan_confirmed: boolean;
  style_proposal: any | null;
  selected_style: any | null;
  selected_template_recommendations: any | null;
  has_unread_notification?: boolean;
  unread_notification_message?: string | null;
  created_at: string;
  completed_slides?: number;
}

function clearProjectNotification(project: Project): Project {
  if (!project.has_unread_notification && !project.unread_notification_message) return project;
  return { ...project, has_unread_notification: false, unread_notification_message: null };
}

function normalizeProjectsForActiveSelection(projects: Project[], activeProjectId: string | null): Project[] {
  if (!activeProjectId) return projects;
  return projects.map((project) => (project.id === activeProjectId ? clearProjectNotification(project) : project));
}

function projectStyleLabel(project: Project): string {
  return (
    project.selected_style?.name ||
    project.style_proposal?.proposals?.[0]?.name ||
    project.style_id ||
    "默认风格"
  );
}

interface Slide {
  id: string;
  page_num: number;
  type: string;
  status: string;
  content_json: any;
  visual_json: any;
  prompt_text: string | null;
  image_path?: string | null;
  error_msg?: string | null;
  reference_images?: { id: string; role: string; url: string }[];
}

const PROTOTYPE_FAMILY_ORDER = ["bookend", "toc", "section", "hero", "data", "content"];

const inferPrototypeFamily = (slide: Slide): string => {
  const visualFamily = slide.visual_json?.seed_family;
  if (visualFamily) return String(visualFamily);
  const pageType = String(slide.visual_json?.type || slide.type || "content").toLowerCase();
  if (pageType === "cover" || pageType === "ending") return "bookend";
  if (pageType === "hero" || pageType === "quote") return "hero";
  if (pageType === "toc") return "toc";
  if (pageType === "section") return "section";
  if (pageType === "data") return "data";
  return "content";
};

const defaultPrototypePageNumsForSlides = (slides: Slide[]): number[] => {
  const byFamily = new Map<string, Slide>();
  [...slides]
    .sort((a, b) => a.page_num - b.page_num)
    .forEach((slide) => {
      const family = inferPrototypeFamily(slide);
      const current = byFamily.get(family);
      const slideIsSeed = Boolean(slide.visual_json?.is_seed_recommended);
      const currentIsSeed = Boolean(current?.visual_json?.is_seed_recommended);
      if (!current || (slideIsSeed && !currentIsSeed)) {
        byFamily.set(family, slide);
      }
    });

  const orderedFamilies = [
    ...PROTOTYPE_FAMILY_ORDER,
    ...Array.from(byFamily.keys()).filter((family) => !PROTOTYPE_FAMILY_ORDER.includes(family)).sort(),
  ];
  return orderedFamilies
    .map((family) => byFamily.get(family)?.page_num)
    .filter((pageNum): pageNum is number => Number.isFinite(pageNum));
};

interface ChatAttachment {
  id: string;
  name: string;
  url: string;
  role?: string;
}

interface PositioningData {
  core_thesis: string;
  strategy: string;
  tone: string;
  estimated_pages: number;
  key_highlights: string[];
}

type AgentNextActionType =
  | "generate_content_plan"
  | "switch_to_visual"
  | "switch_to_content"
  | "generate_style_proposals"
  | "generate_visual_prompts"
  | "generate_images"
  | "start_prototype"
  | "confirm_prototype"
  | "start_generation"
  | "retry_failed"
  | "download";

interface AgentNextAction {
  type: AgentNextActionType;
  label: string;
  description?: string;
  confirm?: boolean;
  payload?: {
    topic?: string;
    page_count?: number;
    page_nums?: number[];
  };
}

type GateActionPayload = {
  topic?: string;
  page_count?: number;
  page_nums?: number[];
  attachment_ids?: string[];
  style?: StyleProposal | any;
};

interface GateActionResult {
  ok: boolean;
  reason?: "missing_project" | "busy" | "chat_loading" | "stale_gate" | "invalid_input" | "not_ready" | "failed";
  message?: string;
  runId?: string;
}

const VISUAL_STYLE_PROPOSE_MESSAGE = "请基于我已上传的素材帮我生成风格提案。";
const VISUAL_STYLE_REGENERATE_MESSAGE = "请基于当前最新的素材和我们之前的讨论，重新给我一套风格提案。";

interface ChatMessage {
  role: "user" | "agent" | "system";
  content: string;
  displayContent?: string;
  projectId?: string;
  action?: string;
  positioning?: PositioningData;
  topic?: string;
  nextAction?: AgentNextAction;
  agentRole?: "content" | "visual" | "finetune";
  gate?: WorkflowGate;
  gateRevision?: number;
  loading?: boolean;
  id?: string;
  runId?: string;
  hasStyleProposal?: boolean;
  styleProposals?: StyleProposal[];
  attachments?: ChatAttachment[];
}

interface PendingChatRequest {
  projectId: string;
  message: string;
  history: { role: string; content: string }[];
  pageContext?: any;
  agentRole: "content" | "visual";
  requestContext?: AgentRequestContext;
  attachmentIds?: string[];
  retryCount?: number;
  createdAt?: number;
  updatedAt?: number;
}

interface RunCompletionFollowup {
  agentRole: "content" | "visual";
  content: string;
  nextAction?: AgentNextAction;
}

function getBriefSubmissionDisplayContent(content: string, explicitDisplayContent?: string) {
  const display = (explicitDisplayContent || "").trim();
  if (display) return display;

  const text = (content || "").trim();
  if (text === "直接生成") return "Brief 已提交，正在生成内容规划。";

  const match = text.match(/^(已提交 Brief，开始生成内容规划|确认建议，开始生成内容规划)(?:\s|$)/);
  if (!match) return text;

  const intro = match[1] === "确认建议，开始生成内容规划"
    ? "建议已确认，正在生成内容规划。"
    : "Brief 已提交，正在生成内容规划。";
  const details = text.slice(match[0].length).trim();
  return details ? `${intro}\n\n${details}` : intro;
}

function truncateBriefDisplay(text: string, limit = 1200) {
  const cleaned = (text || "").trim();
  if (!cleaned || cleaned.length <= limit) return cleaned;
  return `${cleaned.slice(0, limit).trimEnd()}\n...`;
}

function buildSubmittedBriefDisplayContent({
  fromSuggestion,
  userBrief,
  attachmentSummary,
  pageCount,
}: {
  fromSuggestion: boolean;
  userBrief: string;
  attachmentSummary?: string;
  pageCount?: number;
}) {
  const lines = [fromSuggestion ? "建议已确认，正在生成内容规划。" : "Brief 已提交，正在生成内容规划。"];
  const brief = truncateBriefDisplay(userBrief);
  if (brief) lines.push(`本次要求：\n${brief}`);
  if (pageCount) lines.push(`识别到页数目标：约 ${pageCount} 页`);
  if (attachmentSummary) lines.push(`已上传材料：\n${attachmentSummary}`);
  return lines.join("\n\n");
}

function buildRunCompletionFollowup({
  runKind,
  runStatus,
  runError,
  projectStatus,
  completedCount,
  targetCompletedCount,
  failedCount,
  targetCount,
  totalSlides,
  hasSelectedStyle,
  hasPrompt,
  styleProposalCount,
}: {
  runKind: string | null;
  runStatus?: string | null;
  runError?: string | null;
  projectStatus: string | null;
  completedCount: number;
  targetCompletedCount?: number;
  failedCount?: number;
  targetCount?: number;
  totalSlides: number;
  hasSelectedStyle: boolean;
  hasPrompt: boolean;
  styleProposalCount: number;
}): RunCompletionFollowup {
  const status = projectStatus || "未知";
  const runFinishedWithProblem = ["failed", "stale", "cancelled"].includes(String(runStatus || ""));
  if (runFinishedWithProblem) {
    const scopedCompleted = Math.max(0, Number(targetCompletedCount ?? completedCount ?? 0));
    const scopedTotal = Math.max(0, Number(targetCount ?? totalSlides ?? scopedCompleted));
    const scopedFailed = Math.max(0, Number(failedCount ?? Math.max(0, scopedTotal - scopedCompleted)));
    const scopeLabel = runKind === "prototype_generation"
      ? "打样"
      : runKind === "content_plan"
      ? "内容规划"
      : runKind === "style_proposal"
      ? "视觉方向"
      : runKind === "visual_prompts"
      ? "画面方案"
      : "图片生成";
    const errorLine = runError ? `\n\n错误：${runError}` : "";
    const canRetryFailedPages = isImageRunKind(runKind) && scopedFailed > 0 && runStatus !== "cancelled";
    return {
      agentRole: runKind === "content_plan" ? "content" : "visual",
      content: `⚠️ ${scopeLabel}没有成功完成，当前完成 ${scopedCompleted} / ${scopedTotal || scopedCompleted} 页${scopedFailed ? `，失败 ${scopedFailed} 页` : ""}。${errorLine}\n\n👉 下一步：${canRetryFailedPages ? "检查失败页后点击「一键重试失败页」。" : "检查当前页面状态后重新发起任务。"}`,
      nextAction: canRetryFailedPages ? { type: "retry_failed", label: "一键重试失败页", confirm: true } : undefined,
    };
  }
  if (runKind === "content_plan") {
    return {
      agentRole: "content",
      content: "✅ 内容规划已生成。\n\n👉 下一步：请检查页数、标题和顺序；没问题后点击「确认内容，请视觉总监」。进入视觉阶段后，可以先上传 Logo、参考图或模板，再生成视觉方向。",
      nextAction: { type: "switch_to_visual", label: "确认内容，请视觉总监" },
    };
  }
  if (runKind === "style_proposal") {
    if (styleProposalCount > 0) {
      return {
        agentRole: "visual",
        content: `✅ 视觉方向已生成，共 ${styleProposalCount} 套。\n\n👉 下一步：在作品画布选择一套方向，点击「确认并生成画面方案」。`,
      };
    }
    return {
      agentRole: "visual",
      content: "✅ 已进入视觉方案阶段。\n\n👉 下一步：先在「项目素材」补充 Logo、参考图或模板；没有素材也可以直接点击「生成视觉方向」。",
      nextAction: { type: "generate_style_proposals", label: "生成视觉方向" },
    };
  }
  if (runKind === "visual_prompts") {
    return {
      agentRole: "visual",
      content: "✅ 画面设计已完成：每页画面描述和生图 Prompt 已生成。\n\n👉 下一步：先生成打样页预览；满意后再生成全部页面。",
      nextAction: { type: "start_prototype", label: "打样确认", confirm: true },
    };
  }
  if (projectStatus === "prototype_ready") {
    return {
      agentRole: "visual",
      content: "✅ 打样图片已生成，页面已刷新。\n\n👉 下一步：检查样张效果；满意后点击「确认打样，生成全部」，不满意可以重新打样或调整风格。",
      nextAction: { type: "confirm_prototype", label: "确认打样，生成全部", confirm: true },
    };
  }
  if (runKind === "batch_generation" || runKind === "page_generation" || runKind === "retry_failed") {
    if (projectStatus === "completed") {
      return {
        agentRole: "visual",
        content: `✅ 全量生成已完成，共 ${completedCount} / ${totalSlides || completedCount} 页。\n\n👉 下一步：点击上方「下载 PPTX」获取最终文件；需要调整时可选中页面重新生成。`,
        nextAction: { type: "download", label: "下载 PPTX" },
      };
    }
    if (projectStatus === "failed") {
      return {
        agentRole: "visual",
        content: `⚠️ 图片生成任务已结束，当前已有 ${completedCount} / ${totalSlides || completedCount} 页完成。\n\n👉 下一步：点击「一键重试失败页」继续补齐。`,
        nextAction: { type: "retry_failed", label: "一键重试失败页", confirm: true },
      };
    }
    return {
      agentRole: "visual",
      content: `✅ 图片生成任务已结束，当前已有 ${completedCount} / ${totalSlides || completedCount} 页完成。\n\n👉 下一步：检查失败页并重试，或继续调整需要修改的页面。`,
    };
  }
  if (projectStatus === "visual_ready" && !hasSelectedStyle) {
    if (styleProposalCount > 0) {
      return {
        agentRole: "visual",
        content: "✅ 视觉方案阶段已就绪。\n\n👉 下一步：在作品画布选择一套视觉方向，点击「确认并生成画面方案」。",
      };
    }
    return {
      agentRole: "visual",
      content: "✅ 视觉方案阶段已就绪。\n\n👉 下一步：先在「项目素材」补充 Logo、参考图或模板；没有素材也可以直接点击「生成视觉方向」。",
      nextAction: { type: "generate_style_proposals", label: "生成视觉方向" },
    };
  }
  if (projectStatus === "visual_ready" && hasSelectedStyle && !hasPrompt) {
    return {
      agentRole: "visual",
      content: "✅ 视觉方向已确认。\n\n👉 下一步：生成每页画面方案和生图 Prompt，然后再打样。",
      nextAction: { type: "generate_visual_prompts", label: "生成画面方案" },
    };
  }
  if (projectStatus === "prompt_ready" || (hasSelectedStyle && hasPrompt)) {
    return {
      agentRole: "visual",
      content: "✅ 页面状态已更新。\n\n👉 下一步：检查每页画面方案，然后点击「打样确认」。",
      nextAction: { type: "start_prototype", label: "打样确认", confirm: true },
    };
  }
  return {
    agentRole: "visual",
    content: `✅ 当前任务已结束，页面状态已更新为「${status}」。\n\n👉 下一步：请查看作品画布里的当前阶段操作按钮，或直接告诉我你想继续怎么改。`,
  };
}

interface UiAction {
  key: string;
  label: string;
  onClick?: () => void;
  href?: string;
  variant?: "primary" | "secondary" | "danger" | "link";
  disabled?: boolean;
}

interface StageNudge {
  title: string;
  body: string;
  role: "content" | "visual";
  primary?: UiAction;
  secondary?: UiAction;
  tone?: "content" | "visual" | "final" | "warning";
}

const CONTENT_PLAN_TIMEOUT_MS = 300_000; // 内容规划 LLM 调用预留 5 分钟
const CONTENT_PLAN_START_LATCH_GRACE_MS = 8_000;
const VISUAL_PROMPT_MAX_POLL_ERRORS = 5;
const GENERATION_MAX_POLL_ERRORS = 5;
const IMAGE_URL_SESSION_KEY = Date.now();
const BRIEF_IMAGE_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".bmp", ".tif", ".tiff", ".heic"]);
const AGENT_ATTACHMENT_ACCEPT = ".pdf,.doc,.docx,.ppt,.pptx,.md,.markdown,.txt,.csv,.json,.html,.htm,.png,.jpg,.jpeg,.webp,.gif,.svg,.bmp,.tif,.tiff,.heic";
const BRIEF_ATTACHMENT_RE = /\[\[PPTGOD_ATTACHMENT:(doc|image):([^\]]+)]]/g;
const CHAT_HISTORY_SCHEMA_KEY = "ppt_god_chat_history_schema";
const CHAT_HISTORY_SCHEMA_VERSION = "project-scoped-v2";
const PENDING_CHAT_TTL_MS = 30 * 60 * 1000;

function isRunActive(run: any) {
  return !!run && (run.status === "queued" || run.status === "running");
}

function isBriefImageFile(file: File) {
  const lowerName = file.name.toLowerCase();
  const ext = lowerName.includes(".") ? lowerName.slice(lowerName.lastIndexOf(".")) : "";
  return file.type.startsWith("image/") || BRIEF_IMAGE_EXTENSIONS.has(ext);
}

function makeBriefAttachmentToken(kind: "doc" | "image", id: string) {
  return `[[PPTGOD_ATTACHMENT:${kind}:${encodeURIComponent(id)}]]`;
}

function parseBriefAttachmentToken(token: string): { kind: "doc" | "image"; id: string } | null {
  const match = token.match(/^\[\[PPTGOD_ATTACHMENT:(doc|image):([^\]]+)]]$/);
  if (!match) return null;
  try {
    return { kind: match[1] as "doc" | "image", id: decodeURIComponent(match[2]) };
  } catch {
    return { kind: match[1] as "doc" | "image", id: match[2] };
  }
}

function escapeHtml(value: string) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function isTransientRunMessage(message: ChatMessage) {
  if (message.loading) return true;
  const content = message.content || "";
  if (/响应(?:未返回完整结果|不完整，正在自动重试)/.test(content)) return true;
  if (message.role !== "agent") return false;
  return (
    /正在(?:启动|生成|构建|重新生成|准备|处理)/.test(content) &&
    /(?:第\s*\d+\s*\/\s*\d+\s*页|\d+\s*\/\s*\d+\s*(?:页|套)完成)/.test(content)
  );
}

function isWorkflowTransitionMessage(message: ChatMessage) {
  const content = (message.content || "").trim();
  if (message.role === "system" && /^用户回退到/.test(content)) return true;
  if (message.role !== "agent") return false;
  return /^(?:⏪\s*)?已(?:回退到|回到)「.+?」/.test(content);
}

function sanitizeChatHistory(messages: ChatMessage[]) {
  return (messages || []).filter((m) => !isTransientRunMessage(m));
}

function normalizeProjectChatHistory(
  projectId: string,
  messages: ChatMessage[],
  options: { allowLegacy?: boolean } = {}
) {
  return sanitizeChatHistory(messages)
    .filter((message) => message.projectId === projectId || (options.allowLegacy && !message.projectId))
    .map((message) => ({ ...message, projectId }));
}

function clearLegacyChatStorageIfNeeded() {
  if (localStorage.getItem(CHAT_HISTORY_SCHEMA_KEY) === CHAT_HISTORY_SCHEMA_VERSION) return;
  localStorage.setItem(CHAT_HISTORY_SCHEMA_KEY, CHAT_HISTORY_SCHEMA_VERSION);
}

const getBriefDraftStorageKey = (projectId: string) =>
  `ppt_god_composer_draft_brief_${projectId}`;

const getAgentDraftStorageKey = (projectId: string, role: "content" | "visual" | "finetune", slideId?: string | null) =>
  role === "finetune"
    ? `ppt_god_composer_draft_agent_${projectId}_${role}_${slideId || "unselected"}`
    : `ppt_god_composer_draft_agent_${projectId}_${role}`;

const getPendingChatStorageKey = (projectId: string) =>
  `ppt_god_pending_chat_${projectId}`;

function normalizePendingChat(projectId: string, value: any): PendingChatRequest | null {
  if (!value || typeof value !== "object" || value.projectId !== projectId) return null;
  if (typeof value.message !== "string") return null;
  if (value.agentRole !== "content" && value.agentRole !== "visual") return null;

  const updatedAt = Number(value.updatedAt || value.createdAt || 0);
  if (updatedAt && Date.now() - updatedAt > PENDING_CHAT_TTL_MS) return null;

  const history = Array.isArray(value.history)
    ? value.history
        .filter((item: any) => item && typeof item.role === "string" && typeof item.content === "string")
        .map((item: any) => ({ role: item.role, content: item.content }))
    : [];

  return {
    projectId,
    message: value.message,
    history,
    pageContext: value.pageContext,
    agentRole: value.agentRole,
    requestContext: value.requestContext,
    attachmentIds: Array.isArray(value.attachmentIds) ? value.attachmentIds.filter((id: any) => typeof id === "string") : [],
    retryCount: Math.max(0, Number(value.retryCount || 0)),
    createdAt: Number(value.createdAt || updatedAt || Date.now()),
    updatedAt: updatedAt || Date.now(),
  };
}

function readPendingChat(projectId: string): PendingChatRequest | null {
  try {
    const raw = localStorage.getItem(getPendingChatStorageKey(projectId));
    const pending = raw ? normalizePendingChat(projectId, JSON.parse(raw)) : null;
    if (!pending && raw) localStorage.removeItem(getPendingChatStorageKey(projectId));
    return pending;
  } catch {
    localStorage.removeItem(getPendingChatStorageKey(projectId));
    return null;
  }
}

function writePendingChat(pending: PendingChatRequest) {
  try {
    localStorage.setItem(
      getPendingChatStorageKey(pending.projectId),
      JSON.stringify({ ...pending, updatedAt: Date.now(), createdAt: pending.createdAt || Date.now() })
    );
  } catch (err) {
    console.warn("Persist pending chat failed:", err);
  }
}

function clearStoredPendingChat(projectId: string) {
  try {
    localStorage.removeItem(getPendingChatStorageKey(projectId));
  } catch {
    // ignore storage cleanup failure
  }
}

function readComposerDraft(key: string) {
  try {
    return localStorage.getItem(key) || "";
  } catch {
    return "";
  }
}

function writeComposerDraft(key: string, value: string) {
  try {
    if (value) {
      localStorage.setItem(key, value);
    } else {
      localStorage.removeItem(key);
    }
  } catch (err) {
    console.warn("Persist composer draft failed:", err);
  }
}

function cleanProgressMessage(message?: string) {
  if (!message) return "";
  return message
    .replace(/[🧠🚀⏳✅📝🎨]/gu, "")
    .replace(/（?批次\s*\d+\s*\/\s*\d+）?/g, "")
    .replace(/\d+\s*\/\s*\d+\s*页完成/g, "")
    .replace(/\.\.\./g, "")
    .replace(/……/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function secondsSinceIso(value?: string | null) {
  if (!value) return 0;
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return 0;
  return Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
}

function formatWaitDuration(seconds: number) {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 10) return "";
  if (s < 60) return `${s} 秒`;
  const minutes = Math.floor(s / 60);
  const rest = s % 60;
  return rest > 0 ? `${minutes} 分 ${rest} 秒` : `${minutes} 分钟`;
}

function queuedRunText(run: any, fallback: string) {
  const base =
    run?.kind === "style_proposal"
      ? "视觉方向已排队，等待开始"
      : run?.kind === "visual_prompts"
      ? "画面方案已排队，等待开始"
      : run?.kind === "content_plan"
      ? "内容规划已排队，等待开始"
      : isImageRunKind(run?.kind)
      ? "图片生成已排队，等待开始"
      : `${fallback}已排队，等待开始`;
  const waited = formatWaitDuration(secondsSinceIso(run?.started_at));
  return waited ? `${base}，已等待 ${waited}` : base;
}

function userFacingGenerationError(message?: string) {
  const text = String(message || "").trim();
  const known: Record<string, string> = {
    style_reference_file_missing: "风格参考原图暂时不可用，已改为使用已提取的风格信息继续生成。请重试一次。",
  };
  if (known[text]) return known[text];
  if (/failed to fetch|networkerror|network request failed|load failed/i.test(text)) {
    return "连接中断，未能确认后台任务状态。请刷新状态或重试生成。";
  }
  if (text.includes("图片接口上传超时")) {
    return "参考图上传超时：已停止自动重试，避免重复消耗额度。可把必须完整保留的图片改为「精确粘贴」，或稍后重试。";
  }
  if (/429|rate limit|too many requests/i.test(text)) {
    return "生图接口当前限流或繁忙。系统会按接口返回的等待时间重试；如果仍失败，请稍后重试失败页。";
  }
  return text.replace(/\b[a-z]+(?:_[a-z0-9]+){2,}\b/g, "素材状态异常").trim() || "未知错误";
}

function runProgressText(run: any) {
  if (!run) return "任务处理中...";
  const total = Math.max(0, Number(run.total_count || 0));
  const completed = Math.min(total || Number(run.completed_count || 0), Math.max(0, Number(run.completed_count || 0)));
  const fallback =
    run.kind === "content_plan"
      ? "正在生成内容规划"
      : run.kind === "style_proposal"
      ? "正在生成风格提案"
      : run.kind === "visual_prompts"
      ? "正在生成画面描述和 Prompt"
      : run.kind === "prototype_generation"
      ? "正在生成打样图片"
      : "正在生成图片";
  if (run.status === "queued") return queuedRunText(run, fallback);
  const message = cleanProgressMessage(run.message) || fallback;
  const unit = run.kind === "style_proposal" ? "套" : "页";
  return total > 0 ? `${message}：${completed} / ${total} ${unit}完成` : message;
}

function formatPageNums(pageNums: number[], limit = 4) {
  const unique = Array.from(new Set((pageNums || []).map(Number).filter(Number.isFinite))).sort((a, b) => a - b);
  if (unique.length <= limit) return unique.join("、");
  return `${unique.slice(0, limit).join("、")} 等 ${unique.length} 页`;
}

function isImageRunKind(kind?: string | null) {
  return ["prototype_generation", "batch_generation", "page_generation", "retry_failed", "finetune"].includes(String(kind || ""));
}

function workflowProgressText(status: any) {
  const progress = status?.progress;
  if (!progress) return runProgressText(status?.active_run);
  const total = Math.max(0, Number(progress.total ?? progress.total_pages ?? 0));
  const current = Math.min(total || Number(progress.current ?? progress.current_page ?? 0), Math.max(0, Number(progress.current ?? progress.current_page ?? 0)));
  const unit = progress.unit || (progress.kind === "style_proposal" ? "套" : "页");
  const message = cleanProgressMessage(progress.message) || progress.label || "任务处理中";
  const activeRun = status?.active_run || {};
  if ((progress.status || activeRun.status) === "queued") {
    return queuedRunText({ ...activeRun, ...progress, started_at: activeRun.started_at || progress.started_at }, message);
  }
  const activePages = Array.isArray(progress.active_page_nums) ? progress.active_page_nums.map(Number).filter(Number.isFinite) : [];
  if (activePages.length > 0 && isImageRunKind(progress.kind || status?.active_run?.kind)) {
    const activeText = activePages.length === 1
      ? `正在生成第 ${activePages[0]} 页`
      : `正在并行生成第 ${formatPageNums(activePages)} 页`;
    return total > 0 ? `${activeText}：${current} / ${total} ${unit}完成` : activeText;
  }
  return total > 0 ? `${message}：${current} / ${total} ${unit}完成` : message;
}

function workflowProgressCounts(status: any) {
  const progress = status?.progress;
  const total = Math.max(0, Number(progress?.total ?? progress?.total_pages ?? status?.target_count ?? status?.total_slides ?? 0));
  const current = Math.min(
    total || Number(progress?.current ?? progress?.current_page ?? status?.target_completed_slides ?? status?.completed_slides ?? 0),
    Math.max(0, Number(progress?.current ?? progress?.current_page ?? status?.target_completed_slides ?? status?.completed_slides ?? 0))
  );
  const failed = Math.max(0, Number(progress?.failed ?? status?.target_failed_slides ?? 0));
  const unit = progress?.unit || (progress?.kind === "style_proposal" || status?.active_run?.kind === "style_proposal" ? "套" : "页");
  const percent = total > 0 ? Math.min(100, (current / total) * 100) : 0;
  const activePageNums = Array.isArray(progress?.active_page_nums)
    ? progress.active_page_nums.map(Number).filter(Number.isFinite)
    : [];
  const statusText = progress?.status || status?.active_run?.status || null;
  return { current, total, failed, unit, percent, activePageNums, status: statusText };
}

function getSlideImageUrl(imagePath: string, status?: string, cacheKey?: string | number) {
  const base = `${API_BASE}${imagePath.replace("./outputs", "/outputs")}`;
  const version = cacheKey ?? `${status || "image"}-${IMAGE_URL_SESSION_KEY}`;
  const cacheBuster = `?v=${encodeURIComponent(String(version))}`;
  return `${base}${cacheBuster}`;
}

function shouldShowLogoOverlay(slide: any) {
  const policy = slide?.visual_json?.logo_policy;
  if (String(policy?.render_variant || "").toLowerCase() === "omit") return false;
  if (policy && typeof policy.show_logo === "boolean") return policy.show_logo;
  const pageType = String(slide?.visual_json?.type || slide?.type || "content").toLowerCase();
  const layout = String(slide?.visual_json?.layout || "").toLowerCase();
  if (pageType === "cover" || pageType === "ending") return true;
  if (pageType === "hero" || pageType === "quote") return false;
  if (layout === "hero" || layout === "content_hero") return false;
  return true;
}

function isConfirmedLogoRef(ref: any) {
  if (!ref || ref.role !== "logo") return false;
  const status = String(ref.review_status || ref.asset_analysis?.review_status || "auto_confirmed").toLowerCase();
  return status === "auto_confirmed" || status === "user_confirmed";
}

function referenceDedupeKey(ref: any) {
  const analysis = ref?.asset_analysis || {};
  const source = ref?.source_document || analysis.source_document || "";
  const page = ref?.source_page_num || analysis.pptx_source_page_num || ref?.page_num || "";
  const digest = analysis.pptx_image_sha1 || analysis.pptx_raw_image_sha1 || ref?.url || ref?.id || "";
  return `${ref?.role || ""}|${source}|${page}|${digest}`;
}

function dedupeReferenceImages<T>(items?: T[]) {
  const seen = new Set<string>();
  return (items || []).filter((item: any) => {
    const key = referenceDedupeKey(item);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }) as T[];
}

const HEX_COLOR_PATTERN = /#(?:[0-9a-fA-F]{3}){1,2}\b/g;

function stripHexCodes(value: any) {
  return String(value || "")
    .replace(HEX_COLOR_PATTERN, "")
    .replace(/\s+([，。；;,.])/g, "$1")
    .replace(/（\s*）|\(\s*\)/g, "")
    .replace(/\s{2,}/g, " ")
    .trim();
}

function isVisualStyleGenerationMessage(message: string) {
  const text = (message || "").trim();
  return text === VISUAL_STYLE_PROPOSE_MESSAGE || text === VISUAL_STYLE_REGENERATE_MESSAGE;
}

function cleanVisualStyleContextText(value: any) {
  return stripHexCodes(value)
    .replace(/^(?:✅|⏳|❌|⚠️|⚠|🔄|👉|\s)+/gu, "")
    .replace(/\s+/g, " ")
    .trim();
}

function buildVisualStyleGenerationContext(
  history: { role: string; content: string }[],
  triggerMessage: string,
  crossStageContext = ""
) {
  const lines: string[] = [];
  for (const item of history.slice(-14)) {
    const content = cleanVisualStyleContextText(item.content);
    if (!content || isVisualStyleGenerationMessage(content)) continue;

    const role = item.role === "user" ? "用户" : item.role === "system" ? "操作记录" : "";
    if (role === "用户") {
      lines.push(`用户：${content}`);
      continue;
    }
    if (role === "操作记录") {
      lines.push(`操作记录：${content}`);
    }
  }

  const trigger = cleanVisualStyleContextText(triggerMessage);
  if (trigger && !isVisualStyleGenerationMessage(trigger)) {
    lines.push(`当前要求：${trigger}`);
  }

  const crossStage = cleanVisualStyleContextText(crossStageContext);
  if (crossStage) {
    lines.unshift(crossStage);
  }

  return lines.join("\n").slice(-4000);
}

function proposalColorValue(color: any) {
  const raw = typeof color === "string" ? color : color?.hex;
  const match = String(raw || "").match(/#(?:[0-9a-fA-F]{3}){1,2}\b/);
  return match?.[0] || "#d1d5db";
}

function proposalColorLabel(color: any, index: number) {
  if (typeof color === "string") {
    const text = stripHexCodes(color);
    return text && text !== color ? text : `颜色 ${index + 1}`;
  }
  const name = stripHexCodes(color?.name);
  const role = stripHexCodes(color?.role);
  const parts = [name, role].filter(Boolean);
  return Array.from(new Set(parts)).join(" · ") || `颜色 ${index + 1}`;
}

function visualStrategyText(style: any) {
  const strategy = style?.visual_strategy;
  if (!strategy || typeof strategy !== "object") return "";
  return stripHexCodes(strategy.summary || strategy.background_policy || strategy.content_treatment || "");
}

function proposalDecisionField(style: any, key: "decision_label" | "best_for" | "tradeoff" | "visual_focus") {
  return stripHexCodes(style?.[key] || "");
}

function proposalChoiceLabel(style: any, index: number) {
  const decisionLabel = proposalDecisionField(style, "decision_label");
  if (decisionLabel) return `${index + 1}. ${decisionLabel}`;
  return index === 0 ? "推荐" : `方案 ${index + 1}`;
}

function normalizeStylePalette(palette: any[] | undefined) {
  return (palette || []).map((c: any) => {
    if (!c) return { name: "未知", hex: "#CCCCCC", role: "" };
    if (typeof c === "string") return { name: stripHexCodes(c) || c, hex: proposalColorValue(c), role: "" };
    return {
      name: stripHexCodes(c.name) || "颜色",
      hex: proposalColorValue(c),
      role: stripHexCodes(c.role) || "",
    };
  });
}

function buildFallbackStyleAdjustment(baseStyle: any, userFeedback: string, agentResponse: string): StyleProposal {
  const feedback = [userFeedback, agentResponse].filter(Boolean).join(" ");
  const compactFeedback = feedback.replace(/\s+/g, "");
  const wantsLight = /(白色为主|以白色为主|白底|浅底|浅色|米白|暖白|明亮|亮一点|明亮一点|不喜欢黑紫|不要黑紫|不用黑紫|避免黑紫|不是黑紫|舍弃黑紫|不是那种很深邃)/i.test(compactFeedback)
    && !/(不要浅色|不用浅色|避免浅色|不要浅底|不要白底|不用白底|不要明亮)/i.test(compactFeedback);
  const wantsDarkTech = !wantsLight && /(科技|未来|赛博|深色|暗色|黑|酷|炫|低幼|幼|emoji|可爱)/i.test(feedback);
  const basePalette = normalizeStylePalette(baseStyle?.palette);
  const palette = wantsDarkTech
    ? [
        { name: "深空黑", hex: "#050816", role: "背景色" },
        { name: "电紫", hex: "#8B5CF6", role: "主视觉光效" },
        { name: "霓虹蓝", hex: "#38BDF8", role: "数据与线条点缀" },
        { name: "冷白", hex: "#F8FAFC", role: "文字与 Logo 留白" },
      ]
    : wantsLight
    ? [
        { name: "米白", hex: "#F9F8F5", role: "整套页面主背景/内容页浅色基底" },
        { name: "柔紫", hex: "#C4B4E0", role: "标题、页眉、编号和品牌装饰" },
        { name: "淡紫", hex: "#E8E0F0", role: "内容区、卡片和浅紫层次" },
        { name: "玫瑰粉", hex: "#E8C8D8", role: "温暖点缀/装饰线/标签" },
      ]
    : basePalette.length >= 4
    ? basePalette.slice(0, 4)
    : [
        ...basePalette,
        { name: "深灰", hex: "#111827", role: "背景/标题" },
        { name: "亮蓝", hex: "#3B82F6", role: "点缀" },
        { name: "浅灰", hex: "#E5E7EB", role: "辅助信息" },
        { name: "白色", hex: "#FFFFFF", role: "正文" },
      ].slice(0, 4);
  const name = wantsDarkTech
    ? "调整后方案：深色科技感"
    : wantsLight
    ? "调整后方案：明亮浅紫"
    : `调整后方案：${baseStyle?.name || "自定义风格"}`;
  const description = stripHexCodes(agentResponse || userFeedback)
    || "根据你的反馈调整现有风格。确认后会重新生成每页画面描述和生图 Prompt。";
  return {
    ...(baseStyle || {}),
    name,
    palette,
    mood: wantsDarkTech ? "科技感, 克制, 深色, 专业" : wantsLight ? "明亮, 温柔, 精致, 高可读" : (baseStyle?.mood || "调整后, 克制, 专业"),
    font: wantsDarkTech
      ? "几何无衬线体，标题加粗，正文保持高可读"
      : wantsLight
      ? "延续现有字体气质，正文保持清晰高可读"
      : (baseStyle?.font || "清晰无衬线体，标题加粗，正文高可读"),
    description: `${description} 确认后会把这套方向应用到整份 PPT，并重新生成页面画面描述。`,
    source: "agent_adjustment",
  };
}

function logoOverlayPosition(anchor?: string | null, resolvedBox?: any) {
  if (
    resolvedBox &&
    Number.isFinite(Number(resolvedBox.left)) &&
    Number.isFinite(Number(resolvedBox.top)) &&
    Number.isFinite(Number(resolvedBox.width))
  ) {
    const height = Number.isFinite(Number(resolvedBox.height)) ? Number(resolvedBox.height) : undefined;
    return {
      left: `${Number(resolvedBox.left) * 100}%`,
      top: `${Number(resolvedBox.top) * 100}%`,
      width: `${Number(resolvedBox.width) * 100}%`,
      ...(height ? { height: `${height * 100}%` } : {}),
    } as CSSProperties;
  }
  const normalized = (anchor || "top-right").replace("_", "-");
  if (normalized === "center") {
    return { top: "50%", left: "50%", transform: "translate(-50%, -50%)" } as CSSProperties;
  }
  if (normalized === "lower-center") {
    return { top: "68%", left: "50%", transform: "translate(-50%, 0)" } as CSSProperties;
  }
  if (normalized === "title-block-center") {
    return { top: "70%", left: "68%", transform: "translate(-50%, 0)" } as CSSProperties;
  }
  const pos: CSSProperties = { top: "2.8%", right: "2.8%" };
  if (normalized.includes("bottom")) {
    delete pos.top;
    pos.bottom = "2.8%";
  }
  if (normalized.includes("left")) {
    delete pos.right;
    pos.left = "2.8%";
  }
  return pos;
}

function logoOverlaySrc(item: any, variant?: string) {
  if (variant === "symbol" && item?.symbol_overlay_url) return item.symbol_overlay_url;
  return item?.overlay_url || item?.url || "";
}

const OVERLAY_PRESET_BOXES: Record<string, { left: string; top: string; width: string; height: string }> = {
  "top-right-small": { left: "72%", top: "8%", width: "20%", height: "18%" },
  "bottom-right-small": { left: "72%", top: "72%", width: "20%", height: "18%" },
  "left-card": { left: "6.5%", top: "18%", width: "36%", height: "58%" },
  "right-card": { left: "59.5%", top: "18%", width: "34%", height: "58%" },
  "center-card": { left: "28%", top: "20%", width: "44%", height: "56%" },
  "bottom-band": { left: "12%", top: "68%", width: "76%", height: "22%" },
};

function enabledOverlayLayers(slide: any) {
  const layers = slide?.visual_json?.overlay_layers;
  return Array.isArray(layers) ? layers.filter((layer: any) => layer && layer.enabled !== false) : [];
}

function SlideImageWithOverlays({
  slide,
  src,
  logo,
  referenceImages,
  alt,
  className,
  imgClassName,
  onClick,
  onError,
}: {
  slide: any;
  src: string;
  logo?: any;
  referenceImages?: any[];
  alt: string;
  className?: string;
  imgClassName?: string;
  onClick?: (e: ReactMouseEvent<HTMLDivElement>) => void;
  onError?: (e: SyntheticEvent<HTMLImageElement>) => void;
}) {
  const derivedLogos = (referenceImages || []).filter(isConfirmedLogoRef);
  const logoItems = Array.from(
    new Map(
      [...derivedLogos, ...(logo ? [logo] : [])]
        .filter(Boolean)
        .map((item: any) => [String(item.id || item.url), item])
    ).values()
  );
  const showLogo = logoItems.length > 0 && shouldShowLogoOverlay(slide);
  const slideType = String(slide?.visual_json?.type || slide?.type || "content").toLowerCase();
  const policy = slide?.visual_json?.logo_policy || {};
  const resolvedLogoBox = policy.resolved_overlay_box;
  const largeLogo = policy.scale === "large" || slideType === "cover" || slideType === "ending";
  const logoWidth = largeLogo
    ? logoItems.length > 1 ? "clamp(120px, 26%, 340px)" : "clamp(80px, 18%, 240px)"
    : logoItems.length > 1 ? "clamp(64px, 12%, 160px)" : "clamp(28px, 5.2%, 84px)";
  const overlays = enabledOverlayLayers(slide);
  const overlayReferenceImages = [
    ...(referenceImages || []),
    ...((slide?.reference_images || []) as any[]),
  ];
  const assetById = new Map(overlayReferenceImages.map((ref: any) => [String(ref.id), ref]));
  return (
    <div className={`relative ${className || ""}`} onClick={onClick}>
      <img src={src} alt={alt} className={imgClassName || "w-full h-full object-cover"} onError={onError} />
      {overlays.map((layer: any, index: number) => {
        const asset = assetById.get(String(layer.asset_id));
        if (!asset?.url) return null;
        const box = OVERLAY_PRESET_BOXES[layer.preset] || OVERLAY_PRESET_BOXES["right-card"];
        return (
          <div
            key={layer.id || `${layer.asset_id}-${index}`}
            className={`pg-slide-exact-overlay ${layer.mode === "exact_cutout" ? "pg-slide-exact-cutout" : "pg-slide-exact-card"}`}
            style={{ ...box, zIndex: 8 + index }}
          >
            <img
              src={`${API_BASE}${asset.url}`}
              alt=""
              className="pointer-events-none select-none"
            />
          </div>
        );
      })}
      {showLogo && (
        <div
          className="absolute z-10 pointer-events-none select-none flex items-center justify-center gap-[7%]"
          style={{
            ...logoOverlayPosition(policy.placement || logoItems[0]?.logo_anchor, resolvedLogoBox),
            ...(!resolvedLogoBox ? { width: logoWidth } : {}),
            maxHeight: resolvedLogoBox ? undefined : largeLogo ? "136px" : "48px",
          }}
        >
          {logoItems.map((item: any, index: number) => (
            <Fragment key={item.id || item.url || index}>
              {index > 0 && <span className="h-[1.8em] w-px bg-slate-500/45" />}
              <img
                src={`${API_BASE}${logoOverlaySrc(item, policy.render_variant)}`}
                alt=""
                className="min-w-0 flex-1 object-contain"
                style={{ maxHeight: resolvedLogoBox ? "100%" : largeLogo ? "136px" : "48px" }}
              />
            </Fragment>
          ))}
        </div>
      )}
    </div>
  );
}

function SlideReadinessIcons({
  hasVisual,
  hasPrompt,
}: {
  hasVisual: boolean;
  hasPrompt: boolean;
}) {
  const iconBase = "w-5 h-5 rounded border flex items-center justify-center transition-colors";
  const visualClass = hasVisual
    ? "bg-emerald-50 border-emerald-200 text-emerald-600"
    : "bg-slate-50 border-slate-200 text-slate-300";
  const promptClass = hasPrompt
    ? "bg-blue-50 border-blue-200 text-blue-600"
    : "bg-slate-50 border-slate-200 text-slate-300";
  return (
    <div className="flex items-center gap-1" aria-label="页面生成状态">
      <span className={`${iconBase} ${visualClass}`} title={hasVisual ? "画面描述已生成" : "画面描述未生成"}>
        <svg viewBox="0 0 24 24" className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 12s3.2-6 9-6 9 6 9 6-3.2 6-9 6-9-6-9-6Z" />
          <circle cx="12" cy="12" r="2.5" />
        </svg>
      </span>
      <span className={`${iconBase} ${promptClass}`} title={hasPrompt ? "生图 Prompt 已生成" : "生图 Prompt 未生成"}>
        <svg viewBox="0 0 24 24" className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M8 9 4 12l4 3" />
          <path d="m16 9 4 3-4 3" />
          <path d="m14 5-4 14" />
        </svg>
      </span>
    </div>
  );
}

function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const selectedProjectIdRef = useRef<string | null>(null);
  const clearingActiveNotificationRef = useRef<Set<string>>(new Set());
  const [slides, setSlides] = useState<Slide[]>([]);
  const [slidesProjectId, setSlidesProjectId] = useState<string | null>(null);
  const [slidesLoadingProjectId, setSlidesLoadingProjectId] = useState<string | null>(null);
  const slidesCacheRef = useRef<Record<string, Slide[]>>({});
  const [imageRefreshMap, setImageRefreshMap] = useState<Record<string, number>>({});
  const [slidesHistory, setSlidesHistory] = useState<Slide[][]>([]);
  const [slidesHistoryIndex, setSlidesHistoryIndex] = useState(-1);
  const isGlobalUndoingRef = useRef(false);
  const [operatingProjectId, setOperatingProjectId] = useState<string | null>(null);
  const gateContextRef = useRef<GateContext | null>(null);
  const {
    workflowStatus: projectStatus,
    setWorkflowStatus: setProjectStatus,
    refreshWorkflowStatus,
    activeRun,
    hasActiveRun,
  } = useProjectWorkflow(selectedProject?.id || null);
  const currentProjectStatus = projectStatus?.project_id === selectedProject?.id ? projectStatus : null;
  const [gateRevisionMap, setGateRevisionMap] = useState<Record<string, number>>({});
  const gateRevision = selectedProject ? gateRevisionMap[selectedProject.id] || 0 : 0;

  // 追踪当前活跃的聊天流属于哪个项目/角色，防止状态跳到别的窗口
  const activeChatProjectIdRef = useRef<string | null>(null);
  const activeChatRoleRef = useRef<string | null>(null);
  const activeChatGateRef = useRef<string | null>(null);
  const activeChatGateRevisionRef = useRef<number | null>(null);

  // 保存最近一次聊天的请求参数，用于切回来后自动恢复
  const pendingChatRef = useRef<PendingChatRequest | null>(null);
  const chatInProgressRef = useRef(false);
  const lastChatEventAtRef = useRef(0);

  // 单页编辑状态
  const [editingSlide, setEditingSlide] = useState<Slide | null>(null);
  const editingSlideRef = useRef(editingSlide);
  useEffect(() => {
    editingSlideRef.current = editingSlide;
  }, [editingSlide]);

  // Agent 模式：page（单页） / global（全局）
  const [agentMode, setAgentMode] = useState<"page" | "global">("global");

  // 新建项目弹窗
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newTitle, setNewTitle] = useState("");


  const isBusy = operatingProjectId === selectedProject?.id || hasActiveRun;
  const [selectedPages, setSelectedPages] = useState<Set<number>>(new Set());
  const [prototypeSelectionTouched, setPrototypeSelectionTouched] = useState(false);
  const [showPrototypePreview, setShowPrototypePreview] = useState(true);
  const [referenceImages, setReferenceImages] = useState<any[]>([]);
  const [templatePages, setTemplatePages] = useState<any[]>([]);
  const [showTemplateRecommender, setShowTemplateRecommender] = useState(false);

  // 主舞台折叠状态：默认折叠以节省空间
  const [styleBarExpanded, setStyleBarExpanded] = useState(false);
  const [assetsBarExpanded, setAssetsBarExpanded] = useState(false);
  const assetsGuidanceExpandedProjectRef = useRef<string | null>(null);

  const [chatInput, setChatInput] = useState("");
  const chatInputValueRef = useRef("");
  const activeComposerDraftKeyRef = useRef<string | null>(null);
  const suspendComposerDraftPersistRef = useRef(false);
  const saveActiveComposerDraft = () => {
    const key = activeComposerDraftKeyRef.current;
    if (key) writeComposerDraft(key, chatInputValueRef.current);
  };
  const [chatLoading, setChatLoading] = useState(false);
  const [thinkingContent, setThinkingContent] = useState("");
  const [thinkingExpanded, setThinkingExpanded] = useState(false);
  const [galleryModal, setGalleryModal] = useState<{
    urls: string[];
    index: number;
    title?: string;
    slides?: any[];
    logo?: any;
  } | null>(null);

  // 页面待处理标记：
  // content: 文字/参考图等上游信息变了 → 需更新画面方案（画面描述 + 提示词）
  // visual: 画面描述变了 → 需更新提示词
  // image: 画面方案已更新 → 需确认并重新生成图片
  const [staleMap, setStaleMap] = useState<Record<string, { content?: boolean; visual?: boolean; image?: boolean }>>({});

  const getSlideStaleFlags = (slide: Slide) => {
    const stale = slide.visual_json?._artifact?.stale;
    if (!stale || typeof stale !== "object") return {};
    return {
      content: Boolean(stale.content),
      visual: Boolean(stale.visual),
      image: Boolean(stale.image),
    };
  };

  const hydrateSlideStaleMap = (items: Slide[]) => {
    setStaleMap((prev) => {
      const next = { ...prev };
      items.forEach((slide) => {
        const backendStale = getSlideStaleFlags(slide);
        if (backendStale.content || backendStale.visual || backendStale.image) {
          next[slide.id] = { ...next[slide.id], ...backendStale };
        }
      });
      return next;
    });
  };

  const markSlideStale = (slideId: string, type: "content" | "visual" | "image") => {
    setStaleMap((prev) => ({
      ...prev,
      [slideId]: { ...prev[slideId], [type]: true },
    }));
  };

  const clearTransientProjectState = (nextProjectId?: string) => {
    saveActiveComposerDraft();
    activeComposerDraftKeyRef.current = null;
    suspendComposerDraftPersistRef.current = chatInputValueRef.current !== "";
    const cachedSlides = nextProjectId ? slidesCacheRef.current[nextProjectId] : undefined;
    setProjectStatus(null);
    setContentPlanProgress(null);
    setOperatingProjectId(null);
    generationLoadingIdRef.current = null;
    setReferenceImages([]);
    setTemplatePages([]);
    if (nextProjectId && cachedSlides) {
      setSlides(cachedSlides);
      setSlidesProjectId(nextProjectId);
    } else {
      setSlides([]);
      setSlidesProjectId(null);
    }
    setSlidesLoadingProjectId(null);
    setStaleMap({});
    if (nextProjectId && cachedSlides) hydrateSlideStaleMap(cachedSlides);
    setSelectedPages(new Set());
    setPrototypeSelectionTouched(false);
    setEditingSlide(null);
    setAgentMode("global");
    setContentPlanSnapshot([]);
    setStyleProposalsInChat([]);
    setExpandedStyleProposalKey(null);
    setChatInput("");
    setPendingAttachments([]);
    setPendingChatAttachments([]);
    setPendingFinetuneAttachmentsMap({});
    setFinetuneChatHistoryMap({});
  };
  const clearSlideStale = (slideId: string, type?: "content" | "visual" | "image") => {
    setStaleMap((prev) => {
      if (!prev[slideId]) return prev;
      if (type) {
        const next = { ...prev[slideId] };
        delete next[type];
        return { ...prev, [slideId]: next };
      }
      const next = { ...prev };
      delete next[slideId];
      return next;
    });
  };
  const staleSlides = slides
    .map((s) => ({ slide: s, stale: staleMap[s.id] }))
    .filter((x) => x.stale && (x.stale.content || x.stale.visual || x.stale.image));
  const hasContentOrVisualStale = staleSlides.some((x) => x.stale.content || x.stale.visual);
  const imageStaleSlides = staleSlides.filter((x) => x.stale.image && !x.stale.content && !x.stale.visual);

  const [editingProjectId, setEditingProjectId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [documents, setDocuments] = useState<any[]>([]);
  const [pendingAttachments, setPendingAttachments] = useState<string[]>([]);
  const [pendingChatAttachments, setPendingChatAttachments] = useState<ChatAttachment[]>([]);
  const [pendingFinetuneAttachmentsMap, setPendingFinetuneAttachmentsMap] = useState<Record<string, ChatAttachment[]>>({});
  const [uploadingDoc, setUploadingDoc] = useState(false);
  const [, setUploadingStyleRef] = useState(false);
  const [, setUploadingLogo] = useState(false);
  const [, setUploadingVisualAsset] = useState(false);
  const [, setUploadingTemplate] = useState(false);
  const [editingMessageIndex, setEditingMessageIndex] = useState<number | null>(null);
  const [editMessageContent, setEditMessageContent] = useState("");
  const [isDragging, setIsDragging] = useState(false);
  const [documentsExpanded, setDocumentsExpanded] = useState(false);
  const [dragSlideId, setDragSlideId] = useState<string | null>(null);
  const [dragOverSlideId, setDragOverSlideId] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const silentChatAbortRef = useRef(false);
  const isConfirmingRef = useRef(false);
  const contentPlanPollTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const contentPlanProgressIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const contentPlanCheckIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const visualPromptIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const loadedChatProjectIdRef = useRef<string | null>(null);
  const contentPlanStopTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const contentPlanStartingProjectRef = useRef<string | null>(null);
  const contentPlanStartingAtRef = useRef(0);
  const loadingProjectIdRef = useRef<string | null>(null);
  const missingProjectHandledRef = useRef<Set<string>>(new Set());
  const softLockWarnedRef = useRef(false);
  const generationLoadingIdRef = useRef<string | null>(null);
  const locallyHandledRunIdsRef = useRef<Set<string>>(new Set());
  const [contentPlanProgress, setContentPlanProgress] = useState<any>(null);
  const currentContentPlanProgress = contentPlanProgress?.project_id === selectedProject?.id ? contentPlanProgress : null;
  const [, setShowStylePanel] = useState(false);
  const [currentAgentRole, setCurrentAgentRole] = useState<AgentRole>("content");
  const currentAgentRoleRef = useRef(currentAgentRole);
  // 三 Agent 聊天历史隔离（必须在 currentAgentRole 之后定义）
  const [contentChatHistory, setContentChatHistory] = useState<ChatMessage[]>([]);
  const [visualChatHistory, setVisualChatHistory] = useState<ChatMessage[]>([]);
  const [chatHistoryProjectId, setChatHistoryProjectId] = useState<string | null>(null);
  const chatHistoryProjectIdRef = useRef<string | null>(null);
  const setPendingChatRequest = (pending: PendingChatRequest) => {
    pendingChatRef.current = pending;
    writePendingChat(pending);
  };
  const clearPendingChatRequest = (projectId?: string | null) => {
    const pendingProjectId = projectId || pendingChatRef.current?.projectId || selectedProjectIdRef.current;
    pendingChatRef.current = null;
    if (pendingProjectId) clearStoredPendingChat(pendingProjectId);
  };
  const restoreStoredPendingChatForProject = (projectId: string) => {
    const pending = readPendingChat(projectId);
    if (!pending) return null;
    pendingChatRef.current = pending;
    if (currentAgentRoleRef.current !== pending.agentRole) {
      currentAgentRoleRef.current = pending.agentRole;
      setCurrentAgentRole(pending.agentRole);
    }
    return pending;
  };
  // 单页微调：按 slideId 隔离的聊天历史
  const [finetuneChatHistoryMap, setFinetuneChatHistoryMap] = useState<Record<string, ChatMessage[]>>({});
  // 单页微调：当前选中的目标页
  const [finetuneTargetSlideId, setFinetuneTargetSlideId] = useState<string | null>(null);
  // 单页微调：各页的历史版本数据 { slideId: Version[] }
  const [slideVersionsMap, setSlideVersionsMap] = useState<Record<string, any[]>>({});
  // 计算当前活跃的聊天历史
  const roleChatMessages = currentAgentRole === "content"
    ? contentChatHistory
    : currentAgentRole === "visual"
    ? visualChatHistory
    : (finetuneTargetSlideId ? (finetuneChatHistoryMap[finetuneTargetSlideId] || []) : []);
  const chatMessages =
    selectedProject && chatHistoryProjectId === selectedProject.id
      ? normalizeProjectChatHistory(selectedProject.id, roleChatMessages)
      : [];
  const getChatStorageKey = (projectId: string, role: AgentRole, slideId?: string | null) =>
    role === "finetune"
      ? `ppt_god_chat_finetune_${projectId}_${slideId || "unselected"}`
      : `ppt_god_chat_${role}_${projectId}`;
  const readStoredChatMessages = (projectId: string, role: AgentRole, slideId?: string | null) => {
    try {
      const raw = localStorage.getItem(getChatStorageKey(projectId, role, slideId));
      return raw ? normalizeProjectChatHistory(projectId, JSON.parse(raw), { allowLegacy: true }) : [];
    } catch {
      return [];
    }
  };
  const writeStoredChatMessages = (
    projectId: string,
    role: AgentRole,
    messages: ChatMessage[],
    slideId?: string | null
  ) => {
    const key = getChatStorageKey(projectId, role, slideId);
    const normalized = normalizeProjectChatHistory(projectId, messages, { allowLegacy: true });
    try {
      if (normalized.length > 0) {
        localStorage.setItem(key, JSON.stringify(normalized));
      } else {
        localStorage.removeItem(key);
      }
    } catch (err) {
      console.warn("Persist chat messages failed:", err);
    }
    return normalized;
  };
  const updateStoredChatMessages = (
    projectId: string,
    role: AgentRole,
    updater: (messages: ChatMessage[]) => ChatMessage[],
    slideId?: string | null
  ) => {
    const current = readStoredChatMessages(projectId, role, slideId);
    const next = updater(current);
    return writeStoredChatMessages(projectId, role, applyGateMetaToNewMessages(current, next), slideId);
  };
  const appendStoredChatMessage = (
    projectId: string,
    role: AgentRole,
    message: ChatMessage,
    slideId?: string | null
  ) => updateStoredChatMessages(projectId, role, (current) => [...current, message], slideId);
  const updateRoleChatMessages = (
    projectId: string,
    role: AgentRole,
    updater: (messages: ChatMessage[]) => ChatMessage[],
    slideId?: string | null
  ) => {
    const nextStored = updateStoredChatMessages(projectId, role, updater, slideId);
    if (selectedProjectIdRef.current === projectId && chatHistoryProjectIdRef.current === projectId) {
      if (role === "content") {
        setContentChatHistory(nextStored);
      } else if (role === "visual") {
        setVisualChatHistory(nextStored);
      } else if (slideId) {
        setFinetuneChatHistoryMap((prev) => ({ ...prev, [slideId]: nextStored }));
      }
    }
    return nextStored;
  };
  // 设置当前 Agent 的聊天历史。所有写入先同步落盘，再同步 React state。
  const setActiveChatMessages = (updater: React.SetStateAction<ChatMessage[]>) => {
    const projectId = selectedProjectIdRef.current;
    const role = currentAgentRoleRef.current;
    const slideId = role === "finetune" ? finetuneTargetSlideId : null;
    if (!projectId || chatHistoryProjectIdRef.current !== projectId) return;
    if (role === "finetune" && !slideId) return;
    if (
      chatInProgressRef.current &&
      ((activeChatProjectIdRef.current && activeChatProjectIdRef.current !== projectId) ||
        (activeChatRoleRef.current && activeChatRoleRef.current !== role) ||
        (activeChatGateRef.current &&
          gateContextRef.current &&
          (activeChatGateRef.current !== gateContextRef.current.gate ||
            activeChatGateRevisionRef.current !== gateContextRef.current.gateRevision)))
    ) {
      return;
    }
    updateRoleChatMessages(
      projectId,
      role,
      (current) => (typeof updater === "function" ? (updater as (messages: ChatMessage[]) => ChatMessage[])(current) : updater),
      slideId
    );
  };
  const updateFinetuneChatMessages = (
    slideId: string,
    updater: (messages: ChatMessage[]) => ChatMessage[]
  ) => {
    const projectId = selectedProjectIdRef.current;
    if (!projectId) return;
    updateRoleChatMessages(projectId, "finetune", updater, slideId);
  };
  const appendProjectChatMessage = (projectId: string, role: "content" | "visual", message: ChatMessage) => {
    const normalized = withGateMeta({ ...message, agentRole: role, projectId });
    const nextStored = appendStoredChatMessage(projectId, role, normalized);
    if (selectedProjectIdRef.current === projectId && chatHistoryProjectIdRef.current === projectId) {
      if (role === "content") {
        setContentChatHistory(nextStored);
      } else {
        setVisualChatHistory(nextStored);
      }
      return;
    }
  };
  const updateProjectChatMessages = (
    projectId: string,
    role: "content" | "visual",
    updater: (messages: ChatMessage[]) => ChatMessage[]
  ) => {
    const nextStored = updateStoredChatMessages(projectId, role, updater);
    if (selectedProjectIdRef.current === projectId && chatHistoryProjectIdRef.current === projectId) {
      if (role === "content") {
        setContentChatHistory(nextStored);
      } else {
        setVisualChatHistory(nextStored);
      }
    }
  };
  const cleanStageContextText = (value: string) =>
    value
      .replace(/\[\[PPTGOD_ATTACHMENT:[^\]]+\]\]/g, "")
      .replace(/📎\s*[^\n]+/g, "")
      .replace(/\s+/g, " ")
      .replace(/^(已提交 Brief，开始生成内容规划|确认建议，开始生成内容规划)\s+/, "")
      .trim();
  const isVisualRelevantStageContext = (value: string, role?: ChatMessage["role"]) => {
    const text = cleanStageContextText(value);
    if (!text) return false;
    if (/用户(?:在第\s*\d+\s*页[前后]插入了新页面|删除了第\s*\d+\s*页|调整了页面顺序)/.test(text)) return false;
    if (/用户(?:确认了内容规划|回退到|重试了|确认打样效果|更新了\s*\d+\s*页的画面方案)/.test(text)) return false;
    if (/^内容规划已生成|^正在|^已启动后台|^风格提案/.test(text)) return false;
    if (role === "user") return true;
    if (role === "system") {
      return /(上传了(?:品牌 Logo|风格参考|可复用素材|版式模板)|Brief Studio 上传|Agent 窗口上传|选择了风格|Logo|素材|风格|版式|参考图|图片|截图|文档|文件|PDF|Markdown|原样出现)/.test(text);
    }
    return false;
  };
  const getVisualSystemMessageContent = (value: string) => {
    const text = cleanStageContextText(value);
    if (!text) return "";
    if (/^【.+用户补充要求】/.test(text)) {
      const [header, ...lines] = text.split("\n");
      const relevantLines = lines.filter((line) =>
        isVisualRelevantStageContext(line.replace(/^-\s*/, ""), "system")
      );
      return relevantLines.length ? [header, ...relevantLines].join("\n") : "";
    }
    return isVisualRelevantStageContext(text, "system") ? text : "";
  };
  const summarizeStageMessages = (messages: ChatMessage[], stageLabel: string) => {
    const ignored = /^(已提交 Brief|确认建议，开始生成内容规划|正在启动内容规划生成|用户确认了内容规划|用户回退到)/;
    const lines = messages
      .filter((m) => !m.loading && (m.role === "user" || m.role === "system"))
      .map((m) => ({ text: cleanStageContextText(m.content || ""), role: m.role }))
      .filter((item) => item.text && !ignored.test(item.text) && isVisualRelevantStageContext(item.text, item.role))
      .slice(-4)
      .map(({ text }) => `- ${text.length > 180 ? `${text.slice(0, 180)}...` : text}`);
    return lines.length ? `【${stageLabel}用户补充要求】\n${lines.join("\n")}` : "";
  };
  const buildCrossStageContext = (targetRole: "content" | "visual" | "finetune") => {
    if (targetRole === "visual") {
      return summarizeStageMessages(contentChatHistory, "内容阶段");
    }
    if (targetRole === "content") {
      return summarizeStageMessages(visualChatHistory, "视觉阶段");
    }
    return "";
  };
  const withCrossStageContext = (pageContext: any, targetRole: "content" | "visual" | "finetune") => {
    const crossStageContext = buildCrossStageContext(targetRole);
    if (!crossStageContext) return pageContext;
    return { ...(pageContext || {}), cross_stage_context: crossStageContext };
  };
  // 如果视觉总监聊天记录为空，自动添加开场引导语
  const ensureVisualGreetingIfNeeded = () => {
    const projectId = selectedProjectIdRef.current;
    if (projectId && chatHistoryProjectIdRef.current === projectId && visualChatHistory.length === 0) {
      const hasAssets = referenceImages.length > 0;
      const logoAssets = referenceImages.filter(isConfirmedLogoRef);
      const assetDesc = [
        logoAssets.length ? `${logoAssets.length}个品牌 Logo` : "",
        referenceImages.filter((r) => r.role === "visual_asset").length > 0 ? `${referenceImages.filter((r) => r.role === "visual_asset").length}个可复用素材` : "",
        referenceImages.filter((r) => r.role === "style_ref").length > 0 ? `${referenceImages.filter((r) => r.role === "style_ref").length}张风格参考` : "",
        referenceImages.find((r) => r.role === "template") ? "版式模板" : "",
      ].filter(Boolean).join("、");
      const handoffNote = buildCrossStageContext("visual")
        ? "\n\n我也会把内容阶段你提过的补充要求带入后续视觉方案和画面 Prompt。"
        : "";
      const directorMsg = hasAssets
        ? `我是视觉总监。已收到你上传的设计素材（${assetDesc}）。${handoffNote}\n\n👉 如果你还想补充素材，请继续上传；如果已经齐了，点击「生成视觉方向」，我会基于这些素材制定风格方案。`
        : `我是视觉总监。生成视觉方向前，先确认是否要补充素材：品牌 Logo、可复用素材（产品/主视觉/人物/物料图）、风格参考、版式模板。${handoffNote}\n\n👉 这些都可以在上方「项目素材」上传；没有素材也可以直接点击「生成视觉方向」。`;
      appendProjectChatMessage(projectId, "visual", { role: "agent", content: directorMsg, agentRole: "visual" });
    }
  };
  const isLegacyContentGreeting = (message: ChatMessage) => (
    message.role === "agent" &&
    message.agentRole === "content" &&
    (
      message.content.includes("你好！我是你的内容总监") ||
      message.content.includes("请告诉我你想做什么主题的 PPT")
    )
  );

  const stripLegacyContentGreetings = (messages: ChatMessage[]) =>
    messages.filter((message) => !isLegacyContentGreeting(message));

  // 如果内容总监聊天记录为空，按当前阶段补充状态，而不是显示 Brief Studio 之前的 onboarding。
  const ensureContentGreetingIfNeeded = () => {
    const projectId = selectedProjectIdRef.current;
    if (!projectId || chatHistoryProjectIdRef.current !== projectId) return;
    const cleaned = stripLegacyContentGreetings(contentChatHistory);
    if (cleaned.length !== contentChatHistory.length) {
      const normalized = writeStoredChatMessages(projectId, "content", cleaned);
      setContentChatHistory(normalized);
    }
    if (cleaned.length === 0 && slides.length > 0) {
      appendProjectChatMessage(projectId, "content", { role: "agent", content: "内容规划已生成。你可以直接指出要改的页、顺序或文字。", agentRole: "content" });
    }
  };
  // 为指定 slideId 的微调聊天添加开场引导（仅首次）
  const ensureFinetuneGreetingForSlide = (slideId: string) => {
    const projectId = selectedProjectIdRef.current;
    setFinetuneChatHistoryMap((prev) => {
      if (prev[slideId] && prev[slideId].length > 0) return prev;
      const stored = projectId ? readStoredChatMessages(projectId, "finetune", slideId) : [];
      if (stored.length > 0) {
        return { ...prev, [slideId]: stored };
      }
      const greeting = [{ role: "agent" as const, content: "已选中此页。直接写修改要求即可，我会把当前页图片和参考图一起发给模型生成新版本。", agentRole: "finetune" as const }];
      if (projectId) writeStoredChatMessages(projectId, "finetune", greeting, slideId);
      return {
        ...prev,
        [slideId]: greeting,
      };
    });
  };
  const [contentPlanConfirmed, setContentPlanConfirmed] = useState(false);
  const [contentPlanSnapshot, setContentPlanSnapshot] = useState<Slide[]>([]);
  const [confirmingProjectId, setConfirmingProjectId] = useState<string | null>(null);
  const contentPlanSnapshotRef = useRef(contentPlanSnapshot);
  const contentPlanConfirmedRef = useRef(contentPlanConfirmed);
  const [styleProposalsInChat, setStyleProposalsInChat] = useState<StyleProposal[]>([]);
  const [expandedStyleProposalKey, setExpandedStyleProposalKey] = useState<string | null>(null);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [confirmModal, setConfirmModal] = useState<{ message: string; onConfirm: () => void; onCancel: () => void } | null>(null);

  const bumpSlideImageRefresh = (slideId: string) => {
    setImageRefreshMap((prev) => ({ ...prev, [slideId]: Date.now() }));
  };
  const docInputRef = useRef<HTMLInputElement>(null);
  const logoInputRef = useRef<HTMLInputElement>(null);
  const styleRefInputRef = useRef<HTMLInputElement>(null);
  const visualAssetInputRef = useRef<HTMLInputElement>(null);
  const templateInputRef = useRef<HTMLInputElement>(null);
  const chatContainerRef = useRef<HTMLDivElement>(null);
  const chatAutoScrollRef = useRef(true);
  const chatInputRef = useRef<HTMLTextAreaElement>(null);
  const briefEditorRef = useRef<HTMLDivElement>(null);
  const briefEditorValueRef = useRef("");
  const briefDraggedChipRef = useRef<HTMLElement | null>(null);

  const isChatNearBottom = (element: HTMLDivElement) =>
    element.scrollHeight - element.scrollTop - element.clientHeight < 80;

  const scrollChatToBottom = () => {
    const element = chatContainerRef.current;
    if (!element) return;
    element.scrollTop = element.scrollHeight;
  };

  const handleChatScroll = () => {
    const element = chatContainerRef.current;
    if (!element) return;
    chatAutoScrollRef.current = isChatNearBottom(element);
  };

  // Toast 系统
  const showToast = useCallback((message: string, type: ToastItem["type"] = "info") => {
    const id = Math.random().toString(36).slice(2);
    const duration = type === "error" ? 5000 : type === "success" ? 2200 : 2600;
    setToasts((prev) => {
      const withoutDuplicate = prev.filter((toast) => !(toast.message === message && toast.type === type));
      return [...withoutDuplicate, { id, message, type, duration }].slice(-3);
    });
  }, []);
  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const isProjectNotFoundError = (err: any) => {
    return String(err?.message || err || "").includes("HTTP 404") &&
      String(err?.message || err || "").includes("Project not found");
  };

  const recoverMissingProject = async (projectId: string) => {
    if (missingProjectHandledRef.current.has(projectId)) return;
    missingProjectHandledRef.current.add(projectId);
    try {
      const freshProjects = await fetchProjects();
      const normalizedProjects = normalizeProjectsForActiveSelection(freshProjects, selectedProjectIdRef.current);
      setProjects(normalizedProjects);
      if (selectedProjectIdRef.current !== projectId) return;
      clearTransientProjectState();
      const fallback = normalizedProjects[0] ? clearProjectNotification(normalizedProjects[0]) : null;
      if (fallback) {
        selectedProjectIdRef.current = fallback.id;
        setSelectedProject(fallback);
        localStorage.setItem("ppt_god_last_project_id", fallback.id);
        showToast("当前项目暂时无法打开，已切换到最近的可用项目。", "info");
      } else {
        selectedProjectIdRef.current = null;
        setSelectedProject(null);
        localStorage.removeItem("ppt_god_last_project_id");
        showToast("当前项目暂时无法打开，请新建项目。", "info");
      }
    } catch (refreshErr: any) {
      showToast("项目暂时无法打开，刷新项目列表失败：" + (refreshErr.message || "网络错误"), "error");
    }
  };

  // Confirm 模态框
  const showConfirm = (message: string): Promise<boolean> => {
    return new Promise((resolve) => {
      setConfirmModal({
        message,
        onConfirm: () => {
          resolve(true);
          setConfirmModal(null);
        },
        onCancel: () => {
          resolve(false);
          setConfirmModal(null);
        },
      });
    });
  };

  // textarea 自动增高，最多约 5 行
  const autoResizeTextarea = () => {
    const el = chatInputRef.current;
    if (!el) return;
    el.style.height = "auto";
    const maxHeight = 240; // 约 10 行
    if (el.scrollHeight > maxHeight) {
      el.style.height = `${maxHeight}px`;
      el.style.overflowY = "auto";
    } else {
      el.style.height = `${el.scrollHeight}px`;
      el.style.overflowY = "hidden";
    }
  };

  useEffect(() => {
    autoResizeTextarea();
  }, [chatInput]);

  // 保持 ref 与 state 同步，供 loadSlides 等闭包函数读取最新值
  useEffect(() => {
    currentAgentRoleRef.current = currentAgentRole;
  }, [currentAgentRole]);

  useEffect(() => {
    chatHistoryProjectIdRef.current = chatHistoryProjectId;
  }, [chatHistoryProjectId]);

  useEffect(() => {
    contentPlanSnapshotRef.current = contentPlanSnapshot;
  }, [contentPlanSnapshot]);

  useEffect(() => {
    contentPlanConfirmedRef.current = contentPlanConfirmed;
  }, [contentPlanConfirmed]);

  // 离开微调模式时清空目标页
  useEffect(() => {
    if (currentAgentRole !== "finetune") {
      setFinetuneTargetSlideId(null);
    }
  }, [currentAgentRole]);

  // 在详情页内翻页时，同步微调目标页
  useEffect(() => {
    if (currentAgentRole === "finetune" && editingSlide) {
      setFinetuneTargetSlideId(editingSlide.id);
      ensureFinetuneGreetingForSlide(editingSlide.id);
      loadSlideVersions(editingSlide.id);
    }
  }, [editingSlide?.id, currentAgentRole]);

  // 列宽调节与折叠
  const [leftWidth, setLeftWidth] = useState(256);
  const [rightWidth, setRightWidth] = useState(320);
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [rightCollapsed, setRightCollapsed] = useState(false);
  const isResizing = useRef<"left" | "right" | null>(null);
  const resizeStartX = useRef(0);
  const resizeStartWidth = useRef(0);

  const startResize = (side: "left" | "right", e: React.MouseEvent) => {
    isResizing.current = side;
    resizeStartX.current = e.clientX;
    resizeStartWidth.current = side === "left" ? leftWidth : rightWidth;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  };

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!isResizing.current) return;
      const dx = e.clientX - resizeStartX.current;
      if (isResizing.current === "left") {
        setLeftWidth(Math.max(180, Math.min(400, resizeStartWidth.current + dx)));
      } else {
        setRightWidth(Math.max(300, Math.min(460, resizeStartWidth.current - dx)));
      }
    };
    const onUp = () => {
      isResizing.current = null;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  const abortActiveChat = (silent = true) => {
    if (abortRef.current) {
      silentChatAbortRef.current = silent;
      abortRef.current.abort();
      abortRef.current = null;
    }
    activeChatProjectIdRef.current = null;
    activeChatRoleRef.current = null;
    activeChatGateRef.current = null;
    activeChatGateRevisionRef.current = null;
    chatInProgressRef.current = false;
    clearPendingChatRequest();
    setChatLoading(false);
    setThinkingContent("");
    setThinkingExpanded(false);
  };

  const getProjectEntryAgentRole = (project: Project, notificationMessage?: string | null): "content" | "visual" => {
    const message = notificationMessage || "";
    if (message.includes("内容规划")) return "content";
    if (project.status === "draft") return "content";
    if (project.status === "planning" && !project.content_plan_confirmed && !project.selected_style) return "content";
    return "visual";
  };

  const focusProjectLatestUpdate = (project: Project, notificationMessage?: string | null) => {
    setRightCollapsed(false);
    setEditingSlide(null);
    setShowTemplateRecommender(false);
    setShowPrototypePreview(true);
    setCurrentAgentRole(getProjectEntryAgentRole(project, notificationMessage));
    chatAutoScrollRef.current = true;
    requestAnimationFrame(scrollChatToBottom);
  };

  const markActiveProjectNotificationRead = (projectId: string) => {
    if (clearingActiveNotificationRef.current.has(projectId)) return;
    clearingActiveNotificationRef.current.add(projectId);
    fetchProject(projectId)
      .then((fresh) => {
        const normalizedFresh = clearProjectNotification(fresh);
        setProjects((prev) =>
          normalizeProjectsForActiveSelection(
            prev.map((item) => (item.id === projectId ? normalizedFresh : item)),
            selectedProjectIdRef.current
          )
        );
        if (selectedProjectIdRef.current === projectId) {
          setSelectedProject(normalizedFresh);
        }
      })
      .catch((err) => {
        console.warn("Failed to clear active project notification:", err);
      })
      .finally(() => {
        clearingActiveNotificationRef.current.delete(projectId);
      });
  };

  const loadProjects = async () => {
    try {
      const data = await fetchProjects();
      const currentSelectedId = selectedProjectIdRef.current;
      const activeProjectFromServer = currentSelectedId
        ? data.find((p: Project) => p.id === currentSelectedId)
        : null;
      const activeProjectHadUnread = Boolean(activeProjectFromServer?.has_unread_notification);
      const normalizedData = normalizeProjectsForActiveSelection(data, currentSelectedId);
      setProjects(normalizedData);
      if (currentSelectedId) {
        const updated = normalizedData.find((p: Project) => p.id === currentSelectedId);
        if (updated) {
          missingProjectHandledRef.current.delete(currentSelectedId);
          setSelectedProject((prev) => {
            if (!prev || prev.id !== currentSelectedId) return prev;
            if (
              updated.status !== prev.status ||
              updated.title !== prev.title ||
              updated.content_plan_confirmed !== prev.content_plan_confirmed ||
              updated.completed_slides !== prev.completed_slides ||
              updated.has_unread_notification !== prev.has_unread_notification ||
              updated.unread_notification_message !== prev.unread_notification_message ||
              JSON.stringify(updated.selected_style) !== JSON.stringify(prev.selected_style) ||
              JSON.stringify(updated.style_proposal) !== JSON.stringify(prev.style_proposal)
            ) {
              return updated;
            }
            return prev;
          });
        }
        if (activeProjectHadUnread) {
          markActiveProjectNotificationRead(currentSelectedId);
        }
      }
      return normalizedData as Project[];
    } catch (err: any) {
      showToast("加载项目列表失败：" + (err.message || "网络错误"), "error");
      return [] as Project[];
    }
  };

  const selectProject = async (project: Project) => {
    if (editingProjectId === project.id) return;
    if (selectedProjectIdRef.current === project.id) {
      if (slidesProjectId !== project.id && slidesLoadingProjectId !== project.id) {
        loadingProjectIdRef.current = project.id;
        void loadSlides(project.id);
      }
      return;
    }
    const notificationMessage = project.has_unread_notification ? project.unread_notification_message || "" : null;
    abortActiveChat(true);
    isConfirmingRef.current = false;
    softLockWarnedRef.current = false;
    chatHistoryProjectIdRef.current = null;
    setChatHistoryProjectId(null);
    clearTransientProjectState(project.id);

    const optimisticProject = clearProjectNotification(project);
    selectedProjectIdRef.current = project.id;
    setProjects((prev) => prev.map((item) => (item.id === project.id ? optimisticProject : item)));
    setSelectedProject(optimisticProject);
    setShowPrototypePreview(true);
    setExpandedStyleProposalKey(null);

    const isPlanConfirmed = !!optimisticProject.content_plan_confirmed;
    setContentPlanConfirmed(isPlanConfirmed);
    if (notificationMessage) {
      focusProjectLatestUpdate(optimisticProject, notificationMessage);
    } else {
      setCurrentAgentRole(getProjectEntryAgentRole(optimisticProject));
    }

    try {
      const fresh = await fetchProject(project.id);
      const normalizedFresh = clearProjectNotification(fresh);
      missingProjectHandledRef.current.delete(project.id);
      setProjects((prev) => prev.map((item) => (item.id === project.id ? normalizedFresh : item)));
      if (selectedProjectIdRef.current === project.id) {
        setSelectedProject(normalizedFresh);
        if (notificationMessage) {
          focusProjectLatestUpdate(normalizedFresh, notificationMessage);
        }
      }
    } catch (err: any) {
      if (isProjectNotFoundError(err)) {
        void recoverMissingProject(project.id);
        return;
      }
      console.warn("Project detail load failed:", err);
    }
  };

  const loadSlides = async (projectId: string) => {
    if (selectedProjectIdRef.current === projectId) {
      setSlidesLoadingProjectId(projectId);
    }
    try {
      const data = await fetchSlides(projectId);
      if (loadingProjectIdRef.current !== projectId) return slidesCacheRef.current[projectId] || [];
      slidesCacheRef.current[projectId] = data;
      setSlidesProjectId(projectId);
      setSlides(data);
      hydrateSlideStaleMap(data);
      // 视觉阶段的内容变动只影响相关页面，不撤销整套流程。
      if (currentAgentRoleRef.current === "visual" && contentPlanSnapshotRef.current.length > 0 && contentPlanConfirmedRef.current) {
        const changedSlides = data.filter((s: Slide) => {
          const snap = contentPlanSnapshotRef.current.find((cs) => cs.page_num === s.page_num);
          if (!snap) return true;
          return JSON.stringify(snap.content_json || {}) !== JSON.stringify(s.content_json || {});
        });
        if (changedSlides.length > 0) {
          setStaleMap((prev) => {
            const next = { ...prev };
            changedSlides.forEach((changed: Slide) => {
              next[changed.id] = { ...next[changed.id], content: true };
            });
            return next;
          });
          setContentPlanSnapshot(data);
          if (!softLockWarnedRef.current) {
            softLockWarnedRef.current = true;
            updateProjectChatMessages(projectId, "visual", (prev) => [
              ...prev,
              {
                role: "agent",
                content: `检测到 ${changedSlides.length} 页内容变更，已标记为需要更新画面方案；已有图片会保留到你确认重新生成。`,
                agentRole: "visual",
              },
            ]);
          }
        }
      }
      return data;
    } catch (err: any) {
      if (isProjectNotFoundError(err)) {
        void recoverMissingProject(projectId);
        return [];
      }
      showToast("加载页面列表失败：" + (err.message || "网络错误"), "error");
      return [];
    } finally {
      if (selectedProjectIdRef.current === projectId) {
        setSlidesLoadingProjectId(null);
      }
    }
  };

  useEffect(() => {
    if (!selectedProject || slidesProjectId !== selectedProject.id || !editingSlide) return;
    if (slides.length === 0) {
      setEditingSlide(null);
      return;
    }
    const sameId = slides.find((s) => s.id === editingSlide.id);
    if (sameId) {
      if (sameId !== editingSlide) setEditingSlide(sameId);
      return;
    }
    const replacement =
      slides.find((s) => s.page_num === editingSlide.page_num) ||
      slides[Math.min(Math.max((editingSlide.page_num || 1) - 1, 0), slides.length - 1)];
    setEditingSlide(replacement || null);
  }, [selectedProject?.id, slidesProjectId, slides, editingSlide]);

  // 全局撤销/重做：保存 slides 快照
  const pushSlidesHistory = (currentSlides: Slide[]) => {
    if (isGlobalUndoingRef.current) return;
    setSlidesHistory((prev) => {
      const trimmed = prev.slice(0, slidesHistoryIndex + 1);
      const next = [...trimmed, JSON.parse(JSON.stringify(currentSlides))];
      if (next.length > 20) {
        next.shift();
        setSlidesHistoryIndex((idx) => idx - 1);
        return next;
      }
      return next;
    });
    setSlidesHistoryIndex((idx) => Math.min(idx + 1, 19));
  };

  const restoreSlidesToBackend = async (projectId: string, targetSlides: Slide[]) => {
    if (operatingProjectId === projectId) return;
    setOperatingProjectId(projectId);
    try {
      for (const slide of targetSlides) {
        await updateSlideContent(projectId, slide.page_num, slide.content_json, slide.id);
      }
      const nextSlides = JSON.parse(JSON.stringify(targetSlides));
      slidesCacheRef.current[projectId] = nextSlides;
      setSlidesProjectId(projectId);
      setSlides(nextSlides);
      showToast("已撤销到之前的状态", "success");
    } catch (err: any) {
      showToast("撤销保存失败：" + (err.message || "未知错误"), "error");
    } finally {
      setOperatingProjectId(null);
    }
  };

  const handleGlobalUndo = async () => {
    if (slidesHistoryIndex <= 0 || !selectedProject) return;
    const targetIndex = slidesHistoryIndex - 1;
    const targetSlides = slidesHistory[targetIndex];
    isGlobalUndoingRef.current = true;
    setSlidesHistoryIndex(targetIndex);
    await restoreSlidesToBackend(selectedProject.id, targetSlides);
    setTimeout(() => {
      isGlobalUndoingRef.current = false;
    }, 0);
  };

  const handleGlobalRedo = async () => {
    if (slidesHistoryIndex >= slidesHistory.length - 1 || !selectedProject) return;
    const targetIndex = slidesHistoryIndex + 1;
    const targetSlides = slidesHistory[targetIndex];
    isGlobalUndoingRef.current = true;
    setSlidesHistoryIndex(targetIndex);
    await restoreSlidesToBackend(selectedProject.id, targetSlides);
    setTimeout(() => {
      isGlobalUndoingRef.current = false;
    }, 0);
  };

  const canGlobalUndo = slidesHistoryIndex > 0;
  const canGlobalRedo = slidesHistoryIndex < slidesHistory.length - 1;

  const loadStatus = async (projectId: string) => {
    try {
      const data = selectedProjectIdRef.current === projectId
        ? await refreshWorkflowStatus()
        : await fetchWorkflowStatus(projectId);
      if (loadingProjectIdRef.current !== projectId) return;
      setProjectStatus(data?.project_id === projectId ? data : null);
    } catch (err: any) {
      if (isProjectNotFoundError(err)) {
        void recoverMissingProject(projectId);
        return;
      }
      showToast("加载项目状态失败：" + (err.message || "网络错误"), "error");
    }
  };

  const resolveWorkflowFailureMessage = async (
    projectId: string,
    runKind: string,
    fallbackMessage: string
  ): Promise<string> => {
    try {
      const workflow = await fetchWorkflowStatus(projectId);
      if (selectedProjectIdRef.current === projectId) {
        setProjectStatus(workflow?.project_id === projectId ? workflow : null);
      }
      const failedRun = [workflow?.last_run, workflow?.active_run].find(
        (run) =>
          run?.kind === runKind &&
          ["failed", "stale", "cancelled"].includes(String(run.status || ""))
      );
      const workflowMessage = failedRun?.message || failedRun?.error_msg;
      if (workflowMessage) return userFacingGenerationError(workflowMessage);
    } catch (statusErr) {
      console.warn("Workflow failure status lookup failed:", statusErr);
    }
    return fallbackMessage;
  };

  const loadReferenceImages = async (projectId: string) => {
    try {
      const data = await fetchReferenceImages(projectId);
      if (loadingProjectIdRef.current !== projectId) return;
      setReferenceImages(data || []);
    } catch (err: any) {
      if (isProjectNotFoundError(err)) {
        void recoverMissingProject(projectId);
        return;
      }
      showToast("加载参考素材失败：" + (err.message || "网络错误"), "error");
    }
  };

  const loadDocuments = async (projectId: string) => {
    try {
      const data = await fetchDocuments(projectId);
      if (loadingProjectIdRef.current !== projectId) return;
      setDocuments(data || []);
    } catch (err: any) {
      if (isProjectNotFoundError(err)) {
        void recoverMissingProject(projectId);
        return;
      }
      showToast("加载文档列表失败：" + (err.message || "网络错误"), "error");
    }
  };

  const loadTemplatePages = async (projectId: string) => {
    try {
      const data = await fetchTemplatePages(projectId);
      if (loadingProjectIdRef.current !== projectId) return;
      const pages = (data || []).map((ref: any, idx: number) => ({
        page_num: idx + 1,
        url: `${API_BASE}${ref.url}`,
        category: ref.category || "content",
      }));
      setTemplatePages(pages);
    } catch (err: any) {
      if (isProjectNotFoundError(err)) {
        void recoverMissingProject(projectId);
        return;
      }
      showToast("加载模板页面失败：" + (err.message || "未知错误"), "error");
      if (loadingProjectIdRef.current === projectId) setTemplatePages([]);
    }
  };

  // 启动内容规划生成并轮询进度（复用于 Brief Studio 和 Agent regenerate_plan）
  const startContentPlanPoll = async (
    projectId: string,
    topic: string,
    source: "button" | "agent" = "button",
    pageCount?: number,
    options?: {
      onStarted?: () => void;
      submittedLabel?: string;
      submittedContent?: string;
      submittedDisplayContent?: string;
      attachmentIds?: string[];
    }
  ): Promise<GateActionResult> => {
    const notifyStartBlocked = (message: string) => {
      if (source === "agent") {
        updateProjectChatMessages(projectId, "content", (prev) => [
          ...prev,
          {
            role: "agent",
            content: `没有启动重新生成：${message}`,
            agentRole: "content",
          },
        ]);
      } else {
        showToast(message, "info");
      }
    };
    const hasLocalStartLatch =
      operatingProjectId === projectId || contentPlanStartingProjectRef.current === projectId;
    if (hasLocalStartLatch) {
      let recoveredStaleLatch = false;
      const latchAge = Date.now() - (contentPlanStartingAtRef.current || 0);
      if (latchAge > CONTENT_PLAN_START_LATCH_GRACE_MS) {
        try {
          const workflow = await fetchWorkflowStatus(projectId);
          const activeContentPlan =
            workflow?.active_run?.kind === "content_plan" && isRunActive(workflow.active_run);
          if (!activeContentPlan) {
            contentPlanStartingProjectRef.current = null;
            contentPlanStartingAtRef.current = 0;
            if (operatingProjectId === projectId) setOperatingProjectId(null);
            recoveredStaleLatch = true;
          }
        } catch (err) {
          console.warn("Content plan start latch check failed:", err);
        }
      }
      if (!recoveredStaleLatch) {
        const message = "内容规划已经在处理中，请稍候。";
        notifyStartBlocked(message);
        return { ok: false, reason: "busy", message };
      }
    }
    contentPlanStartingProjectRef.current = projectId;
    contentPlanStartingAtRef.current = Date.now();
    abortActiveChat(true);
    const loadingId = `cp-${Date.now()}`;
    const submittedContent = options?.submittedContent || options?.submittedLabel || "已提交 Brief，开始生成内容规划";
    updateProjectChatMessages(projectId, "content", (prev) => [
      ...stripLegacyContentGreetings(prev),
      ...(source === "button"
        ? [{
            role: "user" as const,
            content: submittedContent,
            displayContent: getBriefSubmissionDisplayContent(submittedContent, options?.submittedDisplayContent),
          }]
        : []),
      { role: "agent" as const, content: "正在启动内容规划生成...", agentRole: "content", loading: true, id: loadingId },
    ]);
    setOperatingProjectId(projectId);

    const updateLoadingMsg = (content: string) => {
      updateProjectChatMessages(projectId, "content", (prev) => {
        const idx = prev.findIndex((m) => m.id === loadingId);
        if (idx >= 0) {
          const updated = [...prev];
          updated[idx] = { ...updated[idx], content };
          return updated;
        }
        return prev;
      });
    };
    const removeLoadingMsg = () => {
      updateProjectChatMessages(projectId, "content", (prev) => prev.filter((m) => m.id !== loadingId && !isTransientRunMessage(m)));
    };

    try {
      updateLoadingMsg("正在读取当前页面，准备生成内容规划...");
      // 记录旧 slides 的 ID，用于区分"旧内容还在"和"新生成完成"
      const previousSlides = await loadSlides(projectId);
      const previousSlideIds = previousSlides.map((s: any) => s.id).sort().join(",");
      updateLoadingMsg("正在向后台提交内容规划任务...");
      const result = await generateContentPlan(projectId, topic, pageCount, options?.attachmentIds);
      if (result?.run?.id) {
        updateLoadingMsg(runProgressText(result.run));
      }
      await loadStatus(projectId);
      const startedAt = Date.now();
      let progressInterval: ReturnType<typeof setInterval> | null = null;
      let checkInterval: ReturnType<typeof setInterval> | null = null;
      let pollCompleted = false;
      const cleanupContentPlanPoll = () => {
        if (progressInterval) clearInterval(progressInterval);
        if (checkInterval) clearInterval(checkInterval);
        progressInterval = null;
        checkInterval = null;
        contentPlanProgressIntervalRef.current = null;
        contentPlanCheckIntervalRef.current = null;
        if (selectedProjectIdRef.current === projectId) setContentPlanProgress(null);
        if (contentPlanStartingProjectRef.current === projectId) {
          contentPlanStartingProjectRef.current = null;
          contentPlanStartingAtRef.current = 0;
        }
        setOperatingProjectId(null);
        removeLoadingMsg();
      };
      const failContentPlanPoll = (message: string) => {
        pollCompleted = true;
        cleanupContentPlanPoll();
        updateProjectChatMessages(projectId, "content", (prev) => [
          ...prev,
          {
            role: "agent",
            content: "❌ 内容规划生成失败：" + message + "\n\n👉 请告诉我你的主题，我会重新为你生成。",
            agentRole: "content",
          },
        ]);
      };

      progressInterval = setInterval(async () => {
        if (pollCompleted) return;
        try {
          if (Date.now() - startedAt > CONTENT_PLAN_TIMEOUT_MS) {
            failContentPlanPoll("前端等待超时，但后台可能仍在运行。请稍后刷新页面查看结果，不要重复点击。");
            return;
          }
          const workflow = await fetchWorkflowStatus(projectId);
          if (pollCompleted) return;
          if (selectedProjectIdRef.current === projectId) {
            setProjectStatus(workflow?.project_id === projectId ? workflow : null);
          }
          const progress = workflow?.progress
            ? {
                ...workflow.progress,
                project_id: workflow.project_id,
                project_status: workflow.project_status,
                active_run: workflow.active_run,
              }
            : null;
          if (selectedProjectIdRef.current === projectId) {
            setContentPlanProgress(progress?.project_id === projectId ? progress : null);
          }
          if (progress?.message) {
            const total = Number(progress.total ?? progress.total_pages ?? 0);
            const current = Number(progress.current ?? progress.current_page ?? 0);
            const progressText = total ? `：${current || 0} / ${total} ${progress.unit || "页"}完成` : "";
            updateLoadingMsg(`${cleanProgressMessage(progress.message)}${progressText}`);
          }
          if (
            workflow?.last_run?.kind === "content_plan" &&
            ["failed", "stale", "cancelled"].includes(String(workflow.last_run.status || "")) &&
            !isRunActive(workflow.active_run)
          ) {
            failContentPlanPoll(workflow.last_run.error_msg || workflow.last_run.message || "后台处理异常");
          }
        } catch (e) {
          console.warn("Content plan progress poll error:", e);
        }
      }, 1500);
      contentPlanProgressIntervalRef.current = progressInterval;

      checkInterval = setInterval(async () => {
        if (pollCompleted) return;
        try {
          if (Date.now() - startedAt > CONTENT_PLAN_TIMEOUT_MS) {
            failContentPlanPoll("前端等待超时，但后台可能仍在运行。请稍后刷新页面查看结果，不要重复点击。");
            return;
          }
          const currentSlides = await loadSlides(projectId);
          if (pollCompleted) return;
          await loadProjects();
          await loadStatus(projectId);
          if (pollCompleted) return;
          const currentSlideIds = currentSlides.map((s: any) => s.id).sort().join(",");
          // 必须有 slides，且 ID 集合与旧内容不同（说明是新生成的），才认为完成
          if (currentSlides.length > 0 && currentSlideIds !== previousSlideIds) {
            pollCompleted = true;
            cleanupContentPlanPoll();
            updateProjectChatMessages(projectId, "content", (prev) => [
              ...prev,
              {
                role: "agent",
                content:
                  "✅ 内容规划已生成完毕，共 " +
                  currentSlides.length +
                  " 页。\n\n👉 下一步：请检查左侧每一页的内容是否满意。如果有调整需求，直接告诉我；如果没问题，点击右侧面板的「确认内容，请视觉总监 →」按钮进入视觉设计阶段。",
                agentRole: "content",
                nextAction: { type: "switch_to_visual", label: "确认内容，请视觉总监" },
                gate: gateContext.gate,
                gateRevision: gateContext.gateRevision,
              },
            ]);
            options?.onStarted?.();
            setContentPlanSnapshot(currentSlides);
            const currentEditingSlide = editingSlideRef.current;
            if (selectedProjectIdRef.current === projectId && currentEditingSlide) {
              const freshEditingSlide =
                currentSlides.find((s: Slide) => s.id === currentEditingSlide.id) ||
                currentSlides.find((s: Slide) => s.page_num === currentEditingSlide.page_num) ||
                null;
              setEditingSlide(freshEditingSlide);
            }
          }
        } catch (e) {
          console.warn("Content plan check poll error:", e);
        }
      }, 2000);
      contentPlanCheckIntervalRef.current = checkInterval;
      return { ok: true, runId: result?.run?.id };
    } catch (err: any) {
      if (contentPlanStartingProjectRef.current === projectId) {
        contentPlanStartingProjectRef.current = null;
        contentPlanStartingAtRef.current = 0;
      }
      setOperatingProjectId(null);
      setContentPlanProgress(null);
      removeLoadingMsg();
      const message = "内容规划生成失败：" + (err.message || "未知错误");
      updateProjectChatMessages(projectId, "content", (prev) => [
        ...prev,
        {
          role: "agent",
          content: "❌ " + message + "\n\n👉 解决方法：\n1. 直接告诉我你的主题，我会重新为你生成\n2. 检查网络后刷新页面重试\n3. 也可以尝试缩减主题范围，或分多次生成",
          agentRole: "content",
        },
      ]);
      return { ok: false, reason: "failed", message };
    }
  };


  // 页面加载时从 localStorage 恢复上次选中的项目，Agent 角色由项目状态推断
  useEffect(() => {
    clearLegacyChatStorageIfNeeded();
    const savedProjectId = localStorage.getItem("ppt_god_last_project_id");
    loadProjects().then((loadedProjects) => {
      if (!savedProjectId) return;
      const target = loadedProjects.find((p) => p.id === savedProjectId);
      if (target) selectProject(target);
    });
  }, []);

  useEffect(() => {
    selectedProjectIdRef.current = selectedProject?.id || null;
  }, [selectedProject?.id]);

  useEffect(() => {
    if (!selectedProject) return;
    const confirmed = !!selectedProject.content_plan_confirmed;
    setContentPlanConfirmed(confirmed);
    if (!confirmed && selectedProject.status === "planning" && currentAgentRoleRef.current === "visual") {
      setCurrentAgentRole("content");
    }
  }, [selectedProject?.id, selectedProject?.content_plan_confirmed, selectedProject?.status]);

  useEffect(() => {
    const projectId = selectedProject?.id;
    if (
      !projectId ||
      currentAgentRole !== "visual" ||
      !contentPlanConfirmed ||
      selectedProject?.selected_style ||
      assetsGuidanceExpandedProjectRef.current === projectId
    ) {
      return;
    }
    assetsGuidanceExpandedProjectRef.current = projectId;
    setAssetsBarExpanded(true);
  }, [selectedProject?.id, selectedProject?.selected_style, currentAgentRole, contentPlanConfirmed]);

  useEffect(() => {
    if (selectedProject) {
      loadingProjectIdRef.current = selectedProject.id;
      setProjectStatus(null);
      setContentPlanProgress(null);
      setReferenceImages([]);
      setTemplatePages([]);
      const cachedSlides = slidesCacheRef.current[selectedProject.id];
      if (cachedSlides) {
        setSlides(cachedSlides);
        setSlidesProjectId(selectedProject.id);
      } else {
        setSlides([]);
        setSlidesProjectId(null);
      }
      setStaleMap({});
      if (cachedSlides) hydrateSlideStaleMap(cachedSlides);
      setStyleProposalsInChat([]);
      generationLoadingIdRef.current = null;
      loadSlides(selectedProject.id);
      loadStatus(selectedProject.id);
      loadReferenceImages(selectedProject.id);
      loadDocuments(selectedProject.id);
      loadTemplatePages(selectedProject.id);
      setSelectedPages(new Set());
      setPrototypeSelectionTouched(false);
      setThinkingContent("");
      setThinkingExpanded(false);
      setPendingAttachments([]);
      setPendingChatAttachments([]);

      if (loadedChatProjectIdRef.current !== selectedProject.id) {
        // 首次选中该项目（含页面重新加载后）：尝试从 localStorage 恢复聊天历史
        setContentChatHistory(readStoredChatMessages(selectedProject.id, "content"));
        setVisualChatHistory(readStoredChatMessages(selectedProject.id, "visual"));
        loadedChatProjectIdRef.current = selectedProject.id;
        chatHistoryProjectIdRef.current = selectedProject.id;
        setChatHistoryProjectId(selectedProject.id);
      }
    }
    // 切换项目时清理所有未完成的状态和轮询
    return () => {
      if (contentPlanPollTimeoutRef.current) {
        clearTimeout(contentPlanPollTimeoutRef.current);
        contentPlanPollTimeoutRef.current = null;
      }
      if (contentPlanProgressIntervalRef.current) {
        clearInterval(contentPlanProgressIntervalRef.current);
        contentPlanProgressIntervalRef.current = null;
      }
      if (contentPlanCheckIntervalRef.current) {
        clearInterval(contentPlanCheckIntervalRef.current);
        contentPlanCheckIntervalRef.current = null;
      }
      if (contentPlanStopTimeoutRef.current) {
        clearTimeout(contentPlanStopTimeoutRef.current);
        contentPlanStopTimeoutRef.current = null;
      }
      if (visualPromptIntervalRef.current) {
        clearInterval(visualPromptIntervalRef.current);
        visualPromptIntervalRef.current = null;
      }
      setChatLoading(false);
      setConfirmingProjectId(null);
      setContentPlanProgress(null);
    };
  }, [selectedProject?.id]);

  // 持久化选中项目到 localStorage
  useEffect(() => {
    if (selectedProject) {
      localStorage.setItem("ppt_god_last_project_id", selectedProject.id);
    } else {
      localStorage.removeItem("ppt_god_last_project_id");
    }
  }, [selectedProject?.id]);

  // 持久化聊天历史到 localStorage（按项目和 Agent 隔离）
  useEffect(() => {
    if (!selectedProject) return;
    if (chatHistoryProjectId !== selectedProject.id) return;
    const messages = normalizeProjectChatHistory(selectedProject.id, contentChatHistory);
    if (messages.length > 0) writeStoredChatMessages(selectedProject.id, "content", messages);
  }, [contentChatHistory, selectedProject?.id, chatHistoryProjectId]);
  useEffect(() => {
    if (!selectedProject) return;
    if (chatHistoryProjectId !== selectedProject.id) return;
    const messages = normalizeProjectChatHistory(selectedProject.id, visualChatHistory);
    if (messages.length > 0) writeStoredChatMessages(selectedProject.id, "visual", messages);
  }, [visualChatHistory, selectedProject?.id, chatHistoryProjectId]);

  // 素材变更检测：每次聊天的 system prompt 都会重新注入最新素材摘要，
  // 视觉总监能自动看到最新状态，因此不再需要单独的 system 提示打扰用户。
  // （旧实现的全文已移除，相关 ref/useEffect 一并删除。）

  // 全局快捷键：Ctrl+Z 撤销，Ctrl+Shift+Z / Ctrl+Y 重做（仅在非单页编辑模式下生效）
  // Gallery 预览：左右箭头切换，ESC 关闭
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (galleryModal) {
        if (e.key === "ArrowLeft") {
          e.preventDefault();
          setGalleryModal((prev) =>
            prev
              ? {
                  ...prev,
                  index:
                    prev.index > 0 ? prev.index - 1 : prev.urls.length - 1,
                }
              : prev
          );
          return;
        }
        if (e.key === "ArrowRight") {
          e.preventDefault();
          setGalleryModal((prev) =>
            prev
              ? {
                  ...prev,
                  index:
                    prev.index < prev.urls.length - 1
                      ? prev.index + 1
                      : 0,
                }
              : prev
          );
          return;
        }
        if (e.key === "Escape") {
          setGalleryModal(null);
          return;
        }
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
        if (editingSlide) return; // 单页编辑模式下让单页快捷键处理
        e.preventDefault();
        if (e.shiftKey) {
          handleGlobalRedo();
        } else {
          handleGlobalUndo();
        }
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "y") {
        if (editingSlide) return;
        e.preventDefault();
        handleGlobalRedo();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [handleGlobalUndo, handleGlobalRedo, editingSlide, galleryModal]);

  // 页面切回前台时自动刷新状态（解决切标签页后 SSE 断开导致的卡住）
  useEffect(() => {
    const recoverPendingChat = () => {
      const projectId = selectedProjectIdRef.current;
      const pending = pendingChatRef.current || (projectId ? restoreStoredPendingChatForProject(projectId) : null);
      if (!pending) return;
      if (selectedProjectIdRef.current !== pending.projectId) return;
      if (currentAgentRoleRef.current !== pending.agentRole) {
        currentAgentRoleRef.current = pending.agentRole;
        setCurrentAgentRole(pending.agentRole);
      }

      const lastEventAt = lastChatEventAtRef.current || 0;
      const streamLooksStale = !chatInProgressRef.current || !lastEventAt || Date.now() - lastEventAt > 45_000;
      if (chatInProgressRef.current && !streamLooksStale) return;
      if ((pending.retryCount || 0) >= 2) {
        appendProjectChatMessage(pending.projectId, pending.agentRole, {
          role: "agent",
          content: "请求中断，没有完成这次操作。请再发送一次，或刷新页面后重试。",
          agentRole: pending.agentRole,
        });
        clearPendingChatRequest(pending.projectId);
        setChatLoading(false);
        return;
      }

      if (abortRef.current) {
        silentChatAbortRef.current = true;
        abortRef.current.abort();
        abortRef.current = null;
      }
      chatInProgressRef.current = false;
      lastChatEventAtRef.current = Date.now();
      setChatLoading(true);
      window.setTimeout(() => {
        const latest = pendingChatRef.current;
        if (!latest) return;
        if (selectedProjectIdRef.current !== latest.projectId || currentAgentRoleRef.current !== latest.agentRole) return;
        handleSendChat(latest.message, latest.history as any, true);
      }, 300);
    };

    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        loadProjects();
        if (selectedProject) {
          loadSlides(selectedProject.id);
          loadStatus(selectedProject.id);
        }
        recoverPendingChat();
      }
    };
    const onFocus = () => recoverPendingChat();
    const initialRecoveryTimer = window.setTimeout(recoverPendingChat, 600);
    document.addEventListener("visibilitychange", onVisibilityChange);
    window.addEventListener("focus", onFocus);
    return () => {
      window.clearTimeout(initialRecoveryTimer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      window.removeEventListener("focus", onFocus);
    };
  }, [selectedProject?.id, currentAgentRole, chatLoading]);

  // 轮询运行中任务进度（由后端 active_run 驱动）
  useEffect(() => {
    if (!selectedProject) return;
    if (!hasActiveRun) return;

    let isFetching = false;
    const interval = setInterval(async () => {
      if (isFetching) return;
      isFetching = true;
      try {
        await loadSlides(selectedProject.id);
        await loadProjects();
      } finally {
        isFetching = false;
      }
    }, 3000);

    return () => clearInterval(interval);
  }, [selectedProject?.id, hasActiveRun]);

  // 检测运行中任务结束，清理 Agent loading 提示（按项目隔离，防止切换项目时误触发）
  const prevProjectStatusRef = useRef<{ projectId: string | null; status: string | null }>({ projectId: null, status: null });
  const prevActiveRunRef = useRef<{ projectId: string | null; runId: string | null; kind: string | null }>({ projectId: null, runId: null, kind: null });
  useEffect(() => {
    const pid = selectedProject?.id || null;
    const currentStatus = selectedProject?.status || null;
    const prev = prevProjectStatusRef.current;
    const prevRun = prevActiveRunRef.current;
    const activeRunId = activeRun?.id || null;
    if (prevRun.projectId === pid && prevRun.runId && !activeRunId) {
      if (locallyHandledRunIdsRef.current.has(prevRun.runId)) {
        locallyHandledRunIdsRef.current.delete(prevRun.runId);
        const loadingId = generationLoadingIdRef.current;
        generationLoadingIdRef.current = null;
        if (pid) {
          updateProjectChatMessages(pid, "visual", (prevMsgs) => prevMsgs.filter((m) => m.id !== loadingId && m.runId !== prevRun.runId));
        }
        prevProjectStatusRef.current = { projectId: pid, status: currentStatus };
        prevActiveRunRef.current = { projectId: pid, runId: activeRunId, kind: activeRun?.kind || null };
        return;
      }
      if (prevRun.kind === "visual_prompts" && visualPromptIntervalRef.current) {
        prevProjectStatusRef.current = { projectId: pid, status: currentStatus };
        prevActiveRunRef.current = { projectId: pid, runId: activeRunId, kind: activeRun?.kind || null };
        return;
      }
      const loadingId = generationLoadingIdRef.current;
      generationLoadingIdRef.current = null;
      if (pid) {
        const finishedRunKind = prevRun.kind;
        const finishedRunId = prevRun.runId;
        void (async () => {
          let refreshedProject = selectedProject;
          let refreshedSlides = slides;
          let refreshedWorkflow: any = null;
          try {
            const [freshProjects, freshSlides, freshWorkflow] = await Promise.all([
              loadProjects(),
              loadSlides(pid),
              fetchWorkflowStatus(pid),
            ]);
            const freshProject = freshProjects.find((p: Project) => p.id === pid);
            if (freshProject) refreshedProject = freshProject;
            if (freshSlides.length > 0) refreshedSlides = freshSlides;
            if (freshWorkflow?.project_id === pid) {
              refreshedWorkflow = freshWorkflow;
              setProjectStatus(freshWorkflow);
            }
          } catch (err) {
            console.warn("Failed to refresh project after run completion:", err);
          }
          if (selectedProjectIdRef.current !== pid) return;
          const latestRun = refreshedWorkflow?.last_run?.id === finishedRunId ? refreshedWorkflow.last_run : null;
          const followup = buildRunCompletionFollowup({
            runKind: finishedRunKind,
            runStatus: latestRun?.status,
            runError: latestRun?.error_msg || latestRun?.message,
            projectStatus: refreshedProject?.status || currentStatus,
            completedCount: refreshedSlides.filter((s) => s.status === "completed").length,
            targetCompletedCount: latestRun?.completed_count,
            failedCount: latestRun?.failed_count,
            targetCount: latestRun?.total_count,
            totalSlides: refreshedSlides.length,
            hasSelectedStyle: Boolean(refreshedProject?.selected_style),
            hasPrompt: refreshedSlides.some((s) => Boolean(s.prompt_text)),
            styleProposalCount: refreshedProject?.style_proposal?.proposals?.length || 0,
          });
          const latestGateContext = gateContextRef.current || gateContext;
          updateProjectChatMessages(pid, followup.agentRole, (prevMsgs) => [
            ...prevMsgs.filter((m) => m.id !== loadingId && m.runId !== finishedRunId),
            {
              role: "agent",
              content: followup.content,
              agentRole: followup.agentRole,
              nextAction: followup.nextAction,
              gate: followup.nextAction ? latestGateContext.gate : undefined,
              gateRevision: followup.nextAction ? latestGateContext.gateRevision : undefined,
            },
          ]);
        })();
      }
    }
    // 兼容旧项目状态：只有同一个项目从 generating 变为 completed 才触发
    if (prev.projectId === pid && prev.status === "generating" && currentStatus === "completed") {
      const completedCount = slides.filter((s) => s.status === "completed").length;
      const loadingId = generationLoadingIdRef.current;
      generationLoadingIdRef.current = null;
      if (pid) {
        updateProjectChatMessages(pid, "visual", (prevMsgs) => [
          ...prevMsgs.filter((m) => m.id !== loadingId),
          { role: "system", content: `批量生成完成，共 ${completedCount} 页` },
          {
            role: "agent",
            content: "🎉 全量生成已完成！所有页面的图片都已生成。\n\n👉 下一步：点击上方「下载 PPTX」按钮获取最终演示文稿。如果需要调整某页，可以选中后重新生成。",
            agentRole: "visual",
          },
        ]);
      }
    }
    // 生成失败时也清除 loading 并提示
    if (prev.projectId === pid && prev.status === "generating" && currentStatus === "failed") {
      const loadingId = generationLoadingIdRef.current;
      generationLoadingIdRef.current = null;
      if (pid) {
        updateProjectChatMessages(pid, "visual", (prevMsgs) => [
          ...prevMsgs.filter((m) => m.id !== loadingId),
          {
            role: "agent",
            content: "❌ 批量生成失败，部分页面可能未成功生成。请检查失败页面后重试，或告诉我具体问题。",
            agentRole: "visual",
          },
        ]);
      }
    }
    prevProjectStatusRef.current = { projectId: pid, status: currentStatus };
    prevActiveRunRef.current = { projectId: pid, runId: activeRunId, kind: activeRun?.kind || null };
  }, [selectedProject?.id, selectedProject?.status, activeRun?.id]);

  // 实时更新批量生成进度到 Agent 窗口
  useEffect(() => {
    if (!selectedProject || !hasActiveRun) return;
    if (!generationLoadingIdRef.current || !currentProjectStatus) return;

    const loadingId = generationLoadingIdRef.current;
    const progressText = workflowProgressText(currentProjectStatus);

    setActiveChatMessages((prev) => {
      const idx = prev.findIndex((m) => m.id === loadingId);
      if (idx >= 0) {
        const updated = [...prev];
        updated[idx] = {
          ...updated[idx],
          content: progressText,
          runId: activeRun?.id,
        };
        return updated;
      }
      return prev;
    });
  }, [currentProjectStatus?.progress, activeRun?.id, hasActiveRun]);

  useEffect(() => {
    if (!selectedProject || activeRun?.kind !== "content_plan" || !currentProjectStatus?.progress) return;
    setContentPlanProgress({
      ...currentProjectStatus.progress,
      project_id: selectedProject.id,
      project_status: currentProjectStatus.project_status,
      active_run: activeRun,
    });
  }, [selectedProject?.id, activeRun?.id, currentProjectStatus?.progress]);

  // 聊天自动滚动：用户停在底部附近时跟随；用户上滑阅读历史后不抢滚动。
  useEffect(() => {
    if (!chatAutoScrollRef.current) return;
    requestAnimationFrame(scrollChatToBottom);
  }, [chatMessages, chatLoading]);

  useEffect(() => {
    chatAutoScrollRef.current = true;
    requestAnimationFrame(scrollChatToBottom);
  }, [selectedProject?.id, currentAgentRole, finetuneTargetSlideId]);

  // Rebuild a truthful transient loading message from the backend run after refresh/project switch.
  useEffect(() => {
    if (!selectedProject || !hasActiveRun || !activeRun?.id) return;
    const runId = activeRun.id;
    const targetAgent = activeRun.kind === "content_plan" ? "content" : "visual";
    const loadingId = `run-${runId}`;
    const progressText = workflowProgressText(currentProjectStatus || { active_run: activeRun });
    if (activeRun.kind !== "content_plan") {
      generationLoadingIdRef.current = loadingId;
    }
    updateProjectChatMessages(selectedProject.id, targetAgent, (prev) => {
      const existing = prev.find((m) => m.runId === runId || m.id === loadingId);
      if (existing) {
        return prev.map((m) =>
          m.runId === runId || m.id === loadingId
            ? { ...m, id: loadingId, runId, loading: true, content: progressText, agentRole: targetAgent as any }
            : m
        );
      }
      return [
        ...prev,
        {
          role: "agent",
          content: progressText,
          agentRole: targetAgent as any,
          loading: true,
          id: loadingId,
          runId,
        },
      ];
    });
  }, [selectedProject?.id, activeRun?.id, currentProjectStatus?.progress, hasActiveRun]);

  const addSystemLog = (content: string, attachments?: ChatAttachment[]) => {
    const projectId = selectedProjectIdRef.current;
    if (!projectId) return;
    appendProjectChatMessage(projectId, "content", { role: "system", content, attachments });
    if (isVisualRelevantStageContext(content, "system")) {
      appendProjectChatMessage(projectId, "visual", { role: "system", content, attachments });
    }
  };

  const handleCreate = async () => {
    const title = newTitle.trim() || "未命名项目";
    try {
      const data = await createProject(title);
      if (data.detail) {
        showToast("创建失败：" + data.detail, "error");
        return;
      }
      setNewTitle("");
      setShowCreateModal(false);
      await loadProjects();
      // 自动选中新创建的项目，并重置为内容规划阶段
      if (data.id) {
        const fresh = normalizeProjectsForActiveSelection(await fetchProjects(), selectedProjectIdRef.current);
        const created = fresh.find((p: Project) => p.id === data.id);
        if (created) {
          const normalizedCreated = clearProjectNotification(created);
          selectedProjectIdRef.current = created.id;
          loadedChatProjectIdRef.current = created.id;
          chatHistoryProjectIdRef.current = created.id;
          setSelectedProject(normalizedCreated);
          setChatHistoryProjectId(created.id);
          setShowPrototypePreview(true);
          setExpandedStyleProposalKey(null);
          setCurrentAgentRole("content");
          setContentPlanConfirmed(false);
          setVisualChatHistory([]);
          const initialContentChat = writeStoredChatMessages(created.id, "content", [
            { role: "system", content: `用户创建了项目「${title}」` },
          ]);
          setContentChatHistory(initialContentChat);
        }
      }
    } catch (err: any) {
      showToast("创建项目出错：" + (err.message || "未知错误"), "error");
    }
  };

  const handleSelectStyle = async (style: any) => {
    if (!selectedProject) return;
    const projectId = selectedProject.id;
    try {
      await updateProjectStyle(projectId, style);
      await loadProjects();
      const fresh = normalizeProjectsForActiveSelection(await fetchProjects(), selectedProjectIdRef.current);
      const updated = fresh.find((p: Project) => p.id === projectId);
      if (updated && selectedProjectIdRef.current === projectId) setSelectedProject(clearProjectNotification(updated));
      if (selectedProjectIdRef.current === projectId) {
        setShowStylePanel(false);
        setStyleProposalsInChat([]); // 清除Agent面板内的提案
        setExpandedStyleProposalKey(null);
      }
      appendProjectChatMessage(projectId, "content", { role: "system", content: `用户选择了风格「${style.name || "未命名"}」` });
      appendProjectChatMessage(projectId, "visual", { role: "system", content: `用户选择了风格「${style.name || "未命名"}」` });
      // 自动进入生图方案生成，无需用户再点一次
      if (selectedProjectIdRef.current === projectId) {
        await handleGeneratePrompts(false, style.name);
      }
    } catch (err: any) {
      showToast("保存风格失败：" + (err.message || "未知错误"), "error");
      updateProjectChatMessages(projectId, "visual", (prev) => [
        ...prev,
        {
          role: "agent",
          content: "❌ 保存风格失败：" + (err.message || "未知错误") + "\n\n👉 请检查网络后，在作品画布重新选择风格并点击「确认风格，生成生图方案」。",
          agentRole: "visual",
        },
      ]);
    }
  };

  const handleStartEdit = (project: Project) => {
    setEditingProjectId(project.id);
    setEditTitle(project.title);
  };

  const handleSaveEdit = async (projectId: string) => {
    if (!editTitle.trim()) {
      showToast("项目标题不能为空", "error");
      return;
    }
    try {
      await updateProject(projectId, { title: editTitle.trim() });
      setEditingProjectId(null);
      setEditTitle("");
      await loadProjects();
    } catch (err: any) {
      showToast("保存失败：" + (err.message || "未知错误"), "error");
    }
  };

  const handleDeleteProject = async (projectId: string) => {
    const ok = await showConfirm("确定要删除这个项目吗？此操作不可恢复。");
    if (!ok) return;
    try {
      await deleteProject(projectId);
      if (selectedProject?.id === projectId) {
        setSelectedProject(null);
        setSlides([]);
        setSlidesProjectId(null);
        setSlidesLoadingProjectId(null);
        setProjectStatus(null);
      }
      delete slidesCacheRef.current[projectId];
      await loadProjects();
    } catch (err: any) {
      showToast("删除失败：" + (err.message || "未知错误"), "error");
    }
  };

  const handleGeneratePrompts = async (prototype = false, styleName?: string) => {
    if (!selectedProject) return;
    if (operatingProjectId === selectedProject.id) return;
    const projectId = selectedProject.id;
    setOperatingProjectId(projectId);
    // 记录用户确认动作到聊天记录
    const name = styleName || selectedProject.selected_style?.name || selectedProject.style_id || "默认";
    const pendingPptAssets = documents.filter((doc: any) =>
      ["queued", "running"].includes(String(doc.asset_extraction_status || ""))
    );
    const pendingAssetsNote = pendingPptAssets.length
      ? `\n\n提示：${pendingPptAssets.length} 个 PPT 的图片素材还在后台解析。本轮会先使用已入库素材；解析完成后可重新生成画面方案补齐页面参考图。`
      : "";
    updateProjectChatMessages(projectId, "visual", (prev) => [
      ...prev,
      {
        role: "agent" as const,
        content: `✅ 风格「${name}」已确认，正在生成每页画面描述和生图 Prompt。\n\n完成后会进入「打样确认」阶段。${pendingAssetsNote}`,
        agentRole: "visual",
      },
    ]);

    // 插入真实进度 loading 消息（使用唯一ID确保稳定更新）
    const loadingId = `vp-${Date.now()}`;
    updateProjectChatMessages(projectId, "visual", (prev) => [
      ...prev,
      { role: "agent", content: "🚀 已启动后台生成，正在运行...", agentRole: "visual", loading: true, id: loadingId },
    ]);

    let visualPromptRunId: string | null = null;
    try {
      const pageNums = prototype && selectedPages.size > 0 ? Array.from(selectedPages) : undefined;
      // 触发后台任务（不再依赖 SSE 长连接）
      const startResult = await generateVisualPrompts(projectId, pageNums, buildCrossStageContext("visual"));
      const targetPageSet = pageNums?.length ? new Set(pageNums) : null;
      const getTargetSlides = (items: Slide[]) => targetPageSet ? items.filter((s) => targetPageSet.has(s.page_num)) : items;
      const hasSavedVisualPromptResult = (items: Slide[]) => {
        const targetItems = getTargetSlides(items);
        if (targetItems.length === 0) return false;
        return targetItems.every((s) => {
          const visualDescription = s.visual_json?.visual_description;
          return Boolean(
            visualDescription &&
            String(visualDescription).trim() &&
            s.prompt_text &&
            String(s.prompt_text).trim()
          );
        });
      };
      if (startResult?.run?.id) {
        visualPromptRunId = String(startResult.run.id);
        locallyHandledRunIdsRef.current.add(visualPromptRunId);
        updateProjectChatMessages(projectId, "visual", (prev) =>
          prev.map((m) => (m.id === loadingId ? { ...m, runId: startResult.run.id, content: runProgressText(startResult.run) } : m))
        );
      }

      if (visualPromptIntervalRef.current) {
        clearInterval(visualPromptIntervalRef.current);
      }

      await new Promise<void>((resolve, reject) => {
        let attempts = 0;
        let pollErrors = 0;
        const maxAttempts = 400; // 33 页 Prompt 生成约需 8–15 分钟，预留 20 分钟

        const updateLoadingMsg = (content: string) => {
          updateProjectChatMessages(projectId, "visual", (prev) => {
            const idx = prev.findIndex((m) => m.id === loadingId);
            if (idx >= 0) {
              const updated = [...prev];
              updated[idx] = { ...updated[idx], content };
              return updated;
            }
            return prev;
          });
        };

        visualPromptIntervalRef.current = setInterval(async () => {
          attempts++;
          try {
            const projectData = await fetchWorkflowStatus(projectId);
            const progressData = projectData?.progress || null;
            pollErrors = 0;
            if (selectedProjectIdRef.current === projectId) {
              setProjectStatus(projectData?.project_id === projectId ? projectData : null);
            }
            const projectStage = projectData?.project_status;
            const generationStatus = isRunActive(projectData?.active_run) ? "running" : "idle";

            // 实时更新进度到 Agent 面板
            const totalPages = Number(progressData?.total ?? progressData?.total_pages ?? 0);
            const currentPage = Math.min(Number(progressData?.current ?? progressData?.current_page ?? 0), totalPages || Number(progressData?.current ?? progressData?.current_page ?? 0));
            const message = cleanProgressMessage(progressData?.message) || "后台生成中";
            if (totalPages > 0) {
              updateLoadingMsg(`${message}：${currentPage} / ${totalPages} ${progressData?.unit || "页"}完成`);
            } else if (message) {
              updateLoadingMsg(message);
            }

            if ((projectStage === "visual_ready" || projectStage === "prompt_ready") && generationStatus !== "running") {
              if (visualPromptIntervalRef.current) {
                clearInterval(visualPromptIntervalRef.current);
                visualPromptIntervalRef.current = null;
              }
              await loadSlides(projectId);
              await loadProjects();

              const freshSlides = await fetchSlides(projectId);
              slidesCacheRef.current[projectId] = freshSlides;
              if (selectedProjectIdRef.current === projectId) {
                setSlidesProjectId(projectId);
                setSlides(freshSlides);
                hydrateSlideStaleMap(freshSlides);
              }
              if (!hasSavedVisualPromptResult(freshSlides)) {
                reject(new Error("后台任务已结束，但目标页面缺少画面描述或生图 Prompt。请重试生成生图方案。"));
                return;
              }

              resolve();
              return;
            }

            // 后台任务已结束但状态未就绪 → 任务异常中断
            if (generationStatus === "idle" && projectStage !== "visual_ready" && projectStage !== "prompt_ready") {
              if (visualPromptIntervalRef.current) {
                clearInterval(visualPromptIntervalRef.current);
                visualPromptIntervalRef.current = null;
              }
              const freshSlides = await fetchSlides(projectId);
              slidesCacheRef.current[projectId] = freshSlides;
              if (selectedProjectIdRef.current === projectId) {
                setSlidesProjectId(projectId);
                setSlides(freshSlides);
                hydrateSlideStaleMap(freshSlides);
              }
              if (hasSavedVisualPromptResult(freshSlides)) {
                await loadProjects();
                resolve();
                return;
              }
              reject(new Error("后台任务已结束，但目标页面缺少画面描述或生图 Prompt。请重试生成生图方案。"));
              return;
            }

            if (attempts >= maxAttempts) {
              if (visualPromptIntervalRef.current) {
                clearInterval(visualPromptIntervalRef.current);
                visualPromptIntervalRef.current = null;
              }
              reject(new Error("前端等待超时，但后台可能仍在运行。请稍后刷新页面查看结果，不要重复点击。"));
            }
          } catch (e) {
            console.warn("Visual prompt poll error:", e);
            pollErrors++;
            if (pollErrors >= VISUAL_PROMPT_MAX_POLL_ERRORS) {
              if (visualPromptIntervalRef.current) {
                clearInterval(visualPromptIntervalRef.current);
                visualPromptIntervalRef.current = null;
              }
              reject(new Error("连续无法获取生图方案进度，请检查后端服务后重试"));
            }
          }
        }, 3000);
      });

      showToast("生图方案生成完成", "success");
      updateProjectChatMessages(projectId, "visual", (prev) => [
        ...prev.filter((m) => m.id !== loadingId),
        {
          role: "agent",
          content:
            "✅ 画面设计已完成：每页画面描述和生图 Prompt 已生成。\n\n👉 下一步：先「打样确认」生成关键种子页预览；满意后再「全量生成」所有页面。也可以直接全量生成。",
          agentRole: "visual",
          nextAction: { type: "start_prototype", label: "打样确认", confirm: true },
        },
      ]);
    } catch (err: any) {
      showToast("生成生图方案失败：" + (err.message || "未知错误"), "error");
      const message = err.message || "未知错误";
      const isMissingResult = message.includes("目标页面缺少");
      if (visualPromptRunId && !isMissingResult) {
        locallyHandledRunIdsRef.current.delete(visualPromptRunId);
      }
      updateProjectChatMessages(projectId, "visual", (prev) => [
        ...prev.filter((m) => m.id !== loadingId),
        {
          role: "agent",
          content: isMissingResult
            ? `❌ 生图方案未完整生成：${message}\n\n👉 解决方法：点击「确认风格，生成生图方案」重新补齐缺失页面；已生成好的页面会继续保留。`
            : "❌ 生图方案生成失败：" + message + "\n\n👉 解决方法：\n1. 检查网络连接后，点击上方「确认风格，生成生图方案」按钮重试\n2. 如果多次失败，可以回到「视觉方案」重新选择风格\n3. 也可以直接告诉我具体问题，我来帮你调整",
          agentRole: "visual",
        },
      ]);
    } finally {
      if (visualPromptIntervalRef.current) {
        clearInterval(visualPromptIntervalRef.current);
        visualPromptIntervalRef.current = null;
      }
      setOperatingProjectId(null);
    }
  };

  const handleStartGeneration = async (useSelectedPages = false, prototype = false, explicitPageNums?: number[]) => {
    if (!selectedProject) return;
    if (operatingProjectId === selectedProject.id) return;
    const projectId = selectedProject.id;
    setOperatingProjectId(projectId);
    const modeText = prototype ? "打样" : "全量生成";
    const pageNums = explicitPageNums?.length
      ? explicitPageNums
      : useSelectedPages && selectedPages.size > 0
      ? Array.from(selectedPages)
      : undefined;
    const pageDesc = pageNums ? `第 ${pageNums.join(", ")} 页` : (prototype ? "默认种子页" : "所有页面");
    const loadingId = `gen-${Date.now()}`;
    generationLoadingIdRef.current = loadingId;
    updateProjectChatMessages(projectId, "visual", (prev) => [
      ...prev,
      {
        role: "agent",
        content: `🚀 已启动${modeText}（${pageDesc}），正在准备...`,
        agentRole: "visual",
        loading: true,
        id: loadingId,
      },
    ]);
    let generationRunId: string | null = null;
    try {
      const result = await startGeneration(projectId, pageNums, prototype);
      if (result?.run?.id) {
        generationRunId = String(result.run.id);
        locallyHandledRunIdsRef.current.add(generationRunId);
        updateProjectChatMessages(projectId, "visual", (prev) =>
          prev.map((m) => (m.id === loadingId ? { ...m, runId: result.run.id, content: runProgressText(result.run) } : m))
        );
      }
      const finalStatus = await pollUntilStatusNotGenerating(projectId);
      targetsClearForGeneration(pageNums);
      generationLoadingIdRef.current = null;
      const successMessage = finalStatus === "completed"
        ? "✅ 全量生成已完成，页面已自动刷新。\n\n👉 下一步：点击上方「下载 PPTX」获取最终文件；需要调整时可选中页面重新生成。"
        : finalStatus === "prototype_ready"
        ? "✅ 打样图片已生成，页面已自动刷新。\n\n👉 下一步：检查打样页效果；满意后点击「确认打样，生成全部」，不满意可以重新打样或调整风格。"
        : "";
      updateProjectChatMessages(projectId, "visual", (prev) => [
        ...prev.filter((m) => m.id !== loadingId),
        {
          role: "agent",
          content: finalStatus === "completed" || finalStatus === "prototype_ready"
            ? successMessage
            : `⚠️ 生成结束（状态：${finalStatus || "未知"}），页面已自动刷新，请检查是否有失败页。`,
          agentRole: "visual",
          nextAction: finalStatus === "completed"
            ? { type: "download", label: "下载 PPTX" }
            : finalStatus === "prototype_ready"
            ? { type: "confirm_prototype", label: "确认打样，生成全部", confirm: true }
            : undefined,
        },
      ]);
    } catch (err: any) {
      if (generationRunId) {
        locallyHandledRunIdsRef.current.delete(generationRunId);
      }
      showToast("启动生成失败：" + (err.message || "未知错误"), "error");
      generationLoadingIdRef.current = null;
      updateProjectChatMessages(projectId, "visual", (prev) => [
        ...prev.filter((m) => m.id !== loadingId),
        {
          role: "agent",
          content: "❌ 启动生成失败：" + (err.message || "未知错误") + "\n\n👉 解决方法：\n1. 检查网络连接\n2. 点击「打样确认」或「全量生成」按钮重试\n3. 如果多次失败，可以告诉我具体哪一页有问题",
          agentRole: "visual",
        },
      ]);
    } finally {
      setOperatingProjectId(null);
    }
  };

  const targetsClearForGeneration = (pageNums?: number[]) => {
    if (!pageNums || pageNums.length === 0) return;
    const pageSet = new Set(pageNums);
    slides.forEach((slide) => {
      if (pageSet.has(slide.page_num)) clearSlideStale(slide.id, "image");
    });
  };

  const handleStopGeneration = async () => {
    if (!selectedProject) return;
    const projectId = selectedProject.id;
    try {
      await stopGeneration(projectId);
      await loadStatus(projectId);
      await loadProjects();
      showToast("已停止生成", "info");
      const loadingId = generationLoadingIdRef.current;
      generationLoadingIdRef.current = null;
      updateProjectChatMessages(projectId, "visual", (prev) => [
        ...prev.filter((m) => m.id !== loadingId),
        {
          role: "agent",
          content: "⏹ 已停止生成。当前页面保留已生成的结果，未完成的页面可重新生成。",
          agentRole: "visual",
        },
      ]);
    } catch (err: any) {
      showToast("停止失败：" + (err.message || "未知错误"), "error");
    }
  };

  const handleConfirmPrototype = async () => {
    if (!selectedProject) return;
    if (operatingProjectId === selectedProject.id) return;
    const projectId = selectedProject.id;
    setOperatingProjectId(projectId);
    const loadingId = `gen-${Date.now()}`;
    generationLoadingIdRef.current = loadingId;
    updateProjectChatMessages(projectId, "visual", (prev) => [
      ...prev,
      {
        role: "agent",
        content: "🚀 打样已通过，正在启动全量生成所有页面...",
        agentRole: "visual",
        loading: true,
        id: loadingId,
      },
    ]);
    let generationRunId: string | null = null;
    try {
      const result = await confirmPrototype(projectId);
      if (result?.run?.id) {
        generationRunId = String(result.run.id);
        locallyHandledRunIdsRef.current.add(generationRunId);
        updateProjectChatMessages(projectId, "visual", (prev) =>
          prev.map((m) => (m.id === loadingId ? { ...m, runId: result.run.id, content: runProgressText(result.run) } : m))
        );
      }
      await loadStatus(projectId);
      addSystemLog("用户确认打样效果，开始批量生成");
      // 轮询等待全量生成完成
      updateProjectChatMessages(projectId, "visual", (prev) => [
        ...prev.filter((m) => m.id !== loadingId),
        {
          role: "agent",
          content: "🚀 全量生成已启动，正在后台生成所有页面...",
          agentRole: "visual",
          loading: true,
          id: loadingId,
        },
      ]);
      const finalStatus = await pollUntilStatusNotGenerating(projectId);
      generationLoadingIdRef.current = null;
      updateProjectChatMessages(projectId, "visual", (prev) => [
        ...prev.filter((m) => m.id !== loadingId),
        {
          role: "agent",
          content: finalStatus === "completed"
            ? "✅ 全部页面生成完成。\n\n👉 下一步：点击上方「下载 PPTX」获取最终文件；需要调整时可选中页面重新生成。"
            : `⚠️ 生成结束（状态：${finalStatus}），部分页面可能未成功，请检查进度。`,
          agentRole: "visual",
          nextAction: finalStatus === "completed" ? { type: "download", label: "下载 PPTX" } : undefined,
        },
      ]);
    } catch (err: any) {
      if (generationRunId) {
        locallyHandledRunIdsRef.current.delete(generationRunId);
      }
      showToast("全量生成失败：" + (err.message || "未知错误"), "error");
      generationLoadingIdRef.current = null;
      updateProjectChatMessages(projectId, "visual", (prev) => [
        ...prev.filter((m) => m.id !== loadingId),
        {
          role: "agent",
          content: "❌ 全量生成失败：" + (err.message || "未知错误") + "\n\n👉 请检查网络后重试。",
          agentRole: "visual",
        },
      ]);
    } finally {
      setOperatingProjectId(null);
    }
  };

  // 轮询等待后端 active_run 结束
  const pollUntilStatusNotGenerating = async (projectId: string, timeoutMs = 1_200_000) => {
    const start = Date.now();
    let pollErrors = 0;
    while (Date.now() - start < timeoutMs) {
      await new Promise((r) => setTimeout(r, 3000));
      try {
        const statusData = await fetchWorkflowStatus(projectId);
        if (selectedProjectIdRef.current === projectId) {
          setProjectStatus(statusData?.project_id === projectId ? statusData : null);
        }
        pollErrors = 0;
        const projectStage = statusData.project_status;
        if (!isRunActive(statusData.active_run)) {
          await loadSlides(projectId);
          await loadProjects();
          return projectStage;
        }
      } catch (err) {
        console.warn("Generation status poll error:", err);
        pollErrors++;
        if (pollErrors >= GENERATION_MAX_POLL_ERRORS) {
          throw new Error("连续无法获取生成状态，请检查后端服务后重试");
        }
      }
    }
    throw new Error("前端等待超时（后台 Celery 任务可能仍在运行），请稍后刷新页面查看结果，不要重复点击。");
  };

	  const handleRegenerateSlideFromEdits = async (slideId: string, changes: SlideEditChangeSet) => {
	    if (!selectedProject) return;
	    const projectId = selectedProject.id;
    if (operatingProjectId === projectId || hasActiveRun) {
      throw new Error("当前已有任务在执行中，请等待完成后再重新生成。");
    }
    const slide = slides.find((item) => item.id === slideId) || editingSlideRef.current;
    if (!slide || slide.id !== slideId) {
      throw new Error("没有找到当前页面，请刷新后重试。");
    }

	    const pageNums = [slide.page_num];
	    const needsVisualPlan = !changes.visualChanged && (changes.contentChanged || !slide.visual_json?.visual_description);
	    const needsPrompt = needsVisualPlan || changes.visualChanged || !slide.prompt_text;
	    const stageContext = buildCrossStageContext("visual");
	    const loadingId = `single-regenerate-${slideId}-${Date.now()}`;
	    const updateSinglePageRunMessage = (content: string, extra: Partial<ChatMessage> = {}) => {
	      updateFinetuneChatMessages(slideId, (prev) => {
	        const nextMessage: ChatMessage = {
	          role: "agent",
	          content,
	          agentRole: "finetune",
	          loading: true,
	          id: loadingId,
	          ...extra,
	        };
	        const existing = prev.find((message) => message.id === loadingId);
	        if (existing) {
	          return prev.map((message) =>
	            message.id === loadingId ? { ...message, ...nextMessage } : message
	          );
	        }
	        return [...prev, nextMessage];
	      });
	    };
	    updateSinglePageRunMessage(`正在保存修改并重新生成第 ${slide.page_num} 页...`);
	    setOperatingProjectId(projectId);
	    let generationRunId: string | null = null;
	    try {
	      if (needsVisualPlan) {
	        updateSinglePageRunMessage(`正在更新第 ${slide.page_num} 页画面描述...`);
	        showToast(`正在更新第 ${slide.page_num} 页画面描述...`, "info");
	        await generateVisualPlan(projectId, pageNums, stageContext);
	        clearSlideStale(slideId, "content");
	      }
	      if (needsPrompt) {
	        updateSinglePageRunMessage(`正在更新第 ${slide.page_num} 页生图 Prompt...`);
	        showToast(`正在更新第 ${slide.page_num} 页生图 Prompt...`, "info");
	        await generatePrompts(projectId, pageNums, stageContext);
	        clearSlideStale(slideId, "visual");
	      }
	      updateSinglePageRunMessage(`正在启动第 ${slide.page_num} 页图片生成...`);
	      showToast(`正在重新生成第 ${slide.page_num} 页图片...`, "info");
	      const result = await startGeneration(projectId, pageNums);
	      generationRunId = result?.run?.id ? String(result.run.id) : null;
	      if (generationRunId) {
	        updateSinglePageRunMessage(runProgressText(result.run), { runId: generationRunId });
	      }
	      await pollUntilStatusNotGenerating(projectId);
	      const freshSlides = await loadSlides(projectId);
	      const freshSlide = freshSlides.find((item: Slide) => item.id === slideId);
      if (freshSlide?.status === "failed") {
        throw new Error(freshSlide.error_msg || `第 ${slide.page_num} 页图片生成失败`);
      }
	      if (freshSlide?.image_path) {
	        bumpSlideImageRefresh(slideId);
	      }
	      clearSlideStale(slideId);
	      await loadProjects();
	      updateFinetuneChatMessages(slideId, (prev) => [
	        ...prev.filter((message) => message.id !== loadingId),
	        {
	          role: "agent",
	          content: `第 ${slide.page_num} 页已重新生成，页面图片已刷新。`,
	          agentRole: "finetune",
	        },
	      ]);
	      addSystemLog(`用户保存并重新生成了第 ${slide.page_num} 页`);
	    } catch (err: any) {
	      if (generationRunId) {
	        locallyHandledRunIdsRef.current.delete(generationRunId);
	      }
	      updateFinetuneChatMessages(slideId, (prev) => [
	        ...prev.filter((message) => message.id !== loadingId),
	        {
	          role: "agent",
	          content: "重新生成失败：" + (err.message || "未知错误"),
	          agentRole: "finetune",
	        },
	      ]);
	      throw err;
	    } finally {
	      setOperatingProjectId(null);
	    }
	  };

  // 更新画面方案：只更新画面描述/提示词，不自动生图。
  const handleUpdateStaleSlides = async (targetSlideIds?: string[], options?: { local?: boolean }) => {
    if (!selectedProject) return;
    const projectId = selectedProject.id;
    const targets = targetSlideIds
      ? slides
          .filter((slide) => targetSlideIds.includes(slide.id))
          .map((slide) => ({
            slide,
            stale: staleMap[slide.id] || { content: true },
          }))
          .filter((x) => x.stale.content || x.stale.visual || x.stale.image)
      : staleSlides;
    if (targets.length === 0) return;

    if (!options?.local) {
      setOperatingProjectId(projectId);
    }
    try {
      const needsFullPlan = targets.filter((x) => x.stale.content);
      const needsPrompt = targets.filter((x) => x.stale.content || x.stale.visual);
      const pageNumsForPrompt = Array.from(new Set(needsPrompt.map((x) => x.slide.page_num)));

      if (needsFullPlan.length > 0) {
        showToast(`正在更新 ${needsFullPlan.length} 页的画面描述...`, "info");
        const pageNums = needsFullPlan.map((x) => x.slide.page_num);
        await generateVisualPlan(projectId, pageNums, buildCrossStageContext("visual"));
        await loadSlides(projectId);
      }

      if (pageNumsForPrompt.length > 0) {
        showToast(`正在更新 ${pageNumsForPrompt.length} 页的生图提示词...`, "info");
        await generatePrompts(projectId, pageNumsForPrompt, buildCrossStageContext("visual"));
        await loadSlides(projectId);
        needsPrompt.forEach((x) => {
          clearSlideStale(x.slide.id, "content");
          clearSlideStale(x.slide.id, "visual");
          markSlideStale(x.slide.id, "image");
        });
      }

      const imageStale = targets.filter((x) => x.stale.image);
      if (imageStale.length > 0) {
        showToast(`${imageStale.length} 页需重新生成图片，请先确认`, "info");
        updateProjectChatMessages(projectId, "visual", (prev) => [
          ...prev,
          {
            role: "agent",
            content: `🎨 ${imageStale.length} 页已经需要重新生成图片。\n\n👉 请先检查单页里的文字、参考图、画面描述和生图提示词，再点击「确认并重新生成图片」。`,
            agentRole: "visual",
          },
        ]);
      }

      showToast("更新完成", "success");
      await loadSlides(projectId);
      await loadProjects();
      const updatedCount = needsPrompt.length;
      if (updatedCount > 0) {
        addSystemLog(`用户更新了 ${updatedCount} 页的画面方案`);
        updateProjectChatMessages(projectId, "visual", (prev) => [
          ...prev,
          {
            role: "agent",
            content: `✅ 已更新 ${updatedCount} 页的画面方案。\n\n这些页面需要重新生成图片。请检查后再确认生图。`,
            agentRole: "visual",
          },
        ]);
      }
    } catch (err: any) {
      showToast("更新失败：" + (err.message || "未知错误"), "error");
      updateProjectChatMessages(projectId, "visual", (prev) => [
        ...prev,
        {
          role: "agent",
          content: "❌ 更新画面方案失败：" + (err.message || "未知错误") + "\n\n👉 请检查网络后重试，或告诉我具体需要调整的地方。",
          agentRole: "visual",
        },
      ]);
    } finally {
      if (!options?.local) {
        setOperatingProjectId(null);
      }
    }
  };

  // 用户确认后，重新生成 image 标记的页面。
  const handleGenerateStaleImages = async (targetSlideIds?: string[], options?: { local?: boolean }) => {
    if (!selectedProject) return;
    const projectId = selectedProject.id;
    const targets = targetSlideIds
      ? imageStaleSlides.filter((x) => targetSlideIds.includes(x.slide.id))
      : imageStaleSlides;
    if (targets.length === 0) return;

    if (!options?.local) {
      setOperatingProjectId(projectId);
    }
    try {
      showToast(`正在重新生成 ${targets.length} 页图片...`, "info");
      const pageNums = targets.map((x) => x.slide.page_num);
      await startGeneration(projectId, pageNums);
      await pollUntilStatusNotGenerating(projectId);
      targets.forEach((x) => clearSlideStale(x.slide.id, "image"));
      showToast("图片生成完成", "success");
      await loadSlides(projectId);
      await loadProjects();
      addSystemLog(`用户确认并重新生成了 ${targets.length} 页图片`);
    } catch (err: any) {
      showToast("生成失败：" + (err.message || "未知错误"), "error");
      updateProjectChatMessages(projectId, "visual", (prev) => [
        ...prev,
        {
          role: "agent",
          content: "❌ 图片生成失败：" + (err.message || "未知错误") + "\n\n👉 解决方法：\n1. 检查这一页的提示词和参考图后重试\n2. 如果某页反复失败，可以单独进入该页调整画面方案\n3. 也可以告诉我具体哪一页有问题，我来帮你调整",
          agentRole: "visual",
        },
      ]);
    } finally {
      if (!options?.local) {
        setOperatingProjectId(null);
      }
    }
  };

  const handleRollback = async (targetStage: string) => {
    if (!selectedProject) return;
    const projectId = selectedProject.id;
    const stageNames: Record<string, string> = {
      planning: "内容规划",
      visual_ready: "视觉方案",
      prompt_ready: "画面设计",
      prototype_ready: "效果预览",
      completed: "批量生成",
    };
    const ok = await showConfirm(
      `回到「${stageNames[targetStage] || targetStage}」？\n这会清除后面的设计和图片结果，需要重新生成。`
    );
    if (!ok) return;

    // 全面清理所有运行中状态和轮询
    setChatLoading(false);
    setThinkingContent("");
    setThinkingExpanded(false);
    setContentPlanProgress(null);
    abortActiveChat(true);
    if (visualPromptIntervalRef.current) {
      clearInterval(visualPromptIntervalRef.current);
      visualPromptIntervalRef.current = null;
    }
    if (contentPlanProgressIntervalRef.current) {
      clearInterval(contentPlanProgressIntervalRef.current);
      contentPlanProgressIntervalRef.current = null;
    }
    if (contentPlanCheckIntervalRef.current) {
      clearInterval(contentPlanCheckIntervalRef.current);
      contentPlanCheckIntervalRef.current = null;
    }
    if (contentPlanPollTimeoutRef.current) {
      clearTimeout(contentPlanPollTimeoutRef.current);
      contentPlanPollTimeoutRef.current = null;
    }
    if (contentPlanStopTimeoutRef.current) {
      clearTimeout(contentPlanStopTimeoutRef.current);
      contentPlanStopTimeoutRef.current = null;
    }
    if (contentPlanStartingProjectRef.current === projectId) {
      contentPlanStartingProjectRef.current = null;
      contentPlanStartingAtRef.current = 0;
    }

    // 如果项目正在生成中，先停止生成任务，避免回退后任务完成又覆盖状态
    if (hasActiveRun) {
      try {
        await stopGeneration(projectId);
      } catch {
        // 忽略停止失败的错误，继续回退
      }
    }

    setOperatingProjectId(projectId);
    try {
      await rollbackProject(projectId, targetStage);
      await loadProjects();
      const fresh = normalizeProjectsForActiveSelection(await fetchProjects(), selectedProjectIdRef.current);
      const updated = fresh.find((p: Project) => p.id === projectId);
      if (updated && selectedProjectIdRef.current === projectId) setSelectedProject(clearProjectNotification(updated));
      await loadSlides(projectId);
      const nextGateRevision = (gateRevisionMap[projectId] || 0) + 1;
      const targetGate: WorkflowGate =
        targetStage === "planning"
          ? "content"
          : targetStage === "visual_ready"
          ? "visual"
          : targetStage === "prompt_ready"
          ? "visual_design"
          : "prototype";
      if (selectedProjectIdRef.current === projectId) {
        setStaleMap({});
        setSelectedPages(new Set());
        setPrototypeSelectionTouched(false);
        setEditingSlide(null);
        setGalleryModal(null);
        setShowTemplateRecommender(false);
        setStyleProposalsInChat([]);
        setExpandedStyleProposalKey(null);
        setGateRevisionMap((prev) => ({ ...prev, [projectId]: nextGateRevision }));
      }

      // 根据回退目标生成详细的自动化引导消息
      let rollbackMsg = `已回到「${stageNames[targetStage] || targetStage}」。后面的设计和图片结果需要重新生成。`;
      if (targetStage === "visual_ready") {
        const logoAssets = referenceImages.filter(isConfirmedLogoRef);
        const styleRefAssets = referenceImages.filter((r: any) => r.role === "style_ref");
        const templateAsset = referenceImages.find((r: any) => r.role === "template");
        const visualAssetAssets = referenceImages.filter((r: any) => r.role === "visual_asset");
        rollbackMsg += `\n\n**现在可以重新确认视觉方向。** 为了给你更精准的风格提案和画面生成，请先确认当前的项目素材：\n\n📎 **素材清单（参考强度从高到低）**\n• 品牌 Logo：${logoAssets.length ? `已上传 ${logoAssets.length} 个 ✅` : "未上传"}\n• 可复用素材：${visualAssetAssets.length > 0 ? `已上传 ${visualAssetAssets.length} 个 ✅` : "未上传"}\n• 风格参考：${styleRefAssets.length > 0 ? `已上传 ${styleRefAssets.length} 张 ✅` : "未上传"}\n• 版式模板：${templateAsset ? "已上传 ✅" : "未上传"}\n• 风格描述：可在聊天中直接告诉我（如"更商务一点""要温暖生活感"）\n\n你可以：**① 继续上传素材**（品牌 Logo / 可复用素材 / 风格参考 / 版式模板）→ **② 告诉我你的风格偏好** → **③ 或直接说"开始提案"**，我会基于现有信息立即生成风格方案。`;
      } else if (targetStage === "planning") {
        rollbackMsg += `\n\n**现在可以继续调整内容规划。**\n\n• 增减页数、调整章节结构\n• 修改某一页的标题或正文（直接说"修改第X页"）\n• 更换整体内容方向或主题\n\n👉 确认内容规划满意后，我们再一起进入视觉设计。`;
      } else if (targetStage === "prompt_ready") {
        rollbackMsg += `\n\n**画面设计已重新打开。** 已保留内容和已确认风格，图片结果已回到待打样状态。\n\n👉 你可以在作品画布检查每页画面方案、选择打样页，或告诉我需要重抽哪一页。`;
      } else if (targetStage === "prototype_ready") {
        rollbackMsg += `\n\n**已回到效果预览。** 这里会保留已有样张，方便你继续检查风格、构图和文字可读性。\n\n👉 如果效果不满意，请点击「重新打样」。`;
      }
      updateProjectChatMessages(projectId, targetStage === "planning" ? "content" : "visual", (prev) => [
        ...prev.filter((m) => !m.loading),
        { role: "system" as const, content: `用户回退到「${stageNames[targetStage] || targetStage}」阶段` },
        {
          role: "agent" as const,
          content: rollbackMsg,
          agentRole: targetStage === "planning" ? "content" : "visual",
          gate: targetGate,
          gateRevision: nextGateRevision,
        },
      ]);

      // 根据回退目标调整 Agent 角色
      if (selectedProjectIdRef.current === projectId) {
        if (targetStage === "planning") {
          setCurrentAgentRole("content");
          setContentPlanConfirmed(false);
        } else if (targetStage === "visual_ready") {
          setCurrentAgentRole("visual");
          setContentPlanConfirmed(true);
        } else if (targetStage === "prompt_ready" || targetStage === "prototype_ready") {
          setCurrentAgentRole("visual");
          setContentPlanConfirmed(true);
        }
      }

      showToast("回退成功", "success");
    } catch (err: any) {
      showToast("回退失败：" + (err.message || "未知错误"), "error");
      updateProjectChatMessages(projectId, "visual", (prev) => [
        ...prev,
        {
          role: "agent",
          content: "❌ 没能回到目标位置：" + (err.message || "未知错误") + "\n\n👉 请检查网络后，在顶部流程条重新选择要回到的位置。",
          agentRole: "visual",
        },
      ]);
    } finally {
      setOperatingProjectId(null);
    }
  };

  const togglePage = (pageNum: number) => {
    setSelectedPages((prev) => {
      const next = new Set(prototypeSelectionTouched ? prev : defaultPrototypePageNums);
      if (next.has(pageNum)) {
        next.delete(pageNum);
      } else {
        next.add(pageNum);
      }
      return next;
    });
    setPrototypeSelectionTouched(true);
  };

  const selectAll = () => {
    setSelectedPages(new Set(slides.map((s) => s.page_num)));
    setPrototypeSelectionTouched(true);
  };

  const clearSelection = () => {
    setSelectedPages(new Set());
    setPrototypeSelectionTouched(false);
  };

  const slideHasPrompt = (slide: Slide) => Boolean(slide.prompt_text && String(slide.prompt_text).trim());

  const getPrototypeTargetSlides = (explicitPageNums: number[] = []) => {
    if (explicitPageNums.length > 0) {
      const targetSet = new Set(explicitPageNums);
      return slides.filter((slide) => targetSet.has(slide.page_num));
    }
    const targetPageNums = prototypeSelectionTouched ? Array.from(selectedPages) : defaultPrototypePageNums;
    if (targetPageNums.length === 0) return [];
    const targetSet = new Set(targetPageNums);
    return slides.filter((slide) => targetSet.has(slide.page_num));
  };

  const getPrototypeResampleTargetSlides = (explicitPageNums: number[] = []) => {
    const explicitOrSelected = getPrototypeTargetSlides(explicitPageNums);
    if (explicitPageNums.length > 0 || selectedPages.size > 0 || currentStatus !== "prototype_ready") {
      return explicitOrSelected;
    }
    const sampledSlides = slides.filter((slide) => slide.image_path);
    return sampledSlides.length > 0 ? sampledSlides : explicitOrSelected;
  };

  const getFullGenerationTargetSlides = (explicitPageNums: number[] = []) => {
    if (explicitPageNums.length > 0) {
      const targetSet = new Set(explicitPageNums);
      return slides.filter((slide) => targetSet.has(slide.page_num));
    }
    return slides;
  };

  const handleRetry = async (slideId: string, regeneratePrompt: boolean = false) => {
    if (!selectedProject) return;
    if (operatingProjectId === selectedProject.id) return;
    const projectId = selectedProject.id;
    if (hasActiveRun) {
      showToast("当前已有生成任务在执行中，请稍后再试", "info");
      return;
    }
    const slide = slides.find((s) => s.id === slideId);
    setOperatingProjectId(projectId);
    try {
      const result = await retrySlide(projectId, slideId, regeneratePrompt);
      await loadSlides(projectId);
      await loadStatus(projectId);
      const loadingId = `gen-${Date.now()}`;
      generationLoadingIdRef.current = loadingId;
      updateProjectChatMessages(projectId, "visual", (prev) => [
        ...prev,
        {
          role: "agent",
          content: result?.run ? runProgressText(result.run) : `🔄 正在重新生成第 ${slide?.page_num || "?"} 页...`,
          agentRole: "visual",
          loading: true,
          id: loadingId,
          runId: result?.run?.id,
        },
      ]);
    } catch (err: any) {
      showToast("重试失败：" + (err.message || "未知错误"), "error");
      updateProjectChatMessages(projectId, "visual", (prev) => [
        ...prev,
        {
          role: "agent",
          content: "❌ 重试失败：" + (err.message || "未知错误") + "\n\n👉 解决方法：\n1. 检查网络后点击「重试」按钮再次尝试\n2. 如果多次失败，可以进入单页编辑修改画面描述后重新生成\n3. 也可以告诉我这页想要什么样的效果，我来帮你调整",
          agentRole: "visual",
        },
      ]);
    } finally {
      setOperatingProjectId(null);
    }
  };

  const handleRetryAllFailed = async () => {
    if (!selectedProject) return;
    if (operatingProjectId === selectedProject.id) return;
    const projectId = selectedProject.id;
    const failedSlides = slides.filter((s) => s.status === "failed");
    if (failedSlides.length === 0) {
      showToast("当前没有失败的页面", "info");
      return;
    }
    setOperatingProjectId(projectId);
    const loadingId = `retry-${Date.now()}`;
    generationLoadingIdRef.current = loadingId;
    try {
      const result = await retryFailed(projectId);
      showToast(`已启动 ${result.count} 个失败页面的重试`, "success");
      await loadSlides(projectId);
      await loadStatus(projectId);
      addSystemLog(`用户重试了 ${result.count} 个失败页面`);
      updateProjectChatMessages(projectId, "visual", (prev) => [
        ...prev,
        {
          role: "agent",
          content: `🚀 已启动 ${result.count} 个失败页面的重试（第 ${result.page_nums.join(", ")} 页），正在生成...`,
          agentRole: "visual",
          loading: true,
          id: loadingId,
          runId: result.run?.id,
        },
      ]);
    } catch (err: any) {
      showToast("批量重试失败：" + (err.message || "未知错误"), "error");
      generationLoadingIdRef.current = null;
      updateProjectChatMessages(projectId, "visual", (prev) => [
        ...prev,
        {
          role: "agent",
          content: "❌ 批量重试失败：" + (err.message || "未知错误") + "\n\n👉 解决方法：\n1. 检查网络后点击「重试失败页面」按钮再次尝试\n2. 如果某页反复失败，可以单独选中该页重新生成\n3. 也可以告诉我具体哪一页有问题，我来帮你调整画面描述",
          agentRole: "visual",
        },
      ]);
    } finally {
      setOperatingProjectId(null);
    }
  };

  // ========== 单页微调：版本管理 ==========

  const loadSlideVersions = async (slideId: string) => {
    if (!selectedProject) return;
    try {
      const versions = await getSlideVersions(selectedProject.id, slideId);
      setSlideVersionsMap((prev) => ({ ...prev, [slideId]: versions }));
    } catch {
      // 静默失败，版本不是关键路径
    }
  };

  const handleRestoreVersion = async (slideId: string, versionId: string) => {
    if (!selectedProject) return;
    try {
      await restoreSlideVersion(selectedProject.id, slideId, versionId);
      showToast("已恢复历史版本", "info");
      await loadSlideVersions(slideId);
      const updated = await loadSlides(selectedProject.id);
      const fresh = updated.find((s: Slide) => s.id === slideId);
      if (fresh && editingSlide?.id === slideId) setEditingSlide(fresh);
      bumpSlideImageRefresh(slideId);
    } catch (err: any) {
      showToast("恢复失败：" + (err.message || "未知错误"), "error");
    }
  };

  const handleDeleteVersion = async (slideId: string, versionId: string) => {
    if (!selectedProject) return;
    try {
      await deleteSlideVersion(selectedProject.id, slideId, versionId);
      await loadSlideVersions(slideId);
    } catch (err: any) {
      showToast("删除失败：" + (err.message || "未知错误"), "error");
    }
  };

  const handleEnterEdit = (slide: Slide) => {
    setEditingSlide(slide);
    setAgentMode("page");
  };

  const handleExitEdit = () => {
    setEditingSlide(null);
    setAgentMode("global");
  };

  const handlePrevSlide = () => {
    if (!editingSlide || !slides.length) return;
    const currentIndex = slides.findIndex((s) => s.id === editingSlide.id);
    if (currentIndex > 0) {
      setEditingSlide(slides[currentIndex - 1]);
    }
  };

  const handleNextSlide = () => {
    if (!editingSlide || !slides.length) return;
    const currentIndex = slides.findIndex((s) => s.id === editingSlide.id);
    if (currentIndex < slides.length - 1) {
      setEditingSlide(slides[currentIndex + 1]);
    }
  };

  const handlePinAssetToSlide = async (slideId: string, assetId: string) => {
    if (!selectedProject) return;
    const slide = slides.find((s) => s.id === slideId) || editingSlide;
    if (!slide) return;
    const currentIds: string[] = Array.isArray(slide.visual_json?.manual_visual_asset_ids)
      ? slide.visual_json.manual_visual_asset_ids.map(String)
      : [];
    if (currentIds.includes(assetId)) return;
    const usage = slide.visual_json?.manual_visual_asset_usage || {};
    try {
      await updateSlideAssetPins(selectedProject.id, slideId, [...currentIds, assetId], usage);
      markSlideStale(slideId, "visual");
      const updated = await loadSlides(selectedProject.id);
      const fresh = updated.find((s: Slide) => s.id === slideId);
      if (fresh && editingSlide?.id === slideId) setEditingSlide(fresh);
      showToast(currentIds.length >= 5 ? "已锁定；参考图较多，生成时可能不稳定" : "已锁定到本页", "success");
      addSystemLog(`用户将素材锁定到第 ${slide.page_num} 页`);
    } catch (err: any) {
      showToast("锁定失败：" + (err.message || "未知错误"), "error");
    }
  };

  const handleUnpinAssetFromSlide = async (slideId: string, assetId: string) => {
    if (!selectedProject) return;
    const slide = slides.find((s) => s.id === slideId) || editingSlide;
    if (!slide) return;
    const currentIds: string[] = Array.isArray(slide.visual_json?.manual_visual_asset_ids)
      ? slide.visual_json.manual_visual_asset_ids.map(String)
      : [];
    const nextIds = currentIds.filter((id: string) => id !== assetId);
    const usage = { ...(slide.visual_json?.manual_visual_asset_usage || {}) };
    delete usage[assetId];
    try {
      await updateSlideAssetPins(selectedProject.id, slideId, nextIds, usage);
      const overlayLayers = Array.isArray(slide.visual_json?.overlay_layers)
        ? slide.visual_json.overlay_layers.filter((layer: any) => String(layer?.asset_id) !== assetId)
        : [];
      await updateSlideOverlayLayers(selectedProject.id, slideId, overlayLayers);
      markSlideStale(slideId, "visual");
      const updated = await loadSlides(selectedProject.id);
      const fresh = updated.find((s: Slide) => s.id === slideId);
      if (fresh && editingSlide?.id === slideId) setEditingSlide(fresh);
      showToast("已取消锁定", "success");
      addSystemLog(`用户取消了第 ${slide.page_num} 页的素材锁定`);
    } catch (err: any) {
      showToast("取消锁定失败：" + (err.message || "未知错误"), "error");
    }
  };

  const handleUpdateOverlayLayers = async (slideId: string, layers: any[]) => {
    if (!selectedProject) return;
    const slide = slides.find((s) => s.id === slideId) || editingSlide;
    try {
      await updateSlideOverlayLayers(selectedProject.id, slideId, layers);
      markSlideStale(slideId, "visual");
      const updated = await loadSlides(selectedProject.id);
      const fresh = updated.find((s: Slide) => s.id === slideId);
      if (fresh && editingSlide?.id === slideId) setEditingSlide(fresh);
      const exactCount = (layers || []).filter((layer: any) => layer?.enabled !== false).length;
      showToast(exactCount > 2 ? "原样出现已更新；贴图较多，建议留出版面空间" : "原样出现已更新", "success");
      if (slide) addSystemLog(`用户更新了第 ${slide.page_num} 页的原样出现素材`);
    } catch (err: any) {
      showToast("原样出现更新失败：" + (err.message || "未知错误"), "error");
    }
  };

  const handleDeleteSlide = async (slideId: string) => {
    if (!selectedProject) return;
    const ok = await showConfirm("确定要删除这一页吗？此操作不可恢复。");
    if (!ok) return;
    pushSlidesHistory(slides);
    const slide = slides.find((s) => s.id === slideId);
    try {
      await deleteSlide(selectedProject.id, slideId);
      await loadProjects();
      await loadSlides(selectedProject.id);
      if (slide) {
        addSystemLog(`用户删除了第 ${slide.page_num} 页（类型：${slide.type || "content"}）`);
      }
    } catch (err: any) {
      showToast("删除失败：" + (err.message || "未知错误"), "error");
    }
  };

  const handleInsertSlideBefore = async (slideId: string) => {
    if (!selectedProject) return;
    const slide = slides.find((s) => s.id === slideId);
    if (!slide) return;
    pushSlidesHistory(slides);
    try {
      const pageNum = slide.page_num;
      const defaultContent = {
        type: "content",
        text: { headline: "新页面", subhead: "", body: "" },
      };
      await createSlide(selectedProject.id, pageNum, defaultContent);
      await loadProjects();
      await loadSlides(selectedProject.id);
      if (editingSlide) {
        const updated = await fetchSlides(selectedProject.id);
        const freshSlide = updated.find((s: Slide) => s.id === editingSlide.id);
        if (freshSlide) setEditingSlide(freshSlide);
      }
      addSystemLog(`用户在第 ${slide.page_num} 页前插入了新页面`);
      showToast("已在前方插入新页面", "success");
    } catch (err: any) {
      showToast("插入失败：" + (err.message || "未知错误"), "error");
    }
  };

  const handleInsertSlideAfter = async (slideId: string) => {
    if (!selectedProject) return;
    const slide = slides.find((s) => s.id === slideId);
    if (!slide) return;
    pushSlidesHistory(slides);
    try {
      const pageNum = slide.page_num + 1;
      const defaultContent = {
        type: "content",
        text: { headline: "新页面", subhead: "", body: "" },
      };
      await createSlide(selectedProject.id, pageNum, defaultContent);
      await loadProjects();
      await loadSlides(selectedProject.id);
      if (editingSlide) {
        const updated = await fetchSlides(selectedProject.id);
        const freshSlide = updated.find((s: Slide) => s.id === editingSlide.id);
        if (freshSlide) setEditingSlide(freshSlide);
      }
      addSystemLog(`用户在第 ${slide.page_num} 页后插入了新页面`);
      showToast("已在后方插入新页面", "success");
    } catch (err: any) {
      showToast("插入失败：" + (err.message || "未知错误"), "error");
    }
  };

  const handleReorder = async (newOrder: Slide[]) => {
    if (!selectedProject) return;
    pushSlidesHistory(slides);
    const pageNums = newOrder.map((s) => s.page_num);
    try {
      await reorderSlides(selectedProject.id, pageNums);
      await loadProjects();
      await loadSlides(selectedProject.id);
      addSystemLog(`用户调整了页面顺序：${pageNums.join(" → ")}`);
    } catch (err: any) {
      showToast("排序失败：" + (err.message || "未知错误"), "error");
    }
  };

  const handleUploadPageRef = async (slideId: string) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.multiple = true;
    input.onchange = async (e) => {
      const files = Array.from((e.target as HTMLInputElement).files || []);
      if (files.length === 0 || !selectedProject) return;
      setOperatingProjectId(selectedProject.id);
      try {
        if (currentAgentRole === "finetune" && finetuneTargetSlideId === slideId) {
          const uploaded: ChatAttachment[] = [];
          for (const file of files) {
            const data = await uploadFile(selectedProject.id, file, "finetune_ref", slideId);
            uploaded.push({
              id: data.id,
              name: file.name,
              url: `${API_BASE}${data.url}`,
              role: "finetune_ref",
            });
          }
          setPendingFinetuneAttachmentsMap((prev) => ({
            ...prev,
            [slideId]: [...(prev[slideId] || []), ...uploaded],
          }));
          showToast(files.length > 1 ? `已加入 ${files.length} 张本轮微调参考图` : "参考图已加入本轮微调", "success");
          addSystemLog(`用户为第 ${slides.find((s) => s.id === slideId)?.page_num || "?"} 页添加了本轮微调参考图`);
          return;
        }
        for (const file of files) {
          await uploadFile(selectedProject.id, file, "content_ref", slideId, "blend", {
            asset_name: file.name.replace(/\.[^.]+$/, ""),
            usage_note: "用户从单页上传的本页参考图",
          });
        }
        const slide = slides.find((s) => s.id === slideId);
        markSlideStale(slideId, "visual");
        await loadProjects();
        const updated = await loadSlides(selectedProject.id);
        const fresh = updated.find((s: Slide) => s.id === slideId);
        if (fresh && editingSlide?.id === slideId) setEditingSlide(fresh);
        const pageNum = slide?.page_num || "?";
        showToast(files.length > 1 ? `已加入 ${files.length} 张本页参考图` : "已加入本页参考图", "success");
        addSystemLog(`用户为第 ${pageNum} 页上传了 ${files.length} 张本页参考图`);
        // 微调模式下，在聊天中给予可见反馈
        if (currentAgentRole === "finetune" && finetuneTargetSlideId === slideId) {
          updateFinetuneChatMessages(slideId, (current) => [
            ...current,
            { role: "system", content: `已添加参考图到第 ${pageNum} 页。下一条修改要求会自动带上这张图。` },
          ]);
        }
      } catch (err: any) {
        showToast("上传失败：" + (err.message || "未知错误"), "error");
      } finally {
        setOperatingProjectId(null);
      }
    };
    input.click();
  };

  const getBriefAttachmentLabel = (kind: "doc" | "image", id: string) => {
    if (kind === "doc") {
      const doc = documents.find((item: any) => item.filename === id);
      return doc?.filename || id;
    }
    const ref = briefImageAttachments.find((item: any) => item.id === id);
    const basename = ref?.url?.split("/").pop();
    return ref?.asset_name || (basename ? basename.replace(/^content_ref_/, "") : id);
  };

  const getFileTypeBadge = (filename: string, kind?: "doc" | "image") => {
    if (kind === "image") return "IMG";
    const clean = filename.split("?")[0].split("#")[0].toLowerCase();
    const ext = clean.includes(".") ? clean.slice(clean.lastIndexOf(".") + 1) : "";
    if (["ppt", "pptx"].includes(ext)) return "PPT";
    if (["pdf"].includes(ext)) return "PDF";
    if (["doc", "docx"].includes(ext)) return "DOC";
    if (["md", "markdown"].includes(ext)) return "MD";
    if (["txt"].includes(ext)) return "TXT";
    if (["csv", "tsv", "xls", "xlsx"].includes(ext)) return "DATA";
    if (["png", "jpg", "jpeg", "webp", "gif", "svg", "bmp", "tif", "tiff", "heic"].includes(ext)) return "IMG";
    return kind === "doc" ? "FILE" : "FILE";
  };

  const getBriefAttachmentIcon = (kind: "doc" | "image", id: string) => {
    if (kind === "image") return "IMG";
    const doc = documents.find((item: any) => item.filename === id);
    return getFileTypeBadge(doc?.filename || id, kind);
  };

  const createBriefChipElement = (token: string) => {
    const parsed = parseBriefAttachmentToken(token);
    if (!parsed) return document.createTextNode(token);
    const chip = document.createElement("span");
    chip.className = "pg-brief-inline-chip";
    chip.contentEditable = "false";
    chip.draggable = true;
    chip.dataset.briefToken = token;
    chip.innerHTML =
      `<span class="pg-brief-inline-icon">${getBriefAttachmentIcon(parsed.kind, parsed.id)}</span>` +
      `<span class="pg-brief-inline-name">${escapeHtml(getBriefAttachmentLabel(parsed.kind, parsed.id))}</span>` +
      `<button type="button" class="pg-brief-inline-remove" data-brief-remove="true" title="移除">X</button>`;
    return chip;
  };

  const renderBriefEditorHtml = (value: string) => {
    let html = "";
    let lastIndex = 0;
    value.replace(BRIEF_ATTACHMENT_RE, (match, _kind, _id, offset) => {
      html += escapeHtml(value.slice(lastIndex, offset)).replace(/\n/g, "<br>");
      const parsed = parseBriefAttachmentToken(match);
      if (parsed) {
        html +=
          `<span class="pg-brief-inline-chip" contenteditable="false" draggable="true" data-brief-token="${escapeHtml(match)}">` +
          `<span class="pg-brief-inline-icon">${getBriefAttachmentIcon(parsed.kind, parsed.id)}</span>` +
          `<span class="pg-brief-inline-name">${escapeHtml(getBriefAttachmentLabel(parsed.kind, parsed.id))}</span>` +
          `<button type="button" class="pg-brief-inline-remove" data-brief-remove="true" title="移除">X</button>` +
          `</span>`;
      } else {
        html += escapeHtml(match);
      }
      lastIndex = offset + match.length;
      return match;
    });
    html += escapeHtml(value.slice(lastIndex)).replace(/\n/g, "<br>");
    return html;
  };

  const readBriefEditorValue = () => {
    const root = briefEditorRef.current;
    if (!root) return chatInput;
    let value = "";
    const walk = (node: Node) => {
      if (node.nodeType === Node.TEXT_NODE) {
        value += node.textContent || "";
        return;
      }
      if (!(node instanceof HTMLElement)) return;
      const token = node.dataset.briefToken;
      if (token) {
        value += token;
        return;
      }
      if (node.tagName === "BR") {
        value += "\n";
        return;
      }
      node.childNodes.forEach(walk);
      if ((node.tagName === "DIV" || node.tagName === "P") && value && !value.endsWith("\n")) {
        value += "\n";
      }
    };
    root.childNodes.forEach(walk);
    return value.replace(/\u00a0/g, " ").replace(/\n{3,}/g, "\n\n").trimStart();
  };

  const syncBriefEditorValue = (value: string) => {
    const root = briefEditorRef.current;
    if (!root) return;
    briefEditorValueRef.current = value;
    root.innerHTML = renderBriefEditorHtml(value);
  };

  const clearBriefComposerState = () => {
    briefEditorValueRef.current = "";
    setChatInput("");
    setPendingAttachments([]);
    if (briefEditorRef.current) {
      briefEditorRef.current.innerHTML = "";
    }
  };

  const updateBriefEditorState = () => {
    const value = readBriefEditorValue();
    briefEditorValueRef.current = value;
    setChatInput(value);
  };

  const focusBriefEditorAtEnd = () => {
    const root = briefEditorRef.current;
    if (!root) return;
    root.focus();
    const range = document.createRange();
    range.selectNodeContents(root);
    range.collapse(false);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
  };

  const insertBriefTokenAtSelection = (token: string) => {
    const root = briefEditorRef.current;
    if (!root) return;
    root.focus();
    const selection = window.getSelection();
    let range = selection && selection.rangeCount > 0 ? selection.getRangeAt(0) : null;
    if (!range || !root.contains(range.commonAncestorContainer)) {
      range = document.createRange();
      range.selectNodeContents(root);
      range.collapse(false);
    }
    const chip = createBriefChipElement(token);
    const space = document.createTextNode(" ");
    range.deleteContents();
    range.insertNode(space);
    range.insertNode(chip);
    range.setStartAfter(space);
    range.collapse(true);
    selection?.removeAllRanges();
    selection?.addRange(range);
    updateBriefEditorState();
  };

  const getRangeFromPoint = (x: number, y: number) => {
    const docAny = document as any;
    if (typeof docAny.caretRangeFromPoint === "function") return docAny.caretRangeFromPoint(x, y) as Range | null;
    if (typeof docAny.caretPositionFromPoint === "function") {
      const pos = docAny.caretPositionFromPoint(x, y);
      if (!pos) return null;
      const range = document.createRange();
      range.setStart(pos.offsetNode, pos.offset);
      return range;
    }
    return null;
  };

  const handleBriefEditorDragStart = (event: ReactDragEvent<HTMLDivElement>) => {
    const chip = (event.target as HTMLElement).closest(".pg-brief-inline-chip") as HTMLElement | null;
    const token = chip?.dataset.briefToken;
    if (!chip || !token) return;
    briefDraggedChipRef.current = chip;
    event.dataTransfer.setData("text/plain", token);
    event.dataTransfer.effectAllowed = "move";
  };

  const handleBriefEditorDrop = (event: ReactDragEvent<HTMLDivElement>) => {
    const token = event.dataTransfer.getData("text/plain");
    if (!parseBriefAttachmentToken(token)) return;
    event.preventDefault();
    const root = briefEditorRef.current;
    if (!root) return;
    const range = getRangeFromPoint(event.clientX, event.clientY);
    if (!range || !root.contains(range.commonAncestorContainer)) return;
    briefDraggedChipRef.current?.remove();
    const chip = createBriefChipElement(token);
    const space = document.createTextNode(" ");
    range.insertNode(space);
    range.insertNode(chip);
    briefDraggedChipRef.current = null;
    updateBriefEditorState();
  };

  const handleBriefEditorClick = async (event: ReactMouseEvent<HTMLDivElement>) => {
    const removeButton = (event.target as HTMLElement).closest("[data-brief-remove]");
    if (!removeButton) return;
    event.preventDefault();
    const chip = removeButton.closest(".pg-brief-inline-chip") as HTMLElement | null;
    const token = chip?.dataset.briefToken;
    const parsed = token ? parseBriefAttachmentToken(token) : null;
    chip?.remove();
    updateBriefEditorState();
    if (parsed?.kind === "doc") {
      await handleRemoveBriefDocument(parsed.id);
    } else if (parsed?.kind === "image") {
      await handleRemoveBriefImage(parsed.id);
    }
  };

  const serializeBriefInputForPlan = (value: string) =>
    value.replace(BRIEF_ATTACHMENT_RE, (match) => {
      const parsed = parseBriefAttachmentToken(match);
      if (!parsed) return match;
      return parsed.kind === "doc"
        ? `【文件：${getBriefAttachmentLabel(parsed.kind, parsed.id)}】`
        : `【图片：${getBriefAttachmentLabel(parsed.kind, parsed.id)}】`;
    });

  const getLatestComposerTextForSubmission = (forcedMsg?: string) => {
    if (forcedMsg !== undefined) return serializeBriefInputForPlan(forcedMsg).trim();
    const rawValue = isBriefStudioActive ? readBriefEditorValue() : chatInput;
    return serializeBriefInputForPlan(rawValue).trim();
  };

  const uploadBriefFiles = async (files: File[]) => {
    if (!selectedProject || files.length === 0) return;
    setUploadingDoc(true);
    let uploadedDocs = 0;
    let uploadedImages = 0;
    let docsParsingInBackground = 0;
    const uploadedImageAttachments: ChatAttachment[] = [];
    try {
      for (const file of files) {
        try {
          if (isBriefImageFile(file)) {
            const data = await uploadFile(selectedProject.id, file, "content_ref", undefined, "blend", {
              usage_note: "用户在 Brief Studio 上传，作为内容规划和后续视觉设计参考",
            });
            uploadedImages += 1;
            uploadedImageAttachments.push({
              id: data.id,
              name: file.name,
              url: `${API_BASE}${data.url}`,
              role: "content_ref",
            });
            if (data?.id) insertBriefTokenAtSelection(makeBriefAttachmentToken("image", data.id));
          } else {
            const data = await uploadDocument(selectedProject.id, file);
            if (data.detail) {
              showToast(`"${file.name}" 上传失败：${data.detail}`, "error");
            } else {
              uploadedDocs += 1;
              insertBriefTokenAtSelection(makeBriefAttachmentToken("doc", data.filename));
              const hasBackgroundParsing =
                ["queued", "running"].includes(String(data.text_parse_status || "")) ||
                ["queued", "running"].includes(String(data.asset_extraction_status || ""));
              if (hasBackgroundParsing) {
                docsParsingInBackground += 1;
                [1500, 3500, 8000, 15000, 30000].forEach((delay) => {
                  window.setTimeout(() => {
                    if (selectedProjectIdRef.current === selectedProject.id) {
                      loadDocuments(selectedProject.id);
                      loadReferenceImages(selectedProject.id);
                    }
                  }, delay);
                });
              }
            }
          }
        } catch (err: any) {
          showToast(`"${file.name}" 上传失败：${err.message || "未知错误"}`, "error");
        }
      }
      if (uploadedDocs > 0) await loadDocuments(selectedProject.id);
      if (uploadedImages > 0) await loadReferenceImages(selectedProject.id);
      if (uploadedDocs || uploadedImages) {
        addSystemLog(
          `用户在 Brief Studio 上传了 ${uploadedDocs} 个文档、${uploadedImages} 张图片。图片会作为后续 Agent 对话和内容规划的素材。`,
          uploadedImageAttachments
        );
        if (docsParsingInBackground > 0) {
          showToast(
            `已加入 ${docsParsingInBackground} 个文件，正在后台整理文字和图片素材`,
            "success"
          );
        }
      }
    } finally {
      setUploadingDoc(false);
    }
  };

  const handleUploadDocument = async () => {
    const input = docInputRef.current;
    if (!input || !input.files || input.files.length === 0) return;
    await uploadBriefFiles(Array.from(input.files));
    input.value = "";
  };

  const handleRemoveBriefDocument = async (filename: string) => {
    if (!selectedProject) return;
    try {
      await deleteDocument(selectedProject.id, filename);
      setPendingAttachments((prev) => prev.filter((item) => item !== filename));
      await loadDocuments(selectedProject.id);
    } catch (err: any) {
      showToast("删除失败：" + (err.message || "未知错误"), "error");
    }
  };

  const handleRemoveBriefImage = async (refId: string) => {
    if (!selectedProject) return;
    try {
      await deleteReferenceImage(selectedProject.id, refId);
      await loadReferenceImages(selectedProject.id);
    } catch (err: any) {
      showToast("删除失败：" + (err.message || "未知错误"), "error");
    }
  };

  const handleDeleteDocument = async (filename: string) => {
    if (!selectedProject) return;
    const ok = await showConfirm(`确定删除 "${filename}" 吗？`);
    if (!ok) return;
    try {
      await deleteDocument(selectedProject.id, filename);
      await loadDocuments(selectedProject.id);
    } catch (err: any) {
      showToast("删除失败：" + (err.message || "未知错误"), "error");
    }
  };

  const handleConfirmContentPlan = async () => {
    if (!selectedProject) return;
    if (isConfirmingRef.current) return;
    const projectId = selectedProject.id;

    // 如果已经确认过，只切回视觉总监，不走完整流程
    if (selectedProject.content_plan_confirmed) {
      abortActiveChat(true); // 停止当前流，避免状态错乱
      setCurrentAgentRole("visual");
      setAssetsBarExpanded(true);
      ensureVisualGreetingIfNeeded();
      return;
    }

    isConfirmingRef.current = true;
    setConfirmingProjectId(projectId);
    abortActiveChat(true); // 停止当前 agent 的流，避免切换到视觉总监后状态错乱

    try {
      // 保存当前内容快照用于软锁定检测，同时读取视觉阶段需要展示的素材。
      const [currentSlides, freshReferenceImages] = await Promise.all([
        fetchSlides(projectId),
        fetchReferenceImages(projectId),
      ]);
      if (selectedProjectIdRef.current === projectId) {
        setContentPlanSnapshot(currentSlides);
        softLockWarnedRef.current = false;
        setReferenceImages(freshReferenceImages || []);
      }

      const hasAssets = (freshReferenceImages || []).length > 0;
      const logoAssets = (freshReferenceImages || []).filter(isConfirmedLogoRef);
      const styleRefAssets = (freshReferenceImages || []).filter((r: any) => r.role === "style_ref");
      const templateAsset = (freshReferenceImages || []).find((r: any) => r.role === "template");
      const visualAssetAssets = (freshReferenceImages || []).filter((r: any) => r.role === "visual_asset");
      const assetDesc = [
        logoAssets.length ? `${logoAssets.length}个品牌 Logo` : "",
        visualAssetAssets.length > 0 ? `${visualAssetAssets.length}个可复用素材` : "",
        styleRefAssets.length > 0 ? `${styleRefAssets.length}张风格参考` : "",
        templateAsset ? "版式模板" : "",
      ].filter(Boolean).join("、");

      // 切换状态并显示固定开场白（无需调用 LLM，节省 API 成本）
      if (selectedProjectIdRef.current === projectId) {
        setContentPlanConfirmed(true);
        setCurrentAgentRole("visual");
        setAssetsBarExpanded(true);
      }
      try {
        const updatedProject = await updateProject(projectId, { content_plan_confirmed: true });
        if (selectedProjectIdRef.current === projectId) {
          setSelectedProject(updatedProject);
        }
      } catch (e) {
        console.warn("更新 content_plan_confirmed 失败", e);
      }
      appendProjectChatMessage(projectId, "content", { role: "system", content: `用户确认了内容规划，共 ${currentSlides.length} 页` });
      appendProjectChatMessage(projectId, "visual", { role: "system", content: `用户确认了内容规划，共 ${currentSlides.length} 页` });
      const crossStageContext = buildCrossStageContext("visual");
      if (crossStageContext) {
        appendProjectChatMessage(projectId, "visual", {
          role: "system",
          content: crossStageContext,
          agentRole: "visual",
        });
      }

      // 固定开场白：询问用户是否有素材，等待用户确认后再生成
      const handoffNote = crossStageContext
        ? "\n\n我也会把内容阶段你提过的补充要求带入后续视觉方案和画面 Prompt。"
        : "";
      const directorMsg = hasAssets
        ? `我是视觉总监。已收到你上传的设计素材（${assetDesc}）。${handoffNote}\n\n👉 如果你还想补充素材，请继续上传；如果已经齐了，点击「生成视觉方向」，我会立即基于这些素材制定风格方案。`
        : `我是视觉总监。生成视觉方向前，先确认是否要补充素材：品牌 Logo、可复用素材（产品/主视觉/人物/物料图）、风格参考、版式模板。${handoffNote}\n\n👉 这些都可以在上方「项目素材」上传；没有素材也可以直接点击「生成视觉方向」。`;
      updateProjectChatMessages(projectId, "visual", (prev) => [
        ...prev,
        {
          role: "agent",
          content: directorMsg,
          agentRole: "visual",
        },
      ]);

      // 参考图建议需要一次 LLM 判断，但不应该挡在内容到视觉的切换路上。
      // 后台补充到视觉阶段消息即可，用户可以先继续上传素材或生成视觉方向。
      void suggestReferenceImages(projectId)
        .then((suggestRes) => {
          const suggestions = suggestRes?.suggestions || [];
          if (!suggestions.length || selectedProjectIdRef.current !== projectId) return;
          const suggestionText =
            "页面参考图建议\n\n" +
            suggestions
              .map((s: any) => `第 ${s.page_num} 页（${s.type}）：${s.reason}`)
              .join("\n\n") +
            "\n\n如果某页需要指定图片，可以在单页编辑里添加本页参考图；如果图片必须完整保留，请在项目素材里打开「原样出现」。";
          appendProjectChatMessage(projectId, "visual", {
            role: "agent",
            content: suggestionText,
            agentRole: "visual",
          });
        })
        .catch((e) => {
          console.warn("获取参考图推荐失败", e);
        });
    } catch (err: any) {
      console.error("[ConfirmContentPlan] error:", err);
      showToast("视觉总监介入失败，请重试", "error");
      // 失败时重置状态，让用户可以再次点击确认
      if (selectedProjectIdRef.current === projectId) {
        setContentPlanConfirmed(false);
        setCurrentAgentRole("content");
        setContentPlanSnapshot([]);
        softLockWarnedRef.current = false;
      }
      updateProjectChatMessages(projectId, "content", (prev) => [
        ...prev,
        { role: "agent", content: "❌ 视觉总监介入失败：" + (err.message || "未知错误") + "\n\n👉 请点击下方「确认内容，请视觉总监 →」按钮重试。", agentRole: "content" },
      ]);
    } finally {
      setConfirmingProjectId(null);
      isConfirmingRef.current = false;
    }
  };

  // 注：原 autoGenerateStyleProposals 已合并到 handleSendChat（聊天路径），
  // 按钮和聊天走同一管道，确保历史调整意见和当前提案锚点不会被丢弃。

  const resolveReferenceImageUrl = (url?: string | null) => {
    if (!url) return "";
    if (/^(https?:|data:|blob:)/i.test(url)) return url;
    return `${API_BASE}${url.startsWith("/") ? url : `/${url}`}`;
  };

  const referenceToChatAttachment = (ref: any): ChatAttachment | null => {
    if (!ref?.id || !ref?.url) return null;
    const basename = String(ref.url).split("?")[0].split("/").pop() || "图片";
    return {
      id: String(ref.id),
      name: ref.asset_name || basename.replace(/^(content_ref|chat_ref|visual_asset|style_ref|logo)_/, ""),
      url: resolveReferenceImageUrl(ref.url),
      role: ref.role,
    };
  };

  const mergeChatAttachments = (...groups: ChatAttachment[][]) => {
    const seen = new Set<string>();
    const merged: ChatAttachment[] = [];
    groups.flat().forEach((item) => {
      if (!item?.id || seen.has(item.id)) return;
      seen.add(item.id);
      merged.push(item);
    });
    return merged;
  };

  const shouldAttachProjectImagesForMessage = (message: string) =>
    /(图|图片|截图|照片|素材|参考图|读图|识图|OCR|ocr|解读|这张|这两张|上传)/.test(message || "");

  const getProjectImageContextAttachments = (message: string, role: "content" | "visual" | "finetune") => {
    if (role === "finetune" || !shouldAttachProjectImagesForMessage(message)) return [];
    const candidates: any[] = [];
    referenceImages.forEach((ref: any) => {
      const analysis = ref.asset_analysis || {};
      if (ref.role === "content_ref" && !ref.slide_id && !analysis.pptx_source_page_num) {
        candidates.push(ref);
      } else if (role === "visual" && ["visual_asset", "style_ref", "logo"].includes(ref.role)) {
        candidates.push(ref);
      }
    });
    if (agentMode === "page" && editingSlide?.reference_images) {
      editingSlide.reference_images.forEach((ref: any) => {
        if (["content_ref", "chart_ref", "visual_asset"].includes(ref.role)) candidates.push(ref);
      });
    }
    return mergeChatAttachments(candidates.map(referenceToChatAttachment).filter(Boolean) as ChatAttachment[]).slice(-8);
  };

  const handleSendChat = async (forcedMsg?: string, baseHistory?: typeof chatMessages, isRetry = false) => {
    if (!selectedProject) return;
    const requestProject = selectedProject;
    const requestProjectId = requestProject.id;
    const requestGate = gateContext.gate;
    const requestGateRevision = gateContext.gateRevision;
    const userMsg = getLatestComposerTextForSubmission(forcedMsg);
    const retryRequestContext = isRetry ? pendingChatRef.current?.requestContext : null;
    const requestContext =
      retryRequestContext ||
      inferAgentRequestContext({
        message: userMsg,
        activeAgentRole: currentAgentRole,
        activeScope: activeAgentScope,
        editingPageNum: editingSlide?.page_num,
        selectedPageNums: selectedPageNumsForAgent,
        projectStatus: requestProject.status,
        slideCount: slides.length,
        contentPlanConfirmed: Boolean(requestProject.content_plan_confirmed),
        hasSelectedStyle: Boolean(requestProject.selected_style),
        hasPrompt: slides.some(slideHasPrompt),
        hasGeneratedImage: slides.some((slide) => Boolean(slide.image_path)),
      });
    const requestAgentRole = (isRetry ? pendingChatRef.current?.agentRole : requestContext.targetRole) || requestContext.targetRole;
    if (!isRetry && requestAgentRole !== currentAgentRole) {
      currentAgentRoleRef.current = requestAgentRole;
      setCurrentAgentRole(requestAgentRole);
    }
    const requestRoleCanPersist = requestAgentRole === "content" || requestAgentRole === "visual";
    const isRequestVisible = () =>
      selectedProjectIdRef.current === requestProjectId && currentAgentRoleRef.current === requestAgentRole;
    const isRequestCurrentGate = () => {
      const latestGate = gateContextRef.current;
      return Boolean(
        isRequestVisible() &&
        latestGate &&
        latestGate.gate === requestGate &&
        latestGate.gateRevision === requestGateRevision
      );
    };
    const appendRequestMessage = (message: ChatMessage, options: { allowStale?: boolean } = {}) => {
      if (!options.allowStale && !isRequestCurrentGate()) return false;
      const normalized = withGateMeta(message);
      if (requestRoleCanPersist) {
        appendProjectChatMessage(requestProjectId, requestAgentRole, normalized);
      } else if (isRequestVisible()) {
        setActiveChatMessages((prev) => [...prev, normalized]);
      }
      return true;
    };
    const hasAttachments = pendingAttachments.length > 0;
    const ambientImageAttachments =
      pendingChatAttachments.length > 0 ? [] : getProjectImageContextAttachments(userMsg, requestAgentRole);
    const chatAttachmentsForRequest = mergeChatAttachments(pendingChatAttachments, ambientImageAttachments);
    const attachmentIdsForRequest =
      chatAttachmentsForRequest.length > 0
        ? chatAttachmentsForRequest.map((item) => item.id)
        : isRetry
        ? pendingChatRef.current?.attachmentIds || []
        : [];
    const hasChatImageAttachments = chatAttachmentsForRequest.length > 0;
    const hasFinetunePendingAttachments =
      requestAgentRole === "finetune" &&
      !!finetuneTargetSlideId &&
      (pendingFinetuneAttachmentsMap[finetuneTargetSlideId] || []).length > 0;
    if (!userMsg && !hasAttachments && !hasChatImageAttachments && !hasFinetunePendingAttachments) return;

    // 构建用户消息展示内容（包含附件引用）
    let displayContent = userMsg;
    if (hasAttachments) {
      const attachmentText = pendingAttachments.map((f) => `📎 ${f}`).join("\n");
      displayContent = userMsg ? `${userMsg}\n\n${attachmentText}` : attachmentText;
    }
    if (!displayContent && hasChatImageAttachments) {
      displayContent = "请识别并解读我上传的图片。";
    }

      const newMessage: ChatMessage = { role: "user" as const, content: displayContent };

      const chatResultLooksValid = (r: unknown): boolean =>
        r != null && typeof r === "object" && !Array.isArray(r);

      const parseStreamedContentFallback = (raw: string): any | null => {
        const text = raw.trim();
        if (!text) return null;

        const unfenced = text
          .replace(/^```(?:json)?\s*/i, "")
          .replace(/```\s*$/i, "")
          .trim();
        const candidates = [unfenced];
        const firstBrace = unfenced.indexOf("{");
        const lastBrace = unfenced.lastIndexOf("}");
        if (firstBrace !== -1 && lastBrace > firstBrace) {
          candidates.push(unfenced.slice(firstBrace, lastBrace + 1));
        }

        for (const candidate of candidates) {
          try {
            const parsed = JSON.parse(candidate);
            if (chatResultLooksValid(parsed)) return parsed;
          } catch {
            // Try the next candidate.
          }
        }

        if (unfenced.startsWith("{") || unfenced.startsWith("[")) {
          return null;
        }
        return {
          action: requestAgentRole === "content" && requestProject.status === "draft" ? "collect_content" : "answer",
          response: unfenced,
        };
      };

    if (requestAgentRole === "finetune") {
      const targetSlideId = finetuneTargetSlideId || editingSlide?.id;
      const targetSlide = slides.find((s) => s.id === targetSlideId);
      if (!targetSlide || !targetSlideId) {
        setActiveChatMessages((prev) => [
          ...prev,
          { role: "agent", content: "请先选择一页要微调的幻灯片。", agentRole: "finetune" },
        ]);
        return;
      }
      if (!userMsg) {
        setActiveChatMessages((prev) => [
          ...prev,
          { role: "agent", content: "请直接写你想怎么改，我会把当前页和参考图一起发给模型。", agentRole: "finetune" },
        ]);
        return;
      }

      const loadingId = `finetune-${Date.now()}`;
      const finetuneAttachments = pendingFinetuneAttachmentsMap[targetSlide.id] || [];
      if (!isRetry) {
        updateRoleChatMessages(requestProjectId, "finetune", (prev) => [
          ...prev,
          { ...newMessage, content: userMsg, attachments: finetuneAttachments },
          {
            role: "agent",
            content: `正在微调第 ${targetSlide.page_num} 页...`,
            agentRole: "finetune",
            loading: true,
            id: loadingId,
          },
        ], targetSlide.id);
        setChatInput("");
        setPendingAttachments([]);
        setPendingFinetuneAttachmentsMap((prev) => {
          const next = { ...prev };
          delete next[targetSlide.id];
          return next;
        });
      }
      setChatLoading(true);
      setThinkingContent("");
      setThinkingExpanded(false);
      setOperatingProjectId(selectedProject.id);
      activeChatProjectIdRef.current = requestProjectId;
      activeChatRoleRef.current = requestAgentRole;
      activeChatGateRef.current = requestGate;
      activeChatGateRevisionRef.current = requestGateRevision;
      chatInProgressRef.current = true;

      try {
        await finetuneSlide(selectedProject.id, targetSlide.id, userMsg, finetuneAttachments.map((a) => a.id));
        await loadSlides(selectedProject.id);
        await pollUntilStatusNotGenerating(selectedProject.id);
        await loadSlideVersions(targetSlide.id);
        const freshSlides = await fetchSlides(selectedProject.id);
        const freshSlide = freshSlides.find((s: Slide) => s.id === targetSlide.id);
        if (freshSlide && selectedProjectIdRef.current === requestProjectId) {
          slidesCacheRef.current[selectedProject.id] = freshSlides;
          setSlidesProjectId(selectedProject.id);
          setSlides(freshSlides);
          hydrateSlideStaleMap(freshSlides);
          if (editingSlide?.id === targetSlide.id) setEditingSlide(freshSlide);
        }
        if (freshSlide?.status === "failed") {
          throw new Error(freshSlide.error_msg || "图像模型未能生成微调版本");
        }
        bumpSlideImageRefresh(targetSlide.id);
        updateRoleChatMessages(requestProjectId, "finetune", (prev) => [
          ...prev.filter((m) => m.id !== loadingId),
          {
            role: "agent",
            content: `已生成第 ${targetSlide.page_num} 页的微调版本。当前页原图已自动存入版本历史，可随时回退。`,
            agentRole: "finetune",
          },
        ], targetSlide.id);
      } catch (err: any) {
        updateRoleChatMessages(requestProjectId, "finetune", (prev) => [
          ...prev.filter((m) => m.id !== loadingId),
          {
            role: "agent",
            content: `微调失败：${err.message || "未知错误"}`,
            agentRole: "finetune",
          },
        ], targetSlide.id);
      } finally {
        setOperatingProjectId(null);
        if (selectedProjectIdRef.current === requestProjectId) setChatLoading(false);
        chatInProgressRef.current = false;
        activeChatProjectIdRef.current = null;
        activeChatRoleRef.current = null;
        activeChatGateRef.current = null;
        activeChatGateRevisionRef.current = null;
      }
      return;
    }

    // 重试时不重复添加用户消息
    if (!isRetry) {
      appendRequestMessage(
        { ...newMessage, attachments: chatAttachmentsForRequest, agentRole: requestAgentRole },
        { allowStale: true }
      );
      setChatInput("");
      setPendingAttachments([]);
      setPendingChatAttachments([]);
    }
    setChatLoading(true);
    setThinkingContent("");
    setThinkingExpanded(false);
    // 锁定当前流所属的项目和角色，防止状态跳到别的窗口
    activeChatProjectIdRef.current = requestProjectId;
    activeChatRoleRef.current = requestAgentRole;
    activeChatGateRef.current = requestGate;
    activeChatGateRevisionRef.current = requestGateRevision;
    chatInProgressRef.current = true;
    lastChatEventAtRef.current = Date.now();

    // 创建 AbortController 用于停止输出
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    // 使用 baseHistory（编辑消息时传入）或当前 chatMessages，确保包含最新用户消息
    const requestRoleMessages =
      requestAgentRole === "content"
        ? contentChatHistory
        : requestAgentRole === "visual"
        ? visualChatHistory
        : chatMessages;
    const msgList = baseHistory || requestRoleMessages;
    // 重试时 baseHistory 已包含用户消息，避免重复添加
    const history = (isRetry ? msgList : [...msgList, newMessage]).map((m) => ({
      role: m.role === "agent" ? "assistant" : m.role,
      content: m.content,
    }));
    let result: any = null;
    let streamedContent = "";

    try {

      // 保存请求参数，用于切回来后自动恢复
      const retryCountForRequest = isRetry ? (pendingChatRef.current?.retryCount || 0) + 1 : 0;
      setPendingChatRequest({
        projectId: requestProjectId,
        message: userMsg,
        history: [...history],
        pageContext: undefined as any,
        agentRole: requestAgentRole,
        requestContext,
        attachmentIds: attachmentIdsForRequest,
        retryCount: retryCountForRequest,
        createdAt: pendingChatRef.current?.createdAt || Date.now(),
      });

      // 根据本轮自然语言推断出的作用范围构建 pageContext。
      let pageContext: any = undefined;
      const summarizeSlideForAgent = (s: Slide) => {
        const tc = s.content_json?.text_content || {};
        return {
          page_num: s.page_num,
          type: s.type,
          headline: tc.headline || "",
          subhead: tc.subhead || "",
          body_preview: typeof tc.body === "string"
            ? tc.body.split("\n").filter(Boolean).slice(0, 2)
            : (tc.body || []).slice(0, 2).map((item: any) =>
                typeof item === "string" ? item : item?.content || ""
              ),
        };
      };
      const requestTargetSlide =
        requestContext.scope === "current_slide"
          ? slides.find((s) => requestContext.pageNums.includes(s.page_num)) || editingSlide
          : null;
      if (requestContext.scope === "current_slide" && requestTargetSlide) {
        const otherPages = slides
          .filter((s) => s.id !== requestTargetSlide.id)
          .map((s) => {
            const summary = summarizeSlideForAgent(s);
            return {
              ...summary,
              body_preview: Array.isArray(summary.body_preview)
                ? summary.body_preview.join("\n")
                : summary.body_preview,
            };
          });
        pageContext = {
          mode: "page",
          scope: requestContext.scope,
          target_page_nums: [requestTargetSlide.page_num],
          current_page: {
            page_num: requestTargetSlide.page_num,
            slide_id: requestTargetSlide.id,
            type: requestTargetSlide.type,
            content_json: requestTargetSlide.content_json,
            visual_json: requestTargetSlide.visual_json,
            prompt_text: requestTargetSlide.prompt_text,
            reference_images: dedupeReferenceImages(requestTargetSlide.reference_images || []),
            pending_state: staleMap[requestTargetSlide.id] || null,
          },
          other_pages: otherPages,
        };
      } else if (slides.length > 0) {
        const targetPageSet = new Set(requestContext.pageNums);
        const scopedSlides =
          requestContext.scope === "selected_slides" && targetPageSet.size > 0
            ? slides.filter((s) => targetPageSet.has(s.page_num))
            : slides;
        pageContext = {
          mode: "global",
          scope: requestContext.scope,
          target_page_nums: requestContext.pageNums,
          slides: scopedSlides.map(summarizeSlideForAgent),
        };
      }

      // 更新 pendingChatRef 中的 pageContext
      if (pendingChatRef.current) {
        setPendingChatRequest({ ...pendingChatRef.current, pageContext });
      }
      const effectivePageContext = withCrossStageContext(pageContext, requestAgentRole);
      if (pendingChatRef.current) {
        setPendingChatRequest({ ...pendingChatRef.current, pageContext: effectivePageContext });
      }

      // 用于标记是否因可重试的流中断而跳出循环
      let streamRetryReason: string | null = null;

      for await (const event of chatWithAgentStream(
        requestProjectId,
        userMsg || "请识别并解读我上传的图片。",
        history,
        ctrl.signal,
        effectivePageContext,
        requestAgentRole,
        attachmentIdsForRequest
      )) {
        lastChatEventAtRef.current = Date.now();
        if (event.type === "thinking") {
          if (isRequestVisible()) {
            setThinkingContent((prev) => prev + event.delta);
          }
        } else if (event.type === "result") {
          result = event.data;
          if (import.meta.env.DEV) {
            console.debug("[handleSendChat] received result:", result);
          }
        } else if (event.type === "content") {
          streamedContent += event.delta || "";
        } else if (event.type === "error") {
          const msg = event.message || "";
          // 对于流中断类错误（网络波动、浏览器意外关闭流），自动重试一次
          const isRetryable = msg.includes("中断") || msg.includes("aborted") || msg.includes("连接") || msg.includes("网络") || msg.includes("stream");
          if (isRetryable && !streamRetryReason) {
            streamRetryReason = msg;
            break; // 跳出循环，由外层 retry 逻辑处理
          }
          if (!isRequestCurrentGate()) {
            clearPendingChatRequest(requestProjectId);
            if (isRequestVisible()) setChatLoading(false);
            abortRef.current = null;
            return;
          }
          appendRequestMessage({ role: "agent", content: `❌ ${msg || "请求出错"}`, agentRole: requestAgentRole });
          if (isRequestVisible()) setChatLoading(false);
          abortRef.current = null;
          return;
        }
      }

      if (import.meta.env.DEV) {
        console.debug("[handleSendChat] stream ended, result=", result, "aborted=", ctrl.signal.aborted, "retryReason=", streamRetryReason);
      }

      // 如果流被浏览器自动中断（切标签页导致），静默标记为重试，不提示用户
      if (ctrl.signal.aborted) {
        streamRetryReason = "连接被浏览器中断";
      }

      if ((!chatResultLooksValid(result) || streamRetryReason) && !ctrl.signal.aborted) {
        if (!isRequestCurrentGate()) {
          clearPendingChatRequest(requestProjectId);
          if (isRequestVisible()) setChatLoading(false);
          return;
        }
        if (!streamRetryReason) {
          appendRequestMessage({ role: "system", content: "🔄 响应不完整，正在自动重试..." });
        }
        const retryCtrl = new AbortController();
        abortRef.current = retryCtrl;
        try {
          for await (const event of chatWithAgentStream(
            requestProjectId,
            userMsg || "请识别并解读我上传的图片。",
            history,
            retryCtrl.signal,
            effectivePageContext,
            requestAgentRole,
            attachmentIdsForRequest
          )) {
            lastChatEventAtRef.current = Date.now();
            if (event.type === "result") {
              result = event.data;
            } else if (event.type === "content") {
              streamedContent += event.delta || "";
            } else if (event.type === "error") {
              if (!isRequestCurrentGate()) {
                clearPendingChatRequest(requestProjectId);
                if (isRequestVisible()) setChatLoading(false);
                abortRef.current = null;
                return;
              }
              appendRequestMessage({ role: "agent", content: `❌ ${event.message || "请求出错"}`, agentRole: requestAgentRole });
              if (isRequestVisible()) setChatLoading(false);
              abortRef.current = null;
              return;
            }
          }
        } catch {
          // 只要不是用户主动停止，任何异常都要给用户反馈
          if (!retryCtrl.signal.aborted && isRequestCurrentGate()) {
            appendRequestMessage({ role: "agent", content: "请求失败，请重试。", agentRole: requestAgentRole });
          } else if (!isRequestCurrentGate()) {
            clearPendingChatRequest(requestProjectId);
          }
        } finally {
          abortRef.current = null;
        }
      }

      if (!chatResultLooksValid(result)) {
        const fallbackResult = parseStreamedContentFallback(streamedContent);
        if (chatResultLooksValid(fallbackResult)) {
          result = fallbackResult;
        }
      }

      if (!chatResultLooksValid(result)) {
        if (!isRequestCurrentGate()) {
          clearPendingChatRequest(requestProjectId);
          if (isRequestVisible()) setChatLoading(false);
          return;
        }
        appendRequestMessage({ role: "agent", content: "⚠️ 响应未返回完整结果，请重试一次。", agentRole: requestAgentRole });
        clearPendingChatRequest(requestProjectId);
        if (isRequestVisible()) setChatLoading(false);
        return;
      }

      // 如果重试流被用户主动中断，不继续处理
      if (abortRef.current?.signal?.aborted) return;

      const action = result.action;
      const hasPageTarget = Boolean(result.page_nums?.length || editingSlide);
      if (!isRequestCurrentGate()) {
        clearPendingChatRequest(requestProjectId);
        return;
      }
      const frontendWillHandleAgentReply = isRequestVisible() && (
        (action === "confirm_style" && result.style) ||
        (action === "regenerate_pages" && result.page_nums?.length > 0) ||
        action === "retry_failed" ||
        (action === "reroll_page_visual_plan" && hasPageTarget) ||
        (action === "update_slide_visual" && result.updated_visual) ||
        (action === "update_all_slides_visual" && result.updated_slides_visual?.length > 0) ||
        action === "request_generate_image" ||
        (action === "forward_to_visual" && currentAgentRole === "content") ||
        (action === "forward_to_content" && currentAgentRole === "visual") ||
        (action === "regenerate_plan" && result.topic) ||
        ((action === "propose_styles" || action === "adjust_style") && currentAgentRole === "visual" && selectedProject) ||
        (action === "update_slide_content" && result.updated_content) ||
        (action === "update_all_slides" && result.updated_slides?.length > 0) ||
        (action === "add_slide_before" && result.new_slide) ||
        (action === "add_slide_after" && result.new_slide)
      );
      if (!frontendWillHandleAgentReply) {
        const agentReply = result.response || result.message || "...";
        appendRequestMessage({
          role: "agent",
          content: agentReply,
          action: result.action,
          positioning: result.positioning,
          topic: result.topic,
          nextAction: result.next_action,
          agentRole: requestAgentRole,
        });
      }

      // 如果用户已经切到别的项目/Agent，或回退导致 gateRevision 变化，
      // 后续流程副作用必须停止，避免旧 Agent 响应覆盖新 Gate 状态。
      if (!isRequestCurrentGate()) {
        return;
      }

      // 如果项目还是默认名，Agent 已经推断出主题，自动重命名
      const requestProjectTitle = (requestProject.title || "").trim();
      if (result.title && (!requestProjectTitle || requestProjectTitle.startsWith("未命名项目"))) {
        try {
          await updateProject(requestProjectId, { title: result.title });
          await loadProjects();
        } catch (e) {
          console.warn("Auto-rename after chat error:", e);
        }
      }

        // Agent 在聊天中确认风格，自动保存并推进
        if (result.action === "confirm_style" && result.style) {
          await dispatchGateAction("confirm_style", { style: result.style }, { allowWhileChatLoading: true, source: "agent" });
        }
  
        // Agent 要求重新生成指定页
        if (result.action === "regenerate_pages" && result.page_nums?.length > 0) {
          const regenPageNums = result.page_nums?.length ? result.page_nums : requestContext.pageNums;
          const targetSlides = slides.filter((s) => regenPageNums.includes(s.page_num));
          targetSlides.forEach((s) => markSlideStale(s.id, "image"));
          setActiveChatMessages((prev) => [
            ...prev,
            {
              role: "agent",
              content: `已标记第 ${regenPageNums.join(", ")} 页为「需重新生成图片」。\n\n这一步会产生生图成本，请进入对应页面检查后点击「确认生成图片」。`,
              agentRole: requestAgentRole,
            },
          ]);
        }
  
        // Agent 要求重试所有失败页
        if (result.action === "retry_failed") {
          const failed = slides.filter((s) => s.status === "failed");
          failed.forEach((s) => markSlideStale(s.id, "image"));
          setActiveChatMessages((prev) => [
            ...prev,
            {
              role: "agent",
              content: failed.length
                ? `已找到 ${failed.length} 个失败页面。重试会重新生图并产生成本，请点击「一键重试失败页」或进入单页确认。`
                : "当前没有失败页面需要重试。",
              agentRole: requestAgentRole,
            },
          ]);
        }
  
        // Agent 要求重新抽一版当前/指定页面画面方案（不生图）
        if (result.action === "reroll_page_visual_plan") {
          const pageNums = result.page_nums?.length
            ? result.page_nums
            : requestContext.pageNums.length
            ? requestContext.pageNums
            : editingSlide
            ? [editingSlide.page_num]
            : [];
          const targetIds = slides
            .filter((s) => pageNums.includes(s.page_num))
            .map((s) => s.id);
          if (targetIds.length > 0) {
            await handleUpdateStaleSlides(targetIds, { local: true });
            appendRequestMessage({
              role: "agent",
              content: `已为第 ${pageNums.join(", ")} 页再生成一版画面方案。请检查后再决定是否生成图片。`,
              agentRole: "visual",
            });
          } else {
            appendRequestMessage({
              role: "agent",
              content: pageNums.length
                ? `没有找到第 ${pageNums.join(", ")} 页，未生成新的画面方案。请先确认页码。`
                : "请先选择要重做画面方案的页面。",
              agentRole: "visual",
            });
          }
        }
  
        // Agent 精确修改单页视觉描述
        if (result.action === "update_slide_visual" && result.updated_visual) {
          let pageNum = result.updated_visual.page_num;
          if (requestContext.scope === "current_slide" && requestContext.pageNums[0]) {
            pageNum = requestContext.pageNums[0];
          }
          const targetSlide = slides.find((s) => s.page_num === pageNum);
          if (targetSlide) {
            appendRequestMessage({ role: "agent", content: "正在应用视觉描述修改...", agentRole: "visual" });
            try {
              await updateVisualPlan(selectedProject.id, pageNum, result.updated_visual.visual_json, targetSlide.id);
              markSlideStale(targetSlide.id, "visual");
              await loadSlides(selectedProject.id);
              if (editingSlide && editingSlide.page_num === pageNum) {
                const updated = await fetchSlides(selectedProject.id);
                const freshSlide = updated.find((s: Slide) => s.page_num === pageNum);
                if (freshSlide) setEditingSlide(freshSlide);
              }
              await handleUpdateStaleSlides([targetSlide.id], { local: true });
              appendRequestMessage({
                role: "agent",
                content: `✅ 已更新第 ${pageNum} 页的视觉描述。图片不会自动重生成，请检查后再手动确认生成。`,
                agentRole: "visual",
              });
            } catch (err: any) {
              appendRequestMessage({
                role: "agent",
                content: "应用视觉描述修改失败：" + (err.message || "未知错误"),
                agentRole: "visual",
              });
            }
          } else {
            appendRequestMessage({
              role: "agent",
              content: `没有找到第 ${pageNum || "?"} 页，未应用视觉修改。请先确认页码。`,
              agentRole: "visual",
            });
          }
        }
  
        // Agent 全局修改多页视觉描述
        if (result.action === "update_all_slides_visual" && result.updated_slides_visual?.length > 0) {
          appendRequestMessage({ role: "agent", content: `正在应用 ${result.updated_slides_visual.length} 页的视觉描述修改...`, agentRole: "visual" });
          const existingPageNums = new Set(slides.map((s) => s.page_num));
          const skipped: number[] = [];
          const updatedPageNums: number[] = [];
          const updatedSlideIds: string[] = [];
          for (const patch of result.updated_slides_visual) {
            const pageNum = patch.page_num;
            if (!existingPageNums.has(pageNum)) {
              skipped.push(pageNum);
              continue;
            }
            const slide = slides.find((s) => s.page_num === pageNum);
            if (!slide) continue;
            try {
              await updateVisualPlan(selectedProject.id, pageNum, patch.visual_json, slide.id);
              markSlideStale(slide.id, "visual");
              updatedPageNums.push(pageNum);
              updatedSlideIds.push(slide.id);
            } catch {
              // 单页失败继续下一页
            }
          }
          await loadSlides(selectedProject.id);
          if (editingSlide && updatedPageNums.includes(editingSlide.page_num)) {
            const updated = await fetchSlides(selectedProject.id);
            const freshSlide = updated.find((s: Slide) => s.page_num === editingSlide.page_num);
            if (freshSlide) setEditingSlide(freshSlide);
          }
          if (updatedSlideIds.length > 0) {
            await handleUpdateStaleSlides(updatedSlideIds, { local: true });
            appendRequestMessage({ role: "agent", content: `已更新第 ${updatedPageNums.join(", ")} 页的画面描述。图片不会自动重生成，请检查后再手动确认生成。`, agentRole: "visual" });
          } else {
            appendRequestMessage({
              role: "agent",
              content: skipped.length > 0
                ? `没有找到第 ${skipped.join(", ")} 页，未应用视觉修改。请先确认页码。`
                : "没有可应用的视觉修改，页面未变化。",
              agentRole: "visual",
            });
            return;
          }
          let msg = `✅ 已更新第 ${updatedPageNums.join(", ")} 页的视觉描述。`;
          if (skipped.length > 0) msg += `（跳过不存在的页：第 ${skipped.join(", ")} 页）`;
          appendRequestMessage({ role: "agent", content: msg, agentRole: "visual" });
        }
  
        // Agent 理解用户想生图，但成本动作必须由用户确认
        if (result.action === "request_generate_image") {
          const pageNums = result.page_nums?.length
            ? result.page_nums
            : requestContext.pageNums.length
            ? requestContext.pageNums
            : editingSlide
            ? [editingSlide.page_num]
            : [];
          const targetSlides = slides.filter((s) => pageNums.includes(s.page_num));
          targetSlides.forEach((s) => markSlideStale(s.id, "image"));
          appendRequestMessage({
            role: "agent",
            content: pageNums.length
              ? `可以生成第 ${pageNums.join(", ")} 页图片，但这会产生生图成本。请在单页中点击「确认生成图片」。`
              : "可以生成图片，但这会产生生图成本。请先选择具体页面，并在单页中点击「确认生成图片」。",
            agentRole: "visual",
          });
        }
  
        // 内容总监识别到内容已确认，自动转接视觉总监
        if (result.action === "forward_to_visual" && requestAgentRole === "content") {
          appendRequestMessage({
            role: "agent",
            content: "收到，正在请视觉总监介入。",
            agentRole: "content",
          });
          await dispatchGateAction("switch_to_visual", undefined, { allowWhileChatLoading: true, source: "agent" });
          return;
        }
  
        // 视觉总监识别到内容问题，自动转接内容总监
        if (result.action === "forward_to_content" && requestAgentRole === "visual") {
          setCurrentAgentRole("content");
          appendProjectChatMessage(requestProjectId, "content", {
            role: "agent",
            content: result.response || "已为你转接内容总监，可以继续沟通内容相关的问题。",
            agentRole: "content",
          });
          return;
        }
  
        // Agent 要求重新生成内容规划（页数可能变化）
        if (result.action === "regenerate_plan" && result.topic) {
          if (requestAgentRole === "visual") {
            appendRequestMessage({
              role: "agent",
              content: "我是视觉总监，负责设计风格和画面效果。如果你想调整内容规划，请切换到内容总监继续。",
              agentRole: "visual",
            });
          } else {
            appendRequestMessage({
              role: "agent",
              content: result.page_count
                ? `收到，正在按 ${result.page_count} 页重新生成内容规划。`
                : "收到，正在把这条反馈落实到内容规划里重新生成。",
              agentRole: "content",
            });
            await dispatchGateAction(
              "generate_content_plan",
              { topic: result.topic, page_count: result.page_count, attachment_ids: attachmentIdsForRequest },
              { allowWhileChatLoading: true, source: "agent" }
            );
          }
        }
  
        // 视觉总监确认素材状态，触发风格提案生成；已进入后续阶段时，风格调整必须仍然落成可确认卡片。
        if ((result.action === "propose_styles" || result.action === "adjust_style") && requestAgentRole === "visual" && selectedProject) {
        const latestGateContext = gateContextRef.current || gateContext;
        const isAdjust = result.action === "adjust_style";
        const isBackendStyleGenerationRequest = isVisualStyleGenerationMessage(userMsg);
        const styleGenerationContext = buildVisualStyleGenerationContext(
          history,
          userMsg,
          buildCrossStageContext("visual")
        );
        const canStartBackendStyleProposal = latestGateContext.allowedActions.includes("generate_style_proposals");
        const fallbackBaseStyle =
          selectedProject.selected_style ||
          selectedProject.style_proposal?.proposals?.[0] ||
          styleProposalsInChat[0] ||
          null;
        const proposalFromAgent = result.style_proposal && typeof result.style_proposal === "object"
          ? result.style_proposal
          : null;
        const fallbackProposal = !isBackendStyleGenerationRequest && !canStartBackendStyleProposal && !proposalFromAgent && fallbackBaseStyle
          ? buildFallbackStyleAdjustment(fallbackBaseStyle, userMsg, result.response || "")
          : null;
        const proposalToShow = proposalFromAgent || fallbackProposal;

        if (!proposalToShow && !canStartBackendStyleProposal) {
          appendRequestMessage({
            role: "agent",
            content: "我理解你的风格调整方向，但当前步骤没有拿到可选择的新风格卡片。请再发一次具体方向，例如「更强科技感、深色底、去掉低幼感」，我会生成可确认的调整后方案。",
            agentRole: "visual",
          });
          return;
        }

        // 优先使用 Agent 聊天返回的实时风格提案；若缺少结构化结果，则用当前风格生成一个可确认的调整卡片。
        if (proposalToShow) {
          const proposal = proposalToShow;
          // 标准化 palette 格式
          if (proposal.palette && Array.isArray(proposal.palette)) {
            proposal.palette = proposal.palette.map((c: any) => {
              if (!c) return { name: "未知", hex: "#cccccc", role: "" };
              if (typeof c === "string") return { name: stripHexCodes(c) || c, hex: proposalColorValue(c), role: "" };
              return c;
            });
          }
          if (selectedProjectIdRef.current === requestProjectId) {
            setStyleProposalsInChat([proposal]);
          }
          updateProjectChatMessages(requestProjectId, "visual", (prev) => [
            ...prev,
            {
              role: "agent",
              content: isAdjust
                ? "✅ 已根据你的反馈生成调整后方案，请查看本条消息下方卡片。\n\n👉 满意请点「选择此方案」，我会保存新风格并重新生成画面描述；不满意继续告诉我哪里要再改。"
                : "✅ 风格提案已生成，请查看本条消息下方卡片。\n\n👉 如果满意请点击「选择此方案」；如果想调整，直接告诉我（如「更商务一点」「配色再暖一些」）。",
              agentRole: "visual",
              hasStyleProposal: true,
              styleProposals: [proposal],
              gate: gateContext.gate,
              gateRevision: gateContext.gateRevision,
            },
          ]);
        } else {
          // Agent 没有返回结构化提案，回退到后端生成
          showToast("正在生成风格提案...", "info");
          setOperatingProjectId(requestProjectId);
          const styleLoadingId = `sp-${Date.now()}`;
          updateProjectChatMessages(requestProjectId, "visual", (prev) => [
            ...prev,
            { role: "agent", content: "⏳ 正在生成风格提案，请稍候...", agentRole: "visual", loading: true, id: styleLoadingId },
          ]);
          let styleRunId: string | null = null;
          try {
            const freshReferenceImages = await fetchReferenceImages(requestProjectId);
            if (!isRequestCurrentGate()) {
              clearPendingChatRequest(requestProjectId);
              return;
            }
            const shouldForceStyleProposal = isBackendStyleGenerationRequest || isAdjust || freshReferenceImages.length > 0 || Boolean(styleGenerationContext);
            const styleResult = await generateStyleProposals(requestProjectId, shouldForceStyleProposal, styleGenerationContext);
            styleRunId = styleResult?.run?.id || null;
            if (styleRunId) {
              locallyHandledRunIdsRef.current.add(styleRunId);
              updateProjectChatMessages(requestProjectId, "visual", (prev) =>
                prev.map((m) =>
                  m.id === styleLoadingId
                    ? { ...m, runId: styleRunId || undefined, content: runProgressText(styleResult.run) }
                    : m
                )
              );
            }
            if (!isRequestCurrentGate()) {
              clearPendingChatRequest(requestProjectId);
              return;
            }
            if (styleResult.status === "generating") {
              showToast("风格提案后台生成中，请稍候...", "info");
              await pollForStyleProposals(requestProjectId);
              if (!isRequestCurrentGate()) {
                clearPendingChatRequest(requestProjectId);
                return;
              }
            } else if (styleResult.status === "completed" && styleResult.proposals) {
              showToast("风格提案已就绪", "success");
            }
            await loadProjects();
            const fresh = normalizeProjectsForActiveSelection(await fetchProjects(), selectedProjectIdRef.current);
            if (!isRequestCurrentGate()) {
              clearPendingChatRequest(requestProjectId);
              return;
            }
            const updated = fresh.find((p: Project) => p.id === requestProjectId);
            if (updated && selectedProjectIdRef.current === requestProjectId) setSelectedProject(clearProjectNotification(updated));
            // 尝试从项目状态中提取风格提案，确保聊天卡片能正确展示
            const proposals = updated?.style_proposal?.proposals || [];
            if (proposals.length > 0) {
              if (selectedProjectIdRef.current === requestProjectId) {
                setStyleProposalsInChat(proposals);
              }
              updateProjectChatMessages(requestProjectId, "visual", (prev) => [
                ...prev.filter((m) => m.id !== styleLoadingId),
                {
                  role: "agent",
                  content: isAdjust
                    ? "✅ 已根据你的反馈调整了方案，请查看下方新卡片。\n\n👉 满意请点「选择此方案」，不满意继续告诉我哪里要再改。"
                    : "✅ 风格提案已生成，请查看下方卡片。\n\n👉 从三套方案中选择最喜欢的一套，或直接告诉我你的偏好，我会进一步调整。",
                  agentRole: "visual",
                  hasStyleProposal: true,
                  styleProposals: proposals,
                  gate: gateContext.gate,
                  gateRevision: gateContext.gateRevision,
                },
              ]);
            } else {
              updateProjectChatMessages(requestProjectId, "visual", (prev) => [
                ...prev.filter((m) => m.id !== styleLoadingId),
                {
                  role: "agent",
                  content:
                    "✅ 风格提案已生成，请查看作品画布。\n\n👉 下一步：从三套方案中选择最喜欢的一套，或直接告诉我你的偏好，我会进一步调整。",
                  agentRole: "visual",
                },
                      ]);
                    }
                  } catch (err: any) {
                    if (styleRunId) {
                      locallyHandledRunIdsRef.current.delete(styleRunId);
                    }
                    if (!isRequestCurrentGate()) {
                      clearPendingChatRequest(requestProjectId);
                      return;
                    }
            const errorMessage = await resolveWorkflowFailureMessage(
              requestProjectId,
              "style_proposal",
              userFacingGenerationError(err?.message)
            );
            showToast("风格提案生成失败：" + errorMessage, "error");
            updateProjectChatMessages(requestProjectId, "visual", (prev) => [
              ...prev.filter((m) => m.id !== styleLoadingId),
              {
                role: "agent",
                content: "❌ 风格提案生成失败：" + errorMessage + "\n\n👉 请重试生成，或告诉我你想要的风格方向，我可以直接帮你选择。",
                agentRole: "visual",
              },
            ]);
          } finally {
            setOperatingProjectId(null);
          }
        }
      }

      // Agent 要求修改某一页的文字内容
      if (result.action === "update_slide_content" && result.updated_content) {
        pushSlidesHistory(slides);
        setActiveChatMessages((prev) => [
          ...prev,
          { role: "agent", content: "正在应用内容修改..." },
        ]);
        setOperatingProjectId(selectedProject.id);
        try {
            // 单页模式下强制校正 page_num，防止 LLM 改错页
            let pageNum = result.updated_content.page_num;
            if (requestContext.scope === "current_slide" && requestContext.pageNums[0]) {
              pageNum = requestContext.pageNums[0];
              result.updated_content.page_num = pageNum;
            }
          if (!slides.some((s) => s.page_num === pageNum)) {
            throw new Error(`没有找到第 ${pageNum || "?"} 页，未应用内容修改`);
          }
          await updateSlideContent(selectedProject.id, pageNum, result.updated_content);
          const changedSlide = slides.find((s) => s.page_num === pageNum);
          if (changedSlide) markSlideStale(changedSlide.id, "content");
          await loadProjects();
          await loadSlides(selectedProject.id);
          // 如果当前正在编辑这页，同步更新 editingSlide
          if (editingSlide && editingSlide.page_num === pageNum) {
            const updated = await fetchSlides(selectedProject.id);
            const freshSlide = updated.find((s: Slide) => s.page_num === pageNum);
            if (freshSlide) {
              setEditingSlide(freshSlide);
            }
          }
          setActiveChatMessages((prev) => [
            ...prev,
            { role: "agent", content: `✅ 已更新第 ${pageNum} 页内容。` },
          ]);
          // 内容更新后标记页面需重新设计视觉方案，但不自动触发图片生成
          if (changedSlide) {
            setActiveChatMessages((prev) => [
              ...prev,
              {
                role: "agent",
                content: `📝 第 ${pageNum} 页内容已更新，视觉方案可能需要调整。请切到「视觉总监」重新设计画面后再生成图片。`,
              },
            ]);
          }
        } catch (err: any) {
          setActiveChatMessages((prev) => [
            ...prev,
            { role: "agent", content: "应用修改失败：" + (err.message || "未知错误") },
          ]);
        } finally {
          setOperatingProjectId(null);
        }
      }

      // Agent 要求全局修改多页文字内容
      if (result.action === "update_all_slides" && result.updated_slides?.length > 0) {
        pushSlidesHistory(slides);
        setActiveChatMessages((prev) => [
          ...prev,
          { role: "agent", content: `正在应用 ${result.updated_slides.length} 页的内容修改...` },
        ]);
        setOperatingProjectId(selectedProject.id);
        try {
          const existingPageNums = new Set(slides.map((s) => s.page_num));
          const skipped: number[] = [];
          const updatedPageNums: number[] = [];
          for (const slidePatch of result.updated_slides) {
            const pageNum = slidePatch.page_num;
            if (!existingPageNums.has(pageNum)) {
              skipped.push(pageNum);
              continue;
            }
            await updateSlideContent(selectedProject.id, pageNum, slidePatch);
            const changedSlide = slides.find((s) => s.page_num === pageNum);
            if (changedSlide) markSlideStale(changedSlide.id, "content");
            updatedPageNums.push(pageNum);
          }
          await loadProjects();
          await loadSlides(selectedProject.id);
          // 如果当前正在编辑的页被修改了，同步更新 editingSlide
          if (editingSlide && updatedPageNums.includes(editingSlide.page_num)) {
            const updated = await fetchSlides(selectedProject.id);
            const freshSlide = updated.find((s: Slide) => s.page_num === editingSlide.page_num);
            if (freshSlide) {
              setEditingSlide(freshSlide);
            }
          }
          let msg = `✅ 已更新第 ${updatedPageNums.join(", ")} 页内容。`;
          if (updatedPageNums.length === 0) {
            msg = "没有找到可更新的页面，内容未变化。";
          }
          if (skipped.length > 0) {
            msg += `\n⚠️ 跳过不存在的页面：第 ${skipped.join(", ")} 页（项目当前共 ${slides.length} 页）。`;
          }
          setActiveChatMessages((prev) => [...prev, { role: "agent", content: msg }]);
          // 内容更新后，标记相关页面需要重新设计视觉方案，但不自动触发图片生成
          if (updatedPageNums.length > 0) {
            setActiveChatMessages((prev) => [
              ...prev,
              { role: "agent", content: `📝 内容已更新，相关页面的视觉方案可能需要调整。请确认内容后，切到「视觉总监」重新设计画面，再生成图片。` },
            ]);
          }
        } catch (err: any) {
          setActiveChatMessages((prev) => [
            ...prev,
            { role: "agent", content: "应用修改失败：" + (err.message || "未知错误") },
          ]);
        } finally {
          setOperatingProjectId(null);
        }
      }

      // Agent 要求在当前页前面插入新页
      if (result.action === "add_slide_before" && result.new_slide) {
        pushSlidesHistory(slides);
        setActiveChatMessages((prev) => [
          ...prev,
          { role: "agent", content: "正在插入新页面..." },
        ]);
        setOperatingProjectId(selectedProject.id);
        try {
            let pageNum = result.new_slide.page_num;
            if (requestContext.scope === "current_slide" && requestContext.pageNums[0]) {
              pageNum = requestContext.pageNums[0];
              result.new_slide.page_num = pageNum;
            }
          await createSlide(selectedProject.id, pageNum, result.new_slide);
          await loadProjects();
          await loadSlides(selectedProject.id);
          // 同步更新 editingSlide（如果当前正在编辑，page_num 可能变了）
          if (editingSlide) {
            const updated = await fetchSlides(selectedProject.id);
            const freshSlide = updated.find((s: Slide) => s.id === editingSlide.id);
            if (freshSlide) {
              setEditingSlide(freshSlide);
            }
          }
          setActiveChatMessages((prev) => [
            ...prev,
            { role: "agent", content: `✅ 已在第 ${pageNum} 页前插入新页。` },
          ]);
        } catch (err: any) {
          setActiveChatMessages((prev) => [
            ...prev,
            { role: "agent", content: "插入页面失败：" + (err.message || "未知错误") },
          ]);
        } finally {
          setOperatingProjectId(null);
        }
      }

      // Agent 要求在当前页后面插入新页
      if (result.action === "add_slide_after" && result.new_slide) {
        pushSlidesHistory(slides);
        setActiveChatMessages((prev) => [
          ...prev,
          { role: "agent", content: "正在插入新页面..." },
        ]);
        setOperatingProjectId(selectedProject.id);
        try {
            let pageNum = result.new_slide.page_num;
            if (requestContext.scope === "current_slide" && requestContext.pageNums[0]) {
              pageNum = requestContext.pageNums[0] + 1;
              result.new_slide.page_num = pageNum;
            }
          await createSlide(selectedProject.id, pageNum, result.new_slide);
          await loadProjects();
          await loadSlides(selectedProject.id);
          // 同步更新 editingSlide
          if (editingSlide) {
            const updated = await fetchSlides(selectedProject.id);
            const freshSlide = updated.find((s: Slide) => s.id === editingSlide.id);
            if (freshSlide) {
              setEditingSlide(freshSlide);
            }
          }
          setActiveChatMessages((prev) => [
            ...prev,
            { role: "agent", content: `✅ 已在第 ${pageNum} 页后插入新页。` },
          ]);
        } catch (err: any) {
          setActiveChatMessages((prev) => [
            ...prev,
            { role: "agent", content: "插入页面失败：" + (err.message || "未知错误") },
          ]);
        } finally {
          setOperatingProjectId(null);
        }
      }
    } catch (err: any) {
      // 用户主动停止才显示停止提示；网络中断会自动重试。
      if (err?.name === "AbortError") {
        if (!silentChatAbortRef.current && isRequestCurrentGate()) {
          appendRequestMessage({
            role: "agent",
            content: "⏹ 已停止生成。",
            agentRole: requestAgentRole,
          });
        } else if (!isRequestCurrentGate()) {
          clearPendingChatRequest(requestProjectId);
        }
      } else if (isRequestCurrentGate()) {
        const retryCount = pendingChatRef.current?.retryCount || 0;
        if (retryCount < 2) {
          setChatLoading(true);
          setTimeout(() => {
            const pending = pendingChatRef.current;
            if (!pending) return;
            if (selectedProjectIdRef.current !== pending.projectId || currentAgentRoleRef.current !== pending.agentRole) return;
            handleSendChat(pending.message, pending.history as any, true);
          }, 350);
        } else {
          appendRequestMessage({
            role: "agent",
            content: "请求中断，没有完成这次操作。请再发送一次，或刷新页面后重试。",
            agentRole: requestAgentRole,
          });
          clearPendingChatRequest(requestProjectId);
        }
      }
      // 网络中断会自动重试；重试耗尽后必须给用户可见反馈。
    } finally {
      abortRef.current = null;
      silentChatAbortRef.current = false;
      chatInProgressRef.current = false;
      activeChatProjectIdRef.current = null;
      activeChatRoleRef.current = null;
      activeChatGateRef.current = null;
      activeChatGateRevisionRef.current = null;
      // 只有这条流仍属于当前窗口时才重置 loading，防止切走后状态被覆盖
      if (isRequestVisible()) {
        setChatLoading(false);
      }
      // 只有正常完成（拿到有效结果）时才清空 pendingChatRef；
      // 异常/中断时保留，让 visibilitychange 有机会自动恢复
      if (result != null && chatResultLooksValid(result)) {
        clearPendingChatRequest(requestProjectId);
      }
    }
  };

  const dispatchGateAction = async (
    action: GateActionKey,
    payload?: GateActionPayload,
    options: { allowWhileChatLoading?: boolean; source?: "button" | "agent" } = {}
  ): Promise<GateActionResult> => {
    const currentProject = selectedProject;
    const actionRole = currentAgentRoleRef.current === "visual" ? "visual" : "content";
    const reportBlockedAction = (
      message: string,
      reason: GateActionResult["reason"] = "not_ready",
      toastType: ToastItem["type"] = "info"
    ): GateActionResult => {
      if (currentProject && options.source === "agent") {
        appendProjectChatMessage(currentProject.id, actionRole, {
          role: "agent",
          content: message,
          agentRole: actionRole,
        });
      } else {
        showToast(message, toastType);
      }
      return { ok: false, reason, message };
    };

    if (!currentProject) {
      return reportBlockedAction("请先选择项目。", "missing_project", "info");
    }
    if (isBusy) {
      return reportBlockedAction("当前已有任务在执行中，请等待状态更新完成后再继续。", "busy", "info");
    }
    if (chatLoading && !options.allowWhileChatLoading) {
      return reportBlockedAction("正在处理上一条消息，请稍候。", "chat_loading", "info");
    }
    const latestGateContext = gateContextRef.current || gateContext;
    const pageNums = (payload?.page_nums || []).filter((n) => Number.isFinite(Number(n))).map(Number);
    if (!latestGateContext.allowedActions.includes(action)) {
      return reportBlockedAction("这个操作不适用于当前页面状态。请使用页面上可用的按钮继续，或刷新后重试。", "stale_gate", "info");
    }
    try {
      if (action === "start_prototype" || action === "start_generation") {
        const targetSlides = action === "start_prototype"
          ? getPrototypeTargetSlides(pageNums)
          : getFullGenerationTargetSlides(pageNums);
        if (targetSlides.length === 0) {
          return reportBlockedAction("请先生成页面内容。", "not_ready", "info");
        }
        const pendingChangePages = targetSlides
          .filter((slide) => {
            const localStale = staleMap[slide.id] || {};
            const backendStale = getSlideStaleFlags(slide);
            return Boolean(localStale.content || localStale.visual || backendStale.content || backendStale.visual);
          })
          .map((slide) => slide.page_num);
        if (pendingChangePages.length > 0) {
          return reportBlockedAction(`第 ${pendingChangePages.join(", ")} 页还有未应用的修改，请先应用变更后再生成图片。`, "not_ready", "info");
        }
        const missingPromptPages = targetSlides
          .filter((slide) => !slideHasPrompt(slide))
          .map((slide) => slide.page_num);
        if (missingPromptPages.length > 0) {
          return reportBlockedAction(`第 ${missingPromptPages.join(", ")} 页还没有生图 Prompt，请先生成画面方案。`, "not_ready", "info");
        }
      }

      switch (action) {
        case "send_brief":
          await handleSendChat();
          return { ok: true };
        case "generate_content_plan": {
          const userBrief = payload?.topic || getLatestComposerTextForSubmission();
          const inferredPageCount = payload?.page_count || inferRequestedPageCount(userBrief);
          const topic = [userBrief, briefAttachmentSummary ? `【用户上传材料】\n${briefAttachmentSummary}` : ""]
            .filter(Boolean)
            .join("\n\n");
          if (!topic) {
            briefEditorRef.current?.focus();
            return reportBlockedAction("请先输入 PPT 主题或 Brief。", "invalid_input", "info");
          }
          const submittedLabel = payload?.topic ? "确认建议，开始生成内容规划" : "已提交 Brief，开始生成内容规划";
          const submittedDetails = [userBrief, briefAttachmentSummary ? `已上传材料：\n${briefAttachmentSummary}` : ""]
            .filter(Boolean)
            .join("\n\n");
          const submittedDisplayContent = buildSubmittedBriefDisplayContent({
            fromSuggestion: Boolean(payload?.topic),
            userBrief,
            attachmentSummary: briefAttachmentSummary,
            pageCount: inferredPageCount,
          });
          return await startContentPlanPoll(currentProject.id, topic, options.source || "button", inferredPageCount, {
            onStarted: clearBriefComposerState,
            submittedLabel,
            submittedContent: [submittedLabel, submittedDetails].filter(Boolean).join("\n\n"),
            submittedDisplayContent,
            attachmentIds: payload?.attachment_ids,
          });
        }
        case "confirm_content":
        case "switch_to_visual":
          if (!contentPlanConfirmed && slides.length > 0) {
            await handleConfirmContentPlan();
          } else {
            setCurrentAgentRole("visual");
            ensureVisualGreetingIfNeeded();
          }
          return { ok: true };
        case "switch_to_content":
          setCurrentAgentRole("content");
          return { ok: true };
          case "generate_style_proposals": {
            if (currentAgentRole !== "visual") {
              currentAgentRoleRef.current = "visual";
              setCurrentAgentRole("visual");
            }
            const hasExistingStyleProposals = styleProposalsInChat.length > 0 || Boolean(currentProject.style_proposal?.proposals?.length);
            const styleLoadingId = `sp-action-${Date.now()}`;
            const visualHistoryForContext = visualChatHistory.map((message) => ({
              role: message.role === "agent" ? "assistant" : message.role,
              content: message.content,
            }));
            const styleGenerationContext = buildVisualStyleGenerationContext(
              visualHistoryForContext,
              "",
              buildCrossStageContext("visual")
            );
            updateProjectChatMessages(currentProject.id, "visual", (prev) => [
              ...prev,
              {
                role: "agent",
                content: "正在生成视觉方向，请稍候...",
                agentRole: "visual",
                loading: true,
                id: styleLoadingId,
              },
            ]);
            setOperatingProjectId(currentProject.id);
            let styleRunId: string | null = null;
            try {
              const freshReferenceImages = await fetchReferenceImages(currentProject.id);
              const shouldForceStyleProposal =
                hasExistingStyleProposals || freshReferenceImages.length > 0 || Boolean(styleGenerationContext);
              const styleResult = await generateStyleProposals(
                currentProject.id,
                shouldForceStyleProposal,
                styleGenerationContext
              );
              styleRunId = styleResult?.run?.id || null;
              if (styleRunId) {
                locallyHandledRunIdsRef.current.add(styleRunId);
                updateProjectChatMessages(currentProject.id, "visual", (prev) =>
                  prev.map((message) =>
                    message.id === styleLoadingId
                      ? { ...message, runId: styleRunId || undefined, content: runProgressText(styleResult.run) }
                      : message
                  )
                );
              }
              if (styleResult.status === "generating") {
                await pollForStyleProposals(currentProject.id);
              }
              await loadProjects();
              const fresh = normalizeProjectsForActiveSelection(await fetchProjects(), selectedProjectIdRef.current);
              const updated = fresh.find((project: Project) => project.id === currentProject.id);
              if (updated && selectedProjectIdRef.current === currentProject.id) {
                setSelectedProject(clearProjectNotification(updated));
              }
              const proposals = updated?.style_proposal?.proposals || styleResult.proposals || [];
              if (proposals.length > 0) {
                if (selectedProjectIdRef.current === currentProject.id) setStyleProposalsInChat(proposals);
                updateProjectChatMessages(currentProject.id, "visual", (prev) => [
                  ...prev.filter((message) => message.id !== styleLoadingId),
                  {
                    role: "agent",
                    content: hasExistingStyleProposals
                      ? "视觉方向已重新生成，请查看下方卡片。"
                      : "视觉方向已生成，请查看下方卡片。",
                    agentRole: "visual",
                    hasStyleProposal: true,
                    styleProposals: proposals,
                    gate: gateContext.gate,
                    gateRevision: gateContext.gateRevision,
                  },
                ]);
              } else {
                updateProjectChatMessages(currentProject.id, "visual", (prev) => [
                  ...prev.filter((message) => message.id !== styleLoadingId),
                  {
                    role: "agent",
                    content: "视觉方向已生成，请在作品画布中查看。",
                    agentRole: "visual",
                  },
                ]);
              }
            } catch (err: any) {
              if (styleRunId) locallyHandledRunIdsRef.current.delete(styleRunId);
              const errorMessage = await resolveWorkflowFailureMessage(
                currentProject.id,
                "style_proposal",
                userFacingGenerationError(err?.message)
              );
              updateProjectChatMessages(currentProject.id, "visual", (prev) => [
                ...prev.filter((message) => message.id !== styleLoadingId),
                {
                  role: "agent",
                  content: "视觉方向生成失败：" + errorMessage,
                  agentRole: "visual",
                },
              ]);
              return { ok: false, reason: "failed", message: errorMessage };
            } finally {
              setOperatingProjectId(null);
            }
            return { ok: true };
          }
        case "confirm_style":
          if (!payload?.style) {
            return reportBlockedAction("请先选择一套风格方案。", "invalid_input", "info");
          }
          await handleSelectStyle(payload.style);
          return { ok: true };
        case "generate_visual_prompts":
          await handleGeneratePrompts(false);
          return { ok: true };
        case "start_prototype": {
          const prototypePageNums = getPrototypeTargetSlides(pageNums).map((slide) => slide.page_num);
          setSelectedPages(new Set(prototypePageNums));
          setPrototypeSelectionTouched(true);
          await handleStartGeneration(true, true, prototypePageNums);
          return { ok: true };
        }
        case "resample_prototype": {
          const prototypePageNums = getPrototypeResampleTargetSlides(pageNums).map((slide) => slide.page_num);
          setSelectedPages(new Set(prototypePageNums));
          setPrototypeSelectionTouched(true);
          await handleStartGeneration(true, true, prototypePageNums);
          return { ok: true };
        }
        case "confirm_prototype":
          await handleConfirmPrototype();
          return { ok: true };
        case "start_generation":
          await handleStartGeneration(pageNums.length > 0, false, pageNums.length > 0 ? pageNums : undefined);
          return { ok: true };
        case "retry_failed":
          await handleRetryAllFailed();
          return { ok: true };
        case "templates":
          setShowTemplateRecommender(true);
          return { ok: true };
        case "download":
          window.location.href = getDownloadUrl(currentProject.id);
          return { ok: true };
        default: {
          const exhaustive: never = action;
          console.warn("Unhandled gate action", exhaustive);
          return reportBlockedAction("这个动作暂时还不能自动执行。", "not_ready", "info");
        }
      }
    } catch (err: any) {
      return reportBlockedAction("动作执行失败：" + (err.message || "未知错误"), "failed", "error");
    }
  };

  const handleAgentNextAction = async (nextAction: AgentNextAction, sourceMessage?: ChatMessage) => {
    if (!selectedProject) {
      showToast("请先选择项目。", "info");
      return;
    }
    if (isBusy || chatLoading) {
      showToast("当前还有任务或消息在处理中，请等待状态更新完成后再继续。", "info");
      return;
    }
    if (sourceMessage && !isMessageFromCurrentGate(sourceMessage)) {
      showToast("这条操作不适用于当前页面状态，请使用页面上可用的按钮继续。", "info");
      return;
    }
    if (nextAction.confirm) {
      const ok = await showConfirm(`确定要执行「${nextAction.label}」吗？`);
      if (!ok) return;
    }

    const pageNums = (nextAction.payload?.page_nums || []).filter((n) => Number.isFinite(Number(n))).map(Number);

    switch (nextAction.type) {
      case "generate_content_plan": {
        const topic = nextAction.payload?.topic || sourceMessage?.topic;
        await dispatchGateAction("generate_content_plan", {
          topic,
          page_count: nextAction.payload?.page_count || sourceMessage?.positioning?.estimated_pages,
        });
        return;
      }
      case "switch_to_visual":
        await dispatchGateAction("switch_to_visual");
        return;
      case "switch_to_content":
        await dispatchGateAction("switch_to_content");
        return;
      case "generate_style_proposals": {
        await dispatchGateAction("generate_style_proposals");
        return;
      }
      case "generate_visual_prompts":
        await dispatchGateAction("generate_visual_prompts");
        return;
      case "generate_images":
        await dispatchGateAction("start_generation", { page_nums: pageNums });
        return;
      case "start_prototype":
        await dispatchGateAction("start_prototype", { page_nums: pageNums });
        return;
      case "confirm_prototype":
        await dispatchGateAction("confirm_prototype");
        return;
      case "start_generation":
        await dispatchGateAction("start_generation");
        return;
      case "retry_failed":
        await dispatchGateAction("retry_failed");
        return;
      case "download":
        await dispatchGateAction("download");
        return;
      default: {
        const exhaustive: never = nextAction.type;
        console.warn("Unhandled agent next action:", exhaustive);
        showToast("这个下一步动作暂时还不能自动执行", "info");
      }
    }
  };

  const handleStopChat = () => {
    abortActiveChat(false);
  };

  const handleEditMessage = (index: number) => {
    if (chatMessages[index].role !== "user") return;
    setEditingMessageIndex(index);
    setEditMessageContent(chatMessages[index].content);
  };

  const handleSaveMessageEdit = () => {
    if (editingMessageIndex === null || !editMessageContent.trim()) return;
    // 回滚到该消息之前，然后用编辑后的内容重新发送
    const trimmed = editMessageContent.trim();
    const newMessages = chatMessages.slice(0, editingMessageIndex);
    setActiveChatMessages(newMessages);
    setEditingMessageIndex(null);
    setEditMessageContent("");
    // 重新发送编辑后的消息，传入裁剪后的历史避免闭包拿到旧状态
    setTimeout(() => handleSendChat(trimmed, newMessages), 0);
  };

  const handleDeleteMessage = (index: number) => {
    // 删除该消息及其之后的所有消息（回滚）
    const newMessages = chatMessages.slice(0, index);
    setActiveChatMessages(newMessages);
    if (editingMessageIndex !== null && editingMessageIndex >= index) {
      setEditingMessageIndex(null);
      setEditMessageContent("");
    }
  };

  const uploadAgentAttachmentFiles = async (files: File[]) => {
    if (!selectedProject || files.length === 0) return;
    const imageFiles = files.filter(isBriefImageFile);
    const documentFiles = files.filter((file) => !isBriefImageFile(file));
    const uploadRole = currentAgentRole === "visual" ? "visual_asset" : "content_ref";
    setOperatingProjectId(selectedProject.id);
    setUploadingDoc(true);
    try {
      const uploaded: ChatAttachment[] = [];
      for (const file of imageFiles) {
        const data = await uploadFile(selectedProject.id, file, uploadRole, undefined, "blend", {
          asset_name: file.name.replace(/\.[^.]+$/, ""),
          usage_note:
            currentAgentRole === "visual"
              ? "用户在视觉 Agent 对话上传，作为后续视觉参考或素材"
              : "用户在内容 Agent 对话上传，作为内容提取和后续修改素材",
        });
        uploaded.push({
          id: data.id,
          name: file.name,
          url: `${API_BASE}${data.url}`,
          role: uploadRole,
        });
      }
      const uploadedDocs: string[] = [];
      for (const file of documentFiles) {
        const data = await uploadDocument(selectedProject.id, file);
        if (data.detail) {
          showToast(`"${file.name}" 上传失败：${data.detail}`, "error");
          continue;
        }
        uploadedDocs.push(data.filename || file.name);
      }
      if (uploaded.length > 0) {
        setPendingChatAttachments((prev) => [...prev, ...uploaded]);
        await loadReferenceImages(selectedProject.id);
      }
      if (uploadedDocs.length > 0) {
        setPendingAttachments((prev) => Array.from(new Set([...prev, ...uploadedDocs])));
        await loadDocuments(selectedProject.id);
      }
      if (uploaded.length || uploadedDocs.length) {
        addSystemLog(
          `用户在 Agent 窗口上传了 ${uploadedDocs.length} 个文件、${uploaded.length} 张图片。它们会作为后续 Agent 对话和生成流程的上下文。`,
          uploaded
        );
        showToast(`已加入 ${uploadedDocs.length + uploaded.length} 个附件`, "success");
      }
    } catch (err: any) {
      showToast("附件上传失败：" + (err.message || "未知错误"), "error");
    } finally {
      setOperatingProjectId(null);
      setUploadingDoc(false);
    }
  };

  const handlePickAgentAttachments = () => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = AGENT_ATTACHMENT_ACCEPT;
    input.multiple = true;
    input.onchange = () => {
      const files = Array.from(input.files || []);
      if (files.length > 0) {
        uploadAgentAttachmentFiles(files);
      }
    };
    input.click();
  };

  const handleDropFiles = async (files: FileList) => {
    if (!selectedProject) return;
    if (isBriefStudioActive) {
      await uploadBriefFiles(Array.from(files));
      return;
    }
    if (currentAgentRole === "finetune" && finetuneTargetSlideId) {
      const targetSlide = slides.find((s) => s.id === finetuneTargetSlideId);
      const imageFiles = Array.from(files).filter(isBriefImageFile);
      if (imageFiles.length > 0) {
        setOperatingProjectId(selectedProject.id);
        try {
          const uploaded: ChatAttachment[] = [];
          for (const file of imageFiles) {
            const data = await uploadFile(selectedProject.id, file, "finetune_ref", finetuneTargetSlideId);
            uploaded.push({
              id: data.id,
              name: file.name,
              url: `${API_BASE}${data.url}`,
              role: "finetune_ref",
            });
          }
          setPendingFinetuneAttachmentsMap((prev) => ({
            ...prev,
            [finetuneTargetSlideId]: [...(prev[finetuneTargetSlideId] || []), ...uploaded],
          }));
          showToast(`已加入 ${uploaded.length} 张本轮参考图`, "success");
          addSystemLog(`用户为第 ${targetSlide?.page_num || "?"} 页添加了 ${uploaded.length} 张本轮微调参考图`);
        } catch (err: any) {
          showToast("参考图上传失败：" + (err.message || "未知错误"), "error");
        } finally {
          setOperatingProjectId(null);
        }
        return;
      }
    }
    await uploadAgentAttachmentFiles(Array.from(files));
  };

  const typeLabel: Record<string, string> = {
    cover: "封面",
    toc: "目录",
    content: "内容",
    hero: "金句",
    data: "数据",
    ending: "封底",
    section: "章节",
  };

  const typeColor: Record<string, string> = {
    cover: "bg-purple-100 text-purple-700",
    toc: "bg-blue-100 text-blue-700",
    content: "bg-gray-100 text-gray-700",
    hero: "bg-yellow-100 text-yellow-700",
    data: "bg-green-100 text-green-700",
    ending: "bg-gray-100 text-gray-700",
    section: "bg-pink-100 text-pink-700",
  };
  const projectLogo = referenceImages.find(isConfirmedLogoRef);
  const styleDockProposals: StyleProposal[] =
    styleProposalsInChat.length > 0
      ? styleProposalsInChat
      : (selectedProject?.style_proposal?.proposals || []);

  // 处理 LLM 返回的转义字符（如 \\n -> 真正换行）
  const unescapeText = (text: string): string => {
    return text
      .replace(/<br\s*\/?>/gi, "\n")
      .replace(/\\n/g, "\n")
      .replace(/\\t/g, "\t")
      .replace(/\\"/g, '"')
      .replace(/\\'/g, "'")
      .replace(/\\\\/g, "\\");
  };

  const statusLabel: Record<string, string> = STATUS_LABEL;

  const statusText: Record<string, string> = {
    pending: "",
    visual_ready: "",
    prompt_ready: "",
    prototype: "",
    prototype_ready: "",
    generating: "",
    completed: "",
    failed: "",
  };

  const currentStatus = selectedProject?.status || "draft";
  const slidesMissingPromptCount = slides.filter((slide) => !slideHasPrompt(slide)).length;
  const normalizePageNums = (pageNums?: number[] | null) =>
    Array.isArray(pageNums)
      ? Array.from(new Set(pageNums.map(Number).filter((n) => Number.isFinite(n)))).sort((a, b) => a - b)
      : [];
  const defaultPrototypePageNums = defaultPrototypePageNumsForSlides(slides);
  const selectedPrototypePageNums = prototypeSelectionTouched ? normalizePageNums(Array.from(selectedPages)) : defaultPrototypePageNums;
  const activeRunTargetPageNums = normalizePageNums(
    activeRun?.target_page_nums ||
      currentProjectStatus?.target_page_nums ||
      currentProjectStatus?.progress?.target_page_nums ||
      null
  );
  const isPrototypeRunActive = Boolean(hasActiveRun && activeRun?.kind === "prototype_generation");
  const visiblePrototypePageNums =
    isPrototypeRunActive && activeRunTargetPageNums.length > 0
      ? activeRunTargetPageNums
      : selectedPrototypePageNums;
  const visiblePrototypePageSet = new Set(visiblePrototypePageNums);
  const formatPrototypePages = (pageNums: number[]) =>
    pageNums.length > 0 ? `第 ${pageNums.join("、")} 页，共 ${pageNums.length} 页` : "未选择打样页";
  const prototypeSelectionSummary = formatPrototypePages(visiblePrototypePageNums);
  const canEditPrototypeSelection =
    (currentStatus === "prompt_ready" || currentStatus === "failed") && !isPrototypeRunActive && !isBusy && !chatLoading;
  const shouldShowPrototypeSelection =
    slides.length > 0 && (currentStatus === "prompt_ready" || currentStatus === "failed" || isPrototypeRunActive);
  const slidesAreLoading = Boolean(
    selectedProject &&
    slidesProjectId !== selectedProject.id &&
    (slidesLoadingProjectId === selectedProject.id || slidesProjectId === null)
  );
  const workflowState = buildWorkflowState({
    projectStatus: currentStatus,
    slides,
    activeRun,
    contentPlanConfirmed,
    showPrototypePreview,
    hasSelectedStyle: Boolean(selectedProject?.selected_style),
    selectedPageCount: selectedPrototypePageNums.length,
    staleSummary: {
      hasContentOrVisualStale,
      imageStaleCount: imageStaleSlides.length,
    },
    templatePageCount: templatePages.length,
    isBusy,
  });
  const gateContext = buildGateContext(workflowState, gateRevision);
  gateContextRef.current = gateContext;
  const isBriefStudioActive = Boolean(selectedProject && gateContext.mainStageMode === "brief_studio" && slides.length === 0);
  const isContentPlanRunActive = Boolean(
    selectedProject &&
    ((hasActiveRun && activeRun?.kind === "content_plan") ||
      (operatingProjectId === selectedProject.id && currentStatus === "draft" && currentAgentRole === "content"))
    );
    const agentComposerValue = !isBriefStudioActive && chatInput.includes("[[PPTGOD_ATTACHMENT:") ? "" : chatInput;
    const selectedPageNumsForAgent = useMemo(() => Array.from(selectedPages).sort((a, b) => a - b), [selectedPages]);
    const activeAgentScope: AgentRequestScope =
      agentMode === "page" && editingSlide
        ? "current_slide"
        : selectedPageNumsForAgent.length > 1
        ? "selected_slides"
        : "deck";
    const composerRequestContext = useMemo(
      () =>
        inferAgentRequestContext({
          message: agentComposerValue,
          activeAgentRole: currentAgentRole,
          activeScope: activeAgentScope,
          editingPageNum: editingSlide?.page_num,
          selectedPageNums: selectedPageNumsForAgent,
          projectStatus: currentStatus,
          slideCount: slides.length,
          contentPlanConfirmed,
          hasSelectedStyle: Boolean(selectedProject?.selected_style),
          hasPrompt: slides.some(slideHasPrompt),
          hasGeneratedImage: slides.some((slide) => Boolean(slide.image_path)),
        }),
      [
        activeAgentScope,
        agentComposerValue,
        contentPlanConfirmed,
        currentAgentRole,
        currentStatus,
        editingSlide?.page_num,
        selectedPageNumsForAgent,
        selectedProject?.selected_style,
        slides,
      ]
    );
    const activeComposerDraftKey = useMemo(() => {
    if (!selectedProject) return null;
    if (isBriefStudioActive) return getBriefDraftStorageKey(selectedProject.id);
    const targetSlideId = currentAgentRole === "finetune"
      ? finetuneTargetSlideId || editingSlide?.id || null
      : null;
    return getAgentDraftStorageKey(selectedProject.id, currentAgentRole, targetSlideId);
  }, [selectedProject?.id, isBriefStudioActive, currentAgentRole, finetuneTargetSlideId, editingSlide?.id]);

  useEffect(() => {
    chatInputValueRef.current = chatInput;
    if (suspendComposerDraftPersistRef.current) {
      suspendComposerDraftPersistRef.current = false;
      return;
    }
    const key = activeComposerDraftKeyRef.current;
    if (key) writeComposerDraft(key, chatInput);
  }, [chatInput]);

  useEffect(() => {
    const previousKey = activeComposerDraftKeyRef.current;
    if (previousKey && previousKey !== activeComposerDraftKey) {
      writeComposerDraft(previousKey, chatInputValueRef.current);
    }
    activeComposerDraftKeyRef.current = activeComposerDraftKey;
    const nextValue = activeComposerDraftKey ? readComposerDraft(activeComposerDraftKey) : "";
    chatInputValueRef.current = nextValue;
    setChatInput(nextValue);
    if (isBriefStudioActive) {
      requestAnimationFrame(() => syncBriefEditorValue(nextValue));
    }
  }, [activeComposerDraftKey, isBriefStudioActive]);

  useEffect(() => {
    const saveDraftBeforeHide = () => saveActiveComposerDraft();
    window.addEventListener("pagehide", saveDraftBeforeHide);
    document.addEventListener("visibilitychange", saveDraftBeforeHide);
    return () => {
      window.removeEventListener("pagehide", saveDraftBeforeHide);
      document.removeEventListener("visibilitychange", saveDraftBeforeHide);
    };
  }, []);

  useEffect(() => {
    if (!isBriefStudioActive) return;
    if (briefEditorValueRef.current !== chatInput) {
      syncBriefEditorValue(chatInput);
    }
  }, [isBriefStudioActive, selectedProject?.id]);
  const steps = WORKFLOW_STEPS;
  const displayStepIndex = workflowState.stepIndex;
  const activeAssetSlide =
    editingSlide ||
    (selectedPages.size === 1 ? slides.find((slide) => selectedPages.has(slide.page_num)) || null : null);
  const prototypePromptTargets = getPrototypeTargetSlides();
  const canStartPrototypeGeneration =
    prototypePromptTargets.length > 0 &&
    prototypePromptTargets.every(slideHasPrompt);
  const canStartFullGeneration =
    slides.length > 0 &&
    slides.every(slideHasPrompt);
  const briefImageAttachments = referenceImages.filter((ref: any) => {
    const analysis = ref.asset_analysis || {};
    return ref.role === "content_ref" && !ref.slide_id && !analysis.pptx_source_page_num;
  });
  const isDocumentProcessingStatus = (status: any) =>
    ["queued", "running"].includes(String(status || ""));
  const briefDocumentsParsing = documents.some((doc: any) =>
    isDocumentProcessingStatus(doc.text_parse_status) ||
    isDocumentProcessingStatus(doc.asset_extraction_status)
  );
  const briefComposerSupportText = uploadingDoc
    ? "正在加入材料；你可以继续写 Brief"
    : briefDocumentsParsing
    ? "材料解析中；你可以继续补充 Brief"
    : "支持 PDF、Word、PPT、Markdown、TXT、图片";
  const hasBriefAttachments =
    pendingAttachments.length > 0 ||
    documents.length > 0 ||
    briefImageAttachments.length > 0;
  const briefAttachmentSummary = [
    documents.length ? `已上传文档：${documents.map((doc: any) => doc.filename).join("、")}` : "",
    briefImageAttachments.length ? `已上传图片：${briefImageAttachments.map((ref: any) => ref.asset_name || ref.url?.split("/").pop() || "图片").join("、")}` : "",
  ].filter(Boolean).join("\n");
  const briefAttachmentSignature = [
    ...documents.map((doc: any) => `doc:${doc.filename}`),
    ...briefImageAttachments.map((ref: any) => `image:${ref.id}:${ref.asset_name || ref.url || ""}`),
  ].join("|");

  useEffect(() => {
    if (!isBriefStudioActive || !chatInput.includes("[[PPTGOD_ATTACHMENT:")) return;
    syncBriefEditorValue(chatInput);
  }, [briefAttachmentSignature, isBriefStudioActive]);

  useEffect(() => {
    if (isBriefStudioActive || !chatInput.includes("[[PPTGOD_ATTACHMENT:")) return;
    setChatInput("");
    briefEditorValueRef.current = "";
  }, [chatInput, isBriefStudioActive]);

  const stepStatus = (idx: number) => {
    return workflowState.stepStatuses[idx];
  };

  const isLoadingStatus = workflowState.isLoading;

  // 当前步骤引导文案
  const getGuidanceText = () => {
    return getWorkflowGuidanceText(workflowState);
  };

  const primaryActionKey = getPrimaryActionKey(workflowState);
  const secondaryActionKeys = getSecondaryActionKeys(workflowState);
  const hasContentConfirmCta = Boolean(selectedProject && slides.length > 0 && currentStatus === "planning" && !contentPlanConfirmed);
  const withGateMeta = (message: ChatMessage): ChatMessage => {
    if (
      !message.nextAction &&
      !message.hasStyleProposal &&
      message.action !== "propose_plan" &&
      message.action !== "generate_plan"
    ) return message;
    return {
      ...message,
      gate: message.gate || gateContext.gate,
      gateRevision: message.gateRevision ?? gateContext.gateRevision,
    };
  };
  const applyGateMetaToNewMessages = (previous: ChatMessage[], next: ChatMessage[]) =>
    next.map((message) => (previous.includes(message) ? message : withGateMeta(message)));
  const isMessageFromCurrentGate = (message: ChatMessage) => {
    const hasGateBoundAction =
      Boolean(message.nextAction) ||
      Boolean(message.hasStyleProposal) ||
      message.action === "propose_plan" ||
      message.action === "generate_plan";
    if (message.gateRevision == null || !message.gate) return !hasGateBoundAction;
    return message.gateRevision === gateContext.gateRevision && message.gate === gateContext.gate;
  };
  const shouldRenderMessageNextAction = (message: ChatMessage) => {
    const nextAction = message.nextAction;
    if (!nextAction) return false;
    if (!isMessageFromCurrentGate(message)) return false;
    if (nextAction.type === "generate_content_plan" && message.positioning) return false;
    if (nextAction.type === "start_prototype" && primaryActionKey === "start-prototype") return false;
    if (nextAction.type === "retry_failed" && secondaryActionKeys.includes("retry-failed")) return false;
    if (nextAction.type === "start_generation" && secondaryActionKeys.includes("generate-all")) return false;
    return true;
  };

  const topPrimaryAction: UiAction | null = (() => {
    if (!selectedProject) return null;
    if (["deck_visual", "deck_prototype", "deck_final"].includes(gateContext.mainStageMode)) return null;
    const actionKey = primaryActionKey;
    if (actionKey === "generate-style-proposals") {
      if (styleDockProposals.length > 0) return null;
      return {
        key: "generate-style-proposals",
        label: isBusy ? "生成中..." : "生成视觉方向",
        onClick: () => dispatchGateAction("generate_style_proposals"),
        variant: "primary",
        disabled: isBusy || chatLoading,
      };
    }
    if (actionKey === "generate-visual-prompts") {
      return {
        key: "generate-visual-prompts",
        label: isBusy ? "生成中..." : "生成画面方案",
        onClick: () => dispatchGateAction("generate_visual_prompts"),
        variant: "primary",
        disabled: isBusy || chatLoading,
      };
    }
    if (actionKey === "start-prototype") {
      const prototypeActionCount = prototypePromptTargets.length;
      return {
        key: "prototype",
        label: isBusy
          ? "打样中..."
          : prototypeActionCount > 0
          ? `打样 ${prototypeActionCount} 页`
          : "选择打样页",
        onClick: () => dispatchGateAction("start_prototype"),
        variant: "primary",
        disabled: isBusy || !canStartPrototypeGeneration,
      };
    }
    if (actionKey === "download") {
      return {
        key: "download",
        label: "下载 PPTX",
        href: getDownloadUrl(selectedProject.id),
        variant: "primary",
      };
    }
    return null;
  })();

  const topSecondaryActions: UiAction[] = (() => {
    if (!selectedProject) return [];
    const stageDockOwnsActions = ["deck_visual", "deck_prototype"].includes(gateContext.mainStageMode);
    const actionKeys = secondaryActionKeys;
    const actions: UiAction[] = [];
    if (actionKeys.includes("templates")) {
      actions.push({
        key: "templates",
        label: "查看模板",
        onClick: () => dispatchGateAction("templates"),
        variant: "secondary",
      });
    }
    if (!stageDockOwnsActions && actionKeys.includes("generate-all")) {
      actions.push({
        key: "generate-all",
        label: "生成全部",
        onClick: () => dispatchGateAction("start_generation"),
        variant: "link",
        disabled: isBusy || !canStartFullGeneration,
      });
    }
    if (!stageDockOwnsActions && actionKeys.includes("toggle-prototype-view")) {
      actions.push({
        key: "toggle-prototype-view",
        label: showPrototypePreview ? "返回全局预览" : "查看打样结果",
        onClick: () => setShowPrototypePreview((v) => !v),
        variant: "secondary",
        disabled: isBusy,
      });
    }
    if (!stageDockOwnsActions && actionKeys.includes("resample")) {
      actions.push({
        key: "resample",
        label: "重新打样",
        onClick: () => dispatchGateAction("resample_prototype"),
        variant: "secondary",
        disabled: isBusy,
      });
    }
    if (actionKeys.includes("retry-failed")) {
      actions.push({
        key: "retry-failed",
        label: isBusy ? "重试中..." : "一键重试失败页",
        onClick: () => dispatchGateAction("retry_failed"),
        variant: "danger",
        disabled: isBusy,
      });
    }
    // Page regeneration actions live on the affected page/card. Keeping them out
    // of the global header avoids making a local edit feel like a whole-project step.
    if (actionKeys.includes("regenerate")) {
      actions.push({
        key: "regenerate",
        label: "重新生成",
        onClick: () => dispatchGateAction("start_generation"),
        variant: "secondary",
        disabled: isBusy || !canStartFullGeneration,
      });
    }
    if (slides.length > 0) {
      actions.push({
        key: "export-md",
        label: "导出 MD",
        href: getContentPlanMarkdownUrl(selectedProject.id),
        variant: "link",
      });
    }
    return actions;
  })();

  const actionClassName = (variant: UiAction["variant"] = "secondary") => {
    const base = "pg-action text-sm px-3 py-1 rounded disabled:opacity-50 whitespace-nowrap";
    if (variant === "primary") return `${base} pg-action-primary bg-blue-600 text-white hover:bg-blue-700`;
    if (variant === "danger") return `${base} pg-action-danger bg-red-50 text-red-600 hover:bg-red-100 border border-red-100`;
    if (variant === "link") return "text-sm px-1 py-1 text-slate-500 hover:text-slate-900 underline underline-offset-2 whitespace-nowrap";
    return `${base} pg-action-secondary bg-gray-100 text-gray-700 hover:bg-gray-200`;
  };

  const renderTopAction = (action: UiAction) => {
    if (action.href) {
      return (
        <a key={action.key} href={action.href} className={actionClassName(action.variant)}>
          {action.label}
        </a>
      );
    }
    return (
      <button
        key={action.key}
        onClick={action.onClick}
        disabled={action.disabled}
        className={actionClassName(action.variant)}
      >
        {action.label}
      </button>
    );
  };

  const currentStageNudge: StageNudge | null = (() => {
    if (!selectedProject || currentAgentRole === "finetune" || isBriefStudioActive) return null;
    const actionDisabled = isBusy || chatLoading;
    const visualTone: StageNudge["tone"] = "visual";
    const contentTone: StageNudge["tone"] = "content";

    if (hasActiveRun) {
      return {
        title: "当前任务正在处理",
        body: workflowProgressText(currentProjectStatus || { active_run: activeRun }) || "请等待任务完成，完成后这里会更新下一步。",
        role: activeRun?.kind === "content_plan" ? "content" : "visual",
        tone: activeRun?.kind === "content_plan" ? contentTone : visualTone,
      };
    }

    if (currentAgentRole === "visual" && hasContentOrVisualStale) {
      const count = staleSlides.filter((x) => x.stale.content || x.stale.visual).length;
      return {
        title: "下一步：更新画面方案",
        body: `${count} 页内容或画面描述变更，需要先更新画面方案，再重新生成图片。`,
        role: "visual",
        tone: "warning",
        primary: {
          key: "update-stale",
          label: "更新画面方案",
          onClick: () => handleUpdateStaleSlides(),
          variant: "primary",
          disabled: actionDisabled,
        },
      };
    }

    if (currentAgentRole === "visual" && imageStaleSlides.length > 0) {
      return {
        title: "下一步：重新生成图片",
        body: `${imageStaleSlides.length} 页图片已过期。确认画面方案后，重新生成这些页面即可。`,
        role: "visual",
        tone: "warning",
        primary: {
          key: "generate-stale-images",
          label: "重新生成图片",
          onClick: () => handleGenerateStaleImages(),
          variant: "primary",
          disabled: actionDisabled,
        },
      };
    }

    if (!contentPlanConfirmed) {
      if (slides.length > 0) {
        return {
          title: "下一步：确认内容",
          body: `当前已有 ${slides.length} 页内容规划。检查页数、标题和顺序后，确认进入视觉阶段。`,
          role: "content",
          tone: contentTone,
          primary: {
            key: "confirm-content",
            label: confirmingProjectId === selectedProject.id ? "正在介入..." : "确认内容，请视觉总监",
            onClick: () => dispatchGateAction("confirm_content"),
            variant: "primary",
            disabled: actionDisabled || confirmingProjectId === selectedProject.id,
          },
        };
      }
      return currentAgentRole === "content"
        ? {
            title: "下一步：生成内容规划",
            body: "先在输入框补充主题、材料和目标，再生成整份 PPT 的内容结构。",
            role: "content",
            tone: contentTone,
          }
        : null;
    }

    if (currentAgentRole === "content") {
      return {
        title: "下一步：进入视觉阶段",
        body: "内容已确认。接下来由视觉总监生成整体方向、画面方案和打样页。",
        role: "content",
        tone: contentTone,
        primary: {
          key: "switch-visual",
          label: "请视觉总监介入",
          onClick: () => dispatchGateAction("switch_to_visual"),
          variant: "primary",
          disabled: actionDisabled,
        },
      };
    }

    if (!selectedProject.selected_style) {
      if (styleDockProposals.length > 0) {
        return {
          title: "下一步：选择视觉方向",
          body: `已生成 ${styleDockProposals.length} 套方向。可以选择推荐方案，也可以在作品画布查看详情后选择其它方案。`,
          role: "visual",
          tone: visualTone,
          primary: {
            key: "choose-recommended-style",
            label: "选择推荐方案",
            onClick: () => dispatchGateAction("confirm_style", { style: styleDockProposals[0] }),
            variant: "primary",
            disabled: actionDisabled,
          },
          secondary: {
            key: "regenerate-style",
            label: "重新生成",
            onClick: () => dispatchGateAction("generate_style_proposals"),
            variant: "secondary",
            disabled: actionDisabled,
          },
        };
      }
      return {
        title: "下一步：生成视觉方向",
        body: "生成前可先在上方「项目素材」上传 Logo、风格参考、可复用素材或模板；没有素材也可以直接生成。",
        role: "visual",
        tone: visualTone,
        primary: {
          key: "generate-style",
          label: "生成视觉方向",
          onClick: () => dispatchGateAction("generate_style_proposals"),
          variant: "primary",
          disabled: actionDisabled,
        },
      };
    }

    if (slidesMissingPromptCount > 0) {
      return {
        title: "下一步：生成画面方案",
        body: `还有 ${slidesMissingPromptCount} 页缺少画面方案或生图 Prompt。先补齐这些信息，再进入打样。`,
        role: "visual",
        tone: visualTone,
        primary: {
          key: "generate-prompts",
          label: "生成画面方案",
          onClick: () => dispatchGateAction("generate_visual_prompts"),
          variant: "primary",
          disabled: actionDisabled,
        },
      };
    }

    if (currentStatus === "prototype_ready") {
      return {
        title: "下一步：确认打样",
        body: "检查样张的风格、构图和文字可读性。满意后生成全部页面，不满意可重新打样。",
        role: "visual",
        tone: visualTone,
        primary: {
          key: "confirm-prototype",
          label: "确认打样，生成全部",
          onClick: () => dispatchGateAction("confirm_prototype"),
          variant: "primary",
          disabled: actionDisabled,
        },
        secondary: {
          key: "resample-prototype",
          label: "重新打样",
          onClick: () => dispatchGateAction("resample_prototype"),
          variant: "secondary",
          disabled: actionDisabled,
        },
      };
    }

    if (currentStatus === "completed") {
      return {
        title: "下一步：下载 PPTX",
        body: `${slides.filter((s) => s.image_path).length} / ${slides.length} 页已有画面。需要修改时，选中页面进入微调。`,
        role: "visual",
        tone: "final",
        primary: {
          key: "download",
          label: "下载 PPTX",
          href: getDownloadUrl(selectedProject.id),
          variant: "primary",
          disabled: false,
        },
      };
    }

    if (currentStatus === "failed" && workflowState.hasFailedSlide) {
      return {
        title: "下一步：重试失败页",
        body: "部分页面没有生成成功。先重试失败页，仍失败时再进入单页检查提示词和素材。",
        role: "visual",
        tone: "warning",
        primary: {
          key: "retry-failed",
          label: "一键重试失败页",
          onClick: () => dispatchGateAction("retry_failed"),
          variant: "danger",
          disabled: actionDisabled,
        },
      };
    }

    if (workflowState.hasPrompt) {
      return {
        title: "下一步：生成打样页",
        body: `当前打样页：${prototypeSelectionSummary}。先看样张效果，再决定是否生成全部页面。`,
        role: "visual",
        tone: visualTone,
        primary: {
          key: "start-prototype",
          label: prototypePromptTargets.length > 0 ? `打样 ${prototypePromptTargets.length} 页` : "选择打样页",
          onClick: () => dispatchGateAction("start_prototype"),
          variant: "primary",
          disabled: actionDisabled || !canStartPrototypeGeneration,
        },
      };
    }

    return null;
  })();

  const renderNudgeAction = (action: UiAction) => {
    const className = `${actionClassName(action.variant)} justify-center text-center`;
    if (action.href) {
      return (
        <a key={action.key} href={action.href} className={className}>
          {action.label}
        </a>
      );
    }
    return (
      <button
        key={action.key}
        onClick={action.onClick}
        disabled={action.disabled}
        className={className}
      >
        {action.label}
      </button>
    );
  };

  // 卡片间隙插入触发区：竖条（桌面端）/ 横条（移动端），hover 时显示 +
  const InsertGap = ({ onClick, title }: { onClick: () => void; title: string }) => (
    <div
      className="pg-insert-gap group relative flex-shrink-0 w-3 h-[320px] max-md:w-full max-md:h-5 flex items-center justify-center cursor-pointer"
      onClick={onClick}
      title={title}
    >
      <div className="w-px h-full max-md:w-full max-md:h-px bg-gray-200 group-hover:bg-blue-300 transition-colors absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2" />
      <div className="opacity-0 group-hover:opacity-100 transition-all bg-white border border-gray-300 text-gray-500 hover:text-blue-600 hover:border-blue-400 hover:bg-blue-50 rounded-full w-6 h-6 flex items-center justify-center text-sm relative z-10 shadow-sm hover:shadow-md hover:scale-110">+</div>
    </div>
  );

  const activeProgress = workflowProgressCounts(currentProjectStatus);
  const activeProgressStatusText = workflowProgressText(currentProjectStatus || { active_run: activeRun });
  const activeProgressLabel =
    activeProgress.status === "queued"
      ? "等待开始"
      : currentProjectStatus?.progress?.label ||
        (activeRun?.kind === "content_plan"
          ? "内容规划生成进度"
          : activeRun?.kind === "style_proposal"
          ? "风格提案生成进度"
          : activeRun?.kind === "visual_prompts"
          ? "画面描述生成进度"
          : activeRun?.kind === "prototype_generation"
          ? "打样生成进度"
          : "批量生成进度");
  const shouldShowRunProgressEmptyState = Boolean(
    selectedProject &&
    slides.length === 0 &&
    !slidesAreLoading &&
    (hasActiveRun || currentStatus === "prototype" || currentStatus === "generating")
  );

  return (
    <div className="pg-app flex h-screen w-screen bg-gray-50 text-gray-900 overflow-hidden">
      {/* 左栏：项目导航 */}
      {!leftCollapsed && (
        <aside
          className="pg-sidebar border-r bg-white flex flex-col flex-shrink-0 transition-none"
          style={{ width: leftWidth }}
        >
          <div className="pg-sidebar-header p-3 border-b flex items-center justify-between">
            <h1 className="pg-brand text-base font-bold">
              <PptGodLogo />
            </h1>
            <button
              onClick={() => setLeftCollapsed(true)}
              className="pg-icon-button text-gray-400 hover:text-gray-600 text-xs px-1"
              title="收起"
            >
              ◀
            </button>
          </div>
          <div className="p-3">
            <button
              className="pg-primary-button w-full bg-blue-600 text-white text-sm rounded py-1 hover:bg-blue-700"
              onClick={() => setShowCreateModal(true)}
            >
              + 新建项目
            </button>
          </div>
          <div className="flex-1 overflow-auto">
            {projects.length === 0 && (
              <div className="p-4 text-center">
                <div className="pg-empty-icon mx-auto" aria-hidden="true" />
                <div className="text-sm text-gray-500 mb-1">还没有项目</div>
                <div className="text-xs text-gray-400">点击上方「新建项目」开始创建你的第一份 PPT</div>
              </div>
            )}
            {projects.map((p) => (
              <div
                key={p.id}
                onClick={() => selectProject(p)}
                className={`pg-project-card px-3 py-2 border-b cursor-pointer ${
                  selectedProject?.id === p.id ? "bg-blue-50 border-blue-200" : "hover:bg-gray-100"
                }`}
              >
                {editingProjectId === p.id ? (
                  <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
                    <input
                      className="pg-input flex-1 border rounded px-2 py-1 text-sm"
                      value={editTitle}
                      onChange={(e) => setEditTitle(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") handleSaveEdit(p.id);
                        if (e.key === "Escape") setEditingProjectId(null);
                      }}
                      autoFocus
                    />
                    <button
                      onClick={() => handleSaveEdit(p.id)}
                      className="pg-action pg-action-primary text-xs bg-blue-600 text-white px-2 py-1 rounded hover:bg-blue-700"
                    >
                      保存
                    </button>
                    <button
                      onClick={() => setEditingProjectId(null)}
                      className="pg-action pg-action-secondary text-xs bg-gray-200 text-gray-600 px-2 py-1 rounded hover:bg-gray-300"
                    >
                      取消
                    </button>
                  </div>
                ) : (
                  <>
                    <div className="pg-project-row flex items-start justify-between gap-2">
                      <div className="min-w-0 flex-1">
                        <div className="pg-project-title font-medium text-sm truncate flex items-center gap-1.5" title={p.title}>
                          <span className="truncate">{p.title}</span>
                          {p.has_unread_notification && selectedProject?.id !== p.id && (
                            <span className="inline-block w-2 h-2 rounded-full bg-red-500 flex-shrink-0" title={p.unread_notification_message || "有新动态"} />
                          )}
                        </div>
                        <div className="pg-project-meta text-[11px] text-gray-500 mt-0.5 truncate">
                          {statusLabel[p.status] || p.status} · {projectStyleLabel(p)}
                        </div>
                      </div>
                      <div className="pg-project-actions flex items-center gap-1">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleStartEdit(p);
                          }}
                          className="pg-project-icon-button"
                          title="编辑项目名"
                          aria-label="编辑项目名"
                        >
                          <svg viewBox="0 0 24 24" aria-hidden="true">
                            <path d="M4 20h4.5L19 9.5 14.5 5 4 15.5V20Z" />
                            <path d="m13.5 6 4.5 4.5" />
                          </svg>
                        </button>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDeleteProject(p.id);
                          }}
                          className="pg-project-icon-button pg-project-icon-danger"
                          title="删除项目"
                          aria-label="删除项目"
                        >
                          <svg viewBox="0 0 24 24" aria-hidden="true">
                            <path d="M6 7h12" />
                            <path d="M9 7V5h6v2" />
                            <path d="M9 10v7" />
                            <path d="M15 10v7" />
                            <path d="M8 7l1 13h6l1-13" />
                          </svg>
                        </button>
                      </div>
                    </div>
                  </>
                )}
              </div>
            ))}
          </div>
        </aside>
      )}
      {/* 左栏 resizer / 展开按钮 */}
      {leftCollapsed ? (
        <button
          onClick={() => setLeftCollapsed(false)}
          className="pg-collapsed-rail flex-shrink-0 w-7 border-r bg-white hover:bg-gray-50 flex items-center justify-center text-gray-400 hover:text-gray-600 text-xs"
          title="展开项目栏"
        >
          ▶
        </button>
      ) : (
        <div
          onMouseDown={(e) => startResize("left", e)}
          className="pg-resizer w-0.5 flex-shrink-0 cursor-col-resize bg-gray-200 hover:bg-blue-400 active:bg-blue-500 transition-colors"
          title="拖动调节列宽"
        />
      )}

      {/* 中栏：主预览区 */}
      <main className="pg-main flex-1 flex flex-col min-w-0">
        <header className="pg-topbar h-12 border-b border-slate-200 bg-white flex items-center px-4 justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 min-w-0">
              <span className="font-semibold text-sm truncate max-w-[300px]" title={selectedProject?.title}>
                {selectedProject ? selectedProject.title : "预览区"}
              </span>
              {selectedProject && (
                <span className="text-xs px-2.5 py-0.5 rounded-full bg-slate-100 text-slate-600 font-medium">
                  {statusLabel[currentStatus] || currentStatus}
                </span>
              )}
            </div>
            {/* 选页工具栏：内联到标题栏 */}
            {selectedProject && shouldShowPrototypeSelection && (
              <div className="flex items-center gap-2 mt-0.5 text-xs text-slate-600">
                <span className="text-slate-500">{isPrototypeRunActive ? "正在打样：" : "打样页："}</span>
                {!isPrototypeRunActive && (
                  <>
                    <button onClick={selectAll} disabled={!canEditPrototypeSelection} className="text-blue-600 hover:underline font-medium disabled:text-slate-300 disabled:no-underline">全选</button>
                    <button onClick={clearSelection} disabled={!canEditPrototypeSelection} className="text-slate-400 hover:underline disabled:text-slate-300 disabled:no-underline">默认种子页</button>
                    <span className="text-slate-300">|</span>
                  </>
                )}
                <span>{prototypeSelectionSummary}</span>
                <span className="text-slate-400">
                  {isPrototypeRunActive ? "生成中已锁定，完成后可重新打样。" : "勾选卡片可调整本次打样范围。"}
                </span>
              </div>
            )}
            {selectedProject && currentStatus === "prototype_ready" && (
              <div className="text-xs text-slate-400 mt-0.5">
                已生成样张，可先检查效果；需要重做时点击「重新打样」。
              </div>
            )}
          </div>
          <div className="flex items-center gap-2 flex-wrap justify-end">
            {topSecondaryActions.map(renderTopAction)}
            {topPrimaryAction && renderTopAction(topPrimaryAction)}
          </div>
        </header>

        {/* 项目进程时间线 */}
        {selectedProject && (
          <div className="pg-workflow px-6 py-3 bg-gradient-to-r from-slate-50 via-white to-slate-50 border-b border-slate-200">
            <div className="flex items-center">
              {steps.map((step, idx) => {
                const status = stepStatus(idx);
                const canRollback = status === "done";
                const isCurrentLoading = status === "current" && isLoadingStatus;
                return (
                  <div key={step.key} className="flex items-center">
                    <button
                      onClick={() => { if (!canRollback) return; handleRollback(step.key as any); }}
                      disabled={!canRollback || isBusy}
                      className={`pg-workflow-step flex items-center gap-2 px-3 py-1.5 rounded-lg transition-all duration-200 ${
                        status === "current"
                          ? "bg-blue-600 text-white shadow-md shadow-blue-200"
                          : status === "error"
                          ? "bg-red-50 text-red-700 border border-red-200"
                          : canRollback && !isBusy
                          ? "bg-emerald-50 text-emerald-700 border border-emerald-200 hover:bg-emerald-100 hover:shadow-sm cursor-pointer"
                          : "bg-white text-slate-400 border border-slate-200"
                      }`}
                    >
                      <span className={`w-5 h-5 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0 ${
                        status === "current"
                          ? "bg-white/20 text-white"
                          : status === "error"
                          ? "bg-red-500 text-white"
                          : canRollback
                          ? "bg-emerald-500 text-white"
                          : "bg-slate-200 text-slate-500"
                      }`}>
                        {idx + 1}
                      </span>
                      <span className="text-sm font-medium whitespace-nowrap">{step.label}</span>
                      {isCurrentLoading && (
                        <span className="inline-block w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                      )}
                    </button>
                    {idx < steps.length - 1 && (
                      <div className={`w-6 h-px mx-1.5 rounded-full ${idx < displayStepIndex ? "bg-emerald-400" : "bg-slate-200"}`} />
                    )}
                  </div>
                );
              })}
            </div>
            {/* 引导文案 */}
            {getGuidanceText() && (
              <div className="mt-2 text-sm text-slate-500 font-medium">
                {getGuidanceText()}
              </div>
            )}
          </div>
        )}

        {/* 项目素材条：可折叠，默认折叠 */}
        {!isBriefStudioActive && (selectedProject && referenceImages.length > 0 || (selectedProject && currentAgentRole === "visual" && contentPlanConfirmed)) ? (
          <div className="pg-assets-shell border-b border-gray-200">
            {/* 始终显示的紧凑栏，点击 toggle */}
            <div
              className="pg-assets-toggle flex items-center gap-2 px-3 py-1.5 bg-gray-50 cursor-pointer hover:bg-gray-100 transition-colors"
              onClick={() => setAssetsBarExpanded((v) => !v)}
            >
              <span className="min-w-0 truncate text-xs text-gray-600">
                项目素材
                {referenceImages.length > 0 && (
                  <> · {referenceImages.filter(isConfirmedLogoRef).length > 0 && `${referenceImages.filter(isConfirmedLogoRef).length} 个品牌 Logo `}
                    {referenceImages.filter((r) => r.role === "visual_asset").length > 0 && `${referenceImages.filter((r) => r.role === "visual_asset").length} 个可复用素材 `}
                    {referenceImages.filter((r) => r.role === "style_ref").length > 0 && `${referenceImages.filter((r) => r.role === "style_ref").length} 张风格参考 `}
                    {referenceImages.filter((r) => r.role === "template").length > 0 && "版式模板 "}</>
                )}
                {referenceImages.length === 0 && (
                  <span className="text-gray-400"> · 尚未添加</span>
                )}
              </span>
              <span className="ml-auto shrink-0 text-xs text-slate-400">
                {assetsBarExpanded ? "收起 ▲" : "展开 ▼"}
              </span>
            </div>
            {/* 展开态：完整面板 */}
            {assetsBarExpanded && (
              <div>
                <VisualAssetsPanel
                  referenceImages={referenceImages}
                  activeSlide={activeAssetSlide}
                  templateRecommendations={selectedProject?.selected_template_recommendations}
                  templatePages={templatePages}
                  apiBase={API_BASE}
                  showInVisualStage={currentAgentRole === "visual" && contentPlanConfirmed}
                  onUploadLogo={() => logoInputRef.current?.click()}
                  onUploadStyleRef={() => styleRefInputRef.current?.click()}
                  onUploadTemplate={() => templateInputRef.current?.click()}
                  onUploadVisualAsset={() => visualAssetInputRef.current?.click()}
                  onUpdateVisualAsset={async (refId, data) => {
                    if (!selectedProject) return;
                    try {
                      const targetRef = referenceImages.find((r: any) => r.id === refId);
                      await updateReferenceImage(selectedProject.id, refId, data);
                      showToast(targetRef?.role === "logo" ? "Logo 设置已更新" : "可复用素材已更新");
                      await loadReferenceImages(selectedProject.id);
                      if (targetRef?.role === "visual_asset" || targetRef?.role === "logo" || data.review_status) {
                        slides.forEach((s) => markSlideStale(s.id, "content"));
                      }
                      addSystemLog(targetRef?.role === "logo" ? "用户更新了 Logo 角标设置" : "用户更新了可复用素材说明");
                    } catch (err: any) {
                      showToast("更新失败：" + (err.message || "未知错误"), "error");
                    }
                  }}
                  onDelete={async (refId) => {
                    if (!selectedProject) return;
                    try {
                      const deletedRef = referenceImages.find((r) => r.id === refId);
                      await deleteReferenceImage(selectedProject.id, refId);
                      showToast("已删除");
                      await loadReferenceImages(selectedProject.id);
                      if (deletedRef?.role === "visual_asset") {
                        const updated = await loadSlides(selectedProject.id);
                        if (editingSlide) {
                          const fresh = updated.find((s: Slide) => s.id === editingSlide.id);
                          if (fresh) setEditingSlide(fresh);
                        }
                      }
                      if (refId === referenceImages.find((r) => r.role === "template")?.id) {
                        setTemplatePages([]);
                      }
                      if (deletedRef && (deletedRef.role === "style_ref" || deletedRef.role === "logo" || deletedRef.role === "visual_asset")) {
                        slides.forEach((s) => markSlideStale(s.id, "content"));
                      }
                      if (deletedRef) {
                        const roleMap: Record<string, string> = { style_ref: "风格参考", logo: "品牌 Logo", template: "版式模板", visual_asset: "可复用素材" };
                        addSystemLog(`用户删除了项目${roleMap[deletedRef.role] || "素材"}`);
                      }
                      await loadProjects();
                    } catch (err: any) {
                      showToast("删除失败：" + (err.message || "未知错误"), "error");
                    }
                  }}
                  onPinAsset={handlePinAssetToSlide}
                  onUnpinAsset={handleUnpinAssetFromSlide}
                  onUpdateOverlayLayers={handleUpdateOverlayLayers}
                  onImageClick={(url) => {
                    const urls = referenceImages.map((r: any) => {
                      const assetUrl = r.overlay_url || r.url;
                      return String(assetUrl || "").startsWith("http") ? assetUrl : `${API_BASE}${assetUrl}`;
                    });
                    const index = urls.indexOf(url);
                    setGalleryModal({ urls, index: index >= 0 ? index : 0, title: "设计素材" });
                  }}
                />
              </div>
            )}
          </div>
        ) : null}

        <div className="pg-workspace flex-1 overflow-auto p-3">
          {!selectedProject ? (
            <div className="pg-empty-state flex items-center justify-center h-full text-gray-400">
              <div className="pg-flow-empty text-center">
                <div className="pg-flow-command mx-auto mb-8">古希腊掌管 PPT 的神</div>
                <div className="pg-flow-title">开始创作</div>
                <div className="pg-flow-copy">选择一个项目，或从左侧新建一份可编辑的 AI 演示文稿。</div>
              </div>
            </div>
          ) : isBriefStudioActive ? (
            <div
              className="pg-brief-studio h-full"
              onDragOver={(e) => {
                e.preventDefault();
                setIsDragging(true);
              }}
              onDragLeave={(e) => {
                if (!e.currentTarget.contains(e.relatedTarget as Node)) {
                  setIsDragging(false);
                }
              }}
              onDrop={(e) => {
                e.preventDefault();
                setIsDragging(false);
                if (e.dataTransfer.files.length > 0) {
                  handleDropFiles(e.dataTransfer.files);
                }
              }}
            >
              <div className="pg-brief-header">
                <div>
                  <div className="pg-stage-kicker">Brief Studio</div>
                  <h2 className="pg-stage-title">先把要讲的事说完整</h2>
                  <p className="pg-stage-copy">
                    把主题、材料、听众和希望达成的结果写在这里，我会先整理成可编辑的内容规划。
                  </p>
                </div>
              </div>
              <div className="pg-brief-grid">
                <section className="pg-brief-panel pg-brief-panel-main">
                  <input
                    type="file"
                    ref={docInputRef}
                    className="hidden"
                    multiple
                    accept=".pdf,.doc,.docx,.ppt,.pptx,.md,.markdown,.txt,.png,.jpg,.jpeg,.webp,.gif,.svg,.bmp,.tif,.tiff,.heic"
                    onChange={handleUploadDocument}
                  />
                  <div className={`pg-brief-composer ${isDragging ? "pg-brief-composer-dragging" : ""}`}>
                    <div
                      ref={briefEditorRef}
                      className="pg-brief-editor"
                      contentEditable={!chatLoading && !isBusy}
                      suppressContentEditableWarning
                      data-placeholder={"把文件、图片拖进来，然后直接告诉我你想怎么用。\n\n例如：把 [文件] 做成给业务负责人看的升级汇报，参考 [图片] 的视觉气质，重点讲清楚预算、风险和下一步决策。"}
                      onInput={updateBriefEditorState}
                      onClick={handleBriefEditorClick}
                      onDragStart={handleBriefEditorDragStart}
                      onDragOver={(event) => event.preventDefault()}
                      onDrop={handleBriefEditorDrop}
                      onDragEnd={() => { briefDraggedChipRef.current = null; }}
                      onKeyDown={(event) => {
                        if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                          event.preventDefault();
                          dispatchGateAction("generate_content_plan");
                        }
                      }}
                    />
                    {isDragging && (
                      <div className="pg-brief-drop-overlay">
                        松开即可加入 Brief
                      </div>
                    )}
                    <div className="pg-brief-composer-footer">
                      <div className="pg-brief-composer-tools">
                        <button
                          type="button"
                          onClick={() => docInputRef.current?.click()}
                          disabled={uploadingDoc || isBusy || chatLoading}
                          className="pg-brief-add-button"
                          title="添加文件或图片"
                        >
                          +
                        </button>
                        <span>{briefComposerSupportText}</span>
                      </div>
                      <button
                        onClick={() => dispatchGateAction("generate_content_plan")}
                        disabled={!selectedProject || chatLoading || isBusy || uploadingDoc || (!chatInput.trim() && !hasBriefAttachments)}
                        className="pg-action pg-action-primary bg-slate-950 text-white px-4 py-2.5 rounded-lg text-sm font-medium disabled:opacity-50"
                      >
                        {uploadingDoc ? "正在加入材料..." : chatLoading || isBusy ? "规划中..." : "生成内容规划"}
                      </button>
                    </div>
                  </div>
                  <div className="pg-brief-helper">
                    <span>一份好 Brief 最好说清：</span>
                    <ul>
                      <li><strong>给谁看</strong>，比如老板、客户、投资人、内部团队</li>
                      <li><strong>为什么讲</strong>，是汇报进展、争取资源、说服购买，还是沉淀研究</li>
                      <li><strong>要对方决定什么</strong>，比如批准预算、选择方案、启动试点</li>
                      <li><strong>已有材料或限制</strong>，包括必须使用的文件、图片、品牌、页数和语气</li>
                    </ul>
                  </div>
                  <div className="pg-brief-example-row" aria-label="示例填充">
                    <span>不知道怎么写时</span>
                    <button
                      onClick={() => {
                        const example = "听众：业务负责人和管理层。\n\n为什么讲：我们已经完成 2 个团队的 AI Agent 工作流试点，现在要判断是否扩大投入。\n\n希望对方决定：批准未来两个季度的预算和人力，把试点从 2 个团队扩到 8 个团队。\n\n已有材料：试点数据、用户访谈记录、成本测算、现有流程截图，可以上传到这里一起使用。\n\n限制和偏好：整体要克制、可信、面向决策，不要做成技术科普。PPT 需要讲清楚现状问题、机会判断、方案路径、预算、人力投入、风险和下一步计划。";
                        setChatInput(example);
                        requestAnimationFrame(() => {
                          syncBriefEditorValue(example);
                          focusBriefEditorAtEnd();
                        });
                      }}
                      className="pg-brief-example"
                    >
                      填入完整示例
                    </button>
                  </div>
                </section>
              </div>
            </div>
          ) : slidesAreLoading ? (
            <div className="pg-empty-state flex items-center justify-center h-full text-gray-400">
              <div className="pg-flow-empty text-center max-w-md">
                <div className="pg-flow-title">正在打开项目内容</div>
                <div className="pg-flow-copy">页面加载完成后会直接显示；你也可以先在右侧继续查看项目消息。</div>
              </div>
            </div>
          ) : shouldShowRunProgressEmptyState ? (
            <div className="pg-empty-state flex items-center justify-center h-full text-gray-500">
              <div className="pg-flow-empty text-center max-w-md">
                <div className="pg-flow-title">{activeProgressLabel}</div>
                <div className="mt-4 h-2 w-full max-w-xs mx-auto rounded-full bg-slate-200 overflow-hidden">
                  <div
                    className="h-full rounded-full bg-slate-950 transition-all"
                    style={{ width: `${activeProgress.percent}%` }}
                  />
                </div>
                <div className="pg-flow-copy mt-3">
                  {activeProgress.total > 0
                    ? `${activeProgress.current} / ${activeProgress.total} ${activeProgress.unit}完成`
                    : "正在同步最新进度，生成结果会直接出现在这里。"}
                  {activeProgress.failed > 0 ? `，${activeProgress.failed} ${activeProgress.unit}失败` : ""}
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setRightCollapsed(false);
                    setCurrentAgentRole(activeRun?.kind === "content_plan" ? "content" : "visual");
                    chatAutoScrollRef.current = true;
                    requestAnimationFrame(scrollChatToBottom);
                  }}
                  className="pg-action pg-action-secondary mt-4 bg-white text-slate-700 border border-slate-200 px-3 py-1.5 rounded-lg text-sm hover:bg-slate-50"
                >
                  查看最新消息
                </button>
              </div>
            </div>
          ) : slides.length === 0 ? (
            <div className="pg-empty-state flex items-center justify-center h-full text-gray-400">
              <div className="pg-flow-empty text-center max-w-md">
                <div className="pg-flow-title">还没有页面内容</div>
                <div className="pg-flow-copy">上传材料或填写 Brief 后，先生成内容规划。</div>
              </div>
            </div>
          ) : showTemplateRecommender && templatePages.length > 0 ? (
            <div className="pg-screen-panel p-4">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-bold">模板页面推荐</h2>
                <button
                  onClick={() => setShowTemplateRecommender(false)}
                  className="text-sm text-gray-500 hover:text-gray-700"
                >
                  关闭
                </button>
              </div>
              <TemplateRecommender
                pages={templatePages}
                recommendations={{
                  cover: templatePages[0] || null,
                  toc: templatePages[1] || null,
                  content: templatePages[Math.floor(templatePages.length / 2)] || null,
                  ending: templatePages[templatePages.length - 1] || null,
                }}
                onConfirm={async (selected) => {
                  if (!selectedProject) return;
                  try {
                    await updateTemplateRecommendations(selectedProject.id, selected);
                    await loadProjects();
                    const fresh = normalizeProjectsForActiveSelection(await fetchProjects(), selectedProjectIdRef.current);
                    const updated = fresh.find((p: Project) => p.id === selectedProject.id);
                    if (updated) setSelectedProject(clearProjectNotification(updated));
                    setShowTemplateRecommender(false);
                  } catch (err: any) {
                    showToast("保存模板选择失败：" + (err.message || "未知错误"), "error");
                  }
                }}
              />
            </div>
          ) : editingSlide ? (
            <SingleSlideEditor
              key={editingSlide.id}
              slide={editingSlide}
              projectId={selectedProject.id}
              onExit={handleExitEdit}
              onSaved={async () => {
                await loadProjects();
                const updated = await loadSlides(selectedProject.id);
                const current = editingSlideRef.current;
                if (current) {
                  const fresh = updated.find((s: Slide) => s.id === current.id);
                  if (fresh) setEditingSlide(fresh);
                }
              }}
              onDelete={() => {
                if (editingSlide) handleDeleteSlide(editingSlide.id);
                handleExitEdit();
              }}
              onInsertBefore={() => {
                if (editingSlide) handleInsertSlideBefore(editingSlide.id);
              }}
              onInsertAfter={() => {
                if (editingSlide) handleInsertSlideAfter(editingSlide.id);
              }}
              onPrev={handlePrevSlide}
              onNext={handleNextSlide}
              hasPrev={slides.findIndex((s) => s.id === editingSlide.id) > 0}
              hasNext={slides.findIndex((s) => s.id === editingSlide.id) < slides.length - 1}
              typeLabel={typeLabel}
              typeColor={typeColor}
              projectLogo={projectLogo}
              referenceImages={referenceImages}
              imageCacheKey={imageRefreshMap[editingSlide.id]}
              slideVersions={slideVersionsMap[editingSlide.id] || []}
              onRestoreVersion={(versionId) => handleRestoreVersion(editingSlide.id, versionId)}
              onDeleteVersion={(versionId) => handleDeleteVersion(editingSlide.id, versionId)}
              unescapeText={unescapeText}
              onImageClick={(url) => {
                if (url.includes("/uploads/")) {
                  const refUrls = dedupeReferenceImages(editingSlide?.reference_images || []).map((r: any) => `${API_BASE}${r.url}`);
                  const index = refUrls.indexOf(url);
                  setGalleryModal({ urls: refUrls, index: index >= 0 ? index : 0, title: "本页参考图" });
                } else {
                  const gallerySlides = slides
                    .filter((s) => s.status === "completed" && s.image_path)
                    .sort((a, b) => a.page_num - b.page_num);
                  const slideUrls = gallerySlides.map((s) => getSlideImageUrl(s.image_path!, s.status, imageRefreshMap[s.id]));
                  const index = slideUrls.indexOf(url);
                  setGalleryModal({ urls: slideUrls, index: index >= 0 ? index : 0, title: "PPT 预览", slides: gallerySlides, logo: projectLogo });
                }
              }}
              onToast={showToast}
              markSlideStale={markSlideStale}
              staleStatus={staleMap[editingSlide.id]}
              projectStatus={currentStatus}
              onUpdateStale={() => handleUpdateStaleSlides([editingSlide.id], { local: true })}
              onGenerateImages={() => handleGenerateStaleImages([editingSlide.id], { local: true })}
              onSystemLog={addSystemLog}
              onRetry={async (slideId, regeneratePrompt = false) => {
                await handleRetry(slideId, regeneratePrompt);
              }}
              onRegenerateFromEdits={handleRegenerateSlideFromEdits}
            />
          ) : (
            <>
              {hasContentConfirmCta && (
                <div className="pg-stage-decision pg-stage-decision-content">
                  <div>
                    <div className="pg-stage-decision-title">内容规划已完成 · {slides.length} 页</div>
                    <p>请检查页数、标题和顺序；确认后会进入视觉阶段，可先上传 Logo、参考图或版式模板再生成方向。</p>
                  </div>
                  <button
                    onClick={() => dispatchGateAction("confirm_content")}
                    disabled={isBusy || chatLoading || confirmingProjectId === selectedProject?.id}
                    className="pg-action pg-action-primary bg-emerald-600 text-white px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50"
                  >
                    {confirmingProjectId === selectedProject?.id ? "正在介入..." : "确认内容，请视觉总监"}
                  </button>
                </div>
              )}

              {gateContext.mainStageMode === "deck_style" && contentPlanConfirmed && !selectedProject?.selected_style && (
                <div className="pg-stage-dock pg-style-dock">
                  <div className="pg-stage-dock-head">
                    <div>
                      <div className="pg-stage-kicker">视觉方向</div>
                      <h3>{styleDockProposals.length > 0 ? "选择视觉方向" : "生成视觉方向"}</h3>
                      <p>
                        {styleDockProposals.length > 0
                          ? "查看每套方案的配色、字体和页面使用方式；素材补充统一在上方「项目素材」处理。"
                          : "基于当前内容生成三套视觉方向；需要补充素材时，使用上方「项目素材」。"}
                      </p>
                    </div>
                  </div>
                  {styleDockProposals.length > 0 ? (
                    <div className="pg-style-dock-grid">
                      {styleDockProposals.map((proposal, index) => {
                        const proposalKey = `${proposal.name}-${index}`;
                        const isExpanded = expandedStyleProposalKey === proposalKey;
                        const palette = Array.isArray(proposal.palette) ? proposal.palette : [];
                        const mood = stripHexCodes(proposal.mood) || "—";
                        const font = stripHexCodes(proposal.font) || "—";
                        const pageTypeAdaptation = stripHexCodes((proposal as any).page_type_adaptation || "");
                        const contentStyleHint = stripHexCodes((proposal as any).content_style_hint || "");
                        const strategySummary = visualStrategyText(proposal);
                        const bestFor = proposalDecisionField(proposal, "best_for");
                        const tradeoff = proposalDecisionField(proposal, "tradeoff");
                        const visualFocus = proposalDecisionField(proposal, "visual_focus");
                        const summary = stripHexCodes(proposal.description || proposal.mood || "基于当前内容和素材生成的视觉方向。");
                        return (
                          <div key={proposalKey} className={`pg-style-dock-card ${isExpanded ? "is-expanded" : ""}`}>
                            <div className="pg-style-dock-card-top">
                              <span>{proposalChoiceLabel(proposal, index)}</span>
                              <div className="pg-style-swatches">
                                {palette.slice(0, 5).map((c: any, i: number) => (
                                  <i key={i} style={{ backgroundColor: proposalColorValue(c) }} title={proposalColorLabel(c, i)} />
                                ))}
                              </div>
                            </div>
                            <h4>{proposal.name}</h4>
                            {(bestFor || tradeoff || visualFocus) && (
                              <div className="pg-style-dock-decision">
                                {bestFor && (
                                  <div className="pg-style-dock-decision-row">
                                    <b>适合</b>
                                    <span>{bestFor}</span>
                                  </div>
                                )}
                                {tradeoff && (
                                  <div className="pg-style-dock-decision-row is-tradeoff">
                                    <b>取舍</b>
                                    <span>{tradeoff}</span>
                                  </div>
                                )}
                              </div>
                            )}
                            <p className="pg-style-dock-card-summary">{summary}</p>
                            {isExpanded && (
                              <div className="pg-style-dock-detail">
                                {bestFor && (
                                  <div className="pg-style-dock-detail-block">
                                    <b>适合选择</b>
                                    <p>{bestFor}</p>
                                  </div>
                                )}
                                {tradeoff && (
                                  <div className="pg-style-dock-detail-block">
                                    <b>需要接受</b>
                                    <p>{tradeoff}</p>
                                  </div>
                                )}
                                {visualFocus && (
                                  <div className="pg-style-dock-detail-block pg-style-dock-wide-block">
                                    <b>视觉重点</b>
                                    <p>{visualFocus}</p>
                                  </div>
                                )}
                                {strategySummary && (
                                  <div className="pg-style-dock-detail-block pg-style-dock-wide-block">
                                    <b>整体基底</b>
                                    <p>{strategySummary}</p>
                                  </div>
                                )}
                                <div className="pg-style-dock-detail-block pg-style-dock-palette-block">
                                  <b>配色</b>
                                  <div className="pg-style-dock-color-list">
                                    {palette.slice(0, 5).map((color: any, colorIndex: number) => {
                                      const swatch = proposalColorValue(color);
                                      const label = proposalColorLabel(color, colorIndex);
                                      return (
                                        <span key={`${swatch}-${colorIndex}`}>
                                          <i style={{ backgroundColor: swatch }} />
                                          <em>{label}</em>
                                        </span>
                                      );
                                    })}
                                  </div>
                                </div>
                                <div className="pg-style-dock-detail-block">
                                  <b>氛围</b>
                                  <p>{mood}</p>
                                </div>
                                <div className="pg-style-dock-detail-block">
                                  <b>字体</b>
                                  <p>{font}</p>
                                </div>
                                {pageTypeAdaptation && (
                                  <div className="pg-style-dock-detail-block pg-style-dock-wide-block">
                                    <b>页面使用方式</b>
                                    <p>{pageTypeAdaptation}</p>
                                  </div>
                                )}
                                {contentStyleHint && (
                                  <div className="pg-style-dock-detail-block pg-style-dock-wide-block">
                                    <b>内容适配</b>
                                    <p>{contentStyleHint}</p>
                                  </div>
                                )}
                              </div>
                            )}
                            <div className="pg-style-dock-card-actions">
                              <button
                                onClick={() => setExpandedStyleProposalKey(isExpanded ? null : proposalKey)}
                                disabled={isBusy || chatLoading}
                              >
                                {isExpanded ? "收起说明" : "查看详情"}
                              </button>
                              <button onClick={() => dispatchGateAction("confirm_style", { style: proposal })} disabled={isBusy || chatLoading}>
                                确认并生成画面方案
                              </button>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="pg-style-dock-empty">
                      <div>
                        <b>还没有可选方案</b>
                        <p>生成后会在这里显示三套方向。</p>
                      </div>
                      <button
                        onClick={() => dispatchGateAction("generate_style_proposals")}
                        disabled={isBusy || chatLoading}
                        className="pg-action pg-action-primary bg-purple-600 text-white px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50"
                      >
                        生成视觉方向
                      </button>
                    </div>
                  )}
                </div>
              )}

              {gateContext.mainStageMode === "deck_visual" && (
                <div className="pg-stage-dock pg-visual-dock">
                  <div>
                    <div className="pg-stage-kicker">画面方案</div>
                    <h3>{slidesMissingPromptCount > 0 ? "先生成每页画面方案" : "检查每页画面方案，然后打样"}</h3>
                    <p>
                      {slidesMissingPromptCount > 0
                        ? `还有 ${slidesMissingPromptCount} 页缺少画面方案或生图 Prompt。先补齐后再进入打样。`
                        : `当前打样页：${prototypeSelectionSummary}。勾选或取消卡片前的小框，可调整本次需要打样的页面。`}
                    </p>
                  </div>
                  <div className="pg-visual-dock-actions">
                    {slidesMissingPromptCount > 0 ? (
                      <button
                        onClick={() => dispatchGateAction("generate_visual_prompts")}
                        disabled={isBusy || chatLoading}
                        className="pg-action pg-action-primary bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50"
                      >
                        生成画面方案
                      </button>
                    ) : (
                      <>
                        <button onClick={selectAll} disabled={isBusy || chatLoading}>全选打样</button>
                        <button onClick={clearSelection} disabled={isBusy || chatLoading}>默认种子页</button>
                        <button
                          onClick={() => dispatchGateAction("start_prototype")}
                          disabled={isBusy || chatLoading || !canStartPrototypeGeneration}
                          className="pg-action pg-action-primary bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50"
                          title={!canStartPrototypeGeneration ? "请选择至少一页打样页" : undefined}
                        >
                          {prototypePromptTargets.length > 0 ? `打样 ${prototypePromptTargets.length} 页` : "选择打样页"}
                        </button>
                      </>
                    )}
                  </div>
                </div>
              )}

              {gateContext.mainStageMode === "deck_prototype" && (
                <div className="pg-stage-dock pg-prototype-dock">
                  <div>
                    <div className="pg-stage-kicker">打样确认</div>
                    <h3>{isPrototypeRunActive ? "正在生成打样页" : "先确认种子页，再批量生成"}</h3>
                    <p>
                      {isPrototypeRunActive
                        ? `本次打样范围：${prototypeSelectionSummary}。生成中已锁定选页，完成后可重新打样或进入全量生成。`
                        : `本次样张：${prototypeSelectionSummary}。检查风格、构图和文字可读性；满意后生成全部页面，不满意可重新打样。`}
                    </p>
                  </div>
                  <div className="pg-prototype-strip">
                    {slides.filter((s) => s.image_path).slice(0, 3).map((slide) => (
                      <SlideImageWithOverlays
                        key={slide.id}
                        slide={slide}
                        logo={projectLogo}
                        referenceImages={referenceImages}
                        src={getSlideImageUrl(slide.image_path!, slide.status, imageRefreshMap[slide.id])}
                        alt={`Slide ${slide.page_num}`}
                        className="pg-prototype-thumb"
                        imgClassName="w-full h-full object-cover"
                        onClick={() => {
                          const gallerySlides = slides.filter((s) => s.image_path).sort((a, b) => a.page_num - b.page_num);
                          const urls = gallerySlides.map((s) => getSlideImageUrl(s.image_path!, s.status, imageRefreshMap[s.id]));
                          const url = getSlideImageUrl(slide.image_path!, slide.status, imageRefreshMap[slide.id]);
                          setGalleryModal({ urls, index: Math.max(0, urls.indexOf(url)), title: "打样预览", slides: gallerySlides, logo: projectLogo });
                        }}
                      />
                    ))}
                  </div>
                  <div className="pg-prototype-actions">
                    <button onClick={() => dispatchGateAction("resample_prototype")} disabled={isBusy || chatLoading}>重新打样</button>
                    <button
                      onClick={() => dispatchGateAction("confirm_prototype")}
                      disabled={isBusy || chatLoading}
                      className="pg-action pg-action-primary bg-rose-600 text-white px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50"
                    >
                      确认打样，生成全部
                    </button>
                  </div>
                </div>
              )}

              {gateContext.mainStageMode === "deck_final" && (
                <div className="pg-stage-decision pg-stage-decision-final">
                  <div>
                    <div className="pg-stage-decision-title">
                      {currentStatus === "completed" ? "整套作品已生成" : "正在批量生成"}
                    </div>
                    <p>{slides.filter((s) => s.image_path).length} / {slides.length} 页已有画面。失败页可在卡片上单独重试。</p>
                  </div>
                  {currentStatus === "completed" && selectedProject && (
                    <a href={getDownloadUrl(selectedProject.id)} className="pg-action pg-action-primary bg-slate-950 text-white px-4 py-2 rounded-lg text-sm font-medium">
                      下载 PPTX
                    </a>
                  )}
                </div>
              )}

              {/* 风格已选定：紧凑条，可展开 */}
              {selectedProject?.selected_style && (
                currentStatus === "visual_ready" ||
                currentStatus === "prompt_ready" ||
                currentStatus === "generating" ||
                currentStatus === "prototype_ready" ||
                currentStatus === "completed"
              ) && (
                <div className="pg-style-bar mb-2 bg-indigo-50 border border-indigo-200 rounded-lg overflow-hidden">
                  {/* 紧凑栏（始终显示） */}
                  <div
                    className="pg-style-bar-toggle flex items-center gap-2 px-3 py-1.5 cursor-pointer hover:bg-indigo-100/50 transition-colors"
                    onClick={() => setStyleBarExpanded((v) => !v)}
                  >
                    <span className="pg-style-bar-title text-xs font-medium text-indigo-800 truncate">
                      风格：{selectedProject.selected_style.name}
                    </span>
                    {selectedProject.selected_style.palette && (
                      <div className="flex gap-0.5 ml-1">
                        {selectedProject.selected_style.palette.slice(0, 4).map((c: any, i: number) => {
                          const color = typeof c === "string" ? c : c.hex;
                          return (
                            <div
                              key={i}
                              className="w-3 h-3 rounded-full border border-white"
                              style={{ backgroundColor: color }}
                            />
                          );
                        })}
                      </div>
                    )}
                    <span className="pg-style-bar-meta ml-auto text-2xs text-indigo-400">
                      {styleBarExpanded ? "收起" : "展开"}
                    </span>
                  </div>
                  {/* 展开详情 */}
                  {styleBarExpanded && (
                    <div className="pg-style-bar-detail px-3 pb-2 border-t border-indigo-100">
                      {selectedProject.selected_style.palette && (
                        <div className="flex items-center gap-2 py-1.5">
                          <div className="flex gap-1">
                            {selectedProject.selected_style.palette.slice(0, 5).map((c: any, i: number) => {
                              const color = typeof c === "string" ? c : c.hex;
                              return (
                                <div
                                  key={i}
                                  className="w-4 h-4 rounded-full border border-white shadow-sm"
                                  style={{ backgroundColor: color }}
                                  title={typeof c === "string" ? c : c.name}
                                />
                              );
                            })}
                          </div>
                        </div>
                      )}
                      <div className="pg-style-bar-copy text-[11px] text-indigo-700">
                        氛围：{selectedProject.selected_style.mood || "—"} · 字体：{selectedProject.selected_style.font || "—"}
                      </div>
                      {visualStrategyText(selectedProject.selected_style) && (
                        <p className="pg-style-bar-copy text-[11px] text-indigo-700 mt-0.5 leading-relaxed">
                          整体基底：{visualStrategyText(selectedProject.selected_style)}
                        </p>
                      )}
                      {selectedProject.selected_style.description && (
                        <p className="pg-style-bar-copy text-[11px] text-indigo-600 mt-0.5 leading-relaxed">
                          {selectedProject.selected_style.description}
                        </p>
                      )}
                    </div>
                  )}
                </div>
              )}
              <div className="pg-slide-grid flex flex-wrap w-full">
                {/* 第一页之前：悬浮插入区 */}
                {slides.length > 0 && !isBusy && !chatLoading && (
                  <InsertGap
                    onClick={() => handleInsertSlideBefore(slides[0].id)}
                    title="在第一页之前插入"
                  />
                )}
                {slides.map((slide, index) => {
                const content = slide.content_json || {};
                const text = content.text_content || {
                  headline: content.title,
                  subhead: content.subtitle,
                  body: content.bullets || content.body,
                };
                const visual = slide.visual_json || {};
                const hasVisualDescription = Boolean(visual.visual_description && String(visual.visual_description).trim());
                const hasPromptText = Boolean(slide.prompt_text && String(slide.prompt_text).trim());
                const isPrototypePageChecked = visiblePrototypePageSet.has(slide.page_num);
                const isLast = index === slides.length - 1;
                return (
                  <Fragment key={slide.id}>
                  <div
                    draggable={!isBusy && !chatLoading}
                    onDragStart={() => {
                      if (isBusy || chatLoading) return;
                      setDragSlideId(slide.id);
                    }}
                    onDragOver={(e) => {
                      e.preventDefault();
                      if (dragSlideId && dragSlideId !== slide.id) {
                        setDragOverSlideId(slide.id);
                      }
                    }}
                    onDragLeave={() => {
                      setDragOverSlideId(null);
                    }}
                    onDrop={(e) => {
                      e.preventDefault();
                      if (isBusy || chatLoading) {
                        setDragSlideId(null);
                        setDragOverSlideId(null);
                        return;
                      }
                      if (!dragSlideId || dragSlideId === slide.id) {
                        setDragSlideId(null);
                        setDragOverSlideId(null);
                        return;
                      }
                      const fromIndex = slides.findIndex((s) => s.id === dragSlideId);
                      const toIndex = slides.findIndex((s) => s.id === slide.id);
                      if (fromIndex === -1 || toIndex === -1) return;
                      const newOrder = [...slides];
                      const [moved] = newOrder.splice(fromIndex, 1);
                      newOrder.splice(toIndex, 0, moved);
                      handleReorder(newOrder);
                      setDragSlideId(null);
                      setDragOverSlideId(null);
                    }}
                    onDragEnd={() => {
                      setDragSlideId(null);
                      setDragOverSlideId(null);
                    }}
                    onClick={() => {
                      if (!isBusy && !chatLoading) {
                        // 进入详情页：如果该页已生成图片，右侧自动切到微调 Agent
                        if (slide.status === "completed" && slide.image_path) {
                          abortActiveChat(true);
                          setCurrentAgentRole("finetune");
                          setFinetuneTargetSlideId(slide.id);
                          ensureFinetuneGreetingForSlide(slide.id);
                          loadSlideVersions(slide.id);
                        }
                        handleEnterEdit(slide);
                      }
                    }}
                    className={`pg-slide-card group relative bg-white rounded-lg border border-slate-200 p-3 shadow-sm flex flex-col cursor-pointer hover:shadow-lg hover:border-blue-400 transition-all h-[320px] overflow-hidden w-[calc((100%-4.5rem)/3)] min-w-[260px] flex-shrink-0 max-md:w-full ${
                      isPrototypePageChecked && shouldShowPrototypeSelection
                        ? "ring-2 ring-blue-400"
                        : ""
                    } ${finetuneTargetSlideId === slide.id && currentAgentRole === "finetune" ? "ring-2 ring-amber-400 border-amber-300" : ""} ${dragOverSlideId === slide.id ? "border-dashed border-blue-400 bg-blue-50" : ""} ${dragSlideId === slide.id ? "opacity-50" : ""}`}
                  >
                    <div className="flex items-center justify-between mb-1 shrink-0">
                      <div className="flex items-center gap-1.5">
                        {shouldShowPrototypeSelection && (
                          <input
                            type="checkbox"
                            checked={isPrototypePageChecked}
                            disabled={!canEditPrototypeSelection}
                            title={canEditPrototypeSelection ? "勾选本页加入打样范围" : "当前任务进行中，打样页已锁定"}
                            onClick={(e) => e.stopPropagation()}
                            onChange={(e) => {
                              e.stopPropagation();
                              if (!canEditPrototypeSelection) return;
                              togglePage(slide.page_num);
                            }}
                            className={canEditPrototypeSelection ? "cursor-pointer" : "cursor-not-allowed opacity-60"}
                          />
                        )}
                        <span className="text-xs text-slate-400 font-mono">P{slide.page_num}</span>
                        {statusText[slide.status] && <span className="text-sm">{statusText[slide.status]}</span>}
                      </div>
                      <div className="flex items-center gap-1">
                        <SlideReadinessIcons hasVisual={hasVisualDescription} hasPrompt={hasPromptText} />
                        <span className={`text-xs px-2 py-0.5 rounded font-medium leading-none ${typeColor[slide.type] || "bg-gray-100"}`}>
                          {typeLabel[slide.type] || slide.type}
                        </span>
                      </div>
                    </div>
                    {/* 文字内容区：无图时填满整张卡片，有图时压缩让位给图片 */}
                    <div className={`flex flex-col gap-0.5 ${slide.image_path ? "shrink-0 min-h-[4.5rem]" : "flex-1 min-h-0 overflow-hidden"}`}>
                      {/* 标题：最大最粗，允许两行 */}
                      <h3 className="font-bold text-slate-900 text-sm leading-snug" style={{ maxHeight: "2.5rem", overflow: "hidden" }}>{text.headline || "无标题"}</h3>
                      {/* 副标题：灰色，字号稍小，允许一行 */}
                      {text.subhead && (
                        <p className="text-slate-400 text-xs" style={{ maxHeight: "1.25rem", overflow: "hidden" }}>{text.subhead}</p>
                      )}
                      {/* 正文区：有图时不显示，无图时填满剩余空间可滚动 */}
                      {!slide.image_path && text.body && (
                        (typeof text.body === "string" && text.body.trim()) ||
                        (Array.isArray(text.body) && text.body.length > 0)
                      ) && (
                        <div
                          className="flex-1 min-h-0 overflow-y-auto text-xs text-slate-500 leading-relaxed"
                        >
                          {typeof text.body === "string" ? (
                            <div dangerouslySetInnerHTML={{ __html: renderMarkdown(text.body) }} />
                          ) : (
                            <ul className="space-y-0.5">
                              {text.body.map((item: any, i: number) => (
                                <li key={i} className="flex gap-1">
                                  <span className="text-slate-400 shrink-0">·</span>
                                  <span className="flex-1">{typeof item === "string" ? item : item?.content || JSON.stringify(item)}</span>
                                </li>
                              ))}
                            </ul>
                          )}
                        </div>
                      )}
                    </div>

                    {/* 生成图：占满弹性空间，完整可见，hover 悬浮效果 */}
                    {slide.image_path && (
                      <SlideImageWithOverlays
                        slide={slide}
                        logo={projectLogo}
                        referenceImages={referenceImages}
                        src={getSlideImageUrl(slide.image_path, slide.status, imageRefreshMap[slide.id])}
                        alt={"Slide " + slide.page_num}
                        className="flex-1 min-h-0 w-full rounded-md overflow-hidden cursor-pointer mb-1 border border-slate-100 group/img hover:shadow-md hover:border-blue-300 transition-all duration-200"
                        imgClassName="w-full h-full object-contain bg-slate-50 group-hover/img:scale-[1.01] transition-transform duration-300"
                        onClick={(e) => {
                          e.stopPropagation();
                          const gallerySlides = slides
                            .filter((s) => s.status === "completed" && s.image_path)
                            .sort((a, b) => a.page_num - b.page_num);
                          const allUrls = gallerySlides.map((s) => getSlideImageUrl(s.image_path!, s.status, imageRefreshMap[s.id]));
                          const url = getSlideImageUrl(slide.image_path!, slide.status, imageRefreshMap[slide.id]);
                          const index = allUrls.indexOf(url);
                          setGalleryModal({ urls: allUrls, index: index >= 0 ? index : 0, title: "PPT 预览", slides: gallerySlides, logo: projectLogo });
                        }}
                        onError={(e) => {
                          const el = e.target as HTMLImageElement;
                          el.style.display = "none";
                          el.parentElement!.innerHTML = '<div class="w-full h-full flex items-center justify-center text-xs text-gray-400 bg-gray-100">图片加载失败</div>';
                        }}
                      />
                    )}

                    {/* 版本历史缩略图（单页微调时显示） */}
                    {(() => {
                      const versions = slideVersionsMap[slide.id] || [];
                      if (versions.length === 0) return null;
                      return (
                        <div className="shrink-0 flex items-center gap-1 mb-1 overflow-x-auto">
                          <span className="text-2xs text-slate-400 flex-shrink-0">历史：</span>
                          {versions.map((v: any) => (
                            <div key={v.id} className="relative group/ver flex-shrink-0">
                              <img
                                src={`${API_BASE}${v.image_url}`}
                                alt={`版本 ${v.version_number}`}
                                className="w-8 h-5 rounded object-cover border border-slate-200 cursor-pointer hover:border-amber-400 hover:ring-1 hover:ring-amber-300 transition-all"
                                title={`版本 ${v.version_number} — 点击恢复`}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleRestoreVersion(slide.id, v.id);
                                }}
                                onError={(e) => {
                                  (e.target as HTMLImageElement).style.display = "none";
                                }}
                              />
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleDeleteVersion(slide.id, v.id);
                                }}
                                className="absolute -top-1 -right-1 w-3.5 h-3.5 bg-red-500 text-white rounded-full text-[8px] flex items-center justify-center opacity-0 group-hover/ver:opacity-100 transition-opacity"
                                title="删除此版本"
                              >
                                X
                              </button>
                            </div>
                          ))}
                        </div>
                      );
                    })()}

                    {/* 底部栏：参考图 + 页面操作 */}
                    <div className="shrink-0">
                      {/* 页面级参考图（紧凑模式） */}
                      <div className="flex min-h-7 items-center gap-1 shrink-0">
                        <div className="flex min-w-0 flex-1 items-center gap-1">
                          {dedupeReferenceImages(slide.reference_images || []).length > 0 && (
                            <div className="flex gap-0.5 flex-nowrap overflow-x-auto">
                              {dedupeReferenceImages(slide.reference_images || []).map((ref: any) => (
                                <div key={ref.id} className="relative group flex-shrink-0">
                                  <img
                                    src={`${API_BASE}${ref.url}`}
                                    alt="ref"
                                    className="w-7 h-7 rounded object-cover border cursor-pointer"
                                    title="AI 参考图 — 点击查看大图"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      const allRefUrls = slides
                                        .flatMap((s) => dedupeReferenceImages(s.reference_images || []).map((r: any) => `${API_BASE}${r.url}`))
                                        .filter((v, i, a) => a.indexOf(v) === i);
                                      const url = `${API_BASE}${ref.url}`;
                                      const index = allRefUrls.indexOf(url);
                                      setGalleryModal({ urls: allRefUrls, index: index >= 0 ? index : 0, title: "本页参考图" });
                                    }}
                                    onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                                  />
                                  {/* hover 删除按钮 */}
                                  <button
                                    onClick={async (e) => {
                                      e.stopPropagation();
                                      if (!selectedProject) return;
                                      try {
                                        await deleteReferenceImage(selectedProject.id, ref.id);
                                        markSlideStale(slide.id, "visual");
                                        showToast("已删除");
                                        await loadProjects();
                                        await loadSlides(selectedProject.id);
                                        addSystemLog(`用户删除了第 ${slide.page_num} 页的参考图`);
                                      } catch (err: any) {
                                        showToast("删除失败：" + (err.message || "未知错误"), "error");
                                      }
                                    }}
                                    className="absolute -top-1 -right-1 h-3.5 bg-red-500 text-white text-2xs rounded-full items-center justify-center hidden group-hover:flex shadow-sm px-0.5"
                                    title="删除"
                                  >
                                    删
                                  </button>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                        {!isBusy && !chatLoading && (
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleDeleteSlide(slide.id);
                            }}
                            className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full border border-red-100 bg-white text-red-500 shadow-sm transition-all hover:border-red-200 hover:bg-red-50 hover:text-red-600 focus:outline-none focus:ring-2 focus:ring-red-200"
                            title={`删除第 ${slide.page_num} 页`}
                            aria-label={`删除第 ${slide.page_num} 页`}
                          >
                            <svg viewBox="0 0 24 24" aria-hidden="true" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M6 7h12" />
                              <path d="M9 7V5h6v2" />
                              <path d="M9 10v7" />
                              <path d="M15 10v7" />
                              <path d="M8 7l1 13h6l1-13" />
                            </svg>
                          </button>
                        )}
                      </div>

                      {/* 重试按钮 */}
                      {slide.status === "failed" && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleRetry(slide.id);
                          }}
                          disabled={isBusy || chatLoading}
                          className="mt-1 text-xs bg-red-50 text-red-600 px-2 py-1 rounded hover:bg-red-100 self-start disabled:opacity-50 leading-none"
                        >
                          {isBusy ? "重试中..." : "重试"}
                        </button>
                      )}
                    </div>

                  </div>
                  {/* 每张卡片后的间隙插入区 */}
                  {!isBusy && !chatLoading && (
                    <InsertGap
                      onClick={() => handleInsertSlideAfter(slide.id)}
                      title={isLast ? "在最后一页之后插入" : "在两页之间插入"}
                    />
                  )}
                  </Fragment>
                );
              })}
            </div>
          </>)}
        </div>
      </main>

      {/* Agent 聊天面板 */}
      {!isBriefStudioActive && rightCollapsed && (
        <button
          onClick={() => setRightCollapsed(false)}
          className="pg-collapsed-rail flex-shrink-0 w-7 border-l bg-white hover:bg-gray-50 flex items-center justify-center text-gray-400 hover:text-gray-600 text-xs"
          title="展开智能助手"
        >
          ◀
        </button>
      )}
      {!isBriefStudioActive && !rightCollapsed && (
        <div
          onMouseDown={(e) => startResize("right", e)}
          className="pg-resizer w-0.5 flex-shrink-0 cursor-col-resize bg-gray-200 hover:bg-blue-400 active:bg-blue-500 transition-colors"
          title="拖动调节列宽"
        />
      )}
      {!isBriefStudioActive && !rightCollapsed && (
      <aside
        className={`pg-oracle-panel flex-shrink-0 border-l flex flex-col ${isDragging ? "bg-blue-50/50 ring-2 ring-blue-300 ring-inset" : "bg-white"}`}
        style={{ width: rightWidth }}
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node)) {
            setIsDragging(false);
          }
        }}
        onDrop={(e) => {
          e.preventDefault();
          setIsDragging(false);
          if (e.dataTransfer.files.length > 0) {
            handleDropFiles(e.dataTransfer.files);
          }
        }}
      >
        {/* Agent 切换栏 */}
        <div className="pg-oracle-header px-4 py-3 border-b bg-slate-50/50">
          <div className="flex items-center justify-between mb-2.5">
            <span className="font-semibold text-sm text-slate-700">
              {currentAgentRole === "finetune" ? "微调工作台" : "智能助手"}
            </span>
            <div className="flex items-center gap-1">
              {chatMessages.length > 0 && (
                <button
                  onClick={() => {
                    showConfirm("确定要清空当前 Agent 的聊天记录吗？项目内容不会被删除。").then((confirmed) => {
                      if (confirmed) {
                        setActiveChatMessages([]);
                        showToast("聊天记录已清空", "success");
                      }
                    });
                  }}
                  className="text-slate-400 hover:text-red-500 text-sm px-1.5 py-0.5 rounded hover:bg-red-50 transition-colors"
                  title="清空对话"
                >
                  清空
                </button>
              )}
              <button
                onClick={() => setRightCollapsed(true)}
                className="text-slate-400 hover:text-slate-600 text-sm px-1"
                title="收起"
              >
                ▶
              </button>
            </div>
          </div>
          {/* 三 Agent 标签切换 */}
          <div className="pg-agent-tabs flex items-center gap-1.5">
            <button
              onClick={() => {
                if (currentAgentRole !== "content") {
                  abortActiveChat(true);
                  setCurrentAgentRole("content");
                  ensureContentGreetingIfNeeded();
                }
              }}
                className={`pg-agent-tab flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-sm font-medium transition-all ${
                currentAgentRole === "content"
                  ? "bg-blue-100 text-blue-700 ring-1 ring-blue-300"
                  : "bg-white text-slate-500 hover:bg-slate-100 border border-slate-200"
              }`}
            >
              <span>内容总监</span>
            </button>
            <button
              onClick={() => {
                if (!contentPlanConfirmed) {
                  showToast("请先确认内容规划，再切换到视觉总监", "info");
                  return;
                }
                if (currentAgentRole !== "visual") {
                  abortActiveChat(true);
                  setCurrentAgentRole("visual");
                  ensureVisualGreetingIfNeeded();
                }
              }}
              disabled={!contentPlanConfirmed}
                className={`pg-agent-tab flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-sm font-medium transition-all ${
                !contentPlanConfirmed
                  ? "bg-slate-50 text-slate-300 cursor-not-allowed"
                  : currentAgentRole === "visual"
                  ? "bg-purple-100 text-purple-700 ring-1 ring-purple-300"
                  : "bg-white text-slate-500 hover:bg-slate-100 border border-slate-200"
              }`}
            >
              <span>视觉总监</span>
            </button>
            <button
              onClick={() => {
                const hasAnyCompletedSlide = slides.some((s) => s.status === "completed" && s.image_path);
                if (!selectedProject || !hasAnyCompletedSlide) {
                  showToast("至少需要有一页生成图片后才能使用单页微调", "info");
                  return;
                }
                if (currentAgentRole !== "finetune") {
                  abortActiveChat(true);
                  setCurrentAgentRole("finetune");
                  const defaultSlide =
                    editingSlide && editingSlide.status === "completed" && editingSlide.image_path
                      ? editingSlide
                      : slides.find((s) => s.status === "completed" && s.image_path);
                  if (defaultSlide) {
                    setFinetuneTargetSlideId(defaultSlide.id);
                    ensureFinetuneGreetingForSlide(defaultSlide.id);
                    loadSlideVersions(defaultSlide.id);
                  } else {
                    setFinetuneTargetSlideId(null);
                  }
                  // 自动为已完成页加载历史版本
                  slides.filter((s) => s.status === "completed" && s.image_path).forEach((s) => {
                    loadSlideVersions(s.id);
                  });
                }
              }}
              disabled={!selectedProject || !slides.some((s) => s.status === "completed" && s.image_path)}
                className={`pg-agent-tab flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-sm font-medium transition-all ${
                !selectedProject || !slides.some((s) => s.status === "completed" && s.image_path)
                  ? "bg-slate-50 text-slate-300 cursor-not-allowed"
                  : currentAgentRole === "finetune"
                  ? "bg-amber-100 text-amber-700 ring-1 ring-amber-300"
                  : "bg-white text-slate-500 hover:bg-slate-100 border border-slate-200"
              }`}
            >
              <span>单页微调</span>
            </button>
          </div>
        </div>
          {selectedProject && slides.length > 0 && currentStatus === "planning" && (
            <div className="pg-agent-modebar px-4 py-2 border-b bg-white flex items-center justify-end">
              <div className="flex items-center gap-1">
                  <button
                    onClick={handleGlobalUndo}
                    disabled={!canGlobalUndo || !!operatingProjectId || chatLoading}
                  title="撤销 (Ctrl+Z)"
                  className={`text-xs px-1.5 py-0.5 rounded transition-colors ${
                    canGlobalUndo && !operatingProjectId && !chatLoading
                      ? "text-gray-600 hover:bg-gray-200 hover:text-gray-900"
                      : "text-gray-300 cursor-not-allowed"
                  }`}
                >
                  撤销
                </button>
                <button
                  onClick={handleGlobalRedo}
                  disabled={!canGlobalRedo || !!operatingProjectId || chatLoading}
                  title="重做 (Ctrl+Shift+Z)"
                  className={`text-xs px-1.5 py-0.5 rounded transition-colors ${
                    canGlobalRedo && !operatingProjectId && !chatLoading
                      ? "text-gray-600 hover:bg-gray-200 hover:text-gray-900"
                      : "text-gray-300 cursor-not-allowed"
                  }`}
                  >
                    重做
                  </button>
              </div>
            </div>
          )}
        {currentStageNudge && currentStageNudge.role === currentAgentRole && (
          <div className={`pg-current-step px-4 py-3 border-b ${
            currentStageNudge.tone === "warning"
              ? "bg-amber-50 border-amber-200"
              : currentStageNudge.tone === "final"
              ? "bg-emerald-50 border-emerald-200"
              : currentStageNudge.tone === "visual"
              ? "bg-purple-50 border-purple-100"
              : "bg-blue-50 border-blue-100"
          }`}>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className={`text-sm font-semibold ${
                  currentStageNudge.tone === "warning"
                    ? "text-amber-800"
                    : currentStageNudge.tone === "final"
                    ? "text-emerald-800"
                    : currentStageNudge.tone === "visual"
                    ? "text-purple-800"
                    : "text-blue-800"
                }`}>
                  {currentStageNudge.title}
                </div>
                <p className="mt-1 text-xs leading-relaxed text-slate-600">{currentStageNudge.body}</p>
              </div>
            </div>
            {(currentStageNudge.primary || currentStageNudge.secondary) && (
              <div className="mt-2 flex flex-wrap gap-2">
                {currentStageNudge.primary && renderNudgeAction(currentStageNudge.primary)}
                {currentStageNudge.secondary && renderNudgeAction(currentStageNudge.secondary)}
              </div>
            )}
          </div>
        )}
        <div
          ref={chatContainerRef}
          className="pg-chat-stream flex-1 overflow-auto space-y-3 p-3"
          onScroll={handleChatScroll}
        >
          {!selectedProject && (
            <div className="pg-oracle-card p-3 rounded text-sm">
              你好！我可以帮你生成 PPT。请先新建或选择一个项目。
            </div>
          )}
          {/* 已上传文档折叠面板 */}
          {selectedProject && documents.length > 0 && (
            <div className="pg-documents-panel bg-gray-50 rounded border border-gray-200 overflow-hidden">
              <button
                onClick={() => setDocumentsExpanded((v) => !v)}
                className="flex items-center gap-2 w-full px-3 py-2 text-xs text-gray-600 hover:bg-gray-100 transition-colors"
              >
                <svg
                  className={`w-3 h-3 transition-transform ${documentsExpanded ? "rotate-90" : ""}`}
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
                <span>已上传 {documents.length} 个文档</span>
                <span className="ml-auto text-2xs text-gray-400">
                  {documentsExpanded ? "收起" : "展开"}
                </span>
              </button>
              {documentsExpanded && (
                <div className="px-3 pb-3 space-y-2">
                  {documents.map((doc) => (
                    <div
                      key={doc.filename}
                      className="flex items-center justify-between text-xs bg-white px-2 py-1.5 rounded border border-gray-200"
                    >
                      <span className="min-w-0">
                        <span className="text-gray-700 truncate max-w-[200px] block" title={doc.filename}>
                          {doc.filename}
                        </span>
                        {["queued", "running"].includes(String(doc.text_parse_status || "")) && (
                          <span className="text-2xs text-blue-600 block">文字后台解析中</span>
                        )}
                        {doc.text_parse_status === "failed" && (
                          <span className="text-2xs text-red-600 block">文字解析失败</span>
                        )}
                        {["queued", "running"].includes(String(doc.asset_extraction_status || "")) && (
                          <span className="text-2xs text-amber-600 block">图片素材后台解析中</span>
                        )}
                        {doc.asset_extraction_status === "completed" && doc.extracted_assets?.total > 0 && (
                          <span className="text-2xs text-emerald-600 block">
                            已解析 {doc.extracted_assets.total} 个图片素材
                          </span>
                        )}
                      </span>
                      <button
                        onClick={() => handleDeleteDocument(doc.filename)}
                        className="text-gray-400 hover:text-red-600 ml-2 shrink-0"
                        title="删除"
                      >
                        删
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {isDragging && (
            <div className="pg-drop-zone flex items-center justify-center h-32 border-2 border-dashed border-blue-400 rounded-lg bg-blue-50 text-blue-600 text-sm">
              松开即可加入本轮对话
            </div>
          )}

          {/* 聊天消息 */}
          {chatMessages.map((msg, i) => {
            if (currentAgentRole === "content" && isLegacyContentGreeting(msg)) return null;
            if (isTransientRunMessage(msg)) return null;
            if (isWorkflowTransitionMessage(msg) && (msg.role === "system" || !msg.gate || msg.gateRevision == null || !isMessageFromCurrentGate(msg))) {
              return null;
            }
            const previousVisibleMessage = [...chatMessages.slice(0, i)]
              .reverse()
              .find((item) => !isTransientRunMessage(item) && !isWorkflowTransitionMessage(item));
            if (msg.role === "system" && previousVisibleMessage?.role === "system" && previousVisibleMessage.content === msg.content) {
              return null;
            }
            const systemContentForVisual =
              msg.role === "system" && currentAgentRole === "visual"
                ? getVisualSystemMessageContent(msg.content)
                : msg.content;
            if (msg.role === "system" && currentAgentRole === "visual" && !systemContentForVisual) {
              return null;
            }
            const rowAlignClass =
              msg.role === "user" ? "justify-end" : msg.role === "system" ? "justify-center" : "justify-start";
            const stackRoleClass =
              msg.role === "user"
                ? "pg-user-message-stack"
                : msg.role === "system"
                ? "pg-system-message-stack"
                : "pg-agent-message-stack";
            const visibleMessageContent =
              msg.role === "user"
                ? getBriefSubmissionDisplayContent(systemContentForVisual, msg.displayContent)
                : systemContentForVisual;
            return (
            <div key={msg.id || i} className={`pg-message-row flex ${rowAlignClass}`}>
              <div className={`pg-message-stack max-w-[80%] group ${stackRoleClass}`}>
                {editingMessageIndex === i ? (
                  <div className="flex flex-col gap-2">
                    <textarea
                      className="pg-input w-full border rounded p-2 text-sm min-h-[60px] resize-none"
                      value={editMessageContent}
                      onChange={(e) => setEditMessageContent(e.target.value)}
                      autoFocus
                    />
                    <div className="flex justify-end gap-2">
                      <button
                        onClick={() => {
                          setEditingMessageIndex(null);
                          setEditMessageContent("");
                        }}
                        className="pg-action pg-action-secondary text-xs bg-gray-200 text-gray-700 px-2 py-1 rounded hover:bg-gray-300"
                      >
                        取消
                      </button>
                      <button
                        onClick={handleSaveMessageEdit}
                        className="pg-action pg-action-primary text-xs bg-blue-600 text-white px-2 py-1 rounded hover:bg-blue-700"
                      >
                        保存并重新发送
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    {msg.role === "agent" && msg.agentRole === "visual" && (
                      <div className="pg-agent-label text-xs text-purple-600 mb-1 font-medium">视觉总监</div>
                    )}
                    {msg.role === "agent" && msg.agentRole === "content" && slides.length > 0 && currentStatus === "planning" && (
                      <div className="pg-agent-label text-xs text-blue-600 mb-1 font-medium">内容总监</div>
                    )}
                    {msg.role === "agent" && msg.agentRole === "finetune" && (
                      <div className="pg-agent-label text-xs text-amber-600 mb-1 font-medium">单页微调</div>
                    )}
                    <div
                      className={`pg-message p-3 rounded text-sm ${
                        msg.role === "user"
                          ? "bg-blue-600 text-white rounded-br-none"
                          : msg.role === "system"
                          ? "pg-system-message bg-gray-50 text-gray-500 text-xs border border-gray-200"
                          : msg.agentRole === "visual"
                          ? "bg-purple-50 text-gray-800 rounded-bl-none markdown-body border-l-2 border-purple-300"
                          : msg.agentRole === "finetune"
                          ? "bg-amber-50 text-gray-800 rounded-bl-none markdown-body border-l-2 border-amber-400"
                          : "bg-gray-100 text-gray-800 rounded-bl-none markdown-body"
                      }`}
                    >
                      {msg.loading ? (
                        <div className="flex items-center gap-2 animate-pulse">
                          <svg
                            className="animate-spin h-4 w-4 text-purple-500"
                            xmlns="http://www.w3.org/2000/svg"
                            fill="none"
                            viewBox="0 0 24 24"
                          >
                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                            <path
                              className="opacity-75"
                              fill="currentColor"
                              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                            />
                          </svg>
                          <span className="text-gray-600 text-sm">{visibleMessageContent}</span>
                        </div>
                      ) : msg.role === "system" ? (
                        <div>
                          <div className="flex items-start gap-1.5">
                            <span className="pg-system-dot" aria-hidden="true" />
                            <span className="whitespace-pre-wrap leading-relaxed">{visibleMessageContent}</span>
                          </div>
                          {msg.attachments && msg.attachments.length > 0 && (
                            <div className="mt-2 flex flex-wrap gap-2 pl-4">
                              {msg.attachments.map((att) => (
                                <div key={att.id} className="flex max-w-full items-center gap-1.5 rounded border border-slate-200 bg-white p-1 pr-2">
                                  <img src={att.url} alt={att.name} className="h-7 w-11 rounded border border-slate-100 object-cover" />
                                  <span className="max-w-[130px] truncate text-2xs text-slate-500">{att.name}</span>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      ) : msg.role === "user" ? (
                        (() => {
                          const parts = visibleMessageContent.split("\n📎 ");
                          const text = parts[0];
                          const attachments = parts.slice(1);
                            return (
                              <div>
                                {text && <div className="whitespace-pre-wrap">{text}</div>}
                              {msg.attachments && msg.attachments.length > 0 && (
                                <div className="flex flex-wrap gap-2 mt-2 pt-2 border-t border-white/20">
                                  {msg.attachments.map((att) => (
                                    <div key={att.id} className="flex items-center gap-1.5 bg-white/15 rounded p-1 pr-2 max-w-full">
                                      <img
                                        src={att.url}
                                        alt={att.name}
                                        className="w-10 h-6 rounded object-cover border border-white/20"
                                      />
                                      <span className="text-2xs text-white/90 truncate max-w-[120px]">{att.name}</span>
                                    </div>
                                  ))}
                                </div>
                              )}
                                {attachments.length > 0 && (
                                <div className="flex flex-wrap gap-1.5 mt-2 pt-2 border-t border-white/20">
                                  {attachments.map((att, idx) => (
                                    <span
                                      key={idx}
                                      className="inline-flex items-center gap-1 text-2xs bg-white/20 text-white px-1.5 py-0.5 rounded"
                                    >
                                      <span className="pg-brief-inline-icon">{getFileTypeBadge(att, "doc")}</span>
                                      {att}
                                    </span>
                                  ))}
                                </div>
                              )}
                            </div>
                          );
                        })()
                      ) : (
                        <div dangerouslySetInnerHTML={{ __html: renderMarkdown(unescapeText(visibleMessageContent), true) }} />
                      )}
                      {msg.role === "agent" && (msg.action === "propose_plan" || msg.action === "generate_plan") && msg.positioning && (() => {
                        const canUsePositioningAction =
                          isMessageFromCurrentGate(msg) &&
                          ["draft", "content"].includes(gateContext.gate);
                        return (
                        <div className="pg-oracle-card mt-3 bg-white border border-blue-200 rounded-lg p-4 shadow-sm">
                          <div className="text-sm font-semibold text-gray-800 mb-2">内容定调</div>
                          <div className="space-y-2 text-xs text-gray-600">
                            <div><span className="font-medium text-gray-700">核心洞察：</span>{msg.positioning.core_thesis}</div>
                            <div><span className="font-medium text-gray-700">结构策略：</span>{msg.positioning.strategy}</div>
                            <div><span className="font-medium text-gray-700">文案调性：</span>{msg.positioning.tone}</div>
                            <div><span className="font-medium text-gray-700">预估页数：</span>约 {msg.positioning.estimated_pages} 页</div>
                            {msg.positioning.key_highlights && msg.positioning.key_highlights.length > 0 && (
                              <div>
                                <span className="font-medium text-gray-700">亮点预览：</span>
                                <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                  {msg.positioning.key_highlights.map((h, idx) => (
                                    <li key={idx}>{h}</li>
                                  ))}
                                </ul>
                              </div>
                            )}
                          </div>
                          {canUsePositioningAction && (
                            <button
                              onClick={async () => {
                                if (!selectedProject || !msg.topic) return;
                                await dispatchGateAction("generate_content_plan", {
                                  topic: msg.topic,
                                  page_count: msg.positioning?.estimated_pages,
                                });
                              }}
                              disabled={isBusy || chatLoading}
                              className="pg-action pg-action-primary mt-3 w-full bg-blue-600 text-white text-sm py-2 rounded hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                              {isBusy ? (
                                <span className="flex items-center justify-center gap-2">
                                  <svg className="animate-spin h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                                  </svg>
                                  生成中...
                                </span>
                              ) : (
                                "开始生成内容规划"
                              )}
                            </button>
                          )}
                        </div>
                        );
                      })()}
                      {msg.role === "agent" && shouldRenderMessageNextAction(msg) && (() => {
                        return (
                          <div className="mt-3 border-t border-slate-200 pt-3">
                            {msg.nextAction!.description && (
                              <div className="text-xs text-slate-500 mb-2">{msg.nextAction!.description}</div>
                            )}
                            <button
                              onClick={() => handleAgentNextAction(msg.nextAction!, msg)}
                              disabled={isBusy || chatLoading}
                              className={`pg-action pg-action-primary w-full text-sm py-2 rounded font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                                msg.agentRole === "visual"
                                  ? "bg-purple-600 text-white hover:bg-purple-700"
                                  : "bg-blue-600 text-white hover:bg-blue-700"
                              }`}
                            >
                              {isBusy ? "处理中..." : msg.nextAction!.label}
                            </button>
                          </div>
                        );
                      })()}
                    </div>
                    {/* 视觉总监的风格提案卡片 - 每条消息携带自己的快照，互不干扰 */}
                    {msg.role === "agent" && msg.agentRole === "visual" && msg.styleProposals && msg.styleProposals.length > 0 && isMessageFromCurrentGate(msg) && (
                      <ChatStyleProposal
                        proposals={msg.styleProposals}
                        onSelect={(proposal) => dispatchGateAction("confirm_style", { style: proposal })}
                        onAdjust={() => {
                          if (!selectedProject) return;
                          appendProjectChatMessage(selectedProject.id, "visual", {
                            role: "agent",
                            content: "👉 请告诉我你的调整方向（如「更商务一点」「配色再暖一些」「想要极简感」），我会基于你的反馈重新生成提案。",
                            agentRole: "visual",
                          });
                        }}
                        disabled={isBusy || chatLoading}
                      />
                    )}
                    {/* 消息操作按钮 */}
                    <div className={`flex gap-1 mt-1 opacity-0 group-hover:opacity-100 transition-opacity ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                      {msg.role === "user" && (
                        <button
                          onClick={() => handleEditMessage(i)}
                          disabled={chatLoading || isBusy}
                          className="pg-subtle-link text-xs text-slate-400 hover:text-blue-600 px-1 disabled:opacity-30 disabled:cursor-not-allowed"
                          title="编辑"
                        >
                          编辑
                        </button>
                      )}
                      <button
                        onClick={() => handleDeleteMessage(i)}
                        disabled={chatLoading || isBusy}
                        className="pg-danger-link text-xs text-slate-400 hover:text-red-600 px-1 disabled:opacity-30 disabled:cursor-not-allowed"
                        title="删除这条及之后的对话"
                      >
                        删除
                      </button>
                    </div>
                  </>
                )}
              </div>
            </div>
            );
          })}
          {chatLoading && (
            <div className="flex justify-start">
              <div className="pg-message pg-thinking-message bg-gray-100 rounded text-sm text-gray-600 rounded-bl-none max-w-[80%] overflow-hidden">
                {/* thinking 过程 — 默认折叠 */}
                {thinkingContent && (
                  <div className="border-b border-gray-200">
                    <button
                      onClick={() => setThinkingExpanded((v) => !v)}
                      className="flex items-center gap-2 w-full px-3 py-2 text-xs text-gray-500 hover:bg-gray-200/50 transition-colors"
                    >
                      <svg
                        className={`w-3 h-3 transition-transform ${thinkingExpanded ? "rotate-90" : ""}`}
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                      </svg>
                      <span className="flex items-center gap-1.5">
                        <svg className="animate-spin h-3 w-3" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                        </svg>
                        助手正在思考...
                      </span>
                      <span className="ml-auto text-2xs text-gray-400">
                        {thinkingExpanded ? "收起" : "展开"}
                      </span>
                    </button>
                    {thinkingExpanded && (
                      <div className="px-3 py-2 bg-gray-50/80 text-xs text-gray-500 whitespace-pre-wrap leading-relaxed max-h-64 overflow-auto">
                        {thinkingContent}
                      </div>
                    )}
                  </div>
                )}
                {!thinkingContent && (
                  <div className="px-3 py-2 flex items-center gap-2">
                    <svg className="animate-spin h-3 w-3 text-gray-400" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                    </svg>
                    <span>助手正在思考...</span>
                  </div>
                )}
              </div>
            </div>
          )}
          {/* 批量生成进度（生图 / 生成提示词）：跟在最新消息之后 */}
            {selectedProject && hasActiveRun && currentProjectStatus && activeRun?.kind !== "content_plan" && (
              <div className="flex justify-start">
                <div className="pg-progress-card bg-purple-50 border border-purple-200 rounded-lg text-sm text-purple-800 rounded-bl-none max-w-[80%] overflow-hidden w-72">
                  <div className="px-3 py-2.5">
                    <div className="font-medium mb-1">
                      {activeProgressLabel}
                    </div>
                    <div className="w-full bg-purple-200 rounded-full h-2 mb-2">
                      <div
                        className="bg-purple-500 h-2 rounded-full transition-all"
                        style={{
                          width: `${activeProgress.percent}%`,
                        }}
                      />
                    </div>
                    <div>
                      {activeProgress.status === "queued"
                        ? activeProgressStatusText
                        : `${activeProgress.current} / ${activeProgress.total} ${activeProgress.unit}完成${activeProgress.failed > 0 ? `，${activeProgress.failed} ${activeProgress.unit}失败` : ""}`}
                    </div>
                    {activeProgress.activePageNums.length > 0 && (
                      <div className="mt-1 text-2xs text-purple-600">
                        {activeProgress.activePageNums.length === 1
                          ? `正在生成第 ${activeProgress.activePageNums[0]} 页`
                          : `正在并行生成第 ${formatPageNums(activeProgress.activePageNums)} 页`}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}
          {/* 内容规划动态进度卡片：仅在项目处于生成中状态时显示，防止过时进度残留 */}
          {selectedProject && currentAgentRole === "content" && activeRun?.kind === "content_plan" && currentContentPlanProgress && currentContentPlanProgress.stage && currentContentPlanProgress.stage !== "error" && (
            <div className="flex justify-start">
              <div className="pg-progress-card bg-blue-50 border border-blue-200 rounded-lg text-sm text-gray-700 rounded-bl-none max-w-[80%] overflow-hidden w-72">
                <div className="px-3 py-2.5 flex items-center gap-2">
                  <svg className="animate-spin h-4 w-4 text-blue-500 flex-shrink-0" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                  <div className="flex-1 min-w-0">
                    <div className="font-medium text-blue-800 text-xs truncate">
                      {currentContentPlanProgress.message || "生成中..."}
                    </div>
                      {Number(currentContentPlanProgress.total ?? currentContentPlanProgress.total_pages ?? 0) > 0 && (
                        <div className="mt-1.5">
                          <div className="flex items-center justify-between text-2xs text-blue-600 mb-0.5">
                            <span>进度</span>
                            <span>
                              {currentContentPlanProgress.current ?? currentContentPlanProgress.current_page ?? 0} / {currentContentPlanProgress.total ?? currentContentPlanProgress.total_pages} {currentContentPlanProgress.unit || "页"}
                            </span>
                          </div>
                          <div className="h-1.5 bg-blue-100 rounded-full overflow-hidden">
                            <div
                              className="h-full bg-blue-500 rounded-full transition-all duration-500 ease-out"
                              style={{
                                width: `${Math.min(100, ((currentContentPlanProgress.current ?? currentContentPlanProgress.current_page ?? 0) / (currentContentPlanProgress.total ?? currentContentPlanProgress.total_pages)) * 100)}%`
                              }}
                            />
                        </div>
                      </div>
                    )}
                  </div>
                </div>
                {currentContentPlanProgress.think && (
                  <div className="px-3 py-2 bg-white/60 text-xs text-gray-500 whitespace-pre-wrap leading-relaxed max-h-32 overflow-auto border-t border-blue-100">
                    {currentContentPlanProgress.think}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
        <div className="border-t p-4">
          {/* 内容规划确认条：常驻在输入框上方 */}
          {selectedProject && slides.length > 0 && currentStatus === "planning" && !contentPlanConfirmed && !hasContentConfirmCta && (
            <div className="mb-3 bg-emerald-50/80 border border-emerald-200 rounded-xl p-4 shadow-sm">
              <div className="flex items-center justify-between mb-2">
                <div className="text-sm text-emerald-800">
                  <span className="font-medium">内容规划已完成</span>
                  <span className="text-emerald-600 ml-1">· {slides.length} 页</span>
                </div>
              </div>
              <button
                onClick={() => dispatchGateAction("confirm_content")}
                disabled={confirmingProjectId === selectedProject?.id || isBusy || chatLoading}
                className="w-full bg-emerald-600 text-white text-sm py-2.5 rounded-lg font-medium hover:bg-emerald-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {confirmingProjectId === selectedProject?.id ? (
                  <span className="flex items-center justify-center gap-2">
                    <svg className="animate-spin h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                    </svg>
                    正在请视觉总监介入...
                  </span>
                ) : (
                  "确认内容，请视觉总监"
                )}
              </button>
              <div className="text-center mt-1.5">
                <span className="text-2xs text-emerald-500">
                  你可以继续调整内容，满意后再点击确认
                </span>
              </div>
            </div>
          )}

          {/* 隐藏的文件输入框：由项目素材条触发（始终渲染，确保 ref 可用） */}
          <input
            type="file"
            ref={styleRefInputRef}
            className="hidden"
            accept="image/*"
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (file && selectedProject) {
                setUploadingStyleRef(true);
                try {
                  await uploadFile(selectedProject.id, file, "style_ref");
                  showToast("风格参考已添加");
                  await loadReferenceImages(selectedProject.id);
                  await loadProjects();
                  slides.forEach((s) => markSlideStale(s.id, "content"));
                  addSystemLog(`用户上传了风格参考「${file.name}」`);
                  if (currentAgentRole === "visual") {
                    appendProjectChatMessage(selectedProject.id, "visual", { role: "user", content: `📎 已上传风格参考：${file.name}`, agentRole: "visual" });
                  }
                } catch (err: any) {
                  showToast("上传失败：" + (err.message || "未知错误"), "error");
                } finally {
                  setUploadingStyleRef(false);
                }
              }
              e.target.value = "";
            }}
          />
          <input
            type="file"
            ref={logoInputRef}
            className="hidden"
            accept="image/*"
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (file && selectedProject) {
                setUploadingLogo(true);
                try {
                  await uploadFile(selectedProject.id, file, "logo", undefined, "original", { logo_anchor: "top-right" });
                  showToast("品牌 Logo 已添加");
                  await loadReferenceImages(selectedProject.id);
                  await loadProjects();
                  slides.forEach((s) => markSlideStale(s.id, "content"));
                  addSystemLog(`用户上传了品牌 Logo「${file.name}」`);
                  if (currentAgentRole === "visual") {
                    appendProjectChatMessage(selectedProject.id, "visual", { role: "user", content: `🎯 已上传品牌 Logo：${file.name}`, agentRole: "visual" });
                  }
                } catch (err: any) {
                  showToast("上传失败：" + (err.message || "未知错误"), "error");
                } finally {
                  setUploadingLogo(false);
                }
              }
              e.target.value = "";
            }}
          />
          <input
            type="file"
            ref={visualAssetInputRef}
            className="hidden"
            accept="image/*"
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (file && selectedProject) {
                setUploadingVisualAsset(true);
                try {
                  await uploadFile(selectedProject.id, file, "visual_asset");
                  showToast("可复用素材已添加");
                  await loadReferenceImages(selectedProject.id);
                  slides.forEach((s) => markSlideStale(s.id, "content"));
                  addSystemLog(`用户上传了可复用素材「${file.name}」`);
                  if (currentAgentRole === "visual") {
                    appendProjectChatMessage(selectedProject.id, "visual", { role: "user", content: `🖼️ 已上传可复用素材：${file.name}`, agentRole: "visual" });
                  }
                } catch (err: any) {
                  showToast("上传失败：" + (err.message || "未知错误"), "error");
                } finally {
                  setUploadingVisualAsset(false);
                }
              }
              e.target.value = "";
            }}
          />
          <input
            type="file"
            ref={templateInputRef}
            className="hidden"
            accept=".ppt,.pptx,.pdf"
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (file && selectedProject) {
                setUploadingTemplate(true);
                try {
                  await extractTemplate(selectedProject.id, file);
                  showToast("版式模板已上传并提取");
                  await loadReferenceImages(selectedProject.id);
                  await loadTemplatePages(selectedProject.id);
                  await loadProjects();
                  slides.forEach((s) => markSlideStale(s.id, "content"));
                  addSystemLog(`用户上传了版式模板「${file.name}」`);
                  if (currentAgentRole === "visual") {
                    appendProjectChatMessage(selectedProject.id, "visual", { role: "user", content: `📑 已上传版式模板：${file.name}`, agentRole: "visual" });
                  }
                } catch (err: any) {
                  showToast("上传失败：" + (err.message || "未知错误"), "error");
                } finally {
                  setUploadingTemplate(false);
                }
              }
              e.target.value = "";
            }}
          />

          {/* 当前消息待发送的附件 */}
          {pendingAttachments.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-3">
              {pendingAttachments.map((filename) => (
                <span
                  key={filename}
                  className="pg-attachment-chip inline-flex items-center gap-1 text-xs bg-blue-50 text-blue-700 px-2 py-1 rounded border border-blue-200"
                >
                  <span className="pg-brief-inline-icon">{getFileTypeBadge(filename, "doc")}</span>
                  <span>{filename}</span>
                  <button
                    onClick={() => setPendingAttachments((prev) => prev.filter((f) => f !== filename))}
                    className="pg-subtle-link text-blue-400 hover:text-blue-900 ml-1"
                    title="移除"
                  >
                    X
                  </button>
                </span>
              ))}
            </div>
          )}
          {pendingChatAttachments.length > 0 && currentAgentRole !== "finetune" && (
            <div className="flex flex-wrap gap-2 mb-3">
              {pendingChatAttachments.map((att) => (
                <div key={att.id} className="pg-attachment-chip inline-flex items-center gap-2 bg-slate-50 text-slate-700 px-2 py-1 rounded border border-slate-200 max-w-full">
                  <img src={att.url} alt={att.name} className="w-10 h-7 rounded object-cover border border-slate-200" />
                  <span className="text-xs truncate max-w-[160px]">{att.name}</span>
                  <button
                    onClick={() => setPendingChatAttachments((prev) => prev.filter((item) => item.id !== att.id))}
                    className="pg-subtle-link text-slate-400 hover:text-slate-900 ml-0.5 text-xs"
                    title="移除"
                  >
                    X
                  </button>
                </div>
              ))}
            </div>
          )}
          {currentAgentRole === "finetune" && finetuneTargetSlideId && (pendingFinetuneAttachmentsMap[finetuneTargetSlideId] || []).length > 0 && (
            <div className="flex flex-wrap gap-2 mb-3">
              {(pendingFinetuneAttachmentsMap[finetuneTargetSlideId] || []).map((att) => (
                <div key={att.id} className="pg-attachment-chip inline-flex items-center gap-2 bg-amber-50 text-amber-800 px-2 py-1 rounded border border-amber-200 max-w-full">
                  <img src={att.url} alt={att.name} className="w-10 h-6 rounded object-cover border border-amber-200" />
                  <span className="text-xs truncate max-w-[160px]">{att.name}</span>
                  <button
                    onClick={() => {
                      setPendingFinetuneAttachmentsMap((prev) => ({
                        ...prev,
                        [finetuneTargetSlideId]: (prev[finetuneTargetSlideId] || []).filter((item) => item.id !== att.id),
                      }));
                    }}
                    className="pg-subtle-link text-amber-500 hover:text-amber-900 ml-0.5 text-xs"
                    title="移除"
                  >
                    X
                  </button>
                </div>
              ))}
            </div>
          )}
          {/* 单页微调：当前目标页 */}
          {currentAgentRole === "finetune" && finetuneTargetSlideId && (() => {
            const finetuneSlide = slides.find((s) => s.id === finetuneTargetSlideId);
            if (!finetuneSlide) return null;
            return (
              <div className="pg-finetune-target mb-3 p-2 bg-amber-50 border border-amber-200 rounded-lg">
                <div className="flex items-center gap-2">
                  {finetuneSlide.image_path ? (
                    <img
                      src={getSlideImageUrl(finetuneSlide.image_path, finetuneSlide.status, imageRefreshMap[finetuneSlide.id])}
                      alt={`当前微调: 第${finetuneSlide.page_num}页`}
                      className="w-12 h-7 rounded object-cover border border-amber-300 flex-shrink-0"
                    />
                  ) : (
                    <div className="w-12 h-7 rounded bg-slate-200 flex items-center justify-center text-2xs text-slate-400 flex-shrink-0">
                      无图
                    </div>
                  )}
                  <div className="flex-1 min-w-0">
                    <div className="text-xs text-amber-800 font-medium truncate">第 {finetuneSlide.page_num} 页</div>
                    <div className="text-2xs text-amber-500 truncate">当前页会自动作为底图</div>
                  </div>
                  <button
                    onClick={() => handleUploadPageRef(finetuneTargetSlideId)}
                    disabled={isBusy || chatLoading}
                    className="pg-icon-action text-amber-700 hover:bg-amber-100 border-amber-200 disabled:opacity-50 flex-shrink-0"
                    title="添加参考图到本轮消息"
                    aria-label="添加参考图到本轮消息"
                  >
                    <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                      <path d="M12 5v14" />
                      <path d="M5 12h14" />
                    </svg>
                  </button>
                </div>
              </div>
            );
          })()}
          {currentAgentRole === "finetune" && !finetuneTargetSlideId && (
            <div className="mb-3 p-2 bg-slate-50 border border-dashed border-slate-300 rounded-lg text-center">
              <span className="text-xs text-slate-400">请先在左侧点击一张幻灯片作为微调目标</span>
            </div>
          )}
          {isContentPlanRunActive && currentAgentRole === "content" ? (
            <div className="pg-agent-run-control">
              <div className="pg-agent-run-copy">
                <span className="pg-agent-run-kicker">Brief 已提交</span>
                <span>正在整理页面结构，请稍候；需要取消可以停止生成。</span>
              </div>
              <button
                onClick={handleStopGeneration}
                className="pg-action pg-action-danger pg-agent-run-stop"
                title="停止生成"
              >
                <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
                  <rect x="6" y="6" width="12" height="12" rx="1" />
                </svg>
                停止生成
              </button>
            </div>
            ) : (
            <div className="pg-composer-shell">
              {currentAgentRole !== "finetune" && slides.length > 0 && (
                <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                  <span>作用范围：{composerRequestContext.scopeLabel}</span>
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => setAgentMode("page")}
                      disabled={!editingSlide}
                      className={`px-2 py-1 rounded-md border transition-colors ${
                        composerRequestContext.scope === "current_slide"
                          ? "bg-blue-600 border-blue-600 text-white"
                          : "bg-white border-slate-200 text-slate-600 hover:bg-slate-50 disabled:text-slate-300 disabled:hover:bg-white"
                      }`}
                      title={editingSlide ? `默认作用于第 ${editingSlide.page_num} 页` : "进入单页后可锁定当前页"}
                    >
                      当前页
                    </button>
                    <button
                      type="button"
                      onClick={() => setAgentMode("global")}
                      className={`px-2 py-1 rounded-md border transition-colors ${
                        composerRequestContext.scope === "deck"
                          ? "bg-blue-600 border-blue-600 text-white"
                          : "bg-white border-slate-200 text-slate-600 hover:bg-slate-50"
                      }`}
                      title="默认作用于整套 PPT"
                    >
                      整套 PPT
                    </button>
                  </div>
                  {composerRequestContext.explicitScope && (
                    <span className="text-slate-400">已按指令识别</span>
                  )}
                  {composerRequestContext.risk === "cost" && (
                    <span className="text-amber-600">会先确认生图成本</span>
                  )}
                  {composerRequestContext.risk === "destructive" && (
                    <span className="text-amber-600">大范围改动会先确认</span>
                  )}
                </div>
              )}
              {currentAgentRole !== "finetune" && (
                <div className="pg-composer-tools">
                <button
                  type="button"
                  onClick={handlePickAgentAttachments}
                  disabled={!selectedProject || chatLoading || uploadingDoc}
                  className="pg-composer-attach"
                  title="添加图片、PDF、PPT、Markdown 等材料"
                  aria-label="添加材料"
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 3v10" />
                    <path d="m8 7 4-4 4 4" />
                    <path d="M5 13v5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-5" />
                  </svg>
                  <span>{uploadingDoc ? "上传中..." : "添加材料"}</span>
                </button>
                <span className="pg-composer-tools-hint">支持图片、PDF、PPT、MD</span>
              </div>
            )}
          <div className="pg-composer-row flex gap-2">
            <textarea
              ref={chatInputRef}
              className="pg-chat-input flex-1 border border-slate-300 rounded-lg resize-none px-3 py-2.5 text-sm focus:border-blue-400 focus:ring-1 focus:ring-blue-200 outline-none transition-colors"
              style={{ minHeight: 38, overflowY: "hidden" }}
              placeholder={
                currentAgentRole === "finetune" && !finetuneTargetSlideId
                  ? "请先在左侧点击一页..."
                  : currentStatus === "draft"
                  ? "输入 PPT 主题或粘贴文档内容..."
                  : currentAgentRole === "finetune"
                  ? "告诉我怎么改，也可以添加参考图..."
                  : "输入指令..."
              }
              value={agentComposerValue}
              onChange={(e) => setChatInput(e.target.value)}
              onInput={autoResizeTextarea}
              onKeyDown={(e) => {
                if (e.key !== "Enter") return;
                if ((e as any).nativeEvent?.isComposing) return;
                if (e.shiftKey) return;
                e.preventDefault();
                handleSendChat();
              }}
              disabled={!selectedProject || chatLoading || (currentAgentRole === "finetune" && !finetuneTargetSlideId)}
            />
            {chatLoading ? (
              currentAgentRole === "finetune" ? (
                <button
                  disabled
                  className="pg-action pg-action-primary bg-amber-500 text-white rounded-lg px-3 py-2 text-sm opacity-80 cursor-wait"
                >
                  生成中
                </button>
              ) : (
                <button
                  onClick={handleStopChat}
                  className="pg-action pg-action-primary bg-gray-800 text-white rounded hover:bg-gray-900 px-3 py-2 text-sm"
                >
                  停止
                </button>
              )
            ) : hasActiveRun && currentAgentRole !== "finetune" ? (
              <button
                onClick={handleStopGeneration}
                className="pg-action pg-action-danger bg-red-600 text-white rounded-lg hover:bg-red-700 px-4 py-2.5 text-sm font-medium transition-colors flex items-center gap-1.5"
                title="停止生成"
              >
                <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                  <rect x="6" y="6" width="12" height="12" rx="1" />
                </svg>
                停止生成
              </button>
            ) : (
              <button
                onClick={() => handleSendChat()}
                disabled={
                  !selectedProject ||
                  uploadingDoc ||
                  (!agentComposerValue.trim() && pendingAttachments.length === 0 && pendingChatAttachments.length === 0 && !(currentAgentRole === "finetune" && finetuneTargetSlideId && (pendingFinetuneAttachmentsMap[finetuneTargetSlideId] || []).length > 0)) ||
                  (currentAgentRole === "finetune" && !finetuneTargetSlideId)
                }
                className="pg-action pg-action-primary rounded-lg disabled:opacity-50 px-4 py-2.5 text-sm font-medium transition-colors"
              >
                {currentAgentRole === "finetune" ? "生成" : "发送"}
              </button>
            )}
          </div>
          </div>
          )}
        </div>
      </aside>
      )}

      {/* Gallery Modal */}
      {galleryModal && (
        <div
          className="fixed inset-0 bg-black/80 flex items-center justify-center z-50"
          onClick={() => setGalleryModal(null)}
        >
          <div
            className="relative max-w-5xl max-h-[92vh] w-full flex flex-col items-center m-4"
            onClick={(e) => e.stopPropagation()}
          >
            {/* 顶部标题和关闭 */}
            <div className="flex items-center justify-between w-full mb-2 px-1">
              <span className="text-white text-sm">
                {galleryModal.title || "图片预览"} {galleryModal.urls.length > 1 && (
                  <span className="text-gray-300">（{galleryModal.index + 1} / {galleryModal.urls.length}）</span>
                )}
              </span>
              <button
                onClick={() => setGalleryModal(null)}
                className="text-white hover:text-gray-300 text-xl px-2"
              >
                ✕
              </button>
            </div>

            {/* 图片区 + 左右箭头 */}
            <div className="relative flex items-center justify-center w-full">
              {galleryModal.urls.length > 1 && (
                <button
                  onClick={() => setGalleryModal(prev => prev ? { ...prev, index: prev.index > 0 ? prev.index - 1 : prev.urls.length - 1 } : prev)}
                  className="absolute left-0 z-10 text-white/70 hover:text-white text-3xl px-3 py-6 rounded hover:bg-white/10"
                >
                  ‹
                </button>
              )}
              {galleryModal.slides?.[galleryModal.index] ? (
                <SlideImageWithOverlays
                  slide={galleryModal.slides[galleryModal.index]}
                  logo={galleryModal.logo}
                  referenceImages={referenceImages}
                  src={galleryModal.urls[galleryModal.index]}
                  alt="Preview"
                  className="max-w-full max-h-[78vh] rounded shadow-2xl overflow-hidden"
                  imgClassName="max-w-full max-h-[78vh] object-contain"
                  onError={(e) => {
                    const el = e.target as HTMLImageElement;
                    el.style.display = "none";
                    const parent = el.parentElement;
                    if (parent) parent.innerHTML = '<div class="text-white text-sm py-20">图片加载失败</div>';
                  }}
                />
              ) : (
                <img
                  src={galleryModal.urls[galleryModal.index]}
                  alt="Preview"
                  className="max-w-full max-h-[78vh] rounded shadow-2xl object-contain"
                  onError={(e) => {
                    const el = e.target as HTMLImageElement;
                    el.style.display = "none";
                    const parent = el.parentElement;
                    if (parent) parent.innerHTML = '<div class="text-white text-sm py-20">图片加载失败</div>';
                  }}
                />
              )}
              {galleryModal.urls.length > 1 && (
                <button
                  onClick={() => setGalleryModal(prev => prev ? { ...prev, index: prev.index < prev.urls.length - 1 ? prev.index + 1 : 0 } : prev)}
                  className="absolute right-0 z-10 text-white/70 hover:text-white text-3xl px-3 py-6 rounded hover:bg-white/10"
                >
                  ›
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* 新建项目 Modal */}
      {showCreateModal && (
        <div
          className="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
          onClick={() => setShowCreateModal(false)}
        >
          <div
            className="bg-white rounded-lg shadow-xl w-full max-w-sm m-4 p-6"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="font-bold text-lg mb-4">新建项目</h3>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                项目标题
              </label>
              <input
                className="w-full border rounded px-3 py-2 text-sm"
                placeholder="未命名项目"
                maxLength={100}
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleCreate()}
                autoFocus
              />
            </div>
            <div className="flex justify-end gap-3 mt-6">
              <button
                onClick={() => {
                  setShowCreateModal(false);
                  setNewTitle("");
                }}
                className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800"
              >
                取消
              </button>
              <button
                onClick={handleCreate}
                className="px-4 py-2 text-sm bg-blue-600 text-white rounded hover:bg-blue-700"
              >
                创建
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Toast 通知 */}
      <ToastContainer toasts={toasts} onRemove={removeToast} />

      {/* Confirm 模态框 */}
      {confirmModal && (
        <div
          className="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
          onClick={() => confirmModal.onCancel()}
        >
          <div
            className="bg-white rounded-lg shadow-xl w-full max-w-sm m-4 p-6"
            onClick={(e) => e.stopPropagation()}
          >
            <p className="text-sm text-gray-700 mb-6">{confirmModal.message}</p>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => confirmModal.onCancel()}
                className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800"
              >
                取消
              </button>
              <button
                onClick={() => confirmModal.onConfirm()}
                className="px-4 py-2 text-sm bg-red-600 text-white rounded hover:bg-red-700"
              >
                确认
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

interface EditorState {
  headline: string;
  subhead: string;
  body: string;
}

interface SlideEditChangeSet {
  contentChanged: boolean;
  visualChanged: boolean;
}

interface SaveResult extends SlideEditChangeSet {
  ok: boolean;
}

function SingleSlideEditor({
  slide,
  projectId,
  onExit,
  onSaved,
  onDelete,
  onInsertBefore,
  onInsertAfter,
  onPrev,
  onNext,
  hasPrev,
  hasNext,
  typeLabel,
  typeColor,
  projectLogo,
  referenceImages,
  imageCacheKey,
  slideVersions,
  onRestoreVersion,
  onDeleteVersion,
  unescapeText,
  onImageClick,
  onToast,
  markSlideStale,
  staleStatus,
  projectStatus,
  onUpdateStale,
  onGenerateImages,
  onSystemLog,
  onRetry,
  onRegenerateFromEdits,
}: {
  slide: Slide;
  projectId: string;
  onExit: () => void;
  onSaved?: () => void | Promise<void>;
  onDelete?: () => void;
  onInsertBefore?: () => void;
  onInsertAfter?: () => void;
  onPrev?: () => void;
  onNext?: () => void;
  hasPrev?: boolean;
  hasNext?: boolean;
  typeLabel: Record<string, string>;
  typeColor: Record<string, string>;
  projectLogo?: any;
  referenceImages?: any[];
  imageCacheKey?: number;
  slideVersions?: any[];
  onRestoreVersion?: (versionId: string) => void;
  onDeleteVersion?: (versionId: string) => void;
  unescapeText: (text: string) => string;
  onImageClick?: (url: string) => void;
  onToast?: (message: string, type: ToastItem["type"]) => void;
  markSlideStale?: (slideId: string, type: "content" | "visual" | "image") => void;
  staleStatus?: { content?: boolean; visual?: boolean; image?: boolean };
  projectStatus?: string;
  onUpdateStale?: () => void;
  onGenerateImages?: () => void;
  onSystemLog?: (content: string) => void;
  onRetry?: (slideId: string, regeneratePrompt?: boolean) => Promise<void>;
  onRegenerateFromEdits?: (slideId: string, changes: SlideEditChangeSet) => Promise<void>;
}) {
  const content = slide.content_json || {};
  const text = content.text_content || {};
  const [headline, setHeadline] = useState(unescapeText(text.headline || ""));
  const [subhead, setSubhead] = useState(unescapeText(text.subhead || ""));
  // body 兼容旧数据（string[]）和新数据（string）
  const normalizeBody = (raw: any): string => {
    if (typeof raw === "string") return normalizeMarkdownEmphasis(unescapeText(raw));
    if (Array.isArray(raw)) return normalizeMarkdownEmphasis(raw.map((item: any) =>
      typeof item === "string" ? item : item?.content || ""
    ).join("\n\n"));
    return "";
  };
  const [body, setBody] = useState<string>(normalizeBody(text.body));
  const [bodyEmpty, setBodyEmpty] = useState(!body || body.trim() === "");
  const bodyEditorRef = useRef<HTMLDivElement>(null);
  const turndownRef = useRef(
    (() => {
      const service = new TurndownService({ headingStyle: "atx", bulletListMarker: "-", codeBlockStyle: "fenced" });
      // GFM 表格 + 删除线插件，确保 <table> 能反向转回 markdown 管道符语法，不被展平成纯文本
      service.use(gfmTables);
      service.use(gfmStrikethrough);
      return service;
    })()
  );
  const [speakerNotes, setSpeakerNotes] = useState(unescapeText(content.speaker_notes || ""));

  // 视觉方案编辑状态
  const [visualDescription, setVisualDescription] = useState(slide.visual_json?.visual_description || "");
  const [promptExpanded, setPromptExpanded] = useState(Boolean(slide.prompt_text));

  // 撤销/重做：用 state 管理确保 UI 实时响应
  const initialState: EditorState = {
    headline: unescapeText(text.headline || ""),
    subhead: unescapeText(text.subhead || ""),
    body: normalizeBody(text.body),
  };
  const [history, setHistory] = useState<EditorState[]>([initialState]);
  const [historyIndex, setHistoryIndex] = useState(0);
  const isUndoingRef = useRef(false);

  // Markdown 快捷键：Ctrl/Cmd + B/I/K
  const applyMarkdownShortcut = (
    e: React.KeyboardEvent<HTMLTextAreaElement>,
    setter: (v: string) => void
  ) => {
    if (!(e.ctrlKey || e.metaKey)) return;
    const key = e.key.toLowerCase();
    if (!['b', 'i', 'k'].includes(key)) return;

    const el = e.currentTarget;
    const { selectionStart, selectionEnd, value } = el;
    let newValue = value;
    let newCursorStart = selectionStart;
    let newCursorEnd = selectionEnd;

    if (key === 'b') {
      const wrap = '**';
      if (selectionStart === selectionEnd) {
        newValue = value.slice(0, selectionStart) + wrap + wrap + value.slice(selectionEnd);
        newCursorStart = selectionStart + wrap.length;
        newCursorEnd = newCursorStart;
      } else {
        newValue = value.slice(0, selectionStart) + wrap + value.slice(selectionStart, selectionEnd) + wrap + value.slice(selectionEnd);
        newCursorStart = selectionStart + wrap.length;
        newCursorEnd = selectionEnd + wrap.length;
      }
    } else if (key === 'i') {
      const wrap = '*';
      if (selectionStart === selectionEnd) {
        newValue = value.slice(0, selectionStart) + wrap + wrap + value.slice(selectionEnd);
        newCursorStart = selectionStart + wrap.length;
        newCursorEnd = newCursorStart;
      } else {
        newValue = value.slice(0, selectionStart) + wrap + value.slice(selectionStart, selectionEnd) + wrap + value.slice(selectionEnd);
        newCursorStart = selectionStart + wrap.length;
        newCursorEnd = selectionEnd + wrap.length;
      }
    } else if (key === 'k') {
      const selected = value.slice(selectionStart, selectionEnd);
      const linkText = selected || '链接文字';
      const insert = `[${linkText}](url)`;
      newValue = value.slice(0, selectionStart) + insert + value.slice(selectionEnd);
      if (selected) {
        newCursorStart = selectionStart + selected.length + 3;
        newCursorEnd = newCursorStart + 3;
      } else {
        newCursorStart = selectionStart + 1;
        newCursorEnd = newCursorStart + linkText.length;
      }
    }

    e.preventDefault();
    setter(newValue);
    setTimeout(() => {
      el.focus();
      el.setSelectionRange(newCursorStart, newCursorEnd);
    }, 0);
  };

  const getCurrentState = (): EditorState => {
    let currentBody = body;
    const bodyEl = bodyEditorRef.current;
    if (bodyEl?.matches(":focus")) {
      currentBody = normalizeMarkdownEmphasis(turndownRef.current.turndown(bodyEl.innerHTML));
    }
    return {
      headline,
      subhead,
      body: currentBody,
    };
  };

  // push 当前状态到历史栈（截断 redo、去重、限深）
  const pushHistory = (state: EditorState) => {
    setHistory((prev) => {
      const trimmed = prev.slice(0, historyIndex + 1);
      const top = trimmed[trimmed.length - 1];
      if (
        top &&
        top.headline === state.headline &&
        top.subhead === state.subhead &&
        top.body === state.body
      ) {
        return prev;
      }
      const next = [...trimmed, state];
      if (next.length > 20) {
        next.shift();
        setHistoryIndex((idx) => idx - 1);
      }
      return next;
    });
    setHistoryIndex((idx) => Math.min(idx + 1, 19));
  };

  const restoreState = (state: EditorState) => {
    isUndoingRef.current = true;
    setHeadline(state.headline);
    setSubhead(state.subhead);
    setBody(state.body);
    setTimeout(() => {
      isUndoingRef.current = false;
    }, 0);
  };

  const canUndo = historyIndex > 0;
  const canRedo = historyIndex < history.length - 1;

  const handleUndo = () => {
    setHistoryIndex((idx) => {
      const target = Math.max(0, idx - 1);
      restoreState(history[target]);
      return target;
    });
  };

  const handleRedo = () => {
    setHistoryIndex((idx) => {
      const target = Math.min(history.length - 1, idx + 1);
      restoreState(history[target]);
      return target;
    });
  };

  // 快捷键：Ctrl+Z 撤销，Ctrl+Shift+Z / Ctrl+Y 重做
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) {
          handleRedo();
        } else {
          handleUndo();
        }
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "y") {
        e.preventDefault();
        handleRedo();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [history.length]);

  // 用户手动编辑后，失去焦点时记录历史
  const handleBlurPushHistory = () => {
    if (isUndoingRef.current) return;
    const current = getCurrentState();
    const top = history[historyIndex];
    if (
      top &&
      top.headline === current.headline &&
      top.subhead === current.subhead &&
      top.body === current.body
    ) {
      return;
    }
    pushHistory(current);
  };

  const [saving, setSaving] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [pageActionLoading, setPageActionLoading] = useState<"plan" | "image" | null>(null);
  const [rerollingPlan, setRerollingPlan] = useState(false);
  const [assetRouteLoading, setAssetRouteLoading] = useState<string | null>(null);

  // 保存当前编辑内容（不退出）
  const handleSave = async (options?: { quiet?: boolean }): Promise<SaveResult> => {
    const content = slide.content_json || {};
    const text = content.text_content || {};
    const originalHeadline = unescapeText(text.headline || "");
    const originalSubhead = unescapeText(text.subhead || "");
    const originalBody = normalizeBody(text.body);
    const originalSpeakerNotes = unescapeText(content.speaker_notes || "");

    // 如果正文编辑器还在聚焦，先读取最新内容
    let currentBody = body;
    const bodyEl = bodyEditorRef.current;
    if (bodyEl?.matches(":focus")) {
      currentBody = normalizeMarkdownEmphasis(turndownRef.current.turndown(bodyEl.innerHTML));
      setBody(currentBody);
      setBodyEmpty(!currentBody || currentBody.trim() === "");
    }

    const hasContentChange =
      headline !== originalHeadline ||
      subhead !== originalSubhead ||
      currentBody !== originalBody ||
      speakerNotes !== originalSpeakerNotes;

    const saveData = {
      page_num: slide.page_num,
      type: slide.type,
      section_title: content.section_title || "",
      text_content: { headline, subhead, body: currentBody },
      speaker_notes: speakerNotes,
      visual_suggestion: content.visual_suggestion || "",
    };
    const originalVisualDesc = slide.visual_json?.visual_description ?? "";
    const hasVisualChange = visualDescription !== originalVisualDesc;

    setSaving(true);
    try {
      if (hasContentChange) {
        await updateSlideContent(projectId, slide.page_num, saveData, slide.id);
        markSlideStale?.(slide.id, "content");
      }
      if (hasVisualChange) {
        await updateVisualPlan(projectId, slide.page_num, {
          ...(slide.visual_json || {}),
          visual_description: visualDescription,
        }, slide.id);
        markSlideStale?.(slide.id, "visual");
      }
      await onSaved?.();
      if (hasContentChange || hasVisualChange) {
        if (!options?.quiet) {
          onToast?.(hasVisualChange ? "已保存，请点击「更新画面方案」应用修改" : "已保存", "success");
        }
        onSystemLog?.(`用户编辑了第 ${slide.page_num} 页（类型：${slide.type || "content"}）的标题/正文`);
      }
      return { ok: true, contentChanged: hasContentChange, visualChanged: hasVisualChange };
    } catch (err: any) {
      onToast?.("保存失败：" + (err.message || "未知错误"), "error");
      return { ok: false, contentChanged: false, visualChanged: false };
    } finally {
      setSaving(false);
    }
  };

  // 保存并退出编辑
  const handleSaveAndExit = async () => {
    const result = await handleSave();
    if (result.ok) onExit();
  };

  // 保存并重新生成图片（一键应用修改）
  const handleSaveAndGenerate = async () => {
    const result = await handleSave({ quiet: true });
    if (!result.ok) return;
    if (!onRegenerateFromEdits && !onRetry) {
      onToast?.("无法重新生成：缺少重试接口", "error");
      return;
    }
    setIsGenerating(true);
    onToast?.("正在保存修改并重新生成此页...", "info");
    try {
      if (onRegenerateFromEdits) {
        await onRegenerateFromEdits(slide.id, {
          contentChanged: result.contentChanged,
          visualChanged: result.visualChanged,
        });
      } else {
        await onRetry?.(slide.id, true);
      }
      onToast?.("此页重新生成已完成", "success");
    } catch (err: any) {
      onToast?.("重新生成失败：" + (err.message || "未知错误"), "error");
    } finally {
      setIsGenerating(false);
    }
  };

  // Ctrl+S / Cmd+S 保存
  const handleSaveRef = useRef(handleSave);
  handleSaveRef.current = handleSave;
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        handleSaveRef.current();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  // slide prop 变化时（如 Agent 自动更新后），把旧状态和新状态都记入历史
  const prevContentRef = useRef(slide.content_json);
  const prevVisualRef = useRef(slide.visual_json);
  useEffect(() => {
    if (slide.content_json !== prevContentRef.current && !isUndoingRef.current) {
      prevContentRef.current = slide.content_json;
      const newText = slide.content_json?.text_content || {};
      const newState: EditorState = {
        headline: unescapeText(newText.headline || ""),
        subhead: unescapeText(newText.subhead || ""),
        body: normalizeBody(newText.body),
      };
      // 先 push 当前旧状态，再 push Agent 新状态，这样 undo/redo 链路完整
      setHistory((prev) => {
        const trimmed = prev.slice(0, historyIndex + 1);
        const withOld = [...trimmed, getCurrentState()];
        const withNew = [...withOld, newState];
        if (withNew.length > 20) {
          const drop = withNew.length - 20;
          const next = withNew.slice(drop);
          setHistoryIndex((idx) => idx - drop + 2);
          return next;
        }
        setHistoryIndex((idx) => idx + 2);
        return withNew;
      });
      setHeadline(newState.headline);
      setSubhead(newState.subhead);
      setBody(newState.body);
      setSpeakerNotes(unescapeText(slide.content_json?.speaker_notes || ""));
    }
    // 同步 visual_description
    if (slide.visual_json !== prevVisualRef.current && !isUndoingRef.current) {
      prevVisualRef.current = slide.visual_json;
      setVisualDescription(slide.visual_json?.visual_description || "");
    }
  });

  // 同步 body Markdown → contentEditable HTML（仅在非聚焦时，避免覆盖用户输入）
  useEffect(() => {
    const el = bodyEditorRef.current;
    if (!el || el.matches(":focus")) return;
    const normalizedBody = normalizeMarkdownEmphasis(body || "");
    let html = (marked.parse(normalizedBody, { async: false }) as string) || "<p><br></p>";
    html = fixMarkedBoldHtml(html);
    // 给编辑器内表格附上样式，避免没有边框看起来像被展平
    html = html.replace(/<table\b/g, '<table class="table-auto w-full text-sm border border-slate-300 my-2"');
    html = html.replace(/<thead\b/g, '<thead class="bg-slate-100"');
    html = html.replace(/<th\b/g, '<th class="border border-slate-300 px-2 py-1 text-left font-medium"');
    html = html.replace(/<td\b/g, '<td class="border border-slate-300 px-2 py-1 align-top"');
    const safeHtml = DOMPurify.sanitize(html, {
      ALLOWED_TAGS: [
        "p", "br", "strong", "em", "b", "i", "u",
        "ul", "ol", "li",
        "h1", "h2", "h3", "h4", "h5", "h6",
        "code", "pre", "span", "div",
        "table", "thead", "tbody", "tr", "th", "td",
        "del", "s",
      ],
      ALLOWED_ATTR: ["class", "style"],
    });
    if (el.innerHTML !== safeHtml) {
      el.innerHTML = safeHtml;
    }
  }, [body]);

  // body 变更时同步空状态
  useEffect(() => {
    setBodyEmpty(!body || body.trim() === "");
  }, [body]);

  // 正文编辑器失焦：HTML → Markdown
  const handleBodyBlur = () => {
    const el = bodyEditorRef.current;
    if (!el) return;
    const html = el.innerHTML;
    const md = normalizeMarkdownEmphasis(turndownRef.current.turndown(html));
    setBody(md);
    setBodyEmpty(!md || md.trim() === "");
    handleBlurPushHistory();
  };

  // 正文输入时实时检测是否为空
  const handleBodyInput = () => {
    const el = bodyEditorRef.current;
    if (!el) return;
    const empty = el.innerText.trim() === "";
    setBodyEmpty(empty);
  };

  // 富文本工具栏命令
  const execEditorCmd = (cmd: string, value?: string) => {
    const el = bodyEditorRef.current;
    if (!el) return;
    el.focus();
    document.execCommand(cmd, false, value);
  };

  const projectAssetById = useMemo(() => {
    const map = new Map<string, any>();
    for (const ref of referenceImages || []) {
      if (ref?.id) map.set(String(ref.id), ref);
    }
    return map;
  }, [referenceImages]);
  const enabledOverlayLayers = Array.isArray(slide.visual_json?.overlay_layers)
    ? slide.visual_json.overlay_layers.filter((layer: any) => layer?.enabled !== false && layer?.asset_id)
    : [];
  const overlayAssetIds = new Set(enabledOverlayLayers.map((layer: any) => String(layer.asset_id)));
  const visualAssetIds = Array.isArray(slide.visual_json?.visual_asset_ids)
    ? slide.visual_json.visual_asset_ids.map(String).filter(Boolean)
    : [];
  const manualVisualAssetIds = Array.isArray(slide.visual_json?.manual_visual_asset_ids)
    ? slide.visual_json.manual_visual_asset_ids.map(String).filter(Boolean)
    : [];
  const manualVisualAssetSet = new Set(manualVisualAssetIds);
  const visualAssetUsage = {
    ...(slide.visual_json?.visual_asset_usage || {}),
    ...(slide.visual_json?.manual_visual_asset_usage || {}),
  };
  const assetRouteModes = slide.visual_json?.asset_route_modes && typeof slide.visual_json.asset_route_modes === "object"
    ? slide.visual_json.asset_route_modes
    : {};
  type AssetRoute = "blend" | "double_blend" | "overlay";
  const pageReferenceItems = dedupeReferenceImages(slide.reference_images || []);
  const blendProjectAssetIds = visualAssetIds.filter((id: string, index: number, arr: string[]) =>
    !overlayAssetIds.has(id) && arr.indexOf(id) === index
  );
  const blendProjectAssets = blendProjectAssetIds.map((id: string) => ({
    id,
    asset: projectAssetById.get(id),
    usage: visualAssetUsage[id],
    manual: manualVisualAssetSet.has(id),
    route: assetRouteModes[id] || (
      ["product", "material"].includes(String(projectAssetById.get(id)?.asset_kind || "").toLowerCase())
        ? "double_blend"
        : "blend"
    ),
  }));
  const overlayProjectAssets = enabledOverlayLayers
    .map((layer: any) => ({
      layer,
      id: String(layer.asset_id),
      asset: projectAssetById.get(String(layer.asset_id)),
    }))
    .filter(({ asset }: any) => Boolean(asset));
  const routeAssetName = (asset: any, fallback: string) =>
    asset?.asset_name || asset?.asset_analysis?.subject || fallback;
  const routeAssetUrl = (asset: any) =>
    asset?.overlay_url || asset?.url ? `${API_BASE}${asset.overlay_url || asset.url}` : "";

  const routeLabel = (route: string) => {
    if (route === "overlay") return "精确粘贴";
    if (route === "double_blend") return "精修融合";
    return "智能融合";
  };
  const routeProcessMode = (route: AssetRoute) => {
    if (route === "overlay") return "original";
    if (route === "double_blend") return "crop";
    return "blend";
  };
  const pageReferenceRoute = (ref: any): AssetRoute => {
    const id = String(ref?.id || "");
    if (id && overlayAssetIds.has(id)) return "overlay";
    const mode = String(ref?.process_mode || "").toLowerCase();
    if (mode === "crop") return "double_blend";
    const analysisRoute = String(ref?.asset_analysis?.asset_route_mode || ref?.asset_analysis?.route_mode || "").toLowerCase();
    if (analysisRoute === "double_blend" || analysisRoute === "overlay" || analysisRoute === "blend") {
      return analysisRoute as AssetRoute;
    }
    return "blend";
  };
  const imageReferenceInputCount =
    pageReferenceItems.filter((ref: any) => pageReferenceRoute(ref) !== "overlay").length +
    blendProjectAssets.filter(({ route }: any) => route !== "overlay").length;
  const imageReferenceBadgeText = imageReferenceInputCount > 14
    ? `参考图 ${imageReferenceInputCount} 张 · 前 14 张进入生图`
    : `参考图 ${imageReferenceInputCount}/14 · 会稍慢`;
  const pageReferenceLabel = (ref: any) => {
    const analysis = ref?.asset_analysis || {};
    const page = ref?.source_page_num || analysis.pptx_source_page_num;
    const groupIndex = analysis.asset_group_index;
    const groupSize = analysis.asset_group_size;
    if (analysis.asset_group_role === "parallel_page_reference_set" && page && groupIndex && groupSize) {
      return `原 PPT 第 ${page} 页图片组 ${groupIndex}/${groupSize}`;
    }
    if (page) return `原 PPT 第 ${page} 页参考图`;
    return "本页上传素材";
  };

  const setPageReferenceRoute = async (ref: any, route: AssetRoute) => {
    const refId = String(ref?.id || "");
    if (!refId) return;
    setAssetRouteLoading(refId);
    try {
      const currentLayers = enabledOverlayLayers.filter((layer: any) => String(layer.asset_id) !== refId);
      if (route === "overlay") {
        await updateSlideOverlayLayers(projectId, slide.id, [
          ...currentLayers,
          {
            id: `ov_${refId}`,
            asset_id: refId,
            enabled: true,
            preset: "right-card",
            fit: "contain",
            mode: "exact_card",
            usage_note: `${pageReferenceLabel(ref)}：原图保留`,
          },
        ]);
      } else {
        if (overlayAssetIds.has(refId)) {
          await updateSlideOverlayLayers(projectId, slide.id, currentLayers);
        }
        await updateReferenceImage(projectId, refId, { process_mode: routeProcessMode(route) });
      }
      markSlideStale?.(slide.id, "visual");
      await onSaved?.();
      onToast?.(`已切换为${routeLabel(route)}`, "success");
      onSystemLog?.(`用户将第 ${slide.page_num} 页本页参考图切换为${routeLabel(route)}`);
    } catch (err: any) {
      try {
        await onSaved?.();
      } catch {
        // Best-effort refresh after a failed route update.
      }
      onToast?.("切换失败，已刷新当前状态：" + (err.message || "未知错误"), "error");
    } finally {
      setAssetRouteLoading(null);
    }
  };

  const setPageAssetRoute = async (assetId: string, route: AssetRoute) => {
    setAssetRouteLoading(assetId);
    try {
      const nextRouteModes = { ...assetRouteModes, [assetId]: route };
      const nextManualIds = manualVisualAssetIds.includes(assetId) ? manualVisualAssetIds : [...manualVisualAssetIds, assetId];
      if (!manualVisualAssetIds.includes(assetId)) {
        await updateSlideAssetPins(projectId, slide.id, nextManualIds, {
          ...(slide.visual_json?.manual_visual_asset_usage || {}),
          [assetId]: visualAssetUsage[assetId] || `用户切换为${routeLabel(route)}`,
        });
      }
      const currentLayers = enabledOverlayLayers.filter((layer: any) => String(layer.asset_id) !== assetId);
      if (route === "overlay") {
        await updateSlideOverlayLayers(projectId, slide.id, [
          ...currentLayers,
          {
            id: `ov_${assetId}`,
            asset_id: assetId,
            enabled: true,
            preset: "right-card",
            fit: "contain",
            mode: "exact_card",
            usage_note: visualAssetUsage[assetId] || "用户切换为精确粘贴",
          },
        ]);
      } else if (overlayAssetIds.has(assetId)) {
        await updateSlideOverlayLayers(projectId, slide.id, currentLayers);
      }
      await updateVisualPlan(projectId, slide.page_num, { asset_route_modes: nextRouteModes }, slide.id);
      markSlideStale?.(slide.id, "visual");
      await onSaved?.();
      onToast?.(`已切换为${routeLabel(route)}`, "success");
      onSystemLog?.(`用户将第 ${slide.page_num} 页素材切换为${routeLabel(route)}`);
    } catch (err: any) {
      try {
        await onSaved?.();
      } catch {
        // Best-effort refresh after a failed route update.
      }
      onToast?.("切换失败，已刷新当前状态：" + (err.message || "未知错误"), "error");
    } finally {
      setAssetRouteLoading(null);
    }
  };

  // 内容规划阶段不显示 "需更新画面方案" 的 content stale 提示
  const hasVisualPlan = !!slide.visual_json?.visual_description;
  const pastContentPlanning = !["draft", "planning", "content_plan_ready"].includes(projectStatus || "");
  const showContentStale = staleStatus?.content && hasVisualPlan && pastContentPlanning;
  const showVisualStale = staleStatus?.visual;
  const showImageStale = staleStatus?.image;

  return (
    <div className="pg-editor pg-single-slide-editor max-w-3xl mx-auto bg-white rounded border shadow-sm p-6">
      {/* 顶部工具栏 — 全文字，无图标，三区布局 */}
      <div className="pg-editor-toolbar sticky top-0 z-30 -mx-6 -mt-6 mb-6 flex items-center justify-between border-b border-slate-200 px-6 pb-4 pt-6">
        {/* 左：核心操作 */}
        <div className="flex items-center gap-1.5">
          <button
            onClick={handleSaveAndExit}
            disabled={saving}
            className={`pg-action pg-action-link text-sm px-2.5 py-1.5 rounded-md transition-colors ${
              saving ? "text-slate-300 cursor-not-allowed" : "text-slate-500 hover:text-slate-700 hover:bg-slate-100"
            }`}
          >
            {saving ? "保存中..." : "返回列表"}
          </button>
          <button
            onClick={async () => { await handleSave(); }}
            disabled={saving || isGenerating}
            className={`pg-action pg-action-secondary text-sm px-3 py-1.5 rounded-md border transition-colors ${
              saving || isGenerating
                ? "text-slate-300 border-slate-200 cursor-not-allowed"
                : "text-slate-700 border-slate-300 hover:bg-slate-50"
            }`}
            title="保存 (Ctrl+S)"
          >
            {saving ? "保存中..." : "保存"}
          </button>
          <button
            onClick={handleSaveAndGenerate}
            disabled={saving || isGenerating}
            className={`pg-action pg-action-primary text-sm px-3 py-1.5 rounded-md font-medium transition-all ${
              saving || isGenerating
                ? "bg-slate-300 text-white cursor-not-allowed"
                : "bg-purple-600 text-white hover:bg-purple-700 shadow-sm"
            }`}
            title="保存并重新生成此页图片"
          >
            {isGenerating ? "生成中..." : saving ? "保存中..." : "保存并重新生成"}
          </button>
        </div>

        {/* 中：页面定位 */}
        <div className="flex items-center gap-2">
          {onPrev && (
            <button
              onClick={async () => { const result = await handleSave(); if (result.ok) onPrev?.(); }}
              disabled={!hasPrev || saving}
              className={`text-sm px-2.5 py-1.5 rounded-md transition-colors ${
                hasPrev && !saving ? "text-slate-600 hover:bg-slate-100" : "text-slate-300 cursor-not-allowed"
              }`}
            >
              上一页
            </button>
          )}
          <div className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-100 rounded-md">
            <span className="text-sm font-bold text-slate-700">P{slide.page_num}</span>
            <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${typeColor[slide.type] || "bg-white text-slate-600"}`}>
              {typeLabel[slide.type] || slide.type}
            </span>
          </div>
          {onNext && (
            <button
              onClick={async () => { const result = await handleSave(); if (result.ok) onNext?.(); }}
              disabled={!hasNext || saving}
              className={`text-sm px-2.5 py-1.5 rounded-md transition-colors ${
                hasNext && !saving ? "text-slate-600 hover:bg-slate-100" : "text-slate-300 cursor-not-allowed"
              }`}
            >
              下一页
            </button>
          )}
        </div>

        {/* 右：编辑管理 */}
        <div className="flex items-center gap-0.5">
          <button
            onClick={handleUndo}
            disabled={!canUndo}
            className={`text-sm px-2 py-1.5 rounded-md transition-colors ${
              canUndo ? "text-slate-500 hover:text-slate-700 hover:bg-slate-100" : "text-slate-300 cursor-not-allowed"
            }`}
          >
            撤销
          </button>
          <button
            onClick={handleRedo}
            disabled={!canRedo}
            className={`text-sm px-2 py-1.5 rounded-md transition-colors ${
              canRedo ? "text-slate-500 hover:text-slate-700 hover:bg-slate-100" : "text-slate-300 cursor-not-allowed"
            }`}
          >
            重做
          </button>
          <div className="w-px h-5 bg-slate-200 mx-1.5" />
          {onInsertBefore && (
            <button
              onClick={onInsertBefore}
              className="text-sm text-slate-500 hover:text-green-600 px-2 py-1.5 rounded-md hover:bg-green-50 transition-colors"
            >
              前插
            </button>
          )}
          {onInsertAfter && (
            <button
              onClick={onInsertAfter}
              className="text-sm text-slate-500 hover:text-green-600 px-2 py-1.5 rounded-md hover:bg-green-50 transition-colors"
            >
              后插
            </button>
          )}
          {onDelete && (
            <>
              <div className="w-px h-5 bg-slate-200 mx-1.5" />
              <button
                onClick={onDelete}
                className="text-sm text-slate-500 hover:text-red-600 px-2 py-1.5 rounded-md hover:bg-red-50 transition-colors"
              >
                删除
              </button>
            </>
          )}
        </div>
      </div>

      {/* Stale 状态横幅 */}
      {(showContentStale || showVisualStale || showImageStale) && (
        <div className="pg-stale-banner mb-4 bg-amber-50 border border-amber-200 rounded p-3">
          <div className="flex items-center justify-between">
            <div className="flex flex-wrap gap-2 text-xs">
              {showContentStale && (
                <span className="pg-status-pill px-2 py-0.5 bg-blue-100 text-blue-700 rounded text-xs" title="文字已修改，需更新画面方案">文字已改</span>
              )}
              {showVisualStale && (
                <span className="pg-status-pill px-2 py-0.5 bg-emerald-100 text-emerald-700 rounded text-xs" title="画面描述已修改，需更新提示词">画面已改</span>
              )}
              {showImageStale && (
                <span className="pg-status-pill px-2 py-0.5 bg-purple-100 text-purple-700 rounded text-xs" title="提示词已就绪，可重新生成图片">待生成</span>
              )}
            </div>
            {showContentStale || showVisualStale ? (
              <button
                onClick={async () => {
                  setPageActionLoading("plan");
                  try {
                    await onUpdateStale?.();
                    onSaved?.();
                  } finally {
                    setPageActionLoading(null);
                  }
                }}
                disabled={saving || pageActionLoading !== null}
                className="pg-action pg-action-primary text-xs bg-amber-500 text-white px-3 py-1.5 rounded hover:bg-amber-600 transition-colors flex-shrink-0 ml-2 disabled:opacity-60"
              >
                {pageActionLoading === "plan" ? "应用变更中..." : "应用变更"}
              </button>
            ) : (
              <div className="flex items-center gap-2 ml-2 flex-shrink-0">
                <button
                  onClick={async () => {
                    setRerollingPlan(true);
                    try {
                      markSlideStale?.(slide.id, "content");
                      await onUpdateStale?.();
                      onSaved?.();
                    } finally {
                      setRerollingPlan(false);
                    }
                  }}
                  disabled={saving || pageActionLoading !== null || rerollingPlan}
                  className="pg-action pg-action-secondary text-xs bg-white text-purple-700 border border-purple-200 px-3 py-1.5 rounded hover:bg-purple-50 transition-colors disabled:opacity-60"
                  title="只重新生成画面描述和生图提示词，不会生图"
                >
                  {rerollingPlan ? "生成中..." : "再来一版"}
                </button>
                <button
                  onClick={async () => {
                    setPageActionLoading("image");
                    try {
                      await onGenerateImages?.();
                      onSaved?.();
                    } finally {
                      setPageActionLoading(null);
                    }
                  }}
                  disabled={saving || pageActionLoading !== null || rerollingPlan}
                  className="pg-action pg-action-primary text-xs bg-purple-500 text-white px-3 py-1.5 rounded hover:bg-purple-600 transition-colors disabled:opacity-60"
                >
                  {pageActionLoading === "image" ? "生成中..." : "确认生成图片"}
                </button>
              </div>
            )}
          </div>
          <p className="text-[11px] text-gray-500 mt-1.5">
            {showContentStale
              ? "文字或参考图变更后，需要先更新画面描述和生图提示词。不会自动生图。"
              : showVisualStale
              ? "画面描述变更后，需要更新生图提示词。不会自动生图。"
              : "画面方案已更新。可直接确认生成图片；不满意可以「再来一版」，不会产生生图成本。"}
          </p>
        </div>
      )}

      {/* 标题 */}
      <div className="mb-4">
        <label className="text-xs text-gray-500 mb-1 block font-medium">标题</label>
        <textarea
          value={headline}
          onChange={(e) => setHeadline(e.target.value)}
          onKeyDown={(e) => applyMarkdownShortcut(e, setHeadline)}
          onBlur={handleBlurPushHistory}
          className="pg-input w-full text-xl font-bold border border-gray-200 rounded p-3 focus:outline-none focus:ring-2 focus:ring-blue-300 focus:border-transparent resize-none"
          rows={2}
        />
      </div>

      {/* 副标题 */}
      <div className="mb-4">
        <label className="text-xs text-gray-500 mb-1 block font-medium">副标题</label>
        <textarea
          value={subhead}
          onChange={(e) => setSubhead(e.target.value)}
          onKeyDown={(e) => applyMarkdownShortcut(e, setSubhead)}
          onBlur={handleBlurPushHistory}
          className="pg-input w-full text-base text-gray-600 border border-gray-200 rounded p-2 focus:outline-none focus:ring-2 focus:ring-blue-300 focus:border-transparent resize-none"
          rows={1}
        />
      </div>

      {/* 正文（所见即所得） */}
      <div className="mb-6">
        <label className="text-xs text-gray-500 mb-1 block font-medium">正文</label>
        <div className="pg-rich-editor border border-gray-200 rounded focus-within:ring-2 focus-within:ring-blue-300 focus-within:border-transparent">
          <div className="flex items-center gap-1 px-2 py-1 border-b border-gray-100 bg-gray-50 rounded-t">
            <button
              type="button"
              onClick={() => execEditorCmd("bold")}
              className="text-xs px-2 py-0.5 rounded hover:bg-gray-200 font-bold"
              title="加粗 (Ctrl+B)"
            >
              B
            </button>
            <button
              type="button"
              onClick={() => execEditorCmd("italic")}
              className="text-xs px-2 py-0.5 rounded hover:bg-gray-200 italic"
              title="斜体 (Ctrl+I)"
            >
              I
            </button>
            <button
              type="button"
              onClick={() => execEditorCmd("insertUnorderedList")}
              className="text-xs px-2 py-0.5 rounded hover:bg-gray-200"
              title="无序列表"
            >
              · 列表
            </button>
            <button
              type="button"
              onClick={() => execEditorCmd("insertOrderedList")}
              className="text-xs px-2 py-0.5 rounded hover:bg-gray-200"
              title="有序列表"
            >
              1. 列表
            </button>
          </div>
          <div className="relative">
            {bodyEmpty && (
              <div className="absolute top-3 left-3 text-gray-400 text-sm pointer-events-none select-none">
                输入正文内容...
              </div>
            )}
            <div
              ref={bodyEditorRef}
              contentEditable
              onInput={handleBodyInput}
              onBlur={handleBodyBlur}
              className="w-full text-sm p-3 min-h-[120px] prose prose-sm max-w-none outline-none"
              suppressContentEditableWarning
            />
          </div>
        </div>
      </div>

      {/* 演讲者备注 */}
      <div className="mb-6">
        <label className="text-xs text-gray-500 mb-1 block font-medium">演讲者备注</label>
        <textarea
          value={speakerNotes}
          onChange={(e) => setSpeakerNotes(e.target.value)}
          onKeyDown={(e) => applyMarkdownShortcut(e, setSpeakerNotes)}
          className="pg-input w-full text-sm border border-gray-200 rounded p-3 focus:outline-none focus:ring-2 focus:ring-blue-300 focus:border-transparent resize-y min-h-[80px]"
          placeholder="输入演讲者备注..."
        />
      </div>

      {/* 本页图片确认 */}
      <div className="mb-6">
        <div className="flex items-center gap-1.5 mb-2">
          <label className="text-xs text-gray-500 font-medium">本页图片确认</label>
          {imageReferenceInputCount >= 8 && (
            <span className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded-full px-2 py-0.5">
              {imageReferenceBadgeText}
            </span>
          )}
          <div className="relative group">
            <span className="text-xs text-gray-400 cursor-help">ⓘ</span>
            <div className="absolute left-1/2 -translate-x-1/2 bottom-full mb-1 hidden group-hover:block w-64 bg-gray-800 text-white text-[11px] rounded-lg px-3 py-2 shadow-lg z-50">
              <p className="mb-1">这里列出本页会出现的图片素材。</p>
              <p>你可以保留系统推荐，也可以改成更保真或原样保留。</p>
              <div className="absolute left-1/2 -translate-x-1/2 top-full w-2 h-2 bg-gray-800 rotate-45" />
            </div>
          </div>
        </div>
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 mb-2">
          <div className="grid grid-cols-3 gap-2 text-[11px] mb-3">
            <div className="rounded bg-white border border-slate-200 p-2">
              <div className="font-semibold text-slate-700">智能融合</div>
              <div className="text-slate-500 mt-0.5">自然融入画面</div>
            </div>
            <div className="rounded bg-white border border-blue-200 p-2">
              <div className="font-semibold text-blue-700">精修融合</div>
              <div className="text-slate-500 mt-0.5">融合后校准细节</div>
            </div>
            <div className="rounded bg-white border border-amber-200 p-2">
              <div className="font-semibold text-amber-700">精确粘贴</div>
              <div className="text-slate-500 mt-0.5">原样保留，位置可控</div>
            </div>
          </div>

          <div className="flex flex-col gap-2">
            {pageReferenceItems.map((ref: any) => {
              const route = pageReferenceRoute(ref);
              const refId = String(ref.id);
              return (
                <div key={ref.id} className="flex items-center gap-3 rounded-md bg-white border border-slate-200 p-2">
                  <div className="relative group flex-shrink-0">
                    <img
                      src={`${API_BASE}${ref.url}`}
                      alt="ref"
                      className="w-14 h-14 rounded object-cover border cursor-pointer"
                      onClick={() => onImageClick?.(`${API_BASE}${ref.url}`)}
                      onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                    />
                    <button
                      onClick={async () => {
                        try {
                          await deleteReferenceImage(projectId, ref.id);
                          markSlideStale?.(slide.id, "visual");
                          onSaved?.();
                          onToast?.("已删除", "success");
                          onSystemLog?.(`用户删除了第 ${slide.page_num} 页的本页参考图`);
                        } catch (err: any) {
                          onToast?.("删除失败：" + (err.message || "未知错误"), "error");
                        }
                      }}
                      className="absolute -top-1.5 -right-1.5 w-5 h-5 bg-red-500 text-white text-2xs rounded-full items-center justify-center hidden group-hover:flex shadow-sm z-10"
                      title="删除"
                    >
                      X
                    </button>
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-xs font-semibold text-slate-700 truncate">{ref.asset_name || ref.asset_analysis?.subject || "本页参考图"}</div>
                    <div className="text-[11px] text-slate-500 mt-0.5">{pageReferenceLabel(ref)} · {routeLabel(route)}</div>
                  </div>
                  <div className="flex gap-1">
                    {(["blend", "double_blend", "overlay"] as const).map((target) => (
                      <button
                        key={target}
                        type="button"
                        disabled={assetRouteLoading === refId || route === target}
                        onClick={() => setPageReferenceRoute(ref, target)}
                        className={`text-[11px] px-2 py-1 rounded border ${
                          route === target
                            ? "bg-slate-900 text-white border-slate-900"
                            : "bg-white text-slate-600 border-slate-200 hover:bg-slate-50"
                        } disabled:opacity-60`}
                      >
                        {assetRouteLoading === refId && route !== target ? "切换中" : routeLabel(target)}
                      </button>
                    ))}
                  </div>
                </div>
              );
            })}

            {blendProjectAssets.map(({ id, asset, usage, manual, route }: any) => {
              const url = routeAssetUrl(asset);
              return (
                <div key={id} className="flex items-center gap-3 rounded-md bg-white border border-slate-200 p-2">
                  {url ? (
                    <img
                      src={url}
                      alt={routeAssetName(asset, "项目素材")}
                      className="w-14 h-14 rounded object-contain border bg-white cursor-pointer"
                      onClick={() => onImageClick?.(url)}
                      onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                    />
                  ) : (
                    <div className="w-14 h-14 rounded border bg-slate-100 flex items-center justify-center text-[10px] text-slate-400">缺失</div>
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="text-xs font-semibold text-slate-700 truncate">{routeAssetName(asset, "项目素材")}</div>
                    <div className="text-[11px] text-slate-500 mt-0.5">
                      项目素材 · {manual ? "手动指定" : "系统分配"} · {routeLabel(route)}
                    </div>
                    {usage && <div className="text-[11px] text-slate-400 mt-0.5 truncate">{usage}</div>}
                  </div>
                  <div className="flex gap-1">
                    {(["blend", "double_blend", "overlay"] as const).map((target) => (
                      <button
                        key={target}
                        type="button"
                        disabled={assetRouteLoading === id || route === target}
                        onClick={() => setPageAssetRoute(id, target)}
                        className={`text-[11px] px-2 py-1 rounded border ${
                          route === target
                            ? "bg-slate-900 text-white border-slate-900"
                            : "bg-white text-slate-600 border-slate-200 hover:bg-slate-50"
                        } disabled:opacity-60`}
                      >
                        {assetRouteLoading === id && route !== target ? "切换中" : routeLabel(target)}
                      </button>
                    ))}
                  </div>
                </div>
              );
            })}

            {overlayProjectAssets.map(({ id, asset, layer }: any) => {
              const url = routeAssetUrl(asset);
              return (
                <div key={`overlay-${id}`} className="flex items-center gap-3 rounded-md bg-amber-50 border border-amber-200 p-2">
                  {url ? (
                    <img
                      src={url}
                      alt={routeAssetName(asset, "精确粘贴素材")}
                      className="w-14 h-14 rounded object-contain border bg-white cursor-pointer"
                      onClick={() => onImageClick?.(url)}
                      onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                    />
                  ) : (
                    <div className="w-14 h-14 rounded border bg-amber-100 flex items-center justify-center text-[10px] text-amber-500">缺失</div>
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="text-xs font-semibold text-slate-700 truncate">{routeAssetName(asset, "精确粘贴素材")}</div>
                    <div className="text-[11px] text-amber-700 mt-0.5">项目素材 · 精确粘贴 · {layer.preset || "默认位置"}</div>
                    {layer.usage_note && <div className="text-[11px] text-slate-400 mt-0.5 truncate">{layer.usage_note}</div>}
                  </div>
                  <div className="flex gap-1">
                    {(["blend", "double_blend", "overlay"] as const).map((target) => (
                      <button
                        key={target}
                        type="button"
                        disabled={assetRouteLoading === id || target === "overlay"}
                        onClick={() => setPageAssetRoute(id, target)}
                        className={`text-[11px] px-2 py-1 rounded border ${
                          target === "overlay"
                            ? "bg-slate-900 text-white border-slate-900"
                            : "bg-white text-slate-600 border-slate-200 hover:bg-slate-50"
                        } disabled:opacity-60`}
                      >
                        {assetRouteLoading === id && target !== "overlay" ? "切换中" : routeLabel(target)}
                      </button>
                    ))}
                  </div>
                </div>
              );
            })}

            {pageReferenceItems.length + blendProjectAssets.length + overlayProjectAssets.length === 0 && (
              <div className="text-xs text-slate-500 rounded-md bg-white border border-dashed border-slate-200 p-3">
                当前页没有显式图片素材；最终画面会根据文字和画面描述生成。
              </div>
            )}
          </div>
        </div>
        <button
          onClick={() => {
            const input = document.createElement("input");
            input.type = "file";
            input.accept = "image/*";
            input.multiple = true;
            input.onchange = async (e) => {
              const files = Array.from((e.target as HTMLInputElement).files || []);
              if (files.length === 0) return;
              try {
                for (const file of files) {
                  await uploadFile(projectId, file, "content_ref", slide.id, "blend", {
                    asset_name: file.name.replace(/\.[^.]+$/, ""),
                    usage_note: "用户从单页上传的本页参考图",
                  });
                }
                markSlideStale?.(slide.id, "visual");
                await onSaved?.();
                onToast?.(files.length > 1 ? `已加入 ${files.length} 张本页参考图` : "已加入本页参考图", "success");
                onSystemLog?.(`用户为第 ${slide.page_num} 页上传了 ${files.length} 张本页参考图`);
              } catch (err: any) {
                onToast?.("上传失败：" + (err.message || "未知错误"), "error");
              }
            };
            input.click();
          }}
          className="text-xs bg-gray-100 text-gray-600 px-2 py-1 rounded hover:bg-gray-200"
        >
          + 本页参考图
        </button>
      </div>

      {/* 画面描述 */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-1">
          <label className="text-xs text-gray-500 font-medium">画面描述</label>
          <span className="text-2xs text-gray-400">修改后保存，再更新画面方案。</span>
        </div>
        <textarea
          value={visualDescription}
          onChange={(e) => setVisualDescription(e.target.value)}
          className="pg-input w-full text-sm border border-emerald-100 bg-emerald-50 rounded p-3 focus:outline-none focus:ring-2 focus:ring-emerald-200 focus:border-emerald-200 resize-y min-h-[120px] leading-relaxed"
          placeholder="描述这一页画面应该如何呈现..."
        />
        {slide.visual_json?.layout && (
          <div className="text-xs text-gray-400 mt-1">布局: {slide.visual_json.layout}</div>
        )}
      </div>

      {/* 生图指令（只读，可折叠） */}
      {slide.prompt_text && (
        <div className="mb-6">
          <button
            onClick={() => setPromptExpanded((v) => !v)}
            className="flex items-center gap-1.5 text-xs text-gray-500 mb-1 font-medium hover:text-gray-700 transition-colors"
          >
            <svg
              className={`w-3 h-3 transition-transform ${promptExpanded ? "rotate-90" : ""}`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
            生图指令（只读）
          </button>
          {promptExpanded && (
            <div className="pg-readonly-panel bg-gray-50 border border-gray-200 rounded p-3">
              <p className="text-xs text-gray-500 leading-relaxed whitespace-pre-wrap font-mono">{slide.prompt_text}</p>
            </div>
          )}
        </div>
      )}

      {/* 单页图片预览 */}
      {slide.image_path && (
        <div className="mb-6">
          <label className="text-xs text-gray-500 mb-1 block font-medium">画面预览</label>
          <SlideImageWithOverlays
            slide={slide}
            logo={projectLogo}
            referenceImages={referenceImages}
            src={getSlideImageUrl(slide.image_path, slide.status, imageCacheKey)}
            alt={`Slide ${slide.page_num}`}
            className="aspect-video rounded overflow-hidden cursor-pointer border border-gray-200"
            imgClassName="w-full h-full object-cover"
            onClick={() => {
              const url = getSlideImageUrl(slide.image_path!, slide.status, imageCacheKey);
              onImageClick?.(url);
            }}
            onError={(e) => {
              const el = e.target as HTMLImageElement;
              el.style.display = "none";
              el.parentElement!.innerHTML = '<div class="w-full h-full flex items-center justify-center text-xs text-gray-400 bg-gray-100">图片加载失败</div>';
            }}
          />
          {slideVersions && slideVersions.length > 0 && (
            <div className="mt-2 flex items-center gap-2 flex-wrap">
              <span className="text-xs text-gray-400">历史版本：</span>
              {slideVersions.map((v: any) => (
                <div key={v.id} className="relative group/ver">
                  <img
                    src={`${API_BASE}${v.image_url}?v=${v.id}`}
                    alt={`版本 ${v.version_number}`}
                    className="w-14 h-8 rounded object-cover border border-gray-200 cursor-pointer hover:border-amber-400 hover:ring-1 hover:ring-amber-300 transition-all"
                    title={`恢复版本 ${v.version_number}`}
                    onClick={() => onRestoreVersion?.(v.id)}
                  />
                  <button
                    onClick={() => onDeleteVersion?.(v.id)}
                    className="absolute -top-1 -right-1 w-4 h-4 bg-red-500 text-white rounded-full text-[9px] flex items-center justify-center opacity-0 group-hover/ver:opacity-100 transition-opacity"
                    title="删除此版本"
                  >
                    X
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default App;
