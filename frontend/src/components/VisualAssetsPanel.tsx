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

function AddAssetButton({
  label,
  formats,
  description,
  onClick,
}: {
  label: string;
  formats: string;
  description?: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      title={description || `点击上传 ${label}，支持 ${formats}`}
      className="pg-add-asset-button flex-shrink-0 bg-white rounded-lg border border-dashed border-purple-200 p-2 flex flex-col items-center justify-center gap-0.5 w-[140px] h-[140px] hover:border-purple-400 hover:bg-purple-50 transition-colors group"
    >
      <span className="text-lg text-purple-400 group-hover:text-purple-500 transition-colors">+</span>
      <span className="text-2xs text-gray-600 font-medium">{label}</span>
      <span className="text-2xs text-gray-400">{formats}</span>
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

  const logo = referenceImages.find((r) => r.role === "logo");
  const styleRefs = referenceImages.filter((r) => r.role === "style_ref");
  const template = referenceImages.find((r) => r.role === "template");
  const visualAssets = referenceImages.filter((r) => r.role === "visual_asset");
  const manualPinnedIds: string[] = Array.isArray(activeSlide?.visual_json?.manual_visual_asset_ids)
    ? activeSlide!.visual_json.manual_visual_asset_ids.map(String)
    : [];
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

  const hasAnyAssets = referenceImages.length > 0;
  const anchorLabel: Record<string, string> = {
    "top-left": "左上",
    "top-right": "右上",
    "bottom-left": "左下",
    "bottom-right": "右下",
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
        )}
      </div>
    );
  };

  const renderAssetSection = (title: string, assets: ReferenceImage[], emptyText: string) => (
    <div className="mb-3">
      <div className="text-2xs text-gray-500 font-semibold mb-1.5">{title} · {assets.length}</div>
      {assets.length > 0 ? (
        <div className="flex flex-wrap items-start gap-3 pb-1">{assets.map(renderVisualAssetCard)}</div>
      ) : (
        <div className="text-2xs text-gray-400 bg-white border border-dashed border-gray-200 rounded px-3 py-2">
          {emptyText}
        </div>
      )}
    </div>
  );

  return (
    <div className="pg-visual-assets-panel bg-gray-50 border-b border-gray-200 px-3 py-2">
      {(!hasAnyAssets && showInVisualStage) && (
        <div className="text-2xs text-gray-400 mb-2">
          按参考强度从高到低上传：品牌 Logo、核心资产、风格参考、版式模板
        </div>
      )}
      <div className="text-2xs text-gray-400 mb-2">
        Logo 由系统按页面智能处理；核心资产按页进入生图；风格参考只提取视觉气质；版式模板只参考页面结构。
      </div>
      {activeSlide && (
        <div className="mb-2 flex items-center gap-2 text-2xs text-blue-700 bg-blue-50 border border-blue-100 rounded px-2 py-1">
          <span className="font-medium">当前页 P{activeSlide.page_num}</span>
          <span>手动锁定 {manualPinnedIds.length} 张</span>
          {manualPinnedIds.length > 5 && <span className="text-amber-700">参考图较多，可能降低生图稳定性</span>}
        </div>
      )}

      {visualAssets.length > 0 && (
        <div className="mb-3 grid grid-cols-1 md:grid-cols-4 gap-2">
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

      <div className="flex flex-wrap items-start gap-3 pb-1">
        {/* Logo */}
        {logo ? (
          <div className="flex flex-col items-center gap-1">
            <AssetCard label="品牌 Logo" onDelete={() => onDelete(logo.id)}>
              <img
                src={getImageUrl(apiBase, logo.overlay_url || logo.url)}
                alt="Logo"
                className="h-full w-full rounded object-contain cursor-pointer"
                onClick={() => onImageClick(getImageUrl(apiBase, logo.overlay_url || logo.url))}
                onError={(e) => {
                  (e.target as HTMLImageElement).style.display = "none";
                }}
              />
            </AssetCard>
            <div className="grid grid-cols-4 gap-0.5 w-[140px]" title="选择全局 Logo 角标位置">
              {(["top-left", "top-right", "bottom-left", "bottom-right"] as const).map((anchor) => {
                const active = (logo.logo_anchor || "top-right") === anchor;
                return (
                  <button
                    key={anchor}
                    className={`pg-asset-anchor-button text-2xs rounded px-1 py-0.5 border ${
                      active
                        ? "bg-purple-600 text-white border-purple-600"
                        : "bg-white text-gray-500 border-gray-200 hover:border-purple-300"
                    }`}
                    onClick={() => onUpdateVisualAsset?.(logo.id, { logo_anchor: anchor })}
                    title={`全局固定在${anchorLabel[anchor]}`}
                  >
                    {anchorLabel[anchor]}
                  </button>
                );
              })}
            </div>
            <div className="w-[140px] text-2xs text-gray-400 text-center leading-tight">
              自动去底裁边，按页决定角标或融入画面
            </div>
          </div>
        ) : showInVisualStage && onUploadLogo ? (
          <AddAssetButton
            label="品牌 Logo"
            formats="角标叠加"
            description="上传主品牌 Logo；默认作为右上角品牌角标叠加，下载前预览可见"
            onClick={onUploadLogo}
          />
        ) : null}

        {/* Core visual assets */}
        {showInVisualStage && onUploadVisualAsset && (
          <AddAssetButton
            label="核心资产"
            formats="产品/主KV"
            description="上传产品图、主 KV、模特图等必须保真的素材；系统会按页面内容自动判断什么时候使用"
            onClick={onUploadVisualAsset}
          />
        )}
      </div>

      {renderAssetSection("已锁定到本页", activeSlide ? pinnedAssets : [], "当前页还没有手动锁定素材")}
      {renderAssetSection("推荐候选", activeSlide ? recommendedAssets : [], "没有根据当前页内容命中的候选素材")}
      {renderAssetSection("全部素材", allLibraryAssets, visualAssets.length ? "没有符合筛选条件的素材" : "素材库为空")}

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
        {showInVisualStage && onUploadStyleRef && (
          <AddAssetButton
            label="风格参考"
            formats="只取气质"
            description="上传你喜欢的设计感觉；系统只学习配色、材质、构图节奏，这张图本身不会被要求出现在页面里"
            onClick={onUploadStyleRef}
          />
        )}

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
        ) : showInVisualStage && onUploadTemplate ? (
          <AddAssetButton
            label="版式模板"
            formats="PPT/PDF"
            description="上传参考 PPT 或 PDF；系统只参考封面、目录、内容页、结尾页的版式秩序"
            onClick={onUploadTemplate}
          />
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
