"""Independent FCS readers, complete precision, lineage and bounded export lifetime."""

import hashlib
import io

import flowio
import numpy as np
import pytest
from cytoforge import event_exports
from cytoforge.imports import stream_fcs
from cytoforge.models import Channel, Compensation, Gate, Sample, Transform, Workspace
from cytoforge.science import Engine, save_events
from test_concatenation import apply, body, experiment, start, wait


def exported(client, doc, sample, directory, **changes):
    request = event_exports.Request(revision=doc.revision, sample_id=sample.id, **changes)
    return event_exports.write_export(
        client.app.state.store, Engine(client.app.state.store), doc, request, directory
    )


@pytest.mark.parametrize("values", ["raw", "compensated", "scale"])
def test_fcs_double_precision_is_independently_readable_and_reimport_restores_basis(
    client, tmp_path, values
):
    store = client.app.state.store
    raw = np.array(
        [
            [16_777_217.25, 0.123456789012345],
            [-0.0, -9.87654321098765],
            [2**40 + 0.5, 1234.567890123456],
            [np.nan, np.inf],
        ],
        dtype=float,
    )
    matrix = Compensation(name="Exact basis", detectors=["X", "Y"], matrix=[[2, 0], [0, 4]])
    sample = Sample(
        name="Unicode π | label",
        event_count=len(raw),
        channels=[
            Channel(
                name=n,
                label="| " + n + " | marker π",
                transform=Transform(kind="asinh", cofactor=3),
            )
            for n in "XY"
        ],
        tags={"donor": "Δ | 'B'"},
        compensation_id=matrix.id,
    )
    doc = Workspace(name="Portable precision", samples=[sample], compensations=[matrix])
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), raw)
    doc = store.create(doc)
    before = doc.model_dump_json()
    path, summary = exported(client, doc, sample, tmp_path / "export", values=values)
    external = flowio.FlowData(path)
    assert external.data_type == "D" and external.version == "3.1"
    assert external.text["p1b"] == "64" and external.text["p1e"] == "0,0"
    expected = raw.copy()
    if values != "raw":
        expected /= [2, 4]
        expected[~np.isfinite(expected).all(axis=1)] = np.nan
    if values == "scale":
        expected = np.arcsinh(expected / 3)
    np.testing.assert_array_equal(np.asarray(external.events).reshape(4, 2), expected)
    assert ("spillover" in external.text) == (values == "raw")
    assert summary["matrix_preserved"] == (values == "raw")
    imports = stream_fcs(path, "Reopened.fcs", tmp_path / "export")
    reopened = imports[0]
    assert reopened.sample.tags == sample.tags
    assert reopened.sample.event_export.values == values
    assert bool(reopened.compensation) == (values == "raw")
    if reopened.compensation:
        assert reopened.compensation.id != matrix.id
        assert reopened.compensation.matrix == matrix.matrix
    np.testing.assert_array_equal(np.load(reopened.path), expected)
    assert [c.transform.kind for c in reopened.sample.channels] == [
        "linear" if values == "scale" else "asinh"
    ] * 2
    assert store.get(doc.id).model_dump_json() == before


@pytest.mark.parametrize("values", ["raw", "compensated", "scale"])
@pytest.mark.parametrize("subset", [False, True])
def test_merged_fcs_restores_exact_origin_rows_after_reimport(client, tmp_path, values, subset):
    doc, _ = experiment(client)
    session = wait(client, doc, start(client, doc, body(doc)))
    assert apply(client, doc, session).status_code == 200
    doc = client.app.state.store.get(doc.id)
    merged = doc.samples[-1]
    gate = None
    if subset:
        gate = Gate(
            sample_id=merged.id,
            name="Only later retained events",
            kind="range",
            x="CF_EventID",
            bounds=[1, 4],
            x_transform=Transform(),
        )
        doc = client.app.state.store.mutate(
            doc.id, "Choose export scope", lambda d: d.gates.append(gate), doc.revision
        )
    path, _ = exported(
        client, doc, merged, tmp_path / "export", values=values, gate_id=gate.id if gate else None
    )
    imported = stream_fcs(path, "Merged restored.fcs", tmp_path / "export")[0]
    assert imported.origins_path.exists()
    origins = np.load(imported.origins_path)
    expected = np.array([[1, i] for i in range(5)] + [[2, i] for i in range(4)], dtype="uint64")
    if subset:
        expected = expected[(expected[:, 1] >= 1) & (expected[:, 1] < 4)]
    np.testing.assert_array_equal(origins, expected)
    assert [s.count for s in imported.sample.concatenation.sources] == (
        [3, 3] if subset else [5, 4]
    )
    assert imported.sample.concatenation.values == values
    assert imported.sample.concatenation.keywords == merged.concatenation.keywords
    assert imported.sample.event_export.source_snapshot["populations"] == (
        {gate.id: gate.model_dump()} if gate else {}
    )
    assert event_exports.METADATA_KEY not in imported.sample.metadata
    assert imported.sample.event_export.metadata_sha256


def test_origin_and_keyword_parameters_keep_integer_meaning_in_scale_export(client, tmp_path):
    doc, _ = experiment(client)
    session = wait(client, doc, start(client, doc))
    apply(client, doc, session)
    doc = client.app.state.store.get(doc.id)

    def change(workspace):
        for c in workspace.samples[-1].channels:
            if c.name.startswith("CF_"):
                c.transform = Transform(kind="asinh", cofactor=2)

    doc = client.app.state.store.mutate(doc.id, "Style categorical axes", change, doc.revision)
    path, _ = exported(client, doc, doc.samples[-1], tmp_path / "export", values="scale")
    imported = stream_fcs(path, "Scale identity.fcs", tmp_path / "export")[0]
    values = np.load(imported.path)
    np.testing.assert_array_equal(values[:, 2], [1] * 5 + [2] * 4)
    np.testing.assert_array_equal(values[:, 3], [0, 1, 2, 3, 4, 0, 1, 2, 3])
    np.testing.assert_array_equal(values[:, 4], [1] * 5 + [2] * 4)


@pytest.mark.parametrize("damage", ["data", "metadata", "spillover", "lineage"])
def test_corrupted_portable_fcs_rejects_the_entire_file_and_cleans_arrays(client, tmp_path, damage):
    doc, _ = experiment(client)
    session = wait(client, doc, start(client, doc))
    apply(client, doc, session)
    doc = client.app.state.store.get(doc.id)
    path, _ = exported(client, doc, doc.samples[-1], tmp_path / "export")
    content = path.read_bytes()
    if damage == "data":
        content = content[:-1] + bytes([content[-1] ^ 1])
    elif damage == "metadata":
        marker = content.index(b"CFEX1:") + 8
        content = content[:marker] + bytes([content[marker] ^ 1]) + content[marker + 1 :]
    elif damage == "spillover":
        content = content.replace(b"$SPILLOVER|2,X,Y,2,0,0,4|", b"$SPILLOVER|2,X,Y,3,0,0,4|")
    else:
        source = flowio.FlowData(path)
        envelope, _ = event_exports.decode(source.text[event_exports.METADATA_KEY])
        envelope.lineage.origins_sha256 = "0" * 64
        new = event_exports.encode(envelope)
        prefix = event_exports.fcs_prefix(
            envelope.channels,
            source.event_count,
            {event_exports.METADATA_KEY: new, "$SPILLOVER": source.text["spillover"]},
        )
        data_start = int(source.text["begindata"])
        content = prefix + content[data_start:]
    path.write_bytes(content)
    imported = tmp_path / "imported"
    imported.mkdir()
    with pytest.raises(ValueError):
        stream_fcs(path, "Bad merged.fcs", imported)
    assert not list(imported.glob("*.npy"))


def test_large_data_uses_text_offsets_and_zero_event_fcs_has_an_empty_data_segment():
    channels = [Channel(name="X")]
    prefix = event_exports.fcs_prefix(channels, 15_000_000, {})
    assert prefix[26:42].strip() == b"0       0"
    from cytoforge.imports import read_fcs_header

    # Header validation can check declared offsets without allocating a 120 MB dataset.
    header = read_fcs_header(io.BytesIO(prefix), 0, len(prefix) + 15_000_000 * 8)
    assert header.data_stop > 99_999_999 and header.event_count == 15_000_000
    empty = event_exports.fcs_prefix(channels, 0, {})
    header = read_fcs_header(io.BytesIO(empty), 0, len(empty))
    assert header.event_count == 0 and header.data_stop == header.data_start - 1


def test_raw_spectral_export_restores_weights_background_and_virtual_display(client, tmp_path):
    store = client.app.state.store
    truth = np.array([[1, 3], [5, 2], [-1, 4]], dtype=float)
    spectra = np.array([[1, 0.2, 0.4], [0.1, 1, 0.5]])
    matrix = Compensation(
        name="Spectral snapshot",
        detectors=["D1", "D2", "D3"],
        outputs=["A", "B"],
        kind="spectral",
        matrix=spectra.tolist(),
        background=[3, 5, 9],
        weights=[1, 2, 3],
    )
    sample = Sample(
        name="Spectral export",
        event_count=3,
        channels=[Channel(name=n) for n in ["D1", "D2", "D3", "A", "B"]],
        unmixed_parameters=["A", "B"],
        compensation_id=matrix.id,
    )
    sample.channels[-1].transform = Transform(kind="asinh", cofactor=17)
    doc = Workspace(name="Spectral precision", samples=[sample], compensations=[matrix])
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), truth @ spectra + [3, 5, 9])
    doc = store.create(doc)
    path, summary = exported(client, doc, sample, tmp_path / "export")
    imported = stream_fcs(path, "Spectral restored.fcs", tmp_path / "export")[0]
    assert summary["channel_count"] == 3
    assert imported.sample.channels[-1].transform.cofactor == 17
    assert imported.compensation.background == matrix.background
    assert imported.compensation.weights == matrix.weights
    restored = Workspace(
        name="Restored basis", samples=[imported.sample], compensations=[imported.compensation]
    )
    imported.path.replace(store.data_path(restored.id, imported.sample.id))
    restored = store.create(restored)
    engine = Engine(store)
    np.testing.assert_allclose(
        engine.column(restored, restored.samples[0], "A"), truth[:, 0], atol=1e-12
    )
    np.testing.assert_allclose(
        engine.column(restored, restored.samples[0], "B"), truth[:, 1], atol=1e-12
    )


def test_compressed_metadata_has_integrity_and_a_bounded_decompression_limit(
    client, tmp_path, monkeypatch
):
    doc, _ = experiment(client)
    path, _ = exported(client, doc, doc.samples[0], tmp_path / "export")
    external = flowio.FlowData(path)
    value = external.text[event_exports.METADATA_KEY]
    envelope, digest = event_exports.decode(value)
    assert hashlib.sha256(envelope.model_dump_json().encode()).hexdigest() == digest
    monkeypatch.setattr(event_exports, "MAX_DOCUMENT", 40)
    with pytest.raises(ValueError, match="size limit"):
        event_exports.decode(value)


@pytest.mark.parametrize("reassign", [False, True])
def test_reexport_retains_prior_materialization_and_an_explicit_new_correction(
    client, tmp_path, reassign
):
    doc, _ = experiment(client)
    path, _ = exported(client, doc, doc.samples[0], tmp_path / "first", values="compensated")
    response = client.post(
        f"/api/workspaces/{doc.id}/import?revision={doc.revision}",
        files=[("files", ("Already corrected.fcs", path.read_bytes()))],
    )
    assert response.status_code == 200 and response.json()["imported"] == 1
    store = client.app.state.store
    doc = store.get(doc.id)
    sample = doc.samples[-1]
    first = np.load(store.data_path(doc.id, sample.id))
    if reassign:
        matrix = Compensation(
            name="Explicit next basis", detectors=["X", "Y"], matrix=[[3, 0], [0, 2]]
        )

        def assign(d):
            d.compensations.append(matrix)
            d.samples[-1].compensation_id = matrix.id

        doc = store.mutate(doc.id, "Assign another correction explicitly", assign, doc.revision)
        sample = doc.samples[-1]
    path, _ = exported(client, doc, sample, tmp_path / "second")
    result = stream_fcs(path, "Second export.fcs", tmp_path / "second")[0]
    np.testing.assert_array_equal(np.load(result.path), first)
    assert result.sample.event_export.values == ("raw" if reassign else "compensated")
    assert result.sample.event_export.source_snapshot["event_export"]["values"] == "compensated"
    assert bool(result.compensation) == reassign


def test_empty_population_is_readable_and_reimports_without_inventing_events(client, tmp_path):
    doc, _ = experiment(client)
    sample = doc.samples[0]
    gate = Gate(sample_id=sample.id, name="Empty", kind="range", x="X", bounds=[1000, 2000])
    store = client.app.state.store
    doc = store.mutate(doc.id, "Empty population", lambda d: d.gates.append(gate), doc.revision)
    path, summary = exported(client, doc, sample, tmp_path / "empty", gate_id=gate.id)
    assert summary["event_count"] == 0
    assert len(flowio.FlowData(path).events) == 0
    reopened = stream_fcs(path, "Empty.fcs", tmp_path / "empty")[0]
    assert reopened.sample.event_count == 0
    assert np.load(reopened.path).shape == (0, 2)
