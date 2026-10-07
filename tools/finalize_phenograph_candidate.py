"""Record the immutable native candidate without launching or changing user profiles."""

import hashlib
import json
import socket
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
    source = read("phenograph-source-validation.json")
    integrity = read("phenograph-desktop-package-integrity.json")
    previous = read("multiaf-desktop-candidate-validation.json")
    assert source["full_python_regression"]["status"] == integrity["status"] == "passed"
    assert source["retained_full_goal_unverified"] == previous["retained_full_goal_unverified"]
    assert (
        len(source["retained_full_goal_unverified"]) == 20 and not source["full_objective_complete"]
    )
    binary = ROOT / "artifacts/candidates/phenograph/desktop/CytoForge-0.1.0.AppImage"
    assert digest(binary) == integrity["appimage_sha256"]
    changed_docs, current_hashes = [], {}
    for name, expected in source["current_source_sha256"].items():
        actual = digest(ROOT / name)
        if actual != expected:
            assert name == "README.md", f"Implementation changed after its checkpoint: {name}"
            changed_docs.append(name)
        current_hashes[name] = actual
    retained = read("multiaf-retained-installers.json")["appimages"]
    if previous["appimage"] not in {r["appimage"] for r in retained}:
        retained.append(
            dict(
                appimage=previous["appimage"],
                sha256=previous["appimage_sha256"],
                bytes=previous["appimage_bytes"],
            )
        )
    for row in retained:
        path = ROOT / row["appimage"]
        assert path.resolve().is_relative_to(ROOT) and path.is_file()
        assert digest(path) == row["sha256"] and path.stat().st_size == row["bytes"]
    write(
        "phenograph-retained-installers.json",
        dict(
            status="unchanged",
            appimages=retained,
            scope="All previous installers retained; no preview profile writes performed",
        ),
    )
    code = """
import { previewConfiguration } from './tools/desktop-preview/config.mjs';
const c = previewConfiguration(process.cwd(), {
 CYTOFORGE_PREVIEW_NAME:'phenograph',
 CYTOFORGE_PREVIEW_BINARY:'artifacts/candidates/phenograph/desktop/CytoForge-0.1.0.AppImage',
 CYTOFORGE_PREVIEW_HOST:'0.0.0.0', CYTOFORGE_PREVIEW_PORT:'8001',
 CYTOFORGE_PREVIEW_PUBLIC_HOST:'192.0.2.10'
});
console.log(JSON.stringify({host:c.host,port:c.port,selected_binary:c.binary,
 runtime:c.runtime,session_path:c.sessionPath}));
"""
    run = subprocess.run(
        ["node", "--input-type=module", "-e", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    preview = json.loads(run.stdout)
    assert preview["host"] == "0.0.0.0" and preview["port"] == 8001
    assert Path(preview["selected_binary"]).resolve() == binary.resolve()
    preview.update(
        status="configuration_passed_not_launched",
        native_appimage_launched=False,
        tcp_listener_started=False,
        scope="Read-only configuration; no token or profile created",
    )
    write("phenograph-desktop-preview-configuration.json", preview)
    capability = dict(
        checked_at=datetime.now(UTC).isoformat(),
        listener_started=False,
        native_validation_executed=False,
        scope="Socket creation and immediate close; no bind",
    )
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.close()
        capability.update(status="socket_creation_allowed_native_validation_pending")
    except OSError as exc:
        capability.update(status="socket_creation_denied", errno=exc.errno, reason=str(exc))
    write("phenograph-native-capability.json", capability)
    compacted = read("phenograph-compacted-build-manifest.json")
    assert compacted["status"] == "compacted" and compacted["appimage_sha256"] == digest(binary)
    record = dict(source)
    record.update(
        status="package_integrity_and_preview_configuration_passed_native_validation_pending",
        checked_at=datetime.now(UTC).isoformat(),
        current_source_sha256=current_hashes,
        appimage=str(binary.relative_to(ROOT)),
        appimage_sha256=digest(binary),
        appimage_bytes=binary.stat().st_size,
        package_integrity=dict(
            status="passed",
            proof="artifacts/phenograph-desktop-package-integrity.json",
            engine_files=integrity["complete_engine_files_matched"],
            frontend_files=integrity["frontend_files_matched"],
            desktop_files=integrity["desktop_files_matched"],
        ),
        preview=dict(
            status=preview["status"],
            host=preview["host"],
            port=preview["port"],
            proof="artifacts/phenograph-desktop-preview-configuration.json",
        ),
        native_capability=dict(
            status=capability["status"], proof="artifacts/phenograph-native-capability.json"
        ),
        candidate_build_copies=dict(
            status="verified_and_compacted",
            proof="artifacts/phenograph-compacted-build-manifest.json",
            appimage_retained=True,
            byte_exact_recovery_manifest_retained=True,
        ),
        prior_installers=dict(
            status="unchanged", proof="artifacts/phenograph-retained-installers.json"
        ),
        documentation_updated_after_packaging=changed_docs,
        implementation_inputs_unchanged_since_source_checkpoint=True,
    )
    write("phenograph-desktop-candidate-validation.json", record)
    print(
        json.dumps(
            dict(
                status=record["status"],
                appimage=record["appimage"],
                tests=record["full_python_regression"]["tests"],
                source_files=len(current_hashes),
                prior_installers_retained=len(retained),
                native_validation_executed=False,
                retained_requirements=20,
                full_objective_complete=False,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
