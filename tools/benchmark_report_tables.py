"""Full-data report preparation and row/column reuse on a synthetic cohort."""

import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from cytoforge import report_tables, reports
from cytoforge.models import (
    Channel,
    LayoutDefinition,
    ReportElement,
    Sample,
    TableColumn,
    TableDefinition,
    Workspace,
)
from cytoforge.science import Engine, save_events
from cytoforge.store import Store

root = Path(__file__).resolve().parents[1]
directory = root / ".tmp" / f"table-report-benchmark-{time.time_ns()}"
store = Store(directory)
original = report_tables.tables.evaluate_table
calls = []
try:
    rng = np.random.default_rng(9438)
    samples = [
        Sample(name=f"Acquisition {i + 1:02d}", channels=[Channel(name="X")], event_count=16384)
        for i in range(64)
    ]
    mean = TableColumn(name="Mean", statistic="mean", channel="X")
    count = TableColumn(name="Events", statistic="count", decimals=0)
    scaled = TableColumn(name="Scaled", kind="formula", expression='col("Mean") * 2')
    table = TableDefinition(name="Full cohort", row_mode="samples", columns=[mean, count, scaled])
    doc = Workspace(name="Synthetic full-cohort report benchmark", samples=samples, tables=[table])
    expected = {}
    for index, sample in enumerate(samples):
        values = rng.normal(index, 1, size=(sample.event_count, 1))
        sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
        expected[sample.id] = {
            mean.id: float(np.mean(values[:, 0])),
            count.id: sample.event_count,
            scaled.id: float(np.mean(values[:, 0])) * 2,
        }
    doc = store.create(doc)
    layout = LayoutDefinition(
        name="Continued cohort",
        elements=[
            ReportElement(
                kind="table",
                table_id=table.id,
                auto_paginate=True,
                iterate=False,
                width_mm=180,
                height_mm=140,
                font_size_pt=8,
                columns_per_page=2,
                row_count=12,
            )
        ],
    )
    engine = Engine(store)

    def measured(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    report_tables.tables.evaluate_table = measured

    def prepare():
        started = time.perf_counter()
        plan = reports.plan(doc, layout, engine)
        cells = {}
        svg_bytes = 0
        for index in range(plan["page_count"]):
            page = reports.render(
                doc,
                engine,
                reports.ReportRequest(
                    revision=doc.revision,
                    definition=layout,
                    page=index,
                    review_hash=plan["review_hash"],
                    validate_sources=True,
                ),
                plan,
            )
            assert page["exportable"], page["issues"]
            svg_bytes += len(page["svg"].encode())
            frame = page["manifest"]["elements"][0]
            for row in frame["rows"]:
                for column in frame["columns"]:
                    key = row["sample_id"], column["id"]
                    assert key not in cells
                    actual = row["values"][column["id"]]
                    assert math.isclose(
                        actual, expected[key[0]][key[1]], rel_tol=1e-12, abs_tol=1e-12
                    )
                    cells[key] = actual
        assert len(cells) == 192
        return time.perf_counter() - started, plan["page_count"], svg_bytes

    cold, page_count, svg_bytes = prepare()
    reuse = []
    for step in range(3):
        layout.elements[0].x_mm = 13 + step
        reuse.append(prepare()[0])
    assert len(calls) == 1
    evidence = dict(
        status="passed",
        validated_at=datetime.now(UTC).isoformat(),
        acquisitions=len(samples),
        events=sum(s.event_count for s in samples),
        report_pages=page_count,
        independent_cells_checked=192,
        table_evaluations=len(calls),
        source_hashes_rechecked_on_every_page=True,
        cold_all_pages_seconds=cold,
        arrangement_all_pages_seconds=reuse,
        svg_bytes=svg_bytes,
        limits="Synthetic source-Python cohort on this Linux host. Includes complete report "
        "planning, vector pages, per-page source hashes and independent cell assertions. "
        "Excludes input generation/file writes, HTTP, desktop drawing and PDF/PNG output; "
        "no biological, FlowJo or other-OS claim.",
    )
    (root / "artifacts/table-report-benchmark.json").write_text(json.dumps(evidence, indent=2))
    print(json.dumps(evidence, indent=2))
finally:
    report_tables.tables.evaluate_table = original
    store.close()
