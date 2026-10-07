import copy

import pytest
from cytoforge.models import (
    Channel,
    DerivedParameter,
    Gate,
    GateDimension,
    Group,
    Sample,
    Transform,
    Workspace,
)
from cytoforge.plot_navigation import NavigationRequest, plan_navigation
from cytoforge.store import ConflictError
from pydantic import ValidationError


@pytest.fixture
def experiment():
    samples = [
        Sample(name=n, event_count=100, channels=[Channel(name=c) for c in "XYZ"])
        for n in ("First", "Missing", "Third", "Fourth")
    ]
    gates = []
    for i in (0, 2, 3):
        root = Gate(
            sample_id=samples[i].id,
            name="Cells",
            kind="rectangle",
            x="X",
            y="Y",
            bounds=[0, 5, 0, 5],
        )
        child = Gate(
            sample_id=samples[i].id,
            parent_id=root.id,
            name="Positive",
            kind="range",
            x="Z",
            bounds=[1, 3],
        )
        gates.extend([root, child])
    samples[0].channels[0].transform = Transform(kind="asinh", cofactor=50)
    samples[2].channels[0].transform = Transform(kind="asinh", cofactor=500)
    doc = Workspace(name="Navigation", samples=samples, gates=gates)
    return doc


def state(doc, sample_index=0, child=True, **overrides):
    sample = doc.samples[sample_index]
    gate = next(
        (
            g
            for g in doc.gates
            if g.sample_id == sample.id and g.name == ("Positive" if child else "Cells")
        ),
        None,
    )
    return {
        "workspaceId": doc.id,
        "sampleId": sample.id,
        "gateId": gate.id if gate else None,
        "x": "X",
        "y": "Y",
        "mode": "contour",
        "bounds": [-1, 1, 0, 10],
        "graphOptions": {"palette": "viridis", "smooth": False},
        **overrides,
    }


def request(doc, direction="next", views=None, **extra):
    return NavigationRequest.model_validate(
        {
            "revision": doc.revision,
            "initiator": "main",
            "direction": direction,
            "views": views or [{"id": "main", "state": state(doc)}],
            **extra,
        }
    )


def test_next_skips_missing_path_keeps_every_display_setting(experiment):
    doc = experiment
    before = doc.model_dump_json()
    result = plan_navigation(doc, request(doc))
    value = result["views"][0]["state"]
    assert result["available"] and value["sampleId"] == doc.samples[2].id
    assert value["gateId"] == doc.gates[3].id
    assert value["bounds"] == [-1, 1, 0, 10]
    assert value["mode"] == "contour" and value["graphOptions"]["palette"] == "viridis"
    assert value["xTransform"]["cofactor"] == 50
    assert result["skipped"][0]["sample_id"] == doc.samples[1].id
    assert "Missing population: Cells / Positive" in result["skipped"][0]["reason"]
    assert doc.model_dump_json() == before


def test_matching_requires_unique_full_ancestry(experiment):
    doc = experiment
    target = doc.samples[1]
    root = Gate(
        sample_id=target.id, name="Other", kind="rectangle", x="X", y="Y", bounds=[0, 1, 0, 1]
    )
    doc.gates.extend(
        [
            root,
            Gate(
                sample_id=target.id,
                parent_id=root.id,
                name="Positive",
                kind="range",
                x="Z",
                bounds=[1, 3],
            ),
        ]
    )
    assert plan_navigation(doc, request(doc))["views"][0]["state"]["sampleId"] == doc.samples[2].id
    doc.gates.append(doc.gates[3].model_copy(update={"id": "d" * 32}))
    result = plan_navigation(doc, request(doc))
    assert result["views"][0]["state"]["sampleId"] == doc.samples[3].id
    assert "Ambiguous" in result["skipped"][1]["reason"]


def test_sync_requires_one_common_compatible_target(experiment):
    doc = experiment
    group = Group(name="Second view group", sample_ids=[doc.samples[0].id, doc.samples[3].id])
    doc.groups.append(group)
    result = plan_navigation(
        doc,
        request(
            doc,
            views=[
                {"id": "main", "state": state(doc)},
                {
                    "id": "a" * 32,
                    "state": state(
                        doc,
                        child=False,
                        groupId=group.id,
                        mode="3d",
                        bounds=[0, 5, 0, 5, 0, 5],
                        threeD={
                            "z": "Z",
                            "yaw": 1.2,
                            "pan": [0.3, -0.4],
                            "color_by": "X",
                            "size_by": "Y",
                        },
                    ),
                },
            ],
        ),
    )
    values = [v["state"] for v in result["views"]]
    assert {v["sampleId"] for v in values} == {doc.samples[3].id}
    assert values[1]["threeD"]["yaw"] == 1.2 and values[1]["threeD"]["pan"] == [0.3, -0.4]
    assert values[1]["threeD"]["color_transform"]["cofactor"] == 50
    assert values[1]["groupId"] == group.id
    assert values[0]["gateId"] != values[1]["gateId"]
    assert len(result["skipped"]) == 2


@pytest.mark.parametrize("direction", ["previous", "next", "select"])
def test_no_wrap_or_silent_all_events_replacement(experiment, direction):
    doc = experiment
    current = 0 if direction == "previous" else 3 if direction == "next" else 0
    result = plan_navigation(
        doc,
        request(
            doc,
            direction,
            views=[{"id": "main", "state": state(doc, current)}],
            **({"targetSampleId": doc.samples[1].id} if direction == "select" else {}),
        ),
    )
    assert not result["available"] and not result["views"]


@pytest.mark.parametrize("ref", ["gateId", "coordinateGateId", "backgateId", "groupId"])
def test_removed_references_block_without_fallback(experiment, ref):
    doc = experiment
    with pytest.raises(ValueError, match="unavailable"):
        plan_navigation(
            doc, request(doc, views=[{"id": "main", "state": state(doc, **{ref: "f" * 32})}])
        )


def test_group_and_search_selection_never_steps_outside_cohort(experiment):
    doc = experiment
    doc.samples[0].tags = {"set": "match"}
    doc.samples[3].tags = {"set": "match"}
    result = plan_navigation(
        doc, request(doc, views=[{"id": "main", "state": state(doc, sampleFilter="MATCH")}])
    )
    assert result["views"][0]["state"]["sampleId"] == doc.samples[3].id
    with pytest.raises(ValueError, match="outside"):
        plan_navigation(
            doc, request(doc, views=[{"id": "main", "state": state(doc, sampleFilter="Third")}])
        )
    empty = Group(name="Empty")
    doc.groups.append(empty)
    with pytest.raises(ValueError, match="outside"):
        plan_navigation(
            doc, request(doc, views=[{"id": "main", "state": state(doc, groupId=empty.id)}])
        )


def test_coordinate_and_backgate_paths_are_mapped_and_scales_retained(experiment):
    doc = experiment
    value = state(
        doc,
        coordinateGateId=doc.gates[0].id,
        backgateId=doc.gates[1].id,
        xTransform={"kind": "asinh", "cofactor": 13},
    )
    result = plan_navigation(doc, request(doc, views=[{"id": "main", "state": value}]))["views"][0][
        "state"
    ]
    assert result["coordinateGateId"] == doc.gates[2].id
    assert result["backgateId"] == doc.gates[3].id
    assert result["xTransform"]["cofactor"] == 13


def test_ratio_aliases_must_keep_their_definition(experiment):
    doc = experiment
    for g in (doc.gates[0], doc.gates[2], doc.gates[4]):
        g.dimensions = [GateDimension(channel="Ratio", ratio_channels=("X", "Y"))]
    doc.gates[2].dimensions[0].ratio_a = 2
    value = state(doc, x="Ratio", coordinateGateId=doc.gates[0].id)
    result = plan_navigation(doc, request(doc, views=[{"id": "main", "state": value}]))
    assert result["views"][0]["state"]["sampleId"] == doc.samples[3].id
    assert "Ratio definition differs" in result["skipped"][1]["reason"]


def test_parent_shows_defining_geometry_and_does_not_mutate(experiment):
    doc = experiment
    result = plan_navigation(doc, request(doc, "parent"))["views"][0]["state"]
    assert result["gateId"] == doc.gates[0].id and result["x"] == "Z"
    assert result["y"] is None and result["mode"] == "histogram" and result["bounds"] is None
    result = plan_navigation(
        doc, request(doc, "parent", views=[{"id": "main", "state": state(doc, child=False)}])
    )
    assert result["views"][0]["state"]["gateId"] is None
    assert not plan_navigation(
        doc, request(doc, "parent", views=[{"id": "main", "state": state(doc, gateId=None)}])
    )["available"]


def test_revision_workspace_and_parameter_availability(experiment):
    doc = experiment
    with pytest.raises(ConflictError):
        plan_navigation(doc, request(doc, revision=1))
    with pytest.raises(ValueError, match="workspace"):
        plan_navigation(
            doc, request(doc, views=[{"id": "main", "state": state(doc, workspaceId="f" * 32)}])
        )
    with pytest.raises(ValueError, match="Parameter"):
        plan_navigation(doc, request(doc, views=[{"id": "main", "state": state(doc, x="Removed")}]))


def test_parent_navigation_does_not_depend_on_sample_cohort(experiment):
    doc = experiment
    result = plan_navigation(
        doc,
        request(
            doc,
            "parent",
            views=[{"id": "main", "state": state(doc, groupId="f" * 32, sampleFilter="No match")}],
        ),
    )
    assert result["available"]
    assert result["views"][0]["state"]["groupId"] == "f" * 32


def test_parent_clears_zoom_when_defining_transform_changes(experiment):
    doc = experiment
    result = plan_navigation(
        doc, request(doc, "parent", views=[{"id": "main", "state": state(doc, child=False)}])
    )["views"][0]["state"]
    assert result["x"] == "X" and result["y"] == "Y" and result["mode"] == "contour"
    assert result["bounds"] is None and result["xTransform"]["kind"] == "linear"


def test_transitive_derived_parameter_definitions_must_match(experiment):
    doc = experiment
    for i, sample in enumerate(doc.samples):
        sample.channels.extend([Channel(name="A"), Channel(name="D")])
        sample.derived_parameters = [
            DerivedParameter(name="A", expression=f'ch("X") + {2 if i == 2 else 1}'),
            DerivedParameter(name="D", expression='ch("A") * 2'),
        ]
    result = plan_navigation(doc, request(doc, views=[{"id": "main", "state": state(doc, x="D")}]))
    assert result["views"][0]["state"]["sampleId"] == doc.samples[3].id
    assert "Parameter definition differs: D" in result["skipped"][1]["reason"]


def test_incompatible_coordinate_compensation_is_not_silently_substituted(experiment):
    doc = experiment
    for gate in (doc.gates[0], doc.gates[2], doc.gates[4]):
        gate.dimensions = [GateDimension(channel="X"), GateDimension(channel="Y")]
    doc.gates[2].dimensions[0].compensation_ref = "uncompensated"
    result = plan_navigation(
        doc,
        request(doc, views=[{"id": "main", "state": state(doc, coordinateGateId=doc.gates[0].id)}]),
    )
    assert result["views"][0]["state"]["sampleId"] == doc.samples[3].id
    assert "Coordinate compensation differs" in result["skipped"][1]["reason"]


def test_removed_target_and_source_have_explicit_diagnostics(experiment):
    doc = experiment
    result = plan_navigation(doc, request(doc, "select", targetSampleId="f" * 32))
    assert not result["available"] and "unavailable" in result["reason"]
    with pytest.raises(ValueError, match="source sample is unavailable"):
        plan_navigation(
            doc, request(doc, views=[{"id": "main", "state": state(doc, sampleId="f" * 32)}])
        )


def test_request_rejects_foreign_duplicate_and_unbounded_views(experiment):
    doc = experiment
    with pytest.raises(ValidationError, match="source sample"):
        request(
            doc,
            views=[{"id": "main", "state": state(doc)}, {"id": "a" * 32, "state": state(doc, 2)}],
        )
    with pytest.raises(ValidationError, match="unique"):
        request(doc, views=[{"id": "main", "state": state(doc)}] * 2)
    views = [{"id": "main", "state": state(doc)}] + [
        {"id": f"{i:032x}", "state": state(doc)} for i in range(32)
    ]
    assert len(plan_navigation(doc, request(doc, views=views))["views"]) == 33
    with pytest.raises(ValidationError):
        request(doc, views=views + [{"id": "f" * 32, "state": state(doc)}])


@pytest.mark.parametrize(
    "changes",
    [
        {"bounds": [0, 1, 2]},
        {"xTransform": {"cofactor": 0}},
        {"threeD": {"z": "Z", "yaw": 99}},
        {"sampleFilter": "x" * 257},
        {"unknown": True},
    ],
)
def test_rejects_malformed_view(experiment, changes):
    with pytest.raises(ValidationError):
        request(experiment, views=[{"id": "main", "state": state(experiment, **changes)}])


def test_plan_endpoint_auth_revision_and_history_are_read_only(client):
    doc = client.post("/api/workspaces", json={"name": "Private navigation"}).json()
    import io

    doc = client.post(
        f"/api/workspaces/{doc['id']}/import?revision=0",
        files=[
            ("files", ("one.csv", io.BytesIO(b"X,Y\n1,2\n"), "text/csv")),
            ("files", ("two.csv", io.BytesIO(b"X,Y\n2,3\n"), "text/csv")),
        ],
    ).json()["workspace"]
    route = f"/api/workspaces/{doc['id']}/plot-navigation/plan"
    value = {
        "revision": doc["revision"],
        "initiator": "main",
        "direction": "next",
        "views": [
            {
                "id": "main",
                "state": {
                    "workspaceId": doc["id"],
                    "sampleId": doc["samples"][0]["id"],
                    "gateId": None,
                    "x": "X",
                    "y": "Y",
                    "mode": "scatter",
                },
            }
        ],
    }
    history = client.get(f"/api/workspaces/{doc['id']}/history").json()
    assert (
        client.post(route, json=value).json()["views"][0]["state"]["sampleId"]
        == doc["samples"][1]["id"]
    )
    assert client.get(f"/api/workspaces/{doc['id']}").json() == doc
    assert client.get(f"/api/workspaces/{doc['id']}/history").json() == history
    population_request = {
        **value,
        "direction": "population",
        "targetGateId": None,
        "rememberedViews": [
            {**value["views"][0]["state"], "mode": "cdf", "y": None, "bounds": [-3, 7]}
        ],
    }
    restored = client.post(route, json=population_request).json()
    assert restored["restored_view"] and restored["views"][0]["state"]["bounds"] == [-3, 7]
    assert client.get(f"/api/workspaces/{doc['id']}").json() == doc
    assert client.get(f"/api/workspaces/{doc['id']}/history").json() == history
    assert client.post(route, json=value, headers={"X-CytoForge-Token": "wrong"}).status_code == 401
    value["revision"] += 1
    assert client.post(route, json=value).status_code == 409
    malformed = copy.deepcopy(value)
    malformed["views"][0]["state"]["bounds"] = [1, 0, 1, 0]
    assert client.post(route, json=malformed).status_code == 422


@pytest.mark.parametrize("direction", ["population", "parent", "child"])
def test_population_navigation_restores_the_exact_view_without_scientific_edits(
    experiment, direction
):
    doc = experiment
    root, positive = doc.gates[:2]
    starting = state(doc, gateId=root.id if direction == "child" else positive.id)
    target = positive.id if direction == "child" else root.id
    saved = state(
        doc,
        gateId=target,
        x="Z",
        y="X",
        mode="3d",
        bins=72,
        bounds=[-1, 101, -1, 8, -2, 9],
        coordinateGateId=root.id,
        backgateId=positive.id,
        xTransform={"kind": "asinh", "cofactor": 17},
        threeD={
            "z": "Y",
            "yaw": 1.1,
            "pitch": -0.4,
            "zoom": 1.8,
            "pan": [0.3, -0.6],
            "color_by": "X",
            "all_events": False,
        },
        sampleFilter="Saved filter",
        groupId="e" * 32,
    )
    before = doc.model_dump_json()
    result = plan_navigation(
        doc,
        request(
            doc,
            direction,
            views=[{"id": "main", "state": starting}],
            rememberedViews=[saved],
            **({"targetGateId": target} if direction == "population" else {}),
        ),
    )
    restored = result["views"][0]["state"]
    assert result["restored_view"] and restored["gateId"] == target
    for key in ("x", "y", "mode", "bins", "bounds", "coordinateGateId", "backgateId"):
        assert restored[key] == saved[key]
    assert restored["xTransform"]["cofactor"] == 17
    assert restored["threeD"]["pan"] == [0.3, -0.6]
    assert restored["threeD"]["all_events"] is False
    assert restored["sampleFilter"] == starting.get("sampleFilter", "")
    assert restored["groupId"] == starting.get("groupId")
    assert doc.model_dump_json() == before


def test_population_selection_recovers_unavailable_axes_and_optional_references(experiment):
    doc = experiment
    result = plan_navigation(
        doc,
        request(
            doc,
            "population",
            targetGateId=None,
            views=[
                {
                    "id": "main",
                    "state": state(
                        doc,
                        gateId="f" * 32,
                        x="Removed",
                        coordinateGateId="e" * 32,
                        backgateId="d" * 32,
                        groupId="c" * 32,
                    ),
                }
            ],
        ),
    )
    value = result["views"][0]["state"]
    assert value["gateId"] is None and value["x"] == "X" and value["y"] == "Y"
    assert value["coordinateGateId"] is None and value["backgateId"] is None
    assert value["bounds"] is None and value["groupId"] == "c" * 32


def test_missing_remembered_inputs_stay_explicit_until_reset(experiment):
    doc = experiment
    saved = state(doc, child=False, x="Removed", coordinateGateId="e" * 32, backgateId="d" * 32)
    result = plan_navigation(doc, request(doc, "parent", rememberedViews=[saved]))
    value = result["views"][0]["state"]
    assert value["x"] == "Removed" and value["coordinateGateId"] == "e" * 32
    assert value["backgateId"] == "d" * 32 and value["bounds"] == saved["bounds"]
    reset = plan_navigation(
        doc,
        request(
            doc, "reset_population", views=[{"id": "main", "state": value}], rememberedViews=[saved]
        ),
    )["views"][0]["state"]
    assert reset["x"] == "Z" and reset["y"] is None and reset["mode"] == "histogram"
    assert reset["coordinateGateId"] is None and reset["backgateId"] is None
    assert reset["bounds"] is None


@pytest.mark.parametrize("field", ["coordinateGateId", "backgateId"])
def test_existing_foreign_remembered_references_are_rejected(experiment, field):
    doc = experiment
    saved = state(doc, child=False, **{field: doc.gates[2].id})
    with pytest.raises(ValueError, match="another sample"):
        plan_navigation(doc, request(doc, "parent", rememberedViews=[saved]))


def test_first_visit_uses_volume_coordinates_and_preserves_camera(experiment):
    doc = experiment
    volume = Gate(
        sample_id=doc.samples[0].id,
        parent_id=doc.gates[0].id,
        name="Volume",
        kind="hyperrectangle",
        dimensions=[
            GateDimension(
                channel=c, transform=Transform(kind="asinh", cofactor=25 + i), minimum=0, maximum=2
            )
            for i, c in enumerate("XYZ")
        ],
    )
    doc.gates.append(volume)
    value = plan_navigation(
        doc,
        request(
            doc,
            "population",
            targetGateId=volume.id,
            views=[{"id": "main", "state": state(doc, threeD={"z": "Y", "yaw": 1.4})}],
        ),
    )["views"][0]["state"]
    assert value["mode"] == "3d" and value["x"] == "X" and value["y"] == "Y"
    assert value["threeD"]["z"] == "Z" and value["threeD"]["yaw"] == 1.4
    assert value["threeD"]["z_transform"]["cofactor"] == 27
    assert value["coordinateGateId"] == volume.id


def test_child_and_siblings_follow_workspace_order_without_wrapping(experiment):
    doc = experiment
    root, positive = doc.gates[:2]
    sibling = positive.model_copy(update={"id": "a" * 32, "name": "Negative"})
    doc.gates.append(sibling)
    children = plan_navigation(
        doc, request(doc, "child", views=[{"id": "main", "state": state(doc, child=False)}])
    )
    assert children["views"][0]["state"]["gateId"] == positive.id
    next_sibling = plan_navigation(doc, request(doc, "sibling_next"))["views"][0]["state"]
    assert next_sibling["gateId"] == sibling.id
    assert not plan_navigation(doc, request(doc, "sibling_previous"))["available"]
    assert not plan_navigation(
        doc, request(doc, "sibling_next", views=[{"id": "main", "state": next_sibling}])
    )["available"]
    previous = plan_navigation(
        doc, request(doc, "sibling_previous", views=[{"id": "main", "state": next_sibling}])
    )
    assert previous["views"][0]["state"]["gateId"] == positive.id
    assert not plan_navigation(doc, request(doc, "child"))["available"]
    assert not plan_navigation(
        doc, request(doc, "sibling_next", views=[{"id": "main", "state": state(doc, gateId=None)}])
    )["available"]
    assert root.id == positive.parent_id


@pytest.mark.parametrize("target", ["f" * 32, "foreign"])
def test_missing_or_foreign_selected_population_does_not_fall_back(experiment, target):
    doc = experiment
    result = plan_navigation(
        doc,
        request(doc, "population", targetGateId=doc.gates[2].id if target == "foreign" else target),
    )
    assert not result["available"] and not result["views"] and "unavailable" in result["reason"]


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"targetGateId": "invalid"},
        {"targetGateId": None, "rememberedViews": "invalid"},
    ],
)
def test_population_requests_require_explicit_valid_selection(experiment, extra):
    with pytest.raises(ValidationError):
        request(experiment, "population", **extra)


def test_memory_cannot_cross_sources_duplicate_populations_or_exceed_bound(experiment):
    doc = experiment
    for remembered in (
        [state(doc, 2)],
        [state(doc, workspaceId="f" * 32)],
        [state(doc), state(doc)],
        [state(doc)] * 193,
    ):
        with pytest.raises(ValidationError):
            request(doc, "parent", rememberedViews=remembered)
    with pytest.raises(ValidationError):
        request(doc, "next", rememberedViews=[state(doc)])
    with pytest.raises(ValidationError):
        request(doc, "next", targetGateId=None)
    with pytest.raises(ValidationError):
        request(
            doc,
            "parent",
            views=[{"id": "main", "state": state(doc)}, {"id": "a" * 32, "state": state(doc)}],
        )
