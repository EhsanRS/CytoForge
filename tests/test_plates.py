import csv
import io
import json
from xml.etree import ElementTree as ET

import numpy as np
import pytest
from cytoforge import plates
from cytoforge.models import (
    Channel,
    Gate,
    Group,
    PlateDefinition,
    PlateView,
    Sample,
    TableColumn,
    Workspace,
    new_id,
    plate_position,
    plate_well,
)
from cytoforge.science import Engine, save_events
from cytoforge.store import ConflictError


def acquisition_workspace(store):
    doc = Workspace(name="Plate independent truth")
    for name, well, plate, values in [
        ("first", "A1", "P1", [1, 2, 3, 4]),
        ("second", "A02", "P1", [10, 20, 30, np.nan]),
        ("replicate", "A01", "P1", [100, 200]),
        ("large", "AA01", "P2", [0, 0, 0]),
        ("missing", "", "P1", [2, 4]),
        ("invalid", "A00", "P1", [2, 4]),
    ]:
        data = np.array(values, dtype=float)[:, None]
        sample = Sample(
            name=name,
            event_count=len(data),
            channels=[Channel(name="X")],
            metadata={"$WELLID": well, "$PLATEID": plate},
            tags={"Unrelated": "keep"},
        )
        sample.sha256 = save_events(store.data_path(doc.id, sample.id), data)
        doc.samples.append(sample)
    return store.create(doc)


def definition(doc):
    return PlateDefinition(
        name="Dose plate",
        plate_key="P1",
        assignments={"A01": [doc.samples[0].id, doc.samples[2].id], "A02": [doc.samples[1].id]},
        columns=[
            TableColumn(name="Events", statistic="count"),
            TableColumn(name="MFI", statistic="median", channel="X"),
            TableColumn(name="Treatment", kind="metadata", metadata_key="Treatment"),
        ],
    )


@pytest.mark.parametrize(
    "text,position",
    [
        ("a1", (0, 0)),
        (" A_01 ", (0, 0)),
        ("H12", (7, 11)),
        ("P24", (15, 23)),
        ("AF48", (31, 47)),
        ("AA01", (26, 0)),
        ("b:002", (1, 1)),
    ],
)
def test_well_normalization_and_rows_after_z(text, position):
    assert plate_position(text) == position
    assert plate_position(plate_well(*position)) == position


@pytest.mark.parametrize("value", ["A0", "0", "01", "A1x", " A 0 ", "<A1>", "AAA01"])
def test_invalid_wells_fail(value):
    with pytest.raises(ValueError, match="Invalid well"):
        plate_position(value)


def test_model_canonical_duplicates_dimensions_and_member_repetition():
    a, b = new_id(), new_id()
    plate = PlateDefinition(assignments={"a1": [a]}, annotations={"b02": {"Dose": "0.5"}})
    assert plate.assignments == {"A01": [a]}
    assert plate.annotations == {"B02": {"Dose": "0.5"}}
    with pytest.raises(ValueError, match="Duplicate normalized"):
        PlateDefinition(assignments={"A01": [a], "A1": [b]})
    with pytest.raises(ValueError, match="outside"):
        PlateDefinition(format=96, annotations={"AA01": {"X": "y"}})
    with pytest.raises(ValueError, match="only one well"):
        PlateDefinition(assignments={"A01": [a], "A02": [a]})
    with pytest.raises(ValueError, match="finite and increasing"):
        PlateView(domains={new_id(): (1, 1)})


def test_discovery_does_not_silently_choose_duplicate_wells_and_infers_geometry(store):
    doc = acquisition_workspace(store)
    result = plates.discover(doc, plates.PlateDiscovery(revision=doc.revision))
    assert len(result["issues"]) == 3
    first, second = result["plates"]
    assert first["format"] == 96 and first["assignments"] == {"A02": [doc.samples[1].id]}
    assert second["format"] == 1536 and second["assignments"] == {"AA01": [doc.samples[3].id]}
    result = plates.discover(
        doc, plates.PlateDiscovery(revision=doc.revision, include_replicates=True)
    )
    assert result["plates"][0]["assignments"]["A01"] == [doc.samples[0].id, doc.samples[2].id]
    assert result["assigned_acquisitions"] == 4


def test_discovery_scope_keyword_override_conflicts_and_explicit_format(store):
    doc = acquisition_workspace(store)
    doc.samples[0].tags["Well ID"] = "B03"
    result = plates.discover(doc, plates.PlateDiscovery(revision=doc.revision))
    assert result["plates"][0]["assignments"]["B03"] == [doc.samples[0].id]
    doc.samples[0].tags["well_id"] = "C03"
    result = plates.discover(doc, plates.PlateDiscovery(revision=doc.revision))
    assert any("Conflicting" in i["message"] for i in result["issues"])
    group = Group(name="Large only", sample_ids=[doc.samples[3].id])
    doc.groups.append(group)
    result = plates.discover(
        doc, plates.PlateDiscovery(revision=doc.revision, group_id=group.id, format=96)
    )
    assert result["plates"][0]["assignments"] == {} and len(result["issues"]) == 1
    with pytest.raises(ConflictError):
        plates.discover(doc, plates.PlateDiscovery(revision=doc.revision + 1))


def test_csv_bom_quotes_newlines_invalid_missing_and_duplicate_rows(store):
    doc = acquisition_workspace(store)
    text = (
        "\ufeffWell ID,Treatment,Dose\nA1,first,1\nA01,second,2\n"
        'A02,"Drug, A\nsecond line",0.5\nB03,empty,0\nA0,bad,9\n,missing,1\n'
    )
    result = plates.import_csv(plates.PlateImport(revision=0, plate=definition(doc), text=text))
    assert result["staged_rows"] == 2 and len(result["issues"]) == 3
    assert "A01" not in result["plate"]["annotations"]
    assert result["plate"]["annotations"]["A02"]["Treatment"] == "Drug, A\nsecond line"
    assert result["plate"]["annotations"]["B03"]["Dose"] == "0"
    assert all("Treatment" not in s.tags for s in doc.samples)


def test_csv_multiple_plates_and_explicit_blank_clear(store):
    doc = acquisition_workspace(store)
    plate = definition(doc)
    text = "Plate ID,Well ID,Treatment,Dose\nP1,A1,,1\nP2,A2,foreign,2\n"
    result = plates.import_csv(
        plates.PlateImport(revision=0, plate=plate, text=text, plate_column="Plate ID")
    )
    assert result["skipped_other_plates"] == 1
    assert result["plate"]["annotations"]["A01"] == {"Dose": "1"}
    result = plates.import_csv(
        plates.PlateImport(
            revision=0, plate=plate, text=text, plate_column="Plate ID", clear_blanks=True
        )
    )
    assert result["plate"]["annotations"]["A01"]["Treatment"] is None
    plate.plate_key = ""
    with pytest.raises(ValueError, match="multiple plates"):
        plates.import_csv(
            plates.PlateImport(revision=0, plate=plate, text=text, plate_column="Plate ID")
        )


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Well ID,Well ID\nA1,A2\n",
        'Well ID,X\nA1,"unterminated',
        "Wrong,X\nA1,1\n",
        "Well ID,X\nA1,\x00\n",
    ],
)
def test_malformed_csv_fails_explicitly(text):
    with pytest.raises(ValueError):
        plates.import_csv(plates.PlateImport(revision=0, plate=PlateDefinition(), text=text))


def test_exact_dilution_series_replicates_units_and_boundary_guards(store):
    doc = acquisition_workspace(store)
    body = plates.PlateSeries(
        revision=0,
        plate=definition(doc),
        keyword="Dose",
        unit="µg/mL",
        unit_keyword="Dose unit",
        steps=8,
        replicates=2,
    )
    result = plates.dilution_series(body)
    expected = [str(v) for v in [1, 0.5, 0.25, 0.125, 0.0625, 0.03125, 0.015625, 0.0078125]]
    assert [r["value"] for r in result["records"] if r["replicate"] == 0] == expected
    assert result["plate"]["annotations"]["B08"] == {"Dose": "0.0078125", "Dose unit": "µg/mL"}
    assert all("Dose" not in s.tags for s in doc.samples)
    body.direction = "rows"
    body.steps = 4
    body.replicates = 3
    body.operation = "add"
    body.start = 0
    body.increment = 0.1
    result = plates.dilution_series(body)
    assert result["plate"]["annotations"]["D03"]["Dose"] == "0.3"
    body.start_well = "H12"
    with pytest.raises(ValueError, match="beyond"):
        plates.dilution_series(body)
    body.start_well = "A01"
    body.increment = -1
    with pytest.raises(ValueError, match="nonnegative"):
        plates.dilution_series(body)


def test_full_event_statistics_equal_weight_replicates_missing_channels_and_scales(store):
    doc = acquisition_workspace(store)
    plate = definition(doc)
    result = plates.evaluate(doc, Engine(store), plate)
    cells = {w["well"]: w for w in result["wells"]}
    count, mfi, _ = plate.columns
    assert cells["A01"]["values"][count.id] == 3
    assert cells["A01"]["values"][mfi.id] == 76.25
    assert cells["A02"]["values"][mfi.id] == 20
    assert cells["A03"]["values"][count.id] is None
    assert len(cells) == 96 and result["acquisition_count"] == 3
    assert result["domains"][mfi.id]["min"] == 20
    assert result["domains"][mfi.id]["max"] == 76.25
    plate.aggregate = "sum"
    result = plates.evaluate(doc, Engine(store), plate)
    assert result["wells"][0]["values"][count.id] == 6
    assert result["wells"][0]["values"][mfi.id] == 152.5
    plate.assignments = {}
    result = plates.evaluate(doc, Engine(store), plate)
    assert result["acquisition_count"] == 0 and result["mapped_wells"] == 0
    assert all(w["values"][count.id] is None for w in result["wells"])


def test_population_paths_partial_replicates_and_missing_measurement_explanations(store):
    doc = acquisition_workspace(store)
    gate = Gate(name="Subset", sample_id=doc.samples[0].id, kind="range", x="X", bounds=[0, 3])
    doc.gates.append(gate)
    plate = definition(doc)
    measure = plate.columns[1]
    measure.population_path = ["Subset"]
    result = plates.evaluate(doc, Engine(store), plate)
    assert result["wells"][0]["values"][measure.id] is None
    assert "1 of 2 replicates available" in result["wells"][0]["status"][measure.id]
    plate.missing_replicates = "available"
    result = plates.evaluate(doc, Engine(store), plate)
    assert result["wells"][0]["values"][measure.id] == 1.5
    assert result["wells"][0]["status"][measure.id]
    measure.population_path = []
    measure.channel = "Missing"
    result = plates.evaluate(doc, Engine(store), plate)
    assert result["wells"][0]["values"][measure.id] is None
    assert "parameter is missing" in result["wells"][0]["status"][measure.id].lower()


def test_formula_columns_keyword_heatmaps_and_faces_use_live_applied_values(store):
    doc = acquisition_workspace(store)
    doc.samples[0].tags["Dose"] = "1"
    doc.samples[2].tags["Dose"] = "1"
    doc.samples[1].tags["Dose"] = "0.5"
    plate = definition(doc)
    mfi = plate.columns[1]
    dose = TableColumn(name="Dose", kind="metadata", metadata_key="Dose", metadata_numeric=True)
    formula = TableColumn(name="Normalized", kind="formula", expression='col("MFI")/col("Dose")')
    plate.columns += [dose, formula]
    plate.view = PlateView(mode="faces", primary=dose.id)
    plate = PlateDefinition.model_validate(plate.model_dump())
    result = plates.evaluate(doc, Engine(store), plate)
    assert result["wells"][0]["values"][formula.id] == 76.25
    assert result["wells"][1]["values"][formula.id] == 40
    assert result["wells"][0]["normalized"][dose.id] == 1
    assert "<ellipse" in result["wells"][0]["face_svg"]
    assert "<ellipse" not in result["wells"][2]["face_svg"]
    assert result["face_features"][1]["column_id"] == mfi.id
    plate.view = PlateView(
        mode="split", primary=mfi.id, secondary=dose.id, domains={mfi.id: (0, 30)}
    )
    result = plates.evaluate(doc, Engine(store), plate)
    assert result["wells"][0]["clipped"][mfi.id]
    assert result["wells"][0]["color"] == plates.heat_color(1)
    svg = plates.svg(result)
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg") and "plate" in svg.lower()


def test_annotation_review_replacement_empty_wells_hash_and_fill_missing(store):
    doc = acquisition_workspace(store)
    doc.samples[0].tags.update(Treatment="old", Remove="yes")
    plate = definition(doc)
    plate.annotations = {
        "A01": {"Treatment": "new", "Remove": None},
        "B03": {"Treatment": "future"},
    }
    body = plates.PlateApply(revision=doc.revision, plate=plate)
    review = plates.annotation_review(doc, body)
    assert review["empty_wells"] == ["B03"] and review["sample_count"] == 2
    assert review["change_count"] == 3
    with pytest.raises(ConflictError, match="reviewed"):
        plates.apply_annotations(doc, body)
    body.review_hash = review["review_hash"]
    plates.apply_annotations(doc, body)
    assert doc.samples[0].tags == {"Unrelated": "keep", "Treatment": "new"}
    assert doc.samples[2].tags["Treatment"] == "new"
    assert doc.samples[0].metadata["$WELLID"] == "A1"
    assert doc.plates[0].annotations["B03"]["Treatment"] == "future"
    body.mode = "fill_missing"
    body.plate.annotations["A01"]["Treatment"] = "different"
    assert plates.annotation_review(doc, body)["change_count"] == 0


def test_templates_annotation_export_and_resize_preserve_or_report_identity(store):
    doc = acquisition_workspace(store)
    plate = definition(doc)
    plate.annotations = {
        "A01": {"Dose": "0.5", "Treatment": '=HYPERLINK("x")'},
        "P24": {"Dose": "1"},
    }
    plate.format = 384
    imported = plates.import_template(json.dumps(plates.template(plate)).encode())
    assert imported["plate"]["id"] != plate.id
    assert imported["plate"]["assignments"] == {}
    assert imported["plate"]["annotations"] == plate.annotations
    text = plates.annotation_csv(plate)
    rows = list(csv.DictReader(io.StringIO(text)))
    assert rows[0]["Treatment"].startswith("'=")
    resized = plates.resize(plates.PlateResize(revision=0, plate=plate, format=96))
    assert resized["removed_wells"] == ["P24"]
    assert resized["removed_annotation_keys"] == 1
    assert doc.samples[0].tags == {"Unrelated": "keep"}


def test_category_sources_stable_colors_overrides_and_xml_escaping(store):
    doc = acquisition_workspace(store)
    plate = definition(doc)
    label = '<Drug & "A">'
    for sample in doc.samples[:3]:
        sample.metadata["Condition"] = label
        sample.tags["Condition"] = "Applied condition"
    plate.view = PlateView(
        mode="categories", category_source="metadata", category_keyword="Condition"
    )
    first = plates.evaluate(doc, Engine(store), plate)
    assert first["wells"][0]["category_value"] == label
    assert first["wells"][0]["category_color"] == first["categories"][label]
    original_color = first["categories"][label]
    doc.samples[1].metadata["Condition"] = "New category"
    second = plates.evaluate(doc, Engine(store), plate)
    assert second["categories"][label] == original_color
    plate.view.category_colors[label] = "#123456"
    second = plates.evaluate(doc, Engine(store), plate)
    assert second["wells"][0]["category_color"] == "#123456"
    output = plates.svg(second)
    ET.fromstring(output)
    assert "&lt;Drug &amp; &quot;A&quot;&gt;" in output
    assert '<Drug & "A">' not in output
    plate.view.category_source = "tags"
    assert (
        plates.evaluate(doc, Engine(store), plate)["wells"][0]["category_value"]
        == "Applied condition"
    )
    with pytest.raises(ValueError, match="hex colors"):
        PlateView(category_colors={"unsafe": 'red" onload="bad()'})


def test_extreme_replicate_aggregates_and_face_split_legends(store):
    assert plates.aggregate([1e308, 1e308], "median") == 1e308
    assert plates.aggregate([1e308, -1e308], "mean") == 0
    assert plates.aggregate([1e308, 1e308], "sum") is None
    doc = acquisition_workspace(store)
    plate = definition(doc)
    plate.view.mode = "faces"
    result = plates.evaluate(doc, Engine(store), plate)
    output = plates.svg(result)
    ET.fromstring(output)
    assert "Head width: Events" in output and "Head height: MFI" in output
    assert "Yellow dot: missing face features" in output
    plate.view.mode = "split"
    output = plates.svg(plates.evaluate(doc, Engine(store), plate))
    assert "MFI:" in output and "Events:" in output


def test_manual_subnormal_display_bounds_clip_large_measurements_without_underflow(store):
    doc = acquisition_workspace(store)
    for index in [0, 2]:
        doc.samples[index].tags["Extreme"] = "1e308"
    doc.samples[1].tags["Extreme"] = "-1e308"
    plate = definition(doc)
    measure = TableColumn(
        name="Extreme", kind="metadata", metadata_key="Extreme", metadata_numeric=True
    )
    plate.columns = [measure]
    plate.view = PlateView(primary=measure.id, domains={measure.id: (5e-324, 1e-323)})
    result = plates.evaluate(doc, Engine(store), plate)
    assert result["wells"][0]["values"][measure.id] == 1e308
    assert result["wells"][1]["values"][measure.id] == -1e308
    assert result["wells"][0]["normalized"][measure.id] == 1
    assert result["wells"][1]["normalized"][measure.id] == 0
    assert result["wells"][0]["clipped"][measure.id]
    assert result["wells"][1]["clipped"][measure.id]
    assert result["wells"][0]["color"] == "#5ce0b6"
    assert result["wells"][1]["color"] == "#1e364d"


def test_annotation_csv_reserved_keys_have_unique_roundtrippable_mapping_headers():
    plate = PlateDefinition(
        plate_key="P1",
        annotations={
            "A01": {
                "Well ID": "custom",
                "Plate ID": "planned",
                "Plate ID (mapping 1)": "also planned",
            }
        },
    )
    content = plates.annotation_csv(plate)
    headers = next(csv.reader(io.StringIO(content)))
    assert len(headers) == len(set(headers))
    assert headers[:2] == ["Plate ID (mapping 2)", "Well ID (mapping 1)"]
    restored = plates.import_csv(
        plates.PlateImport(
            revision=0,
            plate=PlateDefinition(plate_key="P1"),
            text=content,
            plate_column=headers[0],
            well_column=headers[1],
        )
    )
    assert restored["plate"]["annotations"] == plate.annotations


def test_measurement_csv_neutralizes_formula_like_headers_and_keeps_numeric_cells(store):
    doc = acquisition_workspace(store)
    plate = definition(doc)
    plate.columns[1].name = '=HYPERLINK("unsafe")'
    rows = list(
        csv.reader(io.StringIO(plates.measurements_csv(plates.evaluate(doc, Engine(store), plate))))
    )
    assert rows[0][5].startswith("'=") and rows[0][8].startswith("'=")
    assert rows[1][5] == "76.25"


def test_api_batch_save_rolls_back_all_changes_and_obeys_review_revision(client):
    doc = acquisition_workspace(client.app.state.store)
    first = definition(doc)
    second = PlateDefinition(name="Second plate", assignments={"B02": [new_id()]})
    base = f"/api/workspaces/{doc.id}"
    body = {"revision": doc.revision, "plates": [first.model_dump(), second.model_dump()]}
    response = client.post(base + "/plates/save-batch", json=body)
    assert response.status_code == 422
    assert client.app.state.store.get(doc.id).plates == []
    second.assignments = {"B02": [doc.samples[3].id]}
    body["plates"][1] = second.model_dump()
    response = client.post(base + "/plates/save-batch", json=body)
    assert response.status_code == 200, response.text
    assert len(response.json()["plates"]) == 2
    body["revision"] = response.json()["revision"]
    body["base_revision"] = doc.revision
    assert client.post(base + "/plates/save-batch", json=body).status_code == 409
    body.pop("base_revision")
    body["plates"][1] = body["plates"][0]
    assert client.post(base + "/plates/save-batch", json=body).status_code == 422


def test_api_atomic_annotations_conflicts_groups_undo_deletion_and_archive(client):
    store = client.app.state.store
    doc = acquisition_workspace(store)
    plate = definition(doc)
    plate.annotations = {"A01": {"Treatment": "new", "Dose": "0.5"}, "B03": {"Dose": "1"}}
    base = f"/api/workspaces/{doc.id}"
    body = dict(revision=doc.revision, plate=plate.model_dump())
    review = client.post(base + "/plates/annotations/review", json=body)
    assert review.status_code == 200, review.text
    body["review_hash"] = review.json()["review_hash"]
    applied = client.post(base + "/plates/annotations/apply", json=body)
    assert applied.status_code == 200, applied.text
    saved = applied.json()
    assert saved["samples"][0]["tags"]["Treatment"] == "new"
    assert len(saved["plates"]) == 1
    conflict = client.post(
        base + "/plates/save",
        json={
            "plate": plate.model_dump(),
            "revision": saved["revision"],
            "base_revision": doc.revision,
        },
    )
    assert conflict.status_code == 409, conflict.text
    # A changed custom keyword makes a stale reviewed preview fail even at a new revision.
    changed = client.patch(
        base + f"/samples/{doc.samples[0].id}",
        json={"revision": saved["revision"], "name": "first", "tags": {"Treatment": "changed"}},
    ).json()
    stale = client.post(
        base + "/plates/annotations/apply", json={**body, "revision": changed["revision"]}
    )
    assert stale.status_code == 409
    body["revision"] = changed["revision"]
    body["review_hash"] = client.post(base + "/plates/annotations/review", json=body).json()[
        "review_hash"
    ]
    saved = client.post(base + "/plates/annotations/apply", json=body).json()
    group = client.post(
        base + "/plates/group",
        json={
            "revision": saved["revision"],
            "plate": plate.model_dump(),
            "wells": ["A1"],
            "name": "Replicates",
        },
    )
    assert group.status_code == 200, group.text
    saved = group.json()
    assert set(saved["groups"][0]["sample_ids"]) == {doc.samples[0].id, doc.samples[2].id}
    exported = client.post(
        base + "/plates/export/json",
        json={"revision": saved["revision"], "plate": plate.model_dump()},
    )
    assert exported.status_code == 200, exported.text
    assert exported.json()["wells"][0]["keywords"]["Treatment"] == "new"
    archive = client.get(base + "/export/project").content
    restored = client.post("/api/import/project", files={"file": ("plate.cytoforge", archive)})
    assert restored.status_code == 200, restored.text
    restored = restored.json()
    result = client.get(f"/api/workspaces/{restored['id']}/plates/{plate.id}/evaluate")
    assert result.status_code == 200 and result.json()["wells"] == exported.json()["wells"]
    removed = client.delete(base + f"/samples/{doc.samples[0].id}?revision={saved['revision']}")
    assert removed.status_code == 200, removed.text
    deleted = removed.json()
    assert deleted["plates"][0]["assignments"]["A01"] == [doc.samples[2].id]
    undone = client.post(base + "/undo", json={"revision": deleted["revision"]}).json()
    assert undone["plates"][0]["assignments"]["A01"] == [doc.samples[0].id, doc.samples[2].id]


def test_api_rejects_missing_members_without_partial_plate_save(client):
    doc = acquisition_workspace(client.app.state.store)
    plate = definition(doc)
    plate.assignments["B01"] = [new_id()]
    response = client.post(
        f"/api/workspaces/{doc.id}/plates/save",
        json={"revision": doc.revision, "plate": plate.model_dump()},
    )
    assert response.status_code == 422
    assert client.app.state.store.get(doc.id).plates == []
