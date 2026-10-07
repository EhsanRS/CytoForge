"""Disk failures and worker interruption must preserve published scientific data."""

import errno
import os
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from cytoforge import comparison_storage as storage
from cytoforge import population_comparison as comparisons
from cytoforge.jobs import JobManager
from cytoforge.models import new_id
from cytoforge.population_statistics import probability_tree
from cytoforge.store import Store
from test_population_comparison import fixture


def original_events(store, doc):
    return {s.id: store.data_path(doc.id, s.id).read_bytes() for s in doc.samples}


def assert_originals_unchanged(store, doc, sources):
    assert store.get(doc.id).model_dump() == doc.model_dump()
    assert original_events(store, doc) == sources


def test_low_disk_failure_preserves_prior_artifacts_workspace_and_source_events(store, monkeypatch):
    doc, request, engine = fixture(store)
    previous = comparisons.calculate(doc, request, engine, new_id())
    path = store.comparison_path(doc.id, previous.id)
    artifact = path.read_bytes()
    sources = original_events(store, doc)
    scratch = store.root / "owned-scratch"
    monkeypatch.setattr(
        storage.shutil,
        "disk_usage",
        lambda directory: SimpleNamespace(free=storage.DISK_HEADROOM_BYTES),
    )
    with pytest.raises(OSError, match="headroom") as failure:
        comparisons.calculate(doc, request, engine, new_id(), scratch_directory=scratch)
    assert failure.value.errno == errno.ENOSPC
    assert not list(scratch.iterdir())
    assert path.read_bytes() == artifact
    assert_originals_unchanged(store, doc, sources)


@pytest.mark.parametrize("allocation", ["missing", "unsupported"])
def test_fallback_checks_combined_unwritten_acquisitions_before_overcommitting_disk(
    tmp_path, monkeypatch, allocation
):
    if allocation == "missing":
        monkeypatch.delattr(storage.os, "posix_fallocate", raising=False)
    else:

        def unsupported(*args):
            raise OSError(errno.EINVAL, "Allocation is unsupported")

        monkeypatch.setattr(storage.os, "posix_fallocate", unsupported, raising=False)
    selection = np.ones(8192, dtype=bool)
    required = len(selection) * 2 * 8 + 4096
    monkeypatch.setattr(
        storage.shutil,
        "disk_usage",
        lambda directory: SimpleNamespace(free=storage.DISK_HEADROOM_BYTES + required + 800),
    )
    with pytest.raises(OSError, match="headroom"):
        with storage.JointStorage(tmp_path, 2) as joint:
            joint.prepare("first", selection)
            handle = joint.samples["first"].handle
            assert not joint.samples["first"].reserved
            joint.prepare("second", selection)
    assert handle.closed
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "error_code", [errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC), errno.EIO]
)
def test_allocation_failure_closes_its_file_and_preserves_scientific_inputs(
    store, monkeypatch, error_code
):
    doc, request, engine = fixture(store)
    sources = original_events(store, doc)
    descriptors = []

    def fail_allocate(descriptor, offset, size):
        descriptors.append(descriptor)
        raise OSError(error_code, "Injected allocation failure")

    monkeypatch.setattr(storage.os, "posix_fallocate", fail_allocate, raising=False)
    scratch = store.root / "owned-scratch"
    with pytest.raises(OSError) as failure:
        comparisons.calculate(doc, request, engine, new_id(), scratch_directory=scratch)
    assert failure.value.errno == error_code
    if error_code != errno.EIO:
        assert "space or quota" in str(failure.value)
    assert len(descriptors) == 1
    with pytest.raises(OSError) as closed:
        os.fstat(descriptors[0])
    assert closed.value.errno == errno.EBADF
    assert not list(scratch.iterdir())
    assert_originals_unchanged(store, doc, sources)


def test_unsupported_allocation_uses_regular_writes_then_readonly_mapping_with_exact_events(
    tmp_path, monkeypatch
):
    def unsupported(*args):
        raise OSError(getattr(errno, "EOPNOTSUPP", errno.EINVAL), "Allocation is unsupported")

    monkeypatch.setattr(storage.os, "posix_fallocate", unsupported, raising=False)
    values = np.arange(36, dtype=float).reshape(12, 3)
    values[2, 1], values[4, 2] = np.nan, np.inf
    selected = np.arange(len(values)) % 2 == 0
    expected = values[selected & np.all(np.isfinite(values), axis=1)]
    with storage.JointStorage(tmp_path, 3) as joint:
        joint.prepare("owned", selected)
        sample = joint.samples["owned"]
        handle = sample.handle
        for axis in range(3):
            joint.write(axis, {"owned": values[:, axis]})
            assert sample.values is None
        population = sample.rows(selected)
        assert handle.closed
        assert not sample.values.flags.writeable
        assert sample.values.flags.f_contiguous
        actual = np.column_stack([population.column(axis) for axis in range(3)])
        np.testing.assert_array_equal(actual, expected)
        assert (
            probability_tree(population, 2, 1).serialize()
            == probability_tree(expected, 2, 1).serialize()
        )
    assert not list(tmp_path.iterdir())


class FailedWriter:
    def __init__(self, handle, kind):
        self.handle, self.kind = handle, kind

    def __getattr__(self, name):
        return getattr(self.handle, name)

    def write(self, value):
        if self.kind == "short":
            return self.handle.write(value[:-8])
        code = errno.ENOSPC if self.kind == "space" else getattr(errno, "EDQUOT", errno.ENOSPC)
        raise OSError(code, "Injected coordinate write failure")


@pytest.mark.parametrize("kind", ["short", "space", "quota"])
def test_failed_coordinate_write_never_publishes_incomplete_columns_and_removes_scratch(
    tmp_path, kind
):
    values = np.arange(10, dtype=float)
    original = values.tobytes()
    with pytest.raises(OSError) as failure:
        with storage.JointStorage(tmp_path, 2) as joint:
            joint.prepare("owned", np.ones(len(values), dtype=bool))
            sample = joint.samples["owned"]
            handle = sample.handle
            sample.handle = FailedWriter(handle, kind)
            try:
                joint.write(0, {"owned": values})
            except OSError:
                with pytest.raises(ValueError, match="incomplete"):
                    sample.rows(np.ones(len(values), dtype=bool))
                raise
    assert failure.value.errno == (
        errno.EIO
        if kind == "short"
        else errno.ENOSPC
        if kind == "space"
        else getattr(errno, "EDQUOT", errno.ENOSPC)
    )
    assert handle.closed
    assert not list(tmp_path.iterdir())
    assert values.tobytes() == original


def test_flush_failure_cannot_open_a_mapping_and_releases_the_staged_file(tmp_path, monkeypatch):
    def fail_flush(descriptor):
        raise OSError(errno.EIO, "Injected durable flush failure")

    with pytest.raises(OSError, match="durable flush"):
        with storage.JointStorage(tmp_path, 2) as joint:
            selected = np.ones(10, dtype=bool)
            joint.prepare("owned", selected)
            sample = joint.samples["owned"]
            handle = sample.handle
            for axis in range(2):
                joint.write(axis, {"owned": np.arange(10)})
            monkeypatch.setattr(storage.os, "fsync", fail_flush)
            try:
                sample.rows(selected)
            except OSError:
                assert sample.values is None
                raise
    assert handle.closed
    assert not list(tmp_path.iterdir())


def hold_partial_result(profile, workspace_id, identifier, directory, crash):
    directory = Path(directory)
    store = Store(Path(profile))
    original_compress = comparisons.np.savez_compressed

    def pause_during_write(handle, **arrays):
        original_compress(handle, **arrays)
        handle.flush()
        (directory / "ready").write_text("Unpublished result and read mapping are open")
        if crash:
            os._exit(23)
        while True:
            time.sleep(0.1)

    with storage.JointStorage(directory, 2) as joint:
        selection = np.ones(4096, dtype=bool)
        joint.prepare("owned", selection)
        for axis in range(2):
            joint.write(axis, {"owned": np.arange(4096)})
        joint.samples["owned"].rows(selection)
        comparisons.np.savez_compressed = pause_during_write
        comparisons.write_artifact(store, workspace_id, identifier, {"counts": np.arange(64)})


@pytest.mark.parametrize("action", ["cancel", "close", "crash"])
def test_parent_cleans_interrupted_actual_artifact_write_only_after_owned_worker_exits(
    store, action
):
    doc, request, _ = fixture(store)
    manager = JobManager(store)
    identifier = manager.submit(doc, request)["id"]
    final = store.comparison_path(doc.id, identifier)
    published = b"Preserved already published result"
    final.write_bytes(published)
    partial = final.with_suffix(".npz.partial")
    sibling = store.comparison_path(doc.id, new_id()).with_suffix(".npz.partial")
    sibling.write_bytes(b"Another job owns this incomplete result")
    scratch = manager.directory / identifier / "comparison-scratch"
    sources = original_events(store, doc)
    child = manager.context.Process(
        target=hold_partial_result,
        args=(str(store.root), doc.id, identifier, str(scratch), action == "crash"),
    )
    try:
        child.start()
        manager.processes[identifier] = child
        manager.records[identifier]["status"] = "running"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not (scratch / "ready").exists():
            if not child.is_alive():
                assert (scratch / "ready").exists(), "Owned worker exited before writing the result"
                break
            time.sleep(0.02)
        assert (scratch / "ready").exists(), "Owned worker did not become ready"
        assert partial.exists()
        assert final.read_bytes() == published
        if action == "crash":
            manager.start()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                job = manager.get(doc, identifier)
                if job["status"] == "failed":
                    break
                time.sleep(0.02)
            assert job["status"] == "failed", job
            assert "code 23" in job["error"]
        elif action == "cancel":
            manager.cancel(doc, identifier)
        else:
            manager.close()
        assert not child.is_alive()
        assert child.exitcode is not None
        assert not scratch.exists()
        assert not partial.exists()
        assert final.read_bytes() == published
        assert sibling.read_bytes() == b"Another job owns this incomplete result"
        assert_originals_unchanged(store, doc, sources)
    finally:
        manager.close()
        if child.is_alive():
            child.terminate()
            child.join(5)


def test_cleanup_failure_still_attempts_other_owned_temporary_files(store, monkeypatch):
    doc, request, _ = fixture(store)
    manager = JobManager(store)
    identifier = manager.submit(doc, request)["id"]
    scratch = manager.directory / identifier / "comparison-scratch"
    scratch.mkdir()
    partial = store.comparison_path(doc.id, identifier).with_suffix(".npz.partial")
    partial.write_bytes(b"Incomplete result")
    original_remove = storage.shutil.rmtree

    def fail_remove(path, *args, **kwargs):
        if Path(path) == scratch:
            raise OSError(errno.EACCES, "Injected cleanup failure")
        return original_remove(path, *args, **kwargs)

    try:
        monkeypatch.setattr(storage.shutil, "rmtree", fail_remove)
        manager.cancel(doc, identifier)
        assert scratch.exists()
        assert not partial.exists()
        assert "Injected cleanup failure" in manager.get(doc, identifier)["cleanup_error"]
    finally:
        manager.close()


@pytest.mark.parametrize("invalid", ["../foreign", "a" * 31, "A" * 32, None])
def test_cleanup_rejects_invalid_job_identity_without_removing_data(store, invalid):
    doc, request, _ = fixture(store)
    manager = JobManager(store)
    identifier = manager.submit(doc, request)["id"]
    partial = store.comparison_path(doc.id, identifier).with_suffix(".npz.partial")
    partial.write_bytes(b"Must remain owned by the actual job")
    try:
        record = manager.records[identifier].copy()
        record["id"] = invalid
        manager._cleanup_comparison_scratch(record)
        assert "Invalid comparison identity" in record["cleanup_error"]
        assert partial.read_bytes() == b"Must remain owned by the actual job"
    finally:
        manager.close()


def test_cleanup_does_not_follow_workspace_directory_symlink_outside_its_store(store, tmp_path):
    doc, request, _ = fixture(store)
    manager = JobManager(store)
    identifier = manager.submit(doc, request)["id"]
    foreign = tmp_path / "other-profile"
    foreign.mkdir()
    sentinel = foreign / f"{identifier}.npz.partial"
    sentinel.write_bytes(b"Data outside the job's store")
    parent = store.root / "comparisons"
    parent.mkdir(exist_ok=True)
    link = parent / doc.id
    link.symlink_to(foreign, target_is_directory=True)
    try:
        record = manager.records[identifier]
        manager._cleanup_comparison_scratch(record)
        assert "outside the job's data directory" in record["cleanup_error"]
        assert sentinel.read_bytes() == b"Data outside the job's store"
    finally:
        link.unlink()
        manager.close()


def test_queued_cancellation_does_not_create_a_comparison_artifact_directory(store):
    doc, request, _ = fixture(store)
    manager = JobManager(store)
    identifier = manager.submit(doc, request)["id"]
    try:
        assert not (store.root / "comparisons").exists()
        manager.cancel(doc, identifier)
        assert not (store.root / "comparisons").exists()
    finally:
        manager.close()


def test_restart_marks_job_interrupted_without_assuming_its_previous_worker_has_exited(store):
    doc, request, _ = fixture(store)
    manager = JobManager(store)
    identifier = manager.submit(doc, request)["id"]
    record = manager.records[identifier]
    record["status"] = "running"
    manager._save(record)
    scratch = manager.directory / identifier / "comparison-scratch"
    scratch.mkdir()
    partial = store.comparison_path(doc.id, identifier).with_suffix(".npz.partial")
    partial.write_bytes(b"No worker death has been verified")
    restarted = JobManager(store)
    try:
        assert restarted.get(doc, identifier)["status"] == "interrupted"
        assert scratch.exists()
        assert partial.read_bytes() == b"No worker death has been verified"
    finally:
        restarted.close()
        manager.close()
