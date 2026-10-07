"""Measured gate-presentation costs on an independently counted, complete event grid."""

import hashlib
import json
import statistics
import tempfile
import time
from pathlib import Path

import numpy as np
from cytoforge.models import (
    Channel,
    Gate,
    GraphOptions,
    PlotDefinition,
    Sample,
    ThreeDView,
    Workspace,
)
from cytoforge.plotting import plot_payload
from cytoforge.report_plots import figure
from cytoforge.science import Engine, save_events
from cytoforge.store import Store
from cytoforge.three_dimensional import point_chunk, prepare

ROOT = Path(__file__).resolve().parents[1]


def checksum(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def measured(call, count=5):
    elapsed = []
    for _ in range(count):
        start = time.perf_counter()
        result = call()
        elapsed.append(time.perf_counter() - start)
    return result, statistics.median(elapsed)


def main():
    sources = {path: checksum(path) for path in (ROOT / "backend/cytoforge").rglob("*.py")}
    gate_style = dict(fill_opacity=0.4, line_width_px=4.5, show_labels=False)
    styles = {
        "axis_labels": dict(font_size_pt=12, font_family="serif", font_weight="bold"),
        "statistics": dict(font_size_pt=8, font_family="mono"),
        "legend": dict(font_size_pt=11, font_family="serif"),
    }
    with tempfile.TemporaryDirectory(
        prefix="graph-gate-style-benchmark-", dir=ROOT / ".tmp"
    ) as owned:
        store = Store(Path(owned))
        try:
            index = np.arange(65536)
            values = np.column_stack((index % 256, index // 256, (index % 16) ** 2)).astype(float)
            sample = Sample(
                name="Known complete grid",
                channels=[Channel(name=name) for name in "XYZ"],
                event_count=len(index),
            )
            workspace = Workspace(name="Owned gate benchmark", samples=[sample])
            workspace.gates = [
                Gate(
                    name="Grid with overlapping exclusions",
                    sample_id=sample.id,
                    kind="polygon",
                    x="X",
                    y="Y",
                    vertices=[(-0.5, -0.5), (255.5, -0.5), (255.5, 255.5), (-0.5, 255.5)],
                    holes=[
                        [(63.5, 63.5), (127.5, 63.5), (127.5, 127.5), (63.5, 127.5)],
                        [(95.5, 95.5), (159.5, 95.5), (159.5, 159.5), (95.5, 159.5)],
                    ],
                ),
                Gate(
                    name="Known range",
                    sample_id=sample.id,
                    kind="range",
                    x="X",
                    bounds=[63.5, 127.5],
                ),
            ]
            path = store.data_path(workspace.id, sample.id)
            sample.sha256 = save_events(path, values)
            workspace = store.create(workspace)
            initial = workspace.model_dump()
            raw_hash = checksum(path)
            engine = Engine(store)
            truth = ~(
                (
                    (values[:, 0] >= 64)
                    & (values[:, 0] <= 127)
                    & (values[:, 1] >= 64)
                    & (values[:, 1] <= 127)
                )
                | (
                    (values[:, 0] >= 96)
                    & (values[:, 0] <= 159)
                    & (values[:, 1] >= 96)
                    & (values[:, 1] <= 159)
                )
            )
            assert truth.sum() == 58368
            assert np.array_equal(engine.mask(workspace, sample, workspace.gates[0].id), truth)
            styled = GraphOptions(typography=styles, gate_style=gate_style).model_dump()
            timings = {}
            for mode in ["histogram", "cdf", "density"]:
                kwargs = dict(
                    x="X",
                    y="Y" if mode == "density" else None,
                    mode=mode,
                    bins=32,
                    bounds=[-0.5, 255.5] * (2 if mode == "density" else 1),
                )
                baseline = plot_payload(workspace, engine, sample.id, **kwargs)
                changed, seconds = measured(
                    lambda kwargs=kwargs: plot_payload(
                        workspace, engine, sample.id, graph_options=styled, **kwargs
                    )
                )
                assert changed == baseline and changed["count"] == changed["finite_count"] == 65536
                assert np.array_equal(
                    changed["counts"],
                    np.full(
                        (1024,) if mode == "density" else (32,), 64 if mode == "density" else 2048
                    ),
                )
                timings[f"{mode}_styled_cached_request_median_seconds"] = seconds
            camera = ThreeDView(z="Z")
            cloud = prepare(workspace, engine, sample.id, "X", "Y", camera)
            baseline = point_chunk(cloud).tobytes()
            changed, seconds = measured(
                lambda: prepare(
                    workspace, engine, sample.id, "X", "Y", camera, graph_options=styled
                )
            )
            assert changed[0] == cloud[0] and changed[0]["data_key"] == cloud[0]["data_key"]
            assert point_chunk(changed).tobytes() == baseline
            timings["three_d_styled_cached_request_median_seconds"] = seconds
            definition = PlotDefinition(
                sample_id=sample.id,
                x="X",
                bins=32,
                bounds=[-0.5, 255.5],
                graph_options=GraphOptions(typography=styles, gate_style=gate_style),
            )
            layers = [
                dict(
                    sample_id=sample.id,
                    source_sample_id=sample.id,
                    gate_id=None,
                    coordinate_gate_id=None,
                    color="#087e8b",
                    label="Known complete grid",
                    locked_control=False,
                )
            ]
            (svg, manifest), seconds = measured(
                lambda: figure(workspace, engine, definition, layers, 180, 100), count=3
            )
            assert manifest["layers"][0]["population_count"] == 65536
            assert manifest["layers"][0]["counts"] == [2048] * 32
            timings["styled_histogram_svg_median_seconds"] = seconds
            density_definition = definition.model_copy(
                update={"mode": "density", "y": "Y", "bounds": [-0.5, 255.5, -0.5, 255.5]}
            )
            (density_svg, density_manifest), seconds = measured(
                lambda: figure(workspace, engine, density_definition, layers, 180, 100), count=3
            )
            assert density_manifest["gate_style"]["fill_opacity"] == 0.4
            assert density_manifest["layers"][0]["counts"] == [64] * 1024
            assert 'clip-rule="evenodd"' in density_svg
            timings["styled_density_with_overlapping_holes_svg_median_seconds"] = seconds
            assert np.array_equal(engine.mask(workspace, sample, workspace.gates[0].id), truth)
            assert store.get(workspace.id).model_dump() == initial and checksum(path) == raw_hash
        finally:
            store.close()
    assert all(checksum(path) == checksum_before for path, checksum_before in sources.items())
    record = dict(
        status="passed",
        events=65536,
        dimensions=3,
        independent_histogram_bin_counts=2048,
        independent_density_bin_counts=64,
        independent_gate_events=58368,
        gate_masks_unchanged=True,
        scientific_payloads_unchanged=True,
        three_d_buffers_and_keys_unchanged=True,
        scratch_workspace_and_acquisition_bytes_unchanged=True,
        generated_inputs_removed=True,
        backend_sources_unchanged=True,
        svg_bytes=len(svg.encode()),
        timings=timings,
        scope="Cached engine requests and real styled publication SVG; no HTTP/native/GPU timing",
    )
    (ROOT / "artifacts/graph-gate-style-benchmark.json").write_text(
        json.dumps(record, indent=2) + "\n"
    )
    print(json.dumps(record))


if __name__ == "__main__":
    main()
