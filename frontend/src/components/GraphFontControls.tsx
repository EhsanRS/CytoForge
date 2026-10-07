import { useEffect, useState } from "react";
import type { GraphOptions } from "../types";
import {
  graphTextRoles,
  patchGraphText,
  scientificGraphOptions,
  type GraphTextRole,
} from "../graphTypography";

export function GraphFontControls({
  options,
  onChange,
  disabled = false,
}: {
  options: GraphOptions;
  onChange: (options: GraphOptions) => void;
  disabled?: boolean;
}) {
  const [role, setRole] = useState<GraphTextRole>("axis_labels");
  const style = options.typography?.[role] ?? {};
  const [sizeDraft, setSizeDraft] = useState(String(style.font_size_pt ?? ""));
  useEffect(
    () => setSizeDraft(String(style.font_size_pt ?? "")),
    [role, style.font_size_pt],
  );
  const patch = (value: Parameters<typeof patchGraphText>[2]) =>
    onChange(patchGraphText(options, role, value));
  return (
    <details className="graph-font-settings">
      <summary>Plot fonts</summary>
      <fieldset className="graph-settings-fields" disabled={disabled}>
        <label>
          Text
          <select
            aria-label="Plot font target"
            value={role}
            onChange={(event) => setRole(event.target.value as GraphTextRole)}
          >
            {Object.entries(graphTextRoles).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Typeface
          <select
            aria-label="Plot typeface"
            value={style.font_family ?? ""}
            onChange={(event) =>
              patch({
                font_family: (event.target.value ||
                  null) as typeof style.font_family,
              })
            }
          >
            <option value="">Automatic</option>
            <option value="sans">Sans</option>
            <option value="serif">Serif</option>
            <option value="mono">Monospace</option>
          </select>
        </label>
        <label>
          Size (pt)
          <input
            aria-label="Plot font size in points"
            type="number"
            min={4}
            max={144}
            step="any"
            value={sizeDraft}
            aria-invalid={
              sizeDraft !== "" &&
              (!Number.isFinite(Number(sizeDraft)) ||
                Number(sizeDraft) < 4 ||
                Number(sizeDraft) > 144)
            }
            placeholder="Automatic"
            onChange={(event) => {
              setSizeDraft(event.target.value);
              if (event.target.value === "") patch({ font_size_pt: null });
              else if (
                event.target.validity.valid &&
                Number.isFinite(event.target.valueAsNumber)
              )
                patch({ font_size_pt: event.target.valueAsNumber });
            }}
            onBlur={(event) => {
              if (!event.currentTarget.validity.valid)
                setSizeDraft(String(style.font_size_pt ?? ""));
            }}
          />
        </label>
        <label>
          Weight
          <select
            aria-label="Plot font weight"
            value={style.font_weight ?? ""}
            onChange={(event) =>
              patch({
                font_weight: (event.target.value ||
                  null) as typeof style.font_weight,
              })
            }
          >
            <option value="">Automatic</option>
            <option value="normal">Normal</option>
            <option value="bold">Bold</option>
          </select>
        </label>
        <label>
          Style
          <select
            aria-label="Plot font style"
            value={style.font_style ?? ""}
            onChange={(event) =>
              patch({
                font_style: (event.target.value ||
                  null) as typeof style.font_style,
              })
            }
          >
            <option value="">Automatic</option>
            <option value="normal">Normal</option>
            <option value="italic">Italic</option>
          </select>
        </label>
        <label>
          Text color
          <input
            aria-label="Plot text color"
            type="color"
            value={style.color ?? "#233449"}
            onChange={(event) => patch({ color: event.target.value })}
          />
        </label>
        <button
          type="button"
          className="text-button"
          onClick={() =>
            onChange(
              patchGraphText(options, role, {
                font_size_pt: null,
                font_family: null,
                font_weight: null,
                font_style: null,
                color: null,
              }),
            )
          }
        >
          Reset {graphTextRoles[role].toLowerCase()}
        </button>
        <button
          type="button"
          className="text-button"
          onClick={() => onChange(scientificGraphOptions(options))}
        >
          Reset all plot fonts
        </button>
      </fieldset>
    </details>
  );
}
