"""Measure complete rectangular AutoSpill on independently known exact spectra."""

import hashlib
import json
import multiprocessing as mp
import resource
import tempfile
import time
from pathlib import Path

import numpy as np
from cytoforge import autospill, quality
from cytoforge.models import (
    Channel,
    Gate,
    GateDimension,
    QualityRequest,
    Sample,
    Transform,
    Workspace,
    new_id,
)
from cytoforge.science import Engine, save_array, save_events
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
            control["used_count"] == doc.samples[i].event_count - (2048 if i == 0 else 0)
            for i, control in enumerate(result.diagnostics["controls"])
        )
        assert all(
            digest(store.data_path(doc.id, sample.id)) == sample.sha256 for sample in doc.samples
        )
        assert all(
            digest(store.quality_path(doc.id, q.id)) == q.data.sha256 for q in doc.quality_results
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
            qc_flags_unchanged=True,
            acquired_ratio_and_reviewed_qc_parent=True,
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
            prefix="autospill-parents-benchmark-", dir=ROOT / ".tmp"
        ) as owned:
            directory = Path(owned)
            store = Store(directory)
            doc = Workspace(name="Exact shared-peak spectral benchmark")
            detectors = [f"D{i + 1}" for i in range(6)]
            background = np.array([33, -10, 55, 4, 19, 100])
            for i in range(3):
                latent = np.zeros((count, 3))
                latent[:, i] = np.linspace(200, 110000, count)
                raw = np.column_stack([latent @ SIGNATURE + background, np.arange(count) / 1000])
                sample = Sample(
                    name=f"Source {i}",
                    event_count=count,
                    channels=[Channel(name=name) for name in detectors + ["Time"]],
                )
                sample.sha256 = save_events(store.data_path(doc.id, sample.id), raw)
                doc.samples.append(sample)
                del latent, raw
            doc = store.create(doc)
            parent = Gate(
                sample_id=doc.samples[0].id,
                name="Acquired ratio",
                kind="hyperrectangle",
                dimensions=[
                    GateDimension(
                        channel="D1 / D2",
                        ratio_channels=("D1", "D2"),
                        compensation_ref="uncompensated",
                        ratio_b=float(background[0]),
                        ratio_c=float(background[1]),
                        transform=Transform(kind="linear"),
                        minimum=0,
                        maximum=100,
                    )
                ],
            )
            doc.gates.append(parent)
            qc_request = QualityRequest(
                revision=doc.revision,
                sample_id=doc.samples[0].id,
                gate_id=parent.id,
                channels=["D1"],
                time_channel="Time",
                compensated=False,
                use_transforms=False,
                bin_events=2048,
            )
            qc, flags = quality.calculate(doc, qc_request, Engine(store), new_id())
            qc.data.sha256 = save_array(store.quality_path(doc.id, qc.id), flags)
            assert qc.bins[0].start == 0 and qc.bins[0].end == 2048
            doc.quality_results.append(qc)
            reviewed = Gate(
                sample_id=doc.samples[0].id,
                name="Reviewed QC",
                kind="quality",
                quality_id=qc.id,
                parent_id=parent.id,
                quality_excluded_bins=[0],
                quality_exclusions=[],
            )
            doc.gates.append(reviewed)
            doc = store.mutate(
                doc.id,
                "Reviewed controls",
                lambda d, document=doc: (
                    setattr(d, "gates", document.gates),
                    setattr(d, "quality_results", document.quality_results),
                ),
                doc.revision,
            )
            del flags
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
            request.controls[0].gate_id = reviewed.id
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
        "backend/cytoforge/quality.py",
        "backend/cytoforge/models.py",
        "backend/cytoforge/store.py",
        "tools/benchmark_autospill_parents.py",
    ]
    proof = dict(
        status="passed",
        scope="Linux CPU; exact shared-peak spectral controls with one acquired ratio/QC parent",
        includes=(
            "Raw and QC flag checksums, acquired ratios, reviewed flags "
            "and all selected finite events; "
            "robust initial fits, weighted linear/biex refinement and reconstruction"
        ),
        excludes=(
            "Fixture/QC generation, startup, scatter cleanup and desktop rendering; "
            "other operating systems and biological accuracy"
        ),
        machine_was_not_exclusive=True,
        records=records,
        source_sha256={name: digest(ROOT / name) for name in names},
    )
    (ROOT / "artifacts/autospill-parents-benchmark.json").write_text(
        json.dumps(proof, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
