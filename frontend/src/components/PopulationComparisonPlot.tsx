import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, download, params } from "../api";
import {
  comparisonFigureView,
  comparisonGeometry,
  defaultComparisonFigureView,
} from "../comparisons";
import type {
  ComparisonPlotData,
  DesktopComparisonState,
  PopulationComparisonResult,
  PopulationComparisonRow,
} from "../types";
import { ErrorState } from "./Common";

const number = (value: unknown) =>
  typeof value === "number"
    ? value.toLocaleString(undefined, { maximumSignificantDigits: 6 })
    : value == null
      ? "Unavailable"
      : String(value);
export const defaultComparisonView = (
  workspaceId: string,
  resultId: string,
): DesktopComparisonState => {
  const defaults = defaultComparisonFigureView();
  return {
    workspaceId,
    resultId,
    parameterId: null,
    targetIndex: 0,
    mode: defaults.mode,
    smoothing: defaults.smoothing,
    controlColor: defaults.control_color,
    targetColor: defaults.target_color,
    differenceScale: defaults.difference_scale,
    showIndividuals: defaults.show_individuals,
  };
};

export function ComparisonMetrics({
  rows,
}: {
  rows: PopulationComparisonRow[];
}) {
  return (
    <div className="comparison-metrics">
      <table>
        <thead>
          <tr>
            <th>Population / parameter</th>
            <th>Finite / selected</th>
            <th>Control finite</th>
            <th>KS D</th>
            <th>KS p</th>
            <th>Overton cumulative %</th>
            <th>Enhanced Dmax %</th>
            <th>ENS-1 %</th>
            <th>PB χ′²</th>
            <th>T(X)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              <th>
                {row.source_name} · {row.population_name}
                <small>
                  {row.role === "control_baseline"
                    ? "Independent control baseline"
                    : row.parameter_id
                      ? "Univariate"
                      : "Joint distribution"}
                </small>
                {row.error && (
                  <small className="error-message">{row.error}</small>
                )}
                {row.shared_events > 0 && (
                  <small>
                    {row.shared_events.toLocaleString()} shared events;
                    independent KS probability unavailable
                  </small>
                )}
                {row.warnings.map((warning, j) => (
                  <small key={j}>{warning}</small>
                ))}
              </th>
              <td>
                {number(row.finite_count)} / {number(row.selected_count)}
              </td>
              <td>{number(row.control_finite_count)}</td>
              {[
                "ks_distance",
                "ks_p_value",
                "overton_cumulative_percent",
                "enhanced_dmax_percent",
                "ens_percent",
              ].map((k) => (
                <td key={k}>{number(row.metrics[k])}</td>
              ))}
              <td>{number(row.probability.chi_squared)}</td>
              <td>{number(row.probability.tx)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Graph({
  data,
  view,
}: {
  data: ComparisonPlotData;
  view: DesktopComparisonState;
}) {
  const geometry = useMemo(
    () => comparisonGeometry(data, view),
    [
      data,
      view.mode,
      view.smoothing,
      view.differenceScale,
      view.showIndividuals,
      view.controlColor,
      view.targetColor,
    ],
  );
  return (
    <svg
      className="comparison-graph"
      viewBox="0 0 800 465"
      role="img"
      aria-label={`${view.mode} population comparison`}
    >
      <defs>
        <clipPath id="comparison-clip">
          <rect x="65" y="65" width="690" height="325" />
        </clipPath>
      </defs>
      <path
        d="M65 65V390H755"
        fill="none"
        stroke="currentColor"
        opacity=".35"
      />
      {view.mode === "difference" && (
        <line
          x1="65"
          x2="755"
          y1={geometry.zeroY}
          y2={geometry.zeroY}
          stroke="currentColor"
          opacity=".25"
        />
      )}
      <g clipPath="url(#comparison-clip)">
        {geometry.curves.map((curve, i) => (
          <polyline
            key={i}
            points={curve.points}
            fill="none"
            stroke={curve.color}
            strokeWidth={curve.width}
            opacity={curve.opacity}
          />
        ))}
        {geometry.ksX !== null && (
          <line
            x1={geometry.ksX}
            x2={geometry.ksX}
            y1="65"
            y2="390"
            stroke="currentColor"
            strokeDasharray="4 4"
            opacity=".4"
          />
        )}
      </g>
      <text x="65" y="418" fill="currentColor" fontSize="11">
        {number(data.edges[0])}
      </text>
      <text x="755" y="418" textAnchor="end" fill="currentColor" fontSize="11">
        {number(data.edges.at(-1))}
      </text>
      <text
        x="410"
        y="445"
        textAnchor="middle"
        fill="currentColor"
        fontSize="13"
      >
        {data.parameter.label || data.parameter.channel} ·{" "}
        {data.parameter.transform.kind}
      </text>
      <text x="70" y="35" fill={view.controlColor} fontSize="13">
        Control n={number(data.row.control_finite_count)}
      </text>
      <text x="335" y="35" fill={view.targetColor} fontSize="13">
        Target n={number(data.row.finite_count)}
      </text>
      <text x="70" y="57" fill="currentColor" fontSize="11">
        {view.mode === "cdf"
          ? "Cumulative fraction"
          : view.mode === "difference"
            ? "Target − control fraction, independent vertical scale"
            : "Fraction per shared bin"}
      </text>
    </svg>
  );
}

export function PopulationComparisonPlot({
  result,
  view,
  onChange,
}: {
  result: PopulationComparisonResult;
  view: DesktopComparisonState;
  onChange: (v: DesktopComparisonState) => void;
}) {
  const parameter =
    result.request.parameters.find((p) => p.id === view.parameterId) ??
    result.request.parameters[0];
  const targetIndex = Math.min(
    view.targetIndex,
    result.request.inputs.length - 1,
  );
  const query = useQuery({
    queryKey: [
      view.workspaceId,
      "comparison-plot",
      result.id,
      parameter.id,
      targetIndex,
      result.stale,
    ],
    queryFn: () =>
      api<ComparisonPlotData>(
        `/workspaces/${view.workspaceId}/population-comparison/${result.id}/plot?${params({ parameter_id: parameter.id, target_index: targetIndex })}`,
      ),
  });
  const set = (patch: Partial<DesktopComparisonState>) =>
    onChange({ ...view, ...patch });
  return (
    <section className="comparison-result">
      {result.stale && (
        <div className="warning-banner">
          Scientific inputs changed. These saved counts describe the original
          comparison; refit to update them.
        </div>
      )}
      <div className="comparison-toolbar">
        <label className="field">
          Parameter
          <select
            aria-label="Comparison graph parameter"
            value={parameter.id}
            onChange={(e) => set({ parameterId: e.target.value })}
          >
            {result.request.parameters.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label || p.channel}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Target
          <select
            aria-label="Comparison graph target"
            value={targetIndex}
            onChange={(e) => set({ targetIndex: Number(e.target.value) })}
          >
            {result.request.inputs.map((s, i) => {
              const row = result.rows.find(
                (r) =>
                  r.role === "target" &&
                  r.source.sample_id === s.sample_id &&
                  r.source.gate_id === s.gate_id,
              );
              return (
                <option key={i} value={i}>
                  {row?.source_name} · {row?.population_name}
                </option>
              );
            })}
          </select>
        </label>
        <label className="field">
          Graph
          <select
            aria-label="Comparison graph mode"
            value={view.mode}
            onChange={(e) =>
              set({ mode: e.target.value as DesktopComparisonState["mode"] })
            }
          >
            <option value="histogram">Histogram</option>
            <option value="cdf">Cumulative distribution</option>
            <option value="difference">Signed difference</option>
          </select>
        </label>
        <label className="field">
          Display smoothing
          <input
            aria-label="Comparison display smoothing"
            type="number"
            min="0"
            max="16"
            step=".25"
            disabled={view.mode === "cdf"}
            value={view.smoothing}
            onChange={(e) =>
              set({
                smoothing: Math.max(0, Math.min(16, Number(e.target.value))),
              })
            }
          />
        </label>
        <label className="field">
          Control tint
          <input
            aria-label="Comparison control tint"
            type="color"
            value={view.controlColor}
            onChange={(e) => set({ controlColor: e.target.value })}
          />
        </label>
        <label className="field">
          Target tint
          <input
            aria-label="Comparison target tint"
            type="color"
            value={view.targetColor}
            onChange={(e) => set({ targetColor: e.target.value })}
          />
        </label>
        {view.mode === "difference" && (
          <label className="field">
            Difference scale
            <input
              aria-label="Difference vertical scale"
              type="number"
              min=".1"
              max="10"
              step=".1"
              value={view.differenceScale}
              onChange={(e) =>
                set({
                  differenceScale: Math.max(
                    0.1,
                    Math.min(10, Number(e.target.value)),
                  ),
                })
              }
            />
          </label>
        )}
        <label>
          <input
            type="checkbox"
            checked={view.showIndividuals}
            onChange={(e) => set({ showIndividuals: e.target.checked })}
          />{" "}
          Individual controls
        </label>
      </div>
      {query.error && <ErrorState error={query.error} />}
      {query.data && (
        <>
          <Graph data={query.data} view={view} />
          {view.smoothing > 0 && view.mode !== "cdf" && (
            <p className="hint">
              Smoothing changes the display only. Numerical statistics use all
              finite events.
            </p>
          )}
          <ComparisonMetrics
            rows={[
              query.data.row,
              ...result.joint_rows.filter(
                (r) =>
                  r.role === "target" &&
                  r.source.sample_id === query.data.row.source.sample_id &&
                  r.source.gate_id === query.data.row.source.gate_id,
              ),
            ]}
          />
          <details>
            <summary>Control baselines for this parameter</summary>
            <ComparisonMetrics
              rows={result.rows.filter(
                (r) =>
                  r.role === "control_baseline" &&
                  r.parameter_id === parameter.id,
              )}
            />
          </details>
        </>
      )}
      <p className="hint">
        ENS-1 uses the published Bagwell correction. KS probabilities assume
        independent continuous distributions; shared events and ties are
        reported. Event-level probabilities do not measure biological replicate
        significance.
      </p>
      <div className="button-row">
        <button
          onClick={() =>
            void download(
              `/workspaces/${view.workspaceId}/population-comparison/${result.id}/statistics`,
              "population-comparison.csv",
            )
          }
        >
          Export statistics CSV
        </button>
        <button
          onClick={() =>
            void download(
              `/workspaces/${view.workspaceId}/population-comparison/${result.id}/report`,
              "population-comparison.json",
            )
          }
        >
          Export report JSON
        </button>
        <button
          onClick={() =>
            void download(
              `/workspaces/${view.workspaceId}/population-comparison/${result.id}/figure?${params({ parameter_id: parameter.id, target_index: targetIndex, ...comparisonFigureView(view) })}`,
              "population-comparison.svg",
            )
          }
        >
          Export graph SVG
        </button>
      </div>
    </section>
  );
}
