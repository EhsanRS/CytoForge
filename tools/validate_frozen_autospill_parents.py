"""Verify acquired ratio/QC control reuse in actual frozen analysis children."""

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
from cytoforge import autospill, autospread, jobs, quality
from cytoforge.science import Engine, save_array
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
    directory = Path(directory)
    inputs = json.loads((directory / "inputs.json").read_text())
    store = Store(directory / "data")
    manager = jobs.JobManager(store, workers=1)
    previous = {name: jobs.PLATFORMS[name] for name in ("autospill", "autospread")}
    jobs.PLATFORMS["autospill"] = (autospill, autospill.AutoSpillResult, frozen_autospill)
    jobs.PLATFORMS["autospread"] = (autospread, autospread.AutoSpreadResult, frozen_autospread)
    manager.start()
    cases, children = [], []

    def record_child(job):
        children.append(json.loads((manager.directory / job["id"] / "runtime.json").read_text()))

    try:
        for row in inputs:
            doc = store.get(row["workspace_id"])
            expected = np.load(directory / row["mask"], allow_pickle=False)
            request = autospill.AutoSpillRequest.model_validate(row["request"])
            queued = manager.submit(doc, request)
            job = wait_job(manager, doc, queued["id"])
            assert job["status"] == "succeeded", job.get("error")
            record_child(job)
            result = autospill.AutoSpillResult.model_validate(job["result"])
            assert result.diagnostics["controls"][0]["parent_count"] == int(expected.sum())
            assert np.all(expected[result.diagnostics["controls"][0]["preview"]["event_ids"]])
            saved = manager.apply_autospill(
                doc.id,
                job["id"],
                doc.revision,
                [s.id for s in doc.samples],
                acknowledge_unconverged=True,
                save_cleanup_gates=True,
            )
            matrix = saved.compensations[0]
            populations = matrix.provenance["control_populations"]
            assert [p["output"] for p in populations] == matrix.outputs
            copied_ids = matrix.provenance["cleanup_gate_ids"]
            captured = next(g for g in saved.gates if g.id in copied_ids and g.kind == "quality")
            qc = next(q for q in saved.quality_results if q.id == captured.quality_id)
            assert quality.is_captured_gate(captured, qc)
            assert quality.is_stale(saved, qc)
            assert not autospill.is_stale(saved, autospill.saved_result(matrix))
            selected = Engine(store).mask(saved, saved.samples[0], populations[0]["gate_id"])
            assert np.all(~selected | expected)
            assert int(selected.sum()) == result.diagnostics["controls"][0]["cleanup_count"]
            if not request.auto_cleanup:
                np.testing.assert_array_equal(selected, expected)
            reused = request.model_copy(deep=True)
            reused.revision = saved.revision
            for control, population in zip(reused.controls, populations, strict=True):
                control.gate_id = population["gate_id"]
            queued = manager.submit(saved, reused)
            repeated = wait_job(manager, saved, queued["id"])
            assert repeated["status"] == "succeeded", repeated.get("error")
            record_child(repeated)
            if not request.auto_cleanup:
                np.testing.assert_allclose(
                    repeated["result"]["compensation"]["matrix"],
                    result.compensation.matrix,
                    atol=2e-12,
                )
            spread_request = autospread.AutoSpreadRequest(
                revision=saved.revision,
                matrix_id=matrix.id,
                controls=populations,
                events_per_bin=20,
                quantiles=32,
            )
            queued = manager.submit(saved, spread_request)
            spread = wait_job(manager, saved, queued["id"])
            assert spread["status"] == "succeeded", spread.get("error")
            record_child(spread)
            assert spread["result"]["controls"][0]["parent_count"] == int(selected.sum())
            assert qc.id in spread["result"]["input_snapshot"]["qc"]
            assert all(digest(store.data_path(saved.id, s.id)) == s.sha256 for s in saved.samples)
            assert store.get(saved.id) == saved
            cases.append(
                dict(
                    name=row["name"],
                    status="passed",
                    parent_count=int(expected.sum()),
                    selected_count=int(selected.sum()),
                    captured_qc_verified=True,
                    saved_populations_recalculated=True,
                    spreading_reuse_verified=True,
                    acquired_files_unchanged=True,
                )
            )

        # Corrupt a real captured flag file, leaving all metadata/cache keys intact.
        flags_path = store.quality_path(saved.id, qc.id)
        original_flags = flags_path.read_bytes()
        before = saved.model_dump_json()
        try:
            flags = np.load(flags_path, allow_pickle=False)
            flags[0, 0] ^= 1
            save_array(flags_path, flags)
            queued = manager.submit(saved, reused)
            rejected = wait_job(manager, saved, queued["id"])
            assert rejected["status"] == "failed" and "integrity" in rejected["error"]
            record_child(rejected)
            assert not (manager.directory / rejected["id"] / "result.json").exists()
            assert store.get(saved.id).model_dump_json() == before
            cases.append(
                dict(
                    name="corrupt-captured-qc-worker",
                    status="passed",
                    atomic_rejection_verified=True,
                )
            )
            try:
                manager.apply_autospill(
                    saved.id,
                    repeated["id"],
                    saved.revision,
                    [s.id for s in saved.samples],
                    acknowledge_unconverged=True,
                    save_cleanup_gates=True,
                )
            except ValueError as error:
                assert "integrity" in str(error)
            else:
                raise AssertionError("Changed QC flags were accepted by matrix save")
            assert store.get(saved.id).model_dump_json() == before
            cases.append(
                dict(
                    name="corrupt-captured-qc-save", status="passed", atomic_rejection_verified=True
                )
            )
        finally:
            flags_path.write_bytes(original_flags)
        assert digest(flags_path) == qc.data.sha256
        assert all(digest(store.data_path(saved.id, s.id)) == s.sha256 for s in saved.samples)
    finally:
        manager.close()
        store.close()
        jobs.PLATFORMS.update(previous)
    (directory / "result.json").write_text(
        json.dumps(
            dict(
                status="passed",
                cases=cases,
                children=children,
                probe_runtime=runtime(),
                acquired_data_unchanged=True,
                deliberately_corrupted_flags_restored=True,
            ),
            indent=2,
        )
        + "\n"
    )


def prepare_inputs(directory):
    # Fixtures are generated on the source side only. Every calculation, save,
    # mask and reuse check below executes with production frozen modules.
    original_paths = sys.path[:]
    sys.path.insert(0, str(ROOT / "tests"))
    try:
        from test_autospill_parents import controls, ratio_parent, reviewed_qc

        store = Store(directory / "data")
        cases = []
        try:
            for kind in ("spillover", "spectral"):
                for cleanup in (False, True):
                    doc, request, engine = controls(store, kind=kind, cleanup=cleanup)
                    parent, _ = ratio_parent(doc, request, engine, transformed=True)
                    qc, _, _, expected = reviewed_qc(doc, request, engine, parent=parent)
                    doc = store.mutate(
                        doc.id,
                        "Reviewed control inputs",
                        lambda d, document=doc: (
                            setattr(d, "gates", document.gates),
                            setattr(d, "quality_results", document.quality_results),
                        ),
                        doc.revision,
                    )
                    assert not quality.is_stale(doc, qc)
                    request.revision = doc.revision
                    name = f"{kind}-qc-ratio-{'cleanup' if cleanup else 'no-cleanup'}"
                    np.save(directory / f"{name}.npy", expected, allow_pickle=False)
                    cases.append(
                        dict(
                            name=name,
                            workspace_id=doc.id,
                            request=request.model_dump(),
                            mask=f"{name}.npy",
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
        "--output", type=Path, default=ROOT / "artifacts/frozen-autospill-parents-validation.json"
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
            prefix="frozen-autospill-parents-", dir=ROOT / ".tmp"
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
            assert len(result["cases"]) == 6 and len(result["children"]) == 13
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
                "tests/test_autospill_parents.py",
                "tools/validate_frozen_autospill_parents.py",
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
                    "Synthetic acquired ratio/QC populations; "
                    "no biological or FlowJo equivalence claim"
                ),
                runtime=result,
            )
            output.write_text(json.dumps(proof, indent=2) + "\n")
            print(
                f"Frozen AutoSpill parents: {len(result['cases'])} cases, "
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
