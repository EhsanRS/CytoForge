import type { Compensation } from "./types";

export interface SpreadControl {
  output: string;
  sample_id: string;
  gate_id: string | null;
}
export interface SpreadPreset {
  name: string;
  controls: SpreadControl[];
  quantiles: number;
  events_per_bin: number;
  significance: number;
  max_events: number | null;
}
const record = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
const validId = (value: unknown): value is string =>
  typeof value === "string" && /^[a-f0-9]{32}$/.test(value);
const population = (
  value: unknown,
): { sample_id: string; gate_id: string | null } | null => {
  const p = record(value);
  if (
    !p ||
    !validId(p.sample_id) ||
    (p.gate_id != null && !validId(p.gate_id)) ||
    p.threshold_channel != null
  )
    return null;
  return {
    sample_id: p.sample_id,
    gate_id: typeof p.gate_id === "string" ? p.gate_id : null,
  };
};

export function spreadingPreset(
  value: unknown,
  outputs: string[],
): SpreadPreset | null {
  const request = record(record(value)?.request);
  if (
    !request ||
    request.algorithm !== "autospread" ||
    typeof request.name !== "string" ||
    !request.name.trim() ||
    !Array.isArray(request.controls) ||
    !request.controls.length
  )
    return null;
  const controls: SpreadControl[] = [];
  for (const item of request.controls) {
    const c = record(item),
      p = population(item);
    if (
      !c ||
      !p ||
      typeof c.output !== "string" ||
      !outputs.includes(c.output) ||
      controls.some((v) => v.output === c.output)
    )
      return null;
    controls.push({ output: c.output, ...p });
  }
  const quantiles = request.quantiles,
    events = request.events_per_bin,
    significance = request.significance,
    limit = request.max_events;
  if (
    typeof quantiles !== "number" ||
    !Number.isInteger(quantiles) ||
    quantiles < 8 ||
    quantiles > 256 ||
    typeof events !== "number" ||
    !Number.isInteger(events) ||
    events < 20 ||
    events > 10000 ||
    typeof significance !== "number" ||
    !Number.isFinite(significance) ||
    significance <= 0 ||
    significance >= 1 ||
    (limit !== null &&
      (typeof limit !== "number" ||
        !Number.isInteger(limit) ||
        limit < 8 * events ||
        limit > 2000000))
  )
    return null;
  return {
    name: request.name,
    controls,
    quantiles,
    events_per_bin: events,
    significance,
    max_events: limit,
  };
}

export function initialSpreadingControls(
  matrix: Compensation,
): SpreadControl[] {
  const provenance = matrix.provenance ?? {};
  const saved = spreadingPreset(provenance.autospread, matrix.outputs);
  const captured = Array.isArray(provenance.control_populations)
    ? provenance.control_populations.map(record).filter((v) => v !== null)
    : [];
  const request = record(provenance.request);
  const entries = Array.isArray(request?.controls)
    ? request.controls.map(record).filter((v) => v !== null)
    : [];
  const af = record(request?.autofluorescence);
  const multipleAF = Array.isArray(request?.autofluorescence_controls)
    ? request.autofluorescence_controls.map(record).filter((v) => v !== null)
    : [];
  const afReferences = multipleAF.length ? multipleAF : af ? [af] : [];
  return matrix.outputs.map((output) => {
    const previous = saved?.controls.find((c) => c.output === output);
    if (previous) return { ...previous };
    let inferred: ReturnType<typeof population> = null;
    if (provenance.kind === "autospill_calculation") {
      const candidates = captured.filter((c) => c.output === output);
      inferred =
        (candidates.length === 1 ? population(candidates[0]) : null) ??
        population(
          entries.find((c) =>
            matrix.kind === "spectral"
              ? c.name === output
              : c.primary_detector === output,
          ),
        );
    } else if (provenance.kind === "control_calculation") {
      const candidates = captured.filter((c) => c.output === output);
      if (candidates.length === 1) inferred = population(candidates[0]);
      const row = entries.find((c) =>
        matrix.kind === "spectral"
          ? c.name === output
          : c.primary_detector === output,
      );
      inferred ??= population(row?.positive);
      if (!inferred && matrix.kind === "spectral") {
        const matches = afReferences.filter(
          (reference) => reference.name === output,
        );
        if (matches.length === 1) inferred = population(matches[0].population);
      }
    }
    return {
      output,
      sample_id: inferred?.sample_id ?? "",
      gate_id: inferred?.gate_id ?? null,
    };
  });
}
