"""Manually encoded standards fixtures, independent of the import decoder."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def packed_little_endian(values, widths):
    """Independent scalar bit encoder, with no padding between fields or events."""
    buffer, used, output = 0, 0, bytearray()
    for row in values:
        for value, width in zip(row, widths, strict=True):
            integer = int(value)
            if not 1 <= width <= 64 or not 0 <= integer < 1 << width:
                raise ValueError("Packed fixture value must fit its unsigned bit width")
            buffer |= integer << used
            used += width
            while used >= 8:
                output.append(buffer & 255)
                buffer >>= 8
                used -= 8
    if used:
        output.append(buffer)
    return bytes(output)


def fcs_dataset(
    values,
    names,
    *,
    data_type="F",
    widths=None,
    order="<",
    metadata=None,
    version="3.1",
    raw_data=None,
    delimiter="/",
    supplemental=None,
    analysis=None,
):
    values = np.asarray(values, dtype=object if data_type == "I" else None).reshape(-1, len(names))
    if widths is None:
        widths = [64 if data_type == "D" else 32] * len(names)
    if raw_data is None:
        if data_type in {"F", "D"}:
            raw_data = values.astype(order + ("f4" if data_type == "F" else "f8")).tobytes()
        elif data_type == "I":
            if any(width % 8 for width in widths):
                if order != "<":
                    raise ValueError("Packed fixture encoder requires little endian")
                raw_data = packed_little_endian(values, widths)
            else:
                raw_data = b"".join(
                    int(value).to_bytes(width // 8, "little" if order == "<" else "big")
                    for row in values
                    for value, width in zip(row, widths, strict=True)
                )
        elif all(w == "*" for w in widths):
            raw_data = b",,\t".join(str(int(v)).encode() for v in values.ravel())
        else:
            raw_data = b"".join(
                str(int(value)).rjust(width).encode()
                for row in values
                for value, width in zip(row, widths, strict=True)
            )
    text = {
        "BEGINANALYSIS": "0" * 20,
        "ENDANALYSIS": "0" * 20,
        "BEGINSTEXT": "0" * 20,
        "ENDSTEXT": "0" * 20,
        "BEGINDATA": "0" * 20,
        "ENDDATA": "0" * 20,
        "BYTEORD": "1,2,3,4" if order == "<" else "4,3,2,1",
        "DATATYPE": data_type,
        "MODE": "L",
        "NEXTDATA": "0" * 20,
        "TOT": str(len(values)),
        "PAR": str(len(names)),
    }
    for index, (name, width) in enumerate(zip(names, widths, strict=True), 1):
        text.update(
            {
                f"P{index}N": name,
                f"P{index}B": str(width),
                f"P{index}E": "0,0",
                f"P{index}R": str(2**width) if data_type == "I" else "262144",
            }
        )
    text.update(metadata or {})

    def encoded(mapping):
        pairs = [
            "$" + key + delimiter + str(value).replace(delimiter, delimiter * 2)
            for key, value in mapping.items()
        ]
        return (delimiter + delimiter.join(pairs) + delimiter).encode("utf-8")

    start = 256 + len(encoded(text))
    stop = start + len(raw_data) - 1
    text["BEGINDATA"], text["ENDDATA"] = f"{start:020d}", f"{stop:020d}"
    extra = encoded(supplemental) if supplemental else b""
    analyzed = encoded(analysis) if analysis else b""
    if extra:
        text["BEGINSTEXT"], text["ENDSTEXT"] = f"{stop + 1:020d}", f"{stop + len(extra):020d}"
    analysis_start = stop + len(extra) + 1 if analyzed else 0
    analysis_stop = analysis_start + len(analyzed) - 1 if analyzed else 0
    text["BEGINANALYSIS"], text["ENDANALYSIS"] = (f"{analysis_start:020d}", f"{analysis_stop:020d}")
    encoded_text = encoded(text)
    header = (
        b"FCS"
        + version.encode()
        + b"    "
        + b"".join(
            f"{offset:8d}".encode()
            for offset in [256, start - 1, start, stop, analysis_start, analysis_stop]
        )
    )
    return header.ljust(256, b" ") + encoded_text + raw_data + extra + analyzed


def fcs_chain(parts, padding=63):
    chained = []
    for index, part in enumerate(parts):
        if index < len(parts) - 1:
            distance = len(part) + padding
            part = part.replace(
                b"$NEXTDATA/00000000000000000000/",
                f"$NEXTDATA/{distance:020d}/".encode(),
                1,
            )
            part += b" " * padding
        chained.append(part)
    return b"".join(chained)


def desktop_fixture():
    first = fcs_dataset(
        [[24, 22, 100], [68, 46, 200]],
        ["X", "Y", "Time"],
        metadata={
            "P1G": "2",
            "P1R": "1000",
            "P3G": "999",
            "P3R": "2000",
            "TIMESTEP": "0.01",
            "SPILLOVER": "2,X,Y,1,0.2,0.1,1",
            "COM": "Budget $100 / sample/",
        },
    )
    second = fcs_dataset(
        [[255, 512, 10], [136, 1023, 20], [42, 1536, 30]],
        ["H", "Log", "Time"],
        data_type="I",
        widths=[8, 16, 32],
        order=">",
        metadata={
            "P1R": "100",
            "P2R": "1024",
            "P2E": "4,0",
            "P2G": "2",
            "P3R": "65536",
            "P3G": "11",
            "TIMESTEP": "0.5",
        },
    )
    third = fcs_dataset(
        [[-0.5, 1e12], [np.nan, np.inf], [1e-300, -0.0]],
        ["Voltage", "Area"],
        data_type="D",
        order=">",
        metadata={"P1R": "5", "P2R": "5"},
    )
    return fcs_chain([first, second, third])


if __name__ == "__main__":
    directory = Path("artifacts/import-fixture")
    directory.mkdir(parents=True, exist_ok=True)
    content = desktop_fixture()
    (directory / "three-datasets.fcs").write_bytes(content)
    # The second dataset is validly linked but its DATA payload is truncated.
    (directory / "broken-chain.fcs").write_bytes(content[:-8])
    (directory / "good.csv").write_text('"X, area",Y\n-2,3\n4,5\n', encoding="utf-8-sig")
    (directory / "bad.csv").write_text("X,Y\n1,wrong\n", encoding="utf-8")
    empty = fcs_dataset([], ["X"], data_type="A", widths=["*"])
    start, stop = int(empty[26:34]), int(empty[34:42])
    data = bytearray(empty)
    data[26:42] = b"       0       0"
    data = (
        bytes(data)
        .replace(f"$BEGINDATA/{start:020d}/".encode(), b"$BEGINDATA/00000000000000000000/")
        .replace(f"$ENDDATA/{stop:020d}/".encode(), b"$ENDDATA/00000000000000000000/")
    )
    (directory / "zero-event-ASCII.fcs").write_bytes(data)
    (directory / "truth.json").write_text(
        json.dumps(
            {
                "first_acquired": [[12, 22, 1], [34, 46, 2]],
                "first_compensated": [[10, 20, 1], [30, 40, 2]],
                "dataset_events": [2, 3, 3],
                "dataset_parameters": [3, 3, 2],
                "third_nonfinite_values": 2,
            },
            indent=2,
        )
        + "\n"
    )
