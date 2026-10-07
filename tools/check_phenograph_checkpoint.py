"""Bind PhenoGraph and raw analysis to complete executed desktop checks."""

import json
from collections import Counter
from datetime import UTC, datetime

from check_spectral_autospill_checkpoint import ROOT, digest, read, suite


def regression():
    full = suite("pytest-phenograph-full.xml")
    collected = [
        line
        for line in (ROOT / "artifacts/pytest-phenograph-collection.log").read_text().splitlines()
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
    assert len(executed) == len(collected) == int(full.get("tests"))
    classes = Counter(case.get("classname") for case in full.findall("testcase"))
    for module, count in {
        "tests.test_autospill_parents": 22,
        "tests.test_phenograph": 23,
        "tests.test_analysis": 17,
        "tests.test_multiaf": 21,
        "tests.test_multiaf_reference": 8,
        "tests.test_autospill": 32,
        "tests.test_spectral_autospill": 24,
        "tests.test_compensation": 8,
        "tests.test_autospread": 31,
    }.items():
        assert classes[module] == count, (module, classes[module])
    started = read("phenograph-full-regression-start.json")
    for path, sha256 in started["source_sha256"].items():
        assert digest(ROOT / path) == sha256, path
        assert (ROOT / path).stat().st_mtime_ns <= started["started_ns"], path
    scope = ROOT / "artifacts/phenograph-full-regression-scope.json"
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
        xml="artifacts/pytest-phenograph-full.xml",
        log="artifacts/pytest-phenograph-full.log",
        scope=str(scope.relative_to(ROOT)),
        transport="HTTPX in-process ASGI with lifespan; no TCP listener",
    )


def main():
    previous = read("multiaf-desktop-candidate-validation.json")
    assert len(previous["retained_full_goal_unverified"]) == 20
    full = regression()
    focused = suite("phenograph-compatibility-focused.xml")
    assert Counter(c.get("classname") for c in focused.findall("testcase")) == {
        "tests.test_phenograph": 23,
        "tests.test_analysis": 17,
        "tests.test_kinetics": 32,
        "tests.test_population_comparison": 11,
        "tests.test_proliferation": 22,
        "tests.test_quality": 19,
    }
    names = [
        "frozen-phenograph-validation.json",
        "frozen-phenograph-multiaf-regression.json",
        "frozen-phenograph-spectral-regression.json",
        "frozen-phenograph-spreading-regression.json",
        "frozen-phenograph-packed-regression.json",
        "frozen-phenograph-graph-regression.json",
        "frozen-phenograph-parents-regression.json",
    ]
    proofs = [read(name) for name in names]
    frozen = proofs[0]
    assert frozen["source_import_paths_removed"] and frozen["actual_frozen_analysis_children"]
    assert len(frozen["runtime"]["cases"]) == 4 and len(frozen["runtime"]["children"]) == 8
    assert all(case["status"] == "passed" for case in frozen["runtime"]["cases"])
    engine = ROOT / frozen["engine"]
    assert digest(engine) == frozen["engine_sha256"]
    for proof in proofs:
        assert proof["status"] == "passed" and proof["engine_sha256"] == frozen["engine_sha256"]
        for path, sha256 in proof["source_sha256"].items():
            assert digest(ROOT / path) == sha256, path
    assert len(proofs[1]["runtime"]["cases"]) == 9
    assert len(proofs[1]["runtime"]["children"]) == 23
    assert len(proofs[2]["runtime"]["cases"]) == 8
    assert len(proofs[3]["runtime"]["cases"]) == 8
    assert len(proofs[4]["runtime"]["cases"]) == 8
    assert len(proofs[5]["cases"]) == 4
    assert len(proofs[6]["runtime"]["cases"]) == 6
    assert len(proofs[6]["runtime"]["children"]) == 13
    assert proofs[6]["runtime"]["deliberately_corrupted_flags_restored"]
    reference_path = ROOT / "tests/fixtures/spectral_autospill/reference.json"
    reference = json.loads(reference_path.read_text())
    assert len(reference["cases"]) == 7
    assert not reference["biological_validation"] and not reference["flowjo_validation"]
    assert (
        digest(ROOT / "tools/validate_spectral_autospill_reference.py")
        == reference["driver_sha256"]
    )
    for key, path in [
        ("controls_sha256", "tests/fixtures/spectral_autospill/controls.npz"),
        ("truth_sha256", "tests/fixtures/spectral_autospill/truth.json"),
        ("paper_sha256", ".cache/references/autospill/paper.xml"),
        ("author_regression_sha256", ".cache/references/autospill/R/fit_robust_linear_model.r"),
    ]:
        assert digest(ROOT / path) == reference[key], path
    benchmark = read("multiaf-benchmark.json")
    assert benchmark["status"] == "passed" and len(benchmark["records"]) == 2
    assert [r["events_per_control"] for r in benchmark["records"]] == [131072, 1048576]
    assert all(
        r["controls"] == r["sources"] == 4
        and r["measured_detectors"] == 8
        and r["autofluorescence_sources"] == 2
        and r["raw_data_unchanged"]
        and r["workspace_unchanged"]
        and r["all_selected_events_used"]
        for r in benchmark["records"]
    )
    multiaf_reference = json.loads((ROOT / "tests/fixtures/multiaf/reference.json").read_text())
    assert len(multiaf_reference["cases"]) == 7
    for key, path in [
        ("controls_sha256", "tests/fixtures/multiaf/controls.npz"),
        ("truth_sha256", "tests/fixtures/multiaf/truth.json"),
        ("generator_sha256", "tools/validate_multiaf_reference.py"),
        ("original_huber_sha256", ".cache/references/autospill/R/fit_robust_linear_model.r"),
        ("retained_driver_sha256", "tools/validate_spectral_autospill_reference.py"),
    ]:
        assert digest(ROOT / path) == multiaf_reference[key], path
    for path, sha256 in benchmark["source_sha256"].items():
        assert digest(ROOT / path) == sha256, path
    fonts = read("graph-font-assets.json")
    assert len(fonts["sha256"]) == 12
    for name, sha256 in fonts["sha256"].items():
        assert digest(ROOT / "frontend/src/assets/fonts" / name) == sha256
        assert digest(engine.parent / "_internal/matplotlib/mpl-data/fonts/ttf" / name) == sha256
    original = read("phenograph-original-workspace-final.json")
    assert original["unchanged"]
    assert all(row["matches_acquisition"] for row in original["acquired_event_files_checked"])
    logs = {
        "typecheck": ("artifacts/typecheck-phenograph.log", "tsc -b"),
        "ui_build": ("artifacts/build-phenograph-ui.log", "built in"),
        "ruff": ("artifacts/ruff-phenograph.log", "All checks passed!"),
        "prettier": (
            "artifacts/prettier-phenograph.log",
            "All matched files use Prettier code style!",
        ),
    }
    for path, marker in logs.values():
        assert marker in (ROOT / path).read_text(), path
    for name, count in [
        ("test-phenograph-spreading-client.log", 14),
        ("test-phenograph-af-client.log", 5),
        ("test-phenograph-spectral-client.log", 9),
    ]:
        log = (ROOT / "artifacts" / name).read_text()
        assert f"# pass {count}" in log and "# fail 0" in log and "# skipped 0" in log
    graph_benchmark = read("phenograph-benchmark.json")
    assert graph_benchmark["status"] == "passed"
    assert [r["fitted_events"] for r in graph_benchmark["records"]] == [20000, 100000]
    assert all(
        r["features"] == 8
        and r["neighbors"] == 30
        and r["louvain_restarts"] == 5
        and r["all_fitted_identities_labeled"]
        and r["no_community_spans_known_separated_blobs"]
        for r in graph_benchmark["records"]
    )
    for path, sha256 in graph_benchmark["source_sha256"].items():
        assert digest(ROOT / path) == sha256, path
    sources = set(previous["current_source_sha256"])
    sources.update({"pyproject.toml", "uv.lock"})
    sources.update(
        f"tests/fixtures/multiaf/{name}"
        for name in ("controls.npz", "truth.json", "reference.json")
    )
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
            "PhenoGraph, raw acquired populations and frozen analysis workers; "
            "native interaction and biology unverified"
        ),
        full_python_regression=full,
        focused_regression=dict(
            status="passed",
            tests=124,
            phenograph_tests=23,
            legacy_analysis_tests=17,
            fingerprint_compatibility_tests=84,
            xml="artifacts/phenograph-compatibility-focused.xml",
        ),
        compiled_client_checks=dict(status="passed", spectral=9, spreading=14, autofluorescence=5),
        scientific_sources_unchanged_since_regression_started=True,
        frozen_engine=frozen,
        frozen_multiaf_regression=proofs[1],
        frozen_spectral_regression=proofs[2],
        frozen_spreading_regression=proofs[3],
        frozen_packed_import_regression=proofs[4],
        frozen_graph_regression=proofs[5],
        frozen_acquired_parent_regression=proofs[6],
        phenograph_benchmark=graph_benchmark,
        multiple_af_benchmark=benchmark,
        multiple_af_independent_reference="artifacts/multiaf-reference-validation.json",
        independent_reference="artifacts/spectral-autospill-reference-validation.json",
        original_workspace=original,
        bundled_native_and_report_fonts=fonts,
        desktop_interaction_executed=False,
        full_objective_complete=False,
        retained_full_goal_unverified=previous["retained_full_goal_unverified"],
        current_pending=[
            "Native interactive review of PhenoGraph, raw parents and independent plot windows",
            "Automatic AF extraction/classification and external biological/FlowJo truth",
            "All 20 retained broad requirements remain incomplete or unverified",
        ],
        previous_goal_turn_classification="verified_wait",
        previous_turn_revalidated=(
            "Launch guidance checked and confirmed live full-regression session 3516 polled"
        ),
        next_safe_action_completed=(
            "PhenoGraph clustering, fitted event identities and retained raw populations; "
            "shared fingerprint compatibility restored and all scientific regressions rerun"
        ),
        current_source_sha256={path: digest(ROOT / path) for path in sorted(sources)},
        **{key: dict(status="passed", log=value[0]) for key, value in logs.items()},
    )
    (ROOT / "artifacts/phenograph-source-validation.json").write_text(
        json.dumps(record, indent=2) + "\n"
    )
    print(
        f"Current source checkpoint: {full['tests']} Python tests, 28 client checks; "
        "all 20 requirements retained"
    )


if __name__ == "__main__":
    main()
