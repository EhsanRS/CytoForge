"""Bind the acquired-parent desktop candidate to current executed checks."""

import json
from collections import Counter
from datetime import UTC, datetime

from check_spectral_autospill_checkpoint import ROOT, digest, read, suite


def regression():
    full = suite("pytest-autospill-parents-full.xml")
    collected = [
        line
        for line in (ROOT / "artifacts/pytest-autospill-parents-collection.log")
        .read_text()
        .splitlines()
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
        "tests.test_autospill": 32,
        "tests.test_spectral_autospill": 24,
        "tests.test_compensation": 8,
        "tests.test_autospread": 31,
    }.items():
        assert classes[module] == count, (module, classes[module])
    started = read("autospill-parents-full-regression-start.json")
    for path, sha256 in started["source_sha256"].items():
        assert digest(ROOT / path) == sha256, path
        assert (ROOT / path).stat().st_mtime_ns <= started["started_ns"], path
    scope = ROOT / "artifacts/autospill-parents-full-regression-scope.json"
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
        xml="artifacts/pytest-autospill-parents-full.xml",
        log="artifacts/pytest-autospill-parents-full.log",
        scope=str(scope.relative_to(ROOT)),
        transport="HTTPX in-process ASGI with lifespan; no TCP listener",
    )


def main():
    previous = read("spectral-autospill-desktop-candidate-validation.json")
    assert len(previous["retained_full_goal_unverified"]) == 20
    full = regression()
    focused = suite("autospill-parents-focused.xml")
    assert Counter(c.get("classname") for c in focused.findall("testcase")) == {
        "tests.test_autospill_parents": 22
    }
    names = [
        "frozen-autospill-parents-validation.json",
        "frozen-autospill-parents-spectral-regression.json",
        "frozen-autospill-parents-spreading-regression.json",
        "frozen-autospill-parents-packed-regression.json",
        "frozen-autospill-parents-graph-regression.json",
    ]
    proofs = [read(name) for name in names]
    frozen = proofs[0]
    assert frozen["source_import_paths_removed"] and frozen["actual_frozen_analysis_children"]
    assert len(frozen["runtime"]["cases"]) == 6 and len(frozen["runtime"]["children"]) == 13
    assert all(case["status"] == "passed" for case in frozen["runtime"]["cases"])
    assert frozen["runtime"]["deliberately_corrupted_flags_restored"]
    engine = ROOT / frozen["engine"]
    assert digest(engine) == frozen["engine_sha256"]
    for proof in proofs:
        assert proof["status"] == "passed" and proof["engine_sha256"] == frozen["engine_sha256"]
        for path, sha256 in proof["source_sha256"].items():
            assert digest(ROOT / path) == sha256, path
    assert len(proofs[1]["runtime"]["cases"]) == 8
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
    benchmark = read("autospill-parents-benchmark.json")
    assert benchmark["status"] == "passed" and len(benchmark["records"]) == 2
    assert [r["events_per_control"] for r in benchmark["records"]] == [131072, 1048576]
    assert all(
        r["acquired_ratio_and_reviewed_qc_parent"]
        and r["qc_flags_unchanged"]
        and r["raw_data_unchanged"]
        and r["workspace_unchanged"]
        for r in benchmark["records"]
    )
    for path, sha256 in benchmark["source_sha256"].items():
        assert digest(ROOT / path) == sha256, path
    fonts = read("graph-font-assets.json")
    assert len(fonts["sha256"]) == 12
    for name, sha256 in fonts["sha256"].items():
        assert digest(ROOT / "frontend/src/assets/fonts" / name) == sha256
        assert digest(engine.parent / "_internal/matplotlib/mpl-data/fonts/ttf" / name) == sha256
    original = read("autospill-parents-original-workspace.json")
    assert original["unchanged"]
    assert all(row["matches_acquisition"] for row in original["acquired_event_files_checked"])
    logs = {
        "typecheck": ("artifacts/typecheck-autospill-parents.log", "tsc -b"),
        "ui_build": ("artifacts/build-autospill-parents-ui.log", "built in"),
        "ruff": ("artifacts/ruff-autospill-parents.log", "All checks passed!"),
        "prettier": (
            "artifacts/prettier-autospill-parents.log",
            "All matched files use Prettier code style!",
        ),
    }
    for path, marker in logs.values():
        assert marker in (ROOT / path).read_text(), path
    for name, count in [
        ("test-autospill-parents-spreading-client.log", 12),
        ("test-autospill-parents-spectral-client.log", 9),
    ]:
        log = (ROOT / "artifacts" / name).read_text()
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
            "Current acquired-parent code and frozen workers; "
            "native interaction and biology unverified"
        ),
        full_python_regression=full,
        focused_regression=dict(status="passed", parent_tests=22),
        compiled_client_checks=dict(status="passed", spectral=9, spreading=12),
        scientific_sources_unchanged_since_regression_started=True,
        frozen_engine=frozen,
        frozen_spectral_regression=proofs[1],
        frozen_spreading_regression=proofs[2],
        frozen_packed_import_regression=proofs[3],
        frozen_graph_regression=proofs[4],
        control_parent_benchmark=benchmark,
        independent_reference="artifacts/spectral-autospill-reference-validation.json",
        original_workspace=original,
        bundled_native_and_report_fonts=fonts,
        desktop_interaction_executed=False,
        full_objective_complete=False,
        retained_full_goal_unverified=previous["retained_full_goal_unverified"],
        current_pending=[
            "Native interactive review of ratio/QC parents and their saved copies",
            "Several autofluorescence components and external biological/FlowJo truth",
            "All 20 retained broad requirements remain incomplete or unverified",
        ],
        previous_goal_turn_classification="no_progress",
        previous_turn_revalidated=(
            "Launch-command guidance; unfinished acquired-parent work revalidated"
        ),
        next_safe_action_completed=(
            "Acquired ratio/QC parents, immutable saved selections and spreading reuse"
        ),
        current_source_sha256={path: digest(ROOT / path) for path in sorted(sources)},
        **{key: dict(status="passed", log=value[0]) for key, value in logs.items()},
    )
    (ROOT / "artifacts/autospill-parents-source-validation.json").write_text(
        json.dumps(record, indent=2) + "\n"
    )
    print(
        f"Current source checkpoint: {full['tests']} Python tests, 21 client checks; "
        "all 20 requirements retained"
    )


if __name__ == "__main__":
    main()
