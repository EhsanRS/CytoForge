"""Refitting preserves live bindings; removal closes dependencies and remains undoable."""

import time

import pytest
from cytoforge.models import (
    AnalysisInput,
    AnalysisRequest,
    Channel,
    DerivedParameter,
    FitConstraint,
    Gate,
    LayoutDefinition,
    PlotDefinition,
    TableDefinition,
    Workspace,
)
from test_cellcycle import dna_events, saved_sample, submit_and_wait
from test_cellcycle import request_for as dna_request
from test_proliferation import dye_events, experiment, request_for, wait_job


def initial_model(client, platform):
    store = client.app.state.store
    if platform == "cell-cycle":
        values, _ = dna_events(size=3000)
        workspace, sample, _ = saved_sample(store, values)
        request = dna_request(workspace, sample)
        base, identifier, _ = submit_and_wait(client, workspace, sample)
    else:
        values, _ = dye_events(size=3000)
        workspace, samples, _ = experiment(store, values)
        sample = samples[0]
        request = request_for(workspace, sample)
        base, identifier, _ = wait_job(client, workspace, request)
    response = client.post(f"{base}/jobs/{identifier}/apply", json={"revision": workspace.revision})
    assert response.status_code == 200, response.text
    document = Workspace.model_validate(response.json())
    return base, document, sample, request, identifier


@pytest.mark.parametrize("platform", ["cell-cycle", "proliferation"])
def test_rename_replace_preserves_populations_and_removal_reviews_full_closure(client, platform):
    base, doc, sample, request, original = initial_model(client, platform)
    field = "cell_cycle_results" if platform == "cell-cycle" else "proliferation_results"
    model = getattr(doc, field)[0]
    original_columns = model.columns.copy()
    original_gate_ids = {g.id for g in doc.gates}

    # Formulas, Boolean populations, descendants and layouts all reference live
    # bindings. A distinct raw layout deliberately reuses a plot ID.
    def add_dependencies(workspace):
        source = workspace.samples[0]
        source.derived_parameters.extend(
            [
                DerivedParameter(name="Response score", expression=f'ch("{model.columns[0]}") + 1'),
                DerivedParameter(name="Squared response", expression='ch("Response score") ** 2'),
            ]
        )
        source.channels.extend([Channel(name="Response score"), Channel(name="Squared response")])
        child = Gate(
            sample_id=sample.id,
            name="Child population",
            kind="range",
            x="Time",
            parent_id=workspace.gates[0].id,
            bounds=[10, 2000],
        )
        formula = Gate(
            sample_id=sample.id,
            name="Formula population",
            kind="range",
            x="Squared response",
            bounds=[1, 4],
        )
        boolean = Gate(
            sample_id=sample.id,
            name="Boolean population",
            kind="boolean",
            operation="or",
            operands=[child.id, formula.id],
        )
        workspace.gates.extend([child, formula, boolean])
        affected = PlotDefinition(
            sample_id=sample.id, x="Response score", mode="histogram", gate_id=formula.id
        )
        unaffected = affected.model_copy(update={"x": request.channel, "gate_id": None})
        workspace.layouts.extend(
            [
                LayoutDefinition(name="Response report", plots=[affected]),
                LayoutDefinition(name="Raw report", plots=[unaffected]),
            ]
        )
        workspace.tables.append(TableDefinition(name="Response table", channel="Squared response"))

    doc = client.app.state.store.mutate(
        doc.id, "Connect model dependencies", add_dependencies, doc.revision
    )
    # An actual PCA consumes the fitted probabilities and must become stale
    # after replacement, while retaining its own original event-aligned output.
    discovery = AnalysisRequest(
        revision=doc.revision,
        name="Probability PCA",
        algorithm="pca",
        inputs=[AnalysisInput(sample_id=sample.id)],
        channels=model.columns[:2],
        use_transforms=False,
        standardize=False,
        max_events=1000,
    )
    response = client.post(f"/api/workspaces/{doc.id}/jobs", json=discovery.model_dump())
    assert response.status_code == 202, response.text
    pca_id = response.json()["id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = client.get(f"/api/workspaces/{doc.id}/jobs/{pca_id}").json()
        if job["status"] not in {"running", "queued"}:
            break
        time.sleep(0.04)
    assert job["status"] == "succeeded", job.get("error")
    response = client.post(
        f"/api/workspaces/{doc.id}/jobs/{pca_id}/apply", json={"revision": doc.revision}
    )
    assert response.status_code == 200, response.text
    doc = Workspace.model_validate(response.json())
    replacement = request.model_copy(
        update={
            "revision": doc.revision,
            "name": "Updated model",
            "replace_result_id": original,
            "g1_cv" if platform == "cell-cycle" else "dye_cv": FitConstraint(
                fixed=4.2 if platform == "cell-cycle" else 24
            ),
        }
    )
    response = client.post(f"{base}/jobs", json=replacement.model_dump())
    assert response.status_code == 202, response.text
    new_id = response.json()["id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = client.get(f"{base}/jobs/{new_id}").json()
        if job["status"] not in {"running", "queued"}:
            break
        time.sleep(0.04)
    assert job["status"] == "succeeded", job.get("error")
    assert job["can_apply"] and job["result"]["columns"] == original_columns
    response = client.post(f"{base}/jobs/{new_id}/apply", json={"revision": doc.revision})
    assert response.status_code == 200, response.text
    doc = Workspace.model_validate(response.json())
    assert len(getattr(doc, field)) == 2
    assert original_gate_ids <= {g.id for g in doc.gates}
    assert len(doc.gates) == len(original_gate_ids) + 3
    assert all(
        p.analysis_id == new_id
        for p in doc.samples[0].computed_parameters
        if p.name in original_columns
    )
    assert all(
        g.provenance.get(f"{request.algorithm}_id") == new_id
        for g in doc.gates
        if g.id in original_gate_ids
    )
    assert client.get(f"/api/workspaces/{doc.id}/jobs/{pca_id}").json()["stale"]
    assert not client.get(f"{base}/{new_id}").json()["stale"]
    assert client.app.state.store.analysis_path(doc.id, original, sample.id).exists()
    renamed = client.patch(
        f"/api/workspaces/{doc.id}/biology/{platform}/{new_id}",
        json={"revision": doc.revision, "name": "Reviewed response"},
    )
    assert renamed.status_code == 200, renamed.text
    doc = Workspace.model_validate(renamed.json())
    assert client.get(f"{base}/jobs/{new_id}").json()["request"]["name"] == "Reviewed response"
    assert getattr(doc, field)[-1].columns == original_columns
    assert not client.get(f"{base}/{new_id}").json()["stale"]
    path = f"/api/workspaces/{doc.id}/biology/{platform}/{new_id}"
    preview = client.get(f"{path}/dependencies").json()
    assert len(preview["derived_parameters"]) == 2
    assert len(preview["gates"]) == len(original_gate_ids) + 3
    assert len(preview["layout_plots"]) == len(preview["tables"]) == 1
    assert pca_id in {r["id"] for r in preview["historical_analyses"]}
    blocked = client.request("DELETE", path, json={"revision": doc.revision})
    assert blocked.status_code == 422
    # A new dependent object invalidates the exact preview before any mutation.
    extra = Gate(
        sample_id=sample.id,
        name="New dependent",
        kind="range",
        x=original_columns[0],
        bounds=[0.2, 0.8],
    )
    doc = client.app.state.store.mutate(
        doc.id, "Add a dependent gate", lambda w: w.gates.append(extra), doc.revision
    )
    blocked = client.request(
        "DELETE",
        path,
        json={"revision": doc.revision, "cascade": True, "review_hash": preview["review_hash"]},
    )
    assert blocked.status_code == 409
    preview = client.get(f"{path}/dependencies").json()
    removed = client.request(
        "DELETE",
        path,
        json={"revision": doc.revision, "cascade": True, "review_hash": preview["review_hash"]},
    )
    assert removed.status_code == 200, removed.text
    removed_doc = Workspace.model_validate(removed.json())
    assert len(getattr(removed_doc, field)) == 1 and not removed_doc.gates
    assert not removed_doc.samples[0].derived_parameters
    assert original_columns[0] not in {c.name for c in removed_doc.samples[0].channels}
    assert not removed_doc.layouts[0].plots and len(removed_doc.layouts[1].plots) == 1
    assert removed_doc.tables[0].channel == ""
    assert removed_doc.analyses[0].id == pca_id
    assert client.get(f"/api/workspaces/{doc.id}/jobs/{pca_id}").json()["stale"]
    undone = client.post(f"/api/workspaces/{doc.id}/undo", json={"revision": removed_doc.revision})
    assert undone.status_code == 200
    restored = Workspace.model_validate(undone.json())
    assert {g.id for g in restored.gates} == {g.id for g in doc.gates}
    assert len(getattr(restored, field)) == 2 and len(restored.samples[0].derived_parameters) == 2
    # Remove only the inactive historical report without disturbing live output.
    response = client.request(
        "DELETE",
        f"/api/workspaces/{doc.id}/biology/{platform}/{original}",
        json={"revision": restored.revision},
    )
    assert response.status_code == 200, response.text
    final = Workspace.model_validate(response.json())
    assert getattr(final, field)[0].id == new_id and len(final.gates) == len(restored.gates)
    archived = client.get(f"/api/workspaces/{doc.id}/export/project")
    assert archived.status_code == 200
    copied = client.post(
        "/api/import/project", files={"file": ("managed.cytoforge", archived.content)}
    )
    assert copied.status_code == 200, copied.text


def test_proliferation_rejects_replacement_scope_and_own_output_as_control(client):
    base, doc, sample, request, identifier = initial_model(client, "proliferation")
    request.revision = doc.revision
    request.replace_result_id = identifier
    changed = request.model_copy(update={"generations": 4})
    response = client.post(f"{base}/jobs", json=changed.model_dump())
    assert response.status_code == 422 and "output count" in response.text
    own = request.model_copy(
        update={
            "undivided_control": AnalysisInput(sample_id=sample.id, gate_id=doc.gates[0].id),
            "undivided_mean": FitConstraint(),
        }
    )
    response = client.post(f"{base}/jobs", json=own.model_dump())
    assert response.status_code == 422 and "own previous output" in response.text
    client.app.state.jobs.workers = 0
    queued = client.post(f"{base}/jobs", json=request.model_dump())
    assert queued.status_code == 202
    response = client.post(f"{base}/jobs/{queued.json()['id']}/cancel", json={})
    assert response.status_code == 200 and response.json()["status"] == "cancelled"
