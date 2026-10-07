import type { GraphGateStyle, GraphOptions } from "./types";

export function patchGraphGateStyle(
  options: GraphOptions,
  patch: Partial<GraphGateStyle>,
): GraphOptions {
  const style = Object.fromEntries(
    Object.entries({ ...options.gate_style, ...patch }).filter(
      ([, value]) => value != null,
    ),
  );
  if (Object.keys(style).length) return { ...options, gate_style: style };
  const { gate_style: _gateStyle, ...rest } = options;
  return rest;
}

export function strokeAndFillGate(
  context: CanvasRenderingContext2D,
  options: GraphOptions,
  color: string,
  region?: {
    outer: number[][];
    holes: number[][][];
    bounds: [number, number, number, number];
  },
) {
  const style = options.gate_style;
  if (region && (style?.fill_opacity ?? 0) > 0) {
    const ring = (points: number[][]) => {
      const path = new Path2D();
      points.forEach(([x, y], index) =>
        index ? path.lineTo(x, y) : path.moveTo(x, y),
      );
      path.closePath();
      return path;
    };
    context.save();
    context.globalAlpha *= style!.fill_opacity!;
    context.fillStyle =
      style?.fill_color ??
      (/^#[0-9a-fA-F]{6}$/.test(color) ? color : "#65788c");
    context.clip(ring(region.outer), "evenodd");
    for (const hole of region.holes) {
      const complement = new Path2D();
      complement.rect(...region.bounds);
      complement.addPath(ring(hole));
      context.clip(complement, "evenodd");
    }
    context.fillRect(...region.bounds);
    context.restore();
  }
  context.lineWidth = style?.line_width_px ?? context.lineWidth;
  context.stroke();
}
