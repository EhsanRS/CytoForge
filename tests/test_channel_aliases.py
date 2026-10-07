"""Panel harmonization against independent detector values and durable scientific state."""

import copy
import hashlib

import flowio
import numpy as np
import pytest
from cytoforge import channel_aliases, concatenation, event_exports, quality
from cytoforge.analysis import input_hash, is_stale
from cytoforge.compensation import assign_matrix
from cytoforge.gatingml import export_gatingml
from cytoforge.imports import stream_fcs
from cytoforge.interchange import parse_document
from cytoforge.models import (
    AnalysisRequest,
    Channel,
    Compensation,
    DerivedParameter,
    Gate,
    GateDimension,
    LayoutDefinition,
    PlateDefinition,
    PlotDefinition,
    QualityRequest,
    ReportElement,
    Sample,
    TableColumn,
    TableDefinition,
    Transform,
    Workspace,
    new_id,
)
from cytoforge.report_sources import SourceAudit
from cytoforge.science import Engine, save_events
from test_analysis import _apply, _submit, _wait
from test_concatenation import apply, start, wait


def cohort(client):
    store = client.app.state.store
    truth = np.array([[1, 2, 9], [3, 4, 8], [5, 6, 7], [7, 8, 6]], dtype=float)
    matrices = [
        Compensation(name="Panel A", detectors=["FL1", "FL2"], matrix=[[2, 0], [0, 4]]),
        Compensation(name="Panel B", detectors=["B2", "B1"], matrix=[[4, 0], [0, 2]]),
    ]
    samples = [
        Sample(
            name="Tube " + str(i + 1),
            event_count=len(truth),
            channels=[
                Channel(
                    name=n, label=label, range=100, transform=Transform(kind="asinh", cofactor=2)
                )
                for n, label in zip(names, labels, strict=True)
            ],
            compensation_id=matrices[i].id,
        )
        for i, (names, labels) in enumerate(
            [
                ("FL1 FL2 FL3".split(), ["CD3", "CD4", "Background"]),
                ("B2 B1 B3".split(), ["CD4", "CD3", "Background"]),
            ]
        )
    ]
    doc = Workspace(name="Two detector panels", samples=samples, compensations=matrices)
    for sample, values in zip(samples, [truth, truth[:, [1, 0, 2]]], strict=True):
        sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    store.create(doc)
    return store.get(doc.id), truth


def request(doc, names=("CD3", "CD4")):
    originals = [("FL1", "FL2"), ("B1", "B2")]
    return dict(
        revision=doc.revision,
        mappings=[
            dict(
                sample_id=s.id,
                bindings=[dict(name=n, source=o) for n, o in zip(names, sources, strict=True)],
            )
            for s, sources in zip(doc.samples, originals, strict=True)
        ],
    )


def harmonize(client, doc, body=None):
    body = body or request(doc)
    prefix = f"/api/workspaces/{doc.id}/channel-aliases"
    preview = client.post(prefix + "/preview", json=body)
    assert preview.status_code == 200, preview.text
    result = client.post(
        prefix + "/apply", json={**body, "review_hash": preview.json()["review_hash"]}
    )
    assert result.status_code == 200, result.text
    return Workspace.model_validate(result.json()), preview.json()


def test_review_and_one_undoable_apply_preserve_measurements_and_matrix_bindings(client):
    doc, _ = cohort(client)
    store = client.app.state.store
    before = doc.model_dump_json()
    history = store.history(doc.id)
    hashes = [s.sha256 for s in doc.samples]
    prefix = f"/api/workspaces/{doc.id}/channel-aliases"
    preview = client.post(prefix + "/preview", json=request(doc))
    assert preview.status_code == 200 and preview.json()["changed"]
    assert store.get(doc.id).model_dump_json() == before
    assert store.history(doc.id) == history
    after, _ = harmonize(client, doc)
    assert after.revision == doc.revision + 1
    assert len(store.history(doc.id)["entries"]) == len(history["entries"]) + 1
    assert after.compensations == doc.compensations
    for old, new, expected in zip(doc.samples, after.samples, hashes, strict=True):
        assert new.acquisition_channels == old.acquisition_channels
        assert new.compensation_id == old.compensation_id and new.sha256 == old.sha256
        assert hashlib.sha256(store.data_path(doc.id, new.id).read_bytes()).hexdigest() == expected
    undone = client.post(f"/api/workspaces/{doc.id}/undo", json={"revision": after.revision}).json()
    assert undone["samples"] == [s.model_dump() for s in doc.samples]
    redone = client.post(
        f"/api/workspaces/{doc.id}/redo", json={"revision": undone["revision"]}
    ).json()
    assert redone["samples"] == [s.model_dump() for s in after.samples]


def test_reordered_panels_resolve_raw_compensated_transformed_ratios_and_fixed_matrices(client):
    doc, truth = cohort(client)
    doc, _ = harmonize(client, doc)
    engine = Engine(client.app.state.store)
    for sample in doc.samples:
        np.testing.assert_array_equal(
            engine.column(doc, sample, "CD3", compensated=False), truth[:, 0]
        )
        np.testing.assert_array_equal(
            engine.column(doc, sample, "CD4", compensated=False), truth[:, 1]
        )
        np.testing.assert_allclose(engine.column(doc, sample, "CD3"), truth[:, 0] / 2)
        np.testing.assert_allclose(engine.column(doc, sample, "CD4"), truth[:, 1] / 4)
        np.testing.assert_allclose(
            engine.column(doc, sample, "CD3", Transform(kind="asinh", cofactor=3)),
            np.arcsinh(truth[:, 0] / 6),
        )
        ratio = GateDimension(channel="CD3/CD4", ratio_channels=("CD3", "CD4"))
        np.testing.assert_allclose(
            engine.dimension(doc, sample, ratio), truth[:, 0] * 2 / truth[:, 1]
        )
        gate = Gate(sample_id=sample.id, name="Shared CD3", kind="range", x="CD3", bounds=[1, 2.5])
        doc.gates.append(gate)
        np.testing.assert_array_equal(
            engine.mask(doc, sample, gate.id), [False, True, False, False]
        )
    fixed = Compensation(name="Fixed", detectors=["FL1", "FL2"], matrix=[[4, 0], [0, 2]])
    doc.compensations.append(fixed)
    np.testing.assert_allclose(
        engine.column(doc, doc.samples[0], "CD3", compensation_ref=fixed.id), truth[:, 0] / 4
    )
    np.testing.assert_array_equal(
        engine.column(doc, doc.samples[0], "CD3", compensation_ref="uncompensated"), truth[:, 0]
    )


def test_legacy_workspace_bytes_and_unrelated_fingerprints_are_unchanged(client):
    doc, _ = cohort(client)
    for sample in doc.samples:
        legacy = sample.model_dump_json()
        assert '"aliases"' not in legacy
        assert Sample.model_validate_json(legacy).model_dump_json() == legacy
    basis = AnalysisRequest(
        revision=doc.revision,
        name="Original detectors",
        algorithm="pca",
        inputs=[dict(sample_id=doc.samples[0].id)],
        channels=["FL1", "FL2"],
    )
    old_hash = input_hash(doc, basis)
    old_closure = SourceAudit(doc).closure(doc.samples[0].id, ["FL1", "FL2"])
    after, _ = harmonize(client, doc)
    assert input_hash(after, basis) == old_hash
    assert SourceAudit(after).closure(after.samples[0].id, ["FL1", "FL2"]) == old_closure
    assert (
        channel_aliases.plan(after, channel_aliases.Request(**request(after)))[1]["changed"]
        is False
    )


@pytest.mark.parametrize(
    "bad", ["missing", "alias_chain", "collision", "duplicate", "foreign", "formula_source"]
)
def test_invalid_cohort_mapping_is_atomic(client, bad):
    doc, _ = cohort(client)
    before = doc.model_dump_json()
    body = request(doc)
    last = body["mappings"][-1]
    if bad == "missing":
        last["bindings"][0]["source"] = "Missing"
    elif bad == "alias_chain":
        last["bindings"][1]["source"] = "CD3"
    elif bad == "collision":
        last["bindings"][0]["name"] = "B2"
    elif bad == "duplicate":
        last["bindings"].append(last["bindings"][0])
    elif bad == "foreign":
        last["sample_id"] = new_id()
    else:
        last["bindings"][0]["source"] = "Formula output"
    response = client.post(f"/api/workspaces/{doc.id}/channel-aliases/preview", json=body)
    assert response.status_code == 422, response.text
    assert client.app.state.store.get(doc.id).model_dump_json() == before


def test_apply_rejects_changed_review_payload_revision_and_unauthenticated_requests(client):
    doc, _ = cohort(client)
    body = request(doc)
    prefix = f"/api/workspaces/{doc.id}/channel-aliases"
    preview = client.post(prefix + "/preview", json=body).json()
    edited = copy.deepcopy(body)
    edited["mappings"][0]["bindings"][0]["source"] = "FL3"
    response = client.post(
        prefix + "/apply", json={**edited, "review_hash": preview["review_hash"]}
    )
    assert response.status_code == 409
    assert client.app.state.store.get(doc.id).revision == doc.revision
    after, _ = harmonize(client, doc)
    assert (
        client.post(
            prefix + "/apply", json={**body, "review_hash": preview["review_hash"]}
        ).status_code
        == 409
    )
    token = client.headers.pop("X-CytoForge-Token")
    try:
        assert client.post(prefix + "/preview", json=request(after)).status_code == 401
    finally:
        client.headers["X-CytoForge-Token"] = token


def test_identity_binding_uses_existing_detector_without_adding_an_alias(client):
    doc, _ = cohort(client)
    body = request(doc)
    body["mappings"][0]["bindings"] = [dict(name="FL1", source="FL1")]
    after, preview = harmonize(client, doc, body)
    assert after.samples[0] == doc.samples[0]
    assert preview["samples"][0]["bindings"][0]["action"] == "already named"


@pytest.mark.parametrize(
    "consumer", ["gate", "formula", "table", "plate", "layout", "positioned_layout"]
)
def test_referenced_alias_removal_names_the_consumer_and_preserves_state(client, consumer):
    doc, _ = cohort(client)
    doc, _ = harmonize(client, doc)
    sample = doc.samples[0]
    if consumer == "gate":
        doc.gates.append(
            Gate(sample_id=sample.id, name="Saved population", kind="range", x="CD3", bounds=[0, 2])
        )
    elif consumer == "formula":
        sample.channels.append(Channel(name="Fold"))
        sample.derived_parameters.append(
            DerivedParameter(name="Fold", expression='ch("CD3") / max(ch("CD4"), 1)')
        )
    elif consumer == "table":
        doc.tables.append(
            TableDefinition(name="Saved table", sample_ids=[sample.id], channel="CD3")
        )
    elif consumer == "plate":
        doc.plates.append(
            PlateDefinition(
                name="Saved plate",
                assignments={"A1": [sample.id]},
                columns=[TableColumn(name="Median", channel="CD3", statistic="median")],
            )
        )
    else:
        plot = PlotDefinition(sample_id=sample.id, x="CD3", y="CD4")
        doc.layouts.append(
            LayoutDefinition(
                name="Saved layout",
                **(
                    dict(plots=[plot])
                    if consumer == "layout"
                    else dict(elements=[ReportElement(kind="plot", plot=plot)])
                ),
            )
        )
    doc = client.app.state.store.mutate(
        doc.id,
        "Save dependent definition",
        lambda current: current.__dict__.update(doc.__dict__),
        doc.revision,
    )
    body = request(doc)
    body["mappings"][0]["bindings"] = [dict(name="CD4", source="FL2")]
    response = client.post(f"/api/workspaces/{doc.id}/channel-aliases/preview", json=body)
    assert response.status_code == 422 and "cannot remove CD3" in response.text
    assert "Saved" in response.text or "Fold" in response.text
    assert client.app.state.store.get(doc.id).model_dump_json() == doc.model_dump_json()


def test_unused_alias_removal_and_rebinding_preserve_independent_display_transform(client):
    doc, _ = cohort(client)
    doc, _ = harmonize(client, doc)
    original = doc.samples[0].channels[-2].transform
    body = request(doc)
    body["mappings"][0]["bindings"] = [dict(name="CD3", source="FL3")]
    after, preview = harmonize(client, doc, body)
    assert after.samples[0].aliases == {"CD3": "FL3"}
    assert after.samples[0].channels[-1].transform == original
    assert {b["action"] for b in preview["samples"][0]["bindings"]} == {"removed", "rebound"}


def test_fitted_model_tracks_alias_binding_and_review_reports_staleness(client):
    doc, _ = cohort(client)
    doc, _ = harmonize(client, doc)
    body = dict(
        revision=doc.revision,
        name="Shared-panel PCA",
        algorithm="pca",
        inputs=[dict(sample_id=s.id) for s in doc.samples],
        channels=["CD3", "CD4"],
        max_events=100,
        use_transforms=False,
    )
    identifier = _submit(client, doc.model_dump(), body)
    completed = _wait(client, doc.model_dump(), identifier)
    assert completed["status"] == "succeeded", completed
    fitted = Workspace.model_validate(_apply(client, doc.model_dump(), identifier))
    result = fitted.analyses[0]
    assert not is_stale(fitted, result)
    mapping = request(fitted)
    mapping["mappings"][0]["bindings"][0]["source"] = "FL3"
    rebound, preview = harmonize(client, fitted, mapping)
    assert preview["affected_models"] == [dict(id=result.id, name=result.request.name)]
    assert is_stale(rebound, rebound.analyses[0])
    assert rebound.analyses[0].model_dump() == result.model_dump()
    closure = SourceAudit(rebound).closure(rebound.samples[0].id, ["CD3"])
    assert closure["parameters"]["CD3"]["alias_source"] == "FL3"
    assert "FL3" in closure["parameters"]


@pytest.mark.parametrize("values", ["raw", "compensated", "scale"])
def test_fcs_round_trip_keeps_original_detectors_or_materializes_aliases_once(
    client, tmp_path, values
):
    doc, truth = cohort(client)
    doc, _ = harmonize(client, doc)
    store = client.app.state.store
    sample = doc.samples[0]
    path, _ = event_exports.write_export(
        store,
        Engine(store),
        doc,
        event_exports.Request(revision=doc.revision, sample_id=sample.id, values=values),
        tmp_path / "export",
    )
    external = flowio.FlowData(path)
    (tmp_path / "import").mkdir()
    imported = stream_fcs(path, "Panel.fcs", tmp_path / "import")[0]
    assert imported.sample.acquisition_channels[0].name == "FL1"
    columns = np.load(imported.path)
    if values == "raw":
        assert external.channel_count == 3
        assert imported.sample.aliases == sample.aliases
        assert imported.compensation.detectors == ["FL1", "FL2"]
        np.testing.assert_array_equal(columns, truth)
    else:
        assert external.channel_count == 5
        assert not imported.sample.aliases and imported.compensation is None
        expected = truth[:, 0] / 2
        if values == "scale":
            expected = np.arcsinh(expected / 2)
        np.testing.assert_allclose(columns[:, 3], expected)
        np.testing.assert_allclose(columns[:, 0], columns[:, 3])
    assert imported.sample.event_export.source_snapshot["aliases"] == sample.aliases


def test_raw_concatenation_maps_aliases_to_original_matrix_and_exact_measurements(client):
    doc, truth = cohort(client)
    doc, _ = harmonize(client, doc)
    body = dict(
        revision=doc.revision,
        name="Harmonized merge",
        inputs=[dict(sample_id=s.id) for s in doc.samples],
        parameters=[dict(name=n, sources={s.id: n for s in doc.samples}) for n in ["CD3", "CD4"]],
    )
    session = wait(client, doc, start(client, doc, body))
    assert session["status"] == "ready", session
    response = apply(client, doc, session)
    assert response.status_code == 200, response.text
    result = Workspace.model_validate(response.json())
    merged = result.samples[-1]
    columns = np.load(client.app.state.store.data_path(doc.id, merged.id))
    np.testing.assert_array_equal(columns[:, :2], np.vstack([truth[:, :2], truth[:, :2]]))
    matrix = next(m for m in result.compensations if m.id == merged.compensation_id)
    assert matrix.detectors == ["CD3", "CD4"] and matrix.matrix == [[2, 0], [0, 4]]
    assert merged.concatenation.sources[0].snapshot["parameters"]["CD3"]["alias_source"] == "FL1"
    duplicate = copy.deepcopy(body)
    duplicate["parameters"].append(
        dict(name="Original CD3", sources={s.id: s.aliases["CD3"] for s in doc.samples})
    )
    with pytest.raises(ValueError, match="each acquired column only once"):
        concatenation.plan(doc, concatenation.Request(**duplicate))


def test_spectral_aliases_reconstruct_weighted_outputs_and_raw_fcs_display_definitions(
    client, tmp_path
):
    store = client.app.state.store
    mixing = np.array([[1, 0.2], [0.1, 1], [0.4, 0.3]])
    expected = np.array([[2, 3], [4, 5], [6, 7]], dtype=float)
    raw = expected @ mixing.T + [10, 20, 30]
    matrix = Compensation(
        name="Weighted panel",
        detectors=["D1", "D2", "D3"],
        outputs=["U1", "U2"],
        kind="spectral",
        matrix=mixing.T.tolist(),
        background=[10, 20, 30],
        weights=[1, 2, 3],
    )
    sample = Sample(
        name="Spectral", event_count=3, channels=[Channel(name=n) for n in matrix.detectors]
    )
    assign_matrix(sample, matrix)
    doc = Workspace(name="Spectral panel", samples=[sample], compensations=[matrix])
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), raw)
    doc = store.create(doc)
    mapping = dict(
        revision=doc.revision,
        mappings=[dict(sample_id=sample.id, bindings=[dict(name="CD3", source="U1")])],
    )
    doc, _ = harmonize(client, doc, mapping)
    sample = doc.samples[0]
    np.testing.assert_allclose(Engine(store).column(doc, sample, "CD3"), expected[:, 0])
    path, _ = event_exports.write_export(
        store,
        Engine(store),
        doc,
        event_exports.Request(revision=doc.revision, sample_id=sample.id),
        tmp_path / "export",
    )
    (tmp_path / "import").mkdir()
    restored = stream_fcs(path, "Spectral.fcs", tmp_path / "import")[0]
    assert restored.sample.aliases == {"CD3": "U1"}
    assert restored.compensation.weights == [1, 2, 3] and restored.compensation.background == [
        10,
        20,
        30,
    ]
    imported_doc = Workspace(
        name="Restored spectral", samples=[restored.sample], compensations=[restored.compensation]
    )
    restored.sample.sha256 = save_events(
        store.data_path(imported_doc.id, restored.sample.id), np.load(restored.path)
    )
    imported_doc = store.create(imported_doc)
    np.testing.assert_allclose(
        Engine(store).column(imported_doc, imported_doc.samples[0], "CD3"), expected[:, 0]
    )


def test_qc_raw_aliases_use_original_ranges_and_track_raw_rebindings(client):
    doc, _ = cohort(client)
    doc, _ = harmonize(client, doc)
    sample = doc.samples[0]
    request_qc = QualityRequest(
        revision=doc.revision,
        sample_id=sample.id,
        channels=["CD4"],
        time_channel="CD3",
        saturation_channels=["CD3"],
        pulse_area="CD3",
        pulse_height="CD4",
    )
    before = quality.input_snapshot(doc, request_qc)
    assert {c["alias_source"] for c in before["raw_channels"]} == {"FL1", "FL2"}
    body = request(doc)
    body["mappings"][0]["bindings"][0]["source"] = "FL3"
    after, _ = harmonize(client, doc, body)
    assert quality.input_snapshot(after, request_qc) != before


def test_formulas_resolve_alias_sources_and_track_changed_bindings(client):
    doc, truth = cohort(client)
    doc, _ = harmonize(client, doc)
    sample = doc.samples[0]
    sample.channels.append(Channel(name="Fold"))
    sample.derived_parameters.append(
        DerivedParameter(name="Fold", expression='ch("CD3") / max(ch("CD4"), 1)')
    )
    doc = client.app.state.store.mutate(
        doc.id,
        "Save alias formula",
        lambda current: setattr(current, "samples", doc.samples),
        doc.revision,
    )
    engine = Engine(client.app.state.store)
    sample = doc.samples[0]
    expected = (truth[:, 0] / 2) / np.maximum(truth[:, 1] / 4, 1)
    np.testing.assert_array_equal(engine.column(doc, sample, "Fold"), expected)
    closure = SourceAudit(doc).closure(sample.id, ["Fold"])
    assert {"Fold", "CD3", "CD4", "FL1", "FL2"} <= closure["parameters"].keys()
    basis = AnalysisRequest(
        revision=doc.revision,
        name="Formula fit",
        algorithm="pca",
        inputs=[dict(sample_id=sample.id)],
        channels=["Fold", "CD4"],
        use_transforms=False,
    )
    before = input_hash(doc, basis)
    body = request(doc)
    body["mappings"][0]["bindings"][0]["source"] = "FL3"
    after, _ = harmonize(client, doc, body)
    assert input_hash(after, basis) != before
    np.testing.assert_array_equal(
        engine.column(after, after.samples[0], "Fold"), truth[:, 2] / np.maximum(truth[:, 1] / 4, 1)
    )


def test_alias_fcs_over_already_corrected_stored_values_never_applies_correction_twice(
    client, tmp_path
):
    doc, truth = cohort(client)
    store = client.app.state.store
    sample = doc.samples[0]
    directory = tmp_path / "first"
    first, _ = event_exports.write_export(
        store,
        Engine(store),
        doc,
        event_exports.Request(revision=doc.revision, sample_id=sample.id, values="compensated"),
        directory,
    )
    restored = stream_fcs(first, "Materialized.fcs", directory)[0]
    corrected_doc = Workspace(name="Corrected stored detector basis", samples=[restored.sample])
    restored.sample.sha256 = save_events(
        store.data_path(corrected_doc.id, restored.sample.id), np.load(restored.path)
    )
    store.create(corrected_doc)
    corrected_doc = store.get(corrected_doc.id)
    mapping = dict(
        revision=corrected_doc.revision,
        mappings=[
            dict(
                sample_id=restored.sample.id,
                bindings=[dict(name="CD3", source="FL1"), dict(name="CD4", source="FL2")],
            )
        ],
    )
    corrected_doc, _ = harmonize(client, corrected_doc, mapping)
    exported, _ = event_exports.write_export(
        store,
        Engine(store),
        corrected_doc,
        event_exports.Request(revision=corrected_doc.revision, sample_id=restored.sample.id),
        tmp_path / "second",
    )
    reopened = stream_fcs(exported, "Aliases on corrected data.fcs", tmp_path / "second")[0]
    assert reopened.sample.aliases == {"CD3": "FL1", "CD4": "FL2"}
    assert reopened.sample.event_export.values == "compensated" and reopened.compensation is None
    np.testing.assert_array_equal(np.load(reopened.path)[:, :2], truth[:, :2] / [2, 4])


def test_table_per_sample_channel_override_protects_alias_removal(client):
    doc, _ = cohort(client)
    doc, _ = harmonize(client, doc)
    sample = doc.samples[0]
    table = TableDefinition(
        name="Per-sample marker table",
        sample_ids=[sample.id],
        columns=[
            TableColumn(
                name="Median",
                statistic="median",
                channel="FL3",
                channel_overrides={sample.id: "CD3"},
            )
        ],
    )
    doc = client.app.state.store.mutate(
        doc.id, "Save per-sample table", lambda current: current.tables.append(table), doc.revision
    )
    body = request(doc)
    body["mappings"][0]["bindings"] = [dict(name="CD4", source="FL2")]
    response = client.post(f"/api/workspaces/{doc.id}/channel-aliases/preview", json=body)
    assert response.status_code == 422 and "Per-sample marker table" in response.text


def test_archive_reopen_retains_aliases_without_copying_original_measurements(client):
    doc, truth = cohort(client)
    doc, _ = harmonize(client, doc)
    exported = client.get(f"/api/workspaces/{doc.id}/export/project")
    assert exported.status_code == 200
    imported = client.post(
        "/api/import/project", files={"file": ("panel.cytoforge", exported.content)}
    )
    assert imported.status_code == 200, imported.text
    restored = Workspace.model_validate(imported.json())
    assert restored.samples[0].aliases == doc.samples[0].aliases
    np.testing.assert_allclose(
        Engine(client.app.state.store).column(restored, restored.samples[0], "CD3"), truth[:, 0] / 2
    )


def test_gatingml_export_resolves_aliases_to_detector_names_and_preserves_ratio_masks(client):
    doc, truth = cohort(client)
    doc, _ = harmonize(client, doc)
    sample = doc.samples[0]
    gate = Gate(
        sample_id=sample.id,
        name="Alias ratio",
        kind="hyperrectangle",
        x="CD3/CD4",
        dimensions=[
            GateDimension(
                channel="CD3/CD4", ratio_channels=("CD3", "CD4"), minimum=1.2, maximum=1.7
            )
        ],
    )
    doc.gates.append(gate)
    exported = export_gatingml(doc, sample)
    parsed = parse_document(exported, "panel.xml").sources[0]
    assert parsed.gates[0].dimensions[0].ratio_channels == ("FL1", "FL2")
    assert b'"CD3"' not in exported and b'"CD4"' not in exported
    engine = Engine(client.app.state.store)
    expected = (truth[:, 0] * 2 / truth[:, 1] >= 1.2) & (truth[:, 0] * 2 / truth[:, 1] < 1.7)
    np.testing.assert_array_equal(engine.mask(doc, sample, gate.id), expected)


@pytest.mark.parametrize("damage", ["chain", "missing_source", "collision", "display_mismatch"])
def test_semantically_invalid_fcs_alias_metadata_cannot_import_partial_events(
    client, tmp_path, damage
):
    doc, truth = cohort(client)
    doc, _ = harmonize(client, doc)
    store = client.app.state.store
    sample = doc.samples[0]
    path, _ = event_exports.write_export(
        store,
        Engine(store),
        doc,
        event_exports.Request(revision=doc.revision, sample_id=sample.id),
        tmp_path / "export",
    )
    external = flowio.FlowData(path)
    envelope, _ = event_exports.decode(external.text[event_exports.METADATA_KEY])
    if damage == "chain":
        envelope.aliases["CD3"] = "CD4"
    elif damage == "missing_source":
        envelope.aliases["CD3"] = "Missing"
    elif damage == "collision":
        envelope.aliases = {"FL1": "FL2"}
        envelope.alias_channels = [Channel(name="FL1")]
    else:
        envelope.alias_channels[0].name = "Different"
    prefix = event_exports.fcs_prefix(
        envelope.channels,
        len(truth),
        {
            event_exports.METADATA_KEY: event_exports.encode(envelope),
            "$SPILLOVER": external.text["spillover"],
        },
    )
    path.write_bytes(prefix + np.asarray(truth, dtype="<f8").tobytes())
    directory = tmp_path / "invalid-import"
    directory.mkdir()
    with pytest.raises(ValueError):
        stream_fcs(path, "Invalid aliases.fcs", directory)
    assert not list(directory.glob("*.npy"))
