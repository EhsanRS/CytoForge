"""Deterministic, bounded magnetic translations in a gate's own coordinates.

All finite parent events contribute to a gate-shaped count surface. A fixed
32-cell-per-width grid locates the nearest local maximum; exact event membership
then chooses a nearby refinement or the unchanged anchor. Plot sampling, zoom,
contour smoothing and rendering limits never participate in this calculation.
"""

from __future__ import annotations

import itertools
import math

import numpy as np
from scipy.ndimage import gaussian_filter, maximum_filter
from scipy.signal import fftconvolve

CELLS = 32
ALGORITHM = "local-window-count-v1"


def frame(gate):
    """Bounding-box center and widths; reject shapes with no bounded search frame."""
    if gate.kind == "range" and gate.y is not None:
        raise ValueError("Magnetic range gates require a single channel")
    if gate.kind in {"rectangle", "range"}:
        intervals = np.asarray(gate.bounds, dtype=float).reshape(-1, 2)
    elif gate.kind == "hyperrectangle" and 1 <= len(gate.dimensions) <= 2:
        if any(d.minimum is None or d.maximum is None for d in gate.dimensions):
            raise ValueError("Magnetic gates require finite bounds on every dimension")
        intervals = np.array([(d.minimum, d.maximum) for d in gate.dimensions])
    elif gate.kind == "polygon":
        vertices = np.asarray(gate.vertices)
        intervals = np.column_stack([vertices.min(axis=0), vertices.max(axis=0)])
    elif gate.kind == "ellipse":
        cosine, sine = math.cos(gate.angle), math.sin(gate.angle)
        extents = [
            math.hypot(gate.radii[0] * cosine, gate.radii[1] * sine),
            math.hypot(gate.radii[0] * sine, gate.radii[1] * cosine),
        ]
        intervals = np.column_stack(
            [np.asarray(gate.center) - extents, np.asarray(gate.center) + extents]
        )
    elif gate.kind == "ellipsoid" and len(gate.dimensions) == 2:
        covariance = np.asarray(gate.covariance)
        if (
            not np.array_equal(covariance, covariance.T)
            or np.linalg.eigvalsh(covariance).min() <= 0
        ):
            raise ValueError("Magnetic ellipsoids need symmetric positive definite covariance")
        with np.errstate(over="ignore", invalid="ignore"):
            extents = np.sqrt(np.diag(covariance)) * math.sqrt(gate.distance_square)
            intervals = np.column_stack(
                [np.asarray(gate.coordinates) - extents, np.asarray(gate.coordinates) + extents]
            )
    else:
        raise ValueError("Magnetic gates require a bounded one- or two-dimensional geometric gate")
    with np.errstate(over="ignore", invalid="ignore"):
        widths = intervals[:, 1] - intervals[:, 0]
        center = intervals[:, 0] / 2 + intervals[:, 1] / 2
    if not np.all(np.isfinite(intervals)) or not np.all(np.isfinite(widths)) or np.any(widths <= 0):
        raise ValueError("Magnetic gate widths must be positive, finite and representable")
    return center, widths


def translated(gate, shift):
    """Translate geometry without changing its transforms, size or orientation."""
    changes = {"magnetic": None}
    if gate.kind in {"range", "rectangle"}:
        changes["bounds"] = [v + float(shift[i // 2]) for i, v in enumerate(gate.bounds)]
    elif gate.kind == "hyperrectangle":
        changes["dimensions"] = [
            d.model_copy(update={"minimum": d.minimum + s, "maximum": d.maximum + s})
            for d, s in zip(gate.dimensions, map(float, shift), strict=True)
        ]
    elif gate.kind == "polygon":
        changes["vertices"] = [
            tuple(map(float, point)) for point in np.asarray(gate.vertices) + shift
        ]
        changes["holes"] = [
            [tuple(map(float, point)) for point in np.asarray(ring) + shift] for ring in gate.holes
        ]
    elif gate.kind == "ellipse":
        changes["center"] = tuple(np.asarray(gate.center) + shift)
    else:
        changes["coordinates"] = (np.asarray(gate.coordinates) + shift).tolist()
    return gate.model_copy(update=changes)


def resolve(gate, columns, parent):
    from .science import shape_mask

    anchor, widths = frame(gate)
    radius = gate.magnetic.max_shift
    finite = parent.copy()
    for column in columns:
        finite &= np.isfinite(column)
    finite_count = int(finite.sum())
    eligible = [column[finite] for column in columns]
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        normalized = np.column_stack(
            [(column - a) / w for column, a, w in zip(eligible, anchor, widths, strict=True)]
        )
    near = np.all(np.abs(normalized) <= radius + 0.5 + 2 / CELLS, axis=1)
    local = [column[near] for column in eligible]
    anchor_count = int(shape_mask(gate, eligible).sum())
    shift = np.zeros(len(columns))
    count = anchor_count
    status = "resolved"
    if not finite_count:
        status = "no-finite-parent-events"
    elif not near.any():
        status = "resolved" if anchor_count else "no-nearby-events"
    elif radius < 1 / CELLS:
        # Tiny requested movements still have a real bounded search, even when
        # the ordinary count-grid spacing would contain only the anchor.
        choices = [(-anchor_count, 0, tuple(shift))]
        for direction in itertools.product((-1, 0, 1), repeat=len(columns)):
            vector = np.asarray(direction, dtype=float)
            length = np.linalg.norm(vector)
            if not length:
                continue
            candidate = vector * radius / length
            moved = translated(gate, candidate * widths)
            try:
                center, size = frame(moved)
            except ValueError:
                continue
            if not np.all(np.isfinite(center)) or not np.allclose(size, widths, rtol=1e-12, atol=0):
                continue
            value = int(shape_mask(moved, eligible).sum())
            choices.append((-value, float(np.linalg.norm(candidate)), tuple(candidate)))
        best = min(choices)
        count, shift = -best[0], np.asarray(best[2])
    else:
        extent = math.ceil(radius * CELLS) + CELLS // 2 + 2
        edges = (np.arange(-extent, extent + 2) - 0.5) / CELLS
        histogram = np.histogramdd(normalized[near], bins=[edges] * len(columns))[0]
        offsets = np.arange(-CELLS // 2, CELLS // 2 + 1) / CELLS
        mesh = np.meshgrid(*([offsets] * len(columns)), indexing="ij")
        with np.errstate(over="ignore", invalid="ignore"):
            kernel_columns = [
                (v.ravel() * w + a) for v, a, w in zip(mesh, anchor, widths, strict=True)
            ]
            kernel = shape_mask(gate, kernel_columns).reshape(mesh[0].shape).astype(float)
        surface = np.maximum(
            np.rint(
                fftconvolve(histogram, kernel[(slice(None, None, -1),) * len(columns)], mode="same")
            ),
            0,
        )
        smooth = gaussian_filter(surface, 1, mode="constant")
        positions = np.stack(
            np.meshgrid(*([np.arange(-extent, extent + 1) / CELLS] * len(columns)), indexing="ij"),
            axis=-1,
        )
        distances = np.linalg.norm(positions, axis=-1)
        allowed = distances <= radius + 1e-12
        scores = np.where(allowed, smooth, -np.inf)
        peaks = (
            allowed
            & (surface > 0)
            & (scores > 1e-6)
            & (scores >= maximum_filter(scores, 5, mode="constant", cval=-np.inf) - 1e-9)
        )
        candidates = np.argwhere(peaks)
        if len(candidates):
            indices = tuple(candidates.T)
            order = np.lexsort(
                (candidates[:, -1], candidates[:, 0], -scores[indices], distances[indices])
            )
            proposal = positions[tuple(candidates[order[0]])]
            # Exact refinement is bounded independently of event count. Very
            # detailed polygons use the anchor and grid proposal only, preserving
            # every vertex while avoiding nine additional full polygon scans.
            refinement = (
                [(0,) * len(columns)]
                if len(gate.vertices) > 128
                else list(itertools.product((-1, 0, 1), repeat=len(columns)))
            )
            choices = []
            for delta in refinement:
                candidate = proposal + np.asarray(delta) / CELLS
                if np.linalg.norm(candidate) > radius + 1e-12:
                    continue
                moved = translated(gate, candidate * widths)
                try:
                    center, size = frame(moved)
                except ValueError:
                    continue
                if not np.all(np.isfinite(center)) or not np.allclose(
                    size, widths, rtol=1e-12, atol=0
                ):
                    continue
                value = int(shape_mask(moved, local).sum())
                choices.append((-value, np.linalg.norm(candidate), tuple(candidate)))
            choices.append((-anchor_count, 0, tuple(shift)))
            best = min(choices)
            count, shift = -best[0], np.asarray(best[2])
    moved = translated(gate, shift * widths)
    # The final count covers the entire parent, including existing polygon
    # boundary tolerance outside the nominal bounding box. A grid proposal can
    # never replace the anchor with a position containing fewer actual events.
    count = int(shape_mask(moved, eligible).sum())
    if count < anchor_count:
        shift, count = np.zeros(len(columns)), anchor_count
        moved = translated(gate, shift * widths)
    parent_count = int(parent.sum())
    report = dict(
        algorithm=ALGORITHM,
        grid_per_gate_width=CELLS,
        status=status,
        anchor=anchor.tolist(),
        position=(anchor + shift * widths).tolist(),
        shift=(shift * widths).tolist(),
        distance=float(np.linalg.norm(shift)),
        max_shift=radius,
        near_limit=bool(np.linalg.norm(shift) > 0 and np.linalg.norm(shift) >= radius - 1 / CELLS),
        anchor_count=anchor_count,
        resolved_count=count,
        parent_count=parent_count,
        finite_parent_count=finite_count,
        population_count=parent_count - count if gate.complement else count,
    )
    return moved, report
