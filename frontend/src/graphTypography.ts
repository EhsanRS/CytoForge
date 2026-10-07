import type { GraphOptions, GraphTextStyle, GraphTypography } from "./types";

export const graphTextRoles = {
  axis_labels: "Axis labels",
  tick_labels: "Axis numbers",
  gate_labels: "Gate names",
  statistics: "Statistics",
  legend: "Legend",
  title: "Title",
} as const;
export type GraphTextRole = keyof GraphTypography;
export const graphFontFamilies = {
  sans: "CytoForge Sans",
  serif: "CytoForge Serif",
  mono: "CytoForge Mono",
} as const;

export function scientificGraphOptions(options: GraphOptions): GraphOptions {
  const {
    typography: _typography,
    gate_style: _gateStyle,
    ...scientific
  } = options;
  return scientific;
}

export function graphTextCSS(options: GraphOptions, role: GraphTextRole) {
  const style = options.typography?.[role];
  if (!style || !Object.values(style).some((value) => value != null)) return {};
  return {
    fontFamily: `"${graphFontFamilies[style.font_family ?? "sans"]}"`,
    ...(style.font_size_pt != null
      ? { fontSize: `${style.font_size_pt}pt` }
      : {}),
    ...(style.font_weight ? { fontWeight: style.font_weight } : {}),
    ...(style.font_style ? { fontStyle: style.font_style } : {}),
    ...(style.color ? { color: style.color } : {}),
  };
}

export function canvasGraphText(
  options: GraphOptions,
  role: GraphTextRole,
  basePixels: number,
  baseColor: string,
  baseWeight = "normal",
) {
  const style = options.typography?.[role];
  const specified =
    style && Object.values(style).some((value) => value != null);
  const pixels = ((style?.font_size_pt ?? basePixels * 0.75) * 4) / 3;
  const family = specified
    ? `"${graphFontFamilies[style.font_family ?? "sans"]}"`
    : "Inter, system-ui";
  return {
    font: `${style?.font_style ?? "normal"} ${style?.font_weight ?? baseWeight} ${pixels}px ${family}`,
    pixels,
    color: style?.color ?? baseColor,
  };
}

export function nativePlotPadding(
  options: GraphOptions,
  yTickWidth = 0,
  endTickWidth = 0,
) {
  const axis = canvasGraphText(options, "axis_labels", 12, "").pixels;
  const tick = canvasGraphText(options, "tick_labels", 11, "").pixels;
  return {
    left: Math.max(64, yTickWidth + axis * 1.4 + 22),
    top: Math.max(24, tick * 0.7 + 7),
    right: Math.max(24, endTickWidth / 2 + 6),
    bottom: Math.max(54, tick * 1.4 + axis * 1.4 + 18),
  };
}

export function patchGraphText(
  options: GraphOptions,
  role: GraphTextRole,
  patch: Partial<GraphTextStyle>,
): GraphOptions {
  const typography = { ...options.typography };
  const style = { ...typography[role], ...patch };
  const clean = Object.fromEntries(
    Object.entries(style).filter(([, value]) => value != null),
  );
  if (Object.keys(clean).length) typography[role] = clean;
  else delete typography[role];
  if (Object.keys(typography).length) return { ...options, typography };
  const { typography: _typography, ...rest } = options;
  return rest;
}
