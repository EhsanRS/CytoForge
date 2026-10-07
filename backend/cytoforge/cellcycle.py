"""Dean–Jett–Fox DNA modeling and an unavailable Watson refinement prototype.

Fits use linear intensity and all source events. Histogram probabilities retain
acquisition identities; model fractions and hard assignments are separate outputs.
Numerical choices, references and validation limits are in docs/CELL_CYCLE.md.
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
from scipy.ndimage import gaussian_filter1d
from scipy.optimize import least_squares
from scipy.signal import find_peaks
from scipy.special import ndtr, ndtri, softmax

from .analysis import _sample_signature, atomic_json, watch_parent
from .biology import output_columns
from .fileio import load_validated_array
from .models import (
    CellCycleData,
    CellCycleFit,
    CellCycleRequest,
    CellCycleResult,
    Workspace,
)
from .science import Engine, save_array
from .store import Store, now

METHOD_VERSION = "dna-models-1"
PHASES = ["G0/G1", "S", "G2/M"]


def validate_request(workspace: Workspace, request: CellCycleRequest):
    if request.method == "watson":
        raise ValueError(
            "Watson refinement is still under scientific validation. "
            "Use the validated DJF model for this fit."
        )
    for source in request.inputs:
        sample = next((s for s in workspace.samples if s.id == source.sample_id), None)
        if sample is None or request.channel not in {c.name for c in sample.channels}:
            raise ValueError("The DNA parameter must exist in every selected sample")
        if sample.event_count >= 2**32:
            raise ValueError("Cell-cycle event identity supports fewer than 2^32 events")
        if source.gate_id and not any(
            g.id == source.gate_id and g.sample_id == sample.id for g in workspace.gates
        ):
            raise ValueError("Cell-cycle source population must belong to its sample")


def input_snapshot(workspace: Workspace, request: CellCycleRequest):
    validate_request(workspace, request)
    basis = SimpleNamespace(channels=[request.channel], use_transforms=False)
    scientific = [_sample_signature(workspace, basis, source) for source in request.inputs]
    # A raw DNA fit still depends on compensation in transformed source gates.
    # Ignore a matrix only when no source gate depends on the sample matrix.
    if not request.compensated:
        for signature in scientific:
            if not signature["gates"]:
                signature["compensation"] = None
    return {
        "method": METHOD_VERSION,
        "settings": request.model_dump(
            exclude={"revision", "name", "create_phase_gates", "replace_result_id"}
        ),
        "scientific_input": scientific,
    }


def fingerprint(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


def input_hash(workspace, request):
    return fingerprint(input_snapshot(workspace, request))


def is_stale(workspace, result):
    try:
        return input_hash(workspace, result.request) != result.input_hash
    except (ValueError, KeyError, StopIteration):
        return True


def limits(spec, lower, upper, scale=1):
    if spec.fixed is not None:
        fixed = spec.fixed / scale
        if not lower <= fixed <= upper:
            raise ValueError("A fixed peak parameter lies outside the fit range or allowed limits")
        return fixed, fixed
    lower = spec.minimum / scale if spec.minimum is not None else lower
    upper = spec.maximum / scale if spec.maximum is not None else upper
    if not lower <= upper:
        raise ValueError("Peak parameter constraints have no feasible solution")
    return lower, upper


class PeakParameters:
    """Feasible parameterization of coupled means, peak ratio and linked CVs."""

    def __init__(self, request, scale, edges, initial):
        self.request, self.scale = request, scale
        width = edges[1] - edges[0]
        self.mean1 = limits(request.g1_mean, max(edges[0], width / 2), edges[-1], scale)
        self.mean2 = limits(request.g2_mean, max(edges[0], width / 2), edges[-1], scale)
        self.ratio = limits(
            request.peak_ratio,
            1.2 if request.peak_ratio.fixed else 1.5,
            3 if request.peak_ratio.fixed else 2.4,
        )
        self.cv1 = limits(
            request.g1_cv, 0.3 if request.g1_cv.fixed else 0.5, 40 if request.g1_cv.fixed else 20
        )
        self.cv2 = limits(
            request.g2_cv, 0.3 if request.g2_cv.fixed else 0.5, 40 if request.g2_cv.fixed else 20
        )
        # Explicit bounded means must still lie within the selected fit range.
        self.mean1 = (max(self.mean1[0], width / 2, edges[0]), min(self.mean1[1], edges[-1]))
        self.mean2 = (max(self.mean2[0], width / 2, edges[0]), min(self.mean2[1], edges[-1]))
        self.mean1 = (
            max(self.mean1[0], self.mean2[0] / self.ratio[1]),
            min(self.mean1[1], self.mean2[1] / self.ratio[0]),
        )
        if self.mean1[0] > self.mean1[1]:
            raise ValueError("Mean and G2/G1 ratio constraints have no feasible solution")
        self.names, self.initial, self.lower, self.upper = [], [], [], []
        guess1 = request.g1_mean.initial / scale if request.g1_mean.initial else initial[0]
        self.add("g1", guess1, *self.mean1)
        guess1 = float(np.clip(guess1, *self.mean1))
        rlo, rhi = self.ratio_limits(guess1)
        guess2 = request.g2_mean.initial / scale if request.g2_mean.initial else initial[1]
        ratio = request.peak_ratio.initial or guess2 / guess1
        if self.mean2[0] != self.mean2[1] and self.ratio[0] != self.ratio[1]:
            self.add("ratio_fraction", (ratio - rlo) / max(rhi - rlo, 1e-12), 0, 1)
        cv1 = request.g1_cv.initial or initial[2]
        cv2 = request.g2_cv.initial or initial[3]
        if request.linked_cv != "none":
            if (
                request.g1_cv.fixed is not None
                and request.g2_cv.minimum is None
                and request.g2_cv.maximum is None
                and request.g2_cv.fixed is None
            ):
                self.cv2 = self.cv1
            elif (
                request.g2_cv.fixed is not None
                and request.g1_cv.minimum is None
                and request.g1_cv.maximum is None
                and request.g1_cv.fixed is None
            ):
                self.cv1 = self.cv2
            self.shared_cv = max(self.cv1[0], self.cv2[0]), min(self.cv1[1], self.cv2[1])
            if self.shared_cv[0] > self.shared_cv[1]:
                raise ValueError("Linked CV constraints have no feasible solution")
            self.add("shared_cv", cv1 if request.linked_cv == "g2_to_g1" else cv2, *self.shared_cv)
        else:
            self.add("cv1", cv1, *self.cv1)
            self.add("cv2", cv2, *self.cv2)

    def add(self, name, initial, lower, upper):
        if lower > upper:
            raise ValueError("Peak parameter constraints have no feasible solution")
        if lower == upper:
            return
        self.names.append(name)
        margin = (upper - lower) * 1e-6
        self.initial.append(float(np.clip(initial, lower + margin, upper - margin)))
        self.lower.append(lower)
        self.upper.append(upper)

    def ratio_limits(self, mean1):
        return max(self.ratio[0], self.mean2[0] / mean1), min(self.ratio[1], self.mean2[1] / mean1)

    def decode(self, vector):
        values = dict(zip(self.names, vector, strict=False))
        mean1 = values.get("g1", self.mean1[0])
        rlo, rhi = self.ratio_limits(mean1)
        if self.mean2[0] == self.mean2[1]:
            mean2 = self.mean2[0]
        elif self.ratio[0] == self.ratio[1]:
            mean2 = mean1 * self.ratio[0]
        else:
            mean2 = mean1 * (rlo + values.get("ratio_fraction", 0.5) * max(0, rhi - rlo))
        if self.request.linked_cv != "none":
            cv1 = cv2 = values.get("shared_cv", self.shared_cv[0])
        else:
            cv1 = values.get("cv1", self.cv1[0])
            cv2 = values.get("cv2", self.cv2[0])
        return np.array([mean1, mean2, cv1, cv2])

    def project(self, peaks):
        """Apply the same exact constraints to Watson's iterative moments."""
        mean1 = float(np.clip(peaks[0], *self.mean1))
        rlo, rhi = self.ratio_limits(mean1)
        mean2 = mean1 * float(np.clip(peaks[1] / mean1, rlo, rhi))
        if self.request.linked_cv != "none":
            linked = peaks[2] if self.request.linked_cv == "g2_to_g1" else peaks[3]
            cv1 = cv2 = float(np.clip(linked, *self.shared_cv))
        else:
            cv1, cv2 = np.clip(peaks[2], *self.cv1), np.clip(peaks[3], *self.cv2)
        return np.array([mean1, mean2, cv1, cv2])


def peak_initialization(edges, counts, request, scale):
    centers = (edges[1:] + edges[:-1]) / 2
    smooth = gaussian_filter1d(counts.astype(float), max(request.smoothing, 0.75))
    found, _ = find_peaks(smooth, prominence=max(smooth.max() * 0.04, 1))
    # Select a resolved low-DNA peak even when G2/M is the dominant population.
    found = found[(centers[found] > edges[-1] * 0.1) & (smooth[found] > smooth.max() * 0.08)]
    i1 = int(found[0]) if len(found) else int(np.argmax(smooth))
    if request.g1_mean.initial or request.g1_mean.fixed:
        i1 = int(
            np.argmin(abs(centers - (request.g1_mean.initial or request.g1_mean.fixed) / scale))
        )
    search = centers >= 1.75 * centers[i1]
    if search.any():
        indices = np.flatnonzero(search)
        i2 = int(indices[np.argmax(smooth[indices])])
    else:
        i2 = int(np.argmin(abs(centers - 2 * centers[i1])))
    if request.g2_mean.initial or request.g2_mean.fixed:
        i2 = int(
            np.argmin(abs(centers - (request.g2_mean.initial or request.g2_mean.fixed) / scale))
        )
    peaks = []
    for index, side in ((i1, -1), (i2, 1)):
        neighbor = index
        while 0 < neighbor < len(centers) - 1 and smooth[neighbor] > 0.6 * smooth[index]:
            neighbor += side
        sigma = max(abs(centers[neighbor] - centers[index]), 1.5 * (edges[1] - edges[0]))
        mean = max(centers[index], edges[1] - edges[0])
        # The uncontaminated side of each peak initializes the nonlinear model.
        for _ in range(3):
            left, right = (-3, 1) if side == -1 else (-1, 3)
            selected = (centers >= mean + left * sigma) & (centers <= mean + right * sigma)
            if selected.sum() < 4:
                break
            x, y = centers[selected], counts[selected]
            result = least_squares(
                lambda p, x=x, y=y: p[0] * np.exp(-0.5 * ((x - p[1]) / p[2]) ** 2) - y,
                [max(smooth[index], 1), mean, sigma],
                bounds=(
                    [0, max(edges[0], 1e-8), (edges[1] - edges[0]) / 3],
                    [np.inf, edges[-1], edges[-1] / 3],
                ),
                max_nfev=100,
            )
            mean, sigma = result.x[1:]
        peaks.append((mean, np.clip(100 * sigma / mean, 0.5, 20)))
    return np.array([peaks[0][0], peaks[1][0], peaks[0][1], peaks[1][1]])


@lru_cache(maxsize=12)
def quadrature(order):
    points, weights = np.polynomial.legendre.leggauss(order)
    return (points + 1) / 2, weights / 2


def djf_components(edges, peaks, shape, synchronous=None, order=256):
    """Bin-integrated probabilities, including DNA-dependent Gaussian broadening.

    A nonnegative quadratic in Bernstein form covers the full quadratic cone:
    b0,b2≥0 and b1≥−sqrt(b0*b2), without clipping a negative fitted polynomial.
    """
    mean1, mean2, cv1, cv2 = peaks
    sigma1, sigma2 = mean1 * cv1 / 100, mean2 * cv2 / 100
    g1 = np.maximum(np.diff(ndtr((edges - mean1) / sigma1)), 0)
    g2 = np.maximum(np.diff(ndtr((edges - mean2) / sigma2)), 0)
    t, weights = quadrature(order)
    angle, middle = shape
    left, right = math.cos(angle), math.sin(angle)
    b0 = (1 - middle) * left**2
    b2 = (1 - middle) * right**2
    b1 = middle - (1 - middle) * left * right
    polynomial = b0 * (1 - t) ** 2 + 2 * b1 * t * (1 - t) + b2 * t**2
    density = polynomial / ((b0 + b1 + b2) / 3)
    if synchronous is not None:
        fraction, location, spread = synchronous
        norm = ndtr((1 - location) / spread) - ndtr(-location / spread)
        wave = np.exp(-0.5 * ((t - location) / spread) ** 2) / (
            math.sqrt(2 * math.pi) * spread * norm
        )
        density = (1 - fraction) * density + fraction * wave
    dna = mean1 + (mean2 - mean1) * t
    broadening = dna * cv1 / 100
    cumulative = ndtr((edges[:, None] - dna[None, :]) / broadening[None, :])
    s = np.maximum(np.diff(cumulative @ (weights * density)), 0)
    values = np.stack([g1, s, g2])
    coverage = values.sum(axis=1)
    if np.any(coverage < 1e-8):
        raise ValueError("A fitted phase has no support in the selected DNA range")
    return values / coverage[:, None], coverage


def poisson_residual(predicted, observed):
    predicted = np.maximum(predicted, 1e-10)
    term = predicted - observed
    positive = observed > 0
    term[positive] += observed[positive] * np.log(observed[positive] / predicted[positive])
    return np.sign(predicted - observed) * np.sqrt(np.maximum(2 * term, 0))


def goodness(observed, components, free_parameters=None):
    predicted = components.sum(axis=0)
    residual = observed - predicted
    chi2 = float(np.sum(residual**2 / np.maximum(predicted, 1)))
    deviance = float(np.sum(poisson_residual(predicted, observed) ** 2))
    degrees = len(observed) - 1 - free_parameters if free_parameters is not None else None
    return {
        "rmsd_events_per_bin": float(np.sqrt(np.mean(residual**2))),
        "normalized_rmsd": float(np.sqrt(np.mean(residual**2)) / max(float(observed.max()), 1)),
        "pearson_chi_square": chi2,
        "poisson_deviance": deviance,
        "degrees_of_freedom": degrees,
        "reduced_chi_square": chi2 / degrees if degrees and degrees > 0 else None,
        "free_parameters": free_parameters,
        "residuals": residual.tolist(),
    }


def fit_djf(edges, counts, parameters, request, progress):
    n_peaks = len(parameters.names)
    initial = [*parameters.initial, math.pi / 4, 0.5, 0.5, -0.8]
    lower = [*parameters.lower, 0, 0, -16, -16]
    upper = [*parameters.upper, math.pi / 2, 1, 16, 16]
    if request.synchronous_s:
        peaks = parameters.decode(parameters.initial)
        location = (
            (request.s_peak_initial / parameters.scale - peaks[0]) / (peaks[1] - peaks[0])
            if request.s_peak_initial
            else 0.5
        )
        initial += [0.5, float(np.clip(location, 0.03, 0.97)), 0.1]
        lower += [0, 0.01, 0.01]
        upper += [1, 0.99, 0.5]
    minimum_cv = parameters.shared_cv[0] if request.linked_cv != "none" else parameters.cv1[0]
    order = max(192, math.ceil(120 / minimum_cv))
    total = float(counts.sum())

    def evaluate(vector):
        peaks = parameters.decode(vector[:n_peaks])
        shape = vector[n_peaks : n_peaks + 2]
        fractions = softmax([vector[n_peaks + 2], 0, vector[n_peaks + 3]])
        sync = vector[n_peaks + 4 :] if request.synchronous_s else None
        probabilities, coverage = djf_components(edges, peaks, shape, sync, order)
        return total * fractions[:, None] * probabilities, peaks, fractions, coverage

    def residual(vector):
        components, *_ = evaluate(vector)
        predicted = components.sum(axis=0)
        if request.objective == "weighted_least_squares":
            return (predicted - counts) / np.sqrt(np.maximum(counts, 1))
        return poisson_residual(predicted, counts)

    best, attempts = None, []
    for attempt in range(3):
        candidate = np.array(initial, dtype=float)
        if attempt:
            candidate[n_peaks + 2 : n_peaks + 4] = [(-0.2, -0.3), (1.1, 0.2)][attempt - 1]
            if request.synchronous_s and not request.s_peak_initial:
                candidate[-2] = [0.35, 0.7][attempt - 1]
        progress(f"Optimizing DJF fit ({attempt + 1}/3)", 0.2 + 0.16 * attempt)
        solution = least_squares(
            residual,
            candidate,
            bounds=(lower, upper),
            max_nfev=request.maximum_evaluations,
            ftol=1e-7,
            xtol=1e-7,
            gtol=1e-7,
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
    components, peaks, fractions, coverage = evaluate(best.x)
    predicted = components.sum(axis=0)
    weights = np.divide(
        components,
        predicted[None, :],
        out=np.full_like(components, 1 / 3),
        where=predicted[None, :] > 0,
    )
    _, singular, right = np.linalg.svd(best.jac, full_matrices=False)
    rank = int((singular > max(singular[0] * 1e-8, 1e-10)).sum())
    at_bounds = [
        i
        for i, (value, lo, hi) in enumerate(zip(best.x, lower, upper, strict=True))
        if min(value - lo, hi - value) < 1e-4 * (hi - lo)
    ]
    diagnostics = goodness(counts, components, len(best.x))
    diagnostics.update(
        objective=request.objective,
        converged=bool(best.success),
        optimizer_message=best.message,
        attempts=attempts,
        quadrature_order=order,
        jacobian_rank=rank,
        parameters_at_bounds=at_bounds,
        captured_component_mass=coverage.tolist(),
        shape={
            "endpoint_angle": float(best.x[n_peaks]),
            "middle_fraction": float(best.x[n_peaks + 1]),
        },
        synchronous_s=best.x[n_peaks + 4 :].tolist() if request.synchronous_s else None,
        fraction_standard_errors=None,
    )
    warnings = []
    if not best.success:
        warnings.append(
            "DJF reached its evaluation limit. Inspect the residuals and adjust "
            "peak constraints before using this fit."
        )
    if rank < len(best.x):
        warnings.append(
            "Some model parameters are not identifiable from this histogram. "
            "Fraction uncertainty is undefined."
        )
    if at_bounds:
        warnings.append(
            "One or more fitted parameters reached a constraint boundary. Review those constraints."
        )
    if rank == len(best.x) and not at_bounds and best.success:
        covariance = (right.T / singular**2) @ right
        if request.objective == "weighted_least_squares":
            covariance *= 2 * best.cost / max(len(counts) - len(best.x), 1)
        gradient = np.zeros((3, len(best.x)))
        for i in (n_peaks + 2, n_peaks + 3):
            step = 1e-4
            changed = best.x.copy()
            changed[i] += step
            gradient[:, i] = (evaluate(changed)[2] - fractions) / step
        diagnostics["fraction_standard_errors"] = np.sqrt(
            np.maximum(np.diag(gradient @ covariance @ gradient.T), 0)
        ).tolist()
    return peaks, fractions, components, weights, diagnostics, warnings


def watson_weights(centers, counts, peaks):
    mean1, mean2, cv1, cv2 = peaks
    sd1, sd2 = mean1 * cv1 / 100, mean2 * cv2 / 100
    central = np.flatnonzero((centers >= mean1 + 3 * sd1) & (centers <= mean2 - 3 * sd2))
    if len(central) < 6:
        raise ValueError(
            "Watson requires a resolved S-phase interval between the peaks. "
            "Use tighter peak constraints or review a DJF fit."
        )
    span = max(3, math.ceil(len(central) / 3))
    shifts, envelopes = [], []
    for mean, sd, indices in ((mean1, sd1, central[:span]), (mean2, sd2, central[-span:])):
        x = (centers[indices] - mean) / sd
        coefficients = np.linalg.lstsq(np.c_[np.ones(len(x)), x], counts[indices], rcond=None)[0]
        envelope = max(float(coefficients[0]), 0)
        peak = max(float(np.interp(mean, centers, counts)), 1)
        # Positive inward shifts implement P(S at the peak) = S-envelope/(2H).
        shift = -float(ndtri(np.clip(envelope / (2 * peak), ndtr(-3), 0.49)))
        shifts.append(shift)
        envelopes.append(envelope)
    g1 = 1 - ndtr((centers - mean1) / sd1 - shifts[0])
    g2 = ndtr((centers - mean2) / sd2 + shifts[1])
    s = np.maximum(1 - g1 - g2, 0)
    weights = np.stack([g1, s, g2])
    weights /= weights.sum(axis=0)
    return weights, {
        "cdf_shifts": shifts,
        "s_envelope_at_peaks": envelopes,
        "regression_bins": [central[:span].tolist(), central[-span:].tolist()],
    }


def fit_watson(edges, counts, parameters, request, progress):
    # Experimental and deliberately unavailable through validate_request: the
    # primary-paper CDF/moment interpretation fails independent latent-label
    # recovery. Do not expose it until that discrepancy has been resolved.
    centers = (edges[1:] + edges[:-1]) / 2
    working = (
        gaussian_filter1d(counts.astype(float), request.smoothing)
        if request.smoothing
        else counts.astype(float)
    )
    peaks = parameters.project(parameters.decode(parameters.initial))
    converged, trace = False, []
    for iteration in range(100):
        weights, details = watson_weights(centers, working, peaks)
        updated = peaks.copy()
        for component, mean_index, cv_index in ((0, 0, 2), (2, 1, 3)):
            phase = weights[component] * working
            if phase.sum() < 5:
                raise ValueError(
                    "Watson cannot resolve a peak with fewer than five expected events"
                )
            mean = float(centers @ phase / phase.sum())
            sd = math.sqrt(float(((centers - mean) ** 2) @ phase / phase.sum()))
            updated[mean_index], updated[cv_index] = mean, 100 * sd / mean
        updated = parameters.project(updated)
        relative = float(np.max(abs(updated - peaks) / np.maximum(abs(peaks), 1e-8)))
        trace.append(relative)
        peaks = updated
        if relative < 0.025:
            converged = True
            break
        if iteration % 5 == 0:
            progress("Refining Watson probability interfaces", min(0.75, 0.2 + iteration / 150))
    weights, details = watson_weights(centers, working, peaks)
    expected = weights @ counts
    fractions = expected / counts.sum()
    mean1, mean2, cv1, cv2 = peaks
    g1 = np.maximum(np.diff(ndtr((edges - mean1) / (mean1 * cv1 / 100))), 0)
    g2 = np.maximum(np.diff(ndtr((edges - mean2) / (mean2 * cv2 / 100))), 0)
    components = np.stack(
        [expected[0] * g1 / g1.sum(), counts * weights[1], expected[2] * g2 / g2.sum()]
    )
    diagnostics = goodness(counts, components)
    diagnostics.update(
        objective="Watson empirical probability weighting",
        converged=converged,
        iterations=len(trace),
        relative_moment_changes=trace,
        fraction_standard_errors=None,
        uncertainty="Undefined: S-phase is estimated empirically from the observed histogram",
        **details,
    )
    warnings = []
    if not converged:
        warnings.append(
            "Watson's peak moments did not converge after 100 refinements. "
            "Review the fit before use."
        )
    if any(v == 0 for v in details["s_envelope_at_peaks"]):
        warnings.append(
            "A Watson S-phase boundary extrapolated to zero. Its interface uses the three-SD limit."
        )
    return peaks, fractions, components, weights, diagnostics, warnings


def fit_sample(workspace, request, source, engine, progress):
    sample = engine.sample(workspace, source.sample_id)
    population = engine.mask(workspace, sample, source.gate_id)
    values = engine.column(workspace, sample, request.channel, compensated=request.compensated)
    finite = population & np.isfinite(values)
    usable = finite & (values >= 0)
    if usable.sum() < 200:
        raise ValueError(
            f"{sample.name}: at least 200 finite, nonnegative source events are required"
        )
    reference = float(np.max(values[usable]))
    if reference <= 0:
        raise ValueError(f"{sample.name}: DNA intensities have no measurable positive variation")
    scaled = values / reference
    # Scale before quantiles to avoid interpolation overflow with valid large DNA.
    lower = request.range_min / reference if request.range_min is not None else 0.0
    upper = (
        request.range_max / reference
        if request.range_max is not None
        else min(1.0, float(np.quantile(scaled[usable], 0.999)) * 1.05)
    )
    if not math.isfinite(upper) or upper <= lower or lower < 0:
        raise ValueError(f"{sample.name}: the selected DNA fit range is invalid")
    identities = np.flatnonzero(usable & (scaled >= lower) & (scaled <= upper))
    if len(identities) < 200:
        raise ValueError(f"{sample.name}: fewer than 200 events lie inside the DNA fit range")
    edges = np.linspace(lower, upper, request.bins + 1)
    counts, _ = np.histogram(scaled[identities], bins=edges)
    if np.count_nonzero(counts) < 16:
        raise ValueError(
            f"{sample.name}: DNA distribution occupies fewer than 16 histogram bins; "
            "adjust the fit range or resolution"
        )
    initial = peak_initialization(edges, counts, request, reference)
    parameters = PeakParameters(request, reference, edges, initial)
    fit = fit_watson if request.method == "watson" else fit_djf
    peaks, fractions, components, weights, diagnostics, warnings = fit(
        edges, counts, parameters, request, progress
    )
    bin_ids = np.minimum(
        np.searchsorted(edges, scaled[identities], side="right") - 1, request.bins - 1
    )
    probabilities = weights[:, bin_ids].T
    outputs = np.full((sample.event_count, 4), np.nan, dtype=np.float32)
    outputs[identities, :3] = probabilities
    assigned = np.argmax(outputs[identities, :3], axis=1) + 1
    outputs[identities, 3] = assigned
    mean1, mean2, cv1, cv2 = peaks
    expected = outputs[identities, :3].sum(axis=0, dtype=np.float64)
    assignments = np.bincount(assigned, minlength=4)[1:]
    fitted_edges = edges * reference
    data = CellCycleData(
        sample_id=sample.id,
        event_count=sample.event_count,
        population_count=int(population.sum()),
        finite_count=int(finite.sum()),
        fitted_count=len(identities),
        sha256="0" * 64,
    )
    diagnostics.update(
        parameter_basis="Linear compensated intensity"
        if request.compensated
        else "Linear uncompensated intensity",
        excluded_nonfinite=int((population & ~finite).sum()),
        excluded_negative=int((finite & (values < 0)).sum()),
        excluded_below_range=int((usable & (scaled < lower)).sum()),
        excluded_above_range=int((usable & (scaled > upper)).sum()),
        population_fraction_fitted=len(identities) / max(data.population_count, 1),
        percentage_basis="Finite, nonnegative source events within the fit range",
        event_probability_basis="Conditional phase probability for the event's histogram bin",
        assignment_basis="Largest phase probability; ties choose G0/G1, then S, then G2/M",
        expected_fraction=(expected / len(identities)).tolist(),
        automatic_range=request.range_min is None or request.range_max is None,
    )
    if request.range_max is None and diagnostics["excluded_above_range"]:
        warnings.append(
            "The automatic upper range excludes extreme high-DNA events. "
            "Set an explicit range to include them."
        )
    if data.finite_count != data.population_count:
        warnings.append("Nonfinite DNA events are excluded from the model and phase assignments.")
    if len(identities) < 1000:
        warnings.append("Fewer than 1,000 events were fitted. Phase estimates may be unstable.")
    if max(cv1, cv2) > 12:
        warnings.append("A fitted peak CV exceeds 12%. Inspect peak overlap, debris and singlets.")
    if min(fractions[0], fractions[2]) < 0.02:
        warnings.append(
            "A fitted G1 or G2/M phase is below 2%. Its peak may require independent constraints."
        )
    if (
        diagnostics.get("captured_component_mass")
        and min(diagnostics["captured_component_mass"]) < 0.98
    ):
        warnings.append(
            "The fit range truncates more than 2% of a modeled phase. "
            "Reported fractions are conditional on this range."
        )
    if diagnostics["normalized_rmsd"] > 0.08:
        warnings.append(
            "Large histogram residuals remain. Review the source population, model and constraints."
        )
    result = CellCycleFit(
        sample_id=sample.id,
        data=data,
        range_min=float(fitted_edges[0]),
        range_max=float(fitted_edges[-1]),
        edges=fitted_edges.tolist(),
        observed=counts.tolist(),
        components=components.tolist(),
        weights=weights.tolist(),
        parameters={
            "g1_mean": float(mean1 * reference),
            "g2_mean": float(mean2 * reference),
            "g1_cv": float(cv1),
            "g2_cv": float(cv2),
            "peak_ratio": float(mean2 / mean1),
        },
        fractions=fractions.tolist(),
        expected_counts=expected.tolist(),
        assigned_counts=assignments.tolist(),
        diagnostics=diagnostics,
        warnings=warnings,
    )
    return result, outputs


def calculate(workspace, request, engine, identifier, progress=lambda stage, fraction: None):
    started = time.monotonic()
    snapshot = input_snapshot(workspace, request)
    fits, arrays = [], {}
    for index, source in enumerate(request.inputs):
        sample = engine.sample(workspace, source.sample_id)

        def stage(message, fraction, sample=sample, index=index):
            progress(f"{sample.name}: {message}", (index + fraction) / len(request.inputs))

        stage("Building the full-event DNA histogram", 0.03)
        fitted, outputs = fit_sample(workspace, request, source, engine, stage)
        fits.append(fitted)
        arrays[source.sample_id] = outputs
    return CellCycleResult(
        id=identifier,
        request=request,
        created_at=now(),
        input_hash=fingerprint(snapshot),
        input_snapshot=snapshot,
        columns=output_columns(
            workspace,
            request,
            [
                f"{request.name} G1 probability",
                f"{request.name} S probability",
                f"{request.name} G2 probability",
                f"{request.name} phase",
            ],
        ),
        fits=fits,
        versions={package: version(package) for package in ("numpy", "scipy")},
        warnings=[
            "Cell-cycle estimates require a DNA stain proportional to DNA content "
            "and a reviewed singlet population. G0 and G1, and G2 and mitosis, "
            "are not distinguished by DNA alone."
        ],
        duration_seconds=time.monotonic() - started,
    ), arrays


def load_data(store, workspace_id, result, data):
    path = store.analysis_path(workspace_id, result.id, data.sample_id)
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != data.sha256:
        raise ValueError("Cell-cycle event probabilities failed their integrity check")
    return load_validated_array(path, lambda values: validate_data(result, data, values))


def validate_data(result, data, values):
    if values.shape != (data.event_count, 4) or values.dtype != np.float32:
        raise ValueError("Invalid event-aligned cell-cycle probabilities")
    fitted = np.all(np.isfinite(values), axis=1)
    undefined = np.all(np.isnan(values), axis=1)
    if not np.all(fitted | undefined) or int(fitted.sum()) != data.fitted_count:
        raise ValueError("Cell-cycle probability rows do not match fitted event counts")
    subset = values[fitted]
    if (
        np.any(subset[:, :3] < 0)
        or np.any(subset[:, :3] > 1)
        or not np.allclose(subset[:, :3].sum(axis=1), 1, atol=1e-6)
    ):
        raise ValueError("Cell-cycle probabilities must be finite and sum to one")
    assignments = np.argmax(subset[:, :3], axis=1) + 1
    if not np.array_equal(subset[:, 3], assignments):
        raise ValueError("Cell-cycle assignments do not match their phase probabilities")
    fit = next(f for f in result.fits if f.sample_id == data.sample_id)
    if not np.array_equal(
        np.bincount(assignments, minlength=4)[1:], fit.assigned_counts
    ) or not np.allclose(
        subset[:, :3].sum(axis=0, dtype=np.float64), fit.expected_counts, rtol=1e-6, atol=0.01
    ):
        raise ValueError("Cell-cycle phase counts do not match their original events")


def figure_svg(result, fit, sample_name, stale=False):
    """Standalone vector figure: histogram, components, residuals and statistics."""
    from xml.etree.ElementTree import Element, SubElement, tostring

    svg = Element(
        "svg",
        xmlns="http://www.w3.org/2000/svg",
        width="1000",
        height="670",
        viewBox="0 0 1000 670",
    )
    SubElement(svg, "rect", width="1000", height="670", fill="white")

    def label(x, y, text, size=14, fill="#334155"):
        item = SubElement(
            svg,
            "text",
            x=str(x),
            y=str(y),
            fill=fill,
            **{"font-family": "Arial, sans-serif", "font-size": str(size)},
        )
        item.text = text

    label(76, 35, f"{sample_name} · {result.request.name}", 22)
    label(
        76,
        60,
        f"Dean–Jett–Fox{' · synchronous S' if result.request.synchronous_s else ''} "
        f"· {result.request.channel} · {fit.data.fitted_count:,} fitted events",
    )
    if stale:
        label(76, 84, "Saved fit: scientific inputs have changed", fill="#b45309")
    observed = np.asarray(fit.observed)
    components = np.asarray(fit.components)
    total = components.sum(axis=0)
    ymax = max(float(observed.max()), float(total.max()), 1) * 1.05
    centers = np.asarray(fit.edges[1:]) / 2 + np.asarray(fit.edges[:-1]) / 2
    # Normalize by endpoints before subtraction, which also handles huge DNA units.
    x = 76 + 864 * (
        (centers / fit.range_max - fit.range_min / fit.range_max)
        / (1 - fit.range_min / fit.range_max)
    )
    for i in range(5):
        y = 405 - i / 4 * 290
        SubElement(svg, "line", x1="76", x2="940", y1=str(y), y2=str(y), stroke="#e2e8f0")
        label(8, y + 5, f"{ymax * i / 4:.0f}", 12)
    for values, color, width in [
        (observed, "#475569", 1.5),
        (total, "#a855f7", 2.5),
        *[
            (row, color, 2)
            for row, color in zip(components, ("#4f7ee6", "#0d9b7c", "#d69531"), strict=True)
        ],
    ]:
        path = "M" + " L".join(
            f"{px:.3f},{405 - 290 * value / ymax:.3f}" for px, value in zip(x, values, strict=True)
        )
        SubElement(svg, "path", d=path, fill="none", stroke=color, **{"stroke-width": str(width)})
    residual = observed - total
    limit = max(float(np.abs(residual).max()), 1)
    SubElement(svg, "line", x1="76", x2="940", y1="471", y2="471", stroke="#cbd5e1")
    path = "M" + " L".join(
        f"{px:.3f},{471 - 35 * value / limit:.3f}" for px, value in zip(x, residual, strict=True)
    )
    SubElement(svg, "path", d=path, fill="none", stroke="#64748b", **{"stroke-width": "1.2"})
    label(8, 455, "Residual", 12)
    label(8, 474, "0", 12)
    for i in range(5):
        value = fit.range_max * ((fit.range_min / fit.range_max) * (1 - i / 4) + i / 4)
        label(76 + 864 * i / 4, 530, f"{value:.5g}", 12)
    label(430, 554, f"{result.request.channel} (linear intensity)")
    for index, (phase, color) in enumerate(
        zip(PHASES, ("#4f7ee6", "#0d9b7c", "#d69531"), strict=True)
    ):
        label(
            76 + index * 285,
            590,
            f"{phase}: model {fit.fractions[index] * 100:.2f}% "
            f"· assigned {fit.assigned_counts[index]:,}",
            fill=color,
        )
    p = fit.parameters
    label(
        76,
        616,
        f"G1 mean {p['g1_mean']:.6g} · CV {p['g1_cv']:.2f}%     "
        f"G2 mean {p['g2_mean']:.6g} · CV {p['g2_cv']:.2f}%     "
        f"Ratio {p['peak_ratio']:.4f}",
    )
    label(
        76,
        642,
        f"RMSD {fit.diagnostics['rmsd_events_per_bin']:.3f} events/bin "
        "· DNA alone does not distinguish G0/G1 or G2/mitosis",
        12,
    )
    return tostring(svg, encoding="utf-8", xml_declaration=True)


def run_cellcycle(directory: str):
    directory = Path(directory)
    stop = threading.Event()
    store = None
    try:
        payload = json.loads((directory / "input.json").read_text())
        threading.Thread(
            target=watch_parent, args=(payload["parent_pid"], stop), daemon=True
        ).start()
        workspace = Workspace.model_validate(payload["workspace"])
        request = CellCycleRequest.model_validate(payload["request"])
        if input_hash(workspace, request) != payload["input_hash"]:
            raise ValueError("Cell-cycle inputs changed before the worker started")
        store = Store(Path(payload["data_dir"]))
        for source in request.inputs:
            sample = next(s for s in workspace.samples if s.id == source.sample_id)
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
