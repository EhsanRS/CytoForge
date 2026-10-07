"""Verify PhenoGraph, raw analysis and event identities with frozen production modules."""

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
from cytoforge import analysis, compensation, graph_clustering, jobs
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
                    seeded_replay_verified=True,
                    raw_parent_preservation_verified=not request.compensated,
                    acquired_data_unchanged=True,
                )
            )
    finally:
        manager.close()
        store.close()
        jobs.run_analysis = previous
    (directory / "result.json").write_text(
        json.dumps(dict(cases=cases, children=children, probe_runtime=runtime()), indent=2) + "\n"
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
        "--output", type=Path, default=ROOT / "artifacts/frozen-phenograph-validation.json"
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
        with tempfile.TemporaryDirectory(prefix="frozen-phenograph-", dir=ROOT / ".tmp") as owned:
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
            assert len(result["cases"]) == 4 and len(result["children"]) == 8
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
                "backend/cytoforge/models.py",
                "backend/cytoforge/jobs.py",
                "backend/cytoforge/science.py",
                "backend/cytoforge/acquired_gates.py",
                "backend/cytoforge/store.py",
                "tests/test_phenograph.py",
                "tools/validate_frozen_phenograph.py",
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
                    "Synthetic PhenoGraph partitions, seeded event identities "
                    "and raw population copies; "
                    "no biological or FlowJo equivalence claim"
                ),
                runtime=result,
            )
            output.write_text(json.dumps(proof, indent=2) + "\n")
            print(
                f"Frozen PhenoGraph: {len(result['cases'])} cases, "
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
