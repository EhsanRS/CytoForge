"""Measure full-data vector reporting and arrangement reuse with independent counts."""

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from cytoforge import reports
from cytoforge.models import (
    Channel,
    Gate,
    LayoutDefinition,
    PlotDefinition,
    ReportElement,
    Sample,
    Workspace,
)
from cytoforge.science import Engine, save_events
from cytoforge.store import Store

root = Path(__file__).resolve().parents[1]
directory = root / ".tmp" / f"report-benchmark-{time.time_ns()}"
store = Store(directory)
try:
    values = np.random.default_rng(583).normal(size=(1_000_000, 2))
    sample = Sample(
        name="Million-event fixture",
        channels=[Channel(name="X"), Channel(name="Y")],
        event_count=len(values),
    )
    doc = Workspace(name="Full-event report benchmark", samples=[sample])
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    gate = Gate(sample_id=sample.id, name="Positive", kind="range", x="X", bounds=[0, 3])
    doc.gates = [gate]
    doc = store.create(doc)
    expected = int(((values[:, 0] >= 0) & (values[:, 0] < 3)).sum())
    definition = LayoutDefinition(
        name="Vector benchmark",
        elements=[
            ReportElement(
                kind="plot",
                width_mm=180,
                height_mm=150,
                plot=PlotDefinition(
                    sample_id=sample.id,
                    gate_id=gate.id,
                    x="X",
                    y="Y",
                    bins=128,
                    bounds=[-3, 3, -3, 3],
                ),
            )
        ],
    )
    engine = Engine(store)

    def render():
        started = time.perf_counter()
        page = reports.render(
            doc,
            engine,
            reports.ReportRequest(
                revision=doc.revision, definition=definition, validate_sources=True
            ),
        )
        assert page["exportable"], page["issues"]
        layer = page["manifest"]["elements"][0]["layers"][0]
        assert layer["population_count"] == expected
        visible = int(
            (
                (values[:, 0] >= 0)
                & (values[:, 0] < 3)
                & (values[:, 1] >= -3)
                & (values[:, 1] <= 3)
            ).sum()
        )
        assert layer["visible_count"] == visible and sum(layer["counts"]) == visible
        return time.perf_counter() - started, page

    cold, page = render()
    warm = []
    for step in range(5):
        definition.elements[0].x_mm = 12 + step
        elapsed, page = render()
        warm.append(elapsed)
    evidence = dict(
        status="passed",
        validated_at=datetime.now(UTC).isoformat(),
        events=len(values),
        independent_population_count=expected,
        bins=128,
        source_hashes_rechecked=True,
        cold_seconds=cold,
        arrangement_seconds=warm,
        svg_bytes=len(page["svg"].encode()),
        data_hash=page["manifest"]["data_hash"],
    )
    (root / "artifacts/report-benchmark.json").write_text(json.dumps(evidence, indent=2))
    print(json.dumps(evidence, indent=2))
finally:
    store.close()
