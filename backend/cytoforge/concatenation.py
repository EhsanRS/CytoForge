"""Bounded event concatenation with reviewed, revision-bound atomic publication."""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from .analysis import atomic_json
from .fileio import load_validated_array
from .formulas import evaluate
from .models import (
    Channel,
    Compensation,
    ConcatenationProvenance,
    ConcatenationSource,
    Id,
    Model,
    Name,
    Sample,
    Transform,
    Workspace,
    new_id,
)
from .report_sources import SourceAudit
from .science import Engine, compensate, transform, validate_matrix
from .store import ConflictError, now

CHUNK_EVENTS = 16384
ACTIVE = {"queued", "running", "applying"}
SOURCE_COLUMN = "CF_Source"
EVENT_COLUMN = "CF_EventID"


class Input(Model):
    sample_id: Id
    gate_id: Id | None = None


class Parameter(Model):
    name: Name
    sources: dict[Id, Name]


class Request(Model):
    revision: int = Field(ge=0)
    name: Name
    inputs: list[Input] = Field(min_length=1, max_length=128)
    parameters: list[Parameter] = Field(min_length=1, max_length=256)
    values: Literal["raw", "compensated", "scale"] = "raw"
    compensation: Literal["preserve", "discard"] = "preserve"
    grouping: Literal["all", "batch", "keyword"] = "all"
    batch_size: int = Field(default=8, ge=1, le=128)
    group_keyword: Name | None = None
    keywords: list[Name] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def unique(self):
        ids = [i.sample_id for i in self.inputs]
        names = [p.name for p in self.parameters]
        reserved = {SOURCE_COLUMN, EVENT_COLUMN, *("CF_" + k for k in self.keywords)}
        if len(ids) != len(set(ids)):
            raise ValueError("Choose each source sample only once")
        if len(names) != len(set(names)) or set(names) & reserved:
            raise ValueError("Output parameters must be unique and separate from origin columns")
        if len(self.keywords) != len(set(self.keywords)):
            raise ValueError("Additional keywords must be unique")
        if any(len("CF_" + key) > 160 for key in self.keywords):
            raise ValueError("Additional keyword names must be at most 157 characters")
        if any(set(p.sources) != set(ids) for p in self.parameters):
            raise ValueError("Map every output parameter for every selected sample")
        if self.grouping == "keyword" and not self.group_keyword:
            raise ValueError("Choose a grouping keyword")
        return self


class Apply(Model):
    revision: int = Field(ge=0)
    review_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class Cancelled(Exception):
    pass


def digest(path, check=lambda: None):
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(1024 * 1024):
            check()
            result.update(block)
    return result.hexdigest()


def keyword(sample, key):
    return sample.tags.get(key, sample.metadata.get(key))


def review_hash(request, samples, matrices):
    value = dict(request=request, samples=samples, matrices=matrices)
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def source_matrix(doc, sample):
    if not sample.compensation_id:
        return None
    matrix = next((m for m in doc.compensations if m.id == sample.compensation_id), None)
    if matrix is None:
        raise ValueError("A source compensation matrix is missing")
    validate_matrix(matrix)
    return matrix


def mapped_matrix(doc, request, items):
    """Clone a common raw spillover basis after explicit parameter renaming."""
    if request.values != "raw" or request.compensation == "discard":
        return None
    basis = []
    for _, sample, _ in items:
        matrix = source_matrix(doc, sample)
        if matrix is None:
            basis.append(None)
            continue
        if matrix.kind != "spillover":
            raise ValueError(
                "Raw spectral data requires discarding the assignment; "
                "choose compensated values to retain unmixed measurements"
            )
        reverse = {
            sample.aliases.get(p.sources[sample.id], p.sources[sample.id]): p.name
            for p in request.parameters
        }
        if not set(matrix.detectors) <= reverse.keys():
            raise ValueError(
                "Preserving compensation requires every matrix detector. "
                "Include them or explicitly discard the matrix assignment."
            )
        detectors = [reverse[n] for n in matrix.detectors]
        order = sorted(range(len(detectors)), key=lambda i: detectors[i])
        basis.append(
            dict(
                detectors=[detectors[i] for i in order],
                matrix=np.asarray(matrix.matrix)[np.ix_(order, order)].tolist(),
            )
        )
    if any(value != basis[0] for value in basis):
        raise ValueError(
            "Source compensation matrices differ. Choose compensated values "
            "or explicitly discard the raw matrix assignment."
        )
    if basis[0] is None:
        return None
    return Compensation(
        name=(request.name[:125] + " compensation snapshot"),
        source="Concatenation snapshot",
        **basis[0],
        provenance={"workspace_id": doc.id, "revision": doc.revision},
    )


def plan(doc, request):
    if doc.revision != request.revision:
        raise ConflictError("Workspace changed. Reload before preparing concatenation.")
    audit = SourceAudit(doc)
    items = []
    for index, item in enumerate(request.inputs, 1):
        sample = audit.samples.get(item.sample_id)
        if sample is None:
            raise ValueError("A selected source sample no longer exists")
        if item.gate_id and (
            item.gate_id not in audit.gates or audit.gates[item.gate_id].sample_id != sample.id
        ):
            raise ValueError("Choose a population belonging to its source sample")
        names = [p.sources[sample.id] for p in request.parameters]
        available = {
            c.name
            for c in (sample.acquisition_channels if request.values == "raw" else sample.channels)
        }
        if request.values == "raw":
            available.update(name for name, source in sample.aliases.items() if source in available)
        if not set(names) <= available:
            raise ValueError(
                f"Unavailable source parameter in {sample.name}; "
                "raw values require acquired channels"
            )
        if len(names) != len(set(names)):
            raise ValueError("Map each source parameter only once per sample")
        if request.values == "raw" and len(
            {sample.aliases.get(name, name) for name in names}
        ) != len(names):
            raise ValueError("Map each acquired column only once; aliases share its measurements")
        matrix = source_matrix(doc, sample)
        if matrix and not set(matrix.detectors) <= {c.name for c in sample.acquisition_channels}:
            raise ValueError("Source matrix detectors do not match the acquired channels")
        snapshot = audit.closure(sample.id, names, [item.gate_id])
        if snapshot["stale"]:
            raise ValueError("A source population or parameter is stale. Refit its model first.")
        snapshot["metadata"] = dict(sample.metadata)
        snapshot["tags"] = dict(sample.tags)
        snapshot["concatenation"] = (
            sample.concatenation.model_dump() if sample.concatenation else None
        )
        snapshot["event_export"] = sample.event_export.model_dump() if sample.event_export else None
        items.append((index, sample, snapshot))
    groups = []
    if request.grouping == "batch":
        groups = [
            items[i : i + request.batch_size] for i in range(0, len(items), request.batch_size)
        ]
    elif request.grouping == "keyword":
        by_keyword = {}
        for item in items:
            value = keyword(item[1], request.group_keyword)
            if value is None:
                raise ValueError(f"{item[1].name} has no {request.group_keyword} keyword")
            by_keyword.setdefault(value, []).append(item)
        groups = list(by_keyword.values())
    else:
        groups = [items]
    matrices = [mapped_matrix(doc, request, group) for group in groups]
    return groups, matrices


class ChunkColumns:
    """Evaluate elementwise formulas and compensation on a bounded event slice."""

    def __init__(self, engine, doc, sample, start, stop, corrected):
        self.engine, self.doc, self.sample = engine, doc, sample
        self.start, self.stop, self.corrected = start, stop, corrected
        self.raw = engine.raw(doc, sample)[start:stop]
        self.names = [c.name for c in sample.acquisition_channels]
        self.cache = {}
        self.matrix = source_matrix(doc, sample) if corrected else None
        self.corrected_values = None

    def column(self, name):
        if name in self.cache:
            return self.cache[name]
        if name in self.sample.aliases:
            return self.column(self.sample.aliases[name])
        derived = next((p for p in self.sample.derived_parameters if p.name == name), None)
        computed = next((p for p in self.sample.computed_parameters if p.name == name), None)
        if derived:
            value = evaluate(derived.expression, self.column, self.stop - self.start)
        elif computed:
            value = self.engine.column(self.doc, self.sample, name)[self.start : self.stop]
        elif self.matrix and name in self.matrix.outputs:
            if self.corrected_values is None:
                values = self.raw[:, [self.names.index(n) for n in self.matrix.detectors]]
                self.corrected_values = compensate(values, self.matrix)
            value = self.corrected_values[:, self.matrix.outputs.index(name)]
        elif name in self.sample.unmixed_parameters:
            value = np.full(self.stop - self.start, np.nan)
        else:
            value = self.raw[:, self.names.index(name)]
        self.cache[name] = value
        return value


def validate_origins(store, workspace_id, sample, path=None):
    provenance = sample.concatenation
    if provenance is None:
        raise ValueError("This sample has no concatenation provenance")
    target = path or store.origins_path(workspace_id, sample.id)
    if digest(target) != provenance.origins_sha256:
        raise ValueError("Concatenation event origins failed their SHA-256 integrity check")
    return load_validated_array(target, lambda values: validate_origin_data(sample, values))


def validate_origin_data(sample, values):
    provenance = sample.concatenation
    if values.dtype != np.dtype("uint64") or values.shape != (sample.event_count, 2):
        raise ValueError("Invalid concatenation event origins")
    for source in provenance.sources:
        previous = None
        for start in range(source.offset, source.offset + source.count, CHUNK_EVENTS):
            rows = values[start : min(start + CHUNK_EVENTS, source.offset + source.count)]
            if (
                np.any(rows[:, 0] != source.index)
                or np.any(rows[:, 1] >= source.event_count)
                or np.any(rows[1:, 1] <= rows[:-1, 1])
                or (previous is not None and rows[0, 1] <= previous)
            ):
                raise ValueError("Concatenation origins do not match the source event ranges")
            previous = rows[-1, 1]


class Sessions:
    def __init__(self, store):
        self.store = store
        self.root = store.root / "concatenations"
        self.root.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.closed = False
        self.records, self.cancel_events = {}, {}
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="concatenation")
        for path in self.root.glob("*/state.json"):
            try:
                record = json.loads(path.read_text())
                if path.parent.name != record["id"]:
                    continue
                # IDs from disk must pass the same path validation as API IDs.
                self.store.data_path(record["workspace_id"], record["id"])
                if record["status"] in ACTIVE:
                    outputs = [Sample.model_validate(s) for s in record.get("samples", [])]
                    referenced = store.referenced_samples(record["workspace_id"])
                    committed = bool(outputs) and {s.id for s in outputs} <= referenced
                    record.update(
                        status="applied" if committed else "interrupted", finished_at=now()
                    )
                    if not committed:
                        for sample in outputs:
                            if sample.id not in referenced:
                                self.store.data_path(record["workspace_id"], sample.id).unlink(
                                    missing_ok=True
                                )
                                self.store.origins_path(record["workspace_id"], sample.id).unlink(
                                    missing_ok=True
                                )
                    atomic_json(path, record)
                self.records[record["id"]] = record
                if record["status"] in {"applied", "failed", "cancelled", "interrupted"}:
                    self._clean(path.parent)
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def _save(self, record):
        atomic_json(self.root / record["id"] / "state.json", record)

    def _record(self, workspace_id, identifier):
        record = self.records.get(identifier)
        if record is None or record["workspace_id"] != workspace_id:
            raise KeyError("Concatenation session not found")
        return record

    def get(self, workspace_id, identifier):
        with self.lock:
            record = json.loads(json.dumps(self._record(workspace_id, identifier)))
        record["stale"] = self.store.revision(workspace_id) != record["revision"]
        record["can_apply"] = record["status"] == "ready" and not record["stale"]
        return record

    def submit(self, doc, request):
        plan(doc, request)
        with self.lock:
            if self.closed:
                raise ValueError("The engine is stopping")
            if sum(r["status"] in ACTIVE for r in self.records.values()) >= 4:
                raise ConflictError("Four concatenations are active. Finish or cancel one first.")
            identifier = new_id()
            directory = self.root / identifier
            directory.mkdir()
            atomic_json(
                directory / "input.json",
                {"workspace": doc.model_dump(), "request": request.model_dump()},
            )
            record = dict(
                id=identifier,
                workspace_id=doc.id,
                revision=doc.revision,
                request=request.model_dump(),
                status="queued",
                stage="Waiting for an event writer",
                created_at=now(),
                finished_at=None,
                events_written=0,
                event_total=0,
                progress=0,
                error=None,
                samples=[],
                matrices=[],
                review_hash=None,
            )
            self.records[identifier] = record
            self.cancel_events[identifier] = threading.Event()
            self._save(record)
            self.executor.submit(self._run, identifier, doc, request)
        return self.get(doc.id, identifier)

    def _update(self, identifier, **values):
        with self.lock:
            self.records[identifier].update(values)
            self._save(self.records[identifier])

    def _check(self, identifier):
        if self.closed or self.cancel_events[identifier].is_set():
            raise Cancelled()

    def _verify_sources(self, identifier, doc, groups):
        audit = SourceAudit(doc)
        engine = Engine(self.store)
        for group in groups:
            for _, sample, snapshot in group:
                self._check(identifier)
                if (
                    digest(self.store.data_path(doc.id, sample.id), lambda: self._check(identifier))
                    != sample.sha256
                ):
                    raise ValueError(f"Source event data failed its integrity check: {sample.name}")
                if sample.concatenation:
                    validate_origins(self.store, doc.id, sample)
                for model in snapshot["models"]:
                    audit.validate_model(engine, model)

    def _run(self, identifier, doc, request):
        directory = self.root / identifier
        try:
            self._check(identifier)
            self._update(identifier, status="running", stage="Verifying source events")
            groups, matrices = plan(doc, request)
            self._verify_sources(identifier, doc, groups)
            engine = Engine(self.store)
            masks, total = {}, 0
            for item in request.inputs:
                self._check(identifier)
                sample = engine.sample(doc, item.sample_id)
                mask = engine.mask(doc, sample, item.gate_id)
                masks[sample.id] = mask
                total += int(np.count_nonzero(mask))
            if not total:
                raise ValueError("The selected populations contain no events")
            self._update(identifier, event_total=total, stage="Writing merged event data")
            codebooks = {
                key: list(
                    dict.fromkeys(
                        keyword(engine.sample(doc, i.sample_id), key) for i in request.inputs
                    )
                )
                for key in request.keywords
            }
            outputs, written = [], 0
            names = {s.name for s in doc.samples}
            for group_number, (group, matrix) in enumerate(zip(groups, matrices, strict=True), 1):
                count = sum(int(np.count_nonzero(masks[s.id])) for _, s, _ in group)
                if not count:
                    raise ValueError(
                        "A grouped output has no events; change the source populations"
                    )
                identifier_out = new_id()
                name = (
                    request.name if len(groups) == 1 else request.name[:140] + f" - {group_number}"
                )
                candidate, suffix = name, 2
                while candidate in names:
                    candidate = name[:145] + f" ({suffix})"
                    suffix += 1
                name = candidate
                names.add(name)
                first = group[0][1]
                channels = []
                for parameter in request.parameters:
                    channel = next(
                        c for c in first.channels if c.name == parameter.sources[first.id]
                    )
                    channels.append(
                        channel.model_copy(
                            deep=True,
                            update={
                                "name": parameter.name,
                                "transform": Transform()
                                if request.values == "scale"
                                else channel.transform.model_copy(deep=True),
                            },
                        )
                    )
                channels += [
                    Channel(name=SOURCE_COLUMN, label="Source sample index"),
                    Channel(name=EVENT_COLUMN, label="Original event ID (zero based)"),
                ]
                channels += [
                    Channel(name="CF_" + key, label=key + " code") for key in request.keywords
                ]
                event_path = directory / f"{identifier_out}.npy"
                origin_path = directory / f"{identifier_out}.origins.npy"
                values = np.lib.format.open_memmap(
                    event_path, mode="w+", dtype="float64", shape=(count, len(channels))
                )
                origins = np.lib.format.open_memmap(
                    origin_path, mode="w+", dtype="uint64", shape=(count, 2)
                )
                sources, offset = [], 0
                for index, sample, snapshot in group:
                    source_count = int(np.count_nonzero(masks[sample.id]))
                    sources.append(
                        ConcatenationSource(
                            index=index,
                            sample_id=sample.id,
                            sample_name=sample.name,
                            gate_id=next(
                                i.gate_id for i in request.inputs if i.sample_id == sample.id
                            ),
                            offset=offset,
                            count=source_count,
                            event_count=sample.event_count,
                            parameters={p.name: p.sources[sample.id] for p in request.parameters},
                            snapshot=snapshot,
                        )
                    )
                    for start in range(0, sample.event_count, CHUNK_EVENTS):
                        self._check(identifier)
                        stop = min(start + CHUNK_EVENTS, sample.event_count)
                        selected = masks[sample.id][start:stop]
                        n = int(np.count_nonzero(selected))
                        if not n:
                            continue
                        chunk = ChunkColumns(
                            engine, doc, sample, start, stop, request.values != "raw"
                        )
                        for column, parameter in enumerate(request.parameters):
                            source_name = parameter.sources[sample.id]
                            array = chunk.column(source_name)
                            if request.values == "scale":
                                spec = next(
                                    c.transform for c in sample.channels if c.name == source_name
                                )
                                array = transform(array, spec)
                            values[offset : offset + n, column] = array[selected]
                        ids = np.flatnonzero(selected).astype("uint64") + start
                        values[offset : offset + n, len(request.parameters)] = index
                        values[offset : offset + n, len(request.parameters) + 1] = ids
                        for column, key in enumerate(request.keywords, len(request.parameters) + 2):
                            values[offset : offset + n, column] = (
                                codebooks[key].index(keyword(sample, key)) + 1
                            )
                        origins[offset : offset + n, 0] = index
                        origins[offset : offset + n, 1] = ids
                        offset += n
                        written += n
                        self._update(
                            identifier,
                            events_written=written,
                            progress=written / total,
                            stage=f"Writing {sample.name}",
                        )
                values.flush()
                origins.flush()
                del values, origins
                provenance = ConcatenationProvenance(
                    workspace_id=doc.id,
                    revision=doc.revision,
                    created_at=now(),
                    values=request.values,
                    origins_sha256=digest(origin_path, lambda: self._check(identifier)),
                    sources=sources,
                    keywords=codebooks,
                )
                common_tags = {
                    k: v
                    for k, v in first.tags.items()
                    if all(s.tags.get(k) == v for _, s, _ in group)
                }
                outputs.append(
                    Sample(
                        id=identifier_out,
                        name=name,
                        event_count=count,
                        channels=channels,
                        metadata={"cytoforge_concatenation": "1", "value_space": request.values},
                        tags=common_tags,
                        compensation_id=matrix.id if matrix else None,
                        source="Concatenated populations",
                        sha256=digest(event_path, lambda: self._check(identifier)),
                        concatenation=provenance,
                    )
                )
            self._update(identifier, stage="Verifying merged events and source snapshots")
            self._verify_sources(identifier, doc, groups)
            samples = [s.model_dump() for s in outputs]
            matrix_values = [m.model_dump() for m in matrices if m is not None]
            review = review_hash(request.model_dump(), samples, matrix_values)
            with self.lock:
                self._check(identifier)
                self._update(
                    identifier,
                    status="ready",
                    stage="Ready to review",
                    samples=samples,
                    matrices=matrix_values,
                    review_hash=review,
                    finished_at=now(),
                )
        except Cancelled:
            for array_name in ("values", "origins"):
                array = locals().get(array_name)
                if isinstance(array, np.memmap):
                    array._mmap.close()
            self._clean(directory)
            self._update(
                identifier, status="cancelled", stage="Concatenation cancelled", finished_at=now()
            )
        except Exception as error:
            for array_name in ("values", "origins"):
                array = locals().get(array_name)
                if isinstance(array, np.memmap):
                    array._mmap.close()
            self._clean(directory)
            self._update(
                identifier,
                status="failed",
                stage="Concatenation failed",
                error=str(error),
                finished_at=now(),
            )

    @staticmethod
    def _clean(directory):
        for path in directory.glob("*.npy"):
            path.unlink(missing_ok=True)

    def cancel(self, workspace_id, identifier):
        with self.lock:
            record = self._record(workspace_id, identifier)
            if record["status"] == "applying":
                raise ConflictError(
                    "The reviewed samples are being saved; use Undo after they finish"
                )
            if record["status"] in {"queued", "running"}:
                self.cancel_events[identifier].set()
            elif record["status"] == "ready":
                record.update(
                    status="cancelled", stage="Concatenation cancelled", finished_at=now()
                )
                self._save(record)
                self._clean(self.root / identifier)
        return self.get(workspace_id, identifier)

    def apply(self, workspace_id, identifier, request):
        with self.lock:
            record = self._record(workspace_id, identifier)
            if record["status"] != "ready" or request.review_hash != record["review_hash"]:
                raise ConflictError("Concatenation is unavailable or the reviewed result changed")
            if (
                review_hash(record["request"], record["samples"], record["matrices"])
                != request.review_hash
            ):
                raise ConflictError("The staged scientific definitions changed after review")
            if (
                request.revision != record["revision"]
                or self.store.revision(workspace_id) != record["revision"]
            ):
                raise ConflictError("Workspace changed. Prepare a new concatenation before saving.")
            original = json.loads((self.root / identifier / "input.json").read_text())
            doc = Workspace.model_validate(original["workspace"])
            source_request = Request.model_validate(original["request"])
            if source_request.model_dump() != record["request"] or doc.id != workspace_id:
                raise ConflictError("The concatenation source snapshot changed after review")
            groups, _ = plan(doc, source_request)
            self.cancel_events.setdefault(identifier, threading.Event())
            record.update(status="applying", stage="Saving reviewed samples")
            self._save(record)
        written = []
        try:
            self._verify_sources(identifier, doc, groups)
            outputs = [Sample.model_validate(s) for s in record["samples"]]
            matrices = [Compensation.model_validate(m) for m in record["matrices"]]
            for sample in outputs:
                event_path = self.root / identifier / f"{sample.id}.npy"
                origin_path = self.root / identifier / f"{sample.id}.origins.npy"
                if digest(event_path) != sample.sha256:
                    raise ValueError("Merged event data failed its SHA-256 integrity check")
                validate_origins(self.store, workspace_id, sample, origin_path)
                for source, target in (
                    (event_path, self.store.data_path(workspace_id, sample.id)),
                    (origin_path, self.store.origins_path(workspace_id, sample.id)),
                ):
                    if target.exists():
                        raise ConflictError("An output event file already exists")
                    written.append(target)
                    shutil.copyfile(source, target)
                if digest(written[-2]) != sample.sha256:
                    raise ValueError("Saved event data failed its integrity check")
                validate_origins(self.store, workspace_id, sample)

            def change(workspace):
                if {s.id for s in outputs} & {s.id for s in workspace.samples}:
                    raise ConflictError("An output sample already exists")
                workspace.samples.extend(outputs)
                workspace.compensations.extend(matrices)

            result = self.store.mutate(
                workspace_id, "Concatenate selected populations", change, request.revision
            )
            written.clear()
            # The SQLite snapshot is committed. A subsequent housekeeping failure
            # must not tell a caller that the scientific transaction was rejected.
            try:
                self._update(
                    identifier, status="applied", stage="Merged samples created", finished_at=now()
                )
                self._clean(self.root / identifier)
            except OSError:
                pass
            return result
        except Exception:
            self._update(identifier, status="ready", stage="Ready to review")
            raise
        finally:
            for path in written:
                path.unlink(missing_ok=True)

    def close(self):
        self.closed = True
        for event in self.cancel_events.values():
            event.set()
        self.executor.shutdown(wait=True, cancel_futures=True)
