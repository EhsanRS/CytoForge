import { useMemo, useState } from "react";
import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  ArrowUpRight,
  Check,
  Download,
  LoaderCircle,
  Play,
  ShieldCheck,
  X,
} from "lucide-react";
import { api, ApiError, download, post } from "../api";
import type {
  Gate,
  QualityBin,
  QualityExclusion,
  QualityJob,
  QualityRequest,
  QualityResult,
  Sample,
  Workspace,
} from "../types";
import {
  acquisitionChannels,
  channelLabel,
  formatNumber,
  percentage,
} from "../types";
import type { Commit } from "./Editors";
import { Empty, ErrorState, Tag } from "./Common";

const eventChecks: { key: QualityExclusion; label: string }[] = [
  { key: "nonfinite", label: "Undefined marker values" },
  { key: "time", label: "Invalid or decreasing time" },
  { key: "saturation", label: "Raw detector saturation" },
  { key: "pulse", label: "Pulse-ratio outliers" },
];
const settings = {
  bin_events: 500,
  min_bin_events: 50,
  score_threshold: 6,
  signal_min_shift: 0.1,
  rate_min_fold: 2,
  saturation_fraction: 0.9999,
  pulse_score: 6,
};
function sourceDefaults(sample: Sample | undefined) {
  const acquired = sample ? acquisitionChannels(sample) : [];
  return {
    sampleId: sample?.id ?? "",
    gateId: "",
    channels: acquired
      .filter((c) => !/^time$/i.test(c.name))
      .slice(0, 12)
      .map((c) => c.name),
    time: acquired.find((c) => /^time$/i.test(c.name))?.name ?? "",
    area: acquired.find((c) => /^fsc.*[-_]a$/i.test(c.name))?.name ?? "",
    height: acquired.find((c) => /^fsc.*[-_]h$/i.test(c.name))?.name ?? "",
  };
}

export function QualityPanel({
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
  const [source, setSource] = useState(() => ({
    ...sourceDefaults(sample ?? workspace.samples[0]),
    gateId: gate?.id ?? "",
  }));
  const [name, setName] = useState("Acquisition QC 01");
  const [options, setOptions] = useState({ ...settings });
  const [compensated, setCompensated] = useState(true);
  const [transformed, setTransformed] = useState(true);
  const [saturation, setSaturation] = useState(true);
  const [pulse, setPulse] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const target = workspace.samples.find((s) => s.id === source.sampleId);
  const acquired = target ? acquisitionChannels(target) : [];
  const base = `/workspaces/${workspace.id}`;
  const jobs = useQuery({
    queryKey: [workspace.id, "quality-jobs", workspace.revision],
    queryFn: () => api<QualityJob[]>(`${base}/quality/jobs`),
    refetchInterval: 1000,
  });
  const selectedJob = jobs.data?.find((j) => j.id === selected);
  const saved = (workspace.quality_results ?? []).find(
    (r) => r.id === selected,
  );
  const result = useQuery({
    queryKey: [workspace.id, "quality-result", selected, workspace.revision],
    queryFn: () => api<QualityResult>(`${base}/quality/${selected}`),
    enabled:
      !!selected &&
      (!!saved ||
        selectedJob?.status === "succeeded" ||
        selectedJob?.status === "applied"),
    placeholderData: keepPreviousData,
  });
  const archived = (workspace.quality_results ?? []).filter(
    (q) => !jobs.data?.some((j) => j.id === q.id),
  );

  async function run(event: React.SubmitEvent) {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      const request: QualityRequest = {
        ...options,
        revision: workspace.revision,
        name,
        algorithm: "acquisition_qc",
        sample_id: source.sampleId,
        gate_id: source.gateId || null,
        channels: source.channels,
        time_channel: source.time || null,
        compensated,
        use_transforms: transformed,
        saturation_channels: saturation
          ? acquired.filter((c) => c.name !== source.time).map((c) => c.name)
          : [],
        pulse_area: pulse ? source.area || null : null,
        pulse_height: pulse ? source.height || null : null,
      };
      if (pulse && (!request.pulse_area || !request.pulse_height))
        throw new Error("Select both area and height pulse channels.");
      const job = await post<QualityJob>(`${base}/quality/jobs`, request);
      setSelected(job.id);
      await cache.invalidateQueries({
        queryKey: [workspace.id, "quality-jobs"],
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 409)
        await cache.invalidateQueries({
          queryKey: ["workspace", workspace.id],
        });
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }
  async function cancel(id: string) {
    try {
      await post(`${base}/quality/jobs/${id}/cancel`, {});
      await jobs.refetch();
    } catch (err) {
      setError((err as Error).message);
    }
  }
  if (!workspace.samples.length)
    return (
      <Empty
        icon={<ShieldCheck size={32} />}
        title="Inspect acquisition quality"
        text="Import a sample to measure time, event rate and signal stability."
      />
    );
  return (
    <div className="discovery-page quality-page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">ACQUISITION QUALITY</span>
          <h2>Understand every exclusion.</h2>
          <p>
            Inspect the complete acquisition, review candidate intervals, and
            retain an auditable cleanup population.
          </p>
        </div>
        <Tag color="#38d9ba">Full event identity</Tag>
      </div>
      <div className="discovery-grid quality-grid">
        <form className="card analysis-config" onSubmit={run}>
          <div className="panel-heading">
            <ShieldCheck size={18} />
            <h3>Acquisition diagnostics</h3>
          </div>
          <label className="field">
            Run name
            <input
              value={name}
              required
              maxLength={160}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label className="field">
            QC sample
            <select
              value={source.sampleId}
              onChange={(e) => {
                setSource(
                  sourceDefaults(
                    workspace.samples.find((s) => s.id === e.target.value),
                  ),
                );
                setPulse(false);
              }}
            >
              {workspace.samples.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Source population
            <select
              value={source.gateId}
              onChange={(e) => setSource({ ...source, gateId: e.target.value })}
            >
              <option value="">All events</option>
              {workspace.gates
                .filter((g) => g.sample_id === source.sampleId)
                .map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name}
                  </option>
                ))}
            </select>
          </label>
          <label className="field">
            Acquisition time
            <select
              value={source.time}
              onChange={(e) =>
                setSource({
                  ...source,
                  time: e.target.value,
                  channels: source.channels.filter((n) => n !== e.target.value),
                })
              }
            >
              <option value="">Event order only</option>
              {acquired.map((c) => (
                <option key={c.name} value={c.name}>
                  {channelLabel(c)}
                </option>
              ))}
            </select>
          </label>
          <fieldset className="quality-marker-picker">
            <legend>
              Stability markers <span>{source.channels.length} / 32</span>
            </legend>
            {target?.channels
              .filter((c) => c.name !== source.time)
              .map((c) => (
                <label className="checkbox-field" key={c.name}>
                  <input
                    type="checkbox"
                    checked={source.channels.includes(c.name)}
                    disabled={
                      !source.channels.includes(c.name) &&
                      source.channels.length >= 32
                    }
                    onChange={(e) =>
                      setSource({
                        ...source,
                        channels: e.target.checked
                          ? [...source.channels, c.name]
                          : source.channels.filter((n) => n !== c.name),
                      })
                    }
                  />
                  {channelLabel(c)}
                </label>
              ))}
          </fieldset>
          <label className="checkbox-field">
            <input
              type="checkbox"
              checked={compensated}
              onChange={(e) => setCompensated(e.target.checked)}
            />
            Use sample compensation for signals
          </label>
          <label className="checkbox-field">
            <input
              type="checkbox"
              checked={transformed}
              onChange={(e) => setTransformed(e.target.checked)}
            />
            Use channel transforms for signals
          </label>
          <label className="checkbox-field">
            <input
              type="checkbox"
              checked={saturation}
              onChange={(e) => setSaturation(e.target.checked)}
            />
            Check raw detector saturation
          </label>
          <label className="checkbox-field">
            <input
              type="checkbox"
              checked={pulse}
              onChange={(e) => setPulse(e.target.checked)}
            />
            Inspect area / height pulse ratio
          </label>
          {pulse && (
            <div className="field-row">
              <label className="field grow">
                Pulse area
                <select
                  required
                  value={source.area}
                  onChange={(e) =>
                    setSource({ ...source, area: e.target.value })
                  }
                >
                  <option value="">Select area</option>
                  {acquired.map((c) => (
                    <option key={c.name}>{c.name}</option>
                  ))}
                </select>
              </label>
              <label className="field grow">
                Pulse height
                <select
                  required
                  value={source.height}
                  onChange={(e) =>
                    setSource({ ...source, height: e.target.value })
                  }
                >
                  <option value="">Select height</option>
                  {acquired.map((c) => (
                    <option key={c.name}>{c.name}</option>
                  ))}
                </select>
              </label>
            </div>
          )}
          <details className="quality-settings">
            <summary>Detection settings</summary>
            <div className="field-grid">
              {(
                [
                  ["bin_events", "Acquired events per bin", 50, 100000, 50],
                  [
                    "min_bin_events",
                    "Minimum source events per bin",
                    10,
                    100000,
                    10,
                  ],
                  ["score_threshold", "Robust score threshold", 2, 20, 0.5],
                  [
                    "signal_min_shift",
                    "Minimum signal shift / spread",
                    0.001,
                    2,
                    0.01,
                  ],
                  [
                    "rate_min_fold",
                    "Minimum acquisition rate fold",
                    1.01,
                    20,
                    0.1,
                  ],
                  [
                    "saturation_fraction",
                    "Saturation / detector range",
                    0.9,
                    1,
                    0.0001,
                  ],
                  ["pulse_score", "Pulse-ratio score threshold", 2, 20, 0.5],
                ] as const
              ).map(([key, label, min, max, step]) => (
                <label className="field" key={key}>
                  {label}
                  <input
                    type="number"
                    required
                    min={min}
                    max={max}
                    step={Number.isInteger(step) ? 1 : "any"}
                    value={Number.isFinite(options[key]) ? options[key] : ""}
                    onChange={(e) =>
                      setOptions({
                        ...options,
                        [key]:
                          e.target.value === "" ? NaN : Number(e.target.value),
                      })
                    }
                  />
                </label>
              ))}
            </div>
            <p className="form-note">
              Robust-bin scores use a majority-stable acquisition baseline.
              Signal quantiles can also change with biological composition.
              Review the traces before applying exclusions.
            </p>
          </details>
          {error && (
            <p role="alert" className="form-error">
              {error}
            </p>
          )}
          <button
            className="button primary wide"
            disabled={
              busy ||
              submitting ||
              !target ||
              !source.channels.length ||
              options.min_bin_events > options.bin_events
            }
            type="submit"
          >
            {submitting ? (
              <LoaderCircle className="spin" size={16} />
            ) : (
              <Play size={16} />
            )}
            Run acquisition QC
          </button>
          <p className="form-note">
            Every acquired event is measured. Time, rate and saturation always
            use acquired values. Pulse outliers require singlet review.
          </p>
        </form>
        <div className="quality-results">
          <div className="card quality-runs">
            <div className="panel-heading">
              <h3>Diagnostic runs</h3>
              <span>{(jobs.data?.length ?? 0) + archived.length}</span>
            </div>
            {jobs.isError && <ErrorState error={jobs.error} />}
            {!jobs.data?.length && !archived.length && (
              <p className="form-note">
                Start a run to inspect rate and signal traces. Cleanup choices
                are saved after review.
              </p>
            )}
            <div className="quality-run-list">
              {jobs.data?.map((job) => (
                <div
                  className={`quality-run ${selected === job.id ? "active" : ""}`}
                  key={job.id}
                >
                  <button
                    className="quality-run-select"
                    onClick={() => setSelected(job.id)}
                  >
                    <span>
                      <strong>{job.request.name}</strong>
                      <small>
                        {workspace.samples.find(
                          (s) => s.id === job.request.sample_id,
                        )?.name ?? "Removed sample"}
                      </small>
                    </span>
                    <Tag
                      color={
                        job.stale
                          ? "#edb96c"
                          : job.status === "applied"
                            ? "#38d9ba"
                            : "#91a5be"
                      }
                    >
                      {job.stale ? "Inputs changed" : job.status}
                    </Tag>
                  </button>
                  {["queued", "running"].includes(job.status) && (
                    <button
                      className="icon-button"
                      title="Cancel QC run"
                      aria-label={`Cancel ${job.request.name}`}
                      onClick={() => void cancel(job.id)}
                    >
                      <X size={15} />
                    </button>
                  )}
                </div>
              ))}
              {archived.map((q) => (
                <button
                  className={`quality-run quality-run-select ${selected === q.id ? "active" : ""}`}
                  key={q.id}
                  onClick={() => setSelected(q.id)}
                >
                  <span>
                    <strong>{q.request.name}</strong>
                    <small>
                      {
                        workspace.samples.find(
                          (s) => s.id === q.request.sample_id,
                        )?.name
                      }
                    </small>
                  </span>
                  <Tag color="#38d9ba">Saved</Tag>
                </button>
              ))}
            </div>
          </div>
          {selectedJob &&
            ["queued", "running"].includes(selectedJob.status) && (
              <div className="card quality-status">
                <LoaderCircle size={20} className="spin" />
                <div>
                  <strong>{selectedJob.stage}</strong>
                  <progress
                    max={1}
                    value={selectedJob.progress}
                    aria-label="Acquisition QC progress"
                  />
                </div>
              </div>
            )}
          {selectedJob?.error && (
            <ErrorState error={new Error(selectedJob.error)} />
          )}
          {result.isError && <ErrorState error={result.error} />}
          {result.data && result.data.id === selected && (
            <QualityReview
              key={result.data.id}
              result={result.data}
              workspace={workspace}
              job={selectedJob}
              busy={busy || result.isFetching}
              commit={commit}
              onExplore={onExplore}
            />
          )}
          {!selected && (
            <div className="card quality-empty">
              <ShieldCheck size={34} />
              <h3>See acquisition quality across time</h3>
              <p>
                Trace marker medians and distributions, review rate anomalies,
                and compare retained events with a complementary rejected
                population.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function QualityReview({
  result,
  workspace,
  job,
  busy,
  commit,
  onExplore,
}: {
  result: QualityResult;
  workspace: Workspace;
  job?: QualityJob;
  busy: boolean;
  commit: Commit;
  onExplore: (sampleId: string, gateId: string) => void;
}) {
  const cache = useQueryClient();
  const existing = workspace.gates.filter((g) => g.quality_id === result.id);
  const clean = existing.find((g) => g.quality_keep !== false);
  const inWorkspace = workspace.quality_results?.some(
    (q) => q.id === result.id,
  );
  const [excluded, setExcluded] = useState<number[]>(
    () =>
      clean?.quality_excluded_bins ??
      result.bins.filter((b) => b.suggested).map((b) => b.index),
  );
  const [exclusions, setExclusions] = useState<QualityExclusion[]>(
    () => clean?.quality_exclusions ?? ["nonfinite", "time"],
  );
  const [name, setName] = useState(
    clean?.name ?? `${result.request.name.slice(0, 145)} · Clean`,
  );
  const [showAll, setShowAll] = useState(false);
  const [metric, setMetric] = useState(
    result.request.time_channel ? "rate" : result.request.channels[0],
  );
  const [error, setError] = useState("");
  const base = `/workspaces/${workspace.id}`;
  const review = {
    revision: workspace.revision,
    name,
    excluded_bins: [...excluded].sort((a, b) => a - b),
    exclusions: [...exclusions].sort(),
    create_rejected: true,
  };
  const counts = useQuery({
    queryKey: [workspace.id, "quality-counts", result.id, review],
    queryFn: () =>
      post<{
        population_count: number;
        retained_count: number;
        rejected_count: number;
        stale: boolean;
      }>(`${base}/quality/${result.id}/review`, review),
    retry: false,
  });
  const visible = result.bins.filter(
    (b) => showAll || b.suggested || excluded.includes(b.index),
  );
  const toggle = (index: number) =>
    setExcluded((old) =>
      old.includes(index) ? old.filter((i) => i !== index) : [...old, index],
    );
  const save = async () => {
    setError("");
    try {
      await commit(
        inWorkspace
          ? `/quality/${result.id}/apply`
          : `/quality/jobs/${result.id}/apply`,
        review,
        inWorkspace
          ? "QC review updated"
          : "Reviewed cleanup populations added",
      );
      await cache.invalidateQueries({
        queryKey: [workspace.id, "quality-jobs"],
      });
    } catch (err) {
      setError((err as Error).message);
    }
  };
  const summary = counts.data;
  return (
    <div className="quality-review card">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">INTERVAL REVIEW</span>
          <h3>{result.request.name}</h3>
        </div>
        <button
          className="button small"
          onClick={() =>
            download(
              `${base}/quality/${result.id}/report`,
              "acquisition-qc.json",
              { method: "POST", body: JSON.stringify(review) },
            ).catch((err) => setError(err.message))
          }
        >
          <Download size={14} />
          QC report
        </button>
      </div>
      {result.stale && (
        <p className="form-warning" role="status">
          The scientific inputs changed after this run. Its original event
          identities remain inspectable; rerun QC before adding or revising
          cleanup gates.
        </p>
      )}
      <p className="quality-run-basis">
        {workspace.samples.find((s) => s.id === result.request.sample_id)
          ?.name ?? "Original sample"}
        {" · "}
        {result.request.gate_id
          ? (workspace.gates.find((g) => g.id === result.request.gate_id)
              ?.name ?? "Original source population")
          : "All events"}
        {" · "}
        {result.request.compensated
          ? "Sample compensation"
          : "Uncompensated signals"}
        {" · "}
        {result.request.use_transforms
          ? "Channel transforms"
          : "Untransformed signals"}
      </p>
      {result.diagnostics.time && (
        <div className="quality-time-summary">
          <span>
            {formatNumber(result.diagnostics.time.resets, 0)} time resets
          </span>
          <span>
            {formatNumber(result.diagnostics.time.repeated_timestamps, 0)}{" "}
            repeated timestamps
          </span>
          <span>
            {formatNumber(result.diagnostics.time.gap_count ?? 0, 0)} large time
            gaps
          </span>
          <span>
            {formatNumber(
              result.diagnostics.time.resolution_limited_bins ?? 0,
              0,
            )}{" "}
            intervals with coarse time
          </span>
        </div>
      )}
      <div className="quality-summary">
        <div>
          <small>Source events</small>
          <strong>{formatNumber(result.data.population_count, 0)}</strong>
        </div>
        <div>
          <small>Retained after review</small>
          <strong data-testid="qc-retained-count">
            {counts.isFetching ? "…" : formatNumber(summary?.retained_count, 0)}
          </strong>
        </div>
        <div>
          <small>Rejected after review</small>
          <strong data-testid="qc-rejected-count">
            {counts.isFetching ? "…" : formatNumber(summary?.rejected_count, 0)}
          </strong>
        </div>
      </div>
      <div className="quality-trace-controls">
        <label className="field">
          Trace
          <select
            aria-label="QC trace"
            value={metric}
            onChange={(e) => setMetric(e.target.value)}
          >
            {result.request.time_channel && (
              <option value="rate">Acquisition rate</option>
            )}
            {result.request.channels.map((n) => (
              <option key={n}>{n}</option>
            ))}
          </select>
        </label>
        <div className="quality-trace-legend">
          <span>
            <i className="median" />
            Median / rate
          </span>
          <span>
            <i className="suggested" />
            Suggested interval
          </span>
          <span>
            <i className="excluded" />
            Excluded interval
          </span>
        </div>
      </div>
      <QualityTrace
        bins={result.bins}
        metric={metric}
        total={result.data.event_count}
        excluded={excluded}
        toggle={toggle}
      />
      <p className="form-note">
        Click an interval in the trace or use its checkbox to change the
        exclusion. Signal bands show the 10th–90th percentiles. The horizontal
        axis preserves acquisition event order.
      </p>
      <div className="quality-review-tools">
        <button
          className="button small"
          onClick={() =>
            setExcluded(
              result.bins.filter((b) => b.suggested).map((b) => b.index),
            )
          }
        >
          Select suggested intervals
        </button>
        <button className="button small" onClick={() => setExcluded([])}>
          Keep all intervals
        </button>
        <label className="checkbox-field">
          <input
            type="checkbox"
            checked={showAll}
            onChange={(e) => setShowAll(e.target.checked)}
          />
          Show all intervals
        </label>
      </div>
      <div
        className="quality-bin-table"
        role="table"
        aria-label="Acquisition interval review"
      >
        <div className="quality-bin-row heading" role="row">
          <span>Exclude</span>
          <span>Interval · event IDs</span>
          <span>Source events</span>
          <span>Findings</span>
        </div>
        {visible.map((b) => (
          <div
            className={`quality-bin-row ${excluded.includes(b.index) ? "excluded" : ""}`}
            key={b.index}
            role="row"
          >
            <label>
              <input
                type="checkbox"
                aria-label={`Exclude interval ${b.index + 1}`}
                checked={excluded.includes(b.index)}
                onChange={() => toggle(b.index)}
              />
            </label>
            <span>
              <strong>
                {b.index + 1} · {formatNumber(b.start, 0)}–
                {formatNumber(b.end - 1, 0)}
              </strong>
              <small>
                {b.time_start != null && b.time_end != null
                  ? `${formatNumber(b.time_start, 3)}–${formatNumber(b.time_end, 3)} ${result.diagnostics.time?.unit ?? "time units"}`
                  : "Time unavailable"}
              </small>
            </span>
            <span>
              {formatNumber(b.population_count, 0)}
              <small>
                {b.rate != null
                  ? `${formatNumber(b.rate, 1)} events / time unit`
                  : "Rate unavailable"}
              </small>
            </span>
            <span>{b.reasons.join("; ") || "Manual review"}</span>
          </div>
        ))}
        {!visible.length && (
          <p className="form-note">
            No candidate intervals at these settings. You can inspect and select
            any interval in the trace.
          </p>
        )}
      </div>
      <fieldset className="quality-event-checks">
        <legend>Individual event exclusions</legend>
        {eventChecks.map((check) => (
          <label className="checkbox-field" key={check.key}>
            <input
              type="checkbox"
              checked={exclusions.includes(check.key)}
              onChange={(e) =>
                setExclusions(
                  e.target.checked
                    ? [...exclusions, check.key]
                    : exclusions.filter((n) => n !== check.key),
                )
              }
            />
            {check.label}
            <span>
              {formatNumber(result.data.flag_counts[check.key] ?? 0, 0)}
            </span>
          </label>
        ))}
        <p className="form-note">
          Counts can overlap with each other and with excluded intervals. The
          review totals count each event once.
        </p>
      </fieldset>
      {result.diagnostics.saturation.length > 0 && (
        <details className="quality-diagnostics">
          <summary>Detector saturation thresholds</summary>
          <table>
            <thead>
              <tr>
                <th>Acquired channel</th>
                <th>Threshold</th>
                <th>Source events</th>
              </tr>
            </thead>
            <tbody>
              {result.diagnostics.saturation.map((s) => (
                <tr key={s.channel}>
                  <td>{s.channel}</td>
                  <td>{formatNumber(s.threshold, 2)}</td>
                  <td>{formatNumber(s.count, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
      {result.diagnostics.pulse && (
        <div className="quality-pulse">
          <h4>Pulse-shape review</h4>
          <PulseTrace pulse={result.diagnostics.pulse} />
          <p className="form-note">
            {formatNumber(result.diagnostics.pulse.outlier_count, 0)}{" "}
            pulse-ratio candidates, including{" "}
            {formatNumber(result.diagnostics.pulse.invalid_count, 0)} invalid
            pulses. Scatter shows at most 2,000 evenly spaced source events.
            Pulse shape alone does not establish a doublet identity.
          </p>
        </div>
      )}
      {!!result.warnings.length && (
        <details className="quality-diagnostics" open>
          <summary>Diagnostic notes · {result.warnings.length}</summary>
          <ul>
            {result.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </details>
      )}
      {counts.isError && <ErrorState error={counts.error} />}
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      <div className="quality-apply">
        <label className="field grow">
          Cleanup population name
          <input
            value={name}
            required
            maxLength={160}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <button
          className="button primary"
          onClick={() => void save()}
          disabled={
            busy ||
            result.stale ||
            !name.trim() ||
            counts.isFetching ||
            !summary ||
            (!inWorkspace && job?.status !== "succeeded")
          }
        >
          <Check size={16} />
          {inWorkspace
            ? "Update reviewed populations"
            : "Create reviewed populations"}
        </button>
      </div>
      <div className="quality-review-footer">
        <span>
          {summary &&
            percentage(
              result.data.population_count
                ? (100 * summary.retained_count) / result.data.population_count
                : null,
            )}{" "}
          retained · {result.bins.length} bins ·{" "}
          {formatNumber(result.duration_seconds, 2)} s
        </span>
        <button
          className="button small"
          onClick={() =>
            download(
              `${base}/quality/${result.id}/events`,
              "acquisition-qc-events.csv",
              { method: "POST", body: JSON.stringify(review) },
            ).catch((err) => setError(err.message))
          }
        >
          <Download size={14} />
          Event identities CSV
        </button>
        {clean && (
          <button
            className="button small"
            onClick={() => onExplore(result.request.sample_id, clean.id)}
          >
            Open saved clean population
            <ArrowUpRight size={14} />
          </button>
        )}
      </div>
      <p className="form-note">
        Saved populations retain original event identities and their review
        choices. Updating this review also updates its rejected population and
        preserves descendant gates.
      </p>
    </div>
  );
}

function QualityTrace({
  bins,
  metric,
  total,
  excluded,
  toggle,
}: {
  bins: QualityBin[];
  metric: string;
  total: number;
  excluded: number[];
  toggle: (index: number) => void;
}) {
  const [hovered, setHovered] = useState<number | null>(null);
  const measurements = bins.map((b) =>
    metric === "rate"
      ? [b.rate, b.rate, b.rate]
      : [
          b.signals[metric]?.p10 ?? null,
          b.signals[metric]?.median ?? null,
          b.signals[metric]?.p90 ?? null,
        ],
  );
  const all = measurements
    .flat()
    .filter((v): v is number => v != null && Number.isFinite(v));
  const lo = all.length ? Math.min(...all) : 0;
  const hi = all.length ? Math.max(...all) : 1;
  const reference = Math.max(Math.abs(lo), Math.abs(hi), 1);
  const normalizedLow = lo / reference;
  const normalizedHigh = hi / reference;
  const span =
    normalizedHigh > normalizedLow
      ? normalizedHigh - normalizedLow
      : Math.max(Math.abs(normalizedLow) * 0.1, 1 / reference);
  const x = (event: number) => 64 + (706 * event) / Math.max(total, 1);
  const y = (value: number) =>
    190 - (160 * (value / reference - normalizedLow)) / span;
  function trace(column: number) {
    let path = "",
      broken = true;
    measurements.forEach((values, i) => {
      const value = values[column];
      if (value == null) {
        broken = true;
        return;
      }
      path += `${broken ? "M" : "L"}${x((bins[i].start + bins[i].end) / 2).toFixed(2)},${y(value).toFixed(2)} `;
      broken = false;
    });
    return path;
  }
  const current = hovered == null ? null : bins[hovered];
  return (
    <div className="quality-trace">
      <svg
        viewBox="0 0 800 235"
        role="group"
        aria-label={`${metric} acquisition trace`}
      >
        {[0, 0.5, 1].map((fraction) => (
          <g key={fraction}>
            <line
              x1="64"
              x2="770"
              y1={190 - 160 * fraction}
              y2={190 - 160 * fraction}
              className="qc-grid"
            />
            <text x="57" y={194 - 160 * fraction} textAnchor="end">
              {formatNumber((normalizedLow + span * fraction) * reference, 2)}
            </text>
            <text x={x(total * fraction)} y="214" textAnchor="middle">
              {formatNumber(total * fraction, 0)}
            </text>
          </g>
        ))}
        {bins.map((b) => (
          <rect
            key={b.index}
            x={x(b.start)}
            y="22"
            width={Math.max(x(b.end) - x(b.start), 0.5)}
            height="177"
            className={
              excluded.includes(b.index)
                ? "qc-excluded"
                : b.suggested
                  ? "qc-suggested"
                  : "qc-interval"
            }
            tabIndex={0}
            role="button"
            aria-label={`Interval ${b.index + 1}, ${b.population_count} source events${b.suggested ? ", suggested" : ""}`}
            aria-pressed={excluded.includes(b.index)}
            onClick={() => toggle(b.index)}
            onMouseEnter={() => setHovered(b.index)}
            onFocus={() => setHovered(b.index)}
            onKeyDown={(e) => {
              if (["Enter", " "].includes(e.key)) {
                e.preventDefault();
                toggle(b.index);
              }
            }}
          >
            <title>{b.reasons.join("; ") || "No suggested anomaly"}</title>
          </rect>
        ))}
        {metric !== "rate" && (
          <>
            <path d={trace(0)} className="qc-band" />
            <path d={trace(2)} className="qc-band" />
          </>
        )}
        <path d={trace(1)} className="qc-line" />
        <text x="416" y="231" textAnchor="middle">
          Original acquisition event index
        </text>
      </svg>
      <div className="quality-trace-readout">
        {current
          ? `Interval ${current.index + 1} · ${current.population_count.toLocaleString()} source events · ${current.reasons.join("; ") || "No suggested anomaly"}`
          : all.length
            ? "Select intervals to review their events."
            : "No measurable values for this trace."}
      </div>
    </div>
  );
}

function PulseTrace({
  pulse,
}: {
  pulse: NonNullable<QualityResult["diagnostics"]["pulse"]>;
}) {
  const points = useMemo(
    () =>
      pulse.preview.event_ids.flatMap((id, i) =>
        pulse.preview.height[i] > 0 && pulse.preview.area[i] > 0
          ? [
              {
                id,
                x: Math.log10(pulse.preview.height[i]),
                y: Math.log10(pulse.preview.area[i]),
                outlier: pulse.preview.outlier[i],
              },
            ]
          : [],
      ),
    [pulse],
  );
  if (!points.length)
    return <p className="form-note">No positive area/height pairs to plot.</p>;
  const xmin = Math.min(...points.map((p) => p.x)),
    xmax = Math.max(...points.map((p) => p.x));
  const ymin = Math.min(...points.map((p) => p.y)),
    ymax = Math.max(...points.map((p) => p.y));
  const xspan = Math.max(xmax - xmin, 0.05),
    yspan = Math.max(ymax - ymin, 0.05);
  const x = (v: number) => 62 + (680 * (v - xmin)) / xspan;
  const y = (v: number) => 195 - (160 * (v - ymin)) / yspan;
  return (
    <svg
      className="quality-pulse-chart"
      viewBox="0 0 800 235"
      role="img"
      aria-label="Pulse area versus height preview"
    >
      <defs>
        <clipPath id={`pulse-${pulse.area.replace(/\W/g, "")}`}>
          <rect x="62" y="24" width="680" height="180" />
        </clipPath>
      </defs>
      <g clipPath={`url(#pulse-${pulse.area.replace(/\W/g, "")})`}>
        {pulse.log_ratio_center != null && (
          <line
            x1={x(xmin)}
            x2={x(xmin + xspan)}
            y1={y(xmin + pulse.log_ratio_center / Math.LN10)}
            y2={y(xmin + xspan + pulse.log_ratio_center / Math.LN10)}
            className="qc-band"
          />
        )}
        {points.map((p) => (
          <circle
            key={p.id}
            cx={x(p.x)}
            cy={y(p.y)}
            r={p.outlier ? 3 : 1.8}
            className={p.outlier ? "pulse-outlier" : "pulse-event"}
          >
            <title>
              Event {p.id}
              {p.outlier ? " · Pulse-ratio candidate" : ""}
            </title>
          </circle>
        ))}
      </g>
      <text x="400" y="227" textAnchor="middle">
        log₁₀ {pulse.height}
      </text>
      <text x="18" y="115" textAnchor="middle" transform="rotate(-90 18 115)">
        log₁₀ {pulse.area}
      </text>
    </svg>
  );
}
