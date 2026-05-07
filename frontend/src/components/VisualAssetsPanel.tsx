import { useState } from "react";

export interface ReferenceImage {
  id: string;
  role: "logo" | "style_ref" | "template" | "visual_asset" | "content_ref" | "chart_ref";
  url: string;
  overlay_url?: string | null;
  page_num?: number | null;
  process_mode?: "blend" | "crop" | "original";
  asset_name?: string | null;
  asset_kind?: "product" | "person" | "scene" | "material" | "other" | null;
  usage_note?: string | null;
  asset_analysis?: any;
  source_document?: string | null;
  source_page_num?: number | null;
  tags?: string[];
  logo_anchor?: "top-left" | "top-right" | "bottom-left" | "bottom-right" | null;
}

const OVERLAY_PRESETS = [
  { value: "right-card", label: "右侧卡片" },
  { value: "left-card", label: "左侧卡片" },
  { value: "center-card", label: "中心卡片" },
  { value: "top-right-small", label: "右上小图" },
  { value: "bottom-right-small", label: "右下小图" },
  { value: "bottom-band", label: "底部横条" },
];

interface VisualAssetsPanelProps {
  referenceImages: ReferenceImage[];
  activeSlide?: {
    id: string;
    page_num: number;
    type?: string;
    content_json?: any;
    visual_json?: any;
  } | null;
  templateRecommendations?: any | null;
  templatePages?: any[];
  onDelete: (refId: string) => void;
  onImageClick: (url: string) => void;
  apiBase: string;
  onUploadLogo?: () => void;
  onUploadStyleRef?: () => void;
  onUploadTemplate?: () => void;
  onUploadVisualAsset?: () => void;
  onUpdateVisualAsset?: (refId: string, data: {
    asset_name?: string;
    asset_kind?: string;
    usage_note?: string;
    process_mode?: string;
    logo_anchor?: string;
  }) => Promise<void> | void;
  onPinAsset?: (slideId: string, assetId: string) => Promise<void> | void;
  onUnpinAsset?: (slideId: string, assetId: string) => Promise<void> | void;
  onUpdateOverlayLayers?: (slideId: string, layers: any[]) => Promise<void> | void;
  showInVisualStage?: boolean;
}

function getImageUrl(apiBase: string, url: string) {
  return url.startsWith("http") ? url : `${apiBase}${url}`;
}

function AssetCard({
  label,
  children,
  onDelete,
}: {
  label: string;
  children: React.ReactNode;
  onDelete: () => void;
}) {
  return (
    <div className="pg-asset-card relative group bg-white rounded-lg border border-gray-200 p-2 flex flex-col items-center gap-1.5 w-[140px] h-[140px]">
      {label && (
        <span className="text-2xs text-gray-500 font-medium leading-none h-3 flex items-center">
          {label}
        </span>
      )}
      <div className={`flex items-center justify-center overflow-hidden ${label ? "flex-1 w-full" : "w-full h-full"}`}>
        {children}
      </div>
      <button
        onClick={onDelete}
        className="pg-danger-icon-button absolute top-1 right-1 w-5 h-5 bg-red-500 text-white rounded-full text-2xs flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity shadow-sm hover:bg-red-600 z-10"
        title="删除"
      >
        X
      </button>
    </div>
  );
}

function UploadChoice({
  label,
  kicker,
  status,
  accepts,
  outcome,
  actionLabel,
  onClick,
}: {
  label: string;
  kicker: string;
  status: string;
  accepts: string;
  outcome: string;
  actionLabel: string;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!onClick}
      className="pg-upload-choice text-left"
      title={`${label}：${outcome}`}
    >
      <div className="pg-upload-choice-head">
        <span className="pg-upload-choice-kicker">{kicker}</span>
        <span className="pg-upload-choice-status">{status}</span>
      </div>
      <div className="pg-upload-choice-title">{label}</div>
      <div className="pg-upload-choice-accepts">{accepts}</div>
      <div className="pg-upload-choice-outcome">{outcome}</div>
      <div className="pg-upload-choice-action">{actionLabel}</div>
    </button>
  );
}

export default function VisualAssetsPanel({
  referenceImages,
  activeSlide,
  templateRecommendations,
  templatePages,
  onDelete,
  onImageClick,
  apiBase,
  onUploadLogo,
  onUploadStyleRef,
  onUploadTemplate,
  onUploadVisualAsset,
  onUpdateVisualAsset,
  onPinAsset,
  onUnpinAsset,
  onUpdateOverlayLayers,
  showInVisualStage = false,
}: VisualAssetsPanelProps) {
  const [showTemplatePages, setShowTemplatePages] = useState(false);
  const [editingAssetId, setEditingAssetId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [kindFilter, setKindFilter] = useState("all");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [modeFilter, setModeFilter] = useState("all");
  const [assetDraft, setAssetDraft] = useState({
    asset_name: "",
    asset_kind: "other",
    usage_note: "",
    process_mode: "blend",
  });

  const logos = referenceImages.filter((r) => r.role === "logo");
  const styleRefs = referenceImages.filter((r) => r.role === "style_ref");
  const template = referenceImages.find((r) => r.role === "template");
  const visualAssets = referenceImages.filter((r) => r.role === "visual_asset");
  const manualPinnedIds: string[] = Array.isArray(activeSlide?.visual_json?.manual_visual_asset_ids)
    ? activeSlide!.visual_json.manual_visual_asset_ids.map(String)
    : [];
  const overlayLayers: any[] = Array.isArray(activeSlide?.visual_json?.overlay_layers)
    ? activeSlide!.visual_json.overlay_layers
    : [];
  const enabledOverlayLayers = overlayLayers.filter((layer) => layer?.enabled !== false);
  const selectedIds: string[] = Array.isArray(activeSlide?.visual_json?.visual_asset_ids)
    ? activeSlide!.visual_json.visual_asset_ids.map(String)
    : [];
  const activeText = [
    activeSlide?.content_json?.text_content?.headline,
    activeSlide?.content_json?.text_content?.subhead,
    typeof activeSlide?.content_json?.text_content?.body === "string"
      ? activeSlide?.content_json?.text_content?.body
      : Array.isArray(activeSlide?.content_json?.text_content?.body)
      ? activeSlide?.content_json?.text_content?.body.join(" ")
      : "",
    activeSlide?.visual_json?.visual_description,
  ].filter(Boolean).join(" ").toLowerCase();

  const shouldShow = showInVisualStage || referenceImages.length > 0;
  if (!shouldShow) return null;

  const anchorLabel: Record<string, string> = {
    "top-left": "左上",
    "top-right": "右上",
    "bottom-left": "左下",
    "bottom-right": "右下",
  };
  const updateLogoAnchor = async (anchor: string) => {
    for (const item of logos) {
      await onUpdateVisualAsset?.(item.id, { logo_anchor: anchor });
    }
  };
  const kindLabel: Record<string, string> = {
    product: "产品",
    person: "人物",
    scene: "场景",
    material: "物料",
    other: "其他",
  };
  const sourceDocuments = Array.from(new Set(
    visualAssets
      .map((asset) => asset.source_document || asset.asset_analysis?.source_document)
      .filter(Boolean)
  )).sort();
  const normalizedQuery = query.trim().toLowerCase();

  const assetSearchText = (asset: ReferenceImage) => [
    asset.asset_name,
    asset.asset_kind,
    asset.process_mode,
    asset.usage_note,
    asset.source_document,
    asset.source_page_num,
    asset.asset_analysis?.source_document,
    asset.asset_analysis?.pptx_source_page_num,
    asset.asset_analysis?.source_slide_text,
    ...(Array.isArray(asset.asset_analysis?.asset_tags) ? asset.asset_analysis.asset_tags : []),
    ...(Array.isArray(asset.asset_analysis?.suggested_keywords) ? asset.asset_analysis.suggested_keywords : []),
  ].filter(Boolean).join(" ").toLowerCase();

  const relevanceScore = (asset: ReferenceImage) => {
    if (!activeSlide) return 0;
    const text = assetSearchText(asset);
    let score = selectedIds.includes(asset.id) ? 10 : 0;
    for (const token of activeText.split(/[\s,，。；;、|/]+/).filter((x) => x.length >= 2)) {
      if (text.includes(token)) score += 1;
    }
    return score;
  };

  const filteredVisualAssets = visualAssets.filter((asset) => {
    const text = assetSearchText(asset);
    if (normalizedQuery && !text.includes(normalizedQuery)) return false;
    if (kindFilter !== "all" && (asset.asset_kind || "other") !== kindFilter) return false;
    if (modeFilter !== "all" && (asset.process_mode || "blend") !== modeFilter) return false;
    const source = asset.source_document || asset.asset_analysis?.source_document || "";
    if (sourceFilter !== "all" && source !== sourceFilter) return false;
    return true;
  });
  const pinnedAssets = activeSlide
    ? manualPinnedIds
        .map((id) => visualAssets.find((asset) => asset.id === id))
        .filter(Boolean) as ReferenceImage[]
    : [];
  const recommendedAssets = activeSlide
    ? filteredVisualAssets
        .filter((asset) => !manualPinnedIds.includes(asset.id) && relevanceScore(asset) > 0)
        .sort((a, b) => relevanceScore(b) - relevanceScore(a))
        .slice(0, 12)
    : [];
  const allLibraryAssets = filteredVisualAssets.filter(
    (asset) => !manualPinnedIds.includes(asset.id) && !recommendedAssets.some((rec) => rec.id === asset.id)
  );
  const uploadChoices = [
    {
      label: "品牌 Logo",
      kicker: "全局标识",
      status: logos.length ? `${logos.length} 个` : "可选",
      accepts: "PNG / JPG / SVG",
      outcome: "上传后自动去底裁边；多 Logo 会组成固定联合标识。",
      actionLabel: logos.length ? "添加 Logo" : "上传 Logo",
      onClick: onUploadLogo,
    },
    {
      label: "核心资产",
      kicker: "页面画面素材",
      status: visualAssets.length ? `${visualAssets.length} 个` : "建议上传",
      accepts: "产品 / 主 KV / 人物 / 场景",
      outcome: "上传后进入素材库，系统按每页文案推荐，也可手动锁定到本页。",
      actionLabel: "上传核心资产",
      onClick: onUploadVisualAsset,
    },
    {
      label: "风格参考",
      kicker: "视觉气质",
      status: styleRefs.length ? `${styleRefs.length} 张` : "可选",
      accepts: "截图 / 海报 / 参考页",
      outcome: "只提取配色、材质和构图节奏，不会强制出现在页面里。",
      actionLabel: "上传风格参考",
      onClick: onUploadStyleRef,
    },
    {
      label: "版式模板",
      kicker: "页面结构",
      status: template ? "已上传" : "可选",
      accepts: "PPT / PDF",
      outcome: "抽取封面、目录、内容、封底等页面秩序，作为布局参考。",
      actionLabel: template ? "更换模板" : "上传模板",
      onClick: onUploadTemplate,
    },
  ];

  const renderVisualAssetCard = (asset: ReferenceImage) => {
    const name = asset.asset_name || asset.asset_analysis?.subject || "未命名资产";
    const isEditing = editingAssetId === asset.id;

    if (isEditing) {
      return (
        <div key={asset.id} className="pg-asset-editor-card flex-shrink-0 bg-white rounded-lg border border-purple-200 p-3 w-[280px]">
          <div className="relative h-24 bg-gray-50 rounded overflow-hidden mb-2">
            <img
              src={getImageUrl(apiBase, asset.url)}
              alt={name}
              className="h-full w-full object-contain"
              onClick={() => onImageClick(getImageUrl(apiBase, asset.url))}
              onError={(e) => {
                (e.target as HTMLImageElement).style.display = "none";
              }}
            />
            <button
              onClick={() => onDelete(asset.id)}
              className="pg-danger-icon-button absolute top-1 right-1 w-5 h-5 bg-red-500 text-white rounded-full text-2xs flex items-center justify-center shadow-sm hover:bg-red-600 z-10"
              title="删除"
            >
              X
            </button>
          </div>
          <div className="space-y-1.5">
            <input
              value={assetDraft.asset_name}
              onChange={(e) => setAssetDraft((prev) => ({ ...prev, asset_name: e.target.value }))}
              placeholder="资产名称"
              className="w-full text-2xs border border-gray-200 rounded px-1.5 py-1"
            />
            <div className="grid grid-cols-2 gap-1">
              <select
                value={assetDraft.asset_kind}
                onChange={(e) => setAssetDraft((prev) => ({ ...prev, asset_kind: e.target.value }))}
                className="text-2xs border border-gray-200 rounded px-1 py-1 bg-white"
              >
                <option value="product">产品</option>
                <option value="person">人物</option>
                <option value="scene">场景</option>
                <option value="material">物料</option>
                <option value="other">其他</option>
              </select>
              <select
                value={assetDraft.process_mode}
                onChange={(e) => setAssetDraft((prev) => ({ ...prev, process_mode: e.target.value }))}
                className="text-2xs border border-gray-200 rounded px-1 py-1 bg-white"
              >
                <option value="blend">融合</option>
                <option value="crop">身份保真</option>
                <option value="original">原图</option>
              </select>
            </div>
            <textarea
              value={assetDraft.usage_note}
              onChange={(e) => setAssetDraft((prev) => ({ ...prev, usage_note: e.target.value }))}
              placeholder="什么时候使用它"
              rows={2}
              className="w-full text-2xs border border-gray-200 rounded px-1.5 py-1 resize-none"
            />
            <div className="flex gap-1">
              <button
                className="pg-action pg-action-primary flex-1 text-2xs bg-purple-600 text-white rounded px-2 py-1 hover:bg-purple-700"
                onClick={async () => {
                  await onUpdateVisualAsset?.(asset.id, assetDraft);
                  setEditingAssetId(null);
                }}
              >
                保存
              </button>
              <button
                className="pg-action pg-action-secondary flex-1 text-2xs bg-gray-100 text-gray-600 rounded px-2 py-1 hover:bg-gray-200"
                onClick={() => setEditingAssetId(null)}
              >
                取消
              </button>
            </div>
          </div>
        </div>
      );
    }

    const source = asset.source_document || asset.asset_analysis?.source_document;
    const pageNum = asset.source_page_num || asset.asset_analysis?.pptx_source_page_num;
    const isPinned = manualPinnedIds.includes(asset.id);
    const overlayLayer = overlayLayers.find((layer) => String(layer?.asset_id) === asset.id && layer?.enabled !== false);
    const isExact = !!overlayLayer;
    const updateExactLayer = async (enabled: boolean, preset?: string) => {
      if (!activeSlide || !onUpdateOverlayLayers) return;
      const current = overlayLayers.filter((layer) => String(layer?.asset_id) !== asset.id);
      const next = enabled
        ? [
            ...current,
            {
              id: overlayLayer?.id || `ov_${asset.id}`,
              asset_id: asset.id,
              enabled: true,
              preset: preset || overlayLayer?.preset || "right-card",
              fit: "contain",
              mode: overlayLayer?.mode || "exact_card",
              usage_note: overlayLayer?.usage_note || asset.usage_note || "",
            },
          ]
        : current;
      await onUpdateOverlayLayers(activeSlide.id, next);
    };
    return (
      <div key={asset.id} className="flex flex-col items-center gap-1">
        <AssetCard label="" onDelete={() => onDelete(asset.id)}>
          <img
            src={getImageUrl(apiBase, asset.url)}
            alt={name}
            className="h-full w-full object-contain cursor-pointer"
            onClick={() => onImageClick(getImageUrl(apiBase, asset.url))}
            onError={(e) => {
              (e.target as HTMLImageElement).style.display = "none";
            }}
          />
        </AssetCard>
        <div className="text-2xs text-gray-600 text-center max-w-[140px] truncate" title={name}>
          {name}
        </div>
        <div className="text-2xs text-gray-400 text-center">
          {kindLabel[asset.asset_kind || "other"]} · {asset.process_mode || "blend"}
        </div>
        {(source || pageNum) && (
          <div className="text-2xs text-gray-400 text-center max-w-[140px] truncate" title={`${source || ""}${pageNum ? ` 第${pageNum}页` : ""}`}>
            {pageNum ? `P${pageNum}` : "来源"} {source || ""}
          </div>
        )}
        {asset.usage_note && (
          <div className="text-2xs text-gray-400 text-center max-w-[140px] truncate" title={asset.usage_note}>
            {asset.usage_note}
          </div>
        )}
        {onUpdateVisualAsset && (
          <div className="flex flex-col items-center gap-1">
            <div className="flex items-center gap-1">
            {activeSlide && (isPinned ? (
              <button
                className="pg-subtle-link text-2xs text-emerald-700 hover:text-emerald-800"
                onClick={() => onUnpinAsset?.(activeSlide.id, asset.id)}
                title="取消锁定到当前页"
              >
                已锁定
              </button>
            ) : (
              <button
                className="pg-subtle-link text-2xs text-blue-600 hover:text-blue-700"
                onClick={() => onPinAsset?.(activeSlide.id, asset.id)}
                title="锁定到当前页"
              >
                Pin 到本页
              </button>
            ))}
            <button
              className="pg-subtle-link text-2xs text-purple-600 hover:text-purple-700"
              onClick={() => {
                setEditingAssetId(asset.id);
                setAssetDraft({
                  asset_name: asset.asset_name || "",
                  asset_kind: asset.asset_kind || "other",
                  usage_note: asset.usage_note || "",
                  process_mode: asset.process_mode || "blend",
                });
              }}
            >
              编辑
            </button>
            </div>
            {activeSlide && isPinned && onUpdateOverlayLayers && (
              <div className="flex flex-wrap justify-center gap-1 max-w-[150px]">
                <button
                  className={`pg-exact-toggle text-2xs rounded-full border px-2 py-0.5 ${
                    isExact
                      ? "bg-amber-500 text-white border-amber-500"
                      : "bg-white text-gray-500 border-gray-200 hover:border-amber-300"
                  }`}
                  onClick={() => updateExactLayer(!isExact)}
                  title="Exact Overlay 会在预览和 PPTX 中原样叠加这张素材"
                >
                  {isExact ? "Exact 已开" : "Exact"}
                </button>
                {isExact && (
                  <select
                    value={overlayLayer?.preset || "right-card"}
                    onChange={(e) => updateExactLayer(true, e.target.value)}
                    className="text-2xs border border-amber-200 rounded-full px-1 py-0.5 bg-white text-amber-800"
                    title="选择叠加位置"
                  >
                    {OVERLAY_PRESETS.map((preset) => (
                      <option key={preset.value} value={preset.value}>{preset.label}</option>
                    ))}
                  </select>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    );
  };

  const renderAssetSection = (title: string, assets: ReferenceImage[], emptyText: string, helper?: string) => (
    <div className="pg-asset-section">
      <div className="pg-asset-section-head">
        <span>{title}</span>
        <span>{assets.length}</span>
      </div>
      {helper && <div className="pg-asset-section-helper">{helper}</div>}
      {assets.length > 0 ? (
        <div className="flex flex-wrap items-start gap-3 pb-1">{assets.map(renderVisualAssetCard)}</div>
      ) : (
        <div className="pg-asset-empty">
          {emptyText}
        </div>
      )}
    </div>
  );

  return (
    <div className="pg-visual-assets-panel bg-gray-50 border-b border-gray-200 px-3 py-2">
      <div className="pg-assets-onboarding">
        <div className="pg-assets-onboarding-copy">
          <div className="pg-assets-onboarding-title">
            {showInVisualStage ? "先上传素材，再生成视觉方案" : "管理项目素材与参考"}
          </div>
          <div className="pg-assets-onboarding-subtitle">
            四类素材用途不同：Logo 管品牌露出，核心资产管页面画面，风格参考管审美气质，版式模板管结构节奏。
          </div>
        </div>
        <div className="pg-assets-upload-grid">
          {uploadChoices.map((choice) => (
            <UploadChoice key={choice.label} {...choice} />
          ))}
        </div>
        <div className="pg-assets-flow">
          <span>上传</span>
          <span>系统分析入库</span>
          <span>按当前页自动推荐</span>
          <span>确认后进入生图 / 导出</span>
        </div>
      </div>
      {activeSlide && (
        <div className="pg-assets-current-slide">
          <span className="font-medium">当前页 P{activeSlide.page_num}</span>
          <span>手动锁定 {manualPinnedIds.length} 张</span>
          <span>Exact {enabledOverlayLayers.length} 张</span>
          {manualPinnedIds.length > 5 && <span className="text-amber-700">参考图较多，可能降低生图稳定性</span>}
          {enabledOverlayLayers.length > 2 && <span className="text-amber-700">精确贴图较多，建议拆页或减少文字</span>}
        </div>
      )}

      <div className="pg-assets-global-strip">
        {/* Logo */}
        {logos.length ? (
          <div className="flex flex-col items-center gap-1">
            <div className="pg-logo-lockup-preview" title={logos.length > 1 ? "联合标识" : "品牌 Logo"}>
              {logos.map((item, index) => (
                <div key={item.id} className="pg-logo-lockup-item">
                  {index > 0 && <span className="pg-logo-lockup-divider" aria-hidden="true" />}
                  <button
                    type="button"
                    className="pg-logo-lockup-image-button"
                    onClick={() => onImageClick(getImageUrl(apiBase, item.overlay_url || item.url))}
                    title={`查看 Logo ${index + 1}`}
                  >
                    <img
                      src={getImageUrl(apiBase, item.overlay_url || item.url)}
                      alt={`Logo ${index + 1}`}
                      onError={(e) => {
                        (e.target as HTMLImageElement).style.display = "none";
                      }}
                    />
                  </button>
                  <button
                    type="button"
                    onClick={() => onDelete(item.id)}
                    className="pg-logo-lockup-delete"
                    title={`删除 Logo ${index + 1}`}
                  >
                    X
                  </button>
                </div>
              ))}
            </div>
            <div className="grid grid-cols-4 gap-0.5 w-[140px]" title="选择全局 Logo 角标位置">
              {(["top-left", "top-right", "bottom-left", "bottom-right"] as const).map((anchor) => {
                const active = (logos[0]?.logo_anchor || "top-right") === anchor;
                return (
                  <button
                    key={anchor}
                    className={`pg-asset-anchor-button text-2xs rounded px-1 py-0.5 border ${
                      active
                        ? "bg-purple-600 text-white border-purple-600"
                        : "bg-white text-gray-500 border-gray-200 hover:border-purple-300"
                    }`}
                    onClick={() => updateLogoAnchor(anchor)}
                    title={`全局固定在${anchorLabel[anchor]}`}
                  >
                    {anchorLabel[anchor]}
                  </button>
                );
              })}
            </div>
            <div className="w-[140px] text-2xs text-gray-400 text-center leading-tight">
              {logos.length > 1 ? "联合标识固定成组叠加，按视觉等高排列" : "自动去底裁边，按页决定角标或融入画面"}
            </div>
          </div>
        ) : null}
      </div>

      {renderAssetSection(
        "已锁定到本页",
        activeSlide ? pinnedAssets : [],
        "当前页还没有手动锁定素材。需要某张图一定参与这一页时，在下方素材卡点击 Pin 到本页。",
        "锁定素材会优先随这一页进入生成。"
      )}
      {renderAssetSection(
        "推荐候选",
        activeSlide ? recommendedAssets : [],
        "没有根据当前页内容命中的候选素材。上传核心资产，或在资产说明里写清使用场景后会更容易命中。",
        "系统根据当前页标题、正文和画面描述自动匹配。"
      )}

      <div className="pg-assets-library-head">
        <div>
          <div className="pg-assets-library-title">全局素材库</div>
          <div className="pg-assets-library-subtitle">这里管理会进入页面生成的核心资产。</div>
        </div>
        <div className="pg-assets-library-count">{visualAssets.length} 个核心资产</div>
      </div>

      {visualAssets.length > 0 && (
        <div className="pg-assets-filter-grid">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索素材、标签、页码"
            className="text-xs border border-gray-200 rounded px-2 py-1 bg-white"
          />
          <select value={kindFilter} onChange={(e) => setKindFilter(e.target.value)} className="text-xs border border-gray-200 rounded px-2 py-1 bg-white">
            <option value="all">全部类型</option>
            {Object.entries(kindLabel).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
          <select value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value)} className="text-xs border border-gray-200 rounded px-2 py-1 bg-white">
            <option value="all">全部来源</option>
            {sourceDocuments.map((source) => <option key={String(source)} value={String(source)}>{String(source)}</option>)}
          </select>
          <select value={modeFilter} onChange={(e) => setModeFilter(e.target.value)} className="text-xs border border-gray-200 rounded px-2 py-1 bg-white">
            <option value="all">全部模式</option>
            <option value="blend">融合</option>
            <option value="crop">身份保真</option>
            <option value="original">原图</option>
          </select>
        </div>
      )}

      {renderAssetSection("全部素材", allLibraryAssets, visualAssets.length ? "没有符合筛选条件的素材" : "素材库为空。请先在上方上传核心资产。")}

      <div className="pg-assets-reference-head">
        <div>
          <div className="pg-assets-library-title">风格与版式参考</div>
          <div className="pg-assets-library-subtitle">这些素材只影响风格或结构，不会默认作为页面主体图片。</div>
        </div>
      </div>

      <div className="flex flex-wrap items-start gap-3 pb-1">
        {/* Style references */}
        {styleRefs.map((ref, idx) => (
          <AssetCard
            key={ref.id}
            label={idx === 0 ? "风格参考" : ""}
            onDelete={() => onDelete(ref.id)}
          >
            <img
              src={getImageUrl(apiBase, ref.url)}
              alt="风格参考"
              className="h-full w-full rounded object-cover cursor-pointer"
              onClick={() => onImageClick(getImageUrl(apiBase, ref.url))}
              onError={(e) => {
                (e.target as HTMLImageElement).style.display = "none";
              }}
            />
          </AssetCard>
        ))}

        {/* Template */}
        {template ? (
          <AssetCard label="版式模板" onDelete={() => onDelete(template.id)}>
            <div
              className="h-full w-full bg-gray-100 rounded flex items-center justify-center cursor-pointer text-xs text-gray-600 hover:bg-gray-200 transition-colors"
              onClick={() => setShowTemplatePages((v) => !v)}
            >
              {templatePages && templatePages.length > 0
                ? `${templatePages.length} 页`
                : "已上传"}
            </div>
          </AssetCard>
        ) : null}
      </div>

      {/* Template pages expandable */}
      {showTemplatePages && templatePages && templatePages.length > 0 && (
        <div className="mt-2 flex gap-2 overflow-x-auto pb-1">
          {templatePages.map((page) => {
            const recEntry = templateRecommendations
              ? Object.entries(templateRecommendations).find(
                  ([, v]) => v && (v as any).page_num === page.page_num
                )
              : null;
            const recKey = recEntry ? recEntry[0] : null;
            const isRecommended = !!recKey;
            const roleLabels: Record<string, string> = {
              cover: "封面",
              toc: "目录",
              content: "内容",
              ending: "封底",
            };
            return (
              <div
                key={page.page_num}
              className={`pg-template-card relative flex-shrink-0 bg-white rounded border p-1.5 ${
                  isRecommended
                    ? "border-purple-300 ring-1 ring-purple-200"
                    : "border-gray-200"
                }`}
              >
                <img
                  src={getImageUrl(apiBase, page.url)}
                  alt={`模板第${page.page_num}页`}
                  className="h-16 w-auto rounded object-cover"
                  onClick={() => onImageClick(getImageUrl(apiBase, page.url))}
                />
                <div className="text-2xs text-center mt-0.5">
                  <span className="text-gray-500">{page.page_num}页</span>
                  {isRecommended && (
                    <span className="text-purple-600 ml-0.5 font-medium">
                      {roleLabels[recKey]}
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
