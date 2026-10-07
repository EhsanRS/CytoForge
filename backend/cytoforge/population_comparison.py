"""Population distributions, event-pooled controls and independent control baselines."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import zipfile
from collections import OrderedDict
from copy import deepcopy
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from . import population_statistics as statistics
from .analysis import _sample_signature, atomic_json, watch_parent
from .comparison_storage import JointStorage, PooledColumns
from .models import (
    PopulationComparisonData,
    PopulationComparisonRequest,
    PopulationComparisonResult,
    PopulationComparisonRow,
    Workspace,
)
from .report_sources import SourceAudit
from .science import Engine
from .store import Store, now
from .virtual_groups import parameter_meaning

METHOD = "roederer-2001-bagwell-ens1-ecdf-1"
MAX_ARTIFACT_BYTES = 256 * 1024**2
MAX_BASELINE_TREES = 8


def key(source):
    return source.sample_id, source.gate_id


def names(request):
    return sorted({n for p in request.parameters for n in p.ratio_channels or (p.channel,)})


def validate_request(workspace, request):
    samples = {s.id: s for s in workspace.samples}
    gates = {g.id: g for g in workspace.gates}
    matrices = {m.id: m for m in workspace.compensations}
    meanings = {}
    audit = SourceAudit(workspace)
    for source in [*request.inputs, *request.controls]:
        sample = samples.get(source.sample_id)
        if sample is None:
            raise ValueError("A comparison population belongs to a missing acquisition")
        if source.gate_id:
            gate = gates.get(source.gate_id)
            if gate is None or gate.sample_id != sample.id:
                raise ValueError("A comparison population must belong to its actual acquisition")
            if audit.population_stale(source.gate_id):
                raise ValueError(f"{sample.name}: refit the selected population before comparison")
        channels = {c.name for c in sample.channels}
        for name in names(request):
            if name not in channels:
                raise ValueError(f"{sample.name} is missing comparison parameter {name}")
            meaning = parameter_meaning(sample, name)
            if name in meanings and meanings[name] != meaning:
                raise ValueError(f"Comparison parameter {name} has incompatible source definitions")
            meanings[name] = meaning
            if audit.parameter_stale(sample.id, name):
                raise ValueError(f"{sample.name}: refit stale comparison parameter {name}")
        for parameter in request.parameters:
            if parameter.compensation_ref not in {"sample", "uncompensated", "FCS"}:
                matrix = matrices.get(parameter.compensation_ref)
                if matrix is None or not set(matrix.detectors) <= {
                    c.name for c in sample.acquisition_channels
                }:
                    raise ValueError(f"{sample.name}: the fixed comparison matrix is incompatible")


def settings_snapshot(request):
    # Typed defaults must have the same representation before and after worker JSON.
    normalized = PopulationComparisonRequest.model_validate(request.model_dump())
    settings = normalized.model_dump(exclude={"revision", "name", "replace_result_id"})
    for p in settings["parameters"]:
        p.pop("label")
    return settings


def input_snapshot(workspace, request):
    validate_request(workspace, request)
    settings = settings_snapshot(request)
    signature_request = SimpleNamespace(channels=names(request), use_transforms=False)
    signatures = {}
    for source in [*request.inputs, *request.controls]:
        signature = _sample_signature(workspace, signature_request, source)
        for value in [
            signature.get("compensation"),
            *signature.get("gate_compensations", {}).values(),
        ]:
            if value:
                value.pop("name", None)
                value.pop("provenance", None)
        signatures[source.sample_id + "/" + (source.gate_id or "all")] = signature
    extra = {
        m.id: m.model_dump(exclude={"name", "provenance"})
        for m in workspace.compensations
        if any(p.compensation_ref == m.id for p in request.parameters)
    }
    if any(p.compensation_ref == "FCS" for p in request.parameters):
        for sample in workspace.samples:
            if sample.id in {s.sample_id for s in [*request.inputs, *request.controls]}:
                extra["FCS/" + sample.id] = {
                    k: sample.metadata.get(k) for k in ["spill", "spillover"]
                }
    return {"method": METHOD, "settings": settings, "sources": signatures, "matrices": extra}


def input_hash(workspace, request):
    return hashlib.sha256(
        json.dumps(input_snapshot(workspace, request), sort_keys=True).encode()
    ).hexdigest()


def is_stale(workspace, result):
    try:
        return input_hash(workspace, result.request) != result.input_hash
    except (KeyError, ValueError, StopIteration):
        return True


def replacement_target(workspace, request):
    if request.replace_result_id is None:
        return None
    previous = next(
        (r for r in workspace.comparison_results if r.id == request.replace_result_id), None
    )
    if previous is None:
        raise ValueError("The comparison selected for refitting is missing")
    if {key(s) for s in previous.request.inputs} != {key(s) for s in request.inputs} or {
        p.id for p in previous.request.parameters
    } != {p.id for p in request.parameters}:
        raise ValueError("A refit must preserve the target populations and parameter identities")
    return previous


def saved_result(workspace, identifier, follow_replacement=False):
    result = next((r for r in workspace.comparison_results if r.id == identifier), None)
    seen = set()
    while result is not None and follow_replacement and result.id not in seen:
        seen.add(result.id)
        replacements = [
            r
            for r in workspace.comparison_results
            if r.request.replace_result_id == result.id and r.id not in seen
        ]
        if not replacements:
            break
        result = replacements[-1]
    return result


def verify_sources(workspace, request, engine):
    audit = SourceAudit(workspace)
    for source in [*request.inputs, *request.controls]:
        frame = {
            "source": audit.closure(
                source.sample_id,
                names(request),
                [source.gate_id] if source.gate_id else [],
                [p.model_dump() for p in request.parameters],
            )
        }
        if frame["source"]["stale"]:
            raise ValueError("Refit stale source populations and parameters before comparison")
        audit.validate_frame(engine, {"kind": "plot", "layers": [frame]})


def verify_snapshot_sources(workspace, result, engine):
    audit = SourceAudit(workspace)
    for source in result.input_snapshot["sources"].values():
        sample_id = source["sample_id"]
        audit.validate_file(engine.store.data_path(workspace.id, sample_id), source["sha256"])
        for definition in source["channels"].values():
            if "analysis" in definition:
                identifier, _, expected = definition["analysis"]
                audit.validate_file(
                    engine.store.analysis_path(workspace.id, identifier, sample_id), expected
                )
            if "event_flags_sha256" in definition:
                # Quality dependencies are keyed by their durable result identifier.
                for name, item in source["channels"].items():
                    if item is definition and name.startswith("quality:"):
                        audit.validate_file(
                            engine.store.quality_path(workspace.id, name[8:]),
                            definition["event_flags_sha256"],
                        )


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def statistics_hash(rows, joint_rows):
    value = {
        "rows": [r.model_dump() for r in rows],
        "joint_rows": [r.model_dump() for r in joint_rows],
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def write_artifact(store, workspace_id, identifier, arrays):
    path = store.comparison_path(workspace_id, identifier)
    temporary = path.with_suffix(".npz.partial")
    try:
        with temporary.open("wb") as handle:
            np.savez_compressed(handle, **arrays)
            handle.flush()
            os.fsync(handle.fileno())
        if temporary.stat().st_size > MAX_ARTIFACT_BYTES:
            raise ValueError("Comparison plot data exceeds the 256 MiB artifact limit")
        temporary.replace(path)
        return path
    finally:
        temporary.unlink(missing_ok=True)


def load_artifact(store, workspace_id, result):
    if hashlib.sha256(
        json.dumps(result.input_snapshot, sort_keys=True).encode()
    ).hexdigest() != result.input_hash or result.input_snapshot.get(
        "settings"
    ) != settings_snapshot(result.request):
        raise ValueError("Comparison scientific settings failed snapshot validation")
    path = store.comparison_path(workspace_id, result.id)
    if not path.is_file():
        raise ValueError("Comparison plot data is missing; restore the project archive")
    if path.stat().st_size != result.data.bytes or digest(path) != result.data.sha256:
        raise ValueError("Comparison plot data failed its SHA-256 integrity check")
    expected = {
        f"{kind}_{i}"
        for i in range(result.data.parameters)
        for kind in ["edges", "control", "targets", "individual"]
    } | {"metadata"}
    # Inspect NPY headers inside the ZIP before any decompression/allocation.
    with zipfile.ZipFile(path) as archive:
        if len(archive.infolist()) != len(expected) or {
            info.filename for info in archive.infolist()
        } != {k + ".npy" for k in expected}:
            raise ValueError("Comparison artifact contains unexpected arrays")
        for info in archive.infolist():
            if info.file_size > 32 * 1024**2:
                raise ValueError("A comparison array exceeds its declared shape limits")
            with archive.open(info) as handle:
                magic = np.lib.format.read_magic(handle)
                if magic not in {(1, 0), (2, 0)}:
                    raise ValueError("Comparison array uses an unsupported NPY header")
                reader = (
                    np.lib.format.read_array_header_1_0
                    if magic == (1, 0)
                    else np.lib.format.read_array_header_2_0
                )
                shape, _, dtype = reader(handle)
            stem = info.filename[:-4]
            if stem == "metadata":
                valid = dtype == np.dtype("uint8") and len(shape) == 1 and shape[0] <= 8 * 1024**2
            elif stem.startswith("edges_"):
                valid = dtype == np.dtype("float64") and len(shape) == 1 and 2 <= shape[0] <= 1025
            elif stem.startswith("control_"):
                valid = dtype == np.dtype("int64") and len(shape) == 1 and 1 <= shape[0] <= 1024
            else:
                number = (
                    result.data.targets if stem.startswith("targets_") else result.data.controls
                )
                valid = (
                    dtype == np.dtype("int64")
                    and len(shape) == 2
                    and shape[0] == number
                    and 1 <= shape[1] <= 1024
                )
            if not valid:
                raise ValueError("Comparison artifact array has an invalid dtype or shape")
    data = {}
    with np.load(path, allow_pickle=False) as archive:
        for name in expected:
            data[name] = archive[name]
    for i in range(result.data.parameters):
        edges = data[f"edges_{i}"]
        if not np.all(np.isfinite(edges)) or np.any(edges[1:] <= edges[:-1]):
            raise ValueError("Comparison plot edges must be finite and increasing")
        for kind in ["control", "targets", "individual"]:
            array = data[f"{kind}_{i}"]
            if array.shape[-1] != len(edges) - 1 or np.any(array < 0):
                raise ValueError("Comparison plot counts do not match their shared edges")
        parameter_id = result.request.parameters[i].id
        target_rows = {
            key(r.source): r
            for r in result.rows
            if r.role == "target" and r.parameter_id == parameter_id
        }
        for index, source in enumerate(result.request.inputs):
            row = target_rows[key(source)]
            if (
                int(data[f"targets_{i}"][index].sum()) != row.finite_count
                or int(data[f"control_{i}"].sum()) != row.control_finite_count
            ):
                raise ValueError("Comparison histogram counts do not match reported event counts")
    metadata = json.loads(data.pop("metadata").tobytes())
    if metadata.get("statistics_hash") != statistics_hash(result.rows, result.joint_rows):
        raise ValueError("Comparison statistics do not match their immutable artifact")
    if metadata["method"] != METHOD or metadata["request"] != result.request.model_dump():
        # Only cosmetic names/labels may change without changing an immutable plot.
        original = deepcopy(metadata["request"])
        current = result.request.model_dump()
        for value in [original, current]:
            value.pop("name", None)
            for p in value["parameters"]:
                p.pop("label", None)
        if metadata["method"] != METHOD or original != current:
            raise ValueError("Comparison plot settings do not match the saved result")
    return data, metadata


def summary_probability(value):
    return {k: v for k, v in value.items() if k not in {"control_counts", "test_counts", "cuts"}}


def owned_coordinate(column):
    """Retain one raw coordinate without retaining its entire acquisition mapping."""
    base = column
    while base is not None:
        if isinstance(base, np.memmap):
            return np.array(column, copy=True)
        base = getattr(base, "base", None)
    return column


def calculate(
    workspace,
    request,
    engine,
    identifier,
    progress=lambda stage, fraction: None,
    *,
    scratch_directory=None,
):
    directory = (
        Path(scratch_directory) if scratch_directory is not None else engine.store.root / "tmp"
    )
    with JointStorage(
        directory, len(request.parameters), request.joint and len(request.parameters) > 1
    ) as joint:
        return _calculate(workspace, request, engine, identifier, progress, joint)


def _calculate(workspace, request, engine, identifier, progress, joint):
    started = time.monotonic()
    snapshot = input_snapshot(workspace, request)
    verify_sources(workspace, request, engine)
    samples = {s.id: s for s in workspace.samples}
    sources = {key(s): s for s in [*request.inputs, *request.controls]}
    masks = {k: engine.mask(workspace, samples[k[0]], k[1]) for k in sources}
    control_masks = {}
    for source in request.controls:
        mask = masks[key(source)]
        if source.sample_id not in control_masks:
            control_masks[source.sample_id] = mask.copy()
        else:
            control_masks[source.sample_id] |= mask
    rows, joint_rows, arrays = [], [], {}
    metadata = {"method": METHOD, "request": request.model_dump(), "joint": {}}
    if joint.enabled:
        for sid in dict.fromkeys(k[0] for k in masks):
            selected = np.zeros(samples[sid].event_count, dtype=bool)
            for (sample_id, _), mask in masks.items():
                if sample_id == sid:
                    selected |= mask
            joint.prepare(sid, selected)
    roles = [("target", source) for source in request.inputs]
    if request.control_baselines:
        roles += [("control_baseline", source) for source in request.controls]

    def row_fields(
        role, source, finite, control_selected, control_finite, shared=0, parameter=None
    ):
        population = next((g.name for g in workspace.gates if g.id == source.gate_id), "All events")
        return dict(
            source=source,
            source_name=samples[source.sample_id].name,
            population_name=population,
            role=role,
            parameter_id=parameter,
            selected_count=int(masks[key(source)].sum()),
            finite_count=len(finite),
            control_selected_count=control_selected,
            control_finite_count=len(control_finite),
            shared_events=shared,
        )

    def pooled(values, exclude=None):
        members = [v for sid, v in values.items() if sid != exclude]
        selected = sum(int(mask.sum()) for sid, mask in control_masks.items() if sid != exclude)
        if not members:
            shape = (
                (0, len(request.parameters))
                if values and next(iter(values.values())).ndim == 2
                else (0,)
            )
            return np.empty(shape), selected
        return np.concatenate(members), selected

    def unavailable(fields, reason):
        return PopulationComparisonRow(**fields, status="unavailable", error=reason)

    for i, parameter in enumerate(request.parameters):
        progress(
            f"Comparing parameter {i + 1}/{len(request.parameters)}",
            0.08 + 0.65 * i / len(request.parameters),
        )
        columns = {
            sid: owned_coordinate(engine.dimension(workspace, samples[sid], parameter))
            for sid in {s.sample_id for s in sources.values()}
        }
        values = {k: columns[k[0]][mask] for k, mask in masks.items()}
        control_values = {sid: columns[sid][mask] for sid, mask in control_masks.items()}
        joint.write(i, columns)
        finite_values = {k: v[np.isfinite(v)] for k, v in values.items()}
        finite_control = {sid: v[np.isfinite(v)] for sid, v in control_values.items()}
        reference, selected = pooled(finite_control)
        all_finite = [v for v in finite_values.values() if len(v)] + (
            [reference] if len(reference) else []
        )
        if all_finite:
            lo = min(float(v.min()) for v in all_finite)
            hi = max(float(v.max()) for v in all_finite)
            edges = statistics.histogram_edges([lo, hi], [lo, hi], request.histogram_bins)
        else:
            edges = statistics.histogram_edges([0, 1], [0, 1], request.histogram_bins)

        def count(v, edges=edges):
            return (
                statistics.bin_counts(v, edges)
                if len(v)
                else np.zeros(len(edges) - 1, dtype=np.int64)
            )

        arrays[f"edges_{i}"] = edges
        arrays[f"control_{i}"] = count(reference)
        arrays[f"targets_{i}"] = np.stack([count(finite_values[key(s)]) for s in request.inputs])
        arrays[f"individual_{i}"] = np.stack(
            [count(finite_values[key(s)]) for s in request.controls]
        )
        for role, source in roles:
            target = finite_values[key(source)]
            ref, ctl_selected = (
                (reference, selected)
                if role == "target"
                else pooled(finite_control, source.sample_id)
            )
            shared = 0
            if role == "target" and source.sample_id in control_masks:
                shared = int(
                    np.sum(
                        masks[key(source)]
                        & control_masks[source.sample_id]
                        & np.isfinite(columns[source.sample_id])
                    )
                )
            fields = row_fields(role, source, target, ctl_selected, ref, shared, parameter.id)
            if not len(target) or not len(ref):
                reason = (
                    "The selected population has no finite events"
                    if not len(target)
                    else "No finite independent control events"
                    if role == "control_baseline"
                    else "The pooled control has no finite events"
                )
                rows.append(unavailable(fields, reason))
                continue
            effects = statistics.empirical(
                ref, target, direction=request.positive_direction, shared_events=shared
            )
            hist = statistics.histogram_comparison(ref, target, request.histogram_bins)
            effects["peak_normalized_excess_percent"] = hist["peak_normalized_excess_percent"]
            pb = statistics.univariate_probability(ref, target, request.probability_bins)
            warnings = []
            if effects["continuous_null_has_ties"]:
                warnings.append(
                    "KS probabilities use a continuous-null calibration; these data contain ties"
                )
            if shared:
                warnings.append(
                    "Target and control share original events; "
                    "no independent KS probability is reported"
                )
            if pb["status"] == "unavailable":
                warnings.append(pb["reason"])
            elif pb["minimum_control_bin_events"] < 10:
                warnings.append("Some probability bins have fewer than ten control events")
            rows.append(
                PopulationComparisonRow(
                    **fields,
                    status="available",
                    metrics=effects,
                    probability=summary_probability(pb),
                    warnings=warnings,
                )
            )
        # The next coordinate and joint phase need no vectors from this coordinate.
        del (
            columns,
            values,
            control_values,
            finite_values,
            finite_control,
            reference,
            all_finite,
            target,
            ref,
        )
    if request.joint and len(request.parameters) > 1:
        progress("Building joint probability partitions", 0.76)
        controls = {sid: joint.samples[sid].rows(mask) for sid, mask in control_masks.items()}

        def pooled_joint(exclude=None):
            return (
                PooledColumns(
                    [value for sid, value in controls.items() if sid != exclude],
                    len(request.parameters),
                ),
                sum(int(mask.sum()) for sid, mask in control_masks.items() if sid != exclude),
            )

        reference, selected = pooled_joint()
        tree = (
            statistics.probability_tree(
                reference, request.probability_bins, request.minimum_bin_events
            )
            if len(reference)
            else None
        )
        metadata["joint"]["control_tree"] = tree.serialize() if tree else None
        baseline_trees = OrderedDict()
        for index, (role, source) in enumerate(roles):
            progress("Comparing joint event distributions", 0.8 + 0.12 * index / len(roles))
            acquisition = joint.samples[source.sample_id]
            target = acquisition.rows(masks[key(source)])
            ref, ctl_selected = (
                (reference, selected) if role == "target" else pooled_joint(source.sample_id)
            )
            shared = 0
            if role == "target" and source.sample_id in control_masks:
                shared = acquisition.shared_count(
                    masks[key(source)], control_masks[source.sample_id]
                )
            fields = row_fields(role, source, target, ctl_selected, ref, shared)
            if not len(target) or not len(ref):
                joint_rows.append(
                    unavailable(
                        fields,
                        "The joint comparison has no finite target or independent control events",
                    )
                )
                continue
            if role == "target":
                actual_tree = tree
            else:
                actual_tree = baseline_trees.pop(source.sample_id, None)
                if actual_tree is None:
                    actual_tree = statistics.probability_tree(
                        ref, request.probability_bins, request.minimum_bin_events
                    )
                baseline_trees[source.sample_id] = actual_tree
                if len(baseline_trees) > MAX_BASELINE_TREES:
                    baseline_trees.popitem(last=False)
            pb = actual_tree.compare(target)
            warnings = []
            if pb["status"] == "unavailable":
                warnings.append(pb["reason"])
            elif pb["minimum_control_bin_events"] < 10:
                warnings.append("Some joint probability bins have fewer than ten control events")
            if pb["status"] == "unavailable":
                joint_rows.append(unavailable(fields, pb["reason"]))
            else:
                joint_rows.append(
                    PopulationComparisonRow(
                        **fields,
                        status="available",
                        probability=summary_probability(pb),
                        warnings=warnings,
                    )
                )
    progress("Verifying original event and model bytes", 0.95)
    verify_sources(workspace, request, engine)
    metadata["statistics_hash"] = statistics_hash(rows, joint_rows)
    arrays["metadata"] = np.frombuffer(
        json.dumps(metadata, sort_keys=True).encode(), dtype=np.uint8
    )
    path = write_artifact(engine.store, workspace.id, identifier, arrays)
    result = PopulationComparisonResult(
        id=identifier,
        request=request,
        created_at=now(),
        input_snapshot=snapshot,
        input_hash=input_hash(workspace, request),
        data=PopulationComparisonData(
            sha256=digest(path),
            bytes=path.stat().st_size,
            parameters=len(request.parameters),
            targets=len(request.inputs),
            controls=len(request.controls),
        ),
        rows=rows,
        joint_rows=joint_rows,
        diagnostics={
            "method": METHOD,
            "control_events_are_pooled_and_deduplicated": True,
            "control_baselines_exclude_the_entire_original_acquisition": True,
            "joint_counts_require_finite_values_in_every_selected_dimension": True,
            "joint_storage": "acquisition-shared temporary columns; all selected finite events",
        },
        versions={k: version(k) for k in ["numpy", "scipy"]},
        duration_seconds=time.monotonic() - started,
    )
    load_artifact(engine.store, workspace.id, result)
    return result


def run_population_comparison(directory):
    directory = Path(directory)
    stop = threading.Event()
    store = None
    try:
        payload = json.loads((directory / "input.json").read_text())
        threading.Thread(
            target=watch_parent, args=(payload["parent_pid"], stop), daemon=True
        ).start()
        workspace = Workspace.model_validate(payload["workspace"])
        request = PopulationComparisonRequest.model_validate(payload["request"])
        if input_hash(workspace, request) != payload["input_hash"]:
            raise ValueError("Comparison scientific inputs failed snapshot validation")
        store = Store(Path(payload["data_dir"]))

        def progress(stage, fraction):
            atomic_json(directory / "progress.json", {"stage": stage, "progress": fraction})

        result = calculate(
            workspace,
            request,
            Engine(store),
            payload["id"],
            progress,
            scratch_directory=directory / "comparison-scratch",
        )
        atomic_json(directory / "result.json", result.model_dump())
    except Exception as exc:
        atomic_json(directory / "error.json", {"error": str(exc) or type(exc).__name__})
    finally:
        stop.set()
        if store is not None:
            store.close()
