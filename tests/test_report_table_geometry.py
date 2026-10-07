"""Physical page boundaries and cell styling checked against independently known values."""

import hashlib
import io
import json
import zipfile
from collections import Counter
from xml.etree import ElementTree as ET

import numpy as np
import pytest
from cytoforge import report_table_geometry, report_templates, reports
from cytoforge.models import (
    LayoutDefinition,
    ReportElement,
    ReportTableCellStyle,
    ReportTableGeometry,
    TableColumn,
    new_id,
)
from cytoforge.science import save_events
from cytoforge.store import ConflictError
from pydantic import ValidationError

from tests.test_report_tables import cohort, pages


@pytest.fixture
def geometry_data(store):
    return cohort(store)


def definition(workspace, **overrides):
    return LayoutDefinition(
        name="Known table geometry",
        show_header=False,
        show_footer=False,
        elements=[
            ReportElement(
                kind="table",
                table_id=workspace.tables[0].id,
                iterate=False,
                auto_paginate=True,
                width_mm=180,
                height_mm=60,
                row_count=200,
                columns_per_page=16,
                table_geometry=ReportTableGeometry(
                    column_width_mm=35,
                    row_height_mm=10,
                    header_height_mm=10,
                    row_heights_mm={1: 20, 4: 25},
                ),
                **overrides,
            )
        ],
    )


def table_panel(page):
    return page["manifest"]["elements"][0]


def cell(page, row, column):
    tree = ET.fromstring(page["svg"])
    return next(
        node
        for node in tree.findall(".//{*}svg")
        if node.get("data-table-row") == str(row) and node.get("data-table-column") == str(column)
    )


def test_unequal_row_and_column_pages_preserve_every_known_cell_exactly_once(geometry_data):
    workspace, engine = geometry_data
    before = workspace.model_dump_json()
    layout = definition(workspace)
    plan, output = pages(workspace, engine, layout)
    assert plan["page_count"] == 6
    paging = plan["iterations"][0]["tables"][layout.elements[0].id]
    assert [(frame["start"], frame["count"]) for frame in paging["row_windows"]] == [
        (0, 3),
        (3, 2),
        (5, 3),
    ]
    assert [(frame["start"], frame["count"]) for frame in paging["column_windows"]] == [
        (0, 3),
        (3, 2),
    ]
    occurrences = Counter()
    for page in output:
        panel = table_panel(page)
        assert [item["id"] for item in panel["fixed_columns"]] == ["sample", "population"]
        assert [frame["width_mm"] for frame in panel["physical_geometry"]["columns"]] == [35] * len(
            panel["physical_geometry"]["columns"]
        )
        assert sum(frame["height_mm"] for frame in panel["physical_geometry"]["rows"]) <= 40
        for row in panel["rows"]:
            index = next(
                i for i, sample in enumerate(workspace.samples) if sample.name == row["sample"]
            )
            for column in panel["columns"]:
                occurrences[index, column["id"]] += 1
                if column["name"] in {"Signal", "Events"}:
                    assert row["values"][column["id"]] == index + 1
                elif column["name"] == "Zero":
                    assert row["values"][column["id"]] == 0
    assert len(occurrences) == 8 * 5 and set(occurrences.values()) == {1}
    assert workspace.model_dump_json() == before
    for sample in workspace.samples:
        assert (
            hashlib.sha256(engine.store.data_path(workspace.id, sample.id).read_bytes()).hexdigest()
            == sample.sha256
        )


def test_custom_frames_respect_start_row_and_measure_offsets(geometry_data):
    workspace, engine = geometry_data
    layout = definition(workspace, row_start=1, column_start=2)
    plan, output = pages(workspace, engine, layout)
    assert plan["page_count"] == 3
    assert [table_panel(page)["offset"] for page in output] == [1, 4, 6]
    assert [table_panel(page)["displayed_rows"] for page in output] == [3, 2, 2]
    assert all(table_panel(page)["column_offset"] == 2 for page in output)
    assert [row["sample"] for page in output for row in table_panel(page)["rows"]] == [
        sample.name for sample in workspace.samples[1:]
    ]


def test_unset_column_widths_share_remaining_space_and_preserve_leading_widths(geometry_data):
    workspace, engine = geometry_data
    layout = definition(workspace)
    layout.elements[0].table_geometry = ReportTableGeometry(
        column_widths_mm={0: 50, 1: 20},
        row_height_mm=10,
        header_height_mm=10,
    )
    _, output = pages(workspace, engine, layout)
    physical = table_panel(output[0])["physical_geometry"]["columns"]
    assert [frame["width_mm"] for frame in physical] == [50, 20, 22, 22, 22, 22, 22]
    assert [frame["left"] for frame in physical] == [0, 50, 70, 92, 114, 136, 158]


def test_cell_fonts_expand_one_logical_row_across_all_horizontal_continuations(geometry_data):
    workspace, engine = geometry_data
    layout = definition(workspace)
    layout.elements[0].table_geometry = ReportTableGeometry(
        column_width_mm=35,
        header_height_mm=10,
        cell_styles=[
            ReportTableCellStyle(
                row=3,
                column=6,
                font_size_pt=18,
                align="right",
                vertical_align="middle",
                color="#123456",
                background="#ffcc00",
                font_family="monospace",
                font_style="italic",
            )
        ],
    )
    plan, output = pages(workspace, engine, layout)
    assert plan["page_count"] == 6
    heights = [
        frame
        for page in output
        for frame in table_panel(page)["physical_geometry"]["rows"]
        if frame["index"] == 3
    ]
    assert len(heights) == 2
    assert [frame["height_mm"] for frame in heights] == pytest.approx([17.145, 17.145])
    node = cell(output[3], 3, 6)
    label = node.find("{*}text")
    assert label.text == "0"
    assert label.get("font-size") == "6.35"
    assert label.get("font-family") == "monospace" and label.get("font-style") == "italic"
    assert label.get("fill") == "#123456" and label.get("text-anchor") == "end"
    assert float(label.get("x")) == pytest.approx(33.6)
    assert float(label.get("y")) == pytest.approx(12.22375)
    assert table_panel(output[3])["rows"][0]["values"][workspace.tables[0].columns[4].id] == 0
    assert "#ffcc00" in output[3]["svg"]


def test_heading_cell_styles_use_physical_points_and_repeated_columns(geometry_data):
    workspace, engine = geometry_data
    layout = definition(workspace)
    layout.elements[0].height_mm = 80
    layout.elements[0].table_geometry = ReportTableGeometry(
        column_width_mm=35,
        row_height_mm=10,
        cell_styles=[
            ReportTableCellStyle(
                column=0,
                font_size_pt=14,
                font_family="serif",
                font_weight=800,
                font_style="italic",
                text_decoration="underline",
                background="#123456",
            )
        ],
    )
    _, output = pages(workspace, engine, layout)
    for page in output:
        label = cell(page, "header", 0).find("{*}text")
        assert label.text == "Sample"
        assert float(label.get("font-size")) == pytest.approx(14 * 25.4 / 72)
        assert label.get("font-family") == "serif" and label.get("font-weight") == "800"
        assert label.get("font-style") == "italic" and label.get("text-decoration") == "underline"
        assert table_panel(page)["physical_geometry"]["header_height_mm"] == pytest.approx(
            14 * 25.4 / 72 * 1.35 * 3
        )


def test_padding_and_bottom_alignment_fit_inside_explicit_cell_geometry(geometry_data):
    workspace, engine = geometry_data
    layout = definition(workspace)
    layout.elements[0].height_mm = 80
    layout.elements[0].table_geometry = ReportTableGeometry(
        column_width_mm=35,
        row_height_mm=15,
        header_height_mm=15,
        padding_x_mm=1,
        padding_y_mm=2,
        align="right",
        vertical_align="bottom",
    )
    _, output = pages(workspace, engine, layout)
    node = cell(output[0], 0, 0)
    label = node.find("{*}text")
    assert float(node.get("x")) == 1 and float(node.get("width")) == 33
    assert float(node.get("height")) == 15 and float(node.get("y")) == 15
    assert float(label.get("x")) == 33 and label.get("text-anchor") == "end"
    assert float(label.get("y")) == pytest.approx(15 - 2 - 10 * 25.4 / 72 * 0.8 * 0.1)


@pytest.mark.parametrize("view", ["data", "pivot", "comparisons"])
def test_custom_widths_and_heights_do_not_change_pivot_or_comparison_results(geometry_data, view):
    workspace, engine = geometry_data
    layout = definition(workspace, table_view=view)
    _, styled = pages(workspace, engine, layout)
    original = layout.elements[0].table_geometry
    layout.elements[0].table_geometry = None
    _, plain = pages(workspace, engine, layout)

    def truth(output):
        return {
            (
                row.get("id")
                or row.get("sample_id")
                or row.get("column_id")
                or json.dumps(row.get("group"), sort_keys=True),
                column["id"],
            ): row
            for page in output
            for row in table_panel(page)["rows"]
            for column in table_panel(page)["columns"]
        }

    assert truth(styled) == truth(plain)
    assert all(
        table_panel(page)["snapshot_sha256"] == table_panel(plain[0])["snapshot_sha256"]
        for page in styled
    )
    layout.elements[0].table_geometry = original


def test_cell_colour_cannot_mask_undefined_values_or_source_errors(geometry_data):
    workspace, engine = geometry_data
    undefined = TableColumn(name="Undefined", kind="formula", expression="0 / 0")
    workspace.tables[0].columns.append(undefined)
    layout = definition(workspace)
    layout.elements[0].table_geometry = ReportTableGeometry(
        cell_styles=[ReportTableCellStyle(row=0, column=7, color="#ffffff")],
    )
    _, output = pages(workspace, engine, layout)
    label = cell(output[0], 0, 7).find("{*}text")
    assert label.get("fill") == "#b03c35"
    assert table_panel(output[0])["rows"][0]["values"][undefined.id] is None
    assert any(
        issue["column_id"] == undefined.id for issue in output[0]["issues"] if "column_id" in issue
    )


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"column_width_mm": 100}, "cannot fit"),
        ({"row_heights_mm": {0: 100}}, "row 1"),
        ({"header_height_mm": 55}, "heading"),
        ({"column_widths_mm": {0: 1}}, "padding"),
    ],
)
def test_impossible_geometry_is_visible_and_blocks_current_exports(geometry_data, changes, message):
    workspace, engine = geometry_data
    layout = definition(workspace)
    layout.elements[0].table_geometry = ReportTableGeometry(**changes)
    plan = reports.plan(workspace, layout, engine)
    assert not plan["exportable"]
    assert any(message in item["message"] for item in plan["iterations"][0]["issues"])
    page = reports.render(
        workspace, engine, reports.ReportRequest(revision=workspace.revision, definition=layout)
    )
    assert not page["exportable"]


def test_geometry_changes_invalidate_review_but_keep_the_cohort_snapshot(geometry_data):
    workspace, engine = geometry_data
    layout = definition(workspace)
    reviewed = reports.plan(workspace, layout, engine)
    layout.elements[0].table_geometry.row_height_mm = 12
    changed = reports.plan(workspace, layout, engine)
    assert changed["review_hash"] != reviewed["review_hash"]
    assert (
        changed["iterations"][0]["tables"][layout.elements[0].id]["snapshot_sha256"]
        == reviewed["iterations"][0]["tables"][layout.elements[0].id]["snapshot_sha256"]
    )
    with pytest.raises(ConflictError):
        reports.render(
            workspace,
            engine,
            reports.ReportRequest(
                revision=workspace.revision, definition=layout, review_hash=reviewed["review_hash"]
            ),
        )


def test_template_save_import_undo_and_redo_preserve_sparse_cell_styles(geometry_data):
    workspace, engine = geometry_data
    layout = definition(workspace)
    layout.elements[0].table_geometry.cell_styles = [
        ReportTableCellStyle(row=4, column=3, background="#ffcc00", align="right")
    ]
    template = report_templates.parsed(
        json.dumps(
            report_templates.exported(
                workspace,
                report_templates.TemplateExport(revision=workspace.revision, definition=layout),
            )
        ).encode()
    )
    request = report_templates.TemplateImport(
        revision=workspace.revision, id=new_id(), template=template, name="Imported cell geometry"
    )
    preview = report_templates.preview(workspace, request, engine)
    assert preview["can_apply"]
    request.review_hash = preview["review_hash"]
    imported = report_templates.apply(engine.store, workspace.id, request, engine)
    saved = engine.store.get(workspace.id).layouts[0]
    assert (
        saved.elements[0].table_geometry.model_dump()
        == layout.elements[0].table_geometry.model_dump()
    )
    undone = engine.store.move_history(workspace.id, -1, imported.revision)
    assert not undone.layouts
    redone = engine.store.move_history(workspace.id, 1, undone.revision)
    assert redone.layouts[0].model_dump() == saved.model_dump()


@pytest.mark.parametrize(
    "invalid",
    [
        {"column_widths_mm": {"01": 10}},
        {"column_widths_mm": {"-1": 10}},
        {"column_widths_mm": {True: 10}},
        {"column_widths_mm": None},
        {"row_heights_mm": {50000: 10}},
        {"column_widths_mm": {516: 10}},
        {"row_height_mm": 0},
        {"column_width_mm": float("nan")},
        {"cell_styles": [{"column": 0, "row": True}]},
        {"cell_styles": [{"column": 0, "color": "url(https://example.test)"}]},
        {"cell_styles": [{"column": 0, "value": 999}]},
        {"cell_styles": [{"column": 0}, {"column": 0, "row": None}]},
    ],
)
def test_invalid_or_ambiguous_cell_geometry_is_rejected(invalid):
    with pytest.raises(ValidationError):
        ReportTableGeometry.model_validate(invalid)


def test_legacy_serialization_omits_unused_geometry_and_round_trips_canonical_indices():
    element = ReportElement(kind="table", table_id=new_id())
    assert "table_geometry" not in element.model_dump()
    geometry = ReportTableGeometry.model_validate(
        {
            "column_widths_mm": {"1": 35},
            "row_heights_mm": {"0": 20},
            "cell_styles": [{"column": 0, "background": "#ffcc00"}],
        }
    )
    assert (
        ReportTableGeometry.model_validate_json(geometry.model_dump_json()).model_dump()
        == geometry.model_dump()
    )
    assert geometry.cell_styles[0].model_dump() == {"column": 0, "background": "#ffcc00"}
    with pytest.raises(ValidationError, match="requires a report table"):
        ReportElement(kind="text", table_geometry=geometry)


def test_layout_cache_is_immutable_and_reuses_physical_frames(geometry_data, monkeypatch):
    workspace, engine = geometry_data
    layout = definition(workspace)
    element = layout.elements[0]
    element.width_mm = 181
    from cytoforge import report_tables

    data = report_tables.dataset(
        workspace, engine, element, {"mapping": {}, "sample_ids": []}, layout
    )
    calls = []
    original = report_table_geometry.build_layout

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(report_table_geometry, "build_layout", counted)
    first = report_table_geometry.layout(data, element, 60)
    first["row_windows"][0]["count"] = 999
    element.x_mm = 50
    second = report_table_geometry.layout(data, element, 60)
    assert len(calls) == 1 and second["row_windows"][0]["count"] == 3
    element.table_geometry.row_height_mm = 12
    third = report_table_geometry.layout(data, element, 60)
    assert len(calls) == 2 and third["row_windows"] != second["row_windows"]


def test_unused_positions_are_reviewed_without_affecting_current_geometry(geometry_data):
    workspace, engine = geometry_data
    layout = definition(workspace)
    plan, original = pages(workspace, engine, layout)
    geometry = layout.elements[0].table_geometry
    geometry.column_widths_mm[515] = 1200
    geometry.row_heights_mm[49999] = 1200
    geometry.cell_styles.append(ReportTableCellStyle(column=515, font_size_pt=144))
    changed, output = pages(workspace, engine, layout)
    assert changed["page_count"] == plan["page_count"]
    assert any(
        "formatting positions" in item["message"] for item in changed["iterations"][0]["issues"]
    )
    assert [table_panel(page)["physical_geometry"] for page in output] == [
        table_panel(page)["physical_geometry"] for page in original
    ]
    assert any("formatting positions" in item["message"] for item in output[0]["issues"])


def test_zero_room_cells_report_invisibility_and_preserve_complete_values(geometry_data):
    workspace, engine = geometry_data
    layout = definition(workspace)
    layout.elements[0].table_geometry = ReportTableGeometry(row_height_mm=0.5, header_height_mm=0.5)
    _, output = pages(workspace, engine, layout)
    panel = table_panel(output[0])
    assert panel["physical_geometry"]["invisible_text_cells"] > 0
    assert any("no room for text" in issue["message"] for issue in output[0]["issues"])
    assert [row["values"][workspace.tables[0].columns[2].id] for row in panel["rows"]] == list(
        range(1, 9)
    )


def test_geometry_page_bound_fails_before_allocating_an_unbounded_plan(geometry_data):
    workspace, _ = geometry_data
    element = definition(workspace).elements[0]
    element.row_count = 1
    data = dict(
        rows=[{} for _ in range(2050)],
        columns=[{"id": "mean", "name": "Mean"}],
        fixed_columns=[{"id": "sample", "name": "Sample"}],
        snapshot_sha256="bounded geometry test",
    )
    with pytest.raises(ValueError, match="1,024"):
        report_table_geometry.layout(data, element, 60)


def test_cross_workspace_cell_styles_render_destination_values_and_mapped_measures(store):
    source, _ = cohort(store)
    destination, engine = cohort(store)
    for index, sample in enumerate(destination.samples):
        sample.sha256 = save_events(
            store.data_path(destination.id, sample.id), np.full((index + 1, 1), index + 101.0)
        )
    destination = store.mutate(
        destination.id,
        "Known destination signals",
        lambda doc: setattr(doc, "samples", destination.samples),
        destination.revision,
    )
    layout = definition(source, column_ids=[column.id for column in source.tables[0].columns[2:]])
    layout.elements[0].table_geometry.cell_styles = [
        ReportTableCellStyle(row=2, column=2, background="#ffcc00", align="right")
    ]
    template = report_templates.exported(
        source, report_templates.TemplateExport(revision=source.revision, definition=layout)
    )
    request = report_templates.TemplateImport(
        revision=destination.revision,
        id=new_id(),
        name="Destination styled cells",
        template=template,
    )
    preview = report_templates.preview(destination, request, engine)
    assert preview["can_apply"], preview["issues"]
    imported = LayoutDefinition.model_validate(preview["definition"])
    assert imported.elements[0].table_id == destination.tables[0].id
    assert imported.elements[0].column_ids == [
        column.id for column in destination.tables[0].columns[2:]
    ]
    _, output = pages(destination, engine, imported)
    row = next(
        row
        for page in output
        for row in table_panel(page)["rows"]
        if row["sample"] == destination.samples[2].name
    )
    assert row["values"][destination.tables[0].columns[2].id] == 103
    styled = next(
        page
        for page in output
        if any(frame["index"] == 2 for frame in table_panel(page)["physical_geometry"]["rows"])
    )
    assert cell(styled, 2, 2).find("{*}text").text == "103.00"


def test_api_reviews_exports_all_geometry_pages_and_rejects_stale_or_bad_cells(client):
    workspace, engine = cohort(client.app.state.store)
    layout = definition(workspace)
    body = dict(revision=workspace.revision, definition=layout.model_dump(mode="json"))
    base = f"/api/workspaces/{workspace.id}"
    response = client.post(base + "/reports/plan", json=body)
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["page_count"] == 6
    archive = client.post(
        base + "/reports/export", json={**body, "format": "zip", "review_hash": plan["review_hash"]}
    )
    assert archive.status_code == 200, archive.text
    with zipfile.ZipFile(io.BytesIO(archive.content)) as package:
        manifest = json.loads(package.read("manifest.json"))
        assert len(manifest["pages"]) == 6
        assert all(
            page["elements"][0]["physical_geometry"]["header_height_mm"] == 10
            for page in manifest["pages"]
        )
    body["definition"]["elements"][0]["table_geometry"]["row_height_mm"] = 12
    stale = client.post(base + "/reports/render", json={**body, "review_hash": plan["review_hash"]})
    assert stale.status_code == 409, stale.text
    body["definition"]["elements"][0]["table_geometry"]["cell_styles"] = [
        {"column": 0, "value": 999}
    ]
    malformed = client.post(base + "/reports/plan", json=body)
    assert malformed.status_code == 422, malformed.text
    body["definition"]["elements"][0]["table_geometry"] = {"column_widths_mm": None}
    wrong_type = client.post(base + "/reports/plan", json=body)
    assert wrong_type.status_code == 422, wrong_type.text
