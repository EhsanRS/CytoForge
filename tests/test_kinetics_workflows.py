"""Durable desktop analyses retain identities, dependencies and portable reports."""

import csv
import hashlib
import io
import json
import time
import zipfile

import numpy as np
import pytest
from cytoforge.models import KineticsRange, PlateDefinition, TableColumn, TableDefinition, Workspace
from cytoforge.science import Engine
from test_kinetics import acquisition


def wait_job(client, doc, request):
    base = f"/api/workspaces/{doc.id}/kinetics"
    response = client.post(f"{base}/jobs", json=request.model_dump())
    assert response.status_code == 202, response.text
    identifier = response.json()["id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = client.get(f"{base}/jobs/{identifier}").json()
        if job["status"] not in {"queued", "running"}:
            break
        time.sleep(0.04)
    assert job["status"] == "succeeded", job
    return base, identifier, job


def initial(client, **settings):
    store = client.app.state.store
    doc, sample, request = acquisition(
        store,
        np.arange(9),
        np.arange(9),
        time_min=0,
        time_max=8,
        threshold=4,
        create_responder_gates=True,
        **settings,
    )
    request.ranges = [
        KineticsRange(name="Before", start=0, end=4),
        KineticsRange(name="After", start=4, end=8),
    ]
    base, identifier, job = wait_job(client, doc, request)
    response = client.post(f"{base}/jobs/{identifier}/apply", json={"revision": doc.revision})
    assert response.status_code == 200, response.text
    return base, Workspace.model_validate(response.json()), sample, request, identifier


def test_saved_ordinary_gates_use_exact_boundaries_and_stream_original_event_ids(client):
    base, doc, sample, request, identifier = initial(client)
    engine = Engine(client.app.state.store)
    expected = {
        ("source", None): np.arange(9),
        ("range", request.ranges[0].id): np.arange(4),
        ("range", request.ranges[1].id): np.arange(4, 9),
        ("responders", request.ranges[0].id): [],
        ("responders", request.ranges[1].id): np.arange(5, 9),
    }
    assert len(doc.gates) == 5
    for gate in doc.gates:
        key = gate.provenance["kinetics_role"], gate.provenance.get("range_id")
        np.testing.assert_array_equal(
            np.flatnonzero(engine.mask(doc, doc.samples[0], gate.id)), expected[key]
        )
    assert client.get(f"/api/workspaces/{doc.id}/jobs").json() == []
    events = client.get(f"{base}/{identifier}/events", params={"sample_id": sample.id})
    assert events.status_code == 200
    rows = list(csv.DictReader(io.StringIO(events.text)))
    assert [int(r["event_id"]) for r in rows] == list(range(9))
    assert [float(r["aligned_time"]) for r in rows] == list(range(9))
    assert all(r["source_membership"] == "1" for r in rows)
    series = list(csv.DictReader(io.StringIO(client.get(f"{base}/{identifier}/series").text)))
    assert len(series) == 8 and int(series[-1]["population_count"]) == 2
    statistics = list(
        csv.DictReader(io.StringIO(client.get(f"{base}/{identifier}/statistics").text))
    )
    assert [int(r["population_count"]) for r in statistics] == [4, 5]
    figure = client.get(f"{base}/{identifier}/figure", params={"sample_id": sample.id})
    assert figure.status_code == 200 and b"<svg" in figure.content
    assert "nan" not in figure.text.lower() and "infinity" not in figure.text.lower()


def test_replacement_updates_live_ranges_and_table_but_retains_history(client):
    base, doc, sample, request, identifier = initial(client)
    store = client.app.state.store
    interval = request.ranges[1]
    model = doc.kinetics_results[0]
    column = TableColumn(
        name="Response",
        kind="biology",
        platform="kinetics",
        result_id=identifier,
        kinetics_range_id=interval.id,
        biology_metric="responder_count",
    )
    table = TableDefinition(name="Response table", columns=[column])
    doc = store.mutate(
        doc.id, "Bind response table", lambda d: d.tables.append(table), doc.revision
    )
    plate = PlateDefinition(
        name="Live response plate",
        assignments={"A01": [sample.id]},
        columns=[column.model_copy(deep=True)],
    )
    doc = store.mutate(
        doc.id, "Bind response plate", lambda d: d.plates.append(plate), doc.revision
    )
    gate_ids = {g.id for g in doc.gates}
    request.revision, request.replace_result_id, request.threshold = doc.revision, identifier, 6
    request.name = "Updated response"
    request.ranges[1].start = 5
    _, replacement, job = wait_job(client, doc, request)
    assert job["can_apply"] and job["result"]["columns"] == model.columns
    response = client.post(f"{base}/jobs/{replacement}/apply", json={"revision": doc.revision})
    assert response.status_code == 200, response.text
    doc = Workspace.model_validate(response.json())
    assert len(doc.kinetics_results) == 2 and {g.id for g in doc.gates} == gate_ids
    assert doc.tables[0].columns[0].result_id == replacement
    responders = next(
        g
        for g in doc.gates
        if g.provenance.get("range_id") == interval.id
        and g.provenance.get("kinetics_role") == "responders"
    )
    np.testing.assert_array_equal(
        np.flatnonzero(Engine(store).mask(doc, doc.samples[0], responders.id)), [7, 8]
    )
    assert client.get(f"{base}/{identifier}").json()["fits"][0]["threshold"] == 4
    assert client.get(f"{base}/{replacement}").json()["fits"][0]["threshold"] == 6
    from cytoforge import tables

    rendered = tables.evaluate_table(doc, Engine(store), doc.tables[0])
    assert rendered["rows"][0]["values"][column.id] == 2
    path = f"/api/workspaces/{doc.id}/biology/kinetics/{replacement}"
    renamed = client.patch(path, json={"revision": doc.revision, "name": "Reviewed response"})
    assert renamed.status_code == 200, renamed.text
    doc = Workspace.model_validate(renamed.json())
    assert not client.get(f"{base}/{replacement}").json()["stale"]
    preview = client.get(path + "/dependencies").json()
    assert len(preview["gates"]) == 5 and len(preview["tables"]) == 1
    assert len(preview["plates"]) == 1
    rejected = client.request("DELETE", path, json={"revision": doc.revision})
    assert rejected.status_code == 422
    removed = client.request(
        "DELETE",
        path,
        json={"revision": doc.revision, "cascade": True, "review_hash": preview["review_hash"]},
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["gates"] == []
    removed_doc = Workspace.model_validate(removed.json())
    from cytoforge import plates

    missing = plates.evaluate(removed_doc, Engine(store), removed_doc.plates[0])
    assert missing["wells"][0]["values"][column.id] is None
    restored = client.post(
        f"/api/workspaces/{doc.id}/undo", json={"revision": removed_doc.revision}
    )
    assert restored.status_code == 200, restored.text
    restored_doc = Workspace.model_validate(restored.json())
    assert {g.id for g in restored_doc.gates} == gate_ids
    assert (
        plates.evaluate(restored_doc, Engine(store), restored_doc.plates[0])["wells"][0]["values"][
            column.id
        ]
        == 2
    )


def test_portable_kinetics_restores_and_rehashed_false_curve_is_rejected(client):
    _, doc, _, _, identifier = initial(client)
    archive = client.get(f"/api/workspaces/{doc.id}/export/project").content
    with zipfile.ZipFile(io.BytesIO(archive)) as original:
        manifest = json.loads(original.read("manifest.json"))
        assert manifest["version"] == 2
        assert set(manifest["workspace"]["kinetics_results"][0]) == {"id", "sha256"}
    restored = client.post("/api/import/project", files={"file": ("kinetics.cytoforge", archive)})
    assert restored.status_code == 200, restored.text
    recovered = Workspace.model_validate(restored.json())
    assert len(recovered.kinetics_results) == 1
    assert recovered.kinetics_results[0].fits[0].ranges[1].responder_count == 4
    rewritten = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(archive)) as source, zipfile.ZipFile(rewritten, "w") as target:
        report_path = f"kinetics/{identifier}.json"
        report = json.loads(source.read(report_path))
        report["fits"][0]["bins"][0]["raw_value"] = 999
        content = json.dumps(report).encode()
        manifest["workspace"]["kinetics_results"][0]["sha256"] = hashlib.sha256(content).hexdigest()
        for name in source.namelist():
            target.writestr(
                name,
                json.dumps(manifest).encode()
                if name == "manifest.json"
                else content
                if name == report_path
                else source.read(name),
            )
    rejected = client.post(
        "/api/import/project", files={"file": ("tampered.cytoforge", rewritten.getvalue())}
    )
    assert rejected.status_code == 422 and "curve" in rejected.text


@pytest.mark.parametrize("target", ["baseline", "bin", "range"])
def test_rehashed_derived_report_fields_are_independently_reconstructed(store, target):
    from cytoforge import kinetics
    from test_kinetics import calculate

    doc, _, request = acquisition(store, np.arange(9), np.arange(9), time_min=0, time_max=8)
    result, arrays = calculate(store, doc, request)
    fit = result.fits[0]
    if target == "baseline":
        fit.threshold += 1
    elif target == "bin":
        fit.bins[0].value = 100
    else:
        fit.ranges[0].auc = 100
    with pytest.raises(ValueError, match="validation"):
        kinetics.validate_data(result, result.data[0], arrays[result.data[0].sample_id])
