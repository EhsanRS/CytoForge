"""Compare experimental DNA refinements with independently labelled mixtures.

None of these experimental interpretations is enabled as a scientific method.
Fixed, known peaks are a diagnostic, not information available to an actual fit.
"""

import hashlib
import json
import math
from importlib.metadata import version
from pathlib import Path

import numpy as np
from cytoforge.cellcycle import PeakParameters, fit_watson, peak_initialization, watson_weights
from cytoforge.models import AnalysisInput, CellCycleRequest, FitConstraint
from scipy.ndimage import gaussian_filter1d
from scipy.optimize import least_squares
from scipy.special import ndtr

ROOT = Path(__file__).resolve().parents[1]
FRACTION_TOLERANCE_PP = 1.7
REFINEMENT_TOLERANCE = 1e-5


def gaussian_refit(edges, counts, parameters, request):
    centers = (edges[:-1] + edges[1:]) / 2
    working = (
        gaussian_filter1d(counts.astype(float), request.smoothing)
        if request.smoothing
        else counts.astype(float)
    )
    peaks = parameters.decode(parameters.initial)
    vector = np.r_[parameters.initial, counts.sum() * 0.55, counts.sum() * 0.15]
    trace = []
    for _iteration in range(100):
        weights, _details = watson_weights(centers, working, peaks)
        corrected = working * (1 - weights[1])

        def component_matrix(p):
            mean1, mean2, cv1, cv2 = parameters.decode(p)
            g1 = np.maximum(np.diff(ndtr((edges - mean1) / (mean1 * cv1 / 100))), 0)
            g2 = np.maximum(np.diff(ndtr((edges - mean2) / (mean2 * cv2 / 100))), 0)
            return np.stack([p[-2] * g1 / g1.sum(), p[-1] * g2 / g2.sum()])

        fitted = least_squares(
            lambda p, corrected=corrected: component_matrix(p).sum(axis=0) - corrected,
            vector,
            bounds=(
                np.r_[parameters.lower, 0, 0],
                np.r_[parameters.upper, counts.sum(), counts.sum()],
            ),
            max_nfev=500,
            x_scale="jac",
        )
        updated = parameters.decode(fitted.x)
        delta = float(np.max(abs(fitted.x - vector) / np.maximum(abs(vector), 1e-8)))
        trace.append(delta)
        peaks, vector = updated, fitted.x
        if delta < REFINEMENT_TOLERANCE:
            break
    weights, _details = watson_weights(centers, working, peaks)
    gaussian = component_matrix(vector)
    components = np.stack([gaussian[0], counts * weights[1], gaussian[1]])
    posterior = components / np.maximum(components.sum(axis=0), 1e-300)
    return peaks, posterior @ counts / counts.sum(), trace


def generated(seed, fractions, size=40000, shape="beta"):
    rng = np.random.default_rng(seed)
    phase = rng.choice(3, size=size, p=fractions)
    values = np.empty(size)
    for index, mean, cv in [(0, 100, 4), (2, 198, 5)]:
        mask = phase == index
        values[mask] = rng.normal(mean, mean * cv / 100, mask.sum())
    mask = phase == 1
    latent = rng.beta(2.5, 1.8, mask.sum())
    if shape in {"quadratic", "mixed_wave"}:
        accepted = []
        while sum(map(len, accepted)) < mask.sum():
            proposal = rng.uniform(0, 1, mask.sum() * 2)
            density = 1 + 0.6 * proposal + 1.2 * proposal**2
            accepted.append(proposal[rng.uniform(0, 2.8, len(proposal)) < density])
        latent = np.concatenate(accepted)[: mask.sum()]
        if shape == "mixed_wave":
            wave = rng.random(len(latent)) < 0.7
            latent[wave] = np.clip(rng.normal(0.64, 0.055, wave.sum()), 0, 1)
    elif shape == "wave":
        latent = np.clip(rng.normal(0.6, 0.05, mask.sum()), 0, 1)
    elif shape != "beta":
        raise ValueError("Unknown independent S-phase generator")
    latent = 100 + 98 * latent
    values[mask] = rng.normal(latent, 0.04 * latent)
    return values, phase


def empirical_refit(edges, counts, parameters, request):
    centers = (edges[:-1] + edges[1:]) / 2
    working = (
        gaussian_filter1d(counts.astype(float), request.smoothing)
        if request.smoothing
        else counts.astype(float)
    )
    vector = np.r_[parameters.initial, counts.sum() * 0.55, counts.sum() * 0.15]

    def gaussian(p):
        mean1, mean2, cv1, cv2 = parameters.decode(p)
        probabilities = np.stack(
            [
                np.maximum(np.diff(ndtr((edges - mean1) / (mean1 * cv1 / 100))), 0),
                np.maximum(np.diff(ndtr((edges - mean2) / (mean2 * cv2 / 100))), 0),
            ]
        )
        return probabilities / probabilities.sum(axis=1)[:, None] * p[-2:, None]

    def empirical_component(p):
        peaks = parameters.decode(p)
        m1, m2, c1, c2 = peaks
        s1, s2 = m1 * c1 / 100, m2 * c2 / 100
        interior = (centers >= m1 + 2 * s1) & (centers <= m2 - 2 * s2)
        if interior.sum() < 6:
            raise ValueError("Empirical S refinement needs an identifiable interior")
        residual = np.maximum(working - gaussian(p).sum(axis=0), 0)
        indices = np.flatnonzero(interior)
        anchors = []
        for mean, sd, side in [(m1, s1, "left"), (m2, s2, "right")]:
            span = max(6, min(len(indices) // 3, math.ceil(3 * sd / (edges[1] - edges[0]))))
            selected = indices[:span] if side == "left" else indices[-span:]
            design = np.c_[np.ones(len(selected)), centers[selected] - mean]
            intercept = np.linalg.lstsq(design, residual[selected], rcond=None)[0][0]
            anchors.append(max(float(intercept), 0))
        spectrum = np.interp(
            centers,
            np.r_[m1, centers[interior], m2],
            np.r_[anchors[0], residual[interior], anchors[1]],
        )
        empirical = spectrum * np.maximum(ndtr((centers - m1) / s1) - ndtr((centers - m2) / s2), 0)
        selected = ((centers >= m1 - 3 * s1) & (centers <= m1 + s1)) | (
            (centers >= m2 - s2) & (centers <= m2 + 3 * s2)
        )
        return empirical, selected

    trace = []
    for _iteration in range(100):
        empirical, selected = empirical_component(vector)
        corrected = np.maximum(working - empirical, 0)
        fitted = least_squares(
            lambda p, corrected=corrected, selected=selected: (gaussian(p).sum(axis=0) - corrected)[
                selected
            ],
            vector,
            bounds=(
                np.r_[parameters.lower, 0, 0],
                np.r_[parameters.upper, counts.sum(), counts.sum()],
            ),
            max_nfev=500,
            x_scale="jac",
        )
        updated = 0.5 * vector + 0.5 * fitted.x
        delta = float(np.max(abs(updated - vector) / np.maximum(abs(vector), 1e-8)))
        trace.append(delta)
        vector = updated
        if delta < REFINEMENT_TOLERANCE:
            break
    peaks = parameters.decode(vector)
    peaks_components = gaussian(vector)
    empirical, _selected = empirical_component(vector)
    components = np.stack([peaks_components[0], empirical, peaks_components[1]])
    weights = components / np.maximum(components.sum(axis=0), 1e-300)
    return peaks, weights @ counts / counts.sum(), trace


def main():
    records = []
    for index, fractions in enumerate(
        [(0.55, 0.3, 0.15), (0.25, 0.6, 0.15), (0.2, 0.4, 0.4), (0.8, 0.1, 0.1)]
    ):
        for shape in ["beta", "wave", "quadratic", "mixed_wave"]:
            values, labels = generated(74 + index, fractions, shape=shape)
            edges = np.linspace(40, 250, 513)
            counts = np.histogram(values, edges)[0]
            fitted = (values >= edges[0]) & (values <= edges[-1])
            truth = np.bincount(labels[fitted], minlength=3) / fitted.sum()
            for fixed_truth_peaks in [False, True]:
                constraints = (
                    dict(
                        g1_mean=FitConstraint(fixed=100),
                        g2_mean=FitConstraint(fixed=198),
                        g1_cv=FitConstraint(fixed=4),
                        g2_cv=FitConstraint(fixed=5),
                    )
                    if fixed_truth_peaks
                    else {}
                )
                request = CellCycleRequest(
                    revision=0,
                    inputs=[AnalysisInput(sample_id="a" * 32)],
                    channel="DNA",
                    method="watson",
                    bins=512,
                    smoothing=0.75,
                    **constraints,
                )
                initial = peak_initialization(edges, counts, request, 1)
                parameters = PeakParameters(request, 1, edges, initial)
                record = dict(
                    seed=74 + index,
                    shape=shape,
                    generated_events=len(values),
                    fitted_events=int(fitted.sum()),
                    histogram_events=int(counts.sum()),
                    fixed_truth_peaks=fixed_truth_peaks,
                    diagnostic_only=fixed_truth_peaks,
                    latent_fractions=truth.tolist(),
                    true_peaks=[100, 198, 4, 5],
                    initial_peaks=initial.tolist(),
                    methods={},
                )
                for name, method in [
                    ("moment", fit_watson),
                    ("gaussian_cdf_refit", gaussian_refit),
                    ("empirical_residual_refit", empirical_refit),
                ]:
                    try:
                        if name == "moment":
                            peaks, recovered, _curves, _weights, diagnostic, _warnings = method(
                                edges, counts, parameters, request, lambda *args: None
                            )
                            trace = diagnostic["relative_moment_changes"]
                            converged = diagnostic["converged"]
                            tolerance = 0.025
                        else:
                            peaks, recovered, trace = method(edges, counts, parameters, request)
                            converged = trace[-1] < REFINEMENT_TOLERANCE
                            tolerance = REFINEMENT_TOLERANCE
                        error_pp = float(abs(recovered - truth).max() * 100)
                        finite = bool(np.isfinite(peaks).all() and np.isfinite(recovered).all())
                        record["methods"][name] = dict(
                            peaks=peaks.tolist(),
                            fractions=recovered.tolist(),
                            max_fraction_error_pp=error_pp,
                            internally_converged=bool(converged),
                            iterations=len(trace),
                            convergence_tolerance=tolerance,
                            relative_changes=trace,
                            finite=finite,
                            fraction_sum=float(recovered.sum()),
                            recovery_passed=bool(finite and error_pp <= FRACTION_TOLERANCE_PP),
                        )
                    except ValueError as error:
                        record["methods"][name] = dict(failure=str(error), recovery_passed=False)
                records.append(record)
    summary = {}
    for name in records[0]["methods"]:
        method = [record["methods"][name] for record in records]
        available = [row for row in method if "failure" not in row]
        summary[name] = dict(
            cases=len(method),
            failures=sum("failure" in row for row in method),
            recovery_passed=sum(row["recovery_passed"] for row in method),
            converged_with_failed_recovery=sum(
                row["internally_converged"] and not row["recovery_passed"] for row in available
            ),
            max_fraction_error_pp=max(row["max_fraction_error_pp"] for row in available),
            largest_fraction_sum_error=max(abs(row["fraction_sum"] - 1) for row in available),
            all_cases_passed=all(row["recovery_passed"] for row in method),
        )
    prior = json.loads(
        (ROOT / "artifacts/graph-fonts-desktop-candidate-validation.json").read_text()
    )
    output = dict(
        status="research_only_not_enabled",
        scope="Independent latent event mixtures; no biological/FlowJo reference claim",
        phase_order=["G0/G1", "S", "G2/M"],
        fraction_tolerance_pp=FRACTION_TOLERANCE_PP,
        fixed_truth_peaks_are_diagnostics=True,
        original_publication_full_text_verified=False,
        implementations="Experimental interpretations, not a verified original Watson algorithm",
        source_sha256={
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [
                Path(__file__).resolve(),
                ROOT / "backend/cytoforge/cellcycle.py",
                ROOT / "backend/cytoforge/models.py",
                ROOT / ".cache/references/cellcycle/blasi-2016-watson.m",
            ]
        },
        dependencies={name: version(name) for name in ["numpy", "scipy", "pydantic"]},
        full_objective_complete=False,
        retained_full_goal_unverified=prior["retained_full_goal_unverified"],
        summary=summary,
        cases=records,
    )
    (ROOT / "artifacts/watson-refinement-investigation.json").write_text(
        json.dumps(output, indent=2) + "\n"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
