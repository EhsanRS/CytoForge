"""Verify the packed import checkpoint against executed checks and current sources."""

from __future__ import annotations

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
    assert all(int(result.get(field)) == 0 for field in ["failures", "errors", "skipped"])
    return result


def full_regression():
    full = suite("pytest-fcs-packed-full.xml")
    collected = [
        line
        for line in (ROOT / "artifacts/pytest-fcs-packed-collection.log").read_text().splitlines()
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
    science = [*(ROOT / "backend/cytoforge").rglob("*.py"), *(ROOT / "tests").rglob("*.py")]
    assert all(path.stat().st_mtime <= started for path in science)
    scope = ROOT / "artifacts/fcs-packed-full-regression-scope.json"
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
        xml="artifacts/pytest-fcs-packed-full.xml",
        log="artifacts/pytest-fcs-packed-full.log",
        scope=str(scope.relative_to(ROOT)),
        transport="HTTPX in-process ASGI with lifespan; no TCP listener",
    )


def main():
    previous = read("backgate-reports-desktop-candidate-validation.json")
    full = full_regression()
    focused = suite("fcs-packed-focused.xml")
    focused_counts = Counter(case.get("classname") for case in focused.findall("testcase"))
    assert focused_counts == {"tests.test_fcs_packed": 90, "tests.test_imports": 61}
    logs = {
        "typecheck": ("artifacts/typecheck-fcs-packed.log", "tsc -b"),
        "ui_build": ("artifacts/build-fcs-packed-ui.log", "built in"),
        "ruff": ("artifacts/ruff-fcs-packed.log", "All checks passed!"),
        "prettier": (
            "artifacts/prettier-fcs-packed.log",
            "All matched files use Prettier code style!",
        ),
    }
    for file, marker in logs.values():
        assert marker in (ROOT / file).read_text()
    compiled = {}
    for label, file, count in [
        ("backgate_controls_and_ancestry", "artifacts/test-fcs-packed-backgates.log", 21),
        ("gate_style_and_native_IPC", "artifacts/test-fcs-packed-gate-style.log", 27),
        ("graph_font_controls", "artifacts/test-fcs-packed-graph-fonts.log", 23),
    ]:
        log = (ROOT / file).read_text()
        assert f"# pass {count}" in log and "# fail 0" in log
        compiled[label] = dict(status="passed", tests=count, log=file)
    frozen = read("frozen-fcs-packed-engine-validation.json")
    assert frozen["status"] == "passed" and frozen["runtime"]["frozen"]
    assert len(frozen["runtime"]["cases"]) == 8
    assert frozen["source_import_paths_removed"] and frozen["runtime"]["input_files_unchanged"]
    assert sum(case["status"] == "passed" for case in frozen["runtime"]["cases"]) == 4
    scientific = next(
        case for case in frozen["runtime"]["cases"] if case["name"] == "native-science"
    )
    assert scientific["restart_identity_and_gating_verified"]
    assert scientific["gate_event_ids"] == [2, 3, 4, 10, 11, 12, 18, 19, 20, 26, 27, 28]
    assert not frozen["desktop_interaction_executed"] and not frozen["http_listener_started"]
    graph_frozen = read("frozen-fcs-packed-graph-regression.json")
    assert graph_frozen["status"] == "passed" and len(graph_frozen["cases"]) == 4
    assert graph_frozen["engine_sha256"] == frozen["engine_sha256"]
    for proof in [frozen, graph_frozen]:
        assert digest(ROOT / proof["engine"]) == proof["engine_sha256"]
        for name, sha256 in proof["source_sha256"].items():
            assert digest(ROOT / name) == sha256
    benchmark = read("fcs-packed-benchmark.json")
    assert benchmark["status"] == "passed" and benchmark["generated_test_data_removed"]
    assert [m["events"] for m in benchmark["measurements"]] == [131075, 1048579, 4194307]
    for row in benchmark["measurements"]:
        assert row["every_preprocessed_value_verified_by_independent_sha256"]
        assert row["rss_growth_bytes"] < 64 * 1024**2
    for name, sha256 in benchmark["source_sha256"].items():
        assert digest(ROOT / name) == sha256
    inventory = read("fcs-packed-reference-inventory.json")
    assert inventory["files_checked"] == 18 and not inventory["packed_reference_paths"]
    assert inventory["importer_sha256"] == digest(ROOT / "backend/cytoforge/imports.py")
    assert not inventory["external_packed_truth_verified"]
    original = read("fcs-packed-original-workspace.json")
    assert original["unchanged"]
    assert all(row["matches_acquisition"] for row in original["acquired_event_files_checked"])
    capability = read("fcs-packed-native-capability.json")
    assert capability["status"] == "socket_creation_denied" and capability["errno"] == 1
    assert not capability["native_validation_executed"] and not capability["listener_started"]
    fonts = read("graph-font-assets.json")
    engine = ROOT / frozen["engine"]
    for name, sha256 in fonts["sha256"].items():
        assert digest(ROOT / "frontend/src/assets/fonts" / name) == sha256
        assert digest(engine.parent / "_internal/matplotlib/mpl-data/fonts/ttf" / name) == sha256
    compaction = read("backgate-reports-compacted-build-manifest.json")
    assert compaction["status"] == "compacted"
    assert digest(ROOT / compaction["appimage"]) == compaction["appimage_sha256"]
    fixtures = json.loads((ROOT / "artifacts/fcs-packed-fixture/truth.json").read_text())
    assert fixtures["synthetic"] and fixtures["gate"]["count"] == 12
    for name, sha256 in fixtures["files"].items():
        assert digest(ROOT / "artifacts/fcs-packed-fixture" / name) == sha256
    names = set(previous["current_source_sha256"])
    for directory in ["backend/cytoforge", "frontend/src", "desktop", "tests"]:
        names.update(
            str(path.relative_to(ROOT))
            for path in (ROOT / directory).rglob("*")
            if path.is_file()
            and path.suffix in {".py", ".ts", ".tsx", ".css", ".cjs", ".mjs", ".ttf"}
        )
    names.update(
        [
            "docs/IMPORTS.md",
            "tools/import_fixture.py",
            "tools/benchmark_fcs_packed.py",
            "tools/validate_frozen_fcs_packed.py",
            "tools/prepare_fcs_packed_fixture.py",
            "tools/desktop_fcs_packed_smoke.mjs",
            "tools/check_fcs_packed_checkpoint.py",
            "tools/finalize_fcs_packed_candidate.py",
            "tools/compact_desktop_candidate.py",
            "artifacts/fcs-packed-fixture/truth.json",
            *["artifacts/fcs-packed-fixture/" + name for name in fixtures["files"]],
        ]
    )
    record = dict(
        status="source_checks_passed_frozen_engine_passed_native_validation_pending",
        checked_at=datetime.now(UTC).isoformat(),
        scope=(
            "Bounded little-endian packed FCS integer import, "
            "scientific identities and desktop candidate"
        ),
        full_python_regression=full,
        scientific_and_test_sources_unchanged_since_regression_started=True,
        focused_packed_imports=dict(
            status="passed",
            packed_tests=90,
            import_regression_tests=61,
            xml="artifacts/fcs-packed-focused.xml",
        ),
        compiled_interface_checks=compiled,
        candidate_frozen_engine=dict(
            status="passed", proof="artifacts/frozen-fcs-packed-engine-validation.json"
        ),
        frozen_scientific_graph_regression=dict(
            status="passed", proof="artifacts/frozen-fcs-packed-graph-regression.json"
        ),
        packed_benchmark=dict(status="passed", proof="artifacts/fcs-packed-benchmark.json"),
        cached_instrument_reference_inventory=dict(
            status="inspected_no_packed_reference_available",
            proof="artifacts/fcs-packed-reference-inventory.json",
            external_packed_truth_verified=False,
        ),
        original_workspace=dict(
            status="unchanged", proof="artifacts/fcs-packed-original-workspace.json"
        ),
        bundled_native_and_report_fonts=dict(status="passed", faces=len(fonts["sha256"])),
        native_packed_workflow=dict(
            status="prepared_not_executed",
            script="tools/desktop_fcs_packed_smoke.mjs",
            reason="TCP socket creation returns EPERM",
            capability="artifacts/fcs-packed-native-capability.json",
        ),
        previous_goal_turn_classification="no_progress_read_only_launcher_confirmation",
        next_safe_action_completed=(
            "Implemented and verified packed FCS decoding while retaining full scope"
        ),
        full_objective_complete=False,
        retained_full_goal_unverified=previous["retained_full_goal_unverified"],
        current_pending=previous["current_pending"]
        + [
            "Actual packaged packed-FCS file chooser, individual plot and restart workflow",
            "External packed instrument truth and verified big-endian packed decoding",
            "FCS 3.2 mixed parameter data types",
        ],
        current_source_sha256={name: digest(ROOT / name) for name in sorted(names)},
        **{name: dict(status="passed", log=file) for name, (file, _) in logs.items()},
    )
    assert len(record["retained_full_goal_unverified"]) == 20
    (ROOT / "artifacts/fcs-packed-source-validation.json").write_text(
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
