"""Independent numerical and portable-spreadsheet references for saved tables."""

import csv
import io
import json
import math
import time
import zipfile
from xml.etree import ElementTree as ET

import numpy as np
import pytest
from cytoforge.models import (
    Channel,
    DerivedParameter,
    FitConstraint,
    Gate,
    Group,
    Sample,
    TableColumn,
    TableComparison,
    TableDefinition,
    TablePivot,
    Workspace,
    new_id,
)
from cytoforge.science import Engine, save_events
from cytoforge.table_expressions import evaluate_table_formula
from cytoforge.tables import adjust_pvalues, evaluate_table
from scipy import stats
from test_biology_management import initial_model


def experiment(store):
    samples = []
    gates = []
    arrays = []
    for index, mean in enumerate([1, 2, 4, 6, 8, 11]):
        sample = Sample(
            name=f"Sample {index}",
            channels=[Channel(name="X"), Channel(name="Y")],
            event_count=6,
            tags={
                "Treatment": "A" if index < 3 else "B",
                "Donor": str(index % 3),
                "Dose": str(mean),
            },
        )
        values = np.c_[
            np.array([mean - 1, mean, mean + 1, mean + 2, mean + 3, np.nan]), np.arange(6)
        ]
        samples.append(sample)
        arrays.append(values)
        gates.extend(
            [
                Gate(sample_id=sample.id, name="Cells", kind="range", x="Y", bounds=[1, 5]),
                Gate(sample_id=sample.id, name="Empty", kind="range", x="Y", bounds=[20, 30]),
            ]
        )
    workspace = Workspace(
        name="Table references",
        samples=samples,
        gates=gates,
        groups=[Group(name="Group A", sample_ids=[s.id for s in samples[:3]])],
    )
    for sample, values in zip(samples, arrays, strict=True):
        sample.sha256 = save_events(store.data_path(workspace.id, sample.id), values)
    workspace = store.create(workspace)
    return workspace, Engine(store), arrays


def definition(**settings):
    columns = [
        TableColumn(name="Events", statistic="count", population_path=["Cells"], decimals=0),
        TableColumn(name="Median", statistic="median", channel="X", population_path=["Cells"]),
        TableColumn(name="Treatment", kind="metadata", metadata_key="Treatment"),
        TableColumn(name="Donor", kind="metadata", metadata_key="Donor"),
        TableColumn(name="Dose", kind="metadata", metadata_key="Dose", metadata_numeric=True),
        TableColumn(name="Double", kind="formula", expression='col("Median") * 2'),
        TableColumn(
            name="Relative", kind="formula", expression='col("Double") / mean(col("Double"))'
        ),
    ]
    return TableDefinition(name="Response", row_mode="samples", columns=columns, **settings)


def test_population_columns_formulas_rename_bindings_and_scopes(store):
    workspace, engine, arrays = experiment(store)
    table = definition()
    output = evaluate_table(workspace, engine, table)
    median, double, relative = [table.columns[i].id for i in (1, 5, 6)]
    truth = [np.median(a[1:5, 0]) for a in arrays]
    assert len(output["rows"]) == 6
    np.testing.assert_allclose([r["values"][median] for r in output["rows"]], truth)
    np.testing.assert_allclose(
        [r["values"][relative] for r in output["rows"]], np.array(truth) / np.mean(truth)
    )
    assert all(r["values"][table.columns[0].id] == 4 for r in output["rows"])
    stored = output["definition"]
    stored["columns"][1]["name"] = "Renamed median"
    restored = TableDefinition.model_validate(stored)
    assert restored.columns[5].formula_refs["Median"] == median
    assert evaluate_table(workspace, engine, restored)["rows"][0]["values"][double] == truth[0] * 2
    table.group_id = workspace.groups[0].id
    grouped = evaluate_table(workspace, engine, table)
    assert grouped["total_rows"] == 3
    np.testing.assert_allclose(
        [r["values"][relative] for r in grouped["rows"]], np.array(truth[:3]) / np.mean(truth[:3])
    )
    table.group_id = new_id()
    assert evaluate_table(workspace, engine, table)["total_rows"] == 0


def test_missing_ambiguous_populations_controls_and_undefined_values(store):
    workspace, engine, _ = experiment(store)
    table = definition()
    duplicate = workspace.gates[0].model_copy(update={"id": new_id()})
    workspace.gates.append(duplicate)
    table.columns += [
        TableColumn(name="No cells", statistic="percent_parent", population_path=["Empty"]),
        TableColumn(name="Missing", statistic="median", channel="MISSING"),
        TableColumn(name="Zero denominator", kind="formula", expression='col("Events") / 0'),
        TableColumn(
            name="Safe fallback", kind="formula", expression='coalesce(col("Zero denominator"), 7)'
        ),
        TableColumn(
            name="Control",
            statistic="median",
            channel="X",
            population_path=["Cells"],
            control_sample_id=workspace.samples[1].id,
        ),
    ]
    table = TableDefinition.model_validate(table.model_dump())
    output = evaluate_table(workspace, engine, table)
    first = output["rows"][0]
    assert first["values"][table.columns[0].id] is None
    assert "ambiguous" in first["status"][table.columns[0].id]
    assert first["values"][table.columns[-4].id] is None
    assert first["values"][table.columns[-2].id] == 7
    assert first["values"][table.columns[-1].id] == 3.5
    table.columns[0].population_overrides[workspace.samples[0].id] = duplicate.id
    assert evaluate_table(workspace, engine, table)["rows"][0]["values"][table.columns[0].id] == 4


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os')",
        "col('A').__class__",
        "col('A')[0]",
        "[x for x in (1,2)]",
        "open('x')",
        "2 ** (1e309)",
    ],
)
def test_formula_language_rejects_arbitrary_code(expression):
    with pytest.raises(ValueError):
        evaluate_table_formula(expression, lambda _: np.arange(4), lambda *_: np.arange(4), 4)


def test_formula_cycles_hidden_helpers_string_conditions_and_control_rows(store):
    workspace, engine, _ = experiment(store)
    table = definition()
    table.columns += [
        TableColumn(
            name="Condition",
            kind="formula",
            expression='ifelse(col("Treatment") == "A", col("Dose"), 0)',
            hidden=True,
        ),
        TableColumn(
            name="Control ratio",
            kind="formula",
            expression='col("Median") / control("Median", "Sample 0")',
        ),
    ]
    table = TableDefinition.model_validate(table.model_dump())
    result = evaluate_table(workspace, engine, table)
    assert [r["values"][table.columns[-2].id] for r in result["rows"]] == [1, 2, 4, 0, 0, 0]
    assert result["rows"][0]["values"][table.columns[-1].id] == 1
    raw = table.model_dump()
    raw["columns"][1].update(kind="formula", expression='col("Double")', formula_refs={})
    with pytest.raises(ValueError, match="cycle"):
        TableDefinition.model_validate(raw)


def test_pivot_aggregates_exact_selected_sample_values_and_missing_replicates(store):
    workspace, engine, arrays = experiment(store)
    table = definition()
    measure = table.columns[1].id
    table.pivot = TablePivot(
        rows=[table.columns[2].id], columns=[], measures=[measure], aggregation="mean"
    )
    output = evaluate_table(workspace, engine, table)["pivot"]
    assert len(output["rows"]) == 2
    identifier = output["columns"][0]["id"]
    assert output["rows"][0]["values"][identifier] == pytest.approx(
        np.mean([np.median(a[1:5, 0]) for a in arrays[:3]]), rel=1e-13
    )
    assert output["rows"][0]["counts"][identifier] == 3
    table.pivot.columns = [table.columns[3].id]
    table.pivot.aggregation = "std"
    output = evaluate_table(workspace, engine, table)["pivot"]
    assert all(v is None for r in output["rows"] for v in r["values"].values())


@pytest.mark.parametrize("method", ["welch", "mann_whitney", "paired_t", "wilcoxon"])
def test_comparisons_use_samples_match_independent_scipy_and_multiplicity(store, method):
    workspace, engine, arrays = experiment(store)
    table = definition()
    table.comparison = TableComparison(
        group_column=table.columns[2].id,
        group_a="A",
        group_b="B",
        measures=[table.columns[1].id, table.columns[5].id],
        method=method,
        pair_column=table.columns[3].id,
    )
    a, b = [np.array([np.median(v[1:5, 0]) for v in part]) for part in (arrays[:3], arrays[3:])]
    expected = {
        "welch": lambda: stats.ttest_ind(a, b, equal_var=False),
        "mann_whitney": lambda: stats.mannwhitneyu(a, b, method="auto"),
        "paired_t": lambda: stats.ttest_rel(a, b),
        "wilcoxon": lambda: stats.wilcoxon(a - b, method="auto"),
    }[method]()
    tested = evaluate_table(workspace, engine, table)["comparisons"]
    assert tested[0]["n_a"] == tested[0]["n_b"] == 3
    assert tested[0]["statistic"] == pytest.approx(expected.statistic)
    assert tested[0]["p_value"] == pytest.approx(expected.pvalue)
    assert tested[0]["adjusted_p_value"] == pytest.approx(min(2 * expected.pvalue, 1))
    assert tested[0]["mean_difference"] == pytest.approx(np.mean(a) - np.mean(b))
    if method in {"welch", "paired_t"}:
        np.testing.assert_allclose(
            tested[0]["confidence_interval"], list(expected.confidence_interval())
        )
    assert adjust_pvalues([0.01, 0.03, 0.04], "holm") == pytest.approx([0.03, 0.06, 0.06])
    assert adjust_pvalues([0.01, 0.03, 0.04], "benjamini_hochberg") == pytest.approx(
        [0.03, 0.04, 0.04]
    )


def test_duplicated_pairs_and_nonindependent_population_comparisons_rejected(store):
    workspace, engine, _ = experiment(store)
    workspace.samples[1].tags["Donor"] = "0"
    table = definition()
    table.comparison = TableComparison(
        group_column=table.columns[2].id,
        group_a="A",
        group_b="B",
        measures=[table.columns[1].id],
        method="paired_t",
        pair_column=table.columns[3].id,
    )
    assert "duplicated" in evaluate_table(workspace, engine, table)["comparisons"][0]["issue"]
    table.row_mode = "populations"
    with pytest.raises(ValueError, match="one row per sample"):
        TableDefinition.model_validate(table.model_dump())


def test_live_definition_save_export_full_csv_xlsx_json_revision_and_archive(client):
    workspace, _, _ = experiment(client.app.state.store)
    table = definition()
    table.columns.append(
        TableColumn(name="Helper", kind="formula", expression='col("Median")+1', hidden=True)
    )
    table.pivot = TablePivot(rows=[table.columns[2].id], measures=[table.columns[1].id])
    base = f"/api/workspaces/{workspace.id}"
    saved = client.post(
        f"{base}/tables/save",
        json={"revision": workspace.revision, "definition": table.model_dump()},
    )
    assert saved.status_code == 200, saved.text
    doc = saved.json()
    body = {"definition": doc["tables"][0], "revision": doc["revision"], "limit": 1}
    evaluated = client.post(f"{base}/tables/evaluate", json=body)
    assert evaluated.status_code == 200, evaluated.text
    assert len(evaluated.json()["rows"]) == 1 and evaluated.json()["total_rows"] == 6
    exported = client.post(f"{base}/tables/export/csv", json=body)
    assert exported.status_code == 200, exported.text
    rows = list(csv.reader(io.StringIO(exported.text)))
    assert len(rows) == 7 and "Helper" not in rows[0]
    xlsx = client.post(f"{base}/tables/export/xlsx", json=body)
    assert xlsx.status_code == 200, xlsx.text
    with zipfile.ZipFile(io.BytesIO(xlsx.content)) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        assert {s.attrib["name"] for s in workbook.find("s:sheets", ns)} == {
            "Data",
            "Cell status",
            "Pivot",
            "Provenance",
        }
        data = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        assert len(data.find("s:sheetData", ns)) == 7
        assert not data.findall(".//s:f", ns)
    assert not list((client.app.state.store.root / "tmp").glob("*.xlsx"))
    exported = client.post(f"{base}/tables/export/json", json=body)
    assert len(exported.json()["rows"]) == 6
    assert (
        client.post(
            f"{base}/tables/evaluate", json=body | {"revision": doc["revision"] - 1}
        ).status_code
        == 409
    )
    archive = client.get(f"{base}/export/project")
    imported = client.post(
        "/api/import/project", files={"file": ("table.cytoforge", archive.content)}
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["tables"][0] == doc["tables"][0]
    deleted = client.request(
        "DELETE", f"{base}/tables/{table.id}", json={"revision": doc["revision"]}
    )
    assert deleted.status_code == 200 and not deleted.json()["tables"]
    undo = client.post(f"{base}/undo", json={"revision": deleted.json()["revision"]})
    assert undo.status_code == 200 and len(undo.json()["tables"]) == 1


def test_large_finite_statistics_keep_representable_values_and_unrepresentable_variance_null(store):
    values = np.array([8e307, 9e307, 1e308, 1.1e308])[:, None]
    sample = Sample(name="Large values", event_count=4, channels=[Channel(name="X")])
    workspace = Workspace(name="Large statistics", samples=[sample])
    sample.sha256 = save_events(store.data_path(workspace.id, sample.id), values)
    workspace = store.create(workspace)
    engine = Engine(store)
    summary = engine.summary(workspace, sample, None, "X")
    assert math.isfinite(summary["mean"]) and summary["mean"] == pytest.approx(9.5e307)
    table = TableDefinition(
        name="Large",
        row_mode="samples",
        columns=[TableColumn(name="Var", statistic="variance", channel="X")],
    )
    result = evaluate_table(workspace, engine, table)
    assert result["rows"][0]["values"][table.columns[0].id] is None


def spreadsheet_rows(archive):
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    output = {}
    for index, sheet in enumerate(workbook.find("s:sheets", ns), 1):
        xml = ET.fromstring(archive.read(f"xl/worksheets/sheet{index}.xml"))
        assert not xml.findall(".//s:f", ns)
        output[sheet.attrib["name"]] = [
            {c.attrib["r"]: "".join(c.itertext()) for c in row}
            for row in xml.find("s:sheetData", ns)
        ]
    return output


@pytest.mark.parametrize("method", ["welch", "paired_t", "mann_whitney", "wilcoxon"])
def test_comparisons_preserve_original_ranks_ties_and_large_common_offsets(store, method):
    workspace, engine, _ = experiment(store)
    if method == "wilcoxon":
        a, b = np.array([0.0, 2.0, 4.0]), np.array([1.0, 1.0, 5.0])
        truth = stats.wilcoxon(a - b, method="auto")
    elif method == "mann_whitney":
        base = 1e308
        step = np.spacing(base)
        a = base + step * np.arange(3)
        b = np.array([base + 3 * step, base + 4 * step, 1.7e308])
        truth = stats.mannwhitneyu(a, b, method="auto")
    else:
        original_a, original_b = np.array([0.0, 1.0, 2.0]), np.array([3.0, 4.0, 6.0])
        a, b = 1e15 + original_a, 1e15 + original_b
        truth = (
            stats.ttest_ind(original_a, original_b, equal_var=False)
            if method == "welch"
            else stats.ttest_rel(original_a, original_b)
        )
    for sample, value in zip(workspace.samples, [*a, *b], strict=True):
        sample.tags["Dose"] = str(value)
    table = definition()
    table.comparison = TableComparison(
        group_column=table.columns[2].id,
        group_a="A",
        group_b="B",
        measures=[table.columns[4].id],
        method=method,
        pair_column=table.columns[3].id,
    )
    output = evaluate_table(workspace, engine, table)["comparisons"][0]
    assert output["statistic"] == pytest.approx(truth.statistic, rel=1e-12)
    assert output["p_value"] == pytest.approx(truth.pvalue, rel=1e-12)
    if method in {"welch", "paired_t"}:
        assert output["mean_difference"] == pytest.approx(
            np.mean(original_a) - np.mean(original_b), abs=1e-12
        )
        interval = truth.confidence_interval()
        np.testing.assert_allclose(
            output["confidence_interval"], [interval.low, interval.high], rtol=1e-12
        )


def test_spreadsheets_preserve_long_unicode_values_definitions_and_split_status_sheets(
    store, tmp_path, monkeypatch
):
    from cytoforge import tables

    workspace, engine, _ = experiment(store)
    text = '=HYPERLINK("https://example.invalid")\n' + "😀x" * 24000
    workspace.samples[0].name = "=A1+1"
    workspace.samples[0].tags["Long"] = text
    table = definition()
    table.columns.extend(
        [
            TableColumn(name="=header", kind="metadata", metadata_key="Long"),
            TableColumn(name="Missing", statistic="median", channel="missing"),
        ]
    )
    # A valid large table definition cannot fit in one Excel text cell either.
    for index in range(40):
        table.columns.append(
            TableColumn(
                name=f"Helper {index}",
                kind="formula",
                hidden=True,
                expression=" + ".join(["1"] * 60),
                population_overrides={s.id: None for s in workspace.samples},
            )
        )
    table = TableDefinition.model_validate(table.model_dump())
    output = evaluate_table(workspace, engine, table, limit=50000)
    assert len(json.dumps(output["definition"], ensure_ascii=False)) > 32767
    csv_rows = list(csv.reader(io.StringIO(tables.csv_text(output))))
    assert csv_rows[1][0] == "'=A1+1" and csv_rows[0][-2] == "'=header"
    assert csv_rows[1][-2] == "'" + text
    path = tmp_path / "long.xlsx"
    monkeypatch.setattr(tables, "MAX_EXCEL_ROWS", 4)
    tables.write_xlsx(path, output, tmp_path)
    with zipfile.ZipFile(path) as archive:
        sheets = spreadsheet_rows(archive)
    assert sheets["Data"][1]["A2"] == "=A1+1"
    assert "Full text" in sheets["Data"][1]["L2"]
    chunks = {}
    statuses = []
    for name, rows in sheets.items():
        if name.startswith("Long text"):
            assert len(rows) <= 4
            for row in rows[1:]:
                parts = list(row.values())
                chunks.setdefault((parts[0], parts[1]), []).append((int(parts[2]), parts[3]))
        if name.startswith("Cell status"):
            assert len(rows) <= 4
            statuses.extend(rows[1:])
    restored = {key: "".join(v for _, v in sorted(parts)) for key, parts in chunks.items()}
    assert restored[("Data", "L2")] == text
    assert json.loads(restored[("Provenance", "B3")]) == output["definition"]
    assert json.loads(restored[("Provenance", "B4")]) == output["provenance"]
    assert len(statuses) == sum(len(r["status"]) for r in output["rows"])


def test_global_ranges_scope_empty_rows_and_unambiguous_pivot_labels(store):
    workspace, engine, arrays = experiment(store)
    table = definition()
    median = table.columns[1].id
    output = evaluate_table(workspace, engine, table, offset=4, limit=1)
    assert output["column_ranges"][median] == [2.5, 12.5]
    assert len(output["rows"]) == 1
    assert output["provenance"]["samples"][0]["sha256"] == workspace.samples[0].sha256
    assert output["provenance"]["software"]["numpy"] == np.__version__
    workspace.samples[0].tags.pop("Donor")
    workspace.samples[1].tags["Donor"] = "(missing)"
    table.pivot = TablePivot(columns=[table.columns[3].id], measures=[median])
    headers = evaluate_table(workspace, engine, table)["pivot"]["columns"]
    assert len({c["name"] for c in headers}) == len(headers)
    assert {c["dimension"][0] for c in headers} >= {None, "(missing)"}
    table.filter = "No such sample"
    empty = evaluate_table(workspace, engine, table)
    assert empty["rows"] == [] and empty["column_ranges"][median] is None
    assert empty["pivot"]["rows"] == []


@pytest.mark.parametrize("platform", ["cell-cycle", "proliferation"])
def test_biological_tables_metrics_recursive_staleness_replacement_removal_and_archive(
    client, platform
):
    base, doc, sample, request, identifier = initial_model(client, platform)
    store = client.app.state.store
    field = "cell_cycle_results" if platform == "cell-cycle" else "proliferation_results"
    metric = "g1_cv" if platform == "cell-cycle" else "dye_cv"
    old = getattr(doc, field)[0]
    phase_gate = doc.gates[0]
    live = TableColumn(
        name="Live CV",
        kind="biology",
        platform=platform,
        result_id=identifier,
        biology_metric=metric,
    )
    historical = live.model_copy(
        update={"id": new_id(), "name": "Historical CV", "follow_replacement": False}
    )
    expected = TableColumn(
        name="Posterior count",
        kind="biology",
        platform=platform,
        result_id=identifier,
        biology_metric="expected_count",
        generation=1,
    )
    fraction = expected.model_copy(
        update={"id": new_id(), "name": "Fraction", "biology_metric": "fraction"}
    )
    table = TableDefinition(
        name="Biology report",
        row_mode="samples",
        columns=[
            live,
            historical,
            expected,
            fraction,
            TableColumn(
                name="Assigned population", statistic="count", population_path=[phase_gate.name]
            ),
            TableColumn(name="Derived probability", statistic="median", channel="Nested score"),
            TableColumn(
                name="Population count", statistic="count", population_path=["Score population"]
            ),
        ],
    )

    def connect(w):
        source = w.samples[0]
        source.derived_parameters.extend(
            [
                DerivedParameter(name="Score", expression=f'ch("{old.columns[0]}") + 1'),
                DerivedParameter(name="Nested score", expression='ch("Score") * 2'),
            ]
        )
        source.channels.extend([Channel(name="Score"), Channel(name="Nested score")])
        w.gates.append(
            Gate(
                name="Score population",
                sample_id=sample.id,
                kind="range",
                x="Nested score",
                bounds=[2, 4],
            )
        )
        w.tables.append(table)

    doc = store.mutate(doc.id, "Connect biological table", connect, doc.revision)
    convergence = TableColumn(
        name="Converged",
        kind="biology",
        platform=platform,
        result_id=identifier,
        biology_metric="converged",
    )
    doc = store.mutate(
        doc.id,
        "Add convergence metric",
        lambda w: w.tables[0].columns.append(convergence),
        doc.revision,
    )
    output = evaluate_table(doc, Engine(store), doc.tables[0])
    values = output["rows"][0]["values"]
    assert values[expected.id] == pytest.approx(old.fits[0].expected_counts[1])
    assert values[fraction.id] == pytest.approx(old.fits[0].fractions[1])
    assert values[convergence.id] == int(old.fits[0].diagnostics["converged"])
    assert type(values[convergence.id]) is int
    assert output["provenance"]["models"][0]["id"] == identifier
    altered = doc.model_copy(deep=True)
    # An external raw-data revision changes the scientific input fingerprint.
    altered.samples[0].sha256 = "f" * 64
    stale = evaluate_table(altered, Engine(store), doc.tables[0])["rows"][0]
    assert all(v is None for v in stale["values"].values())
    allowed = doc.tables[0].model_copy(deep=True)
    for col in allowed.columns:
        col.allow_stale = True
    retained = evaluate_table(altered, Engine(store), allowed)["rows"][0]
    assert all(v is not None for v in retained["values"].values())
    assert all("Stale" in reason for reason in retained["status"].values())
    replacement = request.model_copy(
        update={
            "revision": doc.revision,
            "replace_result_id": identifier,
            "name": "Replacement model",
            "g1_cv" if platform == "cell-cycle" else "dye_cv": FitConstraint(
                fixed=4.2 if platform == "cell-cycle" else 24
            ),
        }
    )
    response = client.post(f"{base}/jobs", json=replacement.model_dump())
    assert response.status_code == 202, response.text
    replacement_id = response.json()["id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = client.get(f"{base}/jobs/{replacement_id}").json()
        if job["status"] not in {"queued", "running"}:
            break
        time.sleep(0.04)
    assert job["status"] == "succeeded", job.get("error")
    response = client.post(f"{base}/jobs/{replacement_id}/apply", json={"revision": doc.revision})
    assert response.status_code == 200, response.text
    doc = Workspace.model_validate(response.json())
    assert doc.tables[0].columns[0].result_id == replacement_id
    assert doc.tables[0].columns[1].result_id == identifier
    assert doc.tables[0].columns[4].population_path[0].startswith("Replacement model · ")
    renamed = client.patch(
        f"/api/workspaces/{doc.id}/biology/{platform}/{replacement_id}",
        json={"revision": doc.revision, "name": "Reviewed model"},
    )
    assert renamed.status_code == 200, renamed.text
    doc = Workspace.model_validate(renamed.json())
    assert doc.tables[0].columns[4].population_path[0].startswith("Reviewed model · ")
    output = evaluate_table(doc, Engine(store), doc.tables[0])
    assert output["rows"][0]["values"][table.columns[4].id] == int(
        Engine(store).mask(doc, doc.samples[0], phase_gate.id).sum()
    )
    assert output["rows"][0]["values"][live.id] == pytest.approx(
        4.2 if platform == "cell-cycle" else 24
    )
    assert output["rows"][0]["values"][historical.id] == pytest.approx(
        old.fits[0].parameters[metric]
    )
    old_path = f"/api/workspaces/{doc.id}/biology/{platform}/{identifier}"
    preview = client.get(f"{old_path}/dependencies").json()
    assert preview["tables"][0]["columns"] == [historical.id]
    response = client.request(
        "DELETE",
        old_path,
        json={"revision": doc.revision, "cascade": True, "review_hash": preview["review_hash"]},
    )
    assert response.status_code == 200, response.text
    doc = Workspace.model_validate(response.json())
    output = evaluate_table(doc, Engine(store), doc.tables[0])
    assert output["rows"][0]["values"][live.id] is not None
    assert output["rows"][0]["values"][historical.id] is None
    new_path = f"/api/workspaces/{doc.id}/biology/{platform}/{replacement_id}"
    preview = client.get(f"{new_path}/dependencies").json()
    assert set(preview["tables"][0]["columns"]) == {
        live.id,
        expected.id,
        fraction.id,
        table.columns[4].id,
        table.columns[-2].id,
        table.columns[-1].id,
        convergence.id,
    }
    response = client.request(
        "DELETE",
        new_path,
        json={"revision": doc.revision, "cascade": True, "review_hash": preview["review_hash"]},
    )
    assert response.status_code == 200, response.text
    removed = Workspace.model_validate(response.json())
    assert all(
        v is None
        for v in evaluate_table(removed, Engine(store), removed.tables[0])["rows"][0][
            "values"
        ].values()
    )
    restored = client.post(f"/api/workspaces/{doc.id}/undo", json={"revision": removed.revision})
    assert restored.status_code == 200, restored.text
    doc = Workspace.model_validate(restored.json())
    archive = client.get(f"/api/workspaces/{doc.id}/export/project")
    imported = client.post(
        "/api/import/project", files={"file": ("table.cytoforge", archive.content)}
    )
    assert imported.status_code == 200, imported.text
    copied = Workspace.model_validate(imported.json())
    assert copied.tables == doc.tables
    assert (
        evaluate_table(copied, Engine(store), copied.tables[0])["rows"][0]["values"][live.id]
        == output["rows"][0]["values"][live.id]
    )
