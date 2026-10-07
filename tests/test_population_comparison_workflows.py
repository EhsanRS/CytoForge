"""Background, reviewed, portable comparisons retain source identities."""

import csv
import io
import json
import time
import zipfile

import pytest
from cytoforge.models import Gate, Transform, Workspace
from test_population_comparison import fixture


def wait_job(client, doc, request):
    base = f"/api/workspaces/{doc.id}/population-comparison"
    response = client.post(
        f"{base}/jobs", json=request if isinstance(request, dict) else request.model_dump()
    )
    assert response.status_code == 202, response.text
    identifier = response.json()["id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = client.get(f"{base}/jobs/{identifier}")
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] not in {"queued", "running"}:
            break
        time.sleep(0.04)
    assert job["status"] == "succeeded", job
    return base, identifier, job


def initial(client, apply=True):
    store = client.app.state.store
    doc, request, _ = fixture(store)
    original = {s.id: store.data_path(doc.id, s.id).read_bytes() for s in doc.samples}
    base, identifier, job = wait_job(client, doc, request)
    assert store.get(doc.id).model_dump() == doc.model_dump()
    if apply:
        response = client.post(f"{base}/jobs/{identifier}/apply", json={"revision": doc.revision})
        assert response.status_code == 200, response.text
        doc = Workspace.model_validate(response.json())
    assert all(store.data_path(doc.id, sid).read_bytes() == v for sid, v in original.items())
    return base, doc, request, identifier, job


def test_minimal_parameter_defaults_survive_worker_serialization_and_saved_history(client):
    store = client.app.state.store
    doc, request, _ = fixture(store)
    body = request.model_dump()
    body["parameters"] = [{"channel": "X"}, {"channel": "Y"}]
    base, identifier, job = wait_job(client, doc, body)
    assert job["can_apply"] and not job["stale"]
    assert job["result"]["rows"][0]["metrics"]["ens_percent"] == pytest.approx(44)
    response = client.post(f"{base}/jobs/{identifier}/apply", json={"revision": doc.revision})
    assert response.status_code == 200, response.text
    doc = Workspace.model_validate(response.json())
    assert len(doc.comparison_results) == 1
    assert not client.get(f"{base}/{identifier}").json()["stale"]
    response = client.post(
        f"{base}/{identifier}/rename",
        json={"revision": doc.revision, "name": "Default coordinates"},
    )
    assert response.status_code == 200, response.text
    assert not client.get(f"{base}/{identifier}").json()["stale"]


def test_review_save_and_single_undo_do_not_create_channels_gates_or_samples(client):
    base, doc, request, identifier, job = initial(client)
    assert job["can_apply"] and not job["stale"]
    assert len(doc.samples) == 2 and len(doc.comparison_results) == 1 and not doc.gates
    assert all(len(s.channels) == 2 and not s.computed_parameters for s in doc.samples)
    assert doc.revision == 1
    assert client.get(f"/api/workspaces/{doc.id}/jobs").json() == []
    listing = client.get(f"{base}/jobs").json()
    assert listing[0]["status"] == "applied" and listing[0]["result"]["rows"] == []
    again = client.post(f"{base}/jobs/{identifier}/apply", json={"revision": doc.revision})
    assert again.status_code == 422
    response = client.post(f"/api/workspaces/{doc.id}/undo", json={"revision": doc.revision})
    assert response.status_code == 200, response.text
    restored = Workspace.model_validate(response.json())
    assert not restored.comparison_results
    assert client.get(f"{base}/jobs/{identifier}").json()["can_apply"]


def test_each_result_endpoint_guards_job_family_and_workspace_ownership(client):
    base, doc, _, identifier, _ = initial(client, apply=False)
    assert client.get(f"/api/workspaces/{doc.id}/kinetics/jobs/{identifier}").status_code == 422
    assert (
        client.post(
            f"/api/workspaces/{doc.id}/jobs/{identifier}/apply", json={"revision": doc.revision}
        ).status_code
        == 422
    )
    other, _, _ = fixture(client.app.state.store)
    assert (
        client.get(f"/api/workspaces/{other.id}/population-comparison/{identifier}").status_code
        == 404
    )
    assert (
        client.get(f"{base}/{identifier}/plot", params={"parameter_id": "a" * 32}).status_code
        == 422
    )


def test_immutable_graph_csv_svg_and_report_match_known_truth(client):
    base, doc, request, identifier, _ = initial(client)
    args = {"parameter_id": request.parameters[0].id, "target_index": 0}
    data = client.get(f"{base}/{identifier}/plot", params=args).json()
    assert sum(data["control_counts"]) == sum(data["target_counts"]) == 10
    assert data["row"]["metrics"]["ens_percent"] == pytest.approx(44)
    assert data["control_cdf"][-1] == pytest.approx(1)
    csv_data = client.get(f"{base}/{identifier}/statistics")
    assert csv_data.status_code == 200, csv_data.text
    rows = list(csv.DictReader(io.StringIO(csv_data.text)))
    assert len(rows) == 6 and float(rows[0]["ens_percent"]) == pytest.approx(44)
    for mode in ["histogram", "cdf", "difference"]:
        figure = client.get(
            f"{base}/{identifier}/figure", params={**args, "mode": mode, "smoothing": 1}
        )
        assert figure.status_code == 200, figure.text
        assert b"<svg" in figure.content and "nan" not in figure.text.lower()
    report = client.get(f"{base}/{identifier}/report").json()
    assert report["input_hash"] == doc.comparison_results[0].input_hash
    assert not report["stale"]


def test_project_restore_keeps_comparison_counts_and_original_source_bytes(client):
    base, doc, request, identifier, _ = initial(client)
    portable = client.get(f"/api/workspaces/{doc.id}/export/project")
    assert portable.status_code == 200, portable.text[:300]
    with zipfile.ZipFile(io.BytesIO(portable.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["version"] == 2
        assert set(manifest["workspace"]["comparison_results"][0]) == {"id", "sha256"}
        assert f"population-comparison/{identifier}.npz" in archive.namelist()
    response = client.post(
        "/api/import/project", files={"file": ("comparison.cytoforge", portable.content)}
    )
    assert response.status_code == 200, response.text
    restored = Workspace.model_validate(response.json())
    assert restored.id != doc.id and len(restored.comparison_results) == 1
    newbase = f"/api/workspaces/{restored.id}/population-comparison"
    report = client.get(f"{newbase}/{identifier}").json()
    assert not report["stale"]
    plot = client.get(
        f"{newbase}/{identifier}/plot", params={"parameter_id": request.parameters[0].id}
    )
    assert plot.status_code == 200 and sum(plot.json()["target_counts"]) == 10
    store = client.app.state.store
    assert all(
        store.data_path(doc.id, s.id).read_bytes()
        == store.data_path(restored.id, s.id).read_bytes()
        for s in doc.samples
    )


def test_cosmetic_rename_and_unrelated_edits_allow_apply_but_scientific_change_does_not(client):
    base, doc, request, identifier, _ = initial(client, apply=False)
    store = client.app.state.store
    renamed = store.mutate(doc.id, "Rename workspace", lambda w: setattr(w, "name", "Cosmetic"), 0)
    assert client.get(f"{base}/jobs/{identifier}").json()["can_apply"]
    outdated = client.post(f"{base}/jobs/{identifier}/apply", json={"revision": 0})
    assert outdated.status_code == 409
    saved = client.post(f"{base}/jobs/{identifier}/apply", json={"revision": renamed.revision})
    assert saved.status_code == 200, saved.text
    response = client.post(
        f"{base}/{identifier}/rename", json={"revision": 2, "name": "Reviewed result"}
    )
    assert response.status_code == 200, response.text
    assert (
        client.get(
            f"{base}/{identifier}/plot", params={"parameter_id": request.parameters[0].id}
        ).status_code
        == 200
    )
    assert client.get(f"{base}/{identifier}").json()["request"]["name"] == "Reviewed result"


def test_refit_retains_history_and_rejects_changed_parameter_identity(client):
    base, doc, request, first, _ = initial(client)
    request.revision = doc.revision
    request.replace_result_id = first
    request.probability_bins = 8
    _, second, _ = wait_job(client, doc, request)
    response = client.post(f"{base}/jobs/{second}/apply", json={"revision": doc.revision})
    assert response.status_code == 200, response.text
    doc = Workspace.model_validate(response.json())
    assert [r.id for r in doc.comparison_results] == [first, second]
    request.revision = doc.revision
    request.parameters[0].id = "a" * 32
    response = client.post(f"{base}/jobs", json=request.model_dump())
    assert response.status_code == 422 and "identities" in response.text


def test_gate_change_stales_job_and_blocks_reviewed_apply(client):
    store = client.app.state.store
    doc, request, _ = fixture(store)
    gate = Gate(
        sample_id=doc.samples[1].id,
        name="Target",
        kind="range",
        x="X",
        x_transform=Transform(),
        bounds=[0, 4],
    )
    doc = store.mutate(doc.id, "Target gate", lambda w: w.gates.append(gate), doc.revision)
    request.inputs[0].gate_id = gate.id
    request.revision = doc.revision
    base, identifier, _ = wait_job(client, doc, request)
    doc = store.mutate(
        doc.id, "Edit target", lambda w: setattr(w.gates[0], "bounds", [0, 2]), doc.revision
    )
    job = client.get(f"{base}/jobs/{identifier}").json()
    assert job["stale"] and not job["can_apply"]
    assert (
        client.post(f"{base}/jobs/{identifier}/apply", json={"revision": doc.revision}).status_code
        == 409
    )


def test_corrupt_plot_or_changed_original_bytes_never_apply(client):
    base, doc, _, identifier, _ = initial(client, apply=False)
    store = client.app.state.store
    path = store.data_path(doc.id, doc.samples[0].id)
    content = path.read_bytes()
    path.write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
    response = client.post(f"{base}/jobs/{identifier}/apply", json={"revision": doc.revision})
    assert response.status_code == 422 and "integrity" in response.text.lower()
    assert not store.get(doc.id).comparison_results
    path.write_bytes(content)
    artifact = store.comparison_path(doc.id, identifier)
    artifact.write_bytes(b"broken")
    response = client.post(f"{base}/jobs/{identifier}/apply", json={"revision": doc.revision})
    assert response.status_code == 422 and "SHA-256" in response.text
