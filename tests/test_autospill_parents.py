"""Acquired AutoSpill parents, reviewed QC flags and saved control reuse."""

import hashlib
import time

import numpy as np
import pytest
from cytoforge import autospill, autospread, quality
from cytoforge.compensation import assign_matrix
from cytoforge.models import (
    Channel,
    Compensation,
    Gate,
    GateDimension,
    QualityRequest,
    Sample,
    Transform,
    Workspace,
    new_id,
)
from cytoforge.science import Engine, save_array, save_events


def controls(store, *, kind="spillover", cleanup=False):
    doc = Workspace(name="Acquired control populations")
    names = ["D1", "D2"] + (["D3"] if kind == "spectral" else [])
    spectra = [[1, 0.18], [0.32, 1]] if kind == "spillover" else [[1, 0.18, 0.4], [1, 0.85, 0.12]]
    rng = np.random.default_rng(40916)
    acquired = names + ["Time"] + (["FSC-A", "SSC-A"] if cleanup else [])
    for index, spectrum in enumerate(spectra):
        intensity = rng.uniform(100, 24000, 2400)
        signals = intensity[:, None] * spectrum + rng.normal(0, 7, (len(intensity), len(names)))
        values = np.column_stack([signals, np.arange(len(signals)) / 1000])
        if cleanup:
            scatter = rng.normal([8000, 12000], [900, 1200], (len(values), 2))
            scatter[-200:] = rng.normal([1600, 2400], [200, 250], (200, 2))
            values = np.column_stack([values, scatter])
        sample = Sample(
            name=f"Source {index + 1}",
            event_count=len(values),
            channels=[Channel(name=name) for name in acquired],
        )
        sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
        doc.samples.append(sample)
    doc = store.create(doc)
    request = autospill.AutoSpillRequest(
        revision=doc.revision,
        kind=kind,
        detectors=names,
        auto_cleanup=cleanup,
        trim_fraction=0,
        controls=[
            dict(
                name=f"Source{i + 1}" if kind == "spectral" else names[i],
                primary_detector="D1" if kind == "spectral" else names[i],
                sample_id=s.id,
            )
            for i, s in enumerate(doc.samples)
        ],
    )
    return doc, request, Engine(store)


def ratio_parent(doc, request, engine, *, compensation_ref="sample", transformed=False):
    sample = doc.samples[0]
    values = engine.raw(doc, sample)
    with np.errstate(all="ignore"):
        ratio = np.clip(2 * (values[:, 0] - 50) / (values[:, 1] - 25), -0.5, 15)
    spec = Transform(kind="asinh", cofactor=3) if transformed else Transform(kind="linear")
    coordinate = np.arcsinh(ratio / 3) if transformed else ratio
    low, high = np.quantile(coordinate[np.isfinite(coordinate)], [0.05, 0.95])
    expected = (coordinate >= low) & (coordinate < high)
    gate = Gate(
        sample_id=sample.id,
        name="Acquired ratio",
        kind="hyperrectangle",
        dimensions=[
            GateDimension(
                channel="D1 / D2 (reviewed ratio)",
                ratio_channels=("D1", "D2"),
                compensation_ref=compensation_ref,
                ratio_a=2,
                ratio_b=50,
                ratio_c=25,
                ratio_bound_min=-0.5,
                ratio_bound_max=15,
                transform=spec,
                minimum=float(low),
                maximum=float(high),
            )
        ],
    )
    doc.gates.append(gate)
    request.controls[0].gate_id = gate.id
    return gate, expected


def reviewed_qc(doc, request, engine, *, parent=None):
    sample = doc.samples[0]
    qc_request = QualityRequest(
        revision=doc.revision,
        sample_id=sample.id,
        gate_id=parent.id if parent else None,
        time_channel="Time",
        channels=["D1"],
        compensated=False,
        use_transforms=False,
        bin_events=200,
    )
    result, flags = quality.calculate(doc, qc_request, engine, new_id())
    result.data.sha256 = save_array(engine.store.quality_path(doc.id, result.id), flags)
    doc.quality_results.append(result)
    gate = Gate(
        sample_id=sample.id,
        name="Reviewed QC",
        kind="quality",
        quality_id=result.id,
        parent_id=parent.id if parent else None,
        quality_excluded_bins=[0],
        quality_exclusions=["nonfinite", "time"],
    )
    doc.gates.append(gate)
    request.controls[0].gate_id = gate.id
    expected = quality.selection(flags, gate.quality_excluded_bins, gate.quality_exclusions)
    if parent:
        expected &= engine.mask(doc, sample, parent.id, compensated=False)
    return result, gate, flags, expected


@pytest.mark.parametrize("kind", ["spillover", "spectral"])
def test_explicit_acquired_dimensions_work_and_saved_masks_match(store, kind):
    doc, request, engine = controls(store, kind=kind)
    sample = doc.samples[0]
    values = engine.raw(doc, sample)[:, 0]
    gate = Gate(
        sample_id=sample.id,
        name="Acquired range",
        kind="hyperrectangle",
        dimensions=[GateDimension(channel="D1", minimum=1000, maximum=20000)],
    )
    doc.gates.append(gate)
    request.controls[0].gate_id = gate.id
    expected = (values >= 1000) & (values < 20000)
    result = autospill.calculate(doc, request, engine)
    assert result.diagnostics["controls"][0]["parent_count"] == int(expected.sum())
    created, populations = autospill.save_control_populations(doc, result)
    assert len(created) == 1
    assert populations[0]["output"] == autospill.output_names(request)[0]
    assert populations[1]["gate_id"] is None
    doc.compensations.append(result.compensation)
    assign_matrix(sample, result.compensation)
    doc.revision += 1
    np.testing.assert_array_equal(engine.mask(doc, sample, populations[0]["gate_id"]), expected)
    assert not autospill.is_stale(doc, result)


@pytest.mark.parametrize("transformed", [False, True])
@pytest.mark.parametrize("compensation_ref", ["sample", "FCS", "explicit"])
def test_inline_ratio_selection_uses_acquired_operands_and_survives_assignment(
    store, transformed, compensation_ref
):
    doc, request, engine = controls(store)
    matrix = Compensation(name="Prior matrix", detectors=["D1", "D2"], matrix=[[1, 0.6], [0.1, 1]])
    doc.compensations.append(matrix)
    assign_matrix(doc.samples[0], matrix)
    if compensation_ref == "explicit":
        compensation_ref = matrix.id
    gate, expected = ratio_parent(
        doc, request, engine, compensation_ref=compensation_ref, transformed=transformed
    )
    np.testing.assert_array_equal(engine.mask(doc, doc.samples[0], gate.id, False), expected)
    result = autospill.calculate(doc, request, engine)
    diagnostic = result.diagnostics["controls"][0]
    assert diagnostic["parent_count"] == int(expected.sum())
    assert diagnostic["used_count"] == int(expected.sum())
    assert np.all(expected[diagnostic["preview"]["event_ids"]])
    created, populations = autospill.save_control_populations(doc, result)
    saved = next(g for g in doc.gates if g.id == created[0])
    assert saved.dimensions[0].ratio_channels == ("D1", "D2")
    assert saved.dimensions[0].compensation_ref == "uncompensated"
    doc.compensations.append(result.compensation)
    assign_matrix(doc.samples[0], result.compensation)
    doc.revision += 1
    np.testing.assert_array_equal(engine.mask(doc, doc.samples[0], saved.id), expected)
    assert populations[0]["gate_id"] == saved.id
    assert not autospill.is_stale(doc, result)
    gate.dimensions[0].ratio_b += 1
    assert autospill.is_stale(doc, result)


def test_signed_zero_denominators_and_ratio_clipping_have_exact_event_identity(store):
    doc, request, engine = controls(store)
    sample = doc.samples[0]
    values = engine.raw(doc, sample).copy()
    values[:8, :2] = [
        [50, 25],
        [60, 25],
        [40, 25],
        [60, 15],
        [40, 15],
        [55, 35],
        [65, 35],
        [45, 35],
    ]
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    engine = Engine(store)
    gate, _ = ratio_parent(doc, request, engine)
    dim = gate.dimensions[0]
    dim.minimum, dim.maximum = -1, 1
    dim.ratio_bound_min, dim.ratio_bound_max = -1, 1
    with np.errstate(all="ignore"):
        ratio = np.clip(2 * (values[:, 0] - 50) / (values[:, 1] - 25), -1, 1)
    expected = (ratio >= -1) & (ratio < 1)
    np.testing.assert_array_equal(engine.mask(doc, sample, gate.id, False), expected)
    np.testing.assert_array_equal(
        expected[:8], [False, False, True, True, False, False, False, True]
    )
    # The narrow selection is intentionally too small for a regression, but its
    # identity must be valid scientific input rather than a rejected ratio type.
    autospill.validate_request(doc, request)


def test_boolean_ratio_dependency_changes_and_invalid_acquired_operands_rejected(store):
    doc, request, engine = controls(store)
    ratio, expected = ratio_parent(doc, request, engine)
    broad = Gate(
        sample_id=doc.samples[0].id,
        name="Raw threshold",
        kind="range",
        x="D1",
        bounds=[1000, 20000],
    )
    combined = Gate(
        sample_id=doc.samples[0].id,
        name="Reviewed controls",
        kind="boolean",
        operands=[ratio.id, broad.id],
        operation="and",
    )
    doc.gates.extend([broad, combined])
    request.controls[0].gate_id = combined.id
    expected &= engine.raw(doc, doc.samples[0])[:, 0] >= 1000
    expected &= engine.raw(doc, doc.samples[0])[:, 0] < 20000
    result = autospill.calculate(doc, request, engine)
    assert result.diagnostics["controls"][0]["parent_count"] == int(expected.sum())
    ids, populations = autospill.save_control_populations(doc, result)
    assert len(ids) == 3
    np.testing.assert_array_equal(
        engine.mask(doc, doc.samples[0], populations[0]["gate_id"]), expected
    )
    ratio.dimensions[0].ratio_c += 1
    assert autospill.is_stale(doc, result)
    ratio.dimensions[0].ratio_channels = ("D1", "Virtual source")
    with pytest.raises(ValueError, match="acquired"):
        autospill.validate_request(doc, request)
    ratio.dimensions[0].ratio_channels = ("D1", "D2")
    broad.sample_id = doc.samples[1].id
    with pytest.raises(ValueError, match="belong"):
        autospill.validate_request(doc, request)
    broad.sample_id = doc.samples[0].id
    ratio.parent_id = combined.id
    with pytest.raises(ValueError, match="cycle"):
        autospill.validate_request(doc, request)


@pytest.mark.parametrize("kind", ["spillover", "spectral"])
@pytest.mark.parametrize("cleanup", [False, True])
def test_reviewed_qc_inside_ratio_parent_is_captured_and_reusable(store, kind, cleanup):
    doc, request, engine = controls(store, kind=kind, cleanup=cleanup)
    ratio, _ = ratio_parent(doc, request, engine, transformed=True)
    qc, gate, _, expected = reviewed_qc(doc, request, engine, parent=ratio)
    result = autospill.calculate(doc, request, engine)
    assert result.diagnostics["controls"][0]["parent_count"] == int(expected.sum())
    assert np.all(expected[result.diagnostics["controls"][0]["preview"]["event_ids"]])
    assert result.input_snapshot["qc"][qc.id]["data"]["sha256"] == qc.data.sha256
    created, populations = autospill.save_control_populations(doc, result)
    captured = next(g for g in doc.gates if g.id in created and g.kind == "quality")
    assert captured.parent_id != ratio.id
    assert quality.is_captured_gate(captured, qc)
    doc.compensations.append(result.compensation)
    for sample in doc.samples:
        assign_matrix(sample, result.compensation)
    doc.revision += 1
    assert quality.is_stale(doc, qc)
    assert not autospill.is_stale(doc, result)
    saved_mask = engine.mask(doc, doc.samples[0], populations[0]["gate_id"])
    if cleanup:
        assert int(saved_mask.sum()) == result.diagnostics["controls"][0]["cleanup_count"]
        assert np.all(~saved_mask | expected)
    else:
        np.testing.assert_array_equal(saved_mask, expected)
    # A new calculation cannot silently reinterpret a stale live QC result.
    with pytest.raises(ValueError, match="stale"):
        autospill.validate_request(doc, request)
    reused = request.model_copy(deep=True)
    for c, p in zip(reused.controls, populations, strict=True):
        c.gate_id = p["gate_id"]
    repeated = autospill.calculate(doc, reused, Engine(store))
    if not cleanup:
        np.testing.assert_allclose(
            repeated.compensation.matrix, result.compensation.matrix, atol=2e-12
        )
    spreading = autospread.AutoSpreadRequest(
        revision=doc.revision,
        matrix_id=result.compensation.id,
        controls=populations,
        events_per_bin=20,
        quantiles=32,
    )
    reviewed = autospread.calculate(doc, spreading, Engine(store))
    assert reviewed.controls[0]["parent_count"] == int(saved_mask.sum())
    assert qc.id in reviewed.input_snapshot["qc"]
    gate.quality_excluded_bins = [1]
    assert autospill.is_stale(doc, result)
    # Saved copies remain independent of later edits to the source selection.
    assert not autospread.is_stale(doc, reviewed)


@pytest.mark.parametrize("operation", ["calculate", "preview", "verify"])
def test_qc_flag_corruption_is_rejected_even_with_warm_mask_cache(store, operation):
    doc, request, engine = controls(store)
    qc, gate, flags, _ = reviewed_qc(doc, request, engine)
    result = autospill.calculate(doc, request, engine)
    engine.mask(doc, doc.samples[0], gate.id, False)
    damaged = flags.copy()
    damaged[0, 0] ^= 1
    save_array(store.quality_path(doc.id, qc.id), damaged)
    with pytest.raises(ValueError, match="integrity"):
        if operation == "calculate":
            autospill.calculate(doc, request, engine)
        elif operation == "preview":
            autospill.preview(doc, result, engine, "D1", "D2")
        else:
            autospill.verify_control_data(doc, request, store)


def test_captured_qc_metadata_substitution_is_rejected_before_cached_masks(store):
    doc, request, engine = controls(store)
    qc, _, _, _ = reviewed_qc(doc, request, engine)
    result = autospill.calculate(doc, request, engine)
    _, populations = autospill.save_control_populations(doc, result)
    captured_id = populations[0]["gate_id"]
    engine.mask(doc, doc.samples[0], captured_id, False)
    qc.data.sha256 = "0" * 64
    with pytest.raises(ValueError, match="Captured QC event flags changed"):
        engine.mask(doc, doc.samples[0], captured_id, False)
    reused = request.model_copy(deep=True)
    reused.controls[0].gate_id = captured_id
    with pytest.raises(ValueError, match="Captured QC event flags changed"):
        autospill.validate_request(doc, reused)


def test_qc_owner_missing_bins_and_changed_dependencies_are_rejected(store):
    doc, request, engine = controls(store)
    qc, gate, _, _ = reviewed_qc(doc, request, engine)
    result = autospill.calculate(doc, request, engine)
    gate.quality_excluded_bins = [len(qc.bins)]
    with pytest.raises(ValueError, match="missing acquisition bin"):
        autospill.validate_request(doc, request)
    assert autospill.is_stale(doc, result)
    gate.quality_excluded_bins = [0]
    qc.request.sample_id = doc.samples[1].id
    with pytest.raises(ValueError, match="another sample"):
        autospill.validate_request(doc, request)
    qc.request.sample_id = doc.samples[0].id
    doc.samples[0].sha256 = "0" * 64
    with pytest.raises(ValueError, match="stale"):
        autospill.validate_request(doc, request)


def wait_job(client, base, identifier):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        job = client.get(f"{base}/jobs/{identifier}").json()
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.04)
    raise AssertionError("Owned AutoSpill parent worker did not finish")


@pytest.mark.parametrize("kind", ["spillover", "spectral"])
def test_actual_worker_saves_captured_qc_archive_reuse_and_undo(client, kind):
    store = client.app.state.store
    doc, request, engine = controls(store, kind=kind)
    ratio, _ = ratio_parent(doc, request, engine, transformed=True)
    qc, _, _, expected = reviewed_qc(doc, request, engine, parent=ratio)
    doc = store.mutate(
        doc.id,
        "Reviewed control populations",
        lambda d: (
            setattr(d, "gates", doc.gates),
            setattr(d, "quality_results", doc.quality_results),
        ),
        doc.revision,
    )
    request.revision = doc.revision
    original = doc.model_dump_json()
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
    matrix = saved.compensations[0]
    populations = matrix.provenance["control_populations"]
    assert [p["output"] for p in populations] == matrix.outputs
    assert len(matrix.provenance["cleanup_gate_ids"]) == 2
    np.testing.assert_array_equal(
        Engine(store).mask(saved, saved.samples[0], populations[0]["gate_id"]), expected
    )
    assert quality.is_stale(saved, qc)
    assert not client.get(f"{base}/jobs/{job['id']}").json()["stale"]
    payload = dict(
        revision=saved.revision,
        result_id=matrix.id,
        primary=matrix.outputs[0],
        secondary=matrix.outputs[1],
    )
    preview = client.post(base + "/preview", json=payload)
    assert preview.status_code == 200, preview.text
    archived = client.get(f"/api/workspaces/{doc.id}/export/project")
    assert archived.status_code == 200
    restored = client.post(
        "/api/import/project", files={"file": ("captured.cytoforge", archived.content)}
    )
    assert restored.status_code == 200, restored.text
    imported = Workspace.model_validate(restored.json())
    imported_matrix = imported.compensations[0]
    imported_populations = imported_matrix.provenance["control_populations"]
    # Reload from persistent storage and a new Engine to avoid live object/cache shortcuts.
    imported = store.get(imported.id)
    np.testing.assert_array_equal(
        Engine(store).mask(imported, imported.samples[0], imported_populations[0]["gate_id"]),
        expected,
    )
    assert not autospill.is_stale(imported, autospill.saved_result(imported_matrix))
    spread_base = f"/api/workspaces/{imported.id}/compensations/autospread"
    spreading = autospread.AutoSpreadRequest(
        revision=imported.revision,
        matrix_id=imported_matrix.id,
        controls=imported_populations,
        events_per_bin=20,
        quantiles=32,
    )
    response = client.post(spread_base + "/jobs", json=spreading.model_dump())
    assert response.status_code == 202, response.text
    spread = wait_job(client, spread_base, response.json()["id"])
    assert spread["status"] == "succeeded", spread.get("error")
    assert spread["result"]["controls"][0]["parent_count"] == int(expected.sum())
    undone = client.post(f"/api/workspaces/{doc.id}/undo", json=dict(revision=saved.revision))
    assert undone.status_code == 200, undone.text
    restored_doc = Workspace.model_validate(undone.json())
    original_doc = Workspace.model_validate_json(original)
    assert restored_doc.gates == original_doc.gates
    assert restored_doc.quality_results == original_doc.quality_results
    assert not restored_doc.compensations
    for sample in restored_doc.samples:
        assert (
            hashlib.sha256(store.data_path(doc.id, sample.id).read_bytes()).hexdigest()
            == sample.sha256
        )


def test_worker_apply_qc_corruption_leaves_workspace_unchanged(client):
    store = client.app.state.store
    doc, request, engine = controls(store)
    qc, _, flags, _ = reviewed_qc(doc, request, engine)
    doc = store.mutate(
        doc.id,
        "Review QC",
        lambda d: (
            setattr(d, "gates", doc.gates),
            setattr(d, "quality_results", doc.quality_results),
        ),
        doc.revision,
    )
    request.revision = doc.revision
    original = store.get(doc.id).model_dump_json()
    base = f"/api/workspaces/{doc.id}/compensations/autospill"
    response = client.post(base + "/jobs", json=request.model_dump())
    assert response.status_code == 202, response.text
    job = wait_job(client, base, response.json()["id"])
    assert job["status"] == "succeeded", job.get("error")
    flags[0, 0] ^= 1
    save_array(store.quality_path(doc.id, qc.id), flags)
    response = client.post(
        f"{base}/jobs/{job['id']}/apply",
        json=dict(
            revision=doc.revision,
            sample_ids=[doc.samples[0].id],
            save_cleanup_gates=True,
            acknowledge_unconverged=True,
        ),
    )
    assert response.status_code == 422 and "integrity" in response.text
    assert store.get(doc.id).model_dump_json() == original
