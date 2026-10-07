"""Independent values, event identity and atomic lifetime of merged populations."""

import io
import json
import threading
import time
import zipfile

import numpy as np
import pytest
from cytoforge import concatenation
from cytoforge.models import (
    Channel,
    Compensation,
    DerivedParameter,
    Gate,
    Sample,
    Transform,
    Workspace,
)
from cytoforge.science import save_events


def experiment(client, different=False):
    store = client.app.state.store
    arrays = [
        np.array([[0, 2], [2, 4], [4, 2], [8, 0], [np.nan, 1]], dtype=float),
        np.array([[6, 3], [8, 5], [2, -1], [10, 7]], dtype=float),
    ]
    matrices = [Compensation(name="Known", detectors=["X", "Y"], matrix=[[2, 0], [0, 4]])]
    if different:
        matrices.append(
            Compensation(name="Different", detectors=["X", "Y"], matrix=[[1, 0], [0, 2]])
        )
    samples = [
        Sample(
            name=f"Tube {i + 1}",
            channels=[
                Channel(name=n, transform=Transform(kind="asinh", cofactor=2))
                for n in ("XY" if i == 0 else "YX")
            ],
            event_count=len(values),
            compensation_id=matrices[min(i, len(matrices) - 1)].id,
            tags={"donor": "A" if i == 0 else "B", "condition": "shared"},
        )
        for i, values in enumerate(arrays)
    ]
    gates = [
        Gate(
            name="Chosen",
            sample_id=s.id,
            kind="range",
            x="X",
            bounds=[0.5, 3.5],
            x_transform=Transform(),
        )
        for s in samples
    ]
    doc = Workspace(
        name="Concatenation truth", samples=samples, gates=gates, compensations=matrices
    )
    for sample, values in zip(samples, arrays, strict=True):
        sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    return store.create(doc), arrays


def body(doc, **changes):
    result = dict(
        revision=doc.revision,
        name="Merged",
        inputs=[dict(sample_id=s.id) for s in doc.samples],
        parameters=[dict(name=n, sources={s.id: n for s in doc.samples}) for n in "XY"],
        values="raw",
        compensation="preserve",
        keywords=["donor"],
    )
    result.update(changes)
    return result


def start(client, doc, request=None):
    response = client.post(f"/api/workspaces/{doc.id}/concatenations", json=request or body(doc))
    assert response.status_code == 200, response.text
    return response.json()


def wait(client, doc, session):
    for _ in range(2000):
        response = client.get(f"/api/workspaces/{doc.id}/concatenations/{session['id']}")
        assert response.status_code == 200, response.text
        result = response.json()
        if result["status"] not in {"queued", "running"}:
            return result
        time.sleep(0.005)
    pytest.fail("Concatenation writer did not finish")


def apply(client, doc, session, **changes):
    request = dict(revision=doc.revision, review_hash=session["review_hash"])
    request.update(changes)
    return client.post(
        f"/api/workspaces/{doc.id}/concatenations/{session['id']}/apply", json=request
    )


@pytest.mark.parametrize("values", ["raw", "compensated", "scale"])
def test_exact_parameter_matching_value_spaces_and_origin_identity(client, values):
    doc, arrays = experiment(client)
    before = doc.model_dump_json()
    session = wait(client, doc, start(client, doc, body(doc, values=values)))
    assert session["status"] == "ready", session
    assert client.app.state.store.get(doc.id).model_dump_json() == before
    response = apply(client, doc, session)
    assert response.status_code == 200, response.text
    merged = response.json()["samples"][-1]
    actual = np.load(client.app.state.store.data_path(doc.id, merged["id"]))
    expected = np.concatenate([arrays[0], arrays[1][:, ::-1]])
    if values != "raw":
        expected = expected / [2, 4]
        expected[~np.isfinite(expected).all(axis=1)] = np.nan
    if values == "scale":
        expected = np.arcsinh(expected / 2)
        assert all(c["transform"]["kind"] == "linear" for c in merged["channels"])
    np.testing.assert_allclose(actual[:, :2], expected, equal_nan=True, atol=0)
    np.testing.assert_array_equal(actual[:, 2], [1] * 5 + [2] * 4)
    np.testing.assert_array_equal(actual[:, 3], list(range(5)) + list(range(4)))
    np.testing.assert_array_equal(actual[:, 4], [1] * 5 + [2] * 4)
    assert merged["concatenation"]["keywords"]["donor"] == ["A", "B"]
    assert merged["tags"] == {"condition": "shared"}
    assert bool(merged["compensation_id"]) == (values == "raw")
    rows = client.get(
        f"/api/workspaces/{doc.id}/samples/{merged['id']}/origins", params={"offset": 4, "limit": 3}
    ).json()["rows"]
    assert [(r["sample_id"], r["source_event_id"]) for r in rows] == [
        (doc.samples[0].id, "4"),
        (doc.samples[1].id, "0"),
        (doc.samples[1].id, "1"),
    ]
    assert all(isinstance(r["source_event_id"], str) for r in rows)
    if values == "raw":
        output_doc = client.app.state.store.get(doc.id)
        output_sample = output_doc.samples[-1]
        np.testing.assert_allclose(
            client.app.state.engine.column(output_doc, output_sample, "X"),
            np.concatenate([arrays[0][:, 0], arrays[1][:, 1]]) / 2,
            equal_nan=True,
        )
        assert output_sample.compensation_id != doc.samples[0].compensation_id


def test_full_population_masks_not_plot_sampling_and_materialized_formulas(client):
    doc, _ = experiment(client)
    for sample in doc.samples:
        sample.channels.append(Channel(name="Ratio"))
        sample.derived_parameters.append(
            DerivedParameter(name="Ratio", expression='ch("X") / max(ch("Y"), 1)')
        )
    doc = client.app.state.store.mutate(
        doc.id, "Define ratio", lambda d: setattr(d, "samples", doc.samples), doc.revision
    )
    request = body(
        doc,
        values="compensated",
        inputs=[
            dict(sample_id=s.id, gate_id=g.id) for s, g in zip(doc.samples, doc.gates, strict=True)
        ],
        parameters=[dict(name="Pooled ratio", sources={s.id: "Ratio" for s in doc.samples})],
    )
    session = wait(client, doc, start(client, doc, request))
    assert session["status"] == "ready", session
    result = apply(client, doc, session).json()["samples"][-1]
    actual = np.load(client.app.state.store.data_path(doc.id, result["id"]))
    # Sample 1 compensated X: [0,1,2,4,nan]; sample 2 X: [1.5,2.5,-.5,3.5].
    np.testing.assert_allclose(actual[:, 0], [1, 2, 1, 1.25])
    np.testing.assert_array_equal(actual[:, 2], [1, 2, 0, 1])
    assert result["derived_parameters"] == []
    assert [s["count"] for s in result["concatenation"]["sources"]] == [2, 2]


@pytest.mark.parametrize("grouping", ["batch", "keyword"])
def test_grouped_outputs_publish_in_one_undo_step(client, grouping):
    doc, _ = experiment(client, different=True)
    session = wait(
        client,
        doc,
        start(client, doc, body(doc, grouping=grouping, batch_size=1, group_keyword="donor")),
    )
    assert session["status"] == "ready", session
    result = apply(client, doc, session).json()
    assert len(result["samples"]) == 4 and result["revision"] == doc.revision + 1
    assert [s["concatenation"]["sources"][0]["index"] for s in result["samples"][2:]] == [1, 2]
    undone = client.post(
        f"/api/workspaces/{doc.id}/undo", json={"revision": result["revision"]}
    ).json()
    assert [s["id"] for s in undone["samples"]] == [s.id for s in doc.samples]
    redone = client.post(
        f"/api/workspaces/{doc.id}/redo", json={"revision": undone["revision"]}
    ).json()
    assert redone["samples"] == result["samples"]
    for sample in redone["samples"][2:]:
        assert (
            client.get(f"/api/workspaces/{doc.id}/samples/{sample['id']}/origins").status_code
            == 200
        )


@pytest.mark.parametrize("choice", ["compensated", "discard"])
def test_differing_matrices_require_an_explicit_scientific_choice(client, choice):
    doc, arrays = experiment(client, different=True)
    rejected = client.post(f"/api/workspaces/{doc.id}/concatenations", json=body(doc))
    assert rejected.status_code == 422 and "matrices differ" in rejected.text
    session = wait(
        client,
        doc,
        start(
            client,
            doc,
            body(
                doc,
                values="compensated" if choice == "compensated" else "raw",
                compensation="discard",
            ),
        ),
    )
    result = apply(client, doc, session).json()["samples"][-1]
    assert result["compensation_id"] is None
    actual = np.load(client.app.state.store.data_path(doc.id, result["id"]))
    expected = np.concatenate(
        [
            arrays[0] / ([2, 4] if choice == "compensated" else [1, 1]),
            arrays[1][:, ::-1] / ([1, 2] if choice == "compensated" else [1, 1]),
        ]
    )
    if choice == "compensated":
        expected[~np.isfinite(expected).all(axis=1)] = np.nan
    np.testing.assert_allclose(actual[:, :2], expected, equal_nan=True)


@pytest.mark.parametrize("damage", ["source", "events", "origins", "review", "revision"])
def test_failed_apply_never_mutates_workspace_or_leaves_output_files(client, damage):
    doc, _ = experiment(client)
    session = wait(client, doc, start(client, doc))
    sample = session["samples"][0]
    directory = client.app.state.concatenations.root / session["id"]
    options = {}
    if damage == "review":
        options["review_hash"] = "0" * 64
    elif damage == "revision":
        client.app.state.store.mutate(
            doc.id, "Unrelated edit", lambda d: setattr(d, "name", "Changed"), doc.revision
        )
    else:
        path = (
            client.app.state.store.data_path(doc.id, doc.samples[0].id)
            if damage == "source"
            else directory / (sample["id"] + (".npy" if damage == "events" else ".origins.npy"))
        )
        with path.open("r+b") as handle:
            handle.seek(-1, 2)
            value = handle.read(1)
            handle.seek(-1, 2)
            handle.write(bytes([value[0] ^ 1]))
    before = client.app.state.store.get(doc.id).model_dump_json()
    response = apply(client, doc, session, **options)
    assert response.status_code in {422, 409}, response.text
    assert client.app.state.store.get(doc.id).model_dump_json() == before
    assert not client.app.state.store.data_path(doc.id, sample["id"]).exists()
    assert not client.app.state.store.origins_path(doc.id, sample["id"]).exists()


def test_cooperative_cancel_discards_whole_staged_batch(client, monkeypatch):
    doc, _ = experiment(client)
    entered, release = threading.Event(), threading.Event()
    original = concatenation.Sessions._verify_sources

    def pause(self, *args):
        entered.set()
        assert release.wait(10)
        return original(self, *args)

    monkeypatch.setattr(concatenation.Sessions, "_verify_sources", pause)
    session = start(client, doc)
    assert entered.wait(10)
    client.post(f"/api/workspaces/{doc.id}/concatenations/{session['id']}/cancel")
    release.set()
    result = wait(client, doc, session)
    assert result["status"] == "cancelled"
    assert client.app.state.store.get(doc.id).model_dump_json() == doc.model_dump_json()
    assert not list((client.app.state.concatenations.root / session["id"]).glob("*.npy"))


def test_ready_cancel_and_cross_workspace_session_isolation(client):
    doc, _ = experiment(client)
    session = wait(client, doc, start(client, doc))
    other = client.app.state.store.create(Workspace(name="Other"))
    assert (
        client.get(f"/api/workspaces/{other.id}/concatenations/{session['id']}").status_code == 404
    )
    cancelled = client.post(
        f"/api/workspaces/{doc.id}/concatenations/{session['id']}/cancel"
    ).json()
    assert cancelled["status"] == "cancelled"
    assert apply(client, doc, session).status_code == 409
    assert not list((client.app.state.concatenations.root / session["id"]).glob("*.npy"))


def test_archive_restores_exact_origins_after_original_samples_removed(client):
    doc, _ = experiment(client)
    session = wait(client, doc, start(client, doc))
    result = apply(client, doc, session).json()
    for sample in doc.samples:
        result = client.delete(
            f"/api/workspaces/{doc.id}/samples/{sample.id}", params={"revision": result["revision"]}
        ).json()
    merged = result["samples"][0]
    archive = client.get(f"/api/workspaces/{doc.id}/export/project")
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        assert f"origins/{merged['id']}.npy" in bundle.namelist()
    restored = client.post(
        "/api/import/project", files={"file": ("merged.cytoforge", archive.content)}
    )
    assert restored.status_code == 200, restored.text
    restored_doc = restored.json()
    assert restored_doc["samples"][0] == merged
    rows = client.get(
        f"/api/workspaces/{restored_doc['id']}/samples/{merged['id']}/origins"
    ).json()["rows"]
    assert [r["source_event_id"] for r in rows] == ["0", "1", "2", "3", "4", "0", "1", "2", "3"]
    assert {r["sample_id"] for r in rows} == {s.id for s in doc.samples}


@pytest.mark.parametrize(
    "problem",
    [
        "missing_parameter",
        "duplicate_input",
        "duplicate_mapping",
        "reserved",
        "missing_keyword",
        "foreign_gate",
        "empty_gate",
        "subset_matrix",
    ],
)
def test_invalid_source_plans_are_rejected_without_scientific_writes(client, problem):
    doc, _ = experiment(client)
    request = body(doc)
    if problem == "missing_parameter":
        request["parameters"][0]["sources"][doc.samples[0].id] = "Missing"
    elif problem == "duplicate_input":
        request["inputs"].append(request["inputs"][0])
    elif problem == "duplicate_mapping":
        request["parameters"][1]["sources"][doc.samples[0].id] = "X"
    elif problem == "reserved":
        request["parameters"][0]["name"] = "CF_Source"
    elif problem == "missing_keyword":
        request.update(grouping="keyword", group_keyword="Absent")
    elif problem == "foreign_gate":
        request["inputs"][0]["gate_id"] = doc.gates[1].id
    elif problem == "subset_matrix":
        request["parameters"] = request["parameters"][:1]
    else:
        doc = client.app.state.store.mutate(
            doc.id,
            "Empty population",
            lambda d: setattr(d.gates[0], "bounds", [100, 200]),
            doc.revision,
        )
        request["revision"] = doc.revision
        request["inputs"] = [dict(sample_id=doc.samples[0].id, gate_id=doc.gates[0].id)]
        for parameter in request["parameters"]:
            parameter["sources"] = {doc.samples[0].id: parameter["name"]}
    response = client.post(f"/api/workspaces/{doc.id}/concatenations", json=request)
    if problem == "empty_gate":
        assert response.status_code == 200
        result = wait(client, doc, response.json())
        assert result["status"] == "failed" and "no events" in result["error"]
    else:
        assert response.status_code in {400, 422}, response.text
    assert client.app.state.store.get(doc.id).model_dump_json() == doc.model_dump_json()


def test_original_sample_json_remains_compatible(client):
    doc, _ = experiment(client)
    assert "concatenation" not in doc.samples[0].model_dump()
    assert "concatenation" not in json.loads(doc.model_dump_json())["samples"][0]


def test_spectral_background_and_weights_are_materialized_without_double_unmixing(client):
    store = client.app.state.store
    truth = np.array([[1, 3], [-2, 5], [0, 0], [20, 4]], dtype=float)
    spectrum = np.array([[1, 0.2, 0.5], [0.3, 1, 0.7]])
    matrix = Compensation(
        name="Spectral truth",
        detectors=["D1", "D2", "D3"],
        outputs=["A", "B"],
        matrix=spectrum.tolist(),
        kind="spectral",
        background=[2, 5, 9],
        weights=[1, 4, 2],
    )
    sample = Sample(
        name="Spectral sample",
        event_count=len(truth),
        channels=[Channel(name=n) for n in ["D1", "D2", "D3", "A", "B"]],
        unmixed_parameters=["A", "B"],
        compensation_id=matrix.id,
    )
    doc = Workspace(name="Spectral merge", samples=[sample], compensations=[matrix])
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), truth @ spectrum + [2, 5, 9])
    doc = store.create(doc)
    request = body(
        doc, values="compensated", parameters=[dict(name=n, sources={sample.id: n}) for n in "AB"]
    )
    session = wait(client, doc, start(client, doc, request))
    assert session["status"] == "ready", session
    merged = apply(client, doc, session).json()["samples"][-1]
    values = np.load(store.data_path(doc.id, merged["id"]))
    np.testing.assert_allclose(values[:, :2], truth, atol=1e-12)
    assert merged["compensation_id"] is None and merged["unmixed_parameters"] == []
    rejected = client.post(
        f"/api/workspaces/{doc.id}/concatenations",
        json=body(doc, parameters=[dict(name=n, sources={sample.id: n}) for n in matrix.detectors]),
    )
    assert rejected.status_code in {409, 422}


def test_an_empty_source_remains_in_provenance_and_missing_keywords_have_a_code(client):
    doc, _ = experiment(client)

    def edit(workspace):
        workspace.gates[0].bounds = [100, 200]
        workspace.samples[1].tags.pop("donor")

    doc = client.app.state.store.mutate(doc.id, "Prepare empty source", edit, doc.revision)
    session = wait(
        client,
        doc,
        start(
            client,
            doc,
            body(
                doc,
                inputs=[
                    dict(sample_id=s.id, gate_id=doc.gates[0].id if i == 0 else None)
                    for i, s in enumerate(doc.samples)
                ],
            ),
        ),
    )
    assert session["status"] == "ready", session
    merged = apply(client, doc, session).json()["samples"][-1]
    assert merged["event_count"] == 4
    assert [s["count"] for s in merged["concatenation"]["sources"]] == [0, 4]
    assert merged["concatenation"]["keywords"]["donor"] == ["A", None]
    origins = client.get(f"/api/workspaces/{doc.id}/samples/{merged['id']}/origins").json()["rows"]
    assert [r["source_index"] for r in origins] == [2] * 4


def test_modified_review_definitions_are_rejected_even_if_event_files_match(client):
    doc, _ = experiment(client)
    session = wait(client, doc, start(client, doc))
    client.app.state.concatenations.records[session["id"]]["samples"][0]["name"] = (
        "Changed after review"
    )
    assert apply(client, doc, session).status_code == 409
    assert client.app.state.store.get(doc.id).model_dump_json() == doc.model_dump_json()


def test_exact_origin_csv_preserves_ids_and_neutralizes_spreadsheet_formulas(client):
    doc, _ = experiment(client)
    doc = client.app.state.store.mutate(
        doc.id, "Hostile label", lambda d: setattr(d.samples[0], "name", "=1+1"), doc.revision
    )
    session = wait(client, doc, start(client, doc))
    merged = apply(client, doc, session).json()["samples"][-1]
    response = client.get(f"/api/workspaces/{doc.id}/samples/{merged['id']}/export/origins")
    assert response.status_code == 200
    assert "'=1+1" in response.text
    assert len(response.text.strip().splitlines()) == 10
    assert response.text.strip().endswith(",Tube 2,3")


def test_cancel_during_chunk_writing_closes_and_removes_staged_files(client, monkeypatch):
    doc, _ = experiment(client)
    entered, release = threading.Event(), threading.Event()
    original = concatenation.ChunkColumns.column
    calls = 0

    def pause(self, name):
        nonlocal calls
        calls += 1
        if calls == 3:
            entered.set()
            assert release.wait(10)
        return original(self, name)

    monkeypatch.setattr(concatenation, "CHUNK_EVENTS", 2)
    monkeypatch.setattr(concatenation.ChunkColumns, "column", pause)
    session = start(client, doc)
    assert entered.wait(10)
    progress = client.get(f"/api/workspaces/{doc.id}/concatenations/{session['id']}").json()
    assert progress["events_written"] == 2
    client.post(f"/api/workspaces/{doc.id}/concatenations/{session['id']}/cancel")
    release.set()
    assert wait(client, doc, session)["status"] == "cancelled"
    assert not list((client.app.state.concatenations.root / session["id"]).glob("*.npy"))
    assert client.app.state.store.get(doc.id).revision == doc.revision


def test_archive_corrupt_origins_cannot_create_an_incomplete_workspace(client):
    doc, _ = experiment(client)
    session = wait(client, doc, start(client, doc))
    merged = apply(client, doc, session).json()["samples"][-1]
    exported = client.get(f"/api/workspaces/{doc.id}/export/project").content
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(exported)) as source, zipfile.ZipFile(output, "w") as target:
        for entry in source.infolist():
            value = source.read(entry)
            if entry.filename == f"origins/{merged['id']}.npy":
                value = value[:-1] + bytes([value[-1] ^ 1])
            target.writestr(entry.filename, value)
    before = client.get("/api/workspaces").json()
    result = client.post(
        "/api/import/project", files={"file": ("corrupt.cytoforge", output.getvalue())}
    )
    assert result.status_code == 422
    assert client.get("/api/workspaces").json() == before
