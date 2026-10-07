"""Native graph geometry and exported figures agree, including extreme coordinates."""

import itertools
import json
import subprocess
from xml.etree import ElementTree as ET

import numpy as np
import pytest
from cytoforge.models import ComparisonPresentation
from cytoforge.population_comparison_views import figure_geometry, figure_svg


@pytest.fixture(scope="module")
def native_geometry(tmp_path_factory):
    output = tmp_path_factory.mktemp("comparison-geometry")
    subprocess.run(
        [
            "node",
            "node_modules/typescript/lib/tsc.js",
            "--ignoreConfig",
            "--target",
            "es2022",
            "--module",
            "esnext",
            "--moduleResolution",
            "bundler",
            "--skipLibCheck",
            "--outDir",
            str(output),
            "frontend/src/comparisons.ts",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return output / "comparisons.js"


def graph_data(edges):
    control = [0.4, 0.3, 0.2, 0.1]
    target = [0.1, 0.2, 0.3, 0.4]
    return {
        "edges": edges,
        "control_fraction": control,
        "target_fraction": target,
        "control_cdf": np.cumsum(control).tolist(),
        "target_cdf": np.cumsum(target).tolist(),
        "difference": (np.asarray(target) - control).tolist(),
        "individual_controls": [{"fraction": [0, 1, 0, 0]}, {"fraction": [0.5, 0, 0, 0.5]}],
        "row": {
            "metrics": {"ks_at_coordinate": edges[2]},
            "control_finite_count": 100,
            "finite_count": 200,
        },
        "parameter": {"label": '<X & "quoted">', "channel": "X", "transform": {"kind": "linear"}},
        "stale": False,
    }


def evaluate_native(module, cases):
    script = """
      import {readFileSync} from 'node:fs';
      const source = readFileSync(process.argv[1]);
      const {comparisonGeometry, uniqueComparisonPopulations} = await import(
        'data:text/javascript;base64,' + source.toString('base64'));
      const cases = JSON.parse(readFileSync(0, 'utf8'));
      const results = cases.map(({data, view}) => comparisonGeometry(data, {
        mode:view.mode, smoothing:view.smoothing, controlColor:view.control_color,
        targetColor:view.target_color, differenceScale:view.difference_scale,
        showIndividuals:view.show_individuals,
      }));
      const populations = uniqueComparisonPopulations([
        {sample_id:'one', gate_id:null}, {sample_id:'one', gate_id:'subset'},
        {sample_id:'two', gate_id:'unique'}, {sample_id:'three', gate_id:null},
      ]);
      process.stdout.write(JSON.stringify({results, populations}));
    """
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script, str(module)],
        input=json.dumps(cases),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(completed.stdout)


def test_actual_frontend_and_portable_geometry_match_all_presentations(native_geometry):
    cases = [
        {
            "data": graph_data(edges),
            "view": ComparisonPresentation(
                mode=mode,
                smoothing=smoothing,
                control_color="#ff0000",
                target_color="#123abc",
                difference_scale=scale,
                show_individuals=individuals,
            ).model_dump(),
        }
        for edges, mode, smoothing, scale, individuals in itertools.product(
            [
                [0, 1, 2, 3, 4],
                [-1e308, -5e307, 0, 5e307, 1e308],
                [-4e-320, -2e-320, 0, 2e-320, 4e-320],
            ],
            ["histogram", "cdf", "difference"],
            [0, 1.25, 16],
            [0.1, 10],
            [False, True],
        )
    ]
    native = evaluate_native(native_geometry, cases)
    assert native["populations"] == {"two": "unique", "three": None}
    for case, actual in zip(cases, native["results"], strict=True):
        expected = figure_geometry(case["data"], case["view"])
        assert actual["zeroY"] == expected["zeroY"]
        assert actual["ksX"] == pytest.approx(expected["ksX"])
        assert len(actual["curves"]) == len(expected["curves"])
        for curve, reference in zip(actual["curves"], expected["curves"], strict=True):
            points = np.array(
                [[float(v) for v in pair.split(",")] for pair in curve["points"].split()]
            )
            assert np.all(np.isfinite(points))
            np.testing.assert_allclose(points, reference["points"], rtol=1e-12, atol=1e-10)
            assert {k: v for k, v in curve.items() if k != "points"} == {
                k: v for k, v in reference.items() if k != "points"
            }


def test_signed_difference_zero_and_custom_tints_are_preserved_in_svg():
    data = graph_data([0, 1, 2, 3, 4])
    view = ComparisonPresentation(
        mode="difference", difference_scale=2, control_color="#123abc", target_color="#aa0000"
    )
    svg = ET.fromstring(figure_svg(data, '<Result & "name">', presentation=view))
    ns = {"s": "http://www.w3.org/2000/svg"}
    assert svg.attrib["viewBox"] == "0 0 800 465"
    assert svg.find("s:title", ns).text == '<Result & "name">'
    metadata = json.loads(svg.find("s:metadata", ns).text)
    assert metadata["presentation"] == view.model_dump()
    zero = next(line for line in svg.findall("s:line", ns) if line.attrib["y1"] == "215")
    assert zero.attrib["y2"] == "215"
    curve = svg.find("s:g/s:polyline", ns)
    points = np.array(
        [[float(v) for v in pair.split(",")] for pair in curve.attrib["points"].split()]
    )
    assert points[0, 1] > 215 and points[-1, 1] < 215
    assert svg.find("s:text[@x='70'][@y='35']", ns).attrib["fill"] == view.control_color
    assert svg.find("s:text[@x='335']", ns).attrib["fill"] == view.target_color


def test_control_characters_in_imported_names_produce_valid_publication_xml():
    data = graph_data([0, 1, 2, 3, 4])
    data["parameter"]["label"] = "Measured\x00\ud800 & channel"
    svg = ET.fromstring(figure_svg(data, "Result\x01\ud800 & name"))
    assert "Result�� & name" == svg.find("{*}title").text
    assert any("Measured�� & channel" in t.text for t in svg.findall("{*}text") if t.text)


@pytest.mark.parametrize(
    "settings",
    [
        {"smoothing": float("nan")},
        {"difference_scale": float("inf")},
        {"difference_scale": 0},
        {"smoothing": 17},
        {"control_color": 'red"/><script/>'},
        {"target_color": "#fff"},
        {"mode": "unknown"},
    ],
)
def test_portable_presentation_rejects_invalid_numeric_and_xml_inputs(settings):
    with pytest.raises(ValueError):
        figure_svg(graph_data([0, 1, 2, 3, 4]), "Valid result", presentation=settings)
