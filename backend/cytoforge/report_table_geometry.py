"""Physical table frames and sparse presentation styles; scientific values stay separate."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from collections import OrderedDict

from .models import ReportTableGeometry

PT_MM = 25.4 / 72
_CACHE = OrderedDict()
_CACHE_BYTES = 0
_CACHE_LIMIT = 8 * 1024**2
_LOCK = threading.RLock()


def configuration(element):
    return element.table_geometry or ReportTableGeometry()


def styles(element):
    return {(style.row, style.column): style for style in configuration(element).cell_styles}


def typography(element, style=None, *, header=False):
    def inherited(key, default):
        value = getattr(style, key, None)
        return default if value is None else value

    # Historical table text uses 80% of the object's caption size. A cell's
    # explicit point size is physical and overrides that inherited size.
    font = inherited("font_size_pt", None)
    return dict(
        font=font * PT_MM if font is not None else element.font_size_pt * PT_MM * 0.8,
        family=inherited("font_family", element.font_family),
        weight=inherited(
            "font_weight", max(700, element.font_weight) if header else element.font_weight
        ),
        style=inherited("font_style", element.font_style),
        decoration=inherited("text_decoration", element.text_decoration),
        line_spacing=inherited("line_spacing", element.line_spacing),
    )


def metrics(element, height, total_columns=None):
    geometry = configuration(element)
    body = typography(element)
    line_height = body["font"] * body["line_spacing"]
    header_line = max(
        [line_height]
        + [
            (value := typography(element, style, header=True))["font"] * value["line_spacing"]
            for style in geometry.cell_styles
            if style.row is None and (total_columns is None or style.column < total_columns)
        ]
    )
    header = geometry.header_height_mm or header_line * 3 + geometry.padding_y_mm * 2
    row = geometry.row_height_mm or line_height * 2 + geometry.padding_y_mm * 2
    reserved = max(10, line_height * 2 + 2)
    body_height = max(0, height - header - reserved)
    fit = max(0, int(body_height / row))
    return dict(
        font=body["font"],
        line_height=line_height,
        header_height=header,
        row_height=row,
        body_height=body_height,
        reserved_height=reserved,
        capacity=min(fit, element.row_count),
    )


def row_sizes(element, total_columns):
    geometry = configuration(element)
    # Automatic heights accommodate fonts anywhere on the same logical row,
    # so every horizontal continuation uses identical vertical boundaries.
    result = {}
    if geometry.row_height_mm is None:
        for style in geometry.cell_styles:
            if style.row is None or style.column >= total_columns:
                continue
            value = typography(element, style)
            needed = value["font"] * value["line_spacing"] * 2 + geometry.padding_y_mm * 2
            result[style.row] = max(result.get(style.row, 0), needed)
    result.update(geometry.row_heights_mm)
    return result


def unused_positions(data, element):
    geometry = configuration(element)
    columns = len(data["fixed_columns"]) + len(data["columns"])
    rows = len(data["rows"])
    return dict(
        column_widths=[index for index in geometry.column_widths_mm if index >= columns],
        row_heights=[index for index in geometry.row_heights_mm if index >= rows],
        cell_styles=[
            dict(row=style.row, column=style.column)
            for style in geometry.cell_styles
            if style.column >= columns or (style.row is not None and style.row >= rows)
        ],
    )


def row_window(element, size, total_rows, total_columns, start, heights=None):
    geometry = configuration(element)
    heights = row_sizes(element, total_columns) if heights is None else heights
    selected = []
    used = 0
    for index in range(start, min(total_rows, start + element.row_count)):
        height = heights.get(index, size["row_height"])
        if index not in geometry.row_heights_mm:
            height = max(height, size["row_height"])
        if height > size["body_height"] + 1e-8:
            if not selected:
                raise ValueError(
                    f"Report table row {index + 1} does not fit below its heading. "
                    "Reduce this row's height/font size or increase the panel height"
                )
            break
        if used + height > size["body_height"] + 1e-8:
            break
        selected.append(dict(index=index, top=size["header_height"] + used, height_mm=height))
        used += height
    return dict(start=start, count=len(selected), height_mm=used, rows=selected)


def column_frame(data, element, start, count):
    geometry = configuration(element)
    fixed = len(data["fixed_columns"])
    indices = [*range(fixed), *range(fixed + start, fixed + start + count)]
    prescribed = [
        geometry.column_widths_mm.get(index, geometry.column_width_mm) for index in indices
    ]
    known = sum(value for value in prescribed if value is not None)
    automatic = prescribed.count(None)
    minimum = geometry.padding_x_mm * 2 + 0.1
    if known > element.width_mm + 1e-8:
        return None
    if any(value is not None and value < minimum - 1e-8 for value in prescribed):
        raise ValueError(
            "Increase column widths or reduce horizontal padding to leave readable cell space"
        )
    remaining = element.width_mm - known
    if automatic and remaining / automatic < minimum - 1e-8:
        return None
    widths = [remaining / automatic if value is None else value for value in prescribed]
    cursor = 0
    frames = []
    for index, width in zip(indices, widths, strict=True):
        frames.append(dict(index=index, left=cursor, width_mm=width))
        cursor += width
    return dict(start=start, count=count, width_mm=cursor, columns=frames)


def column_window(data, element, start):
    count = min(element.columns_per_page, len(data["columns"]) - start)
    if count == 0:
        frame = column_frame(data, element, start, 0)
        if frame is not None:
            return frame
    for selected in range(count, 0, -1):
        frame = column_frame(data, element, start, selected)
        if frame is not None:
            return frame
    raise ValueError(
        f"Report measure column {start + 1} cannot fit beside the repeated heading columns. "
        "Reduce column widths/padding or increase the panel width"
    )


def build_layout(data, element, height):
    total_columns = len(data["fixed_columns"]) + len(data["columns"])
    size = metrics(element, height, total_columns)
    if element.auto_paginate and element.row_start >= len(data["rows"]) and data["rows"]:
        raise ValueError("The starting report row is beyond the selected results")
    if data["columns"] and element.column_start >= len(data["columns"]):
        raise ValueError("The starting report column is beyond the selected measures")
    if element.auto_paginate and size["body_height"] <= 0:
        raise ValueError(
            "Increase the table panel height or reduce its heading/font/line spacing "
            "to fit a heading and at least one row"
        )
    row_windows = []
    heights = row_sizes(element, total_columns)
    cursor = element.row_start
    while True:
        frame = row_window(element, size, len(data["rows"]), total_columns, cursor, heights)
        row_windows.append({key: value for key, value in frame.items() if key != "rows"})
        cursor += frame["count"]
        if not element.auto_paginate or cursor >= len(data["rows"]):
            break
        if not frame["count"]:
            raise ValueError("Increase this table panel's height to fit its next row")
        if len(row_windows) > 1024:
            raise ValueError("Report table continuation exceeds 1,024 row pages")
    column_windows = []
    cursor = element.column_start
    while True:
        frame = column_window(data, element, cursor)
        column_windows.append(frame)
        cursor += frame["count"]
        if not element.auto_paginate or cursor >= len(data["columns"]):
            break
    if len(row_windows) * len(column_windows) > 1024:
        raise ValueError("Report table continuation exceeds 1,024 output pages")
    return dict(
        metrics=size,
        row_windows=row_windows,
        column_windows=column_windows,
        columns=[
            dict(
                index=index,
                id=column["id"],
                name=column["name"],
                fixed=index < len(data["fixed_columns"]),
            )
            for index, column in enumerate([*data["fixed_columns"], *data["columns"]])
        ],
    )


def layout(data, element, height):
    global _CACHE_BYTES
    key = hashlib.sha256(
        json.dumps(
            [
                data["snapshot_sha256"],
                len(data["rows"]),
                len(data["columns"]),
                element.width_mm,
                height,
                element.row_count,
                element.columns_per_page,
                element.row_start,
                element.column_start,
                element.auto_paginate,
                element.font_size_pt,
                element.font_family,
                element.font_weight,
                element.font_style,
                element.text_decoration,
                element.line_spacing,
                configuration(element).model_dump(mode="json"),
            ],
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
        ).encode()
    ).hexdigest()
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return copy.deepcopy(cached[0])
    result = build_layout(data, element, height)
    byte_count = len(json.dumps(result, ensure_ascii=True).encode())
    if byte_count <= _CACHE_LIMIT:
        with _LOCK:
            previous = _CACHE.pop(key, None)
            if previous:
                _CACHE_BYTES -= previous[1]
            while _CACHE and _CACHE_BYTES + byte_count > _CACHE_LIMIT:
                _, (_, removed) = _CACHE.popitem(last=False)
                _CACHE_BYTES -= removed
            _CACHE[key] = (copy.deepcopy(result), byte_count)
            _CACHE_BYTES += byte_count
    return result
