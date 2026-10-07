"""PhenoGraph kernel, seeded communities and native analysis lifecycle invariants."""

import hashlib
from types import SimpleNamespace

import numpy as np
import pytest
from cytoforge import analysis
from cytoforge.compensation import assign_matrix
from cytoforge.graph_clustering import fit, neighbor_graph
from cytoforge.models import AnalysisRequest, AnalysisResult, Compensation, Gate, Workspace
from cytoforge.science import Engine, save_events

from tests.test_analysis import _apply, _request, _submit, _wait, _workspace


def populations(count=40):
    rng = np.random.default_rng(409123)
    truth = np.repeat(np.arange(3), count)
    centers = np.array([[10, 10, 1], [50, 10, 2], [90, 10, 3]])
    values = centers[truth] + rng.normal(0, 0.1, (len(truth), 3))
    return values, truth


def graph_request(doc, **changes):
    return _request(doc) | {"algorithm": "phenograph"} | changes


@pytest.mark.parametrize("failure", ["label", "global_count", "sample_count", "missing_ids"])
def test_review_rejects_invalid_community_labels_counts_and_missing_identity_proof(client, failure):
    values, _ = populations()
    doc = _workspace(client, [values])
    identifier = _submit(client, doc, graph_request(doc, n_neighbors=30))
    job = _wait(client, doc, identifier)
    assert job["status"] == "succeeded"
    result = AnalysisResult.model_validate(job["result"])
    data = result.data[0]
    store = client.app.state.store
    output = np.load(store.analysis_path(doc["id"], result.id, data.sample_id), allow_pickle=False)
    if failure == "label":
        output[0, 0] = 0.5
    elif failure == "global_count":
        result.diagnostics["community_sizes"]["1"] += 1
    elif failure == "sample_count":
        result.diagnostics["cluster_counts"][data.sample_id]["1"] += 1
    else:
        data.fitted_ids_sha256 = ""
    with pytest.raises(ValueError, match="PhenoGraph"):
        analysis.validate_result_data(store, doc["id"], result, data, output)


def test_neighbor_link_budget_rejects_impractical_graph_requests():
    with pytest.raises(ValueError, match="5 million"):
        AnalysisRequest(
            revision=0,
            name="Oversized",
            algorithm="phenograph",
            inputs=[{"sample_id": "a" * 32}],
            channels=["X", "Y"],
            max_events=100000,
            n_neighbors=51,
        )


def test_jaccard_graph_matches_independent_set_arithmetic_and_asymmetric_average():
    neighbors = np.array(
        [[1, 2, 3], [0, 2, 4], [0, 1, 3], [0, 2, 4], [1, 3, 5], [3, 4, 6], [4, 5, 7], [4, 5, 6]]
    )
    sets = [set(row) for row in neighbors]
    expected = np.zeros((8, 8))
    for left in range(8):
        for right in range(left + 1, 8):
            ratio = len(sets[left] & sets[right]) / len(sets[left] | sets[right])
            expected[left, right] = expected[right, left] = (
                ratio * ((right in sets[left]) + (left in sets[right])) / 2
            )
    actual = neighbor_graph(neighbors[:, ::-1]).toarray()
    np.testing.assert_array_equal(actual, expected)
    assert actual[0, 1] == 0.2 and actual[4, 7] == 0.1 and actual[1, 4] == 0
    assert not np.diag(actual).any()


@pytest.mark.parametrize(
    "neighbors",
    [
        [[1, 1], [0, 2], [0, 1]],
        [[0, 1], [0, 2], [0, 1]],
        [[1, 3], [0, 2], [0, 1]],
        [[1, -1], [0, 2], [0, 1]],
        [[1.0, 2.0], [0.0, 2.0], [0.0, 1.0]],
        [1, 2, 3],
    ],
)
def test_neighbor_sets_reject_duplicates_self_indices_foreign_indices_and_nonintegers(neighbors):
    with pytest.raises(ValueError):
        neighbor_graph(neighbors)


def test_seeded_communities_recover_known_separated_populations_and_best_modularity():
    from sklearn.metrics import adjusted_rand_score

    values, truth = populations()
    labels, diagnostics = fit(values, neighbors=30, min_cluster_size=10, seed=8231)
    repeated, report = fit(values, neighbors=30, min_cluster_size=10, seed=8231)
    np.testing.assert_array_equal(labels, repeated)
    assert diagnostics == report
    assert adjusted_rand_score(truth, labels) == 1
    assert diagnostics["community_sizes"] == {"1": 40, "2": 40, "3": 40}
    assert diagnostics["modularity"] == max(diagnostics["restart_modularities"])
    assert diagnostics["unassigned_fitted_count"] == 0
    assert not diagnostics["biological_classes_verified"]


def test_duplicate_events_exclude_self_and_small_communities_have_explicit_unassigned_labels():
    values = np.repeat(np.array([[1.0, 1.0], [20.0, 1.0], [1.0, 20.0]]), 8, axis=0)
    labels, report = fit(values, neighbors=6, min_cluster_size=9, seed=17)
    np.testing.assert_array_equal(labels, np.zeros(len(values)))
    assert not report["community_sizes"] and report["unassigned_fitted_count"] == len(values)
    assert report["discarded_community_sizes"] == [8, 8, 8]


@pytest.mark.parametrize(
    "values, settings",
    [
        (np.zeros((3, 2)), {"neighbors": 3}),
        (np.full((5, 2), np.nan), {"neighbors": 2}),
        (np.zeros((5, 2)), {"neighbors": 2, "resolution": float("inf")}),
        (np.zeros((5, 2)), {"neighbors": 2, "min_cluster_size": 1}),
        (np.zeros((5, 2)), {"neighbors": 2, "restarts": 0}),
    ],
)
def test_invalid_graph_inputs_are_rejected(values, settings):
    with pytest.raises(ValueError):
        fit(values, **settings)


@pytest.mark.parametrize("raw", [False, True])
def test_actual_worker_preserves_fitted_event_identity_and_source_files(client, raw):
    values, _ = populations(80)
    doc = _workspace(client, [values, values[:40]])
    body = graph_request(
        doc,
        compensated=not raw,
        standardize=False,
        use_transforms=False,
        max_events=160,
        n_neighbors=20,
        min_cluster_size=10,
    )
    identifier = _submit(client, doc, body)
    job = _wait(client, doc, identifier)
    assert job["status"] == "succeeded", job.get("error")
    result = AnalysisResult.model_validate(job["result"])
    assert len(result.columns) == 1 and result.versions["igraph"] == "1.0.0"
    store = client.app.state.store
    for data in result.data:
        output = np.load(
            store.analysis_path(doc["id"], result.id, data.sample_id), allow_pickle=False
        )
        identities = np.load(
            store.fitted_ids_path(doc["id"], result.id, data.sample_id), allow_pickle=False
        )
        np.testing.assert_array_equal(np.flatnonzero(np.isfinite(output[:, 0])), identities)
        assert data.mapped_count == data.fitted_count
        sample = next(s for s in doc["samples"] if s["id"] == data.sample_id)
        assert (
            hashlib.sha256(store.data_path(doc["id"], data.sample_id).read_bytes()).hexdigest()
            == sample["sha256"]
        )
    saved = Workspace.model_validate(_apply(client, doc, identifier))
    for sample in saved.samples:
        assert (
            next(c for c in sample.channels if c.name == result.columns[0]).transform.kind
            == "linear"
        )
    for gate in saved.gates:
        if gate.provenance.get("membership_basis") == "fitted_phenograph_community":
            sample = next(s for s in saved.samples if s.id == gate.sample_id)
            expected = result.diagnostics["cluster_counts"][sample.id][
                str(gate.provenance["community"])
            ]
            assert int(Engine(store).mask(saved, sample, gate.id).sum()) == expected


def test_raw_community_gates_keep_acquired_parents_after_assignment_archive_and_undo(client):
    values, _ = populations(80)
    document = _workspace(client, [values])
    store = client.app.state.store
    doc = store.get(document["id"])
    parent = Gate(
        sample_id=doc.samples[0].id, name="Raw X parent", kind="range", x="X", bounds=[0, 70]
    )
    doc = store.mutate(
        doc.id, "Raw analysis parent", lambda d: d.gates.append(parent), doc.revision
    )
    document = doc.model_dump()
    body = graph_request(
        document,
        compensated=False,
        standardize=False,
        use_transforms=False,
        max_events=120,
        n_neighbors=30,
        min_cluster_size=10,
    )
    body["inputs"][0]["gate_id"] = parent.id
    identifier = _submit(client, document, body)
    assert _wait(client, document, identifier)["status"] == "succeeded"
    saved = Workspace.model_validate(_apply(client, document, identifier))
    result = saved.analyses[0]
    memberships = [g for g in saved.gates if g.provenance.get("membership_basis")]
    expected = {g.id: Engine(store).mask(saved, saved.samples[0], g.id).copy() for g in memberships}
    assert all(g.parent_id != parent.id for g in memberships)
    original_parent = Engine(store).mask(saved, saved.samples[0], parent.id).copy()
    matrix = Compensation(
        name="Assigned after raw discovery",
        detectors=["X", "Y", "Z"],
        outputs=["X", "Y", "Z"],
        matrix=[[1, 0, 0], [3, 1, 0], [0, 0, 1]],
    )

    def assign(document):
        document.compensations.append(matrix)
        assign_matrix(document.samples[0], matrix)

    assigned = store.mutate(saved.id, "Assign matrix", assign, saved.revision)
    assert not analysis.is_stale(assigned, result)
    assert not np.array_equal(
        original_parent, Engine(store).mask(assigned, assigned.samples[0], parent.id)
    )
    for gate in memberships:
        np.testing.assert_array_equal(
            Engine(store).mask(assigned, assigned.samples[0], gate.id), expected[gate.id]
        )
    root = f"/api/workspaces/{assigned.id}"
    archive = client.get(root + "/export/project")
    restored = client.post(
        "/api/import/project", files={"file": ("communities.cytoforge", archive.content)}
    )
    assert restored.status_code == 200, restored.text
    reopened = store.get(restored.json()["id"])
    assert not analysis.is_stale(reopened, reopened.analyses[0])
    for gate in reopened.gates:
        if gate.provenance.get("membership_basis"):
            assert (
                Engine(store).mask(reopened, reopened.samples[0], gate.id).sum()
                == result.diagnostics["cluster_counts"][assigned.samples[0].id][
                    str(gate.provenance["community"])
                ]
            )
    undone = client.post(root + "/undo", json={"revision": assigned.revision})
    assert undone.status_code == 200
    undone = client.post(root + "/undo", json={"revision": undone.json()["revision"]})
    assert undone.status_code == 200 and not undone.json()["analyses"]
    assert [g["id"] for g in undone.json()["gates"]] == [parent.id]


def test_analysis_raw_mode_keeps_legacy_fingerprints_and_ignores_assigned_matrix_only_when_raw(
    store,
):
    from cytoforge.models import Channel, Sample

    values, _ = populations()
    doc = Workspace(name="Scientific basis")
    sample = Sample(
        name="Cells", event_count=len(values), channels=[Channel(name=n) for n in ["X", "Y", "Z"]]
    )
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    doc.samples.append(sample)
    request = AnalysisRequest(
        revision=0,
        name="Reference",
        algorithm="pca",
        inputs=[{"sample_id": sample.id}],
        channels=["X", "Y"],
    )
    original = analysis.input_hash(doc, request)
    snapshot = analysis._sample_signature(doc, request, request.inputs[0])
    assert "coordinate_basis" not in snapshot and snapshot["compensation"] is None
    legacy_basis = SimpleNamespace(channels=request.channels, use_transforms=request.use_transforms)
    assert analysis._sample_signature(doc, legacy_basis, request.inputs[0]) == snapshot
    matrix = Compensation(
        name="Change",
        detectors=["X", "Y", "Z"],
        outputs=["X", "Y", "Z"],
        matrix=[[1, 0, 0], [3, 1, 0], [0, 0, 1]],
    )
    request.compensated = False
    raw = analysis.input_hash(doc, request)
    doc.compensations.append(matrix)
    assign_matrix(sample, matrix)
    assert analysis.input_hash(doc, request) == raw
    request.compensated = True
    assert analysis.input_hash(doc, request) != original
    current_snapshot = analysis._sample_signature(doc, request, request.inputs[0])
    assert analysis._sample_signature(doc, legacy_basis, request.inputs[0]) == current_snapshot
    assert current_snapshot["compensation"]["id"] == matrix.id
