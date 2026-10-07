"""Spillover spreading diagnostics from the published two-regression method.

Roca et al. (2021), doi:10.1038/s41467-021-23126-8, Methods, equations 11–12.
This independent implementation is not the binary implementation in FlowJo.
All fits use linear compensated/unmixed intensities, never display transforms.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import Field, model_validator
from scipy.stats import f as f_distribution

from . import quality
from .analysis import atomic_json, gate_signature, watch_parent
from .models import Compensation, Id, Model, Name, Workspace, new_id
from .science import Engine, validate_matrix
from .store import Store, now

METHOD_VERSION = "autospread-two-regressions-1"
REFERENCE = "https://doi.org/10.1038/s41467-021-23126-8"
MAX_VALUES = 64_000_000


class SpreadControl(Model):
    output: Name
    sample_id: Id
    gate_id: Id | None = None


class AutoSpreadRequest(Model):
    revision: int = Field(ge=0)
    algorithm: Literal["autospread"] = "autospread"
    name: Name = "Spillover spreading"
    matrix_id: Id
    controls: list[SpreadControl] = Field(min_length=1, max_length=64)
    quantiles: int = Field(default=256, ge=8, le=256)
    events_per_bin: int = Field(default=100, ge=20, le=10000)
    significance: float = Field(default=0.05, gt=0, lt=1)
    max_events: int | None = Field(default=None, ge=160, le=2000000)

    @model_validator(mode="after")
    def valid_controls(self):
        if len({c.output for c in self.controls}) != len(self.controls):
            raise ValueError("Select at most one single-color control per output")
        if self.max_events is not None and self.max_events < 8 * self.events_per_bin:
            raise ValueError("The event limit must provide at least eight complete quantile bins")
        return self


class AutoSpreadResult(Model):
    id: Id = Field(default_factory=new_id)
    request: AutoSpreadRequest
    created_at: str
    input_hash: str
    input_snapshot: dict
    outputs: list[Name]
    primaries: list[Name]
    matrix: list[list[float | None]]
    controls: list[dict]
    warnings: list[str]
    method: str = METHOD_VERSION
    reference: str = REFERENCE

    @model_validator(mode="after")
    def valid_report(self):
        if self.primaries != [c.output for c in self.request.controls] or not (
            len(self.matrix) == len(self.controls) == len(self.primaries)
        ):
            raise ValueError("Spreading rows must match the selected primary controls")
        if len(set(self.outputs)) != len(self.outputs) or not set(self.primaries) <= set(
            self.outputs
        ):
            raise ValueError("Spreading output identities are inconsistent")
        for primary, row, control, selection in zip(
            self.primaries, self.matrix, self.controls, self.request.controls, strict=True
        ):
            if len(row) != len(self.outputs) or any(
                v is not None and (not np.isfinite(v) or v < 0) for v in row
            ):
                raise ValueError("Spreading matrix has invalid dimensions or coefficients")
            if (control.get("output"), control.get("sample_id"), control.get("gate_id")) != (
                primary,
                selection.sample_id,
                selection.gate_id,
            ):
                raise ValueError("Spreading control identity is inconsistent")
            bins = control.get("bin_count", 0)
            counts = control.get("bin_counts", [])
            if (
                not 8 <= bins <= self.request.quantiles
                or len(counts) != bins
                or any(not isinstance(n, int) or n < self.request.events_per_bin for n in counts)
                or sum(counts) != control.get("used_count")
            ):
                raise ValueError("Spreading quantile counts are inconsistent")
            if not (
                sum(counts) <= control.get("finite_count", -1) <= control.get("parent_count", -1)
            ):
                raise ValueError("Spreading event counts are inconsistent")
            medians = control.get("primary_medians", [])
            if len(medians) != bins or not np.isfinite(medians).all():
                raise ValueError("Spreading primary quantiles are invalid")
            pairs = {p.get("output"): p for p in control.get("pairs", [])}
            if len(pairs) != len(self.outputs) - 1 or set(pairs) != set(self.outputs) - {primary}:
                raise ValueError("Spreading secondary outputs are inconsistent")
            for output, coefficient in zip(self.outputs, row, strict=True):
                if output == primary:
                    if coefficient is not None:
                        raise ValueError("Self-spreading must be unestimated")
                    continue
                pair = pairs[output]
                if coefficient != pair.get("coefficient"):
                    raise ValueError("Spreading matrix differs from its regression diagnostics")
                for key in ("robust_sd", "adjusted_sd", "secondary_medians"):
                    values = pair.get(key, [])
                    if len(values) != bins or not np.isfinite(values).all():
                        raise ValueError("Spreading secondary quantiles are invalid")
                final = pair.get("final_fit", {})
                if final.get("intercept") != 0 or final.get("df") != bins - 1:
                    raise ValueError("Spreading requires the second regression through the origin")
        return self


def selected_matrix(workspace, request) -> Compensation:
    matrix = next((m for m in workspace.compensations if m.id == request.matrix_id), None)
    if matrix is None:
        raise ValueError("Save a compensation or spectral matrix before calculating spreading")
    validate_matrix(matrix)
    if not 2 <= len(matrix.outputs) <= 64:
        raise ValueError("Spreading requires a matrix with 2–64 output parameters")
    if not {c.output for c in request.controls} <= set(matrix.outputs):
        raise ValueError("Every control primary must be an output of the selected matrix")
    return matrix


def input_snapshot(workspace: Workspace, request: AutoSpreadRequest) -> dict:
    matrix = selected_matrix(workspace, request)
    samples, gates, qc = {}, {}, {}
    by_gate = {g.id: g for g in workspace.gates}
    for control in request.controls:
        sample = next((s for s in workspace.samples if s.id == control.sample_id), None)
        if sample is None:
            raise ValueError(f"{control.output}: control sample no longer exists")
        acquired = {c.name for c in sample.acquisition_channels}
        if not set(matrix.detectors) <= acquired:
            raise ValueError(f"{control.output}: selected matrix detectors are missing")
        visiting = set()

        def visit(identifier, sample=sample, acquired=acquired, visiting=visiting):
            if not identifier:
                return
            gate = by_gate.get(identifier)
            if gate is None or gate.sample_id != sample.id:
                raise ValueError("Control populations must belong to their sample")
            if identifier in visiting:
                raise ValueError("Control population dependencies contain a cycle")
            if identifier in gates:
                return
            visiting.add(identifier)
            visit(gate.parent_id)
            for operand in gate.operands:
                visit(operand)
            names = (
                {n for d in gate.dimensions for n in d.ratio_channels or [d.channel]}
                if gate.dimensions
                else {n for n in (gate.x, gate.y) if n}
            )
            if not names <= acquired:
                raise ValueError("Control gates must use acquired parameters, including ratios")
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
                if not quality.is_captured_gate(gate, result) and quality.is_stale(
                    workspace, result
                ):
                    raise ValueError("Control QC population is missing or stale; review QC first")
                qc[result.id] = {
                    "id": result.id,
                    "input_hash": result.input_hash,
                    "data": result.data.model_dump(),
                }
            gates[identifier] = gate_signature(gate)
            gates[identifier].pop("graph_style", None)
            visiting.remove(identifier)

        visit(control.gate_id)
        samples[sample.id] = {
            "id": sample.id,
            "sha256": sample.sha256,
            "event_count": sample.event_count,
            "acquisition_order": [c.name for c in sample.acquisition_channels],
        }
    settings = request.model_dump(exclude={"revision", "name", "controls"})
    settings["controls"] = [c.model_dump() for c in request.controls]
    return {
        "method": METHOD_VERSION,
        "settings": settings,
        "matrix": matrix.model_dump(
            include={"id", "kind", "detectors", "outputs", "matrix", "background", "weights"}
        ),
        "samples": samples,
        "gates": gates,
        "qc": qc,
    }


def input_hash(workspace, request):
    return hashlib.sha256(
        json.dumps(input_snapshot(workspace, request), sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def validate_request(workspace, request):
    input_snapshot(workspace, request)


def is_stale(workspace, result):
    try:
        return input_hash(workspace, result.request) != result.input_hash
    except (ValueError, KeyError, StopIteration):
        return True


def signed_root(values):
    """sign(x) * (sqrt(abs(x)+1)-1), avoiding cancellation near zero."""
    values = np.asarray(values, dtype=np.float64)
    return values / (np.sqrt(np.abs(values) + 1) + 1)


def linear_fit(x, y, intercept=True):
    """OLS with scaled coordinates and the appropriate one-term F test."""
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if len(x) < 3 or len(x) != len(y) or not np.isfinite([x, y]).all():
        raise ValueError("Spreading regressions require at least three finite paired bins")
    mean_x, mean_y = (float(x.mean()), float(y.mean())) if intercept else (0.0, 0.0)
    scale_x = float(np.max(np.abs(x - mean_x)))
    scale_y = float(np.max(np.abs(y - mean_y)))
    if scale_x == 0:
        raise ValueError("Primary quantile medians have no variation; use a brighter control")
    xn, yn = (x - mean_x) / scale_x, (y - mean_y) / (scale_y or 1)
    xx = float(xn @ xn)
    beta = float(xn @ yn) / xx
    residual = yn - beta * xn
    residual_ss, regression_ss = float(residual @ residual), beta * beta * xx
    df = len(x) - (2 if intercept else 1)
    statistic = (
        regression_ss / residual_ss * df
        if residual_ss
        else np.finfo(float).max
        if regression_ss
        else 0.0
    )
    statistic = min(statistic, float(np.finfo(float).max))
    slope = beta * (scale_y or 1) / scale_x
    offset = mean_y - slope * mean_x
    if not np.isfinite([slope, offset, statistic]).all():
        raise ValueError("Spreading regression exceeds finite numeric range")
    return {
        "slope": slope,
        "intercept": offset,
        "f_statistic": statistic,
        "p_value": float(f_distribution.sf(statistic, 1, df)),
        "df": df,
        "r_squared": regression_ss / (regression_ss + residual_ss)
        if regression_ss + residual_ss
        else 0.0,
    }


def spreading_pair(primary, secondary, significance):
    transformed = signed_root(primary)
    initial = linear_fit(transformed, secondary)
    baseline = initial["intercept"]
    with np.errstate(over="ignore", invalid="ignore"):
        adjusted = signed_root(np.square(secondary) - baseline * baseline)
    if not np.isfinite(adjusted).all():
        raise ValueError("Spreading variance exceeds finite numeric range")
    final = linear_fit(transformed, adjusted, intercept=False)
    status = (
        "negative"
        if final["slope"] <= 0
        else "not_significant"
        if final["p_value"] >= significance
        else "positive"
    )
    return {
        "coefficient": final["slope"] if status == "positive" else 0.0,
        "status": status,
        "baseline_noise": baseline,
        "initial_fit": initial,
        "final_fit": final,
        "robust_sd": secondary.tolist(),
        "adjusted_sd": adjusted.tolist(),
    }


def unmixing_operator(matrix):
    values = np.asarray(matrix.matrix, dtype=float)
    if matrix.kind == "spillover":
        return np.linalg.solve(values, np.eye(len(values))), np.zeros(len(values))
    weights = np.sqrt(matrix.weights) if matrix.weights else np.ones(len(matrix.detectors))
    operator = weights[:, None] * np.linalg.pinv(values.T * weights[:, None]).T
    background = np.asarray(matrix.background) if matrix.background else np.zeros(len(weights))
    return operator, background


def verify_data(workspace, snapshot, store):
    files = [
        (store.data_path(workspace.id, key), value["sha256"])
        for key, value in snapshot["samples"].items()
    ]
    files.extend(
        (store.quality_path(workspace.id, key), value["data"]["sha256"])
        for key, value in snapshot["qc"].items()
    )
    for path, expected in files:
        try:
            with path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
        except OSError as exc:
            raise ValueError(
                "Control event data is missing or unreadable; restore its project"
            ) from exc
        if digest != expected:
            raise ValueError("Control event data failed its integrity check")


def calculate(
    workspace, request, engine, identifier=None, progress=lambda *_: None, check=lambda: None
):
    snapshot = input_snapshot(workspace, request)
    verify_data(workspace, snapshot, engine.store)
    matrix = selected_matrix(workspace, request)
    operator, background = unmixing_operator(matrix)
    outputs, reports, rows, warnings = matrix.outputs, [], [], []
    for number, control in enumerate(request.controls):
        check()
        sample = engine.sample(workspace, control.sample_id)
        raw = engine.raw(workspace, sample)
        acquired = [c.name for c in sample.acquisition_channels]
        columns = [acquired.index(n) for n in matrix.detectors]
        ids = np.flatnonzero(engine.mask(workspace, sample, control.gate_id, compensated=False))
        parent_count = len(ids)
        finite = np.empty(len(ids), dtype=bool)
        chunk_rows = max(1, min(65536, 2_000_000 // len(columns)))
        for start in range(0, len(ids), chunk_rows):
            check()
            finite[start : start + chunk_rows] = np.isfinite(
                raw[np.ix_(ids[start : start + chunk_rows], columns)]
            ).all(axis=1)
        ids = ids[finite]
        finite_count = len(ids)
        if not finite.all():
            warnings.append(
                f"{control.output}: {parent_count - finite_count} nonfinite events excluded"
            )
        if request.max_events is not None and len(ids) > request.max_events:
            ids = ids[np.linspace(0, len(ids) - 1, request.max_events, dtype=int)]
            warnings.append(
                f"{control.output}: used {len(ids)} uniformly spaced event IDs of "
                f"{finite_count} finite events"
            )
        bins = min(request.quantiles, len(ids) // request.events_per_bin)
        if bins < 8:
            raise ValueError(
                f"{control.output}: need at least {8 * request.events_per_bin} finite "
                "events for eight quantile bins"
            )
        if len(ids) * len(outputs) > MAX_VALUES:
            raise ValueError(
                "Selected control exceeds the spreading memory limit; select a "
                "smaller population or set an explicit event limit"
            )
        values = np.empty((len(ids), len(outputs)), dtype=float)
        for start in range(0, len(ids), chunk_rows):
            check()
            values[start : start + chunk_rows] = (
                raw[np.ix_(ids[start : start + chunk_rows], columns)] - background
            ) @ operator
            progress(
                f"Unmixing {control.output}",
                0.02
                + 0.9
                * (number + 0.35 * min(1, (start + chunk_rows) / len(ids)))
                / len(request.controls),
            )
        if not np.isfinite(values).all():
            raise ValueError(f"{control.output}: unmixing produced nonfinite intensities")
        primary = outputs.index(control.output)
        order = np.argsort(values[:, primary], kind="stable")
        partitions = np.array_split(order, bins)
        primary_medians = np.array([np.median(values[p, primary]) for p in partitions])
        # Reject tied or off-scale primaries before the through-origin fit can hide them.
        linear_fit(signed_root(primary_medians), primary_medians)
        report = {
            "output": control.output,
            "sample_id": sample.id,
            "gate_id": control.gate_id,
            "parent_count": parent_count,
            "finite_count": finite_count,
            "used_count": len(ids),
            "bin_count": bins,
            "bin_counts": [len(p) for p in partitions],
            "primary_medians": primary_medians.tolist(),
            "pairs": [],
            "used_event_ids_sha256": hashlib.sha256(
                np.asarray(ids, dtype="<i8").tobytes()
            ).hexdigest(),
        }
        row = []
        for secondary, output in enumerate(outputs):
            check()
            if secondary == primary:
                row.append(None)
                continue
            medians, deviations = [], []
            for partition in partitions:
                q50, q84 = np.percentile(values[partition, secondary], [50, 84], method="linear")
                medians.append(q50)
                deviations.append(q84 - q50)
            pair = spreading_pair(primary_medians, np.asarray(deviations), request.significance)
            pair.update(
                output=output,
                secondary_medians=list(map(float, medians)),
                median_signal_fit=linear_fit(primary_medians, medians),
            )
            report["pairs"].append(pair)
            row.append(pair["coefficient"])
            if pair["baseline_noise"] < 0:
                warnings.append(
                    f"{control.output} → {output}: negative fitted baseline noise; "
                    "inspect control range and regression diagnostics"
                )
            fit = pair["median_signal_fit"]
            if abs(fit["slope"]) >= 0.01 and fit["p_value"] < request.significance:
                warnings.append(
                    f"{control.output} → {output}: secondary median varies with primary "
                    "signal; inspect compensation and autofluorescence contamination"
                )
            progress(
                f"Fitting {control.output} → {output}",
                0.02
                + 0.9
                * (number + 0.35 + 0.65 * (secondary + 1) / len(outputs))
                / len(request.controls),
            )
        reports.append(report)
        rows.append(row)
    check()
    return AutoSpreadResult(
        id=identifier or new_id(),
        request=request,
        created_at=now(),
        input_snapshot=snapshot,
        input_hash=hashlib.sha256(
            json.dumps(snapshot, sort_keys=True, allow_nan=False).encode()
        ).hexdigest(),
        outputs=outputs,
        primaries=[c.output for c in request.controls],
        matrix=rows,
        controls=reports,
        warnings=warnings,
    )


def saved_result(matrix):
    value = matrix.provenance.get("autospread")
    if value is None:
        raise ValueError("This matrix has no saved spreading report")
    return AutoSpreadResult.model_validate(value)


def run_autospread(job_dir: str):
    directory, stop, store = Path(job_dir), threading.Event(), None

    def progress(stage, fraction):
        atomic_json(directory / "progress.json", {"stage": stage, "progress": fraction})

    def check():
        if stop.is_set():
            raise InterruptedError("Spreading worker's parent stopped")

    try:
        payload = json.loads((directory / "input.json").read_text())
        threading.Thread(
            target=watch_parent, args=(payload["parent_pid"], stop), daemon=True
        ).start()
        workspace = Workspace.model_validate(payload["workspace"])
        request = AutoSpreadRequest.model_validate(payload["request"])
        store = Store(Path(payload["data_dir"]))
        result = calculate(workspace, request, Engine(store), payload["id"], progress, check)
        if result.input_hash != payload["input_hash"]:
            raise ValueError("Spreading inputs do not match their scientific fingerprint")
        check()
        atomic_json(directory / "result.json", result.model_dump())
        progress("Ready to review spreading", 1)
    except Exception as exc:
        atomic_json(directory / "error.json", {"error": f"{type(exc).__name__}: {exc}"})
    finally:
        stop.set()
        if store:
            store.close()
