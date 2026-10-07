"""Vector 3D reports with the same camera, event identities and axes as the viewer."""

from __future__ import annotations

import hashlib
import io

import matplotlib
import numpy as np
from matplotlib.backends.backend_svg import FigureCanvasSVG
from matplotlib.figure import Figure

from .models import ThreeDView, Transform
from .report_backgates import HIGHLIGHT_COLOR, backgate_manifest
from .report_gate_style import gate_style_manifest, gate_width, show_gate_labels
from .report_graph_typography import draw_legend, plot_layout, text_style, typography_manifest
from .report_plots import _MPL_LOCK, magnetic_provenance, source_payloads
from .report_sources import SourceAudit
from .report_svg import clean_text
from .three_dimensional import CHUNK_EVENTS, point_chunk, prepare, project_positions

PALETTES = {
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
    "viridis": [[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]],
}


def scalar_colors(values, palette):
    ramp = np.asarray(PALETTES[palette], float) / 255
    p = np.clip(values, 0, 0.999999) * (len(ramp) - 1)
    indices = np.floor(p).astype(int)
    colors = ramp[indices] + (ramp[indices + 1] - ramp[indices]) * (p - indices)[:, None]
    colors[values < 0] = np.array([148, 163, 181]) / 255
    return colors


def corners(bounds=(0, 1, 0, 1, 0, 1)):
    return np.array(
        [
            [bounds[1 if i & 1 else 0], bounds[3 if i & 2 else 2], bounds[5 if i & 4 else 4]]
            for i in range(8)
        ]
    )


def figure(workspace, engine, definition, layers, width, height):
    payloads, bounds = source_payloads(workspace, engine, definition, layers)
    view = ThreeDView.model_validate(payloads[0]["three_d"])
    samples = {s.id: s for s in workspace.samples}
    gates = {g.id: g for g in workspace.gates}
    audit = SourceAudit(workspace)
    manifest = []
    with (
        _MPL_LOCK,
        matplotlib.rc_context(
            {
                "svg.fonttype": "path",
                "svg.hashsalt": "cytoforge-report-3d",
                "font.family": "DejaVu Sans",
                "font.size": 7,
                "text.usetex": False,
            }
        ),
    ):
        fig = Figure(figsize=(width / 25.4, height / 25.4), facecolor="white")
        canvas = FigureCanvasSVG(fig)
        position, legend_rows, legend_row_mm = plot_layout(
            definition, width, height, len(layers), three_d=True
        )
        axes = fig.add_axes(position)
        axes.set_xlim(-1, 1)
        axes.set_ylim(-1, 1)
        axes.set_axis_off()
        aspect = position[2] * width / (position[3] * height)

        def wire(limits, color, width=0.45):
            p = project_positions(corners(limits), view, aspect)
            for i in range(8):
                for bit in (1, 2, 4):
                    if not i & bit:
                        axes.plot(
                            p[[i, i | bit], 0],
                            p[[i, i | bit], 1],
                            color=color,
                            linewidth=width,
                            zorder=3,
                        )

        if view.show_cube:
            wire((0, 1, 0, 1, 0, 1), "#8498aa")
        for layer, payload in zip(layers, payloads, strict=True):
            sample = samples[layer["sample_id"]]
            gate = gates.get(layer["gate_id"])
            sample_name = payload["pooled"]["group_name"] if definition.pooled else sample.name
            label = layer["label"] or f"{sample_name} · {gate.name if gate else 'All events'}"
            layer_engine = engine
            if definition.pooled:
                from .virtual_groups import PooledEngine

                layer_engine = PooledEngine(
                    workspace, engine, sample.id, definition.group_id, definition.sample_filter
                )
            prepared = prepare(
                workspace,
                layer_engine,
                sample.id,
                definition.x,
                definition.y,
                view,
                gate_id=layer["gate_id"],
                backgate_id=layer.get("backgate_id"),
                coordinate_gate_id=layer["coordinate_gate_id"],
                x_transform=Transform.model_validate(payload["x_transform"]),
                y_transform=Transform.model_validate(payload["y_transform"]),
                bounds=bounds,
                graph_options=definition.graph_options,
                x_dimension=definition.x_dimension,
                y_dimension=definition.y_dimension,
            )
            identities = hashlib.sha256()
            highlighted_identities = hashlib.sha256()
            camera_count = 0
            camera_backgate_count = 0
            for start in range(0, payload["displayed_count"], CHUNK_EVENTS):
                points = point_chunk(prepared, start)
                identities.update(points["event_id"].tobytes())
                projected = project_positions(points["position"], view, aspect)
                highlighted = points["backgate"] > 0.5
                highlighted_identities.update(points["event_id"][highlighted].tobytes())
                camera_backgate_count += int(
                    np.count_nonzero(highlighted & np.all(np.abs(projected[:, :2]) <= 1, axis=1))
                )
                camera_count += int(np.count_nonzero(np.all(np.abs(projected[:, :2]) <= 1, axis=1)))
                sizes = np.full(len(points), view.point_size * 0.75)
                if view.size_by:
                    sizes *= 0.4 + points["size"] * 1.6
                colors = (
                    scalar_colors(points["color"], payload["graph_options"]["palette"])
                    if view.color_by
                    else layer["color"]
                )
                if np.any(highlighted):
                    if not view.color_by:
                        from matplotlib.colors import to_rgb

                        colors = np.tile(to_rgb(layer["color"]), (len(points), 1))
                    colors[highlighted] = [
                        int(HIGHLIGHT_COLOR[i : i + 2], 16) / 255 for i in (1, 3, 5)
                    ]
                axes.scatter(
                    projected[:, 0],
                    projected[:, 1],
                    s=sizes**2,
                    alpha=view.opacity,
                    c=colors,
                    edgecolors="none",
                    zorder=2,
                    rasterized=False,
                )
            if definition.show_gates:
                for box in payload["boxes"]:
                    wire(box["normalized"], box["color"], gate_width(definition, 0.45))
                    if not show_gate_labels(definition):
                        continue
                    p = project_positions(
                        np.array(
                            [[box["normalized"][0], box["normalized"][2], box["normalized"][5]]]
                        ),
                        view,
                        aspect,
                    )[0]
                    axes.text(
                        p[0],
                        p[1],
                        clean_text(box["name"]),
                        **text_style(definition, "gate_labels", 5, box["color"]),
                        clip_on=True,
                    )
            names = tuple(
                name
                for name in (definition.x, definition.y, view.z, view.color_by, view.size_by)
                if name
            )
            manifest.append(
                dict(
                    **{**layer, "label": label},
                    **backgate_manifest(workspace, layer, payload),
                    **(
                        dict(
                            backgate_displayed_event_ids_sha256=highlighted_identities.hexdigest(),
                            camera_visible_backgate_markers=camera_backgate_count,
                        )
                        if layer.get("backgate_id")
                        else {}
                    ),
                    sample_sha256=sample.sha256,
                    population_count=payload["count"],
                    finite_count=payload["finite_count"],
                    visible_count=payload["visible_count"],
                    outside_view=payload["finite_count"] - payload["visible_count"],
                    displayed_count=payload["displayed_count"],
                    camera_visible_markers=camera_count,
                    x_transform=payload["x_transform"],
                    y_transform=payload["y_transform"],
                    z_transform=payload["z_transform"],
                    axes=payload["axes"],
                    scalar_dimensions=payload["scalar_dimensions"],
                    color_bounds=payload["color_bounds"],
                    size_bounds=payload["size_bounds"],
                    color_finite_count=payload["color_finite_count"],
                    size_finite_count=payload["size_finite_count"],
                    three_d=view.model_dump(),
                    graph_options=payload["graph_options"],
                    sampling=payload["sampling"],
                    display_precision="Normalized float32 display; gates use full event data",
                    event_ids_sha256=identities.hexdigest(),
                    gate=gate.model_dump() if gate else None,
                    **magnetic_provenance(
                        workspace,
                        engine,
                        sample,
                        payload,
                        gate,
                        definition.show_gates,
                        population_ids=(layer.get("backgate_id"),),
                    ),
                    undefined_reason=None,
                    source=payload["pooled_source"]
                    if definition.pooled
                    else audit.closure(
                        sample.id,
                        names,
                        (
                            layer["gate_id"],
                            layer["coordinate_gate_id"],
                            layer.get("backgate_id"),
                            *[box["id"] for box in payload["boxes"] if definition.show_gates],
                        ),
                        dimensions=[*payload["axes"], *payload["scalar_dimensions"]],
                    ),
                )
            )
        if view.show_labels:
            for i, name in enumerate((definition.x, definition.y, view.z)):
                endpoint = np.zeros((1, 3))
                endpoint[0, i] = 1
                p = project_positions(endpoint, view, aspect)[0]
                axes.text(
                    p[0],
                    p[1] - 0.09,
                    clean_text(f"{'XYZ'[i]} · {name}"),
                    ha="center",
                    **text_style(
                        definition, "axis_labels", 6, ["#087e8b", "#315b9a", "#986b20"][i]
                    ),
                    clip_on=True,
                )
                for tick in payloads[0]["ticks"][i]:
                    point = np.zeros((1, 3))
                    from .graph_views import fraction

                    point[0, i] = fraction(np.array([tick["value"]]), bounds[i * 2 : i * 2 + 2])[0]
                    p = project_positions(point, view, aspect)[0]
                    axes.text(
                        p[0],
                        p[1] + 0.03,
                        clean_text(tick["label"]),
                        **text_style(definition, "tick_labels", 4.5, "#52677e"),
                        ha="center",
                        clip_on=True,
                    )
        if not any(p["displayed_count"] for p in payloads):
            axes.text(
                0, 0, "No finite events inside these axes", ha="center", fontsize=7, color="#52677e"
            )
        draw_legend(fig, definition, manifest, width, height, legend_rows, legend_row_mm)
        output = io.StringIO()
        canvas.print_svg(output, metadata={"Date": None, "Creator": "CytoForge"})
        fig.clear()
    return output.getvalue(), dict(
        kind="plot",
        mode="3d",
        normalization="count",
        bounds=bounds,
        three_d=view.model_dump(),
        camera="Orthographic; rotation, pan and zoom preserved",
        layers=manifest,
        **typography_manifest(definition),
        **gate_style_manifest(definition),
    )
