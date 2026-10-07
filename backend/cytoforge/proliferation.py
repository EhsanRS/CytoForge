"""Dye-dilution generation mixtures with explicit controls and precursor weighting.

Lognormal dye or Gaussian dye is diluted by a shared ratio. Independent normal
autofluorescence adds a fixed mean and variance. Counts use bin-integrated CDFs;
per-event posterior probabilities preserve acquired identities. See PROLIFERATION.md.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from scipy.optimize import least_squares, nnls
from scipy.special import ndtr, roots_legendre

from .analysis import _sample_signature, atomic_json, watch_parent
from .biology import histogram_figure, load_probability_data, output_columns
from .cellcycle import fingerprint, goodness, limits, poisson_residual
from .models import (
    ProliferationData,
    ProliferationFit,
    ProliferationRequest,
    ProliferationResult,
    Workspace,
    proliferation_statistics,
)
from .science import Engine, save_array
from .store import Store, now

METHOD_VERSION = "dye-dilution-1"


def sources(request):
    return [
        *request.inputs,
        *[v for v in (request.undivided_control, request.autofluorescence_control) if v],
    ]


def validate_request(workspace: Workspace, request: ProliferationRequest):
    for source in sources(request):
        sample = next((s for s in workspace.samples if s.id == source.sample_id), None)
        if sample is None or request.channel not in {c.name for c in sample.channels}:
            raise ValueError("The dilution-dye parameter must exist in every input and control")
        if sample.event_count >= 2**32:
            raise ValueError("Proliferation event identity supports fewer than 2^32 events")
        if source.gate_id and not any(
            g.id == source.gate_id and g.sample_id == sample.id for g in workspace.gates
        ):
            raise ValueError("Proliferation populations must belong to their samples")


def input_snapshot(workspace, request):
    validate_request(workspace, request)
    basis = SimpleNamespace(channels=[request.channel], use_transforms=False)
    scientific = [_sample_signature(workspace, basis, source) for source in sources(request)]
    if not request.compensated:
        for signature in scientific:
            if not signature["gates"]:
                signature["compensation"] = None
    return {
        "method": METHOD_VERSION,
        "settings": request.model_dump(
            exclude={"revision", "name", "create_generation_gates", "replace_result_id"}
        ),
        "scientific_input": scientific,
    }


def input_hash(workspace, request):
    return fingerprint(input_snapshot(workspace, request))


def is_stale(workspace, result):
    try:
        return input_hash(workspace, result.request) != result.input_hash
    except (ValueError, KeyError, StopIteration):
        return True


@lru_cache(maxsize=4)
def normal_quadrature(order=512):
    nodes, weights = roots_legendre(order)
    nodes, weights = nodes * 9, weights * 9
    return nodes, weights * np.exp(-(nodes**2) / 2) / math.sqrt(2 * math.pi)


def normal_masses(z):
    """Stable Gaussian bin integrals on either tail."""
    lo, hi = z[..., :-1], z[..., 1:]
    return np.maximum(np.where(lo > 0, ndtr(-lo) - ndtr(-hi), ndtr(hi) - ndtr(lo)), 0)


def lognormal_masses(edges, median, sigma):
    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.where(
            edges > 0, (np.log(np.maximum(edges, 1e-300)) - np.log(median)) / sigma, -np.inf
        )
    return normal_masses(z)


def generation_components(
    edges, mean, ratio, cv, background, background_sd, count, distribution="lognormal", order=512
):
    """Return conditional bin masses and untruncated coverage for each generation.

    Quadrature integrates the narrower random variable to avoid a near-step CDF
    when a late-generation dye peak is much narrower than background noise.
    """
    signal = (mean - background) * ratio ** np.arange(count)
    if np.any(signal <= 0):
        raise ValueError("Generation-zero intensity must exceed autofluorescence")
    components = []
    sigma_log = math.sqrt(math.log1p((cv / 100) ** 2))
    nodes, weights = normal_quadrature(order)
    for median in signal:
        if distribution == "gaussian":
            sd = math.hypot(median * cv / 100, background_sd)
            mass = normal_masses((edges - background - median) / sd)
        elif background_sd == 0:
            mass = lognormal_masses(edges - background, median, sigma_log)
        elif median * math.exp(sigma_log**2 / 2) * cv / 100 <= background_sd:
            dye = median * np.exp(sigma_log * nodes)
            z = (edges[None, :] - background - dye[:, None]) / background_sd
            mass = weights @ normal_masses(z)
        else:
            shifted = edges[None, :] - (background + background_sd * nodes[:, None])
            mass = weights @ lognormal_masses(shifted, median, sigma_log)
        components.append(mass)
    components = np.asarray(components)
    coverage = components.sum(axis=1)
    if np.any(coverage < 1e-14):
        raise ValueError(
            "The fit range excludes a modeled generation. Widen it or reduce the last generation."
        )
    return components / coverage[:, None], coverage


def _population(workspace, request, source, engine):
    sample = engine.sample(workspace, source.sample_id)
    values = engine.column(workspace, sample, request.channel, compensated=request.compensated)
    selected = engine.mask(workspace, sample, source.gate_id)
    finite = selected & np.isfinite(values)
    return sample, values, selected, finite


def _control_values(workspace, request, source, engine, undivided=False):
    sample, values, selected, finite = _population(workspace, request, source, engine)
    if undivided:
        if request.control_range_min is not None:
            finite &= values >= request.control_range_min
        if request.control_range_max is not None:
            finite &= values <= request.control_range_max
    subset = values[finite].astype(np.float64)
    if len(subset) < (200 if undivided else 50):
        control_name = "undivided" if undivided else "autofluorescence"
        raise ValueError(f"{sample.name}: the {control_name} control has too few finite events")
    return subset, {
        "sample_id": sample.id,
        "gate_id": source.gate_id,
        "population_count": int(selected.sum()),
        "calibration_count": len(subset),
        "nonfinite_count": int((selected & ~np.isfinite(values)).sum()),
    }


def calibration(workspace, request, engine, progress):
    report, warnings = {}, []
    background, background_sd = request.background, request.background_sd
    if request.autofluorescence_control:
        values, info = _control_values(workspace, request, request.autofluorescence_control, engine)
        scale = max(float(np.abs(values).max()), 1e-300)
        normalized = values / scale
        median = float(np.median(normalized))
        background = max(0, median) * scale
        background_sd = 1.482602218505602 * float(np.median(np.abs(normalized - median))) * scale
        if not math.isfinite(background_sd):
            raise ValueError("The autofluorescence control spread exceeds finite intensity limits")
        report["autofluorescence"] = dict(
            info,
            median=median * scale,
            background=background,
            background_sd=background_sd,
            estimator="Median and normal-consistent median absolute deviation",
        )
        warnings.append(
            "Autofluorescence is approximated by a normal distribution calibrated from "
            "the unstained control; review its population and spread."
        )
        if median < 0:
            warnings.append(
                "The compensated unstained control has a negative median. Its modeled "
                "background mean is fixed at zero; its spread is retained."
            )
    report.update(background=background, background_sd=background_sd)
    if request.undivided_control:
        progress("Calibrating the undivided control", 0.02)
        values, info = _control_values(workspace, request, request.undivided_control, engine, True)
        reference = max(float(np.quantile(values, 0.5)), background, 1e-300)
        if reference <= background:
            raise ValueError(
                "The undivided control must have a resolved dye signal above autofluorescence"
            )
        values, bg, bsd = values / reference, background / reference, background_sd / reference
        median = float(np.median(values))
        spread = 1.482602218505602 * float(np.median(np.abs(values - median)))
        cv = float(
            np.clip(math.sqrt(max(spread**2 - bsd**2, 1e-6)) / (median - bg) * 100, 0.5, 100)
        )
        if request.distribution == "lognormal" and bsd == 0:
            positive = values[values > bg] - bg
            if len(positive) < 200:
                raise ValueError(
                    "The undivided control must have at least 200 positive dye signals"
                )
            logs = np.log(positive)
            sigma = 1.482602218505602 * float(np.median(np.abs(logs - np.median(logs))))
            cv = float(np.clip(math.sqrt(math.expm1(min(sigma**2, math.log(2)))) * 100, 0.5, 100))
        lo, hi = np.quantile(values, [0.0005, 0.9995])
        lo, hi = float(lo - max(spread, 0.01)), float(hi + max(spread, 0.01))
        if not lo < hi:
            raise ValueError("The undivided control has no measurable intensity spread")
        edges = np.linspace(lo, hi, 257)
        counts, _ = np.histogram(values, edges)
        if np.count_nonzero(counts) < 8:
            raise ValueError(
                "The undivided control requires at least eight occupied histogram bins"
            )
        signal = median - bg

        def evaluate(v):
            basis, coverage = generation_components(
                edges, bg + math.exp(v[0]), 0.5, math.exp(v[1]), bg, bsd, 1, request.distribution
            )
            return basis[0] * counts.sum(), coverage[0]

        def residual(v):
            predicted, _ = evaluate(v)
            return poisson_residual(predicted, counts)

        solution = least_squares(
            residual,
            np.log([signal, cv]),
            bounds=(np.log([signal * 0.25, 0.5]), np.log([signal * 4, 100])),
            max_nfev=300,
            ftol=1e-8,
            xtol=1e-8,
            gtol=1e-8,
        )
        mean = (bg + math.exp(solution.x[0])) * reference
        cv = float(np.clip(math.exp(solution.x[1]), 0.5, 100))
        predicted, coverage = evaluate(solution.x)
        diagnostics = goodness(counts, predicted[None, :], 2)
        diagnostics.pop("residuals")
        report["undivided"] = dict(
            info,
            undivided_mean=mean,
            dye_cv=cv,
            converged=bool(solution.success),
            captured_mass=float(coverage),
            diagnostics=diagnostics,
            estimator=f"Single-generation {request.distribution} dye + normal background; "
            "bin-integrated Poisson fit",
        )
        if not solution.success or diagnostics["normalized_rmsd"] > 0.08 or cv > 90:
            warnings.append(
                "The undivided control is broad or poorly described by one generation. "
                "Review its gate and calibration range before interpreting generation labels."
            )
        report.update(undivided_mean=mean, dye_cv=cv)
    return report, warnings


class PeakParameters:
    def __init__(self, request, calibrated):
        anchor = (
            calibrated.get("undivided_mean")
            or request.undivided_mean.fixed
            or request.undivided_mean.initial
        )
        self.scale = anchor
        self.background = calibrated["background"] / self.scale
        self.background_sd = calibrated["background_sd"] / self.scale
        if not math.isfinite(self.background_sd) or self.background >= 1:
            raise ValueError("Generation-zero intensity must exceed autofluorescence")
        self.names, self.initial, self.lower, self.upper, self.fixed = [], [], [], [], {}
        if request.undivided_control and request.control_mode != "initial":
            mean_bounds, mean_guess = (1, 1), 1
        else:
            spec = request.undivided_mean
            mean_guess = (spec.initial or spec.fixed or anchor) / self.scale
            signal = mean_guess - self.background
            mean_bounds = limits(
                spec, self.background + signal * 0.75, self.background + signal * 1.33, self.scale
            )
        if mean_bounds[0] <= self.background:
            raise ValueError("Generation-zero constraints must stay above autofluorescence")
        self.add("undivided_mean", mean_guess, *mean_bounds)
        ratio = request.peak_ratio.initial or request.peak_ratio.fixed or 0.5
        self.add(
            "peak_ratio",
            ratio,
            *limits(
                request.peak_ratio,
                0.25 if request.peak_ratio.fixed else 0.45,
                0.75 if request.peak_ratio.fixed else 0.55,
            ),
        )
        cv = request.dye_cv.initial or request.dye_cv.fixed or calibrated.get("dye_cv", 25)
        cv_bounds = (
            (cv, cv)
            if request.undivided_control and request.control_mode == "fix_mean_cv"
            else limits(
                request.dye_cv,
                0.5 if request.dye_cv.fixed else 5,
                100 if request.dye_cv.fixed else 80,
            )
        )
        if not 0.5 <= cv <= 100:
            raise ValueError(
                "The control-calibrated dye CV is outside the supported 0.5–100% range"
            )
        self.add("dye_cv", cv, *cv_bounds)

    def add(self, name, initial, lower, upper):
        if lower > upper or not math.isfinite(lower + upper):
            raise ValueError("Peak parameter constraints have no finite feasible solution")
        if lower == upper:
            self.fixed[name] = lower
            return
        self.names.append(name)
        margin = (upper - lower) * 1e-6
        self.initial.append(float(np.clip(initial, lower + margin, upper - margin)))
        self.lower.append(lower)
        self.upper.append(upper)

    def decode(self, vector):
        return self.fixed | dict(zip(self.names, vector, strict=True))


def stick_fractions(values, count):
    fractions, remaining = [], 1.0
    for value in values:
        fractions.append(remaining * value)
        remaining *= 1 - value
    fractions.append(remaining)
    return np.array(fractions[:count])


def stick_initial(fractions):
    remaining, values = 1.0, []
    for fraction in fractions[:-1]:
        values.append(float(np.clip(fraction / max(remaining, 1e-12), 0.002, 0.998)))
        remaining -= fraction
    return values


def fit_histogram(edges, counts, parameters, request, progress):
    n_peak, k = len(parameters.names), request.generations + 1
    p = parameters.decode(parameters.initial)

    @lru_cache(maxsize=4)
    def peak_basis(mean, ratio, cv):
        # Fraction updates and their numerical derivatives share the same peaks.
        # Cache their integrated shapes, including the fully control-fixed case.
        return generation_components(
            edges,
            mean,
            ratio,
            cv,
            parameters.background,
            parameters.background_sd,
            k,
            request.distribution,
        )

    basis, _ = peak_basis(p["undivided_mean"], p["peak_ratio"], p["dye_cv"])
    initial_counts, _ = nnls(basis.T, counts.astype(float))
    fractions = initial_counts / initial_counts.sum() if initial_counts.sum() else np.full(k, 1 / k)
    initial = np.array([*parameters.initial, *stick_initial(fractions)])
    lower, upper = [*parameters.lower, *([0] * (k - 1))], [*parameters.upper, *([1] * (k - 1))]
    total = int(counts.sum())

    def evaluate(v):
        p = parameters.decode(v[:n_peak])
        fractions = stick_fractions(v[n_peak:], k)
        basis, coverage = peak_basis(p["undivided_mean"], p["peak_ratio"], p["dye_cv"])
        return total * fractions[:, None] * basis, fractions, p, coverage

    def residual(v):
        components, *_ = evaluate(v)
        predicted = components.sum(axis=0)
        return (
            (predicted - counts) / np.sqrt(np.maximum(counts, 1))
            if request.objective == "weighted_least_squares"
            else poisson_residual(predicted, counts)
        )

    if not len(initial):
        components, fractions, p, coverage = evaluate(initial)
        diagnostics = goodness(counts, components, 0)
        diagnostics.update(
            converged=True,
            attempts=[],
            parameters_at_bounds=[],
            jacobian_rank=0,
            captured_component_mass=coverage.tolist(),
            fraction_standard_errors=[0.0],
            metric_standard_errors=None,
            uncertainty_basis="No fitted parameters; all peak parameters fixed",
        )
        return components, fractions, p, diagnostics
    best, attempts = None, []
    for attempt in range(3):
        candidate = initial.copy()
        if attempt:
            if "dye_cv" in parameters.names:
                index = parameters.names.index("dye_cv")
                candidate[index] *= [0.75, 1.3][attempt - 1]
            if "undivided_mean" in parameters.names:
                index = parameters.names.index("undivided_mean")
                candidate[index] *= [0.96, 1.04][attempt - 1]
            candidate[n_peak:] = stick_initial(np.full(k, 1 / k))
        margin = (np.asarray(upper) - lower) * 1e-6
        candidate = np.clip(candidate, np.asarray(lower) + margin, np.asarray(upper) - margin)
        progress(f"Optimizing generation mixture ({attempt + 1}/3)", 0.18 + attempt * 0.2)
        solution = least_squares(
            residual,
            candidate,
            bounds=(lower, upper),
            max_nfev=request.maximum_evaluations,
            ftol=1e-8,
            xtol=1e-8,
            gtol=1e-8,
            x_scale="jac",
        )
        attempts.append(
            {
                "objective": float(2 * solution.cost),
                "evaluations": solution.nfev,
                "converged": bool(solution.success),
            }
        )
        if best is None or solution.cost < best.cost:
            best = solution
    components, fractions, p, coverage = evaluate(best.x)
    # Numerical optimization can approach an absent component without reaching
    # exactly zero. Less than one millionth of one modeled event is reported zero.
    absent = fractions * total < 1e-6
    if np.any(absent):
        fractions[absent] = 0
        fractions /= fractions.sum()
        basis, coverage = peak_basis(p["undivided_mean"], p["peak_ratio"], p["dye_cv"])
        components = total * fractions[:, None] * basis
    _, singular, right = np.linalg.svd(best.jac, full_matrices=False)
    rank = int((singular > max(singular[0] * 1e-8, 1e-10)).sum()) if len(singular) else 0
    at_bounds = [
        i
        for i, (v, lo, hi) in enumerate(zip(best.x, lower, upper, strict=True))
        if min(v - lo, hi - v) < 1e-4 * (hi - lo)
    ]
    diagnostics = goodness(counts, components, len(best.x))
    diagnostics.update(
        converged=bool(best.success),
        attempts=attempts,
        optimizer_message=best.message,
        captured_component_mass=coverage.tolist(),
        jacobian_rank=rank,
        parameters_at_bounds=at_bounds,
        parameter_names=[
            *parameters.names,
            *[f"generation_{i}_stick_fraction" for i in range(k - 1)],
        ],
        fraction_standard_errors=None,
        metric_standard_errors=None,
        uncertainty_basis=(
            "Local Hessian approximation conditional on fixed controls, generation count "
            "and fit range; control calibration uncertainty is excluded"
        ),
    )
    if best.success and rank == len(best.x) and not at_bounds and not np.any(absent):
        covariance = (right.T / singular**2) @ right
        if request.objective == "weighted_least_squares":
            covariance *= 2 * best.cost / max(len(counts) - len(best.x) - 1, 1)
        derivatives = np.zeros((k, len(best.x)))
        metric_keys = [
            "precursor_frequency",
            "division_index",
            "proliferation_index",
            "expansion_index",
            "replication_index",
        ]
        metric_derivatives = np.zeros((len(metric_keys), len(best.x)))
        for i in range(len(best.x)):
            step = min(
                1e-5 * max(abs(best.x[i]), 1),
                (best.x[i] - lower[i]) / 2,
                (upper[i] - best.x[i]) / 2,
            )
            left, right_vector = best.x.copy(), best.x.copy()
            left[i] -= step
            right_vector[i] += step
            fl = evaluate(left)[1]
            fr = evaluate(right_vector)[1]
            derivatives[:, i] = (fr - fl) / (2 * step)
            ml, mr = proliferation_statistics(fl * total), proliferation_statistics(fr * total)
            metric_derivatives[:, i] = [
                (getattr(mr, key) - getattr(ml, key)) / (2 * step) for key in metric_keys
            ]
        diagnostics["fraction_standard_errors"] = np.sqrt(
            np.maximum(np.diag(derivatives @ covariance @ derivatives.T), 0)
        ).tolist()
        diagnostics["metric_standard_errors"] = dict(
            zip(
                metric_keys,
                np.sqrt(
                    np.maximum(np.diag(metric_derivatives @ covariance @ metric_derivatives.T), 0)
                ).tolist(),
                strict=True,
            )
        )
    return components, fractions, p, diagnostics


def fit_sample(workspace, request, source, engine, calibrated, progress):
    sample, values, selected, finite = _population(workspace, request, source, engine)
    parameters = PeakParameters(request, calibrated)
    positive = finite & (values > 0) if request.histogram_space == "log2" else finite
    subset = values[positive].astype(np.float64) / parameters.scale
    if len(subset) < 200:
        raise ValueError(
            f"{sample.name}: at least 200 finite source events are required in the histogram domain"
        )
    if not np.all(np.isfinite(subset)) or np.max(np.abs(subset)) > 1e100:
        raise ValueError(
            "Intensity spans too far from generation zero for a stable fit; restrict the "
            "source or fit range"
        )
    p = parameters.decode(parameters.initial)
    k = request.generations + 1
    signal = (p["undivided_mean"] - parameters.background) * p["peak_ratio"] ** np.arange(k)
    bg, bsd = parameters.background, parameters.background_sd
    if request.distribution == "lognormal":
        sigma = math.sqrt(math.log1p((p["dye_cv"] / 100) ** 2))
        model_low = bg + signal[-1] * math.exp(-5 * sigma) - 5 * bsd
        model_high = bg + signal[0] * math.exp(5 * sigma) + 5 * bsd
    else:
        model_low = bg + signal[-1] - 5 * math.hypot(signal[-1] * p["dye_cv"] / 100, bsd)
        model_high = bg + signal[0] + 5 * math.hypot(signal[0] * p["dye_cv"] / 100, bsd)
    lo, hi = np.quantile(subset, [0.0001, 0.9999])
    lo, hi = min(float(lo), model_low), max(float(hi), model_high)
    if request.histogram_space == "log2":
        lo = max(
            min(float(np.quantile(subset, 0.0001)) * 0.8, max(model_low, signal[-1] * 0.001)),
            1e-100,
        )
    if request.range_min is not None:
        lo = request.range_min / parameters.scale
    if request.range_max is not None:
        hi = request.range_max / parameters.scale
    if not math.isfinite(lo + hi) or not lo < hi or (request.histogram_space == "log2" and lo <= 0):
        raise ValueError("The proliferation fit range must be finite and increasing")
    if request.histogram_space == "log2":
        scaled_edges = np.geomspace(lo, hi, request.bins + 1)
    else:
        scaled_edges = np.linspace(lo, hi, request.bins + 1)
    with np.errstate(over="ignore"):
        edges = scaled_edges * parameters.scale
    if not np.all(np.isfinite(edges)) or np.any(np.diff(edges) <= 0):
        raise ValueError(
            "The histogram edges exceed finite intensity precision; choose a narrower range"
        )
    fitted = positive & (values >= edges[0]) & (values <= edges[-1])
    indices = np.flatnonzero(fitted)
    if len(indices) < 200:
        raise ValueError(f"{sample.name}: at least 200 source events must lie inside the fit range")
    counts, _ = np.histogram(values[indices], edges)
    if np.count_nonzero(counts) < 8:
        raise ValueError("Proliferation requires at least eight occupied histogram bins")
    components, fractions, p, diagnostics = fit_histogram(
        scaled_edges, counts, parameters, request, progress
    )
    total = components.sum(axis=0)
    if np.any((counts > 0) & (total == 0)):
        raise ValueError(
            "Observed events lie beyond all modeled generations. Review the range, CV "
            "and last generation."
        )
    weights = np.divide(
        components, total[None, :], out=np.full_like(components, 1 / k), where=total[None, :] > 0
    )
    outputs = np.full((sample.event_count, k + 1), np.nan, dtype=np.float32)
    bins = np.minimum(np.searchsorted(edges, values[indices], side="right") - 1, request.bins - 1)
    outputs[indices, :k] = weights[:, bins].T
    outputs[indices, k] = np.argmax(outputs[indices, :k], axis=1)
    expected = outputs[indices, :k].sum(axis=0, dtype=np.float64)
    assigned = np.bincount(outputs[indices, k].astype(int), minlength=k)
    overlap = []
    basis, _ = generation_components(
        scaled_edges,
        p["undivided_mean"],
        p["peak_ratio"],
        p["dye_cv"],
        bg,
        bsd,
        k,
        request.distribution,
    )
    for i in range(k - 1):
        overlap.append(float(np.sqrt(basis[i] * basis[i + 1]).sum()))
    diagnostics.update(
        excluded_nonfinite=int((selected & ~np.isfinite(values)).sum()),
        excluded_nonpositive=int((finite & (values <= 0)).sum())
        if request.histogram_space == "log2"
        else 0,
        excluded_below_range=int((positive & (values < edges[0])).sum()),
        excluded_above_range=int((positive & (values > edges[-1])).sum()),
        adjacent_generation_overlap=overlap,
        expected_fraction=(expected / len(indices)).tolist(),
        objective=request.objective,
        percentage_basis="Conditional on finite source events inside the fit range",
        parameter_basis="Generation-zero center = background + median dye for lognormal "
        "(mean dye for Gaussian); ratio dilutes dye above background; "
        "CV describes dye variability before background convolution",
        posterior_basis=(
            "Bin-integrated generation probability at each original event's histogram bin"
        ),
    )
    warnings = []
    if not diagnostics["converged"]:
        warnings.append(
            "The optimizer did not converge. Review or refit before interpreting "
            "generation statistics."
        )
    if diagnostics["fraction_standard_errors"] is None:
        warnings.append(
            "Local uncertainty is unavailable because the solution is at a bound or the "
            "mixture is not identifiable. Review constraints and generation count."
        )
    if diagnostics["normalized_rmsd"] > 0.08:
        warnings.append(
            "Large model residuals suggest an unsuitable population, peak count or dye model."
        )
    if min(diagnostics["captured_component_mass"]) < 0.98:
        warnings.append(
            "The fit range truncates more than 2% of a modeled generation. Fractions and "
            "precursor statistics are conditional on this range."
        )
    if overlap and max(overlap) > 0.85:
        warnings.append(
            "Adjacent generations overlap strongly. Individual late-generation counts "
            "can be poorly resolved even when the total curve fits well."
        )
    if diagnostics["excluded_nonpositive"]:
        warnings.append(
            "Nonpositive fluorescence is excluded by the logarithmic histogram. A linear "
            "histogram can retain these events when background noise is modeled."
        )
    if k > 1 and fractions[0] < 0.01:
        warnings.append(
            "Generation zero is nearly absent. Its label relies on the supplied control "
            "or manual anchor; peak order alone does not establish division number."
        )
    actual = dict(
        p,
        undivided_mean=p["undivided_mean"] * parameters.scale,
        background=bg * parameters.scale,
        background_sd=bsd * parameters.scale,
    )
    peaks = [
        actual["background"]
        + (actual["undivided_mean"] - actual["background"]) * actual["peak_ratio"] ** i
        for i in range(k)
    ]
    result = ProliferationFit(
        sample_id=sample.id,
        data=ProliferationData(
            sample_id=sample.id,
            event_count=sample.event_count,
            population_count=int(selected.sum()),
            finite_count=int(finite.sum()),
            fitted_count=len(indices),
            sha256="0" * 64,
        ),
        range_min=float(edges[0]),
        range_max=float(edges[-1]),
        edges=edges.tolist(),
        observed=counts.tolist(),
        components=components.tolist(),
        weights=weights.tolist(),
        parameters=actual,
        peak_locations=peaks,
        fractions=fractions.tolist(),
        expected_counts=expected.tolist(),
        assigned_counts=assigned.tolist(),
        statistics=proliferation_statistics(fractions * len(indices)),
        diagnostics=diagnostics,
        warnings=warnings,
    )
    return result, outputs


def calculate(workspace, request, engine, identifier, progress=lambda stage, fraction: None):
    started = time.monotonic()
    snapshot = input_snapshot(workspace, request)
    calibrated, warnings = calibration(workspace, request, engine, progress)
    fits, arrays = [], {}
    for i, source in enumerate(request.inputs):
        sample = engine.sample(workspace, source.sample_id)

        def stage(message, fraction, i=i, sample=sample):
            progress(
                f"{sample.name}: {message}", 0.05 + 0.95 * (i + fraction) / len(request.inputs)
            )

        stage("Building the full-event dye histogram", 0.03)
        fit, outputs = fit_sample(workspace, request, source, engine, calibrated, stage)
        fits.append(fit)
        arrays[sample.id] = outputs
    return ProliferationResult(
        id=identifier,
        request=request,
        created_at=now(),
        input_hash=fingerprint(snapshot),
        input_snapshot=snapshot,
        columns=output_columns(
            workspace,
            request,
            [
                *[f"{request.name} G{i} probability" for i in range(request.generations + 1)],
                f"{request.name} generation",
            ],
        ),
        fits=fits,
        calibration=calibrated,
        versions={p: version(p) for p in ("numpy", "scipy")},
        warnings=[
            *warnings,
            (
                "Generation labeling requires a correctly identified undivided reference. "
                "Precursor statistics assume binary division and do not correct for "
                "selective death, dye loss or sampling bias."
            ),
            (
                "Review singlets, viability, control calibration, residuals and generation "
                "overlap before using the fitted statistics. Model counts, posterior "
                "expected counts and hard-assigned events are distinct quantities."
            ),
        ],
        duration_seconds=time.monotonic() - started,
    ), arrays


def load_data(store, workspace_id, result, data):
    return load_probability_data(store, workspace_id, result, data, "Proliferation")


def figure_svg(result, fit, sample_name, stale=False):
    p, s = fit.parameters, fit.statistics

    def number(v):
        return "undefined" if v is None else f"{v:.4f}"

    return histogram_figure(
        fit,
        f"{sample_name} · {result.request.name}",
        f"{result.request.distribution.title()} dye + normal background · "
        f"Generations 0–{result.request.generations} · {fit.data.fitted_count:,} fitted events",
        result.request.channel,
        [f"G{i}" for i in range(len(fit.fractions))],
        [
            f"Dye CV {p['dye_cv']:.2f}% · Peak ratio {p['peak_ratio']:.5f} · "
            f"Generation zero {p['undivided_mean']:.6g} · "
            f"Background {p['background']:.6g} ± {p['background_sd']:.4g} SD",
            f"Precursor frequency {s.precursor_frequency * 100:.2f}% · "
            f"Division index {s.division_index:.4f} · "
            f"Proliferation index {number(s.proliferation_index)}",
            f"Expansion index {s.expansion_index:.4f} · "
            f"Replication index {number(s.replication_index)} · "
            f"RMSD {fit.diagnostics['rmsd_events_per_bin']:.3f} events/bin",
            (
                "Precursor weighting assumes binary division; selective survival and dye "
                "loss are not corrected."
            ),
        ],
        result.request.histogram_space == "log2",
        stale,
    )


def run_proliferation(directory):
    directory, stop, store = Path(directory), threading.Event(), None
    try:
        payload = json.loads((directory / "input.json").read_text())
        threading.Thread(
            target=watch_parent, args=(payload["parent_pid"], stop), daemon=True
        ).start()
        workspace, request = (
            Workspace.model_validate(payload["workspace"]),
            ProliferationRequest.model_validate(payload["request"]),
        )
        if input_hash(workspace, request) != payload["input_hash"]:
            raise ValueError("Proliferation inputs changed before the worker started")
        store = Store(Path(payload["data_dir"]))
        for sample_id in {v.sample_id for v in sources(request)}:
            sample = next(s for s in workspace.samples if s.id == sample_id)
            with store.data_path(workspace.id, sample.id).open("rb") as handle:
                if hashlib.file_digest(handle, "sha256").hexdigest() != sample.sha256:
                    raise ValueError("Acquired events failed their integrity check")

        def progress(stage, fraction):
            atomic_json(directory / "progress.json", {"stage": stage, "progress": fraction})

        result, arrays = calculate(workspace, request, Engine(store), payload["id"], progress)
        for data in result.data:
            data.sha256 = save_array(
                store.analysis_path(workspace.id, result.id, data.sample_id), arrays[data.sample_id]
            )
            load_data(store, workspace.id, result, data)
        atomic_json(directory / "result.json", result.model_dump())
    except Exception as exc:
        atomic_json(directory / "error.json", {"error": str(exc) or type(exc).__name__})
    finally:
        stop.set()
        if store:
            store.close()
