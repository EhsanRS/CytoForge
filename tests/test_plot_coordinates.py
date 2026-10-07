"""Independent native axis definitions across exploration, reports and navigation."""

import json

import numpy as np
import pytest
from cytoforge.models import (
    Channel,
    Compensation,
    Gate,
    GateDimension,
    PlotDefinition,
    PlotDimension,
    Sample,
    ThreeDView,
    Transform,
    Workspace,
)
from cytoforge.plot_navigation import NavigationRequest, plan_navigation
from cytoforge.plotting import plot_payload
from cytoforge.report_plots import source_payloads
from cytoforge.science import save_events
from cytoforge.three_dimensional import POINT_DTYPE, point_chunk, prepare


def experiment(client, third=False):
    store = client.app.state.store
    values = np.array(
        [[1.2, 4, 1], [2.2, 2, 2], [0, 0, 0], [-1, 2, 1], [3.2, 2, 3], [np.nan, 2, 1]]
    )
    sample = Sample(
        name="Distinct native definitions",
        event_count=len(values),
        channels=[Channel(name=n) for n in "XYZ"],
    )
    fixed = Compensation(
        name="Known diagonal", detectors=list("XYZ"), matrix=np.diag([2, 4, 8]).tolist()
    )
    dims = [
        GateDimension(channel="X", compensation_ref="uncompensated", minimum=1.1, maximum=1.3),
        GateDimension(
            channel="X",
            compensation_ref=fixed.id,
            transform=Transform(kind="asinh", cofactor=2),
            minimum=0.25,
            maximum=0.35,
        ),
    ]
    if third:
        dims.append(
            GateDimension(channel="X", compensation_ref="uncompensated", ratio_channels=("X", "Y"))
        )
    gate = Gate(sample_id=sample.id, name="Native axes", kind="hyperrectangle", dimensions=dims)
    doc = Workspace(name="Axis identity", samples=[sample], gates=[gate], compensations=[fixed])
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    doc = store.create(doc)
    return doc, doc.samples[0], doc.gates[0], values


@pytest.mark.parametrize("mode", ["scatter", "density", "contour", "zebra", "pseudocolor"])
def test_exploration_retains_ordered_compensation_transforms_and_overlay(client, mode):
    doc, sample, gate, values = experiment(client)
    before = doc.model_dump_json()
    response = client.get(
        f"/api/workspaces/{doc.id}/samples/{sample.id}/plot",
        params={
            "x": "X",
            "y": "X",
            "mode": mode,
            "coordinate_gate_id": gate.id,
            "bounds": "[-2,4,-1,1]",
            "bins": 32,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["x_transform"]["kind"] == "linear"
    assert data["y_transform"]["kind"] == "asinh"
    assert data["count"] == 6 and data["finite_count"] == 5
    if mode == "scatter":
        points = np.asarray(data["points"])
        for x in values[:5, 0]:
            assert np.any(np.all(np.isclose(points, [x, np.arcsinh(x / 4)]), axis=1))
    assert len(data["overlays"]) == 1
    np.testing.assert_allclose(
        data["overlays"][0]["vertices"], [[1.1, 0.25], [1.3, 0.25], [1.3, 0.35], [1.1, 0.35]]
    )
    assert client.app.state.engine.mask(doc, sample, gate.id).sum() == 1
    assert client.app.state.store.get(doc.id).model_dump_json() == before


@pytest.mark.parametrize("mode", ["histogram", "cdf"])
def test_one_dimensional_native_view_uses_its_first_axis(client, mode):
    doc, sample, gate, _ = experiment(client)
    data = plot_payload(
        doc, client.app.state.engine, sample.id, "X", coordinate_gate_id=gate.id, mode=mode
    )
    assert data["x_transform"]["kind"] == "linear"
    assert data["finite_count"] == 5 and data["count"] == 6


def test_report_prototype_keeps_separate_native_axis_scales(client):
    doc, sample, gate, _ = experiment(client)
    definition = PlotDefinition(
        sample_id=sample.id,
        x="X",
        y="X",
        mode="scatter",
        coordinate_gate_id=gate.id,
        bounds=[-2, 4, -1, 1],
    )
    layers = [
        dict(
            sample_id=sample.id,
            source_sample_id=sample.id,
            gate_id=None,
            coordinate_gate_id=gate.id,
        )
    ]
    payloads, _ = source_payloads(doc, client.app.state.engine, definition, layers)
    assert payloads[0]["x_transform"]["kind"] == "linear"
    assert payloads[0]["y_transform"]["kind"] == "asinh"
    assert np.any(np.all(np.isclose(payloads[0]["points"], [1.2, np.arcsinh(0.3)]), axis=1))


def test_xyz_stream_preserves_raw_fixed_and_ratio_definitions(client):
    doc, sample, gate, values = experiment(client, third=True)
    prepared = prepare(
        doc,
        client.app.state.engine,
        sample.id,
        "X",
        "X",
        {"z": "X", "all_events": True},
        coordinate_gate_id=gate.id,
        bounds=[-2, 4, -1, 1, -1, 2],
    )
    metadata = prepared[0]
    assert [d["compensation_ref"] for d in metadata["axes"]] == [
        "uncompensated",
        doc.compensations[0].id,
        "uncompensated",
    ]
    assert metadata["axes"][2]["ratio_channels"] == ("X", "Y")
    rows = point_chunk(prepared)
    assert rows.dtype == POINT_DTYPE
    assert rows["event_id"].tolist() == [0, 1, 3, 4]
    for row in rows:
        event = values[int(row["event_id"])]
        expected = [
            (event[0] + 2) / 6,
            (np.arcsinh(event[0] / 4) + 1) / 2,
            (event[0] / event[1] + 1) / 3,
        ]
        np.testing.assert_allclose(row["position"], expected, rtol=2e-6, atol=1e-7)


def test_ambiguous_scalar_does_not_silently_take_another_native_axis(client):
    doc, sample, gate, _ = experiment(client)
    with pytest.raises(ValueError, match="multiple coordinate definitions"):
        prepare(
            doc,
            client.app.state.engine,
            sample.id,
            "X",
            "X",
            {"z": "Z", "color_by": "X"},
            coordinate_gate_id=gate.id,
        )


def test_navigation_checks_the_second_native_definition_and_keeps_scales(client):
    doc, sample, gate, _ = experiment(client)
    target = sample.model_copy(deep=True, update={"id": "2" * 32, "name": "Other sample"})
    equivalent = gate.model_copy(deep=True, update={"id": "3" * 32, "sample_id": target.id})
    doc.samples.append(target)
    doc.gates.append(equivalent)
    state = dict(
        workspaceId=doc.id,
        sampleId=sample.id,
        gateId=gate.id,
        coordinateGateId=gate.id,
        x="X",
        y="X",
        mode="scatter",
    )
    request = NavigationRequest.model_validate(
        dict(
            revision=doc.revision,
            direction="select",
            initiator="main",
            targetSampleId=target.id,
            views=[dict(id="main", state=state)],
        )
    )
    planned = plan_navigation(doc, request)
    assert planned["available"]
    assert planned["views"][0]["state"]["xTransform"]["kind"] == "linear"
    assert planned["views"][0]["state"]["yTransform"]["kind"] == "asinh"
    equivalent.dimensions[1].compensation_ref = "uncompensated"
    rejected = plan_navigation(doc, request)
    assert not rejected["available"] and "compensation" in rejected["reason"]


def test_repeated_same_basis_with_different_scales_projects_each_boundary(client):
    doc, sample, gate, _ = experiment(client)
    gate.dimensions[1].compensation_ref = "uncompensated"
    gate.dimensions[1].minimum, gate.dimensions[1].maximum = 0.5, 0.7
    data = plot_payload(
        doc,
        client.app.state.engine,
        sample.id,
        "X",
        "X",
        coordinate_gate_id=gate.id,
        mode="scatter",
    )
    assert np.any(np.all(np.isclose(data["points"], [1.2, np.arcsinh(0.6)]), axis=1))
    assert len(data["overlays"]) == 1
    np.testing.assert_allclose(
        data["overlays"][0]["vertices"], [[1.1, 0.5], [1.3, 0.5], [1.3, 0.7], [1.1, 0.7]]
    )


def test_navigation_compares_fixed_matrix_definitions_not_names_or_ids(client):
    doc, sample, gate, _ = experiment(client)
    alias = doc.compensations[0].model_copy(
        deep=True, update={"id": "4" * 32, "name": "Equivalent renamed matrix"}
    )
    target = sample.model_copy(deep=True, update={"id": "2" * 32, "name": "Other sample"})
    copied = gate.model_copy(deep=True, update={"id": "3" * 32, "sample_id": target.id})
    copied.dimensions[1].compensation_ref = alias.id
    doc.compensations.append(alias)
    doc.samples.append(target)
    doc.gates.append(copied)
    state = dict(
        workspaceId=doc.id,
        sampleId=sample.id,
        gateId=gate.id,
        coordinateGateId=gate.id,
        x="X",
        y="X",
        mode="scatter",
    )
    request = NavigationRequest.model_validate(
        dict(
            revision=doc.revision,
            direction="select",
            initiator="main",
            targetSampleId=target.id,
            views=[dict(id="main", state=state)],
        )
    )
    assert plan_navigation(doc, request)["available"]
    alias.matrix[0][0] = 3
    changed = plan_navigation(doc, request)
    assert not changed["available"] and "compensation" in changed["reason"]


@pytest.mark.parametrize("mode", ["scatter", "density", "contour", "zebra", "pseudocolor"])
def test_explicit_axes_override_gate_basis_without_changing_populations(client, mode):
    doc, sample, gate, values = experiment(client)
    before = doc.model_dump_json()
    xdim = PlotDimension(channel="X", compensation_ref="uncompensated")
    ydim = PlotDimension(channel="X", compensation_ref=doc.compensations[0].id)
    response = client.get(
        f"/api/workspaces/{doc.id}/samples/{sample.id}/plot",
        params=dict(
            x="X",
            y="X",
            mode=mode,
            coordinate_gate_id=gate.id,
            x_dimension=xdim.model_dump_json(),
            y_dimension=ydim.model_dump_json(),
            bins=32,
        ),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert [d["compensation_ref"] for d in payload["axes"]] == [
        "uncompensated",
        ydim.compensation_ref,
    ]
    assert payload["finite_count"] == 5
    if mode == "scatter":
        np.testing.assert_allclose(
            sorted(payload["points"]), sorted([[v, v / 2] for v in values[:5, 0]])
        )
    assert client.app.state.engine.mask(doc, sample, gate.id).sum() == 1
    assert client.app.state.store.get(doc.id).model_dump_json() == before


@pytest.mark.parametrize("mode", ["histogram", "cdf"])
def test_explicit_ratio_without_a_coordinate_gate(client, mode):
    doc, sample, _, _ = experiment(client)
    dim = PlotDimension(
        channel="X / Y", ratio_channels=("X", "Y"), compensation_ref="uncompensated"
    )
    payload = plot_payload(
        doc,
        client.app.state.engine,
        sample.id,
        dim.channel,
        mode=mode,
        x_dimension=dim,
        bins=16,
        bounds=[-1, 2],
    )
    assert payload["count"] == 6 and payload["finite_count"] == 4
    assert payload["axes"][0]["ratio_channels"] == ("X", "Y")
    assert sum(payload["counts"]) == 4


@pytest.mark.parametrize(
    "changes",
    [
        {"channel": "missing"},
        {"compensation_ref": "f" * 32},
        {"ratio_channels": ("X", "missing")},
        {"minimum": 1},
        {"maximum": 2},
        {"ratio_a": float("inf")},
        {"ratio_bound_min": 2, "ratio_bound_max": 1},
        {"transform": {"kind": "asinh", "cofactor": 0}},
        {"compensation_ref": "file:///secret"},
        {"extra": True},
    ],
)
def test_coordinate_validation_rejects_invalid_definitions_without_writes(client, changes):
    doc, sample, _, _ = experiment(client)
    before = doc.model_dump_json()
    dimension = {**PlotDimension(channel="X").model_dump(), **changes}
    # JSON cannot carry infinity. A JSON number/string that parses nonfinite must still be rejected.
    if dimension.get("ratio_a") == float("inf"):
        dimension["ratio_a"] = "Infinity"
    response = client.post(
        f"/api/workspaces/{doc.id}/samples/{sample.id}/coordinates/validate",
        json=dict(revision=doc.revision, dimension=dimension),
    )
    assert response.status_code in {400, 422}, response.text
    assert client.app.state.store.get(doc.id).model_dump_json() == before


def test_coordinate_validation_is_revision_bound_and_checks_detectors(client):
    doc, sample, _, _ = experiment(client)
    dimension = PlotDimension(channel="X")
    route = f"/api/workspaces/{doc.id}/samples/{sample.id}/coordinates/validate"
    assert (
        client.post(
            route, json=dict(revision=doc.revision, dimension=dimension.model_dump())
        ).status_code
        == 200
    )
    assert (
        client.post(
            route, json=dict(revision=doc.revision + 1, dimension=dimension.model_dump())
        ).status_code
        == 409
    )
    incompatible = Compensation(name="Foreign detector", detectors=["Q"], matrix=[[1]])
    doc = client.app.state.store.mutate(
        doc.id, "Synthetic matrix", lambda d: d.compensations.append(incompatible), doc.revision
    )
    dimension.compensation_ref = incompatible.id
    response = client.post(
        route, json=dict(revision=doc.revision, dimension=dimension.model_dump())
    )
    assert response.status_code in {400, 422} and "detectors" in response.text


def test_explicit_axes_cannot_replace_native_shape_editor_basis(client):
    doc, sample, gate, _ = experiment(client)
    with pytest.raises(ValueError, match="Native gate editing cannot override"):
        plot_payload(
            doc,
            client.app.state.engine,
            sample.id,
            "X",
            "X",
            coordinate_gate_id=gate.id,
            native_gate_coordinates=True,
            x_dimension=PlotDimension(channel="X"),
        )


def test_explicit_3d_axes_and_scalars_stream_independent_definitions(client):
    doc, sample, gate, values = experiment(client)
    raw = PlotDimension(channel="X", compensation_ref="uncompensated")
    fixed = PlotDimension(channel="X", compensation_ref=doc.compensations[0].id)
    ratio = PlotDimension(channel="R", ratio_channels=("X", "Y"), compensation_ref="uncompensated")
    view = ThreeDView(
        z="R",
        z_dimension=ratio,
        color_by="X",
        color_dimension=fixed,
        size_by="X",
        size_dimension=raw,
    )
    params = dict(
        x="X",
        y="X",
        mode="3d",
        coordinate_gate_id=gate.id,
        x_dimension=raw.model_dump_json(),
        y_dimension=fixed.model_dump_json(),
        three_d=view.model_dump_json(),
        bounds="[-2,4,-1,2,-1,2]",
    )
    base = f"/api/workspaces/{doc.id}/samples/{sample.id}"
    response = client.get(base + "/plot", params=params)
    assert response.status_code == 200, response.text
    meta = response.json()
    assert meta["finite_count"] == 4
    assert [d["compensation_ref"] for d in meta["scalar_dimensions"]] == [
        fixed.compensation_ref,
        "uncompensated",
    ]
    stream = client.get(
        base + "/plot3d/points",
        params={**params, "revision": doc.revision, "data_key": meta["data_key"]},
    )
    assert stream.status_code == 200, stream.text if stream.status_code != 200 else ""
    rows = np.frombuffer(stream.content, dtype=POINT_DTYPE)
    assert rows["event_id"].tolist() == [0, 1, 3, 4]
    for row in rows:
        x, y, _ = values[int(row["event_id"])]
        np.testing.assert_allclose(
            row["position"], [(x + 2) / 6, (x / 2 + 1) / 3, (x / y + 1) / 3], rtol=2e-6
        )
    changed = {**params, "y_dimension": raw.model_dump_json()}
    assert (
        client.get(
            base + "/plot3d/points",
            params={**changed, "revision": doc.revision, "data_key": meta["data_key"]},
        ).status_code
        == 409
    )
    # A legacy raw default applies only to axes without explicit choices.
    forced = prepare(
        doc,
        client.app.state.engine,
        sample.id,
        "X",
        "X",
        {**view.model_dump(), "compensation": "uncompensated"},
        x_dimension=raw,
        y_dimension=fixed,
    )[0]
    assert forced["axes"][1]["compensation_ref"] == fixed.compensation_ref


def test_report_ratio_and_fixed_matrix_provenance_without_coordinate_gate(client):
    from cytoforge import reports
    from cytoforge.models import LayoutDefinition, ReportElement
    from cytoforge.report_plots import figure

    doc, sample, _, _ = experiment(client)
    fixed = PlotDimension(channel="X", compensation_ref=doc.compensations[0].id)
    ratio = PlotDimension(channel="R", ratio_channels=("X", "Y"), compensation_ref="uncompensated")
    plot = PlotDefinition(
        sample_id=sample.id, x="X", y="R", mode="scatter", x_dimension=fixed, y_dimension=ratio
    )
    restored = PlotDefinition.model_validate_json(plot.model_dump_json())
    assert restored == plot
    layout = LayoutDefinition(
        name="Independent coordinates", elements=[ReportElement(kind="plot", plot=plot)]
    )
    plan = reports.plan(doc, layout, client.app.state.engine)
    assert plan["exportable"], plan
    layers = [
        dict(
            sample_id=sample.id,
            source_sample_id=sample.id,
            gate_id=None,
            coordinate_gate_id=None,
            label="Independent",
            color="#087e8b",
            locked_control=False,
        )
    ]
    svg, manifest = figure(doc, client.app.state.engine, plot, layers, 90, 85)
    assert "<svg" in svg
    layer = manifest["layers"][0]
    assert layer["finite_count"] == 4
    assert layer["axes"][1]["ratio_channels"] == ("X", "Y")
    assert {"X", "Y"} <= layer["source"]["parameters"].keys()
    assert fixed.compensation_ref in layer["source"]["compensations"]


def test_legacy_plot_and_3d_serialization_omit_new_fields(client):
    doc, sample, _, _ = experiment(client)
    legacy = PlotDefinition(sample_id=sample.id, x="X", y="Y", three_d=ThreeDView(z="Z"))
    serialized = json.loads(legacy.model_dump_json())
    assert not {"x_dimension", "y_dimension"} & serialized.keys()
    assert not {"z_dimension", "color_dimension", "size_dimension"} & serialized["three_d"].keys()


def test_navigation_retains_explicit_ratios_and_fixed_matrices(client):
    doc, sample, _, _ = experiment(client)
    target = sample.model_copy(deep=True, update={"id": "2" * 32, "name": "Target"})
    doc.samples.append(target)
    ratio = PlotDimension(
        channel="R", ratio_channels=("X", "Y"), compensation_ref=doc.compensations[0].id
    )
    state = dict(
        workspaceId=doc.id,
        sampleId=sample.id,
        gateId=None,
        x="R",
        y="X",
        mode="scatter",
        xDimension=ratio.model_dump(),
        yDimension=PlotDimension(channel="X", compensation_ref="uncompensated").model_dump(),
    )
    request = NavigationRequest.model_validate(
        dict(
            revision=doc.revision,
            direction="select",
            initiator="main",
            targetSampleId=target.id,
            views=[dict(id="main", state=state)],
        )
    )
    result = plan_navigation(doc, request)
    assert result["available"], result
    assert result["views"][0]["state"]["xDimension"] == json.loads(ratio.model_dump_json())
    target.channels = [c for c in target.channels if c.name != "Y"]
    rejected = plan_navigation(doc, request)
    assert not rejected["available"] and "input channels" in rejected["reason"]
