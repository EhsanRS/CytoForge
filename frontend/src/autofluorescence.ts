export function afReferenceSettings(selected: string[], outputs: string[]) {
  const names = selected.map((name) => name.trim());
  if (
    names.some((name) => !name || !outputs.includes(name)) ||
    new Set(names).size !== names.length
  )
    throw new Error(
      "Choose distinct AF outputs from the named spectral controls.",
    );
  const ordered = outputs.filter((name) => names.includes(name));
  return ordered.length === 1
    ? { af_output: ordered[0], af_outputs: [] as string[] }
    : { af_output: null, af_outputs: ordered };
}

export function savedAFReferences(request: {
  controls: { name: string }[];
  af_output?: string | null;
  af_outputs?: string[];
}) {
  if (request.af_output && request.af_outputs?.length)
    throw new Error("Saved calculation has conflicting AF reference formats.");
  const selection = request.af_outputs?.length
    ? request.af_outputs
    : request.af_output
      ? [request.af_output]
      : [];
  const settings = afReferenceSettings(
    selection,
    request.controls.map((c) => c.name),
  );
  return settings.af_output ? [settings.af_output] : settings.af_outputs;
}

export interface AFReferenceReview {
  output: string;
  closest_output: string | null;
  weighted_cosine: number | null;
  orthogonal_fraction: number;
  separation_angle_degrees: number;
  weakly_separated: boolean;
}

export function reviewedAFReferences(
  value: unknown,
  outputs: string[],
): AFReferenceReview[] | null {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) return null;
  const names = new Set<string>();
  for (const row of value) {
    if (
      row === null ||
      typeof row !== "object" ||
      typeof row.output !== "string" ||
      !outputs.includes(row.output) ||
      names.has(row.output) ||
      (row.closest_output !== null &&
        (!outputs.includes(row.closest_output) ||
          row.closest_output === row.output)) ||
      (row.weighted_cosine !== null &&
        (typeof row.weighted_cosine !== "number" ||
          !Number.isFinite(row.weighted_cosine) ||
          Math.abs(row.weighted_cosine) > 1)) ||
      (row.closest_output === null) !== (row.weighted_cosine === null) ||
      typeof row.orthogonal_fraction !== "number" ||
      !Number.isFinite(row.orthogonal_fraction) ||
      row.orthogonal_fraction < 0 ||
      row.orthogonal_fraction > 1 ||
      typeof row.separation_angle_degrees !== "number" ||
      !Number.isFinite(row.separation_angle_degrees) ||
      row.separation_angle_degrees < 0 ||
      row.separation_angle_degrees > 90 ||
      typeof row.weakly_separated !== "boolean" ||
      row.weakly_separated !== row.orthogonal_fraction < 0.1 ||
      Math.abs(
        row.separation_angle_degrees -
          (Math.asin(row.orthogonal_fraction) * 180) / Math.PI,
      ) > 1e-6
    )
      return null;
    names.add(row.output);
  }
  return value as AFReferenceReview[];
}
