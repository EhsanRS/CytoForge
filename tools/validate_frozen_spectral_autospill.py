"""Check rectangular AutoSpill in actual frozen workers with source imports removed."""

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
from cytoforge import autospill, jobs, spectral_autospill
from cytoforge.models import Channel, Sample, Workspace
from cytoforge.science import save_events
from cytoforge.store import Store

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def frozen_worker(directory):
    runtime = dict(
        frozen=bool(getattr(sys, "frozen", False)),
        executable=sys.executable,
        modules={
            "autospill": autospill.__file__,
            "spectral_autospill": spectral_autospill.__file__,
            "jobs": jobs.__file__,
            "numpy": np.__file__,
        },
        import_paths=list(sys.path),
    )
    Path(directory, "runtime.json").write_text(json.dumps(runtime, indent=2))
    autospill.run_autospill(directory)


def wait_job(manager, doc, identifier):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        job = manager.get(doc, identifier)
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.05)
    raise RuntimeError("Owned frozen spectral AutoSpill worker exceeded its deadline")


def frozen_probe(directory):
    directory = Path(directory)
    truth = json.loads((directory / "truth.json").read_text())
    reference = json.loads((directory / "reference.json").read_text())["cases"]
    inputs_before = {
        name: digest(directory / name) for name in ("controls.npz", "truth.json", "reference.json")
    }
    original_platform = jobs.PLATFORMS["autospill"]
    jobs.PLATFORMS["autospill"] = (autospill, autospill.AutoSpillResult, frozen_worker)
    store = Store(directory / "data")
    manager = jobs.JobManager(store, workers=1)
    manager.start()
    cases = []
    try:
        with np.load(directory / "controls.npz", allow_pickle=False) as fixture:
            for name, settings in truth.items():
                doc = Workspace(name=f"Frozen spectral {name}")
                for i, output in enumerate(settings["outputs"]):
                    raw = fixture[f"{name}_{i}"]
                    sample = Sample(
                        name=output,
                        event_count=len(raw),
                        channels=[Channel(name=n) for n in settings["detectors"]],
                    )
                    sample.sha256 = save_events(store.data_path(doc.id, sample.id), raw)
                    doc.samples.append(sample)
                doc = store.create(doc)
                request = autospill.AutoSpillRequest(
                    revision=doc.revision,
                    kind="spectral",
                    detectors=settings["detectors"],
                    auto_cleanup=False,
                    af_output="AF",
                    background=settings["background"],
                    weights=settings["weights"],
                    trim_fraction=settings["trim_fraction"],
                    biex_length=4096 if settings["biex"] == "biex4096" else 256,
                    controls=[
                        dict(
                            name=output,
                            primary_detector=settings["detectors"][peak],
                            sample_id=sample.id,
                        )
                        for output, peak, sample in zip(
                            settings["outputs"], settings["peaks"], doc.samples, strict=True
                        )
                    ],
                )
                queued = manager.submit(doc, request)
                job = wait_job(manager, doc, queued["id"])
                assert job["status"] == "succeeded", job.get("error")
                runtime = json.loads((manager.directory / job["id"] / "runtime.json").read_text())
                assert runtime["frozen"]
                for path in runtime["modules"].values():
                    assert (
                        "/_internal/" in path and "/backend/" not in path and "/.venv/" not in path
                    )
                result = job["result"]
                expected = reference[name]
                np.testing.assert_allclose(
                    result["compensation"]["matrix"], expected["matrix"], atol=2e-9, rtol=2e-9
                )
                np.testing.assert_allclose(
                    result["diagnostics"]["residual_slopes"],
                    expected["residual"],
                    atol=2e-9,
                    rtol=2e-9,
                )
                np.testing.assert_allclose(
                    [c["reconstruction_relative_rms"] for c in result["diagnostics"]["controls"]],
                    expected["reconstruction_relative_rms"],
                    atol=2e-10,
                )
                if name == "missing_component":
                    assert result["diagnostics"]["reconstruction_within_tolerance"] is False
                    try:
                        manager.apply_autospill(
                            doc.id, job["id"], doc.revision, [], save_cleanup_gates=False
                        )
                    except ValueError as error:
                        assert "Acknowledge" in str(error)
                    else:
                        raise AssertionError("Unacknowledged detector reconstruction was saved")
                    assert store.get(doc.id) == doc
                saved = manager.apply_autospill(
                    doc.id,
                    job["id"],
                    doc.revision,
                    [doc.samples[0].id],
                    acknowledge_unconverged=True,
                    save_cleanup_gates=False,
                )
                assert saved.compensations[0].kind == "spectral"
                assert saved.samples[0].unmixed_parameters == settings["outputs"]
                assert [c.name for c in saved.samples[0].acquisition_channels] == settings[
                    "detectors"
                ]
                restored = autospill.saved_result(saved.compensations[0])
                assert restored.model_dump(exclude={"compensation"}) == {
                    key: value for key, value in result.items() if key != "compensation"
                }
                assert restored.compensation.matrix == result["compensation"]["matrix"]
                assert all(
                    digest(store.data_path(saved.id, sample.id)) == sample.sha256
                    for sample in saved.samples
                )
                cases.append(
                    dict(
                        name=name,
                        status="passed",
                        runtime=runtime,
                        assigned_virtual_outputs=True,
                        acquired_data_unchanged=True,
                    )
                )
        # Actual child calculation rejects changed bytes before publishing a result.
        path = store.data_path(saved.id, saved.samples[0].id)
        raw = np.load(path, allow_pickle=False)
        raw[0, 0] += 1
        save_events(path, raw)
        request.revision = saved.revision
        queued = manager.submit(saved, request)
        job = wait_job(manager, saved, queued["id"])
        assert job["status"] == "failed" and "integrity check" in job["error"]
        assert not (manager.directory / job["id"] / "result.json").exists()
        assert store.get(saved.id) == saved
        cases.append(dict(name="changed-control-integrity", status="atomic_rejection_verified"))
    finally:
        manager.close()
        store.close()
        jobs.PLATFORMS["autospill"] = original_platform
    assert all(digest(directory / name) == value for name, value in inputs_before.items())
    (directory / "result.json").write_text(
        json.dumps(dict(status="passed", cases=cases, reference_files_unchanged=True), indent=2)
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/frozen-spectral-autospill-validation.json"
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
        parser.error("Keep engine, validation and caches in this checkout")
    original_paths, original_pythonpath = sys.path[:], os.environ.pop("PYTHONPATH", None)
    original_command_line = spawn.get_command_line
    resource_tracker.getfd()

    def frozen_command_line(**kwargs):
        return [str(binary), "--multiprocessing-fork"] + [
            f"{key}={value!r}" for key, value in kwargs.items()
        ]

    try:
        with tempfile.TemporaryDirectory(
            prefix="frozen-spectral-autospill-", dir=ROOT / ".tmp"
        ) as owned:
            directory = Path(owned)
            for name in ("controls.npz", "truth.json", "reference.json"):
                (directory / name).write_bytes(
                    (ROOT / "tests/fixtures/spectral_autospill" / name).read_bytes()
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
                raise RuntimeError("Owned frozen spectral probe exceeded its deadline")
            assert process.exitcode == 0, (
                f"Frozen spectral probe exited with code {process.exitcode}"
            )
            runtime = json.loads((directory / "result.json").read_text())
            names = [
                "backend/cytoforge/autospill.py",
                "backend/cytoforge/spectral_autospill.py",
                "backend/cytoforge/autospill_biex.py",
                "backend/cytoforge/jobs.py",
                "backend/cytoforge/app.py",
                "backend/cytoforge/models.py",
                "backend/cytoforge/science.py",
                "backend/cytoforge/store.py",
                "tools/validate_frozen_spectral_autospill.py",
                "tests/fixtures/spectral_autospill/controls.npz",
                "tests/fixtures/spectral_autospill/truth.json",
                "tests/fixtures/spectral_autospill/reference.json",
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
            print(f"Frozen spectral AutoSpill: {len(runtime['cases'])} cases passed")
    finally:
        sys.path = original_paths
        spawn.get_command_line = original_command_line
        mp.set_executable(sys.executable)
        if original_pythonpath is not None:
            os.environ["PYTHONPATH"] = original_pythonpath


if __name__ == "__main__":
    main()
