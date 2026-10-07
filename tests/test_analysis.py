import csv
import hashlib
import io
import json
import os
import time
import zipfile

import numpy as np
import pytest
from cytoforge.analysis import ConsensusMetacluster, atomic_json, input_hash, sample_indices
from cytoforge.jobs import JobManager
from cytoforge.models import AnalysisRequest, Gate, Transform, new_id
from sklearn.metrics import adjusted_rand_score


def _workspace(client, arrays):
    doc = client.post("/api/workspaces", json={"name": "Analysis experiment"}).json()
    files = []
    for index, values in enumerate(arrays):
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(["X", "Y", "Z"][: values.shape[1]])
        writer.writerows(values)
        files.append(("files", (f"Sample {index + 1}.csv", stream.getvalue(), "text/csv")))
    response = client.post(f"/api/workspaces/{doc['id']}/import?revision=0", files=files)
    assert response.status_code == 200, response.text
    assert response.json()["imported"] == len(arrays)
    return response.json()["workspace"]


def _request(doc, algorithm="pca", **kwargs):
    return dict(
        revision=doc["revision"],
        name="Verified analysis",
        algorithm=algorithm,
        inputs=[{"sample_id": s["id"]} for s in doc["samples"]],
        channels=[c["name"] for c in doc["samples"][0]["channels"]],
        use_transforms=False,
        standardize=False,
        max_events=1000,
        **kwargs,
    )


def _submit(client, doc, body):
    response = client.post(f"/api/workspaces/{doc['id']}/jobs", json=body)
    assert response.status_code == 202, response.text
    return response.json()["id"]


def _wait(client, doc, identifier, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/workspaces/{doc['id']}/jobs/{identifier}")
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.04)
    pytest.fail("The analysis worker did not complete in time")


def _apply(client, doc, identifier):
    response = client.post(
        f"/api/workspaces/{doc['id']}/jobs/{identifier}/apply", json={"revision": doc["revision"]}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_balanced_sampling_is_seeded_unique_and_redistributes_small_samples():
    counts = [3, 0, 100, 100]
    first = sample_indices(counts, 25, "balanced", 19)
    repeat = sample_indices(counts, 25, "balanced", 19)
    assert [len(v) for v in first] == [3, 0, 11, 11]
    for n, a, b in zip(counts, first, repeat, strict=True):
        np.testing.assert_array_equal(a, b)
        assert len(np.unique(a)) == len(a)
        assert np.all((a >= 0) & (a < n))
    proportional = sample_indices(counts, 25, "proportional", 19)
    assert sum(map(len, proportional)) == 25
    all_events = sample_indices(counts, 1000, "balanced", 19)
    assert [len(v) for v in all_events] == counts
    assert all(len(v) == 0 for v in sample_indices([0, 0], 10, "balanced", 42))


def test_algorithm_specific_options_do_not_block_pca(client):
    doc = _workspace(client, [np.random.default_rng(9).normal(size=(20, 3))])
    body = _request(doc, grid_size=2, n_clusters=10)
    identifier = _submit(client, doc, body)
    assert _wait(client, doc, identifier)["status"] == "succeeded"
    body["algorithm"] = "flowsom"
    response = client.post(f"/api/workspaces/{doc['id']}/jobs", json=body)
    assert response.status_code == 422 and "metaclusters" in response.text


def test_pca_matches_svd_and_preserves_nonfinite_event_identity(client):
    rng = np.random.default_rng(321)
    finite = rng.normal(size=(120, 3)) @ np.array([[5, 0, 0], [2, 1, 0], [0, 0, 0.1]])
    values = np.insert(finite, 71, [np.nan, 2, 4], axis=0)
    doc = _workspace(client, [values])
    job_id = _submit(client, doc, _request(doc))
    job = _wait(client, doc, job_id)
    assert job["status"] == "succeeded", job
    assert job["result"]["data"][0]["mapped_count"] == 120
    assert job["result"]["data"][0]["fitted_count"] == 120
    assert job["result"]["warnings"]
    _, singular, right = np.linalg.svd(finite - finite.mean(0), full_matrices=False)
    np.testing.assert_allclose(
        job["result"]["diagnostics"]["explained_variance_ratio"],
        (singular**2 / np.square(singular).sum())[:2],
    )
    saved = _apply(client, doc, job_id)
    assert len(saved["samples"][0]["computed_parameters"]) == 2
    store, engine = client.app.state.store, client.app.state.engine
    snapshot = store.get(saved["id"])
    sample = snapshot.samples[0]
    output = np.column_stack(
        [engine.column(snapshot, sample, c) for c in snapshot.analyses[0].columns]
    )
    assert np.isnan(output[71]).all()
    actual = np.delete(output, 71, axis=0)
    expected = (finite - finite.mean(0)) @ right[:2].T
    np.testing.assert_allclose(actual @ actual.T, expected @ expected.T, atol=1e-10)
    assert client.get(f"/api/workspaces/{doc['id']}/analyses").json()[0]["stale"] is False
    # Newly appended output parameters must not be mistaken for acquisition columns.
    np.testing.assert_allclose(engine.raw(snapshot, sample), values, equal_nan=True)
    response = client.get(f"/api/workspaces/{doc['id']}/samples/{sample.id}/export?format=fcs")
    assert response.status_code == 200
    from cytoforge.science import parse_fcs

    path = store.root / "export.fcs"
    path.write_bytes(response.content)
    exported, exported_values, _, _ = parse_fcs(path, "Analysis export")
    assert len(exported.channels) == 5
    np.testing.assert_allclose(exported_values[:, 3:], output, rtol=2e-6, atol=2e-6, equal_nan=True)


def test_group_model_maps_only_selected_population_and_balances_fit(client):
    rng = np.random.default_rng(10)
    doc = _workspace(client, [rng.normal(size=(30, 3)), rng.normal(size=(200, 3))])
    small = doc["samples"][0]
    gate = Gate(sample_id=small["id"], name="Positive X", kind="range", x="X", bounds=[0, 100])
    response = client.post(
        f"/api/workspaces/{doc['id']}/gates",
        json={"revision": doc["revision"], "gate": gate.model_dump()},
    )
    doc = response.json()
    body = _request(doc)
    body["max_events"] = 40
    body["inputs"][0]["gate_id"] = gate.id
    job_id = _submit(client, doc, body)
    job = _wait(client, doc, job_id)
    assert job["status"] == "succeeded", job
    data = job["result"]["data"]
    assert data[0]["fitted_count"] == data[0]["finite_count"] < 20
    assert sum(v["fitted_count"] for v in data) == 40
    assert data[1]["mapped_count"] == 200
    saved = _apply(client, doc, job_id)
    store, engine = client.app.state.store, client.app.state.engine
    snapshot = store.get(saved["id"])
    sample = snapshot.samples[0]
    column = engine.column(snapshot, sample, snapshot.analyses[0].columns[0])
    mask = engine.mask(snapshot, sample, gate.id)
    assert np.isfinite(column[mask]).all()
    assert np.isnan(column[~mask]).all()


def test_completed_job_cannot_apply_after_another_workspace_edit(client):
    doc = _workspace(client, [np.random.default_rng(11).normal(size=(40, 3))])
    identifier = _submit(client, doc, _request(doc))
    assert _wait(client, doc, identifier)["status"] == "succeeded"
    updated = client.patch(
        f"/api/workspaces/{doc['id']}",
        json={"revision": doc["revision"], "name": "Changed during fit", "description": ""},
    )
    assert updated.status_code == 200
    rejected = client.post(
        f"/api/workspaces/{doc['id']}/jobs/{identifier}/apply",
        json={"revision": updated.json()["revision"]},
    )
    assert rejected.status_code == 409
    assert updated.json()["samples"][0]["computed_parameters"] == []
    assert client.get(f"/api/workspaces/{doc['id']}/jobs/{identifier}").json()["can_apply"] is False


def test_analysis_archives_restore_exact_columns_and_undo_redo(client):
    doc = _workspace(client, [np.random.default_rng(12).normal(size=(45, 3))])
    identifier = _submit(client, doc, _request(doc))
    assert _wait(client, doc, identifier)["status"] == "succeeded"
    doc = _apply(client, doc, identifier)
    store, engine = client.app.state.store, client.app.state.engine
    original = store.get(doc["id"])
    expected = engine.column(original, original.samples[0], original.analyses[0].columns[0]).copy()
    archive = client.get(f"/api/workspaces/{doc['id']}/export/project")
    with zipfile.ZipFile(io.BytesIO(archive.content)) as contents:
        assert any(name.endswith(".fit.npy") for name in contents.namelist())
    assert original.analyses[0].input_snapshot[0]["acquisition_channels"] == ["X", "Y", "Z"]
    restored_response = client.post(
        "/api/import/project", files={"file": ("analysis.cytoforge", archive.content)}
    )
    assert restored_response.status_code == 200, restored_response.text
    restored = store.get(restored_response.json()["id"])
    actual = engine.column(restored, restored.samples[0], restored.analyses[0].columns[0])
    np.testing.assert_array_equal(actual, expected)
    assert client.get(f"/api/workspaces/{restored.id}/analyses").json()[0]["stale"] is False
    undone = client.post(
        f"/api/workspaces/{doc['id']}/undo", json={"revision": doc["revision"]}
    ).json()
    assert undone["analyses"] == [] and len(undone["samples"][0]["channels"]) == 3
    redone = client.post(
        f"/api/workspaces/{doc['id']}/redo", json={"revision": undone["revision"]}
    ).json()
    assert len(redone["analyses"]) == 1
    assert (
        client.get(f"/api/workspaces/{doc['id']}/analyses/{identifier}/provenance").status_code
        == 200
    )


def test_scientific_fingerprint_tracks_transform_and_formula_but_not_presentation(dataset):
    doc, sample, _, _ = dataset
    request = AnalysisRequest(
        revision=0,
        name="PCA",
        algorithm="pca",
        inputs=[{"sample_id": sample.id}],
        channels=["X", "Y"],
    )
    baseline = input_hash(doc, request)
    sample.name = "Renamed"
    sample.tags["donor"] = "A"
    assert input_hash(doc, request) == baseline
    sample.channels[0].transform = Transform(kind="asinh")
    assert input_hash(doc, request) != baseline


def test_archive_rejects_unordered_unsigned_fitted_ids_even_with_a_matching_hash(client):
    doc = _workspace(client, [np.random.default_rng(22).normal(size=(40, 3))])
    identifier = _submit(client, doc, _request(doc))
    assert _wait(client, doc, identifier)["status"] == "succeeded"
    doc = _apply(client, doc, identifier)
    response = client.get(f"/api/workspaces/{doc['id']}/export/project")
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    path = next(name for name in entries if name.endswith(".fit.npy"))
    identities = np.load(io.BytesIO(entries[path]), allow_pickle=False).astype(np.uint64)
    identities[0], identities[1] = identities[1], identities[0]
    output = io.BytesIO()
    np.save(output, identities, allow_pickle=False)
    entries[path] = output.getvalue()
    manifest = json.loads(entries["manifest.json"])
    manifest["workspace"]["analyses"][0]["data"][0]["fitted_ids_sha256"] = hashlib.sha256(
        entries[path]
    ).hexdigest()
    entries["manifest.json"] = json.dumps(manifest).encode()
    altered = io.BytesIO()
    with zipfile.ZipFile(altered, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    rejected = client.post(
        "/api/import/project", files={"file": ("bad-ids.cytoforge", altered.getvalue())}
    )
    assert rejected.status_code == 422
    assert "ordered" in rejected.json()["detail"]


def test_running_job_is_cancellable_and_health_stays_responsive(client):
    doc = _workspace(client, [np.random.default_rng(13).normal(size=(20000, 3))])
    body = _request(doc, "tsne")
    body["max_events"] = 20000
    identifier = _submit(client, doc, body)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = client.get(f"/api/workspaces/{doc['id']}/jobs/{identifier}").json()
        if job["status"] == "running":
            break
        time.sleep(0.02)
    assert job["status"] == "running"
    start = time.monotonic()
    assert client.get("/api/health").status_code == 200
    assert time.monotonic() - start < 1
    cancelled = client.post(f"/api/workspaces/{doc['id']}/jobs/{identifier}/cancel", json={})
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert not client.app.state.jobs.processes
    unchanged = client.get(f"/api/workspaces/{doc['id']}").json()
    assert unchanged["revision"] == doc["revision"]


def test_queued_jobs_cancel_and_interrupted_jobs_recover_as_records(dataset):
    doc, sample, _, engine = dataset
    manager = JobManager(engine.store)
    request = AnalysisRequest(
        revision=0,
        name="Queued PCA",
        algorithm="pca",
        inputs=[{"sample_id": sample.id}],
        channels=["X", "Y"],
    )
    job = manager.submit(doc, request)
    assert manager.cancel(doc, job["id"])["status"] == "cancelled"
    interrupted = manager.submit(doc, request)
    manager.close()
    recovered = JobManager(engine.store)
    assert recovered.get(doc, interrupted["id"])["status"] == "interrupted"
    # A hard crash can leave a running checkpoint; it must never restart silently.
    directory = recovered.directory / new_id()
    directory.mkdir()
    record = dict(interrupted, id=directory.name, status="running")
    atomic_json(directory / "state.json", record)
    after_crash = JobManager(engine.store)
    assert after_crash.get(doc, directory.name)["status"] == "interrupted"
    recovered.close()
    after_crash.close()


def test_tsne_only_assigns_coordinates_to_fitted_event_ids(client):
    doc = _workspace(client, [np.random.default_rng(17).normal(size=(100, 3))])
    body = _request(doc, "tsne")
    body.update(max_events=30, perplexity=5, iterations=300)
    identifier = _submit(client, doc, body)
    job = _wait(client, doc, identifier)
    assert job["status"] == "succeeded", job
    assert job["result"]["data"][0]["mapped_count"] == 30
    assert job["result"]["data"][0]["finite_count"] == 100
    assert "no out-of-sample" in job["result"]["warnings"][0]
    saved = _apply(client, doc, identifier)
    store, engine = client.app.state.store, client.app.state.engine
    snapshot = store.get(saved["id"])
    column = engine.column(snapshot, snapshot.samples[0], snapshot.analyses[0].columns[0])
    expected = sample_indices([100], 30, "balanced", 42)[0]
    np.testing.assert_array_equal(np.flatnonzero(np.isfinite(column)), expected)


def test_umap_projects_remaining_events_with_reproducible_seed(client):
    values = np.random.default_rng(18).normal(size=(80, 3))
    doc = _workspace(client, [values])
    body = _request(doc, "umap")
    body.update(max_events=40, n_neighbors=8)
    first = _submit(client, doc, body)
    job = _wait(client, doc, first, timeout=180)
    assert job["status"] == "succeeded", job
    assert job["result"]["data"][0]["mapped_count"] == 80
    second = _submit(client, doc, body)
    repeat = _wait(client, doc, second, timeout=180)
    assert repeat["status"] == "succeeded", repeat
    a = np.load(client.app.state.store.analysis_path(doc["id"], first, doc["samples"][0]["id"]))
    b = np.load(client.app.state.store.analysis_path(doc["id"], second, doc["samples"][0]["id"]))
    np.testing.assert_array_equal(a, b)


def test_consensus_metaclusters_distinguish_separated_groups():
    rng = np.random.default_rng(19)
    codes = np.concatenate([rng.normal(-10, 0.1, (8, 3)), rng.normal(10, 0.1, (8, 3))])
    model = ConsensusMetacluster(n_clusters=2, seed=42)
    labels = model.fit_predict(codes)
    assert adjusted_rand_score([0] * 8 + [1] * 8, labels) == 1
    np.testing.assert_allclose(model.consensus_[:8, :8], 1)
    np.testing.assert_allclose(model.consensus_[:8, 8:], 0)


def test_flowsom_assigns_every_eligible_event_and_creates_exact_cluster_gates(client):
    rng = np.random.default_rng(20)
    values = np.concatenate([rng.normal(-12, 0.2, (80, 3)), rng.normal(12, 0.2, (80, 3))])
    doc = _workspace(client, [values])
    body = _request(doc, "flowsom")
    body.update(max_events=80, grid_size=3, n_clusters=2, epochs=5)
    identifier = _submit(client, doc, body)
    job = _wait(client, doc, identifier, timeout=180)
    assert job["status"] == "succeeded", job
    assert job["result"]["data"][0]["mapped_count"] == len(values)
    saved = _apply(client, doc, identifier)
    assert len(saved["gates"]) == 2
    store, engine = client.app.state.store, client.app.state.engine
    snapshot = store.get(saved["id"])
    sample = snapshot.samples[0]
    labels = engine.column(snapshot, sample, snapshot.analyses[0].columns[1])
    assert adjusted_rand_score([0] * 80 + [1] * 80, labels) > 0.95
    counts = engine.gate_counts(snapshot, sample)
    assert sum(c["count"] for c in counts) == len(values)
    assert sorted(c["count"] for c in counts) == [80, 80]
    body["revision"] = saved["revision"]
    repeat_id = _submit(client, saved, body)
    repeated = _wait(client, saved, repeat_id, timeout=180)
    assert repeated["status"] == "succeeded", repeated
    original = np.load(store.analysis_path(doc["id"], identifier, sample.id))
    repeat = np.load(store.analysis_path(doc["id"], repeat_id, sample.id))
    np.testing.assert_array_equal(original, repeat)


def test_analysis_validation_rejects_unknown_channels_and_parameters(client):
    doc = _workspace(client, [np.random.default_rng(21).normal(size=(10, 3))])
    body = _request(doc)
    body["channels"] = ["X", "Absent"]
    response = client.post(f"/api/workspaces/{doc['id']}/jobs", json=body)
    assert response.status_code == 422
    body["channels"] = ["X", "X"]
    assert client.post(f"/api/workspaces/{doc['id']}/jobs", json=body).status_code == 422
    body["channels"] = ["X", "Y"]
    body["seed"] = -1
    assert client.post(f"/api/workspaces/{doc['id']}/jobs", json=body).status_code == 422


def test_empty_and_constant_populations_fail_without_mutating_workspace(client):
    doc = _workspace(client, [np.zeros((10, 3))])
    identifier = _submit(client, doc, _request(doc))
    job = _wait(client, doc, identifier)
    assert job["status"] == "failed"
    assert "must vary" in job["error"]
    assert client.get(f"/api/workspaces/{doc['id']}").json()["revision"] == doc["revision"]


def test_outputs_and_job_paths_are_validated(dataset):
    doc, sample, _, engine = dataset
    with pytest.raises(ValueError):
        engine.store.analysis_path(doc.id, "../escape", sample.id)
    # The scientific worker's parent ID is private checkpoint data, not an API field.
    with pytest.raises(ValueError):
        AnalysisRequest(
            revision=0,
            name="PCA",
            algorithm="pca",
            inputs=[{"sample_id": sample.id}],
            channels=["X", "Y"],
            parent_pid=os.getpid(),
        )
