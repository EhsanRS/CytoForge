"""Tie the spreading candidate to complete executed checks and current sources."""

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
    assert all(int(result.get(k)) == 0 for k in ("failures", "errors", "skipped"))
    return result


def regression():
    full = suite("pytest-autospread-full.xml")
    collected = [
        s
        for s in (ROOT / "artifacts/pytest-autospread-collection.log").read_text().splitlines()
        if s.startswith("tests/") and "::" in s
    ]
    modules = {
        str(p.relative_to(ROOT)).removesuffix(".py").replace("/", "."): str(p.relative_to(ROOT))
        for p in (ROOT / "tests").rglob("test_*.py")
    }
    executed = []
    for case in full.findall("testcase"):
        classname = case.get("classname")
        module = max(
            (n for n in modules if classname == n or classname.startswith(n + ".")), key=len
        )
        suffix = classname[len(module) :].strip(".").replace(".", "::")
        executed.append(
            modules[module] + "::" + (suffix + "::" if suffix else "") + case.get("name")
        )
    assert len(executed) == len(collected) == int(full.get("tests"))
    assert Counter(executed) == Counter(collected)
    started = datetime.fromisoformat(full.get("timestamp")).replace(tzinfo=UTC).timestamp()
    assert all(
        p.stat().st_mtime <= started
        for p in [*(ROOT / "backend/cytoforge").rglob("*.py"), *(ROOT / "tests").rglob("*.py")]
    )
    scope = ROOT / "artifacts/autospread-full-regression-scope.json"
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
        xml="artifacts/pytest-autospread-full.xml",
        log="artifacts/pytest-autospread-full.log",
        scope=str(scope.relative_to(ROOT)),
        transport="HTTPX in-process ASGI with lifespan; no TCP listener",
    )


def main():
    previous = read("fcs-packed-desktop-candidate-validation.json")
    full = regression()
    focused = suite("autospread-focused.xml")
    focused_counts = Counter(c.get("classname") for c in focused.findall("testcase"))
    assert focused_counts == {
        "tests.test_autospread": 31,
        "tests.test_autospill": 32,
        "tests.test_compensation": 8,
        "tests.test_quality": 19,
    }
    frozen = read("frozen-autospread-validation.json")
    assert frozen["status"] == "passed" and frozen["source_import_paths_removed"]
    assert frozen["actual_frozen_analysis_children"]
    assert len(frozen["runtime"]["cases"]) == 8
    assert sum(c["status"] == "passed" for c in frozen["runtime"]["cases"]) == 7
    assert frozen["runtime"]["reference_files_unchanged"]
    engine = ROOT / frozen["engine"]
    assert digest(engine) == frozen["engine_sha256"]
    proof_names = [
        "frozen-autospread-validation.json",
        "frozen-autospread-packed-regression.json",
        "frozen-autospread-graph-regression.json",
        "autospread-benchmark.json",
    ]
    for name in proof_names:
        proof = read(name)
        assert proof["status"] == "passed"
        if "engine_sha256" in proof:
            assert proof["engine_sha256"] == frozen["engine_sha256"]
        for path, value in proof["source_sha256"].items():
            assert digest(ROOT / path) == value, path
    reference = json.loads((ROOT / "tests/fixtures/autospread/reference.json").read_text())
    from validate_autospread_reference import R_DRIVER

    assert hashlib.sha256(R_DRIVER.encode()).hexdigest() == reference["driver_sha256"]
    for name, value in reference["fixture_sha256"].items():
        assert digest(ROOT / "tests/fixtures/autospread" / name) == value
    fonts = read("graph-font-assets.json")
    for name, value in fonts["sha256"].items():
        assert digest(ROOT / "frontend/src/assets/fonts" / name) == value
        assert digest(engine.parent / "_internal/matplotlib/mpl-data/fonts/ttf" / name) == value
    original = read("autospread-original-workspace.json")
    assert original["unchanged"]
    assert all(row["matches_acquisition"] for row in original["acquired_event_files_checked"])
    logs = {
        "typecheck": ("artifacts/typecheck-autospread.log", "tsc -b"),
        "ui_build": ("artifacts/build-autospread-ui.log", "built in"),
        "ruff": ("artifacts/ruff-autospread.log", "All checks passed!"),
        "prettier": (
            "artifacts/prettier-autospread.log",
            "All matched files use Prettier code style!",
        ),
    }
    for name, marker in logs.values():
        assert marker in (ROOT / name).read_text()
    client_log = (ROOT / "artifacts/test-autospread-client-presets.log").read_text()
    assert "# pass 8" in client_log and "# fail 0" in client_log
    names = set(previous["current_source_sha256"])
    for folder in ("backend/cytoforge", "frontend/src", "desktop", "tests"):
        names.update(
            str(p.relative_to(ROOT))
            for p in (ROOT / folder).rglob("*")
            if p.is_file() and p.suffix in {".py", ".ts", ".tsx", ".css", ".mjs", ".cjs", ".ttf"}
        )
    names.update(
        [
            "docs/AUTOSPREAD.md",
            "tests/fixtures/autospread/controls.npz",
            "tests/fixtures/autospread/truth.json",
            "tests/fixtures/autospread/reference.json",
            "tests/fixtures/autospread/README.md",
            "tools/validate_autospread_reference.py",
            "tools/validate_frozen_autospread.py",
            "tools/benchmark_autospread.py",
            "tools/check_autospread_checkpoint.py",
            "tools/finalize_autospread_candidate.py",
            "tools/test_spreading.sh",
            "tools/test_spreading.mjs",
        ]
    )
    remaining = previous["retained_full_goal_unverified"]
    assert len(remaining) == 20 and not previous["full_objective_complete"]
    record = dict(
        status="source_checks_passed_frozen_engine_passed_native_validation_pending",
        checked_at=datetime.now(UTC).isoformat(),
        scope=(
            "Published two-regression spreading, conventional/spectral controls "
            "and saved desktop review"
        ),
        full_python_regression=full,
        focused_regression=dict(
            status="passed",
            tests=int(focused.get("tests")),
            per_module=dict(focused_counts),
            xml="artifacts/autospread-focused.xml",
        ),
        compiled_client_presets=dict(
            status="passed", tests=8, log="artifacts/test-autospread-client-presets.log"
        ),
        scientific_sources_unchanged_since_regression_started=True,
        frozen_engine=frozen,
        frozen_packed_import_regression=read(proof_names[1]),
        frozen_graph_regression=read(proof_names[2]),
        spreading_benchmark=read(proof_names[3]),
        independent_reference=reference,
        original_workspace=original,
        bundled_native_and_report_fonts=dict(status="passed", faces=len(fonts["sha256"])),
        desktop_interaction_executed=False,
        full_objective_complete=False,
        retained_full_goal_unverified=remaining,
        current_pending=[
            "Native spreading dialog interaction",
            "External biological/instrument spreading truth",
            "Closed-source FlowJo spreading comparison",
            "Rectangular spectral AutoSpill estimation",
            "All other retained full-goal requirements",
        ],
        previous_goal_turn_classification="no_progress_launch_instructions_only",
        next_safe_action_completed=(
            "Implemented and verified published spreading and portable report workflow"
        ),
        current_source_sha256={name: digest(ROOT / name) for name in sorted(names)},
        **{key: dict(status="passed", log=value[0]) for key, value in logs.items()},
    )
    (ROOT / "artifacts/autospread-source-validation.json").write_text(
        json.dumps(record, indent=2) + "\n"
    )
    print(
        json.dumps(
            dict(
                status=record["status"],
                tests=full["tests"],
                source_files=len(names),
                retained_requirements=len(remaining),
                full_objective_complete=False,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
