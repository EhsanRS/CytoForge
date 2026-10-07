import { useEffect, useState } from "react";
import { patchGraphGateStyle } from "../graphGateStyle";
import type { GraphGateStyle, GraphOptions } from "../types";

function GateNumber({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  const number = Number(draft);
  const valid =
    draft.trim() !== "" &&
    Number.isFinite(number) &&
    number >= min &&
    number <= max;
  return (
    <label>
      {label}
      <input
        aria-label={label}
        type="number"
        min={min}
        max={max}
        step="any"
        value={draft}
        aria-invalid={!valid}
        onChange={(event) => {
          const text = event.target.value;
          setDraft(text);
          const next = Number(text);
          if (
            text.trim() &&
            Number.isFinite(next) &&
            next >= min &&
            next <= max
          )
            onChange(next);
        }}
        onBlur={() => {
          if (!valid) setDraft(String(value));
        }}
      />
    </label>
  );
}

export function GraphGateControls({
  mode,
  options,
  onChange,
  disabled = false,
}: {
  mode: string;
  options: GraphOptions;
  onChange: (value: GraphOptions) => void;
  disabled?: boolean;
}) {
  const style = options.gate_style;
  const patch = (value: Partial<GraphGateStyle>) =>
    onChange(patchGraphGateStyle(options, value));
  return (
    <details className="graph-gate-settings">
      <summary>Gate appearance</summary>
      <fieldset className="graph-settings-fields" disabled={disabled}>
        <GateNumber
          label="Gate border width (px)"
          value={style?.line_width_px ?? (mode === "3d" ? 0.85 : 1.5)}
          min={0.25}
          max={12}
          onChange={(line_width_px) => patch({ line_width_px })}
        />
        <label>
          <input
            aria-label="Show gate labels"
            type="checkbox"
            checked={style?.show_labels ?? true}
            onChange={(event) => patch({ show_labels: event.target.checked })}
          />{" "}
          Show gate labels
        </label>
        {mode !== "3d" && (
          <>
            <GateNumber
              label="Gate fill opacity"
              value={style?.fill_opacity ?? 0}
              min={0}
              max={1}
              onChange={(fill_opacity) => patch({ fill_opacity })}
            />
            <label>
              <input
                aria-label="Use gate colours for fill"
                type="checkbox"
                checked={style?.fill_color == null}
                onChange={(event) =>
                  patch({ fill_color: event.target.checked ? null : "#38d9ba" })
                }
              />{" "}
              Use gate colours for fill
            </label>
            {style?.fill_color != null && (
              <label>
                Gate fill colour
                <input
                  aria-label="Gate fill colour"
                  type="color"
                  value={style.fill_color}
                  onChange={(event) =>
                    patch({ fill_color: event.target.value })
                  }
                />
              </label>
            )}
            <small>
              Fill applies to closed gates. Open quadrant separators keep their
              borders.
            </small>
          </>
        )}
        <button
          type="button"
          onClick={() => {
            const { gate_style: _gateStyle, ...rest } = options;
            onChange(rest);
          }}
        >
          Reset gate appearance
        </button>
      </fieldset>
    </details>
  );
}
