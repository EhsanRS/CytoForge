"""Revision-bound full-event 3D clouds; display precision never defines a gate."""

from __future__ import annotations

import hashlib
import json

import numpy as np

from .graph_views import fraction, resolved_options
from .models import GraphOptions, ThreeDView
from .plot_coordinates import match_dimension_axes, resolve_dimension
from .science import transform

CHUNK_EVENTS = 65536
POINT_DTYPE = np.dtype(
    [
        ("position", "<f4", (3,)),
        ("color", "<f4"),
        ("size", "<f4"),
        ("event_id", "<u8"),
        ("backgate", "<f4"),
    ]
)
assert POINT_DTYPE.itemsize == 32


def extent(values, finite, policy="robust"):
    data = values[finite]
    if not len(data):
        return [0.0, 1.0]
    scale = max(float(np.abs(data).max()), 1e-300)
    lo, hi = np.quantile(data / scale, [0, 1] if policy == "full" else [0.001, 0.999])
    pad = (hi - lo) * 0.04 if hi > lo else max(1 / scale, abs(lo) * 0.1)
    maximum = np.finfo(float).max
    with np.errstate(over="ignore"):
        limits = np.clip(np.array([lo - pad, hi + pad]) * scale, -maximum, maximum)
    if limits[0] >= limits[1]:
        value = float(data[0])
        with np.errstate(over="ignore"):
            limits = np.array(
                [
                    max(-maximum, np.nextafter(value, -np.inf)),
                    min(maximum, np.nextafter(value, np.inf)),
                ]
            )
    return limits.tolist()


def scalar_extent(values, finite):
    data = values[finite]
    if len(data):
        lo, hi = float(data.min()), float(data.max())
        if lo < hi:
            return [lo, hi]
    return extent(values, finite, "full")


def dimensions(
    doc,
    sample,
    names,
    coordinate_gate_id,
    view,
    overrides,
    positions=None,
    explicit=None,
    coordinate_sample_id=None,
):
    coordinate = next(
        (
            g
            for g in doc.gates
            if g.id == coordinate_gate_id and g.sample_id == (coordinate_sample_id or sample.id)
        ),
        None,
    )
    if coordinate_gate_id and coordinate is None:
        raise ValueError("Plot coordinate gate does not belong to this sample")
    result = []
    positions = range(len(names)) if positions is None else positions
    for name, override, axis, definition in zip(
        names, overrides, positions, explicit or [None] * len(names), strict=True
    ):
        dim = resolve_dimension(doc, sample, name, coordinate, axis, definition, override)
        if view.compensation == "uncompensated" and definition is None:
            dim = dim.model_copy(update={"compensation_ref": "uncompensated"})
        result.append(dim)
    return result


def column(doc, engine, sample, dim):
    if not dim.ratio_channels:
        return engine.dimension(doc, sample, dim)
    key = ("3d_dimension", doc.id, doc.revision, sample.id, sample.sha256, dim.model_dump_json())
    value = engine.cache.get(key)
    return value if value is not None else engine.cache.put(key, engine.dimension(doc, sample, dim))


def prepare(
    doc,
    engine,
    sample_id,
    x,
    y,
    view,
    gate_id=None,
    coordinate_gate_id=None,
    x_transform=None,
    y_transform=None,
    bounds=None,
    graph_options=None,
    backgate_id=None,
    x_dimension=None,
    y_dimension=None,
):
    if not y:
        raise ValueError("A 3D plot requires X, Y and Z parameters")
    view = ThreeDView.model_validate(view)
    options = resolved_options(GraphOptions.model_validate(graph_options or {}), "3d")
    sample = engine.sample(doc, sample_id)
    dims = dimensions(
        doc,
        sample,
        [x, y, view.z],
        coordinate_gate_id,
        view,
        [x_transform, y_transform, view.z_transform],
        explicit=[x_dimension, y_dimension, view.z_dimension],
        coordinate_sample_id=sample_id,
    )
    columns = [column(doc, engine, sample, dim) for dim in dims]
    scalar_dims = []
    scalars = []
    for name, override, definition in zip(
        (view.color_by, view.size_by),
        (view.color_transform, view.size_transform),
        (view.color_dimension, view.size_dimension),
        strict=True,
    ):
        if name is None:
            scalar_dims.append(None)
            scalars.append(None)
            continue
        dim = dimensions(
            doc,
            sample,
            [name],
            coordinate_gate_id,
            view,
            [override],
            [None],
            [definition],
            coordinate_sample_id=sample_id,
        )[0]
        scalar_dims.append(dim)
        scalars.append(column(doc, engine, sample, dim))
    if bounds is not None and (
        len(bounds) != 6
        or not np.all(np.isfinite(bounds))
        or any(bounds[i] >= bounds[i + 1] for i in range(0, 6, 2))
    ):
        raise ValueError("3D axis bounds require six finite increasing limits")
    signature = json.dumps(
        dict(
            dimensions=[d.model_dump() for d in dims],
            scalars=[d.model_dump() if d else None for d in scalar_dims],
            bounds=bounds,
            extent=options["axis_extent"],
            all_events=view.all_events,
            point_limit=options["point_limit"],
            color_bounds=view.color_bounds,
            size_bounds=view.size_bounds,
        ),
        sort_keys=True,
        separators=(",", ":"),
    )
    key = (
        "3d",
        doc.id,
        doc.revision,
        sample.id,
        sample.sha256,
        gate_id,
        coordinate_gate_id,
        backgate_id,
        signature,
    )
    indices = engine.cache.get((*key, "indices"))
    statistics = engine.cache.get((*key, "statistics"))
    selected = engine.mask(doc, sample, gate_id)
    backgate = engine.mask(doc, sample, backgate_id) & selected if backgate_id else None
    if indices is None or statistics is None:
        finite = np.logical_and.reduce([np.isfinite(c) for c in columns])
        valid = finite & selected
        limits = bounds or sum((extent(c, finite, options["axis_extent"]) for c in columns), [])
        visible = valid.copy()
        for axis, c in enumerate(columns):
            visible &= (c >= limits[axis * 2]) & (c <= limits[axis * 2 + 1])
        scalar_limits = []
        scalar_finite = []
        for scalar, explicit in zip(scalars, [view.color_bounds, view.size_bounds], strict=True):
            if scalar is None:
                scalar_limits.extend([0, 1])
                scalar_finite.append(0)
            else:
                scalar_limits.extend(explicit or scalar_extent(scalar, np.isfinite(scalar) & valid))
                scalar_finite.append(int(np.count_nonzero(np.isfinite(scalar) & valid)))
        indices = np.flatnonzero(visible).astype(np.uint64)
        visible_count = len(indices)
        if not view.all_events and len(indices) > options["point_limit"]:
            indices = np.sort(
                np.random.default_rng(45).choice(indices, options["point_limit"], replace=False)
            )
        statistics = np.array(
            [
                *limits,
                *scalar_limits,
                int(selected.sum()),
                int(valid.sum()),
                visible_count,
                int(np.count_nonzero(backgate & finite)) if backgate is not None else 0,
                *scalar_finite,
                int(np.count_nonzero(backgate & visible)) if backgate is not None else 0,
                int(np.count_nonzero(backgate[indices])) if backgate is not None else 0,
            ],
            float,
        )
        engine.cache.put((*key, "indices"), indices)
        engine.cache.put((*key, "statistics"), statistics)
    limits = statistics[:6].tolist()
    metadata = dict(
        mode="3d",
        revision=doc.revision,
        x=x,
        y=y,
        z=view.z,
        bounds=limits,
        count=int(statistics[10]),
        finite_count=int(statistics[11]),
        visible_count=int(statistics[12]),
        displayed_count=len(indices),
        backgate_count=int(statistics[13]),
        color_finite_count=int(statistics[14]),
        size_finite_count=int(statistics[15]),
        graph_options=options,
        three_d=view.model_dump(),
        axes=[d.model_dump() for d in dims],
        scalar_dimensions=[d.model_dump() if d else None for d in scalar_dims],
        color_bounds=statistics[6:8].tolist(),
        size_bounds=statistics[8:10].tolist(),
        ticks=[engine.ticks(limits[i * 2 : i * 2 + 2], d.transform) for i, d in enumerate(dims)],
        chunk_events=CHUNK_EVENTS,
        point_stride=32,
        point_format="xyz/color/size float32; event_id uint64; backgate float32; little endian",
        sampling=None
        if view.all_events or len(indices) == int(statistics[12])
        else "uniform deterministic; seed 45",
        data_key=hashlib.sha256(
            (
                doc.id
                + sample.id
                + str(doc.revision)
                + signature
                + (gate_id or "")
                + (backgate_id or "")
                + sample.sha256
            ).encode()
        ).hexdigest(),
    )
    if backgate is not None:
        metadata.update(
            backgate_visible_count=int(statistics[16]),
            backgate_displayed_count=int(statistics[17]),
            backgate_sampling=metadata["sampling"],
        )
    metadata["boxes"] = project_boxes(doc, sample, dims, limits, gate_id, engine)
    for i, name in enumerate(["x_transform", "y_transform", "z_transform"]):
        metadata[name] = dims[i].transform.model_dump()
    return metadata, indices, columns, scalars, backgate


def point_chunk(prepared, start=0, count=CHUNK_EVENTS):
    metadata, indices, columns, scalars, backgate = prepared
    if start < 0 or not 1 <= count <= CHUNK_EVENTS:
        raise ValueError("Invalid 3D event chunk")
    chosen = indices[start : start + count].astype(np.int64)
    rows = np.zeros(len(chosen), dtype=POINT_DTYPE)
    for axis, values in enumerate(columns):
        rows["position"][:, axis] = np.clip(
            fraction(values[chosen], metadata["bounds"][axis * 2 : axis * 2 + 2]), 0, 1
        )
    for name, scalar, limits in zip(
        ["color", "size"], scalars, [metadata["color_bounds"], metadata["size_bounds"]], strict=True
    ):
        rows[name] = 0.5 if name == "size" else -1
        if scalar is not None:
            finite = np.isfinite(scalar[chosen])
            values = scalar[chosen][finite]
            rows[name][finite] = np.clip(fraction(values, limits), 0, 1)
    rows["event_id"] = chosen.astype(np.uint64)
    if backgate is not None:
        rows["backgate"] = backgate[chosen].astype(float)
    return rows


def project_boxes(doc, sample, axes, limits, selected, engine=None):
    def basis(dim):
        ref = (
            (sample.compensation_id or "uncompensated")
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

    boxes = []
    for gate in doc.gates:
        if gate.sample_id != sample.id or (gate.id != selected and gate.parent_id != selected):
            continue
        if gate.kind != "hyperrectangle" or gate.complement:
            continue
        magnetic = None
        if gate.magnetic is not None:
            if engine is None:
                raise ValueError("Magnetic gate boxes require the event engine")
            gate, magnetic = engine.resolve_gate(doc, sample, gate)
        positions = match_dimension_axes(gate.dimensions, axes, basis)
        if any(i is None for i in positions) or len(set(positions)) != len(positions):
            continue
        bounds = limits.copy()
        for dim, index in zip(gate.dimensions, positions, strict=True):
            for side, value in enumerate((dim.minimum, dim.maximum)):
                if value is not None:
                    projected = transform(
                        transform(np.array([value]), dim.transform, True), axes[index].transform
                    )
                    if np.isfinite(projected[0]):
                        bounds[index * 2 + side] = float(projected[0])
        if any(bounds[i + 1] < limits[i] or bounds[i] > limits[i + 1] for i in range(0, 6, 2)):
            continue
        normalized = [
            float(
                np.clip(fraction(np.array([v]), limits[(i // 2) * 2 : (i // 2) * 2 + 2])[0], 0, 1)
            )
            for i, v in enumerate(bounds)
        ]
        box = dict(
            id=gate.id, name=gate.name, color=gate.color, bounds=bounds, normalized=normalized
        )
        if magnetic is not None:
            box["magnetic"] = magnetic
        boxes.append(box)
    return boxes


def project_positions(points, view, aspect=1):
    """Orthographic camera shared numerically by software, GPU and vector reports."""
    points = np.asarray(points, dtype=float) * 2 - 1
    cy, sy = np.cos(view.yaw), np.sin(view.yaw)
    cp, sp = np.cos(view.pitch), np.sin(view.pitch)
    x = points[:, 0] * cy + points[:, 2] * sy
    z = -points[:, 0] * sy + points[:, 2] * cy
    y = points[:, 1] * cp - z * sp
    depth = points[:, 1] * sp + z * cp
    return np.column_stack(
        [(x - view.pan[0]) * view.zoom / (2 * aspect), (y - view.pan[1]) * view.zoom / 2, depth]
    )
