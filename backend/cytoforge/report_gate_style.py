"""Gate presentation in physical units; scientific masks remain unchanged."""

from __future__ import annotations

from xml.etree import ElementTree as ET

import numpy as np
from matplotlib.patches import PathPatch
from matplotlib.path import Path


def gate_width(definition, default):
    style = definition.graph_options.gate_style
    return style.line_width_px * 0.75 if style and style.line_width_px is not None else default


def show_gate_labels(definition):
    style = definition.graph_options.gate_style
    return not style or style.show_labels is not False


def gate_style_manifest(definition):
    style = definition.graph_options.gate_style
    if not style or not style.model_dump():
        return {}
    return dict(
        gate_style=style.model_dump(),
        gate_style_units=dict(line_width_px="CSS px; report width = px × 0.75 pt"),
        gate_fill_scope="Closed 2D gates; holes remain transparent",
    )


def fill_gate(axes, overlay, bounds, histogram, definition, color, fraction):
    style = definition.graph_options.gate_style
    opacity = style.fill_opacity if style else None
    if not opacity:
        return
    color = style.fill_color or color
    if overlay["kind"] == "range":
        axis = overlay["axis"]
        values = fraction(overlay["bounds"], *(bounds[:2] if axis == "x" else bounds[2:]))
        if axis == "x":
            axes.axvspan(*values, facecolor=color, alpha=opacity, edgecolor="none")
        elif not histogram:
            axes.axhspan(*values, facecolor=color, alpha=opacity, edgecolor="none")
    elif not histogram and overlay.get("vertices"):
        rings = []
        for ring in [overlay["vertices"], *overlay.get("holes", [])]:
            points = np.asarray(ring, dtype=float)
            rings.append(
                np.column_stack(
                    [fraction(points[:, 0], *bounds[:2]), fraction(points[:, 1], *bounds[2:])]
                )
            )
        entries = getattr(axes, "_cytoforge_gate_fills", [])
        identifier = f"cytoforge-gate-fill-{len(entries)}"
        outer = rings[0]
        axes.add_patch(
            PathPatch(
                Path(
                    np.vstack([outer, outer[0]]),
                    [Path.MOVETO, *([Path.LINETO] * (len(outer) - 1)), Path.CLOSEPOLY],
                ),
                facecolor=color,
                alpha=opacity,
                edgecolor="none",
                clip_on=True,
                gid=identifier,
            )
        )
        entries.append((identifier, rings[1:]))
        axes._cytoforge_gate_fills = entries


def gate_fill_svg(svg, axes):
    """Subtract the union of holes, including overlapping and nested rings.

    Each complement is a separate even-odd clip. Their intersection implements
    outer AND NOT(any hole), rather than toggling an overlapping hole back in.
    Coordinates stay vector paths in the final publication SVG.
    """
    entries = getattr(axes, "_cytoforge_gate_fills", [])
    if not entries:
        return svg
    namespace = "http://www.w3.org/2000/svg"

    def tag(value):
        return f"{{{namespace}}}{value}"

    root = ET.fromstring(svg)
    defs = root.find(tag("defs"))
    if defs is None:
        defs = ET.SubElement(root, tag("defs"))
    groups = {node.get("id"): node for node in root.iter() if node.get("id")}
    position = axes.get_position()
    width, height = axes.figure.get_size_inches() * 72
    xlim, ylim = axes.get_xlim(), axes.get_ylim()

    def projected(points):
        return [
            (
                width * (position.x0 + (x - xlim[0]) / (xlim[1] - xlim[0]) * position.width),
                height * (1 - position.y0 - (y - ylim[0]) / (ylim[1] - ylim[0]) * position.height),
            )
            for x, y in points
        ]

    def path_data(points):
        return "M " + " L ".join(f"{x:.12g} {y:.12g}" for x, y in projected(points)) + " z"

    rectangle = path_data(
        [(xlim[0], ylim[0]), (xlim[1], ylim[0]), (xlim[1], ylim[1]), (xlim[0], ylim[1])]
    )
    for identifier, holes in entries:
        group = groups[identifier]
        children = list(group)
        for child in children:
            group.remove(child)
        for path in (node for child in children for node in child.iter(tag("path"))):
            path.set("fill-rule", "evenodd")
        inner = group
        for index, hole in enumerate(holes):
            clip_id = f"{identifier}-exclude-{index}"
            clip = ET.SubElement(defs, tag("clipPath"), id=clip_id, clipPathUnits="userSpaceOnUse")
            ET.SubElement(
                clip, tag("path"), {"d": rectangle + " " + path_data(hole), "clip-rule": "evenodd"}
            )
            inner = ET.SubElement(inner, tag("g"), {"clip-path": f"url(#{clip_id})"})
        inner.extend(children)
    return ET.tostring(root, encoding="unicode")
