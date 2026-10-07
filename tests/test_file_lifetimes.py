"""File cleanup must work even when an exception retains the failing stack."""

import errno
import io
import json
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
from cytoforge import fileio, quality
from cytoforge.analysis import atomic_json
from cytoforge.imports import stream_csv, write_blocks
from cytoforge.jobs import JobManager
from cytoforge.models import Gate
from cytoforge.science import Engine, save_array
from test_quality import known_acquisition, persist


@pytest.mark.parametrize("reject", [False, True])
def test_scoped_mapping_closes_with_retained_views_and_traceback(tmp_path, reject):
    path = tmp_path / "events.npy"
    np.save(path, np.arange(12).reshape(6, 2))
    retained = []

    def consume():
        with fileio.mapped_array(path) as values:
            retained.extend([values, values[:, 0]])
            np.testing.assert_array_equal(values[:, 0], np.arange(0, 12, 2))
            if reject:
                raise ValueError("rejected original events")

    if reject:
        with pytest.raises(ValueError, match="rejected") as error:
            consume()
        assert error.value.__traceback__ is not None
    else:
        consume()
    assert retained[0]._mmap.closed
    path.unlink()


def test_validation_failure_closes_mapping_before_returning_the_error(tmp_path):
    path = tmp_path / "probabilities.npy"
    np.save(path, np.array([[0.2, 0.2, 2.0]], dtype=np.float32))
    retained = []

    def validate(values):
        retained.append(values)
        raise ValueError("probabilities do not sum to one")

    with pytest.raises(ValueError, match="sum") as error:
        fileio.load_validated_array(path, validate)
    assert error.value.__traceback__ is not None
    assert retained[0]._mmap.closed
    replacement = tmp_path / "replacement.npy"
    np.save(replacement, np.zeros((1, 3), dtype=np.float32))
    replacement.replace(path)


def test_validated_mapping_remains_available_until_its_consumer_finishes(tmp_path):
    path = tmp_path / "events.npy"
    np.save(path, np.arange(8))
    values = fileio.load_validated_array(
        path, lambda array: np.testing.assert_array_equal(array, np.arange(8))
    )
    try:
        assert not values._mmap.closed
        assert not values.flags.writeable
        np.testing.assert_array_equal(values, np.arange(8))
    finally:
        fileio.close_array(values)
    assert values._mmap.closed


def test_windows_lock_guard_detects_a_mapping_retained_by_a_view(tmp_path, windows_mapping_locks):
    path, replacement = tmp_path / "flags.npy", tmp_path / "replacement.npy"
    np.save(path, np.arange(8).reshape(4, 2))
    np.save(replacement, np.zeros((4, 2)))
    values = np.load(path, mmap_mode="r", allow_pickle=False)
    view = values[:, 0]
    del values
    try:
        with pytest.raises(PermissionError, match="open mapping") as error:
            replacement.replace(path)
        assert error.value.winerror == 5
        assert replacement.exists()
    finally:
        fileio.close_array(view)
    replacement.replace(path)
    np.testing.assert_array_equal(np.load(path, allow_pickle=False), np.zeros((4, 2)))


def test_windows_lock_guard_does_not_retain_finished_mappings(tmp_path, windows_mapping_locks):
    path, replacement = tmp_path / "flags.npy", tmp_path / "replacement.npy"
    np.save(path, np.arange(8).reshape(4, 2))
    np.save(replacement, np.zeros((4, 2)))

    def copy_column():
        values = np.load(path, mmap_mode="r", allow_pickle=False)
        return np.array(values[:, 0], copy=True)

    column = copy_column()
    replacement.replace(path)
    np.testing.assert_array_equal(column, np.arange(0, 8, 2))


def test_disguised_npz_is_rejected_and_closed(tmp_path, monkeypatch):
    path = tmp_path / "events.npy"
    with path.open("wb") as handle:
        np.savez(handle, values=np.arange(4))
    original_load, loaded = np.load, []

    def load(*args, **kwargs):
        value = original_load(*args, **kwargs)
        loaded.append(value)
        return value

    monkeypatch.setattr(np, "load", load)
    with pytest.raises(ValueError, match="NumPy array"):
        with fileio.mapped_array(path):
            pytest.fail("An archive must not be used as an event array")
    assert loaded[0].zip is None
    path.unlink()


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_atomic_json_retries_a_brief_windows_sharing_violation(tmp_path, monkeypatch, winerror):
    path = tmp_path / "progress.json"
    path.write_text('{"progress":0}')
    replace, calls, sleeps = fileio.os.replace, [], []

    def temporarily_busy(source, target):
        calls.append((source, target))
        if len(calls) <= 2:
            error = PermissionError("Reader still has progress.json open")
            error.winerror = winerror
            raise error
        replace(source, target)

    monkeypatch.setattr(fileio.os, "replace", temporarily_busy)
    monkeypatch.setattr(fileio.time, "sleep", sleeps.append)
    atomic_json(path, {"progress": 1})
    assert json.loads(path.read_text()) == {"progress": 1}
    assert len(calls) == 3 and sleeps
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("windows_lock", [False, True])
def test_failed_json_replacement_preserves_previous_file_and_cleans_temporary(
    tmp_path, monkeypatch, windows_lock
):
    path = tmp_path / "progress.json"
    original = b'{"progress":0}'
    path.write_bytes(original)
    clock, calls = [0.0], []

    def fail(source, target):
        calls.append((source, target))
        error = PermissionError("Replacement is denied")
        if windows_lock:
            error.winerror = 32
        raise error

    def advance(delay):
        clock[0] += delay

    monkeypatch.setattr(fileio.os, "replace", fail)
    monkeypatch.setattr(fileio.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(fileio.time, "sleep", advance)
    with pytest.raises(PermissionError, match="denied"):
        atomic_json(path, {"progress": 1})
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]
    assert clock[0] <= 1.0
    assert len(calls) > 1 if windows_lock else len(calls) == 1


@pytest.mark.parametrize("winerror", [None, 5, 32, 33])
def test_json_reader_retries_a_brief_sharing_violation(tmp_path, monkeypatch, winerror):
    path = tmp_path / "progress.json"
    atomic_json(path, {"stage": "Reading events", "progress": 0.25})
    original_read, calls, sleeps = Path.read_text, [], []

    def temporarily_busy(self, *args, **kwargs):
        calls.append(self)
        if len(calls) <= 2:
            if winerror is None:
                error = PermissionError(errno.EACCES, "Progress file is being replaced", str(self))
            else:
                error = PermissionError("Progress file is being replaced")
                error.winerror = winerror
            raise error
        return original_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", temporarily_busy)
    monkeypatch.setattr(fileio.time, "sleep", sleeps.append)
    assert fileio.read_json(path) == {"stage": "Reading events", "progress": 0.25}
    assert calls == [path] * 3 and sleeps == [0.005, 0.01]


def test_json_reader_propagates_persistent_denial_after_a_bounded_retry(tmp_path, monkeypatch):
    path = tmp_path / "progress.json"
    path.write_text('{"progress":0}')
    error = PermissionError(errno.EACCES, "Read is denied", str(path))
    clock, calls = [0.0], []

    def deny_read(self, *args, **kwargs):
        calls.append(self)
        raise error

    def advance(delay):
        clock[0] += delay

    monkeypatch.setattr(Path, "read_text", deny_read)
    monkeypatch.setattr(fileio.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(fileio.time, "sleep", advance)
    with pytest.raises(PermissionError) as caught:
        fileio.read_json(path)
    assert caught.value is error
    assert len(calls) > 1 and clock[0] <= 1.0
    assert path.read_bytes() == b'{"progress":0}'


@pytest.mark.parametrize("invalid", [False, True])
def test_json_reader_does_not_retry_missing_or_invalid_documents(tmp_path, monkeypatch, invalid):
    path = tmp_path / "progress.json"
    if invalid:
        path.write_text('{"progress":')

    def unexpected_retry(delay):
        pytest.fail("Missing or malformed JSON must fail immediately")

    monkeypatch.setattr(fileio.time, "sleep", unexpected_retry)
    with pytest.raises(json.JSONDecodeError if invalid else FileNotFoundError):
        fileio.read_json(path)


def test_json_reader_does_not_retry_unrelated_permission_errors(tmp_path, monkeypatch):
    path = tmp_path / "progress.json"
    error = PermissionError(errno.EPERM, "Operation is not permitted", str(path))

    def denied(self, *args, **kwargs):
        raise error

    def unexpected_retry(delay):
        pytest.fail("An unrelated permission error must fail immediately")

    monkeypatch.setattr(Path, "read_text", denied)
    monkeypatch.setattr(fileio.time, "sleep", unexpected_retry)
    with pytest.raises(PermissionError) as caught:
        fileio.read_json(path)
    assert caught.value is error


def test_job_recovery_does_not_drop_a_briefly_locked_state_document(store, monkeypatch):
    identifier = "a" * 32
    directory = store.root / "jobs" / identifier
    directory.mkdir(parents=True)
    path = directory / "state.json"
    record = {"id": identifier, "status": "interrupted", "workspace_id": "b" * 32}
    atomic_json(path, record)
    original_read, calls, sleeps = Path.read_text, [], []

    def temporarily_busy(self, *args, **kwargs):
        if self == path:
            calls.append(self)
            if len(calls) == 1:
                raise PermissionError(errno.EACCES, "Job state is being replaced", str(self))
        return original_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", temporarily_busy)
    monkeypatch.setattr(fileio.time, "sleep", sleeps.append)
    assert JobManager(store).records[identifier] == record
    assert calls == [path, path] and sleeps == [0.005]


@pytest.mark.parametrize("simulate_read_conflict", [False, True])
def test_json_writers_publish_complete_documents_without_sharing_a_temporary(
    tmp_path, monkeypatch, simulate_read_conflict
):
    path = tmp_path / "progress.json"
    atomic_json(path, {"writer": -1, "sequence": -1})
    stop = threading.Event()
    started = threading.Event()
    observations = []
    denied = []
    original_read = Path.read_text

    def temporarily_busy(self, *args, **kwargs):
        if self == path and not denied:
            denied.append(True)
            # Python's Windows text opener can expose errno 13 without winerror.
            raise PermissionError(errno.EACCES, "Progress file is being replaced", str(path))
        return original_read(self, *args, **kwargs)

    if simulate_read_conflict:
        monkeypatch.setattr(Path, "read_text", temporarily_busy)

    def read():
        started.set()
        while not stop.is_set():
            value = fileio.read_json(path)
            assert set(value) == {"writer", "sequence"}
            observations.append(value)
            stop.wait(0.001)

    def write(writer):
        for sequence in range(20):
            atomic_json(path, {"writer": writer, "sequence": sequence})

    with ThreadPoolExecutor(max_workers=3) as executor:
        reader = executor.submit(read)
        try:
            assert started.wait(timeout=10)
            writers = [executor.submit(write, writer) for writer in range(2)]
            for writer in writers:
                writer.result(timeout=10)
        finally:
            stop.set()
        reader.result(timeout=10)
    assert observations
    assert bool(denied) == simulate_read_conflict
    assert fileio.read_json(path)["sequence"] == 19
    assert list(tmp_path.iterdir()) == [path]


def test_invalid_csv_closes_nested_reader_despite_retained_error(tmp_path, monkeypatch):
    path = tmp_path / "invalid.upload"
    path.write_text("X,Y\n1,wrong\n")
    original_open, readers = Path.open, []

    def open_file(self, *args, **kwargs):
        handle = original_open(self, *args, **kwargs)
        if self == path:
            readers.append(handle)
        return handle

    monkeypatch.setattr(Path, "open", open_file)
    with pytest.raises(ValueError, match="nonnumeric") as error:
        stream_csv(path, "invalid.csv", tmp_path)
    assert error.value.__traceback__ is not None
    assert len(readers) == 2 and all(handle.closed for handle in readers)
    assert not list(tmp_path.glob("*.npy"))
    assert not list(tmp_path.glob("*.pending"))
    path.unlink()


def test_cancelled_block_writer_closes_the_suspended_decoder(tmp_path):
    finished = []

    def blocks():
        try:
            yield np.ones((1, 2))
        finally:
            finished.append(True)

    def cancel():
        raise ValueError("Import cancelled")

    target = tmp_path / "events.npy"
    with pytest.raises(ValueError, match="cancelled") as error:
        write_blocks(target, (1, 2), blocks(), check=cancel)
    assert error.value.__traceback__ is not None
    assert finished == [True]
    assert not list(tmp_path.iterdir())


def test_cached_qc_masks_release_the_flag_mapping_before_replacement(
    store, monkeypatch, windows_mapping_locks
):
    doc, sample, _, request = known_acquisition(store)
    result, flags = quality.calculate(doc, request, Engine(store), "a" * 32)
    persist(store, doc, result, flags)
    doc.quality_results.append(result)
    gate = Gate(
        sample_id=sample.id,
        name="Reviewed flags",
        kind="quality",
        quality_id=result.id,
        quality_exclusions=["nonfinite"],
    )
    doc.gates.append(gate)
    path = store.quality_path(doc.id, result.id)
    original_load, mappings = np.load, []

    def load(filename, *args, **kwargs):
        values = original_load(filename, *args, **kwargs)
        if Path(filename) == path and isinstance(values, np.memmap):
            mappings.append(values)
        return values

    monkeypatch.setattr(np, "load", load)
    engine = Engine(store)
    mask = engine.mask(doc, sample, gate.id, False)
    assert engine.mask(doc, sample, gate.id, False) is mask
    assert mappings and all(values._mmap.closed for values in mappings)
    damaged = flags.copy()
    damaged[0, 0] ^= 1
    save_array(path, damaged)
    with pytest.raises(ValueError, match="integrity"):
        quality.load_data(store, doc.id, result)


@pytest.mark.parametrize("damage", ["shape", "later_sample_hash"])
def test_rejected_project_closes_import_mappings_before_cleanup(client, monkeypatch, damage):
    doc = client.post("/api/workspaces", json={"name": "Original"}).json()
    response = client.post(
        f"/api/workspaces/{doc['id']}/import?revision=0",
        files=[
            ("files", ("one.csv", b"X,Y\n1,2\n3,4\n", "text/csv")),
            ("files", ("two.csv", b"X,Y\n5,6\n7,8\n", "text/csv")),
        ],
    )
    assert response.status_code == 200
    export = client.get(f"/api/workspaces/{doc['id']}/export/project")
    assert export.status_code == 200
    with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(entries["manifest.json"])
    if damage == "shape":
        manifest["workspace"]["samples"][0]["event_count"] += 1
        entries["manifest.json"] = json.dumps(manifest).encode()
    else:
        sample_id = manifest["workspace"]["samples"][1]["id"]
        entries[f"events/{sample_id}.npy"] = b"corrupt later sample"
    body = io.BytesIO()
    with zipfile.ZipFile(body, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    original_load, mappings = np.load, []

    def load(*args, **kwargs):
        values = original_load(*args, **kwargs)
        if isinstance(values, np.memmap):
            mappings.append(values)
        return values

    monkeypatch.setattr(np, "load", load)
    response = client.post(
        "/api/import/project",
        files={"file": ("damaged.cytoforge", body.getvalue(), "application/zip")},
    )
    assert response.status_code == 422, response.text
    expected = "Invalid event data" if damage == "shape" else "Integrity check failed"
    assert expected in response.json()["detail"]
    assert mappings and all(values._mmap.closed for values in mappings)
    assert len(client.get("/api/workspaces").json()) == 1
    store = client.app.state.store
    assert all(
        path.name == doc["id"] or not list(path.iterdir()) for path in store.events_dir.iterdir()
    )
    assert len(list((store.events_dir / doc["id"]).glob("*.npy"))) == 2
