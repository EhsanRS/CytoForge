"""CPU and memory checks for complete fitted PhenoGraph graphs on known blobs."""

import hashlib
import json
import multiprocessing as mp
import resource
import tempfile
import time
from pathlib import Path

import numpy as np
from cytoforge.graph_clustering import fit

ROOT = Path(__file__).resolve().parents[1]


def measure(directory, count):
    rng = np.random.default_rng(82049)
    truth = np.repeat(np.arange(4), count // 4)
    values = rng.normal(0, 1, (len(truth), 8))
    values[:, 0] += truth * 20000
    baseline = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    started = time.perf_counter()
    labels, report = fit(values, neighbors=30, min_cluster_size=20, restarts=5, seed=47)
    elapsed = time.perf_counter() - started
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    assert len(labels) == count and np.isfinite(labels).all()
    for label in np.unique(labels):
        if label > 0:
            assert len(np.unique(truth[labels == label])) == 1
    assert sum(report["community_sizes"].values()) + report["unassigned_fitted_count"] == count
    assert report["modularity"] == max(report["restart_modularities"])
    record = dict(
        status="passed",
        fitted_events=count,
        features=8,
        neighbors=30,
        louvain_restarts=5,
        seconds=elapsed,
        additional_peak_rss_mib=max(0, peak - baseline) / 1024,
        communities=len(report["community_sizes"]),
        unassigned=report["unassigned_fitted_count"],
        graph_edges=report["graph_edges"],
        modularity=report["modularity"],
        all_fitted_identities_labeled=True,
        no_community_spans_known_separated_blobs=True,
    )
    Path(directory, "measurement.json").write_text(json.dumps(record, indent=2) + "\n")


def main():
    records = []
    for count in (20000, 100000):
        with tempfile.TemporaryDirectory(
            prefix="phenograph-benchmark-", dir=ROOT / ".tmp"
        ) as owned:
            process = mp.get_context("spawn").Process(target=measure, args=(owned, count))
            process.start()
            deadline = time.monotonic() + 240
            while process.is_alive() and time.monotonic() < deadline:
                process.join(0.25)
            if process.is_alive():
                process.terminate()
                process.join(5)
                raise RuntimeError("Owned PhenoGraph benchmark exceeded its deadline")
            assert process.exitcode == 0
            records.append(json.loads(Path(owned, "measurement.json").read_text()))
            print(f"{count} fitted events: {records[-1]['seconds']:.3f}s", flush=True)
    sources = [
        "backend/cytoforge/graph_clustering.py",
        "tools/benchmark_phenograph.py",
        "pyproject.toml",
        "uv.lock",
    ]
    proof = dict(
        status="passed",
        scope="Linux CPU, eight features and four separated synthetic blobs",
        includes="Exact nearest neighbours, Jaccard graph and five seeded Louvain attempts",
        excludes="Fixture generation, full engine startup, rendering and biological accuracy",
        machine_was_not_exclusive=True,
        records=records,
        source_sha256={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in sources},
    )
    (ROOT / "artifacts/phenograph-benchmark.json").write_text(json.dumps(proof, indent=2) + "\n")


if __name__ == "__main__":
    main()
