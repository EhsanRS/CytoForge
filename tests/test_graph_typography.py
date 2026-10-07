"""Publication glyphs, physical sizes and unchanged full-event calculations."""

import copy
import hashlib
import re
from xml.etree import ElementTree as ET

import numpy as np
import pytest
from cytoforge import reports
from cytoforge.graph_views import resolved_options
from cytoforge.models import (
    Channel,
    Gate,
    GraphOptions,
    LayoutDefinition,
    PlotDefinition,
    ReportElement,
    Sample,
    ThreeDView,
    Workspace,
)
from cytoforge.plotting import plot_payload
from cytoforge.report_graph_typography import caption_figure
from cytoforge.report_plots import figure
from cytoforge.science import Engine, save_events
from cytoforge.three_dimensional import point_chunk, prepare
from pydantic import ValidationError

STYLES = {
    "axis_labels": dict(
        font_size_pt=12,
        font_family="serif",
        font_weight="bold",
        font_style="italic",
        color="#112233",
    ),
    "tick_labels": dict(font_size_pt=9, font_family="mono", color="#223344"),
    "gate_labels": dict(font_size_pt=10, font_family="sans", font_style="italic", color="#334455"),
    "statistics": dict(font_size_pt=8, font_family="mono", font_weight="bold", color="#445566"),
    "legend": dict(font_size_pt=11, font_family="serif", color="#556677"),
    "title": dict(font_size_pt=14, font_family="serif", font_weight="bold", color="#667788"),
}


def known_workspace(store):
    sample = Sample(
        name="Known $ values", channels=[Channel(name=name) for name in "XYZ"], event_count=4
    )
    workspace = Workspace(name="Graph typography truth", samples=[sample])
    sample.sha256 = save_events(
        store.data_path(workspace.id, sample.id),
        np.array([[0, 0, 0], [1, 1, 1], [2, 2, 2], [np.nan, np.nan, np.nan]]),
    )
    workspace.gates = [
        Gate(name="Positive $ gate", sample_id=sample.id, kind="range", x="X", bounds=[1, 2])
    ]
    return store.create(workspace), Engine(store)


@pytest.fixture
def typography_data(store):
    return known_workspace(store)


def definition(workspace, mode="density", styles=STYLES):
    return PlotDefinition(
        sample_id=workspace.samples[0].id,
        x="X",
        y=None if mode in {"histogram", "cdf"} else "Y",
        mode=mode,
        three_d=ThreeDView(z="Z") if mode == "3d" else None,
        bounds=[-1, 3] * (1 if mode in {"histogram", "cdf"} else 3 if mode == "3d" else 2),
        bins=16,
        graph_options=GraphOptions(typography=styles),
    )


def layers(workspace):
    sample = workspace.samples[0]
    return [
        dict(
            sample_id=sample.id,
            source_sample_id=sample.id,
            gate_id=None,
            coordinate_gate_id=None,
            color="#087e8b",
            label="Known values",
            locked_control=False,
        )
    ]


def assert_glyph_style(svg, color, size, face):
    root = ET.fromstring(svg)
    groups = [
        group
        for group in root.iter()
        if color in group.get("style", "") or group.get("fill") == color
    ]
    assert groups, f"No rendered text in {color}"
    scales = [
        re.search(r"scale\(([\d.]+) (-[\d.]+)\)", node.get("transform", ""))
        for group in groups
        for node in group.iter()
    ]
    assert any(
        scale
        and float(scale[1]) == pytest.approx(size / 100)
        and float(scale[2]) == pytest.approx(-size / 100)
        for scale in scales
    ), f"No actual {size} pt glyph scaling"
    assert any(
        face in (node.get("{http://www.w3.org/1999/xlink}href", "") or node.get("href", ""))
        for group in groups
        for node in group.iter()
    ), f"No rendered {face} glyphs"


def test_legacy_defaults_and_empty_styles_have_identical_serialization():
    expected = dict(
        smooth=None,
        sigma=1.0,
        contour_spacing=None,
        show_outliers=True,
        palette="ocean",
        axis_extent="robust",
        point_limit=12000,
    )
    for typography in [None, {}, {"axis_labels": {}}, {"title": {"color": None}}]:
        assert GraphOptions(typography=typography).model_dump() == expected


@pytest.mark.parametrize(
    "bad",
    [
        {"font_size_pt": 3},
        {"font_size_pt": 145},
        {"font_size_pt": True},
        {"font_size_pt": "12"},
        {"font_size_pt": float("inf")},
        {"font_size_pt": float("nan")},
        {"font_family": "Arial"},
        {"font_family": "url(secret)"},
        {"font_weight": 700},
        {"font_style": "underline"},
        {"color": "red"},
        {"color": "#123456;display:none"},
        {"color": "#fff"},
        {"unknown": 12},
    ],
)
def test_reject_invalid_text_styles(bad):
    with pytest.raises(ValidationError):
        GraphOptions(typography={"axis_labels": bad})


@pytest.mark.parametrize(
    "mode", ["density", "scatter", "histogram", "cdf", "contour", "zebra", "pseudocolor"]
)
def test_fonts_leave_scientific_payload_and_cache_options_unchanged(typography_data, mode):
    workspace, engine = typography_data
    base = definition(workspace, mode, None)
    styled = definition(workspace, mode)
    kwargs = dict(x=base.x, y=base.y, mode=mode, bounds=base.bounds, bins=16)
    before = plot_payload(
        workspace,
        engine,
        workspace.samples[0].id,
        graph_options=base.graph_options.model_dump(),
        **kwargs,
    )
    after = plot_payload(
        workspace,
        engine,
        workspace.samples[0].id,
        graph_options=styled.graph_options.model_dump(),
        **kwargs,
    )
    assert after == before
    assert after["count"] == 4 and after["finite_count"] == 3 and after["visible_count"] == 3
    assert resolved_options(base.graph_options, mode) == resolved_options(
        styled.graph_options, mode
    )


@pytest.mark.parametrize("mode", ["histogram", "density", "3d"])
def test_real_svg_glyph_faces_sizes_colors_and_population_counts(typography_data, mode):
    workspace, engine = typography_data
    styled = definition(workspace, mode)
    svg, manifest = figure(workspace, engine, styled, layers(workspace), 180, 120)
    assert manifest["layers"][0]["population_count"] == 4
    assert manifest["typography_units"] == "pt" and manifest["typography"] == STYLES
    assert_glyph_style(svg, "#112233", 12, "DejaVuSerif-BoldItalic")
    assert_glyph_style(svg, "#223344", 9, "DejaVuSansMono")
    assert_glyph_style(svg, "#445566", 8, "DejaVuSansMono-Bold")
    assert_glyph_style(svg, "#556677", 11, "DejaVuSerif")
    if mode != "3d":
        assert_glyph_style(svg, "#334455", 10, "DejaVuSans-Oblique")


def test_3d_typography_reuses_the_same_full_event_buffers(typography_data):
    workspace, engine = typography_data
    view = ThreeDView(z="Z")
    first = prepare(workspace, engine, workspace.samples[0].id, "X", "Y", view)
    before = point_chunk(first, 0, 10)
    styled = prepare(
        workspace,
        engine,
        workspace.samples[0].id,
        "X",
        "Y",
        view,
        graph_options=GraphOptions(typography=STYLES).model_dump(),
    )
    after = point_chunk(styled, 0, 10)
    assert styled[0] == first[0] and styled[0]["count"] == 4 and styled[0]["finite_count"] == 3
    assert styled[0]["data_key"] == first[0]["data_key"]
    assert np.array_equal(after, before)


@pytest.mark.parametrize("size", [14, 36, 72])
@pytest.mark.parametrize(
    "family,face",
    [("sans", "DejaVu Sans"), ("serif", "DejaVu Serif"), ("mono", "DejaVu Sans Mono")],
)
def test_caption_descenders_fit_the_actual_svg_frame(typography_data, size, family, face):
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextPath

    workspace, _ = typography_data
    plot = definition(
        workspace,
        styles={
            "title": {
                "font_size_pt": size,
                "font_family": family,
                "font_weight": "bold",
                "color": "#667788",
            }
        },
    )
    element = ReportElement(kind="plot", plot=plot, title="gypjq", width_mm=180, height_mm=240)
    height = reports.caption_height(element.title, element)
    svg, omitted = caption_figure(element.title, element, element.width_mm, height)
    assert omitted == 0
    actual = TextPath(
        (0, 0), element.title, size=size, prop=FontProperties(family=face, weight="bold")
    ).get_extents()
    root = ET.fromstring(svg)
    frame_height_pt = float(root.get("height").removesuffix("pt"))
    text_group = next(node for node in root.iter() if "#667788" in node.get("style", ""))
    translation = re.search(r"translate\([\d.]+ ([\d.]+)\)", text_group.get("transform", ""))
    assert translation
    baseline_pt = float(translation[1])
    assert baseline_pt - actual.ymin <= frame_height_pt + 1e-5
    assert baseline_pt - actual.ymax >= 0


def test_title_is_rendered_as_portable_physical_glyphs(typography_data):
    workspace, _ = typography_data
    element = ReportElement(
        kind="plot", plot=definition(workspace), title="Known title", width_mm=180, height_mm=120
    )
    svg, omitted = caption_figure(element.title, element, 180, 20)
    assert omitted == 0
    assert_glyph_style(svg, "#667788", 14, "DejaVuSerif-Bold")
    assert "<text" not in svg


@pytest.mark.parametrize("role", ["axis_labels", "tick_labels", "statistics"])
def test_unfittable_fonts_produce_an_actionable_error(typography_data, role):
    workspace, engine = typography_data
    plot = definition(workspace, styles={role: {"font_size_pt": 144}})
    with pytest.raises(ValueError, match="Increase this figure.*reduce"):
        figure(workspace, engine, plot, layers(workspace), 60, 45)


def test_typography_survives_workspace_history_and_changes_review_hash(typography_data, store):
    workspace, engine = typography_data
    original_raw = hashlib.sha256(
        store.data_path(workspace.id, workspace.samples[0].id).read_bytes()
    ).hexdigest()
    element = ReportElement(
        kind="plot", plot=definition(workspace), title="Known title", width_mm=180, height_mm=120
    )
    layout = LayoutDefinition(name="Known fonts", elements=[element])
    plain = copy.deepcopy(layout)
    plain.elements[0].plot.graph_options.typography = None
    plain_request = reports.ReportRequest(revision=workspace.revision, definition=plain)
    styled_request = reports.ReportRequest(revision=workspace.revision, definition=layout)
    before = reports.render(workspace, engine, plain_request)
    after = reports.render(workspace, engine, styled_request)
    assert after["review_hash"] != before["review_hash"]
    assert_glyph_style(after["svg"], "#667788", 14, "DejaVuSerif-Bold")
    saved = store.mutate(
        workspace.id, "Save graph fonts", lambda doc: doc.layouts.append(layout), workspace.revision
    )
    restored = store.get(saved.id)
    assert restored.layouts[0].elements[0].plot.graph_options.typography.model_dump() == STYLES
    assert (
        hashlib.sha256(store.data_path(saved.id, saved.samples[0].id).read_bytes()).hexdigest()
        == original_raw
    )


def test_api_accepts_styling_and_rejects_invalid_fields_without_changing_events(client):
    workspace, _ = known_workspace(client.app.state.store)
    url = f"/api/workspaces/{workspace.id}/samples/{workspace.samples[0].id}/plot"
    import json

    before = client.get(url, params={"x": "X", "y": "Y"})
    after = client.get(
        url, params={"x": "X", "y": "Y", "graph_options": json.dumps({"typography": STYLES})}
    )
    assert before.status_code == after.status_code == 200 and before.json() == after.json()
    invalid = client.get(
        url,
        params={
            "x": "X",
            "y": "Y",
            "graph_options": json.dumps({"typography": {"unknown": {"color": "#112233"}}}),
        },
    )
    assert invalid.status_code == 422
