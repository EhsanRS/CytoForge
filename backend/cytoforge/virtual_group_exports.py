"""Chunked pooled population files with qualified original member event identities."""

from __future__ import annotations

import csv
import hashlib
import shutil
from pathlib import Path

import numpy as np

from . import concatenation
from .models import Channel, ConcatenationProvenance, ConcatenationSource, Transform
from .report_sources import SourceAudit
from .science import transform
from .store import now
from .virtual_groups import PooledEngine


def source(doc, engine, request):
    scope = request.scope
    if scope.anchor_id != request.sample_id or scope.gate_id != request.gate_id:
        raise ValueError("The pooled export must match its representative sample and population")
    view = PooledEngine(doc, engine, scope.anchor_id, scope.group_id, scope.sample_filter)
    if len(view.members) > 128:
        raise ValueError("A pooled event file supports up to 128 source samples")
    names = [name for name in view.common if name not in {"CF_Source", "CF_EventID"}]
    if request.values == "raw":
        names = [
            name
            for name in names
            if all(
                sample.aliases.get(name, name) in {c.name for c in sample.acquisition_channels}
                for sample in view.members
            )
        ]
        ordered = [n for n in names if n in view.anchor.aliases] + [
            n for n in names if n not in view.anchor.aliases
        ]
        names, bindings = [], set()
        for name in ordered:
            binding = tuple(s.aliases.get(name, name) for s in view.members)
            if binding not in bindings:
                names.append(name)
                bindings.add(binding)
    if not names:
        raise ValueError("The pooled export has no common parameters in this value space")
    view.validate(names, [request.gate_id])
    mapping = dict(view.mapping(request.gate_id))
    spaces = [
        sample.concatenation.values
        if sample.concatenation
        else sample.event_export.values
        if sample.event_export
        else "raw"
        for sample in view.members
    ]
    if "scale" in spaces and any(space != "scale" for space in spaces):
        raise ValueError(
            "Already scaled files cannot be pooled with acquisition-space measurements"
        )
    effective = request.values
    if request.values == "raw":
        if len(set(spaces)) != 1:
            raise ValueError(
                "Stored value spaces differ. Export compensated values "
                "or choose matching source files"
            )
        effective = spaces[0]
    elif request.values == "compensated" and set(spaces) == {"scale"}:
        effective = "scale"
    matrix = None
    if request.values == "raw":
        raw_request = concatenation.Request(
            revision=doc.revision,
            name=view.name,
            inputs=[dict(sample_id=s.id, gate_id=mapping[s.id]) for s in view.members],
            parameters=[dict(name=n, sources={s.id: n for s in view.members}) for n in names],
        )
        matrix = concatenation.mapped_matrix(
            doc, raw_request, [(i, s, None) for i, s in enumerate(view.members, 1)]
        )
        if matrix:
            effective = "raw"
    audit, rows = SourceAudit(doc), []
    for sample in view.members:
        snapshot = audit.closure(sample.id, names, [mapping[sample.id]])
        snapshot.update(
            metadata=dict(sample.metadata),
            tags=dict(sample.tags),
            concatenation=sample.concatenation.model_dump() if sample.concatenation else None,
            event_export=sample.event_export.model_dump() if sample.event_export else None,
        )
        rows.append(
            dict(
                sample_id=sample.id,
                sample_name=sample.name,
                gate_id=mapping[sample.id],
                event_count=sample.event_count,
                snapshot=snapshot,
            )
        )
    channels = [
        next(c for c in view.anchor.channels if c.name == n).model_copy(deep=True) for n in names
    ]
    if request.values == "scale":
        for channel in channels:
            channel.transform = Transform()
    channels.extend(
        [
            Channel(name="CF_Source", label="Original group member", range=129),
            Channel(
                name="CF_EventID",
                label="Original member event index",
                range=max(s.event_count for s in view.members) + 1,
            ),
        ]
    )
    sample = view.view.model_copy(
        update={
            "aliases": {},
            "derived_parameters": [],
            "computed_parameters": [],
            "unmixed_parameters": [],
            "channels": channels,
            "compensation_id": matrix.id if matrix else None,
        }
    )
    return (
        sample,
        channels,
        dict(
            workspace_id=doc.id,
            revision=doc.revision,
            pooled_group=view.descriptor(request.gate_id),
            sources=rows,
            models={},
            effective_values=effective,
            matrix=matrix.model_dump() if matrix else None,
        ),
    )


def write(store, engine, doc, request, directory, progress, check):
    from .event_exports import (
        METADATA_KEY,
        Envelope,
        encode,
        fcs_prefix,
        npy_header,
        verify_sources,
    )
    from .models import Compensation

    sample, channels, snapshot = source(doc, engine, request)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    verify_sources(store, engine, doc, sample, snapshot, check)
    rows = snapshot["sources"]
    display_transforms = {
        c.name: c.transform for c in engine.sample(doc, request.sample_id).channels
    }
    masks = {
        r["sample_id"]: engine.mask(doc, engine.sample(doc, r["sample_id"]), r["gate_id"])
        for r in rows
    }
    count = sum(int(mask.sum()) for mask in masks.values())
    if shutil.disk_usage(directory).free < count * len(channels) * 16 + 16 * 1024**2:
        raise ValueError("Insufficient storage to prepare the complete pooled event export")
    body, output = directory / "events.data", directory / ("population." + request.format)
    data_hash, origins_hash = hashlib.sha256(), hashlib.sha256(npy_header((count, 2), "<u8"))
    lineage, written = [], 0
    try:
        with body.open("wb") as handle:
            for source_index, row in enumerate(rows, 1):
                original = engine.sample(doc, row["sample_id"])
                selected = masks[original.id]
                retained = int(selected.sum())
                lineage.append(
                    ConcatenationSource(
                        index=source_index,
                        sample_id=original.id,
                        sample_name=original.name,
                        gate_id=row["gate_id"],
                        event_count=original.event_count,
                        offset=written,
                        count=retained,
                        parameters={c.name: c.name for c in channels[:-2]},
                        snapshot=row["snapshot"],
                    )
                )
                for start in range(0, original.event_count, concatenation.CHUNK_EVENTS):
                    check()
                    stop = min(start + concatenation.CHUNK_EVENTS, original.event_count)
                    mask = selected[start:stop]
                    indices = np.flatnonzero(mask) + start
                    if not len(indices):
                        continue
                    if np.any(indices > 2**53):
                        raise ValueError(
                            "FCS double precision cannot preserve event IDs above 2^53"
                        )
                    chunk = concatenation.ChunkColumns(
                        engine, doc, original, start, stop, request.values != "raw"
                    )
                    values = np.empty((len(indices), len(channels)), dtype="<f8")
                    for i, channel in enumerate(channels[:-2]):
                        column = chunk.column(channel.name)
                        if request.values == "scale":
                            column = transform(column, display_transforms[channel.name])
                        values[:, i] = column[mask]
                    identities = np.column_stack(
                        [np.full(len(indices), source_index, dtype="<u8"), indices.astype("<u8")]
                    )
                    values[:, -2:] = identities
                    encoded = values.tobytes(order="C")
                    handle.write(encoded)
                    data_hash.update(encoded)
                    origins_hash.update(identities.tobytes(order="C"))
                    written += len(indices)
                    progress(
                        stage="Writing pooled event values",
                        events_written=written,
                        event_total=count,
                    )
        check()
        provenance = ConcatenationProvenance(
            workspace_id=doc.id,
            revision=doc.revision,
            created_at=now(),
            values=snapshot["effective_values"],
            sources=lineage,
            origins_sha256=origins_hash.hexdigest(),
        )
        matrix = Compensation.model_validate(snapshot["matrix"]) if snapshot["matrix"] else None
        envelope = Envelope(
            exported_at=now(),
            sample_name=sample.name,
            values=provenance.values,
            channels=channels,
            matrix=matrix,
            tags={},
            lineage=provenance,
            data_sha256=data_hash.hexdigest(),
            source_snapshot=snapshot,
        )
        if request.format == "fcs":
            metadata = {
                METADATA_KEY: encode(envelope),
                "cytoforge_source": sample.name,
                "cytoforge_value_space": provenance.values,
            }
            if matrix:
                if any("," in n for n in matrix.detectors):
                    raise ValueError(
                        "FCS spillover names cannot contain commas; choose compensated values"
                    )
                metadata["$SPILLOVER"] = ",".join(
                    [
                        str(len(matrix.detectors)),
                        *matrix.detectors,
                        *(format(v, ".17g") for row in matrix.matrix for v in row),
                    ]
                )
            with output.open("wb") as handle, body.open("rb") as events:
                handle.write(fcs_prefix(channels, count, metadata))
                while block := events.read(1024 * 1024):
                    check()
                    handle.write(block)
        else:
            with (
                output.open("w", newline="", encoding="utf-8") as handle,
                body.open("rb") as events,
            ):
                writer = csv.writer(handle)
                writer.writerow(
                    "'" + c.name if c.name.startswith(("=", "+", "-", "@", "\t", "\r")) else c.name
                    for c in channels
                )
                while block := events.read(concatenation.CHUNK_EVENTS * len(channels) * 8):
                    check()
                    writer.writerows(
                        np.frombuffer(block, dtype="<f8").reshape(-1, len(channels)).tolist()
                    )
        verify_sources(store, engine, doc, sample, snapshot, check)
        progress(stage="Checking prepared pooled file", events_written=count, event_total=count)
        return output, dict(
            event_count=count,
            channel_count=len(channels),
            channels=[c.name for c in channels],
            values=provenance.values,
            format=request.format,
            matrix_preserved=matrix is not None and request.format == "fcs",
            exact_event_origins=True,
            pooled_sample_count=len(rows),
        )
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    finally:
        body.unlink(missing_ok=True)
