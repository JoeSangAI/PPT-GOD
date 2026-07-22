import { useCallback, useEffect, useRef, useState } from "react";
import App from "./App";
import PptGodLogo from "./components/PptGodLogo";
import {
  CAPABILITY_REQUIRED_EVENT,
  DEFAULT_PROVIDER_SETTINGS,
  clearStoredAuth,
  fetchRuntimeReadiness,
  getProviderSettings,
  getStoredAgentContext,
  getStoredAuth,
  redeemBrowserHandoff,
  saveProviderSettings,
  saveAgentContext,
  saveStoredAuth,
  type MvpAuth,
  type AgentCapabilityContext,
  type ProviderSettings,
  type RuntimeReadiness,
} from "./api/client";

const LOCAL_WORKSPACE: MvpAuth = { testerId: "local-admin", displayName: "本地工作区" };

type BrowserRoute = {
  projectId: string;
  stage: "project" | "content" | "visual" | "review";
  handoffToken: string;
};

function getBrowserRoute(): BrowserRoute | null {
  const match = window.location.pathname.match(/^\/app\/projects\/([^/]+)\/?$/);
  if (!match) return null;
  const params = new URLSearchParams(window.location.search);
  const requestedStage = params.get("stage") || "project";
  const stage = ["project", "content", "visual", "review"].includes(requestedStage)
    ? requestedStage as BrowserRoute["stage"]
    : "project";
  return {
    projectId: decodeURIComponent(match[1]),
    stage,
    handoffToken: params.get("handoff") || "",
  };
}

function removeHandoffTokenFromAddressBar() {
  const url = new URL(window.location.href);
  url.searchParams.delete("handoff");
  window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
}

function getInitialAuth(): MvpAuth | null {
  return getStoredAuth();
}

function providerName(baseUrl: string, fallback: string) {
  try {
    const host = new URL(baseUrl).hostname.toLowerCase();
    if (host.includes("cometapi")) return "CometAPI";
    if (host.includes("minimax")) return "MiniMax";
    if (host.includes("openrouter")) return "OpenRouter";
    return host.replace(/^api\./, "").replace(/^www\./, "");
  } catch {
    return fallback;
  }
}

function userFacingConnectionError(message?: string) {
  const text = String(message || "").trim();
  if (/failed to fetch|networkerror|network request failed|load failed/i.test(text)) {
    return "没有连接到 PPT God 本地服务。请先启动服务，再点“重新检测”。";
  }
  return text || "暂时无法检测运行环境，请稍后重试。";
}

function ProviderSetup({
  value,
  onChange,
  defaultAdvancedOpen = false,
  focusCapability = null,
}: {
  value: ProviderSettings;
  onChange: (next: ProviderSettings) => void;
  defaultAdvancedOpen?: boolean;
  focusCapability?: "text_generation" | "image_generation" | null;
}) {
  const [notice, setNotice] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(defaultAdvancedOpen);
  const showText = !focusCapability || focusCapability === "text_generation";
  const showImage = !focusCapability || focusCapability === "image_generation";

  const update = (key: keyof ProviderSettings, nextValue: string) => {
    setNotice("");
    onChange({ ...value, [key]: nextValue });
  };

  const useCometPreset = () => {
    onChange({
      ...value,
      ...(showText ? {
        textApiBase: DEFAULT_PROVIDER_SETTINGS.textApiBase,
        textModel: value.textModel || DEFAULT_PROVIDER_SETTINGS.textModel,
      } : {}),
      ...(showImage ? {
        imageApiKey: value.imageApiKey || value.textApiKey,
        imageApiBase: DEFAULT_PROVIDER_SETTINGS.imageApiBase,
        imageModel: value.imageModel || DEFAULT_PROVIDER_SETTINGS.imageModel,
      } : {}),
    });
    setNotice(focusCapability ? "已使用 CometAPI 预设；模型名称仍可自行修改。" : "已使用 CometAPI 预设；如果文本 Key 和图片 Key 相同，已自动同步。模型名称仍可自行修改。");
  };

  return (
    <div className="pg-provider-setup">
      <div className="pg-provider-header">
        <div>
          <div className="pg-provider-title">模型连接</div>
          <p>
            PPT God 不绑定模型厂商。你可以使用任意兼容 OpenAI 接口的文本模型和图片模型；想省事，可以从
            {" "}<a href="https://www.cometapi.com/pricing/" target="_blank" rel="noreferrer">CometAPI 模型大厅</a>{" "}
            选择模型并使用同一枚 Key。
          </p>
        </div>
        <div className="pg-provider-actions">
          <button type="button" className="pg-subtle-button" onClick={useCometPreset}>使用 CometAPI 预设</button>
        </div>
        {notice && <div className="pg-provider-notice">{notice}</div>}
      </div>

      <div className={`pg-key-grid ${focusCapability ? "pg-key-grid--single" : ""}`}>
        {showText && <label className="pg-auth-field">
          <span>文本模型 API Key</span>
          <em>用于内容规划、视觉方向和每页画面描述。由 Agent 提供这些成果时，可以先不填。</em>
          <input
            className="pg-auth-input"
            value={value.textApiKey}
            onChange={(event) => update("textApiKey", event.target.value)}
            placeholder="粘贴文本模型 API Key"
            type="password"
            autoComplete="off"
          />
        </label>}

        {showImage && <label className="pg-auth-field">
          <span>图片模型 API Key</span>
          <em>用于整页画面生成和改单页。若与文本模型共用平台，可以填写同一枚 Key。</em>
          <input
            className="pg-auth-input"
            value={value.imageApiKey}
            onChange={(event) => update("imageApiKey", event.target.value)}
            placeholder="粘贴图片模型 API Key"
            type="password"
            autoComplete="off"
          />
        </label>}
      </div>

      <div className="pg-connection-card">
        <div>
          <div className="pg-connection-title">使用其他平台或其他模型？</div>
          <p>展开后填写平台提供的 API 地址和准确模型名称。PPT God 只要求接口兼容，不限制具体模型。</p>
        </div>
        <button
          type="button"
          className="pg-connection-button"
          onClick={() => setAdvancedOpen((open) => !open)}
          aria-expanded={advancedOpen}
        >
          {advancedOpen ? "收起设置" : "高级设置"}
        </button>
      </div>

      {advancedOpen && (
        <div className="pg-advanced-panel">
          {showText && <section className="pg-endpoint-group">
            <div className="pg-endpoint-head">
              <span>文本生成接口</span>
              <p>需要兼容 OpenAI Chat Completions，并能稳定输出结构化内容。</p>
            </div>
            <div className="pg-advanced-grid">
              <label className="pg-auth-field pg-field-wide">
                <span>API 地址</span>
                <input className="pg-auth-input" value={value.textApiBase} onChange={(event) => update("textApiBase", event.target.value)} />
              </label>
              <label className="pg-auth-field">
                <span>模型名称</span>
                <input className="pg-auth-input" value={value.textModel} onChange={(event) => update("textModel", event.target.value)} />
              </label>
            </div>
          </section>}

          {showImage && <section className="pg-endpoint-group">
            <div className="pg-endpoint-head">
              <span>图片生成接口</span>
              <p>需要兼容 OpenAI Images；使用参考图或改单页时，还需要支持图片编辑。</p>
            </div>
            <div className="pg-advanced-grid">
              <label className="pg-auth-field pg-field-wide">
                <span>API 地址</span>
                <input className="pg-auth-input" value={value.imageApiBase} onChange={(event) => update("imageApiBase", event.target.value)} />
              </label>
              <label className="pg-auth-field">
                <span>模型名称</span>
                <input className="pg-auth-input" value={value.imageModel} onChange={(event) => update("imageModel", event.target.value)} />
              </label>
            </div>
          </section>}
          <div className="pg-provider-footnote">Key 长期只保存在当前浏览器；任务运行时会临时传给本地 PPT God 服务。</div>
        </div>
      )}
    </div>
  );
}

function CapabilityRow({
  title,
  status,
  description,
}: {
  title: string;
  status: "ready" | "missing" | "checking";
  description: string;
}) {
  const label = status === "ready" ? "已配置" : status === "checking" ? "检测中" : "未配置";
  return (
    <div className={`pg-capability-row pg-capability-row--${status}`}>
      <span className="pg-capability-dot" aria-hidden="true" />
      <div className="pg-capability-copy">
        <div className="pg-capability-title">{title}<b>{label}</b></div>
        <p>{description}</p>
      </div>
    </div>
  );
}

export default function AuthGate() {
  const browserRouteRef = useRef<BrowserRoute | null>(getBrowserRoute());
  const [auth, setAuth] = useState<MvpAuth | null>(() => browserRouteRef.current?.handoffToken ? null : getInitialAuth());
  const [handoffPending, setHandoffPending] = useState(Boolean(browserRouteRef.current?.handoffToken));
  const handoffStartedRef = useRef(false);
  const [provider, setProvider] = useState<ProviderSettings>(() => getProviderSettings());
  const [agentContext, setAgentContext] = useState<AgentCapabilityContext | null>(() =>
    getStoredAgentContext(browserRouteRef.current?.projectId)
  );
  const [readiness, setReadiness] = useState<RuntimeReadiness | null>(null);
  const [checking, setChecking] = useState(true);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const [capabilityRequest, setCapabilityRequest] = useState<{ capability: string; message: string } | null>(null);
  const [error, setError] = useState("");

  const refreshReadiness = useCallback(async () => {
    setChecking(true);
    setError("");
    try {
      setReadiness(await fetchRuntimeReadiness(agentContext));
    } catch (exception: any) {
      setReadiness(null);
      setError(userFacingConnectionError(exception?.message));
    } finally {
      setChecking(false);
    }
  }, [agentContext]);

  useEffect(() => {
    void refreshReadiness();
  }, [refreshReadiness]);

  useEffect(() => {
    const route = browserRouteRef.current;
    if (!route?.handoffToken || handoffStartedRef.current) return;
    handoffStartedRef.current = true;
    redeemBrowserHandoff(route.handoffToken, route.projectId)
      .then((result) => {
        const nextAuth = { testerId: result.testerId, displayName: result.displayName };
        saveStoredAuth(nextAuth);
        localStorage.setItem("ppt_god_last_project_id", result.projectId);
        browserRouteRef.current = { ...route, projectId: result.projectId, stage: result.stage, handoffToken: "" };
        saveAgentContext(result.agentContext);
        setAgentContext(result.agentContext);
        removeHandoffTokenFromAddressBar();
        setAuth(nextAuth);
      })
      .catch((exception: any) => {
        clearStoredAuth();
        removeHandoffTokenFromAddressBar();
        setAuth(null);
        setError(userFacingConnectionError(exception?.message));
      })
      .finally(() => setHandoffPending(false));
  }, []);

  useEffect(() => {
    const handleCapabilityRequired = (event: Event) => {
      const detail = (event as CustomEvent).detail || {};
      const capability = String(detail.capability || "");
      const message = String(detail.message || "还缺少运行这一步所需的模型能力。");
      setCapabilityRequest({ capability, message });
      const delegated =
        (capability === "text_generation" && agentContext?.textGeneration) ||
        (capability === "image_generation" && agentContext?.imageGeneration);
      if (delegated) {
        setSettingsOpen(false);
      } else {
        setSettingsOpen(true);
      }
    };
    window.addEventListener(CAPABILITY_REQUIRED_EVENT, handleCapabilityRequired);
    return () => window.removeEventListener(CAPABILITY_REQUIRED_EVENT, handleCapabilityRequired);
  }, [agentContext]);

  useEffect(() => {
    const route = browserRouteRef.current;
    if (!route || route.handoffToken || auth) return;
    saveStoredAuth(LOCAL_WORKSPACE);
    setAuth(LOCAL_WORKSPACE);
  }, [auth]);

  const saveModels = async () => {
    saveProviderSettings(provider);
    await refreshReadiness();
    setSettingsOpen(false);
    setCapabilityRequest(null);
  };

  const enterWorkspace = () => {
    saveProviderSettings(provider);
    saveStoredAuth(LOCAL_WORKSPACE);
    setSettingsOpen(false);
    setAuth(LOCAL_WORKSPACE);
  };

  const returnToLaunchCenter = () => {
    clearStoredAuth();
    setUserMenuOpen(false);
    setAuth(null);
    window.history.replaceState(null, "", "/");
  };

  const imageReady = Boolean(readiness?.capabilities.image_generation.available);
  const textAvailable = Boolean(readiness?.capabilities.text_generation.available);
  const agentCanHandleRequest = Boolean(
    capabilityRequest && (
      (capabilityRequest.capability === "text_generation" && agentContext?.textGeneration) ||
      (capabilityRequest.capability === "image_generation" && agentContext?.imageGeneration)
    )
  );
  const capabilitySummary = (capability: RuntimeReadiness["capabilities"]["text_generation"] | undefined) => {
    if (!capability) return "未配置";
    if (capability.source === "agent") return `${agentContext?.agentName || "外部 Agent"} 代劳`;
    if (capability.provider_configured) {
      return `${providerName(capability.api_base, "已配置")} · ${capability.model || "已选择模型"}`;
    }
    return "未配置";
  };
  const capabilityStatus = (ready: boolean) => checking ? "checking" as const : ready ? "ready" as const : "missing" as const;

  if (handoffPending) {
    return (
      <div className="pg-auth pg-auth-v2">
        <div className="pg-auth-backdrop" aria-hidden="true" />
        <main className="pg-auth-shell">
          <section className="pg-auth-card pg-auth-card-v2" aria-live="polite">
            <div className="pg-auth-card-head"><h2>正在打开项目</h2><p>正在衔接 Agent 创建的项目，无需登录或重新配置账号。</p></div>
          </section>
        </main>
      </div>
    );
  }

  if (auth) {
    const browserRoute = browserRouteRef.current;
    return (
      <>
        <App key={auth.testerId} initialProjectId={browserRoute?.projectId} initialStage={browserRoute?.stage} />
        <div className="pg-user-menu fixed bottom-3 left-3 z-[60] text-xs text-slate-600">
          {userMenuOpen && (
            <div className="pg-user-menu-card mb-2 w-[280px] rounded-lg border border-slate-200 bg-white p-3 shadow-xl">
              <div className="mb-2 flex items-center justify-between gap-2">
                <div><div className="text-sm font-semibold text-slate-900">本地工作区</div><div className="text-[11px] text-slate-400">无需登录，项目保存在本机</div></div>
                <button className="pg-action pg-action-secondary rounded px-2 py-1 text-slate-400 hover:bg-slate-100" onClick={() => setUserMenuOpen(false)}>收起</button>
              </div>
              <div className="space-y-1 rounded-md bg-slate-50 p-2 text-[11px] leading-5 text-slate-500">
                <div>文本模型：{capabilitySummary(readiness?.capabilities.text_generation)}</div>
                <div>图片模型：{capabilitySummary(readiness?.capabilities.image_generation)}</div>
              </div>
              <div className="mt-3 grid grid-cols-2 gap-2">
                <button className="pg-action pg-action-secondary rounded bg-slate-100 px-2 py-1.5 hover:bg-slate-200" onClick={() => setSettingsOpen(true)}>模型设置</button>
                <button className="pg-action pg-action-secondary rounded bg-slate-100 px-2 py-1.5 hover:bg-slate-200" onClick={returnToLaunchCenter}>启动页</button>
              </div>
            </div>
          )}
          <button className="pg-user-trigger flex max-w-[190px] items-center gap-2 rounded-full border border-slate-200 bg-white/95 px-3 py-1.5 shadow-lg backdrop-blur hover:bg-slate-50" onClick={() => setUserMenuOpen((open) => !open)} title="运行与模型设置">
            <span className={`h-2 w-2 rounded-full ${textAvailable && imageReady ? "bg-emerald-500" : "bg-amber-500"}`} />
            <span className="truncate font-medium text-slate-800">运行设置</span>
          </button>
        </div>
        {settingsOpen && (
          <div className="pg-settings-backdrop">
            <div className="pg-settings-modal">
              <div className="mb-4 flex items-start justify-between gap-4">
                <div><h2 className="text-lg font-semibold text-slate-900">模型设置</h2><p className="mt-1 text-sm text-slate-500">只配置当前工作流真正缺少的能力。</p></div>
                <button className="pg-action pg-action-secondary rounded-md px-2 py-1 text-slate-500 hover:bg-slate-100" onClick={() => setSettingsOpen(false)}>关闭</button>
              </div>
              {capabilityRequest && !agentCanHandleRequest && (
                <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm leading-6 text-amber-900" role="alert">
                  {capabilityRequest.message}
                </div>
              )}
              <ProviderSetup
                value={provider}
                onChange={setProvider}
                defaultAdvancedOpen
                focusCapability={capabilityRequest?.capability === "text_generation" || capabilityRequest?.capability === "image_generation" ? capabilityRequest.capability : null}
              />
              <div className="mt-5 flex justify-end gap-2">
                <button type="button" className="pg-action pg-action-primary rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700" onClick={() => void saveModels()}>保存并检测</button>
              </div>
            </div>
          </div>
        )}
        {capabilityRequest && agentCanHandleRequest && (
          <div className="pg-settings-backdrop">
            <div className="pg-settings-modal" role="dialog" aria-modal="true" aria-label="由 Agent 继续">
              <h2 className="text-lg font-semibold text-slate-900">交给 {agentContext?.agentName || "当前 Agent"} 继续</h2>
              <p className="mt-2 text-sm leading-6 text-slate-600">
                这一步需要{capabilityRequest.capability === "image_generation" ? "图片生成" : "文本生成"}。当前 Agent 已声明可以代劳，项目进度已经保存；请回到 Agent 对话继续。
              </p>
              <div className="mt-5 flex justify-end gap-2">
                <button className="pg-action pg-action-secondary rounded-md px-3 py-2 text-sm" onClick={() => { setCapabilityRequest(null); setSettingsOpen(true); }}>改为自己配置</button>
                <button className="pg-action pg-action-primary rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white" onClick={() => setCapabilityRequest(null)}>知道了</button>
              </div>
            </div>
          </div>
        )}
      </>
    );
  }

  return (
    <div className="pg-auth pg-auth-v2">
      <div className="pg-auth-backdrop" aria-hidden="true" />
      <main className="pg-auth-shell">
        <section className="pg-auth-story">
          <PptGodLogo className="pg-auth-wordmark" />
          <p className="pg-auth-lead">本地开源版不需要登录。PPT God 负责把内容规划、视觉方向、页面生成和导出串成一条可确认的工作流。</p>
          <div className="pg-auth-value"><p>模型能力可以由你自己的 API Key 提供，也可以由 WorkBuddy、Codex、Claude Code 等外部 Agent 提供相应成果。</p></div>
        </section>

        <section className="pg-auth-card pg-auth-card-v2" aria-label="PPT God 启动中心">
          <div className="pg-auth-card-head"><h2>准备好 PPT God</h2><p>你可以先进入工作台。只有运行到真正需要模型的步骤时，系统才会提示缺少什么。</p></div>
          <div className="pg-launch-body">
            <ol className="pg-onboarding-steps" aria-label="三步开始使用">
              <li><b>1</b><span><strong>先进入</strong>无需登录，也不用一次配完所有模型。</span></li>
              <li><b>2</b><span><strong>做内容</strong>需要文本模型时再配置；Agent 已代劳则直接接着做。</span></li>
              <li><b>3</b><span><strong>生成页面</strong>需要图片模型时会明确提醒，也可让能生图的 Agent 交付页面图。</span></li>
            </ol>
            <CapabilityRow
              title="文本生成"
              status={capabilityStatus(textAvailable)}
              description={textAvailable ? (readiness?.capabilities.text_generation.source === "agent" ? `由 ${agentContext?.agentName || "外部 Agent"} 提供内容与视觉文本。` : "可以生成内容规划、视觉方向和页面描述。") : "独立使用时需要；若 Agent 已导入内容规划，可先跳过。"}
            />
            <CapabilityRow
              title="图片生成"
              status={capabilityStatus(imageReady)}
              description={imageReady ? "可以生成整页画面，并支持后续改单页。" : "生成页面前通常需要；若外部 Agent 直接提供最终页面图，则可由 Agent 承担。"}
            />
            {error && <div className="pg-auth-error" role="alert">{error}</div>}
            <div className="pg-launch-actions">
              <button className="pg-primary-button pg-login-submit" onClick={enterWorkspace} disabled={!readiness && !error && checking}>直接进入工作台</button>
              <button className="pg-subtle-button" onClick={() => setSettingsOpen((open) => !open)}>{settingsOpen ? "暂不配置" : "配置模型"}</button>
              {error && <button className="pg-subtle-button" onClick={() => void refreshReadiness()}>重新检测</button>}
            </div>
            {settingsOpen && <ProviderSetup value={provider} onChange={setProvider} />}
            {settingsOpen && <button className="pg-primary-button pg-login-submit" onClick={() => void saveModels()}>保存并检测</button>}
            <div className="pg-auth-assurance">原理很简单：PPT God 自己不售卖模型额度，只在需要时调用你选择的模型；Key 长期只保存在当前浏览器。</div>
          </div>
        </section>
      </main>
    </div>
  );
}
