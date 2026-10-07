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
  GitBranch,
  LoaderCircle,
  Play,
  RefreshCw,
  X,
} from "lucide-react";
import { api, download, post } from "../api";
import type {
  Gate,
  ProliferationJob,
  ProliferationRequest,
  ProliferationResult,
  Sample,
  Workspace,
} from "../types";
import { channelLabel, formatNumber } from "../types";
import type { Commit } from "./Editors";
import { Empty, ErrorState, Tag } from "./Common";
import { BiologyModelActions } from "./BiologyModelActions";
import {
  generationColors,
  availableRefitName,
  HistogramFitChart,
  nullableNumber,
  PeakConstraintEditor,
} from "./BiologyCommon";

type Settings = Omit<
  ProliferationRequest,
  "revision" | "name" | "algorithm" | "inputs" | "channel"
>;
const defaults: Settings = {
  replace_result_id: null,
  compensated: true,
  distribution: "lognormal",
  histogram_space: "log2",
  generations: 6,
  bins: 512,
  range_min: null,
  range_max: null,
  undivided_control: null,
  control_mode: "fix_mean",
  control_range_min: null,
  control_range_max: null,
  undivided_mean: {},
  peak_ratio: { fixed: 0.5 },
  dye_cv: {},
  autofluorescence_control: null,
  background: 0,
  background_sd: 0,
  objective: "poisson",
  maximum_evaluations: 800,
  create_generation_gates: true,
};

export function ProliferationPanel({
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
  onExplore: (sid: string, gid: string) => void;
}) {
  const cache = useQueryClient();
  const first = sample ?? workspace.samples[0];
  const [sources, setSources] = useState<Record<string, string>>(() =>
    first ? { [first.id]: gate?.id ?? "" } : {},
  );
  const [channel, setChannel] = useState(
    first?.channels.find((c) =>
      /CFSE|CellTrace|CTV|Violet|PKH|eFluor/i.test(`${c.name} ${c.label}`),
    )?.name ??
      first?.channels.find((c) => !/^(FSC|SSC|Time)/i.test(c.name))?.name ??
      first?.channels[0]?.name ??
      "",
  );
  const [name, setName] = useState("Proliferation 01");
  const [settings, setSettings] = useState<Settings>(defaults);
  const [settingsVersion, setSettingsVersion] = useState(0);
  const [selected, setSelected] = useState<string | null>(
    workspace.proliferation_results?.at(-1)?.id ?? null,
  );
  const [fitSample, setFitSample] = useState(first?.id ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [reviewed, setReviewed] = useState(false);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState(false);
  const base = `/workspaces/${workspace.id}/proliferation`;
  const inputs = workspace.samples.filter((s) => s.id in sources);
  const controls = [
    settings.undivided_control,
    settings.autofluorescence_control,
  ]
    .filter((c) => c !== null)
    .map((c) => workspace.samples.find((s) => s.id === c.sample_id))
    .filter((s) => s !== undefined);
  const channels = (inputs[0]?.channels ?? []).filter((c) =>
    [...inputs, ...controls].every((s) =>
      s.channels.some((v) => v.name === c.name),
    ),
  );
  const jobs = useQuery({
    queryKey: [workspace.id, "proliferation-jobs", workspace.revision],
    queryFn: () => api<ProliferationJob[]>(`${base}/jobs`),
    refetchInterval: 1000,
  });
  const job = jobs.data?.find((j) => j.id === selected);
  const saved = workspace.proliferation_results?.find((r) => r.id === selected);
  const result = useQuery({
    queryKey: [
      workspace.id,
      "proliferation-result",
      selected,
      workspace.revision,
    ],
    queryFn: () => api<ProliferationResult>(`${base}/${selected}`),
    enabled:
      !!selected &&
      (!!saved || job?.status === "succeeded" || job?.status === "applied"),
    placeholderData: keepPreviousData,
  });
  const current = result.data?.id === selected ? result.data : undefined;
  const fit =
    current?.fits.find((f) => f.sample_id === fitSample) ?? current?.fits[0];
  const archived = (workspace.proliferation_results ?? []).filter(
    (r) => !jobs.data?.some((j) => j.id === r.id),
  );
  const labels = fit?.fractions.map((_, i) => `G${i}`) ?? [];
  const nameFor = (sid: string) =>
    workspace.samples.find((s) => s.id === sid)?.name ?? "Removed sample";
  const set = <K extends keyof Settings>(key: K, value: Settings[K]) =>
    setSettings((s) => ({ ...s, [key]: value }));
  function choose(id: string) {
    setSelected(id);
    setReviewed(false);
    setError("");
  }
  async function act(action: () => Promise<unknown>) {
    setError("");
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  function loadSettings(r: ProliferationResult, replace = false) {
    const {
      revision: _revision,
      name: priorName,
      algorithm: _algorithm,
      inputs: priorInputs,
      channel: priorChannel,
      ...options
    } = r.request;
    setName(
      replace
        ? priorName
        : availableRefitName(
            priorName,
            workspace.proliferation_results.map((v) => v.request.name),
          ),
    );
    setExpanded(false);
    setChannel(priorChannel);
    setSettings({ ...options, replace_result_id: replace ? r.id : null });
    setSettingsVersion((v) => v + 1);
    setSources(
      Object.fromEntries(
        priorInputs
          .filter((v) => workspace.samples.some((s) => s.id === v.sample_id))
          .map((v) => [
            v.sample_id,
            workspace.gates.some((g) => g.id === v.gate_id)
              ? (v.gate_id ?? "")
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
      const created = await post<ProliferationJob>(`${base}/jobs`, {
        ...settings,
        revision: workspace.revision,
        name,
        channel,
        algorithm: "proliferation",
        inputs: inputs.map((s) => ({
          sample_id: s.id,
          gate_id: sources[s.id] || null,
        })),
      } satisfies ProliferationRequest);
      choose(created.id);
      await cache.invalidateQueries({
        queryKey: [workspace.id, "proliferation-jobs"],
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  }
  function controlEditor(
    kind: "undivided_control" | "autofluorescence_control",
    label: string,
  ) {
    const source = settings[kind];
    return (
      <div className="population-control">
        <label className="field">
          {label}
          <select
            aria-label={label}
            value={source?.sample_id ?? ""}
            onChange={(e) => {
              const next = e.target.value
                ? { sample_id: e.target.value, gate_id: null }
                : null;
              setSettings((s) => ({
                ...s,
                [kind]: next,
                ...(kind === "undivided_control"
                  ? {
                      undivided_mean: {},
                      control_range_min: null,
                      control_range_max: null,
                      ...(s.control_mode === "fix_mean_cv"
                        ? { dye_cv: {} }
                        : {}),
                    }
                  : {}),
              }));
            }}
          >
            <option value="">
              {kind === "undivided_control"
                ? "Manual generation-zero intensity"
                : "Manual background"}
            </option>
            {workspace.samples.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        {source && (
          <label className="field">
            {label} population
            <select
              aria-label={`${label} population`}
              value={source.gate_id ?? ""}
              onChange={(e) =>
                set(kind, { ...source, gate_id: e.target.value || null })
              }
            >
              <option value="">All events</option>
              {workspace.gates
                .filter((g) => g.sample_id === source.sample_id)
                .map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name}
                  </option>
                ))}
            </select>
          </label>
        )}
      </div>
    );
  }
  if (!workspace.samples.length)
    return (
      <Empty
        icon={<GitBranch size={32} />}
        title="Model dye dilution"
        text="Import stained samples and an undivided control to estimate generation counts and precursor-weighted proliferation statistics."
      />
    );
  return (
    <div className="panel-view proliferation-panel">
      <div className="panel-heading">
        <div>
          <div className="eyebrow">BIOLOGY</div>
          <h2>Proliferation</h2>
          <p>
            Anchor the undivided peak, review each generation, and compare
            precursor responses.
          </p>
        </div>
        <div className="button-group">
          <Tag color="green">Full-event fits</Tag>
          {current && (
            <button
              className="button small"
              onClick={() => setExpanded((v) => !v)}
            >
              {expanded ? "Show fit setup" : "Expand review"}
            </button>
          )}
        </div>
      </div>
      {error && <ErrorState error={new Error(error)} />}
      <div
        className={`cell-cycle-workspace ${expanded ? "biology-review-expanded" : ""}`}
      >
        <form className="card cell-cycle-options" onSubmit={run}>
          <h3>Fit setup</h3>
          <label className="field">
            Model name
            <input
              aria-label="Proliferation model name"
              required
              maxLength={125}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <div className="section-heading">
            <h4>Source populations</h4>
            <div className="button-group">
              <button
                type="button"
                className="button small ghost"
                onClick={() =>
                  setSources(first ? { [first.id]: gate?.id ?? "" } : {})
                }
              >
                Current
              </button>
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
            </div>
          </div>
          {workspace.groups.length > 0 && (
            <label className="field">
              Sample group
              <select
                aria-label="Proliferation sample group"
                value=""
                onChange={(e) => {
                  const group = workspace.groups.find(
                    (g) => g.id === e.target.value,
                  );
                  if (group)
                    setSources(
                      Object.fromEntries(
                        group.sample_ids.map((id) => [id, ""]),
                      ),
                    );
                }}
              >
                <option value="">Choose a group</option>
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
              <div className="population-source" key={s.id}>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={s.id in sources}
                    aria-label={`Fit proliferation ${s.name}`}
                    onChange={(e) =>
                      setSources((prior) => {
                        const next = { ...prior };
                        if (e.target.checked) next[s.id] = "";
                        else delete next[s.id];
                        return next;
                      })
                    }
                  />
                  {s.name}
                  <small>{s.event_count.toLocaleString()} events</small>
                </label>
                {s.id in sources && (
                  <select
                    aria-label={`Proliferation population for ${s.name}`}
                    value={sources[s.id]}
                    onChange={(e) =>
                      setSources((prior) => ({
                        ...prior,
                        [s.id]: e.target.value,
                      }))
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
          <label className="field">
            Dilution dye
            <select
              aria-label="Dilution dye"
              required
              value={channel}
              onChange={(e) => setChannel(e.target.value)}
            >
              <option value="" disabled>
                Select a shared parameter
              </option>
              {channels.map((c) => (
                <option key={c.name} value={c.name}>
                  {channelLabel(c)}
                </option>
              ))}
            </select>
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={settings.compensated}
              onChange={(e) => set("compensated", e.target.checked)}
            />
            Use compensated fluorescence
          </label>
          <h4>Generation zero and background</h4>
          {controlEditor("undivided_control", "Undivided control")}
          {settings.undivided_control && (
            <>
              <label className="field">
                Undivided calibration
                <select
                  aria-label="Undivided calibration"
                  value={settings.control_mode}
                  onChange={(e) =>
                    setSettings((s) => ({
                      ...s,
                      control_mode: e.target.value as Settings["control_mode"],
                      undivided_mean: {},
                      ...(e.target.value === "fix_mean_cv"
                        ? { dye_cv: {} }
                        : {}),
                    }))
                  }
                >
                  <option value="fix_mean">
                    Fix generation-zero intensity
                  </option>
                  <option value="fix_mean_cv">Fix intensity and dye CV</option>
                  <option value="initial">
                    Use control as initial estimate
                  </option>
                </select>
              </label>
              <div className="form-grid">
                <label className="field">
                  Control intensity minimum
                  <input
                    aria-label="Control intensity minimum"
                    type="number"
                    step="any"
                    min="0.000000001"
                    value={settings.control_range_min ?? ""}
                    onChange={(e) =>
                      set("control_range_min", nullableNumber(e.target.value))
                    }
                  />
                </label>
                <label className="field">
                  Control intensity maximum
                  <input
                    aria-label="Control intensity maximum"
                    type="number"
                    step="any"
                    min="0.000000001"
                    value={settings.control_range_max ?? ""}
                    onChange={(e) =>
                      set("control_range_max", nullableNumber(e.target.value))
                    }
                  />
                </label>
              </div>
            </>
          )}
          {(!settings.undivided_control ||
            settings.control_mode === "initial") && (
            <PeakConstraintEditor
              key={`mean-${settingsVersion}`}
              label="Generation-zero intensity"
              value={settings.undivided_mean}
              onChange={(v) => set("undivided_mean", v)}
              fixedDefault={1000}
              rangeDefault={[750, 1330]}
            />
          )}
          <p className="muted small">
            A control or manual generation-zero value is required. A missing
            undivided peak cannot be labeled from peak order alone.
          </p>
          {controlEditor("autofluorescence_control", "Unstained control")}
          {!settings.autofluorescence_control && (
            <div className="form-grid">
              <label className="field">
                Background intensity
                <input
                  aria-label="Background intensity"
                  type="number"
                  min="0"
                  step="any"
                  value={settings.background}
                  onChange={(e) => set("background", Number(e.target.value))}
                />
              </label>
              <label className="field">
                Background SD
                <input
                  aria-label="Background SD"
                  type="number"
                  min="0"
                  step="any"
                  value={settings.background_sd}
                  onChange={(e) => set("background_sd", Number(e.target.value))}
                />
              </label>
            </div>
          )}
          <p className="muted small">
            Background is added to the diluted dye. Background spread can merge
            late generations.
          </p>
          <div className="form-grid">
            <label className="field">
              Last generation
              <input
                aria-label="Last generation"
                type="number"
                min="0"
                max="12"
                required
                value={settings.generations}
                onChange={(e) => set("generations", Number(e.target.value))}
              />
            </label>
            <label className="field">
              Dye distribution
              <select
                aria-label="Dye distribution"
                value={settings.distribution}
                onChange={(e) =>
                  set(
                    "distribution",
                    e.target.value as Settings["distribution"],
                  )
                }
              >
                <option value="lognormal">Lognormal</option>
                <option value="gaussian">Gaussian</option>
              </select>
            </label>
          </div>
          <PeakConstraintEditor
            key={`ratio-${settingsVersion}`}
            label="Dye peak ratio"
            value={settings.peak_ratio}
            onChange={(v) => set("peak_ratio", v)}
            initialDefault={0.5}
            fixedDefault={0.5}
            rangeDefault={[0.45, 0.55]}
          />
          {!(
            settings.undivided_control &&
            settings.control_mode === "fix_mean_cv"
          ) && (
            <PeakConstraintEditor
              key={`cv-${settingsVersion}`}
              label="Dye CV (%)"
              value={settings.dye_cv}
              onChange={(v) => set("dye_cv", v)}
              initialDefault={25}
              fixedDefault={25}
              rangeDefault={[5, 80]}
            />
          )}
          <p className="muted small">
            CV describes the dye signal above background. A ratio of 0.5
            corresponds to equal dye partitioning.
          </p>
          <details>
            <summary>Histogram and optimization</summary>
            <div className="form-grid">
              <label className="field">
                Histogram spacing
                <select
                  aria-label="Proliferation histogram spacing"
                  value={settings.histogram_space}
                  onChange={(e) =>
                    setSettings((s) => ({
                      ...s,
                      histogram_space: e.target
                        .value as Settings["histogram_space"],
                      range_min: null,
                      range_max: null,
                    }))
                  }
                >
                  <option value="log2">Logarithmic</option>
                  <option value="linear">
                    Linear, retains nonpositive intensity
                  </option>
                </select>
              </label>
              <label className="field">
                Bins
                <input
                  aria-label="Proliferation histogram bins"
                  type="number"
                  min="64"
                  max="2048"
                  value={settings.bins}
                  onChange={(e) => set("bins", Number(e.target.value))}
                />
              </label>
              <label className="field">
                Fit minimum
                <input
                  aria-label="Proliferation fit minimum"
                  type="number"
                  step="any"
                  value={settings.range_min ?? ""}
                  onChange={(e) =>
                    set("range_min", nullableNumber(e.target.value))
                  }
                />
              </label>
              <label className="field">
                Fit maximum
                <input
                  aria-label="Proliferation fit maximum"
                  type="number"
                  step="any"
                  value={settings.range_max ?? ""}
                  onChange={(e) =>
                    set("range_max", nullableNumber(e.target.value))
                  }
                />
              </label>
            </div>
            <label className="field">
              Objective
              <select
                aria-label="Proliferation objective"
                value={settings.objective}
                onChange={(e) =>
                  set("objective", e.target.value as Settings["objective"])
                }
              >
                <option value="poisson">Poisson likelihood</option>
                <option value="weighted_least_squares">
                  Weighted least squares
                </option>
              </select>
            </label>
            <label className="field">
              Evaluations per attempt
              <input
                aria-label="Proliferation maximum evaluations"
                type="number"
                min="100"
                max="3000"
                value={settings.maximum_evaluations}
                onChange={(e) =>
                  set("maximum_evaluations", Number(e.target.value))
                }
              />
            </label>
          </details>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={settings.create_generation_gates}
              onChange={(e) => set("create_generation_gates", e.target.checked)}
            />
            Create generation populations when saved
          </label>
          <button
            className="button primary"
            type="submit"
            disabled={
              busy ||
              submitting ||
              !inputs.length ||
              !channels.some((c) => c.name === channel)
            }
          >
            {submitting ? (
              <LoaderCircle className="spin" size={16} />
            ) : (
              <Play size={16} />
            )}
            Fit proliferation
          </button>
        </form>
        <div className="cell-cycle-results">
          <div className="card">
            <div className="section-heading">
              <h3>Models and jobs</h3>
              <span className="muted small">
                {(jobs.data?.length ?? 0) + archived.length} models
              </span>
            </div>
            {jobs.error && <ErrorState error={jobs.error} />}
            {!jobs.data?.length && !archived.length && (
              <p className="muted">
                Fit a source population to review generation counts and control
                calibration.
              </p>
            )}
            {jobs.data?.map((j) => (
              <div
                className={`cell-cycle-job ${selected === j.id ? "selected" : ""}`}
                key={j.id}
              >
                <button
                  className="button ghost"
                  onClick={() => choose(j.id)}
                  aria-label={`Review proliferation ${j.request.name}`}
                >
                  <strong>{j.request.name}</strong>
                  <span>
                    {j.request.inputs.length} sample
                    {j.request.inputs.length === 1 ? "" : "s"} · G0–G
                    {j.request.generations}
                  </span>
                  <small>{j.error ?? j.stage}</small>
                  {["queued", "running"].includes(j.status) && (
                    <progress value={j.progress} max="1" />
                  )}
                </button>
                <Tag
                  color={
                    j.stale || j.status === "failed"
                      ? "amber"
                      : j.status === "applied"
                        ? "green"
                        : "blue"
                  }
                >
                  {j.stale ? "Stale" : j.status}
                </Tag>
                {["queued", "running"].includes(j.status) && (
                  <button
                    className="icon-button"
                    aria-label={`Cancel proliferation ${j.request.name}`}
                    onClick={() =>
                      act(async () => {
                        await post(`${base}/jobs/${j.id}/cancel`, {});
                        await cache.invalidateQueries({
                          queryKey: [workspace.id, "proliferation-jobs"],
                        });
                      })
                    }
                  >
                    <X size={15} />
                  </button>
                )}
              </div>
            ))}
            {archived.map((r) => (
              <div
                className={`cell-cycle-job ${selected === r.id ? "selected" : ""}`}
                key={r.id}
              >
                <button
                  className="button ghost"
                  aria-label={`Review proliferation ${r.request.name}`}
                  onClick={() => choose(r.id)}
                >
                  <strong>{r.request.name}</strong>
                  <span>{r.fits.length} samples · saved in project</span>
                </button>
                <Tag color="green">Saved</Tag>
              </div>
            ))}
          </div>
          {result.error && <ErrorState error={result.error} />}{" "}
          {result.isFetching && !current && (
            <div className="card muted">
              <LoaderCircle size={18} className="spin" /> Loading generation
              fit…
            </div>
          )}
          {current && fit && (
            <div className="card cell-cycle-review proliferation-review">
              <div className="cell-cycle-review-heading">
                <div>
                  <h3>{current.request.name}</h3>
                  <p className="muted small">
                    {current.request.distribution} dye ·{" "}
                    {current.duration_seconds.toFixed(2)} seconds ·{" "}
                    {fit.data.fitted_count.toLocaleString()} fitted events
                  </p>
                </div>
                <Tag color={current.stale ? "amber" : saved ? "green" : "blue"}>
                  {current.stale
                    ? "Scientific inputs changed"
                    : saved
                      ? "Saved model"
                      : "Review fit"}
                </Tag>
              </div>
              <label className="field">
                Fitted sample
                <select
                  aria-label="Proliferation fitted sample"
                  value={fit.sample_id}
                  onChange={(e) => setFitSample(e.target.value)}
                >
                  {current.fits.map((f) => (
                    <option key={f.sample_id} value={f.sample_id}>
                      {nameFor(f.sample_id)}
                    </option>
                  ))}
                </select>
              </label>
              <HistogramFitChart
                key={`${current.id}-${fit.sample_id}`}
                fit={fit}
                channel={current.request.channel}
                labels={labels}
                logarithmic={current.request.histogram_space === "log2"}
              />
              <div
                className="cell-cycle-metrics proliferation-metrics"
                aria-label="Precursor-weighted statistics"
              >
                {[
                  [
                    "Precursor frequency",
                    `${(fit.statistics.precursor_frequency * 100).toFixed(2)}%`,
                  ],
                  [
                    "Division index",
                    formatNumber(fit.statistics.division_index, 3),
                  ],
                  [
                    "Proliferation index",
                    fit.statistics.proliferation_index == null
                      ? "Undefined"
                      : formatNumber(fit.statistics.proliferation_index, 3),
                  ],
                  [
                    "Expansion index",
                    formatNumber(fit.statistics.expansion_index, 3),
                  ],
                  [
                    "Replication index",
                    fit.statistics.replication_index == null
                      ? "Undefined"
                      : formatNumber(fit.statistics.replication_index, 3),
                  ],
                  [
                    "Observed divided events",
                    `${(fit.statistics.observed_divided_fraction * 100).toFixed(2)}%`,
                  ],
                ].map(([label, value]) => (
                  <div key={label}>
                    <span>{label}</span>
                    <strong>{value}</strong>
                  </div>
                ))}
              </div>
              <p className="muted small">
                Indices use modeled generation counts weighted by 1/2ᵍ.
                Precursor frequency differs from the fraction of collected
                events assigned to divided generations. No responding precursors
                makes proliferation and replication indices undefined.
              </p>
              <div className="table-scroll">
                <table aria-label="Generation counts">
                  <thead>
                    <tr>
                      <th>Generation</th>
                      <th>Peak intensity</th>
                      <th>Model fraction</th>
                      <th>Model events</th>
                      <th>Expected events</th>
                      <th>Assigned events</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {labels.map((label, i) => {
                      const population = workspace.gates.find(
                        (g) =>
                          g.sample_id === fit.sample_id &&
                          g.provenance?.proliferation_id === current.id &&
                          g.bounds[0] === i - 0.5,
                      );
                      const se = fit.diagnostics.fraction_standard_errors?.[i];
                      return (
                        <tr key={i}>
                          <td>
                            <span style={{ color: generationColors[i] }}>
                              {label}
                              {i === 0 ? " · undivided" : ""}
                            </span>
                          </td>
                          <td>{formatNumber(fit.peak_locations[i], 3)}</td>
                          <td>
                            {(fit.fractions[i] * 100).toFixed(2)}%
                            <small>
                              {se != null
                                ? `Approx. 95%: ${Math.max(0, (fit.fractions[i] - 1.96 * se) * 100).toFixed(2)}–${Math.min(100, (fit.fractions[i] + 1.96 * se) * 100).toFixed(2)}%`
                                : "Uncertainty unavailable"}
                            </small>
                          </td>
                          <td>
                            {formatNumber(
                              fit.fractions[i] * fit.data.fitted_count,
                              1,
                            )}
                          </td>
                          <td>{formatNumber(fit.expected_counts[i], 1)}</td>
                          <td>{fit.assigned_counts[i].toLocaleString()}</td>
                          <td>
                            {population && (
                              <button
                                className="button small ghost"
                                aria-label={`Explore generation ${i}`}
                                onClick={() =>
                                  onExplore(fit.sample_id, population.id)
                                }
                              >
                                <ArrowUpRight size={13} />
                                Explore
                              </button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="muted small">
                Expected events sum posterior probabilities; assigned events use
                the largest probability. Generated populations retain original
                event identities and stay inside their source population.
              </p>
              <dl>
                <dt>Generation-zero intensity</dt>
                <dd>{formatNumber(fit.parameters.undivided_mean, 4)}</dd>
                <dt>Dye peak ratio / CV</dt>
                <dd>
                  {fit.parameters.peak_ratio.toFixed(5)} /{" "}
                  {fit.parameters.dye_cv.toFixed(2)}%
                </dd>
                <dt>Background / SD</dt>
                <dd>
                  {formatNumber(fit.parameters.background, 3)} /{" "}
                  {formatNumber(fit.parameters.background_sd, 3)}
                </dd>
                <dt>Source / finite / fitted events</dt>
                <dd>
                  {fit.data.population_count.toLocaleString()} /{" "}
                  {fit.data.finite_count.toLocaleString()} /{" "}
                  {fit.data.fitted_count.toLocaleString()}
                </dd>
                <dt>RMSD / Poisson deviance</dt>
                <dd>
                  {formatNumber(fit.diagnostics.rmsd_events_per_bin, 3)} /{" "}
                  {formatNumber(fit.diagnostics.poisson_deviance, 2)}
                </dd>
                <dt>Convergence</dt>
                <dd>
                  {fit.diagnostics.converged ? "Converged" : "Not converged"}
                </dd>
              </dl>
              <div className="notice compact">
                {[...current.warnings, ...fit.warnings].map((v, i) => (
                  <p key={i}>{v}</p>
                ))}
              </div>
              <details>
                <summary>Control calibration</summary>
                <pre>{JSON.stringify(current.calibration, null, 2)}</pre>
              </details>
              <details>
                <summary>Fit diagnostics and uncertainty</summary>
                <p className="muted small">
                  {fit.diagnostics.uncertainty_basis}
                </p>
                <pre>{JSON.stringify(fit.diagnostics, null, 2)}</pre>
              </details>
              <details>
                <summary>Saved fit settings</summary>
                <pre>{JSON.stringify(current.request, null, 2)}</pre>
              </details>
              <div className="button-group population-exports">
                <button
                  className="button small"
                  onClick={() =>
                    act(() =>
                      download(
                        `${base}/${current.id}/statistics`,
                        `${current.request.name}-statistics.csv`,
                      ),
                    )
                  }
                >
                  <ArrowDownToLine size={14} />
                  Statistics CSV
                </button>
                <button
                  className="button small"
                  onClick={() =>
                    act(() =>
                      download(
                        `${base}/${current.id}/events?sample_id=${fit.sample_id}`,
                        `${current.request.name}-event-probabilities.csv`,
                      ),
                    )
                  }
                >
                  Event probabilities
                </button>
                <button
                  className="button small"
                  onClick={() =>
                    act(() =>
                      download(
                        `${base}/${current.id}/figure?sample_id=${fit.sample_id}`,
                        `${current.request.name}.svg`,
                      ),
                    )
                  }
                >
                  Figure SVG
                </button>
                <button
                  className="button small"
                  onClick={() =>
                    act(() =>
                      download(
                        `${base}/${current.id}/report`,
                        `${current.request.name}.json`,
                      ),
                    )
                  }
                >
                  Model JSON
                </button>
                <button
                  className="button small ghost"
                  onClick={() => loadSettings(current)}
                >
                  <RefreshCw size={14} />
                  Refit settings
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
              {settings.replace_result_id && (
                <p className="notice">
                  The next fit will replace the selected saved model while
                  preserving its output identifiers and population connections.
                </p>
              )}
              {job?.apply_blocker && (
                <p className="notice warning">{job.apply_blocker}</p>
              )}
              {job?.status === "succeeded" && !saved && (
                <div className="population-save">
                  <label className="checkbox">
                    <input
                      type="checkbox"
                      checked={reviewed}
                      aria-label="I reviewed the proliferation fit"
                      onChange={(e) => setReviewed(e.target.checked)}
                    />
                    I reviewed controls, peak overlap, residuals and
                    assignments.
                  </label>
                  <button
                    className="button primary"
                    disabled={
                      busy || !reviewed || !!current.stale || !job.can_apply
                    }
                    onClick={() =>
                      act(async () => {
                        await commit(
                          `/proliferation/jobs/${current.id}/apply`,
                          {},
                          "Saved proliferation model",
                        );
                        await cache.invalidateQueries({
                          queryKey: [workspace.id, "proliferation-jobs"],
                        });
                      })
                    }
                  >
                    <Check size={16} />
                    Save proliferation model
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
