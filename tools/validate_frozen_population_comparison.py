"""Run real frozen comparison workers through the scheduler without an HTTP listener."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import multiprocessing as mp
import os
import sys
import tempfile
import time
from multiprocessing import resource_tracker, spawn
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import numpy as np
from cytoforge import comparison_storage, jobs, population_comparison, report_templates, reports
from cytoforge.models import (
    AnalysisInput,
    Channel,
    ComparisonParameter,
    Gate,
    GateDimension,
    LayoutDefinition,
    PlateDefinition,
    PlateGeometry,
    PopulationComparisonRequest,
    PopulationComparisonResult,
    ReportElement,
    ReportTableCellStyle,
    ReportTableGeometry,
    Sample,
    TableColumn,
    TableDefinition,
    Workspace,
    new_id,
)
from cytoforge.population_statistics import probability_tree
from cytoforge.science import Engine, save_events
from cytoforge.store import Store

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def frozen_worker(directory):
    # This small probe delegates to the real worker imported from the frozen engine.
    provenance = {
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "module": population_comparison.__file__,
        "modules": {
            "comparison_storage": comparison_storage.__file__,
            "jobs": jobs.__file__,
            "population_comparison": population_comparison.__file__,
            "report_templates": report_templates.__file__,
            "models": sys.modules["cytoforge.models"].__file__,
            "report_gate_style": sys.modules["cytoforge.report_gate_style"].__file__,
            "report_backgates": sys.modules["cytoforge.report_backgates"].__file__,
            "numpy": np.__file__,
            "scipy": sys.modules["scipy"].__file__,
        },
        "import_paths": sys.path,
    }
    (Path(directory) / "worker-runtime.json").write_text(json.dumps(provenance))
    failure = Path(directory) / "storage-failure.json"
    if failure.exists():
        kind = json.loads(failure.read_text())["kind"]
        if kind == "space":
            comparison_storage.shutil.disk_usage = lambda path: SimpleNamespace(
                free=comparison_storage.DISK_HEADROOM_BYTES
            )
        elif kind == "quota":

            def fail_allocation(*args):
                raise OSError(getattr(errno, "EDQUOT", errno.ENOSPC), "Injected quota failure")

            comparison_storage.os.posix_fallocate = fail_allocation
        else:
            raise ValueError("Unknown owned storage failure probe")
    population_comparison.run_population_comparison(directory)
    result_path = Path(directory) / "result.json"
    if result_path.exists():
        frozen_template_probe(Path(directory))


def frozen_template_probe(directory):
    payload = json.loads((directory / "input.json").read_text())
    store = Store(Path(payload["data_dir"]))
    try:
        workspace = store.get(payload["workspace"]["id"])
        before = workspace.model_dump()
        result = PopulationComparisonResult.model_validate_json(
            (directory / "result.json").read_text()
        )
        workspace.comparison_results.append(result)
        count_column = TableColumn(name="Events", kind="statistic", statistic="count", decimals=0)
        table = TableDefinition(name="Frozen known counts", columns=[count_column])
        workspace.tables.append(table)
        plate = PlateDefinition(
            name="Frozen custom plate",
            format="custom",
            geometry=PlateGeometry(rows=2, columns=3),
            assignments={
                "A01": [workspace.samples[0].id],
                "B03": [workspace.samples[1].id],
            },
        )
        workspace.plates.append(plate)
        source = result.request.inputs[0]
        definition = LayoutDefinition(
            name="Frozen reusable comparison",
            elements=[
                ReportElement(
                    kind="population_comparison",
                    result_id=result.id,
                    sample_id=source.sample_id,
                    gate_id=source.gate_id,
                    comparison_parameter_id=result.request.parameters[0].id,
                    iterate=False,
                    follow_replacement=False,
                    title="Frozen styled caption",
                    font_family="serif",
                    font_weight=700,
                    font_style="italic",
                    text_decoration="underline",
                    line_spacing=2,
                ),
                ReportElement(
                    kind="text",
                    text="Frozen styled annotation\nSecond line",
                    y_mm=140,
                    width_mm=150,
                    height_mm=40,
                    font_family="serif",
                    font_weight=600,
                    font_style="italic",
                    text_decoration="underline",
                    line_spacing=2,
                ),
                ReportElement(
                    kind="table",
                    table_id=table.id,
                    iterate=False,
                    y_mm=200,
                    width_mm=180,
                    height_mm=75,
                    table_geometry=ReportTableGeometry(
                        column_widths_mm={0: 70, 1: 55, 2: 55},
                        row_height_mm=9,
                        header_height_mm=20,
                        row_heights_mm={1: 18},
                        cell_styles=[
                            ReportTableCellStyle(
                                row=1,
                                column=2,
                                font_size_pt=12,
                                font_weight=600,
                                color="#123456",
                                background="#ffcc00",
                                align="right",
                            )
                        ],
                    ),
                    font_family="monospace",
                    font_weight=800,
                    font_style="italic",
                    text_decoration="underline",
                    line_spacing=2,
                ),
            ],
        )
        template = report_templates.exported(
            workspace,
            report_templates.TemplateExport(revision=workspace.revision, definition=definition),
        )
        parsed = report_templates.parsed(json.dumps(template).encode())
        request = report_templates.TemplateImport(
            revision=workspace.revision, id=new_id(), name="Frozen bound figure", template=parsed
        )
        engine = Engine(store)
        review = report_templates.preview(workspace, request, engine)
        assert review["can_apply"], review["issues"]
        imported = LayoutDefinition.model_validate(review["definition"])
        page = reports.render(
            workspace,
            engine,
            reports.ReportRequest(
                revision=workspace.revision, definition=imported, validate_sources=True
            ),
        )
        assert page["exportable"], page["issues"]
        panel = page["manifest"]["elements"][0]
        assert panel["result_id"] == result.id and panel["sample_id"] == source.sample_id
        assert page["manifest"]["definition"]["template_origin"]["template_sha256"] == parsed.sha256
        labels = ET.fromstring(page["svg"]).findall(".//{*}text")
        for value, weight in [
            ("Frozen styled caption", "700"),
            ("Frozen styled annotation", "600"),
        ]:
            label = next(label for label in labels if label.text == value)
            assert label.get("font-family") == "serif"
            assert label.get("font-weight") == weight
            assert label.get("font-style") == "italic"
            assert label.get("text-decoration") == "underline"
        table_panel = page["manifest"]["elements"][2]
        expected_counts = [sample.event_count for sample in workspace.samples]
        assert [row["values"][count_column.id] for row in table_panel["rows"]] == expected_counts
        table_label = next(label for label in labels if label.text == "Events")
        assert table_label.get("font-family") == "monospace"
        assert table_label.get("font-weight") == "800"
        assert table_label.get("font-style") == "italic"
        physical = table_panel["physical_geometry"]
        assert [column["width_mm"] for column in physical["columns"]] == [70, 55, 55]
        assert [row["height_mm"] for row in physical["rows"]] == [9, 18, 9][: len(expected_counts)]
        styled = next(
            node
            for node in ET.fromstring(page["svg"]).findall(".//{*}svg")
            if node.get("data-table-row") == "1" and node.get("data-table-column") == "2"
        )
        label = styled.find("{*}text")
        assert label.text == str(expected_counts[1])
        assert label.get("fill") == "#123456" and label.get("font-weight") == "600"
        assert label.get("text-anchor") == "end"
        assert abs(float(label.get("font-size")) - 12 * 25.4 / 72) < 1e-9
        plate_page = reports.render(
            workspace,
            engine,
            reports.ReportRequest(
                revision=workspace.revision,
                definition=LayoutDefinition(
                    name="Frozen custom plate report",
                    elements=[
                        ReportElement(
                            kind="plate",
                            plate_id=plate.id,
                            iterate=False,
                            width_mm=180,
                            height_mm=130,
                        )
                    ],
                ),
                validate_sources=True,
            ),
        )
        assert plate_page["exportable"], plate_page["issues"]
        plate_data = plate_page["manifest"]["elements"][0]["data"]
        assert (plate_data["rows"], plate_data["columns"]) == (2, 3)
        assert len(plate_data["wells"]) == 6
        assert plate_data["wells"][0]["values"][plate.columns[0].id] == expected_counts[0]
        assert plate_data["wells"][-1]["values"][plate.columns[0].id] == expected_counts[1]
        from cytoforge.models import GraphOptions, PlotDefinition, ThreeDView

        graph_styles = {
            "axis_labels": dict(
                font_size_pt=12,
                font_family="serif",
                font_weight="bold",
                font_style="italic",
                color="#112233",
            ),
            "tick_labels": dict(font_size_pt=9, font_family="mono", color="#223344"),
            "statistics": dict(
                font_size_pt=8, font_family="mono", font_weight="bold", color="#445566"
            ),
            "legend": dict(font_size_pt=11, font_family="serif", color="#556677"),
            "title": dict(
                font_size_pt=14, font_family="serif", font_weight="bold", color="#667788"
            ),
        }
        gate_style = dict(
            fill_opacity=0.4, fill_color="#d12345", line_width_px=4.5, show_labels=False
        )
        sample_id = workspace.samples[0].id
        workspace.gates.extend(
            [
                Gate(
                    name="Frozen range label",
                    sample_id=sample_id,
                    kind="range",
                    x="P0",
                    bounds=[-0.25, 0.25],
                    color="#135791",
                ),
                Gate(
                    name="Frozen polygon label",
                    sample_id=sample_id,
                    kind="polygon",
                    x="P0",
                    y="P1",
                    vertices=[(-1, -1), (1, -1), (1, 1), (-1, 1)],
                    holes=[
                        [(-0.3, -0.3), (0.2, -0.3), (0.2, 0.2), (-0.3, 0.2)],
                        [(-0.2, -0.2), (0.3, -0.2), (0.3, 0.3), (-0.2, 0.3)],
                    ],
                    color="#135791",
                ),
            ]
        )
        graph_modes = ["histogram", "density"]
        if len(workspace.samples[0].channels) >= 3:
            graph_modes.append("3d")
            workspace.gates.append(
                Gate(
                    name="Frozen box label",
                    sample_id=sample_id,
                    kind="hyperrectangle",
                    color="#135791",
                    dimensions=[
                        GateDimension(channel=f"P{i}", minimum=-0.5, maximum=0.5) for i in range(3)
                    ],
                )
            )
        backgate_id = next(g.id for g in workspace.gates if g.name == "Frozen range label")
        for mode in graph_modes:
            graph = PlotDefinition(
                sample_id=workspace.samples[0].id,
                x="P0",
                y="P1" if mode != "histogram" else None,
                mode=mode,
                three_d=ThreeDView(z="P2") if mode == "3d" else None,
                graph_options=GraphOptions(typography=graph_styles, gate_style=gate_style),
                backgate_id=backgate_id,
            )
            graph_page = reports.render(
                workspace,
                engine,
                reports.ReportRequest(
                    revision=workspace.revision,
                    validate_sources=True,
                    definition=LayoutDefinition(
                        name="Frozen graph fonts",
                        elements=[
                            ReportElement(
                                kind="plot",
                                plot=graph,
                                title="Frozen graph title",
                                width_mm=180,
                                height_mm=120,
                            )
                        ],
                    ),
                ),
            )
            assert graph_page["exportable"], graph_page["issues"]
            graph_data = graph_page["manifest"]["elements"][0]
            assert graph_data["layers"][0]["population_count"] == expected_counts[0]
            assert graph_data["typography_units"] == "pt"
            assert graph_data["gate_style"] == gate_style
            raw = engine.raw(workspace, workspace.samples[0])
            columns = 3 if mode == "3d" else 1 if mode == "histogram" else 2
            expected_backgate = int(
                np.count_nonzero(
                    np.all(np.isfinite(raw[:, :columns]), axis=1)
                    & (raw[:, 0] >= -0.25)
                    & (raw[:, 0] < 0.25)
                )
            )
            assert graph_data["layers"][0]["backgate"]["count"] == expected_backgate
            assert backgate_id in graph_data["layers"][0]["source"]["populations"]
            graph_layout = LayoutDefinition.model_validate(graph_page["manifest"]["definition"])
            graph_template = report_templates.parsed(
                json.dumps(
                    report_templates.exported(
                        workspace,
                        report_templates.TemplateExport(
                            revision=workspace.revision, definition=graph_layout
                        ),
                    )
                ).encode()
            )
            graph_review = report_templates.preview(
                workspace,
                report_templates.TemplateImport(
                    revision=workspace.revision,
                    id=new_id(),
                    name="Frozen graph template",
                    template=graph_template,
                ),
                engine,
            )
            assert graph_review["can_apply"], graph_review["issues"]
            rebound = LayoutDefinition.model_validate(graph_review["definition"])
            assert rebound.elements[0].plot.graph_options.typography.model_dump() == graph_styles
            assert rebound.elements[0].plot.graph_options.gate_style.model_dump() == gate_style
            assert rebound.elements[0].plot.backgate_id == backgate_id
            root = ET.fromstring(graph_page["svg"])
            assert any(
                node.get("stroke") == "#135791" and node.get("stroke-width") == "3.375"
                for node in root.iter()
            )
            if mode == "density":
                assert sum(node.get("clip-rule") == "evenodd" for node in root.iter()) >= 2
            for color, face in [
                ("#112233", "DejaVuSerif-BoldItalic"),
                ("#223344", "DejaVuSansMono"),
                ("#445566", "DejaVuSansMono-Bold"),
                ("#556677", "DejaVuSerif"),
                ("#667788", "DejaVuSerif-Bold"),
            ]:
                groups = [node for node in root.iter() if node.get("fill") == color]
                assert any(
                    face
                    in (node.get("href", "") or node.get("{http://www.w3.org/1999/xlink}href", ""))
                    for group in groups
                    for node in group.iter()
                ), (mode, color, face)
            (directory / f"graph-{mode}.svg").write_text(graph_page["svg"])
        assert store.get(workspace.id).model_dump() == before
        (directory / "template-runtime.json").write_text(
            json.dumps(
                {
                    "module": report_templates.__file__,
                    "frozen": bool(getattr(sys, "frozen", False)),
                    "portable_file_verified": True,
                    "publication_typography_verified": True,
                    "table_geometry_verified": True,
                    "table_cell_style_verified": True,
                    "custom_plate_geometry_verified": True,
                    "custom_plate_report_values_verified": True,
                    "graph_typography_glyphs_verified": True,
                    "graph_typography_template_roundtrip_verified": True,
                    "graph_typography_modes": graph_modes,
                    "graph_gate_style_verified": True,
                    "graph_backgate_reports_verified": True,
                    "table_counts": expected_counts,
                    "reviewed_scientific_bindings": True,
                    "actual_artifact_rendered": True,
                    "manifest_preserves_template_provenance": True,
                    "original_workspace_unchanged": True,
                }
            )
        )
    finally:
        store.close()


def scenario(directory, arrays, dimensions, known_ens=None, storage_failure=None):
    store = Store(directory)
    manager = jobs.JobManager(store)
    try:
        samples = [
            Sample(
                name=f"Owned frozen reference {index + 1}",
                event_count=len(values),
                channels=[Channel(name=f"P{axis}") for axis in range(dimensions)],
            )
            for index, values in enumerate(arrays)
        ]
        workspace = Workspace(name="Owned frozen comparison reference", samples=samples)
        for sample, values in zip(samples, arrays, strict=True):
            sample.sha256 = save_events(store.data_path(workspace.id, sample.id), values)
        workspace = store.create(workspace)
        request = PopulationComparisonRequest(
            name="Frozen complete-event comparison",
            revision=workspace.revision,
            inputs=[AnalysisInput(sample_id=samples[-1].id)],
            controls=[AnalysisInput(sample_id=s.id) for s in samples[:-1]],
            parameters=[ComparisonParameter(channel=f"P{axis}") for axis in range(dimensions)],
            probability_bins=4 if known_ens is not None else 16,
            minimum_bin_events=1,
        )
        before = workspace.model_dump_json()
        identifier = manager.submit(workspace, request)["id"]
        if storage_failure:
            (manager.directory / identifier / "storage-failure.json").write_text(
                json.dumps({"kind": storage_failure})
            )
        manager.start()
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            job = manager.get(workspace, identifier)
            if job["status"] not in jobs.ACTIVE:
                break
            time.sleep(0.02)
        runtime = json.loads((manager.directory / identifier / "worker-runtime.json").read_text())
        assert runtime["frozen"], runtime
        assert (
            Path(runtime["module"]).resolve().is_relative_to(Path(runtime["executable"]).parent)
        ), runtime
        assert all(
            Path(path).resolve().is_relative_to(Path(runtime["executable"]).parent)
            for path in runtime["modules"].values()
        ), runtime
        assert all(
            ".venv" not in path and "/backend" not in path for path in runtime["import_paths"]
        ), runtime
        assert store.get(workspace.id).model_dump_json() == before
        assert all(digest(store.data_path(workspace.id, s.id)) == s.sha256 for s in samples)
        assert not (manager.directory / identifier / "comparison-scratch").exists()
        if storage_failure:
            assert job["status"] == "failed", job
            expected_error = "headroom" if storage_failure == "space" else "space or quota"
            assert expected_error in job["error"], job
            output = store.root / "comparisons" / workspace.id / f"{identifier}.npz"
            assert not output.exists()
            assert not output.with_suffix(".npz.partial").exists()
            return {
                "dimensions": dimensions,
                "selected_events": sum(len(v) for v in arrays),
                "injected_storage_failure": storage_failure,
                "job_status": job["status"],
                "error": job["error"],
                "no_result_or_partial_artifact_published": True,
                "original_workspace_and_event_bytes_unchanged": True,
                "temporary_storage_removed": True,
                "runtime": runtime,
            }
        assert job["status"] == "succeeded", job
        template_runtime = json.loads(
            (manager.directory / identifier / "template-runtime.json").read_text()
        )
        assert template_runtime["frozen"] and template_runtime["actual_artifact_rendered"]
        result = job["result"]
        complete = [v[np.all(np.isfinite(v), axis=1)] for v in arrays]
        reference = np.concatenate(complete[:-1])
        expected = probability_tree(reference, request.probability_bins, request.minimum_bin_events)
        assert result["joint_rows"][0]["probability"] == population_comparison.summary_probability(
            expected.compare(complete[-1])
        )
        if known_ens is not None:
            np.testing.assert_allclose(
                result["rows"][0]["metrics"]["ens_percent"], known_ens, rtol=1e-12, atol=0
            )
        return {
            "dimensions": dimensions,
            "selected_events": sum(len(v) for v in arrays),
            "finite_joint_events": sum(len(v) for v in complete),
            "exact_joint_score_matches_dense_reference": True,
            "known_ens_percent": known_ens,
            "original_workspace_and_event_bytes_unchanged": True,
            "temporary_storage_removed": True,
            "versions": result["versions"],
            "runtime": runtime,
            "report_template_runtime": template_runtime,
        }
    finally:
        manager.close()
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts/frozen-population-comparison-validation.json",
    )
    args = parser.parse_args()
    binary = args.engine.resolve()
    if not binary.is_relative_to(ROOT) or not binary.is_file():
        parser.error("Choose an existing engine binary inside this checkout")
    internal = binary.parent / "_internal"
    if not internal.is_dir():
        parser.error("This probe requires the complete PyInstaller onedir engine")
    output = args.output.resolve()
    if not output.is_relative_to(ROOT):
        parser.error("Validation output must stay inside this checkout")
    original_paths, original_pythonpath = sys.path[:], os.environ.pop("PYTHONPATH", None)
    original_command_line = spawn.get_command_line
    # The source driver keeps its normal resource tracker. Real worker children
    # receive the frozen parent's spawn protocol rather than Python's -c protocol.
    resource_tracker.getfd()

    def frozen_command_line(**kwargs):
        return [str(binary), "--multiprocessing-fork"] + [
            f"{key}={value!r}" for key, value in kwargs.items()
        ]

    spawn.get_command_line = frozen_command_line
    original_platform = jobs.PLATFORMS["population_comparison"]
    jobs.PLATFORMS["population_comparison"] = (*original_platform[:2], frozen_worker)
    # Spawn preparation copies the parent's paths. Keep the frozen child isolated
    # from this project's backend and Python environment, including distribution metadata.
    sys.path = [str(internal / "base_library.zip"), str(internal / "lib-dynload"), str(internal)]
    mp.set_executable(str(binary))
    try:
        with tempfile.TemporaryDirectory(prefix="frozen-comparison-", dir=ROOT / ".tmp") as owned:
            known = [
                np.c_[np.repeat([0, 1, 2], [4, 4, 2]), np.arange(10)],
                np.c_[np.repeat([0, 1, 2, 3], [2, 3, 1, 4]), np.arange(10)],
            ]
            cases = [scenario(Path(owned) / "known", known, 2, 44)]
            rng = np.random.default_rng(508)
            large = [rng.normal(size=(n, 64)) for n in [257, 137, 193]]
            large[0][0, 0], large[1][0, -1], large[2][0, 3] = np.nan, np.inf, -np.inf
            cases.append(scenario(Path(owned) / "maximum-dimensions", large, 64))
            for failure in ["space", "quota"]:
                cases.append(
                    scenario(Path(owned) / f"storage-{failure}", known, 2, storage_failure=failure)
                )
        evidence = {
            "status": "passed",
            "engine": str(binary.relative_to(ROOT)),
            "engine_sha256": digest(binary),
            "scope": (
                "Bundled Linux engine modules, real spawned comparison jobs and portable report "
                "template library/figure validation; "
                "HTTP listener, desktop renderer and installers remain separate"
            ),
            "source_import_paths_removed": True,
            "parent_driver": (
                "Source Python scheduler; actual child uses frozen-worker spawn arguments"
            ),
            "generated_test_data_removed": True,
            "source_sha256": {
                name: digest(ROOT / name)
                for name in [
                    "backend/cytoforge/comparison_storage.py",
                    "backend/cytoforge/jobs.py",
                    "backend/cytoforge/population_comparison.py",
                    "backend/cytoforge/report_templates.py",
                    "backend/cytoforge/models.py",
                    "backend/cytoforge/plates.py",
                    "backend/cytoforge/reports.py",
                    "backend/cytoforge/report_svg.py",
                    "backend/cytoforge/report_tables.py",
                    "backend/cytoforge/report_table_geometry.py",
                    "backend/cytoforge/report_cache.py",
                    "backend/cytoforge/report_graph_typography.py",
                    "backend/cytoforge/report_gate_style.py",
                    "backend/cytoforge/report_backgates.py",
                    "backend/cytoforge/science.py",
                    "backend/cytoforge/three_dimensional.py",
                    "backend/cytoforge/virtual_groups.py",
                    "backend/cytoforge/biology.py",
                    "backend/cytoforge/report_plots.py",
                    "backend/cytoforge/report_three_dimensional.py",
                    "backend/cytoforge/graph_views.py",
                    "tools/validate_frozen_population_comparison.py",
                ]
            },
            "cases": cases,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, indent=2) + "\n")
        print(f"Passed frozen workers: {output.relative_to(ROOT)}")
    finally:
        sys.path = original_paths
        if original_pythonpath is not None:
            os.environ["PYTHONPATH"] = original_pythonpath
        jobs.PLATFORMS["population_comparison"] = original_platform
        spawn.get_command_line = original_command_line
        mp.set_executable(sys.executable)


if __name__ == "__main__":
    main()
