"""Time full million-event plotting calculations; preserve explicit display limits."""

import json
import time
from pathlib import Path

import numpy as np
from cytoforge.models import Channel, Sample, Workspace
from cytoforge.plotting import plot_payload
from cytoforge.science import Engine, save_events
from cytoforge.store import Store

root = Path(__file__).resolve().parents[1]
folder = root / ".tmp" / f"graph-benchmark-{time.time_ns()}"
rng = np.random.default_rng(4096)
values = rng.normal(size=(1_000_000, 2))
values[800000:] += [4, 2]
values[-20:] = [50, -50]
sample = Sample(
    name="Million-event two-mode graph truth",
    channels=[Channel(name="X"), Channel(name="Y")],
    event_count=len(values),
)
doc = Workspace(name="Graph benchmark", samples=[sample])
store = Store(folder)
try:
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    doc = store.create(doc)
    engine = Engine(store)
    results = []
    for bins in [160, 384]:
        for mode in ["cdf", "contour", "zebra", "pseudocolor", "scatter"]:
            start = time.perf_counter()
            payload = plot_payload(
                doc,
                engine,
                sample.id,
                "X",
                None if mode == "cdf" else "Y",
                mode=mode,
                bins=bins,
                graph_options={"axis_extent": "full"},
            )
            elapsed = time.perf_counter() - start
            assert (
                payload["count"]
                == payload["finite_count"]
                == payload["visible_count"]
                == len(values)
            )
            if mode == "cdf":
                expected = [
                    int(np.count_nonzero(values[:, 0] <= edge)) for edge in payload["edges"]
                ]
                assert payload["cdf_counts"] == expected
            elif mode != "scatter":
                assert sum(payload["counts"]) == len(values)
                assert abs(sum(payload["density_field"]) - len(values)) < 0.001
            wire = json.dumps(payload, allow_nan=False, separators=(",", ":"))
            results.append(
                dict(
                    mode=mode,
                    bins=bins,
                    seconds=round(elapsed, 4),
                    payload_bytes=len(wire.encode()),
                    population=payload["count"],
                    finite=payload["finite_count"],
                    visible=payload["visible_count"],
                    displayed_markers=payload.get("displayed_count"),
                    contour_vertices=payload.get("contour_vertices"),
                    contours_truncated=payload.get("contours_truncated"),
                    outlier_count=payload.get("outlier_count"),
                )
            )
    evidence = dict(
        status="passed",
        events=len(values),
        profile=str(folder),
        source_sha256=sample.sha256,
        results=results,
        scope=(
            "Scientific source payload calculation; excludes native canvas/PDF rendering "
            "and IPC serialization"
        ),
    )
    (root / "artifacts/benchmark-graphs.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence))
finally:
    store.close()
