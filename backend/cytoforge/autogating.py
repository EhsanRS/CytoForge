"""Full-parent highest-density contours, selected by a scientific-coordinate seed.

Preview is read-only. The accepted outline is a static polygon, with explicit
excluded rings, rather than a convex hull or a sampled-event approximation.
"""

from __future__ import annotations

import contourpy
import numpy as np
from pydantic import Field, model_validator
from scipy.ndimage import label

from .graph_views import cell_boundary_paths, density_grid, fraction, smooth_grid
from .models import Gate, Id, Model, Name, PlotDimension, Transform
from .plot_coordinates import resolve_dimension
from .science import polygon_mask, shape_mask
from .virtual_groups import Scope

ALGORITHM = "highest-density-seeded-contour-v1"


class AutoGateRequest(Model):
    revision: int = Field(ge=0)
    sample_id: Id
    scope: Scope | None = None
    parent_id: Id | None = None
    coordinate_gate_id: Id | None = None
    x: Name
    y: Name
    x_transform: Transform | None = None
    y_transform: Transform | None = None
    x_dimension: PlotDimension | None = None
    y_dimension: PlotDimension | None = None
    seed: tuple[float, float]
    coverage: float = Field(default=0.9, ge=0.01, le=0.995)
    sigma: float = Field(default=1.2, ge=0, le=4)
    bins: int = Field(default=128, ge=32, le=384)
    domain: tuple[float, float, float, float] | None = None

    @model_validator(mode="after")
    def valid_domain(self):
        if self.domain and not (
            self.domain[0] < self.domain[1] and self.domain[2] < self.domain[3]
        ):
            raise ValueError("Automatic gate density bounds must be increasing")
        return self


def extent(values, padding):
    scale = max(float(np.max(np.abs(values))), 1.0)
    low, high = float(values.min() / scale), float(values.max() / scale)
    margin = (high - low) * padding if high > low else max(abs(low) * 0.1, 1 / scale)
    bound = np.finfo(float).max / scale
    limits = [max(-bound, low - margin) * scale, min(bound, high + margin) * scale]
    if not limits[0] < limits[1]:
        raise ValueError("This parameter has no representable automatic-gating extent")
    return limits


def area(ring):
    return float(
        np.sum(ring[:, 0] * np.roll(ring[:, 1], -1) - ring[:, 1] * np.roll(ring[:, 0], -1))
    )


def selected_rings(grid, threshold, seed, smoothed):
    """Keep a single component and all its holes, without joining components."""
    rows, columns = grid.shape
    if not smoothed or threshold == float(grid.max()):
        regions, count = label(grid >= threshold)  # Four-connected cell union.
        ix = min(int(seed[0] * columns), columns - 1)
        iy = min(int(seed[1] * rows), rows - 1)
        component = regions[iy, ix]
        if not component:
            raise ValueError("Click inside a density region, or increase coverage")
        included = regions == component
        if included[0].any() or included[-1].any() or included[:, 0].any() or included[:, -1].any():
            raise ValueError(
                "Selected density region touches the domain boundary; enlarge density bounds"
            )
        rings = [path[:-1] for path in cell_boundary_paths(included)]
        outer = max(range(len(rings)), key=lambda i: abs(area(rings[i])))
        return (
            [rings[outer], *(ring for i, ring in enumerate(rings) if i != outer)],
            int(count),
            "bin_cells",
        )
    positions_x = (np.arange(columns + 2) - 0.5) / columns
    positions_y = (np.arange(rows + 2) - 0.5) / rows
    generator = contourpy.contour_generator(
        x=positions_x, y=positions_y, z=np.pad(grid, 1), fill_type="OuterOffset", corner_mask=False
    )
    points, offsets = generator.filled(np.nextafter(threshold, -np.inf), np.inf)
    for patch, boundaries in zip(points, offsets, strict=True):
        rings = [
            patch[start : end - 1]
            for start, end in zip(boundaries[:-1], boundaries[1:], strict=True)
        ]
        selected = bool(polygon_mask(np.array([seed[0]]), np.array([seed[1]]), rings[0])[0])
        if selected and not any(
            polygon_mask(np.array([seed[0]]), np.array([seed[1]]), ring)[0] for ring in rings[1:]
        ):
            if any(np.any((ring <= 0) | (ring >= 1)) for ring in rings):
                raise ValueError(
                    "Selected density region touches the domain boundary; enlarge density bounds"
                )
            return rings, len(points), "interpolated_centers"
    raise ValueError("Click inside a density region, or increase coverage")


def preview(doc, engine, request):
    sample = engine.sample(doc, request.sample_id)
    if request.parent_id and not any(
        g.id == request.parent_id and g.sample_id == request.sample_id for g in doc.gates
    ):
        raise ValueError("Automatic gate parent does not belong to this sample")
    source = next(
        (
            g
            for g in doc.gates
            if g.id == request.coordinate_gate_id and g.sample_id == request.sample_id
        ),
        None,
    )
    if request.coordinate_gate_id and source is None:
        raise ValueError("Automatic gate coordinate gate does not belong to this sample")
    dimensions = [
        resolve_dimension(doc, sample, name, source, axis, explicit, transform)
        for axis, (name, transform, explicit) in enumerate(
            (
                (request.x, request.x_transform, request.x_dimension),
                (request.y, request.y_transform, request.y_dimension),
            )
        )
    ]
    columns = [engine.dimension(doc, sample, dimension) for dimension in dimensions]
    parent = engine.mask(doc, sample, request.parent_id)
    finite = parent & np.isfinite(columns[0]) & np.isfinite(columns[1])
    denominator = int(finite.sum())
    if not denominator:
        raise ValueError("Automatic gating requires finite events in the parent population")
    domain = (
        list(request.domain)
        if request.domain
        else [
            *extent(columns[0][finite], max(0.04, 4 * request.sigma / request.bins)),
            *extent(columns[1][finite], max(0.04, 4 * request.sigma / request.bins)),
        ]
    )
    seed = [
        float(fraction(np.array([v]), domain[2 * i : 2 * i + 2])[0])
        for i, v in enumerate(request.seed)
    ]
    if not all(0 <= v < 1 for v in seed):
        raise ValueError("Click inside the automatic-gating density domain")
    key = (
        "autogate-density",
        doc.id,
        doc.revision,
        sample.id,
        sample.sha256,
        request.parent_id,
        tuple(d.model_dump_json() for d in dimensions),
        tuple(domain),
        request.bins,
        request.sigma,
    )
    fields = engine.cache.get(key)
    if fields is None:
        raw = density_grid(columns[0][finite], columns[1][finite], domain, request.bins)
        smoothed = smooth_grid(raw, {"smooth": request.sigma > 0, "sigma": request.sigma})
        fields = engine.cache.put(key, np.stack([raw, smoothed]))
    raw, grid = fields
    ordered = np.sort(grid.ravel())[::-1]
    cumulative = np.cumsum(ordered)
    target = request.coverage * denominator
    if not cumulative[-1] or target > cumulative[-1] * (1 + 1e-12):
        raise ValueError(
            "Insufficient finite-parent mass inside density bounds; enlarge the domain"
        )
    threshold = float(ordered[min(int(np.searchsorted(cumulative, target)), len(ordered) - 1)])
    rings, components, geometry = selected_rings(grid, threshold, seed, request.sigma > 0)
    if len(rings) > 129 or sum(map(len, rings)) > 2000:
        raise ValueError(
            "Automatic outline exceeds 2,000 vertices or 128 holes; "
            "reduce resolution or increase smoothing"
        )
    vertices = [
        np.column_stack(
            [(1 - ring[:, i]) * domain[2 * i] + ring[:, i] * domain[2 * i + 1] for i in range(2)]
        ).tolist()
        for ring in rings
    ]
    audit = dict(
        algorithm=ALGORITHM,
        revision=doc.revision,
        sample_sha256=sample.sha256,
        seed=list(request.seed),
        coverage=request.coverage,
        sigma=request.sigma,
        bins=request.bins,
        domain=domain,
        threshold=threshold,
        geometry=geometry,
        component_count=components,
        hole_count=len(rings) - 1,
        vertex_count=sum(map(len, rings)),
        finite_parent_count=denominator,
        parent_count=int(parent.sum()),
        density_count=int(raw.sum()),
        estimated_probability=float(grid[grid >= threshold].sum()) / denominator,
        tied_bins=int(np.count_nonzero(grid == threshold)),
    )
    gate = Gate(
        name="Automatic population",
        sample_id=request.sample_id,
        parent_id=request.parent_id,
        kind="polygon",
        x=request.x,
        y=request.y,
        dimensions=dimensions,
        x_transform=dimensions[0].transform,
        y_transform=dimensions[1].transform,
        vertices=vertices[0],
        holes=vertices[1:],
        provenance={"automatic_gate": audit},
    )
    count = int(np.count_nonzero(parent & shape_mask(gate, columns)))
    return dict(
        gate=gate.model_dump(),
        revision=doc.revision,
        count=count,
        parent_count=int(parent.sum()),
        percent_parent=100 * count / int(parent.sum()),
        audit=audit,
    )
