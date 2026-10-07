"""Repeatable, full-data timing. Results are evidence for this machine only."""

import json
import platform
import time
from pathlib import Path

import numpy as np
from cytoforge.models import (
    Channel,
    Compensation,
    Gate,
    QualityRequest,
    Sample,
    Transform,
    Workspace,
    new_id,
)
from cytoforge.quality import calculate, selection
from cytoforge.science import Engine, save_array, save_events
from cytoforge.store import Store

root = Path(__file__).resolve().parents[1]
directory = root / ".tmp/benchmark"
directory.mkdir(parents=True, exist_ok=True)
store = Store(directory)
rng = np.random.default_rng(71)
n, channels = 1_000_000, 16
events = rng.normal(0, 250, (n, channels))
events[:, :2] = rng.lognormal(8, 0.8, (n, 2))
spill = np.eye(channels)
spill[0, 1] = 0.12
comp = Compensation(
    name="Benchmark matrix", detectors=[f"C{i}" for i in range(channels)], matrix=spill.tolist()
)
sample = Sample(
    name="1M events, 16 channels",
    event_count=n,
    channels=[Channel(name=f"C{i}", transform=Transform(kind="logicle")) for i in range(channels)],
    compensation_id=comp.id,
)
doc = Workspace(name="Performance benchmark", samples=[sample], compensations=[comp])
sample.sha256 = save_events(store.data_path(doc.id, sample.id), events)
doc.gates = [
    Gate(
        sample_id=sample.id,
        name=f"Gate {i}",
        kind="rectangle",
        x="C0",
        y="C1",
        bounds=[0, 10000 + i * 200, 0, 10000],
    )
    for i in range(10)
]
doc = store.create(doc)
engine = Engine(store)
del events


def measure(fn, repeats=5):
    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        times.append((time.perf_counter() - start) * 1000)
    return {
        "first_ms": round(times[0], 2),
        "warm_median_ms": round(float(np.median(times[1:])), 2),
        "all_ms": [round(t, 2) for t in times],
    }


results = {
    "machine": platform.platform(),
    "numpy": np.__version__,
    "events": n,
    "channels": channels,
    "notes": (
        "Synthetic data. One host. Includes compensation, transform and full-data histogram; "
        "excludes HTTP and rendering. First runs may use warm OS page cache."
    ),
    "gating_10_rectangles": measure(lambda: engine.gate_counts(doc, sample)),
    "density_160_bins": measure(
        lambda: engine.plot(
            doc, sample, "C0", "C1", sample.channels[0].transform, sample.channels[1].transform
        )
    ),
    "statistics": measure(lambda: engine.summary(doc, sample, doc.gates[0].id, "C0")),
    "cache_bytes": engine.cache.bytes,
}
output = root / "artifacts/benchmark.json"
output.parent.mkdir(exist_ok=True)
output.write_text(json.dumps(results, indent=2))
print(json.dumps(results, indent=2))

# Separate known disturbances exercise every event/channel and the bounded bin cap.
qc_events = np.column_stack([engine.raw(doc, sample), np.zeros(n)])
qc_events[400000:420000, 3] += 3000
qc_dt = np.full(n, 0.001)
qc_dt[700000:720000] = 0.01
qc_events[1:, -1] = np.cumsum(qc_dt[:-1])
qc_sample = Sample(
    name="1M QC events, 16 markers and Time",
    event_count=n,
    channels=sample.channels + [Channel(name="Time", transform=Transform(kind="linear"))],
    compensation_id=comp.id,
    source="Synthetic benchmark",
)
qc_doc = Workspace(name="QC performance reference", samples=[qc_sample], compensations=[comp])
qc_sample.sha256 = save_events(store.data_path(qc_doc.id, qc_sample.id), qc_events)
qc_doc = store.create(qc_doc)
del qc_events
qc_request = QualityRequest(
    revision=qc_doc.revision,
    sample_id=qc_sample.id,
    channels=[f"C{i}" for i in range(16)],
    time_channel="Time",
)
start = time.perf_counter()
qc_result, flags = calculate(qc_doc, qc_request, Engine(store), new_id())
seconds = time.perf_counter() - start
qc_result.data.sha256 = save_array(store.quality_path(qc_doc.id, qc_result.id), flags)
predicted = ~selection(flags, [b.index for b in qc_result.bins if b.suggested], [])
truth = np.zeros(n, dtype=bool)
truth[400000:420000] = truth[700000:720000] = True
true_positive = int((predicted & truth).sum())
evidence = {
    "events": n,
    "markers": 16,
    "time_channel": True,
    "machine": platform.platform(),
    "seconds": round(seconds, 3),
    "bins": len(qc_result.bins),
    "effective_bin_events": qc_result.diagnostics["effective_bin_events"],
    "predicted_removed": int(predicted.sum()),
    "known_anomaly_events": int(truth.sum()),
    "true_positive": true_positive,
    "precision": true_positive / int(predicted.sum()),
    "recall": true_positive / int(truth.sum()),
    "notes": "Synthetic, one host. Detecting inserted shifts is not biological QC validation.",
}
assert evidence["precision"] > 0.95 and evidence["recall"] > 0.95, evidence
(root / "artifacts/qc-benchmark.json").write_text(json.dumps(evidence, indent=2))
(root / "artifacts/qc-benchmark-report.json").write_text(qc_result.model_dump_json())
print(json.dumps(evidence, indent=2))
store.close()
