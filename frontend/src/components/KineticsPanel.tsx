import { useState } from "react";
import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  Activity,
  ArrowDownToLine,
  ArrowUpRight,
  Check,
  LoaderCircle,
  Play,
  Plus,
  RefreshCw,
  X,
} from "lucide-react";
import { api, download, post } from "../api";
import type {
  Gate,
  KineticsFit,
  KineticsJob,
  KineticsRange,
  KineticsRequest,
  KineticsResult,
  Sample,
  Workspace,
} from "../types";
import { channelLabel, id } from "../types";
import type { Commit } from "./Editors";
import { Empty, ErrorState, Tag } from "./Common";
import {
  availableRefitName,
  generationColors,
  nullableNumber,
} from "./BiologyCommon";
import { BiologyModelActions } from "./BiologyModelActions";

type Settings = Omit<
  KineticsRequest,
  "revision" | "name" | "algorithm" | "inputs" | "channel"
>;
const defaults: Settings = {
  compensated: true,
  time_channel: "Time",
  event_rate: null,
  time_multiplier: 1,
  time_offsets: {},
  clock_policy: "reject",
  time_min: null,
  time_max: null,
  bins: 256,
  minimum_events: 1,
  statistic: "median",
  percentile: 50,
  threshold_mode: "absolute",
  threshold: 0,
  baseline: null,
  baseline_percentile: 95,
  above_threshold_only: false,
  smoothing: "none",
  smoothing_width: 5,
  gaussian_sigma: 1,
  ranges: [],
  create_range_gates: true,
  create_responder_gates: false,
  replace_result_id: null,
};
const numberText = (n: number | null | undefined) =>
  n == null
    ? "Undefined"
    : n.toLocaleString(undefined, { maximumSignificantDigits: 7 });
const range = (
  start = 0,
  end = 60,
  name = "Range 1",
  color = "#38d9ba",
): KineticsRange => ({ id: id(), name, start, end, color });

function RangeEditor({
  value,
  change,
  remove,
  label,
}: {
  value: KineticsRange;
  change: (r: KineticsRange) => void;
  remove?: () => void;
  label: string;
}) {
  return (
    <div className="kinetics-range-editor">
      <label className="field">
        Name
        <input
          aria-label={`${label} name`}
          required
          maxLength={160}
          value={value.name}
          onChange={(e) => change({ ...value, name: e.target.value })}
        />
      </label>
      <label className="field">
        Start
        <input
          aria-label={`${label} start`}
          type="number"
          step="any"
          required
          value={Number.isFinite(value.start) ? value.start : ""}
          onChange={(e) => change({ ...value, start: e.target.valueAsNumber })}
        />
      </label>
      <label className="field">
        End
        <input
          aria-label={`${label} end`}
          type="number"
          step="any"
          required
          value={Number.isFinite(value.end) ? value.end : ""}
          onChange={(e) => change({ ...value, end: e.target.valueAsNumber })}
        />
      </label>
      <input
        aria-label={`${label} color`}
        type="color"
        value={value.color}
        onChange={(e) => change({ ...value, color: e.target.value })}
      />
      {remove && (
        <button
          className="icon-button"
          aria-label={`Remove ${label.toLowerCase()}`}
          type="button"
          onClick={remove}
        >
          <X size={14} />
        </button>
      )}
    </div>
  );
}

function TimeChart({
  fit,
  overlays,
  names,
}: {
  fit: KineticsFit;
  overlays: KineticsFit[];
  names: (id: string) => string;
}) {
  const [index, setIndex] = useState(0);
  const b = fit.bins[Math.min(index, fit.bins.length - 1)];
  const curves = [
    fit,
    ...overlays.filter((f) => f.sample_id !== fit.sample_id && f.time_domain),
  ];
  const domains = curves.flatMap((f) => f.time_domain ?? []);
  const ys = curves.flatMap((f) =>
    f.bins.flatMap((b) => (b.value == null ? [] : [b.value])),
  );
  if (!fit.time_domain || !ys.length)
    return (
      <p className="muted">
        The curve is undefined in this time domain. Review timing, source events
        and minimum events per bin.
      </p>
    );
  const lo = Math.min(...domains),
    hi = Math.max(...domains);
  let ymin = ys.reduce((a, b) => Math.min(a, b), Infinity),
    ymax = ys.reduce((a, b) => Math.max(a, b), -Infinity);
  const xs = Math.max(Math.abs(lo), Math.abs(hi), 1e-300),
    scale = Math.max(Math.abs(ymin), Math.abs(ymax), 1e-300);
  ymin /= scale;
  ymax /= scale;
  if (ymin === ymax) {
    ymin -= 0.5;
    ymax += 0.5;
  }
  const px = (n: number) =>
    78 + (810 * (n / xs - lo / xs)) / (hi / xs - lo / xs);
  const py = (n: number) => 350 - (270 * (n / scale - ymin)) / (ymax - ymin);
  function segments(f: KineticsFit, raw = false) {
    const result: { x: number; y: number }[][] = [];
    let current: { x: number; y: number }[] = [];
    for (const bin of f.bins) {
      const value = raw ? bin.raw_value : bin.value;
      if (value == null) {
        if (current.length) result.push(current);
        current = [];
      } else current.push({ x: px(bin.center), y: py(value) });
    }
    if (current.length) result.push(current);
    return result;
  }
  return (
    <div className="population-chart kinetics-chart">
      <div className="cell-cycle-legend">
        {curves.map((f, i) => (
          <span
            key={f.sample_id}
            style={{ color: generationColors[i % generationColors.length] }}
          >
            {names(f.sample_id)}
          </span>
        ))}
        <span className="muted">Dashed: unsmoothed curve</span>
      </div>
      <svg
        viewBox="0 0 930 410"
        role="img"
        aria-label="Kinetics time course with measured gaps"
      >
        <title>
          Signal over aligned acquisition time. Missing bins remain unmeasured.
        </title>
        {fit.ranges.map((r) => {
          const a = Math.max(lo, r.start),
            z = Math.min(hi, r.end);
          return (
            a < z && (
              <rect
                key={r.id}
                x={px(a)}
                y="80"
                width={px(z) - px(a)}
                height="270"
                fill={r.color}
                opacity="0.08"
              />
            )
          );
        })}
        {[0, 1, 2, 3, 4].map((i) => (
          <g key={i}>
            <line
              x1="78"
              x2="888"
              y1={350 - i * 67.5}
              y2={350 - i * 67.5}
              stroke="var(--line)"
            />
            <text x="67" y={355 - i * 67.5} textAnchor="end">
              {numberText((ymin + ((ymax - ymin) * i) / 4) * scale)}
            </text>
            <text x={78 + i * 202.5} y="378" textAnchor="middle">
              {numberText(lo * (1 - i / 4) + (hi * i) / 4)}
            </text>
          </g>
        ))}
        {curves.map((f, i) => (
          <g key={f.sample_id}>
            {[
              ...segments(f, true).map((p) => ({ points: p, raw: true })),
              ...segments(f).map((p) => ({ points: p, raw: false })),
            ].map(({ points, raw }, j) =>
              points.length === 1 ? (
                <circle
                  key={j}
                  cx={points[0].x}
                  cy={points[0].y}
                  r={raw ? 1.5 : 3}
                  fill={generationColors[i % generationColors.length]}
                />
              ) : (
                <polyline
                  key={j}
                  points={points.map((p) => `${p.x},${p.y}`).join(" ")}
                  fill="none"
                  stroke={generationColors[i % generationColors.length]}
                  strokeWidth={raw ? 1 : 2.3}
                  strokeDasharray={raw ? "3 5" : undefined}
                  opacity={raw ? 0.5 : 1}
                />
              ),
            )}
          </g>
        ))}
        {b && (
          <line
            x1={px(b.center)}
            x2={px(b.center)}
            y1="80"
            y2="350"
            stroke="var(--accent)"
            strokeDasharray="4 5"
          />
        )}
        <text x="483" y="405" textAnchor="middle">
          Aligned time
        </text>
      </svg>
      {b && (
        <>
          <label className="field">
            Inspect time bin
            <input
              aria-label="Inspect kinetics time bin"
              type="range"
              min="0"
              max={fit.bins.length - 1}
              value={Math.min(index, fit.bins.length - 1)}
              onChange={(e) => setIndex(Number(e.target.value))}
            />
          </label>
          <div className="cell-cycle-bin" aria-live="polite">
            Time {numberText(b.start)}–{numberText(b.end)} ·{" "}
            {b.population_count.toLocaleString()} source events ·{" "}
            {b.finite_count.toLocaleString()} finite signals ·{" "}
            {numberText(b.responder_count)} responders · Curve{" "}
            {numberText(b.value)} · Raw {numberText(b.raw_value)}
          </div>
        </>
      )}
    </div>
  );
}

export function KineticsPanel({
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
  onExplore: (sid: string, gid: string, time: string, signal: string) => void;
}) {
  const cache = useQueryClient();
  const first = sample ?? workspace.samples[0];
  const [sources, setSources] = useState<Record<string, string>>(() =>
    first ? { [first.id]: gate?.id ?? "" } : {},
  );
  const [channel, setChannel] = useState(
    first?.channels.find((c) => !/^(Time|FSC|SSC)/i.test(c.name))?.name ??
      first?.channels[0]?.name ??
      "",
  );
  const [name, setName] = useState("Kinetics 01");
  const [settings, setSettings] = useState<Settings>(() => ({
    ...defaults,
    time_channel:
      first?.channels.find((c) => /^time$/i.test(c.name))?.name ?? null,
    event_rate: first?.channels.some((c) => /^time$/i.test(c.name))
      ? null
      : 1000,
  }));
  const [selected, setSelected] = useState<string | null>(
    workspace.kinetics_results?.at(-1)?.id ?? null,
  );
  const [fitSample, setFitSample] = useState(first?.id ?? "");
  const [reviewed, setReviewed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [overlay, setOverlay] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const base = `/workspaces/${workspace.id}/kinetics`;
  const inputs = workspace.samples.filter((s) => s.id in sources);
  const channels = (inputs[0]?.channels ?? []).filter((c) =>
    inputs.every((s) => s.channels.some((v) => v.name === c.name)),
  );
  const jobs = useQuery({
    queryKey: [workspace.id, "kinetics-jobs", workspace.revision],
    queryFn: () => api<KineticsJob[]>(`${base}/jobs`),
    refetchInterval: 1000,
  });
  const job = jobs.data?.find((j) => j.id === selected),
    saved = workspace.kinetics_results?.find((r) => r.id === selected);
  const result = useQuery({
    queryKey: [workspace.id, "kinetics-result", selected, workspace.revision],
    queryFn: () => api<KineticsResult>(`${base}/${selected}`),
    enabled:
      !!selected &&
      (!!saved || job?.status === "succeeded" || job?.status === "applied"),
    placeholderData: keepPreviousData,
  });
  const current = result.data?.id === selected ? result.data : undefined;
  const fit =
    current?.fits.find((f) => f.sample_id === fitSample) ?? current?.fits[0];
  const data = current?.data.find((d) => d.sample_id === fit?.sample_id);
  const archived = (workspace.kinetics_results ?? []).filter(
    (r) => !jobs.data?.some((j) => j.id === r.id),
  );
  const names = (sid: string) =>
    workspace.samples.find((s) => s.id === sid)?.name ?? "Removed sample";
  const set = <K extends keyof Settings>(key: K, value: Settings[K]) =>
    setSettings((s) => ({ ...s, [key]: value }));
  function choose(identifier: string) {
    setSelected(identifier);
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
  function load(r: KineticsResult, replace = false) {
    const {
      revision: _rev,
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
            (workspace.kinetics_results ?? []).map((r) => r.request.name),
          ),
    );
    setChannel(priorChannel);
    setSettings({ ...options, replace_result_id: replace ? r.id : null });
    setSources(
      Object.fromEntries(
        priorInputs
          .filter((i) => workspace.samples.some((s) => s.id === i.sample_id))
          .map((i) => [i.sample_id, i.gate_id ?? ""]),
      ),
    );
    setExpanded(false);
    const unavailable = priorInputs.filter(
      (i) =>
        !workspace.samples.some((s) => s.id === i.sample_id) ||
        (i.gate_id && !workspace.gates.some((g) => g.id === i.gate_id)),
    );
    setError(
      unavailable.length
        ? "Some original samples or populations are unavailable. Review the source scope explicitly before recalculating."
        : "",
    );
  }
  function path(g: Gate): string[] {
    const parent = workspace.gates.find((v) => v.id === g.parent_id);
    return [...(parent ? path(parent) : []), g.name];
  }
  function selectGroup(ids: string[]) {
    if (ids.length > 128) {
      setError(
        "Kinetics supports up to 128 samples per batch. Choose a smaller group.",
      );
      return;
    }
    const prototype = gate ? JSON.stringify(path(gate)) : null;
    const next: Record<string, string> = {};
    const missing: string[] = [];
    for (const sid of ids) {
      const matches = prototype
        ? workspace.gates.filter(
            (g) => g.sample_id === sid && JSON.stringify(path(g)) === prototype,
          )
        : [];
      if (prototype && matches.length !== 1) {
        missing.push(names(sid));
        continue;
      }
      next[sid] = matches[0]?.id ?? "";
    }
    setSources(next);
    setError(
      missing.length
        ? `Skipped samples without an unambiguous matching population: ${missing.join(", ")}`
        : "",
    );
  }
  async function run(event: React.SubmitEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const offsets = Object.fromEntries(
        Object.entries(settings.time_offsets).filter(([sid]) => sid in sources),
      );
      const created = await post<KineticsJob>(`${base}/jobs`, {
        ...settings,
        time_offsets: offsets,
        revision: workspace.revision,
        name,
        channel,
        algorithm: "kinetics",
        inputs: inputs.map((s) => ({
          sample_id: s.id,
          gate_id: sources[s.id] || null,
        })),
      });
      choose(created.id);
      setFitSample(inputs[0].id);
      await cache.invalidateQueries({
        queryKey: [workspace.id, "kinetics-jobs"],
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  }
  const numeric = <K extends keyof Settings>(
    key: K,
    label: string,
    min?: number,
    max?: number,
    optional = false,
    step: number | string = "any",
  ) => (
    <label className="field" key={key}>
      {label}
      <input
        aria-label={`Kinetics ${label.toLowerCase()}`}
        type="number"
        step={step}
        min={min}
        max={max}
        required={!optional}
        value={(settings[key] as number | null) ?? ""}
        onChange={(e) =>
          set(
            key,
            (optional
              ? nullableNumber(e.target.value)
              : e.target.valueAsNumber) as Settings[K],
          )
        }
      />
    </label>
  );
  if (!workspace.samples.length)
    return (
      <Empty
        icon={<Activity size={32} />}
        title="Measure time responses"
        text="Import acquisition data to inspect time courses, baseline thresholds and response intervals."
      />
    );
  return (
    <div className="panel-view kinetics-panel">
      <div className="panel-heading">
        <div>
          <div className="eyebrow">TIME RESPONSE</div>
          <h2>Kinetics</h2>
          <p>
            Align acquisition clocks, inspect measured curves and compare
            response intervals.
          </p>
        </div>
        <div className="button-group">
          <Tag color="green">Full-event statistics</Tag>
          {current && (
            <button
              className="button small"
              onClick={() => setExpanded((v) => !v)}
            >
              {expanded ? "Show analysis setup" : "Expand review"}
            </button>
          )}
        </div>
      </div>
      {error && <ErrorState error={new Error(error)} />}
      <div
        className={`cell-cycle-workspace ${expanded ? "biology-review-expanded" : ""}`}
      >
        <form className="card cell-cycle-options" onSubmit={run}>
          <h3>Analysis setup</h3>
          <label className="field">
            Analysis name
            <input
              aria-label="Kinetics analysis name"
              required
              maxLength={125}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          {settings.replace_result_id && (
            <p className="kinetics-note">
              Replacing the live model preserves its output names and time range
              IDs.{" "}
              <button
                className="button small ghost"
                type="button"
                onClick={() => {
                  set("replace_result_id", null);
                  setName(
                    availableRefitName(
                      name,
                      (workspace.kinetics_results ?? []).map(
                        (r) => r.request.name,
                      ),
                    ),
                  );
                }}
              >
                Save as new model
              </button>
            </p>
          )}
          <div className="section-heading">
            <h4>Source populations</h4>
            <div className="button-group">
              <button
                className="button small ghost"
                type="button"
                onClick={() =>
                  setSources(first ? { [first.id]: gate?.id ?? "" } : {})
                }
              >
                Current
              </button>
              <button
                className="button small ghost"
                type="button"
                onClick={() => selectGroup(workspace.samples.map((s) => s.id))}
              >
                All samples
              </button>
            </div>
          </div>
          {!!workspace.groups.length && (
            <label className="field">
              Sample group
              <select
                aria-label="Kinetics sample group"
                value=""
                onChange={(e) => {
                  const g = workspace.groups.find(
                    (g) => g.id === e.target.value,
                  );
                  if (g) selectGroup(g.sample_ids);
                }}
              >
                <option value="">Choose group</option>
                {workspace.groups.map((g) => (
                  <option value={g.id} key={g.id}>
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
                    aria-label={`Analyze kinetics ${s.name}`}
                    type="checkbox"
                    checked={s.id in sources}
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
                </label>
                {s.id in sources && (
                  <>
                    <select
                      aria-label={`Kinetics population ${s.name}`}
                      value={sources[s.id]}
                      onChange={(e) =>
                        setSources((p) => ({ ...p, [s.id]: e.target.value }))
                      }
                    >
                      <option value="">All events</option>
                      {sources[s.id] &&
                        !workspace.gates.some(
                          (g) => g.id === sources[s.id],
                        ) && (
                          <option value={sources[s.id]}>
                            Missing source population — choose explicitly
                          </option>
                        )}
                      {workspace.gates
                        .filter((g) => g.sample_id === s.id)
                        .map((g) => (
                          <option value={g.id} key={g.id}>
                            {path(g).join(" / ")}
                          </option>
                        ))}
                    </select>
                    <label className="field">
                      Time offset
                      <input
                        aria-label={`Kinetics time offset ${s.name}`}
                        type="number"
                        step="any"
                        required
                        value={settings.time_offsets[s.id] ?? 0}
                        onChange={(e) =>
                          set("time_offsets", {
                            ...settings.time_offsets,
                            [s.id]: e.target.valueAsNumber,
                          })
                        }
                      />
                    </label>
                  </>
                )}
              </div>
            ))}
          </div>
          <label className="field">
            Signal parameter
            <select
              aria-label="Kinetics signal parameter"
              value={channel}
              onChange={(e) => setChannel(e.target.value)}
            >
              {channels.map((c) => (
                <option value={c.name} key={c.name}>
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
            Use compensated signal
          </label>
          <h4>Acquisition clock</h4>
          <label className="field">
            Time source
            <select
              aria-label="Kinetics time source"
              value={settings.time_channel ?? ""}
              onChange={(e) =>
                setSettings((s) => ({
                  ...s,
                  time_channel: e.target.value || null,
                  event_rate: e.target.value ? null : 1000,
                }))
              }
            >
              <option value="">Estimate from original event index</option>
              {channels.map((c) => (
                <option value={c.name} key={c.name}>
                  {channelLabel(c)}
                </option>
              ))}
            </select>
          </label>
          {settings.time_channel === null && (
            <>
              {numeric(
                "event_rate",
                "Assumed events per second",
                Number.MIN_VALUE,
              )}
              <p className="kinetics-note">
                Estimated time assumes constant flow. Pauses and flow changes
                are unmeasured.
              </p>
            </>
          )}
          {numeric("time_multiplier", "Time multiplier", Number.MIN_VALUE)}
          <label className="field">
            Clock decreases
            <select
              aria-label="Kinetics clock decreases"
              value={settings.clock_policy}
              onChange={(e) =>
                set("clock_policy", e.target.value as Settings["clock_policy"])
              }
            >
              <option value="reject">Stop for review</option>
              <option value="use_recorded">Pool recorded times</option>
              <option value="unwrap">Unwrap using median positive step</option>
            </select>
          </label>
          <p className="muted small">
            FCS TIMESTEP is applied on import. Verify units when it is missing.
            Multiplier and sample offsets apply to the complete acquisition
            before gating.
          </p>
          <div className="form-grid">
            {numeric("time_min", "Time minimum", undefined, undefined, true)}
            {numeric("time_max", "Time maximum", undefined, undefined, true)}
            {numeric("bins", "Time bins", 8, 4096, false, 1)}
            {numeric(
              "minimum_events",
              "Minimum events per bin",
              1,
              1000000,
              false,
              1,
            )}
          </div>
          <h4>Curve statistic</h4>
          <label className="field">
            Statistic
            <select
              aria-label="Kinetics statistic"
              value={settings.statistic}
              onChange={(e) =>
                setSettings((s) => ({
                  ...s,
                  statistic: e.target.value as Settings["statistic"],
                  above_threshold_only:
                    e.target.value === "percent_positive"
                      ? false
                      : s.above_threshold_only,
                }))
              }
            >
              <option value="median">Median</option>
              <option value="mean">Arithmetic mean</option>
              <option value="geometric_mean">Geometric mean</option>
              <option value="percentile">Percentile</option>
              <option value="percent_positive">Percent above threshold</option>
            </select>
          </label>
          {settings.statistic === "percentile" &&
            numeric("percentile", "Signal percentile", 0, 100)}
          <label className="field">
            Responder threshold
            <select
              aria-label="Kinetics threshold mode"
              value={settings.threshold_mode}
              onChange={(e) =>
                setSettings((s) => ({
                  ...s,
                  threshold_mode: e.target.value as Settings["threshold_mode"],
                  baseline:
                    e.target.value === "baseline_percentile"
                      ? (s.baseline ?? range(0, 30, "Baseline"))
                      : s.baseline,
                }))
              }
            >
              <option value="absolute">Absolute signal</option>
              <option value="baseline_percentile">
                Baseline signal percentile
              </option>
            </select>
          </label>
          {settings.threshold_mode === "absolute"
            ? numeric("threshold", "Absolute threshold")
            : settings.baseline && (
                <>
                  <RangeEditor
                    value={settings.baseline}
                    change={(r) => set("baseline", r)}
                    label="Kinetics baseline"
                  />
                  {numeric(
                    "baseline_percentile",
                    "Baseline percentile",
                    0,
                    100,
                  )}
                </>
              )}
          <label className="checkbox">
            <input
              type="checkbox"
              aria-label="Kinetics use responders only"
              disabled={settings.statistic === "percent_positive"}
              checked={settings.above_threshold_only}
              onChange={(e) => set("above_threshold_only", e.target.checked)}
            />
            Use responders only for curve values
          </label>
          <p className="muted small">
            Responders are strictly above the threshold. Percentages use finite
            signals as the denominator. Baseline thresholds use the original
            source population, including reference time before a cropped
            display.
          </p>
          <label className="field">
            Smoothing
            <select
              aria-label="Kinetics smoothing"
              value={settings.smoothing}
              onChange={(e) =>
                set("smoothing", e.target.value as Settings["smoothing"])
              }
            >
              <option value="none">None</option>
              <option value="moving_average">Centered moving average</option>
              <option value="gaussian">Centered Gaussian</option>
            </select>
          </label>
          {settings.smoothing !== "none" &&
            numeric("smoothing_width", "Smoothing window", 1, 255, false, 2)}
          {settings.smoothing === "gaussian" &&
            numeric("gaussian_sigma", "Gaussian sigma", Number.MIN_VALUE, 100)}
          <div className="section-heading">
            <h4>Time ranges</h4>
            <button
              type="button"
              className="button small ghost"
              disabled={
                !!settings.replace_result_id || settings.ranges.length >= 32
              }
              onClick={() => {
                const n = settings.ranges.length;
                const start =
                  settings.ranges.at(-1)?.end ?? settings.time_min ?? 0;
                set("ranges", [
                  ...settings.ranges,
                  range(
                    start,
                    start + 30,
                    `Range ${n + 1}`,
                    generationColors[n % generationColors.length],
                  ),
                ]);
              }}
            >
              <Plus size={14} />
              Add range
            </button>
          </div>
          {!settings.ranges.length && (
            <p className="muted small">
              Use the full collection, or add intervals around stimulation and
              response phases.
            </p>
          )}
          {settings.ranges.map((r, i) => (
            <RangeEditor
              key={r.id}
              value={r}
              label={`Kinetics range ${i + 1}`}
              change={(next) =>
                set(
                  "ranges",
                  settings.ranges.map((v) => (v.id === r.id ? next : v)),
                )
              }
              remove={
                settings.replace_result_id
                  ? undefined
                  : () =>
                      set(
                        "ranges",
                        settings.ranges.filter((v) => v.id !== r.id),
                      )
              }
            />
          ))}
          <label className="checkbox">
            <input
              type="checkbox"
              checked={settings.create_range_gates}
              onChange={(e) => set("create_range_gates", e.target.checked)}
            />
            Create time range populations when saved
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              aria-label="Create kinetics responder populations"
              checked={settings.create_responder_gates}
              onChange={(e) => set("create_responder_gates", e.target.checked)}
            />
            Create responder populations when saved
          </label>
          <button
            type="submit"
            className="button primary"
            disabled={
              busy ||
              submitting ||
              !inputs.length ||
              inputs.length > 128 ||
              !channels.some((c) => c.name === channel) ||
              (!!settings.time_channel &&
                !channels.some((c) => c.name === settings.time_channel))
            }
          >
            {submitting ? (
              <LoaderCircle size={16} className="spin" />
            ) : (
              <Play size={16} />
            )}
            Calculate kinetics
          </button>
        </form>
        <div className="cell-cycle-results">
          <div className="card">
            <div className="section-heading">
              <h3>Analyses and jobs</h3>
              <span className="muted small">
                {(jobs.data?.length ?? 0) + archived.length} analyses
              </span>
            </div>
            {jobs.error && <ErrorState error={jobs.error} />}{" "}
            {!jobs.data?.length && !archived.length && (
              <p className="muted">
                Calculate a source population to review its time course.
              </p>
            )}
            {jobs.data?.map((j) => (
              <div
                className={`cell-cycle-job ${selected === j.id ? "selected" : ""}`}
                key={j.id}
              >
                <button
                  className="button ghost"
                  aria-label={`Review kinetics ${j.request.name}`}
                  onClick={() => choose(j.id)}
                >
                  <strong>{j.request.name}</strong>
                  <span>
                    {j.request.inputs.length} samples ·{" "}
                    {j.request.statistic.replaceAll("_", " ")}
                  </span>
                  <small>{j.error ?? j.stage}</small>
                  {["running", "queued"].includes(j.status) && (
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
                {["running", "queued"].includes(j.status) && (
                  <button
                    className="icon-button"
                    aria-label={`Cancel kinetics ${j.request.name}`}
                    onClick={() =>
                      act(async () => {
                        await post(`${base}/jobs/${j.id}/cancel`, {});
                        await cache.invalidateQueries({
                          queryKey: [workspace.id, "kinetics-jobs"],
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
                  aria-label={`Review kinetics ${r.request.name}`}
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
              <LoaderCircle size={18} className="spin" />
              Loading time course…
            </div>
          )}
          {current && fit && (
            <div className="card cell-cycle-review kinetics-review">
              <div className="cell-cycle-review-heading">
                <div>
                  <h3>{current.request.name}</h3>
                  <p className="muted small">
                    {current.duration_seconds.toFixed(2)} seconds ·{" "}
                    {data?.represented_count.toLocaleString()} represented
                    events
                  </p>
                </div>
                <Tag color={current.stale ? "amber" : saved ? "green" : "blue"}>
                  {current.stale
                    ? "Scientific inputs changed"
                    : saved
                      ? "Saved analysis"
                      : "Review time course"}
                </Tag>
              </div>
              <label className="field">
                Analyzed sample
                <select
                  aria-label="Kinetics analyzed sample"
                  value={fit.sample_id}
                  onChange={(e) => setFitSample(e.target.value)}
                >
                  {current.fits.map((f) => (
                    <option value={f.sample_id} key={f.sample_id}>
                      {names(f.sample_id)}
                    </option>
                  ))}
                </select>
              </label>
              {current.fits.length > 1 && (
                <label className="checkbox">
                  <input
                    type="checkbox"
                    aria-label="Overlay kinetics samples"
                    checked={overlay}
                    onChange={(e) => setOverlay(e.target.checked)}
                  />
                  Overlay aligned samples
                </label>
              )}
              <TimeChart
                key={`${current.id}-${fit.sample_id}`}
                fit={fit}
                overlays={overlay ? current.fits : []}
                names={names}
              />
              <div className="cell-cycle-metrics proliferation-metrics">
                {[
                  ["Source events", data?.population_count],
                  ["Timed source events", data?.time_count],
                  ["Finite signals", data?.finite_count],
                  ["Responder threshold", fit.threshold],
                  ["Clock decreases", fit.reset_count],
                  ["Baseline events", fit.baseline_count],
                ].map(([label, value]) => (
                  <div key={String(label)}>
                    <span>{label}</span>
                    <strong>
                      {numberText(value as number | null | undefined)}
                    </strong>
                  </div>
                ))}
              </div>
              {fit.warnings.map((warning, i) => (
                <p key={i} className="kinetics-note" role="note">
                  {warning}
                </p>
              ))}
              <p className="muted small">
                Curve summaries weight time bins equally. Area integrates
                contiguous measured segments between bin centers; it excludes
                gaps and extrapolation. Adjacent time ranges share no events;
                the final collection endpoint is included.
              </p>
              <div className="table-scroll">
                <table
                  className="data-table kinetics-statistics"
                  aria-label="Kinetics time range statistics"
                >
                  <thead>
                    <tr>
                      {[
                        "Range",
                        "Time",
                        "Source events",
                        "Finite signals",
                        "Responders",
                        "Peak time",
                        "Peak",
                        "Mean",
                        "Slope",
                        "Area",
                        "Measured duration",
                        "",
                      ].map((v, i) => (
                        <th key={i}>{v}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {fit.ranges.map((r) => {
                      const population = workspace.gates.find(
                        (g) =>
                          g.sample_id === fit.sample_id &&
                          g.provenance?.kinetics_id === current.id &&
                          g.provenance?.kinetics_role === "range" &&
                          g.provenance?.range_id === r.id,
                      );
                      return (
                        <tr key={r.id}>
                          <td>
                            <span style={{ color: r.color }}>{r.name}</span>
                          </td>
                          <td>
                            {numberText(r.start)}–{numberText(r.end)}
                          </td>
                          {[
                            r.population_count,
                            r.finite_count,
                            r.responder_count,
                            r.peak_time,
                            r.peak,
                            r.mean,
                            r.slope,
                            r.auc,
                            r.covered_duration,
                          ].map((n, i) => (
                            <td key={i}>{numberText(n)}</td>
                          ))}
                          <td>
                            {population && (
                              <button
                                className="icon-button"
                                aria-label={`Explore kinetics ${r.name}`}
                                onClick={() =>
                                  onExplore(
                                    fit.sample_id,
                                    population.id,
                                    current.columns[0],
                                    current.columns[1],
                                  )
                                }
                              >
                                <ArrowUpRight size={15} />
                              </button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <div className="button-group population-exports">
                <button className="button small" onClick={() => load(current)}>
                  <RefreshCw size={14} />
                  Edit as new analysis
                </button>
                <button
                  className="button small"
                  onClick={() =>
                    act(async () => {
                      const ranges = await api<KineticsRange[]>(
                        `${base}/${current.id}/suggested-ranges?sample_id=${fit.sample_id}`,
                      );
                      load(current);
                      set("ranges", ranges);
                    })
                  }
                >
                  Suggest time ranges
                </button>
                {[
                  ["series", "Time series CSV"],
                  ["statistics", "Statistics CSV"],
                  ["report", "Analysis JSON"],
                ].map(([path, label]) => (
                  <button
                    key={path}
                    className="button small"
                    onClick={() =>
                      act(() =>
                        download(
                          `${base}/${current.id}/${path}`,
                          `kinetics-${path}.${path === "report" ? "json" : "csv"}`,
                        ),
                      )
                    }
                  >
                    <ArrowDownToLine size={14} />
                    {label}
                  </button>
                ))}
                <button
                  className="button small"
                  onClick={() =>
                    act(() =>
                      download(
                        `${base}/${current.id}/events?sample_id=${fit.sample_id}`,
                        "kinetics-events.csv",
                      ),
                    )
                  }
                >
                  Event data CSV
                </button>
                <button
                  className="button small"
                  onClick={() =>
                    act(() =>
                      download(
                        `${base}/${current.id}/figure?sample_id=${fit.sample_id}`,
                        "kinetics.svg",
                      ),
                    )
                  }
                >
                  Figure SVG
                </button>
              </div>
              <p className="muted small">
                Suggested ranges use peaks and intervening minima. Review their
                boundaries in setup and calculate again.
              </p>
              {!saved && (
                <>
                  <label className="checkbox">
                    <input
                      type="checkbox"
                      aria-label="Kinetics review confirmation"
                      checked={reviewed}
                      onChange={(e) => setReviewed(e.target.checked)}
                    />
                    I reviewed clocks, gaps, thresholds and time ranges for all
                    samples
                  </label>
                  {job?.apply_blocker && (
                    <p className="kinetics-note">{job.apply_blocker}</p>
                  )}
                  <button
                    className="button primary"
                    disabled={
                      busy || !reviewed || !job?.can_apply || current.stale
                    }
                    onClick={() =>
                      act(async () => {
                        await commit(
                          `/kinetics/jobs/${current.id}/apply`,
                          { revision: workspace.revision },
                          "Save kinetics analysis",
                        );
                        setReviewed(false);
                        await cache.invalidateQueries({
                          queryKey: [workspace.id, "kinetics-jobs"],
                        });
                      })
                    }
                  >
                    <Check size={16} />
                    Save kinetics analysis
                  </button>
                </>
              )}
              {saved && (
                <BiologyModelActions
                  key={`${current.id}-${workspace.revision}`}
                  workspace={workspace}
                  result={current}
                  commit={commit}
                  busy={busy}
                  onReplace={() => load(current, true)}
                  onRemoved={() => setSelected(null)}
                  onError={setError}
                />
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
