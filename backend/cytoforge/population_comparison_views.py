"""Bounded, immutable comparison plot data and portable statistics."""

from __future__ import annotations

import csv
import io
import json
import math
from html import escape

import numpy as np

from . import population_comparison as comparison
from .models import ComparisonPresentation
from .report_svg import clean_text


def plot_data(store, workspace, result, parameter_id, target_index=0):
    index = next((i for i, p in enumerate(result.request.parameters) if p.id == parameter_id), None)
    if index is None or not 0 <= target_index < len(result.request.inputs):
        raise ValueError("Select a parameter and target belonging to this comparison")
    arrays, _ = comparison.load_artifact(store, workspace.id, result)
    source = result.request.inputs[target_index]
    row = next(
        r
        for r in result.rows
        if r.role == "target"
        and r.parameter_id == parameter_id
        and comparison.key(r.source) == comparison.key(source)
    )
    control, target = arrays[f"control_{index}"], arrays[f"targets_{index}"][target_index]

    def fractions(counts):
        total = int(counts.sum())
        return counts / total if total else np.zeros(len(counts))

    c, t = fractions(control), fractions(target)
    return {
        "edges": arrays[f"edges_{index}"].tolist(),
        "control_counts": control.tolist(),
        "target_counts": target.tolist(),
        "control_fraction": c.tolist(),
        "target_fraction": t.tolist(),
        "control_cdf": np.cumsum(c).tolist(),
        "target_cdf": np.cumsum(t).tolist(),
        "difference": (t - c).tolist(),
        "individual_controls": [
            {
                "source": s.model_dump(),
                "counts": counts.tolist(),
                "fraction": fractions(counts).tolist(),
            }
            for s, counts in zip(
                result.request.controls, arrays[f"individual_{index}"], strict=True
            )
        ],
        "parameter": result.request.parameters[index].model_dump(),
        "row": row.model_dump(),
        "stale": comparison.is_stale(workspace, result),
    }


def safe_cell(value):
    if isinstance(value, str):
        value = clean_text(value)
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def statistics_csv(result, stale=False):
    rows = [*result.rows, *result.joint_rows]
    metrics = sorted({k for row in rows for k in row.metrics})
    probability = sorted({k for row in rows for k in row.probability})
    labels = {p.id: p.label or p.channel for p in result.request.parameters}
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "result_id",
            "name",
            "sample_id",
            "sample",
            "gate_id",
            "population",
            "role",
            "parameter_id",
            "parameter",
            "selected_events",
            "finite_events",
            "control_selected_events",
            "control_finite_events",
            "shared_events",
            "stale",
            "status",
            "error",
            "warnings",
            *metrics,
            *[f"pb_{k}" for k in probability],
        ]
    )
    for row in rows:
        writer.writerow(
            [
                safe_cell(v)
                for v in [
                    result.id,
                    result.request.name,
                    row.source.sample_id,
                    row.source_name,
                    row.source.gate_id,
                    row.population_name,
                    row.role,
                    row.parameter_id,
                    labels.get(row.parameter_id, "Joint distribution"),
                    row.selected_count,
                    row.finite_count,
                    row.control_selected_count,
                    row.control_finite_count,
                    row.shared_events,
                    stale,
                    row.status,
                    row.error,
                    "; ".join(row.warnings),
                    *[row.metrics.get(k) for k in metrics],
                    *[row.probability.get(k) for k in probability],
                ]
            ]
        )
    return output.getvalue().encode("utf-8")


def smooth_curve(values, sigma):
    values = np.asarray(values, dtype=float)
    if not sigma:
        return values
    # Same three-sigma, clamped-edge Gaussian as the native graph.
    radius = math.ceil(3 * sigma)
    weights = [math.exp(-0.5 * (j / sigma) ** 2) for j in range(-radius, radius + 1)]
    return np.convolve(np.pad(values, radius, mode="edge"), weights, "valid") / sum(weights)


def figure_geometry(data, presentation):
    view = ComparisonPresentation.model_validate(presentation)
    if view.mode == "cdf":
        main = [np.asarray(data["control_cdf"]), np.asarray(data["target_cdf"])]
    elif view.mode == "difference":
        main = [smooth_curve(data["difference"], view.smoothing)]
    else:
        main = [
            smooth_curve(data["control_fraction"], view.smoothing),
            smooth_curve(data["target_fraction"], view.smoothing),
        ]
    individuals = []
    if view.show_individuals and view.mode != "difference":
        individuals = [
            np.cumsum(c["fraction"])
            if view.mode == "cdf"
            else smooth_curve(c["fraction"], view.smoothing)
            for c in data["individual_controls"]
        ]
    low = min(0.0, *(float(v.min()) for v in [*main, *individuals]))
    high = max(1e-12, *(float(v.max()) for v in [*main, *individuals]))
    edges = data["edges"]
    edge_scale = max(1e-300, *(abs(v) for v in edges))
    first = edges[0] / edge_scale
    span = edges[-1] / edge_scale - first

    def x(value):
        return 65 + ((value / edge_scale - first) / span) * 690

    def points(values):
        return [
            [
                x(edges[i] / 2 + edges[i + 1] / 2),
                215 - (float(v) * 160 / max(high, -low)) * view.difference_scale
                if view.mode == "difference"
                else 390 - float(v) / high * 300,
            ]
            for i, v in enumerate(values)
        ]

    curves = [
        {"points": points(v), "color": view.control_color, "width": 1, "opacity": 0.35}
        for v in individuals
    ]
    curves += [
        {
            "points": points(v),
            "color": "#edb96c"
            if view.mode == "difference"
            else view.target_color
            if i
            else view.control_color,
            "width": 2.5,
            "opacity": 1,
        }
        for i, v in enumerate(main)
    ]
    ks = data["row"]["metrics"].get("ks_at_coordinate")
    return {"curves": curves, "ksX": x(ks) if ks is not None else None, "zeroY": 215}


def figure_svg(data, name, mode="histogram", smoothing=0, *, presentation=None):
    view = ComparisonPresentation.model_validate(
        presentation if presentation is not None else {"mode": mode, "smoothing": smoothing}
    )
    geometry = figure_geometry(data, view)
    paths = []
    for curve in geometry["curves"]:
        points = " ".join(f"{x:.12g},{y:.12g}" for x, y in curve["points"])
        paths.append(
            f'<polyline points="{points}" fill="none" stroke="{curve["color"]}" '
            f'stroke-width="{curve["width"]}" opacity="{curve["opacity"]}"/>'
        )
    if geometry["ksX"] is not None:
        x = geometry["ksX"]
        paths.append(
            f'<line x1="{x:.12g}" x2="{x:.12g}" y1="65" y2="390" '
            'stroke="currentColor" stroke-dasharray="4 4" opacity=".4"/>'
        )
    zero = (
        '<line x1="65" x2="755" y1="215" y2="215" stroke="currentColor" opacity=".25"/>'
        if view.mode == "difference"
        else ""
    )
    row = data["row"]
    label = escape(clean_text(data["parameter"]["label"] or data["parameter"]["channel"]))
    label += " · " + escape(data["parameter"]["transform"]["kind"])
    description = {
        "histogram": "Fraction per shared bin",
        "cdf": "Cumulative fraction",
        "difference": "Target − control fraction, independent vertical scale",
    }[view.mode]
    metadata = escape(json.dumps({"presentation": view.model_dump(), "stale": data["stale"]}))
    notice = "Stale scientific inputs. " if data["stale"] else ""
    if view.smoothing and view.mode != "cdf":
        notice += f"Display smoothing σ={view.smoothing:g} bins; statistics unchanged."
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="465" '
        'viewBox="0 0 800 465" color="#344054" font-family="sans-serif">'
        f"<title>{escape(clean_text(name))}</title><desc>{escape(notice + description)}</desc>"
        f"<metadata>{metadata}</metadata>"
        '<rect width="800" height="465" fill="white"/>'
        '<defs><clipPath id="comparison-clip">'
        '<rect x="65" y="65" width="690" height="325"/></clipPath></defs>'
        '<path d="M65 65V390H755" fill="none" stroke="currentColor" opacity=".35"/>'
        + zero
        + '<g clip-path="url(#comparison-clip)">'
        + "".join(paths)
        + "</g>"
        + f'<text x="65" y="418" fill="currentColor" font-size="11">{data["edges"][0]:.6g}</text>'
        + '<text x="755" y="418" text-anchor="end" fill="currentColor" font-size="11">'
        + f"{data['edges'][-1]:.6g}</text>"
        + f'<text x="410" y="445" text-anchor="middle" fill="currentColor" '
        f'font-size="13">{label}</text>'
        + f'<text x="70" y="35" fill="{view.control_color}" font-size="13">'
        f"Control n={row['control_finite_count']:,}</text>"
        + f'<text x="335" y="35" fill="{view.target_color}" font-size="13">'
        f"Target n={row['finite_count']:,}</text>"
        + f'<text x="70" y="57" fill="currentColor" font-size="11">{description}</text>'
        + "</svg>"
    ).encode()
