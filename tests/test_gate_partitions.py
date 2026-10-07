"""Independent exhaustive partition labels, atomic edits and portable relationships."""

import numpy as np
import pytest
from cytoforge.gatingml import export_gatingml
from cytoforge.interchange import ImportApply, ImportMapping, apply_document, parse_document
from cytoforge.models import (
    Channel,
    Compensation,
    Gate,
    GateDimension,
    GatePartition,
    Sample,
    Workspace,
    new_id,
)
from cytoforge.partitions import expand_partition
from cytoforge.science import save_events
from pydantic import ValidationError


def acquisition(client):
    store = client.app.state.store
    values = np.array(
        [
            [-2, -2],
            [-1, 1],
            [0, 0],
            [0, 1],
            [1, 0],
            [1, -1],
            [2, 2],
            [np.nan, 1],
            [1, np.nan],
            [np.inf, 2],
            [-np.inf, 2],
            [2, np.inf],
        ],
        dtype=float,
    )
    samples = [
        Sample(name=name, channels=[Channel(name="X"), Channel(name="Y")], event_count=len(values))
        for name in ["Source", "Target one", "Target two"]
    ]
    doc = Workspace(name="Linked native partitions", samples=samples)
    for sample in samples:
        sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    return store.create(doc), values


def template(doc, kind="bisector"):
    dims = [GateDimension(channel="X", compensation_ref="uncompensated", maximum=0)]
    if kind == "quadrant":
        dims.append(GateDimension(channel="Y", compensation_ref="uncompensated", minimum=0))
    return Gate(
        name="Partition",
        sample_id=doc.samples[0].id,
        kind="hyperrectangle",
        dimensions=dims,
        partition=GatePartition(kind=kind, member=1),
    )


def create(client, doc, gate):
    response = client.post(
        f"/api/workspaces/{doc.id}/gates",
        json={"revision": doc.revision, "gate": gate.model_dump()},
    )
    assert response.status_code == 200, response.text
    return Workspace.model_validate(response.json())


def edit(client, doc, gate):
    return client.put(
        f"/api/workspaces/{doc.id}/gates/{gate.id}",
        json={"revision": doc.revision, "gate": gate.model_dump()},
    )


def masks(client, doc, gates):
    engine = client.app.state.engine
    sample = next(s for s in doc.samples if s.id == gates[0].sample_id)
    return [engine.mask(doc, sample, gate.id) for gate in gates]


def independent_labels(kind, x, y=None, thresholds=(0, 0)):
    finite = np.isfinite(x)
    right = x >= thresholds[0]
    if kind == "bisector":
        return [finite & ~right, finite & right]
    finite &= np.isfinite(y)
    upper = y >= thresholds[1]
    return [
        finite & ~right & upper,
        finite & right & upper,
        finite & right & ~upper,
        finite & ~right & ~upper,
    ]


@pytest.mark.parametrize("kind", ["bisector", "quadrant"])
def test_creation_exact_partition_and_preview_never_mutates_or_pollutes_caches(client, kind):
    doc, values = acquisition(client)
    gate = template(doc, kind)
    store = client.app.state.store
    before = doc.model_dump_json(), store.history(doc.id)
    response = client.post(
        f"/api/workspaces/{doc.id}/gates/preview-shape",
        json={"revision": 0, "gate": gate.model_dump()},
    )
    assert response.status_code == 200, response.text
    expected = independent_labels(kind, values[:, 0], values[:, 1])
    assert [c["count"] for c in response.json()["partition_counts"]] == [
        int(e.sum()) for e in expected
    ]
    assert (store.get(doc.id).model_dump_json(), store.history(doc.id)) == before
    saved = create(client, doc, gate)
    assert saved.revision == 1
    assert saved.gates[0].id == gate.id
    assert len({g.id for g in saved.gates}) == len(expected)
    assert {g.partition.id for g in saved.gates} == {gate.partition.id}
    actual = masks(client, saved, saved.gates)
    np.testing.assert_array_equal(actual, expected)
    np.testing.assert_array_equal(np.sum(actual, axis=0), np.sum(expected, axis=0))
    assert np.max(np.sum(actual, axis=0)) == 1
    warm = [a.copy() for a in actual]
    selected = saved.gates[-1].model_copy(deep=True)
    selected.dimensions[0].minimum = 1 if selected.partition.high(0) else None
    selected.dimensions[0].maximum = None if selected.partition.high(0) else 1
    response = client.post(
        f"/api/workspaces/{doc.id}/gates/preview-shape",
        json={"revision": saved.revision, "gate": selected.model_dump()},
    )
    assert response.status_code == 200, response.text
    new_labels = independent_labels(kind, values[:, 0], values[:, 1], (1, 0))
    assert [c["count"] for c in response.json()["partition_counts"]] == [
        int(e.sum()) for e in new_labels
    ]
    np.testing.assert_array_equal(masks(client, saved, saved.gates), warm)
    assert store.get(doc.id).model_dump_json() == saved.model_dump_json()


@pytest.mark.parametrize("kind", ["bisector", "quadrant"])
def test_shared_geometry_edit_children_boolean_undo_redo_and_stale_rejection(client, kind):
    doc, values = acquisition(client)
    doc = create(client, doc, template(doc, kind))
    family_ids = [g.id for g in doc.gates]
    child = Gate(
        name="Positive child",
        sample_id=doc.samples[0].id,
        parent_id=doc.gates[1].id,
        kind="range",
        x="X",
        bounds=[0, 10],
    )
    doc = create(client, doc, child)
    union = Gate(
        name="Family union",
        sample_id=doc.samples[0].id,
        kind="boolean",
        operation="or",
        operands=family_ids,
    )
    doc = create(client, doc, union)
    original_names = {g.id: g.name for g in doc.gates}
    selected = doc.gates[1].model_copy(deep=True)
    selected.name = "Reviewed positive"
    selected.dimensions[0].minimum = 1
    response = edit(client, doc, selected)
    assert response.status_code == 200, response.text
    saved = Workspace.model_validate(response.json())
    assert saved.revision == doc.revision + 1
    expected = independent_labels(kind, values[:, 0], values[:, 1], (1, 0))
    np.testing.assert_array_equal(masks(client, saved, saved.gates[: len(family_ids)]), expected)
    np.testing.assert_array_equal(masks(client, saved, [saved.gates[-2]])[0], expected[1])
    np.testing.assert_array_equal(
        masks(client, saved, [saved.gates[-1]])[0], np.any(expected, axis=0)
    )
    assert all(
        g.name == (selected.name if g.id == selected.id else original_names[g.id])
        for g in saved.gates
    )
    history = client.app.state.store.history(doc.id)
    assert edit(client, doc, selected).status_code == 409
    assert client.app.state.store.history(doc.id) == history
    undo = client.post(f"/api/workspaces/{doc.id}/undo", json={"revision": saved.revision})
    assert undo.status_code == 200, undo.text
    undone = Workspace.model_validate(undo.json())
    assert [g.model_dump() for g in undone.gates] == [g.model_dump() for g in doc.gates]
    redo = client.post(f"/api/workspaces/{doc.id}/redo", json={"revision": undone.revision})
    assert redo.status_code == 200, redo.text
    assert redo.json()["gates"] == [g.model_dump(mode="json") for g in saved.gates]


@pytest.mark.parametrize(
    "invalid",
    ["missing", "duplicate", "threshold", "coordinates", "parent", "sample", "membership"],
)
def test_workspace_rejects_incomplete_or_inconsistent_families(client, invalid):
    doc, _ = acquisition(client)
    members = expand_partition(template(doc, "quadrant"))
    if invalid == "missing":
        members.pop()
    elif invalid == "duplicate":
        members[-1].partition.member = 1
        members[-1].dimensions = members[0].dimensions
    elif invalid == "threshold":
        members[1].dimensions[0].minimum = 1
    elif invalid == "coordinates":
        members[1].dimensions[0].transform.kind = "asinh"
    elif invalid == "parent":
        members[1].parent_id = members[0].id
    elif invalid == "sample":
        members[1].sample_id = doc.samples[1].id
    else:
        members[1].partition.kind = "bisector"
    with pytest.raises(ValidationError):
        Workspace.model_validate({**doc.model_dump(), "gates": [g.model_dump() for g in members]})


def test_link_membership_cannot_be_removed_or_replaced_and_invalid_edits_are_atomic(client):
    doc, _ = acquisition(client)
    doc = create(client, doc, template(doc))
    store = client.app.state.store
    before = doc.model_dump_json(), store.history(doc.id)
    gate = doc.gates[0].model_copy(update={"partition": None}, deep=True)
    assert edit(client, doc, gate).status_code == 422
    gate = doc.gates[0].model_copy(deep=True)
    gate.parent_id = doc.gates[1].id
    assert edit(client, doc, gate).status_code == 422
    assert (store.get(doc.id).model_dump_json(), store.history(doc.id)) == before
    assert (
        "partition"
        not in Gate(
            name="Legacy", sample_id=doc.samples[0].id, kind="range", x="X", bounds=[0, 1]
        ).model_dump()
    )


@pytest.mark.parametrize("kind", ["bisector", "quadrant"])
def test_propagation_gives_each_sample_an_independent_family_and_deletion_is_whole_family(
    client, kind
):
    doc, values = acquisition(client)
    doc = create(client, doc, template(doc, kind))
    source_id = doc.samples[0].id
    targets = [s.id for s in doc.samples[1:]]
    response = client.post(
        f"/api/workspaces/{doc.id}/gates/apply",
        json={
            "revision": doc.revision,
            "source_sample_id": source_id,
            "target_sample_ids": targets,
        },
    )
    assert response.status_code == 200, response.text
    doc = Workspace.model_validate(response.json())
    families = [[g for g in doc.gates if g.sample_id == s.id] for s in doc.samples]
    assert len({gates[0].partition.id for gates in families}) == 3
    expected = independent_labels(kind, values[:, 0], values[:, 1])
    for family in families:
        np.testing.assert_array_equal(masks(client, doc, family), expected)
    selected = families[1][0].model_copy(deep=True)
    selected.dimensions[0].maximum = 1
    response = edit(client, doc, selected)
    assert response.status_code == 200, response.text
    doc = Workspace.model_validate(response.json())
    np.testing.assert_array_equal(masks(client, doc, families[0]), expected)
    np.testing.assert_array_equal(masks(client, doc, families[2]), expected)
    child = Gate(
        name="Sibling child",
        sample_id=targets[0],
        parent_id=families[1][1].id,
        kind="range",
        x="X",
        bounds=[-10, 10],
    )
    doc = create(client, doc, child)
    boolean = Gate(
        name="Dependent",
        sample_id=targets[0],
        kind="boolean",
        operation="not",
        operands=[families[1][-1].id],
    )
    doc = create(client, doc, boolean)
    response = client.delete(
        f"/api/workspaces/{doc.id}/gates/{selected.id}?revision={doc.revision}"
    )
    assert response.status_code == 200, response.text
    assert not any(g["sample_id"] == targets[0] for g in response.json()["gates"])
    assert len(response.json()["gates"]) == 2 * len(expected)
    undo = client.post(
        f"/api/workspaces/{doc.id}/undo", json={"revision": response.json()["revision"]}
    )
    assert undo.status_code == 200
    assert undo.json()["gates"] == [g.model_dump(mode="json") for g in doc.gates]


@pytest.mark.parametrize("kind", ["bisector", "quadrant"])
def test_fixed_compensation_ratio_transform_gatingml_links_and_import_remapping(client, kind):
    doc, values = acquisition(client)
    matrix = Compensation(
        name="Fixed definition", detectors=["X", "Y"], outputs=["X", "Y"], matrix=[[1, 0], [0, 2]]
    )
    doc.compensations = [matrix]
    gate = template(doc, kind)
    gate.dimensions[0].compensation_ref = matrix.id
    gate.dimensions[0].transform.kind = "asinh"
    gate.dimensions[0].transform.cofactor = 1
    if kind == "quadrant":
        gate.dimensions[1] = GateDimension(
            channel="Ratio", ratio_channels=("X", "Y"), compensation_ref=matrix.id, minimum=0.5
        )
    doc.gates = expand_partition(gate)
    doc = Workspace.model_validate(doc.model_dump())
    with np.errstate(divide="ignore", invalid="ignore"):
        expected = independent_labels(
            kind, np.arcsinh(values[:, 0]), values[:, 0] / (2 * values[:, 1]), thresholds=(0, 0.5)
        )
    # Solving a compensation matrix requires every input detector to be finite,
    # including detectors outside the plotted axis. The unmixing suite verifies
    # this policy independently; raw bisectors above intentionally use X only.
    expected = [mask & np.isfinite(values).all(axis=1) for mask in expected]
    np.testing.assert_array_equal(masks(client, doc, doc.gates), expected)
    xml = export_gatingml(doc, doc.samples[0])
    plan = parse_document(xml, "linked.xml")
    assert not plan.issues
    source = plan.sources[0]
    assert len(source.gates) == len(expected)
    assert {g.partition.id for g in source.gates} == {gate.partition.id}
    request = ImportApply(
        revision=doc.revision,
        preview_id=new_id(),
        mappings=[ImportMapping(source_id=source.id, sample_ids=[s.id for s in doc.samples[1:]])],
    )
    engine = client.app.state.engine
    apply_document(doc, plan, request, engine)
    families = [[g for g in doc.gates if g.sample_id == s.id] for s in doc.samples]
    assert len({family[0].partition.id for family in families}) == 3
    for family in families:
        np.testing.assert_array_equal(masks(client, doc, family), expected)
