"""Bounded event decoding into immutable NumPy files; no whole-acquisition arrays."""

from __future__ import annotations

import csv
import hashlib
import math
import os
import re
import shutil
from collections.abc import Callable, Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import numpy as np

from .models import Channel, Compensation, Sample, Transform
from .science import validate_matrix

MAX_VALUES = 150_000_000
MAX_DATASETS = 1024
MAX_METADATA_BYTES = 4 * 1024**2
CHUNK_VALUES = 262_144
Progress = Callable[..., None]
Check = Callable[[], None]


class ImportCancelled(Exception):
    """Cancellation must roll back the entire batch before its single commit."""


def nothing(*_args, **_kwargs):
    pass


@dataclass
class FCSHeader:
    offset: int
    version: str
    text: dict[str, str]
    analysis: dict[str, str]
    event_count: int
    channel_count: int
    data_start: int
    data_stop: int
    next_offset: int
    data_type: str
    order: str
    widths: list[int]
    variable_ascii: bool
    channels: list[dict]
    time_index: int | None
    warnings: list[str]


@dataclass
class ImportedDataset:
    sample: Sample
    path: Path | None
    compensation: Compensation | None
    warnings: list[str]
    index: int
    offset: int
    skipped: bool = False
    count: int = 1
    origins_path: Path | None = None


def text_pairs(raw: bytes) -> dict[str, str]:
    """Unescape doubled delimiters without removing '$' from keyword values."""
    if len(raw) < 2 or not 1 <= raw[0] <= 126 or raw[-1] != raw[0]:
        raise ValueError("FCS TEXT must start and end with its ASCII delimiter")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    # A value may end with an escaped delimiter followed by its separator.
    # Scan doubled escapes in order; regex lookbehinds lose that triple-delimiter case.
    delimiter, tokens, current, index = text[0], [], [], 1
    while index < len(text) - 1:
        character = text[index]
        if character != delimiter:
            current.append(character)
        elif index + 1 < len(text) - 1 and text[index + 1] == delimiter:
            current.append(delimiter)
            index += 1
        else:
            tokens.append("".join(current))
            current.clear()
        index += 1
    tokens.append("".join(current))
    if len(tokens) % 2:
        raise ValueError("FCS TEXT contains an unmatched keyword or delimiter")
    result = {}
    for key, value in zip(tokens[::2], tokens[1::2], strict=True):
        key = key.removeprefix("$").lower()
        if not key:
            raise ValueError("FCS TEXT contains an empty keyword")
        if key in result and result[key] != value:
            raise ValueError(f"FCS TEXT contains conflicting values for keyword {key.upper()}")
        result[key] = value
    return result


def _segment(handle: BinaryIO, offset: int, start: int, stop: int, size: int) -> bytes:
    if start < 58 or stop < start or offset + stop >= size:
        raise ValueError("FCS metadata offsets lie outside the dataset/file")
    length = stop - start + 1
    if length > MAX_METADATA_BYTES:
        raise ValueError("FCS metadata segment exceeds 4 MiB")
    handle.seek(offset + start)
    content = handle.read(length)
    if len(content) != length:
        raise ValueError("FCS metadata segment is truncated")
    return content


def _optional_offsets(text: dict, prefix: str, fallback=(0, 0)):
    return (
        int(text.get(f"begin{prefix}", fallback[0])),
        int(text.get(f"end{prefix}", fallback[1])),
    )


def read_fcs_header(handle: BinaryIO, offset: int, size: int) -> FCSHeader:
    handle.seek(offset)
    raw = handle.read(58)
    if len(raw) != 58 or raw[:6] not in {b"FCS2.0", b"FCS3.0", b"FCS3.1"}:
        raise ValueError(f"Missing or unsupported FCS HEADER at byte {offset}")
    version = raw[3:6].decode("ascii")
    try:
        fields = [int(raw[start : start + 8].strip() or b"0") for start in range(10, 58, 8)]
        ts, te, hs, he, als, ale = fields
        text = text_pairs(_segment(handle, offset, ts, te, size))
        segments = [(ts, te, "TEXT")]
        supplemental = _optional_offsets(text, "stext")
        if supplemental != (0, 0):
            ss, se = supplemental
            extra = text_pairs(_segment(handle, offset, ss, se, size))
            for key, value in extra.items():
                if key in text and text[key] != value:
                    raise ValueError(f"Conflicting supplemental FCS keyword {key.upper()}")
                text[key] = value
            segments.append((ss, se, "supplemental TEXT"))
        ds, de = (hs, he) if version == "2.0" else _optional_offsets(text, "data")
        if version != "2.0" and (ds, de) != (hs, he):
            if (hs, he) != (0, 0) or de <= 99_999_999:
                raise ValueError("FCS HEADER and TEXT disagree about DATA offsets")
        count, channels = int(text["tot"]), int(text["par"])
        if count < 0 or not 1 <= channels <= 512:
            raise ValueError("FCS requires a nonnegative event count and 1–512 parameters")
        if count * channels > MAX_VALUES:
            raise ValueError("File exceeds the current 150-million-value import limit")
        if count:
            if ds < 58 or de < ds or offset + de >= size:
                raise ValueError("FCS DATA offsets lie outside the dataset/file")
            segments.append((ds, de, "DATA"))
        elif (ds, de) != (0, 0) and de != ds - 1:
            raise ValueError("A zero-event FCS dataset must have an empty DATA segment")
        analysis_offsets = _optional_offsets(text, "analysis", (als, ale))
        analysis = {}
        if analysis_offsets != (0, 0):
            astart, astop = analysis_offsets
            analysis = text_pairs(_segment(handle, offset, astart, astop, size))
            segments.append((astart, astop, "ANALYSIS"))
        ordered = sorted(segments)
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if current[0] <= previous[1]:
                raise ValueError(f"FCS {previous[2]} and {current[2]} segments overlap")
        nextdata = int(text.get("nextdata", "0"))
        if nextdata < 0:
            raise ValueError("FCS $NEXTDATA must be zero or a positive relative offset")
        if nextdata and (
            nextdata <= max(end for _, end, _ in segments) or offset + nextdata + 58 > size
        ):
            raise ValueError("FCS $NEXTDATA overlaps this dataset or points outside the file")
        if text.get("mode", "").upper() != "L":
            raise ValueError("FCS histogram modes are unsupported; import list-mode acquisitions")
        data_type = text["datatype"].upper()
        if data_type not in {"I", "F", "D", "A"}:
            raise ValueError(f"Unsupported FCS data type: {data_type}")
        byteord = text.get("byteord", "").replace(" ", "")
        if byteord in {"1,2,3,4", "1,2"}:
            order = "<"
        elif byteord in {"4,3,2,1", "2,1"}:
            order = ">"
        else:
            raise ValueError(f"Unsupported FCS byte order: {byteord}; no native-order fallback")
        width_text = [text[f"p{i}b"] for i in range(1, channels + 1)]
        variable_ascii = data_type == "A" and all(w == "*" for w in width_text)
        widths = [] if variable_ascii else [int(w) for w in width_text]
        if data_type in {"F", "D"} and any(w != (32 if data_type == "F" else 64) for w in widths):
            raise ValueError("FCS floating-point parameter widths disagree with $DATATYPE")
        if data_type == "I" and any(w < 1 or w > 64 for w in widths):
            raise ValueError("FCS integer parameters require widths of 1–64 bits")
        packed = data_type == "I" and any(w % 8 for w in widths)
        if packed and order != "<":
            raise ValueError(
                "Big-endian bit-packed FCS integers require a verified instrument layout; "
                "export byte-aligned integers or floating-point data"
            )
        if data_type == "A" and not variable_ascii and any(w < 1 or w > 64 for w in widths):
            raise ValueError("FCS fixed ASCII parameters require widths of 1–64 characters")
        if count and not variable_ascii:
            expected = count * sum(widths) if data_type == "A" else (count * sum(widths) + 7) // 8
            if de - ds + 1 != expected:
                raise ValueError(
                    f"FCS DATA length does not match $TOT × parameter widths "
                    f"({de - ds + 1:,} bytes declared; {expected:,} required)"
                )
        channel_metadata, time_index, warnings = [], None, []
        for index in range(channels):
            n = index + 1
            name = text[f"p{n}n"]
            pnr = float(text[f"p{n}r"])
            pne = tuple(float(v) for v in text.get(f"p{n}e", "0,0").split(","))
            png = float(text.get(f"p{n}g", "1"))
            if len(pne) != 2 or not all(math.isfinite(v) and v >= 0 for v in pne):
                raise ValueError(f"FCS $P{n}E requires two finite nonnegative numbers")
            if not math.isfinite(pnr) or pnr <= 0 or not math.isfinite(png) or png < 0:
                raise ValueError(f"Invalid FCS range/gain for parameter {n}")
            if data_type == "I" and (not text[f"p{n}r"].isdigit() or int(text[f"p{n}r"]) > 2**64):
                raise ValueError(f"FCS integer range for parameter {n} must be 1–2^64")
            if pne[0] > 0 and pne[1] == 0:
                pne = (pne[0], 1.0)
                warnings.append(f"{name}: logarithmic zero corrected to 1 as required by FCS")
            if name.lower() == "time":
                if time_index is not None:
                    raise ValueError("FCS contains more than one Time parameter")
                time_index, png = index, 1.0
            channel_metadata.append(
                {"pnn": name, "pns": text.get(f"p{n}s", ""), "pnr": pnr, "pne": pne, "png": png}
            )
        return FCSHeader(
            offset,
            version,
            text,
            analysis,
            count,
            channels,
            ds,
            de,
            offset + nextdata if nextdata else 0,
            data_type,
            order,
            widths,
            variable_ascii,
            channel_metadata,
            time_index,
            warnings,
        )
    except (KeyError, OverflowError) as exc:
        raise ValueError(f"FCS contains missing or invalid acquisition metadata: {exc}") from exc


def preprocess(header: FCSHeader, values: np.ndarray):
    with np.errstate(over="ignore", invalid="ignore"):
        if header.time_index is not None:
            step = float(header.text.get("timestep", "1").strip() or "1")
            if not math.isfinite(step) or step <= 0:
                raise ValueError("FCS $TIMESTEP must be positive and finite")
            values[:, header.time_index] *= step
        for index, channel in enumerate(header.channels):
            decades, zero = channel["pne"]
            if decades > 0:
                values[:, index] = 10 ** (decades * values[:, index] / channel["pnr"]) * zero
            if channel["png"] not in (0, 1):
                values[:, index] /= channel["png"]


def sample_from_header(header: FCSHeader, name: str):
    channels = []
    for index, channel in enumerate(header.channels):
        upper = np.array([[channel["pnr"]]], dtype=float)
        if header.time_index == index:
            step = float(header.text.get("timestep", "1").strip() or "1")
            if not math.isfinite(step) or step <= 0:
                raise ValueError("FCS $TIMESTEP must be positive and finite")
            upper *= step
        decades, zero = channel["pne"]
        with np.errstate(over="ignore", invalid="ignore"):
            if decades > 0:
                upper = 10 ** (decades * upper / channel["pnr"]) * zero
            if channel["png"] not in (0, 1):
                upper /= channel["png"]
        channels.append(
            Channel(
                name=channel["pnn"],
                label=channel["pns"],
                range=float(upper[0, 0]),
                transform=Transform(
                    kind="linear"
                    if channel["pnn"].lower().startswith(("fsc", "ssc", "time"))
                    else "logicle"
                ),
            )
        )
    sample = Sample(
        name=name,
        event_count=header.event_count,
        channels=channels,
        metadata=header.text.copy(),
        source="FCS",
    )
    sample.metadata.update(
        {f"cytoforge_analysis_{key}": value for key, value in header.analysis.items()}
    )
    warnings = header.warnings.copy()
    comp = None
    spill = header.text.get("spillover") or header.text.get("spill")
    if spill:
        try:
            tokens = [v.strip() for v in spill.split(",")]
            n = int(tokens[0])
            if not 1 <= n <= 512 or len(tokens) != 1 + n + n * n:
                raise ValueError("Invalid spillover dimensions")
            comp = Compensation(
                name=f"Acquisition · {name}"[:160],
                detectors=tokens[1 : n + 1],
                matrix=np.asarray(tokens[n + 1 :], float).reshape(n, n).tolist(),
                source="FCS $SPILLOVER",
            )
            validate_matrix(comp)
            if not set(comp.detectors) <= {c.name for c in channels}:
                raise ValueError("Spillover detector names do not match the channel names")
            sample.compensation_id = comp.id
        except (ValueError, TypeError) as exc:
            comp = None
            warnings.append(f"Acquisition compensation was not applied: {exc}")
    from .event_exports import restore_header

    comp, _ = restore_header(header, sample, comp)
    return sample, comp, warnings


def _ascii_integer(value: bytes) -> float:
    token = value.strip()
    digits = token[1:] if token.startswith((b"+", b"-")) else token
    if not digits.isdigit() or len(token) > 64:
        raise ValueError("FCS ASCII DATA contains an invalid integer token")
    try:
        integer = int(token)
    except ValueError as exc:
        raise ValueError("FCS ASCII DATA contains an invalid integer") from exc
    if abs(integer) > 2**53:
        raise ValueError("FCS integer exceeds exact float64 event-storage precision (2^53)")
    return float(integer)


def _ascii_tokens(handle, remaining: int, check: Check) -> Iterator[bytes]:
    pending = b""
    while remaining:
        check()
        chunk = handle.read(min(1024 * 1024, remaining))
        if not chunk:
            raise ValueError("FCS ASCII DATA is truncated")
        remaining -= len(chunk)
        pieces = re.split(rb"[ \t,\r\n]+", pending + chunk)
        pending = pieces.pop()
        if len(pending) > 64:
            raise ValueError("FCS ASCII integer token exceeds 64 characters")
        yield from (piece for piece in pieces if piece)
    if pending:
        yield pending


def fcs_blocks(handle: BinaryIO, header: FCSHeader, check: Check = nothing):
    if header.event_count == 0:
        return
    handle.seek(header.offset + header.data_start)
    columns = header.channel_count
    chunk_rows = max(1, CHUNK_VALUES // columns)
    if header.data_type == "I" and any(width % 8 for width in header.widths):
        from .fcs_integers import packed_blocks

        blocks = packed_blocks(
            handle,
            event_count=header.event_count,
            widths=header.widths,
            ranges=[int(header.text[f"p{index + 1}r"]) for index in range(columns)],
            chunk_rows=chunk_rows,
            check=check,
        )
        for block in blocks:
            preprocess(header, block)
            yield block
        return
    if header.variable_ascii:
        tokens = iter(_ascii_tokens(handle, header.data_stop - header.data_start + 1, check))
        for start in range(0, header.event_count, chunk_rows):
            check()
            count = min(chunk_rows, header.event_count - start)
            block = np.empty((count, columns), dtype=np.float64)
            try:
                for row in block:
                    for index in range(columns):
                        row[index] = _ascii_integer(next(tokens))
            except StopIteration as exc:
                raise ValueError("FCS ASCII DATA contains fewer values than $TOT × $PAR") from exc
            preprocess(header, block)
            yield block
        if next(tokens, None) is not None:
            raise ValueError("FCS ASCII DATA contains more values than $TOT × $PAR")
        return
    if header.data_type in {"F", "D"}:
        dtype = np.dtype(header.order + ("f4" if header.data_type == "F" else "f8"))
        for start in range(0, header.event_count, chunk_rows):
            check()
            count = min(chunk_rows, header.event_count - start)
            encoded = handle.read(count * columns * dtype.itemsize)
            if len(encoded) != count * columns * dtype.itemsize:
                raise ValueError("FCS DATA is truncated")
            raw = np.frombuffer(encoded, dtype=dtype)
            block = raw.reshape(count, columns).astype(np.float64)
            preprocess(header, block)
            yield block
        return
    widths = header.widths
    stride = sum(widths) if header.data_type == "A" else sum(widths) // 8
    for start in range(0, header.event_count, chunk_rows):
        check()
        count = min(chunk_rows, header.event_count - start)
        raw = handle.read(count * stride)
        if len(raw) != count * stride:
            raise ValueError("FCS DATA is truncated")
        block = np.empty((count, columns), dtype=np.float64)
        position = 0
        for index, width in enumerate(widths):
            if header.data_type == "A":
                for row in range(count):
                    block[row, index] = _ascii_integer(
                        raw[row * stride + position : row * stride + position + width]
                    )
                position += width
                continue
            byte_width = width // 8
            if byte_width in {1, 2, 4, 8}:
                integers = np.ndarray(
                    (count,),
                    dtype=header.order + f"u{byte_width}",
                    buffer=raw,
                    offset=position,
                    strides=(stride,),
                ).astype(np.uint64)
            else:
                octets = np.ndarray(
                    (count, byte_width),
                    dtype="u1",
                    buffer=raw,
                    offset=position,
                    strides=(stride, 1),
                )
                integers = np.zeros(count, dtype=np.uint64)
                for byte in range(byte_width):
                    shift = byte if header.order == "<" else byte_width - byte - 1
                    integers |= octets[:, byte].astype(np.uint64) << (8 * shift)
            range_value = int(header.text[f"p{index + 1}r"])
            mask = (1 << (range_value - 1).bit_length()) - 1
            if mask < 2**width - 1:
                integers &= np.uint64(mask)
            if np.any(integers > 2**53):
                raise ValueError("FCS integer exceeds exact float64 event-storage precision (2^53)")
            block[:, index] = integers
            position += byte_width
        preprocess(header, block)
        yield block


class _HashWriter:
    def __init__(self, handle):
        self.handle, self.digest = handle, hashlib.sha256()

    def write(self, content):
        self.digest.update(content)
        return self.handle.write(content)


def write_blocks(path: Path, shape: tuple[int, int], blocks, progress=nothing, check=nothing):
    """Write/hash .npy once without mmap resident pages or a second full-file pass."""
    pending = path.with_suffix(".pending")
    seen, nonfinite = 0, 0
    try:
        required = shape[0] * shape[1] * 8 + 4096
        if shutil.disk_usage(path.parent).free < required + 16 * 1024**2:
            raise ValueError(
                "Insufficient storage to import this dataset without filling the volume"
            )
        with pending.open("wb") as handle:
            writer = _HashWriter(handle)
            np.lib.format.write_array_header_1_0(
                writer, {"descr": "<f8", "fortran_order": False, "shape": shape}
            )
            for block in blocks:
                check()
                if block.ndim != 2 or block.shape[1] != shape[1]:
                    raise ValueError("Decoded event block does not match the acquisition panel")
                seen += len(block)
                if seen > shape[0]:
                    raise ValueError("Decoded event count exceeds acquisition metadata")
                nonfinite += int(np.count_nonzero(~np.isfinite(block)))
                writer.write(np.asarray(block, dtype="<f8").tobytes(order="C"))
                progress(stage="Reading events", events_read=seen, event_total=shape[0])
            if seen != shape[0]:
                raise ValueError("Decoded event count does not match acquisition metadata")
            handle.flush()
            os.fsync(handle.fileno())
        check()
        pending.replace(path)
        return writer.digest.hexdigest(), nonfinite
    finally:
        close = getattr(blocks, "close", None)
        if close is not None:
            close()
        pending.unlink(missing_ok=True)


def _nonfinite_warning(count: int) -> list[str]:
    return (
        [f"{count:,} nonfinite values retained; plots and statistics omit these values"]
        if count
        else []
    )


def stream_fcs(
    path: Path,
    name: str,
    directory: Path,
    skip: Callable[[int, int], bool] = lambda _index, _offset: False,
    progress: Progress = nothing,
    check: Check = nothing,
) -> list[ImportedDataset]:
    results, offset, total_values = [], 0, 0
    size = path.stat().st_size
    try:
        with path.open("rb") as handle:
            for index in range(1, MAX_DATASETS + 1):
                check()
                progress(
                    stage="Reading FCS metadata", dataset_index=index, events_read=0, event_total=0
                )
                header = read_fcs_header(handle, offset, size)
                total_values += header.event_count * header.channel_count
                if total_values > MAX_VALUES:
                    raise ValueError("File exceeds the current 150-million-value import limit")
                suffix = f" · Dataset {index}" if index > 1 or header.next_offset else ""
                sample, comp, notes = sample_from_header(header, name[: 160 - len(suffix)] + suffix)
                skipped = skip(index, offset)
                target = None if skipped else directory / f"{sample.id}.npy"
                result = ImportedDataset(sample, target, comp, notes, index, offset, skipped)
                results.append(result)
                if target is not None:
                    from .event_exports import METADATA_KEY, HashReader, decode, restore_origins

                    envelope = (
                        decode(header.text[METADATA_KEY])[0]
                        if METADATA_KEY in header.text
                        else None
                    )
                    reader = HashReader(handle) if envelope else handle
                    sample.sha256, nonfinite = write_blocks(
                        target,
                        (header.event_count, header.channel_count),
                        fcs_blocks(reader, header, check),
                        progress,
                        check,
                    )
                    if envelope:
                        if reader.digest.hexdigest() != envelope.data_sha256:
                            raise ValueError(
                                "Scientific FCS DATA failed its SHA-256 integrity check"
                            )
                        result.origins_path = restore_origins(target, sample, envelope, check)
                    notes.extend(_nonfinite_warning(nonfinite))
                if not header.next_offset:
                    break
                offset = header.next_offset
            else:
                raise ValueError(f"FCS file exceeds {MAX_DATASETS} chained datasets")
        for result in results:
            result.count = len(results)
            result.sample.metadata.update(
                cytoforge_dataset_index=str(result.index),
                cytoforge_dataset_offset=str(result.offset),
                cytoforge_dataset_count=str(result.count),
            )
        return results
    except BaseException:
        for result in results:
            if result.path is not None:
                result.path.unlink(missing_ok=True)
            if result.origins_path is not None:
                result.origins_path.unlink(missing_ok=True)
        raise


def _csv_rows(path: Path, names: list[str] | None, check: Check):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, strict=True)
        header = [value.strip() for value in next(reader, [])]
        if not header or len(header) > 512 or any(not value for value in header):
            raise ValueError("CSV requires a nonempty header with at most 512 channel names")
        if len(set(header)) != len(header):
            raise ValueError("CSV channel names must be unique")
        if names is not None and names != header:
            raise ValueError("CSV header changed during import")
        yield header
        for row in reader:
            if reader.line_num % 1024 == 0:
                check()
            if not row:
                continue
            if len(row) != len(header):
                raise ValueError(f"CSV row {reader.line_num}: expected {len(header)} values")
            yield reader.line_num, row


def stream_csv(
    path: Path,
    name: str,
    directory: Path,
    progress: Progress = nothing,
    check: Check = nothing,
) -> list[ImportedDataset]:
    progress(stage="Counting CSV events", dataset_index=1, events_read=0, event_total=0)
    with closing(_csv_rows(path, None, check)) as rows:
        names = next(rows)
        count = 0
        for count, _ in enumerate(rows, 1):
            if count * len(names) > MAX_VALUES:
                raise ValueError("CSV exceeds the current 150-million-value import limit")
            if count % 8192 == 0:
                check()
                progress(stage="Counting CSV events", events_read=count, event_total=0)
    sample = Sample(
        name=name[:160],
        event_count=count,
        channels=[Channel(name=n) for n in names],
        source="CSV",
        metadata={
            "cytoforge_dataset_index": "1",
            "cytoforge_dataset_offset": "0",
            "cytoforge_dataset_count": "1",
        },
    )
    target = directory / f"{sample.id}.npy"
    chunk_rows = max(1, min(8192, CHUNK_VALUES // len(names)))

    def blocks():
        with closing(_csv_rows(path, names, check)) as rows:
            next(rows)
            batch = []
            for row_number, row in rows:
                try:
                    batch.append([float(v) for v in row])
                except ValueError as exc:
                    raise ValueError(f"CSV row {row_number} contains a nonnumeric value") from exc
                if len(batch) == chunk_rows:
                    check()
                    yield np.asarray(batch, dtype=np.float64)
                    batch.clear()
            if batch:
                yield np.asarray(batch, dtype=np.float64)

    try:
        progress(stage="Reading events", events_read=0, event_total=count)
        sample.sha256, nonfinite = write_blocks(
            target, (count, len(names)), blocks(), progress, check
        )
        return [ImportedDataset(sample, target, None, _nonfinite_warning(nonfinite), 1, 0)]
    except BaseException:
        target.unlink(missing_ok=True)
        raise
