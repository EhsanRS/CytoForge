"""Measure full-event source preparation and bounded binary encoding separately."""

import hashlib
import json
import time
from pathlib import Path

import numpy as np
from cytoforge.models import Channel, Sample, Workspace
from cytoforge.science import Engine, save_events
from cytoforge.store import Store
from cytoforge.three_dimensional import CHUNK_EVENTS, point_chunk, prepare

root = Path(__file__).resolve().parents[1]
folder = root / ".tmp" / f"three-d-benchmark-{time.time_ns()}"
store = Store(folder)
try:
    rng = np.random.default_rng(32768)
    events = rng.normal(size=(1_000_000, 5))
    sample = Sample(
        name="Million-event full 3D stream",
        event_count=len(events),
        channels=[Channel(name=n) for n in ("X", "Y", "Z", "C", "S")],
    )
    doc = Workspace(name="3D performance reference", samples=[sample])
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), events)
    doc = store.create(doc)
    engine = Engine(store)
    kwargs = dict(
        graph_options={"axis_extent": "full"}, view={"z": "Z", "color_by": "C", "size_by": "S"}
    )
    start = time.perf_counter()
    prepared = prepare(doc, engine, sample.id, "X", "Y", **kwargs)
    cold = time.perf_counter() - start
    start = time.perf_counter()
    cached = prepare(doc, engine, sample.id, "X", "Y", **kwargs)
    warm = time.perf_counter() - start
    assert prepared[1] is cached[1]
    assert all(
        prepared[0][field] == len(events)
        for field in ("count", "finite_count", "visible_count", "displayed_count")
    )
    start = time.perf_counter()
    identities = hashlib.sha256()
    encoded = 0
    for offset in range(0, len(events), CHUNK_EVENTS):
        rows = point_chunk(prepared, offset)
        assert rows["event_id"].tolist() == list(range(offset, offset + len(rows)))
        assert np.all(np.isfinite(rows["position"]))
        identities.update(rows["event_id"].tobytes())
        encoded += rows.nbytes
    elapsed = time.perf_counter() - start
    expected = hashlib.sha256(np.arange(len(events), dtype="<u8").tobytes()).hexdigest()
    assert expected == identities.hexdigest() and encoded == len(events) * 32
    evidence = dict(
        status="passed",
        events=len(events),
        source_sha256=sample.sha256,
        profile=str(folder),
        source_preparation_seconds=round(cold, 4),
        cached_preparation_seconds=round(warm, 4),
        binary_encoding_and_identity_checks_seconds=round(elapsed, 4),
        bytes=encoded,
        chunk_events=CHUNK_EVENTS,
        chunks=(len(events) + CHUNK_EVENTS - 1) // CHUNK_EVENTS,
        original_event_ids_sha256=expected,
        implicit_sampling=False,
        scope=(
            "Scientific source preparation and binary encoding only; "
            "excludes IPC, network and canvas/PDF rendering"
        ),
    )
    (root / "artifacts/benchmark-three-dimensional.json").write_text(
        json.dumps(evidence, indent=2) + "\n"
    )
    print(json.dumps(evidence))
finally:
    store.close()
