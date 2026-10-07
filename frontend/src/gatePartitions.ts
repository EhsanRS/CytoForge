import type { Gate, GateDimension, GatePartition } from "./types";
import { id } from "./types";

export function linkedPartition(
  gate: Gate,
  kind: Exclude<GatePartition["kind"], "spider" | "curly">,
  dimensions: GateDimension[],
  thresholds: number[],
): Gate {
  return {
    ...gate,
    kind: "hyperrectangle",
    bounds: [],
    partition: { id: id(), kind, member: 1 },
    dimensions: dimensions.map((dimension, axis) => ({
      ...dimension,
      minimum: kind === "quadrant" && axis === 1 ? thresholds[axis] : null,
      maximum: kind === "quadrant" && axis === 1 ? null : thresholds[axis],
    })),
  };
}

export function partitionSize(gate: Gate): number {
  return gate.partition?.kind === "bisector" ? 2 : 4;
}
