"""Report counts, bindings and source errors checked independently of the editor."""

import hashlib
import io
import json
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
import pytest
from cytoforge import reports
from cytoforge.models import (
    Channel,
    Gate,
    LayoutDefinition,
    PlotDefinition,
    PlotLayer,
    ReportBatch,
    ReportElement,
    ReportPage,
    Sample,
    TableColumn,
    TableDefinition,
    Workspace,
)
from cytoforge.report_plots import normalized_histogram
from cytoforge.science import Engine, save_events
from cytoforge.store import ConflictError


@pytest.fixture
def report_data(store):
    arrays = [
        np.array([-2.0, 0.0, 1.0, 2.0, 3.0, 4.0, np.nan]),
        np.array([0.0, 1.0, 2.0, 3.0, 4.0]),
    ]
    samples = [
        Sample(
            name=name,
            channels=[Channel(name="X"), Channel(name="Y")],
            event_count=len(values),
            tags={"Donor": "A", "Treatment": treatment},
        )
        for name, treatment, values in zip(
            ("Control", "Stimulated"), ("CTRL", "STIM"), arrays, strict=True
        )
    ]
    doc = Workspace(name="Independent report truth", samples=samples)
    for sample, values in zip(samples, arrays, strict=True):
        sample.sha256 = save_events(
            store.data_path(doc.id, sample.id), np.column_stack([values, values])
        )
    gates = [
        Gate(sample_id=sample.id, name="Positive", kind="range", x="X", bounds=[1, 3])
        for sample in samples
    ]
    doc.gates = gates
    doc = store.create(doc)
    return doc, Engine(store)


def layout_for(doc, **changes):
    plot = PlotDefinition(
        sample_id=doc.samples[0].id, x="X", gate_id=doc.gates[0].id, bins=16, bounds=[0, 4]
    )
    element = ReportElement(
        kind="plot", plot=plot, title="{{sample}} · Positive", width_mm=160, height_mm=100
    )
    return LayoutDefinition(name="Verification", elements=[element], **changes)


def page_for(doc, engine, definition, **kwargs):
    return reports.render(
        doc, engine, reports.ReportRequest(revision=doc.revision, definition=definition, **kwargs)
    )


def test_full_data_histogram_and_vector_provenance(report_data):
    doc, engine = report_data
    definition = layout_for(doc)
    page = page_for(doc, engine, definition)
    assert page["exportable"]
    layer = page["manifest"]["elements"][0]["layers"][0]
    # Range population includes 1 and 2; its upper boundary 3 is excluded.
    assert layer["population_count"] == layer["finite_count"] == layer["visible_count"] == 2
    assert sum(layer["counts"]) == 2
    assert [layer["counts"][i] for i in (4, 8, 12)] == [1, 1, 0]
    assert layer["source"]["populations"][doc.gates[0].id]["bounds"] == [1, 3]
    assert layer["source"]["sample_sha256"] == doc.samples[0].sha256
    root = ET.fromstring(page["svg"])
    assert float(root.get("width").removesuffix("mm")) == 210
    assert not root.findall(".//{*}image")
    assert root.findall(".//{*}path")
    identifiers = {element.get("id") for element in root.iter() if element.get("id")}
    uses = root.findall(".//{*}use")
    assert uses and all(
        element.get("href", "").removeprefix("#") in identifiers for element in uses
    )
    assert "ns1:href" not in page["svg"]
    embedded = json.loads(root.find("{*}metadata").text)
    assert embedded["data_hash"] == page["manifest"]["data_hash"]
    assert hashlib.sha256(page["svg"].encode()).hexdigest() == page["manifest"]["svg_sha256"]
    assert page_for(doc, engine, definition)["svg"] == page["svg"]


def test_exact_boundary_visibility_denominator_and_normalization(report_data):
    doc, engine = report_data
    layout = layout_for(doc)
    layout.elements[0].plot.gate_id = None
    layout.elements[0].plot.bounds = [0, 3]
    layout.elements[0].plot.normalization = "percent_population"
    layer = page_for(doc, engine, layout)["manifest"]["elements"][0]["layers"][0]
    assert (
        layer["population_count"],
        layer["finite_count"],
        layer["visible_count"],
        layer["outside_view"],
    ) == (7, 6, 4, 2)
    assert sum(layer["displayed_values"]) == pytest.approx(400 / 7)


@pytest.mark.parametrize("normalization", ["percent_population", "unit_area", "peak"])
def test_empty_normalizations_are_undefined_not_zero(normalization):
    values, reason = normalized_histogram(
        dict(counts=[0, 0], edges=[0, 1, 2], count=0), normalization
    )
    assert values == [None, None] and reason
    assert normalized_histogram(dict(counts=[0, 0]), "count") == ([0.0, 0.0], None)


def test_shared_axes_and_locked_control(report_data):
    doc, engine = report_data
    layout = layout_for(doc, batch=ReportBatch(mode="sample"))
    layout.elements[0].plot.overlays = [
        PlotLayer(sample_id=doc.samples[0].id, locked_control=True, label="Reference")
    ]
    plan = reports.plan(doc, layout)
    bindings = plan["iterations"][1]["bindings"]
    assert [layer["sample_id"] for layer in bindings] == [doc.samples[1].id, doc.samples[0].id]
    result = page_for(doc, engine, layout, page=1, review_hash=plan["review_hash"])
    layers = result["manifest"]["elements"][0]["layers"]
    assert [layer["label"] for layer in layers] == ["Stimulated · Positive", "Reference"]
    assert layers[0]["x_transform"] == layers[1]["x_transform"]
    assert result["manifest"]["elements"][0]["title"] == "Stimulated · Positive"


def test_batch_uses_prototype_transform_when_target_preferences_differ(report_data):
    from cytoforge.models import Transform

    doc, engine = report_data
    doc.samples[1].channels[0].transform = Transform(kind="asinh", cofactor=10)
    definition = layout_for(doc, batch=ReportBatch(mode="sample"))
    page = page_for(doc, engine, definition, page=1)
    assert page["exportable"], page["issues"]
    assert page["manifest"]["elements"][0]["layers"][0]["x_transform"]["kind"] == "linear"


def test_missing_population_never_substitutes_all_events(report_data):
    doc, engine = report_data
    doc.gates.pop()
    layout = layout_for(doc, batch=ReportBatch(mode="sample"))
    plan = reports.plan(doc, layout)
    assert not plan["exportable"]
    page = page_for(doc, engine, layout, page=1)
    assert not page["exportable"] and page["manifest"]["elements"][0]["unavailable"]
    assert "Population path" in page["svg"]
    layout.export_policy = "placeholders"
    assert page_for(doc, engine, layout, page=1)["exportable"]


def test_ambiguous_path_requires_explicit_population_mapping(report_data):
    doc, engine = report_data
    duplicate = doc.gates[1].model_copy(update={"id": "a" * 32})
    doc.gates.append(duplicate)
    layout = layout_for(doc, batch=ReportBatch(mode="sample"))
    assert not reports.plan(doc, layout)["exportable"]
    key = f"sample:{doc.samples[1].id}"
    layout.batch.population_overrides[key] = {doc.gates[0].id: duplicate.id}
    plan = reports.plan(doc, layout)
    assert plan["exportable"]
    assert (
        page_for(doc, engine, layout, page=1)["manifest"]["elements"][0]["layers"][0]["gate_id"]
        == duplicate.id
    )


def test_keyword_discriminator_and_panel_bind_two_sources(report_data):
    doc, _ = report_data
    layout = layout_for(doc)
    layout.elements[0].plot.overlays = [PlotLayer(sample_id=doc.samples[1].id)]
    layout.batch = ReportBatch(
        mode="keyword", iterator_keyword="Donor", discriminator_keyword="Treatment"
    )
    plan = reports.plan(doc, layout)
    assert plan["page_count"] == 1 and plan["exportable"]
    assert plan["iterations"][0]["mapping"] == {sample.id: sample.id for sample in doc.samples}
    layout.batch.discriminator_keyword = ""
    assert not reports.plan(doc, layout)["exportable"]
    layout.batch = ReportBatch(mode="panel", panel_size=2)
    assert reports.plan(doc, layout)["exportable"]
    layout.batch.panel_size = 1
    assert not reports.plan(doc, layout)["exportable"]


def test_review_hash_rejects_changed_definition_and_revision(report_data):
    doc, engine = report_data
    layout = layout_for(doc)
    plan = reports.plan(doc, layout)
    layout.elements[0].x_mm = 13
    with pytest.raises(ConflictError):
        page_for(doc, engine, layout, review_hash=plan["review_hash"])
    with pytest.raises(ConflictError):
        reports.render(
            doc, engine, reports.ReportRequest(revision=doc.revision + 1, definition=layout)
        )


def test_live_statistics_metadata_and_xml_escaping(report_data):
    doc, engine = report_data
    doc.samples[1].tags["Treatment"] = '</text><script>alert(1)</script> & "α"'
    element = ReportElement(
        kind="text",
        sample_id=doc.samples[0].id,
        gate_id=doc.gates[0].id,
        text="{{sample}}: {{stat:count}}; {{stat:mean:X}}; {{keyword:Treatment}}",
        width_mm=180,
        height_mm=30,
    )
    layout = LayoutDefinition(
        name="Text truth", elements=[element], batch=ReportBatch(mode="sample")
    )
    page = page_for(doc, engine, layout, page=1)
    assert page["exportable"]
    provenance = page["manifest"]["elements"][0]
    assert provenance["statistics"]["count"]["value"] == 2
    assert provenance["statistics"]["mean:X"]["value"] == 1.5
    assert provenance["text"].startswith("Stimulated: 2; 1.50;")
    root = ET.fromstring(page["svg"])
    assert not root.findall(".//{*}script")


def test_saved_table_zero_undefined_and_geometry(report_data):
    doc, engine = report_data
    zero = TableColumn(name="Zero", kind="formula", expression="0", decimals=0)
    undefined = TableColumn(name="Undefined", kind="formula", expression="0 / 0")
    table = TableDefinition(name="Truth", columns=[zero, undefined])
    doc.tables.append(table)
    element = ReportElement(
        kind="table", table_id=table.id, width_mm=170, height_mm=70, rotation=15
    )
    page = page_for(doc, engine, LayoutDefinition(name="Table", elements=[element]))
    assert page["exportable"]
    row = page["manifest"]["elements"][0]["rows"][0]
    assert row["values"][zero.id] == 0 and row["values"][undefined.id] is None
    assert "Undefined" in page["svg"] and "source issues" in page["svg"]
    assert "rotate(15.0 85.0 35.0)" in page["svg"]


def test_legacy_layout_migration_keeps_sources_and_more_than_four_plots(report_data):
    doc, engine = report_data
    plots = [PlotDefinition(sample_id=doc.samples[0].id, x="X") for _ in range(5)]
    legacy = LayoutDefinition(name="Historical grid", plots=plots)
    migrated = reports.migrated(legacy)
    assert len(migrated.pages) == 2 and len(migrated.elements) == 5 and not migrated.plots
    assert [element.plot.sample_id for element in migrated.elements] == [doc.samples[0].id] * 5
    assert page_for(doc, engine, legacy, page=1)["exportable"]


def test_geometry_ids_and_mapping_limits_are_validated(report_data):
    doc, _ = report_data
    with pytest.raises(ValueError):
        LayoutDefinition(name="Too wide", elements=[ReportElement(kind="text", width_mm=210)])
    with pytest.raises(ValueError):
        ReportPage(width_mm=30, margin_mm=15)
    with pytest.raises(ValueError):
        ReportBatch(population_overrides={"x" * 1001: {}})


def test_api_exports_require_review_and_preserve_manifest(client):
    store = client.app.state.store
    sample = Sample(name="API report", channels=[Channel(name="X")], event_count=4)
    doc = Workspace(name="Desktop report API", samples=[sample])
    sample.sha256 = save_events(
        store.data_path(doc.id, sample.id), np.array([[0.0], [1.0], [2.0], [3.0]])
    )
    doc = store.create(doc)
    layout = LayoutDefinition(
        name="API figure",
        elements=[ReportElement(kind="plot", plot=PlotDefinition(sample_id=sample.id, x="X"))],
        batch=ReportBatch(mode="sample"),
    )
    base = f"/api/workspaces/{doc.id}"
    body = dict(revision=doc.revision, definition=layout.model_dump())
    review = client.post(base + "/reports/plan", json=body)
    assert review.status_code == 200, review.text
    assert client.post(base + "/reports/export", json=body).status_code == 422
    body.update(review_hash=review.json()["review_hash"], format="zip")
    response = client.post(base + "/reports/export", json=body)
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["pages"][0]["elements"][0]["layers"][0]["population_count"] == 4
        assert (
            hashlib.sha256(archive.read("page-0001.svg")).hexdigest()
            == manifest["pages"][0]["svg_sha256"]
        )
    body.pop("format")
    body["revision"] += 1
    assert client.post(base + "/reports/render", json=body).status_code == 409


def test_position_changes_reuse_scientific_panels_but_gate_edits_refresh(report_data, monkeypatch):
    doc, engine = report_data
    definition = layout_for(doc)
    calls = []
    original = reports.report_plots.figure

    def measured(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(reports.report_plots, "figure", measured)
    first = page_for(doc, engine, definition)
    definition.elements[0].x_mm += 1
    doc.revision += 1
    moved = page_for(doc, engine, definition)
    assert len(calls) == 1
    assert moved["manifest"]["elements"][0]["geometry"]["x_mm"] == 13
    assert first["manifest"]["elements"][0]["geometry"]["x_mm"] == 12
    doc.gates[0].bounds = [2, 3]
    doc.revision += 1
    changed = page_for(doc, engine, definition)
    assert len(calls) == 2
    assert changed["manifest"]["elements"][0]["layers"][0]["population_count"] == 1


def test_export_checks_corrupted_bytes_even_after_engine_cache_is_populated(report_data):
    doc, engine = report_data
    definition = layout_for(doc)
    assert page_for(doc, engine, definition, validate_sources=True)["exportable"]
    with engine.store.data_path(doc.id, doc.samples[0].id).open("ab") as handle:
        handle.write(b"altered immutable data")
    page = page_for(doc, engine, definition, validate_sources=True)
    assert not page["exportable"]
    assert page["manifest"]["elements"][0]["unavailable"]
    assert "integrity check" in page["svg"]


@pytest.mark.parametrize("mode", ["histogram", "density"])
@pytest.mark.parametrize("limits", [(-1e308, 1e308), (1e-320, 9e-320)])
def test_finite_extreme_axes_conserve_mass(store, mode, limits):
    sample = Sample(
        name="Finite numeric extremes",
        channels=[Channel(name="X"), Channel(name="Y")],
        event_count=3,
    )
    doc = Workspace(name="Extremes", samples=[sample])
    values = np.array(
        [[limits[0], limits[0]], [0 if limits[0] < 0 else 5e-320] * 2, [limits[1], limits[1]]]
    )
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    doc = store.create(doc)
    plot = PlotDefinition(
        sample_id=sample.id,
        x="X",
        y=None if mode == "histogram" else "Y",
        mode=mode,
        bounds=list(limits) if mode == "histogram" else list(limits) * 2,
        bins=16,
    )
    definition = LayoutDefinition(
        name="Finite report",
        elements=[ReportElement(kind="plot", plot=plot, width_mm=160, height_mm=100)],
    )
    page = page_for(doc, Engine(store), definition, validate_sources=True)
    assert page["exportable"], page["issues"]
    layer = page["manifest"]["elements"][0]["layers"][0]
    assert layer["visible_count"] == 3 and sum(layer["counts"]) == 3
    json.dumps(page, allow_nan=False)


@pytest.mark.parametrize("platform", ["cell-cycle", "proliferation"])
def test_live_model_outputs_and_saved_figures_use_explicit_historical_policy(client, platform):
    from test_biology_management import initial_model

    base, doc, sample, _, identifier = initial_model(client, platform)
    base = f"/api/workspaces/{doc.id}"
    field = "cell_cycle_results" if platform == "cell-cycle" else "proliferation_results"
    result = getattr(doc, field)[0]
    plot = ReportElement(
        kind="plot",
        plot=PlotDefinition(sample_id=sample.id, x=result.columns[0]),
        width_mm=160,
        height_mm=90,
    )
    figure = ReportElement(
        kind="biology",
        platform=platform,
        result_id=identifier,
        sample_id=sample.id,
        y_mm=140,
        width_mm=160,
        height_mm=110,
    )
    definition = LayoutDefinition(name="Biological figure truth", elements=[plot, figure])
    body = dict(revision=doc.revision, definition=definition.model_dump(), validate_sources=True)
    page = client.post(base + "/reports/render", json=body)
    assert page.status_code == 200, page.text
    assert page.json()["exportable"], page.json()["issues"]
    source = getattr(doc, field)[0].data[0]
    with client.app.state.store.analysis_path(doc.id, identifier, source.sample_id).open(
        "ab"
    ) as handle:
        handle.write(b"bad fitted probabilities")
    corrupted = client.post(base + "/reports/render", json=body).json()
    assert not corrupted["exportable"]
    assert all(element["unavailable"] for element in corrupted["manifest"]["elements"])


def test_historical_curve_and_dependent_report_removal_review(client):
    from cytoforge import biology
    from test_biology_management import initial_model

    base, doc, sample, _, identifier = initial_model(client, "cell-cycle")
    result = doc.cell_cycle_results[0]
    plot = ReportElement(
        kind="plot",
        plot=PlotDefinition(sample_id=sample.id, x=result.columns[0]),
        width_mm=160,
        height_mm=90,
    )
    figure = ReportElement(
        kind="biology",
        platform="cell-cycle",
        result_id=identifier,
        sample_id=sample.id,
        y_mm=140,
        width_mm=160,
        height_mm=110,
    )
    definition = LayoutDefinition(name="Source lifecycle", elements=[plot, figure])
    doc.layouts.append(definition)
    affected = biology.dependencies(doc, result)
    assert {row["element_id"] for row in affected["layout_elements"]} == {plot.id, figure.id}
    changed = doc.model_copy(deep=True)
    changed.samples[0].sha256 = "f" * 64
    changed.revision += 1
    engine = Engine(client.app.state.store)
    current = page_for(changed, engine, definition)
    assert not current["exportable"] and any(
        issue["severity"] == "stale" for issue in current["issues"]
    )
    definition.export_policy = "snapshots"
    assert page_for(changed, engine, definition)["exportable"]
    with pytest.raises(ValueError, match="dependent"):
        biology.remove_model(doc, "cell-cycle", identifier)
    biology.remove_model(doc, "cell-cycle", identifier, cascade=True)
    assert [element.id for element in doc.layouts[0].elements] == [figure.id]
    assert not page_for(doc, engine, doc.layouts[0])["exportable"]


def test_whole_plate_figure_keeps_exact_fixed_sources(report_data):
    from cytoforge.models import PlateDefinition

    doc, engine = report_data
    plate = PlateDefinition(
        name="Known plate", assignments={"A01": [doc.samples[0].id], "A02": [doc.samples[1].id]}
    )
    doc.plates.append(plate)
    element = ReportElement(
        kind="plate", plate_id=plate.id, iterate=False, width_mm=180, height_mm=130
    )
    layout = LayoutDefinition(name="Plate report", elements=[element])
    page = page_for(doc, engine, layout, validate_sources=True)
    assert page["exportable"], page["issues"]
    result = page["manifest"]["elements"][0]["data"]
    values = {well["well"]: well["values"][plate.columns[0].id] for well in result["wells"]}
    assert values["A01"] == 7 and values["A02"] == 5
    assert result["acquisition_count"] == 2
    assert not ET.fromstring(page["svg"]).findall(".//{*}script")


def test_native_source_proofs_and_positioned_archive_roundtrip(client):
    store = client.app.state.store
    sample = Sample(name="Portable source", channels=[Channel(name="X")], event_count=3)
    doc = Workspace(name="Portable positioned report", samples=[sample])
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), np.array([[1.0], [2.0], [3.0]]))
    layout = LayoutDefinition(
        name="Positioned archive",
        elements=[
            ReportElement(
                kind="plot",
                plot=PlotDefinition(sample_id=sample.id, x="X"),
                x_mm=15,
                width_mm=150,
                height_mm=100,
            )
        ],
    )
    doc.layouts = [layout]
    doc = store.create(doc)
    base = f"/api/workspaces/{doc.id}"
    body = dict(revision=doc.revision, definition=layout.model_dump(), validate_sources=True)
    page = client.post(base + "/reports/render", json=body).json()
    body.update(
        review_hash=page["review_hash"],
        proofs=[
            dict(
                page=0,
                data_hash=page["manifest"]["data_hash"],
                svg_sha256=page["manifest"]["svg_sha256"],
            )
        ],
    )
    assert client.post(base + "/reports/verify", json=body).status_code == 200
    bad = dict(body)
    bad["proofs"] = [{**body["proofs"][0], "data_hash": "0" * 64}]
    assert client.post(base + "/reports/verify", json=bad).status_code == 409
    archive = client.get(base + "/export/project")
    restored = client.post(
        "/api/import/project",
        files={"file": ("report.cytoforge", archive.content, "application/zip")},
    )
    assert restored.status_code == 200, restored.text
    copy = restored.json()
    assert copy["layouts"][0]["elements"][0]["x_mm"] == 15
    assert copy["layouts"][0]["elements"][0]["plot"]["sample_id"] == sample.id
    rendered = client.post(
        f"/api/workspaces/{copy['id']}/reports/render",
        json=dict(revision=copy["revision"], definition=copy["layouts"][0], validate_sources=True),
    ).json()
    assert (
        rendered["exportable"]
        and rendered["manifest"]["elements"][0]["layers"][0]["population_count"] == 3
    )
