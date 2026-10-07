import type { PlateDefinition, TableColumn, Workspace } from "../types";
import { channelLabel } from "../types";
import {
  biologyMetrics,
  biologyModels,
  kineticsMetrics,
  KineticsRangeField,
  newTableColumn,
  tableStatistics,
} from "./TableBuilder";

export function PlateMeasurements({
  workspace,
  plate,
  selected,
  select,
  change,
}: {
  workspace: Workspace;
  plate: PlateDefinition;
  selected: string;
  select: (value: string) => void;
  change: (value: PlateDefinition) => void;
}) {
  const current =
    plate.columns.find((c) => c.id === selected) ?? plate.columns[0];
  function edit(patch: Partial<TableColumn>) {
    change({
      ...plate,
      columns: plate.columns.map((c) =>
        c.id === current.id ? { ...c, ...patch } : c,
      ),
    });
  }
  const channels = Array.from(
    new Map(
      workspace.samples.flatMap((s) => s.channels).map((c) => [c.name, c]),
    ).values(),
  );
  const gates = new Map(workspace.gates.map((g) => [g.id, g]));
  function path(identifier: string): string[] {
    const gate = gates.get(identifier)!;
    return [...(gate.parent_id ? path(gate.parent_id) : []), gate.name];
  }
  const paths = Array.from(
    new Map(
      workspace.gates.map((g) => {
        const p = path(g.id);
        return [JSON.stringify(p), p] as const;
      }),
    ).values(),
  );
  const models = biologyModels(workspace, current.platform);
  return (
    <div className="plate-measurements">
      <p className="form-note">
        Up to ten live measurements. Each acquisition uses all events in its
        chosen population. Well aggregates give each acquisition equal weight.
      </p>
      <label className="field">
        Copy measurements from a saved table
        <select
          aria-label="Plate measurements from table"
          value=""
          onChange={(e) => {
            const table = workspace.tables.find((t) => t.id === e.target.value);
            if (table) {
              // Keep formula bindings intact; partial table copies would lose hidden helpers.
              change({
                ...plate,
                columns: structuredClone(table.columns),
                view: {
                  ...plate.view,
                  primary: null,
                  secondary: null,
                  domains: {},
                },
              });
              select(table.columns[0].id);
            }
          }}
        >
          <option value="">Choose a table…</option>
          {workspace.tables.map((t) => (
            <option
              key={t.id}
              value={t.id}
              disabled={!t.columns.length || t.columns.length > 10}
            >
              {t.name}
              {t.columns.length > 10 ? " (more than ten columns)" : ""}
            </option>
          ))}
        </select>
      </label>
      <div className="plate-column-list" aria-label="Plate measurement columns">
        {plate.columns.map((c) => (
          <button
            key={c.id}
            className={`button small ${c.id === current.id ? "active" : ""}`}
            onClick={() => select(c.id)}
          >
            {c.name || "Untitled"}
          </button>
        ))}
        <button
          className="button small"
          disabled={plate.columns.length >= 10}
          onClick={() => {
            const column = {
              ...newTableColumn(`Measure ${plate.columns.length + 1}`),
              population_path: [],
            };
            change({ ...plate, columns: [...plate.columns, column] });
            select(column.id);
          }}
        >
          Add measurement
        </button>
      </div>
      <div className="field-grid">
        <label className="field">
          Name
          <input
            aria-label="Plate measurement name"
            value={current.name}
            maxLength={160}
            onChange={(e) => edit({ name: e.target.value })}
          />
        </label>
        <label className="field">
          Measure type
          <select
            aria-label="Plate measurement type"
            value={current.kind}
            onChange={(e) =>
              edit({
                kind: e.target.value as TableColumn["kind"],
                formula_refs: {},
                expression:
                  current.expression ||
                  `col(${JSON.stringify(plate.columns.find((c) => c.id !== current.id)?.name ?? "Events")})`,
              })
            }
          >
            <option value="statistic">Population statistic</option>
            <option value="metadata">Keyword</option>
            <option value="formula">Formula</option>
            <option value="biology">Biological model</option>
          </select>
        </label>
      </div>
      {current.kind === "statistic" && (
        <>
          <label className="field">
            Statistic
            <select
              aria-label="Plate measurement statistic"
              value={current.statistic}
              onChange={(e) => edit({ statistic: e.target.value })}
            >
              {Object.entries(tableStatistics).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
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
                aria-label="Plate measurement parameter"
                value={current.channel}
                onChange={(e) => edit({ channel: e.target.value })}
              >
                <option value="">Select parameter</option>
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
                aria-label="Plate measurement percentile"
                type="number"
                min={0}
                max={100}
                value={current.percentile}
                onChange={(e) => edit({ percentile: Number(e.target.value) })}
              />
            </label>
          )}
          <label className="field">
            Population path
            <select
              aria-label="Plate measurement population"
              value={JSON.stringify(current.population_path ?? [])}
              onChange={(e) =>
                edit({ population_path: JSON.parse(e.target.value) })
              }
            >
              <option value="[]">All events</option>
              {current.population_path?.length &&
              !paths.some(
                (p) =>
                  JSON.stringify(p) === JSON.stringify(current.population_path),
              ) ? (
                <option value={JSON.stringify(current.population_path)}>
                  Missing: {current.population_path.join(" / ")}
                </option>
              ) : null}
              {paths.map((p) => (
                <option key={JSON.stringify(p)} value={JSON.stringify(p)}>
                  {p.join(" / ")}
                </option>
              ))}
            </select>
          </label>
          <details>
            <summary>Override populations per acquisition</summary>
            {workspace.samples.map((s) => (
              <label className="field" key={s.id}>
                {s.name}
                <select
                  aria-label={`Plate population mapping ${s.name}`}
                  value={
                    s.id in current.population_overrides
                      ? (current.population_overrides[s.id] ?? "root")
                      : "path"
                  }
                  onChange={(e) => {
                    const mappings = { ...current.population_overrides };
                    if (e.target.value === "path") delete mappings[s.id];
                    else
                      mappings[s.id] =
                        e.target.value === "root" ? null : e.target.value;
                    edit({ population_overrides: mappings });
                  }}
                >
                  <option value="path">Use path</option>
                  <option value="root">All events</option>
                  {workspace.gates
                    .filter((g) => g.sample_id === s.id)
                    .map((g) => (
                      <option key={g.id} value={g.id}>
                        {path(g.id).join(" / ")}
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
              aria-label="Plate measurement keyword source"
              value={current.metadata_source}
              onChange={(e) =>
                edit({
                  metadata_source: e.target
                    .value as TableColumn["metadata_source"],
                })
              }
            >
              <option value="tags">Applied sample annotations</option>
              <option value="metadata">Acquisition metadata</option>
              <option value="keywords">
                Annotations, then acquisition keywords
              </option>
            </select>
          </label>
          <label className="field">
            Keyword
            <input
              aria-label="Plate measurement keyword"
              value={current.metadata_key}
              maxLength={160}
              list="plate-measure-keywords"
              onChange={(e) => edit({ metadata_key: e.target.value })}
            />
          </label>
          <datalist id="plate-measure-keywords">
            {Array.from(
              new Set(
                workspace.samples.flatMap((s) =>
                  current.metadata_source === "keywords"
                    ? [...Object.keys(s.tags), ...Object.keys(s.metadata)]
                    : Object.keys(s[current.metadata_source]),
                ),
              ),
            ).map((k) => (
              <option key={k} value={k} />
            ))}
          </datalist>
          <label className="checkbox">
            <input
              aria-label="Numeric plate keyword"
              type="checkbox"
              checked={current.metadata_numeric}
              onChange={(e) => edit({ metadata_numeric: e.target.checked })}
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
              aria-label="Plate measurement formula"
              value={current.expression}
              rows={3}
              maxLength={2048}
              onChange={(e) => edit({ expression: e.target.value })}
            />
          </label>
          <p className="form-note">
            Use col("Name"), arithmetic, ifelse, coalesce, and
            mean/median/sum/sd/n over the assigned acquisitions. Saved column
            bindings survive renames.
          </p>
          <select
            aria-label="Insert plate formula reference"
            value=""
            onChange={(e) => {
              const c = plate.columns.find((c) => c.id === e.target.value);
              if (c)
                edit({
                  expression:
                    current.expression + `col(${JSON.stringify(c.name)})`,
                });
            }}
          >
            <option value="">Insert column…</option>
            {plate.columns
              .filter((c) => c.id !== current.id)
              .map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
          </select>
        </>
      )}
      {current.kind === "biology" && (
        <>
          <label className="field">
            Platform
            <select
              aria-label="Plate biology platform"
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
              aria-label="Plate biological model"
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
              {models.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.request.name}
                </option>
              ))}
              {current.result_id &&
                !models.some((r) => r.id === current.result_id) && (
                  <option value={current.result_id}>Missing model</option>
                )}
            </select>
          </label>
          <label className="field">
            Model statistic
            <select
              aria-label="Plate biology statistic"
              value={current.biology_metric}
              onChange={(e) => edit({ biology_metric: e.target.value })}
            >
              {Object.entries(
                current.platform === "kinetics"
                  ? kineticsMetrics
                  : biologyMetrics,
              )
                .filter(([k]) =>
                  current.platform === "kinetics"
                    ? true
                    : current.platform === "proliferation"
                      ? !k.startsWith("g1_") && !k.startsWith("g2_")
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
                        ].includes(k),
                )
                .map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
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
              {current.platform === "cell-cycle" ? "Phase" : "Generation"}
              <select
                aria-label="Plate generation or phase"
                value={current.generation}
                onChange={(e) => edit({ generation: Number(e.target.value) })}
              >
                {Array.from(
                  { length: current.platform === "cell-cycle" ? 3 : 13 },
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
              label="Plate kinetics time range"
            />
          )}
          <label className="checkbox">
            <input
              type="checkbox"
              checked={current.follow_replacement}
              onChange={(e) => edit({ follow_replacement: e.target.checked })}
            />
            Follow model replacements
          </label>
          <label className="checkbox">
            <input
              aria-label="Allow stale plate model"
              type="checkbox"
              checked={current.allow_stale}
              onChange={(e) => edit({ allow_stale: e.target.checked })}
            />
            Use stale model with a visible warning
          </label>
        </>
      )}
      {current.kind !== "formula" && (
        <label className="field">
          Control acquisition
          <select
            aria-label="Plate control acquisition"
            value={current.control_sample_id ?? ""}
            onChange={(e) =>
              edit({ control_sample_id: e.target.value || null })
            }
          >
            <option value="">Use each assigned acquisition</option>
            {workspace.samples.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
      )}
      <label className="field">
        Display decimals
        <input
          aria-label="Plate measurement decimals"
          type="number"
          min={0}
          max={12}
          value={current.decimals}
          onChange={(e) => edit({ decimals: Number(e.target.value) })}
        />
      </label>
      <div className="button-group">
        <button
          className="button small"
          disabled={plate.columns[0].id === current.id}
          onClick={() => {
            const columns = [...plate.columns],
              index = columns.findIndex((c) => c.id === current.id);
            [columns[index - 1], columns[index]] = [
              columns[index],
              columns[index - 1],
            ];
            change({ ...plate, columns });
          }}
        >
          Move measurement earlier
        </button>
        <button
          className="button small"
          disabled={
            plate.columns.length === 1 ||
            plate.columns.some((c) =>
              Object.values(c.formula_refs).includes(current.id),
            )
          }
          onClick={() => {
            const columns = plate.columns.filter((c) => c.id !== current.id),
              domains = { ...plate.view.domains };
            delete domains[current.id];
            change({
              ...plate,
              columns,
              view: {
                ...plate.view,
                primary:
                  plate.view.primary === current.id ? null : plate.view.primary,
                secondary:
                  plate.view.secondary === current.id
                    ? null
                    : plate.view.secondary,
                domains,
              },
            });
            select(columns[0].id);
          }}
        >
          Remove measurement
        </button>
      </div>
    </div>
  );
}
