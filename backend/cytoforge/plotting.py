"""Shared scientific plot payloads for desktop exploration and reports."""

from __future__ import annotations

import numpy as np

from .models import GateDimension, GraphOptions, Transform
from .plot_coordinates import match_dimension_axes, resolve_dimension


def plot_payload(
    doc,
    engine,
    sample_id,
    x,
    y=None,
    gate_id=None,
    coordinate_gate_id=None,
    x_transform=None,
    y_transform=None,
    bins=96,
    bounds=None,
    mode="density",
    backgate_id=None,
    graph_options=None,
    three_d=None,
    overlay_gate_ids=None,
    native_gate_coordinates=False,
    x_dimension=None,
    y_dimension=None,
):
    if mode == "3d":
        from .three_dimensional import prepare

        return prepare(
            doc,
            engine,
            sample_id,
            x,
            y,
            three_d,
            gate_id,
            coordinate_gate_id,
            x_transform,
            y_transform,
            bounds,
            graph_options,
            backgate_id,
            x_dimension=x_dimension,
            y_dimension=y_dimension,
        )[0]
    sample = engine.sample(doc, sample_id)
    coordinate_gate = next(
        (g for g in doc.gates if g.id == coordinate_gate_id and g.sample_id == sample_id), None
    )
    if coordinate_gate_id and coordinate_gate is None:
        raise ValueError("Plot coordinate gate does not belong to this sample")
    if mode not in {"density", "scatter", "histogram", "cdf", "contour", "zebra", "pseudocolor"}:
        raise ValueError("Unsupported plot mode")
    if y is None and mode == "density":
        mode = "histogram"
    if (mode in {"histogram", "cdf"}) != (y is None):
        raise ValueError("Histograms and CDFs require one parameter; other plots require two")
    xd = resolve_dimension(doc, sample, x, coordinate_gate, 0, x_dimension, x_transform)
    yd = resolve_dimension(doc, sample, y, coordinate_gate, 1, y_dimension, y_transform)
    xs, ys = xd.transform, yd.transform if yd else Transform()
    if native_gate_coordinates:
        if x_dimension is not None or y_dimension is not None:
            raise ValueError(
                "Native gate editing cannot override the gate's coordinate definitions"
            )
        if coordinate_gate is None:
            raise ValueError("Native gate coordinates require a coordinate gate")
        ordered = coordinate_gate.dimensions or [
            GateDimension(channel=coordinate_gate.x, transform=coordinate_gate.x_transform),
            *(
                [GateDimension(channel=coordinate_gate.y, transform=coordinate_gate.y_transform)]
                if y and coordinate_gate.kind != "range"
                else []
            ),
        ]
        if (
            len(ordered) != (2 if y else 1)
            or ordered[0].channel != x
            or (y and ordered[1].channel != y)
        ):
            raise ValueError("Native plot axes must match the gate's ordered dimensions")
        xd, yd = ordered[0], ordered[1] if y else None
    if xd:
        xd = xd.model_copy(update={"transform": xs})
    if yd:
        yd = yd.model_copy(update={"transform": ys})
    if bounds is not None and (len(bounds) != (4 if y else 2) or not np.all(np.isfinite(bounds))):
        raise ValueError("Invalid plot limits")
    result = engine.plot(
        doc,
        sample,
        x,
        y,
        xs,
        ys,
        gate_id,
        bins,
        bounds,
        mode,
        backgate_id,
        xd,
        yd,
        GraphOptions.model_validate(graph_options or {}),
    )
    result["revision"] = doc.revision
    result["overlays"] = project_gates(
        doc,
        sample,
        x,
        y,
        xs,
        ys,
        gate_id,
        result["bounds"],
        xd,
        yd,
        engine,
        overlay_gate_ids,
        native_gate_coordinates,
    )
    result["coordinate_gate_id"] = coordinate_gate_id
    result["x_transform"] = xs.model_dump()
    result["y_transform"] = ys.model_dump() if y else None
    result["axes"] = [dim.model_dump() for dim in (xd, yd) if dim]
    return result


def project_gates(
    doc,
    sample,
    x,
    y,
    xs,
    ys,
    selected,
    limits=None,
    xd=None,
    yd=None,
    engine=None,
    overlay_gate_ids=None,
    native_order=False,
):
    from .models import GateDimension
    from .science import transform

    overlays = []
    limits = limits or [-1e6, 1e6, -1e6, 1e6]
    axes = [xd or GateDimension(channel=x, transform=xs)]
    if y:
        axes.append(yd or GateDimension(channel=y, transform=ys))

    def basis(dim):
        ref = (
            sample.compensation_id or "uncompensated"
            if dim.compensation_ref == "sample"
            else dim.compensation_ref
        )
        return (
            dim.channel,
            ref,
            dim.ratio_channels,
            dim.ratio_a,
            dim.ratio_b,
            dim.ratio_c,
            dim.ratio_bound_min,
            dim.ratio_bound_max,
        )

    def convert(values, dim, axis):
        if dim.transform == axes[axis].transform:
            return np.asarray(values)
        return transform(transform(np.asarray(values), dim.transform, True), axes[axis].transform)

    for gate in doc.gates:
        if overlay_gate_ids is not None and gate.id not in overlay_gate_ids:
            continue
        if gate.sample_id != sample.id or (gate.parent_id != selected and gate.id != selected):
            continue
        if gate.kind in {"boolean", "container", "quality"}:
            continue
        magnetic = None
        if gate.magnetic is not None:
            if engine is None:
                raise ValueError("Magnetic overlays require the event engine")
            gate, magnetic = engine.resolve_gate(doc, sample, gate)
        dims = gate.dimensions or [
            GateDimension(channel=gate.x, transform=gate.x_transform),
            *(
                [GateDimension(channel=gate.y, transform=gate.y_transform)]
                if gate.y and gate.kind != "range"
                else []
            ),
        ]
        if len(dims) > 2:
            continue
        indices = match_dimension_axes(dims, axes, basis)
        if native_order:
            if len(dims) != len(axes) or any(
                basis(dim) != basis(axis) for dim, axis in zip(dims, axes, strict=True)
            ):
                continue
            indices = list(range(len(dims)))
        if any(i is None for i in indices) or len(set(indices)) != len(indices):
            continue
        if any(
            (d.transform.bound_min is not None or d.transform.bound_max is not None)
            and d.transform != axes[i].transform
            for d, i in zip(dims, indices, strict=True)
        ):
            continue

        def append_overlay(overlay, magnetic=magnetic, dims=dims, indices=indices):
            if magnetic is not None:
                arrow = {
                    "axes": indices,
                    "from": [
                        float(convert([value], dim, axis)[0])
                        for value, dim, axis in zip(magnetic["anchor"], dims, indices, strict=True)
                    ],
                    "to": [
                        float(convert([value], dim, axis)[0])
                        for value, dim, axis in zip(
                            magnetic["position"], dims, indices, strict=True
                        )
                    ],
                }
                overlay["magnetic"] = dict(magnetic)
                if np.all(np.isfinite([arrow["from"], arrow["to"]])):
                    overlay["magnetic"]["arrow"] = arrow
            overlays.append(overlay)

        if gate.kind in {"spider", "curly"}:
            from .spider import MEMBER_ARMS, clip_arm

            native_limits = []
            for dim, axis in zip(dims, indices, strict=True):
                values = np.asarray(limits[2 * axis : 2 * axis + 2])
                if dim.transform != axes[axis].transform:
                    values = transform(transform(values, axes[axis].transform, True), dim.transform)
                if not np.all(np.isfinite(values)) or values[0] >= values[1]:
                    raise ValueError(
                        "Divider viewport cannot be represented in its saved coordinates"
                    )
                native_limits.extend(values.tolist())
            if gate.kind == "curly":
                from .curly import boundaries, member_boundaries

                curves = boundaries(gate, native_limits)

                def project_segment(segment, dims=dims, indices=indices):
                    if not segment:
                        return []
                    values = np.asarray(segment)
                    projected = np.empty_like(values)
                    for i, (dim, axis) in enumerate(zip(dims, indices, strict=True)):
                        projected[:, axis] = convert(values[:, i], dim, axis)
                    if not np.isfinite(projected).all():
                        raise ValueError(
                            "The curly boundary exceeds this display transform's finite range"
                        )
                    return projected.tolist()

                shared_segments = [project_segment(segment) for segment in curves]
                pieces = member_boundaries(gate, curves)[gate.partition.member]
                center = [0.0, 0.0]
                for value, dim, axis in zip(gate.curly.center, dims, indices, strict=True):
                    center[axis] = float(convert([value], dim, axis)[0])
                append_overlay(
                    dict(
                        id=gate.id,
                        name=gate.name,
                        color=gate.color,
                        kind="curly",
                        segments=[project_segment(segment) for segment in pieces],
                        shared_segments=shared_segments,
                        center=center if np.isfinite(center).all() else None,
                    )
                )
                continue
            segments = []
            for arm in MEMBER_ARMS[gate.partition.member]:
                clipped = clip_arm(gate.spider, native_limits, arm)
                if clipped is None:
                    continue
                weight = np.linspace(0, 1, 65)[:, None]
                dense = (1 - weight) * np.asarray(clipped[0]) + weight * np.asarray(clipped[1])
                projected = np.empty_like(dense)
                for i, (dim, axis) in enumerate(zip(dims, indices, strict=True)):
                    projected[:, axis] = convert(dense[:, i], dim, axis)
                if np.all(np.isfinite(projected)):
                    segments.append(projected.tolist())
            center = [0.0, 0.0]
            for value, dim, axis in zip(gate.spider.center, dims, indices, strict=True):
                center[axis] = float(convert([value], dim, axis)[0])
            append_overlay(
                dict(
                    id=gate.id,
                    name=gate.name,
                    color=gate.color,
                    kind="spider",
                    segments=segments,
                    center=center if np.all(np.isfinite(center)) else None,
                )
            )
            continue
        if gate.kind in {"hyperrectangle", "rectangle", "range", "quadrant"}:
            intervals = [(d.minimum, d.maximum) for d in dims]
            if not gate.dimensions:
                if gate.kind == "quadrant":
                    intervals = [
                        (gate.bounds[0], None)
                        if gate.quadrant in {2, 3}
                        else (None, gate.bounds[0]),
                        (gate.bounds[1], None)
                        if gate.quadrant in {1, 2}
                        else (None, gate.bounds[1]),
                    ]
                else:
                    intervals = [gate.bounds[2 * i : 2 * i + 2] for i in range(len(dims))]
            bounds = []
            for dim, axis, interval in zip(dims, indices, intervals, strict=True):
                bounds.append(
                    [
                        float(convert([value], dim, axis)[0])
                        if value is not None
                        else limits[2 * axis + j]
                        for j, value in enumerate(interval)
                    ]
                )
            if not np.all(np.isfinite(bounds)):
                continue
            if len(dims) == 1:
                append_overlay(
                    dict(
                        id=gate.id,
                        name=gate.name,
                        color=gate.color,
                        kind="range",
                        axis="x" if indices[0] == 0 else "y",
                        bounds=bounds[0],
                    )
                )
                continue
            a, b = bounds[indices.index(0)], bounds[indices.index(1)]
            vertices = [[a[0], b[0]], [a[1], b[0]], [a[1], b[1]], [a[0], b[1]]]
            append_overlay(
                dict(
                    id=gate.id, name=gate.name, color=gate.color, kind="polygon", vertices=vertices
                )
            )
            continue
        if not y:
            continue
        if gate.kind == "polygon":
            vertices = np.asarray(gate.vertices)
        else:
            theta = np.linspace(0, 2 * np.pi, 129)
            if gate.kind == "ellipse":
                ca, sa = np.cos(gate.angle), np.sin(gate.angle)
                dx, dy = gate.radii[0] * np.cos(theta), gate.radii[1] * np.sin(theta)
                vertices = np.column_stack(
                    [gate.center[0] + dx * ca - dy * sa, gate.center[1] + dx * sa + dy * ca]
                )
            else:
                covariance = np.asarray(gate.covariance)
                if not np.allclose(covariance, covariance.T):
                    continue
                values, vectors = np.linalg.eigh(covariance)
                if np.min(values) <= 0:
                    continue
                circle = np.column_stack([np.cos(theta), np.sin(theta)])
                vertices = (
                    np.asarray(gate.coordinates)
                    + (circle * np.sqrt(values * gate.distance_square)) @ vectors.T
                )

        def project_ring(ring, dimensions=dims, positions=indices):
            ring = np.asarray(ring, dtype=float)
            dense = np.concatenate(
                [
                    np.linspace(a, b, 17, endpoint=False)
                    for a, b in zip(ring, np.roll(ring, -1, axis=0), strict=True)
                ]
            )
            points = np.empty_like(dense)
            for i, (dim, axis) in enumerate(zip(dimensions, positions, strict=True)):
                points[:, axis] = convert(dense[:, i], dim, axis)
            return points

        points = project_ring(vertices)
        holes = [project_ring(ring) for ring in gate.holes] if gate.kind == "polygon" else []
        if np.all(np.isfinite(points)) and all(np.all(np.isfinite(ring)) for ring in holes):
            append_overlay(
                dict(
                    id=gate.id,
                    name=gate.name,
                    color=gate.color,
                    kind="polygon",
                    vertices=points.tolist(),
                    **({"holes": [ring.tolist() for ring in holes]} if holes else {}),
                )
            )
    return overlays
