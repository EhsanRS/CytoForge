import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, LoaderCircle, Play, Save, Square } from "lucide-react";
import { api, download, post } from "../api";
import {
  acquisitionChannels,
  formatNumber,
  type Compensation,
  type Workspace,
} from "../types";
import { Modal, Tag } from "./Common";
import type { Commit } from "./Editors";
import {
  initialSpreadingControls,
  spreadingPreset,
  type SpreadPreset,
} from "../spreading";

interface Control {
  output: string;
  sample_id: string;
  gate_id: string | null;
}
interface Fit {
  slope: number;
  intercept: number;
  p_value: number;
  r_squared: number;
  df: number;
}
interface Pair {
  output: string;
  coefficient: number;
  status: "positive" | "negative" | "not_significant";
  baseline_noise: number;
  initial_fit: Fit;
  final_fit: Fit;
  robust_sd: number[];
  adjusted_sd: number[];
  secondary_medians: number[];
  median_signal_fit: Fit;
}
interface ControlReport extends Control {
  parent_count: number;
  finite_count: number;
  used_count: number;
  bin_count: number;
  bin_counts: number[];
  primary_medians: number[];
  pairs: Pair[];
}
interface SpreadResult {
  id: string;
  created_at: string;
  request: SpreadPreset & { algorithm: "autospread"; matrix_id: string };
  outputs: string[];
  primaries: string[];
  matrix: (number | null)[][];
  controls: ControlReport[];
  warnings: string[];
}
interface Reviewed {
  result: SpreadResult | null;
  stale: boolean;
}
interface Job extends Reviewed {
  id: string;
  request: { matrix_id: string; name: string };
  status: string;
  stage: string;
  progress: number;
  error: string | null;
  can_apply: boolean;
}
const active = (status?: string) => status === "queued" || status === "running";
const root = (value: number) => value / (Math.sqrt(Math.abs(value) + 1) + 1);

function RegressionPlot({
  control,
  pair,
  adjusted,
}: {
  control: ControlReport;
  pair: Pair;
  adjusted: boolean;
}) {
  const x = control.primary_medians.map(root),
    y = adjusted ? pair.adjusted_sd : pair.robust_sd;
  const fit = adjusted ? pair.final_fit : pair.initial_fit;
  const loX = Math.min(0, ...x),
    hiX = Math.max(0, ...x);
  const fitY = [loX, hiX].map((v) => fit.intercept + fit.slope * v);
  const loY = Math.min(0, ...y, ...fitY),
    hiY = Math.max(0, ...y, ...fitY);
  const spanX = hiX - loX || 1,
    spanY = hiY - loY || 1;
  const px = (v: number) => 64 + ((v - loX) / spanX) * 442;
  const py = (v: number) => 178 - ((v - loY) / spanY) * 142;
  return (
    <figure className="spread-regression">
      <figcaption>
        {adjusted
          ? "2 · Noise-corrected spreading"
          : "1 · Baseline noise estimate"}
      </figcaption>
      <svg
        viewBox="0 0 540 225"
        role="img"
        aria-label={`${control.output} to ${pair.output}, ${adjusted ? "adjusted" : "baseline"} regression`}
      >
        {[0, 0.25, 0.5, 0.75, 1].map((fraction) => {
          const vx = loX + spanX * fraction,
            vy = loY + spanY * fraction;
          return (
            <g key={fraction}>
              <line
                x1="64"
                x2="506"
                y1={py(vy)}
                y2={py(vy)}
                className="spread-grid"
              />
              <text x="57" y={py(vy) + 4} textAnchor="end">
                {formatNumber(vy)}
              </text>
              <text x={px(vx)} y="196" textAnchor="middle">
                {formatNumber(vx)}
              </text>
            </g>
          );
        })}
        <line
          x1={px(loX)}
          y1={py(fitY[0])}
          x2={px(hiX)}
          y2={py(fitY[1])}
          className="spread-fit"
        />
        {x.map((value, index) => (
          <circle key={index} cx={px(value)} cy={py(y[index])} r="2.4">
            <title>{`${control.bin_counts[index]} events · primary ${formatNumber(control.primary_medians[index])} · SD ${formatNumber(y[index])}`}</title>
          </circle>
        ))}
        <text x="284" y="219" textAnchor="middle">
          Signed root of {control.output} intensity
        </text>
        <text x="14" y="107" transform="rotate(-90 14 107)" textAnchor="middle">
          {adjusted ? "Adjusted SD" : "Robust SD"}
        </text>
      </svg>
    </figure>
  );
}

export function AutoSpreadDialog({
  workspace,
  matrix,
  commit,
  onSaved,
  onClose,
}: {
  workspace: Workspace;
  matrix: Compensation;
  commit: Commit;
  onSaved: (matrix: Compensation) => void;
  onClose: () => void;
}) {
  const [initial] = useState(() =>
    spreadingPreset(matrix.provenance?.autospread, matrix.outputs),
  );
  const [controls, setControls] = useState(() =>
    initialSpreadingControls(matrix),
  );
  const [included, setIncluded] = useState(
    () => initial?.controls.map((c) => c.output) ?? matrix.outputs.slice(),
  );
  const [name, setName] = useState(initial?.name ?? "Spillover spreading");
  const [quantiles, setQuantiles] = useState(initial?.quantiles ?? 256),
    [eventsPerBin, setEventsPerBin] = useState(initial?.events_per_bin ?? 100);
  const [significance, setSignificance] = useState(
      initial?.significance ?? 0.05,
    ),
    [maxEvents, setMaxEvents] = useState<number | null>(
      initial?.max_events ?? null,
    );
  const [selectedId, setSelectedId] = useState(
    matrix.provenance?.autospread ? "saved" : "",
  );
  const [inspection, setInspection] = useState({ primary: "", secondary: "" });
  const [working, setWorking] = useState(false),
    [error, setError] = useState("");
  const queryClient = useQueryClient();
  const base = `/workspaces/${workspace.id}/compensations/autospread`;
  const matrixBase = `/workspaces/${workspace.id}/compensations/${matrix.id}/spreading`;
  const saved = useQuery<Reviewed>({
    queryKey: [workspace.id, "spread-saved", matrix.id, workspace.revision],
    queryFn: () => api(matrixBase),
  });
  const jobs = useQuery<Job[]>({
    queryKey: [workspace.id, "spread-jobs", workspace.revision],
    queryFn: () => api(`${base}/jobs`),
    refetchInterval: (q) =>
      q.state.data?.some((j) => active(j.status)) ? 1000 : false,
  });
  const job = useQuery<Job>({
    queryKey: [workspace.id, "spread-job", selectedId, workspace.revision],
    queryFn: () => api(`${base}/jobs/${selectedId}`),
    enabled: !!selectedId && selectedId !== "saved",
    refetchInterval: (q) => (active(q.state.data?.status) ? 800 : false),
  });
  const reviewed = selectedId === "saved" ? saved.data : job.data;
  const result = reviewed?.result;
  const running = selectedId !== "saved" && active(job.data?.status);
  const control =
    result?.controls.find((c) => c.output === inspection.primary) ??
    result?.controls[0];
  const pair =
    control?.pairs.find((p) => p.output === inspection.secondary) ??
    control?.pairs[0];
  const compatible = workspace.samples.filter((s) =>
    matrix.detectors.every((n) =>
      acquisitionChannels(s).some((c) => c.name === n),
    ),
  );
  const run = async () => {
    setWorking(true);
    setError("");
    try {
      const selected = controls.filter((c) => included.includes(c.output));
      if (!selected.length || selected.some((c) => !c.sample_id))
        throw new Error(
          "Choose a single-color control for every included output",
        );
      const created = await post<Job>(`${base}/jobs`, {
        revision: workspace.revision,
        algorithm: "autospread",
        name,
        matrix_id: matrix.id,
        controls: selected,
        quantiles,
        events_per_bin: eventsPerBin,
        significance,
        max_events: maxEvents,
      });
      setSelectedId(created.id);
      await queryClient.invalidateQueries({
        queryKey: [workspace.id, "spread-jobs"],
      });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setWorking(false);
    }
  };
  const action = async (type: "cancel" | "save" | "json" | "csv") => {
    setWorking(true);
    setError("");
    try {
      if (type === "save") {
        const state = await commit(
          `/compensations/autospread/jobs/${selectedId}/apply`,
          {},
          "Spreading report saved with matrix",
        );
        onSaved(state.compensations.find((c) => c.id === matrix.id)!);
        setSelectedId("saved");
        await queryClient.invalidateQueries({
          queryKey: [workspace.id, "spread-saved"],
        });
      } else if (type === "cancel") {
        await post(`${base}/jobs/${selectedId}/cancel`, {});
        await queryClient.invalidateQueries({
          queryKey: [workspace.id, "spread-job", selectedId],
        });
        await queryClient.invalidateQueries({
          queryKey: [workspace.id, "spread-jobs"],
        });
      } else {
        await download(
          `${selectedId === "saved" ? matrixBase : `${base}/jobs/${selectedId}`}/report?format=${type}`,
          type === "json" ? "spreading.json" : "spreading-matrix.csv",
        );
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setWorking(false);
    }
  };
  const updateControl = (output: string, fields: Partial<Control>) =>
    setControls((current) =>
      current.map((c) => (c.output === output ? { ...c, ...fields } : c)),
    );
  const reuse = () => {
    const preset = spreadingPreset(result, matrix.outputs);
    if (!preset) {
      setError(
        "The report refers to changed outputs or invalid settings. Select new controls.",
      );
      return;
    }
    setControls(
      matrix.outputs.map(
        (output) =>
          preset.controls.find((c) => c.output === output) ?? {
            output,
            sample_id: "",
            gate_id: null,
          },
      ),
    );
    setIncluded(preset.controls.map((c) => c.output));
    setName(preset.name);
    setQuantiles(preset.quantiles);
    setEventsPerBin(preset.events_per_bin);
    setSignificance(preset.significance);
    setMaxEvents(preset.max_events);
    setError("");
  };
  const problem =
    error ||
    (selectedId === "saved" ? saved.error?.message : job.error?.message) ||
    job.data?.error ||
    jobs.error?.message;
  return (
    <Modal
      title="Spillover spreading"
      subtitle={`${matrix.name} · ${matrix.kind === "spectral" ? "Spectral unmixing" : "Conventional compensation"}`}
      onClose={onClose}
      wide
    >
      <div className="spread-dialog">
        <p className="form-note">
          Measure how each single-color control increases noise in other
          outputs. Select populations that include the control’s signal range.
          The selected matrix is applied to acquired events; control gates are
          evaluated in acquired coordinates.
        </p>
        <div className="field-row">
          <label className="field">
            Report name
            <input
              value={name}
              maxLength={160}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label className="field">
            Review report
            <select
              value={selectedId}
              onChange={(e) => {
                setSelectedId(e.target.value);
                setError("");
              }}
            >
              <option value="">New calculation</option>
              {saved.data?.result && (
                <option value="saved">
                  Saved with matrix{saved.data.stale ? " · inputs changed" : ""}
                </option>
              )}
              {(jobs.data ?? [])
                .filter((j) => j.request.matrix_id === matrix.id)
                .map((j) => (
                  <option key={j.id} value={j.id}>
                    {j.request.name} · {j.status} · {j.id.slice(0, 6)}
                  </option>
                ))}
            </select>
          </label>
        </div>
        <details className="spread-controls" open={!result}>
          <summary>
            Single-color controls · {included.length} of {matrix.outputs.length}{" "}
            outputs
          </summary>
          {matrix.provenance?.kind === "control_calculation" && (
            <p className="muted">
              Saved acquired control populations retain raw thresholds. If you
              saved this matrix without its populations, recreate any inline
              thresholds as a gate before selecting controls here.
            </p>
          )}
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Include</th>
                  <th>Primary output</th>
                  <th>Control sample</th>
                  <th>Acquired population</th>
                </tr>
              </thead>
              <tbody>
                {controls.map((c) => (
                  <tr key={c.output}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`Include ${c.output} control`}
                        checked={included.includes(c.output)}
                        onChange={(e) =>
                          setIncluded((current) =>
                            e.target.checked
                              ? [...current, c.output]
                              : current.filter((n) => n !== c.output),
                          )
                        }
                      />
                    </td>
                    <th>{c.output}</th>
                    <td>
                      <select
                        aria-label={`Control for ${c.output}`}
                        value={c.sample_id}
                        disabled={!included.includes(c.output)}
                        onChange={(e) =>
                          updateControl(c.output, {
                            sample_id: e.target.value,
                            gate_id: null,
                          })
                        }
                      >
                        <option value="">Choose control</option>
                        {compatible.map((s) => (
                          <option key={s.id} value={s.id}>
                            {s.name}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <select
                        aria-label={`Population for ${c.output}`}
                        value={c.gate_id ?? ""}
                        disabled={!c.sample_id || !included.includes(c.output)}
                        onChange={(e) =>
                          updateControl(c.output, {
                            gate_id: e.target.value || null,
                          })
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
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="form-note">
            Include an autofluorescence output with its unstained control if
            present. Unselected outputs have no primary row in the report.
            Automatic control calculations prefill their saved populations;
            broaden a positive-only population to include the signal range.
          </p>
        </details>
        <details>
          <summary>Quantiles and significance</summary>
          <div className="spread-settings">
            <label className="field">
              Maximum quantile bins
              <input
                type="number"
                min={8}
                max={256}
                step={1}
                value={quantiles}
                onChange={(e) => setQuantiles(Number(e.target.value))}
              />
            </label>
            <label className="field">
              Minimum events per bin
              <input
                type="number"
                min={20}
                max={10000}
                step={1}
                value={eventsPerBin}
                onChange={(e) => setEventsPerBin(Number(e.target.value))}
              />
            </label>
            <label className="field">
              F-test significance
              <input
                type="number"
                min={0.0001}
                max={0.9999}
                step={0.01}
                value={significance}
                onChange={(e) => setSignificance(Number(e.target.value))}
              />
            </label>
            <label className="field">
              Event limit · blank uses all
              <input
                type="number"
                min={8 * eventsPerBin}
                max={2000000}
                step={1}
                value={maxEvents ?? ""}
                onChange={(e) =>
                  setMaxEvents(
                    e.target.value === "" ? null : Number(e.target.value),
                  )
                }
              />
            </label>
          </div>
          <p className="form-note">
            At least eight bins are required. The F-test applies to each
            secondary fit without a multiple-testing correction. Event limits
            select evenly spaced acquired event IDs and are recorded in the
            report.
          </p>
        </details>
        <div className="button-row">
          <button
            className="button primary"
            disabled={working || running || !included.length}
            onClick={run}
          >
            {working ? (
              <LoaderCircle className="spin" size={16} />
            ) : (
              <Play size={16} />
            )}{" "}
            Calculate spreading
          </button>
          {running && (
            <button
              className="button"
              disabled={working}
              onClick={() => action("cancel")}
            >
              <Square size={15} /> Cancel
            </button>
          )}
        </div>
        {problem && (
          <p className="error" role="alert">
            {problem}
          </p>
        )}
        {running && (
          <div className="spread-progress" role="status">
            <progress max={1} value={job.data?.progress ?? 0} />
            <span>{job.data?.stage}</span>
          </div>
        )}
        {result && (
          <>
            <div className="panel-heading">
              <div>
                <Tag>
                  {reviewed?.stale ? "Inputs changed" : "Ready for review"}
                </Tag>{" "}
                <span className="muted">
                  {result.primaries.length} primary controls ·{" "}
                  {result.outputs.length} outputs
                </span>
              </div>
              <div className="button-row">
                <button
                  className="button"
                  disabled={working || running}
                  onClick={reuse}
                >
                  Reuse controls and settings
                </button>
                <button
                  className="button"
                  disabled={working}
                  onClick={() => action("csv")}
                >
                  <Download size={15} /> Matrix CSV
                </button>
                <button
                  className="button"
                  disabled={working}
                  onClick={() => action("json")}
                >
                  <Download size={15} /> Full report JSON
                </button>
                <button
                  className="button primary"
                  disabled={
                    working || selectedId === "saved" || !job.data?.can_apply
                  }
                  onClick={() => action("save")}
                >
                  <Save size={15} /> Save with matrix
                </button>
              </div>
            </div>
            {reviewed?.stale && (
              <p className="error">
                The matrix or control population changed. Recalculate before
                saving or using these coefficients. Exports retain the original
                calculation and mark its status.
              </p>
            )}
            <div className="table-scroll">
              <table className="matrix-table spread-matrix">
                <thead>
                  <tr>
                    <th>Primary ↓ / Secondary →</th>
                    {result.outputs.map((output) => (
                      <th key={output}>{output}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.primaries.map((primary, i) => (
                    <tr key={primary}>
                      <th>{primary}</th>
                      {result.outputs.map((secondary, j) => {
                        const value = result.matrix[i][j],
                          cell = result.controls[i].pairs.find(
                            (p) => p.output === secondary,
                          );
                        return (
                          <td
                            key={secondary}
                            style={{
                              backgroundColor: value
                                ? `rgba(56,217,186,${Math.min(0.55, 0.08 + Math.log1p(value) / 12)})`
                                : undefined,
                            }}
                          >
                            <button
                              disabled={value === null}
                              className="spread-cell"
                              title={
                                value === null
                                  ? "Self-spreading is not estimated"
                                  : `${cell?.status.replaceAll("_", " ")} · click to inspect`
                              }
                              onClick={() =>
                                setInspection({ primary, secondary })
                              }
                            >
                              {value === null ? "—" : formatNumber(value)}
                            </button>
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="form-note">
              Rows cause spreading; columns receive it. Blank diagonal cells are
              not estimates. Non-positive or non-significant fitted coefficients
              are set to zero. Coefficients depend on instrument scale, matrix
              and control quality.
            </p>
            {control && pair && (
              <section className="spread-inspection">
                <div className="field-row">
                  <label className="field">
                    Primary control
                    <select
                      value={control.output}
                      onChange={(e) =>
                        setInspection({
                          primary: e.target.value,
                          secondary: "",
                        })
                      }
                    >
                      {result.controls.map((c) => (
                        <option key={c.output}>{c.output}</option>
                      ))}
                    </select>
                  </label>
                  <label className="field">
                    Secondary output
                    <select
                      value={pair.output}
                      onChange={(e) =>
                        setInspection({
                          primary: control.output,
                          secondary: e.target.value,
                        })
                      }
                    >
                      {control.pairs.map((p) => (
                        <option key={p.output}>{p.output}</option>
                      ))}
                    </select>
                  </label>
                </div>
                <p>
                  {formatNumber(control.used_count)} of{" "}
                  {formatNumber(control.finite_count)} finite events ·{" "}
                  {control.bin_count} bins · {Math.min(...control.bin_counts)}–
                  {Math.max(...control.bin_counts)} events per bin
                </p>
                <div className="spread-plots">
                  <RegressionPlot
                    control={control}
                    pair={pair}
                    adjusted={false}
                  />
                  <RegressionPlot control={control} pair={pair} adjusted />
                </div>
                <div className="spread-metrics">
                  <div>
                    <span>Spreading coefficient</span>
                    <strong>{formatNumber(pair.coefficient)}</strong>
                  </div>
                  <div>
                    <span>Fitted baseline noise</span>
                    <strong>{formatNumber(pair.baseline_noise)}</strong>
                  </div>
                  <div>
                    <span>Unfiltered slope</span>
                    <strong>{formatNumber(pair.final_fit.slope)}</strong>
                  </div>
                  <div>
                    <span>F-test p-value</span>
                    <strong>{pair.final_fit.p_value.toPrecision(4)}</strong>
                  </div>
                </div>
                <p className="form-note">
                  {pair.status === "positive"
                    ? "Positive, significant spreading."
                    : pair.status === "negative"
                      ? "The fitted spreading slope is non-positive; the reported coefficient is zero."
                      : `The fit does not meet the recorded significance threshold (${result.request.significance}); the reported coefficient is zero.`}{" "}
                  Secondary median signal slope:{" "}
                  {formatNumber(pair.median_signal_fit.slope)}. Inspect
                  compensation and autofluorescence if secondary signal tracks
                  the primary.
                </p>
              </section>
            )}
            {!!result.warnings.length && (
              <details open>
                <summary>{result.warnings.length} control warnings</summary>
                <ul>
                  {result.warnings.map((warning, i) => (
                    <li key={i}>{warning}</li>
                  ))}
                </ul>
              </details>
            )}
            <p className="form-note">
              Uses the published AutoSpread two-regression method. On-scale
              controls and well-orthogonalized signals are required for
              meaningful estimates.
            </p>
          </>
        )}
      </div>
    </Modal>
  );
}
