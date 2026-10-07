"""Reference counts and probability coverage independent of the drawing code."""

import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter

import numpy as np
import pytest
from cytoforge.graph_views import (
    cdf_values,
    cell_boundary_paths,
    probability_view,
    resolved_options,
)
from cytoforge.models import (
    Channel,
    Gate,
    GraphOptions,
    LayoutDefinition,
    PlotDefinition,
    PlotLayer,
    ReportBatch,
    ReportElement,
    ReportPage,
    Sample,
    Workspace,
)
from cytoforge.plotting import plot_payload
from cytoforge.report_plots import figure
from cytoforge.science import Engine, save_events
from pydantic import ValidationError


def fixture(store, values):
    values = np.asarray(values, dtype=float)
    sample = Sample(
        name="Graph reference",
        channels=[Channel(name="X"), Channel(name="Y")],
        event_count=len(values),
    )
    doc = Workspace(name="Independent graph truth", samples=[sample])
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    return store.create(doc), Engine(store)


def test_cdf_ties_and_zoom_keep_the_full_finite_denominator(store):
    values = [0, 0, 1, 1, 2, 3, np.nan, -np.inf]
    doc, engine = fixture(store, np.column_stack([values, values]))
    payload = plot_payload(doc, engine, doc.samples[0].id, "X", mode="cdf", bins=16, bounds=[0, 2])
    reference = [sum(v <= edge for v in values if np.isfinite(v)) for edge in payload["edges"]]
    assert payload["count"] == 8 and payload["finite_count"] == 6 and payload["visible_count"] == 5
    assert payload["cdf_counts"] == reference
    assert payload["cdf_percent"][0] == pytest.approx(100 / 3)
    assert payload["cdf_percent"][-1] == pytest.approx(500 / 6)
    zoom = plot_payload(doc, engine, doc.samples[0].id, "X", mode="cdf", bins=16, bounds=[1, 2])
    assert zoom["cdf_counts"][0] == 4 and zoom["cdf_counts"][-1] == 5
    assert zoom["cdf_below_view"] == 2 and zoom["cdf_above_view"] == 1
    assert sum(zoom["counts"]) == 3


def test_cdf_empty_duplicate_edges_and_extreme_values():
    assert cdf_values(np.array([]), np.array([0, 1]))["cdf_percent"] == [None, None]
    maximum = np.finfo(float).max
    values = np.array([-maximum, 0, 0, maximum])
    edges = np.array([-maximum, 0, 0, maximum])
    actual = cdf_values(values, edges)
    assert actual["cdf_counts"] == [1, 3, 3, 4]
    assert actual["cdf_percent"] == [25, 75, 75, 100]


def test_cdf_population_mask_and_missing_y_values(store):
    doc, engine = fixture(store, [[0, np.nan], [1, 1], [2, 2], [3, 3], [4, 4]])
    gate = Gate(sample_id=doc.samples[0].id, name="Subset", kind="range", x="X", bounds=[1, 4])
    doc.gates = [gate]
    payload = plot_payload(
        doc, engine, doc.samples[0].id, "X", gate_id=gate.id, mode="cdf", bins=16, bounds=[1, 2]
    )
    assert payload["count"] == payload["cdf_denominator"] == 3
    assert payload["cdf_counts"][0] == 1 and payload["cdf_counts"][-1] == 2
    full = plot_payload(doc, engine, doc.samples[0].id, "X", mode="cdf", bins=16, bounds=[0, 4])
    assert full["cdf_denominator"] == 5  # One-dimensional plots do not exclude nonfinite Y.


def test_probability_levels_have_literal_full_population_coverage():
    # Four occupied bins contain 40, 30, 20, 10 events; 5% targets expose density ties.
    x = np.repeat([0.125, 0.375, 0.625, 0.875], [40, 30, 20, 10])
    y = np.full(100, 0.125)
    options = resolved_options(GraphOptions(smooth=False, contour_spacing="10"), "contour")
    result = probability_view(x, y, [0, 1, 0, 1], [0, 1, 0, 1], 4, options)
    assert result["probability_denominator"] == result["density_count"] == 100
    expected = {10: 1.0, 20: 0.9, 30: 0.7, 40: 0.4}
    for level in result["probability_levels"]:
        assert level["estimated_probability"] == pytest.approx(expected[level["threshold"]])
        assert level["binned_event_probability"] == pytest.approx(expected[level["threshold"]])
        assert level["estimated_probability"] >= level["probability"]
    assert result["outlier_count"] == 10
    assert (
        sum(map(len, (path for contour in result["contours"] for path in contour["paths"])))
        == result["contour_vertices"]
    )
    assert result["contour_vertices"] > 0


def assert_cell_region(paths, included):
    """Compare literal oriented cell edges, independently of raster contouring."""
    rows, columns = included.shape
    expected = Counter()
    for y, x in np.argwhere(included):
        corners = [(x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1), (x, y)]
        for start, end in zip(corners[:-1], corners[1:], strict=True):
            if expected[end, start]:
                del expected[end, start]
            else:
                expected[start, end] += 1
    actual = Counter()
    area = 0.0
    for path in paths:
        points = np.asarray(path) * [columns, rows]
        assert len(points) >= 5
        assert np.array_equal(points[0], points[-1])
        assert np.allclose(points, np.round(points), atol=1e-12, rtol=0)
        assert np.all((points >= 0) & (points <= [columns, rows]))
        points = np.round(points).astype(int)
        area += np.sum(points[:-1, 0] * points[1:, 1] - points[1:, 0] * points[:-1, 1]) / 2
        for start, end in zip(points[:-1], points[1:], strict=True):
            difference = end - start
            assert np.count_nonzero(difference) == 1  # Axis-aligned, nonzero edges.
            step = np.sign(difference)
            length = int(np.max(np.abs(difference)))
            for offset in range(length):
                a = tuple(start + step * offset)
                b = tuple(start + step * (offset + 1))
                actual[a, b] += 1
    assert actual == expected
    assert area == int(included.sum())  # Hole orientation subtracts excluded area.


def test_cell_boundaries_exhaustively_preserve_holes_corners_and_domain_edges():
    for bits in range(512):
        included = np.array([(bits >> i) & 1 for i in range(9)], dtype=bool).reshape(3, 3)
        assert_cell_region(list(cell_boundary_paths(included)), included)
    diagonal = list(cell_boundary_paths(np.eye(3, dtype=bool)))
    assert len(diagonal) == 3  # Corner contacts remain independent loops.
    rng = np.random.default_rng(5301)
    for shape in [(1, 17), (13, 1), (7, 11), (19, 23)]:
        for _ in range(8):
            included = rng.random(shape) > 0.5
            assert_cell_region(list(cell_boundary_paths(included)), included)


@pytest.mark.parametrize("mixed", [False, True])
def test_thousands_of_regions_keep_complete_boundaries_across_vector_batches(mixed):
    rows, columns = np.indices((128, 129))
    included = (rows + columns) % 2 == 0
    if mixed:
        included[30:70, 10:55] = True
        included[40:60, 20:30] = False
    paths = list(cell_boundary_paths(included))
    assert len(paths) > 4096
    assert_cell_region(paths, included)


@pytest.mark.parametrize("bins", [16, 32, 160, 384])
@pytest.mark.parametrize("options", [{"smooth": False}, {"smooth": True, "sigma": 0}])
def test_sparse_tied_peaks_enclose_complete_bins_without_changing_mass(bins, options):
    x, y = np.meshgrid(np.arange(5.0), np.arange(5.0))
    x, y = x.ravel(), y.ravel()
    result = probability_view(
        x,
        y,
        [-1, 11, -1, 11],
        [-1, 11, -1, 11],
        bins,
        resolved_options(GraphOptions(**options, contour_spacing="10"), "contour"),
    )
    assert result["density_count"] == result["probability_denominator"] == 25
    assert result["outlier_count"] == result["outlier_displayed_count"] == 0
    assert len(result["contours"]) == 1
    contour = result["contours"][0]
    assert contour["threshold"] == 1 and contour["geometry"] == "bin_cells"
    raw, _, _ = np.histogram2d(x, y, bins=bins, range=[[-1, 11], [-1, 11]])
    assert_cell_region(contour["paths"], raw.T >= 1)
    if bins >= 32:
        assert len(contour["paths"]) == 25
        for path in contour["paths"]:
            assert np.ptp(path, axis=0) == pytest.approx([1 / bins, 1 / bins])
    for level in result["probability_levels"]:
        assert level["threshold"] == 1 and level["tied_bins"] == 25
        assert level["estimated_probability"] == level["binned_event_probability"] == 1
    assert "unsmoothed" in result["probability_method"]
    assert not result["contours_truncated"]
    json.dumps(result, allow_nan=False)


def test_smoothed_peak_uses_bin_edges_while_other_levels_keep_interpolation():
    options = resolved_options(GraphOptions(smooth=True, contour_spacing="2"), "contour")
    result = probability_view(
        np.array([0.5]), np.array([0.5]), [0, 1, 0, 1], [0, 1, 0, 1], 16, options
    )
    grid = np.asarray(result["density_field"]).reshape(16, 16)
    peak_contour = next(c for c in result["contours"] if c["threshold"] == grid.max())
    assert peak_contour["geometry"] == "bin_cells"
    assert_cell_region(peak_contour["paths"], grid == grid.max())
    other = [c for c in result["contours"] if c["threshold"] != grid.max()]
    assert other and all(c["geometry"] == "interpolated_centers" for c in other)
    assert result["density_count"] == result["probability_denominator"] == 1
    assert grid.sum() == pytest.approx(1)
    assert "Gaussian" in result["probability_method"]


def test_cell_boundary_drawing_limit_leaves_all_probabilities_and_counts_complete():
    rows, columns = np.indices((256, 256))
    included = (rows + columns) % 2 == 0
    x, y = (columns[included] + 0.5) / 256, (rows[included] + 0.5) / 256
    result = probability_view(
        x,
        y,
        [0, 1, 0, 1],
        [0, 1, 0, 1],
        256,
        resolved_options(GraphOptions(smooth=False, contour_spacing="10"), "contour"),
    )
    assert result["contours_truncated"] and result["contour_vertices"] == 150000
    assert len(result["contours"][0]["paths"]) == 30000
    assert result["density_count"] == result["probability_denominator"] == 32768
    assert sum(result["density_field"]) == 32768 and result["outlier_count"] == 0
    assert len(result["probability_levels"]) == 9
    assert all(
        level["estimated_probability"] == level["binned_event_probability"] == 1
        and level["tied_bins"] == 32768
        for level in result["probability_levels"]
    )


@pytest.mark.parametrize("mode", ["contour", "zebra"])
def test_unsmoothed_vector_report_contains_visible_bin_boundaries_and_provenance(store, mode):
    x, y = np.meshgrid(np.arange(5.0), np.arange(5.0))
    doc, engine = fixture(store, np.column_stack([x.ravel(), y.ravel()]))
    before = doc.model_dump_json()
    definition = PlotDefinition(
        sample_id=doc.samples[0].id,
        x="X",
        y="Y",
        mode=mode,
        bins=160,
        bounds=[-1, 11, -1, 11],
        graph_options=GraphOptions(smooth=False),
    )
    layer = dict(
        sample_id=doc.samples[0].id,
        source_sample_id=doc.samples[0].id,
        gate_id=None,
        coordinate_gate_id=None,
        label="25 literal events",
        color="#087e8b",
        locked_control=False,
    )
    svg, manifest = figure(doc, engine, definition, [layer], 150, 100)
    outlines = [
        element
        for element in ET.fromstring(svg).iter()
        if element.tag.endswith("path") and "stroke: #087e8b" in element.get("style", "")
    ]
    assert len(outlines) == 25 and "<image" not in svg
    for outline in outlines:
        numbers = [float(v) for v in re.findall(r"[-+]?\d+(?:\.\d+)?", outline.get("d", ""))]
        points = np.asarray(numbers).reshape(-1, 2)
        assert np.all(np.ptp(points, axis=0) > 0.5)  # Physical SVG points, not ULP-sized paths.
    source = manifest["layers"][0]
    assert source["population_count"] == source["probability_denominator"] == 25
    assert source["contour_geometry_levels"] == [{"threshold": 1.0, "geometry": "bin_cells"}]
    assert source["contour_geometry"] and "unsmoothed" in source["probability_method"]
    assert doc.model_dump_json() == before


def test_probability_missing_mass_is_not_renormalized():
    options = resolved_options(GraphOptions(smooth=False), "contour")
    result = probability_view(
        np.array([0.5, 2.0]), np.array([0.5, 2.0]), [0, 1, 0, 1], [0, 1, 0, 1], 16, options
    )
    assert result["probability_denominator"] == 2 and result["density_count"] == 1
    unavailable = [level for level in result["probability_levels"] if level["probability"] > 0.5]
    assert all(level["threshold"] is None and level["reason"] for level in unavailable)
    assert (
        max(level.get("estimated_probability", 0) for level in result["probability_levels"]) == 0.5
    )
    assert result["outlier_count"] == 1 and result["outlier_visible_count"] == 0


@pytest.mark.parametrize("mode", ["contour", "zebra", "pseudocolor", "density", "scatter"])
def test_bivariate_modes_preserve_counts_masks_and_finite_extremes(store, mode):
    maximum = np.finfo(float).max
    doc, engine = fixture(
        store, [[-maximum, -maximum], [0, 0], [0, 0], [maximum, maximum], [np.nan, 1]]
    )
    payload = plot_payload(
        doc,
        engine,
        doc.samples[0].id,
        "X",
        "Y",
        mode=mode,
        bins=16,
        graph_options={"axis_extent": "full"},
    )
    assert payload["count"] == 5 and payload["finite_count"] == payload["visible_count"] == 4
    json.dumps(payload, allow_nan=False)
    if mode != "scatter":
        assert sum(payload["counts"]) == 4
    if "density_field" in payload:
        assert sum(payload["density_field"]) == pytest.approx(4)
    if mode in {"contour", "zebra"}:
        assert payload["probability_denominator"] == 4
        assert all(
            np.all(np.isfinite(path))
            for contour in payload["contours"]
            for path in contour["paths"]
        )


def test_contour_zoom_keeps_estimate_but_changes_visible_counts(store):
    values = np.random.default_rng(721).normal(size=(3000, 2))
    doc, engine = fixture(store, values)
    full = plot_payload(doc, engine, doc.samples[0].id, "X", "Y", mode="zebra", bins=32)
    zoom = plot_payload(
        doc,
        engine,
        doc.samples[0].id,
        "X",
        "Y",
        mode="zebra",
        bins=32,
        bounds=[-0.5, 0.5, -0.5, 0.5],
    )
    assert zoom["density_bounds"] == full["density_bounds"]
    assert zoom["probability_levels"] == full["probability_levels"]
    assert zoom["density_field"] == full["density_field"]
    assert zoom["visible_count"] == int(np.all((values >= -0.5) & (values <= 0.5), axis=1).sum())


def test_sampling_and_axis_extent_are_explicit_and_do_not_change_population(store):
    values = np.column_stack([np.arange(10000.0), np.arange(10000.0)])
    values[-1] = [1e9, 1e9]
    doc, engine = fixture(store, values)
    robust = plot_payload(
        doc, engine, doc.samples[0].id, "X", "Y", mode="scatter", graph_options={"point_limit": 100}
    )
    full = plot_payload(
        doc,
        engine,
        doc.samples[0].id,
        "X",
        "Y",
        mode="scatter",
        graph_options={"point_limit": 100, "axis_extent": "full"},
    )
    assert (
        robust["count"] == full["count"] == full["finite_count"] == full["visible_count"] == 10000
    )
    assert robust["visible_count"] < full["visible_count"]
    assert full["displayed_count"] == 100 and full["point_sampling"]
    assert (
        full["points"]
        == plot_payload(
            doc,
            engine,
            doc.samples[0].id,
            "X",
            "Y",
            mode="scatter",
            graph_options={"point_limit": 100, "axis_extent": "full"},
        )["points"]
    )


@pytest.mark.parametrize("mode", ["cdf", "contour", "zebra", "pseudocolor"])
def test_publication_figures_use_same_scientific_values(store, mode):
    doc, engine = fixture(store, [[0, 0], [0, 0], [1, 1], [2, 2], [3, 3], [np.nan, 0]])
    definition = PlotDefinition(
        sample_id=doc.samples[0].id,
        x="X",
        y=None if mode == "cdf" else "Y",
        mode=mode,
        bins=16,
        graph_options=GraphOptions(palette="viridis", smooth=False),
        bounds=[0, 2] if mode == "cdf" else [0, 3, 0, 3],
    )
    layer = dict(
        sample_id=doc.samples[0].id,
        source_sample_id=doc.samples[0].id,
        gate_id=None,
        coordinate_gate_id=None,
        label="Reference",
        color="#087e8b",
        locked_control=False,
    )
    svg, manifest = figure(doc, engine, definition, [layer], 100, 90)
    assert ET.fromstring(svg).tag.endswith("svg")
    assert "<image" not in svg
    scientific = manifest["layers"][0]
    assert scientific["population_count"] == 6 and scientific["finite_count"] == 5
    assert scientific["graph_options"]["palette"] == "viridis"
    if mode == "cdf":
        assert scientific["cdf_denominator"] == 5
        assert scientific["displayed_values"][0] == 40 and scientific["displayed_values"][-1] == 80
    elif mode in {"contour", "zebra"}:
        assert scientific["probability_denominator"] == 5 and scientific["probability_levels"]


@pytest.mark.parametrize(
    "options",
    [
        {"point_limit": 99},
        {"point_limit": 100001},
        {"sigma": float("nan")},
        {"sigma": 5},
        {"palette": "unknown"},
        {"contour_spacing": "0"},
        {"secret": "ignored"},
    ],
)
def test_invalid_graph_settings_are_rejected(options):
    with pytest.raises(ValidationError):
        GraphOptions.model_validate(options)


def test_cdf_report_settings_validate_dimensions_and_normalization():
    sample = Sample(name="Validation", channels=[Channel(name="X")], event_count=0)
    assert PlotDefinition(sample_id=sample.id, x="X", mode="cdf", bounds=[0, 1]).mode == "cdf"
    for changes in [{"y": "X"}, {"bounds": [0, 1, 0, 1]}, {"normalization": "peak"}]:
        with pytest.raises(ValidationError):
            PlotDefinition(sample_id=sample.id, x="X", mode="cdf", **changes)


@pytest.mark.parametrize("mode", ["cdf", "contour", "zebra", "pseudocolor"])
def test_empty_and_single_value_graphs_are_defined_and_json_finite(store, mode):
    for values in [
        np.empty((0, 2)),
        np.array([[7.0, 7.0]]),
        np.full((20, 2), 3.0),
        np.array([[np.nan, np.nan]]),
    ]:
        doc, engine = fixture(store, values)
        payload = plot_payload(
            doc, engine, doc.samples[0].id, "X", None if mode == "cdf" else "Y", mode=mode, bins=16
        )
        json.dumps(payload, allow_nan=False)
        assert payload["count"] == len(values)
        if mode == "cdf" and not payload["finite_count"]:
            assert (
                all(v is None for v in payload["cdf_percent"]) and payload["cdf_undefined_reason"]
            )
        if mode in {"contour", "zebra"} and payload["finite_count"]:
            assert payload["contour_vertices"] > 0


def test_graph_report_api_overlay_portable_export_and_project_roundtrip(client):
    store = client.app.state.store
    doc, _ = fixture(store, [[0, 0], [0, 0], [1, 1], [2, 2], [3, 3], [np.nan, 0]])
    other = Sample(
        name="Second CDF denominator",
        channels=[Channel(name="X"), Channel(name="Y")],
        event_count=3,
    )
    other.sha256 = save_events(
        store.data_path(doc.id, other.id), np.array([[1.0, 1.0], [2.0, 2.0], [3.0, 3.0]])
    )
    doc.samples.append(other)
    modes = ["cdf", "contour", "zebra", "pseudocolor"]
    layout = LayoutDefinition(
        name="Probability publication",
        pages=[ReportPage() for _ in modes],
        batch=ReportBatch(mode="off"),
        elements=[
            ReportElement(
                kind="plot",
                page=i,
                title=mode,
                width_mm=150,
                height_mm=110,
                plot=PlotDefinition(
                    sample_id=doc.samples[0].id,
                    x="X",
                    y=None if mode == "cdf" else "Y",
                    mode=mode,
                    bins=32,
                    graph_options=GraphOptions(palette="viridis", axis_extent="full"),
                    bounds=[0, 2] if mode == "cdf" else [0, 3, 0, 3],
                    overlays=[PlotLayer(sample_id=other.id)] if mode == "cdf" else [],
                ),
            )
            for i, mode in enumerate(modes)
        ],
    )
    doc.layouts = [layout]

    def save_graphs(current):
        current.samples = doc.samples
        current.layouts = doc.layouts

    doc = store.mutate(doc.id, "Save graph publication", save_graphs, doc.revision)
    base = f"/api/workspaces/{doc.id}"
    payload = client.get(
        base + f"/samples/{doc.samples[0].id}/plot",
        params={"x": "X", "mode": "cdf", "bins": 32, "bounds": "[0,2]"},
    ).json()
    assert payload["cdf_denominator"] == 5 and payload["cdf_counts"][-1] == 4
    body = dict(revision=doc.revision, definition=layout.model_dump(), validate_sources=True)
    review = client.post(base + "/reports/plan", json=body)
    assert review.status_code == 200, review.text
    assert review.json()["page_count"] == 4 and review.json()["exportable"]
    response = client.post(
        base + "/reports/export",
        json={**body, "review_hash": review.json()["review_hash"], "format": "zip"},
    )
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        cdf_layers = manifest["pages"][0]["elements"][0]["layers"]
        assert [layer["cdf_denominator"] for layer in cdf_layers] == [5, 3]
        assert [layer["displayed_values"][-1] for layer in cdf_layers] == pytest.approx(
            [80, 200 / 3]
        )
        for i, mode in enumerate(modes):
            assert manifest["pages"][i]["elements"][0]["mode"] == mode
            assert "<image" not in archive.read(f"page-{i + 1:04}.svg").decode()
    project = client.get(base + "/export/project")
    restored = client.post(
        "/api/import/project",
        files={"file": ("graphs.cytoforge", project.content, "application/zip")},
    )
    assert restored.status_code == 200, restored.text
    copy = restored.json()
    assert copy["layouts"][0]["elements"] == [e.model_dump() for e in layout.elements]
    for i, mode in enumerate(modes):
        rendered = client.post(
            f"/api/workspaces/{copy['id']}/reports/render",
            json={
                "revision": copy["revision"],
                "definition": copy["layouts"][0],
                "page": i,
                "validate_sources": True,
            },
        )
        assert rendered.status_code == 200, rendered.text
        assert (
            rendered.json()["exportable"]
            and rendered.json()["manifest"]["elements"][0]["mode"] == mode
        )
