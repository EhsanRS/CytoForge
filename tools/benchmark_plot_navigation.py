"""Measure metadata-only navigation separately from snapshot I/O and rendering."""

import json
import statistics
import tempfile
import time
from pathlib import Path

from cytoforge.models import Channel, Gate, Sample, Workspace
from cytoforge.plot_navigation import NavigationRequest, plan_navigation
from cytoforge.store import Store

root = Path(__file__).resolve().parents[1]
started = time.perf_counter()
samples, gates = [], []
for i in range(2500):
    sample = Sample(
        name=f"Acquisition {i:04}", event_count=1000000, channels=[Channel(name=n) for n in "XYZ"]
    )
    samples.append(sample)
    parent = None
    for name in ("Cells", "Positive", "Subset"):
        gate = Gate(
            sample_id=sample.id, parent_id=parent, name=name, kind="range", x="X", bounds=[0, 5]
        )
        gates.append(gate)
        parent = gate.id
doc = Workspace(name="Metadata navigation benchmark", samples=samples, gates=gates)
generation_seconds = time.perf_counter() - started
state = {
    "workspaceId": doc.id,
    "sampleId": samples[0].id,
    "gateId": gates[2].id,
    "coordinateGateId": gates[0].id,
    "backgateId": gates[1].id,
    "x": "X",
    "y": "Y",
    "mode": "3d",
    "threeD": {"z": "Z", "yaw": 1.2},
    "bounds": [0, 5, 0, 5, 0, 5],
}
request = NavigationRequest.model_validate(
    {
        "revision": 0,
        "initiator": "main",
        "direction": "next",
        "views": [{"id": "main", "state": state}]
        + [{"id": f"{i:032x}", "state": state} for i in range(32)],
    }
)
timings = []
remembered = [{**state, "gateId": gates[1].id, "threeD": {"z": "Z", "yaw": 1.1}}] + [
    {**state, "gateId": f"{i:032x}"} for i in range(191)
]
population_request = NavigationRequest.model_validate(
    {
        "revision": 0,
        "initiator": "main",
        "direction": "population",
        "targetGateId": gates[1].id,
        "rememberedViews": remembered,
        "views": [{"id": "main", "state": state}],
    }
)
population_timings = []
with tempfile.TemporaryDirectory(prefix="navigation-benchmark-", dir=root / ".tmp") as temporary:
    store = Store(Path(temporary))
    try:
        store.create(doc)
        started = time.perf_counter()
        loaded = store.get(doc.id)
        snapshot_seconds = time.perf_counter() - started
        for _ in range(7):
            started = time.perf_counter()
            plan = plan_navigation(loaded, request)
            timings.append(time.perf_counter() - started)
            assert plan["available"] and len(plan["views"]) == 33
            assert {v["state"]["sampleId"] for v in plan["views"]} == {samples[1].id}
        for _ in range(7):
            started = time.perf_counter()
            plan = plan_navigation(loaded, population_request)
            population_timings.append(time.perf_counter() - started)
            assert plan["restored_view"] and plan["views"][0]["state"]["gateId"] == gates[1].id
            assert plan["views"][0]["state"]["threeD"]["yaw"] == 1.1
        assert not list(store.events_dir.rglob("*.npz"))
        assert store.revision(doc.id) == 0 and not store.history(doc.id)["can_undo"]
    finally:
        store.close()
result = {
    "status": "passed",
    "samples": 2500,
    "populations": 7500,
    "native_views": 33,
    "repetitions": len(timings),
    "workspace_generation_seconds": generation_seconds,
    "snapshot_load_seconds": snapshot_seconds,
    "navigation_seconds": timings,
    "navigation_median_seconds": statistics.median(timings),
    "remembered_population_views": len(remembered),
    "population_navigation_seconds": population_timings,
    "population_navigation_median_seconds": statistics.median(population_timings),
    "event_buffers_opened": 0,
    "scientific_mutations": 0,
    "scope": "Synthetic metadata only; no event payloads, network, renderer or PDF measured.",
}
(root / "artifacts/benchmark-plot-navigation.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
