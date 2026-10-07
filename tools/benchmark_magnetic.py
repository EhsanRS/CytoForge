"""Full authenticated magnetic preview requests on a labelled million-event acquisition."""

from __future__ import annotations

import json
import platform
import time
from pathlib import Path

import numpy as np
from cytoforge.app import create_app
from cytoforge.models import Channel, Gate, Sample, Workspace
from cytoforge.science import ArrayCache, save_events
from fastapi.testclient import TestClient

root = Path(__file__).resolve().parents[1]
directory = root / ".tmp" / f"benchmark-magnetic-{time.time_ns()}"
directory.mkdir(parents=True)
app = create_app(directory)
rng = np.random.default_rng(84)
values = np.vstack(
    [
        rng.normal([1.2, 1.1], 0.02, (100_003, 2)),
        rng.normal([7, 7], 0.04, (899_997, 2)),
    ]
)
sample = Sample(
    name="1M labelled events",
    channels=[Channel(name="X"), Channel(name="Y")],
    event_count=len(values),
)
gates = [
    Gate(
        sample_id=sample.id,
        name=kind,
        kind=kind,
        x="X",
        y=None if kind == "range" else "Y",
        bounds=[-0.5, 0.5] if kind == "range" else [-0.5, 0.5, -0.5, 0.5],
        center=[0, 0],
        radii=[0.5, 0.4],
        vertices=[[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5], [-0.5, 0.5]],
        magnetic={"max_shift": 2.5},
    )
    for kind in ["range", "rectangle", "ellipse", "polygon"]
]
doc = Workspace(name="Magnetic performance truth", samples=[sample], gates=gates)
sample.sha256 = save_events(app.state.store.data_path(doc.id, sample.id), values)
doc = app.state.store.get(app.state.store.create(doc).id)
engine, store = app.state.engine, app.state.store
expected = np.arange(len(values)) < 100_003
before, history = doc.model_dump_json(), store.history(doc.id)
result = {
    "status": "running",
    "machine": platform.platform(),
    "events": len(values),
    "repetitions": 5,
    "scope": "Full authenticated ASGI preview request, model validation, all-event search, "
    "exact counts and response consumption. Application caches are cold for each cold "
    "repetition; operating-system file caches are uncontrolled. Socket transport, native "
    "rendering, request body construction and result parsing are excluded.",
    "cases": [],
}
with TestClient(app, base_url="http://127.0.0.1") as client:
    client.headers["X-CytoForge-Token"] = client.get("/api/bootstrap").json()["token"]
    for gate in doc.gates:
        route = f"/api/workspaces/{doc.id}/gates/preview-magnetic"
        body = {"revision": doc.revision, "gate": gate.model_dump(mode="json")}
        cold, warm, responses = [], [], []
        for _ in range(5):
            engine.cache = ArrayCache()
            engine.magnetic_cache.clear()
            start = time.perf_counter()
            response = client.post(route, json=body)
            cold.append(time.perf_counter() - start)
            assert response.status_code == 200, response.text
            responses.append(response.content)
        for _ in range(5):
            start = time.perf_counter()
            response = client.post(route, json=body)
            warm.append(time.perf_counter() - start)
            assert response.status_code == 200, response.text
            responses.append(response.content)
        assert all(payload == responses[0] for payload in responses)
        report = response.json()["magnetic"]
        assert report["resolved_count"] == 100_003 and report["finite_parent_count"] == 1_000_000
        np.testing.assert_array_equal(engine.mask(doc, sample, gate.id), expected)
        result["cases"].append(
            {
                "kind": gate.kind,
                "cold_seconds": cold,
                "warm_seconds": warm,
                "cold_median_seconds": float(np.median(cold)),
                "warm_median_seconds": float(np.median(warm)),
                "response_bytes": len(responses[0]),
                "resolution": report,
                "full_event_membership_matches_labels": True,
            }
        )
    assert store.get(doc.id).model_dump_json() == before and store.history(doc.id) == history
result["status"] = "passed"
result["workspace_and_history_unchanged"] = True
output = root / "artifacts/benchmark-magnetic.json"
output.write_text(json.dumps(result, indent=2))
print(
    json.dumps(
        {
            "status": result["status"],
            "output": str(output),
            "cases": [
                {k: row[k] for k in ["kind", "cold_median_seconds", "warm_median_seconds"]}
                for row in result["cases"]
            ],
        }
    )
)
