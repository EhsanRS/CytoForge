"""Million-event merge verified against independent numerical and event-ID truth."""

import json
import resource
import time
from pathlib import Path

import numpy as np
from cytoforge.concatenation import CHUNK_EVENTS, Apply, Request, Sessions, validate_origins
from cytoforge.models import Channel, Compensation, DerivedParameter, Sample, Transform, Workspace
from cytoforge.science import save_events
from cytoforge.store import Store

root = Path.cwd()
directory = root / ".tmp" / f"benchmark-concatenation-{time.time_ns()}"
store = Store(directory)
sessions = Sessions(store)
count = 500_000
matrices, samples = [], []
truth = []
try:
    indices = np.arange(count, dtype="float64")
    for index in range(2):
        actual = np.column_stack(
            ((indices % 8192) / 32 - 100 + index, ((indices * 7) % 2048 + 1) / 16)
        )
        truth.append(actual)
        coefficients = np.array([[1, 0.125 + index * 0.125], [0.0625, 1]])
        matrix = Compensation(
            name=f"Basis {index}", detectors=["X", "Y"], matrix=coefficients.tolist()
        )
        matrices.append(matrix)
        sample = Sample(
            name=f"Source {index + 1}",
            event_count=count,
            channels=[
                Channel(name=n, transform=Transform(kind="asinh", cofactor=3))
                for n in ("XY" if index == 0 else "YX")
            ]
            + [Channel(name="Ratio")],
            derived_parameters=[
                DerivedParameter(name="Ratio", expression='ch("X") / max(ch("Y"), 1)')
            ],
            compensation_id=matrix.id,
            tags={"batch": str(index + 1)},
        )
        samples.append(sample)
    doc = Workspace(name="Million-event concatenation", samples=samples, compensations=matrices)
    for index, sample in enumerate(samples):
        measured = truth[index] @ np.asarray(matrices[index].matrix)
        sample.sha256 = save_events(
            store.data_path(doc.id, sample.id), measured if index == 0 else measured[:, ::-1]
        )
    doc = store.create(doc)
    baseline = doc.model_dump_json()
    request = Request(
        revision=doc.revision,
        name="Pooled truth",
        values="compensated",
        inputs=[{"sample_id": s.id} for s in samples],
        parameters=[
            {"name": n, "sources": {s.id: n for s in samples}} for n in ["X", "Y", "Ratio"]
        ],
        keywords=["batch"],
    )
    started = time.perf_counter()
    submitted = sessions.submit(doc, request)
    while True:
        result = sessions.get(doc.id, submitted["id"])
        if result["status"] not in {"queued", "running"}:
            break
        if time.perf_counter() - started > 120:
            raise AssertionError("Million-event event writer exceeded its benchmark deadline")
        time.sleep(0.02)
    prepared_seconds = time.perf_counter() - started
    assert result["status"] == "ready", result.get("error")
    assert store.get(doc.id).model_dump_json() == baseline
    applied_at = time.perf_counter()
    merged_doc = sessions.apply(
        doc.id, result["id"], Apply(revision=doc.revision, review_hash=result["review_hash"])
    )
    apply_seconds = time.perf_counter() - applied_at
    merged = merged_doc.samples[-1]
    assert merged.event_count == count * 2
    data = np.load(store.data_path(doc.id, merged.id), mmap_mode="r")
    origins = validate_origins(store, doc.id, merged)
    maximum_error = 0.0
    for index in range(2):
        for start in range(0, count, CHUNK_EVENTS):
            stop = min(start + CHUNK_EVENTS, count)
            output_start, output_stop = index * count + start, index * count + stop
            expected = truth[index][start:stop]
            actual = data[output_start:output_stop]
            np.testing.assert_allclose(actual[:, :2], expected, rtol=1e-12, atol=1e-12)
            np.testing.assert_allclose(
                actual[:, 2], expected[:, 0] / np.maximum(expected[:, 1], 1), rtol=1e-12, atol=1e-12
            )
            maximum_error = max(maximum_error, float(np.max(np.abs(actual[:, :2] - expected))))
            np.testing.assert_array_equal(actual[:, 3], np.full(stop - start, index + 1))
            np.testing.assert_array_equal(actual[:, 4], np.arange(start, stop))
            np.testing.assert_array_equal(actual[:, 5], np.full(stop - start, index + 1))
            np.testing.assert_array_equal(
                origins[output_start:output_stop, 0], np.full(stop - start, index + 1)
            )
            np.testing.assert_array_equal(
                origins[output_start:output_stop, 1], np.arange(start, stop)
            )
    assert merged_doc.samples[:-1] == doc.samples
    assert merged_doc.revision == doc.revision + 1
    output = dict(
        status="passed",
        source_events=count * 2,
        output_events=merged.event_count,
        write_chunk_events=CHUNK_EVENTS,
        parameters=3,
        additional_origin_keyword_columns=3,
        prepared_seconds=prepared_seconds,
        apply_seconds=apply_seconds,
        preparation_events_per_second=count * 2 / prepared_seconds,
        maximum_measurement_absolute_error=maximum_error,
        every_event_measurement_and_origin_verified=True,
        source_samples_unchanged=True,
        preparation_workspace_and_history_unchanged=True,
        one_atomic_workspace_revision=True,
        process_peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        event_file_bytes=store.data_path(doc.id, merged.id).stat().st_size,
        origins_file_bytes=store.origins_path(doc.id, merged.id).stat().st_size,
        scope=(
            "Local Linux in-process writer/apply; warm OS cache is uncontrolled; "
            "includes fixture and verifier memory; excludes native drawing and FCS serialization"
        ),
    )
    Path("artifacts/benchmark-concatenation.json").write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output))
finally:
    sessions.close()
    store.close()
