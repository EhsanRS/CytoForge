import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pytest
from cytoforge import autospill, spectral_autospill
from cytoforge.models import Channel, Compensation, Sample, Transform, Workspace
from cytoforge.science import Engine, compensate, save_events

FIXTURES = Path(__file__).parent / "fixtures/spectral_autospill"
TRUTH = json.loads((FIXTURES / "truth.json").read_text())
REFERENCE = json.loads((FIXTURES / "reference.json").read_text())


def controls(store, case="independent_af"):
    settings = TRUTH[case]
    doc = Workspace(name="Rectangular AutoSpill validation")
    with np.load(FIXTURES / "controls.npz", allow_pickle=False) as fixture:
        for i, output in enumerate(settings["outputs"]):
            raw = fixture[f"{case}_{i}"]
            sample = Sample(
                name=f"{output} control",
                event_count=len(raw),
                channels=[Channel(name=n) for n in settings["detectors"]],
            )
            sample.sha256 = save_events(store.data_path(doc.id, sample.id), raw)
            doc.samples.append(sample)
    doc = store.create(doc)
    request = autospill.AutoSpillRequest(
        revision=doc.revision,
        kind="spectral",
        detectors=settings["detectors"],
        auto_cleanup=False,
        af_output="AF",
        background=settings["background"],
        weights=settings["weights"],
        trim_fraction=settings["trim_fraction"],
        biex_length=4096 if settings["biex"] == "biex4096" else 256,
        controls=[
            dict(name=output, primary_detector=settings["detectors"][peak], sample_id=sample.id)
            for output, peak, sample in zip(
                settings["outputs"], settings["peaks"], doc.samples, strict=True
            )
        ],
    )
    return doc, request, Engine(store)


@pytest.mark.parametrize("case", list(TRUTH))
def test_rectangular_refinement_matches_independent_r_and_detector_reconstruction(store, case):
    doc, request, engine = controls(store, case)
    result = autospill.calculate(doc, request, engine)
    reference = REFERENCE["cases"][case]
    assert result.compensation.kind == "spectral"
    assert result.compensation.outputs == ["Fluor1", "Fluor2", "AF"]
    assert len(result.compensation.matrix) == 3 and len(result.compensation.matrix[0]) == 6
    np.testing.assert_allclose(
        result.diagnostics["initial_matrix"], reference["initial"], atol=2e-9, rtol=2e-9
    )
    np.testing.assert_allclose(
        result.compensation.matrix, reference["matrix"], atol=2e-9, rtol=2e-9
    )
    np.testing.assert_allclose(
        result.diagnostics["residual_slopes"], reference["residual"], atol=2e-9, rtol=2e-9
    )
    np.testing.assert_allclose(
        [c["reconstruction_slopes"] for c in result.diagnostics["controls"]],
        reference["reconstruction"],
        atol=2e-9,
        rtol=2e-9,
    )
    trace = [
        [
            r["iteration"],
            int(r["scale"] == "biex"),
            r["damping"],
            r["error_sd"],
            r["max_error"],
            r["error_change"],
            r["condition_number"],
        ]
        for r in result.diagnostics["convergence"]
    ]
    np.testing.assert_allclose(trace, reference["convergence"], atol=2e-8, rtol=2e-8)
    assert result.diagnostics["stop_reason"] == reference["stop_reason"]
    np.testing.assert_allclose(
        [c["reconstruction_rms"] for c in result.diagnostics["controls"]],
        reference["reconstruction_rms"],
        atol=2e-8,
        rtol=2e-8,
    )
    np.testing.assert_allclose(
        [c["reconstruction_relative_rms"] for c in result.diagnostics["controls"]],
        reference["reconstruction_relative_rms"],
        atol=2e-10,
        rtol=2e-10,
    )
    assert all(c["used_count"] == 1600 for c in result.diagnostics["controls"])
    assert (
        result.diagnostics["controls"][0]["primary_detector"]
        == result.diagnostics["controls"][1]["primary_detector"]
    )
    assert doc == store.get(doc.id)
    saved = autospill.saved_result(result.compensation)
    assert saved == result and not autospill.is_stale(doc, saved)


def test_independent_latent_truth_and_correlated_af_limitation(store):
    doc, request, engine = controls(store)
    result = autospill.calculate(doc, request, engine)
    np.testing.assert_allclose(
        result.compensation.matrix, TRUTH["independent_af"]["signature"], atol=2e-3, rtol=0
    )
    with np.load(FIXTURES / "controls.npz", allow_pickle=False) as fixture:
        for i in range(3):
            unmixed = compensate(fixture[f"independent_af_{i}"], result.compensation)
            latent = fixture[f"independent_af_latent_{i}"]
            relative_rms = np.sqrt(np.mean((unmixed - latent) ** 2)) / np.sqrt(np.mean(latent**2))
            assert relative_rms < 2e-3
    assert result.diagnostics["reconstruction_within_tolerance"]
    doc, request, engine = controls(store, "correlated_af")
    result = autospill.calculate(doc, request, engine)
    error = np.max(
        np.abs(np.asarray(result.compensation.matrix) - TRUTH["correlated_af"]["signature"])
    )
    assert error > 0.01
    assert any("correlated staining" in warning for warning in result.warnings)


def test_refinement_cannot_repair_wrong_initial_row_space_and_reports_it(store):
    doc, request, engine = controls(store, "missing_component")
    result = autospill.calculate(doc, request, engine)
    initial, final = (
        np.asarray(result.diagnostics["initial_matrix"]),
        np.asarray(result.compensation.matrix),
    )
    np.testing.assert_allclose(
        np.linalg.pinv(initial) @ initial, np.linalg.pinv(final) @ final, atol=2e-12
    )
    assert result.diagnostics["reconstruction_max_relative_rms"] > request.linear_tolerance
    assert result.diagnostics["reconstruction_within_tolerance"] is False
    assert any("outside the initial spectral row space" in warning for warning in result.warnings)


def test_weighted_operator_matches_production_unmixing_and_latent_signal():
    signature = TRUTH["independent_af"]["signature"]
    matrix = Compensation(
        name="Weighted spectral truth",
        kind="spectral",
        detectors=TRUTH["independent_af"]["detectors"],
        outputs=["Fluor1", "Fluor2", "AF"],
        matrix=signature,
        background=[33, -10, 55, 4, 19, 100],
        weights=[1, 2, 0.3, 4, 0.8, 1.5],
    )
    latent = np.random.default_rng(15).normal(0, 100, (701, 3))
    raw = latent @ signature + matrix.background
    actual = (raw - matrix.background) @ spectral_autospill.unmixing_operator(matrix)
    np.testing.assert_allclose(actual, latent, atol=2e-12)
    np.testing.assert_allclose(actual, compensate(raw, matrix), atol=2e-12)
    noisy = raw + np.random.default_rng(17).normal(0, 10, raw.shape)
    np.testing.assert_allclose(
        (noisy - matrix.background) @ spectral_autospill.unmixing_operator(matrix),
        compensate(noisy, matrix),
        atol=2e-12,
    )


@pytest.mark.parametrize(
    "patch,match",
    [
        ({"background": [0]}, "Background"),
        ({"background": [float("inf")] * 6}, "finite number"),
        ({"weights": [1]}, "Weights"),
        ({"weights": [0] * 6}, "Weights"),
        ({"weights": [float("nan")] * 6}, "finite number"),
        ({"af_detector": "D1"}, "autofluorescence output"),
        ({"af_output": "Missing"}, "autofluorescence output"),
        (
            {"detector_transforms": {"D1": Transform(kind="wsp_biex").model_dump()}},
            "refinement outputs",
        ),
    ],
)
def test_invalid_spectral_settings_are_rejected(store, patch, match):
    _, request, _ = controls(store)
    with pytest.raises(ValueError, match=match):
        autospill.AutoSpillRequest.model_validate(request.model_dump() | patch)


def test_legacy_fingerprint_retains_original_settings_and_names(store):
    _, request, _ = controls(store)
    request = autospill.AutoSpillRequest(
        detectors=["D1", "D2"],
        revision=0,
        auto_cleanup=False,
        controls=[
            dict(name=d, primary_detector=d, sample_id=c.sample_id)
            for d, c in zip(["D1", "D2"], request.controls[:2], strict=True)
        ],
    )
    legacy = request.model_dump(
        exclude={"kind", "af_output", "af_outputs", "background", "weights", "revision", "name"}
    )
    assert autospill.settings_snapshot(request) == legacy
    assert autospill.output_names(request) == request.detectors


def test_spectral_settings_staleness_output_override_preview_and_explicit_sampling(store):
    doc, request, engine = controls(store)
    request.max_events = 500
    request.detector_transforms = {
        "AF": Transform(kind="wsp_biex", width=-50, positive=4.2, top=131072)
    }
    result = autospill.calculate(doc, request, engine)
    assert all(c["used_count"] == 500 for c in result.diagnostics["controls"])
    preview = autospill.preview(doc, result, engine, "Fluor1", "Fluor2")
    ids = np.asarray(preview["event_ids"])
    raw = engine.raw(doc, doc.samples[0])[ids]
    np.testing.assert_array_equal(preview["raw"], raw[:, [0, 0]])
    np.testing.assert_allclose(
        preview["compensated"], compensate(raw, result.compensation)[:, [0, 1]], atol=2e-10
    )
    assert preview["raw_x"] == preview["raw_y"] == "D1"
    with pytest.raises(ValueError, match="distinct"):
        autospill.preview(doc, result, engine, "Fluor1", "Fluor1")
    assert (
        autospill.input_hash(doc, request.model_copy(update={"weights": [1] * 6}))
        != result.input_hash
    )
    assert (
        autospill.input_hash(doc, request.model_copy(update={"background": [0] * 6}))
        != result.input_hash
    )
    changed = doc.model_copy(deep=True)
    changed.samples[0].name = "Renamed control"
    changed.revision += 1
    assert not autospill.is_stale(changed, result)
    path = engine.store.data_path(doc.id, doc.samples[0].id)
    raw = np.load(path, allow_pickle=False)
    raw[0, 0] += 1
    save_events(path, raw)
    with pytest.raises(ValueError, match="integrity"):
        autospill.preview(doc, result, engine, "Fluor1", "AF")


def test_rank_deficiency_and_acquired_output_collision_rejected(store):
    doc, request, engine = controls(store)
    request.controls[1] = request.controls[0].model_copy(update={"name": "Other"})
    with pytest.raises(ValueError, match="rank deficient"):
        autospill.calculate(doc, request, engine)
    request.controls[1] = request.controls[1].model_copy(update={"name": "D2"})
    with pytest.raises(ValueError, match="distinct from acquired"):
        autospill.validate_request(doc, request)


def test_full_spectral_detector_count_is_independent_of_source_count(store):
    names = [f"D{i}" for i in range(512)]
    rng = np.random.default_rng(24)
    spectra = rng.uniform(0.05, 0.8, (2, 512))
    spectra[:, 0] = 1
    doc = Workspace(name="512-detector spectral controls")
    for i in range(2):
        latent = np.zeros((300, 2))
        latent[:, i] = np.linspace(100, 100000, 300)
        raw = latent @ spectra
        sample = Sample(
            name=f"Source {i}", event_count=300, channels=[Channel(name=name) for name in names]
        )
        sample.sha256 = save_events(store.data_path(doc.id, sample.id), raw)
        doc.samples.append(sample)
    doc = store.create(doc)
    request = autospill.AutoSpillRequest(
        kind="spectral",
        revision=doc.revision,
        detectors=names,
        auto_cleanup=False,
        trim_fraction=0,
        controls=[
            dict(name=f"Fluor{i}", primary_detector="D0", sample_id=sample.id)
            for i, sample in enumerate(doc.samples)
        ],
    )
    result = autospill.calculate(doc, request, Engine(store))
    np.testing.assert_allclose(result.compensation.matrix, spectra, atol=2e-12)
    assert result.diagnostics["converged"] and result.diagnostics["reconstruction_within_tolerance"]
    assert result.diagnostics["controls"][0]["preview"]["y"] == "Fluor1"
    with pytest.raises(ValueError, match="64 detectors"):
        autospill.AutoSpillRequest.model_validate(request.model_dump() | {"kind": "spillover"})


def wait_job(client, base, identifier):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        job = client.get(f"{base}/jobs/{identifier}").json()
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.04)
    raise AssertionError("Rectangular AutoSpill worker did not finish")


def submit(client, doc, request):
    base = f"/api/workspaces/{doc.id}/compensations/autospill"
    response = client.post(base + "/jobs", json=request.model_dump())
    assert response.status_code == 202, response.text
    return base, wait_job(client, base, response.json()["id"])


def test_actual_worker_review_assignment_saved_preview_report_and_undo(client):
    doc, request, engine = controls(client.app.state.store)
    original = [sample.sha256 for sample in doc.samples]
    base, job = submit(client, doc, request)
    assert job["status"] == "succeeded", job.get("error")
    report = client.get(f"{base}/jobs/{job['id']}/report")
    assert report.status_code == 200 and report.json()["compensation"]["kind"] == "spectral"
    response = client.post(
        f"{base}/jobs/{job['id']}/apply",
        json=dict(
            revision=doc.revision,
            sample_ids=[doc.samples[0].id],
            save_cleanup_gates=False,
            acknowledge_unconverged=True,
        ),
    )
    assert response.status_code == 200, response.text
    updated = Workspace.model_validate(response.json())
    assert updated.samples[0].unmixed_parameters == ["Fluor1", "Fluor2", "AF"]
    assert [s.sha256 for s in updated.samples] == original
    assert [c.name for c in updated.samples[0].acquisition_channels] == request.detectors
    assert not autospill.is_stale(updated, autospill.saved_result(updated.compensations[0]))
    response = client.post(
        base + "/preview",
        json=dict(revision=updated.revision, result_id=job["id"], primary="Fluor1", secondary="AF"),
    )
    assert response.status_code == 200, response.text
    ids = response.json()["event_ids"]
    raw = engine.raw(doc, doc.samples[0])[ids]
    np.testing.assert_allclose(
        response.json()["compensated"],
        compensate(raw, updated.compensations[0])[:, [0, 2]],
        atol=2e-10,
    )
    response = client.post(f"/api/workspaces/{doc.id}/undo", json={"revision": updated.revision})
    assert response.status_code == 200
    restored = Workspace.model_validate(response.json())
    assert not restored.compensations and not restored.samples[0].unmixed_parameters
    for sample in restored.samples:
        assert (
            hashlib.sha256(
                client.app.state.store.data_path(doc.id, sample.id).read_bytes()
            ).hexdigest()
            == sample.sha256
        )


def test_worker_apply_requires_reconstruction_acknowledgement_and_actual_integrity(client):
    doc, request, _ = controls(client.app.state.store, "missing_component")
    base, job = submit(client, doc, request)
    assert job["status"] == "succeeded", job.get("error")
    assert job["result"]["diagnostics"]["reconstruction_within_tolerance"] is False
    response = client.post(
        f"{base}/jobs/{job['id']}/apply",
        json=dict(revision=doc.revision, sample_ids=[], save_cleanup_gates=False),
    )
    assert response.status_code == 422 and "Acknowledge" in response.text
    assert not client.app.state.store.get(doc.id).compensations
    path = client.app.state.store.data_path(doc.id, doc.samples[0].id)
    raw = np.load(path, allow_pickle=False)
    raw[0, 0] += 1
    save_events(path, raw)
    response = client.post(
        f"{base}/jobs/{job['id']}/apply",
        json=dict(
            revision=doc.revision,
            sample_ids=[],
            save_cleanup_gates=False,
            acknowledge_unconverged=True,
        ),
    )
    assert response.status_code == 422 and "integrity" in response.text
    assert not client.app.state.store.get(doc.id).compensations
