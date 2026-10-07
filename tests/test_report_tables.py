"""Independent cohort truth across automatic row/column report continuation."""

import io
import json
import math
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
import pytest
from cytoforge import report_tables, reports
from cytoforge.models import (
    Channel,
    LayoutDefinition,
    ReportBatch,
    ReportElement,
    ReportPage,
    Sample,
    TableColumn,
    TableComparison,
    TableDefinition,
    TablePivot,
    Workspace,
)
from cytoforge.science import Engine, save_events
from cytoforge.store import ConflictError
from scipy import stats


def cohort(store):
    samples = [
        Sample(
            name=f"Acquisition {i + 1}",
            channels=[Channel(name="X")],
            event_count=i + 1,
            tags={"Donor": f"D{i % 4}", "Treatment": "A" if i < 4 else "B"},
        )
        for i in range(8)
    ]
    donor = TableColumn(name="Donor", kind="metadata", metadata_key="Donor")
    treatment = TableColumn(name="Treatment", kind="metadata", metadata_key="Treatment")
    signal = TableColumn(name="Signal", kind="statistic", statistic="mean", channel="X")
    count = TableColumn(name="Events", kind="statistic", statistic="count", decimals=0)
    zero = TableColumn(name="Zero", kind="formula", expression="0", decimals=0)
    table = TableDefinition(
        name="Independent cohort",
        row_mode="samples",
        columns=[donor, treatment, signal, count, zero],
        pivot=TablePivot(
            rows=[donor.id], columns=[treatment.id], measures=[signal.id, count.id, zero.id]
        ),
        comparison=TableComparison(
            group_column=treatment.id,
            group_a="A",
            group_b="B",
            measures=[signal.id, count.id, zero.id],
        ),
    )
    doc = Workspace(name="Report continuation truth", samples=samples, tables=[table])
    for i, sample in enumerate(samples):
        sample.sha256 = save_events(
            store.data_path(doc.id, sample.id), np.full((i + 1, 1), i + 1, dtype=float)
        )
    return store.create(doc), Engine(store)


@pytest.fixture
def report_cohort(store):
    return cohort(store)


def composition(doc, view="data", **changes):
    table = doc.tables[0]
    element = ReportElement(
        kind="table",
        table_id=table.id,
        table_view=view,
        auto_paginate=True,
        iterate=False,
        title=f"Cohort {view}",
        width_mm=180,
        height_mm=80,
        row_count=2,
        columns_per_page=2,
        **changes,
    )
    return LayoutDefinition(name="Full cohort report", elements=[element])


def pages(doc, engine, layout):
    plan = reports.plan(doc, layout, engine)
    output = [
        reports.render(
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
        for index in range(plan["page_count"])
    ]
    assert all(page["exportable"] for page in output), [page["issues"] for page in output]
    return plan, output


def test_raw_continuation_has_every_selected_cell_exactly_once(report_cohort):
    doc, engine = report_cohort
    layout = composition(doc, column_ids=[c.id for c in doc.tables[0].columns[2:]])
    plan, output = pages(doc, engine, layout)
    assert plan["page_count"] == 8
    cells = {}
    for page in output:
        frame = page["manifest"]["elements"][0]
        assert frame["total_rows"] == frame["input_rows"] == 8
        assert frame["total_columns"] == 3
        for row in frame["rows"]:
            for column in frame["columns"]:
                key = row["sample_id"], column["id"]
                assert key not in cells
                cells[key] = row["values"][column["id"]]
    assert len(cells) == 24
    for index, sample in enumerate(doc.samples):
        for column in doc.tables[0].columns[2:]:
            assert cells[sample.id, column.id] == (0 if column.name == "Zero" else index + 1)
    assert len({p["manifest"]["elements"][0]["snapshot_sha256"] for p in output}) == 1


def test_pivot_continuation_preserves_full_cohort_aggregation_counts_and_headers(report_cohort):
    doc, engine = report_cohort
    layout = composition(doc, "pivot")
    plan, output = pages(doc, engine, layout)
    assert plan["page_count"] == 6
    cells = {}
    for page in output:
        frame = page["manifest"]["elements"][0]
        assert frame["total_rows"] == 4 and frame["input_rows"] == 8
        assert frame["total_columns"] == 6 and 'Treatment="' in page["svg"]
        for row in frame["rows"]:
            for column in frame["columns"]:
                donor = int(row["group"][0][1:])
                key = donor, column["dimension"][0], column["measure"]
                assert key not in cells
                cells[key] = row["values"][column["id"]], row["counts"][column["id"]]
    assert len(cells) == 24
    for (donor, condition, measure), (value, count) in cells.items():
        expected = donor + (1 if condition == "A" else 5)
        assert value == (0 if measure == doc.tables[0].columns[-1].id else expected)
        assert count == 1


def test_comparison_column_pages_keep_full_adjustment_family_and_sample_sizes(report_cohort):
    doc, engine = report_cohort
    signal = doc.tables[0].columns[2]
    layout = composition(
        doc,
        "comparisons",
        column_ids=[signal.id],
        comparison_fields=[
            "n_a",
            "n_b",
            "mean_difference",
            "adjusted_p_value",
            "confidence_interval",
        ],
    )
    plan, output = pages(doc, engine, layout)
    assert plan["page_count"] == 3
    expected_t = -4 / math.sqrt(5 / 6)
    expected_p = 2 * stats.t.sf(abs(expected_t), df=6)
    for page in output:
        frame = page["manifest"]["elements"][0]
        record = frame["rows"][0]
        assert record["n_a"] == record["n_b"] == 4
        assert record["mean_a"] == 2.5 and record["mean_b"] == 6.5
        assert record["mean_difference"] == pytest.approx(-4)
        assert record["statistic"] == pytest.approx(expected_t)
        assert record["p_value"] == pytest.approx(expected_p)
        # Both Signal and Events are valid in the saved family, even though the
        # report only selects Signal. The constant Zero measure is undefined.
        assert record["adjusted_p_value"] == pytest.approx(expected_p * 2)
        margin = stats.t.ppf(0.975, 6) * math.sqrt(5 / 6)
        assert record["confidence_interval"] == pytest.approx([-4 - margin, -4 + margin])
        assert 'A="A"' in frame["description"] and "95% CI" in frame["description"]


def test_undefined_comparison_is_labelled_and_retains_reason(report_cohort):
    doc, engine = report_cohort
    layout = composition(
        doc,
        "comparisons",
        column_ids=[doc.tables[0].columns[-1].id],
        comparison_fields=["p_value", "adjusted_p_value"],
    )
    _, output = pages(doc, engine, layout)
    assert len(output) == 1
    frame = output[0]["manifest"]["elements"][0]
    assert frame["rows"][0]["p_value"] is None
    assert "degenerate" in frame["rows"][0]["issue"]
    assert "Undefined" in output[0]["svg"] and "source issues" in output[0]["svg"]


def test_positive_p_values_below_display_precision_do_not_appear_as_zero(report_cohort):
    doc, engine = report_cohort
    for index, sample in enumerate(doc.samples[4:], 5):
        sample.sha256 = save_events(
            engine.store.data_path(doc.id, sample.id),
            np.full((sample.event_count, 1), index + 18, dtype=float),
        )
    layout = composition(
        doc,
        "comparisons",
        column_ids=[doc.tables[0].columns[2].id],
        comparison_fields=["p_value", "adjusted_p_value"],
    )
    _, output = pages(doc, engine, layout)
    record = output[0]["manifest"]["elements"][0]["rows"][0]
    expected = stats.ttest_ind([1, 2, 3, 4], [23, 24, 25, 26], equal_var=False).pvalue
    assert record["p_value"] == pytest.approx(expected)
    assert 1e-7 < expected < 1e-4
    labels = [node.text for node in ET.fromstring(output[0]["svg"]).findall(".//{*}text")]
    assert "0.0000" not in labels
    assert format(expected, ".6g") in labels
    assert format(expected * 2, ".6g") in labels


def test_first_row_column_and_multiple_independent_tables(report_cohort):
    doc, engine = report_cohort
    layout = composition(doc, row_start=3, column_start=2)
    short = layout.elements[0].model_copy(deep=True)
    from cytoforge.models import new_id

    short.id = new_id()
    short.row_start = 7
    short.column_start = 4
    short.y_mm = 130
    layout.elements.append(short)
    plan, output = pages(doc, engine, layout)
    assert plan["page_count"] == 6  # five remaining rows × three remaining columns
    assert (
        sum(any(e["element_id"] == short.id for e in p["manifest"]["elements"]) for p in output)
        == 1
    )
    first = output[0]["manifest"]["elements"][0]
    assert first["offset"] == 3 and first["column_offset"] == 2
    assert first["rows"][0]["sample_id"] == doc.samples[3].id


def test_tiled_batch_uses_largest_continuation_and_keeps_fixed_cohort(report_cohort):
    doc, engine = report_cohort
    layout = composition(doc)
    layout.batch = ReportBatch(
        mode="sample", sample_ids=[s.id for s in doc.samples[:3]], tile_columns=2
    )
    plan, output = pages(doc, engine, layout)
    assert plan["page_count"] == 24  # two tile groups × four row windows × three columns
    assert len(output[0]["manifest"]["elements"]) == 2
    assert all(e["input_rows"] == 8 for page in output for e in page["manifest"]["elements"])
    # Iterating the acquisition scope intentionally changes the cohort to one
    # sample per tile. It must not reuse the fixed full-cohort snapshot.
    layout.elements[0].iterate = True
    changed, result = pages(doc, engine, layout)
    assert changed["page_count"] == 6
    assert all(e["input_rows"] == 1 for page in result for e in page["manifest"]["elements"])


@pytest.mark.parametrize(
    "change, message",
    [
        ({"height_mm": 8}, "heading"),
        ({"row_start": 8}, "starting report row"),
        ({"column_start": 5}, "starting report column"),
    ],
)
def test_invalid_continuation_is_visible_and_blocks_current_exports(report_cohort, change, message):
    doc, engine = report_cohort
    layout = composition(doc)
    layout.elements[0] = layout.elements[0].model_copy(update=change)
    plan = reports.plan(doc, layout, engine)
    assert not plan["exportable"]
    assert any(message in issue["message"] for issue in plan["iterations"][0]["issues"])


def test_page_bound_and_explicit_prototype_navigation(report_cohort):
    doc, engine = report_cohort
    layout = composition(doc)
    layout.pages.append(ReportPage(width_mm=215.9, height_mm=279.4))
    layout.elements.append(
        ReportElement(kind="text", page=1, text="Second prototype", width_mm=180, height_mm=30)
    )
    plan = reports.plan(doc, layout, engine)
    assert plan["page_count"] == 13
    page = reports.render(
        doc,
        engine,
        reports.ReportRequest(revision=doc.revision, definition=layout, prototype_page=1),
    )
    assert page["page"] == 12 and page["geometry"]["width_mm"] == 215.9
    assert "Second prototype" in page["svg"]
    prototype = layout.elements[0]
    layout.pages = [ReportPage() for _ in range(32)]
    from cytoforge.models import new_id

    layout.elements = [
        prototype.model_copy(
            update={"id": new_id(), "page": index, "row_count": 1, "columns_per_page": 1}
        )
        for index in range(32)
    ]
    with pytest.raises(ValueError, match="1024 output pages"):
        reports.plan(doc, layout, engine)


def test_missing_pivot_and_comparison_never_substitute_data_view(report_cohort):
    doc, engine = report_cohort
    doc.tables[0].pivot = None
    layout = composition(doc, "pivot")
    plan = reports.plan(doc, layout, engine)
    assert not plan["exportable"]
    page = reports.render(
        doc, engine, reports.ReportRequest(revision=doc.revision, definition=layout)
    )
    assert not page["exportable"] and page["manifest"]["elements"][0]["unavailable"]
    assert "no pivot" in page["svg"]
    doc.tables[0].comparison = None
    layout.elements[0].table_view = "comparisons"
    assert not reports.plan(doc, layout, engine)["exportable"]


def test_continuation_reuses_full_evaluation_and_rechecks_corrupted_source(
    report_cohort, monkeypatch
):
    doc, engine = report_cohort
    layout = composition(doc)
    calls = []
    original = report_tables.tables.evaluate_table

    def measured(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(report_tables.tables, "evaluate_table", measured)
    plan, _ = pages(doc, engine, layout)
    assert len(calls) == 1
    layout.elements[0].x_mm += 1
    plan, _ = pages(doc, engine, layout)
    assert len(calls) == 1
    path = engine.store.data_path(doc.id, doc.samples[0].id)
    path.write_bytes(b"corrupted after table cache")
    page = reports.render(
        doc,
        engine,
        reports.ReportRequest(revision=doc.revision, definition=layout, validate_sources=True),
    )
    assert not page["exportable"]
    assert page["manifest"]["elements"][0]["unavailable"]
    assert any(issue["severity"] == "error" for issue in page["issues"])
    with pytest.raises(ConflictError):
        reports.render(
            doc,
            engine,
            reports.ReportRequest(
                revision=doc.revision, definition=layout, review_hash=plan["review_hash"]
            ),
        )


def test_empty_pivot_and_long_cells_preserve_zero_and_full_metadata(report_cohort):
    doc, engine = report_cohort
    doc.tables[0].filter = "No acquisition matches"
    layout = composition(doc, "pivot")
    plan, output = pages(doc, engine, layout)
    assert plan["page_count"] == 1 and output[0]["manifest"]["elements"][0]["rows"] == []
    assert "No selected rows" in output[0]["svg"]
    doc.tables[0].filter = ""
    doc.samples[0].name = "A very long sample name " * 5
    layout = composition(doc)
    layout.elements[0].width_mm = 60
    _, output = pages(doc, engine, layout)
    assert "…" in output[0]["svg"]
    assert output[0]["manifest"]["elements"][0]["rows"][0]["sample"] == doc.samples[0].name
    assert any("abbreviated" in problem["message"] for problem in output[0]["issues"])


def test_api_exports_every_page_and_prototype_selector_does_not_repeat_zip_pages(client):
    doc, _ = cohort(client.app.state.store)
    layout = composition(doc, "pivot")
    layout.pages.append(ReportPage(width_mm=215.9, height_mm=279.4))
    layout.elements.append(
        ReportElement(kind="text", page=1, text="Final page", width_mm=180, height_mm=30)
    )
    base = f"/api/workspaces/{doc.id}"
    body = dict(revision=doc.revision, definition=layout.model_dump())
    response = client.post(base + "/reports/plan", json=body)
    assert response.status_code == 200, response.text
    body.update(review_hash=response.json()["review_hash"], prototype_page=1)
    svg = client.post(base + "/reports/export", json=body)
    assert svg.status_code == 200, svg.text
    embedded = json.loads(ET.fromstring(svg.content).find("{*}metadata").text)
    assert embedded["page"] == 6 and embedded["output_page"]["prototype_page"] == 1
    exported = client.post(base + "/reports/export", json={**body, "format": "zip"})
    assert exported.status_code == 200, exported.text
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert len(manifest["pages"]) == 7
        assert [p["page"] for p in manifest["pages"]] == list(range(7))
        assert len({p["svg_sha256"] for p in manifest["pages"]}) == 7
