import { Fragment, useEffect, useRef, useState } from "react";
import type { CSSProperties, MouseEvent as ReactMouseEvent, SyntheticEvent } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";
import TurndownService from "turndown";
import { tables as gfmTables, strikethrough as gfmStrikethrough } from "turndown-plugin-gfm";

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

// 把 markdown 转成纯文本，用于 title tooltip
const mdToPlainText = (md: string): string => {
  return normalizeMarkdownEmphasis(md || "")
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/\*(.+?)\*/g, "$1")
    .replace(/\*\*/g, "")
    .replace(/__/g, "")
    .replace(/\[(.+?)\]\(.+?\)/g, "$1")
    .replace(/`(.+?)`/g, "$1")
    .replace(/~~(.+?)~~/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^[-*+]\s+/gm, "")
    .replace(/^\d+\.\s+/gm, "")
    .replace(/^>\s+/gm, "")
    .trim();
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
  buildWorkflowState,
  getGuidanceText as getWorkflowGuidanceText,
  getPrimaryActionKey,
  getSecondaryActionKeys,
} from "./workflow";

import {
  API_BASE,
  fetchProjects,
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
  uploadFile,
  fetchReferenceImages,
  deleteReferenceImage,
  updateReferenceImage,
  updateReferenceImageMode,
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
  | "start_generation"
  | "retry_failed";

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

interface ChatMessage {
  role: "user" | "agent" | "system";
  content: string;
  action?: string;
  positioning?: PositioningData;
  topic?: string;
  nextAction?: AgentNextAction;
  agentRole?: "content" | "visual" | "finetune";
  loading?: boolean;
  id?: string;
  runId?: string;
  hasStyleProposal?: boolean;
  styleProposals?: StyleProposal[];
  attachments?: ChatAttachment[];
}

interface UiAction {
  key: string;
  label: string;
  onClick?: () => void;
  href?: string;
  variant?: "primary" | "secondary" | "danger" | "link";
  disabled?: boolean;
}

const CONTENT_PLAN_TIMEOUT_MS = 300_000; // 内容规划 LLM 调用预留 5 分钟
const VISUAL_PROMPT_MAX_POLL_ERRORS = 5;
const GENERATION_MAX_POLL_ERRORS = 5;
const IMAGE_URL_SESSION_KEY = Date.now();

function isRunActive(run: any) {
  return !!run && (run.status === "queued" || run.status === "running");
}

function isTransientRunMessage(message: ChatMessage) {
  if (message.loading) return true;
  if (message.role !== "agent") return false;
  const content = message.content || "";
  return (
    /正在(?:启动|生成|构建|重新生成|准备|处理)/.test(content) &&
    /(?:第\s*\d+\s*\/\s*\d+\s*页|\d+\s*\/\s*\d+\s*(?:页|套)完成)/.test(content)
  );
}

function sanitizeChatHistory(messages: ChatMessage[]) {
  return (messages || []).filter((m) => !isTransientRunMessage(m));
}

function cleanProgressMessage(message?: string) {
  if (!message) return "";
  return message
    .replace(/[🧠🚀⏳✅📝🎨]/g, "")
    .replace(/（?批次\s*\d+\s*\/\s*\d+）?/g, "")
    .replace(/\d+\s*\/\s*\d+\s*页完成/g, "")
    .replace(/\.\.\./g, "")
    .replace(/……/g, "")
    .replace(/\s+/g, " ")
    .trim();
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
  const message = cleanProgressMessage(run.message) || fallback;
  const unit = run.kind === "style_proposal" ? "套" : "页";
  return total > 0 ? `${message}：${completed} / ${total} ${unit}完成` : message;
}

function workflowProgressText(status: any) {
  const progress = status?.progress;
  if (!progress) return runProgressText(status?.active_run);
  const total = Math.max(0, Number(progress.total ?? progress.total_pages ?? 0));
  const current = Math.min(total || Number(progress.current ?? progress.current_page ?? 0), Math.max(0, Number(progress.current ?? progress.current_page ?? 0)));
  const unit = progress.unit || (progress.kind === "style_proposal" ? "套" : "页");
  const message = cleanProgressMessage(progress.message) || progress.label || "任务处理中";
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
  return { current, total, failed, unit, percent };
}

function getSlideImageUrl(imagePath: string, status?: string, cacheKey?: string | number) {
  const base = `${API_BASE}${imagePath.replace("./outputs", "/outputs")}`;
  const version = cacheKey ?? `${status || "image"}-${IMAGE_URL_SESSION_KEY}`;
  const cacheBuster = `?v=${encodeURIComponent(String(version))}`;
  return `${base}${cacheBuster}`;
}

function shouldShowLogoOverlay(slide: any) {
  const policy = slide?.visual_json?.logo_policy;
  if (policy && typeof policy.show_logo === "boolean") return policy.show_logo;
  const pageType = String(slide?.visual_json?.type || slide?.type || "content").toLowerCase();
  const layout = String(slide?.visual_json?.layout || "").toLowerCase();
  if (pageType === "cover" || pageType === "ending") return true;
  if (pageType === "hero" || pageType === "quote") return false;
  if (layout === "hero" || layout === "content_hero") return false;
  return true;
}

function logoOverlayPosition(anchor?: string | null) {
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

function SlideImageWithLogo({
  slide,
  src,
  logo,
  alt,
  className,
  imgClassName,
  onClick,
  onError,
}: {
  slide: any;
  src: string;
  logo?: any;
  alt: string;
  className?: string;
  imgClassName?: string;
  onClick?: (e: ReactMouseEvent<HTMLDivElement>) => void;
  onError?: (e: SyntheticEvent<HTMLImageElement>) => void;
}) {
  const showLogo = logo && shouldShowLogoOverlay(slide);
  const slideType = String(slide?.visual_json?.type || slide?.type || "content").toLowerCase();
  const policy = slide?.visual_json?.logo_policy || {};
  const largeLogo = policy.scale === "large" || slideType === "cover" || slideType === "ending";
  const logoWidth = largeLogo ? "clamp(80px, 18%, 240px)" : "clamp(28px, 5.2%, 84px)";
  return (
    <div className={`relative ${className || ""}`} onClick={onClick}>
      <img src={src} alt={alt} className={imgClassName || "w-full h-full object-cover"} onError={onError} />
      {showLogo && (
        <img
          src={`${API_BASE}${logo.overlay_url || logo.url}`}
          alt=""
          className="absolute z-10 object-contain pointer-events-none select-none"
          style={{
            ...logoOverlayPosition(policy.placement || logo.logo_anchor),
            width: logoWidth,
            maxHeight: largeLogo ? "136px" : "48px",
          }}
        />
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
  const [slides, setSlides] = useState<Slide[]>([]);
  const [imageRefreshMap, setImageRefreshMap] = useState<Record<string, number>>({});
  const [slidesHistory, setSlidesHistory] = useState<Slide[][]>([]);
  const [slidesHistoryIndex, setSlidesHistoryIndex] = useState(-1);
  const isGlobalUndoingRef = useRef(false);
  const [operatingProjectId, setOperatingProjectId] = useState<string | null>(null);
  const {
    workflowStatus: projectStatus,
    setWorkflowStatus: setProjectStatus,
    refreshWorkflowStatus,
    activeRun,
    hasActiveRun,
  } = useProjectWorkflow(selectedProject?.id || null);
  const currentProjectStatus = projectStatus?.project_id === selectedProject?.id ? projectStatus : null;

  // 追踪当前活跃的聊天流属于哪个项目/角色，防止状态跳到别的窗口
  const activeChatProjectIdRef = useRef<string | null>(null);
  const activeChatRoleRef = useRef<string | null>(null);

  // 保存最近一次聊天的请求参数，用于切回来后自动恢复
  const pendingChatRef = useRef<{
    projectId: string;
    message: string;
    history: any[];
    pageContext: any;
    agentRole: string;
  } | null>(null);
  const chatInProgressRef = useRef(false);

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
  const [showPrototypePreview, setShowPrototypePreview] = useState(true);
  const [referenceImages, setReferenceImages] = useState<any[]>([]);
  const [templatePages, setTemplatePages] = useState<any[]>([]);
  const [showTemplateRecommender, setShowTemplateRecommender] = useState(false);

  // 主舞台折叠状态：默认折叠以节省空间
  const [styleBarExpanded, setStyleBarExpanded] = useState(false);
  const [assetsBarExpanded, setAssetsBarExpanded] = useState(false);

  const [chatInput, setChatInput] = useState("");
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

  const markSlideStale = (slideId: string, type: "content" | "visual" | "image") => {
    setStaleMap((prev) => ({
      ...prev,
      [slideId]: { ...prev[slideId], [type]: true },
    }));
  };

  const clearTransientProjectState = () => {
    setProjectStatus(null);
    setContentPlanProgress(null);
    setOperatingProjectId(null);
    generationLoadingIdRef.current = null;
    setReferenceImages([]);
    setTemplatePages([]);
    setSlides([]);
    setStaleMap({});
    setSelectedPages(new Set());
    setEditingSlide(null);
    setAgentMode("global");
    setContentPlanSnapshot([]);
    setStyleProposalsInChat([]);
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
  const [pendingFinetuneAttachmentsMap, setPendingFinetuneAttachmentsMap] = useState<Record<string, ChatAttachment[]>>({});
  const [uploadingDoc, setUploadingDoc] = useState(false);
  const [uploadingStyleRef, setUploadingStyleRef] = useState(false);
  const [uploadingLogo, setUploadingLogo] = useState(false);
  const [uploadingVisualAsset, setUploadingVisualAsset] = useState(false);
  const [uploadingTemplate, setUploadingTemplate] = useState(false);
  const [editingMessageIndex, setEditingMessageIndex] = useState<number | null>(null);
  const [editMessageContent, setEditMessageContent] = useState("");
  const [isDragging, setIsDragging] = useState(false);
  const [documentsExpanded, setDocumentsExpanded] = useState(false);
  const [dragSlideId, setDragSlideId] = useState<string | null>(null);
  const [dragOverSlideId, setDragOverSlideId] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const isConfirmingRef = useRef(false);
  const contentPlanPollTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const contentPlanProgressIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const contentPlanCheckIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const visualPromptIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const loadedChatProjectIdRef = useRef<string | null>(null);
  const contentPlanStopTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const loadingProjectIdRef = useRef<string | null>(null);
  const softLockWarnedRef = useRef(false);
  const generationLoadingIdRef = useRef<string | null>(null);
  const locallyHandledRunIdsRef = useRef<Set<string>>(new Set());
  const [contentPlanProgress, setContentPlanProgress] = useState<any>(null);
  const currentContentPlanProgress = contentPlanProgress?.project_id === selectedProject?.id ? contentPlanProgress : null;
  const [styleProposalsLoading] = useState(false);
  const [, setShowStylePanel] = useState(false);
  const [currentAgentRole, setCurrentAgentRole] = useState<"content" | "visual" | "finetune">("content");
  const currentAgentRoleRef = useRef(currentAgentRole);
  // 三 Agent 聊天历史隔离（必须在 currentAgentRole 之后定义）
  const [contentChatHistory, setContentChatHistory] = useState<ChatMessage[]>([]);
  const [visualChatHistory, setVisualChatHistory] = useState<ChatMessage[]>([]);
  // 单页微调：按 slideId 隔离的聊天历史
  const [finetuneChatHistoryMap, setFinetuneChatHistoryMap] = useState<Record<string, ChatMessage[]>>({});
  // 单页微调：当前选中的目标页
  const [finetuneTargetSlideId, setFinetuneTargetSlideId] = useState<string | null>(null);
  // 单页微调：各页的历史版本数据 { slideId: Version[] }
  const [slideVersionsMap, setSlideVersionsMap] = useState<Record<string, any[]>>({});
  // 计算当前活跃的聊天历史
  const chatMessages = currentAgentRole === "content"
    ? contentChatHistory
    : currentAgentRole === "visual"
    ? visualChatHistory
    : (finetuneTargetSlideId ? (finetuneChatHistoryMap[finetuneTargetSlideId] || []) : []);
  // 设置当前 Agent 的聊天历史
  const setActiveChatMessages = (updater: React.SetStateAction<ChatMessage[]>) => {
    if (currentAgentRole === "content") {
      setContentChatHistory(updater);
    } else if (currentAgentRole === "visual") {
      setVisualChatHistory(updater);
    } else if (finetuneTargetSlideId) {
      setFinetuneChatHistoryMap((prev) => {
        const current = prev[finetuneTargetSlideId] || [];
        const next = typeof updater === "function" ? updater(current) : updater;
        return { ...prev, [finetuneTargetSlideId]: next };
      });
    }
  };
  const getChatStorageKey = (projectId: string, role: "content" | "visual") =>
    `ppt_god_chat_${role}_${projectId}`;
  const appendStoredChatMessage = (projectId: string, role: "content" | "visual", message: ChatMessage) => {
    try {
      const key = getChatStorageKey(projectId, role);
      const existing = localStorage.getItem(key);
      const parsed = existing ? sanitizeChatHistory(JSON.parse(existing)) : [];
      localStorage.setItem(key, JSON.stringify(sanitizeChatHistory([...parsed, message])));
    } catch (err) {
      console.warn("Persist background chat message failed:", err);
    }
  };
  const appendProjectChatMessage = (projectId: string, role: "content" | "visual", message: ChatMessage) => {
    const normalized = { ...message, agentRole: role };
    if (selectedProjectIdRef.current === projectId && currentAgentRoleRef.current === role) {
      if (role === "content") {
        setContentChatHistory((prev) => [...prev, normalized]);
      } else {
        setVisualChatHistory((prev) => [...prev, normalized]);
      }
      return;
    }
    appendStoredChatMessage(projectId, role, normalized);
  };
  const updateStoredChatMessages = (
    projectId: string,
    role: "content" | "visual",
    updater: (messages: ChatMessage[]) => ChatMessage[]
  ) => {
    try {
      const key = getChatStorageKey(projectId, role);
      const existing = localStorage.getItem(key);
      const parsed = existing ? sanitizeChatHistory(JSON.parse(existing)) : [];
      localStorage.setItem(key, JSON.stringify(sanitizeChatHistory(updater(parsed))));
    } catch (err) {
      console.warn("Update background chat messages failed:", err);
    }
  };
  const updateProjectChatMessages = (
    projectId: string,
    role: "content" | "visual",
    updater: (messages: ChatMessage[]) => ChatMessage[]
  ) => {
    if (selectedProjectIdRef.current === projectId && currentAgentRoleRef.current === role) {
      if (role === "content") {
        setContentChatHistory(updater);
      } else {
        setVisualChatHistory(updater);
      }
      return;
    }
    updateStoredChatMessages(projectId, role, updater);
  };
  // 如果视觉总监聊天记录为空，自动添加开场引导语
  const ensureVisualGreetingIfNeeded = () => {
    if (visualChatHistory.length === 0) {
      const hasAssets = referenceImages.length > 0;
      const assetDesc = [
        referenceImages.find((r) => r.role === "logo") ? "品牌 Logo" : "",
        referenceImages.filter((r) => r.role === "visual_asset").length > 0 ? `${referenceImages.filter((r) => r.role === "visual_asset").length}个核心资产` : "",
        referenceImages.filter((r) => r.role === "style_ref").length > 0 ? `${referenceImages.filter((r) => r.role === "style_ref").length}张风格参考` : "",
        referenceImages.find((r) => r.role === "template") ? "版式模板" : "",
      ].filter(Boolean).join("、");
      const directorMsg = hasAssets
        ? `我是视觉总监。已收到你上传的设计素材（${assetDesc}）。\n\n👉 如果你还想补充素材，请继续上传；如果已经齐了，点击下方「开始生成」按钮，我会立即基于这些素材制定风格方案。`
        : "我是视觉总监。你可以按参考强度从高到低上传素材：品牌 Logo、核心资产、风格参考、版式模板。\n\n👉 Logo 默认作为预览/PPTX 角标叠加；核心资产会按页面内容进入生成；风格参考只学习视觉气质；版式模板只参考页面结构。没有素材也可以直接生成。";
      setVisualChatHistory([{ role: "agent", content: directorMsg, agentRole: "visual" }]);
    }
  };
  // 如果内容总监聊天记录为空，自动添加开场引导语
  const ensureContentGreetingIfNeeded = () => {
    if (contentChatHistory.length === 0) {
      setContentChatHistory([{ role: "agent", content: "内容总监已介入。你可以继续调整内容规划。", agentRole: "content" }]);
    }
  };
  // 为指定 slideId 的微调聊天添加开场引导（仅首次）
  const ensureFinetuneGreetingForSlide = (slideId: string) => {
    setFinetuneChatHistoryMap((prev) => {
      if (prev[slideId] && prev[slideId].length > 0) return prev;
      return {
        ...prev,
        [slideId]: [{ role: "agent", content: "已选中此页。直接写修改要求即可，我会把当前页图片和参考图一起发给模型生成新版本。", agentRole: "finetune" }],
      };
    });
  };
  const [contentPlanConfirmed, setContentPlanConfirmed] = useState(false);
  const [contentPlanSnapshot, setContentPlanSnapshot] = useState<Slide[]>([]);
  const [confirmingProjectId, setConfirmingProjectId] = useState<string | null>(null);
  const contentPlanSnapshotRef = useRef(contentPlanSnapshot);
  const contentPlanConfirmedRef = useRef(contentPlanConfirmed);
  const [styleProposalsInChat, setStyleProposalsInChat] = useState<StyleProposal[]>([]);
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
  const chatInputRef = useRef<HTMLTextAreaElement>(null);

  // Toast 系统
  const showToast = (message: string, type: ToastItem["type"] = "info") => {
    const id = Math.random().toString(36).slice(2);
    setToasts((prev) => [...prev, { id, message, type }]);
  };
  const removeToast = (id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
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
  const [rightWidth, setRightWidth] = useState(400);
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
        setRightWidth(Math.max(320, Math.min(600, resizeStartWidth.current - dx)));
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

  const loadProjects = async () => {
    try {
      const data = await fetchProjects();
      setProjects(data);
      const currentSelectedId = selectedProjectIdRef.current;
      if (currentSelectedId) {
        const updated = data.find((p: Project) => p.id === currentSelectedId);
        if (updated) {
          setSelectedProject((prev) => {
            if (!prev || prev.id !== currentSelectedId) return prev;
            if (
              updated.status !== prev.status ||
              updated.title !== prev.title ||
              updated.content_plan_confirmed !== prev.content_plan_confirmed ||
              updated.completed_slides !== prev.completed_slides ||
              JSON.stringify(updated.selected_style) !== JSON.stringify(prev.selected_style) ||
              JSON.stringify(updated.style_proposal) !== JSON.stringify(prev.style_proposal)
            ) {
              return updated;
            }
            return prev;
          });
        }
      }
    } catch (err: any) {
      showToast("加载项目列表失败：" + (err.message || "网络错误"), "error");
    }
  };

  const loadSlides = async (projectId: string) => {
    try {
      const data = await fetchSlides(projectId);
      if (loadingProjectIdRef.current !== projectId) return;
      setSlides(data);
      // 软锁定检测：如果当前在视觉总监阶段，且内容发生了变化
      if (currentAgentRoleRef.current === "visual" && contentPlanSnapshotRef.current.length > 0 && contentPlanConfirmedRef.current) {
        const hasChanged = data.some((s: Slide) => {
          const snap = contentPlanSnapshotRef.current.find((cs) => cs.page_num === s.page_num);
          if (!snap) return true;
          // 对比完整 content_json，而不只是 text_content
          return JSON.stringify(snap.content_json || {}) !== JSON.stringify(s.content_json || {});
        });
        if (hasChanged) {
          // 重置确认状态，让用户可以重新确认
          setContentPlanConfirmed(false);
          // 只提示一次，避免重复
          if (!softLockWarnedRef.current) {
            softLockWarnedRef.current = true;
            setVisualChatHistory((prev) => [
              ...prev,
              {
                role: "agent",
                content: "⚠️ 检测到内容已变动。确认条已重新开启，你可以确认后请视觉总监重新提案。",
                agentRole: "visual",
              },
            ]);
          }
        }
      }
      return data;
    } catch (err: any) {
      showToast("加载页面列表失败：" + (err.message || "网络错误"), "error");
      return [];
    }
  };

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
      setSlides(JSON.parse(JSON.stringify(targetSlides)));
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
      showToast("加载项目状态失败：" + (err.message || "网络错误"), "error");
    }
  };

  const loadReferenceImages = async (projectId: string) => {
    try {
      const data = await fetchReferenceImages(projectId);
      if (loadingProjectIdRef.current !== projectId) return;
      setReferenceImages(data || []);
    } catch (err: any) {
      showToast("加载参考素材失败：" + (err.message || "网络错误"), "error");
    }
  };

  const loadDocuments = async (projectId: string) => {
    try {
      const data = await fetchDocuments(projectId);
      if (loadingProjectIdRef.current !== projectId) return;
      setDocuments(data || []);
    } catch (err: any) {
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
      showToast("加载模板页面失败：" + (err.message || "未知错误"), "error");
      setTemplatePages([]);
    }
  };

  // 启动内容规划生成并轮询进度（复用于"直接生成"按钮和 Agent regenerate_plan）
  const startContentPlanPoll = async (projectId: string, topic: string, source: "button" | "agent" = "button", pageCount?: number) => {
    if (operatingProjectId === projectId) return;
    // 记录旧 slides 的 ID，用于区分"旧内容还在"和"新生成完成"
    const previousSlides = await loadSlides(projectId);
    const previousSlideIds = previousSlides.map((s: any) => s.id).sort().join(",");
    const loadingId = `cp-${Date.now()}`;
    updateProjectChatMessages(projectId, "content", (prev) => [
      ...prev,
      ...(source === "button" ? [{ role: "user" as const, content: "直接生成" }] : []),
      { role: "agent" as const, content: "⏳ 正在启动内容规划生成...", agentRole: "content", loading: true, id: loadingId },
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
      const result = await generateContentPlan(projectId, topic, pageCount);
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
          if (workflow?.last_run?.kind === "content_plan" && workflow.last_run.status === "failed" && !isRunActive(workflow.active_run)) {
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
              },
            ]);
            setContentPlanSnapshot(currentSlides);
          }
        } catch (e) {
          console.warn("Content plan check poll error:", e);
        }
      }, 2000);
      contentPlanCheckIntervalRef.current = checkInterval;
    } catch (err: any) {
      setOperatingProjectId(null);
      setContentPlanProgress(null);
      removeLoadingMsg();
      updateProjectChatMessages(projectId, "content", (prev) => [
        ...prev,
        {
          role: "agent",
          content: "❌ 内容规划生成失败：" + (err.message || "未知错误") + "\n\n👉 解决方法：\n1. 直接告诉我你的主题，我会重新为你生成\n2. 检查网络后刷新页面重试\n3. 也可以尝试缩减主题范围，或分多次生成",
          agentRole: "content",
        },
      ]);
    }
  };


  // 页面加载时从 localStorage 恢复上次选中的项目，Agent 角色由项目状态推断
  useEffect(() => {
    const savedProjectId = localStorage.getItem("ppt_god_last_project_id");
    loadProjects().then(() => {
      if (savedProjectId) {
        // 延迟到项目列表加载完成后再选中
        setTimeout(() => {
          setProjects((prev) => {
            const target = prev.find((p) => p.id === savedProjectId);
            if (target) {
              setSelectedProject(target);
              // 根据项目已有状态推断 Agent 角色和确认状态
              const isPlanConfirmed = !!(target as any).content_plan_confirmed;
              setContentPlanConfirmed(isPlanConfirmed);
              if (target.selected_style || isPlanConfirmed) {
                setCurrentAgentRole("visual");
              } else {
                setCurrentAgentRole("content");
              }
            }
            return prev;
          });
        }, 0);
      }
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
    if (selectedProject) {
      loadingProjectIdRef.current = selectedProject.id;
      setProjectStatus(null);
      setContentPlanProgress(null);
      setReferenceImages([]);
      setTemplatePages([]);
      setSlides([]);
      setStaleMap({});
      setStyleProposalsInChat([]);
      generationLoadingIdRef.current = null;
      loadSlides(selectedProject.id);
      loadStatus(selectedProject.id);
      loadReferenceImages(selectedProject.id);
      loadDocuments(selectedProject.id);
      loadTemplatePages(selectedProject.id);
      setSelectedPages(new Set());
      setThinkingContent("");
      setThinkingExpanded(false);
      setPendingAttachments([]);

      if (loadedChatProjectIdRef.current !== selectedProject.id) {
        // 首次选中该项目（含页面重新加载后）：尝试从 localStorage 恢复聊天历史
        const savedContentChat = localStorage.getItem(`ppt_god_chat_content_${selectedProject.id}`);
        const savedVisualChat = localStorage.getItem(`ppt_god_chat_visual_${selectedProject.id}`);
        try {
          setContentChatHistory(savedContentChat ? sanitizeChatHistory(JSON.parse(savedContentChat)) : []);
        } catch {
          setContentChatHistory([]);
        }
        try {
          setVisualChatHistory(savedVisualChat ? sanitizeChatHistory(JSON.parse(savedVisualChat)) : []);
        } catch {
          setVisualChatHistory([]);
        }
        loadedChatProjectIdRef.current = selectedProject.id;
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
    if (contentChatHistory.length === 0) {
      localStorage.removeItem(`ppt_god_chat_content_${selectedProject.id}`);
    } else {
      localStorage.setItem(`ppt_god_chat_content_${selectedProject.id}`, JSON.stringify(sanitizeChatHistory(contentChatHistory)));
    }
  }, [contentChatHistory, selectedProject?.id]);
  useEffect(() => {
    if (!selectedProject) return;
    if (visualChatHistory.length === 0) {
      localStorage.removeItem(`ppt_god_chat_visual_${selectedProject.id}`);
    } else {
      localStorage.setItem(`ppt_god_chat_visual_${selectedProject.id}`, JSON.stringify(sanitizeChatHistory(visualChatHistory)));
    }
  }, [visualChatHistory, selectedProject?.id]);

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
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        loadProjects();
        if (selectedProject) {
          loadSlides(selectedProject.id);
          loadStatus(selectedProject.id);
        }
        // 如果聊天流在后台被浏览器中断，自动静默重试
        // 不依赖 chatLoading state（它可能已被 finally 重置），直接检查 pendingChatRef
        if (!chatInProgressRef.current && pendingChatRef.current) {
          const pending = pendingChatRef.current;
          if (selectedProject?.id === pending.projectId && currentAgentRole === pending.agentRole) {
            setChatLoading(true); // 恢复 loading 状态
            setTimeout(() => {
              handleSendChat(pending.message, pending.history as any, true);
            }, 300);
          }
        }
      }
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => document.removeEventListener("visibilitychange", onVisibilityChange);
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
        setVisualChatHistory((prevMsgs) => prevMsgs.filter((m) => m.id !== loadingId && m.runId !== prevRun.runId));
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
      const completedCount = slides.filter((s) => s.status === "completed").length;
      const ok = currentStatus === "completed" || currentStatus === "prototype_ready" || currentStatus === "prompt_ready" || currentStatus === "visual_ready" || currentStatus === "planning";
      setVisualChatHistory((prevMsgs) => [
        ...prevMsgs.filter((m) => m.id !== loadingId && m.runId !== prevRun.runId),
        {
          role: "agent",
          content: ok
            ? (prevRun.kind === "visual_prompts"
                ? "✅ 画面设计已完成：每页画面描述和生图 Prompt 已生成。\n\n👉 下一步：先「打样确认」生成 1-3 张预览效果；满意后再「全量生成」所有页面。"
                : currentStatus === "completed"
                  ? `✅ 全量生成已完成，共 ${completedCount} 页。\n\n👉 下一步：点击上方「下载 PPTX」获取最终文件；需要调整时可选中页面重新生成。`
                  : "✅ 当前任务已结束，页面状态已更新。\n\n👉 下一步：请根据顶部状态栏继续操作。")
            : `⚠️ 当前任务已结束（状态：${currentStatus || "未知"}），请检查页面状态。`,
          agentRole: "visual",
        },
      ]);
    }
    // 兼容旧项目状态：只有同一个项目从 generating 变为 completed 才触发
    if (prev.projectId === pid && prev.status === "generating" && currentStatus === "completed") {
      const completedCount = slides.filter((s) => s.status === "completed").length;
      const loadingId = generationLoadingIdRef.current;
      generationLoadingIdRef.current = null;
      setVisualChatHistory((prevMsgs) => [
        ...prevMsgs.filter((m) => m.id !== loadingId),
        { role: "system", content: `批量生成完成，共 ${completedCount} 页` },
        {
          role: "agent",
          content: "🎉 全量生成已完成！所有页面的图片都已生成。\n\n👉 下一步：点击上方「下载 PPTX」按钮获取最终演示文稿。如果需要调整某页，可以选中后重新生成。",
          agentRole: "visual",
        },
      ]);
    }
    // 生成失败时也清除 loading 并提示
    if (prev.projectId === pid && prev.status === "generating" && currentStatus === "failed") {
      const loadingId = generationLoadingIdRef.current;
      generationLoadingIdRef.current = null;
      setVisualChatHistory((prevMsgs) => [
        ...prevMsgs.filter((m) => m.id !== loadingId),
        {
          role: "agent",
          content: "❌ 批量生成失败，部分页面可能未成功生成。请检查失败页面后重试，或告诉我具体问题。",
          agentRole: "visual",
        },
      ]);
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

  // 聊天自动滚动
  useEffect(() => {
    if (chatContainerRef.current) {
      chatContainerRef.current.scrollTop = chatContainerRef.current.scrollHeight;
    }
  }, [chatMessages, chatLoading]);

  // Rebuild a truthful transient loading message from the backend run after refresh/project switch.
  useEffect(() => {
    if (!selectedProject || !hasActiveRun || !activeRun?.id) return;
    const runId = activeRun.id;
    const targetAgent = activeRun.kind === "content_plan" ? "content" : "visual";
    const setter = targetAgent === "content" ? setContentChatHistory : setVisualChatHistory;
    const loadingId = `run-${runId}`;
    const progressText = workflowProgressText(currentProjectStatus || { active_run: activeRun });
    if (activeRun.kind !== "content_plan") {
      generationLoadingIdRef.current = loadingId;
    }
    setter((prev) => {
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

  const addSystemLog = (content: string) => {
    const logEntry = { role: "system" as const, content };
    setContentChatHistory((prev) => [...prev, logEntry]);
    setVisualChatHistory((prev) => [...prev, logEntry]);
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
        const fresh = await fetchProjects();
        const created = fresh.find((p: Project) => p.id === data.id);
        if (created) {
          setSelectedProject(created);
          setShowPrototypePreview(true);
          setCurrentAgentRole("content");
          setContentPlanConfirmed(false);
          setContentChatHistory([
            { role: "system", content: `用户创建了项目「${title}」` },
            {
              role: "agent",
              content: "👋 你好！我是你的内容总监。请告诉我你想做什么主题的 PPT？\n\n你可以：\n1. 直接输入主题（如「Q3 销售汇报」）\n2. 粘贴文档内容\n3. 拖拽上传 PDF/Word/PPT 文件",
              agentRole: "content",
            },
          ]);
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
      const fresh = await fetchProjects();
      const updated = fresh.find((p: Project) => p.id === projectId);
      if (updated && selectedProjectIdRef.current === projectId) setSelectedProject(updated);
      if (selectedProjectIdRef.current === projectId) {
        setShowStylePanel(false);
        setStyleProposalsInChat([]); // 清除Agent面板内的提案
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
          content: "❌ 保存风格失败：" + (err.message || "未知错误") + "\n\n👉 请检查网络后，在主舞台重新选择风格并点击「确认风格，生成生图方案」。",
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
        setProjectStatus(null);
      }
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
    updateProjectChatMessages(projectId, "visual", (prev) => [
      ...prev,
      {
        role: "agent" as const,
        content: `✅ 风格「${name}」已确认，正在生成每页画面描述和生图 Prompt。\n\n完成后会进入「打样确认」阶段。`,
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
      const startResult = await generateVisualPrompts(projectId, pageNums);
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
              if (selectedProjectIdRef.current === projectId) setSlides(freshSlides);
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
              if (selectedProjectIdRef.current === projectId) setSlides(freshSlides);
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
            "✅ 画面设计已完成：每页画面描述和生图 Prompt 已生成。\n\n👉 下一步：先「打样确认」生成 1-3 张预览效果；满意后再「全量生成」所有页面。也可以直接全量生成。",
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
            : "❌ 生图方案生成失败：" + message + "\n\n👉 解决方法：\n1. 检查网络连接后，点击上方「确认风格，生成生图方案」按钮重试\n2. 如果多次失败，可以尝试回退到「视觉方案」阶段重新选择风格\n3. 也可以直接告诉我具体问题，我来帮你调整",
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
    const pageDesc = pageNums ? `第 ${pageNums.join(", ")} 页` : (prototype ? "前 3 页" : "所有页面");
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
    try {
      await stopGeneration(selectedProject.id);
      await loadStatus(selectedProject.id);
      await loadProjects();
      showToast("已停止生成", "info");
      const loadingId = generationLoadingIdRef.current;
      generationLoadingIdRef.current = null;
      setVisualChatHistory((prev) => [
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

  // 更新画面方案：只更新画面描述/提示词，不自动生图。
  const handleUpdateStaleSlides = async (targetSlideIds?: string[], options?: { local?: boolean }) => {
    if (!selectedProject) return;
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
      setOperatingProjectId(selectedProject.id);
    }
    try {
      const needsFullPlan = targets.filter((x) => x.stale.content);
      const needsPrompt = targets.filter((x) => x.stale.content || x.stale.visual);
      const pageNumsForPrompt = Array.from(new Set(needsPrompt.map((x) => x.slide.page_num)));

      if (needsFullPlan.length > 0) {
        showToast(`正在更新 ${needsFullPlan.length} 页的画面描述...`, "info");
        const pageNums = needsFullPlan.map((x) => x.slide.page_num);
        await generateVisualPlan(selectedProject.id, pageNums);
        await loadSlides(selectedProject.id);
      }

      if (pageNumsForPrompt.length > 0) {
        showToast(`正在更新 ${pageNumsForPrompt.length} 页的生图提示词...`, "info");
        await generatePrompts(selectedProject.id, pageNumsForPrompt);
        await loadSlides(selectedProject.id);
        needsPrompt.forEach((x) => {
          clearSlideStale(x.slide.id, "content");
          clearSlideStale(x.slide.id, "visual");
          markSlideStale(x.slide.id, "image");
        });
      }

      const imageStale = targets.filter((x) => x.stale.image);
      if (imageStale.length > 0) {
        showToast(`${imageStale.length} 页需重新生成图片，请先确认`, "info");
        setVisualChatHistory((prev) => [
          ...prev,
          {
            role: "agent",
            content: `🎨 ${imageStale.length} 页已经需要重新生成图片。\n\n👉 请先检查单页里的文字、参考图、画面描述和生图提示词，再点击「确认并重新生成图片」。`,
            agentRole: "visual",
          },
        ]);
      }

      showToast("更新完成", "success");
      await loadSlides(selectedProject.id);
      await loadProjects();
      const updatedCount = needsPrompt.length;
      if (updatedCount > 0) {
        addSystemLog(`用户更新了 ${updatedCount} 页的画面方案`);
        setVisualChatHistory((prev) => [
          ...prev,
          {
            role: "agent",
            content: `✅ 已更新 ${updatedCount} 页的画面方案。\n\n这些页面现在进入「需重新生成图片」状态。请检查后再确认生图。`,
            agentRole: "visual",
          },
        ]);
      }
    } catch (err: any) {
      showToast("更新失败：" + (err.message || "未知错误"), "error");
      setVisualChatHistory((prev) => [
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
    const targets = targetSlideIds
      ? imageStaleSlides.filter((x) => targetSlideIds.includes(x.slide.id))
      : imageStaleSlides;
    if (targets.length === 0) return;

    if (!options?.local) {
      setOperatingProjectId(selectedProject.id);
    }
    try {
      showToast(`正在重新生成 ${targets.length} 页图片...`, "info");
      const pageNums = targets.map((x) => x.slide.page_num);
      await startGeneration(selectedProject.id, pageNums);
      await pollUntilStatusNotGenerating(selectedProject.id);
      targets.forEach((x) => clearSlideStale(x.slide.id, "image"));
      showToast("图片生成完成", "success");
      await loadSlides(selectedProject.id);
      await loadProjects();
      addSystemLog(`用户确认并重新生成了 ${targets.length} 页图片`);
    } catch (err: any) {
      showToast("生成失败：" + (err.message || "未知错误"), "error");
      setVisualChatHistory((prev) => [
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
      `回退到「${stageNames[targetStage] || targetStage}」？\n这将清除该阶段之后的所有数据，需要重新生成。`
    );
    if (!ok) return;

    // 全面清理所有运行中状态和轮询
    setChatLoading(false);
    setThinkingContent("");
    setThinkingExpanded(false);
    setContentPlanProgress(null);
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
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
      const fresh = await fetchProjects();
      const updated = fresh.find((p: Project) => p.id === projectId);
      if (updated && selectedProjectIdRef.current === projectId) setSelectedProject(updated);
      await loadSlides(projectId);
      if (selectedProjectIdRef.current === projectId) setStaleMap({});

      // 根据回退目标生成详细的自动化引导消息
      let rollbackMsg = `⏪ 已回退到「${stageNames[targetStage] || targetStage}」。后续数据已重置。`;
      if (targetStage === "visual_ready") {
        const logoAsset = referenceImages.find((r: any) => r.role === "logo");
        const styleRefAssets = referenceImages.filter((r: any) => r.role === "style_ref");
        const templateAsset = referenceImages.find((r: any) => r.role === "template");
        const visualAssetAssets = referenceImages.filter((r: any) => r.role === "visual_asset");
        rollbackMsg += `\n\n**视觉总监已重新介入。** 为了给你更精准的风格提案和画面生成，请先确认当前的项目素材：\n\n📎 **素材清单（参考强度从高到低）**\n• 品牌 Logo：${logoAsset ? "已上传 ✅" : "未上传"}\n• 核心资产：${visualAssetAssets.length > 0 ? `已上传 ${visualAssetAssets.length} 个 ✅` : "未上传"}\n• 风格参考：${styleRefAssets.length > 0 ? `已上传 ${styleRefAssets.length} 张 ✅` : "未上传"}\n• 版式模板：${templateAsset ? "已上传 ✅" : "未上传"}\n• 风格描述：可在聊天中直接告诉我（如"更商务一点""要温暖生活感"）\n\n你可以：**① 继续上传素材**（品牌 Logo / 核心资产 / 风格参考 / 版式模板）→ **② 告诉我你的风格偏好** → **③ 或直接说"开始提案"**，我会基于现有信息立即生成风格方案。`;
      } else if (targetStage === "planning") {
        rollbackMsg += `\n\n**内容总监已重新介入。** 你可以继续调整内容规划：\n\n• 增减页数、调整章节结构\n• 修改某一页的标题或正文（直接说"修改第X页"）\n• 更换整体内容方向或主题\n\n👉 确认内容规划满意后，我们再一起进入视觉设计阶段。`;
      } else if (targetStage === "prompt_ready") {
        rollbackMsg += `\n\n你可以重新选择或调整风格，我会基于新的风格重新为每一页生成生图 Prompt。\n\n👉 确认风格后，点击「确认风格，生成生图方案」即可。`;
      } else if (targetStage === "prototype_ready") {
        rollbackMsg += `\n\n你可以重新选择打样页面或调整风格，然后再次打样确认。\n\n👉 选择页面后点击「打样确认」即可。`;
      }
      updateProjectChatMessages(projectId, targetStage === "planning" ? "content" : "visual", (prev) => [
        ...prev.filter((m) => !m.loading),
        { role: "system" as const, content: `用户回退到「${stageNames[targetStage] || targetStage}」阶段` },
        { role: "agent" as const, content: rollbackMsg, agentRole: targetStage === "planning" ? "content" : "visual" },
      ]);

      // 根据回退目标调整 Agent 角色
      if (selectedProjectIdRef.current === projectId) {
        if (targetStage === "planning") {
          setCurrentAgentRole("content");
          setContentPlanConfirmed(false);
        } else if (targetStage === "visual_ready") {
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
          content: "❌ 回退失败：" + (err.message || "未知错误") + "\n\n👉 请检查网络后，在主舞台顶部流程条中再次点击回退目标。",
          agentRole: "visual",
        },
      ]);
    } finally {
      setOperatingProjectId(null);
    }
  };

  const togglePage = (pageNum: number) => {
    setSelectedPages((prev) => {
      const next = new Set(prev);
      if (next.has(pageNum)) {
        next.delete(pageNum);
      } else {
        next.add(pageNum);
      }
      return next;
    });
  };

  const selectAll = () => {
    setSelectedPages(new Set(slides.map((s) => s.page_num)));
  };

  const clearSelection = () => {
    setSelectedPages(new Set());
  };

  const handleRetry = async (slideId: string, regeneratePrompt: boolean = false) => {
    if (!selectedProject) return;
    if (operatingProjectId === selectedProject.id) return;
    if (hasActiveRun) {
      showToast("当前已有生成任务在执行中，请稍后再试", "info");
      return;
    }
    const slide = slides.find((s) => s.id === slideId);
    setOperatingProjectId(selectedProject.id);
    try {
      const result = await retrySlide(selectedProject.id, slideId, regeneratePrompt);
      await loadSlides(selectedProject.id);
      await loadStatus(selectedProject.id);
      const loadingId = `gen-${Date.now()}`;
      generationLoadingIdRef.current = loadingId;
      setVisualChatHistory((prev) => [
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
      setVisualChatHistory((prev) => [
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
    const failedSlides = slides.filter((s) => s.status === "failed");
    if (failedSlides.length === 0) {
      showToast("当前没有失败的页面", "info");
      return;
    }
    setOperatingProjectId(selectedProject.id);
    const loadingId = `retry-${Date.now()}`;
    generationLoadingIdRef.current = loadingId;
    try {
      const result = await retryFailed(selectedProject.id);
      showToast(`已启动 ${result.count} 个失败页面的重试`, "success");
      await loadSlides(selectedProject.id);
      await loadStatus(selectedProject.id);
      addSystemLog(`用户重试了 ${result.count} 个失败页面`);
      setVisualChatHistory((prev) => [
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
      setVisualChatHistory((prev) => [
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
    input.onchange = async (e) => {
      const file = (e.target as HTMLInputElement).files?.[0];
      if (!file || !selectedProject) return;
      setOperatingProjectId(selectedProject.id);
      try {
        if (currentAgentRole === "finetune" && finetuneTargetSlideId === slideId) {
          const data = await uploadFile(selectedProject.id, file, "finetune_ref", slideId);
          const attachment: ChatAttachment = {
            id: data.id,
            name: file.name,
            url: `${API_BASE}${data.url}`,
            role: "finetune_ref",
          };
          setPendingFinetuneAttachmentsMap((prev) => ({
            ...prev,
            [slideId]: [...(prev[slideId] || []), attachment],
          }));
          showToast("参考图已加入本轮微调", "success");
          addSystemLog(`用户为第 ${slides.find((s) => s.id === slideId)?.page_num || "?"} 页添加了本轮微调参考图`);
          return;
        }
        await uploadFile(selectedProject.id, file, "content_ref", slideId);
        markSlideStale(slideId, "visual");
        await loadProjects();
        await loadSlides(selectedProject.id);
        const slide = slides.find((s) => s.id === slideId);
        const pageNum = slide?.page_num || "?";
        addSystemLog(`用户为第 ${pageNum} 页上传了参考图（融合模式）`);
        // 微调模式下，在聊天中给予可见反馈
        if (currentAgentRole === "finetune" && finetuneTargetSlideId === slideId) {
          setFinetuneChatHistoryMap((prev) => {
            const current = prev[slideId] || [];
            return {
              ...prev,
              [slideId]: [...current, { role: "system", content: `已添加参考图到第 ${pageNum} 页。下一条修改要求会自动带上这张图。` }],
            };
          });
        }
      } catch (err: any) {
        showToast("上传失败：" + (err.message || "未知错误"), "error");
      } finally {
        setOperatingProjectId(null);
      }
    };
    input.click();
  };

  const handleUploadDocument = async () => {
    const input = docInputRef.current;
    if (!input || !input.files || input.files.length === 0) return;
    if (!selectedProject) return;
    const file = input.files[0];
    setUploadingDoc(true);
    try {
      const data = await uploadDocument(selectedProject.id, file);
      if (data.detail) {
        showToast("上传失败：" + data.detail, "error");
      } else {
        await loadDocuments(selectedProject.id);
        setPendingAttachments((prev) => [...prev, data.filename]);
        addSystemLog(`用户上传了文档「${file.name}」`);
        setContentChatHistory((prev) => [
          ...prev,
          {
            role: "agent",
            content: `📎 文档「${file.name}」已上传成功。\n\n👉 请继续描述你的 PPT 需求（如主题、受众、场景），我会结合文档内容为你规划。`,
            agentRole: "content",
          },
        ]);
      }
    } catch (err: any) {
      showToast("上传失败：" + (err.message || "未知错误"), "error");
    } finally {
      setUploadingDoc(false);
      input.value = "";
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
      handleStopChat(); // 停止当前流，避免状态错乱
      setCurrentAgentRole("visual");
      ensureVisualGreetingIfNeeded();
      return;
    }

    isConfirmingRef.current = true;
    setConfirmingProjectId(projectId);
    handleStopChat(); // 停止当前 agent 的流，避免切换到视觉总监后状态错乱
    setChatLoading(true);

    try {
      // 保存当前内容快照用于软锁定检测
      const currentSlides = await fetchSlides(projectId);
      if (selectedProjectIdRef.current === projectId) {
        setContentPlanSnapshot(currentSlides);
        softLockWarnedRef.current = false;
      }

      // 内容总监：获取参考图推荐列表
      let suggestions: any[] = [];
      try {
        const suggestRes = await suggestReferenceImages(projectId);
        suggestions = suggestRes.suggestions || [];
      } catch (e) {
        console.warn("获取参考图推荐失败", e);
      }
      if (suggestions.length > 0) {
        const suggestionText =
          "📋 内容总监参考图建议\n\n" +
          suggestions
            .map(
              (s: any) =>
                `**第${s.page_num}页**（${s.type}）：${s.reason}\n建议处理模式：**${
                  s.recommended_mode === "blend"
                    ? "融合"
                    : s.recommended_mode === "crop"
                    ? "裁切"
                    : "原图"
                }**`
            )
            .join("\n\n") +
          "\n\n你可以在单页编辑里点击「+ 本页参考图」上传只影响当前页的图片。";
        updateProjectChatMessages(projectId, "content", (prev) => [
          ...prev,
          {
            role: "agent",
            content: suggestionText,
            agentRole: "content",
          },
        ]);
      }

      // 检查是否已有设计素材：必须从当前项目实时读取，避免切项目后沿用上个项目素材。
      const freshReferenceImages = await fetchReferenceImages(projectId);
      if (selectedProjectIdRef.current === projectId) {
        setReferenceImages(freshReferenceImages || []);
      }
      const hasAssets = (freshReferenceImages || []).length > 0;
      const logoAsset = (freshReferenceImages || []).find((r: any) => r.role === "logo");
      const styleRefAssets = (freshReferenceImages || []).filter((r: any) => r.role === "style_ref");
      const templateAsset = (freshReferenceImages || []).find((r: any) => r.role === "template");
      const visualAssetAssets = (freshReferenceImages || []).filter((r: any) => r.role === "visual_asset");
      const assetDesc = [
        logoAsset ? "品牌 Logo" : "",
        visualAssetAssets.length > 0 ? `${visualAssetAssets.length}个核心资产` : "",
        styleRefAssets.length > 0 ? `${styleRefAssets.length}张风格参考` : "",
        templateAsset ? "版式模板" : "",
      ].filter(Boolean).join("、");

      // 切换状态并显示固定开场白（无需调用 LLM，节省 API 成本）
      if (selectedProjectIdRef.current === projectId) {
        setContentPlanConfirmed(true);
        setCurrentAgentRole("visual");
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

      // 固定开场白：询问用户是否有素材，等待用户确认后再生成
      const directorMsg = hasAssets
        ? `我是视觉总监。已收到你上传的设计素材（${assetDesc}）。\n\n👉 如果你还想补充素材，请继续上传；如果已经齐了，点击下方「开始生成」按钮，我会立即基于这些素材制定风格方案。`
        : "我是视觉总监。你可以按参考强度从高到低上传素材：品牌 Logo、核心资产、风格参考、版式模板。\n\n👉 Logo 默认作为预览/PPTX 角标叠加；核心资产会按页面内容进入生成；风格参考只学习视觉气质；版式模板只参考页面结构。没有素材也可以直接生成。";
      updateProjectChatMessages(projectId, "visual", (prev) => [
        ...prev,
        {
          role: "agent",
          content: directorMsg,
          agentRole: "visual",
        },
      ]);
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
      if (selectedProjectIdRef.current === projectId) {
        setChatLoading(false);
      }
      isConfirmingRef.current = false;
    }
  };

  // 注：原 autoGenerateStyleProposals 已合并到 handleSendChat（聊天路径），
  // 按钮和聊天走同一管道，确保历史调整意见和当前提案锚点不会被丢弃。

  const handleSendChat = async (forcedMsg?: string, baseHistory?: typeof chatMessages, isRetry = false) => {
    if (!selectedProject) return;
    const requestProject = selectedProject;
    const requestProjectId = requestProject.id;
    const requestAgentRole = currentAgentRole;
    const requestRoleCanPersist = requestAgentRole === "content" || requestAgentRole === "visual";
    const isRequestVisible = () =>
      selectedProjectIdRef.current === requestProjectId && currentAgentRoleRef.current === requestAgentRole;
    const appendRequestMessage = (message: ChatMessage) => {
      if (requestRoleCanPersist) {
        appendProjectChatMessage(requestProjectId, requestAgentRole, message);
      } else if (isRequestVisible()) {
        setActiveChatMessages((prev) => [...prev, message]);
      }
    };
    const userMsg = (forcedMsg || chatInput).trim();
    const hasAttachments = pendingAttachments.length > 0;
    const hasFinetunePendingAttachments =
      currentAgentRole === "finetune" &&
      !!finetuneTargetSlideId &&
      (pendingFinetuneAttachmentsMap[finetuneTargetSlideId] || []).length > 0;
    if (!userMsg && !hasAttachments && !hasFinetunePendingAttachments) return;

    // 构建用户消息展示内容（包含附件引用）
    let displayContent = userMsg;
    if (hasAttachments) {
      const attachmentText = pendingAttachments.map((f) => `📎 ${f}`).join("\n");
      displayContent = userMsg ? `${userMsg}\n\n${attachmentText}` : attachmentText;
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

    if (currentAgentRole === "finetune") {
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
        setActiveChatMessages((prev) => [
          ...prev,
          { ...newMessage, content: userMsg, attachments: finetuneAttachments },
          {
            role: "agent",
            content: `正在微调第 ${targetSlide.page_num} 页...`,
            agentRole: "finetune",
            loading: true,
            id: loadingId,
          },
        ]);
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
      chatInProgressRef.current = true;

      try {
        await finetuneSlide(selectedProject.id, targetSlide.id, userMsg, finetuneAttachments.map((a) => a.id));
        await loadSlides(selectedProject.id);
        await pollUntilStatusNotGenerating(selectedProject.id);
        await loadSlideVersions(targetSlide.id);
        const freshSlides = await fetchSlides(selectedProject.id);
        const freshSlide = freshSlides.find((s: Slide) => s.id === targetSlide.id);
        if (freshSlide) {
          setSlides(freshSlides);
          if (editingSlide?.id === targetSlide.id) setEditingSlide(freshSlide);
        }
        if (freshSlide?.status === "failed") {
          throw new Error(freshSlide.error_msg || "图像模型未能生成微调版本");
        }
        bumpSlideImageRefresh(targetSlide.id);
        setActiveChatMessages((prev) => [
          ...prev.filter((m) => m.id !== loadingId),
          {
            role: "agent",
            content: `已生成第 ${targetSlide.page_num} 页的微调版本。当前页原图已自动存入版本历史，可随时回退。`,
            agentRole: "finetune",
          },
        ]);
      } catch (err: any) {
        setActiveChatMessages((prev) => [
          ...prev.filter((m) => m.id !== loadingId),
          {
            role: "agent",
            content: `微调失败：${err.message || "未知错误"}`,
            agentRole: "finetune",
          },
        ]);
      } finally {
        setOperatingProjectId(null);
        setChatLoading(false);
        chatInProgressRef.current = false;
      }
      return;
    }

      // 重试时不重复添加用户消息
    if (!isRetry) {
      setActiveChatMessages((prev) => [...prev, newMessage]);
      setChatInput("");
      setPendingAttachments([]);
    }
    setChatLoading(true);
    setThinkingContent("");
    setThinkingExpanded(false);
    // 锁定当前流所属的项目和角色，防止状态跳到别的窗口
    activeChatProjectIdRef.current = requestProjectId;
    activeChatRoleRef.current = requestAgentRole;
    chatInProgressRef.current = true;

    // 创建 AbortController 用于停止输出
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    // 使用 baseHistory（编辑消息时传入）或当前 chatMessages，确保包含最新用户消息
    const msgList = baseHistory || chatMessages;
    // 重试时 baseHistory 已包含用户消息，避免重复添加
    const history = (isRetry ? msgList : [...msgList, newMessage]).map((m) => ({
      role: m.role === "agent" ? "assistant" : m.role,
      content: m.content,
    }));
    let result: any = null;
    let streamedContent = "";

    try {

      // 保存请求参数，用于切回来后自动恢复
      pendingChatRef.current = {
        projectId: requestProjectId,
        message: userMsg,
        history: [...history],
        pageContext: undefined as any,
        agentRole: requestAgentRole,
      };

      // 根据 agentMode 构建 pageContext
      let pageContext: any = undefined;
      if (agentMode === "page" && editingSlide) {
        const otherPages = slides
          .filter((s) => s.id !== editingSlide.id)
          .map((s) => {
            const tc = s.content_json?.text_content || {};
            return {
              page_num: s.page_num,
              type: s.type,
              headline: tc.headline || "",
              subhead: tc.subhead || "",
              body_preview: typeof tc.body === "string"
                ? tc.body.split("\n").filter(Boolean).slice(0, 2).join("\n")
                : (tc.body || []).slice(0, 2).map((item: any) =>
                    typeof item === "string" ? item : item?.content || ""
                  ).join("\n"),
            };
          });
        pageContext = {
          mode: "page",
          current_page: {
            page_num: editingSlide.page_num,
            slide_id: editingSlide.id,
            type: editingSlide.type,
            content_json: editingSlide.content_json,
            visual_json: editingSlide.visual_json,
            prompt_text: editingSlide.prompt_text,
            reference_images: editingSlide.reference_images || [],
            pending_state: staleMap[editingSlide.id] || null,
          },
          other_pages: otherPages,
        };
      } else if (agentMode === "global" && slides.length > 0) {
        pageContext = {
          mode: "global",
          slides: slides.map((s) => {
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
          }),
        };
      }

      // 更新 pendingChatRef 中的 pageContext
      if (pendingChatRef.current) {
        pendingChatRef.current.pageContext = pageContext;
      }

      // 用于标记是否因可重试的流中断而跳出循环
      let streamRetryReason: string | null = null;

      for await (const event of chatWithAgentStream(requestProjectId, userMsg, history, ctrl.signal, pageContext, requestAgentRole)) {
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
          appendRequestMessage({ role: "agent", content: `❌ ${msg || "请求出错"}`, agentRole: requestAgentRole });
          setChatLoading(false);
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
        if (!streamRetryReason) {
          appendRequestMessage({ role: "system", content: "🔄 响应不完整，正在自动重试..." });
        }
        const retryCtrl = new AbortController();
        abortRef.current = retryCtrl;
        try {
          for await (const event of chatWithAgentStream(requestProjectId, userMsg, history, retryCtrl.signal, pageContext, requestAgentRole)) {
            if (event.type === "result") {
              result = event.data;
            } else if (event.type === "content") {
              streamedContent += event.delta || "";
            } else if (event.type === "error") {
              appendRequestMessage({ role: "agent", content: `❌ ${event.message || "请求出错"}`, agentRole: requestAgentRole });
              setChatLoading(false);
              abortRef.current = null;
              return;
            }
          }
        } catch (retryErr: any) {
          // 只要不是用户主动停止，任何异常都要给用户反馈
          if (!retryCtrl.signal.aborted) {
            appendRequestMessage({ role: "agent", content: "请求失败，请重试。", agentRole: requestAgentRole });
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
        appendRequestMessage({ role: "agent", content: "⚠️ 响应未返回完整结果，请重试一次。", agentRole: requestAgentRole });
        setChatLoading(false);
        return;
      }

      // 如果重试流被用户主动中断，不继续处理
      if (abortRef.current?.signal?.aborted) return;

      const action = result.action;
      const hasPageTarget = Boolean(result.page_nums?.length || editingSlide);
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

      // 如果项目还是默认名，Agent 已经推断出主题，自动重命名
	      if (result.title && requestProject.title === "未命名项目") {
	        try {
	          await updateProject(requestProjectId, { title: result.title });
	          await loadProjects();
        } catch (e) {
	          console.warn("Auto-rename after chat error:", e);
	        }
	      }

	      // 如果用户已经切到别的项目/Agent，聊天回复已写入原项目的持久记录；
	      // 后续 UI 型副作用等用户回到原项目后再触发，避免污染当前页面状态。
	      if (!isRequestVisible()) {
	        return;
	      }

      // Agent 在聊天中确认风格，自动保存并推进
      if (result.action === "confirm_style" && result.style) {
        await handleSelectStyle(result.style);
      }

      // Agent 要求重新生成指定页
      if (result.action === "regenerate_pages" && result.page_nums?.length > 0) {
        const targetSlides = slides.filter((s) => result.page_nums.includes(s.page_num));
        targetSlides.forEach((s) => markSlideStale(s.id, "image"));
        setActiveChatMessages((prev) => [
          ...prev,
          {
            role: "agent",
            content: `已标记第 ${result.page_nums.join(", ")} 页为「需重新生成图片」。\n\n这一步会产生生图成本，请进入对应页面检查后点击「确认生成图片」。`,
            agentRole: currentAgentRole,
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
            agentRole: currentAgentRole,
          },
        ]);
      }

      // Agent 要求重新抽一版当前/指定页面画面方案（不生图）
      if (result.action === "reroll_page_visual_plan") {
        const pageNums = result.page_nums?.length
          ? result.page_nums
          : editingSlide
          ? [editingSlide.page_num]
          : [];
        const targetIds = slides
          .filter((s) => pageNums.includes(s.page_num))
          .map((s) => s.id);
        if (targetIds.length > 0) {
          await handleUpdateStaleSlides(targetIds, { local: true });
          setVisualChatHistory((prev) => [
            ...prev,
            {
              role: "agent",
              content: `已为第 ${pageNums.join(", ")} 页再生成一版画面方案。请检查后再决定是否生成图片。`,
              agentRole: "visual",
            },
          ]);
        }
      }

      // Agent 精确修改单页视觉描述
      if (result.action === "update_slide_visual" && result.updated_visual) {
        let pageNum = result.updated_visual.page_num;
        if (agentMode === "page" && editingSlide) {
          pageNum = editingSlide.page_num;
        }
        const targetSlide = slides.find((s) => s.page_num === pageNum);
        if (targetSlide) {
          setVisualChatHistory((prev) => [
            ...prev,
            { role: "agent", content: "正在应用视觉描述修改...", agentRole: "visual" },
          ]);
          try {
            await updateVisualPlan(selectedProject.id, pageNum, result.updated_visual.visual_json, targetSlide.id);
            markSlideStale(targetSlide.id, "visual");
            await loadSlides(selectedProject.id);
            // 同步更新 editingSlide
            if (editingSlide && editingSlide.page_num === pageNum) {
              const updated = await fetchSlides(selectedProject.id);
              const freshSlide = updated.find((s: Slide) => s.page_num === pageNum);
              if (freshSlide) setEditingSlide(freshSlide);
            }
            // 自动更新生图提示词并重新生成图片
            await handleUpdateStaleSlides([targetSlide.id], { local: true });
            setVisualChatHistory((prev) => [
              ...prev,
              {
                role: "agent",
                content: `✅ 已更新第 ${pageNum} 页的视觉描述，正在重新生成图片...`,
                agentRole: "visual",
              },
            ]);
            // 自动触发重新生成
            try {
              await handleRetry(targetSlide.id, true);
            } catch (retryErr: any) {
              setVisualChatHistory((prev) => [
                ...prev,
                {
                  role: "agent",
                  content: `⚠️ 视觉描述已更新，但图片重新生成时遇到问题：${retryErr.message || "未知错误"}。你可以稍后点击「重试」按钮再次尝试。`,
                  agentRole: "visual",
                },
              ]);
            }
          } catch (err: any) {
            setVisualChatHistory((prev) => [
              ...prev,
              {
                role: "agent",
                content: "应用视觉描述修改失败：" + (err.message || "未知错误"),
                agentRole: "visual",
              },
            ]);
          }
        }
      }

      // Agent 全局修改多页视觉描述
      if (result.action === "update_all_slides_visual" && result.updated_slides_visual?.length > 0) {
        setVisualChatHistory((prev) => [
          ...prev,
          { role: "agent", content: `正在应用 ${result.updated_slides_visual.length} 页的视觉描述修改...`, agentRole: "visual" },
        ]);
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
          } catch (err) {
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
          // 自动触发重新生成图片
          setVisualChatHistory((prev) => [
            ...prev,
            { role: "agent", content: `正在重新生成第 ${updatedPageNums.join(", ")} 页的图片...`, agentRole: "visual" },
          ]);
          for (const slideId of updatedSlideIds) {
            try {
              await handleRetry(slideId, true);
            } catch (retryErr: any) {
              const slide = slides.find((s) => s.id === slideId);
              setVisualChatHistory((prev) => [
                ...prev,
                {
                  role: "agent",
                  content: `⚠️ 第 ${slide?.page_num || "?"} 页图片重新生成失败：${retryErr.message || "未知错误"}`,
                  agentRole: "visual",
                },
              ]);
            }
          }
        }
        let msg = `✅ 已更新第 ${updatedPageNums.join(", ")} 页的视觉描述并重新生成图片。`;
        if (skipped.length > 0) msg += `（跳过不存在的页：${skipped.join(", ")}）`;
        setVisualChatHistory((prev) => [
          ...prev,
          { role: "agent", content: msg, agentRole: "visual" },
        ]);
      }

      // Agent 理解用户想生图，但成本动作必须由用户确认
      if (result.action === "request_generate_image") {
        const pageNums = result.page_nums?.length
          ? result.page_nums
          : editingSlide
          ? [editingSlide.page_num]
          : [];
        const targetSlides = slides.filter((s) => pageNums.includes(s.page_num));
        targetSlides.forEach((s) => markSlideStale(s.id, "image"));
        setVisualChatHistory((prev) => [
          ...prev,
          {
            role: "agent",
            content: pageNums.length
              ? `可以生成第 ${pageNums.join(", ")} 页图片，但这会产生生图成本。请在单页中点击「确认生成图片」。`
              : "可以生成图片，但这会产生生图成本。请先选择具体页面，并在单页中点击「确认生成图片」。",
            agentRole: "visual",
          },
        ]);
      }

      // 内容总监识别到内容已确认，自动转接视觉总监
      if (result.action === "forward_to_visual" && currentAgentRole === "content") {
        if (!contentPlanConfirmed && slides.length > 0) {
          await handleConfirmContentPlan();
        } else {
          handleStopChat();
          setCurrentAgentRole("visual");
          ensureVisualGreetingIfNeeded();
        }
        return;
      }

      // 视觉总监识别到内容问题，自动转接内容总监
      if (result.action === "forward_to_content" && currentAgentRole === "visual") {
        setCurrentAgentRole("content");
        setContentChatHistory((prev) => [
          ...prev,
          {
            role: "agent",
            content: result.response || "已为你转接内容总监，可以继续沟通内容相关的问题。",
            agentRole: "content",
          },
        ]);
        return;
      }

      // Agent 要求重新生成内容规划（页数可能变化）
      if (result.action === "regenerate_plan" && result.topic) {
        // 视觉总监不应该触发内容规划重新生成
        if (currentAgentRole === "visual") {
          setVisualChatHistory((prev) => [
            ...prev,
            {
              role: "agent",
              content: "我是视觉总监，负责设计风格和画面效果。如果你想调整内容规划，请切换到内容总监继续。",
              agentRole: "visual",
            },
          ]);
        } else {
          await startContentPlanPoll(selectedProject.id, result.topic, "agent", result.page_count);
        }
      }

      // 视觉总监确认素材状态，触发风格提案生成
      if ((result.action === "propose_styles" || result.action === "adjust_style") && currentAgentRole === "visual" && selectedProject) {
        const isAdjust = result.action === "adjust_style";
        // 优先使用 Agent 聊天返回的实时风格提案（与聊天建议保持一致）
        if (result.style_proposal && typeof result.style_proposal === "object") {
          const proposal = result.style_proposal;
          // 标准化 palette 格式
          if (proposal.palette && Array.isArray(proposal.palette)) {
            proposal.palette = proposal.palette.map((c: any) => {
              if (!c) return { name: "未知", hex: "#cccccc", role: "" };
              if (typeof c === "string") return { name: c, hex: c, role: "" };
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
                ? "✅ 已根据你的反馈调整了方案，请查看下方新卡片。\n\n👉 满意请点「选择此方案」，不满意继续告诉我哪里要再改。"
                : "✅ 风格提案已生成，请查看下方卡片。\n\n👉 如果满意请点击「选择此方案」；如果想调整，直接告诉我（如「更商务一点」「配色再暖一些」）。",
              agentRole: "visual",
              hasStyleProposal: true,
              styleProposals: [proposal],
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
          try {
            const freshReferenceImages = await fetchReferenceImages(requestProjectId);
            const styleResult = await generateStyleProposals(requestProjectId, freshReferenceImages.length > 0);
            if (styleResult.status === "generating") {
              showToast("风格提案后台生成中，请稍候...", "info");
              await pollForStyleProposals(requestProjectId);
            } else if (styleResult.status === "completed" && styleResult.proposals) {
              showToast("风格提案已就绪", "success");
            }
            await loadProjects();
            const fresh = await fetchProjects();
            const updated = fresh.find((p: Project) => p.id === requestProjectId);
            if (updated && selectedProjectIdRef.current === requestProjectId) setSelectedProject(updated);
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
                },
              ]);
            } else {
              updateProjectChatMessages(requestProjectId, "visual", (prev) => [
                ...prev.filter((m) => m.id !== styleLoadingId),
                {
                  role: "agent",
                  content:
                    "✅ 风格提案已生成，请查看主舞台。\n\n👉 下一步：从三套方案中选择最喜欢的一套，或直接告诉我你的偏好，我会进一步调整。",
                  agentRole: "visual",
                },
              ]);
            }
          } catch (err: any) {
            showToast("风格提案生成失败：" + (err.message || "未知错误"), "error");
            updateProjectChatMessages(requestProjectId, "visual", (prev) => [
              ...prev.filter((m) => m.id !== styleLoadingId),
              {
                role: "agent",
                content: "❌ 风格提案生成失败：" + (err.message || "未知错误") + "\n\n👉 请重试生成，或告诉我你想要的风格方向，我可以直接帮你选择。",
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
          if (agentMode === "page" && editingSlide) {
            pageNum = editingSlide.page_num;
            result.updated_content.page_num = pageNum;
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
                content: `📝 第 ${pageNum} 页内容已更新，视觉方案可能需要调整。请进入「视觉总监」阶段重新设计画面后再生成图片。`,
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
          if (skipped.length > 0) {
            msg += `\n⚠️ 跳过不存在的页面：第 ${skipped.join(", ")} 页（项目当前共 ${slides.length} 页）。`;
          }
          setActiveChatMessages((prev) => [...prev, { role: "agent", content: msg }]);
          // 内容更新后，标记相关页面需要重新设计视觉方案，但不自动触发图片生成
          setActiveChatMessages((prev) => [
            ...prev,
            { role: "agent", content: `📝 内容已更新，相关页面的视觉方案可能需要调整。请确认内容后，进入「视觉总监」阶段重新设计画面，再生成图片。` },
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
          if (agentMode === "page" && editingSlide) {
            pageNum = editingSlide.page_num;
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
          if (agentMode === "page" && editingSlide) {
            pageNum = editingSlide.page_num + 1;
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
      // 只有用户主动点击「停止」时才添加中断提示；
      // 标签页切换/网络波动导致的异常交给 visibilitychange 静默重试，不打扰用户
      if (err?.name === "AbortError") {
        const isVisual = currentAgentRole === "visual";
        setActiveChatMessages((prev) => [
          ...prev,
          {
            role: "agent",
            content: isVisual
              ? "⏹ 已停止生成。"
              : "⏹ 已停止生成。",
          },
        ]);
      }
      // 其他异常（网络中断、流错误等）静默处理，保留 pendingChatRef 供 visibilitychange 恢复
    } finally {
      abortRef.current = null;
      chatInProgressRef.current = false;
      // 只有这条流仍属于当前窗口时才重置 loading，防止切走后状态被覆盖
	      if (isRequestVisible()) {
	        setChatLoading(false);
	      }
      // 只有正常完成（拿到有效结果）时才清空 pendingChatRef；
      // 异常/中断时保留，让 visibilitychange 有机会自动恢复
      if (result != null && chatResultLooksValid(result)) {
        pendingChatRef.current = null;
      }
    }
  };

  const handleAgentNextAction = async (nextAction: AgentNextAction, sourceMessage?: ChatMessage) => {
    if (!selectedProject || isBusy || chatLoading) return;
    if (nextAction.confirm) {
      const ok = await showConfirm(`确定要执行「${nextAction.label}」吗？`);
      if (!ok) return;
    }

    const pageNums = (nextAction.payload?.page_nums || []).filter((n) => Number.isFinite(Number(n))).map(Number);

    switch (nextAction.type) {
      case "generate_content_plan": {
        const topic = nextAction.payload?.topic || sourceMessage?.topic;
        if (!topic) {
          showToast("缺少主题，无法生成内容规划", "error");
          return;
        }
        await startContentPlanPoll(selectedProject.id, topic, "button", nextAction.payload?.page_count || sourceMessage?.positioning?.estimated_pages);
        return;
      }
      case "switch_to_visual": {
        if (!contentPlanConfirmed && slides.length > 0) {
          await handleConfirmContentPlan();
        } else {
          setCurrentAgentRole("visual");
          ensureVisualGreetingIfNeeded();
        }
        return;
      }
      case "switch_to_content":
        setCurrentAgentRole("content");
        return;
      case "generate_style_proposals": {
        if (currentAgentRole !== "visual") {
          setCurrentAgentRole("visual");
          showToast("已切换到视觉总监，请再次点击生成风格提案", "info");
          return;
        }
        const fakeUserMsg = styleProposalsInChat.length > 0
          ? "请基于当前最新的素材和我们之前的讨论，重新给我一套风格提案。"
          : "请基于我已上传的素材帮我生成风格提案。";
        handleSendChat(fakeUserMsg);
        return;
      }
      case "generate_visual_prompts":
        await handleGeneratePrompts(false);
        return;
      case "generate_images":
        await handleStartGeneration(Boolean(pageNums.length), false, pageNums);
        return;
      case "start_prototype":
        await handleStartGeneration(selectedPages.size > 0, true);
        return;
      case "start_generation":
      case "retry_failed":
        await handleStartGeneration(false, false);
        return;
      default:
        const exhaustive: never = nextAction.type;
        console.warn("Unhandled agent next action:", exhaustive);
        showToast("这个下一步动作暂时还不能自动执行", "info");
    }
  };

  const handleStopChat = () => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    activeChatProjectIdRef.current = null;
    activeChatRoleRef.current = null;
    chatInProgressRef.current = false;
    pendingChatRef.current = null;
    setChatLoading(false);
    setThinkingContent("");
    setThinkingExpanded(false);
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

  const handleDropFiles = async (files: FileList) => {
    if (!selectedProject) return;
    if (currentAgentRole === "finetune" && finetuneTargetSlideId) {
      const targetSlide = slides.find((s) => s.id === finetuneTargetSlideId);
      const imageFiles = Array.from(files).filter((file) => file.type.startsWith("image/"));
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
    for (const file of Array.from(files)) {
      setUploadingDoc(true);
      try {
        const data = await uploadDocument(selectedProject.id, file);
        if (!data.detail) {
          await loadDocuments(selectedProject.id);
          setPendingAttachments((prev) => [...prev, data.filename]);
        }
      } catch (err: any) {
        showToast(`"${file.name}" 上传失败：${err.message || "未知错误"}`, "error");
      } finally {
        setUploadingDoc(false);
      }
    }
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
  const projectLogo = referenceImages.find((r: any) => r.role === "logo");

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
  const workflowState = buildWorkflowState({
    projectStatus: currentStatus,
    slides,
    activeRun,
    contentPlanConfirmed,
    showPrototypePreview,
    selectedPageCount: selectedPages.size,
    staleSummary: {
      hasContentOrVisualStale,
      imageStaleCount: imageStaleSlides.length,
    },
    templatePageCount: templatePages.length,
    isBusy,
  });
  const steps = WORKFLOW_STEPS;
  const displayStepIndex = workflowState.stepIndex;

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
  const hasStyleProposalCta = Boolean(selectedProject && contentPlanConfirmed && currentAgentRole === "visual" && !selectedProject?.selected_style);
  const shouldRenderMessageNextAction = (message: ChatMessage) => {
    const nextAction = message.nextAction;
    if (!nextAction) return false;
    if (nextAction.type === "generate_content_plan" && message.positioning) return false;
    if (nextAction.type === "switch_to_visual" && hasContentConfirmCta) return false;
    if (nextAction.type === "generate_style_proposals" && hasStyleProposalCta) return false;
    if (nextAction.type === "start_prototype" && primaryActionKey === "start-prototype") return false;
    if (nextAction.type === "retry_failed" && secondaryActionKeys.includes("retry-failed")) return false;
    if (nextAction.type === "start_generation" && secondaryActionKeys.includes("generate-all")) return false;
    return true;
  };

  const topPrimaryAction: UiAction | null = (() => {
    if (!selectedProject) return null;
    const actionKey = primaryActionKey;
    if (actionKey === "start-prototype") {
      const hasSelection = selectedPages.size > 0;
      const defaultSampleCount = Math.min(3, slides.length || 0);
      return {
        key: "prototype",
        label: isBusy
          ? "启动中..."
          : hasSelection
          ? `打样已选 ${selectedPages.size} 页`
          : `打样 ${defaultSampleCount} 页`,
        onClick: () => handleStartGeneration(hasSelection, true),
        variant: "primary",
        disabled: isBusy,
      };
    }
    if (actionKey === "confirm-prototype") {
      return {
        key: "confirm-prototype",
        label: isBusy ? "生成中..." : "确认打样，生成全部",
        onClick: () => handleConfirmPrototype(),
        variant: "primary",
        disabled: isBusy,
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
    const actionKeys = secondaryActionKeys;
    const actions: UiAction[] = [];
    if (actionKeys.includes("templates")) {
      actions.push({
        key: "templates",
        label: "查看模板",
        onClick: () => setShowTemplateRecommender(true),
        variant: "secondary",
      });
    }
    if (actionKeys.includes("generate-all")) {
      actions.push({
        key: "generate-all",
        label: "直接生成全部",
        onClick: () => handleStartGeneration(false, false),
        variant: "link",
        disabled: isBusy,
      });
    }
    if (actionKeys.includes("toggle-prototype-view")) {
      actions.push({
        key: "toggle-prototype-view",
        label: showPrototypePreview ? "返回全局预览" : "查看打样结果",
        onClick: () => setShowPrototypePreview((v) => !v),
        variant: "secondary",
        disabled: isBusy,
      });
    }
    if (actionKeys.includes("resample")) {
      actions.push({
        key: "resample",
        label: "重新打样",
        onClick: () => handleStartGeneration(false, true),
        variant: "secondary",
        disabled: isBusy,
      });
    }
    if (actionKeys.includes("retry-failed")) {
      actions.push({
        key: "retry-failed",
        label: isBusy ? "重试中..." : "一键重试失败页",
        onClick: handleRetryAllFailed,
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
        onClick: () => handleStartGeneration(false, false),
        variant: "secondary",
        disabled: isBusy,
      });
    }
    return actions;
  })();

  const actionClassName = (variant: UiAction["variant"] = "secondary") => {
    const base = "text-sm px-3 py-1 rounded disabled:opacity-50 whitespace-nowrap";
    if (variant === "primary") return `${base} bg-blue-600 text-white hover:bg-blue-700`;
    if (variant === "danger") return `${base} bg-red-50 text-red-600 hover:bg-red-100 border border-red-100`;
    if (variant === "link") return `${base} text-gray-500 hover:text-gray-700 underline px-1`;
    return `${base} bg-gray-100 text-gray-700 hover:bg-gray-200`;
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

  // 卡片间隙插入触发区：竖条（桌面端）/ 横条（移动端），hover 时显示 +
  const InsertGap = ({ onClick, title }: { onClick: () => void; title: string }) => (
    <div
      className="group relative flex-shrink-0 w-6 h-[300px] max-md:w-full max-md:h-6 flex items-center justify-center cursor-pointer"
      onClick={onClick}
      title={title}
    >
      <div className="w-px h-full max-md:w-full max-md:h-px bg-gray-200 group-hover:bg-blue-300 transition-colors absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2" />
      <div className="opacity-0 group-hover:opacity-100 transition-all bg-white border border-gray-300 text-gray-500 hover:text-blue-600 hover:border-blue-400 hover:bg-blue-50 rounded-full w-6 h-6 flex items-center justify-center text-sm relative z-10 shadow-sm hover:shadow-md hover:scale-110">+</div>
    </div>
  );

  const activeProgress = workflowProgressCounts(currentProjectStatus);
  const activeProgressLabel =
    currentProjectStatus?.progress?.label ||
    (activeRun?.kind === "style_proposal" ? "风格提案生成进度" : activeRun?.kind === "visual_prompts" ? "画面描述生成进度" : "批量生成进度");

  return (
    <div className="flex h-screen w-screen bg-gray-50 text-gray-900 overflow-hidden">
      {/* 左栏：项目导航 */}
      {!leftCollapsed && (
        <aside
          className="border-r bg-white flex flex-col flex-shrink-0 transition-none"
          style={{ width: leftWidth }}
        >
          <div className="p-3 border-b flex items-center justify-between">
            <h1 className="text-base font-bold">PPT GOD</h1>
            <button
              onClick={() => setLeftCollapsed(true)}
              className="text-gray-400 hover:text-gray-600 text-xs px-1"
              title="收起"
            >
              ◀
            </button>
          </div>
          <div className="p-3">
            <button
              className="w-full bg-blue-600 text-white text-sm rounded py-1 hover:bg-blue-700"
              onClick={() => setShowCreateModal(true)}
            >
              + 新建项目
            </button>
          </div>
          <div className="flex-1 overflow-auto">
            {projects.length === 0 && (
              <div className="p-4 text-center">
                <div className="text-3xl mb-2">📂</div>
                <div className="text-sm text-gray-500 mb-1">还没有项目</div>
                <div className="text-xs text-gray-400">点击上方「新建项目」开始创建你的第一份 PPT</div>
              </div>
            )}
            {projects.map((p) => (
              <div
                key={p.id}
                onClick={() => {
                  if (editingProjectId !== p.id) {
                    // 清理进行中的请求和 refs，防止跨项目状态污染
                    if (abortRef.current) {
                      abortRef.current.abort();
                      abortRef.current = null;
                    }
                    isConfirmingRef.current = false;
                    softLockWarnedRef.current = false;
                    setChatLoading(false);
                    clearTransientProjectState();
                    setSelectedProject(p);
                    setShowPrototypePreview(true);
                    // 根据项目已有状态推断阶段：
                    // - content_plan_confirmed=true 说明内容已确认，进入视觉总监
                    // - 有 selected_style 说明已走过视觉阶段，保持视觉总监角色
                    // - 否则停留在内容总监
                    const isPlanConfirmed = !!(p as any).content_plan_confirmed;
                    setContentPlanConfirmed(isPlanConfirmed);
                    if (p.selected_style || isPlanConfirmed) {
                      setCurrentAgentRole("visual");
                    } else {
                      setCurrentAgentRole("content");
                    }
                  }
                }}
                className={`px-3 py-2 border-b cursor-pointer ${
                  selectedProject?.id === p.id ? "bg-blue-50 border-blue-200" : "hover:bg-gray-100"
                }`}
              >
                {editingProjectId === p.id ? (
                  <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
                    <input
                      className="flex-1 border rounded px-2 py-1 text-sm"
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
                      className="text-xs bg-blue-600 text-white px-2 py-1 rounded hover:bg-blue-700"
                    >
                      保存
                    </button>
                    <button
                      onClick={() => setEditingProjectId(null)}
                      className="text-xs bg-gray-200 text-gray-600 px-2 py-1 rounded hover:bg-gray-300"
                    >
                      取消
                    </button>
                  </div>
                ) : (
                  <>
                    <div className="flex items-center justify-between">
                      <div className="font-medium text-sm truncate flex-1 flex items-center gap-1.5">
                        {p.title}
                        {p.has_unread_notification && (
                          <span className="inline-block w-2 h-2 rounded-full bg-red-500 flex-shrink-0" title={p.unread_notification_message || "有新动态"} />
                        )}
                      </div>
                      <div className="flex items-center gap-1 ml-2">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleStartEdit(p);
                          }}
                          className="text-xs text-gray-400 hover:text-blue-600 px-1"
                          title="编辑"
                        >
                          编辑
                        </button>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDeleteProject(p.id);
                          }}
                          className="text-xs text-gray-400 hover:text-red-600 px-1"
                          title="删除"
                        >
                          删除
                        </button>
                      </div>
                    </div>
                    <div className="text-[11px] text-gray-500 mt-0.5 truncate">
                      {statusLabel[p.status] || p.status} · {p.selected_style?.name || p.style_id || "默认风格"}
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
          className="flex-shrink-0 w-7 border-r bg-white hover:bg-gray-50 flex items-center justify-center text-gray-400 hover:text-gray-600 text-xs"
          title="展开项目栏"
        >
          ▶
        </button>
      ) : (
        <div
          onMouseDown={(e) => startResize("left", e)}
          className="w-0.5 flex-shrink-0 cursor-col-resize bg-gray-200 hover:bg-blue-400 active:bg-blue-500 transition-colors"
          title="拖动调节列宽"
        />
      )}

      {/* 中栏：主预览区 */}
      <main className="flex-1 flex flex-col min-w-0">
        <header className="h-12 border-b border-slate-200 bg-white flex items-center px-4 justify-between gap-3">
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
            {selectedProject && slides.length > 0 && (currentStatus === "prompt_ready" || currentStatus === "failed") && (
              <div className="flex items-center gap-2 mt-0.5 text-xs text-slate-600">
                <span className="text-slate-500">选页打样：</span>
                <button onClick={selectAll} className="text-blue-600 hover:underline font-medium">全选</button>
                <button onClick={clearSelection} className="text-slate-400 hover:underline">清空</button>
                <span className="text-slate-300">|</span>
                <span>已选 {selectedPages.size} / {slides.length} 页</span>
              </div>
            )}
            {selectedProject && currentStatus === "prototype_ready" && (
              <div className="text-xs text-slate-400 mt-0.5">
                当前视图：{showPrototypePreview ? "打样结果" : "全局预览"}。视图切换不会改变项目数据。
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
          <div className="px-6 py-3 bg-gradient-to-r from-slate-50 via-white to-slate-50 border-b border-slate-200">
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
                      className={`flex items-center gap-2 px-3 py-1.5 rounded-lg transition-all duration-200 ${
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
        {selectedProject && referenceImages.length > 0 || (selectedProject && currentAgentRole === "visual" && contentPlanConfirmed) ? (
          <div className="border-b border-gray-200">
            {/* 始终显示的紧凑栏，点击 toggle */}
            <div
              className="flex items-center gap-2 px-3 py-1.5 bg-gray-50 cursor-pointer hover:bg-gray-100 transition-colors"
              onClick={() => setAssetsBarExpanded((v) => !v)}
            >
              <span className="text-xs text-gray-600">
                项目素材
                {referenceImages.length > 0 && (
                  <> · {referenceImages.filter((r) => r.role === "logo").length > 0 && "品牌 Logo "}
                    {referenceImages.filter((r) => r.role === "visual_asset").length > 0 && `${referenceImages.filter((r) => r.role === "visual_asset").length} 个核心资产 `}
                    {referenceImages.filter((r) => r.role === "style_ref").length > 0 && `${referenceImages.filter((r) => r.role === "style_ref").length} 张风格参考 `}
                    {referenceImages.filter((r) => r.role === "template").length > 0 && "版式模板 "}</>
                )}
                {referenceImages.length === 0 && (
                  <span className="text-gray-400"> · 点击上传</span>
                )}
              </span>
              <span className="ml-auto text-xs text-slate-400">
                {assetsBarExpanded ? "收起 ▲" : "展开 ▼"}
              </span>
            </div>
            {/* 展开态：完整面板 */}
            {assetsBarExpanded && (
              <div>
                <VisualAssetsPanel
                  referenceImages={referenceImages}
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
                      showToast(targetRef?.role === "logo" ? "Logo 设置已更新" : "核心资产已更新");
                      await loadReferenceImages(selectedProject.id);
                      if (targetRef?.role === "visual_asset") {
                        slides.forEach((s) => markSlideStale(s.id, "content"));
                      }
                      addSystemLog(targetRef?.role === "logo" ? "用户更新了 Logo 角标设置" : "用户更新了核心资产说明");
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
                      if (refId === referenceImages.find((r) => r.role === "template")?.id) {
                        setTemplatePages([]);
                      }
                      if (deletedRef && (deletedRef.role === "style_ref" || deletedRef.role === "logo" || deletedRef.role === "visual_asset")) {
                        slides.forEach((s) => markSlideStale(s.id, "content"));
                      }
                      if (deletedRef) {
                        const roleMap: Record<string, string> = { style_ref: "风格参考", logo: "品牌 Logo", template: "版式模板", visual_asset: "核心资产" };
                        addSystemLog(`用户删除了项目${roleMap[deletedRef.role] || "素材"}`);
                      }
                      await loadProjects();
                    } catch (err: any) {
                      showToast("删除失败：" + (err.message || "未知错误"), "error");
                    }
                  }}
                  onImageClick={(url) => {
                    const urls = referenceImages.map((r: any) => `${API_BASE}${r.url}`);
                    const index = urls.indexOf(url);
                    setGalleryModal({ urls, index: index >= 0 ? index : 0, title: "设计素材" });
                  }}
                />
              </div>
            )}
          </div>
        ) : null}

        <div className="flex-1 overflow-auto p-3">
          {!selectedProject ? (
            <div className="flex items-center justify-center h-full text-gray-400">
              <div className="text-center">
                <div className="text-4xl mb-4">📊</div>
                <div>选择一个项目开始</div>
              </div>
            </div>
          ) : slides.length === 0 ? (
            <div className="flex items-center justify-center h-full bg-gray-50">
              <div className="max-w-md w-full mx-auto text-center">
                <div className="text-5xl mb-4">✨</div>
                <h2 className="text-xl font-bold text-gray-900 mb-2 truncate max-w-[400px] mx-auto" title={selectedProject.title}>
                  {selectedProject.title}
                </h2>
                <p className="text-sm text-gray-500 mb-6">
                  这是一个全新的项目。请在右侧 Agent 面板中描述你的 PPT 需求。<br />
                  你可以直接输入主题、粘贴文档内容，或上传文件。
                </p>
                <div className="inline-flex items-center gap-2 text-xs text-blue-600 bg-blue-50 px-4 py-2 rounded-full">
                  <span>Agent 会引导你完成内容确认</span>
                </div>
              </div>
            </div>
          ) : showTemplateRecommender && templatePages.length > 0 ? (
            <div className="p-4">
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
                    const fresh = await fetchProjects();
                    const updated = fresh.find((p: Project) => p.id === selectedProject.id);
                    if (updated) setSelectedProject(updated);
                    setShowTemplateRecommender(false);
                  } catch (err: any) {
                    showToast("保存模板选择失败：" + (err.message || "未知错误"), "error");
                  }
                }}
              />
            </div>
          ) : currentStatus === "prototype_ready" && showPrototypePreview ? (
            <div className="p-4 max-w-5xl mx-auto">
              <div className="flex items-center justify-between mb-4">
                <div>
                  <h2 className="text-base font-bold text-gray-800">效果预览确认</h2>
                  <p className="text-xs text-gray-500">确认满意后即可启动批量生成</p>
                </div>
                <button
                  onClick={() => setShowPrototypePreview(false)}
                  className="text-xs text-blue-600 hover:text-blue-800 underline"
                >
                  返回全局预览
                </button>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
                {slides
                  .filter((s) => s.status === "completed" && s.image_path)
                  .map((slide) => (
                    <div
                      key={slide.id}
                      className="border rounded-lg p-3 flex flex-col items-center bg-white"
                    >
                      <div className="text-xs text-gray-500 mb-2 font-medium">
                        {typeLabel[slide.type] || slide.type} · 第 {slide.page_num} 页
                      </div>
                      {slide.image_path ? (
                        <SlideImageWithLogo
                          slide={slide}
                          logo={projectLogo}
                          src={getSlideImageUrl(slide.image_path, slide.status, imageRefreshMap[slide.id])}
                          alt={`Slide ${slide.page_num}`}
                          className="aspect-video w-full rounded overflow-hidden bg-gray-100 mb-2 cursor-pointer"
                          imgClassName="w-full h-full object-cover"
                          onClick={() => {
                            const gallerySlides = slides
                              .filter((s) => s.status === "completed" && s.image_path)
                              .sort((a, b) => a.page_num - b.page_num);
                            const allUrls = gallerySlides.map((s) => getSlideImageUrl(s.image_path!, s.status, imageRefreshMap[s.id]));
                            const url = getSlideImageUrl(slide.image_path!, slide.status, imageRefreshMap[slide.id]);
                            const index = allUrls.indexOf(url);
                            setGalleryModal({ urls: allUrls, index: index >= 0 ? index : 0, title: "PPT 预览", slides: gallerySlides, logo: projectLogo });
                          }}
                          onError={(e) => {
                            (e.target as HTMLImageElement).style.display = "none";
                          }}
                        />
                      ) : (
                        <div className="aspect-video w-full rounded bg-gray-100 mb-2 flex items-center justify-center text-xs text-gray-400">
                          图片加载中...
                        </div>
                      )}
                      {staleMap[slide.id]?.content && !["draft", "planning", "content_plan_ready"].includes(currentStatus) && (
                        <div className="mt-1 text-2xs text-blue-600 bg-blue-50 rounded px-2 py-0.5">需更新画面方案</div>
                      )}
                      {staleMap[slide.id]?.visual && (
                        <div className="mt-1 text-2xs text-orange-600 bg-orange-50 rounded px-2 py-0.5">需更新画面方案</div>
                      )}
                      {staleMap[slide.id]?.image && (
                        <div className="mt-1 text-2xs text-purple-600 bg-purple-50 rounded px-2 py-0.5">需重新生成图片</div>
                      )}
                      {slide.status === "failed" && (
                        <button
                          onClick={() => handleRetry(slide.id)}
                          disabled={isBusy}
                          className="text-xs bg-red-50 text-red-600 px-2 py-1 rounded hover:bg-red-100 disabled:opacity-50"
                        >
                          重试
                        </button>
                      )}
                    </div>
                  ))}
              </div>
              {/* 全部页面概览 */}
              <div className="mb-6">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-bold text-gray-700">📑 全部页面概览（共 {slides.length} 页）</h3>
                  <span className="text-xs text-gray-400">已生成页面可点击预览</span>
                </div>
                <div className="grid grid-cols-4 sm:grid-cols-6 md:grid-cols-8 gap-2">
                  {slides.map((slide) => {
                    const headline = slide.content_json?.headline || slide.content_json?.text_content?.headline || "";
                    return (
                      <div
                        key={slide.id}
                        className="border rounded p-2 flex flex-col items-center text-center border-gray-200 bg-white"
                        title={headline}
                      >
                        <div className={`text-2xs px-1.5 py-0.5 rounded mb-1 font-medium ${
                          typeColor[slide.type] || "bg-gray-100 text-gray-600"
                        }`}>
                          {typeLabel[slide.type] || slide.type}
                        </div>
                        <div className="text-2xs text-gray-400 mb-1">P{slide.page_num}</div>
                        {slide.image_path ? (
                          <SlideImageWithLogo
                            slide={slide}
                            logo={projectLogo}
                            src={getSlideImageUrl(slide.image_path, slide.status, imageRefreshMap[slide.id])}
                            alt={`Slide ${slide.page_num}`}
                            className="aspect-video w-full rounded overflow-hidden bg-gray-100 cursor-pointer"
                            imgClassName="w-full h-full object-cover"
                            onClick={() => {
                              const gallerySlides = slides
                                .filter((s) => s.status === "completed" && s.image_path)
                                .sort((a, b) => a.page_num - b.page_num);
                              const allUrls = gallerySlides.map((s) => getSlideImageUrl(s.image_path!, s.status, imageRefreshMap[s.id]));
                              const url = getSlideImageUrl(slide.image_path!, slide.status, imageRefreshMap[slide.id]);
                              const index = allUrls.indexOf(url);
                              setGalleryModal({ urls: allUrls, index: index >= 0 ? index : 0, title: "PPT 预览", slides: gallerySlides, logo: projectLogo });
                            }}
                            onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                          />
                        ) : (
                          <div className="aspect-video w-full rounded bg-gray-50 flex items-center justify-center">
                            <span className="text-2xs text-gray-300">未生成</span>
                          </div>
                        )}
                        <div className="text-2xs text-gray-500 mt-1 truncate w-full leading-tight">
                          {headline || "未命名"}
                        </div>
                        {staleMap[slide.id]?.content && !["draft", "planning", "content_plan_ready"].includes(currentStatus) && (
                          <div className="mt-0.5 text-2xs text-blue-600 bg-blue-50 rounded px-1 truncate">文字已改</div>
                        )}
                        {staleMap[slide.id]?.visual && (
                          <div className="mt-0.5 text-2xs text-orange-600 bg-orange-50 rounded px-1 truncate">画面已改</div>
                        )}
                        {staleMap[slide.id]?.image && (
                          <div className="mt-0.5 text-2xs text-purple-600 bg-purple-50 rounded px-1 truncate">待生成</div>
                        )}
                        {slide.status === "failed" && (
                          <button
                            onClick={() => handleRetry(slide.id)}
                            disabled={isBusy}
                            className="mt-1 text-2xs bg-red-50 text-red-600 px-1.5 py-0.5 rounded hover:bg-red-100 disabled:opacity-50"
                          >
                            重试
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>

              <div className="flex items-center justify-center gap-3">
                <button
                  onClick={() => handleConfirmPrototype()}
                  disabled={isBusy}
                  className="text-sm bg-rose-600 text-white px-6 py-2 rounded hover:bg-rose-700 disabled:opacity-50"
                >
                  {isBusy ? "启动中..." : "确认预览效果，开始批量生成"}
                </button>
                <button
                  onClick={() => handleStartGeneration(false, true)}
                  disabled={isBusy}
                  className="text-sm bg-gray-200 text-gray-700 px-6 py-2 rounded hover:bg-gray-300 disabled:opacity-50"
                >
                  {isBusy ? "启动中..." : "重新打样"}
                </button>
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
              imageCacheKey={imageRefreshMap[editingSlide.id]}
              slideVersions={slideVersionsMap[editingSlide.id] || []}
              onRestoreVersion={(versionId) => handleRestoreVersion(editingSlide.id, versionId)}
              onDeleteVersion={(versionId) => handleDeleteVersion(editingSlide.id, versionId)}
              unescapeText={unescapeText}
              onImageClick={(url) => {
                if (url.includes("/uploads/")) {
                  const refUrls = editingSlide?.reference_images?.map((r: any) => `${API_BASE}${r.url}`) || [];
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
            />
          ) : (
            <>
              {/* 风格已选定：紧凑条，可展开 */}
              {selectedProject?.selected_style && (
                currentStatus === "visual_ready" ||
                currentStatus === "prompt_ready" ||
                currentStatus === "generating" ||
                currentStatus === "prototype_ready" ||
                currentStatus === "completed"
              ) && (
                <div className="mb-2 bg-indigo-50 border border-indigo-200 rounded-lg overflow-hidden">
                  {/* 紧凑栏（始终显示） */}
                  <div
                    className="flex items-center gap-2 px-3 py-1.5 cursor-pointer hover:bg-indigo-100/50 transition-colors"
                    onClick={() => setStyleBarExpanded((v) => !v)}
                  >
                    <span className="text-xs font-medium text-indigo-800 truncate">
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
                    <span className="ml-auto text-2xs text-indigo-400">
                      {styleBarExpanded ? "收起" : "展开"}
                    </span>
                  </div>
                  {/* 展开详情 */}
                  {styleBarExpanded && (
                    <div className="px-3 pb-2 border-t border-indigo-100">
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
                      <div className="text-[11px] text-indigo-700">
                        氛围：{selectedProject.selected_style.mood || "—"} · 字体：{selectedProject.selected_style.font || "—"}
                      </div>
                      {selectedProject.selected_style.description && (
                        <p className="text-[11px] text-indigo-600 mt-0.5 leading-relaxed">
                          {selectedProject.selected_style.description}
                        </p>
                      )}
                    </div>
                  )}
                </div>
              )}
              <div className="flex flex-wrap w-full">
                {/* 第一页之前：悬浮插入区 */}
                {slides.length > 0 && !isBusy && !chatLoading && (
                  <InsertGap
                    onClick={() => handleInsertSlideBefore(slides[0].id)}
                    title="在第一页之前插入"
                  />
                )}
                {slides.map((slide, index) => {
                const content = slide.content_json || {};
                const text = content.text_content || {};
                const visual = slide.visual_json || {};
                const hasVisualDescription = Boolean(visual.visual_description && String(visual.visual_description).trim());
                const hasPromptText = Boolean(slide.prompt_text && String(slide.prompt_text).trim());
                const isSelected = selectedPages.has(slide.page_num);
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
                          handleStopChat();
                          setCurrentAgentRole("finetune");
                          setFinetuneTargetSlideId(slide.id);
                          ensureFinetuneGreetingForSlide(slide.id);
                          loadSlideVersions(slide.id);
                        }
                        handleEnterEdit(slide);
                      }
                    }}
                    className={`group relative bg-white rounded-lg border border-slate-200 p-3 shadow-sm flex flex-col cursor-pointer hover:shadow-lg hover:border-blue-400 transition-all h-[300px] overflow-hidden w-[calc((100%-4.5rem)/3)] min-w-[260px] flex-shrink-0 max-md:w-full ${
                      isSelected && (currentStatus === "prompt_ready" || currentStatus === "failed")
                        ? "ring-2 ring-blue-400"
                        : ""
                    } ${finetuneTargetSlideId === slide.id && currentAgentRole === "finetune" ? "ring-2 ring-amber-400 border-amber-300" : ""} ${dragOverSlideId === slide.id ? "border-dashed border-blue-400 bg-blue-50" : ""} ${dragSlideId === slide.id ? "opacity-50" : ""}`}
                  >
                    <div className="flex items-center justify-between mb-1 shrink-0">
                      <div className="flex items-center gap-1.5">
                        {(currentStatus === "prompt_ready" || currentStatus === "failed") && (
                          <input
                            type="checkbox"
                            checked={isSelected}
                            onClick={(e) => e.stopPropagation()}
                            onChange={(e) => {
                              e.stopPropagation();
                              togglePage(slide.page_num);
                            }}
                            className="cursor-pointer"
                          />
                        )}
                        <span className="text-xs text-slate-400 font-mono">P{slide.page_num}</span>
                        {statusText[slide.status] && <span className="text-sm">{statusText[slide.status]}</span>}
                      </div>
                      <div className="flex items-center gap-1">
                        <SlideReadinessIcons hasVisual={hasVisualDescription} hasPrompt={hasPromptText} />
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDeleteSlide(slide.id);
                          }}
                          disabled={isBusy || chatLoading}
                          className="text-xs text-gray-400 hover:text-red-500 px-1 leading-none disabled:opacity-30"
                          title="删除"
                        >
                          删
                        </button>
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
                          title={typeof text.body === "string" ? mdToPlainText(text.body) : text.body.map((item: any) => typeof item === "string" ? item : item?.content || "").join("\n")}
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
                      <SlideImageWithLogo
                        slide={slide}
                        logo={projectLogo}
                        src={getSlideImageUrl(slide.image_path, slide.status, imageRefreshMap[slide.id])}
                        alt={"Slide " + slide.page_num}
                        className="flex-1 min-h-0 w-full rounded-md overflow-hidden cursor-pointer mb-1 border border-slate-100 group/img hover:shadow-md hover:border-blue-300 transition-all duration-200"
                        imgClassName="w-full h-full object-cover group-hover/img:scale-105 transition-transform duration-300"
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

                    {/* 底部栏：参考图 + 重试 */}
                    <div className="shrink-0">
                      {/* 页面级参考图（紧凑模式） */}
                      <div className="flex items-center gap-1 shrink-0">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleUploadPageRef(slide.id);
                          }}
                          disabled={isBusy || chatLoading}
                          className="text-xs bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded hover:bg-gray-200 disabled:opacity-50 leading-none"
                        >
                          + 本页参考图
                        </button>
                        {slide.reference_images && slide.reference_images.length > 0 && (
                          <div className="flex gap-0.5 flex-nowrap overflow-x-auto">
                            {slide.reference_images.map((ref: any) => (
                              <div key={ref.id} className="relative group flex-shrink-0">
                                <img
                                  src={`${API_BASE}${ref.url}`}
                                  alt="ref"
                                  className="w-7 h-7 rounded object-cover border cursor-pointer"
                                  title={`${ref.process_mode === "blend" ? "融合" : ref.process_mode === "crop" ? "裁剪" : "原图"} — 点击查看大图`}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    const allRefUrls = slides
                                      .flatMap((s) => s.reference_images?.map((r: any) => `${API_BASE}${r.url}`) || [])
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
      {rightCollapsed && (
        <button
          onClick={() => setRightCollapsed(false)}
          className="flex-shrink-0 w-7 border-l bg-white hover:bg-gray-50 flex items-center justify-center text-gray-400 hover:text-gray-600 text-xs"
          title="展开 Agent 助手"
        >
          ◀
        </button>
      )}
      {!rightCollapsed && (
        <div
          onMouseDown={(e) => startResize("right", e)}
          className="w-0.5 flex-shrink-0 cursor-col-resize bg-gray-200 hover:bg-blue-400 active:bg-blue-500 transition-colors"
          title="拖动调节列宽"
        />
      )}
      {!rightCollapsed && (
      <aside
        className={`flex-shrink-0 border-l flex flex-col ${isDragging ? "bg-blue-50/50 ring-2 ring-blue-300 ring-inset" : "bg-white"}`}
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
        <div className="px-4 py-3 border-b bg-slate-50/50">
          <div className="flex items-center justify-between mb-2.5">
            <span className="font-semibold text-sm text-slate-700">
              {currentAgentRole === "finetune" ? "微调工作台" : "Agent 助手"}
            </span>
            <div className="flex items-center gap-1">
              <button
                onClick={() => {
                  if (chatMessages.length === 0) return;
                  showConfirm("确定要清空当前 Agent 的聊天记录吗？项目内容不会被删除。").then((confirmed) => {
                    if (confirmed) {
                      setActiveChatMessages([]);
                      showToast("聊天记录已清空", "success");
                    }
                  });
                }}
                className="text-slate-400 hover:text-red-500 text-sm px-1.5 py-0.5 rounded hover:bg-red-50 transition-colors"
                title="清空对话"
                disabled={chatMessages.length === 0}
              >
                清空
              </button>
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
          <div className="flex items-center gap-1.5">
            <button
              onClick={() => {
                if (currentAgentRole !== "content") {
                  handleStopChat();
                  setCurrentAgentRole("content");
                  ensureContentGreetingIfNeeded();
                }
              }}
              className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-sm font-medium transition-all ${
                currentAgentRole === "content"
                  ? "bg-blue-100 text-blue-700 ring-1 ring-blue-300"
                  : "bg-white text-slate-500 hover:bg-slate-100 border border-slate-200"
              }`}
            >
              <span>内容总监</span>
            </button>
            <span className="text-slate-300 text-xs">|</span>
            <button
              onClick={() => {
                if (!contentPlanConfirmed) {
                  showToast("请先确认内容规划，再切换到视觉总监", "info");
                  return;
                }
                if (currentAgentRole !== "visual") {
                  handleStopChat();
                  setCurrentAgentRole("visual");
                  ensureVisualGreetingIfNeeded();
                }
              }}
              disabled={!contentPlanConfirmed}
              className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-sm font-medium transition-all ${
                !contentPlanConfirmed
                  ? "bg-slate-50 text-slate-300 cursor-not-allowed"
                  : currentAgentRole === "visual"
                  ? "bg-purple-100 text-purple-700 ring-1 ring-purple-300"
                  : "bg-white text-slate-500 hover:bg-slate-100 border border-slate-200"
              }`}
            >
              <span>视觉总监</span>
              {!contentPlanConfirmed && <span className="text-xs ml-0.5">已锁定</span>}
            </button>
            <span className="text-slate-300 text-xs">|</span>
            <button
              onClick={() => {
                const hasAnyCompletedSlide = slides.some((s) => s.status === "completed" && s.image_path);
                if (!selectedProject || !hasAnyCompletedSlide) {
                  showToast("至少需要有一页生成图片后才能使用单页微调", "info");
                  return;
                }
                if (currentAgentRole !== "finetune") {
                  handleStopChat();
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
              className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-sm font-medium transition-all ${
                !selectedProject || !slides.some((s) => s.status === "completed" && s.image_path)
                  ? "bg-slate-50 text-slate-300 cursor-not-allowed"
                  : currentAgentRole === "finetune"
                  ? "bg-amber-100 text-amber-700 ring-1 ring-amber-300"
                  : "bg-white text-slate-500 hover:bg-slate-100 border border-slate-200"
              }`}
            >
              <span>单页微调</span>
              {(!selectedProject || !slides.some((s) => s.status === "completed" && s.image_path)) && (
                <span className="text-xs ml-0.5">已锁定</span>
              )}
            </button>
          </div>
        </div>
        {/* Agent 模式切换栏：内容规划阶段和视觉总监阶段都显示 */}
        {selectedProject && slides.length > 0 && (currentStatus === "planning" || currentAgentRole === "visual") && (
          <div className="px-4 py-2 border-b bg-white flex items-center justify-between">
            <div className="flex items-center gap-1 text-xs">
              <span className="text-slate-500">调整范围：</span>
              <button
                onClick={() => setAgentMode("page")}
                className={`px-2.5 py-1 rounded-md transition-colors text-sm ${
                  agentMode === "page"
                    ? "bg-blue-600 text-white"
                    : "bg-white text-slate-600 hover:bg-slate-100 border border-slate-200"
                }`}
                title={currentAgentRole === "visual" ? "只修改当前正在编辑的那一页的视觉描述" : "只修改当前正在编辑的那一页"}
              >
                当前页
              </button>
              <button
                onClick={() => setAgentMode("global")}
                className={`px-2.5 py-1 rounded-md transition-colors text-sm ${
                  agentMode === "global"
                    ? "bg-blue-600 text-white"
                    : "bg-white text-slate-600 hover:bg-slate-100 border border-slate-200"
                }`}
                title={currentAgentRole === "visual" ? "调整所有页面的视觉描述" : "调整所有页面的文字内容"}
              >
                全局
              </button>
            </div>
            {currentStatus === "planning" && (
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
            )}
          </div>
        )}
        <div
          ref={chatContainerRef}
          className="flex-1 overflow-auto space-y-3 p-3"
        >
          {!selectedProject && (
            <div className="bg-blue-50 p-3 rounded text-sm">
              你好！我可以帮你生成 PPT。请先新建或选择一个项目。
            </div>
          )}
          {/* 已上传文档折叠面板 */}
          {selectedProject && documents.length > 0 && (
            <div className="bg-gray-50 rounded border border-gray-200 overflow-hidden">
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
                      <span className="text-gray-700 truncate max-w-[200px]" title={doc.filename}>
                        {doc.filename}
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

          {selectedProject && currentStatus === "draft" && (
            <div className="bg-blue-50 rounded space-y-3 p-3 text-sm">
              <div className="font-medium truncate max-w-[300px]" title={selectedProject.title}>欢迎来到 {selectedProject.title === "未命名项目" ? "你的新项目" : selectedProject.title}</div>
              <div>这是一个全新的项目。请告诉我你想做什么主题的 PPT？</div>
              <div className="text-blue-600 text-xs">
                支持直接输入主题、粘贴内容，或拖拽上传 PDF / Word / PPT / Markdown 等文档。
              </div>
              {/* Quick action cards */}
              <div className="grid grid-cols-2 gap-2 mt-2">
                {[
                  { label: "销售汇报", prompt: "我要做一份销售汇报PPT，面向公司管理层，总结上季度业绩、关键数据亮点和下一步计划。" },
                  { label: "教学课件", prompt: "我要做一份教学课件，面向大学生，介绍人工智能的基础概念和应用场景。" },
                  { label: "产品发布", prompt: "我要做一份产品发布PPT，面向潜在客户，展示产品核心功能、竞争优势和定价策略。" },
                  { label: "个人作品集", prompt: "我要做一份个人作品集PPT，展示我的设计案例、项目经历和职业亮点。" },
                ].map((item) => (
                  <button
                    key={item.label}
                    onClick={() => {
                      setChatInput(item.prompt);
                      if (chatInputRef.current) {
                        chatInputRef.current.focus();
                      }
                    }}
                    className="flex items-center gap-2 bg-white border border-blue-100 rounded px-3 py-2 text-sm text-gray-700 hover:border-blue-300 hover:shadow-sm transition-all text-left"
                  >
                    <span>{item.label}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
          {isDragging && (
            <div className="flex items-center justify-center h-32 border-2 border-dashed border-blue-400 rounded-lg bg-blue-50 text-blue-600 text-sm">
              松开即可上传文档
            </div>
          )}

          {/* 聊天消息 */}
          {chatMessages.map((msg, i) => {
            if (isTransientRunMessage(msg)) return null;
            return (
            <div key={msg.id || i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className="max-w-[80%] group">
                {editingMessageIndex === i ? (
                  <div className="flex flex-col gap-2">
                    <textarea
                      className="w-full border rounded p-2 text-sm min-h-[60px] resize-none"
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
                        className="text-xs bg-gray-200 text-gray-700 px-2 py-1 rounded hover:bg-gray-300"
                      >
                        取消
                      </button>
                      <button
                        onClick={handleSaveMessageEdit}
                        className="text-xs bg-blue-600 text-white px-2 py-1 rounded hover:bg-blue-700"
                      >
                        保存并重新发送
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    {msg.role === "agent" && msg.agentRole === "visual" && (
                      <div className="text-xs text-purple-600 mb-1 font-medium">视觉总监</div>
                    )}
                    {msg.role === "agent" && msg.agentRole === "content" && slides.length > 0 && currentStatus === "planning" && (
                      <div className="text-xs text-blue-600 mb-1 font-medium">内容总监</div>
                    )}
                    {msg.role === "agent" && msg.agentRole === "finetune" && (
                      <div className="text-xs text-amber-600 mb-1 font-medium">单页微调</div>
                    )}
                    <div
                      className={`p-3 rounded text-sm ${
                        msg.role === "user"
                          ? "bg-blue-600 text-white rounded-br-none"
                          : msg.role === "system"
                          ? "bg-gray-50 text-gray-500 rounded-bl-none text-xs border border-gray-200"
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
                          <span className="text-gray-600 text-sm">{msg.content}</span>
                        </div>
                      ) : msg.role === "system" ? (
                        <div className="flex items-start gap-1.5">
                          <span className="whitespace-pre-wrap leading-relaxed">{msg.content}</span>
                        </div>
                      ) : msg.role === "user" ? (
                        (() => {
                          const parts = msg.content.split("\n📎 ");
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
                                      📎 {att}
                                    </span>
                                  ))}
                                </div>
                              )}
                            </div>
                          );
                        })()
                      ) : (
                        <div dangerouslySetInnerHTML={{ __html: renderMarkdown(unescapeText(msg.content), true) }} />
                      )}
                      {msg.role === "agent" && (msg.action === "propose_plan" || msg.action === "generate_plan") && msg.positioning && (
                        <div className="mt-3 bg-white border border-blue-200 rounded-lg p-4 shadow-sm">
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
                          <button
                            onClick={async () => {
                              if (!selectedProject || !msg.topic) return;
                              await startContentPlanPoll(selectedProject.id, msg.topic, "button", msg.positioning?.estimated_pages);
                            }}
                            disabled={isBusy || chatLoading}
                            className="mt-3 w-full bg-blue-600 text-white text-sm py-2 rounded hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
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
                        </div>
                      )}
                      {msg.role === "agent" && shouldRenderMessageNextAction(msg) && (
                        <div className="mt-3 border-t border-slate-200 pt-3">
                          {msg.nextAction!.description && (
                            <div className="text-xs text-slate-500 mb-2">{msg.nextAction!.description}</div>
                          )}
                          <button
                            onClick={() => handleAgentNextAction(msg.nextAction!, msg)}
                            disabled={isBusy || chatLoading}
                            className={`w-full text-sm py-2 rounded font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                              msg.agentRole === "visual"
                                ? "bg-purple-600 text-white hover:bg-purple-700"
                                : "bg-blue-600 text-white hover:bg-blue-700"
                            }`}
                          >
                            {isBusy ? "处理中..." : msg.nextAction!.label}
                          </button>
                        </div>
                      )}
                    </div>
                    {/* 视觉总监的风格提案卡片 - 每条消息携带自己的快照，互不干扰 */}
                    {msg.role === "agent" && msg.agentRole === "visual" && msg.styleProposals && msg.styleProposals.length > 0 && (
                      <ChatStyleProposal
                        proposals={msg.styleProposals}
                        onSelect={handleSelectStyle}
                        onAdjust={() => {
                          setVisualChatHistory((prev) => [
                            ...prev,
                            {
                              role: "agent",
                              content: "👉 请告诉我你的调整方向（如「更商务一点」「配色再暖一些」「想要极简感」），我会基于你的反馈重新生成提案。",
                              agentRole: "visual",
                            },
                          ]);
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
                          className="text-xs text-slate-400 hover:text-blue-600 px-1 disabled:opacity-30 disabled:cursor-not-allowed"
                          title="编辑"
                        >
                          编辑
                        </button>
                      )}
                      <button
                        onClick={() => handleDeleteMessage(i)}
                        disabled={chatLoading || isBusy}
                        className="text-xs text-slate-400 hover:text-red-600 px-1 disabled:opacity-30 disabled:cursor-not-allowed"
                        title="删除（回滚到此消息之前）"
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
              <div className="bg-gray-100 rounded text-sm text-gray-600 rounded-bl-none max-w-[80%] overflow-hidden">
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
                        Agent 正在思考...
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
                    <span>Agent 正在思考...</span>
                  </div>
                )}
              </div>
            </div>
          )}
          {/* 批量生成进度（生图 / 生成提示词）：跟在最新消息之后 */}
	          {selectedProject && hasActiveRun && currentProjectStatus && activeRun?.kind !== "content_plan" && (
	            <div className="flex justify-start">
	              <div className="bg-purple-50 border border-purple-200 rounded-lg text-sm text-purple-800 rounded-bl-none max-w-[80%] overflow-hidden w-72">
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
	                    {activeProgress.current} / {activeProgress.total} {activeProgress.unit}完成
	                    {activeProgress.failed > 0 && `，${activeProgress.failed} ${activeProgress.unit}失败`}
	                  </div>
	                </div>
	              </div>
	            </div>
	          )}
          {/* 内容规划动态进度卡片：仅在项目处于生成中状态时显示，防止过时进度残留 */}
          {selectedProject && currentAgentRole === "content" && activeRun?.kind === "content_plan" && currentContentPlanProgress && currentContentPlanProgress.stage && currentContentPlanProgress.stage !== "error" && (
            <div className="flex justify-start">
              <div className="bg-blue-50 border border-blue-200 rounded-lg text-sm text-gray-700 rounded-bl-none max-w-[80%] overflow-hidden w-72">
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
          {selectedProject && slides.length > 0 && currentStatus === "planning" && !contentPlanConfirmed && (
            <div className="mb-3 bg-emerald-50/80 border border-emerald-200 rounded-xl p-4 shadow-sm">
              <div className="flex items-center justify-between mb-2">
                <div className="text-sm text-emerald-800">
                  <span className="font-medium">内容规划已完成</span>
                  <span className="text-emerald-600 ml-1">· {slides.length} 页</span>
                </div>
              </div>
              <button
                onClick={handleConfirmContentPlan}
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
                    setVisualChatHistory((prev) => [
                      ...prev,
                      { role: "user", content: `📎 已上传风格参考：${file.name}`, agentRole: "visual" },
                    ]);
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
                    setVisualChatHistory((prev) => [
                      ...prev,
                      { role: "user", content: `🎯 已上传品牌 Logo：${file.name}`, agentRole: "visual" },
                    ]);
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
                  showToast("核心资产已添加");
                  await loadReferenceImages(selectedProject.id);
                  slides.forEach((s) => markSlideStale(s.id, "content"));
                  addSystemLog(`用户上传了核心资产「${file.name}」`);
                  if (currentAgentRole === "visual") {
                    setVisualChatHistory((prev) => [
                      ...prev,
                      { role: "user", content: `🖼️ 已上传核心资产：${file.name}`, agentRole: "visual" },
                    ]);
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
                    setVisualChatHistory((prev) => [
                      ...prev,
                      { role: "user", content: `📑 已上传版式模板：${file.name}`, agentRole: "visual" },
                    ]);
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

          {/* 视觉总监阶段：生成/重新生成提案按钮 */}
          {selectedProject && contentPlanConfirmed && currentAgentRole === "visual" && !selectedProject?.selected_style && (
            <div className="mb-3 bg-purple-50/80 border border-purple-200 rounded-xl p-4 shadow-sm">
              <div className="text-sm text-purple-800 mb-2">
                {styleProposalsInChat.length === 0 ? (
                  <>
                    <span className="font-medium">准备好生成方案了吗？</span>
                    <span className="text-purple-600 ml-1">· 有素材请上传，没有就直接生成</span>
                  </>
                ) : (
                  <>
                    <span className="font-medium">想要调整方案？</span>
                    <span className="text-purple-600 ml-1">· 上传新素材或点击重新生成</span>
                  </>
                )}
              </div>
              {/* 快捷上传入口 */}
              <div className="flex flex-wrap gap-2 mb-2">
                <button
                  onClick={() => logoInputRef.current?.click()}
                  disabled={uploadingLogo || isBusy || chatLoading}
                  className="flex-1 min-w-[90px] text-xs bg-white text-purple-700 px-2 py-1.5 rounded-lg border border-purple-200 hover:bg-purple-50 disabled:opacity-50 transition-colors"
                  title="上传主品牌 Logo；默认作为统一角标叠加，可在素材栏选择四角位置"
                >
                  {uploadingLogo ? "上传中..." : "+ 品牌 Logo"}
                </button>
                <button
                  onClick={() => visualAssetInputRef.current?.click()}
                  disabled={uploadingVisualAsset || isBusy || chatLoading}
                  className="flex-1 min-w-[90px] text-xs bg-white text-purple-700 px-2 py-1.5 rounded-lg border border-purple-200 hover:bg-purple-50 disabled:opacity-50 transition-colors"
                  title="上传产品图、主 KV、模特图等必须保真的素材；只在相关页面调用"
                >
                  {uploadingVisualAsset ? "上传中..." : "+ 核心资产"}
                </button>
                <button
                  onClick={() => styleRefInputRef.current?.click()}
                  disabled={uploadingStyleRef || isBusy || chatLoading}
                  className="flex-1 min-w-[90px] text-xs bg-white text-purple-700 px-2 py-1.5 rounded-lg border border-purple-200 hover:bg-purple-50 disabled:opacity-50 transition-colors"
                  title="上传风格参考；只提取配色、材质、构图气质，不要求图片本身出现"
                >
                  {uploadingStyleRef ? "上传中..." : "+ 风格参考"}
                </button>
                <button
                  onClick={() => templateInputRef.current?.click()}
                  disabled={uploadingTemplate || isBusy || chatLoading}
                  className="flex-1 min-w-[90px] text-xs bg-white text-purple-700 px-2 py-1.5 rounded-lg border border-purple-200 hover:bg-purple-50 disabled:opacity-50 transition-colors"
                  title="上传 PPT/PDF 版式模板；用于提取封面、目录、内容页等版式秩序"
                >
                  {uploadingTemplate ? "上传中..." : "+ 版式模板"}
                </button>
              </div>
              <button
                onClick={async () => {
                  if (!selectedProject) return;
                  const hasExistingProposal = styleProposalsInChat.length > 0;
                  // 路由到聊天接口：让视觉总监带着完整聊天历史 + 当前提案锚点重新决策
                  // 这样调整意见不会被丢弃，按钮和聊天走同一条管道
                  const fakeUserMsg = hasExistingProposal
                    ? "请基于当前最新的素材和我们之前的讨论，重新给我一套风格提案。"
                    : "请基于我已上传的素材帮我生成风格提案。";
                  handleSendChat(fakeUserMsg);
                }}
                disabled={styleProposalsLoading || isBusy || chatLoading}
                className="w-full bg-purple-600 text-white text-sm py-2.5 rounded-lg font-medium hover:bg-purple-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {styleProposalsLoading || isBusy || chatLoading
                  ? "正在生成..."
                  : styleProposalsInChat.length === 0
                    ? (referenceImages.length > 0 ? "素材已齐，开始生成" : "直接生成风格提案")
                    : "基于当前素材重新生成提案"}
              </button>
            </div>
          )}
          {/* draft 阶段文档上传区域 */}
          {currentStatus === "draft" && (
            <div className="mb-4">
              <input
                type="file"
                ref={docInputRef}
                className="hidden"
                accept=".pdf,.doc,.docx,.ppt,.pptx,.md,.txt,.csv,.json,.html,.htm"
                onChange={handleUploadDocument}
              />
              <div className="flex items-center gap-3 mb-2">
                <button
                  onClick={() => docInputRef.current?.click()}
                  disabled={uploadingDoc || isBusy || chatLoading}
                  className="text-sm bg-gray-100 text-gray-700 px-3 py-1.5 rounded hover:bg-gray-200 disabled:opacity-50 transition-colors"
                >
                  {uploadingDoc ? "解析中..." : "上传文档"}
                </button>
                <span className="text-xs text-gray-400">支持 PDF、Word、PPT、Markdown、TXT 等</span>
              </div>
            </div>
          )}
          {/* 当前消息待发送的附件 */}
          {pendingAttachments.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-3">
              {pendingAttachments.map((filename) => (
                <span
                  key={filename}
                  className="inline-flex items-center gap-1 text-xs bg-blue-50 text-blue-700 px-2 py-1 rounded border border-blue-200"
                >
                  <span>{filename}</span>
                  <button
                    onClick={() => setPendingAttachments((prev) => prev.filter((f) => f !== filename))}
                    className="text-blue-400 hover:text-blue-900 ml-1"
                    title="移除"
                  >
                    X
                  </button>
                </span>
              ))}
            </div>
          )}
          {currentAgentRole === "finetune" && finetuneTargetSlideId && (pendingFinetuneAttachmentsMap[finetuneTargetSlideId] || []).length > 0 && (
            <div className="flex flex-wrap gap-2 mb-3">
              {(pendingFinetuneAttachmentsMap[finetuneTargetSlideId] || []).map((att) => (
                <div key={att.id} className="inline-flex items-center gap-2 bg-amber-50 text-amber-800 px-2 py-1 rounded border border-amber-200 max-w-full">
                  <img src={att.url} alt={att.name} className="w-10 h-6 rounded object-cover border border-amber-200" />
                  <span className="text-xs truncate max-w-[160px]">{att.name}</span>
                  <button
                    onClick={() => {
                      setPendingFinetuneAttachmentsMap((prev) => ({
                        ...prev,
                        [finetuneTargetSlideId]: (prev[finetuneTargetSlideId] || []).filter((item) => item.id !== att.id),
                      }));
                    }}
                    className="text-amber-500 hover:text-amber-900 ml-0.5 text-xs"
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
              <div className="mb-3 p-2 bg-amber-50 border border-amber-200 rounded-lg">
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
                    className="text-xs bg-white text-amber-700 px-2.5 py-1.5 rounded-md hover:bg-amber-100 border border-amber-200 disabled:opacity-50 flex-shrink-0"
                    title="添加参考图到本轮消息"
                  >
                    + 图
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
          <div className="flex gap-2">
            <textarea
              ref={chatInputRef}
              className="flex-1 border border-slate-300 rounded-lg resize-none px-3 py-2.5 text-sm focus:border-blue-400 focus:ring-1 focus:ring-blue-200 outline-none transition-colors"
              style={{ minHeight: 38, overflowY: "hidden" }}
              placeholder={
                currentAgentRole === "finetune" && !finetuneTargetSlideId
                  ? "请先在左侧点击一页..."
                  : currentStatus === "draft"
                  ? "输入 PPT 主题或粘贴文档内容..."
                  : currentAgentRole === "finetune"
                  ? "告诉我怎么改，或先点「+ 图」加参考图..."
                  : "输入指令..."
              }
              value={chatInput}
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
                  className="bg-amber-500 text-white rounded-lg px-3 py-2 text-sm opacity-80 cursor-wait"
                >
                  生成中
                </button>
              ) : (
                <button
                  onClick={handleStopChat}
                  className="bg-gray-800 text-white rounded hover:bg-gray-900 px-3 py-2 text-sm"
                >
                  停止
                </button>
              )
            ) : hasActiveRun && currentAgentRole !== "finetune" ? (
              <button
                onClick={handleStopGeneration}
                className="bg-red-600 text-white rounded-lg hover:bg-red-700 px-4 py-2.5 text-sm font-medium transition-colors flex items-center gap-1.5"
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
                  (!chatInput.trim() && pendingAttachments.length === 0 && !(currentAgentRole === "finetune" && finetuneTargetSlideId && (pendingFinetuneAttachmentsMap[finetuneTargetSlideId] || []).length > 0)) ||
                  (currentAgentRole === "finetune" && !finetuneTargetSlideId)
                }
                className="bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 px-4 py-2.5 text-sm font-medium transition-colors"
              >
                {currentAgentRole === "finetune" ? "生成" : "发送"}
              </button>
            )}
          </div>
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
                <SlideImageWithLogo
                  slide={galleryModal.slides[galleryModal.index]}
                  logo={galleryModal.logo}
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

function ColorChip({ hex }: { hex: string }) {
  return (
    <span className="group inline-flex items-center align-middle mx-0.5 relative">
      <span
        className="inline-block w-4 h-4 rounded border border-gray-200 cursor-pointer transition-transform duration-200 group-hover:scale-150 group-hover:z-10 shadow-sm"
        style={{ backgroundColor: hex }}
        title="配色"
      />
    </span>
  );
}

function renderDescriptionWithColors(text: string) {
  const hexRegex = /(#[0-9A-Fa-f]{6}\b)/g;
  const parts = text.split(hexRegex);
  return parts.map((part, i) => {
    if (/^#[0-9A-Fa-f]{6}$/.test(part)) {
      return <ColorChip key={i} hex={part} />;
    }
    return <span key={i}>{part}</span>;
  });
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
}: {
  slide: Slide;
  projectId: string;
  onExit: () => void;
  onSaved?: () => void;
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
  const [promptExpanded, setPromptExpanded] = useState(false);

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

  // 保存视觉方案画面描述（仅当内容有实际变化时才保存）
  // 保存当前编辑内容（不退出）
  const handleSave = async (): Promise<boolean> => {
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
    const hasVisualChange = slide.visual_json && visualDescription !== originalVisualDesc;

    setSaving(true);
    try {
      if (hasContentChange) {
        await updateSlideContent(projectId, slide.page_num, saveData, slide.id);
        markSlideStale?.(slide.id, "content");
      }
      if (hasVisualChange) {
        await updateVisualPlan(projectId, slide.page_num, {
          ...slide.visual_json,
          visual_description: visualDescription,
        }, slide.id);
        markSlideStale?.(slide.id, "visual");
      }
      onSaved?.();
      if (hasContentChange || hasVisualChange) {
        onToast?.(hasVisualChange ? "已保存，请点击「更新画面方案」应用修改" : "已保存", "success");
        onSystemLog?.(`用户编辑了第 ${slide.page_num} 页（类型：${slide.type || "content"}）的标题/正文`);
      }
      return true;
    } catch (err: any) {
      onToast?.("保存失败：" + (err.message || "未知错误"), "error");
      return false;
    } finally {
      setSaving(false);
    }
  };

  // 保存并退出编辑
  const handleSaveAndExit = async () => {
    const ok = await handleSave();
    if (ok) onExit();
  };

  // 保存并重新生成图片（一键应用修改）
  const handleSaveAndGenerate = async () => {
    const ok = await handleSave();
    if (!ok) return;
    if (!onRetry) {
      onToast?.("无法重新生成：缺少重试接口", "error");
      return;
    }
    setIsGenerating(true);
    onToast?.("正在重新生成图片...", "info");
    try {
      await onRetry(slide.id, true); // regenerate_prompt = true
      onToast?.("图片重新生成已启动", "success");
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

  // 内容规划阶段不显示 "需更新画面方案" 的 content stale 提示
  const hasVisualPlan = !!slide.visual_json?.visual_description;
  const pastContentPlanning = !["draft", "planning", "content_plan_ready"].includes(projectStatus || "");
  const showContentStale = staleStatus?.content && hasVisualPlan && pastContentPlanning;
  const showVisualStale = staleStatus?.visual;
  const showImageStale = staleStatus?.image;

  return (
    <div className="max-w-3xl mx-auto bg-white rounded border shadow-sm p-6">
      {/* 顶部工具栏 — 全文字，无图标，三区布局 */}
      <div className="flex items-center justify-between mb-6 pb-4 border-b border-slate-200">
        {/* 左：核心操作 */}
        <div className="flex items-center gap-1.5">
          <button
            onClick={handleSaveAndExit}
            disabled={saving}
            className={`text-sm px-2.5 py-1.5 rounded-md transition-colors ${
              saving ? "text-slate-300 cursor-not-allowed" : "text-slate-500 hover:text-slate-700 hover:bg-slate-100"
            }`}
          >
            {saving ? "保存中..." : "返回列表"}
          </button>
          <button
            onClick={async () => { await handleSave(); }}
            disabled={saving || isGenerating}
            className={`text-sm px-3 py-1.5 rounded-md border transition-colors ${
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
            className={`text-sm px-3 py-1.5 rounded-md font-medium transition-all ${
              saving || isGenerating
                ? "bg-slate-300 text-white cursor-not-allowed"
                : "bg-purple-600 text-white hover:bg-purple-700 shadow-sm"
            }`}
            title="保存并重新生成此页图片"
          >
            {isGenerating ? "生成中..." : saving ? "保存中..." : "保存并生成"}
          </button>
        </div>

        {/* 中：页面定位 */}
        <div className="flex items-center gap-2">
          {onPrev && (
            <button
              onClick={async () => { const ok = await handleSave(); if (ok) onPrev?.(); }}
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
              onClick={async () => { const ok = await handleSave(); if (ok) onNext?.(); }}
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
        <div className="mb-4 bg-amber-50 border border-amber-200 rounded p-3">
          <div className="flex items-center justify-between">
            <div className="flex flex-wrap gap-2 text-xs">
              {showContentStale && (
                <span className="px-2 py-0.5 bg-blue-100 text-blue-700 rounded text-xs" title="文字已修改，需更新画面方案">文字已改</span>
              )}
              {showVisualStale && (
                <span className="px-2 py-0.5 bg-emerald-100 text-emerald-700 rounded text-xs" title="画面描述已修改，需更新提示词">画面已改</span>
              )}
              {showImageStale && (
                <span className="px-2 py-0.5 bg-purple-100 text-purple-700 rounded text-xs" title="提示词已就绪，可重新生成图片">待生成</span>
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
                className="text-xs bg-amber-500 text-white px-3 py-1.5 rounded hover:bg-amber-600 transition-colors flex-shrink-0 ml-2 disabled:opacity-60"
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
                  className="text-xs bg-white text-purple-700 border border-purple-200 px-3 py-1.5 rounded hover:bg-purple-50 transition-colors disabled:opacity-60"
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
                  className="text-xs bg-purple-500 text-white px-3 py-1.5 rounded hover:bg-purple-600 transition-colors disabled:opacity-60"
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
          className="w-full text-xl font-bold border border-gray-200 rounded p-3 focus:outline-none focus:ring-2 focus:ring-blue-300 focus:border-transparent resize-none"
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
          className="w-full text-base text-gray-600 border border-gray-200 rounded p-2 focus:outline-none focus:ring-2 focus:ring-blue-300 focus:border-transparent resize-none"
          rows={1}
        />
      </div>

      {/* 正文（所见即所得） */}
      <div className="mb-6">
        <label className="text-xs text-gray-500 mb-1 block font-medium">正文</label>
        <div className="border border-gray-200 rounded focus-within:ring-2 focus-within:ring-blue-300 focus-within:border-transparent">
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
        <label className="text-xs text-gray-500 mb-1 block font-medium">💬 演讲者备注</label>
        <textarea
          value={speakerNotes}
          onChange={(e) => setSpeakerNotes(e.target.value)}
          onKeyDown={(e) => applyMarkdownShortcut(e, setSpeakerNotes)}
          className="w-full text-sm border border-gray-200 rounded p-3 focus:outline-none focus:ring-2 focus:ring-blue-300 focus:border-transparent resize-y min-h-[80px]"
          placeholder="输入演讲者备注..."
        />
      </div>

      {/* 本页参考图 */}
      <div className="mb-6">
        <div className="flex items-center gap-1.5 mb-2">
          <label className="text-xs text-gray-500 font-medium">本页参考图</label>
          <div className="relative group">
            <span className="text-xs text-gray-400 cursor-help">ⓘ</span>
            <div className="absolute left-1/2 -translate-x-1/2 bottom-full mb-1 hidden group-hover:block w-64 bg-gray-800 text-white text-[11px] rounded-lg px-3 py-2 shadow-lg z-50">
              <p className="mb-1">只影响当前这一页，优先级高于项目级核心资产。</p>
              <p className="font-semibold mb-1">三种处理模式：</p>
              <p><span className="text-blue-300">融合</span>：提取图片主体，智能融入画面</p>
              <p><span className="text-orange-300">裁剪</span>：保留图片内容，允许裁剪适配</p>
              <p><span className="text-green-300">原图</span>：原样插入，不改变比例</p>
              <div className="absolute left-1/2 -translate-x-1/2 top-full w-2 h-2 bg-gray-800 rotate-45" />
            </div>
          </div>
        </div>
        <div className="flex flex-wrap gap-3 mb-2">
          {slide.reference_images?.map((ref: any) => (
            <div key={ref.id} className="flex flex-col items-center gap-1">
              <div className="relative group">
                <img
                  src={`${API_BASE}${ref.url}`}
                  alt="ref"
                  className="w-16 h-16 rounded object-cover border cursor-pointer"
                  onClick={() => onImageClick?.(`${API_BASE}${ref.url}`)}
                  onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                />
                {/* 删除按钮 */}
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
              {/* 模式切换按钮 */}
              <div className="flex gap-0.5">
                {[
                  { key: "blend", label: "融合", color: "bg-blue-500" },
                  { key: "crop", label: "裁剪", color: "bg-orange-500" },
                  { key: "original", label: "原图", color: "bg-green-600" },
                ].map((m) => (
                  <button
                    key={m.key}
                    onClick={async () => {
                      try {
                        await updateReferenceImageMode(projectId, ref.id, m.key);
                        markSlideStale?.(slide.id, "visual");
                        onSaved?.();
                        onToast?.(`已切换为${m.label}模式`, "success");
                        onSystemLog?.(`用户将第 ${slide.page_num} 页本页参考图切换为${m.label}模式`);
                      } catch (err: any) {
                        onToast?.("更新失败：" + (err.message || "未知错误"), "error");
                      }
                    }}
                    className={`text-2xs px-1.5 py-0.5 rounded leading-none ${ref.process_mode === m.key ? `${m.color} text-white` : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`}
                  >
                    {m.label}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
        <button
          onClick={() => {
            const input = document.createElement("input");
            input.type = "file";
            input.accept = "image/*";
            input.onchange = async (e) => {
              const file = (e.target as HTMLInputElement).files?.[0];
              if (!file) return;
              try {
                await uploadFile(projectId, file, "content_ref", slide.id, "blend");
                markSlideStale?.(slide.id, "visual");
                onSaved?.();
                onToast?.("上传成功", "success");
                onSystemLog?.(`用户为第 ${slide.page_num} 页上传了本页参考图（融合模式）`);
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

      {/* 画面描述（只读） */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-1">
          <label className="text-xs text-gray-500 font-medium">画面描述</label>
          <span className="text-2xs text-gray-400">如需调整，请在右侧 Agent 窗口与视觉总监对话</span>
        </div>
        <div className="bg-emerald-50 border border-emerald-100 rounded p-3">
          {visualDescription ? (
            <p className="text-sm text-gray-700 leading-relaxed">{renderDescriptionWithColors(visualDescription)}</p>
          ) : (
            <span className="text-sm text-gray-400">暂无画面描述</span>
          )}
          {slide.visual_json?.layout && (
            <div className="text-xs text-gray-400 mt-1">布局: {slide.visual_json.layout}</div>
          )}
        </div>
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
            <div className="bg-gray-50 border border-gray-200 rounded p-3">
              <p className="text-xs text-gray-500 leading-relaxed whitespace-pre-wrap font-mono">{slide.prompt_text}</p>
            </div>
          )}
        </div>
      )}

      {/* 单页图片预览 */}
      {slide.image_path && (
        <div className="mb-6">
          <label className="text-xs text-gray-500 mb-1 block font-medium">画面预览</label>
          <SlideImageWithLogo
            slide={slide}
            logo={projectLogo}
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
