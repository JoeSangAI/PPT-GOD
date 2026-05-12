export type AgentRole = "content" | "visual" | "finetune";
export type AgentRequestScope = "current_slide" | "selected_slides" | "deck";
export type AgentRequestRisk = "safe" | "cost" | "destructive";

export interface InferAgentRequestContextInput {
  message: string;
  activeAgentRole: AgentRole;
  activeScope: AgentRequestScope;
  editingPageNum?: number | null;
  selectedPageNums?: number[];
  projectStatus?: string;
  slideCount?: number;
  contentPlanConfirmed?: boolean;
  hasSelectedStyle?: boolean;
  hasPrompt?: boolean;
  hasGeneratedImage?: boolean;
}

export interface AgentRequestContext {
  targetRole: AgentRole;
  scope: AgentRequestScope;
  risk: AgentRequestRisk;
  pageNums: number[];
  explicitScope: boolean;
  scopeLabel: string;
  routeReason: string;
}

const normalizeMessage = (value: string) =>
  String(value || "")
    .replace(/\s+/g, " ")
    .trim();

const uniqueSortedNums = (values: number[]) =>
  Array.from(new Set(values.filter((n) => Number.isFinite(n) && n > 0))).sort((a, b) => a - b);

const normalizeNumberToken = (value: string) =>
  String(value || "").replace(/[０-９]/g, (char) => String.fromCharCode(char.charCodeAt(0) - 0xfee0));

const parsePageCountNumber = (value: string) => {
  const num = Number(normalizeNumberToken(value));
  return Number.isFinite(num) && num >= 1 && num <= 200 ? num : null;
};

const isSlideReferencePrefix = (message: string, index: number) =>
  /(?:第|P)\s*$/i.test(message.slice(Math.max(0, index - 4), index));

const PAGE_COUNT_CONTEXT_RE =
  /(页数|頁数|页面数|頁面数|张数|張数|PPT|幻灯片|簡報|课件|课程|培训|内训|演讲|讲课|大纲|规划|重新|重做|做|做成|做一份|扩成|扩展|拓展|压缩|缩减|控制|目标|最终|生成|约|左右|以内|以上|不少于|至少|不低于|不超过|不多于|最多|至多|deck|slides?|pages?|presentation|workshop|training)/i;

const hasPageCountContext = (message: string, start: number, end: number) => {
  const snippet = message.slice(Math.max(0, start - 24), Math.min(message.length, end + 24));
  return PAGE_COUNT_CONTEXT_RE.test(snippet);
};

export function inferRequestedPageCount(message: string): number | undefined {
  const text = normalizeNumberToken(String(message || ""));
  const pageUnit = "(?:页|頁|张|張|pages?|slides?)";
  const countLabel = "(?:页数|頁数|页面数|頁面数|张数|張数|slide\\s*count|page\\s*count|slides?|pages?)";
  const boundedPatterns = [
    new RegExp(
      `(?:不少于|至少|不低于|min(?:imum)?|at\\s*least)\\s*([0-9]{1,3})\\s*${pageUnit}?[\\s\\S]{0,24}(?:不超过|不多于|最多|至多|max(?:imum)?|at\\s*most)\\s*([0-9]{1,3})\\s*${pageUnit}?`,
      "gi"
    ),
    new RegExp(
      `(?:不超过|不多于|最多|至多|max(?:imum)?|at\\s*most)\\s*([0-9]{1,3})\\s*${pageUnit}?[\\s\\S]{0,24}(?:不少于|至少|不低于|min(?:imum)?|at\\s*least)\\s*([0-9]{1,3})\\s*${pageUnit}?`,
      "gi"
    ),
  ];
  for (const boundedPattern of boundedPatterns) {
    let boundedMatch: RegExpExecArray | null;
    while ((boundedMatch = boundedPattern.exec(text))) {
      const start = parsePageCountNumber(boundedMatch[1]);
      const end = parsePageCountNumber(boundedMatch[2]);
      if (start && end) return Math.max(start, end);
    }
  }
  const upperBoundPattern = new RegExp(
    `(?:不要超过|不超过|不多于|最多|至多|max(?:imum)?|at\\s*most)\\s*([0-9]{1,3})\\s*${pageUnit}?`,
    "gi"
  );
  let upperBoundMatch: RegExpExecArray | null;
  while ((upperBoundMatch = upperBoundPattern.exec(text))) {
    const value = parsePageCountNumber(upperBoundMatch[1]);
    if (value) return value;
  }

  const labelRangePattern = new RegExp(
    `${countLabel}\\D{0,12}([0-9]{1,3})\\s*(?:-|~|～|—|–|－|到|至|to)\\s*([0-9]{1,3})`,
    "gi"
  );
  let labelRangeMatch: RegExpExecArray | null;
  while ((labelRangeMatch = labelRangePattern.exec(text))) {
    const start = parsePageCountNumber(labelRangeMatch[1]);
    const end = parsePageCountNumber(labelRangeMatch[2]);
    if (start && end) return Math.max(start, end);
  }

  const rangePattern = new RegExp(`([0-9]{1,3})\\s*${pageUnit}?\\s*(?:-|~|～|—|–|－|到|至|to)\\s*([0-9]{1,3})\\s*${pageUnit}`, "gi");
  let rangeMatch: RegExpExecArray | null;
  while ((rangeMatch = rangePattern.exec(text))) {
    if (isSlideReferencePrefix(text, rangeMatch.index)) continue;
    const start = parsePageCountNumber(rangeMatch[1]);
    const end = parsePageCountNumber(rangeMatch[2]);
    if (start && end && (Math.min(start, end) >= 20 || hasPageCountContext(text, rangeMatch.index, rangePattern.lastIndex))) {
      return Math.max(start, end);
    }
  }

  const exactPattern = new RegExp(`([0-9]{1,3})\\s*${pageUnit}`, "gi");
  let exactMatch: RegExpExecArray | null;
  while ((exactMatch = exactPattern.exec(text))) {
    if (isSlideReferencePrefix(text, exactMatch.index)) continue;
    const value = parsePageCountNumber(exactMatch[1]);
    if (value && hasPageCountContext(text, exactMatch.index, exactPattern.lastIndex)) return value;
  }
  const labelExactPattern = new RegExp(`${countLabel}\\D{0,12}([0-9]{1,3})`, "gi");
  let labelExactMatch: RegExpExecArray | null;
  while ((labelExactMatch = labelExactPattern.exec(text))) {
    const value = parsePageCountNumber(labelExactMatch[1]);
    if (value) return value;
  }
  return undefined;
}

const extractPageNums = (message: string) => {
  const nums: number[] = [];
  const patterns = [
    /第\s*(\d{1,3})\s*[-到至]\s*(?:第\s*)?(\d{1,3})\s*(?:页|頁|张|張)/gi,
    /\bP\s*(\d{1,3})\s*[-到至~～]\s*(?:P\s*)?(\d{1,3})\b/gi,
    /(?:^|[^\d])(\d{1,3})\s*[-到至~～]\s*(\d{1,3})\s*(?:页|頁|张|張)/gi,
    /第\s*(\d{1,3})\s*(?:页|頁|张|張)/gi,
    /\bP\s*(\d{1,3})\b/gi,
  ];
  for (const pattern of patterns) {
    let match: RegExpExecArray | null;
    while ((match = pattern.exec(message))) {
      const start = Number(match[1]);
      const end = Number(match[2]);
      if (Number.isFinite(start)) nums.push(start);
      if (Number.isFinite(start) && Number.isFinite(end) && end >= start && end - start <= 40) {
        for (let n = start + 1; n <= end; n += 1) nums.push(n);
      }
    }
  }
  return uniqueSortedNums(nums);
};

const hasAny = (message: string, pattern: RegExp) => pattern.test(message);

const CURRENT_SCOPE_RE = /(当前页|当前页面|这一页|这页|本页|这个页面|这一张|这张PPT|这张幻灯片|正在看的页|这一个页面)/i;
const DECK_SCOPE_RE = /(整体|全局|全部|所有|整套|全套|每一页|每页|所有页|所有页面|统一|后面所有|后面都|后续所有|一整套|整个PPT|整份PPT|整套PPT)/i;
const SELECTED_SCOPE_RE = /(选中页|选中的页|这几页|这些页|这几张|这些页面)/i;

const VISUAL_RE = /(风格|视觉|配色|颜色|色彩|字体|排版|版式|背景|质感|调性|商务|科技|高级|深色|浅色|小红书|生活感|杂志|极简|复古|品牌|Logo|logo|素材|参考图|画面|图片|生图|出图|打样|重画|生成图片|画面方案|视觉描述|Prompt|prompt)/i;
const CONTENT_RE = /(内容|文案|标题|正文|小标题|结构|逻辑|故事|页数|原文|大纲|目录|讲稿|数据|案例|补充|改写|重写|扩写|删减|信息|表达|措辞)/i;
const FINETUNE_RE = /(微调|修图|改图|这张图|当前图|成图|底图|最终图|图片里|图里|放大|缩小|擦除|局部|局部调整)/i;
const COST_RE = /(出图|生图|生成图片|生成全部|批量生成|打样|重试失败|重新生成图片|确认生成|开始生成图片|全量生成)/i;
const DESTRUCTIVE_RE = /(重新做|重做|重新规划|重构|覆盖|从头|按原文重新|重新生成内容|重新生成规划|变成\s*\d{1,3}\s*(?:[-到至~～]\s*\d{1,3})?\s*页|(?:做成|扩成|扩展成)\s*\d{1,3}\s*(?:[-到至~～]\s*\d{1,3})?\s*页|删除|删掉|移除|清空|替换整套)/i;

const isVisualStage = (status?: string) =>
  ["visual_ready", "prompt_ready", "prototype", "prototype_ready", "generating", "completed", "failed"].includes(status || "");

export function inferAgentRequestContext(input: InferAgentRequestContextInput): AgentRequestContext {
  const message = normalizeMessage(input.message);
  const activeScope = input.activeScope;
  const selectedPageNums = uniqueSortedNums(input.selectedPageNums || []);
  const requestedPageCount = inferRequestedPageCount(message);
  const explicitPageNums = requestedPageCount ? [] : extractPageNums(message);
  const mentionsCurrent = hasAny(message, CURRENT_SCOPE_RE);
  const mentionsDeck = hasAny(message, DECK_SCOPE_RE);
  const mentionsSelected = hasAny(message, SELECTED_SCOPE_RE);

  let scope: AgentRequestScope = activeScope;
  let pageNums: number[] = [];
  let explicitScope = false;

  if (mentionsDeck) {
    scope = "deck";
    explicitScope = true;
  } else if (explicitPageNums.length > 1) {
    scope = "selected_slides";
    pageNums = explicitPageNums;
    explicitScope = true;
  } else if (explicitPageNums.length === 1) {
    scope = "current_slide";
    pageNums = explicitPageNums;
    explicitScope = true;
  } else if (mentionsSelected && selectedPageNums.length > 0) {
    scope = selectedPageNums.length === 1 ? "current_slide" : "selected_slides";
    pageNums = selectedPageNums;
    explicitScope = true;
  } else if (mentionsCurrent && input.editingPageNum) {
    scope = "current_slide";
    pageNums = [input.editingPageNum];
    explicitScope = true;
  } else if (activeScope === "current_slide" && input.editingPageNum) {
    pageNums = [input.editingPageNum];
  } else if (activeScope === "selected_slides" && selectedPageNums.length > 0) {
    pageNums = selectedPageNums;
  }

  if (scope === "deck") {
    pageNums = [];
  } else if (scope === "current_slide" && pageNums.length === 0 && input.editingPageNum) {
    pageNums = [input.editingPageNum];
  }

  const visualIntent = hasAny(message, VISUAL_RE);
  const contentIntent = hasAny(message, CONTENT_RE);
  const finetuneIntent = hasAny(message, FINETUNE_RE);
  const costIntent = hasAny(message, COST_RE);
  const destructiveIntent = hasAny(message, DESTRUCTIVE_RE);

  let targetRole = input.activeAgentRole;
  let routeReason = "active_agent";
  if (
    input.activeAgentRole === "finetune" &&
    finetuneIntent &&
    (input.hasGeneratedImage || input.projectStatus === "completed" || input.projectStatus === "prototype_ready") &&
    (scope === "current_slide" || input.editingPageNum)
  ) {
    targetRole = "finetune";
    routeReason = "finetune_intent";
  } else if ((input.projectStatus === "draft" || !input.contentPlanConfirmed) && (contentIntent || requestedPageCount || input.projectStatus === "draft")) {
    targetRole = "content";
    routeReason = contentIntent || requestedPageCount ? "content_intent" : "content_stage";
  } else if (costIntent || (visualIntent && (input.contentPlanConfirmed || isVisualStage(input.projectStatus) || input.activeAgentRole === "visual"))) {
    targetRole = "visual";
    routeReason = costIntent ? "cost_visual_action" : "visual_intent";
  } else if (contentIntent || input.projectStatus === "draft" || !input.contentPlanConfirmed) {
    targetRole = "content";
    routeReason = contentIntent ? "content_intent" : "content_stage";
  }

  const risk: AgentRequestRisk = costIntent ? "cost" : destructiveIntent ? "destructive" : "safe";

  return {
    targetRole,
    scope,
    risk,
    pageNums,
    explicitScope,
    scopeLabel: formatAgentScopeLabel(scope, pageNums),
    routeReason,
  };
}

export function formatAgentScopeLabel(scope: AgentRequestScope, pageNums: number[] = []) {
  if (scope === "deck") return "整套 PPT";
  if (scope === "selected_slides") {
    return pageNums.length > 0 ? `第 ${pageNums.join(", ")} 页` : "选中页";
  }
  return pageNums.length > 0 ? `第 ${pageNums[0]} 页` : "当前页";
}
