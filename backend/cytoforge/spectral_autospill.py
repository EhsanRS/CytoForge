"""Rectangular AutoSpill, independently implemented from Roca et al. (2021).

Rows are sources, columns are acquired detectors. The published E @ U correction
acts within the initial row space. Detector reconstruction is therefore reported
separately from residual slopes between unmixed sources.
"""

import hashlib
import math
import platform

import numpy as np
import scipy

from . import autospill
from .autofluorescence import reference_review
from .autospill_biex import biex_functions
from .models import Compensation, new_id
from .science import compensate, validate_matrix
from .store import now

METHOD_VERSION = "autospill-spectral-1"


def definition(request, values):
    return Compensation(
        name=request.name,
        kind="spectral",
        detectors=request.detectors,
        outputs=autospill.output_names(request),
        matrix=np.asarray(values).tolist(),
        background=request.background,
        weights=request.weights,
        source="AutoSpill spectral robust iterative regression",
    )


def unmixing_operator(matrix):
    """Weighted least squares, using the same inverse variances as production."""
    validate_matrix(matrix)
    weights = np.sqrt(matrix.weights) if matrix.weights else np.ones(len(matrix.detectors))
    return weights[:, None] * np.linalg.pinv(np.asarray(matrix.matrix).T * weights[:, None]).T


def initial_signatures(data, request):
    coefficients = np.zeros((len(data), len(request.detectors)))
    fits = []
    for i, (values, control) in enumerate(zip(data, request.controls, strict=True)):
        primary = request.detectors.index(control.primary_detector)
        row = []
        for j in range(len(request.detectors)):
            if j == primary:
                coefficients[i, j] = 1.0
                row.append(
                    dict(count=len(values), converged=True, iterations=0, fallback_ols=False)
                )
                continue
            x, y, count, ties = autospill.trimmed_pair(
                values[:, primary],
                values[:, j],
                request.trim_fraction,
                max(20, min(request.min_events, 50)),
            )
            _, slope, fit = autospill.robust_line(x, y, request.regression_iterations)
            coefficients[i, j] = slope
            row.append(dict(fit, count=count, retained_secondary_ties=ties))
        fits.append(row)
    validate_matrix(definition(request, coefficients))
    return coefficients, fits


def refine(data, initial, request, progress=lambda *_: None):
    labels = autospill.output_names(request)
    peaks = np.array([request.detectors.index(c.primary_detector) for c in request.controls])
    rows = np.arange(len(peaks))
    functions = []
    for output in labels:
        spec = request.detector_transforms.get(output, request.biex)
        functions.append(
            biex_functions(spec.negative, spec.width, spec.positive, spec.top, request.biex_length)
        )
    current = np.array(initial, dtype=float, copy=True)
    background = (
        np.asarray(request.background) if request.background else np.zeros(len(request.detectors))
    )
    centered = [values - background for values in data]
    transformed, damping = False, 1.0
    history, previous, history_count = np.full(10, -1.0), -1.0, 0
    convergence, fallback_pairs = [], set()
    stop_reason, last_converged = "iteration_limit", False
    for iteration in range(request.max_iterations + 1):
        normalizers = current[rows, peaks]
        if not np.isfinite(current).all() or np.any(normalizers <= 1e-12):
            raise ValueError(
                "Spectral refinement produced a nonfinite or nonpositive primary signature"
            )
        current /= normalizers[:, None]
        matrix = definition(request, current)
        condition = validate_matrix(matrix)
        operator = unmixing_operator(matrix)
        unmixed = [values @ operator for values in centered]
        slopes, _, fits = autospill.regression_matrix(unmixed, request, transformed, functions)
        error = slopes - np.eye(len(labels))
        maximum, delta = float(np.max(np.abs(error))), float(np.std(error, ddof=1))
        history[iteration % 10] = delta - previous if previous >= 0 else -1.0
        history_count += int(previous >= 0)
        change = float(np.mean(history))
        convergence.append(
            dict(
                iteration=iteration,
                scale="biex" if transformed else "linear",
                damping=damping,
                error_sd=delta,
                max_error=maximum,
                error_change=change,
                condition_number=condition,
            )
        )
        progress(
            f"Spectral refinement {iteration}: "
            f"{'biex' if transformed else 'linear'} residual {maximum:.3g}",
            min(0.92, 0.35 + 0.56 * (iteration + 1) / request.max_iterations),
        )
        for i, row in enumerate(fits):
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
    return current, dict(
        converged=stop_reason == "target_reached",
        stop_reason=stop_reason,
        iterations=len(convergence) - 1,
        final_scale=convergence[-1]["scale"],
        initial_max_error=convergence[0]["max_error"],
        final_max_error=maximum,
        residual_slopes=error.tolist(),
        convergence=convergence,
        regressions=fits,
        fallback_pairs=[list(p) for p in sorted(fallback_pairs)],
    )


def calculate_prepared(
    workspace, request, data, controls, warnings, selected_ids, snapshot, identifier, progress
):
    progress("Calculating initial robust spectral signatures", 0.3)
    initial, initial_fits = initial_signatures(data, request)
    values, refined = refine(data, initial, request, progress)
    matrix = definition(request, values)
    matrix.id = identifier or new_id()
    labels = matrix.outputs
    condition = validate_matrix(matrix)
    if not refined["converged"]:
        warnings.append(
            f"Spectral refinement stopped at residual {refined['final_max_error']:.3g} "
            f"({refined['stop_reason'].replace('_', ' ')}); "
            f"requested tolerance {request.tolerance:g}"
        )
    if condition > 100:
        warnings.append(f"Matrix condition number {condition:.3g}: detector noise may be amplified")
    for i, row in enumerate(initial_fits):
        if any(f.get("retained_secondary_ties") for f in row):
            warnings.append(f"Initial {labels[i]}: tied secondary trim boundaries retained")
        for j, fit in enumerate(row):
            if fit["fallback_ols"]:
                warnings.append(f"Initial {labels[i]} → {request.detectors[j]}: OLS fallback used")
    for i, j in refined["fallback_pairs"]:
        warnings.append(
            f"Refinement {labels[i]} → {labels[j]}: OLS fallback used in at least one iteration"
        )
    af_names = autospill.autofluorescence_outputs(request)
    if af_names:
        warnings.append(
            "Autofluorescence unmixing assumes a shared reference spectrum; correlated staining "
            "and autofluorescence can bias signatures even with small residual slopes"
            if len(af_names) == 1
            else "Each AF output requires a matching reference spectrum in experimental cells; "
            "correlated staining and AF can bias signatures even with small residual slopes"
        )
        if request.trim_fraction:
            warnings.append(
                "Tail trimming can bias autofluorescence coefficients; "
                "compare with trimming disabled"
            )
    af_review, af_warnings = reference_review(matrix, af_names)
    warnings.extend(af_warnings)
    operator = unmixing_operator(matrix)
    background = (
        np.asarray(request.background) if request.background else np.zeros(len(request.detectors))
    )
    maximum_reconstruction = 0.0
    maximum_relative_rms = 0.0
    for i, (raw, control) in enumerate(zip(data, controls, strict=True)):
        centered = raw - background
        unmixed = centered @ operator
        peak = request.detectors.index(control["primary_detector"])
        residual = centered - unmixed @ values
        if not np.isfinite(residual).all():
            raise ValueError("Spectral detector reconstruction exceeded finite numeric limits")
        slopes, reconstruction_fits = [], []
        for j in range(len(request.detectors)):
            x, y, count, ties = autospill.trimmed_pair(
                centered[:, peak],
                residual[:, j],
                request.trim_fraction,
                max(20, min(request.min_events, 50)),
            )
            _, slope, fit = autospill.robust_line(x, y, request.regression_iterations)
            slopes.append(float(slope))
            reconstruction_fits.append(dict(fit, count=count, retained_secondary_ties=ties))
        maximum = float(np.max(np.abs(slopes)))
        maximum_reconstruction = max(maximum_reconstruction, maximum)
        scale = max(float(np.max(np.abs(centered))), float(np.max(np.abs(residual))))
        relative_rms = float(
            np.sqrt(np.sum((residual / scale) ** 2) / np.sum((centered / scale) ** 2))
        )
        maximum_relative_rms = max(maximum_relative_rms, relative_rms)
        control["reconstruction_slopes"] = slopes
        control["reconstruction_regressions"] = reconstruction_fits
        control["reconstruction_max_error"] = maximum
        scales = np.max(np.abs(residual), axis=0)
        control["reconstruction_rms"] = (
            scales * np.sqrt(np.mean((residual / np.where(scales > 0, scales, 1)) ** 2, axis=0))
        ).tolist()
        control["reconstruction_relative_rms"] = relative_rms
        if maximum > request.linear_tolerance or relative_rms > request.linear_tolerance:
            warnings.append(
                f"{labels[i]}: detector reconstruction maximum slope {maximum:.3g} "
                f"or relative RMS {relative_rms:.3g} exceeds {request.linear_tolerance:g}; "
                "refinement cannot repair signal outside "
                "the initial spectral row space. Inspect controls and autofluorescence."
            )
        if np.max(initial[i]) > 1 + request.linear_tolerance:
            warnings.append(
                f"{labels[i]}: the chosen primary detector is not the largest initial "
                "signature coefficient; review its peak detector"
            )
        candidates = [j for j in range(len(labels)) if j != i]
        secondary = max(candidates, key=lambda j: abs(refined["residual_slopes"][i][j]))
        secondary_peak = request.detectors.index(request.controls[secondary].primary_detector)
        indices = np.linspace(0, len(raw) - 1, min(300, len(raw)), dtype=int)
        control["output_name"] = labels[i]
        control["preview"] = dict(
            x=labels[i],
            y=labels[secondary],
            raw_x=request.detectors[peak],
            raw_y=request.detectors[secondary_peak],
            raw=raw[indices][:, [peak, secondary_peak]].tolist(),
            compensated=unmixed[indices][:, [i, secondary]].tolist(),
            event_ids=selected_ids[i][indices].tolist(),
        )
        control["initial_signature"], control["signature"] = initial[i].tolist(), values[i].tolist()
        control["biex_extrapolated_counts"] = {}
        for j, output in enumerate(labels):
            spec = request.detector_transforms.get(output, request.biex)
            forward = biex_functions(
                spec.negative, spec.width, spec.positive, spec.top, request.biex_length
            )[0]
            count = int(
                ((unmixed[:, j] < forward.x.min()) | (unmixed[:, j] > forward.x.max())).sum()
            )
            control["biex_extrapolated_counts"][output] = count
            if count >= 0.01 * len(raw):
                warnings.append(
                    f"{labels[i]}: {100 * count / len(raw):.1f}% outside the biex lookup "
                    f"range in {output}; linear extrapolation used"
                )
            if not 0.5 <= math.log10(-spec.width) <= 3 and i == 0:
                warnings.append(
                    f"{output}: native biex resets a width outside 0.5–3 decades to 0.5 decades"
                )
        if any(f.get("retained_secondary_ties") for f in refined["regressions"][i]):
            warnings.append(
                f"{labels[i]}: tied secondary trim boundaries retained during final refinement"
            )
    details = dict(
        refined,
        controls=controls,
        initial_matrix=initial.tolist(),
        initial_regressions=initial_fits,
        condition_number=condition,
        rank=len(labels),
        output_units="Selected-primary detector equivalent acquired intensity",
        autofluorescence_sources=af_review,
        reconstruction_max_error=maximum_reconstruction,
        reconstruction_max_relative_rms=maximum_relative_rms,
        reconstruction_tolerance=request.linear_tolerance,
        reconstruction_within_tolerance=max(maximum_reconstruction, maximum_relative_rms)
        <= request.linear_tolerance,
        reconstruction_note="Weighted least-squares residuals in acquired detector space; "
        "cross-source slope convergence cannot repair the initial row space",
    )
    created_at = now()
    digest = autospill.input_hash(workspace, request)
    matrix.provenance = dict(
        kind="autospill_calculation",
        method="autospill",
        version=METHOD_VERSION,
        software=dict(
            cytoforge="0.1.0",
            python=platform.python_version(),
            numpy=np.__version__,
            scipy=scipy.__version__,
        ),
        workspace_id=workspace.id,
        source_revision=workspace.revision,
        created_at=created_at,
        request=request.model_dump(),
        input_hash=digest,
        input_snapshot=snapshot,
        diagnostics=details,
        warnings=warnings,
        calculated_definition=matrix.model_dump(exclude={"provenance"}),
    )
    return autospill.AutoSpillResult(
        id=matrix.id,
        request=request,
        created_at=created_at,
        input_hash=digest,
        input_snapshot=snapshot,
        compensation=matrix,
        diagnostics=details,
        warnings=warnings,
    )


def preview(workspace, result, engine, primary, secondary):
    request = result.request
    labels = autospill.output_names(request)
    if primary not in labels or secondary not in labels or primary == secondary:
        raise ValueError("Choose distinct primary and secondary calculation outputs")
    i, j = labels.index(primary), labels.index(secondary)
    control = result.diagnostics["controls"][i]
    sample = engine.sample(workspace, control["sample_id"])
    with engine.store.data_path(workspace.id, sample.id).open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != sample.sha256:
        raise ValueError("Acquired control data failed its integrity check")
    ids = np.asarray(control["preview"]["event_ids"], dtype=int)
    if np.any(ids < 0) or np.any(ids >= sample.event_count):
        raise ValueError("Preview event identity does not match the control")
    acquired = [c.name for c in sample.acquisition_channels]
    raw = engine.raw(workspace, sample)[ids][:, [acquired.index(n) for n in request.detectors]]
    if not np.isfinite(raw).all():
        raise ValueError("Preview acquired events are no longer finite")
    unmixed = compensate(raw, result.compensation)
    x = request.detectors.index(request.controls[i].primary_detector)
    y = request.detectors.index(request.controls[j].primary_detector)
    return dict(
        x=primary,
        y=secondary,
        raw_x=request.detectors[x],
        raw_y=request.detectors[y],
        raw=raw[:, [x, y]].tolist(),
        compensated=unmixed[:, [i, j]].tolist(),
        event_ids=ids.tolist(),
    )
