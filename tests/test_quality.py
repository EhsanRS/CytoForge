import io
import json
import time
import zipfile
from pathlib import Path

import numpy as np
import pytest
from cytoforge import quality
from cytoforge.analysis import gate_signature
from cytoforge.gatingml import export_gatingml
from cytoforge.models import Channel, Gate, QualityRequest, Sample, Transform, Workspace, new_id
from cytoforge.science import Engine, parse_fcs, save_array, save_events
from pydantic import ValidationError


def known_acquisition(store, n=10000):
    # Known stable pulse ratios; one abrupt signal shift and one acquisition slowdown.
    marker = 100 + (np.arange(n) % 500) / 50
    dt = np.full(n, 0.001)
    if n >= 6500:
        marker[4000:4500] += 200
        dt[6000:6500] = 0.01
    times = np.zeros(n)
    times[1:] = np.cumsum(dt[:-1])
    height = 50 + (np.arange(n) % 37) / 10
    area = 2 * height
    if n > 82:
        marker[73] = 1000
        marker[82] = np.nan
        area[50] *= 2
    values = np.column_stack([times, marker, area, height])
    sample = Sample(
        name="Known anomalies",
        event_count=n,
        source="FCS",
        channels=[
            Channel(name=name, range=1000, transform=Transform(kind="linear"))
            for name in ["Time", "Marker", "FSC-A", "FSC-H"]
        ],
    )
    doc = Workspace(name="QC reference", samples=[sample])
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    doc = store.create(doc)
    request = QualityRequest(
        revision=doc.revision,
        sample_id=sample.id,
        channels=["Marker"],
        time_channel="Time",
        use_transforms=False,
        saturation_channels=["Marker"],
        pulse_area="FSC-A",
        pulse_height="FSC-H",
    )
    return doc, sample, values, request


def persist(store, doc, result, values):
    result.data.sha256 = save_array(store.quality_path(doc.id, result.id), values)
    return result


def test_known_anomalies_exact_event_identity_and_review(store):
    doc, sample, acquired, request = known_acquisition(store)
    result, values = quality.calculate(doc, request, Engine(store), new_id())
    assert {b.index for b in result.bins if any(r.startswith("signal:") for r in b.reasons)} == {8}
    assert {b.index for b in result.bins if any(r.startswith("rate:") for r in b.reasons)} == {12}
    assert result.data.flag_counts == {"nonfinite": 1, "time": 0, "saturation": 1, "pulse": 1}
    assert values[50, 0] == quality.FLAGS["pulse"]
    assert values[73, 0] == quality.FLAGS["saturation"]
    assert values[82, 0] == quality.FLAGS["nonfinite"]
    # Ground truth event IDs, independent of the cleaning implementation.
    expected = np.ones(len(acquired), dtype=bool)
    expected[4000:4500] = expected[6000:6500] = False
    expected[[50, 73, 82]] = False
    np.testing.assert_array_equal(quality.selection(values, [8, 12], list(quality.FLAGS)), expected)
    assert quality.selection(values, [], []).sum() == len(acquired)
    persist(store, doc, result, values)
    doc.quality_results.append(result)
    keep = Gate(
        name="Reviewed clean",
        sample_id=sample.id,
        kind="quality",
        quality_id=result.id,
        quality_excluded_bins=[8, 12],
        quality_exclusions=list(quality.FLAGS),
    )
    reject = keep.model_copy(update={"id": new_id(), "name": "Rejected", "quality_keep": False})
    doc.gates += [keep, reject]
    doc = Workspace.model_validate(doc.model_dump())
    engine = Engine(store)
    np.testing.assert_array_equal(engine.mask(doc, sample, keep.id), expected)
    np.testing.assert_array_equal(engine.mask(doc, sample, reject.id), ~expected)
    assert {r["count"] for r in engine.gate_counts(doc, sample)} == {8997, 1003}
    with pytest.raises(ValueError, match="cannot be represented"):
        export_gatingml(doc, sample)


def test_time_resets_nan_quantization_and_constancy(store):
    doc, sample, acquired, request = known_acquisition(store)
    acquired[:, 0] = np.floor(acquired[:, 0] * 10) / 10
    acquired[7000:, 0] -= acquired[7000, 0]
    acquired[2500, 0] = np.nan
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), acquired)
    result, values = quality.calculate(doc, request, Engine(store), new_id())
    assert result.diagnostics["time"]["resets"] == 1
    assert result.diagnostics["time"]["reset_event_ids"] == [7000]
    assert result.diagnostics["time"]["repeated_timestamps"] > 9000
    assert values[7000, 0] & quality.FLAGS["time"]
    assert values[2500, 0] & quality.FLAGS["time"]
    assert result.bins[13].rate is None  # Interval crossing the reset is never sorted/repaired.
    assert result.bins[4].rate is None
    assert all(b.rate is None or b.rate > 0 for b in result.bins)
    result.model_dump_json()  # All report numbers must be valid JSON.
    acquired[:, 0] = 0
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), acquired)
    result, _ = quality.calculate(doc, request, Engine(store), new_id())
    assert all(b.rate is None for b in result.bins)
    assert any("undefined" in w for w in result.warnings)


@pytest.mark.parametrize("n", [0, 3, 501])
def test_empty_small_and_partial_last_bins(store, n):
    doc, _, _, request = known_acquisition(store, n)
    result, values = quality.calculate(doc, request, Engine(store), new_id())
    quality.validate_data(result, values)
    assert sum(b.end - b.start for b in result.bins) == n
    assert quality.selection(values, [], []).sum() == n
    assert all(not b.suggested for b in result.bins)
    assert any("eight" in w for w in result.warnings)


def test_parent_frequency_does_not_change_acquisition_rate_and_frozen_source(store):
    doc, sample, _, request = known_acquisition(store)
    parent = Gate(
        name="Sparse source", sample_id=sample.id, kind="range", x="FSC-H", bounds=[50, 51]
    )
    doc.gates.append(parent)
    request.gate_id = parent.id
    request.min_bin_events = 10
    result, values = quality.calculate(doc, request, Engine(store), new_id())
    assert result.bins[0].rate == pytest.approx(1000)
    assert result.bins[0].population_count < 200
    original = Engine(store).mask(doc, sample, parent.id)
    np.testing.assert_array_equal(quality.selection(values, [], []), original)
    persist(store, doc, result, values)
    doc.quality_results.append(result)
    gate = Gate(
        name="Fixed IDs",
        sample_id=sample.id,
        kind="quality",
        quality_id=result.id,
        parent_id=parent.id,
    )
    doc.gates.append(gate)
    before = quality.input_hash(doc, request)
    parent.bounds = [50, 100]
    assert quality.input_hash(doc, request) != before
    assert quality.is_stale(doc, result)
    # Growing the parent never fabricates additional QC-reviewed event identities.
    np.testing.assert_array_equal(Engine(store).mask(doc, sample, gate.id), original)


def test_integrity_and_reference_rejection(store):
    doc, sample, _, request = known_acquisition(store)
    result, values = quality.calculate(doc, request, Engine(store), new_id())
    persist(store, doc, result, values)
    bad = values.copy()
    bad[0, 1] = 4
    with pytest.raises(ValueError, match="bin identities"):
        quality.validate_data(result, bad)
    bad = values.copy()
    bad[0, 0] = 32
    with pytest.raises(ValueError, match="flags"):
        quality.validate_data(result, bad)
    with pytest.raises(ValidationError, match="unique"):
        QualityRequest(revision=0, sample_id=sample.id, channels=["Marker", "Marker"])
    with pytest.raises(ValidationError, match="distinct"):
        QualityRequest(
            revision=0,
            sample_id=sample.id,
            channels=["Marker"],
            pulse_area="FSC-A",
            pulse_height="FSC-A",
        )
    path = store.quality_path(doc.id, result.id)
    path.write_bytes(b"invalid")
    with pytest.raises(ValueError, match="integrity"):
        quality.load_data(store, doc.id, result)
    old_gate = Gate(sample_id=sample.id, name="Old", kind="range", x="Marker", bounds=[0, 2])
    assert not any(k.startswith("quality_") for k in gate_signature(old_gate))
    with pytest.raises(ValidationError, match="review fields"):
        Gate(
            sample_id=sample.id,
            name="Confused",
            kind="range",
            x="Marker",
            bounds=[0, 2],
            quality_id=result.id,
        )


def test_coarse_timestamps_and_sparse_tail_do_not_fabricate_rate_anomalies(store):
    doc, sample, acquired, request = known_acquisition(store, 10001)
    acquired[:, 0] = np.floor(np.arange(len(acquired)) / 1000)
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), acquired)
    result, _ = quality.calculate(doc, request, Engine(store), new_id())
    assert all(b.rate is None for b in result.bins)
    assert result.diagnostics["time"]["resolution_limited_bins"] == len(result.bins)
    assert any("three positive timestamp" in w for w in result.warnings)
    acquired[:, 0] = np.arange(len(acquired)) * 0.001
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), acquired)
    result, _ = quality.calculate(doc, request, Engine(store), new_id())
    assert result.bins[-1].rate_score is None
    assert not result.bins[-1].suggested


def test_time_saturation_and_pulse_checks_are_independent_of_compensation(store):
    from cytoforge.models import Compensation

    doc, sample, _, request = known_acquisition(store)
    before, before_values = quality.calculate(doc, request, Engine(store), new_id())
    matrix = Compensation(
        name="Deliberate large correction",
        detectors=["Time", "Marker", "FSC-A", "FSC-H"],
        matrix=[[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [2, 2, 0.2, 1]],
    )
    doc.compensations.append(matrix)
    sample.compensation_id = matrix.id
    doc.samples[0].compensation_id = matrix.id
    after, after_values = quality.calculate(doc, request, Engine(store), new_id())
    for name in ["time", "saturation", "pulse"]:
        bit = quality.FLAGS[name]
        np.testing.assert_array_equal(before_values[:, 0] & bit, after_values[:, 0] & bit)
    assert after.diagnostics["pulse"]["log_ratio_center"] == pytest.approx(np.log(2))
    assert after.diagnostics["time"]["resets"] == 0
    assert after.data.flag_counts["saturation"] == 1
    assert before.input_hash != after.input_hash


def test_qc_scientific_fingerprint_ignores_cosmetic_edits_but_tracks_cleanup_choices(store):
    from cytoforge.analysis import input_hash
    from cytoforge.models import AnalysisInput, AnalysisRequest

    doc, sample, _, request = known_acquisition(store)
    result, values = quality.calculate(doc, request, Engine(store), new_id())
    persist(store, doc, result, values)
    doc.quality_results.append(result)
    gate = Gate(name="Reviewed", kind="quality", sample_id=sample.id, quality_id=result.id)
    doc.gates.append(gate)
    analysis = AnalysisRequest(
        revision=doc.revision,
        name="QC PCA",
        algorithm="pca",
        channels=["Marker", "FSC-A"],
        inputs=[AnalysisInput(sample_id=sample.id, gate_id=gate.id)],
    )
    before = input_hash(doc, analysis)
    sample.name = "Renamed sample"
    gate.color = "#ffffff"
    gate.name = "Renamed gate"
    assert input_hash(doc, analysis) == before
    assert not quality.is_stale(doc, result)
    gate.quality_excluded_bins = [8]
    assert input_hash(doc, analysis) != before


def test_large_finite_values_have_valid_json_and_undefined_pulses_are_explicit(store):
    doc, sample, acquired, request = known_acquisition(store)
    acquired[:, 1] = np.where(np.arange(len(acquired)) % 2, 1e308, -1e308)
    acquired[15, 2] = 0
    acquired[16, 3] = -1
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), acquired)
    result, flags = quality.calculate(doc, request, Engine(store), new_id())
    assert result.bins[0].signals["Marker"]["p10"] == -1e308
    assert result.bins[0].signals["Marker"]["p90"] == 1e308
    assert result.diagnostics["pulse"]["invalid_count"] == 2
    assert flags[15, 0] & quality.FLAGS["pulse"]
    assert flags[16, 0] & quality.FLAGS["pulse"]
    result.model_dump_json()
    # Historical numeric types in saved requests must not change the fingerprint.
    restored = QualityRequest.model_validate(json.loads(request.model_dump_json()))
    assert quality.input_hash(doc, restored) == quality.input_hash(doc, request)


def test_real_instrument_bins_against_independent_sorted_quantiles(store):
    path = Path(__file__).parent / "fixtures/interchange/flowjo/101_DEN084Y5_15_E03_009_clean.fcs"
    sample, events, matrix, _ = parse_fcs(path, path.name)
    doc = Workspace(name="Real acquisition", samples=[sample], compensations=[matrix])
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), events)
    doc = store.create(doc)
    channel = "FSC-A"
    request = QualityRequest(
        revision=doc.revision,
        sample_id=sample.id,
        channels=[channel],
        time_channel="Time",
        bin_events=2000,
        use_transforms=False,
        compensated=False,
        saturation_channels=[channel],
    )
    result, values = quality.calculate(doc, request, Engine(store), new_id())
    # Independent order-statistic interpolation (no quality.py quantile/scoring helper).
    col = [c.name for c in sample.acquisition_channels].index(channel)
    for b in result.bins[::11]:
        sorted_values = sorted(float(x) for x in events[b.start : b.end, col] if np.isfinite(x))
        for fraction, name in [(0.1, "p10"), (0.5, "median"), (0.9, "p90")]:
            pos = (len(sorted_values) - 1) * fraction
            lo, hi = int(np.floor(pos)), int(np.ceil(pos))
            reference = sorted_values[lo] * (hi - pos) + sorted_values[hi] * (pos - lo)
            if hi == lo:
                reference = sorted_values[lo]
            assert b.signals[channel][name] == pytest.approx(reference)
    assert sample.event_count == 283969
    assert quality.selection(values, [], []).sum() == 283969
    quality.validate_data(result, values)
    # This verifies measurement/identity on real data, not biological QC accuracy.
    assert all(b.time_start is not None for b in result.bins)


def upload_known(client):
    doc = client.post("/api/workspaces", json={"name": "QC workflow"}).json()
    rows = ["Time,Marker,FSC-A,FSC-H"]
    for i in range(10000):
        rows.append(
            f"{i / 1000},{100 + (i % 500) / 50 + (200 if 4000 <= i < 4500 else 0)},"
            f"{100 + i % 13},{50 + (i % 13) / 2}"
        )
    response = client.post(
        f"/api/workspaces/{doc['id']}/import?revision={doc['revision']}",
        files=[("files", ("Known.csv", "\n".join(rows), "text/csv"))],
    )
    assert response.status_code == 200, response.text
    return response.json()["workspace"]


def start_and_wait(client, doc, use_transforms=False):
    root = f"/api/workspaces/{doc['id']}"
    response = client.post(
        f"{root}/quality/jobs",
        json={
            "revision": doc["revision"],
            "sample_id": doc["samples"][0]["id"],
            "channels": ["Marker"],
            "time_channel": "Time",
            "use_transforms": use_transforms,
        },
    )
    assert response.status_code == 202, response.text
    job = response.json()
    deadline = time.monotonic() + 30
    while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(0.05)
        job = client.get(f"{root}/quality/jobs/{job['id']}").json()
    assert job["status"] == "succeeded", job
    return root, job


def test_api_review_archive_export_undo_and_revision(client):
    doc = upload_known(client)
    root, job = start_and_wait(client, doc)
    assert client.get(root).json()["gates"] == []
    assert client.get(f"{root}/jobs").json() == []  # Discovery list has its own job type.
    assert len(client.get(f"{root}/quality/jobs").json()) == 1
    review = {
        "revision": doc["revision"],
        "name": "Reviewed clean",
        "excluded_bins": [8],
        "exclusions": ["nonfinite", "time"],
        "create_rejected": True,
    }
    counts = client.post(f"{root}/quality/{job['id']}/review", json=review)
    assert counts.status_code == 200, counts.text
    assert counts.json()["retained_count"] == 9500
    response = client.post(f"{root}/quality/jobs/{job['id']}/apply", json=review)
    assert response.status_code == 200, response.text
    doc = response.json()
    assert len(doc["quality_results"]) == 1
    assert len(doc["gates"]) == 2
    sample = doc["samples"][0]
    counts = client.get(f"{root}/samples/{sample['id']}/counts").json()
    assert {r["count"] for r in counts} == {9500, 500}
    report = client.get(f"{root}/quality/{job['id']}/report")
    assert len(report.json()["reviewed_populations"]) == 2
    identities = client.get(f"{root}/quality/{job['id']}/events?gate_id={doc['gates'][0]['id']}")
    csv = np.genfromtxt(io.StringIO(identities.text), delimiter=",", names=True)
    assert int(csv["in_reviewed_population"].sum()) == 9500
    np.testing.assert_array_equal(csv["event_id"], np.arange(10000))
    archived = client.get(f"{root}/export/project").content
    with zipfile.ZipFile(io.BytesIO(archived)) as source:
        manifest = json.loads(source.read("manifest.json"))
        assert manifest["version"] == 2
        assert set(manifest["workspace"]["quality_results"][0]) == {"id", "sha256"}
        assert f"quality/{job['id']}.json" in source.namelist()
    restored = client.post("/api/import/project", files={"file": ("qc.cytoforge", archived)})
    assert restored.status_code == 200, restored.text
    imported = restored.json()
    restored_root = f"/api/workspaces/{imported['id']}"
    assert not client.get(f"{restored_root}/quality/{job['id']}").json()["stale"]
    assert {
        r["count"] for r in client.get(f"{restored_root}/samples/{sample['id']}/counts").json()
    } == {9500, 500}
    undone = client.post(f"{root}/undo", json={"revision": doc["revision"]})
    assert undone.status_code == 200, undone.text
    assert undone.json()["quality_results"] == []
    redone = client.post(f"{root}/redo", json={"revision": undone.json()["revision"]})
    assert redone.status_code == 200, redone.text
    assert len(redone.json()["quality_results"]) == 1
    assert client.post(f"{root}/quality/jobs/{job['id']}/apply", json=review).status_code == 422
    ids = [g["id"] for g in redone.json()["gates"]]
    revised = client.post(
        f"{root}/quality/{job['id']}/apply",
        json={
            "revision": redone.json()["revision"],
            "name": "Revised clean",
            "excluded_bins": [],
            "exclusions": [],
            "create_rejected": True,
        },
    )
    assert revised.status_code == 200, revised.text
    assert [g["id"] for g in revised.json()["gates"]] == ids
    assert {r["count"] for r in client.get(f"{root}/samples/{sample['id']}/counts").json()} == {
        10000,
        0,
    }
    # Hash failure during archive restore rolls back the imported workspace.
    changed = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(archived)) as source, zipfile.ZipFile(changed, "w") as target:
        for name in source.namelist():
            target.writestr(name, b"broken" if name.startswith("quality/") else source.read(name))
    response = client.post(
        "/api/import/project", files={"file": ("bad.cytoforge", changed.getvalue())}
    )
    assert response.status_code == 422
    assert "integrity" in response.json()["detail"]
    # Version-one archives with inline QC reports remain importable.
    legacy = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(archived)) as source, zipfile.ZipFile(legacy, "w") as target:
        manifest = json.loads(source.read("manifest.json"))
        manifest["version"] = 1
        manifest["workspace"]["quality_results"] = [
            json.loads(source.read(f"quality/{q['id']}.json"))
            for q in manifest["workspace"]["quality_results"]
        ]
        for entry in source.namelist():
            if entry == "manifest.json":
                target.writestr(entry, json.dumps(manifest))
            elif not entry.startswith("quality/") or not entry.endswith(".json"):
                target.writestr(entry, source.read(entry))
    response = client.post(
        "/api/import/project", files={"file": ("legacy.cytoforge", legacy.getvalue())}
    )
    assert response.status_code == 200, response.text


def test_api_stale_inputs_atomic_invalid_review_and_cancel(client):
    doc = upload_known(client)
    root, job = start_and_wait(client, doc, use_transforms=True)
    review = {"revision": doc["revision"], "excluded_bins": [999]}
    rejected = client.post(f"{root}/quality/jobs/{job['id']}/apply", json=review)
    assert rejected.status_code == 422
    assert client.get(root).json()["revision"] == doc["revision"]
    sid = doc["samples"][0]["id"]
    transformed = client.post(
        f"{root}/transforms",
        json={
            "revision": doc["revision"],
            "channel": "Marker",
            "transform": {"kind": "asinh"},
            "sample_ids": [sid],
        },
    )
    assert transformed.status_code == 200, transformed.text
    assert client.get(f"{root}/quality/jobs/{job['id']}").json()["stale"]
    assert (
        client.post(
            f"{root}/quality/jobs/{job['id']}/apply",
            json={
                "revision": transformed.json()["revision"],
            },
        ).status_code
        == 409
    )
    # Start a fresh queued job and cancel before it is applied.
    current = client.get(root).json()
    response = client.post(
        f"{root}/quality/jobs",
        json={
            "revision": current["revision"],
            "sample_id": sid,
            "channels": ["Marker"],
        },
    )
    identifier = response.json()["id"]
    cancelled = client.post(f"{root}/quality/jobs/{identifier}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert (
        client.post(
            f"{root}/quality/jobs/{identifier}/apply",
            json={
                "revision": current["revision"],
            },
        ).status_code
        == 422
    )


def test_quality_jobs_recover_and_cosmetic_revisions_allow_scientific_apply(store):
    from cytoforge.analysis import atomic_json
    from cytoforge.jobs import JobManager
    from cytoforge.models import QualityApply

    doc, sample, _, request = known_acquisition(store)
    manager = JobManager(store, workers=0)
    job = manager.submit(doc, request)
    manager.close()
    recovered = JobManager(store, workers=0)
    assert recovered.get(doc, job["id"])["status"] == "interrupted"
    assert recovered.list(doc) == []
    assert recovered.list(doc, qc=True)[0]["id"] == job["id"]
    completed = recovered.submit(doc, request)
    result, values = quality.calculate(doc, request, Engine(store), completed["id"])
    persist(store, doc, result, values)
    atomic_json(recovered.directory / completed["id"] / "result.json", result.model_dump())
    recovered.records[completed["id"]]["status"] = "succeeded"
    recovered._save(recovered.records[completed["id"]])
    recovered.close()
    restarted = JobManager(store, workers=0)
    doc = store.mutate(doc.id, "Rename", lambda w: setattr(w, "name", "Renamed"), doc.revision)
    record = restarted.get(doc, completed["id"])
    assert not record["stale"] and record["can_apply"]
    applied = restarted.apply_quality(
        doc.id,
        completed["id"],
        QualityApply(
            revision=doc.revision,
            excluded_bins=[8, 12],
            exclusions=["nonfinite", "time"],
        ),
    )
    assert {r["count"] for r in Engine(store).gate_counts(applied, sample)} == {8999, 1001}
    restarted.close()


@pytest.mark.parametrize(
    "manifest",
    [
        [],
        {"format": "cytoforge", "version": 2, "workspace": []},
        {"format": "cytoforge", "version": [2], "workspace": {"name": "Invalid"}},
        {
            "format": "cytoforge",
            "version": 2,
            "workspace": {
                "name": "Invalid",
                "quality_results": [{"id": "../outside", "sha256": "a" * 64}],
            },
        },
    ],
)
def test_malformed_portable_qc_references_never_write_or_create_a_workspace(client, manifest):
    before = client.get("/api/workspaces").json()
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
    response = client.post(
        "/api/import/project", files={"file": ("bad.cytoforge", payload.getvalue())}
    )
    assert response.status_code == 422, response.text
    assert client.get("/api/workspaces").json() == before
