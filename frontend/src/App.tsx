import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, DragEvent as ReactDragEvent, MouseEvent as ReactMouseEvent, SyntheticEvent } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";
import { Node as TiptapNode } from "@tiptap/core";
import { EditorContent, NodeViewWrapper, ReactNodeViewRenderer, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Underline from "@tiptap/extension-underline";
import { Table } from "@tiptap/extension-table";
import TableRow from "@tiptap/extension-table-row";
import TableCell from "@tiptap/extension-table-cell";
import TableHeader from "@tiptap/extension-table-header";
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

function replaceMarkdownOpeningTag(html: string, tag: string, attrs: string): string {
  return html.replace(new RegExp(`<${tag}\\b[^>]*>`, "g"), `<${tag} ${attrs}>`);
}

const renderMarkdown = (md: string, chatStyle = false): string => {
  const normalized = normalizeMarkdownEmphasis(md || "");
  let html = (marked.parse(normalized, { async: false }) as string) || "";
  html = fixMarkedBoldHtml(html);
  if (chatStyle) {
    html = replaceMarkdownOpeningTag(html, "p", 'class="mb-2 last:mb-0" style="white-space:pre-wrap"');
    html = replaceMarkdownOpeningTag(html, "ul", 'class="list-disc pl-4 mb-2"');
    html = replaceMarkdownOpeningTag(html, "ol", 'class="list-decimal pl-4 mb-2"');
    html = replaceMarkdownOpeningTag(html, "li", 'class="mb-1"');
    html = replaceMarkdownOpeningTag(html, "strong", 'class="font-semibold text-gray-900"');
    html = replaceMarkdownOpeningTag(html, "h1", 'class="text-base font-bold mb-2 mt-1"');
    html = replaceMarkdownOpeningTag(html, "h2", 'class="text-sm font-bold mb-2 mt-1"');
    html = replaceMarkdownOpeningTag(html, "h3", 'class="text-sm font-semibold mb-1 mt-1"');
    html = replaceMarkdownOpeningTag(html, "code", 'class="bg-gray-200 px-1 py-0.5 rounded text-xs font-mono"');
    html = replaceMarkdownOpeningTag(html, "pre", 'class="bg-gray-200 p-2 rounded text-xs overflow-auto mb-2"');
  } else {
    // 非聊天模式：给表格加 Tailwind 基础样式（display / border-collapse / 字体大小）
    html = replaceMarkdownOpeningTag(html, "table", 'class="table-auto w-full text-xs border border-slate-300"');
    html = replaceMarkdownOpeningTag(html, "thead", 'class="bg-slate-100"');
    html = replaceMarkdownOpeningTag(html, "th", 'class="border border-slate-300 px-2 py-1 text-left font-medium"');
    html = replaceMarkdownOpeningTag(html, "td", 'class="border border-slate-300 px-2 py-1"');
  }
  // 消毒 HTML，防止 XSS（保留允许的样式类和标签）
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS: [
      "p", "br", "strong", "em", "b", "i", "u", "ul", "ol", "li",
      "h1", "h2", "h3", "h4", "h5", "h6", "code", "pre", "span", "div",
      "table", "thead", "tbody", "tr", "th", "td", "hr", "style"
    ],
    ALLOWED_ATTR: ["class", "style"],
  });
};

import VisualAssetsPanel from "./components/VisualAssetsPanel";
import ToastContainer, { type ToastItem } from "./components/Toast";
import { useProjectWorkflow } from "./hooks/useProjectWorkflow";
import {
  STATUS_LABEL,
  WORKFLOW_STEPS,
  buildGateContext,
  buildWorkflowProgressDisclosure,
  buildWorkflowState,
  evaluateImageGenerationOutcome,
  formatWorkflowPageNumsForUser,
  getWorkflowProgressOverviewDisplay,
  getPrimaryActionKey,
  getSecondaryActionKeys,
  getStatusCard,
  planStaleSlideAction,
  type GateContext,
  type GateActionKey,
  type SlideStaleFlags,
  type StatusActionKey,
  type StatusCardData,
  type WorkflowGate,
} from "./workflow";
import { StatusCard } from "./components/StatusCard";
import { buildSelectedStylePreview } from "./selectedStylePreview";
import {
  inferAgentRequestContext,
  inferRequestedPageCount,
  type AgentRequestContext,
  type AgentRequestScope,
  type AgentRole,
  type AgentTargetArea,
} from "./agentRequestContext";
import {
  buildChangeReceipt,
  formatPageNumsForReceipt,
  summarizeContentChange,
  summarizeInsertedSlide,
  summarizeVisualChange,
} from "./changeReceipt";

import {
  API_BASE,
  fetchProjects,
  fetchProject,
  createProject,
  generateContentPlan,
  generateVisualPrompts,
  fetchSlides,
  startGeneration,
  stopGeneration,
  confirmPrototype,
  fetchWorkflowStatus,
  getDownloadUrl,
  startEditablePptx,
  getEditableDownloadUrl,
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
  updateSlideType,
  deleteSlide,
  createSlide,
  reorderSlides,
  extractTemplate,
  fetchTemplatePages,
  fetchTemplateStatus,
  updateTemplateRecommendations,
  rollbackProject,
  finetuneSlide,
  getSlideVersions,
  deleteSlideVersion,
  restoreSlideVersion,
  type EditablePptxMode,
} from "./api/client";

const EDITABLE_PPTX_MODE_OPTIONS: Array<{ value: EditablePptxMode; label: string; hint: string }> = [
  { value: "standard", label: "标准版（推荐）", hint: "主标题、正文、结论优先，视觉最稳" },
  { value: "enhanced", label: "增强版", hint: "额外拆卡片标签和图表主要标签" },
  { value: "aggressive", label: "激进版", hint: "尽量全拆，可能影响画面" },
];

interface Project {
  id: string;
  title: string;
  status: string;
  style_id: string | null;
  content_plan_confirmed: boolean;
  style_proposal: any | null;
  selected_style: any | null;
  selected_template_recommendations: any | null;
  intent_contract?: Record<string, any> | null;
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

function resolveAssetUrl(apiBase: string, url?: string | null) {
  if (!url) return "";
  if (/^(https?:|data:|blob:)/i.test(url)) return url;
  return `${apiBase}${url.startsWith("/") ? url : `/${url}`}`;
}

type TemplateApplicationStrength = "light" | "standard" | "strong";

const TEMPLATE_CONFIRM_TYPES = [
  { key: "cover", label: "封面" },
  { key: "toc", label: "目录" },
  { key: "section", label: "章节" },
  { key: "content", label: "内容" },
  { key: "data", label: "数据" },
  { key: "quote", label: "金句/强调" },
  { key: "ending", label: "封底" },
] as const;

function buildDefaultTemplateSelection(pages: any[]) {
  const firstPage = pages[0]?.page_num || 1;
  const byCategory = new Map<string, number>();
  pages.forEach((page) => {
    const category = String(page?.category || "content");
    if (!byCategory.has(category)) byCategory.set(category, Number(page.page_num) || firstPage);
  });
  const fallbackContent = byCategory.get("content") || firstPage;
  return TEMPLATE_CONFIRM_TYPES.reduce<Record<string, number>>((acc, item) => {
    acc[item.key] = byCategory.get(item.key) || (item.key === "quote" ? byCategory.get("hero") : undefined) || fallbackContent;
    return acc;
  }, {});
}

function projectStyleLabel(project: Project): string {
  return (
    project.selected_style?.name ||
    project.style_proposal?.proposals?.[0]?.name ||
    project.style_id ||
    "默认风格"
  );
}

function compactContractValue(value: any): string {
  if (value == null) return "";
  if (Array.isArray(value)) {
    return value.map(compactContractValue).filter(Boolean).join("、").slice(0, 140);
  }
  if (typeof value === "object") {
    const preferred = value.summary || value.description || value.title || value.name || value.value;
    if (preferred) return compactContractValue(preferred);
    return Object.values(value).map(compactContractValue).filter(Boolean).join("、").slice(0, 140);
  }
  return String(value).replace(/\s+/g, " ").trim().slice(0, 140);
}

function pickContractValue(contract: Record<string, any>, keys: string[]): string {
  for (const key of keys) {
    const value = compactContractValue(contract[key]);
    if (value) return value;
  }
  return "";
}

function buildAgentContractRows(intent_contract?: Record<string, any> | null) {
  const contract = intent_contract || {};
  return [
    { label: "听众", value: pickContractValue(contract, ["audience", "target_audience", "reader", "listeners"]) },
    { label: "目标", value: pickContractValue(contract, ["goal", "objective", "purpose", "desired_outcome"]) },
    { label: "判断", value: pickContractValue(contract, ["decision", "user_decision", "call_to_action", "ask"]) },
    { label: "口径", value: pickContractValue(contract, ["tone", "voice", "style", "narrative_tone"]) },
    { label: "页数", value: pickContractValue(contract, ["page_count", "target_page_count", "estimated_pages"]) },
  ].filter((row) => row.value);
}

interface Slide {
  id: string;
  page_num: number;
  type: string;
  type_locked?: boolean;
  status: string;
  content_json: any;
  visual_json: any;
  prompt_text: string | null;
  image_path?: string | null;
  error_msg?: string | null;
  reference_images?: { id: string; role: string; url: string }[];
}

const slidesForAgentRequestContext = (items: Slide[]) =>
  items.map((slide) => {
    const textContent = slide.content_json?.text_content || {};
    return {
      page_num: slide.page_num,
      type: slide.type || slide.content_json?.type || slide.visual_json?.type || "",
      headline: textContent.headline || slide.content_json?.headline || slide.visual_json?.headline || "",
      section_title: slide.content_json?.section_title || slide.visual_json?.section_title || "",
    };
  });

const DEFAULT_PROTOTYPE_SAMPLE_COUNT = 3;
const PROTOTYPE_FAMILY_ORDER = ["bookend", "toc", "content", "section", "hero", "data"];

const normalizePrototypeFamily = (value: unknown): string => {
  const family = String(value || "").toLowerCase();
  if (family === "cover" || family === "ending") return "bookend";
  if (family === "quote") return "hero";
  return family || "content";
};

const inferPrototypeFamily = (slide: Slide): string => {
  const visualFamily = slide.visual_json?.seed_family;
  if (visualFamily) return normalizePrototypeFamily(visualFamily);
  return normalizePrototypeFamily(slide.visual_json?.type || slide.type || "content");
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
    .filter((pageNum): pageNum is number => Number.isFinite(pageNum))
    .slice(0, DEFAULT_PROTOTYPE_SAMPLE_COUNT);
};

interface ColorChip {
  name: string;
  hex: string;
  role?: string;
}

interface StyleProposal {
  name: string;
  palette: (string | ColorChip)[];
  mood: string;
  font: string;
  description: string;
  decision_label?: string;
  best_for?: string;
  tradeoff?: string;
  visual_focus?: string;
  visual_strategy?: {
    summary?: string;
    background_policy?: string;
    content_treatment?: string;
    exception_policy?: string;
    logo_contrast?: string;
    base_tone?: string;
  };
  page_type_adaptation?: string;
  content_style_hint?: string;
  source?: string;
}

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
  chat_context?: string;
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
  statusKey?: string;
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
  statusKey?: string;
}

function contentPlanCompletionText(totalSlides?: number) {
  const count = Math.max(0, Number(totalSlides || 0));
  return `✅ 内容规划已完成${count ? `，共 ${count} 页` : ""}。请检查标题和顺序，确认后进入下一步。`;
}

function isContentPlanCompletionMessage(message: ChatMessage) {
  const content = (message.content || "").trim();
  return (
    message.role === "agent" &&
    message.agentRole === "content" &&
    /^✅?\s*内容规划已完成(?:，共\s*\d+\s*页)?。请检查标题和顺序，确认后进入下一步。$/.test(content)
  );
}

function getAgentStatusMessageKey(message: ChatMessage) {
  if (message.role !== "agent") return null;
  const agentRole = message.agentRole || "content";
  if (message.runId && !message.loading) return `run:${agentRole}:${message.runId}`;
  if (message.statusKey && !message.loading) return `status:${agentRole}:${message.statusKey}`;
  if (isQualityReportChatMessage(message)) return `status:${agentRole}:quality-report`;

  const content = (message.content || "")
    .replace(/^(?:✅|⚠️|⚠|❌|🚀|⏳|👉|\s)+/gu, "")
    .replace(/\s+/g, " ")
    .trim();
  if (!content) return null;
  if (isContentPlanCompletionMessage(message)) return "status:content:content-plan-ready";
  if (/^(?:视觉方向已生成|视觉方案阶段已就绪|已进入视觉方案阶段)/.test(content)) return "status:visual:style-ready";
  if (/^(?:每页的设计描述已生成|视觉方向已确认|页面状态已更新)/.test(content)) return "status:visual:visual-prompts-ready";
  if (/^样张已生成/.test(content)) return "status:visual:prototype-ready";
  if (/^(?:全量生成已完成|全部页面生成完成|图片生成任务已结束)/.test(content)) return "status:visual:generation-result";
  const problemMatch = content.match(/^(内容规划|视觉方向|画面方案|样张生成|图片生成)未完成/);
  if (problemMatch) return `status:${agentRole}:${problemMatch[1]}-problem`;
  return null;
}

function dedupeAgentStatusMessages(messages: ChatMessage[]) {
  return messages.reduce<ChatMessage[]>((acc, message) => {
    const key = getAgentStatusMessageKey(message);
    if (key) {
      const existingIndex = acc.findIndex((item) => getAgentStatusMessageKey(item) === key);
      if (existingIndex >= 0) acc.splice(existingIndex, 1);
    }
    acc.push(message);
    return acc;
  }, []);
}

function upsertAgentStatusMessage(messages: ChatMessage[], message: ChatMessage) {
  const key = getAgentStatusMessageKey(message);
  return [
    ...messages.filter((item) => {
      if (message.runId && item.loading && item.runId === message.runId) return false;
      return !key || getAgentStatusMessageKey(item) !== key;
    }),
    message,
  ];
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
      ? "样张生成"
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
      content: `⚠️ ${scopeLabel}未完成${scopedFailed ? `（${scopedFailed} 页失败）` : ""}。${errorLine}${canRetryFailedPages ? "可点击状态栏「重试失败页」继续。" : "请检查页面状态后重新发起。"}`,
      nextAction: canRetryFailedPages ? { type: "retry_failed", label: "一键重试失败页", confirm: true } : undefined,
      statusKey: `${runKind || "run"}-problem`,
    };
  }
  if (runKind === "content_plan") {
    return {
      agentRole: "content",
      content: contentPlanCompletionText(totalSlides),
      nextAction: { type: "switch_to_visual", label: "确认内容，请视觉总监" },
      statusKey: "content-plan-ready",
    };
  }
  if (runKind === "style_proposal") {
    if (styleProposalCount > 0) {
      return {
        agentRole: "visual",
        content: `✅ 视觉方向已生成，共 ${styleProposalCount} 套。\n\n👉 下一步：在作品画布选择一套方向，点击「确认并生成画面方案」。`,
        statusKey: "style-ready",
      };
    }
    return {
      agentRole: "visual",
      content: "✅ 已进入视觉方案阶段。\n\n👉 下一步：先在「项目素材」补充 Logo、参考图或模板；没有素材也可以直接点击「生成视觉方向」。",
      nextAction: { type: "generate_style_proposals", label: "生成视觉方向" },
      statusKey: "style-ready",
    };
  }
  if (runKind === "visual_prompts") {
    return {
      agentRole: "visual",
      content: "✅ 每页的设计描述已生成。先生成几页样张看看效果，满意后再出全部页面。",
      nextAction: { type: "start_prototype", label: "生成样张", confirm: true },
      statusKey: "visual-prompts-ready",
    };
  }
  if (projectStatus === "prototype_ready") {
    return {
      agentRole: "visual",
      content: "✅ 样张已生成。满意就点击「样张满意，生成全部」，不满意可以勾选页面重打。",
      nextAction: { type: "confirm_prototype", label: "样张满意，生成全部", confirm: true },
      statusKey: "prototype-ready",
    };
  }
  if (runKind === "batch_generation" || runKind === "page_generation" || runKind === "retry_failed") {
    if (projectStatus === "completed") {
      return {
        agentRole: "visual",
        content: `✅ 全量生成已完成，共 ${completedCount} / ${totalSlides || completedCount} 页。\n\n👉 下一步：点击右上角「下载图片版 PPTX」获取文件；需要调整时可选中页面重新生成。`,
        statusKey: "generation-result",
      };
    }
    if (projectStatus === "failed") {
      return {
        agentRole: "visual",
        content: `⚠️ 图片生成任务已结束，当前已有 ${completedCount} / ${totalSlides || completedCount} 页完成。\n\n👉 下一步：点击「一键重试失败页」继续补齐。`,
        nextAction: { type: "retry_failed", label: "一键重试失败页", confirm: true },
        statusKey: "generation-result",
      };
    }
    return {
      agentRole: "visual",
      content: `✅ 图片生成任务已结束，当前已有 ${completedCount} / ${totalSlides || completedCount} 页完成。\n\n👉 下一步：检查失败页并重试，或继续调整需要修改的页面。`,
      statusKey: "generation-result",
    };
  }
  if (projectStatus === "visual_ready" && !hasSelectedStyle) {
    if (styleProposalCount > 0) {
      return {
        agentRole: "visual",
        content: "✅ 视觉方案阶段已就绪。\n\n👉 下一步：在作品画布选择一套视觉方向，点击「确认并生成画面方案」。",
        statusKey: "style-ready",
      };
    }
    return {
      agentRole: "visual",
      content: "✅ 视觉方案阶段已就绪。\n\n👉 下一步：先在「项目素材」补充 Logo、参考图或模板；没有素材也可以直接点击「生成视觉方向」。",
      nextAction: { type: "generate_style_proposals", label: "生成视觉方向" },
      statusKey: "style-ready",
    };
  }
  if (projectStatus === "visual_ready" && hasSelectedStyle && !hasPrompt) {
    return {
      agentRole: "visual",
      content: "✅ 视觉方向已确认。\n\n👉 下一步：生成每页画面方案和生图 Prompt，然后生成样张。",
      nextAction: { type: "generate_visual_prompts", label: "生成画面方案" },
      statusKey: "visual-prompts-ready",
    };
  }
  if (projectStatus === "prompt_ready" || (hasSelectedStyle && hasPrompt)) {
    return {
      agentRole: "visual",
      content: "✅ 页面状态已更新。\n\n👉 下一步：检查每页画面方案，然后点击「生成样张」。",
      nextAction: { type: "start_prototype", label: "生成样张", confirm: true },
      statusKey: "visual-prompts-ready",
    };
  }
  return {
    agentRole: "visual",
    content: `✅ 当前任务已结束，页面状态已更新为「${status}」。\n\n👉 下一步：请查看作品画布里的当前阶段操作按钮，或直接告诉我你想继续怎么改。`,
    statusKey: "stage-updated",
  };
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

function isQualityReportChatMessage(message: ChatMessage) {
  const content = (message.content || "").trim();
  if (String(message.id || "").startsWith("quality-report-")) return true;
  if (message.agentRole !== "visual") return false;
  return (
    content.startsWith("⚠️ **还不能交付最终稿**") ||
    content.startsWith("✅ **可以导出最终稿**") ||
    content.startsWith("✅ **可以交付最终稿**")
  );
}

function sanitizeChatHistory(messages: ChatMessage[]) {
  return dedupeAgentStatusMessages((messages || []).filter((m) => !isTransientRunMessage(m)));
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
  if (/logo placeholder/i.test(text)) {
    const page = text.match(/page\s+(\d+)/i)?.[1];
    return `${page ? `第 ${page} 页` : "有页面"}只生成了 Logo 占位信息，未产出可用画面方案。系统已停止本轮生成，避免继续产出不可用页面。请重试；如果仍失败，可补充 Logo 或说明不要使用 Logo。`;
  }
  if (/429|rate limit|too many requests/i.test(text)) {
    return "生图接口当前限流或繁忙。系统会按接口返回的等待时间重试；如果仍失败，请稍后重试失败页。";
  }
  return text.replace(/\b[a-z]+(?:_[a-z0-9]+){2,}\b/g, "素材状态异常").trim() || "未知错误";
}

function runProgressText(run: any) {
  if (!run) return "任务处理中...";
  const disclosure = buildWorkflowProgressDisclosure({ active_run: run });
  if (disclosure?.summary) return disclosure.summary;
  const total = Math.max(0, Number(run.total_count || 0));
  const completed = Math.min(total || Number(run.completed_count || 0), Math.max(0, Number(run.completed_count || 0)));
  const fallback =
    run.kind === "content_plan"
      ? "正在生成内容规划"
      : run.kind === "style_proposal"
      ? "正在生成风格提案"
      : run.kind === "visual_prompts"
      ? "正在生成每页画面方案"
      : run.kind === "prototype_generation"
      ? "正在生成样张"
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

function formatPageScope(pageNums: number[], empty = "未选择页面") {
  return formatWorkflowPageNumsForUser(pageNums, 4) || empty;
}

function isImageRunKind(kind?: string | null) {
  return ["prototype_generation", "batch_generation", "page_generation", "retry_failed", "finetune"].includes(String(kind || ""));
}

function workflowProgressText(status: any) {
  const disclosure = buildWorkflowProgressDisclosure(status);
  if (disclosure?.summary) return disclosure.summary;
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
      : `正在处理第 ${formatPageNums(activePages)} 页`;
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
  const rawPath = String(imagePath || "");
  const outputMarker = "/outputs/";
  let publicPath = rawPath.replace("./outputs", "/outputs");
  const outputIndex = publicPath.indexOf(outputMarker);
  if (outputIndex >= 0) {
    publicPath = publicPath.slice(outputIndex);
  } else if (publicPath && !publicPath.startsWith("/")) {
    publicPath = `/${publicPath}`;
  }
  const base = `${API_BASE}${publicPath}`;
  const version = cacheKey ?? `${status || "image"}-${IMAGE_URL_SESSION_KEY}`;
  const cacheBuster = `?v=${encodeURIComponent(String(version))}`;
  return `${base}${cacheBuster}`;
}

function shouldShowLogoOverlay(slide: any) {
  const policy = slide?.visual_json?.logo_policy;
  const pageType = String(slide?.visual_json?.type || slide?.type || "content").toLowerCase();
  const layout = String(slide?.visual_json?.layout || "").toLowerCase();
  const optionalLogoPage =
    pageType === "section" ||
    pageType === "hero" ||
    pageType === "quote" ||
    layout === "hero" ||
    layout === "content_hero";
  if (String(policy?.render_variant || "").toLowerCase() === "omit") return optionalLogoPage ? false : true;
  if (policy && typeof policy.show_logo === "boolean") return optionalLogoPage ? policy.show_logo : true;
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

function visualAssetIdsForSlide(slide: any) {
  const ids: string[] = [];
  const addId = (value: any) => {
    const id = String(value || "");
    if (id && !ids.includes(id)) ids.push(id);
  };
  if (Array.isArray(slide?.visual_json?.visual_asset_ids)) {
    slide.visual_json.visual_asset_ids.forEach(addId);
  }
  if (Array.isArray(slide?.visual_json?.manual_visual_asset_ids)) {
    slide.visual_json.manual_visual_asset_ids.forEach(addId);
  }
  if (Array.isArray(slide?.visual_json?.overlay_layers)) {
    slide.visual_json.overlay_layers.forEach((layer: any) => {
      if (layer?.enabled !== false) addId(layer?.asset_id);
    });
  }
  return ids;
}

function referenceDisplayName(ref: any, fallback = "参考图") {
  return ref?.asset_name || ref?.asset_analysis?.subject || fallback;
}

function directReplicateSourceFacts(slide: any) {
  const content = slide?.content_json || {};
  const facts = content.source_facts || {};
  if (facts.mode !== "direct_ppt_replicate" && content.generation_status !== "pptx_direct") return null;
  const sourcePage = Number(facts.source_page_num || slide?.page_num || 0);
  return {
    sourcePage: Number.isFinite(sourcePage) && sourcePage > 0 ? sourcePage : slide?.page_num,
    qualityStatus: content.replicate_quality?.status || "passed",
  };
}

function isSourcePageReference(ref: any) {
  const analysis = ref?.asset_analysis || {};
  return analysis.classification === "source_slide_render" || analysis.analysis_type === "source_slide_preview";
}

function referenceThumbTitle(ref: any) {
  if (isSourcePageReference(ref)) return "原稿页面 — 点击查看";
  return "本页参考图 — 点击查看";
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
  const match = String(raw || "").trim().match(/^#?((?:[0-9a-fA-F]{3}){1,2})$/);
  return match ? `#${match[1].toUpperCase()}` : "#d1d5db";
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
        { name: "暖灰紫", hex: "#B0A8C0", role: "中性点缀/装饰线/标签" },
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
    visual_strategy: wantsLight
      ? {
          base_tone: "light",
          summary: "整套页面以白色/米白/浅色明亮基底为主，内容页和数据页优先高可读。",
          background_policy: "封面、章节、正文、数据和表格页都以浅色基底为主。",
          content_treatment: "正文页、内容页、数据页和表格页使用浅色信息基底、留白、卡片和深色文字保证阅读效率。",
          exception_policy: "深色只用于文字、细线或局部强调，不作为内容页整页基底。",
        }
      : wantsDarkTech
      ? {
          base_tone: "dark",
          summary: "整套页面使用深色科技基底，正文和数据页也保持深色信息层级。",
          background_policy: "封面、章节、正文、数据和表格页都沿用深色基底。",
          content_treatment: "正文页使用深色内容区、高对比文字、冷色光效和清晰网格保证阅读效率。",
        }
      : baseStyle?.visual_strategy,
    page_type_adaptation: wantsLight
      ? "页面类型适配规则：整套页面以白色、米白或浅色明亮基底为主；内容页、数据页、表格页必须保持浅底高可读，只用品牌色做编号、细线、标签或重点数字。"
      : wantsDarkTech
      ? "页面类型适配规则：整套页面统一深色科技基底；正文/内容/数据/表格页也必须保持深色底，通过卡片、留白和高对比文字解决可读性。"
      : baseStyle?.page_type_adaptation,
    content_style_hint: wantsLight
      ? "用户要求改为浅色内容页；后续画面方案和 Prompt 必须继承浅色信息基底。"
      : wantsDarkTech
      ? "用户要求强化深色科技感；后续画面方案和 Prompt 必须继承深色基底。"
      : baseStyle?.content_style_hint,
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

function logoOverlaySrc(item: any) {
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
              src={resolveAssetUrl(API_BASE, asset.url)}
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
                src={`${API_BASE}${logoOverlaySrc(item)}`}
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
    adoptWorkflowRun,
    activeRun,
    hasActiveRun,
  } = useProjectWorkflow(selectedProject?.id || null);
  const currentProjectStatus = projectStatus?.project_id === selectedProject?.id ? projectStatus : null;
  const handleWorkflowRunStarted = useCallback((projectId: string, run?: any | null) => {
    if (!run?.id || selectedProjectIdRef.current !== projectId) return;
    adoptWorkflowRun(run);
    void refreshWorkflowStatus();
  }, [adoptWorkflowRun, refreshWorkflowStatus]);
  const [gateRevisionMap, setGateRevisionMap] = useState<Record<string, number>>({});
  const gateRevision = selectedProject ? gateRevisionMap[selectedProject.id] || 0 : 0;

  // 追踪当前活跃的聊天流属于哪个项目/角色，防止状态跳到别的窗口
  const activeChatProjectIdRef = useRef<string | null>(null);
  const activeChatRoleRef = useRef<string | null>(null);
  const activeChatGateRef = useRef<string | null>(null);
  const activeChatGateRevisionRef = useRef<number | null>(null);
  const editableDownloadRunIdRef = useRef<string | null>(null);
  const editableDownloadModeRef = useRef<EditablePptxMode>("standard");

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

  // Agent 指令作用范围，独立于打样页选择。
  const [agentScope, setAgentScope] = useState<AgentRequestScope>("deck");
  const [agentTargetAreaOverride, setAgentTargetAreaOverride] = useState<AgentTargetArea | null>(null);

  // 新建项目弹窗
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newTitle, setNewTitle] = useState("");


  const isBusy = operatingProjectId === selectedProject?.id || hasActiveRun;
  const [deckSelectedPages, setDeckSelectedPages] = useState<Set<number>>(new Set());
  const [selectedPages, setSelectedPages] = useState<Set<number>>(new Set());
  const [prototypeSelectionTouched, setPrototypeSelectionTouched] = useState(false);
  const visiblePrototypePageNumsRef = useRef<number[]>([]);
  const [showPrototypePreview, setShowPrototypePreview] = useState(true);
  const [referenceImages, setReferenceImages] = useState<any[]>([]);
  const [templatePages, setTemplatePages] = useState<any[]>([]);
  const [templateConfirmVisible, setTemplateConfirmVisible] = useState(false);
  const [templateConfirmSaving, setTemplateConfirmSaving] = useState(false);
  const [templateConfirmDismissedProjectId, setTemplateConfirmDismissedProjectId] = useState<string | null>(null);
  const [templatePageSelection, setTemplatePageSelection] = useState<Record<string, number>>({});
  const [templateApplicationStrength, setTemplateApplicationStrength] = useState<TemplateApplicationStrength>("standard");
  const [showAdvancedMapping, setShowAdvancedMapping] = useState(false);

  // 主舞台折叠状态：默认折叠以节省空间
  const [styleBarExpanded, setStyleBarExpanded] = useState(false);
  const [assetsBarExpanded, setAssetsBarExpanded] = useState(false);
  const assetsGuidanceExpandedProjectRef = useRef<string | null>(null);

  const [chatInput, setChatInput] = useState("");
  const [activeTypeMenuSlideId, setActiveTypeMenuSlideId] = useState<string | null>(null);
  useEffect(() => {
    if (!activeTypeMenuSlideId) return;
    const handleClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest("[data-type-menu]")) {
        setActiveTypeMenuSlideId(null);
      }
    };
    document.addEventListener("click", handleClick);
    return () => document.removeEventListener("click", handleClick);
  }, [activeTypeMenuSlideId]);
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
  const [staleMap, setStaleMap] = useState<Record<string, { content?: boolean; visual?: boolean; image?: boolean; localImage?: boolean }>>({});

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
        const prevStale = next[slide.id] || {};
        const backendStale = getSlideStaleFlags(slide);
        const hydrated: { content?: boolean; visual?: boolean; image?: boolean; localImage?: boolean } = {};
        if (backendStale.content) hydrated.content = true;
        if (backendStale.visual) hydrated.visual = true;
        if (backendStale.image || prevStale.localImage) hydrated.image = true;
        if (prevStale.localImage && !backendStale.image) hydrated.localImage = true;
        if (hydrated.content || hydrated.visual || hydrated.image) {
          next[slide.id] = hydrated;
        } else {
          delete next[slide.id];
        }
      });
      return next;
    });
  };

  const markSlideStale = (slideId: string, type: "content" | "visual" | "image") => {
    setStaleMap((prev) => ({
      ...prev,
      [slideId]: { ...prev[slideId], [type]: true, ...(type === "image" ? { localImage: true } : {}) },
    }));
  };

  const clearTransientProjectState = (nextProjectId?: string) => {
    saveActiveComposerDraft();
    activeComposerDraftKeyRef.current = null;
    suspendComposerDraftPersistRef.current = chatInputValueRef.current !== "";
    const cachedSlides = nextProjectId ? slidesCacheRef.current[nextProjectId] : undefined;
    setProjectStatus(null);
    setOperatingProjectId(null);
    generationLoadingIdRef.current = null;
    setReferenceImages([]);
    setDocuments([]);
    setTemplatePages([]);
    setTemplateConfirmVisible(false);
    setTemplateDrawerOpen(false);
    setTemplateConfirmDismissedProjectId(null);
    setTemplatePageSelection({});
    setTemplateApplicationStrength("standard");
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
    setDeckSelectedPages(new Set());
    setSelectedPages(new Set());
    setPrototypeSelectionTouched(false);
    setEditingSlide(null);
    setAgentScope("deck");
    setContentPlanSnapshot([]);
    setStyleProposalsInChat([]);
    setExpandedStyleProposalKey(null);
    setAgentMaterialSheetOpen(false);
    setAgentScopePickerOpen(false);
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
        if (type === "image") delete next.localImage;
        if (!next.content && !next.visual && !next.image) {
          const withoutSlide = { ...prev };
          delete withoutSlide[slideId];
          return withoutSlide;
        }
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
  const staleActionPlan = planStaleSlideAction(staleSlides);
  const hasContentOrVisualStale = staleActionPlan.contentOrVisualCount > 0;
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
  const postedQualityReportSignaturesRef = useRef<Set<string>>(new Set());
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
  const [templateDrawerOpen, setTemplateDrawerOpen] = useState(false);
  const [agentMaterialSheetOpen, setAgentMaterialSheetOpen] = useState(false);
  const [agentScopePickerOpen, setAgentScopePickerOpen] = useState(false);
  const [agentAreaPickerOpen, setAgentAreaPickerOpen] = useState(false);
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
    setAgentTargetAreaOverride(null);
    setAgentAreaPickerOpen(false);
  }, [selectedProject?.id, currentAgentRole]);

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
    setTemplateDrawerOpen(false);
    setAgentMaterialSheetOpen(false);
    setAgentScopePickerOpen(false);

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
                content: `${changedSlides.length} 页内容有变更，需要更新页面设计。已有图片会保留，确认后重新生成。`,
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
        page_num: ref.page_num || idx + 1,
        url: resolveAssetUrl(API_BASE, ref.url),
        layout_url: ref.layout_url ? resolveAssetUrl(API_BASE, ref.layout_url) : undefined,
        category: ref.category || "content",
        category_confidence: ref.category_confidence,
        source_kind: ref.source_kind,
        application_strength: ref.application_strength,
        logo_removed: Boolean(ref.logo_removed),
      }));
      setTemplatePages(pages);
      if (pages.length > 0) setTemplatePageSelection(buildDefaultTemplateSelection(pages));
    } catch (err: any) {
      if (isProjectNotFoundError(err)) {
        void recoverMissingProject(projectId);
        return;
      }
      showToast("加载模板页面失败：" + (err.message || "未知错误"), "error");
      if (loadingProjectIdRef.current === projectId) setTemplatePages([]);
    }
  };

  const waitForTemplateExtraction = async (projectId: string, jobId?: string | null) => {
    for (let attempt = 0; attempt < 80; attempt++) {
      await new Promise((r) => setTimeout(r, 1500));
      const status = await fetchTemplateStatus(projectId);
      if (jobId && status?.job_id && status.job_id !== jobId) continue;
      if (status?.status === "completed") return status;
      if (status?.status === "failed") {
        throw new Error(status.error || "模板提取失败，请重试");
      }
    }
    throw new Error("模板提取还没有完成，请稍后刷新模板页面");
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
      chatContext?: string;
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
      updateLoadingMsg("正在读取原 PPT 的文字和页面截图，准备生成内容规划。");
      // 记录旧 slides 的 ID，用于区分"旧内容还在"和"新生成完成"
      const previousSlides = await loadSlides(projectId);
      const previousSlideIds = previousSlides.map((s: any) => s.id).sort().join(",");
      updateLoadingMsg("正在向后台提交内容规划任务...");
      const result = await generateContentPlan(projectId, topic, pageCount, options?.attachmentIds, options?.chatContext);
      handleWorkflowRunStarted(projectId, result?.run);
      const contentPlanRunId = result?.run?.id ? String(result.run.id) : null;
      if (result?.run?.id) {
        locallyHandledRunIdsRef.current.add(String(result.run.id));
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
      const deferContentPlanPoll = (message: string) => {
        pollCompleted = true;
        cleanupContentPlanPoll();
        updateProjectChatMessages(projectId, "content", (prev) => [
          ...prev,
          {
            role: "agent",
            content: "内容规划仍在后台生成：" + message,
            agentRole: "content",
          },
        ]);
      };

      progressInterval = setInterval(async () => {
        if (pollCompleted) return;
        try {
          if (Date.now() - startedAt > CONTENT_PLAN_TIMEOUT_MS) {
            deferContentPlanPoll("这次内容较长，等待时间超过了页面自动跟踪范围。后台任务可能还在继续，请稍后刷新页面查看结果，不要重复点击。");
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
            deferContentPlanPoll("这次内容较长，等待时间超过了页面自动跟踪范围。后台任务可能还在继续，请稍后刷新页面查看结果，不要重复点击。");
            return;
          }
          const currentSlides = await loadSlides(projectId);
          if (pollCompleted) return;
          await loadProjects();
          const workflow = await fetchWorkflowStatus(projectId);
          if (selectedProjectIdRef.current === projectId) {
            setProjectStatus(workflow?.project_id === projectId ? workflow : null);
          }
          if (pollCompleted) return;
          const currentSlideIds = currentSlides.map((s: any) => s.id).sort().join(",");
          const activeContentPlan =
            workflow?.active_run?.kind === "content_plan" &&
            (!contentPlanRunId || String(workflow.active_run.id) === contentPlanRunId) &&
            isRunActive(workflow.active_run);
          const contentPlanSucceeded =
            !contentPlanRunId ||
            (workflow?.last_run?.id === contentPlanRunId && String(workflow.last_run.status || "") === "succeeded");
          // 必须有 slides，且 ID 集合与旧内容不同（说明是新生成的），才认为完成
          if (currentSlides.length > 0 && currentSlideIds !== previousSlideIds && !activeContentPlan && contentPlanSucceeded) {
            pollCompleted = true;
            cleanupContentPlanPoll();
            const latestGateContext = gateContextRef.current || gateContext;
            updateProjectChatMessages(projectId, "content", (prev) =>
              upsertAgentStatusMessage(prev, {
                role: "agent",
                content: contentPlanCompletionText(currentSlides.length),
                agentRole: "content",
                nextAction: { type: "switch_to_visual", label: "确认内容，请视觉总监" },
                gate: latestGateContext.gate,
                gateRevision: latestGateContext.gateRevision,
                runId: contentPlanRunId || undefined,
                statusKey: "content-plan-ready",
              })
            );
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
        removeLoadingMsg();
      const message = "内容规划生成失败：" + (err.message || "未知错误");
      updateProjectChatMessages(projectId, "content", (prev) => [
        ...prev,
        {
          role: "agent",
          content: "❌ 生成失败：" + message + "。请告诉我你的主题，我会重新生成。",
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
        setReferenceImages([]);
      setDocuments([]);
      setTemplatePages([]);
      setTemplateConfirmVisible(false);
      setTemplateDrawerOpen(false);
      setTemplateConfirmDismissedProjectId(null);
      setTemplatePageSelection({});
      setTemplateApplicationStrength("standard");
      setDeckSelectedPages(new Set());
      setAgentScope("deck");
      setAgentScopePickerOpen(false);
      setAgentMaterialSheetOpen(false);
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
          const targetAgent = prevRun.kind === "content_plan" ? "content" : "visual";
          updateProjectChatMessages(pid, targetAgent, (prevMsgs) =>
            prevMsgs.filter((m) => m.id !== loadingId && !(m.loading && m.runId === prevRun.runId))
          );
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
          updateProjectChatMessages(pid, followup.agentRole, (prevMsgs) => {
            const nextMessage: ChatMessage = {
              role: "agent",
              content: followup.content,
              agentRole: followup.agentRole,
              nextAction: followup.nextAction,
              gate: followup.nextAction ? latestGateContext.gate : undefined,
              gateRevision: followup.nextAction ? latestGateContext.gateRevision : undefined,
              runId: finishedRunId || undefined,
              statusKey: followup.statusKey,
            };
            const baseMessages = prevMsgs.filter((m) => m.id !== loadingId && m.runId !== finishedRunId);
            return upsertAgentStatusMessage(baseMessages, nextMessage);
          });
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
            content: `✅ 全部 ${completedCount} 页已生成。点击右上角「下载图片版 PPTX」。`,
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
            content: `❌ ${slides.filter((s) => s.status === "failed").length} 页生成失败（第 ${slides.filter((s) => s.status === "failed").map((s) => s.page_num).join("、")} 页）。可点击状态栏的「重试失败页」，或选中单页修改后再生成。`,
            agentRole: "visual",
          },
        ]);
      }
    }
    prevProjectStatusRef.current = { projectId: pid, status: currentStatus };
    prevActiveRunRef.current = { projectId: pid, runId: activeRunId, kind: activeRun?.kind || null };
  }, [selectedProject?.id, selectedProject?.status, activeRun?.id]);

  useEffect(() => {
    const projectId = selectedProject?.id;
    const requestedRunId = editableDownloadRunIdRef.current;
    if (!projectId || !requestedRunId || hasActiveRun) return;
    const lastRun = currentProjectStatus?.last_run;
    if (lastRun?.id !== requestedRunId) return;
    if (lastRun.status === "succeeded") {
      const restoreMode = editableDownloadModeRef.current;
      editableDownloadRunIdRef.current = null;
      window.location.href = getEditableDownloadUrl(projectId, restoreMode);
      return;
    }
    if (["failed", "cancelled", "stale"].includes(String(lastRun.status || ""))) {
      editableDownloadRunIdRef.current = null;
      showToast(lastRun.message || "可编辑版准备失败，请稍后重试。", "error");
    }
  }, [
    selectedProject?.id,
    hasActiveRun,
    currentProjectStatus?.last_run?.id,
    currentProjectStatus?.last_run?.status,
  ]);

  // 最终态补充一份交付前检查，不阻塞生成完成提示和导出动作。
  useEffect(() => {
    const projectId = selectedProject?.id;
    if (!projectId) return;
    if (currentProjectStatus?.active_run) {
      updateProjectChatMessages(projectId, "visual", (prevMsgs) => {
        if (!prevMsgs.some(isQualityReportChatMessage)) return prevMsgs;
        return prevMsgs.filter((m) => !isQualityReportChatMessage(m));
      });
      return;
    }

    const report = currentProjectStatus?.quality_report;
    const signature = String(report?.signature || "").trim();
    const content = String(report?.message || "").trim();
    if (!signature || !content) return;

    const scopedSignature = `${projectId}:${signature}`;
    postedQualityReportSignaturesRef.current.add(scopedSignature);

    const messageId = `quality-report-${signature}`;
    updateProjectChatMessages(projectId, "visual", (prevMsgs) => {
      const baseMessages = prevMsgs.filter((m) => !isQualityReportChatMessage(m));
      return [
        ...baseMessages,
        {
          id: messageId,
          role: "agent",
          content,
          agentRole: "visual",
        },
      ];
    });
  }, [
    selectedProject?.id,
    currentProjectStatus?.active_run?.id,
    currentProjectStatus?.quality_report?.signature,
    currentProjectStatus?.quality_report?.message,
  ]);

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
          clearTransientProjectState(created.id);
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
          content: "❌ 保存失败：" + (err.message || "未知错误") + "。请检查网络后重试。",
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
        content: buildChangeReceipt({
          status: "queued",
          subject: `风格「${name}」已确认，正在生成每页画面描述和生图提示词`,
          change: "已保存所选视觉方向",
          next: `完成后会进入样张检查阶段。${pendingAssetsNote}`.trim(),
        }),
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
      handleWorkflowRunStarted(projectId, startResult.run);
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
            const terminalVisualPromptRun = visualPromptRunId
              ? [projectData?.last_run, projectData?.active_run].find(
                  (run) =>
                    run?.id === visualPromptRunId &&
                    ["failed", "stale", "cancelled"].includes(String(run.status || ""))
                )
              : null;
            if (terminalVisualPromptRun) {
              if (visualPromptIntervalRef.current) {
                clearInterval(visualPromptIntervalRef.current);
                visualPromptIntervalRef.current = null;
              }
              reject(new Error(userFacingGenerationError(terminalVisualPromptRun.error_msg || terminalVisualPromptRun.message || "画面方案生成失败")));
              return;
            }

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
      updateProjectChatMessages(projectId, "visual", (prev) =>
        upsertAgentStatusMessage(prev.filter((m) => m.id !== loadingId), {
          role: "agent",
          content: buildChangeReceipt({
            status: "applied",
            subject: "画面设计已完成：每页画面描述和生图提示词已生成",
            change: prototype && selectedPages.size > 0 ? `已处理${formatPageNumsForReceipt(Array.from(selectedPages))}` : `已处理整套 ${slides.length || "全部"} 页`,
            next: "先生成几页样张看效果；满意后点击「样张满意，生成全部」。",
          }),
          agentRole: "visual",
          nextAction: { type: "start_prototype", label: "生成样张", confirm: true },
          runId: visualPromptRunId || undefined,
          statusKey: "visual-prompts-ready",
        })
      );
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
    const modeText = prototype ? "样张生成" : "全量生成";
    const pageNums = explicitPageNums?.length
      ? explicitPageNums
      : useSelectedPages && selectedPages.size > 0
      ? Array.from(selectedPages)
      : undefined;
    const pageDesc = pageNums ? formatPageScope(pageNums) : (prototype ? "默认样张页" : "所有页面");
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
      handleWorkflowRunStarted(projectId, result.run);
      if (result?.run?.id) {
        generationRunId = String(result.run.id);
        locallyHandledRunIdsRef.current.add(generationRunId);
        updateProjectChatMessages(projectId, "visual", (prev) =>
          prev.map((m) => (m.id === loadingId ? { ...m, runId: result.run.id, content: runProgressText(result.run) } : m))
        );
      }
      const finalWorkflow = await pollUntilStatusNotGenerating(projectId);
      const finalRun = generationRunId && finalWorkflow?.last_run?.id === generationRunId
        ? finalWorkflow.last_run
        : null;
      const finalStatus = finalWorkflow?.project_status || "";
      const generationOutcome = evaluateImageGenerationOutcome({
        prototype,
        projectStatus: finalStatus,
        run: finalRun || (generationRunId ? { status: "stale", message: "没有找到本次生成任务的完成记录" } : null),
      });
      targetsClearForGeneration(pageNums);
      generationLoadingIdRef.current = null;
      updateProjectChatMessages(projectId, "visual", (prev) =>
        upsertAgentStatusMessage(prev.filter((m) => m.id !== loadingId), {
          role: "agent",
          content: generationOutcome.isSuccess
            ? `✅ ${generationOutcome.message}\n\n👉 下一步：${
                prototype
                  ? "检查样张效果；满意后点击「样张满意，生成全部」，不满意可以勾选页面重打样张或调整风格。"
                  : "点击右上角「下载图片版 PPTX」获取文件；需要调整时可选中页面重新生成。"
              }`
            : `⚠️ ${generationOutcome.message}\n\n👉 下一步：${
                prototype
                  ? "重打样张，或先调整视觉方案后再生成样张。"
                  : "检查失败页后重试，或选中页面单独重新生成。"
              }`,
          agentRole: "visual",
          nextAction: generationOutcome.canConfirmPrototype
            ? { type: "confirm_prototype", label: "样张满意，生成全部", confirm: true }
            : undefined,
          runId: generationRunId || undefined,
          statusKey: prototype ? "prototype-ready" : "generation-result",
        })
      );
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
          content: "❌ 启动失败：" + (err.message || "未知错误") + "。请检查网络后重试，或告诉我具体哪一页有问题。",
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
        content: "🚀 样张已确认，正在启动全量生成所有页面...",
        agentRole: "visual",
        loading: true,
        id: loadingId,
      },
    ]);
    let generationRunId: string | null = null;
    try {
      const result = await confirmPrototype(projectId);
      handleWorkflowRunStarted(projectId, result.run);
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
      const finalWorkflow = await pollUntilStatusNotGenerating(projectId);
      const finalRun = generationRunId && finalWorkflow?.last_run?.id === generationRunId
        ? finalWorkflow.last_run
        : null;
      const finalStatus = finalWorkflow?.project_status || "";
      const generationOutcome = evaluateImageGenerationOutcome({
        prototype: false,
        projectStatus: finalStatus,
        run: finalRun || (generationRunId ? { status: "stale", message: "没有找到本次生成任务的完成记录" } : null),
      });
      generationLoadingIdRef.current = null;
      updateProjectChatMessages(projectId, "visual", (prev) =>
        upsertAgentStatusMessage(prev.filter((m) => m.id !== loadingId), {
          role: "agent",
          content: generationOutcome.isSuccess
            ? "✅ 全部页面生成完成。\n\n👉 下一步：点击右上角「下载图片版 PPTX」获取文件；需要调整时可选中页面重新生成。"
            : `⚠️ ${generationOutcome.message}\n\n👉 下一步：检查失败页后重试，或选中页面单独重新生成。`,
          agentRole: "visual",
          runId: generationRunId || undefined,
          statusKey: "generation-result",
        })
      );
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

  const handleEditablePptxExport = async (restoreMode: EditablePptxMode) => {
    if (!selectedProject) return;
    const projectId = selectedProject.id;
    if (hasActiveRun || operatingProjectId === projectId) {
      showToast("当前已有任务在执行中，请稍后再试", "info");
      return;
    }
    if (selectedProject.status !== "completed" || !currentProjectStatus?.has_pptx) {
      showToast("请先完成全量 PPT 生成，再下载可编辑版 PPTX。", "info");
      return;
    }

    setOperatingProjectId(projectId);
    try {
      const result = await startEditablePptx(projectId, restoreMode);
      if (result?.status === "ready") {
        window.location.href = getEditableDownloadUrl(projectId, restoreMode);
        return;
      }
      if (result?.run?.id) {
        handleWorkflowRunStarted(projectId, result.run);
        const runId = String(result.run.id);
        editableDownloadRunIdRef.current = runId;
        editableDownloadModeRef.current = restoreMode;
        locallyHandledRunIdsRef.current.add(runId);
      }
      await refreshWorkflowStatus();
      showToast("正在准备可编辑版，完成后会自动下载。", "info");
    } catch (err: any) {
      showToast("可编辑版准备失败：" + (err.message || "未知错误"), "error");
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
        if (!isRunActive(statusData.active_run)) {
          await loadSlides(projectId);
          await loadProjects();
          return statusData;
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
	    let visualPromptRunId: string | null = null;
	    let generationRunId: string | null = null;
	    try {
	      if (needsPrompt) {
	        updateSinglePageRunMessage(`正在更新第 ${slide.page_num} 页画面方案...`);
	        showToast(`正在更新第 ${slide.page_num} 页画面方案...`, "info");
	        const visualPromptResult = await generateVisualPrompts(projectId, pageNums, stageContext);
	        handleWorkflowRunStarted(projectId, visualPromptResult.run);
	        visualPromptRunId = visualPromptResult?.run?.id ? String(visualPromptResult.run.id) : null;
	        if (visualPromptRunId) {
	          locallyHandledRunIdsRef.current.add(visualPromptRunId);
	          updateSinglePageRunMessage(runProgressText(visualPromptResult.run), { runId: visualPromptRunId });
	        }
	        const visualPromptWorkflow = await pollUntilStatusNotGenerating(projectId);
	        const visualPromptRun = visualPromptRunId && visualPromptWorkflow?.last_run?.id === visualPromptRunId
	          ? visualPromptWorkflow.last_run
	          : null;
	        if (visualPromptRun && String(visualPromptRun.status || "") !== "succeeded") {
	          throw new Error(userFacingGenerationError(visualPromptRun.error_msg || visualPromptRun.message || "画面方案生成失败"));
	        }
	        clearSlideStale(slideId, "content");
	        clearSlideStale(slideId, "visual");
	      }
	      updateSinglePageRunMessage(`正在启动第 ${slide.page_num} 页图片生成...`);
	      showToast(`正在重新生成第 ${slide.page_num} 页图片...`, "info");
	      const result = await startGeneration(projectId, pageNums);
	      handleWorkflowRunStarted(projectId, result.run);
	      generationRunId = result?.run?.id ? String(result.run.id) : null;
	      if (generationRunId) {
	        updateSinglePageRunMessage(runProgressText(result.run), { runId: generationRunId });
	      }
	      const finalWorkflow = await pollUntilStatusNotGenerating(projectId);
	      const finalRun = generationRunId && finalWorkflow?.last_run?.id === generationRunId
	        ? finalWorkflow.last_run
	        : null;
	      const generationOutcome = evaluateImageGenerationOutcome({
	        prototype: false,
	        projectStatus: finalWorkflow?.project_status || "",
	        run: finalRun || (generationRunId ? { status: "stale", message: "没有找到本次生成任务的完成记录" } : null),
	      });
	      if (!generationOutcome.isSuccess) {
	        throw new Error(generationOutcome.message);
	      }
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
	      if (visualPromptRunId) {
	        locallyHandledRunIdsRef.current.delete(visualPromptRunId);
	      }
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
  const handleUpdateStaleSlides = async (
    targetSlideIds?: string[],
    options?: { local?: boolean; silent?: boolean; staleOverride?: SlideStaleFlags; throwOnError?: boolean }
  ) => {
    if (!selectedProject) return;
    const projectId = selectedProject.id;
    const targets = targetSlideIds
      ? slides
          .filter((slide) => targetSlideIds.includes(slide.id))
          .map((slide) => {
            const backendStale = getSlideStaleFlags(slide);
            const hasBackendStale = Boolean(backendStale.content || backendStale.visual || backendStale.image);
            return {
              slide,
              stale: options?.staleOverride || staleMap[slide.id] || (hasBackendStale ? backendStale : { content: true }),
            };
          })
          .filter((x) => x.stale.content || x.stale.visual || x.stale.image)
      : staleSlides;
    if (targets.length === 0) return;

    const actionPlan = planStaleSlideAction(targets);
    if (actionPlan.contentOrVisualCount === 0) {
      if (actionPlan.imageOnlyCount > 0) {
        showToast(`${actionPlan.imageOnlyCount} 页图片已过期，请直接重新生成图片`, "info");
      }
      return;
    }

    if (!options?.local) {
      setOperatingProjectId(projectId);
    }
    let visualPromptRunId: string | null = null;
    try {
      const needsFullPlan = targets.filter((x) => x.stale.content);
      const needsPrompt = targets.filter((x) => x.stale.content || x.stale.visual);
      const pageNumsForPrompt = Array.from(new Set(needsPrompt.map((x) => x.slide.page_num)));
      const updatedSlideIds = new Set(needsPrompt.map((x) => x.slide.id));

      if (pageNumsForPrompt.length > 0) {
        showToast(`正在更新 ${pageNumsForPrompt.length} 页的画面方案...`, "info");
        const startResult = await generateVisualPrompts(projectId, pageNumsForPrompt, buildCrossStageContext("visual"));
        handleWorkflowRunStarted(projectId, startResult.run);
        if (startResult?.run?.id) {
          visualPromptRunId = String(startResult.run.id);
          locallyHandledRunIdsRef.current.add(visualPromptRunId);
          const startedWorkflow = await fetchWorkflowStatus(projectId);
          if (startedWorkflow?.project_id === projectId && selectedProjectIdRef.current === projectId) {
            setProjectStatus(startedWorkflow);
          }
        }
        const finalWorkflow = await pollUntilStatusNotGenerating(projectId);
        const finalRun = visualPromptRunId && finalWorkflow?.last_run?.id === visualPromptRunId
          ? finalWorkflow.last_run
          : null;
        if (finalRun && String(finalRun.status || "") !== "succeeded") {
          throw new Error(userFacingGenerationError(finalRun.error_msg || finalRun.message || "画面方案生成失败"));
        }
      }

      const freshSlides = await loadSlides(projectId);
      if (freshSlides.length > 0) {
        setContentPlanSnapshot(freshSlides);
      }
      const freshById = new Map(freshSlides.map((slide: Slide) => [slide.id, slide]));
      const unfinishedPages = needsPrompt
        .filter((x) => {
          const fresh = freshById.get(x.slide.id) as Slide | undefined;
          const freshStale = fresh ? getSlideStaleFlags(fresh) : {};
          const missingVisual = Boolean(x.stale.content) && !String(fresh?.visual_json?.visual_description || "").trim();
          const missingPrompt = !String(fresh?.prompt_text || "").trim();
          return !fresh || missingVisual || missingPrompt || freshStale.content || freshStale.visual;
        })
        .map((x) => x.slide.page_num);
      if (unfinishedPages.length > 0) {
        throw new Error(`第 ${unfinishedPages.join(", ")} 页更新后仍缺少画面方案，请重试。`);
      }

      const imageStale = freshSlides
        .filter((slide: Slide) => {
          if (!targets.some((x) => x.slide.id === slide.id)) return false;
          const stale = getSlideStaleFlags(slide);
          return Boolean(stale.image);
        });
      if (imageStale.length > 0) {
        showToast(`${imageStale.length} 页需重新生成图片，请先确认`, "info");
        if (!options?.silent) {
          updateProjectChatMessages(projectId, "visual", (prev) => [
            ...prev,
            {
              role: "agent",
              content: `${imageStale.length} 页需要重新生成图片。确认后点击重新生成。`,
              agentRole: "visual",
            },
          ]);
        }
      }

      const fullPlanCount = needsFullPlan.length;
      const promptOnlyCount = needsPrompt.length - needsFullPlan.length;
      const successText = fullPlanCount > 0 && promptOnlyCount > 0
        ? `已更新 ${needsPrompt.length} 页画面方案`
        : fullPlanCount > 0
        ? `已更新 ${fullPlanCount} 页画面描述和生图提示词`
        : `已更新 ${promptOnlyCount} 页生图提示词`;
      showToast(successText, "success");
      await loadProjects();
      if (updatedSlideIds.size > 0 && !options?.silent) {
        addSystemLog(`用户更新了 ${updatedSlideIds.size} 页的画面方案`);
        updateProjectChatMessages(projectId, "visual", (prev) => [
          ...prev,
          {
            role: "agent",
            content: imageStale.length > 0
              ? `✅ ${successText}。\n\n这些页面需要重新生成图片。请检查后再确认生图。`
              : `✅ ${successText}。现在可以继续打样或生成图片。`,
            agentRole: "visual",
          },
        ]);
      }
    } catch (err: any) {
      showToast("更新失败：" + (err.message || "未知错误"), "error");
      if (!options?.silent) {
        updateProjectChatMessages(projectId, "visual", (prev) => [
          ...prev,
          {
            role: "agent",
            content: "❌ 更新画面方案失败：" + (err.message || "未知错误") + "\n\n👉 请检查网络后重试，或告诉我具体需要调整的地方。",
            agentRole: "visual",
          },
        ]);
      }
      if (options?.throwOnError) throw err;
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
      const result = await startGeneration(projectId, pageNums);
      handleWorkflowRunStarted(projectId, result.run);
      const generationRunId = result?.run?.id ? String(result.run.id) : null;
      const finalWorkflow = await pollUntilStatusNotGenerating(projectId);
      const finalRun = generationRunId && finalWorkflow?.last_run?.id === generationRunId
        ? finalWorkflow.last_run
        : null;
      const generationOutcome = evaluateImageGenerationOutcome({
        prototype: false,
        projectStatus: finalWorkflow?.project_status || "",
        run: finalRun || (generationRunId ? { status: "stale", message: "没有找到本次生成任务的完成记录" } : null),
      });
      if (!generationOutcome.isSuccess) {
        throw new Error(generationOutcome.message);
      }
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
          content: "❌ 生成失败：" + (err.message || "未知错误") + "。请检查画面描述后重试，或告诉我具体哪一页有问题。",
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
        setDeckSelectedPages(new Set());
        setSelectedPages(new Set());
        setPrototypeSelectionTouched(false);
        setEditingSlide(null);
        setGalleryModal(null);
        setStyleProposalsInChat([]);
        setExpandedStyleProposalKey(null);
        setTemplateDrawerOpen(false);
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
        rollbackMsg += `\n\n**画面设计已重新打开。** 已保留内容和已确认风格，图片结果已回到待生成样张状态。\n\n👉 你可以在作品画布检查每页画面方案、选择样张范围，或告诉我需要重抽哪一页。`;
      } else if (targetStage === "prototype_ready") {
        rollbackMsg += `\n\n**已回到效果预览。** 这里会保留已有样张，方便你继续检查风格、构图和文字可读性。\n\n👉 如果效果不满意，请勾选页面后点击「重打样张」。`;
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

  const toggleDeckPageSelection = (pageNum: number) => {
    setDeckSelectedPages((prev) => {
      const next = new Set(prev);
      if (next.has(pageNum)) {
        next.delete(pageNum);
      } else {
        next.add(pageNum);
      }
      if (next.size > 0) setAgentScope("selected_slides");
      return next;
    });
  };

  const clearDeckSelection = () => {
    setDeckSelectedPages(new Set());
    if (!editingSlide) setAgentScope("deck");
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
    if (explicitPageNums.length > 0) {
      return getPrototypeTargetSlides(explicitPageNums);
    }
    if (currentStatus === "prototype_ready") {
      const visiblePageNums = visiblePrototypePageNumsRef.current;
      const sampledPageNums = slides.filter((slide) => slide.image_path).map((slide) => slide.page_num);
      const targetPageNums = visiblePageNums.length > 0
        ? visiblePageNums
        : prototypeSelectionTouched
        ? Array.from(selectedPages)
        : sampledPageNums.length > 0
        ? sampledPageNums
        : selectedPrototypePageNums;
      if (targetPageNums.length > 0) {
        const targetSet = new Set(targetPageNums);
        return slides.filter((slide) => targetSet.has(slide.page_num));
      }
    }
    return getPrototypeTargetSlides();
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
      handleWorkflowRunStarted(projectId, result?.run);
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

  const activateFinetuneForSlide = (slide: Slide) => {
    if (isBusy || chatLoading || slide.status !== "completed" || !slide.image_path) return false;
    abortActiveChat(true);
    currentAgentRoleRef.current = "finetune";
    setCurrentAgentRole("finetune");
    setFinetuneTargetSlideId(slide.id);
    ensureFinetuneGreetingForSlide(slide.id);
    loadSlideVersions(slide.id);
    return true;
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
    setAgentScope("current_slide");
  };

  const handleExitEdit = () => {
    setEditingSlide(null);
    setAgentScope(deckSelectedPages.size > 0 ? "selected_slides" : "deck");
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
    if (agentScope === "current_slide" && editingSlide?.reference_images) {
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
        targetAreaOverride: agentTargetAreaOverride,
        editingPageNum: editingSlide?.page_num,
        selectedPageNums: selectedPageNumsForAgent,
        projectStatus: requestProject.status,
        slideCount: slides.length,
        slides: slidesForAgentRequestContext(slides),
        contentPlanConfirmed: Boolean(requestProject.content_plan_confirmed),
        hasSelectedStyle: Boolean(requestProject.selected_style),
        hasPrompt: slides.some(slideHasPrompt),
        hasGeneratedImage: slides.some((slide) => Boolean(slide.image_path)),
      });
    if (!isRetry && requestContext.confidence === "needs_input") {
      showToast("先在画布中勾选页面，或改说具体页码。", "info");
      return;
    }
    if (!isRetry && requestContext.risk !== "safe") {
      const impactParts: string[] = [];
      if (requestContext.scope === "deck") {
        impactParts.push(`会影响整套 ${slides.length || 0} 页`);
      } else if (requestContext.scope === "selected_slides") {
        const affectedCount = requestContext.pageNums.length || selectedPageNumsForAgent.length;
        impactParts.push(affectedCount > 0 ? `会影响选中的 ${affectedCount} 页` : "会影响选中页");
      } else {
        const pageNum = requestContext.pageNums[0] || editingSlide?.page_num;
        impactParts.push(pageNum ? `会影响第 ${pageNum} 页` : "会影响当前页");
      }
      if (requestContext.risk === "cost") {
        impactParts.push("会产生生图成本");
      }
      if (requestContext.risk === "destructive") {
        impactParts.push("会覆盖现有画面方案");
      }
      const ok = await showConfirm(`${impactParts.join("，")}。继续吗？`);
      if (!ok) return;
    }
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
        const finetuneResult = await finetuneSlide(selectedProject.id, targetSlide.id, userMsg, finetuneAttachments.map((a) => a.id));
        const generationRunId = finetuneResult?.run?.id ? String(finetuneResult.run.id) : null;
        await loadSlides(selectedProject.id);
        const finalWorkflow = await pollUntilStatusNotGenerating(selectedProject.id);
        const finalRun = generationRunId && finalWorkflow?.last_run?.id === generationRunId
          ? finalWorkflow.last_run
          : null;
        const generationOutcome = evaluateImageGenerationOutcome({
          prototype: false,
          projectStatus: finalWorkflow?.project_status || "",
          run: finalRun || (generationRunId ? { status: "stale", message: "没有找到本次生成任务的完成记录" } : null),
        });
        if (!generationOutcome.isSuccess) {
          throw new Error(generationOutcome.message);
        }
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
          target_area: requestContext.targetArea,
          area_label: requestContext.areaLabel,
          confidence: requestContext.confidence,
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
          target_area: requestContext.targetArea,
          area_label: requestContext.areaLabel,
          confidence: requestContext.confidence,
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
              content: buildChangeReceipt({
                status: "queued",
                subject: `已标记${formatPageNumsForReceipt(regenPageNums)}需要重新生成图片`,
                change: "图片会按当前画面方案重新生成",
                next: "这会产生生图成本，请进入对应页面检查后点击「确认生成图片」。",
              }),
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
                ? buildChangeReceipt({
                    status: "queued",
                    subject: `已找到 ${failed.length} 个失败页面`,
                    change: "这些页面可以重新生成图片",
                    next: "重试会产生生图成本，请点击「一键重试失败页」或进入单页确认。",
                  })
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
            await handleUpdateStaleSlides(targetIds, { local: true, silent: true, staleOverride: { content: true }, throwOnError: true });
            appendRequestMessage({
              role: "agent",
              content: buildChangeReceipt({
                status: "applied",
                subject: `已为${formatPageNumsForReceipt(pageNums)}再生成一版画面方案`,
                change: summarizeVisualChange(userMsg, null, result.response),
                next: "请检查后再决定是否生成图片。",
              }),
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
              await handleUpdateStaleSlides([targetSlide.id], {
                local: true,
                silent: true,
                staleOverride: { visual: true },
                throwOnError: true,
              });
              addSystemLog(`已应用第 ${pageNum} 页视觉描述修改并同步提示词`);
              appendRequestMessage({
                role: "agent",
                content: buildChangeReceipt({
                  status: "applied",
                  subject: `已更新第 ${pageNum} 页画面描述，并同步了生图提示词`,
                  change: summarizeVisualChange(userMsg, result.updated_visual, result.response),
                  next: "图片未自动重生成，请检查后再确认生图。",
                }),
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
            await handleUpdateStaleSlides(updatedSlideIds, {
              local: true,
              silent: true,
              staleOverride: { visual: true },
              throwOnError: true,
            });
            addSystemLog(`已应用 ${updatedSlideIds.length} 页视觉描述修改并同步提示词`);
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
          appendRequestMessage({
            role: "agent",
            content: buildChangeReceipt({
              status: "applied",
              subject: `已更新${formatPageNumsForReceipt(updatedPageNums)}的视觉描述`,
              change: summarizeVisualChange(userMsg, result.updated_slides_visual?.[0], result.response),
              skipped: skipped.length > 0 ? `第 ${skipped.join(", ")} 页不存在` : "",
              next: "图片未自动重生成，请检查后再确认生图。",
            }),
            agentRole: "visual",
          });
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
              ? buildChangeReceipt({
                  status: "queued",
                  subject: `可以生成${formatPageNumsForReceipt(pageNums)}图片`,
                  change: "已进入生图确认状态",
                  next: "这会产生生图成本，请在单页中点击「确认生成图片」。",
                })
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
            // 把内容总监对话历史 + 当前消息打包成 chat_context，确保后端 LLM 看到用户最新反馈
            const contentChatContext = buildVisualStyleGenerationContext(
              history,
              userMsg,
              buildCrossStageContext("content")
            );
            await dispatchGateAction(
              "generate_content_plan",
              {
                topic: result.topic,
                page_count: result.page_count,
                attachment_ids: attachmentIdsForRequest,
                chat_context: contentChatContext,
              },
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
                ? buildChangeReceipt({
                    status: "applied",
                    subject: "已生成调整后视觉方向，请查看本条消息下方卡片",
                    change: summarizeVisualChange(userMsg, proposal, result.response),
                    next: "满意请点「选择此方案」；选择后我会保存新风格并重新生成画面描述。",
                  })
                : buildChangeReceipt({
                    status: "applied",
                    subject: "视觉方向已生成，请查看本条消息下方卡片",
                    next: "满意请点击「选择此方案」；想调整就直接告诉我。",
                  }),
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
            handleWorkflowRunStarted(requestProjectId, styleResult.run);
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
              updateProjectChatMessages(requestProjectId, "visual", (prev) =>
                upsertAgentStatusMessage(prev.filter((m) => m.id !== styleLoadingId), {
                  role: "agent",
                  content: isAdjust
                    ? buildChangeReceipt({
                        status: "applied",
                        subject: "已生成调整后视觉方向，请查看下方新卡片",
                        change: summarizeVisualChange(userMsg, proposals[0], result.response),
                        next: "满意请点「选择此方案」；不满意继续告诉我哪里要再改。",
                      })
                    : buildChangeReceipt({
                        status: "applied",
                        subject: "视觉方向已生成，请查看下方卡片",
                        next: "从三套方案中选择最喜欢的一套，或直接告诉我你的偏好。",
                      }),
                  agentRole: "visual",
                  hasStyleProposal: true,
                  styleProposals: proposals,
                  gate: gateContext.gate,
                  gateRevision: gateContext.gateRevision,
                  runId: styleRunId || undefined,
                  statusKey: "style-ready",
                })
              );
            } else {
              updateProjectChatMessages(requestProjectId, "visual", (prev) =>
                upsertAgentStatusMessage(prev.filter((m) => m.id !== styleLoadingId), {
                  role: "agent",
                  content: buildChangeReceipt({
                    status: "applied",
                    subject: "视觉方向已生成，请查看作品画布",
                    next: "从三套方案中选择最喜欢的一套，或直接告诉我你的偏好。",
                  }),
                  agentRole: "visual",
                  runId: styleRunId || undefined,
                  statusKey: "style-ready",
                })
              );
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
            {
              role: "agent",
              content: buildChangeReceipt({
                status: "applied",
                subject: `已更新第 ${pageNum} 页内容`,
                change: summarizeContentChange(result.updated_content, userMsg),
                next: "相关画面方案需要重新检查；切到「视觉总监」更新画面后再生成图片。",
              }),
            },
          ]);
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
          const receipt =
            updatedPageNums.length > 0
              ? buildChangeReceipt({
                  status: "applied",
                  subject: `已更新${formatPageNumsForReceipt(updatedPageNums)}内容`,
                  change: summarizeContentChange(result.updated_slides?.[0], userMsg),
                  skipped: skipped.length > 0 ? `第 ${skipped.join(", ")} 页不存在` : "",
                  next: "相关画面方案需要重新检查；切到「视觉总监」更新画面后再生成图片。",
                })
              : buildChangeReceipt({
                  status: "no_change",
                  subject: "没有找到可更新的页面，内容未变化",
                  skipped: skipped.length > 0 ? `第 ${skipped.join(", ")} 页不存在` : "",
                });
          setActiveChatMessages((prev) => [...prev, { role: "agent", content: receipt }]);
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
            {
              role: "agent",
              content: buildChangeReceipt({
                status: "applied",
                subject: `已在第 ${pageNum} 页前插入新页`,
                change: summarizeInsertedSlide(result.new_slide),
                next: "请检查新增页内容；需要出图前再生成画面方案。",
              }),
            },
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
            {
              role: "agent",
              content: buildChangeReceipt({
                status: "applied",
                subject: `已在第 ${pageNum} 页后插入新页`,
                change: summarizeInsertedSlide(result.new_slide),
                next: "请检查新增页内容；需要出图前再生成画面方案。",
              }),
            },
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
            chatContext: payload?.chat_context,
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
              handleWorkflowRunStarted(currentProject.id, styleResult.run);
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
                updateProjectChatMessages(currentProject.id, "visual", (prev) =>
                  upsertAgentStatusMessage(prev.filter((message) => message.id !== styleLoadingId), {
                    role: "agent",
                    content: buildChangeReceipt({
                      status: "applied",
                      subject: hasExistingStyleProposals ? "视觉方向已重新生成，请查看下方卡片" : "视觉方向已生成，请查看下方卡片",
                      next: "选择一套方向后，我会保存风格并生成画面描述。",
                    }),
                    agentRole: "visual",
                    hasStyleProposal: true,
                    styleProposals: proposals,
                    gate: gateContext.gate,
                    gateRevision: gateContext.gateRevision,
                    runId: styleRunId || undefined,
                    statusKey: "style-ready",
                  })
                );
              } else {
                updateProjectChatMessages(currentProject.id, "visual", (prev) =>
                  upsertAgentStatusMessage(prev.filter((message) => message.id !== styleLoadingId), {
                    role: "agent",
                    content: buildChangeReceipt({
                      status: "applied",
                      subject: "视觉方向已生成，请在作品画布中查看",
                      next: "选择一套方向后，我会保存风格并生成画面描述。",
                    }),
                    agentRole: "visual",
                    runId: styleRunId || undefined,
                    statusKey: "style-ready",
                  })
                );
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
    agenda: "目录",
    content: "内容",
    hero: "金句",
    quote: "引言",
    data: "数据",
    ending: "封底",
    section: "章节",
  };

  const typeColor: Record<string, string> = {
    cover: "bg-purple-100 text-purple-700",
    toc: "bg-blue-100 text-blue-700",
    agenda: "bg-blue-100 text-blue-700",
    content: "bg-gray-100 text-gray-700",
    hero: "bg-yellow-100 text-yellow-700",
    quote: "bg-yellow-100 text-yellow-700",
    data: "bg-green-100 text-green-700",
    ending: "bg-gray-100 text-gray-700",
    section: "bg-pink-100 text-pink-700",
  };
  const projectLogo = referenceImages.find(isConfirmedLogoRef);
  const projectVisualAssetById = useMemo(() => {
    const map = new Map<string, any>();
    referenceImages.forEach((ref: any) => {
      if (ref?.role === "visual_asset" && ref?.id) {
        map.set(String(ref.id), ref);
      }
    });
    return map;
  }, [referenceImages]);
  const styleDockProposals: StyleProposal[] =
    styleProposalsInChat.length > 0
      ? styleProposalsInChat
      : (selectedProject?.style_proposal?.proposals || []);
  const selectedStylePreview = selectedProject?.selected_style
    ? buildSelectedStylePreview(selectedProject.selected_style)
    : null;
  const selectedStylePalette = Array.isArray(selectedProject?.selected_style?.palette)
    ? selectedProject.selected_style.palette
    : [];
  const selectedStyleSummary = selectedProject?.selected_style
    ? selectedStylePreview?.summary ||
      stripHexCodes(
        selectedProject.selected_style.description ||
        visualStrategyText(selectedProject.selected_style) ||
        selectedProject.selected_style.mood ||
        ""
      )
    : "";
  const generatedSlideCount = slides.filter((slide) => Boolean(slide.image_path)).length;
  const selectedTemplateRecommendations =
    selectedProject?.selected_template_recommendations && typeof selectedProject.selected_template_recommendations === "object"
      ? selectedProject.selected_template_recommendations
      : null;
  const selectedTemplateRecommendationCount = selectedTemplateRecommendations
    ? Object.values(selectedTemplateRecommendations).filter(Boolean).length
    : 0;
  const hasTemplateSource = templatePages.length > 0 || selectedTemplateRecommendationCount > 0;
  const selectedTemplateSourceKind = selectedTemplateRecommendations
    ? (Object.values(selectedTemplateRecommendations).find((value: any) => value?.source_kind) as any)?.source_kind
    : null;
  const templateSourceKind =
    templatePages.find((page: any) => page?.source_kind)?.source_kind ||
    selectedTemplateSourceKind ||
    (referenceImages.some((ref: any) => ref.role === "template" && ref?.asset_analysis?.document_kind === "finished_ppt")
      ? "finished_ppt"
      : "template");
  const templateSourceCopy =
    templateSourceKind === "finished_ppt"
      ? "学习版式、配色和字体节奏，不把旧正文带进新内容。"
      : "学习模板的版式、配色和字体节奏。";
  const templateSourceMeta =
    templatePages.length > 0
      ? `${templatePages.length} 页已读取`
      : selectedTemplateRecommendationCount > 0
      ? `${selectedTemplateRecommendationCount} 类页面已匹配`
      : "";
  const showTemplateConfirmControls =
    templatePages.length > 0 &&
    (templateConfirmVisible ||
      (selectedTemplateRecommendationCount === 0 && templateConfirmDismissedProjectId !== selectedProject?.id));

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
  const isEditablePptxRunActive = Boolean(hasActiveRun && activeRun?.kind === "editable_pptx");
  const editableExportDisabled = Boolean(
    !selectedProject ||
      chatLoading ||
      isBusy ||
      hasActiveRun ||
      operatingProjectId === selectedProject.id ||
      selectedProject.status !== "completed" ||
      !currentProjectStatus?.has_pptx
  );
  const editableExportLabel = isEditablePptxRunActive ? "准备中..." : "下载可编辑版 PPTX";
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
  const generatedPrototypePageNums = currentStatus === "prototype_ready"
    ? normalizePageNums(slides.filter((slide) => slide.image_path).map((slide) => slide.page_num))
    : [];
  const isPrototypeRunActive = Boolean(hasActiveRun && activeRun?.kind === "prototype_generation");
  const visiblePrototypePageNums =
    isPrototypeRunActive && activeRunTargetPageNums.length > 0
      ? activeRunTargetPageNums
      : currentStatus === "prototype_ready" && !prototypeSelectionTouched && selectedPages.size === 0 && generatedPrototypePageNums.length > 0
      ? generatedPrototypePageNums
      : selectedPrototypePageNums;
  visiblePrototypePageNumsRef.current = visiblePrototypePageNums;
  const visiblePrototypePageSet = new Set(visiblePrototypePageNums);
  const canEditPrototypeSelection =
    (currentStatus === "prompt_ready" || currentStatus === "prototype_ready" || currentStatus === "failed") && !isPrototypeRunActive && !isBusy && !chatLoading;
  const shouldShowPrototypeSelection =
    slides.length > 0 && (currentStatus === "prompt_ready" || currentStatus === "prototype_ready" || currentStatus === "failed" || isPrototypeRunActive);
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
  const shouldShowAgentRunControl = Boolean(
    selectedProject &&
    hasActiveRun &&
    currentAgentRole !== "finetune"
  );
  const handleConfirmTemplateRecommendations = async () => {
    if (!selectedProject) return;
    const fallbackSelection = buildDefaultTemplateSelection(templatePages);
    const recommendations = TEMPLATE_CONFIRM_TYPES.reduce<Record<string, any>>((acc, item) => {
      const pageNum = templatePageSelection[item.key] || fallbackSelection[item.key];
      acc[item.key] = pageNum ? { page_num: pageNum, application_strength: templateApplicationStrength } : null;
      return acc;
    }, {});
    setTemplateConfirmSaving(true);
    try {
      const updated = await updateTemplateRecommendations(selectedProject.id, recommendations);
      const normalized = clearProjectNotification(updated);
      setSelectedProject(normalized);
      setProjects((prev) => prev.map((item) => (item.id === normalized.id ? normalized : item)));
      setTemplateConfirmVisible(false);
      setTemplateDrawerOpen(false);
      setTemplateConfirmDismissedProjectId(normalized.id);
      showToast("模板页推荐已确认", "success");
      addSystemLog("用户确认了版式模板页映射");
    } catch (err: any) {
      showToast("保存模板推荐失败：" + (err.message || "未知错误"), "error");
    } finally {
      setTemplateConfirmSaving(false);
    }
  };
    const agentComposerValue = !isBriefStudioActive && chatInput.includes("[[PPTGOD_ATTACHMENT:") ? "" : chatInput;
    const selectedPageNumsForAgent = useMemo(
      () => shouldShowPrototypeSelection ? [] : Array.from(deckSelectedPages).sort((a, b) => a - b),
      [deckSelectedPages, shouldShowPrototypeSelection]
    );
    const activeAgentScope: AgentRequestScope =
      agentScope === "current_slide" && editingSlide
        ? "current_slide"
        : agentScope === "selected_slides" && selectedPageNumsForAgent.length > 0
        ? "selected_slides"
        : "deck";
    const composerRequestContext = useMemo(
      () =>
        inferAgentRequestContext({
          message: agentComposerValue,
          activeAgentRole: currentAgentRole,
          activeScope: activeAgentScope,
          targetAreaOverride: agentTargetAreaOverride,
          editingPageNum: editingSlide?.page_num,
          selectedPageNums: selectedPageNumsForAgent,
          projectStatus: currentStatus,
          slideCount: slides.length,
          slides: slidesForAgentRequestContext(slides),
          contentPlanConfirmed,
          hasSelectedStyle: Boolean(selectedProject?.selected_style),
          hasPrompt: slides.some(slideHasPrompt),
          hasGeneratedImage: slides.some((slide) => Boolean(slide.image_path)),
        }),
      [
        activeAgentScope,
        agentComposerValue,
        agentTargetAreaOverride,
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
    if (!selectedProject?.id) return null;
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
    (!shouldShowPrototypeSelection && deckSelectedPages.size === 1 ? slides.find((slide) => deckSelectedPages.has(slide.page_num)) || null : null);
  const prototypePromptTargets = getPrototypeTargetSlides();
  const resamplePrototypeTargets =
    currentStatus === "prototype_ready" && visiblePrototypePageNums.length > 0
      ? slides.filter((slide) => visiblePrototypePageSet.has(slide.page_num))
      : getPrototypeResampleTargetSlides();
  const resamplePrototypePageNums = resamplePrototypeTargets.map((slide) => slide.page_num);
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
  const activeProgressDisclosure = buildWorkflowProgressDisclosure(currentProjectStatus || { active_run: activeRun });
  const activeProgressStatusText = activeProgressDisclosure?.summary || workflowProgressText(currentProjectStatus || { active_run: activeRun });
  const activeProgressLabel =
    activeProgressDisclosure?.headline ||
    (activeProgress.status === "queued"
      ? "等待开始"
      : currentProjectStatus?.progress?.label ||
        (activeRun?.kind === "content_plan"
          ? "内容规划生成进度"
          : activeRun?.kind === "style_proposal"
          ? "风格提案生成进度"
          : activeRun?.kind === "visual_prompts"
          ? "画面方案进度"
          : activeRun?.kind === "prototype_generation"
          ? "样张生成进度"
          : activeRun?.kind === "editable_pptx"
          ? "可编辑版生成进度"
          : "批量生成进度"));
  const deckSelectedPageNums = Array.from(deckSelectedPages).sort((a, b) => a - b);
  const agentFinetuneHeadline =
    editingSlide ? `将微调第 ${editingSlide.page_num} 页画面` : "先选择一页，再告诉我怎么改";
  const agentSecondaryHint =
    composerRequestContext.confidence === "needs_input"
      ? "先在画布中勾选页面，或改说具体页码。"
      : composerRequestContext.risk === "cost"
      ? "发送前会确认生图成本。"
      : composerRequestContext.risk === "destructive"
      ? "发送前会确认影响范围。"
      : "";
  const agentScopeButtonLabel =
    composerRequestContext.scope === "current_slide"
      ? composerRequestContext.pageNums[0] && (!editingSlide || composerRequestContext.pageNums[0] !== editingSlide.page_num)
        ? `第 ${composerRequestContext.pageNums[0]} 页`
        : "当前页"
      : composerRequestContext.scope === "selected_slides"
      ? "选中页"
      : "整套 PPT";
  const agentAreaButtonLabel =
    composerRequestContext.targetArea === "whole" ? "全页内容" : composerRequestContext.areaLabel;
  const agentAreaOptions: { value: AgentTargetArea; label: string; hint: string }[] = [
    { value: "whole", label: "全页内容", hint: "不限定区域" },
    { value: "title", label: "标题", hint: "标题、副标题" },
    { value: "body", label: "正文", hint: "正文、要点" },
    { value: "visual", label: "画面", hint: "背景、风格、版式" },
    { value: "materials", label: "素材", hint: "Logo、产品图、参考图" },
    { value: "notes", label: "备注", hint: "讲稿、备注" },
  ];
  const agentComposerPlaceholder =
    currentAgentRole === "finetune" && !finetuneTargetSlideId
      ? "请先在左侧点击一页..."
      : currentStatus === "draft"
      ? "输入 PPT 主题或粘贴文档内容..."
      : currentAgentRole === "finetune"
      ? "例如：保留文字，把画面换成更高级的办公室场景"
      : currentAgentRole === "visual"
      ? "例如：把选中页改得更商务，背景更克制"
      : "例如：把第 3 页标题改短，正文更像汇报口吻";
  const agentMaterialCount =
    pendingAttachments.length +
    pendingChatAttachments.length +
    (currentAgentRole === "finetune" && finetuneTargetSlideId
      ? (pendingFinetuneAttachmentsMap[finetuneTargetSlideId] || []).length
      : 0);
  const failedSlidePageNums = slides.filter((s) => s.status === "failed").map((s) => s.page_num);
  const agentContractRows = useMemo(
    () => buildAgentContractRows(selectedProject?.intent_contract),
    [selectedProject?.intent_contract]
  );
  const agentDeliveryCheckItems = useMemo(() => {
    const items: Array<{ tone: "info" | "warning" | "danger"; text: string }> = [];
    const quality_report = currentProjectStatus?.quality_report;
    if (quality_report?.message) {
      items.push({ tone: "info", text: String(quality_report.message).slice(0, 160) });
    } else if (quality_report?.summary) {
      items.push({ tone: "info", text: String(quality_report.summary).slice(0, 160) });
    }
    const issueCount = Array.isArray(quality_report?.issues) ? quality_report.issues.length : 0;
    if (issueCount > 0) {
      items.push({ tone: "warning", text: `${issueCount} 个页面检查项需要复核。` });
    }
    if (hasContentOrVisualStale) {
      items.push({ tone: "warning", text: "有内容或画面修改还没应用到画面方案。" });
    }
    if (imageStaleSlides.length > 0) {
      items.push({ tone: "warning", text: `${imageStaleSlides.length} 页图片需要重新生成。` });
    }
    if (failedSlidePageNums.length > 0) {
      items.push({ tone: "danger", text: `第 ${failedSlidePageNums.join("、")} 页生成失败，可重试。` });
    }
    return items.slice(0, 3);
  }, [
    currentProjectStatus?.quality_report,
    failedSlidePageNums,
    hasContentOrVisualStale,
    imageStaleSlides.length,
  ]);
  const incompleteSlidePageNums = slides
    .filter((s) => s.status !== "completed" && s.status !== "failed")
    .map((s) => s.page_num)
    .filter((n) => Number.isFinite(n));
  const latestVisualPromptProblemRun =
    currentProjectStatus?.last_run?.kind === "visual_prompts" &&
    ["failed", "stale", "cancelled"].includes(String(currentProjectStatus.last_run.status || ""))
      ? currentProjectStatus.last_run
      : null;
  const statusCard: StatusCardData | null = selectedProject
    ? getStatusCard({
        workflowState,
        staleActionPlan,
        failedPageNums: failedSlidePageNums,
        incompletePageNums: incompleteSlidePageNums,
        visiblePrototypePageNums,
        resamplePageNums: resamplePrototypePageNums,
        prototypePromptTargetCount: prototypePromptTargets.length,
        completedSlideCount: generatedSlideCount,
        totalSlideCount: slides.length,
        progressDisclosure: activeProgressDisclosure,
        canStartPrototypeGeneration,
        canStartFullGeneration,
        latestProblemRun: latestVisualPromptProblemRun,
      })
    : null;
  const statusCardActionDispatch = (key: StatusActionKey) => {
    switch (key) {
      case "stop":
        void handleStopGeneration();
        return;
      case "retry-failed":
        void dispatchGateAction("retry_failed");
        return;
      case "continue-generation":
        void dispatchGateAction("start_generation", { page_nums: incompleteSlidePageNums });
        return;
      case "update-stale-visual":
        void handleUpdateStaleSlides();
        return;
      case "regenerate-stale-images":
        void handleGenerateStaleImages();
        return;
      case "confirm-prototype":
        void dispatchGateAction("confirm_prototype");
        return;
      case "resample-prototype":
        void dispatchGateAction("resample_prototype");
        return;
      case "start-prototype":
        void dispatchGateAction("start_prototype");
        return;
      case "generate-style":
        void dispatchGateAction("generate_style_proposals");
        return;
      case "generate-visual-prompts":
        void dispatchGateAction("generate_visual_prompts");
        return;
      case "confirm-content":
        void handleConfirmContentPlan();
        return;
      case "switch-to-visual":
        void dispatchGateAction("switch_to_visual");
        return;
      case "start-generation":
        void dispatchGateAction("start_generation");
        return;
      case "download":
        void dispatchGateAction("download");
        return;
      default: {
        const exhaustive: never = key;
        console.warn("Unhandled status card action", exhaustive);
      }
    }
  };
  const latestPrototypeProblemRun =
    currentProjectStatus?.last_run?.kind === "prototype_generation" &&
    ["failed", "stale", "cancelled"].includes(String(currentProjectStatus.last_run.status || ""))
      ? currentProjectStatus.last_run
      : null;
  const latestPrototypeProblemOutcome = latestPrototypeProblemRun
    ? evaluateImageGenerationOutcome({
        prototype: true,
        projectStatus: currentStatus,
        run: latestPrototypeProblemRun,
      })
    : null;
  const currentStageNudge =
    latestPrototypeProblemOutcome && !latestPrototypeProblemOutcome.isSuccess
      ? {
          title: "上一轮打样未完成",
          body: latestPrototypeProblemOutcome.message,
          primary: {
            label: "重打样张",
            onClick: () => {
              void dispatchGateAction(currentStatus === "prototype_ready" ? "resample_prototype" : "start_prototype");
            },
          },
        }
      : null;
  const renderProgressOverview = (variant: "drawer" | "empty" | "agent" = "drawer") => {
    if (!activeProgressDisclosure) return null;
    const overviewDisplay = getWorkflowProgressOverviewDisplay(activeProgressDisclosure, variant);
    return (
      <div className={`pg-progress-overview pg-progress-overview--${variant}`} aria-live="polite">
        {overviewDisplay.showHeaderCopy && (
          <div className="pg-progress-overview-head">
            <div>
              <b>{activeProgressDisclosure.headline}</b>
              <span>{activeProgressDisclosure.detail}</span>
            </div>
            {activeProgressDisclosure.total > 0 && (
              <em>{Math.round(activeProgressDisclosure.percent)}%</em>
            )}
          </div>
        )}
        <div
          className="pg-progress-overview-bar"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(activeProgressDisclosure.percent)}
        >
          <i style={{ width: `${activeProgressDisclosure.percent}%` }} />
        </div>
        {overviewDisplay.metrics.length > 0 && (
          <div className="pg-progress-metrics">
            {overviewDisplay.metrics.map((item) => (
              <span key={`${item.label}-${item.value}`}>
                <b>{item.label}</b>
                <em>{item.value}</em>
              </span>
            ))}
          </div>
        )}
        {overviewDisplay.showSteps && (
          <div className="pg-progress-steps" aria-label="处理路径">
            {activeProgressDisclosure.steps.map((step) => (
              <span key={step.label} className={`is-${step.status}`}>
                <i />
                <em>{step.label}</em>
              </span>
            ))}
          </div>
        )}
      </div>
    );
  };
  const emptyProgressDisplay = activeProgressDisclosure
    ? getWorkflowProgressOverviewDisplay(activeProgressDisclosure, "empty")
    : null;
  const agentProgressDisplay = activeProgressDisclosure
    ? getWorkflowProgressOverviewDisplay(activeProgressDisclosure, "agent")
    : null;
  const activeProgressTargetPageSet = new Set(activeProgressDisclosure?.targetPageNums || []);
  const activeProgressActivePageSet = new Set(activeProgressDisclosure?.activePageNums || []);
  const activeProgressIsImageRun = isImageRunKind(activeRun?.kind || currentProjectStatus?.progress?.kind);
  const shouldShowRunProgressEmptyState = Boolean(
    selectedProject &&
    slides.length === 0 &&
    !slidesAreLoading &&
    (hasActiveRun || currentStatus === "prototype" || currentStatus === "generating")
  );
  const materialLogoCount = referenceImages.filter(isConfirmedLogoRef).length;
  const materialAssetCount = referenceImages.filter((r: any) => r.role === "visual_asset").length;
  const materialStyleRefCount = referenceImages.filter((r: any) => r.role === "style_ref").length;
  const materialTemplateCount = referenceImages.filter((r: any) => r.role === "template").length;
  const materialSummary = [
    materialLogoCount > 0 ? `${materialLogoCount} 个 Logo` : "",
    materialAssetCount > 0 ? `${materialAssetCount} 个素材` : "",
    materialStyleRefCount > 0 ? `${materialStyleRefCount} 张风格` : "",
    materialTemplateCount > 0 ? "模板" : "",
  ].filter(Boolean).join(" · ") || "尚未添加";

  const renderMaterialLibraryPanel = () => {
    if (!selectedProject) return null;
    return (
      <aside className="pg-material-side-panel" aria-label="项目素材库">
        <VisualAssetsPanel
          referenceImages={referenceImages}
          activeSlide={activeAssetSlide}
          templateRecommendations={selectedProject.selected_template_recommendations}
          templatePages={templatePages}
          apiBase={API_BASE}
          showInVisualStage={true}
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
      </aside>
    );
  };

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
        <header className="pg-topbar pg-project-header">
          <div className="pg-project-title" title={selectedProject?.title}>
            {selectedProject ? selectedProject.title : "预览区"}
          </div>
          <div className="pg-project-exports">
            {selectedProject && slides.length > 0 && (
              <a href={getContentPlanMarkdownUrl(selectedProject.id)} className="pg-project-export-link">
                下载规划 MD
              </a>
            )}
            {selectedProject && generatedSlideCount > 0 ? (
              <a href={getDownloadUrl(selectedProject.id)} className="pg-project-export-link">
                下载图片版 PPTX
              </a>
            ) : selectedProject ? (
              <span className="pg-project-export-link is-disabled" aria-disabled="true">
                下载图片版 PPTX
              </span>
            ) : null}
            {selectedProject ? (
              <div className="pg-editable-export">
                {editableExportDisabled ? (
                  <button
                    type="button"
                    className="pg-project-export-link is-disabled"
                    disabled
                    aria-disabled="true"
                    aria-label={isEditablePptxRunActive ? "正在生成可编辑版 PPTX" : "请先完成全量 PPT 生成"}
                  >
                    {editableExportLabel}
                  </button>
                ) : (
                  <details className="pg-editable-export-menu">
                    <summary
                      className="pg-project-export-link pg-editable-export-trigger"
                      aria-label="下载可编辑版 PPTX，选择解析强度"
                    >
                      <span>{editableExportLabel}</span>
                      <span className="pg-editable-export-caret" aria-hidden="true">⌄</span>
                    </summary>
                    <div className="pg-editable-export-options" role="menu" aria-label="选择解析强度">
                      <div className="pg-editable-export-help">
                        选择解析强度：越高拆得越细，视觉风险越高。
                      </div>
                      {EDITABLE_PPTX_MODE_OPTIONS.map((option) => (
                        <button
                          key={option.value}
                          type="button"
                          role="menuitem"
                          className="pg-editable-export-option"
                          onClick={(event) => {
                            event.currentTarget.closest("details")?.removeAttribute("open");
                            handleEditablePptxExport(option.value);
                          }}
                        >
                          <span className="pg-editable-export-option-title">{option.label}</span>
                          <span className="pg-editable-export-option-hint">{option.hint}</span>
                        </button>
                      ))}
                    </div>
                  </details>
                )}
              </div>
            ) : null}
          </div>
        </header>

        {/* 项目进程管理 */}
        {selectedProject && (
          <div className="pg-workflow">
            <div className="pg-workflow-steps">
              {steps.map((step, idx) => {
                const status = stepStatus(idx);
                const canRollback = status === "done";
                const isCurrentLoading = status === "current" && isLoadingStatus;
                return (
                  <div key={step.key} className="pg-workflow-step-wrap">
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
                      <div className={`pg-workflow-connector ${idx < displayStepIndex ? "is-done" : ""}`} />
                    )}
                  </div>
                );
              })}
            </div>
            {statusCard ? (
              <StatusCard
                card={statusCard}
                onAction={statusCardActionDispatch}
              />
            ) : (
              <div className="pg-workflow-status" role="status" aria-live="polite">
                <span className="pg-progress-dot" />
                <div>
                  <b>{statusLabel[currentStatus] || workflowState.statusLabel}</b>
                </div>
              </div>
            )}
            {!shouldShowPrototypeSelection && deckSelectedPageNums.length > 0 && (
              <div className="pg-workflow-actions">
                <button
                  type="button"
                  onClick={clearDeckSelection}
                  className="pg-selection-pill"
                  title="这些页面会作为右侧 Agent 指令的作用范围；点击清除"
                >
                  右侧指令：{formatPageScope(deckSelectedPageNums)} · 清除
                </button>
              </div>
            )}
          </div>
        )}

        {/* 项目素材管理：中心工作台入口，详情进入停靠面板，避免遮挡编辑区 */}
        {!isBriefStudioActive && selectedProject ? (
          <>
            <div className="pg-workbench-modulebar">
              <button
                type="button"
                className="pg-assets-toggle pg-workbench-module-button"
                onClick={() => setAssetsBarExpanded((open) => !open)}
                aria-pressed={assetsBarExpanded}
              >
                <span className="pg-modulebar-label">素材库</span>
                <span className={`pg-modulebar-summary ${referenceImages.length === 0 ? "is-muted" : ""}`}>
                  {materialSummary}
                </span>
                <span className="ml-auto shrink-0 text-xs text-slate-400">
                  {assetsBarExpanded ? "收起" : "展开"}
                </span>
              </button>
            </div>
          </>
        ) : null}
        {assetsBarExpanded && !isBriefStudioActive && selectedProject && renderMaterialLibraryPanel()}
        {/* 视觉方案：紧凑条，可展开 */}
        {selectedProject?.selected_style && (
          currentStatus === "visual_ready" ||
          currentStatus === "prompt_ready" ||
          currentStatus === "generating" ||
          currentStatus === "prototype_ready" ||
          currentStatus === "completed"
        ) && (
          <div className="pg-style-bar">
            <button
              type="button"
              className="pg-style-bar-toggle pg-workbench-module-button"
              onClick={() => setStyleBarExpanded((v) => !v)}
              aria-expanded={styleBarExpanded}
            >
              <span className="pg-modulebar-label">视觉方案</span>
              {selectedStylePalette.length > 0 && (
                <span className="pg-style-bar-swatches">
                  {selectedStylePalette.slice(0, 5).map((c: any, i: number) => {
                    const color = typeof c === "string" ? c : c.hex;
                    return <i key={i} style={{ backgroundColor: color }} title={typeof c === "string" ? c : c.name} />;
                  })}
                </span>
              )}
              <span className="pg-modulebar-summary pg-style-bar-description">
                <b className="pg-style-bar-name">{selectedProject.selected_style.name}</b>
                {selectedStyleSummary ? ` · ${selectedStyleSummary}` : ""}
              </span>
              <span className="pg-style-bar-meta">{styleBarExpanded ? "收起" : "展开"}</span>
            </button>
            {styleBarExpanded && selectedStylePreview && (
              <div className="pg-style-preview-band">
                <p className="pg-style-preview-summary">{selectedStylePreview.summary}</p>
                <div className="pg-style-page-previews" aria-label="视觉方案页面类型预览">
                  {selectedStylePreview.pages.map((page) => (
                    <div
                      key={page.key}
                      className={`pg-style-page-mini is-${page.tone} is-${page.intensity}`}
                      style={{
                        "--style-page-bg": page.background,
                        "--style-page-accent": page.accent,
                        "--style-page-secondary": page.secondary,
                        "--style-page-highlight": page.highlight,
                        "--style-page-brand": page.brand,
                        "--style-page-text": page.text,
                        "--style-page-surface": page.surface,
                        "--style-page-chart-1": page.chartColors[0],
                        "--style-page-chart-2": page.chartColors[1],
                        "--style-page-chart-3": page.chartColors[2],
                        "--style-page-chart-4": page.chartColors[3],
                      } as CSSProperties}
                    >
                      <span className="pg-style-page-mini-label">{page.label}</span>
                      <i className="pg-style-page-mini-brand" />
                      <i className="pg-style-page-mini-glow" />
                      <i className="pg-style-page-mini-title" />
                      <i className="pg-style-page-mini-line line-1" />
                      <i className="pg-style-page-mini-line line-2" />
                      {page.key === "data" && (
                        <span className="pg-style-page-mini-chart" aria-hidden="true">
                          <i style={{ height: "42%" }} />
                          <i style={{ height: "76%" }} />
                          <i style={{ height: "55%" }} />
                          <i style={{ height: "90%" }} />
                        </span>
                      )}
                    </div>
                  ))}
                </div>
                <div className="pg-style-preview-notes">
                  <div>
                    <b>视觉节奏</b>
                    <p>{selectedStylePreview.rhythmText}</p>
                  </div>
                  <div>
                    <b>字体体系</b>
                    <p>{selectedStylePreview.fontText}</p>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        <div className="pg-workbench-body">
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
                {emptyProgressDisplay?.showStandaloneTitle && (
                  <div className="pg-flow-title">{activeProgressLabel}</div>
                )}
                {renderProgressOverview("empty")}
                {emptyProgressDisplay?.showFooterSummary && (
                  <div className="pg-flow-copy mt-3">
                    {activeProgressDisclosure?.summary ||
                      (activeProgress.total > 0
                        ? `${activeProgress.current} / ${activeProgress.total} ${activeProgress.unit}完成`
                        : "正在同步最新进度，生成结果会直接出现在这里。")}
                    {activeProgress.failed > 0 ? `，${activeProgress.failed} ${activeProgress.unit}失败` : ""}
                  </div>
                )}
                {rightCollapsed && (
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
                    查看处理记录
                  </button>
                )}
              </div>
            </div>
          ) : slides.length === 0 ? (
            <div className="pg-empty-state flex items-center justify-center h-full text-gray-400">
              <div className="pg-flow-empty text-center max-w-md">
                <div className="pg-flow-title">还没有页面内容</div>
                <div className="pg-flow-copy">上传材料或填写 Brief 后，先生成内容规划。</div>
              </div>
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
                const resolvedUrl = resolveAssetUrl(API_BASE, url);
                if (resolvedUrl.includes("/uploads/")) {
                  const pageRefUrls = dedupeReferenceImages(editingSlide?.reference_images || []).map((r: any) =>
                    resolveAssetUrl(API_BASE, r.url)
                  );
                  const projectRefUrls = visualAssetIdsForSlide(editingSlide)
                    .map((id) => {
                      const asset = projectVisualAssetById.get(id);
                      return resolveAssetUrl(API_BASE, asset?.overlay_url || asset?.url);
                    })
                    .filter(Boolean);
                  const refUrls = [...pageRefUrls, ...projectRefUrls].filter((v, i, a) => v && a.indexOf(v) === i);
                  const galleryUrls = refUrls.includes(resolvedUrl)
                    ? refUrls
                    : [resolvedUrl, ...refUrls].filter((v, i, a) => v && a.indexOf(v) === i);
                  const index = galleryUrls.indexOf(resolvedUrl);
                  setGalleryModal({ urls: galleryUrls, index: index >= 0 ? index : 0, title: "本页参考图" });
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
                      <h3>{styleDockProposals.length > 0 ? "选择视觉方向" : hasTemplateSource ? "生成模板视觉方向" : "生成视觉方向"}</h3>
                    </div>
                  </div>
                  {hasTemplateSource && (
                    <div className="pg-dock-section pg-dock-section--template">
                      <div className="pg-dock-section__head">
                        <span className="pg-dock-section__kicker">模板配置</span>
                        <span className="pg-dock-section__title">版式映射</span>
                        <span className="pg-dock-section__hint">{templatePages.length} 页</span>
                      </div>
                      <div className="pg-template-source-strip">
                        <div className="pg-template-source-text">
                          <b>{templateSourceCopy}</b>
                          <p>{templateSourceMeta}</p>
                        </div>
                        <div className="pg-template-source-actions">
                          {selectedTemplateRecommendationCount > 0 && (
                            <span className="pg-template-confirmed-badge">模板已确认</span>
                          )}
                          {selectedTemplateRecommendationCount === 0 && (
                            <>
                              {(() => {
                                const defaultSelection = buildDefaultTemplateSelection(templatePages);
                                const summary = TEMPLATE_CONFIRM_TYPES
                                  .filter((item) => defaultSelection[item.key])
                                .slice(0, 4)
                                .map((item) => `P${defaultSelection[item.key]}(${item.label})`)
                                .join(" · ");
                              return (
                                <div className="pg-template-smart-summary">
                                  <span>系统推荐：{summary}{templatePages.length > 4 ? " · 其他自动匹配" : ""}</span>
                                </div>
                              );
                            })()}
                            <button
                              type="button"
                              className="pg-template-confirm-primary"
                              onClick={handleConfirmTemplateRecommendations}
                              disabled={templateConfirmSaving || templatePages.length === 0}
                            >
                              {templateConfirmSaving ? "保存中..." : "确认使用模板"}
                            </button>
                          </>
                        )}
                        <button
                          type="button"
                          className="pg-template-adjust-btn"
                          onClick={() => {
                            setTemplateConfirmVisible(true);
                            setTemplateDrawerOpen(true);
                          }}
                          disabled={templatePages.length === 0}
                        >
                          调整页面映射
                        </button>
                      </div>
                      {showTemplateConfirmControls && (
                        <div className="pg-template-confirm-block pg-template-confirm-nudge">
                          <span>模板映射已移入右侧抽屉，调整时不会遮挡页面编辑。</span>
                          <button type="button" onClick={() => setTemplateDrawerOpen(true)}>打开映射抽屉</button>
                        </div>
                      )}
                    </div>
                  </div>
                )}
                <div className="pg-dock-section pg-dock-section--proposals">
                  {styleDockProposals.length > 0 ? (
                    <div className="pg-style-dock-grid">
                      {styleDockProposals.map((proposal, index) => {
                        const proposalKey = `${proposal.name}-${index}`;
                        const isExpanded = expandedStyleProposalKey === proposalKey;
                        const palette = Array.isArray(proposal.palette) ? proposal.palette : [];
                        const strategySummary = visualStrategyText(proposal);
                        const bestFor = proposalDecisionField(proposal, "best_for");
                        const tradeoff = proposalDecisionField(proposal, "tradeoff");
                        const visualFocus = proposalDecisionField(proposal, "visual_focus");
                        const summary = stripHexCodes(proposal.description || proposal.mood || "基于当前内容和素材生成的视觉方向。");
                        const proposalPreview = buildSelectedStylePreview(proposal);
                        return (
                          <div key={proposalKey} className={`pg-style-dock-card ${isExpanded ? "is-expanded" : ""}`}>
                            <div className="pg-style-dock-card-top">
                              <span>{proposalChoiceLabel(proposal, index)}</span>
                              <div className="pg-style-swatch-group">
                                <em>配色</em>
                                <div className="pg-style-swatches">
                                  {palette.slice(0, 5).map((c: any, i: number) => (
                                    <i key={i} style={{ backgroundColor: proposalColorValue(c) }} title={proposalColorLabel(c, i)} />
                                  ))}
                                </div>
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
                                <div className="pg-style-dock-preview-block">
                                  <div className="pg-style-page-previews pg-style-proposal-page-previews" aria-label="视觉方向页面类型图例">
                                    {proposalPreview.pages.map((page) => (
                                      <div
                                        key={page.key}
                                        className={`pg-style-page-mini is-${page.tone} is-${page.intensity}`}
                                        style={{
                                          "--style-page-bg": page.background,
                                          "--style-page-accent": page.accent,
                                          "--style-page-secondary": page.secondary,
                                          "--style-page-highlight": page.highlight,
                                          "--style-page-brand": page.brand,
                                          "--style-page-text": page.text,
                                          "--style-page-surface": page.surface,
                                          "--style-page-chart-1": page.chartColors[0],
                                          "--style-page-chart-2": page.chartColors[1],
                                          "--style-page-chart-3": page.chartColors[2],
                                          "--style-page-chart-4": page.chartColors[3],
                                        } as CSSProperties}
                                      >
                                        <span className="pg-style-page-mini-label">{page.label}</span>
                                        <i className="pg-style-page-mini-brand" />
                                        <i className="pg-style-page-mini-glow" />
                                        <i className="pg-style-page-mini-title" />
                                        <i className="pg-style-page-mini-line line-1" />
                                        <i className="pg-style-page-mini-line line-2" />
                                        {page.key === "data" && (
                                          <span className="pg-style-page-mini-chart" aria-hidden="true">
                                            <i style={{ height: "42%" }} />
                                            <i style={{ height: "76%" }} />
                                            <i style={{ height: "55%" }} />
                                            <i style={{ height: "90%" }} />
                                          </span>
                                        )}
                                      </div>
                                    ))}
                                  </div>
                                  <div className="pg-style-dock-compact-meta" aria-label="视觉方向摘要">
                                    <span>
                                      <b>视觉节奏</b>
                                      <em>{proposalPreview.rhythmText}</em>
                                    </span>
                                    <span>
                                      <b>字体</b>
                                      <em>{proposalPreview.fontText}</em>
                                    </span>
                                    {strategySummary && (
                                      <span>
                                        <b>整体基底</b>
                                        <em>{strategySummary}</em>
                                      </span>
                                    )}
                                    {visualFocus && (
                                      <span>
                                        <b>视觉重点</b>
                                        <em>{visualFocus}</em>
                                      </span>
                                    )}
                                  </div>
                                </div>
                              </div>
                            )}
                            <div className="pg-style-dock-card-actions">
                              <button
                                onClick={() => setExpandedStyleProposalKey(isExpanded ? null : proposalKey)}
                                disabled={isBusy || chatLoading}
                              >
                                {isExpanded ? "关闭详情" : "查看详情"}
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
                        <p>{hasTemplateSource ? "生成后会得到一套沿用模板的视觉方向。" : "生成后会在这里显示三套方向。"}</p>
                      </div>
                    </div>
                  )}
                </div>
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
                const slidePageReferenceItems = dedupeReferenceImages(slide.reference_images || []);
                const slideProjectMaterialItems = visualAssetIdsForSlide(slide)
                  .map((id) => ({ id, asset: projectVisualAssetById.get(id) }))
                  .filter(({ asset }) => Boolean(asset?.url || asset?.overlay_url));
                const hasVisualDescription = Boolean(visual.visual_description && String(visual.visual_description).trim());
                const hasPromptText = Boolean(slide.prompt_text && String(slide.prompt_text).trim());
                const isPrototypePageChecked = visiblePrototypePageSet.has(slide.page_num);
                const isDeckPageSelected = deckSelectedPages.has(slide.page_num);
                const isLast = index === slides.length - 1;
                const replicateSource = directReplicateSourceFacts(slide);
                const isSlideInActiveRunScope = Boolean(
                  hasActiveRun &&
                    activeProgressIsImageRun &&
                    (activeProgressTargetPageSet.size === 0 || activeProgressTargetPageSet.has(slide.page_num))
                );
                const isSlideActiveInRun = Boolean(
                  isSlideInActiveRunScope &&
                    (activeProgressActivePageSet.has(slide.page_num) || slide.status === "generating")
                );
                const isSlideWaitingInRun = Boolean(
                  isSlideInActiveRunScope &&
                    !isSlideActiveInRun &&
                    slide.status !== "completed" &&
                    slide.status !== "failed"
                );
                const slideRunLabel = isSlideActiveInRun
                  ? "正在生成"
                  : isSlideWaitingInRun
                  ? activeProgressDisclosure?.status === "queued" ? "排队中" : "等待生成"
                  : "";
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
                        activateFinetuneForSlide(slide);
                        handleEnterEdit(slide);
                      }
                    }}
	                    className={`pg-slide-card group relative bg-white rounded-lg border border-slate-200 p-3 shadow-sm flex flex-col cursor-pointer hover:shadow-lg hover:border-blue-400 transition-all h-[320px] overflow-hidden w-[calc((100%-4.5rem)/3)] min-w-[260px] flex-shrink-0 max-md:w-full ${
	                      isPrototypePageChecked && shouldShowPrototypeSelection
	                        ? "ring-2 ring-blue-400"
	                        : ""
	                    } ${isDeckPageSelected ? "pg-slide-card-selected" : ""} ${isSlideActiveInRun ? "pg-slide-card-run-active" : ""} ${isSlideWaitingInRun ? "pg-slide-card-run-waiting" : ""} ${finetuneTargetSlideId === slide.id && currentAgentRole === "finetune" ? "ring-2 ring-amber-400 border-amber-300" : ""} ${dragOverSlideId === slide.id ? "border-dashed border-blue-400 bg-blue-50" : ""} ${dragSlideId === slide.id ? "opacity-50" : ""}`}
	                  >
	                    <div className="flex items-center justify-between mb-1 shrink-0">
	                      <div className="flex items-center gap-1.5">
	                        {!shouldShowPrototypeSelection && (
	                          <input
	                            type="checkbox"
	                            checked={isDeckPageSelected}
	                            title="选中后可让 Agent 作用于这些页面"
	                            onClick={(e) => e.stopPropagation()}
	                            onChange={(e) => {
	                              e.stopPropagation();
	                              toggleDeckPageSelection(slide.page_num);
	                            }}
	                            className="cursor-pointer"
	                          />
	                        )}
	                        {shouldShowPrototypeSelection && (
	                          <input
                            type="checkbox"
                            checked={isPrototypePageChecked}
                            disabled={!canEditPrototypeSelection}
                            title={
                              canEditPrototypeSelection
                                ? isPrototypePageChecked
                                  ? "从样张范围移出本页"
                                  : "把本页加入样张范围"
                                : "当前任务进行中，样张范围已锁定"
                            }
                            aria-label={`${isPrototypePageChecked ? "移出" : "加入"}第 ${slide.page_num} 页样张范围`}
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
                        {slideRunLabel && (
                          <span className={`pg-slide-run-badge ${isSlideActiveInRun ? "is-active" : ""}`}>
                            <i />
                            {slideRunLabel}
                          </span>
                        )}
                        {replicateSource && (
                          <span
                            className={`text-[10px] px-1.5 py-0.5 rounded-full leading-none ${
                              replicateSource.qualityStatus === "passed"
                                ? "bg-emerald-50 text-emerald-700 border border-emerald-100"
                                : "bg-amber-50 text-amber-700 border border-amber-100"
                            }`}
                            title={replicateSource.qualityStatus === "passed" ? "已按原稿逐页承接" : "原稿承接需要检查"}
                          >
                            原稿 P{replicateSource.sourcePage}
                          </span>
                        )}
                        {statusText[slide.status] && <span className="text-sm">{statusText[slide.status]}</span>}
                      </div>
                      <div className="flex items-center gap-1">
                        <SlideReadinessIcons hasVisual={hasVisualDescription} hasPrompt={hasPromptText} />
                        <div className="relative" data-type-menu>
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              setActiveTypeMenuSlideId(activeTypeMenuSlideId === slide.id ? null : slide.id);
                            }}
                            className={`text-xs px-2 py-0.5 rounded font-medium leading-none cursor-pointer hover:opacity-80 ${typeColor[slide.type] || "bg-gray-100"}`}
                            title={slide.type_locked ? "类型已锁定，点击修改" : "点击修改页面类型"}
                          >
                            {typeLabel[slide.type] || slide.type}
                            {slide.type_locked && <span className="ml-0.5 opacity-60">🔒</span>}
                          </button>
                          {activeTypeMenuSlideId === slide.id && (
                            <div className="absolute right-0 top-full mt-1 z-50 bg-white border border-slate-200 rounded-lg shadow-lg p-1.5 min-w-[120px]">
                              {[
                                { key: "content", label: "正文" },
                                { key: "data", label: "数据" },
                                { key: "section", label: "章节" },
                                { key: "hero", label: "金句" },
                                { key: "agenda", label: "目录" },
                              ].map((opt) => (
                                <button
                                  key={opt.key}
                                  type="button"
                                  onClick={async (e) => {
                                    e.stopPropagation();
                                    if (opt.key !== slide.type && selectedProject) {
                                      try {
                                        await updateSlideType(selectedProject.id, slide.page_num, opt.key, slide.id);
                                        setSlides((prev) =>
                                          prev.map((s) =>
                                            s.id === slide.id ? { ...s, type: opt.key, type_locked: true } : s
                                          )
                                        );
                                      } catch (err) {
                                        console.error("Failed to update slide type:", err);
                                      }
                                    }
                                    setActiveTypeMenuSlideId(null);
                                  }}
                                  className={`w-full text-left text-xs px-2 py-1 rounded mb-0.5 last:mb-0 hover:bg-slate-50 ${
                                    slide.type === opt.key ? "bg-slate-100 font-medium" : ""
                                  }`}
                                >
                                  <span className={`inline-block w-2 h-2 rounded-full mr-1.5 ${typeColor[opt.key]?.split(" ")[0]?.replace("bg-", "bg-") || "bg-gray-200"}`} style={{ backgroundColor: "currentColor" }} />
                                  {opt.label}
                                  {slide.type === opt.key && <span className="ml-1 text-slate-400">✓</span>}
                                </button>
                              ))}
                              {slide.type_locked && (
                                <button
                                  type="button"
                                  onClick={async (e) => {
                                    e.stopPropagation();
                                    if (selectedProject) {
                                      try {
                                        await updateSlideType(selectedProject.id, slide.page_num, slide.type, slide.id);
                                        setSlides((prev) =>
                                          prev.map((s) =>
                                            s.id === slide.id ? { ...s, type_locked: false } : s
                                          )
                                        );
                                      } catch (err) {
                                        console.error("Failed to unlock slide type:", err);
                                      }
                                    }
                                    setActiveTypeMenuSlideId(null);
                                  }}
                                  className="w-full text-left text-xs px-2 py-1 rounded text-slate-400 hover:text-slate-600 hover:bg-slate-50 border-t border-slate-100 mt-1 pt-1"
                                >
                                  🔓 恢复自动判断
                                </button>
                              )}
                            </div>
                          )}
                        </div>
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
                          className="pg-slide-body-preview markdown-body flex-1 min-h-0 overflow-y-auto text-xs text-slate-500 leading-relaxed"
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
                          if (activateFinetuneForSlide(slide)) {
                            handleEnterEdit(slide);
                          }
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
                          {slidePageReferenceItems.length > 0 && (
                            <div className="flex gap-0.5 flex-nowrap overflow-x-auto">
                              {slidePageReferenceItems.map((ref: any) => (
                                <div key={ref.id} className="relative group flex-shrink-0">
                                  <img
                                    src={resolveAssetUrl(API_BASE, ref.url)}
                                    alt="ref"
                                    className="w-7 h-7 rounded object-cover border cursor-pointer"
                                    title={referenceThumbTitle(ref)}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      const allRefUrls = slides
                                        .flatMap((s) => [
                                          ...dedupeReferenceImages(s.reference_images || []).map((r: any) => resolveAssetUrl(API_BASE, r.url)),
                                          ...visualAssetIdsForSlide(s).map((id) => {
                                            const asset = projectVisualAssetById.get(id);
                                            return resolveAssetUrl(API_BASE, asset?.overlay_url || asset?.url);
                                          }),
                                        ])
                                        .filter(Boolean)
                                        .filter((v, i, a) => a.indexOf(v) === i);
                                      const url = resolveAssetUrl(API_BASE, ref.url);
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
                          {slideProjectMaterialItems.length > 0 && (
                            <div className="flex gap-0.5 flex-nowrap overflow-x-auto">
                              {slideProjectMaterialItems.map(({ id, asset }) => {
                                const url = resolveAssetUrl(API_BASE, asset.overlay_url || asset.url);
                                return (
                                  <div key={`project-material-${slide.id}-${id}`} className="relative group flex-shrink-0">
                                    <img
                                      src={url}
                                      alt={referenceDisplayName(asset, "项目素材")}
                                      className="w-7 h-7 rounded object-contain bg-white border border-emerald-200 cursor-pointer"
                                      title="项目素材 — 点击查看"
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        const allRefUrls = slides
                                          .flatMap((s) => [
                                            ...dedupeReferenceImages(s.reference_images || []).map((r: any) => resolveAssetUrl(API_BASE, r.url)),
                                            ...visualAssetIdsForSlide(s).map((assetId) => {
                                              const linkedAsset = projectVisualAssetById.get(assetId);
                                              return resolveAssetUrl(API_BASE, linkedAsset?.overlay_url || linkedAsset?.url);
                                            }),
                                          ])
                                          .filter(Boolean)
                                          .filter((v, i, a) => a.indexOf(v) === i);
                                        const index = allRefUrls.indexOf(url);
                                        setGalleryModal({ urls: allRefUrls, index: index >= 0 ? index : 0, title: "本页参考图" });
                                      }}
                                      onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                                    />
                                  </div>
                                );
                              })}
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
                        <div className="flex flex-col gap-0.5 self-start">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              handleRetry(slide.id);
                            }}
                            disabled={isBusy || chatLoading}
                            className="text-xs bg-red-50 text-red-600 px-2 py-1 rounded hover:bg-red-100 self-start disabled:opacity-50 leading-none"
                            title={slide.error_msg || undefined}
                          >
                            {isBusy ? "重试中..." : "重试"}
                          </button>
                          {slide.error_msg && (
                            <span className="text-[10px] text-slate-400 truncate max-w-[140px]" title={slide.error_msg}>
                              {slide.error_msg.length > 20 ? `${slide.error_msg.slice(0, 20)}...` : slide.error_msg}
                            </span>
                          )}
                        </div>
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
          </div>
		      </main>

      {templateDrawerOpen && templatePages.length > 0 && (
        <div className="pg-side-drawer-backdrop" role="presentation" onClick={() => setTemplateDrawerOpen(false)}>
          <aside className="pg-side-drawer pg-template-drawer" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <div className="pg-drawer-head">
              <div>
                <div className="pg-drawer-kicker">Template Mapping</div>
                <h2>模板页面映射</h2>
                <p>选择每类页面参考哪一张模板缩略图，不再使用长下拉遮挡编辑区。</p>
              </div>
              <button type="button" onClick={() => setTemplateDrawerOpen(false)}>关闭</button>
            </div>
            {(() => {
              const fallbackSelection = buildDefaultTemplateSelection(templatePages);
              const visibleTypes = showAdvancedMapping
                ? TEMPLATE_CONFIRM_TYPES
                : TEMPLATE_CONFIRM_TYPES.filter((item) => ["cover", "content", "ending"].includes(item.key));
              return (
                <>
                  <div className="pg-template-drawer-grid">
                    {visibleTypes.map((item) => {
                      const selectedPageNum = templatePageSelection[item.key] || fallbackSelection[item.key];
                      return (
                        <section key={item.key} className="pg-template-picker-group">
                          <div className="pg-template-picker-head">
                            <span>{item.label}</span>
                            <b>{selectedPageNum ? `P${selectedPageNum}` : "未选择"}</b>
                          </div>
                          <div className="pg-template-thumb-picker">
                            {templatePages.map((page: any) => {
                              const isSelected = Number(page.page_num) === Number(selectedPageNum);
                              return (
                                <button
                                  key={`${item.key}-${page.page_num}`}
                                  type="button"
                                  className={isSelected ? "is-selected" : ""}
                                  onClick={() => setTemplatePageSelection((prev) => ({ ...prev, [item.key]: Number(page.page_num) }))}
                                  title={`使用模板 P${page.page_num}`}
                                >
                                  <img src={page.layout_url || page.url} alt={`模板第 ${page.page_num} 页`} />
                                  <span>P{page.page_num}</span>
                                </button>
                              );
                            })}
                          </div>
                        </section>
                      );
                    })}
                  </div>
                  <button
                    type="button"
                    className="pg-template-advanced-toggle"
                    onClick={() => setShowAdvancedMapping((value) => !value)}
                  >
                    {showAdvancedMapping ? "只看核心页面" : "显示更多页面类型"}
                  </button>
                  <div className="pg-template-strength-row pg-template-drawer-actions">
                    <span>参考范围</span>
                    <button type="button" className={templateApplicationStrength === "light" ? "is-active" : ""} onClick={() => setTemplateApplicationStrength("light")}>
                      只学版式
                    </button>
                    <button type="button" className={templateApplicationStrength === "standard" ? "is-active" : ""} onClick={() => setTemplateApplicationStrength("standard")}>
                      版式+配色
                    </button>
                    <button type="button" className={templateApplicationStrength === "strong" ? "is-active" : ""} onClick={() => setTemplateApplicationStrength("strong")}>
                      完全沿用
                    </button>
                    <button type="button" className="pg-template-confirm-primary" onClick={handleConfirmTemplateRecommendations} disabled={templateConfirmSaving}>
                      {templateConfirmSaving ? "保存中..." : "确认使用模板"}
                    </button>
                  </div>
                </>
              );
            })()}
          </aside>
        </div>
      )}


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
          {selectedProject && agentContractRows.length > 0 && (
            <details className="pg-agent-contract-summary">
              <summary>
                <span>项目背景</span>
                <b>来自 Brief</b>
              </summary>
              <div>
                {agentContractRows.map((row) => (
                  <p key={row.label}>
                    <b>{row.label}</b>
                    <span>{row.value}</span>
                  </p>
                ))}
              </div>
            </details>
          )}
          {selectedProject && agentDeliveryCheckItems.length > 0 && (
            <div className="pg-agent-delivery-check">
              <div>
                <b>交付检查</b>
                <span>根据当前页面状态判断</span>
              </div>
              {agentDeliveryCheckItems.map((item, index) => (
                <p key={`${item.tone}-${index}`} className={`is-${item.tone}`}>{item.text}</p>
              ))}
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
            const isExecutionEvent = Boolean(msg.loading || msg.runId);
            return (
            <div
              key={msg.id || i}
              className={`pg-message-row flex ${rowAlignClass} ${isExecutionEvent ? "pg-agent-execution-event" : ""}`}
              data-run-id={msg.runId || undefined}
            >
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
                          <div className="pg-agent-task-card">
                            <div>
                              <b>{msg.nextAction!.label}</b>
                              <span>{msg.nextAction!.description || "我会按当前项目状态执行这一步。"}</span>
                            </div>
                            <button
                              onClick={() => handleAgentNextAction(msg.nextAction!, msg)}
                              disabled={isBusy || chatLoading}
                              className="pg-action pg-action-primary"
                            >
                              {isBusy ? "处理中..." : msg.nextAction!.label}
                            </button>
                          </div>
                        );
                      })()}
                    </div>
                    {/* 视觉提案由中间主舞台统一管理，Agent 只保留轻量提示。 */}
                    {msg.role === "agent" && msg.agentRole === "visual" && msg.styleProposals && msg.styleProposals.length > 0 && isMessageFromCurrentGate(msg) && (
                      <div className="pg-agent-inline-notice">
                        <b>已生成 {msg.styleProposals.length} 套视觉方向</b>
                        <span>请在中间提案看板比较和确认，详情会在检查器里打开。</span>
                      </div>
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
          {selectedProject && currentAgentRole === "visual" && currentStageNudge && (
            <div className="mb-3 rounded-xl border border-amber-200 bg-amber-50/90 p-3 shadow-sm">
              <div className="text-sm font-semibold text-amber-900">{currentStageNudge.title}</div>
              <p className="mt-1 text-xs leading-relaxed text-amber-800">{currentStageNudge.body}</p>
              {currentStageNudge.primary && (
                <button
                  type="button"
                  onClick={currentStageNudge.primary.onClick}
                  disabled={isBusy || chatLoading}
                  className="pg-action pg-action-primary mt-3 w-full rounded-lg bg-amber-600 px-3 py-2 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-50"
                >
                  {currentStageNudge.primary.label}
                </button>
              )}
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
                  const initialResult = await extractTemplate(selectedProject.id, file);
                  showToast("已收到版式模板，正在提取页面和 Logo");
                  const result = initialResult?.status === "completed"
                    ? initialResult
                    : await waitForTemplateExtraction(selectedProject.id, initialResult?.job_id);
                  const isFinishedPpt = result?.document_kind === "finished_ppt";
                  const logoText = Number(result?.extracted_logos || 0) > 0 ? `，识别出 ${result.extracted_logos} 个 Logo` : "";
                  showToast(isFinishedPpt ? `已学习这份成品 PPT 的版式${logoText}` : `版式模板已上传并提取${logoText}`);
                  await loadReferenceImages(selectedProject.id);
                  await loadTemplatePages(selectedProject.id);
                  await loadProjects();
                  setAssetsBarExpanded(true);
                  setTemplateConfirmDismissedProjectId(null);
                  setTemplateConfirmVisible(true);
                  slides.forEach((s) => markSlideStale(s.id, "content"));
                  addSystemLog(`用户上传了版式模板「${file.name}」`);
                  if (currentAgentRole === "visual") {
                    appendProjectChatMessage(selectedProject.id, "visual", {
                      role: "user",
                      content: isFinishedPpt
                        ? `已上传成品 PPT 作为版式参考：${file.name}。我会只学习版式，不读取正文。`
                        : `已上传版式模板：${file.name}`,
                      agentRole: "visual",
                    });
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
          {shouldShowAgentRunControl ? (
            <div className="pg-agent-run-control">
              <div className="pg-agent-run-copy">
                {agentProgressDisplay?.showAgentCopy ? (
                  <>
                    <span className="pg-agent-run-kicker">{activeProgressDisclosure?.headline || activeProgressLabel}</span>
                    <span>{activeProgressDisclosure?.summary || activeProgressStatusText || "当前任务正在处理；需要取消可以停止生成。"}</span>
                  </>
                ) : (
                  <span className="pg-agent-run-kicker">后台处理中</span>
                )}
                {renderProgressOverview("agent")}
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
              <div className="pg-agent-command-bar">
                <div className="pg-agent-command-summary">
                  {currentAgentRole === "finetune" ? (
                    <div className="pg-agent-command-copy">
                      <b>{agentFinetuneHeadline}</b>
                      {agentSecondaryHint && <span>{agentSecondaryHint}</span>}
                    </div>
                  ) : composerRequestContext.confidence === "needs_input" ? (
                    <div className="pg-agent-command-copy">
                      <b>先选页面，或直接说第几页</b>
                      {agentSecondaryHint && <span>{agentSecondaryHint}</span>}
                    </div>
                  ) : (
                    <div className="pg-agent-command-sentence">
                      <span>将修改</span>
                      <button
                        type="button"
                        onClick={() => {
                          setAgentScopePickerOpen((open) => !open);
                          setAgentMaterialSheetOpen(false);
                          setAgentAreaPickerOpen(false);
                        }}
                        disabled={!selectedProject || slides.length === 0}
                        className={`pg-agent-command-chip ${agentScopePickerOpen ? "is-active" : ""}`}
                        aria-expanded={agentScopePickerOpen}
                        title="调整本次修改范围"
                      >
                        {agentScopeButtonLabel}
                      </button>
                      <span>的</span>
                      <button
                        type="button"
                        onClick={() => {
                          setAgentAreaPickerOpen((open) => !open);
                          setAgentScopePickerOpen(false);
                          setAgentMaterialSheetOpen(false);
                        }}
                        disabled={!selectedProject || slides.length === 0}
                        className={`pg-agent-command-chip ${agentAreaPickerOpen ? "is-active" : ""}`}
                        aria-expanded={agentAreaPickerOpen}
                        title="调整本次修改区域"
                      >
                        {agentAreaButtonLabel}
                      </button>
                    </div>
                  )}
                </div>
                {currentAgentRole !== "finetune" && slides.length > 0 && agentAreaPickerOpen && (
                  <div className="pg-agent-area-panel" role="group" aria-label="调整区域">
                    {agentAreaOptions.map((item) => (
                      <button
                        key={item.value}
                        type="button"
                        onClick={() => {
                          setAgentTargetAreaOverride(item.value);
                          setAgentAreaPickerOpen(false);
                        }}
                        className={composerRequestContext.targetArea === item.value ? "is-active" : ""}
                        title={item.hint}
                      >
                        <b>{item.label}</b>
                        <span>{item.hint}</span>
                      </button>
                    ))}
                  </div>
                )}
                {currentAgentRole !== "finetune" && slides.length > 0 && agentScopePickerOpen && (
                  <div className="pg-agent-scope-panel" role="group" aria-label="调整范围">
                    <button
                      type="button"
                      onClick={() => {
                        setAgentScope("current_slide");
                        setAgentScopePickerOpen(false);
                      }}
                      disabled={!editingSlide}
                      className={composerRequestContext.scope === "current_slide" ? "is-active" : ""}
                      title={editingSlide ? `只修改第 ${editingSlide.page_num} 页` : "进入单页后可用"}
                    >
                      <b>当前页</b>
                      <span>{editingSlide ? `第 ${editingSlide.page_num} 页` : "进入单页可用"}</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setAgentScope("selected_slides");
                        setAgentScopePickerOpen(false);
                      }}
                      disabled={deckSelectedPageNums.length === 0}
                      className={composerRequestContext.scope === "selected_slides" ? "is-active" : ""}
                      title={deckSelectedPageNums.length > 0 ? `只修改选中的 ${deckSelectedPageNums.length} 页` : "先在中间选择页面"}
                    >
                      <b>选中页</b>
                      <span>{deckSelectedPageNums.length > 0 ? `${deckSelectedPageNums.length} 页` : "先勾选页面"}</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setAgentScope("deck");
                        setAgentScopePickerOpen(false);
                      }}
                      className={composerRequestContext.scope === "deck" ? "is-active" : ""}
                      title="修改整套 PPT"
                    >
                      <b>整套</b>
                      <span>{slides.length || 0} 页</span>
                    </button>
                  </div>
                )}
              </div>
              {agentMaterialSheetOpen && (
                <div className="pg-agent-material-sheet">
                  <div className="pg-agent-material-group">
                    <span>本轮材料</span>
                    <button type="button" onClick={() => { setAgentMaterialSheetOpen(false); handlePickAgentAttachments(); }}>
                      <b>让 Agent 阅读</b><span>PDF、PPT、Markdown、图片</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setAgentMaterialSheetOpen(false);
                        const targetSlide = editingSlide || (deckSelectedPages.size === 1 ? slides.find((slide) => deckSelectedPages.has(slide.page_num)) : null);
                        if (targetSlide) {
                          handleUploadPageRef(targetSlide.id);
                        } else {
                          showToast("请先进入单页或只选中一页", "info");
                        }
                      }}
                    >
                      <b>作为本页素材</b><span>只绑定到当前页面</span>
                    </button>
                  </div>
                  <div className="pg-agent-material-group">
                    <span>项目资产</span>
                    <button type="button" onClick={() => { setAgentMaterialSheetOpen(false); styleRefInputRef.current?.click(); }}>
                      <b>作为视觉参考</b><span>学习气质，不强制出现</span>
                    </button>
                    <button type="button" onClick={() => { setAgentMaterialSheetOpen(false); visualAssetInputRef.current?.click(); }}>
                      <b>必须出现在画面</b><span>产品图、人物、物料</span>
                    </button>
                    <button type="button" onClick={() => { setAgentMaterialSheetOpen(false); templateInputRef.current?.click(); }}>
                      <b>学习模板版式</b><span>PPT、PPTX 或 PDF</span>
                    </button>
                  </div>
                </div>
              )}
              <div className="pg-composer-row flex gap-2">
            <div className="pg-composer-input-shell">
              <button
                type="button"
                onClick={() => {
                  setAgentMaterialSheetOpen((open) => !open);
                  setAgentScopePickerOpen(false);
                  setAgentAreaPickerOpen(false);
                }}
                disabled={!selectedProject || chatLoading || uploadingDoc}
                className={`pg-composer-attach-button ${agentMaterialSheetOpen ? "is-active" : ""}`}
                title="添加参考材料"
                aria-label="添加参考材料"
                aria-expanded={agentMaterialSheetOpen}
              >
                {agentMaterialCount > 0 ? (
                  <span className="pg-composer-attach-count">{agentMaterialCount}</span>
                ) : (
                  <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <path d="M12 5v14" />
                    <path d="M5 12h14" />
                  </svg>
                )}
              </button>
              <textarea
                ref={chatInputRef}
                className="pg-chat-input pg-chat-input-inline flex-1 resize-none px-3 py-2.5 text-sm outline-none transition-colors"
                style={{ minHeight: 38, overflowY: "hidden" }}
                placeholder={agentComposerPlaceholder}
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
            </div>
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
                className="relative z-20 text-white hover:text-gray-300 text-xl px-2"
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

type ContentBlockKind = "markdown" | "table" | "flywheel" | "flow" | "matrix";
type VisualRouteMode = "blend" | "crop" | "original";

interface ContentBlock {
  id: string;
  kind: ContentBlockKind;
  visual_type?: string;
  title?: string;
  markdown?: string;
  source_spec?: any;
  route_mode?: VisualRouteMode;
  rendered_asset_id?: string;
  source_hash?: string;
}

interface VisualDirectiveSuggestion {
  id?: string;
  original_text: string;
  directive: string;
  kind?: string;
  diagram_labels?: string[];
}

const assetRouteToBlockRoute = (route: "blend" | "double_blend" | "overlay"): VisualRouteMode => {
  if (route === "overlay") return "original";
  if (route === "double_blend") return "crop";
  return "blend";
};

const VISUAL_BLOCK_LABELS: Record<string, string> = {
  table: "表格",
  flywheel: "飞轮",
  flow: "流程图",
  matrix: "对比矩阵",
};

const newBlockId = (kind: string) => `${kind}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;

const defaultVisualBlock = (kind: Exclude<ContentBlockKind, "markdown">): ContentBlock => {
  const title = VISUAL_BLOCK_LABELS[kind] || "画面素材";
  const source_spec =
    kind === "flywheel"
      ? { center: "增长飞轮", nodes: ["获客", "激活", "留存", "推荐"] }
      : kind === "flow"
      ? { steps: ["输入", "处理", "输出"] }
      : kind === "matrix"
      ? { columns: ["维度", "方案 A", "方案 B"], rows: [["价值", "", ""], ["成本", "", ""]] }
      : { columns: ["项目", "说明"], rows: [["要点", "说明"]] };
  return {
    id: newBlockId(kind),
    kind,
    visual_type: kind,
    title,
    source_spec,
    route_mode: kind === "table" ? "blend" : "crop",
  };
};

const normalizeEditorContentBlocks = (content: any, fallbackBody: string): ContentBlock[] => {
  const raw = Array.isArray(content?.content_blocks) ? content.content_blocks : null;
  if (!raw) {
    return [{ id: "body", kind: "markdown", markdown: fallbackBody || "" }];
  }
  const blocks = raw
    .filter((block: any) => block && typeof block === "object")
    .map((block: any, index: number) => {
      const kind = String(block.kind || "markdown").toLowerCase() as ContentBlockKind;
      const id = String(block.id || `block_${index + 1}`);
      if (kind === "markdown") {
        return { id, kind: "markdown", markdown: String(block.markdown ?? "") } as ContentBlock;
      }
      const normalizedKind: Exclude<ContentBlockKind, "markdown"> =
        kind === "flywheel" || kind === "flow" || kind === "matrix" || kind === "table" ? kind : "table";
      return {
        ...defaultVisualBlock(normalizedKind),
        ...block,
        id,
        kind: normalizedKind,
        visual_type: block.visual_type || normalizedKind,
        route_mode: (block.route_mode === "original" || block.route_mode === "crop" || block.route_mode === "blend")
          ? block.route_mode
          : defaultVisualBlock(normalizedKind).route_mode,
      } as ContentBlock;
    });
  return blocks.length ? blocks : [{ id: "body", kind: "markdown", markdown: fallbackBody || "" }];
};

const visualBlockSummary = (block: ContentBlock): string => {
  const title = block.title || VISUAL_BLOCK_LABELS[block.kind] || "画面素材";
  const spec = block.source_spec || {};
  if (block.kind === "flywheel") {
    const nodes = Array.isArray(spec.nodes) ? spec.nodes.map((node: any) => typeof node === "string" ? node : node?.label).filter(Boolean) : [];
    return `[画面素材：${title}]\n类型：飞轮图\n节点：${nodes.join(" → ")}`;
  }
  if (block.kind === "flow") {
    const steps = Array.isArray(spec.steps) ? spec.steps.map((step: any) => typeof step === "string" ? step : step?.label).filter(Boolean) : [];
    return `[画面素材：${title}]\n类型：流程图\n步骤：${steps.join(" → ")}`;
  }
  return `[画面素材：${title}]\n类型：${VISUAL_BLOCK_LABELS[block.kind] || "结构图"}`;
};

const contentBlocksToMarkdown = (blocks: ContentBlock[]): string => {
  return blocks
    .map((block) => block.kind === "markdown" ? (block.markdown || "").trim() : visualBlockSummary(block))
    .filter(Boolean)
    .join("\n\n");
};

const normalizeDirectiveLine = (value: string) =>
  String(value || "")
    .replace(/^\s*(?:[-*+]\s+|\d+[.)、]\s+|>\s+)?/, "")
    .trim();

const removeDirectiveLineFromMarkdown = (markdown: string, originalText: string) => {
  const target = normalizeDirectiveLine(originalText);
  if (!target) return markdown;
  const lines = String(markdown || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  const next = lines
    .map((line) => {
      const normalized = normalizeDirectiveLine(line);
      if (normalized === target) return "";
      if (normalized.includes(target)) {
        return line
          .replace(target, "")
          .replace(/\s+([，。；：、,.!?])/g, "$1")
          .replace(/([，,；;])\s*([。；;，,])/g, "$2")
          .trim();
      }
      return line;
    })
    .filter((line) => line.trim())
    .join("\n");
  return next.replace(/\n{3,}/g, "\n\n").trim();
};

const uniqueStringList = (values: string[]) =>
  values.map((value) => String(value || "").trim()).filter((value, index, arr) => value && arr.indexOf(value) === index);

const mergeVisualRequirements = (existing: any, suggestion: VisualDirectiveSuggestion) => {
  const current = Array.isArray(existing) ? existing.filter((item) => item && typeof item === "object") : [];
  const next = {
    kind: suggestion.kind || "diagram",
    directive: suggestion.directive,
    diagram_labels: uniqueStringList(suggestion.diagram_labels || []),
    source_text: suggestion.original_text,
  };
  const key = `${next.directive}::${next.diagram_labels.join("|")}`;
  const hasSame = current.some((item: any) => `${item.directive || ""}::${(item.diagram_labels || []).join("|")}` === key);
  return hasSame ? current : [...current, next];
};

const visualSuggestionLine = (suggestion: VisualDirectiveSuggestion) => {
  const labels = uniqueStringList(suggestion.diagram_labels || []);
  return labels.length ? `${suggestion.directive}（图示标签：${labels.join("、")}）` : suggestion.directive;
};

const appendVisualSuggestion = (existing: any, suggestion: VisualDirectiveSuggestion) => {
  const current = String(existing || "").trim();
  const line = visualSuggestionLine(suggestion);
  if (!line) return current;
  if (current.includes(line)) return current;
  return current ? `${current}\n${line}` : line;
};

const EDITOR_MARKDOWN_ALLOWED_TAGS = [
  "p", "br", "strong", "em", "b", "i", "u", "s",
  "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
  "code", "pre", "blockquote", "table", "thead", "tbody", "tr", "th", "td", "hr", "a",
];

const EDITOR_MARKDOWN_ALLOWED_ATTR = ["href", "title", "target", "rel", "colspan", "rowspan"];

const markdownToEditorHtml = (markdown: string): string => {
  const normalized = normalizeMarkdownEmphasis(markdown || "");
  const html = (marked.parse(normalized, { async: false }) as string) || "";
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS: EDITOR_MARKDOWN_ALLOWED_TAGS,
    ALLOWED_ATTR: EDITOR_MARKDOWN_ALLOWED_ATTR,
  });
};

const visualBlockToEditorHtml = (block: ContentBlock): string =>
  `<div data-visual-block="true" data-block="${escapeHtml(JSON.stringify(block))}"></div>`;

const inlineNodeText = (node: any): string => {
  if (!node) return "";
  if (node.type === "text") {
    let text = String(node.text || "");
    const marks = Array.isArray(node.marks) ? node.marks.map((mark: any) => mark.type) : [];
    if (marks.includes("bold")) text = `**${text}**`;
    if (marks.includes("italic")) text = `*${text}*`;
    if (marks.includes("code")) text = `\`${text}\``;
    return text;
  }
  return (node.content || []).map(inlineNodeText).join("");
};

const markdownFromTiptapNode = (node: any): string => {
  if (!node) return "";
  if (node.type === "paragraph") return (node.content || []).map(inlineNodeText).join("");
  if (node.type === "heading") {
    const level = Math.max(1, Math.min(4, Number(node.attrs?.level || 2)));
    return `${"#".repeat(level)} ${(node.content || []).map(inlineNodeText).join("")}`;
  }
  if (node.type === "bulletList") {
    return (node.content || []).map((item: any) => `- ${markdownFromTiptapNode(item).replace(/\n/g, "\n  ")}`).join("\n");
  }
  if (node.type === "orderedList") {
    return (node.content || []).map((item: any, index: number) => `${index + 1}. ${markdownFromTiptapNode(item).replace(/\n/g, "\n   ")}`).join("\n");
  }
  if (node.type === "listItem") return (node.content || []).map(markdownFromTiptapNode).join("\n");
  if (node.type === "blockquote") return (node.content || []).map(markdownFromTiptapNode).join("\n").split("\n").map((line: string) => `> ${line}`).join("\n");
  if (node.type === "codeBlock") return "```\n" + ((node.content || []).map(inlineNodeText).join("") || "") + "\n```";
  if (node.type === "horizontalRule") return "---";
  if (node.type === "hardBreak") return "\n";
  if (node.type === "table") {
    const rows = (node.content || []).map((row: any) => (row.content || []).map((cell: any) => (cell.content || []).map(markdownFromTiptapNode).join(" ").trim()));
    if (!rows.length) return "";
    const widths = rows[0].map((_: string, i: number) => Math.max(...rows.map((row: string[]) => String(row[i] || "").length), 2));
    const fmt = (row: string[]) => `| ${widths.map((w: number, i: number) => String(row[i] || "").padEnd(w, " ")).join(" | ")} |`;
    return [fmt(rows[0]), `| ${widths.map((w: number) => "-".repeat(w)).join(" | ")} |`, ...rows.slice(1).map(fmt)].join("\n");
  }
  return (node.content || []).map(markdownFromTiptapNode).join("\n\n");
};

const tiptapDocFromBlocks = (blocks: ContentBlock[]): string => {
  const html = blocks
    .map((block) => block.kind === "markdown" ? markdownToEditorHtml(block.markdown || "") : visualBlockToEditorHtml(block))
    .filter((part) => part.trim())
    .join("");
  return html || "<p></p>";
};

const tiptapDocToBlocks = (doc: any): ContentBlock[] => {
  const blocks: ContentBlock[] = [];
  let markdownBuffer: string[] = [];
  const flushMarkdown = () => {
    const markdown = markdownBuffer.join("\n\n").trim();
    if (markdown || blocks.length === 0) {
      blocks.push({ id: blocks.length === 0 ? "body" : newBlockId("body"), kind: "markdown", markdown });
    }
    markdownBuffer = [];
  };
  for (const node of doc?.content || []) {
    if (node.type === "visualBlock") {
      flushMarkdown();
      try {
        const parsed = JSON.parse(node.attrs?.block || "{}");
        blocks.push({ ...defaultVisualBlock(parsed.kind || "table"), ...parsed });
      } catch {
        blocks.push(defaultVisualBlock("table"));
      }
    } else {
      const md = markdownFromTiptapNode(node).trim();
      if (md) markdownBuffer.push(md);
    }
  }
  flushMarkdown();
  return blocks;
};

const updateVisualBlockSpec = (block: ContentBlock, patch: any): ContentBlock => ({
  ...block,
  source_spec: {
    ...(block.source_spec || {}),
    ...patch,
  },
});

function VisualBlockNodeView(props: any) {
  const block: ContentBlock = useMemo(() => {
    try {
      const parsed = JSON.parse(props.node?.attrs?.block || "{}");
      const kind = parsed.kind === "flywheel" || parsed.kind === "flow" || parsed.kind === "matrix" || parsed.kind === "table" ? parsed.kind : "table";
      return { ...defaultVisualBlock(kind), ...parsed };
    } catch {
      return defaultVisualBlock("table");
    }
  }, [props.node?.attrs?.block]);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const updateBlock = (next: ContentBlock) => props.updateAttributes({ block: JSON.stringify(next) });
  const spec = block.source_spec || {};
  const listKey = block.kind === "flow" ? "steps" : "nodes";
  const listValue = Array.isArray(spec[listKey])
    ? spec[listKey].map((item: any) => typeof item === "string" ? item : item?.label || "").join("\n")
    : "";
  const tableText = Array.isArray(spec.rows)
    ? [
        Array.isArray(spec.columns) ? spec.columns.join(" | ") : "",
        ...spec.rows.map((row: any) => Array.isArray(row) ? row.join(" | ") : String(row || "")),
      ].filter(Boolean).join("\n")
    : "";
  return (
    <NodeViewWrapper className="my-3" data-visual-block-id={block.id}>
      <div className="rounded-lg border border-violet-200 bg-violet-50/60 p-3">
        <div className="flex items-center justify-between gap-2 mb-2">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-[11px] font-semibold text-violet-700 bg-white border border-violet-200 rounded px-2 py-0.5">
              {VISUAL_BLOCK_LABELS[block.kind] || "画面素材"}
            </span>
            <input
              value={block.title || ""}
              onChange={(e) => updateBlock({ ...block, title: e.target.value })}
              className="min-w-0 flex-1 bg-transparent text-sm font-semibold text-slate-800 outline-none"
              placeholder="素材标题"
            />
          </div>
          <div className="flex items-center gap-1">
            <button type="button" onClick={() => setAdvancedOpen((v) => !v)} className="text-[11px] text-slate-500 hover:text-slate-700 px-2 py-1 rounded hover:bg-white">
              {advancedOpen ? "收起" : "编辑结构"}
            </button>
            <button type="button" onClick={() => props.deleteNode?.()} className="text-[11px] text-red-500 hover:text-red-600 px-2 py-1 rounded hover:bg-white">
              删除
            </button>
          </div>
        </div>
        {block.kind === "flywheel" && (
          <div className="grid grid-cols-1 gap-2">
            <input
              value={spec.center || ""}
              onChange={(e) => updateBlock(updateVisualBlockSpec(block, { center: e.target.value }))}
              className="text-xs rounded border border-violet-100 bg-white px-2 py-1.5 outline-none focus:ring-2 focus:ring-violet-200"
              placeholder="中心词"
            />
            <textarea
              value={listValue}
              onChange={(e) => updateBlock(updateVisualBlockSpec(block, { nodes: e.target.value.split("\n").map((line) => line.trim()).filter(Boolean) }))}
              className="text-xs rounded border border-violet-100 bg-white px-2 py-1.5 min-h-[82px] outline-none focus:ring-2 focus:ring-violet-200"
              placeholder="每行一个节点"
            />
          </div>
        )}
        {block.kind === "flow" && (
          <textarea
            value={listValue}
            onChange={(e) => updateBlock(updateVisualBlockSpec(block, { steps: e.target.value.split("\n").map((line) => line.trim()).filter(Boolean) }))}
            className="w-full text-xs rounded border border-violet-100 bg-white px-2 py-1.5 min-h-[92px] outline-none focus:ring-2 focus:ring-violet-200"
            placeholder="每行一个步骤"
          />
        )}
        {(block.kind === "table" || block.kind === "matrix") && (
          <textarea
            value={tableText}
            onChange={(e) => {
              const rows = e.target.value.split("\n").map((line) => line.split("|").map((cell) => cell.trim())).filter((row) => row.some(Boolean));
              updateBlock(updateVisualBlockSpec(block, { columns: rows[0] || [], rows: rows.slice(1) }));
            }}
            className="w-full text-xs rounded border border-violet-100 bg-white px-2 py-1.5 min-h-[110px] outline-none focus:ring-2 focus:ring-violet-200 font-mono"
            placeholder="第一行是表头，使用 | 分隔列"
          />
        )}
	        {advancedOpen && (
	          <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-violet-100 pt-2">
	            <label className="text-[11px] text-slate-500">默认素材方式</label>
	            <div className="pg-mini-segmented" role="group" aria-label="默认素材方式">
	              {([
	                ["blend", "智能融合"],
	                ["crop", "精修融合"],
	                ["original", "精确粘贴"],
	              ] as Array<[VisualRouteMode, string]>).map(([value, label]) => (
	                <button
	                  key={value}
	                  type="button"
	                  className={(block.route_mode || "crop") === value ? "is-active" : ""}
	                  onClick={() => updateBlock({ ...block, route_mode: value })}
	                >
	                  {label}
	                </button>
	              ))}
	            </div>
	          </div>
	        )}
      </div>
    </NodeViewWrapper>
  );
}

const VisualBlockExtension = TiptapNode.create({
  name: "visualBlock",
  group: "block",
  atom: true,
  draggable: true,
  addAttributes() {
    return {
      block: {
        default: "{}",
      },
    };
  },
  parseHTML() {
    return [{
      tag: "div[data-visual-block]",
      getAttrs: (element) => ({
        block: element instanceof HTMLElement ? element.getAttribute("data-block") || "{}" : "{}",
      }),
    }];
  },
  renderHTML({ HTMLAttributes }) {
    return ["div", { ...HTMLAttributes, "data-visual-block": "true", "data-block": HTMLAttributes.block || "{}" }];
  },
  addNodeView() {
    return ReactNodeViewRenderer(VisualBlockNodeView);
  },
});

interface EditorState {
  headline: string;
  subhead: string;
  body: string;
  contentBlocks: ContentBlock[];
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
  const [contentBlocks, setContentBlocks] = useState<ContentBlock[]>(() => normalizeEditorContentBlocks(content, normalizeBody(text.body)));
  const [bodyEmpty, setBodyEmpty] = useState(!body || body.trim() === "");
  const [speakerNotes, setSpeakerNotes] = useState(unescapeText(content.speaker_notes || ""));

  // 视觉方案编辑状态
  const [visualDescription, setVisualDescription] = useState(slide.visual_json?.visual_description || "");
  const [promptExpanded, setPromptExpanded] = useState(Boolean(slide.prompt_text));

  // 撤销/重做：用 state 管理确保 UI 实时响应
  const initialState: EditorState = {
    headline: unescapeText(text.headline || ""),
    subhead: unescapeText(text.subhead || ""),
    body: normalizeBody(text.body),
    contentBlocks: normalizeEditorContentBlocks(content, normalizeBody(text.body)),
  };
  const [history, setHistory] = useState<EditorState[]>([initialState]);
  const [historyIndex, setHistoryIndex] = useState(0);
  const isUndoingRef = useRef(false);
  const [slashMenuOpen, setSlashMenuOpen] = useState(false);
  const [insertMenuOpen, setInsertMenuOpen] = useState(false);
  const [bodyEditMode, setBodyEditMode] = useState<"canvas" | "markdown">("canvas");
  const [markdownModeEntryBody, setMarkdownModeEntryBody] = useState(body);
  const [visualDirectiveSuggestions, setVisualDirectiveSuggestions] = useState<VisualDirectiveSuggestion[]>([]);

  const editor = useEditor({
    extensions: [
      StarterKit,
      Underline,
      Table.configure({ resizable: true }),
      TableRow,
      TableHeader,
      TableCell,
      VisualBlockExtension,
    ],
    content: tiptapDocFromBlocks(contentBlocks),
    editorProps: {
      attributes: {
        class: "pg-tiptap prose prose-sm max-w-none min-h-[220px] p-4 outline-none",
      },
      handleKeyDown: (_view, event) => {
        if (event.key === "/" && !event.metaKey && !event.ctrlKey && !event.altKey) {
          setSlashMenuOpen(true);
          setInsertMenuOpen(false);
          return false;
        }
        if (event.key === "Escape") {
          setSlashMenuOpen(false);
          setInsertMenuOpen(false);
          return false;
        }
        if (event.key === "Enter" || event.key === " ") {
          setSlashMenuOpen(false);
          setInsertMenuOpen(false);
        }
        return false;
      },
    },
    onUpdate: ({ editor: activeEditor }) => {
      if (isUndoingRef.current) return;
      const nextBlocks = tiptapDocToBlocks(activeEditor.getJSON());
      const nextBody = normalizeMarkdownEmphasis(contentBlocksToMarkdown(nextBlocks));
      setContentBlocks(nextBlocks);
      setBody(nextBody);
      setBodyEmpty(!nextBody.trim());
    },
  }, [slide.id]);

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

  const getCurrentBlocks = (): ContentBlock[] => {
    if (bodyEditMode === "markdown") {
      const normalizedBody = normalizeMarkdownEmphasis(body);
      if (normalizedBody === markdownModeEntryBody) return contentBlocks;
      return [{ id: "body", kind: "markdown", markdown: normalizedBody }];
    }
    return editor ? tiptapDocToBlocks(editor.getJSON()) : contentBlocks;
  };

  const getCurrentBody = (blocks: ContentBlock[] = getCurrentBlocks()): string => {
    if (bodyEditMode === "markdown") return normalizeMarkdownEmphasis(body);
    return normalizeMarkdownEmphasis(contentBlocksToMarkdown(blocks));
  };

  const getCurrentState = (): EditorState => {
    const currentBlocks = getCurrentBlocks();
    const currentBody = getCurrentBody(currentBlocks);
    return {
      headline,
      subhead,
      body: currentBody,
      contentBlocks: currentBlocks,
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
        top.body === state.body &&
        JSON.stringify(top.contentBlocks) === JSON.stringify(state.contentBlocks)
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
    setContentBlocks(state.contentBlocks);
    setBodyEditMode("canvas");
    setMarkdownModeEntryBody(state.body);
    editor?.commands.setContent(tiptapDocFromBlocks(state.contentBlocks), { emitUpdate: false });
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
      top.body === current.body &&
      JSON.stringify(top.contentBlocks) === JSON.stringify(current.contentBlocks)
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
  const [materialsOpen, setMaterialsOpen] = useState(true);

  // 保存当前编辑内容（不退出）
  const handleSave = async (options?: { quiet?: boolean }): Promise<SaveResult> => {
    const content = slide.content_json || {};
    const text = content.text_content || {};
    const originalHeadline = unescapeText(text.headline || "");
    const originalSubhead = unescapeText(text.subhead || "");
    const originalBody = normalizeBody(text.body);
    const originalBlocks = normalizeEditorContentBlocks(content, originalBody);
    const originalSpeakerNotes = unescapeText(content.speaker_notes || "");

    const currentBlocks = getCurrentBlocks();
    const currentBody = getCurrentBody(currentBlocks);
    setBody(currentBody);
    setContentBlocks(currentBlocks);
    setBodyEmpty(!currentBody || currentBody.trim() === "");
    if (bodyEditMode === "markdown") setMarkdownModeEntryBody(currentBody);

    const hasContentChange =
      headline !== originalHeadline ||
      subhead !== originalSubhead ||
      currentBody !== originalBody ||
      JSON.stringify(currentBlocks) !== JSON.stringify(originalBlocks) ||
      speakerNotes !== originalSpeakerNotes;

    const saveData = {
      page_num: slide.page_num,
      type: slide.type,
      section_title: content.section_title || "",
      text_content: { headline, subhead, body: currentBody },
      content_blocks: currentBlocks,
      speaker_notes: speakerNotes,
      visual_suggestion: content.visual_suggestion || "",
      visual_requirements: Array.isArray(content.visual_requirements) ? content.visual_requirements : [],
    };
    const originalVisualDesc = slide.visual_json?.visual_description ?? "";
    const hasVisualChange = visualDescription !== originalVisualDesc;

    setSaving(true);
    try {
      if (hasContentChange) {
        const updateResult = await updateSlideContent(projectId, slide.page_num, saveData, slide.id);
        const suggestions = Array.isArray(updateResult?.visual_directive_suggestions)
          ? updateResult.visual_directive_suggestions
          : [];
        setVisualDirectiveSuggestions(suggestions);
        markSlideStale?.(slide.id, "content");
        // 自动重分类提示（onSaved 中会重新加载 slides，这里只提示）
        if (updateResult?.type_changed && updateResult?.new_type) {
          const newTypeLabel = typeLabel[updateResult.new_type] || updateResult.new_type;
          onToast?.(`已自动优化为${newTypeLabel}页版式`, "info");
        }
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

  const applyVisualDirectiveSuggestion = async (suggestion: VisualDirectiveSuggestion) => {
    const currentBlocks = getCurrentBlocks();
    const currentBody = getCurrentBody(currentBlocks);
    const nextBody = normalizeMarkdownEmphasis(removeDirectiveLineFromMarkdown(currentBody, suggestion.original_text));
    const nextBlocks: ContentBlock[] = [{ id: "body", kind: "markdown", markdown: nextBody }];
    const nextVisualRequirements = mergeVisualRequirements(content.visual_requirements, suggestion);
    const nextVisualSuggestion = appendVisualSuggestion(content.visual_suggestion, suggestion);

    setSaving(true);
    try {
      await updateSlideContent(projectId, slide.page_num, {
        page_num: slide.page_num,
        type: slide.type,
        section_title: content.section_title || "",
        text_content: { headline, subhead, body: nextBody },
        content_blocks: nextBlocks,
        speaker_notes: speakerNotes,
        visual_suggestion: nextVisualSuggestion,
        visual_requirements: nextVisualRequirements,
      }, slide.id);
      setBody(nextBody);
      setContentBlocks(nextBlocks);
      setBodyEmpty(!nextBody.trim());
      setMarkdownModeEntryBody(nextBody);
      editor?.commands.setContent(tiptapDocFromBlocks(nextBlocks), { emitUpdate: false });
      setVisualDirectiveSuggestions((prev) => prev.filter((item) => item.original_text !== suggestion.original_text));
      markSlideStale?.(slide.id, "content");
      await onSaved?.();
      onToast?.("已移到画面要求", "success");
      onSystemLog?.(`用户将第 ${slide.page_num} 页的一条画面要求从正文移出`);
    } catch (err: any) {
      onToast?.("移动失败：" + (err.message || "未知错误"), "error");
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
      const nextBlocks = normalizeEditorContentBlocks(slide.content_json || {}, normalizeBody(newText.body));
      const newState: EditorState = {
        headline: unescapeText(newText.headline || ""),
        subhead: unescapeText(newText.subhead || ""),
        body: normalizeBody(newText.body),
        contentBlocks: nextBlocks,
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
      setContentBlocks(nextBlocks);
      setBodyEditMode("canvas");
      setMarkdownModeEntryBody(newState.body);
      setVisualDirectiveSuggestions((prev) =>
        prev.filter((suggestion) => newState.body.includes(String(suggestion.original_text || "").trim()))
      );
      editor?.commands.setContent(tiptapDocFromBlocks(nextBlocks), { emitUpdate: false });
      setSpeakerNotes(unescapeText(slide.content_json?.speaker_notes || ""));
    }
    // 同步 visual_description
    if (slide.visual_json !== prevVisualRef.current && !isUndoingRef.current) {
      prevVisualRef.current = slide.visual_json;
      setVisualDescription(slide.visual_json?.visual_description || "");
    }
  });

  // body 变更时同步空状态
  useEffect(() => {
    setBodyEmpty(!body || body.trim() === "");
  }, [body]);

  const handleEditorBlur = () => {
    if (!editor || bodyEditMode === "markdown") return;
    const nextBlocks = tiptapDocToBlocks(editor.getJSON());
    const nextBody = normalizeMarkdownEmphasis(contentBlocksToMarkdown(nextBlocks));
    setContentBlocks(nextBlocks);
    setBody(nextBody);
    setBodyEmpty(!nextBody.trim());
    handleBlurPushHistory();
  };

  const enterMarkdownMode = () => {
    const nextBlocks = editor ? tiptapDocToBlocks(editor.getJSON()) : contentBlocks;
    const nextBody = normalizeMarkdownEmphasis(contentBlocksToMarkdown(nextBlocks));
    setContentBlocks(nextBlocks);
    setBody(nextBody);
    setBodyEmpty(!nextBody.trim());
    setMarkdownModeEntryBody(nextBody);
    setBodyEditMode("markdown");
    setSlashMenuOpen(false);
    setInsertMenuOpen(false);
  };

  const enterCanvasMode = () => {
    const normalizedBody = normalizeMarkdownEmphasis(body);
    let nextBlocks = contentBlocks;
    if (normalizedBody !== markdownModeEntryBody) {
      nextBlocks = [{ id: "body", kind: "markdown", markdown: normalizedBody }];
      setContentBlocks(nextBlocks);
      setBody(normalizedBody);
      setBodyEmpty(!normalizedBody.trim());
    }
    editor?.commands.setContent(tiptapDocFromBlocks(nextBlocks), { emitUpdate: false });
    setMarkdownModeEntryBody(normalizedBody);
    setBodyEditMode("canvas");
    setSlashMenuOpen(false);
    setInsertMenuOpen(false);
  };

  const removeSlashTrigger = () => {
    if (!editor) return undefined;
    const { from } = editor.state.selection;
    let chain = editor.chain().focus();
    if (slashMenuOpen && from > 1 && editor.state.doc.textBetween(from - 1, from) === "/") {
      chain = chain.deleteRange({ from: from - 1, to: from });
    }
    return chain;
  };

  const closeInsertMenus = () => {
    setSlashMenuOpen(false);
    setInsertMenuOpen(false);
  };

  const runTextBlockCommand = (command: "paragraph" | "heading1" | "heading2" | "heading3" | "bullet" | "ordered" | "quote" | "code" | "divider") => {
    if (!editor) return;
    const chain = removeSlashTrigger();
    if (!chain) return;
    if (command === "heading1") chain.toggleHeading({ level: 1 }).run();
    if (command === "heading2") chain.toggleHeading({ level: 2 }).run();
    if (command === "heading3") chain.toggleHeading({ level: 3 }).run();
    if (command === "bullet") chain.toggleBulletList().run();
    if (command === "ordered") chain.toggleOrderedList().run();
    if (command === "quote") chain.toggleBlockquote().run();
    if (command === "code") chain.toggleCodeBlock().run();
    if (command === "divider") chain.setHorizontalRule().run();
    if (command === "paragraph") chain.setParagraph().run();
    closeInsertMenus();
  };

  const indentListItem = () => {
    editor?.chain().focus().sinkListItem("listItem").run();
  };

  const outdentListItem = () => {
    editor?.chain().focus().liftListItem("listItem").run();
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
  const ASSET_ROUTE_OPTIONS: AssetRoute[] = ["blend", "double_blend", "overlay"];
  const ASSET_ROUTE_HELP: Record<AssetRoute, { label: string; description: string; costNote?: string }> = {
    blend: {
      label: "智能融合",
      description: "把素材作为画面参考，融入整体风格、光影和构图，适合照片、场景和氛围图。",
    },
    double_blend: {
      label: "精修融合",
      description: "先融合进画面，再校准主体边缘、比例和关键细节，适合产品、人像或必须更准确的素材。",
      costNote: "每个组件会单独精修，生成更慢，也会消耗更多 credits。",
    },
    overlay: {
      label: "精确粘贴",
      description: "保留原图细节和比例，并放在可控位置，适合 Logo、截图、图表和必须原样呈现的素材。",
    },
  };
  const routeFromProcessMode = (processMode: any): AssetRoute | null => {
    const mode = String(processMode || "").toLowerCase();
    if (mode === "original") return "overlay";
    if (mode === "crop") return "double_blend";
    if (mode === "blend") return "blend";
    return null;
  };
  const pageReferenceItems = dedupeReferenceImages(slide.reference_images || []);
  const blendProjectAssetIds = visualAssetIds.filter((id: string, index: number, arr: string[]) =>
    !overlayAssetIds.has(id) && arr.indexOf(id) === index
  );
  const blendProjectAssets = blendProjectAssetIds.map((id: string) => {
    const asset = projectAssetById.get(id);
    return {
      id,
      asset,
      usage: visualAssetUsage[id],
      manual: manualVisualAssetSet.has(id),
      route: assetRouteModes[id] || routeFromProcessMode(asset?.process_mode) || (
        ["product", "material"].includes(String(asset?.asset_kind || "").toLowerCase())
          ? "double_blend"
          : "blend"
      ),
    };
  });
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
    resolveAssetUrl(API_BASE, asset?.overlay_url || asset?.url);

  const routeHelp = (route: string) =>
    ASSET_ROUTE_HELP[ASSET_ROUTE_OPTIONS.includes(route as AssetRoute) ? route as AssetRoute : "blend"];
  const routeLabel = (route: string) => routeHelp(route).label;
  const renderAssetRouteButton = (
    target: AssetRoute,
    options: {
      selected: boolean;
      busy: boolean;
      loading: boolean;
      onClick: () => void;
    }
  ) => {
    const help = routeHelp(target);
    const label = options.loading ? "切换中" : help.label;
    return (
      <div key={target} className="relative group/route inline-flex">
        <button
          type="button"
          disabled={options.busy}
          onClick={options.selected ? undefined : options.onClick}
          title={`${help.label}：${help.description}`}
          aria-label={`${help.label}：${help.description}`}
          aria-pressed={options.selected}
          className={`text-[11px] px-2 py-1 rounded border inline-flex items-center gap-1 ${
            options.selected
              ? "bg-slate-900 text-white border-slate-900"
              : "bg-white text-slate-600 border-slate-200 hover:bg-slate-50"
          } disabled:opacity-60`}
        >
          <span>{label}</span>
          {!options.loading && (
            <span
              aria-hidden="true"
              className={`inline-flex h-3.5 w-3.5 items-center justify-center rounded-full border text-[9px] leading-none ${
                options.selected ? "border-white/50 text-white/85" : "border-slate-300 text-slate-400"
              }`}
            >
              ?
            </span>
          )}
        </button>
        {!options.loading && (
          <div
            role="tooltip"
            className="pointer-events-none absolute right-0 top-full z-50 mt-1 hidden w-64 rounded-md bg-slate-900 px-2.5 py-2 text-[11px] leading-snug text-white shadow-lg group-hover/route:block group-focus-within/route:block"
          >
            <div className="font-semibold">{help.label}</div>
            <div className="mt-0.5 text-slate-200">{help.description}</div>
            {help.costNote && <div className="mt-1 text-amber-200">{help.costNote}</div>}
            <div className="absolute -top-1 right-5 h-2 w-2 rotate-45 bg-slate-900" />
          </div>
        )}
      </div>
    );
  };
  const renderRouteCostNote = (route: string) => {
    const help = routeHelp(route);
    if (!help.costNote) return null;
    return <div className="text-[11px] text-amber-700 mt-0.5">{help.costNote}</div>;
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
    if (mode === "original") return "overlay";
    if (mode === "crop") return "double_blend";
    const analysisRoute = String(ref?.asset_analysis?.asset_route_mode || ref?.asset_analysis?.route_mode || "").toLowerCase();
    if (analysisRoute === "original" || analysisRoute === "exact" || analysisRoute === "exact_overlay") return "overlay";
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
    if (analysis.source === "content_block") return "正文画面素材";
    const page = ref?.source_page_num || analysis.pptx_source_page_num;
    const groupIndex = analysis.asset_group_index;
    const groupSize = analysis.asset_group_size;
    if (analysis.asset_group_role === "parallel_page_reference_set" && page && groupIndex && groupSize) {
      return `原 PPT 第 ${page} 页图片组 ${groupIndex}/${groupSize}`;
    }
    if (page) return `原 PPT 第 ${page} 页参考图`;
    return "本页上传素材";
  };

  const updateContentBlockRouteForRef = (ref: any, route: AssetRoute): ContentBlock[] | null => {
    const blockId = String(ref?.asset_analysis?.content_block_id || "");
    if (!blockId) return null;
    const nextRoute = assetRouteToBlockRoute(route);
    const currentBlocks = editor ? tiptapDocToBlocks(editor.getJSON()) : contentBlocks;
    const matched = currentBlocks.some((block) => block.id === blockId || block.rendered_asset_id === ref.id);
    if (!matched) return null;
    const nextBlocks = currentBlocks.map((block) =>
      block.id === blockId || block.rendered_asset_id === ref.id
        ? { ...block, route_mode: nextRoute, rendered_asset_id: ref.id }
        : block
    );
    setContentBlocks(nextBlocks);
    setBody(normalizeMarkdownEmphasis(contentBlocksToMarkdown(nextBlocks)));
    editor?.commands.setContent(tiptapDocFromBlocks(nextBlocks), { emitUpdate: false });
    return nextBlocks;
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
            mode: "exact_cutout",
            usage_note: `${pageReferenceLabel(ref)}：原图保留`,
          },
        ]);
      } else {
        if (overlayAssetIds.has(refId)) {
          await updateSlideOverlayLayers(projectId, slide.id, currentLayers);
        }
        await updateReferenceImage(projectId, refId, { process_mode: routeProcessMode(route) });
      }
      const nextBlocks = updateContentBlockRouteForRef(ref, route);
      if (nextBlocks) {
        const nextBody = normalizeMarkdownEmphasis(contentBlocksToMarkdown(nextBlocks));
        await updateSlideContent(projectId, slide.page_num, {
          page_num: slide.page_num,
          type: slide.type,
          section_title: slide.content_json?.section_title || "",
          text_content: { headline, subhead, body: nextBody },
          content_blocks: nextBlocks,
          speaker_notes: speakerNotes,
          visual_suggestion: slide.content_json?.visual_suggestion || "",
        }, slide.id);
      }
      markSlideStale?.(slide.id, "visual");
      await onSaved?.();
      onToast?.(`已切换为${routeLabel(route)}`, "success");
      onSystemLog?.(`用户将第 ${slide.page_num} 页本页画面素材切换为${routeLabel(route)}`);
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
            mode: "exact_cutout",
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
  const materialCount = pageReferenceItems.length + blendProjectAssets.length + overlayProjectAssets.length;
  const contentBlockMaterialCount = pageReferenceItems.filter((ref: any) => ref?.asset_analysis?.source === "content_block").length;

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

      {/* 正文 */}
      <div className="mb-6">
        <div className="mb-1 flex items-center justify-between gap-2">
          <label className="text-xs text-gray-500 block font-medium">正文</label>
          <div className="pg-editor-mode-switch" role="group" aria-label="正文编辑模式">
            <button
              type="button"
              className={bodyEditMode === "canvas" ? "is-active" : ""}
              onClick={bodyEditMode === "canvas" ? undefined : enterCanvasMode}
            >
              画布
            </button>
            <button
              type="button"
              className={bodyEditMode === "markdown" ? "is-active" : ""}
              onClick={bodyEditMode === "markdown" ? undefined : enterMarkdownMode}
            >
              Markdown
            </button>
          </div>
        </div>
        <div className={`pg-rich-editor border border-gray-200 rounded focus-within:ring-2 focus-within:ring-blue-300 focus-within:border-transparent ${bodyEditMode === "markdown" ? "pg-rich-editor-source" : ""}`}>
          {bodyEditMode === "canvas" ? (
            <>
              <div className="pg-doc-toolbar flex flex-wrap items-center gap-1 px-2 py-1.5 border-b border-gray-100 bg-gray-50 rounded-t">
                <button
                  type="button"
                  onClick={() => editor?.chain().focus().toggleBold().run()}
                  className={`pg-tool-button font-bold ${editor?.isActive("bold") ? "is-active" : ""}`}
                  title="加粗 (Ctrl+B)"
                  aria-label="加粗"
                >
                  B
                </button>
                <button
                  type="button"
                  onClick={() => editor?.chain().focus().toggleItalic().run()}
                  className={`pg-tool-button italic ${editor?.isActive("italic") ? "is-active" : ""}`}
                  title="斜体 (Ctrl+I)"
                  aria-label="斜体"
                >
                  I
                </button>
                <button
                  type="button"
                  onClick={() => runTextBlockCommand("heading1")}
                  className={`pg-tool-button ${editor?.isActive("heading", { level: 1 }) ? "is-active" : ""}`}
                  title="一级标题"
                  aria-label="一级标题"
                >
                  H1
                </button>
                <button
                  type="button"
                  onClick={() => runTextBlockCommand("heading2")}
                  className={`pg-tool-button ${editor?.isActive("heading", { level: 2 }) ? "is-active" : ""}`}
                  title="二级标题"
                  aria-label="二级标题"
                >
                  H2
                </button>
                <button
                  type="button"
                  onClick={() => runTextBlockCommand("heading3")}
                  className={`pg-tool-button ${editor?.isActive("heading", { level: 3 }) ? "is-active" : ""}`}
                  title="三级标题"
                  aria-label="三级标题"
                >
                  H3
                </button>
                <div className="pg-toolbar-divider" />
                <button
                  type="button"
                  onClick={() => runTextBlockCommand("bullet")}
                  className={`pg-tool-button ${editor?.isActive("bulletList") ? "is-active" : ""}`}
                  title="项目列表"
                  aria-label="项目列表"
                >
                  •
                </button>
                <button
                  type="button"
                  onClick={() => runTextBlockCommand("ordered")}
                  className={`pg-tool-button ${editor?.isActive("orderedList") ? "is-active" : ""}`}
                  title="编号列表"
                  aria-label="编号列表"
                >
                  1.
                </button>
                <button
                  type="button"
                  onClick={outdentListItem}
                  className="pg-tool-button"
                  title="减少缩进"
                  aria-label="减少缩进"
                >
                  ←
                </button>
                <button
                  type="button"
                  onClick={indentListItem}
                  className="pg-tool-button"
                  title="增加缩进"
                  aria-label="增加缩进"
                >
                  →
                </button>
                <div className="pg-toolbar-divider" />
                <button
                  type="button"
                  onClick={() => runTextBlockCommand("quote")}
                  className={`pg-tool-button ${editor?.isActive("blockquote") ? "is-active" : ""}`}
                  title="引用"
                  aria-label="引用"
                >
                  “”
                </button>
                <button
                  type="button"
                  onClick={() => runTextBlockCommand("code")}
                  className={`pg-tool-button ${editor?.isActive("codeBlock") ? "is-active" : ""}`}
                  title="代码块"
                  aria-label="代码块"
                >
                  {"{}"}
                </button>
                <button
                  type="button"
                  onClick={() => runTextBlockCommand("divider")}
                  className="pg-tool-button"
                  title="分割线"
                  aria-label="分割线"
                >
                  —
                </button>
              </div>
              <div className="pg-doc-canvas relative">
                <button
                  type="button"
                  className="pg-block-plus"
                  title="插入内容"
                  aria-label="插入内容"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => {
                    editor?.chain().focus().run();
                    setInsertMenuOpen((open) => !open);
                    setSlashMenuOpen(false);
                  }}
                >
                  +
                </button>
                {(slashMenuOpen || insertMenuOpen) && (
                  <div className={`pg-insert-menu pg-insert-menu-basic ${insertMenuOpen ? "is-from-plus" : ""}`}>
                    <div className="pg-insert-menu-section">
                      <div className="pg-insert-menu-title">基础</div>
                      <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => runTextBlockCommand("paragraph")}>
                        <span>¶</span><b>正文</b>
                      </button>
                      <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => runTextBlockCommand("heading1")}>
                        <span>H1</span><b>一级标题</b>
                      </button>
                      <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => runTextBlockCommand("heading2")}>
                        <span>H2</span><b>二级标题</b>
                      </button>
                      <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => runTextBlockCommand("heading3")}>
                        <span>H3</span><b>三级标题</b>
                      </button>
                      <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => runTextBlockCommand("bullet")}>
                        <span>•</span><b>项目列表</b>
                      </button>
                      <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => runTextBlockCommand("ordered")}>
                        <span>1.</span><b>编号列表</b>
                      </button>
                      <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => runTextBlockCommand("quote")}>
                        <span>“</span><b>引用</b>
                      </button>
                      <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => runTextBlockCommand("code")}>
                        <span>{"{}"}</span><b>代码块</b>
                      </button>
                      <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => runTextBlockCommand("divider")}>
                        <span>—</span><b>分割线</b>
                      </button>
                    </div>
                  </div>
                )}
                {bodyEmpty && (
                  <div className="absolute top-4 left-4 text-gray-400 text-sm pointer-events-none select-none">
                    输入正文，或按 / 插入基础格式...
                  </div>
                )}
                <div onBlur={handleEditorBlur}>
                  <EditorContent editor={editor} />
                </div>
              </div>
            </>
          ) : (
            <textarea
              value={body}
              onChange={(e) => {
                setBody(e.target.value);
                setBodyEmpty(!e.target.value.trim());
              }}
              onKeyDown={(e) => applyMarkdownShortcut(e, setBody)}
              onBlur={handleBlurPushHistory}
              className="pg-markdown-source"
              placeholder="用 Markdown 编辑正文..."
            />
          )}
        </div>
      </div>

      {visualDirectiveSuggestions.length > 0 && (
        <div className="mb-6 rounded-lg border border-amber-200 bg-amber-50 p-3">
          <div className="mb-2 text-xs font-semibold text-amber-800">发现可能的画面要求</div>
          <div className="space-y-2">
            {visualDirectiveSuggestions.map((suggestion) => (
              <div key={suggestion.id || suggestion.original_text} className="rounded border border-amber-200 bg-white p-2">
                <div className="text-sm text-slate-700">{suggestion.original_text}</div>
                {suggestion.diagram_labels && suggestion.diagram_labels.length > 0 && (
                  <div className="mt-1 text-xs text-slate-500">图示标签：{suggestion.diagram_labels.join("、")}</div>
                )}
                <div className="mt-2 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => applyVisualDirectiveSuggestion(suggestion)}
                    disabled={saving}
                    className="text-xs rounded bg-amber-600 px-2.5 py-1.5 font-medium text-white hover:bg-amber-700 disabled:opacity-60"
                  >
                    移到画面要求
                  </button>
                  <button
                    type="button"
                    onClick={() => setVisualDirectiveSuggestions((prev) => prev.filter((item) => item.original_text !== suggestion.original_text))}
                    className="text-xs rounded border border-slate-200 bg-white px-2.5 py-1.5 text-slate-600 hover:bg-slate-50"
                  >
                    保留在正文
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

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

      {/* 本页画面素材 */}
      <div className="mb-6">
        <div className="flex items-center justify-between gap-2 mb-2">
          <div className="flex items-center gap-1.5">
          <label className="text-xs text-gray-500 font-medium">本页画面素材</label>
          <span className="text-[11px] text-slate-500 bg-slate-100 rounded-full px-2 py-0.5">
            {materialCount} 个{contentBlockMaterialCount ? ` · ${contentBlockMaterialCount} 个来自正文` : ""}
          </span>
          {imageReferenceInputCount >= 8 && (
            <span className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded-full px-2 py-0.5">
              {imageReferenceBadgeText}
            </span>
          )}
          <div className="relative group">
            <span className="text-xs text-gray-400 cursor-help">ⓘ</span>
            <div className="absolute left-1/2 -translate-x-1/2 bottom-full mb-1 hidden group-hover:block w-64 bg-gray-800 text-white text-[11px] rounded-lg px-3 py-2 shadow-lg z-50">
              <p className="mb-1">这里列出本页会进入画面的素材。</p>
              <p>图片、图表和正文生成的结构图都可以在这里选择融合或原样保留。</p>
              <div className="absolute left-1/2 -translate-x-1/2 top-full w-2 h-2 bg-gray-800 rotate-45" />
            </div>
          </div>
          </div>
          <button
            type="button"
            onClick={() => setMaterialsOpen((open) => !open)}
            className="text-xs text-slate-600 border border-slate-200 rounded px-2.5 py-1 hover:bg-slate-50"
          >
            {materialsOpen ? "收起" : "展开"}
          </button>
        </div>
        {materialsOpen && (
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 mb-2">
          <div className="flex flex-col gap-2">
            {pageReferenceItems.map((ref: any) => {
              const route = pageReferenceRoute(ref);
              const refId = String(ref.id);
              const refUrl = resolveAssetUrl(API_BASE, ref.url);
              return (
                <div key={ref.id} className="flex items-center gap-3 rounded-md bg-white border border-slate-200 p-2">
                  <div className="relative group flex-shrink-0">
                    <img
                      src={refUrl}
                      alt="ref"
                      className="w-14 h-14 rounded object-cover border cursor-pointer"
                      onClick={() => onImageClick?.(refUrl)}
                      onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                    />
                    <button
                      onClick={async () => {
                        try {
                          await deleteReferenceImage(projectId, ref.id);
                          markSlideStale?.(slide.id, "visual");
                          onSaved?.();
                          onToast?.("已删除", "success");
                          onSystemLog?.(`用户删除了第 ${slide.page_num} 页的本页画面素材`);
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
                    <div className="text-xs font-semibold text-slate-700 truncate">{ref.asset_name || ref.asset_analysis?.subject || "本页画面素材"}</div>
                    <div className="text-[11px] text-slate-500 mt-0.5">{pageReferenceLabel(ref)} · {routeLabel(route)}</div>
                    {renderRouteCostNote(route)}
                  </div>
                  <div className="flex gap-1">
                    {ASSET_ROUTE_OPTIONS.map((target) =>
                      renderAssetRouteButton(target, {
                        selected: route === target,
                        busy: assetRouteLoading === refId,
                        loading: assetRouteLoading === refId && route !== target,
                        onClick: () => setPageReferenceRoute(ref, target),
                      })
                    )}
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
                    {renderRouteCostNote(route)}
                  </div>
                  <div className="flex gap-1">
                    {ASSET_ROUTE_OPTIONS.map((target) =>
                      renderAssetRouteButton(target, {
                        selected: route === target,
                        busy: assetRouteLoading === id,
                        loading: assetRouteLoading === id && route !== target,
                        onClick: () => setPageAssetRoute(id, target),
                      })
                    )}
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
                    {ASSET_ROUTE_OPTIONS.map((target) =>
                      renderAssetRouteButton(target, {
                        selected: target === "overlay",
                        busy: assetRouteLoading === id,
                        loading: assetRouteLoading === id && target !== "overlay",
                        onClick: () => setPageAssetRoute(id, target),
                      })
                    )}
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
        )}
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
                    usage_note: "用户上传的本页画面素材",
                  });
                }
                markSlideStale?.(slide.id, "visual");
                await onSaved?.();
                onToast?.(files.length > 1 ? `已加入 ${files.length} 个本页画面素材` : "已加入本页画面素材", "success");
                onSystemLog?.(`用户为第 ${slide.page_num} 页上传了 ${files.length} 个本页画面素材`);
              } catch (err: any) {
                onToast?.("上传失败：" + (err.message || "未知错误"), "error");
              }
            };
            input.click();
          }}
          className="text-xs bg-gray-100 text-gray-600 px-2 py-1 rounded hover:bg-gray-200"
        >
          + 添加画面素材
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
