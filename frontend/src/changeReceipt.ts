export type ChangeReceiptStatus = "applied" | "queued" | "no_change" | "failed";

export interface ChangeReceiptInput {
  status: ChangeReceiptStatus;
  subject: string;
  change?: string | null;
  next?: string | null;
  skipped?: string | null;
}

const STATUS_PREFIX: Record<ChangeReceiptStatus, string> = {
  applied: "✅",
  queued: "⏳",
  no_change: "未修改",
  failed: "❌",
};

export const compactReceiptText = (value: unknown, limit = 90) => {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit)}...` : text;
};

export const formatPageNumsForReceipt = (pageNums: number[] = []) => {
  const nums = Array.from(new Set(pageNums.filter((n) => Number.isFinite(n) && n > 0))).sort((a, b) => a - b);
  if (nums.length === 0) return "相关页面";
  return `第 ${nums.join(", ")} 页`;
};

export const buildChangeReceipt = ({ status, subject, change, next, skipped }: ChangeReceiptInput) => {
  const prefix = STATUS_PREFIX[status];
  const firstLine =
    status === "no_change"
      ? `${prefix}：${compactReceiptText(subject, 120)}。`
      : `${prefix} ${compactReceiptText(subject, 120)}。`;
  const lines = [firstLine];
  const cleanChange = compactReceiptText(change || "", 120);
  const cleanSkipped = compactReceiptText(skipped || "", 120);
  const cleanNext = compactReceiptText(next || "", 120);
  if (cleanChange) lines.push(`变更：${cleanChange}`);
  if (cleanSkipped) lines.push(`未处理：${cleanSkipped}`);
  if (cleanNext) lines.push(`下一步：${cleanNext}`);
  return lines.join("\n\n");
};

export const summarizeContentChange = (content: any, fallback = "") => {
  const textContent = content?.text_content || content || {};
  const parts: string[] = [];
  const headline = compactReceiptText(textContent.headline || content?.headline || "", 42);
  const subhead = compactReceiptText(textContent.subhead || content?.subhead || "", 42);
  const rawBody = textContent.body ?? content?.body ?? "";
  const body = Array.isArray(rawBody)
    ? rawBody
        .map((item) => (typeof item === "string" ? item : item?.content || item?.text || ""))
        .filter(Boolean)[0]
    : String(rawBody || "").split("\n").filter(Boolean)[0];
  if (headline) parts.push(`标题：${headline}`);
  if (subhead) parts.push(`副标题：${subhead}`);
  if (body) parts.push(`正文：${compactReceiptText(body, 54)}`);
  return parts.join("；") || compactReceiptText(fallback, 90);
};

export const summarizeVisualChange = (userMessage: string, visualPatch: any, response = "") => {
  const visualJson = visualPatch?.visual_json || visualPatch || {};
  const designNotes = compactReceiptText(visualJson.design_notes || "");
  if (designNotes) return designNotes;
  const visualDescription = compactReceiptText(visualJson.visual_description || "");
  if (visualDescription) return visualDescription;
  const cleanResponse = compactReceiptText(String(response || "").replace(/^收到[，,]?\s*/, ""));
  if (cleanResponse) return cleanResponse;
  return `已写入你的要求：${compactReceiptText(userMessage)}`;
};

export const summarizeInsertedSlide = (slide: any) => {
  const summary = summarizeContentChange(slide);
  return summary ? `新增页面：${summary.replace(/^标题：/, "")}` : "新增页面已写入";
};
