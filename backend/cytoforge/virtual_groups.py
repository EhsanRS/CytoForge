"""Live pooled group views over original sample events and correction assignments."""

from __future__ import annotations

import hashlib
import json

import numpy as np
from pydantic import Field

from .formulas import parse
from .models import GateDimension, Id, Model, Name
from .report_sources import SourceAudit
from .science import Engine


def cohort(doc, anchor_id, group_id=None, sample_filter=""):
    group = next((g for g in doc.groups if g.id == group_id), None)
    if group_id and group is None:
        raise ValueError("The pooled group no longer exists")
    ids = group.sample_ids if group else [s.id for s in doc.samples]
    samples = {s.id: s for s in doc.samples}
    if len(ids) != len(set(ids)):
        raise ValueError("A pooled group must contain each sample only once")
    needle = sample_filter.casefold()
    members = [
        samples[i]
        for i in ids
        if i in samples
        and needle in (samples[i].name + " " + " ".join(samples[i].tags.values())).casefold()
    ]
    if not members:
        raise ValueError("The pooled group has no samples in the current filter")
    if anchor_id not in {s.id for s in members}:
        raise ValueError("Choose a sample in the pooled group and current filter")
    return members, group.name if group else "All samples"


def parameter_meaning(sample, name):
    derived = {p.name: p for p in sample.derived_parameters}
    computed = {p.name: p for p in sample.computed_parameters}

    def definition(parameter):
        if parameter in sample.aliases:
            return definition(sample.aliases[parameter])
        if parameter in derived:
            expression = derived[parameter].expression
            return (
                "formula",
                expression,
                tuple((n, definition(n)) for n in sorted(parse(expression)[1])),
            )
        if parameter in computed:
            p = computed[parameter]
            return ("fitted", p.analysis_id, p.index)
        if sample.concatenation:
            if parameter == "CF_Source":
                return (
                    "source_codes",
                    tuple((s.index, s.sample_id) for s in sample.concatenation.sources),
                )
            if parameter.startswith("CF_") and parameter[3:] in sample.concatenation.keywords:
                return ("keyword_codes", tuple(sample.concatenation.keywords[parameter[3:]]))
        space = (
            sample.concatenation.values
            if sample.concatenation
            else sample.event_export.values
            if sample.event_export
            else "raw"
        )
        if space == "scale":
            return ("scaled_measurement",)
        return ("measurement",)

    return definition(name)


class PooledEngine(Engine):
    """Reuse rendering over selected 1-D coordinates; never create a merged event file."""

    def __init__(self, doc, base, anchor_id, group_id=None, sample_filter=""):
        self.doc, self.base = doc, base
        self.store, self.cache = base.store, base.cache
        self.members, self.name = cohort(doc, anchor_id, group_id, sample_filter)
        self.anchor = next(s for s in self.members if s.id == anchor_id)
        self.group_id, self.sample_filter = group_id, sample_filter
        self.audit, self.validated = SourceAudit(doc), set()
        identity = dict(workspace=doc.id, group=group_id, members=[s.id for s in self.members])
        self.identity = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[
            :32
        ]
        available = set.intersection(*({c.name for c in s.channels} for s in self.members))
        self.common = [
            c.name
            for c in self.anchor.channels
            if c.name in available
            and all(
                parameter_meaning(s, c.name) == parameter_meaning(self.anchor, c.name)
                for s in self.members
            )
        ]
        # A transient view descriptor only. It is never validated, saved or passed to raw().
        self.view = self.anchor.model_copy(
            deep=True,
            update={
                "id": self.identity,
                "name": self.name,
                "event_count": sum(s.event_count for s in self.members),
                "concatenation": None,
                "event_export": None,
            },
        )
        self.offsets = np.cumsum([0, *(s.event_count for s in self.members)], dtype=np.int64)
        self.gates = {g.id: g for g in doc.gates}
        self.paths, self.matches, self.mappings = {}, {}, {}
        for gate in doc.gates:
            path = self.path(gate.id)
            self.matches.setdefault((gate.sample_id, path), []).append(gate.id)

    def path(self, identifier):
        if identifier not in self.paths:
            gate = self.gates[identifier]
            self.paths[identifier] = (
                (*self.path(gate.parent_id), gate.name) if gate.parent_id else (gate.name,)
            )
        return self.paths[identifier]

    def mapping(self, identifier):
        if identifier in self.mappings:
            return self.mappings[identifier]
        if identifier is None:
            return tuple((s.id, None) for s in self.members)
        if identifier not in self.gates or self.gates[identifier].sample_id not in {
            s.id for s in self.members
        }:
            raise ValueError("The pooled population must belong to a group member")
        path = self.path(identifier)
        result = []
        for sample in self.members:
            matches = self.matches.get((sample.id, path), [])
            if len(matches) != 1:
                raise ValueError(
                    f"{sample.name}: {'missing' if not matches else 'ambiguous'} pooled population "
                    + " / ".join(path)
                )
            result.append((sample.id, matches[0]))
        self.mappings[identifier] = tuple(result)
        return self.mappings[identifier]

    def validate(self, names=(), populations=()):
        key = (tuple(sorted(names)), tuple(populations))
        if key in self.validated:
            return
        if not set(names) <= set(self.common):
            missing = set(names) - set(self.common)
            raise ValueError(
                "Parameters are unavailable or have different definitions across the group: "
                + ", ".join(sorted(missing))
            )
        for sample in self.members:
            ids = [dict(self.mapping(identifier))[sample.id] for identifier in populations]
            if self.audit.closure(sample.id, names, ids)["stale"]:
                raise ValueError(
                    f"{sample.name}: a pooled parameter or population is stale; refit its model"
                )
        self.validated.add(key)

    def sample(self, workspace, sample_id):
        return (
            self.view
            if sample_id in {self.anchor.id, self.identity}
            else self.base.sample(workspace, sample_id)
        )

    def raw(self, workspace, sample):
        if sample.id == self.identity:
            raise ValueError("A virtual group has no merged acquired event array")
        return self.base.raw(workspace, sample)

    def column(
        self, workspace, sample, name, spec=None, compensated=True, compensation_ref="sample"
    ):
        if sample.id != self.identity:
            return self.base.column(workspace, sample, name, spec, compensated, compensation_ref)
        self.validate([name])
        key = (
            workspace.id,
            workspace.revision,
            "pooled-column",
            self.identity,
            name,
            spec.model_dump_json() if spec else None,
            compensated,
            compensation_ref,
        )
        values = self.cache.get(key)
        if values is None:
            values = np.concatenate(
                [
                    self.base.column(workspace, member, name, spec, compensated, compensation_ref)
                    for member in self.members
                ]
            )
            values = self.cache.put(key, values)
        return values

    def mask(self, workspace, sample, gate_id=None):
        if sample.id != self.identity:
            return self.base.mask(workspace, sample, gate_id)
        self.validate(populations=[gate_id])
        key = (workspace.id, workspace.revision, "pooled-mask", self.identity, gate_id)
        values = self.cache.get(key)
        if values is None:
            mapping = dict(self.mapping(gate_id))
            values = self.cache.put(
                key,
                np.concatenate(
                    [
                        self.base.mask(workspace, member, mapping[member.id])
                        for member in self.members
                    ]
                ),
            )
        return values

    def resolve_gate(self, workspace, sample, gate):
        return self.base.resolve_gate(
            workspace, self.anchor if sample.id == self.identity else sample, gate
        )

    def source_ids(self, indices):
        indices = np.asarray(indices, dtype=np.int64)
        if np.any(indices < 0) or np.any(indices >= self.view.event_count):
            raise ValueError("A pooled event index is outside its source samples")
        sources = np.searchsorted(self.offsets[1:], indices, side="right")
        return sources, indices - self.offsets[sources]

    def descriptor(self, gate_id=None):
        mapping = dict(self.mapping(gate_id))
        rows = []
        for index, sample in enumerate(self.members):
            selected = self.base.mask(self.doc, sample, mapping[sample.id])
            rows.append(
                dict(
                    sample_id=sample.id,
                    sample_name=sample.name,
                    source_index=index,
                    offset=int(self.offsets[index]),
                    event_count=sample.event_count,
                    population_id=mapping[sample.id],
                    count=int(np.count_nonzero(selected)),
                )
            )
        return dict(
            group_id=self.group_id,
            group_name=self.name,
            sample_filter=self.sample_filter,
            common_channels=self.common,
            total_events=self.view.event_count,
            population_events=sum(row["count"] for row in rows),
            sources=rows,
        )

    def counts(self):
        result = []
        for gate in self.doc.gates:
            if gate.sample_id != self.anchor.id:
                continue
            try:
                selected = self.mask(self.doc, self.view, gate.id)
                parent = self.mask(self.doc, self.view, gate.parent_id)
                count, parent_count = int(selected.sum()), int(parent.sum())
                result.append(
                    dict(
                        id=gate.id,
                        count=count,
                        complete=True,
                        percent_parent=100 * count / parent_count if parent_count else None,
                        percent_total=100 * count / self.view.event_count
                        if self.view.event_count
                        else None,
                    )
                )
            except ValueError as error:
                result.append(
                    dict(
                        id=gate.id,
                        count=None,
                        complete=False,
                        error=str(error),
                        percent_parent=None,
                        percent_total=None,
                    )
                )
        return result


class Scope(Model):
    anchor_id: Id
    group_id: Id | None = None
    sample_filter: str = Field(default="", max_length=256)
    gate_id: Id | None = None
    channels: list[Name] = Field(default_factory=list, max_length=128)


def plot(doc, base, anchor_id, group_id=None, sample_filter="", **options):
    from .plotting import plot_payload, project_gates

    include_source = options.pop("include_source", False)

    pooled = PooledEngine(doc, base, anchor_id, group_id, sample_filter)
    names = []
    for field, axis in (("x_dimension", "x"), ("y_dimension", "y")):
        dim = options.get(field)
        if dim:
            names.extend(dim.ratio_channels or [dim.channel])
        elif options.get(axis):
            names.append(options[axis])
    gate_id, backgate_id = options.get("gate_id"), options.get("backgate_id")
    pooled.validate(names, [gate_id, backgate_id])
    payload = plot_payload(doc, pooled, anchor_id, **options)
    axes = [GateDimension.model_validate(d) for d in payload.get("axes", [])]
    selected = dict(pooled.mapping(gate_id))
    backgated = dict(pooled.mapping(backgate_id)) if backgate_id else {}
    canonical = {
        pooled.path(g.id): g.id
        for g in doc.gates
        if g.sample_id == anchor_id and len(pooled.matches[(anchor_id, pooled.path(g.id))]) == 1
    }
    overlays = []
    for member in pooled.members:
        if options.get("mode") == "3d":
            from .three_dimensional import project_boxes

            projected = project_boxes(
                doc, member, axes, payload["bounds"], selected[member.id], base
            )
        else:
            projected = project_gates(
                doc,
                member,
                options["x"],
                options.get("y"),
                axes[0].transform,
                axes[1].transform if len(axes) > 1 else None,
                selected[member.id],
                payload["bounds"],
                axes[0],
                axes[1] if len(axes) > 1 else None,
                base,
            )
        for overlay in projected:
            identifier = overlay["id"]
            target = canonical.get(pooled.path(identifier))
            if target:
                overlay.update(
                    id=target,
                    pooled_source_id=member.id,
                    pooled_source_name=member.name,
                    pooled_gate_id=identifier,
                )
                overlays.append(overlay)
    payload["overlays"] = overlays
    if options.get("mode") == "3d":
        payload["boxes"] = overlays
    payload["pooled"] = pooled.descriptor(gate_id)
    if not include_source:
        return payload
    sources = []
    for member in pooled.members:
        local_ids = [
            selected[member.id],
            *[o["pooled_gate_id"] for o in overlays if o["pooled_source_id"] == member.id],
        ]
        if backgate_id:
            local_ids.append(backgated[member.id])
        if member.id == anchor_id and options.get("coordinate_gate_id"):
            local_ids.append(options["coordinate_gate_id"])
        dimensions = [*payload["axes"], *[d for d in payload.get("scalar_dimensions", []) if d]]
        sources.append(
            pooled.audit.closure(member.id, dimensions=dimensions, populations=local_ids)
        )
    payload["pooled_source"] = dict(
        kind="virtual_group",
        group=payload["pooled"],
        sources=sources,
        stale=any(source["stale"] for source in sources),
    )
    return payload


def render(
    doc,
    base,
    sample_id,
    x,
    y=None,
    gate_id=None,
    coordinate_gate_id=None,
    x_transform=None,
    y_transform=None,
    bins=96,
    bounds=None,
    mode="density",
    backgate_id=None,
    graph_options=None,
    three_d=None,
    x_dimension=None,
    y_dimension=None,
    *,
    group_id=None,
    sample_filter="",
):
    return plot(
        doc,
        base,
        sample_id,
        group_id,
        sample_filter,
        x=x,
        y=y,
        gate_id=gate_id,
        coordinate_gate_id=coordinate_gate_id,
        x_transform=x_transform,
        y_transform=y_transform,
        bins=bins,
        bounds=bounds,
        mode=mode,
        backgate_id=backgate_id,
        graph_options=graph_options,
        three_d=three_d,
        x_dimension=x_dimension,
        y_dimension=y_dimension,
        include_source=True,
    )
