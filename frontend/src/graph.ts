export const palettes: Record<string, number[][]> = {
  ocean: [
    [28, 48, 73],
    [38, 87, 132],
    [39, 153, 173],
    [49, 204, 168],
    [175, 234, 158],
    [255, 228, 147],
  ],
  gray: [
    [40, 40, 40],
    [245, 245, 245],
  ],
  spectrum: [
    [63, 28, 122],
    [28, 87, 183],
    [20, 173, 178],
    [66, 191, 90],
    [246, 220, 55],
    [232, 86, 38],
  ],
  viridis: [
    [68, 1, 84],
    [59, 82, 139],
    [33, 145, 140],
    [94, 201, 98],
    [253, 231, 37],
  ],
};
export function densityColor(value: number, palette = "ocean") {
  const colors = palettes[palette] ?? palettes.ocean;
  const p = Math.min(0.999999, Math.max(0, value)) * (colors.length - 1),
    i = Math.floor(p);
  return colors[i].map((c, j) =>
    Math.round(c + (colors[i + 1][j] - c) * (p - i)),
  );
}
export function axisFraction(value: number, low: number, high: number) {
  const span = high - low,
    difference = value - low;
  if (Number.isFinite(span) && Number.isFinite(difference) && span > 0)
    return difference / span;
  const scale = Math.max(
    Math.abs(low),
    Math.abs(high),
    Math.abs(value),
    1e-300,
  );
  return (value / scale - low / scale) / (high / scale - low / scale);
}
export function axisValue(low: number, high: number, fraction: number) {
  const span = high - low;
  const direct = low + fraction * span;
  if (Number.isFinite(span) && Number.isFinite(direct)) return direct;
  const scale = Math.max(Math.abs(low), Math.abs(high), 1e-300);
  const normalized = (1 - fraction) * (low / scale) + fraction * (high / scale);
  const maximum = Number.MAX_VALUE / scale;
  return Math.max(-maximum, Math.min(maximum, normalized)) * scale;
}
