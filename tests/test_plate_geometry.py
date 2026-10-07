"""Independent physical well grids, acquisition truth and reviewed custom plate workflows."""

import csv
import io
import json
from xml.etree import ElementTree as ET

import pytest
from cytoforge import plates, report_templates, reports
from cytoforge.models import (
    LayoutDefinition,
    PlateDefinition,
    PlateGeometry,
    ReportElement,
    TableColumn,
    plate_well,
)
from cytoforge.science import Engine
from cytoforge.store import ConflictError
from pydantic import ValidationError

from tests.test_plates import acquisition_workspace
from tests.test_report_templates import request_for
from tests.test_reports import page_for


def custom_plate(workspace):
    return PlateDefinition(
        name="Custom dose grid",
        format="custom",
        geometry=PlateGeometry(rows=2, columns=3),
        assignments={
            "A01": [workspace.samples[0].id, workspace.samples[2].id],
            "A03": [workspace.samples[1].id],
        },
        annotations={"A01": {"Dose": "0.5"}, "B03": {"Dose": "0", "Clear": None}},
        columns=[
            TableColumn(name="Events", statistic="count", decimals=0),
            TableColumn(name="Signal", statistic="median", channel="X"),
        ],
    )


@pytest.mark.parametrize(
    "format,shape",
    [
        (6, (2, 3)),
        (12, (3, 4)),
        (24, (4, 6)),
        (48, (6, 8)),
        (96, (8, 12)),
        (384, (16, 24)),
        (1536, (32, 48)),
    ],
)
def test_numbered_formats_have_complete_independently_known_grids(store, format, shape):
    workspace = acquisition_workspace(store)
    plate = PlateDefinition(format=format, assignments={"A01": [workspace.samples[0].id]})
    result = plates.evaluate(workspace, Engine(store), plate)
    assert (result["rows"], result["columns"]) == shape
    assert len(result["wells"]) == format
    assert len({w["well"] for w in result["wells"]}) == format
    assert result["wells"][-1]["well"] == plate_well(shape[0] - 1, shape[1] - 1)
    assert result["wells"][0]["values"][plate.columns[0].id] == 4
    assert "geometry" not in plate.model_dump()


@pytest.mark.parametrize("shape", [(1, 1), (2, 3), (3, 8), (96, 1), (1, 96), (16, 96)])
def test_custom_grid_positions_and_empty_wells_are_complete(store, shape):
    workspace = acquisition_workspace(store)
    plate = PlateDefinition(
        format="custom",
        geometry=PlateGeometry(rows=shape[0], columns=shape[1]),
        assignments={"A01": [workspace.samples[2].id]},
    )
    result = plates.evaluate(workspace, Engine(store), plate)
    assert (result["rows"], result["columns"]) == shape
    assert [(w["row"], w["column"]) for w in result["wells"]] == [
        (r, c) for r in range(shape[0]) for c in range(shape[1])
    ]
    assert result["wells"][0]["values"][plate.columns[0].id] == 2
    assert all(w["values"][plate.columns[0].id] is None for w in result["wells"][1:])
    assert ET.fromstring(plates.svg(result)).tag.endswith("svg")


def test_custom_measurements_and_svg_keep_independent_replica_truth(store):
    workspace = acquisition_workspace(store)
    original = workspace.model_dump_json()
    plate = custom_plate(workspace)
    result = plates.evaluate(workspace, Engine(store), plate)
    wells = {w["well"]: w for w in result["wells"]}
    assert result["acquisition_count"] == 3 and result["mapped_wells"] == 2
    count, signal = plate.columns
    assert wells["A01"]["values"][count.id] == 3  # median of complete counts 4 and 2
    assert wells["A01"]["values"][signal.id] == 76.25  # median of 2.5 and 150
    assert wells["A03"]["values"][count.id] == 4
    assert wells["A03"]["values"][signal.id] == 20
    assert wells["B03"]["staged"]["Dose"] == "0"
    assert wells["B03"]["values"][count.id] is None
    svg = ET.fromstring(plates.svg(result))
    text = " ".join(svg.itertext())
    assert "A01" in text and "A03" in text and "B03" in text
    assert workspace.model_dump_json() == original


@pytest.mark.parametrize(
    "geometry",
    [
        None,
        {},
        {"rows": 0, "columns": 3},
        {"rows": 2, "columns": 0},
        {"rows": 97, "columns": 1},
        {"rows": 1, "columns": 97},
        {"rows": 96, "columns": 17},
        {"rows": True, "columns": 3},
        {"rows": 2.0, "columns": 3},
        {"rows": "2", "columns": 3},
        {"rows": 2, "columns": 3, "spacing": 1},
        {"rows": float("nan"), "columns": 3},
    ],
)
def test_invalid_custom_dimensions_are_rejected(geometry):
    with pytest.raises(ValidationError):
        PlateDefinition(format="custom", geometry=geometry)


def test_numbered_format_cannot_carry_conflicting_dimensions():
    with pytest.raises(ValidationError, match="custom"):
        PlateDefinition(format=96, geometry=PlateGeometry(rows=8, columns=12))
    with pytest.raises(ValidationError, match="outside"):
        PlateDefinition(
            format="custom",
            geometry=PlateGeometry(rows=2, columns=3),
            annotations={"C01": {"Dose": "1"}},
        )
    with pytest.raises(ValidationError, match="custom"):
        plates.PlateDiscovery(revision=0, geometry=PlateGeometry(rows=2, columns=3))


def test_resize_same_total_changes_geometry_and_reviews_every_removed_position(store):
    workspace = acquisition_workspace(store)
    plate = custom_plate(workspace)
    before = plate.model_dump_json()
    result = plates.resize(
        plates.PlateResize(
            revision=workspace.revision,
            plate=plate,
            format="custom",
            geometry=PlateGeometry(rows=3, columns=2),
        )
    )
    resized = PlateDefinition.model_validate(result["plate"])
    assert resized.dimensions == (3, 2)
    assert result["removed_wells"] == ["A03", "B03"]
    assert result["removed_acquisitions"] == 1 and result["removed_annotation_keys"] == 2
    assert resized.assignments == {"A01": plate.assignments["A01"]}
    assert resized.annotations == {"A01": {"Dose": "0.5"}}
    assert plate.model_dump_json() == before
    restored = plates.resize(
        plates.PlateResize(revision=workspace.revision, plate=resized, format=24)
    )
    assert restored["removed_wells"] == [] and "geometry" not in restored["plate"]


def test_custom_discovery_includes_rows_after_z_and_preserves_default_inference(store):
    workspace = acquisition_workspace(store)
    legacy = plates.discover(
        workspace, plates.PlateDiscovery(revision=workspace.revision, include_replicates=True)
    )
    assert [p["format"] for p in legacy["plates"]] == [96, 1536]
    workspace.samples[3].metadata["$WELLID"] = "CR01"
    result = plates.discover(
        workspace,
        plates.PlateDiscovery(
            revision=workspace.revision,
            format="custom",
            geometry=PlateGeometry(rows=96, columns=1),
            include_replicates=True,
        ),
    )
    mapped = next(p for p in result["plates"] if p["plate_key"] == "P2")
    assert mapped["assignments"] == {"CR01": [workspace.samples[3].id]}
    assert mapped["geometry"] == {"rows": 96, "columns": 1}
    assert any(i.get("well") == "A02" for i in result["issues"])


def test_custom_template_annotations_and_dilution_keep_exact_positions(store):
    workspace = acquisition_workspace(store)
    plate = custom_plate(workspace)
    imported = PlateDefinition.model_validate(
        plates.import_template(json.dumps(plates.template(plate)).encode())["plate"]
    )
    assert imported.id != plate.id and imported.assignments == {}
    assert imported.geometry == plate.geometry and imported.annotations == plate.annotations
    csv_rows = list(csv.DictReader(io.StringIO(plates.annotation_csv(plate))))
    assert [r["Well ID"] for r in csv_rows] == ["A01", "A03", "B03"]
    assert next(r for r in csv_rows if r["Well ID"] == "B03")["Dose"] == "0"
    generated = plates.dilution_series(
        plates.PlateSeries(
            revision=workspace.revision, plate=plate, start_well="B01", steps=3, start=1, factor=0.5
        )
    )
    assert [r["value"] for r in generated["records"]] == ["1", "0.5", "0.25"]
    assert [r["well"] for r in generated["records"]] == ["B01", "B02", "B03"]
    with pytest.raises(ValueError, match="beyond"):
        plates.dilution_series(
            plates.PlateSeries(revision=workspace.revision, plate=plate, start_well="B01", steps=4)
        )


def test_shape_change_invalidates_annotation_review_even_when_well_count_is_equal(store):
    workspace = acquisition_workspace(store)
    plate = custom_plate(workspace)
    first = plates.PlateApply(revision=workspace.revision, plate=plate)
    reviewed = plates.annotation_review(workspace, first)
    plate.geometry = PlateGeometry(rows=3, columns=2)
    plate.assignments.pop("A03")
    plate.annotations.pop("B03")
    next_request = plates.PlateApply(
        revision=workspace.revision, plate=plate, review_hash=reviewed["review_hash"]
    )
    with pytest.raises(ConflictError, match="review"):
        plates.apply_annotations(workspace, next_request)


def test_plate_report_render_keeps_custom_geometry_and_source_values(store):
    workspace = acquisition_workspace(store)
    plate = custom_plate(workspace)
    workspace.plates.append(plate)
    layout = LayoutDefinition(
        name="Custom plate report",
        elements=[
            ReportElement(
                kind="plate", plate_id=plate.id, iterate=False, width_mm=170, height_mm=120
            )
        ],
    )
    result = page_for(workspace, Engine(store), layout, validate_sources=True)
    assert result["exportable"], result["issues"]
    data = result["manifest"]["elements"][0]["data"]
    assert data["rows"] == 2 and data["columns"] == 3 and len(data["wells"]) == 6
    assert data["plate"]["geometry"] == {"rows": 2, "columns": 3}
    assert data["wells"][0]["values"][plate.columns[1].id] == 76.25


def test_portable_report_rebinds_to_destination_plate_geometry_and_measurements(store):
    source = acquisition_workspace(store)
    target = acquisition_workspace(store)
    source_plate = custom_plate(source)
    target_plate = PlateDefinition(
        name=source_plate.name,
        format="custom",
        geometry=PlateGeometry(rows=3, columns=4),
        assignments={"C04": [target.samples[2].id]},
    )
    source.plates = [source_plate]
    target.plates = [target_plate]
    layout = LayoutDefinition(
        name="Portable plate",
        elements=[
            ReportElement(
                kind="plate", plate_id=source_plate.id, iterate=False, width_mm=170, height_mm=120
            )
        ],
    )
    request = request_for(source, target, layout)
    review = report_templates.preview(target, request, Engine(store))
    assert review["can_apply"], review["issues"]
    imported = LayoutDefinition.model_validate(review["definition"])
    assert imported.elements[0].plate_id == target_plate.id
    page = reports.render(
        target,
        Engine(store),
        reports.ReportRequest(revision=target.revision, definition=imported, validate_sources=True),
    )
    assert page["exportable"], page["issues"]
    data = page["manifest"]["elements"][0]["data"]
    assert data["rows"] == 3 and data["columns"] == 4
    assert data["wells"][-1]["well"] == "C04"
    assert data["wells"][-1]["values"][target_plate.columns[0].id] == 2


def test_api_custom_plate_history_archive_and_invalid_resizing_are_atomic(client):
    store = client.app.state.store
    workspace = acquisition_workspace(store)
    plate = custom_plate(workspace)
    base = f"/api/workspaces/{workspace.id}"
    body = {"revision": workspace.revision, "plate": plate.model_dump()}
    saved = client.post(base + "/plates/save", json=body)
    assert saved.status_code == 200, saved.text
    saved = saved.json()
    evaluated = client.get(base + f"/plates/{plate.id}/evaluate")
    assert evaluated.status_code == 200 and len(evaluated.json()["wells"]) == 6
    resize_body = {
        "revision": saved["revision"],
        "plate": plate.model_dump(),
        "format": "custom",
        "geometry": {"rows": 3, "columns": 2},
    }
    resized = client.post(base + "/plates/resize", json=resize_body)
    assert resized.status_code == 200 and resized.json()["removed_wells"] == ["A03", "B03"]
    assert store.get(workspace.id).plates[0] == plate  # preview does not save
    for geometry in [None, {"rows": True, "columns": 3}, {"rows": 96, "columns": 17}]:
        bad = client.post(base + "/plates/resize", json={**resize_body, "geometry": geometry})
        assert bad.status_code == 422
    assert store.get(workspace.id).revision == saved["revision"]
    staged = client.post(
        base + "/plates/save",
        json={"revision": saved["revision"], "plate": resized.json()["plate"]},
    )
    assert staged.status_code == 200, staged.text
    staged = staged.json()
    undone = client.post(base + "/undo", json={"revision": staged["revision"]}).json()
    assert undone["plates"][0]["geometry"] == {"rows": 2, "columns": 3}
    redone = client.post(base + "/redo", json={"revision": undone["revision"]}).json()
    assert redone["plates"][0]["geometry"] == {"rows": 3, "columns": 2}
    archive = client.get(base + "/export/project").content
    imported = client.post(
        "/api/import/project", files={"file": ("custom-plate.cytoforge", archive)}
    )
    assert imported.status_code == 200, imported.text
    restored = imported.json()
    assert restored["plates"][0]["geometry"] == {"rows": 3, "columns": 2}
    assert restored["plates"][0]["assignments"] == redone["plates"][0]["assignments"]


def test_individual_replicate_plot_requests_keep_full_acquisition_counts(client):
    workspace = acquisition_workspace(client.app.state.store)
    counts = []
    for sample in [workspace.samples[0], workspace.samples[2]]:
        response = client.get(
            f"/api/workspaces/{workspace.id}/samples/{sample.id}/plot",
            params={"x": "X", "mode": "histogram", "bins": 32, "pooled": False},
        )
        assert response.status_code == 200, response.text
        counts.append(response.json()["count"])
    assert counts == [4, 2]
