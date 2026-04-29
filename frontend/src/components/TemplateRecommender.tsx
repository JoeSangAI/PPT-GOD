import { useState } from "react";

interface TemplatePage {
  page_num: number;
  url: string;
  category?: string;
}

interface Recommendations {
  cover: TemplatePage | null;
  toc: TemplatePage | null;
  content: TemplatePage | null;
  ending: TemplatePage | null;
}

const categoryLabels: Record<string, string> = {
  cover: "封面",
  toc: "目录",
  content: "内容页",
  ending: "封底",
};

export default function TemplateRecommender({
  pages,
  recommendations,
  onConfirm,
}: {
  pages: TemplatePage[];
  recommendations: Recommendations;
  onConfirm: (selected: Recommendations) => void;
}) {
  const [selected, setSelected] = useState<Recommendations>(recommendations);
  const [swapped, setSwapped] = useState<Record<string, number>>({});

  const handleSwap = (category: keyof Recommendations) => {
    const current = selected[category];
    if (!current || pages.length <= 1) return;

    // 只从同类页面中找下一个候选
    const sameCategoryPages = pages.filter((p) => p.category === category);
    const candidates = sameCategoryPages.length > 0 ? sameCategoryPages : pages;

    const currentIdx = candidates.findIndex((p) => p.page_num === current.page_num);
    const nextIdx = (currentIdx + 1) % candidates.length;
    const nextPage = candidates[nextIdx];

    setSelected((prev) => ({ ...prev, [category]: nextPage }));
    setSwapped((prev) => ({ ...prev, [category]: (prev[category] || 0) + 1 }));
  };

  const getSwapHint = (category: keyof Recommendations) => {
    const count = swapped[category] || 0;
    if (count === 0) return "";
    const sameCategoryPages = pages.filter((p) => p.category === category);
    const total = sameCategoryPages.length > 0 ? sameCategoryPages.length : pages.length;
    if (total <= 3) return ` (${count + 1}/${total})`;
    return ` (已浏览 ${Math.min(count + 1, total)}/${total})`;
  };

  return (
    <div className="bg-white rounded border shadow-sm p-6 max-w-4xl mx-auto">
      <div className="text-center mb-6">
        <h2 className="text-lg font-bold text-gray-800 mb-1">模板页面推荐</h2>
        <p className="text-sm text-gray-500">AI 从您上传的文件中挑选了以下代表页作为布局参考</p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        {(Object.keys(categoryLabels) as Array<keyof Recommendations>).map((cat) => {
          const page = selected[cat];
          return (
            <div key={cat} className="border rounded-lg p-3 flex flex-col items-center">
              <div className="text-xs text-gray-500 mb-2 font-medium">{categoryLabels[cat]}</div>
              {page ? (
                <>
                  <div className="aspect-[4/3] w-full rounded overflow-hidden bg-gray-100 mb-2">
                    <img
                      src={page.url}
                      alt={`${categoryLabels[cat]} ${page.page_num}`}
                      className="w-full h-full object-cover"
                      onError={(e) => {
                        (e.target as HTMLImageElement).style.display = "none";
                      }}
                    />
                  </div>
                  <div className="text-xs text-gray-400 mb-1">第 {page.page_num} 页</div>
                  <button
                    onClick={() => handleSwap(cat)}
                    className="text-xs text-gray-500 hover:text-blue-600 underline"
                  >
                    换一张 ↻{getSwapHint(cat)}
                  </button>
                </>
              ) : (
                <div className="text-xs text-gray-400 py-4">无推荐</div>
              )}
            </div>
          );
        })}
      </div>

      <div className="text-center">
        <button
          onClick={() => onConfirm(selected)}
          className="text-sm bg-blue-600 text-white px-6 py-2 rounded hover:bg-blue-700 transition-colors"
        >
          确认使用这套模板
        </button>
      </div>
    </div>
  );
}
