"""Full-event display estimates; display sampling never defines a gate mask."""

from __future__ import annotations

import contourpy
import numpy as np
from scipy.ndimage import gaussian_filter

from .models import GraphOptions


def resolved_options(options, mode):
    result = (options or GraphOptions()).model_dump()
    # Presentation changes must never invalidate full-event scientific caches.
    result.pop("typography", None)
    result.pop("gate_style", None)
    if result["smooth"] is None:
        result["smooth"] = mode in {"contour", "zebra", "pseudocolor"}
    if result["contour_spacing"] is None:
        result["contour_spacing"] = "2" if mode == "zebra" else "5"
    return result


def fraction(values, limits):
    scale = max(abs(limits[0]), abs(limits[1]), 1e-300)
    return (values / scale - limits[0] / scale) / (limits[1] / scale - limits[0] / scale)


def density_grid(x, y, limits, bins):
    inside = (x >= limits[0]) & (x <= limits[1]) & (y >= limits[2]) & (y <= limits[3])
    grid, _, _ = np.histogram2d(
        np.clip(fraction(x[inside], limits[:2]), 0, 1),
        np.clip(fraction(y[inside], limits[2:]), 0, 1),
        bins=bins,
        range=[(0, 1), (0, 1)],
    )
    return grid.T


def smooth_grid(counts, options):
    grid = (
        gaussian_filter(counts.astype(float), options["sigma"], mode="constant")
        if options["smooth"] and options["sigma"] > 0
        else counts.astype(float)
    )
    total = float(grid.sum())
    if total:
        grid *= float(counts.sum()) / total
    return grid


def cdf_values(values, edges):
    """Exact F(t)=P(X<=t) at every displayed edge, including events below the view."""
    groups = np.bincount(np.searchsorted(edges, values, side="left"), minlength=len(edges) + 1)
    counts = np.cumsum(groups[: len(edges)], dtype=np.int64)
    return dict(
        cdf_counts=counts.tolist(),
        cdf_percent=(counts.astype(float) * (100 / len(values))).tolist()
        if len(values)
        else [None] * len(edges),
        cdf_denominator=len(values),
        cdf_undefined_reason=None if len(values) else "No finite events in this population",
        cdf_below_view=int(np.count_nonzero(values < edges[0])),
        cdf_above_view=int(np.count_nonzero(values > edges[-1])),
    )


def sampled_points(x, y, indices, limit, seed=42):
    # Uniform deterministic sampling is explicitly reported, never a population count.
    if len(indices) > limit:
        indices = np.sort(np.random.default_rng(seed).choice(indices, limit, replace=False))
    return np.column_stack([x[indices], y[indices]]).tolist()


def cell_boundary_paths(included):
    """Trace the exact union of included bins, including holes and corner contacts.

    Two samples per bin leave a full quad inside every included cell. Contouring
    this binary raster at 0.5 separates diagonal contacts. Mapping its crossings
    back to integer cell corners then recovers the cell edges, without changing
    a scientific density threshold or depending on degenerate contour quads.
    """
    rows, columns = included.shape
    raster = np.pad(np.repeat(np.repeat(included, 2, axis=0), 2, axis=1), 1)
    generator = contourpy.contour_generator(
        z=raster, line_type="ChunkCombinedOffset", corner_mask=False
    )
    for combined, offsets in zip(*generator.lines(0.5), strict=True):
        if combined is None:
            continue
        # Limit temporary vector buffers for highly fragmented regions. Each
        # batch contains whole closed rings, preserving topology and order.
        for first in range(0, len(offsets) - 1, 4096):
            last = min(first + 4096, len(offsets) - 1)
            batch = combined[offsets[first] : offsets[last]]
            local_offsets = offsets[first : last + 1] - offsets[first]
            corners = np.floor(batch / 2).astype(np.int64)
            keep = np.r_[True, np.any(corners[1:] != corners[:-1], axis=1)]
            # Adjacent rings may touch at a corner: retain each ring's own start.
            keep[local_offsets[:-1]] = True
            counts = np.add.reduceat(keep, local_offsets[:-1], dtype=np.int64)
            compact_offsets = np.r_[0, np.cumsum(counts)]
            corners = corners[keep]
            # The padded binary regions are closed. Drop each duplicate endpoint
            # while finding corners; never use a neighboring ring's points.
            open_ring = np.ones(len(corners), dtype=bool)
            open_ring[compact_offsets[1:] - 1] = False
            corners = corners[open_ring]
            ring_offsets = compact_offsets - np.arange(len(compact_offsets))
            starts, ends = ring_offsets[:-1], ring_offsets[1:] - 1
            incoming = corners - np.roll(corners, 1, axis=0)
            outgoing = np.roll(corners, -1, axis=0) - corners
            incoming[starts] = corners[starts] - corners[ends]
            outgoing[ends] = corners[starts] - corners[ends]
            turns = incoming[:, 0] * outgoing[:, 1] != incoming[:, 1] * outgoing[:, 0]
            turn_counts = np.add.reduceat(turns, starts, dtype=np.int64)
            turn_offsets = np.r_[0, np.cumsum(turn_counts)]
            closed_offsets = turn_offsets + np.arange(len(turn_offsets))
            normalized = corners[turns] / [columns, rows]
            closed = np.empty((closed_offsets[-1], 2))
            endpoints = np.zeros(len(closed), dtype=bool)
            endpoints[closed_offsets[1:] - 1] = True
            closed[~endpoints] = normalized
            closed[endpoints] = normalized[turn_offsets[:-1]]
            for start, end in zip(closed_offsets[:-1], closed_offsets[1:], strict=True):
                yield closed[start:end]


def probability_view(x, y, limits, view, bins, options):
    """Highest-density probability regions on a fixed grid, with tied-bin coverage audit."""
    raw = density_grid(x, y, limits, bins)
    grid = smooth_grid(raw, options)
    denominator = len(x)
    ordered = np.sort(grid.ravel())[::-1]
    cumulative = np.cumsum(ordered)
    spacing = options["contour_spacing"]
    targets = (
        [0.95 / 2**i for i in range(10)]
        if spacing == "log"
        else (np.arange(1, int(100 / int(spacing))) * int(spacing) / 100).tolist()
    )
    levels = []
    for probability in sorted(targets, reverse=True):
        target = probability * denominator
        if not denominator or not cumulative[-1] or target > cumulative[-1] * (1 + 1e-12):
            levels.append(
                dict(
                    probability=probability,
                    threshold=None,
                    reason="Insufficient finite mass inside density bounds",
                )
            )
            continue
        index = min(int(np.searchsorted(cumulative, target)), len(ordered) - 1)
        threshold = float(ordered[index])
        included = grid >= threshold
        levels.append(
            dict(
                probability=probability,
                threshold=threshold,
                estimated_probability=float(grid[included].sum()) / denominator,
                binned_event_probability=int(raw[included].sum()) / denominator,
                tied_bins=int(np.count_nonzero(grid == threshold)),
                reason=None,
            )
        )
    unique = sorted({item["threshold"] for item in levels if item["threshold"] is not None})
    positions = (np.arange(bins + 2) - 0.5) / bins
    generator = contourpy.contour_generator(
        x=positions,
        y=positions,
        z=np.pad(grid, 1),
        line_type="Separate",
        corner_mask=False,
    )
    contours = []
    vertices = 0
    truncated = False
    smoothed = bool(options["smooth"] and options["sigma"] > 0)
    peak = float(grid.max())
    # Bound payload size. Statistics remain complete even when drawing reaches this limit.
    for threshold in unique:
        paths = []
        # Inclusive peak thresholds otherwise collapse to zero-area paths, even
        # when moved down by one ULP. Unsmoothed fields are piecewise constant,
        # so their regions follow cell edges rather than interpolated centers.
        geometry = "bin_cells" if not smoothed or threshold == peak else "interpolated_centers"
        lines = (
            cell_boundary_paths(grid >= threshold)
            if geometry == "bin_cells"
            else generator.lines(np.nextafter(threshold, -np.inf))
        )
        for path in lines:
            if vertices + len(path) > 150000:
                truncated = True
                break
            paths.append(path.tolist() if geometry == "bin_cells" else np.clip(path, 0, 1).tolist())
            vertices += len(path)
        contours.append(dict(threshold=threshold, geometry=geometry, paths=paths))
        if truncated:
            break
    bands = np.searchsorted(unique, grid, side="right") if unique else np.zeros_like(raw)
    outer = unique[0] if unique else None
    inside = (x >= limits[0]) & (x <= limits[1]) & (y >= limits[2]) & (y <= limits[3])
    density = np.zeros(len(x))
    if inside.any():
        ix = np.clip((fraction(x[inside], limits[:2]) * bins).astype(int), 0, bins - 1)
        iy = np.clip((fraction(y[inside], limits[2:]) * bins).astype(int), 0, bins - 1)
        density[inside] = grid[iy, ix]
    outlier = np.ones(len(x), dtype=bool) if outer is None else density < outer
    visible = (x >= view[0]) & (x <= view[1]) & (y >= view[2]) & (y <= view[3])
    indices = np.flatnonzero(outlier & visible)
    points = (
        sampled_points(x, y, indices, options["point_limit"], 44)
        if options["show_outliers"]
        else []
    )
    return dict(
        density_bounds=limits,
        density_field=grid.ravel().tolist(),
        density_max=peak,
        density_count=int(raw.sum()),
        probability_denominator=denominator,
        probability_levels=levels,
        contours=contours,
        contour_geometry=(
            "Contours follow bin edges without smoothing and at peak density; "
            "other smoothed levels interpolate bin centers."
        ),
        contour_vertices=vertices,
        contours_truncated=truncated,
        zebra_bands=bands.ravel().astype(int).tolist(),
        zebra_max_band=len(unique),
        outlier_count=int(outlier.sum()),
        outlier_visible_count=len(indices),
        outlier_points=points,
        outlier_displayed_count=len(points),
        outlier_sampling="uniform deterministic; seed 44"
        if len(points) < len(indices) and options["show_outliers"]
        else None,
        probability_method=(
            "Highest density regions of a mass-preserving binned Gaussian estimate; ties included"
            if smoothed
            else "Highest density regions of unsmoothed full-event bin counts; ties included"
        ),
    )
