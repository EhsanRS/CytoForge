"""Measure complete rectangular AutoSpill on independently known exact spectra."""

import hashlib
import json
import multiprocessing as mp
import resource
import tempfile
import time
from pathlib import Path

import numpy as np
from cytoforge import autospill
from cytoforge.models import Channel, Sample, Workspace
from cytoforge.science import Engine, save_events
from cytoforge.store import Store

ROOT = Path(__file__).resolve().parents[1]
SIGNATURE = np.array(
    [
        [1, 0.12, 0.28, 0.09, 0.03, 0.11],
        [1, 0.75, 0.15, 0.6, 0.04, 0.27],
        [0.04, 0.07, 0.18, 0.23, 1, 0.5],
    ]
)


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def measure(directory, workspace_id, settings):
    store = Store(Path(directory))
    try:
        doc = store.get(workspace_id)
        request = autospill.AutoSpillRequest.model_validate(settings)
        baseline = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        started = time.perf_counter()
        autospill.verify_control_data(doc, request, store)
        result = autospill.calculate(doc, request, Engine(store))
        elapsed = time.perf_counter() - started
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        np.testing.assert_allclose(result.compensation.matrix, SIGNATURE, atol=2e-10, rtol=0)
        assert (
            result.diagnostics["converged"]
            and result.diagnostics["reconstruction_within_tolerance"]
        )
        assert all(
            control["used_count"] == doc.samples[i].event_count
            for i, control in enumerate(result.diagnostics["controls"])
        )
        assert all(
            digest(store.data_path(doc.id, sample.id)) == sample.sha256 for sample in doc.samples
        )
        assert store.get(doc.id) == doc
        record = dict(
            status="passed",
            events_per_control=doc.samples[0].event_count,
            controls=3,
            measured_detectors=6,
            sources=3,
            seconds=elapsed,
            additional_peak_rss_mib=max(0, peak - baseline) / 1024,
            coefficient_max_absolute_error=float(
                np.max(np.abs(np.asarray(result.compensation.matrix) - SIGNATURE))
            ),
            raw_data_unchanged=True,
            workspace_unchanged=True,
            all_selected_events_used=True,
        )
        Path(directory, "measurement.json").write_text(json.dumps(record, indent=2) + "\n")
    finally:
        store.close()


def main():
    records = []
    for count in (131072, 1048576):
        with tempfile.TemporaryDirectory(
            prefix="spectral-autospill-benchmark-", dir=ROOT / ".tmp"
        ) as owned:
            directory = Path(owned)
            store = Store(directory)
            doc = Workspace(name="Exact shared-peak spectral benchmark")
            detectors = [f"D{i + 1}" for i in range(6)]
            background = np.array([33, -10, 55, 4, 19, 100])
            for i in range(3):
                latent = np.zeros((count, 3))
                latent[:, i] = np.linspace(200, 110000, count)
                raw = latent @ SIGNATURE + background
                sample = Sample(
                    name=f"Source {i}",
                    event_count=count,
                    channels=[Channel(name=name) for name in detectors],
                )
                sample.sha256 = save_events(store.data_path(doc.id, sample.id), raw)
                doc.samples.append(sample)
                del latent, raw
            doc = store.create(doc)
            request = autospill.AutoSpillRequest(
                revision=doc.revision,
                kind="spectral",
                detectors=detectors,
                auto_cleanup=False,
                af_output="AF",
                background=background.tolist(),
                weights=[1, 2, 0.3, 4, 0.8, 1.5],
                controls=[
                    dict(name=name, primary_detector=detectors[peak], sample_id=sample.id)
                    for name, peak, sample in zip(
                        ["Fluor1", "Fluor2", "AF"], [0, 0, 4], doc.samples, strict=True
                    )
                ],
            )
            store.close()
            process = mp.get_context("spawn").Process(
                target=measure, args=(str(directory), doc.id, request.model_dump())
            )
            process.start()
            deadline = time.monotonic() + 150
            while process.is_alive() and time.monotonic() < deadline:
                process.join(0.25)
            if process.is_alive():
                process.terminate()
                process.join(5)
                raise RuntimeError("Owned spectral benchmark exceeded its deadline")
            assert process.exitcode == 0
            records.append(json.loads((directory / "measurement.json").read_text()))
            print(f"{count} events per control: {records[-1]['seconds']:.3f}s", flush=True)
    names = [
        "backend/cytoforge/autospill.py",
        "backend/cytoforge/spectral_autospill.py",
        "backend/cytoforge/autospill_biex.py",
        "backend/cytoforge/science.py",
        "tools/benchmark_spectral_autospill.py",
    ]
    proof = dict(
        status="passed",
        scope="Linux CPU; three exact controls, two sharing a peak detector",
        includes=(
            "Control checksums, acquired parents and all finite events; "
            "robust initial fits, weighted linear/biex refinement and reconstruction"
        ),
        excludes=(
            "Fixture generation, startup, scatter cleanup and desktop rendering; "
            "other operating systems and biological accuracy"
        ),
        machine_was_not_exclusive=True,
        records=records,
        source_sha256={name: digest(ROOT / name) for name in names},
    )
    (ROOT / "artifacts/spectral-autospill-benchmark.json").write_text(
        json.dumps(proof, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
