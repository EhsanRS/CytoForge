export interface AutoSpillControl {
  name: string;
  primary_detector: string;
  sample_id: string;
  gate_id: string | null;
}

export function autoSpillOutputs(request: {
  kind?: "spillover" | "spectral";
  detectors: string[];
  controls: AutoSpillControl[];
}): string[] {
  return request.kind === "spectral"
    ? request.controls.map((control) => control.name)
    : request.detectors;
}

export function spectralDetectorVector(
  detectors: string[],
  values: Record<string, number>,
  defaultValue: 0 | 1,
): number[] {
  if (!detectors.some((detector) => Object.hasOwn(values, detector))) return [];
  return detectors.map((detector) => {
    const value = Object.hasOwn(values, detector)
      ? values[detector]
      : defaultValue;
    if (!Number.isFinite(value) || (defaultValue === 1 && value <= 0))
      throw new Error(
        `${detector}: ${defaultValue === 1 ? "inverse variance must be finite and positive" : "background must be finite"}.`,
      );
    return value;
  });
}

export function selectAutoSpillDetectors(
  detectors: string[],
  controls: AutoSpillControl[],
  kind: "spillover" | "spectral",
): AutoSpillControl[] {
  if (kind === "spectral")
    return controls.map((control) => ({
      ...control,
      primary_detector: detectors.includes(control.primary_detector)
        ? control.primary_detector
        : (detectors[0] ?? ""),
    }));
  return detectors.map(
    (detector) =>
      controls.find((control) => control.primary_detector === detector) ?? {
        name: detector,
        primary_detector: detector,
        sample_id: "",
        gate_id: null,
      },
  );
}

export function spectralControlNames(
  controls: AutoSpillControl[],
  acquired: string[],
): AutoSpillControl[] {
  const used = new Set(acquired);
  return controls.map((control) => {
    const base = used.has(control.name)
      ? `Unmixed ${control.name}`
      : control.name;
    let name = base.slice(0, 150);
    let suffix = 2;
    while (used.has(name)) name = `${base.slice(0, 150)} ${suffix++}`;
    used.add(name);
    return { ...control, name };
  });
}
