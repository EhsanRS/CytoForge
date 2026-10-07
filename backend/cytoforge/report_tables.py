"""Full-cohort report tables, bounded evaluation reuse and explicit page windows."""

from __future__ import annotations

import hashlib
import json
import math
import threading
from collections import OrderedDict

from . import report_cache, report_svg, report_table_geometry, tables


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()
    ).hexdigest()


class EvaluationCache:
    """Private immutable results; callers select new windows without mutating entries."""

    def __init__(self, maximum_bytes=64 * 1024 * 1024):
        self.maximum_bytes = maximum_bytes
        self.bytes = 0
        self.items = OrderedDict()
        self.lock = threading.RLock()

    def evaluate(self, key, workspace, engine, definition):
        with self.lock:
            if key in self.items:
                self.items.move_to_end(key)
                return self.items[key][0]
            result = tables.evaluate_table(workspace, engine, definition, 0, tables.MAX_ROWS)
            encoded = json.dumps(
                result, sort_keys=True, ensure_ascii=True, allow_nan=False
            ).encode()
            result = {**result, "snapshot_sha256": hashlib.sha256(encoded).hexdigest()}
            size = len(encoded)
            if size <= self.maximum_bytes:
                while self.items and self.bytes + size > self.maximum_bytes:
                    self.bytes -= self.items.popitem(last=False)[1][1]
                self.items[key] = (result, size)
                self.bytes += size
            return result


EVALUATIONS = EvaluationCache()


COMPARISON_FIELDS = {
    "n_a": "n (A)",
    "n_b": "n (B)",
    "pairs": "Complete pairs",
    "excluded_a": "Excluded (A)",
    "excluded_b": "Excluded (B)",
    "mean_a": "Mean (A)",
    "mean_b": "Mean (B)",
    "mean_difference": "Mean Δ (A − B)",
    "median_difference": "Median Δ (A − B)",
    "statistic": "Test statistic",
    "p_value": "Raw p",
    "adjusted_p_value": "Adjusted p",
    "confidence_interval": "Confidence interval (Δ)",
}


def scoped_definition(workspace, element, iteration, layout):
    definition = next((table for table in workspace.tables if table.id == element.table_id), None)
    if definition is None:
        raise ValueError("The saved report table is unavailable")
    if element.iterate and layout.batch.mode != "off":
        selected = list(dict.fromkeys(value for value in iteration["mapping"].values() if value))
        selected = selected or iteration["sample_ids"]
        if not selected:
            raise ValueError("The report table has no resolved batch acquisitions")
        definition = definition.model_copy(update={"group_id": None, "sample_ids": selected})
    if element.table_view == "pivot" and definition.pivot is None:
        raise ValueError("This saved table has no pivot; configure it in Statistics first")
    if element.table_view == "comparisons" and definition.comparison is None:
        raise ValueError("This saved table has no comparison; configure it in Statistics first")
    return definition


def dataset(workspace, engine, element, iteration, layout, scientific_key=None):
    definition = scoped_definition(workspace, element, iteration, layout)
    scientific_key = scientific_key or report_cache.workspace_key(workspace, engine)
    key = digest([scientific_key, definition.model_dump()])
    result = EVALUATIONS.evaluate(key, workspace, engine, definition)
    source_columns = {column["id"]: column for column in result["columns"]}
    requested = element.column_ids
    if set(requested) - source_columns.keys():
        raise ValueError("A selected report table column is unavailable")
    if element.table_view == "data":
        columns = (
            [source_columns[i] for i in requested]
            if requested
            else [c for c in result["columns"] if not c["hidden"]]
        )
        fixed = [{"id": "sample", "name": "Sample"}, {"id": "population", "name": "Population"}]
        rows = result["rows"]
        description = "Live values"
        relevant = {column["id"] for column in columns}
    elif element.table_view == "pivot":
        pivot = result["pivot"]
        if set(requested) - set(definition.pivot.measures):
            raise ValueError("Choose report measures from the saved pivot's numeric measures")
        columns = [
            {**c, "decimals": source_columns[c["measure"]]["decimals"]}
            for c in pivot["columns"]
            if not requested or c["measure"] in requested
        ]
        if requested:
            columns.sort(
                key=lambda c: (
                    json.dumps(c["dimension"], ensure_ascii=True),
                    requested.index(c["measure"]),
                )
            )
        fixed = pivot["row_columns"] or [{"id": "summary", "name": "Scope"}]
        rows = pivot["rows"]
        description = f"Pivot: {pivot['aggregation']} · n = finite contributing rows"
        relevant = (
            {c["measure"] for c in columns}
            | set(definition.pivot.rows)
            | set(definition.pivot.columns)
        )
    else:
        if set(requested) - set(definition.comparison.measures):
            raise ValueError("Choose report measures from the saved comparison's measures")
        columns = [
            {"id": field, "name": COMPARISON_FIELDS[field], "decimals": 4}
            for field in element.comparison_fields
        ]
        fixed = [{"id": "column", "name": "Measure"}]
        rows = [r for r in result["comparisons"] if not requested or r["column_id"] in requested]
        if requested:
            rows.sort(key=lambda r: requested.index(r["column_id"]))
        settings = definition.comparison
        description = (
            f"{settings.method} · A={json.dumps(settings.group_a, ensure_ascii=False)} · "
            f"B={json.dumps(settings.group_b, ensure_ascii=False)} · {settings.adjustment} · "
            f"{settings.confidence_level * 100:g}% CI · Δ = A − B"
        )
        relevant = set(settings.measures) | {settings.group_column, settings.pair_column}
    if not columns:
        # An empty pivot cohort has no generated measure columns, but still has a
        # printable empty page. A deliberate empty selection is rejected otherwise.
        if element.table_view != "pivot" or rows:
            raise ValueError("Select at least one report table measure")
    statuses = {}
    if element.table_view != "data":
        for row in result["rows"]:
            for identifier, message in row["status"].items():
                if identifier not in relevant:
                    continue
                record = statuses.setdefault(
                    (identifier, message),
                    {"message": message, "column_id": identifier, "affected_rows": 0},
                )
                record["affected_rows"] += 1
    return dict(
        definition=result["definition"],
        columns=columns,
        fixed_columns=fixed,
        rows=rows,
        description=description,
        notices=result["notices"],
        input_status=list(statuses.values()),
        provenance=result["provenance"],
        input_rows=result["total_rows"],
        snapshot_sha256=digest(
            [result["snapshot_sha256"], element.table_view, requested, element.comparison_fields]
        ),
    )


def metrics(element, height):
    return report_table_geometry.metrics(element, height)


def pagination(data, element, height):
    geometry = report_table_geometry.layout(data, element, height)
    vertical = len(geometry["row_windows"])
    horizontal = len(geometry["column_windows"])
    return dict(
        table_view=element.table_view,
        total_rows=len(data["rows"]),
        total_columns=len(data["columns"]),
        fixed_columns=data["fixed_columns"],
        rows_per_page=max(window["count"] for window in geometry["row_windows"]),
        columns_per_page=element.columns_per_page,
        row_start=element.row_start,
        column_start=element.column_start,
        vertical_pages=vertical,
        horizontal_pages=horizontal,
        segments=vertical * horizontal if element.auto_paginate else 1,
        snapshot_sha256=data["snapshot_sha256"],
        input_rows=data["input_rows"],
        row_windows=geometry["row_windows"],
        column_windows=geometry["column_windows"],
        columns=geometry["columns"],
        header_height_mm=geometry["metrics"]["header_height"],
        unused_geometry=report_table_geometry.unused_positions(data, element),
    )


def window(element, paging, continuation):
    if not element.auto_paginate:
        return element
    if continuation >= paging["segments"]:
        return None
    row, column = divmod(continuation, paging["horizontal_pages"])
    row_window = paging["row_windows"][row]
    column_window = paging["column_windows"][column]
    return element.model_copy(
        update={
            "row_start": row_window["start"],
            "row_count": max(1, row_window["count"]),
            "column_start": column_window["start"],
            "columns_per_page": max(1, column_window["count"]),
        }
    )


def formatted(value, decimals=2):
    if value is None:
        return "Undefined"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(formatted(v, decimals) for v in value) + "]"
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            return "Undefined"
        if abs(value) >= 1e10 or (value and abs(value) < 10 ** (-decimals - 3)):
            return format(value, ".6g")
        return format(value, f",.{decimals}f")
    return str(value)


def severity(message):
    lower = message.lower()
    return (
        "stale"
        if "stale" in lower
        else "error"
        if any(word in lower for word in ("missing", "unavailable", "ambiguous", "not fitted"))
        else "warning"
    )


def probability(value, decimals):
    if value == 0:
        return "Below numerical resolution"
    if isinstance(value, (int, float)) and 0 < value < 10 ** (-decimals):
        return format(value, ".6g")
    return formatted(value, decimals)


def panel(workspace, engine, element, iteration, layout, width, height):
    data = dataset(workspace, engine, element, iteration, layout)
    geometry = report_table_geometry.configuration(element)
    total_columns = len(data["fixed_columns"]) + len(data["columns"])
    size = report_table_geometry.metrics(element, height, total_columns)
    row_frame = report_table_geometry.row_window(
        element, size, len(data["rows"]), total_columns, element.row_start
    )
    column_frame = report_table_geometry.column_window(data, element, element.column_start)
    cell_styles = report_table_geometry.styles(element)
    columns = data["columns"][element.column_start : element.column_start + column_frame["count"]]
    visible = data["rows"][element.row_start : element.row_start + row_frame["count"]]
    all_columns = [*data["fixed_columns"], *columns]
    svg = report_svg.node("svg", viewBox=f"0 0 {width} {height}", width=width, height=height)
    problems = [
        {"message": v, "severity": severity(v), "element_id": element.id} for v in data["notices"]
    ]
    problems.extend(
        {**status, "severity": severity(status["message"]), "element_id": element.id}
        for status in data["input_status"]
    )
    clipped = 0
    invisible = 0

    def cell(value, x, y, cell_width, cell_height, row, column, *, color=None, header=False):
        nonlocal clipped, invisible
        style = cell_styles.get((row, column))
        text = report_table_geometry.typography(element, style, header=header)
        if style and style.background:
            report_svg.node(
                "rect", svg, x=x, y=y, width=cell_width, height=cell_height, fill=style.background
            )
        content_width = max(0.1, cell_width - geometry.padding_x_mm * 2)
        content_height = max(0, cell_height - geometry.padding_y_mm * 2)
        line_height = text["font"] * text["line_spacing"]
        lines = max(0, int((content_height + 1e-8) / line_height))
        group = report_svg.node(
            "svg",
            svg,
            x=x + geometry.padding_x_mm,
            y=y,
            width=content_width,
            height=cell_height,
            overflow="hidden",
            data_table_row="header" if header else row,
            data_table_column=column,
        )
        wrapped = report_svg.lines(value, content_width, text["font"], text["family"])
        if len(wrapped) > lines:
            if not lines and any(wrapped):
                invisible += 1
            else:
                clipped += 1
            wrapped = wrapped[:lines]
            if wrapped:
                wrapped[-1] = wrapped[-1][:-1] + "…"
        align = style.align if style and style.align is not None else geometry.align
        vertical_align = (
            style.vertical_align
            if style and style.vertical_align is not None
            else geometry.vertical_align
        )
        remaining = max(0, content_height - len(wrapped) * line_height)
        top = (
            remaining / 2
            if vertical_align == "middle"
            else remaining
            if vertical_align == "bottom"
            else 0
        )
        text_x = (
            content_width / 2 if align == "center" else content_width if align == "right" else 0
        )
        anchor = {"left": "start", "center": "middle", "right": "end"}[align]
        for index, label_value in enumerate(wrapped):
            report_svg.label(
                group,
                text_x,
                geometry.padding_y_mm + top + (index + 1) * line_height - text["font"] * 0.1,
                label_value,
                text["font"],
                color or (style.color if style and style.color else element.color),
                text_anchor=anchor,
                font_family=text["family"],
                font_weight=text["weight"],
                font_style=text["style"],
                text_decoration=text["decoration"],
            )
        report_svg.node("title", group).text = report_svg.clean_text(value)

    report_svg.node(
        "rect", svg, width=column_frame["width_mm"], height=size["header_height"], fill="#eaf0f5"
    )
    for index, column in enumerate(all_columns):
        cell(
            column["name"],
            column_frame["columns"][index]["left"],
            0,
            column_frame["columns"][index]["width_mm"],
            size["header_height"],
            None,
            column_frame["columns"][index]["index"],
            header=True,
        )
    for index, row in enumerate(visible):
        statuses = {}
        if element.table_view == "data":
            values = [row["sample"], row["population"]] + [
                formatted(row["values"].get(c["id"]), c["decimals"]) for c in columns
            ]
            statuses = {
                c["id"]: row["status"][c["id"]] for c in columns if c["id"] in row["status"]
            }
        elif element.table_view == "pivot":
            values = [
                json.dumps(v, ensure_ascii=False) if v is not None else "(missing)"
                for v in row["group"]
            ] or ["All selected rows"]
            values += [
                formatted(row["values"].get(c["id"]), c["decimals"])
                + (f"\n(n={row['counts'][c['id']]})" if element.pivot_counts else "")
                for c in columns
            ]
        else:
            values = [row["column"]] + [
                (probability if c["id"] in {"p_value", "adjusted_p_value"} else formatted)(
                    row.get(c["id"]),
                    0
                    if c["id"] in {"n_a", "n_b", "pairs", "excluded_a", "excluded_b"}
                    else c["decimals"],
                )
                for c in columns
            ]
            if row["issue"]:
                statuses = {c["id"]: row["issue"] for c in columns}
            else:
                statuses = {
                    c["id"]: "The test returned zero below floating-point p-value resolution"
                    for c in columns
                    if c["id"] in {"p_value", "adjusted_p_value"} and row[c["id"]] == 0
                }
        row_geometry = row_frame["rows"][index]
        y = row_geometry["top"]
        if index % 2:
            report_svg.node(
                "rect",
                svg,
                y=y,
                width=column_frame["width_mm"],
                height=row_geometry["height_mm"],
                fill="#f6f9fc",
            )
        for position, (column, value) in enumerate(zip(all_columns, values, strict=True)):
            status = statuses.get(column["id"])
            cell(
                str(value) + (" *" if status else ""),
                column_frame["columns"][position]["left"],
                y,
                column_frame["columns"][position]["width_mm"],
                row_geometry["height_mm"],
                row_geometry["index"],
                column_frame["columns"][position]["index"],
                color="#b03c35" if status else None,
            )
        for identifier, message in statuses.items():
            problems.append(
                dict(
                    message=message,
                    severity=severity(message),
                    element_id=element.id,
                    row_id=row.get("id", row.get("column_id")),
                    column_id=identifier,
                )
            )
    if not visible:
        cell("No selected rows", 0, size["header_height"], width, size["row_height"], "empty", 0)
    unused = report_table_geometry.unused_positions(data, element)
    if any(unused.values()):
        problems.append(
            dict(
                message="Some table formatting positions are outside the current result; "
                "review row and column formatting after changing the cohort or table view",
                severity="warning",
                element_id=element.id,
                unused_geometry=unused,
            )
        )
    if invisible:
        problems.append(
            dict(
                message=f"{invisible} table headings or cells have no room for text; "
                "increase row/heading heights or reduce font size/padding. "
                "Full values remain in the source manifest",
                severity="warning",
                element_id=element.id,
            )
        )
    if not element.auto_paginate:
        if element.row_start + len(visible) < len(data["rows"]):
            problems.append(
                dict(
                    message="Additional table rows are outside this panel; "
                    "enable automatic continuation to export them",
                    severity="warning",
                    element_id=element.id,
                )
            )
        if element.column_start + len(columns) < len(data["columns"]):
            problems.append(
                dict(
                    message="Additional table columns are outside this panel; "
                    "enable automatic continuation to export them",
                    severity="warning",
                    element_id=element.id,
                )
            )
    if clipped:
        problems.append(
            dict(
                message=f"{clipped} table headings or cells are abbreviated with an ellipsis; "
                "enlarge the panel or show fewer columns. "
                "Full values remain in the source manifest",
                severity="warning",
                element_id=element.id,
            )
        )
    start = element.row_start + 1 if visible else 0
    col_start = element.column_start + 1 if columns else 0
    # Footers use the same explicit ellipsis convention as cells; long group
    # values and method context remain complete in the manifest and SVG title.
    footer = report_svg.lines(data["description"], width, 2.3, element.font_family)
    description = footer[0] if footer else ""
    if len(footer) > 1:
        description = description[:-1] + "…"
        problems.append(
            dict(
                message="Table method context is abbreviated; "
                "full context remains in the source manifest",
                severity="warning",
                element_id=element.id,
            )
        )
    typography = dict(
        font_family=element.font_family,
        font_weight=element.font_weight,
        font_style=element.font_style,
        text_decoration=element.text_decoration,
    )
    label = report_svg.label(svg, 0, height - 5.5, description, 2.3, "#52677e", **typography)
    report_svg.node("title", label).text = report_svg.clean_text(data["description"])
    report_svg.label(
        svg,
        0,
        height - 1,
        f"Rows {start}–{element.row_start + len(visible)} of {len(data['rows'])} "
        f"· columns {col_start}–{element.column_start + len(columns)} of {len(data['columns'])}"
        + (" · * see source issues" if problems else ""),
        2.3,
        "#52677e",
        **typography,
    )
    return (
        svg,
        dict(
            kind="table",
            table_view=element.table_view,
            table_id=element.table_id,
            definition=data["definition"],
            description=data["description"],
            fixed_columns=data["fixed_columns"],
            columns=columns,
            physical_geometry=dict(
                header_height_mm=size["header_height"],
                columns=column_frame["columns"],
                rows=row_frame["rows"],
                abbreviated_cells=clipped,
                invisible_text_cells=invisible,
            ),
            offset=element.row_start,
            column_offset=element.column_start,
            requested_rows=element.row_count,
            displayed_rows=len(visible),
            total_rows=len(data["rows"]),
            total_columns=len(data["columns"]),
            input_rows=data["input_rows"],
            rows=visible,
            snapshot_sha256=data["snapshot_sha256"],
            provenance=data["provenance"],
            abbreviated_cells=clipped,
        ),
        problems,
    )
