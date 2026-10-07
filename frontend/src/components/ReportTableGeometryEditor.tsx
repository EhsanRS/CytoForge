import { useState } from "react";
import type {
  ReportElement,
  ReportTableCellStyle,
  ReportTableGeometry,
  ReportTablePagination,
} from "../types";

export function ReportTableGeometryEditor({
  element,
  paging,
  onChange,
}: {
  element: ReportElement;
  paging?: ReportTablePagination;
  onChange: (value: ReportTableGeometry | null) => void;
}) {
  const [column, setColumn] = useState(0);
  const [row, setRow] = useState(0);
  const [heading, setHeading] = useState(true);
  const value = element.table_geometry ?? {};
  const styles = value.cell_styles ?? [];
  const selectedRow = heading ? null : row;
  const style = styles.find(
    (item) => (item.row ?? null) === selectedRow && item.column === column,
  ) ?? { row: selectedRow, column };
  function patch(patch: Partial<ReportTableGeometry>) {
    onChange({ ...value, ...patch });
  }
  function patchCell(cellUpdates: Partial<ReportTableCellStyle>) {
    const next = { ...style, ...cellUpdates };
    const format = Object.fromEntries(
      Object.entries(next).filter(
        ([key, value]) => !["row", "column"].includes(key) && value != null,
      ),
    );
    const others = styles.filter(
      (item) => (item.row ?? null) !== selectedRow || item.column !== column,
    );
    patch({
      cell_styles: Object.keys(format).length
        ? [...others, { row: selectedRow, column, ...format }]
        : others,
    });
  }
  function size(
    label: string,
    current: number | null | undefined,
    update: (value: number | undefined) => void,
    { placeholder = "Automatic", min = 0.1, max = 1200, disabled = false } = {},
  ) {
    return (
      <label>
        {label}
        <input
          aria-label={label}
          type="number"
          min={min}
          max={max}
          step={0.1}
          value={current ?? ""}
          placeholder={placeholder}
          disabled={disabled}
          onChange={(e) => {
            const input = e.target.value;
            const number = Number(input);
            if (!input) update(undefined);
            else if (Number.isFinite(number) && number >= min && number <= max)
              update(number);
          }}
        />
      </label>
    );
  }
  function dimension(
    kind: "column_widths_mm" | "row_heights_mm",
    index: number,
    size: number | undefined,
  ) {
    const next = { ...value[kind] };
    if (size == null) delete next[index];
    else next[index] = size;
    patch({ [kind]: next });
  }
  const columns = paging?.columns ?? [];
  return (
    <details className="studio-table-geometry">
      <summary>Column and cell layout</summary>
      <small>
        Column settings follow the displayed table order. Heading columns repeat
        on every continuation page. Empty size fields use automatic geometry.
      </small>
      <div className="studio-fields">
        {size(
          "Default column width (mm)",
          value.column_width_mm,
          (column_width_mm) => patch({ column_width_mm }),
        )}
        {size("Default row height (mm)", value.row_height_mm, (row_height_mm) =>
          patch({ row_height_mm }),
        )}
        {size(
          "Heading height (mm)",
          value.header_height_mm,
          (header_height_mm) => patch({ header_height_mm }),
        )}
        {size(
          "Horizontal cell padding (mm)",
          value.padding_x_mm ?? 0.7,
          (padding_x_mm) => patch({ padding_x_mm: padding_x_mm ?? 0.7 }),
          { min: 0, max: 20 },
        )}
        {size(
          "Vertical cell padding (mm)",
          value.padding_y_mm ?? 0,
          (padding_y_mm) => patch({ padding_y_mm: padding_y_mm ?? 0 }),
          { min: 0, max: 20 },
        )}
        <label>
          Table text alignment
          <select
            aria-label="Table text alignment"
            value={value.align ?? "left"}
            onChange={(e) =>
              patch({ align: e.target.value as ReportTableGeometry["align"] })
            }
          >
            {["left", "center", "right"].map((key) => (
              <option key={key}>{key}</option>
            ))}
          </select>
        </label>
        <label>
          Table vertical alignment
          <select
            aria-label="Table vertical alignment"
            value={value.vertical_align ?? "top"}
            onChange={(e) =>
              patch({
                vertical_align: e.target
                  .value as ReportTableGeometry["vertical_align"],
              })
            }
          >
            {["top", "middle", "bottom"].map((key) => (
              <option key={key}>{key}</option>
            ))}
          </select>
        </label>
      </div>
      <label>
        Column
        <select
          aria-label="Formatted table column"
          value={column}
          onChange={(e) => setColumn(Number(e.target.value))}
        >
          {columns.length ? (
            columns.map((item) => (
              <option key={item.index} value={item.index}>
                {item.index + 1}. {item.name}
              </option>
            ))
          ) : (
            <option value={0}>1. First column</option>
          )}
        </select>
      </label>
      {size(
        "Selected column width (mm)",
        value.column_widths_mm?.[column],
        (next) => dimension("column_widths_mm", column, next),
        {
          placeholder: value.column_width_mm
            ? String(value.column_width_mm)
            : "Automatic",
        },
      )}
      <label className="check">
        <input
          aria-label="Format table heading cell"
          type="checkbox"
          checked={heading}
          onChange={(e) => setHeading(e.target.checked)}
        />
        Format heading cell
      </label>
      <label>
        Row
        <input
          aria-label="Formatted table row"
          type="number"
          min={1}
          max={Math.max(1, paging?.total_rows ?? 50000)}
          disabled={heading}
          value={row + 1}
          onChange={(e) => {
            const next = Number(e.target.value);
            if (
              Number.isInteger(next) &&
              next >= 1 &&
              next <= (paging?.total_rows ?? 50000)
            )
              setRow(next - 1);
          }}
        />
      </label>
      {!heading &&
        size(
          "Selected row height (mm)",
          value.row_heights_mm?.[row],
          (next) => dimension("row_heights_mm", row, next),
          {
            placeholder: value.row_height_mm
              ? String(value.row_height_mm)
              : "Automatic",
          },
        )}
      <div className="studio-fields">
        <label>
          Cell text alignment
          <select
            aria-label="Cell text alignment"
            value={style.align ?? ""}
            onChange={(e) =>
              patchCell({
                align: (e.target.value ||
                  undefined) as ReportTableCellStyle["align"],
              })
            }
          >
            <option value="">Inherit</option>
            {["left", "center", "right"].map((key) => (
              <option key={key}>{key}</option>
            ))}
          </select>
        </label>
        <label>
          Cell vertical alignment
          <select
            aria-label="Cell vertical alignment"
            value={style.vertical_align ?? ""}
            onChange={(e) =>
              patchCell({
                vertical_align: (e.target.value ||
                  undefined) as ReportTableCellStyle["vertical_align"],
              })
            }
          >
            <option value="">Inherit</option>
            {["top", "middle", "bottom"].map((key) => (
              <option key={key}>{key}</option>
            ))}
          </select>
        </label>
        {size(
          "Cell font size (pt)",
          style.font_size_pt,
          (font_size_pt) => patchCell({ font_size_pt }),
          { placeholder: "Inherit", min: 4, max: 144 },
        )}
        {size(
          "Cell line spacing",
          style.line_spacing,
          (line_spacing) => patchCell({ line_spacing }),
          { placeholder: "Inherit", min: 1, max: 3 },
        )}
        <label>
          Cell font family
          <select
            aria-label="Cell font family"
            value={style.font_family ?? ""}
            onChange={(e) =>
              patchCell({
                font_family: (e.target.value ||
                  undefined) as ReportTableCellStyle["font_family"],
              })
            }
          >
            <option value="">Inherit</option>
            {["sans-serif", "serif", "monospace"].map((key) => (
              <option key={key}>{key}</option>
            ))}
          </select>
        </label>
        <label>
          Cell font weight
          <select
            aria-label="Cell font weight"
            value={style.font_weight ?? ""}
            onChange={(e) =>
              patchCell({
                font_weight: e.target.value
                  ? (Number(
                      e.target.value,
                    ) as ReportTableCellStyle["font_weight"])
                  : undefined,
              })
            }
          >
            <option value="">Inherit</option>
            {[
              [400, "Regular"],
              [600, "Semibold"],
              [700, "Bold"],
              [800, "Extra bold"],
            ].map(([key, name]) => (
              <option key={key} value={key}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Cell font style
          <select
            aria-label="Cell font style"
            value={style.font_style ?? ""}
            onChange={(e) =>
              patchCell({
                font_style: (e.target.value ||
                  undefined) as ReportTableCellStyle["font_style"],
              })
            }
          >
            <option value="">Inherit</option>
            <option value="normal">Normal</option>
            <option value="italic">Italic</option>
          </select>
        </label>
        <label>
          Cell decoration
          <select
            aria-label="Cell text decoration"
            value={style.text_decoration ?? ""}
            onChange={(e) =>
              patchCell({
                text_decoration: (e.target.value ||
                  undefined) as ReportTableCellStyle["text_decoration"],
              })
            }
          >
            <option value="">Inherit</option>
            <option value="none">None</option>
            <option value="underline">Underline</option>
          </select>
        </label>
        <label>
          Cell text colour
          <input
            aria-label="Cell text colour"
            type="color"
            value={style.color ?? element.color}
            onChange={(e) => patchCell({ color: e.target.value })}
          />
        </label>
        <label>
          Cell background
          <input
            aria-label="Cell background"
            type="color"
            value={style.background ?? "#ffffff"}
            onChange={(e) => patchCell({ background: e.target.value })}
          />
        </label>
      </div>
      <small>
        Source error cells keep their diagnostic text colour. Automatic row
        heights accommodate cell fonts; explicit heights report clipped text.
      </small>
      <div className="button-group">
        <button
          className="text-button"
          type="button"
          onClick={() =>
            patch({
              cell_styles: styles.filter(
                (item) =>
                  (item.row ?? null) !== selectedRow || item.column !== column,
              ),
            })
          }
        >
          Reset this cell
        </button>
        <button
          className="text-button"
          type="button"
          onClick={() => onChange(null)}
        >
          Reset table layout
        </button>
      </div>
    </details>
  );
}
