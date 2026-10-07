"""Independent populations for traced, nonconvex and self-crossing outlines."""

import numpy as np
import pytest
from cytoforge.models import Channel, Gate, Sample, Workspace
from cytoforge.science import _polygon_mask_vectorized, polygon_mask, save_events


def test_balanced_self_crossing_outline_preview_save_children_undo(client):
    # Upper/lower triangles have opposite orientation and zero total signed area.
    values = np.array([(x, y) for y in np.arange(-3, 3.5, 0.5) for x in np.arange(-3, 3.5, 0.5)])
    values = np.vstack((values, [[np.nan, 1], [1, np.inf]]))
    finite = np.isfinite(values).all(axis=1)
    expected = finite & (np.abs(values[:, 0]) <= 2) & (np.abs(values[:, 1]) <= 2)
    expected &= np.abs(values[:, 1]) >= np.abs(values[:, 0])
    sample = Sample(
        name="Labelled crossing lobes",
        event_count=len(values),
        channels=[Channel(name="X"), Channel(name="Y")],
    )
    doc = Workspace(name="Freehand scientific truth", samples=[sample])
    store, engine = client.app.state.store, client.app.state.engine
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    doc = store.create(doc)
    outline = Gate(
        sample_id=sample.id,
        name="Two crossing lobes",
        kind="polygon",
        x="X",
        y="Y",
        vertices=[(-2, -2), (2, 2), (-2, 2), (2, -2)],
        provenance={"drawing_tool": "freehand", "vertex_spacing_css_pixels": 2},
    )
    before, history = doc.model_dump_json(), store.history(doc.id)
    response = client.post(
        f"/api/workspaces/{doc.id}/gates/preview-shape",
        json={"revision": doc.revision, "gate": outline.model_dump()},
    )
    assert response.status_code == 200, response.text
    assert response.json()["count"] == int(expected.sum())
    assert response.json()["parent_count"] == len(values)
    assert store.get(doc.id).model_dump_json() == before
    assert store.history(doc.id) == history
    child = Gate(
        sample_id=sample.id,
        parent_id=outline.id,
        name="Upper lobe",
        kind="range",
        x="Y",
        bounds=[0.5, 3],
    )
    response = client.post(
        f"/api/workspaces/{doc.id}/gates/batch",
        json={"revision": doc.revision, "gates": [outline.model_dump(), child.model_dump()]},
    )
    assert response.status_code == 200, response.text
    saved = Workspace.model_validate(response.json())
    sample = saved.samples[0]
    np.testing.assert_array_equal(engine.mask(saved, sample, outline.id), expected)
    np.testing.assert_array_equal(
        engine.mask(saved, sample, child.id), expected & (values[:, 1] >= 0.5)
    )
    response = client.post(f"/api/workspaces/{saved.id}/undo", json={"revision": saved.revision})
    assert response.status_code == 200, response.text
    restored = Workspace.model_validate(response.json())
    assert not restored.gates
    response = client.post(
        f"/api/workspaces/{restored.id}/redo", json={"revision": restored.revision}
    )
    assert response.status_code == 200, response.text
    redone = Workspace.model_validate(response.json())
    np.testing.assert_array_equal(engine.mask(redone, redone.samples[0], outline.id), expected)
    assert redone.gates[0].vertices == outline.vertices
    assert redone.gates[0].provenance == outline.provenance


@pytest.mark.parametrize("scale", [1e-140, 1.0, 1e140])
def test_polygon_validity_is_not_signed_area_or_coordinate_scale(scale):
    gate = Gate(
        sample_id="a" * 32,
        name="Crossing",
        kind="polygon",
        x="X",
        y="Y",
        vertices=[(-scale, -scale), (scale, scale), (-scale, scale), (scale, -scale)],
    )
    assert len(gate.vertices) == 4
    with pytest.raises(ValueError, match="area"):
        Gate(
            sample_id="a" * 32,
            name="Collinear",
            kind="polygon",
            x="X",
            y="Y",
            vertices=[(-scale, -scale), (0, 0), (scale, scale)],
        )


@pytest.mark.parametrize("shape", ["rectangle", "nonconvex", "crossing"])
def test_dense_outline_index_has_independently_known_populations(shape):
    if shape == "rectangle":
        corners = [(-2, -2), (2, -2), (2, 2), (-2, 2)]
    elif shape == "nonconvex":
        corners = [(-2, -2), (2, -2), (2, -1), (-1, -1), (-1, 1), (2, 1), (2, 2), (-2, 2)]
    else:
        corners = [(-2, -2), (2, 2), (-2, 2), (2, -2)]
    vertices = [
        tuple(a + (b - a) * f)
        for a, b in zip(np.array(corners), np.roll(corners, -1, axis=0), strict=True)
        for f in np.linspace(0, 1, 2000 // len(corners), endpoint=False)
    ]
    xy = np.array([(x, y) for x in np.arange(-50, 51) * 0.06 for y in np.arange(-50, 51) * 0.06])
    xy = np.vstack((xy, corners, [[0, 0], [1, -1], [np.nan, 0], [0, np.inf]]))
    x, y = xy.T
    expected = np.isfinite(x) & np.isfinite(y) & (np.abs(x) <= 2) & (np.abs(y) <= 2)
    if shape == "nonconvex":
        expected &= (x <= -1) | (np.abs(y) >= 1)
    elif shape == "crossing":
        expected &= np.abs(y) >= np.abs(x)
    np.testing.assert_array_equal(polygon_mask(x, y, vertices), expected)


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_dense_outline_keeps_original_boundary_tolerances_and_input_shape(dtype):
    vertices = [
        tuple(a + (b - a) * f)
        for a, b in zip(
            np.array([[-2, -2], [2, -2], [2, 2], [-2, 2]]),
            np.array([[2, -2], [2, 2], [-2, 2], [-2, -2]]),
            strict=True,
        )
        for f in np.linspace(0, 1, 500, endpoint=False)
    ]
    x = np.linspace(-3, 3, 1024, dtype=dtype).reshape(-1, 1)
    y = np.full_like(x, -2)
    for delta in [0, -5e-11, -2e-10, 5e-11]:
        shifted = y + dtype(delta)
        np.testing.assert_array_equal(
            polygon_mask(x, shifted, vertices), _polygon_mask_vectorized(x, shifted, vertices)
        )
