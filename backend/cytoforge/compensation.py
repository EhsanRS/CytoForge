"""Single-stain median-difference calculation in acquired detector coordinates.

This is the conventional positive-minus-negative estimator, not AutoSpill.
Spectral signatures have unit peak response and retain negative coefficients.
"""

from __future__ import annotations

import hashlib
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from . import quality
from .acquired_gates import acquired_gate_copies
from .autofluorescence import reference_review
from .fileio import close_array
from .models import (
    Channel,
    Compensation,
    Gate,
    GateDimension,
    Id,
    Model,
    Name,
    Sample,
    Transform,
    Workspace,
)
from .science import Engine, validate_matrix


class ControlPopulation(Model):
    sample_id: Id
    gate_id: Id | None = None
    threshold_channel: Name | None = None
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def valid_threshold(self):
        if (self.minimum is not None or self.maximum is not None) != bool(self.threshold_channel):
            raise ValueError("A raw threshold needs a channel and at least one bound")
        if self.minimum is not None and self.maximum is not None and self.minimum >= self.maximum:
            raise ValueError("Raw threshold bounds must increase")
        return self


class SingleStainControl(Model):
    name: Name
    primary_detector: Name | None = None
    positive: ControlPopulation
    negative: ControlPopulation


class AutofluorescenceControl(Model):
    name: Name = "Autofluorescence"
    population: ControlPopulation


class ControlCalculation(Model):
    revision: int = Field(ge=0)
    name: Name
    kind: Literal["spillover", "spectral"] = "spillover"
    detectors: list[Name] = Field(min_length=1, max_length=512)
    controls: list[SingleStainControl] = Field(min_length=1, max_length=512)
    min_events: int = Field(default=100, ge=20, le=100000)
    background: list[float] = Field(default_factory=list, max_length=512)
    weights: list[float] = Field(default_factory=list, max_length=512)
    autofluorescence: AutofluorescenceControl | None = None
    autofluorescence_controls: list[AutofluorescenceControl] = Field(
        default_factory=list, max_length=512
    )

    @model_validator(mode="after")
    def valid_controls(self):
        if len(set(self.detectors)) != len(self.detectors):
            raise ValueError("Select unique detectors")
        names = [c.name for c in self.controls]
        if self.autofluorescence:
            names.append(self.autofluorescence.name)
        names.extend(c.name for c in self.autofluorescence_controls)
        if self.autofluorescence and self.autofluorescence_controls:
            raise ValueError("Use either autofluorescence or autofluorescence_controls")
        if len(set(names)) != len(names):
            raise ValueError("Control output names must be unique")
        if self.kind == "spillover":
            if (
                self.autofluorescence
                or self.autofluorescence_controls
                or self.background
                or self.weights
            ):
                raise ValueError(
                    "Autofluorescence, background and weights require spectral unmixing"
                )
            primary = [c.primary_detector for c in self.controls]
            if len(primary) != len(self.detectors) or set(primary) != set(self.detectors):
                raise ValueError(
                    "Spillover requires one single-stain control per selected detector"
                )
        if len(names) > len(self.detectors):
            raise ValueError("More spectral sources than detectors cannot be unmixed uniquely")
        if self.background and len(self.background) != len(self.detectors):
            raise ValueError("Background must match the selected detectors")
        if self.weights and (len(self.weights) != len(self.detectors) or min(self.weights) <= 0):
            raise ValueError("Weights must be positive and match the selected detectors")
        for control in self.controls:
            if control.primary_detector and control.primary_detector not in self.detectors:
                raise ValueError("A control's primary detector must be selected")
        return self


def assign_matrix(sample: Sample, matrix: Compensation | None):
    """Append virtual spectral outputs without altering immutable acquired arrays."""
    if matrix is None:
        sample.compensation_id = None
        return
    acquired = {c.name for c in sample.acquisition_channels}
    if not set(matrix.detectors) <= acquired:
        raise ValueError(f"Matrix detectors are missing from {sample.name}")
    if matrix.kind == "spectral":
        other = {c.name for c in sample.channels} - set(sample.unmixed_parameters)
        if set(matrix.outputs) & other:
            raise ValueError(
                f"Spectral outputs must have distinct names from acquired, formula and analysis "
                f"parameters in {sample.name}"
            )
        for name in matrix.outputs:
            if name not in sample.unmixed_parameters:
                sample.unmixed_parameters.append(name)
                sample.channels.append(Channel(name=name, label=name))
    sample.compensation_id = matrix.id


def calculate_controls(workspace: Workspace, request: ControlCalculation, engine: Engine) -> dict:
    warnings: list[str] = []
    snapshots: dict[str, dict] = {}
    gate_definitions: dict[str, dict] = {}
    quality_inputs: dict[str, dict] = {}
    verified_samples: set[str] = set()
    gates = {g.id: g for g in workspace.gates}

    def population(selection: ControlPopulation, label: str):
        sample = engine.sample(workspace, selection.sample_id)
        acquired = [c.name for c in sample.acquisition_channels]
        if not set(request.detectors) <= set(acquired):
            raise ValueError(f"{label}: selected detectors are absent in {sample.name}")
        if sample.id not in verified_samples:
            with engine.store.data_path(workspace.id, sample.id).open("rb") as handle:
                if hashlib.file_digest(handle, "sha256").hexdigest() != sample.sha256:
                    raise ValueError("Acquired control data failed its integrity check")
            verified_samples.add(sample.id)
        visiting = set()

        def visit(gate_id):
            if not gate_id:
                return
            gate = gates.get(gate_id)
            if not gate or gate.sample_id != sample.id:
                raise ValueError(f"{label}: population belongs to a different sample")
            if gate_id in visiting:
                raise ValueError(f"{label}: population dependencies contain a cycle")
            if gate_id in gate_definitions:
                return
            visiting.add(gate_id)
            dimension_channels = (
                [n for dim in gate.dimensions for n in dim.ratio_channels or (dim.channel,)]
                if gate.dimensions
                else [n for n in (gate.x, gate.y) if n]
            )
            if not set(dimension_channels) <= set(acquired):
                raise ValueError(f"{label}: control gates must use acquired detector parameters")
            for dependency in ([gate.parent_id] if gate.parent_id else []) + gate.operands:
                visit(dependency)
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
                        f"{label}: QC population is missing or belongs to another sample"
                    )
                if not quality.is_captured_gate(gate, result) and quality.is_stale(
                    workspace, result
                ):
                    raise ValueError(f"{label}: QC population is stale; review current QC first")
                if any(index >= len(result.bins) for index in gate.quality_excluded_bins):
                    raise ValueError(f"{label}: QC exclusion references a missing acquisition bin")
                close_array(quality.load_data(engine.store, workspace.id, result))
                quality_inputs[result.id] = dict(
                    id=result.id, input_hash=result.input_hash, data=result.data.model_dump()
                )
            gate_definitions[gate_id] = gate.model_dump()
            visiting.remove(gate_id)

        visit(selection.gate_id)
        mask = engine.mask(workspace, sample, selection.gate_id, compensated=False).copy()
        raw = engine.raw(workspace, sample)
        if selection.threshold_channel:
            if selection.threshold_channel not in acquired:
                raise ValueError(f"{label}: thresholds must use acquired parameters")
            threshold = raw[:, acquired.index(selection.threshold_channel)]
            mask &= np.isfinite(threshold)
            if selection.minimum is not None:
                mask &= threshold >= selection.minimum
            if selection.maximum is not None:
                mask &= threshold < selection.maximum
        count = int(mask.sum())
        indices = [acquired.index(n) for n in request.detectors]
        selected = raw[np.ix_(np.flatnonzero(mask), indices)]
        finite = np.isfinite(selected).all(axis=1)
        if not np.all(finite):
            selected = selected[finite]
        if len(selected) < request.min_events:
            raise ValueError(
                f"{label}: {len(selected)} finite events; at least {request.min_events} required"
            )
        if len(selected) < count:
            warnings.append(f"{label}: excluded {count - len(selected)} nonfinite events")
        snapshots[sample.id] = {
            "sample_id": sample.id,
            "sha256": sample.sha256,
            "acquisition_channels": acquired,
            "event_count": sample.event_count,
        }
        medians = np.median(selected, axis=0)
        mad = 1.4826 * np.median(np.abs(selected - medians), axis=0)
        # Detector ranges are upper acquisition limits, not display extents.
        ranges = np.array([sample.acquisition_channels[i].range for i in indices])
        saturated = (
            np.count_nonzero(selected >= ranges * 0.9999, axis=0)
            if sample.source == "FCS"
            else np.zeros(len(indices), dtype=int)
        )
        for i in np.flatnonzero(saturated / len(selected) >= 0.01):
            warnings.append(
                f"{label}: {100 * saturated[i] / len(selected):.1f}% of events are near the "
                f"{request.detectors[i]} acquisition range; inspect saturation"
            )
        return (
            sample,
            mask,
            selected,
            {
                "sample_id": sample.id,
                "gate_id": selection.gate_id,
                "count": count,
                "finite_count": len(selected),
                "median": medians.tolist(),
                "robust_sd": mad.tolist(),
            },
        )

    rows, diagnostics, outputs = [], [], []
    controls = request.controls
    if request.kind == "spillover":
        controls = sorted(controls, key=lambda c: request.detectors.index(c.primary_detector))
    for control in controls:
        pos_sample, pos_mask, positive, pos = population(
            control.positive, f"{control.name} positive"
        )
        neg_sample, neg_mask, negative, neg = population(
            control.negative, f"{control.name} negative"
        )
        if pos_sample.id == neg_sample.id and np.any(pos_mask & neg_mask):
            raise ValueError(f"{control.name}: positive and negative populations overlap")
        delta = np.asarray(pos["median"]) - np.asarray(neg["median"])
        primary = (
            request.detectors.index(control.primary_detector)
            if control.primary_detector
            else int(np.argmax(delta))
        )
        normalization = primary if request.kind == "spillover" else int(np.argmax(delta))
        signal = float(delta[normalization])
        scale = max(1.0, abs(pos["median"][normalization]), abs(neg["median"][normalization]))
        if signal <= 1e-10 * scale:
            raise ValueError(f"{control.name}: no positive separation from the negative control")
        noise = max(pos["robust_sd"][normalization], neg["robust_sd"][normalization])
        separation = signal / noise if noise > 0 else None
        if separation is not None and separation < 3:
            warnings.append(
                f"{control.name}: weak positive/negative separation ({separation:.2g} robust SD)"
            )
        row = delta / signal
        if np.any(row < 0):
            warnings.append(
                f"{control.name}: negative coefficients retained; inspect background matching"
            )
        output = control.primary_detector if request.kind == "spillover" else control.name
        outputs.append(output)
        rows.append(row.tolist())
        secondary = int(np.argmax(np.abs(np.where(np.arange(len(delta)) == primary, 0, row))))

        def points(values, axes=(primary, secondary)):
            # Deterministic preview sampling; medians use every finite selected event.
            idx = np.linspace(0, len(values) - 1, min(200, len(values)), dtype=int)
            return values[idx][:, axes].tolist()

        diagnostics.append(
            {
                "name": control.name,
                "output": output,
                "positive": pos,
                "negative": neg,
                "delta": delta.tolist(),
                "signature": row.tolist(),
                "normalization_detector": request.detectors[normalization],
                "normalization_signal": signal,
                "separation_robust_sd": separation,
                "preview": {
                    "x": request.detectors[primary],
                    "y": request.detectors[secondary],
                    "positive": points(positive),
                    "negative": points(negative),
                },
            }
        )
    af_controls = request.autofluorescence_controls or (
        [request.autofluorescence] if request.autofluorescence else []
    )
    for af in af_controls:
        _, _, _, details = population(af.population, af.name)
        response = np.asarray(details["median"]) - (
            np.asarray(request.background) if request.background else 0
        )
        peak = float(np.max(response))
        if peak <= 1e-10 * max(1.0, float(np.max(np.abs(response)))):
            raise ValueError(
                f"{af.name}: autofluorescence reference has no positive signal above background"
            )
        rows.append((response / peak).tolist())
        outputs.append(af.name)
        diagnostics.append(
            {
                "name": af.name,
                "output": af.name,
                "positive": details,
                "negative": None,
                "delta": response.tolist(),
                "signature": rows[-1],
                "normalization_detector": request.detectors[int(np.argmax(response))],
                "normalization_signal": peak,
                "separation_robust_sd": None,
                "preview": None,
            }
        )
    matrix = Compensation(
        name=request.name,
        kind=request.kind,
        detectors=request.detectors,
        outputs=outputs,
        matrix=rows,
        background=request.background,
        weights=request.weights,
        source="Single-stain median differences",
    )
    condition = validate_matrix(matrix)
    af_review, af_warnings = reference_review(matrix, [c.name for c in af_controls])
    warnings.extend(af_warnings)
    values = np.asarray(rows)
    normalized = values / np.linalg.norm(values, axis=1, keepdims=True)
    similarities = normalized @ normalized.T
    if condition > 100:
        warnings.append(f"Matrix condition number {condition:.3g}: detector noise may be amplified")
    for i in range(len(outputs)):
        for j in range(i):
            if similarities[i, j] > 0.98:
                warnings.append(
                    f"{outputs[j]} and {outputs[i]} have very similar reference signatures"
                )
    matrix.provenance = {
        "kind": "control_calculation",
        "method": "median_difference",
        "version": 1,
        "workspace_id": workspace.id,
        "source_revision": workspace.revision,
        "request": request.model_dump(
            exclude={"autofluorescence_controls"}
            if not request.autofluorescence_controls
            else set()
        ),
        "samples": list(snapshots.values()),
        "gates": list(gate_definitions.values()),
        "controls": diagnostics,
        "calculated_matrix": rows,
        "calculated_definition": matrix.model_dump(exclude={"provenance"}),
        "autofluorescence_sources": af_review,
    }
    if quality_inputs:
        matrix.provenance["quality_inputs"] = quality_inputs
    return {
        "revision": workspace.revision,
        "compensation": matrix.model_dump(),
        "diagnostics": {
            "condition_number": condition,
            "rank": len(rows),
            "autofluorescence_sources": af_review,
            "signature_cosines": similarities.tolist(),
            "controls": diagnostics,
            "output_units": "Primary-detector equivalent intensity"
            if request.kind == "spillover"
            else "Peak-detector equivalent intensity (unit peak reference signatures)",
        },
        "warnings": warnings,
    }


def save_control_populations(workspace, matrix, store):
    """Save the reviewed median controls with raw parents and inline thresholds."""
    provenance = matrix.provenance
    if (
        provenance.get("kind") != "control_calculation"
        or provenance.get("workspace_id") != workspace.id
    ):
        raise ValueError("Saving control populations requires a calculation from this workspace")
    request = ControlCalculation.model_validate(provenance["request"])
    references = request.autofluorescence_controls or (
        [request.autofluorescence] if request.autofluorescence else []
    )
    outputs = [
        control.name if request.kind == "spectral" else control.primary_detector
        for control in request.controls
    ] + [reference.name for reference in references]
    if request.kind != matrix.kind or set(outputs) != set(matrix.outputs):
        raise ValueError("Calculated output names changed; recalculate before saving populations")
    for snapshot in provenance["samples"]:
        sample = next((s for s in workspace.samples if s.id == snapshot["sample_id"]), None)
        if (
            sample is None
            or sample.sha256 != snapshot["sha256"]
            or sample.event_count != snapshot["event_count"]
            or [c.name for c in sample.acquisition_channels] != snapshot["acquisition_channels"]
        ):
            raise ValueError("Control acquisition changed; recalculate before saving populations")
        with store.data_path(workspace.id, sample.id).open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != sample.sha256:
                raise ValueError("Acquired control data failed its integrity check")
    for snapshot in provenance["gates"]:
        original = Gate.model_validate(snapshot)
        current = next((g for g in workspace.gates if g.id == original.id), None)
        if current is None or current != original:
            raise ValueError("Control populations changed; recalculate before saving")
        if current.membership is not None:
            from .population_snapshot import packed_data

            verifier = Engine(store, cache_bytes=32 * 1024**2)
            packed_data(
                verifier,
                workspace,
                verifier.sample(workspace, current.sample_id),
                current.membership,
            )
    for identifier, snapshot in provenance.get("quality_inputs", {}).items():
        result = next((q for q in workspace.quality_results if q.id == identifier), None)
        if (
            result is None
            or result.input_hash != snapshot["input_hash"]
            or result.data.model_dump() != snapshot["data"]
        ):
            raise ValueError("Reviewed QC data changed; recalculate before saving populations")
        close_array(quality.load_data(store, workspace.id, result))
    append, parent, created = acquired_gate_copies(
        workspace, matrix.name, dict(control_matrix_id=matrix.id)
    )

    def capture(selection, label):
        parent_id = parent(selection.gate_id)
        if selection.threshold_channel:
            gate = Gate(
                sample_id=selection.sample_id,
                parent_id=parent_id,
                name="Raw threshold",
                kind="hyperrectangle",
                dimensions=[
                    GateDimension(
                        channel=selection.threshold_channel,
                        compensation_ref="uncompensated",
                        transform=Transform(kind="linear"),
                        minimum=selection.minimum,
                        maximum=selection.maximum,
                    )
                ],
            )
            parent_id = append(gate, f"{matrix.name[:64]} · {label[:64]} · Raw threshold")
        return dict(sample_id=selection.sample_id, gate_id=parent_id)

    positives, negatives = [], []
    for control in request.controls:
        output = control.name if request.kind == "spectral" else control.primary_detector
        positives.append(
            dict(output=output, **capture(control.positive, f"{control.name} positive"))
        )
        negatives.append(
            dict(output=output, **capture(control.negative, f"{control.name} negative"))
        )
    for reference in references:
        positives.append(
            dict(output=reference.name, **capture(reference.population, reference.name))
        )
    matrix.provenance.update(
        control_populations=positives, negative_populations=negatives, helper_gate_ids=created
    )
