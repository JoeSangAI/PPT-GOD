import { useMemo, useState } from "react";

export type UploadChoiceKey = "logo" | "asset" | "style" | "template";

export interface UploadStatus {
  key?: UploadChoiceKey | string;
  title: string;
  detail?: string;
  fileName?: string;
}

export interface ReferenceImage {
  id: string;
  role: "logo" | "style_ref" | "template" | "visual_asset" | "content_ref" | "chart_ref";
  url?: string | null;
  overlay_url?: string | null;
  symbol_overlay_url?: string | null;
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
  review_status?: "auto_confirmed" | "user_confirmed" | "needs_review" | "dismissed" | "not_logo" | string | null;
  needs_user_review?: boolean;
  confidence_score?: number | null;
  review_reason?: string | null;
  detected_names?: string[];
  matched_terms?: string[];
  relevance_reason?: string | null;
  file_exists?: boolean;
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
    reference_images?: any[];
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
    review_status?: string;
    review_reason?: string;
  }) => Promise<void> | void;
  onPinAsset?: (slideId: string, assetId: string) => Promise<void> | void;
  onUnpinAsset?: (slideId: string, assetId: string) => Promise<void> | void;
  onUpdateOverlayLayers?: (slideId: string, layers: any[]) => Promise<void> | void;
  showInVisualStage?: boolean;
  uploadStatus?: UploadStatus | null;
  uploadDisabled?: boolean;
}

function getImageUrl(apiBase: string, url?: string | null) {
  if (!url) return "";
  return url.startsWith("http") ? url : `${apiBase}${url}`;
}

function reviewStatus(ref: ReferenceImage) {
  return String(ref.review_status || ref.asset_analysis?.review_status || "auto_confirmed").toLowerCase();
}

function isConfirmedLogo(ref: ReferenceImage) {
  return ref.role === "logo" && ["auto_confirmed", "user_confirmed"].includes(reviewStatus(ref));
}

function assetSource(ref: ReferenceImage) {
  return ref.source_document || ref.asset_analysis?.source_document || "";
}

function assetPage(ref: ReferenceImage) {
  return ref.source_page_num || ref.asset_analysis?.pptx_source_page_num || ref.page_num || null;
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
  uploadStatus = null,
  uploadDisabled = false,
}: VisualAssetsPanelProps) {
  const [showManager, setShowManager] = useState(false);
  const [showTemplatePages, setShowTemplatePages] = useState(false);
  const [editingAssetId, setEditingAssetId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [kindFilter, setKindFilter] = useState("all");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [assetDraft, setAssetDraft] = useState({
    asset_name: "",
    asset_kind: "other",
    usage_note: "",
  });

  const logos = referenceImages.filter((r) => r.role === "logo");
  const confirmedLogos = logos.filter(isConfirmedLogo);
  const reviewLogos = logos.filter((r) => r.needs_user_review || reviewStatus(r) === "needs_review");
  const styleRefs = referenceImages.filter((r) => r.role === "style_ref");
  const templateRefs = referenceImages.filter((r) => r.role === "template");
  const template = templateRefs[0];
  const visualAssets = referenceImages.filter((r) => r.role === "visual_asset");
  const shouldShow = showInVisualStage || referenceImages.length > 0;

  const manualPinnedIds: string[] = Array.isArray(activeSlide?.visual_json?.manual_visual_asset_ids)
    ? activeSlide!.visual_json.manual_visual_asset_ids.map(String)
    : [];
  const overlayLayers: any[] = Array.isArray(activeSlide?.visual_json?.overlay_layers)
    ? activeSlide!.visual_json.overlay_layers
    : [];

  const kindLabel: Record<string, string> = {
    all: "全部类型",
    product: "产品",
    person: "人物",
    scene: "场景",
    material: "物料",
    other: "其他",
  };
  const anchorLabel: Record<string, string> = {
    "top-left": "左上",
    "top-right": "右上",
    "bottom-left": "左下",
    "bottom-right": "右下",
  };

  const assetSearchText = (asset: ReferenceImage) => [
    asset.asset_name,
    asset.asset_kind,
    asset.usage_note,
    assetSource(asset),
    assetPage(asset),
    asset.asset_analysis?.source_slide_text,
    ...(Array.isArray(asset.asset_analysis?.asset_tags) ? asset.asset_analysis.asset_tags : []),
    ...(Array.isArray(asset.asset_analysis?.suggested_keywords) ? asset.asset_analysis.suggested_keywords : []),
  ].filter(Boolean).join(" ").toLowerCase();

  const sourceDocuments = useMemo(() => Array.from(new Set(
    visualAssets.map(assetSource).filter(Boolean)
  )).sort(), [visualAssets]);
  const normalizedQuery = query.trim().toLowerCase();
  const filteredVisualAssets = visualAssets.filter((asset) => {
    if (normalizedQuery && !assetSearchText(asset).includes(normalizedQuery)) return false;
    if (kindFilter !== "all" && (asset.asset_kind || "other") !== kindFilter) return false;
    if (sourceFilter !== "all" && assetSource(asset) !== sourceFilter) return false;
    return true;
  });
  const allLibraryAssets = filteredVisualAssets;

  if (!shouldShow) return null;

  const isUploadActive = uploadDisabled || Boolean(uploadStatus);
  const runUpload = (handler?: () => void) => {
    if (isUploadActive) return;
    handler?.();
  };
  const uploadChoices: Array<{ key: UploadChoiceKey; icon: string; title: string; detail: string; action?: () => void }> = [
    { key: "logo", icon: "L", title: "Logo", detail: "品牌标识", action: onUploadLogo },
    { key: "asset", icon: "+", title: "素材", detail: "产品/人物/物料", action: onUploadVisualAsset },
    { key: "style", icon: "S", title: "风格", detail: "参考气质", action: onUploadStyleRef },
    { key: "template", icon: "T", title: "模板", detail: "参考版式", action: onUploadTemplate },
  ];

  const AddChoiceGrid = ({ compact = false }: { compact?: boolean }) => (
    <div className={`pg-add-menu ${compact ? "is-compact" : ""}`} aria-label="添加项目素材">
      {uploadChoices.map((choice) => {
        const isChoiceActive = uploadStatus?.key === choice.key;
        return (
          <button
            key={choice.key}
            type="button"
            onClick={() => runUpload(choice.action)}
            disabled={isUploadActive}
            aria-busy={isChoiceActive}
          >
            <span className={`pg-add-choice-icon ${isChoiceActive ? "is-uploading" : ""}`} aria-hidden="true">
              {isChoiceActive ? "" : choice.icon}
            </span>
            <b>{choice.title}</b>
            <span>{isChoiceActive ? "上传中..." : choice.detail}</span>
          </button>
        );
      })}
    </div>
  );

  const updateLogoAnchor = async (anchor: string) => {
    for (const item of confirmedLogos) {
      await onUpdateVisualAsset?.(item.id, { logo_anchor: anchor });
    }
  };

  const confirmLogo = async (logo: ReferenceImage) => {
    await onUpdateVisualAsset?.(logo.id, {
      review_status: "user_confirmed",
      review_reason: "用户确认这是品牌 Logo",
    });
  };

  const convertLogoToAsset = async (logo: ReferenceImage) => {
    await onUpdateVisualAsset?.(logo.id, {
      review_status: "not_logo",
      asset_kind: "material",
      asset_name: logo.asset_name || logo.asset_analysis?.subject || "PPT 提取素材",
      usage_note: "用户确认这不是品牌 Logo，作为普通可复用素材保留",
    });
  };

  const dismissLogo = async (logo: ReferenceImage) => {
    await onUpdateVisualAsset?.(logo.id, {
      review_status: "dismissed",
      review_reason: "用户忽略了这个疑似 Logo",
    });
  };

  const Thumb = ({ item, className = "" }: { item: ReferenceImage; className?: string }) => {
    const displayUrl = item.overlay_url || item.url;
    const canPreview = Boolean(displayUrl) && (item.file_exists !== false || Boolean(item.overlay_url));
    const title = item.file_exists === false
      ? "原文件缺失，请删除后重新上传"
      : item.asset_name || item.asset_analysis?.subject || "查看素材";
    return (
      <button
        type="button"
        className={`pg-asset-thumb ${item.file_exists === false ? "is-missing" : ""} ${className}`}
        onClick={() => canPreview && onImageClick(getImageUrl(apiBase, displayUrl))}
        title={title}
        disabled={!canPreview}
      >
        {canPreview ? (
          <img
            src={getImageUrl(apiBase, displayUrl)}
            alt={item.asset_name || item.role}
            onError={(e) => {
              (e.target as HTMLImageElement).style.display = "none";
            }}
          />
        ) : (
          <span>缺文件</span>
        )}
      </button>
    );
  };

  const SummaryChip = ({ label, value }: { label: string; value: string | number }) => (
    <span className="pg-asset-summary-chip"><b>{value}</b>{label}</span>
  );

  const previewItems = [
    ...confirmedLogos.map((item) => ({ item, label: "Logo" })),
    ...visualAssets.map((item) => ({ item, label: item.asset_name || item.asset_analysis?.subject || "可复用" })),
    ...styleRefs.map((item) => ({ item, label: "风格参考" })),
    ...(template ? [{ item: template, label: templatePages?.length ? `${templatePages.length} 页模板` : "版式模板" }] : []),
  ];
  const missingAssetCount = referenceImages.filter((item) => item.file_exists === false).length;

  const FilterChip = ({ active, children, onClick }: { active: boolean; children: React.ReactNode; onClick: () => void }) => (
    <button type="button" className={`pg-filter-chip ${active ? "is-active" : ""}`} onClick={onClick}>
      {children}
    </button>
  );

  const renderAssetCard = (asset: ReferenceImage, variant: "manager" | "suggestion" | "pinned" = "manager") => {
    const name = asset.asset_name || asset.asset_analysis?.subject || "未命名素材";
    const isPinned = manualPinnedIds.includes(asset.id);
    const overlayLayer = overlayLayers.find((layer) => String(layer?.asset_id) === asset.id && layer?.enabled !== false);
    const source = assetSource(asset);
    const pageNum = assetPage(asset);
    const isEditing = editingAssetId === asset.id;
    const updateExactLayer = async (enabled: boolean, preset?: string) => {
      if (!activeSlide || !onUpdateOverlayLayers) return;
      const current = overlayLayers.filter((layer) => String(layer?.asset_id) !== asset.id);
      const next = enabled
        ? [...current, {
            id: overlayLayer?.id || `ov_${asset.id}`,
            asset_id: asset.id,
            enabled: true,
            preset: preset || overlayLayer?.preset || "right-card",
            fit: "contain",
            mode: overlayLayer?.mode || "exact_card",
            usage_note: overlayLayer?.usage_note || asset.usage_note || "",
          }]
        : current;
      await onUpdateOverlayLayers(activeSlide.id, next);
    };
    const setExactAppearance = async (enabled: boolean) => {
      if (enabled && activeSlide && !isPinned) {
        await onPinAsset?.(activeSlide.id, asset.id);
      }
      await updateExactLayer(enabled);
    };

    if (isEditing) {
      return (
        <div key={asset.id} className="pg-manager-asset-card is-editing">
          <Thumb item={asset} />
          <input
            value={assetDraft.asset_name}
            onChange={(e) => setAssetDraft((prev) => ({ ...prev, asset_name: e.target.value }))}
            placeholder="素材名称"
          />
          <div className="pg-chip-row">
            {Object.entries(kindLabel).filter(([key]) => key !== "all").map(([key, label]) => (
              <FilterChip key={key} active={assetDraft.asset_kind === key} onClick={() => setAssetDraft((prev) => ({ ...prev, asset_kind: key }))}>
                {label}
              </FilterChip>
            ))}
          </div>
          <textarea
            value={assetDraft.usage_note}
            onChange={(e) => setAssetDraft((prev) => ({ ...prev, usage_note: e.target.value }))}
            placeholder="什么时候使用它"
            rows={2}
          />
          <div className="pg-card-actions">
            <button type="button" className="pg-mini-primary" onClick={async () => {
              await onUpdateVisualAsset?.(asset.id, assetDraft);
              setEditingAssetId(null);
            }}>保存</button>
            <button type="button" onClick={() => setEditingAssetId(null)}>取消</button>
          </div>
        </div>
      );
    }

    return (
      <div key={asset.id} className={`pg-manager-asset-card ${variant !== "manager" ? "is-compact" : ""}`}>
        <Thumb item={asset} />
        <div className="pg-asset-card-name" title={name}>{name}</div>
        <div className="pg-asset-card-meta">
          {kindLabel[asset.asset_kind || "other"]} · {overlayLayer ? "原样出现" : "AI 参考"}
        </div>
        {(source || pageNum) && (
          <div className="pg-asset-card-meta" title={`${source}${pageNum ? ` 第${pageNum}页` : ""}`}>
            {pageNum ? `P${pageNum}` : "来源"} {source}
          </div>
        )}
        <div className="pg-card-actions">
          {activeSlide && (isPinned ? (
            <button type="button" onClick={() => onUnpinAsset?.(activeSlide.id, asset.id)}>
              {variant === "pinned" ? "取消指定" : "已指定"}
            </button>
          ) : (
            <button type="button" onClick={() => onPinAsset?.(activeSlide.id, asset.id)}>
              指定给本页
            </button>
          ))}
          {variant === "manager" && (
            <>
              <button type="button" onClick={() => {
                setEditingAssetId(asset.id);
                setAssetDraft({
                  asset_name: asset.asset_name || "",
                  asset_kind: asset.asset_kind || "other",
                  usage_note: asset.usage_note || "",
                });
              }}>编辑</button>
              {onUpdateOverlayLayers && activeSlide && (
                <button type="button" onClick={() => setExactAppearance(!overlayLayer)}>
                  {overlayLayer ? "改为 AI 参考" : "原样出现"}
                </button>
              )}
              <button type="button" className="pg-danger-text" onClick={() => onDelete(asset.id)}>删除</button>
            </>
          )}
        </div>
        {variant === "manager" && overlayLayer && (
          <div className="pg-chip-row">
            {OVERLAY_PRESETS.map((preset) => (
              <FilterChip
                key={preset.value}
                active={(overlayLayer?.preset || "right-card") === preset.value}
                onClick={() => updateExactLayer(true, preset.value)}
              >
                {preset.label}
              </FilterChip>
            ))}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="pg-visual-assets-panel bg-gray-50 border-b border-gray-200 px-3 py-2">
      <section className="pg-assets-tray pg-project-assets-section">
        <div className="pg-assets-tray-head">
          <div className="pg-assets-tray-summary">
            <div className="pg-assets-tray-title">
              <span>已添加</span>
              <b>{referenceImages.length}</b>
            </div>
            {referenceImages.length > 0 ? (
              <div className="pg-assets-tray-chips">
                <SummaryChip value={confirmedLogos.length} label=" Logo" />
                <SummaryChip value={visualAssets.length} label=" 可复用" />
                <SummaryChip value={styleRefs.length} label=" 风格" />
                <SummaryChip value={template ? 1 : 0} label=" 模板" />
                {missingAssetCount > 0 && <SummaryChip value={missingAssetCount} label=" 需重传" />}
              </div>
            ) : (
              <span className="pg-assets-tray-hint">Logo、产品图、风格参考、模板都可以先放这里。</span>
            )}
          </div>
          <div className="pg-assets-tray-actions">
            {uploadChoices.map((choice) => {
              const isChoiceActive = uploadStatus?.key === choice.key;
              return (
                <button
                  key={choice.key}
                  type="button"
                  className="pg-tray-upload-button"
                  onClick={() => runUpload(choice.action)}
                  disabled={isUploadActive}
                  aria-busy={isChoiceActive}
                >
                  <span className={`pg-tray-upload-icon ${isChoiceActive ? "is-uploading" : ""}`} aria-hidden="true">
                    {isChoiceActive ? "" : choice.icon}
                  </span>
                  <span>{isChoiceActive ? "上传中" : choice.title}</span>
                </button>
              );
            })}
            <button type="button" className="pg-assets-manage-button" onClick={() => setShowManager(true)}>
              管理
            </button>
          </div>
        </div>
        {uploadStatus && (
          <div className="pg-upload-status" role="status" aria-live="polite">
            <span className="pg-upload-status-spinner" aria-hidden="true" />
            <div className="pg-upload-status-copy">
              <b>{uploadStatus.title}</b>
              {uploadStatus.fileName && <span title={uploadStatus.fileName}>{uploadStatus.fileName}</span>}
              {uploadStatus.detail && <em>{uploadStatus.detail}</em>}
            </div>
          </div>
        )}
        {previewItems.length > 0 && (
          <div className="pg-lite-thumb-row pg-project-asset-preview-row">
            {previewItems.slice(0, 8).map(({ item, label }) => (
              <span key={item.id} className="pg-project-asset-preview-item" title={label}>
                <Thumb item={item} />
                <em>{label}</em>
              </span>
            ))}
            {previewItems.length > 8 && <span className="pg-more-pill">+{previewItems.length - 8}</span>}
          </div>
        )}
        {previewItems.length === 0 && (
          <div className="pg-project-asset-empty-state">
            <b>{showInVisualStage ? "还没有项目素材" : "还没有项目素材"}</b>
            <em>用上方按钮添加素材；没有素材也可以继续生成。</em>
          </div>
        )}
      </section>

      {reviewLogos.length > 0 && (
        <section className="pg-review-strip has-issues">
          <div className="pg-strip-title">
            <span>需要确认</span>
            <b>{reviewLogos.length}</b>
          </div>
          <div className="pg-review-list">
            {reviewLogos.map((logo) => (
              <div key={logo.id} className="pg-review-card">
                <Thumb item={logo} />
                <div className="pg-review-copy">
                  <b>{logo.asset_analysis?.subject || "疑似 Logo"}</b>
                  <span>{logo.review_reason || logo.asset_analysis?.review_reason || "系统不确定这是否应该作为品牌标识。"}</span>
                </div>
                <div className="pg-card-actions">
                  <button type="button" className="pg-mini-primary" onClick={() => confirmLogo(logo)}>确认是 Logo</button>
                  <button type="button" onClick={() => convertLogoToAsset(logo)}>作为普通素材</button>
                  <button type="button" onClick={() => dismissLogo(logo)}>忽略</button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {showManager && (
        <div className="pg-asset-manager-backdrop" role="presentation" onClick={() => setShowManager(false)}>
          <div className="pg-asset-manager" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <div className="pg-manager-head">
              <div>
                <div className="pg-assets-onboarding-title">项目素材库</div>
                <div className="pg-assets-onboarding-subtitle">这里管理跨页复用素材；当前页专用的图会留在对应页面的「本页图片确认」里。</div>
              </div>
              <button type="button" onClick={() => setShowManager(false)}>关闭</button>
            </div>

            <section className="pg-manager-section">
              <div className="pg-strip-title"><span>添加素材</span><b>上传</b></div>
              <AddChoiceGrid compact />
              {uploadStatus && (
                <div className="pg-upload-status is-compact" role="status" aria-live="polite">
                  <span className="pg-upload-status-spinner" aria-hidden="true" />
                  <div className="pg-upload-status-copy">
                    <b>{uploadStatus.title}</b>
                    {uploadStatus.fileName && <span title={uploadStatus.fileName}>{uploadStatus.fileName}</span>}
                    {uploadStatus.detail && <em>{uploadStatus.detail}</em>}
                  </div>
                </div>
              )}
            </section>

            <section className="pg-manager-section">
              <div className="pg-strip-title"><span>Logo</span><b>{confirmedLogos.length}</b></div>
              {confirmedLogos.length > 0 ? (
                <>
                  <div className="pg-logo-manager-row">
                    {confirmedLogos.map((logo) => (
                      <div key={logo.id} className="pg-logo-manager-card">
                        <Thumb item={logo} />
                        <button type="button" className="pg-danger-text" onClick={() => onDelete(logo.id)}>删除</button>
                      </div>
                    ))}
                  </div>
                  <div className="pg-chip-row">
                    {(["top-left", "top-right", "bottom-left", "bottom-right"] as const).map((anchor) => (
                      <FilterChip
                        key={anchor}
                        active={(confirmedLogos[0]?.logo_anchor || "top-right") === anchor}
                        onClick={() => updateLogoAnchor(anchor)}
                      >
                        {anchorLabel[anchor]}
                      </FilterChip>
                    ))}
                  </div>
                </>
              ) : (
                <div className="pg-clean-state">还没有已确认 Logo。</div>
              )}
            </section>

            <section className="pg-manager-section">
              <div className="pg-assets-filter-grid-lite">
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="搜索素材、标签、页码"
                />
                <div className="pg-chip-row">
                  {Object.entries(kindLabel).map(([key, label]) => (
                    <FilterChip key={key} active={kindFilter === key} onClick={() => setKindFilter(key)}>{label}</FilterChip>
                  ))}
                </div>
                <div className="pg-chip-row">
                  <FilterChip active={sourceFilter === "all"} onClick={() => setSourceFilter("all")}>全部来源</FilterChip>
                  {sourceDocuments.map((source) => (
                    <FilterChip key={String(source)} active={sourceFilter === source} onClick={() => setSourceFilter(String(source))}>
                      {String(source)}
                    </FilterChip>
                  ))}
                </div>
              </div>
              <div className="pg-manager-card-grid">
                {allLibraryAssets.length > 0
                  ? allLibraryAssets.map((asset) => renderAssetCard(asset, "manager"))
                  : <div className="pg-clean-state">没有符合条件的可复用素材。</div>}
              </div>
            </section>

            <section className="pg-manager-section">
              <div className="pg-strip-title"><span>风格与版式参考</span><b>{styleRefs.length + (template ? 1 : 0)}</b></div>
              <div className="pg-manager-card-grid">
                {styleRefs.map((ref) => (
                  <div key={ref.id} className="pg-manager-asset-card">
                    <Thumb item={ref} />
                    <div className="pg-asset-card-name">风格参考</div>
                    <button type="button" className="pg-danger-text" onClick={() => onDelete(ref.id)}>删除</button>
                  </div>
                ))}
                {template && (
                  <div className="pg-manager-asset-card">
                    <button type="button" className="pg-template-mini" onClick={() => setShowTemplatePages((v) => !v)}>
                      {templatePages?.length ? `${templatePages.length} 页模板` : "版式模板"}
                    </button>
                    <button type="button" className="pg-danger-text" onClick={() => onDelete(template.id)}>删除</button>
                  </div>
                )}
              </div>
              {showTemplatePages && templatePages && templatePages.length > 0 && (
                <div className="pg-template-strip">
                  {templatePages.map((page) => {
                    const recEntry = templateRecommendations
                      ? Object.entries(templateRecommendations).find(([, v]) => v && (v as any).page_num === page.page_num)
                      : null;
                    const recKey = recEntry ? recEntry[0] : null;
                    const roleLabels: Record<string, string> = {
                      cover: "封面",
                      toc: "目录",
                      section: "章节",
                      content: "内容",
                      data: "数据",
                      quote: "金句",
                      ending: "封底",
                    };
                    return (
                      <button key={page.page_num} type="button" className="pg-template-page" onClick={() => onImageClick(getImageUrl(apiBase, page.url))}>
                        <img src={getImageUrl(apiBase, page.url)} alt={`模板第${page.page_num}页`} />
                        <span>{page.page_num}页 {recKey ? roleLabels[recKey] : ""}</span>
                      </button>
                    );
                  })}
                </div>
              )}
            </section>
          </div>
        </div>
      )}
    </div>
  );
}
