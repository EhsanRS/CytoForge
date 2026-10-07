"""Gate display geometry, physical report output and unchanged event identities."""

import copy
import hashlib
import json
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import pytest
from cytoforge import report_templates, reports
from cytoforge.graph_views import resolved_options
from cytoforge.models import (
    Channel,
    Gate,
    GateDimension,
    GraphOptions,
    LayoutDefinition,
    PlotDefinition,
    ReportElement,
    Sample,
    ThreeDView,
    Workspace,
)
from cytoforge.plotting import plot_payload
from cytoforge.report_gate_style import fill_gate, gate_fill_svg
from cytoforge.report_plots import figure, fraction, gate_artists
from cytoforge.science import Engine, save_events
from cytoforge.three_dimensional import point_chunk, prepare
from matplotlib.backends.backend_svg import FigureCanvasSVG
from matplotlib.figure import Figure
from matplotlib.font_manager import findfont
from matplotlib.ft2font import FT2Font
from pydantic import ValidationError

STYLE = dict(fill_opacity=0.4, fill_color="#d12345", line_width_px=4.5, show_labels=False)


@pytest.fixture
def gate_data(store):
    values = np.array([[x, y, (x + y) / 2] for x in [0.5, 1.5, 2.5] for y in [0.5, 1.5, 2.5]])
    values = np.vstack([values, [np.nan, np.nan, np.nan]])
    sample = Sample(name="Known events", channels=[Channel(name=c) for c in "XYZ"], event_count=10)
    workspace = Workspace(name="Gate display truth", samples=[sample])
    sample.sha256 = save_events(store.data_path(workspace.id, sample.id), values)
    workspace.gates = [
        Gate(
            name="Gate with excluded centre",
            sample_id=sample.id,
            kind="polygon",
            x="X",
            y="Y",
            vertices=[(0, 0), (3, 0), (3, 3), (0, 3)],
            holes=[[(1, 1), (2, 1), (2, 2), (1, 2)]],
            color="#135791",
        ),
        Gate(
            name="Known range",
            sample_id=sample.id,
            kind="range",
            x="X",
            bounds=[1, 2],
            color="#246802",
        ),
        Gate(
            name="Known box",
            sample_id=sample.id,
            kind="hyperrectangle",
            color="#357913",
            dimensions=[GateDimension(channel=c, minimum=0, maximum=3) for c in "XYZ"],
        ),
    ]
    return store.create(workspace), Engine(store)


def plot(workspace, mode="scatter", style=STYLE):
    return PlotDefinition(
        sample_id=workspace.samples[0].id,
        x="X",
        y=None if mode in {"histogram", "cdf"} else "Y",
        mode=mode,
        bins=16,
        bounds=[0, 3] * (3 if mode == "3d" else 1 if mode in {"histogram", "cdf"} else 2),
        three_d=ThreeDView(z="Z") if mode == "3d" else None,
        graph_options=GraphOptions(gate_style=style),
    )


def layers(workspace):
    return [
        dict(
            sample_id=workspace.samples[0].id,
            source_sample_id=workspace.samples[0].id,
            gate_id=None,
            coordinate_gate_id=None,
            color="#087e8b",
            label="Known events",
            locked_control=False,
        )
    ]


def test_defaults_and_empty_gate_styles_preserve_existing_workspace_serialization():
    base = GraphOptions().model_dump()
    for style in [None, {}, {"fill_opacity": None}, {"show_labels": None}]:
        assert GraphOptions(gate_style=style).model_dump() == base
    assert GraphOptions(gate_style={"fill_opacity": 0, "show_labels": False}).model_dump()[
        "gate_style"
    ] == {
        "fill_opacity": 0,
        "show_labels": False,
    }


@pytest.mark.parametrize(
    "bad",
    [
        {"fill_opacity": -0.1},
        {"fill_opacity": 1.1},
        {"fill_opacity": True},
        {"fill_opacity": "0.4"},
        {"fill_opacity": float("nan")},
        {"fill_opacity": float("inf")},
        {"line_width_px": 0.24},
        {"line_width_px": 12.1},
        {"line_width_px": "2"},
        {"line_width_px": True},
        {"line_width_px": float("nan")},
        {"fill_color": "red"},
        {"fill_color": "#fff"},
        {"fill_color": "#abcdef;display:none"},
        {"show_labels": 0},
        {"show_labels": "false"},
        {"unknown": 1},
    ],
)
def test_invalid_gate_display_values_are_rejected(bad):
    with pytest.raises(ValidationError):
        GraphOptions(gate_style=bad)


@pytest.mark.parametrize(
    "mode", ["scatter", "histogram", "cdf", "density", "pseudocolor", "contour", "zebra"]
)
def test_styles_do_not_change_scientific_counts_masks_geometry_or_sampling(gate_data, mode):
    workspace, engine = gate_data
    styled, plain = plot(workspace, mode), plot(workspace, mode, None)
    kwargs = dict(x=styled.x, y=styled.y, bounds=styled.bounds, bins=16, mode=mode)
    before = plot_payload(
        workspace, engine, workspace.samples[0].id, graph_options=plain.graph_options, **kwargs
    )
    after = plot_payload(
        workspace, engine, workspace.samples[0].id, graph_options=styled.graph_options, **kwargs
    )
    assert before == after
    assert before["count"] == 10 and before["finite_count"] == before["visible_count"] == 9
    assert resolved_options(plain.graph_options, mode) == resolved_options(
        styled.graph_options, mode
    )
    expected = np.ones(10, dtype=bool)
    expected[[4, 9]] = False
    np.testing.assert_array_equal(
        engine.mask(workspace, workspace.samples[0], workspace.gates[0].id), expected
    )


@pytest.mark.parametrize("reverse_outer", [False, True])
@pytest.mark.parametrize("reverse_hole", [False, True])
def test_actual_pixels_preserve_holes_and_opacity_for_either_ring_orientation(
    gate_data, reverse_outer, reverse_hole
):
    workspace, _engine = gate_data
    definition = plot(workspace, style={"fill_color": "#ff0000", "fill_opacity": 0.4})
    outer = [(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)]
    hole = [(0.4, 0.4), (0.6, 0.4), (0.6, 0.6), (0.4, 0.6)]
    overlay = dict(
        kind="polygon",
        vertices=outer[::-1] if reverse_outer else outer,
        holes=[hole[::-1] if reverse_hole else hole],
    )
    image = Figure(figsize=(1, 1), dpi=100, facecolor="white")
    canvas = FigureCanvasSVG(image)
    axes = image.add_axes([0, 0, 1, 1], xlim=(0, 1), ylim=(0, 1))
    axes.set_axis_off()
    fill_gate(axes, overlay, [0, 1, 0, 1], False, definition, "#000000", fraction)
    import io

    output = io.StringIO()
    canvas.print_svg(output)
    pixels = svg_pixels(gate_fill_svg(output.getvalue(), axes))
    np.testing.assert_allclose(pixels[75, 25, :3], [255, 153, 153], atol=1)
    np.testing.assert_array_equal(pixels[50, 50, :3], [255, 255, 255])
    np.testing.assert_array_equal(pixels[5, 5, :3], [255, 255, 255])


def svg_pixels(svg):
    code = """
const { Resvg } = require('@resvg/resvg-js');
const fs = require('node:fs');
const image = new Resvg(fs.readFileSync(0, 'utf8'), {
  fitTo: { mode: 'width', value: 100 }, font: { loadSystemFonts: false }
}).render();
process.stdout.write(image.pixels);
"""
    result = subprocess.run(
        ["node", "-e", code],
        input=svg.encode(),
        capture_output=True,
        check=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape(100, 100, 4)


@pytest.mark.parametrize("topology", ["overlap", "nested", "outside", "double_outer"])
def test_final_svg_subtracts_the_union_of_holes_and_handles_self_intersections(gate_data, topology):
    import io

    workspace, _engine = gate_data
    definition = plot(workspace, style={"fill_color": "#ff0000", "fill_opacity": 0.4})
    outer = [(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)]
    holes = [[(0.3, 0.3), (0.6, 0.3), (0.6, 0.6), (0.3, 0.6)]]
    if topology == "overlap":
        holes.append([(0.4, 0.4), (0.7, 0.4), (0.7, 0.7), (0.4, 0.7)])
    elif topology == "nested":
        holes.append([(0.4, 0.4), (0.55, 0.4), (0.55, 0.55), (0.4, 0.55)])
    elif topology == "outside":
        holes.append([(0.8, 0.8), (1.2, 0.8), (1.2, 1.2), (0.8, 1.2)])
    else:
        outer = [*outer, outer[0], *outer]
        holes = []
    overlay = dict(kind="polygon", vertices=outer, holes=holes)
    image = Figure(figsize=(1, 1), facecolor="white")
    canvas = FigureCanvasSVG(image)
    axes = image.add_axes([0, 0, 1, 1], xlim=(0, 1), ylim=(0, 1))
    axes.set_axis_off()
    fill_gate(axes, overlay, [0, 1, 0, 1], False, definition, "#000000", fraction)
    output = io.StringIO()
    canvas.print_svg(output)
    pixels = svg_pixels(gate_fill_svg(output.getvalue(), axes))
    np.testing.assert_array_equal(pixels[50, 50, :3], [255, 255, 255])
    if topology != "double_outer":
        np.testing.assert_allclose(pixels[75, 25, :3], [255, 153, 153], atol=1)


@pytest.mark.parametrize("mode", ["scatter", "histogram", "3d"])
def test_actual_svg_reports_keep_borders_in_physical_units_and_omit_only_gate_labels(
    gate_data, mode
):
    workspace, engine = gate_data
    styled = plot(workspace, mode)
    svg, manifest = figure(workspace, engine, styled, layers(workspace), 180, 120)
    root = ET.fromstring(svg)
    strokes = [node.get("style", "") for node in root.iter()]
    colour = "#357913" if mode == "3d" else "#246802" if mode == "histogram" else "#135791"
    assert any(f"stroke: {colour}" in style and "stroke-width: 3.375" in style for style in strokes)
    assert not any(gate.name in svg for gate in workspace.gates)
    glyphs = [
        node.get("href", node.get("{http://www.w3.org/1999/xlink}href", "")) for node in root.iter()
    ]
    face = FT2Font(findfont("DejaVu Sans"))
    for character in ["X", "K"]:
        identifier = f"#{face.postscript_name}-{face.get_char_index(ord(character)):x}"
        assert identifier in glyphs
    assert manifest["gate_style"] == STYLE
    assert manifest["layers"][0]["population_count"] == 10
    if mode != "3d":
        assert any("fill: #d12345" in style and "opacity: 0.4" in style for style in strokes)


def test_repeated_report_layers_do_not_compound_the_same_gate_fill(gate_data):
    workspace, engine = gate_data
    definition = plot(workspace)
    payload = plot_payload(
        workspace,
        engine,
        workspace.samples[0].id,
        "X",
        "Y",
        mode="scatter",
        bins=16,
        bounds=[0, 3, 0, 3],
    )
    image = Figure()
    axes = image.add_subplot()
    seen = set()
    gate_artists(axes, payload, [0, 3, 0, 3], False, definition, seen)
    first = len(axes.patches), len(axes.lines)
    gate_artists(axes, copy.deepcopy(payload), [0, 3, 0, 3], False, definition, seen)
    assert first[0] > 0 and first[1] > 0
    assert (len(axes.patches), len(axes.lines)) == first


def test_3d_gate_styles_reuse_identical_event_buffers_and_ids(gate_data):
    workspace, engine = gate_data
    args = workspace, engine, workspace.samples[0].id, "X", "Y", ThreeDView(z="Z")
    before = prepare(*args)
    after = prepare(*args, graph_options=GraphOptions(gate_style=STYLE))
    assert before[0] == after[0]
    assert before[0]["data_key"] == after[0]["data_key"]
    np.testing.assert_array_equal(point_chunk(before, 0, 10), point_chunk(after, 0, 10))


def test_report_history_template_round_trip_and_review_hash_preserve_gate_presentation(
    gate_data, store
):
    workspace, engine = gate_data
    raw = store.data_path(workspace.id, workspace.samples[0].id)
    original_sha = hashlib.sha256(raw.read_bytes()).hexdigest()
    layout = LayoutDefinition(
        name="Gate presentation",
        elements=[ReportElement(kind="plot", plot=plot(workspace), width_mm=180, height_mm=120)],
    )
    plain = copy.deepcopy(layout)
    plain.elements[0].plot.graph_options.gate_style = None
    before = reports.render(
        workspace, engine, reports.ReportRequest(revision=workspace.revision, definition=plain)
    )
    after = reports.render(
        workspace, engine, reports.ReportRequest(revision=workspace.revision, definition=layout)
    )
    assert before["review_hash"] != after["review_hash"]
    template = report_templates.exported(
        workspace, report_templates.TemplateExport(revision=workspace.revision, definition=layout)
    )
    parsed = report_templates.parsed(json.dumps(template).encode())
    assert parsed.definition.elements[0].plot.graph_options.gate_style.model_dump() == STYLE
    saved = store.mutate(
        workspace.id, "Save gate style", lambda doc: doc.layouts.append(layout), workspace.revision
    )
    assert (
        store.get(saved.id).layouts[0].elements[0].plot.graph_options.gate_style.model_dump()
        == STYLE
    )
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == original_sha


def test_api_ignores_valid_presentation_for_science_and_rejects_malformed_style(
    client, gate_data, store
):
    workspace, _engine = gate_data
    api_store = client.app.state.store
    values = np.load(store.data_path(workspace.id, workspace.samples[0].id))
    save_events(api_store.data_path(workspace.id, workspace.samples[0].id), values)
    api_store.create(workspace)
    url = f"/api/workspaces/{workspace.id}/samples/{workspace.samples[0].id}/plot"
    before = client.get(url, params={"x": "X", "y": "Y"})
    after = client.get(
        url, params={"x": "X", "y": "Y", "graph_options": json.dumps({"gate_style": STYLE})}
    )
    assert before.status_code == after.status_code == 200
    assert before.json() == after.json()
    response = client.get(
        url,
        params={
            "x": "X",
            "y": "Y",
            "graph_options": json.dumps({"gate_style": {"show_labels": 1}}),
        },
    )
    assert response.status_code == 422
