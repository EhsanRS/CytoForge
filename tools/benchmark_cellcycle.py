"""Full-event DJF benchmark with independently labeled synthetic DNA."""

import json
import platform
import time
from pathlib import Path

import numpy as np
from cytoforge.cellcycle import calculate
from cytoforge.models import AnalysisInput, CellCycleRequest, Channel, Sample, Workspace, new_id
from cytoforge.science import Engine, save_events
from cytoforge.store import Store

root = Path(__file__).resolve().parents[1]
rng = np.random.default_rng(59)
n = 1_000_000
labels = rng.choice(3, n, p=[0.5, 0.3, 0.2])
dna = np.empty(n)
for phase, mean in [(0, 100), (2, 198)]:
    selected = labels == phase
    dna[selected] = rng.normal(mean, 0.04 * mean, selected.sum())
selected = labels == 1
latent = 100 + 98 * rng.beta(2, 2, selected.sum())
dna[selected] = rng.normal(latent, 0.04 * latent)
directory = root / ".tmp" / f"cellcycle-benchmark-{time.time_ns()}"
store = Store(directory)
try:
    sample = Sample(name="Synthetic DNA benchmark", event_count=n, channels=[Channel(name="DNA")])
    workspace = Workspace(name="Cell-cycle benchmark", samples=[sample])
    sample.sha256 = save_events(store.data_path(workspace.id, sample.id), dna[:, None])
    workspace = store.create(workspace)
    request = CellCycleRequest(
        revision=workspace.revision,
        channel="DNA",
        inputs=[AnalysisInput(sample_id=sample.id)],
        range_max=280.0,
        name="Million-event DNA",
    )
    started = time.monotonic()
    result, arrays = calculate(workspace, request, Engine(store), new_id())
    seconds = time.monotonic() - started
    fit = result.fits[0]
    truth = np.bincount(labels, minlength=3) / n
    evidence = {
        "method": "djf",
        "machine": platform.platform(),
        "events": n,
        "seconds": round(seconds, 3),
        "histogram_bins": request.bins,
        "fitted_events": fit.data.fitted_count,
        "latent_fractions": truth.tolist(),
        "recovered_fractions": fit.fractions,
        "parameters": fit.parameters,
        "largest_absolute_fraction_error": float(np.max(abs(np.array(fit.fractions) - truth))),
        "converged": fit.diagnostics["converged"],
        "probability_array_bytes": arrays[sample.id].nbytes,
        "notes": "Synthetic, one host. Known latent labels do not establish biological accuracy.",
    }
    np.testing.assert_allclose(fit.fractions, truth, atol=0.01)
    (root / "artifacts/cellcycle-benchmark.json").write_text(json.dumps(evidence, indent=2))
    print(json.dumps(evidence, indent=2))
finally:
    store.close()
