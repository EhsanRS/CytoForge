"""Magnetic population following: independent acquisition labels and exports."""

import io
import json
import zipfile

import numpy as np
import pytest
from cytoforge.gatingml import export_gatingml
from cytoforge.interchange import parse_document
from cytoforge.models import (
    Channel,
    Compensation,
    Gate,
    GateDimension,
    MagneticGate,
    Sample,
    Transform,
    Workspace,
)
from cytoforge.plotting import plot_payload
from cytoforge.science import Engine, save_events
from pydantic import ValidationError


def geometric(sample, kind="rectangle", **changes):
    specification = dict(
        sample_id=sample.id,
        name="Following λ",
        kind=kind,
        x="X",
        y="Y",
        bounds=[-0.5, 0.5, -0.5, 0.5],
        magnetic={"max_shift": 2.5},
    )
    if kind == "range":
        specification.update(y=None, bounds=[-0.5, 0.5])
    elif kind == "polygon":
        specification.update(vertices=[[-0.5, -0.5], [0.5, -0.5], [0, 0.5]])
    elif kind == "ellipse":
        specification.update(center=[0, 0], radii=[0.5, 0.35], angle=0.4)
    elif kind in {"hyperrectangle", "ellipsoid"}:
        specification.update(
            x=None,
            y=None,
            bounds=[],
            dimensions=[
                GateDimension(channel=name, minimum=-0.5, maximum=0.5) for name in ["X", "Y"]
            ],
        )
        if kind == "ellipsoid":
            specification.update(coordinates=[0, 0], covariance=[[0.25, 0.07], [0.07, 0.17]])
    specification.update(changes)
    return Gate(**specification)


def acquisition(store, values=None, kind="rectangle", **changes):
    if values is None:
        rng = np.random.default_rng(40)
        values = np.vstack(
            [
                rng.normal([1.2, 1.1], 0.01, (257, 2)),
                rng.normal([7, 7], 0.01, (2048, 2)),
                [np.nan, 1],
                [np.inf, 1],
            ]
        )
    sample = Sample(
        name="Shifted acquisition",
        channels=[Channel(name=n) for n in ["X", "Y"]],
        event_count=len(values),
    )
    gate = geometric(sample, kind, **changes)
    workspace = Workspace(name="Magnetic truth", samples=[sample], gates=[gate])
    sample.sha256 = save_events(store.data_path(workspace.id, sample.id), values)
    doc = store.get(store.create(workspace).id)
    return doc, doc.samples[0], doc.gates[0], values


@pytest.mark.parametrize(
    "kind", ["range", "rectangle", "polygon", "ellipse", "hyperrectangle", "ellipsoid"]
)
def test_follows_known_population_with_full_event_membership_and_preserved_shape(store, kind):
    doc, sample, gate, values = acquisition(store, kind=kind)
    engine = Engine(store)
    before, history = store.get(doc.id).model_dump_json(), store.history(doc.id)
    moved, result = engine.resolve_gate(doc, sample, gate)
    expected = np.arange(len(values)) < 257  # independently labelled acquisition populations
    np.testing.assert_array_equal(engine.mask(doc, sample, gate.id), expected)
    assert result["anchor_count"] == 0
    assert result["resolved_count"] == result["population_count"] == 257
    assert result["finite_parent_count"] == len(values) - 2
    assert 0 < result["distance"] <= gate.magnetic.max_shift
    assert moved.magnetic is None
    if kind == "range":
        assert moved.bounds[1] - moved.bounds[0] == 1
    elif kind == "rectangle":
        np.testing.assert_array_equal(np.diff(np.asarray(moved.bounds).reshape(2, 2)), [[1], [1]])
    elif kind == "polygon":
        np.testing.assert_allclose(np.diff(moved.vertices, axis=0), np.diff(gate.vertices, axis=0))
    elif kind == "ellipse":
        assert moved.radii == gate.radii and moved.angle == gate.angle
    elif kind == "ellipsoid":
        assert moved.covariance == gate.covariance and moved.distance_square == gate.distance_square
    else:
        assert all(d.maximum - d.minimum == 1 for d in moved.dimensions)
    assert store.get(doc.id).model_dump_json() == before and store.history(doc.id) == history


def test_nearest_local_population_wins_over_larger_more_distant_population(store):
    rng = np.random.default_rng(88)
    values = np.vstack(
        [rng.normal([1.1, 0], 0.01, (129, 2)), rng.normal([2.9, 0], 0.01, (10000, 2))]
    )
    doc, sample, gate, _ = acquisition(store, values, "range", magnetic={"max_shift": 3})
    engine = Engine(store)
    mask = engine.mask(doc, sample, gate.id)
    np.testing.assert_array_equal(mask, np.arange(len(values)) < 129)
    assert engine.resolve_gate(doc, sample, gate)[1]["resolved_count"] == 129


@pytest.mark.parametrize(
    "values,status",
    [
        (np.empty((0, 2)), "no-finite-parent-events"),
        (np.array([[np.nan, np.nan], [np.inf, 1]]), "no-finite-parent-events"),
        (np.array([[100.0, 100.0]]), "no-nearby-events"),
        (np.zeros((12, 2)), "resolved"),
    ],
)
def test_empty_nonfinite_distant_and_already_enclosed_populations_keep_anchor(
    store, values, status
):
    doc, sample, gate, _ = acquisition(store, values)
    engine = Engine(store)
    moved, result = engine.resolve_gate(doc, sample, gate)
    assert moved.bounds == gate.bounds and result["distance"] == 0 and result["status"] == status
    assert engine.mask(doc, sample, gate.id).sum() == result["resolved_count"]


def test_complement_boolean_child_and_statistics_use_resolved_full_parent(store):
    doc, sample, gate, values = acquisition(store, complement=True)
    engine = Engine(store)
    expected = np.arange(len(values)) >= 257
    np.testing.assert_array_equal(engine.mask(doc, sample, gate.id), expected)
    assert engine.resolve_gate(doc, sample, gate)[1]["population_count"] == len(values) - 257
    boolean = Gate(
        sample_id=sample.id, name="Outside following", kind="boolean", operands=[gate.id]
    )
    child = Gate(
        sample_id=sample.id,
        name="Far subset",
        parent_id=gate.id,
        kind="range",
        x="X",
        bounds=[6, 8],
    )
    doc = store.mutate(
        doc.id, "Add dependents", lambda d: d.gates.extend([boolean, child]), doc.revision
    )
    np.testing.assert_array_equal(engine.mask(doc, sample, boolean.id), expected)
    np.testing.assert_array_equal(
        engine.mask(doc, sample, child.id),
        (np.arange(len(values)) >= 257) & (np.arange(len(values)) < 2305),
    )
    assert engine.summary(doc, sample, child.id, "X")["count"] == 2048


def test_parent_edits_and_history_recalculate_position_without_changing_anchor(client):
    store, engine = client.app.state.store, client.app.state.engine
    rng = np.random.default_rng(4)
    values = np.vstack(
        [rng.normal([1.2, 1.2], 0.01, (129, 2)), rng.normal([2.9, 2.9], 0.01, (256, 2))]
    )
    doc, sample, gate, _ = acquisition(store, values, magnetic={"max_shift": 4})
    parent = Gate(sample_id=sample.id, name="Reviewed parent", kind="range", x="Y", bounds=[0, 2])

    def add(d):
        d.gates.append(parent)
        d.gates[0].parent_id = parent.id

    doc = store.mutate(doc.id, "Parent", add, doc.revision)
    before = engine.resolve_gate(doc, sample, doc.gates[0])[1]
    np.testing.assert_array_equal(engine.mask(doc, sample, gate.id), np.arange(len(values)) < 129)
    response = client.put(
        f"/api/workspaces/{doc.id}/gates/{parent.id}",
        json={
            "revision": doc.revision,
            "gate": parent.model_copy(update={"bounds": [2, 4]}).model_dump(),
        },
    )
    assert response.status_code == 200
    doc = Workspace.model_validate(response.json())
    after = engine.resolve_gate(doc, sample, doc.gates[0])[1]
    assert after["shift"] != before["shift"] and after["population_count"] == 256
    np.testing.assert_array_equal(engine.mask(doc, sample, gate.id), np.arange(len(values)) >= 129)
    for action, count in [("undo", 129), ("redo", 256)]:
        response = client.post(
            f"/api/workspaces/{doc.id}/{action}", json={"revision": doc.revision}
        )
        assert response.status_code == 200
        doc = Workspace.model_validate(response.json())
        assert engine.mask(doc, sample, gate.id).sum() == count
        assert doc.gates[0].bounds == [-0.5, 0.5, -0.5, 0.5]


def test_read_only_draft_preview_freeze_stale_graph_validation_and_legacy_serialization(client):
    store, engine = client.app.state.store, client.app.state.engine
    doc, sample, gate, _ = acquisition(store, magnetic=None)
    assert "magnetic" not in gate.model_dump() and "magnetic" not in json.loads(
        gate.model_dump_json()
    )
    before, history = doc.model_dump_json(), store.history(doc.id)
    draft = gate.model_copy(update={"magnetic": MagneticGate(max_shift=2.5)})
    endpoint = f"/api/workspaces/{doc.id}/gates/preview-magnetic"
    response = client.post(endpoint, json={"revision": doc.revision, "gate": draft.model_dump()})
    assert response.status_code == 200
    result = response.json()
    assert result["magnetic"]["resolved_count"] == 257 and "magnetic" not in result["gate"]
    frozen = Gate.model_validate(result["gate"])
    assert frozen.bounds != gate.bounds
    assert store.get(doc.id).model_dump_json() == before and store.history(doc.id) == history
    assert engine.mask(doc, sample, gate.id).sum() == 0
    assert (
        client.post(
            endpoint, json={"revision": doc.revision + 1, "gate": draft.model_dump()}
        ).status_code
        == 409
    )
    bad = draft.model_copy(update={"parent_id": draft.id})
    assert client.post(
        endpoint, json={"revision": doc.revision, "gate": bad.model_dump()}
    ).status_code in {400, 422}
    missing = draft.model_copy(update={"x": "Unavailable"})
    assert client.post(
        endpoint, json={"revision": doc.revision, "gate": missing.model_dump()}
    ).status_code in {400, 422}
    saved = client.put(
        f"/api/workspaces/{doc.id}/gates/{gate.id}",
        json={"revision": doc.revision, "gate": frozen.model_dump()},
    )
    assert saved.status_code == 200
    current = Workspace.model_validate(saved.json())
    assert engine.mask(current, sample, gate.id).sum() == 257
    assert "magnetic" not in saved.json()["gates"][0]


@pytest.mark.parametrize(
    "kind", ["range", "rectangle", "polygon", "ellipse", "hyperrectangle", "ellipsoid"]
)
def test_gatingml_snapshot_and_portable_archive_preserve_exact_membership(client, kind):
    store = client.app.state.store
    doc, sample, gate, values = acquisition(store, kind=kind)
    before = doc.model_dump_json()
    with pytest.raises(ValueError, match="event engine"):
        export_gatingml(doc, sample)
    response = client.get(f"/api/workspaces/{doc.id}/samples/{sample.id}/export/gatingml")
    assert response.status_code == 200 and b"static-snapshot" in response.content
    plan = parse_document(response.content, "following.xml")
    assert any(i.code == "magnetic-snapshot" for i in plan.issues)
    assert all(g.magnetic is None for g in plan.sources[0].gates)
    imported = Workspace(
        name="Snapshot",
        samples=[sample],
        gates=[g.model_copy(update={"sample_id": sample.id}) for g in plan.sources[0].gates],
    )
    save_events(store.data_path(imported.id, sample.id), values)
    expected = np.arange(len(values)) < 257
    np.testing.assert_array_equal(
        Engine(store).mask(imported, sample, imported.gates[0].id), expected
    )
    archive = client.get(f"/api/workspaces/{doc.id}/export/project")
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as project:
        json_names = [n for n in project.namelist() if n.endswith(".json")]
        assert any(b'"magnetic"' in project.read(n) for n in json_names)
    restored = client.post(
        "/api/import/project", files={"file": ("following.cytoforge", archive.content)}
    )
    assert restored.status_code == 200
    restored_doc = Workspace.model_validate(restored.json())
    restored_sample = restored_doc.samples[0]
    assert restored_doc.gates[0].magnetic.max_shift == 2.5
    np.testing.assert_array_equal(
        Engine(store).mask(restored_doc, restored_sample, restored_doc.gates[0].id), expected
    )
    assert store.get(doc.id).model_dump_json() == before


@pytest.mark.parametrize(
    "options",
    [
        {"max_shift": 0},
        {"max_shift": -1},
        {"max_shift": 4.1},
        {"max_shift": float("nan")},
        {"max_shift": float("inf")},
        {"max_shift": 2, "unrecognized": True},
    ],
)
def test_invalid_search_settings_are_rejected(options):
    with pytest.raises(ValidationError):
        MagneticGate.model_validate(options)


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "quadrant", "bounds": [0, 0]},
        {"kind": "boolean", "operands": ["a" * 32]},
        {"kind": "container"},
        {"kind": "range", "bounds": [-1, 1], "y": "Y"},
        {"kind": "hyperrectangle", "dimensions": [GateDimension(channel="X")]},
        {
            "kind": "hyperrectangle",
            "dimensions": [GateDimension(channel=n, minimum=0, maximum=1) for n in ["X", "Y", "Z"]],
        },
        {
            "kind": "ellipsoid",
            "dimensions": [GateDimension(channel=n) for n in ["X", "Y"]],
            "coordinates": [0, 0],
            "covariance": [[1, 0.1], [0, 1]],
        },
        {
            "kind": "ellipsoid",
            "dimensions": [GateDimension(channel=n) for n in ["X", "Y"]],
            "coordinates": [0, 0],
            "covariance": [[-1, 0], [0, 1]],
        },
        {"bounds": [-1e308, 1e308, -1, 1]},
    ],
)
def test_unsupported_or_unbounded_magnetic_shapes_are_explicitly_rejected(changes):
    sample = Sample(
        name="Validation", event_count=0, channels=[Channel(name="X"), Channel(name="Y")]
    )
    with pytest.raises(ValidationError, match="Magnetic"):
        geometric(sample, **changes)


@pytest.mark.parametrize("mode", ["scatter", "contour", "zebra", "pseudocolor"])
def test_overlays_use_resolved_geometry_and_display_settings_never_change_membership(store, mode):
    doc, sample, gate, values = acquisition(store)
    engine = Engine(store)
    expected = np.arange(len(values)) < 257
    moved, report = engine.resolve_gate(doc, sample, gate)
    payload = plot_payload(
        doc,
        engine,
        sample.id,
        "Y",
        "X",
        bins=25,
        bounds=[0, 0.01, 0, 0.01],
        mode=mode,
        graph_options={"smooth": False},
    )
    overlay = payload["overlays"][0]
    assert overlay["magnetic"]["resolved_count"] == 257
    assert overlay["magnetic"]["arrow"]["axes"] == [1, 0]
    assert overlay["magnetic"]["arrow"]["to"] == report["position"]
    vertices = np.asarray(overlay["vertices"])
    np.testing.assert_allclose(vertices.min(axis=0), [moved.bounds[2], moved.bounds[0]])
    assert payload["visible_count"] == 0
    np.testing.assert_array_equal(engine.mask(doc, sample, gate.id), expected)
    # Callers cannot corrupt cached positions by mutating a returned record.
    report["shift"][0] = 1e9
    assert engine.resolve_gate(doc, sample, gate)[1]["shift"][0] != 1e9


def test_full_acquisition_resolution_and_parent_masks_ignore_marker_caps(store):
    values = np.vstack([np.tile([1.2, 1.1], (100_003, 1)), np.tile([7, 7], (100_001, 1))])
    doc, sample, gate, _ = acquisition(store, values)
    engine = Engine(store)
    expected = np.arange(len(values)) < 100_003
    np.testing.assert_array_equal(engine.mask(doc, sample, gate.id), expected)
    report = engine.resolve_gate(doc, sample, gate)[1]
    assert report["finite_parent_count"] == 200_004 and report["resolved_count"] == 100_003


def test_copy_to_another_sample_recalculates_from_anchor_and_preserves_independent_settings(client):
    store, engine = client.app.state.store, client.app.state.engine
    doc, sample, gate, values = acquisition(store)
    shifted = values.copy()
    shifted[:257] -= [2.4, 2.2]
    target = Sample(
        name="Different population position", channels=sample.channels, event_count=len(values)
    )
    target.sha256 = save_events(store.data_path(doc.id, target.id), shifted)
    doc = store.mutate(
        doc.id, "Target acquisition", lambda d: d.samples.append(target), doc.revision
    )
    response = client.post(
        f"/api/workspaces/{doc.id}/gates/apply",
        json={
            "revision": doc.revision,
            "source_sample_id": sample.id,
            "target_sample_ids": [target.id],
            "replace": False,
        },
    )
    assert response.status_code == 200
    doc = Workspace.model_validate(response.json())
    copied = next(g for g in doc.gates if g.sample_id == target.id)
    assert (
        copied.bounds == gate.bounds and copied.id != gate.id and copied.magnetic == gate.magnetic
    )
    for s, g in [(sample, gate), (target, copied)]:
        np.testing.assert_array_equal(engine.mask(doc, s, g.id), np.arange(len(values)) < 257)
    assert engine.resolve_gate(doc, sample, gate)[1]["shift"][0] > 0
    assert engine.resolve_gate(doc, target, copied)[1]["shift"][0] < 0
    # A new engine reads the same position from stored source events and settings.
    assert (
        Engine(store).resolve_gate(doc, target, copied)[1]
        == engine.resolve_gate(doc, target, copied)[1]
    )


def test_transformed_ratio_dimension_and_zero_denominators_use_full_event_coordinates(store):
    desired = np.r_[np.full(257, 1.2), np.full(2048, 7), np.nan]
    denominator = np.linspace(1, 2, len(desired))
    numerator = np.sinh(desired) * denominator / 2
    values = np.column_stack([numerator, denominator])
    values[-1] = [1, 0]
    dimension = GateDimension(
        channel="X",
        ratio_channels=["X", "Y"],
        ratio_a=2,
        transform=Transform(kind="asinh", cofactor=1),
        minimum=-0.5,
        maximum=0.5,
    )
    doc, sample, gate, _ = acquisition(store, values, "hyperrectangle", dimensions=[dimension])
    engine = Engine(store)
    np.testing.assert_array_equal(engine.mask(doc, sample, gate.id), np.arange(len(values)) < 257)
    assert engine.resolve_gate(doc, sample, gate)[1]["finite_parent_count"] == len(values) - 1
    histogram = plot_payload(
        doc, engine, sample.id, "X", coordinate_gate_id=gate.id, mode="histogram"
    )
    assert histogram["overlays"][0]["magnetic"]["resolved_count"] == 257


def test_compensation_changes_recompute_magnetic_position(store):
    rng = np.random.default_rng(3)
    true = np.vstack([rng.normal([1.2, 1.1], 0.01, (257, 2)), np.full((30, 2), 7)])
    matrix = Compensation(
        name="Scientific compensation", detectors=["X", "Y"], matrix=[[1, 0.2], [0.1, 1]]
    )
    measured = true @ np.asarray(matrix.matrix)
    doc, sample, gate, _ = acquisition(store, measured)

    def assign(d):
        d.compensations.append(matrix)
        d.samples[0].compensation_id = matrix.id

    doc = store.mutate(doc.id, "Assign compensation", assign, doc.revision)
    engine = Engine(store)
    before = engine.resolve_gate(doc, doc.samples[0], doc.gates[0])[1]
    np.testing.assert_array_equal(
        engine.mask(doc, doc.samples[0], gate.id), np.arange(len(true)) < 257
    )

    def change(d):
        d.compensations[0].matrix = (np.asarray(matrix.matrix) * 2).tolist()

    doc = store.mutate(doc.id, "Updated matrix", change, doc.revision)
    after = engine.resolve_gate(doc, doc.samples[0], doc.gates[0])[1]
    assert before["shift"] != after["shift"]
    np.testing.assert_array_equal(
        engine.mask(doc, doc.samples[0], gate.id), np.arange(len(true)) < 257
    )


def test_large_polygon_preserves_every_vertex_and_all_event_membership(store):
    theta = np.linspace(0, 2 * np.pi, 2000, endpoint=False)
    vertices = np.column_stack([0.5 * np.cos(theta), 0.5 * np.sin(theta)]).tolist()
    doc, sample, gate, values = acquisition(store, kind="polygon", vertices=vertices)
    engine = Engine(store)
    moved, report = engine.resolve_gate(doc, sample, gate)
    assert len(moved.vertices) == 2000 and report["resolved_count"] == 257
    np.testing.assert_allclose(
        np.diff(moved.vertices, axis=0), np.diff(vertices, axis=0), atol=1e-14
    )
    np.testing.assert_array_equal(engine.mask(doc, sample, gate.id), np.arange(len(values)) < 257)


@pytest.mark.parametrize("radius", [0.0001, 0.03125, 0.15, 1.5])
def test_search_radius_is_enforced_and_small_subgrid_movements_are_supported(store, radius):
    values = np.tile([0.50001, 0], (31, 1))
    doc, sample, gate, _ = acquisition(store, values, "range", magnetic={"max_shift": radius})
    moved, report = Engine(store).resolve_gate(doc, sample, gate)
    assert 0 < report["distance"] <= radius + 1e-12
    assert moved.bounds[1] > 0.50001 and report["resolved_count"] == 31


def test_shuffled_event_order_and_tied_populations_produce_identical_resolutions(store):
    rng = np.random.default_rng(12)
    values = np.vstack(
        [rng.normal([1.1, 0], 0.01, (129, 2)), rng.normal([-1.1, 0], 0.01, (129, 2))]
    )
    doc, sample, gate, _ = acquisition(store, values, "range")
    first = Engine(store).resolve_gate(doc, sample, gate)[1]
    other, sample2, gate2, _ = acquisition(store, rng.permutation(values), "range")
    second = Engine(store).resolve_gate(other, sample2, gate2)[1]
    assert first == second and first["resolved_count"] == 129


def test_magnetic_csv_and_fcs_exports_contain_only_the_followed_population(client):
    import csv

    import flowio

    store = client.app.state.store
    doc, sample, gate, values = acquisition(store)
    prefix = f"/api/workspaces/{doc.id}/samples/{sample.id}/export?gate_id={gate.id}"
    csv_response = client.get(prefix + "&format=csv")
    assert csv_response.status_code == 200
    rows = list(csv.reader(io.StringIO(csv_response.text)))
    assert len(rows) == 258
    np.testing.assert_allclose(np.asarray(rows[1:], dtype=float), values[:257])
    fcs_response = client.get(prefix + "&format=fcs")
    assert fcs_response.status_code == 200
    data = flowio.FlowData(io.BytesIO(fcs_response.content))
    assert data.event_count == 257
    np.testing.assert_allclose(np.asarray(data.events).reshape(257, 2), values[:257], rtol=1e-6)


def test_preview_requires_local_authenticated_session_and_correct_origin(client):
    store = client.app.state.store
    doc, _, gate, _ = acquisition(store)
    endpoint = f"/api/workspaces/{doc.id}/gates/preview-magnetic"
    body = {"revision": doc.revision, "gate": gate.model_dump()}
    assert (
        client.post(endpoint, json=body, headers={"X-CytoForge-Token": "invalid"}).status_code
        == 401
    )
    assert (
        client.post(endpoint, json=body, headers={"Origin": "https://hostile.example"}).status_code
        == 403
    )
    assert client.post(endpoint, json=body, headers={"Host": "hostile.example"}).status_code == 400


def test_clearing_tracking_returns_to_anchor_and_keeps_legacy_dto_shape(client):
    store, engine = client.app.state.store, client.app.state.engine
    doc, sample, gate, _ = acquisition(store)
    assert engine.mask(doc, sample, gate.id).sum() == 257
    response = client.put(
        f"/api/workspaces/{doc.id}/gates/{gate.id}",
        json={
            "revision": doc.revision,
            "gate": gate.model_copy(update={"magnetic": None}).model_dump(),
        },
    )
    assert response.status_code == 200
    current = Workspace.model_validate(response.json())
    assert current.gates[0].bounds == gate.bounds and "magnetic" not in response.json()["gates"][0]
    assert engine.mask(current, sample, gate.id).sum() == 0


@pytest.mark.parametrize("kind,mode", [("range", "histogram"), ("rectangle", "contour")])
def test_real_vector_report_and_manifest_record_the_resolved_shape(client, kind, mode):
    from xml.etree import ElementTree as ET

    from cytoforge.models import LayoutDefinition, PlotDefinition, ReportElement

    store = client.app.state.store
    doc, sample, gate, _ = acquisition(store, kind=kind)
    plot = PlotDefinition(
        sample_id=sample.id,
        gate_id=gate.id,
        x="X",
        y=None if kind == "range" else "Y",
        mode=mode,
        bounds=[-1, 3] * (1 if kind == "range" else 2),
    )
    definition = LayoutDefinition(
        name="Magnetic vector review",
        elements=[ReportElement(kind="plot", plot=plot, width_mm=160, height_mm=100)],
    )
    response = client.post(
        f"/api/workspaces/{doc.id}/reports/render",
        json={
            "revision": doc.revision,
            "definition": definition.model_dump(mode="json"),
        },
    )
    assert response.status_code == 200
    page = response.json()
    assert page["exportable"]
    layer = page["manifest"]["elements"][0]["layers"][0]
    assert layer["population_count"] == 257
    records = layer["magnetic_gates"]
    assert len(records) == 1 and records[0]["resolution"]["resolved_count"] == 257
    assert records[0]["anchor_gate"]["bounds"] == gate.bounds
    assert records[0]["resolved_gate"]["bounds"] != gate.bounds
    assert "magnetic" not in records[0]["resolved_gate"]
    root = ET.fromstring(page["svg"])
    assert root.findall(".//{*}path")
    assert "magnetic" in page["svg"]


def test_magnetic_planar_hyperrectangle_has_resolved_boxes_in_3d_and_report_metadata(store):
    from cytoforge.models import LayoutDefinition, PlotDefinition, ReportElement
    from cytoforge.reports import ReportRequest, render

    rng = np.random.default_rng(40)
    values = np.vstack([rng.normal([1.2, 1.1, 0.5], 0.01, (257, 3)), np.full((128, 3), 7)])
    sample = Sample(
        name="XYZ acquisition",
        channels=[Channel(name=n) for n in ["X", "Y", "Z"]],
        event_count=len(values),
    )
    gate = geometric(sample, "hyperrectangle")
    doc = Workspace(name="Magnetic 3D truth", samples=[sample], gates=[gate])
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    doc = store.get(store.create(doc).id)
    engine = Engine(store)
    payload = plot_payload(
        doc,
        engine,
        sample.id,
        "X",
        "Y",
        gate_id=gate.id,
        mode="3d",
        three_d={"z": "Z"},
        bounds=[-1, 3, -1, 3, 0, 1],
    )
    moved, report = engine.resolve_gate(doc, sample, gate)
    assert payload["count"] == 257 and len(payload["boxes"]) == 1
    box = payload["boxes"][0]
    assert box["magnetic"]["resolved_count"] == 257
    np.testing.assert_allclose(
        box["bounds"][:4], [value for d in moved.dimensions for value in [d.minimum, d.maximum]]
    )
    definition = LayoutDefinition(
        name="Magnetic XYZ report",
        elements=[
            ReportElement(
                kind="plot",
                width_mm=160,
                height_mm=100,
                plot=PlotDefinition(
                    sample_id=sample.id,
                    gate_id=gate.id,
                    x="X",
                    y="Y",
                    mode="3d",
                    three_d={"z": "Z"},
                    bounds=[-1, 3, -1, 3, 0, 1],
                ),
            )
        ],
    )
    page = render(doc, engine, ReportRequest(revision=doc.revision, definition=definition))
    layer = page["manifest"]["elements"][0]["layers"][0]
    assert layer["magnetic_gates"][0]["resolution"] == report
    assert layer["population_count"] == 257 and page["exportable"]
