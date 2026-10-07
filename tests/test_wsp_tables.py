"""External WSP tables and literal independent controls, row order and event truth."""

import warnings
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import pytest
from cytoforge.interchange import ImportApply, ImportMapping, apply_document, parse_document
from cytoforge.models import Channel, Compensation, Gate, Sample, TableDefinition, Workspace, new_id
from cytoforge.science import Engine, parse_fcs, save_events
from cytoforge.table_expressions import evaluate_table_formula, parse_table_formula
from cytoforge.tables import evaluate_table
from cytoforge.wsp_tables import convert_formula

from tools.wsp_table_fixture import NAMES, TRUTH, VALUES, workspace_bytes, workspace_tree


def setup_workspace(store):
    samples = [
        Sample(
            name=name,
            event_count=len(values),
            channels=[Channel(name="X"), Channel(name="Y")],
            tags={"dose": str(index + 2)},
            metadata={"tot": str(len(values))},
        )
        for index, (name, values) in enumerate(zip(NAMES, VALUES, strict=True))
    ]
    old = Gate(sample_id=samples[0].id, name="Cells", kind="range", x="X", bounds=[1000, 2000])
    doc = Workspace(
        name="WSP literal table truth",
        samples=list(reversed(samples)),
        gates=[old],
        tables=[TableDefinition(name="WSP Main")],
    )
    for sample, values in zip(samples, VALUES, strict=True):
        sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    created = store.create(doc)
    samples = [next(s for s in created.samples if s.id == original.id) for original in samples]
    return created, samples, Engine(store)


def import_tables(doc, samples, engine, xml=None, **options):
    plan = parse_document(xml or workspace_bytes(), "tables.wsp")
    request = ImportApply(
        revision=doc.revision,
        preview_id=new_id(),
        mappings=[
            ImportMapping(source_id=str(i), sample_ids=[s.id]) for i, s in enumerate(samples, 1)
        ],
        allow_partial=True,
        include_display_settings=False,
        **options,
    )
    record = apply_document(doc, plan, request, engine)
    return plan, record, doc.tables[-2] if request.include_tables else None


def test_literal_values_bind_new_gates_fixed_matrices_and_source_order(store):
    doc, samples, engine = setup_workspace(store)
    _, record, table = import_tables(doc, samples, engine)
    assert len(record.table_ids) == 2
    assert table.name == "WSP Main (2)"
    assert table.sample_ids == [s.id for s in samples]
    assert all(s.compensation_id is None for s in samples)
    output = evaluate_table(doc, engine, table)
    assert [r["sample_id"] for r in output["rows"]] == [s.id for s in samples]
    columns = {c.name: c.id for c in table.columns}
    for name, expected in TRUTH.items():
        actual = [r["values"][columns[name]] for r in output["rows"]]
        for got, value in zip(actual, expected, strict=True):
            assert got == pytest.approx(value) if isinstance(value, (int, float)) else got == value
    assert output["rows"][0]["status"][columns["Previous delta"]]
    assert output["rows"][1]["status"][columns["Next delta"]]
    assert samples[0].tags["dose"] == "2" and "Dose" not in samples[0].tags
    assert samples[0].tags["Batch"] == "Workspace cohort"
    assert samples[0].metadata["tot"] == "3" and "$TOT" not in samples[0].tags
    assert len(output["provenance"]["column_compensations"]) == 2
    assert any(i["code"] == "keyword-conflict" for i in record.report["issues"])
    scoped = evaluate_table(doc, engine, doc.tables[-1])
    assert scoped["total_rows"] == 1 and scoped["rows"][0]["sample_id"] == samples[0].id
    # Changing the sample's current compensation cannot replace the imported column basis.
    other_matrix = Compensation(
        name="Changed assignment", detectors=["X", "Y"], matrix=[[1, 0.1], [0, 1]]
    )
    doc.compensations.append(other_matrix)
    samples[0].compensation_id = other_matrix.id
    doc.revision += 1
    again = evaluate_table(doc, engine, table)
    assert [r["values"][columns["Raw signal"]] for r in again["rows"]] == [9, 26]
    assert [r["values"][columns["Comp signal"]] for r in again["rows"]] == [1.5, 3.5]
    # Removing the exact imported population never substitutes the preexisting lookalike.
    imported_cells = next(
        g
        for g in doc.gates
        if g.sample_id == samples[0].id and g.name == "Cells" and g.id in record.gate_ids
    )
    doc.gates = [
        g for g in doc.gates if g.id != imported_cells.id and g.parent_id != imported_cells.id
    ]
    unavailable = evaluate_table(doc, engine, table)["rows"][0]
    assert unavailable["values"][columns["Cells n"]] is None
    assert "unavailable" in unavailable["status"][columns["Cells n"]]


@pytest.mark.parametrize(
    "filename", ["8_color_ICS_with_ellipse.wsp", "8_color_ICS_boolean_gate_testing.wsp"]
)
def test_external_saved_table_frequencies_against_flowkit(filename, store):
    import flowkit

    root = Path(__file__).parent / "fixtures/interchange/flowjo"
    plan = parse_document((root / filename).read_bytes(), filename)
    assert [(t.total_columns, len(t.definition.columns)) for t in plan.tables] == [
        (15, 15),
        (12, 12),
        (12, 12),
    ]
    source = plan.sources[0]
    sample, values, matrix, _ = parse_fcs(root / source.file_name, source.file_name)
    doc = Workspace(
        name="External table reference", samples=[sample], compensations=[matrix] if matrix else []
    )
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    engine = Engine(store)
    record = apply_document(
        doc,
        plan,
        ImportApply(
            revision=0,
            preview_id=new_id(),
            allow_partial=True,
            mappings=[ImportMapping(source_id=source.id, sample_ids=[sample.id])],
        ),
        engine,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        oracle = flowkit.Workspace(str(root / filename), fcs_samples=str(root / source.file_name))
    oracle.analyze_samples(sample_id=source.file_name)
    checked, missing = 0, 0
    for table in doc.tables:
        output = evaluate_table(doc, engine, table)
        assert output["total_rows"] == 1
        row = output["rows"][0]
        for column in table.columns:
            if not column.population_path:
                assert row["values"][column.id] == sample.event_count
                checked += 1
                continue
            if sample.id in column.population_unavailable:
                assert row["values"][column.id] is None
                assert "not converted" in row["status"][column.id]
                missing += 1
                continue
            path = column.population_path
            numerator = oracle.get_gate_membership(
                source.file_name, path[-1], gate_path=("root", *path[:-1])
            ).sum()
            denominator = (
                oracle.get_gate_membership(
                    source.file_name, path[-2], gate_path=("root", *path[:-2])
                ).sum()
                if len(path) > 1
                else sample.event_count
            )
            expected = 100 * numerator / denominator if denominator else None
            assert (
                row["values"][column.id] == pytest.approx(expected)
                if expected is not None
                else row["values"][column.id] is None
            )
            checked += 1
    assert checked >= 17 and missing > 0
    assert len(record.table_ids) == 3


def test_unconverted_population_and_unmapped_control_do_not_fall_back(store):
    doc, samples, engine = setup_workspace(store)
    root = workspace_tree()
    for sample in root.find("SampleList"):
        sample.find("SampleNode/Subpopulations/Population/Gate")[0].tag = "UnsupportedShape"
    _, record, table = import_tables(doc, samples, engine, ET.tostring(root))
    output = evaluate_table(doc, engine, table)
    columns = {c.name: c.id for c in table.columns}
    assert output["rows"][0]["values"][columns["Cells n"]] is None
    assert "not converted" in output["rows"][0]["status"][columns["Cells n"]]
    assert output["rows"][0]["values"][columns["Acquired"]] == 3
    assert any(i["code"] == "table-binding-unavailable" for i in record.report["issues"])
    other = doc.model_copy(deep=True)
    plan = parse_document(workspace_bytes(), "missing-control.wsp")
    new_record = apply_document(
        other,
        plan,
        ImportApply(
            revision=other.revision,
            preview_id=new_id(),
            allow_partial=True,
            mappings=[ImportMapping(source_id="2", sample_ids=[samples[1].id])],
        ),
        engine,
    )
    selected = next(t for t in other.tables if t.id == new_record.table_ids[0])
    output = evaluate_table(other, engine, selected)
    column = next(c for c in selected.columns if c.name == "Control signal")
    assert column.control_unavailable and column.control_sample_id is None
    assert output["rows"][0]["values"][column.id] is None
    assert output["total_rows"] == 1
    assert len(new_record.table_ids) == 1  # No mapped sample in Control-only scope.


def test_spectral_aliases_and_control_formula_keep_explicit_source_bindings(store):
    doc, samples, engine = setup_workspace(store)
    root = workspace_tree()
    for sample in root.find("SampleList"):
        matrix = next(e for e in sample if e.tag.endswith("spilloverMatrix"))
        matrix.set("spectral", "1")
    for column in root.find("TableEditor/Table").findall("TColumn"):
        if column.get("rename") == "Double hidden":
            column.set("iscontrolvalue", "1")
    _, _, table = import_tables(doc, samples, engine, ET.tostring(root))
    output = evaluate_table(doc, engine, table)
    columns = {c.name: c for c in table.columns}
    assert all(
        name.startswith("Unmixed ") for name in columns["Comp signal"].channel_overrides.values()
    )
    np.testing.assert_allclose(
        [r["values"][columns["Comp signal"].id] for r in output["rows"]], [1.5, 3.5]
    )
    assert [r["values"][columns["Double hidden"].id] for r in output["rows"]] == [6, 6]
    assert [r["values"][columns["Raw signal"].id] for r in output["rows"]] == [9, 26]


def test_table_only_source_and_explicit_options_preserve_existing_annotations(store):
    doc, samples, engine = setup_workspace(store)
    root = workspace_tree()
    for sample in root.find("SampleList"):
        node = sample.find("SampleNode")
        node.remove(node.find("Subpopulations"))
    _, record, table = import_tables(
        doc, samples, engine, ET.tostring(root), include_keywords=False
    )
    assert len(record.gate_ids) == 0 and len(record.table_ids) == 2
    assert "Batch" not in samples[0].tags
    output = evaluate_table(doc, engine, table)
    acquired = next(c.id for c in table.columns if c.name == "Acquired")
    assert [r["values"][acquired] for r in output["rows"]] == [3, 4]


@pytest.mark.parametrize(
    "expression,expected",
    [
        ('<Cell column="A"/> * 2', [4, 8, 16]),
        ('<Cell column="A"/> / <Cell column="A"[1]/> * 100', [100, 200, 400]),
        ('<Cell column="A"/> - <Cell column="A"[-1]/>', [None, 2, 4]),
        ('<Cell column="A"/> - <Cell column="A"[+1]/>', [-2, -4, None]),
        ('Ifthen(<Cell column="A"/> > 3 && <Cell column="A"/> < 7, Log(100), Ln(1))', [0, 2, 0]),
        ('max(<Cell column="A"/>, 3, 5)', [5, 5, 8]),
        ('Neg(<Cell column="A"/>)', [-2, -4, -8]),
        ("ceil(1.2) + floor(1.9) + Abs(-1) + pow(0)", [5, 5, 5]),
        ('Ifthen(!<Cell column="A"/> < 3, 10, 20)', [10, 10, 10]),
        ('Ifthen(!(<Cell column="A"/> < 3), 10, 20)', [20, 10, 10]),
        ('Ifthen(!!<Cell column="A"/>, 10, 20)', [10, 10, 10]),
        ('Ifthen(!Abs(<Cell column="A"/> - 4), 10, 20)', [20, 10, 20]),
        ('Ifthen(!-<Cell column="A"/> > 1 || <Cell column="A"/> = 4, 10, 20)', [20, 10, 20]),
    ],
)
def test_documented_formula_examples_are_numeric_and_bounded(expression, expected):
    converted = convert_formula(expression)
    values = evaluate_table_formula(
        converted, lambda _name: np.array([2, 4, 8]), lambda *_: None, 3
    )
    np.testing.assert_allclose(
        values, [np.nan if x is None else x for x in expected], equal_nan=True
    )


@pytest.mark.parametrize(
    "expression",
    [
        '__import__("os").system("echo bad")',
        'open("/etc/passwd")',
        '<Cell column="A"[0]/>',
        '<Cell column="A"[50001]/>',
        '<Cell column="A"/><Image src="file:/etc/passwd"/>',
        "Round(1.5)",
        "Char(123)",
        'Num("3")',
        "'patient identifier'",
        '<Cell column="A"/> < 3 < 4',
        "!",
        "!(2",
    ],
)
def test_unsupported_or_executable_source_formulas_are_rejected(expression):
    with pytest.raises(ValueError):
        convert_formula(expression)


@pytest.mark.parametrize(
    "expression", ['row("A", True)', 'row("A", -1)', 'offset("A", col("B"))', 'offset("A", -50001)']
)
def test_native_row_references_require_literal_bounded_indices(expression):
    with pytest.raises(ValueError):
        parse_table_formula(expression)


def test_readonly_api_preview_options_archive_undo_and_source_integrity(client):
    from tools.import_fixture import fcs_dataset

    doc = client.post("/api/workspaces", json={"name": "Native WSP migration"}).json()
    base = f"/api/workspaces/{doc['id']}"
    response = client.post(
        base + "/import?revision=0",
        files=[
            ("files", (name, fcs_dataset(values, ["X", "Y"])))
            for name, values in zip(NAMES, VALUES, strict=True)
        ],
    )
    assert response.status_code == 200, response.text
    doc = response.json()["workspace"]
    before = client.get(base).json()
    source = workspace_bytes()
    preview = client.post(
        base + f"/interchange/preview?revision={doc['revision']}",
        files={"file": ("tables.wsp", source)},
    ).json()
    assert client.get(base).json() == before
    assert preview["requires_acknowledgement"]
    request = {
        "revision": doc["revision"],
        "preview_id": preview["preview_id"],
        "mappings": [
            {"source_id": str(i), "sample_ids": [sample["id"]]}
            for i, sample in enumerate(doc["samples"], 1)
        ],
    }
    assert client.post(base + "/interchange/apply", json=request).status_code == 422
    assert client.get(base).json() == before
    response = client.post(base + "/interchange/apply", json={**request, "allow_partial": True})
    assert response.status_code == 200, response.text
    doc = response.json()
    record = doc["interchanges"][-1]
    assert len(record["table_ids"]) == 2 and len(doc["tables"]) == 2
    assert client.get(base + f"/interchange/{record['id']}/source").content == source
    archive = client.get(base + "/export/project").content
    restored = client.post("/api/import/project", files={"file": ("table.cytoforge", archive)})
    assert restored.status_code == 200, restored.text
    restored_doc = restored.json()
    assert restored_doc["tables"] == doc["tables"]
    assert restored_doc["interchanges"] == doc["interchanges"]
    restored_base = f"/api/workspaces/{restored_doc['id']}"
    assert client.get(restored_base + f"/interchange/{record['id']}/source").content == source
    undo = client.post(base + "/undo", json={"revision": doc["revision"]}).json()
    assert not undo["tables"] and not undo["gates"]
    redo = client.post(base + "/redo", json={"revision": undo["revision"]}).json()
    assert redo["tables"] == doc["tables"]
    # Explicitly opting out preserves gate migration and leaves native tables unchanged.
    preview = client.post(
        base + f"/interchange/preview?revision={redo['revision']}",
        files={"file": ("tables.wsp", source)},
    ).json()
    response = client.post(
        base + "/interchange/apply",
        json={
            **request,
            "revision": redo["revision"],
            "preview_id": preview["preview_id"],
            "allow_partial": True,
            "include_tables": False,
            "include_keywords": False,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["tables"] == redo["tables"]
    assert response.json()["interchanges"][-1]["table_ids"] == []
