"""Tie the spectral desktop candidate to current source and executed validation."""

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def read(name):
    return json.loads((ROOT / "artifacts" / name).read_text())


def suite(name):
    result = ET.parse(ROOT / "artifacts" / name).getroot().find("testsuite")
    assert result is not None
    assert all(int(result.get(key)) == 0 for key in ("failures", "errors", "skipped"))
    return result


def regression():
    full = suite("pytest-spectral-autospill-full.xml")
    collected = [
        line
        for line in (ROOT / "artifacts/pytest-spectral-autospill-collection.log")
        .read_text()
        .splitlines()
        if line.startswith("tests/") and "::" in line
    ]
    modules = {
        str(path.relative_to(ROOT)).removesuffix(".py").replace("/", "."): str(
            path.relative_to(ROOT)
        )
        for path in (ROOT / "tests").rglob("test_*.py")
    }
    executed = []
    for case in full.findall("testcase"):
        classname = case.get("classname")
        module = max(
            (name for name in modules if classname == name or classname.startswith(name + ".")),
            key=len,
        )
        suffix = classname[len(module) :].strip(".").replace(".", "::")
        executed.append(
            modules[module] + "::" + (suffix + "::" if suffix else "") + case.get("name")
        )
    assert len(executed) == len(collected) == int(full.get("tests"))
    assert Counter(executed) == Counter(collected)
    started = datetime.fromisoformat(full.get("timestamp")).replace(tzinfo=UTC).timestamp()
    assert all(
        path.stat().st_mtime <= started
        for path in [*(ROOT / "backend/cytoforge").rglob("*.py"), *(ROOT / "tests").rglob("*.py")]
    )
    scope = ROOT / "artifacts/spectral-autospill-full-regression-scope.json"
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
        xml="artifacts/pytest-spectral-autospill-full.xml",
        log="artifacts/pytest-spectral-autospill-full.log",
        scope=str(scope.relative_to(ROOT)),
        transport="HTTPX in-process ASGI with lifespan; no TCP listener",
    )


def main():
    previous = read("autospread-desktop-candidate-validation.json")
    full = regression()
    focused = suite("spectral-autospill-focused.xml")
    assert Counter(case.get("classname") for case in focused.findall("testcase")) == {
        "tests.test_spectral_autospill": 24
    }
    legacy = suite("spectral-autospill-legacy.xml")
    assert Counter(case.get("classname") for case in legacy.findall("testcase")) == {
        "tests.test_autospill": 32,
        "tests.test_compensation": 8,
    }
    frozen = read("frozen-spectral-autospill-validation.json")
    assert frozen["status"] == "passed" and frozen["source_import_paths_removed"]
    assert frozen["actual_frozen_analysis_children"]
    assert len(frozen["runtime"]["cases"]) == 8
    assert sum(case["status"] == "passed" for case in frozen["runtime"]["cases"]) == 7
    assert frozen["runtime"]["reference_files_unchanged"]
    engine = ROOT / frozen["engine"]
    assert digest(engine) == frozen["engine_sha256"]
    names = [
        "frozen-spectral-autospill-validation.json",
        "frozen-spectral-autospill-spreading-regression.json",
        "frozen-spectral-autospill-packed-regression.json",
        "frozen-spectral-autospill-graph-regression.json",
        "spectral-autospill-benchmark.json",
    ]
    for name in names:
        proof = read(name)
        assert proof["status"] == "passed"
        if "engine_sha256" in proof:
            assert proof["engine_sha256"] == frozen["engine_sha256"]
        for path, value in proof["source_sha256"].items():
            assert digest(ROOT / path) == value, path
    reference_path = ROOT / "tests/fixtures/spectral_autospill/reference.json"
    reference = json.loads(reference_path.read_text())
    assert len(reference["cases"]) == 7 and reference["biological_validation"] is False
    assert reference["flowjo_validation"] is False
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
    original_reference = json.loads((ROOT / "tests/fixtures/autospill/truth.json").read_text())
    assert original_reference["commit"] == reference["reference_commit"]
    assert (
        original_reference["source_sha256"]["fit_robust_linear_model.r"]
        == reference["author_regression_sha256"]
    )
    fonts = read("graph-font-assets.json")
    for name, value in fonts["sha256"].items():
        assert digest(ROOT / "frontend/src/assets/fonts" / name) == value
        assert digest(engine.parent / "_internal/matplotlib/mpl-data/fonts/ttf" / name) == value
    original = read("spectral-autospill-original-workspace.json")
    assert original["unchanged"]
    assert all(row["matches_acquisition"] for row in original["acquired_event_files_checked"])
    logs = {
        "typecheck": ("artifacts/typecheck-spectral-autospill.log", "tsc -b"),
        "ui_build": ("artifacts/build-spectral-autospill-ui.log", "built in"),
        "ruff": ("artifacts/ruff-spectral-autospill.log", "All checks passed!"),
        "prettier": (
            "artifacts/prettier-spectral-autospill.log",
            "All matched files use Prettier code style!",
        ),
    }
    for path, marker in logs.values():
        assert marker in (ROOT / path).read_text()
    for path in [
        "artifacts/test-spectral-autospill-client.log",
        "artifacts/test-spectral-autospill-spreading-client.log",
    ]:
        log = (ROOT / path).read_text()
        assert "# pass 9" in log and "# fail 0" in log and "# skipped 0" in log
    sources = set(previous["current_source_sha256"])
    for folder in ("backend/cytoforge", "frontend/src", "desktop", "tests"):
        sources.update(
            str(path.relative_to(ROOT))
            for path in (ROOT / folder).rglob("*")
            if path.is_file()
            and path.suffix in {".py", ".ts", ".tsx", ".css", ".mjs", ".cjs", ".ttf"}
        )
    sources.update(
        [
            "docs/AUTOSPILL.md",
            "frontend/src/spreading.ts",
            "tools/test_spreading.mjs",
            "tools/validate_spectral_autospill_reference.py",
            "tools/validate_frozen_spectral_autospill.py",
            "tools/benchmark_spectral_autospill.py",
            "tools/test_spectral_autospill.sh",
            "tools/test_spectral_autospill.mjs",
            "tools/check_spectral_autospill_checkpoint.py",
            "tools/deduplicate_candidate_engine.py",
            "tools/compact_desktop_candidate.py",
            "tools/finalize_spectral_autospill_candidate.py",
            "tests/fixtures/spectral_autospill/README.md",
            "tests/fixtures/spectral_autospill/controls.npz",
            "tests/fixtures/spectral_autospill/truth.json",
            "tests/fixtures/spectral_autospill/reference.json",
        ]
    )
    record = dict(
        status="passed",
        checked_at=datetime.now(UTC).isoformat(),
        scope=(
            "Current sources and actual frozen workers; native interaction, "
            "other OS and biological accuracy remain unverified"
        ),
        full_python_regression=full,
        focused_regression=dict(status="passed", spectral_tests=24, legacy_tests=40),
        compiled_client_checks=dict(status="passed", spectral=9, spreading=9),
        scientific_sources_unchanged_since_regression_started=True,
        frozen_engine=frozen,
        frozen_spreading_regression=read(names[1]),
        frozen_packed_import_regression=read(names[2]),
        frozen_graph_regression=read(names[3]),
        spectral_benchmark=read(names[4]),
        independent_reference="artifacts/spectral-autospill-reference-validation.json",
        original_workspace=original,
        bundled_native_and_report_fonts=fonts,
        desktop_interaction_executed=False,
        full_objective_complete=False,
        retained_full_goal_unverified=previous["retained_full_goal_unverified"],
        current_pending=[
            "Native interactive review of the spectral workflow",
            "External spectral/biological/FlowJo comparisons",
            "All retained full-goal requirements beyond the verified numerical and packaging scope",
        ],
        previous_goal_turn_classification="progress",
        next_safe_action_completed="Rectangular spectral AutoSpill and independent verification",
        current_source_sha256={path: digest(ROOT / path) for path in sorted(sources)},
        **{key: dict(status="passed", log=value[0]) for key, value in logs.items()},
    )
    assert len(record["retained_full_goal_unverified"]) == 20
    (ROOT / "artifacts/spectral-autospill-source-validation.json").write_text(
        json.dumps(record, indent=2) + "\n"
    )
    print(
        f"Current source checkpoint: {full['tests']} Python tests, 18 client checks, "
        "all 20 full-goal requirements retained"
    )


if __name__ == "__main__":
    main()
