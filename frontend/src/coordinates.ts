import type { Channel, Gate, GateDimension, Sample } from "./types";

export function curlyAxisEligible(
  channel: Channel | undefined,
  dimension: GateDimension | undefined,
  sample: Sample | null,
): boolean {
  if (!channel || !sample || dimension?.ratio_channels) return false;
  const spec = channel.transform;
  return (
    !["linear", "gml_linear", "wsp_log", "wsp_biex"].includes(spec.kind) &&
    spec.bound_min == null &&
    spec.bound_max == null &&
    !/^(?:fsc|ssc|time)(?:$|[-_\s])/i.test(channel.name) &&
    !sample.derived_parameters.some((d) => d.name === channel.name) &&
    !sample.computed_parameters.some((p) => p.name === channel.name)
  );
}

export function dimensionForAxis(
  gate: Gate | undefined,
  channel: string | undefined,
  axis: number,
): GateDimension | undefined {
  if (!gate || !channel) return undefined;
  const ordered = gate.dimensions?.[axis];
  if (ordered?.channel === channel) return ordered;
  const candidates =
    gate.dimensions?.filter((d) => d.channel === channel) ?? [];
  const key = (d: GateDimension) =>
    JSON.stringify({
      ...d,
      minimum: null,
      maximum: null,
    });
  return candidates.every((d) => key(d) === key(candidates[0]))
    ? candidates[0]
    : undefined;
}

export function channelForAxis(
  gate: Gate | undefined,
  channel: Channel | undefined,
  axis: number,
  explicit?: GateDimension | null,
): Channel | undefined {
  const dimension = explicit ?? dimensionForAxis(gate, channel?.name, axis);
  return channel && dimension
    ? {
        ...channel,
        label: dimension.ratio_channels?.join(" / ") ?? channel.label,
        transform: dimension.transform,
      }
    : channel;
}

export function virtualDimension(
  sample: Sample,
  name: string,
  dimensions: (GateDimension | null | undefined)[],
): GateDimension | null {
  if (sample.channels.some((c) => c.name === name)) return null;
  const candidates = dimensions.filter(
    (d): d is GateDimension => !!d && d.channel === name,
  );
  const key = (d: GateDimension) =>
    JSON.stringify({ ...d, minimum: null, maximum: null });
  return candidates.length &&
    candidates.every((d) => key(d) === key(candidates[0]))
    ? { ...candidates[0], minimum: null, maximum: null }
    : null;
}
