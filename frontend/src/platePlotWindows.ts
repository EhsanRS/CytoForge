import type { DesktopPlotState, PlateDefinition, Workspace } from "./types";
import { plateDimensions, platePosition, wellName } from "./plateGeometry.js";

export function plateAcquisitionPlot(
  workspace: Workspace,
  plate: PlateDefinition,
  well: string,
  sampleId: string,
): DesktopPlotState {
  const [rows, columns] = plateDimensions(plate);
  const [row, column] = platePosition(well);
  const canonical = wellName(row, column);
  if (
    row >= rows ||
    column >= columns ||
    !plate.assignments[canonical]?.includes(sampleId)
  ) {
    throw new Error("Choose an acquisition assigned to this well.");
  }
  const sample = workspace.samples.find((item) => item.id === sampleId);
  if (!sample) throw new Error("This acquisition is no longer available.");
  const x = sample.channels[0]?.name;
  const y = sample.channels[1]?.name ?? null;
  if (!x) throw new Error("This acquisition has no available plot parameters.");
  return {
    workspaceId: workspace.id,
    sampleId,
    gateId: null,
    coordinateGateId: null,
    backgateId: null,
    x,
    y,
    mode: y ? "density" : "histogram",
    bounds: null,
    graphOptions: {},
    bins: 160,
    groupId: null,
    pooled: false,
    sampleFilter: "",
    xTransform: null,
    yTransform: null,
  };
}
