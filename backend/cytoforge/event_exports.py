"""Bounded float64 event export and checked scientific FCS metadata."""

from __future__ import annotations

import base64
import copy
import csv
import hashlib
import io
import json
import re
import shutil
import threading
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import Field, model_serializer

from .analysis import atomic_json
from .concatenation import (
    CHUNK_EVENTS,
    Cancelled,
    ChunkColumns,
    digest,
    source_matrix,
    validate_origins,
)
from .models import (
    Channel,
    Compensation,
    ConcatenationProvenance,
    EventExportProvenance,
    Id,
    Model,
    Name,
    Sample,
    Transform,
    Workspace,
    new_id,
)
from .report_sources import SourceAudit
from .science import Engine, transform, validate_matrix
from .store import ConflictError, now
from .virtual_groups import Scope

METADATA_KEY = "cytoforge_event_export"
MAX_METADATA = 4 * 1024**2
MAX_DOCUMENT = 32 * 1024**2
SHA = r"^[0-9a-f]{64}$"


class Request(Model):
    revision: int = Field(ge=0)
    sample_id: Id
    gate_id: Id | None = None
    format: Literal["fcs", "csv"] = "fcs"
    values: Literal["raw", "compensated", "scale"] = "raw"
    scope: Scope | None = None

    @model_serializer(mode="wrap")
    def legacy(self, serializer):
        value = serializer(self)
        if self.scope is None:
            value.pop("scope", None)
        return value


class Envelope(Model):
    version: Literal[1] = 1
    exported_at: str
    sample_name: Name
    values: Literal["raw", "compensated", "scale"]
    channels: list[Channel] = Field(min_length=1, max_length=512)
    matrix: Compensation | None = None
    virtual_channels: list[Channel] = Field(default_factory=list, max_length=512)
    aliases: dict[Name, Name] = Field(default_factory=dict, max_length=128)
    alias_channels: list[Channel] = Field(default_factory=list, max_length=128)
    tags: dict[str, str]
    lineage: ConcatenationProvenance | None = None
    data_sha256: str = Field(pattern=SHA)
    source_snapshot: dict

    @model_serializer(mode="wrap")
    def serialize_legacy(self, handler):
        value = handler(self)
        if not self.aliases:
            value.pop("aliases", None)
        if not self.alias_channels:
            value.pop("alias_channels", None)
        return value


def encode(envelope):
    content = envelope.model_dump_json().encode()
    if len(content) > MAX_DOCUMENT:
        raise ValueError("Scientific export history exceeds the 32 MiB document limit")
    encoded = base64.b64encode(zlib.compress(content, 6)).decode("ascii")
    result = "CFEX1:" + hashlib.sha256(content).hexdigest() + ":" + encoded
    if len(result) > MAX_METADATA - 256_000:
        raise ValueError("Scientific export history exceeds the FCS metadata limit")
    return result


def decode(value):
    try:
        prefix, expected, encoded = value.split(":", 2)
        if prefix != "CFEX1" or len(expected) != 64 or len(value) > MAX_METADATA:
            raise ValueError("Invalid scientific FCS metadata header")
        inflater = zlib.decompressobj()
        content = inflater.decompress(base64.b64decode(encoded, validate=True), MAX_DOCUMENT + 1)
        if (
            len(content) > MAX_DOCUMENT
            or not inflater.eof
            or inflater.unused_data
            or inflater.unconsumed_tail
        ):
            raise ValueError("Scientific FCS metadata is truncated or exceeds its size limit")
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError("Scientific FCS metadata failed its SHA-256 integrity check")
        return Envelope.model_validate_json(content), expected
    except (ValueError, TypeError, zlib.error) as error:
        raise ValueError(f"Invalid scientific FCS metadata: {error}") from error


def npy_header(shape, dtype):
    output = io.BytesIO()
    np.lib.format.write_array_header_1_0(
        output,
        {
            "descr": np.dtype(dtype).str,
            "fortran_order": False,
            "shape": shape,
        },
    )
    return output.getvalue()


def text_segment(pairs):
    # Prefer an absent delimiter. Escaped delimiters next to a field boundary
    # are interpreted inconsistently by downstream readers of FCS TEXT.
    candidates = "|/~!#%&;@"
    content_characters = set("".join(str(key) + str(value) for key, value in pairs.items()))
    starts = {str(value)[0] for value in pairs.values() if str(value)} | {
        key[0] for key in pairs if key
    }
    delimiter = next((c for c in candidates if c not in content_characters), None)
    if delimiter is None:
        ends = {str(value)[-1] for value in pairs.values() if str(value)} | {
            key[-1] for key in pairs if key
        }
        delimiter = next((c for c in candidates if c not in starts | ends), None)
    if delimiter is None:
        raise ValueError(
            "FCS metadata needs a delimiter distinct from every value's first character"
        )
    values = []
    for key, value in pairs.items():
        if not key or not str(value):
            continue
        values += [
            key.replace(delimiter, delimiter * 2),
            str(value).replace(delimiter, delimiter * 2),
        ]
    content = (delimiter + delimiter.join(values) + delimiter).encode("utf-8")
    if len(content) > MAX_METADATA:
        raise ValueError("FCS TEXT metadata exceeds 4 MiB")
    return content


def fcs_prefix(channels, count, metadata):
    pairs = {
        "$BEGINANALYSIS": "0",
        "$ENDANALYSIS": "0",
        "$BEGINSTEXT": "0",
        "$ENDSTEXT": "0",
        "$BYTEORD": "1,2,3,4",
        "$DATATYPE": "D",
        "$MODE": "L",
        "$NEXTDATA": "0",
        "$PAR": str(len(channels)),
        "$TOT": str(count),
        "$TIMESTEP": "1",
        "$BEGINDATA": "0",
        "$ENDDATA": "0",
        **metadata,
    }
    for index, channel in enumerate(channels, 1):
        pairs.update(
            {
                f"$P{index}B": "64",
                f"$P{index}E": "0,0",
                f"$P{index}G": "1",
                f"$P{index}N": channel.name,
                f"$P{index}S": channel.label,
                f"$P{index}R": format(channel.range, ".17g"),
            }
        )
    # Offset digit counts can change TEXT length. Iterate to a stable encoding.
    start = 256
    for _ in range(12):
        content = text_segment(pairs)
        end = start + len(content) - 1
        data_start = (end + 8) // 8 * 8
        data_end = data_start + count * len(channels) * 8 - 1
        ds, de = data_start, data_end
        if pairs["$BEGINDATA"] == str(ds) and pairs["$ENDDATA"] == str(de):
            break
        pairs["$BEGINDATA"], pairs["$ENDDATA"] = str(ds), str(de)
    else:
        raise ValueError("FCS offset encoding did not converge")
    hs, he = (ds, de) if de <= 99_999_999 else (0, 0)
    header = b"FCS3.1    " + "".join(f"{v:8d}" for v in (start, end, hs, he, 0, 0)).encode("ascii")
    assert len(header) == 58
    return header + b" " * (start - 58) + content + b" " * (data_start - end - 1)


def source(doc, engine, request, include_raw_virtual=False):
    if doc.revision != request.revision:
        raise ConflictError("Workspace changed. Reload before preparing event export.")
    if request.scope:
        from .virtual_group_exports import source as pooled_source

        return pooled_source(doc, engine, request)
    sample = engine.sample(doc, request.sample_id)
    if request.gate_id:
        gate = next((g for g in doc.gates if g.id == request.gate_id), None)
        if gate is None or gate.sample_id != sample.id:
            raise ValueError("Choose a population belonging to the exported sample")
    channels = (
        sample.acquisition_channels
        if request.values == "raw" and not include_raw_virtual
        else sample.channels
    )
    if request.values == "raw" and request.format == "fcs" and sample.aliases:
        matrix = source_matrix(doc, sample)
        outputs = set(matrix.outputs) if matrix and matrix.kind == "spectral" else set()
        inactive = set(sample.aliases.values()) & (set(sample.unmixed_parameters) - outputs)
        if inactive:
            raise ValueError(
                "Stored FCS cannot reconstruct inactive unmixed alias sources: "
                + ", ".join(sorted(inactive))
                + ". Assign their spectral matrix or export compensated values."
            )
    audit = SourceAudit(doc)
    snapshot = audit.closure(sample.id, [c.name for c in channels], [request.gate_id])
    if snapshot["stale"]:
        raise ValueError(
            "An exported population or parameter is stale. Refit its source model first."
        )
    snapshot["workspace_id"], snapshot["revision"] = doc.id, doc.revision
    snapshot["metadata"] = {k: v for k, v in sample.metadata.items() if k != METADATA_KEY}
    snapshot["tags"] = dict(sample.tags)
    if sample.aliases:
        snapshot["aliases"] = dict(sample.aliases)
        snapshot["alias_channels"] = [
            c.model_dump() for c in sample.channels if c.name in sample.aliases
        ]
    snapshot["event_export"] = sample.event_export.model_dump() if sample.event_export else None
    # Carry complete lineage once rather than duplicating it in every history layer.
    snapshot["concatenation_sha256"] = (
        hashlib.sha256(sample.concatenation.model_dump_json().encode()).hexdigest()
        if sample.concatenation
        else None
    )
    return sample, channels, snapshot


def verify_sources(store, engine, doc, sample, snapshot, check):
    check()
    audit = SourceAudit(doc)
    if "pooled_group" in snapshot:
        for row in snapshot["sources"]:
            original = engine.sample(doc, row["sample_id"])
            verify_sources(store, engine, doc, original, row["snapshot"], check)
        return None
    audit.validate_file(store.data_path(doc.id, sample.id), sample.sha256)
    for identifier in snapshot["models"]:
        check()
        audit.validate_model(engine, identifier)
    return validate_origins(store, doc.id, sample) if sample.concatenation else None


def write_export(
    store,
    engine,
    doc,
    request,
    directory,
    progress=lambda **_: None,
    check=lambda: None,
    include_raw_virtual=False,
):
    if request.scope:
        from .virtual_group_exports import write

        return write(store, engine, doc, request, directory, progress, check)
    sample, channels, snapshot = source(doc, engine, request, include_raw_virtual)
    progress(stage="Verifying scientific sources", events_written=0, event_total=0)
    origins = verify_sources(store, engine, doc, sample, snapshot, check)
    check()
    mask = engine.mask(doc, sample, request.gate_id)
    count = int(np.count_nonzero(mask))
    channels = [c.model_copy(deep=True) for c in channels]
    if request.values == "scale":
        for channel in channels:
            channel.transform = Transform()
    if origins is not None:
        names = [c.name for c in channels]
        if not {"CF_Source", "CF_EventID"} <= set(names):
            raise ValueError("Exact merged origins require CF_Source and CF_EventID parameters")
        origin_columns = [names.index("CF_Source"), names.index("CF_EventID")]
        categorical = {"CF_" + key: values for key, values in sample.concatenation.keywords.items()}
        for index in [
            *origin_columns,
            *(names.index(name) for name in categorical if name in names),
        ]:
            channels[index].transform = Transform()
    else:
        origin_columns = None
        categorical = {}
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    body = directory / "events.data"
    output = directory / ("population." + request.format)
    if shutil.disk_usage(directory).free < count * len(channels) * 16 + 16 * 1024**2:
        raise ValueError("Insufficient storage to prepare the complete event export")
    lineage = sample.concatenation.model_copy(deep=True) if sample.concatenation else None
    retained = {s.index: 0 for s in lineage.sources} if lineage else {}
    origin_hash = hashlib.sha256(npy_header((count, 2), "<u8")) if lineage else None
    data_hash, written = hashlib.sha256(), 0
    try:
        with body.open("wb") as handle:
            for start in range(0, sample.event_count, CHUNK_EVENTS):
                check()
                stop = min(start + CHUNK_EVENTS, sample.event_count)
                selected = mask[start:stop]
                n = int(np.count_nonzero(selected))
                if not n:
                    continue
                chunk = ChunkColumns(engine, doc, sample, start, stop, request.values != "raw")
                values = np.empty((n, len(channels)), dtype="<f8")
                for index, channel in enumerate(channels):
                    array = chunk.column(channel.name)
                    if request.values == "scale":
                        spec = next(c.transform for c in sample.channels if c.name == channel.name)
                        array = transform(array, spec)
                    values[:, index] = array[selected]
                if lineage:
                    identities = np.asarray(origins[start:stop][selected], dtype="<u8")
                    if np.any(identities[:, 1] > 2**53):
                        raise ValueError(
                            "FCS double precision cannot preserve event IDs above 2^53"
                        )
                    values[:, origin_columns] = identities
                    raw_columns = [c.name for c in sample.acquisition_channels]
                    for name, codes in categorical.items():
                        if name not in names or name not in raw_columns:
                            raise ValueError(
                                "Merged keyword codebook is missing its acquired parameter"
                            )
                        code = engine.raw(doc, sample)[start:stop, raw_columns.index(name)][
                            selected
                        ]
                        if (
                            not np.isfinite(code).all()
                            or np.any(code < 1)
                            or np.any(code > len(codes))
                            or np.any(code != np.floor(code))
                        ):
                            raise ValueError(
                                "Merged keyword values disagree with their category codebook"
                            )
                        values[:, names.index(name)] = code
                    origin_hash.update(identities.tobytes(order="C"))
                    indices, counts = np.unique(identities[:, 0], return_counts=True)
                    for index, amount in zip(indices, counts, strict=True):
                        retained[int(index)] += int(amount)
                encoded = values.tobytes(order="C")
                handle.write(encoded)
                data_hash.update(encoded)
                written += n
                progress(stage="Writing event values", events_written=written, event_total=count)
        check()
        effective = request.values
        previous_values = (
            sample.concatenation.values
            if sample.concatenation
            else sample.event_export.values
            if sample.event_export
            else "raw"
        )
        if request.values == "raw":
            effective = previous_values
        elif (
            request.values == "compensated"
            and previous_values == "scale"
            and not sample.compensation_id
        ):
            effective = "scale"
        matrix = source_matrix(doc, sample) if request.values == "raw" else None
        if matrix:
            # An explicitly assigned current correction defines this file's raw basis.
            # Earlier materialization remains in the immutable source snapshot.
            effective = "raw"
            validate_matrix(matrix)
            if not set(matrix.detectors) <= {c.name for c in channels}:
                raise ValueError(
                    "Exported acquisition parameters do not include every matrix detector"
                )
        if lineage:
            offset = 0
            for item in lineage.sources:
                item.offset, item.count = offset, retained[item.index]
                offset += item.count
            lineage.origins_sha256 = origin_hash.hexdigest()
            lineage.values = effective
            lineage = ConcatenationProvenance.model_validate(lineage.model_dump())
        virtual_channels = [
            c for c in sample.channels if matrix and c.name in sample.unmixed_parameters
        ]
        envelope = Envelope(
            exported_at=now(),
            sample_name=sample.name,
            values=effective,
            channels=channels,
            virtual_channels=virtual_channels,
            aliases=sample.aliases if request.values == "raw" else {},
            alias_channels=[
                c for c in sample.channels if request.values == "raw" and c.name in sample.aliases
            ],
            matrix=matrix,
            tags=dict(sample.tags),
            lineage=lineage,
            data_sha256=data_hash.hexdigest(),
            source_snapshot=snapshot,
        )
        if request.format == "fcs":
            metadata = {
                METADATA_KEY: encode(envelope),
                "cytoforge_source": sample.name,
                "cytoforge_value_space": effective,
            }
            if matrix and matrix.kind == "spillover":
                if any("," in name for name in matrix.detectors):
                    raise ValueError(
                        "FCS spillover detector names cannot contain commas; "
                        "export compensated values instead"
                    )
                metadata["$SPILLOVER"] = ",".join(
                    [
                        str(len(matrix.detectors)),
                        *matrix.detectors,
                        *(format(v, ".17g") for row in matrix.matrix for v in row),
                    ]
                )
            prefix = fcs_prefix(channels, count, metadata)
            with output.open("wb") as handle, body.open("rb") as events:
                handle.write(prefix)
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
                    ("'" + c.name)
                    if c.name.startswith(("=", "+", "-", "@", "\t", "\r"))
                    else c.name
                    for c in channels
                )
                while raw := events.read(CHUNK_EVENTS * len(channels) * 8):
                    check()
                    values = np.frombuffer(raw, dtype="<f8").reshape(-1, len(channels))
                    writer.writerows(values.tolist())
        progress(stage="Checking prepared event file", events_written=count, event_total=count)
        verify_sources(store, engine, doc, sample, snapshot, check)
        return output, dict(
            event_count=count,
            channel_count=len(channels),
            channels=[c.name for c in channels],
            values=effective,
            matrix_preserved=matrix is not None and request.format == "fcs",
            exact_event_origins=lineage is not None and request.format == "fcs",
            format=request.format,
            **(
                {"alias_count": len(sample.aliases)}
                if sample.aliases and request.values == "raw" and request.format == "fcs"
                else {}
            ),
        )
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    finally:
        body.unlink(missing_ok=True)


def restore_header(header, sample, matrix):
    if METADATA_KEY not in header.text:
        return matrix, None
    envelope, checksum = decode(header.text[METADATA_KEY])
    if header.version != "3.1" or header.data_type != "D" or header.order != "<":
        raise ValueError("Scientific FCS export requires FCS 3.1 little-endian double precision")
    if float(header.text.get("timestep", "1")) != 1 or any(
        c["pne"] != (0.0, 0.0) or c["png"] != 1 for c in header.channels
    ):
        raise ValueError("Scientific FCS export must retain its unscaled DATA values")
    if [(c.name, c.label, c.range) for c in envelope.channels] != [
        (c["pnn"], c["pns"], c["pnr"]) for c in header.channels
    ]:
        raise ValueError("Scientific FCS parameter definitions disagree with its TEXT metadata")
    if envelope.matrix:
        if envelope.values != "raw":
            raise ValueError("Materialized scientific FCS values cannot carry a correction matrix")
        validate_matrix(envelope.matrix)
        if not set(envelope.matrix.detectors) <= {c.name for c in envelope.channels}:
            raise ValueError("Scientific FCS matrix detectors are unavailable")
        if envelope.matrix.kind == "spillover":
            if (
                matrix is None
                or matrix.detectors != envelope.matrix.detectors
                or matrix.matrix != envelope.matrix.matrix
            ):
                raise ValueError("Scientific FCS matrix disagrees with $SPILLOVER")
        elif matrix is not None:
            raise ValueError("A spectral scientific FCS export cannot include spillover correction")
    elif matrix is not None or header.text.get("spillover") or header.text.get("spill"):
        raise ValueError(
            "Materialized scientific FCS values cannot receive spillover a second time"
        )
    sample.channels = copy.deepcopy(envelope.channels)
    sample.tags = dict(envelope.tags)
    sample.concatenation = envelope.lineage
    sample.event_export = EventExportProvenance(
        exported_at=envelope.exported_at,
        values=envelope.values,
        data_sha256=envelope.data_sha256,
        metadata_sha256=checksum,
        source_snapshot=envelope.source_snapshot,
    )
    sample.metadata.pop(METADATA_KEY, None)
    sample.metadata.update(
        cytoforge_event_export_sha256=checksum, cytoforge_value_space=envelope.values
    )
    matrix = (
        envelope.matrix.model_copy(deep=True, update={"id": new_id()}) if envelope.matrix else None
    )
    from .compensation import assign_matrix

    sample.compensation_id = None
    assign_matrix(sample, matrix)
    if envelope.virtual_channels:
        if (
            matrix is None
            or matrix.kind != "spectral"
            or {c.name for c in envelope.virtual_channels} != set(matrix.outputs)
        ):
            raise ValueError(
                "Scientific FCS virtual display parameters must match its spectral outputs"
            )
        definitions = {c.name: c for c in envelope.virtual_channels}
        sample.channels = [
            definitions.get(c.name, c).model_copy(deep=True) for c in sample.channels
        ]
    if envelope.aliases or envelope.alias_channels:
        if len(envelope.alias_channels) != len(envelope.aliases):
            raise ValueError("Scientific FCS aliases require exact display definitions")
        if {c.name for c in envelope.alias_channels} != envelope.aliases.keys():
            raise ValueError("Scientific FCS alias display definitions disagree with its bindings")
        if envelope.aliases.keys() & {c.name for c in sample.channels}:
            raise ValueError("Scientific FCS aliases collide with stored parameters")
        sample.aliases = dict(envelope.aliases)
        sample.channels.extend(c.model_copy(deep=True) for c in envelope.alias_channels)
    Sample.model_validate(sample.model_dump())
    return matrix, envelope


class HashReader:
    def __init__(self, handle):
        self.handle, self.digest = handle, hashlib.sha256()

    def read(self, count):
        raw = self.handle.read(count)
        self.digest.update(raw)
        return raw

    def seek(self, *args):
        return self.handle.seek(*args)


def restore_origins(path, sample, envelope, check=lambda: None):
    if envelope.lineage is None:
        return None
    names = [c.name for c in envelope.channels]
    if not {"CF_Source", "CF_EventID"} <= set(names):
        raise ValueError("Scientific merged FCS export is missing exact origin parameters")
    columns = [names.index("CF_Source"), names.index("CF_EventID")]
    data = np.load(path, mmap_mode="r", allow_pickle=False)
    target = Path(path).with_suffix(".origins.npy")
    try:
        with target.open("wb") as handle:
            handle.write(npy_header((sample.event_count, 2), "<u8"))
            for start in range(0, sample.event_count, CHUNK_EVENTS):
                check()
                values = data[start : start + CHUNK_EVENTS, columns]
                if (
                    not np.isfinite(values).all()
                    or np.any(values < 0)
                    or np.any(values > 2**53)
                    or np.any(values != np.floor(values))
                ):
                    raise ValueError(
                        "Scientific FCS event origins must be exact nonnegative integers"
                    )
                handle.write(np.asarray(values, dtype="<u8").tobytes(order="C"))
        # Validate hashes, alignment, source counts, ranges and monotonic event IDs.
        validate_origins(None, "", sample, target)
        return target
    except BaseException:
        target.unlink(missing_ok=True)
        raise


class Sessions:
    """Prepare files without editing science; lease completed files during downloads."""

    def __init__(self, store):
        self.store = store
        self.root = store.root / "event-exports"
        self.root.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.records, self.cancel_events, self.downloads = {}, {}, {}
        self.closed = False
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="event-export")
        for path in self.root.glob("*/state.json"):
            try:
                record = json.loads(path.read_text())
                self.store.data_path(record["workspace_id"], record["id"])
                if path.parent.name != record["id"]:
                    continue
                Request.model_validate(record["request"])
                if record["status"] in {"queued", "running"} or record.get("cancel_requested"):
                    record.update(status="interrupted", finished_at=now(), cancel_requested=False)
                    atomic_json(path, record)
                self.records[record["id"]] = record
                self.cancel_events[record["id"]] = threading.Event()
                if record["status"] != "ready":
                    self._clean(record["id"])
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def _save(self, record):
        atomic_json(self.root / record["id"] / "state.json", record)

    def _record(self, workspace_id, identifier):
        record = self.records.get(identifier)
        if record is None or record["workspace_id"] != workspace_id:
            raise KeyError("Event export not found")
        return record

    def _clean(self, identifier):
        directory = self.root / identifier
        for name in ("events.data", "population.fcs", "population.csv"):
            (directory / name).unlink(missing_ok=True)

    def _check(self, identifier):
        if self.closed or self.cancel_events[identifier].is_set():
            raise Cancelled()

    def _update(self, identifier, **values):
        with self.lock:
            record = self.records[identifier]
            record.update(values)
            total = record["event_total"]
            record["progress"] = min(1, record["events_written"] / total) if total else 0
            self._save(record)

    def get(self, workspace_id, identifier):
        with self.lock:
            record = json.loads(json.dumps(self._record(workspace_id, identifier)))
        record["stale"] = self.store.revision(workspace_id) != record["revision"]
        record["can_download"] = (
            record["status"] == "ready"
            and not record["stale"]
            and not record.get("cancel_requested")
        )
        return record

    def submit(self, doc, request):
        source(doc, Engine(self.store), request)
        with self.lock:
            if self.closed:
                raise ConflictError("The engine is stopping")
            for record in self.records.values():
                if record["status"] != "ready" or self.downloads.get(record["id"]):
                    continue
                try:
                    current = self.store.get(record["workspace_id"])
                    available = any(s.id == record["request"]["sample_id"] for s in current.samples)
                except KeyError:
                    available = False
                if not available:
                    self._clean(record["id"])
                    record.update(
                        status="cancelled",
                        stage="Prepared file discarded after its sample was removed",
                        finished_at=now(),
                    )
                    self._save(record)
            if (
                sum(r["status"] in {"queued", "running", "ready"} for r in self.records.values())
                >= 4
            ):
                raise ConflictError("Four event exports are open. Save or cancel one first.")
            identifier = new_id()
            directory = self.root / identifier
            directory.mkdir()
            atomic_json(
                directory / "input.json",
                dict(workspace=doc.model_dump(), request=request.model_dump()),
            )
            record = dict(
                id=identifier,
                workspace_id=doc.id,
                revision=doc.revision,
                request=request.model_dump(),
                input_sha256=digest(directory / "input.json"),
                status="queued",
                stage="Waiting for an event writer",
                created_at=now(),
                finished_at=None,
                events_written=0,
                event_total=0,
                progress=0,
                cancel_requested=False,
                error=None,
                summary=None,
                file_sha256=None,
                file_bytes=None,
            )
            self.records[identifier] = record
            self.cancel_events[identifier] = threading.Event()
            self._save(record)
            self.executor.submit(self._run, identifier, doc, request)
        return self.get(doc.id, identifier)

    def list(self, workspace_id, sample_id):
        with self.lock:
            identifiers = [
                r["id"]
                for r in sorted(self.records.values(), key=lambda r: r["created_at"], reverse=True)
                if r["workspace_id"] == workspace_id
                and r["request"]["sample_id"] == sample_id
                and r["status"] in {"queued", "running", "ready"}
            ]
        return [self.get(workspace_id, identifier) for identifier in identifiers]

    def _run(self, identifier, doc, request):
        try:
            self._check(identifier)
            self._update(identifier, status="running", stage="Verifying source events")
            path, summary = write_export(
                self.store,
                Engine(self.store),
                doc,
                request,
                self.root / identifier,
                check=lambda: self._check(identifier),
                progress=lambda **values: self._update(identifier, **values),
            )
            file_hash = digest(path, lambda: self._check(identifier))
            with self.lock:
                self._check(identifier)
                if self.store.revision(doc.id) != doc.revision:
                    raise ConflictError("Workspace changed. Prepare the export again.")
                self._update(
                    identifier,
                    status="ready",
                    stage="Ready to save",
                    summary=summary,
                    file_sha256=file_hash,
                    file_bytes=path.stat().st_size,
                    finished_at=now(),
                )
        except Cancelled:
            self._clean(identifier)
            self._update(
                identifier, status="cancelled", stage="Export cancelled", finished_at=now()
            )
        except BaseException as error:
            self._clean(identifier)
            self._update(
                identifier,
                status="failed",
                stage="Export failed",
                error=str(error),
                finished_at=now(),
            )

    def cancel(self, workspace_id, identifier):
        with self.lock:
            record = self._record(workspace_id, identifier)
            if record["status"] in {"queued", "running", "ready"}:
                self.cancel_events[identifier].set()
                record["cancel_requested"] = True
                if record["status"] == "ready" and not self.downloads.get(identifier):
                    self._clean(identifier)
                    record.update(status="cancelled", stage="Export cancelled", finished_at=now())
                self._save(record)
        return self.get(workspace_id, identifier)

    def download(self, workspace_id, identifier):
        with self.lock:
            record = self._record(workspace_id, identifier)
            if (
                record["status"] != "ready"
                or record["cancel_requested"]
                or self.store.revision(workspace_id) != record["revision"]
            ):
                raise ConflictError("The prepared export is unavailable or its workspace changed")
            self.downloads[identifier] = self.downloads.get(identifier, 0) + 1
            record = json.loads(json.dumps(record))
        try:

            def check():
                self._check(identifier)

            directory = self.root / identifier
            if digest(directory / "input.json", check) != record["input_sha256"]:
                raise ValueError("The scientific export source snapshot changed after preparation")
            original = json.loads((directory / "input.json").read_text())
            doc = Workspace.model_validate(original["workspace"])
            request = Request.model_validate(original["request"])
            if doc.id != workspace_id or request.model_dump() != record["request"]:
                raise ValueError("The scientific export request changed after preparation")
            sample, _, snapshot = source(doc, Engine(self.store), request)
            verify_sources(self.store, Engine(self.store), doc, sample, snapshot, check)
            path = directory / ("population." + request.format)
            if digest(path, check) != record["file_sha256"]:
                raise ValueError("The prepared event file failed its SHA-256 integrity check")
            check()
            if self.store.revision(workspace_id) != doc.revision:
                raise ConflictError("Workspace changed. Prepare the export again.")
            name = re.sub(r'[\\/\x00-\x1f<>:"|?*]', "_", sample.name).strip(" .") or "sample"
            return path, name[:120] + "-population." + request.format
        except Cancelled as error:
            self.release(identifier)
            raise ConflictError("The event export was cancelled") from error
        except BaseException:
            self.release(identifier)
            raise

    def release(self, identifier):
        with self.lock:
            self.downloads[identifier] = max(0, self.downloads.get(identifier, 0) - 1)
            record = self.records[identifier]
            if record["cancel_requested"] and not self.downloads[identifier]:
                self._clean(identifier)
                record.update(status="cancelled", stage="Export cancelled", finished_at=now())
                self._save(record)

    def close(self):
        self.closed = True
        self.executor.shutdown(wait=True)
