"""Save acquired gate dependencies and captured reviewed QC selections."""

import numpy as np

from .models import GateDimension, new_id


def acquired_gate_copies(workspace, name, provenance):
    source = {g.id: g for g in workspace.gates}
    copied, created = {}, []

    def append(gate, name):
        existing = {
            g.name
            for g in workspace.gates
            if g.sample_id == gate.sample_id and g.parent_id == gate.parent_id
        }
        base, suffix = name[:160], 1
        while name[:160] in existing:
            suffix += 1
            ending = f" ({suffix})"
            name = base[: 160 - len(ending)] + ending
        gate.name = name[:160]
        gate.provenance.update(provenance, coordinate_basis="acquired")
        workspace.gates.append(gate)
        created.append(gate.id)
        return gate.id

    def raw_parent(identifier):
        if not identifier:
            return None
        if identifier in copied:
            return copied[identifier]
        original = source[identifier]
        gate = original.model_copy(deep=True)
        gate.id = new_id()
        gate.provenance = {"source_gate_id": original.id, "source_gate_name": original.name}
        gate.parent_id = raw_parent(original.parent_id)
        gate.operands = [raw_parent(value) for value in original.operands]
        dims = gate.dimensions
        if not dims and gate.kind not in {"container", "boolean", "quality", "membership"}:
            names = [gate.x] + ([gate.y] if gate.y else [])
            specs = [gate.x_transform, gate.y_transform]
            dims = [GateDimension(channel=n, transform=specs[i]) for i, n in enumerate(names)]
            if gate.kind == "range":
                dims[0].minimum, dims[0].maximum = gate.bounds
                gate.kind = "hyperrectangle"
            elif gate.kind == "rectangle":
                dims[0].minimum, dims[0].maximum, dims[1].minimum, dims[1].maximum = gate.bounds
                gate.kind = "hyperrectangle"
            elif gate.kind == "quadrant":
                right, upper = gate.quadrant in {2, 3}, gate.quadrant in {1, 2}
                for axis, high in enumerate((right, upper)):
                    dims[axis].minimum = gate.bounds[axis] if high else None
                    dims[axis].maximum = None if high else gate.bounds[axis]
                gate.kind = "hyperrectangle"
            elif gate.kind == "ellipse":
                cosine, sine = np.cos(gate.angle), np.sin(gate.angle)
                rotation = np.array([[cosine, -sine], [sine, cosine]])
                gate.coordinates = list(gate.center)
                gate.covariance = (rotation @ np.diag(np.square(gate.radii)) @ rotation.T).tolist()
                gate.distance_square = 1
                gate.kind = "ellipsoid"
        for dim in dims:
            dim.compensation_ref = "uncompensated"
        gate.dimensions = dims
        if gate.kind == "quality":
            qc_result = next(q for q in workspace.quality_results if q.id == gate.quality_id)
            gate.provenance.update(
                qc_selection_basis="captured_reviewed_event_flags",
                qc_input_hash=qc_result.input_hash,
                qc_data_sha256=qc_result.data.sha256,
            )
        label = "Captured QC" if gate.kind == "quality" else "Raw"
        copied[identifier] = append(gate, f"{name[:64]} · {label} {original.name[:80]}")
        return gate.id

    return append, raw_parent, created
