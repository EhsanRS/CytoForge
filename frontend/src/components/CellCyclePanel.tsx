import { useState } from "react";
import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  ArrowDownToLine,
  ArrowUpRight,
  Check,
  Dna,
  LoaderCircle,
  Play,
  RefreshCw,
  X,
} from "lucide-react";
import { api, download, post } from "../api";
import type {
  CellCycleFit,
  CellCycleJob,
  CellCycleRequest,
  CellCycleResult,
  FitConstraint,
  Gate,
  Sample,
  Workspace,
} from "../types";
import { channelLabel, formatNumber } from "../types";
import { Empty, ErrorState, Tag } from "./Common";
import type { Commit } from "./Editors";
import { BiologyModelActions } from "./BiologyModelActions";
import { availableRefitName } from "./BiologyCommon";

const phases = ["G0/G1", "S", "G2/M"];
const colors = ["#719bff", "#38d9ba", "#edb96c"];
const defaults = {
  compensated: true,
  bins: 256,
  range_min: null,
  range_max: null,
  g1_mean: {},
  g2_mean: {},
  g1_cv: {},
  g2_cv: {},
  peak_ratio: {},
  linked_cv: "none",
  synchronous_s: false,
  s_peak_initial: null,
  objective: "poisson",
  maximum_evaluations: 800,
  smoothing: 0.75,
  create_phase_gates: true,
  replace_result_id: null,
} satisfies Partial<CellCycleRequest>;
const nullable = (value: string) =>
  value.trim() === "" ? null : Number(value);

function ConstraintRow({
  label,
  value,
  onChange,
  cv = false,
}: {
  label: string;
  value: FitConstraint;
  onChange: (value: FitConstraint) => void;
  cv?: boolean;
}) {
  const [mode, setMode] = useState(
    value.fixed != null
      ? "fixed"
      : value.minimum != null || value.maximum != null
        ? "range"
        : "free",
  );
  return (
    <div className="cell-cycle-constraint">
      <label className="field">
        {label}
        <select
          aria-label={`${label} constraint`}
          value={mode}
          onChange={(e) => {
            setMode(e.target.value);
            const initial = value.initial;
            onChange(
              e.target.value === "fixed"
                ? { fixed: cv ? 4 : label.includes("ratio") ? 2 : 100, initial }
                : e.target.value === "range"
                  ? {
                      minimum: cv ? 2 : label.includes("ratio") ? 1.8 : 80,
                      maximum: cv ? 8 : label.includes("ratio") ? 2.1 : 220,
                      initial,
                    }
                  : { initial },
            );
          }}
        >
          <option value="free">Automatic bounds</option>
          <option value="fixed">Fixed value</option>
          <option value="range">Bounded range</option>
        </select>
      </label>
      {mode === "fixed" && (
        <label className="field">
          Fixed {cv ? "CV (%)" : "value"}
          <input
            aria-label={`${label} fixed value`}
            type="number"
            step="any"
            min={cv ? 0.3 : 0.000001}
            max={cv ? 40 : undefined}
            value={value.fixed ?? ""}
            required
            onChange={(e) =>
              onChange({ ...value, fixed: nullable(e.target.value) })
            }
          />
        </label>
      )}
      {mode === "range" && (
        <>
          <label className="field">
            Minimum
            <input
              aria-label={`${label} minimum`}
              type="number"
              step="any"
              min={cv ? 0.3 : 0.000001}
              value={value.minimum ?? ""}
              onChange={(e) =>
                onChange({ ...value, minimum: nullable(e.target.value) })
              }
            />
          </label>
          <label className="field">
            Maximum
            <input
              aria-label={`${label} maximum`}
              type="number"
              step="any"
              min={cv ? 0.3 : 0.000001}
              max={cv ? 40 : undefined}
              value={value.maximum ?? ""}
              onChange={(e) =>
                onChange({ ...value, maximum: nullable(e.target.value) })
              }
            />
          </label>
        </>
      )}
      {mode !== "fixed" && (
        <label className="field">
          Initial guess
          <input
            aria-label={`${label} initial guess`}
            type="number"
            step="any"
            min={0.000001}
            placeholder="Automatic"
            value={value.initial ?? ""}
            onChange={(e) =>
              onChange({ ...value, initial: nullable(e.target.value) })
            }
          />
        </label>
      )}
    </div>
  );
}

function FitChart({ fit, channel }: { fit: CellCycleFit; channel: string }) {
  const [selected, setSelected] = useState(Math.floor(fit.observed.length / 2));
  const [shown, setShown] = useState([true, true, true]);
  const n = fit.observed.length;
  const total = fit.observed.map((_, i) =>
    fit.components.reduce((sum, row) => sum + row[i], 0),
  );
  const maximum = Math.max(...fit.observed, ...total, 1) * 1.05;
  const residual = fit.observed.map((value, i) => value - total[i]);
  const residualMax = Math.max(...residual.map(Math.abs), 1);
  const path = (values: number[], residuals = false) =>
    values
      .map(
        (value, i) =>
          `${i ? "L" : "M"}${64 + ((i + 0.5) / n) * 776},${residuals ? 398 - (value / residualMax) * 35 : 326 - (value / maximum) * 252}`,
      )
      .join(" ");
  const x = 64 + ((selected + 0.5) / n) * 776;
  return (
    <div className="cell-cycle-chart">
      <div className="cell-cycle-legend">
        <span>Observed</span>
        <span style={{ color: "#b595f6" }}>Total fit</span>
        {phases.map((phase, i) => (
          <button
            key={phase}
            type="button"
            className="button small ghost"
            style={{ color: colors[i], opacity: shown[i] ? 1 : 0.45 }}
            aria-pressed={shown[i]}
            onClick={() => setShown(shown.map((v, j) => (i === j ? !v : v)))}
          >
            {phase}
          </button>
        ))}
      </div>
      <svg
        viewBox="0 0 880 475"
        role="img"
        aria-label={`${channel} full-event DNA histogram with fitted phase curves and residuals`}
        onPointerMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          const pointer = ((e.clientX - box.left) / box.width) * 880;
          setSelected(
            Math.max(
              0,
              Math.min(n - 1, Math.floor(((pointer - 64) / 776) * n)),
            ),
          );
        }}
      >
        {[0, 1, 2, 3, 4].map((i) => (
          <g key={i}>
            <line
              x1="64"
              x2="840"
              y1={326 - i * 63}
              y2={326 - i * 63}
              stroke="var(--line)"
            />
            <text x="54" y={331 - i * 63} textAnchor="end">
              {formatNumber((maximum * i) / 4, 0)}
            </text>
          </g>
        ))}
        <path
          d={path(fit.observed)}
          fill="none"
          stroke="var(--muted)"
          strokeWidth="1.4"
        />
        {fit.components.map(
          (row, i) =>
            shown[i] && (
              <path
                key={i}
                d={path(row)}
                fill="none"
                stroke={colors[i]}
                strokeWidth="2"
              />
            ),
        )}
        <path d={path(total)} fill="none" stroke="#b595f6" strokeWidth="2.2" />
        <line
          x1={x}
          x2={x}
          y1="74"
          y2="433"
          stroke="var(--muted)"
          strokeDasharray="4 4"
          opacity=".5"
        />
        <line x1="64" x2="840" y1="398" y2="398" stroke="var(--line)" />
        <path
          d={path(residual, true)}
          fill="none"
          stroke="var(--muted)"
          strokeWidth="1.4"
        />
        <text x="4" y="367">
          Residual
        </text>
        <text x="52" y="402" textAnchor="end">
          0
        </text>
        {[0, 1, 2, 3, 4].map((i) => (
          <text key={i} x={64 + i * 194} y="451" textAnchor="middle">
            {formatNumber(
              fit.range_max *
                ((fit.range_min / fit.range_max) * (1 - i / 4) + i / 4),
              2,
            )}
          </text>
        ))}
        <text x="452" y="473" textAnchor="middle">
          {channel} · linear intensity
        </text>
      </svg>
      <label className="field">
        Inspect histogram bin
        <input
          aria-label="Inspect histogram bin"
          type="range"
          min="0"
          max={n - 1}
          value={selected}
          onChange={(e) => setSelected(Number(e.target.value))}
        />
      </label>
      <div className="cell-cycle-bin" aria-live="polite">
        DNA {formatNumber(fit.edges[selected], 3)}–
        {formatNumber(fit.edges[selected + 1], 3)} ·{" "}
        {fit.observed[selected].toLocaleString()} observed ·{" "}
        {formatNumber(total[selected], 2)} fitted
        {phases.map((phase, i) => (
          <span key={phase} style={{ color: colors[i] }}>
            {phase} probability {(fit.weights[i][selected] * 100).toFixed(1)}%
          </span>
        ))}
      </div>
    </div>
  );
}

export function CellCyclePanel({
  workspace,
  sample,
  gate,
  commit,
  busy,
  onExplore,
}: {
  workspace: Workspace;
  sample: Sample | null;
  gate: Gate | null;
  commit: Commit;
  busy: boolean;
  onExplore: (sampleId: string, gateId: string) => void;
}) {
  const cache = useQueryClient();
  const first = sample ?? workspace.samples[0];
  const [sources, setSources] = useState<Record<string, string>>(() =>
    first ? { [first.id]: gate?.id ?? "" } : {},
  );
  const [channel, setChannel] = useState(
    first?.channels.find((c) =>
      /DNA|DAPI|Hoechst|(^|[^A-Za-z])PI([^A-Za-z]|$)|7.AAD/i.test(
        `${c.name} ${c.label}`,
      ),
    )?.name ??
      first?.channels.find((c) => !/^(FSC|SSC|Time)/i.test(c.name))?.name ??
      first?.channels[0]?.name ??
      "",
  );
  const [name, setName] = useState("Cell cycle 01");
  const [options, setOptions] = useState<
    Omit<
      CellCycleRequest,
      "revision" | "name" | "algorithm" | "method" | "inputs" | "channel"
    >
  >({ ...defaults });
  const [selected, setSelected] = useState<string | null>(
    (workspace.cell_cycle_results ?? []).at(-1)?.id ?? null,
  );
  const [fitSample, setFitSample] = useState<string>(first?.id ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [reviewed, setReviewed] = useState(false);
  const [settingsVersion, setSettingsVersion] = useState(0);
  const [error, setError] = useState("");
  const base = `/workspaces/${workspace.id}/cell-cycle`;
  const jobs = useQuery({
    queryKey: [workspace.id, "cell-cycle-jobs", workspace.revision],
    queryFn: () => api<CellCycleJob[]>(`${base}/jobs`),
    refetchInterval: 1000,
  });
  const job = jobs.data?.find((j) => j.id === selected);
  const saved = (workspace.cell_cycle_results ?? []).find(
    (r) => r.id === selected,
  );
  const result = useQuery({
    queryKey: [workspace.id, "cell-cycle-result", selected, workspace.revision],
    queryFn: () => api<CellCycleResult>(`${base}/${selected}`),
    enabled:
      !!selected &&
      (!!saved || job?.status === "succeeded" || job?.status === "applied"),
    placeholderData: keepPreviousData,
  });
  const current = result.data?.id === selected ? result.data : undefined;
  const fit =
    current?.fits.find((f) => f.sample_id === fitSample) ?? current?.fits[0];
  const inputs = workspace.samples.filter((s) => s.id in sources);
  const channels = (inputs[0]?.channels ?? []).filter((c) =>
    inputs.every((s) => s.channels.some((v) => v.name === c.name)),
  );
  const archived = (workspace.cell_cycle_results ?? []).filter(
    (r) => !jobs.data?.some((j) => j.id === r.id),
  );
  function choose(identifier: string) {
    setSelected(identifier);
    setReviewed(false);
    setError("");
  }
  function loadSettings(r: CellCycleResult, replace = false) {
    const {
      revision: _revision,
      name: previousName,
      algorithm: _algorithm,
      method: _method,
      inputs: priorInputs,
      channel: priorChannel,
      ...priorOptions
    } = r.request;
    setName(
      replace
        ? previousName
        : availableRefitName(
            previousName,
            workspace.cell_cycle_results.map((v) => v.request.name),
          ),
    );
    setChannel(priorChannel);
    setOptions({ ...priorOptions, replace_result_id: replace ? r.id : null });
    setSettingsVersion((v) => v + 1);
    setSources(
      Object.fromEntries(
        priorInputs
          .filter((i) => workspace.samples.some((s) => s.id === i.sample_id))
          .map((i) => [
            i.sample_id,
            workspace.gates.some((g) => g.id === i.gate_id)
              ? (i.gate_id ?? "")
              : "",
          ]),
      ),
    );
    setError("");
  }
  async function run(event: React.SubmitEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const request: CellCycleRequest = {
        ...options,
        revision: workspace.revision,
        name,
        channel,
        algorithm: "cell_cycle",
        method: "djf",
        inputs: inputs.map((s) => ({
          sample_id: s.id,
          gate_id: sources[s.id] || null,
        })),
      };
      const created = await post<CellCycleJob>(`${base}/jobs`, request);
      choose(created.id);
      await cache.invalidateQueries({
        queryKey: [workspace.id, "cell-cycle-jobs"],
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  }
  async function act(action: () => Promise<unknown>) {
    setError("");
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  if (!workspace.samples.length)
    return (
      <Empty
        icon={<Dna size={32} />}
        title="Fit DNA distributions"
        text="Import DNA-stained samples and select a reviewed singlet population to model the cell cycle."
      />
    );
  return (
    <div className="panel-view cell-cycle-panel">
      <div className="panel-heading">
        <div>
          <div className="eyebrow">BIOLOGY</div>
          <h2>Cell cycle</h2>
          <p>Review DNA peaks, fit each sample, and inspect phase overlap.</p>
        </div>
        <Tag color="#719bff">Dean–Jett–Fox</Tag>
      </div>
      {(error || jobs.error || result.error) && (
        <ErrorState
          error={
            new Error(
              error ||
                (jobs.error as Error)?.message ||
                (result.error as Error)?.message,
            )
          }
        />
      )}
      <div className="cell-cycle-workspace">
        <form className="card cell-cycle-settings" onSubmit={run}>
          <h3>Fit settings</h3>
          <label className="field">
            Model name
            <input
              value={name}
              required
              maxLength={125}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <fieldset>
            <legend>Source populations</legend>
            <div className="button-row">
              <button
                type="button"
                className="button small ghost"
                onClick={() =>
                  setSources(
                    Object.fromEntries(
                      workspace.samples.map((s) => [s.id, ""]),
                    ),
                  )
                }
              >
                All samples
              </button>
              <button
                type="button"
                className="button small ghost"
                onClick={() =>
                  setSources(first ? { [first.id]: gate?.id ?? "" } : {})
                }
              >
                Current population
              </button>
            </div>
            {workspace.groups.length > 0 && (
              <label className="field">
                Select group
                <select
                  aria-label="Cell-cycle group"
                  value=""
                  onChange={(e) => {
                    const group = workspace.groups.find(
                      (g) => g.id === e.target.value,
                    );
                    if (group)
                      setSources(
                        Object.fromEntries(
                          group.sample_ids.map((sid) => [sid, ""]),
                        ),
                      );
                  }}
                >
                  <option value="">Choose a group…</option>
                  {workspace.groups.map((g) => (
                    <option key={g.id} value={g.id}>
                      {g.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <div className="cell-cycle-sources">
              {workspace.samples.map((s) => (
                <div key={s.id}>
                  <label className="check-row">
                    <input
                      type="checkbox"
                      checked={s.id in sources}
                      onChange={(e) => {
                        const next = { ...sources };
                        if (e.target.checked) next[s.id] = "";
                        else delete next[s.id];
                        setSources(next);
                      }}
                    />
                    {s.name}
                  </label>
                  {s.id in sources && (
                    <select
                      aria-label={`Source population for ${s.name}`}
                      value={sources[s.id]}
                      onChange={(e) =>
                        setSources({ ...sources, [s.id]: e.target.value })
                      }
                    >
                      <option value="">All events</option>
                      {workspace.gates
                        .filter((g) => g.sample_id === s.id)
                        .map((g) => (
                          <option key={g.id} value={g.id}>
                            {g.name}
                          </option>
                        ))}
                    </select>
                  )}
                </div>
              ))}
            </div>
          </fieldset>
          <label className="field">
            DNA parameter
            <select
              aria-label="DNA parameter"
              required
              value={channel}
              onChange={(e) => setChannel(e.target.value)}
            >
              <option value="">Choose a shared parameter…</option>
              {channels.map((c) => (
                <option key={c.name} value={c.name}>
                  {channelLabel(c)}
                </option>
              ))}
            </select>
          </label>
          <label className="check-row">
            <input
              type="checkbox"
              checked={options.compensated}
              onChange={(e) =>
                setOptions({ ...options, compensated: e.target.checked })
              }
            />
            Use sample compensation
          </label>
          <p className="muted">
            Fits use linear DNA intensity and every event in the selected
            population.
          </p>
          <div className="form-grid">
            <label className="field">
              Histogram bins
              <select
                value={options.bins}
                onChange={(e) =>
                  setOptions({ ...options, bins: Number(e.target.value) })
                }
              >
                {[64, 128, 256, 512, 1024].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              Fit objective
              <select
                value={options.objective}
                onChange={(e) =>
                  setOptions({
                    ...options,
                    objective: e.target.value as CellCycleRequest["objective"],
                  })
                }
              >
                <option value="poisson">Poisson deviance</option>
                <option value="weighted_least_squares">
                  Weighted least squares
                </option>
              </select>
            </label>
            <label className="field">
              DNA range minimum
              <input
                type="number"
                step="any"
                min="0"
                placeholder="Automatic: 0"
                value={options.range_min ?? ""}
                onChange={(e) =>
                  setOptions({
                    ...options,
                    range_min: nullable(e.target.value),
                  })
                }
              />
            </label>
            <label className="field">
              DNA range maximum
              <input
                type="number"
                step="any"
                min="0.000001"
                placeholder="Automatic"
                value={options.range_max ?? ""}
                onChange={(e) =>
                  setOptions({
                    ...options,
                    range_max: nullable(e.target.value),
                  })
                }
              />
            </label>
          </div>
          <details>
            <summary>Peak constraints and initial guesses</summary>
            <p className="muted">
              Apply these constraints to every selected sample. Each sample gets
              its own fit.
            </p>
            {(
              [
                ["G1 mean", "g1_mean"],
                ["G2 mean", "g2_mean"],
                ["G1 CV (%)", "g1_cv"],
                ["G2 CV (%)", "g2_cv"],
                ["G2/G1 ratio", "peak_ratio"],
              ] as const
            ).map(([label, key]) => (
              <ConstraintRow
                key={`${key}-${settingsVersion}`}
                label={label}
                cv={key.endsWith("cv")}
                value={options[key]}
                onChange={(value) => setOptions({ ...options, [key]: value })}
              />
            ))}
            <label className="field">
              Link peak CVs
              <select
                aria-label="Link peak CVs"
                value={options.linked_cv}
                onChange={(e) =>
                  setOptions({
                    ...options,
                    linked_cv: e.target.value as CellCycleRequest["linked_cv"],
                  })
                }
              >
                <option value="none">Independent CVs</option>
                <option value="g2_to_g1">G2 CV equals G1 CV</option>
                <option value="g1_to_g2">G1 CV equals G2 CV</option>
              </select>
            </label>
          </details>
          <label className="check-row">
            <input
              type="checkbox"
              checked={options.synchronous_s}
              onChange={(e) =>
                setOptions({ ...options, synchronous_s: e.target.checked })
              }
            />
            Fit a synchronous S-phase wave
          </label>
          {options.synchronous_s && (
            <label className="field">
              S-wave initial DNA intensity
              <input
                type="number"
                step="any"
                min="0.000001"
                placeholder="Automatic"
                value={options.s_peak_initial ?? ""}
                onChange={(e) =>
                  setOptions({
                    ...options,
                    s_peak_initial: nullable(e.target.value),
                  })
                }
              />
            </label>
          )}
          <details>
            <summary>Solver and population outputs</summary>
            <label className="field">
              Maximum evaluations per fit attempt
              <input
                type="number"
                min="100"
                max="3000"
                step="100"
                value={options.maximum_evaluations}
                onChange={(e) =>
                  setOptions({
                    ...options,
                    maximum_evaluations: Number(e.target.value),
                  })
                }
              />
            </label>
            <label className="field">
              Initial histogram smoothing (bins)
              <input
                type="number"
                min="0"
                max="3"
                step="0.25"
                value={options.smoothing}
                onChange={(e) =>
                  setOptions({ ...options, smoothing: Number(e.target.value) })
                }
              />
            </label>
            <label className="check-row">
              <input
                type="checkbox"
                checked={options.create_phase_gates}
                onChange={(e) =>
                  setOptions({
                    ...options,
                    create_phase_gates: e.target.checked,
                  })
                }
              />
              Create maximum-probability phase populations
            </label>
          </details>
          <button
            className="button primary"
            disabled={
              busy ||
              submitting ||
              !inputs.length ||
              !channels.some((c) => c.name === channel)
            }
          >
            {submitting ? (
              <LoaderCircle size={16} className="spin" />
            ) : (
              <Play size={16} />
            )}
            Fit{" "}
            {inputs.length === 1 ? "cell cycle" : `${inputs.length} samples`}
          </button>
        </form>
        <div className="cell-cycle-results">
          <div className="card">
            <h3>Fits and queue</h3>
            {!jobs.data?.length && !archived.length && (
              <p className="muted">
                Completed fits remain available for review before saving.
              </p>
            )}
            {jobs.data?.map((j) => (
              <div
                className={`cell-cycle-job ${selected === j.id ? "selected" : ""}`}
                key={j.id}
              >
                <button type="button" onClick={() => choose(j.id)}>
                  <strong>{j.request.name}</strong>
                  <span>
                    {j.request.inputs.length} sample
                    {j.request.inputs.length > 1 ? "s" : ""} · {j.status}
                    {j.stale ? " · inputs changed" : ""}
                  </span>
                  {["queued", "running"].includes(j.status) && (
                    <>
                      <progress value={j.progress} max="1" />
                      <small>{j.stage}</small>
                    </>
                  )}
                </button>
                {["queued", "running"].includes(j.status) && (
                  <button
                    type="button"
                    className="icon-button"
                    aria-label={`Cancel ${j.request.name}`}
                    onClick={() =>
                      act(async () => {
                        await post(`${base}/jobs/${j.id}/cancel`, {});
                        await jobs.refetch();
                      })
                    }
                  >
                    <X size={16} />
                  </button>
                )}
              </div>
            ))}
            {archived.map((r) => (
              <div
                className={`cell-cycle-job ${selected === r.id ? "selected" : ""}`}
                key={r.id}
              >
                <button type="button" onClick={() => choose(r.id)}>
                  <strong>{r.request.name}</strong>
                  <span>
                    {r.fits.length} sample{r.fits.length > 1 ? "s" : ""} · saved
                    fit
                  </span>
                </button>
              </div>
            ))}
          </div>
          {job?.error && <ErrorState error={new Error(job.error)} />}
          {result.isFetching && !current && (
            <div className="card">
              <LoaderCircle size={20} className="spin" /> Loading fitted
              distributions…
            </div>
          )}
          {!current && !result.isFetching && (
            <Empty
              icon={<Dna size={30} />}
              title="Inspect a completed fit"
              text="The histogram, phase curves, residuals, constraints and event assignments appear here."
            />
          )}
          {current && fit && (
            <div className="card cell-cycle-review">
              <div className="cell-cycle-review-heading">
                <div>
                  <h3>{current.request.name}</h3>
                  <p>
                    {current.request.synchronous_s
                      ? "DJF with a synchronous S-phase wave"
                      : "DJF with polynomial S phase"}{" "}
                    · {current.duration_seconds.toFixed(2)}s
                  </p>
                </div>
                <Tag color={fit.diagnostics.converged ? "#38d9ba" : "#edb96c"}>
                  {fit.diagnostics.converged
                    ? "Converged"
                    : "Review convergence"}
                </Tag>
              </div>
              <label className="field">
                Review sample
                <select
                  aria-label="Review cell-cycle sample"
                  value={fit.sample_id}
                  onChange={(e) => setFitSample(e.target.value)}
                >
                  {current.fits.map((f) => (
                    <option key={f.sample_id} value={f.sample_id}>
                      {workspace.samples.find((s) => s.id === f.sample_id)
                        ?.name ?? "Removed sample"}
                    </option>
                  ))}
                </select>
              </label>
              {current.stale && (
                <div className="notice warning">
                  Scientific inputs changed. These saved probabilities retain
                  their original event identities. Refit before saving a new
                  result.
                </div>
              )}
              <FitChart
                key={`${current.id}-${fit.sample_id}`}
                fit={fit}
                channel={current.request.channel}
              />
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Phase</th>
                    <th>Model fraction</th>
                    <th>Expected events</th>
                    <th>Assigned events</th>
                  </tr>
                </thead>
                <tbody>
                  {phases.map((phase, i) => (
                    <tr key={phase}>
                      <th style={{ color: colors[i] }}>{phase}</th>
                      <td>
                        {(fit.fractions[i] * 100).toFixed(2)}%
                        {fit.diagnostics.fraction_standard_errors && (
                          <small className="muted">
                            {" "}
                            ±{" "}
                            {(
                              1.96 *
                              fit.diagnostics.fraction_standard_errors[i] *
                              100
                            ).toFixed(2)}
                            % (95% local estimate)
                          </small>
                        )}
                      </td>
                      <td>{formatNumber(fit.expected_counts[i], 2)}</td>
                      <td>{fit.assigned_counts[i].toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="muted">
                Model fractions describe overlapping fitted curves. Expected
                events sum phase probabilities. Assigned populations give each
                fitted event to its most probable phase.
              </p>
              <div className="cell-cycle-metrics">
                {(
                  [
                    ["G1 mean", fit.parameters.g1_mean, 3],
                    ["G2 mean", fit.parameters.g2_mean, 3],
                    ["G1 CV (%)", fit.parameters.g1_cv, 2],
                    ["G2 CV (%)", fit.parameters.g2_cv, 2],
                    ["G2/G1 ratio", fit.parameters.peak_ratio, 4],
                    [
                      "RMSD (events/bin)",
                      fit.diagnostics.rmsd_events_per_bin,
                      3,
                    ],
                  ] as const
                ).map(([label, value, digits]) => (
                  <div key={label}>
                    <span>{label}</span>
                    <strong>{formatNumber(value, digits)}</strong>
                  </div>
                ))}
              </div>
              <details>
                <summary>Fit diagnostics and excluded events</summary>
                <dl>
                  <dt>Fitted / source events</dt>
                  <dd>
                    {fit.data.fitted_count.toLocaleString()} /{" "}
                    {fit.data.population_count.toLocaleString()}
                  </dd>
                  <dt>DNA basis</dt>
                  <dd>{fit.diagnostics.parameter_basis}</dd>
                  <dt>Fraction denominator</dt>
                  <dd>{fit.diagnostics.percentage_basis}</dd>
                  <dt>Poisson deviance</dt>
                  <dd>{formatNumber(fit.diagnostics.poisson_deviance, 3)}</dd>
                  <dt>Reduced chi-square</dt>
                  <dd>
                    {fit.diagnostics.reduced_chi_square == null
                      ? "Undefined"
                      : formatNumber(fit.diagnostics.reduced_chi_square, 3)}
                  </dd>
                  <dt>Excluded events</dt>
                  <dd>
                    {fit.diagnostics.excluded_nonfinite} nonfinite ·{" "}
                    {fit.diagnostics.excluded_negative} negative ·{" "}
                    {fit.diagnostics.excluded_below_range} below range ·{" "}
                    {fit.diagnostics.excluded_above_range} above range
                  </dd>
                  <dt>Fraction uncertainty</dt>
                  <dd>
                    {fit.diagnostics.fraction_standard_errors
                      ? "Local covariance estimate; assumes independent Poisson histogram counts."
                      : "Undefined for a non-identifiable, bounded or unconverged fit."}
                  </dd>
                </dl>
                <pre>{JSON.stringify(current.request, null, 2)}</pre>
              </details>
              {[...current.warnings, ...fit.warnings].map((warning, i) => (
                <div className="notice warning" key={i}>
                  {warning}
                </div>
              ))}
              <div className="button-row">
                <button
                  type="button"
                  className="button small"
                  onClick={() =>
                    act(() =>
                      download(
                        `${base}/${current.id}/report`,
                        "cell-cycle.json",
                      ),
                    )
                  }
                >
                  <ArrowDownToLine size={15} />
                  JSON report
                </button>
                <button
                  type="button"
                  className="button small"
                  onClick={() =>
                    act(() =>
                      download(
                        `${base}/${current.id}/statistics`,
                        "cell-cycle-statistics.csv",
                      ),
                    )
                  }
                >
                  <ArrowDownToLine size={15} />
                  Batch statistics CSV
                </button>
                <button
                  type="button"
                  className="button small"
                  onClick={() =>
                    act(() =>
                      download(
                        `${base}/${current.id}/events?sample_id=${fit.sample_id}`,
                        "cell-cycle-event-probabilities.csv",
                      ),
                    )
                  }
                >
                  <ArrowDownToLine size={15} />
                  Event probabilities CSV
                </button>
                <button
                  type="button"
                  className="button small"
                  onClick={() =>
                    act(() =>
                      download(
                        `${base}/${current.id}/figure?sample_id=${fit.sample_id}`,
                        "cell-cycle.svg",
                      ),
                    )
                  }
                >
                  <ArrowDownToLine size={15} />
                  SVG figure
                </button>
                <button
                  type="button"
                  className="button small"
                  onClick={() => loadSettings(current)}
                >
                  <RefreshCw size={15} />
                  Use settings for refit
                </button>
              </div>
              {saved && (
                <BiologyModelActions
                  key={current.id}
                  workspace={workspace}
                  result={current}
                  commit={commit}
                  busy={busy}
                  onReplace={() => loadSettings(current, true)}
                  onRemoved={() => {
                    setSelected(null);
                    setReviewed(false);
                  }}
                  onError={setError}
                />
              )}
              {options.replace_result_id && (
                <p className="notice">
                  The next fit will replace the selected saved model while
                  preserving its output identifiers and population connections.
                </p>
              )}
              {job?.apply_blocker && (
                <p className="notice warning">{job.apply_blocker}</p>
              )}
              {job?.status === "succeeded" && !saved && (
                <>
                  <label className="check-row">
                    <input
                      type="checkbox"
                      checked={reviewed}
                      onChange={(e) => setReviewed(e.target.checked)}
                    />
                    I reviewed the phase curves, constraints and fit warnings.
                  </label>
                  <button
                    type="button"
                    className="button primary"
                    disabled={
                      busy ||
                      !reviewed ||
                      !!current.stale ||
                      result.isFetching ||
                      !job.can_apply
                    }
                    onClick={() =>
                      act(async () => {
                        await commit(
                          `/cell-cycle/jobs/${current.id}/apply`,
                          {},
                          "Cell-cycle fit and probabilities saved",
                        );
                        await jobs.refetch();
                      })
                    }
                  >
                    <Check size={16} />
                    Save fit to workspace
                  </button>
                </>
              )}
              {!!saved && (
                <div className="button-row">
                  {workspace.gates
                    .filter(
                      (g) =>
                        g.sample_id === fit.sample_id &&
                        g.provenance?.cell_cycle_id === current.id,
                    )
                    .map((g) => (
                      <button
                        type="button"
                        className="button small"
                        key={g.id}
                        onClick={() => onExplore(fit.sample_id, g.id)}
                      >
                        <ArrowUpRight size={15} />
                        Explore {g.name.split(" · ").at(-1)}
                      </button>
                    ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
