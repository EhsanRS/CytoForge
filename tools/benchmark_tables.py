"""Full-event custom-table benchmark with independent values on this host."""

import json
import platform
import time
from pathlib import Path

import numpy as np
from cytoforge.models import (
    Channel,
    Gate,
    Sample,
    TableColumn,
    TableComparison,
    TableDefinition,
    TablePivot,
    Workspace,
)
from cytoforge.science import Engine, save_events
from cytoforge.store import Store
from cytoforge.tables import evaluate_table, write_xlsx

root = Path(__file__).resolve().parents[1]
directory = root / ".tmp/benchmark-tables"
directory.mkdir(parents=True, exist_ok=True)
store = Store(directory)
rng = np.random.default_rng(901)
workspace = Workspace(name="Full-event table performance")
truth = []
for index in range(4):
    values = rng.normal(index * 100, 200, (250000, 16))
    values[0, 0] = np.nan
    sample = Sample(
        name=f"Sample {index}",
        event_count=len(values),
        channels=[Channel(name=f"C{i}") for i in range(16)],
        tags={"Treatment": "A" if index < 2 else "B"},
    )
    workspace.samples.append(sample)
    gate = Gate(sample_id=sample.id, name="Cells", kind="range", x="C1", bounds=[-1000, 1500])
    workspace.gates.append(gate)
    chosen = values[(values[:, 1] >= -1000) & (values[:, 1] < 1500), 0]
    truth.append(float(np.nanmedian(chosen)))
    sample.sha256 = save_events(store.data_path(workspace.id, sample.id), values)
workspace = store.create(workspace)
columns = [TableColumn(name="Events", statistic="count", population_path=["Cells"], decimals=0)]
columns += [
    TableColumn(
        name=stat.title(), statistic=stat, channel="C0", population_path=["Cells"], percentile=90
    )
    for stat in ("median", "mean", "std", "percentile")
]
columns += [
    TableColumn(name="Treatment", kind="metadata", metadata_key="Treatment"),
    TableColumn(
        name="Relative median", kind="formula", expression='col("Median") / mean(col("Median"))'
    ),
    TableColumn(name="Variation", kind="formula", expression='col("Std") / col("Mean")'),
]
table = TableDefinition(
    name="Full-event response",
    row_mode="samples",
    columns=columns,
    pivot=TablePivot(rows=[columns[5].id], measures=[columns[1].id]),
    comparison=TableComparison(
        group_column=columns[5].id,
        group_a="A",
        group_b="B",
        measures=[columns[1].id, columns[6].id],
    ),
)
engine = Engine(store)
timings = []
for _ in range(4):
    start = time.perf_counter()
    output = evaluate_table(workspace, engine, table)
    timings.append(time.perf_counter() - start)
    np.testing.assert_allclose(
        [r["values"][columns[1].id] for r in output["rows"]], truth, rtol=1e-12, atol=1e-12
    )
start = time.perf_counter()
write_xlsx(root / "artifacts/table-benchmark.xlsx", output, directory)
evidence = dict(
    machine=platform.platform(),
    events=1000000,
    samples=4,
    acquisition_channels=16,
    table_columns=len(columns),
    full_event_medians=truth,
    first_seconds=timings[0],
    warm_median_seconds=float(np.median(timings[1:])),
    all_seconds=timings,
    xlsx_seconds=time.perf_counter() - start,
    cache_bytes=engine.cache.bytes,
    pivot_groups=len(output["pivot"]["rows"]),
    comparisons=output["comparisons"],
    notes=(
        "Synthetic data on one host; full-event calculations include formulas, pivot "
        "and comparisons. Excludes HTTP/rendering. OS page cache may already be warm; "
        "this does not establish biological accuracy."
    ),
)
(root / "artifacts/table-benchmark.json").write_text(json.dumps(evidence, indent=2))
print(json.dumps(evidence, indent=2))
store.close()
