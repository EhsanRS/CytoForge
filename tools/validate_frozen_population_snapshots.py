"""Verify captured populations, AF control reuse and frozen event identities."""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import sys
import tempfile
import time
from multiprocessing import resource_tracker, spawn
from pathlib import Path

import igraph
import numba
import numpy as np
from cytoforge import analysis, compensation, graph_clustering, jobs, population_snapshot
from cytoforge.science import Engine, save_events
from cytoforge.store import Store

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def runtime():
    return dict(
        frozen=bool(getattr(sys, "frozen", False)),
        executable=sys.executable,
        import_paths=list(sys.path),
        modules={
            name: module.__file__
            for name, module in (
                ("analysis", analysis),
                ("graph_clustering", graph_clustering),
                ("population_snapshot", population_snapshot),
                ("compensation", compensation),
                ("jobs", jobs),
                ("numpy", np),
                ("numba", numba),
                ("igraph", igraph),
                ("igraph_native", igraph._igraph),
            )
        },
    )


def frozen_analysis(directory):
    Path(directory, "runtime.json").write_text(json.dumps(runtime(), indent=2))
    analysis.run_analysis(directory)


def wait_job(manager, doc, identifier):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        job = manager.get(doc, identifier)
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.05)
    raise RuntimeError("Owned frozen control-population worker exceeded its deadline")


def frozen_probe(directory):
    from cytoforge.models import AnalysisRequest, AnalysisResult, Compensation

    directory = Path(directory)
    rows = json.loads((directory / "inputs.json").read_text())
    store = Store(directory / "data")
    previous = jobs.run_analysis
    jobs.run_analysis = frozen_analysis
    manager = jobs.JobManager(store, workers=1)
    manager.start()
    cases, children = [], []

    def execute(doc, request):
        queued = manager.submit(doc, request)
        job = wait_job(manager, doc, queued["id"])
        assert job["status"] == "succeeded", job.get("error")
        children.append(json.loads((manager.directory / job["id"] / "runtime.json").read_text()))
        return job

    try:
        for row in rows:
            doc = store.get(row["workspace_id"])
            request = AnalysisRequest.model_validate(row["request"])
            job = execute(doc, request)
            result = AnalysisResult.model_validate(job["result"])
            assert result.versions.get("igraph") == (
                "1.0.0" if request.algorithm == "phenograph" else None
            )
            for data in result.data:
                output = np.load(
                    store.analysis_path(doc.id, result.id, data.sample_id), allow_pickle=False
                )
                fitted = np.load(
                    store.fitted_ids_path(doc.id, result.id, data.sample_id), allow_pickle=False
                )
                analysis.validate_result_data(store, doc.id, result, data, output)
                assert output.shape == (
                    data.event_count,
                    1 if request.algorithm == "phenograph" else 2,
                )
                if request.algorithm == "phenograph":
                    np.testing.assert_array_equal(np.flatnonzero(np.isfinite(output[:, 0])), fitted)
                    assert data.mapped_count == data.fitted_count
            saved = manager.apply(doc.id, job["id"], doc.revision)
            engine = Engine(store)
            members = [gate for gate in saved.gates if gate.provenance.get("membership_basis")]
            original = {
                gate.id: engine.mask(saved, engine.sample(saved, gate.sample_id), gate.id).copy()
                for gate in members
            }
            selected_source = members[0].id if members else request.inputs[0].gate_id

            def keep_population(document, request=request, selected_source=selected_source):
                document.gates.append(
                    population_snapshot.capture(
                        document,
                        request.inputs[0].sample_id,
                        selected_source,
                        "Frozen captured population",
                        True,
                        Engine(store),
                    )
                )

            saved = store.mutate(
                saved.id, "Capture frozen population", keep_population, saved.revision
            )
            captured = saved.gates[-1]
            captured_before = engine.mask(
                saved, engine.sample(saved, captured.sample_id), captured.id
            ).copy()
            shadow = saved.model_copy(deep=True)
            shifted = Compensation(
                name="Snapshot assignment",
                detectors=["X", "Y", "Z"],
                matrix=[[1, 0, 0], [3, 1, 0], [0, 0, 1]],
            )
            shadow.compensations.append(shifted)
            for sample in shadow.samples:
                compensation.assign_matrix(sample, shifted)
            shadow.revision += 1
            np.testing.assert_array_equal(
                engine.mask(shadow, engine.sample(shadow, captured.sample_id), captured.id),
                captured_before,
            )
            if not request.compensated:
                matrix = Compensation(
                    name="Assigned after raw analysis",
                    detectors=["X", "Y", "Z"],
                    outputs=["X", "Y", "Z"],
                    matrix=[[1, 0, 0], [3, 1, 0], [0, 0, 1]],
                )

                def assign(document, matrix=matrix):
                    document.compensations.append(matrix)
                    for sample in document.samples:
                        compensation.assign_matrix(sample, matrix)

                saved = store.mutate(saved.id, "Assign matrix", assign, saved.revision)
                assert not analysis.is_stale(saved, result)
                for gate in members:
                    np.testing.assert_array_equal(
                        engine.mask(saved, engine.sample(saved, gate.sample_id), gate.id),
                        original[gate.id],
                    )
                request.revision = saved.revision
            else:
                request.revision = saved.revision
            repeated = execute(saved, request)
            repeated_result = AnalysisResult.model_validate(repeated["result"])
            for first, second in zip(result.data, repeated_result.data, strict=True):
                np.testing.assert_allclose(
                    np.load(store.analysis_path(saved.id, result.id, first.sample_id)),
                    np.load(store.analysis_path(saved.id, repeated_result.id, second.sample_id)),
                    equal_nan=True,
                    atol=1e-12,
                )
            assert all(
                digest(store.data_path(saved.id, sample.id)) == sample.sha256
                for sample in saved.samples
            )
            cases.append(
                dict(
                    name=row["name"],
                    status="passed",
                    fitted_identity_verified=True,
                    captured_population_verified=True,
                    seeded_replay_verified=True,
                    raw_parent_preservation_verified=not request.compensated,
                    acquired_data_unchanged=True,
                )
            )
    finally:
        manager.close()
        store.close()
        jobs.run_analysis = previous
    cases.append(frozen_median_snapshots(store))
    (directory / "result.json").write_text(
        json.dumps(dict(cases=cases, children=children, probe_runtime=runtime()), indent=2) + "\n"
    )


def frozen_median_snapshots(store):
    from cytoforge.models import Channel, Compensation, Sample, Workspace

    document = Workspace(name="Frozen AF snapshots")
    centers = [[100, 10, 5], [10, 100, 5], [10, 20, 100], [0, 0, 0]]
    for index, center in enumerate(centers):
        sample = Sample(
            name=f"Control {index}",
            event_count=80,
            channels=[Channel(name=n) for n in ("X", "Y", "Z")],
        )
        sample.sha256 = save_events(
            store.data_path(document.id, sample.id), np.tile(center, (80, 1)).astype(float)
        )
        document.samples.append(sample)
    captured = []
    for sample in document.samples[:3]:
        gate = population_snapshot.capture(
            document, sample.id, None, sample.name + " snapshot", False, Engine(store)
        )
        document.gates.append(gate)
        captured.append(gate)
    request = compensation.ControlCalculation(
        revision=0,
        name="Captured AF references",
        kind="spectral",
        detectors=["Z", "X", "Y"],
        min_events=20,
        controls=[
            dict(
                name="F",
                primary_detector="X",
                positive=dict(sample_id=captured[0].sample_id, gate_id=captured[0].id),
                negative=dict(sample_id=document.samples[3].id),
            )
        ],
        autofluorescence_controls=[
            dict(name=f"AF{index}", population=dict(sample_id=g.sample_id, gate_id=g.id))
            for index, g in enumerate(captured[1:], 1)
        ],
    )
    response = compensation.calculate_controls(document, request, Engine(store))
    matrix = Compensation.model_validate(response["compensation"])
    compensation.save_control_populations(document, matrix, store)
    document.compensations.append(matrix)
    for sample in document.samples:
        compensation.assign_matrix(sample, matrix)
    document.revision += 1
    document = Workspace.model_validate(document.model_dump())
    engine = Engine(store)
    for gate in captured:
        assert engine.mask(document, engine.sample(document, gate.sample_id), gate.id).sum() == 80
    path = store.membership_path(document.id, captured[0].membership.id)
    original = path.read_bytes()
    changed = np.load(path, allow_pickle=False).copy()
    changed[0] ^= np.uint8(1)
    np.save(path, changed, allow_pickle=False)
    try:
        compensation.save_control_populations(document, matrix, store)
    except ValueError as error:
        assert "integrity check" in str(error)
    else:
        raise AssertionError("Changed captured controls were accepted")
    finally:
        path.write_bytes(original)
    assert digest(path) == captured[0].membership.sha256
    assert all(
        digest(store.data_path(document.id, sample.id)) == sample.sha256
        for sample in document.samples
    )
    return dict(
        name="captured-multiple-af-controls",
        status="passed",
        snapshots=3,
        median_calculation_verified=True,
        assignment_memberships_verified=True,
        altered_membership_rejected=True,
        corrupted_owned_flags_restored=True,
    )


def prepare_inputs(directory):
    from cytoforge.models import AnalysisRequest, Channel, Gate, Sample, Workspace

    store = Store(directory / "data")
    records = []
    try:
        for name, raw, pooled, pca in [
            ("compensated-phenograph", False, False, False),
            ("raw-parent-phenograph", True, False, False),
            ("pooled-raw-phenograph", True, True, False),
            ("raw-parent-pca", True, False, True),
        ]:
            rng = np.random.default_rng(409123)
            centers = np.array([[10, 10, 1], [50, 10, 2], [90, 10, 3]])
            values = centers[np.repeat(np.arange(3), 80)] + rng.normal(0, 0.1, (240, 3))
            doc = Workspace(name=name)
            for index, data in enumerate([values, values[:40]] if pooled else [values]):
                sample = Sample(
                    name=f"Cells {index}",
                    event_count=len(data),
                    channels=[Channel(name=n) for n in ("X", "Y", "Z")],
                )
                sample.sha256 = save_events(store.data_path(doc.id, sample.id), data)
                doc.samples.append(sample)
            parent = None
            if raw and not pooled:
                parent = Gate(
                    sample_id=doc.samples[0].id,
                    name="Raw parent",
                    kind="range",
                    x="X",
                    bounds=[0, 70],
                )
                doc.gates.append(parent)
            doc = store.create(doc)
            request = AnalysisRequest(
                revision=doc.revision,
                name=name,
                algorithm="pca" if pca else "phenograph",
                inputs=[
                    dict(sample_id=sample.id, gate_id=parent.id if parent else None)
                    for sample in doc.samples
                ],
                channels=["X", "Y", "Z"],
                use_transforms=False,
                standardize=False,
                compensated=not raw,
                max_events=160 if pooled else 120,
                n_neighbors=30,
                min_cluster_size=10,
            )
            records.append(dict(name=name, workspace_id=doc.id, request=request.model_dump()))
    finally:
        store.close()
    (directory / "inputs.json").write_text(json.dumps(records, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts/frozen-population-snapshots-validation.json",
    )
    args = parser.parse_args()
    binary, output = args.engine.resolve(), args.output.resolve()
    internal = binary.parent / "_internal"
    if (
        not binary.is_relative_to(ROOT)
        or not binary.is_file()
        or not internal.is_dir()
        or not output.is_relative_to(ROOT)
    ):
        parser.error("Keep engine, proof and caches inside this checkout")
    original_paths = sys.path[:]
    original_command_line = spawn.get_command_line
    original_pythonpath = os.environ.pop("PYTHONPATH", None)
    resource_tracker.getfd()

    def frozen_command_line(**kwargs):
        return [str(binary), "--multiprocessing-fork"] + [f"{k}={v!r}" for k, v in kwargs.items()]

    try:
        with tempfile.TemporaryDirectory(
            prefix="frozen-population-snapshots-", dir=ROOT / ".tmp"
        ) as owned:
            directory = Path(owned)
            prepare_inputs(directory)
            spawn.get_command_line = frozen_command_line
            sys.path = [
                str(internal / "base_library.zip"),
                str(internal / "lib-dynload"),
                str(internal),
            ]
            mp.set_executable(str(binary))
            process = mp.get_context("spawn").Process(target=frozen_probe, args=(str(directory),))
            process.start()
            deadline = time.monotonic() + 240
            while process.is_alive() and time.monotonic() < deadline:
                process.join(0.25)
            if process.is_alive():
                process.terminate()
                process.join(5)
                raise RuntimeError("Owned frozen parent probe exceeded its deadline")
            assert process.exitcode == 0, f"Frozen parent probe exited with code {process.exitcode}"
            result = json.loads((directory / "result.json").read_text())
            assert len(result["cases"]) == 5 and len(result["children"]) == 8
            for record in [result["probe_runtime"], *result["children"]]:
                assert record["frozen"] and Path(record["executable"]).resolve() == binary
                assert all(
                    Path(p).resolve().is_relative_to(internal) for p in record["modules"].values()
                )
                assert all(
                    "/backend" not in p and "/.venv" not in p for p in record["import_paths"]
                )
            paths = [
                "backend/cytoforge/analysis.py",
                "backend/cytoforge/graph_clustering.py",
                "backend/cytoforge/population_snapshot.py",
                "backend/cytoforge/compensation.py",
                "backend/cytoforge/models.py",
                "backend/cytoforge/jobs.py",
                "backend/cytoforge/science.py",
                "backend/cytoforge/acquired_gates.py",
                "backend/cytoforge/store.py",
                "tests/test_phenograph.py",
                "tests/test_population_snapshots.py",
                "tools/validate_frozen_population_snapshots.py",
                "tools/package_engine.py",
                "pyproject.toml",
                "uv.lock",
            ]
            proof = dict(
                status="passed",
                engine=str(binary.relative_to(ROOT)),
                engine_sha256=digest(binary),
                source_import_paths_removed=True,
                actual_frozen_analysis_children=True,
                desktop_interaction_executed=False,
                http_listener_started=False,
                source_sha256={p: digest(ROOT / p) for p in paths},
                scope=(
                    "Synthetic captured populations, AF controls and frozen event identities "
                    "and raw population copies; "
                    "no biological or FlowJo equivalence claim"
                ),
                runtime=result,
            )
            output.write_text(json.dumps(proof, indent=2) + "\n")
            print(
                f"Frozen captured populations: {len(result['cases'])} cases, "
                f"{len(result['children'])} actual children passed"
            )
    finally:
        sys.path = original_paths
        spawn.get_command_line = original_command_line
        mp.set_executable(sys.executable)
        if original_pythonpath is not None:
            os.environ["PYTHONPATH"] = original_pythonpath


if __name__ == "__main__":
    main()
