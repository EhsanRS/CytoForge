from __future__ import annotations

import hashlib
import io
import math
import threading
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path

import flowio
import numpy as np
from flowutils import transforms
from scipy import stats

from .biex import biex_functions
from .formulas import evaluate
from .graph_views import cdf_values, probability_view, resolved_options, sampled_points, smooth_grid
from .models import Channel, Compensation, GateDimension, Sample, Transform, Workspace
from .store import Store


def transform(values: np.ndarray, spec: Transform, inverse: bool = False) -> np.ndarray:
    if spec.bound_min is not None or spec.bound_max is not None:
        base = spec.model_copy(update={"bound_min": None, "bound_max": None})
        result = transform(values, base, inverse)
        if not inverse:
            result = np.where(np.isposinf(values), np.inf, result)
            if spec.kind not in {"log", "gml_log"}:
                result = np.where(np.isneginf(values), -np.inf, result)
            result = np.clip(
                result,
                spec.bound_min if spec.bound_min is not None else -np.inf,
                spec.bound_max if spec.bound_max is not None else np.inf,
            )
        return result
    values = np.asarray(values, dtype=np.float64)
    if not values.size or spec.kind == "linear":
        return values
    if not np.all(np.isfinite(values)):
        output = np.full(values.shape, np.nan)
        finite = np.isfinite(values)
        output[finite] = transform(values[finite], spec, inverse)
        return output
    if spec.kind == "gml_linear":
        return (
            values * (spec.t + spec.a) - spec.a
            if inverse
            else (values + spec.a) / (spec.t + spec.a)
        )
    if spec.kind == "gml_log":
        func = transforms.log_inverse if inverse else transforms.log
        with np.errstate(divide="ignore", invalid="ignore"):
            return func(values, None, t=spec.t, m=spec.m)
    if spec.kind == "gml_asinh":
        func = transforms.asinh_inverse if inverse else transforms.asinh
        return func(values, None, t=spec.t, m=spec.m, a=spec.a)
    if spec.kind == "wsp_log":
        return (
            spec.offset * np.power(10.0, values * spec.m)
            if inverse
            else (np.log10(np.maximum(values, spec.offset)) - np.log10(spec.offset)) / spec.m
        )
    if spec.kind == "wsp_biex":
        forward, backward = biex_functions(spec.negative, spec.width, spec.positive, spec.top)
        return backward(values) if inverse else forward(values)
    if spec.kind == "asinh":
        return np.sinh(values) * spec.cofactor if inverse else np.arcsinh(values / spec.cofactor)
    if spec.kind == "log":
        if inverse:
            return np.power(10.0, values)
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(values > 0, np.log10(values), np.nan)
    func = getattr(transforms, spec.kind + ("_inverse" if inverse else ""))
    return func(values, None, t=spec.t, m=spec.m, w=spec.w, a=spec.a)


def validate_matrix(matrix: Compensation):
    values = np.asarray(matrix.matrix, dtype=np.float64)
    if matrix.weights:
        values = values * np.sqrt(matrix.weights)
    if not np.all(np.isfinite(values)):
        raise ValueError("Compensation values must be finite")
    if values.shape[0] > values.shape[1] or np.linalg.matrix_rank(values) < values.shape[0]:
        raise ValueError("Compensation matrix is rank deficient")
    condition = float(np.linalg.cond(values))
    if condition > 1e8:
        raise ValueError(f"Matrix is numerically unstable (condition number {condition:.3g})")
    return condition


def compensate(events: np.ndarray, matrix: Compensation) -> np.ndarray:
    validate_matrix(matrix)
    values = np.asarray(matrix.matrix, dtype=np.float64)
    if matrix.kind == "spillover":
        # Row-source convention: measured = true @ spillover.
        return np.linalg.solve(values.T, events.T).T
    measured = np.asarray(events, dtype=np.float64)
    if matrix.background:
        measured = measured - np.asarray(matrix.background)
    # Weights are inverse detector variances, not their square roots.
    weights = np.sqrt(matrix.weights) if matrix.weights else np.ones(len(matrix.detectors))
    finite = np.isfinite(measured).all(axis=1)
    result = np.full((len(measured), len(matrix.outputs)), np.nan)
    result[finite] = np.linalg.lstsq(
        values.T * weights[:, None], (measured[finite] * weights).T, rcond=None
    )[0].T
    return result


def polygon_mask(x: np.ndarray, y: np.ndarray, vertices: list[tuple[float, float]]) -> np.ndarray:
    """Exact even-odd membership, indexing dense outlines by their Y intervals."""
    if len(vertices) < 64 or x.size < 1024 or x.dtype != np.float64 or y.dtype != np.float64:
        return _polygon_mask_vectorized(x, y, vertices)
    points = np.asarray(vertices, dtype=float)
    with np.errstate(over="ignore", invalid="ignore"):
        differences = np.roll(points, -1, axis=0) - points
    if not np.isfinite(points).all() or not np.isfinite(differences).all():
        return _polygon_mask_vectorized(x, y, vertices)
    padding = 1e-10 * max(1.0, float(np.abs(differences).max()))
    left, right = float(points[:, 0].min()) - padding, float(points[:, 0].max()) + padding
    bottom, top = float(points[:, 1].min()) - padding, float(points[:, 1].max()) + padding
    span = top - bottom
    if not np.isfinite([left, right, bottom, top, span]).all() or span <= 0:
        return _polygon_mask_vectorized(x, y, vertices)
    candidates = np.flatnonzero(
        np.isfinite(x) & np.isfinite(y) & (x >= left) & (x <= right) & (y >= bottom) & (y <= top)
    )
    result = np.zeros(x.shape, dtype=bool)
    if not len(candidates):
        return result
    cx, cy = x.ravel()[candidates], y.ravel()[candidates]
    bins = min(256, max(16, len(vertices) // 8))
    keys = np.clip(((cy - bottom) / span * bins).astype(np.int64), 0, bins - 1)
    order = np.argsort(keys, kind="stable")
    counts = np.bincount(keys, minlength=bins)
    edges: list[list[int]] = [[] for _ in range(bins)]
    for i, (_, ay) in enumerate(vertices):
        _, by = vertices[(i + 1) % len(vertices)]
        dx, dy = differences[i]
        tolerance = 1e-10 * max(1.0, abs(float(dx)), abs(float(dy)))
        # One neighboring interval on each side conservatively covers rounding
        # at bucket boundaries; the exact original predicate still decides.
        lo = max(0, int((min(ay, by) - tolerance - bottom) / span * bins) - 1)
        hi = min(bins - 1, int((max(ay, by) + tolerance - bottom) / span * bins) + 1)
        for bucket in range(lo, hi + 1):
            edges[bucket].append(i)
    if sum(map(len, edges)) > bins * len(vertices) // 4:
        return _polygon_mask_vectorized(x, y, vertices)
    offset = 0
    for bucket, count in enumerate(counts):
        if not count:
            continue
        selected = order[offset : offset + count]
        bx, by = cx[selected], cy[selected]
        inside, boundary = np.zeros(count, dtype=bool), np.zeros(count, dtype=bool)
        for edge in edges[bucket]:
            ax, ay = vertices[edge]
            ex, ey = vertices[(edge + 1) % len(vertices)]
            dx, dy = ex - ax, ey - ay
            cross = (bx - ax) * dy - (by - ay) * dx
            tolerance = 1e-10 * max(1.0, abs(dx), abs(dy))
            boundary |= (
                (np.abs(cross) <= tolerance)
                & (bx >= min(ax, ex) - tolerance)
                & (bx <= max(ax, ex) + tolerance)
                & (by >= min(ay, ey) - tolerance)
                & (by <= max(ay, ey) + tolerance)
            )
            if dy:
                inside ^= ((ay > by) != (ey > by)) & (bx < ax + (by - ay) * dx / dy)
        result.ravel()[candidates[selected]] = inside | boundary
        offset += count
    return result


def _polygon_mask_vectorized(
    x: np.ndarray, y: np.ndarray, vertices: list[tuple[float, float]]
) -> np.ndarray:
    """Vectorized even-odd polygon test with included boundaries."""
    inside = np.zeros(x.shape, dtype=bool)
    boundary = np.zeros(x.shape, dtype=bool)
    for i, (ax, ay) in enumerate(vertices):
        bx, by = vertices[(i + 1) % len(vertices)]
        dx, dy = bx - ax, by - ay
        cross = (x - ax) * dy - (y - ay) * dx
        tolerance = 1e-10 * max(1.0, abs(dx), abs(dy))
        boundary |= (
            (np.abs(cross) <= tolerance)
            & (x >= min(ax, bx) - tolerance)
            & (x <= max(ax, bx) + tolerance)
            & (y >= min(ay, by) - tolerance)
            & (y <= max(ay, by) + tolerance)
        )
        if dy:
            crossing = ((ay > y) != (by > y)) & (x < ax + (y - ay) * dx / dy)
            inside ^= crossing
    return (inside | boundary) & np.isfinite(x) & np.isfinite(y)


def shape_mask(gate, columns):
    """Positive geometric membership, before complement and parent intersection."""
    if gate.kind == "curly":
        from .curly import classify_gate

        return classify_gate(gate, columns) == gate.partition.member
    if gate.kind == "spider":
        from .spider import labels

        return labels(*columns, gate.spider) == gate.partition.member
    if gate.dimensions:
        if gate.kind == "hyperrectangle":
            result = np.ones(len(columns[0]), dtype=bool)
            for dimension, values in zip(gate.dimensions, columns, strict=True):
                if dimension.minimum is not None:
                    result &= values >= dimension.minimum
                if dimension.maximum is not None:
                    result &= values < dimension.maximum
        elif gate.kind == "polygon":
            result = polygon_mask(*columns, gate.vertices)
        else:
            values = np.column_stack(columns) - np.asarray(gate.coordinates)
            inv_cov = np.linalg.inv(np.asarray(gate.covariance))
            distance = np.einsum("ni,ij,nj->n", values, inv_cov, values, optimize=True)
            result = distance <= gate.distance_square
    else:
        x = columns[0]
        y = columns[1] if len(columns) > 1 else None
        if gate.kind == "range":
            result = (x >= gate.bounds[0]) & (x < gate.bounds[1])
        elif gate.kind == "rectangle":
            lo, hi, bottom, top = gate.bounds
            result = (x >= lo) & (x < hi) & (y >= bottom) & (y < top)
        elif gate.kind == "polygon":
            result = polygon_mask(x, y, gate.vertices)
        elif gate.kind == "ellipse":
            dx, dy = x - gate.center[0], y - gate.center[1]
            cosine, sine = np.cos(gate.angle), np.sin(gate.angle)
            result = ((dx * cosine + dy * sine) / gate.radii[0]) ** 2 + (
                (-dx * sine + dy * cosine) / gate.radii[1]
            ) ** 2 <= 1
        else:
            right, upper = x >= gate.bounds[0], y >= gate.bounds[1]
            result = {
                1: ~right & upper,
                2: right & upper,
                3: right & ~upper,
                4: ~right & ~upper,
            }[gate.quadrant]
    if gate.kind == "polygon":
        for ring in gate.holes:
            result &= ~polygon_mask(columns[0], columns[1], ring)
    for values in columns:
        result &= np.isfinite(values)
    return result


class ArrayCache:
    def __init__(self, max_bytes: int = 256 * 1024 * 1024):
        self.max_bytes = max_bytes
        self.items: OrderedDict[tuple, np.ndarray] = OrderedDict()
        self.bytes = 0
        self.lock = threading.RLock()

    def get(self, key: tuple):
        with self.lock:
            value = self.items.get(key)
            if value is not None:
                self.items.move_to_end(key)
            return value

    def put(self, key: tuple, value: np.ndarray):
        if value.nbytes > self.max_bytes:
            return value
        value.flags.writeable = False
        with self.lock:
            old = self.items.pop(key, None)
            if old is not None:
                self.bytes -= old.nbytes
            while self.items and self.bytes + value.nbytes > self.max_bytes:
                _, old = self.items.popitem(last=False)
                self.bytes -= old.nbytes
            self.items[key] = value
            self.bytes += value.nbytes
        return value


class Engine:
    def __init__(self, store: Store, cache_bytes: int = 256 * 1024 * 1024):
        self.store = store
        self.cache = ArrayCache(cache_bytes)
        # Small resolution records have a separate hard entry limit; no event
        # arrays or gate models are retained in this cache.
        self.magnetic_cache = OrderedDict()
        self.magnetic_lock = threading.RLock()

    def sample(self, workspace: Workspace, sample_id: str) -> Sample:
        for sample in workspace.samples:
            if sample.id == sample_id:
                return sample
        raise KeyError("Sample not found")

    def raw(self, workspace: Workspace, sample: Sample) -> np.ndarray:
        path = self.store.data_path(workspace.id, sample.id)
        if not path.exists():
            raise ValueError(
                f"Event data for {sample.name} is missing. Restore the project archive."
            )
        events = np.load(path, mmap_mode="r", allow_pickle=False)
        if events.shape != (
            sample.event_count,
            len(sample.acquisition_channels),
        ):
            raise ValueError(f"Event data for {sample.name} does not match its metadata")
        return events

    def column(
        self,
        workspace: Workspace,
        sample: Sample,
        name: str,
        spec: Transform | None = None,
        compensated: bool = True,
        compensation_ref: str = "sample",
    ) -> np.ndarray:
        names = [c.name for c in sample.channels]
        if name not in names:
            raise ValueError(f"Channel {name} is not available in {sample.name}")
        if name in sample.aliases:
            return self.column(
                workspace,
                sample,
                sample.aliases[name],
                spec,
                compensated=compensated,
                compensation_ref=compensation_ref,
            )
        key = (
            workspace.id,
            workspace.revision,
            sample.id,
            "column",
            name,
            spec.model_dump_json() if spec else "raw",
            compensated,
            compensation_ref,
        )
        result = self.cache.get(key)
        if result is not None:
            return result
        derived = next((d for d in sample.derived_parameters if d.name == name), None)
        computed = next((p for p in sample.computed_parameters if p.name == name), None)
        if computed:
            definition = next(
                a
                for a in [
                    *workspace.analyses,
                    *workspace.cell_cycle_results,
                    *workspace.proliferation_results,
                    *workspace.kinetics_results,
                ]
                if a.id == computed.analysis_id
            )
            path = self.store.analysis_path(workspace.id, computed.analysis_id, sample.id)
            if not path.exists():
                raise ValueError(
                    "Analysis data is missing. Restore the project archive or rerun it."
                )
            if definition.request.algorithm == "kinetics":
                from .kinetics import load_data

                data = next(d for d in definition.data if d.sample_id == sample.id)
                data_key = (workspace.id, "kinetics", definition.id, sample.id, data.sha256)
                values = self.cache.get(data_key)
                if values is None:
                    values = self.cache.put(
                        data_key, load_data(self.store, workspace.id, definition, data)
                    )
            else:
                values = np.load(path, mmap_mode="r", allow_pickle=False)
            if values.shape != (sample.event_count, len(definition.columns)):
                raise ValueError("Analysis data does not match its event IDs")
            result = values[:, computed.index]
        elif derived:
            result = evaluate(
                derived.expression,
                lambda n: self.column(
                    workspace, sample, n, compensated=compensated, compensation_ref=compensation_ref
                ),
                sample.event_count,
            )
        elif name in sample.unmixed_parameters:
            # Keep inactive output definitions for existing gates/formulas, but never
            # substitute acquired detector values when their matrix is unassigned.
            result = np.full(sample.event_count, np.nan)
        else:
            result = self.raw(workspace, sample)[:, names.index(name)]
        comp_id = sample.compensation_id if compensation_ref == "sample" else compensation_ref
        if comp_id and comp_id != "uncompensated" and compensated and not derived and not computed:
            if comp_id == "FCS":
                comp = embedded_matrix(sample)
            else:
                comp = next((c for c in workspace.compensations if c.id == comp_id), None)
                if comp is None:
                    raise ValueError("Gate compensation matrix is missing")
            if comp is None:
                if compensation_ref == "FCS":
                    return transform(result, spec) if spec else result
                raise ValueError("Compensation matrix is missing")
            if name in comp.outputs:
                matrix_key = (workspace.id, workspace.revision, sample.id, "compensated", comp_id)
                corrected = self.cache.get(matrix_key)
                if corrected is None:
                    raw = self.raw(workspace, sample)[:, [names.index(n) for n in comp.detectors]]
                    corrected = self.cache.put(matrix_key, compensate(raw, comp))
                result = corrected[:, comp.outputs.index(name)]
        if spec:
            result = transform(result, spec)
        # Views of memory-mapped raw files need no RAM cache entry.
        if (
            derived
            or name in sample.unmixed_parameters
            or (
                spec
                and (
                    spec.kind != "linear"
                    or spec.bound_min is not None
                    or spec.bound_max is not None
                )
            )
        ):
            return self.cache.put(key, result)
        return result

    def dimension(
        self, workspace: Workspace, sample: Sample, dimension: GateDimension, compensated=True
    ):
        if not dimension.ratio_channels:
            return self.column(
                workspace,
                sample,
                dimension.channel,
                dimension.transform,
                compensated=compensated,
                compensation_ref=dimension.compensation_ref,
            )

        def column(name):
            return self.column(
                workspace,
                sample,
                name,
                compensated=compensated,
                compensation_ref=dimension.compensation_ref,
            )

        if dimension.ratio_channels:
            numerator, denominator = (column(n) for n in dimension.ratio_channels)
            with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
                values = (
                    dimension.ratio_a
                    * (numerator - dimension.ratio_b)
                    / (denominator - dimension.ratio_c)
                )
            if dimension.ratio_bound_min is not None or dimension.ratio_bound_max is not None:
                values = np.clip(
                    values,
                    dimension.ratio_bound_min if dimension.ratio_bound_min is not None else -np.inf,
                    dimension.ratio_bound_max if dimension.ratio_bound_max is not None else np.inf,
                )
        else:
            values = column(dimension.channel)
        return transform(values, dimension.transform)

    def gate_columns(self, workspace, sample, gate, compensated=True):
        if gate.kind == "curly":
            return [
                self.dimension(
                    workspace, sample, d.model_copy(update={"transform": Transform()}), compensated
                )
                for d in gate.dimensions
            ]
        if gate.dimensions:
            return [self.dimension(workspace, sample, d, compensated) for d in gate.dimensions]
        return [
            self.column(workspace, sample, gate.x, gate.x_transform, compensated),
            *(
                [self.column(workspace, sample, gate.y, gate.y_transform, compensated)]
                if gate.y
                else []
            ),
        ]

    def resolve_gate(self, workspace, sample, gate, compensated=True):
        if gate.magnetic is None:
            return gate, None
        from .magnetic import resolve, translated

        fingerprint = hashlib.sha256(gate.model_dump_json().encode()).digest()
        key = (workspace.id, workspace.revision, sample.id, gate.id, compensated, fingerprint)
        with self.magnetic_lock:
            cached = self.magnetic_cache.get(key)
            if cached is not None:
                self.magnetic_cache.move_to_end(key)
                return translated(gate, np.asarray(cached["shift"])), deepcopy(cached)
        parent = self.mask(workspace, sample, gate.parent_id, compensated)
        moved, report = resolve(
            gate, self.gate_columns(workspace, sample, gate, compensated), parent
        )
        with self.magnetic_lock:
            self.magnetic_cache[key] = report
            self.magnetic_cache.move_to_end(key)
            while len(self.magnetic_cache) > 256:
                self.magnetic_cache.popitem(last=False)
        return moved, deepcopy(report)

    def mask(
        self, workspace: Workspace, sample: Sample, gate_id: str | None, compensated: bool = True
    ) -> np.ndarray:
        if not gate_id:
            return np.ones(sample.event_count, dtype=bool)
        gate = next((g for g in workspace.gates if g.id == gate_id), None)
        if not gate or gate.sample_id != sample.id:
            raise ValueError("Selected gate does not belong to this sample")
        from .population_snapshot import verify_dependencies

        verify_dependencies(self, workspace, sample, gate_id)
        if gate.kind == "quality":
            from .quality import is_captured_gate

            qc_result = next(
                (q for q in workspace.quality_results if q.id == gate.quality_id), None
            )
            if qc_result is None or qc_result.request.sample_id != sample.id:
                raise ValueError("QC population is missing or belongs to another sample")
            is_captured_gate(gate, qc_result)
        key = (workspace.id, workspace.revision, sample.id, "mask", gate_id, compensated)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        parent = self.mask(workspace, sample, gate.parent_id, compensated)
        gate, _ = self.resolve_gate(workspace, sample, gate, compensated)
        if gate.kind == "membership":
            from .population_snapshot import load

            result = load(self, workspace, sample, gate.membership)
        elif gate.kind == "quality":
            from .quality import load_data, selection

            result = next(q for q in workspace.quality_results if q.id == gate.quality_id)
            data_key = (workspace.id, "quality", result.id, result.data.sha256)
            values = self.cache.get(data_key)
            if values is None:
                values = self.cache.put(data_key, load_data(self.store, workspace.id, result))
            result = selection(
                values, gate.quality_excluded_bins, gate.quality_exclusions, gate.quality_keep
            )
        elif gate.kind == "boolean":
            operands = [self.mask(workspace, sample, value, compensated) for value in gate.operands]
            if gate.operand_complements:
                operands = [
                    ~v if c else v for v, c in zip(operands, gate.operand_complements, strict=True)
                ]
            if gate.operation == "not":
                result = ~operands[0]
            else:
                fn = {"and": np.logical_and, "or": np.logical_or, "xor": np.logical_xor}[
                    gate.operation
                ]
                result = fn.reduce(operands)
        elif gate.kind == "container":
            result = np.ones(sample.event_count, dtype=bool)
        elif gate.kind in {"spider", "curly"}:
            from .spider import labels

            family_key = (
                workspace.id,
                workspace.revision,
                sample.id,
                gate.kind,
                gate.partition.id,
                compensated,
            )
            classification = self.cache.get(family_key)
            if classification is None:
                if gate.kind == "curly":
                    from .curly import classify_gate

                    values = classify_gate(
                        gate, self.gate_columns(workspace, sample, gate, compensated)
                    )
                else:
                    values = labels(
                        *self.gate_columns(workspace, sample, gate, compensated), gate.spider
                    )
                classification = self.cache.put(
                    family_key,
                    values,
                )
            result = classification == gate.partition.member
        else:
            result = shape_mask(gate, self.gate_columns(workspace, sample, gate, compensated))
        if gate.complement:
            result = ~result
        return self.cache.put(key, result & parent)

    def gate_counts(self, workspace: Workspace, sample: Sample) -> list[dict]:
        rows = []
        for gate in workspace.gates:
            if gate.sample_id != sample.id:
                continue
            count = int(np.count_nonzero(self.mask(workspace, sample, gate.id)))
            parent = int(np.count_nonzero(self.mask(workspace, sample, gate.parent_id)))
            rows.append(
                {
                    "id": gate.id,
                    "count": count,
                    "percent_parent": 100 * count / parent if parent else None,
                    "percent_total": 100 * count / sample.event_count
                    if sample.event_count
                    else None,
                }
            )
        return rows

    def summary(
        self,
        workspace: Workspace,
        sample: Sample,
        gate_id: str | None,
        channel: str,
        compensated: bool = True,
        compensation_ref: str = "sample",
    ):
        mask = self.mask(workspace, sample, gate_id)
        raw = self.column(
            workspace, sample, channel, compensated=compensated, compensation_ref=compensation_ref
        )[mask]
        values = raw[np.isfinite(raw)]
        result = {"count": int(mask.sum()), "finite_count": len(values), "channel": channel}
        if not len(values):
            return result | dict.fromkeys(
                [
                    "mean",
                    "median",
                    "std",
                    "cv",
                    "robust_cv",
                    "geometric_mean",
                    "p5",
                    "p95",
                    "min",
                    "max",
                ],
                None,
            )
        scale = max(float(np.abs(values).max()), 1e-300)
        normalized = values / scale
        mean_unit = float(np.mean(normalized))
        median_unit = float(np.median(normalized))
        sd_unit = float(np.std(normalized, ddof=1)) if len(values) > 1 else None
        mad_unit = float(np.median(np.abs(normalized - median_unit)))
        with np.errstate(all="ignore"):
            statistics = {
                "mean": mean_unit * scale,
                "median": median_unit * scale,
                "std": sd_unit * scale if sd_unit is not None else None,
                "cv": 100 * sd_unit / abs(mean_unit) if mean_unit and sd_unit is not None else None,
                "robust_cv": 100 * 1.4826 * mad_unit / abs(median_unit) if median_unit else None,
                "geometric_mean": float(np.exp(np.mean(np.log(values))))
                if np.all(values > 0)
                else None,
                "p5": float(np.quantile(normalized, 0.05)) * scale,
                "p95": float(np.quantile(normalized, 0.95)) * scale,
                "min": float(values.min()),
                "max": float(values.max()),
            }
        return result | {
            key: value if value is None or math.isfinite(value) else None
            for key, value in statistics.items()
        }

    def plot(
        self,
        workspace: Workspace,
        sample: Sample,
        x_name: str,
        y_name: str | None,
        x_spec: Transform,
        y_spec: Transform,
        gate_id: str | None = None,
        bins: int = 160,
        bounds: list[float] | None = None,
        mode: str = "density",
        backgate_id: str | None = None,
        x_dimension: GateDimension | None = None,
        y_dimension: GateDimension | None = None,
        graph_options=None,
    ) -> dict:
        selected = self.mask(workspace, sample, gate_id)
        x = (
            self.dimension(workspace, sample, x_dimension)
            if x_dimension
            else self.column(workspace, sample, x_name, x_spec)
        )
        y = (
            (
                self.dimension(workspace, sample, y_dimension)
                if y_dimension
                else self.column(workspace, sample, y_name, y_spec)
            )
            if y_name
            else None
        )
        finite = np.isfinite(x) & (np.isfinite(y) if y is not None else True)
        valid = selected & finite
        options = resolved_options(graph_options, mode)

        def extent(values):
            finite_values = values[finite]
            if not len(finite_values):
                return [0.0, 1.0]
            scale = max(float(np.abs(finite_values).max()), 1e-300)
            lo, hi = np.quantile(
                finite_values / scale,
                [0, 1] if options["axis_extent"] == "full" else [0.001, 0.999],
            )
            pad = (hi - lo) * 0.04 if hi > lo else max(1 / scale, abs(lo) * 0.1)
            maximum = np.finfo(float).max
            with np.errstate(over="ignore"):
                return [
                    float(np.clip((lo - pad) * scale, -maximum, maximum)),
                    float(np.clip((hi + pad) * scale, -maximum, maximum)),
                ]

        base_x = extent(x)
        base_y = extent(y) if y is not None else None
        xlim = bounds[:2] if bounds else base_x
        ylim = bounds[2:] if bounds and y is not None else base_y
        if (
            len(xlim) != 2
            or not np.all(np.isfinite(xlim))
            or xlim[1] <= xlim[0]
            or (ylim and (len(ylim) != 2 or not np.all(np.isfinite(ylim)) or ylim[1] <= ylim[0]))
        ):
            raise ValueError("Plot bounds must be finite and increasing")
        visible = valid & (x >= xlim[0]) & (x <= xlim[1])
        if y is not None:
            visible &= (y >= ylim[0]) & (y <= ylim[1])
        count = int(selected.sum())
        result = {
            "mode": mode,
            "x": x_name,
            "y": y_name,
            "bins": bins,
            "count": count,
            "finite_count": int(valid.sum()),
            "visible_count": int(visible.sum()),
            "bounds": xlim + (ylim or []),
            "ticks_x": self.ticks(xlim, x_spec),
            "ticks_y": self.ticks(ylim, y_spec) if ylim else [],
            "graph_options": options,
        }
        if y is None:
            if unstable_domain(xlim, bins):
                counts, _ = np.histogram(axis_fraction(x[visible], xlim), bins=bins, range=(0, 1))
                edges = axis_positions(xlim, bins + 1)
            else:
                counts, edges = np.histogram(x[valid], bins=bins, range=xlim)
            result.update(counts=counts.tolist(), edges=edges.tolist(), max_count=int(counts.max()))
            if mode == "cdf":
                result.update(cdf_values(x[valid], edges))
        elif mode == "scatter":
            indices = np.flatnonzero(visible)
            result["points"] = sampled_points(x, y, indices, options["point_limit"])
            result["displayed_count"] = len(result["points"])
            result["point_sampling"] = (
                "uniform deterministic; seed 42" if len(result["points"]) < len(indices) else None
            )
        else:
            if unstable_domain(xlim, bins) or unstable_domain(ylim, bins):
                counts, _, _ = np.histogram2d(
                    axis_fraction(x[visible], xlim),
                    axis_fraction(y[visible], ylim),
                    bins=bins,
                    range=[(0, 1), (0, 1)],
                )
            else:
                counts, _, _ = np.histogram2d(x[valid], y[valid], bins=bins, range=[xlim, ylim])
            result.update(counts=counts.T.ravel().astype(int).tolist(), max_count=int(counts.max()))
            if mode in {"contour", "zebra"}:
                # Zoom inside the automatic domain keeps the probability estimate fixed.
                density_bounds = [
                    min(base_x[0], xlim[0]),
                    max(base_x[1], xlim[1]),
                    min(base_y[0], ylim[0]),
                    max(base_y[1], ylim[1]),
                ]
                result.update(
                    probability_view(
                        x[valid], y[valid], density_bounds, result["bounds"], bins, options
                    )
                )
            elif mode == "pseudocolor" or options["smooth"]:
                field = smooth_grid(counts.T, options)
                result.update(
                    density_field=field.ravel().tolist(),
                    density_max=float(field.max()),
                    density_bounds=result["bounds"],
                )
        if backgate_id:
            bg = self.mask(workspace, sample, backgate_id) & finite & selected
            indices = np.flatnonzero(bg & visible)
            backgate_visible = len(indices)
            if len(indices) > 6000:
                indices = np.random.default_rng(43).choice(indices, 6000, replace=False)
            result["backgate_points"] = (
                np.column_stack([x[indices], y[indices]]).tolist()
                if y is not None
                else [[float(x[i]), 0.0] for i in indices]
            )
            result["backgate_count"] = int(bg.sum())
            result["backgate_visible_count"] = backgate_visible
            result["backgate_displayed_count"] = len(indices)
            result["backgate_sampling"] = (
                "uniform deterministic within viewport; seed 43"
                if len(indices) < backgate_visible
                else None
            )
        return result

    def ticks(self, limits: list[float], spec: Transform) -> list[dict]:
        if spec.kind in {
            "logicle",
            "hyperlog",
            "asinh",
            "log",
            "gml_log",
            "gml_asinh",
            "wsp_log",
            "wsp_biex",
        }:
            raw = np.array([-10000, -1000, -100, 0, 100, 1000, 10000, 100000, 1000000], float)
            positions = transform(raw, spec)
            ticks = [
                (p, v) for p, v in zip(positions, raw, strict=True) if limits[0] <= p <= limits[1]
            ]
            if len(ticks) >= 2:
                return [{"value": float(p), "label": self.number(v)} for p, v in ticks]
        positions = axis_positions(limits, 5)
        raw = transform(positions, spec, inverse=True)
        return [
            {"value": float(p), "label": self.number(v)}
            for p, v in zip(positions, raw, strict=True)
        ]

    @staticmethod
    def number(value: float) -> str:
        if abs(value) >= 1e6:
            return f"{value / 1e6:.3g}M"
        if abs(value) >= 1e3:
            return f"{value / 1e3:.3g}k"
        return f"{value:.3g}"


def axis_positions(limits, count):
    fractions = np.linspace(0, 1, count)
    return (1 - fractions) * limits[0] + fractions * limits[1]


def unstable_domain(limits, bins):
    with np.errstate(all="ignore"):
        span = np.float64(limits[1]) - limits[0]
        edges = axis_positions(limits, bins + 1)
        return not np.isfinite(span) or span / bins == 0 or np.any(edges[1:] <= edges[:-1])


def axis_fraction(values, limits):
    scale = max(abs(limits[0]), abs(limits[1]))
    return (values / scale - limits[0] / scale) / (limits[1] / scale - limits[0] / scale)


def embedded_matrix(sample: Sample) -> Compensation | None:
    text = sample.metadata.get("spillover") or sample.metadata.get("spill")
    if not text:
        return None
    tokens = [v.strip() for v in text.split(",")]
    n = int(tokens[0])
    if not 1 <= n <= 512 or len(tokens) != 1 + n + n * n:
        raise ValueError("Embedded FCS spillover is malformed")
    matrix = Compensation(
        name="Embedded FCS spillover",
        detectors=tokens[1 : n + 1],
        matrix=np.asarray(tokens[n + 1 :], float).reshape(n, n).tolist(),
        source="FCS $SPILLOVER",
    )
    validate_matrix(matrix)
    if not set(matrix.detectors) <= {c.name for c in sample.acquisition_channels}:
        raise ValueError("Embedded FCS detector names do not match this sample")
    return matrix


def parse_fcs(path: Path, name: str) -> tuple[Sample, np.ndarray, Compensation | None, list[str]]:
    header = flowio.FlowData(str(path), only_text=True)
    if int(header.text.get("nextdata", "0")):
        raise ValueError("Multi-dataset FCS is not yet supported. Export individual datasets.")
    if header.event_count * header.channel_count > 150_000_000:
        raise ValueError("File exceeds the current 150-million-value import limit")
    data = flowio.FlowData(str(path))
    values = data.as_array(preprocess=True)
    channels = []
    for number, channel in sorted(data.channels.items()):
        channel_name = channel["pnn"]
        linear = channel_name.lower().startswith(("fsc", "ssc", "time"))
        # Store the upper range in the same preprocessed units as event arrays.
        upper = float(channel["pnr"])
        if data.time_index == number - 1:
            upper *= float(data.text.get("timestep", "1").strip() or "1")
        decades, log_zero = channel["pne"]
        if decades > 0:
            upper = 10 ** (decades * upper / channel["pnr"]) * log_zero
        if channel["png"] not in (0, 1):
            upper /= channel["png"]
        channels.append(
            Channel(
                name=channel_name,
                label=channel.get("pns", ""),
                range=upper,
                transform=Transform(kind="linear" if linear else "logicle"),
            )
        )
    sample = Sample(
        name=name,
        event_count=data.event_count,
        channels=channels,
        metadata={k: str(v) for k, v in data.text.items()},
        source="FCS",
    )
    warnings = []
    nonfinite = int(np.count_nonzero(~np.isfinite(values)))
    if nonfinite:
        warnings.append(
            f"{nonfinite:,} nonfinite values retained; plots and statistics omit these values"
        )
    comp = None
    spill = data.text.get("spillover") or data.text.get("spill")
    if spill:
        try:
            tokens = [v.strip() for v in spill.split(",")]
            n = int(tokens[0])
            if len(tokens) != 1 + n + n * n or n > 512 or n < 1:
                raise ValueError("Invalid spillover dimensions")
            comp = Compensation(
                name=f"Acquisition · {name}",
                detectors=tokens[1 : n + 1],
                matrix=np.array(tokens[n + 1 :], dtype=float).reshape(n, n).tolist(),
                source="FCS $SPILLOVER",
            )
            validate_matrix(comp)
            if not set(comp.detectors) <= {c.name for c in channels}:
                raise ValueError("Spillover detector names do not match the channel names")
            sample.compensation_id = comp.id
        except (ValueError, TypeError) as exc:
            comp = None
            warnings.append(f"Acquisition compensation was not applied: {exc}")
    return sample, values, comp, warnings


def save_events(path: Path, values: np.ndarray) -> str:
    return save_array(path, np.asarray(values, dtype=np.float64))


def save_array(path: Path, values: np.ndarray) -> str:
    temporary = path.with_suffix(".pending")
    try:
        with temporary.open("wb") as handle:
            np.save(handle, values, allow_pickle=False)
            handle.flush()
            import os

            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def export_fcs(events: np.ndarray, sample: Sample, label: str) -> bytes:
    output = io.BytesIO()
    flowio.create_fcs(
        output,
        events.ravel().tolist(),
        [c.name for c in sample.channels],
        [c.label for c in sample.channels],
        metadata_dict={"cytoforge_population": label, "cytoforge_source": sample.name},
    )
    return output.getvalue()


def compare_populations(a: np.ndarray, b: np.ndarray) -> dict:
    a, b = a[np.isfinite(a)], b[np.isfinite(b)]
    if not len(a) or not len(b):
        raise ValueError("Population comparison requires events in both populations")
    ks = stats.ks_2samp(a, b)
    mw = stats.mannwhitneyu(a, b, alternative="two-sided")
    return {
        "ks_statistic": float(ks.statistic),
        "ks_pvalue": float(ks.pvalue),
        "mann_whitney_u": float(mw.statistic),
        "mann_whitney_pvalue": float(mw.pvalue),
        "median_difference": float(np.median(a) - np.median(b)),
        "n_a": len(a),
        "n_b": len(b),
    }
