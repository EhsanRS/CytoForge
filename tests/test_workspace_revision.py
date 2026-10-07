import pytest
from cytoforge.models import Workspace


def test_revision_observation_does_not_load_or_mutate_snapshot(store, monkeypatch):
    doc = store.create(Workspace(name="Revision observation"))
    before = store.history(doc.id)

    def unexpected_load(*args, **kwargs):
        raise AssertionError("Revision polling must not deserialize the workspace")

    monkeypatch.setattr(Workspace, "model_validate_json", unexpected_load)
    for _ in range(50):
        assert store.revision(doc.id) == 0
    assert store.history(doc.id) == before
    with pytest.raises(KeyError):
        store.revision("0" * 32)


def test_revision_increases_on_edit_undo_and_redo(store):
    doc = store.create(Workspace(name="Initial"))
    changed = store.mutate(doc.id, "Rename", lambda value: setattr(value, "name", "Changed"), 0)
    assert store.revision(doc.id) == changed.revision == 1
    undone = store.move_history(doc.id, -1, changed.revision)
    assert undone.name == "Initial"
    assert store.revision(doc.id) == undone.revision == 2
    redone = store.move_history(doc.id, 1, undone.revision)
    assert redone.name == "Changed"
    assert store.revision(doc.id) == redone.revision == 3


def test_revision_endpoint_is_authenticated_and_returns_only_commit_identity(client):
    doc = client.post("/api/workspaces", json={"name": "Private workspace"}).json()
    route = f"/api/workspaces/{doc['id']}/revision"
    assert client.get(route).json() == {"id": doc["id"], "revision": 0}
    assert client.get(route, headers={"X-CytoForge-Token": "wrong"}).status_code == 401
    assert client.get(route, headers={"Origin": "https://untrusted.example"}).status_code == 403
    assert client.get("/api/workspaces/invalid/revision").status_code == 422
    assert client.get(f"/api/workspaces/{'0' * 32}/revision").status_code == 404
