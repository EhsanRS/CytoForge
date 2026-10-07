import csv
import hashlib
import io
import json
import time
from pathlib import Path

import numpy as np
import pytest
from cytoforge import autospread, quality
from cytoforge.jobs import JobManager
from cytoforge.models import (
    Channel,
    Compensation,
    Gate,
    GateDimension,
    QualityRequest,
    Sample,
    Workspace,
    new_id,
)
from cytoforge.science import Engine, compensate, save_array, save_events

FIXTURES = Path(__file__).parent / "fixtures/autospread"
CASES = [
    "baseline",
    "signed",
    "constant_noise",
    "negative",
    "nonsignificant",
    "adaptive",
    "spectral",
]


def fixture(store, case="baseline"):
    truth = json.loads((FIXTURES / "truth.json").read_text())[case]
    detectors = [f"D{i + 1}" for i in range(len(truth["matrix"][0]))]
    matrix = Compensation(
        name="Reference matrix",
        detectors=detectors,
        outputs=["F1", "F2", "AF"] if case == "spectral" else detectors,
        matrix=truth["matrix"],
        kind=truth["kind"],
        background=truth["background"] if case == "spectral" else [],
        weights=truth["weights"] if case == "spectral" else [],
    )
    doc = Workspace(name="Synthetic spreading reference", compensations=[matrix])
    with np.load(FIXTURES / "controls.npz", allow_pickle=False) as arrays:
        for i, output in enumerate(matrix.outputs):
            values = arrays[f"{case}_{i}"]
            sample = Sample(
                name=f"{output} control",
                event_count=len(values),
                channels=[Channel(name=n) for n in detectors],
            )
            sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
            doc.samples.append(sample)
    doc = store.create(doc)
    request = autospread.AutoSpreadRequest(
        revision=doc.revision,
        matrix_id=matrix.id,
        quantiles=truth["quantiles"],
        events_per_bin=truth["events_per_bin"],
        controls=[
            dict(output=output, sample_id=sample.id)
            for output, sample in zip(matrix.outputs, doc.samples, strict=True)
        ],
    )
    return doc, request, truth


@pytest.mark.parametrize("case", CASES)
def test_published_equations_match_independent_base_r_and_weighted_normal_equations(store, case):
    doc, request, truth = fixture(store, case)
    before = doc.model_dump_json()
    result = autospread.calculate(doc, request, Engine(store))
    assert doc.model_dump_json() == before
    assert store.get(doc.id).revision == doc.revision
    for i, reference_control in enumerate(truth["controls"]):
        control = result.controls[i]
        assert sum(control["bin_counts"]) == control["used_count"]
        assert min(control["bin_counts"]) >= request.events_per_bin
        for reference in reference_control["pairs"]:
            j = reference["secondary"]
            pair = next(p for p in control["pairs"] if p["output"] == result.outputs[j])
            assert pair["status"] == reference["status"]
            for observed, expected in (
                (pair["baseline_noise"], reference["baseline"]),
                (pair["initial_fit"]["slope"], reference["initial_slope"]),
                (pair["final_fit"]["slope"], reference["final_slope"]),
                (pair["final_fit"]["p_value"], reference["p_value"]),
                (pair["final_fit"]["r_squared"], reference["r_squared"]),
                (result.matrix[i][j], reference["coefficient"]),
            ):
                assert observed == pytest.approx(expected, rel=5e-10, abs=2e-9)
            for key, reference_key in (
                ("robust_sd", "robust_sd"),
                ("adjusted_sd", "adjusted_sd"),
                ("secondary_medians", "median"),
            ):
                np.testing.assert_allclose(
                    pair[key], [b[reference_key] for b in reference["bins"]], rtol=5e-10, atol=2e-9
                )
            np.testing.assert_allclose(
                control["primary_medians"],
                [b["primary"] for b in reference["bins"]],
                rtol=5e-10,
                atol=2e-9,
            )
        assert result.matrix[i][i] is None
    json.dumps(result.model_dump(), allow_nan=False)


@pytest.mark.parametrize("case", ["baseline", "spectral"])
def test_block_operator_matches_production_compensation_with_detector_order(store, case):
    doc, request, _ = fixture(store, case)
    matrix = doc.compensations[0]
    raw = Engine(store).raw(doc, doc.samples[0])
    operator, background = autospread.unmixing_operator(matrix)
    np.testing.assert_allclose((raw - background) @ operator, compensate(raw, matrix), atol=2e-9)
    permutation = list(reversed(range(len(matrix.detectors))))
    matrix.detectors = [matrix.detectors[i] for i in permutation]
    matrix.matrix = [[row[i] for i in permutation] for row in matrix.matrix]
    matrix.background = [matrix.background[i] for i in permutation] if matrix.background else []
    matrix.weights = [matrix.weights[i] for i in permutation] if matrix.weights else []
    first = autospread.calculate(doc, request, Engine(store))
    for row in first.matrix:
        assert all(v is None or np.isfinite(v) for v in row)


def test_signed_root_handles_negative_zero_tiny_and_large_intensities():
    values = np.array([-1e308, -99, -3, -1e-30, 0, 1e-30, 3, 99, 1e308])
    expected = np.array([-1e154, -9, -1, -5e-31, 0, 5e-31, 1, 9, 1e154])
    np.testing.assert_allclose(autospread.signed_root(values), expected, rtol=2e-15, atol=0)


def test_f_test_rejects_nonsignificant_and_negative_slopes_and_zero_noise():
    primary = np.arange(1, 65, dtype=float) ** 2 - 1
    zero = autospread.spreading_pair(primary, np.zeros(64), 0.05)
    assert zero["coefficient"] == 0 and zero["final_fit"]["p_value"] == 1
    negative = autospread.spreading_pair(primary, 100 - 0.3 * autospread.signed_root(primary), 0.05)
    assert negative["final_fit"]["slope"] < 0 and negative["coefficient"] == 0
    fit = autospread.linear_fit(np.arange(1, 65), np.tile([1, -1], 32), intercept=False)
    assert fit["p_value"] > 0.05 and fit["df"] == 63


def test_second_regression_corrects_baseline_and_keeps_signed_variance():
    primary = np.arange(1, 65, dtype=float) ** 2 - 1
    sd = 30 + 0.5 * autospread.signed_root(primary)
    result = autospread.spreading_pair(primary, sd, 0.05)
    assert result["baseline_noise"] == pytest.approx(30)
    assert result["final_fit"]["slope"] > result["initial_fit"]["slope"]
    assert result["final_fit"]["intercept"] == 0
    assert result["adjusted_sd"][0] == pytest.approx(0)
    mixed = autospread.spreading_pair(primary, 30 + np.sin(np.arange(64)) * 10, 0.05)
    assert min(mixed["adjusted_sd"]) < 0  # Variance below the fitted baseline is not clipped away.


@pytest.mark.parametrize(
    "changes",
    [
        dict(quantiles=7),
        dict(quantiles=257),
        dict(events_per_bin=19),
        dict(significance=0),
        dict(significance=1),
        dict(max_events=799),
    ],
)
def test_invalid_scientific_settings_rejected(store, changes):
    doc, request, _ = fixture(store)
    with pytest.raises(ValueError):
        autospread.AutoSpreadRequest.model_validate(request.model_dump() | changes)


def test_duplicate_unknown_primary_missing_matrix_and_rank_deficiency(store):
    doc, request, _ = fixture(store)
    with pytest.raises(ValueError, match="at most one"):
        autospread.AutoSpreadRequest.model_validate(
            request.model_dump() | {"controls": [request.controls[0].model_dump()] * 2}
        )
    request.controls[0].output = "Absent"
    with pytest.raises(ValueError, match="output of"):
        autospread.validate_request(doc, request)
    request.controls[0].output = doc.compensations[0].outputs[0]
    doc.compensations[0].matrix[1] = doc.compensations[0].matrix[0].copy()
    with pytest.raises(ValueError, match="rank deficient"):
        autospread.validate_request(doc, request)
    doc.compensations.clear()
    with pytest.raises(ValueError, match="Save a compensation"):
        autospread.validate_request(doc, request)


def test_subset_event_limit_finite_filter_and_stable_event_identity(store):
    doc, request, _ = fixture(store)
    request.controls = request.controls[:1]
    request.max_events = 900
    sample = doc.samples[0]
    values = Engine(store).raw(doc, sample).copy()
    values[:10, 0] = np.nan
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    result = autospread.calculate(doc, request, Engine(store))
    control = result.controls[0]
    assert result.primaries == ["D1"] and len(result.matrix) == 1
    assert control["parent_count"] == 8192 and control["finite_count"] == 8182
    assert control["used_count"] == 900 and control["bin_count"] == 9
    ids = np.arange(10, 8192)[np.linspace(0, 8181, 900, dtype=int)]
    assert (
        control["used_event_ids_sha256"]
        == hashlib.sha256(np.asarray(ids, dtype="<i8").tobytes()).hexdigest()
    )
    assert any("10 nonfinite" in w for w in result.warnings)
    assert any("900 uniformly spaced" in w for w in result.warnings)


def test_actual_raw_data_integrity_failure_and_atomic_calculation(store):
    doc, request, _ = fixture(store)
    before = store.get(doc.id).model_dump_json()
    path = store.data_path(doc.id, doc.samples[0].id)
    values = Engine(store).raw(doc, doc.samples[0]).copy()
    values[0, 0] += 1
    save_events(path, values)
    with pytest.raises(ValueError, match="integrity check"):
        autospread.calculate(doc, request, Engine(store))
    assert store.get(doc.id).model_dump_json() == before


def test_raw_ratio_boolean_population_and_assigned_matrix_independence(store):
    doc, request, _ = fixture(store)
    sample = doc.samples[0]
    parent = Gate(sample_id=sample.id, name="Signal", kind="range", x="D1", bounds=[500, 40001])
    ratio = Gate(
        sample_id=sample.id,
        name="Raw ratio",
        kind="hyperrectangle",
        parent_id=parent.id,
        dimensions=[
            GateDimension(channel="D1", ratio_channels=["D1", "D2"], minimum=-1000, maximum=1000)
        ],
    )
    boolean = Gate(
        sample_id=sample.id,
        name="Intersection",
        kind="boolean",
        operands=[parent.id, ratio.id],
        operation="and",
    )
    doc.gates.extend([parent, ratio, boolean])
    sample.compensation_id = doc.compensations[0].id
    request.controls = request.controls[:1]
    request.controls[0].gate_id = boolean.id
    first = autospread.calculate(doc, request, Engine(store))
    alternate = doc.compensations[0].model_copy(deep=True)
    alternate.id = "a" * 32
    alternate.matrix = [[1, 0.8], [0, 1]]
    doc.compensations.append(alternate)
    sample.compensation_id = alternate.id
    second = autospread.calculate(doc, request, Engine(store))
    assert first.input_hash == second.input_hash and first.matrix == second.matrix
    assert set(first.input_snapshot["gates"]) == {parent.id, ratio.id, boolean.id}
    boolean.operands = ["b" * 32]
    with pytest.raises(ValueError, match="belong"):
        autospread.validate_request(doc, request)


def test_snapshot_tracks_science_and_ignores_cosmetic_and_assignment_changes(store):
    doc, request, _ = fixture(store)
    sample = doc.samples[0]
    gate = Gate(sample_id=sample.id, name="Parent", kind="range", x="D1", bounds=[-1, 50000])
    doc.gates.append(gate)
    request.controls[0].gate_id = gate.id
    result = autospread.calculate(doc, request, Engine(store))
    sample.name = "Renamed"
    sample.compensation_id = doc.compensations[0].id
    doc.compensations[0].name = "Renamed matrix"
    doc.compensations[0].source = "Edited label"
    gate.name = "Renamed gate"
    gate.color = "#abcdef"
    assert not autospread.is_stale(doc, result)
    gate.bounds[0] = 2
    assert autospread.is_stale(doc, result)
    gate.bounds[0] = -1
    doc.compensations[0].matrix[0][1] += 0.01
    assert autospread.is_stale(doc, result)


def test_reviewed_qc_population_integrity_and_stale_dependencies(store):
    doc, request, _ = fixture(store)
    sample = doc.samples[0]
    acquired = Engine(store).raw(doc, sample).copy()
    acquired = np.column_stack([acquired, np.arange(len(acquired)) / 1000])
    sample.channels.append(Channel(name="Time"))
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), acquired)
    qc_request = QualityRequest(
        revision=doc.revision,
        sample_id=sample.id,
        time_channel="Time",
        channels=["D1"],
        use_transforms=False,
    )
    result, flags = quality.calculate(doc, qc_request, Engine(store), new_id())
    result.data.sha256 = save_array(store.quality_path(doc.id, result.id), flags)
    doc.quality_results.append(result)
    gate = Gate(
        sample_id=sample.id,
        name="QC population",
        kind="quality",
        quality_id=result.id,
        quality_excluded_bins=[0],
        quality_exclusions=[],
    )
    doc.gates.append(gate)
    request.controls = request.controls[:1]
    request.controls[0].gate_id = gate.id
    spreading = autospread.calculate(doc, request, Engine(store))
    expected = quality.selection(flags, [0], [])
    assert spreading.controls[0]["parent_count"] == int(expected.sum())
    assert result.id in spreading.input_snapshot["qc"]
    gate.quality_excluded_bins = [1]
    assert autospread.is_stale(doc, spreading)
    gate.quality_excluded_bins = [0]
    # A warm mask cache must not hide corruption of the saved QC flag file.
    engine = Engine(store)
    engine.mask(doc, sample, gate.id, compensated=False)
    path = store.quality_path(doc.id, result.id)
    damaged = flags.copy()
    damaged[0, 0] ^= 1
    save_array(path, damaged)
    with pytest.raises(ValueError, match="integrity"):
        autospread.calculate(doc, request, engine)
    sample.sha256 = "0" * 64
    with pytest.raises(ValueError, match="missing or stale"):
        autospread.validate_request(doc, request)


def test_population_cycles_foreign_samples_and_virtual_parameters_rejected(store):
    doc, request, _ = fixture(store)
    gate = Gate(sample_id=doc.samples[0].id, name="Raw", kind="range", x="D1", bounds=[0, 50000])
    doc.gates.append(gate)
    request.controls[0].gate_id = gate.id
    gate.parent_id = gate.id
    with pytest.raises(ValueError, match="cycle"):
        autospread.validate_request(doc, request)
    gate.parent_id = None
    gate.sample_id = doc.samples[1].id
    with pytest.raises(ValueError, match="belong"):
        autospread.validate_request(doc, request)
    gate.sample_id = doc.samples[0].id
    gate.x = "Virtual"
    with pytest.raises(ValueError, match="acquired parameters"):
        autospread.validate_request(doc, request)


def test_report_rejects_damaged_matrix_and_quantile_event_identity(store):
    doc, request, _ = fixture(store)
    result = autospread.calculate(doc, request, Engine(store)).model_dump()
    result["matrix"][0][1] += 1
    with pytest.raises(ValueError, match="differs from"):
        autospread.AutoSpreadResult.model_validate(result)
    result["matrix"][0][1] = result["controls"][0]["pairs"][0]["coefficient"]
    result["controls"][0]["bin_counts"][0] += 1
    with pytest.raises(ValueError, match="quantile counts"):
        autospread.AutoSpreadResult.model_validate(result)


def test_small_flat_controls_memory_limit_and_cancellation(store, monkeypatch):
    doc, request, _ = fixture(store)
    request.controls = request.controls[:1]
    monkeypatch.setattr(autospread, "MAX_VALUES", 100)
    with pytest.raises(ValueError, match="memory limit"):
        autospread.calculate(doc, request, Engine(store))
    monkeypatch.setattr(autospread, "MAX_VALUES", 64_000_000)
    called = []

    def check():
        called.append(1)
        if len(called) == 3:
            raise InterruptedError("Cancel")

    with pytest.raises(InterruptedError, match="Cancel"):
        autospread.calculate(doc, request, Engine(store), check=check)
    assert len(called) == 3
    values = np.zeros((799, 2))
    sample = doc.samples[0]
    sample.event_count = len(values)
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    with pytest.raises(ValueError, match="eight quantile"):
        autospread.calculate(doc, request, Engine(store))
    sample.event_count = 800
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), np.zeros((800, 2)))
    with pytest.raises(ValueError, match="no variation"):
        autospread.calculate(doc, request, Engine(store))


def wait_job(manager, doc, identifier):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        job = manager.get(doc, identifier)
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.05)
    raise AssertionError(f"Spreading worker timed out: {job}")


def test_spawned_worker_save_replace_undo_and_restart(store):
    doc, request, _ = fixture(store, "spectral")
    manager = JobManager(store)
    manager.start()
    try:
        job = manager.submit(doc, request)
        completed = wait_job(manager, doc, job["id"])
        assert completed["status"] == "succeeded", completed.get("error")
        assert completed["can_apply"]
        assert manager.list(doc) == [] and len(manager.list(doc, spread=True)) == 1
        original_matrix = doc.compensations[0].matrix.copy()
        original_raw = [s.sha256 for s in doc.samples]
        saved = manager.apply_autospread(doc.id, job["id"], doc.revision)
        assert saved.compensations[0].matrix == original_matrix
        assert [s.sha256 for s in saved.samples] == original_raw
        assert all(s.compensation_id is None for s in saved.samples)
        assert manager.get(saved, job["id"])["status"] == "applied"
        assert autospread.saved_result(saved.compensations[0]).id == job["id"]
        undone = store.move_history(doc.id, -1, saved.revision)
        assert "autospread" not in undone.compensations[0].provenance
        assert manager.get(undone, job["id"])["can_apply"]
        saved = manager.apply_autospread(doc.id, job["id"], undone.revision)
    finally:
        manager.close()
    restored = JobManager(store)
    try:
        assert restored.get(saved, job["id"])["status"] == "applied"
    finally:
        restored.close()


def test_saving_rechecks_actual_data_and_stale_matrix_atomically(store):
    from cytoforge.store import ConflictError

    doc, request, _ = fixture(store)
    manager = JobManager(store)
    manager.start()
    try:
        job = manager.submit(doc, request)
        completed = wait_job(manager, doc, job["id"])
        assert completed["status"] == "succeeded", completed.get("error")
        sample = doc.samples[0]
        original = Engine(store).raw(doc, sample).copy()
        changed = original.copy()
        changed[0, 0] += 1
        save_events(store.data_path(doc.id, sample.id), changed)
        with pytest.raises(ValueError, match="integrity"):
            manager.apply_autospread(doc.id, job["id"], doc.revision)
        assert store.get(doc.id).revision == doc.revision
        save_events(store.data_path(doc.id, sample.id), original)

        def change(state):
            state.compensations[0].matrix[0][1] += 0.01

        updated = store.mutate(doc.id, "Matrix change", change, doc.revision)
        assert manager.get(updated, job["id"])["stale"]
        with pytest.raises(ConflictError, match="populations changed"):
            manager.apply_autospread(doc.id, job["id"], updated.revision)
        assert "autospread" not in store.get(doc.id).compensations[0].provenance
    finally:
        manager.close()


def test_queued_cancellation_keeps_no_report_and_preserves_workspace(store):
    doc, request, _ = fixture(store)
    manager = JobManager(store)
    try:
        job = manager.submit(doc, request)
        assert job["status"] == "queued"
        cancelled = manager.cancel(doc, job["id"])
        assert cancelled["status"] == "cancelled" and cancelled["result"] is None
        assert not (manager.directory / job["id"] / "result.json").exists()
        assert store.get(doc.id).model_dump_json() == doc.model_dump_json()
        with pytest.raises(ValueError, match="successful"):
            manager.apply_autospread(doc.id, job["id"], doc.revision)
    finally:
        manager.close()


def test_http_contract_review_export_staleness_and_project_roundtrip(client):
    store = client.app.state.store
    doc, request, _ = fixture(store, "spectral")
    base = f"/api/workspaces/{doc.id}"
    endpoint = base + "/compensations/autospread/jobs"
    assert (
        client.get(base + f"/compensations/{request.matrix_id}/spreading").json()["result"] is None
    )
    submitted = client.post(endpoint, json=request.model_dump())
    assert submitted.status_code == 202, submitted.text
    job_id = submitted.json()["id"]
    completed = wait_job(client.app.state.jobs, doc, job_id)
    assert completed["status"] == "succeeded", completed.get("error")
    assert client.get(base + "/jobs").json() == []
    report = client.get(endpoint + f"/{job_id}/report").json()
    assert report["stale"] is False and report["result"]["primaries"] == ["F1", "F2", "AF"]
    saved_response = client.post(endpoint + f"/{job_id}/apply", json={"revision": doc.revision})
    assert saved_response.status_code == 200, saved_response.text
    saved = saved_response.json()
    path = base + f"/compensations/{request.matrix_id}/spreading"
    assert client.get(path).json()["result"] == report["result"]
    csv_response = client.get(path + "/report?format=csv")
    rows = list(csv.reader(io.StringIO(csv_response.content.decode("utf-8-sig"))))
    assert rows[0] == ["Primary output / Secondary output", "F1", "F2", "AF"]
    assert rows[1][1] == "" and float(rows[1][2]) >= 0
    assert csv_response.headers["X-CytoForge-Stale"] == "false"
    assert (
        client.post(endpoint + f"/{job_id}/apply", json={"revision": saved["revision"]}).status_code
        == 422
    )
    assert client.get(base + f"/compensations/autospill/jobs/{job_id}").status_code == 422
    archive = client.get(base + "/export/project")
    assert archive.status_code == 200, archive.text
    imported = client.post(
        "/api/import/project",
        files={"file": ("spread.cytoforge", archive.content, "application/zip")},
    )
    assert imported.status_code == 200, imported.text
    imported_doc = imported.json()
    restored = client.get(
        f"/api/workspaces/{imported_doc['id']}/compensations/{request.matrix_id}/spreading"
    ).json()
    assert restored["result"] == report["result"] and restored["stale"] is False
    matrix = saved["compensations"][0]
    matrix["weights"][0] *= 2
    changed = client.post(
        base + "/compensations",
        json={"revision": saved["revision"], "compensation": matrix, "sample_ids": []},
    )
    assert changed.status_code == 200, changed.text
    assert client.get(path).json()["stale"] is True
    assert client.get(path + "/report").json()["stale"] is True
