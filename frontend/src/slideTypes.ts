export const CANONICAL_SLIDE_TYPES = [
  "cover",
  "toc",
  "section",
  "content",
  "data",
  "hero",
  "quote",
  "ending",
] as const;

export type CanonicalSlideType = (typeof CANONICAL_SLIDE_TYPES)[number];

export const SLIDE_TYPE_OPTIONS: ReadonlyArray<{ key: CanonicalSlideType; label: string }> = [
  { key: "cover", label: "封面" },
  { key: "toc", label: "目录" },
  { key: "section", label: "章节" },
  { key: "content", label: "内容" },
  { key: "data", label: "数据" },
  { key: "hero", label: "金句" },
  { key: "quote", label: "引用" },
  { key: "ending", label: "封底" },
];

export const SLIDE_TYPE_LABELS: Record<CanonicalSlideType, string> = Object.fromEntries(
  SLIDE_TYPE_OPTIONS.map((item) => [item.key, item.label])
) as Record<CanonicalSlideType, string>;

export const SLIDE_TYPE_COLORS: Record<CanonicalSlideType, string> = {
  cover: "bg-purple-100 text-purple-700",
  toc: "bg-blue-100 text-blue-700",
  section: "bg-pink-100 text-pink-700",
  content: "bg-gray-100 text-gray-700",
  data: "bg-green-100 text-green-700",
  hero: "bg-yellow-100 text-yellow-700",
  quote: "bg-amber-100 text-amber-700",
  ending: "bg-gray-100 text-gray-700",
};
