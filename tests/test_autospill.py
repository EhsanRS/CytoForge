import json
import time
from pathlib import Path

import numpy as np
import pytest
from cytoforge import autospill
from cytoforge.jobs import JobManager
from cytoforge.models import Channel, Compensation, Gate, Sample, Transform, Workspace
from cytoforge.science import Engine, compensate, save_events

FIXTURES = Path(__file__).parent / "fixtures/autospill"


@pytest.mark.parametrize("name", ["biex256", "biex4096", "biex_negative", "biex_width_clamp"])
def test_biex_matches_native_lookup_and_independent_r_natural_splines(name):
    from cytoforge.autospill_biex import biex_functions, lookup

    specs = json.loads((FIXTURES / "truth.json").read_text())["native_transform"]["specs"]
    length, positive, negative, width, top = specs[name]
    with np.load(FIXTURES / "controls.npz", allow_pickle=False) as fixture:
        native_lut = fixture[name + "_lookup"]
        spline = fixture[name + "_spline"]
    raw, coordinates = lookup(negative, width, positive, top, length)
    np.testing.assert_allclose(raw, native_lut[:, 0], atol=2e-10, rtol=3e-15)
    np.testing.assert_array_equal(coordinates, native_lut[:, 1])
    forward, inverse = biex_functions(negative, width, positive, top, length)
    np.testing.assert_allclose(forward(spline[:, 0]), spline[:, 1], atol=1e-10, rtol=2e-12)
    np.testing.assert_allclose(inverse(spline[:, 2]), spline[:, 3], atol=2e-8, rtol=2e-12)
    # Native refinement extrapolates; it must never clamp extreme compensated signals.
    assert forward(raw[0] - 1000) < coordinates[0]
    assert forward(raw[-1] + 1000) > coordinates[-1]


def controls(store, case="af"):
    doc = Workspace(name="AutoSpill independent reference")
    names = ["D1", "D2", "AF"] + (["FSC-A", "SSC-A"] if case == "cleanup" else [])
    with np.load(FIXTURES / "controls.npz", allow_pickle=False) as fixture:
        for i in range(3):
            values = fixture[f"{case}_{i}"]
            sample = Sample(
                name=f"{names[i]} control",
                event_count=len(values),
                channels=[Channel(name=n) for n in names],
            )
            sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
            doc.samples.append(sample)
    doc = store.create(doc)
    request = autospill.AutoSpillRequest(
        revision=doc.revision,
        detectors=names[:3],
        auto_cleanup=case == "cleanup",
        af_detector="AF" if case.startswith("af") else None,
        controls=[
            dict(name=n, primary_detector=n, sample_id=s.id)
            for n, s in zip(names[:3], doc.samples, strict=True)
        ],
    )
    return doc, request, Engine(store)


@pytest.mark.parametrize("case", ["tails", "af", "cleanup", "af_independent"])
def test_complete_algorithm_agrees_with_pinned_author_r_reference(store, case):
    doc, request, engine = controls(store, case)
    reference = json.loads((FIXTURES / "truth.json").read_text())["cases"][case]
    # Reordered controls must retain detector-order matrix rows and diagnostics.
    request.controls.reverse()
    result = autospill.calculate(doc, request, engine)
    np.testing.assert_allclose(
        result.compensation.matrix, reference["matrix"], atol=2e-12, rtol=2e-12
    )
    np.testing.assert_allclose(
        result.diagnostics["initial_matrix"], reference["initial"], atol=2e-12, rtol=2e-12
    )
    np.testing.assert_allclose(
        result.diagnostics["residual_slopes"], reference["residual"], atol=2e-12, rtol=2e-12
    )
    assert result.diagnostics["converged"]
    assert result.diagnostics["final_scale"] == "biex"
    assert result.diagnostics["iterations"] == len(reference["convergence"]) - 1
    assert result.diagnostics["final_max_error"] < request.tolerance
    assert not autospill.is_stale(doc, result)
    assert (
        result.compensation.provenance["input_snapshot"]["reference_commit"]
        == autospill.REFERENCE_COMMIT
    )
    assert all(not f["fallback_ols"] for row in result.diagnostics["regressions"] for f in row)
    if case == "af_independent":
        # Tail conditioning biases even independent sources in both implementations.
        # A separate physical-truth test verifies recovery with explicit trim_fraction=0.
        physical_error = np.max(
            np.abs(np.asarray(result.compensation.matrix) - reference["physical_matrix"])
        )
        assert physical_error > 0.005
    if case == "af":
        assert result.diagnostics["controls"][-1]["autofluorescence"]
        assert any("autofluorescence spectrum" in w for w in result.warnings)


@pytest.mark.parametrize("primary", range(3))
def test_automatic_cleanup_matches_r_event_identities_and_excludes_debris(primary):
    with np.load(FIXTURES / "controls.npz", allow_pickle=False) as fixture:
        data, expected = fixture[f"cleanup_{primary}"], fixture[f"cleanup_gate_{primary}"]
    actual, details = autospill.cleanup_gate(data[:, 3:], [262144, 262144])
    np.testing.assert_array_equal(np.flatnonzero(actual), expected)
    assert not actual[1800:].any()
    assert len(details["vertices"]) >= 3


def test_huber_recovers_linear_truth_with_outliers_and_scale_extremes():
    x = np.linspace(100, 10000, 1500)
    y = 72 + 0.18 * x + np.sin(np.arange(len(x)))
    y[::31] += 1500
    offset, slope, diagnostic = autospill.robust_line(x, y)
    assert diagnostic["converged"] and not diagnostic["fallback_ols"]
    assert offset == pytest.approx(72, abs=0.15)
    assert slope == pytest.approx(0.18, abs=2e-5)
    for factor in (1e-100, 1e100):
        _, value, _ = autospill.robust_line(x * factor, y * factor)
        assert value == pytest.approx(slope, rel=1e-12)
    with pytest.raises(ValueError, match="variation"):
        autospill.robust_line(np.ones(100), np.arange(100))
    with pytest.raises(ValueError, match="finite"):
        autospill.robust_line(np.arange(10), np.r_[np.arange(9), np.nan])
    _, _, diagnostic = autospill.robust_line(x, y, iterations=1)
    assert diagnostic["fallback_ols"]


def test_continuous_noiseless_negative_spillover_is_retained(store):
    matrix = np.array([[1, -0.15, 0.04], [0.1, 1, 0.12], [0.03, 0.08, 1]])
    doc = Workspace(name="Exact linear references")
    for primary in range(3):
        true = np.zeros((1000, 3))
        true[:, primary] = np.linspace(500, 15000, 1000)
        values = true @ matrix + [70, 40, 20]
        sample = Sample(
            name=str(primary),
            event_count=len(values),
            channels=[Channel(name=n) for n in ["D1", "D2", "AF"]],
        )
        sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
        doc.samples.append(sample)
    doc = store.create(doc)
    request = autospill.AutoSpillRequest(
        revision=doc.revision,
        detectors=["D1", "D2", "AF"],
        auto_cleanup=False,
        controls=[
            dict(name=str(i), primary_detector=n, sample_id=s.id)
            for i, (n, s) in enumerate(zip(["D1", "D2", "AF"], doc.samples, strict=True))
        ],
    )
    result = autospill.calculate(doc, request, Engine(store))
    np.testing.assert_allclose(result.compensation.matrix, matrix, atol=1e-12)
    assert result.compensation.matrix[0][1] < 0


def test_cleanup_and_regression_validation_fail_explicitly(store):
    doc, request, engine = controls(store)
    request.min_events = 3000
    with pytest.raises(ValueError, match="finite cleanup events"):
        autospill.calculate(doc, request, engine)
    with pytest.raises(ValueError, match="Scatter data|continuous scatter"):
        autospill.cleanup_gate(np.c_[np.ones(100), np.arange(100)], [262144, 262144])
    with pytest.raises(ValueError, match="Scatter data"):
        autospill.cleanup_gate(np.c_[-np.arange(1, 101), -np.arange(1, 101)], [262144, 262144])
    with pytest.raises(ValueError, match="after regression trimming"):
        autospill.trimmed_pair(np.r_[np.zeros(98), 1, 2], np.arange(100), 0.01, 20)
    body = request.model_dump()
    body["controls"][0]["primary_detector"] = "AF"
    with pytest.raises(ValueError, match="one control"):
        autospill.AutoSpillRequest.model_validate(body)


def test_raw_cleanup_and_fingerprint_ignore_assigned_compensation_and_cosmetics(store):
    doc, request, engine = controls(store)
    sample = doc.samples[0]
    gate = Gate(sample_id=sample.id, name="Raw parent", kind="range", x="D1", bounds=[0, 200000])
    doc.gates.append(gate)
    request.controls[0].gate_id = gate.id
    initial = autospill.calculate(doc, request, engine)
    changed = doc.model_copy(deep=True)
    changed.name = "Cosmetic name"
    changed.samples[0].name = "Renamed control"
    changed.gates[0].name = "Renamed cleanup"
    changed.gates[0].color = "#ffffff"
    changed.compensations.append(
        Compensation(
            name="Existing assignment",
            detectors=request.detectors,
            matrix=[[1, 0.5, 0.1], [0.4, 1, 0.2], [0.1, 0.1, 1]],
        )
    )
    changed.samples[0].compensation_id = changed.compensations[0].id
    changed.revision += 1
    assert not autospill.is_stale(changed, initial)
    actual = autospill.calculate(changed, request, engine)
    np.testing.assert_array_equal(actual.compensation.matrix, initial.compensation.matrix)
    changed.samples[0].channels[0].transform = Transform(kind="asinh", cofactor=500)
    assert not autospill.is_stale(changed, initial)
    changed.gates[0].x_transform = Transform(kind="asinh", cofactor=500)
    assert autospill.is_stale(changed, initial)
    wrong = doc.model_copy(deep=True)
    wrong.gates[0].bounds = [0, 5000]
    assert autospill.is_stale(wrong, initial)
    request.controls[1].gate_id = gate.id
    with pytest.raises(ValueError, match="belong"):
        autospill.validate_request(doc, request)


def test_explicit_sampling_preview_matches_acquired_event_ids_and_detects_corruption(store):
    doc, request, engine = controls(store)
    request.max_events = 500
    result = autospill.calculate(doc, request, engine)
    assert all(
        c["used_count"] == 500 and c["finite_count"] == 1600 for c in result.diagnostics["controls"]
    )
    assert any("uniformly spaced event IDs" in w for w in result.warnings)
    actual = autospill.preview(doc, result, engine, "D1", "AF")
    ids = np.asarray(actual["event_ids"])
    raw = engine.raw(doc, doc.samples[0])[ids]
    np.testing.assert_array_equal(actual["raw"], raw[:, [0, 2]])
    np.testing.assert_allclose(
        actual["compensated"], compensate(raw, result.compensation)[:, [0, 2]], atol=1e-12
    )
    path = store.data_path(doc.id, doc.samples[0].id)
    values = np.load(path, allow_pickle=False)
    values[0, 0] += 1
    save_events(path, values)
    with pytest.raises(ValueError, match="integrity"):
        autospill.preview(doc, result, engine, "D1", "AF")


def wait_job(client, base, identifier):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        response = client.get(f"{base}/jobs/{identifier}")
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.04)
    raise AssertionError("AutoSpill worker did not complete")


def submit(client, doc, request):
    base = f"/api/workspaces/{doc.id}/compensations/autospill"
    response = client.post(base + "/jobs", json=request.model_dump())
    assert response.status_code == 202, response.text
    return base, wait_job(client, base, response.json()["id"])


def test_worker_review_atomic_assignment_cosmetic_revision_report_archive_and_undo(client):
    doc, request, engine = controls(client.app.state.store)
    base, job = submit(client, doc, request)
    assert job["status"] == "succeeded", job["error"]
    assert job["can_apply"] and not job["stale"]
    root = f"/api/workspaces/{doc.id}"
    assert client.get(root + "/jobs").json() == []
    summary = client.get(base + "/jobs").json()[0]
    assert summary["result"]["compensation"]["provenance"] == {}
    assert "controls" not in summary["result"]["diagnostics"]
    original = client.get(base + f"/jobs/{job['id']}/report").json()
    assert original["input_hash"] == job["result"]["input_hash"]
    changed = client.app.state.store.mutate(
        doc.id, "Rename", lambda d: setattr(d, "name", "Renamed"), doc.revision
    )
    preview = client.post(
        base + "/preview",
        json=dict(revision=changed.revision, result_id=job["id"], primary="D1", secondary="AF"),
    )
    assert preview.status_code == 200, preview.text
    assert len(preview.json()["event_ids"]) == 300
    # A missing assignment target cannot leave a saved matrix or partial assignment.
    bad = client.post(
        base + f"/jobs/{job['id']}/apply",
        json=dict(revision=changed.revision, sample_ids=[doc.samples[0].id, "f" * 32]),
    )
    assert bad.status_code == 422, bad.text
    assert client.app.state.store.get(doc.id).compensations == []
    response = client.post(
        base + f"/jobs/{job['id']}/apply",
        json=dict(revision=changed.revision, sample_ids=[s.id for s in doc.samples]),
    )
    assert response.status_code == 200, response.text
    saved = Workspace.model_validate(response.json())
    assert all(s.compensation_id == job["id"] for s in saved.samples)
    assert not client.get(base + f"/jobs/{job['id']}").json()["stale"]
    assert client.get(base + "/saved").json()[0]["id"] == job["id"]
    output = np.column_stack([engine.column(saved, saved.samples[0], n) for n in request.detectors])
    np.testing.assert_allclose(
        output, compensate(engine.raw(saved, saved.samples[0]), saved.compensations[0]), atol=1e-10
    )
    archived = client.get(root + "/export/project").content
    restored_response = client.post(
        "/api/import/project", files={"file": ("autospill.cytoforge", archived)}
    )
    assert restored_response.status_code == 200, restored_response.text
    restored = Workspace.model_validate(restored_response.json())
    report = client.get(f"/api/workspaces/{restored.id}/compensations/autospill/saved/{job['id']}")
    assert report.status_code == 200, report.text
    assert not report.json()["stale"]
    np.testing.assert_array_equal(
        report.json()["result"]["diagnostics"]["residual_slopes"],
        job["result"]["diagnostics"]["residual_slopes"],
    )
    undone = client.post(root + "/undo", json={"revision": saved.revision}).json()
    assert undone["compensations"] == [] and all(
        s["compensation_id"] is None for s in undone["samples"]
    )
    assert client.get(base + f"/jobs/{job['id']}").json()["can_apply"]
    redone = client.post(root + "/redo", json={"revision": undone["revision"]}).json()
    assert len(redone["compensations"]) == 1
    # Manual edits preserve the estimator and original calculation provenance.
    matrix = redone["compensations"][0]
    matrix["matrix"][0][1] += 0.01
    edited = client.post(
        root + "/compensations",
        json=dict(revision=redone["revision"], compensation=matrix, sample_ids=[]),
    )
    assert edited.status_code == 200, edited.text
    assert (
        edited.json()["compensations"][0]["source"]
        == "AutoSpill robust iterative regression · edited"
    )
    assert edited.json()["compensations"][0]["provenance"]["edited_fields"] == ["matrix"]


def test_unmet_tolerance_requires_explicit_review_and_saved_acknowledgement(client):
    doc, request, _ = controls(client.app.state.store)
    request.max_iterations = 1
    request.tolerance = 1e-8
    base, job = submit(client, doc, request)
    assert job["status"] == "succeeded", job["error"]
    assert not job["result"]["diagnostics"]["converged"]
    assert job["result"]["diagnostics"]["stop_reason"] == "iteration_limit"
    body = dict(revision=doc.revision, sample_ids=[doc.samples[0].id])
    denied = client.post(base + f"/jobs/{job['id']}/apply", json=body)
    assert denied.status_code == 422 and "Acknowledge" in denied.text
    body["acknowledge_unconverged"] = True
    applied = client.post(base + f"/jobs/{job['id']}/apply", json=body)
    assert applied.status_code == 200, applied.text
    assert applied.json()["compensations"][0]["provenance"]["unconverged_acknowledged"]


def test_stale_controls_prevent_apply_and_preview_and_revision_conflicts(client):
    doc, request, _ = controls(client.app.state.store)
    gate = Gate(
        sample_id=doc.samples[0].id, name="Parent", kind="range", x="D1", bounds=[0, 200000]
    )
    doc = client.app.state.store.mutate(
        doc.id, "Parent", lambda d: d.gates.append(gate), doc.revision
    )
    request.revision = doc.revision
    request.controls[0].gate_id = gate.id
    base, job = submit(client, doc, request)
    assert job["status"] == "succeeded", job["error"]
    changed = client.app.state.store.mutate(
        doc.id, "Change parent", lambda d: setattr(d.gates[0], "bounds", [0, 5000]), doc.revision
    )
    assert client.get(base + f"/jobs/{job['id']}").json()["stale"]
    denied = client.post(
        base + f"/jobs/{job['id']}/apply", json=dict(revision=changed.revision, sample_ids=[])
    )
    assert denied.status_code == 409
    assert client.app.state.store.get(doc.id).compensations == []
    assert (
        client.post(
            base + "/preview",
            json=dict(revision=changed.revision, result_id=job["id"], primary="D1", secondary="AF"),
        ).status_code
        == 422
    )
    assert client.post(base + "/jobs", json=request.model_dump()).status_code == 409


def test_cancel_queued_job_and_restart_recovery(store, client):
    doc, request, _ = controls(store)
    manager = JobManager(store, workers=0)
    job = manager.submit(doc, request)
    restarted = JobManager(store, workers=0)
    assert restarted.get(doc, job["id"])["status"] == "interrupted"
    restarted.close()
    manager.close()
    doc, request, _ = controls(client.app.state.store)
    client.app.state.jobs.workers = 0
    base = f"/api/workspaces/{doc.id}/compensations/autospill"
    queued = client.post(base + "/jobs", json=request.model_dump()).json()
    assert queued["status"] == "queued"
    response = client.post(base + f"/jobs/{queued['id']}/cancel", json={})
    assert response.status_code == 200 and response.json()["status"] == "cancelled"
    assert (
        client.post(
            base + f"/jobs/{queued['id']}/apply", json=dict(revision=doc.revision, sample_ids=[])
        ).status_code
        == 422
    )
    client.app.state.jobs.workers = 2


def test_nonfinite_scatter_and_fluorescence_are_excluded_from_preview_and_counts(store):
    doc, request, engine = controls(store, "cleanup")
    raw = np.array(engine.raw(doc, doc.samples[0]))
    raw[10, 3] = np.nan
    raw[20, 0] = np.inf
    doc.samples[0].sha256 = save_events(store.data_path(doc.id, doc.samples[0].id), raw)
    result = autospill.calculate(doc, request, engine)
    details = result.diagnostics["controls"][0]
    assert np.isfinite(details["cleanup"]["preview"]).all()
    assert np.isfinite(details["preview"]["raw"]).all()
    assert 10 not in details["preview"]["event_ids"]
    assert 20 not in details["preview"]["event_ids"]
    assert details["finite_count"] <= details["cleanup_count"]
    # Serialize with the same finite-only contract used for saved scientific artifacts.
    json.dumps(result.model_dump(), allow_nan=False)


def test_secondary_ties_are_reported_and_memory_limit_never_silently_subsamples(store, monkeypatch):
    x, y, count, ties = autospill.trimmed_pair(
        np.arange(1000), np.r_[np.zeros(500), np.ones(500)], 0.01, 20
    )
    assert ties and count == 980 and len(x) == len(y)
    doc, request, engine = controls(store)
    monkeypatch.setattr(autospill, "MAX_REGRESSION_VALUES", 4500)
    with pytest.raises(ValueError, match="memory limit"):
        autospill.calculate(doc, request, engine)
    request.max_events = 500
    sampled = autospill.calculate(doc, request, engine)
    assert all(c["used_count"] == 500 for c in sampled.diagnostics["controls"])


def test_worker_rejects_corrupt_acquired_data(client):
    doc, request, _ = controls(client.app.state.store)
    store = client.app.state.store
    path = store.data_path(doc.id, doc.samples[0].id)
    raw = np.load(path, allow_pickle=False)
    raw[0, 0] += 100
    save_events(path, raw)
    base, job = submit(client, doc, request)
    assert job["status"] == "failed" and "integrity" in job["error"]
    assert store.get(doc.id).compensations == []


def test_worker_omitted_defaults_are_canonical_and_saved_cleanup_matches_r_truth(client):
    doc, request, engine = controls(client.app.state.store, "cleanup")
    base = f"/api/workspaces/{doc.id}/compensations/autospill"
    body = request.model_dump(exclude_defaults=True)
    assert "biex" not in body
    queued = client.post(base + "/jobs", json=body)
    assert queued.status_code == 202, queued.text
    job = wait_job(client, base, queued.json()["id"])
    assert job["status"] == "succeeded", job["error"]
    response = client.post(
        base + f"/jobs/{job['id']}/apply",
        json=dict(revision=doc.revision, sample_ids=[s.id for s in doc.samples]),
    )
    assert response.status_code == 200, response.text
    saved = Workspace.model_validate(response.json())
    assert len(saved.gates) == 3
    ids = saved.compensations[0].provenance["cleanup_gate_ids"]
    assert ids == [g.id for g in saved.gates]
    with np.load(FIXTURES / "controls.npz", allow_pickle=False) as fixture:
        for i, gate in enumerate(saved.gates):
            assert all(d.compensation_ref == "uncompensated" for d in gate.dimensions)
            np.testing.assert_array_equal(
                np.flatnonzero(engine.mask(saved, saved.samples[i], gate.id)),
                fixture[f"cleanup_gate_{i}"],
            )
    assert not client.get(base + f"/jobs/{job['id']}").json()["stale"]


@pytest.mark.parametrize(
    "kind", ["range", "rectangle", "polygon", "ellipse", "quadrant", "boolean"]
)
def test_saved_cleanup_preserves_acquired_parent_constraints_after_assignment(store, kind):
    doc, request, engine = controls(store, "cleanup")
    options = {
        "range": dict(x="D1", bounds=[0, 20000]),
        "rectangle": dict(x="D1", y="D2", bounds=[0, 20000, 0, 6000]),
        "polygon": dict(x="D1", y="D2", vertices=[(0, 0), (22000, 0), (22000, 6000), (0, 6000)]),
        "ellipse": dict(x="D1", y="D2", center=(8000, 2000), radii=(12000, 5000), angle=0.2),
        "quadrant": dict(x="D1", y="D2", bounds=[1000, 50], quadrant=2),
    }
    if kind == "boolean":
        source = Gate(
            sample_id=doc.samples[0].id, name="Raw range", kind="range", x="D1", bounds=[0, 20000]
        )
        doc.gates.append(source)
        options[kind] = dict(operands=[source.id], operation="and")
    parent = Gate(sample_id=doc.samples[0].id, name="Parent", kind=kind, **options[kind])
    parent.provenance = {"gatingml_id": "foreign-id-must-not-be-duplicated"}
    doc.gates.append(parent)
    request.controls[0].gate_id = parent.id
    result = autospill.calculate(doc, request, engine)
    ids = autospill.save_cleanup_gates(doc, result)
    doc.compensations.append(result.compensation)
    for sample in doc.samples:
        sample.compensation_id = result.id
    doc.revision += 1
    doc = Workspace.model_validate_json(doc.model_dump_json())
    cleanup = next(
        g
        for g in doc.gates
        if g.id in ids
        and g.sample_id == doc.samples[0].id
        and g.provenance.get("control_detector") == "D1"
    )
    mask = engine.mask(doc, doc.samples[0], cleanup.id)
    assert int(mask.sum()) == result.diagnostics["controls"][0]["cleanup_count"]
    assert np.all(~mask | engine.mask(doc, doc.samples[0], parent.id, compensated=False))
    copied = next(g for g in doc.gates if g.id == cleanup.parent_id)
    assert copied.provenance["source_gate_id"] == parent.id
    assert "gatingml_id" not in copied.provenance
    assert not autospill.is_stale(doc, result)


def test_running_autospill_worker_is_cancellable(client):
    store = client.app.state.store
    # A fresh spawned worker stays cancellable during data preparation and refinement.
    doc, request, _ = controls(store, "cleanup")
    base = f"/api/workspaces/{doc.id}/compensations/autospill"
    queued = client.post(base + "/jobs", json=request.model_dump()).json()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        running = client.get(base + f"/jobs/{queued['id']}").json()
        if running["status"] == "running":
            break
        assert running["status"] == "queued"
        time.sleep(0.02)
    else:
        raise AssertionError("Worker did not start")
    cancelled = client.post(base + f"/jobs/{queued['id']}/cancel", json={})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert store.get(doc.id).compensations == []


def test_autofluorescence_recovers_known_matrix_for_independent_continuous_sources(store):
    matrix = np.array([[1, 0.18, 0.04], [0.09, 1, 0.1], [0.35, 0.2, 1]])
    stain, af = np.meshgrid(np.linspace(1000, 15000, 101), np.linspace(100, 2000, 31))
    doc = Workspace(name="Independent AF physical truth")
    for primary in range(3):
        true = np.zeros((stain.size, 3))
        true[:, 2] = af.ravel()
        if primary < 2:
            true[:, primary] = stain.ravel()
        values = true @ matrix + [70, 35, 25]
        sample = Sample(
            name=str(primary),
            event_count=len(values),
            channels=[Channel(name=n) for n in ["D1", "D2", "AF"]],
        )
        sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
        doc.samples.append(sample)
    doc = store.create(doc)
    request = autospill.AutoSpillRequest(
        revision=doc.revision,
        detectors=["D1", "D2", "AF"],
        auto_cleanup=False,
        af_detector="AF",
        trim_fraction=0,
        controls=[
            dict(name=n, primary_detector=n, sample_id=s.id)
            for n, s in zip(["D1", "D2", "AF"], doc.samples, strict=True)
        ],
    )
    result = autospill.calculate(doc, request, Engine(store))
    assert result.diagnostics["converged"]
    np.testing.assert_allclose(result.compensation.matrix, matrix, atol=1e-4, rtol=0)
