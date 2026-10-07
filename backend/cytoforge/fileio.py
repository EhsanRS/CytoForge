"""Explicit lifetimes for owned array mappings and atomic file publication."""

import errno
import json
import os
import time
from contextlib import contextmanager

import numpy as np


def close_array(values):
    """Close an owned mapping after all consumers have finished with its views."""
    if isinstance(values, np.memmap):
        values._mmap.close()


def _load_mapping(path):
    values = np.load(path, mmap_mode="r", allow_pickle=False)
    if not isinstance(values, np.ndarray):
        values.close()
        raise ValueError("Event data must contain a NumPy array")
    return values


@contextmanager
def mapped_array(path):
    """Keep a read-only array mapped only for the duration of this scope."""
    values = _load_mapping(path)
    try:
        yield values
    finally:
        close_array(values)


def load_validated_array(path, validate):
    """Transfer a valid mapping to its caller; close rejected data before raising."""
    values = _load_mapping(path)
    try:
        validate(values)
    except BaseException:
        close_array(values)
        raise
    return values


def read_text(path):
    """Read UTF-8 text, allowing a brief replacement conflict to finish."""
    deadline = time.monotonic() + 1.0
    delay = 0.005
    while True:
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError as exc:
            # Windows' CRT text opener can report EACCES without a winerror.
            if exc.errno != errno.EACCES and getattr(exc, "winerror", None) not in {5, 32, 33}:
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, 0.1)


def read_json(path):
    """Read an atomically published JSON document without retrying invalid data."""
    return json.loads(read_text(path))


def replace_file(source, target):
    """Allow a brief Windows reader to finish without hiding persistent I/O errors."""
    deadline = time.monotonic() + 1.0
    delay = 0.005
    while True:
        try:
            os.replace(source, target)
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in {5, 32, 33}:
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, 0.1)
