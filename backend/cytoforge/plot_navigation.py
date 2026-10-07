"""Read-only, revision-bound plans for independent and synchronized native plots."""

from __future__ import annotations

from collections import defaultdict
from typing import Annotated, Literal

from pydantic import Field, model_serializer, model_validator

from .formulas import parse
from .models import GraphOptions, Id, Model, Name, PlotDimension, ThreeDView, Transform
from .plot_coordinates import resolve_dimension
from .store import ConflictError


class NavigationState(Model):
    workspaceId: Id
    sampleId: Id
    gateId: Id | None
    x: Name
    y: Name | None
    mode: Literal["density", "scatter", "histogram", "cdf", "contour", "zebra", "pseudocolor", "3d"]
    groupId: Id | None = None
    pooled: bool = False
    sampleFilter: str = Field(default="", max_length=256)
    coordinateGateId: Id | None = None
    backgateId: Id | None = None
    xTransform: Transform | None = None
    yTransform: Transform | None = None
    xDimension: PlotDimension | None = None
    yDimension: PlotDimension | None = None
    graphOptions: GraphOptions = Field(default_factory=GraphOptions)
    threeD: ThreeDView | None = None
    bins: int = Field(default=160, ge=16, le=384)
    bounds: list[float] | None = Field(default=None, max_length=6)

    @model_serializer(mode="wrap")
    def legacy(self, serializer):
        value = serializer(self)
        if not self.pooled:
            value.pop("pooled", None)
        return value

    @model_validator(mode="after")
    def display_shape(self):
        for name, dim in ((self.x, self.xDimension), (self.y, self.yDimension)):
            if dim and dim.channel != name:
                raise ValueError("Plot coordinate definitions must match their selected parameters")
        if self.mode not in {"histogram", "cdf"} and self.y is None:
            raise ValueError("Select the plot Y parameter")
        if self.mode == "3d" and self.threeD is None:
            raise ValueError("Select the plot Z parameter")
        length = 6 if self.mode == "3d" else 2 if self.mode in {"histogram", "cdf"} else 4
        if self.bounds is not None and (
            len(self.bounds) != length
            or any(self.bounds[i] >= self.bounds[i + 1] for i in range(0, length, 2))
        ):
            raise ValueError("Plot bounds must be finite and increasing")
        return self


class NavigationView(Model):
    id: Annotated[str, Field(pattern=r"^(main|[a-f0-9]{32})$")]
    state: NavigationState


class NavigationRequest(Model):
    revision: int = Field(ge=0)
    initiator: Annotated[str, Field(pattern=r"^(main|[a-f0-9]{32})$")]
    direction: Literal[
        "previous",
        "next",
        "parent",
        "select",
        "population",
        "child",
        "sibling_previous",
        "sibling_next",
        "reset_population",
    ]
    targetSampleId: Id | None = None
    targetGateId: Id | None = None
    rememberedViews: list[NavigationState] = Field(default_factory=list, max_length=192)
    views: list[NavigationView] = Field(min_length=1, max_length=33)

    @model_validator(mode="after")
    def coherent_views(self):
        if len({v.id for v in self.views}) != len(self.views):
            raise ValueError("Plot window IDs must be unique")
        if self.initiator not in {v.id for v in self.views}:
            raise ValueError("The initiating plot is unavailable")
        if len({(v.state.workspaceId, v.state.sampleId) for v in self.views}) != 1:
            raise ValueError("Synchronized plots must share a workspace and source sample")
        if (self.direction == "select") != (self.targetSampleId is not None):
            raise ValueError("A sample selection requires exactly one target sample")
        population = self.direction in {
            "parent",
            "population",
            "child",
            "sibling_previous",
            "sibling_next",
            "reset_population",
        }
        if population and len(self.views) != 1:
            raise ValueError("Population navigation applies to the current plot")
        if (self.direction == "population") != ("targetGateId" in self.model_fields_set):
            raise ValueError("Population selection requires an explicit population or all events")
        if self.rememberedViews and not population:
            raise ValueError("Remembered views apply to population navigation")
        source = self.views[0].state
        if any(
            v.workspaceId != source.workspaceId or v.sampleId != source.sampleId
            for v in self.rememberedViews
        ):
            raise ValueError("Remembered views remain in the source workspace and sample")
        if len(
            {
                (
                    v.gateId,
                    v.pooled,
                    v.groupId if v.pooled else None,
                    v.sampleFilter if v.pooled else "",
                )
                for v in self.rememberedViews
            }
        ) != len(self.rememberedViews):
            raise ValueError("Remembered population views must be unique")
        return self


def plan_navigation(doc, request: NavigationRequest, engine=None):
    if request.revision != doc.revision:
        raise ConflictError("Workspace changed. Reload before navigating plots.")
    if any(v.state.workspaceId != doc.id for v in request.views):
        raise ValueError("Plots remain in their workspace")
    samples = {s.id: s for s in doc.samples}
    groups = {g.id: set(g.sample_ids) for g in doc.groups}
    gates = {g.id: g for g in doc.gates}
    paths, matches = {}, defaultdict(list)

    def path(gate):
        if gate.id not in paths:
            chain, current = [], gate
            while current and current.id not in paths:
                chain.append(current)
                current = gates.get(current.parent_id)
            prefix = paths[current.id] if current else ()
            for item in reversed(chain):
                prefix = (*prefix, item.name)
                paths[item.id] = prefix
        return paths[gate.id]

    for gate in doc.gates:
        matches[gate.sample_id, path(gate)].append(gate)
    initiator = next(v for v in request.views if v.id == request.initiator)
    source_id = initiator.state.sampleId
    if source_id not in samples:
        raise ValueError("The source sample is unavailable. Restore it or choose another sample.")
    source = samples[source_id]
    population_action = request.direction in {
        "parent",
        "population",
        "child",
        "sibling_previous",
        "sibling_next",
        "reset_population",
    }

    def in_cohort(state, sample):
        if state.groupId and state.groupId not in groups:
            raise ValueError("The plot group is unavailable. Restore it or choose a group.")
        return (state.groupId is None or sample.id in groups[state.groupId]) and (
            state.sampleFilter.lower() in f"{sample.name} {' '.join(sample.tags.values())}".lower()
        )

    def coordinate(state, sample, name, axis):
        gate = gates.get(state.coordinateGateId)
        explicit = (
            state.xDimension,
            state.yDimension,
            state.threeD.z_dimension if state.threeD else None,
            state.threeD.color_dimension if state.threeD else None,
            state.threeD.size_dimension if state.threeD else None,
        )[axis]
        dim = resolve_dimension(doc, sample, name, gate, axis if axis < 3 else None, explicit)
        if state.mode == "3d" and state.threeD.compensation == "uncompensated" and explicit is None:
            dim = dim.model_copy(update={"compensation_ref": "uncompensated"})
        return dim.transform, dim

    def references(state):
        for identifier, label in (
            (state.gateId, "population"),
            (state.coordinateGateId, "coordinate population"),
            (state.backgateId, "backgate"),
        ):
            if identifier and (
                identifier not in gates or gates[identifier].sample_id != state.sampleId
            ):
                raise ValueError(f"The {label} is unavailable. Restore it or choose a replacement.")

    def active_parameters(state):
        parameters = [(state.x, 0)]
        if state.mode not in {"histogram", "cdf"}:
            parameters.append((state.y, 1))
        if state.mode == "3d":
            parameters.extend(
                [
                    (state.threeD.z, 2),
                    (state.threeD.color_by, 3),
                    (state.threeD.size_by, 4),
                ]
            )
        return [(name, axis) for name, axis in parameters if name]

    for view in request.views:
        if not population_action:
            references(view.state)
        if (
            not population_action
            and request.direction != "select"
            and not in_cohort(view.state, source)
        ):
            raise ValueError("The current sample is outside a plot's group or search filter")
        if not population_action:
            for name, axis in active_parameters(view.state):
                coordinate(view.state, source, name, axis)

    def result(views=None, reason=None, skipped=None):
        if views:
            from .virtual_groups import PooledEngine

            for view in views:
                state = view.state
                if state.pooled:
                    if engine is None:
                        raise ValueError("Pooled navigation requires the sample engine")
                    pooled = PooledEngine(
                        doc, engine, state.sampleId, state.groupId, state.sampleFilter
                    )
                    names = []
                    for name, axis in active_parameters(state):
                        dimension = coordinate(state, samples[state.sampleId], name, axis)[1]
                        names.extend(dimension.ratio_channels or [name])
                    try:
                        pooled.validate(names, [state.gateId, state.backgateId])
                    except ValueError as error:
                        return result(reason=str(error), skipped=skipped)
        return {
            "revision": doc.revision,
            "available": views is not None,
            "reason": reason,
            "views": [v.model_dump(mode="json", exclude_none=False) for v in views or []],
            "skipped": skipped or [],
        }

    if population_action:
        state = initiator.state.model_copy(deep=True)
        gate = gates.get(state.gateId)
        if (
            state.gateId
            and (gate is None or gate.sample_id != source_id)
            and request.direction != "population"
        ):
            return result(reason="The current population is unavailable")
        children = [
            g for g in doc.gates if g.sample_id == source_id and g.parent_id == state.gateId
        ]
        if request.direction == "parent":
            if gate is None:
                return result(reason="This plot already displays all events")
            target_id, defining = gate.parent_id, gate
        elif request.direction == "population":
            target_id, defining = request.targetGateId, None
        elif request.direction == "reset_population":
            target_id, defining = state.gateId, None
        elif request.direction == "child":
            if not children:
                return result(reason="This population has no child populations")
            target_id, defining = children[0].id, None
        else:
            if gate is None:
                return result(reason="All events has no sibling populations")
            siblings = [
                g for g in doc.gates if g.sample_id == source_id and g.parent_id == gate.parent_id
            ]
            index = next(i for i, g in enumerate(siblings) if g.id == gate.id)
            index += -1 if request.direction == "sibling_previous" else 1
            if not 0 <= index < len(siblings):
                return result(reason="No sibling population in this direction")
            target_id, defining = siblings[index].id, None
        target = gates.get(target_id)
        if target_id and (target is None or target.sample_id != source_id):
            return result(reason="The selected population is unavailable in this sample")
        remembered = (
            None
            if request.direction == "reset_population"
            else next(
                (
                    v
                    for v in request.rememberedViews
                    if v.gateId == target_id
                    and v.pooled == state.pooled
                    and (
                        not state.pooled
                        or (v.groupId == state.groupId and v.sampleFilter == state.sampleFilter)
                    )
                ),
                None,
            )
        )
        if remembered:
            # Missing optional references remain explicit; existing foreign references are rejected.
            for reference in (remembered.coordinateGateId, remembered.backgateId):
                if reference in gates and gates[reference].sample_id != source_id:
                    raise ValueError(
                        "Remembered coordinate and backgate references belong to another sample"
                    )
            restored = remembered.model_copy(
                update={
                    "groupId": state.groupId,
                    "sampleFilter": state.sampleFilter,
                    "pooled": state.pooled,
                },
                deep=True,
            )
            value = result([NavigationView(id=initiator.id, state=restored)])
            value["restored_view"] = True
            return value
        state.gateId = target_id
        state.backgateId = None
        state.xDimension = state.yDimension = None
        if state.threeD:
            state.threeD = state.threeD.model_copy(
                update={"z_dimension": None, "color_dimension": None, "size_dimension": None}
            )
        if defining is None:
            next_gates = [
                g for g in doc.gates if g.sample_id == source_id and g.parent_id == target_id
            ]
            defining = next((g for g in next_gates if g.y or len(g.dimensions) > 1), None) or next(
                iter(next_gates), target
            )
        if defining and (defining.dimensions or defining.x):
            state.coordinateGateId = defining.id if defining.dimensions else None
            state.x = defining.dimensions[0].channel if defining.dimensions else defining.x
            state.xTransform = (
                defining.dimensions[0].transform if defining.dimensions else defining.x_transform
            )
            state.y = defining.dimensions[1].channel if len(defining.dimensions) > 1 else defining.y
            state.yTransform = (
                defining.dimensions[1].transform
                if len(defining.dimensions) > 1
                else defining.y_transform
            )
            if len(defining.dimensions) >= 3:
                state.mode = "3d"
                state.threeD = (
                    state.threeD or ThreeDView(z=defining.dimensions[2].channel)
                ).model_copy(
                    update={
                        "z": defining.dimensions[2].channel,
                        "z_transform": defining.dimensions[2].transform,
                    }
                )
            elif state.y is None:
                state.mode = state.mode if state.mode in {"histogram", "cdf"} else "histogram"
            elif state.mode in {"histogram", "cdf", "3d"}:
                state.mode = "density"
        else:
            state.coordinateGateId = None
            channels = source.channels
            if state.pooled:
                from .virtual_groups import PooledEngine

                common = PooledEngine(
                    doc, engine, source.id, state.groupId, state.sampleFilter
                ).common
                channels = [c for c in channels if c.name in common]
                if not channels:
                    return result(reason="Harmonize the group's parameters before pooling")
            state.x, state.xTransform = channels[0].name, None
            state.y = channels[1].name if len(channels) > 1 else None
            state.yTransform = None
            state.mode = "density" if state.y else "histogram"
        state.bounds = None
        return result([NavigationView(id=initiator.id, state=state)])

    def mapped(state, target):
        value = state.model_copy(deep=True)
        value.sampleId = target.id
        if not in_cohort(state, target):
            raise ValueError("Outside an open plot's group or search filter")
        for field in ("gateId", "coordinateGateId", "backgateId"):
            original = gates.get(getattr(state, field))
            if original:
                candidates = matches[target.id, path(original)]
                label = " / ".join(path(original))
                if len(candidates) != 1:
                    raise ValueError(
                        f"{'Missing' if not candidates else 'Ambiguous'} population: {label}"
                    )
                if candidates[0].kind != original.kind:
                    raise ValueError(f"Population definition differs: {label}")
                setattr(value, field, candidates[0].id)
        for name, axis in active_parameters(state):
            _, before = coordinate(state, source, name, axis)
            _, after = coordinate(value, target, name, axis)
            ratio_fields = (
                "ratio_channels",
                "ratio_a",
                "ratio_b",
                "ratio_c",
                "ratio_bound_min",
                "ratio_bound_max",
            )
            if (bool(before and before.ratio_channels) != bool(after and after.ratio_channels)) or (
                before
                and after
                and any(getattr(before, f) != getattr(after, f) for f in ratio_fields)
            ):
                raise ValueError(f"Ratio definition differs: {name}")
            if state.mode != "3d" or state.threeD.compensation != "uncompensated":

                def compensation_basis(dim):
                    ref = dim.compensation_ref if dim else "sample"
                    if ref in {"sample", "uncompensated", "FCS"}:
                        return ref
                    matrix = next((m for m in doc.compensations if m.id == ref), None)
                    return (
                        (
                            matrix.kind,
                            matrix.detectors,
                            matrix.outputs,
                            matrix.matrix,
                            matrix.background,
                            matrix.weights,
                        )
                        if matrix
                        else ref
                    )

                if compensation_basis(before) != compensation_basis(after):
                    raise ValueError(f"Coordinate compensation differs: {name}")

            def meaning(sample, parameter_name=name):
                derived = {p.name: p for p in sample.derived_parameters}
                computed = {p.name: p for p in sample.computed_parameters}

                def definition(parameter):
                    if parameter in derived:
                        expression = derived[parameter].expression
                        return (
                            "derived",
                            expression,
                            tuple((n, definition(n)) for n in sorted(parse(expression)[1])),
                        )
                    if parameter in computed:
                        p = computed[parameter]
                        return ("computed", p.analysis_id, p.index)
                    return ("acquired",)

                return definition(parameter_name)

            names = [name, *(before.ratio_channels if before and before.ratio_channels else [])]
            if any(
                meaning(source, selected_name) != meaning(target, selected_name)
                for selected_name in names
            ):
                raise ValueError(f"Parameter definition differs: {name}")
        value.xTransform = state.xTransform or coordinate(state, source, state.x, 0)[0]
        if state.y and state.mode not in {"histogram", "cdf"}:
            value.yTransform = state.yTransform or coordinate(state, source, state.y, 1)[0]
        if state.mode == "3d":
            for field, parameter, axis in (
                ("z_transform", state.threeD.z, 2),
                ("color_transform", state.threeD.color_by, 3),
                ("size_transform", state.threeD.size_by, 4),
            ):
                if parameter and getattr(value.threeD, field) is None:
                    setattr(value.threeD, field, coordinate(state, source, parameter, axis)[0])
        return NavigationState.model_validate(value.model_dump())

    cohort = [s for s in doc.samples if in_cohort(initiator.state, s)]
    if request.direction == "select" and request.targetSampleId not in samples:
        return result(
            reason="The selected sample is unavailable. Restore it or choose another sample."
        )
    index = next(i for i, s in enumerate(cohort) if s.id == source_id)
    candidates = (
        ([samples[request.targetSampleId]] if request.targetSampleId in samples else [])
        if request.direction == "select"
        else (
            cohort[index + 1 :] if request.direction == "next" else list(reversed(cohort[:index]))
        )
    )
    skipped = []
    for target in candidates:
        try:
            views = [NavigationView(id=v.id, state=mapped(v.state, target)) for v in request.views]
        except ValueError as error:
            skipped.append({"sample_id": target.id, "name": target.name, "reason": str(error)})
            continue
        return result(views, skipped=skipped)
    reason = (
        skipped[0]["reason"]
        if request.direction == "select" and skipped
        else (
            "No compatible sample in this direction" if skipped else "No sample in this direction"
        )
    )
    return result(reason=reason, skipped=skipped)
