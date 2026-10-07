import io

import flowio
import numpy as np
import pytest
from cytoforge.compensation import ControlCalculation, calculate_controls
from cytoforge.models import Channel, Compensation, DerivedParameter, Gate, Sample, Workspace
from cytoforge.science import Engine, compensate, save_events


def add_samples(store, arrays, names):
    doc = Workspace(name="Control references")
    for label, values in arrays.items():
        sample = Sample(
            name=label, event_count=len(values), channels=[Channel(name=n) for n in names]
        )
        sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
        doc.samples.append(sample)
    return store.create(doc)


def conventional(store):
    matrix = np.array([[1, 0.2], [0.1, 1]])
    background = np.array([10, 5])
    arrays = {
        "Unstained": np.tile(background, (120, 1)),
        "X stain": np.tile(np.array([100, 0]) @ matrix + background, (120, 1)),
        "Y stain": np.tile(np.array([0, 200]) @ matrix + background, (120, 1)),
    }
    doc = add_samples(store, arrays, ["X", "Y"])
    negative, x, y = doc.samples
    request = ControlCalculation(
        revision=doc.revision,
        name="Calculated spillover",
        detectors=["X", "Y"],
        controls=[
            {
                "name": "Y fluor",
                "primary_detector": "Y",
                "positive": {"sample_id": y.id},
                "negative": {"sample_id": negative.id},
            },
            {
                "name": "X fluor",
                "primary_detector": "X",
                "positive": {"sample_id": x.id},
                "negative": {"sample_id": negative.id},
            },
        ],
    )
    return doc, request, matrix


def test_median_controls_order_raw_gate_and_provenance(store):
    doc, request, expected = conventional(store)
    comp = Compensation(name="Already assigned", detectors=["X", "Y"], matrix=expected.tolist())
    x = doc.samples[1]
    gate = Gate(sample_id=x.id, name="Raw bright", kind="range", x="X", bounds=[109.9, 200])

    def change(state):
        state.compensations.append(comp)
        state.samples[1].compensation_id = comp.id
        state.gates.append(gate)

    doc = store.mutate(doc.id, "Assign existing matrix", change, doc.revision)
    engine = Engine(store)
    assert engine.mask(doc, doc.samples[1], gate.id).sum() == 0
    assert engine.mask(doc, doc.samples[1], gate.id, compensated=False).sum() == 120
    request.revision = doc.revision
    request.controls[1].positive.gate_id = gate.id
    result = calculate_controls(doc, request, engine)
    np.testing.assert_allclose(result["compensation"]["matrix"], expected, atol=1e-12)
    assert result["compensation"]["outputs"] == ["X", "Y"]
    assert result["diagnostics"]["rank"] == 2
    assert result["diagnostics"]["controls"][0]["positive"]["finite_count"] == 120
    provenance = result["compensation"]["provenance"]
    assert provenance["source_revision"] == doc.revision
    assert provenance["gates"][0]["id"] == gate.id
    assert provenance["samples"][0]["sha256"]
    assert result["warnings"] == []


def test_mixed_control_raw_thresholds_and_overlap_rejected(store):
    doc, request, expected = conventional(store)
    raw = Engine(store).raw(doc, doc.samples[0])
    values = np.vstack([raw, Engine(store).raw(doc, doc.samples[1])])
    mixed = add_samples(store, {"Mixed X": values}, ["X", "Y"])
    population = {"sample_id": mixed.samples[0].id}
    control = {
        "name": "X control",
        "primary_detector": "X",
        "positive": population | {"threshold_channel": "X", "minimum": 50},
        "negative": population | {"threshold_channel": "X", "maximum": 50},
    }
    # A one-detector conventional calculation still separates populations using
    # acquired X and uses all selected events, not the scatter preview subset.
    request = ControlCalculation(name="Mixed", revision=0, detectors=["X"], controls=[control])
    result = calculate_controls(mixed, request, Engine(store))
    assert result["compensation"]["matrix"] == [[1]]
    assert result["diagnostics"]["controls"][0]["negative"]["count"] == 120
    request.controls[0].negative.minimum = 40
    request.controls[0].negative.maximum = 200
    with pytest.raises(ValueError, match="overlap"):
        calculate_controls(mixed, request, Engine(store))


def test_control_rejects_small_and_unseparated_populations(store):
    doc, request, _ = conventional(store)
    request.min_events = 121
    with pytest.raises(ValueError, match="at least 121"):
        calculate_controls(doc, request, Engine(store))
    request.min_events = 100
    positive = request.controls[0].positive
    request.controls[0].positive = request.controls[0].negative.model_copy()
    request.controls[0].negative = positive
    with pytest.raises(ValueError, match="no positive separation"):
        calculate_controls(doc, request, Engine(store))


def test_control_rejects_virtual_gate_and_keeps_negative_spillover(store):
    doc, request, _ = conventional(store)
    sample = doc.samples[1]
    gate = Gate(sample_id=sample.id, name="Formula gate", kind="range", x="Ratio", bounds=[0, 100])

    def change(state):
        target = state.samples[1]
        target.channels.append(Channel(name="Ratio"))
        target.derived_parameters.append(
            DerivedParameter(name="Ratio", expression='ch("X") / ch("Y")')
        )
        state.gates.append(gate)

    doc = store.mutate(doc.id, "Add formula gate", change, doc.revision)
    request.controls[1].positive.gate_id = gate.id
    with pytest.raises(ValueError, match="control gates must use acquired"):
        calculate_controls(doc, request, Engine(store))
    request.controls[1].positive.gate_id = None
    values = Engine(store).raw(doc, doc.samples[1]).copy()
    values[:, 1] = 0
    doc.samples[1].sha256 = save_events(store.data_path(doc.id, sample.id), values)
    result = calculate_controls(doc, request, Engine(store))
    assert result["compensation"]["matrix"][0][1] == pytest.approx(-0.05)
    assert any("negative coefficients retained" in w for w in result["warnings"])


def spectral_fixture(store, af=True):
    spectra = np.array([[1, 0.2, 0.1, 0.05], [0.1, 1, 0.3, 0.2], [0.3, 0.2, 0.5, 1]])
    background = np.array([2, 3, 4, 5], dtype=float)
    unstained = np.tile(30 * spectra[2] + background, (120, 1))
    truth = np.array([[10, 20, 30], [-5, 0, 10], [0, 0, 0]], dtype=float)
    arrays = {
        "Unstained": unstained,
        "F1 control": unstained + 100 * spectra[0],
        "F2 control": unstained + 200 * spectra[1],
        "Mixture": truth @ spectra + background,
    }
    doc = add_samples(store, arrays, ["D1", "D2", "D3", "D4"])
    neg, f1, f2, mixture = doc.samples
    request = ControlCalculation(
        revision=doc.revision,
        name="Spectral controls",
        kind="spectral",
        detectors=["D1", "D2", "D3", "D4"],
        background=background.tolist(),
        weights=[1, 2, 0.5, 3],
        controls=[
            {
                "name": "Fluor 1",
                "positive": {"sample_id": f1.id},
                "negative": {"sample_id": neg.id},
            },
            {
                "name": "Fluor 2",
                "positive": {"sample_id": f2.id},
                "negative": {"sample_id": neg.id},
            },
        ],
        autofluorescence={"name": "AF", "population": {"sample_id": neg.id}} if af else None,
    )
    return doc, request, spectra, truth, mixture.id


def test_spectral_reference_af_and_weighted_background_solution(store):
    doc, request, expected, truth, sample_id = spectral_fixture(store)
    result = calculate_controls(doc, request, Engine(store))
    matrix = Compensation.model_validate(result["compensation"])
    np.testing.assert_allclose(matrix.matrix, expected, atol=1e-12)
    sample = next(s for s in doc.samples if s.id == sample_id)
    np.testing.assert_allclose(
        compensate(Engine(store).raw(doc, sample), matrix), truth, atol=1e-12
    )
    # Independent weighted normal equations on an overdetermined, noisy system.
    noisy = Engine(store).raw(doc, sample).copy()
    noisy[:, 2] += 7
    w = np.diag(matrix.weights)
    residual = noisy - np.array(matrix.background)
    normal = residual @ w @ expected.T @ np.linalg.inv(expected @ w @ expected.T)
    np.testing.assert_allclose(compensate(noisy, matrix), normal, atol=1e-12)
    noisy[0, 0] = np.nan
    assert np.isnan(compensate(noisy, matrix)[0]).all()
    assert result["diagnostics"]["output_units"].startswith("Peak-detector")


def test_rank_deficient_and_nonfinite_controls(store):
    doc, request, _, _, _ = spectral_fixture(store, af=False)
    request.controls[1].positive = request.controls[0].positive.model_copy()
    with pytest.raises(ValueError, match="rank deficient"):
        calculate_controls(doc, request, Engine(store))
    doc, request, _ = conventional(store)
    sample = doc.samples[1]
    raw = Engine(store).raw(doc, sample).copy()
    raw[0, 0] = np.nan
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), raw)
    result = calculate_controls(doc, request, Engine(store))
    assert result["diagnostics"]["controls"][0]["positive"]["finite_count"] == 119
    assert any("nonfinite" in w for w in result["warnings"])


def test_spectral_virtual_workflow_export_archive_undo_and_inactive_outputs(client):
    store = client.app.state.store
    doc, request, spectra, truth, sample_id = spectral_fixture(store)
    base = f"/api/workspaces/{doc.id}"
    response = client.post(base + "/compensations/calculate", json=request.model_dump())
    assert response.status_code == 200, response.text
    matrix = response.json()["compensation"]
    assert store.get(doc.id).revision == doc.revision  # Preview is read-only.
    saved = client.post(
        base + "/compensations",
        json={
            "revision": doc.revision,
            "compensation": matrix,
            "sample_ids": [sample_id],
        },
    )
    assert saved.status_code == 200, saved.text
    state = saved.json()
    sample = next(s for s in state["samples"] if s["id"] == sample_id)
    assert sample["unmixed_parameters"] == ["Fluor 1", "Fluor 2", "AF"]
    exported = client.get(base + f"/samples/{sample_id}/export?format=csv")
    values = np.loadtxt(io.StringIO(exported.text), delimiter=",", skiprows=1)
    np.testing.assert_allclose(values[:, 4:], truth, atol=1e-12)
    np.testing.assert_allclose(values[:, :4], truth @ spectra + request.background, atol=1e-12)
    fcs = client.get(base + f"/samples/{sample_id}/export?format=fcs")
    parsed = flowio.FlowData(io.BytesIO(fcs.content))
    assert parsed.channel_count == 7
    np.testing.assert_allclose(parsed.as_array()[:, 4:], truth, atol=1e-5)
    gate = Gate(
        sample_id=sample_id, name="Fluor 1 positive", kind="range", x="Fluor 1", bounds=[0.1, 100]
    ).model_dump()
    state = client.post(base + "/gates", json={"revision": state["revision"], "gate": gate}).json()
    assert client.get(base + f"/samples/{sample_id}/counts").json()[0]["count"] == 1
    expression = {"name": "Total fluor", "expression": 'ch("Fluor 1") + ch("Fluor 2")'}
    derived = client.post(
        base + "/derived",
        json={
            "revision": state["revision"],
            "parameter": expression,
            "sample_ids": [sample_id],
        },
    )
    assert derived.status_code == 200, derived.text
    state = derived.json()
    summary = client.get(base + f"/samples/{sample_id}/statistics?channel=Total%20fluor")
    assert summary.status_code == 200, summary.text
    assert summary.json()["median"] == pytest.approx(0, abs=1e-12)
    archive = client.get(base + "/export/project")
    restored = client.post(
        "/api/import/project",
        files={"file": ("spectral.cytoforge", archive.content, "application/zip")},
    )
    assert restored.status_code == 200, restored.text
    copy = restored.json()
    reexport = client.get(f"/api/workspaces/{copy['id']}/samples/{sample_id}/export")
    np.testing.assert_allclose(
        np.loadtxt(io.StringIO(reexport.text), delimiter=",", skiprows=1)[:, 4:7], truth, atol=1e-12
    )
    removed = client.post(
        base + "/compensations/assign",
        json={
            "revision": state["revision"],
            "compensation_id": None,
            "sample_ids": [sample_id],
        },
    )
    assert removed.status_code == 200, removed.text
    assert client.get(base + f"/samples/{sample_id}/counts").json()[0]["count"] == 0
    undefined = client.get(base + f"/samples/{sample_id}/export")
    assert np.isnan(np.loadtxt(io.StringIO(undefined.text), delimiter=",", skiprows=1)[:, 4:]).all()
    undo = client.post(base + "/undo", json={"revision": removed.json()["revision"]})
    assert undo.status_code == 200
    assert client.get(base + f"/samples/{sample_id}/counts").json()[0]["count"] == 1


def test_stale_preview_rejected_and_matrix_edit_updates_assigned_sample_outputs(client):
    store = client.app.state.store
    doc, request, _, _, sample_id = spectral_fixture(store)
    base = f"/api/workspaces/{doc.id}"
    preview = client.post(base + "/compensations/calculate", json=request.model_dump()).json()
    state = store.mutate(doc.id, "Rename", lambda d: setattr(d, "name", "Changed"), doc.revision)
    response = client.post(
        base + "/compensations",
        json={
            "revision": state.revision,
            "compensation": preview["compensation"],
            "sample_ids": [sample_id],
        },
    )
    assert response.status_code == 409
    request.revision = state.revision
    matrix = client.post(base + "/compensations/calculate", json=request.model_dump()).json()[
        "compensation"
    ]
    response = client.post(
        base + "/compensations",
        json={
            "revision": state.revision,
            "compensation": matrix,
            "sample_ids": [sample_id],
        },
    )
    state = response.json()
    matrix["outputs"][0] = "Renamed fluor"
    response = client.post(
        base + "/compensations",
        json={
            "revision": state["revision"],
            "compensation": matrix,
            "sample_ids": [],
        },
    )
    assert response.status_code == 200, response.text
    sample = next(s for s in response.json()["samples"] if s["id"] == sample_id)
    assert "Renamed fluor" in sample["unmixed_parameters"]
    summary = client.get(base + f"/samples/{sample_id}/statistics?channel=Fluor%201")
    assert summary.json()["finite_count"] == 0
    matrix["outputs"][0] = "D1"
    response = client.post(
        base + "/compensations",
        json={
            "revision": response.json()["revision"],
            "compensation": matrix,
            "sample_ids": [],
        },
    )
    assert response.status_code == 422 and "distinct names" in response.json()["detail"]
