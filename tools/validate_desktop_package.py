"""Verify actual AppImage contents match the validated engine and current UI build."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--package-dir",
    type=Path,
    default=Path(os.environ.get("CYTOFORGE_PACKAGE_DIR", "artifacts/installers")),
)
parser.add_argument(
    "--engine", type=Path, default=Path("artifacts/engine/cytoforge-engine/cytoforge-engine")
)
parser.add_argument("--engine-proof", type=Path, default=Path("artifacts/engine-smoke.json"))
parser.add_argument("--output", type=Path, default=Path("artifacts/desktop-package-integrity.json"))
args = parser.parse_args()


def owned(value):
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        parser.error("Package inputs, validation output and extraction must stay in this checkout")
    return path


directory = owned(args.package_dir)
engine = owned(args.engine)
engine_proof = owned(args.engine_proof)
output = owned(args.output)
binary = directory / "CytoForge-0.1.0.AppImage"


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


temporary = Path(tempfile.mkdtemp(prefix="desktop-package-integrity-", dir=root / ".tmp"))
try:
    subprocess.run(
        [str(binary), "--appimage-extract"], cwd=temporary, stdout=subprocess.DEVNULL, check=True
    )
    extracted = temporary / "squashfs-root"
    unpacked = directory / "linux-unpacked/resources"
    expected = digest(engine)
    smoke = json.loads(engine_proof.read_text())
    assert smoke["status"] == "passed"
    assert smoke.get("binary_sha256", smoke.get("engine_sha256")) == expected
    assert digest(unpacked / "engine/cytoforge-engine") == expected
    assert digest(extracted / "resources/engine/cytoforge-engine") == expected
    engine_files = [p for p in engine.parent.rglob("*") if p.is_file()]
    expected_paths = {p.relative_to(engine.parent).as_posix() for p in engine_files}
    engine_hashes = {}
    for bundled in [unpacked / "engine", extracted / "resources/engine"]:
        assert {
            p.relative_to(bundled).as_posix() for p in bundled.rglob("*") if p.is_file()
        } == expected_paths, "The complete bundled engine inventory must match its validated source"
    for source in engine_files:
        assert source.resolve().is_relative_to(engine.parent), (
            "Engine links must remain self-contained"
        )
        relative = source.relative_to(engine.parent)
        sha256 = digest(source)
        assert digest(unpacked / "engine" / relative) == sha256, str(relative)
        assert digest(extracted / "resources/engine" / relative) == sha256, str(relative)
        engine_hashes[relative.as_posix()] = sha256
    files = [p for p in (root / "frontend/dist").rglob("*") if p.is_file()]
    for source in files:
        relative = source.relative_to(root / "frontend/dist")
        assert digest(source) == digest(unpacked / "frontend" / relative)
        assert digest(source) == digest(extracted / "resources/frontend" / relative)
    desktop = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-",
            str(root),
            str(unpacked / "app.asar"),
            str(extracted / "resources/app.asar"),
        ],
        input="""
import { extractFile } from '@electron/asar';
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
const [root, ...archives] = process.argv.slice(2);
let count = 0;
function visit(directory) {
  for (const entry of readdirSync(path.join(root, directory), { withFileTypes: true })) {
    const relative = directory + '/' + entry.name;
    if (entry.isDirectory()) visit(relative);
    else {
      const source = readFileSync(path.join(root, relative));
      for (const archive of archives) {
        if (!extractFile(archive, relative).equals(source)) {
          throw new Error('Desktop mismatch: ' + relative);
        }
      }
      count++;
    }
  }
}
visit('desktop');
console.log(JSON.stringify({ desktop_files_matched: count }));
""",
        text=True,
        capture_output=True,
        cwd=root,
        check=True,
    )
    desktop_matched = json.loads(desktop.stdout)["desktop_files_matched"]
    report = dict(
        checked_at=datetime.now(UTC).isoformat(),
        status="passed",
        source_frozen_unpacked_appimage_engine_sha256=expected,
        complete_engine_files_matched=len(engine_files),
        engine_tree_sha256=hashlib.sha256(
            json.dumps(engine_hashes, sort_keys=True).encode()
        ).hexdigest(),
        frontend_files_matched=len(files),
        desktop_files_matched=desktop_matched,
        packaged_native_shell_matches_latest_source=True,
        appimage_engine_matches_validated_binary=True,
        appimage_engine_matches_frozen_smoke_binary=engine_proof
        == root / "artifacts/engine-smoke.json",
        engine_validation_evidence=str(engine_proof.relative_to(root)),
        scope=(
            "Package contents, full engine tree, desktop shell and UI build; "
            "native runtime remains a separate gate"
        ),
        packaged_frontend_matches_latest_build=True,
        appimage_sha256=digest(binary),
        appimage_bytes=binary.stat().st_size,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
finally:
    shutil.rmtree(temporary)
