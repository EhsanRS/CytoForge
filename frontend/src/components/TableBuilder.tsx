import { useEffect, useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  ChevronLeft,
  ChevronRight,
  Download,
  FileSpreadsheet,
  Plus,
  Save,
  Trash2,
} from "lucide-react";
import { api, download } from "../api";
import { uniqueComparisonPopulations } from "../comparisons";
import type {
  TableColumn,
  TableComparison,
  TableDefinition,
  TableEvaluation,
  TablePivot,
  Workspace,
} from "../types";
import { channelLabel, id } from "../types";
import { Empty, ErrorState, Loading, Tag } from "./Common";
import type { Commit } from "./Editors";

const statistics: Record<string, string> = {
  count: "Event count",
  percent_parent: "% of parent",
  percent_total: "% of total",
  finite_count: "Finite event count",
  nonfinite_count: "Nonfinite event count",
  mean: "Arithmetic mean",
  median: "Median",
  std: "Sample standard deviation",
  variance: "Sample variance",
  cv: "CV (%)",
  robust_cv: "Robust CV (%)",
  mad: "Median absolute deviation",
  geometric_mean: "Geometric mean",
  geometric_std: "Geometric standard deviation",
  min: "Minimum",
  max: "Maximum",
  percentile: "Percentile",
};
const metrics: Record<string, string> = {
  converged: "Fit converged (1 or 0)",
  precursor_frequency: "Precursor frequency",
  percent_divided: "Percent divided",
  division_index: "Division index",
  proliferation_index: "Proliferation index",
  expansion_index: "Expansion index",
  replication_index: "Replication index",
  observed_divided_fraction: "Collected divided fraction",
  precursor_equivalents: "Precursor equivalents",
  responding_precursor_equivalents: "Responding precursor equivalents",
  fraction: "Model fraction",
  model_count: "Model event count",
  expected_count: "Posterior expected count",
  assigned_count: "Assigned event count",
  fitted_count: "Fitted events",
  normalized_rmsd: "Normalized RMS discrepancy",
  undivided_mean: "Undivided center",
  dye_cv: "Dye CV (%)",
  peak_ratio: "Peak ratio",
  background: "Background mean",
  background_sd: "Background standard deviation",
  g1_mean: "G1 center",
  g2_mean: "G2 center",
  g1_cv: "G1 CV (%)",
  g2_cv: "G2 CV (%)",
};
export const kineticsMetrics: Record<string, string> = {
  peak: "Peak curve value",
  peak_time: "Peak time",
  mean: "Mean curve value",
  slope: "Slope (least squares)",
  auc: "Area under measured curve",
  duration: "Range duration",
  covered_duration: "Measured integration duration",
  population_count: "Timed source events",
  finite_count: "Finite signal events",
  selected_count: "Selected signal events",
  responder_count: "Responder events",
  responder_percent: "Responder percentage",
  curve_points: "Measured curve points",
  threshold: "Responder threshold",
  baseline_count: "Baseline finite events",
  represented_count: "Represented events (whole curve)",
  time_count: "Timed events (whole acquisition)",
};
export function biologyModels(
  workspace: Workspace,
  platform: TableColumn["platform"],
) {
  return platform === "cell-cycle"
    ? workspace.cell_cycle_results
    : platform === "kinetics"
      ? (workspace.kinetics_results ?? [])
      : workspace.proliferation_results;
}
export function KineticsRangeField({
  workspace,
  column,
  change,
  label,
}: {
  workspace: Workspace;
  column: TableColumn;
  change: (patch: Partial<TableColumn>) => void;
  label: string;
}) {
  const ranges =
    workspace.kinetics_results?.find((r) => r.id === column.result_id)?.fits[0]
      ?.ranges ?? [];
  return (
    <label className="field">
      Time range
      <select
        aria-label={label}
        value={column.kinetics_range_id ?? ""}
        onChange={(e) => change({ kinetics_range_id: e.target.value || null })}
      >
        <option value="">Select time range</option>
        {ranges.map((r) => (
          <option key={r.id} value={r.id}>
            {r.name} · {r.start}–{r.end}
          </option>
        ))}
        {column.kinetics_range_id &&
          !ranges.some((r) => r.id === column.kinetics_range_id) && (
            <option value={column.kinetics_range_id}>Missing time range</option>
          )}
      </select>
    </label>
  );
}
const numeric = (column: TableColumn) =>
  column.kind !== "metadata" || column.metadata_numeric;
function column(name = "Events"): TableColumn {
  return {
    id: id(),
    name,
    kind: "statistic",
    statistic: "count",
    channel: "",
    percentile: 50,
    population_path: null,
    population_overrides: {},
    control_sample_id: null,
    metadata_source: "tags",
    metadata_key: "",
    metadata_numeric: false,
    expression: "",
    formula_refs: {},
    platform: "proliferation",
    result_id: null,
    biology_metric: "precursor_frequency",
    kinetics_range_id: null,
    generation: 0,
    allow_stale: false,
    follow_replacement: true,
    hidden: false,
    decimals: 2,
    heatmap: false,
  };
}
export {
  column as newTableColumn,
  statistics as tableStatistics,
  metrics as biologyMetrics,
};

function fresh(groupId: string | null): TableDefinition {
  return {
    id: id(),
    name: "Custom statistics",
    channel: "",
    filter: "",
    columns: [{ ...column(), decimals: 0 }],
    row_mode: "samples",
    group_id: groupId,
    sample_ids: [],
    compensated: true,
    sort_by: "sample",
    descending: false,
    pivot: null,
    comparison: null,
  };
}
function valueText(value: string | number | null | undefined, decimals = 2) {
  return typeof value === "number"
    ? new Intl.NumberFormat("en", {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      }).format(value)
    : (value ?? "—");
}
function probabilityText(value: number | null) {
  return value != null && value > 0 && value < 1e-6
    ? value.toExponential(3)
    : valueText(value, 6);
}
function ColumnChoices({
  label,
  columns,
  values,
  onChange,
}: {
  label: string;
  columns: TableColumn[];
  values: string[];
  onChange: (v: string[]) => void;
}) {
  return (
    <fieldset className="table-choice-set">
      <legend>{label}</legend>
      {columns.map((c) => (
        <label className="checkbox" key={c.id}>
          <input
            type="checkbox"
            aria-label={`${label}: ${c.name}`}
            checked={values.includes(c.id)}
            onChange={(e) =>
              onChange(
                e.target.checked
                  ? [...values, c.id]
                  : values.filter((v) => v !== c.id),
              )
            }
          />
          {c.name}
        </label>
      ))}
    </fieldset>
  );
}

export function TableBuilder({
  workspace,
  groupId,
  commit,
  busy,
  onOpen,
}: {
  workspace: Workspace;
  groupId: string | null;
  commit: Commit;
  busy: boolean;
  onOpen: (sampleId: string, gateId: string | null) => void;
}) {
  const [draft, setDraft] = useState(() => fresh(groupId));
  const [settled, setSettled] = useState(draft);
  const [selected, setSelected] = useState(draft.columns[0].id);
  const [view, setView] = useState<"data" | "pivot" | "comparisons">("data");
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState("");
  const [exporting, setExporting] = useState(false);
  const saved = workspace.tables.find((t) => t.id === draft.id);
  useEffect(() => {
    const timeout = setTimeout(() => {
      setSettled(draft);
      setOffset(0);
    }, 350);
    return () => clearTimeout(timeout);
  }, [draft]);
  const query = useQuery({
    queryKey: [
      "custom-table",
      workspace.id,
      workspace.revision,
      settled,
      offset,
    ],
    queryFn: ({ signal }) =>
      api<TableEvaluation>(`/workspaces/${workspace.id}/tables/evaluate`, {
        method: "POST",
        body: JSON.stringify({
          definition: settled,
          revision: workspace.revision,
          offset,
          limit: 100,
        }),
        signal,
      }),
    placeholderData: keepPreviousData,
    gcTime: 60000,
  });
  const result = query.data;
  const editing = draft !== settled;
  const ready =
    !!result &&
    !editing &&
    !query.isFetching &&
    !query.error &&
    result.revision === workspace.revision;
  const current = draft.columns.find((c) => c.id === selected);
  const channels = useMemo(
    () =>
      Array.from(
        new Map(
          workspace.samples.flatMap((s) => s.channels).map((c) => [c.name, c]),
        ).values(),
      ),
    [workspace.samples],
  );
  const paths = useMemo(() => {
    const gates = new Map(workspace.gates.map((g) => [g.id, g]));
    function path(identifier: string): string[] {
      const gate = gates.get(identifier)!;
      return [...(gate.parent_id ? path(gate.parent_id) : []), gate.name];
    }
    return Array.from(
      new Map(
        workspace.gates.map((g) => {
          const p = path(g.id);
          return [JSON.stringify(p), p] as const;
        }),
      ).values(),
    );
  }, [workspace.gates]);
  function set<K extends keyof TableDefinition>(
    key: K,
    value: TableDefinition[K],
  ) {
    setDraft((d) => ({ ...d, [key]: value }));
  }
  function edit(patch: Partial<TableColumn>) {
    setDraft((d) => ({
      ...d,
      columns: d.columns.map((c) =>
        c.id === selected ? { ...c, ...patch } : c,
      ),
    }));
  }
  function load(definition: TableDefinition) {
    setDraft(structuredClone(definition));
    setSelected(definition.columns[0]?.id ?? "");
    setOffset(0);
    setError("");
  }
  useEffect(() => {
    if (
      result &&
      !draft.columns.length &&
      result.definition.id === draft.id &&
      result.definition.columns.length
    ) {
      setDraft(result.definition);
      setSelected(result.definition.columns[0].id);
    }
  }, [result, draft]);
  useEffect(() => {
    if (!ready || !result || result.definition.id !== draft.id) return;
    const canonical = new Map(result.definition.columns.map((c) => [c.id, c]));
    if (
      !draft.columns.some((c) => {
        const refs = canonical.get(c.id)?.formula_refs;
        return (
          refs &&
          (Object.keys(refs).length !== Object.keys(c.formula_refs).length ||
            Object.entries(refs).some(
              ([name, target]) => c.formula_refs[name] !== target,
            ))
        );
      })
    )
      return;
    const bound = {
      ...draft,
      columns: draft.columns.map((c) => ({
        ...c,
        formula_refs: canonical.get(c.id)?.formula_refs ?? c.formula_refs,
      })),
    };
    setDraft(bound);
    setSettled(bound);
  }, [result, draft, ready]);
  async function act(action: () => Promise<unknown>) {
    setError("");
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function exportTable(format: "csv" | "xlsx" | "json") {
    setExporting(true);
    await act(() =>
      download(
        `/workspaces/${workspace.id}/tables/export/${format}`,
        `${draft.name}.${format}`,
        {
          method: "POST",
          body: JSON.stringify({
            definition: result?.definition ?? draft,
            revision: workspace.revision,
          }),
        },
      ),
    );
    setExporting(false);
  }
  const visible = (result?.columns ?? []).filter((c) => !c.hidden);
  const numericColumns = draft.columns.filter(numeric);
  const heatRanges = result?.column_ranges ?? {};
  function move(delta: number) {
    const index = draft.columns.findIndex((c) => c.id === selected),
      target = index + delta;
    if (index < 0 || target < 0 || target >= draft.columns.length) return;
    const values = [...draft.columns];
    [values[index], values[target]] = [values[target], values[index]];
    set("columns", values);
  }
  const dependents = current
    ? draft.columns.filter(
        (c) =>
          Object.values(c.formula_refs).includes(current.id) ||
          (c.kind === "formula" &&
            c.expression.includes(JSON.stringify(current.name))),
      )
    : [];
  const dimensionRefs =
    current &&
    [
      ...(draft.pivot?.rows ?? []),
      ...(draft.pivot?.columns ?? []),
      ...(draft.pivot?.measures ?? []),
      ...(draft.comparison?.measures ?? []),
      draft.comparison?.group_column,
      draft.comparison?.pair_column,
    ].includes(current.id);
  function updatePivot(patch: Partial<TablePivot>) {
    set("pivot", { ...draft.pivot!, ...patch });
  }
  function updateComparison(patch: Partial<TableComparison>) {
    set("comparison", { ...draft.comparison!, ...patch });
  }

  return (
    <div className="page-panel custom-table-builder">
      <div className="page-heading">
        <div>
          <span className="eyebrow">LIVE EXPERIMENT TABLES</span>
          <h1>Custom table</h1>
          <p>
            Map populations, build formulas and compare sample-level responses.
          </p>
        </div>
        <div className="button-group">
          {(["csv", "xlsx", "json"] as const).map((format) => (
            <button
              className={`button ${format === "xlsx" ? "primary" : "ghost"}`}
              key={format}
              disabled={busy || !ready || exporting}
              onClick={() => void exportTable(format)}
            >
              <Download size={15} />
              {format.toUpperCase()}
            </button>
          ))}
        </div>
      </div>
      <div className="panel custom-table-settings">
        <label className="field">
          Table name
          <input
            aria-label="Custom table name"
            value={draft.name}
            maxLength={160}
            onChange={(e) => set("name", e.target.value)}
          />
        </label>
        <label className="field">
          Saved definition
          <select
            aria-label="Custom saved table"
            value={saved ? draft.id : ""}
            onChange={(e) => {
              const value = workspace.tables.find(
                (t) => t.id === e.target.value,
              );
              if (value) load(value);
              else load(fresh(groupId));
            }}
          >
            <option value="">New table</option>
            {workspace.tables.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Rows
          <select
            aria-label="Table row mode"
            value={draft.row_mode}
            onChange={(e) => {
              setDraft((d) => ({
                ...d,
                row_mode: e.target.value as TableDefinition["row_mode"],
                comparison:
                  e.target.value === "populations" ? null : d.comparison,
              }));
            }}
          >
            <option value="samples">One row per sample</option>
            <option value="populations">All sample populations</option>
          </select>
        </label>
        <label className="field">
          Sample scope
          <select
            aria-label="Table sample group"
            value={draft.group_id ?? ""}
            onChange={(e) => set("group_id", e.target.value || null)}
          >
            <option value="">All samples</option>
            {workspace.groups.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
            {draft.group_id &&
              !workspace.groups.some((g) => g.id === draft.group_id) && (
                <option value={draft.group_id}>Missing saved group</option>
              )}
          </select>
        </label>
        <label className="field">
          Filter rows
          <input
            aria-label="Custom table filter"
            value={draft.filter}
            placeholder="Sample or population name"
            onChange={(e) => set("filter", e.target.value)}
          />
        </label>
        <label className="checkbox">
          <input
            aria-label="Compensated table statistics"
            type="checkbox"
            checked={draft.compensated}
            onChange={(e) => set("compensated", e.target.checked)}
          />
          Compensated intensities
        </label>
        <div className="button-group">
          <button
            className="button primary"
            disabled={busy || !ready || !draft.name.trim()}
            onClick={() =>
              void act(async () => {
                const doc = await commit(
                  "/tables/save",
                  { definition: result!.definition },
                  "Saved custom table",
                );
                load(doc.tables.find((t) => t.id === draft.id)!);
              })
            }
          >
            <Save size={15} />
            Save custom table
          </button>
          <button className="button ghost" onClick={() => load(fresh(groupId))}>
            <Plus size={15} />
            New table
          </button>
          {saved && (
            <button
              className="button ghost"
              disabled={busy}
              onClick={() =>
                void act(async () => {
                  await commit(
                    `/tables/${draft.id}`,
                    {},
                    "Removed saved table",
                    "DELETE",
                  );
                  load(fresh(groupId));
                })
              }
            >
              <Trash2 size={15} />
              Remove saved table
            </button>
          )}
        </div>
        <details className="custom-sample-scope">
          <summary>
            Choose individual samples{" "}
            {draft.sample_ids.length
              ? `(${draft.sample_ids.length})`
              : "(all in scope)"}
          </summary>
          <p className="muted small">
            A nonempty selection is intersected with the selected sample group.
          </p>
          <div className="table-checkbox-grid">
            {workspace.samples.map((s) => (
              <label className="checkbox" key={s.id}>
                <input
                  type="checkbox"
                  aria-label={`Table sample ${s.name}`}
                  checked={draft.sample_ids.includes(s.id)}
                  onChange={(e) =>
                    set(
                      "sample_ids",
                      e.target.checked
                        ? [...draft.sample_ids, s.id]
                        : draft.sample_ids.filter((v) => v !== s.id),
                    )
                  }
                />
                {s.name}
              </label>
            ))}
          </div>
        </details>
      </div>
      {error && <ErrorState error={new Error(error)} />}
      <div className="custom-table-grid">
        <aside className="panel custom-column-editor">
          <div className="panel-heading">
            <h3>Columns</h3>
            <button
              className="button small"
              onClick={() => {
                const value = column(`Column ${draft.columns.length + 1}`);
                set("columns", [...draft.columns, value]);
                setSelected(value.id);
              }}
            >
              <Plus size={14} />
              Add column
            </button>
          </div>
          <div className="custom-column-list" aria-label="Table columns">
            {draft.columns.map((c) => (
              <button
                key={c.id}
                className={`custom-column-item ${c.id === selected ? "selected" : ""}`}
                aria-label={`Edit table column ${c.name}`}
                aria-pressed={c.id === selected}
                onClick={() => setSelected(c.id)}
              >
                <span>{c.name}</span>
                <small>
                  {c.kind}
                  {c.hidden ? " · hidden" : ""}
                </small>
              </button>
            ))}
          </div>
          {current && (
            <div className="custom-column-fields">
              <div className="button-group">
                <button
                  className="icon-button"
                  aria-label="Move column left"
                  disabled={selected === draft.columns[0]?.id}
                  onClick={() => move(-1)}
                >
                  <ArrowUp size={15} />
                </button>
                <button
                  className="icon-button"
                  aria-label="Move column right"
                  disabled={selected === draft.columns.at(-1)?.id}
                  onClick={() => move(1)}
                >
                  <ArrowDown size={15} />
                </button>
                <button
                  className="button small ghost"
                  disabled={
                    draft.columns.length <= 1 ||
                    dependents.length > 0 ||
                    !!dimensionRefs
                  }
                  title={
                    dependents.length
                      ? `Used by ${dependents.map((c) => c.name).join(", ")}`
                      : dimensionRefs
                        ? "Remove this column from pivot/comparison settings first"
                        : "Remove column"
                  }
                  onClick={() => {
                    setDraft((d) => ({
                      ...d,
                      columns: d.columns.filter((c) => c.id !== selected),
                      sort_by: d.sort_by === selected ? "sample" : d.sort_by,
                    }));
                    setSelected(
                      draft.columns.find((c) => c.id !== selected)!.id,
                    );
                  }}
                >
                  <Trash2 size={14} />
                  Remove column
                </button>
              </div>
              <label className="field">
                Column name
                <input
                  aria-label="Custom column name"
                  maxLength={160}
                  value={current.name}
                  onChange={(e) => edit({ name: e.target.value })}
                />
              </label>
              <label className="field">
                Column type
                <select
                  aria-label="Custom column type"
                  value={current.kind}
                  onChange={(e) =>
                    edit({
                      kind: e.target.value as TableColumn["kind"],
                      control_sample_id: null,
                      ...(e.target.value === "population_comparison"
                        ? {
                            biology_metric: "ks_distance",
                            result_id: null,
                            comparison_parameter_id: null,
                          }
                        : {}),
                    })
                  }
                >
                  <option value="statistic">Population statistic</option>
                  <option value="metadata">Keyword</option>
                  <option value="formula">Formula</option>
                  <option value="biology">Biological model</option>
                  <option value="population_comparison">
                    Population comparison
                  </option>
                </select>
              </label>
              {current.kind === "statistic" && (
                <>
                  <label className="field">
                    Statistic
                    <select
                      aria-label="Custom column statistic"
                      value={current.statistic}
                      onChange={(e) => edit({ statistic: e.target.value })}
                    >
                      {Object.entries(statistics).map(([key, label]) => (
                        <option key={key} value={key}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </label>
                  {!["count", "percent_parent", "percent_total"].includes(
                    current.statistic,
                  ) && (
                    <label className="field">
                      Parameter
                      <select
                        aria-label="Custom column parameter"
                        value={current.channel}
                        onChange={(e) =>
                          edit({
                            channel: e.target.value,
                            channel_overrides: {},
                          })
                        }
                      >
                        <option value="">Choose parameter</option>
                        {channels.map((c) => (
                          <option key={c.name} value={c.name}>
                            {channelLabel(c)}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  {current.statistic === "percentile" && (
                    <label className="field">
                      Percentile
                      <input
                        aria-label="Custom percentile"
                        type="number"
                        min={0}
                        max={100}
                        value={current.percentile}
                        onChange={(e) =>
                          edit({ percentile: Number(e.target.value) })
                        }
                      />
                    </label>
                  )}
                  <label className="field">
                    Population
                    <select
                      aria-label="Custom column population"
                      value={
                        current.population_path === null
                          ? "row"
                          : JSON.stringify(current.population_path)
                      }
                      onChange={(e) =>
                        edit({
                          population_path:
                            e.target.value === "row"
                              ? null
                              : JSON.parse(e.target.value),
                        })
                      }
                    >
                      <option value="row">Row population</option>
                      <option value="[]">All events</option>
                      {current.population_path?.length &&
                      !paths.some(
                        (p) =>
                          JSON.stringify(p) ===
                          JSON.stringify(current.population_path),
                      ) ? (
                        <option value={JSON.stringify(current.population_path)}>
                          Unavailable: {current.population_path.join(" / ")}
                        </option>
                      ) : null}
                      {paths.map((p) => (
                        <option
                          key={JSON.stringify(p)}
                          value={JSON.stringify(p)}
                        >
                          {p.join(" / ")}
                        </option>
                      ))}
                    </select>
                  </label>
                  <details>
                    <summary>Map populations per sample</summary>
                    <p className="muted small">
                      Override missing or ambiguous paths with a specific
                      population.
                    </p>
                    {workspace.samples.map((s) => (
                      <label className="field" key={s.id}>
                        {s.name}
                        <select
                          aria-label={`Table population mapping ${s.name}`}
                          value={
                            s.id in current.population_overrides
                              ? (current.population_overrides[s.id] ?? "root")
                              : "path"
                          }
                          onChange={(e) => {
                            const mapping = { ...current.population_overrides };
                            if (e.target.value === "path") delete mapping[s.id];
                            else
                              mapping[s.id] =
                                e.target.value === "root"
                                  ? null
                                  : e.target.value;
                            edit({ population_overrides: mapping });
                          }}
                        >
                          <option value="path">Use population path</option>
                          <option value="root">All events</option>
                          {workspace.gates
                            .filter((g) => g.sample_id === s.id)
                            .map((g) => (
                              <option key={g.id} value={g.id}>
                                {g.name}
                              </option>
                            ))}
                        </select>
                      </label>
                    ))}
                  </details>
                </>
              )}
              {current.kind === "metadata" && (
                <>
                  <label className="field">
                    Keyword source
                    <select
                      aria-label="Custom keyword source"
                      value={current.metadata_source}
                      onChange={(e) =>
                        edit({
                          metadata_source: e.target
                            .value as TableColumn["metadata_source"],
                        })
                      }
                    >
                      <option value="tags">Sample annotations</option>
                      <option value="metadata">Acquisition metadata</option>
                      <option value="keywords">
                        Annotations, then acquisition keywords
                      </option>
                    </select>
                  </label>
                  <label className="field">
                    Keyword
                    <input
                      aria-label="Custom keyword"
                      list="table-keywords"
                      value={current.metadata_key}
                      onChange={(e) => edit({ metadata_key: e.target.value })}
                    />
                  </label>
                  <datalist id="table-keywords">
                    {Array.from(
                      new Set(
                        workspace.samples.flatMap((s) =>
                          current.metadata_source === "keywords"
                            ? [
                                ...Object.keys(s.tags),
                                ...Object.keys(s.metadata),
                              ]
                            : Object.keys(s[current.metadata_source]),
                        ),
                      ),
                    ).map((k) => (
                      <option key={k} value={k} />
                    ))}
                  </datalist>
                  <label className="checkbox">
                    <input
                      aria-label="Numeric table keyword"
                      type="checkbox"
                      checked={current.metadata_numeric}
                      onChange={(e) =>
                        edit({ metadata_numeric: e.target.checked })
                      }
                    />
                    Interpret as numeric
                  </label>
                </>
              )}
              {current.kind === "formula" && (
                <>
                  <label className="field">
                    Expression
                    <textarea
                      aria-label="Custom table formula"
                      rows={4}
                      value={current.expression}
                      maxLength={2048}
                      placeholder={'col("Median") / mean(col("Median"))'}
                      onChange={(e) => edit({ expression: e.target.value })}
                    />
                  </label>
                  <select
                    aria-label="Insert table column reference"
                    value=""
                    onChange={(e) => {
                      const c = draft.columns.find(
                        (c) => c.id === e.target.value,
                      );
                      if (c)
                        edit({
                          expression:
                            current.expression +
                            `col(${JSON.stringify(c.name)})`,
                        });
                    }}
                  >
                    <option value="">Insert column reference…</option>
                    {draft.columns
                      .filter((c) => c.id !== selected)
                      .map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.name}
                        </option>
                      ))}
                  </select>
                  <details>
                    <summary>Formula functions</summary>
                    <p className="muted small">
                      Arithmetic, comparisons, abs, sqrt, log, log10, exp,
                      asinh, min, max, clip, ifelse and coalesce.
                    </p>
                    <p className="muted small">
                      mean, median, sum, sd and n aggregate the selected,
                      filtered table rows. control("Median", "Sample name")
                      selects a control row with the matching population path.
                      Column references retain their saved bindings after
                      rename.
                    </p>
                  </details>
                </>
              )}
              {current.kind === "population_comparison" && (
                <>
                  <label className="field">
                    Saved comparison
                    <select
                      aria-label="Table population comparison"
                      value={current.result_id ?? ""}
                      onChange={(e) => {
                        const result = workspace.comparison_results?.find(
                          (r) => r.id === e.target.value,
                        );
                        edit({
                          result_id: result?.id ?? null,
                          comparison_parameter_id:
                            result?.request.parameters[0]?.id ?? null,
                          population_overrides: uniqueComparisonPopulations(
                            result?.request.inputs ?? [],
                          ),
                        });
                      }}
                    >
                      <option value="">Choose comparison</option>
                      {workspace.comparison_results?.map((r) => (
                        <option key={r.id} value={r.id}>
                          {r.request.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="field">
                    Comparison parameter
                    <select
                      aria-label="Table comparison parameter"
                      value={current.comparison_parameter_id ?? ""}
                      onChange={(e) =>
                        edit({
                          comparison_parameter_id: e.target.value || null,
                          biology_metric: e.target.value ? "ks_distance" : "tx",
                        })
                      }
                    >
                      <option value="">Joint distribution</option>
                      {workspace.comparison_results
                        ?.find((r) => r.id === current.result_id)
                        ?.request.parameters.map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.label || p.channel}
                          </option>
                        ))}
                    </select>
                  </label>
                  <label className="field">
                    Comparison statistic
                    <select
                      aria-label="Table comparison statistic"
                      value={current.biology_metric}
                      onChange={(e) => edit({ biology_metric: e.target.value })}
                    >
                      {[
                        ...[
                          "ks_distance",
                          "ks_p_value",
                          "ks_at_coordinate",
                          "overton_cumulative_percent",
                          "enhanced_dmax_percent",
                          "ens_percent",
                          "peak_normalized_excess_percent",
                        ].filter(() => !!current.comparison_parameter_id),
                        "chi_squared",
                        "tx",
                        "finite_count",
                        "selected_count",
                        "control_finite_count",
                        "control_selected_count",
                        "shared_events",
                      ].map((metric) => (
                        <option key={metric} value={metric}>
                          {metric.replaceAll("_", " ")}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="checkbox">
                    <input
                      type="checkbox"
                      checked={current.follow_replacement}
                      onChange={(e) =>
                        edit({ follow_replacement: e.target.checked })
                      }
                    />{" "}
                    Follow reviewed refits
                  </label>
                </>
              )}
              {current.kind === "biology" && (
                <>
                  <label className="field">
                    Platform
                    <select
                      aria-label="Table biology platform"
                      value={current.platform}
                      onChange={(e) =>
                        edit({
                          platform: e.target.value as TableColumn["platform"],
                          result_id: null,
                          biology_metric:
                            e.target.value === "cell-cycle"
                              ? "fraction"
                              : e.target.value === "kinetics"
                                ? "peak"
                                : "precursor_frequency",
                          kinetics_range_id: null,
                        })
                      }
                    >
                      <option value="proliferation">Proliferation</option>
                      <option value="cell-cycle">Cell cycle</option>
                      <option value="kinetics">Kinetics</option>
                    </select>
                  </label>
                  <label className="field">
                    Saved model
                    <select
                      aria-label="Table biological model"
                      value={current.result_id ?? ""}
                      onChange={(e) =>
                        edit({
                          result_id: e.target.value || null,
                          kinetics_range_id:
                            workspace.kinetics_results?.find(
                              (r) => r.id === e.target.value,
                            )?.fits[0]?.ranges[0]?.id ?? null,
                        })
                      }
                    >
                      <option value="">Select model</option>
                      {biologyModels(workspace, current.platform).map((r) => (
                        <option key={r.id} value={r.id}>
                          {r.request.name} · {r.fits.length} samples ·{" "}
                          {new Date(r.created_at).toLocaleTimeString()}
                        </option>
                      ))}
                      {current.result_id &&
                        !biologyModels(workspace, current.platform).some(
                          (r) => r.id === current.result_id,
                        ) && (
                          <option value={current.result_id}>
                            Missing saved model
                          </option>
                        )}
                    </select>
                  </label>
                  <label className="field">
                    Model statistic
                    <select
                      aria-label="Table biology statistic"
                      value={current.biology_metric}
                      onChange={(e) => edit({ biology_metric: e.target.value })}
                    >
                      {Object.entries(
                        current.platform === "kinetics"
                          ? kineticsMetrics
                          : metrics,
                      )
                        .filter(([key]) =>
                          current.platform === "kinetics"
                            ? true
                            : current.platform === "proliferation"
                              ? !key.startsWith("g1_") && !key.startsWith("g2_")
                              : [
                                  "fraction",
                                  "model_count",
                                  "expected_count",
                                  "assigned_count",
                                  "fitted_count",
                                  "normalized_rmsd",
                                  "g1_mean",
                                  "g2_mean",
                                  "g1_cv",
                                  "g2_cv",
                                  "peak_ratio",
                                ].includes(key),
                        )
                        .map(([key, label]) => (
                          <option key={key} value={key}>
                            {label}
                          </option>
                        ))}
                    </select>
                  </label>
                  {[
                    "fraction",
                    "model_count",
                    "expected_count",
                    "assigned_count",
                  ].includes(current.biology_metric) && (
                    <label className="field">
                      {current.platform === "cell-cycle"
                        ? "Phase"
                        : "Generation"}
                      <select
                        aria-label="Table generation or phase"
                        value={current.generation}
                        onChange={(e) =>
                          edit({ generation: Number(e.target.value) })
                        }
                      >
                        {Array.from(
                          {
                            length: current.platform === "cell-cycle" ? 3 : 13,
                          },
                          (_, i) => (
                            <option key={i} value={i}>
                              {current.platform === "cell-cycle"
                                ? ["G1", "S", "G2/M"][i]
                                : `G${i}`}
                            </option>
                          ),
                        )}
                      </select>
                    </label>
                  )}
                  {current.platform === "kinetics" && (
                    <KineticsRangeField
                      workspace={workspace}
                      column={current}
                      change={edit}
                      label="Table kinetics time range"
                    />
                  )}
                  <label className="checkbox">
                    <input
                      type="checkbox"
                      aria-label="Follow biological model replacements"
                      checked={current.follow_replacement}
                      onChange={(e) =>
                        edit({ follow_replacement: e.target.checked })
                      }
                    />
                    Follow replacements
                  </label>
                </>
              )}
              {
                <label className="field">
                  Control value
                  <select
                    aria-label="Table control sample"
                    value={current.control_sample_id ?? ""}
                    onChange={(e) =>
                      edit({
                        control_sample_id: e.target.value || null,
                        control_unavailable: false,
                      })
                    }
                  >
                    <option value="">
                      {current.control_unavailable
                        ? "Source control unavailable"
                        : "Each row's sample"}
                    </option>
                    {current.control_sample_id &&
                      !workspace.samples.some(
                        (s) => s.id === current.control_sample_id,
                      ) && (
                        <option value={current.control_sample_id}>
                          Unavailable control sample
                        </option>
                      )}
                    {workspace.samples.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                  {current.control_unavailable && (
                    <button
                      className="button small"
                      onClick={() =>
                        edit({
                          control_unavailable: false,
                          control_sample_id: null,
                        })
                      }
                    >
                      Use each row's sample
                    </button>
                  )}
                </label>
              }
              {!!Object.keys(current.channel_overrides ?? {}).length && (
                <details>
                  <summary>Source parameter names</summary>
                  {Object.entries(current.channel_overrides ?? {}).map(
                    ([sampleId, name]) => (
                      <p className="form-note" key={sampleId}>
                        {workspace.samples.find(
                          (sample) => sample.id === sampleId,
                        )?.name ?? "Unavailable sample"}{" "}
                        · {name}
                      </p>
                    ),
                  )}
                </details>
              )}
              {!!Object.keys(current.compensation_overrides ?? {}).length && (
                <p className="form-note">
                  Source compensation retained for{" "}
                  {Object.keys(current.compensation_overrides ?? {}).length}{" "}
                  samples.
                  <button
                    className="button small"
                    onClick={() => edit({ compensation_overrides: {} })}
                  >
                    Use current sample compensation
                  </button>
                </p>
              )}
              {(current.kind === "biology" ||
                current.kind === "statistic" ||
                current.kind === "population_comparison") && (
                <label className="checkbox">
                  <input
                    aria-label="Include stale table model values"
                    type="checkbox"
                    checked={current.allow_stale}
                    onChange={(e) => edit({ allow_stale: e.target.checked })}
                  />
                  Include stale model values with status
                </label>
              )}
              <div className="table-checkbox-grid">
                <label className="checkbox">
                  <input
                    aria-label="Hide table column"
                    type="checkbox"
                    checked={current.hidden}
                    onChange={(e) => edit({ hidden: e.target.checked })}
                  />
                  Hide helper column
                </label>
                <label className="checkbox">
                  <input
                    aria-label="Table column heatmap"
                    type="checkbox"
                    checked={current.heatmap}
                    onChange={(e) => edit({ heatmap: e.target.checked })}
                  />
                  Heat map
                </label>
              </div>
              <label className="field">
                Display decimals
                <input
                  aria-label="Custom column decimals"
                  type="number"
                  min={0}
                  max={12}
                  value={current.decimals}
                  onChange={(e) => edit({ decimals: Number(e.target.value) })}
                />
              </label>
            </div>
          )}
        </aside>
        <section className="panel custom-table-result">
          <div className="custom-table-result-toolbar">
            <div className="segmented" aria-label="Custom table views">
              {(["data", "pivot", "comparisons"] as const).map((v) => (
                <button
                  key={v}
                  aria-pressed={v === view}
                  className={v === view ? "active" : ""}
                  onClick={() => setView(v)}
                >
                  {v === "data"
                    ? "Data"
                    : v === "pivot"
                      ? "Pivot"
                      : "Comparisons"}
                </button>
              ))}
            </div>
            <Tag color={query.error ? "amber" : ready ? "green" : "blue"}>
              {query.error
                ? "Definition needs attention"
                : ready
                  ? "Live"
                  : "Calculating"}
            </Tag>
          </div>
          {query.error && <ErrorState error={query.error} />}
          {result?.notices.map((message) => (
            <p className="notice warning" key={message}>
              {message}
            </p>
          ))}
          {view === "pivot" && (
            <div className="custom-pivot-settings">
              <label className="checkbox">
                <input
                  type="checkbox"
                  aria-label="Enable table pivot"
                  checked={!!draft.pivot}
                  onChange={(e) =>
                    set(
                      "pivot",
                      e.target.checked
                        ? {
                            rows: [],
                            columns: [],
                            measures: numericColumns
                              .slice(0, 1)
                              .map((c) => c.id),
                            aggregation: "mean",
                          }
                        : null,
                    )
                  }
                />
                Enable pivot
              </label>
              {draft.pivot && (
                <>
                  <div className="table-checkbox-grid">
                    <ColumnChoices
                      label="Pivot rows"
                      columns={draft.columns}
                      values={draft.pivot.rows}
                      onChange={(v) => updatePivot({ rows: v })}
                    />
                    <ColumnChoices
                      label="Pivot columns"
                      columns={draft.columns}
                      values={draft.pivot.columns}
                      onChange={(v) => updatePivot({ columns: v })}
                    />
                    <ColumnChoices
                      label="Pivot measures"
                      columns={numericColumns}
                      values={draft.pivot.measures}
                      onChange={(v) => updatePivot({ measures: v })}
                    />
                  </div>
                  <label className="field">
                    Aggregation
                    <select
                      aria-label="Table pivot aggregation"
                      value={draft.pivot.aggregation}
                      onChange={(e) =>
                        updatePivot({
                          aggregation: e.target
                            .value as TablePivot["aggregation"],
                        })
                      }
                    >
                      {[
                        "mean",
                        "median",
                        "sum",
                        "count",
                        "std",
                        "min",
                        "max",
                      ].map((v) => (
                        <option key={v} value={v}>
                          {v === "std" ? "Sample standard deviation" : v}
                        </option>
                      ))}
                    </select>
                  </label>
                </>
              )}
            </div>
          )}
          {view === "comparisons" && (
            <div className="custom-pivot-settings">
              <label className="checkbox">
                <input
                  type="checkbox"
                  aria-label="Enable table comparisons"
                  disabled={draft.row_mode !== "samples"}
                  checked={!!draft.comparison}
                  onChange={(e) =>
                    set(
                      "comparison",
                      e.target.checked
                        ? {
                            group_column:
                              draft.columns.find((c) => !numeric(c))?.id ??
                              draft.columns[0].id,
                            group_a: "",
                            group_b: "",
                            measures: numericColumns
                              .slice(0, 1)
                              .map((c) => c.id),
                            method: "welch",
                            pair_column: null,
                            adjustment: "holm",
                            confidence_level: 0.95,
                          }
                        : null,
                    )
                  }
                />
                Compare sample groups
              </label>
              <p className="muted small">
                Tests use one observation per selected sample. Choose
                annotations describing independent experimental units; matched
                designs require a unique pairing value in each group.
              </p>
              {draft.comparison && (
                <>
                  <div className="custom-comparison-fields">
                    <label className="field">
                      Grouping column
                      <select
                        aria-label="Comparison grouping column"
                        value={draft.comparison.group_column}
                        onChange={(e) =>
                          updateComparison({ group_column: e.target.value })
                        }
                      >
                        {draft.columns.map((c) => (
                          <option key={c.id} value={c.id}>
                            {c.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="field">
                      Group A
                      <input
                        aria-label="Comparison group A"
                        value={draft.comparison.group_a}
                        onChange={(e) =>
                          updateComparison({ group_a: e.target.value })
                        }
                      />
                    </label>
                    <label className="field">
                      Group B
                      <input
                        aria-label="Comparison group B"
                        value={draft.comparison.group_b}
                        onChange={(e) =>
                          updateComparison({ group_b: e.target.value })
                        }
                      />
                    </label>
                    <label className="field">
                      Test
                      <select
                        aria-label="Table comparison method"
                        value={draft.comparison.method}
                        onChange={(e) =>
                          updateComparison({
                            method: e.target.value as TableComparison["method"],
                          })
                        }
                      >
                        <option value="welch">Welch t-test</option>
                        <option value="mann_whitney">Mann–Whitney U</option>
                        <option value="paired_t">Paired t-test</option>
                        <option value="wilcoxon">Wilcoxon signed-rank</option>
                      </select>
                    </label>
                    {["paired_t", "wilcoxon"].includes(
                      draft.comparison.method,
                    ) && (
                      <label className="field">
                        Match pairs by
                        <select
                          aria-label="Comparison pairing column"
                          value={draft.comparison.pair_column ?? ""}
                          onChange={(e) =>
                            updateComparison({
                              pair_column: e.target.value || null,
                            })
                          }
                        >
                          <option value="">Choose pairing column</option>
                          {draft.columns.map((c) => (
                            <option key={c.id} value={c.id}>
                              {c.name}
                            </option>
                          ))}
                        </select>
                      </label>
                    )}
                    <label className="field">
                      Multiple comparisons
                      <select
                        aria-label="Comparison p-value adjustment"
                        value={draft.comparison.adjustment}
                        onChange={(e) =>
                          updateComparison({
                            adjustment: e.target
                              .value as TableComparison["adjustment"],
                          })
                        }
                      >
                        <option value="holm">Holm</option>
                        <option value="benjamini_hochberg">
                          Benjamini–Hochberg
                        </option>
                        <option value="none">Unadjusted</option>
                      </select>
                    </label>
                    <label className="field">
                      Confidence level
                      <input
                        aria-label="Comparison confidence level"
                        type="number"
                        min={0.51}
                        max={0.999}
                        step={0.01}
                        value={draft.comparison.confidence_level}
                        onChange={(e) =>
                          updateComparison({
                            confidence_level: Number(e.target.value),
                          })
                        }
                      />
                    </label>
                  </div>
                  <ColumnChoices
                    label="Comparison measures"
                    columns={numericColumns}
                    values={draft.comparison.measures}
                    onChange={(v) => updateComparison({ measures: v })}
                  />
                </>
              )}
            </div>
          )}
          {!result && !query.error ? (
            <Loading label="Calculating table" />
          ) : view === "data" ? (
            <>
              <div className="custom-table-sort">
                <label className="field">
                  Sort by
                  <select
                    aria-label="Custom table sort"
                    value={draft.sort_by}
                    onChange={(e) => set("sort_by", e.target.value)}
                  >
                    <option value="sample">Sample</option>
                    <option value="input">Input order</option>
                    <option value="population">Population</option>
                    {draft.columns.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="checkbox">
                  <input
                    aria-label="Descending custom table sort"
                    type="checkbox"
                    checked={draft.descending}
                    onChange={(e) => set("descending", e.target.checked)}
                  />
                  Descending
                </label>
              </div>
              {result?.rows.length ? (
                <div
                  className={`table-scroll custom-data-scroll ${!ready ? "updating" : ""}`}
                  aria-busy={!ready}
                >
                  <table
                    className="data-table"
                    aria-label="Custom statistics table"
                  >
                    <thead>
                      <tr>
                        <th>Sample</th>
                        {draft.row_mode === "populations" && (
                          <th>Population</th>
                        )}
                        {visible.map((c) => (
                          <th key={c.id}>{c.name}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {result.rows.map((row) => (
                        <tr key={row.id}>
                          <td>
                            <button
                              className="text-button"
                              onClick={() => onOpen(row.sample_id, row.gate_id)}
                            >
                              {row.sample}
                            </button>
                          </td>
                          {draft.row_mode === "populations" && (
                            <td>{row.population}</td>
                          )}
                          {visible.map((c) => {
                            const value = row.values[c.id],
                              range = c.heatmap ? heatRanges[c.id] : null;
                            const scale = range
                              ? Math.max(
                                  Math.abs(range[0]),
                                  Math.abs(range[1]),
                                  1e-300,
                                )
                              : 1;
                            const alpha =
                              range &&
                              typeof value === "number" &&
                              range[1] > range[0]
                                ? 0.08 +
                                  0.32 *
                                    ((value / scale - range[0] / scale) /
                                      (range[1] / scale - range[0] / scale))
                                : 0;
                            return (
                              <td
                                key={c.id}
                                className={`mono ${row.status[c.id] ? "custom-cell-status" : ""}`}
                                title={row.status[c.id]}
                                style={
                                  alpha
                                    ? {
                                        backgroundColor: `rgba(44, 194, 173, ${alpha})`,
                                      }
                                    : undefined
                                }
                              >
                                {valueText(value, c.decimals)}
                                {row.status[c.id] && (
                                  <span
                                    className="cell-status-marker"
                                    tabIndex={0}
                                    role="note"
                                    title={row.status[c.id]}
                                    aria-label={row.status[c.id]}
                                  >
                                    *
                                  </span>
                                )}
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                !query.error && (
                  <Empty
                    icon={<FileSpreadsheet size={30} />}
                    title="No matching rows"
                    text="Adjust the sample scope or row filter."
                  />
                )
              )}
              <div className="table-bottom">
                <span>
                  {result?.total_rows ?? 0} rows · full-event statistics · *
                  cell status available
                </span>
                <div className="button-group">
                  <button
                    className="button small ghost"
                    aria-label="Previous custom table page"
                    disabled={offset === 0 || !ready}
                    onClick={() => setOffset(Math.max(0, offset - 100))}
                  >
                    <ChevronLeft size={15} />
                  </button>
                  <span>
                    {result?.total_rows ? offset + 1 : 0}–
                    {Math.min(offset + 100, result?.total_rows ?? 0)}
                  </span>
                  <button
                    className="button small ghost"
                    aria-label="Next custom table page"
                    disabled={
                      !ready || offset + 100 >= (result?.total_rows ?? 0)
                    }
                    onClick={() => setOffset(offset + 100)}
                  >
                    <ChevronRight size={15} />
                  </button>
                </div>
              </div>
            </>
          ) : view === "pivot" && result?.pivot ? (
            <div className="table-scroll">
              <table className="data-table" aria-label="Custom pivot table">
                <thead>
                  <tr>
                    {result.pivot.row_columns.map((c) => (
                      <th key={c.id}>{c.name}</th>
                    ))}
                    {result.pivot.columns.map((c) => (
                      <th key={c.id}>{c.name}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.pivot.rows.map((row, i) => (
                    <tr key={i}>
                      {row.group.map((v, j) => (
                        <td key={j}>{valueText(v)}</td>
                      ))}
                      {result.pivot!.columns.map((c) => (
                        <td
                          key={c.id}
                          className="mono"
                          title={`${row.counts[c.id]} finite sample values`}
                        >
                          {valueText(row.values[c.id])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : view === "comparisons" && result?.comparisons.length ? (
            <div className="table-scroll">
              <table
                className="data-table"
                aria-label="Sample comparison results"
              >
                <thead>
                  <tr>
                    <th>Measure</th>
                    <th>n A / B</th>
                    <th>Mean A − B</th>
                    <th>Confidence interval</th>
                    <th>p</th>
                    <th>Adjusted p</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {result.comparisons.map((row) => (
                    <tr key={row.column_id}>
                      <td>{row.column}</td>
                      <td>
                        {row.n_a} / {row.n_b}
                      </td>
                      <td className="mono">{valueText(row.mean_difference)}</td>
                      <td className="mono">
                        {row.confidence_interval
                          ?.map((v) => valueText(v))
                          .join(" to ") ?? "—"}
                      </td>
                      <td className="mono">{probabilityText(row.p_value)}</td>
                      <td className="mono">
                        {probabilityText(row.adjusted_p_value)}
                      </td>
                      <td>
                        {row.issue ??
                          `${row.method} · ${row.excluded_a + row.excluded_b} excluded`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          <p className="subtle-note">
            Exports include every selected row. XLSX retains numeric values, a
            cell-status sheet, pivot/comparison results and the saved
            definition. Display rounding does not change calculations.
          </p>
        </section>
      </div>
    </div>
  );
}
