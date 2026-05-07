import { useMemo, useState } from "react";
import App from "./App";
import {
  API_BASE,
  DEFAULT_PROVIDER_SETTINGS,
  clearStoredAuth,
  getProviderSettings,
  getStoredAuth,
  saveProviderSettings,
  saveStoredAuth,
  testerLogin,
  type MvpAuth,
  type ProviderSettings,
} from "./api/client";
import PptGodLogo from "./components/PptGodLogo";

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

function ProviderFields({
  value,
  onChange,
}: {
  value: ProviderSettings;
  onChange: (next: ProviderSettings) => void;
}) {
  const [presetNotice, setPresetNotice] = useState("");
  const update = (key: keyof ProviderSettings, nextValue: string) => {
    setPresetNotice("");
    onChange({ ...value, [key]: nextValue });
  };
  const applyPreset = (preset: "deer-image" | "minimax-deer") => {
    if (preset === "deer-image") {
      onChange({
        ...value,
        deerApiBase: DEFAULT_PROVIDER_SETTINGS.deerApiBase,
        deerImageModel: DEFAULT_PROVIDER_SETTINGS.deerImageModel,
      });
      setPresetNotice("已套用图片接口：Deer API + gpt-image-2-all。API Key 保持不变。");
      return;
    }
    if (preset === "minimax-deer") {
      onChange({
        ...value,
        minimaxApiBase: "https://api.minimaxi.com/v1",
        minimaxLlmModel: "MiniMax-M2.7",
        deerApiBase: "https://api.deerapi.com/v1",
        deerImageModel: "gpt-image-2-all",
      });
      setPresetNotice("已套用示例组合：MiniMax 文本 + Deer 生图。API Key 保持不变。");
    }
  };
  const inputClass = "pg-input w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100";
  const labelClass = "text-xs font-semibold text-slate-600";
  const presetButtonClass = "pg-action pg-action-secondary rounded-md border border-slate-300 bg-white px-3 py-2 text-xs font-medium text-slate-700 hover:border-blue-300 hover:bg-blue-50 hover:text-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-200";

  return (
    <div className="grid gap-4">
      <div className="pg-provider-note rounded-md bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-600">
        兼容 OpenAI-style 接口：填入 Base URL、API Key 和模型名即可。默认图片接口使用 Deer API + gpt-image-2-all；GRSAI、OpenRouter、各种中转站只要接口兼容也可以填在这里。
      </div>
      <div className="grid gap-2">
        <div className="text-xs font-semibold text-slate-600">快速填充</div>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className={presetButtonClass} onClick={() => applyPreset("deer-image")}>
            图片接口：Deer API 默认
          </button>
          <button type="button" className={presetButtonClass} onClick={() => applyPreset("minimax-deer")}>
            示例组合：MiniMax 文本 + Deer 图片
          </button>
          <span className="text-xs text-slate-500">自定义平台请直接修改下面的 URL / Key / 模型名。</span>
        </div>
        {presetNotice && <div className="pg-provider-note rounded-md bg-blue-50 px-3 py-2 text-xs text-blue-700">{presetNotice}</div>}
      </div>
      <div className="grid gap-2">
        <div className="flex items-center justify-between gap-3">
          <div className={labelClass}>文本/规划接口 API Key</div>
          <span className="text-xs text-slate-400">例如 MiniMax / GRSAI / OpenRouter</span>
        </div>
        <input
          className={inputClass}
          value={value.minimaxApiKey}
          onChange={(e) => update("minimaxApiKey", e.target.value)}
          placeholder="填文本模型 API Key，例如 MiniMax / GRSAI / OpenRouter"
          type="password"
          autoComplete="off"
        />
      </div>
      <div className="grid grid-cols-1 md:grid-cols-[1fr_180px] gap-3">
        <div className="grid gap-2">
          <div className={labelClass}>文本/规划接口 Base URL</div>
          <input className={inputClass} value={value.minimaxApiBase} onChange={(e) => update("minimaxApiBase", e.target.value)} />
        </div>
        <div className="grid gap-2">
          <div className={labelClass}>文本模型</div>
          <input className={inputClass} value={value.minimaxLlmModel} onChange={(e) => update("minimaxLlmModel", e.target.value)} />
        </div>
      </div>

      <div className="h-px bg-slate-200" />

      <div className="grid gap-2">
        <div className="flex items-center justify-between gap-3">
          <div className={labelClass}>图片接口 API Key</div>
          <span className="text-xs text-slate-400">默认 Deer API</span>
        </div>
        <input
          className={inputClass}
          value={value.deerApiKey}
          onChange={(e) => update("deerApiKey", e.target.value)}
          placeholder="填图片模型 API Key，例如 Deer API / GRSAI / 兼容图片接口"
          type="password"
          autoComplete="off"
        />
      </div>
      <div className="grid grid-cols-1 md:grid-cols-[1fr_180px] gap-3">
        <div className="grid gap-2">
          <div className={labelClass}>图片接口 Base URL</div>
          <input className={inputClass} value={value.deerApiBase} onChange={(e) => update("deerApiBase", e.target.value)} />
        </div>
        <div className="grid gap-2">
          <div className={labelClass}>图片模型</div>
          <input
            className={inputClass}
            value={value.deerImageModel}
            onChange={(e) => update("deerImageModel", e.target.value)}
            placeholder="例如：gpt-image-2-all"
          />
        </div>
      </div>
      <div className="pg-provider-note pg-provider-warning rounded-md bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800">
        图片模型名会原样传给接口。默认使用 Deer API 的 gpt-image-2-all；如果同事换平台，要以对方平台后台/文档显示的模型 ID 为准，避免填到高价模型。
      </div>
    </div>
  );
}

export default function AuthGate() {
  const [auth, setAuth] = useState<MvpAuth | null>(() => getStoredAuth());
  const [displayName, setDisplayName] = useState("");
  const [provider, setProvider] = useState<ProviderSettings>(() => getProviderSettings());
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const canEnter = useMemo(() => {
    return (
      displayName.trim() &&
      provider.minimaxApiKey.trim() &&
      provider.minimaxLlmModel.trim() &&
      provider.deerApiKey.trim() &&
      provider.deerImageModel.trim()
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
                <div className="truncate">文本模型 {providerModelLabel(provider.minimaxApiBase, provider.minimaxLlmModel, "文本接口")}</div>
                <div className="truncate">图片模型 {providerModelLabel(provider.deerApiBase, provider.deerImageModel, "图片接口")}</div>
                <div className="text-slate-400">Key 只在设置里维护</div>
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
          <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40 px-4">
            <div className="pg-modal-card w-full max-w-2xl rounded-lg bg-white p-6 shadow-2xl">
              <div className="mb-4 flex items-start justify-between gap-4">
                <div>
                  <h2 className="text-lg font-semibold text-slate-900">测试设置</h2>
                  <p className="mt-1 text-sm text-slate-500">这些 Key 只保存在当前浏览器，并随请求发给后端用于本次生成。</p>
                </div>
                <button className="pg-action pg-action-secondary rounded-md px-2 py-1 text-slate-500 hover:bg-slate-100" onClick={() => setSettingsOpen(false)}>
                  关闭
                </button>
              </div>
              <ProviderFields value={provider} onChange={setProvider} />
              <div className="mt-5 flex justify-end gap-2">
                <button
                  className="pg-action pg-action-secondary rounded-md bg-slate-100 px-4 py-2 text-sm text-slate-700 hover:bg-slate-200"
                  onClick={() =>
                    setProvider({
                      ...DEFAULT_PROVIDER_SETTINGS,
                      minimaxApiKey: provider.minimaxApiKey,
                      deerApiKey: provider.deerApiKey,
                    })
                  }
                >
                  恢复接口默认
                </button>
                <button
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
    <div className="pg-auth min-h-screen w-screen overflow-auto bg-slate-950 text-slate-900">
      <div className="mx-auto grid min-h-screen max-w-6xl grid-cols-1 items-center gap-8 px-6 py-8 lg:grid-cols-[420px_1fr]">
        <section className="pg-auth-copy text-white">
          <div className="pg-auth-brand text-sm font-semibold uppercase tracking-[0.18em] text-blue-300">
            <PptGodLogo subtitle="古希腊掌管 PPT 的神" />
          </div>
          <h1 className="mt-4 text-4xl font-bold leading-tight">进入专业创作工作台</h1>
          <p className="mt-4 text-base leading-7 text-slate-300">
            用一个稳定的用户名登录。下次关闭标签页再回来，浏览器会自动回到你的项目；换设备时输入同样的用户名也能找回。
          </p>
          <div className="pg-auth-note mt-6 rounded-lg border border-white/10 bg-white/5 p-4 text-sm leading-6 text-slate-300">
            <div className="font-semibold text-white">注意事项</div>
            <p className="mt-2">API Key 会保存在当前浏览器，并通过 HTTPS 发给后端调用你填写的平台。不要在公共电脑保存 Key。</p>
            <p className="mt-2">模型调用费用走你自己的 API 账号；服务器只负责项目保存、排队和文件生成。</p>
            <p className="mt-2">遇到问题先截图，再复制反馈模板发给 Joe。</p>
          </div>
        </section>

        <section className="pg-auth-card rounded-lg bg-white p-6 shadow-2xl">
          <div className="mb-5">
            <h2 className="text-xl font-semibold">登录信息</h2>
            <p className="mt-1 text-sm text-slate-500">开放测试阶段只用用户名区分项目空间，请使用一个不会和同事撞名的名字。</p>
          </div>
          <div className="grid gap-4">
            <div className="grid gap-2">
              <label className="text-xs font-semibold text-slate-600">用户名</label>
              <input
                className="pg-input w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="例如：张三-市场部"
              />
            </div>
          </div>

          <div className="my-6 h-px bg-slate-200" />
          <ProviderFields value={provider} onChange={setProvider} />
          {error && <div className="mt-4 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
          <button
            disabled={!canEnter || busy}
            onClick={submit}
            className="pg-primary-button mt-6 w-full rounded-md bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? "正在进入..." : "进入 PPT God"}
          </button>
        </section>
      </div>
    </div>
  );
}
