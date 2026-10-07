"""Full-event time-course statistics with explicit clocks, gaps and event identity."""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from xml.etree.ElementTree import Element, SubElement, tostring

import numpy as np
from scipy.signal import find_peaks

from .analysis import _sample_signature, atomic_json, watch_parent
from .biology import output_columns
from .fileio import load_validated_array
from .models import (
    Gate,
    GateDimension,
    KineticsBin,
    KineticsData,
    KineticsFit,
    KineticsRange,
    KineticsRangeSummary,
    KineticsRequest,
    KineticsResult,
    Workspace,
)
from .science import Engine, save_array
from .store import Store, now

METHOD = "exact-time-bins-1"
FULL_RANGE_ID = hashlib.sha256(b"CytoForge kinetics full collection").hexdigest()[:32]


def number(value):
    return float(value) if value is not None and np.isfinite(value) else None


def quantile(values, percentile):
    """Linear order-statistic interpolation without an overflowing high-low difference."""
    if not len(values):
        return None
    ordered = np.sort(values)
    position = percentile / 100 * (len(ordered) - 1)
    lower, upper = math.floor(position), math.ceil(position)
    fraction = position - lower
    return number(ordered[lower] * (1 - fraction) + ordered[upper] * fraction)


def mean(values):
    if not len(values):
        return None
    scale = max(float(np.max(np.abs(values))), 1e-300)
    return number(np.mean(values / scale) * scale)


def centered(values):
    # Subtract a nearby value before scaling, retaining small variations on a large offset.
    with np.errstate(all="ignore"):
        differences = values - values[0]
    if np.all(np.isfinite(differences)):
        scale = max(float(np.abs(differences).max()), 1e-300)
        units = differences / scale
    else:
        scale = max(float(np.abs(values).max()), 1e-300)
        units = values / scale
    return units - mean(units), scale


def product_ratio(a, b, c=1.0):
    if a == 0 or b == 0:
        return 0.0
    ma, ea = math.frexp(a)
    mb, eb = math.frexp(b)
    mc, ec = math.frexp(c)
    try:
        return number(math.ldexp(ma * mb / mc, ea + eb - ec))
    except OverflowError:
        return None


def validate_request(workspace, request):
    samples = {s.id: s for s in workspace.samples}
    for source in request.inputs:
        sample = samples.get(source.sample_id)
        if sample is None:
            raise ValueError("Kinetics references a missing sample")
        required = {request.channel} | ({request.time_channel} if request.time_channel else set())
        missing = required - {c.name for c in sample.channels}
        if missing:
            raise ValueError(
                f"{sample.name} is missing kinetics parameters: {', '.join(sorted(missing))}"
            )
        if source.gate_id and not any(
            g.id == source.gate_id and g.sample_id == sample.id for g in workspace.gates
        ):
            raise ValueError("The kinetics population must belong to its input sample")


def input_snapshot(workspace, request):
    validate_request(workspace, request)
    channels = [request.channel] + ([request.time_channel] if request.time_channel else [])
    return {
        "method": METHOD,
        "settings": request.model_dump(exclude={"name", "revision", "replace_result_id"}),
        "scientific_input": [
            _sample_signature(
                workspace, SimpleNamespace(channels=channels, use_transforms=False), source
            )
            for source in request.inputs
        ],
    }


def fingerprint(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


def input_hash(workspace, request):
    return fingerprint(input_snapshot(workspace, request))


def is_stale(workspace, result):
    try:
        return input_hash(workspace, result.request) != result.input_hash
    except (KeyError, StopIteration, ValueError):
        return True


def clock(raw, request, sample_id):
    raw = np.asarray(raw, dtype=np.float64).copy()
    finite_ids = np.flatnonzero(np.isfinite(raw))
    finite = raw[finite_ids]
    decreases = np.flatnonzero(finite[1:] < finite[:-1]) + 1
    repeated = int(np.sum(finite[1:] == finite[:-1]))
    if len(decreases) and request.clock_policy == "reject":
        raise ValueError(
            f"Time decreases {len(decreases)} times. Review the clock and explicitly "
            "choose recorded-time pooling or reset unwrapping."
        )
    if len(decreases) and request.clock_policy == "unwrap":
        positive = np.diff(finite)
        positive = positive[positive > 0]
        if not len(positive):
            raise ValueError("Clock resets cannot be unwrapped without a positive time step")
        step = quantile(positive, 50)
        additions = np.zeros(len(finite), dtype=np.float64)
        with np.errstate(all="ignore"):
            additions[decreases] = finite[decreases - 1] - finite[decreases] + step
            finite = finite + np.cumsum(additions)
        if not np.all(np.isfinite(finite)):
            raise ValueError("Unwrapped time exceeds the finite numeric range")
        raw[finite_ids] = finite
    with np.errstate(all="ignore"):
        raw = raw * request.time_multiplier + request.time_offsets.get(sample_id, 0.0)
    if np.any(~np.isfinite(raw[finite_ids])):
        raise ValueError("Time calibration exceeds the finite numeric range")
    raw[~np.isfinite(raw)] = np.nan
    return raw, len(decreases), repeated


def edges_for(lower, upper, bins):
    fractions = np.linspace(0, 1, bins + 1)
    edges = lower * (1 - fractions) + upper * fractions
    edges[0], edges[-1] = lower, upper
    if not np.all(np.isfinite(edges)) or np.any(edges[1:] <= edges[:-1]):
        raise ValueError(
            "Time bins are narrower than numeric precision; use fewer bins or wider bounds"
        )
    return edges


def bin_ids(times, edges):
    inside = np.isfinite(times) & (times >= edges[0]) & (times <= edges[-1])
    indices = np.full(len(times), -1, dtype=np.int32)
    indices[inside] = np.minimum(
        np.searchsorted(edges, times[inside], side="right") - 1, len(edges) - 2
    )
    return indices


def range_mask(times, interval, domain):
    # Adjacent ranges partition boundary events. The final domain endpoint is included.
    upper = times <= interval.end if domain and interval.end >= domain[1] else times < interval.end
    inside = np.isfinite(times) & (times >= interval.start) & upper
    if domain:
        inside &= (times >= domain[0]) & (times <= domain[1])
    return inside


def smooth(values, request):
    values = np.asarray([np.nan if v is None else v for v in values], dtype=np.float64)
    output = values.copy()
    if request.smoothing == "none":
        return [number(v) for v in output]
    valid = np.isfinite(values)
    changes = np.diff(np.r_[False, valid, False].astype(int))
    radius = request.smoothing_width // 2
    for start, end in zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1), strict=True):
        for i in range(start, end):
            lo, hi = max(start, i - radius), min(end, i + radius + 1)
            local = values[lo:hi]
            weights = (
                np.exp(-0.5 * ((np.arange(lo, hi) - i) / request.gaussian_sigma) ** 2)
                if request.smoothing == "gaussian"
                else np.ones(len(local))
            )
            scale = max(float(np.abs(local).max()), 1e-300)
            output[i] = np.dot(local / scale, weights / weights.sum()) * scale
    return [number(v) for v in output]


def curve_summary(bins, interval):
    points = [
        b
        for b in bins
        if b.value is not None
        and interval.start <= b.center
        and (
            b.center < interval.end
            or (bins and interval.end >= bins[-1].end and b.center <= interval.end)
        )
    ]
    result = dict(
        curve_points=len(points),
        peak_time=None,
        peak=None,
        mean=None,
        slope=None,
        auc=None,
        covered_duration=0.0,
        duration=number(interval.end - interval.start),
    )
    if points:
        ys = np.array([b.value for b in points], dtype=np.float64)
        xs = np.array([b.center for b in points], dtype=np.float64)
        peak = int(np.argmax(ys))
        result.update(peak_time=float(xs[peak]), peak=float(ys[peak]), mean=mean(ys))
        if len(points) >= 2:
            centered_x, xscale = centered(xs)
            centered_y, yscale = centered(ys)
            denominator = np.dot(centered_x, centered_x)
            with np.errstate(all="ignore"):
                if denominator > 0:
                    result["slope"] = product_ratio(
                        float(np.dot(centered_x, centered_y) / denominator), yscale, xscale
                    )
    # Integrate only contiguous measured segments; gaps remain unmeasured.
    pieces, durations = [], []
    yscale = max((abs(b.value) for b in bins if b.value is not None), default=1.0)
    yscale = max(yscale, 1e-300)
    for a, b in zip(bins[:-1], bins[1:], strict=True):
        if a.value is None or b.value is None:
            continue
        lo, hi = max(a.center, interval.start), min(b.center, interval.end)
        if lo >= hi:
            continue
        width = b.center - a.center
        p, q = (lo - a.center) / width, (hi - a.center) / width
        left = a.value / yscale * (1 - p) + b.value / yscale * p
        right = a.value / yscale * (1 - q) + b.value / yscale * q
        duration = hi - lo
        with np.errstate(all="ignore"):
            pieces.append(left / 2 + right / 2)
        durations.append(duration)
    if pieces:
        tscale = max(durations)
        weighted = math.fsum(p * (d / tscale) for p, d in zip(pieces, durations, strict=True))
        factors = [math.frexp(v) for v in (weighted, yscale, tscale)]
        try:
            result["auc"] = number(
                math.ldexp(math.prod(v[0] for v in factors), sum(v[1] for v in factors))
            )
        except OverflowError:
            result["auc"] = None
        result["covered_duration"] = product_ratio(math.fsum(d / tscale for d in durations), tscale)
    return result


def statistic(values, request, responders, threshold):
    selected = values[responders] if request.above_threshold_only else values
    if len(selected) < request.minimum_events:
        return None
    if request.statistic == "percent_positive":
        return 100 * int(responders.sum()) / len(values) if threshold is not None else None
    if request.statistic == "mean":
        return mean(selected)
    if request.statistic == "geometric_mean":
        return number(np.exp(mean(np.log(selected)))) if np.all(selected > 0) else None
    return quantile(selected, request.percentile if request.statistic == "percentile" else 50)


def binned(values, request, domain, threshold, progress=lambda stage, fraction: None):
    times, signal = values[:, 0], values[:, 1]
    timed = (values[:, 2] == 1) & np.isfinite(times)
    finite = timed & np.isfinite(signal)
    responders = (
        finite & (signal > threshold)
        if threshold is not None
        else np.zeros(len(values), dtype=bool)
    )
    selected = responders if request.above_threshold_only else finite
    output_bins = []
    represented = np.zeros(len(values), dtype=bool)
    if domain:
        edges = edges_for(*domain, request.bins)
        indices = bin_ids(times, edges)
        represented = finite & (indices >= 0)
        ordered_ids = np.flatnonzero(timed & (indices >= 0))
        ordered_ids = ordered_ids[np.argsort(indices[ordered_ids], kind="stable")]
        counts = np.bincount(indices[ordered_ids], minlength=request.bins)
        boundaries = np.r_[0, np.cumsum(counts)]
        for i in range(request.bins):
            if i % max(1, request.bins // 20) == 0:
                progress("Calculating exact time-bin statistics", 0.15 + 0.6 * i / request.bins)
            ids = ordered_ids[boundaries[i] : boundaries[i + 1]]
            ids = ids[np.isfinite(signal[ids])]
            response = responders[ids]
            output_bins.append(
                KineticsBin(
                    index=i,
                    start=float(edges[i]),
                    end=float(edges[i + 1]),
                    center=float(edges[i] / 2 + edges[i + 1] / 2),
                    population_count=int(counts[i]),
                    finite_count=len(ids),
                    selected_count=None
                    if request.above_threshold_only and threshold is None
                    else int(selected[ids].sum()),
                    responder_count=None if threshold is None else int(response.sum()),
                    raw_value=statistic(signal[ids], request, response, threshold),
                )
            )
        for b, value in zip(
            output_bins, smooth([b.raw_value for b in output_bins], request), strict=True
        ):
            b.value = value
    return output_bins, represented


def intervals_for(request, domain):
    return request.ranges or (
        [KineticsRange(id=FULL_RANGE_ID, name="Full collection", start=domain[0], end=domain[1])]
        if domain
        else []
    )


def summaries_for(values, request, domain, threshold, bins):
    times, signal = values[:, 0], values[:, 1]
    timed = (values[:, 2] == 1) & np.isfinite(times)
    finite = timed & np.isfinite(signal)
    responders = (
        finite & (signal > threshold)
        if threshold is not None
        else np.zeros(len(values), dtype=bool)
    )
    selected = responders if request.above_threshold_only else finite
    summaries = []
    for interval in intervals_for(request, domain):
        inside = range_mask(times, interval, domain)
        summaries.append(
            KineticsRangeSummary(
                **interval.model_dump(),
                population_count=int((inside & timed).sum()),
                finite_count=int((inside & finite).sum()),
                selected_count=None
                if request.above_threshold_only and threshold is None
                else int((inside & selected).sum()),
                responder_count=None if threshold is None else int((inside & responders).sum()),
                **curve_summary(bins, interval),
            )
        )
    return summaries


def fit_sample(workspace, request, source, engine, progress=lambda stage, fraction: None):
    sample = engine.sample(workspace, source.sample_id)
    population = engine.mask(workspace, sample, source.gate_id)
    signal = engine.column(
        workspace, sample, request.channel, compensated=request.compensated
    ).astype(np.float64, copy=True)
    if request.time_channel:
        raw_time = engine.column(workspace, sample, request.time_channel, compensated=False)
    else:
        with np.errstate(all="ignore"):
            raw_time = np.arange(sample.event_count, dtype=np.float64) / request.event_rate
        if not np.all(np.isfinite(raw_time)):
            raise ValueError("The assumed event rate produces a nonfinite acquisition duration")
    times, resets, repeated = clock(raw_time, request, sample.id)
    signal[~np.isfinite(signal)] = np.nan
    values = np.column_stack([times, signal, population.astype(np.float64)])
    timed = population & np.isfinite(times)
    finite = timed & np.isfinite(signal)
    warnings = []
    if request.time_channel is None:
        warnings.append(
            "Time is estimated from original event index and an assumed constant event rate; "
            "pauses and flow changes are not measured."
        )
    elif not sample.metadata.get("timestep"):
        warnings.append(
            "Verify time units/calibration; no FCS TIMESTEP is recorded for this acquisition."
        )
    if resets:
        warnings.append(
            f"{resets} clock decreases: {request.clock_policy} was explicitly selected."
        )
    if repeated:
        warnings.append(f"{repeated} repeated timestamps are retained as separate events.")
    if int(timed.sum()) < int(population.sum()):
        warnings.append(f"{int(population.sum() - timed.sum())} source events have undefined time.")
    if int(finite.sum()) < int(timed.sum()):
        warnings.append(
            f"{int(timed.sum() - finite.sum())} timed source events have undefined signal."
        )
    acquired_time = times[np.isfinite(times)]
    lo = (
        request.time_min
        if request.time_min is not None
        else (float(acquired_time.min()) if len(acquired_time) else None)
    )
    hi = (
        request.time_max
        if request.time_max is not None
        else (float(acquired_time.max()) if len(acquired_time) else None)
    )
    domain = (lo, hi) if lo is not None and hi is not None and lo < hi else None
    threshold, baseline_count = request.threshold, 0
    if request.threshold_mode == "baseline_percentile":
        # A reference baseline may precede a cropped response display domain.
        baseline_end = request.baseline.end
        upper = (
            times <= baseline_end
            if len(acquired_time) and baseline_end >= acquired_time.max()
            else times < baseline_end
        )
        baseline = finite & (times >= request.baseline.start) & upper
        baseline_count = int(baseline.sum())
        threshold = quantile(signal[baseline], request.baseline_percentile)
        if threshold is None:
            warnings.append(
                "The baseline has no finite signal events; responder statistics are undefined."
            )
    responders = (
        finite & (signal > threshold) if threshold is not None else np.zeros(len(times), dtype=bool)
    )
    selected = responders if request.above_threshold_only else finite
    output_bins, represented = binned(values, request, domain, threshold, progress)
    if domain is None:
        warnings.append(
            "The acquisition has no increasing finite time domain; "
            "its time-course curve is undefined."
        )
    if not represented.any():
        warnings.append("No finite source events are represented in the analysis time domain.")
    if request.statistic == "geometric_mean" and np.any(selected & (signal <= 0)):
        warnings.append(
            "Bins containing selected nonpositive signals have undefined geometric means."
        )
    summaries = summaries_for(values, request, domain, threshold, output_bins)
    data = KineticsData(
        sample_id=sample.id,
        event_count=sample.event_count,
        population_count=int(population.sum()),
        time_count=int(timed.sum()),
        finite_count=int(finite.sum()),
        represented_count=int(represented.sum()),
        sha256="0" * 64,
    )
    fit = KineticsFit(
        sample_id=sample.id,
        gate_id=source.gate_id,
        time_domain=domain,
        threshold=threshold,
        baseline_count=baseline_count,
        reset_count=resets,
        repeated_timestamps=repeated,
        bins=output_bins,
        ranges=summaries,
        warnings=warnings,
    )
    return fit, data, values


def calculate(
    workspace, request, engine, identifier, progress=lambda stage, fraction: None, sink=None
):
    started = time.monotonic()
    snapshot = input_snapshot(workspace, request)
    fits, data, arrays = [], [], {}
    for index, source in enumerate(request.inputs):
        sample = engine.sample(workspace, source.sample_id)

        def stage(message, fraction, sample=sample, index=index):
            progress(f"{sample.name}: {message}", (index + fraction) / len(request.inputs))

        stage("Reading original event timing and signal", 0.03)
        fit, original, values = fit_sample(workspace, request, source, engine, stage)
        fits.append(fit)
        data.append(original)
        if sink is None:
            arrays[sample.id] = values
        else:
            original.sha256 = sink(original, values)
    return KineticsResult(
        id=identifier,
        request=request,
        created_at=now(),
        input_hash=fingerprint(snapshot),
        input_snapshot=snapshot,
        columns=output_columns(
            workspace,
            request,
            [f"{request.name} Time", f"{request.name} Signal", f"{request.name} Source"],
        ),
        fits=fits,
        data=data,
        versions={
            "cytoforge_kinetics": METHOD,
            "numpy": version("numpy"),
            "scipy": version("scipy"),
        },
        duration_seconds=time.monotonic() - started,
    ), arrays


def selection(values, fit, range_id, response_only=False):
    interval = next((r for r in fit.ranges if r.id == range_id), None)
    if interval is None:
        raise ValueError("Kinetics population references a missing time range")
    inside = (values[:, 2] == 1) & range_mask(values[:, 0], interval, fit.time_domain)
    if response_only:
        if fit.threshold is None:
            raise ValueError(
                "Responder membership is undefined because this baseline has no finite events"
            )
        inside &= np.isfinite(values[:, 1]) & (values[:, 1] > fit.threshold)
    return inside


def validate_data(result, data, values):
    if values.shape != (data.event_count, 3) or values.dtype != np.float64:
        raise ValueError(
            "Kinetics data must retain float64 time, signal and source membership "
            "per original event"
        )
    if np.any(np.isinf(values[:, :2])) or not np.all((values[:, 2] == 0) | (values[:, 2] == 1)):
        raise ValueError("Kinetics data contains invalid times, signals or membership flags")
    fit = next(f for f in result.fits if f.sample_id == data.sample_id)
    pop = values[:, 2] == 1
    timed = pop & np.isfinite(values[:, 0])
    finite = timed & np.isfinite(values[:, 1])
    if (int(pop.sum()), int(timed.sum()), int(finite.sum())) != (
        data.population_count,
        data.time_count,
        data.finite_count,
    ):
        raise ValueError("Kinetics event counts failed validation")
    request = result.request
    if fingerprint(result.input_snapshot) != result.input_hash or result.input_snapshot.get(
        "settings"
    ) != request.model_dump(exclude={"name", "revision", "replace_result_id"}):
        raise ValueError("Kinetics scientific settings failed snapshot validation")
    times, signal = values[:, 0], values[:, 1]
    acquired = times[np.isfinite(times)]
    lo = (
        request.time_min
        if request.time_min is not None
        else (float(acquired.min()) if len(acquired) else None)
    )
    hi = (
        request.time_max
        if request.time_max is not None
        else (float(acquired.max()) if len(acquired) else None)
    )
    domain = (lo, hi) if lo is not None and hi is not None and lo < hi else None
    if domain != fit.time_domain:
        raise ValueError("Kinetics time domain failed event-identity validation")
    threshold, baseline_count = request.threshold, 0
    if request.threshold_mode == "baseline_percentile":
        upper = (
            times <= request.baseline.end
            if len(acquired) and request.baseline.end >= acquired.max()
            else times < request.baseline.end
        )
        baseline = finite & (times >= request.baseline.start) & upper
        baseline_count = int(baseline.sum())
        threshold = quantile(signal[baseline], request.baseline_percentile)
    if threshold != fit.threshold or baseline_count != fit.baseline_count:
        raise ValueError("Kinetics baseline threshold failed event-identity validation")
    bins, represented = binned(values, request, domain, threshold)
    if int(represented.sum()) != data.represented_count or bins != fit.bins:
        raise ValueError("Kinetics bin counts and curve failed event-identity validation")
    if summaries_for(values, request, domain, threshold, bins) != fit.ranges:
        raise ValueError("Kinetics range counts and statistics failed event-identity validation")


def load_data(store, workspace_id, result, data):
    path = store.analysis_path(workspace_id, result.id, data.sample_id)
    try:
        with path.open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != data.sha256:
                raise ValueError("Kinetics event data failed its integrity check")
        return load_validated_array(path, lambda values: validate_data(result, data, values))
    except OSError as exc:
        raise ValueError("Kinetics event data is missing; restore the project archive") from exc


def suggested_ranges(fit, maximum=8, prominence=None):
    """Reviewable peak/minimum partitions of measured curve segments."""
    if fit.time_domain is None:
        return []
    values = np.array([np.nan if b.value is None else b.value for b in fit.bins])
    valid = np.isfinite(values)
    changes = np.diff(np.r_[False, valid, False].astype(int))
    cuts = []
    for start, end in zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1), strict=True):
        segment = values[start:end]
        scale = max(float(np.abs(segment).max()), 1e-300)
        local = segment / scale
        effect = (
            prominence / scale if prominence is not None else max(float(np.ptp(local)) * 0.1, 1e-12)
        )
        peaks, _ = find_peaks(local, prominence=effect, distance=3)
        for a, b in zip(peaks[:-1], peaks[1:], strict=True):
            cut = start + int(a + np.argmin(local[a : b + 1]))
            cuts.append(fit.bins[cut].center)
    bounds = [fit.time_domain[0], *sorted(set(cuts))[: maximum - 1], fit.time_domain[1]]
    return [
        KineticsRange(name=f"Range {i + 1}", start=a, end=b)
        for i, (a, b) in enumerate(zip(bounds[:-1], bounds[1:], strict=True))
        if a < b
    ]


def gate_name(model, label, identifier=""):
    prefix = model + " · "
    available = 160 - len(prefix)
    if len(label) > available:
        label = (label[: available - 33] + " " + identifier) if available > 33 else identifier
    return prefix + label


def sync_gates(workspace, result, sample, source, target=None):
    """Ordinary gates on frozen, event-aligned outputs; preserve IDs on replacement."""
    fit = next(f for f in result.fits if f.sample_id == sample.id)
    owned = [
        g
        for g in workspace.gates
        if g.sample_id == sample.id and g.provenance.get("kinetics_id") == result.id
    ]
    if target and any(
        g.provenance.get("range_id") not in {r.id for r in fit.ranges}
        for g in owned
        if g.provenance.get("range_id")
    ):
        raise ValueError("Replacement time ranges are undefined; save the report as a new model")
    if not owned and not (
        result.request.create_range_gates or result.request.create_responder_gates
    ):
        return
    source_gate = next((g for g in owned if g.provenance.get("kinetics_role") == "source"), None)
    if source_gate is None:
        source_gate = Gate(
            sample_id=sample.id,
            parent_id=source.gate_id,
            name=gate_name(result.request.name, "Source"),
            kind="hyperrectangle",
            dimensions=[
                GateDimension(
                    channel=result.columns[2], compensation_ref="uncompensated", minimum=0.5
                )
            ],
            provenance={"kinetics_id": result.id, "kinetics_role": "source"},
        )
        workspace.gates.append(source_gate)
    for interval in fit.ranges:
        lo, hi = interval.start, interval.end
        if fit.time_domain:
            lo, hi = max(lo, fit.time_domain[0]), min(hi, fit.time_domain[1])
        # Only the final analysis endpoint is included. nextafter preserves strict event boundaries.
        last = fit.time_domain is not None and interval.end >= fit.time_domain[1]
        with np.errstate(over="ignore"):
            upper = number(np.nextafter(hi, np.inf)) if last else hi
        dimensions = (
            [
                GateDimension(
                    channel=result.columns[0],
                    compensation_ref="uncompensated",
                    minimum=lo,
                    maximum=upper,
                )
            ]
            if lo < hi or (lo == hi and last)
            else [
                GateDimension(
                    channel=result.columns[2], compensation_ref="uncompensated", minimum=1.5
                )
            ]
        )
        range_gate = next(
            (
                g
                for g in owned
                if g.provenance.get("kinetics_role") == "range"
                and g.provenance.get("range_id") == interval.id
            ),
            None,
        )
        if range_gate is None:
            if not (result.request.create_range_gates or result.request.create_responder_gates):
                continue
            range_gate = Gate(
                sample_id=sample.id,
                parent_id=source_gate.id,
                name=gate_name(result.request.name, interval.name, interval.id),
                kind="hyperrectangle",
                dimensions=dimensions,
            )
            workspace.gates.append(range_gate)
        range_gate.dimensions = dimensions
        range_gate.name = gate_name(result.request.name, interval.name, interval.id)
        range_gate.color = interval.color
        range_gate.provenance = {
            "kinetics_id": result.id,
            "kinetics_role": "range",
            "range_id": interval.id,
            "range_name": interval.name,
            "start": interval.start,
            "end": interval.end,
        }
        responder = next(
            (
                g
                for g in owned
                if g.provenance.get("kinetics_role") == "responders"
                and g.provenance.get("range_id") == interval.id
            ),
            None,
        )
        if not result.request.create_responder_gates and responder is None:
            continue
        if fit.threshold is None:
            raise ValueError(f"{sample.name}: responder gates require a defined baseline threshold")
        with np.errstate(over="ignore"):
            lower = number(np.nextafter(fit.threshold, np.inf))
        dimensions = (
            [
                GateDimension(
                    channel=result.columns[1], compensation_ref="uncompensated", minimum=lower
                )
            ]
            if lower is not None
            else [
                GateDimension(
                    channel=result.columns[2], compensation_ref="uncompensated", minimum=1.5
                )
            ]
        )
        if responder is None:
            responder = Gate(
                sample_id=sample.id,
                parent_id=range_gate.id,
                name=gate_name(result.request.name, "Responders"),
                kind="hyperrectangle",
                dimensions=dimensions,
            )
            workspace.gates.append(responder)
        responder.dimensions = dimensions
        responder.provenance = {
            "kinetics_id": result.id,
            "kinetics_role": "responders",
            "range_id": interval.id,
            "threshold": fit.threshold,
            "comparison": "strictly greater than",
        }


def figure_svg(result, fit, title, stale=False):
    """Standalone vector figure preserving unmeasured gaps and explicit time ranges."""
    svg = Element(
        "svg",
        xmlns="http://www.w3.org/2000/svg",
        width="1100",
        height="620",
        viewBox="0 0 1100 620",
        role="img",
    )
    SubElement(svg, "title").text = f"{result.request.name} · {title}"
    SubElement(svg, "rect", width="1100", height="620", fill="#101827")

    def label(x, y, value, size=14, color="#bccbe1"):
        SubElement(
            svg,
            "text",
            x=str(x),
            y=str(y),
            fill=color,
            **{"font-family": "sans-serif", "font-size": str(size)},
        ).text = str(value)

    label(50, 42, f"{result.request.name} · {title}", 23, "#f0f5ff")
    label(
        50,
        70,
        f"{result.request.statistic.replace('_', ' ')} · {result.request.channel} · "
        f"{result.request.smoothing.replace('_', ' ')}",
    )
    if stale:
        label(50, 95, "Historical snapshot · scientific inputs have changed", 14, "#efb76b")
    finite = [b.value for b in fit.bins if b.value is not None]
    if fit.time_domain is None or not finite:
        label(60, 275, "No measured curve in this time domain", 19)
    else:
        left, top, width, height = 90, 125, 950, 365
        lo, hi = fit.time_domain
        ymin, ymax = min(finite), max(finite)
        yscale = max(abs(ymin), abs(ymax), 1e-300)
        ynmin, ynmax = ymin / yscale, ymax / yscale
        if ynmin == ynmax:
            ynmin, ynmax = ynmin - 0.5, ynmax + 0.5
        xscale = max(abs(lo), abs(hi), 1e-300)

        def x(v):
            return left + ((v / xscale - lo / xscale) / (hi / xscale - lo / xscale)) * width

        def y(v):
            return top + height - ((v / yscale - ynmin) / (ynmax - ynmin)) * height

        for interval in fit.ranges:
            a, b = max(lo, interval.start), min(hi, interval.end)
            if a < b:
                SubElement(
                    svg,
                    "rect",
                    x=str(x(a)),
                    y=str(top),
                    width=str(x(b) - x(a)),
                    height=str(height),
                    fill=interval.color,
                    opacity="0.08",
                )
        for i in range(5):
            t = i / 4
            py = top + t * height
            SubElement(
                svg,
                "line",
                x1=str(left),
                x2=str(left + width),
                y1=str(py),
                y2=str(py),
                stroke="#2b3950",
            )
            label(8, py + 5, format((ynmax - t * (ynmax - ynmin)) * yscale, ".5g"), 12)
            label(left + t * width, top + height + 24, format(lo * (1 - t) + hi * t, ".5g"), 12)
        segments, segment = [], []
        for b in fit.bins:
            if b.value is None:
                if segment:
                    segments.append(segment)
                segment = []
            else:
                segment.append(f"{x(b.center):.5f},{y(b.value):.5f}")
        if segment:
            segments.append(segment)
        for points in segments:
            if len(points) == 1:
                px, py = points[0].split(",")
                SubElement(svg, "circle", cx=px, cy=py, r="3", fill="#38d9ba")
            else:
                SubElement(
                    svg,
                    "polyline",
                    points=" ".join(points),
                    fill="none",
                    stroke="#38d9ba",
                    **{"stroke-width": "2.5"},
                )
        label(480, 545, "Aligned time", 14)
    label(
        50, 577, "Empty bins remain unmeasured; smoothing and integration do not bridge gaps.", 12
    )
    label(50, 601, "Event-aligned snapshot · " + result.input_hash[:16] + " · " + METHOD, 12)
    return tostring(svg, encoding="utf-8", xml_declaration=True)


def run_kinetics(directory):
    directory = Path(directory)
    stop = threading.Event()
    store = None
    try:
        payload = json.loads((directory / "input.json").read_text())
        threading.Thread(
            target=watch_parent, args=(payload["parent_pid"], stop), daemon=True
        ).start()
        workspace = Workspace.model_validate(payload["workspace"])
        request = KineticsRequest.model_validate(payload["request"])
        if input_hash(workspace, request) != payload["input_hash"]:
            raise ValueError("Kinetics inputs changed before its worker started")
        store = Store(Path(payload["data_dir"]))
        for source in request.inputs:
            sample = next(s for s in workspace.samples if s.id == source.sample_id)
            with store.data_path(workspace.id, sample.id).open("rb") as handle:
                if hashlib.file_digest(handle, "sha256").hexdigest() != sample.sha256:
                    raise ValueError("Acquired events failed their integrity check")

        def progress(stage, fraction):
            atomic_json(directory / "progress.json", {"stage": stage, "progress": fraction})

        def persist(data, values):
            return save_array(
                store.analysis_path(workspace.id, payload["id"], data.sample_id), values
            )

        result, _ = calculate(
            workspace, request, Engine(store), payload["id"], progress, sink=persist
        )
        for data in result.data:
            load_data(store, workspace.id, result, data)
        atomic_json(directory / "result.json", result.model_dump())
    except Exception as exc:
        atomic_json(directory / "error.json", {"error": str(exc) or type(exc).__name__})
    finally:
        stop.set()
        if store:
            store.close()
