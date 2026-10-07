"""Measure sparse and fragmented full-event contour payloads; no display timing claim."""

import json
import time
from pathlib import Path

import numpy as np
from cytoforge.graph_views import probability_view, resolved_options
from cytoforge.models import GraphOptions

root = Path(__file__).resolve().parents[1]
options = resolved_options(GraphOptions(smooth=False, contour_spacing="10"), "contour")
cases = []
for bins in [160, 384]:
    x, y = np.meshgrid(np.arange(5.0), np.arange(5.0))
    cases.append(("25 sparse tied bins", x.ravel(), y.ravel(), [-1, 11, -1, 11], bins))
rows, columns = np.indices((256, 256))
included = (rows + columns) % 2 == 0
cases.append(
    (
        "32,768 isolated occupied cells",
        (columns[included] + 0.5) / 256,
        (rows[included] + 0.5) / 256,
        [0, 1, 0, 1],
        256,
    )
)
rng = np.random.default_rng(2401)
values = rng.normal(size=(1_000_000, 2))
values[750000:] += [4, 2]
for bins in [160, 384]:
    cases.append(("Million-event bimodal unsmoothed population", *values.T, [-7, 12, -7, 12], bins))
results = []
for name, x, y, limits, bins in cases:
    times = []
    for _ in range(3):
        start = time.perf_counter()
        payload = probability_view(x, y, limits, limits, bins, options)
        times.append(time.perf_counter() - start)
        assert payload["probability_denominator"] == payload["density_count"] == len(x)
        assert sum(payload["density_field"]) == len(x)
        assert all(
            level["estimated_probability"] >= level["probability"] - 1e-12
            for level in payload["probability_levels"]
        )
        assert all(contour["geometry"] == "bin_cells" for contour in payload["contours"])
        if name == "32,768 isolated occupied cells":
            assert payload["contours_truncated"] and payload["contour_vertices"] == 150000
        else:
            assert not payload["contours_truncated"]
    start = time.perf_counter()
    wire = json.dumps(payload, allow_nan=False, separators=(",", ":"))
    serialization = time.perf_counter() - start
    results.append(
        dict(
            scenario=name,
            bins=bins,
            events=len(x),
            repetitions=3,
            median_seconds=float(np.median(times)),
            times_seconds=times,
            serialization_seconds=serialization,
            payload_bytes=len(wire.encode()),
            contour_vertices=payload["contour_vertices"],
            contour_paths=sum(len(c["paths"]) for c in payload["contours"]),
            drawing_limit_reported=payload["contours_truncated"],
        )
    )
evidence = dict(
    status="passed",
    results=results,
    scope="Source full-event histogram, probability and boundary calculation; excludes loading, "
    "IPC and native rendering. Serialization is measured separately. Synthetic data only.",
)
(root / "artifacts/benchmark-contour-boundaries.json").write_text(
    json.dumps(evidence, indent=2) + "\n"
)
print(json.dumps(evidence))
