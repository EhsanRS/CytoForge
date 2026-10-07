"""Independent million-event pooled measurements without a merged acquisition file."""

import hashlib
import json
import time
from pathlib import Path

import numpy as np
from cytoforge.models import Channel, Compensation, Gate, Group, Sample, Transform, Workspace
from cytoforge.science import Engine, save_events
from cytoforge.store import Store
from cytoforge.three_dimensional import point_chunk, prepare
from cytoforge.virtual_groups import PooledEngine, plot

root = Path.cwd()
store = Store(root / ".tmp" / f"benchmark-virtual-groups-{time.time_ns()}")
count, per_sample = 1_000_000, 125_000
samples, matrices, gates = [], [], []
reference_x = (np.arange(count) % 4096 - 2048) / 128
reference_y = (np.arange(count) % 2048) / 256
for index in range(8):
    names = [f"D{index}A", f"D{index}B"]
    scale = 2 ** (index % 4 + 1)
    matrix = Compensation(
        name=f"Source basis {index}", detectors=names, matrix=[[scale, 0], [0, scale * 2]]
    )
    sample = Sample(
        name=f"Source {index + 1}",
        event_count=per_sample,
        channels=[Channel(name=name) for name in names]
        + [Channel(name=name) for name in ("CD3", "CD4")],
        aliases=dict(zip(["CD3", "CD4"], names, strict=True)),
        compensation_id=matrix.id,
    )
    samples.append(sample)
    matrices.append(matrix)
    gates.append(
        Gate(sample_id=sample.id, name="Common population", kind="range", x="CD3", bounds=[-1, 2])
    )
group = Group(name="Eight original sources", sample_ids=[s.id for s in samples])
doc = Workspace(
    name="Million-event virtual truth",
    samples=samples,
    compensations=matrices,
    gates=gates,
    groups=[group],
)
try:
    paths = [store.data_path(doc.id, s.id) for s in samples]
    for index, (sample, path) in enumerate(zip(samples, paths, strict=True)):
        span = slice(index * per_sample, (index + 1) * per_sample)
        scale = matrices[index].matrix[0][0]
        sample.sha256 = save_events(
            path, np.column_stack([reference_x[span] * scale, reference_y[span] * scale * 2])
        )
    doc = store.create(doc)
    before = doc.model_dump_json()
    hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
    files_before = sorted(str(p.relative_to(store.root)) for p in store.root.rglob("*.npy"))
    engine = Engine(store)
    view = PooledEngine(doc, engine, samples[0].id, group.id)
    times = {}
    started = time.perf_counter()
    x = view.column(doc, view.view, "CD3")
    y = view.column(doc, view.view, "CD4")
    times["pooled_two_coordinates_seconds"] = time.perf_counter() - started
    error = max(float(np.max(np.abs(x - reference_x))), float(np.max(np.abs(y - reference_y))))
    assert error == 0
    expected = (reference_x >= -1) & (reference_x < 2)
    started = time.perf_counter()
    mask = view.mask(doc, view.view, gates[0].id)
    times["pooled_population_seconds"] = time.perf_counter() - started
    np.testing.assert_array_equal(mask, expected)
    for mode in ["histogram", "cdf", "density"]:
        started = time.perf_counter()
        payload = plot(
            doc,
            engine,
            samples[0].id,
            group.id,
            x="CD3",
            y=None if mode != "density" else "CD4",
            gate_id=gates[0].id,
            mode=mode,
            bins=128,
            x_transform=Transform(),
            y_transform=Transform(),
        )
        times[mode + "_seconds"] = time.perf_counter() - started
        assert payload["count"] == int(expected.sum())
        if mode in {"histogram", "cdf"}:
            assert sum(payload["counts"]) == int(expected.sum())
    started = time.perf_counter()
    prepared = prepare(
        doc,
        view,
        samples[0].id,
        "CD3",
        "CD4",
        {"z": "CD3", "all_events": True},
        gate_id=gates[0].id,
    )
    event_ids = np.concatenate(
        [
            point_chunk(prepared, start)["event_id"]
            for start in range(0, prepared[0]["displayed_count"], 65536)
        ]
    )
    np.testing.assert_array_equal(event_ids, np.flatnonzero(expected))
    times["prepare_and_stream_selected_3d_seconds"] = time.perf_counter() - started
    assert store.get(doc.id).model_dump_json() == before
    assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths] == hashes
    assert sorted(str(p.relative_to(store.root)) for p in store.root.rglob("*.npy")) == files_before
    result = dict(
        status="passed",
        event_count=count,
        source_count=8,
        independently_checked_measurements=2 * count,
        maximum_absolute_error=error,
        population_count=int(expected.sum()),
        merged_acquisition_files_created=0,
        original_workspace_and_event_files_unchanged=True,
        times=times,
        scope="Local in-process CPU timings, warm filesystem; eight source-specific matrices; "
        "no hardware-GPU or cross-platform claim",
    )
    (root / "artifacts" / "benchmark-virtual-groups.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print(json.dumps(result))
finally:
    store.close()
