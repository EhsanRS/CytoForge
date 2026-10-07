"""Full-event plate measurements with independently computed per-acquisition medians."""

import json
import platform
import time
from pathlib import Path

import numpy as np
from cytoforge import plates
from cytoforge.models import (
    Channel,
    Gate,
    PlateDefinition,
    Sample,
    TableColumn,
    Workspace,
    plate_well,
)
from cytoforge.science import Engine, save_events
from cytoforge.store import Store

root = Path(__file__).resolve().parents[1]
directory = root / ".tmp" / f"benchmark-plates-{time.time_ns()}"
store = Store(directory)
rng = np.random.default_rng(619)
workspace = Workspace(name="Plate full-event performance")
truth, counts = [], []
for index in range(384):
    values = rng.normal(index * 3, 100, (2604 + (index < 64), 16))
    values[0, 0] = np.nan
    dose = index % 12 + 1
    sample = Sample(
        name=f"Acquisition {index}",
        event_count=len(values),
        channels=[Channel(name=f"C{i}") for i in range(16)],
        tags={"Dose": str(dose), "Treatment": str(index % 4)},
    )
    workspace.samples.append(sample)
    gate = Gate(sample_id=sample.id, name="Cells", kind="range", x="C1", bounds=[-200, 1300])
    workspace.gates.append(gate)
    chosen = values[(values[:, 1] >= -200) & (values[:, 1] < 1300), 0]
    truth.append(float(np.nanmedian(chosen)))
    counts.append(len(chosen))
    sample.sha256 = save_events(store.data_path(workspace.id, sample.id), values)
workspace = store.create(workspace)
columns = [TableColumn(name="Events", statistic="count", population_path=["Cells"])]
columns += [
    TableColumn(name=s.title(), statistic=s, channel="C0", population_path=["Cells"])
    for s in ("median", "mean", "std", "finite_count")
]
columns += [
    TableColumn(name="Dose", kind="metadata", metadata_key="Dose", metadata_numeric=True),
    TableColumn(name="Treatment", kind="metadata", metadata_key="Treatment"),
    TableColumn(name="Normalized", kind="formula", expression='col("Median")/col("Dose")'),
]
reports = []
for shape in (96, 384, 1536):
    members = workspace.samples[:96] if shape == 96 else workspace.samples
    width = plates.PLATE_SHAPES[shape][1]
    plate = PlateDefinition(
        format=shape,
        columns=columns,
        assignments={
            plate_well(i // width, i % width): [sample.id] for i, sample in enumerate(members)
        },
    )
    engine = Engine(store)
    elapsed = []
    for _ in range(3):
        start = time.perf_counter()
        result = plates.evaluate(workspace, engine, plate)
        elapsed.append(time.perf_counter() - start)
        np.testing.assert_allclose(
            [well["values"][columns[1].id] for well in result["wells"][: len(members)]],
            truth[: len(members)],
            rtol=1e-12,
        )
        assert [
            well["values"][columns[0].id] for well in result["wells"][: len(members)]
        ] == counts[: len(members)]
    start = time.perf_counter()
    encoded = json.dumps(result).encode()
    reports.append(
        dict(
            format=shape,
            events=sum(s.event_count for s in members),
            acquisitions=len(members),
            measured_wells=result["mapped_wells"],
            columns=len(columns),
            first_seconds=elapsed[0],
            warm_median_seconds=float(np.median(elapsed[1:])),
            all_seconds=elapsed,
            json_bytes=len(encoded),
            json_seconds=time.perf_counter() - start,
            cache_bytes=engine.cache.bytes,
        )
    )
evidence = dict(
    machine=platform.platform(),
    reports=reports,
    notes=(
        "Synthetic acquisitions on one host. Full-event population statistics, keywords, "
        "formulas, replicate aggregation and display scaling. Independent medians and counts "
        "checked after each run. Excludes input generation, file writing, HTTP and rendering; "
        "OS pages may already be warm. This is not biological or FlowJo accuracy evidence."
    ),
)
(root / "artifacts/plate-benchmark.json").write_text(json.dumps(evidence, indent=2))
print(json.dumps(evidence, indent=2))
store.close()
