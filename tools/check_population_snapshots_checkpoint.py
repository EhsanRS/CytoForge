"""Bind captured acquired-event populations to complete desktop build checks."""

import json
from collections import Counter
from datetime import UTC, datetime

from check_spectral_autospill_checkpoint import ROOT, digest, read, suite

PREFIX = "population-snapshots"


def regression():
    full = suite(f"pytest-{PREFIX}-full.xml")
    collected = [
        line
        for line in (ROOT / f"artifacts/pytest-{PREFIX}-collection.log").read_text().splitlines()
        if line.startswith("tests/") and "::" in line
    ]
    modules = {
        str(p.relative_to(ROOT)).removesuffix(".py").replace("/", "."): str(p.relative_to(ROOT))
        for p in (ROOT / "tests").rglob("test_*.py")
    }
    executed = []
    for case in full.findall("testcase"):
        classname = case.get("classname")
        module = max(
            (m for m in modules if classname == m or classname.startswith(m + ".")), key=len
        )
        suffix = classname[len(module) :].strip(".").replace(".", "::")
        executed.append(
            modules[module] + "::" + (suffix + "::" if suffix else "") + case.get("name")
        )
    assert Counter(executed) == Counter(collected)
    assert len(executed) == len(collected) == int(full.get("tests")) == 1561
    assert (
        Counter(c.get("classname") for c in full.findall("testcase"))[
            "tests.test_population_snapshots"
        ]
        == 23
    )
    started = read(f"{PREFIX}-full-regression-start.json")
    assert len(started["source_sha256"]) == 133
    for path, sha256 in started["source_sha256"].items():
        assert digest(ROOT / path) == sha256, path
        assert (ROOT / path).stat().st_mtime_ns <= started["started_ns"], path
    scope = ROOT / f"artifacts/{PREFIX}-full-regression-scope.json"
    scope.write_text(
        json.dumps(
            dict(status="passed", collected_nodeids=collected, executed_nodeids=executed), indent=2
        )
        + "\n"
    )
    return dict(
        status="passed",
        tests=len(executed),
        failures=0,
        errors=0,
        skipped=0,
        seconds=float(full.get("time")),
        xml=f"artifacts/pytest-{PREFIX}-full.xml",
        log=f"artifacts/pytest-{PREFIX}-full.log",
        scope=str(scope.relative_to(ROOT)),
        transport="HTTPX in-process ASGI with lifespan; no TCP listener",
    )


def current_sources(proof):
    assert proof["status"] == "passed"
    for path, sha256 in proof["source_sha256"].items():
        assert digest(ROOT / path) == sha256, path


def main():
    previous = read("phenograph-desktop-candidate-validation.json")
    assert len(previous["retained_full_goal_unverified"]) == 20
    full = regression()
    focused = suite(f"{PREFIX}-stable-focused.xml")
    assert int(focused.get("tests")) == 23
    assert Counter(c.get("classname") for c in focused.findall("testcase")) == {
        "tests.test_population_snapshots": 23
    }
    frozen = read(f"frozen-{PREFIX}-validation.json")
    current_sources(frozen)
    assert frozen["source_import_paths_removed"] and frozen["actual_frozen_analysis_children"]
    cases = frozen["runtime"]["cases"]
    assert len(cases) == 5 and len(frozen["runtime"]["children"]) == 8
    assert all(c["status"] == "passed" for c in cases)
    assert all(c["captured_population_verified"] for c in cases[:4])
    assert all(
        cases[4][key]
        for key in (
            "median_calculation_verified",
            "assignment_memberships_verified",
            "altered_membership_rejected",
            "corrupted_owned_flags_restored",
        )
    )
    engine = ROOT / frozen["engine"]
    assert digest(engine) == frozen["engine_sha256"]
    proofs = {}
    for name, count, children in [
        ("phenograph", 4, 8),
        ("multiaf", 9, 23),
        ("parents", 6, 13),
        ("spectral", 8, None),
        ("spreading", 8, None),
        ("packed", 8, None),
        ("graph", 4, None),
    ]:
        proof = read(f"frozen-{PREFIX}-{name}-regression.json")
        current_sources(proof)
        assert proof["engine_sha256"] == frozen["engine_sha256"]
        runtime = proof.get("runtime", proof)
        assert len(runtime["cases"]) == count
        if name == "graph":
            assert all(
                c["original_workspace_and_event_bytes_unchanged"] and c["temporary_storage_removed"]
                for c in runtime["cases"]
            )
            assert all(c["exact_joint_score_matches_dense_reference"] for c in runtime["cases"][:2])
            assert all(c["no_result_or_partial_artifact_published"] for c in runtime["cases"][2:])
        elif name == "packed":
            assert Counter(c["status"] for c in runtime["cases"]) == {
                "passed": 4,
                "rejected_atomically": 4,
            }
            assert all(
                c["generated_arrays_removed"]
                for c in runtime["cases"]
                if c["status"] == "rejected_atomically"
            )
        elif name in {"spectral", "spreading"}:
            assert Counter(c["status"] for c in runtime["cases"]) == {
                "passed": 7,
                "atomic_rejection_verified": 1,
            }
        else:
            assert all(c["status"] == "passed" for c in runtime["cases"])
        if children is not None:
            assert len(runtime["children"]) == children
        proofs[name] = proof
    assert proofs["parents"]["runtime"]["deliberately_corrupted_flags_restored"]
    benchmark = read(f"{PREFIX}-benchmark.json")
    current_sources(benchmark)
    assert len(benchmark["records"]) == 1
    measurement = benchmark["records"][0]
    assert measurement["acquired_events"] == 1048576
    assert measurement["selected_events"] == 81920
    assert measurement["exact_event_identities_verified"]
    assert measurement["packed_file_bytes"] == 131200
    multiple_af = read(f"{PREFIX}-multiaf-benchmark.json")
    current_sources(multiple_af)
    assert [r["events_per_control"] for r in multiple_af["records"]] == [131072, 1048576]
    assert all(
        r["controls"] == r["sources"] == 4
        and r["measured_detectors"] == 8
        and r["autofluorescence_sources"] == 2
        and r["raw_data_unchanged"]
        and r["workspace_unchanged"]
        and r["all_selected_events_used"]
        for r in multiple_af["records"]
    )
    graph_benchmark = read("phenograph-benchmark.json")
    current_sources(graph_benchmark)
    assert [r["fitted_events"] for r in graph_benchmark["records"]] == [20000, 100000]
    references = {
        "spectral_autospill": {
            "controls_sha256": "tests/fixtures/spectral_autospill/controls.npz",
            "truth_sha256": "tests/fixtures/spectral_autospill/truth.json",
            "driver_sha256": "tools/validate_spectral_autospill_reference.py",
            "paper_sha256": ".cache/references/autospill/paper.xml",
            "author_regression_sha256": ".cache/references/autospill/R/fit_robust_linear_model.r",
        },
        "multiaf": {
            "controls_sha256": "tests/fixtures/multiaf/controls.npz",
            "truth_sha256": "tests/fixtures/multiaf/truth.json",
            "generator_sha256": "tools/validate_multiaf_reference.py",
            "original_huber_sha256": ".cache/references/autospill/R/fit_robust_linear_model.r",
            "retained_driver_sha256": "tools/validate_spectral_autospill_reference.py",
        },
    }
    for folder, pins in references.items():
        reference = json.loads((ROOT / f"tests/fixtures/{folder}/reference.json").read_text())
        assert len(reference["cases"]) == 7
        for key, path in pins.items():
            assert digest(ROOT / path) == reference[key], path
    fonts = read("graph-font-assets.json")
    assert len(fonts["sha256"]) == 12
    for name, sha256 in fonts["sha256"].items():
        assert digest(ROOT / "frontend/src/assets/fonts" / name) == sha256
        assert digest(engine.parent / "_internal/matplotlib/mpl-data/fonts/ttf" / name) == sha256
    original = read(f"{PREFIX}-original-workspace-final.json")
    assert original["unchanged"]
    assert all(row["matches_acquisition"] for row in original["acquired_event_files_checked"])
    logs = {
        "typecheck": (f"artifacts/typecheck-{PREFIX}.log", "tsc -b"),
        "ui_build": (f"artifacts/build-{PREFIX}-ui.log", "built in"),
        "ruff": (f"artifacts/ruff-{PREFIX}.log", "All checks passed!"),
        "prettier": (
            f"artifacts/prettier-{PREFIX}.log",
            "All matched files use Prettier code style!",
        ),
    }
    for path, marker in logs.values():
        assert marker in (ROOT / path).read_text(), path
    for name, count in [("spreading", 14), ("af", 5), ("spectral", 9)]:
        log = (ROOT / f"artifacts/test-{PREFIX}-{name}-client.log").read_text()
        assert f"# pass {count}" in log and "# fail 0" in log and "# skipped 0" in log
    sources = set(previous["current_source_sha256"])
    for folder in ("backend/cytoforge", "frontend/src", "desktop", "tests", "tools", "docs"):
        sources.update(
            str(p.relative_to(ROOT))
            for p in (ROOT / folder).rglob("*")
            if p.is_file()
            and p.suffix
            in {".py", ".ts", ".tsx", ".css", ".mjs", ".cjs", ".sh", ".md", ".ttf", ".png", ".svg"}
        )
    record = dict(
        status="passed",
        checked_at=datetime.now(UTC).isoformat(),
        scope=(
            "Captured acquired-event populations and packaged workers; "
            "native GUI and biological truth unverified"
        ),
        full_python_regression=full,
        focused_regression=dict(
            status="passed", tests=23, xml=f"artifacts/{PREFIX}-stable-focused.xml"
        ),
        compiled_client_checks=dict(status="passed", spectral=9, spreading=14, autofluorescence=5),
        scientific_sources_unchanged_since_regression_started=True,
        frozen_engine=frozen,
        frozen_workflow_regressions=proofs,
        population_snapshot_benchmark=benchmark,
        multiple_af_benchmark=multiple_af,
        phenograph_benchmark=graph_benchmark,
        original_workspace=original,
        bundled_native_and_report_fonts=fonts,
        desktop_interaction_executed=False,
        full_objective_complete=False,
        retained_full_goal_unverified=previous["retained_full_goal_unverified"],
        current_pending=[
            "Native interactive review of captured populations, controls and separate plot windows",
            "Distinct AF spectrum ranking, automatic AF extraction/classification and Opt-SNE",
            "External biological/instrument/FlowJo truth and all 20 retained broad requirements",
        ],
        previous_goal_turn_classification="progress",
        previous_turn_revalidated=(
            "PhenoGraph installer, source fingerprints and preserved original workspace checked"
        ),
        next_safe_action_completed=(
            "Captured immutable acquired-event memberships; "
            "reuse of clustered populations as AF controls"
        ),
        current_source_sha256={path: digest(ROOT / path) for path in sorted(sources)},
        **{key: dict(status="passed", log=value[0]) for key, value in logs.items()},
    )
    (ROOT / f"artifacts/{PREFIX}-source-validation.json").write_text(
        json.dumps(record, indent=2) + "\n"
    )
    print(
        f"Current source checkpoint: {full['tests']} Python tests, 28 client checks; "
        "all 20 requirements retained"
    )


if __name__ == "__main__":
    main()
