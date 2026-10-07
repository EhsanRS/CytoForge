"""Read-only native-shape previews, independent membership and cache isolation."""

import numpy as np
import pytest
from cytoforge.models import (
    Channel,
    Compensation,
    Gate,
    GateDimension,
    Sample,
    Transform,
    Workspace,
)
from cytoforge.science import save_events


def acquisition(client, values=None):
    store = client.app.state.store
    if values is None:
        values = np.array(
            [[-3, -3], [0, 0], [0.2, 0.2], [1, 0.3], [1.2, 1.1], [2, 2], [5, 5], [np.nan, 1]]
        )
    sample = Sample(
        name="Independent shape labels",
        channels=[Channel(name="X"), Channel(name="Y")],
        event_count=len(values),
    )
    gate = Gate(
        sample_id=sample.id,
        name="Original",
        kind="rectangle",
        x="X",
        y="Y",
        bounds=[-0.5, 0.5, -0.5, 0.5],
    )
    doc = Workspace(name="Visual gate review", samples=[sample], gates=[gate])
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    doc = store.create(doc)
    return doc, doc.samples[0], doc.gates[0], values


def preview(client, doc, gate, **view):
    return client.post(
        f"/api/workspaces/{doc.id}/gates/preview-shape",
        json={
            "revision": doc.revision,
            "gate": gate.model_dump(),
            **view,
        },
    )


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("range", 2),
        ("rectangle", 1),
        ("polygon", 1),
        ("ellipse", 1),
        ("hyperrectangle", 1),
        ("ellipsoid", 1),
        ("quadrant", 3),
    ],
)
def test_exact_native_coordinate_previews_and_no_saved_cache_pollution(client, kind, expected):
    doc, sample, original, _ = acquisition(client)
    engine, store = client.app.state.engine, client.app.state.store
    before, history = doc.model_dump_json(), store.history(doc.id)
    assert engine.mask(doc, sample, original.id).sum() == 2
    changes = dict(kind=kind, bounds=[1, 1.4, 1, 1.4])
    if kind == "range":
        changes.update(y=None, bounds=[0.9, 1.3])
    elif kind == "polygon":
        changes.update(vertices=[(0.9, 0.9), (1.6, 0.9), (1.25, 1.6)])
    elif kind == "ellipse":
        changes.update(center=(1.2, 1.1), radii=(0.3, 0.2), angle=0.3)
    elif kind in {"hyperrectangle", "ellipsoid"}:
        changes.update(
            x=None,
            y=None,
            bounds=[],
            dimensions=[GateDimension(channel=n, minimum=1, maximum=1.4) for n in ["X", "Y"]],
        )
        if kind == "ellipsoid":
            changes.update(coordinates=[1.2, 1.1], covariance=[[0.09, 0], [0, 0.04]])
    elif kind == "quadrant":
        changes.update(bounds=[1, 1], quadrant=2)
    draft = Gate.model_validate(original.model_copy(update=changes).model_dump())
    response = preview(client, doc, draft)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["count"] == expected
    assert data["parent_count"] == 8
    assert data["plot"]["finite_count"] == 7
    assert data["plot"]["revision"] == doc.revision and data["magnetic"] is None
    # Repeat with the same gate ID/revision after an independent geometry change.
    assert preview(client, doc, original).json()["count"] == 2
    assert engine.mask(doc, sample, original.id).sum() == 2
    assert store.get(doc.id).model_dump_json() == before and store.history(doc.id) == history


def test_range_ignores_legacy_second_channel_and_rejects_cross_sample_and_3d(client):
    doc, sample, gate, _ = acquisition(client)
    range_gate = gate.model_copy(update={"kind": "range", "bounds": [-0.5, 0.5]})
    response = preview(client, doc, range_gate, mode="cdf")
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 2
    assert response.json()["plot"]["y"] is None
    assert preview(client, doc, gate.model_copy(update={"sample_id": "1" * 32})).status_code == 422
    three_d = Gate(
        sample_id=sample.id,
        name="Three dimensions",
        kind="hyperrectangle",
        dimensions=[GateDimension(channel=n) for n in ["X", "Y", "Z"]],
    )
    assert preview(client, doc, three_d).status_code == 422


def test_preview_parent_membership_complement_apply_descendants_and_undo(client):
    doc, sample, gate, _ = acquisition(client)
    store, engine = client.app.state.store, client.app.state.engine
    child = Gate(
        sample_id=sample.id,
        parent_id=gate.id,
        name="Child",
        kind="range",
        x="Y",
        bounds=[-0.5, 0.5],
    )
    doc = store.mutate(doc.id, "Child", lambda d: d.gates.append(child), doc.revision)
    assert engine.mask(doc, sample, child.id).sum() == 2
    draft = gate.model_copy(update={"bounds": [1, 1.4, 1, 1.4]})
    assert preview(client, doc, draft).json()["count"] == 1
    assert preview(client, doc, draft.model_copy(update={"complement": True})).json()["count"] == 7
    assert engine.mask(doc, sample, child.id).sum() == 2
    response = client.put(
        f"/api/workspaces/{doc.id}/gates/{gate.id}",
        json={"revision": doc.revision, "gate": draft.model_dump()},
    )
    assert response.status_code == 200
    saved = Workspace.model_validate(response.json())
    np.testing.assert_array_equal(
        engine.mask(saved, sample, gate.id), [False, False, False, False, True, False, False, False]
    )
    assert engine.mask(saved, sample, child.id).sum() == 0
    restored = Workspace.model_validate(
        client.post(f"/api/workspaces/{doc.id}/undo", json={"revision": saved.revision}).json()
    )
    assert (
        engine.mask(restored, sample, gate.id).sum()
        == engine.mask(restored, sample, child.id).sum()
        == 2
    )


def test_unbounded_native_dimension_and_ratio_compensation_basis(client):
    doc, sample, gate, values = acquisition(client)
    # Ratio values are calculated from raw X/Y; the zero denominator remains nonfinite.
    dimension = GateDimension(
        channel="X",
        ratio_channels=("X", "Y"),
        compensation_ref="uncompensated",
        transform={"kind": "asinh", "cofactor": 2},
        minimum=float(np.arcsinh(1 / 2)),
        maximum=None,
    )
    draft = gate.model_copy(
        update={
            "kind": "hyperrectangle",
            "x": None,
            "y": None,
            "bounds": [],
            "dimensions": [dimension],
        }
    )
    response = preview(client, doc, draft, mode="cdf")
    assert response.status_code == 200, response.text
    with np.errstate(all="ignore"):
        ratio = values[:, 0] / values[:, 1]
    expected = np.isfinite(ratio) & (ratio >= 1)
    assert response.json()["count"] == int(expected.sum())
    assert response.json()["plot"]["x_transform"]["kind"] == "asinh"
    assert response.json()["plot"]["cdf_denominator"] == int(np.isfinite(ratio).sum())
    assert response.json()["plot"]["coordinate_gate_id"] == gate.id
    assert client.app.state.engine.mask(doc, sample, gate.id).sum() == 2


def test_magnetic_preview_keeps_anchor_and_isolated_resolved_counts(client):
    doc, _, gate, _ = acquisition(client)
    before = client.app.state.store.get(doc.id).model_dump_json()
    draft = Gate.model_validate(
        {**gate.model_dump(), "bounds": [0.65, 0.85, 1, 1.2], "magnetic": {"max_shift": 4}}
    )
    response = preview(client, doc, draft)
    assert response.status_code == 200, response.text
    report = response.json()["magnetic"]
    assert report["anchor_count"] == 0 and report["population_count"] == 1
    assert response.json()["count"] == 1 and report["shift"] != [0, 0]
    assert client.app.state.store.get(doc.id).model_dump_json() == before


def test_stale_invalid_graph_cross_sample_shapes_and_access_guards(client):
    doc, sample, gate, _ = acquisition(client)
    route = f"/api/workspaces/{doc.id}/gates/preview-shape"
    body = {"revision": doc.revision, "gate": gate.model_dump()}
    assert client.post(route, json={**body, "revision": doc.revision + 1}).status_code == 409
    assert preview(client, doc, gate.model_copy(update={"parent_id": gate.id})).status_code == 422
    assert preview(client, doc, gate.model_copy(update={"x": "Missing"})).status_code == 422
    assert preview(client, doc, gate, bounds=[0, 1]).status_code == 422
    assert preview(client, doc, gate, bounds=[1, 0, 0, 1]).status_code == 422
    assert preview(client, doc, gate, bins=1000).status_code == 422
    unsupported = Gate(sample_id=sample.id, name="Boolean", kind="boolean", operands=[gate.id])
    assert preview(client, doc, unsupported).status_code == 422
    assert (
        client.post(route, json=body, headers={"X-CytoForge-Token": "invalid"}).status_code == 401
    )
    assert (
        client.post(route, json=body, headers={"Origin": "https://hostile.example"}).status_code
        == 403
    )
    assert client.post(route, json=body, headers={"Host": "hostile.example"}).status_code == 400


def test_empty_parent_draft_cdf_and_unsaved_gate_have_no_side_effects(client):
    doc, sample, gate, _ = acquisition(client, np.empty((0, 2)))
    store = client.app.state.store
    new = Gate(sample_id=sample.id, name="Unsaved range", kind="range", x="X", bounds=[1, 2])
    before, history = doc.model_dump_json(), store.history(doc.id)
    response = preview(client, doc, new, mode="cdf")
    assert response.status_code == 200, response.text
    assert response.json()["count"] == response.json()["parent_count"] == 0
    assert response.json()["plot"]["cdf_denominator"] == 0
    assert store.get(doc.id).model_dump_json() == before and store.history(doc.id) == history


def test_fixed_compensation_and_bounded_native_scale_override_sample_display(client):
    doc, sample, gate, _ = acquisition(client)
    fixed = Compensation(
        name="Independent diagonal control", detectors=["X", "Y"], matrix=[[2, 0], [0, 4]]
    )
    assigned = Compensation(name="Sample default", detectors=["X", "Y"], matrix=[[1, 0], [0, 1]])

    def assign(value):
        value.compensations.extend([fixed, assigned])
        value.samples[0].compensation_id = assigned.id

    doc = client.app.state.store.mutate(doc.id, "Matrices", assign, doc.revision)
    scale = Transform(kind="asinh", cofactor=2, bound_min=0, bound_max=1)
    dimensions = [
        GateDimension(
            channel=name,
            transform=scale,
            compensation_ref=fixed.id,
            minimum=float(np.arcsinh(lo / 2)),
            maximum=float(np.arcsinh(hi / 2)),
        )
        for name, lo, hi in [("X", 0.55, 0.65), ("Y", 0.25, 0.30)]
    ]
    draft = Gate.model_validate(
        {
            **gate.model_dump(),
            "kind": "hyperrectangle",
            "x": None,
            "y": None,
            "bounds": [],
            "dimensions": dimensions,
        }
    )
    response = preview(client, doc, draft, mode="scatter")
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1
    assert response.json()["plot"]["x_transform"] == scale.model_dump()
    points = np.asarray(response.json()["plot"]["points"])
    expected = np.arcsinh(np.array([0.6, 0.275]) / 2)
    assert np.any(np.all(np.isclose(points, expected), axis=1))


def test_same_parameter_with_distinct_compensation_axes_retains_native_basis(client):
    doc, sample, gate, _ = acquisition(client)
    fixed = Compensation(name="Fixed diagonal", detectors=["X", "Y"], matrix=[[2, 0], [0, 4]])
    doc = client.app.state.store.mutate(
        doc.id, "Matrix", lambda d: d.compensations.append(fixed), doc.revision
    )
    dimensions = [
        GateDimension(channel="X", compensation_ref="uncompensated", minimum=1.1, maximum=1.3),
        GateDimension(channel="X", compensation_ref=fixed.id, minimum=0.55, maximum=0.65),
    ]
    draft = Gate.model_validate(
        {
            **gate.model_dump(),
            "kind": "hyperrectangle",
            "x": None,
            "y": None,
            "bounds": [],
            "dimensions": dimensions,
        }
    )
    response = preview(client, doc, draft, mode="scatter")
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1
    points = np.asarray(response.json()["plot"]["points"])
    assert np.any(np.all(np.isclose(points, [1.2, 0.6]), axis=1))
    overlay = response.json()["plot"]["overlays"]
    assert len(overlay) == 1
    np.testing.assert_allclose(
        overlay[0]["vertices"], [[1.1, 0.55], [1.3, 0.55], [1.3, 0.65], [1.1, 0.65]]
    )
