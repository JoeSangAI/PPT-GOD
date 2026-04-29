import { useEffect, useRef, useState } from "react";
import { marked } from "marked";
import TurndownService from "turndown";

// 修复 marked 无法解析 **text标点**后接字符 的粗体（CommonMark 规范限制）
const fixMarkedBoldHtml = (html: string): string => {
  return html.replace(/\*\*([^*]+?)\*\*([^<\s])/g, "<strong>$1</strong>$2");
};

const renderMarkdown = (md: string, chatStyle = false): string => {
  let html = (marked.parse(md || "", { async: false }) as string) || "";
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
  }
  return html;
};

import StyleProposalSelector, { type StyleProposal } from "./components/StyleProposalSelector";
import TemplateRecommender from "./components/TemplateRecommender";
import VisualAssetsPanel from "./components/VisualAssetsPanel";
import ToastContainer, { type ToastItem } from "./components/Toast";
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
  fetchProjectStatus,
  fetchGenerationProgress,
  fetchGenerationStatus,
  getDownloadUrl,
  uploadFile,
  fetchReferenceImages,
  deleteReferenceImage,
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
  setSeedPage,
  unsetSeedPage,
  extractTemplate,
  fetchTemplatePages,
  updateTemplateRecommendations,
  rollbackProject,
} from "./api/client";

interface Project {
  id: string;
  title: string;
  status: string;
  style_id: string | null;
  style_proposal: any | null;
  selected_style: any | null;
  selected_template_recommendations: any | null;
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
  reference_images?: { id: string; role: string; url: string }[];
}

interface PositioningData {
  core_thesis: string;
  strategy: string;
  tone: string;
  estimated_pages: number;
  key_highlights: string[];
}

interface ChatMessage {
  role: "user" | "agent" | "system";
  content: string;
  action?: string;
  positioning?: PositioningData;
  topic?: string;
  agentRole?: "content" | "visual";
  loading?: boolean;
  id?: string;
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

function getSlideImageUrl(imagePath: string, status?: string) {
  const base = `${API_BASE}${imagePath.replace("./outputs", "/outputs")}`;
  const cacheBuster = status ? `?v=${status}` : `?t=${Date.now()}`;
  return `${base}${cacheBuster}`;
}

function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [slides, setSlides] = useState<Slide[]>([]);
  const [slidesHistory, setSlidesHistory] = useState<Slide[][]>([]);
  const [slidesHistoryIndex, setSlidesHistoryIndex] = useState(-1);
  const isGlobalUndoingRef = useRef(false);
  const [operatingProjectId, setOperatingProjectId] = useState<string | null>(null);

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


  const isBusy = operatingProjectId === selectedProject?.id;
  const [projectStatus, setProjectStatus] = useState<any>(null);
  const [selectedPages, setSelectedPages] = useState<Set<number>>(new Set());
  const [showPrototypePreview, setShowPrototypePreview] = useState(true);
  const [referenceImages, setReferenceImages] = useState<any[]>([]);
  const [templatePages, setTemplatePages] = useState<any[]>([]);
  const [showTemplateRecommender, setShowTemplateRecommender] = useState(false);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const [thinkingContent, setThinkingContent] = useState("");
  const [thinkingExpanded, setThinkingExpanded] = useState(false);
  const [galleryModal, setGalleryModal] = useState<{
    urls: string[];
    index: number;
    title?: string;
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
  const [uploadingDoc, setUploadingDoc] = useState(false);
  const [uploadingStyleRef, setUploadingStyleRef] = useState(false);
  const [uploadingLogo, setUploadingLogo] = useState(false);
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
  const [contentPlanProgress, setContentPlanProgress] = useState<any>(null);
  const [styleProposalsLoading, setStyleProposalsLoading] = useState(false);
  const [showStylePanel, setShowStylePanel] = useState(false);
  const [currentAgentRole, setCurrentAgentRole] = useState<"content" | "visual">("content");
  const [contentPlanConfirmed, setContentPlanConfirmed] = useState(false);
  const [contentPlanSnapshot, setContentPlanSnapshot] = useState<Slide[]>([]);
  const [confirmingPlan, setConfirmingPlan] = useState(false);
  const currentAgentRoleRef = useRef(currentAgentRole);
  const contentPlanSnapshotRef = useRef(contentPlanSnapshot);
  const contentPlanConfirmedRef = useRef(contentPlanConfirmed);
  const [styleProposalsInChat, setStyleProposalsInChat] = useState<StyleProposal[]>([]);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [confirmModal, setConfirmModal] = useState<{ message: string; onConfirm: () => void; onCancel: () => void } | null>(null);
  const docInputRef = useRef<HTMLInputElement>(null);
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
      // 同步更新当前选中项目的状态，避免 stale status
      if (selectedProject) {
        const updated = data.find((p: Project) => p.id === selectedProject.id);
        if (updated) {
          // 只有关键字段变化时才更新，避免引用变化导致不必要的重渲染
          if (
            updated.status !== selectedProject.status ||
            updated.title !== selectedProject.title ||
            updated.completed_slides !== selectedProject.completed_slides ||
            JSON.stringify(updated.selected_style) !== JSON.stringify(selectedProject.selected_style) ||
            JSON.stringify(updated.style_proposal) !== JSON.stringify(selectedProject.style_proposal)
          ) {
            setSelectedProject(updated);
          }
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
            setChatMessages((prev) => [
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
      const data = await fetchProjectStatus(projectId);
      if (loadingProjectIdRef.current !== projectId) return;
      setProjectStatus(data);
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
    setChatMessages((prev) => [
      ...prev,
      ...(source === "button" ? [{ role: "user" as const, content: "直接生成" }] : []),
      { role: "agent" as const, content: "⏳ 正在启动内容规划生成...", agentRole: "content", loading: true, id: loadingId },
    ]);
    setOperatingProjectId(projectId);

    const updateLoadingMsg = (content: string) => {
      setChatMessages((prev) => {
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
      setChatMessages((prev) => prev.filter((m) => m.id !== loadingId));
    };

    try {
      await generateContentPlan(projectId, topic, pageCount);
      const startedAt = Date.now();
      let progressInterval: ReturnType<typeof setInterval> | null = null;
      let checkInterval: ReturnType<typeof setInterval> | null = null;
      const cleanupContentPlanPoll = () => {
        if (progressInterval) clearInterval(progressInterval);
        if (checkInterval) clearInterval(checkInterval);
        progressInterval = null;
        checkInterval = null;
        contentPlanProgressIntervalRef.current = null;
        contentPlanCheckIntervalRef.current = null;
        setContentPlanProgress(null);
        setOperatingProjectId(null);
        removeLoadingMsg();
      };
      const failContentPlanPoll = (message: string) => {
        cleanupContentPlanPoll();
        setChatMessages((prev) => [
          ...prev,
          {
            role: "agent",
            content: "❌ 内容规划生成失败：" + message + "\n\n👉 请告诉我你的主题，我会重新为你生成。",
            agentRole: "content",
          },
        ]);
      };

      progressInterval = setInterval(async () => {
        try {
          if (Date.now() - startedAt > CONTENT_PLAN_TIMEOUT_MS) {
            failContentPlanPoll("前端等待超时，但后台可能仍在运行。请稍后刷新页面查看结果，不要重复点击。");
            return;
          }
          const progress = await fetchGenerationProgress(projectId);
          setContentPlanProgress(progress);
          // 同步更新 Agent 窗口进度
          if (progress?.message) {
            const progressText = progress.total_pages ? `（${progress.current_page || 0} / ${progress.total_pages}）` : "";
            updateLoadingMsg(`⏳ ${progress.message}${progressText}`);
          }
          if (progress.stage === "error") {
            failContentPlanPoll(progress.message || "后台处理异常");
          }
        } catch (e) {
          console.warn("Content plan progress poll error:", e);
        }
      }, 1500);
      contentPlanProgressIntervalRef.current = progressInterval;

      checkInterval = setInterval(async () => {
        try {
          if (Date.now() - startedAt > CONTENT_PLAN_TIMEOUT_MS) {
            failContentPlanPoll("前端等待超时，但后台可能仍在运行。请稍后刷新页面查看结果，不要重复点击。");
            return;
          }
          const currentSlides = await loadSlides(projectId);
          await loadProjects();
          const currentSlideIds = currentSlides.map((s: any) => s.id).sort().join(",");
          // 必须有 slides，且 ID 集合与旧内容不同（说明是新生成的），才认为完成
          if (currentSlides.length > 0 && currentSlideIds !== previousSlideIds) {
            cleanupContentPlanPoll();
            setChatMessages((prev) => [
              ...prev,
              {
                role: "agent",
                content:
                  "✅ 内容规划已生成完毕，共 " +
                  currentSlides.length +
                  " 页。\n\n👉 下一步：请检查左侧每一页的内容是否满意。如果有调整需求，直接告诉我；如果没问题，点击右侧面板的「确认内容，请视觉总监 →」按钮进入视觉设计阶段。",
                agentRole: "content",
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
      setChatMessages((prev) => [
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
    if (selectedProject) {
      loadingProjectIdRef.current = selectedProject.id;
      loadSlides(selectedProject.id);
      loadStatus(selectedProject.id);
      loadReferenceImages(selectedProject.id);
      loadDocuments(selectedProject.id);
      loadTemplatePages(selectedProject.id);
      setSelectedPages(new Set());
      setThinkingContent("");
      setThinkingExpanded(false);
      setPendingAttachments([]);
      if (abortRef.current) {
        abortRef.current.abort();
        abortRef.current = null;
      }

      if (loadedChatProjectIdRef.current !== selectedProject.id) {
        // 首次选中该项目（含页面重新加载后）：尝试从 localStorage 恢复聊天历史
        const savedChat = localStorage.getItem(`ppt_god_chat_${selectedProject.id}`);
        if (savedChat) {
          try {
            setChatMessages(JSON.parse(savedChat));
          } catch {
            setChatMessages([]);
          }
        } else {
          setChatMessages([]);
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
      if (abortRef.current) {
        abortRef.current.abort();
        abortRef.current = null;
      }
      setChatLoading(false);
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

  // 持久化聊天历史到 localStorage（按项目隔离）
  useEffect(() => {
    if (!selectedProject) return;
    if (chatMessages.length === 0) {
      localStorage.removeItem(`ppt_god_chat_${selectedProject.id}`);
    } else {
      localStorage.setItem(`ppt_god_chat_${selectedProject.id}`, JSON.stringify(chatMessages));
    }
  }, [chatMessages, selectedProject?.id]);

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
      }
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => document.removeEventListener("visibilitychange", onVisibilityChange);
  }, [selectedProject?.id]);

  // 轮询生成进度（generating 或 prototype 时都轮询）
  useEffect(() => {
    if (!selectedProject) return;
    if (!["generating", "prototype"].includes(selectedProject.status)) return;

    let isFetching = false;
    const interval = setInterval(async () => {
      if (isFetching) return;
      isFetching = true;
      try {
        await loadStatus(selectedProject.id);
        await loadSlides(selectedProject.id);
        await loadProjects();
      } finally {
        isFetching = false;
      }
    }, 3000);

    return () => clearInterval(interval);
  }, [selectedProject?.status]);

  // 检测全量生成完成/失败，发送 Agent 提示（按项目隔离，防止切换项目时误触发）
  const prevProjectStatusRef = useRef<{ projectId: string | null; status: string | null }>({ projectId: null, status: null });
  useEffect(() => {
    const pid = selectedProject?.id || null;
    const currentStatus = selectedProject?.status || null;
    const prev = prevProjectStatusRef.current;
    // 只有同一个项目从 generating 变为 completed 才触发
    if (prev.projectId === pid && prev.status === "generating" && currentStatus === "completed") {
      const completedCount = slides.filter((s) => s.status === "completed").length;
      const loadingId = generationLoadingIdRef.current;
      generationLoadingIdRef.current = null;
      setChatMessages((prevMsgs) => [
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
      setChatMessages((prevMsgs) => [
        ...prevMsgs.filter((m) => m.id !== loadingId),
        {
          role: "agent",
          content: "❌ 批量生成失败，部分页面可能未成功生成。请检查失败页面后重试，或告诉我具体问题。",
          agentRole: "visual",
        },
      ]);
    }
    prevProjectStatusRef.current = { projectId: pid, status: currentStatus };
  }, [selectedProject?.id, selectedProject?.status]);

  // 实时更新批量生成进度到 Agent 窗口
  useEffect(() => {
    if (!selectedProject || !["generating", "prototype"].includes(selectedProject.status)) return;
    if (!generationLoadingIdRef.current || !projectStatus) return;

    const loadingId = generationLoadingIdRef.current;
    const completed = projectStatus.completed_slides || 0;
    const target = projectStatus.target_count || projectStatus.total_slides || 0;

    setChatMessages((prev) => {
      const idx = prev.findIndex((m) => m.id === loadingId);
      if (idx >= 0) {
        const updated = [...prev];
        updated[idx] = { ...updated[idx], content: `🚀 正在生成图片... ${completed} / ${target} 页完成` };
        return updated;
      }
      return prev;
    });
  }, [projectStatus?.completed_slides, projectStatus?.target_count, selectedProject?.status]);

  // 聊天自动滚动
  useEffect(() => {
    if (chatContainerRef.current) {
      chatContainerRef.current.scrollTop = chatContainerRef.current.scrollHeight;
    }
  }, [chatMessages, chatLoading]);

  const addSystemLog = (content: string) => {
    setChatMessages((prev) => [...prev, { role: "system", content }]);
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
          setChatMessages([
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
    try {
      await updateProjectStyle(selectedProject.id, style);
      await loadProjects();
      const fresh = await fetchProjects();
      const updated = fresh.find((p: Project) => p.id === selectedProject.id);
      if (updated) setSelectedProject(updated);
      setShowStylePanel(false);
      setStyleProposalsInChat([]); // 清除Agent面板内的提案
      addSystemLog(`用户选择了风格「${style.name || "未命名"}」`);
      // 自动进入生图方案生成，无需用户再点一次
      await handleGeneratePrompts(false, style.name);
    } catch (err: any) {
      showToast("保存风格失败：" + (err.message || "未知错误"), "error");
      setChatMessages((prev) => [
        ...prev,
        {
          role: "agent",
          content: "❌ 保存风格失败：" + (err.message || "未知错误") + "\n\n👉 请检查网络后，在主舞台重新选择风格并点击「确认风格，生成生图方案」。",
          agentRole: "visual",
        },
      ]);
    }
  };

  const handleRegenerateStyleProposals = async () => {
    if (!selectedProject) return;
    setStyleProposalsLoading(true);
    try {
      // 通过 Agent 重新提案，确保与对话上下文一致
      await handleSendChat("我都不满意，请重新生成一套风格提案");
    } catch (err: any) {
      showToast("重新生成风格提案失败：" + (err.message || "未知错误"), "error");
    } finally {
      setStyleProposalsLoading(false);
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
    setOperatingProjectId(selectedProject.id);
    // 记录用户确认动作到聊天记录
    const name = styleName || selectedProject.selected_style?.name || selectedProject.style_id || "默认";
    setChatMessages((prev) => [
      ...prev,
      {
        role: "agent" as const,
        content: `⏩ 用户确认风格「${name}」，开始生成画面描述和生图 Prompt...`,
        agentRole: "visual",
      },
    ]);

    // 插入真实进度 loading 消息（使用唯一ID确保稳定更新）
    const loadingId = `vp-${Date.now()}`;
    setChatMessages((prev) => [
      ...prev,
      { role: "agent", content: "🚀 已启动后台生成，正在运行...", agentRole: "visual", loading: true, id: loadingId },
    ]);

    try {
      const pageNums = prototype && selectedPages.size > 0 ? Array.from(selectedPages) : undefined;
      // 触发后台任务（不再依赖 SSE 长连接）
      await generateVisualPrompts(selectedProject.id, pageNums);

      if (visualPromptIntervalRef.current) {
        clearInterval(visualPromptIntervalRef.current);
      }

      await new Promise<void>((resolve, reject) => {
        let attempts = 0;
        let pollErrors = 0;
        const maxAttempts = 400; // 33 页 Prompt 生成约需 8–15 分钟，预留 20 分钟

        const updateLoadingMsg = (content: string) => {
          setChatMessages((prev) => {
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
            const [projectData, progressData, genStatusData] = await Promise.all([
              fetchProjectStatus(selectedProject.id),
              fetchGenerationProgress(selectedProject.id),
              fetchGenerationStatus(selectedProject.id),
            ]);
            pollErrors = 0;
            const status = projectData?.project_status || projectData?.status;
            const taskStatus = genStatusData?.status; // "running" | "idle"

            // 实时更新进度到 Agent 面板
            const progressText = progressData?.total_pages
              ? `（${progressData.current_page || 0} / ${progressData.total_pages}）`
              : "";
            if (progressData?.message) {
              updateLoadingMsg(`🚀 ${progressData.message}${progressText}`);
            } else if (progressText) {
              updateLoadingMsg(`🚀 后台生成中${progressText}...`);
            }

            if ((status === "visual_ready" || status === "prompt_ready") && taskStatus !== "running") {
              if (visualPromptIntervalRef.current) {
                clearInterval(visualPromptIntervalRef.current);
                visualPromptIntervalRef.current = null;
              }
              await loadSlides(selectedProject.id);
              await loadProjects();

              // 验证数据是否真的写入了数据库（防止后台任务失败但状态未回滚）
              const freshSlides = await fetchSlides(selectedProject.id);
              const hasVisual = freshSlides.some((s: Slide) => s.visual_json && Object.keys(s.visual_json).length > 0);
              const hasPrompt = freshSlides.some((s: Slide) => s.prompt_text);
              const dataReady =
                (status === "prompt_ready" && hasPrompt) ||
                (status === "visual_ready" && hasVisual);
              if (!dataReady) {
                reject(new Error("后台任务已结束，但未成功保存结果。请刷新页面查看状态，或重试。"));
                return;
              }

              resolve();
              return;
            }

            // 后台任务已结束但状态未就绪 → 任务异常中断
            if (taskStatus === "idle" && status !== "visual_ready" && status !== "prompt_ready") {
              if (visualPromptIntervalRef.current) {
                clearInterval(visualPromptIntervalRef.current);
                visualPromptIntervalRef.current = null;
              }
              reject(new Error("后台任务已结束，但未成功保存结果。请刷新页面查看状态，或重试。"));
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
      setChatMessages((prev) => [
        ...prev.filter((m) => m.id !== loadingId),
        {
          role: "agent",
          content:
            "✅ 画面描述和生图 Prompt 已生成完毕。\n\n👉 下一步：你可以先「打样确认」生成 1-3 张预览效果，满意后再「全量生成」所有页面。也可以直接全量生成。",
          agentRole: "visual",
        },
      ]);
    } catch (err: any) {
      showToast("生成生图方案失败：" + (err.message || "未知错误"), "error");
      setChatMessages((prev) => [
        ...prev.filter((m) => m.id !== loadingId),
        {
          role: "agent",
          content: "❌ 生图方案生成失败：" + (err.message || "未知错误") + "\n\n👉 解决方法：\n1. 检查网络连接后，点击上方「确认风格，生成生图方案」按钮重试\n2. 如果多次失败，可以尝试回退到「视觉方案」阶段重新选择风格\n3. 也可以直接告诉我具体问题，我来帮你调整",
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

  const handleStartGeneration = async (useSelectedPages = false, prototype = false) => {
    if (!selectedProject) return;
    if (operatingProjectId === selectedProject.id) return;
    setOperatingProjectId(selectedProject.id);
    const modeText = prototype ? "打样" : "全量生成";
    const pageNums = useSelectedPages && selectedPages.size > 0 ? Array.from(selectedPages) : undefined;
    const pageDesc = pageNums ? `第 ${pageNums.join(", ")} 页` : (prototype ? "种子页" : "所有页面");
    const loadingId = `gen-${Date.now()}`;
    generationLoadingIdRef.current = loadingId;
    setChatMessages((prev) => [
      ...prev,
      {
        role: "agent",
        content: `🚀 已启动${modeText}（${pageDesc}），正在准备...`,
        agentRole: "visual",
        loading: true,
        id: loadingId,
      },
    ]);
    try {
      await startGeneration(selectedProject.id, pageNums, prototype);
      await loadStatus(selectedProject.id);
      await loadProjects();
    } catch (err: any) {
      showToast("启动生成失败：" + (err.message || "未知错误"), "error");
      generationLoadingIdRef.current = null;
      setChatMessages((prev) => [
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

  const handleStopGeneration = async () => {
    if (!selectedProject) return;
    try {
      await stopGeneration(selectedProject.id);
      await loadStatus(selectedProject.id);
      await loadProjects();
      showToast("已停止生成", "info");
      const loadingId = generationLoadingIdRef.current;
      generationLoadingIdRef.current = null;
      setChatMessages((prev) => [
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
    setOperatingProjectId(selectedProject.id);
    const loadingId = `gen-${Date.now()}`;
    generationLoadingIdRef.current = loadingId;
    setChatMessages((prev) => [
      ...prev,
      {
        role: "agent",
        content: "🚀 打样已通过，正在启动全量生成所有页面...",
        agentRole: "visual",
        loading: true,
        id: loadingId,
      },
    ]);
    try {
      await confirmPrototype(selectedProject.id);
      await loadStatus(selectedProject.id);
      addSystemLog("用户确认打样效果，开始批量生成");
      // 轮询等待全量生成完成
      setChatMessages((prev) => [
        ...prev.filter((m) => m.id !== loadingId),
        {
          role: "agent",
          content: "🚀 全量生成已启动，正在后台生成所有页面...",
          agentRole: "visual",
          loading: true,
          id: loadingId,
        },
      ]);
      const finalStatus = await pollUntilStatusNotGenerating(selectedProject.id);
      generationLoadingIdRef.current = null;
      setChatMessages((prev) => [
        ...prev.filter((m) => m.id !== loadingId),
        {
          role: "agent",
          content: finalStatus === "completed"
            ? "✅ 全部页面生成完成！可以下载 PPTX 了。"
            : `⚠️ 生成结束（状态：${finalStatus}），部分页面可能未成功，请检查进度。`,
          agentRole: "visual",
        },
      ]);
    } catch (err: any) {
      showToast("全量生成失败：" + (err.message || "未知错误"), "error");
      generationLoadingIdRef.current = null;
      setChatMessages((prev) => [
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

  // 轮询等待生成任务完成（generating / prototype 状态结束）
  const pollUntilStatusNotGenerating = async (projectId: string, timeoutMs = 1_200_000) => {
    const start = Date.now();
    let pollErrors = 0;
    while (Date.now() - start < timeoutMs) {
      await new Promise((r) => setTimeout(r, 3000));
      try {
        const statusData = await fetchProjectStatus(projectId);
        pollErrors = 0;
        const projectStage = statusData.project_status || statusData.status;
        if (projectStage !== "generating" && projectStage !== "prototype") {
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
        setChatMessages((prev) => [
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
        setChatMessages((prev) => [
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
      setChatMessages((prev) => [
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
      setChatMessages((prev) => [
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
    if (selectedProject.status === "generating") {
      try {
        await stopGeneration(selectedProject.id);
      } catch {
        // 忽略停止失败的错误，继续回退
      }
    }

    setOperatingProjectId(selectedProject.id);
    try {
      await rollbackProject(selectedProject.id, targetStage);
      await loadProjects();
      const fresh = await fetchProjects();
      const updated = fresh.find((p: Project) => p.id === selectedProject.id);
      if (updated) setSelectedProject(updated);
      await loadSlides(selectedProject.id);
      setStaleMap({});

      // 根据回退目标生成详细的自动化引导消息
      let rollbackMsg = `⏪ 已回退到「${stageNames[targetStage] || targetStage}」。后续数据已重置。`;
      if (targetStage === "visual_ready") {
        const logoAsset = referenceImages.find((r: any) => r.role === "logo");
        const styleRefAssets = referenceImages.filter((r: any) => r.role === "style_ref");
        const templateAsset = referenceImages.find((r: any) => r.role === "template");
        rollbackMsg += `\n\n**视觉总监已重新介入。** 为了给你更精准的风格提案，请先确认当前的设计素材：\n\n📎 **素材清单**\n• Logo：${logoAsset ? "已上传 ✅" : "未上传"}\n• 参考图：${styleRefAssets.length > 0 ? `已上传 ${styleRefAssets.length} 张 ✅` : "未上传"}\n• 参考模板：${templateAsset ? "已上传 ✅" : "未上传"}\n• 风格描述：可在聊天中直接告诉我（如"更商务一点""要温暖生活感"）\n\n你可以：**① 继续上传素材**（参考图 / 模板 / Logo）→ **② 告诉我你的风格偏好** → **③ 或直接说"开始提案"**，我会基于现有信息立即生成风格方案。`;
      } else if (targetStage === "planning") {
        rollbackMsg += `\n\n**内容总监已重新介入。** 你可以继续调整内容规划：\n\n• 增减页数、调整章节结构\n• 修改某一页的标题或正文（直接说"修改第X页"）\n• 更换整体内容方向或主题\n\n👉 确认内容规划满意后，我们再一起进入视觉设计阶段。`;
      } else if (targetStage === "prompt_ready") {
        rollbackMsg += `\n\n你可以重新选择或调整风格，我会基于新的风格重新为每一页生成生图 Prompt。\n\n👉 确认风格后，点击「确认风格，生成生图方案」即可。`;
      } else if (targetStage === "prototype_ready") {
        rollbackMsg += `\n\n你可以重新选择打样页面或调整风格，然后再次打样确认。\n\n👉 选择页面后点击「打样确认」即可。`;
      }
      setChatMessages((prev) => [
        ...prev.filter((m) => !m.loading),
        { role: "system" as const, content: `用户回退到「${stageNames[targetStage] || targetStage}」阶段` },
        { role: "agent" as const, content: rollbackMsg, agentRole: targetStage === "planning" ? "content" : "visual" },
      ]);

      // 根据回退目标调整 Agent 角色
      if (targetStage === "planning") {
        setCurrentAgentRole("content");
        setContentPlanConfirmed(false);
      } else if (targetStage === "visual_ready") {
        setCurrentAgentRole("visual");
        setContentPlanConfirmed(true);
      }

      showToast("回退成功", "success");
    } catch (err: any) {
      showToast("回退失败：" + (err.message || "未知错误"), "error");
      setChatMessages((prev) => [
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

  const handleRetry = async (slideId: string) => {
    if (!selectedProject) return;
    if (operatingProjectId === selectedProject.id) return;
    const slide = slides.find((s) => s.id === slideId);
    setOperatingProjectId(selectedProject.id);
    try {
      await retrySlide(selectedProject.id, slideId);
      await loadSlides(selectedProject.id);
      setChatMessages((prev) => [
        ...prev,
        {
          role: "agent",
          content: `🔄 已重新生成第 ${slide?.page_num || "?"} 页。\n\n👉 请稍等片刻，生成完成后页面会自动更新。`,
          agentRole: "visual",
        },
      ]);
    } catch (err: any) {
      showToast("重试失败：" + (err.message || "未知错误"), "error");
      setChatMessages((prev) => [
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
    try {
      const result = await retryFailed(selectedProject.id);
      showToast(`已启动 ${result.count} 个失败页面的重试`, "success");
      await loadSlides(selectedProject.id);
      addSystemLog(`用户重试了 ${result.count} 个失败页面`);
      setChatMessages((prev) => [
        ...prev,
        {
          role: "agent",
          content: `🔄 已启动 ${result.count} 个失败页面的重试（第 ${result.page_nums.join(", ")} 页）。\n\n👉 请稍等片刻，生成完成后页面会自动更新。`,
          agentRole: "visual",
        },
      ]);
    } catch (err: any) {
      showToast("批量重试失败：" + (err.message || "未知错误"), "error");
      setChatMessages((prev) => [
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

  const handleSetSeed = async (slideId: string) => {
    if (!selectedProject) return;
    const slide = slides.find((s) => s.id === slideId);
    try {
      await setSeedPage(selectedProject.id, slideId);
      await loadSlides(selectedProject.id);
      slides.forEach((s) => markSlideStale(s.id, "content"));
      if (slide) addSystemLog(`用户将第 ${slide.page_num} 页设为种子页`);
    } catch (err: any) {
      showToast("设置种子页失败：" + (err.message || "未知错误"), "error");
    }
  };

  const handleUnsetSeed = async (slideId: string) => {
    if (!selectedProject) return;
    const slide = slides.find((s) => s.id === slideId);
    try {
      await unsetSeedPage(selectedProject.id, slideId);
      await loadSlides(selectedProject.id);
      slides.forEach((s) => markSlideStale(s.id, "content"));
      if (slide) addSystemLog(`用户取消了第 ${slide.page_num} 页的种子页设置`);
    } catch (err: any) {
      showToast("取消种子页失败：" + (err.message || "未知错误"), "error");
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
        await uploadFile(selectedProject.id, file, "content_ref", slideId);
        markSlideStale(slideId, "content");
        await loadSlides(selectedProject.id);
        const slide = slides.find((s) => s.id === slideId);
        addSystemLog(`用户为第 ${slide?.page_num || "?"} 页上传了参考图（融合模式）`);
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
        setChatMessages((prev) => [
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

    // 如果已经确认过，只切回视觉总监，不走完整流程
    if (contentPlanConfirmed) {
      setCurrentAgentRole("visual");
      return;
    }

    isConfirmingRef.current = true;
    setConfirmingPlan(true);
    setChatLoading(true);

    try {
      // 保存当前内容快照用于软锁定检测
      const currentSlides = await fetchSlides(selectedProject.id);
      setContentPlanSnapshot(currentSlides);
      softLockWarnedRef.current = false;

      // 内容总监：获取参考图推荐列表
      let suggestions: any[] = [];
      try {
        const suggestRes = await suggestReferenceImages(selectedProject.id);
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
          "\n\n你可以在左侧全局预览中点击「+ 参考图」上传图片，或在单页编辑中管理参考图。";
        setChatMessages((prev) => [
          ...prev,
          {
            role: "agent",
            content: suggestionText,
            agentRole: "content",
          },
        ]);
      }

      // 检查是否已有设计素材
      const hasAssets = referenceImages.length > 0;
      const logoAsset = referenceImages.find((r) => r.role === "logo");
      const styleRefAssets = referenceImages.filter((r) => r.role === "style_ref");
      const templateAsset = referenceImages.find((r) => r.role === "template");
      const assetDesc = [
        logoAsset ? "Logo" : "",
        styleRefAssets.length > 0 ? `${styleRefAssets.length}张风格参考图` : "",
        templateAsset ? "参考模板" : "",
      ].filter(Boolean).join("、");

      // 1. 先获取视觉总监开场白（优先让用户看到反馈）
      const history = chatMessages.map((m) => ({ role: m.role, content: m.content }));
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      let result: any = null;

      const initialMessage = hasAssets
        ? `用户已确认内容规划。用户已上传以下设计素材：${assetDesc}。请基于这些素材直接分析并输出风格提案，不要询问素材情况。如果还需要补充素材，可以简要询问。`
        : "用户已确认内容规划。用户目前尚未上传任何设计素材。请先引导用户上传素材（参考模板、参考图、Logo、风格描述），不要直接提供风格提案。";

      try {
        for await (const event of chatWithAgentStream(
          selectedProject.id,
          initialMessage,
          history,
          ctrl.signal,
          undefined,
          "visual"
        )) {
          if (event.type === "result") {
            result = event.data;
          } else if (event.type === "error") {
            throw new Error(event.message || "请求出错");
          }
        }
      } finally {
        abortRef.current = null;
      }

      if (ctrl.signal.aborted) return;

      // API 全部成功后再切换状态，避免失败时状态错乱
      setContentPlanConfirmed(true);
      setCurrentAgentRole("visual");
      try {
        await updateProject(selectedProject.id, { content_plan_confirmed: true });
      } catch (e) {
        console.warn("更新 content_plan_confirmed 失败", e);
      }
      addSystemLog(`用户确认了内容规划，共 ${slides.length} 页`);

      // 立即显示视觉总监消息
      const directorMsg = result?.response ||
        (hasAssets
          ? "我是视觉总监。已收到你上传的设计素材，让我基于这些素材为你制定视觉方案。"
          : "我是视觉总监。为了给你最精准的视觉方案，请问你是否能提供以下素材？\n\n1. **参考模板**（PPT/PDF）\n2. **参考图**（喜欢的设计风格截图）\n3. **Logo**（品牌标识）\n4. **风格描述**（文字描述）\n\n如果有以上素材，请直接上传或描述。如果没有，我将基于内容自行推荐。");
      setChatMessages((prev) => [
        ...prev,
        {
          role: "agent",
          content: directorMsg,
          agentRole: "visual",
          action: result?.action,
        },
      ]);

      // 风格提案延后生成：等用户在聊天中确认素材状态后，再由 Agent action 触发
      // 或者用户直接在主舞台点击按钮触发
    } catch (err: any) {
      console.error("[ConfirmContentPlan] error:", err);
      const isNetworkError = err.message?.includes("网络") || err.message?.includes("连接") || err.message?.includes("Connection");
      if (isNetworkError) {
        setChatMessages((prev) => [
          ...prev,
          { role: "agent", content: "⏹ 连接已中断（你可能切换了标签页或网络波动）。\n\n👉 解决方法：\n1. 检查网络连接\n2. 点击下方「确认内容，请视觉总监 →」按钮重试\n3. 如果多次失败，请刷新页面", agentRole: "content" },
        ]);
      } else {
        showToast("视觉总监介入失败，请重试", "error");
        // 失败时重置状态，让用户可以再次点击确认
        setContentPlanConfirmed(false);
        setCurrentAgentRole("content");
        setContentPlanSnapshot([]);
        softLockWarnedRef.current = false;
        setChatMessages((prev) => [
          ...prev,
          { role: "agent", content: "❌ 视觉总监介入失败：" + (err.message || "未知错误") + "\n\n👉 请点击下方「确认内容，请视觉总监 →」按钮重试。", agentRole: "content" },
        ]);
      }
    } finally {
      setConfirmingPlan(false);
      setChatLoading(false);
      isConfirmingRef.current = false;
    }
  };

  const handleSwitchToContentDirector = () => {
    // 清理可能残留的加载状态
    setChatLoading(false);
    setThinkingContent("");
    setThinkingExpanded(false);
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setCurrentAgentRole("content");
    // 仅切换角色，不重置确认状态——用户只是想回内容总监问问题，不是推翻确认
    setStyleProposalsInChat([]);
    setChatMessages((prev) => [
      ...prev.filter((m) => !m.loading),
      {
        role: "agent",
        content: "内容总监已重新介入。你可以继续调整内容规划，调整完成后可以再次确认。",
        agentRole: "content",
      },
    ]);
  };

  const handleSendChat = async (forcedMsg?: string, baseHistory?: typeof chatMessages) => {
    if (!selectedProject) return;
    const userMsg = (forcedMsg || chatInput).trim();
    const hasAttachments = pendingAttachments.length > 0;
    if (!userMsg && !hasAttachments) return;

    // 构建用户消息展示内容（包含附件引用）
    let displayContent = userMsg;
    if (hasAttachments) {
      const attachmentText = pendingAttachments.map((f) => `📎 ${f}`).join("\n");
      displayContent = userMsg ? `${userMsg}\n\n${attachmentText}` : attachmentText;
    }

      const newMessage = { role: "user" as const, content: displayContent };

      const chatResultLooksValid = (r: unknown): boolean =>
        r != null && typeof r === "object" && !Array.isArray(r);

      // 先更新 UI 显示用户消息
    setChatMessages((prev) => [...prev, newMessage]);
    setChatInput("");
    setPendingAttachments([]);
    setChatLoading(true);
    setThinkingContent("");
    setThinkingExpanded(false);

    // 创建 AbortController 用于停止输出
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      // 使用 baseHistory（编辑消息时传入）或当前 chatMessages，确保包含最新用户消息
      const msgList = baseHistory || chatMessages;
      const history = [...msgList, newMessage].map((m) => ({
        role: m.role === "agent" ? "assistant" : m.role,
        content: m.content,
      }));
      let result: any = null;

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

      for await (const event of chatWithAgentStream(selectedProject.id, userMsg, history, ctrl.signal, pageContext, currentAgentRole)) {
        if (event.type === "thinking") {
          setThinkingContent((prev) => prev + event.delta);
        } else if (event.type === "result") {
          result = event.data;
          if (import.meta.env.DEV) {
            console.debug("[handleSendChat] received result:", result);
          }
        } else if (event.type === "error") {
          setChatMessages((prev) => [...prev, { role: "agent", content: `❌ ${event.message || "请求出错"}`, agentRole: currentAgentRole }]);
          setChatLoading(false);
          abortRef.current = null;
          return;
        }
      }

      if (import.meta.env.DEV) {
        console.debug("[handleSendChat] stream ended, result=", result, "aborted=", ctrl.signal.aborted);
      }

      if (ctrl.signal.aborted) return;

      if (!chatResultLooksValid(result) && !ctrl.signal.aborted) {
        setChatMessages((prev) => [...prev, { role: "system", content: "🔄 响应不完整，正在自动重试..." }]);
        const retryCtrl = new AbortController();
        abortRef.current = retryCtrl;
        try {
          for await (const event of chatWithAgentStream(selectedProject.id, userMsg, history, retryCtrl.signal, pageContext, currentAgentRole)) {
            if (event.type === "result") {
              result = event.data;
            } else if (event.type === "error") {
              setChatMessages((prev) => [...prev, { role: "agent", content: `❌ ${event.message || "请求出错"}`, agentRole: currentAgentRole }]);
              setChatLoading(false);
              abortRef.current = null;
              return;
            }
          }
        } catch (retryErr: any) {
          if (retryErr?.name !== "AbortError") {
            setChatMessages((prev) => [...prev, { role: "agent", content: "请求失败，请重试。", agentRole: currentAgentRole }]);
          }
        } finally {
          abortRef.current = null;
        }
      }

      if (!chatResultLooksValid(result)) {
        setChatMessages((prev) => [
          ...prev,
          { role: "agent", content: "⚠️ 响应未返回完整结果，请重试一次。", agentRole: currentAgentRole },
        ]);
        setChatLoading(false);
        return;
      }

      // 如果重试流被用户主动中断，不继续处理
      if (abortRef.current?.signal?.aborted) return;

      const agentReply = result.response || result.message || "...";
      setChatMessages((prev) => [
        ...prev,
        {
          role: "agent",
          content: agentReply,
          action: result.action,
          positioning: result.positioning,
          topic: result.topic,
          agentRole: currentAgentRole,
        },
      ]);

      // 如果项目还是默认名，Agent 已经推断出主题，自动重命名
      if (result.title && selectedProject?.title === "未命名项目") {
        try {
          await updateProject(selectedProject.id, { title: result.title });
          await loadProjects();
        } catch (e) {
          console.warn("Auto-rename after chat error:", e);
        }
      }

      // Agent 在聊天中确认风格，自动保存并推进
      if (result.action === "confirm_style" && result.style) {
        setChatMessages((prev) => [
          ...prev,
          {
            role: "agent",
            content: `✅ 风格「${result.style.name || "已选风格"}」已确认。正在保存并进入画面设计阶段...`,
            agentRole: "visual",
          },
        ]);
        await handleSelectStyle(result.style);
      }

      // Agent 要求重新生成指定页
      if (result.action === "regenerate_pages" && result.page_nums?.length > 0) {
        const targetSlides = slides.filter((s) => result.page_nums.includes(s.page_num));
        targetSlides.forEach((s) => markSlideStale(s.id, "image"));
        setChatMessages((prev) => [
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
        setChatMessages((prev) => [
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
          setChatMessages((prev) => [
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
          setChatMessages((prev) => [
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
            // 自动更新生图提示词
            await handleUpdateStaleSlides([targetSlide.id], { local: true });
            setChatMessages((prev) => [
              ...prev,
              {
                role: "agent",
                content: `✅ 已更新第 ${pageNum} 页的视觉描述，并重新生成了生图提示词。请检查画面描述和生图指令，确认后可点击「确认生成图片」。`,
                agentRole: "visual",
              },
            ]);
          } catch (err: any) {
            setChatMessages((prev) => [
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
        setChatMessages((prev) => [
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
        }
        let msg = `✅ 已更新第 ${updatedPageNums.join(", ")} 页的视觉描述，并重新生成了生图提示词。`;
        if (skipped.length > 0) msg += `（跳过不存在的页：${skipped.join(", ")}）`;
        setChatMessages((prev) => [
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
        setChatMessages((prev) => [
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

      // 视觉总监识别到内容问题，自动转接内容总监
      if (result.action === "forward_to_content" && currentAgentRole === "visual") {
        setCurrentAgentRole("content");
        setChatMessages((prev) => [
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
          setChatMessages((prev) => [
            ...prev,
            {
              role: "agent",
              content: "我是视觉总监，负责设计风格和画面效果。如果你想调整内容规划，请切换到内容总监继续。",
              agentRole: "visual",
            },
          ]);
        } else {
          setChatMessages((prev) => [
            ...prev,
            { role: "agent", content: `正在重新生成内容规划：${result.topic.slice(0, 50)}...` },
          ]);
          await startContentPlanPoll(selectedProject.id, result.topic, "agent", result.page_count);
        }
      }

      // 视觉总监确认素材状态，触发风格提案生成
      if ((result.action === "propose_styles" || result.action === "adjust_style") && currentAgentRole === "visual" && selectedProject) {
        // 优先使用 Agent 聊天返回的实时风格提案（与聊天建议保持一致）
        if (result.style_proposal && typeof result.style_proposal === "object") {
          const proposal = result.style_proposal;
          // 标准化 palette 格式
          if (proposal.palette && Array.isArray(proposal.palette)) {
            proposal.palette = proposal.palette.map((c: any) => {
              if (typeof c === "string") return { name: c, hex: c, role: "" };
              return c;
            });
          }
          setStyleProposalsInChat([proposal]);
          setChatMessages((prev) => [
            ...prev,
            {
              role: "agent",
              content:
                "✅ 风格提案已生成，请查看主舞台。\n\n👉 下一步：如果满意请点击「选择此方案」；如果想调整，直接告诉我（如「更商务一点」「配色再暖一些」）。",
              agentRole: "visual",
            },
          ]);
        } else {
          // Agent 没有返回结构化提案，回退到后端生成
          showToast("正在生成风格提案...", "info");
          setOperatingProjectId(selectedProject.id);
          const styleLoadingId = `sp-${Date.now()}`;
          setChatMessages((prev) => [
            ...prev,
            { role: "agent", content: "⏳ 正在生成风格提案，请稍候...", agentRole: "visual", loading: true, id: styleLoadingId },
          ]);
          try {
            const styleResult = await generateStyleProposals(selectedProject.id);
            if (styleResult.status === "generating") {
              showToast("风格提案后台生成中，请稍候...", "info");
              await pollForStyleProposals(selectedProject.id);
            }
            await loadProjects();
            const fresh = await fetchProjects();
            const updated = fresh.find((p: Project) => p.id === selectedProject.id);
            if (updated) setSelectedProject(updated);
            setChatMessages((prev) => [
              ...prev.filter((m) => m.id !== styleLoadingId),
              {
                role: "agent",
                content:
                  "✅ 风格提案已生成，请查看主舞台。\n\n👉 下一步：从三套方案中选择最喜欢的一套，或直接告诉我你的偏好，我会进一步调整。",
                agentRole: "visual",
              },
            ]);
          } catch (err: any) {
            showToast("风格提案生成失败：" + (err.message || "未知错误"), "error");
            setChatMessages((prev) => [
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
        setChatMessages((prev) => [
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
          await loadSlides(selectedProject.id);
          // 如果当前正在编辑这页，同步更新 editingSlide
          if (editingSlide && editingSlide.page_num === pageNum) {
            const updated = await fetchSlides(selectedProject.id);
            const freshSlide = updated.find((s: Slide) => s.page_num === pageNum);
            if (freshSlide) {
              setEditingSlide(freshSlide);
            }
          }
          setChatMessages((prev) => [
            ...prev,
            { role: "agent", content: `✅ 已更新第 ${pageNum} 页内容。` },
          ]);
        } catch (err: any) {
          setChatMessages((prev) => [
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
        setChatMessages((prev) => [
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
          setChatMessages((prev) => [...prev, { role: "agent", content: msg }]);
        } catch (err: any) {
          setChatMessages((prev) => [
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
        setChatMessages((prev) => [
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
          await loadSlides(selectedProject.id);
          // 同步更新 editingSlide（如果当前正在编辑，page_num 可能变了）
          if (editingSlide) {
            const updated = await fetchSlides(selectedProject.id);
            const freshSlide = updated.find((s: Slide) => s.id === editingSlide.id);
            if (freshSlide) {
              setEditingSlide(freshSlide);
            }
          }
          setChatMessages((prev) => [
            ...prev,
            { role: "agent", content: `✅ 已在第 ${pageNum} 页前插入新页。` },
          ]);
        } catch (err: any) {
          setChatMessages((prev) => [
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
        setChatMessages((prev) => [
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
          await loadSlides(selectedProject.id);
          // 同步更新 editingSlide
          if (editingSlide) {
            const updated = await fetchSlides(selectedProject.id);
            const freshSlide = updated.find((s: Slide) => s.id === editingSlide.id);
            if (freshSlide) {
              setEditingSlide(freshSlide);
            }
          }
          setChatMessages((prev) => [
            ...prev,
            { role: "agent", content: `✅ 已在第 ${pageNum} 页后插入新页。` },
          ]);
        } catch (err: any) {
          setChatMessages((prev) => [
            ...prev,
            { role: "agent", content: "插入页面失败：" + (err.message || "未知错误") },
          ]);
        } finally {
          setOperatingProjectId(null);
        }
      }
    } catch (err: any) {
      if (err?.name === "AbortError") {
        const isVisual = currentAgentRole === "visual";
        setChatMessages((prev) => [
          ...prev,
          {
            role: "agent",
            content: isVisual
              ? "⏹ 连接已中断（你可能切换了标签页或网络波动）。任务可能仍在后台运行，请稍等片刻后刷新页面查看最新状态。"
              : "⏹ 连接已中断（你可能切换了标签页或网络波动）。如果内容规划已部分生成，请检查左侧页面卡片。",
          },
        ]);
      } else {
        setChatMessages((prev) => [...prev, { role: "agent", content: "请求失败，请重试。" }]);
      }
    } finally {
      abortRef.current = null;
      setChatLoading(false);
    }
  };

  const handleStopChat = () => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
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
    setChatMessages(newMessages);
    setEditingMessageIndex(null);
    setEditMessageContent("");
    // 重新发送编辑后的消息，传入裁剪后的历史避免闭包拿到旧状态
    setTimeout(() => handleSendChat(trimmed, newMessages), 0);
  };

  const handleDeleteMessage = (index: number) => {
    // 删除该消息及其之后的所有消息（回滚）
    const newMessages = chatMessages.slice(0, index);
    setChatMessages(newMessages);
    if (editingMessageIndex !== null && editingMessageIndex >= index) {
      setEditingMessageIndex(null);
      setEditMessageContent("");
    }
  };

  const handleDropFiles = async (files: FileList) => {
    if (!selectedProject) return;
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

  const statusIcon: Record<string, string> = {
    pending: "⏳",
    visual_ready: "✨",
    prompt_ready: "📝",
    prototype: "🧪",
    prototype_ready: "👀",
    generating: "🔥",
    completed: "✅",
    failed: "❌",
  };

  const currentStatus = selectedProject?.status || "draft";
  const workflowState = buildWorkflowState({
    projectStatus: currentStatus,
    slides,
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

  const stepTone = (status: string, canRollback: boolean) => {
    if (status === "error") return "border-red-300 bg-red-50 text-red-700";
    if (status === "current") return "border-blue-400 bg-blue-50 text-blue-800";
    if (canRollback) return "border-green-200 bg-green-50 text-green-800 hover:border-green-300 hover:bg-green-100";
    return "border-gray-200 bg-gray-50 text-gray-400";
  };

  const isLoadingStatus = workflowState.isLoading;

  // 当前步骤引导文案
  const getGuidanceText = () => {
    return getWorkflowGuidanceText(workflowState);
  };

  const topPrimaryAction: UiAction | null = (() => {
    if (!selectedProject) return null;
    const actionKey = getPrimaryActionKey(workflowState);
    if (actionKey === "start-prototype") {
      return {
        key: "prototype",
        label: isBusy ? "启动中..." : "先打样种子页",
        onClick: () => handleStartGeneration(false, true),
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
    const actionKeys = getSecondaryActionKeys(workflowState);
    const actions: UiAction[] = [];
    if (actionKeys.includes("templates")) {
      actions.push({
        key: "templates",
        label: "查看模板",
        onClick: () => setShowTemplateRecommender(true),
        variant: "secondary",
      });
    }
    if (actionKeys.includes("sample-selected")) {
      actions.push({
        key: "sample-selected",
        label: `打样 ${selectedPages.size} 页`,
        onClick: () => handleStartGeneration(true, false),
        variant: "secondary",
        disabled: isBusy,
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
                    setSelectedProject(p);
                    setShowPrototypePreview(true);
                    setStaleMap({});
                    setEditingSlide(null);
                    setAgentMode("global");
                    setContentPlanSnapshot([]);
                    setStyleProposalsInChat([]);
                    // 根据项目已有状态推断阶段：
                    // - 有 selected_style 说明已走过视觉阶段，保持视觉总监角色
                    // - 有 slides 但未确认风格，显示确认条（content 角色 + 未确认）
                    // - 纯 draft 无 slides，内容总监角色
                    if (p.selected_style) {
                      setCurrentAgentRole("visual");
                      setContentPlanConfirmed(true);
                    } else {
                      setCurrentAgentRole("content");
                      setContentPlanConfirmed(false);
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
                      <div className="font-medium text-sm truncate flex-1">{p.title}</div>
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
        <header className="min-h-14 border-b bg-white flex items-center px-3 justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 min-w-0">
              <span className="font-medium truncate max-w-[300px]" title={selectedProject?.title}>
                {selectedProject ? selectedProject.title : "预览区"}
              </span>
              {selectedProject && (
                <span className="text-[11px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">
                  {statusLabel[currentStatus] || currentStatus}
                </span>
              )}
            </div>
            {selectedProject && currentStatus === "prototype_ready" && (
              <div className="text-[11px] text-gray-500 mt-0.5">
                当前视图：{showPrototypePreview ? "打样结果" : "全局预览"}。视图切换不会改变项目数据。
              </div>
            )}
          </div>
          <div className="flex items-center gap-2 flex-wrap justify-end">
            {currentStatus === "prototype" && (
              <span className="text-sm text-orange-600 font-medium">
                种子页打样中...
              </span>
            )}
            {currentStatus === "generating" && (
              <div className="flex items-center gap-2">
                <span className="text-sm text-orange-600 font-medium">
                  生成中 {projectStatus?.completed_slides || 0}/{projectStatus?.target_count || projectStatus?.total_slides || 0}...
                </span>
                <button
                  onClick={handleStopGeneration}
                  className="text-xs bg-red-50 text-red-600 px-2 py-1 rounded hover:bg-red-100 border border-red-200"
                >
                  停止生成
                </button>
              </div>
            )}
            {topSecondaryActions.map(renderTopAction)}
            {topPrimaryAction && renderTopAction(topPrimaryAction)}
          </div>
        </header>

        {/* 项目进程时间线 */}
        {selectedProject && (
          <div className="px-4 py-3 bg-white border-b">
            <div className="flex items-center justify-between gap-3 overflow-x-auto">
              {steps.map((step, idx) => {
                const status = stepStatus(idx);
                const canRollback = status === "done";
                const isCurrentLoading = status === "current" && isLoadingStatus;
                return (
                  <div key={step.key} className="flex items-center gap-2 min-w-fit">
                    <button
                      onClick={() => {
                        if (!canRollback) return;
                        handleRollback(step.key as any);
                      }}
                      disabled={!canRollback || isBusy}
                      title={canRollback && !isBusy ? `回退到「${step.label}」：会清除后续阶段数据` : step.label}
                      className={`group border rounded-xl px-3 py-2 min-w-[112px] text-left transition-all ${stepTone(status, canRollback && !isBusy)} ${
                        canRollback && !isBusy ? "cursor-pointer" : "cursor-default"
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        <span className={`w-5 h-5 rounded-full flex items-center justify-center text-[11px] font-bold ${
                          status === "current"
                            ? "bg-blue-600 text-white"
                            : status === "error"
                            ? "bg-red-600 text-white"
                            : canRollback
                            ? "bg-green-600 text-white"
                            : "bg-gray-200 text-gray-500"
                        }`}>
                          {status === "done" ? "✓" : idx + 1}
                        </span>
                        <span className="text-sm font-medium whitespace-nowrap">{step.label}</span>
                        {isCurrentLoading && (
                          <span className="inline-block w-3 h-3 border-2 border-blue-200 border-t-blue-600 rounded-full animate-spin" />
                        )}
                      </div>
                      <div className="mt-1 text-[10px] opacity-80">
                        {status === "current"
                          ? "当前步骤"
                          : status === "error"
                          ? "需要处理"
                          : canRollback
                          ? "可回退"
                          : "未开始"}
                      </div>
                    </button>
                    {idx < steps.length - 1 && (
                      <span className={`text-xl ${idx < displayStepIndex ? "text-green-400" : "text-gray-300"}`}>→</span>
                    )}
                  </div>
                );
              })}
            </div>
            {/* 引导文案 */}
            {getGuidanceText() && (
              <div className="mt-2 flex items-start justify-between gap-3 rounded bg-blue-50 px-3 py-2">
                <div className="text-[12px] text-blue-900">
                  <span className="font-medium">现在：</span>
                  {getGuidanceText()}
                </div>
                <div className="text-[11px] text-blue-700 whitespace-nowrap">
                  已完成步骤可点击回退
                </div>
                {currentStatus === "prototype_ready" && (
                  <div className="text-[11px] text-blue-700 whitespace-nowrap border-l border-blue-200 pl-3">
                    可切换：全局预览 / 打样结果
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* 选页工具栏：只在 prompt_ready / failed 阶段显示 */}
        {selectedProject && slides.length > 0 && (currentStatus === "prompt_ready" || currentStatus === "failed") && (
          <div className="px-3 py-1.5 bg-gray-100 border-b flex items-center gap-3 text-sm">
            <span className="text-gray-600">选页打样：</span>
            <button onClick={selectAll} className="text-blue-600 hover:underline">
              全选
            </button>
            <button onClick={clearSelection} className="text-gray-500 hover:underline">
              清空
            </button>
            <span className="text-gray-400">|</span>
            <span className="text-gray-600">
              已选 {selectedPages.size} / {slides.length} 页
            </span>
          </div>
        )}

        {/* 设计素材面板 */}
        {selectedProject && referenceImages.length > 0 && (
          <VisualAssetsPanel
            referenceImages={referenceImages}
            templateRecommendations={selectedProject?.selected_template_recommendations}
            templatePages={templatePages}
            apiBase={API_BASE}
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
                if (deletedRef && (deletedRef.role === "style_ref" || deletedRef.role === "logo")) {
                  slides.forEach((s) => markSlideStale(s.id, "content"));
                }
                if (deletedRef) {
                  const roleMap: Record<string, string> = { style_ref: "风格参考图", logo: "Logo", template: "模板" };
                  addSystemLog(`用户删除了全局${roleMap[deletedRef.role] || "参考图"}`);
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
        )}

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
                  <span>👉</span>
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
              <div className="text-center mb-6">
                <h2 className="text-lg font-bold text-gray-800 mb-1">效果预览确认</h2>
                <p className="text-sm text-gray-500">
                  以下页面已生成预览，确认效果满意后即可启动批量生成
                </p>
                <button
                  onClick={() => setShowPrototypePreview(false)}
                  className="mt-3 text-sm text-blue-600 hover:text-blue-800 underline"
                >
                  先返回全局预览，继续检查和调整页面
                </button>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
                {slides
                  .filter((s) => s.visual_json?.is_seed_recommended)
                  .map((slide) => (
                    <div
                      key={slide.id}
                      className="border rounded-lg p-3 flex flex-col items-center bg-white"
                    >
                      <div className="text-xs text-gray-500 mb-2 font-medium">
                        {typeLabel[slide.type] || slide.type} · 第 {slide.page_num} 页
                      </div>
                      {slide.image_path ? (
                        <div
                          className="aspect-video w-full rounded overflow-hidden bg-gray-100 mb-2 cursor-pointer"
                          onClick={() => {
                            const allUrls = slides
                              .filter((s) => s.status === "completed" && s.image_path)
                              .sort((a, b) => a.page_num - b.page_num)
                              .map((s) => getSlideImageUrl(s.image_path!, s.status));
                            const url = getSlideImageUrl(slide.image_path!, slide.status);
                            const index = allUrls.indexOf(url);
                            setGalleryModal({ urls: allUrls, index: index >= 0 ? index : 0, title: "PPT 预览" });
                          }}
                        >
                          <img
                            src={getSlideImageUrl(slide.image_path, slide.status)}
                            alt={`Slide ${slide.page_num}`}
                            className="w-full h-full object-cover"
                            onError={(e) => {
                              (e.target as HTMLImageElement).style.display = "none";
                            }}
                          />
                        </div>
                      ) : (
                        <div className="aspect-video w-full rounded bg-gray-100 mb-2 flex items-center justify-center text-xs text-gray-400">
                          图片加载中...
                        </div>
                      )}
                      {staleMap[slide.id]?.content && !["draft", "planning", "content_plan_ready"].includes(currentStatus) && (
                        <div className="mt-1 text-[10px] text-blue-600 bg-blue-50 rounded px-2 py-0.5">需更新画面方案</div>
                      )}
                      {staleMap[slide.id]?.visual && (
                        <div className="mt-1 text-[10px] text-orange-600 bg-orange-50 rounded px-2 py-0.5">需更新画面方案</div>
                      )}
                      {staleMap[slide.id]?.image && (
                        <div className="mt-1 text-[10px] text-purple-600 bg-purple-50 rounded px-2 py-0.5">需重新生成图片</div>
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
                  <span className="text-xs text-gray-400">种子页已高亮</span>
                </div>
                <div className="grid grid-cols-4 sm:grid-cols-6 md:grid-cols-8 gap-2">
                  {slides.map((slide) => {
                    const isSeed = slide.visual_json?.is_seed_recommended;
                    const headline = slide.content_json?.headline || slide.content_json?.text_content?.headline || "";
                    return (
                      <div
                        key={slide.id}
                        className={`border rounded p-2 flex flex-col items-center text-center ${
                          isSeed ? "border-rose-300 bg-rose-50 ring-1 ring-rose-200" : "border-gray-200 bg-white"
                        }`}
                        title={headline}
                      >
                        <div className={`text-[10px] px-1.5 py-0.5 rounded mb-1 font-medium ${
                          typeColor[slide.type] || "bg-gray-100 text-gray-600"
                        }`}>
                          {typeLabel[slide.type] || slide.type}
                        </div>
                        <div className="text-[10px] text-gray-400 mb-1">P{slide.page_num}</div>
                        {slide.image_path ? (
                          <div
                            className="aspect-video w-full rounded overflow-hidden bg-gray-100 cursor-pointer"
                            onClick={() => {
                              const allUrls = slides
                                .filter((s) => s.status === "completed" && s.image_path)
                                .sort((a, b) => a.page_num - b.page_num)
                                .map((s) => getSlideImageUrl(s.image_path!, s.status));
                              const url = getSlideImageUrl(slide.image_path!, slide.status);
                              const index = allUrls.indexOf(url);
                              setGalleryModal({ urls: allUrls, index: index >= 0 ? index : 0, title: "PPT 预览" });
                            }}
                          >
                            <img
                              src={getSlideImageUrl(slide.image_path, slide.status)}
                              alt={`Slide ${slide.page_num}`}
                              className="w-full h-full object-cover"
                              onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                            />
                          </div>
                        ) : (
                          <div className="aspect-video w-full rounded bg-gray-50 flex items-center justify-center">
                            <span className="text-[10px] text-gray-300">
                              {isSeed ? "🌱" : "⏳"}
                            </span>
                          </div>
                        )}
                        <div className="text-[10px] text-gray-500 mt-1 truncate w-full leading-tight">
                          {headline || "未命名"}
                        </div>
                        {staleMap[slide.id]?.content && !["draft", "planning", "content_plan_ready"].includes(currentStatus) && (
                          <div className="mt-0.5 text-[9px] text-blue-600 bg-blue-50 rounded px-1 truncate">需更新画面方案</div>
                        )}
                        {staleMap[slide.id]?.visual && (
                          <div className="mt-0.5 text-[9px] text-orange-600 bg-orange-50 rounded px-1 truncate">需更新画面方案</div>
                        )}
                        {staleMap[slide.id]?.image && (
                          <div className="mt-0.5 text-[9px] text-purple-600 bg-purple-50 rounded px-1 truncate">需重新生成图片</div>
                        )}
                        {slide.status === "failed" && (
                          <button
                            onClick={() => handleRetry(slide.id)}
                            disabled={isBusy}
                            className="mt-1 text-[10px] bg-red-50 text-red-600 px-1.5 py-0.5 rounded hover:bg-red-100 disabled:opacity-50"
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
                  {isBusy ? "启动中..." : "✅ 确认预览效果，开始批量生成"}
                </button>
                <button
                  onClick={() => handleStartGeneration(false, true)}
                  disabled={isBusy}
                  className="text-sm bg-gray-200 text-gray-700 px-6 py-2 rounded hover:bg-gray-300 disabled:opacity-50"
                >
                  {isBusy ? "启动中..." : "🔄 重新打样"}
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
              unescapeText={unescapeText}
              onImageClick={(url) => {
                if (url.includes("/uploads/")) {
                  const refUrls = editingSlide?.reference_images?.map((r: any) => `${API_BASE}${r.url}`) || [];
                  const index = refUrls.indexOf(url);
                  setGalleryModal({ urls: refUrls, index: index >= 0 ? index : 0, title: "参考图片" });
                } else {
                  const slideUrls = slides
                    .filter((s) => s.status === "completed" && s.image_path)
                    .sort((a, b) => a.page_num - b.page_num)
                    .map((s) => getSlideImageUrl(s.image_path!, s.status));
                  const index = slideUrls.indexOf(url);
                  setGalleryModal({ urls: slideUrls, index: index >= 0 ? index : 0, title: "PPT 预览" });
                }
              }}
              onToast={showToast}
              markSlideStale={markSlideStale}
              staleStatus={staleMap[editingSlide.id]}
              projectStatus={currentStatus}
              onUpdateStale={() => handleUpdateStaleSlides([editingSlide.id], { local: true })}
              onGenerateImages={() => handleGenerateStaleImages([editingSlide.id], { local: true })}
              onSystemLog={addSystemLog}
            />
          ) : ((currentStatus === "visual_ready" || (currentStatus === "planning" && contentPlanConfirmed)) || styleProposalsLoading || styleProposalsInChat.length > 0 || selectedProject?.style_proposal?.proposals?.length > 0) && !selectedProject?.selected_style ? (
            <div className="h-full flex flex-col">
              <div className="text-center py-4">
                <h2 className="text-lg font-bold text-gray-800 mb-1">Visual Director 风格提案</h2>
                <p className="text-sm text-gray-500">根据您的内容，推荐以下视觉风格方案</p>
              </div>
              <div className="flex-1 overflow-auto px-4 pb-4">
                <StyleProposalSelector
                  proposals={
                    selectedProject?.style_proposal?.proposals || styleProposalsInChat || [
                      {
                        name: "深海商务",
                        palette: ["#1E3A5F", "#F5F5F0", "#D4A574", "#2C5282"],
                        mood: "冷静、专业、适合金融场景",
                        font: "无衬线，标题加粗",
                        description: "深色背景配金色点缀，营造沉稳专业的商务氛围",
                      },
                      {
                        name: "暖调极简",
                        palette: ["#F7F3EE", "#E8DDD0", "#C4A882", "#8B7355"],
                        mood: "温暖、亲和、适合消费品",
                        font: "衬线细体，优雅精致",
                        description: "米白暖灰基调，温和不刺眼，适合长篇幅阅读",
                      },
                      {
                        name: "科技未来",
                        palette: ["#0A0A0A", "#00D4AA", "#7B61FF", "#FFFFFF"],
                        mood: "前卫、锐利、适合科技产品",
                        font: "等宽科技体，数据突出",
                        description: "深色背景配荧光绿和紫色，营造未来科技感",
                      },
                    ]
                  }
                  onSelect={handleSelectStyle}
                  onRegenerate={handleRegenerateStyleProposals}
                  loading={styleProposalsLoading}
                  disabled={isBusy || chatLoading}
                />
              </div>
            </div>
          ) : (
            <>
              {showStylePanel && (
                <div className="mb-4">
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="text-sm font-medium text-gray-700">选择视觉风格</h3>
                    <button
                      onClick={() => setShowStylePanel(false)}
                      className="text-xs text-gray-400 hover:text-gray-600"
                    >
                      关闭
                    </button>
                  </div>
                  <StyleProposalSelector
                    proposals={
                      selectedProject?.style_proposal?.proposals || [
                        {
                          name: "深海商务",
                          palette: ["#1E3A5F", "#F5F5F0", "#D4A574", "#2C5282"],
                          mood: "冷静、专业、适合金融场景",
                          font: "无衬线，标题加粗",
                          description: "深色背景配金色点缀，营造沉稳专业的商务氛围",
                        },
                        {
                          name: "暖调极简",
                          palette: ["#F7F3EE", "#E8DDD0", "#C4A882", "#8B7355"],
                          mood: "温暖、亲和、适合消费品",
                          font: "衬线细体，优雅精致",
                          description: "米白暖灰基调，温和不刺眼，适合长篇幅阅读",
                        },
                        {
                          name: "科技未来",
                          palette: ["#0A0A0A", "#00D4AA", "#7B61FF", "#FFFFFF"],
                          mood: "前卫、锐利、适合科技产品",
                          font: "等宽科技体，数据突出",
                          description: "深色背景配荧光绿和紫色，营造未来科技感",
                        },
                      ]
                    }
                    onSelect={handleSelectStyle}
                    onRegenerate={handleRegenerateStyleProposals}
                    loading={styleProposalsLoading}
                    disabled={isBusy || chatLoading}
                  />
                </div>
              )}
              {/* 风格已选定，自动生成中 / 已完成 */}
              {selectedProject?.selected_style && (
                currentStatus === "visual_ready" ||
                currentStatus === "prompt_ready" ||
                currentStatus === "generating" ||
                currentStatus === "prototype_ready" ||
                currentStatus === "completed"
              ) && (
                <div className="mb-4 bg-indigo-50 border border-indigo-200 rounded-lg p-4">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-lg">🎨</span>
                    <h3 className="text-sm font-semibold text-indigo-800">
                      风格已选定：{selectedProject.selected_style.name}
                    </h3>
                  </div>
                  {selectedProject.selected_style.palette && (
                    <div className="flex items-center gap-2 mb-2">
                      <div className="flex gap-1">
                        {selectedProject.selected_style.palette.slice(0, 5).map((c: any, i: number) => {
                          const color = typeof c === "string" ? c : c.hex;
                          return (
                            <div
                              key={i}
                              className="w-5 h-5 rounded-full border border-white shadow-sm"
                              style={{ backgroundColor: color }}
                              title={typeof c === "string" ? c : `${c.name} ${c.hex}`}
                            />
                          );
                        })}
                      </div>
                    </div>
                  )}
                  <div className="text-xs text-indigo-700 mb-1">
                    氛围：{selectedProject.selected_style.mood || "—"} · 字体：{selectedProject.selected_style.font || "—"}
                  </div>
                  {selectedProject.selected_style.description && (
                    <p className="text-xs text-indigo-600 line-clamp-2 leading-relaxed">
                      {selectedProject.selected_style.description}
                    </p>
                  )}
                </div>
              )}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3 w-full">
                {slides.map((slide) => {
                const content = slide.content_json || {};
                const text = content.text_content || {};
                const visual = slide.visual_json || {};
                const isSelected = selectedPages.has(slide.page_num);
                return (
                  <div
                    key={slide.id}
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
                      if (!isBusy && !chatLoading) handleEnterEdit(slide);
                    }}
                    className={`group relative bg-white rounded border p-2.5 shadow-sm flex flex-col cursor-pointer hover:shadow-md hover:border-blue-300 transition-all h-[260px] overflow-hidden ${
                      isSelected && (currentStatus === "prompt_ready" || currentStatus === "failed")
                        ? "ring-2 ring-blue-400"
                        : ""
                    } ${dragOverSlideId === slide.id ? "border-dashed border-blue-400 bg-blue-50" : ""} ${dragSlideId === slide.id ? "opacity-50" : ""}`}
                  >
                    {/* 顶部插入触发区：在此页之前插入 */}
                    <div
                      className={`absolute -top-1.5 left-0 right-0 h-3 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity z-10 cursor-pointer ${isBusy || chatLoading ? "pointer-events-none" : ""}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        if (isBusy || chatLoading) return;
                        handleInsertSlideBefore(slide.id);
                      }}
                      title="在此页之前插入"
                    >
                      <div className="w-full h-px bg-blue-400 absolute top-1/2 -translate-y-1/2" />
                      <div className="bg-blue-500 text-white rounded-full w-5 h-5 flex items-center justify-center text-xs shadow-sm relative z-10 hover:bg-blue-600 hover:scale-110 transition-all">+</div>
                    </div>
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
                        <span className="text-xs text-gray-400">P{slide.page_num}</span>
                        {visual.is_seed_recommended ? (
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              handleUnsetSeed(slide.id);
                            }}
                            disabled={isBusy || chatLoading}
                            className="text-xs hover:scale-110 transition-transform disabled:opacity-30"
                            title={`种子页：${visual.seed_family || "未知"}（点击取消）`}
                          >
                            🌱
                          </button>
                        ) : (
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              handleSetSeed(slide.id);
                            }}
                            disabled={isBusy || chatLoading}
                            className="text-xs opacity-30 group-hover:opacity-100 transition-opacity disabled:opacity-10"
                            title="设为种子页（生图参考基准）"
                          >
                            🌱
                          </button>
                        )}
                        <span className="text-sm">{statusIcon[slide.status] || "⏳"}</span>
                      </div>
                      <div className="flex items-center gap-1">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDeleteSlide(slide.id);
                          }}
                          disabled={isBusy || chatLoading}
                          className="text-xs text-gray-400 hover:text-red-500 px-1 leading-none disabled:opacity-30"
                          title="删除"
                        >
                          ×
                        </button>
                        <span className={`text-xs px-1.5 py-0.5 rounded leading-none ${typeColor[slide.type] || "bg-gray-100"}`}>
                          {typeLabel[slide.type] || slide.type}
                        </span>
                      </div>
                    </div>
                    <h3 className="font-bold text-sm mb-0.5 line-clamp-2 leading-tight shrink-0">{text.headline || "无标题"}</h3>
                    {text.subhead && (
                      <p className="text-xs text-gray-500 mb-0.5 line-clamp-1 shrink-0">{text.subhead}</p>
                    )}
                    {text.body && (
                      (typeof text.body === "string" && text.body.trim()) ||
                      (Array.isArray(text.body) && text.body.length > 0)
                    ) && (
                      <div className="text-xs text-gray-600 space-y-0.5 mb-2 flex-1 overflow-hidden">
                        {typeof text.body === "string" ? (
                          <div dangerouslySetInnerHTML={{ __html: renderMarkdown(text.body) }} style={{ whiteSpace: 'pre-wrap' }} />
                        ) : (
                          <ul>
                            {text.body.map((item: any, i: number) => (
                              <li key={i}>
                                · {typeof item === "string" ? item : item?.content || JSON.stringify(item)}
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    )}

                    {/* 视觉意向（简洁标签） */}
                    {visual.visual_summary && (
                      <div className="text-xs text-gray-500 mb-1 bg-gray-50 px-1.5 py-0.5 rounded truncate shrink-0" title={visual.visual_description || visual.visual_summary}>
                        🎨 {visual.visual_summary}
                      </div>
                    )}

                    {/* Slide 图片预览 */}
                    {slide.image_path && (
                      <div
                        className="shrink-0 h-14 w-full rounded overflow-hidden cursor-pointer mb-1"
                        onClick={(e) => {
                          e.stopPropagation();
                          const allUrls = slides
                            .filter((s) => s.status === "completed" && s.image_path)
                            .sort((a, b) => a.page_num - b.page_num)
                            .map((s) => getSlideImageUrl(s.image_path!, s.status));
                          const url = getSlideImageUrl(slide.image_path!, slide.status);
                          const index = allUrls.indexOf(url);
                          setGalleryModal({ urls: allUrls, index: index >= 0 ? index : 0, title: "PPT 预览" });
                        }}
                      >
                        <img
                          src={getSlideImageUrl(slide.image_path, slide.status)}
                          alt={`Slide ${slide.page_num}`}
                          className="w-full h-full object-cover"
                          onError={(e) => {
                            const el = e.target as HTMLImageElement;
                            el.style.display = "none";
                            el.parentElement!.innerHTML = '<div class="w-full h-full flex items-center justify-center text-xs text-gray-400 bg-gray-100">图片加载失败</div>';
                          }}
                        />
                      </div>
                    )}

                    {/* 页面级参考图 */}
                    <div className="flex items-center gap-1.5 mb-1 shrink-0">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleUploadPageRef(slide.id);
                        }}
                        disabled={isBusy || chatLoading}
                        className="text-xs bg-gray-100 text-gray-600 px-2 py-1 rounded hover:bg-gray-200 disabled:opacity-50 leading-none"
                      >
                        + 参考图
                      </button>
                      {slide.reference_images && slide.reference_images.length > 0 && (
                        <div className="flex gap-1 flex-nowrap overflow-x-auto">
                          {slide.reference_images.map((ref: any) => (
                            <div key={ref.id} className="relative group">
                              <img
                                src={`${API_BASE}${ref.url}`}
                                alt="ref"
                                className="w-10 h-10 rounded object-cover border cursor-pointer"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  const allRefUrls = slides
                                    .flatMap((s) => s.reference_images?.map((r: any) => `${API_BASE}${r.url}`) || [])
                                    .filter((v, i, a) => a.indexOf(v) === i);
                                  const url = `${API_BASE}${ref.url}`;
                                  const index = allRefUrls.indexOf(url);
                                  setGalleryModal({ urls: allRefUrls, index: index >= 0 ? index : 0, title: "参考图片" });
                                }}
                                onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                              />
                              {/* 彩色模式标签 */}
                              <span className={`absolute bottom-0 right-0 text-[8px] text-white px-1 rounded-tl ${
                                ref.process_mode === "blend" ? "bg-blue-500" : ref.process_mode === "crop" ? "bg-orange-500" : "bg-green-600"
                              }`}>
                                {ref.process_mode === "blend" ? "融合" : ref.process_mode === "crop" ? "裁剪" : "原图"}
                              </span>
                              {/* hover 删除按钮 */}
                              <button
                                onClick={async (e) => {
                                  e.stopPropagation();
                                  if (!selectedProject) return;
                                  try {
                                    await deleteReferenceImage(selectedProject.id, ref.id);
                                    markSlideStale(slide.id, "content");
                                    showToast("已删除");
                                    await loadSlides(selectedProject.id);
                                    addSystemLog(`用户删除了第 ${slide.page_num} 页的参考图`);
                                  } catch (err: any) {
                                    showToast("删除失败：" + (err.message || "未知错误"), "error");
                                  }
                                }}
                                className="absolute -top-1 -right-1 w-4 h-4 bg-red-500 text-white text-[10px] rounded-full items-center justify-center hidden group-hover:flex shadow-sm"
                                title="删除"
                              >
                                ×
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
                        className="mt-1 text-xs bg-red-50 text-red-600 px-2 py-1 rounded hover:bg-red-100 self-start disabled:opacity-50 leading-none shrink-0"
                      >
                        {isBusy ? "重试中..." : "重试"}
                      </button>
                    )}

                    {/* 底部插入触发区：在此页之后插入 */}
                    <div
                      className={`absolute -bottom-1.5 left-0 right-0 h-3 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity z-10 cursor-pointer ${isBusy || chatLoading ? "pointer-events-none" : ""}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        if (isBusy || chatLoading) return;
                        handleInsertSlideAfter(slide.id);
                      }}
                      title="在此页之后插入"
                    >
                      <div className="w-full h-px bg-blue-400 absolute top-1/2 -translate-y-1/2" />
                      <div className="bg-blue-500 text-white rounded-full w-5 h-5 flex items-center justify-center text-xs shadow-sm relative z-10 hover:bg-blue-600 hover:scale-110 transition-all">+</div>
                    </div>
                  </div>
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
        <div className="p-3 border-b font-medium flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span>Agent 助手</span>
            {currentAgentRole === "visual" && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-100 text-purple-700">视觉总监</span>
            )}
            {currentAgentRole === "content" && slides.length > 0 && currentStatus === "planning" && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-100 text-blue-700">内容总监</span>
            )}
          </div>
          <div className="flex items-center gap-1">
            {currentAgentRole === "visual" && (
              <button
                onClick={handleSwitchToContentDirector}
                className="text-[10px] text-gray-500 hover:text-blue-600 px-1"
                title="回到内容总监"
              >
                ← 内容总监
              </button>
            )}
            {currentAgentRole === "content" && contentPlanConfirmed && (
              <button
                onClick={() => setCurrentAgentRole("visual")}
                className="text-[10px] text-gray-500 hover:text-purple-600 px-1"
                title="回到视觉总监"
              >
                → 视觉总监
              </button>
            )}
            <button
              onClick={() => setRightCollapsed(true)}
              className="text-gray-400 hover:text-gray-600 text-xs px-1"
              title="收起"
            >
              ▶
            </button>
          </div>
        </div>
        {/* Agent 模式切换栏：内容规划阶段和视觉总监阶段都显示 */}
        {selectedProject && slides.length > 0 && (currentStatus === "planning" || currentAgentRole === "visual") && (
          <div className="px-3 py-2 border-b bg-gray-50 flex items-center justify-between">
            <div className="flex items-center gap-1 text-xs">
              <span className="text-gray-500">调整范围：</span>
              <button
                onClick={() => setAgentMode("page")}
                className={`px-2 py-0.5 rounded transition-colors ${
                  agentMode === "page"
                    ? "bg-blue-600 text-white"
                    : "bg-white text-gray-600 hover:bg-gray-100 border border-gray-200"
                }`}
                title={currentAgentRole === "visual" ? "只修改当前正在编辑的那一页的视觉描述" : "只修改当前正在编辑的那一页"}
              >
                📝 当前页
              </button>
              <button
                onClick={() => setAgentMode("global")}
                className={`px-2 py-0.5 rounded transition-colors ${
                  agentMode === "global"
                    ? "bg-blue-600 text-white"
                    : "bg-white text-gray-600 hover:bg-gray-100 border border-gray-200"
                }`}
                title={currentAgentRole === "visual" ? "调整所有页面的视觉描述" : "调整所有页面的文字内容"}
              >
                🌐 全局
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
                  ↩ 撤销
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
                  ↪ 重做
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
                <span>📄 已上传 {documents.length} 个文档</span>
                <span className="ml-auto text-[10px] text-gray-400">
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
                        ×
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {selectedProject && currentStatus === "draft" && (
            <div className="bg-blue-50 rounded space-y-3 p-3 text-sm">
              <div className="font-medium truncate max-w-[300px]" title={selectedProject.title}>👋 欢迎来到 {selectedProject.title === "未命名项目" ? "你的新项目" : selectedProject.title}</div>
              <div>这是一个全新的项目。请告诉我你想做什么主题的 PPT？</div>
              <div className="text-blue-600 text-xs">
                支持直接输入主题、粘贴内容，或拖拽上传 PDF / Word / PPT / Markdown 等文档。
              </div>
              {/* Quick action cards */}
              <div className="grid grid-cols-2 gap-2 mt-2">
                {[
                  { emoji: "📊", label: "销售汇报", prompt: "我要做一份销售汇报PPT，面向公司管理层，总结上季度业绩、关键数据亮点和下一步计划。" },
                  { emoji: "🎓", label: "教学课件", prompt: "我要做一份教学课件，面向大学生，介绍人工智能的基础概念和应用场景。" },
                  { emoji: "💡", label: "产品发布", prompt: "我要做一份产品发布PPT，面向潜在客户，展示产品核心功能、竞争优势和定价策略。" },
                  { emoji: "🎨", label: "个人作品集", prompt: "我要做一份个人作品集PPT，展示我的设计案例、项目经历和职业亮点。" },
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
                    <span>{item.emoji}</span>
                    <span>{item.label}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
          {isDragging && (
            <div className="flex items-center justify-center h-32 border-2 border-dashed border-blue-400 rounded-lg bg-blue-50 text-blue-600 text-sm">
              📎 松开即可上传文档
            </div>
          )}
          {/* 生成进度：仅在 generating 阶段显示 */}
          {selectedProject && currentStatus === "generating" && projectStatus && (
            <div className="bg-orange-50 p-3 rounded text-sm text-orange-800">
              <div className="font-medium mb-1">生成进度</div>
              <div className="w-full bg-orange-200 rounded-full h-2 mb-2">
                <div
                  className="bg-orange-500 h-2 rounded-full transition-all"
                  style={{
                    width: `${(projectStatus.completed_slides / (projectStatus.target_count || projectStatus.total_slides || 1)) * 100}%`,
                  }}
                />
              </div>
              <div>
                {projectStatus.completed_slides} / {projectStatus.target_count || projectStatus.total_slides} 页完成
              </div>
            </div>
          )}

          {/* 聊天消息 */}
          {chatMessages.map((msg, i) => (
            <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
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
                      <div className="text-[10px] text-purple-600 mb-1 font-medium">视觉总监</div>
                    )}
                    {msg.role === "agent" && msg.agentRole === "content" && slides.length > 0 && currentStatus === "planning" && (
                      <div className="text-[10px] text-blue-600 mb-1 font-medium">内容总监</div>
                    )}
                    <div
                      className={`p-3 rounded text-sm ${
                        msg.role === "user"
                          ? "bg-blue-600 text-white rounded-br-none"
                          : msg.role === "system"
                          ? "bg-gray-50 text-gray-500 rounded-bl-none text-xs border border-gray-200"
                          : msg.agentRole === "visual"
                          ? "bg-purple-50 text-gray-800 rounded-bl-none markdown-body border-l-2 border-purple-300"
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
                          <span className="shrink-0">⚙️</span>
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
                              {attachments.length > 0 && (
                                <div className="flex flex-wrap gap-1.5 mt-2 pt-2 border-t border-white/20">
                                  {attachments.map((att, idx) => (
                                    <span
                                      key={idx}
                                      className="inline-flex items-center gap-1 text-[10px] bg-white/20 text-white px-1.5 py-0.5 rounded"
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
                          <div className="text-sm font-semibold text-gray-800 mb-2">📋 内容定调</div>
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
                    </div>
                    {/* 视觉总监的风格提案已移至主舞台展示 */}
                    {/* 消息操作按钮 */}
                    <div className={`flex gap-1 mt-1 opacity-0 group-hover:opacity-100 transition-opacity ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                      {msg.role === "user" && (
                        <button
                          onClick={() => handleEditMessage(i)}
                          disabled={chatLoading || isBusy}
                          className="text-[10px] text-gray-400 hover:text-blue-600 px-1 disabled:opacity-30 disabled:cursor-not-allowed"
                          title="编辑"
                        >
                          编辑
                        </button>
                      )}
                      <button
                        onClick={() => handleDeleteMessage(i)}
                        disabled={chatLoading || isBusy}
                        className="text-[10px] text-gray-400 hover:text-red-600 px-1 disabled:opacity-30 disabled:cursor-not-allowed"
                        title="删除（回滚到此消息之前）"
                      >
                        删除
                      </button>
                    </div>
                  </>
                )}
              </div>
            </div>
          ))}
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
                      <span className="ml-auto text-[10px] text-gray-400">
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
          {/* 内容规划动态进度卡片 */}
          {contentPlanProgress && contentPlanProgress.stage && contentPlanProgress.stage !== "error" && (
            <div className="flex justify-start">
              <div className="bg-blue-50 border border-blue-200 rounded-lg text-sm text-gray-700 rounded-bl-none max-w-[80%] overflow-hidden w-72">
                <div className="px-3 py-2.5 flex items-center gap-2">
                  <svg className="animate-spin h-4 w-4 text-blue-500 flex-shrink-0" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                  <div className="flex-1 min-w-0">
                    <div className="font-medium text-blue-800 text-xs truncate">
                      {contentPlanProgress.message || "生成中..."}
                    </div>
                    {contentPlanProgress.total_pages > 0 && (
                      <div className="mt-1.5">
                        <div className="flex items-center justify-between text-[10px] text-blue-600 mb-0.5">
                          <span>进度</span>
                          <span>{contentPlanProgress.current_page || 0} / {contentPlanProgress.total_pages} 页</span>
                        </div>
                        <div className="h-1.5 bg-blue-100 rounded-full overflow-hidden">
                          <div
                            className="h-full bg-blue-500 rounded-full transition-all duration-500 ease-out"
                            style={{
                              width: `${Math.min(100, ((contentPlanProgress.current_page || 0) / contentPlanProgress.total_pages) * 100)}%`
                            }}
                          />
                        </div>
                      </div>
                    )}
                  </div>
                </div>
                {contentPlanProgress.think && (
                  <div className="px-3 py-2 bg-white/60 text-xs text-gray-500 whitespace-pre-wrap leading-relaxed max-h-32 overflow-auto border-t border-blue-100">
                    {contentPlanProgress.think}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
        <div className="border-t p-4">
          {/* 内容规划确认条：常驻在输入框上方 */}
          {selectedProject && slides.length > 0 && currentStatus === "planning" && !contentPlanConfirmed && (
            <div className="mb-3 bg-emerald-50 border border-emerald-200 rounded-lg p-3">
              <div className="flex items-center justify-between mb-2">
                <div className="text-sm text-emerald-800">
                  <span className="font-medium">✅ 内容规划已完成</span>
                  <span className="text-emerald-600 ml-1">· {slides.length} 页</span>
                </div>
              </div>
              <button
                onClick={handleConfirmContentPlan}
                disabled={confirmingPlan || isBusy || chatLoading}
                className="w-full bg-emerald-600 text-white text-sm py-2 rounded hover:bg-emerald-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {confirmingPlan ? (
                  <span className="flex items-center justify-center gap-2">
                    <svg className="animate-spin h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                    </svg>
                    正在请视觉总监介入...
                  </span>
                ) : (
                  "确认内容，请视觉总监 →"
                )}
              </button>
              <div className="text-center mt-1.5">
                <span className="text-[10px] text-emerald-500">
                  你可以继续调整内容，满意后再点击确认
                </span>
              </div>
            </div>
          )}
          {/* 视觉总监素材收集提示：已确认内容但还没选定风格 */}
          {selectedProject && contentPlanConfirmed && currentAgentRole === "visual" && !selectedProject?.selected_style && currentStatus === "planning" && (
            <div className="mb-3 bg-purple-50 border border-purple-200 rounded-lg p-3">
              <div className="text-sm text-purple-800 mb-2">
                <span className="font-medium">🎨 视觉总监已介入</span>
                <span className="text-purple-600 ml-1">· 提供设计素材可以让提案更精准</span>
              </div>
              <div className="flex flex-wrap gap-2 mb-2">
                <input
                  type="file"
                  id="style-ref-input"
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
                        addSystemLog(`用户上传了风格参考图「${file.name}」`);
                        if (currentAgentRole === "visual") {
                          setChatMessages((prev) => [
                            ...prev,
                            { role: "user", content: `📎 已上传风格参考图：${file.name}`, agentRole: "visual" },
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
                  id="logo-input"
                  className="hidden"
                  accept="image/*"
                  onChange={async (e) => {
                    const file = e.target.files?.[0];
                    if (file && selectedProject) {
                      setUploadingLogo(true);
                      try {
                        await uploadFile(selectedProject.id, file, "logo");
                        showToast("Logo 已添加");
                        await loadReferenceImages(selectedProject.id);
                        await loadProjects();
                        slides.forEach((s) => markSlideStale(s.id, "content"));
                        addSystemLog(`用户上传了 Logo「${file.name}」`);
                        if (currentAgentRole === "visual") {
                          setChatMessages((prev) => [
                            ...prev,
                            { role: "user", content: `🎯 已上传 Logo：${file.name}`, agentRole: "visual" },
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
                  id="template-input"
                  className="hidden"
                  accept=".ppt,.pptx,.pdf"
                  onChange={async (e) => {
                    const file = e.target.files?.[0];
                    if (file && selectedProject) {
                      setUploadingTemplate(true);
                      try {
                        await extractTemplate(selectedProject.id, file);
                        showToast("模板已上传并提取");
                        await loadReferenceImages(selectedProject.id);
                        await loadTemplatePages(selectedProject.id);
                        await loadProjects();
                        slides.forEach((s) => markSlideStale(s.id, "content"));
                        addSystemLog(`用户上传了参考模板「${file.name}」`);
                        if (currentAgentRole === "visual") {
                          setChatMessages((prev) => [
                            ...prev,
                            { role: "user", content: `📑 已上传参考模板：${file.name}`, agentRole: "visual" },
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
                <button
                  onClick={() => document.getElementById("style-ref-input")?.click()}
                  disabled={uploadingStyleRef || isBusy || chatLoading}
                  className="text-xs bg-white text-purple-700 px-2 py-1 rounded border border-purple-200 hover:bg-purple-50 disabled:opacity-50"
                >
                  {uploadingStyleRef ? "上传中..." : "📎 风格参考"}
                </button>
                <button
                  onClick={() => document.getElementById("logo-input")?.click()}
                  disabled={uploadingLogo || isBusy || chatLoading}
                  className="text-xs bg-white text-purple-700 px-2 py-1 rounded border border-purple-200 hover:bg-purple-50 disabled:opacity-50"
                >
                  {uploadingLogo ? "上传中..." : "🎯 Logo"}
                </button>
                <button
                  onClick={() => document.getElementById("template-input")?.click()}
                  disabled={uploadingTemplate || isBusy || chatLoading}
                  className="text-xs bg-white text-purple-700 px-2 py-1 rounded border border-purple-200 hover:bg-purple-50 disabled:opacity-50"
                >
                  {uploadingTemplate ? "上传中..." : "📑 模板"}
                </button>
              </div>
              <button
                onClick={async () => {
                  if (!selectedProject) return;
                  setStyleProposalsLoading(true);
                  setChatMessages((prev) => [
                    ...prev,
                    {
                      role: "agent" as const,
                      content: `⏩ 用户确认${referenceImages.length > 0 ? "素材已齐" : "没有素材"}，开始生成风格提案...`,
                      agentRole: "visual",
                    },
                  ]);
                  try {
                    // 直接调用后端 API 生成提案（比 Agent 聊天更稳定）
                    const styleResult = await generateStyleProposals(selectedProject.id);
                    if (styleResult.status === "generating") {
                      showToast("风格提案后台生成中，请稍候...", "info");
                      await pollForStyleProposals(selectedProject.id);
                    }
                    await loadProjects();
                    const fresh = await fetchProjects();
                    const updated = fresh.find((p: Project) => p.id === selectedProject.id);
                    if (updated) setSelectedProject(updated);
                    // 让 Agent 生成配套的文字描述，保持对话上下文
                    setTimeout(() => handleSendChat("请基于已生成的风格提案，给用户一个简短的风格介绍和下一步指引"), 0);
                  } catch (err: any) {
                    showToast("生成失败：" + (err.message || "未知错误"), "error");
                  } finally {
                    setStyleProposalsLoading(false);
                  }
                }}
                disabled={styleProposalsLoading || isBusy || chatLoading}
                className="w-full bg-purple-600 text-white text-sm py-2 rounded hover:bg-purple-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {styleProposalsLoading || isBusy || chatLoading
                  ? "⏳ 正在生成风格提案..."
                  : referenceImages.length > 0
                    ? "✅ 确认素材已齐，生成风格提案"
                    : "✅ 没有素材，直接开始提案"}
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
                  {uploadingDoc ? "解析中..." : "📎 上传文档"}
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
                  <span>📎 {filename}</span>
                  <button
                    onClick={() => setPendingAttachments((prev) => prev.filter((f) => f !== filename))}
                    className="text-blue-400 hover:text-blue-900 ml-1"
                    title="移除"
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
          <div className="flex gap-2">
            <textarea
              ref={chatInputRef}
              className="flex-1 border rounded resize-none px-3 py-2 text-sm"
              style={{ minHeight: 36, overflowY: "hidden" }}
              placeholder={currentStatus === "draft" ? "输入 PPT 主题或粘贴文档内容..." : "输入指令..."}
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              onInput={autoResizeTextarea}
              onKeyDown={(e) => {
                if (e.key !== "Enter") return;
                // 输入法组字中，按 Enter 只是上屏，不发送
                if ((e as any).nativeEvent?.isComposing) return;
                // Shift + Enter 换行，不发送
                if (e.shiftKey) return;
                e.preventDefault();
                handleSendChat();
              }}
              disabled={!selectedProject || chatLoading}
            />
            {chatLoading ? (
              <button
                onClick={handleStopChat}
                className="bg-gray-800 text-white rounded hover:bg-gray-900 px-3 py-2 text-sm"
              >
                停止
              </button>
            ) : (
              <button
                onClick={() => handleSendChat()}
                disabled={!selectedProject || (!chatInput.trim() && pendingAttachments.length === 0)}
                className="bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 px-3 py-2 text-sm"
              >
                发送
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
        title={hex}
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
  unescapeText,
  onImageClick,
  onToast,
  markSlideStale,
  staleStatus,
  projectStatus,
  onUpdateStale,
  onGenerateImages,
  onSystemLog,
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
  unescapeText: (text: string) => string;
  onImageClick?: (url: string) => void;
  onToast?: (message: string, type: ToastItem["type"]) => void;
  markSlideStale?: (slideId: string, type: "content" | "visual" | "image") => void;
  staleStatus?: { content?: boolean; visual?: boolean; image?: boolean };
  projectStatus?: string;
  onUpdateStale?: () => void;
  onGenerateImages?: () => void;
  onSystemLog?: (content: string) => void;
}) {
  const content = slide.content_json || {};
  const text = content.text_content || {};
  const [headline, setHeadline] = useState(unescapeText(text.headline || ""));
  const [subhead, setSubhead] = useState(unescapeText(text.subhead || ""));
  // body 兼容旧数据（string[]）和新数据（string）
  const normalizeBody = (raw: any): string => {
    if (typeof raw === "string") return unescapeText(raw);
    if (Array.isArray(raw)) return raw.map((item: any) =>
      typeof item === "string" ? item : item?.content || ""
    ).join("\n\n");
    return "";
  };
  const [body, setBody] = useState<string>(normalizeBody(text.body));
  const [bodyEmpty, setBodyEmpty] = useState(!body || body.trim() === "");
  const bodyEditorRef = useRef<HTMLDivElement>(null);
  const turndownRef = useRef(new TurndownService({ headingStyle: "atx", bulletListMarker: "-", codeBlockStyle: "fenced" }));
  const [speakerNotes, setSpeakerNotes] = useState(unescapeText(content.speaker_notes || ""));

  // 视觉方案编辑状态
  const [visualDescription, setVisualDescription] = useState(slide.visual_json?.visual_description || "");
  const [editingVisual, setEditingVisual] = useState(false);
  const [promptExpanded, setPromptExpanded] = useState(false);
  const visualTextareaRef = useRef<HTMLTextAreaElement>(null);

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
      currentBody = turndownRef.current.turndown(bodyEl.innerHTML);
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
  const [pageActionLoading, setPageActionLoading] = useState<"plan" | "image" | null>(null);
  const [rerollingPlan, setRerollingPlan] = useState(false);

  // 保存视觉方案画面描述
  const handleSaveVisual = async () => {
    if (!slide.visual_json) return;
    try {
      await updateVisualPlan(projectId, slide.page_num, {
        ...slide.visual_json,
        visual_description: visualDescription,
      }, slide.id);
      markSlideStale?.(slide.id, "visual");
      onSaved?.();
      onToast?.("画面描述已保存，请点击「更新画面方案」应用修改", "success");
      onSystemLog?.(`用户编辑了第 ${slide.page_num} 页（类型：${slide.type}）的画面描述`);
    } catch (err: any) {
      onToast?.("保存画面描述失败：" + (err.message || "未知错误"), "error");
    }
  };

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
      currentBody = turndownRef.current.turndown(bodyEl.innerHTML);
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
    const html = (marked.parse(body || "", { async: false }) as string) || "<p><br></p>";
    const fixedHtml = fixMarkedBoldHtml(html);
    if (el.innerHTML !== fixedHtml) {
      el.innerHTML = fixedHtml;
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
    const md = turndownRef.current.turndown(html);
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
      {/* 顶部导航 */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <button
            onClick={handleSaveAndExit}
            disabled={saving}
            className={`text-sm flex items-center gap-1 ${
              saving
                ? "text-gray-400 cursor-not-allowed"
                : "text-gray-600 hover:text-gray-900"
            }`}
          >
            <span>←</span>
            <span>{saving ? "保存中..." : "保存并返回"}</span>
          </button>
          <button
            onClick={async () => { await handleSave(); }}
            disabled={saving}
            className={`text-sm px-2 py-1 rounded border ${
              saving
                ? "text-gray-400 border-gray-200 cursor-not-allowed"
                : "text-blue-600 border-blue-200 hover:bg-blue-50"
            }`}
            title="保存 (Ctrl+S)"
          >
            {saving ? "保存中..." : "保存"}
          </button>
          {onPrev && (
            <button
              onClick={async () => { const ok = await handleSave(); if (ok) onPrev?.(); }}
              disabled={!hasPrev || saving}
              className={`text-sm flex items-center gap-1 px-2 py-1 rounded transition-colors ${
                hasPrev && !saving
                  ? "text-gray-600 hover:bg-gray-100 hover:text-gray-900"
                  : "text-gray-300 cursor-not-allowed"
              }`}
            >
              <span>↑</span>
              <span>上一页</span>
            </button>
          )}
        </div>
        <div className="flex items-center gap-2">
          {/* 撤销 / 重做 */}
          <button
            onClick={handleUndo}
            disabled={!canUndo}
            title="撤销 (Ctrl+Z)"
            className={`text-sm px-2 py-1 rounded transition-colors ${
              canUndo
                ? "text-gray-600 hover:bg-gray-100 hover:text-gray-900"
                : "text-gray-300 cursor-not-allowed"
            }`}
          >
            ↩
          </button>
          <button
            onClick={handleRedo}
            disabled={!canRedo}
            title="重做 (Ctrl+Shift+Z)"
            className={`text-sm px-2 py-1 rounded transition-colors ${
              canRedo
                ? "text-gray-600 hover:bg-gray-100 hover:text-gray-900"
                : "text-gray-300 cursor-not-allowed"
            }`}
          >
            ↪
          </button>
          <span className="text-xs text-gray-400">P{slide.page_num}</span>
          <span
            className={`text-xs px-2 py-0.5 rounded leading-none ${
              typeColor[slide.type] || "bg-gray-100"
            }`}
          >
            {typeLabel[slide.type] || slide.type}
          </span>
          {onNext && (
            <button
              onClick={async () => { const ok = await handleSave(); if (ok) onNext?.(); }}
              disabled={!hasNext || saving}
              className={`text-sm flex items-center gap-1 px-2 py-1 rounded transition-colors ${
                hasNext && !saving
                  ? "text-gray-600 hover:bg-gray-100 hover:text-gray-900"
                  : "text-gray-300 cursor-not-allowed"
              }`}
            >
              <span>下一页</span>
              <span>↓</span>
            </button>
          )}
          {onInsertBefore && (
            <button
              onClick={onInsertBefore}
              className="text-sm text-gray-400 hover:text-green-600 px-2 py-1 rounded hover:bg-green-50 transition-colors"
              title="在前插入新页"
            >
              ↑ 插入
            </button>
          )}
          {onInsertAfter && (
            <button
              onClick={onInsertAfter}
              className="text-sm text-gray-400 hover:text-green-600 px-2 py-1 rounded hover:bg-green-50 transition-colors"
              title="在后插入新页"
            >
              ↓ 插入
            </button>
          )}
          {onDelete && (
            <button
              onClick={onDelete}
              className="text-sm text-gray-400 hover:text-red-500 px-2 py-1 rounded hover:bg-red-50 transition-colors"
              title="删除此页"
            >
              删除
            </button>
          )}
        </div>
      </div>

      {/* Stale 状态横幅 */}
      {(showContentStale || showVisualStale || showImageStale) && (
        <div className="mb-4 bg-amber-50 border border-amber-200 rounded p-3">
          <div className="flex items-center justify-between">
            <div className="flex flex-wrap gap-2 text-xs">
              {showContentStale && (
                <span className="px-2 py-0.5 bg-blue-100 text-blue-700 rounded">需更新画面方案</span>
              )}
              {showVisualStale && (
                <span className="px-2 py-0.5 bg-emerald-100 text-emerald-700 rounded">需更新画面方案</span>
              )}
              {showImageStale && (
                <span className="px-2 py-0.5 bg-purple-100 text-purple-700 rounded">需重新生成图片</span>
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
                {pageActionLoading === "plan" ? "更新中..." : "更新画面方案"}
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

      {/* 参考图片 */}
      <div className="mb-6">
        <div className="flex items-center gap-1.5 mb-2">
          <label className="text-xs text-gray-500 font-medium">🖼️ 参考图片</label>
          <div className="relative group">
            <span className="text-xs text-gray-400 cursor-help">ⓘ</span>
            <div className="absolute left-1/2 -translate-x-1/2 bottom-full mb-1 hidden group-hover:block w-64 bg-gray-800 text-white text-[11px] rounded-lg px-3 py-2 shadow-lg z-50">
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
                      onSystemLog?.(`用户删除了第 ${slide.page_num} 页的参考图`);
                    } catch (err: any) {
                      onToast?.("删除失败：" + (err.message || "未知错误"), "error");
                    }
                  }}
                  className="absolute -top-1.5 -right-1.5 w-5 h-5 bg-red-500 text-white text-[10px] rounded-full items-center justify-center hidden group-hover:flex shadow-sm z-10"
                  title="删除"
                >
                  ×
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
                        onSystemLog?.(`用户将第 ${slide.page_num} 页参考图切换为${m.label}模式`);
                      } catch (err: any) {
                        onToast?.("更新失败：" + (err.message || "未知错误"), "error");
                      }
                    }}
                    className={`text-[10px] px-1.5 py-0.5 rounded leading-none ${ref.process_mode === m.key ? `${m.color} text-white` : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`}
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
                onSystemLog?.(`用户为第 ${slide.page_num} 页上传了参考图（融合模式）`);
              } catch (err: any) {
                onToast?.("上传失败：" + (err.message || "未知错误"), "error");
              }
            };
            input.click();
          }}
          className="text-xs bg-gray-100 text-gray-600 px-2 py-1 rounded hover:bg-gray-200"
        >
          + 上传参考图
        </button>
      </div>

      {/* 视觉方案画面描述 */}
      <div className="mb-6">
        <label className="text-xs text-gray-500 mb-1 block font-medium">🎨 视觉方案（画面描述）</label>
        {editingVisual ? (
          <textarea
            ref={visualTextareaRef}
            value={visualDescription}
            onChange={(e) => setVisualDescription(e.target.value)}
            onBlur={() => {
              setEditingVisual(false);
              handleSaveVisual();
            }}
            className="w-full text-sm border border-emerald-200 rounded p-3 focus:outline-none focus:ring-2 focus:ring-emerald-300 focus:border-transparent resize-y min-h-[100px] bg-emerald-50"
            placeholder="描述画面视觉风格、色彩、元素..."
            autoFocus
          />
        ) : (
          <div
            onClick={() => {
              setEditingVisual(true);
              setTimeout(() => visualTextareaRef.current?.focus(), 0);
            }}
            className="bg-emerald-50 border border-emerald-100 rounded p-3 cursor-text hover:border-emerald-300 transition-colors"
          >
            {visualDescription ? (
              <p className="text-sm text-gray-700 leading-relaxed">{renderDescriptionWithColors(visualDescription)}</p>
            ) : (
              <span className="text-sm text-gray-400">点击编辑画面描述...</span>
            )}
            {slide.visual_json?.layout && (
              <div className="text-xs text-gray-400 mt-1">布局: {slide.visual_json.layout}</div>
            )}
          </div>
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
            📝 生图指令（只读）
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
          <div
            className="aspect-video rounded overflow-hidden cursor-pointer border border-gray-200"
            onClick={() => {
              const url = getSlideImageUrl(slide.image_path!, slide.status);
              onImageClick?.(url);
            }}
          >
            <img
              src={getSlideImageUrl(slide.image_path, slide.status)}
              alt={`Slide ${slide.page_num}`}
              className="w-full h-full object-cover"
              onError={(e) => {
                const el = e.target as HTMLImageElement;
                el.style.display = "none";
                el.parentElement!.innerHTML = '<div class="w-full h-full flex items-center justify-center text-xs text-gray-400 bg-gray-100">图片加载失败</div>';
              }}
            />
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
