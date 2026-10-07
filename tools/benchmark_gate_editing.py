"""Authenticated full-event edit previews; include validation, plots and JSON bytes."""

import hashlib
import json
import platform
import statistics
import time
from pathlib import Path

import numpy as np
from cytoforge.app import create_app
from cytoforge.models import Channel, Gate, Sample, Workspace
from cytoforge.science import save_events
from fastapi.testclient import TestClient

root = Path.cwd()
profile = root / ".tmp" / "gate-editing-benchmark"
events, target, repetitions = 1_000_000, 100_003, 5
rng = np.random.default_rng(88)
values = np.vstack(
    [
        rng.normal([1.2, 1.1], 0.008, (target, 2)),
        rng.normal([7, 7], 0.008, (events - target, 2)),
    ]
)
app = create_app(profile)
result = {
    "status": "running",
    "machine": platform.platform(),
    "events": events,
    "repetitions": repetitions,
    "scope": (
        "Full authenticated ASGI request, workspace graph/model validation, isolated draft masks, "
        "all parent events, scientific plot and complete response-byte consumption. Each preview "
        "uses a fresh scientific engine. OS file caches are uncontrolled. Socket transport, native "
        "rendering, request-body construction and result parsing are excluded."
    ),
    "cases": [],
}
with TestClient(app, base_url="http://127.0.0.1") as client:
    client.headers["X-CytoForge-Token"] = client.get("/api/bootstrap").json()["token"]
    store = app.state.store
    sample = Sample(
        name="Million independent labels",
        channels=[Channel(name="X"), Channel(name="Y")],
        event_count=events,
    )
    doc = Workspace(name="Full-event gate edit benchmark", samples=[sample])
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    doc = store.create(doc)
    saved, history = doc.model_dump_json(), store.history(doc.id)
    gates = [
        Gate(sample_id=sample.id, name="Range", kind="range", x="X", bounds=[1, 1.4]),
        Gate(
            sample_id=sample.id,
            name="Rectangle",
            kind="rectangle",
            x="X",
            y="Y",
            bounds=[1, 1.4, 0.9, 1.3],
        ),
        Gate(
            sample_id=sample.id,
            name="Ellipse",
            kind="ellipse",
            x="X",
            y="Y",
            center=[1.2, 1.1],
            radii=[0.2, 0.2],
            angle=0.4,
        ),
        Gate(
            sample_id=sample.id,
            name="Polygon",
            kind="polygon",
            x="X",
            y="Y",
            vertices=[(1, 0.9), (1.4, 0.9), (1.4, 1.3), (1, 1.3)],
        ),
    ]
    for gate in gates:
        body = json.dumps(
            {
                "revision": doc.revision,
                "gate": gate.model_dump(),
                "bounds": [0, 8] if gate.kind == "range" else [0, 8, 0, 8],
                "bins": 96,
            }
        )
        durations, expected_bytes = [], None
        for _ in range(repetitions):
            started = time.perf_counter()
            response = client.post(
                f"/api/workspaces/{doc.id}/gates/preview-shape",
                content=body,
                headers={"Content-Type": "application/json"},
            )
            payload = response.content
            durations.append(time.perf_counter() - started)
            assert response.status_code == 200, response.text
            if expected_bytes is None:
                expected_bytes = payload
            assert payload == expected_bytes
            data = json.loads(payload)
            assert data["count"] == target and data["parent_count"] == events
            assert data["plot"]["finite_count"] == events
            assert sum(data["plot"]["counts"]) == events
            assert len(data["plot"]["overlays"]) == 1
        result["cases"].append(
            {
                "kind": gate.kind,
                "seconds": durations,
                "median_seconds": statistics.median(durations),
                "response_bytes": len(expected_bytes),
                "complete_response_sha256": hashlib.sha256(expected_bytes).hexdigest(),
                "all_repeated_response_bytes_identical": True,
                "exact_count_matches_independent_labels": target,
                "full_parent_plot_event_sum": events,
            }
        )
    assert store.get(doc.id).model_dump_json() == saved and store.history(doc.id) == history
    result["workspace_and_history_unchanged"] = True
result["status"] = "passed"
path = root / "artifacts/benchmark-gate-editing.json"
path.write_text(json.dumps(result, indent=2) + "\n")
print(
    json.dumps(
        {
            "status": result["status"],
            "events": events,
            "median_seconds": {c["kind"]: c["median_seconds"] for c in result["cases"]},
        }
    )
)
