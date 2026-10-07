"""Acquisition-shared, column-addressable temporary storage for complete joint events."""

from __future__ import annotations

import errno
import mmap
import os
import shutil
import tempfile
from pathlib import Path

import numpy as np

DISK_HEADROOM_BYTES = 256 * 1024**2


def reserve_file(handle, size):
    """Reserve physical blocks when supported; ordinary writes handle the fallback."""
    allocate = getattr(os, "posix_fallocate", None)
    if allocate is not None:
        try:
            allocate(handle.fileno(), 0, size)
            return True
        except OSError as error:
            if error.errno not in {
                errno.EINVAL,
                getattr(errno, "ENOSYS", errno.EINVAL),
                getattr(errno, "EOPNOTSUPP", errno.EINVAL),
            }:
                raise
    handle.truncate(size)
    return False


def storage_error(error, size):
    if error.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)}:
        return OSError(
            error.errno,
            f"Joint comparison temporary storage needs {size / 1024**2:.1f} MiB; "
            "the filesystem has insufficient space or quota. Free space and retry.",
        )
    return error


class ColumnMatrix:
    """Finite joint rows, read one coordinate at a time without materializing a matrix."""

    shape: tuple[int, int]

    def __len__(self):
        return self.shape[0]

    def column(self, axis, events=None):
        raise NotImplementedError


class PopulationColumns(ColumnMatrix):
    def __init__(self, acquisition, rows):
        self.acquisition = acquisition
        self.rows = rows
        self.shape = (len(rows), acquisition.shape[1])

    def column(self, axis, events=None):
        rows = self.rows if events is None else self.rows[events]
        values = self.acquisition.values[rows, axis]
        self.acquisition.release_pages()
        return values


class PooledColumns(ColumnMatrix):
    def __init__(self, members, dimensions):
        self.members = [member for member in members if len(member)]
        if any(member.shape[1] != dimensions for member in self.members):
            raise ValueError("Joint pool coordinates do not match")
        self.shape = (sum(len(member) for member in self.members), dimensions)

    def column(self, axis, events=None):
        result = np.empty(len(self) if events is None else len(events), dtype=np.float64)
        offset, cursor = 0, 0
        for member in self.members:
            stop = offset + len(member)
            if events is None:
                result[offset:stop] = member.column(axis)
            else:
                # Tree partitions preserve original row order, so each source is a span.
                start = int(np.searchsorted(events, offset))
                end = int(np.searchsorted(events, stop))
                if end > start:
                    result[cursor : cursor + end - start] = member.column(
                        axis, events[start:end] - offset
                    )
                    cursor += end - start
            offset = stop
        return result


class AcquisitionColumns:
    def __init__(self, path, selection, dimensions):
        self.path = path
        self.event_ids = np.flatnonzero(selection)
        self.finite = np.ones(len(self.event_ids), dtype=bool)
        self.written = set()
        self.shape = (len(self.event_ids), dimensions)
        self.values = None if len(self.event_ids) else np.empty(self.shape)
        self.handle = None
        self.required_bytes = 0
        self.reserved = True
        if len(self.event_ids):
            self.handle = path.open("w+b")
            try:
                np.lib.format.write_array_header_1_0(
                    self.handle,
                    {
                        "descr": np.lib.format.dtype_to_descr(np.dtype(np.float64)),
                        "fortran_order": True,
                        "shape": self.shape,
                    },
                )
                self.offset = self.handle.tell()
                self.required_bytes = self.offset + len(self.event_ids) * dimensions * 8
                self.handle.flush()
                self.reserved = reserve_file(self.handle, self.required_bytes)
            except BaseException as error:
                self.close()
                path.unlink(missing_ok=True)
                if isinstance(error, OSError):
                    raise storage_error(error, self.required_bytes) from error
                raise

    def release_pages(self):
        mapping = getattr(self.values, "_mmap", None)
        if mapping is not None and hasattr(mapping, "madvise") and hasattr(mmap, "MADV_DONTNEED"):
            try:
                mapping.madvise(mmap.MADV_DONTNEED)
            except OSError:
                pass

    def write(self, axis, column):
        if not 0 <= axis < self.shape[1] or axis in self.written:
            raise ValueError("Joint coordinate index is invalid or already written")
        values = np.asarray(column[self.event_ids], dtype=np.float64)
        if values.shape != (len(self.event_ids),):
            raise ValueError("A joint coordinate must contain one value per original event")
        if self.handle is not None:
            try:
                self.handle.seek(self.offset + axis * len(values) * 8)
                buffer = memoryview(values).cast("B")
                if self.handle.write(buffer) != len(buffer):
                    raise OSError(errno.EIO, "Incomplete joint coordinate write")
                self.handle.flush()
            except OSError as error:
                raise storage_error(error, self.required_bytes) from error
        self.finite &= np.isfinite(values)
        self.written.add(axis)

    def rows(self, selection):
        if len(self.written) != self.shape[1]:
            raise ValueError("Joint coordinates are incomplete")
        if self.values is None:
            try:
                self.handle.flush()
                os.fsync(self.handle.fileno())
                self.handle.close()
                self.handle = None
                self.values = np.load(self.path, mmap_mode="r", allow_pickle=False)
            except OSError as error:
                raise storage_error(error, self.required_bytes) from error
        rows = np.flatnonzero(selection[self.event_ids] & self.finite)
        return PopulationColumns(self, rows)

    def shared_count(self, target, control):
        return int(np.count_nonzero(target[self.event_ids] & control[self.event_ids] & self.finite))

    def close(self):
        handle, self.handle = self.handle, None
        try:
            if handle is not None:
                handle.close()
        finally:
            mapping = getattr(self.values, "_mmap", None)
            if mapping is not None:
                mapping.close()


class JointStorage:
    def __init__(self, directory: Path, dimensions: int, enabled=True):
        if not 1 <= dimensions <= 64:
            raise ValueError("Joint storage supports 1–64 coordinates")
        self.directory = directory
        self.dimensions = dimensions
        self.enabled = enabled
        self.samples = {}
        self.temporary = None

    def __enter__(self):
        if self.enabled:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.temporary = tempfile.TemporaryDirectory(
                prefix="population-joint-", dir=self.directory
            )
        return self

    def prepare(self, sample_id, selection):
        if self.enabled:
            if sample_id in self.samples:
                raise ValueError("This acquisition's joint storage was already prepared")
            if selection.ndim != 1 or selection.dtype.kind != "b":
                raise ValueError("Joint storage requires a one-dimensional event mask")
            count = int(np.count_nonzero(selection))
            if count:
                required = count * self.dimensions * 8 + 4096
                unreserved = sum(
                    sample.required_bytes
                    for sample in self.samples.values()
                    if not sample.reserved and sample.values is None
                )
                free = shutil.disk_usage(self.directory).free
                if free < required + unreserved + DISK_HEADROOM_BYTES:
                    raise OSError(
                        errno.ENOSPC,
                        f"Joint comparison temporary storage needs "
                        f"{(required + unreserved) / 1024**2:.1f} MiB plus "
                        f"{DISK_HEADROOM_BYTES / 1024**2:.0f} MiB headroom, "
                        f"but only {free / 1024**2:.1f} MiB is available. Free space and retry.",
                    )
            self.samples[sample_id] = AcquisitionColumns(
                Path(self.temporary.name) / f"{len(self.samples)}.npy", selection, self.dimensions
            )

    def write(self, axis, columns):
        if self.enabled:
            for sample_id, column in columns.items():
                self.samples[sample_id].write(axis, column)

    def __exit__(self, *error):
        failures = []
        try:
            for sample in self.samples.values():
                try:
                    sample.close()
                except OSError as failure:
                    failures.append(failure)
        finally:
            if self.temporary is not None:
                self.temporary.cleanup()
        if failures and error[0] is None:
            raise failures[0]
