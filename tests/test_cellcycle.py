"""DNA models checked against latent labels and independent numerical integrals."""

import csv
import io
import json
import math
import time
import zipfile

import numpy as np
import pytest
from cytoforge import cellcycle
from cytoforge.models import (
    AnalysisInput,
    CellCycleRequest,
    Channel,
    FitConstraint,
    Gate,
    Sample,
    Transform,
    Workspace,
    new_id,
)
from cytoforge.science import Engine, save_array, save_events
from scipy.integrate import quad
from scipy.special import ndtr


def dna_events(seed=29, size=40000, mean=100, fractions=(0.55, 0.30, 0.15), sync=False):
    """Draw latent biological DNA then independent multiplicative measurement noise."""
    rng = np.random.default_rng(seed)
    phases = rng.choice(3, size=size, p=fractions)
    measured = np.empty(size)
    g1, s, g2 = phases == 0, phases == 1, phases == 2
    measured[g1] = rng.normal(mean, 0.04 * mean, g1.sum())
    measured[g2] = rng.normal(1.98 * mean, 0.05 * 1.98 * mean, g2.sum())
    accepted = []
    while sum(map(len, accepted)) < s.sum():
        proposal = rng.uniform(0, 1, s.sum() * 2)
        q = 1 + 0.6 * proposal + 1.2 * proposal**2
        accepted.append(proposal[rng.uniform(0, 2.8, len(proposal)) < q])
    t = np.concatenate(accepted)[: s.sum()]
    if sync:
        wave = rng.random(len(t)) < 0.7
        t[wave] = np.clip(rng.normal(0.64, 0.055, wave.sum()), 0, 1)
    true_dna = mean * (1 + 0.98 * t)
    measured[s] = rng.normal(true_dna, true_dna * 0.04)
    return measured, phases


def saved_sample(store, values, name="DNA singlets"):
    events = np.c_[values, np.arange(len(values))]
    sample = Sample(
        name=name,
        event_count=len(events),
        channels=[Channel(name="DNA", range=300), Channel(name="Time")],
    )
    workspace = Workspace(name="Cell cycle", samples=[sample])
    sample.sha256 = save_events(store.data_path(workspace.id, sample.id), events)
    workspace = store.create(workspace)
    return workspace, workspace.samples[0], Engine(store)


def request_for(workspace, sample, **settings):
    inputs = settings.pop("inputs", [AnalysisInput(sample_id=sample.id)])
    return CellCycleRequest(
        revision=workspace.revision,
        name="DNA model",
        channel="DNA",
        inputs=inputs,
        range_max=280.0,
        **settings,
    )


@pytest.mark.parametrize("sync", [False, True])
def test_djf_recovers_independently_generated_latent_labels(store, sync):
    values, labels = dna_events(sync=sync)
    workspace, sample, engine = saved_sample(store, values)
    request = request_for(workspace, sample, synchronous_s=sync)
    result, arrays = cellcycle.calculate(workspace, request, engine, new_id())
    fit = result.fits[0]
    true_fractions = np.bincount(labels, minlength=3) / len(labels)
    np.testing.assert_allclose(fit.fractions, true_fractions, atol=0.017)
    assert fit.parameters["g1_mean"] == pytest.approx(100, abs=0.5)
    assert fit.parameters["g2_mean"] == pytest.approx(198, abs=1.2)
    assert fit.parameters["g1_cv"] == pytest.approx(4, abs=0.35)
    assert fit.parameters["g2_cv"] == pytest.approx(5, abs=0.5)
    assert fit.diagnostics["converged"]
    assert fit.diagnostics["poisson_deviance"] < 380
    assert sum(fit.assigned_counts) == len(values)
    np.testing.assert_allclose(arrays[sample.id][:, :3].sum(axis=1), 1, atol=1e-6)
    data = fit.data
    data.sha256 = save_array(
        store.analysis_path(workspace.id, result.id, sample.id), arrays[sample.id]
    )
    np.testing.assert_array_equal(
        cellcycle.load_data(store, workspace.id, result, data), arrays[sample.id]
    )


def test_djf_convolution_agrees_with_independent_adaptive_quadrature():
    edges = np.linspace(30, 290, 101)
    peaks = [100, 197, 3.7, 4.2]
    shape = [0.65, 0.2]
    b0 = (1 - shape[1]) * math.cos(shape[0]) ** 2
    b2 = (1 - shape[1]) * math.sin(shape[0]) ** 2
    b1 = shape[1] - (1 - shape[1]) * math.sin(shape[0]) * math.cos(shape[0])
    normalizer = (b0 + b1 + b2) / 3

    def density(t):
        return (b0 * (1 - t) ** 2 + 2 * b1 * t * (1 - t) + b2 * t**2) / normalizer

    actual, coverage = cellcycle.djf_components(edges, peaks, shape, order=256)
    expected = []
    for lower, upper in zip(edges[:-1], edges[1:], strict=True):

        def integrand(t, upper=upper, lower=lower):
            dna = 100 + 97 * t
            sd = 0.037 * dna
            return density(t) * (ndtr((upper - dna) / sd) - ndtr((lower - dna) / sd))

        expected.append(quad(integrand, 0, 1, epsabs=1e-11)[0])
    expected = np.array(expected)
    np.testing.assert_allclose(actual[1], expected / expected.sum(), atol=1e-10)
    assert coverage.min() > 0.999999
    # This includes a negative Bernstein middle coefficient, which a mixture of
    # three nonnegative basis coefficients would incorrectly prohibit.
    assert b1 < 0


def test_s_quadratic_can_vanish_at_both_endpoints_without_approximation():
    edges = np.linspace(0, 300, 97)
    actual, _ = cellcycle.djf_components(edges, [100, 200, 4, 4], [math.pi / 4, 1])
    expected = []
    for lower, upper in zip(edges[:-1], edges[1:], strict=True):

        def integrand(t, lower=lower, upper=upper):
            dna = 100 + 100 * t
            return (
                6
                * t
                * (1 - t)
                * (ndtr((upper - dna) / (0.04 * dna)) - ndtr((lower - dna) / (0.04 * dna)))
            )

        expected.append(quad(integrand, 0, 1, epsabs=1e-11)[0])
    expected = np.array(expected)
    np.testing.assert_allclose(actual[1], expected / expected.sum(), atol=1e-10)


def test_unvalidated_watson_refinement_cannot_run_as_a_scientific_analysis(store):
    values, _ = dna_events(size=4000)
    workspace, sample, engine = saved_sample(store, values)
    request = request_for(workspace, sample, method="watson", bins=512)
    with pytest.raises(ValueError, match="Watson refinement is still"):
        cellcycle.calculate(workspace, request, engine, new_id())


def test_exact_fixed_mean_ratio_and_linked_cv_constraints(store):
    values, _ = dna_events(size=16000)
    workspace, sample, engine = saved_sample(store, values)
    request = request_for(
        workspace,
        sample,
        g1_mean=FitConstraint(fixed=100),
        peak_ratio=FitConstraint(fixed=1.98),
        g1_cv=FitConstraint(fixed=4),
        linked_cv="g2_to_g1",
    )
    result, _ = cellcycle.calculate(workspace, request, engine, new_id())
    peaks = result.fits[0].parameters
    assert peaks["g1_mean"] == 100
    assert peaks["g2_mean"] == 198
    assert peaks["peak_ratio"] == 1.98
    assert peaks["g1_cv"] == peaks["g2_cv"] == 4


def test_fit_constraints_reject_infeasible_mean_ranges():
    request = CellCycleRequest(
        revision=0,
        channel="DNA",
        inputs=[AnalysisInput(sample_id=new_id())],
        g1_mean=FitConstraint(fixed=100),
        g2_mean=FitConstraint(fixed=150),
        peak_ratio=FitConstraint(fixed=2),
    )
    with pytest.raises(ValueError, match="feasible"):
        cellcycle.PeakParameters(request, 300, np.linspace(0, 1, 257), [1 / 3, 2 / 3, 4, 5])


def test_nonfinite_negative_and_out_of_range_events_keep_original_identity(store):
    values, _ = dna_events(size=8000)
    values = np.r_[np.nan, values, -1, 999, np.inf]
    workspace, sample, engine = saved_sample(store, values)
    result, arrays = cellcycle.calculate(
        workspace, request_for(workspace, sample), engine, new_id()
    )
    fit, output = result.fits[0], arrays[sample.id]
    assert fit.data.population_count == 8004
    assert fit.data.finite_count == 8002
    assert fit.data.fitted_count == 8000
    assert np.isnan(output[[0, -1, -2, -3]]).all()
    assert np.isfinite(output[1:8001]).all()
    assert fit.diagnostics["excluded_nonfinite"] == 2
    assert fit.diagnostics["excluded_negative"] == 1
    assert fit.diagnostics["excluded_above_range"] == 1


@pytest.mark.parametrize("values", [np.ones(1000) * 100, np.ones(199) * 100, np.zeros(1000)])
def test_empty_small_or_constant_dna_is_rejected(store, values):
    workspace, sample, engine = saved_sample(store, values)
    with pytest.raises(ValueError, match="200|variation|16 histogram"):
        cellcycle.calculate(workspace, request_for(workspace, sample), engine, new_id())


def submit_and_wait(client, workspace, sample, **settings):
    request = request_for(workspace, sample, **settings)
    base = f"/api/workspaces/{workspace.id}/cell-cycle"
    response = client.post(f"{base}/jobs", json=request.model_dump())
    assert response.status_code == 202, response.text
    identifier = response.json()["id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = client.get(f"{base}/jobs/{identifier}")
        assert response.status_code == 200, response.text
        record = response.json()
        if record["status"] not in {"queued", "running"}:
            assert record["status"] == "succeeded", record
            return base, identifier, record
        time.sleep(0.02)
    pytest.fail("Cell-cycle worker did not finish")


def test_cell_cycle_worker_review_plot_exports_archive_and_undo(client):
    values, _ = dna_events(size=6000)
    values = np.r_[values, np.nan, -5, 999]
    store = client.app.state.store
    workspace, sample, _ = saved_sample(store, values)
    base, identifier, job = submit_and_wait(client, workspace, sample)
    fit = job["result"]["fits"][0]
    assert job["can_apply"] and not job["stale"]
    assert client.get(f"/api/workspaces/{workspace.id}/jobs").json() == []
    assert client.get(f"/api/workspaces/{workspace.id}/quality/jobs").json() == []
    summary = client.get(f"{base}/jobs").json()[0]
    assert summary["result"]["fits"][0]["observed"] == []
    stats = client.get(f"{base}/{identifier}/statistics")
    rows = list(csv.DictReader(io.StringIO(stats.text)))
    assert int(rows[0]["fitted_events"]) == 6000
    assert float(rows[0]["G0/G1_fraction"]) == fit["fractions"][0]
    events = client.get(f"{base}/{identifier}/events?sample_id={sample.id}")
    rows = list(csv.DictReader(io.StringIO(events.text)))
    assert len(rows) == 6003 and int(rows[-1]["event_id"]) == 6002
    assert rows[-1]["G0_G1_probability"] == "nan"
    assert rows[0]["assigned_phase"] in {"1", "2", "3"}
    figure = client.get(f"{base}/{identifier}/figure?sample_id={sample.id}")
    assert figure.status_code == 200 and b"<svg" in figure.content
    assert b"linear intensity" in figure.content and b"assigned" in figure.content
    assert (
        client.get(f"{base}/{identifier}/report").json()["input_hash"]
        == job["result"]["input_hash"]
    )
    response = client.post(f"{base}/jobs/{identifier}/apply", json={"revision": workspace.revision})
    assert response.status_code == 200, response.text
    saved = Workspace.model_validate(response.json())
    engine = client.app.state.engine
    output = np.c_[
        [engine.column(saved, saved.samples[0], c) for c in saved.cell_cycle_results[0].columns]
    ].T
    assert output.shape == (6003, 4)
    assert np.isnan(output[-3:]).all()
    counts = [engine.mask(saved, saved.samples[0], g.id).sum() for g in saved.gates]
    assert counts == fit["assigned_counts"] and sum(counts) == 6000
    raw = engine.raw(saved, saved.samples[0])
    assert raw.shape == (6003, 2)
    exported = client.get(f"/api/workspaces/{workspace.id}/samples/{sample.id}/export?format=fcs")
    assert exported.status_code == 200
    from cytoforge.science import parse_fcs

    fcs_path = store.root / "dna-export.fcs"
    fcs_path.write_bytes(exported.content)
    fcs_sample, fcs_values, _, _ = parse_fcs(fcs_path, "DNA export")
    assert len(fcs_sample.channels) == 6
    np.testing.assert_allclose(fcs_values[:, 2:], output, rtol=2e-6, atol=2e-6, equal_nan=True)
    archived = client.get(f"/api/workspaces/{workspace.id}/export/project")
    assert archived.status_code == 200, archived.text[:200] if archived.status_code != 200 else ""
    with zipfile.ZipFile(io.BytesIO(archived.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["version"] == 2
        assert set(manifest["workspace"]["cell_cycle_results"][0]) == {"id", "sha256"}
    response = client.post(
        "/api/import/project", files={"file": ("dna.cytoforge", archived.content)}
    )
    assert response.status_code == 200, response.text
    restored = Workspace.model_validate(response.json())
    actual = np.c_[
        [
            engine.column(restored, restored.samples[0], c)
            for c in restored.cell_cycle_results[0].columns
        ]
    ].T
    np.testing.assert_array_equal(actual, output)
    assert not client.get(f"/api/workspaces/{restored.id}/cell-cycle/{identifier}").json()["stale"]
    undone = client.post(
        f"/api/workspaces/{workspace.id}/undo", json={"revision": saved.revision}
    ).json()
    assert undone["cell_cycle_results"] == [] and len(undone["samples"][0]["channels"]) == 2
    redone = client.post(
        f"/api/workspaces/{workspace.id}/redo", json={"revision": undone["revision"]}
    ).json()
    assert len(redone["cell_cycle_results"]) == 1
    assert len(redone["gates"]) == 3


def test_source_gate_growth_does_not_reclassify_unfitted_events_and_stale_apply(client):
    values, _ = dna_events(size=7000)
    store = client.app.state.store
    workspace, sample, _ = saved_sample(store, values)
    gate = Gate(
        sample_id=sample.id, name="First acquisition", kind="range", x="Time", bounds=[0, 5000]
    )
    workspace = store.mutate(
        workspace.id, "Gate source", lambda doc: doc.gates.append(gate), workspace.revision
    )
    base, identifier, job = submit_and_wait(client, workspace, sample)
    # Refit only the chosen source, preserving identity when its parent later expands.
    request = request_for(workspace, sample)
    request.inputs[0].gate_id = gate.id
    response = client.post(f"{base}/jobs", json=request.model_dump())
    assert response.status_code == 202
    source_id = response.json()["id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        fitted = client.get(f"{base}/jobs/{source_id}").json()
        if fitted["status"] not in {"queued", "running"}:
            break
        time.sleep(0.02)
    assert fitted["status"] == "succeeded", fitted
    response = client.post(f"{base}/jobs/{source_id}/apply", json={"revision": workspace.revision})
    assert response.status_code == 200, response.text
    saved = Workspace.model_validate(response.json())
    expanded = gate.model_copy(update={"bounds": [0, 10000]})
    changed = client.put(
        f"/api/workspaces/{workspace.id}/gates/{gate.id}",
        json={"revision": saved.revision, "gate": expanded.model_dump()},
    )
    assert changed.status_code == 200, changed.text
    doc = Workspace.model_validate(changed.json())
    assert client.get(f"{base}/{source_id}").json()["stale"]
    engine = client.app.state.engine
    assert (
        sum(engine.mask(doc, doc.samples[0], g.id).sum() for g in doc.gates if g.id != gate.id)
        == 5000
    )
    assert np.isnan(
        engine.column(doc, doc.samples[0], doc.cell_cycle_results[0].columns[3])[5000:]
    ).all()
    # Cosmetic revision changes alone remain compatible with the original job.
    assert not client.get(f"{base}/jobs/{identifier}").json()["stale"]
    assert (
        client.post(f"{base}/jobs/{identifier}/apply", json={"revision": doc.revision}).status_code
        == 422
    )  # duplicate output names


def test_dna_display_transform_does_not_change_fit_fingerprint(store):
    values, _ = dna_events(size=2000)
    workspace, sample, engine = saved_sample(store, values)
    request = request_for(workspace, sample)
    result, _ = cellcycle.calculate(workspace, request, engine, new_id())
    workspace.samples[0].channels[0].transform = Transform(kind="logicle")
    assert not cellcycle.is_stale(workspace, result)
    assert result.fits[0].diagnostics["parameter_basis"] == "Linear compensated intensity"


def test_large_finite_dna_values_do_not_overflow_model_or_figure(store):
    values, _ = dna_events(size=4000)
    workspace, sample, engine = saved_sample(store, values * 4e305)
    request = CellCycleRequest(
        revision=workspace.revision,
        channel="DNA",
        inputs=[AnalysisInput(sample_id=sample.id)],
        range_max=1.12e308,
    )
    result, _ = cellcycle.calculate(workspace, request, engine, new_id())
    assert result.fits[0].parameters["g1_mean"] == pytest.approx(4e307, rel=0.01)
    assert all(math.isfinite(value) for value in result.fits[0].parameters.values())
    figure = cellcycle.figure_svg(result, result.fits[0], "Large DNA")
    assert b"nan" not in figure and b"inf" not in figure


def test_saved_batch_survives_removing_one_input_sample_and_archive_restore(client):
    store = client.app.state.store
    values, _ = dna_events(size=3500)
    workspace, sample, _ = saved_sample(store, values)
    other_values, _ = dna_events(mean=125, size=4000)
    other = Sample(
        name="Second DNA sample", event_count=len(other_values), channels=sample.channels
    )
    other.sha256 = save_events(
        store.data_path(workspace.id, other.id), np.c_[other_values, np.arange(len(other_values))]
    )
    workspace = store.mutate(
        workspace.id, "Add batch sample", lambda doc: doc.samples.append(other), workspace.revision
    )
    base, identifier, job = submit_and_wait(
        client,
        workspace,
        sample,
        inputs=[AnalysisInput(sample_id=sample.id), AnalysisInput(sample_id=other.id)],
    )
    means = [fit["parameters"]["g1_mean"] for fit in job["result"]["fits"]]
    np.testing.assert_allclose(means, [100, 125], atol=1)
    saved = client.post(
        f"{base}/jobs/{identifier}/apply", json={"revision": workspace.revision}
    ).json()
    removed = client.delete(
        f"/api/workspaces/{workspace.id}/samples/{sample.id}?revision={saved['revision']}"
    )
    assert removed.status_code == 200, removed.text
    doc = Workspace.model_validate(removed.json())
    assert len(doc.samples) == 1 and len(doc.cell_cycle_results[0].fits) == 2
    assert client.get(f"{base}/{identifier}").json()["stale"]
    export = client.get(f"/api/workspaces/{workspace.id}/export/project")
    assert export.status_code == 200
    restored = client.post(
        "/api/import/project", files={"file": ("batch.cytoforge", export.content)}
    )
    assert restored.status_code == 200, restored.text
    assert len(restored.json()["cell_cycle_results"][0]["fits"]) == 2
    assert restored.json()["samples"][0]["id"] == other.id


@pytest.mark.parametrize(
    "corruption", ["report_hash", "probability_normalization", "assigned_phase"]
)
def test_corrupt_cell_cycle_archives_are_rejected_before_workspace_commit(client, corruption):
    store = client.app.state.store
    values, _ = dna_events(size=3000)
    workspace, sample, _ = saved_sample(store, values)
    base, identifier, _ = submit_and_wait(client, workspace, sample)
    saved = client.post(f"{base}/jobs/{identifier}/apply", json={"revision": workspace.revision})
    assert saved.status_code == 200, saved.text
    exported = client.get(f"/api/workspaces/{workspace.id}/export/project")
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    report_name = f"cell-cycle/{identifier}.json"
    if corruption == "report_hash":
        entries[report_name] += b" "
    else:
        array_name = f"cell-cycle/{identifier}/{sample.id}.npy"
        output = np.load(io.BytesIO(entries[array_name]), allow_pickle=False)
        if corruption == "probability_normalization":
            output[0, :3] = 0
        else:
            output[0, 3] = 4
        buffer = io.BytesIO()
        np.save(buffer, output, allow_pickle=False)
        entries[array_name] = buffer.getvalue()
        report = json.loads(entries[report_name])
        import hashlib

        report["fits"][0]["data"]["sha256"] = hashlib.sha256(entries[array_name]).hexdigest()
        entries[report_name] = json.dumps(report).encode()
        manifest = json.loads(entries["manifest.json"])
        manifest["workspace"]["cell_cycle_results"][0]["sha256"] = hashlib.sha256(
            entries[report_name]
        ).hexdigest()
        entries["manifest.json"] = json.dumps(manifest).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    before = {item["id"] for item in client.get("/api/workspaces").json()}
    response = client.post(
        "/api/import/project", files={"file": ("invalid.cytoforge", buffer.getvalue())}
    )
    assert response.status_code == 422, response.text
    assert {item["id"] for item in client.get("/api/workspaces").json()} == before
