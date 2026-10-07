"""Reviewed channel aliases over immutable acquisition and spectral measurements."""

from __future__ import annotations

import hashlib
import json

from pydantic import Field, model_validator

from .formulas import parse
from .models import Id, Model, Name, Sample, Workspace
from .report_sources import SourceAudit
from .store import ConflictError


class Binding(Model):
    name: Name
    source: Name
    label: str | None = Field(default=None, max_length=160)


class Mapping(Model):
    sample_id: Id
    bindings: list[Binding] = Field(max_length=128)

    @model_validator(mode="after")
    def unique(self):
        if len({b.name for b in self.bindings}) != len(self.bindings):
            raise ValueError("Choose each shared parameter name only once")
        return self


class Request(Model):
    revision: int = Field(ge=0)
    mappings: list[Mapping] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def unique(self):
        if len({m.sample_id for m in self.mappings}) != len(self.mappings):
            raise ValueError("Choose each sample only once")
        return self


class Apply(Request):
    review_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def sources(doc, sample):
    """Offer actual detector names and outputs of the current spectral matrix."""
    available = {c.name for c in sample.acquisition_channels}
    matrix = next((m for m in doc.compensations if m.id == sample.compensation_id), None)
    if matrix and matrix.kind == "spectral":
        available.update(matrix.outputs)
    return [c for c in sample.channels if c.name in available]


def mentions(value, name):
    """Inspect current scientific parameter fields, excluding historical snapshots."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"provenance", "input_snapshot", "source_snapshot", "metadata", "tags"}:
                continue
            parameter = key in {
                "x",
                "y",
                "z",
                "channel",
                "channels",
                "ratio_channels",
                "pulse_area",
                "pulse_height",
                "channel_overrides",
            }
            parameter |= key.endswith(("_channel", "_channels"))
            if parameter and (
                item == name
                or isinstance(item, (list, tuple))
                and name in item
                or isinstance(item, dict)
                and name in item.values()
            ):
                return True
            if mentions(item, name):
                return True
    elif isinstance(value, (list, tuple)):
        return any(mentions(item, name) for item in value)
    return False


def references(doc, sample, name):
    found = []
    for parameter in sample.derived_parameters:
        if name in parse(parameter.expression)[1]:
            found.append("Formula: " + parameter.name)
    for gate in doc.gates:
        if gate.sample_id == sample.id and mentions(gate.model_dump(), name):
            found.append("Population: " + gate.name)
    for item in SourceAudit(doc).models.values():
        result = item[0]
        request = result.request.model_dump()
        ids = {i["sample_id"] for i in request.get("inputs", [])}
        ids.update(i["sample_id"] for i in request.get("controls", []) if "sample_id" in i)
        if request.get("sample_id"):
            ids.add(request["sample_id"])
        if sample.id in ids and mentions(request, name):
            found.append("Fitted analysis: " + result.request.name)
    for layout in doc.layouts:
        for plot in [*layout.plots, *(e.plot for e in layout.elements if e.plot)]:
            ids = {plot.sample_id, *(layer.sample_id for layer in plot.overlays)}
            if (sample.id in ids or layout.batch.mode != "off") and mentions(
                plot.model_dump(), name
            ):
                found.append("Layout: " + layout.name)
                break
    for table in doc.tables:
        ids = set(table.sample_ids) or {s.id for s in doc.samples}
        if table.group_id:
            group = next((g for g in doc.groups if g.id == table.group_id), None)
            ids &= set(group.sample_ids) if group else set()
        if sample.id in ids and mentions(table.model_dump(), name):
            found.append("Table: " + table.name)
    for plate in doc.plates:
        if any(sample.id in ids for ids in plate.assignments.values()) and mentions(
            [c.model_dump() for c in plate.columns], name
        ):
            found.append("Plate: " + plate.name)
    return sorted(set(found))


def plan(doc: Workspace, request: Request):
    if doc.revision != request.revision:
        raise ConflictError("Workspace changed. Reload and review the channel mapping again.")
    candidate = doc.model_copy(deep=True)
    samples = {s.id: s for s in candidate.samples}
    reviews = []
    for mapping in request.mappings:
        sample = samples.get(mapping.sample_id)
        if sample is None:
            raise ValueError("A selected sample no longer exists")
        original = next(s for s in doc.samples if s.id == sample.id)
        available = {c.name: c for c in sources(doc, original)}
        channels = {c.name: c for c in original.channels}
        aliases, definitions, rows = {}, [], []
        for binding in mapping.bindings:
            previous = original.aliases.get(binding.name)
            if binding.source not in available and previous != binding.source:
                raise ValueError(
                    f"{original.name}: {binding.source} is not an acquired channel "
                    "or an active unmixed output"
                )
            if binding.source in original.aliases:
                raise ValueError("Alias chains are not supported; choose the original parameter")
            if binding.name in channels and binding.name not in original.aliases:
                if binding.name != binding.source:
                    raise ValueError(f"{original.name}: {binding.name} already exists")
                rows.append(dict(name=binding.name, source=binding.source, action="already named"))
                continue
            source = channels[binding.source]
            aliases[binding.name] = binding.source
            definition = source.model_copy(deep=True, update={"name": binding.name})
            definition.label = binding.label if binding.label is not None else binding.name
            if binding.name in original.aliases:
                old = channels[binding.name]
                definition.transform = old.transform.model_copy(deep=True)
                if binding.label is None:
                    definition.label = old.label
            definitions.append(definition)
            rows.append(
                dict(
                    name=binding.name,
                    source=binding.source,
                    previous=previous,
                    action="rebound"
                    if previous and previous != binding.source
                    else ("retained" if previous else "added"),
                    references=references(doc, original, binding.name) if previous else [],
                )
            )
        for name in original.aliases.keys() - aliases.keys():
            dependencies = references(doc, original, name)
            if dependencies:
                raise ValueError(
                    f"{original.name}: cannot remove {name}; used by " + "; ".join(dependencies)
                )
            rows.append(dict(name=name, source=original.aliases[name], action="removed"))
        sample.channels = [c for c in sample.channels if c.name not in original.aliases]
        sample.channels.extend(definitions)
        sample.aliases = aliases
        Sample.model_validate(sample.model_dump())
        reviews.append(dict(sample_id=sample.id, sample_name=sample.name, bindings=rows))
    candidate = Workspace.model_validate(candidate.model_dump())
    before, after = SourceAudit(doc), SourceAudit(candidate)
    affected = [
        dict(id=identifier, name=item[0].request.name)
        for identifier, item in before.models.items()
        if not before.stale(identifier) and after.stale(identifier)
    ]
    preview = dict(
        revision=doc.revision,
        samples=reviews,
        affected_models=affected,
        changed=any(
            samples[m.sample_id].model_dump()
            != next(s for s in doc.samples if s.id == m.sample_id).model_dump()
            for m in request.mappings
        ),
    )
    payload = dict(
        workspace_sha256=hashlib.sha256(doc.model_dump_json().encode()).hexdigest(),
        request=request.model_dump(exclude={"review_hash"}),
        preview=preview,
    )
    preview["review_hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return candidate, preview


def apply(doc: Workspace, request: Apply):
    candidate, preview = plan(doc, request)
    if preview["review_hash"] != request.review_hash:
        raise ConflictError("The channel mapping changed. Review it again before applying.")
    if not preview["changed"]:
        raise ValueError("This mapping already matches the selected samples")
    doc.samples = candidate.samples
