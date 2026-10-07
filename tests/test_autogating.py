"""Independent topology, event labels, readonly previews and portable strategies."""

import numpy as np
import pytest
from cytoforge.autogating import AutoGateRequest, extent, preview, selected_rings
from cytoforge.gatingml import export_gatingml
from cytoforge.interchange import ImportApply, ImportMapping, apply_document, parse_document
from cytoforge.magnetic import translated
from cytoforge.models import (
    Channel,
    Gate,
    GateDimension,
    PlotDimension,
    Sample,
    Transform,
    Workspace,
    new_id,
)
from cytoforge.plotting import plot_payload
from cytoforge.science import Engine, polygon_mask, save_events, shape_mask
from pydantic import ValidationError


def square(lo, hi):
    return [(lo, lo), (hi, lo), (hi, hi), (lo, hi)]


def dataset(store, values, gates=None):
    sample = Sample(
        name="Contour events",
        channels=[Channel(name="X"), Channel(name="Y")],
        event_count=len(values),
    )
    doc = Workspace(name="Automatic density gating", samples=[sample])
    if gates:
        doc.gates = gates(sample)
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), np.asarray(values, dtype=float))
    doc = store.create(doc)
    return doc, doc.samples[0], Engine(store)


def request(doc, seed, **kwargs):
    return AutoGateRequest(
        revision=doc.revision,
        sample_id=doc.samples[0].id,
        x="X",
        y="Y",
        seed=seed,
        domain=(-2, 2, -2, 2),
        bins=32,
        sigma=0,
        **kwargs,
    )


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("complement", [False, True])
def test_holes_use_union_exclusion_and_closed_boundaries(store, explicit, complement):
    values = np.array(
        [[x, y] for x in np.arange(-2, 2.1, 0.25) for y in np.arange(-2, 2.1, 0.25)]
        + [[np.nan, 0], [0, np.inf]]
    )

    def gates(sample):
        return [
            Gate(
                name="Hollow",
                sample_id=sample.id,
                kind="polygon",
                x="X",
                y="Y",
                vertices=square(-1.5, 1.5),
                holes=[square(-1, 0.25), square(-0.25, 1)],
                complement=complement,
                dimensions=[GateDimension(channel="X"), GateDimension(channel="Y")]
                if explicit
                else [],
            )
        ]

    doc, sample, engine = dataset(store, values, gates)
    x, y = values.T
    finite = np.isfinite(x) & np.isfinite(y)
    outer = (x >= -1.5) & (x <= 1.5) & (y >= -1.5) & (y <= 1.5)
    holes = ((x >= -1) & (x <= 0.25) & (y >= -1) & (y <= 0.25)) | (
        (x >= -0.25) & (x <= 1) & (y >= -0.25) & (y <= 1)
    )
    positive = outer & ~holes & finite
    expected = ~positive if complement else positive  # Native complement includes nonfinite events.
    np.testing.assert_array_equal(engine.mask(doc, sample, doc.gates[0].id), expected)
    gate = doc.gates[0].model_copy(update={"complement": False})
    moved = translated(gate, np.array([0.5, -0.75]))
    np.testing.assert_allclose(moved.holes, np.asarray(gate.holes) + [0.5, -0.75])
    np.testing.assert_array_equal(shape_mask(moved, [x + 0.5, y - 0.75]), positive)


def test_exact_cell_contour_preserves_donut_and_disconnected_component(store):
    indices = [
        (x, y)
        for x in range(12, 20)
        for y in range(12, 20)
        if x < 14 or x >= 18 or y < 14 or y >= 18
    ]
    ring = [[-2 + (x + 0.5) / 8, -2 + (y + 0.5) / 8] for x, y in indices]
    other = [[1.5625, 1.5625]] * 8
    doc, sample, engine = dataset(store, ring + other)
    result = preview(doc, engine, request(doc, ring[0], coverage=0.995))
    gate = Gate.model_validate(result["gate"])
    assert len(gate.holes) == 1
    assert result["audit"]["component_count"] == 2
    assert result["count"] == len(ring)
    assert not shape_mask(gate, [np.array([0]), np.array([0])])[0]
    assert not shape_mask(gate, [np.array([1.5625]), np.array([1.5625])])[0]
    with pytest.raises(ValueError, match="inside a density region"):
        preview(doc, engine, request(doc, (0, 0), coverage=0.995))
    selected = preview(doc, engine, request(doc, other[0], coverage=0.1))
    assert selected["count"] == len(other)
    assert len(selected["gate"]["vertices"]) == 4


@pytest.mark.parametrize("smoothed", [False, True])
def test_ring_topology_and_diagonal_contacts_against_independent_cells(smoothed):
    grid = np.zeros((32, 32))
    grid[5:15, 5:15] = 20
    grid[8:12, 8:12] = 0
    grid[15:18, 15:18] = 20  # Corner contact does not join the components.
    rings, count, geometry = selected_rings(grid, 12 if smoothed else 20, (0.2, 0.2), smoothed)
    assert count == 2 and len(rings) == 2
    x, y = np.meshgrid((np.arange(32) + 0.5) / 32, (np.arange(32) + 0.5) / 32)
    actual = polygon_mask(x.ravel(), y.ravel(), rings[0])
    actual &= ~polygon_mask(x.ravel(), y.ravel(), rings[1])
    expected = np.zeros((32, 32), dtype=bool)
    expected[5:15, 5:15] = True
    expected[8:12, 8:12] = False
    np.testing.assert_array_equal(actual.reshape(32, 32), expected)
    assert geometry == ("interpolated_centers" if smoothed else "bin_cells")


def test_clipped_and_background_regions_are_rejected():
    grid = np.zeros((32, 32))
    grid[0:3, 0:4] = 5
    for smooth in [False, True]:
        with pytest.raises(ValueError, match="domain boundary"):
            selected_rings(grid, 2, (0.025, 0.025), smooth)
        with pytest.raises(ValueError, match="inside a density region"):
            selected_rings(grid, 2, (0.7, 0.7), smooth)


def test_api_preview_is_revision_checked_readonly_and_density_cache_is_bounded(client):
    store = client.app.state.store
    doc, sample, _ = dataset(store, [[-0.4375, -0.4375]] * 90 + [[0.6875, 0.6875]] * 10)
    route = f"/api/workspaces/{doc.id}/gates/automatic-preview"
    before = store.get(doc.id).model_dump_json(), store.history(doc.id)
    body = request(doc, (-0.4375, -0.4375), coverage=0.85).model_dump()
    response = client.post(route, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 90
    assert response.json()["audit"]["estimated_probability"] == 0.9
    gate = Gate.model_validate(response.json()["gate"])
    engine = client.app.state.engine
    keys = tuple(engine.cache.items)
    assert any(k[0] == "autogate-density" for k in keys)
    assert not any(gate.id in k for k in keys)
    for _ in range(3):
        assert client.post(route, json=body).status_code == 200
    assert set(engine.cache.items) == set(keys)
    assert engine.cache.bytes <= engine.cache.max_bytes
    assert (store.get(doc.id).model_dump_json(), store.history(doc.id)) == before
    body["revision"] = 1
    assert client.post(route, json=body).status_code == 409
    body["revision"] = 0
    body["parent_id"] = new_id()
    assert client.post(route, json=body).status_code == 422
    body["parent_id"] = None
    body["coordinate_gate_id"] = new_id()
    assert client.post(route, json=body).status_code == 422
    assert (store.get(doc.id).model_dump_json(), store.history(doc.id)) == before


@pytest.mark.parametrize("sigma", [0, 1.2])
def test_full_finite_parent_population_and_native_axes_are_preserved(store, sigma):
    values = np.array([[2, 4]] * 100 + [[4, 2]] * 50 + [[8, 1]] * 20 + [[0, 0], [np.nan, 1]])

    def gates(sample):
        return [
            Gate(
                name="Native repeated axes",
                sample_id=sample.id,
                kind="polygon",
                dimensions=[
                    GateDimension(
                        channel="X",
                        compensation_ref="uncompensated",
                        transform=Transform(kind="asinh", cofactor=1),
                    ),
                    GateDimension(
                        channel="X", ratio_channels=("Y", "X"), compensation_ref="uncompensated"
                    ),
                ],
                vertices=[(-1, -1), (4, -1), (4, 3), (-1, 3)],
            )
        ]

    doc, sample, engine = dataset(store, values, gates)
    native = doc.gates[0]
    result = preview(
        doc,
        engine,
        AutoGateRequest(
            revision=0,
            sample_id=sample.id,
            parent_id=native.id,
            coordinate_gate_id=native.id,
            x="X",
            y="X",
            seed=(float(np.arcsinh(2)), 2),
            sigma=sigma,
            bins=64,
            coverage=0.6,
        ),
    )
    gate = Gate.model_validate(result["gate"])
    assert gate.dimensions == native.dimensions
    assert result["count"] == 100
    assert result["parent_count"] == 170
    assert result["audit"]["finite_parent_count"] == 170


@pytest.mark.parametrize("complement", [False, True])
def test_schema_valid_gatingml_retains_holes_parent_and_complement(store, complement):
    values = [
        [x, y]
        for x in [-2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2]
        for y in [-2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2]
    ]

    def gates(sample):
        parent = Gate(
            name="Parent",
            sample_id=sample.id,
            kind="rectangle",
            x="X",
            y="Y",
            bounds=[-2, 2, -2, 2],
        )
        return [
            parent,
            Gate(
                name="Donut",
                sample_id=sample.id,
                parent_id=parent.id,
                kind="polygon",
                x="X",
                y="Y",
                vertices=square(-1.5, 1.5),
                holes=[square(-1, 0.25), square(-0.25, 1)],
                complement=complement,
            ),
        ]

    doc, sample, engine = dataset(store, values, gates)
    original = engine.mask(doc, sample, doc.gates[-1].id).copy()
    payload = plot_payload(
        doc,
        engine,
        sample.id,
        "X",
        "Y",
        gate_id=doc.gates[0].id,
        overlay_gate_ids={doc.gates[1].id},
    )
    assert len(payload["overlays"][0]["holes"]) == 2
    plan = parse_document(export_gatingml(doc, sample), "donut.xml")
    assert not plan.issues
    apply = ImportApply(
        revision=0,
        preview_id=new_id(),
        mappings=[ImportMapping(source_id=plan.sources[0].id, sample_ids=[sample.id])],
    )
    apply_document(doc, plan, apply, engine)
    imported = next(g for g in reversed(doc.gates) if g.name == "Donut")
    # GatingML represents excluded rings as an ordinary Boolean strategy.
    np.testing.assert_array_equal(Engine(store).mask(doc, sample, imported.id), original)


def test_empty_nonfinite_and_insufficient_domain_mass_fail_without_a_gate(store):
    for values in [np.empty((0, 2)), [[np.nan, np.nan], [np.inf, 0]]]:
        doc, _, engine = dataset(store, values)
        with pytest.raises(ValueError, match="finite events"):
            preview(doc, engine, request(doc, (0, 0)))
    doc, _, engine = dataset(store, [[-0.4375, -0.4375]] + [[10, 10]] * 20)
    with pytest.raises(ValueError, match="Insufficient finite-parent mass"):
        preview(doc, engine, request(doc, (-0.4375, -0.4375), coverage=0.9))


def test_extents_handle_opposite_extremes_and_constant_parameters():
    for values in [np.array([-1e308, 1e308]), np.array([0.0, 0.0]), np.array([2.0, 2.0])]:
        limits = extent(values, 0.1)
        assert np.all(np.isfinite(limits)) and limits[0] < limits[1]
        assert limits[0] <= values.min() and limits[1] >= values.max()


@pytest.mark.parametrize(
    "changes",
    [
        {"coverage": 1},
        {"sigma": -1},
        {"bins": 500},
        {"seed": (np.nan, 0)},
        {"domain": (1, 0, 0, 1)},
    ],
)
def test_invalid_requests_are_rejected(changes):
    with pytest.raises(ValidationError):
        AutoGateRequest(
            **{"revision": 0, "sample_id": new_id(), "x": "X", "y": "Y", "seed": (0, 0), **changes}
        )


def test_explicit_ratio_automatic_gate_uses_full_parent_and_retains_definition(store):
    ring = [
        [-2 + (x + 0.5) / 8, -2 + (y + 0.5) / 8]
        for x in range(12, 20)
        for y in range(12, 20)
        if x < 14 or x >= 18 or y < 14 or y >= 18
    ]
    displayed = ring + [[1.5625, 1.5625]] * 8
    acquired = [[ratio * denominator, denominator] for ratio, denominator in displayed] + [[1, 0]]
    doc, sample, engine = dataset(store, acquired)
    before = doc.model_dump_json()
    ratio = PlotDimension(
        channel="X/Y", compensation_ref="uncompensated", ratio_channels=("X", "Y")
    )
    raw = PlotDimension(channel="Y", compensation_ref="uncompensated")
    body = AutoGateRequest(
        revision=doc.revision,
        sample_id=sample.id,
        x="X/Y",
        y="Y",
        x_dimension=ratio,
        y_dimension=raw,
        seed=ring[0],
        domain=(-2, 2, -2, 2),
        bins=32,
        sigma=0,
        coverage=0.995,
    )
    result = preview(doc, engine, body)
    assert result["count"] == len(ring)
    assert result["parent_count"] == len(acquired)
    assert result["audit"]["finite_parent_count"] == len(displayed)
    assert result["gate"]["dimensions"][0]["ratio_channels"] == ("X", "Y")
    assert len(result["gate"]["holes"]) == 1
    assert store.get(doc.id).model_dump_json() == before
    gate = Gate.model_validate(result["gate"])
    doc.gates.append(gate)
    np.testing.assert_array_equal(
        engine.mask(doc, sample, gate.id), [True] * len(ring) + [False] * 9
    )
