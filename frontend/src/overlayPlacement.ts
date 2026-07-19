export type OverlayPlacementStyle = {
  left: string;
  top: string;
  width: string;
  height: string;
};

export const OVERLAY_PRESET_BOXES: Record<string, OverlayPlacementStyle> = {
  "top-right-small": { left: "72%", top: "8%", width: "20%", height: "18%" },
  "bottom-right-small": { left: "72%", top: "72%", width: "20%", height: "18%" },
  "left-card": { left: "6.5%", top: "18%", width: "36%", height: "58%" },
  "right-card": { left: "59.5%", top: "18%", width: "34%", height: "58%" },
  "center-card": { left: "28%", top: "20%", width: "44%", height: "56%" },
  "bottom-band": { left: "12%", top: "68%", width: "76%", height: "22%" },
  "gallery-2-left": { left: "22%", top: "24%", width: "25%", height: "58%" },
  "gallery-2-right": { left: "53%", top: "24%", width: "25%", height: "58%" },
  "gallery-3-left": { left: "11%", top: "24%", width: "25%", height: "58%" },
  "gallery-3-center": { left: "37.5%", top: "24%", width: "25%", height: "58%" },
  "gallery-3-right": { left: "64%", top: "24%", width: "25%", height: "58%" },
  "gallery-4-left": { left: "7.5%", top: "26%", width: "20%", height: "52%" },
  "gallery-4-mid-left": { left: "28.5%", top: "26%", width: "20%", height: "52%" },
  "gallery-4-mid-right": { left: "49.5%", top: "26%", width: "20%", height: "52%" },
  "gallery-4-right": { left: "70.5%", top: "26%", width: "20%", height: "52%" },
  "primary-left": { left: "9%", top: "24%", width: "46%", height: "58%" },
  "secondary-right": { left: "63%", top: "24%", width: "25%", height: "58%" },
  "secondary-right-top": { left: "63%", top: "24%", width: "25%", height: "27%" },
  "secondary-right-bottom": { left: "63%", top: "55%", width: "25%", height: "27%" },
};

function finiteRatio(value: unknown): number | null {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 && number <= 1 ? number : null;
}

export function overlayPlacementStyle(layer: any): OverlayPlacementStyle {
  const resolved = layer?.resolved_overlay_box;
  const left = finiteRatio(resolved?.left);
  const top = finiteRatio(resolved?.top);
  const width = finiteRatio(resolved?.width);
  const height = finiteRatio(resolved?.height);
  const matchesSource =
    (!resolved?.source_preset || resolved.source_preset === layer?.preset) &&
    (!resolved?.source_mode || resolved.source_mode === layer?.mode);
  if (
    matchesSource &&
    left !== null &&
    top !== null &&
    width !== null &&
    height !== null &&
    width > 0 &&
    height > 0 &&
    left + width <= 1.001 &&
    top + height <= 1.001
  ) {
    return {
      left: `${left * 100}%`,
      top: `${top * 100}%`,
      width: `${width * 100}%`,
      height: `${height * 100}%`,
    };
  }
  return OVERLAY_PRESET_BOXES[layer?.preset] || OVERLAY_PRESET_BOXES["right-card"];
}
