"""Exact acquisition/gate identity, historical values and portable comparison tables."""

import csv
import io
import zipfile
from xml.etree import ElementTree as ET

import pytest
from cytoforge import population_comparison as comparisons
from cytoforge.models import AnalysisInput, Gate, TableColumn, TableDefinition, Workspace, new_id
from cytoforge.tables import csv_text, evaluate_table, write_xlsx
from test_population_comparison import fixture


def saved(store):
    doc, request, engine = fixture(store)
    target = doc.samples[1]
    # Duplicate names are deliberately distinct populations with different event counts.
    low, high = [
        Gate(sample_id=target.id, name="Responders", kind="range", x="X", bounds=bounds)
        for bounds in ([0, 1], [1, 4])
    ]
    doc = store.mutate(
        doc.id,
        "Save comparison target gates",
        lambda value: value.gates.extend([low, high]),
        doc.revision,
    )
    request.inputs.extend(AnalysisInput(sample_id=target.id, gate_id=g.id) for g in [low, high])
    request.revision = doc.revision
    request = type(request).model_validate(request.model_dump())
    result = comparisons.calculate(doc, request, engine, new_id())
    doc.comparison_results.append(result)
    return doc, request, engine, result, low, high


def table(doc, result, **settings):
    column = TableColumn(
        name="Compared events",
        kind="population_comparison",
        result_id=result.id,
        comparison_parameter_id=result.request.parameters[0].id,
        biology_metric="finite_count",
        follow_replacement=False,
    )
    return TableDefinition(
        name="Comparison populations",
        row_mode="populations",
        sample_ids=[doc.samples[1].id],
        columns=[column],
        **settings,
    )


def commit_fixture(store, doc):
    def persist(workspace):
        workspace.gates = doc.gates
        workspace.comparison_results = doc.comparison_results

    return store.mutate(doc.id, "Save comparison fixture", persist, doc.revision)


def test_population_rows_use_actual_gate_identity_even_with_duplicate_names(store):
    doc, _, engine, result, low, high = saved(store)
    definition = table(doc, result)
    output = evaluate_table(doc, engine, definition)
    column = definition.columns[0].id
    assert {r["gate_id"]: r["values"][column] for r in output["rows"]} == {
        None: 10,
        low.id: 2,
        high.id: 8,
    }
    assert all(not r["status"] for r in output["rows"])
    assert output["provenance"]["models"][0]["id"] == result.id


def test_sample_rows_require_explicit_mapping_for_multiple_compared_populations(store):
    doc, _, engine, result, low, _ = saved(store)
    definition = table(doc, result)
    definition.row_mode = "samples"
    column = definition.columns[0]
    row = evaluate_table(doc, engine, definition)["rows"][0]
    assert row["values"][column.id] is None
    assert "Multiple populations" in row["status"][column.id]
    column.population_overrides = {doc.samples[1].id: low.id}
    assert evaluate_table(doc, engine, definition)["rows"][0]["values"][column.id] == 2
    column.population_overrides = {}
    column.population_path = []
    assert evaluate_table(doc, engine, definition)["rows"][0]["values"][column.id] == 10
    column.population_path = ["Responders"]
    row = evaluate_table(doc, engine, definition)["rows"][0]
    assert row["values"][column.id] is None and "ambiguous" in row["status"][column.id]


def test_joint_mapping_missing_targets_and_unavailable_statistics_are_explicit(store):
    doc, _, engine, result, low, high = saved(store)
    definition = table(doc, result)
    definition.sample_ids = []
    column = definition.columns[0]
    column.comparison_parameter_id = None
    output = evaluate_table(doc, engine, definition)
    targets = [r for r in output["rows"] if r["sample_id"] == doc.samples[1].id]
    assert {r["gate_id"]: r["values"][column.id] for r in targets} == {
        None: 10,
        low.id: 2,
        high.id: 8,
    }
    control = next(r for r in output["rows"] if r["sample_id"] == doc.samples[0].id)
    assert control["values"][column.id] is None
    assert "not compared" in control["status"][column.id]
    column.comparison_parameter_id = "a" * 32
    row = evaluate_table(doc, engine, definition)["rows"][0]
    assert row["values"][column.id] is None


def test_refits_follow_only_when_selected_and_keep_original_gate_counts(store):
    doc, request, engine, result, low, _ = saved(store)
    doc = commit_fixture(store, doc)
    doc = store.mutate(
        doc.id,
        "Edit target gate",
        lambda w: setattr(next(g for g in w.gates if g.id == low.id), "bounds", [0, 2]),
        doc.revision,
    )
    updated = request.model_copy(
        deep=True, update={"replace_result_id": result.id, "revision": doc.revision}
    )
    replacement = comparisons.calculate(doc, updated, engine, new_id())
    doc.comparison_results.append(replacement)
    definition = table(doc, result)
    column = definition.columns[0]
    column.population_overrides = {doc.samples[1].id: low.id}
    row = evaluate_table(doc, engine, definition)["rows"][0]
    assert row["values"][column.id] is None and "Stale" in row["status"][column.id]
    column.allow_stale = True
    row = evaluate_table(doc, engine, definition)["rows"][0]
    assert row["values"][column.id] == 2 and "Stale" in row["status"][column.id]
    column.follow_replacement = True
    row = evaluate_table(doc, engine, definition)["rows"][0]
    assert row["values"][column.id] == 5 and not row["status"]
    assert len(doc.comparison_results) == 2


@pytest.mark.parametrize("source", ["control", "target", "artifact"])
def test_changed_source_bytes_cannot_be_hidden_by_cached_or_historical_counts(store, source):
    doc, _, engine, result, _, _ = saved(store)
    definition = table(doc, result)
    assert (
        evaluate_table(doc, engine, definition)["rows"][0]["values"][definition.columns[0].id] == 10
    )
    path = (
        store.comparison_path(doc.id, result.id)
        if source == "artifact"
        else store.data_path(
            doc.id,
            doc.samples[0 if source == "control" else 1].id,
        )
    )
    content = bytearray(path.read_bytes())
    content[-1] ^= 1
    path.write_bytes(content)
    definition.columns[0].allow_stale = True
    output = evaluate_table(doc, engine, definition)
    column = definition.columns[0].id
    assert all(r["values"][column] is None for r in output["rows"])
    assert all("SHA-256" in r["status"][column] for r in output["rows"])


def test_comparison_table_csv_and_xlsx_retain_each_distinct_population(store, tmp_path):
    doc, _, engine, result, _, _ = saved(store)
    definition = table(doc, result)
    definition.columns.append(
        TableColumn(name="Double", kind="formula", expression='col("Compared events") * 2')
    )
    definition = TableDefinition.model_validate(definition.model_dump())
    output = evaluate_table(doc, engine, definition)
    rows = list(csv.DictReader(io.StringIO(csv_text(output))))
    assert [float(r["Compared events"]) for r in rows] == [10, 2, 8]
    assert [float(r["Double"]) for r in rows] == [20, 4, 16]
    path = tmp_path / "compared.xlsx"
    write_xlsx(path, output, tmp_path)
    with zipfile.ZipFile(path) as archive:
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        for address, expected in [("E2", 10), ("E3", 2), ("E4", 8)]:
            assert [
                float(c.find("s:v", ns).text) for c in sheet.findall(f".//s:c[@r='{address}']", ns)
            ] == [expected]


def test_comparison_table_formulas_csv_xlsx_and_archive_preserve_distinct_populations(client):
    store = client.app.state.store
    doc, _, _, result, _, _ = saved(store)

    doc = commit_fixture(store, doc)
    original = {s.id: store.data_path(doc.id, s.id).read_bytes() for s in doc.samples}
    definition = table(doc, result)
    definition.columns.append(
        TableColumn(name="Double", kind="formula", expression='col("Compared events") * 2')
    )
    base = f"/api/workspaces/{doc.id}"
    response = client.post(
        f"{base}/tables/save",
        json={"revision": doc.revision, "definition": definition.model_dump()},
    )
    assert response.status_code == 200, response.text
    doc = Workspace.model_validate(response.json())
    body = {"revision": doc.revision, "definition": definition.model_dump()}
    exported = client.post(f"{base}/tables/export/csv", json=body)
    assert exported.status_code == 200, exported.text
    rows = list(csv.DictReader(io.StringIO(exported.text)))
    assert [float(r["Compared events"]) for r in rows] == [10, 2, 8]
    assert [float(r["Double"]) for r in rows] == [20, 4, 16]
    exported = client.post(f"{base}/tables/export/xlsx", json=body)
    assert exported.status_code == 200, exported.text
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        assert [float(c.find("s:v", ns).text) for c in sheet.findall(".//s:c[@r='E2']", ns)] == [10]
        assert [float(c.find("s:v", ns).text) for c in sheet.findall(".//s:c[@r='E3']", ns)] == [2]
        assert [float(c.find("s:v", ns).text) for c in sheet.findall(".//s:c[@r='E4']", ns)] == [8]
    portable = client.get(f"{base}/export/project")
    assert portable.status_code == 200, portable.text[:300]
    restored = client.post(
        "/api/import/project", files={"file": ("compared.cytoforge", portable.content)}
    )
    assert restored.status_code == 200, restored.text
    newdoc = Workspace.model_validate(restored.json())
    output = client.get(f"/api/workspaces/{newdoc.id}/tables/{definition.id}/evaluate")
    assert output.status_code == 200, output.text
    assert [r["values"][definition.columns[0].id] for r in output.json()["rows"]] == [10, 2, 8]
    assert all(
        store.data_path(doc.id, sid).read_bytes() == content for sid, content in original.items()
    )
    assert all(
        store.data_path(newdoc.id, sid).read_bytes() == content for sid, content in original.items()
    )
