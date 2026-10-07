"""Immutable captured event identities bound to an acquired sample."""

import hashlib
import os
from types import SimpleNamespace

import numpy as np

from .analysis import _sample_signature
from .models import Gate, PopulationMembership
from .store import now


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def validate_binding(sample, data):
    if (
        data.sample_id != sample.id
        or data.raw_sha256 != sample.sha256
        or data.event_count != sample.event_count
        or data.acquisition_channels != [c.name for c in sample.acquisition_channels]
    ):
        raise ValueError("Captured population does not match its acquired sample")


def packed_data(engine, workspace, sample, data):
    validate_binding(sample, data)
    raw = engine.store.data_path(workspace.id, sample.id)
    stamp = raw.stat()
    raw_key = (
        workspace.id,
        "membership-acquisition",
        sample.id,
        sample.sha256,
        stamp.st_size,
        stamp.st_mtime_ns,
        stamp.st_ctime_ns,
        stamp.st_ino,
    )
    if engine.cache.get(raw_key) is None:
        if digest(raw) != data.raw_sha256:
            raise ValueError("Captured population acquisition failed its integrity check")
        engine.cache.put(raw_key, np.ones(1, dtype=bool))
    path = engine.store.membership_path(workspace.id, data.id)
    if not path.is_file() or digest(path) != data.sha256:
        raise ValueError("Captured population data failed its integrity check")
    with path.open("rb") as stream:
        packed = np.load(stream, allow_pickle=False)
        if not isinstance(packed, np.ndarray):
            packed.close()
            raise ValueError("Captured population requires a packed event array")
        if stream.read(1):
            raise ValueError("Captured population contains trailing data")
    if packed.dtype != np.uint8 or packed.shape != ((data.event_count + 7) // 8,):
        raise ValueError("Captured population data have an invalid shape or encoding")
    if data.event_count % 8 and int(packed[-1]) >> (data.event_count % 8):
        raise ValueError("Captured population contains event identities beyond its sample")
    if int(np.bitwise_count(packed).sum()) != data.selected_count:
        raise ValueError("Captured population count does not match its event identities")
    return packed


def load(engine, workspace, sample, data):
    packed = packed_data(engine, workspace, sample, data)
    selected = np.unpackbits(packed, count=data.event_count, bitorder="little").astype(bool)
    selected.flags.writeable = False
    return selected


def verify_dependencies(engine, workspace, sample, identifier):
    if not any(g.membership for g in workspace.gates):
        return
    gates = {g.id: g for g in workspace.gates}
    pending, visited = [identifier], set()
    while pending:
        value = pending.pop()
        if value is None or value in visited:
            continue
        visited.add(value)
        gate = gates.get(value)
        if gate is None or gate.sample_id != sample.id:
            raise ValueError("Population dependencies must belong to the same sample")
        if gate.membership is not None:
            packed_data(engine, workspace, sample, gate.membership)
        pending.extend([gate.parent_id, *gate.operands])


def capture(workspace, sample_id, gate_id, name, compensated, engine):
    name = name.strip()
    if not name:
        raise ValueError("Enter a population snapshot name")
    sample = engine.sample(workspace, sample_id)
    engine.raw(workspace, sample)
    raw = engine.store.data_path(workspace.id, sample.id)
    if digest(raw) != sample.sha256:
        raise ValueError("Acquired population data failed its integrity check")
    selection = engine.mask(workspace, sample, gate_id, compensated=compensated)
    source = _sample_signature(
        workspace,
        SimpleNamespace(channels=[], use_transforms=False, compensated=compensated),
        SimpleNamespace(sample_id=sample_id, gate_id=gate_id),
    )
    data = PopulationMembership(
        sample_id=sample.id,
        raw_sha256=sample.sha256,
        event_count=sample.event_count,
        selected_count=int(np.count_nonzero(selection)),
        acquisition_channels=[c.name for c in sample.acquisition_channels],
        sha256="0" * 64,
    )
    target = engine.store.membership_path(workspace.id, data.id)
    temporary = target.with_suffix(".partial")
    try:
        with temporary.open("xb") as stream:
            np.save(stream, np.packbits(selection, bitorder="little"), allow_pickle=False)
        if digest(raw) != sample.sha256:
            raise ValueError("Acquired population data changed during capture")
        data.sha256 = digest(temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return Gate(
        sample_id=sample.id,
        name=name,
        kind="membership",
        membership=data,
        provenance=dict(
            kind="captured_population",
            source_gate_id=gate_id,
            source_gate_name=next(
                (g.name for g in workspace.gates if g.id == gate_id), "All events"
            ),
            source_snapshot=source,
            captured_at=now(),
            selection_basis="compensated" if compensated else "acquired",
            membership_basis="captured_acquired_event_identities",
        ),
    )
