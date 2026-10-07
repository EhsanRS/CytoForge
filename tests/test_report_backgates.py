"""Known event intersections survive plotting, iteration, exports and saved history."""

import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import pytest
from cytoforge import report_templates, reports
from cytoforge.models import (
    Channel,
    Gate,
    Group,
    LayoutDefinition,
    PlotDefinition,
    PlotLayer,
    ReportBatch,
    ReportElement,
    Sample,
    ThreeDView,
    Workspace,
    new_id,
)
from cytoforge.plotting import plot_payload
from cytoforge.report_plots import figure
from cytoforge.science import Engine, save_events
from cytoforge.three_dimensional import point_chunk, prepare


def make_data(store):
    values = np.array(
        [
            [0, 0, 0],
            [1, 1, 1],
            [2, 2, 2],
            [3, 3, 3],
            [4, 4, 4],
            [-1, -1, -1],
            [np.nan, 1, 1],
            [1, np.nan, 1],
            [1, 1, np.nan],
        ]
    )
    doc = Workspace(name="Independent backgate truth")
    for index in range(2):
        sample = Sample(
            name=f"Acquisition {index}",
            channels=[Channel(name=c) for c in "XYZ"],
            event_count=len(values),
        )
        sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
        parent = Gate(sample_id=sample.id, name="First", kind="range", x="X", bounds=[0, 4])
        middle = Gate(
            sample_id=sample.id,
            parent_id=parent.id,
            name="Second",
            kind="range",
            x="Y",
            bounds=[1, 4],
        )
        final = Gate(
            sample_id=sample.id,
            parent_id=middle.id,
            name="Interest",
            kind="range",
            x="Z",
            bounds=[2, 4],
        )
        doc.samples.append(sample)
        doc.gates.extend([parent, middle, final])
    doc.groups = [Group(name="Both", sample_ids=[s.id for s in doc.samples])]
    return store.create(doc), Engine(store)


@pytest.fixture
def backgate_data(store):
    return make_data(store)


def definition(doc, mode="scatter", **updates):
    one = mode in {"histogram", "cdf"}
    plot = PlotDefinition(
        sample_id=doc.samples[0].id,
        x="X",
        y=None if one else "Y",
        mode=mode,
        bins=16,
        bounds=[-2, 5] * (1 if one else 3 if mode == "3d" else 2),
        three_d=ThreeDView(z="Z", show_labels=False) if mode == "3d" else None,
        backgate_id=doc.gates[2].id,
        show_gates=False,
        **updates,
    )
    return LayoutDefinition(
        name="Backgate figures",
        elements=[ReportElement(kind="plot", plot=plot, width_mm=160, height_mm=120)],
    )


def render(doc, engine, layout, **kwargs):
    return reports.render(
        doc, engine, reports.ReportRequest(revision=doc.revision, definition=layout, **kwargs)
    )


@pytest.mark.parametrize(
    "mode", ["scatter", "density", "contour", "zebra", "pseudocolor", "histogram", "cdf", "3d"]
)
def test_exact_finite_intersection_vector_highlights_and_unchanged_base_counts(backgate_data, mode):
    doc, engine = backgate_data
    original = doc.model_dump_json()
    layout = definition(doc, mode)
    highlighted = render(doc, engine, layout)
    assert highlighted["exportable"], highlighted["issues"]
    layer = highlighted["manifest"]["elements"][0]["layers"][0]
    assert layer["backgate_id"] == doc.gates[2].id
    assert (
        layer["backgate"]["count"]
        == layer["backgate"]["visible_count"]
        == layer["backgate"]["displayed_count"]
        == 2
    )
    assert layer["backgate"]["denominator"] == (
        8 if mode in {"histogram", "cdf"} else 6 if mode == "3d" else 7
    )
    assert layer["backgate"]["sampling"] is None
    assert set(layer["source"]["populations"]) == {g.id for g in doc.gates[:3]}
    svg = ET.fromstring(highlighted["svg"])
    assert not svg.findall(".//{*}image")
    assert "#f0b96a" in highlighted["svg"]
    if mode != "3d":
        assert any("cytoforge-backgate-0" in node.get("id", "") for node in svg.iter())
    layout.elements[0].plot.backgate_id = None
    plain = render(doc, engine, layout)["manifest"]["elements"][0]["layers"][0]
    for name in [
        "population_count",
        "finite_count",
        "visible_count",
        "counts",
        "edges",
        "displayed_values",
        "cdf_counts",
        "probability_denominator",
        "density_count",
        "event_ids_sha256",
    ]:
        if name in plain:
            assert layer[name] == plain[name]
    assert "backgate" not in plain and "backgate_id" not in plain
    assert original == doc.model_dump_json()
    if mode == "3d":
        assert (
            layer["backgate_displayed_event_ids_sha256"]
            == hashlib.sha256(np.array([2, 3], "<u8").tobytes()).hexdigest()
        )


def test_range_membership_boundaries_and_nonancestor_intersection(backgate_data):
    doc, engine = backgate_data
    for gate, expected in zip(
        doc.gates[:3], [[0, 1, 2, 3, 7, 8], [1, 2, 3, 8], [2, 3]], strict=True
    ):
        assert np.flatnonzero(engine.mask(doc, doc.samples[0], gate.id)).tolist() == expected
    layout = definition(doc, gate_id=doc.gates[0].id)
    layer = render(doc, engine, layout)["manifest"]["elements"][0]["layers"][0]
    assert layer["population_count"] == 6 and layer["finite_count"] == 5
    assert layer["backgate"]["count"] == 2
    other = Gate(sample_id=doc.samples[0].id, name="Other", kind="range", x="X", bounds=[-2, 0])
    doc.gates.append(other)
    layout.elements[0].plot.gate_id = other.id
    layer = render(doc, engine, layout)["manifest"]["elements"][0]["layers"][0]
    assert layer["population_count"] == 1 and layer["backgate"]["percent"] == 0


@pytest.mark.parametrize("mode", ["scatter", "histogram", "cdf", "3d"])
def test_bounds_audit_distinguishes_complete_count_from_visible_highlights(backgate_data, mode):
    doc, engine = backgate_data
    layout = definition(doc, mode)
    layout.elements[0].plot.bounds = [1.5, 2.5] * (
        3 if mode == "3d" else 1 if mode in {"histogram", "cdf"} else 2
    )
    layer = render(doc, engine, layout)["manifest"]["elements"][0]["layers"][0]
    assert layer["backgate"]["count"] == 2
    assert layer["backgate"]["visible_count"] == layer["backgate"]["displayed_count"] == 1
    assert layer["backgate"]["outside_view"] == 1


@pytest.mark.parametrize("visible", [3, 6001])
def test_planar_highlight_limit_samples_inside_the_viewport(store, visible):
    x = np.r_[np.full(12000, -10.0), np.linspace(0, 1, visible)]
    sample = Sample(
        name="Rare viewport", channels=[Channel(name=c) for c in "XY"], event_count=len(x)
    )
    doc = Workspace(name="Visible sampling", samples=[sample])
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), np.c_[x, x])
    gate = Gate(sample_id=sample.id, name="All finite", kind="range", x="X", bounds=[-11, 2])
    doc.gates = [gate]
    doc = store.create(doc)
    engine = Engine(store)
    options = dict(bounds=[0, 1, 0, 1], mode="scatter", backgate_id=gate.id)
    result = plot_payload(doc, engine, sample.id, "X", "Y", **options)
    assert result["backgate_count"] == 12000 + visible
    assert result["backgate_visible_count"] == visible
    assert result["backgate_displayed_count"] == min(6000, visible)
    points = np.asarray(result["backgate_points"])
    assert np.all((points >= 0) & (points <= 1))
    assert bool(result["backgate_sampling"]) == (visible > 6000)
    assert (
        result["backgate_points"]
        == plot_payload(doc, engine, sample.id, "X", "Y", **options)["backgate_points"]
    )
    if visible == 3:
        assert points.tolist() == [[0, 0], [0.5, 0.5], [1, 1]]


def test_sample_iteration_locked_layer_and_required_backgate_mapping(backgate_data):
    doc, engine = backgate_data
    layout = definition(doc)
    layout.batch = ReportBatch(mode="sample")
    layout.elements[0].plot.overlays = [
        PlotLayer(sample_id=doc.samples[0].id, backgate_id=doc.gates[2].id, locked_control=True)
    ]
    plan = reports.plan(doc, layout, engine)
    assert plan["exportable"], plan["issues"]
    bindings = plan["iterations"][1]["bindings"]
    assert [layer["backgate_id"] for layer in bindings] == [doc.gates[5].id, doc.gates[2].id]
    assert bindings[0]["source_backgate_id"] == doc.gates[2].id
    page = render(doc, engine, layout, page=1)
    assert [layer["backgate"]["count"] for layer in page["manifest"]["elements"][0]["layers"]] == [
        2,
        2,
    ]
    layout.batch.population_overrides[f"sample:{doc.samples[1].id}"] = {doc.gates[2].id: None}
    failed = render(doc, engine, layout, page=1)
    assert not failed["exportable"] and "cannot be mapped to all events" in failed["svg"]


@pytest.mark.parametrize("damage", ["missing", "foreign", "ambiguous"])
def test_missing_foreign_and_ambiguous_backgates_require_review(backgate_data, damage):
    doc, engine = backgate_data
    layout = definition(doc)
    if damage == "foreign":
        layout.elements[0].plot.backgate_id = doc.gates[5].id
    else:
        layout.batch = ReportBatch(mode="sample")
        if damage == "missing":
            doc.gates.pop()
        else:
            doc.gates.append(doc.gates[5].model_copy(update={"id": "e" * 32}))
    plan = reports.plan(doc, layout, engine)
    assert not plan["exportable"]
    failed = render(doc, engine, layout, page=1 if damage != "foreign" else 0)
    assert not failed["exportable"] and failed["manifest"]["elements"][0]["unavailable"]
    layout.export_policy = "placeholders"
    assert render(doc, engine, layout, page=1 if damage != "foreign" else 0)["exportable"]


@pytest.mark.parametrize("mode", ["scatter", "3d"])
def test_virtual_group_backgate_source_closure_covers_every_member(backgate_data, mode):
    doc, engine = backgate_data
    layout = definition(doc, mode, pooled=True, group_id=doc.groups[0].id)
    page = render(doc, engine, layout)
    assert page["exportable"], page["issues"]
    layer = page["manifest"]["elements"][0]["layers"][0]
    assert layer["backgate"]["count"] == layer["backgate"]["displayed_count"] == 4
    for index, source in enumerate(layer["source"]["sources"]):
        assert set(source["populations"]) == {
            gate.id for gate in doc.gates[index * 3 : index * 3 + 3]
        }


def test_default_and_empty_backgate_fields_keep_legacy_serialization(backgate_data):
    doc, _ = backgate_data
    for model in [
        PlotDefinition(sample_id=doc.samples[0].id, x="X"),
        PlotLayer(sample_id=doc.samples[0].id),
    ]:
        assert "backgate_id" not in model.model_dump()
        assert (
            type(model).model_validate({**model.model_dump(), "backgate_id": None}).model_dump()
            == model.model_dump()
        )


def test_backgate_changes_review_hash_and_3d_flags_only(backgate_data):
    doc, engine = backgate_data
    layout = definition(doc, "3d")
    initial = reports.plan(doc, layout, engine)["review_hash"]
    highlighted = prepare(
        doc,
        engine,
        doc.samples[0].id,
        "X",
        "Y",
        ThreeDView(z="Z"),
        bounds=[-2, 5] * 3,
        backgate_id=doc.gates[2].id,
    )
    plain = prepare(doc, engine, doc.samples[0].id, "X", "Y", ThreeDView(z="Z"), bounds=[-2, 5] * 3)
    points, normal = point_chunk(highlighted), point_chunk(plain)
    assert points["event_id"].tolist() == [0, 1, 2, 3, 4, 5]
    assert points["backgate"].tolist() == [0, 0, 1, 1, 0, 0]
    for field in ["position", "event_id", "color", "size"]:
        assert points[field].tobytes() == normal[field].tobytes()
    assert highlighted[0]["data_key"] != plain[0]["data_key"]
    layout.elements[0].plot.backgate_id = None
    assert initial != reports.plan(doc, layout, engine)["review_hash"]


def test_template_binding_review_save_history_and_raw_bytes(backgate_data, store):
    doc, engine = backgate_data
    layout = definition(doc)
    template = report_templates.exported(
        doc, report_templates.TemplateExport(revision=doc.revision, definition=layout)
    )
    assert report_templates.binding_key("population", doc.gates[2].id) in {
        binding["key"] for binding in template["bindings"]
    }
    request = report_templates.TemplateImport(
        id=new_id(), revision=doc.revision, name="Retained backgate", template=template, mappings={}
    )
    review = report_templates.preview(doc, request, engine)
    assert review["can_apply"], review["issues"]
    assert review["definition"]["elements"][0]["plot"]["backgate_id"] == doc.gates[2].id
    raw = [store.data_path(doc.id, sample.id).read_bytes() for sample in doc.samples]
    request.review_hash = review["review_hash"]
    changed = report_templates.apply(store, doc.id, request, engine)
    assert changed.layouts[-1].elements[0].plot.backgate_id == doc.gates[2].id
    undone = store.move_history(doc.id, -1, changed.revision)
    assert not undone.layouts
    redone = store.move_history(doc.id, 1, undone.revision)
    assert redone.layouts[-1].elements[0].plot.backgate_id == doc.gates[2].id
    assert raw == [store.data_path(doc.id, sample.id).read_bytes() for sample in doc.samples]


@pytest.mark.parametrize("mode", ["scatter", "histogram"])
def test_highlight_pixels_are_at_the_known_event_coordinates(backgate_data, mode):
    doc, engine = backgate_data
    layout = definition(doc, mode)
    layers = reports.plan(doc, layout, engine)["iterations"][0]["bindings"]
    svg, _ = figure(doc, engine, layout.elements[0].plot, layers, 160, 120)
    code = """
const {Resvg} = require('@resvg/resvg-js');
const fs = require('node:fs');
const result = new Resvg(fs.readFileSync(0, 'utf8'), {
  fitTo: {mode:'width',value:640}, font:{loadSystemFonts:false}
}).render();
process.stdout.write(result.pixels);
"""
    result = subprocess.run(
        ["node", "-e", code],
        input=svg.encode(),
        capture_output=True,
        cwd=Path(__file__).resolve().parents[1],
        check=True,
    )
    pixels = np.frombuffer(result.stdout, np.uint8).reshape(480, 640, 4)

    def orange(patch):
        return (
            (patch[:, :, 0] > 210)
            & (patch[:, :, 0].astype(int) - patch[:, :, 1] > 8)
            & (patch[:, :, 1].astype(int) - patch[:, :, 2] > 10)
        )

    for value in [2, 3]:
        x = round((0.16 + 0.8 * (value + 2) / 7) * 640)
        fraction = (value + 2) / 7 if mode == "scatter" else 0.015
        y = round((1 - (20 / 120 + 97 / 120 * fraction)) * 480)
        assert np.any(orange(pixels[y - 2 : y + 3, x - 2 : x + 3, :3]))
    if mode == "scatter":
        x = round((0.16 + 0.8 * 3 / 7) * 640)
        y = round((1 - (20 / 120 + 97 / 120 * 3 / 7)) * 480)
        assert not np.any(orange(pixels[y - 2 : y + 3, x - 2 : x + 3, :3]))


def test_templates_bind_destination_backgates_and_require_a_real_population(backgate_data, store):
    source, engine = backgate_data
    destination, _ = make_data(store)
    layout = definition(source, "3d")
    template = report_templates.exported(
        source, report_templates.TemplateExport(revision=source.revision, definition=layout)
    )
    request = report_templates.TemplateImport(
        id=new_id(), revision=destination.revision, name="Rebound backgate", template=template
    )
    original = [source.model_dump_json(), destination.model_dump_json()]
    review = report_templates.preview(destination, request, engine)
    assert review["can_apply"], review["issues"]
    assert review["definition"]["elements"][0]["plot"]["backgate_id"] == destination.gates[2].id
    request.mappings[report_templates.binding_key("population", source.gates[2].id)] = None
    refused = report_templates.preview(destination, request, engine)
    assert not refused["can_apply"]
    assert any(
        row["status"] == "missing"
        for row in refused["bindings"]
        if row["source_id"] == source.gates[2].id
    )
    assert original == [source.model_dump_json(), destination.model_dump_json()]


def test_empty_backgate_is_visible_in_provenance_without_drawing_other_events(backgate_data):
    doc, engine = backgate_data
    empty = Gate(sample_id=doc.samples[0].id, name="Empty", kind="range", x="X", bounds=[10, 11])
    doc.gates.append(empty)
    layout = definition(doc)
    layout.elements[0].plot.backgate_id = empty.id
    page = render(doc, engine, layout)
    layer = page["manifest"]["elements"][0]["layers"][0]
    assert (
        layer["backgate"]["count"]
        == layer["backgate"]["visible_count"]
        == layer["backgate"]["displayed_count"]
        == 0
    )
    assert layer["backgate"]["percent"] == 0
    assert "cytoforge-backgate-0" not in page["svg"]


@pytest.mark.parametrize("sampled", [False, True])
def test_large_3d_chunk_highlights_preserve_identities_and_sample_audits(store, sampled):
    x = np.arange(66000, dtype=float)
    sample = Sample(
        name="Known 3D identities", channels=[Channel(name=c) for c in "XYZ"], event_count=len(x)
    )
    doc = Workspace(name="Chunk truth", samples=[sample])
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), np.c_[x, x, x])
    gate = Gate(sample_id=sample.id, name="Interest", kind="range", x="X", bounds=[2000, 65540])
    doc.gates = [gate]
    doc = store.create(doc)
    engine = Engine(store)
    prepared = prepare(
        doc,
        engine,
        sample.id,
        "X",
        "Y",
        ThreeDView(z="Z", all_events=not sampled),
        bounds=[0, 66000] * 3,
        graph_options={"point_limit": 1000},
        backgate_id=gate.id,
    )
    arrays = [
        point_chunk(prepared, start) for start in range(0, prepared[0]["displayed_count"], 65536)
    ]
    points = np.concatenate(arrays)
    truth = (points["event_id"] >= 2000) & (points["event_id"] < 65540)
    np.testing.assert_array_equal(points["backgate"], truth.astype(float))
    assert prepared[0]["backgate_count"] == prepared[0]["backgate_visible_count"] == 63540
    assert prepared[0]["backgate_displayed_count"] == int(truth.sum())
    assert prepared[0]["sampling"] == prepared[0]["backgate_sampling"]
    if sampled:
        assert len(points) == 1000 and prepared[0]["backgate_sampling"]
    else:
        assert len(arrays) == 2 and len(points) == 66000
        assert points["event_id"].tolist() == list(range(66000))
    assert (
        hashlib.sha256(store.data_path(doc.id, sample.id).read_bytes()).hexdigest() == sample.sha256
    )


def test_magnetic_backgate_reports_resolved_membership_without_drawing_its_outline(store):
    from test_magnetic import acquisition

    doc, sample, gate, _ = acquisition(store)
    engine = Engine(store)
    plot = PlotDefinition(
        sample_id=sample.id, x="X", y="Y", mode="scatter", backgate_id=gate.id, show_gates=False
    )
    layout = LayoutDefinition(
        name="Following backgate",
        elements=[ReportElement(kind="plot", plot=plot, width_mm=160, height_mm=120)],
    )
    layer = render(doc, engine, layout)["manifest"]["elements"][0]["layers"][0]
    assert layer["backgate"]["count"] == 257
    assert layer["magnetic_gates"][0]["id"] == gate.id
    assert layer["magnetic_gates"][0]["resolution"]["resolved_count"] == 257


def test_project_roundtrip_and_report_zip_keep_highlight_provenance(client):
    store = client.app.state.store
    # Use the fixture's literal construction in a separate store owned by this API.
    doc, engine = make_data(store)
    layout = definition(doc, "3d")
    base = f"/api/workspaces/{doc.id}"
    response = client.post(
        base + "/layouts/save",
        json={
            "revision": doc.revision,
            "definition": layout.model_dump(),
        },
    )
    assert response.status_code == 200, response.text
    changed = response.json()
    body = {
        "revision": changed["revision"],
        "definition": layout.model_dump(),
        "validate_sources": True,
    }
    plan = client.post(base + "/reports/plan", json=body).json()
    exported = client.post(
        base + "/reports/export", json={**body, "review_hash": plan["review_hash"], "format": "zip"}
    )
    assert exported.status_code == 200, exported.text
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        layer = json.loads(archive.read("manifest.json"))["pages"][0]["elements"][0]["layers"][0]
        assert layer["backgate"]["count"] == 2
        assert "#f0b96a" in archive.read("page-0001.svg").decode()
    project = client.get(base + "/export/project")
    restored = client.post(
        "/api/import/project",
        files={"file": ("backgates.cytoforge", project.content, "application/zip")},
    )
    assert restored.status_code == 200, restored.text
    copied = restored.json()
    assert copied["layouts"][0]["elements"][0]["plot"]["backgate_id"] == copied["gates"][2]["id"]
    original_sample = store.get(doc.id).samples[0]
    assert (
        store.data_path(doc.id, original_sample.id).read_bytes()
        == store.data_path(copied["id"], copied["samples"][0]["id"]).read_bytes()
    )


def test_biological_model_removal_reviews_backgate_only_report_dependencies(client):
    from cytoforge import biology
    from test_biology_management import initial_model

    _, doc, sample, _, identifier = initial_model(client, "cell-cycle")
    result = next(item for item in doc.cell_cycle_results if item.id == identifier)
    target = next(gate for gate in doc.gates if gate.sample_id == sample.id)
    plot = PlotDefinition(sample_id=sample.id, x=sample.channels[0].name, backgate_id=target.id)
    legacy = LayoutDefinition(name="Legacy backgate", plots=[plot])
    element = ReportElement(kind="plot", plot=plot)
    positioned = LayoutDefinition(name="Positioned backgate", elements=[element])
    doc.layouts = [legacy, positioned]
    original = doc.model_dump_json()
    dependencies = biology.dependencies(doc, result)
    assert any(row["layout_id"] == legacy.id for row in dependencies["layout_plots"])
    assert any(row["element_id"] == element.id for row in dependencies["layout_elements"])
    assert doc.model_dump_json() == original
