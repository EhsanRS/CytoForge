"""Read-only export lifetime, saved file checks, cancellation and import publication."""

import hashlib
import io
import json
import threading
import time

import numpy as np
import pytest
from cytoforge import event_exports
from test_concatenation import apply, body, experiment, start, wait


def prepare(client, doc, **changes):
    request = dict(revision=doc.revision, sample_id=doc.samples[0].id)
    request.update(changes)
    response = client.post(f"/api/workspaces/{doc.id}/event-exports", json=request)
    assert response.status_code == 200, response.text
    return response.json()


def finish(client, doc, session):
    for _ in range(2000):
        response = client.get(f"/api/workspaces/{doc.id}/event-exports/{session['id']}")
        assert response.status_code == 200, response.text
        result = response.json()
        if result["status"] not in {"queued", "running"}:
            return result
        time.sleep(0.005)
    pytest.fail("Event export did not finish")


@pytest.mark.parametrize("format", ["fcs", "csv"])
def test_prepared_download_exact_values_without_workspace_or_history_edits(client, format):
    doc, arrays = experiment(client)
    before = doc.model_dump_json()
    session = finish(client, doc, prepare(client, doc, format=format))
    assert session["status"] == "ready", session
    assert session["can_download"] and not session["stale"]
    response = client.get(f"/api/workspaces/{doc.id}/event-exports/{session['id']}/download")
    assert response.status_code == 200, response.text
    assert hashlib.sha256(response.content).hexdigest() == session["file_sha256"]
    assert len(response.content) == session["file_bytes"]
    if format == "csv":
        values = np.loadtxt(io.BytesIO(response.content), delimiter=",", skiprows=1)
        np.testing.assert_array_equal(values, arrays[0])
    assert client.app.state.store.get(doc.id).model_dump_json() == before
    manager = client.app.state.event_exports
    assert manager.downloads[session["id"]] == 0
    path = manager.root / session["id"] / ("population." + format)
    assert path.exists()
    cancelled = client.post(f"/api/workspaces/{doc.id}/event-exports/{session['id']}/cancel").json()
    assert cancelled["status"] == "cancelled" and not path.exists()


def test_cancel_keeps_a_leased_file_until_the_reader_has_closed_it(client):
    doc, _ = experiment(client)
    session = finish(client, doc, prepare(client, doc))
    manager = client.app.state.event_exports
    path, _ = manager.download(doc.id, session["id"])
    manager.cancel(doc.id, session["id"])
    assert path.exists() and not manager.get(doc.id, session["id"])["can_download"]
    with path.open("rb") as handle:
        assert handle.read(6) == b"FCS3.1"
    manager.release(session["id"])
    assert not path.exists()
    assert manager.get(doc.id, session["id"])["status"] == "cancelled"


@pytest.mark.parametrize("damage", ["file", "input", "source"])
def test_download_rechecks_saved_bytes_and_scientific_sources(client, damage):
    doc, _ = experiment(client)
    session = finish(client, doc, prepare(client, doc))
    manager = client.app.state.event_exports
    directory = manager.root / session["id"]
    path = {
        "file": directory / "population.fcs",
        "input": directory / "input.json",
        "source": client.app.state.store.data_path(doc.id, doc.samples[0].id),
    }[damage]
    with path.open("ab") as handle:
        handle.write(b"damaged")
    response = client.get(f"/api/workspaces/{doc.id}/event-exports/{session['id']}/download")
    assert response.status_code == 422, response.text
    assert "integrity" in response.text or "snapshot changed" in response.text
    assert manager.downloads[session["id"]] == 0
    assert client.app.state.store.get(doc.id).model_dump_json() == doc.model_dump_json()


def test_stale_export_foreign_workspace_population_and_session_token_are_rejected(client):
    doc, _ = experiment(client)
    other, _ = experiment(client)
    base = f"/api/workspaces/{doc.id}/event-exports"
    response = client.post(base, json=dict(revision=doc.revision + 1, sample_id=doc.samples[0].id))
    assert response.status_code == 409
    response = client.post(
        base,
        json=dict(revision=doc.revision, sample_id=doc.samples[0].id, gate_id=other.gates[0].id),
    )
    assert response.status_code == 422
    session = finish(client, doc, prepare(client, doc))
    response = client.get(f"/api/workspaces/{other.id}/event-exports/{session['id']}")
    assert response.status_code == 404
    client.app.state.store.mutate(
        doc.id, "Rename", lambda d: setattr(d, "name", "Changed"), doc.revision
    )
    assert client.get(base + f"/{session['id']}").json()["stale"]
    assert client.get(base + f"/{session['id']}/download").status_code == 409
    assert (
        client.get(
            base + f"/{session['id']}", headers={"X-CytoForge-Token": "incorrect"}
        ).status_code
        == 401
    )


def test_chunk_cancellation_cleans_temporary_files_before_reporting_completion(client, monkeypatch):
    doc, _ = experiment(client)
    original = event_exports.write_export
    reached, resume = threading.Event(), threading.Event()

    def blocked(*args, progress, **kwargs):
        def report(**values):
            progress(**values)
            if values["stage"] == "Writing event values":
                reached.set()
                assert resume.wait(10)

        return original(*args, progress=report, **kwargs)

    monkeypatch.setattr(event_exports, "write_export", blocked)
    session = prepare(client, doc)
    assert reached.wait(10)
    response = client.post(f"/api/workspaces/{doc.id}/event-exports/{session['id']}/cancel")
    assert response.json()["cancel_requested"]
    resume.set()
    result = finish(client, doc, session)
    assert result["status"] == "cancelled", result
    directory = client.app.state.event_exports.root / session["id"]
    assert not (directory / "events.data").exists()
    assert not (directory / "population.fcs").exists()
    assert client.app.state.store.get(doc.id).model_dump_json() == doc.model_dump_json()


def test_four_ready_exports_limit_and_recovery_preserve_science(client):
    doc, _ = experiment(client)
    sessions = [finish(client, doc, prepare(client, doc)) for _ in range(4)]
    response = client.post(
        f"/api/workspaces/{doc.id}/event-exports",
        json=dict(revision=doc.revision, sample_id=doc.samples[0].id),
    )
    assert response.status_code == 409
    old = client.app.state.event_exports
    old.close()
    path = old.root / sessions[0]["id"] / "state.json"
    record = json.loads(path.read_text())
    record["status"] = "running"
    path.write_text(json.dumps(record))
    restored = event_exports.Sessions(client.app.state.store)
    try:
        assert restored.get(doc.id, sessions[0]["id"])["status"] == "interrupted"
        assert not (path.parent / "population.fcs").exists()
        assert restored.get(doc.id, sessions[1]["id"])["can_download"]
        assert len(restored.list(doc.id, doc.samples[0].id)) == 3
        file, _ = restored.download(doc.id, sessions[1]["id"])
        assert file.exists()
        restored.release(sessions[1]["id"])
    finally:
        restored.close()


def test_preparations_from_removed_samples_cannot_exhaust_the_export_limit(client):
    doc, _ = experiment(client)
    files = [finish(client, doc, prepare(client, doc)) for _ in range(4)]
    store = client.app.state.store
    removed = doc.samples[0].id

    def remove(d):
        d.samples = [s for s in d.samples if s.id != removed]
        d.gates = [g for g in d.gates if g.sample_id != removed]

    doc = store.mutate(doc.id, "Remove original sample", remove, doc.revision)
    next_file = finish(client, doc, prepare(client, doc))
    assert next_file["status"] == "ready"
    manager = client.app.state.event_exports
    assert all(manager.get(doc.id, f["id"])["status"] == "cancelled" for f in files)
    assert all(not (manager.root / f["id"] / "population.fcs").exists() for f in files)
    # Historical original event data remains available for undo.
    assert store.data_path(doc.id, removed).exists()


def test_native_fcs_import_publishes_origins_atomically_and_undo_redo_retains_them(client):
    doc, _ = experiment(client)
    merged = wait(client, doc, start(client, doc, body(doc)))
    assert apply(client, doc, merged).status_code == 200
    store = client.app.state.store
    doc = store.get(doc.id)
    sample = doc.samples[-1]
    session = finish(client, doc, prepare(client, doc, sample_id=sample.id))
    data = client.get(f"/api/workspaces/{doc.id}/event-exports/{session['id']}/download").content
    response = client.post(
        f"/api/workspaces/{doc.id}/import?revision={doc.revision}",
        files=[("files", ("Reopened.fcs", data))],
    )
    assert response.status_code == 200, response.text
    reopened = store.get(doc.id).samples[-1]
    assert reopened.id != sample.id and reopened.concatenation
    np.testing.assert_array_equal(
        np.load(store.origins_path(doc.id, reopened.id)),
        np.load(store.origins_path(doc.id, sample.id)),
    )
    np.testing.assert_array_equal(
        np.load(store.data_path(doc.id, reopened.id)), np.load(store.data_path(doc.id, sample.id))
    )
    imported = store.get(doc.id).model_dump_json()
    assert (
        client.post(
            f"/api/workspaces/{doc.id}/undo", json={"revision": store.get(doc.id).revision}
        ).status_code
        == 200
    )
    assert not any(s.id == reopened.id for s in store.get(doc.id).samples)
    assert store.origins_path(doc.id, reopened.id).exists()
    assert (
        client.post(
            f"/api/workspaces/{doc.id}/redo", json={"revision": store.get(doc.id).revision}
        ).status_code
        == 200
    )
    # History navigation changes revision, so compare science separately.
    assert json.loads(imported)["samples"] == store.get(doc.id).model_dump()["samples"]


def test_damaged_fcs_import_never_commits_partial_science_or_origin_files(client):
    doc, _ = experiment(client)
    session = finish(client, doc, prepare(client, doc))
    data = bytearray(
        client.get(f"/api/workspaces/{doc.id}/event-exports/{session['id']}/download").content
    )
    data[-1] ^= 1
    store = client.app.state.store
    before = store.get(doc.id).model_dump_json()
    files = set(store.data_path(doc.id, doc.samples[0].id).parent.glob("*.npy"))
    response = client.post(
        f"/api/workspaces/{doc.id}/import?revision={doc.revision}",
        files=[("files", ("Damaged.fcs", bytes(data)))],
    )
    assert response.status_code == 200, response.text
    assert response.json()["imported"] == 0
    assert "integrity check" in response.json()["errors"][0]["message"]
    assert store.get(doc.id).model_dump_json() == before
    assert set(store.data_path(doc.id, doc.samples[0].id).parent.glob("*.npy")) == files
