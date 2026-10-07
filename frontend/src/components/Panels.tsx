import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ChevronDown,
  Download,
  Edit3,
  FileSpreadsheet,
  FlaskConical,
  Plus,
  Save,
  Search,
  Trash2,
  ArrowRight,
} from "lucide-react";
import { api, download, params } from "../api";
import type { Gate, Sample, TableRow, Workspace } from "../types";
import { channelLabel, formatNumber, id, percentage } from "../types";
import { Empty, ErrorState, Loading, Tag } from "./Common";
import type { Commit } from "./Editors";
import { TableBuilder } from "./TableBuilder";

export function SamplesPanel({
  workspace,
  samples,
  selected,
  setSelected,
  onAnalyze,
  onEdit,
  onRemove,
  onGroup,
  onConcatenate,
  onHarmonize,
  onOrigins,
  onImport,
}: {
  workspace: Workspace;
  samples: Sample[];
  selected: string[];
  setSelected: (ids: string[]) => void;
  onAnalyze: (sample: Sample) => void;
  onEdit: (sample: Sample) => void;
  onRemove: (sample: Sample) => void;
  onGroup: () => void;
  onConcatenate: () => void;
  onHarmonize: () => void;
  onOrigins: (sample: Sample) => void;
  onImport: () => void;
}) {
  return (
    <div className="page-panel">
      <div className="page-heading">
        <div>
          <span className="eyebrow">EXPERIMENT MANAGEMENT</span>
          <h1>
            Samples <span className="heading-count">{samples.length}</span>
          </h1>
          <p>Your acquisition data, organized for analysis.</p>
        </div>
        <button className="button primary" onClick={onImport}>
          <Plus size={16} />
          Import samples
        </button>
      </div>
      <div className="panel">
        <div className="panel-heading">
          <span>
            {selected.length
              ? `${selected.length} samples selected`
              : `${formatNumber(
                  samples.reduce((sum, s) => sum + s.event_count, 0),
                  0,
                )} total events`}
          </span>
          <div className="row-actions">
            <button
              className="button small"
              onClick={onHarmonize}
              disabled={!selected.length || selected.length > 128}
            >
              Harmonize panel
            </button>
            <button
              className="button small"
              onClick={onConcatenate}
              disabled={!selected.length}
            >
              Concatenate populations
            </button>
            <button
              className="button small"
              onClick={onGroup}
              disabled={!selected.length}
            >
              <Plus size={14} />
              Create group
            </button>
          </div>
        </div>
        {!samples.length ? (
          <Empty
            icon={<FlaskConical size={32} />}
            title="Your experiment starts here"
            text="Import FCS files from your cytometer or a numeric CSV event matrix."
          >
            <button className="button" onClick={onImport}>
              Choose files
            </button>
          </Empty>
        ) : (
          <div className="table-scroll">
            <table className="data-table sample-table">
              <thead>
                <tr>
                  <th className="check-cell">
                    <input
                      aria-label="Select all visible samples"
                      type="checkbox"
                      checked={samples.every((s) => selected.includes(s.id))}
                      onChange={(e) =>
                        setSelected(
                          e.target.checked
                            ? [
                                ...new Set([
                                  ...selected,
                                  ...samples.map((s) => s.id),
                                ]),
                              ]
                            : selected.filter(
                                (id) => !samples.some((s) => s.id === id),
                              ),
                        )
                      }
                    />
                  </th>
                  <th>Sample</th>
                  <th>Events</th>
                  <th>Channels</th>
                  <th>Donor / Condition</th>
                  <th>Compensation</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {samples.map((sample) => (
                  <tr
                    key={sample.id}
                    className={
                      selected.includes(sample.id) ? "selected-row" : ""
                    }
                  >
                    <td>
                      <input
                        aria-label={`Select ${sample.name}`}
                        type="checkbox"
                        checked={selected.includes(sample.id)}
                        onChange={(e) =>
                          setSelected(
                            e.target.checked
                              ? [...selected, sample.id]
                              : selected.filter((v) => v !== sample.id),
                          )
                        }
                      />
                    </td>
                    <td>
                      <button
                        className="sample-name"
                        onClick={() => onAnalyze(sample)}
                      >
                        <span className="sample-glyph">
                          <FlaskConical size={15} />
                        </span>
                        {sample.name}
                      </button>
                      <small>{sample.source}</small>
                    </td>
                    <td className="mono">
                      {formatNumber(sample.event_count, 0)}
                    </td>
                    <td>{sample.channels.length}</td>
                    <td>
                      <div className="inline-tags">
                        {Object.entries(sample.tags)
                          .slice(0, 2)
                          .map(([k, v]) => (
                            <Tag key={k}>{v}</Tag>
                          ))}
                      </div>
                    </td>
                    <td>
                      {sample.compensation_id ? (
                        <span className="comp-badge">
                          <span className="status-dot" />
                          {
                            workspace.compensations.find(
                              (c) => c.id === sample.compensation_id,
                            )?.name
                          }
                        </span>
                      ) : (
                        <span className="dim">Uncompensated</span>
                      )}
                    </td>
                    <td className="row-actions">
                      {sample.concatenation && (
                        <button
                          className="button small"
                          aria-label={`Origins for ${sample.name}`}
                          onClick={() => onOrigins(sample)}
                        >
                          Origins
                        </button>
                      )}
                      <button
                        className="icon-button"
                        aria-label={`Edit ${sample.name}`}
                        onClick={() => onEdit(sample)}
                      >
                        <Edit3 size={15} />
                      </button>
                      <button
                        className="icon-button danger-hover"
                        aria-label={`Remove ${sample.name}`}
                        onClick={() => onRemove(sample)}
                      >
                        <Trash2 size={15} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

export function StatisticsPanel(props: {
  workspace: Workspace;
  groupId: string | null;
  commit: Commit;
  busy: boolean;
  onOpen: (sampleId: string, gateId: string | null) => void;
}) {
  const [custom, setCustom] = useState(false);
  return (
    <>
      <div
        className="statistics-view-switch segmented"
        aria-label="Statistics workbench views"
      >
        <button
          className={!custom ? "active" : ""}
          aria-pressed={!custom}
          onClick={() => setCustom(false)}
        >
          Population overview
        </button>
        <button
          className={custom ? "active" : ""}
          aria-pressed={custom}
          onClick={() => setCustom(true)}
        >
          Custom tables
        </button>
      </div>
      {custom ? (
        <TableBuilder {...props} />
      ) : (
        <PopulationStatisticsPanel {...props} />
      )}
    </>
  );
}

function PopulationStatisticsPanel({
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
  const [channel, setChannel] = useState(""),
    [filter, setFilter] = useState(""),
    [name, setName] = useState("Population statistics"),
    [definitionId, setDefinitionId] = useState<string | null>(null),
    [error, setError] = useState("");
  const query = useQuery({
    queryKey: ["table", workspace.id, workspace.revision, groupId, channel],
    queryFn: ({ signal }) =>
      api<TableRow[]>(
        `/workspaces/${workspace.id}/statistics?${params({ group_id: groupId, channel })}`,
        { signal },
      ),
  });
  const rows = useMemo(
    () =>
      (query.data ?? []).filter((row) =>
        `${row.sample} ${row.population}`
          .toLowerCase()
          .includes(filter.toLowerCase()),
      ),
    [query.data, filter],
  );
  const channels = Array.from(
    new Map(
      workspace.samples.flatMap((s) => s.channels).map((c) => [c.name, c]),
    ).values(),
  );
  const numericColumns = [
    "count",
    "percent_parent",
    "percent_total",
    ...(channel ? ["median", "mean", "robust_cv", "p5", "p95"] : []),
  ];
  const labels: Record<string, string> = {
    count: "Events",
    percent_parent: "% Parent",
    percent_total: "% Total",
    median: "Median",
    mean: "Mean",
    robust_cv: "Robust CV",
    p5: "5th percentile",
    p95: "95th percentile",
  };
  return (
    <div className="page-panel">
      <div className="page-heading">
        <div>
          <span className="eyebrow">LIVE POPULATION STATISTICS</span>
          <h1>Table builder</h1>
          <p>
            Exact population counts and channel statistics across your
            experiment.
          </p>
        </div>
        <button
          className="button primary"
          disabled={!rows.length}
          onClick={() =>
            download(
              `/workspaces/${workspace.id}/export/statistics.csv?${params({ group_id: groupId, channel })}`,
              "cytoforge-statistics.csv",
            ).catch((err) => setError(err.message))
          }
        >
          <Download size={16} />
          Export CSV
        </button>
      </div>
      <div className="panel">
        <div className="table-toolbar">
          <label className="search-field">
            <Search size={15} />
            <input
              aria-label="Filter statistics"
              placeholder="Filter samples or populations…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          </label>
          <select
            aria-label="Statistics channel"
            value={channel}
            onChange={(e) => setChannel(e.target.value)}
          >
            <option value="">Counts & frequencies</option>
            {channels.map((c) => (
              <option key={c.name} value={c.name}>
                {channelLabel(c)}
              </option>
            ))}
          </select>
          <select
            aria-label="Saved table definition"
            value={definitionId ?? ""}
            onChange={(e) => {
              const d = workspace.tables.find((t) => t.id === e.target.value);
              if (d) {
                setDefinitionId(String(d.id));
                setName(String(d.name));
                setChannel(String(d.channel ?? ""));
                setFilter(String(d.filter ?? ""));
              } else setDefinitionId(null);
            }}
          >
            <option value="">Saved tables</option>
            {workspace.tables.map((d) => (
              <option key={String(d.id)} value={String(d.id)}>
                {String(d.name)}
              </option>
            ))}
          </select>
        </div>
        {error && <p className="form-error">{error}</p>}
        {query.isPending ? (
          <Loading label="Calculating experiment statistics" />
        ) : query.error ? (
          <ErrorState error={query.error} onRetry={() => query.refetch()} />
        ) : !rows.length ? (
          <Empty
            icon={<FileSpreadsheet size={32} />}
            title="No populations to display"
            text="Import samples or adjust the population filter."
          />
        ) : (
          <div className="table-scroll stats-scroll">
            <table className="data-table stats-table">
              <thead>
                <tr>
                  <th>Sample</th>
                  <th>Population</th>
                  {numericColumns.map((k) => (
                    <th key={k}>{labels[k]}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, i) => (
                  <tr
                    key={i}
                    onDoubleClick={() =>
                      onOpen(
                        String(row.sample_id),
                        row.gate_id ? String(row.gate_id) : null,
                      )
                    }
                  >
                    <td>
                      <button
                        className="text-button"
                        onClick={() =>
                          onOpen(
                            String(row.sample_id),
                            row.gate_id ? String(row.gate_id) : null,
                          )
                        }
                      >
                        {row.sample}
                      </button>
                    </td>
                    <td className="population-path">
                      {String(row.population)
                        .split(" / ")
                        .map((name, index, list) => (
                          <span key={index}>
                            {name}
                            {index < list.length - 1 && (
                              <ChevronDown size={11} className="path-chevron" />
                            )}
                          </span>
                        ))}
                    </td>
                    {numericColumns.map((k) => (
                      <td className="mono" key={k}>
                        {k.startsWith("percent") || k === "robust_cv"
                          ? percentage(row[k] as number)
                          : formatNumber(
                              row[k] as number,
                              k === "count" ? 0 : 1,
                            )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="table-bottom">
          <span>
            {rows.length} rows · live calculations{channel && ` · ${channel}`}
            <span className="dim"> · undefined statistics shown as —</span>
          </span>
          <div>
            <input
              aria-label="Table definition name"
              className="compact-input"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <button
              className="button small"
              disabled={busy || !name.trim()}
              onClick={() =>
                commit(
                  "/tables/save",
                  {
                    definition: {
                      id: definitionId ?? id(),
                      name: name.trim(),
                      channel,
                      filter,
                    },
                  },
                  "Table definition saved",
                )
                  .then((doc) => setDefinitionId(String(doc.tables.at(-1)!.id)))
                  .catch((err) => setError(err.message))
              }
            >
              <Save size={14} />
              Save table
            </button>
          </div>
        </div>
      </div>
      <div className="subtle-note">
        Channel statistics use compensated, untransformed intensities. Geometric
        statistics are undefined when a population contains zero or negative
        values.
      </div>
    </div>
  );
}

export function GateInspector({
  gate,
  count,
  sample,
  workspace,
  onEdit,
  onDelete,
  onBoolean,
  onApply,
  onDerived,
  onCapture,
  pooled = false,
  pooledInfo,
}: {
  gate: Gate | null;
  count:
    | {
        count: number | null;
        percent_parent: number | null;
        percent_total: number | null;
      }
    | undefined;
  sample: Sample;
  workspace: Workspace;
  onEdit: () => void;
  onDelete: () => void;
  onBoolean: () => void;
  onApply: () => void;
  onDerived: () => void;
  onCapture?: () => void;
  pooled?: boolean;
  pooledInfo?: import("../types").PooledGroup;
}) {
  const parents: Gate[] = [];
  const total = pooled ? pooledInfo?.total_events : sample.event_count;
  let parent = gate;
  while (parent) {
    parents.unshift(parent);
    parent = workspace.gates.find((g) => g.id === parent?.parent_id) ?? null;
  }
  return (
    <aside className="inspector">
      <div className="inspector-heading">
        <span className="section-label">POPULATION INSPECTOR</span>
        {gate && (
          <button
            className="icon-button"
            aria-label="Edit selected gate"
            onClick={onEdit}
          >
            <Edit3 size={15} />
          </button>
        )}
      </div>
      <span
        className="population-color"
        style={{ backgroundColor: gate?.color ?? "#38d9ba" }}
      />
      <h2>{gate?.name ?? "All events"}</h2>
      <p>
        {gate
          ? gate.partition
            ? `Linked ${gate.partition.kind} population`
            : gate.kind === "membership"
              ? "Captured population"
              : `${gate.kind.charAt(0).toUpperCase() + gate.kind.slice(1)} gate`
          : "Root population"}{" "}
        · {pooled ? (pooledInfo?.group_name ?? "Pooled group") : sample.name}
      </p>
      <div className="inspector-count">
        <strong>
          {formatNumber(count?.count ?? (!gate ? total : undefined), 0)}
        </strong>
        <span>events in population</span>
      </div>
      <div className="inspector-frequencies">
        <div>
          <span>Of parent</span>
          <strong>
            {percentage(
              count?.percent_parent ?? (!gate && total ? 100 : undefined),
            )}
          </strong>
        </div>
        <div>
          <span>Of total</span>
          <strong>
            {percentage(
              count?.percent_total ?? (!gate && total ? 100 : undefined),
            )}
          </strong>
        </div>
      </div>
      <div className="inspector-section">
        <span className="section-label">GATING PATH</span>
        <div className="gating-path">
          <span>All events</span>
          {parents.map((g) => (
            <span key={g.id}>
              <ChevronDown size={12} />
              {g.name}
            </span>
          ))}
        </div>
      </div>
      {!!gate?.dimensions?.length && (
        <div className="inspector-section">
          <span className="section-label">GATE COORDINATES</span>
          <dl>
            {gate.dimensions.map((dim, index) => (
              <div key={index}>
                <dt>{dim.ratio_channels?.join(" / ") ?? dim.channel}</dt>
                <dd>
                  {dim.transform.kind}
                  <br />
                  {dim.compensation_ref === "sample"
                    ? "Sample compensation"
                    : dim.compensation_ref === "uncompensated"
                      ? "Uncompensated"
                      : dim.compensation_ref === "FCS"
                        ? "Embedded FCS"
                        : workspace.compensations.find(
                            (m) => m.id === dim.compensation_ref,
                          )?.name}
                </dd>
              </div>
            ))}
          </dl>
          {gate.dimensions.length > 2 && (
            <p className="form-note">
              All {gate.dimensions.length} dimensions define this population. A
              two-dimensional plot shows only the selected axes.
            </p>
          )}
        </div>
      )}
      <div className="inspector-section">
        <span className="section-label">
          {pooled ? "REPRESENTATIVE SAMPLE" : "SAMPLE METADATA"}
        </span>
        <dl>
          {Object.entries(sample.tags).map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd>{value}</dd>
            </div>
          ))}
          <div>
            <dt>Channels</dt>
            <dd>{sample.channels.length}</dd>
          </div>
          <div>
            <dt>Compensation</dt>
            <dd>{sample.compensation_id ? "Applied" : "None"}</dd>
          </div>
        </dl>
      </div>
      <div className="inspector-actions">
        <button
          className="button full-width"
          onClick={onCapture}
          disabled={!onCapture}
        >
          <Plus size={14} />
          Capture population
        </button>
        <button className="button full-width" onClick={onDerived}>
          <Plus size={14} />
          Derived parameter
        </button>
        <button className="button full-width" onClick={onBoolean}>
          <Plus size={14} />
          Boolean population
        </button>
        <button className="button full-width" onClick={onApply}>
          <ArrowRight size={14} />
          Apply tree to samples
        </button>
        {gate && (
          <button className="text-button danger" onClick={onDelete}>
            <Trash2 size={14} />
            Delete population
          </button>
        )}
      </div>
      <div className="inspector-source">
        <span className="status-dot" />
        {sample.source === "Synthetic demo"
          ? "Synthetic demonstration data"
          : "Local acquisition data"}
      </div>
    </aside>
  );
}
