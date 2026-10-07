import type { GraphOptions } from "../types";
import { GraphFontControls } from "./GraphFontControls";
import { GraphGateControls } from "./GraphGateControls";

export function GraphControls({
  mode,
  options,
  bins,
  onChange,
  onBinsChange,
  disabled = false,
}: {
  mode: string;
  options: GraphOptions;
  bins: number;
  onChange: (options: GraphOptions) => void;
  onBinsChange: (bins: number) => void;
  disabled?: boolean;
}) {
  const probability = ["contour", "zebra"].includes(mode);
  const density = ["density", "pseudocolor", "contour", "zebra"].includes(mode);
  const smooth =
    options.smooth ?? ["contour", "zebra", "pseudocolor"].includes(mode);
  const patch = (value: Partial<GraphOptions>) =>
    onChange({ ...options, ...value });
  return (
    <details className="graph-settings">
      <summary>Graph settings</summary>
      <fieldset className="graph-settings-fields" disabled={disabled}>
        <label>
          Axis extent
          <select
            aria-label="Graph axis extent"
            value={options.axis_extent ?? "robust"}
            onChange={(e) =>
              patch({
                axis_extent: e.target.value as GraphOptions["axis_extent"],
              })
            }
          >
            <option value="robust">Robust automatic</option>
            <option value="full">All finite events</option>
          </select>
        </label>
        {mode !== "3d" && (
          <label>
            Resolution
            <select
              aria-label="Graph resolution"
              value={bins}
              onChange={(e) => onBinsChange(Number(e.target.value))}
            >
              {[32, 64, 96, 128, 160, 256, 384].map((v) => (
                <option key={v} value={v}>
                  {v} bins
                </option>
              ))}
              {![32, 64, 96, 128, 160, 256, 384].includes(bins) && (
                <option value={bins}>{bins} bins</option>
              )}
            </select>
          </label>
        )}
        {density && (
          <label>
            <input
              aria-label="Smooth density"
              type="checkbox"
              checked={smooth}
              onChange={(e) => patch({ smooth: e.target.checked })}
            />{" "}
            Smooth density
          </label>
        )}
        {density && smooth && (
          <label>
            Smoothing
            <select
              aria-label="Density smoothing width"
              value={options.sigma ?? 1}
              onChange={(e) => patch({ sigma: Number(e.target.value) })}
            >
              {[0, 0.5, 1, 2, 4].map((v) => (
                <option key={v} value={v}>
                  {v} bins
                </option>
              ))}
              {options.sigma != null &&
                ![0, 0.5, 1, 2, 4].includes(options.sigma) && (
                  <option value={options.sigma}>{options.sigma} bins</option>
                )}
            </select>
          </label>
        )}
        {["density", "pseudocolor", "zebra", "3d"].includes(mode) && (
          <label>
            Palette
            <select
              aria-label="Graph palette"
              value={options.palette ?? "ocean"}
              onChange={(e) =>
                patch({ palette: e.target.value as GraphOptions["palette"] })
              }
            >
              {["ocean", "gray", "spectrum", "viridis"].map((v) => (
                <option key={v}>{v}</option>
              ))}
            </select>
          </label>
        )}
        {probability && (
          <>
            <label>
              Probability spacing
              <select
                aria-label="Contour probability spacing"
                value={
                  options.contour_spacing ?? (mode === "zebra" ? "2" : "5")
                }
                onChange={(e) =>
                  patch({
                    contour_spacing: e.target
                      .value as GraphOptions["contour_spacing"],
                  })
                }
              >
                <option value="2">2%</option>
                <option value="5">5%</option>
                <option value="10">10%</option>
                <option value="log">Logarithmic</option>
              </select>
            </label>
            <label>
              <input
                aria-label="Show contour outliers"
                type="checkbox"
                checked={options.show_outliers ?? true}
                onChange={(e) => patch({ show_outliers: e.target.checked })}
              />{" "}
              Show outliers
            </label>
          </>
        )}
        {(mode === "scatter" || mode === "3d" || probability) && (
          <label>
            Marker limit
            <select
              aria-label="Graph marker limit"
              value={options.point_limit ?? 12000}
              onChange={(e) => patch({ point_limit: Number(e.target.value) })}
            >
              {[1000, 12000, 50000, 100000].map((v) => (
                <option key={v} value={v}>
                  {v.toLocaleString()}
                </option>
              ))}
              {options.point_limit != null &&
                ![1000, 12000, 50000, 100000].includes(options.point_limit) && (
                  <option value={options.point_limit}>
                    {options.point_limit.toLocaleString()}
                  </option>
                )}
            </select>
          </label>
        )}
      </fieldset>
      <GraphFontControls
        options={options}
        onChange={onChange}
        disabled={disabled}
      />
      <GraphGateControls
        mode={mode}
        options={options}
        onChange={onChange}
        disabled={disabled}
      />
      {probability && (
        <small>
          Probability regions use all finite population events. Smoothing and
          tied density bins affect coverage; zoom preserves the estimate inside
          the automatic domain.
        </small>
      )}
      {mode === "cdf" && (
        <small>
          Cumulative frequency uses all finite population events, including
          events outside the view. Values at displayed bin edges include ties.
        </small>
      )}
    </details>
  );
}
