"""Create a source checkpoint from executed checks and their current inputs."""

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


def main():
    previous = json.loads((ROOT / "artifacts/custom-plates-source-validation.json").read_text())
    full_xml = ROOT / "artifacts/pytest-graph-fonts-full.xml"
    suite = ET.parse(full_xml).getroot().find("testsuite")
    assert suite is not None
    total = int(suite.get("tests"))
    assert all(int(suite.get(key)) == 0 for key in ["failures", "errors", "skipped"])
    collected = [
        line
        for line in (ROOT / "artifacts/pytest-graph-fonts-collection.log").read_text().splitlines()
        if line.startswith("tests/") and "::" in line
    ]
    modules = {
        str(path.relative_to(ROOT)).removesuffix(".py").replace("/", "."): str(
            path.relative_to(ROOT)
        )
        for path in (ROOT / "tests").rglob("test_*.py")
    }
    executed = []
    for case in suite.findall("testcase"):
        classname = case.get("classname")
        matches = [
            module
            for module in modules
            if classname == module or classname.startswith(module + ".")
        ]
        module = max(matches, key=len)
        suffix = classname[len(module) :].strip(".").replace(".", "::")
        executed.append(
            modules[module] + "::" + (suffix + "::" if suffix else "") + case.get("name")
        )
    assert len(collected) == total == len(executed)
    assert Counter(collected) == Counter(executed)
    started = datetime.fromisoformat(suite.get("timestamp")).replace(tzinfo=UTC).timestamp()
    science_inputs = sorted(
        [*(ROOT / "backend/cytoforge").rglob("*.py"), *(ROOT / "tests").rglob("*.py")]
    )
    assert all(path.stat().st_mtime <= started for path in science_inputs)
    scope_path = ROOT / "artifacts/graph-fonts-full-regression-scope.json"
    scope_path.write_text(
        json.dumps(
            dict(status="passed", collected_nodeids=collected, executed_nodeids=executed), indent=2
        )
        + "\n"
    )
    logs = {
        "typecheck": ("artifacts/typecheck-graph-fonts.log", "tsc -b --pretty false"),
        "ui_build": ("artifacts/build-graph-fonts-ui.log", "built in"),
        "ruff": ("artifacts/ruff-graph-fonts.log", "All checks passed!"),
        "prettier": (
            "artifacts/prettier-graph-fonts-final.log",
            "All matched files use Prettier code style!",
        ),
    }
    for path, marker in logs.values():
        assert marker in (ROOT / path).read_text()
    assert "# pass 23" in (ROOT / "artifacts/test-graph-fonts.log").read_text()
    assert "# fail 0" in (ROOT / "artifacts/test-graph-fonts.log").read_text()
    focused = (
        ET.parse(ROOT / "artifacts/pytest-graph-fonts-focused.xml").getroot().find("testsuite")
    )
    assert int(focused.get("tests")) == 41 and int(focused.get("failures")) == 0
    frozen = json.loads((ROOT / "artifacts/frozen-graph-fonts-engine-validation.json").read_text())
    assert frozen["status"] == "passed"
    for name, checksum in frozen["source_sha256"].items():
        assert digest(ROOT / name) == checksum
    original = json.loads((ROOT / "artifacts/graph-fonts-original-workspace.json").read_text())
    assert original["unchanged"] and all(
        row["matches_acquisition"] for row in original["acquired_event_files_checked"]
    )
    font_assets = json.loads((ROOT / "artifacts/graph-font-assets.json").read_text())
    benchmark = json.loads((ROOT / "artifacts/graph-font-benchmark.json").read_text())
    assert benchmark["status"] == "passed" and benchmark["events"] == 65536
    assert (
        benchmark["scientific_payloads_unchanged"]
        and benchmark["three_d_buffers_and_keys_unchanged"]
    )
    for name, checksum in font_assets["sha256"].items():
        assert digest(ROOT / "frontend/src/assets/fonts" / name) == checksum
        assert (
            digest(
                ROOT
                / "artifacts/candidates/graph-fonts/engine/cytoforge-engine"
                / "_internal/matplotlib/mpl-data/fonts/ttf"
                / name
            )
            == checksum
        )
    names = set(previous["current_source_sha256"])
    names.update(
        str(path.relative_to(ROOT))
        for directory in ["backend/cytoforge", "frontend/src", "desktop", "tests"]
        for path in (ROOT / directory).rglob("*")
        if path.is_file() and path.suffix in {".py", ".ts", ".tsx", ".css", ".cjs", ".mjs", ".ttf"}
    )
    names.update(
        [
            "frontend/public/licenses/DEJAVU.txt",
            "tools/build_graph_fonts.py",
            "tools/compact_desktop_candidate.py",
            "tools/test_graph_typography.sh",
            "tools/test_graph_typography.mjs",
            "tools/desktop_graph_typography_smoke.mjs",
            "tools/audit_desktop_progress_workspace.py",
            "tools/check_graph_typography_checkpoint.py",
            "tools/benchmark_graph_fonts.py",
        ]
    )
    hashes = {name: digest(ROOT / name) for name in sorted(names)}
    record = dict(
        status="source_checks_passed_frozen_engine_passed_native_validation_pending",
        checked_at=datetime.now(UTC).isoformat(),
        scope="Desktop/popup and physical publication plot typography; "
        "full regression, frozen workers and immutable font assets",
        full_python_regression=dict(
            status="passed",
            tests=total,
            failures=0,
            errors=0,
            skipped=0,
            seconds=float(suite.get("time")),
            log=str(full_xml.with_suffix(".log").relative_to(ROOT)),
            xml=str(full_xml.relative_to(ROOT)),
            scope=str(scope_path.relative_to(ROOT)),
            transport="HTTPX ASGI transport with application lifespan; no TCP listener",
        ),
        focused_graph_typography=dict(
            status="passed",
            tests=41,
            xml="artifacts/pytest-graph-fonts-focused.xml",
            log="artifacts/pytest-graph-fonts-focused.log",
        ),
        compiled_graph_fonts_and_native_IPC=dict(
            status="passed", tests=23, log="artifacts/test-graph-fonts.log"
        ),
        scientific_and_test_sources_unchanged_since_regression_started=True,
        scientific_and_test_source_files=len(science_inputs),
        candidate_frozen_engine=dict(
            status="passed", proof="artifacts/frozen-graph-fonts-engine-validation.json"
        ),
        bundled_native_and_report_fonts=dict(
            status="passed",
            faces=len(font_assets["sha256"]),
            proof="artifacts/graph-font-assets.json",
        ),
        graph_font_benchmark=dict(status="passed", proof="artifacts/graph-font-benchmark.json"),
        original_workspace=dict(
            status="unchanged", proof="artifacts/graph-fonts-original-workspace.json"
        ),
        native_graph_font_workflow=dict(
            status="prepared_not_executed",
            script="tools/desktop_graph_typography_smoke.mjs",
            reason="Local TCP socket creation returns EPERM; no native launch claimed",
        ),
        full_objective_complete=False,
        retained_full_goal_unverified=previous["retained_full_goal_unverified"],
        current_pending=previous["current_pending"]
        + ["Packaged native graph typography workflow, Linux sandbox and GUI export validation"],
        current_source_sha256=hashes,
        **{name: dict(status="passed", log=path) for name, (path, _) in logs.items()},
    )
    (ROOT / "artifacts/graph-fonts-source-validation.json").write_text(
        json.dumps(record, indent=2) + "\n"
    )
    print(
        json.dumps(
            dict(
                status=record["status"],
                tests=total,
                source_inputs=len(hashes),
                full_objective_complete=False,
            )
        )
    )


if __name__ == "__main__":
    main()
