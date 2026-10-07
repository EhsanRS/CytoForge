"""Event identity and portable figures shared by fitted biological populations."""

from __future__ import annotations

import hashlib
import json
import math
from xml.etree.ElementTree import Element, SubElement, tostring

import numpy as np

from .fileio import load_validated_array
from .formulas import parse

PLATFORM_FIELDS = {
    "cell-cycle": "cell_cycle_results",
    "proliferation": "proliferation_results",
    "kinetics": "kinetics_results",
}


def population_paths(workspace):
    gates = {g.id: g for g in workspace.gates}
    output = {}
    for gate in workspace.gates:
        path, identifier = [], gate.id
        while identifier:
            parent = gates[identifier]
            path.append(parent.name)
            identifier = parent.parent_id
        output[gate.id] = (gate.sample_id, tuple(reversed(path)))
    return output


def preserve_table_populations(workspace, previous):
    """Keep prototype paths bound to the same populations after model renames."""
    current = population_paths(workspace)
    for table in workspace.tables:
        for column in table.columns:
            if not column.population_path:
                continue
            matches = {
                i: s
                for i, (s, path) in previous.items()
                if path == tuple(column.population_path) and i in current
            }
            paths = {current[i][1] for i in matches}
            if len(paths) == 1:
                column.population_path = list(next(iter(paths)))
            elif len(paths) > 1:
                # A subset of samples may use another model with the same name.
                # Preserve only originally unambiguous bindings, never choose a
                # population from an ambiguous prototype or override user maps.
                for identifier, sample_id in matches.items():
                    if (
                        sample_id not in column.population_overrides
                        and sum(s == sample_id for s in matches.values()) == 1
                        and current[identifier][1] != tuple(column.population_path)
                    ):
                        column.population_overrides[sample_id] = identifier


def saved_result(workspace, platform, identifier):
    result = next(
        (r for r in getattr(workspace, PLATFORM_FIELDS[platform]) if r.id == identifier), None
    )
    if result is None:
        raise KeyError("Saved biological model not found")
    return result


def replacement_target(workspace, request, snapshot=None):
    identifier = request.replace_result_id
    if identifier is None:
        return None
    platform = {
        "cell_cycle": "cell-cycle",
        "proliferation": "proliferation",
        "kinetics": "kinetics",
    }[request.algorithm]
    original = saved_result(workspace, platform, identifier)
    if {(v.sample_id, v.gate_id) for v in original.request.inputs} != {
        (v.sample_id, v.gate_id) for v in request.inputs
    }:
        raise ValueError(
            "Replacement requires the same sample populations. "
            "Save a new model for a different scope."
        )
    count = (
        4
        if request.algorithm == "cell_cycle"
        else 3
        if request.algorithm == "kinetics"
        else request.generations + 2
    )
    if request.algorithm == "kinetics" and {r.id for r in original.request.ranges} != {
        r.id for r in request.ranges
    }:
        raise ValueError(
            "Replacement preserves time range identities. Save a new model to add/remove ranges."
        )
    if len(original.columns) != count:
        raise ValueError(
            "Replacement requires the same output count. "
            "Save a new model to change the number of generations."
        )
    for source in request.inputs:
        sample = next(s for s in workspace.samples if s.id == source.sample_id)
        active = {
            (p.name, p.index) for p in sample.computed_parameters if p.analysis_id == identifier
        }
        if active != set(zip(original.columns, range(count), strict=True)):
            raise ValueError(
                "This model no longer owns its output parameters. Refit it as a new model."
            )
    if snapshot is not None:
        for signature in snapshot["scientific_input"]:
            if any(
                v.get("analysis", [None])[0] == identifier for v in signature["channels"].values()
            ):
                raise ValueError(
                    "A replacement fit cannot use its own previous output "
                    "in an input population or control."
                )
    return original


def output_columns(workspace, request, defaults):
    target = replacement_target(workspace, request)
    return target.columns.copy() if target is not None else defaults


def dependencies(workspace, result):
    """Compute the exact parameter/gate/layout closure before a reversible removal."""
    removed, outputs, derived = {}, [], []
    for sample in workspace.samples:
        names = {p.name for p in sample.computed_parameters if p.analysis_id == result.id}
        if not names:
            continue
        outputs.append({"sample_id": sample.id, "sample": sample.name, "parameters": sorted(names)})
        while True:
            dependent = {
                p.name for p in sample.derived_parameters if set(parse(p.expression)[1]) & names
            }
            if dependent <= names:
                break
            names |= dependent
        removed[sample.id] = names
        derived.extend(
            {"sample_id": sample.id, "sample": sample.name, "name": p.name}
            for p in sample.derived_parameters
            if p.name in names
        )
    gate_ids = set()
    for gate in workspace.gates:
        names = removed.get(gate.sample_id, set())
        channels = {v for v in (gate.x, gate.y) if v}
        for dimension in gate.dimensions:
            channels.update(dimension.ratio_channels or (dimension.channel,))
        if names & channels:
            gate_ids.add(gate.id)
    while True:
        dependent = {
            g.id for g in workspace.gates if g.parent_id in gate_ids or set(g.operands) & gate_ids
        }
        if dependent <= gate_ids:
            break
        gate_ids |= dependent
    plots = [
        {"layout_id": layout.id, "layout": layout.name, "plot_id": p.id, "title": p.title}
        for layout in workspace.layouts
        for p in layout.plots
        if any(
            layer.gate_id in gate_ids
            or layer.coordinate_gate_id in gate_ids
            or layer.backgate_id in gate_ids
            or {p.x, p.y} & removed.get(layer.sample_id, set())
            for layer in [p, *p.overlays]
        )
    ]
    layout_elements = []
    for layout in workspace.layouts:
        for element in layout.elements:
            affected_element = element.gate_id in gate_ids
            if element.plot:
                affected_element |= any(
                    layer.gate_id in gate_ids
                    or layer.coordinate_gate_id in gate_ids
                    or layer.backgate_id in gate_ids
                    or {element.plot.x, element.plot.y} & removed.get(layer.sample_id, set())
                    for layer in [element.plot, *element.plot.overlays]
                )
            elif element.kind == "biology":
                affected_element |= element.result_id == result.id
                affected_element |= element.follow_replacement and bool(
                    removed.get(element.sample_id, set()) & set(result.columns)
                )
            elif element.kind == "text":
                affected_element |= any(
                    f"{{{{stat:{metric}:{name}}}}}" in element.text
                    for name in removed.get(element.sample_id, set())
                    for metric in (
                        "mean",
                        "median",
                        "std",
                        "cv",
                        "robust_cv",
                        "geometric_mean",
                        "finite_count",
                        "min",
                        "max",
                    )
                )
            if affected_element:
                layout_elements.append(
                    dict(
                        layout_id=layout.id,
                        layout=layout.name,
                        element_id=element.id,
                        kind=element.kind,
                        title=element.title,
                    )
                )
    names = set().union(*removed.values()) if removed else set()
    retained_names = {
        c.name
        for s in workspace.samples
        for c in s.channels
        if c.name not in removed.get(s.id, set())
    }
    gate_map = {g.id: g for g in workspace.gates}

    def gate_names(identifier):
        path = []
        while identifier:
            gate = gate_map[identifier]
            path.append(gate.name)
            identifier = gate.parent_id
        return list(reversed(path))

    removed_paths = [gate_names(identifier) for identifier in gate_ids]
    model_map = {
        r.id: r
        for r in [
            *workspace.cell_cycle_results,
            *workspace.proliferation_results,
            *workspace.kinetics_results,
        ]
    }
    tables, plate_reports = [], []
    for table in [*workspace.tables, *workspace.plates]:
        affected_columns = [
            c.id
            for c in table.columns
            if c.channel in names
            or c.result_id == result.id
            or (
                bool(removed)
                and c.follow_replacement
                and c.result_id in model_map
                and model_map[c.result_id].columns == result.columns
            )
            or any(g in gate_ids for g in c.population_overrides.values())
            or c.population_path in removed_paths
        ]
        is_plate = table in workspace.plates
        if (
            not is_plate and table.channel in names and table.channel not in retained_names
        ) or affected_columns:
            (plate_reports if is_plate else tables).append(
                {"id": table.id, "name": table.name, "columns": affected_columns}
            )

    def uses_snapshot(value):
        if isinstance(value, dict):
            names = removed.get(value.get("sample_id"), set())
            if (
                names
                and isinstance(value.get("channels"), dict)
                and any(
                    isinstance(v, dict) and v.get("name") in names
                    for v in value["channels"].values()
                )
            ):
                return True
            if (
                isinstance(value.get("analysis"), list)
                and value["analysis"]
                and value["analysis"][0] == result.id
            ):
                return True
            if isinstance(value.get("gates"), dict) and set(value["gates"]) & gate_ids:
                return True
            return any(uses_snapshot(v) for v in value.values())
        return isinstance(value, list) and any(uses_snapshot(v) for v in value)

    historical = [
        {"id": r.id, "name": r.request.name}
        for r in [
            *workspace.analyses,
            *workspace.cell_cycle_results,
            *workspace.proliferation_results,
            *workspace.quality_results,
            *workspace.kinetics_results,
        ]
        if r.id != result.id and uses_snapshot(r.input_snapshot)
    ]
    return dict(
        outputs=outputs,
        derived_parameters=derived,
        gates=[
            {"id": g.id, "name": g.name, "sample_id": g.sample_id}
            for g in workspace.gates
            if g.id in gate_ids
        ],
        layout_plots=plots,
        layout_elements=layout_elements,
        tables=tables,
        plates=plate_reports,
        historical_analyses=historical,
    )


def remove_model(workspace, platform, identifier, cascade=False):
    result = saved_result(workspace, platform, identifier)
    affected = dependencies(workspace, result)
    if not cascade and any(
        affected[k]
        for k in (
            "derived_parameters",
            "gates",
            "layout_plots",
            "layout_elements",
            "tables",
            "plates",
        )
    ):
        raise ValueError(
            "This model has dependent populations, parameters or reports. "
            "Review them before cascade removal."
        )
    removed = {row["sample_id"]: set(row["parameters"]) for row in affected["outputs"]}
    for row in affected["derived_parameters"]:
        removed[row["sample_id"]].add(row["name"])
    for sample in workspace.samples:
        names = removed.get(sample.id, set())
        sample.channels = [c for c in sample.channels if c.name not in names]
        sample.computed_parameters = [
            p for p in sample.computed_parameters if p.analysis_id != identifier
        ]
        sample.derived_parameters = [p for p in sample.derived_parameters if p.name not in names]
    gate_ids = {v["id"] for v in affected["gates"]}
    workspace.gates = [g for g in workspace.gates if g.id not in gate_ids]
    plot_ids = {(v["layout_id"], v["plot_id"]) for v in affected["layout_plots"]}
    for layout in workspace.layouts:
        layout.plots = [p for p in layout.plots if (layout.id, p.id) not in plot_ids]
        elements = {
            row["element_id"]
            for row in affected["layout_elements"]
            if row["layout_id"] == layout.id
        }
        layout.elements = [
            element
            for element in layout.elements
            if element.id not in elements or element.kind == "biology"
        ]
        for element in layout.elements:
            if element.id in elements and element.kind == "biology":
                element.result_id = identifier
    table_ids = {v["id"] for v in affected["tables"]}
    for table in workspace.tables:
        if table.id in table_ids:
            table.channel = ""
    # Keep a removed live model explicitly unavailable rather than silently
    # falling back to a historical fit through a follow-replacement reference.
    affected_reports = {
        row["id"]: set(row["columns"]) for row in [*affected["tables"], *affected["plates"]]
    }
    for report in [*workspace.tables, *workspace.plates]:
        for column in report.columns:
            if column.id in affected_reports.get(report.id, set()) and column.kind == "biology":
                column.result_id = identifier
    setattr(
        workspace,
        PLATFORM_FIELDS[platform],
        [r for r in getattr(workspace, PLATFORM_FIELDS[platform]) if r.id != identifier],
    )


def rename_model(workspace, platform, identifier, name):
    result = saved_result(workspace, platform, identifier)
    paths = population_paths(workspace)
    previous, result.request.name = result.request.name, name
    prefix = f"{previous} · "
    for gate in workspace.gates:
        if gate.provenance.get(
            f"{result.request.algorithm}_id"
        ) == identifier and gate.name.startswith(prefix):
            if platform == "kinetics":
                from .kinetics import gate_name

                gate.name = gate_name(
                    name,
                    gate.provenance.get("range_name", gate.name[len(prefix) :]),
                    gate.provenance.get("range_id", ""),
                )
            else:
                gate.name = f"{name} · {gate.name[len(prefix) :]}"
    for sample in workspace.samples:
        names = {p.name for p in sample.computed_parameters if p.analysis_id == identifier}
        for channel in sample.channels:
            if channel.name in names and channel.label.startswith(previous + " "):
                channel.label = name + channel.label[len(previous) :]
    preserve_table_populations(workspace, paths)


def removal_preview(workspace, platform, identifier):
    result = saved_result(workspace, platform, identifier)
    preview = dict(model_id=identifier, name=result.request.name, **dependencies(workspace, result))
    return dict(
        preview,
        revision=workspace.revision,
        review_hash=hashlib.sha256(json.dumps(preview, sort_keys=True).encode()).hexdigest(),
    )


COLORS = [
    "#4f7ee6",
    "#0d9b7c",
    "#d69531",
    "#a66bdb",
    "#dc687d",
    "#38a5ad",
    "#b28242",
    "#7188d6",
    "#5d976a",
    "#c278b5",
    "#738190",
    "#d88954",
    "#729fbd",
]


def load_probability_data(store, workspace_id, result, data, label, offset=0):
    """Verify all original rows in bounded chunks, including undefined rows."""
    path = store.analysis_path(workspace_id, result.id, data.sample_id)
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != data.sha256:
        raise ValueError(f"{label} event probabilities failed their integrity check")
    return load_validated_array(
        path, lambda values: validate_probability_data(result, data, values, label, offset)
    )


def validate_probability_data(result, data, values, label, offset):
    k = len(result.columns) - 1
    if values.shape != (data.event_count, k + 1) or values.dtype != np.float32:
        raise ValueError(f"Invalid event-aligned {label.lower()} probabilities")
    fitted_count, expected, assigned = 0, np.zeros(k), np.zeros(k, dtype=np.int64)
    for start in range(0, data.event_count, 100000):
        chunk = values[start : start + 100000]
        finite = np.all(np.isfinite(chunk), axis=1)
        if not np.all(finite | np.all(np.isnan(chunk), axis=1)):
            raise ValueError(f"{label} probability rows must be wholly defined or undefined")
        subset = chunk[finite]
        fitted_count += len(subset)
        probabilities = subset[:, :k]
        if (
            np.any(probabilities < 0)
            or np.any(probabilities > 1)
            or not np.allclose(probabilities.sum(axis=1, dtype=np.float64), 1, atol=1e-6, rtol=0)
        ):
            raise ValueError(f"{label} probabilities must be finite and sum to one")
        assignments = np.argmax(probabilities, axis=1)
        if not np.array_equal(subset[:, k], assignments + offset):
            raise ValueError(f"{label} assignments do not match their probabilities")
        expected += probabilities.sum(axis=0, dtype=np.float64)
        assigned += np.bincount(assignments, minlength=k)
    fit = next(f for f in result.fits if f.sample_id == data.sample_id)
    if (
        fitted_count != data.fitted_count
        or not np.array_equal(assigned, fit.assigned_counts)
        or (not np.allclose(expected, fit.expected_counts, rtol=1e-6, atol=0.01))
    ):
        raise ValueError(f"{label} population counts do not match their original events")


def histogram_figure(fit, title, subtitle, channel, labels, footer, logarithmic=False, stale=False):
    """Standalone vector histogram with component, total and residual curves."""
    k = len(labels)
    rows = math.ceil(k / 3)
    height = 608 + rows * 26 + len(footer) * 24
    svg = Element(
        "svg",
        xmlns="http://www.w3.org/2000/svg",
        width="1000",
        height=str(height),
        viewBox=f"0 0 1000 {height}",
    )
    SubElement(svg, "rect", width="1000", height=str(height), fill="white")

    def label(x, y, value, size=14, fill="#334155"):
        item = SubElement(
            svg,
            "text",
            x=str(x),
            y=str(y),
            fill=fill,
            **{"font-family": "Arial, sans-serif", "font-size": str(size)},
        )
        item.text = str(value)

    label(76, 35, title, 22)
    label(76, 60, subtitle, 13)
    label(
        76,
        84,
        "Scientific inputs changed — historical fit" if stale else "Fitted source events",
        13,
        "#b45309" if stale else "#64748b",
    )
    observed, components = np.asarray(fit.observed), np.asarray(fit.components)
    total = components.sum(axis=0)
    ymax = max(float(observed.max()), float(total.max()), 1) * 1.08
    edges = np.asarray(fit.edges)
    positions = (
        np.log2(edges) if logarithmic else edges / max(abs(edges[0]), abs(edges[-1]), 1e-300)
    )
    centers = positions[:-1] / 2 + positions[1:] / 2
    x = 76 + 864 * (centers - positions[0]) / (positions[-1] - positions[0])
    for i in range(5):
        y = 405 - 290 * i / 4
        SubElement(svg, "line", x1="76", x2="940", y1=str(y), y2=str(y), stroke="#e2e8f0")
        label(8, y + 5, f"{ymax * i / 4:.0f}", 12)
    for values, color, width in [
        (observed, "#475569", 1.5),
        (total, "#a855f7", 2.5),
        *[(row, COLORS[i], 1.8) for i, row in enumerate(components)],
    ]:
        path = "M" + " L".join(
            f"{px:.3f},{405 - 290 * v / ymax:.3f}" for px, v in zip(x, values, strict=True)
        )
        SubElement(svg, "path", d=path, fill="none", stroke=color, **{"stroke-width": str(width)})
    residual = observed - total
    limit = max(float(np.abs(residual).max()), 1)
    SubElement(svg, "line", x1="76", x2="940", y1="471", y2="471", stroke="#cbd5e1")
    path = "M" + " L".join(
        f"{px:.3f},{471 - 35 * v / limit:.3f}" for px, v in zip(x, residual, strict=True)
    )
    SubElement(svg, "path", d=path, fill="none", stroke="#64748b", **{"stroke-width": "1.2"})
    label(8, 455, "Residual", 12)
    label(8, 474, "0", 12)
    for i in range(5):
        t = i / 4
        if logarithmic:
            # Dividing by the maximum keeps very large, finite intensities finite.
            value = fit.range_max * math.exp(math.log(fit.range_min / fit.range_max) * (1 - t))
        else:
            value = fit.range_min * (1 - t) + fit.range_max * t
        label(76 + 864 * t, 530, f"{value:.5g}", 12)
    label(350, 554, f"{channel} ({'logarithmic' if logarithmic else 'linear'} intensity)")
    label(
        76,
        580,
        "Observed: gray · Total model: purple · Generation counts conditional on fit range",
        12,
    )
    for i, name in enumerate(labels):
        label(
            76 + (i % 3) * 285,
            606 + (i // 3) * 26,
            f"{name}: {fit.fractions[i] * 100:.2f}% · assigned {fit.assigned_counts[i]:,}",
            13,
            COLORS[i],
        )
    for i, line in enumerate(footer):
        label(76, 612 + rows * 26 + i * 24, line, 12)
    return tostring(svg, encoding="utf-8", xml_declaration=True)
