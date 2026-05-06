import { useState } from "react";

export interface ColorChip {
  name: string;
  hex: string;
  role?: string;
}

export interface StyleProposal {
  name: string;
  palette: (string | ColorChip)[];
  mood: string;
  font: string;
  description: string;
  source?: string;
}

interface Props {
  proposals: StyleProposal[];
  onSelect: (proposal: StyleProposal) => void;
  onAdjust?: () => void;
  disabled?: boolean;
}

function normalizePalette(palette: (string | ColorChip)[]): ColorChip[] {
  return palette.map((c) => {
    if (!c) return { name: "未知", hex: "#cccccc" };
    if (typeof c === "string") return { name: c, hex: c };
    return c;
  });
}

function ProposalCard({
  proposal,
  index,
  onSelect,
  disabled,
}: {
  proposal: StyleProposal;
  index: number;
  onSelect: (p: StyleProposal) => void;
  disabled?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const palette = normalizePalette(proposal.palette);

  return (
    <div className="border border-purple-200 rounded-lg overflow-hidden bg-white">
      {/* 顶部色条 - 一眼看到配色 */}
      <div className="flex h-2">
        {palette.slice(0, 4).map((c, i) => (
          <div
            key={i}
            className="flex-1"
            style={{ backgroundColor: c.hex }}
            title={c.name}
          />
        ))}
      </div>

      <div className="p-3">
        {/* 名称与标签 */}
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-1.5">
            <span className="text-xs font-bold text-gray-800">
              {index + 1}.
            </span>
            <span className="text-sm font-bold text-gray-900">
              {proposal.name}
            </span>
          </div>
          {proposal.source === "original" ? (
            <span className="text-2xs bg-purple-100 text-purple-700 px-1.5 py-0.5 rounded">
              AI原创
            </span>
          ) : proposal.source ? (
            <span className="text-2xs bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded">
              风格库
            </span>
          ) : null}
        </div>

        {/* 配色明细 - 紧凑列表 */}
        <div className="space-y-1 mb-2">
          {palette.slice(0, 4).map((c, i) => (
            <div key={i} className="flex items-center gap-2">
              <div
                className="w-3 h-3 rounded-sm shrink-0 border border-gray-200"
                style={{ backgroundColor: c.hex }}
              />
              <span className="text-xs text-gray-600 truncate">
                {c.name} · {c.hex}
                {c.role && (
                  <span className="text-gray-400 ml-0.5">({c.role})</span>
                )}
              </span>
            </div>
          ))}
        </div>

        {/* 氛围词 - 标签式 */}
        {proposal.mood && (
          <div className="flex flex-wrap gap-1 mb-2">
            {proposal.mood.split(/[,，、\s]+/).filter(Boolean).slice(0, 5).map((m, i) => (
              <span
                key={i}
                className="text-2xs bg-purple-50 text-purple-700 px-1.5 py-0.5 rounded"
              >
                {m}
              </span>
            ))}
          </div>
        )}

        {/* 字体建议 */}
        {proposal.font && (
          <div className="text-xs text-gray-500 mb-2 truncate">
            字体：{proposal.font}
          </div>
        )}

        {/* 描述 - 可展开 */}
        {proposal.description && (
          <div className="mb-2">
            <div
              className={`text-xs text-gray-600 leading-relaxed ${
                expanded ? "" : "line-clamp-2"
              }`}
            >
              {proposal.description}
            </div>
            {proposal.description.length > 60 && (
              <button
                onClick={() => setExpanded(!expanded)}
                className="text-2xs text-purple-600 mt-0.5 hover:underline"
              >
                {expanded ? "收起" : "展开详情"}
              </button>
            )}
          </div>
        )}

        {/* 操作按钮 */}
        <button
          onClick={() => onSelect(proposal)}
          disabled={disabled}
          className="w-full py-1.5 bg-purple-600 text-white text-xs font-medium rounded hover:bg-purple-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors"
        >
          选择此方案
        </button>
      </div>
    </div>
  );
}

export default function ChatStyleProposal({
  proposals,
  onSelect,
  onAdjust,
  disabled,
}: Props) {
  if (!proposals || proposals.length === 0) return null;

  return (
    <div className="space-y-3 my-2">
      {proposals.map((p, i) => (
        <ProposalCard
          key={i}
          proposal={p}
          index={i}
          onSelect={onSelect}
          disabled={disabled}
        />
      ))}
      {onAdjust && (
        <button
          onClick={onAdjust}
          disabled={disabled}
          className="w-full py-1.5 border border-purple-300 text-purple-700 text-xs rounded hover:bg-purple-50 disabled:opacity-50 transition-colors"
        >
          调整方案（告诉我你的偏好）
        </button>
      )}
    </div>
  );
}
