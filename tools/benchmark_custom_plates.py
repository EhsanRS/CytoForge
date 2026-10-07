"""Evaluate all events and every well in an independently known custom rectangular plate."""

import hashlib
import json
import math
import tempfile
import time
from pathlib import Path

import numpy as np
from cytoforge import plates
from cytoforge.models import Channel, PlateDefinition, PlateGeometry, Sample, TableColumn, Workspace
from cytoforge.science import Engine, save_events
from cytoforge.store import Store

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="custom-plate-benchmark-", dir=ROOT / ".tmp") as owned:
        store = Store(Path(owned) / "data")
        try:
            workspace = Workspace(name="Synthetic custom grid independent truth")
            positions = ["A01", "B02", "C03", "Z04", "AA05", "AF24", "AL27", "AV32"]
            for index in range(8):
                values = (np.arange(16384, dtype=np.float64) + index)[:, None]
                sample = Sample(
                    name=f"Acquisition {index}", event_count=16384, channels=[Channel(name="X")]
                )
                sample.sha256 = save_events(store.data_path(workspace.id, sample.id), values)
                workspace.samples.append(sample)
            workspace = store.create(workspace)
            before = store.get(workspace.id).model_dump_json()
            count = TableColumn(name="Events", statistic="count", decimals=0)
            median = TableColumn(name="Signal", statistic="median", channel="X")
            plate = PlateDefinition(
                format="custom",
                geometry=PlateGeometry(rows=48, columns=32),
                assignments={w: [s.id] for w, s in zip(positions, workspace.samples, strict=True)},
                columns=[count, median],
            )
            engine = Engine(store)
            elapsed, median_errors = [], []
            for _ in range(2):
                started = time.perf_counter()
                result = plates.evaluate(workspace, engine, plate)
                elapsed.append(time.perf_counter() - started)
                wells = {w["well"]: w for w in result["wells"]}
                assert len(wells) == 1536 and result["rows"] == 48 and result["columns"] == 32
                for index, well in enumerate(positions):
                    assert wells[well]["values"][count.id] == 16384
                    expected = 8191.5 + index
                    ulp_error = abs(wells[well]["values"][median.id] - expected) / math.ulp(
                        expected
                    )
                    # Safe finite arithmetic can differ from the mathematical median by one ULP.
                    assert ulp_error <= 1, (well, ulp_error)
                    median_errors.append(ulp_error)
                assert all(
                    w["values"][count.id] is None
                    for key, w in wells.items()
                    if key not in positions
                )
            started = time.perf_counter()
            svg = plates.svg(result)
            svg_elapsed = time.perf_counter() - started
            for sample in workspace.samples:
                with store.data_path(workspace.id, sample.id).open("rb") as handle:
                    assert hashlib.file_digest(handle, "sha256").hexdigest() == sample.sha256
            assert store.get(workspace.id).model_dump_json() == before
            report = {
                "status": "passed",
                "scope": "Synthetic full-event plate evaluation and complete custom grid; SVG "
                "serialization timed separately; excludes generation/writing, HTTP "
                "and native runtime",
                "host_load": "Timings depend on other concurrent host activity",
                "acquisitions": 8,
                "events": 131072,
                "geometry": {"rows": 48, "columns": 32},
                "complete_wells": 1536,
                "independent_counts_verified_exactly": True,
                "independent_medians_verified_within_one_ulp": True,
                "maximum_median_error_ulp": max(median_errors),
                "original_workspace_and_event_bytes_unchanged": True,
                "evaluation_seconds": elapsed,
                "svg_seconds": svg_elapsed,
                "svg_bytes": len(svg.encode()),
                "generated_input_removed": True,
            }
        finally:
            store.close()
    output = ROOT / "artifacts/custom-plate-benchmark.json"
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
