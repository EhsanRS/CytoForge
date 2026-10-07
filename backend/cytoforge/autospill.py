"""AutoSpill: Huber regressions, tessellation cleanup and two-scale refinement.

Algorithm adapted from Roca et al. (2021), doi:10.1038/s41467-021-23126-8,
and carlosproca/autospill, commit 1e60e86337b297f1dd9ffec6701d8010dd06175b.
Copyright (c) 2020 VIB (Belgium) & Babraham Institute (United Kingdom).
The MIT notice is distributed in licenses/AutoSpill-MIT.txt.
See docs/AUTOSPILL.md for reference comparisons and explicit differences.
"""

from __future__ import annotations

import hashlib
import json
import math
import platform
import threading
from pathlib import Path
from typing import Literal

import numpy as np
import scipy
from pydantic import Field, model_validator
from scipy.interpolate import RegularGridInterpolator
from scipy.ndimage import maximum_filter
from scipy.spatial import ConvexHull, cKDTree

from . import quality
from .acquired_gates import acquired_gate_copies
from .analysis import atomic_json, watch_parent
from .autospill_biex import biex_functions
from .fileio import close_array
from .models import Compensation, Gate, GateDimension, Id, Model, Name, Transform, Workspace, new_id
from .science import Engine, polygon_mask, validate_matrix
from .store import Store, now

METHOD_VERSION = "autospill-1"
REFERENCE_COMMIT = "1e60e86337b297f1dd9ffec6701d8010dd06175b"
MAX_REGRESSION_VALUES = 64_000_000


class RegressionControl(Model):
    name: Name
    primary_detector: Name
    sample_id: Id
    gate_id: Id | None = None


class AutoSpillRequest(Model):
    revision: int = Field(ge=0)
    algorithm: Literal["autospill"] = "autospill"
    name: Name = "AutoSpill compensation"
    kind: Literal["spillover", "spectral"] = "spillover"
    detectors: list[Name] = Field(min_length=2, max_length=512)
    controls: list[RegressionControl] = Field(min_length=2, max_length=64)
    af_detector: Name | None = None
    af_output: Name | None = None
    af_outputs: list[Name] = Field(default_factory=list, max_length=64)
    background: list[float] = Field(default_factory=list, max_length=512)
    weights: list[float] = Field(default_factory=list, max_length=512)
    auto_cleanup: bool = True
    scatter_x: Name = "FSC-A"
    scatter_y: Name = "SSC-A"
    density_threshold: float = Field(default=0.33, gt=0, lt=1)
    target_peak: int = Field(default=1, ge=1, le=64)
    min_events: int = Field(default=100, ge=20, le=100000)
    max_events: int | None = Field(default=None, ge=100, le=2000000)
    trim_fraction: float = Field(default=0.01, ge=0, le=0.1)
    max_iterations: int = Field(default=100, ge=1, le=200)
    regression_iterations: int = Field(default=100, ge=1, le=200)
    linear_tolerance: float = Field(default=1e-2, gt=0, le=0.1)
    tolerance: float = Field(default=1e-4, ge=1e-8, le=0.01)
    plateau_tolerance: float = Field(default=1e-6, ge=1e-10, le=0.001)
    damping: float = Field(default=0.1, gt=0, lt=1)
    biex_length: Literal[256, 4096] = 256
    biex: Transform = Field(
        default_factory=lambda: Transform(
            kind="wsp_biex", width=-100, positive=4.418539922, top=262144
        )
    )
    detector_transforms: dict[Name, Transform] = Field(default_factory=dict, max_length=64)

    @model_validator(mode="after")
    def valid_controls(self):
        if len(set(self.detectors)) != len(self.detectors):
            raise ValueError("AutoSpill detectors must be unique")
        primaries = [c.primary_detector for c in self.controls]
        if len({c.name for c in self.controls}) != len(self.controls):
            raise ValueError("Control names must be unique")
        if self.kind == "spillover":
            if len(self.detectors) > 64:
                raise ValueError("Conventional AutoSpill supports at most 64 detectors")
            if len(primaries) != len(set(primaries)) or set(primaries) != set(self.detectors):
                raise ValueError("AutoSpill needs one control for each selected primary detector")
            if self.af_output or self.af_outputs or self.background or self.weights:
                raise ValueError(
                    "Output autofluorescence, background and weights require spectral unmixing"
                )
            if self.af_detector and self.af_detector not in self.detectors:
                raise ValueError("The autofluorescence detector must be selected")
        else:
            if len(self.controls) > len(self.detectors):
                raise ValueError("More spectral sources than detectors cannot be unmixed uniquely")
            if not set(primaries) <= set(self.detectors):
                raise ValueError("Each spectral control needs a selected primary detector")
            if self.af_detector:
                raise ValueError("Choose an autofluorescence output for spectral AutoSpill")
            if self.af_output and self.af_output not in {c.name for c in self.controls}:
                raise ValueError("The autofluorescence output must have a control")
            if self.af_output and self.af_outputs:
                raise ValueError("Use either af_output or af_outputs for AF references")
            if len(set(self.af_outputs)) != len(self.af_outputs):
                raise ValueError("Autofluorescence outputs must be unique")
            if not set(self.af_outputs) <= {c.name for c in self.controls}:
                raise ValueError("Every autofluorescence output must have a control")
            if self.background and (
                len(self.background) != len(self.detectors)
                or not np.isfinite(self.background).all()
            ):
                raise ValueError("Background must be finite and match the selected detectors")
            if self.weights and (
                len(self.weights) != len(self.detectors)
                or not np.isfinite(self.weights).all()
                or min(self.weights) <= 0
            ):
                raise ValueError(
                    "Weights must be finite, positive and match the selected detectors"
                )
        if self.auto_cleanup and self.scatter_x == self.scatter_y:
            raise ValueError("Cleanup requires two distinct acquired scatter channels")
        if self.max_events is not None and self.max_events < self.min_events:
            raise ValueError("Event limit must be at least the minimum finite event count")
        if not set(self.detector_transforms) <= set(output_names(self)):
            raise ValueError("Biex overrides must refer to selected refinement outputs")
        for spec in [self.biex, *self.detector_transforms.values()]:
            if spec.kind != "wsp_biex" or spec.bound_min is not None or spec.bound_max is not None:
                raise ValueError("AutoSpill refinement requires an unbounded FlowJo biex transform")
        return self


class AutoSpillResult(Model):
    id: Id = Field(default_factory=new_id)
    request: AutoSpillRequest
    created_at: str
    input_hash: str
    input_snapshot: dict
    compensation: Compensation
    diagnostics: dict
    warnings: list[str]


def output_names(request: AutoSpillRequest) -> list[str]:
    return [c.name for c in request.controls] if request.kind == "spectral" else request.detectors


def autofluorescence_outputs(request: AutoSpillRequest) -> list[str]:
    selected = set(request.af_outputs or ([request.af_output] if request.af_output else []))
    return [c.name for c in request.controls if c.name in selected]


def ordered_controls(request: AutoSpillRequest) -> list[RegressionControl]:
    if request.kind == "spectral":
        return request.controls
    by_primary = {c.primary_detector: c for c in request.controls}
    return [by_primary[d] for d in request.detectors]


def settings_snapshot(request: AutoSpillRequest) -> dict:
    excluded = {"revision", "name"}
    af_names = autofluorescence_outputs(request)
    if len(af_names) <= 1:
        excluded.add("af_outputs")
    if request.kind == "spillover":
        # Keep scientific fingerprints of existing saved square calculations stable.
        excluded |= {"kind", "af_output", "background", "weights"}
    settings = request.model_dump(exclude=excluded)
    if request.kind == "spectral":
        if len(af_names) == 1:
            settings["af_output"] = af_names[0]
        elif af_names:
            settings["af_outputs"] = af_names
    return settings


def input_snapshot(workspace: Workspace, request: AutoSpillRequest, *, check_qc=False) -> dict:
    """Scientific identity of acquired inputs; assigned compensation is irrelevant."""
    samples, gates, qc = {}, {}, {}
    by_gate = {g.id: g for g in workspace.gates}
    for control in request.controls:
        sample = next((s for s in workspace.samples if s.id == control.sample_id), None)
        if sample is None:
            raise ValueError(f"{control.name}: control sample does not exist")
        acquired = {c.name for c in sample.acquisition_channels}
        if request.kind == "spectral" and set(output_names(request)) & acquired:
            raise ValueError("Spectral output names must be distinct from acquired parameters")
        required = set(request.detectors)
        if request.auto_cleanup:
            required |= {request.scatter_x, request.scatter_y}
        if not required <= acquired:
            raise ValueError(f"{sample.name}: missing acquired detectors or cleanup channels")
        visiting = set()

        def visit(
            identifier,
            sample=sample,
            visiting=visiting,
            acquired=acquired,
        ):
            if not identifier:
                return
            gate = by_gate.get(identifier)
            if gate is None or gate.sample_id != sample.id:
                raise ValueError("Control cleanup gates must belong to their sample")
            if identifier in gates:
                return
            if identifier in visiting:
                raise ValueError("Cleanup gate dependencies contain a cycle")
            visiting.add(identifier)
            visit(gate.parent_id)
            for operand in gate.operands:
                visit(operand)
            names = (
                {name for d in gate.dimensions for name in d.ratio_channels or [d.channel]}
                if gate.dimensions
                else {n for n in (gate.x, gate.y) if n}
            )
            if not names <= acquired:
                raise ValueError("AutoSpill control gates and ratios must use acquired parameters")
            if gate.kind == "quality":
                result = next(
                    (q for q in workspace.quality_results if q.id == gate.quality_id), None
                )
                if (
                    result is None
                    or result.request.sample_id != sample.id
                    or result.data.event_count != sample.event_count
                ):
                    raise ValueError(
                        "Control QC population is missing or belongs to another sample"
                    )
                captured = quality.is_captured_gate(gate, result)
                if check_qc and not captured and quality.is_stale(workspace, result):
                    raise ValueError(
                        "Control QC population is stale; recalculate and review QC first"
                    )
                if any(index >= len(result.bins) for index in gate.quality_excluded_bins):
                    raise ValueError("Control QC exclusion references a missing acquisition bin")
                qc[result.id] = {
                    "id": result.id,
                    "sample_id": result.request.sample_id,
                    "input_hash": result.input_hash,
                    "data": result.data.model_dump(),
                    "bins": [
                        {"index": b.index, "start": b.start, "end": b.end} for b in result.bins
                    ],
                    "selection_basis": "captured_reviewed_event_flags",
                }
            canonical_gate = Gate.model_validate(gate.model_dump(exclude={"provenance"}))
            gates[identifier] = canonical_gate.model_dump(exclude={"name", "color", "provenance"})
            visiting.remove(identifier)

        visit(control.gate_id)
        samples[sample.id] = {
            "id": sample.id,
            "sha256": sample.sha256,
            "event_count": sample.event_count,
            "acquisition_order": [c.name for c in sample.acquisition_channels],
            "ranges": {
                c.name: float(c.range) for c in sample.acquisition_channels if c.name in required
            },
        }
    snapshot = {
        "method": "autospill-spectral-1" if request.kind == "spectral" else METHOD_VERSION,
        "reference_commit": REFERENCE_COMMIT,
        "refinement_transform": "native-biex-natural-spline-v1",
        "settings": settings_snapshot(AutoSpillRequest.model_validate(request.model_dump())),
        "samples": sorted(samples.values(), key=lambda s: s["id"]),
        "gates": sorted(gates.values(), key=lambda g: g["id"]),
    }
    if qc:
        # Existing calculations without QC keep their scientific fingerprints.
        snapshot["qc"] = qc
    return snapshot


def validate_request(workspace: Workspace, request: AutoSpillRequest):
    input_snapshot(workspace, request, check_qc=True)


def verify_qc_data(workspace, request, store):
    for identifier in input_snapshot(workspace, request).get("qc", {}):
        result = next(q for q in workspace.quality_results if q.id == identifier)
        close_array(quality.load_data(store, workspace.id, result))


def verify_control_data(workspace, request, store):
    for sample_id in {control.sample_id for control in request.controls}:
        sample = next(sample for sample in workspace.samples if sample.id == sample_id)
        with store.data_path(workspace.id, sample.id).open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != sample.sha256:
            raise ValueError(f"{sample.name}: acquired control data failed its integrity check")
    verify_qc_data(workspace, request, store)
    from .population_snapshot import verify_dependencies

    engine = Engine(store, cache_bytes=32 * 1024**2)
    for control in request.controls:
        sample = engine.sample(workspace, control.sample_id)
        verify_dependencies(engine, workspace, sample, control.gate_id)


def input_hash(workspace: Workspace, request: AutoSpillRequest) -> str:
    return hashlib.sha256(
        json.dumps(input_snapshot(workspace, request), sort_keys=True).encode()
    ).hexdigest()


def is_stale(workspace: Workspace, result: AutoSpillResult) -> bool:
    try:
        return input_hash(workspace, result.request) != result.input_hash
    except (ValueError, KeyError, StopIteration):
        return True


def robust_line(x, y, iterations=100):
    """Huber M-estimate, k=1.345, residual MAD scale, relative residual tolerance 1e-4.

    Normalized coordinates keep the weighted normal equations well conditioned.
    An unconverged IRLS fit falls back to OLS, explicitly recorded as in AutoSpill R.
    """
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if len(x) < 3 or not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("Regression needs at least three finite paired events")
    x_scale, y_scale = max(float(np.max(np.abs(x))), 1e-300), max(float(np.max(np.abs(y))), 1e-300)
    xn, yn = x / x_scale, y / y_scale
    x_center, y_center = float(np.mean(xn)), float(np.mean(yn))
    xn, yn = xn - x_center, yn - y_center
    spread = float(np.dot(xn, xn))
    if spread <= np.finfo(float).eps ** 2 * len(x):
        raise ValueError("Primary detector has insufficient variation for a regression")
    initial_slope = float(np.dot(xn, yn) / spread)
    slope, intercept = initial_slope, 0.0
    residual = yn - slope * xn
    converged = False
    iteration = 0
    for _ in range(iterations):
        iteration += 1
        scale = float(np.median(np.abs(residual))) / 0.6745
        if scale <= np.finfo(float).eps * max(1.0, abs(slope)):
            converged = True
            break
        previous = residual
        weights = np.minimum(1.0, 1.345 * scale / np.maximum(np.abs(residual), 1e-300))
        total = float(np.sum(weights))
        wx, wy = float(np.dot(weights, xn) / total), float(np.dot(weights, yn) / total)
        centered = xn - wx
        denominator = float(np.dot(weights, centered**2))
        if denominator <= np.finfo(float).eps ** 2 * total:
            raise ValueError("Weighted regression primary detector is singular")
        slope = float(np.dot(weights * centered, yn - wy) / denominator)
        intercept = wy - slope * wx
        residual = yn - (intercept + slope * xn)
        change = np.sqrt(
            float(np.dot(residual - previous, residual - previous))
            / max(1e-20, float(np.dot(previous, previous)))
        )
        if change <= 1e-4:
            converged = True
            break
    if not converged:
        slope, intercept = initial_slope, 0.0
    coefficient = float(slope * (y_scale / x_scale))
    offset = float((y_center + intercept - slope * x_center) * y_scale)
    if not np.isfinite(coefficient) or not np.isfinite(offset):
        raise ValueError("Regression coefficients exceeded finite numeric limits")
    return (
        offset,
        coefficient,
        {"iterations": iteration, "converged": converged, "fallback_ols": not converged},
    )


def trimmed_pair(x, y, fraction, minimum):
    """R's rounded 1% order statistics, strict comparisons, independently on both axes."""
    count = int(np.rint(len(x) * fraction))
    selected = np.ones(len(x), dtype=bool)
    retained_ties = False
    if count:
        for axis, values in enumerate((x, y)):
            if np.ptp(values) == 0:
                continue
            low, high = np.partition(values, [count - 1, len(values) - count])[
                [count - 1, len(values) - count]
            ]
            trimmed = (values > low) & (values < high)
            # A secondary ADC channel can be quantized, or numerically constant
            # after exact compensation. Keep tied secondary boundaries rather
            # than discarding the entire otherwise informative control.
            if axis == 1 and int((selected & trimmed).sum()) < minimum:
                inclusive = (values >= low) & (values <= high)
                if int((selected & inclusive).sum()) >= minimum:
                    trimmed, retained_ties = inclusive, True
            selected &= trimmed
    if int(selected.sum()) < minimum:
        raise ValueError(
            f"Only {int(selected.sum())} events remain after regression trimming; need {minimum}"
        )
    return x[selected], y[selected], int(selected.sum()), retained_ties


def density_grid(data, factor, size=100):
    """Separable Gaussian KDE with MASS's normal-reference bandwidth; bounded working memory."""
    n = len(data)
    if n < 3:
        raise ValueError("Scatter cleanup needs at least three events")
    grid = [np.linspace(float(data[:, k].min()), float(data[:, k].max()), size) for k in (0, 1)]
    widths = []
    for axis in (0, 1):
        values = data[:, axis]
        quartiles = np.quantile(values, [0.25, 0.75])
        width = (
            factor
            * 1.06
            * min(float(np.std(values, ddof=1)), float(np.diff(quartiles)[0] / 1.34))
            * n**-0.2
        )
        if not np.isfinite(width) or width <= 0:
            raise ValueError("Scatter cleanup needs nonconstant, continuous scatter intensities")
        widths.append(width)
    density = np.zeros((size, size))
    for start in range(0, n, 8192):
        part = data[start : start + 8192]
        kernels = [
            np.exp(-0.5 * ((grid[k][:, None] - part[:, k]) / widths[k]) ** 2) for k in (0, 1)
        ]
        density += kernels[0] @ kernels[1].T
    density /= 2 * np.pi * n * widths[0] * widths[1]
    return grid, density


def density_tile(data, factor, neighborhood, target=1, exclude_corner=False):
    grid, density = density_grid(data, factor)
    maxima = np.argwhere(
        density >= maximum_filter(density, size=2 * neighborhood + 1, mode="nearest")
    )
    # R orders equal-density maxima in column-major order. Ignore numerical zero plateaus.
    maxima = maxima[density[maxima[:, 0], maxima[:, 1]] > 0]
    order = np.lexsort((maxima[:, 0], maxima[:, 1], -density[maxima[:, 0], maxima[:, 1]]))
    maxima = maxima[order]
    coordinates = np.column_stack([grid[k][maxima[:, k]] for k in (0, 1)])
    if exclude_corner:
        normalized = (coordinates - data.min(axis=0)) / np.ptp(data, axis=0)
        eligible = np.flatnonzero(np.any(normalized > 0.05, axis=1))
        if not len(eligible):
            raise ValueError("No scatter population found outside the debris corner")
        chosen = int(eligible[0]) + target - 1
    else:
        chosen = target - 1
    if chosen >= len(coordinates):
        raise ValueError("Selected scatter density peak does not exist")
    nearest = cKDTree(coordinates).query(data)[1]
    return data[nearest == chosen], coordinates[chosen], len(coordinates)


def cleanup_gate(scatter, ranges, threshold=0.33, target=1):
    """Published two-tessellation cell/bead cleanup, retaining original event identities."""
    finite = np.isfinite(scatter).all(axis=1)
    data = scatter[finite]
    if len(data) < 20:
        raise ValueError("Too few finite scatter events for automatic cleanup")
    low = np.maximum(0.0, data.min(axis=0))
    high = np.minimum(ranges, data.max(axis=0))
    if np.any(high <= low):
        raise ValueError("Scatter data have no positive acquisition range")
    bounds = [low + 0.01 * (high - low), low + 0.99 * (high - low)]
    trimmed = data[np.all((data > bounds[0]) & (data < bounds[1]), axis=1)]
    bound, peak, peaks = density_tile(trimmed, 3.0, 3, target, exclude_corner=True)
    center = np.median(bound, axis=0)
    mad = 1.4826 * np.median(np.abs(bound - center), axis=0)
    region_low = np.maximum(bounds[0], center - 3 * mad)
    region_high = np.minimum(bounds[1], center + 3 * mad)
    region = data[np.all((data > region_low) & (data < region_high), axis=1)]
    tile, final_peak, final_peaks = density_tile(region, 2.0, 2)
    grid, density = density_grid(tile, 1.0)
    scores = RegularGridInterpolator(tuple(grid), density, bounds_error=True)(tile)
    level = (1 - threshold) * float(scores.min()) + threshold * float(scores.max())
    strict = np.unique(tile[scores > level], axis=0)
    if len(strict) < 3:
        raise ValueError("Scatter density selection cannot define a cleanup polygon")
    try:
        hull = ConvexHull(strict)
    except Exception as exc:
        raise ValueError("Scatter cleanup population is collinear") from exc
    vertices = strict[hull.vertices].tolist()
    # Include the convex-hull boundary, as the published sp::point.in.polygon does.
    selected = polygon_mask(scatter[:, 0], scatter[:, 1], vertices) & finite
    return selected, {
        "vertices": vertices,
        "bound_peak": peak.tolist(),
        "bound_peaks": peaks,
        "region_peak": final_peak.tolist(),
        "region_peaks": final_peaks,
        "density_threshold": threshold,
    }


def regression_matrix(data, request, transformed=False, functions=None):
    labels = output_names(request)
    n = len(labels)
    slopes, intercepts = np.eye(n), np.zeros((n, n))
    fits = []
    for primary, values in enumerate(data):
        if transformed:
            values = np.column_stack([functions[k][0](values[:, k]) for k in range(n)])
        row = []
        for secondary in range(n):
            if secondary == primary:
                row.append(
                    {
                        "count": len(values),
                        "converged": True,
                        "iterations": 0,
                        "fallback_ols": False,
                    }
                )
                continue
            x, y, count, retained_ties = trimmed_pair(
                values[:, primary],
                values[:, secondary],
                request.trim_fraction,
                max(20, min(request.min_events, 50)),
            )
            intercept, slope, fit = robust_line(x, y, request.regression_iterations)
            if transformed:
                x1, x2 = float(x.min()), float(x.max())
                y1, y2 = intercept + slope * x1, intercept + slope * x2
                primary_span = float(functions[primary][1](x2) - functions[primary][1](x1))
                secondary_span = float(functions[secondary][1](y2) - functions[secondary][1](y1))
                if primary_span <= 0:
                    raise ValueError(
                        f"{labels[primary]}: primary signal collapses under the biex "
                        "transform; adjust its range"
                    )
                slope = secondary_span / primary_span if y1 != y2 else 0.0
            intercepts[primary, secondary] = intercept
            slopes[primary, secondary] = slope
            row.append(dict(fit, count=count, retained_secondary_ties=retained_ties))
        fits.append(row)
    return slopes, intercepts, fits


def refine(data, initial, request, progress=lambda *_: None):
    functions = []
    for detector in request.detectors:
        spec = request.detector_transforms.get(detector, request.biex)
        functions.append(
            biex_functions(spec.negative, spec.width, spec.positive, spec.top, request.biex_length)
        )
    current = np.asarray(initial, dtype=float).copy()
    transformed, damping = False, 1.0
    history, previous, history_count = np.full(10, -1.0), -1.0, 0
    convergence, fallback_pairs = [], set()
    stop_reason, final_fits = "iteration_limit", []
    last_converged = False
    for iteration in range(request.max_iterations + 1):
        if not np.isfinite(current).all() or np.any(np.abs(np.diag(current)) < 1e-12):
            raise ValueError("Refinement produced a nonfinite or zero-primary spillover matrix")
        current /= np.diag(current)[:, None]
        condition = validate_matrix(
            Compensation(name=request.name, detectors=request.detectors, matrix=current.tolist())
        )
        inverse = np.linalg.inv(current)
        compensated = [values @ inverse for values in data]
        slopes, _, final_fits = regression_matrix(compensated, request, transformed, functions)
        error = slopes - np.eye(len(current))
        maximum = float(np.max(np.abs(error)))
        delta = float(np.std(error, ddof=1))
        history[iteration % 10] = delta - previous if previous >= 0 else -1.0
        history_count += int(previous >= 0)
        change = float(np.mean(history))
        convergence.append(
            {
                "iteration": iteration,
                "scale": "biex" if transformed else "linear",
                "damping": damping,
                "error_sd": delta,
                "max_error": maximum,
                "error_change": change,
                "condition_number": condition,
            }
        )
        progress(
            f"Refinement {iteration}: {'biex' if transformed else 'linear'} residual {maximum:.3g}",
            min(0.92, 0.35 + 0.56 * (iteration + 1) / request.max_iterations),
        )
        for i, row in enumerate(final_fits):
            for j, fit in enumerate(row):
                if fit["fallback_ols"]:
                    fallback_pairs.add((i, j))
        if transformed and maximum < request.tolerance:
            if last_converged:
                stop_reason = "target_reached"
                break
            last_converged = True
        else:
            last_converged = False
        if not transformed and maximum < request.linear_tolerance:
            transformed, damping = True, 1.0
            last_converged = maximum < request.tolerance
            history, previous, history_count = np.full(10, -1.0), -1.0, 0
        else:
            plateau = history_count >= 10 and change > -request.plateau_tolerance
            if plateau and damping == 1.0:
                damping = request.damping
                history, previous, history_count = np.full(10, -1.0), -1.0, 0
            elif plateau:
                stop_reason = "plateau"
                break
            else:
                previous = delta
        if iteration < request.max_iterations:
            current += damping * (error @ current)
    return current, {
        "converged": stop_reason == "target_reached",
        "stop_reason": stop_reason,
        "iterations": len(convergence) - 1,
        "final_scale": convergence[-1]["scale"],
        "initial_max_error": convergence[0]["max_error"],
        "final_max_error": convergence[-1]["max_error"],
        "residual_slopes": error.tolist(),
        "convergence": convergence,
        "regressions": final_fits,
        "fallback_pairs": [list(p) for p in sorted(fallback_pairs)],
    }


def calculate(workspace, request, engine, identifier=None, progress=lambda *_: None):
    snapshot = input_snapshot(workspace, request, check_qc=True)
    verify_qc_data(workspace, request, engine.store)
    controls = ordered_controls(request)
    af_sources = set(autofluorescence_outputs(request))
    data, diagnostics, warnings, selected_ids = [], [], [], []
    for index, control in enumerate(controls):
        detector = control.primary_detector
        sample = engine.sample(workspace, control.sample_id)
        raw = engine.raw(workspace, sample)
        acquired = [c.name for c in sample.acquisition_channels]
        mask = engine.mask(workspace, sample, control.gate_id, compensated=False).copy()
        parent_count = int(mask.sum())
        cleanup = None
        progress(f"Preparing {control.name}", 0.03 + 0.25 * index / len(controls))
        if request.auto_cleanup:
            scatter = raw[
                np.ix_(
                    np.flatnonzero(mask),
                    [acquired.index(request.scatter_x), acquired.index(request.scatter_y)],
                )
            ]
            ranges = [
                next(c.range for c in sample.acquisition_channels if c.name == name)
                for name in (request.scatter_x, request.scatter_y)
            ]
            clean_mask, cleanup = cleanup_gate(
                scatter, ranges, request.density_threshold, request.target_peak
            )
            indices = np.flatnonzero(mask)
            mask[indices[~clean_mask]] = False
            finite_ids = np.flatnonzero(np.isfinite(scatter).all(axis=1))
            preview_indices = finite_ids[
                np.linspace(0, len(finite_ids) - 1, min(600, len(finite_ids)), dtype=int)
            ]
            cleanup.update(
                {
                    "x": request.scatter_x,
                    "y": request.scatter_y,
                    "preview": scatter[preview_indices].tolist(),
                    "selected": clean_mask[preview_indices].tolist(),
                }
            )
        cleanup_count = int(mask.sum())
        candidates = np.flatnonzero(mask)
        columns = [acquired.index(n) for n in request.detectors]
        finite = np.empty(len(candidates), dtype=bool)
        for start in range(0, len(candidates), 65536):
            finite[start : start + 65536] = np.isfinite(
                raw[np.ix_(candidates[start : start + 65536], columns)]
            ).all(axis=1)
        event_ids = candidates[finite]
        if len(event_ids) < request.min_events:
            raise ValueError(
                f"{control.name}: {len(event_ids)} finite cleanup events; need {request.min_events}"
            )
        if not finite.all():
            warnings.append(
                f"{control.name}: {int((~finite).sum())} nonfinite fluorescence events excluded"
            )
        finite_count = len(event_ids)
        if request.max_events is not None and len(event_ids) > request.max_events:
            indices = np.linspace(0, len(event_ids) - 1, request.max_events, dtype=int)
            event_ids = event_ids[indices]
            warnings.append(
                f"{control.name}: regression uses {len(event_ids)} uniformly spaced event IDs "
                f"of {finite_count} finite events"
            )
        required_values = sum(v.size for v in data) + len(event_ids) * len(columns)
        if required_values > MAX_REGRESSION_VALUES:
            raise ValueError(
                "Selected controls exceed the regression memory limit. "
                "Set an explicit event limit or choose smaller cleanup populations."
            )
        selected = np.array(raw[np.ix_(event_ids, columns)], dtype=float)
        saturation = []
        for column, name in enumerate(request.detectors):
            limit = next(c.range for c in sample.acquisition_channels if c.name == name)
            fraction = float(np.mean(selected[:, column] >= limit))
            if fraction >= 0.01:
                saturation.append(name)
                warnings.append(
                    f"{control.name}: {100 * fraction:.1f}% at or above the acquisition "
                    f"range in {name}"
                )
        data.append(selected)
        selected_ids.append(event_ids)
        diagnostics.append(
            {
                "name": control.name,
                "primary_detector": detector,
                "sample_id": sample.id,
                "sample_name": sample.name,
                "gate_id": control.gate_id,
                "parent_count": parent_count,
                "cleanup_count": cleanup_count,
                "finite_count": finite_count,
                "used_count": len(selected),
                "cleanup": cleanup,
                "saturated_detectors": saturation,
                "autofluorescence": control.name in af_sources
                if request.kind == "spectral"
                else detector == request.af_detector,
            }
        )
    if request.kind == "spectral":
        from .spectral_autospill import calculate_prepared

        return calculate_prepared(
            workspace,
            request,
            data,
            diagnostics,
            warnings,
            selected_ids,
            snapshot,
            identifier,
            progress,
        )
    progress("Calculating initial robust spillover", 0.3)
    initial, _, initial_fits = regression_matrix(data, request)
    matrix, refined = refine(data, initial, request, progress)
    condition = validate_matrix(
        Compensation(name=request.name, detectors=request.detectors, matrix=matrix.tolist())
    )
    if not refined["converged"]:
        warnings.append(
            f"Refinement did not reach the requested residual tolerance ({request.tolerance:g}); "
            f"stopped at {refined['final_max_error']:.3g} "
            f"with {refined['stop_reason'].replace('_', ' ')}"
        )
    if condition > 100:
        warnings.append(f"Matrix condition number {condition:.3g}: detector noise may be amplified")
    for i, row in enumerate(initial_fits):
        for j, fit in enumerate(row):
            if fit.get("retained_secondary_ties"):
                warnings.append(
                    f"Initial {request.detectors[i]} → {request.detectors[j]}: tied secondary "
                    "trim boundaries retained to preserve the regression population"
                )
            if fit["fallback_ols"]:
                warnings.append(
                    f"Initial {request.detectors[i]} → {request.detectors[j]}: "
                    "robust regression did not converge; OLS fallback used"
                )
    for i, j in refined["fallback_pairs"]:
        warnings.append(
            f"Refinement {request.detectors[i]} → {request.detectors[j]}: "
            "OLS fallback used in at least one iteration"
        )
    if request.af_detector:
        warnings.append(
            "Autofluorescence subtraction assumes the unstained reference, all single stains "
            "and experimental cells share the same autofluorescence spectrum; "
            "inspect heterogeneous cell types separately. Correlated staining and "
            "autofluorescence can bias coefficients even when residual slopes are small"
        )
        if request.trim_fraction:
            warnings.append(
                "Tail trimming can bias autofluorescence coefficients even for independent "
                "sources; inspect control distributions and compare with trimming disabled"
            )
    for detector in request.detectors:
        spec = request.detector_transforms.get(detector, request.biex)
        if not 0.5 <= math.log10(-spec.width) <= 3:
            warnings.append(
                f"{detector}: native biex compatibility resets a width outside 0.5–3 "
                "decades to 0.5 decades"
            )
    inverse = np.linalg.inv(matrix)
    for i, (values, control) in enumerate(zip(data, diagnostics, strict=True)):
        secondary = int(np.argmax(np.abs(np.where(np.arange(len(matrix)) == i, 0, matrix[i]))))
        indices = np.linspace(0, len(values) - 1, min(300, len(values)), dtype=int)
        all_compensated = values @ inverse
        compensated = all_compensated[indices]
        control["biex_extrapolated_counts"] = {}
        for j, detector in enumerate(request.detectors):
            spec = request.detector_transforms.get(detector, request.biex)
            forward = biex_functions(
                spec.negative, spec.width, spec.positive, spec.top, request.biex_length
            )[0]
            clipped = int(
                (
                    (all_compensated[:, j] < forward.x.min())
                    | (all_compensated[:, j] > forward.x.max())
                ).sum()
            )
            control["biex_extrapolated_counts"][detector] = clipped
            if clipped >= 0.01 * len(values):
                warnings.append(
                    f"{control['name']}: {100 * clipped / len(values):.1f}% "
                    f"outside the biex refinement lookup range in {detector}; "
                    "linear extrapolation used, inspect its scale"
                )
        if any(f.get("retained_secondary_ties") for f in refined["regressions"][i]):
            warnings.append(
                f"{control['name']}: tied secondary trim boundaries retained "
                "during the final refinement"
            )
        control["preview"] = {
            "x": request.detectors[i],
            "y": request.detectors[secondary],
            "raw": values[indices][:, [i, secondary]].tolist(),
            "compensated": compensated[:, [i, secondary]].tolist(),
            "event_ids": selected_ids[i][indices].tolist(),
        }
        control["initial_signature"] = initial[i].tolist()
        control["signature"] = matrix[i].tolist()
    details = dict(
        refined,
        controls=diagnostics,
        initial_matrix=initial.tolist(),
        initial_regressions=initial_fits,
        condition_number=condition,
        rank=len(matrix),
        output_units="Primary-detector equivalent acquired intensity",
    )
    identifier = identifier or new_id()
    compensation = Compensation(
        id=identifier,
        name=request.name,
        detectors=request.detectors,
        matrix=matrix.tolist(),
        source="AutoSpill robust iterative regression",
    )
    created_at = now()
    compensation.provenance = {
        "kind": "autospill_calculation",
        "method": "autospill",
        "version": METHOD_VERSION,
        "software": {
            "cytoforge": "0.1.0",
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
        },
        "workspace_id": workspace.id,
        "source_revision": workspace.revision,
        "created_at": created_at,
        "request": request.model_dump(),
        "input_hash": input_hash(workspace, request),
        "input_snapshot": snapshot,
        "diagnostics": details,
        "warnings": warnings,
        "calculated_definition": compensation.model_dump(exclude={"provenance"}),
    }
    return AutoSpillResult(
        id=identifier,
        request=request,
        created_at=created_at,
        input_hash=input_hash(workspace, request),
        input_snapshot=snapshot,
        compensation=compensation,
        diagnostics=details,
        warnings=warnings,
    )


def saved_result(matrix: Compensation) -> AutoSpillResult:
    provenance = matrix.provenance
    if provenance.get("kind") != "autospill_calculation":
        raise ValueError("This matrix has no AutoSpill calculation")
    original = Compensation.model_validate(provenance["calculated_definition"])
    original.provenance = provenance
    return AutoSpillResult(
        id=matrix.id,
        request=AutoSpillRequest.model_validate(provenance["request"]),
        created_at=provenance.get("created_at", ""),
        input_hash=provenance["input_hash"],
        input_snapshot=provenance["input_snapshot"],
        compensation=original,
        diagnostics=provenance["diagnostics"],
        warnings=provenance["warnings"],
    )


def save_control_populations(workspace: Workspace, result: AutoSpillResult):
    """Save polygons and raw copies of their parents; preserve parent/subset counts.

    Explicit uncompensated dimensions keep these populations stable when a new
    matrix is assigned, including parent fluorescence gates and Boolean operands.
    """
    append, raw_parent, created = acquired_gate_copies(
        workspace,
        result.request.name,
        dict(autospill_id=result.id, input_hash=result.input_hash),
    )
    populations = []
    for control in result.diagnostics["controls"]:
        cleanup = control["cleanup"]
        parent = raw_parent(control["gate_id"])
        if cleanup is None:
            populations.append(
                dict(
                    output=control.get("output_name", control["primary_detector"]),
                    sample_id=control["sample_id"],
                    gate_id=parent,
                )
            )
            continue
        gate = Gate(
            sample_id=control["sample_id"],
            name="Cleanup",
            kind="polygon",
            parent_id=parent,
            vertices=cleanup["vertices"],
            x=cleanup["x"],
            y=cleanup["y"],
            dimensions=[
                GateDimension(
                    channel=cleanup[axis],
                    compensation_ref="uncompensated",
                    transform=Transform(kind="linear"),
                )
                for axis in ("x", "y")
            ],
        )
        gate.provenance.update(
            control_detector=control["primary_detector"], cleanup_count=control["cleanup_count"]
        )
        append(gate, f"{result.request.name[:64]} · {control['name'][:64]} · Cleanup")
        populations.append(
            dict(
                output=control.get("output_name", control["primary_detector"]),
                sample_id=control["sample_id"],
                gate_id=gate.id,
            )
        )
    return created, populations


def save_cleanup_gates(workspace: Workspace, result: AutoSpillResult):
    return save_control_populations(workspace, result)[0]


def preview(workspace, result, engine, primary, secondary):
    if is_stale(workspace, result):
        raise ValueError("Acquired controls or cleanup gates changed. Recalculate to preview.")
    request = result.request
    verify_qc_data(workspace, request, engine.store)
    if request.kind == "spectral":
        from .spectral_autospill import preview as spectral_preview

        return spectral_preview(workspace, result, engine, primary, secondary)
    if (
        primary not in request.detectors
        or secondary not in request.detectors
        or primary == secondary
    ):
        raise ValueError("Choose distinct primary and secondary calculation detectors")
    i, j = request.detectors.index(primary), request.detectors.index(secondary)
    control = result.diagnostics["controls"][i]
    sample = engine.sample(workspace, control["sample_id"])
    with engine.store.data_path(workspace.id, sample.id).open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != sample.sha256:
        raise ValueError("Acquired control data failed its integrity check")
    event_ids = np.asarray(control["preview"]["event_ids"], dtype=int)
    if np.any(event_ids < 0) or np.any(event_ids >= sample.event_count):
        raise ValueError("Preview event identity does not match the control")
    acquired = [c.name for c in sample.acquisition_channels]
    raw = engine.raw(workspace, sample)[event_ids][
        :, [acquired.index(n) for n in request.detectors]
    ]
    if not np.isfinite(raw).all():
        raise ValueError("Preview acquired events are no longer finite")
    compensated = raw @ np.linalg.inv(np.asarray(result.compensation.matrix))
    return {
        "x": primary,
        "y": secondary,
        "raw": raw[:, [i, j]].tolist(),
        "compensated": compensated[:, [i, j]].tolist(),
        "event_ids": event_ids.tolist(),
    }


def run_autospill(job_dir: str):
    directory, stop, store = Path(job_dir), threading.Event(), None

    def progress(stage, fraction):
        atomic_json(directory / "progress.json", {"stage": stage, "progress": fraction})

    try:
        payload = json.loads((directory / "input.json").read_text())
        threading.Thread(
            target=watch_parent, args=(payload["parent_pid"], stop), daemon=True
        ).start()
        workspace = Workspace.model_validate(payload["workspace"])
        request = AutoSpillRequest.model_validate(payload["request"])
        store = Store(Path(payload["data_dir"]))
        for identifier in sorted({c.sample_id for c in request.controls}):
            sample = next(s for s in workspace.samples if s.id == identifier)
            progress(f"Verifying acquired events: {sample.name}", 0.01)
            with store.data_path(workspace.id, identifier).open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            if digest != sample.sha256:
                raise ValueError("Acquired control data failed its integrity check")
        result = calculate(workspace, request, Engine(store), payload["id"], progress)
        if result.input_hash != payload["input_hash"]:
            raise ValueError("Control inputs do not match their scientific fingerprint")
        atomic_json(directory / "result.json", result.model_dump())
        progress("Ready to review compensation and convergence", 1)
    except Exception as exc:
        atomic_json(directory / "error.json", {"error": f"{type(exc).__name__}: {exc}"})
    finally:
        stop.set()
        if store:
            store.close()
