import { useState } from "react";
import {
  ArrowLeft,
  Check,
  FlaskConical,
  LoaderCircle,
  Plus,
  Trash2,
} from "lucide-react";
import { post } from "../api";
import {
  acquisitionChannels,
  formatNumber,
  type Compensation,
  type Workspace,
} from "../types";
import { Modal, Tag } from "./Common";
import type { Commit } from "./Editors";
import { AutoSpillWizard, type ControlWizardProps } from "./AutoSpillWizard";
import { AutofluorescenceReview } from "./AutofluorescenceReview";

interface Population {
  sample_id: string;
  gate_id: string | null;
  threshold_channel: string | null;
  minimum: number | null;
  maximum: number | null;
}
interface Control {
  name: string;
  primary_detector: string | null;
  positive: Population;
  negative: Population;
}
interface PopulationDiagnostic {
  count: number;
  finite_count: number;
  median: number[];
  robust_sd: number[];
}
interface Preview {
  x: string;
  y: string;
  positive: [number, number][];
  negative: [number, number][];
}
interface ControlDiagnostic {
  name: string;
  output: string;
  positive: PopulationDiagnostic;
  negative: PopulationDiagnostic | null;
  delta: number[];
  signature: number[];
  normalization_detector: string;
  normalization_signal: number;
  separation_robust_sd: number | null;
  preview: Preview | null;
}
interface Calculation {
  revision: number;
  compensation: Compensation;
  diagnostics: {
    condition_number: number;
    rank: number;
    output_units: string;
    signature_cosines: number[][];
    controls: ControlDiagnostic[];
    autofluorescence_sources?: unknown;
  };
  warnings: string[];
}
const emptyPopulation = (): Population => ({
  sample_id: "",
  gate_id: null,
  threshold_channel: null,
  minimum: null,
  maximum: null,
});
const defaultControl = (name: string, primary: string | null): Control => ({
  name,
  primary_detector: primary,
  positive: emptyPopulation(),
  negative: emptyPopulation(),
});

function PopulationPicker({
  workspace,
  value,
  label,
  onChange,
}: {
  workspace: Workspace;
  value: Population;
  label: string;
  onChange: (value: Population) => void;
}) {
  const sample = workspace.samples.find((s) => s.id === value.sample_id);
  const mode =
    value.minimum != null ? "above" : value.maximum != null ? "below" : "all";
  return (
    <fieldset className="control-population">
      <legend>{label}</legend>
      <label className="field">
        Sample
        <select
          aria-label={`${label} sample`}
          value={value.sample_id}
          onChange={(e) =>
            onChange({ ...emptyPopulation(), sample_id: e.target.value })
          }
        >
          <option value="">Select control sample</option>
          {workspace.samples.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        Cleanup population
        <select
          aria-label={`${label} population`}
          value={value.gate_id ?? ""}
          disabled={!sample}
          onChange={(e) =>
            onChange({ ...value, gate_id: e.target.value || null })
          }
        >
          <option value="">All events</option>
          {workspace.gates
            .filter((g) => g.sample_id === sample?.id)
            .map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
        </select>
      </label>
      <label className="field">
        Raw intensity selection
        <select
          aria-label={`${label} selection`}
          value={mode}
          disabled={!sample}
          onChange={(e) =>
            onChange({
              ...value,
              threshold_channel:
                e.target.value === "all"
                  ? null
                  : (acquisitionChannels(sample!)[0]?.name ?? null),
              minimum: e.target.value === "above" ? 0 : null,
              maximum: e.target.value === "below" ? 0 : null,
            })
          }
        >
          <option value="all">Whole population</option>
          <option value="above">At or above threshold</option>
          <option value="below">Below threshold</option>
        </select>
      </label>
      {mode !== "all" && sample && (
        <div className="field-row">
          <label className="field">
            Detector
            <select
              aria-label={`${label} threshold detector`}
              value={value.threshold_channel ?? ""}
              onChange={(e) =>
                onChange({ ...value, threshold_channel: e.target.value })
              }
            >
              {acquisitionChannels(sample).map((c) => (
                <option key={c.name}>{c.name}</option>
              ))}
            </select>
          </label>
          <label className="field">
            Threshold
            <input
              aria-label={`${label} threshold`}
              type="number"
              step="any"
              value={value.minimum ?? value.maximum ?? ""}
              onChange={(e) =>
                onChange({
                  ...value,
                  [mode === "above" ? "minimum" : "maximum"]:
                    e.target.value === "" ? NaN : Number(e.target.value),
                })
              }
            />
          </label>
        </div>
      )}
    </fieldset>
  );
}

function ControlScatter({ preview }: { preview: Preview }) {
  const points = [...preview.positive, ...preview.negative];
  const bounds = [0, 1].map((axis) => {
    const values = points.map((p) => p[axis]);
    const lo = Math.min(...values),
      hi = Math.max(...values);
    const pad = (hi - lo) * 0.08 || Math.max(1, Math.abs(lo) * 0.08);
    return [lo - pad, hi + pad];
  });
  const position = (value: number, axis: number) =>
    (value - bounds[axis][0]) / (bounds[axis][1] - bounds[axis][0]);
  return (
    <svg
      className="control-scatter"
      viewBox="0 0 300 210"
      role="img"
      aria-label={`Raw positive and negative controls: ${preview.x} versus ${preview.y}`}
    >
      <path d="M48 16V166H282" fill="none" stroke="currentColor" opacity=".3" />
      {[preview.negative, preview.positive].map((population, k) => (
        <g key={k} fill={k ? "#38d9ba" : "#8196bd"} opacity=".65">
          {population.map((p, i) => (
            <circle
              key={i}
              cx={48 + 232 * position(p[0], 0)}
              cy={166 - 148 * position(p[1], 1)}
              r="2.3"
            />
          ))}
        </g>
      ))}
      <text x="164" y="200" textAnchor="middle">
        {preview.x} (raw)
      </text>
      <text transform="translate(13 94) rotate(-90)" textAnchor="middle">
        {preview.y} (raw)
      </text>
      <text x="48" y="180" fontSize="9">
        {formatNumber(bounds[0][0], 0)}
      </text>
      <text x="282" y="180" textAnchor="end" fontSize="9">
        {formatNumber(bounds[0][1], 0)}
      </text>
      <text x="43" y="20" textAnchor="end" fontSize="9">
        {formatNumber(bounds[1][1], 0)}
      </text>
    </svg>
  );
}

function SignaturePlot({
  detectors,
  values,
}: {
  detectors: string[];
  values: number[];
}) {
  const low = Math.min(0, ...values),
    high = Math.max(1, ...values);
  const point = (v: number, i: number) =>
    `${40 + (242 * i) / Math.max(1, values.length - 1)},${135 - ((v - low) / (high - low)) * 112}`;
  return (
    <svg
      className="signature-plot"
      viewBox="0 0 300 190"
      role="img"
      aria-label="Normalized spectral reference signature"
    >
      <path
        d={`M40 23V135H282`}
        stroke="currentColor"
        opacity=".3"
        fill="none"
      />
      <polyline
        points={values.map(point).join(" ")}
        stroke="#38d9ba"
        strokeWidth="2"
        fill="none"
      />
      {values.map((v, i) => {
        const [x, y] = point(v, i).split(",");
        return (
          <g key={i}>
            <circle cx={x} cy={y} r="3" fill="#38d9ba" />
            {detectors.length <= 12 && (
              <text transform={`translate(${x} 147) rotate(35)`} fontSize="9">
                {detectors[i]}
              </text>
            )}
          </g>
        );
      })}
      <text x="34" y="27" textAnchor="end">
        {formatNumber(high, 2)}
      </text>
      <text x="34" y="138" textAnchor="end">
        {formatNumber(low, 2)}
      </text>
    </svg>
  );
}

function MedianCompensationWizard({
  workspace,
  selected,
  commit,
  onClose,
  onSaved,
  onAutoSpill,
}: {
  workspace: Workspace;
  selected: string[];
  commit: Commit;
  onClose: () => void;
  onSaved: (matrix: Compensation) => void;
  onAutoSpill: () => void;
}) {
  const available = acquisitionChannels(
    workspace.samples.find((s) => s.id === selected[0]) ?? workspace.samples[0],
  );
  const initial = available
    .filter((c) => !/^(fsc|ssc|time)/i.test(c.name))
    .map((c) => c.name);
  const [kind, setKind] = useState<"spillover" | "spectral">("spillover");
  const [name, setName] = useState("Single-stain compensation");
  const [detectors, setDetectors] = useState(
    initial.length ? initial : available.map((c) => c.name),
  );
  const [controls, setControls] = useState<Control[]>(() =>
    (initial.length ? initial : available.map((c) => c.name)).map((d) =>
      defaultControl(d, d),
    ),
  );
  const [negative, setNegative] = useState(emptyPopulation);
  const [minEvents, setMinEvents] = useState(100);
  const [background, setBackground] = useState("");
  const [weights, setWeights] = useState("");
  const [useAF, setUseAF] = useState(false);
  const [afReferences, setAFReferences] = useState(() => [
    { name: "Autofluorescence", population: emptyPopulation() },
  ]);
  const [result, setResult] = useState<Calculation | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const [targets, setTargets] = useState(selected);
  const [savePopulations, setSavePopulations] = useState(true);
  const configure = (next: string[], nextKind = kind) => {
    setDetectors(next);
    setKind(nextKind);
    setResult(null);
    setError("");
    setControls((previous) =>
      nextKind === "spillover"
        ? next.map(
            (d) =>
              previous.find((c) => c.primary_detector === d) ??
              defaultControl(d, d),
          )
        : previous
            .slice(0, Math.max(1, Math.min(2, next.length)))
            .map((c, i) => ({
              ...c,
              name: `Unmixed ${i + 1}`,
              primary_detector: null,
            })),
    );
    if (nextKind !== "spectral") setUseAF(false);
  };
  const update = (index: number, control: Control) =>
    setControls(controls.map((c, i) => (i === index ? control : c)));
  const vector = (input: string, label: string) => {
    if (!input.trim()) return [];
    const values = input
      .trim()
      .split(/[\s,;]+/)
      .map(Number);
    if (values.length !== detectors.length || !values.every(Number.isFinite))
      throw new Error(
        `${label}: enter one finite value per selected detector, in detector order`,
      );
    return values;
  };
  const calculate = async () => {
    setWorking(true);
    setError("");
    try {
      if (!name.trim() || !detectors.length || !controls.length)
        throw new Error(
          "Enter a matrix name, select detectors and add controls",
        );
      const populations = controls
        .flatMap((c) => [c.positive, c.negative])
        .concat(
          kind === "spectral" && useAF
            ? afReferences.map((r) => r.population)
            : [],
        );
      if (populations.some((p) => !p.sample_id))
        throw new Error(
          "Select a sample for every positive and negative control",
        );
      if (
        populations.some((p) =>
          [p.minimum, p.maximum].some((v) => v != null && !Number.isFinite(v)),
        )
      )
        throw new Error("Thresholds must contain finite numbers");
      const value = await post<Calculation>(
        `/workspaces/${workspace.id}/compensations/calculate`,
        {
          revision: workspace.revision,
          name: name.trim(),
          kind,
          detectors,
          controls: controls.map((c) => ({ ...c, name: c.name.trim() })),
          min_events: minEvents,
          background:
            kind === "spectral"
              ? vector(background, "Electronic background")
              : [],
          weights:
            kind === "spectral" ? vector(weights, "Detector weights") : [],
          autofluorescence:
            kind === "spectral" && useAF && afReferences.length === 1
              ? {
                  name: afReferences[0].name.trim(),
                  population: afReferences[0].population,
                }
              : null,
          autofluorescence_controls:
            kind === "spectral" && useAF && afReferences.length > 1
              ? afReferences.map((r) => ({ ...r, name: r.name.trim() }))
              : [],
        },
      );
      setResult(value);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setWorking(false);
    }
  };
  const save = async () => {
    if (!result) return;
    setWorking(true);
    setError("");
    try {
      const state = await commit(
        "/compensations",
        {
          compensation: result.compensation,
          sample_ids: targets,
          save_control_populations: savePopulations,
        },
        "Control matrix saved and applied",
      );
      onSaved(
        state.compensations.find((c) => c.id === result.compensation.id)!,
      );
      onClose();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setWorking(false);
    }
  };
  const compatible = (sampleId: string) => {
    const sample = workspace.samples.find((s) => s.id === sampleId)!;
    const acquired = acquisitionChannels(sample).map((c) => c.name);
    return (
      detectors.every((d) => acquired.includes(d)) &&
      (kind !== "spectral" ||
        !result ||
        result.compensation.outputs.every(
          (n) =>
            !sample.channels.some((c) => c.name === n) ||
            sample.unmixed_parameters.includes(n),
        ))
    );
  };
  const stale = result && result.revision !== workspace.revision;
  return (
    <Modal
      title="Compensation control wizard"
      subtitle={
        result
          ? "Review the calculated matrix and control diagnostics."
          : "Calculate spillover or spectral reference signatures from single-stain controls."
      }
      onClose={onClose}
      wide
    >
      <div className="compensation-wizard">
        <label className="field wizard-estimator">
          Estimation method
          <select
            aria-label="Estimation method"
            value="median"
            onChange={onAutoSpill}
            disabled={working}
          >
            <option value="median">
              Positive / negative median differences
            </option>
            <option value="autospill">
              AutoSpill · robust iterative regression
            </option>
          </select>
        </label>
        {!result ? (
          <>
            <div className="field-row">
              <label className="field">
                Matrix name
                <input
                  aria-label="Control matrix name"
                  value={name}
                  maxLength={160}
                  onChange={(e) => setName(e.target.value)}
                />
              </label>
              <label className="field">
                Correction method
                <select
                  aria-label="Correction method"
                  value={kind}
                  onChange={(e) => {
                    const next = e.target.value as typeof kind;
                    configure(detectors, next);
                    setName(
                      next === "spectral"
                        ? "Spectral reference matrix"
                        : "Single-stain compensation",
                    );
                  }}
                >
                  <option value="spillover">Conventional spillover</option>
                  <option value="spectral">Spectral unmixing</option>
                </select>
              </label>
              <label className="field">
                Minimum finite events
                <input
                  type="number"
                  min={20}
                  max={100000}
                  value={minEvents}
                  onChange={(e) => setMinEvents(Number(e.target.value))}
                />
              </label>
            </div>
            <p className="form-note">
              Controls use acquired intensities. Cleanup gates are evaluated
              without compensation. Positive and negative references should
              match in autofluorescence and material; select disjoint
              populations when using the same sample.
            </p>
            <div
              className="detector-choices"
              role="group"
              aria-label="Control detectors"
            >
              {available.map((c) => (
                <label key={c.name}>
                  <input
                    type="checkbox"
                    checked={detectors.includes(c.name)}
                    onChange={(e) =>
                      configure(
                        e.target.checked
                          ? available
                              .filter(
                                (v) =>
                                  detectors.includes(v.name) ||
                                  v.name === c.name,
                              )
                              .map((v) => v.name)
                          : detectors.filter((d) => d !== c.name),
                      )
                    }
                  />
                  {c.name}
                </label>
              ))}
            </div>
            <details className="shared-negative">
              <summary>Reuse a negative reference</summary>
              <PopulationPicker
                workspace={workspace}
                value={negative}
                label="Shared negative"
                onChange={setNegative}
              />
              <button
                className="button"
                disabled={!negative.sample_id}
                onClick={() =>
                  setControls(
                    controls.map((c) => ({ ...c, negative: { ...negative } })),
                  )
                }
              >
                Use for all controls
              </button>
            </details>
            <div className="control-cards">
              {controls.map((control, index) => (
                <section
                  className="control-card"
                  key={kind === "spillover" ? control.primary_detector : index}
                >
                  <div className="control-card-heading">
                    <Tag>{index + 1}</Tag>
                    <label className="field">
                      {kind === "spectral"
                        ? "Output parameter name"
                        : "Control label"}
                      <input
                        aria-label={`Control ${index + 1} name`}
                        value={control.name}
                        maxLength={160}
                        onChange={(e) =>
                          update(index, { ...control, name: e.target.value })
                        }
                      />
                    </label>
                    {kind === "spillover" ? (
                      <span className="muted">
                        Primary: {control.primary_detector}
                      </span>
                    ) : (
                      <button
                        className="icon-button"
                        aria-label={`Remove control ${index + 1}`}
                        onClick={() =>
                          setControls(controls.filter((_, i) => i !== index))
                        }
                      >
                        <Trash2 size={16} />
                      </button>
                    )}
                  </div>
                  <div className="control-populations">
                    <PopulationPicker
                      workspace={workspace}
                      value={control.positive}
                      label={`Control ${index + 1} positive`}
                      onChange={(p) =>
                        update(index, { ...control, positive: p })
                      }
                    />
                    <PopulationPicker
                      workspace={workspace}
                      value={control.negative}
                      label={`Control ${index + 1} negative`}
                      onChange={(p) =>
                        update(index, { ...control, negative: p })
                      }
                    />
                  </div>
                </section>
              ))}
            </div>
            {kind === "spectral" && (
              <>
                <button
                  className="button"
                  disabled={
                    controls.length + (useAF ? afReferences.length : 0) >=
                    detectors.length
                  }
                  onClick={() =>
                    setControls([
                      ...controls,
                      defaultControl(`Unmixed ${controls.length + 1}`, null),
                    ])
                  }
                >
                  <Plus size={16} />
                  Add spectral reference
                </button>
                <div className="spectral-options">
                  <div className="field-row">
                    <label className="field">
                      Electronic background (optional)
                      <input
                        aria-label="Electronic background"
                        placeholder={detectors.map(() => "0").join(", ")}
                        value={background}
                        onChange={(e) => setBackground(e.target.value)}
                      />
                    </label>
                    <label className="field">
                      Detector weights (optional)
                      <input
                        aria-label="Detector weights"
                        placeholder={detectors.map(() => "1").join(", ")}
                        value={weights}
                        onChange={(e) => setWeights(e.target.value)}
                      />
                    </label>
                  </div>
                  <p className="form-note">
                    One value per detector in the order above. Background is
                    subtracted before unmixing; weights are positive inverse
                    detector variances. Reference signatures use peak-detector
                    units and preserve negative coefficients.
                  </p>
                  <label className="checkbox-field">
                    <input
                      type="checkbox"
                      checked={useAF}
                      disabled={!useAF && controls.length >= detectors.length}
                      onChange={(e) => setUseAF(e.target.checked)}
                    />
                    Extract autofluorescence as separate outputs
                  </label>
                  {useAF && (
                    <>
                      {afReferences.map((reference, index) => (
                        <section className="control-card" key={index}>
                          <label className="field">
                            Autofluorescence output
                            <input
                              aria-label={`Autofluorescence output ${index + 1}`}
                              value={reference.name}
                              maxLength={160}
                              onChange={(e) =>
                                setAFReferences((old) =>
                                  old.map((r, i) =>
                                    i === index
                                      ? { ...r, name: e.target.value }
                                      : r,
                                  ),
                                )
                              }
                            />
                          </label>
                          <PopulationPicker
                            workspace={workspace}
                            value={reference.population}
                            label={`Autofluorescence reference ${index + 1}`}
                            onChange={(population) =>
                              setAFReferences((old) =>
                                old.map((r, i) =>
                                  i === index ? { ...r, population } : r,
                                ),
                              )
                            }
                          />
                          <button
                            type="button"
                            className="button ghost"
                            disabled={afReferences.length <= 1}
                            onClick={() =>
                              setAFReferences((old) =>
                                old.filter((_, i) => i !== index),
                              )
                            }
                          >
                            Remove AF reference
                          </button>
                        </section>
                      ))}
                      <button
                        type="button"
                        className="button"
                        disabled={
                          controls.length + afReferences.length >=
                          detectors.length
                        }
                        onClick={() =>
                          setAFReferences((old) => {
                            const names = new Set([
                              ...controls.map((c) => c.name.trim()),
                              ...old.map((r) => r.name.trim()),
                            ]);
                            let index = old.length + 1;
                            while (names.has(`Autofluorescence ${index}`))
                              index++;
                            return [
                              ...old,
                              {
                                name: `Autofluorescence ${index}`,
                                population: emptyPopulation(),
                              },
                            ];
                          })
                        }
                      >
                        Add AF reference
                      </button>
                      <p className="form-note">
                        Choose a distinct representative unstained population
                        for each AF spectrum. Each median spectrum minus
                        electronic background becomes a named reference. Review
                        separation and use matching preparation for the
                        experimental cells.
                      </p>
                    </>
                  )}
                </div>
              </>
            )}
          </>
        ) : (
          <>
            <div className="control-review-heading">
              <div>
                <span className="eyebrow">CALCULATED MATRIX</span>
                <h3>{result.compensation.name}</h3>
                <p className="form-note">{result.diagnostics.output_units}</p>
              </div>
              <Tag>
                Rank {result.diagnostics.rank} · condition{" "}
                {formatNumber(result.diagnostics.condition_number, 3)}
              </Tag>
            </div>
            <div className="table-scroll">
              <table className="matrix-table control-review-matrix">
                <thead>
                  <tr>
                    <th>Source / detector</th>
                    {detectors.map((d) => (
                      <th key={d}>{d}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.compensation.matrix.map((row, i) => (
                    <tr key={i}>
                      <th>{result.compensation.outputs[i]}</th>
                      {row.map((v, j) => (
                        <td key={j}>{formatNumber(v * 100, 4)}%</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <AutofluorescenceReview
              rows={result.diagnostics.autofluorescence_sources}
              outputs={result.compensation.outputs}
            />
            <label className="checkbox-field">
              <input
                type="checkbox"
                aria-label="Save acquired control populations"
                checked={savePopulations}
                onChange={(e) => setSavePopulations(e.target.checked)}
              />
              Save acquired control populations, including raw parents, QC flags
              and thresholds.
            </label>
            {result.warnings.length > 0 && (
              <div className="control-warnings" role="status">
                <strong>Review these diagnostics</strong>
                <ul>
                  {result.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </div>
            )}
            <div className="control-diagnostics">
              {result.diagnostics.controls.map((c, i) => (
                <section className="control-diagnostic" key={i}>
                  <div className="control-card-heading">
                    <h3>{c.name}</h3>
                    <Tag>{c.normalization_detector}</Tag>
                  </div>
                  <div className="control-counts">
                    <span>
                      Positive{" "}
                      <strong>
                        {formatNumber(c.positive.finite_count, 0)}
                      </strong>
                    </span>
                    <span>
                      Negative{" "}
                      <strong>
                        {c.negative
                          ? formatNumber(c.negative.finite_count, 0)
                          : "AF reference"}
                      </strong>
                    </span>
                    <span>
                      Separation{" "}
                      <strong>
                        {c.separation_robust_sd == null
                          ? "—"
                          : `${formatNumber(c.separation_robust_sd, 2)} SD`}
                      </strong>
                    </span>
                  </div>
                  {kind === "spectral" ? (
                    <SignaturePlot detectors={detectors} values={c.signature} />
                  ) : (
                    c.preview && <ControlScatter preview={c.preview} />
                  )}
                  {c.preview && (
                    <p className="form-note">
                      {kind === "spillover"
                        ? "Teal: positive · gray: negative. "
                        : ""}
                      Calculation uses all finite selected events.
                    </p>
                  )}
                  <details>
                    <summary>Detector medians and response</summary>
                    <div className="table-scroll">
                      <table className="diagnostic-table">
                        <thead>
                          <tr>
                            <th>Detector</th>
                            <th>Positive</th>
                            <th>Negative</th>
                            <th>Difference</th>
                          </tr>
                        </thead>
                        <tbody>
                          {detectors.map((d, j) => (
                            <tr key={d}>
                              <th>{d}</th>
                              <td>{formatNumber(c.positive.median[j], 3)}</td>
                              <td>
                                {c.negative
                                  ? formatNumber(c.negative.median[j], 3)
                                  : "Background"}
                              </td>
                              <td>{formatNumber(c.delta[j], 3)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </details>
                </section>
              ))}
            </div>
            <fieldset className="control-targets">
              <legend>Apply matrix to samples</legend>
              <div className="detector-choices">
                {workspace.samples.map((s) => (
                  <label key={s.id} className={compatible(s.id) ? "" : "muted"}>
                    <input
                      type="checkbox"
                      disabled={!compatible(s.id)}
                      checked={targets.includes(s.id)}
                      onChange={(e) =>
                        setTargets(
                          e.target.checked
                            ? [...targets, s.id]
                            : targets.filter((v) => v !== s.id),
                        )
                      }
                    />
                    {s.name}
                    {!compatible(s.id) ? " (incompatible parameters)" : ""}
                  </label>
                ))}
              </div>
            </fieldset>
            {kind === "spectral" && (
              <p className="form-note">
                Measured detector channels are retained; new output parameters
                are available for plots, gates, statistics and exports.
                Unassigned spectral outputs are undefined.
              </p>
            )}
            {stale && (
              <p className="form-error" role="alert">
                The workspace changed. Return to controls and recalculate before
                saving.
              </p>
            )}
          </>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <div className="modal-actions">
          {result && (
            <button
              className="button"
              disabled={working}
              onClick={() => {
                setResult(null);
                setError("");
              }}
            >
              <ArrowLeft size={16} />
              Back to controls
            </button>
          )}
          <button className="button" disabled={working} onClick={onClose}>
            Cancel
          </button>
          <button
            className="button primary"
            disabled={
              working ||
              !!stale ||
              (!!result && targets.some((t) => !compatible(t)))
            }
            onClick={result ? save : calculate}
          >
            {working ? (
              <LoaderCircle size={16} className="spin" />
            ) : result ? (
              <Check size={16} />
            ) : (
              <FlaskConical size={16} />
            )}
            {working
              ? "Working…"
              : result
                ? targets.length
                  ? "Save & apply control matrix"
                  : "Save control matrix"
                : "Calculate & review"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

export function CompensationWizard(props: ControlWizardProps) {
  const [method, setMethod] = useState<"median" | "autospill">("median");
  return method === "autospill" ? (
    <AutoSpillWizard {...props} onMedian={() => setMethod("median")} />
  ) : (
    <MedianCompensationWizard
      {...props}
      onAutoSpill={() => setMethod("autospill")}
    />
  );
}
