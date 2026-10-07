"""Reproducible report planning, explicit population mappings and page rendering."""

from __future__ import annotations

import hashlib
import json
import math
import re

from pydantic import Field

from . import (
    biology,
    cellcycle,
    kinetics,
    plates,
    population_comparison,
    population_comparison_views,
    proliferation,
    report_cache,
    report_plots,
    report_svg,
    report_tables,
)
from .models import ComparisonPresentation, LayoutDefinition, Model, ReportElement, ReportPage
from .plot_coordinates import resolve_dimension
from .report_graph_typography import (
    caption_descent,
    caption_figure,
    caption_lines,
    caption_style,
    styled_caption,
)
from .report_sources import SourceAudit
from .store import ConflictError


class ReportRequest(Model):
    revision: int = Field(ge=0)
    definition: LayoutDefinition
    page: int = Field(default=0, ge=0, le=65535)
    prototype_page: int | None = Field(default=None, ge=0, le=31)
    review_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    validate_sources: bool = False


class ReportExportRequest(ReportRequest):
    pages: list[int] = Field(default_factory=list, max_length=1024)
    format: str = Field(default="svg", pattern=r"^(svg|zip)$")


class PageProof(Model):
    page: int = Field(ge=0, le=65535)
    data_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    svg_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReportVerifyRequest(ReportRequest):
    proofs: list[PageProof] = Field(min_length=1, max_length=1024)


def checked_plan(workspace, request, engine=None):
    if request.revision != workspace.revision:
        raise ConflictError("Workspace changed; refresh this report before reviewing")
    if len(request.definition.model_dump_json().encode()) > 1024 * 1024:
        raise ValueError("Report definition exceeds one MiB")
    result = plan(workspace, request.definition, engine)
    if request.review_hash and request.review_hash != result["review_hash"]:
        raise ConflictError("Report sources or mappings changed; review the batch again")
    return result


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def migrated(layout):
    """Old grid reports become positioned pages without changing their data selections."""
    if not layout.plots:
        return LayoutDefinition.model_validate(layout.model_dump())
    pages, elements = [], []
    for index, plot in enumerate(layout.plots):
        page_index, position = divmod(index, 4)
        if position == 0:
            pages.append(ReportPage())
        elements.append(
            ReportElement(
                id=plot.id,
                kind="plot",
                page=page_index,
                x_mm=12 + (position % 2) * 96,
                y_mm=38 + (position // 2) * 111,
                width_mm=90,
                height_mm=101,
                title=plot.title,
                plot=plot,
            )
        )
    return LayoutDefinition.model_validate(
        {
            **layout.model_dump(),
            "plots": [],
            "pages": [page.model_dump() for page in pages],
            "elements": [element.model_dump() for element in elements],
        }
    )


def keyword(sample, name):
    if name.startswith("tag:"):
        return sample.tags.get(name[4:])
    if name.startswith("metadata:"):
        return sample.metadata.get(name[9:])
    return sample.tags.get(name, sample.metadata.get(name))


def sources(layout):
    result = []
    for element in layout.elements:
        if not element.iterate:
            continue
        if element.plot:
            for layer in [element.plot, *element.plot.overlays]:
                if not layer.locked_control and layer.sample_id not in result:
                    result.append(layer.sample_id)
        if (
            element.kind in {"biology", "population_comparison", "text"}
            and element.sample_id
            and element.sample_id not in result
        ):
            result.append(element.sample_id)
    return result


def issue(message, element_id=None, severity="error", **detail):
    return dict(message=str(message), element_id=element_id, severity=severity, **detail)


def population_map(workspace):
    paths = biology.population_paths(workspace)
    indexed = {}
    for identifier, path in paths.items():
        indexed.setdefault(path, []).append(identifier)
    return paths, indexed


def resolve_population(workspace, source_id, target_id, identifier, paths, indexed, overrides=None):
    if identifier is None:
        return None
    if identifier not in paths or paths[identifier][0] != source_id:
        raise ValueError("The source population is missing or belongs to another acquisition")
    if overrides and identifier in overrides:
        target = overrides[identifier]
        if target is not None and (target not in paths or paths[target][0] != target_id):
            raise ValueError(
                "The explicit population mapping is unavailable in the target acquisition"
            )
        return target
    if target_id == source_id:
        return identifier
    candidates = indexed.get((target_id, paths[identifier][1]), [])
    if len(candidates) != 1:
        description = " / ".join(paths[identifier][1])
        raise ValueError(
            f"Population path {description!r} is "
            f"{'missing' if not candidates else 'ambiguous'} in the target acquisition"
        )
    return candidates[0]


def resolved_layer(workspace, element, layer, iteration, paths, indexed):
    samples = {sample.id: sample for sample in workspace.samples}
    if layer.sample_id not in samples:
        raise ValueError("The source acquisition has been removed")
    target = (
        layer.sample_id
        if not element.iterate or layer.locked_control
        else iteration["mapping"].get(layer.sample_id)
    )
    if target not in samples:
        raise ValueError("Select an explicit target acquisition for this source")
    label, missing = substitute(
        layer.title if hasattr(layer, "title") else layer.label,
        workspace,
        iteration,
        samples[target],
    )
    if missing:
        raise ValueError("Missing legend annotation context: " + ", ".join(missing))
    result = dict(
        source_sample_id=layer.sample_id,
        source_gate_id=layer.gate_id,
        source_coordinate_gate_id=layer.coordinate_gate_id,
        sample_id=target,
        gate_id=resolve_population(
            workspace,
            layer.sample_id,
            target,
            layer.gate_id,
            paths,
            indexed,
            iteration.get("population_overrides")
            if element.iterate and not layer.locked_control
            else None,
        ),
        coordinate_gate_id=resolve_population(
            workspace,
            layer.sample_id,
            target,
            layer.coordinate_gate_id,
            paths,
            indexed,
            iteration.get("population_overrides")
            if element.iterate and not layer.locked_control
            else None,
        ),
        color=layer.color,
        label=label,
        locked_control=layer.locked_control or not element.iterate,
    )
    if layer.backgate_id is not None:
        backgate = resolve_population(
            workspace,
            layer.sample_id,
            target,
            layer.backgate_id,
            paths,
            indexed,
            iteration.get("population_overrides")
            if element.iterate and not layer.locked_control
            else None,
        )
        if backgate is None:
            raise ValueError("A backgate population cannot be mapped to all events")
        result.update(source_backgate_id=layer.backgate_id, backgate_id=backgate)
    return result


def plan(workspace, definition, engine=None):
    layout = migrated(definition)
    prototype_sources = sources(layout)
    samples = {sample.id: sample for sample in workspace.samples}
    scientific_key = report_cache.workspace_key(workspace, engine) if engine is not None else None
    batch = layout.batch
    notices = []
    if batch.group_id:
        group = next((group for group in workspace.groups if group.id == batch.group_id), None)
        if group is None:
            selected = []
            notices.append(issue("The saved batch group is unavailable"))
        else:
            selected = [
                samples[identifier] for identifier in group.sample_ids if identifier in samples
            ]
    else:
        selected = list(workspace.samples)
    if batch.sample_ids:
        selected = [
            samples[identifier]
            for identifier in batch.sample_ids
            if identifier in samples and identifier in {sample.id for sample in selected}
        ]
        if missing := set(batch.sample_ids) - samples.keys():
            notices.append(
                issue("Selected batch acquisitions are unavailable", sample_ids=sorted(missing))
            )
    iterations = []
    if batch.mode == "off":
        iterations.append(
            dict(
                key="off",
                label="Report",
                sample_ids=prototype_sources,
                mapping={identifier: identifier for identifier in prototype_sources},
                issues=[],
            )
        )
    else:
        groups = []
        if batch.mode == "sample":
            groups = [(f"sample:{sample.id}", sample.name, [sample]) for sample in selected]
        elif batch.mode == "panel":
            for start in range(0, len(selected), batch.panel_size):
                members = selected[start : start + batch.panel_size]
                groups.append(
                    (
                        f"panel:{start // batch.panel_size}",
                        " / ".join(sample.name for sample in members),
                        members,
                    )
                )
        else:
            by_value = {}
            for sample in selected:
                value = keyword(sample, batch.iterator_keyword)
                key = f"keyword:{value}" if value is not None else f"missing:{sample.id}"
                by_value.setdefault(key, [value, []])[1].append(sample)
            groups = [
                (key, value if value is not None else "Missing iterator", members)
                for key, (value, members) in by_value.items()
            ]
        for key, label, members in groups:
            mapping, problems = {}, []
            overrides = batch.overrides.get(key, {})
            for position, identifier in enumerate(prototype_sources):
                candidates = []
                if identifier in overrides:
                    target = overrides[identifier]
                    candidates = [sample for sample in members if sample.id == target]
                    if not candidates:
                        problems.append(
                            issue(
                                "A mapping override must belong to this iteration",
                                source_sample_id=identifier,
                            )
                        )
                elif identifier not in samples:
                    problems.append(
                        issue("A prototype acquisition is unavailable", source_sample_id=identifier)
                    )
                elif batch.mode == "sample":
                    candidates = members if position == 0 else []
                elif batch.mode == "panel":
                    candidates = members[position : position + 1]
                elif batch.discriminator_keyword:
                    value = keyword(samples[identifier], batch.discriminator_keyword)
                    if value is None:
                        problems.append(
                            issue(
                                "A prototype discriminator keyword is missing",
                                source_sample_id=identifier,
                            )
                        )
                    else:
                        candidates = [
                            sample
                            for sample in members
                            if keyword(sample, batch.discriminator_keyword) == value
                        ]
                else:
                    candidates = members
                target = candidates[0].id if len(candidates) == 1 else None
                mapping[identifier] = target
                if target is None:
                    problems.append(
                        issue(
                            "Target acquisition is missing or ambiguous; "
                            "choose a mapping explicitly",
                            source_sample_id=identifier,
                            candidates=[sample.id for sample in candidates],
                        )
                    )
            if batch.mode == "panel" and len(prototype_sources) != batch.panel_size:
                problems.append(
                    issue("Panel size must match the number of unlocked prototype acquisitions")
                )
            if batch.mode == "keyword" and any(
                keyword(sample, batch.iterator_keyword) is None for sample in members
            ):
                problems.append(issue("An iteration keyword is missing"))
            iterations.append(
                dict(
                    key=key,
                    label=label,
                    sample_ids=[sample.id for sample in members],
                    mapping=mapping,
                    issues=problems,
                )
            )
    if not iterations:
        notices.append(issue("The batch contains no target acquisitions"))
    paths, indexed = population_map(workspace)
    audit = SourceAudit(workspace)
    for iteration in iterations:
        iteration["population_overrides"] = batch.population_overrides.get(iteration["key"], {})
        bindings = []
        for element in layout.elements:
            if element.plot:
                for layer in [element.plot, *element.plot.overlays]:
                    try:
                        resolved = resolved_layer(
                            workspace, element, layer, iteration, paths, indexed
                        )
                        target = samples[resolved["sample_id"]]
                        plot = element.plot
                        parameters = [(plot.x, plot.x_dimension, 0), (plot.y, plot.y_dimension, 1)]
                        if element.plot.mode == "3d":
                            view = plot.three_d
                            parameters.extend(
                                [
                                    (view.z, view.z_dimension, 2),
                                    (view.color_by, view.color_dimension, None),
                                    (view.size_by, view.size_dimension, None),
                                ]
                            )
                        coordinate = next(
                            (
                                gate
                                for gate in workspace.gates
                                if gate.id == resolved["coordinate_gate_id"]
                            ),
                            None,
                        )
                        dimensions = [
                            resolve_dimension(workspace, target, name, coordinate, axis, explicit)
                            for name, explicit, axis in parameters
                            if name
                        ]
                        needed = {
                            name
                            for dim in dimensions
                            for name in dim.ratio_channels or (dim.channel,)
                        }
                        if any(audit.parameter_stale(target.id, name) for name in needed) or any(
                            audit.population_stale(resolved.get(key))
                            for key in ("gate_id", "coordinate_gate_id", "backgate_id")
                        ):
                            iteration["issues"].append(
                                issue(
                                    "This live plot uses historical model outputs",
                                    element.id,
                                    "stale",
                                )
                            )
                        bindings.append(dict(element_id=element.id, **resolved))
                    except (ValueError, KeyError) as error:
                        target_id = (
                            layer.sample_id
                            if layer.locked_control or not element.iterate
                            else iteration["mapping"].get(layer.sample_id)
                        )
                        bindings.append(
                            dict(
                                element_id=element.id,
                                source_sample_id=layer.sample_id,
                                source_gate_id=layer.gate_id,
                                source_coordinate_gate_id=layer.coordinate_gate_id,
                                **(
                                    {"source_backgate_id": layer.backgate_id}
                                    if layer.backgate_id is not None
                                    else {}
                                ),
                                sample_id=target_id,
                                gate_id=None,
                                locked_control=layer.locked_control or not element.iterate,
                                resolution_error=str(error),
                            )
                        )
                        iteration["issues"].append(
                            issue(error, element.id, source_sample_id=layer.sample_id)
                        )
            elif element.kind == "biology":
                target = (
                    element.sample_id
                    if not element.iterate
                    else iteration["mapping"].get(element.sample_id)
                )
                try:
                    result, fit, stale = biological_source(workspace, element, target)
                    bindings.append(
                        dict(
                            element_id=element.id,
                            sample_id=target,
                            result_id=result.id,
                            stale=stale,
                        )
                    )
                    if stale:
                        iteration["issues"].append(
                            issue(
                                "Saved biological figure is a historical snapshot",
                                element.id,
                                "stale",
                            )
                        )
                except (ValueError, KeyError) as error:
                    iteration["issues"].append(issue(error, element.id))
            elif element.kind == "population_comparison":
                target = (
                    element.sample_id
                    if not element.iterate
                    else iteration["mapping"].get(element.sample_id)
                )
                try:
                    result, target_index, row, stale = comparison_source(
                        workspace, element, target, iteration
                    )
                    if engine is not None:
                        population_comparison.load_artifact(engine.store, workspace.id, result)
                    bindings.append(
                        dict(
                            element_id=element.id,
                            sample_id=target,
                            gate_id=row.source.gate_id,
                            result_id=result.id,
                            parameter_id=element.comparison_parameter_id,
                            target_index=target_index,
                            stale=stale,
                        )
                    )
                    if stale:
                        iteration["issues"].append(
                            issue(
                                "Saved comparison figure is a historical snapshot",
                                element.id,
                                "stale",
                            )
                        )
                except (ValueError, KeyError, OSError) as error:
                    iteration["issues"].append(issue(error, element.id))
            elif element.kind == "table":
                try:
                    report_tables.scoped_definition(workspace, element, iteration, layout)
                    if engine is None and element.auto_paginate:
                        raise ValueError(
                            "Automatic table pagination requires the scientific engine"
                        )
                    if engine is not None:
                        data = report_tables.dataset(
                            workspace, engine, element, iteration, layout, scientific_key
                        )
                        target = (
                            element.sample_id
                            if not element.iterate
                            else iteration["mapping"].get(element.sample_id)
                        )
                        title, _ = substitute(
                            element.title, workspace, iteration, samples.get(target)
                        )
                        paging = report_tables.pagination(
                            data, element, element.height_mm - caption_height(title, element)
                        )
                        iteration.setdefault("tables", {})[element.id] = paging
                        if any(paging["unused_geometry"].values()):
                            iteration["issues"].append(
                                issue(
                                    "Some table formatting positions are outside this result; "
                                    "review the destination row and column formatting",
                                    element.id,
                                    "warning",
                                )
                            )
                        iteration["issues"].extend(
                            issue(
                                status["message"],
                                element.id,
                                report_tables.severity(status["message"]),
                                affected_rows=status["affected_rows"],
                                column_id=status["column_id"],
                            )
                            for status in data["input_status"]
                        )
                except (ValueError, KeyError, OSError) as error:
                    iteration["issues"].append(issue(error, element.id))
            elif element.kind == "plate":
                if not any(plate.id == element.plate_id for plate in workspace.plates):
                    iteration["issues"].append(issue("The saved plate is unavailable", element.id))
                elif batch.mode != "off" and element.iterate:
                    iteration["issues"].append(
                        issue(
                            "A whole-plate figure must be fixed across batch iterations", element.id
                        )
                    )
        iteration["bindings"] = bindings
    capacity = batch.tile_columns * batch.tile_rows if batch.mode != "off" else 1
    output_pages = []
    for batch_index in range(math.ceil(len(iterations) / capacity)):
        members = iterations[batch_index * capacity : (batch_index + 1) * capacity]
        for page_index in range(len(layout.pages)):
            continuations = max(
                [
                    paging["segments"]
                    for iteration in members
                    for element in layout.elements
                    if element.page == page_index
                    and element.kind == "table"
                    and element.auto_paginate
                    if (paging := iteration.get("tables", {}).get(element.id))
                ],
                default=1,
            )
            if len(output_pages) + continuations > 1024:
                raise ValueError(
                    "Report exceeds 1024 output pages; reduce the selected scope "
                    "or enlarge the table panels"
                )
            output_pages.extend(
                dict(
                    prototype_page=page_index,
                    batch_index=batch_index,
                    continuation=index,
                    continuation_count=continuations,
                )
                for index in range(continuations)
            )
    page_count = len(output_pages)
    result = dict(
        version=2,
        workspace_id=workspace.id,
        revision=workspace.revision,
        layout_id=layout.id,
        prototype_sources=prototype_sources,
        iterations=iterations,
        notices=notices,
        page_count=page_count,
        output_pages=output_pages,
        prototype_pages=[page.model_dump() for page in layout.pages],
        definition=layout.model_dump(),
    )
    result["review_hash"] = digest(
        dict(workspace_revision=workspace.revision, definition=layout.model_dump(), plan=result)
    )
    result["exportable"] = exportable(
        layout, notices + [problem for iteration in iterations for problem in iteration["issues"]]
    )
    return result


def comparison_source(workspace, element, target, iteration):
    result = population_comparison.saved_result(
        workspace, element.result_id, element.follow_replacement
    )
    if result is None:
        raise ValueError("The saved population comparison is unavailable")
    if target is None:
        raise ValueError("A comparison figure has no mapped acquisition")
    if not any(
        s.sample_id == element.sample_id and s.gate_id == element.gate_id
        for s in result.request.inputs
    ):
        raise ValueError("The source population was not compared by this result")
    identifier = element.gate_id
    if target != element.sample_id:
        paths, indexed = population_map(workspace)
        identifier = resolve_population(
            workspace,
            element.sample_id,
            target,
            identifier,
            paths,
            indexed,
            iteration.get("population_overrides") if element.iterate else None,
        )
    index = next(
        (
            i
            for i, s in enumerate(result.request.inputs)
            if s.sample_id == target and s.gate_id == identifier
        ),
        None,
    )
    if index is None:
        raise ValueError("This exact mapped acquisition and population was not compared")
    row = next(
        (
            r
            for r in result.rows
            if r.role == "target"
            and r.source.sample_id == target
            and r.source.gate_id == identifier
            and r.parameter_id == element.comparison_parameter_id
        ),
        None,
    )
    if row is None:
        raise ValueError("The selected parameter does not belong to this comparison")
    if row.status == "unavailable":
        raise ValueError(row.error)
    return result, index, row, population_comparison.is_stale(workspace, result)


def biological_source(workspace, element, target):
    if target is None:
        raise ValueError("A biological figure has no mapped acquisition")
    result = biology.saved_result(workspace, element.platform, element.result_id)
    sample = next((sample for sample in workspace.samples if sample.id == target), None)
    if element.follow_replacement and sample is not None:
        owners = {
            parameter.analysis_id
            for parameter in sample.computed_parameters
            if parameter.name in result.columns
        }
        if len(owners) == 1:
            active = next(
                (
                    value
                    for value in getattr(workspace, biology.PLATFORM_FIELDS[element.platform])
                    if value.id == next(iter(owners))
                ),
                None,
            )
            if active and active.columns == result.columns:
                result = active
    fit = next((fit for fit in result.fits if fit.sample_id == target), None)
    if fit is None:
        raise ValueError("This acquisition was not fitted by the selected model")
    module = {"cell-cycle": cellcycle, "proliferation": proliferation, "kinetics": kinetics}[
        element.platform
    ]
    return result, fit, module.is_stale(workspace, result)


def substitute(value, workspace, iteration, sample=None, population_name=None):
    target_id = next(iter(iteration["mapping"].values()), None)
    target = sample or next(
        (sample for sample in workspace.samples if sample.id == target_id), None
    )
    missing = []

    def replace(match):
        key = match[1].strip()
        if key == "workspace":
            return workspace.name
        if key == "iteration":
            return str(iteration["label"])
        if key == "population" and population_name is not None:
            return population_name
        if key == "sample":
            if target:
                return target.name
        elif key.startswith("keyword:") and target:
            result = keyword(target, key[8:])
            if result is not None:
                return str(result)
        missing.append(key)
        return f"[Missing {key}]"

    return re.sub(r"\{\{([^{}]+)\}\}", replace, value), missing


def formatted(value, decimals=2):
    if value is None:
        return "Undefined"
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            return "Undefined"
        if abs(value) >= 1e10 or (value and abs(value) < 10 ** (-decimals - 3)):
            return format(value, ".6g")
        return format(value, f",.{decimals}f")
    return str(value)


def caption_height(title, element):
    if not title or element.kind in {"shape", "text"}:
        return 0
    style = caption_style(element)
    font = style["size"] * 25.4 / 72
    rows = (
        caption_lines(title, element.width_mm, style)
        if styled_caption(element)
        else report_svg.lines(title, element.width_mm, font, style["family"])
    )
    return min(
        element.height_mm / 3,
        min(2, len(rows)) * font * element.line_spacing
        + (caption_descent(rows[:2], style) if styled_caption(element) else 0)
        + 1,
    )


def output_index(request, report_plan):
    if request.prototype_page is None:
        return request.page
    for index, descriptor in enumerate(report_plan["output_pages"]):
        if (
            descriptor["prototype_page"] == request.prototype_page
            and descriptor["batch_index"] == 0
        ):
            return index
    raise ValueError("This report prototype page does not exist")


def statistic_text(value, workspace, engine, element, iteration):
    measured = {}

    def replace(match):
        metric, _, channel = match[1].partition(":")
        source = element.sample_id or next(iter(iteration["mapping"]), None)
        target = (
            source
            if not element.iterate or iteration["key"] == "off"
            else iteration["mapping"].get(source)
        )
        if not target:
            raise ValueError("Select an acquisition context for live statistics")
        sample = engine.sample(workspace, target)
        paths, indexed = population_map(workspace)
        population = resolve_population(
            workspace,
            source,
            target,
            element.gate_id,
            paths,
            indexed,
            iteration.get("population_overrides")
            if element.iterate and iteration["key"] != "off"
            else None,
        )
        if metric in {"count", "percent_parent", "percent_total"}:
            count = int(engine.mask(workspace, sample, population).sum())
            if metric == "count":
                result = count
            else:
                gate = next((gate for gate in workspace.gates if gate.id == population), None)
                denominator = (
                    sample.event_count
                    if metric == "percent_total"
                    else int(engine.mask(workspace, sample, gate.parent_id if gate else None).sum())
                )
                result = 100 * count / denominator if denominator else None
        else:
            if (
                metric
                not in {
                    "mean",
                    "median",
                    "std",
                    "cv",
                    "robust_cv",
                    "geometric_mean",
                    "finite_count",
                    "min",
                    "max",
                }
                or not channel
            ):
                raise ValueError(
                    "Unknown report statistic; specify a supported statistic and channel"
                )
            result = engine.summary(
                workspace, sample, population, channel, element.compensated
            ).get(metric)
        measured[match[1]] = dict(
            sample_id=target,
            gate_id=population,
            metric=metric,
            channel=channel or None,
            value=result,
            compensated=element.compensated,
            source=SourceAudit(workspace).closure(
                target, (channel,) if channel else (), (population,)
            ),
        )
        return formatted(result, 0 if metric in {"count", "finite_count"} else 2)

    return re.sub(r"\{\{stat:([^{}]+)\}\}", replace, value), measured


def content(workspace, engine, element, iteration, layout):
    width, height = element.width_mm, element.height_mm
    svg = report_svg.node(
        "svg", viewBox=f"0 0 {width} {height}", width=width, height=height, overflow="hidden"
    )
    problems = []
    source_id = element.plot.sample_id if element.plot else element.sample_id
    target_id = source_id if not element.iterate else iteration["mapping"].get(source_id)
    if element.plot and element.plot.locked_control:
        target_id = source_id
    target_sample = next((sample for sample in workspace.samples if sample.id == target_id), None)
    population_name = None
    if element.plot:
        paths, indexed = population_map(workspace)
        primary = resolved_layer(workspace, element, element.plot, iteration, paths, indexed)
        population = next((gate for gate in workspace.gates if gate.id == primary["gate_id"]), None)
        population_name = population.name if population else "All events"
    elif element.kind == "population_comparison":
        _, _, comparison_row, _ = comparison_source(workspace, element, target_id, iteration)
        population_name = comparison_row.population_name
    title, missing = substitute(element.title, workspace, iteration, target_sample, population_name)
    problems.extend(issue(f"Missing annotation context: {key}", element.id) for key in missing)
    top = 0
    if title and element.kind not in {"shape", "text"}:
        style = caption_style(element)
        font = style["size"] * 25.4 / 72
        top = caption_height(title, element)
        if styled_caption(element):
            figure, omitted = caption_figure(title, element, width, top)
            report_svg.embed(svg, figure, width, top, f"caption-{element.id}")
        else:
            omitted = report_svg.paragraph(
                svg,
                title,
                width,
                top,
                font,
                style["color"],
                element.align,
                style["family"],
                style["weight"],
                style=style["style"],
                decoration=element.text_decoration,
                line_spacing=element.line_spacing,
            )
        if omitted:
            problems.append(
                issue("The figure caption is abbreviated by its panel size", element.id, "warning")
            )
    body_height = height - top
    group = report_svg.node("g", svg, transform=f"translate(0 {top})")
    if element.kind == "plot":
        paths, indexed = population_map(workspace)
        layers = [
            resolved_layer(workspace, element, layer, iteration, paths, indexed)
            for layer in [element.plot, *element.plot.overlays]
        ]
        figure, provenance = report_plots.figure(
            workspace, engine, element.plot, layers, width, body_height
        )
        inner = report_svg.embed(
            group,
            figure,
            width,
            body_height,
            f"figure-{element.id}-{digest(iteration['key'])[:16]}",
        )
        inner.set("role", "img")
        inner.set(
            "aria-label",
            f"{element.plot.mode} plot of {element.plot.x}"
            + (f" versus {element.plot.y}" if element.plot.y else "")
            + (f" versus {element.plot.three_d.z}" if element.plot.mode == "3d" else ""),
        )
        inner.set("data-ready", "true")
        for layer in provenance["layers"]:
            if layer["source"]["stale"]:
                problems.append(
                    issue("This plot uses historical model outputs", element.id, "stale")
                )
            if layer["undefined_reason"]:
                problems.append(issue(layer["undefined_reason"], element.id, "warning"))
    elif element.kind == "biology":
        target = (
            element.sample_id
            if not element.iterate
            else iteration["mapping"].get(element.sample_id)
        )
        result, fit, stale = biological_source(workspace, element, target)
        module = {"cell-cycle": cellcycle, "proliferation": proliferation, "kinetics": kinetics}[
            element.platform
        ]
        data = next(item for item in result.data if item.sample_id == target)
        module.load_data(engine.store, workspace.id, result, data)
        sample = next((sample for sample in workspace.samples if sample.id == target), None)
        figure = module.figure_svg(
            result, fit, sample.name if sample else "Removed acquisition", stale
        )
        report_svg.embed(
            group,
            figure,
            width,
            body_height,
            f"model-{element.id}-{digest(iteration['key'])[:16]}",
            light=True,
        )
        provenance = dict(
            kind="biology",
            platform=element.platform,
            result_id=result.id,
            sample_id=target,
            input_hash=result.input_hash,
            data=data.model_dump(),
            fit=fit.model_dump(),
            stale=stale,
        )
        if stale:
            problems.append(
                issue("This figure preserves a historical biological snapshot", element.id, "stale")
            )
    elif element.kind == "population_comparison":
        result, target_index, row, stale = comparison_source(
            workspace, element, target_id, iteration
        )
        data = population_comparison_views.plot_data(
            engine.store, workspace, result, element.comparison_parameter_id, target_index
        )
        view = element.comparison_view or ComparisonPresentation()
        figure = population_comparison_views.figure_svg(
            data, result.request.name, presentation=view
        )
        report_svg.embed(
            group,
            figure,
            width,
            body_height,
            f"comparison-{element.id}-{digest(iteration['key'])[:16]}",
        )
        provenance = dict(
            kind="population_comparison",
            result_id=result.id,
            input_hash=result.input_hash,
            sample_id=target_id,
            gate_id=row.source.gate_id,
            parameter_id=element.comparison_parameter_id,
            target_index=target_index,
            data=result.data.model_dump(),
            row=row.model_dump(),
            input_snapshot=result.input_snapshot,
            presentation=view.model_dump(),
            stale=stale,
        )
        if stale:
            problems.append(
                issue("This figure preserves a historical comparison snapshot", element.id, "stale")
            )
    elif element.kind == "table":
        figure, provenance, notices = report_tables.panel(
            workspace, engine, element, iteration, layout, width, body_height
        )
        report_svg.embed(
            group, figure, width, body_height, f"table-{element.id}-{iteration['key']}"
        )
        problems.extend(notices)
    elif element.kind == "plate":
        plate = next((plate for plate in workspace.plates if plate.id == element.plate_id), None)
        if plate is None:
            raise ValueError("The saved plate is unavailable")
        if layout.batch.mode != "off" and element.iterate:
            raise ValueError("Fix whole-plate figures across batch iterations")
        values = plates.evaluate(workspace, engine, plate)
        report_svg.embed(
            group,
            plates.svg(values),
            width,
            body_height,
            f"plate-{element.id}-{iteration['key']}",
            light=True,
        )
        provenance = dict(kind="plate", plate_id=plate.id, data=values)
        for well in values["wells"]:
            for status in well.get("status", {}).values():
                if status and any(
                    word in str(status).lower() for word in ("missing", "unavailable", "stale")
                ):
                    problems.append(
                        issue(
                            str(status),
                            element.id,
                            "stale" if "stale" in str(status).lower() else "error",
                            well=well["well"],
                        )
                    )
    elif element.kind == "text":
        value, measurements = statistic_text(element.text, workspace, engine, element, iteration)
        sample_id = (
            element.sample_id
            if not element.iterate
            else iteration["mapping"].get(element.sample_id)
        )
        sample = next((sample for sample in workspace.samples if sample.id == sample_id), None)
        value, missing = substitute(value, workspace, iteration, sample)
        omitted = report_svg.paragraph(
            group,
            value,
            width,
            body_height,
            element.font_size_pt * 25.4 / 72,
            element.color,
            element.align,
            element.font_family,
            element.font_weight,
            style=element.font_style,
            decoration=element.text_decoration,
            line_spacing=element.line_spacing,
        )
        problems.extend(issue(f"Missing annotation context: {key}", element.id) for key in missing)
        if omitted:
            problems.append(
                issue(f"{omitted} annotation lines do not fit this panel", element.id, "warning")
            )
        provenance = dict(kind="text", text=value, omitted_lines=omitted, statistics=measurements)
        for measurement in measurements.values():
            if measurement["source"]["stale"]:
                problems.append(
                    issue("This annotation uses historical model outputs", element.id, "stale")
                )
    else:
        if element.shape == "rectangle":
            report_svg.node(
                "rect",
                group,
                x=element.stroke_width_mm / 2,
                y=element.stroke_width_mm / 2,
                width=max(0, width - element.stroke_width_mm),
                height=max(0, body_height - element.stroke_width_mm),
                fill=element.fill,
                stroke=element.stroke,
                stroke_width=element.stroke_width_mm,
            )
        elif element.shape == "ellipse":
            report_svg.node(
                "ellipse",
                group,
                cx=width / 2,
                cy=body_height / 2,
                rx=max(0, (width - element.stroke_width_mm) / 2),
                ry=max(0, (body_height - element.stroke_width_mm) / 2),
                fill=element.fill,
                stroke=element.stroke,
                stroke_width=element.stroke_width_mm,
            )
        else:
            marker = None
            if element.shape == "arrow":
                definitions = report_svg.node("defs", group)
                identifier = f"arrow-{element.id}"
                arrow = report_svg.node(
                    "marker",
                    definitions,
                    id=identifier,
                    viewBox="0 0 10 10",
                    refX=10,
                    refY=5,
                    markerWidth=8,
                    markerHeight=8,
                    orient="auto-start-reverse",
                )
                report_svg.node("path", arrow, d="M 0 0 L 10 5 L 0 10 Z", fill=element.stroke)
                marker = f"url(#{identifier})"
            report_svg.node(
                "line",
                group,
                x1=element.stroke_width_mm / 2,
                y1=body_height / 2,
                x2=width - max(1, element.stroke_width_mm * 8),
                y2=body_height / 2,
                stroke=element.stroke,
                stroke_width=element.stroke_width_mm,
                marker_end=marker,
            )
        provenance = dict(kind="shape", shape=element.shape)
    provenance.update(element_id=element.id, title=title, issues=problems)
    return svg, provenance, problems


def exportable(layout, problems):
    return not any(
        problem["severity"] == "error"
        and layout.export_policy != "placeholders"
        or problem["severity"] == "stale"
        and layout.export_policy == "current"
        for problem in problems
    )


def render(workspace, engine, request, prepared_plan=None):
    if request.revision != workspace.revision:
        raise ConflictError("Workspace changed; refresh this report before rendering")
    layout = migrated(request.definition)
    report_plan = prepared_plan or checked_plan(workspace, request, engine)
    if request.review_hash is not None and request.review_hash != report_plan["review_hash"]:
        raise ConflictError("Report sources or mappings changed; review the batch again")
    output_page = output_index(request, report_plan)
    if output_page >= report_plan["page_count"]:
        raise ValueError("This report page does not exist")
    descriptor = report_plan["output_pages"][output_page]
    page_index = descriptor["prototype_page"]
    batch_index = descriptor["batch_index"]
    page = layout.pages[page_index]
    columns = layout.batch.tile_columns if layout.batch.mode != "off" else 1
    rows = layout.batch.tile_rows if layout.batch.mode != "off" else 1
    capacity = columns * rows
    iterations = report_plan["iterations"][batch_index * capacity : (batch_index + 1) * capacity]
    svg = report_svg.node(
        "svg",
        width=f"{page.width_mm}mm",
        height=f"{page.height_mm}mm",
        viewBox=f"0 0 {page.width_mm} {page.height_mm}",
        role="img",
        aria_label=f"{layout.name}, page {output_page + 1}",
    )
    report_svg.node("rect", svg, width=page.width_mm, height=page.height_mm, fill=page.background)
    problems = [*report_plan["notices"]]
    scientific_key = report_cache.workspace_key(workspace, engine)
    source_audit = SourceAudit(workspace)
    frames = []
    if layout.show_header:
        report_svg.label(svg, page.margin_mm, page.margin_mm + 4, layout.name, 5, font_weight=700)
        report_svg.label(
            svg,
            page.margin_mm,
            page.margin_mm + 10,
            f"{workspace.name} · revision {workspace.revision}",
            2.6,
            "#52677e",
        )
        if layout.description:
            group = report_svg.node(
                "g", svg, transform=f"translate({page.margin_mm} {page.margin_mm + 12})"
            )
            omitted = report_svg.paragraph(
                group, layout.description, page.width_mm - 2 * page.margin_mm, 8, 2.5, "#52677e"
            )
            if omitted:
                problems.append(
                    issue(
                        "The report description is abbreviated in the page header",
                        severity="warning",
                    )
                )
    for tile_index, iteration in enumerate(iterations):
        problems.extend(iteration["issues"])
        if capacity == 1:
            offset_x = offset_y = 0
            scale = 1
        else:
            column = tile_index % columns if layout.batch.order == "across" else tile_index // rows
            row = tile_index // columns if layout.batch.order == "across" else tile_index % rows
            tile_width = (page.width_mm - 2 * page.margin_mm) / columns
            tile_height = (page.height_mm - 2 * page.margin_mm - 28) / rows
            if tile_height <= 0:
                raise ValueError("This page is too short for the requested batch tiles")
            scale = min(tile_width / page.width_mm, (tile_height - 5) / page.height_mm)
            if scale <= 0:
                raise ValueError("Batch tile geometry has no printable content area")
            offset_x = page.margin_mm + column * tile_width
            offset_y = page.margin_mm + 26 + row * tile_height
            report_svg.label(
                svg, offset_x + 1, offset_y + 3, iteration["label"], 2.4, font_weight=600
            )
            offset_y += 5
        group = report_svg.node(
            "g", svg, transform=f"translate({offset_x} {offset_y}) scale({scale})"
        )
        if capacity == 1 and layout.batch.mode != "off":
            report_svg.label(group, page.margin_mm, 32, iteration["label"], 3, font_weight=600)
        for element in [element for element in layout.elements if element.page == page_index]:
            paging = iteration.get("tables", {}).get(element.id)
            if element.kind == "table" and element.auto_paginate and paging:
                element = report_tables.window(element, paging, descriptor["continuation"])
                if element is None:
                    continue
            frame = report_svg.node(
                "g",
                group,
                data_element_id=element.id,
                transform=f"translate({element.x_mm} {element.y_mm}) "
                f"rotate({element.rotation} {element.width_mm / 2} {element.height_mm / 2})",
                opacity=element.opacity,
            )
            try:
                cache_key = report_cache.panel_key(scientific_key, element, iteration, layout)
                cached = report_cache.PANELS.get(cache_key)
                if cached is None:
                    cached = report_cache.PANELS.put(
                        cache_key, *content(workspace, engine, element, iteration, layout)
                    )
                figure, provenance, notices = cached
                if request.validate_sources:
                    source_audit.validate_frame(engine, provenance)
                problems.extend(notices)
            except (ValueError, KeyError, OSError) as error:
                notices = [issue(error, element.id)]
                problems.extend(notices)
                figure = report_svg.placeholder(element.width_mm, element.height_mm, str(error))
                provenance = dict(
                    element_id=element.id, kind=element.kind, issues=notices, unavailable=True
                )
            report_svg.embed(
                frame,
                figure,
                element.width_mm,
                element.height_mm,
                f"panel-{element.id}-{tile_index}",
            )
            provenance["iteration"] = iteration["key"]
            if paging:
                provenance["pagination"] = paging
            provenance["geometry"] = dict(
                page=element.page,
                x_mm=element.x_mm,
                y_mm=element.y_mm,
                width_mm=element.width_mm,
                height_mm=element.height_mm,
                rotation=element.rotation,
                tile_scale=scale,
            )
            frames.append(provenance)
    from . import __version__

    manifest = dict(
        version=2,
        software=dict(cytoforge=__version__),
        workspace_id=workspace.id,
        revision=workspace.revision,
        layout_id=layout.id,
        definition=layout.model_dump(),
        page=output_page,
        output_page=descriptor,
        page_geometry=page.model_dump(),
        review_hash=report_plan["review_hash"],
        iterations=[iteration["key"] for iteration in iterations],
        elements=frames,
        issues=problems,
    )
    manifest["data_hash"] = digest(manifest)
    serious = [problem for problem in problems if problem["severity"] in {"error", "stale"}]
    if serious:
        report_svg.node(
            "rect",
            svg,
            x=page.margin_mm,
            y=page.height_mm - 13,
            width=page.width_mm - 2 * page.margin_mm,
            height=5,
            fill="#fce7df",
        )
        report_svg.label(
            svg,
            page.margin_mm + 1,
            page.height_mm - 9.5,
            f"{len(serious)} source issues · policy: {layout.export_policy} · see manifest",
            2.5,
            "#9a352f",
        )
    if layout.show_footer:
        report_svg.label(
            svg,
            page.margin_mm,
            page.height_mm - max(2, page.margin_mm / 2),
            f"CytoForge · {manifest['data_hash'][:16]} "
            f"· page {output_page + 1}/{report_plan['page_count']}",
            2.2,
            "#52677e",
        )
    report_svg.metadata(svg, manifest)
    payload = report_svg.serialize(svg)
    manifest["svg_sha256"] = hashlib.sha256(payload.encode()).hexdigest()
    return dict(
        workspace_id=workspace.id,
        revision=workspace.revision,
        review_hash=report_plan["review_hash"],
        page=output_page,
        page_count=report_plan["page_count"],
        geometry=page.model_dump(),
        svg=payload,
        manifest=manifest,
        issues=problems,
        exportable=exportable(layout, problems),
    )
