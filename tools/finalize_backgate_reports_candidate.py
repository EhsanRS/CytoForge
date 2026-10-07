"""Record the checked desktop candidate without claiming a native GUI launch."""

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def read(name):
    return json.loads((ROOT / "artifacts" / name).read_text())


def write(name, value):
    (ROOT / "artifacts" / name).write_text(json.dumps(value, indent=2) + "\n")


def main():
    source = read("backgate-reports-source-validation.json")
    integrity = read("backgate-reports-desktop-package-integrity.json")
    assert integrity["status"] == "passed"
    binary = ROOT / "artifacts/candidates/backgate-reports/desktop/CytoForge-0.1.0.AppImage"
    assert digest(binary) == integrity["appimage_sha256"]
    assert source["full_python_regression"]["status"] == "passed"
    assert not source["full_objective_complete"]
    previous = read("graph-gates-desktop-candidate-validation.json")
    assert source["retained_full_goal_unverified"] == previous["retained_full_goal_unverified"]
    assert len(source["retained_full_goal_unverified"]) == 20
    documentation = []
    hashes = {}
    for name, checksum in source["current_source_sha256"].items():
        current = digest(ROOT / name)
        if current != checksum:
            assert name == "README.md", f"Implementation changed after packaging: {name}"
            documentation.append(name)
        hashes[name] = current
    rows = read("graph-gates-retained-installers.json")["appimages"]
    if previous["appimage"] not in {row["appimage"] for row in rows}:
        rows.append(
            dict(
                appimage=previous["appimage"],
                sha256=previous["appimage_sha256"],
                bytes=previous["appimage_bytes"],
            )
        )
    for row in rows:
        path = ROOT / row["appimage"]
        assert path.resolve().is_relative_to(ROOT) and path.is_file()
        assert digest(path) == row["sha256"] and path.stat().st_size == row["bytes"]
    write(
        "backgate-reports-retained-installers.json",
        dict(
            status="unchanged",
            appimages=rows,
            scope="Retained installers; no user profiles modified",
        ),
    )
    code = """
import { previewConfiguration } from './tools/desktop-preview/config.mjs';
const c = previewConfiguration(process.cwd(), {
 CYTOFORGE_PREVIEW_NAME: 'backgate-reports',
 CYTOFORGE_PREVIEW_BINARY: 'artifacts/candidates/backgate-reports/desktop/CytoForge-0.1.0.AppImage',
 CYTOFORGE_PREVIEW_HOST: '0.0.0.0', CYTOFORGE_PREVIEW_PORT: '8001',
 CYTOFORGE_PREVIEW_PUBLIC_HOST: '192.0.2.10'
});
console.log(JSON.stringify({host:c.host, port:c.port, selected_binary:c.binary,
 runtime:c.runtime, session_path:c.sessionPath}));
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    preview = json.loads(result.stdout)
    assert preview["host"] == "0.0.0.0" and preview["port"] == 8001
    assert Path(preview["selected_binary"]).resolve() == binary.resolve()
    preview.update(
        status="configuration_passed_not_launched",
        native_appimage_launched=False,
        tcp_listener_started=False,
        scope="Read-only configuration; no token or profile created",
    )
    write("backgate-reports-desktop-preview-configuration.json", preview)
    capability = read("backgate-reports-native-capability.json")
    assert capability["status"] == "socket_creation_denied" and capability["errno"] == 1
    assert not capability["listener_started"] and not capability["native_validation_executed"]
    record = dict(source)
    record.update(
        status="package_integrity_and_preview_configuration_passed_native_validation_pending",
        checked_at=datetime.now(UTC).isoformat(),
        current_source_sha256=hashes,
        appimage=str(binary.relative_to(ROOT)),
        appimage_sha256=digest(binary),
        appimage_bytes=binary.stat().st_size,
        package_integrity=dict(
            status="passed",
            proof="artifacts/backgate-reports-desktop-package-integrity.json",
            engine_files=integrity["complete_engine_files_matched"],
            frontend_files=integrity["frontend_files_matched"],
            desktop_files=integrity["desktop_files_matched"],
        ),
        preview=dict(
            status=preview["status"],
            host=preview["host"],
            port=preview["port"],
            proof="artifacts/backgate-reports-desktop-preview-configuration.json",
        ),
        prior_installers=dict(
            status="unchanged", proof="artifacts/backgate-reports-retained-installers.json"
        ),
        documentation_updated_after_packaging=documentation,
        implementation_inputs_unchanged_since_source_checkpoint=True,
        native_capability=dict(
            status=capability["status"], proof="artifacts/backgate-reports-native-capability.json"
        ),
        reproducible_build_compaction=dict(
            status="verified", proof="artifacts/graph-gates-compacted-build-manifest.json"
        ),
    )
    write("backgate-reports-desktop-candidate-validation.json", record)
    print(
        json.dumps(
            dict(
                status=record["status"],
                tests=source["full_python_regression"]["tests"],
                appimage_sha256=record["appimage_sha256"],
                retained_installers=len(rows),
                full_objective_complete=False,
            )
        )
    )


if __name__ == "__main__":
    main()
