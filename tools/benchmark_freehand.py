"""Full-event dense-outline masks with independently known membership."""

import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from cytoforge.science import polygon_mask

vertices = [
    (float(np.cos(a)), float(np.sin(a))) for a in np.linspace(0, 2 * np.pi, 2000, endpoint=False)
]
cases = []
for name, inside in [
    ("rare_cluster", 100003),
    ("dense_inside", 1000000),
    ("spread_inside", 1000000),
]:
    x, y = np.full(1000000, 8.0), np.full(1000000, 8.0)
    x[:inside], y[:inside] = 0.125, 0.25
    if name == "spread_inside":
        rng = np.random.default_rng(7301)
        x, y = rng.uniform(-0.4, 0.4, size=(2, 1000000))
    expected = np.arange(1000000) < inside
    timings = []
    for _ in range(5):
        started = time.perf_counter()
        result = polygon_mask(x, y, vertices)
        timings.append(time.perf_counter() - started)
        assert np.array_equal(result, expected)
    cases.append(
        {
            "name": name,
            "events": len(x),
            "vertices": len(vertices),
            "selected": int(result.sum()),
            "repetitions": len(timings),
            "seconds": timings,
            "median_seconds": statistics.median(timings),
            "matches_independent_labels": True,
        }
    )
report = {
    "status": "passed",
    "checked_at": datetime.now(UTC).isoformat(),
    "cases": cases,
    "scope": "Direct float64 scientific polygon masks, allocation included; "
    "API/transport/rendering excluded. OS caches and other host activity uncontrolled.",
    "source": "backend/cytoforge/science.py",
}
Path("artifacts/benchmark-freehand-mask.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
