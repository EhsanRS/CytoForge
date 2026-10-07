"""Independent square-root labels, boundary precision and linked scientific workflows."""

import math
from decimal import Decimal, localcontext

import numpy as np
import pytest
from cytoforge.curly import boundaries, exact_positive, labels, raw_center
from cytoforge.models import (
    Channel,
    Compensation,
    CurlyGeometry,
    Gate,
    GateDimension,
    GatePartition,
    Sample,
    Workspace,
)
from cytoforge.science import save_events
from pydantic import ValidationError


def independent(values, center, coefficients):
    x, y = values.T
    cx, cy = center
    ax, ay = coefficients
    h = cy + ay * (np.sqrt(np.maximum(x, cx if cx > 0 else 0)) - math.sqrt(max(cx, 0)))
    v = cx + ax * (np.sqrt(np.maximum(y, cy if cy > 0 else 0)) - math.sqrt(max(cy, 0)))
    return np.where(
        np.isfinite(values).all(axis=1),
        np.where(y >= h, np.where(x >= v, 2, 1), np.where(x >= v, 3, 4)),
        0,
    )


@pytest.mark.parametrize("coefficients", [(0, 0), (1, 0), (0, 2), (1, 2), (4, 5)])
def test_random_full_parent_partition_against_independent_square_root_formulas(coefficients):
    values = np.random.default_rng(3091).uniform(-10, 200, size=(100000, 2))
    np.testing.assert_array_equal(
        labels(*values.T, (3, 7), coefficients), independent(values, (3, 7), coefficients)
    )


def test_straight_negative_arms_center_and_exact_curved_boundaries():
    values = np.array([[0, 4], [1, 0], [0, 0], [1, 4], [4, 7], [3, 9]])
    np.testing.assert_array_equal(labels(*values.T, (1, 4), (2, 3)), [1, 3, 4, 2, 2, 2])
    for boundary, coordinate in [([4, 7], 1), ([3, 9], 0)]:
        below = boundary.copy()
        above = boundary.copy()
        below[coordinate] = np.nextafter(boundary[coordinate], -math.inf)
        above[coordinate] = np.nextafter(boundary[coordinate], math.inf)
        # Lists retain the noninteger neighboring float exactly.
        actual = labels(*np.array([below, boundary, above], dtype=float).T, (1, 4), (2, 3))
        np.testing.assert_array_equal(actual, [3 if coordinate == 1 else 1, 2, 2])


def test_negative_compensated_values_and_crossing_noise_limits_have_explicit_four_labels():
    values = np.array([[-2, -3], [-1, -2], [4, -2], [-1, 9], [1, 1], [np.nan, 0], [0, np.inf]])
    np.testing.assert_array_equal(labels(*values.T, (-2, -3), (5, 5)), [2, 2, 3, 1, 4, 0, 0])


def test_extreme_coefficients_coordinate_differences_and_subnormal_noise():
    tiny = np.nextafter(0.0, 1.0)
    np.testing.assert_array_equal(
        labels(np.array([1e308, -1e308]), np.array([1e308, -1e308]), (0, 0), (1e308, 1e308)), [4, 4]
    )
    np.testing.assert_array_equal(
        labels(np.array([1e308, -1e308]), np.array([-1e308, 1e308]), (-1e308, -1e308), (0, 0)),
        [2, 2],
    )
    np.testing.assert_array_equal(
        labels(np.array([tiny, tiny]), np.array([tiny, 0]), (0, 0), (tiny, tiny)), [2, 3]
    )


def test_exact_radical_comparison_against_high_precision_decimal():
    with localcontext() as context:
        context.prec = 120
        rng = np.random.default_rng(593)
        for x, y, cx, cy, a in rng.uniform(-5, 40, size=(500, 5)):
            a = abs(a)
            signal = max(Decimal(float(x)), Decimal(float(cx)), Decimal(0))
            base = max(Decimal(float(cx)), Decimal(0))
            expected = Decimal(float(y)) - Decimal(float(cy)) >= Decimal(float(a)) * (
                signal.sqrt() - base.sqrt()
            )
            assert exact_positive(float(x), float(y), float(cx), float(cy), float(a)) == expected


def fixture(client):
    values = np.array(
        [[x, y] for x in [-1, 0, 1, 4, 9] for y in [-1, 0, 1, 4, 9]] + [[np.nan, 0], [0, np.inf]]
    )
    store = client.app.state.store
    samples = [
        Sample(
            name=name,
            event_count=len(values),
            channels=[Channel(name="FL1-A"), Channel(name="FL2-A")],
        )
        for name in ["Source", "Target"]
    ]
    doc = Workspace(name="Curly detection limits", samples=samples)
    for sample in samples:
        sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    doc = store.create(doc)
    gate = Gate(
        name="Curly",
        sample_id=samples[0].id,
        kind="curly",
        dimensions=[GateDimension(channel="FL1-A"), GateDimension(channel="FL2-A")],
        partition=GatePartition(kind="curly", member=1),
        curly=CurlyGeometry(center=(0, 0), coefficients=(2, 3)),
    )
    return doc, gate, values


def create(client, doc, gate):
    result = client.post(
        f"/api/workspaces/{doc.id}/gates",
        json={"revision": doc.revision, "gate": gate.model_dump()},
    )
    assert result.status_code == 200, result.text
    return Workspace.model_validate(result.json())


def test_creation_mask_cache_readonly_preview_and_atomic_edit(client):
    doc, gate, values = fixture(client)
    expected = independent(values, (0, 0), (2, 3))
    store = client.app.state.store
    before = doc.model_dump_json(), store.history(doc.id)
    response = client.post(
        f"/api/workspaces/{doc.id}/gates/preview-shape",
        json={"revision": 0, "gate": gate.model_dump()},
    )
    assert response.status_code == 200, response.text
    assert [c["count"] for c in response.json()["partition_counts"]] == [
        int((expected == n).sum()) for n in range(1, 5)
    ]
    assert response.json()["plot"]["overlays"][0]["kind"] == "curly"
    assert len(response.json()["plot"]["overlays"][0]["shared_segments"]) == 4
    assert (store.get(doc.id).model_dump_json(), store.history(doc.id)) == before
    doc = create(client, doc, gate)
    engine = client.app.state.engine
    masks = [engine.mask(doc, doc.samples[0], g.id).copy() for g in doc.gates]
    for n, mask in enumerate(masks, 1):
        np.testing.assert_array_equal(mask, expected == n)
    keys = [k for k in engine.cache.items if "curly" in k]
    assert len(keys) == 1 and engine.cache.items[keys[0]].dtype == np.uint8
    assert not engine.cache.items[keys[0]].flags.writeable
    edited = doc.gates[2].model_copy(deep=True)
    edited.curly.center = (1, 1)
    edited.curly.coefficients = (0.5, 2)
    preview = client.post(
        f"/api/workspaces/{doc.id}/gates/preview-shape",
        json={"revision": doc.revision, "gate": edited.model_dump()},
    )
    assert preview.status_code == 200, preview.text
    np.testing.assert_array_equal(
        [engine.mask(doc, doc.samples[0], g.id) for g in doc.gates], masks
    )
    response = client.put(
        f"/api/workspaces/{doc.id}/gates/{edited.id}",
        json={"revision": doc.revision, "gate": edited.model_dump()},
    )
    assert response.status_code == 200, response.text
    saved = Workspace.model_validate(response.json())
    assert [g.id for g in saved.gates] == [g.id for g in doc.gates]
    assert all(g.curly == edited.curly for g in saved.gates)
    assert (
        client.put(
            f"/api/workspaces/{doc.id}/gates/{edited.id}",
            json={"revision": doc.revision, "gate": edited.model_dump()},
        ).status_code
        == 409
    )


def test_transformed_coordinates_preserve_raw_intensity_predicate_and_nonfinite_exclusion(client):
    from cytoforge.partitions import expand_partition

    doc, gate, values = fixture(client)
    for dimension in gate.dimensions:
        dimension.transform.kind = "asinh"
        dimension.transform.cofactor = 1
    gate.curly.center = (0, 0)
    doc.gates = expand_partition(gate)
    engine = client.app.state.engine
    expected = independent(values, (0, 0), (2, 3))
    assert raw_center(gate) == (0, 0)
    for member in doc.gates:
        np.testing.assert_array_equal(
            engine.mask(doc, doc.samples[0], member.id), expected == member.partition.member
        )
    curves = boundaries(gate, [-2, 4, -2, 4])
    assert len(curves) == 4
    assert np.isfinite(curves).all()
    assert np.allclose(np.asarray(curves[2])[:, 1], 0)
    assert np.allclose(np.asarray(curves[3])[:, 0], 0)
    assert np.asarray(curves[0])[-1, 1] > 0


def test_portable_project_propagation_deletion_and_undo_retain_curly_family(client):
    doc, gate, values = fixture(client)
    doc = create(client, doc, gate)
    copied = client.post(
        f"/api/workspaces/{doc.id}/gates/apply",
        json={
            "revision": doc.revision,
            "source_sample_id": doc.samples[0].id,
            "target_sample_ids": [doc.samples[1].id],
        },
    )
    assert copied.status_code == 200, copied.text
    doc = Workspace.model_validate(copied.json())
    assert len({g.partition.id for g in doc.gates}) == 2
    archive = client.get(f"/api/workspaces/{doc.id}/export/project")
    imported = client.post(
        "/api/import/project",
        files={"file": ("curly.cytoforge", archive.content, "application/zip")},
    )
    assert imported.status_code == 200, imported.text
    restored = Workspace.model_validate(imported.json())
    assert restored.id != doc.id and restored.gates == doc.gates
    selected = next(g for g in doc.gates if g.sample_id == doc.samples[1].id)
    deleted = client.delete(f"/api/workspaces/{doc.id}/gates/{selected.id}?revision={doc.revision}")
    assert deleted.status_code == 200, deleted.text
    assert len(deleted.json()["gates"]) == 4
    undo = client.post(
        f"/api/workspaces/{doc.id}/undo", json={"revision": deleted.json()["revision"]}
    )
    assert undo.json()["gates"] == doc.model_dump(mode="json")["gates"]


@pytest.mark.parametrize(
    "change",
    [
        {"coefficients": (-1, 2)},
        {"coefficients": (math.inf, 1)},
        {"center": (math.nan, 0)},
        {"convention": "invented"},
    ],
)
def test_invalid_noise_geometry_rejected(change):
    with pytest.raises(ValidationError):
        CurlyGeometry(**{"center": (0, 0), **change})


@pytest.mark.parametrize(
    "change",
    [{"bound_min": 0}, {"bound_max": 1}, {"kind": "wsp_log"}, {"kind": "wsp_biex"}],
)
def test_noninvertible_curly_axis_rejected_without_workspace_write(client, change):
    doc, gate, _ = fixture(client)
    payload = gate.model_dump()
    payload["dimensions"][0]["transform"].update(change)
    before = client.app.state.store.get(doc.id).model_dump_json()
    response = client.post(f"/api/workspaces/{doc.id}/gates", json={"revision": 0, "gate": payload})
    assert response.status_code == 422
    assert "invertible" in response.text
    assert client.app.state.store.get(doc.id).model_dump_json() == before


def test_fixed_compensation_duplicate_axis_and_ratio_use_original_scientific_columns(client):
    from cytoforge.partitions import expand_partition

    doc, gate, values = fixture(client)
    matrix = Compensation(
        name="Fixed noise basis",
        detectors=["FL1-A", "FL2-A"],
        outputs=["FL1-A", "FL2-A"],
        matrix=[[1, 0], [0, 2]],
    )
    doc.compensations = [matrix]
    gate.dimensions[0].compensation_ref = "uncompensated"
    gate.dimensions[0].transform.kind = "asinh"
    gate.dimensions[0].transform.cofactor = 1
    gate.dimensions[1] = GateDimension(
        channel="FL1-A",
        ratio_channels=("FL2-A", "FL1-A"),
        compensation_ref=matrix.id,
    )
    doc.gates = expand_partition(gate)
    doc = Workspace.model_validate(doc.model_dump())
    with np.errstate(divide="ignore", invalid="ignore"):
        raw = np.column_stack((values[:, 0], values[:, 1] / (2 * values[:, 0])))
    expected = independent(raw, (0, 0), gate.curly.coefficients)
    for member in doc.gates:
        np.testing.assert_array_equal(
            client.app.state.engine.mask(doc, doc.samples[0], member.id),
            expected == member.partition.member,
        )


def test_nonfinite_log_coordinates_are_ineligible_even_with_finite_raw_values(client):
    from cytoforge.partitions import expand_partition

    doc, gate, raw = fixture(client)
    for dimension in gate.dimensions:
        dimension.transform.kind = "log"
    gate.curly.coefficients = (0, 0)
    doc.gates = expand_partition(gate)
    expected = independent(raw, (1, 1), (0, 0))
    expected[~(np.isfinite(raw).all(axis=1) & (raw > 0).all(axis=1))] = 0
    for member in doc.gates:
        np.testing.assert_array_equal(
            client.app.state.engine.mask(doc, doc.samples[0], member.id),
            expected == member.partition.member,
        )


def test_crossing_limits_draw_boundaries_of_all_adjacent_populations_including_background(client):
    from cytoforge.curly import member_boundaries
    from cytoforge.partitions import expand_partition
    from cytoforge.plotting import project_gates

    doc, gate, _ = fixture(client)
    doc.gates = expand_partition(gate)
    curves = boundaries(gate, [-1, 10, -1, 10])
    pieces = member_boundaries(gate, curves)
    assert len(pieces[4]) == 4  # Lower/left straight arms plus positive crossing branches.
    assert any(np.asarray(piece)[:, 0].max() > 1 for piece in pieces[4])
    direct = project_gates(
        doc,
        doc.samples[0],
        "FL1-A",
        "FL2-A",
        gate.x_transform,
        gate.y_transform,
        None,
        limits=[-1, 10, -1, 10],
    )
    reversed_axes = project_gates(
        doc,
        doc.samples[0],
        "FL2-A",
        "FL1-A",
        gate.y_transform,
        gate.x_transform,
        None,
        limits=[-1, 10, -1, 10],
    )
    for original, swapped in zip(direct, reversed_axes, strict=True):
        assert original["kind"] == swapped["kind"] == "curly"
        for first, second in zip(original["segments"], swapped["segments"], strict=True):
            np.testing.assert_allclose(np.asarray(first)[:, ::-1], second)


def test_population_csv_fcs_exports_use_full_scientific_membership_and_gatingml_is_explicit(client):
    import csv
    import io

    import flowio

    doc, gate, raw = fixture(client)
    doc = create(client, doc, gate)
    expected_labels = independent(raw, (0, 0), gate.curly.coefficients)
    for member in doc.gates:
        route = f"/api/workspaces/{doc.id}/samples/{doc.samples[0].id}/export?gate_id={member.id}"
        expected = raw[expected_labels == member.partition.member]
        exported = client.get(route + "&format=csv")
        assert exported.status_code == 200, exported.text
        rows = list(csv.reader(io.StringIO(exported.text)))
        np.testing.assert_array_equal(np.asarray(rows[1:], dtype=float).reshape(-1, 2), expected)
        exported = client.get(route + "&format=fcs")
        assert exported.status_code == 200, exported.text
        parsed = flowio.FlowData(io.BytesIO(exported.content))
        np.testing.assert_array_equal(np.asarray(parsed.events).reshape(-1, 2), expected)
    response = client.get(f"/api/workspaces/{doc.id}/samples/{doc.samples[0].id}/export/gatingml")
    assert response.status_code == 422
    assert "curly" in response.text.lower()


def test_inconsistent_curly_family_is_rejected_atomically(client):
    doc, gate, _ = fixture(client)
    doc = create(client, doc, gate)
    invalid = doc.model_copy(deep=True)
    invalid.gates[0].curly.coefficients = (1, 1)
    with pytest.raises(ValidationError, match="share their parent and coordinates"):
        Workspace.model_validate(invalid.model_dump())
