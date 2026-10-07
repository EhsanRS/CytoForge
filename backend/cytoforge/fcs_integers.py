"""Bounded decoding of the little-endian, LSB-first packed FCS integer stream."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import BinaryIO

import numpy as np


def packed_blocks(
    handle: BinaryIO,
    *,
    event_count: int,
    widths: list[int],
    ranges: list[int],
    chunk_rows: int,
    check: Callable[[], None],
) -> Iterator[np.ndarray]:
    """Consume each encoded byte once, carrying at most one byte between blocks.

    Events have no padding or delimiters; the last byte may contain unused bits.
    Gather at most nine octets per field without expanding the input to a bit array.
    This also preserves DATA hashes when the handle is a scientific HashReader.
    """
    stride = sum(widths)
    carry, bit_offset = b"", 0
    masks = [
        min((1 << width) - 1, (1 << (value - 1).bit_length()) - 1)
        for width, value in zip(widths, ranges, strict=True)
    ]
    for start in range(0, event_count, chunk_rows):
        check()
        count = min(chunk_rows, event_count - start)
        length = (bit_offset + count * stride + 7) // 8
        encoded = handle.read(length - len(carry))
        if len(encoded) != length - len(carry):
            raise ValueError("FCS DATA is truncated")
        raw = carry + encoded
        octets = np.frombuffer(raw, dtype=np.uint8)
        starts = np.arange(count, dtype=np.intp) * stride + bit_offset
        block = np.empty((count, len(widths)), dtype=np.float64)
        position = 0
        for index, (width, mask) in enumerate(zip(widths, masks, strict=True)):
            check()
            byte_positions, shifts = np.divmod(starts + position, 8)
            integers = octets[byte_positions].astype(np.uint64) >> shifts.astype(np.uint64)
            for byte in range(1, (width + 14) // 8):
                active = width + shifts > byte * 8
                if not np.any(active):
                    break
                piece = octets[byte_positions[active] + byte].astype(np.uint64)
                integers[active] |= piece << (byte * 8 - shifts[active]).astype(np.uint64)
            integers &= np.uint64(mask)
            if np.any(integers > 2**53):
                raise ValueError("FCS integer exceeds exact float64 event-storage precision (2^53)")
            block[:, index] = integers
            position += width
        bit_offset = (bit_offset + count * stride) % 8
        carry = raw[-1:] if bit_offset else b""
        yield block
