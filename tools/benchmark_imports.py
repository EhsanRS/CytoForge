"""Measure real import RSS and verify every stored value with independent output hashes."""

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
from import_fixture import fcs_dataset


def header_digest(events, channels):
    prefix = io.BytesIO()
    np.lib.format.write_array_header_1_0(
        prefix, {"descr": "<f8", "fortran_order": False, "shape": (events, channels)}
    )
    return hashlib.sha256(prefix.getvalue())


def prepare_fcs(path, events):
    names = ["FSC-A", "SSC-A", "M1", "M2", "M3", "M4", "M5", "Time"]
    prefix = fcs_dataset(
        np.empty((0, 8)),
        names,
        raw_data=b"",
        metadata={"TOT": str(events), "P3G": "2", "P8R": "16777216", "TIMESTEP": "0.01"},
    )
    start = int(prefix[26:34])
    stop = start + events * 8 * 4 - 1
    prefix = prefix.replace(
        f"$ENDDATA/{start - 1:020d}/".encode(), f"$ENDDATA/{stop:020d}/".encode(), 1
    )
    prefix = bytearray(prefix)
    prefix[26:34] = f"{start if stop <= 99_999_999 else 0:8d}".encode()
    prefix[34:42] = f"{stop if stop <= 99_999_999 else 0:8d}".encode()
    digest = header_digest(events, 8)
    with path.open("wb") as handle:
        handle.write(prefix)
        for first in range(0, events, 8192):
            rows = np.arange(first, min(first + 8192, events), dtype=np.int64)
            raw = (rows[:, None] % 4096 + np.arange(1, 9)[None, :]).astype("<f4")
            raw[:, 7] = rows
            handle.write(raw.tobytes())
            expected = raw.astype("<f8")
            expected[:, 2] /= 2
            expected[:, 7] *= 0.01
            digest.update(expected.tobytes())
    return digest.hexdigest()


def prepare_csv(path, events):
    digest = header_digest(events, 3)
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("X,Y,Z\n")
        for first in range(0, events, 8192):
            rows = np.arange(first, min(first + 8192, events), dtype=np.int64)
            block = np.column_stack((rows, rows % 101, -rows))
            handle.writelines(f"{a},{b},{c}\n" for a, b, c in block)
            digest.update(block.astype("<f8").tobytes())
    return digest.hexdigest()


def worker(path, kind):
    from cytoforge.imports import stream_csv, stream_fcs

    baseline = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    started = time.perf_counter()
    result = (stream_fcs if kind == "fcs" else stream_csv)(path, path.name, path.parent)[0]
    duration = time.perf_counter() - started
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    with result.path.open("rb") as handle:
        stored_digest = hashlib.file_digest(handle, "sha256").hexdigest()
    assert stored_digest == result.sample.sha256
    array = np.load(result.path, mmap_mode="r", allow_pickle=False)
    assert array.shape == (result.sample.event_count, len(result.sample.acquisition_channels))
    first, last = array[0].tolist(), array[-1].tolist()
    del array
    print(
        json.dumps(
            {
                "format": kind,
                "events": result.sample.event_count,
                "parameters": len(result.sample.channels),
                "input_bytes": path.stat().st_size,
                "stored_bytes": result.path.stat().st_size,
                "seconds": duration,
                "baseline_peak_rss_bytes": baseline,
                "peak_rss_bytes": peak,
                "rss_growth_bytes": max(0, peak - baseline),
            "output_sha256": stored_digest,
                "first_event": first,
                "last_event": last,
            }
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--format", choices=["fcs", "csv"])
    args = parser.parse_args()
    if args.worker:
        worker(args.worker, args.format)
        return
    results = []
    with tempfile.TemporaryDirectory(prefix="import-benchmark-", dir=Path(".tmp")) as folder:
        directory = Path(folder)
        for kind, events in [
            ("fcs", 1_000_000),
            ("fcs", 8_000_000),
            ("csv", 500_000),
            ("csv", 2_000_000),
        ]:
            path = directory / f"events-{events}.{kind}"
            truth = (prepare_fcs if kind == "fcs" else prepare_csv)(path, events)
            process = subprocess.run(
                [sys.executable, __file__, "--worker", str(path), "--format", kind],
                check=True,
                capture_output=True,
                text=True,
            )
            result = json.loads(process.stdout)
            assert result["output_sha256"] == truth, result
            result["all_values_verified_by_independent_sha256"] = True
            results.append(result)
            for output in directory.iterdir():
                output.unlink()
    # Acquisition size grows eightfold; peak event-decoding memory must remain bounded.
    assert results[1]["rss_growth_bytes"] < 64 * 1024**2, results[1]
    assert results[3]["rss_growth_bytes"] < 64 * 1024**2, results[3]
    report = {
        "status": "passed",
        "verified_at": datetime.now(UTC).isoformat(),
        "platform": sys.platform,
        "measurements": results,
        "scope": "source Python importer on this Linux host, including decode/hash/write/fsync",
        "excludes": [
            "input generation",
            "multipart transfer",
            "workspace commit",
            "plots",
            "Electron startup",
            "verification reread of the stored file",
        ],
        "memory_measure": (
            "process peak RSS before and after import; no full-array scan after decoding"
        ),
        "truth": (
            "independently generated SHA-256 of every preprocessed float64 value and NumPy header"
        ),
    }
    Path("artifacts/import-benchmark.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
