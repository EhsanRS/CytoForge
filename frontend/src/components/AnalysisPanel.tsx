import { useEffect, useMemo, useState } from "react";
import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  ArrowUpRight,
  Check,
  ChevronDown,
  Download,
  Layers3,
  LoaderCircle,
  Network,
  Play,
  RefreshCw,
  X,
} from "lucide-react";
import { api, ApiError, download, post } from "../api";
import type {
  AnalysisJob,
  AnalysisRequest,
  AnalysisResult,
  Gate,
  Sample,
  Workspace,
} from "../types";
import { channelLabel, formatNumber } from "../types";
import type { Commit } from "./Editors";
import { Empty, ErrorState, Tag } from "./Common";

const algorithms = [
  { id: "pca", name: "PCA", description: "Linear structure & marker loadings" },
  {
    id: "umap",
    name: "UMAP",
    description: "Local neighborhoods & nonlinear structure",
  },
  {
    id: "tsne",
    name: "t-SNE",
    description: "Local similarity in a fitted event subset",
  },
  {
    id: "flowsom",
    name: "FlowSOM",
    description: "Self-organizing map & consensus clusters",
  },
  {
    id: "phenograph",
    name: "PhenoGraph",
    description: "Jaccard neighbor graph & Louvain communities",
  },
] as const;
const defaults = {
  seed: 42,
  max_events: 10000,
  sampling: "balanced" as const,
  use_transforms: true,
  compensated: true,
  standardize: true,
  n_neighbors: 15,
  min_dist: 0.1,
  perplexity: 30,
  iterations: 1000,
  grid_size: 10,
  n_clusters: 10,
  epochs: 10,
  create_cluster_gates: true,
  min_cluster_size: 10,
  graph_resolution: 1,
  louvain_restarts: 5,
};
function pathName(gate: Gate, gates: Gate[]): string {
  const parent = gates.find((g) => g.id === gate.parent_id);
  return parent ? `${pathName(parent, gates)} / ${gate.name}` : gate.name;
}
function initialChannels(sample: Sample | null) {
  const ordinary =
    sample?.channels.filter(
      (c) =>
        !sample.computed_parameters.some((p) => p.name === c.name) &&
        !/^(fsc|ssc|time)/i.test(c.name),
    ) ?? [];
  return (ordinary.length >= 2 ? ordinary : (sample?.channels ?? []))
    .slice(0, 32)
    .map((c) => c.name);
}

export function AnalysisPanel({
  workspace,
  sample,
  gate,
  commit,
  busy,
  onExplore,
  initialInputs,
  initialParameters,
}: {
  workspace: Workspace;
  sample: Sample | null;
  gate: Gate | null;
  commit: Commit;
  busy: boolean;
  onExplore: (result: AnalysisResult, sampleId: string) => void;
  initialInputs?: AnalysisRequest["inputs"];
  initialParameters?: string[];
}) {
  const cache = useQueryClient();
  const [algorithm, setAlgorithm] =
    useState<AnalysisRequest["algorithm"]>("pca");
  const [name, setName] = useState("PCA 01");
  const [inputs, setInputs] = useState<AnalysisRequest["inputs"]>(
    initialInputs ??
      (sample ? [{ sample_id: sample.id, gate_id: gate?.id ?? null }] : []),
  );
  const [channels, setChannels] = useState(
    () => initialParameters ?? initialChannels(sample),
  );
  const [settings, setSettings] = useState<
    Omit<
      AnalysisRequest,
      "revision" | "name" | "algorithm" | "inputs" | "channels"
    >
  >({ ...defaults });
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const selectedSamples = workspace.samples.filter((s) =>
    inputs.some((i) => i.sample_id === s.id),
  );
  const commonChannels = useMemo(() => {
    const first = workspace.samples.find((s) => s.id === inputs[0]?.sample_id);
    return (
      first?.channels.filter((c) =>
        inputs.every((i) =>
          workspace.samples
            .find((s) => s.id === i.sample_id)
            ?.channels.some((other) => other.name === c.name),
        ),
      ) ?? []
    );
  }, [workspace.samples, inputs]);
  useEffect(() => {
    setChannels((previous) =>
      previous.filter((c) => commonChannels.some((v) => v.name === c)),
    );
  }, [commonChannels]);
  useEffect(() => {
    setInputs((previous) =>
      previous
        .filter((i) => workspace.samples.some((s) => s.id === i.sample_id))
        .map((i) => ({
          ...i,
          gate_id: workspace.gates.some((g) => g.id === i.gate_id)
            ? i.gate_id
            : null,
        })),
    );
  }, [workspace.samples, workspace.gates]);
  const jobs = useQuery({
    queryKey: ["jobs", workspace.id, workspace.revision],
    queryFn: ({ signal }) =>
      api<AnalysisJob[]>(`/workspaces/${workspace.id}/jobs`, { signal }),
    placeholderData: keepPreviousData,
    refetchInterval: (query) =>
      query.state.data?.some(
        (j) => j.status === "running" || j.status === "queued",
      )
        ? 800
        : 4000,
  });
  const analyses = useQuery({
    queryKey: ["analyses", workspace.id, workspace.revision],
    queryFn: ({ signal }) =>
      api<AnalysisResult[]>(`/workspaces/${workspace.id}/analyses`, { signal }),
    placeholderData: keepPreviousData,
  });
  const chooseAlgorithm = (value: AnalysisRequest["algorithm"]) => {
    setAlgorithm(value);
    setName(
      `${algorithms.find((a) => a.id === value)!.name} ${String(workspace.analyses.length + 1).padStart(2, "0")}`,
    );
    if (value === "tsne")
      setSettings((s) => ({ ...s, max_events: Math.min(s.max_events, 30000) }));
  };
  const selectSample = (id: string, include: boolean) => {
    setInputs((previous) =>
      include
        ? [...previous, { sample_id: id, gate_id: null }]
        : previous.filter((i) => i.sample_id !== id),
    );
  };
  const run = async (request?: AnalysisRequest) => {
    setError("");
    setSubmitting(true);
    try {
      await post<AnalysisJob>(
        `/workspaces/${workspace.id}/jobs`,
        request
          ? { ...request, revision: workspace.revision }
          : {
              ...settings,
              revision: workspace.revision,
              name: name.trim(),
              algorithm,
              inputs,
              channels,
            },
      );
      await cache.invalidateQueries({ queryKey: ["jobs", workspace.id] });
    } catch (err) {
      setError((err as Error).message);
      if (err instanceof ApiError && err.status === 409)
        await cache.invalidateQueries({
          queryKey: ["workspace", workspace.id],
        });
    } finally {
      setSubmitting(false);
    }
  };
  const apply = async (job: AnalysisJob) => {
    setError("");
    try {
      await commit(
        `/jobs/${job.id}/apply`,
        {},
        `${job.request.name} parameters added`,
      );
      await cache.invalidateQueries({ queryKey: ["jobs", workspace.id] });
    } catch (err) {
      setError((err as Error).message);
    }
  };
  if (!workspace.samples.length)
    return (
      <Empty
        icon={<Network size={40} />}
        title="Explore cell states"
        text="Import samples to compare marker structure, embed populations, and discover clusters."
      />
    );
  const running =
    jobs.data?.filter((j) => j.status === "running" || j.status === "queued")
      .length ?? 0;
  return (
    <div className="discovery-page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">HIGH-DIMENSIONAL CYTOMETRY</span>
          <h1>Discover cell states</h1>
          <p>
            Explore shared structure across populations, with every event traced
            to its original sample.
          </p>
        </div>
        <Tag color="#38d9ba">
          <Network size={13} />{" "}
          {running
            ? `${running} active ${running === 1 ? "analysis" : "analyses"}`
            : "Local analysis engine"}
        </Tag>
      </div>
      <div className="discovery-grid">
        <form
          className="panel analysis-config"
          onSubmit={(event) => {
            event.preventDefault();
            void run();
          }}
        >
          <div className="panel-heading">
            <h3>Configure an analysis</h3>
            <span className="muted">1 · Method</span>
          </div>
          <div
            className="algorithm-picker"
            role="radiogroup"
            aria-label="Analysis method"
          >
            {algorithms.map((a) => (
              <label
                className={algorithm === a.id ? "selected" : ""}
                key={a.id}
              >
                <input
                  type="radio"
                  name="algorithm"
                  value={a.id}
                  checked={algorithm === a.id}
                  onChange={() => chooseAlgorithm(a.id)}
                />
                <strong>{a.name}</strong>
                <span>{a.description}</span>
              </label>
            ))}
          </div>
          <label className="field">
            Analysis name
            <input
              required
              maxLength={130}
              aria-label="Analysis name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <div className="discovery-section-title">
            <h4>Input populations</h4>
            <span>2 · Samples</span>
          </div>
          <div className="analysis-sample-list">
            {workspace.samples.map((s) => {
              const input = inputs.find((i) => i.sample_id === s.id);
              return (
                <div key={s.id} className={input ? "selected" : ""}>
                  <label>
                    <input
                      type="checkbox"
                      aria-label={`Include ${s.name}`}
                      checked={!!input}
                      onChange={(e) => selectSample(s.id, e.target.checked)}
                    />
                    <span title={s.name}>{s.name}</span>
                    <small>{formatNumber(s.event_count, 0)}</small>
                  </label>
                  {input && (
                    <select
                      aria-label={`Population for ${s.name}`}
                      value={input.gate_id ?? ""}
                      onChange={(e) =>
                        setInputs((previous) =>
                          previous.map((i) =>
                            i.sample_id === s.id
                              ? { ...i, gate_id: e.target.value || null }
                              : i,
                          ),
                        )
                      }
                    >
                      <option value="">All events</option>
                      {workspace.gates
                        .filter((g) => g.sample_id === s.id)
                        .map((g) => (
                          <option value={g.id} key={g.id}>
                            {pathName(g, workspace.gates)}
                          </option>
                        ))}
                    </select>
                  )}
                </div>
              );
            })}
          </div>
          <div className="discovery-section-title">
            <h4>
              Features <span className="muted">({channels.length})</span>
            </h4>
            <button
              className="text-button"
              type="button"
              onClick={() =>
                setChannels(commonChannels.slice(0, 64).map((c) => c.name))
              }
            >
              Select all
            </button>
          </div>
          <div className="analysis-feature-list">
            {commonChannels.map((c) => (
              <label key={c.name}>
                <input
                  type="checkbox"
                  aria-label={`Feature ${c.name}`}
                  checked={channels.includes(c.name)}
                  onChange={(e) =>
                    setChannels((p) =>
                      e.target.checked
                        ? [...p, c.name]
                        : p.filter((n) => n !== c.name),
                    )
                  }
                />
                <span title={channelLabel(c)}>{channelLabel(c)}</span>
              </label>
            ))}
            {!commonChannels.length && (
              <p className="muted">Select samples with a shared panel.</p>
            )}
          </div>
          <div className="discovery-section-title">
            <h4>Fit & map</h4>
            <span>3 · Reproducibility</span>
          </div>
          <div className="form-grid">
            <label className="field">
              Maximum fitted events
              <input
                type="number"
                aria-label="Maximum fitted events"
                min={4}
                max={algorithm === "tsne" ? 30000 : 100000}
                step={1}
                value={settings.max_events}
                onChange={(e) =>
                  setSettings((s) => ({
                    ...s,
                    max_events: Number(e.target.value),
                  }))
                }
              />
            </label>
            <label className="field">
              Random seed
              <input
                type="number"
                aria-label="Random seed"
                min={0}
                max={2147483647}
                step={1}
                value={settings.seed}
                onChange={(e) =>
                  setSettings((s) => ({ ...s, seed: Number(e.target.value) }))
                }
              />
            </label>
          </div>
          <label className="field">
            Sampling strategy
            <select
              aria-label="Sampling strategy"
              value={settings.sampling}
              onChange={(e) =>
                setSettings((s) => ({
                  ...s,
                  sampling: e.target.value as AnalysisRequest["sampling"],
                }))
              }
            >
              <option value="balanced">Balanced across input samples</option>
              <option value="proportional">
                Proportional to population size
              </option>
            </select>
          </label>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={settings.use_transforms}
              onChange={(e) =>
                setSettings((s) => ({ ...s, use_transforms: e.target.checked }))
              }
            />
            Use each feature’s display transform
          </label>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={settings.standardize}
              onChange={(e) =>
                setSettings((s) => ({ ...s, standardize: e.target.checked }))
              }
            />
            Standardize features using the fitted set
          </label>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={settings.compensated}
              onChange={(e) =>
                setSettings((s) => ({ ...s, compensated: e.target.checked }))
              }
            />
            Use assigned compensation or unmixing
          </label>
          {!settings.compensated && (
            <p className="muted">
              Acquired detector values and populations are used before
              compensation.
            </p>
          )}
          {algorithm === "phenograph" && (
            <>
              <div className="form-grid">
                <label className="field">
                  Neighbors
                  <input
                    type="number"
                    aria-label="PhenoGraph neighbors"
                    min={2}
                    max={200}
                    value={settings.n_neighbors}
                    onChange={(e) =>
                      setSettings((s) => ({
                        ...s,
                        n_neighbors: Number(e.target.value),
                      }))
                    }
                  />
                </label>
                <label className="field">
                  Minimum community size
                  <input
                    type="number"
                    aria-label="Minimum community size"
                    min={2}
                    max={100000}
                    value={settings.min_cluster_size}
                    onChange={(e) =>
                      setSettings((s) => ({
                        ...s,
                        min_cluster_size: Number(e.target.value),
                      }))
                    }
                  />
                </label>
                <label className="field">
                  Louvain resolution
                  <input
                    type="number"
                    aria-label="Louvain resolution"
                    min={0.001}
                    max={100}
                    step={0.1}
                    value={settings.graph_resolution}
                    onChange={(e) =>
                      setSettings((s) => ({
                        ...s,
                        graph_resolution: Number(e.target.value),
                      }))
                    }
                  />
                </label>
                <label className="field">
                  Seeded restarts
                  <input
                    type="number"
                    aria-label="Louvain restarts"
                    min={1}
                    max={20}
                    value={settings.louvain_restarts}
                    onChange={(e) =>
                      setSettings((s) => ({
                        ...s,
                        louvain_restarts: Number(e.target.value),
                      }))
                    }
                  />
                </label>
              </div>
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={settings.create_cluster_gates}
                  onChange={(e) =>
                    setSettings((s) => ({
                      ...s,
                      create_cluster_gates: e.target.checked,
                    }))
                  }
                />
                Create community gates when adding the result
              </label>
              <p className="muted">
                Communities label fitted events only. Other events remain
                undefined; small discarded communities use label 0. Louvain uses
                igraph and reports the best modularity across the seeded
                restarts.
              </p>
            </>
          )}
          {algorithm === "umap" && (
            <div className="form-grid">
              <label className="field">
                Neighbors
                <input
                  type="number"
                  aria-label="UMAP neighbors"
                  min={2}
                  max={200}
                  value={settings.n_neighbors}
                  onChange={(e) =>
                    setSettings((s) => ({
                      ...s,
                      n_neighbors: Number(e.target.value),
                    }))
                  }
                />
              </label>
              <label className="field">
                Minimum distance
                <input
                  type="number"
                  aria-label="UMAP minimum distance"
                  min={0}
                  max={1}
                  step={0.01}
                  value={settings.min_dist}
                  onChange={(e) =>
                    setSettings((s) => ({
                      ...s,
                      min_dist: Number(e.target.value),
                    }))
                  }
                />
              </label>
            </div>
          )}
          {algorithm === "tsne" && (
            <div className="form-grid">
              <label className="field">
                Perplexity
                <input
                  type="number"
                  aria-label="t-SNE perplexity"
                  min={1}
                  max={1000}
                  value={settings.perplexity}
                  onChange={(e) =>
                    setSettings((s) => ({
                      ...s,
                      perplexity: Number(e.target.value),
                    }))
                  }
                />
              </label>
              <label className="field">
                Iterations
                <input
                  type="number"
                  aria-label="t-SNE iterations"
                  min={300}
                  max={10000}
                  step={50}
                  value={settings.iterations}
                  onChange={(e) =>
                    setSettings((s) => ({
                      ...s,
                      iterations: Number(e.target.value),
                    }))
                  }
                />
              </label>
            </div>
          )}
          {algorithm === "flowsom" && (
            <>
              <div className="form-grid">
                <label className="field">
                  SOM grid width
                  <input
                    type="number"
                    aria-label="SOM grid width"
                    min={2}
                    max={20}
                    value={settings.grid_size}
                    onChange={(e) =>
                      setSettings((s) => ({
                        ...s,
                        grid_size: Number(e.target.value),
                      }))
                    }
                  />
                </label>
                <label className="field">
                  Metaclusters
                  <input
                    type="number"
                    aria-label="Metaclusters"
                    min={2}
                    max={100}
                    value={settings.n_clusters}
                    onChange={(e) =>
                      setSettings((s) => ({
                        ...s,
                        n_clusters: Number(e.target.value),
                      }))
                    }
                  />
                </label>
                <label className="field">
                  Training epochs
                  <input
                    type="number"
                    aria-label="Training epochs"
                    min={1}
                    max={100}
                    value={settings.epochs}
                    onChange={(e) =>
                      setSettings((s) => ({
                        ...s,
                        epochs: Number(e.target.value),
                      }))
                    }
                  />
                </label>
              </div>
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={settings.create_cluster_gates}
                  onChange={(e) =>
                    setSettings((s) => ({
                      ...s,
                      create_cluster_gates: e.target.checked,
                    }))
                  }
                />
                Create a population for each metacluster
              </label>
            </>
          )}
          <p className="analysis-mapping-note">
            {algorithm === "phenograph"
              ? "Only fitted events receive community labels. Unfitted events stay undefined; label 0 identifies discarded small communities. Acquired parent gates are saved when using raw data."
              : algorithm === "tsne"
                ? "Only fitted events receive t-SNE coordinates. Remaining events stay undefined; gates on this embedding describe the fitted subset."
                : algorithm === "umap"
                  ? "The fitted set defines the embedding. Remaining eligible events receive coordinates through UMAP transform."
                  : algorithm === "pca"
                    ? "Fit principal components on the selected set, then project every eligible event into the same coordinate system."
                    : "Train the SOM on the selected set, compute consensus metaclusters, then assign every eligible event to its nearest node."}{" "}
            Nonfinite events are excluded.
          </p>
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <button
            className="button primary run-analysis"
            type="submit"
            disabled={
              busy ||
              submitting ||
              inputs.length === 0 ||
              channels.length < 2 ||
              channels.length > 64 ||
              !name.trim()
            }
          >
            {submitting ? (
              <LoaderCircle className="spin" size={16} />
            ) : (
              <Play size={16} />
            )}
            Run {algorithms.find((a) => a.id === algorithm)!.name}
          </button>
          <p className="analysis-config-footer">
            {selectedSamples.length}{" "}
            {selectedSamples.length === 1 ? "sample" : "samples"} ·{" "}
            {channels.length} features · seed {settings.seed}
          </p>
        </form>
        <div className="analysis-results-column">
          <div className="panel analysis-job-panel">
            <div className="panel-heading">
              <div>
                <h3>Analysis queue</h3>
                <p className="muted">
                  Continue gating while analyses run. Add results when ready.
                </p>
              </div>
              <button
                type="button"
                className="icon-button"
                aria-label="Refresh analyses"
                onClick={() => jobs.refetch()}
              >
                <RefreshCw size={16} />
              </button>
            </div>
            {jobs.isError && (
              <ErrorState error={jobs.error} onRetry={() => jobs.refetch()} />
            )}
            {jobs.data?.length ? (
              <div className="analysis-jobs">
                {jobs.data.map((job) => (
                  <JobCard
                    key={job.id}
                    job={job}
                    workspace={workspace}
                    busy={busy || submitting}
                    onRun={run}
                    onApply={() => apply(job)}
                    onExplore={onExplore}
                    onCancel={async () => {
                      try {
                        await post(
                          `/workspaces/${workspace.id}/jobs/${job.id}/cancel`,
                          {},
                        );
                        await jobs.refetch();
                      } catch (err) {
                        setError((err as Error).message);
                      }
                    }}
                  />
                ))}
              </div>
            ) : (
              <div className="analysis-empty">
                <Network size={48} />
                <h3>Your next discovery starts here</h3>
                <p>
                  Choose populations and markers, then run an analysis.
                  <br />
                  Results retain event identities, settings, and software
                  versions.
                </p>
                <span>
                  100% local · Reproducible sampling · Cancellable jobs
                </span>
              </div>
            )}
          </div>
          {!!analyses.data?.length && (
            <section className="panel saved-analyses">
              <div className="panel-heading">
                <h3>Workspace analyses</h3>
                <Tag>{analyses.data.length} saved</Tag>
              </div>
              {analyses.data.map((result) => (
                <div className="saved-analysis-row" key={result.id}>
                  <div>
                    <strong>{result.request.name}</strong>
                    <span>
                      {result.request.algorithm.toUpperCase()} ·{" "}
                      {formatNumber(
                        result.data.reduce((sum, d) => sum + d.mapped_count, 0),
                        0,
                      )}{" "}
                      mapped events
                    </span>
                    {result.stale && (
                      <small className="analysis-warning">
                        Scientific inputs changed. These parameters retain the
                        original analysis; rerun to refresh.
                      </small>
                    )}
                  </div>
                  <button
                    className="button small"
                    onClick={() =>
                      onExplore(
                        result,
                        result.data.find((d) =>
                          workspace.samples.some((s) => s.id === d.sample_id),
                        )?.sample_id ?? result.data[0].sample_id,
                      )
                    }
                    disabled={
                      !result.data.some((d) =>
                        workspace.samples.some((s) => s.id === d.sample_id),
                      )
                    }
                  >
                    Explore
                    <ArrowUpRight size={14} />
                  </button>
                </div>
              ))}
            </section>
          )}
          <div className="analysis-principles">
            <Layers3 size={18} />
            <p>
              Samples share one fitted model. Sampling controls the fit, while
              mapped counts show which events received results. Parameters are
              saved snapshots and can be used for gates, statistics, reports,
              and export.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

function JobCard({
  job,
  workspace,
  busy,
  onRun,
  onApply,
  onExplore,
  onCancel,
}: {
  job: AnalysisJob;
  workspace: Workspace;
  busy: boolean;
  onRun: (request: AnalysisRequest) => Promise<void>;
  onApply: () => Promise<void>;
  onExplore: (result: AnalysisResult, sampleId: string) => void;
  onCancel: () => Promise<void>;
}) {
  const [details, setDetails] = useState(false);
  const full = useQuery({
    queryKey: ["job", workspace.id, job.id],
    queryFn: ({ signal }) =>
      api<AnalysisJob>(`/workspaces/${workspace.id}/jobs/${job.id}`, {
        signal,
      }),
    enabled: details && !!job.result,
    staleTime: Infinity,
  });
  const result = full.data?.result ?? job.result;
  const active = job.status === "queued" || job.status === "running";
  const saved = workspace.analyses.some((a) => a.id === job.id);
  const sampleId = result?.data.find((d) =>
    workspace.samples.some((s) => s.id === d.sample_id),
  )?.sample_id;
  return (
    <article className={`analysis-job ${job.status}`}>
      <div className="analysis-job-header">
        <div className="analysis-job-icon">
          {active ? (
            <LoaderCircle className="spin" size={19} />
          ) : job.status === "failed" ? (
            <X size={19} />
          ) : job.status === "succeeded" || job.status === "applied" ? (
            <Check size={19} />
          ) : (
            <Network size={19} />
          )}
        </div>
        <div>
          <h4>{job.request.name}</h4>
          <span>
            {job.request.algorithm.toUpperCase()} · {job.request.inputs.length}{" "}
            {job.request.inputs.length === 1 ? "sample" : "samples"} ·{" "}
            {job.request.channels.length} features
          </span>
        </div>
        <Tag
          color={
            job.status === "failed" ? "#ef8b9b" : active ? "#719bff" : undefined
          }
        >
          {job.status === "succeeded" ? "Ready" : saved ? "Saved" : job.status}
        </Tag>
      </div>
      {active && (
        <>
          <div
            className="analysis-progress"
            role="progressbar"
            aria-label={`${job.request.name} progress`}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(job.progress * 100)}
          >
            <i style={{ width: `${Math.max(3, job.progress * 100)}%` }} />
          </div>
          <div className="analysis-job-stage">
            <span role="status">{job.stage}</span>
            <button
              className="text-button"
              type="button"
              aria-label={`Cancel ${job.request.name}`}
              onClick={() => onCancel()}
            >
              Cancel
            </button>
          </div>
        </>
      )}
      {job.error && (
        <p className="analysis-warning" role="alert">
          {job.error}
        </p>
      )}
      {(job.status === "cancelled" || job.status === "interrupted") && (
        <p className="muted">{job.stage}</p>
      )}
      {result && (
        <>
          <div className="analysis-job-metrics">
            <div>
              <span>FITTED</span>
              <strong>
                {formatNumber(
                  result.data.reduce((sum, d) => sum + d.fitted_count, 0),
                  0,
                )}
              </strong>
            </div>
            <div>
              <span>MAPPED</span>
              <strong>
                {formatNumber(
                  result.data.reduce((sum, d) => sum + d.mapped_count, 0),
                  0,
                )}
              </strong>
            </div>
            <div>
              <span>ELIGIBLE</span>
              <strong>
                {formatNumber(
                  result.data.reduce((sum, d) => sum + d.finite_count, 0),
                  0,
                )}
              </strong>
            </div>
            <div>
              <span>COMPUTE</span>
              <strong>{formatNumber(result.duration_seconds, 1)}s</strong>
            </div>
          </div>
          {!!result.diagnostics.explained_variance_ratio && (
            <div className="pca-variance">
              <span>Explained variance</span>
              {result.diagnostics.explained_variance_ratio.map((v, i) => (
                <div key={i}>
                  <span>PC{i + 1}</span>
                  <div>
                    <i style={{ width: `${v * 100}%` }} />
                  </div>
                  <strong>{(v * 100).toFixed(1)}%</strong>
                </div>
              ))}
            </div>
          )}
          <p className="analysis-result-mapping">
            {result.diagnostics.mapping}
          </p>
          {result.request.algorithm === "phenograph" && (
            <>
              <div className="analysis-job-metrics">
                <div>
                  <span>COMMUNITIES</span>
                  <strong>
                    {
                      Object.keys(result.diagnostics.community_sizes ?? {})
                        .length
                    }
                  </strong>
                </div>
                <div>
                  <span>GRAPH EDGES</span>
                  <strong>
                    {formatNumber(result.diagnostics.graph_edges ?? 0, 0)}
                  </strong>
                </div>
                <div>
                  <span>BEST MODULARITY</span>
                  <strong>
                    {typeof result.diagnostics.modularity === "number"
                      ? result.diagnostics.modularity.toFixed(4)
                      : "—"}
                  </strong>
                </div>
                <div>
                  <span>UNASSIGNED FITTED</span>
                  <strong>
                    {formatNumber(
                      result.diagnostics.unassigned_fitted_count ?? 0,
                      0,
                    )}
                  </strong>
                </div>
              </div>
              <p className="muted">
                Community IDs describe this fitted graph. Interpret cell types
                and AF reference spectra using the acquired markers and
                controls.
              </p>
            </>
          )}
          {result.warnings.map((warning) => (
            <p className="analysis-warning" key={warning}>
              {warning}
            </p>
          ))}
          {(job.stale || (job.status === "succeeded" && !job.can_apply)) && (
            <p className="analysis-warning">
              Workspace inputs or revision changed. Run this analysis again
              before adding results.
            </p>
          )}
        </>
      )}
      {!active && (
        <div className="analysis-job-actions">
          {job.status === "succeeded" && (
            <button
              className="button small primary"
              disabled={busy || !job.can_apply || job.stale}
              onClick={() => onApply()}
            >
              <Check size={14} />
              Add parameters
            </button>
          )}
          {saved && result && sampleId && (
            <button
              className="button small primary"
              onClick={() => onExplore(result, sampleId)}
            >
              Explore result
              <ArrowUpRight size={14} />
            </button>
          )}
          <button
            className="button small"
            disabled={busy}
            onClick={() => onRun(job.request)}
          >
            <RefreshCw size={14} />
            Run again
          </button>
          {result && (
            <button
              className="text-button"
              aria-expanded={details}
              onClick={() => setDetails(!details)}
            >
              Provenance
              <ChevronDown size={14} />
            </button>
          )}
        </div>
      )}
      {details && result && (
        <div className="analysis-provenance">
          <div>
            <span>Seed {job.request.seed}</span>
            <span>{job.request.sampling} sampling</span>
            <span>
              {job.request.use_transforms
                ? "Display transforms"
                : "Linear feature values"}
            </span>
            <span>{job.request.standardize ? "Standardized" : "Unscaled"}</span>
          </div>
          <p>{job.request.channels.join(" · ")}</p>
          <table>
            <thead>
              <tr>
                <th>Sample</th>
                <th>Finite</th>
                <th>Fitted</th>
                <th>Mapped</th>
              </tr>
            </thead>
            <tbody>
              {result.data.map((data) => (
                <tr key={data.sample_id}>
                  <td>
                    {workspace.samples.find((s) => s.id === data.sample_id)
                      ?.name ?? "Removed sample"}
                  </td>
                  <td>{formatNumber(data.finite_count, 0)}</td>
                  <td>{formatNumber(data.fitted_count, 0)}</td>
                  <td>{formatNumber(data.mapped_count, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {result.diagnostics.loadings && (
            <table className="pca-loadings">
              <thead>
                <tr>
                  <th>Component weight</th>
                  <th>PC1</th>
                  <th>PC2</th>
                </tr>
              </thead>
              <tbody>
                {result.diagnostics.feature_names.map((feature, i) => (
                  <tr key={feature}>
                    <td>{feature}</td>
                    <td>{result.diagnostics.loadings![i][0].toFixed(4)}</td>
                    <td>{result.diagnostics.loadings![i][1].toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="analysis-versions">
            {Object.entries(result.versions)
              .map(([name, value]) => `${name} ${value}`)
              .join(" · ")}
          </p>
          {saved && (
            <button
              className="button small"
              onClick={() =>
                download(
                  `/workspaces/${workspace.id}/analyses/${job.id}/provenance`,
                  `${job.request.name}.json`,
                )
              }
            >
              <Download size={14} />
              Export provenance
            </button>
          )}
        </div>
      )}
    </article>
  );
}
