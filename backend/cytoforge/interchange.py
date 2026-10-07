"""Safe, explicit conversion of GatingML 2.0 and FlowJo workspace gate trees."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from urllib.parse import unquote, urlparse

import numpy as np
from defusedxml import ElementTree as safe_xml
from lxml import etree
from pydantic import Field

from .models import (
    Compensation,
    Gate,
    GateDimension,
    GatePartition,
    Group,
    Id,
    InterchangeRecord,
    Model,
    Transform,
    Workspace,
    new_id,
)
from .science import transform, validate_matrix
from .wsp_tables import ImportedTable, apply_workspace_tables, parse_workspace_tables

G = "http://www.isac-net.org/std/Gating-ML/v2.0/gating"
T = "http://www.isac-net.org/std/Gating-ML/v2.0/transformations"
D = "http://www.isac-net.org/std/Gating-ML/v2.0/datatypes"
ZERO = "0" * 32
MAX_XML_BYTES = 32 * 1024**2


class ImportMapping(Model):
    source_id: str = Field(min_length=1, max_length=160)
    sample_ids: list[Id] = Field(min_length=1, max_length=128)


class ImportApply(Model):
    revision: int = Field(ge=0)
    preview_id: Id
    mappings: list[ImportMapping] = Field(min_length=1, max_length=1024)
    include_display_settings: bool = True
    include_tables: bool = True
    include_keywords: bool = True
    replace_gates: bool = False
    allow_partial: bool = False


class Issue(Model):
    severity: str = "warning"
    code: str
    message: str
    source_id: str | None = None
    gate: str | None = None


class ImportSource(Model):
    id: str
    name: str
    file_name: str = ""
    event_count: int | None = None
    gates: list[Gate] = Field(default_factory=list)
    matrices: list[Compensation] = Field(default_factory=list)
    display_transforms: dict[str, Transform] = Field(default_factory=dict)
    metadata: dict[str, str] = Field(default_factory=dict)
    channels: list[str] = Field(default_factory=list)
    suggested_sample_ids: list[Id] = Field(default_factory=list)
    compatible_sample_ids: list[Id] = Field(default_factory=list)
    total_gates: int = 0
    parameter_aliases: dict[str, str] = Field(default_factory=dict)
    parameter_compensations: dict[str, Id] = Field(default_factory=dict)


class ImportDocument(Model):
    format: str
    name: str
    sha256: str
    sources: list[ImportSource]
    groups: list[dict] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    version: str = ""
    tables: list[ImportedTable] = Field(default_factory=list)


def tag(element) -> str:
    return element.tag.rsplit("}", 1)[-1] if isinstance(element.tag, str) else ""


def children(element, name):
    return [e for e in element if tag(e) == name] if element is not None else []


def child(element, name):
    return next(iter(children(element, name)), None)


def attr(element, name, namespace=None, default=None):
    if element is None:
        return default
    return (
        element.get(f"{{{namespace}}}{name}", element.get(name, default))
        if namespace
        else element.get(name, default)
    )


def number(element, name, namespace=None, default=None):
    value = attr(element, name, namespace, default)
    if value is None:
        raise ValueError(f"Missing {name} attribute")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def bound(element, name):
    value = attr(element, name, G)
    if value is None:
        return None
    if value == ("-INF" if name == "min" else "INF"):
        return None
    return number(element, name, G)


def boolean(value, default=False):
    if value is None:
        return default
    if value not in {"true", "false", "0", "1"}:
        raise ValueError("Invalid XML Boolean value")
    return value in {"true", "1"}


def safe_root(data: bytes):
    if not data or len(data) > MAX_XML_BYTES:
        raise ValueError("Gate XML must contain at most 32 MiB")
    try:
        safe_xml.fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
        parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
        root = etree.fromstring(data, parser)
    except Exception as exc:
        raise ValueError(f"Unsafe or malformed gate XML: {str(exc)[:300]}") from exc
    stack = [(root, 0)]
    count = 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if depth > 256 or count > 200000:
            raise ValueError("XML exceeds the supported depth or element count")
        stack.extend((c, depth + 1) for c in node)
    return root


def schema():
    path = Path(__file__).parent / "resources/gatingml/Gating-ML.v2.0.xsd"
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    return etree.XMLSchema(etree.parse(str(path), parser))


def parse_transform(element, wsp=False) -> Transform | dict:
    kind = tag(element)
    if kind == "transformation":
        items = [e for e in element if tag(e) != "custom_info"]
        if len(items) != 1:
            raise ValueError("A transformation must contain exactly one function")
        spec = parse_transform(items[0])
        minimum = attr(element, "boundMin", T)
        maximum = attr(element, "boundMax", T)
        minimum = None if minimum is None or minimum == "-INF" else number(element, "boundMin", T)
        maximum = None if maximum is None or maximum == "INF" else number(element, "boundMax", T)
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError("Transformation boundMin cannot exceed boundMax")
        if isinstance(spec, dict):
            return spec | {"ratio_bound_min": minimum, "ratio_bound_max": maximum}
        return Transform.model_validate(
            spec.model_dump() | {"bound_min": minimum, "bound_max": maximum}
        )
    if kind in {"flin", "linear"}:
        return Transform(
            kind="gml_linear",
            t=number(element, "maxRange" if wsp else "T", T),
            a=number(element, "minRange" if wsp else "A", T),
        )
    if kind == "flog":
        return Transform(kind="gml_log", t=number(element, "T", T), m=number(element, "M", T))
    if kind == "log" and wsp:
        return Transform(
            kind="wsp_log", offset=number(element, "offset", T), m=number(element, "decades", T)
        )
    if kind in {"logicle", "hyperlog"}:
        return Transform(
            kind=kind,
            t=number(element, "T", T),
            m=number(element, "M", T),
            w=number(element, "W", T),
            a=number(element, "A", T),
        )
    if kind == "fasinh":
        return Transform(
            kind="gml_asinh",
            t=number(element, "T", T),
            m=number(element, "M", T),
            a=number(element, "A", T),
        )
    if kind == "fratio":
        channels = [attr(e, "name", D) for e in children(element, "fcs-dimension")]
        if len(channels) != 2 or any(not c for c in channels):
            raise ValueError("A ratio needs exactly two FCS channels")
        return {
            "ratio_channels": channels,
            "ratio_a": number(element, "A", T),
            "ratio_b": number(element, "B", T),
            "ratio_c": number(element, "C", T),
        }
    if kind == "biex" and wsp:
        if number(element, "length", T) != 256:
            raise ValueError("FlowJo biex length other than 256 requires reference validation")
        spec = Transform(
            kind="wsp_biex",
            negative=number(element, "neg", T),
            width=number(element, "width", T),
            positive=number(element, "pos", T),
            top=number(element, "maxRange", T),
        )
        transform(np.array([0.0]), spec)  # Validate the actual lookup table now.
        return spec
    raise ValueError(f"Unsupported transform {kind}")


def parse_matrix(element, wsp=False):
    if wsp:
        parameters = [
            attr(e, "name", D) for e in children(child(element, "parameters"), "parameter")
        ]
        rows = children(element, "spillover")
        spectral = attr(element, "spectral") == "1"
        if spectral and attr(element, "weightOptAlgorithmType", default="OLS") != "OLS":
            raise ValueError("This FlowJo spectral weighting algorithm is not supported")
        if not rows:
            raise ValueError("Empty spillover matrix")
        detectors = (
            [attr(e, "parameter", D) for e in children(rows[0], "coefficient")]
            if spectral
            else parameters
        )
        outputs = parameters if spectral else detectors
        values = [[number(e, "value", T) for e in children(row, "coefficient")] for row in rows]
        external_id = attr(element, "id", T, attr(element, "name"))
    else:
        detectors = [
            attr(e, "name", D) for e in children(child(element, "detectors"), "fcs-dimension")
        ]
        outputs = [
            attr(e, "name", D) for e in children(child(element, "fluorochromes"), "fcs-dimension")
        ]
        values = [
            [number(e, "value", T) for e in children(row, "coefficient")]
            for row in children(element, "spectrum")
        ]
        external_id = attr(element, "id", T)
        spectral = len(outputs) != len(detectors)
        if boolean(attr(element, "matrix-inverted-already", T)):
            if spectral:
                raise ValueError(
                    "An inverted rectangular spectrum matrix cannot be represented "
                    "as a reference spectrum"
                )
            values = np.linalg.inv(np.asarray(values)).tolist()
    if not external_id or not detectors or any(not n for n in detectors + outputs):
        raise ValueError("Matrix IDs and channel names must be nonempty")
    if len(outputs) != len(values):
        raise ValueError("Spectrum row count must match the fluorochrome names")
    if len(set(outputs)) != len(outputs):
        raise ValueError("Matrix output names must be unique")
    # Square compensation outputs are associated with acquired detector slots;
    # GatingML fluorochrome names may be aliases of those slots.
    internal_outputs = outputs if spectral else detectors
    matrix = Compensation(
        name=attr(element, "name", default=external_id),
        kind="spectral" if spectral else "spillover",
        detectors=detectors,
        outputs=internal_outputs,
        matrix=values,
        source="FlowJo workspace" if wsp else "GatingML 2.0",
        provenance={
            "format": "flowjo" if wsp else "gatingml",
            "external_id": external_id,
            "fluorochromes": outputs,
        },
    )
    validate_matrix(matrix)
    aliases = dict(zip(outputs, internal_outputs, strict=True))
    aliases.update({n: n for n in internal_outputs})
    return external_id, matrix, aliases


def coordinates(element):
    return [number(e, "value", D) for e in children(element, "coordinate")]


def parse_dimension(element, transforms, matrices, matrix_aliases):
    comp_ref = attr(element, "compensation-ref", G, "uncompensated")
    if comp_ref not in {"uncompensated", "FCS"}:
        if comp_ref not in matrices:
            raise ValueError(f"Unknown compensation reference {comp_ref}")
        comp_id = matrices[comp_ref].id
    else:
        comp_id = comp_ref
    fcs = child(element, "fcs-dimension")
    ratio_el = child(element, "new-dimension")
    ratio = None
    if fcs is not None:
        name = attr(fcs, "name", D)
        name = matrix_aliases.get(comp_ref, {}).get(name, name)
    elif ratio_el is not None:
        name = attr(ratio_el, "transformation-ref", D)
        ratio = transforms.get(name)
        if not isinstance(ratio, dict):
            raise ValueError(f"Unknown ratio transformation {name}")
        ratio = ratio | {
            "ratio_channels": [
                matrix_aliases.get(comp_ref, {}).get(n, n) for n in ratio["ratio_channels"]
            ]
        }
    else:
        raise ValueError("Dimension lacks a channel or ratio reference")
    xform_id = attr(element, "transformation-ref", G)
    spec = transforms.get(xform_id, Transform())
    if xform_id and xform_id not in transforms:
        raise ValueError(f"Unknown transformation {xform_id}")
    if not isinstance(spec, Transform):
        raise ValueError("A ratio transformation belongs in a new-dimension reference")
    return GateDimension(
        channel=name,
        transform=spec,
        compensation_ref=comp_id,
        minimum=bound(element, "min"),
        maximum=bound(element, "max"),
        **(ratio or {}),
    )


def parse_gml(root, name, digest):
    validator = schema()
    if not validator.validate(root):
        raise ValueError(f"GatingML 2.0 schema validation failed: {validator.error_log.last_error}")
    issues, transforms, matrices, aliases = [], {}, {}, {}
    for e in children(root, "transformation"):
        transforms[attr(e, "id", T)] = parse_transform(e)
        if attr(e, "boundMin", T) is not None or attr(e, "boundMax", T) is not None:
            issues.append(
                Issue(
                    code="bounded-transformation",
                    severity="info",
                    message="Transformation output bounds are preserved and applied before gating.",
                )
            )
    for e in children(root, "spectrumMatrix"):
        identifier, matrix, lut = parse_matrix(e)
        matrices[identifier], aliases[identifier] = matrix, lut
    gate_elements = [e for e in root if tag(e).endswith("Gate")]
    identifiers = {attr(e, "id", G): new_id() for e in gate_elements}
    for e in gate_elements:
        if tag(e) == "QuadrantGate":
            identifiers.update({attr(q, "id", G): new_id() for q in children(e, "Quadrant")})
    gates = []
    for e in gate_elements:
        identifier = attr(e, "id", G)
        parent = attr(e, "parent_id", G)
        if parent and parent not in identifiers:
            raise ValueError(f"Missing parent gate {parent}")
        data = dict(
            id=identifiers[identifier],
            sample_id=ZERO,
            name=identifier,
            parent_id=identifiers.get(parent),
            provenance={"format": "gatingml", "external_id": identifier},
        )
        info = child(e, "custom_info")
        if info is not None:
            label = info.find("{https://cytoforge.local/interchange/v1}population")
            if label is not None:
                data["name"] = attr(label, "name", default=identifier)[:160]
                data["provenance"]["export_helper"] = attr(label, "helper") == "true"
            snapshot = info.find("{https://cytoforge.local/interchange/v1}magnetic_snapshot")
            partition = info.find("{https://cytoforge.local/interchange/v1}linked_partition")
            if partition is not None:
                data["partition"] = GatePartition.model_validate(
                    {
                        **dict(partition.attrib),
                        "member": int(attr(partition, "member", default="0")),
                    }
                )
            if snapshot is not None:
                data["provenance"]["magnetic_snapshot"] = dict(snapshot.attrib)
                issues.append(
                    Issue(
                        code="magnetic-snapshot",
                        severity="info",
                        gate=data["name"],
                        message="This gate is a static snapshot of a resolved magnetic position. "
                        "The portable project preserves automatic population following.",
                    )
                )
        kind = tag(e)
        if kind == "BooleanGate":
            operation = next(x for x in e if tag(x) in {"and", "or", "not"})
            refs = children(operation, "gateReference")
            missing = [attr(r, "ref", G) for r in refs if attr(r, "ref", G) not in identifiers]
            if missing:
                raise ValueError(f"Missing Boolean gate references: {', '.join(missing)}")
            gates.append(
                Gate(
                    **data,
                    kind="boolean",
                    operation=tag(operation),
                    operands=[identifiers[attr(r, "ref", G)] for r in refs],
                    operand_complements=[boolean(attr(r, "use-as-complement", G)) for r in refs],
                )
            )
            continue
        if kind == "QuadrantGate":
            gates.append(Gate(**data, kind="container"))
            dividers = {}
            for divider in children(e, "divider"):
                dim = parse_dimension(divider, transforms, matrices, aliases)
                cuts = sorted(float(v.text) for v in children(divider, "value"))
                if not all(math.isfinite(v) for v in cuts):
                    raise ValueError("Quadrant divider cuts must be finite")
                if len(set(cuts)) != len(cuts):
                    raise ValueError("Quadrant divider cuts must be unique")
                dividers[attr(divider, "id", G)] = dim, cuts
            for q in children(e, "Quadrant"):
                q_id = attr(q, "id", G)
                dims = []
                for position in children(q, "position"):
                    ref = attr(position, "divider_ref", G)
                    if ref not in dividers:
                        raise ValueError(f"Unknown quadrant divider {ref}")
                    dimension, cuts = dividers[ref]
                    location = number(position, "location", G)
                    if location in cuts:
                        raise ValueError(
                            "Quadrant location must be inside an interval, not on a cut"
                        )
                    interval = int(np.searchsorted(cuts, location))
                    dims.append(
                        dimension.model_copy(
                            update={
                                "minimum": cuts[interval - 1] if interval else None,
                                "maximum": cuts[interval] if interval < len(cuts) else None,
                            }
                        )
                    )
                gates.append(
                    Gate(
                        id=identifiers[q_id],
                        sample_id=ZERO,
                        name=q_id,
                        parent_id=data["id"],
                        kind="hyperrectangle",
                        dimensions=dims,
                        provenance={
                            "format": "gatingml",
                            "external_id": q_id,
                            "quadrant_container": identifier,
                        },
                    )
                )
            continue
        dims = [parse_dimension(d, transforms, matrices, aliases) for d in children(e, "dimension")]
        axes = dict(
            x=dims[0].channel,
            y=dims[1].channel if len(dims) > 1 else None,
            x_transform=dims[0].transform,
            y_transform=dims[1].transform if len(dims) > 1 else Transform(),
        )
        if kind == "RectangleGate":
            gate = Gate(**data, **axes, kind="hyperrectangle", dimensions=dims)
        elif kind == "PolygonGate":
            gate = Gate(
                **data,
                **axes,
                kind="polygon",
                dimensions=dims,
                vertices=[coordinates(v) for v in children(e, "vertex")],
            )
        elif kind == "EllipsoidGate":
            covariance = [
                [number(v, "value", D) for v in children(row, "entry")]
                for row in children(child(e, "covarianceMatrix"), "row")
            ]
            gate = Gate(
                **data,
                **axes,
                kind="ellipsoid",
                dimensions=dims,
                coordinates=coordinates(child(e, "mean")),
                covariance=covariance,
                distance_square=number(child(e, "distanceSquare"), "value", D),
            )
            values = np.asarray(covariance)
            if (
                not np.allclose(values, values.T)
                or np.min(np.linalg.eigvalsh((values + values.T) / 2)) <= 0
            ):
                issues.append(
                    Issue(
                        code="general-quadratic-form",
                        gate=gate.name,
                        message=(
                            "Source covariance is not symmetric positive definite; "
                            "its original quadratic form is preserved exactly"
                        ),
                    )
                )
        else:
            raise ValueError(f"Unsupported GatingML gate {kind}")
        gates.append(gate)
    used = sorted(
        {
            c
            for gate in gates
            for dim in gate.dimensions
            for c in dim.ratio_channels or (dim.channel,)
        }
    )
    source = ImportSource(
        id="template",
        name="Gating strategy",
        gates=gates,
        matrices=list(matrices.values()),
        channels=used,
        total_gates=len(gates),
    )
    return ImportDocument(
        format="gatingml", name=name, sha256=digest, sources=[source], issues=issues, version="2.0"
    )


def wsp_ellipse(element, dims):
    foci = np.asarray([coordinates(v) for v in children(child(element, "foci"), "vertex")]) / 256
    edge = np.asarray([coordinates(v) for v in children(child(element, "edge"), "vertex")]) / 256
    if foci.shape != (2, 2) or edge.shape != (4, 2) or len(dims) != 2:
        raise ValueError("FlowJo ellipse requires two foci, four edge points and two axes")
    center = foci.mean(axis=0)
    direction = foci[1] - foci[0]
    angle = float(np.arctan2(direction[1], direction[0]))
    cosine, sine = np.cos(angle), np.sin(angle)
    rotation = np.array([[cosine, -sine], [sine, cosine]])
    points = (edge - center) @ rotation
    major = max(abs(points[0]).max(), abs(points[2]).max())
    focus_distance = np.linalg.norm(foci[0] - center)
    if major <= 0 or focus_distance >= major:
        raise ValueError("Invalid FlowJo ellipse foci or major radius")
    minor = math.sqrt(major**2 - focus_distance**2)
    # Account for display scaling: FlowJo biex coordinates span 4096, others 1.
    scaling = np.diag([4096 if d.transform.kind == "wsp_biex" else 1 for d in dims])
    inverse_rotation = rotation.T
    covariance = (
        scaling @ inverse_rotation.T @ np.diag([major**2, minor**2]) @ inverse_rotation @ scaling
    )
    return (center @ scaling).tolist(), covariance.tolist()


def parse_wsp(root, name, digest):
    version = attr(root, "flowJoVersion", default="")
    if not version or int(version.split(".")[0]) < 10:
        raise ValueError("This FlowJo format requires a FlowJo 10 or later workspace")
    sources, issues, groups = [], [], []
    for sample_index, sample_el in enumerate(children(child(root, "SampleList"), "Sample")):
        dataset, node = child(sample_el, "DataSet"), child(sample_el, "SampleNode")
        if node is None:
            issues.append(
                Issue(
                    code="missing-sample-node",
                    severity="error",
                    message="A sample has no sample node",
                )
            )
            continue
        source_id = attr(
            node, "sampleID", default=attr(dataset, "sampleID", default=str(sample_index + 1))
        )
        source_name = attr(node, "name", default=f"Sample {source_id}")
        uri = attr(dataset, "uri", default="")
        file_name = unquote(urlparse(uri).path).replace("\\", "/").rsplit("/", 1)[-1] or source_name
        keywords = {
            attr(k, "name"): attr(k, "value", default="")
            for k in children(child(sample_el, "Keywords"), "Keyword")
        }
        event_count = keywords.get("$TOT") or attr(node, "count")
        source = ImportSource(
            id=source_id,
            name=source_name,
            file_name=file_name,
            event_count=int(event_count) if event_count and int(event_count) >= 0 else None,
            metadata=keywords,
        )
        xforms, bad_xforms = {}, set()
        for xform_el in (
            child(sample_el, "Transformations")
            if child(sample_el, "Transformations") is not None
            else []
        ):
            channel = attr(child(xform_el, "parameter"), "name", D)
            try:
                xforms[channel] = parse_transform(xform_el, wsp=True)
            except (ValueError, OverflowError) as exc:
                bad_xforms.add(channel)
                issues.append(
                    Issue(
                        code="unsupported-transform",
                        severity="error",
                        source_id=source_id,
                        message=f"{channel}: {exc}",
                    )
                )
        matrix, prefix, suffix = None, "", ""
        matrix_elements = children(sample_el, "spilloverMatrix")
        bad_matrix = False
        if len(matrix_elements) > 1:
            bad_matrix = True
            issues.append(
                Issue(
                    code="multiple-matrices",
                    severity="error",
                    source_id=source_id,
                    message="Multiple sample compensation matrices require an explicit selection",
                )
            )
        elif matrix_elements:
            try:
                _, matrix, _ = parse_matrix(matrix_elements[0], wsp=True)
                source.matrices.append(matrix)
                prefix, suffix = (
                    attr(matrix_elements[0], "prefix", default=""),
                    attr(matrix_elements[0], "suffix", default=""),
                )
                for parameter in matrix.outputs:
                    decorated = prefix + parameter + suffix
                    source.parameter_aliases[decorated] = parameter
                    source.parameter_compensations[decorated] = matrix.id
            except (ValueError, np.linalg.LinAlgError) as exc:
                bad_matrix = True
                issues.append(
                    Issue(
                        code="unsupported-matrix",
                        severity="error",
                        source_id=source_id,
                        message=str(exc),
                    )
                )

        def dimension(
            element,
            prefix=prefix,
            suffix=suffix,
            bad_xforms=bad_xforms,
            xforms=xforms,
            matrix=matrix,
            bad_matrix=bad_matrix,
        ):
            original = attr(child(element, "fcs-dimension"), "name", D)
            if not original:
                raise ValueError("FlowJo dimension has no parameter name")
            channel = original
            decorated = False
            if prefix and channel.startswith(prefix):
                channel, decorated = channel[len(prefix) :], True
            if suffix and channel.endswith(suffix):
                channel, decorated = channel[: -len(suffix)], True
            if original in bad_xforms or channel in bad_xforms:
                raise ValueError(f"{channel} uses an unsupported transformation")
            spec = xforms.get(original, xforms.get(channel, Transform()))
            if not isinstance(spec, Transform):
                raise ValueError("FlowJo ratio dimension needs an explicit parameter definition")
            compensation = "uncompensated"
            if matrix and channel in matrix.outputs and (decorated or not prefix and not suffix):
                compensation = matrix.id
            if bad_matrix and (decorated or channel in xforms):
                raise ValueError("Gate depends on an unsupported compensation matrix")
            low, high = bound(element, "min"), bound(element, "max")
            low = float(transform(np.asarray([low]), spec)[0]) if low is not None else None
            high = float(transform(np.asarray([high]), spec)[0]) if high is not None else None
            return GateDimension(
                channel=channel,
                transform=spec,
                compensation_ref=compensation,
                minimum=low,
                maximum=high,
            )

        pending, ids, blocked = [], {}, set()

        def visit(
            subpop,
            parent_path=(),
            source_id=source_id,
            source=source,
            ids=ids,
            blocked=blocked,
            pending=pending,
            dimension=dimension,
        ):
            for population in subpop if subpop is not None else []:
                node_kind = tag(population)
                if node_kind not in {"Population", "AndNode", "OrNode", "NotNode"}:
                    if node_kind not in {"Statistic"}:
                        issues.append(
                            Issue(
                                code="unsupported-node",
                                source_id=source_id,
                                message=f"Unsupported analysis node {node_kind}",
                            )
                        )
                    else:
                        issues.append(
                            Issue(
                                code="unsupported-statistics-definition",
                                source_id=source_id,
                                message=(
                                    "FlowJo statistic definitions are retained in source XML; "
                                    "live statistics use CytoForge definitions"
                                ),
                            )
                        )
                    continue
                label = attr(population, "name", default=node_kind)
                path = parent_path + (label,)
                source.total_gates += 1
                identifier = new_id()
                if path in ids:
                    raise ValueError(f"Duplicate population path {'/'.join(path)}")
                ids[path] = identifier
                if parent_path in blocked:
                    blocked.add(path)
                    issues.append(
                        Issue(
                            code="unsupported-parent",
                            severity="error",
                            source_id=source_id,
                            gate="/".join(path),
                            message="Parent population could not be converted",
                        )
                    )
                else:
                    try:
                        provenance = {
                            "format": "flowjo",
                            "external_path": list(path),
                            "reported_count": attr(population, "count"),
                        }
                        base = dict(
                            id=identifier,
                            sample_id=ZERO,
                            name=label,
                            parent_id=ids.get(parent_path),
                            provenance=provenance,
                        )
                        if node_kind in {"AndNode", "OrNode", "NotNode"}:
                            refs = [
                                tuple(attr(e, "name").strip("/").split("/"))
                                for e in children(child(population, "Dependents"), "Dependent")
                            ]
                            pending.append((path, base, node_kind, refs))
                        else:
                            wrapper = child(population, "Gate")
                            shapes = [e for e in wrapper if tag(e)] if wrapper is not None else []
                            if len(shapes) != 1:
                                raise ValueError("Population does not have a unique gate shape")
                            shape = shapes[0]
                            if any(
                                key.rsplit("}", 1)[-1].casefold()
                                in {"magnetic", "ismagnetic", "magneticgate"}
                                and boolean(value)
                                for node in (population, wrapper, shape)
                                for key, value in node.attrib.items()
                            ):
                                raise ValueError(
                                    "FlowJo magnetic following uses an unspecified algorithm. "
                                    "Review this gate in the original workspace before conversion."
                                )
                            kind = tag(shape)
                            dims = [dimension(d) for d in children(shape, "dimension")]
                            axes = dict(
                                x=dims[0].channel if dims else None,
                                y=dims[1].channel if len(dims) > 1 else None,
                                x_transform=dims[0].transform if dims else Transform(),
                                y_transform=dims[1].transform if len(dims) > 1 else Transform(),
                            )
                            complement = attr(shape, "eventsInside", default="1") == "0"
                            if kind == "RectangleGate":
                                gate = Gate(
                                    **base,
                                    **axes,
                                    kind="hyperrectangle",
                                    dimensions=dims,
                                    complement=complement,
                                )
                            elif kind == "PolygonGate":
                                points = np.asarray(
                                    [coordinates(v) for v in children(shape, "vertex")]
                                )
                                if points.ndim != 2 or points.shape[1] != 2 or len(dims) != 2:
                                    raise ValueError("FlowJo polygon requires two axes")
                                points = np.column_stack(
                                    [
                                        transform(points[:, i], d.transform)
                                        for i, d in enumerate(dims)
                                    ]
                                )
                                gate = Gate(
                                    **base,
                                    **axes,
                                    kind="polygon",
                                    dimensions=dims,
                                    vertices=points.tolist(),
                                    complement=complement,
                                )
                            elif kind == "EllipsoidGate":
                                center, covariance = wsp_ellipse(shape, dims)
                                gate = Gate(
                                    **base,
                                    **axes,
                                    kind="ellipsoid",
                                    dimensions=dims,
                                    coordinates=center,
                                    covariance=covariance,
                                    complement=complement,
                                )
                            else:
                                raise ValueError(f"Unsupported FlowJo gate shape {kind}")
                            source.gates.append(gate)
                    except (ValueError, IndexError, TypeError, OverflowError) as exc:
                        blocked.add(path)
                        issues.append(
                            Issue(
                                code="unsupported-gate",
                                severity="error",
                                source_id=source_id,
                                gate="/".join(path),
                                message=str(exc)[:500],
                            )
                        )
                visit(child(population, "Subpopulations"), path)

        visit(child(node, "Subpopulations"))
        while pending:
            remaining = []
            progress = False
            for path, base, kind, refs in pending:
                if any(ref not in ids or ref in blocked for ref in refs):
                    blocked.add(path)
                    issues.append(
                        Issue(
                            code="missing-boolean-reference",
                            severity="error",
                            source_id=source_id,
                            gate="/".join(path),
                            message="Boolean operand is missing or unsupported",
                        )
                    )
                    progress = True
                elif any(ref in {p[0] for p in pending} for ref in refs):
                    remaining.append((path, base, kind, refs))
                else:
                    source.gates.append(
                        Gate(
                            **base,
                            kind="boolean",
                            operation={"AndNode": "and", "OrNode": "or", "NotNode": "not"}[kind],
                            operands=[ids[ref] for ref in refs],
                        )
                    )
                    progress = True
            if not progress:
                raise ValueError("FlowJo Boolean dependencies contain a cycle")
            pending = remaining
        excluded = {ids[p] for p in blocked if p in ids}
        while True:
            dependents = {
                g.id for g in source.gates if g.parent_id in excluded or set(g.operands) & excluded
            } - excluded
            if not dependents:
                break
            excluded.update(dependents)
        for gate in source.gates:
            if gate.id in excluded and gate.id not in {ids[p] for p in blocked if p in ids}:
                issues.append(
                    Issue(
                        code="unsupported-gate-dependency",
                        severity="error",
                        source_id=source_id,
                        gate=gate.name,
                        message="Parent or Boolean operand could not be imported",
                    )
                )
        source.gates = [g for g in source.gates if g.id not in excluded]
        source.channels = sorted(
            {
                c
                for gate in source.gates
                for dim in gate.dimensions
                for c in dim.ratio_channels or (dim.channel,)
            }
        )
        display = {}
        for original, spec in xforms.items():
            channel, decorated = original, False
            if prefix and channel.startswith(prefix):
                channel, decorated = channel[len(prefix) :], True
            if suffix and channel.endswith(suffix):
                channel, decorated = channel[: -len(suffix)], True
            if isinstance(spec, Transform) and (decorated or channel not in display):
                display[channel] = spec
        source.display_transforms = display
        sources.append(source)
    for node in children(child(root, "Groups"), "GroupNode"):
        group = child(node, "Group")
        refs = [attr(e, "sampleID") for e in children(child(group, "SampleRefs"), "SampleRef")]
        groups.append({"name": attr(node, "name", default="Imported group"), "source_ids": refs})
        if len(child(group, "Criteria")) if child(group, "Criteria") is not None else False:
            issues.append(
                Issue(
                    code="dynamic-group-criteria",
                    message=(
                        f"{attr(node, 'name')}: dynamic criteria retained in XML; "
                        "imported membership is a snapshot"
                    ),
                )
            )
    tables = parse_workspace_tables(root, sources, groups, issues)
    for table in tables:
        if table.definition is None:
            continue
        for source in sources:
            if source.id in table.source_ids:
                source.channels = sorted(
                    set(source.channels)
                    | {column.channel for column in table.definition.columns if column.channel}
                )
    for feature in (
        "LayoutEditor",
        "DerivedParameters",
        "CalculatedParameters",
        "CellCycle",
        "Proliferation",
        "Kinetics",
        "PluginNode",
        "Scripts",
    ):
        matches = [
            e for e in root.iter() if tag(e) == feature and (len(e) or (e.text or "").strip())
        ]
        if matches:
            issues.append(
                Issue(
                    code="unsupported-workspace-feature",
                    message=(
                        f"{feature}: {len(matches)} definition(s) retained in original XML; "
                        "not converted to a native analysis"
                    ),
                )
            )
    if not sources:
        raise ValueError("FlowJo workspace contains no usable sample definitions")
    return ImportDocument(
        format="flowjo",
        name=name,
        sha256=digest,
        sources=sources,
        groups=groups,
        issues=issues,
        version=version,
        tables=tables,
    )


def parse_document(data: bytes, name: str) -> ImportDocument:
    root = safe_root(data)
    digest = hashlib.sha256(data).hexdigest()
    if root.tag == f"{{{G}}}Gating-ML":
        plan = parse_gml(root, name, digest)
    elif tag(root) == "Workspace" and attr(root, "flowJoVersion"):
        plan = parse_wsp(root, name, digest)
    else:
        raise ValueError("Choose a GatingML 2.0 XML or FlowJo 10/11 workspace file")
    if len({s.id for s in plan.sources}) != len(plan.sources):
        raise ValueError("Source sample IDs must be unique")
    for source in plan.sources:
        gates = {g.id: g for g in source.gates}
        visiting, visited = set(), set()

        def visit(identifier, gates=gates, visiting=visiting, visited=visited):
            if identifier in visited:
                return
            if identifier not in gates:
                raise ValueError("Gate references a missing population")
            if identifier in visiting or len(visiting) > 256:
                raise ValueError("Gate dependencies contain a cycle or exceed 256 levels")
            visiting.add(identifier)
            gate = gates[identifier]
            for dependency in ([gate.parent_id] if gate.parent_id else []) + gate.operands:
                visit(dependency)
            visiting.remove(identifier)
            visited.add(identifier)

        for gate in source.gates:
            visit(gate.id)
    return plan


def needs_partial_acknowledgement(plan: ImportDocument):
    return any(
        i.severity == "error"
        or i.code.startswith("unsupported-")
        or i.code == "dynamic-group-criteria"
        or i.code == "native-table-statistics"
        for i in plan.issues
    )


def suggest_samples(plan: ImportDocument, workspace: Workspace):
    for source in plan.sources:
        virtual = {n for m in source.matrices if m.kind == "spectral" for n in m.outputs}
        required = (set(source.channels) - virtual) | {
            n for m in source.matrices for n in m.detectors
        }
        wanted = {source.name.casefold(), source.file_name.casefold()}
        for sample in workspace.samples:
            if required <= {c.name for c in sample.channels}:
                source.compatible_sample_ids.append(sample.id)
                names = {
                    sample.name.casefold(),
                    sample.metadata.get("cytoforge_import_filename", "").casefold(),
                    sample.metadata.get("fil", "").replace("\\", "/").rsplit("/", 1)[-1].casefold(),
                }
                if wanted & (names - {""}):
                    source.suggested_sample_ids.append(sample.id)
    return plan


def apply_document(workspace: Workspace, plan: ImportDocument, request: ImportApply, engine):
    """Mutate a transactional snapshot; never detach missing parents or operands."""
    from .compensation import assign_matrix
    from .store import now

    if needs_partial_acknowledgement(plan) and not request.allow_partial:
        raise ValueError("Review the report and acknowledge features that cannot be converted")
    sources = {s.id: s for s in plan.sources}
    if len(sources) != len(plan.sources):
        raise ValueError("Source sample IDs must be unique")
    if len({m.source_id for m in request.mappings}) != len(request.mappings):
        raise ValueError("Map each source sample only once")
    if any(m.source_id not in sources for m in request.mappings):
        raise ValueError("Mapping references an unknown source sample")
    target_ids = [i for m in request.mappings for i in m.sample_ids]
    if len(set(target_ids)) != len(target_ids):
        raise ValueError("Each target sample can receive only one source tree per import")
    if sum(len(sources[m.source_id].gates) * len(m.sample_ids) for m in request.mappings) > 20000:
        raise ValueError("Import at most 20,000 mapped gates in one operation")
    record = InterchangeRecord(
        format=plan.format,
        name=plan.name[:160],
        sha256=plan.sha256,
        created_at=now(),
        mappings={m.source_id: m.sample_ids for m in request.mappings},
        report={
            "version": plan.version,
            "issues": [i.model_dump() for i in plan.issues],
            "sources": [],
            "counts": [],
            "partial_acknowledged": request.allow_partial,
            "source_compensations": {},
            "source_parameter_aliases": {},
        },
    )
    for mapping in request.mappings:
        source = sources[mapping.source_id]
        if not source.gates and not (
            request.include_tables
            and any(t.definition is not None and source.id in t.source_ids for t in plan.tables)
        ):
            raise ValueError(f"{source.name} has no supported populations")
        targets = [engine.sample(workspace, i) for i in mapping.sample_ids]
        matrix_map, output_aliases = {}, {}
        occupied = {
            c.name for s in targets for c in s.channels if c.name not in s.unmixed_parameters
        }
        for original in source.matrices:
            matrix = original.model_copy(deep=True)
            matrix.id = new_id()
            matrix_map[original.id] = matrix
            if matrix.kind == "spectral":
                aliases = {}
                for name in matrix.outputs:
                    candidate, index = name, 1
                    while candidate in occupied or candidate in aliases.values():
                        candidate = f"Unmixed {name[:130]} {index}"
                        index += 1
                    aliases[name] = candidate
                matrix.outputs = [aliases[n] for n in matrix.outputs]
                output_aliases[original.id] = aliases
            validate_matrix(matrix)
            workspace.compensations.append(matrix)
        record.report["source_compensations"][source.id] = {
            original: converted.id for original, converted in matrix_map.items()
        }
        record.report["source_parameter_aliases"][source.id] = output_aliases
        record.report["sources"].append(
            {
                "id": source.id,
                "name": source.name,
                "file_name": source.file_name,
                "total_gates": source.total_gates,
                "supported_gates": len(source.gates),
                "sample_ids": mapping.sample_ids,
            }
        )
        for sample in targets:
            if request.include_keywords and plan.format == "flowjo":
                for key, value in source.metadata.items():
                    if key.startswith("$"):
                        continue
                    matches = [k for k in sample.tags if k.casefold() == key.casefold()]
                    if matches:
                        if len(matches) != 1 or sample.tags[matches[0]] != value:
                            record.report["issues"].append(
                                Issue(
                                    code="keyword-conflict",
                                    source_id=source.id,
                                    message=f"{sample.name}: existing annotation "
                                    f"{key} was preserved.",
                                ).model_dump()
                            )
                    else:
                        sample.tags[key] = value
            previous_matrix = sample.compensation_id
            for matrix in matrix_map.values():
                if matrix.kind == "spectral":
                    assign_matrix(sample, matrix)
            sample.compensation_id = previous_matrix
            if request.include_display_settings and plan.format == "flowjo":
                if source.matrices:
                    assign_matrix(sample, matrix_map[source.matrices[0].id])
                for channel in sample.channels:
                    original_name = next(
                        (
                            old
                            for aliases in output_aliases.values()
                            for old, new in aliases.items()
                            if new == channel.name
                        ),
                        channel.name,
                    )
                    if original_name in source.display_transforms:
                        channel.transform = source.display_transforms[original_name].model_copy()
            if request.replace_gates:
                workspace.gates = [g for g in workspace.gates if g.sample_id != sample.id]
            remap = {g.id: new_id() for g in source.gates}
            partition_remap = {g.partition.id: new_id() for g in source.gates if g.partition}
            for original in source.gates:
                gate = original.model_copy(deep=True)
                gate.id, gate.sample_id = remap[original.id], sample.id
                gate.parent_id = remap[original.parent_id] if original.parent_id else None
                gate.operands = [remap[o] for o in original.operands]
                if gate.partition:
                    gate.partition = gate.partition.model_copy(
                        update={"id": partition_remap[gate.partition.id]}
                    )
                for dim in gate.dimensions:
                    if dim.compensation_ref in matrix_map:
                        aliases = output_aliases.get(dim.compensation_ref, {})
                        dim.channel = aliases.get(dim.channel, dim.channel)
                        if dim.ratio_channels:
                            dim.ratio_channels = tuple(
                                aliases.get(n, n) for n in dim.ratio_channels
                            )
                        dim.compensation_ref = matrix_map[dim.compensation_ref].id
                if gate.dimensions:
                    gate.x = (
                        None if gate.dimensions[0].ratio_channels else gate.dimensions[0].channel
                    )
                    gate.y = (
                        gate.dimensions[1].channel
                        if len(gate.dimensions) > 1 and not gate.dimensions[1].ratio_channels
                        else None
                    )
                gate.provenance["interchange_id"] = record.id
                workspace.gates.append(gate)
                record.gate_ids.append(gate.id)
            if source.event_count is not None and source.event_count != sample.event_count:
                record.report["issues"].append(
                    Issue(
                        code="event-count-mismatch",
                        source_id=source.id,
                        message=(
                            f"{sample.name}: source contains {source.event_count} events; "
                            f"target contains {sample.event_count}"
                        ),
                    ).model_dump()
                )
    for group in plan.groups:
        members = sorted(
            {s for source_id in group["source_ids"] for s in record.mappings.get(source_id, [])}
        )
        if members:
            workspace.groups.append(Group(name=group["name"][:160], sample_ids=members))
    apply_workspace_tables(workspace, plan, request, record)
    checked = Workspace.model_validate(workspace.model_dump())
    checked.revision += 1
    # Candidate revisions can be reused after a failed transaction. Keep preview
    # masks/columns out of the live cache until the snapshot actually commits.
    from .science import Engine

    preview_engine = Engine(engine.store, cache_bytes=engine.cache.max_bytes)
    gate_lookup = {g.id: g for g in checked.gates}
    imported_ids = set(record.gate_ids)
    for sample_id in target_ids:
        sample = preview_engine.sample(checked, sample_id)
        for count in preview_engine.gate_counts(checked, sample):
            if count["id"] not in imported_ids:
                continue
            gate = gate_lookup[count["id"]]
            expected = gate.provenance.get("reported_count")
            if expected is not None and int(expected) < 0:
                expected = None
            record.report["counts"].append(
                count
                | {
                    "sample_id": sample_id,
                    "name": gate.name,
                    "reported_count": int(expected) if expected is not None else None,
                }
            )
            if expected is not None and int(expected) != count["count"]:
                record.report["issues"].append(
                    Issue(
                        code="gate-count-mismatch",
                        gate=gate.name,
                        message=(
                            f"{sample.name} / {gate.name}: source reports {expected}; "
                            f"CytoForge selects {count['count']} events"
                        ),
                    ).model_dump()
                )
    workspace.interchanges.append(record)
    return record
