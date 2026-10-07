"""Captured event identities, control reuse, integrity and portable lifecycle."""

import io
import zipfile

import numpy as np
import pytest
from cytoforge.models import Channel, Gate, Sample, Transform, Workspace
from cytoforge.population_snapshot import capture, digest, load
from cytoforge.science import Engine, save_events

from tests.test_analysis import _apply, _request, _submit, _wait, _workspace


def snapshot(client, doc, gate_id=None, sample_id=None, **changes):
    response = client.post(
        f"/api/workspaces/{doc['id']}/gates/capture",
        json=dict(
            revision=doc["revision"],
            name="Captured population",
            sample_id=sample_id or doc["samples"][0]["id"],
            gate_id=gate_id,
        )
        | changes,
    )
    assert response.status_code == 200, response.text
    return response.json()


def source_population(client, count=13):
    values = np.column_stack([np.arange(count), np.arange(count) * 2, np.ones(count)])
    doc = _workspace(client, [values])
    gate = Gate(
        sample_id=doc["samples"][0]["id"],
        name="Source",
        kind="range",
        x="X",
        x_transform=Transform(kind="linear"),
        bounds=[3, 9.1],
    )
    response = client.post(
        f"/api/workspaces/{doc['id']}/gates",
        json={"revision": doc["revision"], "gate": gate.model_dump()},
    )
    assert response.status_code == 200, response.text
    return response.json(), gate, values


def test_capture_preserves_selection_after_source_edit_delete_archive_and_history(client):
    doc, source, values = source_population(client)
    original = Workspace.model_validate(doc)
    doc = snapshot(client, doc, source.id)
    gate = doc["gates"][-1]
    assert gate["kind"] == "membership" and gate["parent_id"] is None
    assert gate["membership"]["selected_count"] == 7
    store = client.app.state.store
    engine = Engine(store)
    workspace = Workspace.model_validate(doc)
    expected = (values[:, 0] >= 3) & (values[:, 0] <= 9.1)
    np.testing.assert_array_equal(
        engine.mask(workspace, workspace.samples[0], gate["id"]), expected
    )
    source.bounds = [0, 2]
    changed = client.put(
        f"/api/workspaces/{doc['id']}/gates/{source.id}",
        json={"revision": doc["revision"], "gate": source.model_dump()},
    )
    assert changed.status_code == 200, changed.text
    doc = changed.json()
    removed = client.delete(
        f"/api/workspaces/{doc['id']}/gates/{source.id}?revision={doc['revision']}"
    )
    assert removed.status_code == 200, removed.text
    doc = removed.json()
    workspace = Workspace.model_validate(doc)
    np.testing.assert_array_equal(
        engine.mask(workspace, workspace.samples[0], gate["id"]), expected
    )
    archive = client.get(f"/api/workspaces/{doc['id']}/export/project")
    assert archive.status_code == 200, archive.text
    reopened = client.post(
        "/api/import/project", files={"file": ("captured.cytoforge", archive.content)}
    )
    assert reopened.status_code == 200, reopened.text
    reopened_doc = Workspace.model_validate(reopened.json())
    np.testing.assert_array_equal(
        engine.mask(reopened_doc, reopened_doc.samples[0], gate["id"]), expected
    )
    assert (
        digest(store.data_path(original.id, original.samples[0].id)) == original.samples[0].sha256
    )
    # Remove and undo the snapshot itself; history retains its physical membership data.
    removed = client.delete(
        f"/api/workspaces/{doc['id']}/gates/{gate['id']}?revision={doc['revision']}"
    )
    assert removed.status_code == 200
    undone = client.post(
        f"/api/workspaces/{doc['id']}/undo",
        json={"revision": removed.json()["revision"]},
    )
    assert undone.status_code == 200, undone.text
    restored = Workspace.model_validate(undone.json())
    np.testing.assert_array_equal(engine.mask(restored, restored.samples[0], gate["id"]), expected)


@pytest.mark.parametrize("dependency", ["direct", "child", "boolean"])
@pytest.mark.parametrize("corruption", ["membership", "raw"])
def test_cached_masks_reject_changed_membership_or_acquisition_through_dependencies(
    client, dependency, corruption
):
    doc, source, _ = source_population(client)
    doc = snapshot(client, doc, source.id)
    workspace = Workspace.model_validate(doc)
    member = workspace.gates[-1]
    target = member
    if dependency == "child":
        target = Gate(
            sample_id=member.sample_id,
            name="Child",
            parent_id=member.id,
            kind="range",
            x="X",
            x_transform=Transform(kind="linear"),
            bounds=[4, 6],
        )
    elif dependency == "boolean":
        target = Gate(
            sample_id=member.sample_id, name="Boolean", kind="boolean", operands=[member.id]
        )
    if target is not member:
        workspace.gates.append(target)
    store = client.app.state.store
    engine = Engine(store)
    engine.mask(workspace, workspace.samples[0], target.id)
    path = (
        store.membership_path(workspace.id, member.membership.id)
        if corruption == "membership"
        else store.data_path(workspace.id, member.sample_id)
    )
    array = np.load(path, allow_pickle=False).copy()
    if corruption == "membership":
        array[0] ^= np.uint8(1)
    else:
        array[0, 0] += 1
    np.save(path, array, allow_pickle=False)
    with pytest.raises(ValueError, match="integrity check"):
        engine.mask(workspace, workspace.samples[0], target.id)


@pytest.mark.parametrize("malformed", ["dtype", "shape", "padding", "count", "trailing"])
def test_valid_hash_cannot_hide_malformed_packed_membership(client, malformed):
    doc, source, _ = source_population(client)
    doc = snapshot(client, doc, source.id)
    workspace = Workspace.model_validate(doc)
    data = workspace.gates[-1].membership
    engine = Engine(client.app.state.store)
    path = engine.store.membership_path(workspace.id, data.id)
    packed = np.load(path, allow_pickle=False).copy()
    if malformed == "dtype":
        packed = packed.astype(np.int8)
    elif malformed == "shape":
        packed = packed[None, :]
    elif malformed == "padding":
        packed[-1] |= np.uint8(1 << (data.event_count % 8))
    elif malformed == "count":
        data.selected_count += 1
    np.save(path, packed, allow_pickle=False)
    if malformed == "trailing":
        with path.open("ab") as stream:
            stream.write(b"unexpected tail")
    data.sha256 = digest(path)
    with pytest.raises(ValueError):
        load(engine, workspace, workspace.samples[0], data)


def test_pheno_communities_can_be_captured_and_reused_as_multiple_af_controls(client):
    rng = np.random.default_rng(3047)
    centers = np.array([[100, 10, 5], [10, 100, 5], [10, 20, 100]])
    values = centers[np.repeat(np.arange(3), 40)] + rng.normal(0, 0.02, (120, 3))
    doc = _workspace(client, [values, np.zeros((40, 3))])
    source, negative = doc["samples"]
    body = _request(doc) | dict(
        algorithm="phenograph",
        compensated=False,
        inputs=[{"sample_id": source["id"]}],
        n_neighbors=30,
    )
    job_id = _submit(client, doc, body)
    assert _wait(client, doc, job_id)["status"] == "succeeded"
    doc = _apply(client, doc, job_id)
    communities = [
        g
        for g in doc["gates"]
        if g.get("provenance", {}).get("analysis_id") == job_id
        and g.get("provenance", {}).get("community")
    ]
    assert len(communities) == 3
    stored = client.app.state.store
    engine = Engine(stored)
    captured = {}
    for community in communities:
        current = Workspace.model_validate(doc)
        selected = engine.mask(current, current.samples[0], community["id"])
        label = int(np.argmax(values[selected].mean(axis=0)))
        doc = snapshot(client, doc, community["id"], name=f"Reference {label}")
        captured[label] = doc["gates"][-1]["id"]
    request = dict(
        revision=doc["revision"],
        name="AF from discovered populations",
        kind="spectral",
        detectors=["Z", "X", "Y"],
        min_events=20,
        controls=[
            dict(
                name="F",
                primary_detector="X",
                positive=dict(sample_id=source["id"], gate_id=captured[0]),
                negative=dict(sample_id=negative["id"]),
            )
        ],
        autofluorescence_controls=[
            dict(name=f"AF{i}", population=dict(sample_id=source["id"], gate_id=captured[i]))
            for i in (1, 2)
        ],
    )
    root = f"/api/workspaces/{doc['id']}"
    result = client.post(root + "/compensations/calculate", json=request)
    assert result.status_code == 200, result.text
    matrix = result.json()["compensation"]
    saved = client.post(
        root + "/compensations",
        json=dict(
            revision=doc["revision"],
            compensation=matrix,
            sample_ids=[source["id"]],
            save_control_populations=True,
        ),
    )
    assert saved.status_code == 200, saved.text
    current = Workspace.model_validate(saved.json())
    for label, identifier in captured.items():
        flags = engine.mask(current, current.samples[0], identifier)
        assert flags.sum() == 40
        assert np.argmax(values[flags].mean(0)) == label
    # A completed preview cannot authorize reuse after its membership file changes.
    from cytoforge.compensation import save_control_populations
    from cytoforge.models import Compensation

    data = next(g.membership for g in current.gates if g.id == captured[0])
    path = stored.membership_path(current.id, data.id)
    packed = np.load(path, allow_pickle=False).copy()
    packed[0] ^= np.uint8(1)
    np.save(path, packed, allow_pickle=False)
    with pytest.raises(ValueError, match="integrity check"):
        save_control_populations(current, Compensation.model_validate(matrix), stored)


@pytest.mark.parametrize("bad", ["foreign_gate", "revision"])
def test_capture_rejects_foreign_population_and_stale_revision_without_creating_files(client, bad):
    doc, gate, values = source_population(client)
    if bad == "foreign_gate":
        doc = client.post(
            f"/api/workspaces/{doc['id']}/import?revision={doc['revision']}",
            files={"files": ("other.csv", "X,Y,Z\n1,2,3\n", "text/csv")},
        ).json()["workspace"]
    store = client.app.state.store
    before = {p.name for p in (store.root / "memberships" / doc["id"]).glob("*")}
    response = client.post(
        f"/api/workspaces/{doc['id']}/gates/capture",
        json=dict(
            revision=doc["revision"] - (bad == "revision"),
            name="Rejected capture",
            sample_id=doc["samples"][-1]["id"],
            gate_id=gate.id,
        ),
    )
    assert response.status_code in (409, 422), response.text
    assert {p.name for p in (store.root / "memberships" / doc["id"]).glob("*")} == before


def test_portable_snapshot_rejects_altered_bytes_and_releases_owned_import_files(client):
    doc, source, _ = source_population(client)
    doc = snapshot(client, doc, source.id)
    archive = client.get(f"/api/workspaces/{doc['id']}/export/project")
    data = doc["gates"][-1]["membership"]
    entry = f"memberships/{data['id']}.npy"
    store = client.app.state.store
    before = set((store.root / "memberships").rglob("*.npy"))
    changed = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(archive.content)) as original,
        zipfile.ZipFile(changed, "w") as output,
    ):
        for item in original.infolist():
            content = original.read(item.filename)
            if item.filename == entry:
                content = content[:-1] + bytes([content[-1] ^ 1])
            output.writestr(item.filename, content)
    response = client.post(
        "/api/import/project", files={"file": ("corrupted.cytoforge", changed.getvalue())}
    )
    assert response.status_code == 422, response.text
    assert set((store.root / "memberships").rglob("*.npy")) == before


def test_legacy_gate_serialization_and_snapshot_copy_binding_remain_explicit(client):
    doc, source, _ = source_population(client)
    assert "membership" not in source.model_dump()
    doc = snapshot(client, doc, source.id)
    captured = Gate.model_validate(doc["gates"][-1])
    altered = captured.model_dump()
    altered["membership"]["acquisition_channels"].reverse()
    with pytest.raises(ValueError, match="acquired sample"):
        Workspace.model_validate(doc | {"gates": [altered]})


@pytest.mark.parametrize("count", [0, 1, 7, 8, 9])
def test_snapshot_bit_boundaries_and_empty_acquisitions(store, count):
    doc = Workspace(name="Bit boundaries")
    sample = Sample(
        name="Cells", event_count=count, channels=[Channel(name="X"), Channel(name="Y")]
    )
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), np.zeros((count, 2)))
    doc.samples.append(sample)
    engine = Engine(store)
    gate = capture(doc, sample.id, None, "All captured events", True, engine)
    doc.gates.append(gate)
    np.testing.assert_array_equal(engine.mask(doc, sample, gate.id), np.ones(count, dtype=bool))
    assert gate.membership.selected_count == count
    packed = np.load(store.membership_path(doc.id, gate.membership.id), allow_pickle=False)
    assert packed.shape == ((count + 7) // 8,)


def test_autospill_revalidates_captured_control_data_before_apply(client):
    from cytoforge.autospill import AutoSpillRequest, verify_control_data

    doc, source, _ = source_population(client)
    doc = snapshot(client, doc, source.id)
    workspace = Workspace.model_validate(doc)
    member = workspace.gates[-1]
    request = AutoSpillRequest(
        revision=workspace.revision,
        detectors=["X", "Y", "Z"],
        scatter_x="X",
        scatter_y="Y",
        auto_cleanup=False,
        controls=[
            dict(name=n, primary_detector=n, sample_id=member.sample_id, gate_id=member.id)
            for n in ("X", "Y", "Z")
        ],
    )
    store = client.app.state.store
    verify_control_data(workspace, request, store)
    path = store.membership_path(workspace.id, member.membership.id)
    packed = np.load(path, allow_pickle=False).copy()
    packed[0] ^= np.uint8(1)
    np.save(path, packed, allow_pickle=False)
    with pytest.raises(ValueError, match="integrity check"):
        verify_control_data(workspace, request, store)
