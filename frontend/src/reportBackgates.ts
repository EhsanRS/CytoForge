import { dimensionForAxis } from "./coordinates";
import type {
  Gate,
  GateDimension,
  PlotDefinition,
  ReportLayout,
  ReportPage,
  Workspace,
} from "./types";
import { id } from "./types";

type CurrentPlot = Omit<PlotDefinition, "id" | "title">;
const displayDimension = (dimension?: GateDimension | null) =>
  dimension
    ? { ...structuredClone(dimension), minimum: null, maximum: null }
    : null;

export function backgateAncestryPlots(
  workspace: Workspace,
  current: CurrentPlot,
): PlotDefinition[] {
  const identifier = current.backgate_id ?? current.gate_id;
  const lookup = new Map(workspace.gates.map((gate) => [gate.id, gate]));
  const selected = identifier ? lookup.get(identifier) : undefined;
  if (!selected || selected.sample_id !== current.sample_id)
    throw new Error(
      "Select a backgate population in this acquisition before adding ancestry.",
    );
  const chain: Gate[] = [];
  const visited = new Set<string>();
  let step: Gate | undefined = selected;
  while (step) {
    if (visited.has(step.id))
      throw new Error("This population ancestry contains a cycle.");
    if (step.sample_id !== current.sample_id)
      throw new Error("Population ancestry belongs to another acquisition.");
    visited.add(step.id);
    chain.push(step);
    const parent: Gate | undefined = step.parent_id
      ? lookup.get(step.parent_id)
      : undefined;
    if (step.parent_id && !parent)
      throw new Error("A parent population is unavailable.");
    step = parent;
  }
  return chain.reverse().map((gate) => {
    const geometric = !!gate.x || !!gate.dimensions?.length;
    const dimensions = gate.dimensions ?? [];
    const x = geometric ? (dimensions[0]?.channel ?? gate.x!) : current.x;
    const y = geometric
      ? (dimensions[1]?.channel ?? gate.y ?? null)
      : current.y;
    const volume = dimensions.length >= 3;
    const mode = !geometric
      ? current.mode
      : volume
        ? "3d"
        : !y
          ? "histogram"
          : ["histogram", "cdf", "3d"].includes(current.mode)
            ? "density"
            : current.mode;
    const xd = geometric ? dimensionForAxis(gate, x, 0) : current.x_dimension;
    const yd = geometric
      ? dimensionForAxis(gate, y ?? undefined, 1)
      : current.y_dimension;
    return {
      ...structuredClone(current),
      id: id(),
      title: "",
      gate_id: gate.parent_id ?? null,
      backgate_id: selected.id,
      coordinate_gate_id: geometric
        ? gate.id
        : (current.coordinate_gate_id ?? null),
      x,
      y,
      mode,
      x_dimension: displayDimension(xd),
      y_dimension: y ? displayDimension(yd) : null,
      x_transform: geometric
        ? (xd?.transform ?? gate.x_transform)
        : current.x_transform,
      y_transform: y
        ? geometric
          ? (yd?.transform ?? gate.y_transform)
          : current.y_transform
        : null,
      three_d: volume
        ? {
            ...structuredClone(current.three_d ?? {}),
            z: dimensions[2].channel,
            z_dimension: displayDimension(dimensions[2]),
            z_transform: dimensions[2].transform,
          }
        : mode === "3d"
          ? structuredClone(current.three_d)
          : null,
      bounds: null,
      overlays: [],
      normalization: "count",
      show_gates: true,
    };
  });
}

export function backgateAncestryPlacement(
  layout: ReportLayout,
  count: number,
  geometry: ReportPage,
  orientation: "horizontal" | "vertical",
) {
  if (!Number.isInteger(count) || count < 1)
    throw new Error("An ancestry must contain at least one population.");
  const margin = geometry.margin_mm;
  const top = margin + (layout.show_header ? 24 : 0);
  const availableWidth = geometry.width_mm - 2 * margin;
  const availableHeight =
    geometry.height_mm - margin - top - (layout.show_footer ? 12 : 0);
  if (
    ![availableWidth, availableHeight, margin].every(Number.isFinite) ||
    margin < 0 ||
    availableWidth < 60 ||
    availableHeight < 75
  )
    throw new Error(
      "Increase this page's usable area to fit ancestry figures.",
    );
  const width = Math.min(90, availableWidth);
  const height = Math.min(95, availableHeight);
  const columns =
    orientation === "vertical"
      ? 1
      : Math.max(1, Math.floor((availableWidth + 6) / (width + 6)));
  const rows = Math.max(1, Math.floor((availableHeight + 6) / (height + 6)));
  const perPage = columns * rows;
  const firstPage =
    layout.elements.length === 0 && layout.pages.length === 1
      ? 0
      : layout.pages.length;
  const needed = Math.ceil(count / perPage);
  if (layout.elements.length + count > 256 || firstPage + needed > 32)
    throw new Error(
      "This ancestry exceeds the layout's 256 figures or 32 pages. Use another layout.",
    );
  const pages = structuredClone(layout.pages);
  for (let page = pages.length; page < firstPage + needed; page++)
    pages.push(structuredClone(geometry));
  if (firstPage === 0) pages[0] = structuredClone(geometry);
  return {
    pages,
    placements: Array.from({ length: count }, (_, index) => ({
      page: firstPage + Math.floor(index / perPage),
      x_mm: margin + (index % columns) * (width + 6),
      y_mm: top + Math.floor((index % perPage) / columns) * (height + 6),
      width_mm: width,
      height_mm: height,
    })),
  };
}
