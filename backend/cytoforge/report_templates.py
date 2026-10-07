"""Portable report compositions with reviewed, destination-owned scientific bindings."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field, model_validator

from . import biology, reports
from .models import Id, LayoutDefinition, Model, Name, ReportBatch, ReportTemplateOrigin, Workspace
from .store import ConflictError

MAX_TEMPLATE_BYTES = 8 * 1024**2
KINDS = Literal[
    "sample",
    "population",
    "channel",
    "group",
    "compensation",
    "table",
    "column",
    "cell_cycle",
    "proliferation",
    "kinetics",
    "comparison",
    "comparison_parameter",
    "plate",
]
PARENTS = {
    "population": "sample",
    "channel": "sample",
    "column": "table",
    "comparison_parameter": "comparison",
}
COLLECTIONS = {
    "sample": "samples",
    "population": "gates",
    "group": "groups",
    "compensation": "compensations",
    "table": "tables",
    "cell_cycle": "cell_cycle_results",
    "proliferation": "proliferation_results",
    "kinetics": "kinetics_results",
    "comparison": "comparison_results",
    "plate": "plates",
}
RESULT_KINDS = {"cell_cycle", "proliferation", "kinetics", "comparison"}


def object_name(kind, item):
    return item.request.name if kind in RESULT_KINDS else item.name


def binding_key(kind, identifier, owner=None):
    return f"{kind}/{owner}/{identifier}" if kind == "channel" else f"{kind}/{identifier}"


class TemplateBinding(Model):
    key: str = Field(min_length=1, max_length=400)
    kind: KINDS
    source_id: Name
    owner_id: Id | None = None
    name: Name
    population_path: list[Name] = Field(default_factory=list, max_length=128)
    details: dict[str, str] = Field(default_factory=dict, max_length=16)

    @model_validator(mode="after")
    def identity(self):
        if self.key != binding_key(self.kind, self.source_id, self.owner_id):
            raise ValueError("Template binding identity is inconsistent")
        if self.kind != "channel" and not re.fullmatch(r"[a-f0-9]{32}", self.source_id):
            raise ValueError("Template object references require valid IDs")
        if bool(self.owner_id) != (self.kind in PARENTS):
            raise ValueError("Template binding owner is inconsistent")
        if self.kind != "population" and self.population_path:
            raise ValueError("Only population bindings carry ancestry paths")
        if any(len(key) > 160 or len(value) > 2048 for key, value in self.details.items()):
            raise ValueError("Template binding details exceed their bounds")
        return self


class ReportTemplate(Model):
    format: Literal["cytoforge-report-template"] = "cytoforge-report-template"
    version: Literal[1] = 1
    source_workspace_id: Id
    definition: LayoutDefinition
    bindings: list[TemplateBinding] = Field(default_factory=list, max_length=16384)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class TemplateExport(Model):
    revision: int = Field(ge=0)
    definition: LayoutDefinition


class TemplateImport(Model):
    revision: int = Field(ge=0)
    id: Id
    template: ReportTemplate
    name: Name
    mappings: dict[
        Annotated[str, Field(min_length=1, max_length=400)],
        Annotated[str, Field(min_length=1, max_length=160)] | None,
    ] = Field(default_factory=dict, max_length=16384)
    batch_scope: Literal["destination", "template"] = "destination"
    batch_sample_ids: list[Id] = Field(default_factory=list, max_length=1024)
    review_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


@dataclass
class Reference:
    path: tuple
    key: str
    token: str | None = None


class Inventory:
    def __init__(self, definition, population_owner):
        self.bindings = {}
        self.references = []
        self.population_owner = population_owner
        self.definition = definition
        self.scan()

    def add(self, kind, value, path=None, owner=None, token=None):
        if value is None:
            return
        if kind == "population":
            actual = self.population_owner(value)
            if owner is not None and actual != owner:
                raise ValueError("A template population belongs to another acquisition")
            owner = actual
        key = binding_key(kind, value, owner)
        entry = dict(kind=kind, source_id=value, owner_id=owner)
        if key in self.bindings and self.bindings[key] != entry:
            raise ValueError("Template binding owners disagree")
        self.bindings[key] = entry
        if kind in PARENTS:
            self.add(PARENTS[kind], owner)
        if path is not None:
            self.references.append(Reference(tuple(path), key, token))

    def dimension(self, name, dimension, owner, path, name_path):
        if dimension and dimension.get("ratio_channels"):
            for index, value in enumerate(dimension["ratio_channels"]):
                self.add("channel", value, (*path, "ratio_channels", index), owner)
        else:
            self.add("channel", name, name_path, owner)
            if dimension:
                self.add("channel", dimension["channel"], (*path, "channel"), owner)
        if dimension and dimension["compensation_ref"] not in {"sample", "uncompensated", "FCS"}:
            self.add("compensation", dimension["compensation_ref"], (*path, "compensation_ref"))

    def plot(self, plot, path):
        owner = plot["sample_id"]
        self.add("group", plot.get("group_id"), (*path, "group_id"))
        for layer, layer_path in [(plot, path)] + [
            (layer, (*path, "overlays", index)) for index, layer in enumerate(plot["overlays"])
        ]:
            self.add("sample", layer["sample_id"], (*layer_path, "sample_id"))
            for field in ["gate_id", "coordinate_gate_id", "backgate_id"]:
                self.add("population", layer.get(field), (*layer_path, field), layer["sample_id"])
        for axis in ["x", "y"]:
            self.dimension(
                plot[axis],
                plot.get(f"{axis}_dimension"),
                owner,
                (*path, f"{axis}_dimension"),
                (*path, axis),
            )
        if view := plot.get("three_d"):
            for name, field in [
                ("z", "z_dimension"),
                ("color_by", "color_dimension"),
                ("size_by", "size_dimension"),
            ]:
                self.dimension(
                    view[name],
                    view.get(field),
                    owner,
                    (*path, "three_d", field),
                    (*path, "three_d", name),
                )

    def scan(self):
        definition = self.definition
        prototype = next(iter(reports.sources(LayoutDefinition.model_validate(definition))), None)
        for index, element in enumerate(definition["elements"]):
            path = ("elements", index)
            if element.get("plot"):
                self.plot(element["plot"], (*path, "plot"))
            owner = element.get("sample_id")
            self.add("sample", owner, (*path, "sample_id"))
            self.add("population", element.get("gate_id"), (*path, "gate_id"), owner)
            self.add("table", element.get("table_id"), (*path, "table_id"))
            for ordinal, value in enumerate(element["column_ids"]):
                self.add("column", value, (*path, "column_ids", ordinal), element.get("table_id"))
            self.add("plate", element.get("plate_id"), (*path, "plate_id"))
            if element.get("result_id"):
                if element["kind"] not in {"biology", "population_comparison"}:
                    raise ValueError("Remove unused model references before exporting a template")
                kind = (
                    "comparison"
                    if element["kind"] == "population_comparison"
                    else element["platform"].replace("-", "_")
                )
                self.add(kind, element["result_id"], (*path, "result_id"))
            self.add(
                "comparison_parameter",
                element.get("comparison_parameter_id"),
                (*path, "comparison_parameter_id"),
                element.get("result_id"),
            )
            if element["kind"] == "text":
                for match in re.finditer(r"\{\{stat:([^{}]+)\}\}", element["text"]):
                    _, _, channel = match[1].partition(":")
                    if channel:
                        context = owner or prototype
                        if context is None:
                            raise ValueError("Live statistic text needs an acquisition context")
                        self.add("channel", channel, (*path, "text"), context, match[1])
        batch = definition["batch"]
        self.add("group", batch["group_id"], ("batch", "group_id"))
        for index, identifier in enumerate(batch["sample_ids"]):
            self.add("sample", identifier, ("batch", "sample_ids", index))
        for field, kind in [("overrides", "sample"), ("population_overrides", "population")]:
            for key, values in batch[field].items():
                if key.startswith(("sample:", "missing:")):
                    self.add("sample", key.partition(":")[2], ("batch", field, key, "@iteration"))
                for source, target in values.items():
                    self.add(kind, source, ("batch", field, key, source, "@key"))
                    self.add(kind, target, ("batch", field, key, source))


def column_details(column):
    return {
        key: str(getattr(column, key) or "")
        for key in [
            "kind",
            "statistic",
            "channel",
            "platform",
            "metadata_key",
            "biology_metric",
            "expression",
            "percentile",
        ]
    }


def parameter_details(parameter):
    return {"channel": parameter.channel, "transform": parameter.transform.kind}


def exported(workspace, request):
    if request.revision != workspace.revision:
        raise ConflictError("Workspace changed; refresh before exporting the report template")
    definition = reports.migrated(request.definition)
    # Each exported template describes the current composition; prior import history
    # remains attached to the saved report rather than carrying old destination bindings.
    definition.template_origin = None
    definition = LayoutDefinition.model_validate(definition.model_dump())
    data = definition.model_dump()
    objects = {
        kind: {item.id: item for item in getattr(workspace, field)}
        for kind, field in COLLECTIONS.items()
    }
    paths = biology.population_paths(workspace)

    def owner(identifier):
        if identifier not in objects["population"]:
            raise ValueError("A report template references a removed population")
        return objects["population"][identifier].sample_id

    inventory = Inventory(data, owner)
    bindings = []
    for key, entry in inventory.bindings.items():
        kind, identifier, parent = entry["kind"], entry["source_id"], entry["owner_id"]
        details, ancestry = {}, []
        if kind == "channel":
            sample = objects["sample"].get(parent)
            if sample is None or identifier not in {c.name for c in sample.channels}:
                raise ValueError(f"Report parameter {identifier!r} is unavailable")
            name = identifier
        elif kind == "column":
            table = objects["table"].get(parent)
            column = next((c for c in table.columns if c.id == identifier), None) if table else None
            if column is None:
                raise ValueError("A report template references a removed table column")
            name, details = column.name, column_details(column)
        elif kind == "comparison_parameter":
            result = objects["comparison"].get(parent)
            parameter = (
                next((p for p in result.request.parameters if p.id == identifier), None)
                if result
                else None
            )
            if parameter is None:
                raise ValueError("A report template references a removed comparison coordinate")
            name, details = parameter.label or parameter.channel, parameter_details(parameter)
        else:
            item = objects[kind].get(identifier)
            if item is None:
                raise ValueError(f"A report template references a removed {kind.replace('_', ' ')}")
            name = object_name(kind, item)
            if kind == "population":
                ancestry = list(paths[identifier][1])
                details = {"gate_kind": item.kind}
        bindings.append(
            TemplateBinding(key=key, **entry, name=name, details=details, population_path=ancestry)
        )
    value = dict(
        format="cytoforge-report-template",
        version=1,
        source_workspace_id=workspace.id,
        definition=data,
        bindings=[binding.model_dump() for binding in bindings],
    )
    value["sha256"] = reports.digest(value)
    checked_template(ReportTemplate.model_validate(value))
    if len(json.dumps(value).encode()) > MAX_TEMPLATE_BYTES:
        raise ValueError("Report template exceeds eight MiB")
    return value


def checked_template(template):
    template = ReportTemplate.model_validate(template.model_dump())
    value = template.model_dump()
    expected = value.pop("sha256")
    if reports.digest(value) != expected:
        raise ValueError("Report template failed its SHA-256 integrity check")
    if len(json.dumps(value).encode()) > MAX_TEMPLATE_BYTES:
        raise ValueError("Report template exceeds eight MiB")
    if template.definition.plots:
        raise ValueError("Report templates require migrated positioned pages")
    bindings = {binding.key: binding for binding in template.bindings}
    if len(bindings) != len(template.bindings):
        raise ValueError("Report template repeats a binding")

    def owner(identifier):
        binding = bindings.get(binding_key("population", identifier))
        if binding is None:
            raise ValueError("Report template is missing a population binding")
        return binding.owner_id

    inventory = Inventory(template.definition.model_dump(), owner)
    if set(bindings) != set(inventory.bindings):
        raise ValueError(
            "Report template binding inventory is incomplete or contains extra objects"
        )
    for key, entry in inventory.bindings.items():
        binding = bindings[key]
        if any(getattr(binding, field) != value for field, value in entry.items()):
            raise ValueError("Report template binding ownership is inconsistent")
    return template, bindings, inventory


def parsed(content):
    if len(content) > MAX_TEMPLATE_BYTES:
        raise ValueError("Report template exceeds eight MiB")

    def unique_keys(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Report template contains duplicate JSON keys")
            value[key] = item
        return value

    try:
        value = json.loads(content, object_pairs_hook=unique_keys)
        template = ReportTemplate.model_validate(value)
    except (ValueError, RecursionError) as error:
        raise ValueError("Choose a valid CytoForge version-one report template") from error
    return checked_template(template)[0]


class Targets:
    def __init__(self, workspace):
        self.workspace = workspace
        self.paths = biology.population_paths(workspace)
        self.objects = {
            kind: {item.id: item for item in getattr(workspace, field)}
            for kind, field in COLLECTIONS.items()
        }
        self.pools = {}
        self.by_id = {}
        self.by_name = {}
        self.by_path = {}
        self.populations = {}
        for gate in workspace.gates:
            self.populations.setdefault(gate.sample_id, []).append(gate)

    def options(self, kind, owner=None):
        pool = f"{kind}/{owner}" if kind in PARENTS else kind
        if pool in self.pools:
            return pool, self.pools[pool]
        if kind == "channel":
            sample = self.objects["sample"].get(owner)
            values = (
                [dict(id=c.name, name=c.name, details={}) for c in sample.channels]
                if sample
                else []
            )
        elif kind == "population":
            values = [
                dict(
                    id=g.id,
                    name=g.name,
                    population_path=list(self.paths[g.id][1]),
                    details={"gate_kind": g.kind},
                )
                for g in self.populations.get(owner, [])
            ]
        elif kind == "column":
            table = self.objects["table"].get(owner)
            values = (
                [dict(id=c.id, name=c.name, details=column_details(c)) for c in table.columns]
                if table
                else []
            )
        elif kind == "comparison_parameter":
            result = self.objects["comparison"].get(owner)
            values = (
                [
                    dict(id=p.id, name=p.label or p.channel, details=parameter_details(p))
                    for p in result.request.parameters
                ]
                if result
                else []
            )
        else:
            values = [
                dict(id=item.id, name=object_name(kind, item), details={})
                for item in self.objects[kind].values()
            ]
        self.pools[pool] = values
        self.by_id[pool] = {value["id"]: value for value in values}
        self.by_name[pool] = {}
        self.by_path[pool] = {}
        for value in values:
            self.by_name[pool].setdefault(value["name"], []).append(value)
            if kind == "population":
                self.by_path[pool].setdefault(tuple(value["population_path"]), []).append(value)
        return pool, values


def updated_definition(template, identifier, name, references, choices, batch_scope, batch_samples):
    definition = deepcopy(template.definition.model_dump(mode="json"))
    text_updates = {}
    key_maps, iteration_maps = {}, {}
    # Scalar slots first; dictionary keys are rebuilt separately to avoid collisions.
    for reference in references:
        if reference.path[-1] == "@key":
            key_maps.setdefault(reference.path[1:3], {})[reference.path[3]] = choices[reference.key]
            continue
        if reference.path[-1] == "@iteration":
            iteration_maps[reference.path[1:3]] = choices[reference.key]
            continue
        current = definition
        for part in reference.path[:-1]:
            current = current[part]
        value = choices[reference.key]
        if reference.token is not None:
            metric = reference.token.partition(":")[0]
            text_updates.setdefault(reference.path, {})[reference.token] = metric + ":" + value
        else:
            current[reference.path[-1]] = value
    for path, replacements in text_updates.items():
        current = definition
        for part in path[:-1]:
            current = current[part]
        current[path[-1]] = re.sub(
            r"\{\{stat:([^{}]+)\}\}",
            lambda match, replacements=replacements: (
                "{{stat:" + replacements.get(match[1], match[1]) + "}}"
            ),
            current[path[-1]],
        )
    for field in ["overrides", "population_overrides"]:
        source = template.definition.batch.model_dump()[field]
        rebuilt = {}
        for iteration in source:
            remap_keys = key_maps.get((field, iteration), {})
            target_iteration = iteration_maps.get((field, iteration))
            destination = (
                iteration.partition(":")[0] + ":" + target_iteration
                if target_iteration is not None
                else iteration
            )
            if destination in rebuilt:
                raise ValueError("Template batch iterations collide after rebinding")
            mapped = {}
            for key, value in definition["batch"][field].get(iteration, {}).items():
                target = remap_keys.get(key, key)
                if target in mapped:
                    raise ValueError("Template batch overrides collide after rebinding")
                mapped[target] = value
            rebuilt[destination] = mapped
        definition["batch"][field] = rebuilt
    if batch_scope == "destination" and definition["batch"]["mode"] != "off":
        definition["batch"].update(sample_ids=batch_samples, overrides={}, population_overrides={})
    definition["id"], definition["name"] = identifier, name

    def fresh(old):
        return reports.digest({"import": identifier, "object": old})[:32]

    for element in definition["elements"]:
        element["id"] = fresh(element["id"])
        if element["group_id"]:
            element["group_id"] = fresh(element["group_id"])
        if plot := element.get("plot"):
            plot["id"] = fresh(plot["id"])
            for layer in plot["overlays"]:
                layer["id"] = fresh(layer["id"])
    return LayoutDefinition.model_validate(definition)


def preview(workspace, request, engine=None):
    # Numeric defaults must have the same typed representation before and after persistence.
    workspace = Workspace.model_validate(workspace.model_dump())
    if request.revision != workspace.revision:
        raise ConflictError("Workspace changed; review the report template again")
    if any(layout.id == request.id for layout in workspace.layouts):
        raise ValueError("This template import ID is already saved; start a new import")
    template, bindings, inventory = checked_template(request.template)
    if set(request.mappings) - bindings.keys():
        raise ValueError("Report template mappings contain unknown bindings")
    if len(set(request.batch_sample_ids)) != len(request.batch_sample_ids) or not set(
        request.batch_sample_ids
    ) <= {s.id for s in workspace.samples}:
        raise ValueError("Choose distinct destination batch acquisitions")
    resetting = request.batch_scope == "destination" and template.definition.batch.mode != "off"
    references = [
        r
        for r in inventory.references
        if not (
            resetting
            and r.path[:2]
            in {("batch", "sample_ids"), ("batch", "overrides"), ("batch", "population_overrides")}
        )
    ]
    text_channels = {reference.key for reference in references if reference.token is not None}
    override_keys = {reference.key for reference in references if reference.path[-1] == "@key"}
    backgate_keys = {
        reference.key for reference in references if reference.path[-1] == "backgate_id"
    }
    active = {r.key for r in references}
    for key in list(active):
        binding = bindings[key]
        if binding.kind in PARENTS:
            active.add(binding_key(PARENTS[binding.kind], binding.owner_id))
    targets, choices, rows, issues = Targets(workspace), {}, [], []
    # Parents resolve before their populations, channels and result/table coordinates.
    ordered = sorted(bindings.values(), key=lambda b: b.kind in PARENTS)
    for binding in ordered:
        if binding.key not in active:
            rows.append(
                dict(
                    **binding.model_dump(),
                    active=False,
                    target=None,
                    status="unused",
                    options_key=None,
                )
            )
            continue
        parent = (
            choices.get(binding_key(PARENTS[binding.kind], binding.owner_id))
            if binding.kind in PARENTS
            else None
        )
        pool, options = targets.options(binding.kind, parent)
        supplied = binding.key in request.mappings
        selected = request.mappings.get(binding.key)
        candidates = [
            option
            for option in targets.by_name[pool].get(binding.name, [])
            if (
                binding.kind not in {"column", "comparison_parameter"}
                or option["details"] == binding.details
            )
        ]
        if binding.kind == "population":
            candidates = targets.by_path[pool].get(tuple(binding.population_path), [])
        if not supplied:
            same = []
            if (
                template.source_workspace_id == workspace.id
                and binding.source_id in targets.by_id[pool]
            ):
                same = [targets.by_id[pool][binding.source_id]]
            candidates = same or candidates
            selected = candidates[0]["id"] if len(candidates) == 1 else None
        valid = selected in targets.by_id[pool]
        if supplied and selected is None and binding.kind == "population" and parent is not None:
            valid = binding.key not in override_keys | backgate_keys
        if (
            binding.kind == "channel"
            and selected is not None
            and binding.key in text_channels
            and any(c in selected for c in "{}")
        ):
            valid = False
        status = "mapped" if valid else "missing" if supplied or not candidates else "ambiguous"
        if valid:
            choices[binding.key] = selected
        else:
            issues.append(
                dict(
                    severity="error",
                    binding_key=binding.key,
                    message=(
                        f"Choose a destination {binding.kind.replace('_', ' ')} "
                        f"for {binding.name!r}"
                    ),
                )
            )
        rows.append(
            dict(
                **binding.model_dump(),
                active=True,
                target=selected,
                status=status,
                options_key=pool,
            )
        )
    candidate, prototype_plan, batch_plan = None, None, None
    if not issues:
        candidate = updated_definition(
            template,
            request.id,
            request.name,
            references,
            choices,
            request.batch_scope,
            request.batch_sample_ids,
        )
        candidate.template_origin = ReportTemplateOrigin(
            template_sha256=template.sha256,
            source_workspace_id=template.source_workspace_id,
            source_layout_id=template.definition.id,
            bindings=choices,
            batch_scope=request.batch_scope,
        )
        prototype = candidate.model_copy(deep=True)
        prototype.batch = ReportBatch()
        prototype_plan = reports.plan(workspace, prototype, engine)
        batch_plan = reports.plan(workspace, candidate, engine)
        issues.extend(prototype_plan["notices"])
        for iteration in prototype_plan["iterations"]:
            issues.extend(iteration["issues"])
        current_iterations = {iteration["key"] for iteration in batch_plan["iterations"]}
        unused_iterations = {
            key
            for field in (candidate.batch.overrides, candidate.batch.population_overrides)
            for key, values in field.items()
            if values and key not in current_iterations
        }
        for key in sorted(unused_iterations):
            issues.append(
                dict(
                    severity="warning",
                    message=(
                        f"Saved batch override {key!r} has no destination iteration. "
                        "It remains saved but will not affect this cohort; review the batch."
                    ),
                )
            )
    can_apply = candidate is not None and prototype_plan["exportable"]
    review_hash = reports.digest(
        dict(
            revision=workspace.revision,
            request=request.model_dump(exclude={"review_hash"}),
            choices=choices,
            prototype=prototype_plan["review_hash"] if prototype_plan else None,
            batch=batch_plan["review_hash"] if batch_plan else None,
        )
    )
    if request.review_hash is not None and request.review_hash != review_hash:
        raise ConflictError("Template bindings or report sources changed; review again")
    return dict(
        workspace_id=workspace.id,
        revision=workspace.revision,
        review_hash=review_hash,
        can_apply=can_apply,
        template_sha256=template.sha256,
        bindings=rows,
        targets=targets.pools,
        issues=issues,
        definition=candidate.model_dump(mode="json") if candidate else None,
        plan=batch_plan,
        resets_batch_bindings=resetting,
    )


def apply(store, workspace_id, request, engine):
    if request.review_hash is None:
        raise ValueError("Review report template bindings before importing")
    result = preview(store.get(workspace_id), request, engine)
    if not result["can_apply"]:
        raise ValueError("Resolve report template bindings before importing")
    definition = LayoutDefinition.model_validate(result["definition"])

    def change(workspace):
        workspace.layouts.append(definition)

    return store.mutate(workspace_id, "Import report template", change, request.revision)
