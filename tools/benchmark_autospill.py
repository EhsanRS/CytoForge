"""Measure full-population AutoSpill regression on synthetic acquired controls."""

import json
import platform
import time
from pathlib import Path

import numpy as np
from cytoforge.autospill import AutoSpillRequest, calculate
from cytoforge.models import Channel, Sample, Workspace
from cytoforge.science import Engine, save_events
from cytoforge.store import Store

root = Path(__file__).resolve().parents[1]
run_dir = root / ".tmp" / f"benchmark-autospill-{time.time_ns()}"
store = Store(run_dir)
rng = np.random.default_rng(54392)
detectors = [f"D{i + 1}" for i in range(8)]
matrix = rng.uniform(0.005, 0.08, (8, 8))
np.fill_diagonal(matrix, 1)
doc = Workspace(name="Synthetic AutoSpill benchmark")
for primary in range(8):
    true = np.zeros((50000, 8))
    true[:, primary] = rng.lognormal(np.log(4500), 0.8, len(true))
    values = true @ matrix + rng.normal(0, 8, true.shape) + 35
    sample = Sample(
        name=detectors[primary],
        event_count=len(values),
        channels=[Channel(name=n) for n in detectors],
    )
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    doc.samples.append(sample)
doc = store.create(doc)
request = AutoSpillRequest(
    revision=doc.revision,
    detectors=detectors,
    auto_cleanup=False,
    controls=[
        dict(name=n, primary_detector=n, sample_id=s.id)
        for n, s in zip(detectors, doc.samples, strict=True)
    ],
)
started = time.perf_counter()
result = calculate(doc, request, Engine(store))
elapsed = time.perf_counter() - started
error = float(np.max(np.abs(np.asarray(result.compensation.matrix) - matrix)))
assert result.diagnostics["converged"]
assert error < 0.0005
evidence = dict(
    machine=platform.platform(),
    detectors=8,
    controls=8,
    events_per_control=50000,
    acquired_events=400000,
    regression_input_events=400000,
    seconds=elapsed,
    iterations=result.diagnostics["iterations"],
    final_residual=result.diagnostics["final_max_error"],
    physical_matrix_error=error,
    versions=result.compensation.provenance["software"],
    notes="Synthetic controls, one host, all selected events. Includes input hashing, preparation, "
    "regression, refinement and diagnostics. Excludes input generation, file writes, "
    "automatic scatter cleanup, HTTP, rendering and competing workloads.",
)
(root / "artifacts/benchmark-autospill.json").write_text(json.dumps(evidence, indent=2))
print(json.dumps(evidence, indent=2))
