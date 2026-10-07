"""One million independent alias measurements, bounded truth checks and shared correction RAM."""

import hashlib
import json
import resource
import time
from pathlib import Path

import numpy as np
from cytoforge import channel_aliases
from cytoforge.concatenation import CHUNK_EVENTS
from cytoforge.models import Channel, Compensation, Gate, Sample, Transform, Workspace
from cytoforge.science import Engine
from cytoforge.store import Store


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def truth(start, stop):
    events = np.arange(start, stop)
    return np.column_stack((events % 4096 / 17 - 50, (events * 7) % 2048 / 31 + 5, events % 3))


root = Path.cwd()
directory = root / ".tmp" / f"benchmark-aliases-{time.time_ns()}"
store = Store(directory)
count = 1_000_000
sample = Sample(
    name="Million-event detector truth",
    event_count=count,
    channels=[
        Channel(name=n, transform=Transform(kind="asinh", cofactor=150))
        for n in ["FL1", "FL2", "FL3"]
    ],
)
matrix = Compensation(name="Known basis", detectors=["FL1", "FL2"], matrix=[[2, 0], [0, 4]])
sample.compensation_id = matrix.id
doc = Workspace(name="Alias performance", samples=[sample], compensations=[matrix])
try:
    path = store.data_path(doc.id, sample.id)
    values = np.lib.format.open_memmap(path, mode="w+", dtype="<f8", shape=(count, 3))
    for start in range(0, count, CHUNK_EVENTS):
        stop = min(count, start + CHUNK_EVENTS)
        values[start:stop] = truth(start, stop)
    values.flush()
    del values
    sample.sha256 = digest(path)
    store.create(doc)
    doc = store.get(doc.id)
    request = channel_aliases.Request(
        revision=doc.revision,
        mappings=[
            dict(
                sample_id=sample.id,
                bindings=[dict(name="CD3", source="FL1"), dict(name="CD4", source="FL2")],
            )
        ],
    )
    started = time.perf_counter()
    _, preview = channel_aliases.plan(doc, request)
    preview_seconds = time.perf_counter() - started
    started = time.perf_counter()
    doc = store.mutate(
        doc.id,
        "Harmonize million-event panel",
        lambda current: channel_aliases.apply(
            current,
            channel_aliases.Apply(**request.model_dump(), review_hash=preview["review_hash"]),
        ),
        doc.revision,
    )
    apply_seconds = time.perf_counter() - started
    sample = doc.samples[0]
    engine = Engine(store)
    before = doc.model_dump_json()
    started = time.perf_counter()
    raw_columns = [engine.column(doc, sample, name, compensated=False) for name in ["CD3", "CD4"]]
    raw_lookup_seconds = time.perf_counter() - started
    assert all(isinstance(column, np.memmap) and not column.flags.owndata for column in raw_columns)
    assert engine.cache.bytes == 0
    started = time.perf_counter()
    corrected = [engine.column(doc, sample, name) for name in ["CD3", "CD4"]]
    correction_seconds = time.perf_counter() - started
    correction_bytes = engine.cache.bytes
    physical = [engine.column(doc, sample, name) for name in ["FL1", "FL2"]]
    assert engine.cache.bytes == correction_bytes == count * 2 * 8
    assert all(np.shares_memory(a, b) for a, b in zip(corrected, physical, strict=True))
    max_error, expected_count = 0.0, 0
    for start in range(0, count, CHUNK_EVENTS):
        stop = min(count, start + CHUNK_EVENTS)
        expected = truth(start, stop)[:, :2]
        np.testing.assert_array_equal(
            np.column_stack([c[start:stop] for c in raw_columns]), expected
        )
        actual = np.column_stack([c[start:stop] for c in corrected])
        np.testing.assert_array_equal(actual, expected / [2, 4])
        max_error = max(max_error, float(np.abs(actual - expected / [2, 4]).max()))
        expected_count += int(
            np.count_nonzero((expected[:, 0] / 2 >= 0) & (expected[:, 0] / 2 < 30))
        )
    gate = Gate(
        sample_id=sample.id,
        name="Known corrected CD3",
        kind="range",
        x="CD3",
        bounds=[0, 30],
        x_transform=Transform(),
    )
    temporary = doc.model_copy(deep=True)
    temporary.gates.append(gate)
    started = time.perf_counter()
    actual_count = int(np.count_nonzero(engine.mask(temporary, sample, gate.id)))
    gate_seconds = time.perf_counter() - started
    assert actual_count == expected_count
    assert digest(path) == sample.sha256
    assert store.get(doc.id).model_dump_json() == before
    result = dict(
        status="passed",
        events=count,
        acquired_parameters=3,
        alias_parameters=2,
        independently_verified_measurements=count * 4,
        maximum_error=max_error,
        raw_columns_are_memory_mapped_views=True,
        alias_correction_shares_physical_ram=True,
        corrected_array_cache_bytes=correction_bytes,
        additional_alias_cache_bytes=0,
        preview_seconds=preview_seconds,
        apply_seconds=apply_seconds,
        raw_lookup_seconds=raw_lookup_seconds,
        cold_correction_seconds=correction_seconds,
        alias_gate_seconds=gate_seconds,
        expected_gate_events=expected_count,
        actual_gate_events=actual_count,
        original_event_sha256=sample.sha256,
        original_event_bytes_unchanged=True,
        scientific_reads_leave_workspace_unchanged=True,
        peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        validation_chunk_events=CHUNK_EVENTS,
        scope=(
            "Local Linux x64 in-process benchmark; fixture/OS cache warm, "
            "no cache-control or hardware-GPU claim"
        ),
        directory=str(directory),
    )
    (root / "artifacts" / "benchmark-channel-aliases.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in [
                    "status",
                    "events",
                    "maximum_error",
                    "preview_seconds",
                    "apply_seconds",
                    "cold_correction_seconds",
                    "alias_gate_seconds",
                    "additional_alias_cache_bytes",
                ]
            }
        )
    )
finally:
    store.close()
