"""Lossless GatingML 2.0 export for representable native gate strategies."""

from __future__ import annotations

import json
import math

import numpy as np
from lxml import etree

from .interchange import D, G, T, schema
from .models import GateDimension

C = "https://cytoforge.local/interchange/v1"


def export_gatingml(workspace, sample, engine=None):
    root = etree.Element(
        f"{{{G}}}Gating-ML", nsmap={"gating": G, "transforms": T, "data-type": D, "cytoforge": C}
    )
    matrices = {m.id: m for m in workspace.compensations}
    native_gates = {f"gate_{gate.id}": gate for gate in workspace.gates}
    transforms, emitted_matrices = {}, set()
    sequence = 0

    def element(parent, namespace, tag_name, **attributes):
        return etree.SubElement(
            parent,
            f"{{{namespace}}}{tag_name}",
            {f"{{{namespace}}}{k}": str(v) for k, v in attributes.items() if v is not None},
        )

    def helper_id():
        nonlocal sequence
        sequence += 1
        return f"helper_{sequence}"

    def gate_element(kind, identifier, parent=None, name=None):
        e = element(root, G, kind, id=identifier, **{"parent_id": parent})
        info = element(e, D, "custom_info")
        etree.SubElement(
            info,
            f"{{{C}}}population",
            name=name or identifier,
            helper="true" if identifier.startswith("helper_") else "false",
        )
        native = native_gates.get(identifier)
        if native and native.partition:
            etree.SubElement(
                info,
                f"{{{C}}}linked_partition",
                **{key: str(value) for key, value in native.partition.model_dump().items()},
            )
        return e

    def matrix_ref(reference):
        ref = sample.compensation_id if reference == "sample" else reference
        if not ref or ref == "uncompensated":
            return "uncompensated"
        if ref == "FCS":
            return ref
        matrix = matrices[ref]
        identifier = f"matrix_{ref}"
        if ref not in emitted_matrices:
            if (matrix.background and any(matrix.background)) or (
                matrix.weights and len(set(matrix.weights)) > 1
            ):
                raise ValueError(
                    f"{matrix.name}: GatingML cannot encode background subtraction "
                    "or weighted unmixing"
                )
            if len(matrix.outputs) < 2 or len(matrix.detectors) < 2:
                raise ValueError(
                    f"{matrix.name}: GatingML spectrum matrices require at least two dimensions"
                )
            e = element(root, T, "spectrumMatrix", id=identifier)
            for kind, names in [("fluorochromes", matrix.outputs), ("detectors", matrix.detectors)]:
                group = element(e, T, kind)
                for name in names:
                    element(group, D, "fcs-dimension", name=name)
            for row in matrix.matrix:
                spectrum = element(e, T, "spectrum")
                for value in row:
                    element(spectrum, T, "coefficient", value=value)
            emitted_matrices.add(ref)
        return identifier

    def transform_ref(spec):
        if spec.kind == "linear" and spec.bound_min is None and spec.bound_max is None:
            return None
        key = spec.model_dump_json()
        if key in transforms:
            return transforms[key]
        if spec.kind == "linear":
            kind, values = "flin", {"T": 1, "A": 0}
        elif spec.kind == "log":
            kind, values = "flog", {"T": 10, "M": 1}
        elif spec.kind == "asinh":
            kind, values = (
                "fasinh",
                {"T": spec.cofactor * math.sinh(1), "M": 1 / math.log(10), "A": 0},
            )
        elif spec.kind == "gml_linear":
            kind, values = "flin", {"T": spec.t, "A": spec.a}
        elif spec.kind == "gml_log":
            kind, values = "flog", {"T": spec.t, "M": spec.m}
        elif spec.kind == "gml_asinh":
            kind, values = "fasinh", {"T": spec.t, "M": spec.m, "A": spec.a}
        elif spec.kind in {"logicle", "hyperlog"}:
            kind, values = spec.kind, {"T": spec.t, "W": spec.w, "M": spec.m, "A": spec.a}
        else:
            raise ValueError(
                f"GatingML cannot exactly represent {spec.kind}. "
                "Download the original source or portable project to preserve these gates."
            )
        identifier = f"transform_{len(transforms) + 1}"
        transforms[key] = identifier
        e = element(
            root,
            T,
            "transformation",
            id=identifier,
            boundMin=spec.bound_min,
            boundMax=spec.bound_max,
        )
        element(e, T, kind, **values)
        return identifier

    def dimension(parent, dim, bounds=False):
        ref = matrix_ref(dim.compensation_ref)
        attrs = {"compensation-ref": ref, "transformation-ref": transform_ref(dim.transform)}
        if bounds:
            attrs.update(min=dim.minimum, max=dim.maximum)
        e = element(parent, G, "dimension", **attrs)
        names = tuple(sample.aliases.get(n, n) for n in (dim.ratio_channels or (dim.channel,)))
        allowed = {c.name for c in sample.acquisition_channels}
        if ref not in {"uncompensated", "FCS"}:
            allowed.update(matrices[ref.removeprefix("matrix_")].outputs)
        if not set(names) <= allowed:
            raise ValueError(
                f"{dim.channel}: export derived or computed gate parameters "
                "as event data before exchanging gates"
            )
        if dim.ratio_channels:
            identifier = helper_id()
            ratio = element(
                element(
                    root,
                    T,
                    "transformation",
                    id=identifier,
                    boundMin=dim.ratio_bound_min,
                    boundMax=dim.ratio_bound_max,
                ),
                T,
                "fratio",
                A=dim.ratio_a,
                B=dim.ratio_b,
                C=dim.ratio_c,
            )
            for name in names:
                element(ratio, D, "fcs-dimension", name=name)
            element(e, D, "new-dimension", **{"transformation-ref": identifier})
        else:
            element(e, D, "fcs-dimension", name=names[0])

    def bool_gate(identifier, operation, operands, parent=None, name=None):
        if len(operands) == 1 and operation in {"and", "or"}:
            operands = operands * 2
        gate_node = gate_element("BooleanGate", identifier, parent, name)
        e = element(gate_node, G, operation)
        for ref, complement in operands:
            element(
                e,
                G,
                "gateReference",
                ref=ref,
                **{"use-as-complement": "true" if complement else None},
            )
        return gate_node

    for gate in [g for g in workspace.gates if g.sample_id == sample.id]:
        if gate.kind == "curly":
            raise ValueError(
                "Unbounded curly noise boundaries cannot be represented exactly "
                "by standard GatingML. "
                "Export the selected events or a portable project to retain the full geometry."
            )
        if gate.kind == "spider":
            raise ValueError(
                "Unbounded spider dividers cannot be represented exactly by standard GatingML. "
                "Export the selected events or a portable project to retain the full geometry."
            )
        magnetic = None
        original = gate
        if gate.magnetic is not None:
            if engine is None:
                raise ValueError("Magnetic GatingML snapshots require the event engine")
            gate, magnetic = engine.resolve_gate(workspace, sample, gate)
        if gate.kind in {"quality", "membership"}:
            raise ValueError(
                "Captured population and QC event identities cannot be represented by GatingML. "
                "Export the gated FCS/CSV or a portable project instead."
            )
        identifier, parent = f"gate_{gate.id}", f"gate_{gate.parent_id}" if gate.parent_id else None
        geometry_id = helper_id() if gate.complement else identifier
        geometry_parent = None if gate.complement else parent
        if gate.kind == "boolean":
            operands = [
                (f"gate_{o}", c)
                for o, c in zip(
                    gate.operands,
                    gate.operand_complements or [False] * len(gate.operands),
                    strict=True,
                )
            ]
            operation = gate.operation
            if operation == "xor":
                current = operands[0]
                for other in operands[1:]:
                    a, b, joined = helper_id(), helper_id(), helper_id()
                    bool_gate(a, "and", [current, (other[0], not other[1])])
                    bool_gate(b, "and", [(current[0], not current[1]), other])
                    bool_gate(joined, "or", [(a, False), (b, False)])
                    current = joined, False
                operation, operands = "and", [current]
            bool_gate(geometry_id, operation, operands, geometry_parent, gate.name)
        elif gate.kind == "container":
            empty = helper_id()
            e = gate_element("RectangleGate", empty)
            dimension(
                e,
                GateDimension(
                    channel=sample.acquisition_channels[0].name,
                    compensation_ref="uncompensated",
                    minimum=0,
                    maximum=0,
                ),
                True,
            )
            bool_gate(geometry_id, "not", [(empty, False)], geometry_parent, gate.name)
        else:
            dims = [d.model_copy(deep=True) for d in gate.dimensions] or [
                GateDimension(channel=gate.x, transform=gate.x_transform),
                *([GateDimension(channel=gate.y, transform=gate.y_transform)] if gate.y else []),
            ]
            if gate.kind in {"rectangle", "range"}:
                for i, dim in enumerate(dims):
                    dim.minimum, dim.maximum = gate.bounds[2 * i : 2 * i + 2]
            if gate.kind == "quadrant":
                x, y = gate.bounds
                if gate.quadrant in {2, 3}:
                    dims[0].minimum = x
                else:
                    dims[0].maximum = x
                if gate.quadrant in {1, 2}:
                    dims[1].minimum = y
                else:
                    dims[1].maximum = y
            if gate.kind in {"rectangle", "range", "hyperrectangle", "quadrant"}:
                e = gate_element("RectangleGate", geometry_id, geometry_parent, gate.name)
                for dim in dims:
                    dimension(e, dim, True)
            elif gate.kind == "polygon":

                def polygon(identifier, parent, name, points, gate_dimensions=dims):
                    e = gate_element("PolygonGate", identifier, parent, name)
                    for dim in gate_dimensions:
                        dimension(e, dim)
                    for point in points:
                        vertex = element(e, G, "vertex")
                        for value in point:
                            etree.SubElement(
                                vertex, f"{{{G}}}coordinate", {f"{{{D}}}value": str(value)}
                            )
                    return e

                if gate.holes:
                    outer = helper_id()
                    polygon(outer, None, gate.name + " outline", gate.vertices)
                    operands = [(outer, False)]
                    for index, ring in enumerate(gate.holes):
                        excluded = helper_id()
                        polygon(excluded, None, f"{gate.name} excluded {index + 1}", ring)
                        operands.append((excluded, True))
                    e = bool_gate(geometry_id, "and", operands, geometry_parent, gate.name)
                else:
                    e = polygon(geometry_id, geometry_parent, gate.name, gate.vertices)
            else:
                e = gate_element("EllipsoidGate", geometry_id, geometry_parent, gate.name)
                for dim in dims:
                    dimension(e, dim)
                if gate.kind == "ellipse":
                    cosine, sine = math.cos(gate.angle), math.sin(gate.angle)
                    rotation = np.array([[cosine, -sine], [sine, cosine]])
                    covariance = rotation @ np.diag(np.square(gate.radii)) @ rotation.T
                    center, distance = gate.center, 1
                else:
                    covariance, center, distance = (
                        gate.covariance,
                        gate.coordinates,
                        gate.distance_square,
                    )
                mean = element(e, G, "mean")
                for value in center:
                    etree.SubElement(mean, f"{{{G}}}coordinate", {f"{{{D}}}value": str(value)})
                cov = element(e, G, "covarianceMatrix")
                for row in covariance:
                    row_element = element(cov, G, "row")
                    for value in row:
                        etree.SubElement(
                            row_element, f"{{{G}}}entry", {f"{{{D}}}value": str(value)}
                        )
                etree.SubElement(e, f"{{{G}}}distanceSquare", {f"{{{D}}}value": str(distance)})
        if gate.complement:
            bool_gate(identifier, "not", [(geometry_id, False)], parent, gate.name)
        if magnetic is not None:
            info = e.find(f"{{{D}}}custom_info")
            etree.SubElement(
                info,
                f"{{{C}}}magnetic_snapshot",
                behavior="static-snapshot",
                workspace_revision=str(workspace.revision),
                sample_id=sample.id,
                anchor_gate=original.model_dump_json(),
                resolution=json.dumps(magnetic),
            )
    validator = schema()
    if not validator.validate(root):
        raise ValueError(
            f"GatingML export failed schema validation: {validator.error_log.last_error}"
        )
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True, pretty_print=True)
