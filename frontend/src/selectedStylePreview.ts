export type StylePreviewTone = "dark" | "light" | "mixed";

export interface StylePreviewColor {
  name: string;
  hex: string;
  role: string;
}

export interface StylePagePreview {
  key: "cover" | "section" | "content" | "data";
  label: string;
  tone: StylePreviewTone;
  background: string;
  accent: string;
  brand: string;
  text: string;
  surface: string;
  intensity: "strong" | "medium" | "calm";
}

export interface SelectedStylePreview {
  name: string;
  summary: string;
  baseTone: StylePreviewTone;
  palette: StylePreviewColor[];
  pages: StylePagePreview[];
  rhythmText: string;
  fontText: string;
}

const HEX_COLOR_PATTERN = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;
const FALLBACK_PALETTE: StylePreviewColor[] = [
  { name: "主色", hex: "#4F46E5", role: "标题强调" },
  { name: "强调色", hex: "#7C3AED", role: "重点信息" },
  { name: "信息底", hex: "#F8FAFC", role: "正文页基底" },
  { name: "正文色", hex: "#111827", role: "正文文字" },
];

function stripHexCodes(value: any) {
  return String(value || "")
    .replace(/#(?:[0-9a-fA-F]{3}){1,2}\b/g, "")
    .replace(/\s+([，。；;,.])/g, "$1")
    .replace(/（\s*）|\(\s*\)/g, "")
    .replace(/\s{2,}/g, " ")
    .trim();
}

function normalizeHex(value: any) {
  const raw = String(value || "").trim();
  if (!HEX_COLOR_PATTERN.test(raw)) return "#CBD5E1";
  if (raw.length === 4) {
    return `#${raw[1]}${raw[1]}${raw[2]}${raw[2]}${raw[3]}${raw[3]}`.toUpperCase();
  }
  return raw.toUpperCase();
}

function hexBrightness(hex: string) {
  const normalized = normalizeHex(hex);
  if (normalized === "#CBD5E1" && hex !== "#CBD5E1") return 210;
  const r = Number.parseInt(normalized.slice(1, 3), 16);
  const g = Number.parseInt(normalized.slice(3, 5), 16);
  const b = Number.parseInt(normalized.slice(5, 7), 16);
  return (r * 299 + g * 587 + b * 114) / 1000;
}

function isLight(hex: string) {
  return hexBrightness(hex) >= 180;
}

function hexSaturation(hex: string) {
  const normalized = normalizeHex(hex);
  const r = Number.parseInt(normalized.slice(1, 3), 16) / 255;
  const g = Number.parseInt(normalized.slice(3, 5), 16) / 255;
  const b = Number.parseInt(normalized.slice(5, 7), 16) / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  if (max === 0) return 0;
  return (max - min) / max;
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function colorSignal(color: StylePreviewColor) {
  return `${color.name} ${color.role}`.replace(/\s+/g, "");
}

function isBaseColor(color: StylePreviewColor) {
  return /(?:背景|基底|底色|主背景|页面基底|内容区|卡片|白|米白|浅底|浅色)/i.test(colorSignal(color));
}

function isTextColor(color: StylePreviewColor) {
  return /(?:正文|文字|标题文字|图表文字|text)/i.test(colorSignal(color));
}

function isNeutralColor(color: StylePreviewColor) {
  return hexSaturation(color.hex) < 0.08 || /(?:灰|白|黑|辅助线|分割线)/i.test(colorSignal(color));
}

function isLowRatioColor(color: StylePreviewColor) {
  return /(?:低占比|少量|小范围|点缀|细线|编号|标签|关键数字|Logo呼应|logo呼应|装饰线)/i.test(colorSignal(color));
}

function isBrandColor(color: StylePreviewColor) {
  return /(?:Logo|logo|品牌主色|主品牌色|品牌金|品牌黄|品牌识别|品牌色|品牌锚点|分众金|原有金|金色Logo|金色logo)/i.test(colorSignal(color));
}

function pageToneSignalText(style: any, palette: StylePreviewColor[]) {
  const paletteText = palette.map((color) => `${color.name} ${color.role}`).join(" ");
  return stripHexCodes(
    [
      style?.visual_strategy?.summary,
      style?.visual_strategy?.content_treatment,
      style?.page_type_adaptation,
      style?.content_style_hint,
      style?.visual_rhythm,
      style?.description,
      paletteText,
    ]
      .filter(Boolean)
      .join(" ")
  ).replace(/\s+/g, "");
}

function hasDarkInformationPageContract(text: string) {
  const informationPages = "(?:正文|内容|数据|表格|信息)(?:页|页面)?";
  const darkBase = "(?:黑色|深色|暗色|黑底|深底|深色底|深色基底|深色背景|深色系基底)";
  const surface = "(?:底|基底|背景)";
  return (
    new RegExp(`${informationPages}.{0,28}${darkBase}.{0,8}${surface}?`, "i").test(text) ||
    new RegExp(`${darkBase}.{0,8}${surface}?.{0,28}${informationPages}`, "i").test(text) ||
    /(?:整套|全套|全页|所有页面|页面整体).{0,16}(?:黑色|深色|暗色).{0,8}(?:底|基底|背景)/i.test(text)
  );
}

function hasLightInformationPageContract(text: string) {
  const informationPages = "(?:正文|内容|数据|表格|信息)(?:页|页面)?";
  const lightBase = "(?:白色?|白底|浅色?|浅底|米白|明亮|淡色)";
  const surface = "(?:底|基底|背景|内容区|卡片)";
  return (
    new RegExp(`${informationPages}.{0,28}${lightBase}.{0,8}${surface}?`, "i").test(text) ||
    new RegExp(`${lightBase}.{0,8}${surface}.{0,28}${informationPages}`, "i").test(text) ||
    /(?:整套|全套|全页|所有页面|页面整体).{0,16}(?:白色?|浅色?|米白|明亮).{0,8}(?:底|基底|背景)/i.test(text)
  );
}

function hasDeckWideLightContract(text: string) {
  return /(?:整套|全套|全页|所有页面|页面整体).{0,16}(?:白色?|浅色?|米白|明亮).{0,8}(?:底|基底|背景)/i.test(text);
}

function hasExplicitMixedToneContract(text: string) {
  const coverSection = "(?:封面|章节|过渡页|目录|扉页|开篇)";
  const contentData = "(?:正文|内容|数据|表格|信息|详情|内页)";
  const darkSignal = "(?:黑色|深色|暗色|黑底|深底|深色底|深色基底|深色背景|暗调|深色系)";
  const lightSignal = "(?:白色?|白底|浅色?|浅底|米白|明亮|淡色|清亮|通透)";

  const hasCoverDark =
    new RegExp(`${coverSection}.{0,32}${darkSignal}`, "i").test(text) ||
    new RegExp(`${darkSignal}.{0,32}${coverSection}`, "i").test(text);

  const hasContentLight =
    new RegExp(`${contentData}.{0,32}${lightSignal}`, "i").test(text) ||
    new RegExp(`${lightSignal}.{0,32}${contentData}`, "i").test(text);

  const hasCoverLight =
    new RegExp(`${coverSection}.{0,32}${lightSignal}`, "i").test(text) ||
    new RegExp(`${lightSignal}.{0,32}${coverSection}`, "i").test(text);

  const hasContentDark =
    new RegExp(`${contentData}.{0,32}${darkSignal}`, "i").test(text) ||
    new RegExp(`${darkSignal}.{0,32}${contentData}`, "i").test(text);

  return (hasCoverDark && hasContentLight) || (hasCoverLight && hasContentDark);
}

function hasLightInformationPaletteContract(palette: StylePreviewColor[]) {
  return palette.some((color) => {
    const roleText = `${color.name} ${color.role}`.replace(/\s+/g, "");
    return (
      isLight(color.hex) &&
      /(?:正文|内容|数据|表格|信息)(?:页|页面)?.{0,8}(?:底|基底|背景|内容区|卡片)/i.test(roleText) &&
      !/(?:深色?|黑色?|暗色?)/i.test(roleText)
    );
  });
}

function normalizePalette(palette: any[] | undefined): StylePreviewColor[] {
  const normalized = (Array.isArray(palette) ? palette : []).map((color, index) => {
    if (typeof color === "string") {
      return { name: stripHexCodes(color) || `颜色 ${index + 1}`, hex: normalizeHex(color), role: "" };
    }
    return {
      name: stripHexCodes(color?.name) || `颜色 ${index + 1}`,
      hex: normalizeHex(color?.hex),
      role: stripHexCodes(color?.role) || "",
    };
  });
  const merged = [...normalized];
  for (const fallback of FALLBACK_PALETTE) {
    if (merged.length >= 4) break;
    merged.push(fallback);
  }
  return merged.slice(0, 5);
}

function inferBaseTone(style: any, palette: StylePreviewColor[]): StylePreviewTone {
  const explicit = String(style?.visual_strategy?.base_tone || "").toLowerCase();
  if (explicit === "dark" || explicit === "light" || explicit === "mixed") return explicit;
  const joined = pageToneSignalText(style, palette);

  if (hasExplicitMixedToneContract(joined)) return "mixed";

  if (hasDarkInformationPageContract(joined)) return "dark";
  if (hasDeckWideLightContract(joined)) return "light";
  if (hasLightInformationPageContract(joined) || hasLightInformationPaletteContract(palette)) return "mixed";
  if (/深色|黑色|暗色|深蓝|深紫|dark/i.test(joined) && !/浅色|白色|米白|明亮|light/i.test(joined)) return "dark";
  if (/浅色|白色|米白|明亮|light/i.test(joined) && !/全页深色|深色基底|dark/i.test(joined)) return "light";
  const lightCount = palette.filter((color) => isLight(color.hex)).length;
  const darkCount = palette.filter((color) => !isLight(color.hex)).length;
  if (lightCount >= 3) return "light";
  if (darkCount >= 3) return "dark";
  return "mixed";
}

function pickColor(palette: StylePreviewColor[], matcher: RegExp, fallbackIndex: number, tone?: "dark" | "light") {
  const matchesTone = (color: StylePreviewColor) => (tone === "light" ? isLight(color.hex) : tone === "dark" ? !isLight(color.hex) : true);
  return (
    palette.find((color) => matcher.test(`${color.name} ${color.role}`) && matchesTone(color)) ||
    palette.find((color) => matchesTone(color)) ||
    palette[fallbackIndex] ||
    FALLBACK_PALETTE[fallbackIndex]
  );
}

function pickBrandColor(palette: StylePreviewColor[]) {
  return palette.find(isBrandColor) || null;
}

function pickVisualAccent(palette: StylePreviewColor[], brandColor: StylePreviewColor | null) {
  const isUsableAccent = (color: StylePreviewColor) =>
    color.hex !== brandColor?.hex &&
    !isBaseColor(color) &&
    !isTextColor(color) &&
    !isLowRatioColor(color) &&
    !isNeutralColor(color);
  return (
    palette.find((color) => /(?:视觉锚点|标题强调|标题|页眉|主色|强调|装饰|辅助)/i.test(colorSignal(color)) && isUsableAccent(color)) ||
    palette.find(isUsableAccent) ||
    brandColor ||
    palette.find((color) => !isBaseColor(color) && !isTextColor(color)) ||
    palette[1] ||
    FALLBACK_PALETTE[1]
  );
}

function buildPagePreviews(baseTone: StylePreviewTone, palette: StylePreviewColor[]): StylePagePreview[] {
  const brandColor = pickBrandColor(palette);
  const accent = pickVisualAccent(palette, brandColor);
  const lightBase = pickColor(palette, /白|浅|米|明亮|内容区|卡片|背景|基底/i, 2, "light");
  const darkBase = pickColor(palette, /深|黑|暗|背景|基底/i, 3, "dark");
  const textColor = pickColor(palette, /正文|文字|图表文字|text/i, 3);
  const brand = brandColor?.hex || accent.hex;
  const darkBackground = isLight(darkBase.hex) ? "#111827" : darkBase.hex;
  const lightBackground = isLight(lightBase.hex) ? lightBase.hex : "#F8FAFC";
  const darkText = isLight(textColor.hex) ? textColor.hex : "#F8FAFC";
  const lightText = isLight(textColor.hex) ? "#111827" : textColor.hex;
  const informationTone = baseTone === "dark" ? "dark" : baseTone === "light" ? "light" : "light";
  const informationBackground = informationTone === "dark" ? darkBackground : lightBackground;
  const informationText = informationTone === "dark" ? darkText : lightText;
  const informationSurface = informationTone === "dark" ? "rgba(15, 23, 42, 0.72)" : "#FFFFFF";

  return [
    {
      key: "cover",
      label: "封面",
      tone: baseTone === "light" ? "light" : "dark",
      background: baseTone === "light" ? lightBackground : darkBackground,
      accent: accent.hex,
      brand,
      text: baseTone === "light" ? lightText : darkText,
      surface: accent.hex,
      intensity: "strong",
    },
    {
      key: "section",
      label: "章节",
      tone: baseTone === "light" ? "light" : "dark",
      background: baseTone === "light" ? lightBackground : darkBackground,
      accent: accent.hex,
      brand,
      text: baseTone === "light" ? lightText : darkText,
      surface: brand,
      intensity: "medium",
    },
    {
      key: "content",
      label: "正文",
      tone: informationTone,
      background: informationBackground,
      accent: accent.hex,
      brand,
      text: informationText,
      surface: informationSurface,
      intensity: "calm",
    },
    {
      key: "data",
      label: "数据",
      tone: informationTone,
      background: informationBackground,
      accent: accent.hex,
      brand,
      text: informationText,
      surface: informationSurface,
      intensity: "calm",
    },
  ];
}

function normalizeBrandCopy(raw: string, palette: StylePreviewColor[]) {
  const brandColor = pickBrandColor(palette);
  const accentColor = pickVisualAccent(palette, brandColor);
  let text = stripHexCodes(raw);
  if (!brandColor || !accentColor || brandColor.hex === accentColor.hex) return text;
  const brandName = brandColor.name || "Logo 色";
  const accentName = accentColor.name || "辅助色";
  const brandPattern = escapeRegExp(brandName);
  const accentPattern = escapeRegExp(accentName);
  text = text
    .replace(new RegExp(`${brandPattern}(?:仅|只)?(?:作|作为|做)低占比品牌点缀`, "g"), `${brandName}作为品牌识别色并控制低占比`)
    .replace(new RegExp(`使用${accentPattern}作为品牌主色`, "g"), `保留${brandName}作为品牌识别色，${accentName}用于辅助强调`)
    .replace(new RegExp(`${accentPattern}定调品牌识别(?:和(?:主)?装饰)?`, "g"), `${brandName}作为品牌识别色，${accentName}用于辅助强调`)
    .replace(new RegExp(`${accentPattern}作为品牌主色`, "g"), `${accentName}作为辅助强调色`)
    .replace(new RegExp(`${accentPattern}作为品牌识别(?:和(?:主)?装饰)?`, "g"), `${brandName}作为品牌识别色，${accentName}用于辅助强调`)
    .replace(new RegExp(`${accentPattern}做品牌识别和装饰`, "g"), `${brandName}保留为品牌识别色，${accentName}用于辅助强调和装饰`)
    .replace(new RegExp(`${accentPattern}做品牌识别`, "g"), `${brandName}做品牌识别，${accentName}做辅助强调`);
  return text;
}

export function buildSelectedStylePreview(style: any): SelectedStylePreview {
  const palette = normalizePalette(style?.palette);
  const baseTone = inferBaseTone(style, palette);
  const pages = buildPagePreviews(baseTone, palette);
  const summary = normalizeBrandCopy(
    style?.visual_strategy?.summary ||
      style?.visual_strategy?.content_treatment ||
      style?.description ||
      style?.mood ||
      "这套方案会按页面类型控制视觉强弱，先保证正文和数据页可读。",
    palette
  );
  const rhythmText =
    baseTone === "dark"
      ? "封面/章节页放大主色和装饰，正文/数据页保持同一深色基底，用卡片、留白和高对比文字保证阅读。"
      : baseTone === "light"
        ? "封面/章节页保持明亮基底并增强品牌色，正文/数据页降低装饰强度，优先保证信息清晰。"
        : "封面/章节页承担视觉记忆点，正文/数据页降低背景复杂度，保持同一套色彩和层级。";
  const fontText = stripHexCodes(style?.font) || "标题、正文和数据使用同一套清晰字体系，数字和重点信息保持更强对比。";
  return {
    name: stripHexCodes(style?.name) || "视觉方案",
    summary,
    baseTone,
    palette,
    pages,
    rhythmText,
    fontText,
  };
}
