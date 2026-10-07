"""Full-data report plots on shared axes, with explicit display normalization."""

from __future__ import annotations

import io
import json
import math
import threading
from functools import partial

import matplotlib
import numpy as np
from matplotlib.backends.backend_svg import FigureCanvasSVG
from matplotlib.figure import Figure
from matplotlib.patches import PathPatch
from matplotlib.path import Path

from .models import Transform
from .plot_coordinates import coordinate_dimension
from .plotting import plot_payload
from .report_backgates import HIGHLIGHT_COLOR, backgate_manifest
from .report_gate_style import (
    fill_gate,
    gate_fill_svg,
    gate_style_manifest,
    gate_width,
    show_gate_labels,
)
from .report_graph_typography import draw_legend, plot_layout, text_style, typography_manifest
from .report_sources import SourceAudit
from .report_svg import clean_text

_MPL_LOCK = threading.RLock()


def fraction(values, low, high):
    scale = max(abs(low), abs(high))
    if not scale or not low < high:
        raise ValueError("Report axes require a finite, increasing domain")
    return (np.asarray(values, dtype=float) / scale - low / scale) / (high / scale - low / scale)


def normalized_histogram(payload, normalization):
    counts = np.asarray(payload["counts"], dtype=float)
    reason = None
    with np.errstate(all="ignore"):
        if normalization == "percent_population":
            values = counts / payload["count"] * 100 if payload["count"] else counts * np.nan
            if not payload["count"]:
                reason = "Percent of an empty population is undefined"
        elif normalization == "unit_area":
            widths = np.diff(np.asarray(payload["edges"], dtype=float))
            values = counts / counts.sum() / widths if counts.sum() else counts * np.nan
            if not counts.sum():
                reason = "Unit-area density needs visible events"
            elif not np.all(np.isfinite(widths) & (widths > 0)):
                values[:] = np.nan
                reason = "Unit-area density is undefined for these representable bin widths"
        elif normalization == "peak":
            values = counts / counts.max() if counts.max() else counts * np.nan
            if not counts.max():
                reason = "Peak normalization needs visible events"
        else:
            values = counts
    if not np.all(np.isfinite(values)) and reason is None:
        reason = "A normalized density exceeds the finite numeric range"
    return [float(value) if math.isfinite(value) else None for value in values], reason


def source_payloads(workspace, engine, definition, layers):
    renderer = plot_payload
    if definition.pooled:
        from .virtual_groups import render

        renderer = partial(
            render, group_id=definition.group_id, sample_filter=definition.sample_filter
        )
    payloads = []
    xs, ys = definition.x_transform, definition.y_transform
    prototype = engine.sample(workspace, layers[0]["source_sample_id"])
    coordinate = next(
        (gate for gate in workspace.gates if gate.id == definition.coordinate_gate_id), None
    )

    def prototype_transform(name, axis, explicit=None):
        if explicit:
            return explicit.transform
        dimension = coordinate_dimension(coordinate, name, axis)
        channel = next((c for c in prototype.channels if c.name == name), None)
        return dimension.transform if dimension else channel.transform if channel else None

    xs = xs or prototype_transform(definition.x, 0, definition.x_dimension)
    ys = ys or prototype_transform(definition.y, 1, definition.y_dimension)
    view = definition.three_d
    if definition.mode == "3d":
        view = view.model_copy(
            update={
                "z_transform": view.z_transform or prototype_transform(view.z, 2, view.z_dimension),
                "color_transform": view.color_transform
                or prototype_transform(view.color_by, None, view.color_dimension),
                "size_transform": view.size_transform
                or prototype_transform(view.size_by, None, view.size_dimension),
            }
        )
    for layer in layers:
        payload = renderer(
            workspace,
            engine,
            layer["sample_id"],
            definition.x,
            definition.y,
            layer["gate_id"],
            layer["coordinate_gate_id"],
            xs,
            ys,
            definition.bins,
            definition.bounds,
            definition.mode,
            graph_options=definition.graph_options,
            backgate_id=layer.get("backgate_id"),
            three_d=view,
            x_dimension=definition.x_dimension,
            y_dimension=definition.y_dimension,
        )
        if not all(math.isfinite(value) for value in payload["bounds"]):
            raise ValueError("Automatic report axes exceed finite limits; choose explicit bounds")
        if xs is None:
            xs = Transform.model_validate(payload["x_transform"])
        if definition.y and ys is None:
            ys = Transform.model_validate(payload["y_transform"])
        payloads.append(payload)
    bounds = definition.bounds or [
        (min if index % 2 == 0 else max)(payload["bounds"][index] for payload in payloads)
        for index in range(6 if definition.mode == "3d" else 2 if definition.y is None else 4)
    ]
    if definition.mode == "3d":
        view = view.model_copy(
            update={
                field: getattr(view, field)
                or tuple(
                    (min if i == 0 else max)(payload[field][i] for payload in payloads)
                    for i in range(2)
                )
                for field in ("color_bounds", "size_bounds")
            }
        )
    for index, layer in enumerate(layers):
        if payloads[index]["bounds"] != bounds or definition.mode == "3d":
            payloads[index] = renderer(
                workspace,
                engine,
                layer["sample_id"],
                definition.x,
                definition.y,
                layer["gate_id"],
                layer["coordinate_gate_id"],
                xs,
                ys,
                definition.bins,
                bounds,
                definition.mode,
                graph_options=definition.graph_options,
                backgate_id=layer.get("backgate_id"),
                three_d=view,
                x_dimension=definition.x_dimension,
                y_dimension=definition.y_dimension,
            )
    return payloads, bounds


def density_patch(axes, payload, color, alpha):
    """Combine equally colored full-data bins into vector paths, without rasterization."""
    zebra = payload["mode"] == "zebra"
    field = payload.get("zebra_bands") if zebra else payload.get("density_field")
    counts = np.asarray(field if field is not None else payload["counts"]).reshape(
        payload["bins"], payload["bins"]
    )
    if not counts.max():
        return
    shades = (
        counts.astype(int)
        if zebra
        else np.ceil(np.log1p(counts) / np.log1p(counts.max()) * 24).astype(int)
    )
    maximum = payload["zebra_max_band"] if zebra else 24
    channels = np.array([int(color[i : i + 2], 16) for i in (1, 3, 5)]) / 255
    bins = payload["bins"]
    domain = payload.get("density_bounds", payload["bounds"])
    view = payload["bounds"]
    dx = fraction(domain[:2], *view[:2])
    dy = fraction(domain[2:], *view[2:])
    for shade in range(1, maximum + 1):
        rows, columns = np.nonzero(shades == shade)
        if not len(rows):
            continue
        vertices = np.empty((len(rows), 5, 2), dtype=float)
        vertices[:, :, 0] = (
            np.column_stack([columns, columns + 1, columns + 1, columns, columns]) / bins
        )
        vertices[:, :, 1] = np.column_stack([rows, rows, rows + 1, rows + 1, rows]) / bins
        vertices[:, :, 0] = dx[0] + vertices[:, :, 0] * (dx[1] - dx[0])
        vertices[:, :, 1] = dy[0] + vertices[:, :, 1] * (dy[1] - dy[0])
        codes = np.tile(
            [Path.MOVETO, Path.LINETO, Path.LINETO, Path.LINETO, Path.CLOSEPOLY], len(rows)
        )
        fill = 1 - (1 - channels) * (0.16 + 0.84 * shade / maximum)
        if (
            payload["mode"] in {"zebra", "pseudocolor"}
            or payload.get("graph_options", {}).get("palette") != "ocean"
        ):
            fill = palette_color(shade / maximum, payload["graph_options"]["palette"])
        axes.add_patch(
            PathPatch(
                Path(vertices.reshape(-1, 2), codes), facecolor=fill, edgecolor="none", alpha=alpha
            )
        )


def palette_color(value, palette):
    colors = (
        np.asarray(
            {
                "ocean": [
                    [28, 48, 73],
                    [38, 87, 132],
                    [39, 153, 173],
                    [49, 204, 168],
                    [175, 234, 158],
                    [255, 228, 147],
                ],
                "gray": [[40, 40, 40], [245, 245, 245]],
                "spectrum": [
                    [63, 28, 122],
                    [28, 87, 183],
                    [20, 173, 178],
                    [66, 191, 90],
                    [246, 220, 55],
                    [232, 86, 38],
                ],
                "viridis": [
                    [68, 1, 84],
                    [59, 82, 139],
                    [33, 145, 140],
                    [94, 201, 98],
                    [253, 231, 37],
                ],
            }[palette],
            dtype=float,
        )
        / 255
    )
    position = min(0.999999, max(0, value)) * (len(colors) - 1)
    index = int(position)
    return colors[index] + (colors[index + 1] - colors[index]) * (position - index)


def probability_artists(axes, payload, color):
    domain = payload["density_bounds"]
    bounds = payload["bounds"]
    for contour in payload["contours"]:
        for path in contour["paths"]:
            vertices = np.asarray(path)
            x = (1 - vertices[:, 0]) * domain[0] + vertices[:, 0] * domain[1]
            y = (1 - vertices[:, 1]) * domain[2] + vertices[:, 1] * domain[3]
            axes.plot(
                fraction(x, *bounds[:2]), fraction(y, *bounds[2:]), color=color, linewidth=0.45
            )
    points = np.asarray(payload["outlier_points"]).reshape(-1, 2)
    if len(points):
        axes.scatter(
            fraction(points[:, 0], *bounds[:2]),
            fraction(points[:, 1], *bounds[2:]),
            s=1,
            color=color,
            alpha=0.6,
            edgecolors="none",
        )


def gate_artists(axes, payload, bounds, histogram, definition, seen=None):
    for overlay in payload["overlays"]:
        if seen is not None and definition.graph_options.gate_style is not None:
            key = json.dumps(overlay, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
        label = overlay["name"] + (" · magnetic" if overlay.get("magnetic") else "")
        color = overlay["color"]
        if not isinstance(color, str) or len(color) != 7 or not color.startswith("#"):
            color = "#65788c"
        try:
            int(color[1:], 16)
        except ValueError:
            color = "#65788c"
        fill_gate(axes, overlay, bounds, histogram, definition, color, fraction)
        width = gate_width(definition, 0.6)
        if overlay["kind"] == "range":
            limits = overlay["bounds"]
            axis = overlay["axis"]
            values = fraction(limits, *(bounds[:2] if axis == "x" else bounds[2:]))
            for value in values:
                if axis == "x":
                    axes.axvline(value, color=color, linewidth=width, alpha=0.9)
                elif not histogram:
                    axes.axhline(value, color=color, linewidth=width, alpha=0.9)
            anchor = float(np.mean(values))
            if 0 <= anchor <= 1 and show_gate_labels(definition):
                axes.text(
                    anchor if axis == "x" else 0.02,
                    0.94 if axis == "x" else anchor,
                    clean_text(label),
                    **text_style(definition, "gate_labels", 5, color),
                    clip_on=True,
                    ha="center" if axis == "x" else "left",
                    transform=axes.transAxes,
                    bbox=dict(facecolor="white", alpha=0.75, edgecolor="none", pad=0.3),
                )
        elif not histogram and overlay["kind"] in {"spider", "curly"}:
            for segment in overlay["segments"]:
                points = np.asarray(segment, dtype=float)
                axes.plot(
                    fraction(points[:, 0], *bounds[:2]),
                    fraction(points[:, 1], *bounds[2:]),
                    color=color,
                    linewidth=width,
                )
            if overlay.get("center") and show_gate_labels(definition):
                center = overlay["center"]
                axes.text(
                    float(fraction([center[0]], *bounds[:2])[0]),
                    float(fraction([center[1]], *bounds[2:])[0]),
                    clean_text(label),
                    **text_style(definition, "gate_labels", 5, color),
                    clip_on=True,
                )
        elif not histogram:
            vertices = np.asarray(overlay["vertices"], dtype=float)
            if len(vertices):
                x = fraction(vertices[:, 0], *bounds[:2])
                y = fraction(vertices[:, 1], *bounds[2:])
                axes.plot(np.r_[x, x[:1]], np.r_[y, y[:1]], color=color, linewidth=width)
                for ring in overlay.get("holes", []):
                    hole = np.asarray(ring, dtype=float)
                    hx = fraction(hole[:, 0], *bounds[:2])
                    hy = fraction(hole[:, 1], *bounds[2:])
                    axes.plot(np.r_[hx, hx[:1]], np.r_[hy, hy[:1]], color=color, linewidth=width)
                if show_gate_labels(definition):
                    axes.text(
                        float(np.mean(x)),
                        float(np.mean(y)),
                        clean_text(label),
                        **text_style(definition, "gate_labels", 5, color),
                        clip_on=True,
                        ha="center",
                        bbox=dict(facecolor="white", alpha=0.75, edgecolor="none", pad=0.3),
                    )
        magnetic = overlay.get("magnetic")
        if magnetic and magnetic.get("arrow") and magnetic["distance"] > 0:
            arrow = magnetic["arrow"]
            points = []
            for key in ("from", "to"):
                point = [0.5, 0.5]
                for axis, value in zip(arrow["axes"], arrow[key], strict=True):
                    point[axis] = float(fraction([value], *bounds[2 * axis : 2 * axis + 2])[0])
                points.append(point)
            axes.annotate(
                "",
                xy=points[1],
                xytext=points[0],
                xycoords="axes fraction",
                arrowprops={
                    "arrowstyle": "->",
                    "color": color,
                    "linewidth": gate_width(definition, 0.7),
                },
                annotation_clip=True,
            )


def magnetic_provenance(workspace, engine, sample, payload, gate, show_gates, population_ids=()):
    if payload.get("pooled"):
        records, seen = [], set()
        for row in payload["pooled"]["sources"]:
            identifiers = {row["population_id"]}
            if any(population_ids):
                for source in payload["pooled_source"]["sources"]:
                    if source["sample_id"] == row["sample_id"]:
                        identifiers.update(source["populations"])
            if show_gates:
                identifiers.update(
                    o["pooled_gate_id"]
                    for o in payload.get("overlays", [])
                    if o["pooled_source_id"] == row["sample_id"] and o.get("magnetic")
                )
            member = engine.sample(workspace, row["sample_id"])
            for original in workspace.gates:
                if (
                    original.id not in identifiers
                    or original.magnetic is None
                    or original.id in seen
                ):
                    continue
                seen.add(original.id)
                moved, report = engine.resolve_gate(workspace, member, original)
                records.append(
                    dict(
                        id=original.id,
                        sample_id=member.id,
                        sample_name=member.name,
                        anchor_gate=original.model_dump(),
                        resolved_gate=moved.model_dump(),
                        resolution=report,
                    )
                )
        return {"magnetic_gates": records} if records else {}
    ids = {gate.id} if gate and gate.magnetic is not None else set()
    pending = [identifier for identifier in population_ids if identifier]
    gates = {item.id: item for item in workspace.gates}
    visited = set()
    while pending:
        identifier = pending.pop()
        if identifier in visited:
            continue
        visited.add(identifier)
        original = gates[identifier]
        if original.magnetic is not None:
            ids.add(identifier)
        pending.extend(value for value in [original.parent_id, *original.operands] if value)
    if show_gates:
        ids.update(
            o["id"]
            for o in [*payload.get("overlays", []), *payload.get("boxes", [])]
            if o.get("magnetic")
        )
    if not ids:
        return {}
    records = []
    for original in workspace.gates:
        if original.id not in ids or original.magnetic is None:
            continue
        moved, report = engine.resolve_gate(workspace, sample, original)
        records.append(
            dict(
                id=original.id,
                anchor_gate=original.model_dump(),
                resolved_gate=moved.model_dump(),
                resolution=report,
            )
        )
    return {"magnetic_gates": records}


def figure(workspace, engine, definition, layers, width, height):
    if width < 25 or height < 25:
        raise ValueError("A scientific figure needs at least 25 × 25 mm")
    if definition.mode == "3d":
        from .report_three_dimensional import figure as figure_3d

        return figure_3d(workspace, engine, definition, layers, width, height)
    payloads, bounds = source_payloads(workspace, engine, definition, layers)
    histogram = definition.y is None
    cdf = definition.mode == "cdf"
    normalized = (
        [(payload["cdf_percent"], payload["cdf_undefined_reason"]) for payload in payloads]
        if cdf
        else [normalized_histogram(payload, definition.normalization) for payload in payloads]
        if histogram
        else []
    )
    finite_values = [value for values, _ in normalized for value in values if value is not None]
    ymax = 100 if cdf else max(finite_values, default=1) or 1
    samples = {sample.id: sample for sample in workspace.samples}
    gates = {gate.id: gate for gate in workspace.gates}
    manifest = []
    audit = SourceAudit(workspace)
    for index, (layer, payload) in enumerate(zip(layers, payloads, strict=True)):
        sample = samples[layer["sample_id"]]
        gate = gates.get(layer["gate_id"])
        sample_name = payload["pooled"]["group_name"] if definition.pooled else sample.name
        label = layer["label"] or f"{sample_name} · {gate.name if gate else 'All events'}"
        manifest.append(
            dict(
                **{**layer, "label": label},
                **backgate_manifest(workspace, layer, payload),
                **magnetic_provenance(
                    workspace,
                    engine,
                    sample,
                    payload,
                    gate,
                    definition.show_gates,
                    population_ids=(layer.get("backgate_id"),),
                ),
                sample_sha256=sample.sha256,
                population_count=payload["count"],
                finite_count=payload["finite_count"],
                visible_count=payload["visible_count"],
                outside_view=payload["finite_count"] - payload["visible_count"],
                displayed_count=payload.get("displayed_count", payload["visible_count"]),
                x_transform=payload["x_transform"],
                y_transform=payload["y_transform"],
                axes=payload["axes"],
                gate=gate.model_dump() if gate else None,
                compensation=next(
                    (
                        matrix.model_dump()
                        for matrix in workspace.compensations
                        if matrix.id == sample.compensation_id
                    ),
                    None,
                ),
                counts=payload.get("counts"),
                edges=payload.get("edges"),
                displayed_values=normalized[index][0] if histogram else None,
                undefined_reason=normalized[index][1] if histogram else None,
                scatter_seed=42 if definition.mode == "scatter" else None,
                graph_options=payload["graph_options"],
                cdf_denominator=payload.get("cdf_denominator"),
                cdf_counts=payload.get("cdf_counts"),
                cdf_below_view=payload.get("cdf_below_view"),
                cdf_above_view=payload.get("cdf_above_view"),
                probability_denominator=payload.get("probability_denominator"),
                probability_levels=payload.get("probability_levels"),
                probability_method=payload.get("probability_method"),
                density_bounds=payload.get("density_bounds"),
                density_count=payload.get("density_count"),
                contour_geometry=payload.get("contour_geometry"),
                contour_geometry_levels=[
                    dict(threshold=contour["threshold"], geometry=contour["geometry"])
                    for contour in payload.get("contours", [])
                ],
                contour_vertices=payload.get("contour_vertices"),
                contours_truncated=payload.get("contours_truncated"),
                outlier_count=payload.get("outlier_count"),
                outlier_visible_count=payload.get("outlier_visible_count"),
                outlier_displayed_count=payload.get("outlier_displayed_count"),
                outlier_sampling=payload.get("outlier_sampling"),
                point_sampling=payload.get("point_sampling"),
                source=payload["pooled_source"]
                if definition.pooled
                else audit.closure(
                    sample.id,
                    tuple(name for name in (definition.x, definition.y) if name),
                    (
                        layer["gate_id"],
                        layer["coordinate_gate_id"],
                        layer.get("backgate_id"),
                        *[
                            overlay["id"]
                            for overlay in payload["overlays"]
                            if definition.show_gates
                        ],
                    ),
                    dimensions=payload["axes"],
                ),
            )
        )
    with (
        _MPL_LOCK,
        matplotlib.rc_context(
            {
                "svg.fonttype": "path",
                "svg.hashsalt": "cytoforge-report",
                "font.family": "DejaVu Sans",
                "font.size": 7,
                "axes.labelcolor": "#233449",
                "axes.edgecolor": "#798da2",
                "xtick.color": "#52677e",
                "ytick.color": "#52677e",
                "text.usetex": False,
            }
        ),
    ):
        fig = Figure(figsize=(width / 25.4, height / 25.4), facecolor="white")
        canvas = FigureCanvasSVG(fig)
        y_labels = (
            [engine.number(float(value * ymax)) for value in np.linspace(0, 1, 5)]
            if histogram
            else [tick["label"] for tick in payloads[0]["ticks_y"]]
        )
        position, legend_rows, legend_row_mm = plot_layout(
            definition, width, height, len(layers), y_labels
        )
        axes = fig.add_axes(position)
        axes.set_xlim(0, 1)
        axes.set_ylim(0, 1.06 if histogram and not cdf else 1)
        axes.grid(color="#e3eaf0", linewidth=0.4, zorder=-10)
        axes.spines[["top", "right"]].set_visible(False)
        axes.tick_params(length=2, width=0.4, labelsize=6)
        ticks = payloads[0]["ticks_x"]
        axes.set_xticks(
            fraction([tick["value"] for tick in ticks], *bounds[:2]),
            [tick["label"] for tick in ticks],
        )
        axes.set_xlabel(
            clean_text(definition.x),
            labelpad=3,
            **text_style(definition, "axis_labels", 7, "#233449"),
        )
        if histogram:
            fractions = np.linspace(0, 1, 5)
            axes.set_yticks(fractions, [engine.number(float(value * ymax)) for value in fractions])
            axes.set_ylabel(
                "Cumulative frequency (%)"
                if cdf
                else {
                    "count": "Events",
                    "percent_population": "% of population",
                    "unit_area": "Unit-area density",
                    "peak": "Relative peak",
                }[definition.normalization],
                labelpad=3,
                **text_style(definition, "axis_labels", 7, "#233449"),
            )
        else:
            ticks = payloads[0]["ticks_y"]
            axes.set_yticks(
                fraction([tick["value"] for tick in ticks], *bounds[2:]),
                [tick["label"] for tick in ticks],
            )
            axes.set_ylabel(
                clean_text(definition.y),
                labelpad=3,
                **text_style(definition, "axis_labels", 7, "#233449"),
            )
        tick_style = text_style(definition, "tick_labels", 6, "#52677e")
        for label in [*axes.get_xticklabels(), *axes.get_yticklabels()]:
            label.update(tick_style)
        shown_gates = set()
        for index, (layer, payload) in enumerate(zip(layers, payloads, strict=True)):
            if cdf:
                values = np.asarray(
                    [v / 100 if v is not None else np.nan for v in payload["cdf_percent"]]
                )
                axes.plot(
                    fraction(payload["edges"], *bounds[:2]),
                    values,
                    color=layer["color"],
                    linewidth=0.9,
                )
            elif histogram:
                values = np.array(
                    [
                        value / ymax if value is not None else np.nan
                        for value in normalized[index][0]
                    ]
                )
                axes.stairs(
                    values,
                    np.linspace(0, 1, len(values) + 1),
                    baseline=None,
                    color=layer["color"],
                    linewidth=0.9,
                )
            elif definition.mode == "scatter":
                points = np.asarray(payload["points"], dtype=float).reshape(-1, 2)
                if len(points):
                    axes.scatter(
                        fraction(points[:, 0], *bounds[:2]),
                        fraction(points[:, 1], *bounds[2:]),
                        s=1.2,
                        alpha=0.45,
                        color=layer["color"],
                        edgecolors="none",
                    )
            elif definition.mode != "contour":
                density_patch(axes, payload, layer["color"], 1 if len(layers) == 1 else 0.55)
            if definition.mode in {"contour", "zebra"}:
                probability_artists(axes, payload, layer["color"])
            backgate_points = np.asarray(payload.get("backgate_points", []), dtype=float).reshape(
                -1, 2
            )
            if len(backgate_points):
                highlight = axes.scatter(
                    fraction(backgate_points[:, 0], *bounds[:2]),
                    np.full(len(backgate_points), 0.015)
                    if histogram
                    else fraction(backgate_points[:, 1], *bounds[2:]),
                    s=12 if histogram else 3.24,
                    marker="|" if histogram else "s",
                    linewidths=0.75 if histogram else 0,
                    color=HIGHLIGHT_COLOR,
                    alpha=0.88,
                    zorder=3,
                )
                highlight.set_gid(f"cytoforge-backgate-{index}")
            if definition.show_gates:
                gate_artists(axes, payload, bounds, histogram, definition, shown_gates)
        if not any(
            payload["finite_count"] if cdf else payload["visible_count"] for payload in payloads
        ):
            axes.text(
                0.5,
                0.5,
                "No finite events inside these axes",
                ha="center",
                va="center",
                transform=axes.transAxes,
                fontsize=7,
                color="#52677e",
            )
        if any(reason for _, reason in normalized):
            axes.text(
                0.98,
                0.98,
                "Undefined normalization",
                ha="right",
                va="top",
                transform=axes.transAxes,
                fontsize=6,
                color="#a23230",
            )
        draw_legend(fig, definition, manifest, width, height, legend_rows, legend_row_mm)
        output = io.StringIO()
        canvas.print_svg(output, metadata={"Date": None, "Creator": "CytoForge"})
        rendered_svg = gate_fill_svg(output.getvalue(), axes)
        fig.clear()
    return rendered_svg, dict(
        kind="plot",
        mode=definition.mode,
        normalization=definition.normalization,
        bounds=bounds,
        bins=definition.bins,
        layers=manifest,
        **typography_manifest(definition),
        **gate_style_manifest(definition),
    )
