"""Known source values survive publication styling, pagination and template history."""

import hashlib
import json
from xml.etree import ElementTree as ET

import numpy as np
import pytest
from cytoforge import report_cache, report_templates, reports
from cytoforge.models import (
    Channel,
    LayoutDefinition,
    PlotDefinition,
    ReportElement,
    Sample,
    TableColumn,
    TableDefinition,
    Workspace,
    new_id,
)
from cytoforge.science import Engine, save_events
from cytoforge.store import ConflictError
from pydantic import ValidationError


@pytest.fixture
def publication_data(store):
    samples = [
        Sample(name=f"Acquisition {i + 1}", channels=[Channel(name="X")], event_count=2)
        for i in range(8)
    ]
    column = TableColumn(name="Mean X", kind="statistic", statistic="mean", channel="X")
    table = TableDefinition(name="Known values", columns=[column])
    workspace = Workspace(name="Typography truth", samples=samples, tables=[table])
    for i, sample in enumerate(samples):
        sample.sha256 = save_events(
            store.data_path(workspace.id, sample.id), np.array([[i + 1.0], [i + 3.0]])
        )
    return store.create(workspace), Engine(store)


def render(workspace, engine, layout, **options):
    return reports.render(
        workspace,
        engine,
        reports.ReportRequest(revision=workspace.revision, definition=layout, **options),
    )


def composition(*elements):
    return LayoutDefinition(
        name="Publication styles", elements=list(elements), show_header=False, show_footer=False
    )


def labels(page):
    return ET.fromstring(page["svg"]).findall(".//{*}text")


@pytest.mark.parametrize("family,weight", [("sans-serif", 400), ("serif", 600), ("monospace", 800)])
def test_live_annotations_keep_known_values_and_apply_typography(publication_data, family, weight):
    workspace, engine = publication_data
    before = workspace.model_dump_json()
    element = ReportElement(
        kind="text",
        sample_id=workspace.samples[0].id,
        text="{{stat:count}}\n{{stat:mean:X}}",
        width_mm=100,
        height_mm=30,
        font_size_pt=10,
        font_family=family,
        font_weight=weight,
        font_style="italic",
        text_decoration="underline",
        line_spacing=2,
        align="right",
        color="#123456",
    )
    page = render(workspace, engine, composition(element))
    assert page["exportable"]
    assert page["manifest"]["elements"][0]["text"] == "2\n2.00"
    for i, label in enumerate(labels(page)):
        assert label.get("font-family") == family
        assert label.get("font-weight") == str(weight)
        assert label.get("font-style") == "italic"
        assert label.get("text-decoration") == "underline"
        assert label.get("text-anchor") == "end" and label.get("x") == "100"
        assert label.get("fill") == "#123456"
        assert float(label.get("y")) == pytest.approx((i + 1) * 10 * 25.4 / 72 * 2)
    assert [label.text for label in labels(page)] == ["2", "2.00"]
    assert workspace.model_dump_json() == before
    for sample in workspace.samples:
        assert (
            hashlib.sha256(engine.store.data_path(workspace.id, sample.id).read_bytes()).hexdigest()
            == sample.sha256
        )


def test_line_spacing_reports_actual_annotation_overflow(publication_data):
    workspace, engine = publication_data
    element = ReportElement(kind="text", text="First\nSecond\nThird", height_mm=15, line_spacing=2)
    page = render(workspace, engine, composition(element))
    assert [label.text for label in labels(page)] == ["First", "Second"]
    assert page["manifest"]["elements"][0]["omitted_lines"] == 1
    assert any("1 annotation lines" in issue["message"] for issue in page["issues"])
    element.line_spacing = 1
    complete = render(workspace, engine, composition(element))
    assert [label.text for label in labels(complete)] == ["First", "Second", "Third"]
    assert complete["manifest"]["elements"][0]["omitted_lines"] == 0


def test_figure_caption_uses_saved_font_alignment_and_spacing(publication_data):
    workspace, engine = publication_data
    element = ReportElement(
        kind="plot",
        plot=PlotDefinition(sample_id=workspace.samples[0].id, x="X", bins=16, bounds=[0, 4]),
        title="Styled caption",
        width_mm=150,
        height_mm=100,
        font_family="monospace",
        font_weight=700,
        font_style="italic",
        text_decoration="underline",
        line_spacing=2,
        align="center",
    )
    page = render(workspace, engine, composition(element))
    assert page["exportable"]
    caption = next(label for label in labels(page) if label.text == "Styled caption")
    assert caption.get("font-family") == "monospace"
    assert caption.get("font-weight") == "700"
    assert caption.get("font-style") == "italic"
    assert caption.get("text-decoration") == "underline"
    assert caption.get("text-anchor") == "middle" and caption.get("x") == "75"
    assert reports.caption_height(element.title, element) == pytest.approx(10 * 25.4 / 72 * 2 + 1)
    layer = page["manifest"]["elements"][0]["layers"][0]
    assert layer["population_count"] == 2 and sum(layer["counts"]) == 2


def test_table_typography_repaginates_without_changing_cohort_truth(publication_data):
    workspace, engine = publication_data
    table = workspace.tables[0]
    element = ReportElement(
        kind="table",
        table_id=table.id,
        auto_paginate=True,
        width_mm=180,
        height_mm=60,
        row_count=200,
        font_family="monospace",
        font_weight=800,
        font_style="italic",
        text_decoration="underline",
        line_spacing=1,
        color="#123456",
    )
    layout = composition(element)
    initial = reports.plan(workspace, layout, engine)
    element.line_spacing = 2
    reviewed = reports.plan(workspace, layout, engine)
    assert initial["page_count"] < reviewed["page_count"] == 4
    assert initial["review_hash"] != reviewed["review_hash"]
    with pytest.raises(ConflictError):
        render(workspace, engine, layout, review_hash=initial["review_hash"])
    rows = []
    for page_index in range(reviewed["page_count"]):
        page = render(
            workspace, engine, layout, page=page_index, review_hash=reviewed["review_hash"]
        )
        assert page["exportable"]
        rows.extend(page["manifest"]["elements"][0]["rows"])
        for label in labels(page):
            assert label.get("font-family") == "monospace"
            assert label.get("font-weight") == "800"
            assert label.get("font-style") == "italic"
            assert label.get("text-decoration") == "underline"
    assert [row["sample"] for row in rows] == [sample.name for sample in workspace.samples]
    assert [row["values"][table.columns[0].id] for row in rows] == list(range(2, 10))


def test_geometry_reuses_panels_but_typography_invalidates_them(publication_data, monkeypatch):
    workspace, engine = publication_data
    monkeypatch.setattr(report_cache, "PANELS", report_cache.PanelCache())
    calls = []
    original = reports.content

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(reports, "content", counted)
    element = ReportElement(kind="text", text="Known typography", width_mm=150)
    layout = composition(element)
    render(workspace, engine, layout)
    element.x_mm = 25
    render(workspace, engine, layout)
    assert len(calls) == 1
    element.font_style = "italic"
    styled = render(workspace, engine, layout)
    assert len(calls) == 2
    assert labels(styled)[0].get("font-style") == "italic"


def test_new_style_defaults_preserve_legacy_document_serialization():
    element = ReportElement(kind="text", text="Legacy")
    original = element.model_dump()
    assert not {"font_style", "text_decoration", "line_spacing"} & original.keys()
    legacy = ReportElement.model_validate(json.loads(element.model_dump_json()))
    assert legacy.model_dump() == original
    element.font_style = "italic"
    element.text_decoration = "underline"
    element.line_spacing = 2
    saved = element.model_dump()
    assert saved["font_style"] == "italic" and saved["text_decoration"] == "underline"
    assert saved["line_spacing"] == 2


def test_publication_styles_survive_template_import_save_undo_and_redo(publication_data):
    workspace, engine = publication_data
    element = ReportElement(
        kind="text",
        sample_id=workspace.samples[0].id,
        text="{{stat:count}}",
        font_family="serif",
        font_weight=600,
        font_style="italic",
        text_decoration="underline",
        line_spacing=2,
    )
    exported = report_templates.exported(
        workspace,
        report_templates.TemplateExport(
            revision=workspace.revision, definition=composition(element)
        ),
    )
    template = report_templates.parsed(json.dumps(exported).encode())
    request = report_templates.TemplateImport(
        revision=workspace.revision, id=new_id(), template=template, name="Saved styles"
    )
    preview = report_templates.preview(workspace, request, engine)
    assert preview["can_apply"]
    request.review_hash = preview["review_hash"]
    imported = report_templates.apply(engine.store, workspace.id, request, engine)
    stored = engine.store.get(workspace.id)
    saved = stored.layouts[0].model_dump()
    assert stored.layouts[0].template_origin.source_workspace_id == workspace.id
    page = render(stored, engine, stored.layouts[0])
    assert [label.text for label in labels(page)] == ["2"]
    assert labels(page)[0].get("font-family") == "serif"
    assert labels(page)[0].get("font-style") == "italic"
    assert labels(page)[0].get("font-weight") == "600"
    assert stored.layouts[0].elements[0].line_spacing == 2
    undone = engine.store.move_history(workspace.id, -1, imported.revision)
    assert not undone.layouts
    redone = engine.store.move_history(workspace.id, 1, undone.revision)
    assert redone.layouts[0].model_dump() == saved
    for sample in redone.samples:
        assert (
            hashlib.sha256(engine.store.data_path(workspace.id, sample.id).read_bytes()).hexdigest()
            == sample.sha256
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("font_style", "oblique"),
        ("text_decoration", "url(https://example.test)"),
        ("font_family", "serif; fill:red"),
        ("line_spacing", 0.9),
        ("line_spacing", 3.1),
        ("line_spacing", float("nan")),
        ("line_spacing", float("inf")),
    ],
)
def test_invalid_typography_is_rejected(field, value):
    with pytest.raises(ValidationError):
        ReportElement(kind="text", **{field: value})
