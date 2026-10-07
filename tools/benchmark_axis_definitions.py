"""Million-event mixed raw/fixed-ratio plots checked against independent NumPy bins."""

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

import numpy as np
from cytoforge.app import create_app
from cytoforge.models import Channel, Compensation, PlotDimension, Sample, Transform, Workspace
from cytoforge.science import save_events
from fastapi.testclient import TestClient

rng = np.random.default_rng(9513)
values = np.column_stack([rng.normal(0, 5, 1_000_000), rng.uniform(0.05, 8, 1_000_000)])
values[::4096, 1] = 0.8
values[0, 0] = 0.2
with np.errstate(divide="ignore", invalid="ignore"):
    x = np.arcsinh(values[:, 0] / 2)
    y = 1.5 * (values[:, 0] / 2 - 0.1) / (values[:, 1] / 4 - 0.2)
finite = np.isfinite(x) & np.isfinite(y)
visible = finite & (x >= -3) & (x <= 3) & (y >= -12) & (y <= 12)
expected = (
    np.histogram2d(x[visible], y[visible], bins=96, range=[[-3, 3], [-12, 12]])[0]
    .T.ravel()
    .astype(int)
)
profile = Path.cwd() / ".tmp" / f"axis-benchmark-{time.time_ns()}"
with TestClient(create_app(profile), base_url="http://127.0.0.1") as client:
    client.headers["X-CytoForge-Token"] = client.get("/api/bootstrap").json()["token"]
    store = client.app.state.store
    sample = Sample(
        name="Million mixed coordinates",
        event_count=len(values),
        channels=[Channel(name="X"), Channel(name="Y")],
    )
    matrix = Compensation(name="Known diagonal", detectors=["X", "Y"], matrix=[[2, 0], [0, 4]])
    doc = Workspace(name="Million-event explicit axes", samples=[sample], compensations=[matrix])
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    doc = store.create(doc)
    raw = PlotDimension(
        channel="X", compensation_ref="uncompensated", transform=Transform(kind="asinh", cofactor=2)
    )
    ratio = PlotDimension(
        channel="R",
        ratio_channels=("X", "Y"),
        compensation_ref=matrix.id,
        ratio_a=1.5,
        ratio_b=0.1,
        ratio_c=0.2,
    )
    before = doc.model_dump_json(), store.history(doc.id)
    durations = []
    for _ in range(6):
        started = time.perf_counter()
        response = client.get(
            f"/api/workspaces/{doc.id}/samples/{sample.id}/plot",
            params=dict(
                x="X",
                y="R",
                x_dimension=raw.model_dump_json(),
                y_dimension=ratio.model_dump_json(),
                mode="density",
                bins=96,
                bounds="[-3,3,-12,12]",
            ),
        )
        elapsed = time.perf_counter() - started
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["count"] == len(values)
        assert payload["finite_count"] == int(finite.sum())
        assert payload["visible_count"] == int(visible.sum())
        np.testing.assert_array_equal(payload["counts"], expected)
        durations.append(elapsed)
    assert (store.get(doc.id).model_dump_json(), store.history(doc.id)) == before
    cache = client.app.state.engine.cache
    assert cache.bytes <= cache.max_bytes
    result = dict(
        status="passed",
        checked_at=datetime.now(UTC).isoformat(),
        events=len(values),
        finite_events=int(finite.sum()),
        visible_events=int(visible.sum()),
        every_density_bin_matches_independent_compensation_ratio_transform=True,
        workspace_and_history_unchanged=True,
        request_seconds=durations,
        median_after_first_seconds=median(durations[1:]),
        cache_bytes=cache.bytes,
        cache_limit_bytes=cache.max_bytes,
        scope=(
            "Local TestClient request/response with full-event bins; excludes sockets and native "
            "rendering. Host load and OS file cache are uncontrolled."
        ),
    )
Path("artifacts/benchmark-axis-definitions.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
