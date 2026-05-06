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

function normalizePalette(palette: (string | ColorChip)[]): ColorChip[] {
  return palette.map((c) => {
    if (typeof c === "string") {
      return { name: c, hex: c };
    }
    return c;
  });
}

export default function StyleProposalSelector({
  proposals,
  onSelect,
  onRegenerate,
  loading,
  disabled,
}: {
  proposals: StyleProposal[];
  onSelect: (proposal: StyleProposal) => void;
  onRegenerate: () => void;
  loading?: boolean;
  disabled?: boolean;
}) {
  return (
    <div className="bg-white rounded border shadow-sm p-6">
      <div className="text-center mb-6">
        <p className="text-sm text-gray-500">
          {loading ? "AI 正在根据内容生成风格方案..." : "根据您的内容，推荐以下视觉风格方案"}
        </p>
      </div>
      {loading ? (
        <div className="space-y-4 mb-6">
          {[1, 2, 3].map((i) => (
            <div key={i} className="border rounded-lg p-4 flex gap-4 animate-pulse">
              <div className="w-48 flex-shrink-0 space-y-2">
                <div className="h-4 bg-gray-200 rounded w-2/3" />
                <div className="flex gap-1">
                  {[1, 2, 3, 4].map((j) => (
                    <div key={j} className="w-6 h-6 rounded-full bg-gray-200" />
                  ))}
                </div>
                <div className="h-3 bg-gray-200 rounded w-full" />
                <div className="h-3 bg-gray-200 rounded w-1/2" />
                <div className="h-7 bg-gray-200 rounded w-full" />
              </div>
              <div className="flex-1 space-y-2">
                <div className="h-3 bg-gray-200 rounded w-full" />
                <div className="h-3 bg-gray-200 rounded w-full" />
                <div className="h-3 bg-gray-200 rounded w-3/4" />
              </div>
            </div>
          ))}
        </div>
      ) : (
        <>
          <div className="space-y-4 mb-6">
            {proposals.length === 0 ? (
              <div className="border-2 border-dashed border-gray-200 rounded-lg p-8 text-center">
                <div className="text-4xl mb-3">—</div>
                <h3 className="text-base font-semibold text-gray-800 mb-1">暂无风格提案</h3>
                <p className="text-sm text-gray-500 mb-4">你可以先上传素材（Logo / 参考图 / 模板），或直接生成风格提案</p>
                <button
                  onClick={onRegenerate}
                  disabled={disabled}
                  className="bg-blue-600 text-white px-5 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  生成风格提案
                </button>
              </div>
            ) : (
              proposals.map((proposal, idx) => {
              const isOriginal = proposal.source === "original" || !proposal.source;
              return (
                <div
                  key={idx}
                  className="border rounded-lg p-4 hover:border-blue-300 hover:shadow-md transition-all"
                >
                  <div className="flex gap-4">
                    {/* 左侧：核心信息 */}
                    <div className="w-56 flex-shrink-0">
                      <div className="flex items-center gap-2 mb-2">
                        <div className="text-sm font-semibold text-gray-800">{proposal.name}</div>
                        <span
                          className={`text-2xs px-1.5 py-0.5 rounded leading-none ${
                            isOriginal
                              ? "bg-purple-100 text-purple-700"
                              : "bg-gray-100 text-gray-500"
                          }`}
                        >
                          {isOriginal ? "AI 原创" : "风格库"}
                        </span>
                      </div>
                      <div className="space-y-1.5 mb-3">
                        {normalizePalette(proposal.palette).slice(0, 4).map((color, cidx) => (
                          <div
                            key={cidx}
                            className="group flex items-center gap-2 cursor-default"
                            title={color.name}
                          >
                            <div className="relative">
                              <div
                                className="w-5 h-5 rounded-full border border-gray-200 flex-shrink-0 transition-transform group-hover:scale-150 group-hover:z-10"
                                style={{ backgroundColor: color.hex }}
                              />
                            </div>
                            <div className="text-xs text-gray-600 truncate">
                              <span className="font-medium">{color.name}</span>
                              {color.role && <span className="text-gray-400 ml-1">· {color.role}</span>}
                            </div>
                          </div>
                        ))}
                      </div>
                      <div className="text-xs text-gray-500 mb-1">{proposal.mood}</div>
                      <div className="text-xs text-gray-400 mb-3">{proposal.font}</div>
                      <button
                        onClick={() => onSelect(proposal)}
                        disabled={disabled}
                        className="w-full text-xs bg-blue-600 text-white px-3 py-1.5 rounded hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        选择此方案
                      </button>
                    </div>

                    {/* 右侧：详细说明 */}
                    <div className="flex-1 border-l pl-4 min-w-0">
                      <div className="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap max-h-48 overflow-y-auto pr-2">
                        {proposal.description}
                      </div>
                    </div>
                  </div>
                </div>
              );
            })
          )}
          </div>
          <div className="text-center">
            <button
              onClick={onRegenerate}
              disabled={loading || disabled}
              className="text-sm text-gray-500 hover:text-gray-700 underline disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {loading ? "正在重新提案..." : "都不满意，让 Agent 重新提案"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
