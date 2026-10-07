export interface BackgateCounts {
  backgate_count?: number;
  backgate_visible_count?: number;
  backgate_displayed_count?: number;
  backgate_sampling?: string | null;
}

export function backgateSummary(
  data: BackgateCounts | null | undefined,
  format: (count: number) => string,
): string {
  if (data?.backgate_visible_count == null) return "";
  return ` · ${format(data.backgate_count ?? 0)} backgate finite · ${format(data.backgate_visible_count)} in axes · ${format(data.backgate_displayed_count ?? 0)} ${data.backgate_sampling ? "sampled highlights" : "highlights"}`;
}

export function drawBackgatePoints(
  context: CanvasRenderingContext2D,
  points: [number, number][],
  px: (value: number) => number,
  py: ((value: number) => number) | null,
  bottom: number,
): void {
  context.save();
  if (py) {
    context.fillStyle = "#f0b96ae0";
    for (const [x, y] of points)
      context.fillRect(px(x) - 1, py(y) - 1, 2.4, 2.4);
  } else {
    context.strokeStyle = "#f0b96ae0";
    context.lineWidth = 1;
    context.beginPath();
    for (const [x] of points) {
      context.moveTo(px(x), bottom - 1);
      context.lineTo(px(x), bottom - 7);
    }
    context.stroke();
  }
  context.restore();
}
