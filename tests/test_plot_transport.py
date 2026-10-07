"""Full plot response compatibility, independent counts and local request guards."""

import json

import numpy as np
import pytest
from cytoforge.models import Channel, Gate, Sample, Workspace
from cytoforge.plotting import plot_payload
from cytoforge.science import save_events
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

MODES = ["density", "scatter", "histogram", "cdf", "contour", "zebra", "pseudocolor", "3d"]


def acquisition(store, scenario):
    if scenario == "mixed":
        # Histograms/CDFs include event 5 despite its missing Y value. Missing
        # color on event 4 does not remove its complete XYZ coordinates.
        values = np.array(
            [
                [0, 0, 0, 0, 1],
                [1, 2, 3, 1, 2],
                [2, 1, 2, 2, 3],
                [3, 3, 1, 3, 4],
                [0, 2, 4, np.nan, 5],
                [2, np.nan, 2, 5, 6],
                [np.nan, 1, 1, 6, 7],
                [np.inf, 2, 2, 7, 8],
                [-np.inf, 2, 2, 8, 9],
            ]
        )
    elif scenario == "empty":
        values = np.empty((0, 5))
    elif scenario == "constant":
        values = np.full((3, 5), 0.5)
    elif scenario == "extreme":
        maximum = np.finfo(float).max
        values = np.array(
            [
                [-maximum, -maximum, -maximum, -maximum, -maximum],
                [0, 0, 0, 0, 0],
                [maximum, maximum, maximum, maximum, maximum],
            ]
        )
    else:
        raise AssertionError(scenario)
    sample = Sample(
        name="λ rare acquisition 🧪",
        channels=[Channel(name=name) for name in ["X", "Y", "Z", "C", "S"]],
        event_count=len(values),
    )
    gate = Gate(
        sample_id=sample.id,
        name="λ <review> Ω 🧪",
        kind="rectangle",
        x="X",
        y="Y",
        bounds=[0, 2, 0, 3],
    )
    doc = Workspace(name="Full transport truth", samples=[sample], gates=[gate])
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    # Read the authoritative snapshot just as the route does. Revalidation
    # normalizes numeric model defaults, including integer defaults to floats.
    return store.get(store.create(doc).id), values


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("scenario", ["mixed", "empty", "constant", "extreme"])
def test_complete_plot_bytes_match_previous_encoding_and_literal_counts(client, mode, scenario):
    store, engine = client.app.state.store, client.app.state.engine
    doc, values = acquisition(store, scenario)
    before = store.get(doc.id).model_dump_json()
    history_before = store.history(doc.id)
    one_dimensional = mode in {"histogram", "cdf"}
    bounds = [-1, 5] * (1 if one_dimensional else 3 if mode == "3d" else 2)
    if scenario == "extreme":
        bounds = [-np.finfo(float).max, np.finfo(float).max] * (len(bounds) // 2)
    options = {"smooth": False, "axis_extent": "full", "contour_spacing": "10"}
    view = {"z": "Z", "color_by": "C", "size_by": "S", "pan": [0.1, -0.2]}
    args = {
        "x": "X",
        "mode": mode,
        "bins": "32",
        "bounds": json.dumps(bounds),
        "backgate_id": doc.gates[0].id,
        "graph_options": json.dumps(options),
    }
    if not one_dimensional:
        args["y"] = "Y"
    if mode == "3d":
        args["three_d"] = json.dumps(view)
    expected = plot_payload(
        doc,
        engine,
        doc.samples[0].id,
        "X",
        None if one_dimensional else "Y",
        bins=32,
        bounds=bounds,
        mode=mode,
        backgate_id=doc.gates[0].id,
        graph_options=options,
        three_d=view if mode == "3d" else None,
    )
    # This is the previous framework conversion/encoding contract. Compare the
    # entire body, including Unicode, nulls, tuples, ordering and float text.
    previous_body = JSONResponse(jsonable_encoder(expected)).body
    response = client.get(f"/api/workspaces/{doc.id}/samples/{doc.samples[0].id}/plot", params=args)
    assert response.status_code == 200, response.text
    assert response.content == previous_body
    assert response.headers["content-type"] == "application/json"
    assert int(response.headers["content-length"]) == len(previous_body)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    actual = response.json()
    assert actual["count"] == len(values)
    assert (
        actual["finite_count"]
        == (
            {"mixed": 6 if one_dimensional else 5, "empty": 0, "constant": 3, "extreme": 3}[
                scenario
            ]
        )
    )
    assert (
        actual["backgate_count"] == {"mixed": 3, "empty": 0, "constant": 3, "extreme": 1}[scenario]
    )
    if mode != "3d" and not one_dimensional:
        assert actual["overlays"][0]["name"] == doc.gates[0].name
        assert doc.gates[0].name.encode() in response.content
    if one_dimensional:
        assert actual["y"] is None and actual["y_transform"] is None
    if mode == "3d":
        assert actual["three_d"]["pan"] == [0.1, -0.2]
        assert actual["axes"][0]["compensation_ref"] == "sample"
    if mode in {"contour", "zebra"}:
        assert actual["probability_denominator"] == actual["finite_count"]
        assert not actual["contours_truncated"]
    assert store.get(doc.id).model_dump_json() == before
    assert store.history(doc.id) == history_before


@pytest.mark.parametrize("mode", ["contour", "zebra"])
def test_fragmented_plot_transport_keeps_all_bins_regions_mass_and_declared_limit(client, mode):
    store, engine = client.app.state.store, client.app.state.engine
    rows, columns = np.indices((256, 256))
    occupied = (rows + columns) % 2 == 0
    values = np.column_stack([(columns[occupied] + 64.5) / 384, (rows[occupied] + 64.5) / 384])
    sample = Sample(
        name="Complete fragmented field",
        channels=[Channel(name="X"), Channel(name="Y")],
        event_count=len(values),
    )
    doc = Workspace(name="Drawing limits retain science", samples=[sample])
    sample.sha256 = save_events(store.data_path(doc.id, sample.id), values)
    doc = store.get(store.create(doc).id)
    options = {"smooth": False, "axis_extent": "full", "contour_spacing": "10"}
    expected = plot_payload(
        doc,
        engine,
        sample.id,
        "X",
        "Y",
        bins=384,
        bounds=[0, 1, 0, 1],
        mode=mode,
        graph_options=options,
    )
    response = client.get(
        f"/api/workspaces/{doc.id}/samples/{sample.id}/plot",
        params={
            "x": "X",
            "y": "Y",
            "mode": mode,
            "bins": 384,
            "bounds": "[0,1,0,1]",
            "graph_options": json.dumps(options),
        },
    )
    assert response.status_code == 200, response.text
    assert response.content == JSONResponse(jsonable_encoder(expected)).body
    actual = response.json()
    assert actual["count"] == actual["finite_count"] == actual["density_count"] == 32768
    assert actual["probability_denominator"] == 32768
    assert sum(actual["counts"]) == sum(actual["density_field"]) == 32768
    assert actual["contour_vertices"] == 150000 and actual["contours_truncated"] is True
    assert len(actual["contours"][0]["paths"]) == 30000
    assert all(
        level["estimated_probability"] == level["binned_event_probability"] == 1
        and level["tied_bins"] == 32768
        for level in actual["probability_levels"]
    )


def test_plot_request_guards_and_invalid_parameters_preserve_the_workspace(client):
    store = client.app.state.store
    doc, _ = acquisition(store, "mixed")
    before = store.get(doc.id).model_dump_json()
    path = f"/api/workspaces/{doc.id}/samples/{doc.samples[0].id}/plot"
    args = {"x": "X", "y": "Y"}
    response = client.get(path, params=args, headers={"X-CytoForge-Token": "wrong"})
    assert response.status_code == 401
    assert (
        client.get(path, params=args, headers={"Origin": "https://hostile.invalid"}).status_code
        == 403
    )
    assert client.get(path, params=args, headers={"Host": "hostile.invalid"}).status_code == 400
    for bad in [
        {"mode": "unknown"},
        {"bins": 385},
        {"bounds": "[0,1]"},
        {"bounds": "[0,0,0,1]"},
        {"bounds": "[0,NaN,0,1]"},
        {"graph_options": '{"unknown":true}'},
        {"x_transform": '{"kind":"unknown"}'},
    ]:
        response = client.get(path, params={**args, **bad})
        assert response.status_code == 422, (bad, response.text)
        assert isinstance(response.json()["detail"], (str, list))
    assert store.get(doc.id).model_dump_json() == before
