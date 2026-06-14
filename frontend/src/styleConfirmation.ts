export interface StyleConfirmationProject {
  selected_style?: any | null;
  style_proposal?: { proposals?: any[] | null } | null;
}

export interface StyleConfirmationResult {
  style: any | null;
  message: string;
}

function normalizeChoiceText(value: any): string {
  return String(value || "")
    .replace(/[\s。.!！?？,，、:：;；~～"'“”‘’（）()【】[\]_-]+/g, "")
    .toLowerCase();
}

function isUsableStyleObject(value: any): boolean {
  return (
    value != null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Boolean(value.name || value.palette || value.mood || value.font || value.description || value.visual_strategy)
  );
}

function cloneStyle(value: any): any {
  if (!isUsableStyleObject(value)) return null;
  try {
    return JSON.parse(JSON.stringify(value));
  } catch {
    return { ...value };
  }
}

function styleCandidates(project?: StyleConfirmationProject | null, inlineProposals: any[] = []): any[] {
  const candidates: any[] = [];
  for (const item of inlineProposals || []) {
    if (isUsableStyleObject(item)) candidates.push(item);
  }
  const proposals = project?.style_proposal?.proposals;
  if (Array.isArray(proposals)) {
    for (const item of proposals) {
      if (isUsableStyleObject(item)) candidates.push(item);
    }
  }
  if (isUsableStyleObject(project?.selected_style)) candidates.push(project?.selected_style);

  const unique: any[] = [];
  const seen = new Set<string>();
  for (const item of candidates) {
    const key = normalizeChoiceText(item.name || item.decision_label || JSON.stringify(item.palette || []));
    if (!key || seen.has(key)) continue;
    seen.add(key);
    unique.push(item);
  }
  return unique;
}

function requestedStyleIndex(text: string): number | null {
  const normalized = normalizeChoiceText(text);
  const cnNums: Record<string, number> = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
  };
  const match = normalized.match(/(?:方案|第)?([0-9]{1,2}|[一二两三四五六七八九十])(?:套|个|号|版)?/);
  if (!match) return null;
  const raw = match[1];
  const value = /^[0-9]+$/.test(raw) ? Number(raw) : cnNums[raw];
  return value > 0 ? value - 1 : null;
}

function styleLabels(style: any, index: number): string[] {
  const labels = [
    style?.name,
    style?.decision_label,
    style?.source,
    `方案${index + 1}`,
    `第${index + 1}套`,
  ];
  if (index === 0) labels.push("推荐", "默认", "第一套");
  return labels.map(normalizeChoiceText).filter(Boolean);
}

export function resolveStyleForConfirmation(
  requestedStyle: any,
  project?: StyleConfirmationProject | null,
  inlineProposals: any[] = []
): StyleConfirmationResult {
  const directStyle = cloneStyle(requestedStyle);
  if (directStyle) return { style: directStyle, message: "" };

  const candidates = styleCandidates(project, inlineProposals);
  if (candidates.length === 0) {
    return { style: null, message: "请先生成视觉方向，再选择一套继续。" };
  }

  const requestedText = typeof requestedStyle === "string" ? requestedStyle : "";
  const normalizedText = normalizeChoiceText(requestedText);
  if (normalizedText) {
    const index = requestedStyleIndex(requestedText);
    if (index != null && candidates[index]) return { style: cloneStyle(candidates[index]), message: "" };

    for (let i = 0; i < candidates.length; i += 1) {
      const labels = styleLabels(candidates[i], i);
      if (labels.includes(normalizedText) || labels.some((label) => normalizedText.includes(label))) {
        return { style: cloneStyle(candidates[i]), message: "" };
      }
    }

    if (!/(直接|继续|跳过|默认|推荐|就这个|选这个|确认|可以|ok|okay|好)/.test(normalizedText)) {
      return { style: null, message: "没有找到这套视觉方向，请在画布中选择一套方案。" };
    }
  }

  return { style: cloneStyle(candidates[0]), message: "" };
}
