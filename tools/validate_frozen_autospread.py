"""Verify spreading in actual frozen analysis workers with source imports removed."""

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
from cytoforge import autospread, jobs
from cytoforge.models import Channel, Compensation, Sample, Workspace
from cytoforge.science import Engine, save_events
from cytoforge.store import Store

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def frozen_worker(directory):
    import scipy.stats

    runtime = {
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "modules": {
            "autospread": autospread.__file__,
            "jobs": jobs.__file__,
            "numpy": np.__file__,
            "scipy.stats": scipy.stats.__file__,
        },
        "import_paths": list(sys.path),
    }
    Path(directory, "runtime.json").write_text(json.dumps(runtime, indent=2))
    autospread.run_autospread(directory)


def wait_job(manager, doc, identifier):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        record = manager.get(doc, identifier)
        if record["status"] not in {"queued", "running"}:
            return record
        time.sleep(0.05)
    raise RuntimeError("Owned frozen spreading worker exceeded its deadline")


def frozen_probe(directory):
    directory = Path(directory)
    reference = json.loads((directory / "truth.json").read_text())
    input_before = {name: digest(directory / name) for name in ("controls.npz", "truth.json")}
    original_platform = jobs.PLATFORMS["autospread"]
    jobs.PLATFORMS["autospread"] = (autospread, autospread.AutoSpreadResult, frozen_worker)
    store = Store(directory / "data")
    manager = jobs.JobManager(store, workers=1)
    manager.start()
    cases = []
    try:
        with np.load(directory / "controls.npz", allow_pickle=False) as inputs:
            for name, truth in reference.items():
                detectors = [f"D{i + 1}" for i in range(len(truth["matrix"][0]))]
                outputs = ["F1", "F2", "AF"] if truth["kind"] == "spectral" else detectors
                matrix = Compensation(
                    name="Frozen reference",
                    kind=truth["kind"],
                    detectors=detectors,
                    outputs=outputs,
                    matrix=truth["matrix"],
                    background=truth["background"] if truth["kind"] == "spectral" else [],
                    weights=truth["weights"] if truth["kind"] == "spectral" else [],
                )
                doc = Workspace(name=f"Frozen {name}", compensations=[matrix])
                for i, output in enumerate(outputs):
                    values = inputs[f"{name}_{i}"]
                    sample = Sample(
                        name=output,
                        event_count=len(values),
                        channels=[Channel(name=n) for n in detectors],
                    )
                    sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
                    doc.samples.append(sample)
                doc = store.create(doc)
                request = autospread.AutoSpreadRequest(
                    revision=doc.revision,
                    matrix_id=matrix.id,
                    quantiles=truth["quantiles"],
                    events_per_bin=truth["events_per_bin"],
                    controls=[
                        dict(output=n, sample_id=s.id)
                        for n, s in zip(outputs, doc.samples, strict=True)
                    ],
                )
                job = manager.submit(doc, request)
                completed = wait_job(manager, doc, job["id"])
                assert completed["status"] == "succeeded", completed.get("error")
                result = completed["result"]
                runtime = json.loads((manager.directory / job["id"] / "runtime.json").read_text())
                assert runtime["frozen"] and Path(runtime["executable"]).is_file()
                assert all(
                    Path(p).resolve().is_relative_to(Path(runtime["executable"]).parent)
                    for p in runtime["modules"].values()
                )
                assert all(
                    "/backend" not in p and ".venv" not in p for p in runtime["import_paths"]
                )
                compared = 0
                for i, control in enumerate(truth["controls"]):
                    for expected in control["pairs"]:
                        j = expected["secondary"]
                        pair = next(
                            p for p in result["controls"][i]["pairs"] if p["output"] == outputs[j]
                        )
                        np.testing.assert_allclose(
                            [
                                result["matrix"][i][j],
                                pair["baseline_noise"],
                                pair["final_fit"]["slope"],
                                pair["final_fit"]["p_value"],
                            ],
                            [
                                expected["coefficient"],
                                expected["baseline"],
                                expected["final_slope"],
                                expected["p_value"],
                            ],
                            atol=2e-9,
                            rtol=5e-10,
                        )
                        assert pair["status"] == expected["status"]
                        for key in ("robust_sd", "adjusted_sd"):
                            np.testing.assert_allclose(
                                pair[key], [b[key] for b in expected["bins"]], atol=2e-9, rtol=5e-10
                            )
                        compared += 1
                saved = manager.apply_autospread(doc.id, job["id"], doc.revision)
                assert saved.compensations[0].matrix == matrix.matrix
                assert autospread.saved_result(saved.compensations[0]).model_dump() == result
                assert all(s.compensation_id is None for s in saved.samples)
                assert all(digest(store.data_path(doc.id, s.id)) == s.sha256 for s in saved.samples)
                cases.append(
                    dict(
                        name=name,
                        status="passed",
                        pairs=compared,
                        runtime=runtime,
                        saved_matrix_unchanged=True,
                        raw_data_unchanged=True,
                    )
                )
        # A separate actual frozen worker must reject changed acquired bytes atomically.
        request.revision = saved.revision
        values = Engine(store).raw(saved, saved.samples[0]).copy()
        values[0, 0] += 1
        save_events(store.data_path(saved.id, saved.samples[0].id), values)
        rejected = manager.submit(saved, request)
        failed = wait_job(manager, saved, rejected["id"])
        assert failed["status"] == "failed" and "integrity check" in failed["error"]
        assert not (manager.directory / rejected["id"] / "result.json").exists()
        assert store.get(saved.id).revision == saved.revision
        cases.append(dict(name="changed-control-integrity", status="atomic_rejection_verified"))
    finally:
        manager.close()
        store.close()
        jobs.PLATFORMS["autospread"] = original_platform
    assert all(digest(directory / name) == value for name, value in input_before.items())
    (directory / "result.json").write_text(
        json.dumps(dict(status="passed", cases=cases, reference_files_unchanged=True), indent=2)
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/frozen-autospread-validation.json"
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
        parser.error("Keep the complete frozen engine, validation and caches in this checkout")
    original_paths, original_pythonpath = sys.path[:], os.environ.pop("PYTHONPATH", None)
    original_command_line = spawn.get_command_line
    resource_tracker.getfd()

    def frozen_command_line(**kwargs):
        return [str(binary), "--multiprocessing-fork"] + [
            f"{key}={value!r}" for key, value in kwargs.items()
        ]

    try:
        with tempfile.TemporaryDirectory(prefix="frozen-autospread-", dir=ROOT / ".tmp") as owned:
            directory = Path(owned)
            for name in ("controls.npz", "truth.json"):
                (directory / name).write_bytes(
                    (ROOT / "tests/fixtures/autospread" / name).read_bytes()
                )
            spawn.get_command_line = frozen_command_line
            sys.path = [
                str(internal / "base_library.zip"),
                str(internal / "lib-dynload"),
                str(internal),
            ]
            mp.set_executable(str(binary))
            process = mp.get_context("spawn").Process(target=frozen_probe, args=(str(directory),))
            process.start()
            deadline = time.monotonic() + 150
            while process.is_alive() and time.monotonic() < deadline:
                process.join(0.25)
            if process.is_alive():
                process.terminate()
                process.join(5)
                raise RuntimeError("Owned frozen spreading probe exceeded its deadline")
            assert process.exitcode == 0, f"Frozen probe exited with code {process.exitcode}"
            runtime = json.loads((directory / "result.json").read_text())
            names = [
                "backend/cytoforge/autospread.py",
                "backend/cytoforge/jobs.py",
                "backend/cytoforge/app.py",
                "backend/cytoforge/models.py",
                "backend/cytoforge/science.py",
                "backend/cytoforge/quality.py",
                "backend/cytoforge/analysis.py",
                "backend/cytoforge/store.py",
                "tools/validate_frozen_autospread.py",
                "tests/fixtures/autospread/controls.npz",
                "tests/fixtures/autospread/truth.json",
                "tests/fixtures/autospread/reference.json",
            ]
            record = dict(
                status="passed",
                engine=str(binary.relative_to(ROOT)),
                engine_sha256=digest(binary),
                source_import_paths_removed=True,
                actual_frozen_analysis_children=True,
                desktop_interaction_executed=False,
                http_listener_started=False,
                source_sha256={name: digest(ROOT / name) for name in names},
                runtime=runtime,
            )
            output.write_text(json.dumps(record, indent=2) + "\n")
            print(json.dumps(record, indent=2))
    finally:
        sys.path = original_paths
        spawn.get_command_line = original_command_line
        mp.set_executable(sys.executable)
        if original_pythonpath is not None:
            os.environ["PYTHONPATH"] = original_pythonpath


if __name__ == "__main__":
    main()
