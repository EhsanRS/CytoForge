"""Reproducible, population-mapped statistics tables, pivots and comparisons."""

import csv
import hashlib
import io
import json
import math
import platform
import warnings
from functools import cache, lru_cache

import numpy as np
import scipy
import xlsxwriter
from scipy import stats

from . import (
    __version__,
    analysis,
    biology,
    cellcycle,
    kinetics,
    population_comparison,
    proliferation,
    quality,
)
from .formulas import parse
from .models import TableColumn
from .table_expressions import evaluate_table_formula

MAX_ROWS = 50000
MAX_CELLS = 2_000_000
MAX_EXCEL_ROWS = 1_048_576


def finite(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, str):
        return value
    value = float(value)
    return value if math.isfinite(value) else None


def numeric_column(column):
    return column.kind != "metadata" or column.metadata_numeric


def expanded_definition(definition):
    if definition.columns:
        return definition
    fields = [("count", "Events"), ("percent_parent", "% Parent"), ("percent_total", "% Total")]
    if definition.channel:
        fields += [
            (k, k.replace("_", " ").title()) for k in ("median", "mean", "robust_cv", "min", "max")
        ]
    result = definition.model_copy(deep=True)
    result.columns = [
        TableColumn(
            id=hashlib.sha256(f"{definition.id}:{key}".encode()).hexdigest()[:32],
            name=label,
            statistic=key,
            channel=definition.channel,
            decimals=0 if key == "count" else 2,
        )
        for key, label in fields
    ]
    return result


def evaluate_table(workspace, engine, definition, offset=0, limit=2000):
    definition = expanded_definition(definition)
    columns = {c.id: c for c in definition.columns}
    if definition.pivot:
        if any(not numeric_column(columns[i]) for i in definition.pivot.measures):
            raise ValueError("Pivot measures require numeric columns")
    if definition.comparison:
        if any(not numeric_column(columns[i]) for i in definition.comparison.measures):
            raise ValueError("Comparison measures require numeric columns")
        if any(columns[i].control_sample_id for i in definition.comparison.measures):
            raise ValueError("Control-valued columns are not independent sample observations")
    samples = {s.id: s for s in workspace.samples}
    gates = {g.id: g for g in workspace.gates}
    notices = []
    selected = set(samples)
    if definition.group_id:
        group = next((g for g in workspace.groups if g.id == definition.group_id), None)
        if group is None:
            selected = set()
            notices.append("The saved sample group is unavailable; no samples were substituted.")
        else:
            selected &= set(group.sample_ids)
    if definition.sample_ids:
        missing = set(definition.sample_ids) - set(samples)
        if missing:
            notices.append(f"{len(missing)} selected samples are unavailable.")
        selected &= set(definition.sample_ids)

    @cache
    def gate_path(identifier):
        if identifier is None:
            return ()
        gate = gates[identifier]
        return (*gate_path(gate.parent_id), gate.name)

    by_path = {}
    for gate in workspace.gates:
        by_path.setdefault((gate.sample_id, gate_path(gate.id)), []).append(gate.id)

    def mapped_gate(sample_id, column, context):
        if sample_id in column.population_overrides:
            identifier = column.population_overrides[sample_id]
            if identifier and (identifier not in gates or gates[identifier].sample_id != sample_id):
                return None, "Mapped population is unavailable or belongs to another sample"
            return identifier, None
        if sample_id in column.population_unavailable:
            return None, "Source population was not converted for this sample; map it explicitly"
        path = tuple(column.population_path) if column.population_path is not None else context
        if not path:
            return None, None
        matches = by_path.get((sample_id, path), [])
        if len(matches) != 1:
            return (
                None,
                "Population path is missing"
                if not matches
                else "Population path is ambiguous; map this sample explicitly",
            )
        return matches[0], None

    rows = []
    ordered_samples = workspace.samples
    if definition.sample_order == "selection" and definition.sample_ids:
        ordered_samples = [samples[i] for i in definition.sample_ids if i in samples]
    for sample in ordered_samples:
        if sample.id not in selected:
            continue
        identifiers = [None]
        if definition.row_mode == "populations":
            identifiers += [g.id for g in workspace.gates if g.sample_id == sample.id]
        for identifier in identifiers:
            path = gate_path(identifier)
            population = " / ".join(path) if path else "All events"
            if definition.filter.casefold() not in f"{sample.name} {population}".casefold():
                continue
            rows.append(
                dict(
                    id=f"{sample.id}:{identifier or 'root'}",
                    sample_id=sample.id,
                    sample=sample.name,
                    gate_id=identifier,
                    population=population,
                    population_path=list(path),
                    values={},
                    status={},
                )
            )
    if len(rows) > MAX_ROWS or len(rows) * len(columns) > MAX_CELLS:
        raise ValueError(
            "Table exceeds 50,000 rows or two million cells; reduce its sample scope or columns"
        )

    @cache
    def stale(identifier):
        for values, module in [
            (workspace.analyses, analysis),
            (workspace.cell_cycle_results, cellcycle),
            (workspace.proliferation_results, proliferation),
            (workspace.kinetics_results, kinetics),
            (workspace.quality_results, quality),
            (workspace.comparison_results, population_comparison),
        ]:
            result = next((r for r in values if r.id == identifier), None)
            if result:
                return module.is_stale(workspace, result)
        return True

    @cache
    def parameter_stale(sample_id, channel):
        sample = samples[sample_id]
        computed = next((p for p in sample.computed_parameters if p.name == channel), None)
        if computed and stale(computed.analysis_id):
            return True
        derived = next((p for p in sample.derived_parameters if p.name == channel), None)
        return bool(
            derived and any(parameter_stale(sample_id, dep) for dep in parse(derived.expression)[1])
        )

    @cache
    def population_stale(identifier):
        if identifier is None:
            return False
        gate = gates[identifier]
        names = {name for name in (gate.x, gate.y) if name}
        for dimension in gate.dimensions:
            names.update(dimension.ratio_channels or (dimension.channel,))
        return (
            any(parameter_stale(gate.sample_id, name) for name in names)
            or bool(gate.quality_id and stale(gate.quality_id))
            or any(population_stale(g) for g in [gate.parent_id, *gate.operands])
        )

    used_samples, used_gates, used_models, used_matrices = {}, set(), set(), set()

    def observe_sample(sample):
        return used_samples.setdefault(sample.id, dict(parameters=set(), keywords={}))

    def observe_parameter(sample, name):
        observed = observe_sample(sample)["parameters"]
        if name in observed:
            return
        observed.add(name)
        derived = next((p for p in sample.derived_parameters if p.name == name), None)
        if derived:
            for dependency in parse(derived.expression)[1]:
                observe_parameter(sample, dependency)
        computed = next((p for p in sample.computed_parameters if p.name == name), None)
        if computed:
            used_models.add(computed.analysis_id)

    def observe_population(identifier):
        if identifier is None or identifier in used_gates:
            return
        used_gates.add(identifier)
        gate = gates[identifier]
        for name in {n for n in (gate.x, gate.y) if n}:
            observe_parameter(samples[gate.sample_id], name)
        for dimension in gate.dimensions:
            for name in dimension.ratio_channels or (dimension.channel,):
                observe_parameter(samples[gate.sample_id], name)
            if dimension.compensation_ref == "FCS":
                observed = observe_sample(samples[gate.sample_id])
                for key in ("spillover", "spill"):
                    observed["keywords"][f"metadata:{key}"] = samples[gate.sample_id].metadata.get(
                        key
                    )
        if gate.quality_id:
            used_models.add(gate.quality_id)
        for dependency in [gate.parent_id, *gate.operands]:
            observe_population(dependency)

    @lru_cache(maxsize=1024)
    def summary(sample_id, identifier, channel, compensation_ref):
        return engine.summary(
            workspace,
            samples[sample_id],
            identifier,
            channel,
            definition.compensated,
            compensation_ref,
        )

    @cache
    def population_count(sample_id, identifier):
        return int(engine.mask(workspace, samples[sample_id], identifier).sum())

    @lru_cache(maxsize=1024)
    def extended(sample_id, identifier, channel, statistic, percentile, compensation_ref):
        sample = samples[sample_id]
        values = engine.column(
            workspace,
            sample,
            channel,
            compensated=definition.compensated,
            compensation_ref=compensation_ref,
        )[engine.mask(workspace, sample, identifier)]
        values = values[np.isfinite(values)]
        if not len(values):
            return None
        scale = max(float(np.abs(values).max()), 1e-300)
        normalized = values / scale
        with np.errstate(all="ignore"):
            if statistic == "percentile":
                return finite(np.quantile(normalized, percentile / 100) * scale)
            if statistic == "mad":
                return finite(np.median(abs(normalized - np.median(normalized))) * scale)
            if statistic == "variance":
                sd = np.std(normalized, ddof=1) * scale if len(values) > 1 else np.nan
                return finite(np.square(sd))
            if statistic == "geometric_std":
                return (
                    finite(np.exp(np.std(np.log(values), ddof=1)))
                    if len(values) > 1 and np.all(values > 0)
                    else None
                )
        return None

    @cache
    def comparison_result(identifier):
        result = population_comparison.saved_result(workspace, identifier)
        if result:
            population_comparison.load_artifact(engine.store, workspace.id, result)
            population_comparison.verify_snapshot_sources(workspace, result, engine)
        return result

    def comparison_cell(sample, row, column):
        result = population_comparison.saved_result(
            workspace, column.result_id, column.follow_replacement
        )
        if result is None:
            return None, "Saved population comparison is unavailable"
        try:
            comparison_result(result.id)
        except (OSError, ValueError) as exc:
            return None, f"Comparison data is unavailable: {exc}"
        used_models.add(result.id)
        issue = "Stale population comparison" if stale(result.id) else None
        if issue and not column.allow_stale:
            return None, issue
        contextual = (
            column.population_path is None
            and sample.id not in column.population_overrides
            and sample.id not in column.population_unavailable
        )
        if contextual and definition.row_mode == "populations":
            # The row already identifies the actual gate, even when names are duplicated.
            identifier, mapping_issue = row["gate_id"], None
        elif contextual and sum(s.sample_id == sample.id for s in result.request.inputs) > 1:
            return None, (
                "Multiple populations were compared for this acquisition; "
                "select a population or use population rows"
            )
        else:
            identifier, mapping_issue = mapped_gate(
                sample.id, column, tuple(row["population_path"])
            )
        if mapping_issue:
            return None, mapping_issue
        values = result.rows if column.comparison_parameter_id else result.joint_rows
        outcome = next(
            (
                r
                for r in values
                if r.role == "target"
                and r.source.sample_id == sample.id
                and r.source.gate_id == identifier
                and r.parameter_id == column.comparison_parameter_id
            ),
            None,
        )
        if outcome is None:
            return None, "This exact acquisition and population was not compared"
        if outcome.status == "unavailable":
            return None, outcome.error
        metric = column.biology_metric
        if metric in {
            "selected_count",
            "finite_count",
            "control_selected_count",
            "control_finite_count",
            "shared_events",
        }:
            value = getattr(outcome, metric)
        else:
            value = outcome.metrics.get(metric, outcome.probability.get(metric))
        return finite(value), issue or (
            "Comparison statistic is unavailable" if value is None else None
        )

    def biological(sample, column):
        values = getattr(workspace, biology.PLATFORM_FIELDS[column.platform])
        result = next((r for r in values if r.id == column.result_id), None)
        if result is None:
            return None, "Saved biological model is unavailable"
        # Replacements retain original output identifiers and historical reports.
        # Follow current bindings for this sample while preserving saved table references.
        owners = {p.analysis_id for p in sample.computed_parameters if p.name in result.columns}
        if column.follow_replacement and len(owners) == 1:
            active = next((r for r in values if r.id == next(iter(owners))), None)
            if active and active.columns == result.columns:
                result = active
        fit = next((f for f in result.fits if f.sample_id == sample.id), None)
        if fit is None:
            return None, "Sample was not fitted by this model"
        used_models.add(result.id)
        issue = "Stale biological model" if stale(result.id) else None
        if issue and not column.allow_stale:
            return None, issue
        key, generation = column.biology_metric, column.generation
        if column.platform == "kinetics":
            if key in {"threshold", "baseline_count"}:
                value = getattr(fit, key)
            elif key in {"represented_count", "time_count"}:
                value = getattr(next(d for d in result.data if d.sample_id == sample.id), key)
            else:
                interval = next((r for r in fit.ranges if r.id == column.kinetics_range_id), None)
                if interval is None:
                    return None, "Time range is not present in this kinetics model"
                if key == "responder_percent":
                    value = (
                        100 * interval.responder_count / interval.finite_count
                        if interval.responder_count is not None and interval.finite_count
                        else None
                    )
                else:
                    value = getattr(interval, key, None)
            return finite(value), issue or (
                "Statistic is undefined for this time range" if value is None else None
            )
        if key in {"fraction", "model_count", "expected_count", "assigned_count"}:
            if generation >= len(fit.fractions):
                return None, "Generation or phase is not present in this model"
            value = {
                "fraction": fit.fractions,
                "model_count": np.array(fit.fractions) * fit.data.fitted_count,
                "expected_count": fit.expected_counts,
                "assigned_count": fit.assigned_counts,
            }[key][generation]
        elif key == "fitted_count":
            value = fit.data.fitted_count
        elif key in fit.parameters:
            value = fit.parameters[key]
        elif key in fit.diagnostics:
            value = fit.diagnostics[key]
        elif hasattr(fit, "statistics") and key in type(fit.statistics).model_fields:
            value = getattr(fit.statistics, key)
        else:
            return None, "Statistic is unavailable for this model"
        return finite(value), issue or (
            "Statistic is undefined for this fit" if value is None else None
        )

    def source_cell(row, column):
        if column.control_unavailable and not column.control_sample_id:
            return None, "Source control sample is unavailable; choose a control explicitly"
        sample = samples.get(column.control_sample_id or row["sample_id"])
        if sample is None:
            return None, "Control sample is unavailable"
        observed = observe_sample(sample)
        if column.kind == "metadata":
            if column.metadata_source == "keywords":
                value = None
                canonical = column.metadata_key.lstrip("$").casefold()
                for source in ("tags", "metadata"):
                    keywords = getattr(sample, source)
                    matches = [k for k in keywords if k.lstrip("$").casefold() == canonical]
                    if column.metadata_key in keywords:
                        matches = [column.metadata_key]
                    if len(matches) > 1:
                        return None, "Keyword name is ambiguous"
                    if matches:
                        value = keywords[matches[0]]
                        observed["keywords"][f"{source}:{matches[0]}"] = value
                        break
            else:
                value = getattr(sample, column.metadata_source).get(column.metadata_key)
                observed["keywords"][f"{column.metadata_source}:{column.metadata_key}"] = value
            if value is None:
                return None, "Keyword is missing"
            if column.metadata_numeric:
                try:
                    value = finite(float(value))
                except (ValueError, OverflowError):
                    value = None
                if value is None:
                    return None, "Keyword is not a finite number"
            return value, None
        if column.kind == "biology":
            return biological(sample, column)
        if column.kind == "population_comparison":
            return comparison_cell(sample, row, column)
        identifier, issue = mapped_gate(sample.id, column, tuple(row["population_path"]))
        if issue:
            return None, issue
        observe_population(identifier)
        channel = column.channel_overrides.get(sample.id, column.channel)
        if column.statistic not in {"count", "percent_parent", "percent_total"}:
            if channel not in {c.name for c in sample.channels}:
                return None, "Parameter is missing from this sample"
            observe_parameter(sample, channel)
        issue = (
            "Stale population or computed parameter"
            if (
                population_stale(identifier)
                or (
                    column.statistic not in {"count", "percent_parent", "percent_total"}
                    and parameter_stale(sample.id, channel)
                )
            )
            else None
        )
        if issue and not column.allow_stale:
            return None, issue
        count = population_count(sample.id, identifier)
        if column.statistic == "count":
            return count, issue
        if column.statistic in {"percent_parent", "percent_total"}:
            parent = gates[identifier].parent_id if identifier else None
            denominator = (
                sample.event_count
                if column.statistic == "percent_total"
                else population_count(sample.id, parent)
            )
            return (
                100 * count / denominator if denominator else None,
                issue if denominator else "Frequency denominator is zero",
            )
        if column.statistic in {"percentile", "mad", "variance", "geometric_std"}:
            calculation = "extended"
        else:
            calculation = "summary"
        compensation_ref = column.compensation_overrides.get(sample.id, "sample")
        if compensation_ref == "FCS":
            for key in ("spillover", "spill"):
                observed["keywords"][f"metadata:{key}"] = sample.metadata.get(key)
        elif compensation_ref not in {"sample", "uncompensated"}:
            if not any(m.id == compensation_ref for m in workspace.compensations):
                return None, "Column compensation matrix is unavailable"
            used_matrices.add(compensation_ref)
        try:
            if calculation == "extended":
                value = extended(
                    sample.id,
                    identifier,
                    channel,
                    column.statistic,
                    column.percentile,
                    compensation_ref,
                )
            else:
                values = summary(sample.id, identifier, channel, compensation_ref)
                value = (
                    values["count"] - values["finite_count"]
                    if column.statistic == "nonfinite_count"
                    else values.get(column.statistic)
                )
        except (ValueError, IndexError, np.linalg.LinAlgError) as exc:
            return None, f"Column calculation is unavailable: {exc}"
        return finite(value), issue or (
            "Statistic is undefined for this population" if value is None else None
        )

    arrays = {}
    for column in definition.columns:
        if column.kind == "formula":
            continue
        for row in rows:
            value, issue = source_cell(row, column)
            row["values"][column.id] = value
            if issue:
                row["status"][column.id] = issue
        arrays[column.id] = np.array(
            [r["values"][column.id] for r in rows],
            dtype=float if numeric_column(column) else object,
        )

    def formula(identifier):
        if identifier in arrays:
            return arrays[identifier]
        column = columns[identifier]
        for dependency in column.formula_refs.values():
            formula(dependency)

        def control(alias, reference):
            matches = [s for s in samples.values() if s.id == reference or s.name == reference]
            if len(matches) != 1:
                return np.full(len(rows), np.nan)
            target = column.formula_refs[alias]
            lookup = {
                tuple(r["population_path"]): arrays[target][i]
                for i, r in enumerate(rows)
                if r["sample_id"] == matches[0].id
            }
            return np.array([lookup.get(tuple(r["population_path"]), np.nan) for r in rows])

        try:
            array = evaluate_table_formula(
                column.expression,
                lambda alias: arrays[column.formula_refs[alias]],
                control,
                len(rows),
            )
            if column.control_sample_id:
                lookup = {
                    tuple(row["population_path"]): array[i]
                    for i, row in enumerate(rows)
                    if row["sample_id"] == column.control_sample_id
                }
                array = np.array(
                    [lookup.get(tuple(row["population_path"]), np.nan) for row in rows]
                )
            elif column.control_unavailable:
                array = np.full(len(rows), np.nan)
            issue = "Formula result is undefined; check input values, controls and denominators"
        except (ValueError, TypeError, OverflowError, ZeroDivisionError) as exc:
            array, issue = np.full(len(rows), np.nan), str(exc)
        arrays[identifier] = array
        for i, row in enumerate(rows):
            row["values"][identifier] = finite(array[i])
            if not np.isfinite(array[i]):
                row["status"][identifier] = issue
            elif any("Stale" in row["status"].get(dep, "") for dep in column.formula_refs.values()):
                row["status"][identifier] = "Uses a stale model value"
        return array

    for column in definition.columns:
        formula(column.id)
    comparisons = (
        compare_rows(rows, columns, definition.comparison) if definition.comparison else []
    )
    pivot = pivot_rows(rows, columns, definition.pivot) if definition.pivot else None
    sort = definition.sort_by

    def sort_key(row):
        value = row.get(sort) if sort in {"sample", "population"} else row["values"].get(sort)
        return value is None, (
            value.casefold() if isinstance(value, str) else value
        ) if value is not None else 0

    if sort != "input":
        present, missing = (
            [r for r in rows if not sort_key(r)[0]],
            [r for r in rows if sort_key(r)[0]],
        )
        present.sort(key=sort_key, reverse=definition.descending)
        rows = present + missing
    elif definition.descending:
        rows.reverse()
    ranges = {}
    for column in definition.columns:
        array = arrays[column.id]
        values = array[np.isfinite(array)] if numeric_column(column) else []
        ranges[column.id] = [float(values.min()), float(values.max())] if len(values) else None
    model_manifest = []
    for name, results in [
        ("discovery", workspace.analyses),
        ("cell-cycle", workspace.cell_cycle_results),
        ("proliferation", workspace.proliferation_results),
        ("kinetics", workspace.kinetics_results),
        ("quality", workspace.quality_results),
        ("population-comparison", workspace.comparison_results),
    ]:
        for result in results:
            if result.id in used_models:
                model_manifest.append(
                    dict(
                        id=result.id,
                        platform=name,
                        input_hash=result.input_hash,
                        data=[d.model_dump() for d in result.data]
                        if isinstance(result.data, list)
                        else result.data.model_dump(),
                        stale=stale(result.id),
                    )
                )
    matrices = {c.id: c for c in workspace.compensations}
    provenance = dict(
        software=dict(
            cytoforge=__version__,
            python=platform.python_version(),
            numpy=np.__version__,
            scipy=scipy.__version__,
            xlsxwriter=xlsxwriter.__version__,
        ),
        samples=[
            dict(
                sample_id=identifier,
                sha256=samples[identifier].sha256,
                event_count=samples[identifier].event_count,
                acquisition_channels=[c.name for c in samples[identifier].acquisition_channels],
                compensation=matrices[samples[identifier].compensation_id].model_dump()
                if samples[identifier].compensation_id
                else None,
                parameters=[
                    dict(
                        name=name,
                        derived=next(
                            (
                                p.model_dump()
                                for p in samples[identifier].derived_parameters
                                if p.name == name
                            ),
                            None,
                        ),
                        computed=next(
                            (
                                p.model_dump()
                                for p in samples[identifier].computed_parameters
                                if p.name == name
                            ),
                            None,
                        ),
                    )
                    for name in sorted(observed["parameters"])
                ],
                keywords=observed["keywords"],
            )
            for identifier, observed in used_samples.items()
        ],
        gates=[g.model_dump() for g in workspace.gates if g.id in used_gates],
        gate_compensations=[
            c.model_dump()
            for c in workspace.compensations
            if any(
                dim.compensation_ref == c.id
                for identifier in used_gates
                for dim in gates[identifier].dimensions
            )
        ],
        column_compensations=[
            c.model_dump() for c in workspace.compensations if c.id in used_matrices
        ],
        models=model_manifest,
    )
    return dict(
        workspace_id=workspace.id,
        revision=workspace.revision,
        definition=definition.model_dump(),
        columns=[c.model_dump() for c in definition.columns],
        rows=rows[offset : offset + limit],
        total_rows=len(rows),
        column_ranges=ranges,
        provenance=provenance,
        offset=offset,
        pivot=pivot,
        comparisons=comparisons,
        notices=notices,
    )


def aggregate(values, method):
    values = np.array(
        [v for v in values if isinstance(v, (int, float)) and math.isfinite(v)], dtype=float
    )
    n = len(values)
    if method == "count":
        return n, n
    if not n or (method == "std" and n < 2):
        return None, n
    scale = max(float(np.abs(values).max()), 1e-300)
    functions = {
        "mean": np.mean,
        "median": np.median,
        "sum": np.sum,
        "std": lambda a: np.std(a, ddof=1),
        "min": np.min,
        "max": np.max,
    }
    with np.errstate(all="ignore"):
        return finite(functions[method](values / scale) * scale), n


def pivot_rows(rows, columns, settings):
    groups, dimensions = {}, set()
    for row in rows:
        left = tuple(row["values"].get(i) for i in settings.rows)
        right = tuple(row["values"].get(i) for i in settings.columns)
        groups.setdefault(left, {}).setdefault(right, []).append(row)
        dimensions.add(right)
    if (
        len(dimensions) * len(settings.measures) > 512
        or len(groups) * max(len(dimensions), 1) * len(settings.measures) > MAX_CELLS
    ):
        raise ValueError("Pivot exceeds 512 result columns or two million cells")
    dimensions = sorted(dimensions, key=lambda key: json.dumps(key, ensure_ascii=False))
    descriptors = []
    for right in dimensions:
        for measure in settings.measures:
            identifier = hashlib.sha256(json.dumps([right, measure]).encode()).hexdigest()[:32]
            label = " · ".join(
                [
                    *(
                        f"{columns[i].name}={json.dumps(v, ensure_ascii=False)}"
                        for i, v in zip(settings.columns, right, strict=True)
                    ),
                    columns[measure].name,
                ]
            )
            descriptors.append(
                dict(id=identifier, name=label, measure=measure, dimension=list(right))
            )
    output = []
    for left, combinations in sorted(
        groups.items(), key=lambda item: json.dumps(item[0], ensure_ascii=False)
    ):
        values, counts = {}, {}
        for descriptor in descriptors:
            batch = combinations.get(tuple(descriptor["dimension"]), [])
            value, n = aggregate(
                [r["values"].get(descriptor["measure"]) for r in batch], settings.aggregation
            )
            values[descriptor["id"]], counts[descriptor["id"]] = value, n
        output.append(dict(group=list(left), values=values, counts=counts))
    return dict(
        row_columns=[dict(id=i, name=columns[i].name) for i in settings.rows],
        columns=descriptors,
        rows=output,
        aggregation=settings.aggregation,
    )


def adjust_pvalues(values, method):
    if not values:
        return []
    p = np.array(values)
    order = np.argsort(p)
    ranked = p[order]
    m = len(p)
    if method == "holm":
        adjusted = np.maximum.accumulate(ranked * (m - np.arange(m)))
    elif method == "benjamini_hochberg":
        adjusted = np.minimum.accumulate((ranked * m / np.arange(1, m + 1))[::-1])[::-1]
    else:
        adjusted = ranked
    result = np.empty(m)
    result[order] = np.minimum(adjusted, 1)
    return result.tolist()


def compare_rows(rows, columns, settings):
    eligible = [
        [
            r
            for r in rows
            if r["values"].get(settings.group_column) is not None
            and str(r["values"][settings.group_column]) == label
        ]
        for label in (settings.group_a, settings.group_b)
    ]
    paired = settings.method in {"paired_t", "wilcoxon"}
    output = []
    for identifier in settings.measures:
        record = dict(
            column_id=identifier,
            column=columns[identifier].name,
            method=settings.method,
            group_a=settings.group_a,
            group_b=settings.group_b,
            adjustment=settings.adjustment,
            n_a=0,
            n_b=0,
            excluded_a=0,
            excluded_b=0,
            pairs=0,
            mean_a=None,
            mean_b=None,
            mean_difference=None,
            median_difference=None,
            statistic=None,
            p_value=None,
            adjusted_p_value=None,
            confidence_interval=None,
            confidence_level=settings.confidence_level,
            issue=None,
        )
        pairs = []
        if paired:
            lookups = []
            for group in eligible:
                lookup = {}
                for row in group:
                    key = row["values"].get(settings.pair_column)
                    if key is None:
                        continue
                    if key in lookup:
                        record["issue"] = (
                            "Pairing values are duplicated within a group; "
                            "resolve technical replicates first"
                        )
                    lookup[key] = row
                lookups.append(lookup)
            for key in lookups[0].keys() & lookups[1].keys():
                values = [lookup[key]["values"].get(identifier) for lookup in lookups]
                if all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
                    pairs.append(values)
            a, b = [np.array([p[i] for p in pairs], dtype=float) for i in (0, 1)]
            record["pairs"] = len(pairs)
        else:
            a, b = [
                np.array(
                    [
                        r["values"][identifier]
                        for r in group
                        if isinstance(r["values"].get(identifier), (int, float))
                        and math.isfinite(r["values"][identifier])
                    ],
                    dtype=float,
                )
                for group in eligible
            ]
        record.update(
            n_a=len(a),
            n_b=len(b),
            excluded_a=len(eligible[0]) - len(a),
            excluded_b=len(eligible[1]) - len(b),
        )
        if len(a) < 2 or len(b) < 2:
            record["issue"] = (
                record["issue"]
                or "At least two finite samples or complete pairs are required per group"
            )
        if not record["issue"]:
            # Center before scaling so a large common offset cannot erase small
            # differences. Rank tests use original observations/differences to
            # preserve their ties; rescaling can change rounded ranks.
            anchor = a[0] / 2 + b[0] / 2
            with np.errstate(all="ignore"):
                ac, bc = a - anchor, b - anchor
            if not (np.all(np.isfinite(ac)) and np.all(np.isfinite(bc))):
                anchor, ac, bc = 0, a, b
            scale = max(float(np.abs(ac).max()), float(np.abs(bc).max()), 1e-300)
            au, bu = ac / scale, bc / scale
            record.update(
                mean_a=finite(anchor + np.mean(au) * scale),
                mean_b=finite(anchor + np.mean(bu) * scale),
                mean_difference=finite((np.mean(au) - np.mean(bu)) * scale),
                median_difference=finite((np.median(au) - np.median(bu)) * scale),
            )
            with warnings.catch_warnings(), np.errstate(all="ignore"):
                warnings.simplefilter("ignore", RuntimeWarning)
                if settings.method == "welch":
                    tested = stats.ttest_ind(au, bu, equal_var=False)
                elif settings.method == "paired_t":
                    differences = a - b
                    if not np.all(np.isfinite(differences)):
                        differences = a.astype(np.longdouble) - b.astype(np.longdouble)
                    if not np.all(np.isfinite(differences)):
                        record["issue"] = "Paired differences exceed the numerical range"
                        output.append(record)
                        continue
                    scale = max(np.abs(differences).max(), 1e-300)
                    tested = stats.ttest_1samp(np.asarray(differences / scale, dtype=float), 0)
                elif settings.method == "mann_whitney":
                    tested = stats.mannwhitneyu(a, b, alternative="two-sided", method="auto")
                else:
                    differences = a - b
                    if not np.all(np.isfinite(differences)):
                        differences = a.astype(np.longdouble) - b.astype(np.longdouble)
                    if not np.all(np.isfinite(differences)):
                        record["issue"] = "Paired differences exceed the numerical range"
                        output.append(record)
                        continue
                    tested = (
                        stats.wilcoxon(
                            differences,
                            zero_method="wilcox",
                            alternative="two-sided",
                            method="auto",
                        )
                        if np.any(differences != 0)
                        else None
                    )
                if tested is None:
                    record.update(statistic=0.0, p_value=1.0)
                else:
                    record.update(statistic=finite(tested.statistic), p_value=finite(tested.pvalue))
                    if hasattr(tested, "confidence_interval"):
                        ci = tested.confidence_interval(settings.confidence_level)
                        record["confidence_interval"] = [
                            finite(ci.low * scale),
                            finite(ci.high * scale),
                        ]
            if record["statistic"] is None or record["p_value"] is None:
                record["issue"] = "Test is undefined for constant or degenerate observations"
                record["p_value"] = None
        output.append(record)
    valid = [r for r in output if r["p_value"] is not None]
    for record, corrected in zip(
        valid, adjust_pvalues([r["p_value"] for r in valid], settings.adjustment), strict=True
    ):
        record["adjusted_p_value"] = corrected
    return output


def export_columns(result, include_hidden=False):
    return [c for c in result["columns"] if include_hidden or not c["hidden"]]


def csv_text(result, include_hidden=False):
    stream = io.StringIO()
    writer = csv.writer(stream)
    columns = export_columns(result, include_hidden)

    def safe(value):
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
            return "'" + value
        return value

    writer.writerow(
        ["Sample", "Sample ID", "Population", "Gate ID", *[safe(c["name"]) for c in columns]]
    )
    for row in result["rows"]:
        writer.writerow(
            [
                safe(row["sample"]),
                row["sample_id"],
                safe(row["population"]),
                row["gate_id"],
                *[safe(row["values"][c["id"]]) for c in columns],
            ]
        )
    return stream.getvalue()


def write_xlsx(path, result, temp_dir, include_hidden=False):
    columns = export_columns(result, include_hidden)
    with xlsxwriter.Workbook(
        str(path),
        {
            "constant_memory": True,
            "tmpdir": str(temp_dir),
            "strings_to_formulas": False,
            "strings_to_urls": False,
        },
    ) as book:
        book.set_properties({"title": result["definition"]["name"], "author": "CytoForge"})
        heading = book.add_format({"bold": True, "bg_color": "#15263D", "font_color": "#FFFFFF"})
        long_sheet, long_index, long_number = None, 1, 0

        def write(sheet, row, column, value, cell_format=None):
            nonlocal long_sheet, long_index, long_number
            # Excel limits text cells to 32,767 UTF-16 units. Preserve overflow
            # in ordered chunks, rather than accepting XlsxWriter's truncation.
            if (
                isinstance(value, str)
                and len(value) > 16000
                and len(value.encode("utf-16-le")) // 2 > 32767
            ):
                reference = xlsxwriter.utility.xl_rowcol_to_cell(row, column)
                source = sheet.name
                for part, start in enumerate(range(0, len(value), 16000), 1):
                    if long_sheet is None or long_index >= MAX_EXCEL_ROWS:
                        long_number += 1
                        long_sheet = book.add_worksheet(
                            "Long text" if long_number == 1 else f"Long text {long_number}"
                        )
                        long_sheet.write_row(
                            0, 0, ["Source sheet", "Cell", "Part", "Text"], heading
                        )
                        long_sheet.freeze_panes(1, 0)
                        long_index = 1
                    long_sheet.write_row(
                        long_index, 0, [source, reference, part, value[start : start + 16000]]
                    )
                    long_index += 1
                value = (
                    f"[Full text is preserved in Long text sheet(s): {source}!{reference}; "
                    "concatenate chunks by Part.]"
                )
            sheet.write(row, column, value, cell_format)

        def write_row(sheet, row, values, cell_format=None):
            for column, value in enumerate(values):
                write(sheet, row, column, value, cell_format)

        sheet = book.add_worksheet("Data")
        sheet.freeze_panes(1, 2)
        sheet.set_column(0, 3, 26)
        write_row(
            sheet,
            0,
            ["Sample", "Sample ID", "Population", "Gate ID", *[c["name"] for c in columns]],
            heading,
        )
        formats = [
            book.add_format(
                {"num_format": "0" if c["decimals"] == 0 else "0." + "0" * c["decimals"]}
            )
            for c in columns
        ]
        for index, row in enumerate(result["rows"], 1):
            write_row(
                sheet, index, [row["sample"], row["sample_id"], row["population"], row["gate_id"]]
            )
            for j, column in enumerate(columns, 4):
                value = row["values"][column["id"]]
                write(sheet, index, j, value, formats[j - 4])
        sheet.autofilter(0, 0, len(result["rows"]), 3 + len(columns))
        for j, column in enumerate(columns, 4):
            sheet.set_column(j, j, 20)
            if column["heatmap"] and result["rows"]:
                sheet.conditional_format(1, j, len(result["rows"]), j, {"type": "3_color_scale"})
        status = book.add_worksheet("Cell status")
        status.write_row(0, 0, ["Sample ID", "Population", "Column", "Status"], heading)
        index = 1
        status_number = 1
        by_id = {c["id"]: c for c in result["columns"]}
        for row in result["rows"]:
            for identifier, issue in row["status"].items():
                if index >= MAX_EXCEL_ROWS:
                    status_number += 1
                    status = book.add_worksheet(f"Cell status {status_number}")
                    status.write_row(0, 0, ["Sample ID", "Population", "Column", "Status"], heading)
                    index = 1
                column = by_id[identifier]
                write_row(
                    status, index, [row["sample_id"], row["population"], column["name"], issue]
                )
                index += 1
        if result["pivot"]:
            pivot = result["pivot"]
            sheet = book.add_worksheet("Pivot")
            write_row(
                sheet,
                0,
                [
                    *[c["name"] for c in pivot["row_columns"]],
                    *[c["name"] for c in pivot["columns"]],
                ],
                heading,
            )
            for index, row in enumerate(pivot["rows"], 1):
                write_row(
                    sheet,
                    index,
                    [*row["group"], *[row["values"][c["id"]] for c in pivot["columns"]]],
                )
            sheet.freeze_panes(1, len(pivot["row_columns"]))
        if result["comparisons"]:
            sheet = book.add_worksheet("Comparisons")
            keys = list(result["comparisons"][0])
            write_row(sheet, 0, keys, heading)
            for index, row in enumerate(result["comparisons"], 1):
                write_row(
                    sheet,
                    index,
                    [json.dumps(row[k]) if isinstance(row[k], list) else row[k] for k in keys],
                )
        sheet = book.add_worksheet("Provenance")
        sheet.set_column(0, 0, 24)
        sheet.set_column(1, 1, 100)
        info = [
            ["Workspace ID", result["workspace_id"]],
            ["Workspace revision", result["revision"]],
            ["Definition", json.dumps(result["definition"], ensure_ascii=False)],
            ["Source manifest", json.dumps(result["provenance"], ensure_ascii=False)],
            [
                "Intensity basis",
                "Compensated parameter values; display transformations excluded"
                if result["definition"]["compensated"]
                else "Acquired parameter values; display transformations excluded",
            ],
            ["Undefined values", "Blank numeric cells; reasons are in Cell status"],
            [
                "Comparison unit",
                "Selected samples; technical replicates need an appropriate experimental design",
            ],
            ["Multiplicity", "Adjustment covers only valid tests in this saved comparison"],
            [
                "Large text",
                "Values exceeding Excel's text limit are split in Long text sheets; "
                "concatenate by Source sheet, Cell, then Part.",
            ],
        ]
        for i, values in enumerate(info):
            write_row(sheet, i, values)
