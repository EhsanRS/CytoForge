"""Complete column storage agrees with dense distributions and cleans temporary data."""

import time
from pathlib import Path

import numpy as np
import pytest
from cytoforge.comparison_storage import JointStorage, PooledColumns
from cytoforge.jobs import JobManager
from cytoforge.models import AnalysisInput, Gate, new_id
from cytoforge.population_comparison import calculate, summary_probability
from cytoforge.population_statistics import probability_tree
from test_population_comparison import fixture


@pytest.mark.parametrize("dimensions", [2, 16, 64])
@pytest.mark.parametrize("kind", ["continuous", "ties", "extreme", "subnormal"])
def test_column_pools_match_dense_complete_event_partitions(tmp_path, dimensions, kind):
    rng = np.random.default_rng(871)
    arrays = [rng.normal(size=(n, dimensions)) for n in [257, 137, 163]]
    for values in arrays:
        if kind == "ties":
            values[:] = np.round(values)
        elif kind == "extreme":
            values[:] = np.clip(values, -4, 4) * 2e307
        elif kind == "subnormal":
            values[:] *= 1e-310
    arrays[0][0, 0] = np.nan
    arrays[0][1, -1] = np.inf
    arrays[1][3, -1] = np.nan
    arrays[2][-1, 0] = np.inf
    expected = [v[np.all(np.isfinite(v), axis=1)] for v in arrays]
    dense_reference = np.concatenate(expected[:2])
    dense_tree = probability_tree(dense_reference, bins=16, minimum_events=4)
    selections = [np.ones(len(v), dtype=bool) for v in arrays]
    with JointStorage(tmp_path, dimensions) as storage:
        for index, selection in enumerate(selections):
            storage.prepare(str(index), selection)
        for axis in range(dimensions):
            storage.write(axis, {str(i): v[:, axis] for i, v in enumerate(arrays)})
        columns = [
            storage.samples[str(i)].rows(selection) for i, selection in enumerate(selections)
        ]
        reference = PooledColumns(columns[:2], dimensions)
        tree = probability_tree(reference, bins=16, minimum_events=4)
        assert tree.serialize() == dense_tree.serialize()
        np.testing.assert_array_equal(tree.assign(columns[2]), dense_tree.assign(expected[2]))
        assert tree.compare(columns[2]) == dense_tree.compare(expected[2])
        for axis in [0, dimensions - 1]:
            np.testing.assert_array_equal(reference.column(axis), dense_reference[:, axis])
            events = np.array([0, 8, len(expected[0]) - 1, len(expected[0]), len(reference) - 1])
            np.testing.assert_array_equal(
                reference.column(axis, events), dense_reference[events, axis]
            )
        retained = columns[2].column(0)
        assert len(list(tmp_path.rglob("*.npy"))) == 3
    assert not list(tmp_path.rglob("*.npy"))
    np.testing.assert_array_equal(retained, expected[2][:, 0])


def test_storage_reuses_each_acquisition_for_overlapping_gates_and_shared_finite_events(tmp_path):
    selected = np.array([True, False, True, True, True, True])
    x, y = np.array([0, 1, 2, 3, np.nan, 5]), np.array([0, 1, 2, np.inf, 4, 5])
    target = np.array([True, True, True, True, True, False])
    control = np.array([False, True, True, True, True, True])
    with JointStorage(tmp_path, 2) as storage:
        storage.prepare("sample", selected)
        storage.write(0, {"sample": x})
        with pytest.raises(ValueError, match="incomplete"):
            storage.samples["sample"].rows(target)
        storage.write(1, {"sample": y})
        sample = storage.samples["sample"]
        first, second = sample.rows(target), sample.rows(control)
        assert first.acquisition is second.acquisition
        assert len(first) == len(second) == 2
        np.testing.assert_array_equal(first.column(0), [0, 2])
        np.testing.assert_array_equal(second.column(0), [2, 5])
        assert sample.shared_count(target, control) == 1
        assert len(list(tmp_path.rglob("*.npy"))) == 1


def test_empty_members_and_disabled_joint_storage_create_no_large_files(tmp_path):
    with JointStorage(tmp_path, 2) as storage:
        storage.prepare("empty", np.zeros(3, dtype=bool))
        storage.prepare("real", np.ones(4, dtype=bool))
        for axis in range(2):
            storage.write(axis, {"empty": np.arange(3), "real": np.arange(4)})
        members = [
            storage.samples["empty"].rows(np.zeros(3, dtype=bool)),
            storage.samples["real"].rows(np.ones(4, dtype=bool)),
        ]
        pool = PooledColumns(members, 2)
        assert pool.shape == (4, 2) and probability_tree(pool, 2).control_counts.tolist() == [2, 2]
        with pytest.raises(ValueError, match="coordinates"):
            PooledColumns(members, 3)
    disabled = tmp_path / "unused"
    with JointStorage(disabled, 2, enabled=False) as storage:
        storage.prepare("unused", np.ones(10, dtype=bool))
        storage.write(0, {"unused": np.arange(10)})
    assert not disabled.exists()


def test_failed_or_cancelled_calculation_closes_mappings_before_removing_scratch(store):
    doc, request, engine = fixture(store)
    scratch = store.root / "owned-scratch"
    before = doc.model_dump_json()
    source_bytes = {s.id: store.data_path(doc.id, s.id).read_bytes() for s in doc.samples}

    def cancel(stage, fraction):
        if stage == "Building joint probability partitions":
            assert len(list(scratch.rglob("*.npy"))) == 2
            raise InterruptedError("User cancelled")

    with pytest.raises(InterruptedError, match="cancelled"):
        calculate(doc, request, engine, new_id(), cancel, scratch_directory=scratch)
    assert not list(scratch.iterdir())
    assert doc.model_dump_json() == before
    assert all(
        store.data_path(doc.id, sid).read_bytes() == data for sid, data in source_bytes.items()
    )


def test_shared_baseline_tree_reuses_only_the_matching_excluded_acquisition(store, monkeypatch):
    rng = np.random.default_rng(125)
    arrays = [rng.normal(size=(n, 2)) + i for i, n in enumerate([50, 60, 70])]
    doc, request, engine = fixture(store, arrays)
    gate = Gate(
        sample_id=doc.samples[0].id, name="Control subset", kind="range", x="X", bounds=[-1.2, 0.4]
    )
    doc = store.mutate(
        doc.id, "Add control subset", lambda value: value.gates.append(gate), doc.revision
    )
    request.revision = doc.revision
    request.inputs = [AnalysisInput(sample_id=doc.samples[2].id)]
    request.controls = [AnalysisInput(sample_id=s.id) for s in doc.samples[:2]] + [
        AnalysisInput(sample_id=doc.samples[0].id, gate_id=gate.id)
    ]
    request = type(request).model_validate(request.model_dump())
    partition_sizes = []

    def record_partition(reference, *args, **kwargs):
        partition_sizes.append(len(reference))
        return probability_tree(reference, *args, **kwargs)

    monkeypatch.setattr("cytoforge.population_statistics.probability_tree", record_partition)
    result = calculate(doc, request, engine, new_id())
    assert partition_sizes == [110, 60, 50]
    targets = [arrays[0], arrays[1], arrays[0][(arrays[0][:, 0] >= -1.2) & (arrays[0][:, 0] < 0.4)]]
    for row, target, reference in zip(
        result.joint_rows[1:], targets, [arrays[1], arrays[0], arrays[1]], strict=True
    ):
        expected = probability_tree(reference, request.probability_bins, request.minimum_bin_events)
        assert row.probability == summary_probability(expected.compare(target))


class ExitedWorker:
    def terminate(self):
        pass

    def join(self, timeout=None):
        pass

    def is_alive(self):
        return False


@pytest.mark.parametrize("action", ["cancel", "close"])
def test_parent_removes_owned_comparison_scratch_after_worker_has_exited(store, action):
    doc, request, _ = fixture(store)
    manager = JobManager(store)
    identifier = manager.submit(doc, request)["id"]
    scratch = manager.directory / identifier / "comparison-scratch"
    scratch.mkdir()
    (scratch / "partial.npy").write_bytes(b"partial temporary coordinate")
    manager.processes[identifier] = ExitedWorker()
    try:
        manager.cancel(doc, identifier) if action == "cancel" else manager.close()
        assert not scratch.exists()
        assert store.get(doc.id).model_dump() == doc.model_dump()
    finally:
        manager.close()


def test_background_comparison_worker_finishes_without_network_and_preserves_original_events(store):
    doc, request, _ = fixture(store)
    request = type(request).model_validate(request.model_dump())
    manager = JobManager(store)
    original = {s.id: store.data_path(doc.id, s.id).read_bytes() for s in doc.samples}
    identifier = manager.submit(doc, request)["id"]
    try:
        manager.start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            job = manager.get(doc, identifier)
            if job["status"] not in {"queued", "running"}:
                break
            time.sleep(0.03)
        assert job["status"] == "succeeded", job
        assert job["result"]["joint_rows"][0]["probability"]["calibration_minimum_events"] == 10
        assert job["result"]["rows"][0]["metrics"]["ens_percent"] == pytest.approx(44)
        assert not (manager.directory / identifier / "comparison-scratch").exists()
        assert all(
            store.data_path(doc.id, sid).read_bytes() == data for sid, data in original.items()
        )
        assert store.get(doc.id).model_dump() == doc.model_dump()
    finally:
        manager.close()


def hold_mapped_comparison_scratch(directory):
    directory = Path(directory)
    with JointStorage(directory, 64) as storage:
        storage.prepare("owned-acquisition", np.ones(4096, dtype=bool))
        for axis in range(64):
            storage.write(axis, {"owned-acquisition": np.arange(4096)})
        storage.samples["owned-acquisition"].rows(np.ones(4096, dtype=bool))
        (directory / "ready").write_text("mapping is open")
        # The parent deliberately terminates this owned child during cancellation.
        while True:
            time.sleep(0.1)


@pytest.mark.parametrize("action", ["cancel", "close"])
def test_terminating_owned_worker_releases_open_mappings_before_parent_cleanup(store, action):
    doc, request, _ = fixture(store)
    manager = JobManager(store)
    identifier = manager.submit(doc, request)["id"]
    scratch = manager.directory / identifier / "comparison-scratch"
    child = manager.context.Process(target=hold_mapped_comparison_scratch, args=(str(scratch),))
    originals = {s.id: store.data_path(doc.id, s.id).read_bytes() for s in doc.samples}
    try:
        child.start()
        manager.processes[identifier] = child
        manager.records[identifier]["status"] = "running"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not (scratch / "ready").exists():
            assert child.is_alive(), "The owned test worker exited before opening its mapping"
            time.sleep(0.02)
        assert (scratch / "ready").exists(), "The owned worker did not become ready"
        assert len(list(scratch.rglob("*.npy"))) == 1
        manager.cancel(doc, identifier) if action == "cancel" else manager.close()
        assert not child.is_alive()
        assert child.exitcode is not None
        assert not scratch.exists()
        assert manager.get(doc, identifier)["status"] == (
            "cancelled" if action == "cancel" else "interrupted"
        )
        assert store.get(doc.id).model_dump() == doc.model_dump()
        assert all(
            store.data_path(doc.id, sid).read_bytes() == data for sid, data in originals.items()
        )
    finally:
        manager.close()
        if child.is_alive():
            child.terminate()
            child.join(5)
