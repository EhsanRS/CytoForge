"""Independent angular labels, linked edits, cache isolation and extreme coordinates."""

import math
from fractions import Fraction

import numpy as np
import pytest
from cytoforge.models import (
    Channel,
    Compensation,
    Gate,
    GateDimension,
    GatePartition,
    Sample,
    SpiderGeometry,
    Workspace,
)
from cytoforge.science import save_events
from cytoforge.spider import MEMBER_ARMS, clip_arm, labels
from pydantic import ValidationError


def fixture(client):
    values = np.array(
        [[x, y] for x in [-1, 0, 1] for y in [-1, 0, 1]] + [[np.nan, 0], [0, np.inf], [0, -np.inf]]
    )
    store = client.app.state.store
    samples = [
        Sample(name=name, event_count=len(values), channels=[Channel(name="X"), Channel(name="Y")])
        for name in ["Source", "Target"]
    ]
    doc = Workspace(name="Native spider partitions", samples=samples)
    for sample in samples:
        sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    doc = store.create(doc)
    gate = Gate(
        name="Spider",
        sample_id=samples[0].id,
        kind="spider",
        x="X",
        y="Y",
        dimensions=[GateDimension(channel="X"), GateDimension(channel="Y")],
        partition=GatePartition(kind="spider", member=1),
        spider=SpiderGeometry(center=(0, 0), scale=(1, 1)),
    )
    return doc, gate, values


def masks(client, doc):
    engine = client.app.state.engine
    sample = doc.samples[0]
    return [
        engine.mask(doc, sample, g.id)
        for g in doc.gates
        if g.partition and g.partition.id == doc.gates[0].partition.id
    ]


def test_default_cardinal_boundary_ownership_matches_four_quadrants():
    x, y = np.meshgrid([-1.0, 0.0, 1.0], [-1.0, 0.0, 1.0])
    expected = np.where(y >= 0, np.where(x >= 0, 2, 1), np.where(x >= 0, 3, 4))
    geometry = SpiderGeometry(center=(0, 0), scale=(1, 1))
    np.testing.assert_array_equal(labels(x.ravel(), y.ravel(), geometry), expected.ravel())
    assert labels(np.array([np.nan, 0, np.inf]), np.array([0, np.inf, 0]), geometry).tolist() == [
        0,
        0,
        0,
    ]


@pytest.mark.parametrize("angles", [[0, 0.4, 2.3, 4.9], [5.9, 0.4, 2.3, 4.9], [0, 0.2, 0.4, 0.6]])
def test_rotated_and_reflex_sectors_against_independent_polar_labels(angles):
    rng = np.random.default_rng(712)
    x, y = rng.normal(size=(2, 50000))
    geometry = SpiderGeometry(center=(0.25, -0.5), scale=(2, 0.75), angles=angles)
    polar = (np.arctan2((y + 0.5) / 0.75, (x - 0.25) / 2) - angles[0]) % (2 * math.pi)
    offsets = [(angle - angles[0]) % (2 * math.pi) for angle in angles]
    index = np.searchsorted(offsets, polar, side="right") - 1
    expected = np.array([2, 1, 4, 3])[index]
    np.testing.assert_array_equal(labels(x, y, geometry), expected)


def test_extreme_coordinate_differences_scales_and_subnormal_sides():
    tiny = np.nextafter(0.0, 1.0)
    values = np.array(
        [[-tiny, 1e308], [tiny, -1e308], [-1e308, -tiny], [1e308, tiny], [0, 0], [-1e308, 1e308]]
    )
    for scale in [(1, 1), (1e-308, 1e308), (1e308, 1e-308)]:
        geometry = SpiderGeometry(center=(0, 0), scale=scale)
        np.testing.assert_array_equal(labels(*values.T, geometry), [1, 3, 4, 2, 2, 1])
    geometry = SpiderGeometry(center=(1e308, -1e308), scale=(1e-308, 1e308))
    np.testing.assert_array_equal(
        labels(np.array([-1e308, 1e308]), np.array([1e308, -1e308]), geometry), [1, 2]
    )


def test_creation_cache_once_per_family_and_full_finite_parent_partition(client):
    doc, gate, values = fixture(client)
    response = client.post(
        f"/api/workspaces/{doc.id}/gates", json={"revision": 0, "gate": gate.model_dump()}
    )
    assert response.status_code == 200, response.text
    doc = Workspace.model_validate(response.json())
    assert len(doc.gates) == 4
    np.testing.assert_array_equal(
        np.sum(masks(client, doc), axis=0), np.isfinite(values).all(axis=1)
    )
    assert [int(mask.sum()) for mask in masks(client, doc)] == [2, 4, 2, 1]
    keys = [k for k in client.app.state.engine.cache.items if "spider" in k]
    assert len(keys) == 1
    assert client.app.state.engine.cache.items[keys[0]].dtype == np.uint8
    assert not client.app.state.engine.cache.items[keys[0]].flags.writeable
    assert client.app.state.engine.cache.bytes <= client.app.state.engine.cache.max_bytes


@pytest.mark.parametrize(
    "changes",
    [
        {"angles": [0, 0, 2, 3]},
        {"angles": [0, 3, 2, 4]},
        {"angles": [0, 1, 2, math.tau]},
        {"scale": [0, 1]},
        {"center": [math.inf, 0]},
    ],
)
def test_invalid_shared_geometry_rejected(changes):
    with pytest.raises(ValidationError):
        SpiderGeometry(**{"center": (0, 0), "scale": (1, 1), **changes})


def test_shared_edit_is_atomic_and_inconsistent_families_rejected(client):
    doc, gate, _ = fixture(client)
    doc = Workspace.model_validate(
        client.post(
            f"/api/workspaces/{doc.id}/gates", json={"revision": 0, "gate": gate.model_dump()}
        ).json()
    )
    edited = doc.gates[2].model_copy(deep=True)
    edited.spider.center = (0.5, -0.5)
    edited.spider.angles = (0, 0.7, 2.5, 4.8)
    response = client.put(
        f"/api/workspaces/{doc.id}/gates/{edited.id}",
        json={"revision": 1, "gate": edited.model_dump()},
    )
    assert response.status_code == 200, response.text
    updated = Workspace.model_validate(response.json())
    assert [g.id for g in updated.gates] == [g.id for g in doc.gates]
    assert all(g.spider == edited.spider for g in updated.gates)
    assert len({g.partition.id for g in updated.gates}) == 1
    assert (
        client.put(
            f"/api/workspaces/{doc.id}/gates/{edited.id}",
            json={"revision": 1, "gate": edited.model_dump()},
        ).status_code
        == 409
    )
    updated.gates[0].spider.center = (1, 1)
    with pytest.raises(ValidationError, match="share their parent and coordinates"):
        Workspace.model_validate(updated.model_dump())


def test_rational_ray_clipping_viewport_and_opposite_extremes():
    geometry = SpiderGeometry(center=(0, 0), scale=(1, 1))
    assert clip_arm(geometry, [-1, 1, -2, 2], 0) == [[0, 0], [1, 0]]
    assert clip_arm(geometry, [-1, 1, -2, 2], 1) == [[0, 0], [0, 2]]
    assert clip_arm(geometry, [-1, 1, -2, 2], 2) == [[0, 0], [-1, 0]]
    assert clip_arm(geometry, [1, 2, 1, 2], 0) is None
    geometry = SpiderGeometry(center=(-1e308, 0), scale=(1e-308, 1e308))
    assert clip_arm(geometry, [0, 1e308, -1, 1], 0) == [[0, 0], [1e308, 0]]
    assert MEMBER_ARMS == {1: (1, 2), 2: (0, 1), 3: (3, 0), 4: (2, 3)}


def test_events_one_ulp_either_side_of_arm_use_exact_saved_float_coefficients():
    angle = math.pi / 4
    cosine, sine = math.cos(angle), math.sin(angle)
    x = np.array([np.nextafter(cosine, 0), cosine, np.nextafter(cosine, math.inf)] * 3)
    y = np.repeat([np.nextafter(sine, 0), sine, np.nextafter(sine, math.inf)], 3)
    geometry = SpiderGeometry(
        center=(0, 0), scale=(1, 1), angles=(angle, math.pi / 2, math.pi, 3 * math.pi / 2)
    )
    exact = [
        (Fraction(float(b)) * Fraction(cosine) - Fraction(float(a)) * Fraction(sine))
        for a, b in zip(x, y, strict=True)
    ]
    np.testing.assert_array_equal(
        labels(x, y, geometry), [2 if cross >= 0 else 3 for cross in exact]
    )


def test_preview_is_readonly_and_saved_label_cache_is_not_polluted(client):
    doc, gate, _ = fixture(client)
    store = client.app.state.store
    before = store.get(doc.id).model_dump_json(), store.history(doc.id)
    preview = client.post(
        f"/api/workspaces/{doc.id}/gates/preview-shape",
        json={"revision": 0, "gate": gate.model_dump()},
    )
    assert preview.status_code == 200, preview.text
    assert [c["count"] for c in preview.json()["partition_counts"]] == [2, 4, 2, 1]
    assert preview.json()["plot"]["overlays"][0]["kind"] == "spider"
    assert (store.get(doc.id).model_dump_json(), store.history(doc.id)) == before
    doc = Workspace.model_validate(
        client.post(
            f"/api/workspaces/{doc.id}/gates", json={"revision": 0, "gate": gate.model_dump()}
        ).json()
    )
    warm = [m.copy() for m in masks(client, doc)]
    gate = doc.gates[0].model_copy(deep=True)
    gate.spider.center = (0.5, 0.5)
    preview = client.post(
        f"/api/workspaces/{doc.id}/gates/preview-shape",
        json={"revision": doc.revision, "gate": gate.model_dump()},
    )
    assert preview.status_code == 200, preview.text
    assert [c["count"] for c in preview.json()["partition_counts"]] == [2, 1, 2, 4]
    np.testing.assert_array_equal(masks(client, doc), warm)
    assert store.get(doc.id).model_dump_json() == doc.model_dump_json()
    assert (
        client.post(
            f"/api/workspaces/{doc.id}/gates/preview-shape",
            json={"revision": 0, "gate": gate.model_dump()},
        ).status_code
        == 409
    )


def test_propagation_deletion_undo_and_portable_project_retain_linked_geometry(client):
    doc, gate, _ = fixture(client)
    doc = client.post(
        f"/api/workspaces/{doc.id}/gates", json={"revision": 0, "gate": gate.model_dump()}
    ).json()
    response = client.post(
        f"/api/workspaces/{doc['id']}/gates/apply",
        json={
            "revision": doc["revision"],
            "source_sample_id": doc["samples"][0]["id"],
            "target_sample_ids": [doc["samples"][1]["id"]],
        },
    )
    assert response.status_code == 200, response.text
    doc = Workspace.model_validate(response.json())
    families = [[g for g in doc.gates if g.sample_id == sample.id] for sample in doc.samples]
    assert len({f[0].partition.id for f in families}) == 2
    assert all(g.spider == gate.spider for g in doc.gates)
    archive = client.get(f"/api/workspaces/{doc.id}/export/project")
    assert archive.status_code == 200, archive.text
    restored = client.post(
        "/api/import/project",
        files={"file": ("spider.cytoforge", archive.content, "application/zip")},
    )
    assert restored.status_code == 200, restored.text
    imported = Workspace.model_validate(restored.json())
    assert imported.id != doc.id
    assert imported.gates == doc.gates
    for sample in imported.samples:
        assert [
            client.app.state.engine.mask(imported, sample, g.id).sum()
            for g in imported.gates
            if g.sample_id == sample.id
        ] == [2, 4, 2, 1]
    response = client.delete(
        f"/api/workspaces/{doc.id}/gates/{families[1][2].id}?revision={doc.revision}"
    )
    assert response.status_code == 200, response.text
    assert [g["id"] for g in response.json()["gates"]] == [g.id for g in families[0]]
    undo = client.post(
        f"/api/workspaces/{doc.id}/undo", json={"revision": response.json()["revision"]}
    )
    assert undo.status_code == 200, undo.text
    assert undo.json()["gates"] == doc.model_dump(mode="json")["gates"]


def test_unbounded_spider_gatingml_export_has_explicit_error_instead_of_cropping(client):
    doc, gate, _ = fixture(client)
    client.post(f"/api/workspaces/{doc.id}/gates", json={"revision": 0, "gate": gate.model_dump()})
    response = client.get(f"/api/workspaces/{doc.id}/samples/{doc.samples[0].id}/export/gatingml")
    assert response.status_code == 422, response.text
    assert "cannot be represented exactly" in response.json()["detail"]


def test_projection_clips_rays_without_overflow_at_opposite_extremes(client):
    from cytoforge.plotting import project_gates

    doc, gate, _ = fixture(client)
    gate.spider.center = (-1e308, 0)
    gate.partition.member = 2
    doc.gates = [gate]
    overlay = project_gates(
        doc,
        doc.samples[0],
        "X",
        "Y",
        gate.dimensions[0].transform,
        gate.dimensions[1].transform,
        None,
        limits=[-1e308, 1e308, -1, 1],
    )[0]
    assert overlay["kind"] == "spider"
    assert np.isfinite(overlay["segments"]).all()
    np.testing.assert_equal(overlay["segments"][0][0], [-1e308, 0])
    np.testing.assert_equal(overlay["segments"][0][-1], [1e308, 0])


def test_ordered_duplicate_labels_preserve_fixed_ratio_and_transformed_membership(client):
    from cytoforge.partitions import expand_partition

    doc, gate, raw = fixture(client)
    matrix = Compensation(
        name="Fixed spider basis", detectors=["X", "Y"], outputs=["X", "Y"], matrix=[[1, 0], [0, 2]]
    )
    doc.compensations = [matrix]
    gate.dimensions[0].compensation_ref = "uncompensated"
    gate.dimensions[0].transform.kind = "asinh"
    gate.dimensions[0].transform.cofactor = 1
    gate.dimensions[1] = GateDimension(
        channel="X", ratio_channels=("Y", "X"), compensation_ref=matrix.id
    )
    gate.spider = SpiderGeometry(center=(0.1, -0.2), scale=(1, 2), angles=(0.3, 1.8, 3.2, 4.8))
    doc.gates = expand_partition(gate)
    doc = Workspace.model_validate(doc.model_dump())
    with np.errstate(divide="ignore", invalid="ignore"):
        x, y = np.arcsinh(raw[:, 0]), raw[:, 1] / (2 * raw[:, 0])
    finite = np.isfinite(raw).all(axis=1) & np.isfinite(x) & np.isfinite(y)
    angle = np.mod(np.arctan2((y + 0.2) / 2, x - 0.1) - 0.3, math.tau)
    offsets = np.mod(np.array(gate.spider.angles) - 0.3, math.tau)
    expected = np.zeros(len(raw), dtype=np.uint8)
    expected[finite] = np.array([2, 1, 4, 3])[
        np.searchsorted(offsets, angle[finite], side="right") - 1
    ]
    for member in doc.gates:
        np.testing.assert_array_equal(
            client.app.state.engine.mask(doc, doc.samples[0], member.id),
            expected == member.partition.member,
        )


def test_population_exports_use_every_selected_source_event(client):
    import csv
    import io

    import flowio

    doc, gate, raw = fixture(client)
    doc = Workspace.model_validate(
        client.post(
            f"/api/workspaces/{doc.id}/gates", json={"revision": 0, "gate": gate.model_dump()}
        ).json()
    )
    expected = raw[(raw[:, 0] < 0) & (raw[:, 1] >= 0) & np.isfinite(raw).all(axis=1)]
    route = f"/api/workspaces/{doc.id}/samples/{doc.samples[0].id}/export?gate_id={gate.id}"
    exported = client.get(route + "&format=csv")
    assert exported.status_code == 200, exported.text
    rows = list(csv.reader(io.StringIO(exported.text)))
    np.testing.assert_array_equal(np.asarray(rows[1:], dtype=float), expected)
    exported = client.get(route + "&format=fcs")
    assert exported.status_code == 200
    parsed = flowio.FlowData(io.BytesIO(exported.content))
    np.testing.assert_array_equal(np.asarray(parsed.events).reshape(-1, 2), expected)
