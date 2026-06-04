import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import App from "./App";
import PptGodLogo from "./components/PptGodLogo";
import {
  API_BASE,
  CLIENT_PROVIDER_SETTINGS_ENABLED,
  DEFAULT_PROVIDER_SETTINGS,
  clearStoredAuth,
  fetchAuthMe,
  getProviderSettings,
  getStoredAuth,
  saveProviderSettings,
  saveStoredAuth,
  testerLogin,
  type MvpAuth,
  type ProviderSettings,
} from "./api/client";

const SERVER_MANAGED_PROVIDERS = !CLIENT_PROVIDER_SETTINGS_ENABLED;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function getInitialAuth(): MvpAuth | null {
  const stored = getStoredAuth();
  if (stored?.testerId === "local-admin" || (stored?.testerId && !UUID_PATTERN.test(stored.testerId))) {
    clearStoredAuth();
    return null;
  }
  return stored;
}

function providerName(baseUrl: string, fallback: string) {
  try {
    const host = new URL(baseUrl).hostname.toLowerCase();
    if (host.includes("minimax")) return "MiniMax";
    if (host.includes("deerapi")) return "Deer";
    if (host.includes("openrouter")) return "OpenRouter";
    if (host.includes("grsai")) return "GRSAI";
    return host.replace(/^api\./, "").replace(/^www\./, "");
  } catch {
    return fallback;
  }
}

function providerModelLabel(baseUrl: string, model: string, fallback: string) {
  const modelName = model.trim() || "未填写模型";
  return `${providerName(baseUrl, fallback)} · ${modelName}`;
}

function feedbackTemplate(auth: MvpAuth | null) {
  return [
    "PPT God 测试反馈",
    `测试账号：${auth?.displayName || ""}`,
    `时间：${new Date().toLocaleString()}`,
    `页面地址：${window.location.href}`,
    `后端地址：${API_BASE}`,
    `浏览器：${navigator.userAgent}`,
    "",
    "我正在做什么：",
    "",
    "遇到的问题：",
    "",
    "期望结果：",
    "",
    "截图：请附上当前页面截图",
  ].join("\n");
}

function ProviderSetup({
  value,
  onChange,
  defaultAdvancedOpen = false,
}: {
  value: ProviderSettings;
  onChange: (next: ProviderSettings) => void;
  defaultAdvancedOpen?: boolean;
}) {
  const [presetNotice, setPresetNotice] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(defaultAdvancedOpen);
  const advancedPanelRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!advancedOpen || defaultAdvancedOpen) return;
    window.requestAnimationFrame(() => {
      advancedPanelRef.current?.scrollIntoView({ block: "nearest", behavior: "auto" });
    });
  }, [advancedOpen, defaultAdvancedOpen]);

  const update = (key: keyof ProviderSettings, nextValue: string) => {
    setPresetNotice("");
    onChange({ ...value, [key]: nextValue });
  };
  const restoreDefaultProvider = () => {
    onChange({
      ...value,
      minimaxApiBase: DEFAULT_PROVIDER_SETTINGS.minimaxApiBase,
      minimaxLlmModel: DEFAULT_PROVIDER_SETTINGS.minimaxLlmModel,
      deerApiBase: DEFAULT_PROVIDER_SETTINGS.deerApiBase,
      deerImageModel: DEFAULT_PROVIDER_SETTINGS.deerImageModel,
    });
    setPresetNotice("已恢复默认 MiniMax + Deer API 配置。你填写的 Key 不会被改动。");
  };

  return (
    <div className="pg-provider-setup">
      <div className="pg-provider-header">
        <div>
          <div className="pg-provider-title">API Key</div>
          <p>默认使用 MiniMax 做内容生成、Deer API 做图片生成。如果你用的是这两个平台，只填下面两枚 Key 就可以。</p>
        </div>
        <div className="pg-provider-summary">默认：MiniMax + Deer API</div>
      </div>

      <div className="pg-key-grid">
        <label className="pg-auth-field">
          <span>MiniMax API Key</span>
          <em>用于内容规划、文案和页面结构。</em>
          <input
            className="pg-auth-input"
            value={value.minimaxApiKey}
            onChange={(e) => update("minimaxApiKey", e.target.value)}
            placeholder="粘贴 MiniMax API Key"
            type="password"
            autoComplete="off"
          />
        </label>

        <label className="pg-auth-field">
          <span>Deer API Key</span>
          <em>用于生成封面、配图和视觉素材。</em>
          <input
            className="pg-auth-input"
            value={value.deerApiKey}
            onChange={(e) => update("deerApiKey", e.target.value)}
            placeholder="粘贴 Deer API Key"
            type="password"
            autoComplete="off"
          />
        </label>
      </div>

      <div className="pg-connection-card">
        <div>
          <div className="pg-connection-title">不是 MiniMax 或 Deer API？</div>
          <p>展开后填写你实际平台的 API URL 和模型名称，否则后面生成时可能会因为配置不匹配而报错。</p>
        </div>
        <button
          type="button"
          className="pg-connection-button"
          onClick={() => setAdvancedOpen((open) => !open)}
          aria-expanded={advancedOpen}
        >
          {advancedOpen ? "收起设置" : "核对 / 修改"}
        </button>
      </div>

      {advancedOpen && (
        <div className="pg-advanced-panel" ref={advancedPanelRef}>
          <div className="pg-provider-actions">
            <button type="button" className="pg-subtle-button" onClick={restoreDefaultProvider}>
              恢复默认 MiniMax + Deer API 配置
            </button>
          </div>
          {presetNotice && <div className="pg-provider-notice">{presetNotice}</div>}

          <section className="pg-endpoint-group">
            <div className="pg-endpoint-head">
              <span>内容生成接口</span>
              <p>用于大纲、文案、页面规划。</p>
            </div>
            <div className="pg-advanced-grid">
              <label className="pg-auth-field pg-field-wide">
                <span>API URL</span>
                <input
                  className="pg-auth-input"
                  value={value.minimaxApiBase}
                  onChange={(e) => update("minimaxApiBase", e.target.value)}
                  placeholder="https://api.minimaxi.com/v1"
                />
              </label>
              <label className="pg-auth-field">
                <span>模型名称</span>
                <input
                  className="pg-auth-input"
                  value={value.minimaxLlmModel}
                  onChange={(e) => update("minimaxLlmModel", e.target.value)}
                  placeholder="MiniMax-M3"
                />
              </label>
            </div>
          </section>

          <section className="pg-endpoint-group">
            <div className="pg-endpoint-head">
              <span>图片生成接口</span>
              <p>用于封面、配图和视觉素材。</p>
            </div>
            <div className="pg-advanced-grid">
              <label className="pg-auth-field pg-field-wide">
                <span>API URL</span>
                <input
                  className="pg-auth-input"
                  value={value.deerApiBase}
                  onChange={(e) => update("deerApiBase", e.target.value)}
                  placeholder="https://api.deerapi.com/v1"
                />
              </label>
              <label className="pg-auth-field">
                <span>模型名称</span>
                <input
                  className="pg-auth-input"
                  value={value.deerImageModel}
                  onChange={(e) => update("deerImageModel", e.target.value)}
                  placeholder="gpt-image-2-all"
                />
              </label>
            </div>
          </section>

          <div className="pg-provider-footnote">
            只要你没有改用其他平台，这里保持默认即可。更换平台时，请确认 API URL、模型名称和 Key 来自同一个平台。
          </div>
        </div>
      )}
    </div>
  );
}

function RestoreDefaultsButton({
  provider,
  onRestore,
}: {
  provider: ProviderSettings;
  onRestore: (next: ProviderSettings) => void;
}) {
  return (
    <button
      type="button"
      className="pg-action pg-action-secondary rounded-md bg-slate-100 px-4 py-2 text-sm text-slate-700 hover:bg-slate-200"
      onClick={() =>
        onRestore({
          ...DEFAULT_PROVIDER_SETTINGS,
          minimaxApiKey: provider.minimaxApiKey,
          deerApiKey: provider.deerApiKey,
        })
      }
    >
      恢复接口默认
    </button>
  );
}

export default function AuthGate() {
  const [auth, setAuth] = useState<MvpAuth | null>(() => getInitialAuth());
  const [displayName, setDisplayName] = useState("");
  const [provider, setProvider] = useState<ProviderSettings>(() => getProviderSettings());
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const canEnter = useMemo(() => {
    if (SERVER_MANAGED_PROVIDERS) return Boolean(displayName.trim());
    return Boolean(
      displayName.trim() &&
      provider.minimaxApiBase.trim() &&
      provider.minimaxApiKey.trim() &&
      provider.minimaxLlmModel.trim() &&
      provider.deerApiBase.trim() &&
      provider.deerApiKey.trim() &&
      provider.deerImageModel.trim(),
    );
  }, [displayName, provider]);

  const submit = async () => {
    setError("");
    setBusy(true);
    try {
      saveProviderSettings(provider);
      const nextAuth = await testerLogin(displayName);
      saveStoredAuth(nextAuth);
      setAuth(nextAuth);
    } catch (e: any) {
      setError(e?.message || "登录失败");
    } finally {
      setBusy(false);
    }
  };

  const logout = () => {
    clearStoredAuth();
    setUserMenuOpen(false);
    setAuth(null);
  };

  const copyFeedback = async () => {
    await navigator.clipboard.writeText(feedbackTemplate(auth));
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  useEffect(() => {
    if (!auth) return;
    let cancelled = false;
    fetchAuthMe()
      .then((serverAuth) => {
        if (cancelled) return;
        const nextAuth = {
          testerId: serverAuth.testerId,
          displayName: serverAuth.displayName || auth.displayName,
        };
        if (nextAuth.testerId !== auth.testerId || nextAuth.displayName !== auth.displayName) {
          saveStoredAuth(nextAuth);
          setAuth(nextAuth);
        }
      })
      .catch(() => {
        if (cancelled) return;
        clearStoredAuth();
        setAuth(null);
        setError("登录状态已失效，请重新输入固定用户名。");
      });
    return () => {
      cancelled = true;
    };
  }, [auth]);

  const handleLoginSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canEnter || busy) return;
    void submit();
  };

  if (auth) {
    return (
      <>
        <App key={auth.testerId} />
        <div className="pg-user-menu fixed bottom-3 left-3 z-[60] text-xs text-slate-600">
          {userMenuOpen && (
            <div className="pg-user-menu-card mb-2 w-[260px] rounded-lg border border-slate-200 bg-white p-3 shadow-xl">
              <div className="mb-2 flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold text-slate-900">{auth.displayName}</div>
                  <div className="text-[11px] text-slate-400">测试空间</div>
                </div>
                <button className="pg-action pg-action-secondary rounded px-2 py-1 text-slate-400 hover:bg-slate-100" onClick={() => setUserMenuOpen(false)}>
                  收起
                </button>
              </div>
              <div className="space-y-1 rounded-md bg-slate-50 p-2 text-[11px] leading-5 text-slate-500">
                {SERVER_MANAGED_PROVIDERS ? (
                  <div className="text-slate-500">使用服务器端模型额度</div>
                ) : (
                  <>
                    <div className="truncate">文本模型 {providerModelLabel(provider.minimaxApiBase, provider.minimaxLlmModel, "文本接口")}</div>
                    <div className="truncate">图片模型 {providerModelLabel(provider.deerApiBase, provider.deerImageModel, "图片接口")}</div>
                    <div className="text-slate-400">Key 只在设置里维护</div>
                  </>
                )}
              </div>
              <div className="mt-3 grid grid-cols-3 gap-2">
                <button className="pg-action pg-action-secondary rounded bg-slate-100 px-2 py-1.5 hover:bg-slate-200" onClick={() => setSettingsOpen(true)}>
                  设置
                </button>
                <button className="pg-action pg-action-secondary rounded bg-slate-100 px-2 py-1.5 hover:bg-slate-200" onClick={copyFeedback}>
                  {copied ? "已复制" : "反馈"}
                </button>
                <button className="pg-action pg-action-secondary rounded bg-slate-100 px-2 py-1.5 hover:bg-slate-200" onClick={logout}>
                  退出
                </button>
              </div>
            </div>
          )}
          <button
            className="pg-user-trigger flex max-w-[190px] items-center gap-2 rounded-full border border-slate-200 bg-white/95 px-3 py-1.5 shadow-lg backdrop-blur hover:bg-slate-50"
            onClick={() => setUserMenuOpen((v) => !v)}
            title="测试用户设置"
          >
            <span className="h-2 w-2 rounded-full bg-emerald-500" />
            <span className="truncate font-medium text-slate-800">{auth.displayName}</span>
          </button>
        </div>
        {settingsOpen && (
          <div className="pg-settings-backdrop">
            <div className="pg-settings-modal">
              <div className="mb-4 flex items-start justify-between gap-4">
                <div>
                  <h2 className="text-lg font-semibold text-slate-900">测试设置</h2>
                  <p className="mt-1 text-sm text-slate-500">
                    {SERVER_MANAGED_PROVIDERS
                      ? "当前使用服务器端模型配置。"
                      : "这些 Key 只保存在当前浏览器，并随请求发给后端用于本次生成。"}
                  </p>
                </div>
                <button className="pg-action pg-action-secondary rounded-md px-2 py-1 text-slate-500 hover:bg-slate-100" onClick={() => setSettingsOpen(false)}>
                  关闭
                </button>
              </div>
              {SERVER_MANAGED_PROVIDERS ? (
                <div className="rounded-md bg-slate-50 p-4 text-sm text-slate-600">
                  这次线上测试先不要求朋友填写 API Key。后续接 credits 时，会在这里显示余额和用量。
                </div>
              ) : (
                <ProviderSetup value={provider} onChange={setProvider} defaultAdvancedOpen />
              )}
              <div className="mt-5 flex justify-end gap-2">
                {!SERVER_MANAGED_PROVIDERS && <RestoreDefaultsButton provider={provider} onRestore={setProvider} />}
                <button
                  type="button"
                  className="pg-action pg-action-primary rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
                  onClick={() => {
                    saveProviderSettings(provider);
                    setSettingsOpen(false);
                  }}
                >
                  保存
                </button>
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
          <p className="pg-auth-lead">
            从真实资料出发，先整理叙事逻辑，再完成页面设计与视觉生成；导出前，你可以在工作台里持续调整结构、内容和风格。
          </p>
          <div className="pg-auth-value">
            <p>
              它不是简单套模板，而是把内容策划、视觉方向和整套 PPT 生成串成一个完整流程，帮助你更稳定地做出可交付的演示稿。
            </p>
          </div>
        </section>

        <section className="pg-auth-card pg-auth-card-v2" aria-label="登录到 PPT God">
          <div className="pg-auth-card-head">
            <h2>进入 PPT God</h2>
            {SERVER_MANAGED_PROVIDERS ? (
              <p>目前还是过渡测试阶段，只需要填写一个固定用户名。用户名用来识别你的测试空间。</p>
            ) : (
              <p>
                目前还是过渡测试阶段，所以需要你填写一个固定用户名和两枚 API Key。用户名用来识别你的测试空间；
                API Key 用来调用内容和图片模型。
              </p>
            )}
          </div>

          <form className="pg-login-form" onSubmit={handleLoginSubmit}>
            <label className="pg-auth-field">
              <span>固定用户名</span>
              <em>只用于识别你的项目记录。每次用同一个用户名登录，才能看到之前生成的资料；换用户名就会进入另一个空间。</em>
              <input
                className="pg-auth-input"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="输入一个你会持续使用的用户名"
                autoComplete="name"
              />
            </label>

            {!SERVER_MANAGED_PROVIDERS && <ProviderSetup value={provider} onChange={setProvider} />}

            {error && <div className="pg-auth-error">{error}</div>}
            <button disabled={!canEnter || busy} className="pg-primary-button pg-login-submit">
              {busy ? "正在进入..." : "进入 PPT God"}
            </button>
            <div className="pg-auth-assurance">
              {SERVER_MANAGED_PROVIDERS
                ? "模型调用使用服务器端测试额度。"
                : "你的 Key 只保存在当前浏览器，并随请求用于本次生成。公共电脑上请不要保存 Key。"}
            </div>
          </form>
        </section>
      </main>
    </div>
  );
}
