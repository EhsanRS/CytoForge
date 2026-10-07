"""Full-event proliferation benchmark with independently sampled latent labels."""

import json
import math
import platform
import time
from pathlib import Path

import numpy as np
from cytoforge.models import (
    AnalysisInput,
    Channel,
    FitConstraint,
    ProliferationRequest,
    Sample,
    Workspace,
    new_id,
    proliferation_statistics,
)
from cytoforge.proliferation import calculate
from cytoforge.science import Engine, save_events
from cytoforge.store import Store

root = Path(__file__).resolve().parents[1]
evidence = {"machine": platform.platform(), "cases": []}
n = 1_000_000
for background_sd in (0, 6):
    rng = np.random.default_rng(832 + background_sd)
    labels = rng.choice(6, n, p=[0.12, 0.18, 0.25, 0.22, 0.15, 0.08])
    dye = 1024 * 0.5**labels * rng.lognormal(0, math.sqrt(math.log1p(0.25**2)), n)
    values = dye + rng.normal(20, background_sd, n)
    store = Store(root / ".tmp" / f"proliferation-benchmark-{time.time_ns()}")
    try:
        sample = Sample(name="Synthetic CFSE", event_count=n, channels=[Channel(name="CFSE")])
        workspace = Workspace(name="Proliferation benchmark", samples=[sample])
        sample.sha256 = save_events(store.data_path(workspace.id, sample.id), values[:, None])
        workspace = store.create(workspace)
        request = ProliferationRequest(
            revision=workspace.revision,
            channel="CFSE",
            inputs=[AnalysisInput(sample_id=sample.id)],
            name="Million-event CFSE",
            generations=5,
            undivided_mean=FitConstraint(initial=1044),
            background=20,
            background_sd=background_sd,
        )
        started = time.monotonic()
        result, arrays = calculate(workspace, request, Engine(store), new_id())
        seconds = time.monotonic() - started
        fit = result.fits[0]
        truth = np.bincount(labels, minlength=6) / n
        truth_metrics = proliferation_statistics(np.bincount(labels, minlength=6))
        np.testing.assert_allclose(fit.fractions, truth, atol=0.015)
        assert fit.diagnostics["converged"]
        assert arrays[sample.id].shape == (n, 7)
        assert sum(fit.assigned_counts) == fit.data.fitted_count
        record = {
            "events": n,
            "seconds": round(seconds, 3),
            "background_sd": background_sd,
            "histogram_bins": request.bins,
            "fitted_events": fit.data.fitted_count,
            "latent_fractions": truth.tolist(),
            "recovered_fractions": fit.fractions,
            "parameters": fit.parameters,
            "largest_absolute_fraction_error": float(np.max(abs(np.array(fit.fractions) - truth))),
            "latent_statistics": truth_metrics.model_dump(),
            "recovered_statistics": fit.statistics.model_dump(),
            "converged": fit.diagnostics["converged"],
            "normalized_rmsd": fit.diagnostics["normalized_rmsd"],
            "probability_array_bytes": arrays[sample.id].nbytes,
            "versions": result.versions,
        }
        evidence["cases"].append(record)
        print(f"One million events, background SD {background_sd}: {seconds:.3f}s", flush=True)
    finally:
        store.close()
evidence["notes"] = (
    "Synthetic, one host, six lognormal generations. Independently sampled latent labels "
    "verify recovery under this specified model, not biological or FlowJo fitting accuracy. "
    "Timings include fitting, event mapping and report validation; exclude input generation, "
    "disk writes, HTTP and rendering."
)
(root / "artifacts/proliferation-benchmark.json").write_text(json.dumps(evidence, indent=2))
