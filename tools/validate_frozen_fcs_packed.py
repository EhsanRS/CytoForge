"""Exercise the bundled packed importer in a real, isolated frozen child process."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import multiprocessing as mp
import os
import sys
import tempfile
import time
from multiprocessing import resource_tracker, spawn
from pathlib import Path

import numpy as np
from cytoforge import fcs_integers, imports
from cytoforge.models import Gate, Transform, Workspace
from cytoforge.science import Engine
from cytoforge.store import Store

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def array_digest(values):
    handle = io.BytesIO()
    np.lib.format.write_array_header_1_0(
        handle, {"descr": "<f8", "fortran_order": False, "shape": values.shape}
    )
    encoded = hashlib.sha256(handle.getvalue())
    encoded.update(values.astype("<f8").tobytes())
    return encoded.hexdigest()


def frozen_probe(directory):
    directory = Path(directory)
    specification = json.loads((directory / "cases.json").read_text())
    results = []
    for case in specification:
        prepared = directory / (case["name"] + "-prepared")
        prepared.mkdir()
        original = directory / (case["name"] + ".fcs")
        before = digest(original)
        cancelled = {"requested": False}
        should_cancel = bool(case.get("cancel"))

        def progress(_cancel=should_cancel, _state=cancelled, **state):
            if _cancel and state.get("stage") == "Reading events":
                _state["requested"] = state["events_read"] > 0

        def check(_state=cancelled):
            if _state["requested"]:
                raise imports.ImportCancelled()

        try:
            imported = imports.stream_fcs(
                original, original.name, prepared, progress=progress, check=check
            )
        except (ValueError, imports.ImportCancelled) as exc:
            assert case.get("error") is not None, (case, str(exc))
            assert case["error"] in str(exc), (case, str(exc))
            assert not list(prepared.iterdir()), case
            results.append(
                dict(
                    name=case["name"],
                    status="rejected_atomically",
                    message=str(exc),
                    generated_arrays_removed=True,
                )
            )
        else:
            assert case.get("error") is None, case
            assert len(imported) == 1
            result = imported[0]
            assert digest(result.path) == result.sample.sha256 == case["output_sha256"]
            data = np.load(result.path, mmap_mode="r", allow_pickle=False)
            assert data.shape == (case["events"], case["channels"])
            record = dict(
                name=case["name"],
                status="passed",
                events=len(data),
                output_sha256=result.sample.sha256,
                every_value_verified=True,
            )
            del data
            # Persist actual imported bytes, gate them in the bundled scientific
            # engine, close/reopen SQLite, and check the same identities again.
            if case.get("gate_event_ids") is not None:
                store_root = directory / (case["name"] + "-store")
                store = Store(store_root)
                try:
                    workspace = Workspace(name="Frozen packed import", samples=[result.sample])
                    result.path.replace(store.data_path(workspace.id, result.sample.id))
                    gate = Gate(
                        sample_id=result.sample.id,
                        name="Known packed range",
                        kind="range",
                        x="X",
                        bounds=[2, 5],
                        x_transform=Transform(kind="linear"),
                    )
                    workspace.gates.append(gate)
                    workspace = store.create(workspace)
                    selected = np.flatnonzero(
                        Engine(store).mask(workspace, workspace.samples[0], gate.id)
                    ).tolist()
                    assert selected == case["gate_event_ids"]
                    record["gate_event_ids"] = selected
                    saved_id, saved_sample_id = workspace.id, result.sample.id
                finally:
                    store.close()
                reopened = Store(store_root)
                try:
                    restored = reopened.get(saved_id)
                    assert restored.samples[0].id == saved_sample_id
                    assert (
                        digest(reopened.data_path(saved_id, saved_sample_id))
                        == case["output_sha256"]
                    )
                    selected = np.flatnonzero(
                        Engine(reopened).mask(restored, restored.samples[0], gate.id)
                    ).tolist()
                    assert selected == case["gate_event_ids"]
                    record["restart_identity_and_gating_verified"] = True
                finally:
                    reopened.close()
            results.append(record)
        assert digest(original) == before, case
    evidence = dict(
        status="passed",
        frozen=bool(getattr(sys, "frozen", False)),
        executable=sys.executable,
        modules={
            "imports": imports.__file__,
            "fcs_integers": fcs_integers.__file__,
            "numpy": np.__file__,
            "science": sys.modules["cytoforge.science"].__file__,
        },
        import_paths=sys.path,
        cases=results,
        input_files_unchanged=True,
    )
    (directory / "result.json").write_text(json.dumps(evidence, indent=2) + "\n")


def prepare_cases(directory):
    # Fixture generation runs only in the source driver. The child cannot import
    # this checkout's backend, tools, NumPy environment, or fixture helpers.
    from import_fixture import fcs_chain, fcs_dataset

    cases = []

    def add(name, content, values=None, error=None, **extra):
        (directory / (name + ".fcs")).write_bytes(content)
        case = dict(name=name, error=error, **extra)
        if values is not None:
            values = np.asarray(values, dtype=np.float64)
            case.update(
                events=len(values), channels=values.shape[1], output_sha256=array_digest(values)
            )
        cases.append(case)

    add(
        "literal",
        fcs_dataset(
            [[5, 2], [6, 1], [7, 0]], ["X", "Y"], data_type="I", widths=[3, 2], raw_data=b"\xd5\x9d"
        ),
        [[5, 2], [6, 1], [7, 0]],
        gate_event_ids=[],
    )
    rows = np.arange(262_149, dtype=np.int64)
    expected = np.column_stack(
        (rows % 8, ((rows * 17) % 1024) / 2, (rows % 32) * 0.25, rows % 4, rows)
    )
    raw = np.empty((len(rows), 5), dtype=np.uint64)
    raw[:, 0] = rows % 8
    raw[:, 1] = (rows * 17) % 1024
    raw[:, 2] = rows % 32
    raw[:, 3] = rows % 4
    raw[:, 4] = rows.astype(np.uint64) | (1 << 63)
    content = fcs_dataset(
        raw,
        ["X", "Y", "Time", "Flag", "Index"],
        data_type="I",
        widths=[3, 10, 5, 3, 64],
        metadata={"P2G": "2", "P3G": "999", "TIMESTEP": "0.25", "P5R": str((1 << 40) + 1)},
    )
    add("mixed-unaligned", content, expected)
    add("cancelled", content, error="", cancel=True)
    add("empty", fcs_dataset([], ["X", "Y"], data_type="I", widths=[3, 7]), np.empty((0, 2)))
    known = [[row % 8, row * 17, row] for row in range(31)]
    add(
        "native-science",
        fcs_dataset(
            known,
            ["X", "Y", "Time"],
            data_type="I",
            widths=[3, 10, 5],
            metadata={"TIMESTEP": "0.25", "P2G": "2"},
        ),
        [[row % 8, row * 8.5, row * 0.25] for row in range(31)],
        gate_event_ids=[2, 3, 4, 10, 11, 12, 18, 19, 20, 26, 27, 28],
    )
    add(
        "precision",
        fcs_chain(
            [
                fcs_dataset([[1]], ["Flag"], data_type="I", widths=[3]),
                fcs_dataset([[1, (1 << 53) + 1]], ["Flag", "Value"], data_type="I", widths=[1, 64]),
            ]
        ),
        error="exact float64",
    )
    add(
        "wrong-length",
        fcs_dataset([[1], [2], [3]], ["X"], data_type="I", widths=[3], raw_data=b"\x01"),
        error="DATA length",
    )
    add(
        "big-endian",
        fcs_dataset([[1]], ["X"], data_type="I", widths=[3], order=">", raw_data=b"\x01"),
        error="verified instrument layout",
    )
    (directory / "cases.json").write_text(json.dumps(cases) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/frozen-fcs-packed-engine-validation.json"
    )
    args = parser.parse_args()
    binary, output = args.engine.resolve(), args.output.resolve()
    if not binary.is_relative_to(ROOT) or not binary.is_file():
        parser.error("Choose a frozen engine binary in this checkout")
    internal = binary.parent / "_internal"
    if not internal.is_dir() or not output.is_relative_to(ROOT):
        parser.error("Keep the complete frozen engine and evidence in this checkout")
    original_paths, original_pythonpath = sys.path[:], os.environ.pop("PYTHONPATH", None)
    original_command_line = spawn.get_command_line
    resource_tracker.getfd()

    def frozen_command_line(**kwargs):
        return [str(binary), "--multiprocessing-fork"] + [
            f"{key}={value!r}" for key, value in kwargs.items()
        ]

    try:
        with tempfile.TemporaryDirectory(prefix="frozen-packed-fcs-", dir=ROOT / ".tmp") as owned:
            directory = Path(owned)
            prepare_cases(directory)
            spawn.get_command_line = frozen_command_line
            sys.path = [
                str(internal / "base_library.zip"),
                str(internal / "lib-dynload"),
                str(internal),
            ]
            mp.set_executable(str(binary))
            process = mp.get_context("spawn").Process(target=frozen_probe, args=(str(directory),))
            process.start()
            deadline = time.monotonic() + 120
            while process.is_alive() and time.monotonic() < deadline:
                process.join(0.5)
            if process.is_alive():
                process.terminate()
                process.join(10)
                raise RuntimeError("Owned frozen import probe exceeded its execution deadline")
            assert process.exitcode == 0, process.exitcode
            runtime = json.loads((directory / "result.json").read_text())
            assert runtime["status"] == "passed" and runtime["frozen"]
            assert Path(runtime["executable"]).resolve() == binary
            assert all(
                Path(module).is_relative_to(internal) for module in runtime["modules"].values()
            )
            assert all(Path(path).is_relative_to(internal) for path in runtime["import_paths"])
        evidence = dict(
            status="passed",
            engine=str(binary.relative_to(ROOT)),
            engine_sha256=digest(binary),
            scope="Actual frozen packed importer and scientific gating in a spawned child process",
            desktop_interaction_executed=False,
            http_listener_started=False,
            source_import_paths_removed=True,
            generated_test_data_removed=True,
            runtime=runtime,
            source_sha256={
                name: digest(ROOT / name)
                for name in [
                    "backend/cytoforge/imports.py",
                    "backend/cytoforge/fcs_integers.py",
                    "backend/cytoforge/science.py",
                    "backend/cytoforge/models.py",
                    "backend/cytoforge/store.py",
                    "tools/import_fixture.py",
                    "tools/validate_frozen_fcs_packed.py",
                ]
            },
        )
        output.write_text(json.dumps(evidence, indent=2) + "\n")
        print(f"Passed real frozen packed importer: {output.relative_to(ROOT)}")
    finally:
        sys.path = original_paths
        if original_pythonpath is not None:
            os.environ["PYTHONPATH"] = original_pythonpath
        spawn.get_command_line = original_command_line
        mp.set_executable(sys.executable)


if __name__ == "__main__":
    main()
