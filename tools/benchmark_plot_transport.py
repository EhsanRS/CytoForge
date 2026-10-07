"""Compare complete authenticated plot responses against a preserved local app."""

import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

import fastapi
import numpy as np
from cytoforge.app import create_app
from cytoforge.models import Channel, Gate, Sample, Workspace
from cytoforge.science import save_events
from fastapi.testclient import TestClient

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--baseline", type=Path, required=True)
parser.add_argument("--repetitions", type=int, default=5)
parser.add_argument(
    "--output", type=Path, default=root / "artifacts/plot-transport-comparison.json"
)
args = parser.parse_args()
baseline_path = args.baseline.resolve()
assert baseline_path.is_relative_to(root) and args.output.resolve().is_relative_to(root)
assert args.repetitions >= 3
module_name = "cytoforge._plot_transport_baseline"
spec = importlib.util.spec_from_file_location(module_name, baseline_path)
baseline = importlib.util.module_from_spec(spec)
sys.modules[module_name] = baseline
spec.loader.exec_module(baseline)
directory = Path(tempfile.mkdtemp(prefix="plot-transport-comparison-", dir=root / ".tmp"))
factories = {"before": baseline.create_app, "after": create_app}
options = {"smooth": False, "axis_extent": "full", "contour_spacing": "10"}
mixed = np.array(
    [
        [0, 0, 0, 0, 1],
        [1, 2, 3, 1, 2],
        [2, 1, 2, 2, 3],
        [3, 3, 1, 3, 4],
        [0, 2, 4, np.nan, 5],
        [2, np.nan, 2, 5, 6],
        [np.nan, 1, 1, 6, 7],
        [np.inf, 2, 2, 7, 8],
        [-np.inf, 2, 2, 8, 9],
    ]
)
cases = []


def case(name, values, mode, bins=160, bounds=None, **extra):
    one_dimension = mode in {"histogram", "cdf"} or extra.pop("one_dimension", False)
    params = {
        "x": "X",
        "mode": mode,
        "bins": bins,
        "bounds": json.dumps(
            bounds or ([-1, 5] * (1 if one_dimension else 3 if mode == "3d" else 2))
        ),
        "graph_options": json.dumps({**options, **extra.pop("graph_options", {})}),
    }
    if not one_dimension:
        params["y"] = "Y"
    if mode == "3d":
        params["three_d"] = json.dumps(
            {"z": "Z", "color_by": "C", "size_by": "S", "pan": [0.1, -0.2]}
        )
    sample = Sample(
        name=name,
        channels=[Channel(name=name) for name in ["X", "Y", "Z", "C", "S"][: values.shape[1]]],
        event_count=len(values),
    )
    gate = Gate(
        sample_id=sample.id,
        name="λ <review> Ω 🧪",
        kind="rectangle",
        x="X",
        y="Y",
        bounds=[0, 2, 0, 3],
    )
    doc = Workspace(name=name, samples=[sample], gates=[gate])
    params["backgate_id"] = gate.id
    cases.append(
        {
            "name": name,
            "values": values,
            "doc": doc,
            "params": params,
            "one_dimension": one_dimension,
            **extra,
        }
    )


for mode in ["density", "scatter", "histogram", "cdf", "contour", "zebra", "pseudocolor", "3d"]:
    case("Mixed finite/nonfinite " + mode, mixed, mode, bins=32)
case("Legacy density without Y", mixed, "density", bins=32, one_dimension=True)
case("Empty 384-bin contour", np.empty((0, 5)), "contour", bins=384)
x, y = np.meshgrid(np.arange(5.0), np.arange(5.0))
case(
    "25 sparse tied bins",
    np.column_stack([x.ravel(), y.ravel()]),
    "contour",
    bounds=[-1, 11, -1, 11],
)
for bins in [256, 384]:
    # Keep the acquired field inside the explicit view's padded automatic
    # domain. At 384 bins these are exactly the native stress fixture's cells;
    # 256 bins independently exercises its coarser quantization.
    rows, columns = np.indices((256, 256))
    occupied = (rows + columns) % 2 == 0
    case(
        "32,768 checkerboard events in a bounded view",
        np.column_stack([(columns[occupied] + 64.5) / 384, (rows[occupied] + 64.5) / 384]),
        "contour",
        bins=bins,
        bounds=[0, 1, 0, 1],
        **({"expected_vertices": 150000, "expected_truncated": True} if bins == 384 else {}),
    )
rng = np.random.default_rng(2401)
values = rng.normal(size=(1_000_000, 2))
values[750000:] += [4, 2]
for bins in [160, 384]:
    case("Million-event bimodal contour", values, "contour", bins=bins, bounds=[-7, 12, -7, 12])
case(
    "100,000 scatter markers from 200,000 events",
    rng.uniform(-4, 4, (200_000, 2)),
    "scatter",
    bounds=[-5, 5, -5, 5],
    graph_options={"point_limit": 100000},
    expected_displayed_count=100000,
)
results = []
with ExitStack() as stack:
    clients = {}
    snapshots = {}
    for key, factory in factories.items():
        app = factory(directory / key)
        client = stack.enter_context(TestClient(app, base_url="http://127.0.0.1"))
        client.headers["X-CytoForge-Token"] = client.get("/api/bootstrap").json()["token"]
        clients[key] = client
        for item in cases:
            doc = item["doc"].model_copy(deep=True)
            sample = doc.samples[0]
            sample.sha256 = save_events(
                app.state.store.data_path(doc.id, sample.id), item["values"]
            )
            app.state.store.create(doc)
            snapshots[key, doc.id] = (
                app.state.store.get(doc.id).model_dump_json(),
                app.state.store.history(doc.id),
            )
    for item in cases:
        doc, params = item["doc"], item["params"]
        route = f"/api/workspaces/{doc.id}/samples/{doc.samples[0].id}/plot"
        # Both engines have independent stores/caches with the same source bytes
        # and identifiers. Warm once, then alternate full request order.
        for client in clients.values():
            response = client.get(route, params=params)
            assert response.status_code == 200, response.text
        times = {key: [] for key in clients}
        expected_hash = None
        for repetition in range(args.repetitions):
            order = ["before", "after"] if repetition % 2 == 0 else ["after", "before"]
            for key in order:
                start = time.perf_counter()
                response = clients[key].get(route, params=params)
                times[key].append(time.perf_counter() - start)
                assert response.status_code == 200, (item["name"], response.text)
                assert response.headers["content-type"] == "application/json"
                assert response.headers["cache-control"] == "no-store"
                assert int(response.headers["content-length"]) == len(response.content)
                response_hash = hashlib.sha256(response.content).hexdigest()
                if expected_hash is None:
                    expected_hash = response_hash
                assert response_hash == expected_hash, (
                    item["name"],
                    key,
                    "Complete HTTP response changed",
                )
                data = response.json()
                assert data["count"] == len(item["values"])
                for field in ["vertices", "truncated", "displayed_count"]:
                    if "expected_" + field in item:
                        actual_field = {
                            "vertices": "contour_vertices",
                            "truncated": "contours_truncated",
                        }.get(field, field)
                        assert data[actual_field] == item["expected_" + field]
                length = len(response.content)
                del data, response
        before, after = (float(np.median(times[key])) for key in ["before", "after"])
        results.append(
            {
                "scenario": item["name"],
                "events": len(item["values"]),
                "mode": params["mode"],
                "bins": params["bins"],
                "repetitions": args.repetitions,
                "before_median_seconds": before,
                "after_median_seconds": after,
                "speedup": before / after,
                "times_seconds": times,
                "response_bytes": length,
                "complete_response_sha256": expected_hash,
                "complete_response_bytes_unchanged": True,
            }
        )
    for key, client in clients.items():
        for item in cases:
            identifier = item["doc"].id
            store = client.app.state.store
            assert (
                store.get(identifier).model_dump_json(),
                store.history(identifier),
            ) == snapshots[key, identifier]
evidence = {
    "status": "passed",
    "results": results,
    "baseline_app_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
    "current_app_sha256": hashlib.sha256(
        (root / "backend/cytoforge/app.py").read_bytes()
    ).hexdigest(),
    "scientific_graph_source_sha256": hashlib.sha256(
        (root / "backend/cytoforge/graph_views.py").read_bytes()
    ).hexdigest(),
    "fastapi_version": fastapi.__version__,
    "directory": str(directory),
    "scientific_snapshots_and_history_unchanged": True,
    "scope": "Interleaved warmed authenticated ASGI requests per implementation/scenario with "
    "independent stores and engine caches. Timings include source loading, full scientific "
    "calculation, framework/middleware, strict JSON encoding and complete response consumption "
    "in TestClient; exclude real sockets, native rendering and test-driver transfer. Full raw "
    "response hashes and parsing are checked outside timing. Synthetic data on this host only.",
}
args.output.write_text(json.dumps(evidence, indent=2) + "\n")
print(json.dumps(evidence))
