"""Compare full contour payloads and timing against a preserved local implementation."""

import argparse
import hashlib
import json
import resource
import subprocess
import sys
import time
import types
from pathlib import Path

import numpy as np
from cytoforge.graph_views import probability_view, resolved_options
from cytoforge.models import GraphOptions

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--baseline", type=Path, required=True)
parser.add_argument(
    "--output", type=Path, default=root / "artifacts/contour-performance-comparison.json"
)
parser.add_argument("--repetitions", type=int, default=5)
parser.add_argument("--memory-implementation", choices=["before", "after"])
args = parser.parse_args()
baseline_path = args.baseline.resolve()
assert baseline_path.is_relative_to(root) and args.output.resolve().is_relative_to(root)
baseline_source = baseline_path.read_text()
baseline = types.ModuleType("cytoforge.graph_views_before_performance")
baseline.__package__ = "cytoforge"
exec(compile(baseline_source, str(baseline_path), "exec"), baseline.__dict__)
implementations = {"before": baseline.probability_view, "after": probability_view}


def rss():
    if sys.platform == "linux":
        # ru_maxrss may retain a parent's pre-exec peak. VmHWM belongs to this
        # process's current memory map and avoids that inherited watermark.
        fields = dict(
            line.split(":", 1)
            for line in Path("/proc/self/status").read_text().splitlines()
            if ":" in line
        )
        return int(fields["VmHWM"].split()[0]) / 1024, "proc VmHWM"
    factor = 1 / 1024**2 if sys.platform == "darwin" else 1 / 1024
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * factor, "resource ru_maxrss"


def checker(bins):
    rows, columns = np.indices((bins, bins))
    occupied = (rows + columns) % 2 == 0
    return (columns[occupied] + 0.5) / bins, (rows[occupied] + 0.5) / bins


if args.memory_implementation:
    x, y = checker(384)
    options = resolved_options(GraphOptions(smooth=False, contour_spacing="10"), "contour")
    before, method = rss()
    payload = implementations[args.memory_implementation](
        x, y, [0, 1, 0, 1], [0, 1, 0, 1], 384, options
    )
    peak, _ = rss()
    print(
        json.dumps(
            {
                "implementation": args.memory_implementation,
                "peak_rss_mib": peak,
                "rss_before_calculation_mib": before,
                "measurement": method,
                "events": len(x),
                "vertices": payload["contour_vertices"],
                "drawing_limit": payload["contours_truncated"],
            }
        )
    )
    raise SystemExit(0)

options = resolved_options(GraphOptions(smooth=False, contour_spacing="10"), "contour")
cases = []
for bins in [160, 384]:
    x, y = np.meshgrid(np.arange(5.0), np.arange(5.0))
    cases.append(("25 sparse tied bins", x.ravel(), y.ravel(), [-1, 11, -1, 11], bins, options))
for bins in [256, 384]:
    x, y = checker(bins)
    cases.append(("Isolated checkerboard cells", x, y, [0, 1, 0, 1], bins, options))
cases.append(
    (
        "Smoothed single-event peak",
        np.array([0.5]),
        np.array([0.5]),
        [0, 1, 0, 1],
        16,
        resolved_options(GraphOptions(smooth=True, contour_spacing="2"), "contour"),
    )
)
rng = np.random.default_rng(2401)
values = rng.normal(size=(1_000_000, 2))
values[750000:] += [4, 2]
for bins in [160, 384]:
    cases.append(
        ("Million-event bimodal unsmoothed population", *values.T, [-7, 12, -7, 12], bins, options)
    )
results = []
for name, x, y, limits, bins, settings in cases:
    times = {key: [] for key in implementations}
    expected_hash = None
    for repetition in range(args.repetitions):
        # Alternate execution order; compare all fields, path order and exact
        # float values, including the same prefix at the declared drawing limit.
        order = ["before", "after"] if repetition % 2 == 0 else ["after", "before"]
        for key in order:
            start = time.perf_counter()
            payload = implementations[key](x, y, limits, limits, bins, settings)
            times[key].append(time.perf_counter() - start)
            assert payload["probability_denominator"] == payload["density_count"] == len(x)
            wire = json.dumps(
                payload, allow_nan=False, separators=(",", ":"), sort_keys=True
            ).encode()
            digest = hashlib.sha256(wire).hexdigest()
            if expected_hash is None:
                expected_hash = digest
            assert digest == expected_hash, (name, bins, key, "Full payload changed")
            vertices = payload["contour_vertices"]
            truncated = payload["contours_truncated"]
            del payload, wire
    before, after = (float(np.median(times[key])) for key in ["before", "after"])
    results.append(
        {
            "scenario": name,
            "events": len(x),
            "bins": bins,
            "repetitions": args.repetitions,
            "before_median_seconds": before,
            "after_median_seconds": after,
            "speedup": before / after,
            "times_seconds": times,
            "full_payload_sha256": expected_hash,
            "full_payload_unchanged": True,
            "vertices": vertices,
            "drawing_limit": truncated,
        }
    )
memory = []
for key in implementations:
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--baseline",
            str(baseline_path),
            "--memory-implementation",
            key,
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=root,
    )
    memory.append(json.loads(result.stdout))
evidence = {
    "status": "passed",
    "results": results,
    "memory": memory,
    "baseline_sha256": hashlib.sha256(baseline_source.encode()).hexdigest(),
    "current_source_sha256": hashlib.sha256(
        (root / "backend/cytoforge/graph_views.py").read_bytes()
    ).hexdigest(),
    "scope": "Interleaved source full-event calculations; excludes loading, JSON serialization, "
    "IPC, native rendering and PDF. Complete JSON payloads are compared outside timing. "
    "Peak RSS uses separate processes at 384 bins. Synthetic data on this host only.",
}
args.output.write_text(json.dumps(evidence, indent=2) + "\n")
print(json.dumps(evidence))
