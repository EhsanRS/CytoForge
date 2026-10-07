"""Measure captured event storage and integrity-aware cached population masks."""

import hashlib
import json
import tempfile
import time
from pathlib import Path

import numpy as np
from cytoforge.models import Channel, Gate, Sample, Transform, Workspace
from cytoforge.population_snapshot import capture
from cytoforge.science import Engine, save_events
from cytoforge.store import Store

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(
        prefix="population-snapshot-benchmark-", dir=ROOT / ".tmp"
    ) as owned:
        store = Store(Path(owned))
        try:
            count = 1048576
            document = Workspace(name="Captured population benchmark")
            sample = Sample(
                name="Million events",
                event_count=count,
                channels=[Channel(name=f"D{i}") for i in range(8)],
            )
            values = np.tile((np.arange(count) % 128)[:, None], (1, 8)).astype(float)
            sample.sha256 = save_events(store.data_path(document.id, sample.id), values)
            document.samples.append(sample)
            source = Gate(
                sample_id=sample.id,
                name="Selected events",
                kind="range",
                x="D0",
                x_transform=Transform(kind="linear"),
                bounds=[20, 29.9],
            )
            document.gates.append(source)
            engine = Engine(store)
            started = time.perf_counter()
            member = capture(document, sample.id, source.id, "Captured selection", True, engine)
            capture_seconds = time.perf_counter() - started
            document.gates.append(member)
            expected = (values[:, 0] >= 20) & (values[:, 0] <= 29.9)
            engine = Engine(store)
            started = time.perf_counter()
            np.testing.assert_array_equal(engine.mask(document, sample, member.id), expected)
            first_mask_seconds = time.perf_counter() - started
            timings = []
            for _ in range(20):
                started = time.perf_counter()
                result = engine.mask(document, sample, member.id)
                timings.append(time.perf_counter() - started)
                np.testing.assert_array_equal(result, expected)
            record = dict(
                status="passed",
                acquired_events=count,
                acquired_detectors=8,
                selected_events=int(expected.sum()),
                capture_seconds=capture_seconds,
                first_verified_mask_seconds=first_mask_seconds,
                median_cached_verified_mask_seconds=float(np.median(timings)),
                packed_file_bytes=store.membership_path(document.id, member.membership.id)
                .stat()
                .st_size,
                exact_event_identities_verified=True,
            )
        finally:
            store.close()
    sources = [
        "backend/cytoforge/population_snapshot.py",
        "backend/cytoforge/science.py",
        "backend/cytoforge/models.py",
        "backend/cytoforge/store.py",
        "tools/benchmark_population_snapshots.py",
    ]
    proof = dict(
        status="passed",
        scope="Linux CPU and generated acquired events",
        records=[record],
        machine_was_not_exclusive=True,
        excludes="Fixture setup, desktop rendering and biological truth",
        source_sha256={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in sources},
    )
    (ROOT / "artifacts/population-snapshots-benchmark.json").write_text(
        json.dumps(proof, indent=2) + "\n"
    )
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
