const configuredApiBase = import.meta.env.VITE_API_BASE_URL;
const shouldUseSameOrigin =
  import.meta.env.PROD &&
  (!configuredApiBase ||
    configuredApiBase.includes("localhost") ||
    configuredApiBase.includes("127.0.0.1"));
export const API_BASE =
  shouldUseSameOrigin
    ? ""
    : configuredApiBase !== undefined
    ? configuredApiBase
    : import.meta.env.DEV
    ? "http://localhost:8000"
    : "";
function makeApiUrl(path: string): URL {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const rawUrl = `${API_BASE}${normalizedPath}`;
  return new URL(rawUrl, window.location.origin);
}

const AUTH_STORAGE_KEY = "pptgod.mvpAuth";
const PROVIDER_STORAGE_KEY = "pptgod.providerSettings";
const AGENT_CONTEXT_STORAGE_PREFIX = "pptgod.agentContext.";
export const CAPABILITY_REQUIRED_EVENT = "pptgod:capability-required";

export interface MvpAuth {
  testerId: string;
  displayName: string;
}

export interface AgentCapabilityContext {
  projectId: string;
  agentName: string;
  textGeneration: boolean;
  imageGeneration: boolean;
}

export function getStoredAgentContext(projectId?: string | null): AgentCapabilityContext | null {
  if (!projectId) return null;
  try {
    const raw = sessionStorage.getItem(`${AGENT_CONTEXT_STORAGE_PREFIX}${projectId}`);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return {
      projectId,
      agentName: String(parsed.agentName || "外部 Agent"),
      textGeneration: Boolean(parsed.textGeneration),
      imageGeneration: Boolean(parsed.imageGeneration),
    };
  } catch {
    return null;
  }
}

export function saveAgentContext(context: AgentCapabilityContext) {
  try {
    sessionStorage.setItem(
      `${AGENT_CONTEXT_STORAGE_PREFIX}${context.projectId}`,
      JSON.stringify(context)
    );
  } catch {
    // Agent context is a session convenience; project artifacts remain durable.
  }
}

export interface ProviderSettings {
  textApiKey: string;
  textApiBase: string;
  textModel: string;
  imageApiKey: string;
  imageApiBase: string;
  imageModel: string;
}

export const DEFAULT_PROVIDER_SETTINGS: ProviderSettings = {
  textApiKey: "",
  textApiBase: "https://api.cometapi.com/v1",
  textModel: "MiniMax-M3",
  imageApiKey: "",
  imageApiBase: "https://api.cometapi.com/v1",
  imageModel: "gpt-image-2",
};

function clearLegacyStoredAuth() {
  try {
    localStorage.removeItem(AUTH_STORAGE_KEY);
  } catch {
    // Ignore storage access failures; the login gate will ask for the username again.
  }
}

function getAuthSessionStorage(): Storage | null {
  try {
    return sessionStorage;
  } catch {
    return null;
  }
}

export function getStoredAuth(): MvpAuth | null {
  clearLegacyStoredAuth();
  try {
    const raw = getAuthSessionStorage()?.getItem(AUTH_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed?.testerId) return null;
    return { testerId: parsed.testerId, displayName: parsed.displayName || "测试用户" };
  } catch {
    return null;
  }
}

export function saveStoredAuth(auth: MvpAuth) {
  clearLegacyStoredAuth();
  getAuthSessionStorage()?.setItem(AUTH_STORAGE_KEY, JSON.stringify(auth));
}

export function clearStoredAuth() {
  getAuthSessionStorage()?.removeItem(AUTH_STORAGE_KEY);
  clearLegacyStoredAuth();
}

export function getProviderSettings(): ProviderSettings {
  try {
    const raw = localStorage.getItem(PROVIDER_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    const next: ProviderSettings = {
      textApiKey: parsed.textApiKey ?? parsed.minimaxApiKey ?? "",
      textApiBase: parsed.textApiBase ?? parsed.minimaxApiBase ?? DEFAULT_PROVIDER_SETTINGS.textApiBase,
      textModel: parsed.textModel ?? parsed.minimaxLlmModel ?? DEFAULT_PROVIDER_SETTINGS.textModel,
      imageApiKey: parsed.imageApiKey ?? parsed.cometApiKey ?? parsed.deerApiKey ?? "",
      imageApiBase: parsed.imageApiBase ?? parsed.cometApiBase ?? parsed.deerApiBase ?? DEFAULT_PROVIDER_SETTINGS.imageApiBase,
      imageModel: parsed.imageModel ?? parsed.cometImageModel ?? parsed.deerImageModel ?? DEFAULT_PROVIDER_SETTINGS.imageModel,
    };
    if (next.imageApiBase.includes("api.deepapi.com")) next.imageApiBase = DEFAULT_PROVIDER_SETTINGS.imageApiBase;
    if (next.imageModel === "GPT-Image-V4" || next.imageModel === "gpt-image-2-all") next.imageModel = DEFAULT_PROVIDER_SETTINGS.imageModel;
    if (next.textModel === "MiniMax-M2.7") next.textModel = DEFAULT_PROVIDER_SETTINGS.textModel;
    return next;
  } catch {
    return { ...DEFAULT_PROVIDER_SETTINGS };
  }
}

export function saveProviderSettings(settings: ProviderSettings) {
  localStorage.setItem(PROVIDER_STORAGE_KEY, JSON.stringify(settings));
}

function headerSafe(value: string): string {
  return value.replace(/[^\x20-\x7e]/g, "").trim();
}

function providerHeaders(): Record<string, string> {
  const auth = getStoredAuth();
  const headers: Record<string, string> = {};
  if (auth?.testerId) {
    headers["x-pptgod-tester-id"] = auth.testerId;
  }
  const provider = getProviderSettings();
  const textApiKey = headerSafe(provider.textApiKey);
  const textApiBase = headerSafe(provider.textApiBase);
  const textModel = headerSafe(provider.textModel);
  const imageApiKey = headerSafe(provider.imageApiKey);
  const imageApiBase = headerSafe(provider.imageApiBase);
  const imageModel = headerSafe(provider.imageModel);
  if (textApiKey) headers["x-pptgod-text-api-key"] = textApiKey;
  if (textApiKey && textApiBase) headers["x-pptgod-text-api-base"] = textApiBase;
  if (textApiKey && textModel) headers["x-pptgod-text-model"] = textModel;
  if (imageApiKey) headers["x-pptgod-image-api-key"] = imageApiKey;
  if (imageApiKey && imageApiBase) headers["x-pptgod-image-api-base"] = imageApiBase;
  if (imageApiKey && imageModel) headers["x-pptgod-image-model"] = imageModel;
  return headers;
}

export async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}) {
  const headers = new Headers(init.headers || {});
  for (const [key, value] of Object.entries(providerHeaders())) {
    if (value && !headers.has(key)) headers.set(key, value);
  }
  return window.fetch(input, { ...init, headers });
}

export function formatApiErrorDetail(detail: any): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const formatted = detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (!item || typeof item !== "object") return String(item);
        const loc = Array.isArray(item.loc)
          ? item.loc.filter((part: any) => part !== "body").join(".")
          : "";
        const msg = item.msg || item.message || item.type || JSON.stringify(item);
        return loc ? `${loc}: ${msg}` : String(msg);
      })
      .filter(Boolean);
    return formatted.join("；") || "请求参数不正确";
  }
  if (detail && typeof detail === "object") {
    return detail.message || detail.msg || JSON.stringify(detail);
  }
  return String(detail || "服务器错误");
}

export async function testerLogin(displayName: string, passcode: string = ""): Promise<MvpAuth> {
  const res = await window.fetch(`${API_BASE}/auth/tester-login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ display_name: displayName, passcode }),
  });
  const data = await (await checkRes(res)).json();
  return { testerId: data.tester_id, displayName: data.display_name };
}

export async function fetchAuthMe(): Promise<MvpAuth> {
  const res = await apiFetch(`${API_BASE}/auth/me`);
  const data = await (await checkRes(res)).json();
  return { testerId: data.tester_id, displayName: data.display_name || "测试用户" };
}

export interface BrowserHandoffResult extends MvpAuth {
  projectId: string;
  stage: "project" | "content" | "visual" | "review";
  agentContext: AgentCapabilityContext;
}

export async function redeemBrowserHandoff(token: string, projectId: string): Promise<BrowserHandoffResult> {
  const res = await window.fetch(`${API_BASE}/auth/browser-handoff/redeem`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, project_id: projectId }),
  });
  const data = await (await checkRes(res)).json();
  return {
    testerId: data.tester_id,
    displayName: data.display_name || "测试用户",
    projectId: data.project_id,
    stage: data.stage,
    agentContext: {
      projectId: data.project_id,
      agentName: data.agent_name || "外部 Agent",
      textGeneration: Boolean(data.agent_capabilities?.text_generation),
      imageGeneration: Boolean(data.agent_capabilities?.image_generation),
    },
  };
}

export interface RuntimeCapability {
  id: "text_generation" | "image_generation";
  label: string;
  available: boolean;
  provider_configured: boolean;
  agent_supplied: boolean;
  source: "provider" | "agent" | "missing";
  api_base: string;
  model: string;
  missing_fields: string[];
  used_for: string[];
}

export interface RuntimeReadiness {
  ok: boolean;
  ready: boolean;
  standalone_ready: boolean;
  summary: string;
  capabilities: {
    text_generation: RuntimeCapability;
    image_generation: RuntimeCapability;
  };
  missing: string[];
  principle: string;
  next_steps: Array<{ capability: string; action: string; message: string }>;
}

export async function fetchRuntimeReadiness(agentContext?: AgentCapabilityContext | null): Promise<RuntimeReadiness> {
  const url = makeApiUrl("/agent/readiness");
  if (agentContext?.textGeneration) url.searchParams.set("agent_text", "true");
  if (agentContext?.imageGeneration) url.searchParams.set("agent_image", "true");
  const res = await apiFetch(url.toString());
  return (await (await checkRes(res)).json()) as RuntimeReadiness;
}

export class ApiError extends Error {
  status: number;
  detail: any;

  constructor(status: number, message: string, detail: any = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export async function checkRes(res: Response) {
  const contentType = String(res.headers.get("content-type") || "").toLowerCase();
  if (res.ok && contentType.includes("text/html")) {
    throw new ApiError(
      502,
      "PPT God 本地服务正在更新，或页面与服务版本不一致。请重新双击“打开 PPT GOD.command”，页面会自动刷新到当前版本。",
    );
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "Unknown error");
    const isHtml = text.trim().startsWith("<") && text.includes("</");
    if (isHtml) {
      const title = text.match(/<title>(.*?)<\/title>/i)?.[1];
      throw new ApiError(res.status, `HTTP ${res.status}: ${title || "服务器错误"}`);
    }
    // FastAPI 返回 { detail: "..." }，尝试提取
    let json: any = null;
    try {
      json = JSON.parse(text);
    } catch {
      json = null;
    }
    if (json?.detail) {
      const message = `HTTP ${res.status}: ${formatApiErrorDetail(json.detail)}`;
      const error = new ApiError(res.status, message, json.detail);
      if (["missing_model_capability", "agent_action_required"].includes(String(json.detail?.code || ""))) {
        window.dispatchEvent(new CustomEvent(CAPABILITY_REQUIRED_EVENT, { detail: json.detail }));
      }
      throw error;
    }
    throw new ApiError(res.status, `HTTP ${res.status}: ${text.slice(0, 200)}`);
  }
  return res;
}

export async function fetchProjects() {
  const res = await apiFetch(`${API_BASE}/projects`);
  return (await checkRes(res)).json();
}

export async function fetchProject(projectId: string) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}`);
  return (await checkRes(res)).json();
}

export async function createProject(title: string, styleId?: string) {
  const res = await apiFetch(`${API_BASE}/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, style_id: styleId }),
  });
  return (await checkRes(res)).json();
}

export async function updateProject(
  projectId: string,
  data: { title?: string; content_plan_confirmed?: boolean; intent_contract?: Record<string, any> }
) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return (await checkRes(res)).json();
}

export async function deleteProject(projectId: string) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}`, {
    method: "DELETE",
  });
  return (await checkRes(res)).json();
}

export async function generateContentPlan(projectId: string, topic?: string, pageCount?: number, attachmentIds?: string[], chatContext?: string) {
  const body: any = {};
  if (topic) body.topic = topic;
  if (pageCount) body.page_count = pageCount;
  if (attachmentIds?.length) body.attachment_ids = attachmentIds;
  if (chatContext && chatContext.trim()) body.chat_context = chatContext.trim();
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/content-plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return (await checkRes(res)).json();
}

export async function fetchSlides(projectId: string) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/slides`);
  return (await checkRes(res)).json();
}

export async function generateVisualPlan(projectId: string, pageNums?: number[], stageContext?: string) {
  const body: any = {};
  if (pageNums) body.page_nums = pageNums;
  if (stageContext) body.stage_context = stageContext;
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/visual-plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return (await checkRes(res)).json();
}

export async function generatePrompts(projectId: string, pageNums?: number[], stageContext?: string) {
  const body: any = {};
  if (pageNums) body.page_nums = pageNums;
  if (stageContext) body.stage_context = stageContext;
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/prompts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return (await checkRes(res)).json();
}

export async function generateVisualPrompts(projectId: string, pageNums?: number[], stageContext?: string) {
  const body: any = {};
  if (pageNums) body.page_nums = pageNums;
  if (stageContext) body.stage_context = stageContext;
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/visual-prompts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return (await checkRes(res)).json();
}

export async function startGeneration(projectId: string, pageNums?: number[], prototype?: boolean) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ page_nums: pageNums, prototype }),
  });
  return (await checkRes(res)).json();
}

export async function stopGeneration(projectId: string) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/stop-generation`, {
    method: "POST",
  });
  return (await checkRes(res)).json();
}

export async function confirmPrototype(projectId: string) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/confirm-prototype`, {
    method: "POST",
  });
  return (await checkRes(res)).json();
}

export async function fetchWorkflowStatus(projectId: string) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/workflow-status`);
  return (await checkRes(res)).json();
}

export function getDownloadUrl(projectId: string, prototype?: boolean) {
  const url = makeApiUrl(`/projects/${projectId}/download`);
  if (prototype) url.searchParams.set("prototype", "1");
  const testerId = getStoredAuth()?.testerId;
  if (testerId) url.searchParams.set("tester_id", testerId);
  return url.toString();
}

export function getContentPlanMarkdownUrl(projectId: string) {
  const url = makeApiUrl(`/projects/${projectId}/slides/export-markdown`);
  const testerId = getStoredAuth()?.testerId;
  if (testerId) url.searchParams.set("tester_id", testerId);
  return url.toString();
}

export async function uploadFile(
  projectId: string,
  file: File,
  role: "style_ref" | "logo" | "template" | "visual_asset" | "content_ref" | "chart_ref" | "finetune_ref" | "chat_ref",
  slideId?: string,
  processMode?: "blend" | "crop" | "original",
  metadata?: { asset_name?: string; asset_kind?: string; usage_note?: string; logo_anchor?: string }
) {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("role", role);
  if (slideId) {
    formData.append("slide_id", slideId);
  }
  if (processMode) formData.append("process_mode", processMode);
  if (metadata?.asset_name) formData.append("asset_name", metadata.asset_name);
  if (metadata?.asset_kind) formData.append("asset_kind", metadata.asset_kind);
  if (metadata?.usage_note) formData.append("usage_note", metadata.usage_note);
  if (metadata?.logo_anchor) formData.append("logo_anchor", metadata.logo_anchor);
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/upload`, {
    method: "POST",
    body: formData,
  });
  return (await checkRes(res)).json();
}

export async function suggestReferenceImages(projectId: string) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/suggest-reference-images`, {
    method: "POST",
  });
  return (await checkRes(res)).json();
}

export async function fetchReferenceImages(projectId: string, slideId?: string) {
  let url = `${API_BASE}/projects/${projectId}/reference-images`;
  if (slideId) {
    url += `?slide_id=${slideId}`;
  }
  const res = await apiFetch(url);
  const data = await (await checkRes(res)).json();
  return Array.isArray(data) ? data : (data.items || []);
}

export async function updateSlideAssetPins(
  projectId: string,
  slideId: string,
  assetIds: string[],
  usage: Record<string, string> = {}
) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/slides/${slideId}/asset-pins`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ asset_ids: assetIds, usage }),
  });
  return (await checkRes(res)).json();
}

export async function updateSlideOverlayLayers(
  projectId: string,
  slideId: string,
  layers: any[]
) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/slides/${slideId}/overlay-layers`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ layers }),
  });
  return (await checkRes(res)).json();
}

export async function deleteReferenceImage(projectId: string, refId: string) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/reference-images/${refId}`, {
    method: "DELETE",
  });
  return (await checkRes(res)).json();
}

export async function updateReferenceImage(projectId: string, refId: string, data: {
  process_mode?: string;
  asset_name?: string;
  asset_kind?: string;
  usage_note?: string;
  logo_anchor?: string;
  review_status?: string;
  review_reason?: string;
  reanalyze?: boolean;
}) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/reference-images/${refId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return (await checkRes(res)).json();
}

export async function retrySlide(projectId: string, slideId: string, regeneratePrompt: boolean = false, userFeedback?: string) {
  const body: any = { regenerate_prompt: regeneratePrompt };
  if (userFeedback && userFeedback.trim()) body.user_feedback = userFeedback.trim();
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/slides/${slideId}/retry`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return (await checkRes(res)).json();
}

export async function retryFailed(projectId: string) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/retry-failed`, {
    method: "POST",
  });
  return (await checkRes(res)).json();
}

// ========== 单页微调：版本管理 ==========

export async function getSlideVersions(projectId: string, slideId: string) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/slides/${slideId}/versions`);
  return (await checkRes(res)).json();
}

export async function deleteSlideVersion(projectId: string, slideId: string, versionId: string) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/slides/${slideId}/versions/${versionId}`, {
    method: "DELETE",
  });
  return (await checkRes(res)).json();
}

export async function restoreSlideVersion(projectId: string, slideId: string, versionId: string) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/slides/${slideId}/versions/${versionId}/restore`, {
    method: "POST",
  });
  return (await checkRes(res)).json();
}

export interface FinetuneRegion {
  id?: string;
  label?: string;
  bbox: {
    x: number;
    y: number;
    width: number;
    height: number;
  };
}

export async function finetuneSlide(projectId: string, slideId: string, instruction: string, attachmentIds?: string[], regions?: FinetuneRegion[]) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/slides/${slideId}/finetune`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ instruction, attachment_ids: attachmentIds || [], regions: regions || [] }),
  });
  return (await checkRes(res)).json();
}

export async function* chatWithAgentStream(
  projectId: string,
  message: string,
  history?: { role: string; content: string }[],
  signal?: AbortSignal,
  pageContext?: any,
  agentRole?: string,
  attachmentIds?: string[]
) {
  let response: Response;
  try {
    response = await apiFetch(`${API_BASE}/projects/${projectId}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        history,
        page_context: pageContext,
        agent_role: agentRole || "content",
        attachment_ids: attachmentIds || [],
      }),
      signal,
    });
  } catch {
    yield { type: "error", message: "网络连接失败，请检查网络后重试" };
    return;
  }

  if (!response.ok) {
    const text = await response.text().catch(() => "Unknown error");
    yield { type: "error", message: `HTTP ${response.status}: ${text}` };
    return;
  }

  if (!response.body) {
    yield { type: "error", message: "服务器未返回数据" };
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        const normalizedLine = line.endsWith("\r") ? line.slice(0, -1) : line;
        if (normalizedLine.startsWith("data: ")) {
          try {
            const data = JSON.parse(normalizedLine.slice(6));
            yield data;
          } catch {
            // ignore malformed lines
          }
        }
      }
    }
  } catch (readErr: any) {
    const errMsg = readErr?.message || "";
    // 只有用户主动取消（点击停止）才静默返回
    if (signal?.aborted) {
      return;
    }
    // Chrome 等浏览器在流意外中断时会抛 AbortError（如 BodyStreamBuffer was aborted）
    // 这不是用户主动取消，必须上报，否则聊天会"卡住"且用户无感知
    yield { type: "error", message: "读取响应流失败：" + (errMsg || "网络连接中断") };
    return;
  } finally {
    reader.releaseLock();
  }

  const trailing = decoder.decode();
  if (trailing) {
    buffer += trailing;
  }

  for (const line of buffer.split("\n")) {
    const normalizedLine = line.trimEnd();
    if (!normalizedLine) continue;
    if (normalizedLine.startsWith("data: ")) {
      try {
        yield JSON.parse(normalizedLine.slice(6));
      } catch {
        // 流结束时还有未解析完的 data 行（JSON 被截断），主动报错而不是静默忽略
        yield { type: "error", message: "响应流被意外中断，JSON 不完整" };
      }
    }
  }
}

export async function uploadDocument(projectId: string, file: File) {
  const formData = new FormData();
  formData.append("file", file);
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/upload-document`, {
    method: "POST",
    body: formData,
  });
  return (await checkRes(res)).json();
}

export async function fetchDocuments(projectId: string) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/documents`);
  return (await checkRes(res)).json();
}

export async function deleteDocument(projectId: string, filename: string) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/documents/${encodeURIComponent(filename)}`, {
    method: "DELETE",
  });
  return (await checkRes(res)).json();
}

export async function updateSlideContent(projectId: string, pageNum: number, contentJson: any, slideId?: string) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/slides/content`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ page_num: pageNum, slide_id: slideId, content_json: contentJson }),
  });
  return (await checkRes(res)).json();
}

export async function updateVisualPlan(projectId: string, pageNum: number, visualJson: any, slideId?: string) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/slides/visual`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ page_num: pageNum, slide_id: slideId, visual_json: visualJson }),
  });
  return (await checkRes(res)).json();
}

export async function updateSlideType(projectId: string, pageNum: number, type: string, slideId?: string) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/slides/type`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ page_num: pageNum, slide_id: slideId, type }),
  });
  return (await checkRes(res)).json();
}

export async function deleteSlide(projectId: string, slideId: string) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/slides/${slideId}`, {
    method: "DELETE",
  });
  return (await checkRes(res)).json();
}

export async function createSlide(projectId: string, pageNum: number, contentJson: any) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/slides`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ page_num: pageNum, content_json: contentJson }),
  });
  return (await checkRes(res)).json();
}

export async function reorderSlides(projectId: string, pageNums: number[]) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/reorder`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ page_nums: pageNums }),
  });
  return (await checkRes(res)).json();
}

export async function extractTemplate(projectId: string, file: File) {
  const formData = new FormData();
  formData.append("file", file);
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/extract-template`, {
    method: "POST",
    body: formData,
  });
  return (await checkRes(res)).json();
}

export async function fetchTemplatePages(projectId: string) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/template-pages`);
  return (await checkRes(res)).json();
}

export async function fetchTemplateStatus(projectId: string) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/template-status`);
  return (await checkRes(res)).json();
}

export async function updateTemplateRecommendations(projectId: string, recommendations: any) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/template-recommendations`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ recommendations }),
  });
  return (await checkRes(res)).json();
}

export async function updateProjectStyle(projectId: string, selectedStyle: any) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/style`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ selected_style: selectedStyle }),
  });
  return (await checkRes(res)).json();
}

export async function generateStyleProposals(projectId: string, force: boolean = false, userDescription: string = ""): Promise<any> {
  const url = makeApiUrl(`/projects/${projectId}/style-proposals`);
  if (force) url.searchParams.set("force", "true");
  const trimmedDescription = userDescription.trim();
  const res = await apiFetch(url.toString(), {
    method: "POST",
    ...(trimmedDescription
      ? {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ user_description: trimmedDescription }),
        }
      : {}),
  });
  return (await checkRes(res)).json();
}

export async function pollForStyleProposals(
  projectId: string,
  maxAttempts = 120,
  intervalMs = 2000
): Promise<any[]> {
  for (let i = 0; i < maxAttempts; i++) {
    await new Promise((r) => setTimeout(r, intervalMs));
    const project = await fetchProject(projectId);
    if (project?.style_proposal?.proposals) {
      return project.style_proposal.proposals;
    }
    const workflow = await fetchWorkflowStatus(projectId);
    const run = workflow?.active_run || workflow?.last_run;
    if (run?.kind === "style_proposal" && ["failed", "stale", "cancelled"].includes(run.status)) {
      throw new Error(run.message || run.error_msg || "风格提案生成没有完成，请重试");
    }
  }
  throw new Error("风格提案生成超时，请刷新页面后重试");
}

export async function rollbackProject(projectId: string, targetStage: string) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}/rollback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target_stage: targetStage }),
  });
  return (await checkRes(res)).json();
}
