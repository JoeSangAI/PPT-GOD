import { useState } from "react";

export interface ReferenceImage {
  id: string;
  role: "logo" | "style_ref" | "template" | "content_ref" | "chart_ref";
  url: string;
  page_num?: number | null;
}

interface VisualAssetsPanelProps {
  referenceImages: ReferenceImage[];
  templateRecommendations?: any | null;
  templatePages?: any[];
  onDelete: (refId: string) => void;
  onImageClick: (url: string) => void;
  apiBase: string;
  onUploadLogo?: () => void;
  onUploadStyleRef?: () => void;
  onUploadTemplate?: () => void;
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
    <div className="relative group bg-white rounded-lg border border-gray-200 p-2 flex flex-col items-center gap-1.5 min-w-[100px] h-[92px]">
      <span className="text-2xs text-gray-500 font-medium leading-none h-3 flex items-center">
        {label || " "}
      </span>
      <div className="flex-1 flex items-center justify-center overflow-hidden">
        {children}
      </div>
      <button
        onClick={onDelete}
        className="absolute -top-1.5 -right-1.5 w-5 h-5 bg-red-500 text-white rounded-full text-2xs flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity shadow-sm hover:bg-red-600"
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
  onClick,
}: {
  label: string;
  formats: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      title={`点击上传 ${label}，支持 ${formats}`}
      className="flex-shrink-0 bg-white rounded-lg border border-dashed border-purple-200 p-2 flex flex-col items-center justify-center gap-0.5 min-w-[100px] h-[92px] hover:border-purple-400 hover:bg-purple-50 transition-colors group"
    >
      <span className="text-lg text-purple-400 group-hover:text-purple-500 transition-colors">+</span>
      <span className="text-2xs text-gray-600 font-medium">{label}</span>
      <span className="text-2xs text-gray-400">{formats}</span>
    </button>
  );
}

export default function VisualAssetsPanel({
  referenceImages,
  templateRecommendations,
  templatePages,
  onDelete,
  onImageClick,
  apiBase,
  onUploadLogo,
  onUploadStyleRef,
  onUploadTemplate,
  showInVisualStage = false,
}: VisualAssetsPanelProps) {
  const [showTemplatePages, setShowTemplatePages] = useState(false);

  const logo = referenceImages.find((r) => r.role === "logo");
  const styleRefs = referenceImages.filter((r) => r.role === "style_ref");
  const template = referenceImages.find((r) => r.role === "template");

  // 在视觉总监阶段始终显示面板，即使没有素材
  const shouldShow = showInVisualStage || referenceImages.length > 0;
  if (!shouldShow) return null;

  const hasAnyAssets = referenceImages.length > 0;

  return (
    <div className="bg-gray-50 border-b border-gray-200 px-3 py-2">
      <div className="flex items-center gap-2 mb-1.5">
        <span className="text-xs font-semibold text-gray-700">视觉素材</span>
        {hasAnyAssets && (
          <span className="text-2xs text-gray-400">
            {logo ? "Logo · " : ""}
            {styleRefs.length > 0 ? `${styleRefs.length} 张风格参考` : ""}
            {template ? " · 模板" : ""}
          </span>
        )}
        {!hasAnyAssets && showInVisualStage && (
          <span className="text-2xs text-gray-400">
            上传素材可让风格提案更精准
          </span>
        )}
      </div>

      <div className="flex items-start gap-2 overflow-x-auto pb-1">
        {/* Logo */}
        {logo ? (
          <AssetCard label="Logo" onDelete={() => onDelete(logo.id)}>
            <img
              src={getImageUrl(apiBase, logo.url)}
              alt="Logo"
              className="h-full w-full rounded object-contain cursor-pointer"
              onClick={() => onImageClick(getImageUrl(apiBase, logo.url))}
              onError={(e) => {
                (e.target as HTMLImageElement).style.display = "none";
              }}
            />
          </AssetCard>
        ) : showInVisualStage && onUploadLogo ? (
          <AddAssetButton label="上传 Logo" formats="PNG, JPG, SVG" onClick={onUploadLogo} />
        ) : null}

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
          <AddAssetButton label="风格参考" formats="PNG, JPG, WEBP" onClick={onUploadStyleRef} />
        )}

        {/* Template */}
        {template ? (
          <div className="relative group bg-white rounded-lg border border-gray-200 p-2 flex flex-col items-center gap-1.5 min-w-[100px] h-[92px]">
            <span className="text-2xs text-gray-500 font-medium leading-none h-3 flex items-center">模板</span>
            <div className="flex-1 flex items-center justify-center w-full">
              <div
                className="h-full w-[80px] bg-gray-100 rounded flex items-center justify-center cursor-pointer text-xs text-gray-600 hover:bg-gray-200 transition-colors"
                onClick={() => setShowTemplatePages((v) => !v)}
              >
                {templatePages && templatePages.length > 0
                  ? `${templatePages.length} 页`
                  : "已上传"}
              </div>
            </div>
            <button
              onClick={() => onDelete(template.id)}
              className="absolute -top-1.5 -right-1.5 w-5 h-5 bg-red-500 text-white rounded-full text-2xs flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity shadow-sm hover:bg-red-600"
              title="删除"
            >
              X
            </button>
          </div>
        ) : showInVisualStage && onUploadTemplate ? (
          <AddAssetButton label="参考模板" formats="PPT, PPTX, PDF" onClick={onUploadTemplate} />
        ) : null}
      </div>

      {/* Template pages expandable */}
      {showTemplatePages && templatePages && templatePages.length > 0 && (
        <div className="mt-2 flex gap-2 overflow-x-auto pb-1">
          {templatePages.map((page) => {
            // 根据后端推荐结果匹配当前页的角色
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
                className={`relative flex-shrink-0 bg-white rounded border p-1.5 ${
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
