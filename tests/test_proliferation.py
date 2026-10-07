"""Generation fits tested against independent latent sampling and adaptive integrals."""

import csv
import hashlib
import io
import json
import math
import time
import zipfile

import numpy as np
import pytest
from cytoforge import proliferation
from cytoforge.models import (
    AnalysisInput,
    Channel,
    FitConstraint,
    Gate,
    ProliferationRequest,
    Sample,
    Transform,
    Workspace,
    new_id,
    proliferation_statistics,
)
from cytoforge.science import Engine, parse_fcs, save_array, save_events
from scipy.integrate import quad
from scipy.special import ndtr


def dye_events(
    seed=61,
    size=30000,
    fractions=(0.12, 0.18, 0.25, 0.22, 0.15, 0.08),
    mean=1044,
    ratio=0.5,
    cv=25,
    background=20,
    background_sd=0,
    distribution="lognormal",
):
    rng = np.random.default_rng(seed)
    labels = rng.choice(len(fractions), size=size, p=fractions)
    dye = (mean - background) * ratio**labels
    if distribution == "lognormal":
        dye *= rng.lognormal(0, math.sqrt(math.log1p((cv / 100) ** 2)), size)
    else:
        dye += rng.normal(0, dye * cv / 100)
    values = dye + rng.normal(background, background_sd, size)
    return values, labels


def experiment(store, *values, names=None):
    samples = [
        Sample(
            name=names[i] if names else f"Dye {i}",
            event_count=len(v),
            channels=[Channel(name="CFSE", range=4096), Channel(name="Time")],
        )
        for i, v in enumerate(values)
    ]
    workspace = Workspace(name="Generation validation", samples=samples)
    for sample, v in zip(samples, values, strict=True):
        sample.sha256 = save_events(
            store.data_path(workspace.id, sample.id), np.c_[v, np.arange(len(v))]
        )
    workspace = store.create(workspace)
    return workspace, workspace.samples, Engine(store)


def request_for(workspace, sample, **settings):
    options = dict(
        undivided_mean=FitConstraint(initial=1044),
        background=20,
        generations=5,
        peak_ratio=FitConstraint(initial=0.5),
    )
    options.update(settings)
    inputs = options.pop("inputs", [AnalysisInput(sample_id=sample.id)])
    return ProliferationRequest(
        revision=workspace.revision,
        name="Known generations",
        channel="CFSE",
        inputs=inputs,
        **options,
    )


def wait_job(client, workspace, request):
    base = f"/api/workspaces/{workspace.id}/proliferation"
    response = client.post(
        f"{base}/jobs",
        json=request.model_dump() if isinstance(request, ProliferationRequest) else request,
    )
    assert response.status_code == 202, response.text
    identifier = response.json()["id"]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        response = client.get(f"{base}/jobs/{identifier}")
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] not in {"queued", "running"}:
            assert job["status"] == "succeeded", job.get("error")
            return base, identifier, job
        time.sleep(0.04)
    pytest.fail("Proliferation worker did not finish")


def test_omitted_background_defaults_survive_worker_serialization(client):
    values, _ = dye_events(size=6000, mean=1024, background=0, cv=20)
    control, _ = dye_events(size=3000, mean=1024, background=0, cv=20, fractions=(1,))
    workspace, samples, _ = experiment(client.app.state.store, values, control)
    options = {
        "revision": workspace.revision,
        "channel": "CFSE",
        "inputs": [{"sample_id": samples[0].id}],
        "undivided_control": {"sample_id": samples[1].id},
        "control_mode": "fix_mean_cv",
        "generations": 5,
    }
    request = ProliferationRequest.model_validate(options)
    restored = ProliferationRequest.model_validate_json(request.model_dump_json())
    assert proliferation.input_hash(workspace, request) == proliferation.input_hash(
        workspace, restored
    )
    _, _, job = wait_job(client, workspace, options)
    assert job["result"]["fits"][0]["data"]["fitted_count"] == 6000
    assert job["result"]["calibration"]["background"] == 0


def test_precursor_metrics_match_published_flowjo_example_and_undefined_response():
    stats = proliferation_statistics([15888, 32922, 13647, 897])
    assert stats.total_events == 63354
    assert stats.precursor_equivalents == 35872.875
    assert stats.responding_precursor_equivalents == 19984.875
    assert stats.division_equivalents == 23620.875
    assert stats.division_index == pytest.approx(23620.875 / 35872.875)
    assert stats.proliferation_index == pytest.approx(23620.875 / 19984.875)
    assert stats.expansion_index == pytest.approx(63354 / 35872.875)
    assert stats.replication_index == pytest.approx(47466 / 19984.875)
    assert stats.precursor_frequency == pytest.approx(
        stats.division_index / stats.proliferation_index
    )
    assert stats.observed_divided_fraction > stats.precursor_frequency
    zero = proliferation_statistics([1000, 0, 0, 0])
    assert zero.division_index == zero.precursor_frequency == 0
    assert zero.proliferation_index is None and zero.replication_index is None
    assert zero.expansion_index == 1
    # Half the original cells divide exactly three times: collected events are
    # predominantly divided, while precursor frequency remains one half.
    half = proliferation_statistics([100, 0, 0, 800])
    assert half.precursor_frequency == 0.5 and half.division_index == 1.5
    assert half.proliferation_index == 3 and half.replication_index == 8


@pytest.mark.parametrize("cv,noise", [(25, 0), (25, 4), (25, 30), (80, 25), (100, 30)])
def test_background_convolution_matches_independent_adaptive_quadrature(cv, noise):
    edges = np.linspace(-30, 300, 41)
    sigma = math.sqrt(math.log1p((cv / 100) ** 2))
    actual, coverage = proliferation.generation_components(edges, 150, 0.5, cv, 20, noise, 4)
    expected = []
    for generation in range(4):
        median = 130 * 0.5**generation
        probabilities = []
        for lo, hi in zip(edges[:-1], edges[1:], strict=True):
            if noise:

                def integrand(z, median=median, hi=hi, lo=lo):
                    dye = median * math.exp(sigma * z)
                    return (
                        (ndtr((hi - 20 - dye) / noise) - ndtr((lo - 20 - dye) / noise))
                        * math.exp(-z * z / 2)
                        / math.sqrt(2 * math.pi)
                    )

                points = [math.log((x - 20) / median) / sigma for x in (lo, hi) if x > 20]
                probabilities.append(
                    quad(
                        integrand, -10, 10, epsabs=1e-12, points=[v for v in points if -10 < v < 10]
                    )[0]
                )
            else:

                def cdf(x, median=median):
                    return ndtr(math.log((x - 20) / median) / sigma) if x > 20 else 0

                probabilities.append(cdf(hi) - cdf(lo))
        probabilities = np.asarray(probabilities)
        assert coverage[generation] == pytest.approx(probabilities.sum(), abs=1e-7)
        expected.append(probabilities / probabilities.sum())
    np.testing.assert_allclose(actual, expected, atol=1e-7, rtol=0)


@pytest.mark.parametrize("distribution", ["lognormal", "gaussian"])
def test_independent_latent_generations_and_peak_constraints(store, distribution):
    values, labels = dye_events(distribution=distribution)
    workspace, samples, engine = experiment(store, values)
    request = request_for(
        workspace,
        samples[0],
        distribution=distribution,
        undivided_mean=FitConstraint(minimum=1000, maximum=1100, initial=1040),
        peak_ratio=FitConstraint(minimum=0.48, maximum=0.52, initial=0.5),
        dye_cv=FitConstraint(minimum=20, maximum=30, initial=25),
    )
    result, arrays = proliferation.calculate(workspace, request, engine, new_id())
    fit = result.fits[0]
    truth = np.bincount(labels, minlength=6) / len(labels)
    np.testing.assert_allclose(fit.fractions, truth, atol=0.009)
    assert fit.parameters["undivided_mean"] == pytest.approx(1044, abs=6)
    assert fit.parameters["peak_ratio"] == pytest.approx(0.5, abs=0.003)
    assert fit.parameters["dye_cv"] == pytest.approx(25, abs=1)
    assert fit.diagnostics["converged"]
    assert fit.diagnostics["fraction_standard_errors"] is not None
    assert fit.diagnostics["metric_standard_errors"]["division_index"] > 0
    probabilities = arrays[samples[0].id][:, :6]
    valid = np.isfinite(probabilities).all(axis=1)
    assert valid.sum() >= len(values) - 3
    np.testing.assert_allclose(probabilities[valid].sum(axis=1), 1, atol=1e-6)
    assert (arrays[samples[0].id][valid, -1] == probabilities[valid].argmax(axis=1)).all()
    known = proliferation_statistics(np.bincount(labels, minlength=6))
    assert fit.statistics.division_index == pytest.approx(known.division_index, abs=0.035)
    fit.data.sha256 = save_array(
        store.analysis_path(workspace.id, result.id, samples[0].id), arrays[samples[0].id]
    )
    np.testing.assert_array_equal(
        proliferation.load_data(store, workspace.id, result, fit.data), arrays[samples[0].id]
    )


def test_undivided_and_unstained_controls_calibrate_full_population_and_fingerprint(store):
    values, labels = dye_events(size=10000, background_sd=2)
    undivided, _ = dye_events(seed=99, size=12000, fractions=(1,), background_sd=2)
    rng = np.random.default_rng(9)
    unstained = rng.normal(20, 2, 10000)
    workspace, samples, engine = experiment(store, values, undivided, unstained)
    request = request_for(
        workspace,
        samples[0],
        undivided_mean=FitConstraint(),
        undivided_control=AnalysisInput(sample_id=samples[1].id),
        control_mode="fix_mean_cv",
        autofluorescence_control=AnalysisInput(sample_id=samples[2].id),
        peak_ratio=FitConstraint(fixed=0.5),
    )
    result, _ = proliferation.calculate(workspace, request, engine, new_id())
    fit = result.fits[0]
    assert result.calibration["undivided"]["calibration_count"] == 12000
    assert fit.parameters["undivided_mean"] == pytest.approx(1044, abs=9)
    assert fit.parameters["dye_cv"] == pytest.approx(25, abs=1)
    assert fit.parameters["background"] == pytest.approx(20, abs=0.1)
    assert fit.parameters["background_sd"] == pytest.approx(2, abs=0.1)
    np.testing.assert_allclose(
        fit.fractions, np.bincount(labels, minlength=6) / len(labels), atol=0.015
    )
    workspace.samples[0].channels[0].transform = Transform(kind="logicle")
    workspace.samples[1].name = "Control label edited"
    assert not proliferation.is_stale(workspace, result)
    changed = request.model_copy(update={"control_range_min": 400})
    assert proliferation.input_hash(workspace, changed) != result.input_hash
    workspace.samples.pop()
    assert proliferation.is_stale(workspace, result)


def test_nonpositive_values_are_explicitly_excluded_or_retained_by_linear_histogram(store):
    values, labels = dye_events(
        size=12000,
        fractions=(0.05, 0.1, 0.2, 0.3, 0.35),
        mean=120,
        background=20,
        background_sd=15,
        cv=20,
        distribution="gaussian",
    )
    values = np.r_[values, np.nan, np.inf]
    workspace, samples, engine = experiment(store, values)
    request = request_for(
        workspace,
        samples[0],
        generations=4,
        distribution="gaussian",
        histogram_space="linear",
        undivided_mean=FitConstraint(fixed=120),
        peak_ratio=FitConstraint(fixed=0.5),
        dye_cv=FitConstraint(fixed=20),
        background_sd=15,
    )
    result, arrays = proliferation.calculate(workspace, request, engine, new_id())
    fit = result.fits[0]
    assert fit.diagnostics["excluded_nonfinite"] == 2
    assert fit.diagnostics["excluded_nonpositive"] == 0
    assert (values < 0).sum() > 100
    assert np.isfinite(arrays[samples[0].id][values < 0]).all()
    np.testing.assert_allclose(
        fit.fractions, np.bincount(labels, minlength=5) / len(labels), atol=0.07
    )
    assert any("overlap" in v for v in fit.warnings)
    logarithmic = request.model_copy(update={"histogram_space": "log2"})
    log_result, log_arrays = proliferation.calculate(workspace, logarithmic, engine, new_id())
    assert log_result.fits[0].diagnostics["excluded_nonpositive"] == int((values <= 0).sum())
    assert np.isnan(log_arrays[samples[0].id][values <= 0]).all()


def test_generation_zero_only_and_absent_reference_generations(store):
    undivided, _ = dye_events(size=5000, fractions=(1,))
    workspace, samples, engine = experiment(store, undivided)
    request = request_for(
        workspace,
        samples[0],
        generations=0,
        undivided_mean=FitConstraint(fixed=1044),
        dye_cv=FitConstraint(fixed=25),
        peak_ratio=FitConstraint(fixed=0.5),
    )
    result, arrays = proliferation.calculate(workspace, request, engine, new_id())
    assert result.fits[0].fractions == [1]
    assert result.fits[0].statistics.proliferation_index is None
    assert result.fits[0].statistics.replication_index is None
    assert arrays[samples[0].id].shape == (5000, 2)
    values, labels = dye_events(seed=104, size=10000, fractions=(0, 0.25, 0.4, 0.35))
    workspace, samples, engine = experiment(store, values)
    request = request_for(
        workspace,
        samples[0],
        generations=3,
        undivided_mean=FitConstraint(fixed=1044),
        peak_ratio=FitConstraint(fixed=0.5),
    )
    result, _ = proliferation.calculate(workspace, request, engine, new_id())
    assert result.fits[0].fractions[0] < 0.003
    np.testing.assert_allclose(
        result.fits[0].fractions, np.bincount(labels, minlength=4) / len(labels), atol=0.015
    )
    assert any("nearly absent" in v for v in result.fits[0].warnings)


def test_large_finite_intensities_keep_probabilities_and_vector_figure_finite(store):
    values, _ = dye_events(size=3000, cv=12)
    scale = 1e304
    workspace, samples, engine = experiment(store, values * scale)
    request = request_for(
        workspace,
        samples[0],
        undivided_mean=FitConstraint(initial=1044 * scale),
        background=20 * scale,
        dye_cv=FitConstraint(fixed=12),
    )
    result, arrays = proliferation.calculate(workspace, request, engine, new_id())
    assert result.fits[0].parameters["undivided_mean"] == pytest.approx(1044 * scale, rel=0.015)
    assert np.isfinite(arrays[samples[0].id]).all()
    figure = proliferation.figure_svg(result, result.fits[0], "Large intensities")
    assert b"nan" not in figure and b"inf" not in figure


@pytest.mark.parametrize("values", [np.full(250, 1000.0), np.arange(40.0), np.full(500, np.nan)])
def test_uninformative_population_rejected_before_any_fit(store, values):
    workspace, samples, engine = experiment(store, values)
    with pytest.raises(ValueError, match="occupied|200"):
        proliferation.calculate(workspace, request_for(workspace, samples[0]), engine, new_id())


def test_required_anchor_and_explicit_parameter_limits():
    base = dict(revision=0, inputs=[AnalysisInput(sample_id=new_id())], channel="CFSE")
    with pytest.raises(ValueError, match="generation-zero"):
        ProliferationRequest(**base)
    for options in [
        dict(generations=13),
        dict(peak_ratio=FitConstraint(fixed=0.9)),
        dict(dye_cv=FitConstraint(fixed=0.1)),
        dict(range_min=-1),
    ]:
        with pytest.raises(ValueError):
            ProliferationRequest(**base, undivided_mean=FitConstraint(initial=1000), **options)


def test_worker_batch_review_exports_event_identities_archive_and_undo(client):
    values, labels = dye_events(size=5000)
    other, _ = dye_events(seed=18, size=4500, fractions=(0.3, 0.2, 0.2, 0.1, 0.1, 0.1))
    store = client.app.state.store
    workspace, samples, engine = experiment(store, np.r_[values, np.nan, -5], other)
    request = request_for(
        workspace, samples[0], inputs=[AnalysisInput(sample_id=s.id) for s in samples]
    )
    base, identifier, job = wait_job(client, workspace, request)
    assert job["can_apply"] and not job["stale"]
    for suffix in ["jobs", "quality/jobs", "cell-cycle/jobs"]:
        assert client.get(f"/api/workspaces/{workspace.id}/{suffix}").json() == []
    assert client.get(f"{base}/jobs").json()[0]["result"]["fits"][0]["observed"] == []
    fit = job["result"]["fits"][0]
    stats = list(csv.DictReader(io.StringIO(client.get(f"{base}/{identifier}/statistics").text)))
    assert (
        len(stats) == 12
        and float(stats[0]["division_index"]) == fit["statistics"]["division_index"]
    )
    rows = list(
        csv.DictReader(
            io.StringIO(client.get(f"{base}/{identifier}/events?sample_id={samples[0].id}").text)
        )
    )
    assert (
        len(rows) == 5002 and rows[-1]["event_id"] == "5001" and rows[-1]["G0_probability"] == "nan"
    )
    assert rows[0]["assigned_generation"] in {"0", "1", "2", "3", "4", "5"}
    svg = client.get(f"{base}/{identifier}/figure?sample_id={samples[0].id}")
    assert (
        svg.status_code == 200 and b"<svg" in svg.content and b"Precursor frequency" in svg.content
    )
    response = client.post(f"{base}/jobs/{identifier}/apply", json={"revision": workspace.revision})
    assert response.status_code == 200, response.text
    saved = Workspace.model_validate(response.json())
    model = saved.proliferation_results[0]
    outputs = np.array([engine.column(saved, saved.samples[0], c) for c in model.columns]).T
    assert outputs.shape == (5002, 7) and np.isnan(outputs[-2:]).all()
    counts = [
        int(engine.mask(saved, saved.samples[0], g.id).sum())
        for g in saved.gates
        if g.sample_id == samples[0].id
    ]
    assert counts == fit["assigned_counts"] and sum(counts) == 5000
    assert engine.raw(saved, saved.samples[0]).shape == (5002, 2)
    exported = client.get(
        f"/api/workspaces/{workspace.id}/samples/{samples[0].id}/export?format=fcs"
    )
    path = store.root / "proliferation.fcs"
    path.write_bytes(exported.content)
    fcs_sample, fcs_values, _, _ = parse_fcs(path, "Probability export")
    assert len(fcs_sample.channels) == 9
    np.testing.assert_allclose(fcs_values[:, 2:], outputs, atol=1e-6, equal_nan=True)
    archived = client.get(f"/api/workspaces/{workspace.id}/export/project")
    assert archived.status_code == 200, archived.text[:200] if archived.status_code != 200 else ""
    with zipfile.ZipFile(io.BytesIO(archived.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["version"] == 2 and set(
            manifest["workspace"]["proliferation_results"][0]
        ) == {"id", "sha256"}
    response = client.post(
        "/api/import/project", files={"file": ("generations.cytoforge", archived.content)}
    )
    assert response.status_code == 200, response.text
    restored = Workspace.model_validate(response.json())
    actual = np.array([engine.column(restored, restored.samples[0], c) for c in model.columns]).T
    np.testing.assert_array_equal(actual, outputs)
    assert not client.get(f"/api/workspaces/{restored.id}/proliferation/{identifier}").json()[
        "stale"
    ]
    undone = client.post(
        f"/api/workspaces/{workspace.id}/undo", json={"revision": saved.revision}
    ).json()
    assert undone["proliferation_results"] == [] and len(undone["samples"][0]["channels"]) == 2
    redone = client.post(
        f"/api/workspaces/{workspace.id}/redo", json={"revision": undone["revision"]}
    ).json()
    assert len(redone["proliferation_results"]) == 1 and len(redone["gates"]) == 12


def test_source_gate_change_marks_stale_and_frozen_assignments_do_not_grow(client):
    values, _ = dye_events(size=7000)
    workspace, samples, engine = experiment(client.app.state.store, values)
    parent = Gate(
        sample_id=samples[0].id, name="Original source", kind="range", x="Time", bounds=[0, 5000]
    )
    workspace = client.app.state.store.mutate(
        workspace.id, "Source gate", lambda doc: doc.gates.append(parent), workspace.revision
    )
    request = request_for(
        workspace, samples[0], inputs=[AnalysisInput(sample_id=samples[0].id, gate_id=parent.id)]
    )
    base, identifier, _ = wait_job(client, workspace, request)
    saved = Workspace.model_validate(
        client.post(f"{base}/jobs/{identifier}/apply", json={"revision": workspace.revision}).json()
    )
    changed = client.put(
        f"/api/workspaces/{workspace.id}/gates/{parent.id}",
        json={
            "revision": saved.revision,
            "gate": parent.model_copy(update={"bounds": [0, 7000]}).model_dump(),
        },
    )
    assert changed.status_code == 200, changed.text
    document = Workspace.model_validate(changed.json())
    assert client.get(f"{base}/{identifier}").json()["stale"]
    assert (
        sum(
            engine.mask(document, document.samples[0], g.id).sum()
            for g in document.gates
            if g.id != parent.id
        )
        == 5000
    )
    assert np.isnan(
        engine.column(document, document.samples[0], document.proliferation_results[0].columns[-1])[
            5000:
        ]
    ).all()
    request.revision = document.revision
    _, pending, _ = wait_job(client, document, request)
    changed = client.put(
        f"/api/workspaces/{workspace.id}/gates/{parent.id}",
        json={
            "revision": document.revision,
            "gate": parent.model_copy(update={"bounds": [0, 6000]}).model_dump(),
        },
    )
    assert changed.status_code == 200
    assert (
        client.post(
            f"{base}/jobs/{pending}/apply", json={"revision": changed.json()["revision"]}
        ).status_code
        == 409
    )


@pytest.mark.parametrize("corruption", ["probability", "assignment", "statistics"])
def test_rehashed_corrupt_portable_model_is_rejected_before_workspace_commit(client, corruption):
    values, _ = dye_events(size=2500)
    store = client.app.state.store
    workspace, samples, _ = experiment(store, values)
    base, identifier, _ = wait_job(client, workspace, request_for(workspace, samples[0]))
    saved = client.post(f"{base}/jobs/{identifier}/apply", json={"revision": workspace.revision})
    assert saved.status_code == 200
    archived = client.get(f"/api/workspaces/{workspace.id}/export/project").content
    with zipfile.ZipFile(io.BytesIO(archived)) as source:
        files = {n: source.read(n) for n in source.namelist()}
    report_path = f"proliferation/{identifier}.json"
    report = json.loads(files[report_path])
    if corruption == "statistics":
        report["fits"][0]["statistics"]["division_index"] += 1
    else:
        data_path = f"proliferation/{identifier}/{samples[0].id}.npy"
        values = np.load(io.BytesIO(files[data_path]), allow_pickle=False)
        if corruption == "probability":
            values[0, 0] = 2
        else:
            values[0, -1] = (values[0, -1] + 1) % 6
        payload = io.BytesIO()
        np.save(payload, values, allow_pickle=False)
        files[data_path] = payload.getvalue()
        report["fits"][0]["data"]["sha256"] = hashlib.sha256(files[data_path]).hexdigest()
    files[report_path] = json.dumps(report).encode()
    manifest = json.loads(files["manifest.json"])
    manifest["workspace"]["proliferation_results"][0]["sha256"] = hashlib.sha256(
        files[report_path]
    ).hexdigest()
    files["manifest.json"] = json.dumps(manifest).encode()
    modified = io.BytesIO()
    with zipfile.ZipFile(modified, "w") as target:
        for name, payload in files.items():
            target.writestr(name, payload)
    before = len(store.list())
    response = client.post(
        "/api/import/project", files={"file": ("damaged.cytoforge", modified.getvalue())}
    )
    assert response.status_code == 422, response.text
    assert len(store.list()) == before
