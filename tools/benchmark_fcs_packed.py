"""Verify every imported packed value and measure bounded process memory at scale."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import resource
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from import_fixture import fcs_dataset, packed_little_endian

ROOT = Path(__file__).resolve().parents[1]
WIDTHS = [3, 18, 27, 64, 9]


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def prepare(path, events):
    prefix = fcs_dataset(
        np.empty((0, 5)),
        ["Flag", "X", "Y", "Index", "Time"],
        data_type="I",
        widths=WIDTHS,
        raw_data=b"",
        metadata={
            "TOT": str(events),
            "P2R": "100000",
            "P3G": "2",
            "P4R": str((1 << 40) + 1),
            "P5G": "999",
            "TIMESTEP": "0.01",
        },
    )
    start = int(prefix[26:34])
    stop = start + (events * sum(WIDTHS) + 7) // 8 - 1
    prefix = bytearray(
        prefix.replace(
            f"$ENDDATA/{start - 1:020d}/".encode(),
            f"$ENDDATA/{stop:020d}/".encode(),
            1,
        )
    )
    prefix[26:34] = f"{start if stop <= 99_999_999 else 0:8d}".encode()
    prefix[34:42] = f"{stop if stop <= 99_999_999 else 0:8d}".encode()
    header = io.BytesIO()
    np.lib.format.write_array_header_1_0(
        header,
        {"descr": "<f8", "fortran_order": False, "shape": (events, 5)},
    )
    truth = hashlib.sha256(header.getvalue())
    with path.open("wb") as handle:
        handle.write(prefix)
        # Full generation blocks end on a byte boundary; only the final one has
        # unused bits. The decoder's 52,428-event blocks do cross byte boundaries.
        for first in range(0, events, 8192):
            rows = np.arange(first, min(first + 8192, events), dtype=np.int64)
            encoded_rows = (
                [
                    int(row) % 8,
                    (int(row) * 17) % (1 << 18),
                    (int(row) * 31) % (1 << 27),
                    (1 << 63) | int(row),
                    int(row) % 512,
                ]
                for row in rows
            )
            handle.write(packed_little_endian(encoded_rows, WIDTHS))
            expected = np.column_stack(
                (
                    rows % 8,
                    ((rows * 17) % (1 << 18)) & ((1 << 17) - 1),
                    ((rows * 31) % (1 << 27)) / 2,
                    rows,
                    (rows % 512) * 0.01,
                )
            ).astype("<f8")
            truth.update(expected.tobytes())
    assert path.stat().st_size == stop + 1
    return truth.hexdigest()


def worker(path):
    from cytoforge.imports import stream_fcs

    baseline = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    began = time.perf_counter()
    imported = stream_fcs(path, path.name, path.parent)[0]
    elapsed = time.perf_counter() - began
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    actual = digest(imported.path)
    assert actual == imported.sample.sha256
    print(
        json.dumps(
            dict(
                events=imported.sample.event_count,
                widths_bits=WIDTHS,
                input_bytes=path.stat().st_size,
                output_bytes=imported.path.stat().st_size,
                seconds=elapsed,
                events_per_second=imported.sample.event_count / elapsed,
                baseline_peak_rss_bytes=baseline,
                peak_rss_bytes=peak,
                rss_growth_bytes=max(0, peak - baseline),
                output_sha256=actual,
            )
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker)
        return
    measurements = []
    with tempfile.TemporaryDirectory(prefix="packed-fcs-benchmark-", dir=ROOT / ".tmp") as owned:
        directory = Path(owned)
        for events in [131_075, 1_048_579, 4_194_307]:
            path = directory / f"packed-{events}.fcs"
            truth = prepare(path, events)
            output = subprocess.run(
                [sys.executable, __file__, "--worker", str(path)],
                capture_output=True,
                text=True,
                check=True,
            )
            measured = json.loads(output.stdout)
            assert measured["output_sha256"] == truth, measured
            assert measured["rss_growth_bytes"] < 64 * 1024**2, measured
            measured["every_preprocessed_value_verified_by_independent_sha256"] = True
            measurements.append(measured)
            print(f"Verified {events:,} events in {measured['seconds']:.3f}s", flush=True)
            for generated in directory.iterdir():
                generated.unlink()
    evidence = dict(
        status="passed",
        verified_at=datetime.now(UTC).isoformat(),
        platform=sys.platform,
        measurements=measurements,
        scope=(
            "Source Python packed FCS import on this Linux host, including decode/hash/write/fsync"
        ),
        excludes=[
            "fixture generation",
            "stored-file verification reread",
            "multipart transfer",
            "workspace commit",
            "Electron startup",
            "plots",
            "other platforms",
        ],
        truth="Independent scalar bit encoder and independently computed preprocessed output hash",
        memory="Peak RSS before and after import, before verification, in separate child processes",
        generated_test_data_removed=True,
        source_sha256={
            name: digest(ROOT / name)
            for name in [
                "backend/cytoforge/imports.py",
                "backend/cytoforge/fcs_integers.py",
                "tools/import_fixture.py",
                "tools/benchmark_fcs_packed.py",
            ]
        },
        remaining=[
            "big-endian packed instrument truth",
            "FCS 3.2 mixed datatypes",
            "histogram modes",
            "full instrument coverage",
            "resumable transfer/import",
            "cross-platform and native desktop validation",
        ],
    )
    (ROOT / "artifacts/fcs-packed-benchmark.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print("Passed packed FCS benchmark: artifacts/fcs-packed-benchmark.json")


if __name__ == "__main__":
    main()
