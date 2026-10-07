"""Virtual cohorts against independent event values and source-specific populations."""

import hashlib
import json

import flowio
import numpy as np
import pytest
from cytoforge import event_exports
from cytoforge.analysis import is_stale
from cytoforge.imports import stream_fcs
from cytoforge.models import (
    Channel,
    DerivedParameter,
    Gate,
    GateDimension,
    GatePartition,
    Group,
    PlotDefinition,
    Transform,
    Workspace,
)
from cytoforge.report_plots import figure
from cytoforge.report_sources import SourceAudit
from cytoforge.three_dimensional import POINT_DTYPE
from cytoforge.virtual_groups import PooledEngine
from test_analysis import _apply, _submit, _wait
from test_channel_aliases import cohort, harmonize


def setup_group(client):
    doc, truth = cohort(client)
    doc, _ = harmonize(client, doc)
    group = Group(name="Panel cohort", sample_ids=[s.id for s in doc.samples])
    gates = [
        Gate(sample_id=s.id, name="Positive", kind="range", x="CD3", bounds=bounds)
        for s, bounds in zip(doc.samples, [[0, 2], [2, 4]], strict=True)
    ]

    def change(candidate):
        candidate.groups.append(group)
        candidate.gates.extend(gates)

    doc = client.app.state.store.mutate(doc.id, "Cohort test", change, doc.revision)
    return doc, truth, group, gates


def pooled(client, doc, group, anchor=None, **kwargs):
    return PooledEngine(
        doc, client.app.state.engine, anchor or doc.samples[0].id, group.id, **kwargs
    )


def source_hashes(client, doc):
    store = client.app.state.store
    return {
        s.id: hashlib.sha256(store.data_path(doc.id, s.id).read_bytes()).hexdigest()
        for s in doc.samples
    }


def test_live_columns_apply_each_original_panel_and_keep_event_identity(client):
    doc, truth, group, gates = setup_group(client)
    before = source_hashes(client, doc)
    view = pooled(client, doc, group)
    assert view.common == ["CD3", "CD4"]
    np.testing.assert_array_equal(view.column(doc, view.view, "CD3"), np.tile(truth[:, 0] / 2, 2))
    np.testing.assert_array_equal(view.column(doc, view.view, "CD4"), np.tile(truth[:, 1] / 4, 2))
    np.testing.assert_array_equal(
        view.column(doc, view.view, "CD3", compensated=False), np.tile(truth[:, 0], 2)
    )
    np.testing.assert_array_equal(
        view.mask(doc, view.view, gates[0].id), [True, True, False, False, False, False, True, True]
    )
    sources, local = view.source_ids([0, 3, 4, 7])
    np.testing.assert_array_equal(sources, [0, 0, 1, 1])
    np.testing.assert_array_equal(local, [0, 3, 0, 3])
    with pytest.raises(ValueError, match="outside"):
        view.source_ids([8])
    with pytest.raises(ValueError, match="no merged"):
        view.raw(doc, view.view)
    assert source_hashes(client, doc) == before
    assert len(client.app.state.store.get(doc.id).samples) == 2


@pytest.mark.parametrize(
    "mode", ["density", "scatter", "histogram", "cdf", "contour", "zebra", "pseudocolor"]
)
def test_exact_pooled_plots_and_qualified_source_overlays(client, mode):
    doc, _, group, gates = setup_group(client)
    one_dimensional = mode in {"histogram", "cdf"}
    params = dict(
        pooled="true",
        group_id=group.id,
        gate_id=gates[0].id,
        x="CD3",
        mode=mode,
        x_transform=Transform().model_dump_json(),
        bins=16,
        bounds=json.dumps([0, 4] if one_dimensional else [0, 4, 0, 2]),
        graph_options=json.dumps({"axis_extent": "full"}),
    )
    if not one_dimensional:
        params.update(y="CD4", y_transform=Transform().model_dump_json())
    response = client.get(
        f"/api/workspaces/{doc.id}/samples/{doc.samples[0].id}/plot", params=params
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert (data["count"], data["finite_count"], data["visible_count"]) == (4, 4, 4)
    assert data["pooled"]["total_events"] == 8
    assert data["pooled"]["population_events"] == 4
    assert [s["count"] for s in data["pooled"]["sources"]] == [2, 2]
    assert {o["pooled_source_id"] for o in data["overlays"]} == set(group.sample_ids)
    assert {o["id"] for o in data["overlays"]} == {gates[0].id}
    if mode in {"histogram", "cdf"}:
        assert sum(data["counts"]) == 4
    if mode == "cdf":
        assert data["cdf_counts"][-1] == 4
        assert data["cdf_percent"][-1] == 100
    if mode == "scatter":
        np.testing.assert_allclose(
            sorted(data["points"]), [[0.5, 0.5], [1.5, 1], [2.5, 1.5], [3.5, 2]]
        )


def test_aggregate_counts_statistics_and_member_mapping(client):
    doc, _, group, gates = setup_group(client)
    params = dict(pooled="true", group_id=group.id)
    prefix = f"/api/workspaces/{doc.id}/samples/{doc.samples[0].id}"
    response = client.get(prefix + "/counts", params=params)
    assert response.status_code == 200, response.text
    assert response.json()[0] == dict(
        id=gates[0].id, count=4, complete=True, percent_parent=50, percent_total=50
    )
    response = client.get(
        prefix + "/statistics", params={**params, "gate_id": gates[0].id, "channel": "CD3"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["mean"] == 2
    assert response.json()["count"] == 4
    view = pooled(client, doc, group)
    assert view.descriptor(gates[0].id)["sources"][1]["population_id"] == gates[1].id


@pytest.mark.parametrize("ambiguous", [False, True])
def test_missing_or_ambiguous_population_never_silently_drops_events(client, ambiguous):
    doc, _, group, gates = setup_group(client)

    def change(candidate):
        if ambiguous:
            candidate.gates.append(gates[1].model_copy(update={"id": "a" * 32}))
        else:
            candidate.gates = [g for g in candidate.gates if g.id != gates[1].id]

    doc = client.app.state.store.mutate(doc.id, "Test population mapping", change, doc.revision)
    view = pooled(client, doc, group)
    with pytest.raises(ValueError, match="ambiguous" if ambiguous else "missing"):
        view.mask(doc, view.view, gates[0].id)
    count = view.counts()[0]
    assert count["count"] is None and count["complete"] is False
    assert doc.samples[1].name in count["error"]


def test_filters_cohort_order_and_cache_do_not_change_single_sample_values(client):
    doc, truth, group, _ = setup_group(client)
    both = pooled(client, doc, group)
    one = pooled(client, doc, group, sample_filter="tube 1")
    assert both.identity != one.identity
    assert len(both.column(doc, both.view, "CD3")) == 8
    assert len(one.column(doc, one.view, "CD3")) == 4
    np.testing.assert_array_equal(
        client.app.state.engine.column(doc, doc.samples[0], "CD3"), truth[:, 0] / 2
    )
    with pytest.raises(ValueError, match="current filter"):
        pooled(client, doc, group, sample_filter="tube 2")
    with pytest.raises(ValueError, match="no samples"):
        pooled(client, doc, group, sample_filter="missing")
    with pytest.raises(ValueError, match="no longer exists"):
        PooledEngine(doc, client.app.state.engine, doc.samples[0].id, "f" * 32)


def test_conflicting_formula_definitions_are_not_pooled(client):
    doc, _, group, _ = setup_group(client)

    def change(candidate):
        for i, sample in enumerate(candidate.samples):
            sample.channels.append(Channel(name="Signal"))
            sample.derived_parameters.append(
                DerivedParameter(name="Signal", expression=f'ch("CD3") * {i + 1}')
            )

    doc = client.app.state.store.mutate(doc.id, "Formula definitions", change, doc.revision)
    view = pooled(client, doc, group)
    assert "Signal" not in view.common
    with pytest.raises(ValueError, match="different definitions"):
        view.column(doc, view.view, "Signal")


def gate_review(client, doc, group, gates, action="create"):
    body = dict(
        revision=doc.revision,
        anchor_id=doc.samples[0].id,
        group_id=group.id,
        action=action,
        gates=[g.model_dump() for g in gates],
    )
    prefix = f"/api/workspaces/{doc.id}/virtual-groups/gates"
    response = client.post(prefix + "/preview", json=body)
    assert response.status_code == 200, response.text
    return body, response.json(), prefix


def test_shared_gate_review_is_read_only_and_apply_is_one_undo(client):
    doc, _, group, gates = setup_group(client)
    before = source_hashes(client, doc)
    child = Gate(
        sample_id=doc.samples[0].id,
        name="Shared child",
        parent_id=gates[0].id,
        kind="range",
        x="CD3",
        bounds=[0, 3],
    )
    body, review, prefix = gate_review(client, doc, group, [child])
    assert client.app.state.store.get(doc.id).model_dump() == doc.model_dump()
    assert [[g["count"] for g in s["populations"]] for s in review["samples"]] == [[2], [1]]
    result = client.post(prefix + "/apply", json={**body, "review_hash": review["review_hash"]})
    assert result.status_code == 200, result.text
    saved = Workspace.model_validate(result.json())
    assert len(saved.gates) == 4 and saved.revision == doc.revision + 1
    second = next(
        g for g in saved.gates if g.sample_id == doc.samples[1].id and g.name == child.name
    )
    assert second.parent_id == gates[1].id
    assert source_hashes(client, saved) == before
    assert saved.compensations == doc.compensations and saved.samples == doc.samples
    undone = client.post(f"/api/workspaces/{doc.id}/undo", json={"revision": saved.revision})
    assert undone.status_code == 200
    assert Workspace.model_validate(undone.json()).gates == doc.gates


def test_shared_edit_preserves_existing_ids_children_and_rejects_changed_review(client):
    doc, _, group, gates = setup_group(client)
    edited = gates[0].model_copy(update={"bounds": [0, 3]})
    body, review, prefix = gate_review(client, doc, group, [edited], "edit")
    tampered = {
        **body,
        "gates": [edited.model_copy(update={"bounds": [0, 4]}).model_dump()],
        "review_hash": review["review_hash"],
    }
    assert client.post(prefix + "/apply", json=tampered).status_code == 409
    assert client.app.state.store.get(doc.id).model_dump() == doc.model_dump()
    result = client.post(prefix + "/apply", json={**body, "review_hash": review["review_hash"]})
    assert result.status_code == 200, result.text
    saved = Workspace.model_validate(result.json())
    assert {g.id for g in saved.gates} == {g.id for g in doc.gates}
    assert all(g.bounds == [0, 3] for g in saved.gates)
    assert (
        client.post(
            prefix + "/apply", json={**body, "review_hash": review["review_hash"]}
        ).status_code
        == 409
    )


def test_shared_partition_reviews_have_stable_ids_and_edit_whole_families(client):
    doc, _, group, _ = setup_group(client)
    gate = Gate(
        sample_id=doc.samples[0].id,
        name="CD3 split",
        kind="hyperrectangle",
        dimensions=[GateDimension(channel="CD3", maximum=2)],
        partition=GatePartition(kind="bisector", member=1),
    )
    body, review, prefix = gate_review(client, doc, group, [gate])
    second_review = client.post(prefix + "/preview", json=body).json()
    assert second_review["review_hash"] == review["review_hash"]
    assert review["population_count"] == 4
    assert [[g["count"] for g in s["populations"]] for s in review["samples"]] == [[2, 2], [2, 2]]
    result = client.post(prefix + "/apply", json={**body, "review_hash": review["review_hash"]})
    assert result.status_code == 200, result.text
    saved = Workspace.model_validate(result.json())
    actual = next(g for g in saved.gates if g.id == gate.id)
    edited = actual.model_copy(update={"dimensions": [GateDimension(channel="CD3", maximum=3)]})
    body, review, prefix = gate_review(client, saved, group, [edited], "edit")
    assert [[g["count"] for g in s["populations"]] for s in review["samples"]] == [[3, 1], [3, 1]]
    result = client.post(prefix + "/apply", json={**body, "review_hash": review["review_hash"]})
    assert result.status_code == 200, result.text
    updated = Workspace.model_validate(result.json())
    assert {g.id for g in saved.gates} == {g.id for g in updated.gates}


def test_group_gate_unavailable_axis_rolls_back_all_members(client):
    doc, _, group, _ = setup_group(client)
    gate = Gate(
        sample_id=doc.samples[0].id,
        name="Invalid common axis",
        kind="range",
        x="FL1",
        bounds=[0, 2],
    )
    body = dict(
        revision=doc.revision,
        anchor_id=doc.samples[0].id,
        group_id=group.id,
        action="create",
        gates=[gate.model_dump()],
    )
    response = client.post(f"/api/workspaces/{doc.id}/virtual-groups/gates/preview", json=body)
    assert response.status_code == 422 and "different definitions" in response.text
    assert client.app.state.store.get(doc.id).model_dump() == doc.model_dump()


def test_group_inspection_uses_actual_inputs_for_joint_models(client):
    doc, _, group, gates = setup_group(client)
    response = client.post(
        f"/api/workspaces/{doc.id}/virtual-groups/inspect",
        json=dict(anchor_id=doc.samples[0].id, group_id=group.id, gate_id=gates[0].id),
    )
    assert response.status_code == 200, response.text
    assert response.json()["analysis_inputs"] == [
        dict(sample_id=s.id, gate_id=g.id) for s, g in zip(doc.samples, gates, strict=True)
    ]


@pytest.mark.parametrize("values", ["raw", "compensated", "scale"])
@pytest.mark.parametrize("file_format", ["fcs", "csv"])
def test_pooled_export_preserves_full_values_populations_and_member_identities(
    client, tmp_path, values, file_format
):
    doc, truth, group, gates = setup_group(client)
    hashes = source_hashes(client, doc)
    scope = dict(anchor_id=doc.samples[0].id, group_id=group.id, gate_id=gates[0].id)
    request = event_exports.Request(
        revision=doc.revision,
        sample_id=doc.samples[0].id,
        gate_id=gates[0].id,
        scope=scope,
        values=values,
        format=file_format,
    )
    path, summary = event_exports.write_export(
        client.app.state.store, client.app.state.engine, doc, request, tmp_path / "pooled-file"
    )
    expected = truth[:, :2].copy()
    if values != "raw":
        expected /= [2, 4]
    if values == "scale":
        expected = np.arcsinh(expected / 2)
    expected = np.column_stack([expected, [1, 1, 2, 2], [0, 1, 2, 3]])
    if file_format == "csv":
        actual = np.loadtxt(path, delimiter=",", skiprows=1)
    else:
        external = flowio.FlowData(str(path))
        actual = np.asarray(external.events, dtype=float).reshape(4, 4)
        assert external.channel_count == 4 and external.event_count == 4
        assert ("spillover" in external.text) == (values == "raw")
        (tmp_path / "restored").mkdir()
        imported = stream_fcs(path, "Pooled population.fcs", tmp_path / "restored")[0]
        restored, matrix = imported.sample, imported.compensation
        np.testing.assert_allclose(np.load(imported.path), expected, rtol=0, atol=1e-15)
        origins = np.load(imported.path.with_name(restored.id + ".origins.npy"))
        np.testing.assert_array_equal(origins, expected[:, -2:].astype(np.uint64))
        assert [s.sample_id for s in restored.concatenation.sources] == group.sample_ids
        assert [s.gate_id for s in restored.concatenation.sources] == [g.id for g in gates]
        assert bool(matrix) == (values == "raw")
    np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-15)
    assert summary["event_count"] == 4 and summary["pooled_sample_count"] == 2
    assert summary["exact_event_origins"] is True
    assert source_hashes(client, doc) == hashes
    assert client.app.state.store.get(doc.id).model_dump() == doc.model_dump()


def test_pooled_shape_preview_reports_whole_group_and_keeps_live_cache_unchanged(client):
    doc, _, group, gates = setup_group(client)
    before = (
        pooled(client, doc, group).mask(doc, pooled(client, doc, group).view, gates[0].id).copy()
    )
    edited = gates[0].model_copy(update={"bounds": [0, 3]})
    response = client.post(
        f"/api/workspaces/{doc.id}/gates/preview-shape",
        json=dict(
            revision=doc.revision,
            gate=edited.model_dump(),
            mode="histogram",
            scope=dict(anchor_id=doc.samples[0].id, group_id=group.id, gate_id=None),
        ),
    )
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 6 and response.json()["parent_count"] == 8
    assert response.json()["plot"]["pooled"]["total_events"] == 8
    view = pooled(client, doc, group)
    np.testing.assert_array_equal(view.mask(doc, view.view, gates[0].id), before)


def test_pooled_3d_stream_keeps_distinct_global_ids_and_original_coordinate_owner(client):
    doc, _, group, gates = setup_group(client)
    prefix = f"/api/workspaces/{doc.id}/samples/{doc.samples[0].id}"
    params = dict(
        pooled="true",
        group_id=group.id,
        x="CD3",
        y="CD4",
        mode="3d",
        gate_id=gates[0].id,
        coordinate_gate_id=gates[0].id,
        x_transform=Transform().model_dump_json(),
        y_transform=Transform().model_dump_json(),
        three_d=json.dumps(dict(z="CD3", z_transform=Transform().model_dump(), all_events=True)),
        bounds=json.dumps([0, 4, 0, 2, 0, 4]),
    )
    response = client.get(prefix + "/plot", params=params)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["count"] == 4 and payload["displayed_count"] == 4
    response = client.get(
        prefix + "/plot3d/points",
        params={**params, "revision": doc.revision, "data_key": payload["data_key"]},
    )
    assert response.status_code == 200, response.text
    points = np.frombuffer(response.content, dtype=POINT_DTYPE)
    np.testing.assert_array_equal(points["event_id"], [0, 1, 6, 7])
    view = pooled(client, doc, group)
    sources, local = view.source_ids(points["event_id"])
    np.testing.assert_array_equal(sources, [0, 0, 1, 1])
    np.testing.assert_array_equal(local, [0, 1, 2, 3])
    mismatch = client.get(
        prefix + "/plot3d/points",
        params={
            **params,
            "pooled": "false",
            "revision": doc.revision,
            "data_key": payload["data_key"],
        },
    )
    assert mismatch.status_code == 409


@pytest.mark.parametrize("mode", ["histogram", "density", "3d"])
def test_pooled_report_has_every_original_source_and_checks_their_bytes(client, mode):
    doc, _, group, gates = setup_group(client)
    definition = PlotDefinition(
        sample_id=doc.samples[0].id,
        gate_id=gates[0].id,
        pooled=True,
        group_id=group.id,
        mode=mode,
        x="CD3",
        y=None if mode == "histogram" else "CD4",
        x_transform=Transform(),
        y_transform=Transform(),
        three_d=dict(z="CD3", z_transform=Transform(), all_events=True) if mode == "3d" else None,
    )
    layers = [
        dict(
            sample_id=doc.samples[0].id,
            source_sample_id=doc.samples[0].id,
            gate_id=gates[0].id,
            coordinate_gate_id=None,
            label="",
            color="#087e8b",
            locked_control=False,
        )
    ]
    _, manifest = figure(doc, client.app.state.engine, definition, layers, 100, 85)
    assert manifest["layers"][0]["population_count"] == 4
    assert group.name in manifest["layers"][0]["label"]
    source = manifest["layers"][0]["source"]
    assert source["kind"] == "virtual_group"
    assert [s["sample_id"] for s in source["sources"]] == group.sample_ids
    audit = SourceAudit(doc)
    audit.validate_frame(client.app.state.engine, manifest)
    path = client.app.state.store.data_path(doc.id, doc.samples[1].id)
    original = path.read_bytes()
    path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    with pytest.raises(ValueError, match="SHA-256"):
        SourceAudit(doc).validate_frame(client.app.state.engine, manifest)


def test_pooled_deletion_reviews_entire_partitions_and_children_and_undo_restores_all(client):
    doc, _, group, roots = setup_group(client)
    hashes = source_hashes(client, doc)
    child = Gate(
        sample_id=doc.samples[0].id,
        name="Child partition",
        parent_id=roots[0].id,
        kind="hyperrectangle",
        dimensions=[GateDimension(channel="CD4", maximum=1)],
        partition=GatePartition(kind="bisector", member=1),
    )
    body, review, prefix = gate_review(client, doc, group, [child])
    response = client.post(prefix + "/apply", json={**body, "review_hash": review["review_hash"]})
    assert response.status_code == 200, response.text
    saved = Workspace.model_validate(response.json())
    parent = next(g for g in saved.gates if g.id == roots[0].id)
    body, review, prefix = gate_review(client, saved, group, [parent], "delete")
    assert review["population_count"] == 6
    assert [len(s["populations"]) for s in review["samples"]] == [3, 3]
    assert client.app.state.store.get(doc.id).gates == saved.gates
    response = client.post(prefix + "/apply", json={**body, "review_hash": review["review_hash"]})
    assert response.status_code == 200, response.text
    after = Workspace.model_validate(response.json())
    assert (
        not after.gates
        and after.samples == saved.samples
        and after.compensations == saved.compensations
    )
    undone = client.post(f"/api/workspaces/{doc.id}/undo", json={"revision": after.revision})
    assert undone.status_code == 200
    assert Workspace.model_validate(undone.json()).gates == saved.gates
    assert source_hashes(client, doc) == hashes


def test_group_gate_review_identifies_stale_joint_pca_and_undo_restores_its_validity(client):
    doc, _, group, gates = setup_group(client)
    response = client.post(
        f"/api/workspaces/{doc.id}/virtual-groups/inspect",
        json=dict(anchor_id=doc.samples[0].id, group_id=group.id, gate_id=gates[0].id),
    )
    info = response.json()
    job = _submit(
        client,
        doc.model_dump(),
        dict(
            revision=doc.revision,
            name="Joint cohort PCA",
            algorithm="pca",
            inputs=info["analysis_inputs"],
            channels=["CD3", "CD4"],
            max_events=100,
            use_transforms=False,
        ),
    )
    completed = _wait(client, doc.model_dump(), job)
    assert completed["status"] == "succeeded", completed
    fitted = Workspace.model_validate(_apply(client, doc.model_dump(), job))
    result = fitted.analyses[0]
    assert not is_stale(fitted, result)
    assert [s.sample_id for s in result.data] == group.sample_ids
    view = pooled(client, fitted, group)
    assert set(result.columns) <= set(view.common)
    values = view.column(fitted, view.view, result.columns[0])
    assert len(values) == 8
    np.testing.assert_array_equal(
        np.isfinite(values), [True, True, False, False, False, False, True, True]
    )
    edited = next(g for g in fitted.gates if g.id == gates[0].id).model_copy(
        update={"bounds": [0, 3]}
    )
    body, review, prefix = gate_review(client, fitted, group, [edited], "edit")
    assert review["affected_models"] == [dict(id=result.id, name=result.request.name)]
    response = client.post(prefix + "/apply", json={**body, "review_hash": review["review_hash"]})
    assert response.status_code == 200, response.text
    changed = Workspace.model_validate(response.json())
    assert is_stale(changed, changed.analyses[0])
    with pytest.raises(ValueError, match="stale"):
        pooled(client, changed, group).column(
            changed, pooled(client, changed, group).view, result.columns[0]
        )
    undone = client.post(f"/api/workspaces/{doc.id}/undo", json={"revision": changed.revision})
    assert undone.status_code == 200
    restored = Workspace.model_validate(undone.json())
    assert not is_stale(restored, restored.analyses[0])


def test_raw_pooled_aliases_do_not_duplicate_detectors_or_split_correction_basis(client, tmp_path):
    doc, truth, group, _ = setup_group(client)

    def same_panel(candidate):
        first = candidate.samples[0]
        second = candidate.samples[1]
        second.channels = [c.model_copy(deep=True) for c in first.channels]
        second.aliases = dict(first.aliases)
        second.compensation_id = first.compensation_id
        from cytoforge.science import save_events

        second.sha256 = save_events(client.app.state.store.data_path(doc.id, second.id), truth)

    doc = client.app.state.store.mutate(doc.id, "Identical panel fixture", same_panel, doc.revision)
    request = event_exports.Request(
        revision=doc.revision,
        sample_id=doc.samples[0].id,
        scope=dict(anchor_id=doc.samples[0].id, group_id=group.id),
        values="raw",
    )
    path, summary = event_exports.write_export(
        client.app.state.store, client.app.state.engine, doc, request, tmp_path / "raw-aliases"
    )
    assert summary["channels"] == ["CD3", "CD4", "FL3", "CF_Source", "CF_EventID"]
    external = flowio.FlowData(path)
    actual = np.asarray(external.events).reshape(8, 5)
    np.testing.assert_array_equal(actual[:, :3], np.tile(truth, (2, 1)))
    assert "CD3,CD4" in external.text["spillover"]


def test_empty_members_preserve_event_offsets_counts_and_no_nan_dropping(client):
    from cytoforge.science import save_events

    doc, _, group, gates = setup_group(client)

    def empty_first(candidate):
        sample = candidate.samples[0]
        sample.event_count = 0
        sample.sha256 = save_events(
            client.app.state.store.data_path(doc.id, sample.id), np.empty((0, 3))
        )

    doc = client.app.state.store.mutate(doc.id, "Empty member fixture", empty_first, doc.revision)
    view = pooled(client, doc, group)
    sources, local = view.source_ids([0, 3])
    np.testing.assert_array_equal(sources, [1, 1])
    np.testing.assert_array_equal(local, [0, 3])
    assert [s["count"] for s in view.descriptor(gates[0].id)["sources"]] == [0, 2]
    assert view.counts()[0]["count"] == 2


def test_different_raw_matrices_require_explicit_compensated_pooled_export(client, tmp_path):
    doc, _, group, _ = setup_group(client)

    def change(candidate):
        candidate.compensations[1].matrix = [[8, 0], [0, 2]]

    doc = client.app.state.store.mutate(
        doc.id, "Different correction fixture", change, doc.revision
    )
    scope = dict(anchor_id=doc.samples[0].id, group_id=group.id)
    request = event_exports.Request(
        revision=doc.revision, sample_id=doc.samples[0].id, scope=scope, values="raw"
    )
    with pytest.raises(ValueError, match="matrices differ"):
        event_exports.write_export(
            client.app.state.store, client.app.state.engine, doc, request, tmp_path / "raw"
        )
    assert not list((tmp_path / "raw").glob("*"))
    corrected = request.model_copy(update={"values": "compensated"})
    path, summary = event_exports.write_export(
        client.app.state.store, client.app.state.engine, doc, corrected, tmp_path / "corrected"
    )
    assert not summary["matrix_preserved"] and summary["event_count"] == 8
    actual = np.asarray(flowio.FlowData(path).events).reshape(8, 4)
    np.testing.assert_array_equal(actual[4:, 1], [0.25, 0.5, 0.75, 1])
