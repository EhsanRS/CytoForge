"""Multiple AF references, weighted separability and preserved control selections."""

import hashlib
import time

import numpy as np
import pytest
from cytoforge import autospill, autospread, quality
from cytoforge.autofluorescence import reference_review
from cytoforge.compensation import (
    ControlCalculation,
    assign_matrix,
    calculate_controls,
    save_control_populations,
)
from cytoforge.models import Channel, Compensation, Gate, QualityRequest, Sample, Workspace, new_id
from cytoforge.science import Engine, compensate, save_array, save_events

SPECTRA = np.array(
    [
        [1, 0.12, 0.28, 0.09, 0.03, 0.11, 0.18, 0.2],
        [1, 0.75, 0.15, 0.6, 0.04, 0.27, 0.14, 0.3],
        [0.04, 0.07, 0.18, 0.23, 1, 0.5, 0.11, 0.06],
        [0.2, 0.07, 0.1, 0.03, 0.12, 0.4, 1, 0.2],
    ]
)
BACKGROUND = np.array([33, -10, 55, 4, 19, 100, -50, 21])
WEIGHTS = [1, 2, 0.3, 4, 0.8, 1.5, 2, 0.6]
OUTPUTS = ["Fluor1", "Fluor2", "AF1", "AF2"]


def add_sample(store, doc, name, values, extra=()):
    names = [f"D{i + 1}" for i in range(8)] + list(extra)
    sample = Sample(name=name, event_count=len(values), channels=[Channel(name=n) for n in names])
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    doc.samples.append(sample)
    return sample


def spectral_controls(store, case="exact", count=4096):
    doc = Workspace(name="Independent multiple AF fixture")
    rng = np.random.default_rng(819417)
    for index in range(4):
        latent = np.zeros((count, 4))
        latent[:, index] = np.linspace(200, 32000, count)
        rng.shuffle(latent[:, index])
        if case in {"independent_af", "correlated_af"}:
            for af in (2, 3):
                if af != index:
                    latent[:, af] = rng.uniform(100, 800, count)
            if case == "correlated_af" and index < 2:
                latent[:, 3] += 0.15 * latent[:, index]
        raw = latent @ SPECTRA + BACKGROUND
        if case == "detector_noise":
            raw += rng.normal(0, 2, raw.shape)
        add_sample(store, doc, OUTPUTS[index], raw)
    doc = store.create(doc)
    request = autospill.AutoSpillRequest(
        revision=doc.revision,
        kind="spectral",
        detectors=[f"D{i + 1}" for i in range(8)],
        auto_cleanup=False,
        trim_fraction=0,
        background=BACKGROUND.tolist(),
        weights=WEIGHTS,
        af_outputs=["AF1", "AF2"],
        controls=[
            dict(name=name, primary_detector=peak, sample_id=sample.id)
            for name, peak, sample in zip(
                OUTPUTS, ["D1", "D1", "D5", "D7"], doc.samples, strict=True
            )
        ],
    )
    return doc, request, Engine(store)


def median_controls(store, *, qc=False):
    doc = Workspace(name="Paired median differences and two AF populations")
    count = 2048
    rng = np.random.default_rng(29181)
    latent = np.zeros((count, 4))
    latent[:, 2:] = rng.uniform(200, 600, (count, 2))
    negative_values = latent @ SPECTRA + BACKGROUND
    negative = add_sample(store, doc, "Matched negative", negative_values)
    positives = [
        add_sample(store, doc, name, negative_values + 6000 * SPECTRA[i])
        for i, name in enumerate(OUTPUTS[:2])
    ]
    af_rows = []
    for index in (2, 3):
        af_rows.append(
            np.column_stack(
                [
                    np.linspace(200, 32000, count)[:, None] * SPECTRA[index] + BACKGROUND,
                    np.full(count, index - 2),
                    np.arange(count) / 1000 + (index - 2) * count / 1000,
                ]
            )
        )
    reference = add_sample(
        store, doc, "Two raw AF populations", np.vstack(af_rows), ["ReferenceID", "Time"]
    )
    doc = store.create(doc)
    engine = Engine(store)
    qc_result = None
    parent_id = None
    if qc:
        request = QualityRequest(
            revision=doc.revision,
            sample_id=reference.id,
            channels=["D1"],
            time_channel="Time",
            compensated=False,
            use_transforms=False,
            bin_events=256,
        )
        qc_result, flags = quality.calculate(doc, request, engine, new_id())
        qc_result.data.sha256 = save_array(store.quality_path(doc.id, qc_result.id), flags)
        gate = Gate(
            sample_id=reference.id,
            name="Reviewed AF QC",
            kind="quality",
            quality_id=qc_result.id,
            quality_excluded_bins=[0],
            quality_exclusions=[],
        )
        doc = store.mutate(
            doc.id,
            "Review AF QC",
            lambda d: (d.quality_results.append(qc_result), d.gates.append(gate)),
            doc.revision,
        )
        parent_id = gate.id
    request = ControlCalculation(
        revision=doc.revision,
        kind="spectral",
        name="Two AF references",
        detectors=[f"D{i + 1}" for i in range(8)],
        background=BACKGROUND.tolist(),
        weights=WEIGHTS,
        controls=[
            dict(name=name, positive=dict(sample_id=pos.id), negative=dict(sample_id=negative.id))
            for name, pos in zip(OUTPUTS[:2], positives, strict=True)
        ],
        autofluorescence_controls=[
            dict(
                name=name,
                population=dict(
                    sample_id=reference.id,
                    gate_id=parent_id,
                    threshold_channel="ReferenceID",
                    minimum=index - 0.5,
                    maximum=index + 0.5,
                ),
            )
            for index, name in enumerate(OUTPUTS[2:])
        ],
    )
    return doc, request, Engine(store), qc_result


def test_two_af_spectra_recover_independent_physical_latents(store):
    doc, request, engine = spectral_controls(store)
    result = autospill.calculate(doc, request, engine)
    np.testing.assert_allclose(result.compensation.matrix, SPECTRA, atol=2e-12)
    assert result.diagnostics["converged"] and result.diagnostics["reconstruction_within_tolerance"]
    assert [c["autofluorescence"] for c in result.diagnostics["controls"]] == [
        False,
        False,
        True,
        True,
    ]
    assert [r["output"] for r in result.diagnostics["autofluorescence_sources"]] == OUTPUTS[2:]
    rng = np.random.default_rng(5190)
    physical = rng.uniform(100, 25000, (1000, 4))
    np.testing.assert_allclose(
        compensate(physical @ SPECTRA + BACKGROUND, result.compensation), physical, atol=2e-10
    )
    # Omitting the second AF signature creates actual dye leakage and detector residuals.
    incomplete = result.compensation.model_copy(deep=True)
    incomplete.outputs = OUTPUTS[:3]
    incomplete.matrix = SPECTRA[:3].tolist()
    wrong = compensate(physical @ SPECTRA + BACKGROUND, incomplete)
    assert np.max(np.abs(wrong[:, :2] - physical[:, :2])) > 100
    assert not autospill.is_stale(doc, result)
    request.af_outputs = ["AF2"]
    assert autospill.is_stale(doc, result)


def test_multiple_af_median_references_preserve_inline_thresholds_after_assignment(store):
    doc, request, engine, _ = median_controls(store)
    report = calculate_controls(doc, request, engine)
    matrix = Compensation.model_validate(report["compensation"])
    assert matrix.outputs == OUTPUTS
    np.testing.assert_allclose(matrix.matrix, SPECTRA, atol=1e-12)
    assert [row["positive"]["finite_count"] for row in report["diagnostics"]["controls"][2:]] == [
        2048,
        2048,
    ]
    save_control_populations(doc, matrix, store)
    doc.compensations.append(matrix)
    for sample in doc.samples:
        assign_matrix(sample, matrix)
    doc.revision += 1
    populations = matrix.provenance["control_populations"]
    assert [p["output"] for p in populations] == OUTPUTS
    for index, population in enumerate(populations[2:]):
        sample = next(s for s in doc.samples if s.id == population["sample_id"])
        expected = engine.raw(doc, sample)[:, 8] == index
        np.testing.assert_array_equal(
            Engine(store).mask(doc, sample, population["gate_id"]), expected
        )
        gate = next(g for g in doc.gates if g.id == population["gate_id"])
        assert gate.dimensions[0].compensation_ref == "uncompensated"
    assert len(matrix.provenance["negative_populations"]) == 2


def test_weighted_af_separation_compares_with_the_joint_other_source_span():
    matrix = Compensation(
        name="Weighted AF separation",
        kind="spectral",
        detectors=["D1", "D2", "D3"],
        outputs=["F", "AF1", "AF2"],
        matrix=[[1, 0, 0], [0, 1, 0], [0, 1, 0.05]],
        weights=[2, 1, 4],
    )
    rows, warnings = reference_review(matrix, ["AF1", "AF2"])
    expected = np.degrees(np.arctan(0.1))
    for row in rows:
        assert row["separation_angle_degrees"] == pytest.approx(expected, abs=2e-12)
        assert row["orthogonal_fraction"] == pytest.approx(0.1 / np.sqrt(1.01), abs=2e-14)
        assert row["weakly_separated"]
    assert len(warnings) == 2
    scaled = matrix.model_copy(deep=True)
    scaled.weights = [w * 1e200 for w in matrix.weights]
    scaled.matrix = (np.asarray(matrix.matrix) * [[1e200], [1e-200], [1e100]]).tolist()
    scaled_rows, _ = reference_review(scaled, ["AF1", "AF2"])
    np.testing.assert_allclose(
        [r["separation_angle_degrees"] for r in scaled_rows], [expected, expected], atol=2e-12
    )
    # Pairwise similarity alone misses a reference composed of several other sources.
    mixed = Compensation(
        name="Joint span separation",
        kind="spectral",
        detectors=["D1", "D2", "D3", "D4"],
        outputs=["F1", "F2", "AF"],
        matrix=[[1, 0, 0, 0], [0, 1, 0, 0], [1, 1, 0.01, 0]],
    )
    row = reference_review(mixed, ["AF"])[0][0]
    assert abs(row["weighted_cosine"]) < 0.8 and row["separation_angle_degrees"] < 1
    assert row["weakly_separated"]


def test_af_roles_are_canonical_and_legacy_single_reference_hashes_stay_stable(store):
    doc, request, _ = spectral_controls(store)
    before = autospill.input_hash(doc, request)
    request.af_outputs.reverse()
    assert autospill.input_hash(doc, request) == before
    request.af_outputs = ["AF1"]
    listed = autospill.input_hash(doc, request)
    request.af_outputs = []
    request.af_output = "AF1"
    assert autospill.input_hash(doc, request) == listed
    assert "af_outputs" not in autospill.settings_snapshot(request)


@pytest.mark.parametrize(
    "change, message",
    [
        ({"af_outputs": ["AF1", "AF1"]}, "unique"),
        ({"af_outputs": ["Missing"]}, "control"),
        ({"af_output": "AF1"}, "either"),
    ],
)
def test_invalid_autospill_af_roles_are_rejected(store, change, message):
    _, request, _ = spectral_controls(store)
    with pytest.raises(ValueError, match=message):
        autospill.AutoSpillRequest.model_validate(request.model_dump() | change)


@pytest.mark.parametrize("qc", [False, True])
def test_actual_api_saves_af_controls_for_spreading_archive_and_undo(client, qc):
    store = client.app.state.store
    doc, request, engine, result = median_controls(store, qc=qc)
    original = doc.model_dump_json()
    root = f"/api/workspaces/{doc.id}"
    response = client.post(root + "/compensations/calculate", json=request.model_dump())
    assert response.status_code == 200, response.text
    report = response.json()
    np.testing.assert_allclose(report["compensation"]["matrix"], SPECTRA, atol=1e-12)
    response = client.post(
        root + "/compensations",
        json=dict(
            revision=doc.revision,
            compensation=report["compensation"],
            sample_ids=[s.id for s in doc.samples],
            save_control_populations=True,
        ),
    )
    assert response.status_code == 200, response.text
    saved = Workspace.model_validate(response.json())
    matrix = saved.compensations[0]
    if qc:
        assert quality.is_stale(saved, result)
        captured = next(g for g in saved.gates if g.provenance.get("qc_selection_basis"))
        assert quality.is_captured_gate(captured, result)
    populations = matrix.provenance["control_populations"]
    for population, diagnostic in zip(populations, report["diagnostics"]["controls"], strict=True):
        sample = next(s for s in saved.samples if s.id == population["sample_id"])
        assert (
            int(Engine(store).mask(saved, sample, population["gate_id"]).sum())
            == diagnostic["positive"]["count"]
        )
    af_populations = populations[2:]
    spreading = autospread.AutoSpreadRequest(
        revision=saved.revision,
        matrix_id=matrix.id,
        controls=af_populations,
        events_per_bin=20,
        quantiles=32,
    )
    reviewed = autospread.calculate(saved, spreading, Engine(store))
    assert [c["parent_count"] for c in reviewed.controls] == [
        d["positive"]["count"] for d in report["diagnostics"]["controls"][2:]
    ]
    archived = client.get(root + "/export/project")
    assert archived.status_code == 200
    response = client.post(
        "/api/import/project", files={"file": ("two-af.cytoforge", archived.content)}
    )
    assert response.status_code == 200, response.text
    restored = store.get(response.json()["id"])
    restored_populations = restored.compensations[0].provenance["control_populations"]
    for p, d in zip(restored_populations, report["diagnostics"]["controls"], strict=True):
        sample = next(s for s in restored.samples if s.id == p["sample_id"])
        assert (
            int(Engine(store).mask(restored, sample, p["gate_id"]).sum()) == d["positive"]["count"]
        )
    undone = client.post(root + "/undo", json=dict(revision=saved.revision))
    assert undone.status_code == 200, undone.text
    expected = Workspace.model_validate_json(original)
    actual = Workspace.model_validate(undone.json())
    assert actual.gates == expected.gates and actual.quality_results == expected.quality_results
    assert not actual.compensations
    assert all(
        hashlib.sha256(store.data_path(doc.id, s.id).read_bytes()).hexdigest() == s.sha256
        for s in actual.samples
    )


def test_median_qc_flag_corruption_rejected_with_warm_caches_and_atomic_save(client):
    store = client.app.state.store
    doc, request, engine, qc = median_controls(store, qc=True)
    engine.mask(
        doc, doc.samples[-1], request.autofluorescence_controls[0].population.gate_id, False
    )
    report = calculate_controls(doc, request, engine)
    path = store.quality_path(doc.id, qc.id)
    flags = np.load(path, allow_pickle=False)
    flags[0, 0] ^= 1
    save_array(path, flags)
    with pytest.raises(ValueError, match="integrity"):
        calculate_controls(doc, request, engine)
    original = store.get(doc.id).model_dump_json()
    response = client.post(
        f"/api/workspaces/{doc.id}/compensations",
        json=dict(
            revision=doc.revision,
            compensation=report["compensation"],
            sample_ids=[doc.samples[-1].id],
            save_control_populations=True,
        ),
    )
    assert response.status_code == 422 and "integrity" in response.text
    assert store.get(doc.id).model_dump_json() == original


def wait_job(client, base, identifier):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        job = client.get(f"{base}/jobs/{identifier}").json()
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.04)
    raise AssertionError("Owned multiple AF worker did not finish")


@pytest.mark.parametrize("failure", ["raw_data", "output_name"])
def test_control_population_save_rejects_changed_inputs_without_mutating_document(store, failure):
    doc, request, engine, _ = median_controls(store)
    matrix = Compensation.model_validate(calculate_controls(doc, request, engine)["compensation"])
    if failure == "raw_data":
        # A warm numerical cache must not hide changes to the acquired file.
        sample = doc.samples[0]
        raw = engine.raw(doc, sample).copy()
        raw[0, 0] += 10
        save_events(store.data_path(doc.id, sample.id), raw)
        with pytest.raises(ValueError, match="integrity"):
            calculate_controls(doc, request, engine)
    else:
        matrix.outputs[0] = "Changed"
    original = doc.model_dump_json()
    with pytest.raises(ValueError, match="integrity|output names"):
        save_control_populations(doc, matrix, store)
    assert doc.model_dump_json() == original


@pytest.mark.parametrize("failure", ["cycle", "foreign", "stale_qc", "missing_bin"])
def test_median_control_dependency_review_rejects_invalid_populations(store, failure):
    doc, request, engine, qc = median_controls(store, qc=True)
    gate = next(
        g for g in doc.gates if g.id == request.autofluorescence_controls[0].population.gate_id
    )
    if failure == "cycle":
        gate.parent_id = gate.id
    elif failure == "foreign":
        gate.sample_id = doc.samples[0].id
    elif failure == "stale_qc":
        next(q for q in doc.quality_results if q.id == qc.id).request.score_threshold += 1
    else:
        gate.quality_excluded_bins = [len(qc.bins)]
    with pytest.raises(ValueError, match="cycle|different sample|stale|missing acquisition bin"):
        calculate_controls(doc, request, engine)


@pytest.mark.parametrize("failure", ["duplicate", "both_formats", "conventional", "too_many"])
def test_median_multiple_af_request_rejects_ambiguous_roles_and_excess_sources(store, failure):
    _, request, _, _ = median_controls(store)
    settings = request.model_dump()
    if failure == "duplicate":
        settings["autofluorescence_controls"][1]["name"] = "AF1"
    elif failure == "both_formats":
        settings["autofluorescence"] = settings["autofluorescence_controls"][0]
    elif failure == "conventional":
        settings["kind"] = "spillover"
    else:
        settings["detectors"] = ["D1", "D2", "D3"]
    with pytest.raises(ValueError):
        ControlCalculation.model_validate(settings)


def test_actual_autospill_worker_saves_two_af_roles_and_reopens_report(client):
    doc, request, _ = spectral_controls(client.app.state.store)
    base = f"/api/workspaces/{doc.id}/compensations/autospill"
    response = client.post(base + "/jobs", json=request.model_dump())
    assert response.status_code == 202, response.text
    job = wait_job(client, base, response.json()["id"])
    assert job["status"] == "succeeded", job.get("error")
    response = client.post(
        f"{base}/jobs/{job['id']}/apply",
        json=dict(
            revision=doc.revision,
            sample_ids=[s.id for s in doc.samples],
            save_cleanup_gates=True,
            acknowledge_unconverged=True,
        ),
    )
    assert response.status_code == 200, response.text
    saved = Workspace.model_validate(response.json())
    report = client.get(f"{base}/saved/{saved.compensations[0].id}")
    assert report.status_code == 200 and not report.json()["stale"]
    assert report.json()["result"]["request"]["af_outputs"] == ["AF1", "AF2"]
    assert [
        r["output"] for r in report.json()["result"]["diagnostics"]["autofluorescence_sources"]
    ] == ["AF1", "AF2"]
    np.testing.assert_allclose(saved.compensations[0].matrix, SPECTRA, atol=2e-12)
