"""Atomic linked partitions expressed as ordinary half-open GatingML intervals."""

from .models import Gate, new_id


def partition_dimensions(gate: Gate, member: int):
    """Keep every scientific axis definition, replacing only interval endpoints."""
    if gate.partition.kind in {"spider", "curly"}:
        return [dimension.model_copy(deep=True) for dimension in gate.dimensions]
    partition = gate.partition.model_copy(update={"member": member})
    result = []
    for axis, dimension in enumerate(gate.dimensions):
        threshold = dimension.minimum if gate.partition.high(axis) else dimension.maximum
        high = partition.high(axis)
        result.append(
            dimension.model_copy(
                update={
                    "minimum": threshold if high else None,
                    "maximum": None if high else threshold,
                },
                deep=True,
            )
        )
    return result


def expand_partition(gate: Gate) -> list[Gate]:
    if not gate.partition:
        return [gate]
    size = 2 if gate.partition.kind == "bisector" else 4
    result = []
    for member in range(1, size + 1):
        suffix = (" −" if member == 1 else " +") if size == 2 else f" Q{member}"
        result.append(
            gate.model_copy(
                update={
                    "id": gate.id if member == gate.partition.member else new_id(),
                    "name": gate.name[: 160 - len(suffix)] + suffix,
                    "partition": gate.partition.model_copy(update={"member": member}),
                    "dimensions": partition_dimensions(gate, member),
                },
                deep=True,
            )
        )
    return result


def replace_gate(gates: list[Gate], gate: Gate, *, create: bool = False) -> list[Gate]:
    original = next((item for item in gates if item.id == gate.id), None)
    if original is None:
        if not create:
            raise KeyError("Gate not found")
        if gate.partition and any(
            item.partition and item.partition.id == gate.partition.id for item in gates
        ):
            raise ValueError("Partition ID already exists")
        return [*gates, *expand_partition(gate)]
    if create:
        raise ValueError("Gate ID already exists")
    if gate.sample_id != original.sample_id:
        raise ValueError("Gate sample cannot be changed")
    if gate.partition != original.partition:
        raise ValueError("Partition membership cannot be changed while editing a population")
    result = []
    for item in gates:
        if item.id == gate.id:
            result.append(gate)
        elif gate.partition and item.partition and item.partition.id == gate.partition.id:
            result.append(
                item.model_copy(
                    update={
                        "parent_id": gate.parent_id,
                        "x": gate.x,
                        "y": gate.y,
                        "x_transform": gate.x_transform,
                        "y_transform": gate.y_transform,
                        "dimensions": partition_dimensions(gate, item.partition.member),
                        "spider": gate.spider.model_copy(deep=True) if gate.spider else None,
                        "curly": gate.curly.model_copy(deep=True) if gate.curly else None,
                    },
                    deep=True,
                )
            )
        else:
            result.append(item)
    return result
