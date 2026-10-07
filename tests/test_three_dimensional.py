"""Independent event identities, finite masks, coordinate bases and stream guards."""

import hashlib
import io
import json
import zipfile

import numpy as np
import pytest
from cytoforge.models import (
    Channel,
    Compensation,
    Gate,
    GateDimension,
    LayoutDefinition,
    PlotDefinition,
    PlotLayer,
    ReportBatch,
    ReportElement,
    Sample,
    ThreeDView,
    Transform,
    Workspace,
)
from cytoforge.report_plots import figure, source_payloads
from cytoforge.science import Engine, save_events
from cytoforge.three_dimensional import (
    CHUNK_EVENTS,
    POINT_DTYPE,
    point_chunk,
    prepare,
    project_positions,
)
from pydantic import ValidationError


def test_3d_reports_preserve_camera_event_ids_and_project_roundtrip(client):
    store = client.app.state.store
    doc, engine = dataset(
        store,
        [
            [0, 0, 0, 0, 0],
            [1, 1, 1, 10, 100],
            [2, 2, 2, np.nan, np.nan],
            [3, 3, 3, 30, 300],
            [np.nan, 0, 0, 0, 0],
        ],
    )
    sample = doc.samples[0]
    view = ThreeDView(
        z="Z", color_by="C", size_by="S", yaw=0.7, pitch=-0.4, zoom=1.3, pan=(0.1, -0.2)
    )
    plot = PlotDefinition(
        sample_id=sample.id,
        x="X",
        y="Y",
        mode="3d",
        three_d=view,
        bounds=[0, 2, 0, 2, 0, 2],
        graph_options={"palette": "viridis"},
    )
    layout = LayoutDefinition(
        name="3D vector publication",
        batch=ReportBatch(mode="off"),
        elements=[ReportElement(kind="plot", plot=plot, width_mm=150, height_mm=110)],
    )
    base = f"/api/workspaces/{doc.id}"
    saved = client.post(
        base + "/layouts/save", json={"revision": doc.revision, "definition": layout.model_dump()}
    )
    assert saved.status_code == 200, saved.text
    doc = store.get(doc.id)
    body = {"revision": doc.revision, "definition": layout.model_dump(), "validate_sources": True}
    plan = client.post(base + "/reports/plan", json=body)
    assert plan.status_code == 200 and plan.json()["exportable"], plan.text
    rendered = client.post(base + "/reports/render", json=body)
    assert rendered.status_code == 200 and rendered.json()["exportable"], rendered.text
    response = rendered.json()
    assert "<image" not in response["svg"] and "<path" in response["svg"]
    panel = response["manifest"]["elements"][0]
    layer = panel["layers"][0]
    assert panel["mode"] == "3d" and panel["bounds"] == [0, 2, 0, 2, 0, 2]
    assert layer["population_count"] == 5 and layer["finite_count"] == 4
    assert layer["visible_count"] == layer["displayed_count"] == 3 and layer["outside_view"] == 1
    assert (
        layer["event_ids_sha256"]
        == hashlib.sha256(np.array([0, 1, 2], "<u8").tobytes()).hexdigest()
    )
    assert layer["three_d"]["yaw"] == 0.7 and layer["three_d"]["pan"] == [0.1, -0.2]
    assert set(layer["source"]["parameters"]) == {"X", "Y", "Z", "C", "S"}
    assert layer["sample_sha256"] == sample.sha256 and layer["sampling"] is None
    exported = client.post(
        base + "/reports/export",
        json={**body, "review_hash": plan.json()["review_hash"], "format": "zip"},
    )
    assert exported.status_code == 200, exported.text
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert json.loads(archive.read("manifest.json"))["pages"][0]["elements"][0] == panel
        assert "<image" not in archive.read("page-0001.svg").decode()
    archive = client.get(base + "/export/project")
    restored = client.post(
        "/api/import/project",
        files={"file": ("cloud.cytoforge", archive.content, "application/zip")},
    )
    assert restored.status_code == 200, restored.text
    copy = restored.json()
    assert copy["layouts"][0]["elements"][0]["plot"]["three_d"] == view.model_dump(mode="json")
    rerender = client.post(
        f"/api/workspaces/{copy['id']}/reports/render",
        json={
            "revision": copy["revision"],
            "definition": copy["layouts"][0],
            "validate_sources": True,
        },
    )
    assert rerender.status_code == 200 and rerender.json()["exportable"], rerender.text
    assert (
        rerender.json()["manifest"]["elements"][0]["layers"][0]["event_ids_sha256"]
        == layer["event_ids_sha256"]
    )
    missing = plot.model_copy(update={"three_d": view.model_copy(update={"z": "Unavailable"})})
    bad_layout = layout.model_copy(
        update={"elements": [layout.elements[0].model_copy(update={"plot": missing})]}
    )
    review = client.post(
        base + "/reports/plan",
        json={"revision": doc.revision, "definition": bad_layout.model_dump()},
    )
    assert review.status_code == 200 and not review.json()["exportable"]


def test_report_overlay_uses_shared_xyz_and_scalar_transforms_and_ranges(store):
    doc, engine = dataset(store, [[0, 0, 0, 0, 0], [1, 1, 1, 1, 1]])
    other = Sample(
        name="Other coordinate display",
        event_count=2,
        channels=[
            Channel(name=n, transform=Transform(kind="asinh", cofactor=1))
            for n in ["X", "Y", "Z", "C", "S"]
        ],
    )
    other.sha256 = save_events(
        store.data_path(doc.id, other.id), np.array([[2, 2, 2, 2, 2], [4, 4, 4, 4, 4]], float)
    )
    doc.samples.append(other)
    definition = PlotDefinition(
        sample_id=doc.samples[0].id,
        x="X",
        y="Y",
        mode="3d",
        three_d={"z": "Z", "color_by": "C", "size_by": "S"},
        overlays=[PlotLayer(sample_id=other.id)],
    )
    layers = [
        dict(
            source_sample_id=s.id,
            sample_id=s.id,
            source_gate_id=None,
            gate_id=None,
            source_coordinate_gate_id=None,
            coordinate_gate_id=None,
            color="#087e8b",
            label="",
            locked_control=False,
        )
        for s in doc.samples
    ]
    payloads, bounds = source_payloads(doc, engine, definition, layers)
    assert len(bounds) == 6 and payloads[0]["bounds"] == payloads[1]["bounds"]
    assert all(p["three_d"]["z_transform"]["kind"] == "linear" for p in payloads)
    assert all(
        p["scalar_dimensions"][i]["transform"]["kind"] == "linear" for p in payloads for i in [0, 1]
    )
    assert all(p["color_bounds"] == p["size_bounds"] == [0, 4] for p in payloads)
    svg, manifest = figure(doc, engine, definition, layers, 150, 110)
    assert "<image" not in svg and len(manifest["layers"]) == 2
    assert [layer["displayed_count"] for layer in manifest["layers"]] == [2, 2]


def test_box_wholly_outside_view_does_not_create_a_false_cube_edge(store):
    doc, engine = dataset(store, [[0, 0, 0, 0, 0], [1, 1, 1, 1, 1]])
    gate = Gate(
        sample_id=doc.samples[0].id,
        name="Outside",
        kind="hyperrectangle",
        dimensions=[GateDimension(channel="Z", minimum=2, maximum=3)],
    )
    doc.gates.append(gate)
    prepared = prepare(
        doc, engine, doc.samples[0].id, "X", "Y", {"z": "Z"}, bounds=[0, 1, 0, 1, 0, 1]
    )
    assert prepared[0]["boxes"] == []


def dataset(store, values, names=("X", "Y", "Z", "C", "S")):
    values = np.asarray(values, float)
    sample = Sample(
        name="Independent 3D truth",
        channels=[Channel(name=n) for n in names],
        event_count=len(values),
    )
    doc = Workspace(name="3D truth", samples=[sample])
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    return store.create(doc), Engine(store)


def test_streamed_rows_preserve_literal_event_ids_finite_masks_and_scalar_missingness(store):
    values = [
        [0, 0, 0, 0, 0],
        [1, 1, 1, 10, 100],
        [2, 2, 2, np.nan, np.nan],
        [3, 3, 3, 30, 300],
        [np.nan, 0, 0, 0, 0],
        [0, 0, np.inf, 0, 0],
    ]
    doc, engine = dataset(store, values)
    prepared = prepare(
        doc,
        engine,
        doc.samples[0].id,
        "X",
        "Y",
        {"z": "Z", "color_by": "C", "size_by": "S"},
        bounds=[0, 2, 0, 2, 0, 2],
    )
    meta = prepared[0]
    assert (
        meta["count"] == 6
        and meta["finite_count"] == 4
        and meta["visible_count"] == meta["displayed_count"] == 3
    )
    rows = point_chunk(prepared)
    assert rows.dtype == POINT_DTYPE and rows.nbytes == 3 * 32
    assert rows["event_id"].tolist() == [0, 1, 2]
    np.testing.assert_allclose(rows["position"], [[0, 0, 0], [0.5, 0.5, 0.5], [1, 1, 1]])
    np.testing.assert_allclose(rows["color"], [0, 1 / 3, -1])
    np.testing.assert_allclose(rows["size"], [0, 1 / 3, 0.5])
    assert meta["color_finite_count"] == meta["size_finite_count"] == 3
    assert meta["sampling"] is None
    assert POINT_DTYPE.fields["event_id"][1] == 20 and POINT_DTYPE.fields["backgate"][1] == 28
    assert np.all(np.isfinite(rows["position"]))
    json.dumps(meta, allow_nan=False)


def test_box_gate_and_backgate_counts_use_all_source_events(store):
    values = np.array([[x, y, z, 0, 0] for x in [0, 1, 2] for y in [0, 1, 2] for z in [0, 1, 2]])
    doc, engine = dataset(store, values)
    gate = Gate(
        sample_id=doc.samples[0].id,
        name="Unit cube",
        kind="hyperrectangle",
        dimensions=[GateDimension(channel=name, minimum=0, maximum=2) for name in ["X", "Y", "Z"]],
    )
    doc.gates = [gate]
    prepared = prepare(
        doc,
        engine,
        doc.samples[0].id,
        "X",
        "Y",
        {"z": "Z"},
        gate_id=gate.id,
        backgate_id=gate.id,
        bounds=[0, 2, 0, 2, 0, 2],
    )
    assert prepared[0]["count"] == prepared[0]["backgate_count"] == 8
    expected = [i for i, (x, y, z, _, _) in enumerate(values) if x < 2 and y < 2 and z < 2]
    rows = point_chunk(prepared)
    assert rows["event_id"].tolist() == expected
    assert rows["backgate"].tolist() == [1] * 8
    assert prepared[0]["boxes"][0]["normalized"] == [0, 1, 0, 1, 0, 1]


def test_all_events_cross_chunk_boundary_and_sampling_is_explicit(store):
    n = CHUNK_EVENTS + 17
    values = np.column_stack([np.arange(n)] * 5)
    doc, engine = dataset(store, values)
    full = prepare(
        doc,
        engine,
        doc.samples[0].id,
        "X",
        "Y",
        {"z": "Z"},
        graph_options={"axis_extent": "full", "point_limit": 100},
    )
    assert full[0]["displayed_count"] == n and full[0]["sampling"] is None
    first = point_chunk(full)
    last = point_chunk(full, CHUNK_EVENTS)
    assert len(first) == CHUNK_EVENTS and len(last) == 17
    assert first["event_id"].tolist() == list(range(CHUNK_EVENTS))
    assert last["event_id"].tolist() == list(range(CHUNK_EVENTS, n))
    limited = prepare(
        doc,
        engine,
        doc.samples[0].id,
        "X",
        "Y",
        {"z": "Z", "all_events": False},
        graph_options={"axis_extent": "full", "point_limit": 100},
    )
    assert limited[0]["count"] == limited[0]["visible_count"] == n
    assert limited[0]["displayed_count"] == 100 and limited[0]["sampling"]
    again = prepare(
        doc,
        engine,
        doc.samples[0].id,
        "X",
        "Y",
        {"z": "Z", "all_events": False},
        graph_options={"axis_extent": "full", "point_limit": 100},
    )
    assert point_chunk(limited).tobytes() == point_chunk(again).tobytes()


def test_camera_changes_reuse_rows_but_source_geometry_changes_data_key(store):
    doc, engine = dataset(store, [[0, 0, 0, 0, 0], [1, 2, 3, 4, 5]])
    a = prepare(doc, engine, doc.samples[0].id, "X", "Y", {"z": "Z"})
    b = prepare(
        doc, engine, doc.samples[0].id, "X", "Y", {"z": "Z", "yaw": 1, "pitch": -0.3, "zoom": 2}
    )
    assert a[0]["data_key"] == b[0]["data_key"] and a[1] is b[1]
    assert point_chunk(a).tobytes() == point_chunk(b).tobytes()
    c = prepare(doc, engine, doc.samples[0].id, "X", "Y", {"z": "C"})
    assert c[0]["data_key"] != a[0]["data_key"]


@pytest.mark.parametrize(
    "values",
    [
        np.empty((0, 5)),
        np.full((2, 5), 7),
        np.full((2, 5), np.nan),
        np.array([[-np.finfo(float).max] * 5, [np.finfo(float).max] * 5]),
    ],
)
def test_empty_constant_and_extreme_finite_clouds(store, values):
    doc, engine = dataset(store, values)
    prepared = prepare(
        doc,
        engine,
        doc.samples[0].id,
        "X",
        "Y",
        {"z": "Z", "color_by": "C", "size_by": "S"},
        graph_options={"axis_extent": "full"},
    )
    json.dumps(prepared[0], allow_nan=False)
    rows = point_chunk(prepared)
    assert np.all(np.isfinite(rows["position"]))
    assert np.all((rows["position"] >= 0) & (rows["position"] <= 1))
    assert np.all(np.isfinite(rows["color"])) and np.all(np.isfinite(rows["size"]))


def test_fixed_compensation_ratio_and_uncompensated_axes_are_preserved(store):
    doc, engine = dataset(store, [[2, 1, 4, 0, 0], [4, 2, 6, 0, 0], [1, 0, 8, 0, 0]])
    sample = doc.samples[0]
    matrix = Compensation(name="Fixed basis", detectors=["X", "Y"], matrix=[[1, 0.5], [0, 1]])
    doc.compensations = [matrix]
    gate = Gate(
        sample_id=sample.id,
        name="Coordinate basis",
        kind="hyperrectangle",
        dimensions=[
            GateDimension(
                channel="Ratio", ratio_channels=("X", "Y"), compensation_ref="uncompensated"
            ),
            GateDimension(channel="Y", compensation_ref=matrix.id),
            GateDimension(channel="Z"),
        ],
    )
    doc.gates = [gate]
    prepared = prepare(
        doc,
        engine,
        sample.id,
        "Ratio",
        "Y",
        {"z": "Z"},
        coordinate_gate_id=gate.id,
        bounds=[0, 3, -1, 1, 0, 10],
    )
    rows = point_chunk(prepared)
    assert rows["event_id"].tolist() == [0, 1]
    np.testing.assert_allclose(rows["position"], [[2 / 3, 0.5, 0.4], [2 / 3, 0.5, 0.6]], rtol=1e-6)
    assert prepared[0]["axes"][1]["compensation_ref"] == matrix.id
    raw = prepare(
        doc,
        engine,
        sample.id,
        "X",
        "Y",
        {"z": "Z", "compensation": "uncompensated"},
        bounds=[0, 5, 0, 5, 0, 10],
    )
    np.testing.assert_allclose(point_chunk(raw)["position"][:, 1], [0.2, 0.4, 0])


def test_projection_has_literal_camera_rotation_pan_and_zoom():
    points = np.array([[0, 0.5, 1], [1, 0.5, 0]])
    view = ThreeDView(z="Z", yaw=np.pi / 2, pitch=0)
    actual = project_positions(points, view)
    np.testing.assert_allclose(actual[:, 0], [0.5, -0.5], atol=1e-12)
    np.testing.assert_allclose(actual[:, 1], [0, 0], atol=1e-12)
    moved = project_positions(
        points, view.model_copy(update={"pan": (0.5, 0), "zoom": 2}), aspect=2
    )
    np.testing.assert_allclose(moved[:, 0], [0.25, -0.75], atol=1e-12)


@pytest.mark.parametrize(
    "settings",
    [
        {"z": "Z", "yaw": 4},
        {"z": "Z", "pitch": float("nan")},
        {"z": "Z", "zoom": 0},
        {"z": "Z", "pan": [6, 0]},
        {"z": "Z", "color_bounds": [1, 1]},
        {"z": "Z", "point_size": 13},
        {"z": "Z", "unknown": True},
    ],
)
def test_invalid_camera_and_display_settings_are_rejected(settings):
    with pytest.raises(ValidationError):
        ThreeDView.model_validate(settings)


def test_binary_api_binds_revision_geometry_and_population_and_limits_chunks(client):
    doc, _ = dataset(client.app.state.store, [[0, 0, 0, 0, 0], [1, 1, 1, 1, 1]])
    base = f"/api/workspaces/{doc.id}/samples/{doc.samples[0].id}"
    args = {"x": "X", "y": "Y", "three_d": json.dumps({"z": "Z"}), "mode": "3d"}
    response = client.get(base + "/plot", params=args)
    assert response.status_code == 200, response.text
    metadata = response.json()
    args.pop("mode")
    args.update(revision=doc.revision, data_key=metadata["data_key"])
    binary = client.get(base + "/plot3d/points", params={**args, "count": 1})
    assert binary.status_code == 200 and len(binary.content) == 32
    assert binary.headers["x-cytoforge-3d-key"] == metadata["data_key"]
    assert np.frombuffer(binary.content, dtype=POINT_DTYPE)["event_id"][0] == 0
    assert (
        client.get(base + "/plot3d/points", params={**args, "data_key": "0" * 64}).status_code
        == 409
    )
    assert (
        client.get(
            base + "/plot3d/points", params={**args, "revision": doc.revision + 1}
        ).status_code
        == 409
    )
    assert (
        client.get(
            base + "/plot3d/points", params={**args, "three_d": json.dumps({"z": "S"})}
        ).status_code
        == 409
    )
    assert client.get(base + "/plot3d/points", params={**args, "count": 65537}).status_code == 422
    assert client.get(base + "/plot3d/points", params={**args, "start": -1}).status_code == 422
    assert len(client.get(base + "/plot3d/points", params={**args, "start": 2}).content) == 0
    token = client.headers.pop("X-CytoForge-Token")
    assert client.get(base + "/plot3d/points", params=args).status_code == 401
    client.headers["X-CytoForge-Token"] = token
