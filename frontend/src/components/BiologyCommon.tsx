import { useState } from "react";
import type { FitConstraint } from "../types";
import { formatNumber } from "../types";

export const generationColors = [
  "#719bff",
  "#38d9ba",
  "#edb96c",
  "#b595f6",
  "#ef8b9b",
  "#62bac0",
  "#bba272",
  "#91a5eb",
  "#89b68a",
  "#d69dca",
  "#a5adb8",
  "#d89770",
  "#90bccd",
];
export const nullableNumber = (value: string) =>
  value.trim() === "" ? null : Number(value);

export function availableRefitName(name: string, existing: string[]) {
  const base = name.replace(/ refit(?: \d+)?$/, "");
  const names = new Set(existing);
  let index = 1;
  while (true) {
    const suffix = index === 1 ? " refit" : ` refit ${index}`;
    const candidate = base.slice(0, 125 - suffix.length) + suffix;
    if (!names.has(candidate)) return candidate;
    index++;
  }
}

export function PeakConstraintEditor({
  label,
  value,
  onChange,
  initialDefault,
  fixedDefault,
  rangeDefault,
}: {
  label: string;
  value: FitConstraint;
  onChange: (v: FitConstraint) => void;
  initialDefault?: number;
  fixedDefault: number;
  rangeDefault: [number, number];
}) {
  const [mode, setMode] = useState(
    value.fixed != null
      ? "fixed"
      : value.minimum != null || value.maximum != null
        ? "range"
        : "free",
  );
  const input = (key: keyof FitConstraint, text: string, required = false) => (
    <label className="field" key={key}>
      {text}
      <input
        aria-label={`${label} ${text.toLowerCase()}`}
        type="number"
        step="any"
        min="0.000000001"
        required={required}
        value={value[key] ?? ""}
        onChange={(e) =>
          onChange({ ...value, [key]: nullableNumber(e.target.value) })
        }
      />
    </label>
  );
  return (
    <div className="population-constraint">
      <label className="field">
        {label}
        <select
          aria-label={`${label} constraint`}
          value={mode}
          onChange={(e) => {
            setMode(e.target.value);
            onChange(
              e.target.value === "fixed"
                ? { fixed: value.fixed ?? value.initial ?? fixedDefault }
                : e.target.value === "range"
                  ? {
                      minimum: rangeDefault[0],
                      maximum: rangeDefault[1],
                      initial: value.initial ?? initialDefault,
                    }
                  : { initial: value.initial ?? value.fixed ?? initialDefault },
            );
          }}
        >
          <option value="free">Fit</option>
          <option value="fixed">Fixed</option>
          <option value="range">Bounded fit</option>
        </select>
      </label>
      {mode === "fixed" ? (
        input("fixed", "Value", true)
      ) : (
        <>
          {mode === "range" && (
            <>
              {input("minimum", "Minimum")}
              {input("maximum", "Maximum")}
            </>
          )}
          {input("initial", "Initial value")}
        </>
      )}
    </div>
  );
}

export function HistogramFitChart({
  fit,
  channel,
  labels,
  logarithmic = false,
}: {
  fit: {
    edges: number[];
    observed: number[];
    components: number[][];
    weights: number[][];
  };
  channel: string;
  labels: string[];
  logarithmic?: boolean;
}) {
  const [hidden, setHidden] = useState<number[]>([]);
  const [selected, setSelected] = useState(Math.floor(fit.observed.length / 2));
  const n = fit.observed.length;
  const index = Math.min(selected, n - 1);
  const scale = Math.max(
    Math.abs(fit.edges[0]),
    Math.abs(fit.edges[n]),
    1e-300,
  );
  const positions = fit.edges.map((v) =>
    logarithmic ? Math.log2(v) : v / scale,
  );
  const centers = positions
    .slice(0, -1)
    .map((v, i) => v / 2 + positions[i + 1] / 2);
  const px = (i: number) =>
    72 + (800 * (centers[i] - positions[0])) / (positions[n] - positions[0]);
  const total = fit.observed.map((_, i) =>
    fit.components.reduce((sum, row) => sum + row[i], 0),
  );
  const ymax = Math.max(1, ...fit.observed, ...total) * 1.08;
  const path = (values: number[], y: number, height: number, max: number) =>
    "M" +
    values
      .map(
        (v, i) => `${px(i).toFixed(3)},${(y - (height * v) / max).toFixed(3)}`,
      )
      .join(" L");
  const residual = fit.observed.map((v, i) => v - total[i]);
  const rmax = Math.max(1, ...residual.map(Math.abs));
  function inspect(event: React.PointerEvent<SVGSVGElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    const fraction = Math.max(
      0,
      Math.min(
        1,
        (((event.clientX - rect.left) * 900) / rect.width - 72) / 800,
      ),
    );
    const value = positions[0] + fraction * (positions[n] - positions[0]);
    let lower = 0,
      upper = n;
    while (lower < upper) {
      const mid = Math.floor((lower + upper) / 2);
      if (positions[mid + 1] < value) lower = mid + 1;
      else upper = mid;
    }
    setSelected(Math.min(lower, n - 1));
  }
  const tick = (i: number) =>
    logarithmic
      ? fit.edges[n] *
        Math.exp(Math.log(fit.edges[0] / fit.edges[n]) * (1 - i / 4))
      : fit.edges[0] * (1 - i / 4) + (fit.edges[n] * i) / 4;
  return (
    <div className="population-chart">
      <div className="cell-cycle-legend">
        <span>Gray: observed · Purple: total model</span>
        {labels.map((v, i) => (
          <button
            key={v}
            className="button small ghost"
            aria-pressed={!hidden.includes(i)}
            style={{
              color: generationColors[i],
              opacity: hidden.includes(i) ? 0.4 : 1,
            }}
            onClick={() =>
              setHidden((h) =>
                h.includes(i) ? h.filter((j) => j !== i) : [...h, i],
              )
            }
          >
            {v}
          </button>
        ))}
      </div>
      <svg
        viewBox="0 0 900 490"
        role="img"
        aria-label="Generation model histogram and residuals"
        onPointerMove={inspect}
      >
        <title>
          Observed dye fluorescence, generation components, total fitted model,
          and residuals
        </title>
        {[0, 1, 2, 3, 4].map((i) => (
          <g key={i}>
            <line
              x1="72"
              x2="872"
              y1={340 - (230 * i) / 4}
              y2={340 - (230 * i) / 4}
              stroke="var(--line)"
            />
            <text x="62" y={345 - (230 * i) / 4} textAnchor="end">
              {formatNumber((ymax * i) / 4, 0)}
            </text>
          </g>
        ))}
        <text x="72" y="72">
          Events per bin
        </text>
        <path
          d={path(fit.observed, 340, 230, ymax)}
          stroke="var(--muted)"
          fill="none"
          strokeWidth="1.4"
        />
        <path
          d={path(total, 340, 230, ymax)}
          stroke="#b595f6"
          fill="none"
          strokeWidth="2.6"
        />
        {fit.components.map(
          (row, i) =>
            !hidden.includes(i) && (
              <path
                key={i}
                d={path(row, 340, 230, ymax)}
                stroke={generationColors[i]}
                fill="none"
                strokeWidth="1.8"
              />
            ),
        )}
        <line
          x1={px(index)}
          x2={px(index)}
          y1="104"
          y2="420"
          stroke="var(--accent)"
          strokeDasharray="4 5"
        />
        <line x1="72" x2="872" y1="390" y2="390" stroke="var(--line)" />
        <path
          d={path(residual, 390, 28, rmax)}
          stroke="var(--muted)"
          fill="none"
          strokeWidth="1.2"
        />
        <text x="72" y="366">
          Observed − fitted
        </text>
        {[0, 1, 2, 3, 4].map((i) => (
          <text key={i} x={72 + i * 200} y="446" textAnchor="middle">
            {formatNumber(tick(i), 2)}
          </text>
        ))}
        <text x="472" y="477" textAnchor="middle">
          {channel} · {logarithmic ? "logarithmic" : "linear"} intensity
        </text>
      </svg>
      <label className="field">
        Inspect generation histogram bin
        <input
          aria-label="Inspect generation histogram bin"
          type="range"
          min="0"
          max={n - 1}
          value={index}
          onChange={(e) => setSelected(Number(e.target.value))}
        />
      </label>
      <div className="cell-cycle-bin" aria-live="polite">
        Intensity {formatNumber(fit.edges[index], 3)}–
        {formatNumber(fit.edges[index + 1], 3)} ·{" "}
        {fit.observed[index].toLocaleString()} observed ·{" "}
        {formatNumber(total[index], 2)} fitted
        {labels.map((v, i) => (
          <span key={v} style={{ color: generationColors[i] }}>
            {v} probability {(fit.weights[i][index] * 100).toFixed(1)}%
          </span>
        ))}
      </div>
    </div>
  );
}
