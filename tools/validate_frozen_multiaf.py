"""Verify multiple AF references and median control reuse with frozen production modules."""

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

import numpy as np
from cytoforge import autospill, autospread, compensation, jobs, quality
from cytoforge.science import Engine
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
                ("autospill", autospill),
                ("autospread", autospread),
                ("quality", quality),
                ("compensation", compensation),
                ("jobs", jobs),
                ("numpy", np),
            )
        },
    )


def frozen_autospill(directory):
    Path(directory, "runtime.json").write_text(json.dumps(runtime(), indent=2))
    autospill.run_autospill(directory)


def frozen_autospread(directory):
    Path(directory, "runtime.json").write_text(json.dumps(runtime(), indent=2))
    autospread.run_autospread(directory)


def wait_job(manager, doc, identifier):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        job = manager.get(doc, identifier)
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.05)
    raise RuntimeError("Owned frozen control-population worker exceeded its deadline")


def frozen_probe(directory):
    from cytoforge.models import Compensation

    directory = Path(directory)
    inputs = json.loads((directory / "inputs.json").read_text())
    store = Store(directory / "data")
    manager = jobs.JobManager(store, workers=1)
    previous = {name: jobs.PLATFORMS[name] for name in ("autospill", "autospread")}
    jobs.PLATFORMS["autospill"] = (autospill, autospill.AutoSpillResult, frozen_autospill)
    jobs.PLATFORMS["autospread"] = (autospread, autospread.AutoSpreadResult, frozen_autospread)
    manager.start()
    cases, children = [], []

    def execute(doc, request):
        queued = manager.submit(doc, request)
        job = wait_job(manager, doc, queued["id"])
        assert job["status"] == "succeeded", job.get("error")
        children.append(json.loads((manager.directory / job["id"] / "runtime.json").read_text()))
        return job

    try:
        for row in inputs:
            doc = store.get(row["workspace_id"])
            if row["kind"] == "autospill":
                request = autospill.AutoSpillRequest.model_validate(row["request"])
                job = execute(doc, request)
                result = autospill.AutoSpillResult.model_validate(job["result"])
                np.testing.assert_allclose(
                    result.compensation.matrix, row["reference"]["matrix"], atol=2e-9, rtol=2e-9
                )
                review = result.diagnostics["autofluorescence_sources"]
                expected_roles = autospill.autofluorescence_outputs(request)
                assert [r["output"] for r in review] == expected_roles
                actual = [
                    [
                        r["orthogonal_fraction"],
                        r["separation_angle_degrees"],
                        r["weighted_cosine"],
                        result.compensation.outputs.index(r["closest_output"]),
                    ]
                    for r in review
                ]
                np.testing.assert_allclose(
                    actual, row["reference"]["af_review"], atol=2e-9, rtol=2e-9
                )
                saved = manager.apply_autospill(
                    doc.id,
                    job["id"],
                    doc.revision,
                    [s.id for s in doc.samples],
                    acknowledge_unconverged=True,
                    save_cleanup_gates=True,
                )
                matrix = saved.compensations[0]
                mappings = matrix.provenance["control_populations"]
                reused = request.model_copy(deep=True)
                reused.revision = saved.revision
                for control, selection in zip(reused.controls, mappings, strict=True):
                    control.sample_id, control.gate_id = (
                        selection["sample_id"],
                        selection["gate_id"],
                    )
                repeated = execute(saved, reused)
                np.testing.assert_allclose(
                    repeated["result"]["compensation"]["matrix"], matrix.matrix, atol=2e-12
                )
                assert repeated["result"]["request"]["af_outputs"] == expected_roles
            else:
                request = compensation.ControlCalculation.model_validate(row["request"])
                report = compensation.calculate_controls(doc, request, Engine(store))
                matrix = Compensation.model_validate(report["compensation"])
                np.testing.assert_allclose(matrix.matrix, row["signature"], atol=2e-12)

                def save(document, matrix=matrix):
                    compensation.save_control_populations(document, matrix, store)
                    document.compensations.append(matrix)
                    for sample in document.samples:
                        compensation.assign_matrix(sample, matrix)

                saved = store.mutate(doc.id, "Save multiple AF controls", save, doc.revision)
                matrix = saved.compensations[0]
                mappings = matrix.provenance["control_populations"]
                for population, diagnostic in zip(
                    mappings, report["diagnostics"]["controls"], strict=True
                ):
                    sample = next(s for s in saved.samples if s.id == population["sample_id"])
                    assert (
                        int(Engine(store).mask(saved, sample, population["gate_id"]).sum())
                        == diagnostic["positive"]["count"]
                    )
                if row["qc"]:
                    qc = saved.quality_results[0]
                    assert quality.is_stale(saved, qc)
                    assert any(
                        quality.is_captured_gate(gate, qc)
                        for gate in saved.gates
                        if gate.kind == "quality" and gate.provenance.get("qc_selection_basis")
                    )
                reused = request.model_copy(deep=True)
                reused.revision = saved.revision
                for control, positive, negative in zip(
                    reused.controls,
                    mappings[:2],
                    matrix.provenance["negative_populations"],
                    strict=True,
                ):
                    control.positive = compensation.ControlPopulation.model_validate(
                        {k: positive[k] for k in ("sample_id", "gate_id")}
                    )
                    control.negative = compensation.ControlPopulation.model_validate(
                        {k: negative[k] for k in ("sample_id", "gate_id")}
                    )
                for reference, selection in zip(
                    reused.autofluorescence_controls, mappings[2:], strict=True
                ):
                    reference.population = compensation.ControlPopulation.model_validate(
                        {k: selection[k] for k in ("sample_id", "gate_id")}
                    )
                recalculated = compensation.calculate_controls(saved, reused, Engine(store))
                np.testing.assert_allclose(
                    recalculated["compensation"]["matrix"], matrix.matrix, atol=2e-12
                )
                assert all(
                    int(
                        Engine(store)
                        .mask(
                            saved,
                            next(s for s in saved.samples if s.id == selection["sample_id"]),
                            selection["gate_id"],
                        )
                        .sum()
                    )
                    == diagnostic["positive"]["count"]
                    for selection, diagnostic in zip(
                        mappings, recalculated["diagnostics"]["controls"], strict=True
                    )
                )
                mappings = mappings[2:]
            before = saved.model_dump_json()
            spread_request = autospread.AutoSpreadRequest(
                revision=saved.revision,
                matrix_id=matrix.id,
                controls=mappings,
                events_per_bin=20,
                quantiles=32,
            )
            spread = execute(saved, spread_request)
            assert [c["output"] for c in spread["result"]["controls"]] == [
                p["output"] for p in mappings
            ]
            assert store.get(saved.id).model_dump_json() == before
            assert all(
                digest(store.data_path(saved.id, sample.id)) == sample.sha256
                for sample in saved.samples
            )
            cases.append(
                dict(
                    name=row["name"],
                    status="passed",
                    two_af_roles_verified=True,
                    saved_population_reuse_verified=True,
                    spreading_reuse_verified=True,
                    acquired_data_unchanged=True,
                )
            )
    finally:
        manager.close()
        store.close()
        jobs.PLATFORMS.update(previous)
    (directory / "result.json").write_text(
        json.dumps(dict(cases=cases, children=children, probe_runtime=runtime()), indent=2) + "\n"
    )


def prepare_inputs(directory):
    original_paths = sys.path[:]
    sys.path.insert(0, str(ROOT / "tests"))
    try:
        from test_multiaf import SPECTRA, median_controls
        from test_multiaf_reference import REFERENCE, TRUTH, controls

        store = Store(directory / "data")
        cases = []
        try:
            for case in TRUTH:
                doc, request, _ = controls(store, case)
                cases.append(
                    dict(
                        name=case,
                        kind="autospill",
                        workspace_id=doc.id,
                        request=request.model_dump(),
                        reference=REFERENCE["cases"][case],
                    )
                )
            for qc in (False, True):
                doc, request, _, _ = median_controls(store, qc=qc)
                cases.append(
                    dict(
                        name=f"median-threshold-{'qc' if qc else 'raw'}",
                        kind="median",
                        workspace_id=doc.id,
                        request=request.model_dump(),
                        signature=SPECTRA.tolist(),
                        qc=qc,
                    )
                )
        finally:
            store.close()
        (directory / "inputs.json").write_text(json.dumps(cases, indent=2) + "\n")
    finally:
        sys.path = original_paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/frozen-multiaf-validation.json"
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
        with tempfile.TemporaryDirectory(prefix="frozen-multiaf-", dir=ROOT / ".tmp") as owned:
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
            assert len(result["cases"]) == 9 and len(result["children"]) == 23
            for record in [result["probe_runtime"], *result["children"]]:
                assert record["frozen"] and Path(record["executable"]).resolve() == binary
                assert all(
                    Path(p).resolve().is_relative_to(internal) for p in record["modules"].values()
                )
                assert all(
                    "/backend" not in p and "/.venv" not in p for p in record["import_paths"]
                )
            paths = [
                "backend/cytoforge/autospill.py",
                "backend/cytoforge/spectral_autospill.py",
                "backend/cytoforge/autospread.py",
                "backend/cytoforge/quality.py",
                "backend/cytoforge/science.py",
                "backend/cytoforge/models.py",
                "backend/cytoforge/jobs.py",
                "backend/cytoforge/store.py",
                "backend/cytoforge/acquired_gates.py",
                "backend/cytoforge/autofluorescence.py",
                "backend/cytoforge/compensation.py",
                "tests/test_multiaf.py",
                "tests/test_multiaf_reference.py",
                "tests/fixtures/multiaf/controls.npz",
                "tests/fixtures/multiaf/truth.json",
                "tests/fixtures/multiaf/reference.json",
                "tools/validate_frozen_multiaf.py",
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
                    "Synthetic two-AF spectra and preserved median threshold/QC populations; "
                    "no biological or FlowJo equivalence claim"
                ),
                runtime=result,
            )
            output.write_text(json.dumps(proof, indent=2) + "\n")
            print(
                f"Frozen multiple AF: {len(result['cases'])} cases, "
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
