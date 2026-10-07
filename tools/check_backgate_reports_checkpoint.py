"""Record executed source and frozen checks while retaining the full desktop objective."""

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


def regression():
    xml = ROOT / "artifacts/pytest-backgate-reports-full.xml"
    suite = ET.parse(xml).getroot().find("testsuite")
    assert suite is not None
    assert all(int(suite.get(key)) == 0 for key in ["failures", "errors", "skipped"])
    collected = [
        line
        for line in (ROOT / "artifacts/pytest-backgate-reports-collection.log")
        .read_text()
        .splitlines()
        if line.startswith("tests/") and "::" in line
    ]
    modules = {
        str(p.relative_to(ROOT)).removesuffix(".py").replace("/", "."): str(p.relative_to(ROOT))
        for p in (ROOT / "tests").rglob("test_*.py")
    }
    executed = []
    for case in suite.findall("testcase"):
        classname = case.get("classname")
        module = max(
            (m for m in modules if classname == m or classname.startswith(m + ".")), key=len
        )
        suffix = classname[len(module) :].strip(".").replace(".", "::")
        executed.append(
            modules[module] + "::" + (suffix + "::" if suffix else "") + case.get("name")
        )
    assert len(collected) == len(executed) == int(suite.get("tests"))
    assert Counter(collected) == Counter(executed)
    started = datetime.fromisoformat(suite.get("timestamp")).replace(tzinfo=UTC).timestamp()
    inputs = [*(ROOT / "backend/cytoforge").rglob("*.py"), *(ROOT / "tests").rglob("*.py")]
    assert all(p.stat().st_mtime <= started for p in inputs)
    scope = ROOT / "artifacts/backgate-reports-full-regression-scope.json"
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
        seconds=float(suite.get("time")),
        xml=str(xml.relative_to(ROOT)),
        log="artifacts/pytest-backgate-reports-full.log",
        scope=str(scope.relative_to(ROOT)),
        transport="HTTPX in-process ASGI with lifespan; no TCP listener",
    ), len(inputs)


def main():
    previous = read("graph-gates-desktop-candidate-validation.json")
    full, science_count = regression()
    focused = (
        ET.parse(ROOT / "artifacts/pytest-backgate-reports-focused.xml").getroot().find("testsuite")
    )
    assert int(focused.get("tests")) == 33
    assert all(int(focused.get(k)) == 0 for k in ["failures", "errors", "skipped"])
    logs = {
        "typecheck": ("artifacts/typecheck-backgate-reports.log", "tsc -b --pretty false"),
        "ui_build": ("artifacts/build-backgate-reports-ui.log", "built in"),
        "ruff": ("artifacts/ruff-backgate-reports.log", "All checks passed!"),
        "prettier": (
            "artifacts/prettier-backgate-reports.log",
            "All matched files use Prettier code style!",
        ),
    }
    for file, marker in logs.values():
        assert marker in (ROOT / file).read_text()
    compiled = {}
    for label, file, count in [
        ("backgate_controls_and_ancestry", "artifacts/test-report-backgates.log", 21),
        ("gate_style_and_native_IPC", "artifacts/test-backgate-gate-style-regression.log", 27),
        ("graph_font_regression", "artifacts/test-backgate-font-regression.log", 23),
    ]:
        log = (ROOT / file).read_text()
        assert f"# pass {count}" in log and "# fail 0" in log
        compiled[label] = dict(status="passed", tests=count, log=file)
    frozen = read("frozen-backgate-reports-engine-validation.json")
    assert frozen["status"] == "passed" and len(frozen["cases"]) == 4
    successful = [case for case in frozen["cases"] if "report_template_runtime" in case]
    assert len(successful) == 2
    assert all(
        case["report_template_runtime"]["graph_backgate_reports_verified"] for case in successful
    )
    for name, checksum in frozen["source_sha256"].items():
        assert digest(ROOT / name) == checksum
    benchmark = read("report-backgate-benchmark.json")
    assert (
        benchmark["status"] == "passed"
        and benchmark["events"] == 1048576
        and benchmark["known_backgate_events"] == 131072
    )
    assert all(
        benchmark[key]
        for key in [
            "scientific_payloads_unchanged",
            "complete_three_d_event_identities_verified",
            "complete_three_d_highlight_membership_verified",
            "source_and_workspace_unchanged",
            "generated_test_data_removed",
        ]
    )
    for name, checksum in benchmark["source_sha256"].items():
        assert digest(ROOT / name) == checksum
    original = read("backgate-reports-original-workspace.json")
    assert original["unchanged"] and all(
        row["matches_acquisition"] for row in original["acquired_event_files_checked"]
    )
    fonts = read("graph-font-assets.json")
    for name, checksum in fonts["sha256"].items():
        assert digest(ROOT / "frontend/src/assets/fonts" / name) == checksum
        assert (
            digest(
                ROOT
                / "artifacts/candidates/backgate-reports/engine/cytoforge-engine"
                / "_internal/matplotlib/mpl-data/fonts/ttf"
                / name
            )
            == checksum
        )
    capability = read("backgate-reports-native-capability.json")
    assert capability["status"] == "socket_creation_denied" and capability["errno"] == 1
    assert not capability["native_validation_executed"] and not capability["listener_started"]
    watson = read("watson-refinement-investigation.json")
    assert watson["status"] == "research_only_not_enabled"
    assert (
        digest(ROOT / "backend/cytoforge/cellcycle.py")
        == watson["source_sha256"]["backend/cytoforge/cellcycle.py"]
    )
    names = set(previous["current_source_sha256"])
    for directory in ["backend/cytoforge", "frontend/src", "desktop", "tests"]:
        names.update(
            str(p.relative_to(ROOT))
            for p in (ROOT / directory).rglob("*")
            if p.is_file() and p.suffix in {".py", ".ts", ".tsx", ".css", ".cjs", ".mjs", ".ttf"}
        )
    names.update(
        [
            "tools/test_report_backgates.sh",
            "tools/test_report_backgates.mjs",
            "tools/desktop_backgate_reports_smoke.mjs",
            "tools/benchmark_report_backgates.py",
            "tools/check_backgate_reports_checkpoint.py",
            "tools/finalize_backgate_reports_candidate.py",
            "docs/BACKGATING.md",
        ]
    )
    record = dict(
        status="source_checks_passed_frozen_engine_passed_native_validation_pending",
        checked_at=datetime.now(UTC).isoformat(),
        scope="Saved desktop/popup backgates, ancestry, source closure, "
        "vector reports and viewport sampling",
        full_python_regression=full,
        scientific_and_test_source_files=science_count,
        scientific_and_test_sources_unchanged_since_regression_started=True,
        focused_backgate_reports=dict(
            status="passed",
            tests=33,
            xml="artifacts/pytest-backgate-reports-focused.xml",
            log="artifacts/pytest-backgate-reports-focused.log",
        ),
        compiled_interface_checks=compiled,
        candidate_frozen_engine=dict(
            status="passed", proof="artifacts/frozen-backgate-reports-engine-validation.json"
        ),
        bundled_native_and_report_fonts=dict(
            status="passed", faces=len(fonts["sha256"]), proof="artifacts/graph-font-assets.json"
        ),
        backgate_benchmark=dict(status="passed", proof="artifacts/report-backgate-benchmark.json"),
        original_workspace=dict(
            status="unchanged", proof="artifacts/backgate-reports-original-workspace.json"
        ),
        native_backgate_workflow=dict(
            status="prepared_not_executed",
            script="tools/desktop_backgate_reports_smoke.mjs",
            reason="TCP socket creation returns EPERM",
            capability="artifacts/backgate-reports-native-capability.json",
        ),
        historical_watson_research=dict(
            status="research_only_not_enabled",
            proof="artifacts/watson-refinement-investigation.json",
            cell_cycle_implementation_matches_investigated_version=True,
        ),
        previous_goal_turn_classification="progress_packaged_graph_gate_candidate_and_preserved_full_scope",
        full_objective_complete=False,
        retained_full_goal_unverified=previous["retained_full_goal_unverified"],
        current_pending=[
            p
            for p in previous["current_pending"]
            if p != "Complete isolated desktop candidate packaging and package content verification"
        ]
        + ["Actual AppImage backgate/ancestry workflow and export GUI validation"],
        current_source_sha256={name: digest(ROOT / name) for name in sorted(names)},
        **{name: dict(status="passed", log=file) for name, (file, _) in logs.items()},
    )
    assert len(record["retained_full_goal_unverified"]) == 20
    (ROOT / "artifacts/backgate-reports-source-validation.json").write_text(
        json.dumps(record, indent=2) + "\n"
    )
    print(
        json.dumps(
            dict(
                status=record["status"],
                tests=full["tests"],
                source_inputs=len(names),
                full_objective_complete=False,
            )
        )
    )


if __name__ == "__main__":
    main()
