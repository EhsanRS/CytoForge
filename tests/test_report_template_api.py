"""Real authenticated desktop-engine routes for portable template review and history."""

import json

import pytest
from cytoforge.models import new_id
from test_report_templates import composition, request_for, template_data


def owned_data(client):
    return template_data.__wrapped__(client.app.state.store)


def test_template_file_to_review_to_atomic_import_and_history(client):
    source, target, _ = owned_data(client)
    exported = client.post(
        f"/api/workspaces/{source.id}/report-templates/export",
        json={
            "revision": source.revision,
            "definition": composition(source).model_dump(mode="json"),
        },
    )
    assert exported.status_code == 200
    assert "attachment" in exported.headers["content-disposition"]
    parsed = client.post(
        f"/api/workspaces/{target.id}/report-templates/parse?revision={target.revision}",
        files={"file": ("composition.cytoforge-report.json", exported.content, "application/json")},
    )
    assert parsed.status_code == 200, parsed.text
    body = request_for(source, target, composition(source)).model_dump(mode="json")
    body["template"] = parsed.json()
    reviewed = client.post(f"/api/workspaces/{target.id}/report-templates/review", json=body)
    assert reviewed.status_code == 200, reviewed.text
    review = reviewed.json()
    assert review["can_apply"] and review["workspace_id"] == target.id
    denied = client.post(f"/api/workspaces/{target.id}/report-templates/apply", json=body)
    assert denied.status_code == 422 and "Review" in denied.text
    body["review_hash"] = review["review_hash"]
    imported = client.post(f"/api/workspaces/{target.id}/report-templates/apply", json=body)
    assert imported.status_code == 200, imported.text
    changed = imported.json()
    assert changed["revision"] == target.revision + 1
    assert changed["layouts"][-1]["id"] == body["id"]
    page = client.post(
        f"/api/workspaces/{target.id}/reports/render",
        json={
            "revision": changed["revision"],
            "definition": changed["layouts"][-1],
            "validate_sources": True,
        },
    )
    assert page.status_code == 200, page.text
    assert page.json()["manifest"]["elements"][0]["layers"][0]["population_count"] == 4
    undone = client.post(
        f"/api/workspaces/{target.id}/undo", json={"revision": changed["revision"]}
    )
    assert undone.status_code == 200 and not undone.json()["layouts"]
    redone = client.post(
        f"/api/workspaces/{target.id}/redo", json={"revision": undone.json()["revision"]}
    )
    assert redone.status_code == 200 and redone.json()["layouts"][-1]["id"] == body["id"]
    duplicate = client.post(
        f"/api/workspaces/{target.id}/report-templates/apply",
        json={**body, "revision": redone.json()["revision"]},
    )
    assert duplicate.status_code == 422 and "already saved" in duplicate.text
    origin = redone.json()["layouts"][-1]["template_origin"]
    archive = client.get(f"/api/workspaces/{target.id}/export/project")
    assert archive.status_code == 200, archive.text
    restored = client.post(
        "/api/import/project",
        files={"file": ("rebound-report.cytoforge", archive.content, "application/zip")},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["layouts"][-1]["template_origin"] == origin
    assert page.json()["manifest"]["definition"]["template_origin"] == origin


@pytest.mark.parametrize("damage", ["version", "extra_field", "digest", "missing_binding"])
def test_invalid_portable_file_never_creates_or_updates_workspace_data(client, damage):
    source, target, _ = owned_data(client)
    template = request_for(source, target, composition(source)).template.model_dump(mode="json")
    if damage == "version":
        template["version"] = 2
    elif damage == "extra_field":
        template["raw_events"] = [[1, 2, 3]]
    elif damage == "digest":
        template["sha256"] = "0" * 64
    else:
        template["bindings"].pop()
    response = client.post(
        f"/api/workspaces/{target.id}/report-templates/parse?revision={target.revision}",
        files={"file": ("bad.json", json.dumps(template).encode(), "application/json")},
    )
    assert response.status_code == 422
    assert client.app.state.store.get(target.id).model_dump() == target.model_dump()


def test_stale_revision_and_foreign_sources_cannot_be_applied(client):
    source, target, _ = owned_data(client)
    body = request_for(source, target, composition(source)).model_dump(mode="json")
    reviewed = client.post(f"/api/workspaces/{target.id}/report-templates/review", json=body)
    body["review_hash"] = reviewed.json()["review_hash"]
    body["mappings"][next(iter(body["mappings"]))] = source.samples[0].id
    changed = client.post(f"/api/workspaces/{target.id}/report-templates/apply", json=body)
    assert changed.status_code == 409
    assert not client.app.state.store.get(target.id).layouts
    body["id"] = new_id()
    body["review_hash"] = None
    body["revision"] = target.revision + 1
    stale = client.post(f"/api/workspaces/{target.id}/report-templates/review", json=body)
    assert stale.status_code == 409
