import hashlib
import io
import json
import zipfile

import flowio
import numpy as np
import pytest
from cytoforge.models import Gate, Workspace
from cytoforge.store import ConflictError, Store


def create(client, name="Experiment"):
    response = client.post("/api/workspaces", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def import_csv(client, doc, content=b"X,Y\n1,2\n3,4\n5,6\n", name="cells.csv"):
    response = client.post(
        f"/api/workspaces/{doc['id']}/import?revision={doc['revision']}",
        files=[("files", (name, content, "text/csv"))],
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_security_token_origin_and_host(client):
    assert client.get("/api/workspaces", headers={"X-CytoForge-Token": "wrong"}).status_code == 401
    assert (
        client.get("/api/bootstrap", headers={"Origin": "https://untrusted.example"}).status_code
        == 403
    )
    assert client.get("/api/health", headers={"Host": "untrusted.example"}).status_code == 400
    assert (
        client.post(
            "/api/workspaces",
            json={"name": "Attack"},
            headers={"Origin": "https://untrusted.example"},
        ).status_code
        == 403
    )


def test_import_partial_success_duplicate_and_error_cleanup(client):
    doc = create(client)
    response = client.post(
        f"/api/workspaces/{doc['id']}/import?revision=0",
        files=[
            ("files", ("good.csv", b"X,Y\n1,2\n3,4\n", "text/csv")),
            ("files", ("bad.csv", b"X,Y\n1,wrong\n", "text/csv")),
            ("files", ("malformed.fcs", b"corrupt", "application/octet-stream")),
        ],
    )
    result = response.json()
    assert result["imported"] == 1 and len(result["errors"]) == 2
    assert not list((client.app.state.store.root / "tmp").iterdir())
    duplicate = import_csv(client, result["workspace"], b"X,Y\n1,2\n3,4\n", "renamed.csv")
    assert duplicate["imported"] == 0 and len(duplicate["warnings"]) == 1
    assert len(duplicate["workspace"]["samples"]) == 1


def test_fcs_acquisition_matrix_and_gain(client):
    doc = create(client)
    buffer = io.BytesIO()
    original = np.array([[10, 20], [30, 40]], float)
    spill = np.array([[1, 0.2], [0.1, 1]])
    flowio.create_fcs(
        buffer,
        (original @ spill).ravel().tolist(),
        ["X", "Y"],
        metadata_dict={"spillover": "2,X,Y,1,0.2,0.1,1"},
    )
    response = client.post(
        f"/api/workspaces/{doc['id']}/import?revision=0",
        files=[("files", ("acquisition.fcs", buffer.getvalue(), "application/octet-stream"))],
    )
    result = response.json()
    assert result["imported"] == 1, result
    updated = result["workspace"]
    sample = updated["samples"][0]
    assert sample["compensation_id"] == updated["compensations"][0]["id"]
    csv_result = client.get(f"/api/workspaces/{doc['id']}/samples/{sample['id']}/export?format=csv")
    np.testing.assert_allclose(
        np.loadtxt(io.StringIO(csv_result.text), delimiter=",", skiprows=1), original, atol=1e-5
    )


def test_complete_gate_edit_export_undo_redo_restore_flow(client):
    doc = import_csv(client, create(client))["workspace"]
    sample = doc["samples"][0]
    gate = Gate(
        sample_id=sample["id"], name="Selected", kind="rectangle", x="X", y="Y", bounds=[0, 4, 0, 5]
    ).model_dump()
    created = client.post(
        f"/api/workspaces/{doc['id']}/gates", json={"revision": doc["revision"], "gate": gate}
    )
    assert created.status_code == 200, created.text
    doc = created.json()
    counts = client.get(f"/api/workspaces/{doc['id']}/samples/{sample['id']}/counts").json()
    assert counts[0]["count"] == 2 and counts[0]["percent_total"] == pytest.approx(200 / 3)
    population = client.get(
        f"/api/workspaces/{doc['id']}/samples/{sample['id']}/export?gate_id={gate['id']}&format=fcs"
    )
    parsed = flowio.FlowData(io.BytesIO(population.content))
    assert parsed.event_count == 2
    wrong = client.post(
        f"/api/workspaces/{doc['id']}/gates",
        json={"revision": doc["revision"] - 1, "gate": gate | {"name": "Conflict"}},
    )
    assert wrong.status_code == 409
    undo = client.post(
        f"/api/workspaces/{doc['id']}/undo", json={"revision": doc["revision"]}
    ).json()
    assert undo["gates"] == [] and undo["revision"] > doc["revision"]
    redo = client.post(
        f"/api/workspaces/{doc['id']}/redo", json={"revision": undo["revision"]}
    ).json()
    assert len(redo["gates"]) == 1 and redo["revision"] > undo["revision"]
    archive = client.get(f"/api/workspaces/{doc['id']}/export/project")
    assert archive.status_code == 200
    restored = client.post(
        "/api/import/project",
        files={"file": ("project.cytoforge", archive.content, "application/zip")},
    )
    assert restored.status_code == 200, restored.text
    copy = restored.json()
    assert copy["id"] != doc["id"] and copy["samples"][0]["sha256"] == sample["sha256"]
    assert (
        client.get(f"/api/workspaces/{copy['id']}/samples/{sample['id']}/counts").json()[0]["count"]
        == 2
    )


def test_persistent_history_branches_and_atomic_failure(tmp_path):
    root = tmp_path / "history"
    store = Store(root)
    doc = store.create(Workspace(name="Original"))
    edited = store.mutate(doc.id, "Rename", lambda w: setattr(w, "name", "Changed"), 0)
    store.close()
    store = Store(root)
    assert store.get(doc.id).name == "Changed"
    undone = store.move_history(doc.id, -1, edited.revision)
    assert undone.name == "Original"
    with pytest.raises(ConflictError):
        store.mutate(doc.id, "Conflict", lambda w: setattr(w, "name", "Bad"), edited.revision)
    with pytest.raises(ValueError):
        store.mutate(doc.id, "Invalid", lambda w: setattr(w, "name", ""), undone.revision)
    assert store.get(doc.id).revision == undone.revision
    branched = store.mutate(
        doc.id, "Branch", lambda w: setattr(w, "name", "Branch"), undone.revision
    )
    assert branched.name == "Branch" and not store.history(doc.id)["can_redo"]
    store.close()


def test_incompatible_batch_gate_propagation_is_atomic(client):
    doc = import_csv(client, create(client))["workspace"]
    doc = import_csv(client, doc, b"A,B\n1,2\n", "different-panel.csv")["workspace"]
    first = doc["samples"][0]
    gate = Gate(
        sample_id=first["id"], name="Parent", kind="range", x="X", bounds=[0, 4]
    ).model_dump()
    doc = client.post(
        f"/api/workspaces/{doc['id']}/gates", json={"revision": doc["revision"], "gate": gate}
    ).json()
    response = client.post(
        f"/api/workspaces/{doc['id']}/gates/apply",
        json={
            "revision": doc["revision"],
            "source_sample_id": first["id"],
            "target_sample_ids": [doc["samples"][1]["id"]],
            "replace": True,
        },
    )
    assert response.status_code == 422
    after = client.get(f"/api/workspaces/{doc['id']}").json()
    assert after["revision"] == doc["revision"] and len(after["gates"]) == 1


def test_archive_integrity_and_no_traversal(client):
    doc = import_csv(client, create(client))["workspace"]
    archive = client.get(f"/api/workspaces/{doc['id']}/export/project").content
    source, destination = io.BytesIO(archive), io.BytesIO()
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(destination, "w") as modified:
        for item in original.infolist():
            content = original.read(item.filename)
            if item.filename.endswith(".npy"):
                content = content[:-1] + bytes([content[-1] ^ 1])
            modified.writestr(item.filename, content)
        modified.writestr("../../escape", b"must not extract")
    response = client.post(
        "/api/import/project",
        files={"file": ("tampered.cytoforge", destination.getvalue(), "application/zip")},
    )
    assert response.status_code == 422 and "Integrity" in response.text
    assert len(client.get("/api/workspaces").json()) == 1


def test_empty_import_and_plot(client):
    doc = import_csv(client, create(client), b"X,Y\n", "empty.csv")["workspace"]
    sample = doc["samples"][0]
    response = client.get(f"/api/workspaces/{doc['id']}/samples/{sample['id']}/plot?x=X&y=Y")
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 0
    table = client.get(f"/api/workspaces/{doc['id']}/statistics?channel=X").json()
    assert table[0]["percent_total"] is None and table[0]["median"] is None


def test_archive_manifest_hashes_match_raw_data(client):
    doc = import_csv(client, create(client))["workspace"]
    archive = client.get(f"/api/workspaces/{doc['id']}/export/project")
    with zipfile.ZipFile(io.BytesIO(archive.content)) as container:
        manifest = json.loads(container.read("manifest.json"))
        for sample in manifest["workspace"]["samples"]:
            assert (
                hashlib.sha256(container.read(f"events/{sample['id']}.npy")).hexdigest()
                == sample["sha256"]
            )


def test_table_metadata_cannot_override_population_counts(client):
    doc = import_csv(client, create(client))["workspace"]
    sample = doc["samples"][0]
    response = client.patch(
        f"/api/workspaces/{doc['id']}/samples/{sample['id']}",
        json={
            "revision": doc["revision"],
            "name": sample["name"],
            "tags": {"count": "wrong", "=formula": "=2+2"},
        },
    )
    assert response.status_code == 200
    rows = client.get(f"/api/workspaces/{doc['id']}/statistics").json()
    assert rows[0]["count"] == 3 and rows[0]["metadata:count"] == "wrong"
    exported = client.get(f"/api/workspaces/{doc['id']}/export/statistics.csv").text
    assert "'=2+2" in exported


def test_invalid_report_definition_is_rejected_atomically(client):
    doc = create(client)
    response = client.post(
        f"/api/workspaces/{doc['id']}/layouts/save",
        json={"revision": 0, "definition": {"name": "Broken", "plots": [{"mode": "unsupported"}]}},
    )
    assert response.status_code == 422
    assert client.get(f"/api/workspaces/{doc['id']}").json()["revision"] == 0
