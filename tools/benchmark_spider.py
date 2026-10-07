"""Full-parent spider previews, independent polar labels and bounded family cache."""

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

import numpy as np
from cytoforge.app import create_app
from cytoforge.models import (
    Channel,
    Gate,
    GateDimension,
    GatePartition,
    Sample,
    SpiderGeometry,
    Workspace,
)
from cytoforge.science import save_events
from cytoforge.spider import labels
from fastapi.testclient import TestClient

root = Path.cwd()
profile = root / ".tmp" / f"spider-benchmark-{time.time_ns()}"
rng = np.random.default_rng(9046)
raw = rng.uniform(-6, 6, size=(1_000_000, 2))
geometry = SpiderGeometry(center=(0.2, -0.15), scale=(2, 3), angles=(0.2, 1.4, 3.2, 4.9))
phi = np.mod(np.arctan2((raw[:, 1] + 0.15) / 3, (raw[:, 0] - 0.2) / 2) - 0.2, 2 * np.pi)
expected = np.array([2, 1, 4, 3], dtype=np.uint8)[
    np.searchsorted(np.array(geometry.angles) - 0.2, phi, side="right") - 1
]
expected_counts = [int((expected == member).sum()) for member in range(1, 5)]
direct = []
for _ in range(5):
    started = time.perf_counter()
    np.testing.assert_array_equal(labels(raw[:, 0], raw[:, 1], geometry), expected)
    direct.append(time.perf_counter() - started)
with TestClient(create_app(profile), base_url="http://127.0.0.1") as client:
    client.headers["X-CytoForge-Token"] = client.get("/api/bootstrap").json()["token"]
    store = client.app.state.store
    sample = Sample(
        name="Million angular events",
        event_count=len(raw),
        channels=[Channel(name="X"), Channel(name="Y")],
    )
    doc = Workspace(name="Million-event spider preview", samples=[sample])
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), raw)
    doc = store.create(doc)
    gate = Gate(
        name="Spider",
        sample_id=sample.id,
        kind="spider",
        dimensions=[GateDimension(channel="X"), GateDimension(channel="Y")],
        partition=GatePartition(kind="spider", member=1),
        spider=geometry,
    )
    before = doc.model_dump_json(), store.history(doc.id)
    timings = []
    for _ in range(6):
        started = time.perf_counter()
        response = client.post(
            f"/api/workspaces/{doc.id}/gates/preview-shape",
            json={"revision": 0, "gate": gate.model_dump(), "bins": 96, "mode": "density"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["parent_count"] == len(raw)
        assert [c["count"] for c in payload["partition_counts"]] == expected_counts
        timings.append(time.perf_counter() - started)
    assert (store.get(doc.id).model_dump_json(), store.history(doc.id)) == before
    response = client.post(
        f"/api/workspaces/{doc.id}/gates", json={"revision": 0, "gate": gate.model_dump()}
    )
    assert response.status_code == 200, response.text
    doc = Workspace.model_validate(response.json())
    engine = client.app.state.engine
    cold = time.perf_counter()
    masks = [engine.mask(doc, sample, g.id) for g in doc.gates]
    cold = time.perf_counter() - cold
    assert [int(m.sum()) for m in masks] == expected_counts
    np.testing.assert_array_equal(np.sum(masks, axis=0), np.ones(len(raw), dtype=int))
    for g in doc.gates:
        np.testing.assert_array_equal(
            engine.mask(doc, sample, g.id), expected == g.partition.member
        )
    family_keys = [key for key in engine.cache.items if "spider" in key]
    assert len(family_keys) == 1
    assert engine.cache.items[family_keys[0]].nbytes == len(raw)
    assert not engine.cache.items[family_keys[0]].flags.writeable
    assert engine.cache.bytes <= engine.cache.max_bytes
    result = {
        "status": "passed",
        "checked_at": datetime.now(UTC).isoformat(),
        "events": len(raw),
        "counts": expected_counts,
        "independent_angular_labels_match_every_event": True,
        "finite_parent_coverage_exactly_once": True,
        "direct_label_seconds": direct,
        "direct_label_median_seconds": median(direct),
        "preview_seconds": timings,
        "preview_median_after_first_seconds": median(timings[1:]),
        "cold_four_member_mask_seconds": cold,
        "workspace_and_history_unchanged": True,
        "saved_family_labels_cached_once_bytes": len(raw),
        "engine_cache_bytes": engine.cache.bytes,
        "engine_cache_bound_bytes": engine.cache.max_bytes,
        "scope": (
            "Local TestClient validation, isolated draft engine, full-parent plotting and JSON. "
            "Direct label times include independent equality checks. Hardware rendering and "
            "socket transport excluded; host activity and OS caches uncontrolled."
        ),
    }
    (root / "artifacts/benchmark-spider-preview.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print(json.dumps(result))
