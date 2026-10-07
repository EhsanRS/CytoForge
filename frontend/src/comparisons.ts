import type {
  ComparisonPlotData,
  ComparisonFigureView,
  DesktopComparisonState,
  PopulationComparisonRequest,
} from "./types";

export type ComparisonPresentation = Pick<
  DesktopComparisonState,
  | "mode"
  | "smoothing"
  | "controlColor"
  | "targetColor"
  | "differenceScale"
  | "showIndividuals"
>;

export function defaultComparisonFigureView(): ComparisonFigureView {
  return {
    mode: "histogram",
    smoothing: 0,
    control_color: "#38d9ba",
    target_color: "#b595f6",
    difference_scale: 1,
    show_individuals: false,
  };
}

export function comparisonFigureView(
  view: ComparisonPresentation,
): ComparisonFigureView {
  return {
    mode: view.mode,
    smoothing: view.smoothing,
    control_color: view.controlColor,
    target_color: view.targetColor,
    difference_scale: view.differenceScale,
    show_individuals: view.showIndividuals,
  };
}

export function uniqueComparisonPopulations(
  inputs: PopulationComparisonRequest["inputs"],
) {
  const counts = new Map<string, number>();
  for (const source of inputs)
    counts.set(source.sample_id, (counts.get(source.sample_id) ?? 0) + 1);
  return Object.fromEntries(
    inputs
      .filter((source) => counts.get(source.sample_id) === 1)
      .map((source) => [source.sample_id, source.gate_id]),
  );
}

export function cumulativeFraction(values: number[]) {
  let total = 0;
  return values.map((value) => (total += value));
}

export function smoothComparison(values: number[], sigma: number) {
  if (!sigma) return values;
  const radius = Math.ceil(3 * sigma);
  const weights = Array.from({ length: 2 * radius + 1 }, (_, i) =>
    Math.exp(-0.5 * ((i - radius) / sigma) ** 2),
  );
  const weight = weights.reduce((a, b) => a + b, 0);
  return values.map((_, i) => {
    let total = 0;
    for (let j = -radius; j <= radius; j++)
      total +=
        values[Math.max(0, Math.min(values.length - 1, i + j))] *
        weights[j + radius];
    return total / weight;
  });
}

// Geometry and Gaussian boundary rules also define portable comparison figures.
export function comparisonGeometry(
  data: ComparisonPlotData,
  view: ComparisonPresentation,
) {
  const main =
    view.mode === "cdf"
      ? [data.control_cdf, data.target_cdf]
      : view.mode === "difference"
        ? [smoothComparison(data.difference, view.smoothing)]
        : [
            smoothComparison(data.control_fraction, view.smoothing),
            smoothComparison(data.target_fraction, view.smoothing),
          ];
  const individuals =
    view.showIndividuals && view.mode !== "difference"
      ? data.individual_controls.map((c) =>
          view.mode === "cdf"
            ? cumulativeFraction(c.fraction)
            : smoothComparison(c.fraction, view.smoothing),
        )
      : [];
  let low = 0,
    high = 1e-12;
  for (const curve of [...main, ...individuals])
    for (const value of curve) {
      low = Math.min(low, value);
      high = Math.max(high, value);
    }
  const y = (value: number) =>
    view.mode === "difference"
      ? 215 - ((value * 160) / Math.max(high, -low)) * view.differenceScale
      : 390 - (value / high) * 300;
  const edgeScale = Math.max(...data.edges.map(Math.abs), 1e-300);
  const first = data.edges[0] / edgeScale;
  const span = data.edges.at(-1)! / edgeScale - first;
  const x = (value: number) => 65 + ((value / edgeScale - first) / span) * 690;
  const points = (values: number[]) =>
    values
      .map(
        (value, i) =>
          `${x(data.edges[i] / 2 + data.edges[i + 1] / 2)},${y(value)}`,
      )
      .join(" ");
  const curves = [
    ...individuals.map((values) => ({
      points: points(values),
      color: view.controlColor,
      width: 1,
      opacity: 0.35,
    })),
    ...main.map((values, i) => ({
      points: points(values),
      color:
        view.mode === "difference"
          ? "#edb96c"
          : i
            ? view.targetColor
            : view.controlColor,
      width: 2.5,
      opacity: 1,
    })),
  ];
  const ks = data.row.metrics.ks_at_coordinate;
  return { curves, ksX: typeof ks === "number" ? x(ks) : null, zeroY: 215 };
}
