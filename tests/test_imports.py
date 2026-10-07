from __future__ import annotations

import hashlib

# This encoder writes bytes independently; it does not use FlowIO or the decoder.
import sys
import threading
from pathlib import Path

import flowio
import numpy as np
import pytest
from cytoforge import imports as decoding
from cytoforge.import_sessions import ImportSessions
from cytoforge.imports import ImportCancelled, stream_csv, stream_fcs, text_pairs
from cytoforge.models import Workspace
from cytoforge.science import Engine, save_events
from cytoforge.store import ConflictError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from import_fixture import desktop_fixture, fcs_dataset  # noqa: E402


def stream(tmp_path, content, **kwargs):
    path = tmp_path / "input.fcs"
    path.write_bytes(content)
    directory = tmp_path / "prepared"
    directory.mkdir(exist_ok=True)
    return stream_fcs(path, "Acquisition.fcs", directory, **kwargs)


@pytest.mark.parametrize("data_type", ["F", "D"])
@pytest.mark.parametrize("order", ["<", ">"])
@pytest.mark.parametrize("version", ["2.0", "3.0", "3.1"])
def test_float_formats_endianness_versions_and_precision(tmp_path, data_type, order, version):
    truth = np.array([[-1.25, 0], [1e5, 1e-30], [np.nan, np.inf]], float)
    result = stream(
        tmp_path, fcs_dataset(truth, ["X", "Y"], data_type=data_type, order=order, version=version)
    )[0]
    actual = np.load(result.path, mmap_mode="r")
    expected = truth.astype("f4" if data_type == "F" else "f8").astype(float)
    np.testing.assert_array_equal(actual, expected)
    assert result.sample.sha256 == hashlib.file_digest(result.path.open("rb"), "sha256").hexdigest()
    assert result.sample.metadata["cytoforge_dataset_count"] == "1"
    assert result.warnings == [
        "2 nonfinite values retained; plots and statistics omit these values"
    ]
    assert isinstance(actual, np.memmap)


def test_chained_datasets_different_panels_gain_log_time_masks_and_literal_metadata(tmp_path):
    content = desktop_fixture()
    results = stream(tmp_path, content)
    assert [r.sample.event_count for r in results] == [2, 3, 3]
    assert [len(r.sample.channels) for r in results] == [3, 3, 2]
    assert [r.sample.name for r in results] == [
        f"Acquisition.fcs · Dataset {index}" for index in [1, 2, 3]
    ]
    assert [r.count for r in results] == [3, 3, 3]
    assert results[2].offset > results[1].offset > 0
    np.testing.assert_array_equal(np.load(results[0].path), [[12, 22, 1], [34, 46, 2]])
    expected = np.array([[127, 50, 5], [8, 10 ** (4 * 1023 / 1024) / 2, 10], [42, 50, 15]])
    np.testing.assert_allclose(np.load(results[1].path), expected, rtol=1e-15)
    assert results[0].sample.metadata["com"] == "Budget $100 / sample/"
    assert results[0].sample.channels[0].range == 500
    assert results[0].sample.channels[2].range == 20
    assert results[1].sample.channels[1].range == 5000
    assert results[0].compensation.matrix == [[1, 0.2], [0.1, 1]]
    assert "logarithmic zero corrected" in results[1].warnings[0]
    assert "2 nonfinite values retained" in results[2].warnings[0]
    # FlowIO is an additional, independently maintained oracle for supported formats.
    reference = flowio.read_multiple_data_sets(str(tmp_path / "input.fcs"))
    for actual, oracle in zip(results, reference, strict=True):
        np.testing.assert_array_equal(np.load(actual.path), oracle.as_array())


@pytest.mark.parametrize("order", ["<", ">"])
@pytest.mark.parametrize("widths", [[8, 16, 32], [16, 16, 16], [24, 48, 64]])
def test_unsigned_integer_widths_masks_and_non_power_of_two_ranges(tmp_path, order, widths):
    values = [[129, 65000, 12345], [255, 1024, 17]]
    result = stream(
        tmp_path,
        fcs_dataset(
            values,
            ["A", "B", "C"],
            data_type="I",
            widths=widths,
            order=order,
            metadata={"P1R": "100", "P2R": "1000", "P3R": "8192"},
        ),
    )[0]
    np.testing.assert_array_equal(np.load(result.path), [[1, 488, 4153], [127, 0, 17]])


@pytest.mark.parametrize("widths", [[4, 6], ["*", "*"]])
def test_legacy_ascii_fixed_and_free_format(tmp_path, widths):
    result = stream(
        tmp_path, fcs_dataset([[0, 13], [99, -14]], ["A", "B"], data_type="A", widths=widths)
    )[0]
    np.testing.assert_array_equal(np.load(result.path), [[0, 13], [99, -14]])


@pytest.mark.parametrize(
    "payload",
    [
        b"1,2,3",
        b"1,2,3,4,5",
        b"1,wrong,3,4",
        b"1,2,3_0,4",
        b"1,2," + b"0" * 65 + b",4",
    ],
)
def test_ascii_count_and_tokens_reject_corruption_atomically(tmp_path, payload):
    with pytest.raises(ValueError, match="ASCII DATA"):
        stream(
            tmp_path,
            fcs_dataset(
                [[1, 2], [3, 4]], ["A", "B"], data_type="A", widths=["*", "*"], raw_data=payload
            ),
        )
    assert not list((tmp_path / "prepared").iterdir())


@pytest.mark.parametrize("delimiter", ["/", "|", ".", "*", "\\", "^", "-", "]"])
def test_metadata_escapes_utf8_dollars_and_trailing_delimiter(tmp_path, delimiter):
    result = stream(
        tmp_path,
        fcs_dataset(
            [[1]],
            ["Δ Marker"],
            delimiter=delimiter,
            metadata={"COM": f"Cost $20 {delimiter} unicode Δ {delimiter}"},
        ),
    )[0]
    assert result.sample.metadata["com"] == f"Cost $20 {delimiter} unicode Δ {delimiter}"
    assert result.sample.channels[0].name == "Δ Marker"
    with pytest.raises(ValueError, match="conflicting"):
        text_pairs(b"/$TOT/1/$TOT/2/")


@pytest.mark.parametrize(
    "mutation, message",
    [
        ({"NEXTDATA": "-2"}, "NEXTDATA"),
        ({"NEXTDATA": "58"}, "NEXTDATA"),
        ({"NEXTDATA": "999999999"}, "NEXTDATA"),
        ({"TOT": "3"}, "DATA length"),
        ({"BYTEORD": "2,1,4,3"}, "byte order"),
        ({"P1B": "16"}, "parameter widths"),
        ({"P1R": "nan"}, "range/gain"),
        ({"P1E": "nan,0"}, "finite nonnegative"),
        ({"MODE": "C"}, "histogram"),
    ],
)
def test_corrupt_metadata_strict_offsets_and_no_silent_byte_order(tmp_path, mutation, message):
    with pytest.raises(ValueError, match=message):
        stream(tmp_path, fcs_dataset([[1], [2]], ["X"], metadata=mutation))
    assert not list((tmp_path / "prepared").iterdir())


def test_bad_later_dataset_removes_earlier_staged_arrays(tmp_path):
    with pytest.raises(ValueError, match="outside"):
        stream(tmp_path, desktop_fixture()[:-8])
    assert not list((tmp_path / "prepared").iterdir())


def test_cancellation_after_first_dataset_and_between_chunks_cleans_all(tmp_path, monkeypatch):
    monkeypatch.setattr(decoding, "CHUNK_VALUES", 3)
    stop = threading.Event()
    seen = []

    def progress(**value):
        if value.get("stage") == "Reading events":
            seen.append(value["events_read"])
            stop.set()

    def check():
        if stop.is_set():
            raise ImportCancelled()

    with pytest.raises(ImportCancelled):
        stream(tmp_path, desktop_fixture(), progress=progress, check=check)
    assert seen == [1]
    assert not list((tmp_path / "prepared").iterdir())


def test_rejects_unrepresentable_integer_without_rounding(tmp_path):
    with pytest.raises(ValueError, match="exact float64"):
        stream(tmp_path, fcs_dataset([[2**53 + 1]], ["X"], data_type="I", widths=[64]))
    assert not list((tmp_path / "prepared").iterdir())


def test_csv_two_pass_chunks_quotes_bom_blanks_empty_and_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(decoding, "CHUNK_VALUES", 4)
    path = tmp_path / "quoted.csv"
    path.write_text('"X, area",Y\n1,2\n\n3,nan\n4,inf\n-1,5\n', encoding="utf-8-sig")
    records = []
    result = stream_csv(path, "CSV", tmp_path, lambda **value: records.append(value))[0]
    np.testing.assert_array_equal(np.load(result.path), [[1, 2], [3, np.nan], [4, np.inf], [-1, 5]])
    assert [v["events_read"] for v in records if v["stage"] == "Reading events"] == [0, 2, 4]
    assert result.warnings[0].startswith("2 nonfinite")
    path.write_text("X,Y\n", encoding="utf-8")
    empty = stream_csv(path, "Empty", tmp_path)[0]
    assert np.load(empty.path).shape == (0, 2)
    existing = {p.name for p in tmp_path.glob("*.npy")}
    path.write_text("X,Y\n1,2\n3,4\n5,wrong\n", encoding="utf-8")
    with pytest.raises(ValueError, match="CSV row 4"):
        stream_csv(path, "Invalid", tmp_path)
    assert {p.name for p in tmp_path.glob("*.npy")} == existing
    assert not list(tmp_path.glob("*.pending"))
    path.write_text("X,X\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        stream_csv(path, "Invalid header", tmp_path)


def create(client):
    return client.post("/api/workspaces", json={"name": "Native import truth"}).json()


def upload(client, doc, files, **query):
    params = {"revision": doc["revision"], **query}
    response = client.post(
        f"/api/workspaces/{doc['id']}/import",
        params=params,
        files=[("files", (name, content, "application/octet-stream")) for name, content in files],
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_api_chains_duplicates_deleted_sibling_restore_allow_duplicates_and_export(client):
    doc = create(client)
    content = desktop_fixture()
    result = upload(client, doc, [("chain.fcs", content), ("renamed.fcs", content)])
    assert result["imported"] == 3 and len(result["warnings"]) == 5
    assert len(result["datasets"]) == 6 and sum(d["skipped"] for d in result["datasets"]) == 3
    doc = result["workspace"]
    store = client.app.state.store
    engine = Engine(store)
    workspace = store.get(doc["id"])
    np.testing.assert_allclose(
        np.column_stack(
            [engine.column(workspace, workspace.samples[0], name) for name in ["X", "Y", "Time"]]
        ),
        [[10, 20, 1], [30, 40, 2]],
        atol=1e-12,
    )
    second = doc["samples"][1]["id"]
    removed = client.delete(
        f"/api/workspaces/{doc['id']}/samples/{second}", params={"revision": doc["revision"]}
    )
    assert removed.status_code == 200, removed.text
    result = upload(client, removed.json(), [("renamed.fcs", content)])
    assert result["imported"] == 1 and len(result["workspace"]["samples"]) == 3
    assert [d["skipped"] for d in result["datasets"]] == [True, False, True]
    restored_id = result["workspace"]["samples"][-1]["id"]
    assert restored_id != second
    np.testing.assert_array_equal(
        np.load(store.data_path(doc["id"], second)),
        np.load(store.data_path(doc["id"], restored_id)),
    )
    again = upload(client, result["workspace"], [("chain.fcs", content)], allow_duplicates=True)
    assert again["imported"] == 3 and len(again["workspace"]["samples"]) == 6
    # A portable archive retains dataset identity and the actual immutable arrays.
    archive = client.get(f"/api/workspaces/{doc['id']}/export/project")
    assert archive.status_code == 200, archive.text[:200]
    restored = client.post(
        "/api/import/project", files={"file": ("truth.cytoforge", archive.content)}
    )
    assert restored.status_code == 200, restored.text
    duplicate = upload(client, restored.json(), [("again.fcs", content)])
    assert duplicate["imported"] == 0
    assert not list((store.root / "tmp").iterdir())
    assert all(
        list(directory.iterdir()) == [directory / "state.json"]
        for directory in (store.root / "imports").iterdir()
    )


def test_api_malformed_chain_atomic_per_file_and_good_files_retained(client):
    result = upload(
        client,
        create(client),
        [
            ("bad-chain.fcs", desktop_fixture()[:-8]),
            ("good.csv", b"X,Y\n1,2\n"),
        ],
    )
    assert result["imported"] == 1 and len(result["errors"]) == 1
    assert len(result["workspace"]["samples"]) == 1
    assert len(result["datasets"]) == 1 and result["datasets"][0]["file"] == "good.csv"
    files = list((client.app.state.store.events_dir / result["workspace"]["id"]).glob("*.npy"))
    assert len(files) == 1
    session = client.get(
        f"/api/workspaces/{result['workspace']['id']}/imports/{result['import_id']}"
    ).json()
    assert session["status"] == "succeeded" and session["imported"] == 1
    assert "outputs" not in session


def test_api_progress_cancel_mid_batch_leaves_revision_history_and_disk_unchanged(
    client, monkeypatch
):
    doc = create(client)
    session = client.post(f"/api/workspaces/{doc['id']}/imports", json={"revision": 0}).json()
    manager = client.app.state.import_sessions
    entered, release = threading.Event(), threading.Event()
    original = decoding.write_blocks

    def intercepted(path, shape, blocks, progress, check):
        entered.set()
        assert release.wait(10)
        return original(path, shape, blocks, progress, check)

    monkeypatch.setattr(decoding, "write_blocks", intercepted)
    outcome = []
    worker = threading.Thread(
        target=lambda: outcome.append(
            upload(client, doc, [("chain.fcs", desktop_fixture())], import_id=session["id"])
        )
    )
    worker.start()
    try:
        assert entered.wait(10)
        progress = client.get(f"/api/workspaces/{doc['id']}/imports/{session['id']}").json()
        assert progress["status"] == "reading" and progress["dataset_index"] == 1
        cancelled = client.post(
            f"/api/workspaces/{doc['id']}/imports/{session['id']}/cancel"
        ).json()
        assert cancelled["cancel_requested"] is True
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive()
    assert outcome[0]["cancelled"] is True and outcome[0]["imported"] == 0
    assert outcome[0]["workspace"]["revision"] == 0
    assert manager.get(doc["id"], session["id"])["status"] == "cancelled"
    assert not list(client.app.state.store.events_dir.rglob("*.npy"))
    assert list((manager.root / session["id"]).iterdir()) == [
        manager.root / session["id"] / "state.json"
    ]


def test_recovery_preserves_committed_undo_history_and_removes_uncommitted_moves(store, tmp_path):
    manager = ImportSessions(store)
    doc = store.create(Workspace(name="Recovery"))
    result = stream(tmp_path, fcs_dataset([[1]], ["X"]))[0]
    identifier = manager.create(doc.id, doc.revision)["id"]
    manager.begin(doc.id, identifier, doc.revision, 1, 0)
    manager.committing(doc.id, identifier, [result.sample.id])
    result.path.replace(store.data_path(doc.id, result.sample.id))
    committed = store.mutate(
        doc.id, "Import", lambda w: w.samples.append(result.sample), doc.revision
    )
    undone = store.move_history(doc.id, -1, committed.revision)
    assert not undone.samples
    # Simulate a stop after SQLite commit, before the import's success record.
    recovered = ImportSessions(store)
    assert recovered.get(doc.id, identifier)["status"] == "succeeded"
    assert store.data_path(doc.id, result.sample.id).is_file()
    redone = store.move_history(doc.id, 1, undone.revision)
    np.testing.assert_array_equal(Engine(store).raw(redone, redone.samples[0]), [[1]])
    # Simulate a different stop after moving an array, before SQLite commit.
    failed = recovered.create(doc.id, redone.revision)["id"]
    orphan = result.sample.model_copy(update={"id": "a" * 32})
    recovered.begin(doc.id, failed, redone.revision, 1, 0)
    recovered.committing(doc.id, failed, [orphan.id])
    save_events(store.data_path(doc.id, orphan.id), np.array([[99]], float))
    (recovered.root / failed / "abandoned.upload").write_bytes(b"original")
    reopened = ImportSessions(store)
    assert reopened.get(doc.id, failed)["status"] == "interrupted"
    assert not store.data_path(doc.id, orphan.id).exists()
    assert not (reopened.root / failed / "abandoned.upload").exists()
    assert store.get(doc.id).samples[0].id == result.sample.id


def test_session_binding_reuse_and_cancel_commit_race(client):
    doc, other = create(client), create(client)
    identifier = client.post(f"/api/workspaces/{doc['id']}/imports", json={"revision": 0}).json()[
        "id"
    ]
    manager = client.app.state.import_sessions
    assert client.get(f"/api/workspaces/{other['id']}/imports/{identifier}").status_code == 404
    manager.begin(doc["id"], identifier, 0, 1, 0)
    with pytest.raises(ConflictError, match="already been used"):
        manager.begin(doc["id"], identifier, 0, 1, 0)
    manager.committing(doc["id"], identifier, [])
    assert (
        client.post(f"/api/workspaces/{doc['id']}/imports/{identifier}/cancel").status_code == 409
    )
    manager.finish(doc["id"], identifier, "succeeded", stage="Finished")
    assert (
        client.post(f"/api/workspaces/{doc['id']}/imports/{identifier}/cancel").json()["status"]
        == "succeeded"
    )


def test_reference_acquisitions_match_exactly_after_streaming_import(tmp_path):
    for path in sorted(Path("tests/fixtures/interchange").rglob("*.fcs")):
        oracle = flowio.FlowData(str(path))
        result = stream_fcs(path, path.name, tmp_path)[0]
        np.testing.assert_array_equal(np.load(result.path), oracle.as_array())
        assert result.sample.event_count == oracle.event_count
        assert [c.name for c in result.sample.channels] == oracle.pnn_labels
        result.path.unlink()


def test_supplemental_analysis_segments_and_conflicting_metadata(tmp_path):
    result = stream(
        tmp_path,
        fcs_dataset(
            [[1, 2]],
            ["X", "Y"],
            supplemental={"DONOR": "D1", "COM": "Units $ / marker/"},
            analysis={"RESULT": "Original instrument summary"},
        ),
    )[0]
    assert result.sample.metadata["donor"] == "D1"
    assert result.sample.metadata["com"] == "Units $ / marker/"
    assert result.sample.metadata["cytoforge_analysis_result"] == "Original instrument summary"
    np.testing.assert_array_equal(np.load(result.path), [[1, 2]])
    result.path.unlink()
    with pytest.raises(ValueError, match="Conflicting supplemental"):
        stream(tmp_path, fcs_dataset([[1]], ["X"], supplemental={"TOT": "2"}))
    assert not list((tmp_path / "prepared").iterdir())


def test_nonuniform_final_chunk_retains_every_event_in_order(tmp_path, monkeypatch):
    monkeypatch.setattr(decoding, "CHUNK_VALUES", 17)
    truth = np.arange(771).reshape(257, 3).astype(float)
    result = stream(tmp_path, fcs_dataset(truth, ["X", "Y", "Time"]))[0]
    np.testing.assert_array_equal(np.load(result.path), truth)


@pytest.mark.parametrize("kind,width", [("F", 32), ("D", 64), ("A", "*")])
def test_zero_event_datasets_with_absent_data_offsets(tmp_path, kind, width):
    content = fcs_dataset(np.empty((0, 1)), ["X"], data_type=kind, widths=[width], raw_data=b"")
    original_start, original_stop = int(content[26:34]), int(content[34:42])
    data = bytearray(content)
    data[26:42] = b"       0       0"
    data = (
        bytes(data)
        .replace(f"$BEGINDATA/{original_start:020d}/".encode(), b"$BEGINDATA/00000000000000000000/")
        .replace(f"$ENDDATA/{original_stop:020d}/".encode(), b"$ENDDATA/00000000000000000000/")
    )
    result = stream(tmp_path, data)[0]
    assert result.sample.event_count == 0
    assert np.load(result.path).shape == (0, 1)


def test_storage_failure_before_or_during_write_cleans_every_partial_file(tmp_path, monkeypatch):
    content = desktop_fixture()
    usage = type("Usage", (), {"free": 4096})()
    monkeypatch.setattr(decoding.shutil, "disk_usage", lambda _: usage)
    with pytest.raises(ValueError, match="Insufficient storage"):
        stream(tmp_path, content)
    assert not list((tmp_path / "prepared").iterdir())


def test_revision_conflict_after_preparation_removes_moves_and_preserves_other_edit(
    client, monkeypatch
):
    doc = create(client)
    original = decoding.stream_csv
    from cytoforge import app as app_module

    def changing_workspace(*args, **kwargs):
        result = original(*args, **kwargs)
        client.app.state.store.mutate(
            doc["id"], "Concurrent edit", lambda w: setattr(w, "description", "Keep this edit"), 0
        )
        return result

    monkeypatch.setattr(app_module, "stream_csv", changing_workspace)
    response = client.post(
        f"/api/workspaces/{doc['id']}/import?revision=0",
        files=[("files", ("new.csv", b"X,Y\n1,2\n"))],
    )
    assert response.status_code == 409, response.text
    store = client.app.state.store
    current = store.get(doc["id"])
    assert current.description == "Keep this edit" and current.revision == 1 and not current.samples
    assert not list(store.events_dir.rglob("*.npy"))
    record = next(iter(client.app.state.import_sessions.records.values()))
    assert record["status"] == "failed"
    assert list((store.root / "imports" / record["id"]).iterdir()) == [
        store.root / "imports" / record["id"] / "state.json"
    ]


def test_legacy_duplicate_metadata_and_pre_transfer_cancellation(client):
    doc = create(client)
    content = b"X,Y\n1,2\n"
    first = upload(client, doc, [("data.csv", content)])
    store = client.app.state.store

    def legacy(workspace):
        for key in [
            "cytoforge_dataset_index",
            "cytoforge_dataset_offset",
            "cytoforge_dataset_count",
        ]:
            workspace.samples[0].metadata.pop(key)

    current = store.mutate(doc["id"], "Legacy import", legacy, first["workspace"]["revision"])
    result = upload(client, current.model_dump(), [("renamed.csv", content)])
    assert result["imported"] == 0 and result["datasets"][0]["skipped"]
    session = client.post(
        f"/api/workspaces/{doc['id']}/imports", json={"revision": current.revision}
    ).json()
    client.post(f"/api/workspaces/{doc['id']}/imports/{session['id']}/cancel")
    cancelled = upload(
        client, current.model_dump(), [("other.csv", b"X,Y\n3,4\n")], import_id=session["id"]
    )
    assert cancelled["cancelled"] and cancelled["workspace"]["revision"] == current.revision
    assert len(cancelled["workspace"]["samples"]) == 1
