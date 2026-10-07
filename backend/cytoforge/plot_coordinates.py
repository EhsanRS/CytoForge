"""Preserve scientific dimension identity when native axes repeat a parameter."""

from __future__ import annotations

from .models import GateDimension, PlotDimension


def resolve_dimension(doc, sample, name, gate=None, axis=None, explicit=None, transform=None):
    """Resolve and validate one axis independently, including virtual ratio labels."""
    if name is None:
        if explicit is not None:
            raise ValueError("A coordinate definition requires a selected parameter")
        return None
    if explicit is not None:
        dim = PlotDimension.model_validate(
            explicit.model_dump() if isinstance(explicit, GateDimension) else explicit
        )
        if dim.channel != name:
            raise ValueError("Coordinate definition does not match the selected parameter")
    else:
        dim = coordinate_dimension(gate, name, axis)
        if dim is None:
            channel = next((c for c in sample.channels if c.name == name), None)
            if channel is None:
                raise ValueError(f"Parameter {name} is unavailable in this sample")
            dim = GateDimension(channel=name, transform=channel.transform)
        dim = dim.model_copy(update={"minimum": None, "maximum": None})
    channels = {c.name for c in sample.channels}
    if not set(dim.ratio_channels or (dim.channel,)) <= channels:
        raise ValueError(f"Coordinate {name} references unavailable input channels")
    if dim.compensation_ref not in {"sample", "uncompensated", "FCS"}:
        matrix = next((m for m in doc.compensations if m.id == dim.compensation_ref), None)
        if matrix is None:
            raise ValueError("Coordinate references a missing compensation matrix")
        if not set(matrix.detectors) <= {c.name for c in sample.acquisition_channels}:
            raise ValueError("Coordinate compensation detectors do not match this sample")
    return dim.model_copy(update={"transform": transform}) if transform else dim


def coordinate_dimension(gate, channel, axis=None):
    if gate is None or channel is None:
        return None
    dimensions = gate.dimensions
    if axis is not None and axis < len(dimensions) and dimensions[axis].channel == channel:
        return dimensions[axis]
    candidates = [d for d in dimensions if d.channel == channel]
    if not candidates:
        return None
    definition = candidates[0].model_copy(update={"minimum": None, "maximum": None})
    if any(
        d.model_copy(update={"minimum": None, "maximum": None}) != definition
        for d in candidates[1:]
    ):
        raise ValueError(
            f"Parameter {channel} has multiple coordinate definitions; "
            "use its original axis or leave gate coordinates."
        )
    return candidates[0]


def match_dimension_axes(dimensions, axes, basis):
    """Match each dimension to a distinct compatible axis, preferring native order."""
    positions, used = [], set()
    for ordinal, dimension in enumerate(dimensions):
        candidates = [
            i for i, axis in enumerate(axes) if i not in used and basis(dimension) == basis(axis)
        ]
        position = ordinal if ordinal in candidates else next(iter(candidates), None)
        positions.append(position)
        if position is not None:
            used.add(position)
    return positions
