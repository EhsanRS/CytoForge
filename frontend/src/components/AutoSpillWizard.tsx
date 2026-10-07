import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Check,
  Download,
  LoaderCircle,
  Play,
  X,
} from "lucide-react";
import { api, download, post, saveBlob } from "../api";
import { afReferenceSettings, savedAFReferences } from "../autofluorescence";
import { AutofluorescenceReview } from "./AutofluorescenceReview";
import {
  acquisitionChannels,
  formatNumber,
  type AnalysisJob,
  type Compensation,
  type Workspace,
} from "../types";
import { Modal, Tag } from "./Common";
import type { Commit } from "./Editors";
import {
  autoSpillOutputs,
  selectAutoSpillDetectors,
  spectralControlNames,
  spectralDetectorVector,
} from "../spectralAutoSpill";

interface Biex {
  kind: "wsp_biex";
  positive: number;
  negative: number;
  width: number;
  top: number;
}
interface Control {
  name: string;
  primary_detector: string;
  sample_id: string;
  gate_id: string | null;
}
interface Settings {
  auto_cleanup: boolean;
  scatter_x: string;
  scatter_y: string;
  density_threshold: number;
  target_peak: number;
  min_events: number;
  max_events: number | null;
  trim_fraction: number;
  max_iterations: number;
  regression_iterations: number;
  linear_tolerance: number;
  tolerance: number;
  plateau_tolerance: number;
  damping: number;
  biex_length: 256 | 4096;
  biex: Biex;
}
interface Request extends Settings {
  revision: number;
  algorithm: "autospill";
  name: string;
  kind?: "spillover" | "spectral";
  detectors: string[];
  controls: Control[];
  af_detector: string | null;
  af_output?: string | null;
  af_outputs?: string[];
  background?: number[];
  weights?: number[];
  detector_transforms: Record<string, Biex>;
}
interface Preview {
  x: string;
  y: string;
  raw_x?: string;
  raw_y?: string;
  raw: [number, number][];
  compensated: [number, number][];
  event_ids: number[];
}
interface Diagnostic {
  name: string;
  primary_detector: string;
  output_name?: string;
  reconstruction_max_error?: number;
  reconstruction_relative_rms?: number;
  reconstruction_slopes?: number[];
  sample_name: string;
  parent_count: number;
  cleanup_count: number;
  finite_count: number;
  used_count: number;
  autofluorescence: boolean;
  preview: Preview;
  cleanup: null | {
    x: string;
    y: string;
    vertices: [number, number][];
    preview: [number, number][];
    selected: boolean[];
  };
}
interface Convergence {
  iteration: number;
  scale: "linear" | "biex";
  damping: number;
  max_error: number;
}
interface Result {
  id: string;
  request: Request;
  compensation: Compensation;
  diagnostics: {
    converged: boolean;
    stop_reason: string;
    iterations: number;
    initial_max_error: number;
    final_max_error: number;
    residual_slopes: number[][];
    convergence: Convergence[];
    condition_number: number;
    reconstruction_max_error?: number;
    reconstruction_max_relative_rms?: number;
    reconstruction_within_tolerance?: boolean;
    reconstruction_tolerance?: number;
    rank: number;
    controls: Diagnostic[];
    autofluorescence_sources?: unknown;
  };
  warnings: string[];
}
interface Job extends Omit<AnalysisJob, "request" | "result"> {
  request: Request;
  result: Result | null;
}
interface Saved {
  id: string;
  name: string;
  stale: boolean;
}
export interface ControlWizardProps {
  workspace: Workspace;
  selected: string[];
  commit: Commit;
  onClose: () => void;
  onSaved: (matrix: Compensation) => void;
}

function Scatter({
  points,
  x,
  y,
  title,
  asinh = false,
  cofactor = 150,
  selected,
  vertices,
}: {
  points: [number, number][];
  x: string;
  y: string;
  title: string;
  asinh?: boolean;
  cofactor?: number;
  selected?: boolean[];
  vertices?: [number, number][];
}) {
  const convert = (v: number) => (asinh ? Math.asinh(v / cofactor) : v);
  const bounds = [0, 1].map((axis) => {
    const values = [...points, ...(vertices ?? [])].map((p) =>
      convert(p[axis]),
    );
    const lo = Math.min(...values),
      hi = Math.max(...values);
    const pad = (hi - lo) * 0.07 || 1;
    return [lo - pad, hi + pad];
  });
  const project = (v: number, axis: number) =>
    (convert(v) - bounds[axis][0]) / (bounds[axis][1] - bounds[axis][0]);
  const unconvert = (v: number) => (asinh ? Math.sinh(v) * cofactor : v);
  return (
    <figure className="autospill-scatter">
      <figcaption>{title}</figcaption>
      <svg viewBox="0 0 330 215" role="img" aria-label={title}>
        <path
          d="M50 14V169H316"
          fill="none"
          stroke="currentColor"
          opacity=".3"
        />
        {points.map((p, i) => (
          <circle
            key={i}
            cx={50 + 266 * project(p[0], 0)}
            cy={169 - 154 * project(p[1], 1)}
            r="2"
            fill={selected && !selected[i] ? "#7d8da6" : "#38d9ba"}
            opacity={selected && !selected[i] ? 0.35 : 0.65}
          />
        ))}
        {vertices && (
          <polygon
            points={vertices
              .map(
                (p) =>
                  `${50 + 266 * project(p[0], 0)},${169 - 154 * project(p[1], 1)}`,
              )
              .join(" ")}
            fill="none"
            stroke="#edb96c"
            strokeWidth="1.5"
          />
        )}
        <text x="184" y="204" textAnchor="middle">
          {x}
        </text>
        <text transform="translate(12 93) rotate(-90)" textAnchor="middle">
          {y}
        </text>
        <text x="50" y="185" fontSize="9">
          {formatNumber(unconvert(bounds[0][0]), 0)}
        </text>
        <text x="316" y="185" fontSize="9" textAnchor="end">
          {formatNumber(unconvert(bounds[0][1]), 0)}
        </text>
        <text x="47" y="169" fontSize="9" textAnchor="end">
          {formatNumber(unconvert(bounds[1][0]), 0)}
        </text>
        <text x="47" y="20" fontSize="9" textAnchor="end">
          {formatNumber(unconvert(bounds[1][1]), 0)}
        </text>
      </svg>
    </figure>
  );
}

function ConvergencePlot({
  history,
  tolerance,
}: {
  history: Convergence[];
  tolerance: number;
}) {
  const logs = history.map((h) => Math.log10(Math.max(h.max_error, 1e-12)));
  const low = Math.min(...logs, Math.log10(tolerance)) - 0.4;
  const high = Math.max(...logs, Math.log10(tolerance)) + 0.4;
  const position = (v: number) => 155 - (131 * (v - low)) / (high - low);
  const x = (iteration: number) =>
    55 + (565 * iteration) / Math.max(1, history.at(-1)!.iteration);
  return (
    <svg
      className="autospill-convergence"
      viewBox="0 0 645 192"
      role="img"
      aria-label="AutoSpill convergence history"
    >
      <path d="M55 20V155H620" fill="none" stroke="currentColor" opacity=".3" />
      <path
        d={`M55 ${position(Math.log10(tolerance))}H620`}
        stroke="#edb96c"
        strokeDasharray="5 4"
      />
      <polyline
        points={history
          .map((h, i) => `${x(h.iteration)},${position(logs[i])}`)
          .join(" ")}
        fill="none"
        stroke="#91a5be"
        strokeWidth="1.5"
      />
      {history.map((h, i) => (
        <circle
          key={i}
          cx={x(h.iteration)}
          cy={position(logs[i])}
          r="3.5"
          fill={h.scale === "linear" ? "#719bff" : "#38d9ba"}
        >
          <title>
            Iteration {h.iteration}: {h.scale}, residual{" "}
            {h.max_error.toExponential(3)}, damping {h.damping}
          </title>
        </circle>
      ))}
      <text x="335" y="185" textAnchor="middle">
        Refinement iteration
      </text>
      <text transform="translate(13 91) rotate(-90)" textAnchor="middle">
        Maximum residual slope
      </text>
      <text x="50" y="30" textAnchor="end" fontSize="10">
        {(10 ** high).toExponential(1)}
      </text>
      <text x="50" y="154" textAnchor="end" fontSize="10">
        {(10 ** low).toExponential(1)}
      </text>
      <text x="55" y="169" fontSize="10">
        0
      </text>
      <text x="620" y="169" textAnchor="end" fontSize="10">
        {history.at(-1)!.iteration}
      </text>
    </svg>
  );
}

function ControlReview({
  workspace,
  result,
  index,
  secondary,
  stale,
  onSecondary,
}: {
  workspace: Workspace;
  result: Result;
  index: number;
  secondary: string;
  stale: boolean;
  onSecondary: (name: string) => void;
}) {
  const control = result.diagnostics.controls[index];
  const [asinh, setAsinh] = useState(true);
  const [cofactor, setCofactor] = useState(150);
  const alternate = secondary !== control.preview.y;
  const preview = useQuery<Preview>({
    queryKey: [
      workspace.id,
      "autospill-preview",
      result.id,
      index,
      secondary,
      workspace.revision,
    ],
    queryFn: () =>
      post(`/workspaces/${workspace.id}/compensations/autospill/preview`, {
        revision: workspace.revision,
        result_id: result.id,
        primary: control.output_name ?? control.primary_detector,
        secondary,
      }),
    enabled: alternate && !stale,
    retry: false,
  });
  const shown = alternate ? preview.data : control.preview;
  return (
    <section className="control-diagnostic">
      <div className="control-card-heading">
        <div>
          <h3>{control.name}</h3>
          <small className="muted">{control.sample_name}</small>
        </div>
        <Tag>
          {control.autofluorescence
            ? "Unstained / autofluorescence"
            : control.primary_detector}
        </Tag>
      </div>
      <div className="control-counts">
        <span>
          Parent <strong>{formatNumber(control.parent_count, 0)}</strong>
        </span>
        <span>
          Cleanup <strong>{formatNumber(control.cleanup_count, 0)}</strong>
        </span>
        <span>
          Finite <strong>{formatNumber(control.finite_count, 0)}</strong>
        </span>
        <span>
          Regression input{" "}
          <strong>{formatNumber(control.used_count, 0)}</strong>
        </span>
      </div>
      {control.reconstruction_slopes && (
        <details className="autospill-advanced">
          <summary>
            Detector reconstruction · maximum slope{" "}
            {formatNumber(control.reconstruction_max_error ?? 0, 5)}
          </summary>
          <p className="form-note">
            These regressions compare acquired detector signal with the signal
            reconstructed from unmixed sources. Small cross-source slopes can
            still leave a missing spectral component. Relative reconstruction
            RMS: {formatNumber(control.reconstruction_relative_rms ?? 0, 5)}.
            The warning threshold for slopes and relative RMS is{" "}
            {result.diagnostics.reconstruction_tolerance}.
          </p>
          <div className="table-scroll">
            <table className="matrix-table">
              <thead>
                <tr>
                  <th>Detector</th>
                  <th>Residual slope</th>
                </tr>
              </thead>
              <tbody>
                {control.reconstruction_slopes.map((value, i) => (
                  <tr key={i}>
                    <th>{result.request.detectors[i]}</th>
                    <td>{value.toExponential(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
      <div className="field-row autospill-preview-options">
        <label className="field">
          Secondary output
          <select
            aria-label="Preview secondary detector"
            value={secondary}
            disabled={stale}
            onChange={(e) => onSecondary(e.target.value)}
          >
            {autoSpillOutputs(result.request)
              .filter(
                (d) => d !== (control.output_name ?? control.primary_detector),
              )
              .map((d) => (
                <option key={d}>{d}</option>
              ))}
          </select>
        </label>
        <label className="field">
          Preview scale
          <select
            aria-label="Control preview scale"
            value={asinh ? "asinh" : "linear"}
            onChange={(e) => setAsinh(e.target.value === "asinh")}
          >
            <option value="asinh">Asinh</option>
            <option value="linear">Linear</option>
          </select>
        </label>
        {asinh && (
          <label className="field">
            Cofactor
            <input
              aria-label="Control preview cofactor"
              type="number"
              min="0.001"
              step="any"
              value={cofactor}
              onChange={(e) => {
                const n = Number(e.target.value);
                if (n > 0 && Number.isFinite(n)) setCofactor(n);
              }}
            />
          </label>
        )}
      </div>
      {preview.isError && (
        <p className="form-error" role="alert">
          {preview.error.message}
        </p>
      )}
      {alternate && preview.isFetching ? (
        <p className="form-note">
          <LoaderCircle className="spin" size={15} /> Loading matched control
          events
        </p>
      ) : (
        shown && (
          <div className="autospill-preview-pair">
            <Scatter
              points={shown.raw.map(([p, s]) => [s, p])}
              x={shown.raw_y ?? shown.y}
              y={shown.raw_x ?? shown.x}
              title="Acquired control"
              asinh={asinh}
              cofactor={cofactor}
            />
            <Scatter
              points={shown.compensated.map(([p, s]) => [s, p])}
              x={shown.y}
              y={shown.x}
              title={
                result.request.kind === "spectral"
                  ? "Unmixed control"
                  : "Compensated control"
              }
              asinh={asinh}
              cofactor={cofactor}
            />
          </div>
        )
      )}
      <p className="form-note">
        Both previews use the same {control.preview.event_ids.length} captured
        event IDs. Preview scale affects display. Regressions trim selected
        fluorescence tails using the saved settings. Event limits are explicit.
      </p>
      {control.cleanup && (
        <details className="autospill-cleanup-review">
          <summary>Inspect automatic scatter cleanup</summary>
          <Scatter
            points={control.cleanup.preview}
            x={control.cleanup.x}
            y={control.cleanup.y}
            selected={control.cleanup.selected}
            vertices={control.cleanup.vertices}
            title="Automatic scatter cleanup"
          />
          <p className="form-note">
            The polygon encloses the selected density peak. Gray events were
            excluded; selected events are teal.
          </p>
        </details>
      )}
    </section>
  );
}

const defaultBiex: Biex = {
  kind: "wsp_biex",
  positive: 4.418539922,
  negative: 0,
  width: -100,
  top: 262144,
};
const defaults = (channels: string[]): Settings => {
  const x =
    channels.find((c) => /^fsc[-_ ]?a$/i.test(c)) ??
    channels.find((c) => /^fsc/i.test(c)) ??
    channels[0] ??
    "FSC-A";
  const y =
    channels.find((c) => /^ssc[-_ ]?a$/i.test(c)) ??
    channels.find((c) => /^ssc/i.test(c)) ??
    channels[1] ??
    "SSC-A";
  return {
    auto_cleanup:
      channels.some((c) => /^fsc/i.test(c)) &&
      channels.some((c) => /^ssc/i.test(c)),
    scatter_x: x,
    scatter_y: y,
    density_threshold: 0.33,
    target_peak: 1,
    min_events: 100,
    max_events: null,
    trim_fraction: 0.01,
    max_iterations: 100,
    regression_iterations: 100,
    linear_tolerance: 0.01,
    tolerance: 0.0001,
    plateau_tolerance: 0.000001,
    damping: 0.1,
    biex_length: 256,
    biex: defaultBiex,
  };
};

export function AutoSpillWizard({
  workspace,
  selected,
  commit,
  onClose,
  onSaved,
  onMedian,
}: ControlWizardProps & { onMedian: () => void }) {
  const available = acquisitionChannels(
    workspace.samples.find((s) => s.id === selected[0]) ?? workspace.samples[0],
  );
  const initial = available
    .filter((c) => !/^(fsc|ssc|time)/i.test(c.name))
    .map((c) => c.name);
  const [name, setName] = useState("AutoSpill compensation");
  const [kind, setKind] = useState<"spillover" | "spectral">(
    initial.length > 64 ? "spectral" : "spillover",
  );
  const [detectors, setDetectors] = useState(initial);
  const [controls, setControls] = useState<Control[]>(
    initial.slice(0, initial.length > 64 ? 2 : 64).map((d) => ({
      name: initial.length > 64 ? `Unmixed ${d}` : d,
      primary_detector: d,
      sample_id: "",
      gate_id: null,
    })),
  );
  const [settings, setSettings] = useState(() =>
    defaults(available.map((c) => c.name)),
  );
  const [afDetector, setAFDetector] = useState<string | null>(null);
  const [afOutputs, setAFOutputs] = useState<string[]>([]);
  const [background, setBackground] = useState<Record<string, number>>({});
  const [weights, setWeights] = useState<Record<string, number>>({});
  const outputs = autoSpillOutputs({ kind, detectors, controls });
  const [overrides, setOverrides] = useState<Record<string, Biex>>({});
  const [selectedId, setSelectedId] = useState("");
  const [targets, setTargets] = useState(selected);
  const [inspection, setInspection] = useState({ index: 0, secondary: "" });
  const [acknowledge, setAcknowledge] = useState(false);
  const [saveCleanup, setSaveCleanup] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const client = useQueryClient();
  const base = `/workspaces/${workspace.id}/compensations/autospill`;
  const jobs = useQuery<Job[]>({
    queryKey: [workspace.id, "autospill-jobs", workspace.revision],
    queryFn: () => api(`${base}/jobs`),
    refetchInterval: (q) =>
      q.state.data?.some((j) => ["queued", "running"].includes(j.status))
        ? 1000
        : false,
  });
  const saved = useQuery<Saved[]>({
    queryKey: [workspace.id, "autospill-saved", workspace.revision],
    queryFn: () => api(`${base}/saved`),
  });
  const isSaved =
    !!selectedId &&
    !!saved.data?.some((s) => s.id === selectedId) &&
    !jobs.data?.some((j) => j.id === selectedId);
  const job = useQuery<Job>({
    queryKey: [workspace.id, "autospill-job", selectedId, workspace.revision],
    queryFn: () => api(`${base}/jobs/${selectedId}`),
    enabled: !!selectedId && !isSaved,
    refetchInterval: (q) =>
      q.state.data && ["queued", "running"].includes(q.state.data.status)
        ? 600
        : false,
    retry: false,
  });
  const archived = useQuery<{ result: Result; stale: boolean }>({
    queryKey: [
      workspace.id,
      "autospill-archive",
      selectedId,
      workspace.revision,
    ],
    queryFn: () => api(`${base}/saved/${selectedId}`),
    enabled: isSaved,
    retry: false,
  });
  const result = isSaved ? archived.data?.result : job.data?.result;
  const stale = isSaved ? archived.data?.stale : job.data?.stale;
  const active =
    !isSaved && !!job.data && ["queued", "running"].includes(job.data.status);
  const canApply =
    !isSaved &&
    job.data?.can_apply &&
    job.data.status === "succeeded" &&
    !stale;
  const updateControl = (index: number, patch: Partial<Control>) => {
    const previous = controls[index];
    if (
      kind === "spectral" &&
      patch.name !== undefined &&
      patch.name !== previous.name
    ) {
      setAFOutputs((old) =>
        old.map((value) =>
          value === previous.name.trim() ? patch.name!.trim() : value,
        ),
      );
      setOverrides((old) => {
        const next = { ...old };
        if (Object.hasOwn(next, previous.name)) {
          if (!Object.hasOwn(next, patch.name!))
            next[patch.name!] = next[previous.name];
          delete next[previous.name];
        }
        return next;
      });
    }
    setControls((old) =>
      old.map((c, i) => (i === index ? { ...c, ...patch } : c)),
    );
  };
  const configure = (next: string[]) => {
    setDetectors(next);
    setControls((old) => selectAutoSpillDetectors(next, old, kind));
    if (kind === "spectral") return;
    if (afDetector && !next.includes(afDetector)) setAFDetector(null);
    setOverrides((old) =>
      Object.fromEntries(
        Object.entries(old).filter(([key]) => next.includes(key)),
      ),
    );
  };
  const select = (id: string) => {
    setSelectedId(id);
    setInspection({ index: 0, secondary: "" });
    setAcknowledge(false);
    setError("");
  };
  const edit = (request?: Request) => {
    if (request) {
      setName(request.name);
      setKind(request.kind ?? "spillover");
      setDetectors(request.detectors);
      setControls(request.controls);
      setAFDetector(request.kind === "spectral" ? null : request.af_detector);
      setAFOutputs(
        request.kind === "spectral" ? savedAFReferences(request) : [],
      );
      setBackground(
        Object.fromEntries(
          (request.background ?? []).map((value, i) => [
            request.detectors[i],
            value,
          ]),
        ),
      );
      setWeights(
        Object.fromEntries(
          (request.weights ?? []).map((value, i) => [
            request.detectors[i],
            value,
          ]),
        ),
      );
      setOverrides(request.detector_transforms);
      const next = { ...settings };
      for (const key of Object.keys(next) as (keyof Settings)[])
        Object.assign(next, { [key]: request[key] });
      setSettings(next);
    }
    select("");
  };
  const submit = async () => {
    setWorking(true);
    setError("");
    try {
      if (
        !name.trim() ||
        detectors.length < 2 ||
        controls.some((c) => !c.sample_id || !c.name.trim())
      )
        throw new Error(
          "Name the matrix, select at least two detectors and assign every control sample.",
        );
      const numeric = Object.values(settings).filter(
        (v) => typeof v === "number",
      ) as number[];
      if (!numeric.every(Number.isFinite))
        throw new Error("Calculation settings must be finite numbers.");
      const created = await post<Job>(`${base}/jobs`, {
        ...settings,
        revision: workspace.revision,
        algorithm: "autospill",
        name: name.trim(),
        kind,
        detectors,
        controls: controls.map((c) => ({ ...c, name: c.name.trim() })),
        af_detector: kind === "spillover" ? afDetector : null,
        ...(kind === "spectral"
          ? afReferenceSettings(afOutputs, outputs)
          : { af_output: null, af_outputs: [] }),
        background:
          kind === "spectral"
            ? spectralDetectorVector(detectors, background, 0)
            : [],
        weights:
          kind === "spectral"
            ? spectralDetectorVector(detectors, weights, 1)
            : [],
        detector_transforms: Object.fromEntries(
          Object.entries(overrides)
            .filter(([output]) => outputs.includes(output))
            .map(([output, spec]) => [output.trim(), spec]),
        ),
      });
      select(created.id);
      await client.invalidateQueries({
        queryKey: [workspace.id, "autospill-jobs"],
      });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setWorking(false);
    }
  };
  const cancel = async () => {
    setWorking(true);
    setError("");
    try {
      await post(`${base}/jobs/${selectedId}/cancel`, {});
      await client.invalidateQueries({ queryKey: [workspace.id] });
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
      const doc = await commit(
        `/compensations/autospill/jobs/${result.id}/apply`,
        {
          sample_ids: targets,
          acknowledge_unconverged: acknowledge,
          save_cleanup_gates: saveCleanup,
        },
        "AutoSpill matrix saved and applied",
      );
      onSaved(doc.compensations.find((c) => c.id === result.id)!);
      onClose();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setWorking(false);
    }
  };
  const exportReport = async () => {
    if (!result) return;
    try {
      await download(
        isSaved
          ? `/workspaces/${workspace.id}/compensations/${result.id}/provenance`
          : `${base}/jobs/${result.id}/report`,
        "autospill.json",
      );
    } catch (err) {
      setError((err as Error).message);
    }
  };
  const exportMatrix = () => {
    if (!result) return;
    const csv = (text: string) =>
      `"${(/^[=+\-@\t\r]/.test(text) ? "'" + text : text).replaceAll('"', '""')}"`;
    const matrix = result.compensation;
    const lines = [
      ["Source / detector", ...matrix.detectors].map(csv).join(","),
      ...matrix.matrix.map((row, i) =>
        [csv(matrix.outputs[i]), ...row.map(String)].join(","),
      ),
    ];
    saveBlob(
      new Blob([lines.join("\r\n") + "\r\n"], { type: "text/csv" }),
      "autospill-matrix.csv",
    );
  };
  const count = result?.diagnostics.controls.length ?? 0;
  const needsAcknowledgement =
    !!result &&
    (!result.diagnostics.converged ||
      result.diagnostics.reconstruction_within_tolerance === false);
  const editedFields = result?.compensation.provenance?.edited_fields;
  const hasManualEdits = Array.isArray(editedFields) && editedFields.length > 0;
  const index = Math.min(inspection.index, Math.max(0, count - 1));
  const secondary =
    inspection.secondary ||
    result?.diagnostics.controls[index]?.preview.y ||
    "";
  const compatible = (sampleId: string) => {
    const sample = workspace.samples.find((s) => s.id === sampleId);
    if (!sample) return false;
    const names = acquisitionChannels(sample).map((c) => c.name);
    if (result?.request.kind === "spectral") {
      const other = sample.channels
        .filter((channel) => !sample.unmixed_parameters.includes(channel.name))
        .map((channel) => channel.name);
      if (result.compensation.outputs.some((output) => other.includes(output)))
        return false;
    }
    return (result?.request.detectors ?? detectors).every((d) =>
      names.includes(d),
    );
  };
  return (
    <Modal
      title="Compensation control wizard"
      subtitle="AutoSpill robust regression and iterative refinement"
      onClose={onClose}
      wide
    >
      <div className="compensation-wizard autospill-wizard">
        <label className="field wizard-estimator">
          Estimation method
          <select
            aria-label="Estimation method"
            value="autospill"
            onChange={() => onMedian()}
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
        {!!jobs.data?.length || !!saved.data?.length ? (
          <details className="autospill-runs" open={!!selectedId}>
            <summary>Previous calculations and saved matrices</summary>
            <div className="quality-run-list">
              {jobs.data?.map((j) => (
                <button
                  className={`quality-run quality-run-select ${selectedId === j.id ? "active" : ""}`}
                  key={j.id}
                  onClick={() => select(j.id)}
                >
                  <strong>{j.request.name}</strong>
                  <Tag color={j.stale ? "#edb96c" : "#91a5be"}>
                    {j.stale ? "Inputs changed" : j.status}
                  </Tag>
                </button>
              ))}
              {saved.data
                ?.filter((s) => !jobs.data?.some((j) => j.id === s.id))
                .map((s) => (
                  <button
                    className={`quality-run quality-run-select ${selectedId === s.id ? "active" : ""}`}
                    key={s.id}
                    onClick={() => select(s.id)}
                  >
                    <strong>{s.name}</strong>
                    <Tag color={s.stale ? "#edb96c" : "#38d9ba"}>
                      {s.stale ? "Inputs changed" : "Saved matrix"}
                    </Tag>
                  </button>
                ))}
            </div>
          </details>
        ) : null}
        {jobs.isError && <p className="form-error">{jobs.error.message}</p>}
        {!selectedId ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void submit();
            }}
          >
            <div className="field-row">
              <label className="field">
                Matrix name
                <input
                  aria-label="AutoSpill matrix name"
                  maxLength={160}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                />
              </label>
              <label className="field">
                Minimum finite events
                <input
                  aria-label="AutoSpill minimum events"
                  type="number"
                  min={20}
                  max={100000}
                  value={settings.min_events}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      min_events: Number(e.target.value),
                    })
                  }
                  required
                />
              </label>
            </div>
            <label className="field">
              Matrix type
              <select
                aria-label="AutoSpill matrix type"
                value={kind}
                onChange={(e) => {
                  const next = e.target.value as "spillover" | "spectral";
                  setKind(next);
                  setAFDetector(null);
                  setAFOutputs([]);
                  setOverrides({});
                  setControls((old) =>
                    next === "spectral"
                      ? spectralControlNames(
                          old.slice(0, 64),
                          workspace.samples.flatMap((sample) =>
                            acquisitionChannels(sample).map(
                              (channel) => channel.name,
                            ),
                          ),
                        )
                      : selectAutoSpillDetectors(detectors, old, next),
                  );
                }}
              >
                <option value="spillover">Conventional spillover</option>
                <option value="spectral">Spectral unmixing</option>
              </select>
            </label>
            <p className="form-note">
              {kind === "spectral"
                ? "Assign one control per spectral source. Select all measured detectors; sources can share a peak detector and need distinct output names. "
                : "Assign one single-stain sample per primary detector. "}
              Use the whole cell or bead cleanup population, including its
              continuous fluorescence distribution. Positive and negative
              fluorescence gates are unnecessary. Parent cleanup gates use
              acquired values, before any assigned compensation, with their
              saved transforms.
            </p>
            <div
              className="detector-choices"
              role="group"
              aria-label="AutoSpill detectors"
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
            <section className="spectral-options">
              <label className="checkbox-field">
                <input
                  aria-label="Automatic scatter cleanup"
                  type="checkbox"
                  checked={settings.auto_cleanup}
                  onChange={(e) =>
                    setSettings({ ...settings, auto_cleanup: e.target.checked })
                  }
                />
                Automatically clean up scatter populations
              </label>
              {settings.auto_cleanup ? (
                <div className="field-row">
                  <label className="field">
                    Forward scatter
                    <select
                      aria-label="AutoSpill scatter X"
                      value={settings.scatter_x}
                      onChange={(e) =>
                        setSettings({ ...settings, scatter_x: e.target.value })
                      }
                    >
                      {available.map((c) => (
                        <option key={c.name}>{c.name}</option>
                      ))}
                    </select>
                  </label>
                  <label className="field">
                    Side scatter
                    <select
                      aria-label="AutoSpill scatter Y"
                      value={settings.scatter_y}
                      onChange={(e) =>
                        setSettings({ ...settings, scatter_y: e.target.value })
                      }
                    >
                      {available.map((c) => (
                        <option key={c.name}>{c.name}</option>
                      ))}
                    </select>
                  </label>
                </div>
              ) : (
                <p className="form-note">
                  Select a reviewed cleanup population for each control, or use
                  all events from already cleaned controls.
                </p>
              )}
              {kind === "spillover" && (
                <label className="checkbox-field">
                  <input
                    aria-label="AutoSpill autofluorescence subtraction"
                    type="checkbox"
                    checked={!!afDetector}
                    onChange={(e) =>
                      setAFDetector(
                        e.target.checked ? (outputs.at(-1) ?? null) : null,
                      )
                    }
                  />
                  Subtract autofluorescence using an empty detector
                </label>
              )}
              {kind === "spillover" && afDetector && (
                <>
                  <label className="field">
                    Empty detector
                    <select
                      aria-label="AutoSpill autofluorescence detector"
                      value={afDetector}
                      onChange={(e) => setAFDetector(e.target.value)}
                    >
                      {outputs.map((d) => (
                        <option key={d}>{d}</option>
                      ))}
                    </select>
                  </label>
                  <p className="form-note">
                    Assign representative unstained cells to this detector’s
                    control below. All controls and experimental cells must have
                    a matching autofluorescence spectrum. Prefer a bright
                    autofluorescence channel without an assigned fluorophore;
                    inspect heterogeneous cell types separately.
                  </p>
                </>
              )}
              {kind === "spectral" && (
                <fieldset className="fieldset">
                  <legend>Unstained autofluorescence outputs</legend>
                  <div className="checkbox-grid">
                    {outputs.map((output, i) => (
                      <label className="checkbox-field" key={i}>
                        <input
                          type="checkbox"
                          aria-label={`AutoSpill AF output ${i + 1}`}
                          checked={afOutputs.includes(output)}
                          onChange={(e) =>
                            setAFOutputs((old) =>
                              e.target.checked
                                ? [...old, output]
                                : old.filter((v) => v !== output),
                            )
                          }
                        />
                        {output || `Source ${i + 1}`}
                      </label>
                    ))}
                  </div>
                  <p className="form-note">
                    Select each source represented by a distinct unstained
                    population. AF references and experimental cells must share
                    those spectra. Review source separation; adding similar
                    references can amplify detector noise.
                  </p>
                </fieldset>
              )}
            </section>
            <p className="form-note">
              Control parents use acquired values, including ratios between
              acquired channels. Reviewed QC flags are captured for this
              calculation; review current QC before starting a new calculation.
            </p>
            <div className="control-cards">
              {controls.map((c, i) => (
                <section className="control-card" key={i}>
                  <div className="control-card-heading">
                    <Tag>{c.primary_detector}</Tag>
                    <label className="field">
                      {(
                        kind === "spectral"
                          ? afOutputs.includes(c.name.trim())
                          : c.primary_detector === afDetector
                      )
                        ? "Unstained control label"
                        : "Single-stain control label"}
                      <input
                        aria-label={`AutoSpill control ${i + 1} name`}
                        value={c.name}
                        maxLength={160}
                        onChange={(e) =>
                          updateControl(i, { name: e.target.value })
                        }
                        required
                      />
                    </label>
                  </div>
                  <div className="field-row">
                    {kind === "spectral" && (
                      <label className="field">
                        Peak detector
                        <select
                          aria-label={`AutoSpill control ${i + 1} peak detector`}
                          value={c.primary_detector}
                          onChange={(e) =>
                            updateControl(i, {
                              primary_detector: e.target.value,
                            })
                          }
                        >
                          {detectors.map((detector) => (
                            <option key={detector}>{detector}</option>
                          ))}
                        </select>
                      </label>
                    )}
                    <label className="field">
                      {(
                        kind === "spectral"
                          ? afOutputs.includes(c.name.trim())
                          : c.primary_detector === afDetector
                      )
                        ? "Unstained sample"
                        : "Single-stain sample"}
                      <select
                        aria-label={`AutoSpill control ${i + 1} sample`}
                        value={c.sample_id}
                        onChange={(e) =>
                          updateControl(i, {
                            sample_id: e.target.value,
                            gate_id: null,
                          })
                        }
                        required
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
                      Parent cleanup population
                      <select
                        aria-label={`AutoSpill control ${i + 1} population`}
                        value={c.gate_id ?? ""}
                        disabled={!c.sample_id}
                        onChange={(e) =>
                          updateControl(i, { gate_id: e.target.value || null })
                        }
                      >
                        <option value="">All events</option>
                        {workspace.gates
                          .filter((g) => g.sample_id === c.sample_id)
                          .map((g) => (
                            <option key={g.id} value={g.id}>
                              {g.name}
                            </option>
                          ))}
                      </select>
                    </label>
                  </div>
                  {kind === "spectral" && (
                    <button
                      type="button"
                      className="button ghost"
                      disabled={controls.length <= 2}
                      onClick={() => {
                        setControls((old) =>
                          old.filter((_, index) => index !== i),
                        );
                        setAFOutputs((old) =>
                          old.filter((value) => value !== c.name.trim()),
                        );
                        setOverrides((old) => {
                          const next = { ...old };
                          delete next[c.name];
                          return next;
                        });
                      }}
                    >
                      Remove source
                    </button>
                  )}
                </section>
              ))}
            </div>
            {kind === "spectral" && (
              <>
                <button
                  type="button"
                  className="button"
                  disabled={controls.length >= Math.min(64, detectors.length)}
                  onClick={() => {
                    const added = spectralControlNames(
                      [
                        {
                          name: "Spectral source",
                          primary_detector: detectors[0] ?? "",
                          sample_id: "",
                          gate_id: null,
                        },
                      ],
                      [...outputs, ...available.map((channel) => channel.name)],
                    )[0];
                    setControls((old) => [...old, added]);
                  }}
                >
                  Add spectral source
                </button>
                <details className="autospill-advanced">
                  <summary>
                    Detector background and inverse variance weights
                  </summary>
                  <p className="form-note">
                    Fixed acquired background is subtracted before unmixing.
                    Weight is inverse detector variance; use measured noise
                    estimates. Settings follow detector names when the selection
                    changes.
                  </p>
                  <div className="table-scroll">
                    <table className="matrix-table">
                      <thead>
                        <tr>
                          <th>Detector</th>
                          <th>Background</th>
                          <th>Inverse variance</th>
                        </tr>
                      </thead>
                      <tbody>
                        {detectors.map((detector) => (
                          <tr key={detector}>
                            <th>{detector}</th>
                            <td>
                              <input
                                aria-label={`AutoSpill background ${detector}`}
                                type="number"
                                step="any"
                                value={
                                  Object.hasOwn(background, detector)
                                    ? background[detector]
                                    : 0
                                }
                                onChange={(e) =>
                                  setBackground((old) => ({
                                    ...old,
                                    [detector]: Number(e.target.value),
                                  }))
                                }
                              />
                            </td>
                            <td>
                              <input
                                aria-label={`AutoSpill weight ${detector}`}
                                type="number"
                                min={Number.MIN_VALUE}
                                step="any"
                                value={
                                  Object.hasOwn(weights, detector)
                                    ? weights[detector]
                                    : 1
                                }
                                onChange={(e) =>
                                  setWeights((old) => ({
                                    ...old,
                                    [detector]: Number(e.target.value),
                                  }))
                                }
                              />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </details>
              </>
            )}
            <details className="autospill-advanced">
              <summary>Regression, cleanup and refinement settings</summary>
              <div className="autospill-settings-grid">
                {(
                  [
                    ["max_iterations", "Maximum refinements", 1, 200, 1],
                    [
                      "regression_iterations",
                      "Maximum regression iterations",
                      1,
                      200,
                      1,
                    ],
                    ["trim_fraction", "Tail trim fraction", 0, 0.1, 0.001],
                    [
                      "density_threshold",
                      "Cleanup density threshold",
                      0.001,
                      0.999,
                      "any",
                    ],
                    ["target_peak", "Scatter density peak", 1, 64, 1],
                    [
                      "linear_tolerance",
                      "Switch to biex below",
                      0.000001,
                      0.1,
                      "any",
                    ],
                    [
                      "tolerance",
                      "Residual slope tolerance",
                      0.00000001,
                      0.01,
                      "any",
                    ],
                    [
                      "plateau_tolerance",
                      "Plateau change threshold",
                      0.0000000001,
                      0.001,
                      "any",
                    ],
                    ["damping", "Damped update fraction", 0.001, 0.999, "any"],
                  ] as const
                ).map(([key, label, min, max, step]) => (
                  <label className="field" key={key}>
                    {label}
                    <input
                      aria-label={label}
                      type="number"
                      min={min}
                      max={max}
                      step={step}
                      value={settings[key]}
                      onChange={(e) =>
                        setSettings({
                          ...settings,
                          [key]: Number(e.target.value),
                        })
                      }
                      required
                    />
                  </label>
                ))}
                <label className="field">
                  Events per control (blank = all)
                  <input
                    aria-label="AutoSpill event limit"
                    type="number"
                    min={100}
                    max={2000000}
                    value={settings.max_events ?? ""}
                    onChange={(e) =>
                      setSettings({
                        ...settings,
                        max_events:
                          e.target.value === "" ? null : Number(e.target.value),
                      })
                    }
                  />
                </label>
              </div>
              <p className="form-note">
                Tail trimming applies to both regression axes. Automatic cleanup
                selects the requested scatter density peak outside the
                lower-left debris corner. A plateau triggers smaller updates; a
                remaining unmet tolerance is reported for review.
              </p>
              <h4>Biex refinement coordinates</h4>
              <div className="autospill-settings-grid">
                {(
                  [
                    ["top", "Top of scale"],
                    ["positive", "Positive decades"],
                    ["negative", "Negative decades"],
                    ["width", "Width basis"],
                  ] as const
                ).map(([key, label]) => (
                  <label className="field" key={key}>
                    {label}
                    <input
                      aria-label={`AutoSpill biex ${label}`}
                      type="number"
                      step="any"
                      value={settings.biex[key]}
                      onChange={(e) =>
                        setSettings({
                          ...settings,
                          biex: {
                            ...settings.biex,
                            [key]: Number(e.target.value),
                          },
                        })
                      }
                      required
                    />
                  </label>
                ))}
                <label className="field">
                  Lookup resolution
                  <select
                    aria-label="AutoSpill biex resolution"
                    value={settings.biex_length}
                    onChange={(e) =>
                      setSettings({
                        ...settings,
                        biex_length: Number(e.target.value) as 256 | 4096,
                      })
                    }
                  >
                    <option value={256}>256 · reference default</option>
                    <option value={4096}>4096</option>
                  </select>
                </label>
              </div>
              <details>
                <summary>
                  Override biex coordinates by refinement output
                </summary>
                {outputs.map((d, i) => (
                  <div className="autospill-override" key={i}>
                    <label className="checkbox-field">
                      <input
                        aria-label={`Customize biex ${d}`}
                        type="checkbox"
                        checked={Object.hasOwn(overrides, d)}
                        onChange={(e) =>
                          setOverrides((old) => {
                            const next = { ...old };
                            if (e.target.checked)
                              next[d] = { ...settings.biex };
                            else delete next[d];
                            return next;
                          })
                        }
                      />
                      {d}
                    </label>
                    {Object.hasOwn(overrides, d) && (
                      <div className="autospill-settings-grid">
                        {(
                          ["top", "positive", "negative", "width"] as const
                        ).map((key) => (
                          <label className="field" key={key}>
                            {key}
                            <input
                              aria-label={`${d} biex ${key}`}
                              type="number"
                              step="any"
                              value={overrides[d][key]}
                              onChange={(e) =>
                                setOverrides({
                                  ...overrides,
                                  [d]: {
                                    ...overrides[d],
                                    [key]: Number(e.target.value),
                                  },
                                })
                              }
                              required
                            />
                          </label>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </details>
            </details>
            {error && (
              <p className="form-error" role="alert">
                {error}
              </p>
            )}
            <div className="modal-actions">
              <button className="button" type="button" onClick={onClose}>
                Cancel
              </button>
              <button
                className="button primary"
                type="submit"
                disabled={working}
              >
                {working ? (
                  <LoaderCircle className="spin" size={16} />
                ) : (
                  <Play size={16} />
                )}
                Run AutoSpill
              </button>
            </div>
          </form>
        ) : (
          <>
            {(job.isFetching || archived.isFetching) && !result && (
              <p className="form-note">
                <LoaderCircle className="spin" size={16} />
                Loading calculation
              </p>
            )}
            {(isSaved ? archived.error : job.error) && (
              <p className="form-error" role="alert">
                {(isSaved ? archived.error : job.error)?.message}
              </p>
            )}
            {active && (
              <section className="autospill-progress">
                <h3>{job.data?.request.name}</h3>
                <progress
                  max={1}
                  value={job.data?.progress}
                  aria-label="AutoSpill progress"
                />
                <p>{job.data?.stage}</p>
                <button
                  className="button"
                  disabled={working}
                  onClick={() => void cancel()}
                >
                  <X size={15} />
                  Cancel calculation
                </button>
                <p className="form-note">
                  The calculation continues if you close this dialog. Open
                  previous calculations to resume review.
                </p>
              </section>
            )}
            {job.data?.error && (
              <p className="form-error" role="alert">
                {job.data.error}
              </p>
            )}
            {!active && !result && job.data && (
              <p className="form-note">
                Calculation {job.data.status}. Return to controls to run it
                again.
              </p>
            )}
            {result && (
              <>
                <div className="control-review-heading">
                  <div>
                    <span className="eyebrow">AUTOSPILL MATRIX</span>
                    <h3>{result.compensation.name}</h3>
                    <p className="form-note">
                      Primary-detector equivalent intensity ·{" "}
                      {result.request.kind === "spectral" &&
                      savedAFReferences(result.request).length
                        ? `AF references: ${savedAFReferences(result.request).join(", ")}`
                        : result.request.af_detector
                          ? `autofluorescence reference: ${result.request.af_detector}`
                          : "single-stain controls"}
                    </p>
                  </div>
                  <Tag
                    color={result.diagnostics.converged ? "#38d9ba" : "#edb96c"}
                  >
                    {result.diagnostics.converged
                      ? "Converged"
                      : "Tolerance unmet"}
                  </Tag>
                </div>
                <div className="autospill-metrics">
                  <span>
                    Final residual{" "}
                    <strong>
                      {result.diagnostics.final_max_error.toExponential(2)}
                    </strong>
                  </span>
                  <span>
                    Target{" "}
                    <strong>{result.request.tolerance.toExponential(1)}</strong>
                  </span>
                  <span>
                    Refinements <strong>{result.diagnostics.iterations}</strong>
                  </span>
                  <span>
                    Condition{" "}
                    <strong>
                      {formatNumber(result.diagnostics.condition_number, 3)}
                    </strong>
                  </span>
                  <span>
                    Rank <strong>{result.diagnostics.rank}</strong>
                  </span>
                </div>
                <div className="table-scroll">
                  <table className="matrix-table control-review-matrix">
                    <thead>
                      <tr>
                        <th>Source / detector</th>
                        {result.request.detectors.map((d) => (
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
                {result.request.kind === "spectral" && (
                  <p className="form-note">
                    {result.compensation.outputs.length} sources ×{" "}
                    {result.request.detectors.length} acquired detectors.
                    Detector reconstruction maximum slope:{" "}
                    {formatNumber(
                      result.diagnostics.reconstruction_max_error ?? 0,
                      5,
                    )}{" "}
                    and maximum relative RMS:{" "}
                    {formatNumber(
                      result.diagnostics.reconstruction_max_relative_rms ?? 0,
                      5,
                    )}{" "}
                    (threshold {result.diagnostics.reconstruction_tolerance}).
                    Inspect detector residuals for each control below;
                    refinement preserves the initial spectral row space.
                  </p>
                )}
                {hasManualEdits && (
                  <p className="form-note">
                    This review shows the original calculation. Any later manual
                    matrix edits are recorded in the saved provenance.
                  </p>
                )}
                {!!result.warnings.length && (
                  <div className="control-warnings" role="status">
                    <strong>Review these diagnostics</strong>
                    <ul>
                      {result.warnings.map((w, i) => (
                        <li key={i}>{w}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <section className="autospill-convergence-card">
                  <h3>Refinement convergence</h3>
                  <ConvergencePlot
                    history={result.diagnostics.convergence}
                    tolerance={result.request.tolerance}
                  />
                  <p className="form-note">
                    <span className="dot" style={{ background: "#719bff" }} />{" "}
                    Linear ·{" "}
                    <span className="dot" style={{ background: "#38d9ba" }} />{" "}
                    Biex · dashed line: requested tolerance
                  </p>
                </section>
                <details className="autospill-residuals">
                  <summary>Residual slopes for every detector pair</summary>
                  <div className="table-scroll">
                    <table className="matrix-table">
                      <thead>
                        <tr>
                          <th>Primary / secondary</th>
                          {autoSpillOutputs(result.request).map((d) => (
                            <th key={d}>{d}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {result.diagnostics.residual_slopes.map((row, i) => (
                          <tr key={i}>
                            <th>{autoSpillOutputs(result.request)[i]}</th>
                            {row.map((value, j) => (
                              <td key={j}>
                                {i === j ? (
                                  "—"
                                ) : (
                                  <button
                                    className={`autospill-residual ${Math.abs(value) > result.request.tolerance ? "exceeds" : ""}`}
                                    title={`Inspect ${autoSpillOutputs(result.request)[i]} versus ${autoSpillOutputs(result.request)[j]}`}
                                    onClick={() =>
                                      setInspection({
                                        index: i,
                                        secondary: autoSpillOutputs(
                                          result.request,
                                        )[j],
                                      })
                                    }
                                    disabled={!!stale}
                                  >
                                    {value.toExponential(2)}
                                  </button>
                                )}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </details>
                <label className="field autospill-control-select">
                  Review control
                  <select
                    aria-label="Review AutoSpill control"
                    value={index}
                    onChange={(e) =>
                      setInspection({
                        index: Number(e.target.value),
                        secondary: "",
                      })
                    }
                  >
                    {result.diagnostics.controls.map((c, i) => (
                      <option key={c.primary_detector} value={i}>
                        {c.name} · {c.sample_name}
                      </option>
                    ))}
                  </select>
                </label>
                <ControlReview
                  key={`${result.id}-${index}`}
                  workspace={workspace}
                  result={result}
                  index={index}
                  secondary={secondary}
                  stale={!!stale}
                  onSecondary={(value) =>
                    setInspection({ index, secondary: value })
                  }
                />
                {stale && (
                  <p className="form-error" role="alert">
                    Acquired controls or cleanup gates changed. Captured
                    diagnostics remain available; recalculate before applying or
                    previewing other detector pairs.
                  </p>
                )}
                {canApply && (
                  <fieldset className="control-targets">
                    <legend>Apply matrix to samples</legend>
                    <div className="detector-choices">
                      {workspace.samples.map((s) => (
                        <label
                          key={s.id}
                          className={compatible(s.id) ? "" : "muted"}
                        >
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
                          {!compatible(s.id) ? " (incompatible matrix)" : ""}
                        </label>
                      ))}
                    </div>
                  </fieldset>
                )}
                {canApply &&
                  (result.request.auto_cleanup ||
                    result.request.controls.some((c) => c.gate_id)) && (
                    <label className="checkbox-field">
                      <input
                        aria-label="Save AutoSpill control populations"
                        type="checkbox"
                        checked={saveCleanup}
                        onChange={(e) => setSaveCleanup(e.target.checked)}
                      />
                      Save acquired control populations in the sample trees,
                      including cleanup gates and captured reviewed QC flags.
                    </label>
                  )}
                <AutofluorescenceReview
                  rows={result.diagnostics.autofluorescence_sources}
                  outputs={result.compensation.outputs}
                />
                {canApply && needsAcknowledgement && (
                  <label className="checkbox-field autospill-acknowledge">
                    <input
                      aria-label="Acknowledge AutoSpill unmet tolerance"
                      type="checkbox"
                      checked={acknowledge}
                      onChange={(e) => setAcknowledge(e.target.checked)}
                    />
                    I reviewed the residuals and want to save this matrix with
                    its unmet tolerance recorded.
                  </label>
                )}
              </>
            )}
            {error && (
              <p className="form-error" role="alert">
                {error}
              </p>
            )}
            <div className="modal-actions">
              {!active && (
                <button
                  className="button"
                  disabled={working}
                  onClick={() => edit(result?.request ?? job.data?.request)}
                >
                  <ArrowLeft size={16} />
                  {result
                    ? "Edit controls and recalculate"
                    : "Return to controls"}
                </button>
              )}
              {result && (
                <>
                  <button className="button" onClick={exportMatrix}>
                    <Download size={16} />
                    Matrix CSV
                  </button>
                  <button
                    className="button"
                    onClick={() => void exportReport()}
                  >
                    <Download size={16} />
                    Calculation report
                  </button>
                </>
              )}
              <button className="button" onClick={onClose}>
                Close
              </button>
              {canApply && result && (
                <button
                  className="button primary"
                  disabled={
                    working ||
                    (needsAcknowledgement && !acknowledge) ||
                    targets.some((id) => !compatible(id))
                  }
                  onClick={() => void save()}
                >
                  {working ? (
                    <LoaderCircle className="spin" size={16} />
                  ) : (
                    <Check size={16} />
                  )}
                  Save and apply matrix
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
