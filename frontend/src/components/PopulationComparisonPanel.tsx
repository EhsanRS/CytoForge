import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpRight,
  LoaderCircle,
  Play,
  Plus,
  RefreshCw,
  X,
} from "lucide-react";
import { api, post } from "../api";
import {
  channelLabel,
  id,
  linear,
  type ComparisonParameter,
  type DesktopComparisonState,
  type Gate,
  type PopulationComparisonJob,
  type PopulationComparisonRequest,
  type PopulationComparisonResult,
  type Sample,
  type Workspace,
} from "../types";
import type { Commit } from "./Editors";
import { ErrorState } from "./Common";
import { AxisDefinitionDialog } from "./AxisDefinitionDialog";
import { PopulationComparisonActions } from "./PopulationComparisonActions";
import {
  defaultComparisonView,
  PopulationComparisonPlot,
} from "./PopulationComparisonPlot";

type Source = PopulationComparisonRequest["inputs"][number];
const sourceKey = (source: Source) =>
  `${source.sample_id}/${source.gate_id ?? "all"}`;
const parameter = (sample: Sample, channel: string): ComparisonParameter => ({
  id: id(),
  label: channelLabel(sample.channels.find((c) => c.name === channel)!),
  channel,
  transform:
    sample.channels.find((c) => c.name === channel)?.transform ?? linear,
  compensation_ref: "sample",
  ratio_channels: null,
  ratio_a: 1,
  ratio_b: 0,
  ratio_c: 0,
  ratio_bound_min: null,
  ratio_bound_max: null,
});

function Cohort({
  label,
  workspace,
  sources,
  onChange,
}: {
  label: string;
  workspace: Workspace;
  sources: Source[];
  onChange: (sources: Source[]) => void;
}) {
  const [sid, setSid] = useState(workspace.samples[0]?.id ?? ""),
    [gid, setGid] = useState(""),
    [group, setGroup] = useState("");
  const add = (values: Source[]) =>
    onChange(
      [
        ...sources,
        ...values.filter(
          (v) => !sources.some((s) => sourceKey(s) === sourceKey(v)),
        ),
      ].slice(0, 128),
    );
  return (
    <fieldset className="comparison-cohort">
      <legend>{label}</legend>
      <div className="comparison-toolbar">
        <label className="field">
          Acquisition
          <select
            aria-label={`${label} acquisition`}
            value={sid}
            onChange={(e) => {
              setSid(e.target.value);
              setGid("");
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
          Population
          <select
            aria-label={`${label} population`}
            value={gid}
            onChange={(e) => setGid(e.target.value)}
          >
            <option value="">All events</option>
            {workspace.gates
              .filter((g) => g.sample_id === sid)
              .map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
          </select>
        </label>
        <button
          type="button"
          aria-label={`Add ${label.toLowerCase()} population`}
          disabled={!sid || sources.length >= 128}
          onClick={() => add([{ sample_id: sid, gate_id: gid || null }])}
        >
          <Plus size={14} /> Add population
        </button>
        <label className="field">
          Group
          <select
            aria-label={`${label} group`}
            value={group}
            onChange={(e) => setGroup(e.target.value)}
          >
            <option value="">All acquisitions</option>
            {workspace.groups.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          onClick={() =>
            add(
              workspace.samples
                .filter(
                  (s) =>
                    !group ||
                    workspace.groups
                      .find((g) => g.id === group)
                      ?.sample_ids.includes(s.id),
                )
                .map((s) => ({ sample_id: s.id, gate_id: null })),
            )
          }
        >
          Add group roots
        </button>
      </div>
      <ul className="comparison-source-list">
        {sources.map((s) => (
          <li key={sourceKey(s)}>
            <span>
              {workspace.samples.find((v) => v.id === s.sample_id)?.name ??
                "Missing acquisition"}{" "}
              ·{" "}
              {workspace.gates.find((g) => g.id === s.gate_id)?.name ??
                "All events"}
            </span>
            <button
              type="button"
              aria-label={`Remove ${label.toLowerCase()} ${sourceKey(s)}`}
              onClick={() =>
                onChange(sources.filter((v) => sourceKey(v) !== sourceKey(s)))
              }
            >
              <X size={14} />
            </button>
          </li>
        ))}
      </ul>
      {!sources.length && <p className="hint">Add at least one population.</p>}
    </fieldset>
  );
}

export function PopulationComparisonPanel({
  workspace,
  sample,
  gate,
  commit,
  busy,
}: {
  workspace: Workspace;
  sample?: Sample | null;
  gate?: Gate | null;
  commit: Commit;
  busy: boolean;
}) {
  const reference = sample ?? workspace.samples[0];
  const [request, setRequest] = useState<PopulationComparisonRequest>(() => ({
    revision: workspace.revision,
    name: "Population comparison",
    algorithm: "population_comparison",
    inputs: reference
      ? [{ sample_id: reference.id, gate_id: gate?.id ?? null }]
      : [],
    controls: workspace.samples
      .filter((s) => s.id !== reference?.id)
      .slice(0, 1)
      .map((s) => ({ sample_id: s.id, gate_id: null })),
    parameters: reference
      ? reference.channels.slice(0, 2).map((c) => parameter(reference, c.name))
      : [],
    probability_bins: 64,
    histogram_bins: 256,
    minimum_bin_events: 10,
    positive_direction: "higher",
    joint: true,
    control_baselines: true,
    replace_result_id: null,
  }));
  const [selected, setSelected] = useState<string | null>(null),
    [working, setWorking] = useState(false),
    [error, setError] = useState("");
  const [editing, setEditing] = useState<ComparisonParameter | null>(null);
  const [view, setView] = useState<DesktopComparisonState>(
    defaultComparisonView(workspace.id, ""),
  );
  const cache = useQueryClient(),
    base = `/workspaces/${workspace.id}/population-comparison`;
  const jobs = useQuery({
    queryKey: [workspace.id, "comparison-jobs", workspace.revision],
    queryFn: () => api<PopulationComparisonJob[]>(`${base}/jobs`),
    refetchInterval: (q) =>
      q.state.data?.some((j) => j.status === "queued" || j.status === "running")
        ? 750
        : false,
  });
  const saved = workspace.comparison_results?.find((r) => r.id === selected),
    job = jobs.data?.find((j) => j.id === selected);
  const result = useQuery({
    queryKey: [
      workspace.id,
      "comparison-result",
      selected,
      workspace.revision,
      job?.status,
    ],
    queryFn: () => api<PopulationComparisonResult>(`${base}/${selected}`),
    enabled:
      !!selected &&
      (!!saved || job?.status === "succeeded" || job?.status === "applied"),
  });
  const current = result.data?.id === selected ? result.data : undefined;
  const selectedSamples = workspace.samples.filter((s) =>
    [...request.inputs, ...request.controls].some((v) => v.sample_id === s.id),
  );
  const coordinateSample =
    workspace.samples.find((s) => s.id === request.inputs[0]?.sample_id) ??
    reference;
  const common =
    coordinateSample?.channels.filter((c) =>
      selectedSamples.every((s) => s.channels.some((v) => v.name === c.name)),
    ) ?? [];
  const select = (identifier: string) => {
    setSelected(identifier);
    setView(defaultComparisonView(workspace.id, identifier));
    setError("");
  };
  const patch = (value: Partial<PopulationComparisonRequest>) =>
    setRequest((r) => ({ ...r, ...value }));
  const run = async () => {
    setWorking(true);
    setError("");
    try {
      const created = await post<PopulationComparisonJob>(`${base}/jobs`, {
        ...request,
        revision: workspace.revision,
      });
      select(created.id);
      await cache.invalidateQueries({
        queryKey: [workspace.id, "comparison-jobs"],
      });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setWorking(false);
    }
  };
  const operate = async (action: () => Promise<unknown>) => {
    setWorking(true);
    setError("");
    try {
      await action();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setWorking(false);
    }
  };
  if (!reference) return <p>Import an acquisition to compare populations.</p>;
  return (
    <div className="population-comparison-panel">
      <div className="panel-title">
        <div>
          <h2>Population comparison</h2>
          <p>
            Compare full event distributions against one or more control
            populations.
          </p>
        </div>
      </div>
      {error && (
        <div role="alert" className="error-message">
          {error}
        </div>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
      >
        <fieldset disabled={working || busy}>
          <label className="field">
            Comparison name
            <input
              aria-label="Comparison name"
              value={request.name}
              maxLength={125}
              required
              onChange={(e) => patch({ name: e.target.value })}
            />
          </label>
          <div className="comparison-cohorts">
            <Cohort
              label="Targets"
              workspace={workspace}
              sources={request.inputs}
              onChange={(inputs) => patch({ inputs })}
            />
            <Cohort
              label="Controls"
              workspace={workspace}
              sources={request.controls}
              onChange={(controls) => patch({ controls })}
            />
          </div>
          <button
            type="button"
            onClick={() =>
              patch({
                inputs: [
                  { sample_id: reference.id, gate_id: gate?.id ?? null },
                ],
              })
            }
          >
            Use current population as target
          </button>
          <fieldset className="comparison-parameters">
            <legend>Parameters and shared coordinate definitions</legend>
            {common.map((c) => {
              const p = request.parameters.find(
                (v) => v.channel === c.name && !v.ratio_channels,
              );
              return (
                <label key={c.name}>
                  <input
                    type="checkbox"
                    checked={!!p}
                    onChange={(e) =>
                      patch({
                        parameters: e.target.checked
                          ? [
                              ...request.parameters,
                              parameter(coordinateSample!, c.name),
                            ]
                          : request.parameters.filter((v) => v.id !== p?.id),
                      })
                    }
                  />
                  {channelLabel(c)}
                  {p && (
                    <button
                      type="button"
                      aria-label={`Define comparison ${c.name}`}
                      onClick={() => setEditing(p)}
                    >
                      Define coordinate
                    </button>
                  )}
                </label>
              );
            })}
            {request.parameters
              .filter(
                (p) =>
                  p.ratio_channels || !common.some((c) => c.name === p.channel),
              )
              .map((p) => (
                <div key={p.id}>
                  {p.label || p.channel}
                  <button type="button" onClick={() => setEditing(p)}>
                    Define coordinate
                  </button>
                  <button
                    type="button"
                    onClick={() =>
                      patch({
                        parameters: request.parameters.filter(
                          (v) => v.id !== p.id,
                        ),
                      })
                    }
                  >
                    Remove
                  </button>
                </div>
              ))}
            <button
              type="button"
              disabled={
                !coordinateSample?.channels.length ||
                request.parameters.length >= 64
              }
              onClick={() =>
                setEditing({
                  ...parameter(
                    coordinateSample!,
                    coordinateSample!.channels[0].name,
                  ),
                  id: id(),
                })
              }
            >
              Add coordinate or ratio
            </button>
          </fieldset>
          <div className="comparison-toolbar">
            <label className="field">
              Probability bins
              <input
                aria-label="Probability bins"
                type="number"
                min="2"
                max="4096"
                required
                value={request.probability_bins}
                onChange={(e) =>
                  patch({ probability_bins: e.target.valueAsNumber })
                }
              />
            </label>
            <label className="field">
              Histogram bins
              <input
                aria-label="Comparison histogram bins"
                type="number"
                min="16"
                max="1024"
                required
                value={request.histogram_bins}
                onChange={(e) =>
                  patch({ histogram_bins: e.target.valueAsNumber })
                }
              />
            </label>
            <label className="field">
              Minimum control events / joint bin
              <input
                aria-label="Minimum probability bin events"
                type="number"
                min="1"
                max="10000"
                required
                value={request.minimum_bin_events}
                onChange={(e) =>
                  patch({ minimum_bin_events: e.target.valueAsNumber })
                }
              />
            </label>
            <label className="field">
              Positive direction
              <select
                aria-label="Positive direction"
                value={request.positive_direction}
                onChange={(e) =>
                  patch({
                    positive_direction: e.target.value as "higher" | "lower",
                  })
                }
              >
                <option value="higher">Higher coordinates</option>
                <option value="lower">Lower coordinates</option>
              </select>
            </label>
            <label>
              <input
                type="checkbox"
                checked={request.joint}
                onChange={(e) => patch({ joint: e.target.checked })}
              />{" "}
              Joint probability comparison
            </label>
            <label>
              <input
                type="checkbox"
                checked={request.control_baselines}
                onChange={(e) => patch({ control_baselines: e.target.checked })}
              />{" "}
              Independent control baselines
            </label>
          </div>
          <p className="hint">
            Controls are pooled by event count. Overlapping control gates
            contribute each original event once. Baselines exclude the entire
            acquisition being tested.
          </p>
          {request.replace_result_id && (
            <p>
              Refitting {request.replace_result_id}. Previous results are
              retained.{" "}
              <button
                type="button"
                onClick={() => patch({ replace_result_id: null })}
              >
                Start a separate comparison
              </button>
            </p>
          )}
          <button
            type="submit"
            className="primary"
            disabled={
              !request.inputs.length ||
              !request.controls.length ||
              !request.parameters.length ||
              working
            }
          >
            {working ? <LoaderCircle size={14} /> : <Play size={14} />} Run
            comparison
          </button>
        </fieldset>
      </form>
      <section className="comparison-jobs">
        <h3>Comparisons and jobs</h3>
        {jobs.error && <ErrorState error={jobs.error} />}
        {jobs.data?.map((j) => (
          <div key={j.id} className="comparison-job">
            <button
              onClick={() => select(j.id)}
              aria-pressed={selected === j.id}
            >
              {j.request.name} · {j.status}
            </button>
            <span>{j.stage}</span>
            {(j.status === "queued" || j.status === "running") && (
              <>
                <progress value={j.progress} max="1" />
                <button
                  disabled={working}
                  onClick={() =>
                    void operate(async () => {
                      await post(`${base}/jobs/${j.id}/cancel`, {});
                      await cache.invalidateQueries({
                        queryKey: [workspace.id, "comparison-jobs"],
                      });
                    })
                  }
                >
                  Cancel
                </button>
              </>
            )}
            {j.error && <span role="alert">{j.error}</span>}
          </div>
        ))}
        {workspace.comparison_results
          ?.filter((r) => !jobs.data?.some((j) => j.id === r.id))
          .map((r) => (
            <button key={r.id} onClick={() => select(r.id)}>
              {r.request.name} · saved
            </button>
          ))}
      </section>
      {result.error && <ErrorState error={result.error} />}
      {current && (
        <>
          <div className="comparison-toolbar">
            <h3>{current.request.name}</h3>
            {saved && (
              <PopulationComparisonActions
                key={current.id}
                workspace={workspace}
                result={current}
                commit={commit}
                busy={busy || working}
                onRemoved={() => setSelected(null)}
                onError={setError}
              />
            )}
            {!saved && (
              <button
                className="primary"
                disabled={busy || working || !job?.can_apply}
                onClick={() =>
                  void operate(async () => {
                    await commit(
                      `/population-comparison/jobs/${current.id}/apply`,
                      {},
                      `Saved ${current.request.name}`,
                    );
                    await cache.invalidateQueries({
                      queryKey: [workspace.id, "comparison-jobs"],
                    });
                  })
                }
              >
                Review complete · Save comparison
              </button>
            )}
            <button
              disabled={working}
              onClick={() => {
                patch({
                  ...current.request,
                  revision: workspace.revision,
                  replace_result_id: saved ? current.id : null,
                });
              }}
            >
              <RefreshCw size={14} /> Refit settings
            </button>
            {saved && window.cytoforgeDesktop?.openComparisonWindow && (
              <button
                disabled={working}
                onClick={() =>
                  void operate(() =>
                    window.cytoforgeDesktop!.openComparisonWindow({
                      ...view,
                      resultId: current.id,
                    }),
                  )
                }
              >
                <ArrowUpRight size={14} /> Open comparison window
              </button>
            )}
          </div>
          <PopulationComparisonPlot
            result={current}
            view={view}
            onChange={setView}
          />
        </>
      )}
      {editing && coordinateSample && (
        <AxisDefinitionDialog
          axis="Comparison"
          workspace={workspace}
          sample={coordinateSample}
          value={{ ...editing, minimum: null, maximum: null }}
          onClose={() => setEditing(null)}
          onApply={(dimension) => {
            const {
              minimum: _minimum,
              maximum: _maximum,
              ...definition
            } = dimension;
            const p = {
              ...definition,
              id: editing.id,
              label: definition.channel,
            };
            patch({
              parameters: [
                ...request.parameters.filter((v) => v.id !== p.id),
                p,
              ],
            });
          }}
        />
      )}
    </div>
  );
}
