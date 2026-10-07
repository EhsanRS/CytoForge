"""FlowJo saved table definitions, restricted formulas and explicit source bindings."""

from __future__ import annotations

import ast
import html
import json
import re

from pydantic import Field

from .models import Id, Model, TableColumn, TableDefinition, new_id
from .table_expressions import parse_table_formula


class ImportedTable(Model):
    id: str
    name: str
    group_name: str = ""
    source_ids: list[str] = Field(default_factory=list)
    total_columns: int = 0
    definition: TableDefinition | None = None
    column_sources: dict[Id, dict[str, str]] = Field(default_factory=dict)
    attributes: dict[str, str] = Field(default_factory=dict)


CELL = re.compile(
    r"""<Cell\s+column\s*=\s*(?P<quote>["'])(?P<name>.*?)(?P=quote)\s*"""
    r"(?:\[(?P<row>[+-]?\d+)\])?\s*/>",
    re.S,
)
TOKEN = re.compile(
    r"\s+|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|[A-Za-z_]\w*|"
    r"""'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|"""
    r"&&|\|\||<=|>=|!=|==|[+\-*/%,()<>!=]"
)
FUNCTIONS = {
    "abs": "abs",
    "ceil": "ceil",
    "floor": "floor",
    "neg": "-",
    "min": "min",
    "max": "max",
    "pow": "exp",
    "exp": "exp",
    "ln": "log",
    "log": "log10",
    "sqrt": "sqrt",
    "ifthen": "ifelse",
    "sin": "sin",
    "cos": "cos",
    "tan": "tan",
    "sinh": "sinh",
    "cosh": "cosh",
    "tanh": "tanh",
    "asin": "asin",
    "acos": "acos",
    "atan": "atan",
}
STATISTICS = {
    "count": "count",
    "fj.stat.count": "count",
    "freq. of parent": "percent_parent",
    "fj.stat.freqofparent": "percent_parent",
    "freq. of total": "percent_total",
    "fj.stat.freqoftotal": "percent_total",
    "mean": "mean",
    "fj.stat.mean": "mean",
    "median": "median",
    "fj.stat.median": "median",
    "sd": "std",
    "fj.stat.sd": "std",
    "cv": "cv",
    "fj.stat.cv": "cv",
    "min": "min",
    "minimum": "min",
    "max": "max",
    "maximum": "max",
    "percentile": "percentile",
    "fj.stat.percentile": "percentile",
    "median abs dev": "mad",
    "fj.stat.mad": "mad",
}


def _bind_negations(tokens):
    """Keep source unary ! precedence, which differs from Python's not."""

    def operand(position):
        if position >= len(tokens):
            raise ValueError("Missing logical negation operand")
        value = tokens[position]
        if value in {"!", "+", "-"}:
            following, end = operand(position + 1)
            prefix = "not " if value == "!" else value
            return f"({prefix}({following}))", end
        function = ""
        if value in FUNCTIONS.values() and position + 1 < len(tokens):
            function, position = value, position + 1
            value = tokens[position]
        if value == "(":
            depth, end = 1, position + 1
            while end < len(tokens) and depth:
                depth += (tokens[end] == "(") - (tokens[end] == ")")
                end += 1
            if depth:
                raise ValueError("Unclosed logical negation operand")
            inside = _bind_negations(tokens[position + 1 : end - 1])
            return f"{function}({inside})", end
        if function or not (
            value.startswith(("col(", "row(", "offset("))
            or re.fullmatch(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", value)
        ):
            raise ValueError("Unsupported logical negation operand")
        return value, position + 1

    output, position = [], 0
    while position < len(tokens):
        if tokens[position] == "!":
            following, position = operand(position + 1)
            output.append(f"(not ({following}))")
        else:
            output.append(tokens[position])
            position += 1
    return " ".join(output)


def convert_formula(expression):
    """Translate documented Cell references without executing XML or code."""
    if not expression or len(expression) > 2048:
        raise ValueError("FlowJo formulas require 1–2048 characters")
    position, pieces = 0, []
    while position < len(expression):
        cell = CELL.match(expression, position)
        if cell:
            alias = html.unescape(cell["name"])
            index = cell["row"]
            if index is None:
                pieces.append(f"col({json.dumps(alias)})")
            else:
                function = "offset" if index.startswith(("+", "-")) else "row"
                pieces.append(f"{function}({json.dumps(alias)}, {int(index)})")
            position = cell.end()
            continue
        token = TOKEN.match(expression, position)
        if token is None:
            raise ValueError("Unsupported FlowJo formula token or Cell reference")
        value = token[0]
        position = token.end()
        if value.isspace():
            continue
        elif value[0].isalpha() or value[0] == "_":
            replacement = FUNCTIONS.get(value.casefold())
            if replacement is None:
                raise ValueError(f"Unsupported FlowJo function or bare column name: {value}")
            pieces.append(replacement)
        else:
            if value.startswith(("'", '"')):
                raise ValueError("Text formula constants have no numeric table conversion")
            pieces.append({"&&": "and", "||": "or", "=": "=="}.get(value, value))
    try:
        converted = _bind_negations(pieces)
    except RecursionError as exc:
        raise ValueError("FlowJo formula nesting is too deep") from exc
    tree, _ = parse_table_formula(converted)
    if any(isinstance(node, ast.Compare) and len(node.ops) > 1 for node in ast.walk(tree)):
        raise ValueError("Chained comparisons require explicit Boolean predicates")
    return converted


def _flag(attributes, name):
    value = attributes.get(name, "0")
    if value not in {"0", "1", "false", "true", ""}:
        raise ValueError(f"{name} is not a valid Boolean")
    return value in {"1", "true"}


def _name(label, occupied):
    base = (label.strip() or "Column")[:160]
    candidate, count = base, 2
    while candidate.casefold() in occupied:
        suffix = f" ({count})"
        candidate = base[: 160 - len(suffix)] + suffix
        count += 1
    occupied.add(candidate.casefold())
    return candidate


def parse_workspace_tables(root, sources, groups, issues):
    from .interchange import Issue, child, children

    def issue(code, message, table, severity="warning"):
        issues.append(Issue(code=code, message=f"{table.name}: {message}"[:500], severity=severity))

    tables = []
    elements = children(child(root, "TableEditor"), "Table")
    if len(elements) > 1000:
        raise ValueError("Import at most 1,000 FlowJo table definitions")
    for index, element in enumerate(elements):
        table = ImportedTable(
            id=f"table-{index + 1}",
            name=element.get("name", f"Table {index + 1}"),
            attributes=dict(element.attrib),
        )
        tables.append(table)
        columns = children(element, "TColumn")
        table.total_columns = len(columns)
        if not columns:
            continue
        if len(columns) > 128:
            issue(
                "unsupported-table",
                "More than 128 columns; the complete definition stays in XML.",
                table,
            )
            continue
        iteration = child(element, "Iteration")
        if iteration is not None and iteration.get("iterationType", "SAMPLE") != "SAMPLE":
            issue(
                "unsupported-table-iteration",
                "Only sample iteration has a native conversion.",
                table,
            )
            continue
        if iteration is not None and any(
            iteration.get(key) for key in ("iterationKeyword", "discriminator")
        ):
            issue(
                "unsupported-table-iteration",
                "Keyword or discriminator iteration stays in XML.",
                table,
            )
            continue
        if any(child(element, key) is not None for key in ("PrintLayout", "PageSection")):
            issue(
                "unsupported-table-formatting",
                "Source print geometry and headers/footers stay in XML; "
                "native table/report formatting applies.",
                table,
            )
        table.group_name = (
            iteration.get("groupName", "workspaceSelection")
            if iteration is not None
            else "workspaceSelection"
        )
        if table.group_name in {"", "workspaceSelection", "All Samples"}:
            table.source_ids = [s.id for s in sources]
        else:
            matches = [g for g in groups if g["name"] == table.group_name]
            if len(matches) != 1:
                issue("unsupported-table-group", "The source group is missing or ambiguous.", table)
                continue
            table.source_ids = matches[0]["source_ids"]
        if set(table.source_ids) - {s.id for s in sources}:
            issue(
                "unsupported-table-group",
                "The source group references unavailable sample definitions.",
                table,
            )
            continue
        native, aliases, occupied = [], {}, set()
        for column_index, element_column in enumerate(columns):
            attributes = dict(element_column.attrib)
            original_name = attributes.get("columnRename") or attributes.get("rename")
            label = original_name or " · ".join(
                filter(
                    None,
                    (
                        attributes.get("analysisPath"),
                        attributes.get("name") or attributes.get("statistic"),
                        attributes.get("parameterName"),
                    ),
                )
            )
            label = _name(label, occupied)
            try:
                _flag(attributes, "iscontrolvalue")
                column = TableColumn(
                    name=label,
                    kind="metadata"
                    if attributes.get("valueType") == "keyword"
                    else "formula"
                    if attributes.get("valueType") == "formula"
                    else "statistic",
                    hidden=_flag(attributes, "ishiddencolumn") or _flag(attributes, "hideValue"),
                    heatmap=_flag(attributes, "heatMap"),
                    **(
                        {
                            "metadata_source": "keywords",
                            "metadata_key": attributes.get("keyword", ""),
                        }
                        if attributes.get("valueType") == "keyword"
                        else {"expression": convert_formula(attributes.get("formula", ""))}
                        if attributes.get("valueType") == "formula"
                        else {"statistic": "count"}
                    ),
                )
                if attributes.get("valueType", "statistic") not in {
                    "",
                    "statistic",
                    "keyword",
                    "formula",
                }:
                    raise ValueError(f"Unsupported column type {attributes['valueType']}")
                if column.kind == "statistic":
                    statistic = STATISTICS.get(
                        (attributes.get("statistic") or attributes.get("name", "")).casefold()
                    )
                    if statistic is None:
                        raise ValueError(f"Unsupported statistic {attributes.get('statistic')}")
                    path = attributes.get("analysisPath", "")
                    if not path:
                        raise ValueError("A statistic column requires an explicit population path")
                    column.population_path = [] if path == "Ungated" else path.split("/")
                    column.statistic = statistic
                    column.decimals = 0 if statistic == "count" else 2
                    if statistic not in {"count", "percent_parent", "percent_total"}:
                        parameter = attributes.get("parameterName", "")
                        if not parameter:
                            raise ValueError("A channel statistic requires an explicit parameter")
                        names = {
                            s.parameter_aliases.get(parameter, parameter)
                            for s in sources
                            if s.id in table.source_ids
                        }
                        if len(names) != 1:
                            raise ValueError(
                                "This parameter has inconsistent names across the source panels"
                            )
                        column.channel = names.pop()
                        if statistic == "percentile":
                            if not attributes.get("percentile"):
                                raise ValueError("Percentile requires an explicit percentile value")
                            column.percentile = float(attributes["percentile"])
                        issue(
                            "native-table-statistics",
                            f"{label} uses full-event acquisition units. FlowJo's "
                            "transform/binning conventions may differ; CV uses absolute mean.",
                            table,
                        )
                # Revalidate every edited scalar and population path.
                column = TableColumn.model_validate(column.model_dump())
                native.append(column)
                table.column_sources[column.id] = attributes
                for alias in {original_name, attributes.get("name"), label} - {None, ""}:
                    aliases.setdefault(alias, []).append(column.id)
            except (ValueError, TypeError, OverflowError) as exc:
                issue("unsupported-table-column", f"Column {column_index + 1}: {exc}", table)
        retained = []
        for column in native:
            try:
                if column.kind == "formula":
                    references = parse_table_formula(column.expression)[1]
                    for alias in references:
                        if len(aliases.get(alias, [])) != 1:
                            raise ValueError(f"Column reference {alias} is missing or ambiguous")
                    column.formula_refs = {alias: aliases[alias][0] for alias in references}
                retained.append(column)
            except ValueError as exc:
                issue("unsupported-table-formula", f"{column.name}: {exc}", table)
        while True:
            identifiers = {c.id for c in retained}
            missing = [c for c in retained if set(c.formula_refs.values()) - identifiers]
            if not missing:
                break
            for column in missing:
                issue(
                    "unsupported-table-formula",
                    f"{column.name} depends on an unconverted column.",
                    table,
                )
            retained = [c for c in retained if c not in missing]
        if not retained:
            continue
        try:
            table.definition = TableDefinition(
                name=table.name[:160],
                row_mode="samples",
                columns=retained,
                sample_order="selection",
                sort_by="input",
                provenance={"format": "flowjo", "external_table_id": table.id},
            )
        except ValueError as exc:
            issue("unsupported-table", str(exc), table)
        if any(c.kind == "metadata" for c in retained):
            issue(
                "table-keywords",
                "Keywords read current target annotations and acquisition metadata; "
                "original workspace keywords remain in XML.",
                table,
                "info",
            )
    return tables


def apply_workspace_tables(workspace, plan, request, record):
    from .interchange import Issue

    record.report["tables"] = []
    record.report["tables_requested"] = request.include_tables
    if not request.include_tables or not plan.tables:
        return
    occupied = {t.name.casefold() for t in workspace.tables}
    sources = {s.id: s for s in plan.sources}
    imported = [g for g in workspace.gates if g.provenance.get("interchange_id") == record.id]
    by_path = {}
    for gate in imported:
        by_path.setdefault((gate.sample_id, "/".join(gate.provenance["external_path"])), []).append(
            gate.id
        )
    for table in plan.tables:
        if table.definition is None:
            continue
        selected = [
            sample for source in table.source_ids for sample in record.mappings.get(source, [])
        ]
        if not selected:
            record.report["issues"].append(
                Issue(
                    code="unmapped-table-scope",
                    message=f"{table.name}: no mapped source samples; table was not created.",
                ).model_dump()
            )
            continue
        definition = table.definition.model_copy(deep=True)
        definition.id = new_id()
        definition.name = _name(definition.name, occupied)
        definition.sample_ids = selected
        definition.provenance["interchange_id"] = record.id
        bindings = []
        for column in definition.columns:
            attributes = table.column_sources[column.id]
            path = attributes.get("analysisPath", "")
            missing = []
            if column.kind == "statistic":
                for source_id in table.source_ids:
                    source = sources[source_id]
                    for sample_id in record.mappings.get(source_id, []):
                        if path == "Ungated":
                            column.population_overrides[sample_id] = None
                        else:
                            matches = by_path.get((sample_id, path), [])
                            if len(matches) == 1:
                                column.population_overrides[sample_id] = matches[0]
                            else:
                                column.population_unavailable.append(sample_id)
                                missing.append(sample_id)
                        if column.channel:
                            original = attributes["parameterName"]
                            reference = source.parameter_compensations.get(
                                original, "uncompensated"
                            )
                            if reference != "uncompensated":
                                aliases = record.report["source_parameter_aliases"][source_id].get(
                                    reference, {}
                                )
                                if column.channel in aliases:
                                    column.channel_overrides[sample_id] = aliases[column.channel]
                                reference = record.report["source_compensations"][source_id][
                                    reference
                                ]
                            column.compensation_overrides[sample_id] = reference
            if _flag(attributes, "iscontrolvalue"):
                controls = record.mappings.get(table.source_ids[0], []) if table.source_ids else []
                column.control_sample_id = controls[0] if controls else None
                column.control_unavailable = not controls
            if missing or column.control_unavailable:
                record.report["issues"].append(
                    Issue(
                        code="table-binding-unavailable",
                        message=f"{table.name} / {column.name}: source populations or the "
                        "first source control were not mapped. Affected cells remain undefined.",
                    ).model_dump()
                )
            bindings.append(
                {
                    "column_id": column.id,
                    "name": column.name,
                    "source": attributes,
                    "population_overrides": column.population_overrides,
                    "population_unavailable": column.population_unavailable,
                    "compensation_overrides": column.compensation_overrides,
                    "channel_overrides": column.channel_overrides,
                    "control_sample_id": column.control_sample_id,
                    "control_unavailable": column.control_unavailable,
                }
            )
        definition = TableDefinition.model_validate(definition.model_dump())
        workspace.tables.append(definition)
        record.table_ids.append(definition.id)
        record.report["tables"].append(
            {
                "id": definition.id,
                "name": definition.name,
                "source_id": table.id,
                "source_name": table.name,
                "group_name": table.group_name,
                "sample_ids": selected,
                "total_source_columns": table.total_columns,
                "converted_columns": len(definition.columns),
                "bindings": bindings,
            }
        )
