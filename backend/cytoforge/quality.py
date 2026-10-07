"""Reviewable acquisition diagnostics, with immutable, full-data event identity.

This is CytoForge's robust-bin method, not a reimplementation of PeacoQC,
flowCut or flowAI. The mathematical choices and limitations are in docs/QC.md.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from scipy.stats import spearmanr

from .analysis import _sample_signature, atomic_json, gate_signature, watch_parent
from .models import (
    AnalysisInput,
    Gate,
    QualityBin,
    QualityData,
    QualityRequest,
    QualityResult,
    Workspace,
)
from .science import Engine, save_array
from .store import Store, now

FLAGS = {"nonfinite": 1, "time": 2, "saturation": 4, "pulse": 8}
OUTSIDE = 16
METHOD_VERSION = "robust-bins-1"
MAX_BINS = 1024


def validate_request(workspace: Workspace, request: QualityRequest):
    sample = next((s for s in workspace.samples if s.id == request.sample_id), None)
    if sample is None:
        raise ValueError("QC sample does not exist")
    if sample.event_count >= 2**32:
        raise ValueError("QC event identity supports fewer than 2^32 events per sample")
    names = {c.name for c in sample.channels}
    acquired = {c.name for c in sample.acquisition_channels}
    if not set(request.channels) <= names:
        raise ValueError("QC markers must exist in the selected sample")
    raw_names = set(request.saturation_channels) | {
        n for n in (request.time_channel, request.pulse_area, request.pulse_height) if n
    }
    if not {sample.aliases.get(name, name) for name in raw_names} <= acquired:
        raise ValueError("Time, saturation and pulse checks require acquired channels")
    if request.gate_id and not any(
        g.id == request.gate_id and g.sample_id == sample.id for g in workspace.gates
    ):
        raise ValueError("QC source population must belong to the selected sample")


def input_snapshot(workspace: Workspace, request: QualityRequest) -> dict:
    validate_request(workspace, request)
    sample = next(s for s in workspace.samples if s.id == request.sample_id)
    scientific = _sample_signature(
        workspace,
        SimpleNamespace(channels=request.channels, use_transforms=request.use_transforms),
        AnalysisInput(sample_id=request.sample_id, gate_id=request.gate_id),
    )
    # Default numeric values must have the same representation before and after
    # persistence, including transforms on explicit and ratio gate dimensions.
    by_gate = {gate.id: gate for gate in workspace.gates}
    scientific["gates"] = {
        identifier: gate_signature(Gate.model_validate(by_gate[identifier].model_dump()))
        for identifier in scientific["gates"]
    }
    raw_names = set(request.saturation_channels) | {
        n for n in (request.time_channel, request.pulse_area, request.pulse_height) if n
    }
    raw_definitions = [
        {"name": c.name, "range": float(c.range)}
        for c in sample.acquisition_channels
        if c.name in raw_names
    ]
    for name in sorted(raw_names & sample.aliases.keys()):
        source = sample.aliases[name]
        channel = next(c for c in sample.acquisition_channels if c.name == source)
        raw_definitions.append(
            {"name": name, "alias_source": source, "range": float(channel.range)}
        )
    return {
        "method": METHOD_VERSION,
        "settings": request.model_dump(exclude={"revision", "name"}),
        "scientific_input": scientific,
        "raw_channels": raw_definitions,
        "time_step": sample.metadata.get("timestep"),
    }


def fingerprint(snapshot: dict) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


def input_hash(workspace: Workspace, request: QualityRequest) -> str:
    return fingerprint(input_snapshot(workspace, request))


def is_stale(workspace: Workspace, result: QualityResult) -> bool:
    try:
        return input_hash(workspace, result.request) != result.input_hash
    except (KeyError, StopIteration, ValueError):
        return True


def number(value):
    return float(value) if value is not None and np.isfinite(value) else None


def robust_scores(values, minimum_shift, threshold):
    """Median/MAD score; the minimum effect sets a nonzero scale floor."""
    values = np.asarray(values, dtype=float)
    finite = np.isfinite(values)
    if not finite.any():
        return np.full_like(values, np.nan), None, None
    reference = max(float(np.max(np.abs(values[finite]))), 1e-300)
    normalized = values / reference
    center = float(np.median(normalized[finite]))
    mad = float(1.4826 * np.median(np.abs(normalized[finite] - center)))
    scale = max(mad, minimum_shift / reference / threshold, abs(center) * 1e-12, 1e-12 / reference)
    return (
        np.abs(normalized - center) / scale,
        number(center * reference),
        number(scale * reference),
    )


def signal_quantiles(values):
    # NumPy's unscaled interpolation can overflow for two valid large floats.
    reference = float(np.max(np.abs(values)))
    if reference > 1e150:
        return np.quantile(values / reference, [0.1, 0.5, 0.9]) * reference
    return np.quantile(values, [0.1, 0.5, 0.9])


def calculate(
    workspace: Workspace,
    request: QualityRequest,
    engine: Engine,
    identifier: str,
    progress=lambda stage, fraction: None,
) -> tuple[QualityResult, np.ndarray]:
    started = time.monotonic()
    snapshot = input_snapshot(workspace, request)
    sample = engine.sample(workspace, request.sample_id)
    population = engine.mask(workspace, sample, request.gate_id)
    n = sample.event_count
    flags = np.where(population, 0, OUTSIDE).astype(np.uint32)
    size = max(request.bin_events, math.ceil(n / MAX_BINS))
    bin_ids = np.arange(n, dtype=np.uint32) // size
    bins = [
        QualityBin(
            index=i,
            start=start,
            end=min(n, start + size),
            population_count=int(population[start : start + size].sum()),
        )
        for i, start in enumerate(range(0, n, size))
    ]
    warnings = []
    diagnostics = {
        "method": METHOD_VERSION,
        "effective_bin_events": size,
        "acquisition_order": "Original event order; timestamps are never sorted",
        "signal_basis": "Sample compensation" if request.compensated else "Acquired/derived values",
        "rate_basis": "All acquired events, independent of source population frequency",
        "signal_baselines": {},
        "saturation": [],
        "pulse": None,
        "time": None,
    }
    if not population.any():
        warnings.append("The source population is empty. No events can be retained or rejected.")
    if size != request.bin_events:
        warnings.append(f"Bins contain {size:,} acquired events to keep at most {MAX_BINS} bins.")

    progress("Inspecting acquisition time and rate", 0.08)
    if request.time_channel:
        times = engine.column(workspace, sample, request.time_channel, compensated=False)
        valid = np.isfinite(times) & (times >= 0)
        delta = np.diff(times)
        adjacent = valid[:-1] & valid[1:]
        resets = np.flatnonzero(adjacent & (delta < 0)) + 1
        bad = ~valid
        bad[resets] = True
        flags[bad & population] |= FLAGS["time"]
        positive = delta[adjacent & (delta > 0) & np.isfinite(delta)]
        tick = float(np.median(positive)) if len(positive) else 0
        repeated = int((adjacent & (delta == 0)).sum())
        gaps = np.flatnonzero(adjacent & np.isfinite(delta) & (delta > 20 * tick)) + 1
        resolution_limited = 0
        diagnostics["time"] = {
            "channel": request.time_channel,
            "unit": "seconds"
            if sample.source == "FCS" and request.time_channel.lower() == "time"
            else "stored time units",
            "invalid_count": int((~valid).sum()),
            "resets": len(resets),
            "reset_event_ids": resets[:100].tolist(),
            "repeated_timestamps": repeated,
            "gap_count": len(gaps),
            "gap_event_ids": gaps[:100].tolist(),
            "largest_positive_gap": number(positive.max()) if len(positive) else None,
            "median_positive_step": tick,
            "acquired_duration": number(positive.sum()) if len(positive) else None,
        }
        # Prefix counts make each interval's time validation O(1).
        bad_time_prefix = np.r_[0, np.cumsum(~valid)]
        reset_prefix = np.r_[0, np.cumsum(adjacent & (delta < 0))]
        for b in bins:
            stop = min(b.end + 1, n)
            time_ok = bad_time_prefix[stop] == bad_time_prefix[b.start] and (
                reset_prefix[max(b.start, stop - 1)] == reset_prefix[b.start]
            )
            b.time_start = number(times[b.start])
            end_time = times[b.end] if b.end < n else times[b.end - 1] + tick
            b.time_end = number(end_time)
            duration = end_time - times[b.start]
            if time_ok and repeated and duration < 3 * tick:
                resolution_limited += 1
            elif time_ok and np.isfinite(duration) and duration > 0:
                b.rate = number((b.end - b.start) / float(duration))
            elif not time_ok:
                b.reasons.append("time: invalid or decreasing timestamps")
                b.suggested = True
        rates = np.array([b.rate if b.rate is not None else np.nan for b in bins])
        diagnostics["time"]["resolution_limited_bins"] = resolution_limited
        eligible = np.isfinite(rates) & np.array([b.end - b.start >= 50 for b in bins], dtype=bool)
        if eligible.sum() >= 8:
            logs = np.full(len(bins), np.nan)
            logs[eligible] = np.log(rates[eligible])
            scores, center, scale = robust_scores(
                logs, math.log(request.rate_min_fold), request.score_threshold
            )
            diagnostics["rate_baseline"] = {
                "median_rate": math.exp(center),
                "log_scale": scale,
                "eligible_bins": int(eligible.sum()),
            }
            for b, score in zip(bins, scores, strict=True):
                b.rate_score = number(score)
                if score > request.score_threshold and abs(math.log(b.rate) - center) >= math.log(
                    request.rate_min_fold
                ):
                    b.reasons.append("rate: atypical acquisition rate")
                    b.suggested = True
        else:
            warnings.append(
                "Fewer than eight measurable time bins; rate outlier scoring is disabled."
            )
        if resolution_limited:
            warnings.append(
                f"{resolution_limited} intervals span fewer than three positive timestamp steps. "
                "Their rate estimates are undefined; increase acquired events per bin."
            )
        if len(resets):
            warnings.append(
                "Time decreases in acquisition order. Reset intervals need manual review."
            )
        if not len(positive):
            warnings.append(
                "Time has no measurable positive steps. Acquisition rates are undefined."
            )
    else:
        warnings.append(
            "No time channel selected. Signal checks use event order; rates are unavailable."
        )

    for index, name in enumerate(request.channels):
        progress(f"Measuring stability · {name}", 0.15 + 0.55 * index / len(request.channels))
        spec = next(c.transform for c in sample.channels if c.name == name)
        values = engine.column(
            workspace, sample, name, spec if request.use_transforms else None, request.compensated
        )
        finite = np.isfinite(values)
        flags[population & ~finite] |= FLAGS["nonfinite"]
        quantiles = np.full((len(bins), 3), np.nan)
        for b in bins:
            selected = population[b.start : b.end] & finite[b.start : b.end]
            count = int(selected.sum())
            q = signal_quantiles(values[b.start : b.end][selected]) if count else [None] * 3
            b.signals[name] = {
                "finite_count": count,
                "p10": number(q[0]),
                "median": number(q[1]),
                "p90": number(q[2]),
                "score": None,
            }
            if count >= request.min_bin_events:
                quantiles[b.index] = q
        eligible = np.all(np.isfinite(quantiles), axis=1)
        baseline = {
            "eligible_bins": int(eligible.sum()),
            "transform": spec.kind if request.use_transforms else "none",
            "quantiles": {},
        }
        if eligible.sum() >= 8:
            reference = max(float(np.max(np.abs(quantiles[eligible]))), 1e-300)
            spread = float(
                np.median(quantiles[eligible, 2] / reference - quantiles[eligible, 0] / reference)
            )
            minimum_shift = (request.signal_min_shift * spread) * reference
            max_scores = np.full(len(bins), np.nan)
            for column, label in enumerate(["p10", "median", "p90"]):
                scores, center, scale = robust_scores(
                    quantiles[:, column], minimum_shift, request.score_threshold
                )
                baseline["quantiles"][label] = {"center": center, "scale": scale}
                max_scores = np.fmax(max_scores, scores)
            for b, score in zip(bins, max_scores, strict=True):
                b.signals[name]["score"] = number(score)
                if score > request.score_threshold:
                    b.reasons.append(f"signal: {name}")
                    b.suggested = True
            medians = quantiles[eligible, 1]
            if len(np.unique(medians)) > 1:
                rho = float(spearmanr(np.flatnonzero(eligible), medians).statistic)
                baseline["order_correlation"] = number(rho)
                if abs(rho) >= 0.8 and abs(medians[-1] / reference - medians[0] / reference) > max(
                    spread * 0.5, 1e-12 / reference
                ):
                    warnings.append(
                        f"{name}: strong signal drift across acquisition. "
                        "A global baseline may miss gradual changes; review the trace."
                    )
        else:
            warnings.append(
                f"{name}: fewer than eight bins contain enough finite source events; "
                "signal outlier scoring is disabled."
            )
        diagnostics["signal_baselines"][name] = baseline

    progress("Checking raw detector saturation and pulse shape", 0.75)
    for name in request.saturation_channels:
        channel = next(
            c for c in sample.acquisition_channels if c.name == sample.aliases.get(name, name)
        )
        values = engine.column(workspace, sample, name, compensated=False)
        saturated = (
            population
            & np.isfinite(values)
            & (values >= channel.range * request.saturation_fraction)
        )
        flags[saturated] |= FLAGS["saturation"]
        diagnostics["saturation"].append(
            {
                "channel": name,
                "range": channel.range,
                "threshold": channel.range * request.saturation_fraction,
                "count": int(saturated.sum()),
            }
        )
    if request.saturation_channels and sample.source != "FCS":
        warnings.append(
            "Non-FCS channel ranges may be display estimates. Saturation flags "
            "are range threshold checks, not a confirmed instrument ceiling."
        )
    if request.pulse_area:
        area = engine.column(workspace, sample, request.pulse_area, compensated=False)
        height = engine.column(workspace, sample, request.pulse_height, compensated=False)
        positive = population & np.isfinite(area) & np.isfinite(height) & (area > 0) & (height > 0)
        ratios = np.full(n, np.nan)
        # Subtract logs instead of dividing: valid large finite values cannot overflow.
        ratios[positive] = np.log(area[positive]) - np.log(height[positive])
        scores, center, scale = robust_scores(ratios, 0.15, request.pulse_score)
        outliers = population & (~positive | (scores > request.pulse_score))
        flags[outliers] |= FLAGS["pulse"]
        ids = np.flatnonzero(population & np.isfinite(area) & np.isfinite(height))
        ids = ids[np.linspace(0, len(ids) - 1, min(2000, len(ids)), dtype=int)] if len(ids) else ids
        diagnostics["pulse"] = {
            "area": request.pulse_area,
            "height": request.pulse_height,
            "log_ratio_center": center,
            "log_ratio_scale": scale,
            "invalid_count": int((population & ~positive).sum()),
            "positive_count": int(positive.sum()),
            "outlier_count": int(outliers.sum()),
            "preview": {
                "event_ids": ids.tolist(),
                "area": area[ids].tolist(),
                "height": height[ids].tolist(),
                "outlier": outliers[ids].tolist(),
            },
        }
        warnings.append(
            "Pulse-ratio outliers are candidates for singlet review, not a definitive "
            "doublet classification. Biological size/shape changes can alter the ratio."
        )
        if positive.sum() < 100:
            warnings.append(
                "Fewer than 100 valid positive pulses; the pulse baseline is poorly supported."
            )
    flagged_bins = sum(b.population_count for b in bins if b.suggested)
    if population.any() and flagged_bins > 0.2 * population.sum():
        warnings.append(
            "Suggested intervals contain more than 20% of the source population. "
            "Check the reference baseline and biological changes before excluding them."
        )
    diagnostics["suggested_interval_events"] = flagged_bins
    result = QualityResult(
        id=identifier,
        request=request,
        created_at=now(),
        input_snapshot=snapshot,
        input_hash=fingerprint(snapshot),
        data=QualityData(
            event_count=n,
            population_count=int(population.sum()),
            sha256="0" * 64,
            flag_counts={name: int(((flags & bit) != 0).sum()) for name, bit in FLAGS.items()},
        ),
        bins=bins,
        diagnostics=diagnostics,
        warnings=warnings,
        versions={
            "cytoforge_qc": METHOD_VERSION,
            "numpy": version("numpy"),
            "scipy": version("scipy"),
        },
        duration_seconds=time.monotonic() - started,
    )
    return result, np.column_stack([flags, bin_ids]).astype(np.uint32, copy=False)


def validate_data(result: QualityResult, values):
    if values.shape != (result.data.event_count, 2) or values.dtype != np.dtype("uint32"):
        raise ValueError("QC data must retain two uint32 values per original event")
    flags = values[:, 0]
    inside = (flags & OUTSIDE) == 0
    if np.any(flags > 31) or int(inside.sum()) != result.data.population_count:
        raise ValueError("QC flags do not match their recorded source population")
    for name, bit in FLAGS.items():
        if int(((flags & bit) != 0).sum()) != result.data.flag_counts.get(name, 0):
            raise ValueError("QC flag counts failed validation")
        if np.any(((flags & bit) != 0) & ~inside):
            raise ValueError("QC event flags must be limited to the original source population")
    for b in result.bins:
        if (
            np.any(values[b.start : b.end, 1] != b.index)
            or int(inside[b.start : b.end].sum()) != b.population_count
        ):
            raise ValueError("QC bin identities/counts failed validation")


def is_captured_gate(gate, result):
    """A captured gate retains reviewed flags independently of later compensation."""
    if gate.provenance.get("qc_selection_basis") != "captured_reviewed_event_flags":
        return False
    if (
        gate.provenance.get("qc_input_hash") != result.input_hash
        or gate.provenance.get("qc_data_sha256") != result.data.sha256
    ):
        raise ValueError(
            "Captured QC event flags changed; restore the captured data or review QC again"
        )
    return True


def load_data(store: Store, workspace_id: str, result: QualityResult):
    path = store.quality_path(workspace_id, result.id)
    try:
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != result.data.sha256:
            raise ValueError("QC event data failed its integrity check")
        values = np.load(path, mmap_mode="r", allow_pickle=False)
        validate_data(result, values)
        return values
    except OSError as exc:
        raise ValueError(
            "QC event data is missing or unreadable. Restore its project archive."
        ) from exc


def selection(values, excluded_bins, exclusions, keep=True):
    if set(exclusions) - FLAGS.keys():
        raise ValueError("Unknown QC event exclusion")
    inside = (values[:, 0] & OUTSIDE) == 0
    bits = sum(FLAGS[name] for name in exclusions)
    rejected = ((values[:, 0] & bits) != 0) | np.isin(values[:, 1], excluded_bins)
    return inside & (~rejected if keep else rejected)


def run_quality(job_dir: str):
    directory = Path(job_dir)
    stop = threading.Event()
    store = None

    def progress(stage, fraction):
        atomic_json(directory / "progress.json", {"stage": stage, "progress": fraction})

    try:
        payload = json.loads((directory / "input.json").read_text())
        threading.Thread(
            target=watch_parent, args=(payload["parent_pid"], stop), daemon=True
        ).start()
        workspace = Workspace.model_validate(payload["workspace"])
        request = QualityRequest.model_validate(payload["request"])
        store = Store(Path(payload["data_dir"]))
        sample = next(s for s in workspace.samples if s.id == request.sample_id)
        progress("Verifying original acquired events", 0.02)
        with store.data_path(workspace.id, sample.id).open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != sample.sha256:
            raise ValueError("Acquired event data failed its integrity check")
        result, values = calculate(workspace, request, Engine(store), payload["id"], progress)
        if result.input_hash != payload["input_hash"]:
            raise ValueError("QC inputs do not match their saved scientific fingerprint")
        progress("Saving exact event flags", 0.95)
        result.data.sha256 = save_array(store.quality_path(workspace.id, result.id), values)
        validate_data(result, values)
        atomic_json(directory / "result.json", result.model_dump())
        progress("Ready for interval review", 1)
    except Exception as exc:
        atomic_json(directory / "error.json", {"error": f"{type(exc).__name__}: {exc}"})
    finally:
        stop.set()
        if store:
            store.close()
