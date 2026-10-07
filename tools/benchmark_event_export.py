"""Million-event FCS export checked against independent precision and identity truth."""

import hashlib
import json
import resource
import time
from pathlib import Path

import flowio
import numpy as np
from cytoforge.concatenation import CHUNK_EVENTS
from cytoforge.event_exports import Request, Sessions
from cytoforge.imports import stream_fcs
from cytoforge.models import (
    Channel,
    Compensation,
    ConcatenationProvenance,
    ConcatenationSource,
    Sample,
    Workspace,
    new_id,
)
from cytoforge.store import Store, now


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def truth(start, stop):
    row = np.arange(start, stop)
    return np.column_stack(
        (
            (row % 8192) / 31 - 110.00000000000003,
            ((row * 7) % 2048 + 1) / 17,
            row // 500_000 + 1,
            (row % 500_000) * 2,
            row // 500_000 + 1,
        )
    )


root = Path.cwd()
directory = root / ".tmp" / f"benchmark-event-export-{time.time_ns()}"
store = Store(directory)
sessions = Sessions(store)
count = 1_000_000
sample = Sample(
    name="Million-event precision truth",
    channels=[Channel(name=n) for n in ["X", "Y", "CF_Source", "CF_EventID", "CF_batch"]],
    event_count=count,
    tags={"panel": "known"},
)
matrix = Compensation(
    name="Known correction", detectors=["X", "Y"], matrix=[[2, 0.125], [0.0625, 4]]
)
sample.compensation_id = matrix.id
doc = Workspace(name="Million-event FCS", samples=[sample], compensations=[matrix])
try:
    path = store.data_path(doc.id, sample.id)
    data = np.lib.format.open_memmap(path, mode="w+", dtype="<f8", shape=(count, 5))
    origin_path = store.origins_path(doc.id, sample.id)
    origins = np.lib.format.open_memmap(origin_path, mode="w+", dtype="<u8", shape=(count, 2))
    for start in range(0, count, CHUNK_EVENTS):
        stop = min(count, start + CHUNK_EVENTS)
        values = truth(start, stop)
        data[start:stop] = values
        origins[start:stop] = values[:, [2, 3]].astype("<u8")
    data.flush()
    origins.flush()
    del data, origins
    sample.sha256 = digest(path)
    sample.concatenation = ConcatenationProvenance(
        workspace_id=doc.id,
        revision=0,
        created_at=now(),
        values="raw",
        origins_sha256=digest(origin_path),
        keywords={"batch": ["A", "B"]},
        sources=[
            ConcatenationSource(
                index=i + 1,
                sample_id=new_id(),
                sample_name=f"Independent source {i + 1}",
                offset=i * 500_000,
                count=500_000,
                event_count=1_000_000,
                parameters={"X": "X", "Y": "Y"},
                snapshot={"truth": "Every second acquisition event retained"},
            )
            for i in range(2)
        ],
    )
    doc = store.create(doc)
    before = doc.model_dump_json()
    began = time.perf_counter()
    job = sessions.submit(doc, Request(revision=doc.revision, sample_id=sample.id))
    while True:
        state = sessions.get(doc.id, job["id"])
        if state["status"] not in {"queued", "running"}:
            break
        assert time.perf_counter() - began < 120, "Export exceeded benchmark deadline"
        time.sleep(0.01)
    prepared_seconds = time.perf_counter() - began
    assert state["status"] == "ready", state.get("error")
    began = time.perf_counter()
    exported, _ = sessions.download(doc.id, job["id"])
    verify_seconds = time.perf_counter() - began
    external = flowio.FlowData(exported)
    assert external.data_type == "D" and external.text["p1b"] == "64"
    external_values = np.asarray(external.events).reshape(count, 5)
    began = time.perf_counter()
    imported = stream_fcs(exported, "Reopened.fcs", directory)[0]
    import_seconds = time.perf_counter() - began
    reopened = np.load(imported.path, mmap_mode="r", allow_pickle=False)
    reopened_origins = np.load(imported.origins_path, mmap_mode="r", allow_pickle=False)
    maximum_error = 0.0
    for start in range(0, count, CHUNK_EVENTS):
        stop = min(count, start + CHUNK_EVENTS)
        expected = truth(start, stop)
        np.testing.assert_array_equal(external_values[start:stop], expected)
        np.testing.assert_array_equal(reopened[start:stop], expected)
        np.testing.assert_array_equal(reopened_origins[start:stop], expected[:, [2, 3]])
        maximum_error = max(
            maximum_error, float(np.max(np.abs(reopened[start:stop, :2] - expected[:, :2])))
        )
    assert imported.compensation.matrix == matrix.matrix
    assert imported.sample.concatenation.model_dump() == sample.concatenation.model_dump()
    assert store.get(doc.id).model_dump_json() == before
    result = dict(
        status="passed",
        event_count=count,
        channel_count=5,
        fcs_bytes=exported.stat().st_size,
        preparation_seconds=prepared_seconds,
        download_verification_seconds=verify_seconds,
        reimport_seconds=import_seconds,
        maximum_measurement_error=maximum_error,
        exact_event_origin_rows_verified=count,
        chunk_events=CHUNK_EVENTS,
        precision="FCS 3.1 D, 64-bit little-endian",
        matrix_restored=True,
        workspace_and_history_unchanged=True,
        peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        scope=(
            "Local engine preparation and reimport; excludes fixture creation, FlowIO "
            "verification, HTTP, native download and disk-cache control. RSS includes fixture, "
            "importer and independent reader."
        ),
    )
    Path("artifacts/benchmark-event-export.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    sessions.release(job["id"])
    sessions.cancel(doc.id, job["id"])
finally:
    sessions.close()
    store.close()
