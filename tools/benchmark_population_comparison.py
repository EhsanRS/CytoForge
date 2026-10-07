"""Measure exact joint storage and the full comparison worker on owned synthetic data.

Run after sourcing tools/env.sh. Each measurement uses a separate process; generated
acquisitions and scratch files stay in this checkout and are removed after the run.
The dense baseline uses the current partition algorithm, so its memory measurement
does not include the former full-matrix copy at every recursive node.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from cytoforge.comparison_storage import JointStorage, PooledColumns
from cytoforge.models import (
    AnalysisInput,
    Channel,
    ComparisonParameter,
    Gate,
    PopulationComparisonRequest,
    Sample,
    Workspace,
    new_id,
)
from cytoforge.population_comparison import (
    calculate,
    load_artifact,
    summary_probability,
    validate_request,
)
from cytoforge.population_statistics import probability_tree
from cytoforge.science import Engine
from cytoforge.store import Store

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def json_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def memory():
    peak = None
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak *= 1 if sys.platform == "darwin" else 1024
    except ImportError:
        pass
    resident = None
    if sys.platform == "linux":
        resident = int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    return {"peak_rss_bytes": peak, "current_rss_bytes": resident}


def populations(workspace, request, engine):
    samples = {s.id: s for s in workspace.samples}
    masks = {
        (s.sample_id, s.gate_id): engine.mask(workspace, samples[s.sample_id], s.gate_id)
        for s in [*request.inputs, *request.controls]
    }
    controls = {}
    for source in request.controls:
        mask = masks[source.sample_id, source.gate_id]
        controls.setdefault(source.sample_id, np.zeros(len(mask), dtype=bool))[:] |= mask
    return masks, controls


def joint_outcome(workspace, request, controls, population, pool):
    full_reference = pool()
    tree = probability_tree(full_reference, request.probability_bins, request.minimum_bin_events)
    outcomes, counts, baseline_trees = [], [], {}
    roles = [("target", source) for source in request.inputs] + [
        ("control_baseline", source) for source in request.controls
    ]
    for role, source in roles:
        target = population(source)
        if role == "target":
            actual_tree, reference_count = tree, len(full_reference)
        else:
            if source.sample_id not in baseline_trees:
                reference = pool(source.sample_id)
                baseline_trees[source.sample_id] = (
                    probability_tree(
                        reference, request.probability_bins, request.minimum_bin_events
                    ),
                    len(reference),
                )
            actual_tree, reference_count = baseline_trees[source.sample_id]
        probability = actual_tree.compare(target)
        assert sum(probability["test_counts"]) == len(target)
        assert sum(probability["control_counts"]) == reference_count
        outcomes.append(
            {
                "role": role,
                "sample_id": source.sample_id,
                "gate_id": source.gate_id,
                "finite_count": len(target),
                "control_finite_count": reference_count,
                "probability": summary_probability(probability),
            }
        )
        counts.append(probability)
    return {
        "control_tree_sha256": json_digest(tree.serialize()),
        "all_joint_counts_sha256": json_digest(counts),
        "joint_outcomes": outcomes,
        "deduplicated_control_selected_count": sum(int(m.sum()) for m in controls.values()),
    }


def worker(method, configuration):
    config = json.loads(configuration.read_text())
    store = Store(Path(config["profile"]))
    try:
        workspace = store.get(config["workspace_id"])
        request = PopulationComparisonRequest.model_validate(config["request"])
        engine = Engine(store)
        started = time.perf_counter()
        stages = {}
        if method == "full":

            def progress(stage, fraction):
                if stage == "Building joint probability partitions":
                    stages["before_joint"] = memory() | {
                        "elapsed_seconds": time.perf_counter() - started
                    }

            result = calculate(workspace, request, engine, new_id(), progress)
            _, metadata = load_artifact(store, workspace.id, result)
            outcome = {
                "control_tree_sha256": json_digest(metadata["joint"]["control_tree"]),
                "joint_outcomes": [
                    row.model_dump(
                        include={"role", "finite_count", "control_finite_count", "probability"}
                    )
                    | {"sample_id": row.source.sample_id, "gate_id": row.source.gate_id}
                    for row in result.joint_rows
                ],
                "univariate_rows": len(result.rows),
                "count_artifact_sha256": result.data.sha256,
                "scratch_cleaned": not list((store.root / "tmp").glob("population-joint-*")),
            }
            assert outcome["scratch_cleaned"]
        else:
            masks, controls = populations(workspace, request, engine)
            if method == "dense":
                values = {}
                for sample in workspace.samples:
                    raw = np.load(store.data_path(workspace.id, sample.id), allow_pickle=False)
                    finite = np.all(np.isfinite(raw), axis=1)
                    values[sample.id] = raw, finite
                members = {
                    sid: values[sid][0][mask & values[sid][1]] for sid, mask in controls.items()
                }

                def population(source):
                    raw, finite = values[source.sample_id]
                    return raw[masks[source.sample_id, source.gate_id] & finite]

                def pool(exclude=None):
                    return np.concatenate([v for sid, v in members.items() if sid != exclude])

                stages["storage_ready"] = memory() | {
                    "elapsed_seconds": time.perf_counter() - started
                }
                outcome = joint_outcome(workspace, request, controls, population, pool)
            else:
                with JointStorage(
                    store.root / "column-scratch", len(request.parameters)
                ) as storage:
                    for sample in workspace.samples:
                        selected = np.zeros(sample.event_count, dtype=bool)
                        for (sid, _), mask in masks.items():
                            if sid == sample.id:
                                selected |= mask
                        storage.prepare(sample.id, selected)
                    # Include the source mapping and staging costs in the peak measurement.
                    for axis in range(len(request.parameters)):
                        for sample in workspace.samples:
                            raw = np.load(
                                store.data_path(workspace.id, sample.id),
                                mmap_mode="r",
                                allow_pickle=False,
                            )
                            storage.write(axis, {sample.id: raw[:, axis]})
                            del raw
                    members = {
                        sid: storage.samples[sid].rows(mask) for sid, mask in controls.items()
                    }

                    def population(source):
                        return storage.samples[source.sample_id].rows(
                            masks[source.sample_id, source.gate_id]
                        )

                    def pool(exclude=None):
                        return PooledColumns(
                            [v for sid, v in members.items() if sid != exclude],
                            len(request.parameters),
                        )

                    stages["storage_ready"] = memory() | {
                        "elapsed_seconds": time.perf_counter() - started
                    }
                    outcome = joint_outcome(workspace, request, controls, population, pool)
                    outcome["temporary_acquisition_files"] = len(
                        list(Path(storage.temporary.name).glob("*.npy"))
                    )
                assert not list((store.root / "column-scratch").iterdir())
                outcome["scratch_cleaned"] = True
        outcome.update(
            method=method,
            elapsed_seconds=time.perf_counter() - started,
            memory=memory(),
            stages=stages,
        )
        (configuration.parent / f"{method}-result.json").write_text(
            json.dumps(outcome, indent=2) + "\n"
        )
    finally:
        store.close()


def create_case(directory, events, dimensions):
    store = Store(directory / "profile")
    try:
        sizes = [events // 4, events // 4, events - 2 * (events // 4)]
        samples = [
            Sample(
                name=f"Synthetic acquisition {index + 1}",
                channels=[Channel(name=f"P{axis}") for axis in range(dimensions)],
                event_count=count,
            )
            for index, count in enumerate(sizes)
        ]
        gates = [
            Gate(
                sample_id=samples[0].id,
                name="Overlapping control",
                kind="range",
                x="P0",
                bounds=[-0.5, 1],
            ),
            Gate(
                sample_id=samples[2].id,
                name="Target subset",
                kind="range",
                x="P0",
                bounds=[-0.25, 1.25],
            ),
        ]
        workspace = Workspace(
            name="Owned synthetic comparison benchmark", samples=samples, gates=gates
        )
        for index, sample in enumerate(samples):
            path = store.data_path(workspace.id, sample.id)
            raw = np.lib.format.open_memmap(
                path, mode="w+", dtype=np.float64, shape=(sample.event_count, dimensions)
            )
            rng = np.random.default_rng(8150 + index)
            scale = (np.arange(dimensions) % 7 + 1) / 3
            for offset in range(0, sample.event_count, 32768):
                count = min(32768, sample.event_count - offset)
                raw[offset : offset + count] = rng.normal(size=(count, dimensions)) * scale
            if index == 2:
                raw[:, 0] += 0.7
            raw[0, 0], raw[1, -1] = np.nan, np.inf
            raw.flush()
            raw._mmap.close()
            sample.sha256 = digest(path)
        workspace = store.create(workspace)
        request = PopulationComparisonRequest(
            revision=workspace.revision,
            name="Synthetic shifted cohort",
            inputs=[
                AnalysisInput(sample_id=samples[2].id),
                AnalysisInput(sample_id=samples[2].id, gate_id=gates[1].id),
            ],
            controls=[AnalysisInput(sample_id=s.id) for s in samples[:2]]
            + [AnalysisInput(sample_id=samples[0].id, gate_id=gates[0].id)],
            parameters=[ComparisonParameter(channel=f"P{axis}") for axis in range(dimensions)],
            histogram_bins=128,
        )
        validate_request(workspace, request)
        configuration = directory / "configuration.json"
        configuration.write_text(
            json.dumps(
                {
                    "profile": str(store.root),
                    "workspace_id": workspace.id,
                    "request": request.model_dump(),
                }
            )
        )
        return configuration, workspace.model_dump_json(), [s.sha256 for s in samples]
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=1_000_000)
    parser.add_argument("--dimensions", type=int, nargs="+", default=[16, 64])
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/benchmark-population-comparison.json"
    )
    parser.add_argument("--worker", choices=["dense", "columns", "full"])
    parser.add_argument("--configuration", type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker, args.configuration)
        return
    if args.events < 1024 or any(not 2 <= d <= 64 for d in args.dimensions):
        parser.error("Use at least 1024 events and 2–64 coordinates")
    output = args.output.resolve()
    if not output.is_relative_to(ROOT):
        parser.error("The evidence output must stay inside this checkout")
    temporary = ROOT / ".tmp"
    temporary.mkdir(exist_ok=True)
    required = args.events * max(args.dimensions) * 8 * 3 + 256 * 1024**2
    if shutil.disk_usage(ROOT).free < required:
        parser.error(f"This synthetic benchmark requires approximately {required} available bytes")
    cases = []
    with tempfile.TemporaryDirectory(prefix="comparison-benchmark-", dir=temporary) as owned:
        for dimensions in args.dimensions:
            directory = Path(owned) / str(dimensions)
            directory.mkdir()
            configuration, before, original_hashes = create_case(directory, args.events, dimensions)
            measured = {}
            for method in ["dense", "columns", "full"]:
                subprocess.run(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--worker",
                        method,
                        "--configuration",
                        str(configuration),
                    ],
                    cwd=ROOT,
                    check=True,
                    timeout=1800,
                )
                measured[method] = json.loads((directory / f"{method}-result.json").read_text())
                print(
                    json.dumps(
                        {
                            "dimensions": dimensions,
                            "method": method,
                            "seconds": measured[method]["elapsed_seconds"],
                            "peak_rss_bytes": measured[method]["memory"]["peak_rss_bytes"],
                        }
                    ),
                    flush=True,
                )
            for field in ["control_tree_sha256", "all_joint_counts_sha256", "joint_outcomes"]:
                assert measured["dense"][field] == measured["columns"][field], field
            for field in ["control_tree_sha256", "joint_outcomes"]:
                assert measured["dense"][field] == measured["full"][field], field
            store = Store(directory / "profile")
            try:
                workspace = store.get(json.loads(configuration.read_text())["workspace_id"])
                assert workspace.model_dump_json() == before
                actual_hashes = [
                    digest(store.data_path(workspace.id, s.id)) for s in workspace.samples
                ]
                assert actual_hashes == original_hashes
            finally:
                store.close()
            assert measured["columns"]["temporary_acquisition_files"] == 3
            cases.append(
                {
                    "events": args.events,
                    "dimensions": dimensions,
                    "acquisitions": 3,
                    "target_populations": 2,
                    "control_populations": 3,
                    "source_sha256": original_hashes,
                    "exact_tree_and_joint_statistics_equal": True,
                    "original_sources_and_workspace_unchanged": True,
                    "measurements": measured,
                }
            )
            shutil.rmtree(directory)
    evidence = {
        "status": "passed",
        "platform": platform.platform(),
        "scope": (
            "Synthetic all-event Python joint storage and full comparison calculation; "
            "excludes IPC, network, native rendering and vendor equivalence"
        ),
        "dense_baseline": (
            "Complete dense matrices with the current coordinate-at-a-time partition algorithm; "
            "excludes former recursive full-matrix subset copies"
        ),
        "rss_scope": (
            "Fresh worker process peak RSS including imports, source loading and "
            "temporary staging; Linux/macOS only"
        ),
        "implicit_sampling": False,
        "generated_data_removed": True,
        "source_sha256": {
            name: digest(ROOT / name)
            for name in [
                "backend/cytoforge/comparison_storage.py",
                "backend/cytoforge/population_statistics.py",
                "backend/cytoforge/population_comparison.py",
                "tools/benchmark_population_comparison.py",
            ]
        },
        "cases": cases,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2) + "\n")
    print(f"Passed: {output.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
